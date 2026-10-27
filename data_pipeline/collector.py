"""GTFS Realtime Vehicle Positions Collector.

Runs an asynchronous collection loop fetching vehicles.pb every 15 seconds,
parsing positions, batch-inserting into PostgreSQL via COPY, and handling
graceful shutdown on SIGINT/Ctrl+C.

Usage:
    uv run python collector.py
    uv run python collector.py --interval 10
    uv run python collector.py --max-iterations 3
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# Ensure UTF-8 output on Windows terminal
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Ensure data_pipeline root is on sys.path
_DATA_PIPELINE_ROOT = Path(__file__).resolve().parent
if str(_DATA_PIPELINE_ROOT) not in sys.path:
    sys.path.insert(0, str(_DATA_PIPELINE_ROOT))

import aiohttp
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from db import close_async_pool, get_async_connection, run_async
from downloaders.gtfs_realtime import (
    DEFAULT_REALTIME_URL,
    RealtimeVehicleDownloader,
)
from writers.vehicle_positions import async_insert_vehicle_positions

console = Console()

DEFAULT_INTERVAL = int(os.getenv("COLLECTOR_INTERVAL_SECONDS", "15"))


class VehicleCollector:
    """Orchestrates periodic asynchronous fetching and writing of GTFS-RT vehicles."""

    def __init__(
        self,
        url: str = DEFAULT_REALTIME_URL,
        interval: float = DEFAULT_INTERVAL,
        max_iterations: int | None = None,
    ) -> None:
        self.url = url
        self.interval = interval
        self.max_iterations = max_iterations
        self.downloader = RealtimeVehicleDownloader(url=url)
        self.stop_event = asyncio.Event()

        # Metrics
        self.iteration_count = 0
        self.total_vehicles_inserted = 0
        self.skipped_duplicates = 0
        self.error_count = 0
        self.start_time: float | None = None

    def request_stop(self) -> None:
        """Signals the collection loop to stop gracefully."""
        self.stop_event.set()

    async def run(self) -> None:
        """Main collection loop."""
        self.start_time = time.time()
        console.print(Panel(
            f"[bold]URL źródła:[/bold] {self.url}\n"
            f"[bold]Interwał:[/bold] {self.interval} sekund\n"
            f"[bold]Max iteracji:[/bold] {self.max_iterations or 'Brak (nieskończona pętla)'}\n"
            f"[bold]Zatrzymanie:[/bold] Naciśnij [cyan]Ctrl+C[/cyan] w dowolnym momencie",
            title="🚌 Opti-Transit — GTFS Realtime Vehicle Collector",
            border_style="cyan",
        ))

        # Reusable HTTP session with connection pooling
        async with aiohttp.ClientSession() as session:
            db_conn = None

            while not self.stop_event.is_set():
                self.iteration_count += 1
                tick_start = time.time()
                time_str = datetime.now().strftime("%H:%M:%S")

                try:
                    # 1. Fetch & parse from mkuran.pl
                    t_http_0 = time.time()
                    fetch_result = await self.downloader.fetch_and_parse(session=session)
                    http_duration_ms = (time.time() - t_http_0) * 1000

                    if fetch_result is None:
                        self.skipped_duplicates += 1
                        console.print(
                            f"[dim][{time_str}] Iteracja #{self.iteration_count}: "
                            f"Dane identyczne (brak nowych pozycji). Pomijam zapis. "
                            f"(HTTP: {http_duration_ms:.0f}ms)[/dim]"
                        )
                    else:
                        feed_ts, positions = fetch_result

                        # 2. Reconnect / verify DB connection
                        if db_conn is None or db_conn.closed:
                            db_conn = await get_async_connection()

                        # 3. Batch insert using COPY
                        t_db_0 = time.time()
                        inserted = await async_insert_vehicle_positions(db_conn, positions)
                        db_duration_ms = (time.time() - t_db_0) * 1000
                        self.total_vehicles_inserted += inserted

                        feed_dt_str = datetime.fromtimestamp(feed_ts).strftime("%H:%M:%S")
                        console.print(
                            f"[bold green][{time_str}] Iteracja #{self.iteration_count}:[/bold green] "
                            f"Zapisano [bold cyan]{inserted:,}[/bold cyan] pojazdów "
                            f"[dim](feed: {feed_dt_str}, HTTP: {http_duration_ms:.0f}ms, COPY: {db_duration_ms:.1f}ms)[/dim]"
                        )

                except Exception as exc:
                    self.error_count += 1
                    console.print(f"[bold red][{time_str}] Błąd w iteracji #{self.iteration_count}: {exc}[/bold red]")
                    # Close connection on error so it reconnects on next tick
                    if db_conn and not db_conn.closed:
                        try:
                            await db_conn.close()
                        except Exception:
                            pass
                        db_conn = None

                # Check max iterations
                if self.max_iterations and self.iteration_count >= self.max_iterations:
                    break

                # Sleep until next tick, aborting early if stop_event is set
                elapsed = time.time() - tick_start
                sleep_time = max(0.0, self.interval - elapsed)
                try:
                    await asyncio.wait_for(self.stop_event.wait(), timeout=sleep_time)
                except asyncio.TimeoutError:
                    pass

            # Close DB connection upon exit
            if db_conn and not db_conn.closed:
                await db_conn.close()

        self._print_summary()

    def _print_summary(self) -> None:
        """Prints the final execution summary table."""
        total_time = time.time() - (self.start_time or time.time())
        summary_table = Table(title="📊 Podsumowanie pracy GTFS Realtime Collector", border_style="green")
        summary_table.add_column("Metryka", style="cyan")
        summary_table.add_column("Wartość", style="bold green", justify="right")

        summary_table.add_row("Łączny czas działania", f"{total_time:.1f} s")
        summary_table.add_row("Wykonanych iteracji", str(self.iteration_count))
        summary_table.add_row("Zapisanych pozycji pojazdów", f"{self.total_vehicles_inserted:,}")
        summary_table.add_row("Pominiętych duplikatów feedu", str(self.skipped_duplicates))
        summary_table.add_row("Liczba błędów", str(self.error_count))

        console.print("\n")
        console.print(summary_table)


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Collector pozycji pojazdów na żywo dla Opti-Transit")
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL,
        help="Interwał pobierania w sekundach (domyślnie 15)",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="Maksymalna liczba iteracji (domyślnie: nieskończoność)",
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_REALTIME_URL,
        help="URL feedu vehicles.pb",
    )

    args = parser.parse_args()

    collector = VehicleCollector(
        url=args.url,
        interval=args.interval,
        max_iterations=args.max_iterations,
    )

    async def _runner():
        # Setup graceful signal handlers
        loop = asyncio.get_running_loop()
        try:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, collector.request_stop)
        except (NotImplementedError, AttributeError):
            # Windows does not implement add_signal_handler for all signals
            pass

        try:
            await collector.run()
        except (KeyboardInterrupt, asyncio.CancelledError):
            console.print("\n[yellow]Otrzymano sygnał zatrzymania... Kończę pracę.[/yellow]")
        finally:
            await close_async_pool()

    try:
        run_async(_runner())
    except KeyboardInterrupt:
        console.print("[yellow]Zatrzymano przez użytkownika.[/yellow]")


if __name__ == "__main__":
    main()

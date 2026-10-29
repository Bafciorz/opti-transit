"""Unified Pipeline Runner for Opti-Transit.

Orchestrates all data pipeline components in a single process:
1. Realtime Vehicle Collector: polls GTFS-RT vehicles.pb every 15s into PostgreSQL.
2. Static GTFS Sync: checks for new warsaw.zip daily (03:00) and loads with Zero-Downtime Blue-Green swap.
3. Historical Archiver: compresses data older than 24h into ZSTD Parquet files daily (04:00).
4. Monitoring & Health: logs periodic throughput and database metrics every 5 minutes.

Usage:
    uv run python runner.py
    uv run python runner.py --collector-interval 15 --monitor-interval 60
    uv run python runner.py --run-once
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

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

from archiver import DEFAULT_ARCHIVE_DIR, archive_old_positions
from db import close_all_pools, get_async_connection, run_async
from downloaders.gtfs_realtime import (
    DEFAULT_REALTIME_URL,
    RealtimeVehicleDownloader,
)
from downloaders.gtfs_static import (
    DEFAULT_DEST_PATH as DEFAULT_STATIC_ZIP_PATH,
    DEFAULT_URL as DEFAULT_STATIC_URL,
    download_gtfs_static,
)
from loaders.gtfs_static import load_gtfs_static
from logging_config import get_monitor, setup_logging
from writers.vehicle_positions import async_insert_vehicle_positions

console = Console()

# Default configuration from environment variables
DEFAULT_COLLECTOR_INTERVAL = float(os.getenv("COLLECTOR_INTERVAL_SECONDS", "15"))
DEFAULT_MONITOR_INTERVAL = float(os.getenv("MONITOR_INTERVAL_SECONDS", "300"))
DEFAULT_STATIC_SYNC_HOUR = int(os.getenv("STATIC_SYNC_HOUR", "3"))  # 03:00 Warsaw time
DEFAULT_ARCHIVE_HOUR = int(os.getenv("ARCHIVE_HOUR", "4"))          # 04:00 Warsaw time
DEFAULT_RETENTION_HOURS = float(os.getenv("ARCHIVE_RETENTION_HOURS", "24"))
WARSAW_TZ = ZoneInfo("Europe/Warsaw")


class PipelineRunner:
    """Manages the lifecycle and concurrency of the entire data pipeline."""

    def __init__(
        self,
        collector_interval: float = DEFAULT_COLLECTOR_INTERVAL,
        monitor_interval: float = DEFAULT_MONITOR_INTERVAL,
        static_sync_hour: int = DEFAULT_STATIC_SYNC_HOUR,
        archive_hour: int = DEFAULT_ARCHIVE_HOUR,
        retention_hours: float = DEFAULT_RETENTION_HOURS,
        enable_collector: bool = True,
        enable_static_sync: bool = True,
        enable_archiver: bool = True,
        sync_on_startup: bool = False,
        archive_on_startup: bool = False,
        max_collector_iterations: int | None = None,
        archive_dir: Path | str = DEFAULT_ARCHIVE_DIR,
        static_url: str = DEFAULT_STATIC_URL,
        static_dest: Path = DEFAULT_STATIC_ZIP_PATH,
        realtime_url: str = DEFAULT_REALTIME_URL,
    ) -> None:
        self.collector_interval = collector_interval
        self.monitor_interval = monitor_interval
        self.static_sync_hour = static_sync_hour
        self.archive_hour = archive_hour
        self.retention_hours = retention_hours
        self.enable_collector = enable_collector
        self.enable_static_sync = enable_static_sync
        self.enable_archiver = enable_archiver
        self.sync_on_startup = sync_on_startup
        self.archive_on_startup = archive_on_startup
        self.max_collector_iterations = max_collector_iterations
        self.archive_dir = Path(archive_dir)
        self.static_url = static_url
        self.static_dest = Path(static_dest)
        self.realtime_url = realtime_url

        self.stop_event = asyncio.Event()
        self.monitor = get_monitor()
        self.logger = setup_logging()

    def request_stop(self) -> None:
        """Signals all loops to stop gracefully."""
        self.logger.info("Shutdown requested. Gracefully stopping all tasks...")
        self.stop_event.set()

    async def run(self) -> None:
        """Starts all pipeline loops concurrently."""
        source_label = "dane.um.warszawa.pl [Official Warsaw API]"
        console.print(
            Panel.fit(
                "[bold cyan]Opti-Transit Data Pipeline — Unified Runner[/bold cyan]\n"
                f"• Realtime Collector: [green]ENABLED[/green] (every {self.collector_interval}s, source: [cyan]{source_label}[/cyan])\n"
                f"• Static GTFS Sync:   {'[green]ENABLED[/green] (daily at ' + str(self.static_sync_hour) + ':00)' if self.enable_static_sync else '[dim]DISABLED[/dim]'}\n"
                f"• Parquet Archiver:   {'[green]ENABLED[/green] (daily at ' + str(self.archive_hour) + ':00, >' + str(self.retention_hours) + 'h)' if self.enable_archiver else '[dim]DISABLED[/dim]'}\n"
                f"• Health Monitor:     [green]ENABLED[/green] (every {self.monitor_interval}s)\n"
                f"• Shutdown:           Press [yellow]Ctrl+C[/yellow] to terminate gracefully",
                border_style="cyan",
            )
        )

        tasks: list[asyncio.Task[Any]] = []

        # 1. Health monitor loop
        tasks.append(asyncio.create_task(self._run_monitor_loop(), name="monitor_loop"))

        # 2. Realtime vehicle collector loop
        if self.enable_collector:
            tasks.append(asyncio.create_task(self._run_collector_loop(), name="collector_loop"))

        # 3. Static GTFS sync loop
        if self.enable_static_sync:
            tasks.append(asyncio.create_task(self._run_static_sync_loop(), name="static_sync_loop"))

        # 4. Parquet archiver loop
        if self.enable_archiver:
            tasks.append(asyncio.create_task(self._run_archiver_loop(), name="archiver_loop"))

        try:
            # Wait until stop_event is set or any critical task errors
            await self.stop_event.wait()
        except asyncio.CancelledError:
            pass
        finally:
            self.logger.info("Cancelling running pipeline tasks...")
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await close_all_pools()
            self._print_shutdown_summary()

    async def _run_collector_loop(self) -> None:
        """Asynchronous loop fetching vehicles.pb and bulk-inserting into PostgreSQL."""
        self.logger.info("Starting realtime vehicle collector loop (interval: %ss)", self.collector_interval)
        downloader = RealtimeVehicleDownloader(url=self.realtime_url)
        iterations = 0
        db_conn = None

        async with aiohttp.ClientSession() as session:
            while not self.stop_event.is_set():
                iterations += 1
                tick_start = time.time()

                try:
                    fetch_result = await downloader.fetch_and_parse(session=session)
                    duration_ms = (time.time() - tick_start) * 1000

                    if fetch_result is None:
                        self.monitor.record_realtime_success(
                            rows_count=0,
                            duration_ms=duration_ms,
                            skipped=True,
                        )
                        last_ts_str = (
                            datetime.fromtimestamp(downloader.last_feed_timestamp, tz=timezone.utc).strftime("%H:%M:%S")
                            if downloader.last_feed_timestamp
                            else "unknown"
                        )
                        self.logger.info(
                            "Duplicate feed skipped: external server has not updated positions yet (timestamp: %s)",
                            last_ts_str,
                        )
                    else:
                        feed_ts, positions = fetch_result
                        feed_dt = datetime.fromtimestamp(feed_ts, tz=timezone.utc)

                        # Ensure persistent DB connection with auto-reconnect
                        if db_conn is None or db_conn.closed:
                            db_conn = await get_async_connection()

                        inserted_count = await async_insert_vehicle_positions(db_conn, positions)
                        duration_ms = (time.time() - tick_start) * 1000
                        self.monitor.record_realtime_success(
                            rows_count=inserted_count,
                            duration_ms=duration_ms,
                            timestamp=feed_dt,
                        )
                        self.logger.info(
                            "Inserted %d vehicle positions in %.1f ms (feed timestamp: %s)",
                            inserted_count,
                            duration_ms,
                            feed_dt.strftime("%H:%M:%S"),
                        )

                except Exception as exc:
                    self.monitor.record_realtime_error(exc)
                    if db_conn and not db_conn.closed:
                        try:
                            await db_conn.close()
                        except Exception:
                            pass
                    db_conn = None

                if self.max_collector_iterations and iterations >= self.max_collector_iterations:
                    self.logger.info("Reached maximum collector iterations (%d). Stopping.", iterations)
                    self.stop_event.set()
                    break

                # Sleep until next interval (interrupted if stop_event fires)
                elapsed = time.time() - tick_start
                sleep_time = max(0.0, self.collector_interval - elapsed)
                try:
                    await asyncio.wait_for(self.stop_event.wait(), timeout=sleep_time)
                except asyncio.TimeoutError:
                    pass

        if db_conn and not db_conn.closed:
            await db_conn.close()

    async def _run_static_sync_loop(self) -> None:
        """Periodically checks and loads new static GTFS schedules."""
        self.logger.info("Static GTFS sync task started (scheduled for %02d:00 Warsaw time)", self.static_sync_hour)

        # Optional initial check on startup
        if self.sync_on_startup:
            self.logger.info("Performing startup static GTFS check...")
            await asyncio.to_thread(self._sync_static_gtfs_worker)

        while not self.stop_event.is_set():
            # Calculate seconds until next scheduled run
            delay = self._seconds_until_hour(self.static_sync_hour)
            self.logger.debug("Next static GTFS sync in %d seconds", int(delay))

            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=delay)
                break
            except asyncio.TimeoutError:
                pass

            # Execute static sync in a separate thread so asyncio loop is not blocked
            self.logger.info("Running scheduled static GTFS sync...")
            await asyncio.to_thread(self._sync_static_gtfs_worker)

    def _sync_static_gtfs_worker(self) -> None:
        """Worker executing download and zero-downtime swap."""
        try:
            dest_path, feed_info, is_new = download_gtfs_static(
                url=self.static_url,
                dest_path=self.static_dest,
                force=False,
            )
            if is_new:
                self.logger.info("New static feed detected (%s). Loading into PostgreSQL...", feed_info.get("feed_version"))
                load_gtfs_static(zip_path=dest_path, force=True)
                self.monitor.record_static_sync(True, f"Loaded version {feed_info.get('feed_version')}")
            else:
                self.monitor.record_static_sync(True, "Feed up to date")
        except Exception as exc:
            self.logger.error("Static GTFS sync failed: %s", exc, exc_info=True)
            self.monitor.record_static_sync(False, str(exc))

    async def _run_archiver_loop(self) -> None:
        """Periodically archives historical vehicle positions to Parquet and purges them from PG."""
        self.logger.info("Parquet archiver task started (scheduled for %02d:00 Warsaw time)", self.archive_hour)

        if self.archive_on_startup:
            self.logger.info("Performing startup archival check...")
            await asyncio.to_thread(self._archive_worker)

        while not self.stop_event.is_set():
            delay = self._seconds_until_hour(self.archive_hour)
            self.logger.debug("Next archival in %d seconds", int(delay))

            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=delay)
                break
            except asyncio.TimeoutError:
                pass

            self.logger.info("Running scheduled Parquet archival (threshold: %sh)...", self.retention_hours)
            await asyncio.to_thread(self._archive_worker)

    def _archive_worker(self) -> None:
        """Worker executing Parquet archival."""
        try:
            res = archive_old_positions(
                older_than_hours=self.retention_hours,
                archive_dir=self.archive_dir,
                delete_after=True,
                dry_run=False,
            )
            self.monitor.record_archiver_run(res.archived_rows, res.deleted_rows)
        except Exception as exc:
            self.logger.error("Archival failed: %s", exc, exc_info=True)

    async def _run_monitor_loop(self) -> None:
        """Emits health and progress summaries at fixed intervals."""
        while not self.stop_event.is_set():
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=self.monitor_interval)
                break
            except asyncio.TimeoutError:
                pass

            self.monitor.log_periodic_summary()

    def _seconds_until_hour(self, target_hour: int) -> float:
        """Computes seconds until the next occurrence of target_hour in Europe/Warsaw timezone."""
        now = datetime.now(WARSAW_TZ)
        target = now.replace(hour=target_hour, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return (target - now).total_seconds()

    def _print_shutdown_summary(self) -> None:
        """Prints a clean shutdown summary of the pipeline session."""
        m = self.monitor.metrics
        uptime = datetime.now(timezone.utc) - m.start_time

        table = Table(title="Opti-Transit Pipeline Session Summary", border_style="cyan")
        table.add_column("Metric", style="bold")
        table.add_column("Value", style="green")

        table.add_row("Uptime", str(uptime).split(".")[0])
        table.add_row("Realtime Cycles Completed", f"{m.realtime_cycles_total:,}")
        table.add_row("Successful Inserts", f"{m.realtime_cycles_success:,}")
        table.add_row("Duplicate Feeds Skipped", f"{m.realtime_cycles_skipped:,}")
        table.add_row("Cycle Errors", f"{m.realtime_cycles_error:,}")
        table.add_row("Total Vehicle Positions Saved", f"{m.realtime_rows_collected:,}")
        table.add_row("Static GTFS Sync Checks", f"{m.static_syncs_total:,}")
        table.add_row("Archiver Runs", f"{m.archiver_runs_total:,}")
        table.add_row("Rows Archived to Parquet", f"{m.archived_rows_total:,}")

        console.print(table)


def main() -> None:
    """CLI entry point for runner.py."""
    parser = argparse.ArgumentParser(
        description="Opti-Transit Data Pipeline Unified Runner"
    )
    parser.add_argument(
        "--collector-interval",
        type=float,
        default=DEFAULT_COLLECTOR_INTERVAL,
        help=f"Realtime collector interval in seconds (default: {DEFAULT_COLLECTOR_INTERVAL})",
    )
    parser.add_argument(
        "--monitor-interval",
        type=float,
        default=DEFAULT_MONITOR_INTERVAL,
        help=f"Periodic monitor logging interval in seconds (default: {DEFAULT_MONITOR_INTERVAL})",
    )
    parser.add_argument(
        "--static-sync-hour",
        type=int,
        default=DEFAULT_STATIC_SYNC_HOUR,
        help=f"Daily hour (0-23) to check static GTFS schedule (default: {DEFAULT_STATIC_SYNC_HOUR})",
    )
    parser.add_argument(
        "--archive-hour",
        type=int,
        default=DEFAULT_ARCHIVE_HOUR,
        help=f"Daily hour (0-23) to run Parquet archival (default: {DEFAULT_ARCHIVE_HOUR})",
    )
    parser.add_argument(
        "--retention-hours",
        type=float,
        default=DEFAULT_RETENTION_HOURS,
        help=f"Archive records older than this many hours (default: {DEFAULT_RETENTION_HOURS})",
    )
    parser.add_argument(
        "--sync-on-startup",
        action="store_true",
        help="Check and download static GTFS immediately on startup",
    )
    parser.add_argument(
        "--archive-on-startup",
        action="store_true",
        help="Run Parquet archival immediately on startup",
    )
    parser.add_argument(
        "--skip-static-sync",
        action="store_true",
        help="Disable static GTFS schedule synchronizer",
    )
    parser.add_argument(
        "--skip-archiver",
        action="store_true",
        help="Disable background Parquet archiver",
    )
    parser.add_argument(
        "--run-once",
        action="store_true",
        help="Run a single collector cycle and exit (useful for testing/CI)",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="Maximum collector iterations before exiting",
    )

    args = parser.parse_args()

    max_iter = 1 if args.run_once else args.max_iterations

    runner = PipelineRunner(
        collector_interval=args.collector_interval,
        monitor_interval=args.monitor_interval,
        static_sync_hour=args.static_sync_hour,
        archive_hour=args.archive_hour,
        retention_hours=args.retention_hours,
        enable_collector=True,
        enable_static_sync=not args.skip_static_sync,
        enable_archiver=not args.skip_archiver,
        sync_on_startup=args.sync_on_startup,
        archive_on_startup=args.archive_on_startup,
        max_collector_iterations=max_iter,
    )

    def handle_signal(*_: Any) -> None:
        runner.request_stop()

    if sys.platform != "win32":
        try:
            loop = asyncio.get_event_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, runner.request_stop)
        except Exception:
            pass

    try:
        run_async(runner.run())
    except KeyboardInterrupt:
        runner.request_stop()


if __name__ == "__main__":
    main()

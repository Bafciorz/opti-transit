"""Verification script for GTFS static data in PostgreSQL.

Checks table row counts, UTF-8 encoding of Polish characters,
samples routes and stops, and verifies feed_metadata.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure UTF-8 output on Windows terminal
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Ensure data_pipeline root is on sys.path
_DATA_PIPELINE_ROOT = Path(__file__).resolve().parent.parent
if str(_DATA_PIPELINE_ROOT) not in sys.path:
    sys.path.insert(0, str(_DATA_PIPELINE_ROOT))

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from db import close_sync_pool, get_connection

console = Console()


def verify_tables() -> None:
    """Verifies all GTFS static tables."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            # 1. Feed metadata
            cur.execute("""
                SELECT feed_version, feed_publisher_name, loaded_at, 
                       routes_count, stops_count, trips_count, stop_times_count 
                FROM feed_metadata 
                ORDER BY loaded_at DESC LIMIT 1;
            """)
            meta = cur.fetchone()

            if meta:
                console.print(Panel(
                    f"[bold]Wersja feedu:[/bold] {meta[0]}\n"
                    f"[bold]Wydawca:[/bold] {meta[1]}\n"
                    f"[bold]Data załadowania:[/bold] {meta[2]}\n"
                    f"[bold]Statystyki:[/bold] {meta[3]:,} linii, {meta[4]:,} przystanków, "
                    f"{meta[5]:,} kursów, {meta[6]:,} czasów",
                    title="📋 Metadane Feedu (feed_metadata)",
                    border_style="cyan",
                ))

            # 2. Table row counts
            tables = [
                "gtfs_agency",
                "gtfs_routes",
                "gtfs_stops",
                "gtfs_trips",
                "gtfs_stop_times",
                "gtfs_calendar_dates",
                "gtfs_shapes",
            ]

            count_table = Table(title="📊 Liczba wierszy w tabelach GTFS", border_style="blue")
            count_table.add_column("Tabela", style="bold green")
            count_table.add_column("Liczba wierszy w bazie", justify="right", style="cyan")

            for tbl in tables:
                cur.execute(f"SELECT COUNT(*) FROM {tbl};")
                count = cur.fetchone()[0]
                count_table.add_row(tbl, f"{count:,}")

            console.print(count_table)

            # 3. Polish characters check
            cur.execute("""
                SELECT stop_id, stop_name, town_name, street_name 
                FROM gtfs_stops 
                WHERE stop_name ILIKE '%Św%' 
                   OR stop_name ILIKE '%Ż%' 
                   OR stop_name ILIKE '%Ł%' 
                   OR stop_name ILIKE '%łę%'
                ORDER BY stop_name 
                LIMIT 8;
            """)
            polish_stops = cur.fetchall()

            stops_table = Table(title="🇵🇱 Test kodowania polskich znaków (UTF-8)", border_style="magenta")
            stops_table.add_column("Stop ID", style="dim")
            stops_table.add_column("Nazwa przystanku", style="bold yellow")
            stops_table.add_column("Miejscowość", style="green")
            stops_table.add_column("Ulica", style="cyan")

            for s_id, s_name, town, street in polish_stops:
                stops_table.add_row(s_id, s_name, town or "-", street or "-")

            console.print(stops_table)

            # 4. Sample trip with stop times
            cur.execute("""
                SELECT r.route_short_name, t.trip_id, t.trip_headsign,
                       st.stop_sequence, s.stop_name, st.arrival_time, st.departure_time
                FROM gtfs_trips t
                JOIN gtfs_routes r ON t.route_id = r.route_id
                JOIN gtfs_stop_times st ON t.trip_id = st.trip_id
                JOIN gtfs_stops s ON st.stop_id = s.stop_id
                WHERE r.route_short_name = 'M1'
                ORDER BY t.trip_id, st.stop_sequence
                LIMIT 5;
            """)
            metro_sample = cur.fetchall()

            if metro_sample:
                metro_table = Table(title="🚇 Przykładowy kurs metra M1 ze stop_times", border_style="cyan")
                metro_table.add_column("Linia", style="bold red")
                metro_table.add_column("Kierunek", style="yellow")
                metro_table.add_column("Sekw.", justify="right")
                metro_table.add_column("Przystanek", style="bold white")
                metro_table.add_column("Przyjazd", style="green")
                metro_table.add_column("Odjazd", style="cyan")

                for line, t_id, headsign, seq, stop, arr, dep in metro_sample:
                    metro_table.add_row(line, headsign, str(seq), stop, arr, dep)

                console.print(metro_table)


def main() -> None:
    try:
        verify_tables()
    finally:
        close_sync_pool()


if __name__ == "__main__":
    main()

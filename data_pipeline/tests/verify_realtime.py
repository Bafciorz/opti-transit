"""Verification and analysis script for vehicle_positions table in PostgreSQL.

Checks row counts, timestamps, Warsaw geographic bounding box,
route distribution, table disk size, and 24h volume extrapolation.
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


def verify_realtime_data() -> None:
    """Queries vehicle_positions and prints metrics and data quality stats."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            # 1. Total row count & time range
            cur.execute("""
                SELECT 
                    COUNT(*),
                    MIN(time),
                    MAX(time),
                    MIN(latitude),
                    MAX(latitude),
                    MIN(longitude),
                    MAX(longitude),
                    COUNT(DISTINCT vehicle_id),
                    COUNT(DISTINCT route_id)
                FROM vehicle_positions;
            """)
            row = cur.fetchone()

            total_rows = row[0]
            if total_rows == 0:
                console.print("[yellow]Tabela vehicle_positions jest pusta. Uruchom collector.py aby zebrać dane.[/yellow]")
                return

            min_time, max_time = row[1], row[2]
            min_lat, max_lat = row[3], row[4]
            min_lon, max_lon = row[5], row[6]
            distinct_vehicles, distinct_routes = row[7], row[8]

            # 2. Table disk size in PostgreSQL
            cur.execute("""
                SELECT 
                    pg_size_pretty(pg_relation_size('vehicle_positions')) AS data_size,
                    pg_size_pretty(pg_indexes_size('vehicle_positions')) AS index_size,
                    pg_size_pretty(pg_total_relation_size('vehicle_positions')) AS total_size,
                    pg_total_relation_size('vehicle_positions') AS total_bytes;
            """)
            size_row = cur.fetchone()
            data_size, index_size, total_size, total_bytes = size_row

            # Extrapolations: ~1,900 vehicles per 15s tick = 4 ticks/min * 1900 = 7,600 rows/min = ~10.9M rows/day
            estimated_rows_per_day = 10_944_000
            bytes_per_row = total_bytes / total_rows if total_rows > 0 else 180
            estimated_mb_per_day = (estimated_rows_per_day * bytes_per_row) / (1024 * 1024)

            console.print(Panel(
                f"[bold]Łączna liczba wierszy:[/bold] {total_rows:,}\n"
                f"[bold]Unikalnych pojazdów:[/bold] {distinct_vehicles:,}\n"
                f"[bold]Obsługiwanych linii:[/bold] {distinct_routes:,}\n"
                f"[bold]Zakres czasowy (GPS):[/bold] {min_time} -> {max_time}\n"
                f"[bold]Rozmiar tabeli w PG:[/bold] Dane: {data_size}, Indeksy: {index_size} (Łącznie: {total_size})\n"
                f"[bold]Ekstrapolacja na 24h:[/bold] ~{estimated_rows_per_day:,} wierszy/dzień (~{estimated_mb_per_day:.1f} MB/dzień)",
                title="🛰 GTFS Realtime — Metryki vehicle_positions",
                border_style="cyan",
            ))

            # 3. Warsaw Geographic Bounds validation (Lat ~52, Lon ~21)
            valid_lat = 51.5 <= min_lat <= 53.0 and 51.5 <= max_lat <= 53.0
            valid_lon = 20.0 <= min_lon <= 22.0 and 20.0 <= max_lon <= 22.0

            geo_status = "[bold green]✓ Prawidłowe (w granicach aglomeracji warszawskiej)[/bold green]" if (valid_lat and valid_lon) else "[bold red]Uwaga: Współrzędne poza zakresem![/bold red]"
            console.print(f"  [bold]Szerokość (Lat):[/bold] [{min_lat:.5f} .. {max_lat:.5f}]")
            console.print(f"  [bold]Długość (Lon):[/bold]  [{min_lon:.5f} .. {max_lon:.5f}] -> {geo_status}\n")

            # 4. Recent samples table
            cur.execute("""
                SELECT 
                    route_id,
                    brigade,
                    vehicle_label,
                    ROUND(latitude::numeric, 5),
                    ROUND(longitude::numeric, 5),
                    TO_CHAR(time, 'YYYY-MM-DD HH24:MI:SS'),
                    TO_CHAR(fetched_at, 'HH24:MI:SS')
                FROM vehicle_positions
                ORDER BY time DESC
                LIMIT 8;
            """)
            samples = cur.fetchall()

            samples_table = Table(title="📍 Ostatnie zapisane pozycje pojazdów", border_style="magenta")
            samples_table.add_column("Linia", style="bold yellow")
            samples_table.add_column("Brygada", style="cyan")
            samples_table.add_column("Nr boczny", style="bold white")
            samples_table.add_column("Lat", style="white")
            samples_table.add_column("Lon", style="white")
            samples_table.add_column("Czas GPS (UTC)", style="magenta")
            samples_table.add_column("Pobrano (UTC)", style="dim")

            for r_id, brig, label, lat, lon, t_str, f_str in samples:
                samples_table.add_row(r_id or "-", brig or "-", label or "-", str(lat), str(lon), t_str, f_str)

            console.print(samples_table)

            # 5. Top 10 routes with most active vehicles
            cur.execute("""
                SELECT route_id, COUNT(*) 
                FROM vehicle_positions
                WHERE route_id IS NOT NULL
                GROUP BY route_id
                ORDER BY COUNT(*) DESC
                LIMIT 10;
            """)
            top_routes = cur.fetchall()

            routes_table = Table(title="🚍 Top 10 linii o największej liczbie pozycji", border_style="blue")
            routes_table.add_column("Linia", style="bold green")
            routes_table.add_column("Zapisanych pozycji", justify="right", style="cyan")

            for r_id, cnt in top_routes:
                routes_table.add_row(r_id, f"{cnt:,}")

            console.print(routes_table)


def main() -> None:
    try:
        verify_realtime_data()
    finally:
        close_sync_pool()


if __name__ == "__main__":
    main()

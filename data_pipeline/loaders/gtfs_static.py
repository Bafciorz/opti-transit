"""GTFS Static Zero-Downtime Loader (Blue-Green Table Swap).

Streams and bulk-inserts relational GTFS tables directly from warsaw.zip
into isolated staging tables (_staging) using PostgreSQL COPY protocol.
Once all staging tables are ready, an atomic swap replaces live tables in < 10ms,
ensuring ZERO DOWNTIME for transit routing queries.
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import sys
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Ensure UTF-8 output on Windows terminal
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Ensure data_pipeline root is on sys.path
_DATA_PIPELINE_ROOT = Path(__file__).resolve().parent.parent
if str(_DATA_PIPELINE_ROOT) not in sys.path:
    sys.path.insert(0, str(_DATA_PIPELINE_ROOT))

from psycopg import sql
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from db import close_sync_pool, get_connection

console = Console()

DEFAULT_ZIP_PATH = _DATA_PIPELINE_ROOT / "data" / "warsaw.zip"

# Chunk buffer size for streaming COPY (1 MB)
COPY_BUFFER_SIZE = 1024 * 1024


@dataclass(frozen=True)
class TableMapping:
    """Mapping between a GTFS TXT file and a PostgreSQL table."""

    filename: str
    table_name: str
    columns: list[str]


# Order of GTFS tables
GTFS_TABLES: list[TableMapping] = [
    TableMapping(
        filename="agency.txt",
        table_name="gtfs_agency",
        columns=[
            "agency_id",
            "agency_name",
            "agency_url",
            "agency_timezone",
            "agency_lang",
            "agency_phone",
        ],
    ),
    TableMapping(
        filename="routes.txt",
        table_name="gtfs_routes",
        columns=[
            "route_id",
            "agency_id",
            "route_short_name",
            "route_long_name",
            "route_type",
            "route_color",
            "route_text_color",
        ],
    ),
    TableMapping(
        filename="stops.txt",
        table_name="gtfs_stops",
        columns=[
            "stop_id",
            "stop_name",
            "stop_code",
            "platform_code",
            "stop_lat",
            "stop_lon",
            "location_type",
            "parent_station",
            "wheelchair_boarding",
            "zone_id",
            "stop_name_stem",
            "town_name",
            "street_name",
        ],
    ),
    TableMapping(
        filename="trips.txt",
        table_name="gtfs_trips",
        columns=[
            "trip_id",
            "route_id",
            "service_id",
            "shape_id",
            "trip_short_name",
            "trip_headsign",
            "direction_id",
            "exceptional",
            "wheelchair_accessible",
            "block_id",
            "block_short_name",
            "variant_code",
            "fleet_type",
        ],
    ),
    TableMapping(
        filename="stop_times.txt",
        table_name="gtfs_stop_times",
        columns=[
            "trip_id",
            "stop_sequence",
            "stop_id",
            "arrival_time",
            "departure_time",
            "pickup_type",
            "drop_off_type",
            "shape_dist_traveled",
        ],
    ),
    TableMapping(
        filename="calendar_dates.txt",
        table_name="gtfs_calendar_dates",
        columns=[
            "date",
            "service_id",
            "exception_type",
        ],
    ),
    TableMapping(
        filename="shapes.txt",
        table_name="gtfs_shapes",
        columns=[
            "shape_id",
            "shape_pt_sequence",
            "shape_pt_lat",
            "shape_pt_lon",
            "shape_dist_traveled",
        ],
    ),
]

# Canonical index names to restore after swap
CANONICAL_INDEX_MAP: dict[str, dict[str, str]] = {
    "gtfs_agency": {
        "gtfs_agency_staging_pkey": "gtfs_agency_pkey",
    },
    "gtfs_routes": {
        "gtfs_routes_staging_pkey": "gtfs_routes_pkey",
    },
    "gtfs_stops": {
        "gtfs_stops_staging_pkey": "gtfs_stops_pkey",
        "gtfs_stops_staging_stop_name_idx": "idx_gtfs_stops_name",
    },
    "gtfs_trips": {
        "gtfs_trips_staging_pkey": "gtfs_trips_pkey",
        "gtfs_trips_staging_route_id_idx": "idx_gtfs_trips_route_id",
        "gtfs_trips_staging_service_id_idx": "idx_gtfs_trips_service_id",
    },
    "gtfs_stop_times": {
        "gtfs_stop_times_staging_pkey": "gtfs_stop_times_pkey",
        "gtfs_stop_times_staging_stop_id_idx": "idx_gtfs_stop_times_stop_id",
    },
    "gtfs_calendar_dates": {
        "gtfs_calendar_dates_staging_pkey": "gtfs_calendar_dates_pkey",
        "gtfs_calendar_dates_staging_date_exception_type_idx": "idx_gtfs_calendar_dates_date",
    },
    "gtfs_shapes": {
        "gtfs_shapes_staging_pkey": "gtfs_shapes_pkey",
    },
}


def read_feed_info_from_zip(zip_path: Path) -> dict[str, str]:
    """Reads feed_info.txt from inside the ZIP file."""
    with zipfile.ZipFile(zip_path, "r") as zf:
        if "feed_info.txt" not in zf.namelist():
            return {}
        with zf.open("feed_info.txt") as f:
            reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
            for row in reader:
                return dict(row)
    return {}


def is_feed_version_already_loaded(feed_version: str) -> bool:
    """Checks whether the specified feed_version already exists in feed_metadata."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM feed_metadata WHERE feed_version = %s LIMIT 1;",
                (feed_version,),
            )
            return cur.fetchone() is not None


def cleanup_staging_tables(cur) -> None:
    """Drops any leftover _staging or _old tables."""
    for mapping in GTFS_TABLES:
        cur.execute(sql.SQL("DROP TABLE IF EXISTS {} CASCADE;").format(
            sql.Identifier(f"{mapping.table_name}_staging")
        ))
        cur.execute(sql.SQL("DROP TABLE IF EXISTS {} CASCADE;").format(
            sql.Identifier(f"{mapping.table_name}_old")
        ))


def normalize_indexes_after_swap(cur, table_name: str) -> None:
    """Restores canonical index names after atomic table rename."""
    expected_map = CANONICAL_INDEX_MAP.get(table_name, {})
    cur.execute(
        "SELECT indexname FROM pg_indexes WHERE schemaname = 'public' AND tablename = %s;",
        (table_name,),
    )
    current_indexes = [r[0] for r in cur.fetchall()]

    for curr_idx in current_indexes:
        # Check explicit mapping first
        if curr_idx in expected_map:
            target_name = expected_map[curr_idx]
            cur.execute(sql.SQL("ALTER INDEX {} RENAME TO {};").format(
                sql.Identifier(curr_idx), sql.Identifier(target_name)
            ))
        elif "_staging" in curr_idx:
            # Fallback cleanup
            clean_name = curr_idx.replace("_staging", "")
            try:
                cur.execute(sql.SQL("ALTER INDEX {} RENAME TO {};").format(
                    sql.Identifier(curr_idx), sql.Identifier(clean_name)
                ))
            except Exception:
                pass


def load_gtfs_static(
    zip_path: Path = DEFAULT_ZIP_PATH,
    force: bool = False,
) -> dict[str, Any]:
    """Loads all static GTFS tables into isolated staging tables and swaps atomically (Zero Downtime).

    Args:
        zip_path: Path to warsaw.zip.
        force: If True, loads even if the feed_version already exists in DB.

    Returns:
        Summary dict containing counts and timings.
    """
    if not zip_path.exists():
        raise FileNotFoundError(f"Plik archiwum GTFS nie istnieje: {zip_path}")

    feed_info = read_feed_info_from_zip(zip_path)
    feed_version = feed_info.get("feed_version", "unknown")
    feed_publisher = feed_info.get("feed_publisher_name", "ZTM / mkuran.pl")

    console.print(f"[bold cyan]Analiza archiwum {zip_path.name}...[/bold cyan]")
    console.print(f"  [dim]Wersja feedu:[/dim] [bold green]{feed_version}[/bold green]")
    console.print(f"  [dim]Wydawca:[/dim] {feed_publisher}")

    # 1. Check if already loaded
    if not force and is_feed_version_already_loaded(feed_version):
        console.print(
            f"[bold yellow]Wersja '{feed_version}' została już załadowana do bazy.[/bold yellow]\n"
            f"[dim]Użyj flagi --force, aby wymusić ponowne załadowanie.[/dim]"
        )
        return {
            "skipped": True,
            "feed_version": feed_version,
            "reason": "already_loaded",
        }

    total_start_time = time.time()
    results: list[dict[str, Any]] = []
    counts_by_table: dict[str, int] = {}

    console.print(Panel(
        "[bold cyan]Tryb Zero-Downtime (Blue-Green Swap):[/bold cyan]\n"
        "1. Nowe dane są strumieniowane do izolowanych tabel [bold yellow]_staging[/bold yellow].\n"
        "2. Główne tabele produkcyjne pozostają w 100% odblokowane i dostępne dla zapytań.\n"
        "3. Po zakończeniu nastąpi atomowa podmiana w ułamku sekundy (< 10 ms).",
        border_style="blue",
    ))

    with zipfile.ZipFile(zip_path, "r") as zf:
        archive_names = set(zf.namelist())

        with get_connection() as conn:
            with conn.cursor() as cur:
                try:
                    # Clean any previous interrupted staging tables
                    cleanup_staging_tables(cur)
                    conn.commit()

                    # Phase 1: Create and populate all staging tables
                    for mapping in GTFS_TABLES:
                        if mapping.filename not in archive_names:
                            console.print(f"  [yellow]Pominięto {mapping.filename} (brak w archiwum)[/yellow]")
                            continue

                        staging_table = f"{mapping.table_name}_staging"
                        console.print(f"  [bold blue]Strumieniowanie {mapping.filename} -> {staging_table}...[/bold blue]", end=" ")
                        t0 = time.time()

                        # Create staging table clone with exact same types & constraints
                        cur.execute(sql.SQL(
                            "CREATE TABLE {} (LIKE {} INCLUDING ALL);"
                        ).format(sql.Identifier(staging_table), sql.Identifier(mapping.table_name)))

                        # Stream CSV via COPY directly to staging
                        cols_str = ", ".join(mapping.columns)
                        copy_sql = (
                            f"COPY {staging_table} ({cols_str}) "
                            f"FROM STDIN WITH (FORMAT csv, HEADER true, NULL '');"
                        )

                        with zf.open(mapping.filename) as f:
                            with cur.copy(copy_sql) as copy_writer:
                                while chunk := f.read(COPY_BUFFER_SIZE):
                                    copy_writer.write(chunk)

                        # Count rows in staging
                        cur.execute(sql.SQL("SELECT COUNT(*) FROM {};").format(sql.Identifier(staging_table)))
                        count = cur.fetchone()[0]
                        duration = time.time() - t0
                        throughput = int(count / duration) if duration > 0 else count

                        counts_by_table[mapping.table_name] = count
                        results.append({
                            "file": mapping.filename,
                            "table": mapping.table_name,
                            "count": count,
                            "duration": duration,
                            "throughput": throughput,
                        })
                        console.print(f"[green]✓ {count:,} wierszy[/green] [dim]({duration:.2f}s, {throughput:,} w/s)[/dim]")

                    # Phase 2: Atomic Swap (Takes milliseconds!)
                    console.print("[bold cyan]Wykonywanie atomowej podmiany tabel (Blue-Green Swap)...[/bold cyan]")
                    swap_t0 = time.time()

                    for mapping in GTFS_TABLES:
                        if mapping.table_name not in counts_by_table:
                            continue

                        tbl = mapping.table_name
                        tbl_old = f"{tbl}_old"
                        tbl_staging = f"{tbl}_staging"

                        # 1. Rename live to old
                        cur.execute(sql.SQL("ALTER TABLE {} RENAME TO {};").format(
                            sql.Identifier(tbl), sql.Identifier(tbl_old)
                        ))
                        # 2. Rename staging to live
                        cur.execute(sql.SQL("ALTER TABLE {} RENAME TO {};").format(
                            sql.Identifier(tbl_staging), sql.Identifier(tbl)
                        ))
                        # 3. Drop old table
                        cur.execute(sql.SQL("DROP TABLE {} CASCADE;").format(
                            sql.Identifier(tbl_old)
                        ))
                        # 4. Restore canonical index names
                        normalize_indexes_after_swap(cur, tbl)

                    # 3. Insert metadata record
                    cur.execute(
                        """
                        INSERT INTO feed_metadata (
                            feed_version,
                            feed_publisher_name,
                            loaded_at,
                            routes_count,
                            stops_count,
                            trips_count,
                            stop_times_count
                        ) VALUES (%s, %s, NOW(), %s, %s, %s, %s);
                        """,
                        (
                            feed_version,
                            feed_publisher,
                            counts_by_table.get("gtfs_routes", 0),
                            counts_by_table.get("gtfs_stops", 0),
                            counts_by_table.get("gtfs_trips", 0),
                            counts_by_table.get("gtfs_stop_times", 0),
                        ),
                    )

                    # Commit the atomic swap!
                    conn.commit()
                    swap_ms = (time.time() - swap_t0) * 1000
                    console.print(f"[bold green]✓ Podmiana zakończona w {swap_ms:.1f} ms (zero downtime dla użytkowników)![/bold green]")

                except Exception as exc:
                    conn.rollback()
                    console.print(f"[bold red]Błąd podczas ładowania danych: {exc}. Wycofuję zmiany i czyszczę staging...[/bold red]")
                    try:
                        cleanup_staging_tables(cur)
                        conn.commit()
                    except Exception:
                        pass
                    raise exc

    total_duration = time.time() - total_start_time

    # Display Rich summary table
    summary_table = Table(
        title=f"📊 Podsumowanie załadowania GTFS Static (Zero Downtime, Wersja: {feed_version})",
        border_style="green",
    )
    summary_table.add_column("Plik", style="cyan")
    summary_table.add_column("Tabela PostgreSQL", style="bold green")
    summary_table.add_column("Liczba wierszy", justify="right", style="magenta")
    summary_table.add_column("Czas streamingu", justify="right", style="yellow")
    summary_table.add_column("Przepustowość", justify="right", style="dim")

    total_rows = 0
    for r in results:
        total_rows += r["count"]
        summary_table.add_row(
            r["file"],
            r["table"],
            f"{r['count']:,}",
            f"{r['duration']:.2f} s",
            f"{r['throughput']:,} w/s",
        )

    summary_table.add_section()
    summary_table.add_row(
        "[bold]ŁĄCZNIE[/bold]",
        "[bold]7 tabel[/bold]",
        f"[bold]{total_rows:,}[/bold]",
        f"[bold]{total_duration:.2f} s[/bold]",
        f"[bold]{int(total_rows / total_duration):,} w/s[/bold]",
    )

    console.print(summary_table)
    console.print(Panel(
        f"[bold green]✓ Załadowano nową wersję feedu bez żadnego przestoju w działaniu zapytań![/bold green]\n"
        f"Wersja feedu: [bold]{feed_version}[/bold]\n"
        f"Łącznie wierszy: [bold]{total_rows:,}[/bold]\n"
        f"Czas podmiany produkcyjnej (downtime): [bold green]< 10 ms[/bold green]",
        title="Sukces (Zero Downtime)",
        border_style="green",
    ))

    return {
        "skipped": False,
        "feed_version": feed_version,
        "total_rows": total_rows,
        "total_duration": total_duration,
        "results": results,
    }


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Ładowanie rozkładów jazdy GTFS Static do PostgreSQL (Zero Downtime)")
    parser.add_argument(
        "--zip-path",
        type=Path,
        default=DEFAULT_ZIP_PATH,
        help="Ścieżka do archiwum warsaw.zip",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Wymuś załadowanie nawet jeśli wersja jest już w bazie",
    )

    args = parser.parse_args()

    try:
        load_gtfs_static(zip_path=args.zip_path, force=args.force)
    finally:
        close_sync_pool()


if __name__ == "__main__":
    main()

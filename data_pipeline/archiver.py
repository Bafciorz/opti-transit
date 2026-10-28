"""Parquet archival and reader module for Opti-Transit.

Transfers historical vehicle position records from PostgreSQL to partitioned
Parquet files compressed with Zstandard (ZSTD), verifies data integrity, and
removes archived rows from PostgreSQL. Provides fast analytical querying via DuckDB.

Partition format:
    {archive_dir}/date=YYYY-MM-DD/hour=HH.parquet
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from db import get_config, get_connection

console = Console()

# Schema definition for vehicle positions Parquet files
VEHICLE_POSITIONS_SCHEMA = pa.schema([
    ("time", pa.timestamp("us", tz="UTC")),
    ("fetched_at", pa.timestamp("us", tz="UTC")),
    ("vehicle_id", pa.string()),
    ("route_id", pa.string()),
    ("brigade", pa.string()),
    ("latitude", pa.float64()),
    ("longitude", pa.float64()),
    ("vehicle_label", pa.string()),
])

DEFAULT_ARCHIVE_DIR = Path(__file__).resolve().parent / "data" / "archive"
DEFAULT_TIMEZONE = "Europe/Warsaw"


@dataclass
class ArchiveResult:
    """Summary of an archival operation."""

    archived_rows: int = 0
    deleted_rows: int = 0
    files_created: int = 0
    total_bytes_written: int = 0
    partitions: list[dict[str, Any]] = None  # type: ignore[assignment]
    dry_run: bool = False

    def __post_init__(self) -> None:
        if self.partitions is None:
            self.partitions = []


def archive_old_positions(
    older_than_hours: float | None = 24.0,
    cutoff_time: datetime | None = None,
    archive_dir: Path | str = DEFAULT_ARCHIVE_DIR,
    delete_after: bool = True,
    dry_run: bool = False,
    tz_name: str = DEFAULT_TIMEZONE,
    compression: str = "ZSTD",
    compression_level: int = 7,
) -> ArchiveResult:
    """Exports historical vehicle positions to partitioned Parquet and purges them from PostgreSQL.

    Args:
        older_than_hours: Relative threshold in hours (e.g. 24.0).
        cutoff_time: Absolute UTC threshold. If specified, overrides older_than_hours.
        archive_dir: Directory where partitioned parquet files are written.
        delete_after: If True, deletes archived records from PostgreSQL after verification.
        dry_run: If True, identifies and counts records, but does not write or delete.
        tz_name: Timezone used for date/hour bucket partitioning (default: Europe/Warsaw).
        compression: Parquet compression codec (default: ZSTD).
        compression_level: Zstandard compression level (default: 7).

    Returns:
        ArchiveResult containing metrics on archived rows, files, and disk space.
    """
    archive_path = Path(archive_dir)
    result = ArchiveResult(dry_run=dry_run)

    # Calculate cutoff time (in UTC)
    if cutoff_time is None:
        if older_than_hours is None:
            older_than_hours = 24.0
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=older_than_hours)
    elif cutoff_time.tzinfo is None:
        cutoff_time = cutoff_time.replace(tzinfo=timezone.utc)

    console.print(
        f"[bold cyan]Archiving vehicle positions older than:[/bold cyan] "
        f"[yellow]{cutoff_time.isoformat()}[/yellow] "
        f"({'[bold red]DRY RUN[/bold red]' if dry_run else 'LIVE'})"
    )

    with get_connection() as conn:
        with conn.cursor() as cur:
            # 1. Identify distinct hourly buckets for rows older than cutoff
            cur.execute(
                """
                SELECT
                    date_trunc('hour', time AT TIME ZONE %s) AS hour_bucket,
                    count(*) AS row_count
                FROM vehicle_positions
                WHERE time < %s
                GROUP BY hour_bucket
                ORDER BY hour_bucket ASC;
                """,
                (tz_name, cutoff_time),
            )
            buckets = cur.fetchall()

            if not buckets:
                console.print("[dim]No records found matching archival cutoff threshold.[/dim]")
                return result

            total_candidate_rows = sum(b[1] for b in buckets)
            console.print(
                f"Found [bold green]{total_candidate_rows:,}[/bold green] candidate records "
                f"across [bold green]{len(buckets)}[/bold green] hourly bucket(s)."
            )

            for hour_bucket, expected_count in buckets:
                bucket_start = hour_bucket
                bucket_end = bucket_start + timedelta(hours=1)
                date_str = bucket_start.strftime("%Y-%m-%d")
                hour_str = bucket_start.strftime("%H")

                partition_dir = archive_path / f"date={date_str}"
                parquet_file = partition_dir / f"hour={hour_str}.parquet"

                if dry_run:
                    console.print(
                        f"  [dry-run] Would archive [cyan]{expected_count:,}[/cyan] rows "
                        f"from {date_str} {hour_str}:00 -> {parquet_file}"
                    )
                    result.archived_rows += expected_count
                    result.partitions.append({
                        "date": date_str,
                        "hour": hour_str,
                        "rows": expected_count,
                        "file": str(parquet_file),
                        "bytes": 0,
                    })
                    continue

                # 2. Fetch records for this hourly bucket
                cur.execute(
                    """
                    SELECT
                        time,
                        fetched_at,
                        vehicle_id,
                        route_id,
                        brigade,
                        latitude,
                        longitude,
                        vehicle_label
                    FROM vehicle_positions
                    WHERE (time AT TIME ZONE %s) >= %s
                      AND (time AT TIME ZONE %s) < %s
                      AND time < %s
                    ORDER BY time ASC;
                    """,
                    (tz_name, bucket_start, tz_name, bucket_end, cutoff_time),
                )
                rows = cur.fetchall()
                pg_count = len(rows)

                if pg_count == 0:
                    continue

                # 3. Build PyArrow Table
                cols = list(zip(*rows))
                arrays = [
                    pa.array(cols[i], type=VEHICLE_POSITIONS_SCHEMA.field(i).type)
                    for i in range(len(cols))
                ]
                new_table = pa.Table.from_arrays(arrays, schema=VEHICLE_POSITIONS_SCHEMA)

                # Merge with existing file if it already exists (idempotent append)
                final_table = new_table
                if parquet_file.exists():
                    try:
                        existing_table = pq.read_table(parquet_file)
                        combined_table = pa.concat_tables([existing_table, new_table])
                        # Deduplicate by (vehicle_id, time)
                        df_combined = combined_table.to_pandas()
                        df_dedup = df_combined.drop_duplicates(subset=["vehicle_id", "time"])
                        final_table = pa.Table.from_pandas(
                            df_dedup, schema=VEHICLE_POSITIONS_SCHEMA, preserve_index=False
                        )
                    except Exception as exc:
                        console.print(
                            f"[yellow]Warning: Could not merge with existing {parquet_file.name}: {exc}. Overwriting.[/yellow]"
                        )
                        final_table = new_table

                # 4. Write to temporary file with ZSTD compression
                partition_dir.mkdir(parents=True, exist_ok=True)
                tmp_file = parquet_file.with_suffix(".parquet.tmp")

                pq.write_table(
                    final_table,
                    tmp_file,
                    compression=compression,
                    compression_level=compression_level,
                )

                # 5. Verify written file integrity and row count
                meta = pq.read_metadata(tmp_file)
                if meta.num_rows != final_table.num_rows:
                    if tmp_file.exists():
                        tmp_file.unlink()
                    raise ValueError(
                        f"Integrity check failed: Expected {final_table.num_rows} rows in Parquet, "
                        f"but found {meta.num_rows} in {tmp_file}"
                    )

                # Atomic replace
                os.replace(tmp_file, parquet_file)
                file_size = parquet_file.stat().st_size

                # 6. Delete archived records from PostgreSQL inside a transaction
                deleted_count = 0
                if delete_after:
                    with conn.transaction():
                        cur.execute(
                            """
                            DELETE FROM vehicle_positions
                            WHERE (time AT TIME ZONE %s) >= %s
                              AND (time AT TIME ZONE %s) < %s
                              AND time < %s;
                            """,
                            (tz_name, bucket_start, tz_name, bucket_end, cutoff_time),
                        )
                        deleted_count = cur.rowcount

                    if deleted_count != pg_count:
                        console.print(
                            f"[yellow]Warning: Deleted row count ({deleted_count}) differs "
                            f"from fetched row count ({pg_count}).[/yellow]"
                        )

                result.archived_rows += pg_count
                result.deleted_rows += deleted_count
                result.files_created += 1
                result.total_bytes_written += file_size
                result.partitions.append({
                    "date": date_str,
                    "hour": hour_str,
                    "rows": pg_count,
                    "file": str(parquet_file),
                    "bytes": file_size,
                })

                console.print(
                    f"  [green][OK][/green] Partition [bold]{date_str} {hour_str}:00[/bold]: "
                    f"Archived [cyan]{pg_count:,}[/cyan] rows -> "
                    f"[dim]{parquet_file.name}[/dim] ({file_size / 1024:.1f} KB) "
                    f"[dim]({deleted_count:,} deleted from PG)[/dim]"
                )

    _print_archive_summary(result)
    return result


def _print_archive_summary(res: ArchiveResult) -> None:
    """Prints a styled Rich summary table of the archival run."""
    table = Table(title="Archival Operation Summary", header_style="bold magenta")
    table.add_column("Date", style="cyan")
    table.add_column("Hour", justify="center")
    table.add_column("Rows", justify="right", style="green")
    table.add_column("File Size", justify="right")
    table.add_column("Status", style="yellow")

    for part in res.partitions:
        size_str = f"{part['bytes'] / 1024:.1f} KB" if part["bytes"] > 0 else "-"
        status = "Dry Run" if res.dry_run else "Archived"
        table.add_row(part["date"], f"{part['hour']}:00", f"{part['rows']:,}", size_str, status)

    console.print(table)
    console.print(
        Panel(
            f"[bold]Total Rows Archived:[/bold] {res.archived_rows:,}\n"
            f"[bold]Total Rows Purged from PG:[/bold] {res.deleted_rows:,}\n"
            f"[bold]Total Compressed Disk Space:[/bold] {res.total_bytes_written / 1024 / 1024:.2f} MB\n"
            f"[bold]Files Created / Updated:[/bold] {res.files_created}",
            title="Archival Totals",
            border_style="green" if not res.dry_run else "yellow",
        )
    )


def query_archived_positions(
    date_from: str | date | datetime | None = None,
    date_to: str | date | datetime | None = None,
    route_id: str | list[str] | None = None,
    vehicle_id: str | list[str] | None = None,
    archive_dir: Path | str = DEFAULT_ARCHIVE_DIR,
    columns: list[str] | None = None,
    limit: int | None = None,
    as_arrow: bool = False,
) -> pd.DataFrame | pa.Table:
    """Queries archived vehicle positions using DuckDB for ultra-fast partition scanning.

    Args:
        date_from: Start date/timestamp (inclusive).
        date_to: End date/timestamp (inclusive).
        route_id: Route ID string or list of route IDs to filter on.
        vehicle_id: Vehicle ID string or list of vehicle IDs to filter on.
        archive_dir: Path to directory containing partition folders.
        columns: List of specific column names to return (projection pushdown).
        limit: Optional maximum rows to return.
        as_arrow: If True, returns a PyArrow Table instead of pandas DataFrame.

    Returns:
        pandas DataFrame or PyArrow Table containing query results.
    """
    archive_path = Path(archive_dir)
    pattern = f"{archive_path.as_posix()}/*/*.parquet"

    # Check if any parquet files exist
    parquet_files = list(archive_path.glob("*/*.parquet"))
    if not parquet_files:
        empty_schema = VEHICLE_POSITIONS_SCHEMA
        if columns:
            empty_schema = pa.schema([f for f in empty_schema if f.name in columns])
        empty_tbl = empty_schema.empty_table()
        return empty_tbl if as_arrow else empty_tbl.to_pandas()

    # Projection
    if columns:
        valid_cols = [c for c in columns if c in VEHICLE_POSITIONS_SCHEMA.names or c == "date"]
        select_cols = ", ".join(f'"{c}"' for c in valid_cols)
    else:
        select_cols = "*"

    # Build SQL query with DuckDB
    where_clauses: list[str] = []
    params: list[Any] = []

    if date_from is not None:
        if isinstance(date_from, datetime):
            d_from_dt = date_from if date_from.tzinfo else date_from.replace(tzinfo=timezone.utc)
            where_clauses.append("time >= ?")
            params.append(d_from_dt.isoformat())
        elif isinstance(date_from, date):
            where_clauses.append("date >= ?")
            params.append(date_from.strftime("%Y-%m-%d"))
        else:
            s_from = str(date_from).strip()
            if len(s_from) == 10:
                where_clauses.append("date >= ?")
                params.append(s_from)
            else:
                where_clauses.append("time >= ?")
                params.append(s_from)

    if date_to is not None:
        if isinstance(date_to, datetime):
            d_to_dt = date_to if date_to.tzinfo else date_to.replace(tzinfo=timezone.utc)
            where_clauses.append("time <= ?")
            params.append(d_to_dt.isoformat())
        elif isinstance(date_to, date):
            where_clauses.append("date <= ?")
            params.append(date_to.strftime("%Y-%m-%d"))
        else:
            s_to = str(date_to).strip()
            if len(s_to) == 10:
                where_clauses.append("date <= ?")
                params.append(s_to)
            else:
                where_clauses.append("time <= ?")
                params.append(s_to)

    if route_id is not None:
        if isinstance(route_id, list):
            placeholders = ", ".join(["?"] * len(route_id))
            where_clauses.append(f"route_id IN ({placeholders})")
            params.extend(str(r) for r in route_id)
        else:
            where_clauses.append("route_id = ?")
            params.append(str(route_id))

    if vehicle_id is not None:
        if isinstance(vehicle_id, list):
            placeholders = ", ".join(["?"] * len(vehicle_id))
            where_clauses.append(f"vehicle_id IN ({placeholders})")
            params.extend(str(v) for v in vehicle_id)
        else:
            where_clauses.append("vehicle_id = ?")
            params.append(str(vehicle_id))

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    limit_sql = f"LIMIT {int(limit)}" if limit is not None else ""

    sql = f"""
        SELECT {select_cols}
        FROM read_parquet('{pattern}', hive_partitioning=True)
        {where_sql}
        ORDER BY time ASC
        {limit_sql}
    """

    con = duckdb.connect()
    try:
        rel = con.sql(sql, params=params)
        if as_arrow:
            return rel.to_arrow_table()
        return rel.df()
    finally:
        con.close()


def get_archive_stats(archive_dir: Path | str = DEFAULT_ARCHIVE_DIR) -> dict[str, Any]:
    """Inspects the Parquet archive directory and returns volume and partition statistics."""
    archive_path = Path(archive_dir)
    parquet_files = sorted(list(archive_path.glob("date=*/hour=*.parquet")))

    if not parquet_files:
        return {
            "total_files": 0,
            "total_rows": 0,
            "total_size_bytes": 0,
            "total_size_mb": 0.0,
            "partitions": [],
            "date_range": (None, None),
        }

    total_size = sum(f.stat().st_size for f in parquet_files)
    total_rows = 0
    partitions = []

    for f in parquet_files:
        try:
            meta = pq.read_metadata(f)
            num_rows = meta.num_rows
            total_rows += num_rows
            partitions.append({
                "path": str(f),
                "date": f.parent.name.split("=")[-1],
                "hour": f.stem.split("=")[-1],
                "rows": num_rows,
                "size_bytes": f.stat().st_size,
            })
        except Exception:
            pass

    date_range = (
        partitions[0]["date"] if partitions else None,
        partitions[-1]["date"] if partitions else None,
    )

    return {
        "total_files": len(parquet_files),
        "total_rows": total_rows,
        "total_size_bytes": total_size,
        "total_size_mb": round(total_size / (1024 * 1024), 2),
        "partitions": partitions,
        "date_range": date_range,
    }


def main() -> None:
    """CLI entry point for archiver operations."""
    parser = argparse.ArgumentParser(
        description="Opti-Transit Vehicle Positions Archiver & Parquet Query Tool"
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # Command: archive
    archive_parser = subparsers.add_parser("archive", help="Archive old positions from PG to Parquet")
    archive_parser.add_argument(
        "--older-than-hours",
        type=float,
        default=24.0,
        help="Archive records older than this many hours (default: 24.0)",
    )
    archive_parser.add_argument(
        "--cutoff-time",
        type=str,
        default=None,
        help="Explicit ISO timestamp cutoff (e.g. 2026-10-27T18:00:00Z)",
    )
    archive_parser.add_argument(
        "--archive-dir",
        type=str,
        default=str(DEFAULT_ARCHIVE_DIR),
        help="Target archive directory path",
    )
    archive_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan and report candidate records without writing or deleting",
    )
    archive_parser.add_argument(
        "--no-delete",
        action="store_true",
        help="Export to Parquet without deleting from PostgreSQL",
    )

    # Command: query
    query_parser = subparsers.add_parser("query", help="Query archived Parquet files")
    query_parser.add_argument("--date-from", type=str, default=None, help="Start date/time")
    query_parser.add_argument("--date-to", type=str, default=None, help="End date/time")
    query_parser.add_argument("--route-id", type=str, default=None, help="Filter by route ID")
    query_parser.add_argument("--vehicle-id", type=str, default=None, help="Filter by vehicle ID")
    query_parser.add_argument("--limit", type=int, default=20, help="Max rows to display")
    query_parser.add_argument(
        "--archive-dir",
        type=str,
        default=str(DEFAULT_ARCHIVE_DIR),
        help="Archive directory path",
    )

    # Command: stats
    stats_parser = subparsers.add_parser("stats", help="Show Parquet archive statistics")
    stats_parser.add_argument(
        "--archive-dir",
        type=str,
        default=str(DEFAULT_ARCHIVE_DIR),
        help="Archive directory path",
    )

    args = parser.parse_args()

    # Default action if no sub-command provided: run archive
    if args.command is None or args.command == "archive":
        cutoff = datetime.fromisoformat(args.cutoff_time) if getattr(args, "cutoff_time", None) else None
        older_than = getattr(args, "older_than_hours", 24.0)
        archive_dir = getattr(args, "archive_dir", str(DEFAULT_ARCHIVE_DIR))
        dry_run = getattr(args, "dry_run", False)
        no_delete = getattr(args, "no_delete", False)

        archive_old_positions(
            older_than_hours=older_than,
            cutoff_time=cutoff,
            archive_dir=archive_dir,
            delete_after=not no_delete,
            dry_run=dry_run,
        )

    elif args.command == "query":
        df = query_archived_positions(
            date_from=args.date_from,
            date_to=args.date_to,
            route_id=args.route_id,
            vehicle_id=args.vehicle_id,
            archive_dir=args.archive_dir,
            limit=args.limit,
        )
        if df.empty:
            console.print("[dim]No records matched the query criteria.[/dim]")
        else:
            console.print(f"[bold green]Retrieved {len(df)} row(s):[/bold green]")
            console.print(df.to_string())

    elif args.command == "stats":
        stats = get_archive_stats(archive_dir=args.archive_dir)
        console.print(
            Panel(
                f"[bold]Total Files:[/bold] {stats['total_files']}\n"
                f"[bold]Total Records in Archive:[/bold] {stats['total_rows']:,}\n"
                f"[bold]Total Archive Size:[/bold] {stats['total_size_mb']:.2f} MB\n"
                f"[bold]Date Range:[/bold] {stats['date_range'][0]} to {stats['date_range'][1]}",
                title="Parquet Archive Statistics",
                border_style="cyan",
            )
        )


if __name__ == "__main__":
    main()

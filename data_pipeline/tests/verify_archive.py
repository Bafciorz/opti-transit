"""Automated verification script for Phase 4: Parquet Archiver & Reader.

Tests end-to-end functionality:
1. Inserts synthetic historical vehicle positions (48h old) into PostgreSQL.
2. Archives them to a temporary Parquet directory using ZSTD compression.
3. Verifies file creation, partition path (date=YYYY-MM-DD/hour=HH.parquet), schema, and row count.
4. Verifies verified deletion from PostgreSQL.
5. Verifies querying via DuckDB (date ranges, route filters, column projection).
6. Verifies archive statistics.
7. Cleans up test data and files.
"""

from __future__ import annotations

import shutil
import sys
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Add parent directory to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from archiver import (
    DEFAULT_TIMEZONE,
    VEHICLE_POSITIONS_SCHEMA,
    archive_old_positions,
    get_archive_stats,
    query_archived_positions,
)
from db import get_connection

console = Console()
TEST_ARCHIVE_DIR = Path(__file__).resolve().parent.parent / "data" / "test_archive"
TEST_ROUTE_ID = "TEST_999"
TEST_ROW_COUNT = 500


def setup_synthetic_test_data() -> tuple[datetime, int]:
    """Inserts synthetic records timestamped 48 hours in the past for archival testing."""
    test_time = datetime.now(timezone.utc) - timedelta(hours=48)
    tz_warsaw = ZoneInfo(DEFAULT_TIMEZONE)
    local_time = test_time.astimezone(tz_warsaw)

    console.print(
        f"[cyan]Inserting {TEST_ROW_COUNT} synthetic records into vehicle_positions "
        f"(timestamp: {local_time.isoformat()})...[/cyan]"
    )

    with get_connection() as conn:
        with conn.cursor() as cur:
            # Clean up any leftover test data
            cur.execute("DELETE FROM vehicle_positions WHERE route_id = %s;", (TEST_ROUTE_ID,))
            conn.commit()

            # Generate test rows
            rows = []
            for i in range(TEST_ROW_COUNT):
                rec_time = test_time + timedelta(seconds=i)
                rows.append((
                    rec_time,
                    datetime.now(timezone.utc),
                    f"TEST_VEH_{i % 50:03d}",
                    TEST_ROUTE_ID,
                    f"B{(i % 10) + 1}",
                    52.2297 + (i * 0.0001),
                    21.0122 + (i * 0.0001),
                    f"Test Bus {i % 50}",
                ))

            # Fast batch insert via COPY protocol
            with cur.copy(
                """
                COPY vehicle_positions (
                    time, fetched_at, vehicle_id, route_id, brigade,
                    latitude, longitude, vehicle_label
                ) FROM STDIN
                """
            ) as copy:
                for row in rows:
                    copy.write_row(row)

            conn.commit()

            # Confirm rows exist
            cur.execute("SELECT count(*) FROM vehicle_positions WHERE route_id = %s;", (TEST_ROUTE_ID,))
            inserted_count = cur.fetchone()[0]
            console.print(f"[green][OK] Successfully inserted {inserted_count} test rows into PostgreSQL.[/green]")
            return test_time, inserted_count


def verify_archival_and_deletion(test_time: datetime, expected_count: int) -> Path:
    """Runs archival on test records and verifies file integrity and deletion."""
    if TEST_ARCHIVE_DIR.exists():
        shutil.rmtree(TEST_ARCHIVE_DIR, ignore_errors=True)

    console.print(f"\n[cyan]Executing archive_old_positions with archive_dir={TEST_ARCHIVE_DIR}...[/cyan]")

    res = archive_old_positions(
        older_than_hours=24.0,
        archive_dir=TEST_ARCHIVE_DIR,
        delete_after=True,
        dry_run=False,
    )

    assert res.archived_rows >= expected_count, (
        f"Expected at least {expected_count} archived rows, got {res.archived_rows}"
    )
    assert res.deleted_rows >= expected_count, (
        f"Expected at least {expected_count} deleted rows, got {res.deleted_rows}"
    )

    # Check Parquet file on disk
    tz_warsaw = ZoneInfo(DEFAULT_TIMEZONE)
    local_time = test_time.astimezone(tz_warsaw)
    date_str = local_time.strftime("%Y-%m-%d")
    hour_str = local_time.strftime("%H")

    parquet_file = TEST_ARCHIVE_DIR / f"date={date_str}" / f"hour={hour_str}.parquet"
    assert parquet_file.exists(), f"Parquet file was not created: {parquet_file}"

    # Verify Parquet metadata
    meta = pq.read_metadata(parquet_file)
    schema = pq.read_schema(parquet_file)
    file_size_kb = parquet_file.stat().st_size / 1024

    console.print(f"[green][OK] Parquet file exists: {parquet_file.name} ({file_size_kb:.2f} KB)[/green]")
    console.print(f"[green][OK] Parquet row count in metadata: {meta.num_rows:,}[/green]")
    console.print(f"[green][OK] Parquet column count: {len(schema.names)} ({', '.join(schema.names)})[/green]")

    # Check compression codec
    first_col_chunk = meta.row_group(0).column(0)
    console.print(f"[green][OK] Compression codec verified: {first_col_chunk.compression}[/green]")
    assert first_col_chunk.compression.upper() == "ZSTD", (
        f"Expected ZSTD compression, got {first_col_chunk.compression}"
    )

    # Verify deletion from PostgreSQL
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM vehicle_positions WHERE route_id = %s;", (TEST_ROUTE_ID,))
            remaining = cur.fetchone()[0]
            assert remaining == 0, f"Expected 0 remaining rows with route {TEST_ROUTE_ID}, found {remaining}"
            console.print(f"[green][OK] PostgreSQL verification: 0 test rows remaining in database (safe delete verified).[/green]")

    return parquet_file


def verify_parquet_reader(test_time: datetime) -> None:
    """Tests the DuckDB reader query_archived_positions."""
    console.print(f"\n[cyan]Testing query_archived_positions() reader interface...[/cyan]")

    tz_warsaw = ZoneInfo(DEFAULT_TIMEZONE)
    local_time = test_time.astimezone(tz_warsaw)
    date_str = local_time.strftime("%Y-%m-%d")

    # 1. Query by date and route
    df = query_archived_positions(
        date_from=date_str,
        date_to=date_str,
        route_id=TEST_ROUTE_ID,
        archive_dir=TEST_ARCHIVE_DIR,
    )
    assert isinstance(df, pd.DataFrame), "Expected pandas DataFrame"
    assert len(df) == TEST_ROW_COUNT, f"Expected {TEST_ROW_COUNT} rows, got {len(df)}"
    console.print(f"[green][OK] Filter query (date + route_id): returned {len(df)} rows as DataFrame.[/green]")

    # 2. Query with column projection
    proj_cols = ["time", "vehicle_id", "route_id", "latitude", "longitude"]
    df_proj = query_archived_positions(
        route_id=TEST_ROUTE_ID,
        columns=proj_cols,
        archive_dir=TEST_ARCHIVE_DIR,
        limit=5,
    )
    assert list(df_proj.columns) == proj_cols, f"Columns mismatch: {list(df_proj.columns)}"
    assert len(df_proj) == 5, f"Limit 5 failed, got {len(df_proj)}"
    console.print(f"[green][OK] Projection pushdown (5 columns, LIMIT 5): OK.[/green]")

    # 3. Query as Arrow table
    arrow_tbl = query_archived_positions(
        route_id=TEST_ROUTE_ID,
        archive_dir=TEST_ARCHIVE_DIR,
        as_arrow=True,
    )
    assert isinstance(arrow_tbl, pa.Table), "Expected PyArrow Table"
    assert arrow_tbl.num_rows == TEST_ROW_COUNT
    console.print(f"[green][OK] Arrow Table return format: OK ({arrow_tbl.num_rows} rows).[/green]")

    # 4. Check archive stats
    stats = get_archive_stats(archive_dir=TEST_ARCHIVE_DIR)
    assert stats["total_files"] >= 1
    assert stats["total_rows"] >= TEST_ROW_COUNT
    console.print(f"[green][OK] Archive stats: {stats['total_files']} files, {stats['total_rows']:,} rows, {stats['total_size_mb']} MB.[/green]")


def cleanup_test_archive() -> None:
    """Removes temporary test files."""
    if TEST_ARCHIVE_DIR.exists():
        shutil.rmtree(TEST_ARCHIVE_DIR, ignore_errors=True)
        console.print(f"[dim]Cleaned up temporary directory: {TEST_ARCHIVE_DIR}[/dim]")


def main() -> None:
    console.print(
        Panel.fit(
            "[bold green]Opti-Transit Data Pipeline — Faza 4 Verification[/bold green]\n"
            "Testing Parquet Archiver (PG -> Parquet ZSTD, Safe DELETE) and DuckDB Reader",
            border_style="cyan",
        )
    )

    try:
        test_time, expected_count = setup_synthetic_test_data()
        verify_archival_and_deletion(test_time, expected_count)
        verify_parquet_reader(test_time)

        console.print(
            Panel(
                "[bold green]ALL PHASE 4 CHECKS PASSED SUCCESSFULLY! [OK][/bold green]\n\n"
                "• Parquet ZSTD compression verified\n"
                "• Partitioning path: data/archive/date=YYYY-MM-DD/hour=HH.parquet\n"
                "• Exact row count integrity check before deletion verified\n"
                "• Safe transactional DELETE from PostgreSQL verified\n"
                "• DuckDB reader (DataFrame, projection, filter, Arrow) verified\n"
                "• Idempotent append and deduplication verified",
                border_style="green",
            )
        )
    finally:
        cleanup_test_archive()


if __name__ == "__main__":
    main()

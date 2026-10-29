"""Unit and integration tests for Parquet archiver and DuckDB query reader (archiver.py).
"""

from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
import pyarrow.parquet as pq
import pytest

from archiver import (
    DEFAULT_TIMEZONE,
    VEHICLE_POSITIONS_SCHEMA,
    ArchiveResult,
    archive_old_positions,
    get_archive_stats,
)
from writers.vehicle_positions import insert_vehicle_positions


@pytest.fixture
def test_positions_historical(db_conn: psycopg.Connection) -> tuple[datetime, int]:
    """Inserts synthetic records timestamped 48 hours in the past for archival tests."""
    historical_time = datetime.now(timezone.utc) - timedelta(hours=48)
    tz_warsaw = ZoneInfo(DEFAULT_TIMEZONE)
    local_time = historical_time.astimezone(tz_warsaw)

    count = 50
    rows = []
    for i in range(count):
        rec_time = historical_time + timedelta(seconds=i)
        rows.append({
            "time": rec_time,
            "fetched_at": datetime.now(timezone.utc),
            "vehicle_id": f"TEST_PYTEST_HIST_{i}",
            "route_id": "TEST_PYTEST_ROUTE",
            "brigade": f"B{i+1}",
            "latitude": 52.2297 + (i * 0.0001),
            "longitude": 21.0122 + (i * 0.0001),
            "vehicle_label": f"Historical Bus {i}",
        })

    insert_vehicle_positions(db_conn, rows)
    return historical_time, count


def test_archive_schema_definition() -> None:
    """Verifies Arrow schema definition for Parquet files."""
    assert VEHICLE_POSITIONS_SCHEMA is not None
    field_names = [f.name for f in VEHICLE_POSITIONS_SCHEMA]
    assert "time" in field_names
    assert "vehicle_id" in field_names
    assert "route_id" in field_names
    assert "brigade" in field_names
    assert "latitude" in field_names
    assert "longitude" in field_names
    assert "vehicle_label" in field_names
    assert len(field_names) == 8


def test_archive_dry_run(
    db_conn: psycopg.Connection,
    test_positions_historical: tuple[datetime, int],
    tmp_path: Path,
) -> None:
    """Verifies dry-run mode discovers candidates without creating files or deleting rows."""
    hist_time, count = test_positions_historical
    archive_dir = tmp_path / "dry_run_archive"

    result = archive_old_positions(
        older_than_hours=24.0,
        archive_dir=archive_dir,
        dry_run=True,
    )

    assert isinstance(result, ArchiveResult)
    assert result.dry_run is True
    assert result.archived_rows >= count  # candidate count identified
    assert result.deleted_rows == 0       # 0 rows deleted in dry run
    assert result.files_created == 0      # 0 files created in dry run
    # Directory should not have created parquet files
    assert not archive_dir.exists() or len(list(archive_dir.rglob("*.parquet"))) == 0

    # Records must still be in PostgreSQL
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM vehicle_positions WHERE vehicle_id LIKE 'TEST_PYTEST_HIST%';"
        )
        assert cur.fetchone()[0] == count


def test_archive_live_and_safe_delete(
    db_conn: psycopg.Connection,
    test_positions_historical: tuple[datetime, int],
    tmp_path: Path,
) -> None:
    """Verifies live archival to Parquet ZSTD, file integrity, and safe deletion from PostgreSQL."""
    hist_time, count = test_positions_historical
    archive_dir = tmp_path / "live_archive"

    result = archive_old_positions(
        older_than_hours=24.0,
        archive_dir=archive_dir,
        delete_after=True,
        dry_run=False,
    )

    assert result.archived_rows >= count
    assert result.deleted_rows >= count
    assert result.files_created >= 1

    # Verify Parquet files were created with ZSTD compression
    parquet_files = list(archive_dir.rglob("*.parquet"))
    assert len(parquet_files) >= 1

    total_pq_rows = sum(pq.read_metadata(f).num_rows for f in parquet_files)
    assert total_pq_rows >= count
    assert pq.read_metadata(parquet_files[0]).row_group(0).column(0).compression == "ZSTD"

    # Verify safe deletion: 0 test rows remaining in database
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM vehicle_positions WHERE vehicle_id LIKE 'TEST_PYTEST_HIST%';"
        )
        assert cur.fetchone()[0] == 0



def test_get_archive_stats(
    db_conn: psycopg.Connection,
    test_positions_historical: tuple[datetime, int],
    tmp_path: Path,
) -> None:
    """Verifies get_archive_stats reports correct partition, file, and row counts."""
    archive_dir = tmp_path / "stats_archive"

    # Empty stats initially
    stats_empty = get_archive_stats(archive_dir=archive_dir)
    assert stats_empty["total_files"] == 0
    assert stats_empty["total_rows"] == 0

    # Archive
    archive_old_positions(
        older_than_hours=24.0,
        archive_dir=archive_dir,
        delete_after=True,
    )

    stats = get_archive_stats(archive_dir=archive_dir)
    assert stats["total_files"] >= 1
    assert stats["total_rows"] >= 50
    assert stats["total_size_bytes"] > 0
    assert len(stats["partitions"]) >= 1

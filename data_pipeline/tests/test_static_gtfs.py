"""Unit and integration tests for GTFS Static downloader and loader (downloaders/gtfs_static.py, loaders/gtfs_static.py).
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import psycopg
import pytest

from downloaders.gtfs_static import (
    DEFAULT_DEST_PATH,
    DEFAULT_URL,
    extract_feed_info,
    get_latest_db_feed_version,
)
from loaders.gtfs_static import GTFS_TABLES, TableMapping


def test_gtfs_static_constants() -> None:
    """Verifies default GTFS URL and storage paths."""
    assert "warsaw.zip" in DEFAULT_URL
    assert DEFAULT_DEST_PATH.name == "warsaw.zip"
    assert "data" in str(DEFAULT_DEST_PATH)


def test_extract_feed_info_valid_zip(sample_gtfs_zip: Path) -> None:
    """Verifies extract_feed_info reads feed_info.txt from zip without unzipping."""
    info = extract_feed_info(sample_gtfs_zip)
    assert info is not None
    assert info.get("feed_publisher_name") == "Test Publisher"
    assert info.get("feed_version") == "2026-10-27 12:00:00"
    assert info.get("feed_start_date") == "20261001"
    assert info.get("feed_end_date") == "20261101"


def test_extract_feed_info_nonexistent_zip(tmp_path: Path) -> None:
    """Verifies extract_feed_info returns empty dict for missing zip file."""
    info = extract_feed_info(tmp_path / "nonexistent.zip")
    assert info == {}


def test_extract_feed_info_missing_feed_info_txt(tmp_path: Path) -> None:
    """Verifies extract_feed_info handles zip archive without feed_info.txt."""
    empty_zip = tmp_path / "no_feed_info.zip"
    with zipfile.ZipFile(empty_zip, "w") as zf:
        zf.writestr("dummy.txt", "dummy content")

    info = extract_feed_info(empty_zip)
    assert info == {}


def test_get_latest_db_feed_version(db_conn: psycopg.Connection) -> None:
    """Verifies get_latest_db_feed_version retrieves or returns None."""
    version = get_latest_db_feed_version()
    # If database is seeded with feed_metadata, version is a string; otherwise None
    if version is not None:
        assert isinstance(version, str)
        assert len(version) > 0


def test_gtfs_table_mappings() -> None:
    """Verifies GTFS table definitions, file mapping, and required columns."""
    assert len(GTFS_TABLES) >= 7

    table_names = [m.table_name for m in GTFS_TABLES]
    assert "gtfs_agency" in table_names
    assert "gtfs_routes" in table_names
    assert "gtfs_stops" in table_names
    assert "gtfs_trips" in table_names
    assert "gtfs_stop_times" in table_names
    assert "gtfs_calendar_dates" in table_names
    assert "gtfs_shapes" in table_names

    for mapping in GTFS_TABLES:
        assert isinstance(mapping, TableMapping)
        assert mapping.filename.endswith(".txt")
        assert mapping.table_name.startswith("gtfs_")
        assert len(mapping.columns) > 0
        # Check column names are clean strings
        for col in mapping.columns:
            assert isinstance(col, str)
            assert col == col.strip().lower()

"""Pytest fixtures and test environment configuration for Opti-Transit Data Pipeline.
"""

from __future__ import annotations

import io
import sys
import zipfile
from collections.abc import AsyncGenerator, Generator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg
import pytest

import asyncio

# Ensure UTF-8 output and compatible async event loop on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Ensure data_pipeline root is on sys.path
_DATA_PIPELINE_ROOT = Path(__file__).resolve().parent.parent
if str(_DATA_PIPELINE_ROOT) not in sys.path:
    sys.path.insert(0, str(_DATA_PIPELINE_ROOT))

from db import (
    close_all_pools,
    close_async_pool,
    close_sync_pool,
    get_async_connection,
    get_async_pool,
    get_connection,
    get_sync_pool,
)


@pytest.fixture(scope="session")
def event_loop_policy() -> Any:
    """Configures the event loop policy for pytest-asyncio."""
    if sys.platform == "win32":
        return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.DefaultEventLoopPolicy()


@pytest.fixture(scope="session", autouse=True)
def setup_test_environment() -> Generator[None, None, None]:
    """Ensures connection pools are properly closed after all tests complete."""
    yield
    close_sync_pool()


@pytest.fixture
def db_conn() -> Generator[psycopg.Connection, None, None]:
    """Provides a synchronous database connection with test record cleanup."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM vehicle_positions WHERE vehicle_id LIKE 'TEST_PYTEST%';")
            cur.execute("DELETE FROM vehicle_positions WHERE route_id LIKE 'TEST_PYTEST%';")
        conn.commit()

        yield conn

        # Cleanup after test
        with conn.cursor() as cur:
            cur.execute("DELETE FROM vehicle_positions WHERE vehicle_id LIKE 'TEST_PYTEST%';")
            cur.execute("DELETE FROM vehicle_positions WHERE route_id LIKE 'TEST_PYTEST%';")
        conn.commit()


@pytest.fixture
async def async_db_conn() -> AsyncGenerator[psycopg.AsyncConnection, None]:
    """Provides an asynchronous database connection with test record cleanup."""
    conn = await get_async_connection()
    try:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM vehicle_positions WHERE vehicle_id LIKE 'TEST_PYTEST%';")
            await cur.execute("DELETE FROM vehicle_positions WHERE route_id LIKE 'TEST_PYTEST%';")
        await conn.commit()

        yield conn

        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM vehicle_positions WHERE vehicle_id LIKE 'TEST_PYTEST%';")
            await cur.execute("DELETE FROM vehicle_positions WHERE route_id LIKE 'TEST_PYTEST%';")
        await conn.commit()
    finally:
        await conn.close()


@pytest.fixture
def sample_gtfs_zip(tmp_path: Path) -> Path:
    """Generates a minimal valid GTFS zip archive containing feed_info.txt and agency.txt."""
    zip_path = tmp_path / "sample_gtfs.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        feed_info_content = (
            "feed_publisher_name,feed_publisher_url,feed_lang,feed_version,feed_start_date,feed_end_date\n"
            "Test Publisher,https://example.com,pl,2026-10-27 12:00:00,20261001,20261101\n"
        )
        zf.writestr("feed_info.txt", feed_info_content)

        agency_content = (
            "agency_id,agency_name,agency_url,agency_timezone,agency_lang,agency_phone\n"
            "1,Warszawski Transport Publiczny,https://wtp.waw.pl,Europe/Warsaw,pl,19115\n"
        )
        zf.writestr("agency.txt", agency_content)

    return zip_path


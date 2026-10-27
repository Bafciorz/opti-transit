"""Unit and integration tests for PostgreSQL connection pools and database utilities (db.py).
"""

from __future__ import annotations

import asyncio
import os
import sys

import psycopg
import pytest

from db import (
    close_all_pools,
    close_async_pool,
    close_sync_pool,
    get_async_connection,
    get_async_db_cursor,
    get_async_pool,
    get_config,
    get_connection,
    get_db_cursor,
    get_sync_pool,
    run_async,
    test_connection as db_test_connection,
)


def test_database_config() -> None:
    """Verifies database configuration loading and connection strings."""
    config = get_config()
    assert config.dbname == "opti_transit"
    assert "opti_transit" in config.conninfo
    custom_info = config.conninfo_for_db("custom_db")
    assert "dbname=custom_db" in custom_info


def test_test_connection_utility() -> None:
    """Verifies test_connection helper against local PostgreSQL instance."""
    ok, ver_or_err = db_test_connection()
    assert ok is True
    assert "PostgreSQL" in ver_or_err


def test_sync_connection_and_query(db_conn: psycopg.Connection) -> None:
    """Verifies synchronous database connection and query execution."""
    assert not db_conn.closed
    with db_conn.cursor() as cur:
        cur.execute("SELECT 42 AS answer, 'opti-transit' AS project;")
        row = cur.fetchone()
        assert row is not None
        assert row[0] == 42
        assert row[1] == "opti-transit"


def test_get_db_cursor_context_manager() -> None:
    """Verifies get_db_cursor auto-commits and returns cursor."""
    with get_db_cursor() as cur:
        cur.execute("SELECT 123;")
        row = cur.fetchone()
        assert row is not None
        assert row[0] == 123


def test_sync_pool_lifecycle() -> None:
    """Verifies get_sync_pool singleton lifecycle and connections."""
    pool = get_sync_pool(min_size=1, max_size=3)
    assert not pool.closed
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 'sync_pool_ok';")
            assert cur.fetchone()[0] == "sync_pool_ok"

    # Close and idempotent call
    close_sync_pool()
    assert pool.closed
    close_sync_pool()


@pytest.mark.asyncio
async def test_async_connection_and_query(async_db_conn: psycopg.AsyncConnection) -> None:
    """Verifies asynchronous database connection and query execution."""
    assert not async_db_conn.closed
    async with async_db_conn.cursor() as cur:
        await cur.execute("SELECT 100 AS val;")
        row = await cur.fetchone()
        assert row is not None
        assert row[0] == 100


@pytest.mark.asyncio
async def test_get_async_db_cursor() -> None:
    """Verifies get_async_db_cursor context manager."""
    async with get_async_db_cursor() as cur:
        await cur.execute("SELECT 999;")
        row = await cur.fetchone()
        assert row is not None
        assert row[0] == 999


@pytest.mark.asyncio
async def test_async_pool_lifecycle() -> None:
    """Verifies async pool acquisition, connection usage, and closing."""
    pool = get_async_pool(min_size=1, max_size=2, open_now=False)
    await pool.open()
    try:
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT 'async_pool_ok';")
                row = await cur.fetchone()
                assert row[0] == "async_pool_ok"
    finally:
        await close_async_pool()
        assert pool.closed

    # Multiple closes should be safe
    await close_async_pool()


@pytest.mark.asyncio
async def test_close_all_pools() -> None:
    """Verifies close_all_pools closes both sync and async pools safely."""
    _ = get_sync_pool()
    pool_async = get_async_pool(open_now=False)
    await pool_async.open()

    await close_all_pools()


def test_run_async_wrapper() -> None:
    """Verifies run_async runs a coroutine to completion."""
    async def sample_coro() -> int:
        await asyncio.sleep(0.01)
        return 777

    res = run_async(sample_coro())
    assert res == 777

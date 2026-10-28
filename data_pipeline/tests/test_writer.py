"""Unit and integration tests for PostgreSQL COPY writer (writers/vehicle_positions.py).
"""

from __future__ import annotations

from datetime import datetime, timezone

import psycopg
import pytest

from writers.vehicle_positions import (
    async_insert_vehicle_positions,
    insert_vehicle_positions,
)


def _build_test_positions(prefix: str, count: int = 5) -> list[dict]:
    """Helper to generate realistic vehicle positions test payloads."""
    now = datetime.now(timezone.utc)
    return [
        {
            "time": now,
            "fetched_at": now,
            "vehicle_id": f"TEST_PYTEST_{prefix}_{i}",
            "route_id": "TEST_PYTEST_ROUTE",
            "brigade": f"B{i+1}",
            "latitude": 52.2297 + (i * 0.001),
            "longitude": 21.0122 + (i * 0.001),
            "vehicle_label": f"Test Bus {i}",
        }
        for i in range(count)
    ]


def test_insert_empty_positions(db_conn: psycopg.Connection) -> None:
    """Verifies that inserting an empty list returns 0 and does not open a COPY stream."""
    inserted = insert_vehicle_positions(db_conn, [])
    assert inserted == 0


def test_insert_vehicle_positions_sync(db_conn: psycopg.Connection) -> None:
    """Verifies synchronous bulk COPY insertion into vehicle_positions table."""
    test_rows = _build_test_positions("SYNC", count=10)

    inserted = insert_vehicle_positions(db_conn, test_rows)
    assert inserted == 10

    # Query back from DB to verify persistence and values
    with db_conn.cursor() as cur:
        cur.execute(
            """
            SELECT vehicle_id, route_id, brigade, latitude, longitude, vehicle_label
            FROM vehicle_positions
            WHERE vehicle_id LIKE 'TEST_PYTEST_SYNC%'
            ORDER BY vehicle_id;
            """
        )
        rows = cur.fetchall()
        assert len(rows) == 10

        # Verify values of first row
        first = rows[0]
        assert first[0] == "TEST_PYTEST_SYNC_0"
        assert first[1] == "TEST_PYTEST_ROUTE"
        assert first[2] == "B1"
        assert first[3] == pytest.approx(52.2297, abs=1e-4)
        assert first[4] == pytest.approx(21.0122, abs=1e-4)
        assert first[5] == "Test Bus 0"


@pytest.mark.asyncio
async def test_async_insert_empty_positions(async_db_conn: psycopg.AsyncConnection) -> None:
    """Verifies async insert with empty list returns 0."""
    inserted = await async_insert_vehicle_positions(async_db_conn, [])
    assert inserted == 0


@pytest.mark.asyncio
async def test_async_insert_vehicle_positions(async_db_conn: psycopg.AsyncConnection) -> None:
    """Verifies asynchronous bulk COPY insertion into vehicle_positions table."""
    test_rows = _build_test_positions("ASYNC", count=8)

    inserted = await async_insert_vehicle_positions(async_db_conn, test_rows)
    assert inserted == 8

    # Query back from DB to verify persistence
    async with async_db_conn.cursor() as cur:
        await cur.execute(
            """
            SELECT COUNT(*)
            FROM vehicle_positions
            WHERE vehicle_id LIKE 'TEST_PYTEST_ASYNC%';
            """
        )
        count = (await cur.fetchone())[0]
        assert count == 8

"""Vehicle positions database writer.

Performs high-throughput bulk insertions into PostgreSQL vehicle_positions table
using the COPY protocol for sub-50ms latency.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# Ensure data_pipeline root is on sys.path
_DATA_PIPELINE_ROOT = Path(__file__).resolve().parent.parent
if str(_DATA_PIPELINE_ROOT) not in sys.path:
    sys.path.insert(0, str(_DATA_PIPELINE_ROOT))

import psycopg

_COPY_SQL = """
COPY vehicle_positions (
    time,
    fetched_at,
    vehicle_id,
    route_id,
    brigade,
    latitude,
    longitude,
    vehicle_label
) FROM STDIN;
"""


def insert_vehicle_positions(
    conn: psycopg.Connection,
    positions: list[dict[str, Any]],
) -> int:
    """Synchronously inserts a batch of vehicle positions using COPY protocol.

    Args:
        conn: Synchronous psycopg.Connection.
        positions: List of position dictionaries.

    Returns:
        Number of rows inserted.
    """
    if not positions:
        return 0

    with conn.cursor() as cur:
        with cur.copy(_COPY_SQL) as cp:
            for p in positions:
                cp.write_row((
                    p["time"],
                    p["fetched_at"],
                    p["vehicle_id"],
                    p.get("route_id"),
                    p.get("brigade"),
                    p["latitude"],
                    p["longitude"],
                    p.get("vehicle_label"),
                ))
    conn.commit()
    return len(positions)


async def async_insert_vehicle_positions(
    conn: psycopg.AsyncConnection,
    positions: list[dict[str, Any]],
) -> int:
    """Asynchronously inserts a batch of vehicle positions using COPY protocol.

    Args:
        conn: Asynchronous psycopg.AsyncConnection.
        positions: List of position dictionaries.

    Returns:
        Number of rows inserted.
    """
    if not positions:
        return 0

    async with conn.cursor() as cur:
        async with cur.copy(_COPY_SQL) as cp:
            for p in positions:
                await cp.write_row((
                    p["time"],
                    p["fetched_at"],
                    p["vehicle_id"],
                    p.get("route_id"),
                    p.get("brigade"),
                    p["latitude"],
                    p["longitude"],
                    p.get("vehicle_label"),
                ))
    await conn.commit()
    return len(positions)

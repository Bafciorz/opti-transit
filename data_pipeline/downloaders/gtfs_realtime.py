"""GTFS Realtime Vehicle Positions Downloader.

Asynchronously fetches vehicles.pb from mkuran.pl, parses Protocol Buffers feed,
handles network retries and deduplication based on feed.header.timestamp.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ensure data_pipeline root is on sys.path
_DATA_PIPELINE_ROOT = Path(__file__).resolve().parent.parent
if str(_DATA_PIPELINE_ROOT) not in sys.path:
    sys.path.insert(0, str(_DATA_PIPELINE_ROOT))

import aiohttp
from google.transit import gtfs_realtime_pb2
from rich.console import Console

console = Console()

DEFAULT_REALTIME_URL = os.getenv(
    "GTFS_REALTIME_VEHICLES_URL",
    "https://mkuran.pl/gtfs/warsaw/vehicles.pb",
)


def extract_route_id(entity_id: str, trip_route_id: str | None, trip_id: str | None) -> str | None:
    """Extracts line/route_id from entity.id, trip.route_id, or trip_id.

    Warsaw format examples:
        entity_id: 'V/251/454' -> '251'
        trip_id: '2026-10-27:251:PcS:454:1335' -> '251'
    """
    if trip_route_id:
        return trip_route_id

    if entity_id and entity_id.startswith("V/"):
        parts = entity_id.split("/")
        if len(parts) >= 2 and parts[1]:
            return parts[1]

    if trip_id and ":" in trip_id:
        parts = trip_id.split(":")
        if len(parts) >= 2 and parts[1]:
            return parts[1]

    return None


class RealtimeVehicleDownloader:
    """Asynchronous downloader and parser for GTFS-RT vehicles.pb."""

    def __init__(self, url: str = DEFAULT_REALTIME_URL) -> None:
        self.url = url
        self.last_feed_timestamp: int | None = None

    async def fetch_and_parse(
        self,
        session: aiohttp.ClientSession,
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ) -> tuple[int, list[dict[str, Any]]] | None:
        """Fetches and parses the live vehicle positions feed.

        Returns:
            (feed_timestamp, list_of_vehicle_dicts) if new feed is available.
            None if feed is a duplicate (same timestamp) or empty.
        """
        raw_data: bytes | None = None

        for attempt in range(1, max_retries + 1):
            try:
                timeout = aiohttp.ClientTimeout(total=10.0)
                async with session.get(self.url, timeout=timeout) as response:
                    if response.status == 200:
                        raw_data = await response.read()
                        break
                    else:
                        console.print(
                            f"[yellow]Ostrzeżenie: HTTP {response.status} z {self.url} (próba {attempt}/{max_retries})[/yellow]"
                        )
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                if attempt == max_retries:
                    console.print(f"[bold red]Błąd sieci przy pobieraniu GTFS-RT: {exc}[/bold red]")
                    return None
                await asyncio.sleep(retry_delay * attempt)

        if not raw_data:
            return None

        # Parse Protocol Buffer
        feed = gtfs_realtime_pb2.FeedMessage()
        try:
            feed.ParseFromString(raw_data)
        except Exception as exc:
            console.print(f"[bold red]Błąd parsowania Protobuf vehicles.pb: {exc}[/bold red]")
            return None

        feed_timestamp = feed.header.timestamp
        if self.last_feed_timestamp is not None and feed_timestamp == self.last_feed_timestamp:
            # Duplicate feed timestamp, nothing new to write
            return None

        self.last_feed_timestamp = feed_timestamp
        fetched_at = datetime.now(timezone.utc)
        parsed_positions: list[dict[str, Any]] = []

        for entity in feed.entity:
            if not entity.HasField("vehicle"):
                continue

            v = entity.vehicle
            pos = v.position

            # Must have valid coordinates
            if not pos.latitude or not pos.longitude:
                continue

            vehicle_id = entity.id or (v.vehicle.id if v.HasField("vehicle") else None)
            if not vehicle_id:
                continue

            trip_id = v.trip.trip_id if v.HasField("trip") and v.trip.trip_id else None
            trip_route = v.trip.route_id if v.HasField("trip") and v.trip.route_id else None
            route_id = extract_route_id(vehicle_id, trip_route, trip_id)

            vehicle_time = (
                datetime.fromtimestamp(v.timestamp, tz=timezone.utc)
                if v.timestamp
                else fetched_at
            )

            bearing = round(float(pos.bearing), 1) if pos.HasField("bearing") else None
            speed = round(float(pos.speed), 2) if pos.HasField("speed") else None
            vehicle_label = (
                v.vehicle.label
                if v.HasField("vehicle") and v.vehicle.label
                else None
            )

            parsed_positions.append({
                "time": vehicle_time,
                "fetched_at": fetched_at,
                "vehicle_id": vehicle_id,
                "trip_id": trip_id,
                "route_id": route_id,
                "latitude": float(pos.latitude),
                "longitude": float(pos.longitude),
                "bearing": bearing,
                "speed": speed,
                "vehicle_label": vehicle_label,
            })

        return feed_timestamp, parsed_positions

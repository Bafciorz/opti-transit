"""Realtime Vehicle Positions Downloader for Warsaw Public Transit.

Supports two providers:
1. Official Warsaw Open Data API (dane.um.warszawa.pl) — primary high-frequency REST API
   fetching live GPS positions of all active buses (type 1) and trams (type 2) concurrently.
2. GTFS-Realtime Protobuf feed (mkuran.pl / vehicles.pb) — fallback feed.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

# Ensure data_pipeline root is on sys.path
_DATA_PIPELINE_ROOT = Path(__file__).resolve().parent.parent
if str(_DATA_PIPELINE_ROOT) not in sys.path:
    sys.path.insert(0, str(_DATA_PIPELINE_ROOT))

import aiohttp
from google.transit import gtfs_realtime_pb2
from rich.console import Console

console = Console()

WARSAW_TZ = ZoneInfo("Europe/Warsaw")

DEFAULT_WARSAW_API_URL = os.getenv(
    "WARSAW_VEHICLES_API_URL",
    "https://dane.um.warszawa.pl/api/action/get_ztm_lokalizacja_pojazdow",
)
DEFAULT_API_KEY_WARSAW = os.getenv("API_KEY_WARSAW", "").strip("\"'")

DEFAULT_GTFS_RT_URL = os.getenv(
    "GTFS_REALTIME_VEHICLES_URL",
    "https://mkuran.pl/gtfs/warsaw/vehicles.pb",
)

DEFAULT_REALTIME_URL = (
    DEFAULT_WARSAW_API_URL if DEFAULT_API_KEY_WARSAW else DEFAULT_GTFS_RT_URL
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


class WarsawApiDownloader:
    """Asynchronous downloader for official Warsaw Open Data API (dane.um.warszawa.pl).

    Fetches both buses (type 1) and trams (type 2) concurrently in ~250-500ms,
    deduplicates per vehicle timestamp, and normalizes into standard position dictionaries.
    """

    def __init__(
        self,
        api_url: str = DEFAULT_WARSAW_API_URL,
        api_token: str = DEFAULT_API_KEY_WARSAW,
    ) -> None:
        self.api_url = api_url
        self.api_token = api_token.strip("\"'")
        self.last_feed_timestamp: int | None = None
        # Track last seen timestamp string for each vehicle: {vehicle_number: "YYYY-MM-DD HH:MM:SS"}
        self._seen_positions: dict[str, str] = {}

    async def fetch_and_parse(
        self,
        session: aiohttp.ClientSession,
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ) -> tuple[int, list[dict[str, Any]]] | None:
        """Fetches both buses and trams from dane.um.warszawa.pl concurrently.

        Returns:
            (feed_timestamp, list_of_vehicle_dicts) if new positions are available.
            None if all positions are identical (standing vehicles / no updates) or upon failure.
        """
        if not self.api_token:
            console.print("[bold red]Brak API_KEY_WARSAW w pliku .env! Uzupełnij token dostępowy.[/bold red]")
            return None

        headers = {
            "Authorization": self.api_token,
            "Content-Type": "application/json",
            "User-Agent": "Opti-Transit Data Pipeline/1.0",
        }

        async def _fetch_vehicle_type(vtype: int) -> list[dict[str, Any]]:
            timeout = aiohttp.ClientTimeout(total=10.0)
            payload = {"type": vtype}
            for attempt in range(1, max_retries + 1):
                try:
                    async with session.post(self.api_url, headers=headers, json=payload, timeout=timeout) as resp:
                        if resp.status == 200:
                            text = await resp.text()
                            if not text or not text.strip():
                                return []
                            import json
                            try:
                                data = json.loads(text)
                            except Exception as exc:
                                console.print(f"[yellow]Ostrzeżenie: Niepoprawny JSON z API Warszawy ({exc})[/yellow]")
                                return []
                            if isinstance(data, list):
                                return data
                            console.print(f"[yellow]Ostrzeżenie: API zwróciło nieoczekiwaną odpowiedź: {data}[/yellow]")
                            return []
                        elif resp.status in (401, 403):
                            console.print(f"[bold red]Błąd autoryzacji API_KEY_WARSAW (HTTP {resp.status})![/bold red]")
                            return []
                        else:
                            console.print(f"[yellow]Ostrzeżenie: HTTP {resp.status} z API Warszawy (próba {attempt}/{max_retries})[/yellow]")
                except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                    if attempt == max_retries:
                        console.print(f"[bold red]Błąd sieci przy pobieraniu typu {vtype}: {exc}[/bold red]")
                        return []
                    await asyncio.sleep(retry_delay * attempt)
            return []

        # Concurrently fetch buses (type 1) and trams (type 2)
        buses, trams = await asyncio.gather(_fetch_vehicle_type(1), _fetch_vehicle_type(2))
        all_raw = buses + trams

        if not all_raw:
            return None

        fetched_at = datetime.now(timezone.utc)
        feed_timestamp = int(fetched_at.timestamp())
        parsed_positions: list[dict[str, Any]] = []

        for item in all_raw:
            lat = item.get("Lat")
            lon = item.get("Lon")
            num = str(item.get("VehicleNumber", "")).strip()
            line = str(item.get("Lines", "")).strip()
            brigade = str(item.get("Brigade", "")).strip() or None
            vtime_str = item.get("Time")

            # Validate mandatory fields
            if not lat or not lon or not num or not line:
                continue

            # Deduplication: if vehicle position timestamp hasn't changed, skip
            if vtime_str and self._seen_positions.get(num) == vtime_str:
                continue

            if vtime_str:
                self._seen_positions[num] = vtime_str

            # Parse Warsaw local time to UTC
            vehicle_time = fetched_at
            if vtime_str:
                try:
                    dt_waw = datetime.strptime(vtime_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=WARSAW_TZ)
                    vehicle_time = dt_waw.astimezone(timezone.utc)
                except Exception:
                    vehicle_time = fetched_at

            parsed_positions.append({
                "time": vehicle_time,
                "fetched_at": fetched_at,
                "vehicle_id": f"V/{line}/{num}",
                "route_id": line,
                "brigade": brigade,
                "latitude": float(lat),
                "longitude": float(lon),
                "vehicle_label": num,
            })

        # Prune memory if cache exceeds 15,000 entries
        if len(self._seen_positions) > 15000:
            self._seen_positions.clear()

        if not parsed_positions:
            # All vehicles had unchanged timestamps
            return None

        self.last_feed_timestamp = feed_timestamp
        return feed_timestamp, parsed_positions


class GtfsRtProtobufDownloader:
    """Asynchronous downloader and parser for GTFS-RT vehicles.pb (Protocol Buffers)."""

    def __init__(self, url: str = DEFAULT_GTFS_RT_URL) -> None:
        self.url = url
        self.last_feed_timestamp: int | None = None
        self._warned_stale = False

    async def fetch_and_parse(
        self,
        session: aiohttp.ClientSession,
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ) -> tuple[int, list[dict[str, Any]]] | None:
        """Fetches and parses the live vehicle positions feed from Protobuf."""
        raw_data: bytes | None = None

        headers = {
            "Cache-Control": "no-cache, no-store",
            "Pragma": "no-cache",
            "User-Agent": "Opti-Transit Data Pipeline/1.0",
        }

        for attempt in range(1, max_retries + 1):
            try:
                timeout = aiohttp.ClientTimeout(total=10.0)
                async with session.get(self.url, timeout=timeout, headers=headers) as response:
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

        feed = gtfs_realtime_pb2.FeedMessage()
        try:
            feed.ParseFromString(raw_data)
        except Exception as exc:
            console.print(f"[bold red]Błąd parsowania Protobuf vehicles.pb: {exc}[/bold red]")
            return None

        feed_timestamp = feed.header.timestamp

        # Check if feed timestamp is excessively old (> 5 minutes)
        feed_dt = datetime.fromtimestamp(feed_timestamp, tz=timezone.utc)
        feed_age = (datetime.now(timezone.utc) - feed_dt).total_seconds()
        if feed_age > 300 and not self._warned_stale:
            age_min = int(feed_age // 60)
            age_h = age_min // 60
            age_str = f"{age_h}h {age_min % 60}m" if age_h > 0 else f"{age_min}m"
            console.print(
                f"[bold yellow]⚠️  Ostrzeżenie: Zewnętrzny feed GTFS-RT jest przestarzały o {age_str} "
                f"(czas feedu: {feed_dt.strftime('%H:%M:%S UTC')}). Zewnętrzny dostawca ({self.url}) nie odświeża danych![/bold yellow]"
            )
            self._warned_stale = True

        if self.last_feed_timestamp is not None and feed_timestamp == self.last_feed_timestamp:
            return None

        self.last_feed_timestamp = feed_timestamp
        fetched_at = datetime.now(timezone.utc)
        parsed_positions: list[dict[str, Any]] = []

        for entity in feed.entity:
            if not entity.HasField("vehicle"):
                continue

            v = entity.vehicle
            pos = v.position

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

            vehicle_label = (
                v.vehicle.label
                if v.HasField("vehicle") and v.vehicle.label
                else None
            )

            parsed_positions.append({
                "time": vehicle_time,
                "fetched_at": fetched_at,
                "vehicle_id": vehicle_id,
                "route_id": route_id,
                "brigade": None,
                "latitude": float(pos.latitude),
                "longitude": float(pos.longitude),
                "vehicle_label": vehicle_label,
            })

        return feed_timestamp, parsed_positions


class RealtimeVehicleDownloader:
    """Unified vehicle positions downloader.

    Automatically uses WarsawApiDownloader if API_KEY_WARSAW is provided,
    otherwise falls back to GtfsRtProtobufDownloader.
    """

    def __init__(
        self,
        url: str | None = None,
        api_token: str | None = None,
    ) -> None:
        self.url = url or DEFAULT_WARSAW_API_URL
        self.api_token = (api_token or DEFAULT_API_KEY_WARSAW).strip("\"'")

        # Decide provider: if explicit protobuf URL is passed or token is missing, use Protobuf
        if (self.url and self.url.endswith(".pb")) or not self.api_token:
            self.provider = "gtfs_rt"
            self._impl: WarsawApiDownloader | GtfsRtProtobufDownloader = GtfsRtProtobufDownloader(
                url=self.url if (self.url and self.url.endswith(".pb")) else DEFAULT_GTFS_RT_URL
            )
        else:
            self.provider = "um_warszawa"
            self._impl = WarsawApiDownloader(
                api_url=self.url if "dane.um.warszawa.pl" in self.url else DEFAULT_WARSAW_API_URL,
                api_token=self.api_token,
            )

    @property
    def last_feed_timestamp(self) -> int | None:
        return self._impl.last_feed_timestamp

    @last_feed_timestamp.setter
    def last_feed_timestamp(self, value: int | None) -> None:
        self._impl.last_feed_timestamp = value

    async def fetch_and_parse(
        self,
        session: aiohttp.ClientSession,
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ) -> tuple[int, list[dict[str, Any]]] | None:
        return await self._impl.fetch_and_parse(
            session=session,
            max_retries=max_retries,
            retry_delay=retry_delay,
        )

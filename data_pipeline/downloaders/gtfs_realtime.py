"""Realtime Vehicle Positions Downloader for Warsaw Public Transit.

Supports two providers:
1. GTFS-Realtime Protobuf feed (mkuran.pl / vehicles.pb) — global CDN, no geoblocking, no API key required.
2. Official Warsaw Open Data REST API (dane.um.warszawa.pl) — requires API_KEY_WARSAW (may be geoblocked outside PL).
"""

from __future__ import annotations

import asyncio
import json
import os
import ssl
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

# Ensure data_pipeline root is on sys.path
_DATA_PIPELINE_ROOT = Path(__file__).resolve().parent.parent
if str(_DATA_PIPELINE_ROOT) not in sys.path:
    sys.path.insert(0, str(_DATA_PIPELINE_ROOT))

import aiohttp
try:
    import certifi
    SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except Exception:
    SSL_CONTEXT = ssl.create_default_context()

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
DEFAULT_REALTIME_URL = DEFAULT_WARSAW_API_URL


def extract_route_and_brigade(
    entity_id: str,
    trip_route_id: str | None,
    trip_id: str | None,
) -> tuple[str | None, str | None]:
    """Extracts line/route_id and brigade from entity_id, trip.route_id, or trip_id.

    Warsaw format examples:
        entity_id: 'V/178/M8' -> route: '178', brigade: 'M8'
        trip_id: '2026-11-21:178:SbS:M8:1744' -> route: '178', brigade: 'M8'
    """
    route_id = trip_route_id or None
    brigade = None

    if entity_id and entity_id.startswith("V/"):
        parts = entity_id.split("/")
        if len(parts) >= 2 and parts[1]:
            route_id = route_id or parts[1]
        if len(parts) >= 3 and parts[2]:
            brigade = parts[2]

    if trip_id and ":" in trip_id:
        trip_parts = trip_id.split(":")
        if len(trip_parts) >= 2 and trip_parts[1]:
            route_id = route_id or trip_parts[1]
        if len(trip_parts) >= 4 and trip_parts[3]:
            brigade = brigade or trip_parts[3]

    return route_id, brigade


def extract_route_id(entity_id: str, trip_route_id: str | None, trip_id: str | None) -> str | None:
    """Extracts line/route_id for backward compatibility."""
    route, _ = extract_route_and_brigade(entity_id, trip_route_id, trip_id)
    return route


class GtfsRtProtobufDownloader:
    """Asynchronous downloader and parser for GTFS-RT vehicles.pb (mkuran.pl)."""

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
            "User-Agent": "Opti-Transit Data Pipeline/1.0 (Mozilla/5.0)",
            "Accept": "application/x-protobuf, application/octet-stream, */*",
        }

        for attempt in range(1, max_retries + 1):
            try:
                timeout = aiohttp.ClientTimeout(total=15.0)
                async with session.get(self.url, headers=headers, timeout=timeout, ssl=SSL_CONTEXT) as response:
                    if response.status == 200:
                        raw_data = await response.read()
                        break
                    else:
                        console.print(
                            f"[yellow]Ostrzeżenie: HTTP {response.status} z {self.url} (próba {attempt}/{max_retries})[/yellow]"
                        )
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                if attempt == max_retries:
                    console.print(f"[bold red]Błąd sieci przy pobieraniu GTFS-RT ({self.url}): {type(exc).__name__} ({exc})[/bold red]")
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

            vlabel = v.vehicle.label if v.HasField("vehicle") and v.vehicle.label else None
            trip_id = v.trip.trip_id if v.HasField("trip") and v.trip.trip_id else None
            trip_route = v.trip.route_id if v.HasField("trip") and v.trip.route_id else None
            route_id, brigade = extract_route_and_brigade(entity.id, trip_route, trip_id)

            # Match standard Opti-Transit vehicle_id format: V/{route}/{label}
            vehicle_id = (
                f"V/{route_id}/{vlabel}"
                if (route_id and vlabel)
                else (entity.id or (v.vehicle.id if v.HasField("vehicle") else None))
            )
            if not vehicle_id:
                continue

            vehicle_time = (
                datetime.fromtimestamp(v.timestamp, tz=timezone.utc)
                if v.timestamp
                else fetched_at
            )

            parsed_positions.append({
                "time": vehicle_time,
                "fetched_at": fetched_at,
                "vehicle_id": vehicle_id,
                "route_id": route_id,
                "brigade": brigade,
                "latitude": float(pos.latitude),
                "longitude": float(pos.longitude),
                "vehicle_label": vlabel,
            })

        return feed_timestamp, parsed_positions


class _WarsawApiDownloader:
    """Asynchronous downloader for official Warsaw Open Data API (dane.um.warszawa.pl)."""

    def __init__(
        self,
        api_url: str = DEFAULT_WARSAW_API_URL,
        api_token: str = DEFAULT_API_KEY_WARSAW,
    ) -> None:
        self.api_url = api_url
        self.api_token = api_token.strip("\"'")
        self.last_feed_timestamp: int | None = None
        self._seen_positions: dict[str, str] = {}
        self.last_error_was_network = False

    async def fetch_and_parse(
        self,
        session: aiohttp.ClientSession,
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ) -> tuple[int, list[dict[str, Any]]] | None:
        self.last_error_was_network = False
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
                    async with session.post(self.api_url, headers=headers, json=payload, timeout=timeout, ssl=SSL_CONTEXT) as resp:
                        if resp.status == 200:
                            text = await resp.text()
                            if not text or not text.strip():
                                return []
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
                        self.last_error_was_network = True
                        console.print(f"[bold red]Błąd sieci przy pobieraniu typu {vtype}: {type(exc).__name__} ({exc})[/bold red]")
                        return []
                    await asyncio.sleep(retry_delay * attempt)
            return []

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

            if not lat or not lon or not num or not line:
                continue

            if vtime_str and self._seen_positions.get(num) == vtime_str:
                continue

            if vtime_str:
                self._seen_positions[num] = vtime_str

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

        if len(self._seen_positions) > 15000:
            self._seen_positions.clear()

        if not parsed_positions:
            return None

        self.last_feed_timestamp = feed_timestamp
        return feed_timestamp, parsed_positions


class RealtimeVehicleDownloader:
    """Unified vehicle positions downloader.

    Supports both:
    1. GTFS-Realtime Protobuf feed (mkuran.pl / vehicles.pb)
    2. Warsaw Open Data REST API (dane.um.warszawa.pl)
    Automatically falls back to mkuran.pl if Warsaw API suffers from network timeouts/geoblocking.
    """

    def __init__(
        self,
        api_url: str | None = None,
        api_token: str | None = None,
        url: str | None = None,
    ) -> None:
        raw_url = url or api_url or DEFAULT_REALTIME_URL
        self.api_url = raw_url
        self.url = raw_url
        self.api_token = (
            api_token.strip().strip("\"'").strip()
            if api_token is not None
            else DEFAULT_API_KEY_WARSAW
        )

        # Decide provider
        if raw_url.endswith(".pb") or "mkuran.pl" in raw_url:
            self.provider = "gtfs_rt"
            self._impl: _WarsawApiDownloader | GtfsRtProtobufDownloader = GtfsRtProtobufDownloader(url=raw_url)
        else:
            self.provider = "um_warszawa"
            self._impl = _WarsawApiDownloader(api_url=raw_url, api_token=self.api_token)

        self._fallback_impl: GtfsRtProtobufDownloader | None = None

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
        result = await self._impl.fetch_and_parse(
            session=session,
            max_retries=max_retries,
            retry_delay=retry_delay,
        )
        if result is not None:
            return result

        # Check if Warsaw API failed due to network error/timeout (e.g. geoblocking on VPS)
        if (
            self.provider == "um_warszawa"
            and getattr(self._impl, "last_error_was_network", False)
        ):
            console.print(
                "[bold yellow]⚠️ Wykryto błąd sieci/timeout z dane.um.warszawa.pl (geoblokada VPS). "
                f"Przełączam na feed GTFS-RT mkuran.pl ({DEFAULT_GTFS_RT_URL})...[/bold yellow]"
            )
            if self._fallback_impl is None:
                self._fallback_impl = GtfsRtProtobufDownloader(url=DEFAULT_GTFS_RT_URL)

            fallback_res = await self._fallback_impl.fetch_and_parse(
                session=session,
                max_retries=max_retries,
                retry_delay=retry_delay,
            )
            if fallback_res is not None:
                self.last_feed_timestamp = fallback_res[0]
                return fallback_res

        return None


# Backward compatibility alias
WarsawApiDownloader = RealtimeVehicleDownloader

"""Realtime Vehicle Positions Downloader for Warsaw Public Transit.

Fetches live GPS positions of all active buses (type 1) and trams (type 2)
directly from the official Warsaw Open Data API (dane.um.warszawa.pl).
"""

from __future__ import annotations

import asyncio
import json
import os
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
from rich.console import Console

console = Console()

WARSAW_TZ = ZoneInfo("Europe/Warsaw")

DEFAULT_WARSAW_API_URL = os.getenv(
    "WARSAW_VEHICLES_API_URL",
    "https://dane.um.warszawa.pl/api/action/get_ztm_lokalizacja_pojazdow",
)
DEFAULT_API_KEY_WARSAW = os.getenv("API_KEY_WARSAW", "").strip("\"'")
DEFAULT_REALTIME_URL = DEFAULT_WARSAW_API_URL


class RealtimeVehicleDownloader:
    """Asynchronous downloader for official Warsaw Open Data API (dane.um.warszawa.pl).

    Fetches both buses (type 1) and trams (type 2) concurrently in ~250-500ms,
    deduplicates per vehicle timestamp, and normalizes into standard position dictionaries.
    """

    def __init__(
        self,
        api_url: str | None = None,
        api_token: str | None = None,
        url: str | None = None,
    ) -> None:
        self.api_url = api_url or url or DEFAULT_WARSAW_API_URL
        self.api_token = (api_token or DEFAULT_API_KEY_WARSAW).strip().strip("\"'").strip()
        self.last_feed_timestamp: int | None = None
        # Track last seen timestamp string for each vehicle: {vehicle_number: "YYYY-MM-DD HH:MM:SS"}
        self._seen_positions: dict[str, str] = {}

    @property
    def url(self) -> str:
        return self.api_url

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


# Alias for backward compatibility
WarsawApiDownloader = RealtimeVehicleDownloader

"""Unit tests for GTFS Realtime downloader, parser, and route extraction (downloaders/gtfs_realtime.py).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from google.transit import gtfs_realtime_pb2

from downloaders.gtfs_realtime import RealtimeVehicleDownloader, extract_route_id


# -----------------------------------------------------------------------------
# Tests for extract_route_id
# -----------------------------------------------------------------------------

def test_extract_route_id_direct_trip_route() -> None:
    """Trip route_id takes highest precedence."""
    assert extract_route_id(entity_id="V/251/454", trip_route_id="190", trip_id=None) == "190"
    assert extract_route_id(entity_id=None, trip_route_id="M1", trip_id=None) == "M1"


def test_extract_route_id_from_entity_id() -> None:
    """Extracts line from entity_id with V/ prefix."""
    # Bus line
    assert extract_route_id(entity_id="V/251/454", trip_route_id=None, trip_id=None) == "251"
    assert extract_route_id(entity_id="V/509/101", trip_route_id="", trip_id=None) == "509"
    # Metro line
    assert extract_route_id(entity_id="V/M1/01", trip_route_id=None, trip_id=None) == "M1"
    assert extract_route_id(entity_id="V/M2/12", trip_route_id=None, trip_id=None) == "M2"
    # Tram line
    assert extract_route_id(entity_id="V/24/3", trip_route_id=None, trip_id=None) == "24"
    # Night bus
    assert extract_route_id(entity_id="V/N43/2", trip_route_id=None, trip_id=None) == "N43"


def test_extract_route_id_from_trip_id() -> None:
    """Extracts line from Warsaw-formatted trip_id (colon delimited)."""
    assert (
        extract_route_id(
            entity_id="",
            trip_route_id=None,
            trip_id="2026-10-27:251:PcS:454:1335",
        )
        == "251"
    )
    assert (
        extract_route_id(
            entity_id="UNKNOWN",
            trip_route_id=None,
            trip_id="2026-10-27:M1:Kabaty:01:1400",
        )
        == "M1"
    )


def test_extract_route_id_fallbacks() -> None:
    """Returns None when no valid route can be extracted."""
    assert extract_route_id(entity_id="", trip_route_id=None, trip_id=None) is None
    assert extract_route_id(entity_id="INVALID", trip_route_id=None, trip_id="INVALID") is None
    assert extract_route_id(entity_id="V/", trip_route_id=None, trip_id="NO_COLONS") is None


# -----------------------------------------------------------------------------
# Tests for RealtimeVehicleDownloader
# -----------------------------------------------------------------------------

def test_downloader_initialization() -> None:
    """Verifies downloader default settings and state."""
    downloader = RealtimeVehicleDownloader()
    assert downloader.last_feed_timestamp is None
    assert "vehicles.pb" in downloader.url

    custom_url = "https://example.com/test.pb"
    custom_downloader = RealtimeVehicleDownloader(url=custom_url)
    assert custom_downloader.url == custom_url


@pytest.mark.asyncio
async def test_fetch_and_parse_success(sample_realtime_pb_bytes: bytes) -> None:
    """Verifies parsing of valid Protobuf feed into vehicle dictionaries."""
    downloader = RealtimeVehicleDownloader()

    # Mock aiohttp response
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.read.return_value = sample_realtime_pb_bytes

    mock_session = MagicMock()
    mock_session.get.return_value.__aenter__.return_value = mock_resp

    result = await downloader.fetch_and_parse(mock_session)
    assert result is not None

    feed_ts, positions = result
    assert feed_ts == 1793100000
    assert downloader.last_feed_timestamp == 1793100000

    # Only 2 valid vehicles (entity 3 had no coordinates and was skipped)
    assert len(positions) == 2

    # Check vehicle 1 (Bus 190)
    bus = positions[0]
    assert bus["vehicle_id"] == "V/190/1001"
    assert bus["route_id"] == "190"
    assert bus["trip_id"] == "2026-10-27:190:PcS:1001:1200"
    assert bus["latitude"] == pytest.approx(52.2297, abs=1e-4)
    assert bus["longitude"] == pytest.approx(21.0122, abs=1e-4)
    assert bus["bearing"] == 90.0
    assert bus["speed"] == 12.5
    assert bus["vehicle_label"] == "Bus 190/1"
    assert isinstance(bus["time"], datetime)
    assert isinstance(bus["fetched_at"], datetime)

    # Check vehicle 2 (Metro M1)
    metro = positions[1]
    assert metro["vehicle_id"] == "V/M1/2001"
    assert metro["route_id"] == "M1"
    assert metro["speed"] == 18.0


@pytest.mark.asyncio
async def test_fetch_and_parse_deduplication(sample_realtime_pb_bytes: bytes) -> None:
    """Verifies that consecutive feeds with the identical feed timestamp return None."""
    downloader = RealtimeVehicleDownloader()

    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.read.return_value = sample_realtime_pb_bytes

    mock_session = MagicMock()
    mock_session.get.return_value.__aenter__.return_value = mock_resp

    # First fetch succeeds
    res1 = await downloader.fetch_and_parse(mock_session)
    assert res1 is not None

    # Second fetch with identical timestamp is ignored as duplicate
    res2 = await downloader.fetch_and_parse(mock_session)
    assert res2 is None


@pytest.mark.asyncio
async def test_fetch_and_parse_http_error() -> None:
    """Verifies downloader retries and handles HTTP errors gracefully."""
    downloader = RealtimeVehicleDownloader()

    mock_resp = AsyncMock()
    mock_resp.status = 503

    mock_session = MagicMock()
    mock_session.get.return_value.__aenter__.return_value = mock_resp

    res = await downloader.fetch_and_parse(mock_session, max_retries=2, retry_delay=0.01)
    assert res is None


@pytest.mark.asyncio
async def test_fetch_and_parse_invalid_protobuf() -> None:
    """Verifies downloader handles corrupted protobuf payload gracefully."""
    downloader = RealtimeVehicleDownloader()

    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.read.return_value = b"corrupted non-protobuf bytes"

    mock_session = MagicMock()
    mock_session.get.return_value.__aenter__.return_value = mock_resp

    res = await downloader.fetch_and_parse(mock_session)
    assert res is None

"""Unit tests for GTFS Realtime and Warsaw Open Data API downloaders (downloaders/gtfs_realtime.py).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from google.transit import gtfs_realtime_pb2

from downloaders.gtfs_realtime import (
    GtfsRtProtobufDownloader,
    RealtimeVehicleDownloader,
    WarsawApiDownloader,
    extract_route_id,
)


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
# Tests for Protobuf Downloader
# -----------------------------------------------------------------------------

def test_downloader_initialization() -> None:
    """Verifies downloader default settings and provider detection."""
    downloader = RealtimeVehicleDownloader()
    assert downloader.last_feed_timestamp is None
    assert downloader.provider in ("um_warszawa", "gtfs_rt")

    # Explicit Protobuf URL
    custom_pb = RealtimeVehicleDownloader(url="https://example.com/test.pb", api_token="")
    assert custom_pb.provider == "gtfs_rt"
    assert "test.pb" in custom_pb.url


@pytest.mark.asyncio
async def test_fetch_and_parse_protobuf_success(sample_realtime_pb_bytes: bytes) -> None:
    """Verifies parsing of valid Protobuf feed into vehicle dictionaries."""
    downloader = GtfsRtProtobufDownloader()

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

    bus = positions[0]
    assert bus["vehicle_id"] == "V/190/1001"
    assert bus["route_id"] == "190"
    assert bus["brigade"] is None
    assert bus["latitude"] == pytest.approx(52.2297, abs=1e-4)
    assert bus["longitude"] == pytest.approx(21.0122, abs=1e-4)
    assert bus["vehicle_label"] == "Bus 190/1"
    assert isinstance(bus["time"], datetime)
    assert isinstance(bus["fetched_at"], datetime)


@pytest.mark.asyncio
async def test_fetch_and_parse_protobuf_deduplication(sample_realtime_pb_bytes: bytes) -> None:
    """Verifies that consecutive feeds with identical timestamp return None."""
    downloader = GtfsRtProtobufDownloader()

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
async def test_fetch_and_parse_protobuf_http_error() -> None:
    """Verifies downloader retries and handles HTTP errors gracefully."""
    downloader = GtfsRtProtobufDownloader()

    mock_resp = AsyncMock()
    mock_resp.status = 503

    mock_session = MagicMock()
    mock_session.get.return_value.__aenter__.return_value = mock_resp

    res = await downloader.fetch_and_parse(mock_session, max_retries=2, retry_delay=0.01)
    assert res is None


import json


# -----------------------------------------------------------------------------
# Tests for Official Warsaw API Downloader (dane.um.warszawa.pl)
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_warsaw_api_downloader_success() -> None:
    """Verifies WarsawApiDownloader parsing and concurrent bus + tram merging."""
    downloader = WarsawApiDownloader(
        api_url="https://dane.um.warszawa.pl/api/action/get_ztm_lokalizacja_pojazdow",
        api_token="test_mock_jwt_token",
    )

    mock_buses = [
        {"Brigade": "1", "Lines": "190", "Lat": 52.2297, "Lon": 21.0122, "Time": "2026-10-28 11:30:00", "VehicleNumber": "1001"},
        {"Brigade": "2", "Lines": "509", "Lat": 52.2500, "Lon": 21.0200, "Time": "2026-10-28 11:30:05", "VehicleNumber": "1002"},
    ]
    mock_trams = [
        {"Brigade": "5", "Lines": "9", "Lat": 52.2350, "Lon": 21.0150, "Time": "2026-10-28 11:30:10", "VehicleNumber": "2001"},
    ]

    mock_session = MagicMock()

    # Define mock response for session.post
    class MockPostContext:
        def __init__(self, data: list):
            self.data = data

        async def __aenter__(self):
            resp = MagicMock()
            resp.status = 200
            resp.text = AsyncMock(return_value=json.dumps(self.data))
            return resp

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    def side_effect_post(url, **kwargs):
        payload = kwargs.get("json", {})
        if payload.get("type") == 1:
            return MockPostContext(mock_buses)
        elif payload.get("type") == 2:
            return MockPostContext(mock_trams)
        return MockPostContext([])

    mock_session.post.side_effect = side_effect_post

    result = await downloader.fetch_and_parse(mock_session)
    assert result is not None

    feed_ts, positions = result
    assert isinstance(feed_ts, int)
    assert len(positions) == 3

    # Check bus 190
    bus = positions[0]
    assert bus["vehicle_id"] == "V/190/1001"
    assert bus["route_id"] == "190"
    assert bus["brigade"] == "1"
    assert bus["vehicle_label"] == "1001"
    assert bus["latitude"] == pytest.approx(52.2297, abs=1e-4)
    assert bus["longitude"] == pytest.approx(21.0122, abs=1e-4)
    assert bus["time"].tzinfo is not None

    # Check tram 9
    tram = positions[2]
    assert tram["vehicle_id"] == "V/9/2001"
    assert tram["route_id"] == "9"
    assert tram["brigade"] == "5"
    assert tram["vehicle_label"] == "2001"


@pytest.mark.asyncio
async def test_warsaw_api_downloader_deduplication() -> None:
    """Verifies that vehicles with unchanged Time timestamps are skipped on subsequent cycles."""
    downloader = WarsawApiDownloader(
        api_token="test_token",
    )

    mock_data = [
        {"Brigade": "1", "Lines": "190", "Lat": 52.2297, "Lon": 21.0122, "Time": "2026-10-28 11:30:00", "VehicleNumber": "1001"},
    ]

    mock_session = MagicMock()

    class MockPostContext:
        async def __aenter__(self):
            resp = MagicMock()
            resp.status = 200
            resp.text = AsyncMock(return_value=json.dumps(mock_data))
            return resp

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    mock_session.post.return_value = MockPostContext()

    # First fetch: vehicle is new -> returns 1 position
    res1 = await downloader.fetch_and_parse(mock_session)
    assert res1 is not None
    assert len(res1[1]) == 1

    # Second fetch with identical Time: vehicle unchanged -> returns None
    res2 = await downloader.fetch_and_parse(mock_session)
    assert res2 is None

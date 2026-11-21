"""Unit tests for Warsaw Open Data API downloader (downloaders/gtfs_realtime.py).
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from downloaders.gtfs_realtime import (
    DEFAULT_WARSAW_API_URL,
    RealtimeVehicleDownloader,
    WarsawApiDownloader,
)


def test_downloader_initialization() -> None:
    """Verifies downloader default settings and initialization."""
    downloader = RealtimeVehicleDownloader()
    assert downloader.last_feed_timestamp is None
    assert downloader.api_url == DEFAULT_WARSAW_API_URL
    assert downloader.url == DEFAULT_WARSAW_API_URL

    # Custom URL and token with quotes
    custom = RealtimeVehicleDownloader(url="https://custom.api.pl", api_token=' "test_token" ')
    assert custom.api_url == "https://custom.api.pl"
    assert custom.api_token == "test_token"

    # Alias check
    assert WarsawApiDownloader is RealtimeVehicleDownloader


@pytest.mark.asyncio
async def test_warsaw_api_downloader_success() -> None:
    """Verifies parsing and concurrent bus (type 1) + tram (type 2) merging."""
    downloader = RealtimeVehicleDownloader(
        api_url="https://dane.um.warszawa.pl/api/action/get_ztm_lokalizacja_pojazdow",
        api_token="test_mock_jwt_token",
    )

    mock_buses = [
        {
            "Brigade": "1",
            "Lines": "190",
            "Lat": 52.2297,
            "Lon": 21.0122,
            "Time": "2026-10-28 11:30:00",
            "VehicleNumber": "1001",
        },
        {
            "Brigade": "2",
            "Lines": "509",
            "Lat": 52.2500,
            "Lon": 21.0200,
            "Time": "2026-10-28 11:30:05",
            "VehicleNumber": "1002",
        },
    ]
    mock_trams = [
        {
            "Brigade": "5",
            "Lines": "9",
            "Lat": 52.2350,
            "Lon": 21.0150,
            "Time": "2026-10-28 11:30:10",
            "VehicleNumber": "2001",
        },
    ]

    mock_session = MagicMock()

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
    assert isinstance(bus["time"], datetime)
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
    downloader = RealtimeVehicleDownloader(
        api_token="test_token",
    )

    mock_data = [
        {
            "Brigade": "1",
            "Lines": "190",
            "Lat": 52.2297,
            "Lon": 21.0122,
            "Time": "2026-10-28 11:30:00",
            "VehicleNumber": "1001",
        },
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


@pytest.mark.asyncio
async def test_warsaw_api_downloader_missing_token() -> None:
    """Verifies that missing token returns None safely."""
    downloader = RealtimeVehicleDownloader(api_token="")
    mock_session = MagicMock()
    result = await downloader.fetch_and_parse(mock_session)
    assert result is None


@pytest.mark.asyncio
async def test_warsaw_api_downloader_auth_error() -> None:
    """Verifies handling of HTTP 401 Unauthorized."""
    downloader = RealtimeVehicleDownloader(api_token="invalid_token")

    mock_session = MagicMock()

    class MockAuthErrorContext:
        async def __aenter__(self):
            resp = MagicMock()
            resp.status = 401
            return resp

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    mock_session.post.return_value = MockAuthErrorContext()

    result = await downloader.fetch_and_parse(mock_session)
    assert result is None


@pytest.mark.asyncio
async def test_warsaw_api_downloader_empty_response() -> None:
    """Verifies handling of empty body and malformed responses from city server."""
    downloader = RealtimeVehicleDownloader(api_token="test_token")

    mock_session = MagicMock()

    class MockEmptyContext:
        async def __aenter__(self):
            resp = MagicMock()
            resp.status = 200
            resp.text = AsyncMock(return_value="")
            return resp

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    mock_session.post.return_value = MockEmptyContext()

    result = await downloader.fetch_and_parse(mock_session)
    assert result is None


@pytest.mark.asyncio
async def test_warsaw_api_downloader_skips_invalid_items() -> None:
    """Verifies items missing required coordinates, number, or lines are ignored."""
    downloader = RealtimeVehicleDownloader(api_token="test_token")

    invalid_items = [
        {"Lines": "190", "Lat": None, "Lon": 21.0, "VehicleNumber": "1001"},  # missing Lat
        {"Lines": "190", "Lat": 52.0, "Lon": None, "VehicleNumber": "1002"},  # missing Lon
        {"Lines": "", "Lat": 52.0, "Lon": 21.0, "VehicleNumber": "1003"},     # empty Line
        {"Lines": "190", "Lat": 52.0, "Lon": 21.0, "VehicleNumber": ""},     # empty Number
        {"Lines": "190", "Lat": 52.0, "Lon": 21.0, "VehicleNumber": "1005"}, # VALID
    ]

    mock_session = MagicMock()

    class MockContext:
        def __init__(self, data: list):
            self.data = data

        async def __aenter__(self):
            resp = MagicMock()
            resp.status = 200
            resp.text = AsyncMock(return_value=json.dumps(self.data))
            return resp

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    def side_effect(url, **kwargs):
        payload = kwargs.get("json", {})
        if payload.get("type") == 1:
            return MockContext(invalid_items)
        return MockContext([])

    mock_session.post.side_effect = side_effect

    result = await downloader.fetch_and_parse(mock_session)
    assert result is not None
    feed_ts, positions = result
    assert len(positions) == 1
    assert positions[0]["vehicle_label"] == "1005"


def test_extract_route_and_brigade() -> None:
    """Verifies extraction of route and brigade from various Warsaw format strings."""
    from downloaders.gtfs_realtime import extract_route_and_brigade

    # entity_id with V/route/brigade
    r, b = extract_route_and_brigade("V/178/M8", None, None)
    assert r == "178"
    assert b == "M8"

    # trip_id with YYYY-MM-DD:route:code:brigade:time
    r2, b2 = extract_route_and_brigade("other_id", None, "2026-11-21:507:SbS:3:1740")
    assert r2 == "507"
    assert b2 == "3"

    # Explicit trip_route_id takes precedence for route
    r3, b3 = extract_route_and_brigade("V/178/M8", "178_exp", None)
    assert r3 == "178_exp"
    assert b3 == "M8"


@pytest.mark.asyncio
async def test_protobuf_downloader_success() -> None:
    """Verifies parsing of GTFS-Realtime Protocol Buffers feed."""
    from google.transit import gtfs_realtime_pb2
    from downloaders.gtfs_realtime import GtfsRtProtobufDownloader

    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = 1700000000

    entity = feed.entity.add()
    entity.id = "V/178/M8"
    entity.vehicle.trip.trip_id = "2026-11-21:178:SbS:M8:1744"
    entity.vehicle.vehicle.id = "V/178/M8"
    entity.vehicle.vehicle.label = "4220"
    entity.vehicle.position.latitude = 52.2011
    entity.vehicle.position.longitude = 20.9360
    entity.vehicle.position.bearing = 113.6
    entity.vehicle.timestamp = 1700000000

    raw_pb = feed.SerializeToString()

    downloader = RealtimeVehicleDownloader(url="https://mkuran.pl/gtfs/warsaw/vehicles.pb")
    assert downloader.provider == "gtfs_rt"

    mock_session = MagicMock()

    class MockGetContext:
        async def __aenter__(self):
            resp = MagicMock()
            resp.status = 200
            resp.read = AsyncMock(return_value=raw_pb)
            return resp

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    mock_session.get.return_value = MockGetContext()

    res = await downloader.fetch_and_parse(mock_session)
    assert res is not None
    ts, positions = res
    assert ts == 1700000000
    assert len(positions) == 1
    pos = positions[0]
    assert pos["vehicle_id"] == "V/178/4220"
    assert pos["route_id"] == "178"
    assert pos["brigade"] == "M8"
    assert pos["vehicle_label"] == "4220"
    assert pos["latitude"] == pytest.approx(52.2011, abs=1e-4)
    assert pos["longitude"] == pytest.approx(20.9360, abs=1e-4)


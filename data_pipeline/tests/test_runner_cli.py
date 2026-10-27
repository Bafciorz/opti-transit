"""Unit tests for PipelineRunner, VehicleCollector, and CLI argument parsing (runner.py, collector.py, cli.py).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from cli import build_parser
from collector import VehicleCollector
from runner import PipelineRunner


def test_pipeline_runner_initialization_defaults() -> None:
    """Verifies PipelineRunner default parameters and tasks setup."""
    runner = PipelineRunner()
    assert runner.collector_interval == 15.0
    assert runner.monitor_interval == 300.0
    assert runner.static_sync_hour == 3
    assert runner.archive_hour == 4
    assert runner.enable_collector is True
    assert runner.enable_static_sync is True
    assert runner.enable_archiver is True
    assert runner.stop_event.is_set() is False


def test_pipeline_runner_custom_config() -> None:
    """Verifies PipelineRunner custom settings and request_stop."""
    runner = PipelineRunner(
        collector_interval=5.0,
        enable_collector=False,
        enable_static_sync=False,
        enable_archiver=False,
        sync_on_startup=True,
        archive_on_startup=True,
        max_collector_iterations=3,
    )
    assert runner.collector_interval == 5.0
    assert runner.enable_collector is False
    assert runner.enable_static_sync is False
    assert runner.enable_archiver is False
    assert runner.sync_on_startup is True
    assert runner.archive_on_startup is True
    assert runner.max_collector_iterations == 3

    # Stop signaling
    runner.request_stop()
    assert runner.stop_event.is_set() is True


def test_vehicle_collector_initialization() -> None:
    """Verifies VehicleCollector defaults and stop_event."""
    collector = VehicleCollector(interval=10.0, max_iterations=5)
    assert collector.interval == 10.0
    assert collector.max_iterations == 5
    assert collector.downloader is not None
    assert not collector.stop_event.is_set()

    collector.request_stop()
    assert collector.stop_event.is_set()


# -----------------------------------------------------------------------------
# CLI Argument Parser Tests
# -----------------------------------------------------------------------------

def test_cli_parser_inspect() -> None:
    """Verifies CLI parsing of inspect command."""
    parser = build_parser()
    args = parser.parse_args(["inspect"])
    assert args.command == "inspect"


def test_cli_parser_stats() -> None:
    """Verifies CLI parsing of stats command."""
    parser = build_parser()
    args = parser.parse_args(["stats"])
    assert args.command == "stats"


def test_cli_parser_run() -> None:
    """Verifies CLI parsing of run command with all custom flags."""
    parser = build_parser()
    args = parser.parse_args([
        "run",
        "--collector-interval", "20",
        "--monitor-interval", "60",
        "--run-once",
        "--skip-archiver",
        "--skip-static-sync",
    ])
    assert args.command == "run"
    assert args.collector_interval == 20.0
    assert args.monitor_interval == 60.0
    assert args.run_once is True
    assert args.skip_archiver is True
    assert args.skip_static_sync is True
    assert args.skip_collector is False


def test_cli_parser_collect() -> None:
    """Verifies CLI parsing of collect command."""
    parser = build_parser()
    args = parser.parse_args(["collect", "--interval", "10", "--max-iterations", "2"])
    assert args.command == "collect"
    assert args.interval == 10.0
    assert args.max_iterations == 2


def test_cli_parser_load_gtfs() -> None:
    """Verifies CLI parsing of load-gtfs command."""
    parser = build_parser()
    args = parser.parse_args(["load-gtfs", "--force", "--skip-download"])
    assert args.command == "load-gtfs"
    assert args.force is True
    assert args.skip_download is True


def test_cli_parser_archive() -> None:
    """Verifies CLI parsing of archive command."""
    parser = build_parser()
    args = parser.parse_args([
        "archive",
        "--older-than-hours", "48",
        "--dry-run",
        "--no-delete",
    ])
    assert args.command == "archive"
    assert args.older_than_hours == 48.0
    assert args.dry_run is True
    assert args.no_delete is True


def test_cli_parser_query() -> None:
    """Verifies CLI parsing of query command."""
    parser = build_parser()
    args = parser.parse_args([
        "query",
        "--route-id", "190",
        "--vehicle-id", "1001",
        "--limit", "50",
    ])
    assert args.command == "query"
    assert args.route_id == "190"
    assert args.vehicle_id == "1001"
    assert args.limit == 50


def test_cli_parser_verify() -> None:
    """Verifies CLI parsing of verify command."""
    parser = build_parser()
    args = parser.parse_args(["verify", "--suite", "unit"])
    assert args.command == "verify"
    assert args.suite == "unit"


def test_cli_parser_init_db() -> None:
    """Verifies CLI parsing of init-db command."""
    parser = build_parser()
    args = parser.parse_args(["init-db", "--check-only"])
    assert args.command == "init-db"
    assert args.check_only is True


def test_cli_parser_invalid_command() -> None:
    """Verifies CLI exits on unknown argument."""
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--unknown-flag"])

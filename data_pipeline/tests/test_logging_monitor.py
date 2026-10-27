"""Unit and integration tests for logging configuration and PipelineMonitor (logging_config.py).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import psycopg
import pytest

from logging_config import (
    PipelineMetrics,
    PipelineMonitor,
    get_monitor,
    setup_logging,
)


def test_setup_logging_file_and_level(tmp_path: Path) -> None:
    """Verifies logger configuration, custom level, and file output creation."""
    log_file = tmp_path / "test_pipeline.log"
    logger = setup_logging(
        level="DEBUG",
        log_file=log_file,
        console_output=False,
    )

    assert logger is not None
    assert logger.name == "opti_transit"

    # Emit test logs
    logger.debug("Debug message from test")
    logger.info("Info message from test")

    # Flush handlers
    for h in logging.getLogger().handlers:
        h.flush()

    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")
    assert "Debug message from test" in content
    assert "Info message from test" in content
    assert "[DEBUG]" in content
    assert "[INFO]" in content


def test_pipeline_metrics_defaults() -> None:
    """Verifies default initialization of PipelineMetrics dataclass."""
    metrics = PipelineMetrics()
    assert metrics.realtime_cycles_total == 0
    assert metrics.realtime_cycles_success == 0
    assert metrics.realtime_cycles_skipped == 0
    assert metrics.realtime_cycles_error == 0
    assert metrics.realtime_rows_collected == 0
    assert metrics.archived_rows_total == 0
    assert metrics.purged_rows_total == 0
    assert isinstance(metrics.start_time, datetime)


def test_pipeline_monitor_realtime_success() -> None:
    """Verifies recording of successful realtime collection cycle."""
    monitor = PipelineMonitor()
    assert monitor.metrics.realtime_cycles_total == 0

    now = datetime.now(timezone.utc)
    monitor.record_realtime_success(rows_count=150, duration_ms=45.2, timestamp=now)

    assert monitor.metrics.realtime_cycles_total == 1
    assert monitor.metrics.realtime_cycles_success == 1
    assert monitor.metrics.realtime_cycles_skipped == 0
    assert monitor.metrics.realtime_rows_collected == 150
    assert monitor.metrics.last_realtime_duration_ms == 45.2
    assert monitor.metrics.last_realtime_timestamp == now

    # Record skipped cycle
    monitor.record_realtime_success(rows_count=0, duration_ms=10.0, skipped=True)
    assert monitor.metrics.realtime_cycles_total == 2
    assert monitor.metrics.realtime_cycles_skipped == 1
    assert monitor.metrics.realtime_rows_collected == 150


def test_pipeline_monitor_realtime_error() -> None:
    """Verifies recording of realtime cycle errors."""
    monitor = PipelineMonitor()

    monitor.record_realtime_error(ValueError("Network connection timeout"))
    assert monitor.metrics.realtime_cycles_total == 1
    assert monitor.metrics.realtime_cycles_error == 1
    assert "Network connection timeout" in monitor.metrics.last_error_message
    assert monitor.metrics.last_error_time is not None


def test_pipeline_monitor_static_sync_and_archiver() -> None:
    """Verifies recording static sync and Parquet archiver runs."""
    monitor = PipelineMonitor()

    # Static sync
    monitor.record_static_sync(success=True, message="Loaded version 2026-10-27")
    assert monitor.metrics.static_syncs_total == 1
    assert monitor.metrics.static_syncs_success == 1

    # Archiver
    monitor.record_archiver_run(archived_rows=300, purged_rows=300)
    assert monitor.metrics.archiver_runs_total == 1
    assert monitor.metrics.archived_rows_total == 300
    assert monitor.metrics.purged_rows_total == 300


def test_pipeline_monitor_db_stats_and_summary(db_conn: psycopg.Connection) -> None:
    """Verifies get_db_stats and log_periodic_summary execute cleanly."""
    monitor = PipelineMonitor()
    stats = monitor.get_db_stats()

    assert stats["connected"] is True
    assert "vp_rows" in stats
    assert "vp_size_mb" in stats
    assert "db_size_mb" in stats
    assert stats["vp_rows"] >= 0

    # Ensure log_periodic_summary runs without throwing an exception
    monitor.record_realtime_success(rows_count=25, duration_ms=30.0)
    monitor.log_periodic_summary()


def test_get_monitor_singleton() -> None:
    """Verifies get_monitor returns a singleton instance."""
    m1 = get_monitor()
    m2 = get_monitor()
    assert m1 is m2

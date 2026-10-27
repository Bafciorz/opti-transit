"""Logging and metrics monitoring configuration for Opti-Transit Data Pipeline.

Provides unified logging to console and rotating file, formatted as:
    [TIMESTAMP] [MODULE] [LEVEL] Message
Also includes PipelineMonitor for tracking periodic metrics and health.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.table import Table

console = Console()

DEFAULT_LOG_DIR = Path(__file__).resolve().parent / "logs"
DEFAULT_LOG_FILE = DEFAULT_LOG_DIR / "pipeline.log"
LOG_FORMAT = "[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(
    level: str | int = "INFO",
    log_file: Path | str | None = DEFAULT_LOG_FILE,
    console_output: bool = True,
    max_bytes: int = 10 * 1024 * 1024,  # 10 MB
    backup_count: int = 5,
) -> logging.Logger:
    """Configures the root and pipeline logger with console and rotating file handlers.

    Args:
        level: Minimum log level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        log_file: Path to destination log file. If None, file logging is disabled.
        console_output: If True, attaches a formatted console handler.
        max_bytes: Maximum size of a single log file before rotation.
        backup_count: Number of rotated backup log files to retain.

    Returns:
        The configured 'opti_transit' logger.
    """
    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Clear any existing handlers to avoid duplicates
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)

    # 1. Console Handler
    if console_output:
        # Use a Standard StreamHandler with clean format
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_formatter = logging.Formatter(fmt=LOG_FORMAT, datefmt=DATE_FORMAT)
        console_handler.setFormatter(console_formatter)
        root_logger.addHandler(console_handler)

    # 2. Rotating File Handler
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            filename=log_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_formatter = logging.Formatter(fmt=LOG_FORMAT, datefmt=DATE_FORMAT)
        file_handler.setFormatter(file_formatter)
        root_logger.addHandler(file_handler)

    # Suppress overly chatty external loggers
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("duckdb").setLevel(logging.WARNING)

    logger = logging.getLogger("opti_transit")
    logger.info("Logging initialized (level=%s, log_file=%s)", logging.getLevelName(level), log_file)
    return logger


@dataclass
class PipelineMetrics:
    """Container for pipeline runtime health and performance metrics."""

    start_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    realtime_cycles_total: int = 0
    realtime_cycles_success: int = 0
    realtime_cycles_skipped: int = 0
    realtime_cycles_error: int = 0
    realtime_rows_collected: int = 0
    last_realtime_timestamp: datetime | None = None
    last_realtime_duration_ms: float = 0.0

    static_syncs_total: int = 0
    static_syncs_success: int = 0
    last_static_sync_time: datetime | None = None

    archiver_runs_total: int = 0
    archived_rows_total: int = 0
    purged_rows_total: int = 0
    last_archiver_run_time: datetime | None = None

    last_error_message: str | None = None
    last_error_time: datetime | None = None


class PipelineMonitor:
    """Tracks running statistics and logs periodic summaries of the data pipeline."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger("opti_transit.monitor")
        self.metrics = PipelineMetrics()
        self._last_summary_rows = 0

    def record_realtime_success(
        self,
        rows_count: int,
        duration_ms: float,
        timestamp: datetime | None = None,
        skipped: bool = False,
    ) -> None:
        """Records a successful realtime collection cycle."""
        self.metrics.realtime_cycles_total += 1
        if skipped:
            self.metrics.realtime_cycles_skipped += 1
        else:
            self.metrics.realtime_cycles_success += 1
            self.metrics.realtime_rows_collected += rows_count

        self.metrics.last_realtime_duration_ms = duration_ms
        self.metrics.last_realtime_timestamp = timestamp or datetime.now(timezone.utc)

    def record_realtime_error(self, error: Exception | str) -> None:
        """Records a failed realtime collection cycle."""
        self.metrics.realtime_cycles_total += 1
        self.metrics.realtime_cycles_error += 1
        self.metrics.last_error_message = str(error)
        self.metrics.last_error_time = datetime.now(timezone.utc)
        self.logger.error("Realtime collector cycle failed: %s", error)

    def record_static_sync(self, success: bool, message: str = "") -> None:
        """Records an execution of the static GTFS sync."""
        self.metrics.static_syncs_total += 1
        if success:
            self.metrics.static_syncs_success += 1
        self.metrics.last_static_sync_time = datetime.now(timezone.utc)
        self.logger.info("Static GTFS sync completed (success=%s): %s", success, message)

    def record_archiver_run(self, archived_rows: int, purged_rows: int) -> None:
        """Records an execution of the Parquet archiver."""
        self.metrics.archiver_runs_total += 1
        self.metrics.archived_rows_total += archived_rows
        self.metrics.purged_rows_total += purged_rows
        self.metrics.last_archiver_run_time = datetime.now(timezone.utc)
        self.logger.info(
            "Archiver completed: %d rows archived to Parquet, %d rows purged from PG",
            archived_rows,
            purged_rows,
        )

    def get_db_stats(self) -> dict[str, Any]:
        """Queries PostgreSQL for live table row counts and disk space usage."""
        try:
            from db import get_connection

            with get_connection() as conn:
                with conn.cursor() as cur:
                    # Vehicle positions count & size
                    cur.execute(
                        """
                        SELECT
                            count(*),
                            pg_total_relation_size('vehicle_positions')
                        FROM vehicle_positions;
                        """
                    )
                    vp_count, vp_bytes = cur.fetchone()

                    # Database size
                    cur.execute("SELECT pg_database_size(current_database());")
                    db_bytes = cur.fetchone()[0]

                    return {
                        "vp_rows": vp_count,
                        "vp_size_mb": round(vp_bytes / (1024 * 1024), 2),
                        "db_size_mb": round(db_bytes / (1024 * 1024), 2),
                        "connected": True,
                    }
        except Exception as exc:
            return {
                "vp_rows": 0,
                "vp_size_mb": 0.0,
                "db_size_mb": 0.0,
                "connected": False,
                "error": str(exc),
            }

    def log_periodic_summary(self) -> None:
        """Emits a periodic health and throughput summary to the logger."""
        now = datetime.now(timezone.utc)
        uptime = now - self.metrics.start_time
        uptime_str = str(uptime).split(".")[0]  # HH:MM:SS

        recent_rows = self.metrics.realtime_rows_collected - self._last_summary_rows
        self._last_summary_rows = self.metrics.realtime_rows_collected

        db_stats = self.get_db_stats()

        summary_msg = (
            f"[MONITOR] Uptime: {uptime_str} | "
            f"Collected Total: {self.metrics.realtime_rows_collected:,} rows "
            f"(+{recent_rows:,} in interval) | "
            f"Cycles: {self.metrics.realtime_cycles_total} "
            f"(OK: {self.metrics.realtime_cycles_success}, "
            f"Skip: {self.metrics.realtime_cycles_skipped}, "
            f"Err: {self.metrics.realtime_cycles_error}) | "
            f"PG vehicle_positions: {db_stats['vp_rows']:,} rows ({db_stats['vp_size_mb']} MB) | "
            f"DB Total: {db_stats['db_size_mb']} MB | "
            f"Archived Total: {self.metrics.archived_rows_total:,} rows"
        )
        self.logger.info(summary_msg)


_global_monitor: PipelineMonitor | None = None


def get_monitor() -> PipelineMonitor:
    """Returns the singleton PipelineMonitor instance."""
    global _global_monitor
    if _global_monitor is None:
        _global_monitor = PipelineMonitor()
    return _global_monitor

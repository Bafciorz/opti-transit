"""Unified Command-Line Interface (CLI) for Opti-Transit Data Pipeline.

Provides subcommands for all data pipeline tasks:
    inspect     - Inspects database connectivity, feed metadata, and storage stats
    run         - Starts the full unified pipeline (realtime + static sync + archiver)
    collect     - Runs the GTFS Realtime vehicle positions collector
    load-gtfs   - Downloads and loads static GTFS schedules into PostgreSQL
    archive     - Archives historical vehicle positions to ZSTD Parquet files
    query       - Queries archived Parquet files using DuckDB
    stats       - Displays storage and record statistics for DB and Parquet archive
    verify      - Runs automated verification tests
    init-db     - Initializes or verifies the PostgreSQL schema

Usage:
    uv run python cli.py --help
    uv run python cli.py run
    uv run python cli.py inspect
    uv run python cli.py stats
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# Ensure UTF-8 output on Windows terminal
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Ensure data_pipeline root is on sys.path
_DATA_PIPELINE_ROOT = Path(__file__).resolve().parent
if str(_DATA_PIPELINE_ROOT) not in sys.path:
    sys.path.insert(0, str(_DATA_PIPELINE_ROOT))

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()


def cmd_inspect(args: argparse.Namespace) -> None:
    """Inspects environment, database status, and feed metadata."""
    from db import get_config, test_connection

    cfg = get_config()
    ok, version = test_connection()

    db_status = f"[green]CONNECTED[/green] ({version.split(',')[0]})" if ok else f"[red]FAILED[/red] ({version})"

    console.print(
        Panel.fit(
            f"[bold cyan]Opti-Transit Data Pipeline — System Inspection[/bold cyan]\n"
            f"• DB Host:      [bold]{cfg.host}:{cfg.port}[/bold]\n"
            f"• DB Name:      [bold]{cfg.dbname}[/bold] (User: {cfg.user})\n"
            f"• DB Status:    {db_status}\n"
            f"• Pipeline Dir: [dim]{_DATA_PIPELINE_ROOT}[/dim]\n"
            f"• Archive Dir:  [dim]{_DATA_PIPELINE_ROOT / 'data' / 'archive'}[/dim]",
            title="System Configuration",
            border_style="cyan",
        )
    )

    if ok:
        cmd_stats(args)


def cmd_stats(args: argparse.Namespace) -> None:
    """Displays tables, row counts, sizes, and Parquet archive metrics."""
    from archiver import get_archive_stats
    from db import get_connection

    # Database table statistics
    table_db = Table(title="PostgreSQL Database Tables", border_style="cyan")
    table_db.add_column("Table", style="bold")
    table_db.add_column("Row Count", justify="right", style="green")
    table_db.add_column("Table Size", justify="right", style="yellow")
    table_db.add_column("Total Size (incl. Indexes)", justify="right", style="magenta")

    tables = [
        "gtfs_agency",
        "gtfs_routes",
        "gtfs_stops",
        "gtfs_trips",
        "gtfs_stop_times",
        "gtfs_calendar_dates",
        "gtfs_shapes",
        "feed_metadata",
        "vehicle_positions",
    ]

    total_rows = 0
    total_bytes = 0

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                for t in tables:
                    try:
                        cur.execute(
                            f"""
                            SELECT
                                count(*),
                                pg_relation_size('{t}'),
                                pg_total_relation_size('{t}')
                            FROM {t};
                            """
                        )
                        cnt, rel_size, tot_size = cur.fetchone()
                        total_rows += cnt
                        total_bytes += tot_size
                        table_db.add_row(
                            t,
                            f"{cnt:,}",
                            f"{rel_size / (1024*1024):.2f} MB",
                            f"{tot_size / (1024*1024):.2f} MB",
                        )
                    except Exception as e:
                        table_db.add_row(t, "ERROR", "-", "-")

                cur.execute("SELECT pg_database_size(current_database());")
                db_total = cur.fetchone()[0]

                table_db.add_section()
                table_db.add_row(
                    "[bold]TOTAL (All Tables)[/bold]",
                    f"[bold]{total_rows:,}[/bold]",
                    "-",
                    f"[bold]{total_bytes / (1024*1024):.2f} MB[/bold]",
                )
                table_db.add_row(
                    "[bold]Database Physical Disk Size[/bold]",
                    "-",
                    "-",
                    f"[bold cyan]{db_total / (1024*1024):.2f} MB[/bold cyan]",
                )

        console.print(table_db)

    except Exception as exc:
        console.print(f"[bold red]Could not query PostgreSQL: {exc}[/bold red]")

    # Parquet archive statistics
    stats = get_archive_stats()
    table_pq = Table(title="Historical Parquet Archive (ZSTD)", border_style="green")
    table_pq.add_column("Metric", style="bold")
    table_pq.add_column("Value", style="green")

    table_pq.add_row("Total Parquet Files", str(stats["total_files"]))
    table_pq.add_row("Archived Positions Count", f"{stats['total_rows']:,}")
    table_pq.add_row("Total Compressed Archive Size", f"{stats['total_size_mb']} MB")
    table_pq.add_row(
        "Date Range Covered",
        f"{stats['date_range'][0] or 'N/A'} to {stats['date_range'][1] or 'N/A'}",
    )
    console.print(table_pq)


def cmd_run(args: argparse.Namespace) -> None:
    """Runs the unified runner."""
    from runner import PipelineRunner
    from db import run_async

    max_iter = 1 if args.run_once else args.max_iterations

    runner = PipelineRunner(
        collector_interval=args.collector_interval,
        monitor_interval=args.monitor_interval,
        static_sync_hour=args.static_sync_hour,
        archive_hour=args.archive_hour,
        retention_hours=args.retention_hours,
        enable_collector=not args.skip_collector,
        enable_static_sync=not args.skip_static_sync,
        enable_archiver=not args.skip_archiver,
        sync_on_startup=args.sync_on_startup,
        archive_on_startup=args.archive_on_startup,
        max_collector_iterations=max_iter,
    )

    try:
        run_async(runner.run())
    except KeyboardInterrupt:
        runner.request_stop()


def cmd_collect(args: argparse.Namespace) -> None:
    """Runs only the realtime vehicle positions collector."""
    from collector import VehicleCollector
    from db import run_async

    collector = VehicleCollector(
        url=args.url,
        interval=args.interval,
        max_iterations=args.max_iterations,
    )

    try:
        run_async(collector.run())
    except KeyboardInterrupt:
        collector.request_stop()


def cmd_load_gtfs(args: argparse.Namespace) -> None:
    """Downloads and loads GTFS Static into PostgreSQL."""
    from downloaders.gtfs_static import (
        DEFAULT_DEST_PATH as DEFAULT_STATIC_ZIP,
        DEFAULT_URL as DEFAULT_STATIC_URL,
        download_gtfs_static,
    )
    from loaders.gtfs_static import load_gtfs_static

    zip_path = Path(args.zip_path) if args.zip_path else DEFAULT_STATIC_ZIP

    if not args.skip_download:
        dest_path, feed_info, is_new = download_gtfs_static(
            url=args.url or DEFAULT_STATIC_URL,
            dest_path=zip_path,
            force=args.force,
        )
        if not is_new and not args.force:
            console.print("[green]Static GTFS is already up to date.[/green]")
            if not args.load_anyway:
                return

    load_gtfs_static(zip_path=zip_path, force=args.force)


def cmd_archive(args: argparse.Namespace) -> None:
    """Executes archival of historical vehicle positions to Parquet."""
    from archiver import DEFAULT_ARCHIVE_DIR, archive_old_positions

    cutoff = datetime.fromisoformat(args.cutoff_time) if args.cutoff_time else None

    archive_old_positions(
        older_than_hours=args.older_than_hours,
        cutoff_time=cutoff,
        archive_dir=args.archive_dir or DEFAULT_ARCHIVE_DIR,
        delete_after=not args.no_delete,
        dry_run=args.dry_run,
    )


def cmd_query(args: argparse.Namespace) -> None:
    """Queries archived Parquet files using DuckDB."""
    from archiver import DEFAULT_ARCHIVE_DIR, query_archived_positions

    df = query_archived_positions(
        date_from=args.date_from,
        date_to=args.date_to,
        route_id=args.route_id,
        vehicle_id=args.vehicle_id,
        archive_dir=args.archive_dir or DEFAULT_ARCHIVE_DIR,
        limit=args.limit,
    )

    if df.empty:
        console.print("[dim]No records matched the query criteria.[/dim]")
    else:
        console.print(f"[bold green]Retrieved {len(df)} row(s):[/bold green]")
        console.print(df.to_string())


def cmd_verify(args: argparse.Namespace) -> None:
    """Runs automated diagnostic and verification scripts."""
    scripts_to_run = []

    if args.suite == "all" or args.suite == "gtfs":
        scripts_to_run.append("tests/verify_gtfs.py")
    if args.suite == "all" or args.suite == "realtime":
        scripts_to_run.append("tests/verify_realtime.py")
    if args.suite == "all" or args.suite == "archive":
        scripts_to_run.append("tests/verify_archive.py")

    for script in scripts_to_run:
        script_path = _DATA_PIPELINE_ROOT / script
        console.print(f"\n[bold cyan]=== Running {script} ===[/bold cyan]")
        res = subprocess.run([sys.executable, str(script_path)])
        if res.returncode != 0:
            console.print(f"[bold red]FAILED: {script} exited with code {res.returncode}[/bold red]")
            sys.exit(res.returncode)

    if args.suite in ("unit", "pytest"):
        console.print(f"\n[bold cyan]=== Running Pytest Suite ===[/bold cyan]")
        res = subprocess.run([sys.executable, "-m", "pytest", "tests/"])
        if res.returncode != 0:
            console.print(f"[bold red]FAILED: pytest exited with code {res.returncode}[/bold red]")
            sys.exit(res.returncode)

    console.print(f"\n[bold green]All selected verification scripts passed successfully! [OK][/bold green]")


def cmd_init_db(args: argparse.Namespace) -> None:
    """Initializes or checks the PostgreSQL schema."""
    script_path = _DATA_PIPELINE_ROOT / "scripts" / "init_db.py"
    cmd = [sys.executable, str(script_path)]
    if args.check_only:
        cmd.append("--check-only")
    if args.drop_all:
        cmd.append("--drop-all")

    res = subprocess.run(cmd)
    if res.returncode != 0:
        sys.exit(res.returncode)


def build_parser() -> argparse.ArgumentParser:
    """Builds and returns the argument parser for data-pipeline CLI."""
    parser = argparse.ArgumentParser(
        prog="data-pipeline",
        description="Opti-Transit Data Pipeline Management CLI",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Command: inspect
    subparsers.add_parser("inspect", help="Inspect database connectivity, feed versions, and storage stats")

    # Command: stats
    subparsers.add_parser("stats", help="Display row counts and sizes for PostgreSQL and Parquet archive")

    # Command: run
    p_run = subparsers.add_parser("run", help="Run full unified pipeline (collector + static sync + archiver)")
    p_run.add_argument("--collector-interval", type=float, default=15.0, help="Collector interval in seconds")
    p_run.add_argument("--monitor-interval", type=float, default=300.0, help="Monitor logging interval in seconds")
    p_run.add_argument("--static-sync-hour", type=int, default=3, help="Daily hour (0-23) for GTFS static sync")
    p_run.add_argument("--archive-hour", type=int, default=4, help="Daily hour (0-23) for Parquet archival")
    p_run.add_argument("--retention-hours", type=float, default=24.0, help="Retention threshold in hours")
    p_run.add_argument("--sync-on-startup", action="store_true", help="Sync static GTFS immediately on startup")
    p_run.add_argument("--archive-on-startup", action="store_true", help="Archive immediately on startup")
    p_run.add_argument("--skip-collector", action="store_true", help="Disable realtime collector")
    p_run.add_argument("--skip-static-sync", action="store_true", help="Disable static GTFS sync")
    p_run.add_argument("--skip-archiver", action="store_true", help="Disable Parquet archiver")
    p_run.add_argument("--run-once", action="store_true", help="Run a single collector cycle and exit")
    p_run.add_argument("--max-iterations", type=int, default=None, help="Max collector iterations")

    # Command: collect
    p_collect = subparsers.add_parser("collect", help="Run realtime vehicle positions collector only")
    p_collect.add_argument("--interval", type=float, default=15.0, help="Polling interval in seconds")
    p_collect.add_argument("--max-iterations", type=int, default=None, help="Maximum collection iterations")
    p_collect.add_argument("--url", default="https://mkuran.pl/gtfs/warsaw/vehicles.pb", help="GTFS-RT URL")

    # Command: load-gtfs
    p_load = subparsers.add_parser("load-gtfs", help="Download and load static GTFS into PostgreSQL")
    p_load.add_argument("--force", action="store_true", help="Force reload even if version exists in DB")
    p_load.add_argument("--skip-download", action="store_true", help="Skip download and load existing ZIP")
    p_load.add_argument("--load-anyway", action="store_true", help="Load even if download found matching version")
    p_load.add_argument("--zip-path", type=str, default=None, help="Custom path to GTFS zip archive")
    p_load.add_argument("--url", type=str, default=None, help="Custom download URL")

    # Command: archive
    p_archive = subparsers.add_parser("archive", help="Archive historical vehicle positions to Parquet")
    p_archive.add_argument("--older-than-hours", type=float, default=24.0, help="Retention threshold in hours")
    p_archive.add_argument("--cutoff-time", type=str, default=None, help="Explicit ISO cutoff timestamp")
    p_archive.add_argument("--dry-run", action="store_true", help="Report candidate records without writing/deleting")
    p_archive.add_argument("--no-delete", action="store_true", help="Export to Parquet without deleting from PG")
    p_archive.add_argument("--archive-dir", type=str, default=None, help="Target archive directory")

    # Command: query
    p_query = subparsers.add_parser("query", help="Query archived Parquet files using DuckDB")
    p_query.add_argument("--date-from", type=str, default=None, help="Start date/timestamp")
    p_query.add_argument("--date-to", type=str, default=None, help="End date/timestamp")
    p_query.add_argument("--route-id", type=str, default=None, help="Filter by route ID")
    p_query.add_argument("--vehicle-id", type=str, default=None, help="Filter by vehicle ID")
    p_query.add_argument("--limit", type=int, default=20, help="Max rows to display")
    p_query.add_argument("--archive-dir", type=str, default=None, help="Archive directory")

    # Command: verify
    p_verify = subparsers.add_parser("verify", help="Run automated verification checks")
    p_verify.add_argument(
        "--suite",
        choices=["all", "gtfs", "realtime", "archive", "unit", "pytest"],
        default="all",
        help="Verification suite to run (default: all; use 'unit' for pytest)",
    )

    # Command: init-db
    p_init = subparsers.add_parser("init-db", help="Initialize or check PostgreSQL database schema")
    p_init.add_argument("--check-only", action="store_true", help="Only verify existing tables and indexes")
    p_init.add_argument("--drop-all", action="store_true", help="Drop all tables and recreate from schema.sql")

    return parser


def main() -> None:
    """Main CLI entrypoint."""
    parser = build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    dispatch = {
        "inspect": cmd_inspect,
        "stats": cmd_stats,
        "run": cmd_run,
        "collect": cmd_collect,
        "load-gtfs": cmd_load_gtfs,
        "archive": cmd_archive,
        "query": cmd_query,
        "verify": cmd_verify,
        "init-db": cmd_init_db,
    }

    handler = dispatch.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

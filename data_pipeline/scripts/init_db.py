"""Database initialization script for Opti-Transit.

Creates the application database (if missing) and executes `schema.sql`
to create all GTFS static and realtime tables and indexes.

Usage:
    uv run python scripts/init_db.py
    uv run python scripts/init_db.py --drop-all
    uv run python scripts/init_db.py --check-only
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure UTF-8 output on Windows terminal
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Ensure data_pipeline root is on sys.path when running script directly
_DATA_PIPELINE_ROOT = Path(__file__).resolve().parent.parent
if str(_DATA_PIPELINE_ROOT) not in sys.path:
    sys.path.insert(0, str(_DATA_PIPELINE_ROOT))

import psycopg
from psycopg import sql
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from db import close_sync_pool, get_config, get_connection

console = Console()

# Tables to drop if --drop-all is passed
_MANAGED_TABLES = [
    "vehicle_positions",
    "gtfs_stop_times",
    "gtfs_trips",
    "gtfs_shapes",
    "gtfs_calendar_dates",
    "gtfs_stops",
    "gtfs_routes",
    "gtfs_agency",
    "feed_metadata",
]


def ensure_database_exists(target_db: str) -> None:
    """Connects to the default 'postgres' database to ensure target_db exists."""
    console.print(f"[bold cyan]Sprawdzanie bazy danych '{target_db}'[/bold cyan]")
    try:
        with get_connection(dbname="postgres", autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM pg_database WHERE datname = %s;",
                    (target_db,),
                )
                exists = cur.fetchone() is not None

                if not exists:
                    console.print(f"[yellow]⚡ Baza '{target_db}' nie istnieje. Tworzenie...[/yellow]")
                    cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(target_db)))
                    console.print(f"[bold green] Baza danych '{target_db}' została utworzona pomyślnie.[/bold green]")
                else:
                    console.print(f"[green]Baza danych '{target_db}' już istnieje.[/green]")
    except psycopg.OperationalError as exc:
        console.print(Panel(
            f"[bold red]Błąd połączenia z PostgreSQL:[/bold red]\n{exc}\n\n"
            f"[yellow]Wskazówka:[/yellow] Sprawdź hasło i parametry w pliku [bold].env[/bold] w folderze data_pipeline.",
            title="Błąd połączenia",
            border_style="red",
        ))
        sys.exit(1)


def drop_tables(target_db: str) -> None:
    """Drops existing managed tables."""
    console.print(f"[bold yellow]⚠️  Usuwanie istniejących tabel w bazie '{target_db}'...[/bold yellow]")
    with get_connection(dbname=target_db, autocommit=True) as conn:
        with conn.cursor() as cur:
            for table in _MANAGED_TABLES:
                cur.execute(sql.SQL("DROP TABLE IF EXISTS {} CASCADE;").format(sql.Identifier(table)))
                console.print(f"  [dim]Usunięto (o ile istniała):[/dim] {table}")
    console.print("[bold green]✓ Tabele zostały usunięte.[/bold green]")


def apply_schema(target_db: str, schema_path: Path) -> None:
    """Reads and executes schema.sql in a single transaction."""
    if not schema_path.exists():
        console.print(f"[bold red]Błąd: Plik ze schematem nie istnieje: {schema_path}[/bold red]")
        sys.exit(1)

    schema_sql = schema_path.read_text(encoding="utf-8")
    console.print(f"[bold cyan]Aplikowanie schematu z {schema_path.name}...[/bold cyan]")

    with get_connection(dbname=target_db) as conn:
        with conn.cursor() as cur:
            cur.execute(schema_sql)
        conn.commit()

    console.print("[bold green]Schemat SQL został zaaplikowany pomyślnie![/bold green]")


def display_db_status(target_db: str) -> None:
    """Queries and prints current tables, column count, and index count."""
    with get_connection(dbname=target_db) as conn:
        with conn.cursor() as cur:
            # Query tables and column count
            cur.execute("""
                SELECT 
                    t.table_name,
                    COUNT(c.column_name) AS column_count
                FROM information_schema.tables t
                LEFT JOIN information_schema.columns c 
                    ON t.table_name = c.table_name 
                    AND t.table_schema = c.table_schema
                WHERE t.table_schema = 'public'
                  AND t.table_type = 'BASE TABLE'
                GROUP BY t.table_name
                ORDER BY t.table_name;
            """)
            tables = cur.fetchall()

            # Query indexes
            cur.execute("""
                SELECT tablename, indexname 
                FROM pg_indexes 
                WHERE schemaname = 'public'
                ORDER BY tablename, indexname;
            """)
            indexes = cur.fetchall()

    index_map: dict[str, list[str]] = {}
    for tbl, idx in indexes:
        index_map.setdefault(tbl, []).append(idx)

    summary_table = Table(title=f"📊 Stan bazy '{target_db}' (public schema)", border_style="cyan")
    summary_table.add_column("Tabela", style="bold green")
    summary_table.add_column("Liczba kolumn", justify="right", style="cyan")
    summary_table.add_column("Indeksy", style="magenta")

    for tbl_name, col_count in tables:
        tbl_indexes = index_map.get(tbl_name, [])
        idx_display = ", ".join(tbl_indexes) if tbl_indexes else "[dim]Brak[/dim]"
        summary_table.add_row(tbl_name, str(col_count), idx_display)

    console.print(summary_table)


def main() -> None:
    """Main CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Inicjalizacja bazy danych PostgreSQL dla Opti-Transit")
    parser.add_argument(
        "--drop-all",
        action="store_true",
        help="Usuń istniejące tabele przed zaaplikowaniem schematu",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Sprawdź tylko stan bazy i istniejące tabele bez modyfikacji",
    )
    parser.add_argument(
        "--schema-file",
        type=Path,
        default=_DATA_PIPELINE_ROOT / "sql" / "schema.sql",
        help="Ścieżka do pliku SQL ze schematem",
    )

    args = parser.parse_args()
    config = get_config()

    console.print(Panel(
        f"[bold]Serwer:[/bold] {config.host}:{config.port}\n"
        f"[bold]Baza:[/bold] {config.dbname}\n"
        f"[bold]Użytkownik:[/bold] {config.user}\n"
        f"[bold]Plik schematu:[/bold] {args.schema_file}",
        title="🔧 Opti-Transit — Inicjalizacja Bazy Danych",
        border_style="blue",
    ))

    try:
        if args.check_only:
            display_db_status(config.dbname)
            return

        # 1. Upewnij się, że baza danych istnieje
        ensure_database_exists(config.dbname)

        # 2. Opcjonalnie usuń istniejące tabele
        if args.drop_all:
            drop_tables(config.dbname)

        # 3. Zaaplikuj schema.sql
        apply_schema(config.dbname, args.schema_file)

        # 4. Wyświetl stan bazy
        display_db_status(config.dbname)
    finally:
        close_sync_pool()


if __name__ == "__main__":
    main()

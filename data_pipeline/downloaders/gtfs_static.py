"""GTFS Static Downloader (warsaw.zip).

Downloads the GTFS static feed archive from mkuran.pl to the data/ directory,
checks for new feed versions using feed_info.txt, and compares with PostgreSQL feed_metadata.
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import sys
import zipfile
from pathlib import Path
from typing import Any

# Ensure UTF-8 output on Windows terminal
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Ensure data_pipeline root is on sys.path
_DATA_PIPELINE_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _DATA_PIPELINE_ROOT.parent
if str(_DATA_PIPELINE_ROOT) not in sys.path:
    sys.path.insert(0, str(_DATA_PIPELINE_ROOT))

import httpx
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

from db import get_connection

console = Console()

DEFAULT_URL = os.getenv("GTFS_STATIC_URL", "https://mkuran.pl/gtfs/warsaw.zip")
DEFAULT_DEST_PATH = _REPO_ROOT / "data" / "warsaw.zip"


def get_latest_db_feed_version() -> str | None:
    """Returns the latest loaded feed_version from the feed_metadata table."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT feed_version FROM feed_metadata ORDER BY loaded_at DESC LIMIT 1;"
                )
                row = cur.fetchone()
                return row[0] if row else None
    except Exception as exc:
        console.print(f"[yellow]Ostrzeżenie: Nie udało się pobrać wersji z bazy ({exc}).[/yellow]")
        return None


def extract_feed_info(zip_path: Path) -> dict[str, str]:
    """Reads feed_info.txt directly from a local GTFS zip archive without extracting."""
    if not zip_path.exists():
        return {}

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            if "feed_info.txt" not in zf.namelist():
                return {}
            with zf.open("feed_info.txt") as f:
                reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
                for row in reader:
                    return dict(row)
    except Exception as exc:
        console.print(f"[yellow]Błąd odczytu feed_info.txt z {zip_path.name}: {exc}[/yellow]")
    return {}


def download_gtfs_zip(
    url: str = DEFAULT_URL,
    dest_path: Path = DEFAULT_DEST_PATH,
    force: bool = False,
) -> tuple[Path, dict[str, str], bool]:
    """Downloads GTFS static zip archive if remote has a new version or if forced.

    Args:
        url: Direct HTTP URL to the GTFS zip archive.
        dest_path: Target path to save the archive (defaults to opti-transit/data/warsaw.zip).
        force: If True, forces download even if local file or DB has matching version.

    Returns:
        (dest_path, feed_info_dict, is_new_version)
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = dest_path.with_suffix(".zip.tmp")

    db_version = get_latest_db_feed_version()
    console.print(f"[bold cyan]Sprawdzanie wersji feedu GTFS Static...[/bold cyan]")
    if db_version:
        console.print(f"  [dim]Aktualna wersja w bazie PostgreSQL:[/dim] [bold green]{db_version}[/bold green]")
    else:
        console.print(f"  [dim]Aktualna wersja w bazie PostgreSQL:[/dim] [yellow]Brak danych (baza pusta)[/yellow]")

    # Check if local file exists and what version it is
    local_feed_info = extract_feed_info(dest_path) if dest_path.exists() else {}
    local_version = local_feed_info.get("feed_version")
    if local_version:
        console.print(f"  [dim]Lokalny plik {dest_path.name}:[/dim] [cyan]{local_version}[/cyan]")

    # Check remote HTTP headers
    remote_last_modified = None
    remote_content_length = None
    try:
        with httpx.Client(follow_redirects=True, timeout=15.0) as client:
            head_resp = client.head(url)
            if head_resp.status_code == 200:
                remote_last_modified = head_resp.headers.get("last-modified")
                cl_header = head_resp.headers.get("content-length")
                if cl_header:
                    remote_content_length = int(cl_header)
    except Exception as exc:
        console.print(f"[yellow]Ostrzeżenie: Nie udało się pobrać nagłówków HEAD: {exc}[/yellow]")

    # If local file already exists, matches remote size, and has same version as DB (and not force), skip download
    if (
        not force
        and dest_path.exists()
        and local_version
        and db_version
        and local_version == db_version
        and remote_content_length
        and dest_path.stat().st_size == remote_content_length
    ):
        console.print(
            f"[bold green]Lokalny plik {dest_path.name} jest aktualny i zgodny z bazą ({local_version}). Pomijam pobieranie.[/bold green]"
        )
        return dest_path, local_feed_info, False

    # Download with streaming and Rich progress bar
    console.print(f"[bold cyan]Pobieranie {url} -> {dest_path}...[/bold cyan]")
    with httpx.Client(follow_redirects=True, timeout=120.0) as client:
        with client.stream("GET", url) as response:
            if response.status_code != 200:
                raise RuntimeError(
                    f"Błąd HTTP {response.status_code} podczas pobierania {url}"
                )

            total_size = int(response.headers.get("content-length", 0))

            with Progress(
                TextColumn("[bold blue]{task.fields[filename]}"),
                BarColumn(bar_width=40),
                DownloadColumn(),
                TransferSpeedColumn(),
                TimeRemainingColumn(),
                console=console,
            ) as progress:
                task = progress.add_task(
                    "download",
                    total=total_size or None,
                    filename=dest_path.name,
                )

                with open(temp_path, "wb") as f:
                    for chunk in response.iter_bytes(chunk_size=65536):
                        f.write(chunk)
                        progress.update(task, advance=len(chunk))

    # Safely replace destination file
    if dest_path.exists():
        dest_path.unlink()
    temp_path.replace(dest_path)

    console.print(f"[bold green]Pobrano pomyślnie:[/bold green] {dest_path} ({dest_path.stat().st_size / (1024*1024):.1f} MB)")

    # Read feed_info.txt from newly downloaded archive
    new_feed_info = extract_feed_info(dest_path)
    new_version = new_feed_info.get("feed_version", "Nieznana")
    console.print(f"  [dim]Pobrana wersja feedu:[/dim] [bold green]{new_version}[/bold green]")

    is_new = (new_version != db_version)
    return dest_path, new_feed_info, is_new


def main() -> None:
    """CLI entrypoint for standalone downloading."""
    parser = argparse.ArgumentParser(description="Pobieranie archiwum warsaw.zip (GTFS Static)")
    parser.add_argument("--url", default=DEFAULT_URL, help="URL do archiwum GTFS")
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST_PATH, help="Docelowa ścieżka pliku zip")
    parser.add_argument("--force", action="store_true", help="Wymuś pobieranie nawet jeśli wersja jest aktualna")

    args = parser.parse_args()

    console.print(Panel(
        f"[bold]URL:[/bold] {args.url}\n"
        f"[bold]Cel:[/bold] {args.dest}\n"
        f"[bold]Force:[/bold] {args.force}",
        title="📥 GTFS Static Downloader",
        border_style="cyan",
    ))

    dest, info, is_new = download_gtfs_zip(url=args.url, dest_path=args.dest, force=args.force)
    if is_new:
        console.print("[bold yellow]Dostępna jest nowa wersja feedu! Wymagane załadowanie do bazy.[/bold yellow]")
    else:
        console.print("[green]Baza danych zawiera już tę wersję feedu.[/green]")


if __name__ == "__main__":
    main()

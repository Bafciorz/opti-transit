"""Database connection and pooling module for Opti-Transit.

Provides synchronous and asynchronous database connections and pools using psycopg 3.
Configuration is loaded from environment variables or a local `.env` file.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Iterator

from dotenv import load_dotenv
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool, ConnectionPool

# Load environment variables from .env file (if present)
_ENV_PATH = Path(__file__).resolve().parent / ".env"
if _ENV_PATH.exists():
    load_dotenv(_ENV_PATH)
else:
    load_dotenv()  # Fallback to current working directory search


@dataclass(frozen=True)
class DatabaseConfig:
    """PostgreSQL configuration parameters."""

    host: str = os.getenv("DB_HOST", "localhost")
    port: int = int(os.getenv("DB_PORT", "5432"))
    dbname: str = os.getenv("DB_NAME", "opti_transit")
    user: str = os.getenv("DB_USER", "postgres")
    password: str = os.getenv("DB_PASSWORD", "postgres")

    @property
    def conninfo(self) -> str:
        """Standard PostgreSQL conninfo connection string."""
        return (
            f"host={self.host} "
            f"port={self.port} "
            f"dbname={self.dbname} "
            f"user={self.user} "
            f"password={self.password}"
        )

    def conninfo_for_db(self, dbname: str) -> str:
        """Connection string pointing to a specific database name."""
        return (
            f"host={self.host} "
            f"port={self.port} "
            f"dbname={dbname} "
            f"user={self.user} "
            f"password={self.password}"
        )


_config = DatabaseConfig()
_sync_pool: ConnectionPool | None = None
_async_pool: AsyncConnectionPool | None = None


def get_config() -> DatabaseConfig:
    """Returns the current database configuration."""
    return _config


def get_connection(
    dbname: str | None = None,
    autocommit: bool = False,
    row_factory: Any = None,
) -> psycopg.Connection:
    """Creates and returns a new synchronous psycopg connection.

    Args:
        dbname: Optional specific database name override (e.g. 'postgres').
        autocommit: If True, executes queries outside a transaction block.
        row_factory: Optional psycopg row factory (e.g. dict_row).

    Returns:
        psycopg.Connection instance.
    """
    conninfo = _config.conninfo_for_db(dbname) if dbname else _config.conninfo
    kwargs: dict[str, Any] = {"autocommit": autocommit}
    if row_factory is not None:
        kwargs["row_factory"] = row_factory
    return psycopg.connect(conninfo, **kwargs)


async def get_async_connection(
    dbname: str | None = None,
    autocommit: bool = False,
    row_factory: Any = None,
) -> psycopg.AsyncConnection:
    """Creates and returns a new asynchronous psycopg connection."""
    conninfo = _config.conninfo_for_db(dbname) if dbname else _config.conninfo
    kwargs: dict[str, Any] = {"autocommit": autocommit}
    if row_factory is not None:
        kwargs["row_factory"] = row_factory
    return await psycopg.AsyncConnection.connect(conninfo, **kwargs)


def get_sync_pool(
    min_size: int = 1,
    max_size: int = 10,
    open_now: bool = True,
) -> ConnectionPool:
    """Returns or creates the singleton synchronous connection pool."""
    global _sync_pool
    if _sync_pool is None or _sync_pool.closed:
        _sync_pool = ConnectionPool(
            conninfo=_config.conninfo,
            min_size=min_size,
            max_size=max_size,
            open=open_now,
        )
    return _sync_pool


def get_async_pool(
    min_size: int = 1,
    max_size: int = 10,
    open_now: bool = False,
) -> AsyncConnectionPool:
    """Returns or creates the singleton asynchronous connection pool."""
    global _async_pool
    if _async_pool is None or _async_pool.closed:
        _async_pool = AsyncConnectionPool(
            conninfo=_config.conninfo,
            min_size=min_size,
            max_size=max_size,
            open=open_now,
        )
    return _async_pool


def close_sync_pool() -> None:
    """Closes the synchronous connection pool."""
    global _sync_pool
    if _sync_pool is not None and not _sync_pool.closed:
        _sync_pool.close()
        _sync_pool = None


async def close_async_pool() -> None:
    """Closes the asynchronous connection pool."""
    global _async_pool
    if _async_pool is not None and not _async_pool.closed:
        await _async_pool.close()
        _async_pool = None


async def close_all_pools() -> None:
    """Closes both synchronous and asynchronous connection pools."""
    close_sync_pool()
    await close_async_pool()


# Backwards compatibility alias for synchronous pool closing
close_pools = close_sync_pool


@contextmanager
def get_db_cursor(
    dbname: str | None = None,
    autocommit: bool = False,
) -> Iterator[psycopg.Cursor]:
    """Context manager for a single connection and cursor.

    Automatically closes connection and commits/rolls back.
    """
    with get_connection(dbname=dbname, autocommit=autocommit) as conn:
        with conn.cursor() as cur:
            yield cur


@asynccontextmanager
async def get_async_db_cursor(
    dbname: str | None = None,
    autocommit: bool = False,
) -> AsyncIterator[psycopg.AsyncCursor]:
    """Async context manager for an async connection and cursor."""
    async with await get_async_connection(dbname=dbname, autocommit=autocommit) as conn:
        async with conn.cursor() as cur:
            yield cur


def test_connection(dbname: str | None = None) -> tuple[bool, str]:
    """Tests connectivity to PostgreSQL.

    Returns:
        (True, version_string) on success, or (False, error_message) on failure.
    """
    try:
        with get_connection(dbname=dbname, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT version();")
                res = cur.fetchone()
                version = res[0] if res else "Unknown"
                return True, version
    except Exception as exc:
        return False, str(exc)

"""Azure SQL Database connection helper (Phase 1).

Env (set in App Service / local .env — never commit secrets):

  AZURE_SQL_SERVER=zinklondon-db.database.windows.net
  AZURE_SQL_DATABASE=<your-db-name>
  AZURE_SQL_USER=<sql-admin-or-user>
  AZURE_SQL_PASSWORD=<password>
  AZURE_SQL_ENCRYPT=yes
  AZURE_SQL_TRUST_SERVER_CERTIFICATE=no

Optional:
  AZURE_SQL_ODBC_DRIVER=ODBC Driver 18 for SQL Server
  AZURE_SQL_ENABLED=1
  AZURE_SQL_QUERY=1   # Home/Sales/Analyzer/Target filter+KPI queries on SQL
"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Any, Generator, Iterator

logger = logging.getLogger(__name__)


def azure_sql_enabled() -> bool:
    flag = (os.environ.get("AZURE_SQL_ENABLED") or "").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return False
    if flag in ("1", "true", "yes", "on"):
        return True
    # Auto-enable when server + database + credentials are present.
    return bool(
        (os.environ.get("AZURE_SQL_SERVER") or "").strip()
        and (os.environ.get("AZURE_SQL_DATABASE") or "").strip()
        and (os.environ.get("AZURE_SQL_USER") or "").strip()
        and (os.environ.get("AZURE_SQL_PASSWORD") or "").strip()
    )


def _odbc_driver() -> str:
    return (
        os.environ.get("AZURE_SQL_ODBC_DRIVER")
        or "ODBC Driver 18 for SQL Server"
    ).strip()


def connection_string() -> str:
    server = (os.environ.get("AZURE_SQL_SERVER") or "").strip()
    database = (os.environ.get("AZURE_SQL_DATABASE") or "").strip()
    user = (os.environ.get("AZURE_SQL_USER") or "").strip()
    password = os.environ.get("AZURE_SQL_PASSWORD") or ""
    encrypt = (os.environ.get("AZURE_SQL_ENCRYPT") or "yes").strip()
    trust = (os.environ.get("AZURE_SQL_TRUST_SERVER_CERTIFICATE") or "no").strip()
    if not server or not database or not user or not password:
        raise RuntimeError(
            "Azure SQL env incomplete. Set AZURE_SQL_SERVER, AZURE_SQL_DATABASE, "
            "AZURE_SQL_USER, AZURE_SQL_PASSWORD."
        )
    driver = _odbc_driver()
    return (
        f"DRIVER={{{driver}}};"
        f"SERVER={server};"
        f"DATABASE={database};"
        f"UID={user};"
        f"PWD={password};"
        f"Encrypt={encrypt};"
        f"TrustServerCertificate={trust};"
        "Connection Timeout=30;"
    )


@contextmanager
def azure_sql_connection() -> Generator[Any, None, None]:
    """Yield an open pyodbc connection; commits on success, rolls back on error."""
    import pyodbc

    if not azure_sql_enabled():
        raise RuntimeError("Azure SQL is not enabled / configured.")
    conn = pyodbc.connect(connection_string(), autocommit=False)
    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass


def ping_azure_sql() -> dict[str, Any]:
    """Lightweight connectivity check for /health or ops scripts."""
    if not azure_sql_enabled():
        return {
            "ok": False,
            "configured": False,
            "error": "Azure SQL env not set",
        }
    try:
        with azure_sql_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1 AS ok, DB_NAME() AS db_name, @@VERSION AS version")
            row = cur.fetchone()
            version = str(row[2] or "") if row else ""
            return {
                "ok": True,
                "configured": True,
                "server": (os.environ.get("AZURE_SQL_SERVER") or "").strip(),
                "database": str(row[1]) if row else None,
                "version_preview": version.split("\n")[0][:120] if version else None,
            }
    except Exception as exc:
        logger.warning("Azure SQL ping failed: %s", exc)
        return {
            "ok": False,
            "configured": True,
            "server": (os.environ.get("AZURE_SQL_SERVER") or "").strip(),
            "error": str(exc),
        }


def execute(sql: str, params: tuple | list | None = None) -> None:
    with azure_sql_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, params or [])


def fetchall(sql: str, params: tuple | list | None = None) -> list[tuple]:
    with azure_sql_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, params or [])
        return list(cur.fetchall())


def iter_batches(
    sql: str,
    params: tuple | list | None = None,
    *,
    batch_size: int = 5000,
) -> Iterator[list[tuple]]:
    with azure_sql_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, params or [])
        while True:
            rows = cur.fetchmany(batch_size)
            if not rows:
                break
            yield list(rows)

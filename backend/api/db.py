"""Database connection helper — read-only for API.

Backends:
  - blob: Azure Blob only (default). SQL/DAX helpers are disabled.
  - powerbi: legacy DAX (not used for transfer/replen)
  - fabric_sql: legacy Fabric SQL (disabled — raise clear error)

Set DATABASE_BACKEND=blob in .env.
"""
from .config import DATABASE_BACKEND

_BACKEND = DATABASE_BACKEND.lower()
_USE_BLOB = _BACKEND in ("blob", "azure_blob", "storage")
_USE_POWERBI = _BACKEND == "powerbi"
_USE_FABRIC = _BACKEND == "fabric_sql"


def get_conn():
    """Return a connection/session."""
    if _USE_BLOB:
        raise RuntimeError(
            "DATABASE_BACKEND=blob: use api.blob_store.BlobJsonStore for data. "
            "SQL/DAX connections are disabled (OneLake/Fabric cut)."
        )
    if _USE_POWERBI:
        from .db_powerbi import get_powerbi_session
        return get_powerbi_session()
    if _USE_FABRIC:
        raise RuntimeError(
            "Fabric SQL is disabled. Set DATABASE_BACKEND=blob and use Azure Blob Storage."
        )

    raise RuntimeError(
        "Invalid DATABASE_BACKEND. Set DATABASE_BACKEND=blob in .env"
    )


def query_one(conn, query: str, params=None):
    """Execute query and return first row."""
    if _USE_BLOB:
        raise RuntimeError("DATABASE_BACKEND=blob: SQL/DAX query_one is disabled.")
    if _USE_POWERBI:
        from .db_powerbi import query_one_dax
        return query_one_dax(query)
    if _USE_FABRIC:
        raise RuntimeError("Fabric SQL is disabled. Use DATABASE_BACKEND=blob.")
    raise RuntimeError("Invalid DATABASE_BACKEND. Set DATABASE_BACKEND=blob in .env")


def query_all(conn, query: str, params=None):
    """Execute query and return all rows."""
    if _USE_BLOB:
        raise RuntimeError("DATABASE_BACKEND=blob: SQL/DAX query_all is disabled.")
    if _USE_POWERBI:
        from .db_powerbi import query_all_dax
        return query_all_dax(query)
    if _USE_FABRIC:
        raise RuntimeError("Fabric SQL is disabled. Use DATABASE_BACKEND=blob.")
    raise RuntimeError("Invalid DATABASE_BACKEND. Set DATABASE_BACKEND=blob in .env")


def query_df(conn, query: str, params=None):
    """Execute query and return DataFrame."""
    if _USE_BLOB:
        raise RuntimeError("DATABASE_BACKEND=blob: SQL/DAX query_df is disabled.")
    if _USE_POWERBI:
        from .db_powerbi import query_df_dax
        return query_df_dax(query)
    if _USE_FABRIC:
        raise RuntimeError("Fabric SQL is disabled. Use DATABASE_BACKEND=blob.")
    raise RuntimeError("Invalid DATABASE_BACKEND. Set DATABASE_BACKEND=blob in .env")

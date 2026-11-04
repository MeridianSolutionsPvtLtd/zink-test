"""
Fabric SQL — DISABLED.

merged_backend uses Azure Blob only. This module remains importable so legacy
route files do not crash at import time, but every query raises.
"""


class _FabricDisabled:
    def __getattr__(self, name):
        raise RuntimeError(
            "Fabric SQL / OneLake disabled. Use DATABASE_BACKEND=blob and api.blob_store."
        )


def get_connection():
    raise RuntimeError(
        "Fabric SQL / OneLake is disabled in merged_backend. "
        "Set DATABASE_BACKEND=blob and use AZURE_STORAGE_CONNECTION_STRING."
    )


def query_one(*_a, **_k):
    raise RuntimeError("Fabric SQL disabled — use Azure Blob.")


def query_all(*_a, **_k):
    raise RuntimeError("Fabric SQL disabled — use Azure Blob.")


def query_df(*_a, **_k):
    raise RuntimeError("Fabric SQL disabled — use Azure Blob.")


def is_configured() -> bool:
    return False

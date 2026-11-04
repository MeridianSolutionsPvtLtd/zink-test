"""
DEPRECATED: OneLake / Fabric Data Lake access has been removed.

All callers now use Azure Blob via api.blob_store.BlobJsonStore.
This module only re-exports the Blob store under the old name so existing
imports (`from api.onelake_store import OneLakeJsonStore`) keep working.
"""
from api.blob_store import BlobJsonStore as OneLakeJsonStore

__all__ = ["OneLakeJsonStore"]

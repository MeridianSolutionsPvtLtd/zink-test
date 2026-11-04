from fastapi import APIRouter, HTTPException
import logging

router = APIRouter(prefix="/api/combined", tags=["Combined Data"])
logger = logging.getLogger(__name__)

@router.get("/summary")
async def get_combined_summary():
    """Blob-only health summary (Power BI / Fabric lakehouse removed)."""
    try:
        from api.blob_store import BlobJsonStore

        ping = BlobJsonStore().ping()
        return {
            "source": "azure_blob",
            "blob": ping,
            "source_powerbi": {"disabled": True, "message": "Cut over to Azure Blob"},
            "source_lakehouse": {"disabled": True, "message": "OneLake / Fabric SQL removed"},
            "status": "success" if ping.get("ok") else "error",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

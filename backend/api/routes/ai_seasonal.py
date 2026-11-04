"""AI Seasonal routes — Fabric-backed; disabled after Blob cutover."""
from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/ai-seasonal", tags=["AI Seasonal Planning"])

_DISABLED = {
    "disabled": True,
    "message": "AI Seasonal uses Fabric SQL which is removed. Use Replenishments / Transfers (Azure Blob).",
}


@router.get("/recommendation-summary")
async def get_recommendation_summary(
    target_season: str = None,
    baseline_years: list[str] = Query(default=[]),
    site: str = None,
    sku: str = None,
    dept: str = None,
    month: str = None,
):
    return _DISABLED


@router.get("/attribute-allocation")
async def get_attribute_allocation(
    attribute: str = "fabric",
    target_season: str = None,
    baseline_years: list[str] = Query(default=[]),
    site: str = None,
    sku: str = None,
    dept: str = None,
    month: str = None,
):
    return {**_DISABLED, "rows": []}


@router.get("/planning-insights")
async def get_planning_insights(
    limit: int = 50,
    offset: int = 0,
    sort_by: str = "predicted_monthly_qty_2026",
    sort_order: str = "desc",
    target_season: str = None,
    baseline_years: list[str] = Query(default=[]),
    site: str = None,
    sku: str = None,
    dept: str = None,
    month: str = None,
):
    return {**_DISABLED, "rows": [], "total": 0}

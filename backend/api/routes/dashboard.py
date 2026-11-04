"""
Legacy Power BI dashboard routes — unused stub KPIs/filters removed.

Removed (not used by Home / Sales / Managers):
  /api/dashboard/stores|skus|seasons|months|categories  (hardcoded stubs)
  /api/dashboard/summary|regions|all|region-profile     (old Fabric/PowerBI KPIs)

Home KPIs: /api/executive-overview/* and /api/sales-dashboard/*
"""
from __future__ import annotations

import logging

from fastapi import APIRouter

from api.config import DATABASE_BACKEND

logger = logging.getLogger(__name__)

router = APIRouter()
_USE_POWERBI = (DATABASE_BACKEND or "").lower() == "powerbi"


def _fetch_discount_by_category_dax() -> list:
    """DAX: discount rate per department using [1Discount %] measure from Fact_Sales_Detail."""
    from api.db_powerbi import execute_dax
    from api.dax_queries import _norm_row

    dax = """
EVALUATE
SUMMARIZECOLUMNS(
    'Fact_Sales_Detail'[Department],
    "discount_pct", CALCULATE([1Discount %])
)
ORDER BY [discount_pct] DESC
"""
    try:
        rows = execute_dax(dax)
        result = []
        for r in rows or []:
            rn = _norm_row(r)
            dept = rn.get("DEPARTMENT") or rn.get("Department") or rn.get("department")
            disc = rn.get("discount_pct") or 0
            if dept:
                result.append(
                    {
                        "category": str(dept),
                        "discountRate": round(float(disc) * 100, 1),
                    }
                )
        return sorted(result, key=lambda x: x["discountRate"], reverse=True)
    except Exception as exc:
        logger.warning("Discount by category DAX failed: %s", exc)
        return []


@router.get("/discount-by-category")
def discount_by_category_route():
    """Return discount rate per department (Power BI when enabled)."""
    if _USE_POWERBI:
        return {"categories": _fetch_discount_by_category_dax(), "data_source": "powerbi"}
    return {"categories": [], "data_source": "none"}

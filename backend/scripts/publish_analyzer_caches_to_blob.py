"""Build analyzer/managers page caches locally and publish to Azure blob.

Uploads:
  - cache/api_cache/daily_inventory_movement.parquet (+ meta)
  - cache/page-response/sales-analyzer/*.json  (FY27 + empty-filters variants)
  - cache/page-response/store-managers/*.json
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")


def _warm_analyzer(filters: dict, label: str) -> dict:
    from api.page_response_cache import _REBUILD
    from api.routes.sales_analyzer import (
        AnalyzerQueryRequest,
        SortConfig,
        ValueConfig,
        execute_analyzer_query,
    )

    values = [
        ValueConfig(field="sales", aggregation="sum"),
        ValueConfig(field="sales_ly", aggregation="sum"),
        ValueConfig(field="sales_growth_pct", aggregation="calculated"),
        ValueConfig(field="dispatch_qty", aggregation="sum"),
        ValueConfig(field="quantity", aggregation="sum"),
        ValueConfig(field="current_soh", aggregation="sum"),
        ValueConfig(field="sell_through_pct", aggregation="calculated"),
        ValueConfig(field="asp", aggregation="avg"),
    ]
    token = _REBUILD.set(True)
    t1 = time.perf_counter()
    try:
        analyzer = execute_analyzer_query(
            AnalyzerQueryRequest(
                rows=["zone", "sales_channel"],
                columns=[],
                values=values,
                filters=filters,
                sort=[SortConfig(field="sales", direction="desc")],
                year=2027,
            )
        )
    finally:
        _REBUILD.reset(token)
    gt = ((analyzer or {}).get("grand_total") or {}).get("metrics") or {}
    print(
        f"Analyzer [{label}]:",
        f"ok={analyzer.get('ok')} rows={analyzer.get('total_records')} "
        f"dispatch={gt.get('dispatch_qty')} soh={gt.get('current_soh')} "
        f"sales={gt.get('sales')} in {time.perf_counter()-t1:.1f}s",
    )
    return analyzer or {}


def main() -> int:
    t0 = time.perf_counter()
    print("cwd=", os.getcwd())
    print("conn=", "yes" if os.environ.get("AZURE_STORAGE_CONNECTION_STRING") else "NO")

    from api.current_soh import publish_daily_movement_to_blob
    from api.routes.manager_performance import warm_managers_overview_cache
    from api.routes.sales_analyzer import _get_prepared_soh_df
    from api.routes.sales_dashboard import _blob_stamp_signature

    pub = publish_daily_movement_to_blob()
    print("DM publish:", json.dumps(pub))

    soh = _get_prepared_soh_df(force_refresh=True)
    if soh is None or soh.empty:
        print("ERROR: prepared SOH empty — aborting page cache warm")
        return 1
    soh_sum = float(soh["current_soh"].sum()) if "current_soh" in soh.columns else 0.0
    disp_sum = float(soh["dispatch_qty"].sum()) if "dispatch_qty" in soh.columns else 0.0
    print(f"Prepared SOH rows={len(soh)} soh={soh_sum:,.0f} dispatch={disp_sum:,.0f}")
    print("stamp=", _blob_stamp_signature() or "ready-blob")

    a1 = _warm_analyzer({"timeline_mode": "fy_year", "timeline_values": "FY27"}, "fy27")
    a2 = _warm_analyzer({}, "empty_filters")
    for label, payload in (("fy27", a1), ("empty", a2)):
        m = ((payload.get("grand_total") or {}).get("metrics") or {})
        if not m.get("current_soh") and not m.get("dispatch_qty"):
            print(f"ERROR: {label} warm still has empty SOH/dispatch")
            return 1

    t2 = time.perf_counter()
    managers = warm_managers_overview_cache(year=2027)
    print(
        "Managers warm:",
        f"ok={managers.get('ok')} count={managers.get('managers')} "
        f"in {time.perf_counter()-t2:.1f}s",
    )

    print(f"DONE in {time.perf_counter()-t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

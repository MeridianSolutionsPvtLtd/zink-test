"""Warm + publish every default-open cache hosted pages need."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")


def main() -> int:
    results: dict = {}

    from api.current_soh import publish_daily_movement_to_blob
    from api.routes.dispatch_lead_time import (
        _compute_lead_time_dataset,
        publish_dispatch_lead_time_to_blob,
    )
    from api.routes.focus_day_events import warm_festival_comparison_cache
    from api.routes.manager_performance import warm_managers_overview_cache
    from api.routes.product_performance import warm_product_performance_overview
    from api.routes.replenishments_recom import warm_replenishments_recom_cache
    from api.routes.sales_analyzer import (
        publish_prepared_analyzer_caches_to_blob,
        warm_analyzer_prepared_frames,
    )
    from api.routes.sales_dashboard import warm_sales_overview_cache
    from api.routes.sku_breakeven import (
        _compute_breakeven_dataset,
        publish_sku_breakeven_to_blob,
    )
    from api.page_response_cache import warm_default_page_caches

    print("1) daily_inventory_movement -> blob")
    results["daily_movement"] = publish_daily_movement_to_blob()
    print(results["daily_movement"])

    print("2) inventory lead-time + breakeven")
    dlt = _compute_lead_time_dataset(force_refresh=False)
    be = _compute_breakeven_dataset(force_refresh=False)
    results["lead_time"] = {
        "rows": 0 if dlt is None else len(dlt),
        "publish": publish_dispatch_lead_time_to_blob(),
    }
    results["breakeven"] = {
        "rows": 0 if be is None else len(be),
        "publish": publish_sku_breakeven_to_blob(),
    }
    print(results["lead_time"], results["breakeven"])

    from api.sales_year_loader import get_default_dashboard_year, warm_fy_years_sorted

    dash_year = int(get_default_dashboard_year())
    warm_years = sorted(warm_fy_years_sorted(), reverse=True)

    print("3) analyzer prepared frames")
    for y in warm_years:
        warm_analyzer_prepared_frames(year=int(y))
    results["prepared_analyzer"] = publish_prepared_analyzer_caches_to_blob()
    print(results["prepared_analyzer"])

    print("4) festival + replenishment + managers + product")
    results["festival"] = warm_festival_comparison_cache(force_refresh=False)
    results["replenishment"] = warm_replenishments_recom_cache(force_refresh=False)
    results["managers"] = warm_managers_overview_cache(year=dash_year)
    results["product"] = warm_product_performance_overview(year=dash_year, limit=1500)
    print(
        {
            k: results[k]
            for k in ("festival", "replenishment", "managers", "product")
        }
    )

    print("5) sales overview cache")
    results["sales_overview"] = warm_sales_overview_cache(force_refresh=False)
    print(results["sales_overview"])

    print("6) default page_response JSON warm (Home/Sales/Analyzer/...)")
    results["page_caches"] = warm_default_page_caches()
    print(
        {
            "ok": results["page_caches"].get("ok"),
            "seconds": results["page_caches"].get("seconds"),
            "steps": {
                k: v.get("ok") for k, v in (results["page_caches"].get("steps") or {}).items()
            },
            "publish": results["page_caches"].get("publish"),
        }
    )

    out = ROOT / "pipeline" / "output_files" / "default_cache_publish.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print("wrote", out)

    # Fail only if critical default caches did not publish
    critical_ok = True
    for key in ("daily_movement", "lead_time", "breakeven", "festival", "replenishment", "managers"):
        node = results.get(key)
        if key in ("lead_time", "breakeven"):
            pub = (node or {}).get("publish") or {}
            if not pub.get("ok"):
                critical_ok = False
        elif isinstance(node, dict) and node.get("ok") is False:
            critical_ok = False
        elif isinstance(node, dict) and "published" in node and isinstance(node["published"], dict):
            if node.get("ok") is False:
                critical_ok = False
    return 0 if critical_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

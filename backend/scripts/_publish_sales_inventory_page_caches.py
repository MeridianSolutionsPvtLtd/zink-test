"""Warm + blob-publish progressive Sales Overview + Inventory Analytics defaults."""
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
    from api.routes.dispatch_lead_time import (
        get_dispatch_lead_time_report,
        publish_dispatch_lead_time_to_blob,
    )
    from api.routes.sales_dashboard import warm_sales_overview_progressive_caches
    from api.routes.sku_breakeven import get_sku_breakeven_report, publish_sku_breakeven_to_blob
    from api.sales_year_loader import fy_token, get_default_dashboard_year

    year = int(get_default_dashboard_year())
    fy = fy_token(year)
    out: dict = {"year": year, "fy": fy}

    print("1) progressive Sales Overview page_cache -> blob")
    out["sales_progressive"] = warm_sales_overview_progressive_caches(
        year=year, fy_label=fy, force_refresh=True
    )
    print(json.dumps(out["sales_progressive"], indent=2, default=str))

    print("2) dispatch-lead-time page_size=20 -> blob")
    dlt = get_dispatch_lead_time_report(
        search="",
        alert_filter="all",
        section="",
        season="",
        sort_by="inward_date",
        sort_dir="desc",
        page=1,
        page_size=20,
        refresh=1,
    )
    out["dispatch_lead_time_page"] = {
        "ok": bool(isinstance(dlt, dict) and dlt.get("ok", True)),
        "total": (dlt or {}).get("total"),
        "rows": len((dlt or {}).get("rows") or []),
    }
    out["dispatch_lead_time_parquet"] = publish_dispatch_lead_time_to_blob()
    print(out["dispatch_lead_time_page"], out["dispatch_lead_time_parquet"])

    print("3) sku-breakeven page_size=50 -> blob")
    be = get_sku_breakeven_report(
        search="",
        risk_filter="all",
        section="",
        season="",
        sort_by="dispatch_gap_vs_be",
        sort_dir="desc",
        page=1,
        page_size=50,
        refresh=1,
    )
    out["sku_breakeven_page"] = {
        "ok": bool(isinstance(be, dict) and be.get("ok", True)),
        "total": (be or {}).get("total"),
        "rows": len((be or {}).get("rows") or []),
    }
    out["sku_breakeven_parquet"] = publish_sku_breakeven_to_blob()
    print(out["sku_breakeven_page"], out["sku_breakeven_parquet"])

    path = ROOT / "pipeline" / "output_files" / "sales_inventory_page_cache_publish.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print("wrote", path)

    ok = bool(
        (out.get("sales_progressive") or {}).get("ok")
        and (out.get("dispatch_lead_time_page") or {}).get("ok")
        and (out.get("sku_breakeven_page") or {}).get("ok")
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

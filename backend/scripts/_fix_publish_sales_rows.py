"""Force /rows page_cache write + blob upload; print why if it skips."""
from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")


def main() -> int:
    from api.page_response_cache import _blob_sync_enabled, _path, save_page_cache
    from api.routes.sales_dashboard import get_sales_dashboard_rows
    from api.sales_year_loader import fy_token, get_default_dashboard_year

    year = int(get_default_dashboard_year())
    fy = fy_token(year)
    print("blob_sync", _blob_sync_enabled(), "year", year, "fy", fy)

    common = dict(
        year=year,
        site_id="all",
        site_ids="",
        channel="all",
        month="all",
        sku="all",
        season="all",
        barcode="all",
        barcodes="",
        departments="",
        sections="",
        zones="",
        store_grades="",
        states="",
        cities="",
        search="",
        timeline_mode="fy_year",
        timeline_values=fy,
        refresh=1,
    )
    for size in (50, 500):
        print(f"\n=== rows page_size={size} ===")
        try:
            payload = get_sales_dashboard_rows(**common, page=1, page_size=size)
        except Exception:
            traceback.print_exc()
            return 1
        print(
            "ok",
            payload.get("ok"),
            "rows",
            len(payload.get("rows") or []),
            "total",
            payload.get("total"),
            "keys",
            list(payload.keys())[:12],
        )
        # Build same response_key as endpoint
        response_key = "|".join(
            str(part)
            for part in (
                "dm-soh-str-v5-site-identity",
                year,
                "all",
                "",
                "all",
                "all",
                "all",
                "all",
                "all",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "fy_year",
                fy,
                1,
                size,
            )
        )
        path = _path("sales-dashboard", response_key)
        print("expected_path", path)
        try:
            text = json.dumps(payload, default=lambda v: getattr(v, "item", lambda: str(v))())
            print("json_bytes", len(text.encode("utf-8")))
        except Exception as exc:
            print("json_dumps_failed", exc)
            traceback.print_exc()
            continue
        try:
            save_page_cache("sales-dashboard", response_key, payload)
            print("saved_exists", path.exists(), "size", path.stat().st_size if path.exists() else 0)
        except Exception:
            traceback.print_exc()
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

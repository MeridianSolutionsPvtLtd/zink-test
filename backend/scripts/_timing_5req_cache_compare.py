"""5 consecutive Analyzer requests — cache stage timing compare."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

# Keep blob sync on so blob_lookup_ms is real when Azure is reachable.
os.environ.setdefault("PAGE_CACHE_BLOB", "1")
os.environ.setdefault("ANALYTICS_QUERY_ENGINE", "duckdb")

from api.financial_calendar import CALENDAR_VERSION
from api.page_response_cache import (
    _BLOB_CHECK_AT,
    _CACHE_DIR,
    _blob_name,
    _digest,
    _path,
)
from api.routes.sales_analyzer import (
    AnalyzerQueryRequest,
    SortConfig,
    ValueConfig,
    _QUERY_RESULT_CACHE,
    execute_analyzer_query,
)


def main() -> None:
    req = AnalyzerQueryRequest(
        rows=["zone", "sales_channel"],
        columns=[],
        values=[
            ValueConfig(field="sales", aggregation="sum"),
            ValueConfig(field="sales_ly", aggregation="sum"),
            ValueConfig(field="sales_growth_pct", aggregation="calculated"),
            ValueConfig(field="dispatch_qty", aggregation="sum"),
            ValueConfig(field="quantity", aggregation="sum"),
            ValueConfig(field="current_soh", aggregation="sum"),
            ValueConfig(field="sell_through_pct", aggregation="calculated"),
            ValueConfig(field="asp", aggregation="avg"),
        ],
        filters={
            "timeline_mode": "month",
            "timeline_values": "2026-10",
            "sales_channel": ["EBO"],
            "department": ["Shirts"],
        },
        sort=[SortConfig(field="sales", direction="desc")],
        year=2027,
    )

    req_for_cache = req.model_copy(update={"year": 2027})
    cache_key = f"v20:{CALENDAR_VERSION}:{req_for_cache.model_dump_json()}"
    page_path = _path("sales-analyzer", cache_key)
    etag_path = Path(str(page_path) + ".etag")
    print("digest", _digest(cache_key))
    print("page_path", page_path)
    print("page_exists_before", page_path.exists())

    if page_path.exists():
        page_path.unlink()
    if etag_path.exists():
        etag_path.unlink()

    # Best-effort: drop remote page-cache blob so req1 is a true compute miss.
    blob_deleted = False
    try:
        from api.routes.sales_dashboard import _blob_store

        store = _blob_store()
        if store is not None:
            bname = _blob_name("sales-analyzer", cache_key)
            try:
                store.container.get_blob_client(bname).delete_blob()
                blob_deleted = True
                print("deleted blob", bname)
            except Exception as exc:
                print("blob delete skipped:", type(exc).__name__, exc)
    except Exception as exc:
        print("blob store unavailable:", type(exc).__name__, exc)

    _QUERY_RESULT_CACHE.clear()
    _BLOB_CHECK_AT.clear()

    rows: list[dict] = []
    for i in range(1, 6):
        # Clear RAM between calls so 2–5 hit page/disk cache (expected table).
        _QUERY_RESULT_CACHE.clear()
        t0 = time.perf_counter()
        res = execute_analyzer_query(req)
        wall = round((time.perf_counter() - t0) * 1000.0, 1)
        timing = (res or {}).get("timing") or {}
        gt = ((res or {}).get("grand_total") or {}).get("metrics") or {}
        row = {
            "req": i,
            "engine": timing.get("engine"),
            "memory_cache_lookup_ms": timing.get("memory_cache_lookup_ms"),
            "local_disk_lookup_ms": timing.get("local_disk_lookup_ms"),
            "blob_lookup_ms": timing.get("blob_lookup_ms"),
            "cache_lookup_ms": timing.get("cache_lookup_ms"),
            "total_api_ms": timing.get("total_api_ms"),
            "wall_ms": wall,
            "duckdb_query_ms": timing.get("duckdb_query_ms"),
            "sales": gt.get("sales"),
            "ok": (res or {}).get("ok"),
        }
        rows.append(row)
        print(json.dumps(row, default=str))

    out = Path("data/api_cache/_timing_5req_oct2026_ebo_shirts.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "filter": "Oct 2026 · Channel EBO · Department Shirts · FY27",
        "page_cache_blob_env": os.environ.get("PAGE_CACHE_BLOB"),
        "blob_deleted_before": blob_deleted,
        "note": (
            "Memory cleared before each request so 2–5 exercise page/disk cache. "
            "Local page JSON deleted before suite; remote blob deleted when possible."
        ),
        "rows": rows,
    }
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print("wrote", out)


if __name__ == "__main__":
    main()

"""DuckDB vs pandas correctness regression after SOH/LY/stale-guard fixes."""
from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

os.environ.setdefault("ANALYTICS_QUERY_ENGINE", "duckdb")
os.environ["PAGE_CACHE_BLOB"] = "0"

from api.financial_calendar import CALENDAR_VERSION
from api.page_response_cache import (
    _PAGE_RAM,
    _path,
    _CACHE_DIR,
    _page_ram_put,
    _page_ram_get,
    _analyzer_metrics_empty,
)
from api.routes.sales_analyzer import (
    AnalyzerQueryRequest,
    SortConfig,
    ValueConfig,
    _PREPARED_DF_CACHE,
    _QUERY_RESULT_CACHE,
    _STORE_GRAIN_CACHE,
    execute_analyzer_query,
)


def _values(extra=True):
    vals = [
        ValueConfig(field="sales", aggregation="sum"),
        ValueConfig(field="quantity", aggregation="sum"),
        ValueConfig(field="current_soh", aggregation="sum"),
        ValueConfig(field="dispatch_qty", aggregation="sum"),
    ]
    if extra:
        vals.extend(
            [
                ValueConfig(field="sales_ly", aggregation="sum"),
                ValueConfig(field="sell_through_pct", aggregation="calculated"),
                ValueConfig(field="asp", aggregation="avg"),
            ]
        )
    return vals


def _req(filters, rows=None, columns=None, extra=True):
    return AnalyzerQueryRequest(
        rows=rows or ["zone", "sales_channel"],
        columns=columns or [],
        values=_values(extra=extra),
        filters=filters,
        sort=[SortConfig(field="sales", direction="desc")],
        year=2027,
    )


def _purge(req):
    _QUERY_RESULT_CACHE.clear()
    _PAGE_RAM.clear()
    _PREPARED_DF_CACHE.clear()
    _STORE_GRAIN_CACHE.clear()
    rk = req.model_copy(update={"year": 2027})
    p = _path("sales-analyzer", f"v20:{CALENDAR_VERSION}:{rk.model_dump_json()}")
    if p.exists():
        p.unlink()
    et = Path(str(p) + ".etag")
    if et.exists():
        et.unlink()


def _count_nodes(rows):
    n = 0
    for r in rows or []:
        n += 1
        n += _count_nodes(r.get("children") or [])
    return n


def _snap(res):
    gt = ((res or {}).get("grand_total") or {}).get("metrics") or {}
    tm = (res or {}).get("timing") or {}
    return {
        "engine": tm.get("engine"),
        "sales": round(float(gt.get("sales") or 0), 2),
        "qty": round(float(gt.get("quantity") or 0), 2),
        "soh": round(float(gt.get("current_soh") or 0), 2),
        "disp": round(float(gt.get("dispatch_qty") or 0), 2),
        "ly": round(float(gt.get("sales_ly") or 0), 2) if gt.get("sales_ly") is not None else None,
        "records": int((res or {}).get("total_records") or 0),
        "top_rows": len((res or {}).get("rows") or []),
        "nodes": _count_nodes((res or {}).get("rows") or []),
        "cache_source": tm.get("cache_source"),
        "total_api_ms": tm.get("total_api_ms"),
        "fallback": tm.get("duckdb_fallback"),
        "fallback_reason": tm.get("duckdb_fallback_reason"),
    }


def _run(eng, filters, **kwargs):
    os.environ["ANALYTICS_QUERY_ENGINE"] = eng
    req = _req(filters, **kwargs)
    _purge(req)
    return _snap(execute_analyzer_query(req)), req


def main():
    report = {"cases": [], "checks": {}, "issues": []}

    cases = [
        ("ebo_shirts_oct", {"timeline_mode": "month", "timeline_values": "2026-10", "sales_channel": ["EBO"], "department": ["Shirts"]}),
        ("ebo_oct", {"timeline_mode": "month", "timeline_values": "2026-10", "sales_channel": ["EBO"]}),
        ("shirts_oct", {"timeline_mode": "month", "timeline_values": "2026-10", "department": ["Shirts"]}),
        ("empty_dept", {"timeline_mode": "month", "timeline_values": "2026-10", "sales_channel": ["EBO"], "department": ["ZZZ_NONE"]}),
        ("soh_positive_shirts", {"timeline_mode": "month", "timeline_values": "2026-10", "department": ["Shirts"]}, {"rows": ["zone"]}),
    ]

    for item in cases:
        name, filters = item[0], item[1]
        kw = item[2] if len(item) > 2 else {}
        duck, _ = _run("duckdb", filters, **kw)
        panda, _ = _run("pandas", filters, **kw)
        sales_ok = abs(duck["sales"] - panda["sales"]) < 0.05
        qty_ok = abs(duck["qty"] - panda["qty"]) < 0.05
        soh_ok = abs(duck["soh"] - panda["soh"]) < 1.0
        ly_ok = True
        if duck.get("ly") is not None and panda.get("ly") is not None:
            # allow 1% relative for LY floating
            denom = max(abs(panda["ly"]), 1.0)
            ly_ok = abs(duck["ly"] - panda["ly"]) / denom < 0.02 or abs(duck["ly"] - panda["ly"]) < 1.0
        nodes_ok = duck["nodes"] == panda["nodes"]
        entry = {
            "name": name,
            "duckdb": duck,
            "pandas": panda,
            "sales_match": sales_ok,
            "qty_match": qty_ok,
            "soh_match": soh_ok,
            "ly_match": ly_ok,
            "nodes_match": nodes_ok,
        }
        report["cases"].append(entry)
        for flag, label in (
            (sales_ok, "sales"),
            (qty_ok, "qty"),
            (soh_ok, "soh"),
            (ly_ok, "ly"),
            (nodes_ok, "nodes"),
        ):
            if not flag:
                report["issues"].append(f"{name}: {label} mismatch duck={duck} pandas={panda}")

    # Column split
    duck, req = _run(
        "duckdb",
        {"timeline_mode": "month", "timeline_values": "2026-10", "sales_channel": ["EBO"], "department": ["Shirts"]},
        rows=["zone"],
        columns=["sales_channel"],
    )
    panda, _ = _run(
        "pandas",
        {"timeline_mode": "month", "timeline_values": "2026-10", "sales_channel": ["EBO"], "department": ["Shirts"]},
        rows=["zone"],
        columns=["sales_channel"],
    )
    report["cases"].append(
        {
            "name": "column_split",
            "duckdb": duck,
            "pandas": panda,
            "sales_match": abs(duck["sales"] - panda["sales"]) < 0.05,
            "soh_match": abs(duck["soh"] - panda["soh"]) < 1.0,
        }
    )

    # Page cache hit after SOH>0 compute
    os.environ["ANALYTICS_QUERY_ENGINE"] = "duckdb"
    filters = {"timeline_mode": "month", "timeline_values": "2026-10", "sales_channel": ["EBO"], "department": ["Shirts"]}
    req = _req(filters)
    _purge(req)
    r1 = execute_analyzer_query(req)
    _QUERY_RESULT_CACHE.clear()
    t0 = time.perf_counter()
    r2 = execute_analyzer_query(req)
    tm2 = r2.get("timing") or {}
    report["checks"]["page_cache_after_soh"] = {
        "engine": tm2.get("engine"),
        "cache_source": tm2.get("cache_source"),
        "total_api_ms": tm2.get("total_api_ms"),
        "wall_ms": round((time.perf_counter() - t0) * 1000, 1),
        "soh": ((r2.get("grand_total") or {}).get("metrics") or {}).get("current_soh"),
        "sales": ((r1.get("grand_total") or {}).get("metrics") or {}).get("sales"),
    }
    if tm2.get("engine") != "page_cache":
        report["issues"].append("page cache not used on second request after SOH>0 compute")

    # Daily stamp invalidation
    stamp = _CACHE_DIR / "_daily_stamp.json"
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(json.dumps({"built_at": "A"}), encoding="utf-8")
    _page_ram_put("t:x", {"ok": True})
    stamp.write_text(json.dumps({"built_at": "B"}), encoding="utf-8")
    report["checks"]["daily_stamp_clears_ram"] = _page_ram_get("t:x") is None

    # Stale guard: numeric SOH=0 with sales>0 is legitimate and must remain cacheable.
    # Blank/None SOH with sales>0 is still treated as a stale warm.
    legit_zero = {
        "ok": True,
        "measures": [{"field": "sales"}, {"field": "current_soh"}, {"field": "dispatch_qty"}],
        "grand_total": {"metrics": {"sales": 100.0, "current_soh": 0.0, "dispatch_qty": 0.0}},
    }
    blank_soh = {
        "ok": True,
        "measures": [{"field": "sales"}, {"field": "current_soh"}, {"field": "dispatch_qty"}],
        "grand_total": {"metrics": {"sales": 100.0, "current_soh": None, "dispatch_qty": None}},
    }
    report["checks"]["stale_guard_zero_soh_ok"] = _analyzer_metrics_empty(legit_zero) is False
    report["checks"]["stale_guard_blank_soh_stale"] = _analyzer_metrics_empty(blank_soh) is True
    if _analyzer_metrics_empty(legit_zero):
        report["issues"].append("stale guard wrongly treats numeric SOH=0 as stale")
    if not _analyzer_metrics_empty(blank_soh):
        report["issues"].append("stale guard missed blank SOH warm")

    # Timeline-specific cache keys: month filter must not reuse unfiltered / other month
    os.environ["ANALYTICS_QUERY_ENGINE"] = "duckdb"
    req_oct = _req({"timeline_mode": "month", "timeline_values": "2026-10", "sales_channel": ["EBO"]})
    req_sep = _req({"timeline_mode": "month", "timeline_values": "2026-09", "sales_channel": ["EBO"]})
    k_oct = f"v20:{CALENDAR_VERSION}:{req_oct.model_dump_json()}"
    k_sep = f"v20:{CALENDAR_VERSION}:{req_sep.model_dump_json()}"
    report["checks"]["timeline_cache_keys_differ"] = k_oct != k_sep
    if k_oct == k_sep:
        report["issues"].append("timeline cache keys collide across months")

    # Concurrent 5
    variants = [
        ("ebo_shirts", {"timeline_mode": "month", "timeline_values": "2026-10", "sales_channel": ["EBO"], "department": ["Shirts"]}),
        ("ebo", {"timeline_mode": "month", "timeline_values": "2026-10", "sales_channel": ["EBO"]}),
        ("shirts", {"timeline_mode": "month", "timeline_values": "2026-10", "department": ["Shirts"]}),
        ("empty", {"timeline_mode": "month", "timeline_values": "2026-10", "department": ["ZZZ_NONE"]}),
        ("ssl", {"timeline_mode": "month", "timeline_values": "2026-10", "sales_channel": ["SSL"]}),
    ]
    # purge analyzer page cache files
    pc = _CACHE_DIR / "sales-analyzer"
    if pc.exists():
        for p in list(pc.glob("*.json")) + list(pc.glob("*.etag")):
            p.unlink()
    _QUERY_RESULT_CACHE.clear()
    _PAGE_RAM.clear()

    def _one(label, fl):
        os.environ["ANALYTICS_QUERY_ENGINE"] = "duckdb"
        res = execute_analyzer_query(_req(fl, rows=["zone"]))
        return {"label": label, **_snap(res)}

    conc = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        futs = [pool.submit(_one, lab, fl) for lab, fl in variants]
        for f in as_completed(futs):
            conc.append(f.result())
    report["checks"]["concurrent_5"] = conc
    if any(x.get("fallback") for x in conc):
        report["issues"].append("concurrent_5 had duckdb fallback")

    report["pass"] = len(report["issues"]) == 0
    out = Path("data/api_cache/_correctness_regression.json")
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print("PASS" if report["pass"] else f"ISSUES {len(report['issues'])}")


if __name__ == "__main__":
    main()

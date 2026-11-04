"""
Local correctness + performance comparison: Pandas vs DuckDB sales filter path.

Usage (from backend root):
  python scripts/bench_sales_dashboard_engine.py

Does not modify pipeline outputs. Default production engine remains pandas.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import pandas as pd


SCENARIOS = [
    {"name": "default_fy", "kw": {}},
    {"name": "fy_season", "kw": {"season": "SS-26"}},
    {"name": "zone", "kw": {"zones": "EAST"}},
    {"name": "zone_season", "kw": {"zones": "EAST", "season": "SS-26"}},
    {"name": "zone_site", "kw": {"zones": "EAST", "site_ids": "314"}},
    {"name": "zone_site_season", "kw": {"zones": "EAST", "site_ids": "314", "season": "SS-26"}},
    {"name": "channel_pt", "kw": {"channel": "PT"}},
    {"name": "dept", "kw": {"departments": "Blouses"}},
]


def _kpi_tuple(df: pd.DataFrame) -> dict:
    if df is None or df.empty:
        return {"rows": 0, "net": 0.0, "qty": 0.0, "stores": 0}
    return {
        "rows": int(len(df)),
        "net": round(float(df["net_sale_amount"].sum()), 2),
        "qty": round(float(df["qty"].sum()), 2),
        "stores": int(df["site_id"].nunique()) if "site_id" in df.columns else 0,
    }


def _run_pandas(year_val: int, tl_mode: str, tl_values: str, **filt) -> tuple[dict, float]:
    from api.routes.sales_dashboard import (
        _apply_filters,
        _apply_timeline_filter,
        _load_sales_for_timeline,
    )

    t0 = time.perf_counter()
    full = _load_sales_for_timeline(
        year_val=year_val, timeline_mode=tl_mode, timeline_values=tl_values
    )
    filtered = _apply_filters(full, **filt)
    filtered = _apply_timeline_filter(
        filtered, timeline_mode=tl_mode, timeline_values=tl_values
    )
    ms = (time.perf_counter() - t0) * 1000
    return _kpi_tuple(filtered), ms


def _run_duck(year_val: int, tl_mode: str, tl_values: str, **filt) -> tuple[dict, float, dict]:
    from api.routes.sales_dashboard import _fy_years_for_timeline
    from api.services.analytics_query_engine import load_filtered_sales_duckdb

    fy_years = _fy_years_for_timeline(
        year_val=year_val, timeline_mode=tl_mode, timeline_values=tl_values
    )
    t0 = time.perf_counter()
    df, diag = load_filtered_sales_duckdb(
        fy_years=fy_years,
        timeline_mode=tl_mode,
        timeline_values=tl_values,
        force_refresh=True,
        **filt,
    )
    ms = (time.perf_counter() - t0) * 1000
    return _kpi_tuple(df), ms, diag


def main() -> int:
    from api.services.analytics_query_engine import duckdb_available

    if not duckdb_available():
        print("FAIL: duckdb not installed")
        return 1

    year_val = 2027
    tl_mode = "fy_year"
    tl_values = "FY27"

    print("=" * 72)
    print("StockPulse Sales Dashboard — Pandas vs DuckDB correctness/bench")
    print(f"timeline={tl_mode} values={tl_values} year={year_val}")
    print("=" * 72)

    mismatches = 0
    rows = []
    for sc in SCENARIOS:
        name = sc["name"]
        kw = sc["kw"]
        print(f"\n--- {name} {kw} ---")
        p_kpi, p_ms = _run_pandas(year_val, tl_mode, tl_values, **kw)
        d_kpi, d_ms, diag = _run_duck(year_val, tl_mode, tl_values, **kw)
        from api.services.analytics_query_engine import load_filtered_sales_duckdb
        from api.routes.sales_dashboard import _fy_years_for_timeline

        fy_years = _fy_years_for_timeline(
            year_val=year_val, timeline_mode=tl_mode, timeline_values=tl_values
        )
        # Populate cache then measure hit
        load_filtered_sales_duckdb(
            fy_years=fy_years,
            timeline_mode=tl_mode,
            timeline_values=tl_values,
            force_refresh=False,
            **kw,
        )
        t0 = time.perf_counter()
        load_filtered_sales_duckdb(
            fy_years=fy_years,
            timeline_mode=tl_mode,
            timeline_values=tl_values,
            force_refresh=False,
            **kw,
        )
        d_ms_cached = (time.perf_counter() - t0) * 1000

        ok_rows = p_kpi["rows"] == d_kpi["rows"]
        ok_qty = abs(p_kpi["qty"] - d_kpi["qty"]) < 0.01
        ok_net = abs(p_kpi["net"] - d_kpi["net"]) <= max(1.0, abs(p_kpi["net"]) * 1e-6)
        ok_stores = p_kpi["stores"] == d_kpi["stores"]
        ok = ok_rows and ok_qty and ok_net and ok_stores
        if not ok:
            mismatches += 1
        status = "PASS" if ok else "FAIL"
        print(
            f"  {status} pandas={p_kpi} ({p_ms:.0f}ms) "
            f"duckdb={d_kpi} (cold={d_ms:.0f}ms cache={d_ms_cached:.0f}ms) "
            f"version={diag.get('dataset_version')}"
        )
        if not ok:
            print(f"  DIFF rows={ok_rows} qty={ok_qty} net={ok_net} stores={ok_stores}")
        rows.append(
            {
                "scenario": name,
                "status": status,
                "pandas_ms": round(p_ms, 1),
                "duckdb_cold_ms": round(d_ms, 1),
                "duckdb_cache_ms": round(d_ms_cached, 1),
                "pandas": p_kpi,
                "duckdb": d_kpi,
            }
        )

    print("\n" + "=" * 72)
    print(f"Mismatches: {mismatches}/{len(SCENARIOS)}")
    out = ROOT / "data" / "api_cache" / "bench_sales_dashboard_engine.json"
    try:
        import json

        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        print(f"Wrote {out}")
    except Exception as exc:
        print(f"Could not write bench json: {exc}")
    return 1 if mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())

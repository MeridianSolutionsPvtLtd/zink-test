"""Compare live DuckDB /kpis fields against in-process Pandas _kpis for key scenarios."""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.setdefault("ANALYTICS_QUERY_ENGINE", "pandas")

BASE = os.environ.get("STOCKPULSE_VALIDATE_BASE", "http://127.0.0.1:5003")
OUT = ROOT / "data" / "api_cache" / "kpi_duck_vs_pandas.json"


def _http(path: str, params: dict):
    qs = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
    url = f"{BASE}{path}?{qs}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _pandas_kpis(**filt):
    """Mirror live /kpis Pandas path: soh_map + SPLY with season shift + large-year skip."""
    import pandas as pd
    from api.routes.sales_dashboard import (
        _apply_filters,
        _apply_timeline_filter,
        _dashboard_scope_dict,
        _get_soh_map,
        _kpis,
        _load_sales_for_timeline,
        _load_sply_prior_df,
    )

    year_val = int(filt.pop("year", 2027))
    tl_mode = filt.pop("timeline_mode", "fy_year")
    tl_values = filt.pop("timeline_values", "FY27")
    full = _load_sales_for_timeline(
        year_val=year_val, timeline_mode=tl_mode, timeline_values=tl_values
    )
    base_kw = dict(filt)
    filtered = _apply_filters(full, **base_kw)
    filtered = _apply_timeline_filter(
        filtered, timeline_mode=tl_mode, timeline_values=tl_values
    )
    scope = _dashboard_scope_dict(
        season=base_kw.get("season", ""),
        sku=base_kw.get("sku", ""),
        barcode=base_kw.get("barcode", ""),
        barcodes=base_kw.get("barcodes", ""),
        departments=base_kw.get("departments", ""),
        sections=base_kw.get("sections", ""),
        channel=base_kw.get("channel", ""),
        zones=base_kw.get("zones", ""),
        site_id=base_kw.get("site_id", "all"),
        site_ids=base_kw.get("site_ids", ""),
        store_grades=base_kw.get("store_grades", ""),
        states=base_kw.get("states", ""),
        cities=base_kw.get("cities", ""),
    )
    has_active = any(
        [
            base_kw.get("site_id") and base_kw.get("site_id") != "all",
            base_kw.get("site_ids"),
            base_kw.get("channel") and base_kw.get("channel") != "all",
            base_kw.get("season") and base_kw.get("season") != "all",
            base_kw.get("barcode") and base_kw.get("barcode") != "all",
            base_kw.get("barcodes"),
            base_kw.get("departments"),
            base_kw.get("sections"),
            base_kw.get("zones"),
            base_kw.get("store_grades"),
            base_kw.get("states"),
            base_kw.get("cities"),
            base_kw.get("search"),
        ]
    )
    soh_map = _get_soh_map()
    large_year_view = len(filtered) >= 150_000 or (
        str(tl_mode or "").strip().lower() in ("year", "fy_year", "fy", "financial_year")
        and not has_active
    )
    sply_prior = pd.DataFrame()
    if not large_year_view and not filtered.empty:
        sply_prior = _load_sply_prior_df(
            filtered,
            filter_kw=base_kw,
            timeline_mode=tl_mode,
            timeline_values=tl_values,
        )
    kpis = _kpis(filtered, soh_map=soh_map, df_prior=sply_prior, scope=scope)
    return kpis, int(len(filtered))


SCENARIOS = [
    {"name": "default_fy", "params": {"year": 2027, "timeline_mode": "fy_year", "timeline_values": "FY27"}},
    {"name": "fy_season", "params": {"year": 2027, "timeline_mode": "fy_year", "timeline_values": "FY27", "season": "SS-26"}},
    {"name": "zone", "params": {"year": 2027, "timeline_mode": "fy_year", "timeline_values": "FY27", "zones": "EAST"}},
    {"name": "zone_site", "params": {"year": 2027, "timeline_mode": "fy_year", "timeline_values": "FY27", "zones": "EAST", "site_ids": "314"}},
    {"name": "zone_site_season", "params": {"year": 2027, "timeline_mode": "fy_year", "timeline_values": "FY27", "zones": "EAST", "site_ids": "314", "season": "SS-26"}},
    {"name": "category", "params": {"year": 2027, "timeline_mode": "fy_year", "timeline_values": "FY27", "departments": "Blouses"}},
    {"name": "barcode", "params": {"year": 2027, "timeline_mode": "fy_year", "timeline_values": "FY27", "barcodes": "8907569465597"}},
    {"name": "date_range", "params": {"year": 2027, "timeline_mode": "date", "timeline_values": "2026-04-01,2026-05-01"}},
    {"name": "east_ss26", "params": {"year": 2027, "timeline_mode": "fy_year", "timeline_values": "FY27", "zones": "EAST", "season": "SS-26"}},
]


def _close(a, b, tol_abs=1.0, tol_rel=1e-6):
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    af, bf = float(a), float(b)
    return abs(af - bf) <= max(tol_abs, abs(af) * tol_rel)


def main() -> int:
    rows = []
    fails = 0
    for sc in SCENARIOS:
        print(f"=== {sc['name']} ===", flush=True)
        t0 = time.perf_counter()
        pk, prow = _pandas_kpis(**dict(sc["params"]))
        pms = (time.perf_counter() - t0) * 1000
        params = dict(sc["params"])
        params["refresh"] = 1
        t1 = time.perf_counter()
        duck = _http("/api/sales-dashboard/kpis", params)
        dms = (time.perf_counter() - t1) * 1000
        dk = (duck or {}).get("kpis") or {}
        checks = {
            "net_sales": _close(pk.get("net_sales"), dk.get("net_sales")),
            "units_sold": _close(pk.get("units_sold"), dk.get("units_sold"), tol_abs=0.01),
            "current_soh": _close(pk.get("current_soh"), dk.get("current_soh"), tol_abs=1.0),
            "store_count": int(pk.get("store_count") or 0) == int(dk.get("store_count") or 0),
            "total_rows": prow == int((duck or {}).get("total") or -1),
        }
        # YoY may be skipped on large unfiltered year view in DuckDB path — only compare when both set
        pyoy = pk.get("yoy_sales_growth_pct")
        dyoy = dk.get("yoy_sales_growth_pct")
        if pyoy is not None and dyoy is not None:
            checks["yoy_sales_growth_pct"] = _close(pyoy, dyoy, tol_abs=0.2)
        ok = all(checks.values()) and (duck or {}).get("query_engine") == "duckdb"
        if not ok:
            fails += 1
        row = {
            "scenario": sc["name"],
            "ok": ok,
            "checks": checks,
            "pandas_ms": round(pms, 2),
            "duck_http_ms": round(dms, 2),
            "pandas": {
                "net_sales": pk.get("net_sales"),
                "units_sold": pk.get("units_sold"),
                "current_soh": pk.get("current_soh"),
                "yoy_sales_growth_pct": pyoy,
                "rows": prow,
            },
            "duckdb": {
                "net_sales": dk.get("net_sales"),
                "units_sold": dk.get("units_sold"),
                "current_soh": dk.get("current_soh"),
                "yoy_sales_growth_pct": dyoy,
                "rows": (duck or {}).get("total"),
                "engine": (duck or {}).get("query_engine"),
            },
        }
        print(f"  ok={ok} checks={checks} duck_ms={dms:.0f} pandas_ms={pms:.0f}", flush=True)
        rows.append(row)

    payload = {
        "base": BASE,
        "fails": fails,
        "passed": len(rows) - fails,
        "total": len(rows),
        "rows": rows,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {OUT} passed={payload['passed']}/{payload['total']}", flush=True)
    return 0 if fails == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())

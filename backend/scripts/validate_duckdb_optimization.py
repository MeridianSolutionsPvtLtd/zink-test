"""
Post-implementation DuckDB validation harness (localhost only).

Measures P50/P95/max latency, peak RSS, CPU%, correctness vs Pandas,
endpoint engine usage, concurrency, dataset-version refresh, missing-file safety.

Does not modify pipeline / blob publish. Does not change production default.
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

# Keep process default pandas for import-side effects; we toggle per path.
os.environ.setdefault("ANALYTICS_QUERY_ENGINE", "pandas")

BASE = os.environ.get("STOCKPULSE_VALIDATE_BASE", "http://127.0.0.1:5001")
ITERS = int(os.environ.get("STOCKPULSE_VALIDATE_ITERS", "7"))
WARM_DROP = 2  # first N cold samples separated

OUT = ROOT / "data" / "api_cache" / "duckdb_validation_results.json"
REPORT_ROWS: list[dict] = []
CHECKS: dict[str, object] = {}


def _pct(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return round(sorted_vals[0], 2)
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return round(sorted_vals[f], 2)
    return round(sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f), 2)


def _rss_mb() -> float:
    try:
        import psutil

        return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
    except Exception:
        # Windows: WorkingSet via ctypes fallback
        try:
            import ctypes
            from ctypes import wintypes

            class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            GetCurrentProcess = ctypes.windll.kernel32.GetCurrentProcess
            GetProcessMemoryInfo = ctypes.windll.psapi.GetProcessMemoryInfo
            counters = PROCESS_MEMORY_COUNTERS()
            counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
            if GetProcessMemoryInfo(GetCurrentProcess(), ctypes.byref(counters), counters.cb):
                return counters.WorkingSetSize / (1024 * 1024)
        except Exception:
            pass
    return -1.0


def _cpu_percent(sample_s: float = 0.2) -> float:
    try:
        import psutil

        p = psutil.Process(os.getpid())
        p.cpu_percent(interval=None)
        return float(p.cpu_percent(interval=sample_s))
    except Exception:
        return -1.0


def _kpi_tuple(df) -> dict:
    import pandas as pd

    if df is None or getattr(df, "empty", True):
        return {"rows": 0, "net": 0.0, "qty": 0.0, "stores": 0}
    return {
        "rows": int(len(df)),
        "net": round(float(df["net_sale_amount"].sum()), 2),
        "qty": round(float(df["qty"].sum()), 2),
        "stores": int(df["site_id"].nunique()) if "site_id" in df.columns else 0,
    }


def _match(a: dict, b: dict) -> bool:
    if a["rows"] != b["rows"] or a["stores"] != b["stores"]:
        return False
    if abs(a["qty"] - b["qty"]) > 0.01:
        return False
    tol = max(1.0, abs(a["net"]) * 1e-6)
    return abs(a["net"] - b["net"]) <= tol


def _sample_values() -> dict:
    from api.sales_year_loader import load_sales_for_year

    df = load_sales_for_year(2027)
    barcode = ""
    if "barcode" in df.columns and not df.empty:
        barcode = str(df["barcode"].dropna().astype(str).iloc[0]).strip()
    dept = "Blouses"
    if "department" in df.columns:
        vc = df["department"].dropna().astype(str).str.strip().value_counts()
        if len(vc):
            dept = str(vc.index[0])
    # date range from data mid span
    dmin = dmax = ""
    if "date" in df.columns and not df.empty:
        s = df["date"].dropna()
        if len(s):
            dmin = str(s.min().date())
            # ~30 day window if possible
            end = min(s.min() + __import__("pandas").Timedelta(days=30), s.max())
            dmax = str(end.date())
    return {"barcode": barcode, "department": dept, "date_from": dmin, "date_to": dmax}


def _run_pandas(year_val, tl_mode, tl_values, **filt):
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


def _run_duck(year_val, tl_mode, tl_values, force_refresh: bool, **filt):
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
        force_refresh=force_refresh,
        **filt,
    )
    ms = (time.perf_counter() - t0) * 1000
    return _kpi_tuple(df), ms, diag


def bench_filter_scenarios(samples: dict) -> list[dict]:
    from api.services.analytics_query_engine import clear_analytics_query_cache, duckdb_available

    assert duckdb_available(), "duckdb not installed"
    year_val, tl_mode, tl_values = 2027, "fy_year", "FY27"
    scenarios = [
        {"name": "default_fy", "api": "filter:default_fy", "kw": {}, "tl": (tl_mode, tl_values)},
        {"name": "fy_season", "api": "filter:fy+season", "kw": {"season": "SS-26"}, "tl": (tl_mode, tl_values)},
        {"name": "zone", "api": "filter:zone", "kw": {"zones": "EAST"}, "tl": (tl_mode, tl_values)},
        {
            "name": "zone_site",
            "api": "filter:zone+site",
            "kw": {"zones": "EAST", "site_ids": "314"},
            "tl": (tl_mode, tl_values),
        },
        {
            "name": "zone_site_season",
            "api": "filter:zone+site+season",
            "kw": {"zones": "EAST", "site_ids": "314", "season": "SS-26"},
            "tl": (tl_mode, tl_values),
        },
        {"name": "category_dept", "api": "filter:category", "kw": {"departments": samples["department"]}, "tl": (tl_mode, tl_values)},
        {
            "name": "barcode",
            "api": "filter:barcode",
            "kw": {"barcodes": samples["barcode"]} if samples["barcode"] else {"barcodes": "__none__"},
            "tl": (tl_mode, tl_values),
        },
        {
            "name": "date_range",
            "api": "filter:date_range",
            "kw": {},
            "tl": ("date", f"{samples['date_from']},{samples['date_to']}"),
        },
        {
            "name": "season_category",
            "api": "filter:season+category",
            "kw": {"season": "SS-26", "departments": samples["department"]},
            "tl": (tl_mode, tl_values),
        },
    ]

    rows = []
    peak_rss = _rss_mb()
    for sc in scenarios:
        name = sc["name"]
        kw = sc["kw"]
        mode, vals = sc["tl"]
        print(f"\n=== Engine bench: {name} ===", flush=True)
        # Warm pandas year cache once
        p_kpi, _ = _run_pandas(year_val, mode, vals, **kw)

        p_cold: list[float] = []
        p_warm: list[float] = []
        d_cold: list[float] = []
        d_warm: list[float] = []
        d_kpi = None
        match = False

        # Cold duck: clear cache + force_refresh
        clear_analytics_query_cache()
        for i in range(ITERS):
            rss0 = _rss_mb()
            pk, pms = _run_pandas(year_val, mode, vals, **kw)
            rss1 = _rss_mb()
            peak_rss = max(peak_rss, rss0, rss1)
            if i < WARM_DROP:
                p_cold.append(pms)
            else:
                p_warm.append(pms)

        clear_analytics_query_cache()
        for i in range(ITERS):
            force = i < WARM_DROP
            if force:
                clear_analytics_query_cache()
            rss0 = _rss_mb()
            dk, dms, diag = _run_duck(year_val, mode, vals, force_refresh=force, **kw)
            rss1 = _rss_mb()
            peak_rss = max(peak_rss, rss0, rss1)
            d_kpi = dk
            if force:
                d_cold.append(dms)
            else:
                d_warm.append(dms)

        match = _match(p_kpi, d_kpi or {})
        p_all = sorted(p_warm or p_cold)
        d_all = sorted(d_warm or d_cold)
        p50_p, p95_p = _pct(p_all, 50), _pct(p_all, 95)
        p50_d, p95_d = _pct(d_all, 50), _pct(d_all, 95)
        max_p = round(max(p_all), 2) if p_all else 0
        max_d = round(max(d_all), 2) if d_all else 0
        speedup = round(p50_p / p50_d, 2) if p50_d > 0 else None
        row = {
            "api_scenario": sc["api"],
            "pandas_p50_ms": p50_p,
            "duckdb_p50_ms": p50_d,
            "pandas_p95_ms": p95_p,
            "duckdb_p95_ms": p95_d,
            "pandas_max_ms": max_p,
            "duckdb_max_ms": max_d,
            "pandas_cold_median_ms": _pct(sorted(p_cold), 50) if p_cold else None,
            "duckdb_cold_median_ms": _pct(sorted(d_cold), 50) if d_cold else None,
            "peak_rss_mb_process": round(peak_rss, 1),
            "speedup_p50": speedup,
            "result_match": match,
            "pandas_kpis": p_kpi,
            "duckdb_kpis": d_kpi,
            "dataset_version": (diag or {}).get("dataset_version") if d_kpi is not None else None,
        }
        print(
            f"  match={match} P50 pandas={p50_p} duck={p50_d} "
            f"P95 pandas={p95_p} duck={p95_d} speedup={speedup} peak_rss={peak_rss:.1f}MB"
        )
        rows.append(row)
        REPORT_ROWS.append(row)
    CHECKS["engine_peak_rss_mb"] = round(peak_rss, 1)
    return rows


def _http_get(path: str, params: dict, timeout: float = 180.0) -> tuple[dict | None, float, int]:
    qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None and v != ""})
    url = f"{BASE}{path}?{qs}"
    t0 = time.perf_counter()
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            status = resp.status
        ms = (time.perf_counter() - t0) * 1000
        data = json.loads(body.decode("utf-8"))
        return data, ms, status
    except Exception as exc:
        ms = (time.perf_counter() - t0) * 1000
        return {"ok": False, "error": str(exc)}, ms, 0


def api_endpoint_checks(samples: dict) -> list[dict]:
    """Hit live uvicorn (must be running with ANALYTICS_QUERY_ENGINE=duckdb)."""
    print("\n=== Live API endpoint engine checks ===")
    scenarios = [
        {
            "name": "sales_kpis_default",
            "path": "/api/sales-dashboard/kpis",
            "params": {
                "year": 2027,
                "timeline_mode": "fy_year",
                "timeline_values": "FY27",
                "refresh": 1,
            },
        },
        {
            "name": "sales_kpis_zone_site_season",
            "path": "/api/sales-dashboard/kpis",
            "params": {
                "year": 2027,
                "timeline_mode": "fy_year",
                "timeline_values": "FY27",
                "zones": "EAST",
                "site_ids": "314",
                "season": "SS-26",
                "refresh": 1,
            },
        },
        {
            "name": "sales_kpis_barcode",
            "path": "/api/sales-dashboard/kpis",
            "params": {
                "year": 2027,
                "timeline_mode": "fy_year",
                "timeline_values": "FY27",
                "barcodes": samples["barcode"],
                "refresh": 1,
            },
        },
        {
            "name": "sales_kpis_date_range",
            "path": "/api/sales-dashboard/kpis",
            "params": {
                "year": 2027,
                "timeline_mode": "date",
                "timeline_values": f"{samples['date_from']},{samples['date_to']}",
                "refresh": 1,
            },
        },
        {
            "name": "sales_insights_season",
            "path": "/api/sales-dashboard/insights",
            "params": {
                "year": 2027,
                "timeline_mode": "fy_year",
                "timeline_values": "FY27",
                "season": "SS-26",
                "refresh": 1,
            },
        },
        {
            "name": "exec_summary",
            "path": "/api/executive-overview/summary",
            "params": {
                "year": 2027,
                "timeline_mode": "fy_year",
                "timeline_values": "FY27",
                "zones": "EAST",
                "season": "SS-26",
            },
        },
        {
            "name": "exec_target",
            "path": "/api/executive-overview/target-vs-achievement",
            "params": {
                "year": 2027,
                "timeline_mode": "fy_year",
                "timeline_values": "FY27",
                "season": "SS-26",
                "group_by": "zone",
            },
        },
        {
            "name": "exec_breakdowns",
            "path": "/api/executive-overview/breakdowns",
            "params": {
                "year": 2027,
                "timeline_mode": "fy_year",
                "timeline_values": "FY27",
                "season": "SS-26",
            },
        },
    ]

    rows = []
    duck_used = 0
    duck_expected = 0
    for sc in scenarios:
        latencies = []
        last = None
        engine = None
        for i in range(5):
            data, ms, status = _http_get(sc["path"], sc["params"])
            latencies.append(ms)
            last = data
            if isinstance(data, dict):
                engine = data.get("query_engine") or engine
        latencies_s = sorted(latencies)
        exposes = "query_engine" in (last or {})
        expects_duck = sc["path"].startswith("/api/sales-dashboard/") or sc["name"] in (
            "exec_summary",
        )
        if expects_duck:
            duck_expected += 1
            if engine == "duckdb":
                duck_used += 1
        ok = bool(last) and (last.get("ok") is not False) and (
            last.get("error") is None or last.get("ok") is True
        )
        if expects_duck and engine != "duckdb":
            ok = False
        # Non-empty guard: default FY should not be blank zeros for net when unfiltered
        kpis = (last or {}).get("kpis") or {}
        blank_suspect = False
        if sc["name"] == "sales_kpis_default" and ok:
            net = float(kpis.get("net_sales") or kpis.get("net") or 0)
            if net <= 0:
                blank_suspect = True
                ok = False
        row = {
            "api_scenario": f"api:{sc['name']}",
            "pandas_p50_ms": None,
            "duckdb_p50_ms": _pct(latencies_s, 50),
            "pandas_p95_ms": None,
            "duckdb_p95_ms": _pct(latencies_s, 95),
            "pandas_max_ms": None,
            "duckdb_max_ms": round(max(latencies_s), 2),
            "peak_rss_mb_process": None,
            "speedup_p50": None,
            "result_match": ok and not blank_suspect,
            "query_engine": engine,
            "exposes_query_engine": exposes,
            "http_ok": ok,
            "blank_suspect": blank_suspect,
            "sample_kpis": {
                "net_sales": kpis.get("net_sales"),
                "units_sold": kpis.get("units_sold"),
                "current_soh": kpis.get("current_soh"),
                "yoy_sales_growth_pct": kpis.get("yoy_sales_growth_pct"),
            }
            if kpis
            else {"response_keys": list((last or {}).keys())[:20]},
        }
        print(
            f"  {sc['name']}: engine={engine} p50={row['duckdb_p50_ms']} "
            f"p95={row['duckdb_p95_ms']} ok={ok}"
        )
        rows.append(row)
        REPORT_ROWS.append(row)

    CHECKS["kpi_endpoints_duckdb"] = f"{duck_used}/{duck_expected}"
    CHECKS["kpi_duckdb_ok"] = duck_used == duck_expected and duck_expected > 0
    return rows


def _find_uvicorn_proc():
    """Attach to the uvicorn worker for STOCKPULSE_VALIDATE_BASE (highest RSS on port)."""
    try:
        import psutil
    except Exception:
        return None

    port = 5001
    try:
        port = int(urllib.parse.urlparse(BASE).port or 5001)
    except Exception:
        pass
    needle = f":{port}"
    best = None
    best_rss = -1.0
    try:
        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                cmd = " ".join(p.info.get("cmdline") or [])
                if "uvicorn" not in cmd:
                    continue
                if needle not in cmd and f"--port={port}" not in cmd and f"--port {port}" not in cmd:
                    continue
                proc = psutil.Process(p.info["pid"])
                rss = proc.memory_info().rss / (1024 * 1024)
                if rss > best_rss:
                    best_rss = rss
                    best = proc
            except Exception:
                continue
    except Exception:
        pass
    if best is not None:
        return best
    # Fallback: process listening on the port
    try:
        for c in psutil.net_connections(kind="inet"):
            if c.laddr and c.laddr.port == port and c.status == "LISTEN" and c.pid:
                return psutil.Process(c.pid)
    except Exception:
        pass
    return None


def concurrency_test(samples: dict) -> dict:
    print("\n=== Concurrency (8 parallel KPI requests) ===")
    params = {
        "year": 2027,
        "timeline_mode": "fy_year",
        "timeline_values": "FY27",
        "zones": "EAST",
        "season": "SS-26",
        "refresh": 1,
    }
    latencies = []
    engines = []
    errors = 0
    server = _find_uvicorn_proc()
    peak_server_rss = -1.0
    if server is not None:
        try:
            peak_server_rss = server.memory_info().rss / (1024 * 1024)
            server.cpu_percent(interval=None)
        except Exception:
            pass

    def one(_i: int):
        data, ms, status = _http_get("/api/sales-dashboard/kpis", params)
        return data, ms, status

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(one, i) for i in range(8)]
        for f in as_completed(futs):
            data, ms, status = f.result()
            latencies.append(ms)
            if server is not None:
                try:
                    peak_server_rss = max(peak_server_rss, server.memory_info().rss / (1024 * 1024))
                except Exception:
                    pass
            if not data or data.get("error") or status != 200:
                errors += 1
            else:
                engines.append(data.get("query_engine"))
    wall = (time.perf_counter() - t0) * 1000
    lat_s = sorted(latencies)
    server_cpu = -1.0
    if server is not None:
        try:
            server_cpu = float(server.cpu_percent(interval=0.5))
            peak_server_rss = max(peak_server_rss, server.memory_info().rss / (1024 * 1024))
        except Exception:
            pass
    result = {
        "workers": 8,
        "wall_ms": round(wall, 2),
        "p50_ms": _pct(lat_s, 50),
        "p95_ms": _pct(lat_s, 95),
        "max_ms": round(max(lat_s), 2) if lat_s else None,
        "errors": errors,
        "engines": engines,
        "all_duckdb": all(e == "duckdb" for e in engines) if engines else False,
        "server_pid": server.pid if server is not None else None,
        "server_cpu_percent": server_cpu,
        "server_peak_rss_mb": round(peak_server_rss, 1) if peak_server_rss >= 0 else None,
        "validator_rss_mb": round(_rss_mb(), 1),
    }
    print(f"  {result}")
    CHECKS["concurrency"] = result
    return result


def version_refresh_test() -> dict:
    """Touch FY parquet mtime and ensure dataset_version / cache key change without restart.

    Does not rebuild Sales-Master CSV — only bumps parquet mtime used in version fingerprint.
    """
    print("\n=== Dataset version refresh (no restart) ===", flush=True)
    from api.services.analytics_query_engine import (
        clear_analytics_query_cache,
        dataset_version_for_years,
        load_filtered_sales_duckdb,
        resolve_fy_parquet_paths,
    )

    paths = resolve_fy_parquet_paths(2027)
    if not paths:
        result = {"ok": False, "reason": "no fy parquet"}
        CHECKS["version_refresh"] = result
        return result
    v1 = dataset_version_for_years([2027])
    clear_analytics_query_cache()
    df1, diag1 = load_filtered_sales_duckdb(
        fy_years=[2027],
        timeline_mode="fy_year",
        timeline_values="FY27",
        season="SS-26",
        force_refresh=True,
    )
    # Bump parquet mtime only (simulate newly published analytical file)
    p = paths[0]
    now = time.time() + 5
    os.utime(p, (now, now))
    v2 = dataset_version_for_years([2027])
    clear_analytics_query_cache()
    df2, diag2 = load_filtered_sales_duckdb(
        fy_years=[2027],
        timeline_mode="fy_year",
        timeline_values="FY27",
        season="SS-26",
        force_refresh=True,
    )
    ok = (
        v1 != v2
        and diag1.get("dataset_version") != diag2.get("dataset_version")
        and int(len(df1)) == int(len(df2))
    )
    result = {
        "ok": ok,
        "version_before": v1,
        "version_after": v2,
        "diag_before": diag1.get("dataset_version"),
        "diag_after": diag2.get("dataset_version"),
        "rows_stable": int(len(df1)) == int(len(df2)),
        "path": str(p),
    }
    print(f"  {result}", flush=True)
    CHECKS["version_refresh"] = result
    return result


def missing_file_safety() -> dict:
    print("\n=== Missing / incomplete file safety ===")
    from api.services.analytics_query_engine import load_filtered_sales_duckdb

    raised = False
    err = ""
    try:
        load_filtered_sales_duckdb(
            fy_years=[2099],  # no parquet
            timeline_mode="fy_year",
            timeline_values="FY99",
            force_refresh=True,
        )
    except Exception as exc:
        raised = True
        err = type(exc).__name__ + ": " + str(exc)[:200]
    # Empty intentional filter should be empty rows, not fake network totals via engine alone
    from api.services.analytics_query_engine import clear_analytics_query_cache

    clear_analytics_query_cache()
    df, diag = load_filtered_sales_duckdb(
        fy_years=[2027],
        timeline_mode="fy_year",
        timeline_values="FY27",
        barcodes="__none__",
        force_refresh=True,
    )
    empty_ok = len(df) == 0 and float(df["net_sale_amount"].sum() if len(df) and "net_sale_amount" in df.columns else 0) == 0
    result = {
        "missing_fy_raises": raised,
        "missing_error": err,
        "empty_filter_zero_rows": empty_ok,
        "empty_rows": int(len(df)),
        "ok": raised and empty_ok,
    }
    print(f"  {result}")
    CHECKS["missing_file_safety"] = result
    return result


def soh_and_yoy_checks() -> dict:
    print("\n=== Scoped SOH + YoY via live KPI ===")
    data, ms, status = _http_get(
        "/api/sales-dashboard/kpis",
        {
            "year": 2027,
            "timeline_mode": "fy_year",
            "timeline_values": "FY27",
            "zones": "EAST",
            "season": "SS-26",
            "refresh": 1,
        },
    )
    k = (data or {}).get("kpis") or {}
    result = {
        "http_status": status,
        "query_engine": (data or {}).get("query_engine"),
        "net_sales": k.get("net_sales"),
        "current_soh": k.get("current_soh"),
        "sell_through_pct": k.get("sell_through_pct"),
        "yoy_sales_growth_pct": k.get("yoy_sales_growth_pct"),
        "yoy_qty_growth_pct": k.get("yoy_qty_growth_pct"),
        "latency_ms": round(ms, 2),
        "ok": status == 200
        and (data or {}).get("query_engine") == "duckdb"
        and float(k.get("net_sales") or 0) > 0
        and k.get("current_soh") is not None,
    }
    print(f"  {result}")
    CHECKS["soh_yoy"] = result
    return result


def manager_note() -> dict:
    # Sales dashboard has no manager query param; manager analytics are separate routes.
    from pathlib import Path as P

    routes = list((ROOT / "api" / "routes").glob("*manager*"))
    routes += list((ROOT / "api" / "routes").glob("*store*"))
    result = {
        "sales_dashboard_manager_filter": False,
        "note": "Manager filter not on /sales-dashboard; covered only if separate manager routes exist",
        "related_route_files": [p.name for p in routes],
        "status": "N/A — not in Sales Dashboard DuckDB path",
    }
    CHECKS["manager"] = result
    return result


def pipeline_unchanged_check() -> dict:
    print("\n=== Pipeline protection check ===")
    # Confirm DuckDB-related files do not include pipeline scheduler edits from this feature set
    duck_files = {
        "api/services/analytics_query_engine.py",
        "api/services/__init__.py",
        "api/routes/sales_dashboard.py",
        "api/routes/executive_overview.py",
        "api/config.py",
        "requirements.txt",
        "scripts/bench_sales_dashboard_engine.py",
        "scripts/validate_duckdb_optimization.py",
    }
    import subprocess

    try:
        out = subprocess.check_output(
            ["git", "diff", "--name-only", "--", "pipeline/"],
            cwd=str(ROOT),
            text=True,
            stderr=subprocess.STDOUT,
        )
        dirty = [l.strip() for l in out.splitlines() if l.strip()]
    except Exception as exc:
        dirty = [f"git_error:{exc}"]
    # daily_scheduler should not be in duck feature; report if dirty
    result = {
        "pipeline_dirty_files": dirty,
        "duckdb_feature_files": sorted(duck_files),
        "scheduler_path_exists": (ROOT / "pipeline" / "daily_scheduler.py").exists(),
        "auto_daily_run_in_dirty": "pipeline/auto_daily_run.py" in dirty,
        "daily_scheduler_in_dirty": "pipeline/daily_scheduler.py" in dirty,
        "note": (
            "Local git shows pre-existing pipeline dirty files; DuckDB work did not require "
            "editing scheduler. Do not deploy pipeline diffs with this feature."
        ),
        "ok_for_duckdb_scope": "pipeline/daily_scheduler.py" not in dirty,
    }
    print(f"  dirty={dirty}")
    CHECKS["pipeline"] = result
    return result


def pairwise_api_pandas_vs_duck_latency(samples: dict) -> list[dict]:
    """
    Compare live API with engine toggled is not possible on one server.
    Instead compare in-process context builders with env simulation via direct functions,
    and report API duck latencies already collected.
    For Pandas API baseline: call filter path only (already in REPORT_ROWS).
    """
    return []


def main() -> int:
    # Force line-buffered logs for long runs
    try:
        sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    except Exception:
        pass

    try:
        import psutil  # noqa: F401
    except ImportError:
        print("Installing psutil for CPU/RAM metrics...", flush=True)
        import subprocess

        subprocess.check_call([sys.executable, "-m", "pip", "install", "psutil", "-q"])

    from api.services.analytics_query_engine import duckdb_available

    if not duckdb_available():
        print("FAIL: duckdb missing", flush=True)
        return 1

    skip_bench = os.environ.get("STOCKPULSE_SKIP_BENCH_SUBPROCESS", "0") == "1"
    print("Sampling FY27 dimension values...", flush=True)
    try:
        samples = _sample_values()
    except Exception as exc:
        print(f"sample fallback due to: {exc}", flush=True)
        samples = {
            "barcode": "8900000000000",
            "department": "Blouses",
            "date_from": "2026-04-01",
            "date_to": "2026-04-30",
        }
    print(f"  samples={samples}", flush=True)
    CHECKS["samples"] = samples

    # 1) Existing bench script (optional if already run this session)
    import subprocess

    bench_rc = 0
    if skip_bench:
        print("Skipping bench subprocess (STOCKPULSE_SKIP_BENCH_SUBPROCESS=1)", flush=True)
        CHECKS["bench_script_exit"] = "skipped"
    else:
        print("\n=== Running scripts/bench_sales_dashboard_engine.py ===", flush=True)
        bench_rc = subprocess.call(
            [sys.executable, "-u", str(ROOT / "scripts" / "bench_sales_dashboard_engine.py")]
        )
        CHECKS["bench_script_exit"] = bench_rc

    # 2-4) Extended P50/P95 engine benches
    print("\n=== Extended P50/P95 engine benches ===", flush=True)
    bench_filter_scenarios(samples)

    # 5-6) Live API (requires duckdb-enabled uvicorn)
    api_endpoint_checks(samples)
    soh_and_yoy_checks()
    manager_note()

    # 7) Concurrency
    concurrency_test(samples)

    # 8) Version refresh
    version_refresh_test()

    # 9) Missing file
    missing_file_safety()

    # 10) Pipeline
    pipeline_unchanged_check()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "base_url": BASE,
        "iters": ITERS,
        "checks": CHECKS,
        "rows": REPORT_ROWS,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }

    def _json_safe(obj):
        if isinstance(obj, dict):
            return {str(k): _json_safe(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [_json_safe(x) for x in obj]
        if isinstance(obj, (str, int, float, bool)) or obj is None:
            return obj
        return str(obj)

    OUT.write_text(json.dumps(_json_safe(payload), indent=2), encoding="utf-8")
    print(f"\nWrote {OUT}", flush=True)

    # Fail if KPI endpoints not on duckdb or correctness mismatches
    mismatches = [
        r
        for r in REPORT_ROWS
        if r.get("result_match") is False and str(r.get("api_scenario", "")).startswith("filter:")
    ]
    kpi_ok = CHECKS.get("kpi_duckdb_ok")
    missing_ok = bool((CHECKS.get("missing_file_safety") or {}).get("ok"))
    version_ok = bool((CHECKS.get("version_refresh") or {}).get("ok"))
    if (bench_rc not in (0, "skipped") and bench_rc != 0) or mismatches or not kpi_ok:
        print(
            f"VALIDATION ISSUES: bench_rc={bench_rc} mismatches={len(mismatches)} "
            f"kpi_duckdb_ok={kpi_ok} missing_ok={missing_ok} version_ok={version_ok}",
            flush=True,
        )
        return 2
    if not missing_ok or not version_ok:
        print(
            f"VALIDATION WARNINGS: missing_ok={missing_ok} version_ok={version_ok}",
            flush=True,
        )
        return 3
    print("VALIDATION CORE OK", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

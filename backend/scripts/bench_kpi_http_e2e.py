"""
End-to-end HTTP KPI/Insights benchmark against a running uvicorn worker.

Measures P50/P95/max, attaches RSS/CPU to the real uvicorn PID (port match),
and compares DuckDB vs Pandas by restarting is NOT required — run twice with
different ANALYTICS_QUERY_ENGINE on the server, or pass --base twice.

Usage:
  # Server must already run with ANALYTICS_QUERY_ENGINE=duckdb (or pandas)
  python -u scripts/bench_kpi_http_e2e.py --base http://127.0.0.1:5001 --iters 5
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "api_cache" / "bench_kpi_http_e2e.json"


def _pct(vals: list[float], p: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    k = (len(s) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return round(s[f], 2)
    return round(s[f] + (s[c] - s[f]) * (k - f), 2)


def _find_uvicorn(port: int):
    try:
        import psutil
    except ImportError:
        return None
    needle = f":{port}"
    best = None
    best_rss = -1.0
    for p in psutil.process_iter(["pid", "cmdline", "memory_info"]):
        try:
            cmd = " ".join(p.info.get("cmdline") or [])
            if "uvicorn" in cmd and (needle in cmd or f"port {port}" in cmd or f"--port={port}" in cmd or f"--port {port}" in cmd):
                rss = p.memory_info().rss / (1024 * 1024)
                # Prefer the worker with highest RSS (actual app process)
                if rss > best_rss:
                    best_rss = rss
                    best = p
        except Exception:
            continue
    # Fallback: who listens on port
    if best is None:
        try:
            import psutil

            for c in psutil.net_connections(kind="inet"):
                if c.laddr and c.laddr.port == port and c.status == "LISTEN" and c.pid:
                    best = psutil.Process(c.pid)
                    break
        except Exception:
            pass
    return best


def _http(base: str, path: str, params: dict, timeout: float = 180.0):
    qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None and v != ""})
    url = f"{base}{path}?{qs}"
    t0 = time.perf_counter()
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
        status = resp.status
    ms = (time.perf_counter() - t0) * 1000.0
    data = json.loads(body.decode("utf-8"))
    return data, ms, status


SCENARIOS = [
    {"name": "default_fy", "path": "/api/sales-dashboard/kpis", "params": {"year": 2027, "timeline_mode": "fy_year", "timeline_values": "FY27"}},
    {"name": "fy_season", "path": "/api/sales-dashboard/kpis", "params": {"year": 2027, "timeline_mode": "fy_year", "timeline_values": "FY27", "season": "SS-26"}},
    {"name": "zone", "path": "/api/sales-dashboard/kpis", "params": {"year": 2027, "timeline_mode": "fy_year", "timeline_values": "FY27", "zones": "EAST"}},
    {"name": "zone_site", "path": "/api/sales-dashboard/kpis", "params": {"year": 2027, "timeline_mode": "fy_year", "timeline_values": "FY27", "zones": "EAST", "site_ids": "314"}},
    {"name": "zone_site_season", "path": "/api/sales-dashboard/kpis", "params": {"year": 2027, "timeline_mode": "fy_year", "timeline_values": "FY27", "zones": "EAST", "site_ids": "314", "season": "SS-26"}},
    {"name": "category", "path": "/api/sales-dashboard/kpis", "params": {"year": 2027, "timeline_mode": "fy_year", "timeline_values": "FY27", "departments": "Blouses"}},
    {"name": "barcode", "path": "/api/sales-dashboard/kpis", "params": {"year": 2027, "timeline_mode": "fy_year", "timeline_values": "FY27", "barcodes": "8907569465597"}},
    {"name": "date_range", "path": "/api/sales-dashboard/kpis", "params": {"year": 2027, "timeline_mode": "date", "timeline_values": "2026-04-01,2026-05-01"}},
    {"name": "insights_season", "path": "/api/sales-dashboard/insights", "params": {"year": 2027, "timeline_mode": "fy_year", "timeline_values": "FY27", "season": "SS-26"}},
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.environ.get("STOCKPULSE_VALIDATE_BASE", "http://127.0.0.1:5001"))
    ap.add_argument("--iters", type=int, default=5)
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument(
        "--refresh-mode",
        choices=("mixed", "always", "cached"),
        default="mixed",
        help="mixed=1 cold refresh=1 then warm refresh=0; always=all refresh=1 (uncached compute); cached=all refresh=0",
    )
    args = ap.parse_args()
    if not args.port:
        try:
            args.port = int(urllib.parse.urlparse(args.base).port or 5001)
        except Exception:
            args.port = 5001

    try:
        import psutil  # noqa: F401
    except ImportError:
        import subprocess

        subprocess.check_call([sys.executable, "-m", "pip", "install", "psutil", "-q"])

    worker = _find_uvicorn(args.port)
    print(
        f"base={args.base} port={args.port} worker_pid={getattr(worker, 'pid', None)} "
        f"mode={args.refresh_mode}",
        flush=True,
    )

    rows = []
    peak_rss = -1.0
    if worker is not None:
        try:
            peak_rss = worker.memory_info().rss / (1024 * 1024)
            worker.cpu_percent(interval=None)
        except Exception:
            pass

    for sc in SCENARIOS:
        cold_ms: list[float] = []
        warm_ms: list[float] = []
        refresh_ms: list[float] = []
        engine = None
        sample = None
        for i in range(args.iters):
            params = dict(sc["params"])
            if args.refresh_mode == "always":
                params["refresh"] = 1
            elif args.refresh_mode == "cached":
                params["refresh"] = 0
            else:
                params["refresh"] = 1 if i == 0 else 0
            data, ms, status = _http(args.base, sc["path"], params)
            if worker is not None:
                try:
                    peak_rss = max(peak_rss, worker.memory_info().rss / (1024 * 1024))
                except Exception:
                    pass
            if args.refresh_mode == "always":
                refresh_ms.append(ms)
                if i == 0:
                    cold_ms.append(ms)
                else:
                    warm_ms.append(ms)  # warm OS/DuckDB, still uncached response
            elif args.refresh_mode == "cached":
                warm_ms.append(ms)
            else:
                if i == 0:
                    cold_ms.append(ms)
                    refresh_ms.append(ms)
                else:
                    warm_ms.append(ms)
            engine = data.get("query_engine") or engine
            if sc["path"].endswith("/kpis"):
                k = data.get("kpis") or {}
                sample = {
                    "net_sales": k.get("net_sales"),
                    "units_sold": k.get("units_sold"),
                    "current_soh": k.get("current_soh"),
                    "yoy_sales_growth_pct": k.get("yoy_sales_growth_pct"),
                    "total": data.get("total"),
                    "golm_pct": k.get("golm_pct"),
                }
            else:
                sample = {
                    "total": data.get("total"),
                    "monthly": len((data.get("insights") or {}).get("monthly_trend") or []),
                }
            ok = status == 200 and data.get("ok") is not False
            if not ok:
                print(f"FAIL {sc['name']} status={status}", flush=True)
        warm_s = sorted(warm_ms) if warm_ms else sorted(cold_ms or refresh_ms)
        ref_s = sorted(refresh_ms) if refresh_ms else warm_s
        row = {
            "scenario": sc["name"],
            "path": sc["path"],
            "query_engine": engine,
            "refresh_mode": args.refresh_mode,
            "cold_ms": round(cold_ms[0], 2) if cold_ms else None,
            "refresh1_p50_ms": _pct(ref_s, 50) if refresh_ms else None,
            "refresh1_p95_ms": _pct(ref_s, 95) if refresh_ms else None,
            "warm_p50_ms": _pct(warm_s, 50),
            "warm_p95_ms": _pct(warm_s, 95),
            "warm_max_ms": round(max(warm_s), 2) if warm_s else None,
            "peak_rss_mb": round(peak_rss, 1) if peak_rss >= 0 else None,
            "sample": sample,
        }
        print(
            f"{sc['name']}: engine={engine} cold={row['cold_ms']} "
            f"refresh1_p50={row['refresh1_p50_ms']} refresh1_p95={row['refresh1_p95_ms']} "
            f"warm_p50={row['warm_p50_ms']} warm_p95={row['warm_p95_ms']} "
            f"rss={row['peak_rss_mb']} sample={sample}",
            flush=True,
        )
        rows.append(row)

    cpu = -1.0
    if worker is not None:
        try:
            cpu = float(worker.cpu_percent(interval=0.5))
            peak_rss = max(peak_rss, worker.memory_info().rss / (1024 * 1024))
        except Exception:
            pass

    # correctness spot-check vs known EAST+SS-26 net from prior validation
    zss = next((r for r in rows if r["scenario"] == "zone_site_season"), None)
    match_note = None
    if zss and zss.get("sample"):
        net = float(zss["sample"].get("net_sales") or 0)
        match_note = abs(net - 2075075.61) < 1.0  # site 314 + EAST + SS-26

    payload = {
        "base": args.base,
        "worker_pid": getattr(worker, "pid", None),
        "peak_rss_mb": round(peak_rss, 1) if peak_rss >= 0 else None,
        "cpu_percent_after": cpu,
        "zone_site_season_net_match": match_note,
        "rows": rows,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

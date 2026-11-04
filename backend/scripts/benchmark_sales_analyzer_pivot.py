#!/usr/bin/env python3
"""Phase A baseline / Phase F after benchmarks for Sales Analyzer pivots.

Runs against local API (default http://127.0.0.1:5001). Does not touch the pipeline.

Usage:
  python scripts/benchmark_sales_analyzer_pivot.py
  python scripts/benchmark_sales_analyzer_pivot.py --base http://127.0.0.1:5001 --samples 2 --out data/api_cache/analyzer_pivot_bench.json
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


SCENARIOS: list[dict[str, Any]] = [
    {
        "name": "manager_only",
        "body": {
            "rows": ["manager"],
            "columns": [],
            "values": [
                {"field": "sales", "aggregation": "sum"},
                {"field": "quantity", "aggregation": "sum"},
                {"field": "current_soh", "aggregation": "sum"},
                {"field": "sell_through_pct", "aggregation": "calculated"},
            ],
            "filters": {},
            "year": 2027,
        },
    },
    {
        "name": "size_only",
        "body": {
            "rows": ["size"],
            "columns": [],
            "values": [
                {"field": "sales", "aggregation": "sum"},
                {"field": "quantity", "aggregation": "sum"},
                {"field": "current_soh", "aggregation": "sum"},
            ],
            "filters": {},
            "year": 2027,
        },
    },
    {
        "name": "manager_store_size",
        "body": {
            "rows": ["manager", "site_id_name", "size"],
            "columns": [],
            "values": [
                {"field": "sales", "aggregation": "sum"},
                {"field": "quantity", "aggregation": "sum"},
                {"field": "current_soh", "aggregation": "sum"},
                {"field": "sell_through_pct", "aggregation": "calculated"},
            ],
            "filters": {},
            "year": 2027,
        },
    },
    {
        "name": "zone_store_size",
        "body": {
            "rows": ["zone", "site_id_name", "size"],
            "columns": [],
            "values": [
                {"field": "sales", "aggregation": "sum"},
                {"field": "quantity", "aggregation": "sum"},
                {"field": "current_soh", "aggregation": "sum"},
            ],
            "filters": {},
            "year": 2027,
        },
    },
    {
        "name": "dept_section_size",
        "body": {
            "rows": ["department", "section", "size"],
            "columns": [],
            "values": [
                {"field": "sales", "aggregation": "sum"},
                {"field": "quantity", "aggregation": "sum"},
                {"field": "current_soh", "aggregation": "sum"},
            ],
            "filters": {},
            "year": 2027,
        },
    },
    {
        "name": "season_barcode",
        "body": {
            "rows": ["season", "barcode"],
            "columns": [],
            "values": [
                {"field": "sales", "aggregation": "sum"},
                {"field": "quantity", "aggregation": "sum"},
                {"field": "current_soh", "aggregation": "sum"},
            ],
            "filters": {},
            "year": 2027,
        },
    },
    {
        "name": "manager_store_size_x_season",
        "body": {
            "rows": ["manager", "site_id_name", "size"],
            "columns": ["season"],
            "values": [
                {"field": "sales", "aggregation": "sum"},
                {"field": "quantity", "aggregation": "sum"},
                {"field": "current_soh", "aggregation": "sum"},
                {"field": "sell_through_pct", "aggregation": "calculated"},
            ],
            "filters": {},
            "year": 2027,
        },
    },
]


def _post_json(url: str, body: dict[str, Any], timeout: float) -> tuple[dict[str, Any], float, int]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        elapsed = time.perf_counter() - t0
        payload = json.loads(raw.decode("utf-8"))
        return payload, elapsed, len(raw)


def _count_nodes(nodes: list[Any] | None) -> int:
    if not nodes:
        return 0
    n = 0
    for node in nodes:
        n += 1
        kids = node.get("children") if isinstance(node, dict) else None
        n += _count_nodes(kids)
    return n


def run_scenario(base: str, scenario: dict[str, Any], samples: int, timeout: float) -> dict[str, Any]:
    url = f"{base.rstrip('/')}/api/sales-analyzer/query"
    times: list[float] = []
    sizes: list[int] = []
    last: dict[str, Any] | None = None
    err: str | None = None
    for i in range(samples):
        try:
            payload, elapsed, nbytes = _post_json(url, scenario["body"], timeout)
            times.append(elapsed)
            sizes.append(nbytes)
            last = payload
            print(f"  sample {i+1}/{samples}: {elapsed:.2f}s  bytes={nbytes:,}", flush=True)
        except Exception as exc:  # noqa: BLE001
            err = str(exc)
            print(f"  sample {i+1}/{samples}: FAIL {exc}", flush=True)
            break

    metrics = ((last or {}).get("grand_total") or {}).get("metrics") or {}
    rows = (last or {}).get("rows") or []
    out: dict[str, Any] = {
        "name": scenario["name"],
        "ok": err is None and bool(times),
        "error": err,
        "samples": len(times),
        "seconds": {
            "all": [round(t, 3) for t in times],
            "p50": round(statistics.median(times), 3) if times else None,
            "p95": round(sorted(times)[max(0, int(len(times) * 0.95) - 1)], 3) if times else None,
            "mean": round(statistics.mean(times), 3) if times else None,
            "min": round(min(times), 3) if times else None,
            "max": round(max(times), 3) if times else None,
        },
        "response_bytes": {
            "all": sizes,
            "mean": int(statistics.mean(sizes)) if sizes else None,
            "max": max(sizes) if sizes else None,
        },
        "tree_nodes": _count_nodes(rows),
        "top_rows": len(rows),
        "grand_total": {
            "sales": metrics.get("sales"),
            "quantity": metrics.get("quantity"),
            "current_soh": metrics.get("current_soh"),
            "sell_through_pct": metrics.get("sell_through_pct"),
        },
        "engine_hint": (last or {}).get("query_engine") or (last or {}).get("engine"),
    }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:5001")
    ap.add_argument("--samples", type=int, default=2)
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument(
        "--out",
        default="data/api_cache/analyzer_pivot_bench_baseline.json",
    )
    ap.add_argument("--only", default="", help="Comma-separated scenario names")
    args = ap.parse_args()

    only = {x.strip() for x in args.only.split(",") if x.strip()}
    scenarios = [s for s in SCENARIOS if not only or s["name"] in only]

    # Warm metadata
    try:
        urllib.request.urlopen(f"{args.base.rstrip('/')}/api/sales-analyzer/metadata", timeout=30).read()
    except Exception as exc:  # noqa: BLE001
        print(f"API not reachable at {args.base}: {exc}", file=sys.stderr)
        return 2

    results: list[dict[str, Any]] = []
    for sc in scenarios:
        print(f"\n=== {sc['name']} ===", flush=True)
        results.append(run_scenario(args.base, sc, args.samples, args.timeout))

    payload = {
        "base": args.base,
        "samples": args.samples,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "phase": "A_baseline",
        "results": results,
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nWrote {out_path}")

    print("\nSUMMARY")
    for r in results:
        sec = (r.get("seconds") or {}).get("mean")
        soh = ((r.get("grand_total") or {}).get("current_soh"))
        st = ((r.get("grand_total") or {}).get("sell_through_pct"))
        status = "OK" if r.get("ok") else "FAIL"
        print(
            f"  {r['name']:<32} {status:<4} mean={sec}s  nodes={r.get('tree_nodes')}  "
            f"bytes={((r.get('response_bytes') or {}).get('mean'))}  soh={soh}  str={st}"
        )
    return 0 if all(r.get("ok") for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Compare analyzer /query trees: main baseline vs Option B feat branch."""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from typing import Any

MAIN = "http://127.0.0.1:5002/api"
FEAT = "http://127.0.0.1:5001/api"


def post(base: str, payload: dict) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        base + "/sales-analyzer/query",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {"ok": False, "http_status": e.code, "error": e.read().decode(errors="replace")[:500]}


def _round_num(v: Any, nd: int = 4) -> Any:
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, (int, float)):
        try:
            f = float(v)
            if f != f:  # NaN
                return None
            return round(f, nd)
        except Exception:
            return v
    return v


def _norm_metrics(m: dict | None) -> dict:
    out = {}
    for k, v in sorted((m or {}).items()):
        out[k] = _round_num(v)
    return out


def flatten_tree(nodes: list | None, path: tuple = ()) -> dict[str, dict]:
    """key -> metrics dict for every node."""
    out: dict[str, dict] = {}
    for n in nodes or []:
        dim = n.get("dimension")
        val = n.get("value")
        p = path + (f"{dim}={val}",)
        key = " / ".join(p)
        out[key] = _norm_metrics((n.get("metrics") or {}))
        # split metrics
        for sk, sm in sorted((n.get("split_metrics") or {}).items()):
            out[f"{key} || split:{sk}"] = _norm_metrics(sm)
        out.update(flatten_tree(n.get("children") or [], p))
    return out


def compare_payloads(name: str, a: dict, b: dict, measure_keys: list[str]) -> list[str]:
    diffs: list[str] = []
    if a.get("ok") != b.get("ok"):
        diffs.append(f"ok: main={a.get('ok')} feat={b.get('ok')}")
    ga = _norm_metrics(((a.get("grand_total") or {}).get("metrics") or {}))
    gb = _norm_metrics(((b.get("grand_total") or {}).get("metrics") or {}))
    for k in measure_keys:
        if ga.get(k) != gb.get(k):
            diffs.append(f"grand_total.{k}: main={ga.get(k)} feat={gb.get(k)}")
    ta = flatten_tree(a.get("rows") or [])
    tb = flatten_tree(b.get("rows") or [])
    keys = sorted(set(ta) | set(tb))
    only_main = [k for k in keys if k not in tb]
    only_feat = [k for k in keys if k not in ta]
    for k in only_main[:50]:
        diffs.append(f"node only in main: {k}")
    for k in only_feat[:50]:
        diffs.append(f"node only in feat: {k}")
    if len(only_main) > 50:
        diffs.append(f"... +{len(only_main) - 50} more only-main nodes")
    if len(only_feat) > 50:
        diffs.append(f"... +{len(only_feat) - 50} more only-feat nodes")
    shared = [k for k in keys if k in ta and k in tb]
    for k in shared:
        ma, mb = ta[k], tb[k]
        for mk in measure_keys:
            if ma.get(mk) != mb.get(mk):
                diffs.append(f"{k} :: {mk}: main={ma.get(mk)} feat={mb.get(mk)}")
                if len(diffs) > 200:
                    diffs.append("... truncated at 200 diffs")
                    print(f"\n=== {name}: DIFFER ({len(diffs)}+ listed) ===")
                    for d in diffs:
                        print(" -", d)
                    return diffs
    # also compare total_records
    if a.get("total_records") != b.get("total_records"):
        diffs.append(
            f"total_records: main={a.get('total_records')} feat={b.get('total_records')}"
        )
    if not diffs:
        print(f"\n=== {name}: IDENTICAL ===")
        print(
            f"  nodes={len(shared)} grand={[ga.get(k) for k in measure_keys]} "
            f"records={a.get('total_records')}"
        )
    else:
        print(f"\n=== {name}: DIFFER ({len(diffs)} diffs) ===")
        for d in diffs[:100]:
            print(" -", d)
        if len(diffs) > 100:
            print(f" - ... +{len(diffs) - 100} more")
    return diffs


def main() -> int:
    cases = [
        (
            "a) Manager>Store>Size x Season FY26 East",
            {
                "rows": ["manager", "site_id_name", "size"],
                "columns": ["season"],
                "values": [
                    {"field": "sales", "aggregation": "sum"},
                    {"field": "quantity", "aggregation": "sum"},
                    {"field": "current_soh", "aggregation": "sum"},
                    {"field": "sell_through_pct", "aggregation": "calculated"},
                ],
                "filters": {
                    "timeline_mode": "fy_year",
                    "timeline_values": "FY26",
                    "zone": ["East"],
                },
                "sort": [{"field": "sales", "direction": "desc"}],
                "year": 2026,
            },
            ["sales", "quantity", "current_soh", "sell_through_pct"],
        ),
        (
            "b) Zone FY27 sales/LY/growth",
            {
                "rows": ["zone"],
                "columns": [],
                "values": [
                    {"field": "sales", "aggregation": "sum"},
                    {"field": "sales_ly", "aggregation": "sum"},
                    {"field": "sales_growth_pct", "aggregation": "calculated"},
                ],
                "filters": {"timeline_mode": "fy_year", "timeline_values": "FY27"},
                "sort": [{"field": "sales", "direction": "desc"}],
                "year": 2027,
            },
            ["sales", "sales_ly", "sales_growth_pct"],
        ),
        (
            "c) Zone FY26 sales/LY/growth",
            {
                "rows": ["zone"],
                "columns": [],
                "values": [
                    {"field": "sales", "aggregation": "sum"},
                    {"field": "sales_ly", "aggregation": "sum"},
                    {"field": "sales_growth_pct", "aggregation": "calculated"},
                ],
                "filters": {"timeline_mode": "fy_year", "timeline_values": "FY26"},
                "sort": [{"field": "sales", "direction": "desc"}],
                "year": 2026,
            },
            ["sales", "sales_ly", "sales_growth_pct"],
        ),
    ]

    all_identical = True
    for name, payload, measures in cases:
        print(f"\nRequesting {name} ...")
        main_p = post(MAIN, payload)
        feat_p = post(FEAT, payload)
        print(
            f"  main ok={main_p.get('ok')} records={main_p.get('total_records')} "
            f"http={main_p.get('http_status')}"
        )
        print(
            f"  feat ok={feat_p.get('ok')} records={feat_p.get('total_records')} "
            f"req={feat_p.get('requested_year')} eff={feat_p.get('effective_year')} "
            f"http={feat_p.get('http_status')}"
        )
        diffs = compare_payloads(name, main_p, feat_p, measures)
        if diffs:
            all_identical = False

    print("\n==== SUMMARY ====")
    print("ALL_IDENTICAL" if all_identical else "HAS_DIFFERENCES")
    return 0 if all_identical else 1


if __name__ == "__main__":
    sys.exit(main())

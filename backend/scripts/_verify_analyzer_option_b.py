"""Local single-request checks for Analyzer Option B FY scope."""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

BASE = "http://127.0.0.1:5001/api"


def get(path: str):
    with urllib.request.urlopen(BASE + path, timeout=120) as r:
        return json.loads(r.read().decode())


def post(path: str, payload: dict):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        BASE + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        return e.code, {"ok": False, "error": body[:800]}


def main() -> None:
    vals = [
        {"field": "sales", "aggregation": "sum"},
        {"field": "sales_ly", "aggregation": "sum"},
        {"field": "sales_growth_pct", "aggregation": "calculated"},
        {"field": "quantity", "aggregation": "sum"},
        {"field": "current_soh", "aggregation": "sum"},
        {"field": "sell_through_pct", "aggregation": "calculated"},
    ]

    print("=== filter-options year list (Analyzer) ===")
    fo = get("/sales-analyzer/filter-options?year=2027")
    print("years", (fo.get("options") or {}).get("year"))
    print("fy", (fo.get("options") or {}).get("financial_year"))

    print("\n=== e) Home/Overview year lists (must still include FY25) ===")
    try:
        cal = get("/sales-dashboard/financial-calendar")
        years = [y.get("year") for y in (cal.get("years") or [])]
        print("financial-calendar years", years)
        print("includes_2025", 2025 in years)
    except Exception as e:
        print("calendar FAIL", e)

    from api.sales_year_loader import dashboard_fy_years_sorted, warm_fy_years_sorted

    print("dashboard_fy_years_sorted", dashboard_fy_years_sorted())
    print("warm_fy_years_sorted", warm_fy_years_sorted())

    print("\n=== a) FY27 default ===")
    st, p27 = post(
        "/sales-analyzer/query",
        {
            "rows": ["zone"],
            "columns": [],
            "values": vals,
            "filters": {"timeline_mode": "fy_year", "timeline_values": "FY27"},
            "sort": [{"field": "sales", "direction": "desc"}],
            "year": 2027,
        },
    )
    gt27 = ((p27.get("grand_total") or {}).get("metrics") or {})
    print(
        "status",
        st,
        "ok",
        p27.get("ok"),
        "req",
        p27.get("requested_year"),
        "eff",
        p27.get("effective_year"),
        "fallback",
        p27.get("year_fallback"),
    )
    print(
        "sales",
        gt27.get("sales"),
        "sales_ly",
        gt27.get("sales_ly"),
        "qty",
        gt27.get("quantity"),
        "soh",
        gt27.get("current_soh"),
    )

    print("\n=== b) FY26 LY/growth ===")
    st, p26 = post(
        "/sales-analyzer/query",
        {
            "rows": ["zone"],
            "columns": [],
            "values": vals,
            "filters": {"timeline_mode": "fy_year", "timeline_values": "FY26"},
            "sort": [{"field": "sales", "direction": "desc"}],
            "year": 2026,
        },
    )
    gt26 = ((p26.get("grand_total") or {}).get("metrics") or {})
    print(
        "status",
        st,
        "ok",
        p26.get("ok"),
        "req",
        p26.get("requested_year"),
        "eff",
        p26.get("effective_year"),
    )
    print(
        "sales",
        gt26.get("sales"),
        "sales_ly",
        gt26.get("sales_ly"),
        "growth",
        gt26.get("sales_growth_pct"),
    )
    try:
        ly_n = float(gt26.get("sales_ly") or 0)
    except (TypeError, ValueError):
        ly_n = 0.0
    print("LY_REAL", ly_n > 0)

    print("\n=== c) FY26 East Manager>Store>Size x Season ===")
    st, p_c = post(
        "/sales-analyzer/query",
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
    )
    gtc = ((p_c.get("grand_total") or {}).get("metrics") or {})
    print("status", st, "ok", p_c.get("ok"), "records", p_c.get("total_records"))
    print(
        "metrics",
        {k: gtc.get(k) for k in ("sales", "quantity", "current_soh", "sell_through_pct")},
    )
    print(
        "BEFORE_AFTER_IDENTITY: NOT VERIFIED "
        "(no pre-change baseline captured in this session)"
    )

    print("\n=== d) year=2025 and year=0 no 500 ===")
    for y in (2025, 0):
        st, p = post(
            "/sales-analyzer/query",
            {
                "rows": ["zone"],
                "columns": [],
                "values": [{"field": "sales", "aggregation": "sum"}],
                "filters": {},
                "year": y,
            },
        )
        print(
            f"year={y}",
            "status",
            st,
            "ok",
            p.get("ok"),
            "req",
            p.get("requested_year"),
            "eff",
            p.get("effective_year"),
            "fallback",
            p.get("year_fallback"),
            "sales",
            ((p.get("grand_total") or {}).get("metrics") or {}).get("sales"),
            "err",
            (p.get("error") or "")[:120],
        )

    print("\nDONE")


if __name__ == "__main__":
    main()

"""One-filter-at-a-time validation for Home + Sales Overview (lean)."""
from __future__ import annotations

import json
import sys
import time
import urllib.parse
import urllib.request
from typing import Any

BASE = "http://127.0.0.1:5001/api"
YEAR = 2027
TIMEOUT = 240


def get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    q = {k: v for k, v in (params or {}).items() if v is not None and str(v) != ""}
    q.setdefault("year", YEAR)
    if "timeline_mode" not in q:
        q["timeline_mode"] = "fy_year"
        q["timeline_values"] = "FY27"
    url = f"{BASE}{path}?{urllib.parse.urlencode(q)}"
    t0 = time.time()
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
            data = json.load(r)
        return {"_http": True, "_ms": int((time.time() - t0) * 1000), **data}
    except Exception as exc:
        return {"_http": False, "_ms": int((time.time() - t0) * 1000), "_error": str(exc), "_url": url}


def num(d: Any, *keys: str) -> float:
    if not isinstance(d, dict):
        return 0.0
    for k in keys:
        if d.get(k) is not None:
            try:
                return float(d[k])
            except (TypeError, ValueError):
                pass
    return 0.0


def site_ids_for(sites: list[dict], *, limit: int = 40, **match: str) -> str:
    keys: list[str] = []
    for s in sites:
        if all(str(s.get(k) or "").strip().upper() == str(v).strip().upper() for k, v in match.items()):
            code = str(s.get("site_code") or s.get("site_key") or "").strip()
            if code:
                keys.append(code)
        if len(keys) >= limit:
            break
    return ",".join(keys)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    print("CM filters...", flush=True)
    cm = get("/sales-dashboard/customer-master-filters", {})
    if not cm.get("_http"):
        print("FAIL CM:", cm.get("_error"))
        return 1
    opts = cm.get("options") or {}
    sites = cm.get("sites") or []
    print(f"sites={len(sites)} zones={opts.get('zones')}", flush=True)

    print("Season options...", flush=True)
    base_rows = get("/sales-dashboard/rows", {"page": 1, "page_size": 5})
    seasons = [
        (s.get("id") if isinstance(s, dict) else s)
        for s in (base_rows.get("seasons") or [])
    ]
    seasons = [str(s) for s in seasons if s and str(s).lower() != "all"]
    season = next((s for s in ("SS-26", "AW-25", "SS-25", "AW-26") if s in seasons), None)
    if not season:
        season = next((s for s in seasons if str(s).upper().startswith(("SS-", "AW-"))), "SS-26")

    # departments from unfiltered insights (real sales depts)
    print("Insights for dept sample...", flush=True)
    base_ins = get("/sales-dashboard/insights", {})
    depts = [
        str(x.get("name"))
        for x in ((base_ins.get("insights") or {}).get("top_departments") or [])
        if x.get("name")
    ]

    print("Baseline...", flush=True)
    home0 = get("/executive-overview/summary", {})
    sales0 = get("/sales-dashboard/kpis", {})
    h0_net, h0_u = num(home0, "net_sales"), num(home0, "units_sold")
    s0k = sales0.get("kpis") or {}
    s0_net, s0_u = num(s0k, "net_sales"), num(s0k, "units_sold")
    print(f"BASE HOME {h0_net/1e7:.2f}Cr / {h0_u:.0f}u | SALES {s0_net/1e7:.2f}Cr / {s0_u:.0f}u", flush=True)

    zone = "North" if "North" in (opts.get("zones") or []) else (opts.get("zones") or ["North"])[0]
    channel = (opts.get("salesChannels") or ["PT"])[0]
    grade = next((g for g in (opts.get("storeGrades") or []) if g in ("A", "A+", "B")), "A")
    state = next((s for s in (opts.get("states") or []) if s in ("Delhi", "Maharashtra", "Gujarat")), "Delhi")
    city = next((c for c in (opts.get("cities") or []) if c in ("DELHI", "MUMBAI", "AHMEDABAD", "PUNE")), "DELHI")
    north = [s for s in sites if str(s.get("zone") or "").upper() == "NORTH"]
    store = str((north[0] if north else sites[0]).get("site_code") or "")

    cases: list[tuple[str, dict]] = [
        ("zone", {"zones": zone, "site_ids": site_ids_for(sites, zone=zone)}),
        ("channel", {"channel": channel}),
        ("store_grade", {"store_grades": grade, "site_ids": site_ids_for(sites, store_grade=grade)}),
        ("state", {"states": state, "site_ids": site_ids_for(sites, state=state)}),
        ("city", {"cities": city, "site_ids": site_ids_for(sites, city=city)}),
        ("season", {"season": season}),
        ("store", {"site_ids": store}),
        ("timeline_month", {"timeline_mode": "month", "timeline_values": "2026-07"}),
    ]
    if depts:
        cases.append(("department", {"departments": depts[0]}))

    issues: list[str] = []
    results: list[dict] = []

    for label, params in cases:
        print(f"\n--- {label}: {params} ---", flush=True)
        home = get("/executive-overview/summary", params)
        bd = get("/executive-overview/breakdowns", params)
        sales = get("/sales-dashboard/kpis", params)
        rows = get("/sales-dashboard/rows", {**params, "page": 1, "page_size": 15})

        if not home.get("_http") or not home.get("ok"):
            issues.append(f"{label}/HOME summary FAIL {home.get('_error')}")
            print("  HOME FAIL", home.get("_error"), flush=True)
            continue
        if not sales.get("_http") or not sales.get("ok"):
            issues.append(f"{label}/SALES kpis FAIL {sales.get('_error')}")
            print("  SALES FAIL", sales.get("_error"), flush=True)
            continue

        sk = sales.get("kpis") or {}
        h_net, h_u = num(home, "net_sales"), num(home, "units_sold")
        s_net, s_u = num(sk, "net_sales"), num(sk, "units_sold")
        h_soh, s_soh = num(home, "total_soh"), num(sk, "current_soh")
        h_asp, s_asp = num(home.get("sales_kpis") or {}, "asp"), num(sk, "asp")
        sample = rows.get("rows") or [] if rows.get("_http") else []

        print(
            f"  HOME  net={h_net/1e7:.2f}Cr units={h_u:.0f} soh={h_soh:.0f} asp={h_asp:.1f} ms={home.get('_ms')}",
            flush=True,
        )
        print(
            f"  SALES net={s_net/1e7:.2f}Cr units={s_u:.0f} soh={s_soh:.0f} asp={s_asp:.1f} rows={len(sample)}/{rows.get('total')} ms={sales.get('_ms')}/{rows.get('_ms')}",
            flush=True,
        )

        # Must shrink vs baseline (except tiny float)
        if h0_u > 0 and h_u > h0_u * 1.02:
            issues.append(f"{label}/HOME units > baseline")
        if s0_u > 0 and s_u > s0_u * 1.02:
            issues.append(f"{label}/SALES units > baseline")
        if h0_net > 0 and h_net > h0_net * 1.02:
            issues.append(f"{label}/HOME net > baseline")
        if s0_net > 0 and s_net > s0_net * 1.02:
            issues.append(f"{label}/SALES net > baseline")

        # Home/Sales parity
        if h_net > 0 and s_net > 0:
            drift = abs(h_net - s_net) / max(h_net, s_net)
            print(f"  CROSS net drift={drift*100:.1f}% units H={h_u:.0f} S={s_u:.0f}", flush=True)
            if drift > 0.08:
                issues.append(f"{label}/CROSS Home-Sales net drift {drift*100:.1f}%")
        if h_u > 0 and s_u > 0 and abs(h_u - s_u) / max(h_u, s_u) > 0.08:
            issues.append(f"{label}/CROSS Home-Sales units drift")

        # Row integrity
        if label == "zone" and sample:
            bad = [r for r in sample if str(r.get("zone") or "").strip().upper() not in {zone.upper(), ""}]
            bad = [r for r in bad if str(r.get("zone") or "").strip()]
            if bad:
                issues.append(f"zone/SALES row zone={bad[0].get('zone')}")
        if label == "channel" and sample:
            bad = [
                r
                for r in sample
                if str(r.get("sales_channel") or r.get("channel") or "").strip().upper() not in {channel.upper(), ""}
                and str(r.get("sales_channel") or r.get("channel") or "").strip()
            ]
            if bad:
                issues.append(
                    f"channel/SALES row ch={bad[0].get('sales_channel') or bad[0].get('channel')}"
                )
        if label == "season" and sample:
            bad = [
                r
                for r in sample
                if str(r.get("season") or "").strip().upper() != season.upper()
                and str(r.get("season") or "").strip()
            ]
            if bad:
                issues.append(f"season/SALES row season={bad[0].get('season')}")
            if s0_u > 0 and s_u >= s0_u * 0.999:
                issues.append("season/SALES did not shrink units")
        if label == "state" and sample:
            bad = [
                r
                for r in sample
                if str(r.get("state") or "").strip().upper() != state.upper()
                and str(r.get("state") or "").strip()
            ]
            if bad:
                issues.append(f"state/SALES row state={bad[0].get('state')}")
        if label == "city" and sample:
            bad = [
                r
                for r in sample
                if str(r.get("city") or "").strip().upper() != city.upper()
                and str(r.get("city") or "").strip()
            ]
            if bad:
                issues.append(f"city/SALES row city={bad[0].get('city')}")
        if label == "store_grade" and sample:
            bad = [
                r
                for r in sample
                if str(r.get("store_grade") or "").strip().upper() != grade.upper()
                and str(r.get("store_grade") or "").strip()
            ]
            if bad:
                issues.append(f"grade/SALES row grade={bad[0].get('store_grade')}")
        if label == "store" and sample:
            blob_ok = any(store.upper() in " ".join(str(r.get(c) or "") for c in ("site_id", "site_name", "site_short_code")).upper() for r in sample)
            if not blob_ok:
                issues.append(f"store/SALES rows missing site {store}")

        # Home breakdown sanity
        if bd.get("_http") and bd.get("ok"):
            if label == "zone":
                zs = bd.get("zone_sales") or []
                foreign = [z for z in zs if str(z.get("zone") or "").upper() not in {zone.upper(), ""} and num(z, "sales", "value") > 0]
                if foreign:
                    issues.append(f"zone/HOME zone_sales foreign={foreign[0].get('zone')}")
            if label == "channel":
                cs = bd.get("channel_sales") or []
                foreign = [
                    c
                    for c in cs
                    if str(c.get("name") or "").upper() not in {channel.upper()}
                    and num(c, "value", "sales") > 0
                ]
                if foreign:
                    issues.append(f"channel/HOME channel_sales foreign={foreign[0].get('name')}")

        results.append(
            {
                "filter": label,
                "params": params,
                "home_net_cr": round(h_net / 1e7, 2),
                "sales_net_cr": round(s_net / 1e7, 2),
                "home_units": int(h_u),
                "sales_units": int(s_u),
                "home_soh": int(h_soh),
                "sales_soh": int(s_soh),
                "ok": True,
            }
        )

    print("\n========== SUMMARY ==========", flush=True)
    for r in results:
        print(
            f"  {r['filter']}: Home {r['home_net_cr']}Cr/{r['home_units']}u soh={r['home_soh']} | "
            f"Sales {r['sales_net_cr']}Cr/{r['sales_units']}u soh={r['sales_soh']}",
            flush=True,
        )
    if issues:
        print(f"\nFAILS ({len(issues)}):", flush=True)
        for i in issues:
            print(" -", i, flush=True)
        return 1
    print("\nALL FILTER CHECKS PASSED", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

import json
import re
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:5001/api"


def get(path, **q):
    q.setdefault("year", 2027)
    q.setdefault("timeline_mode", "fy_year")
    q.setdefault("timeline_values", "FY27")
    url = f"{BASE}{path}?{urllib.parse.urlencode(q)}"
    with urllib.request.urlopen(url, timeout=240) as r:
        return json.load(r)


rows = get("/sales-dashboard/rows", page=1, page_size=8)
print("unfiltered sample:")
for r in rows.get("rows") or []:
    print(
        " ",
        r.get("site_id"),
        "|",
        r.get("site_name"),
        "|",
        r.get("zone"),
        "|",
        r.get("sales_channel") or r.get("channel"),
    )

zr = get(
    "/sales-dashboard/rows",
    page=1,
    page_size=8,
    zones="North",
    site_ids="120,121,125,P007,P008,P020",
)
print("\nnorth scoped sample:")
for r in zr.get("rows") or []:
    print(" ", r.get("site_id"), r.get("site_name"), "qty", r.get("qty"))

cands = []
for r in (zr.get("rows") or []) + (rows.get("rows") or []):
    sid = str(r.get("site_id") or "")
    for c in re.findall(r"\(([A-Z]?\d+)\)", sid.upper()):
        if c not in cands:
            cands.append(c)
    for c in re.findall(r"\b(P\d+|\d{2,5})\b", sid.upper()):
        if c not in cands:
            cands.append(c)

print("\ncandidates", cands[:10])
for c in cands[:6]:
    k = get("/sales-dashboard/kpis", site_ids=c)
    h = get("/executive-overview/summary", site_ids=c)
    kp = k.get("kpis") or {}
    print(
        f"  site_ids={c}: SALES net={float(kp.get('net_sales') or 0)/1e7:.3f}Cr "
        f"units={kp.get('units_sold')} | HOME net={float(h.get('net_sales') or 0)/1e7:.3f}Cr "
        f"units={h.get('units_sold')} active={h.get('active_stores')}"
    )

# Channel alias check
r2 = get("/sales-dashboard/rows", page=1, page_size=5, channel="PT")
chs = sorted(
    {
        str(x.get("sales_channel") or x.get("channel") or "")
        for x in (r2.get("rows") or [])
    }
)
print("\nPT channel row labels:", chs)

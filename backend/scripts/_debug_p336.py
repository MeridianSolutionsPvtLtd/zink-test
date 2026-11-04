"""Debug why Home/Sales Overview return zero for site P336."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")


def main() -> None:
    import pandas as pd

    from api.routes.sales_dashboard import (
        _apply_filters,
        _get_sales_df,
        get_customer_master_filters,
        get_sales_dashboard_kpis,
    )
    from api.sales_year_loader import fy_token, get_default_dashboard_year
    from api.routes.executive_overview import get_executive_summary
    from api.page_response_cache import _frontend_request

    year = int(get_default_dashboard_year())
    fy = fy_token(year)
    print("year", year, "fy", fy)

    cm = get_customer_master_filters()
    stores = (cm.get("options") or {}).get("stores") or (cm.get("stores") if isinstance(cm, dict) else None)
    # payload shape varies
    print("cm keys", list(cm.keys())[:20] if isinstance(cm, dict) else type(cm))
    rows = cm.get("rows") or cm.get("sites") or []
    p336_rows = [r for r in rows if "336" in str(r).upper()]
    print("cm p336 sample", p336_rows[:5])

    # Look in options.site_keys / stores
    opts = cm.get("options") or {}
    for k, v in opts.items():
        if isinstance(v, list):
            hit = [x for x in v if "336" in str(x).upper()]
            if hit:
                print("opt", k, "hits", hit[:8])

    df = _get_sales_df(force_refresh=False)
    print("sales rows", len(df), "cols", list(df.columns)[:20])
    for col in ("site_id", "site_name", "site_code", "store_code", "site_short_code"):
        if col in df.columns:
            s = df[col].astype(str)
            m = s.str.contains("336", case=False, na=False)
            print(col, "336_hits", int(m.sum()), "sample", sorted(s[m].unique().tolist())[:15])

    # How FE likely sends site_ids for P336
    candidates = ["P336", "336", "PT-Circular Road Dimapur (P336)"]
    for sid in candidates:
        filtered = _apply_filters(df, site_id="all", site_ids=sid)
        print(
            "filter site_ids=%r rows=%s net=%s"
            % (
                sid,
                len(filtered),
                float(pd.to_numeric(filtered.get("net_sale_amount", 0), errors="coerce").fillna(0).sum())
                if len(filtered)
                else 0,
            )
        )

    for sid in candidates:
        try:
            kpis = get_sales_dashboard_kpis(
                year=year,
                site_id="all",
                site_ids=sid,
                channel="all",
                month="all",
                sku="all",
                season="all",
                barcode="all",
                barcodes="",
                departments="",
                sections="",
                zones="",
                store_grades="",
                states="",
                cities="",
                search="",
                timeline_mode="fy_year",
                timeline_values=fy,
                refresh=1,
            )
            print(
                "kpis site_ids=%r total=%s soh=%s net=%s"
                % (
                    sid,
                    kpis.get("total"),
                    (kpis.get("kpis") or {}).get("current_soh"),
                    (kpis.get("kpis") or {}).get("net_sales"),
                )
            )
        except Exception as exc:
            print("kpis fail", sid, exc)

    for sid in candidates:
        try:
            q = f"year={year}&timeline_mode=fy_year&timeline_values={fy}&site_ids={sid}"
            summ = get_executive_summary(
                _frontend_request(q),
                year=year,
                zone="all",
                channel="all",
                site_ids=sid,
                season="",
            )
            print(
                "home site_ids=%r net=%s qty=%s detail=%s"
                % (
                    sid,
                    (summ.get("kpis") or summ).get("netSales")
                    or (summ.get("summary") or {}).get("net_sales")
                    or summ.get("net_sales"),
                    (summ.get("kpis") or {}).get("quantitySold")
                    or (summ.get("summary") or {}).get("qty"),
                    {k: summ.get(k) for k in list(summ.keys())[:12]},
                )
            )
        except Exception as exc:
            print("home fail", sid, exc)


if __name__ == "__main__":
    main()

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from pipeline_inputs import exclude_closed_replenishment_sites, get_as_of_date
from customer_master import fill_city_state

OUTPUT_DIR = BASE_DIR / "output_files"
PREDICTION_FULL = (
    OUTPUT_DIR / "SS26_weekly_predictions_formula_next_week_with_refill_recommendation.csv"
)
NO_SALES_DONOR_FILE = OUTPUT_DIR / "soh_null_no_sales_transfer_days.csv"
IN_STORE_SOH        = OUTPUT_DIR / "store_soh_new.csv"
OUT_DIR = BASE_DIR / "transfer" / "output"
OUT_RECEIVERS = OUT_DIR / "potential_receivers.csv"
OUT_DONORS = OUT_DIR / "potential_donors.csv"
OUT_SUMMARY = OUT_DIR / "summary.txt"

# ── Donor thresholds ──────────────────────────────────────────────────────────
# A site+SKU is donor-eligible only when ALL its sizes have had zero sales for
# at least this many days, AND no future demand is predicted for any size.
NO_SALE_DAYS: int = 25

# Temporary: 12 Jul 2026. Later set SALES_REFERENCE_DATE = None for today's date.
SALES_REFERENCE_DATE: str | None = None  # use get_as_of_date() (today) when None
NO_SALES_LABEL = "No Sales"

# WH replenishment columns: if any of these > 0 for a site+barcode, that
# barcode is already being restocked from the warehouse → not a donor.
WH_REPLENISHMENT_COLS: list[str] = [
    "covered_by_wh_soh",
    "wh_min_2_35d_push_qty",
    "wh_store_zero_push_qty",
]

GRADE_LOOKUP_FILES = [
    OUTPUT_DIR / "SS26_weekly_predictions_formula_next_week_with_refill_recommendation.csv",
    OUTPUT_DIR / "soh_null_no_sales_transfer_days.csv",
]


def _to_num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").fillna(0)


def _clean_text(series: pd.Series) -> pd.Series:
    out = series.astype(str).str.strip()
    return out.replace({"nan": "", "None": "", "<NA>": ""})


def _format_no_sales_days(series: pd.Series) -> pd.Series:
    """Replace 0/999/9999/blank with 'No Sales'; keep numeric day counts as int."""
    out = series.astype(object)
    num = pd.to_numeric(series, errors="coerce")
    mask = (
        series.astype(str).str.strip().eq(NO_SALES_LABEL)
        | num.isin([0, 999, 9999])
        | num.isna()
    )
    out.loc[mask] = NO_SALES_LABEL
    valid = ~mask & num.notna()
    out.loc[valid] = num.loc[valid].astype(int)
    return out


def _is_no_recent_sale_days(series: pd.Series, threshold: int = NO_SALE_DAYS) -> pd.Series:
    """True when no sale history or last sale older than threshold days."""
    s = series.astype(str).str.strip()
    num = pd.to_numeric(series, errors="coerce")
    no_sales = s.eq(NO_SALES_LABEL) | num.isin([999, 9999]) | num.isna()
    return no_sales | (num > threshold)


def _has_recent_sale_days(series: pd.Series, threshold: int = NO_SALE_DAYS) -> pd.Series:
    """True when last sale was within threshold days."""
    s = series.astype(str).str.strip()
    num = pd.to_numeric(series, errors="coerce")
    no_sales = s.eq(NO_SALES_LABEL) | num.isin([999, 9999]) | num.isna()
    return (~no_sales) & (num <= threshold)


def _recalc_days_since_last_sale(
    df: pd.DataFrame,
    reference_date: str | None = None,
) -> pd.DataFrame:
    """Recompute days_since_last_sale = as_of_date - last_selling_date."""
    out = df.copy()
    ref_str = reference_date or SALES_REFERENCE_DATE or get_as_of_date()
    ref = pd.to_datetime(ref_str, errors="coerce").normalize()
    if pd.isna(ref):
        return out

    ltd_col = None
    for c in ("last_selling_date", "Last_Selling_Date", "last_sale_date"):
        if c in out.columns:
            ltd_col = c
            break
    if ltd_col is None:
        return out

    last_sale = pd.to_datetime(out[ltd_col], errors="coerce")
    days = (ref - last_sale).dt.days
    out["days_since_last_sale"] = out.get("days_since_last_sale", pd.Series(index=out.index, dtype=object))
    out["days_since_last_sale"] = out["days_since_last_sale"].astype(object)
    out.loc[last_sale.notna(), "days_since_last_sale"] = (
        pd.to_numeric(days, errors="coerce").clip(lower=0).astype("Int64")
    )
    out.loc[last_sale.isna(), "days_since_last_sale"] = NO_SALES_LABEL
    return out


def _rename_alias_columns(df: pd.DataFrame, alias_map: dict[str, list[str]]) -> pd.DataFrame:
    out = df.copy()
    norm_to_actual = {str(c).strip().lower().replace(" ", "").replace("_", ""): c for c in out.columns}
    for target, aliases in alias_map.items():
        if target in out.columns:
            continue
        for alias in aliases:
            key = str(alias).strip().lower().replace(" ", "").replace("_", "")
            if key in norm_to_actual:
                out = out.rename(columns={norm_to_actual[key]: target})
                norm_to_actual = {
                    str(c).strip().lower().replace(" ", "").replace("_", ""): c for c in out.columns
                }
                break
    return out


def _normalize_prediction(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = out.columns.str.strip()
    out = _rename_alias_columns(
        out,
        {
            "sum_of_sales": ["Sum_of_Sales", "sum_of_sales", "sum of sales", "total_net_sales"],
            "site_id": ["Site_ID", "site_id", "site id"],
            "site_name": ["Site_Name", "site_name", "site name"],
            "barcode": ["Barcode", "barcode"],
            "sku": ["SKU", "sku"],
            "size": ["Size", "size"],
            "city": ["City", "city"],
            "state": ["State", "state"],
            "store_type": ["Store_Type", "store_type", "store type"],
        },
    )

    for c in ("site_id", "site_name", "barcode", "sku", "size", "city", "state", "store_type"):
        if c in out.columns:
            out[c] = _clean_text(out[c])
        else:
            out[c] = ""
    out["site_id"] = out["site_id"].str.upper()
    out["barcode"] = out["barcode"].str.replace(".0", "", regex=False)
    out["sku"] = out["sku"].str.upper()
    out["size"] = out["size"].str.upper()

    out = _recalc_days_since_last_sale(out)

    num_cols = [
        "store_soh",
        "wh_soh",
        "days_since_last_sale",
        "days_since_last_transfer",
        "unfilled_qty",
        "sold_qty_prev_35d",
        "lag1_sold_qty",
        "priority_score",
        "recommendation_refill_qty",
        "sum_of_sales",
    ]
    for c in num_cols:
        if c == "days_since_last_sale":
            if c not in out.columns:
                out[c] = NO_SALES_LABEL
            else:
                out[c] = _format_no_sales_days(out[c])
            continue
        if c not in out.columns:
            out[c] = 0
        out[c] = _to_num(out[c])

    out["_last_purchase_blank"] = False
    for c in ("last_purchase_date", "Last_Purchase_Date", "last_purchase_dates"):
        if c in out.columns:
            out["_last_purchase_blank"] = _clean_text(out[c]).eq("")
            break
    return out


def build_receivers(pred: pd.DataFrame) -> pd.DataFrame:
    """
    Receivers woh site+sku hain jahan kam se kam 2 sizes ki need (unfilled_qty > 0) ho.
    Output mein **saari sizes** (including jahan qty_still_needed = 0) aayengi,
    taaki receiver ke paas poori size-wise availability (store_soh, sales, etc.) dikhe.
    """
    need_rows = pred[pred["unfilled_qty"] > 0].copy()
    need_rows["qty_still_needed"] = need_rows["unfilled_qty"].clip(lower=0)

    need_count = (
        need_rows
        .groupby(["site_id", "sku"], as_index=False)
        .agg(need_size_count=("qty_still_needed", lambda x: (x > 0).sum()))
    )
    eligible_pairs = need_count[need_count["need_size_count"] >= 2][["site_id", "sku"]]

    if eligible_pairs.empty:
        return pd.DataFrame()

    recv = pred.merge(eligible_pairs, on=["site_id", "sku"], how="inner").copy()
    recv["qty_still_needed"] = recv["unfilled_qty"].clip(lower=0)
    recv["recommended_qty_to_send"] = _to_num(recv.get("recommendation_refill_qty", 0))

    if NO_SALES_DONOR_FILE.exists():
        sn = pd.read_csv(NO_SALES_DONOR_FILE, low_memory=False)
        sn.columns = sn.columns.str.strip()
        sn = sn.rename(columns={
            "Site_ID": "site_id",
            "Site": "site_name",
            "Barcode": "barcode",
            "SKU": "sku",
            "Size": "size",
            "Store_Soh": "store_soh"
        })
        for c in ("site_id", "site_name", "barcode", "sku", "size", "city", "state", "store_type"):
            if c in sn.columns:
                sn[c] = _clean_text(sn[c])
        sn["site_id"] = sn["site_id"].str.upper()
        sn["barcode"] = sn["barcode"].str.replace(".0", "", regex=False)
        sn["sku"] = sn["sku"].str.upper()
        sn["store_soh"] = _to_num(sn.get("store_soh", 0))
        sn["wh_soh"] = _to_num(sn.get("wh_soh", 0))
        
        sn = sn[sn["store_soh"] > 0].copy()
        recv_nosales = sn.merge(eligible_pairs, on=["site_id", "sku"], how="inner").copy()
        
        if not recv_nosales.empty:
            recv_nosales["qty_still_needed"] = 0.0
            recv_nosales["recommended_qty_to_send"] = 0.0
            recv_nosales["days_since_last_sale"] = NO_SALES_LABEL
            recv_nosales["days_since_last_transfer"] = _to_num(
                recv_nosales.get("days_since_last_transfer", 999)
            ).fillna(999.0)
            
            recv_nosales["lag1_sold_qty"] = 0.0
            recv_nosales["sold_qty_prev_35d"] = 0.0
            recv_nosales["sum_of_sales"] = 0.0
            recv_nosales["priority_score"] = 0.0
            
            recv = pd.concat([recv, recv_nosales], ignore_index=True)
            recv = recv.drop_duplicates(subset=["site_id", "barcode", "sku", "size"], keep="first")

    if "_last_purchase_blank" not in recv.columns:
        recv["_last_purchase_blank"] = False
    blank_mask = recv["_last_purchase_blank"].fillna(False)

    cols = [
        "site_id",
        "site_name",
        "barcode",
        "sku",
        "size",
        "store_soh",
        "wh_soh",
        "city",
        "state",
        "store_type",
        "days_since_last_sale",
        "days_since_last_transfer",
        "lag1_sold_qty",
        "sold_qty_prev_35d",
        "sum_of_sales",
        "qty_still_needed",
        "recommended_qty_to_send",
        "priority_score",
    ]
    for c in cols:
        if c not in recv.columns:
            recv[c] = 0 if c not in {"site_id", "site_name", "barcode", "sku", "size", "city", "state", "store_type"} else ""

    recv = recv[cols].rename(
        columns={
            "lag1_sold_qty": "last_week_sales",
            "sold_qty_prev_35d": "last_35day_sold_qty",
            "sum_of_sales": "total_sales",
        }
    )

    ordered = [
        "site_id",
        "site_name",
        "barcode",
        "sku",
        "size",
        "store_soh",
        "wh_soh",
        "city",
        "state",
        "store_type",
        "days_since_last_sale",
        "days_since_last_transfer",
        "last_week_sales",
        "last_35day_sold_qty",
        "total_sales",
        "qty_still_needed",
        "recommended_qty_to_send",
        "priority_score",
    ]
    recv = recv[ordered].copy()

    recv["days_since_last_sale"] = _format_no_sales_days(recv["days_since_last_sale"])
    recv["days_since_last_transfer"] = recv["days_since_last_transfer"].astype(object)
    recv.loc[blank_mask, "days_since_last_transfer"] = "No Transfer"
    return recv.sort_values(["site_id", "sku", "size"], kind="stable").reset_index(drop=True)


def build_donors_from_prediction(pred: pd.DataFrame, receiver_pairs: pd.DataFrame) -> pd.DataFrame:
    """
    Identify donor site+SKU pairs from the main prediction/refill file.

    A site+SKU is eligible as a donor only when ALL three conditions hold:
      1. NO RECENT SALES  – every available size had zero sales in the last
                            NO_SALE_DAYS days (uses sold_qty_prev_25d when
                            present, falls back to sold_qty_prev_35d).
      2. NO PREDICTED DEMAND – no size of this SKU at this site has
                            predicted_qty > 0 (ML model sees no future need).
      3. NO WH REPLENISHMENT – none of the barcodes for this site+SKU is
                            currently being restocked from the warehouse
                            (covered_by_wh_soh / wh_min_2_35d_push_qty /
                             wh_store_zero_push_qty all = 0).
    """
    x = pred.copy()
    x["store_soh"]             = _to_num(x["store_soh"])
    x["days_since_last_transfer"] = _to_num(x.get("days_since_last_transfer", 0))
    x["lag1_sold_qty"]         = _to_num(x["lag1_sold_qty"])
    x["unfilled_qty"]          = _to_num(x["unfilled_qty"])
    x["predicted_qty"]         = _to_num(x.get("predicted_qty", 0))

    # Use 25-day sales column if available; fall back to 35-day column
    if "sold_qty_prev_25d" in x.columns:
        x["_sales_check"] = _to_num(x["sold_qty_prev_25d"])
    else:
        x["_sales_check"] = _to_num(x.get("sold_qty_prev_35d", 0))

    # WH replenishment flag per row
    wh_cols_present = [c for c in WH_REPLENISHMENT_COLS if c in x.columns]
    if wh_cols_present:
        for c in wh_cols_present:
            x[c] = _to_num(x[c])
        x["_wh_active"] = x[wh_cols_present].max(axis=1) > 0
    else:
        x["_wh_active"] = False

    # Only rows with physical stock (for Conditions 1 & 3)
    x_avail = x[x["store_soh"] > 0].copy()
    if x_avail.empty:
        return pd.DataFrame()

    # ── Condition 1: no sales in last NO_SALE_DAYS days ──────────────────────
    x_avail["_no_recent_sale"] = (
        (x_avail["_sales_check"] <= 0) &
        _is_no_recent_sale_days(x_avail["days_since_last_sale"])
    )

    # ── Condition 2: no predicted demand — check ALL sizes (SOH=0 included) ──
    # A SKU is NOT a donor if ANY size (even sold-out ones) has predicted_qty > 0.
    # We must check the FULL x, not just x_avail, because a sold-out size
    # (store_soh=0) can still have predicted demand that we must respect.
    pred_by_sku = x.groupby(["site_id", "sku"], as_index=False).agg(
        max_predicted_qty_all = ("predicted_qty", "max")   # across ALL sizes
    )
    skus_with_demand = set(
        pred_by_sku.loc[pred_by_sku["max_predicted_qty_all"] > 0, "site_id"].astype(str).str.upper()
        + "_"
        + pred_by_sku.loc[pred_by_sku["max_predicted_qty_all"] > 0, "sku"].astype(str).str.upper()
    )

    # ── Group available rows to site+SKU level (Cond 1 & 3) ──────────────────
    grp = x_avail.groupby(["site_id", "sku"], as_index=False).agg(
        available_barcode_count = ("barcode",         "nunique"),
        all_no_recent_sale      = ("_no_recent_sale", "all"),    # Cond 1
        any_wh_active           = ("_wh_active",      "any"),    # Cond 3 check
    )

    # Apply Condition 2 via the full-data demand set
    grp["_sku_key"]       = grp["site_id"].astype(str).str.upper() + "_" + grp["sku"].astype(str).str.upper()
    grp["_has_any_demand"] = grp["_sku_key"].isin(skus_with_demand)

    # Apply all three conditions
    donor_site_sku = grp[
        (grp["available_barcode_count"] > 0) &
        (grp["all_no_recent_sale"]) &                 # Cond 1: no recent sales
        (~grp["_has_any_demand"]) &                   # Cond 2: no predicted demand (any size)
        (~grp["any_wh_active"])                       # Cond 3: no WH replenishment
    ][["site_id", "sku"]]

    donors = x_avail.merge(donor_site_sku, on=["site_id", "sku"], how="inner")
    donors = donors[donors["unfilled_qty"] <= 0]

    # Exclude site+barcode pairs that are receivers
    if not receiver_pairs.empty:
        donors = donors.merge(
            receiver_pairs.assign(_is_receiver=True),
            on=["site_id", "barcode"],
            how="left",
        )
        donors = donors[donors["_is_receiver"].isna()].drop(columns=["_is_receiver"])

    donors["donor_source"] = "sales_data_donors"
    if "_last_purchase_blank" not in donors.columns:
        donors["_last_purchase_blank"] = False

    # Keep sold_qty_prev_35d for output (for reference); use _sales_check internally
    if "sold_qty_prev_35d" not in donors.columns:
        donors["sold_qty_prev_35d"] = donors["_sales_check"]

    keep_cols = [
        "site_id", "site_name", "barcode", "sku", "size",
        "store_soh", "wh_soh", "city", "state", "store_type",
        "days_since_last_sale", "days_since_last_transfer",
        "lag1_sold_qty", "sold_qty_prev_35d", "sum_of_sales",
        "donor_source", "_last_purchase_blank",
    ]
    donors = donors[[c for c in keep_cols if c in donors.columns]].copy()
    for c in keep_cols:
        if c not in donors.columns:
            donors[c] = "" if c in {"site_id","site_name","barcode","sku","size",
                                    "city","state","store_type","donor_source"} else 0

    donors = donors.rename(columns={
        "days_since_last_sale":     "days_from_last_sales",
        "days_since_last_transfer": "days_from_last_transfer",
        "lag1_sold_qty":            "last_week_sales",
        "sold_qty_prev_35d":        "last_35day_sold_qty",
        "sum_of_sales":             "total_sales",
    })
    ordered = [
        "site_id", "site_name", "barcode", "sku", "size",
        "store_soh", "wh_soh", "city", "state", "store_type",
        "days_from_last_sales", "days_from_last_transfer",
        "last_week_sales", "last_35day_sold_qty", "total_sales",
        "donor_source",
    ]
    donors["days_from_last_transfer"] = donors["days_from_last_transfer"].astype(object)
    donors.loc[donors["_last_purchase_blank"].fillna(False), "days_from_last_transfer"] = "No Transfer"
    donors = donors.drop(columns=["_last_purchase_blank"], errors="ignore")
    return donors[[c for c in ordered if c in donors.columns]]


def build_donors_from_no_sales(path: Path, receiver_pairs: pd.DataFrame) -> pd.DataFrame:
    """
    Donors from soh_null_no_sales_transfer_days.csv.

    That file is already filtered at **site + SKU** (any barcode of a SKU that
    sold at the site is excluded upstream). Here we only keep stocked barcodes
    with last transfer older than NO_SALE_DAYS and that are not receivers.
    """
    if not path.exists():
        return pd.DataFrame()

    d = pd.read_csv(path, low_memory=False)
    d.columns = d.columns.str.strip()
    ren = {
        "Site_ID": "site_id",
        "Site": "site_name",
        "Barcode": "barcode",
        "SKU": "sku",
        "Size": "size",
        "Store_Soh": "store_soh",
        "wh_soh": "wh_soh",
        "city": "city",
        "state": "state",
        "store_type": "store_type",
    }
    d = d.rename(columns=ren)
    for c in ("site_id", "site_name", "barcode", "sku", "size", "city", "state", "store_type"):
        if c not in d.columns:
            d[c] = ""
        d[c] = _clean_text(d[c])
    d["site_id"] = d["site_id"].str.upper()
    d["barcode"] = d["barcode"].str.replace(".0", "", regex=False)
    d["store_soh"] = _to_num(d.get("store_soh", 0))
    d["wh_soh"] = _to_num(d.get("wh_soh", 0))
    d["days_since_last_transfer"] = _to_num(d.get("days_since_last_transfer", 0))

    d["days_since_last_sale"] = NO_SALES_LABEL
    d["lag1_sold_qty"] = 0.0
    d["sold_qty_prev_35d"] = 0.0
    d["sum_of_sales"] = 0.0
    # Use NO_SALE_DAYS (25) — items transferred >25 days ago with zero sales
    d = d[(d["store_soh"] > 0) & (d["days_since_last_transfer"] > NO_SALE_DAYS)].copy()

    if not receiver_pairs.empty:
        d = d.merge(
            receiver_pairs.assign(_is_receiver=True),
            on=["site_id", "barcode"],
            how="left",
        )
        d = d[d["_is_receiver"].isna()].drop(columns=["_is_receiver"])

    d["donor_source"] = "no_sales_data_donors"
    out = d[
        [
            "site_id",
            "site_name",
            "barcode",
            "sku",
            "size",
            "store_soh",
            "wh_soh",
            "city",
            "state",
            "store_type",
            "days_since_last_sale",
            "days_since_last_transfer",
            "lag1_sold_qty",
            "sold_qty_prev_35d",
            "sum_of_sales",
            "donor_source",
        ]
    ].copy().rename(
        columns={
            "days_since_last_sale": "days_from_last_sales",
            "days_since_last_transfer": "days_from_last_transfer",
            "lag1_sold_qty": "last_week_sales",
            "sold_qty_prev_35d": "last_35day_sold_qty",
            "sum_of_sales": "total_sales",
        }
    )
    ordered = [
        "site_id",
        "site_name",
        "barcode",
        "sku",
        "size",
        "store_soh",
        "wh_soh",
        "city",
        "state",
        "store_type",
        "days_from_last_sales",
        "days_from_last_transfer",
        "last_week_sales",
        "last_35day_sold_qty",
        "total_sales",
        "donor_source",
    ]
    return out[ordered]


# ========== NEW FUNCTION: donors from store_soh_new.csv ==========
def build_donors_from_store_soh(path: Path, receiver_pairs: pd.DataFrame, pred_df: pd.DataFrame) -> pd.DataFrame:
    """Read store_soh_new.csv and include any available stock (store_soh > 0)
    that is NOT present in the prediction file (site_id + barcode) and not a receiver."""
    if not path.exists():
        return pd.DataFrame()

    df = pd.read_csv(path, low_memory=False)
    df.columns = df.columns.str.strip()

    # Standard column mapping
    rename_map = {
        "Site_ID": "site_id",
        "Site_id": "site_id",
        "Site": "site_name",
        "Site_Name": "site_name",
        "Barcode": "barcode",
        "SKU": "sku",
        "Size": "size",
        "SIZE": "size",
        "Store_SOH": "store_soh",
        "Current_Stock_Qty": "store_soh",
        "WH_SOH": "wh_soh",
        "City": "city",
        "State": "state",
        "Store_Type": "store_type",
    }
    df = df.rename(columns=rename_map)

    for c in ("site_id", "site_name", "barcode", "sku", "size", "city", "state", "store_type"):
        if c not in df.columns:
            df[c] = ""
        df[c] = _clean_text(df[c])

    df["site_id"] = df["site_id"].str.upper()
    df["barcode"] = df["barcode"].str.replace(".0", "", regex=False)
    
    if "store_soh" not in df.columns:
        df["store_soh"] = 0
    df["store_soh"] = _to_num(df["store_soh"])
    
    if "wh_soh" not in df.columns:
        df["wh_soh"] = 0
    df["wh_soh"] = _to_num(df["wh_soh"])

    # Keep only positive stock
    df = df[df["store_soh"] > 0].copy()
    if df.empty:
        return pd.DataFrame()

    # Exclude rows that already exist in the prediction file (site_id + barcode)
    pred_keys = pred_df[["site_id", "barcode"]].drop_duplicates()
    df = df.merge(pred_keys.assign(in_pred=True), on=["site_id", "barcode"], how="left")
    df = df[df["in_pred"].isna()].drop(columns=["in_pred"])

    # Exclude receiver pairs
    if not receiver_pairs.empty:
        df = df.merge(receiver_pairs.assign(_is_receiver=True), on=["site_id", "barcode"], how="left")
        df = df[df["_is_receiver"].isna()].drop(columns=["_is_receiver"])

    # Set donor source and fake sales columns (no sales history available)
    df["donor_source"] = "store_soh_donors"
    df["days_since_last_sale"] = NO_SALES_LABEL
    df["days_since_last_transfer"] = 999.0   # will be turned into "No Sales" later
    df["lag1_sold_qty"] = 0.0
    df["sold_qty_prev_35d"] = 0.0
    df["sum_of_sales"] = 0.0

    out = df[[
        "site_id", "site_name", "barcode", "sku", "size", "store_soh", "wh_soh",
        "city", "state", "store_type", "days_since_last_sale", "days_since_last_transfer",
        "lag1_sold_qty", "sold_qty_prev_35d", "sum_of_sales", "donor_source"
    ]].copy()
    out = out.rename(columns={
        "days_since_last_sale": "days_from_last_sales",
        "days_since_last_transfer": "days_from_last_transfer",
        "lag1_sold_qty": "last_week_sales",
        "sold_qty_prev_35d": "last_35day_sold_qty",
        "sum_of_sales": "total_sales",
    })
    return out
# ========== END NEW FUNCTION ==========


def _build_grade_map() -> dict[str, str]:
    """Build site_id → store_grade lookup from prediction and soh_null files."""
    grade_map: dict[str, str] = {}
    for fpath in GRADE_LOOKUP_FILES:
        if not fpath.exists():
            continue
        try:
            if "prediction" in str(fpath):
                df = pd.read_csv(fpath, low_memory=False, usecols=["site_id", "store_grade"])
                df["site_id"] = df["site_id"].astype(str).str.strip().str.upper()
            else:  # soh_null
                df = pd.read_csv(fpath, low_memory=False, usecols=["Site_ID", "store_grade"])
                df["Site_ID"] = df["Site_ID"].astype(str).str.strip().str.upper()
                df = df.rename(columns={"Site_ID": "site_id"})
            df["store_grade"] = df["store_grade"].astype(str).str.strip().replace({"nan": "", "None": ""})
            for _, row in df.drop_duplicates("site_id").iterrows():
                if row["site_id"] and not grade_map.get(row["site_id"]):
                    grade_map[row["site_id"]] = row["store_grade"]
        except Exception:
            pass
    return grade_map


def _enrich_store_grade(df: pd.DataFrame, site_col: str = "site_id") -> pd.DataFrame:
    """Fill store_grade column using site_id lookup; create column if missing."""
    grade_map = _build_grade_map()
    if not grade_map:
        return df

    out = df.copy()
    if "store_grade" not in out.columns:
        out["store_grade"] = ""

    site_series = out[site_col].astype(str).str.strip().str.upper()
    blank_mask = out["store_grade"].astype(str).str.strip().isin(["", "nan", "None"])
    if blank_mask.any():
        out.loc[blank_mask, "store_grade"] = site_series[blank_mask].map(grade_map).fillna("")

    return out


def main() -> None:
    if not PREDICTION_FULL.exists():
        raise FileNotFoundError(f"Prediction file not found: {PREDICTION_FULL}")

    pred_raw = pd.read_csv(PREDICTION_FULL, low_memory=False)
    pred = _normalize_prediction(pred_raw)
    pred = exclude_closed_replenishment_sites(pred, log_prefix="[transfer] ")
    try:
        from sku_scope import (
            is_remapped_scope,
            remapped_sku_names,
            mask_remapped_skus,
            merge_prior_other_skus,
        )
    except ImportError:
        import sys as _sys
        _sys.path.insert(0, str(BASE_DIR))
        from sku_scope import (  # type: ignore
            is_remapped_scope,
            remapped_sku_names,
            mask_remapped_skus,
            merge_prior_other_skus,
        )

    remap_names = remapped_sku_names() if is_remapped_scope() else set()
    if remap_names:
        before = len(pred)
        pred = pred.loc[mask_remapped_skus(pred, remap_names)].copy()
        pred_raw = pred_raw.loc[mask_remapped_skus(pred_raw, remap_names)].copy()
        print(
            f"[sku_scope] remapped SKUs only for transfer: {before:,} → {len(pred):,} prediction rows",
            flush=True,
        )
        if pred.empty:
            raise ValueError("SKU remap scope: no prediction rows match OLD/NEW SKUs in SKU_REMAP.xlsx")
    as_of = SALES_REFERENCE_DATE or get_as_of_date()
    print(f"[INFO] days_since_last_sale recalculated using reference date: {as_of}", flush=True)

    store_soh_exists = IN_STORE_SOH.exists()
    if not store_soh_exists:
        print(f"[INFO] store_soh file not found: {IN_STORE_SOH} (some donors may be missing)")

    receivers = build_receivers(pred)
    if receivers.empty:
        print(
            "[INFO] No transfer receivers (need 2+ unfilled sizes per site+sku). "
            "Writing empty donor/receiver files.",
            flush=True,
        )
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        recv_cols = [
            "site_id", "site_name", "barcode", "sku", "size", "store_soh", "wh_soh",
            "city", "state", "store_type", "days_since_last_sale", "days_since_last_transfer",
            "last_week_sales", "last_35day_sold_qty", "total_sales",
            "qty_still_needed", "recommended_qty_to_send", "priority_score", "store_grade",
        ]
        donor_cols = recv_cols + ["donor_source", "days_from_last_sales", "days_from_last_transfer"]
        pd.DataFrame(columns=recv_cols).to_csv(OUT_RECEIVERS, index=False, encoding="utf-8-sig")
        pd.DataFrame(columns=donor_cols).to_csv(OUT_DONORS, index=False, encoding="utf-8-sig")
        summary = [
            f"Prediction input: {PREDICTION_FULL}",
            "Potential donors rows: 0",
            "Potential receivers rows: 0",
            "Note: No receivers — fewer than 2 unfilled sizes on any site+sku.",
            f"Saved donors: {OUT_DONORS}",
            f"Saved receivers: {OUT_RECEIVERS}",
        ]
        OUT_SUMMARY.write_text("\n".join(summary), encoding="utf-8")
        print("\n".join(summary))
        return

    receiver_pairs = receivers[["site_id", "barcode"]].drop_duplicates()

    donors_pred     = build_donors_from_prediction(pred, receiver_pairs)
    donors_nosales  = build_donors_from_no_sales(NO_SALES_DONOR_FILE, receiver_pairs)
    donors_storesoh = build_donors_from_store_soh(IN_STORE_SOH, receiver_pairs, pred)

    donors = pd.concat([donors_pred, donors_nosales, donors_storesoh], ignore_index=True)
    donors = donors.drop_duplicates(subset=["site_id", "barcode", "sku", "size"], keep="first")
    receivers = exclude_closed_replenishment_sites(receivers, log_prefix="[transfer receivers] ")
    donors = exclude_closed_replenishment_sites(donors, log_prefix="[transfer donors] ")

    # ── SKU‑Level Dead Stock Validation ──────────────────────────────────────
    # If ANY size of a SKU at a site (available or sold-out) had sales in the
    # last NO_SALE_DAYS days → exclude the entire SKU from donors.
    # Uses sold_qty_prev_25d when present, falls back to sold_qty_prev_35d.
    _sales_col = "sold_qty_prev_25d" if "sold_qty_prev_25d" in pred.columns else "sold_qty_prev_35d"
    sales_info = pred[["site_id", "barcode", "sku", "size", "store_soh",
                            _sales_col, "days_since_last_sale"]].copy()
    sales_info[_sales_col]           = pd.to_numeric(sales_info[_sales_col], errors="coerce").fillna(0)
    sales_info["days_since_last_sale"] = _format_no_sales_days(sales_info["days_since_last_sale"])
    sales_info["store_soh"]          = pd.to_numeric(sales_info["store_soh"], errors="coerce").fillna(0)
    sales_info["sku"]     = sales_info["sku"].astype(str).str.strip().str.upper()
    sales_info["site_id"] = sales_info["site_id"].astype(str).str.strip().str.upper()

    # has_recent_sale: sold in last NO_SALE_DAYS days
    sales_info["has_recent_sale"] = (
        (sales_info[_sales_col] > 0) &
        _has_recent_sale_days(sales_info["days_since_last_sale"])
    )

    # 1. Available sizes (SOH > 0) with recent sales
    sales_avail = sales_info[sales_info["store_soh"] > 0].copy()
    sku_sales_check = sales_avail.groupby(["site_id", "sku"], as_index=False).agg(
        any_size_sold_25=("has_recent_sale", "any")
    )
    active_site_skus = sku_sales_check[sku_sales_check["any_size_sold_25"]][["site_id", "sku"]]
    active_keys = set(active_site_skus["site_id"] + "_" + active_site_skus["sku"])

    # 2. Sold-out sizes (SOH ≤ 0) that still had recent sales (sold out because they were selling!)
    sold_out_active = sales_info[
        (sales_info["store_soh"] <= 0) & (sales_info["has_recent_sale"])
    ][["site_id", "sku"]].drop_duplicates()
    sold_out_keys = set(sold_out_active["site_id"] + "_" + sold_out_active["sku"])

    # Combined active keys
    all_active_keys = active_keys.union(sold_out_keys)

    # Filter the final donors list to exclude any active SKUs
    donors["sku_upper"] = donors["sku"].astype(str).str.strip().str.upper()
    donors["site_upper"] = donors["site_id"].astype(str).str.strip().str.upper()
    donors_key = donors["site_upper"] + "_" + donors["sku_upper"]

    donors = donors[~donors_key.isin(all_active_keys)].drop(columns=["sku_upper", "site_upper"])

    # ── SKU‑Level Expansion: Include ALL available sizes of the validated SKU at that store ──
    # Ensure all available sizes (store_soh > 0) of validated donor SKUs from both pred and soh_null are included.
    if not donors.empty:
        validated_pairs = donors[["site_id", "sku"]].drop_duplicates()
        
        # 1. Expand from prediction (pred)
        pred_avail = pred[pred["store_soh"] > 0].copy()
        pred_expanded = pred_avail.merge(validated_pairs, on=["site_id", "sku"], how="inner")
        
        pred_expanded = pred_expanded[[
            "site_id", "site_name", "barcode", "sku", "size", "store_soh", "wh_soh",
            "city", "state", "store_type", "days_since_last_sale", "days_since_last_transfer",
            "lag1_sold_qty", "sold_qty_prev_35d", "sum_of_sales"
        ]].copy().rename(columns={
            "days_since_last_sale": "days_from_last_sales",
            "days_since_last_transfer": "days_from_last_transfer",
            "lag1_sold_qty": "last_week_sales",
            "sold_qty_prev_35d": "last_35day_sold_qty",
            "sum_of_sales": "total_sales",
        })
        pred_expanded["donor_source"] = "sales_data_donors"
        
        # 2. Expand from soh_null (if exists)
        sn_expanded = pd.DataFrame()
        if NO_SALES_DONOR_FILE.exists():
            sn = pd.read_csv(NO_SALES_DONOR_FILE, low_memory=False)
            sn.columns = sn.columns.str.strip()
            sn = sn.rename(columns={
                "Site_ID": "site_id",
                "Site": "site_name",
                "Barcode": "barcode",
                "SKU": "sku",
                "Size": "size",
                "Store_Soh": "store_soh"
            })
            for c in ("site_id", "site_name", "barcode", "sku", "size", "city", "state", "store_type"):
                if c in sn.columns:
                    sn[c] = _clean_text(sn[c])
            sn["site_id"] = sn["site_id"].str.upper()
            sn["barcode"] = sn["barcode"].str.replace(".0", "", regex=False)
            sn["sku"] = sn["sku"].str.upper()
            sn["store_soh"] = _to_num(sn.get("store_soh", 0))
            sn["wh_soh"] = _to_num(sn.get("wh_soh", 0))
            sn = sn[sn["store_soh"] > 0].copy()
            
            sn_expanded = sn.merge(validated_pairs, on=["site_id", "sku"], how="inner").copy()
            if not sn_expanded.empty:
                sn_expanded["days_since_last_sale"] = NO_SALES_LABEL
                sn_expanded["days_since_last_transfer"] = _to_num(
                    sn_expanded.get("days_since_last_transfer", 999)
                ).fillna(999.0)
                
                sn_expanded["lag1_sold_qty"] = 0.0
                sn_expanded["sold_qty_prev_35d"] = 0.0
                sn_expanded["sum_of_sales"] = 0.0
                
                sn_expanded = sn_expanded[[
                    "site_id", "site_name", "barcode", "sku", "size", "store_soh", "wh_soh",
                    "city", "state", "store_type", "days_since_last_sale", "days_since_last_transfer",
                    "lag1_sold_qty", "sold_qty_prev_35d", "sum_of_sales"
                ]].copy().rename(columns={
                    "days_since_last_sale": "days_from_last_sales",
                    "days_since_last_transfer": "days_from_last_transfer",
                    "lag1_sold_qty": "last_week_sales",
                    "sold_qty_prev_35d": "last_35day_sold_qty",
                    "sum_of_sales": "total_sales",
                })
                sn_expanded["donor_source"] = "no_sales_data_donors"
        
        # Combine everything
        donors = pd.concat([pred_expanded, sn_expanded], ignore_index=True)
        donors = donors.drop_duplicates(subset=["site_id", "barcode", "sku", "size"], keep="first")

    # ── FINAL SAFETY FILTER: predicted demand check across ALL donor sources ──
    # build_donors_from_no_sales() and build_donors_from_store_soh() do NOT
    # check predicted_qty. A site+SKU where ANY size (even SOH=0) has
    # predicted_qty > 0 must NEVER be a donor — even if it arrived via those
    # other paths. Apply this as a hard final gate over the full donors table.
    pred_qty_check = pred_raw[["site_id", "barcode", "sku", "predicted_qty"]].copy()
    pred_qty_check["predicted_qty"] = pd.to_numeric(
        pred_qty_check["predicted_qty"], errors="coerce"
    ).fillna(0)
    pred_qty_check["site_id"] = pred_qty_check["site_id"].astype(str).str.strip().str.upper()
    pred_qty_check["sku"]     = pred_qty_check["sku"].astype(str).str.strip().str.upper()

    # site+SKU pairs that have ANY predicted demand (any size, any SOH level)
    has_demand_pairs = (
        pred_qty_check[pred_qty_check["predicted_qty"] > 0][["site_id", "sku"]]
        .drop_duplicates()
        .assign(_has_demand=True)
    )
    before = len(donors)
    donors = donors.merge(has_demand_pairs, on=["site_id", "sku"], how="left")
    donors = donors[donors["_has_demand"].isna()].drop(columns=["_has_demand"])
    after = len(donors)
    if before != after:
        print(f"[SAFETY FILTER] Removed {before - after:,} donor rows where predicted_qty > 0 for some size.")

    # Post‑processing for display columns
    donors["days_from_last_transfer"] = _clean_text(donors.get("days_from_last_transfer", ""))
    m_nosales_999 = (donors["donor_source"] == "no_sales_data_donors") & (
        pd.to_numeric(donors["days_from_last_transfer"], errors="coerce") == 999
    )
    m_pred_0 = (donors["donor_source"] != "no_sales_data_donors") & (
        pd.to_numeric(donors["days_from_last_transfer"], errors="coerce") == 0
    )
    donors.loc[m_nosales_999 | m_pred_0, "days_from_last_transfer"] = "No Sales"

    donors["days_from_last_sales"] = _format_no_sales_days(donors.get("days_from_last_sales", NO_SALES_LABEL))
    donors["days_from_last_transfer"] = donors["days_from_last_transfer"].replace("No Sales", "No Transfer")
    donors = donors.sort_values(["site_id", "sku", "size"], kind="stable").reset_index(drop=True)
    receivers = fill_city_state(receivers, site_col="site_id")
    donors = fill_city_state(donors, site_col="site_id")

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Enrich store_grade based on site_id
    receivers = _enrich_store_grade(receivers, site_col="site_id")
    donors = _enrich_store_grade(donors, site_col="site_id")

    if remap_names:
        donors = merge_prior_other_skus(donors, OUT_DONORS, names=remap_names)
        receivers = merge_prior_other_skus(receivers, OUT_RECEIVERS, names=remap_names)

    receivers.to_csv(OUT_RECEIVERS, index=False, encoding="utf-8-sig")
    donors.to_csv(OUT_DONORS, index=False, encoding="utf-8-sig")

    donor_site_sku = donors[["site_id", "sku"]].drop_duplicates()
    receiver_site_sku = receivers[["site_id", "sku"]].drop_duplicates()
    overlap_site_sku = donor_site_sku.merge(receiver_site_sku, on=["site_id", "sku"], how="inner")

    summary = [
        f"Prediction input: {PREDICTION_FULL}",
        f"No-sales donor input: {NO_SALES_DONOR_FILE if NO_SALES_DONOR_FILE.exists() else 'NOT_FOUND'}",
        f"Store SOH input: {IN_STORE_SOH if store_soh_exists else 'NOT_FOUND'}",
        f"Potential donors rows: {len(donors):,}",
        f"Potential receivers rows: {len(receivers):,}",
        f"Distinct donor site+sku: {len(donor_site_sku):,}",
        f"Distinct receiver site+sku: {len(receiver_site_sku):,}",
        f"Donor/receiver overlap on site+sku: {len(overlap_site_sku):,}",
        f"Saved donors: {OUT_DONORS}",
        f"Saved receivers: {OUT_RECEIVERS}",
    ]
    OUT_SUMMARY.write_text("\n".join(summary), encoding="utf-8")
    print("\n".join(summary))


if __name__ == "__main__":
    main()
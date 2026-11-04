"""
Store SOH rows whose (site, SKU) never appears in combined sales, with SOH > 0.

No-sales is evaluated at **site + SKU** (not site + barcode): if any size/barcode
of that SKU sold at the site, the whole site+SKU is treated as having sales.

Barcode-level rows are still written for transfer matching, but only for SKUs
that never sold at that site.

For those combos: from COMBINED_TRANSFER attach max(Transfer_Date) and sum of
Quantity per site+barcode, then days from max transfer date to reference date.

Inputs (no retail_input workbook):
  - output_files/COMBINED_SOH.xlsx
  - output_files/COMBINED_SALES.xlsx
  - output_files/COMBINED_TRANSFER.xlsx
  - input_files/@ITEM MASTER DETAILS REGISTER_SP@PACKING.xlsx
  - input_files/@WAREHOUSE CLOSING STOCK REGISTER_SP_09-07-2026.xlsx
  - input_files/@CUSTOMER MASTER REGISTER_NEW (1).xlsx  (optional city/state)
  - input_files/Store List June 26.xlsx                 (optional grade)

Output: output_files/soh_null_no_sales_transfer_days.csv
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from pipeline_inputs import (
    SOH_NULL_OUT,
    build_grade_map,
    build_item_map,
    build_warehouse_soh_map,
    get_as_of_date,
    enrich_with_customer_master,
    load_combined_sales,
    load_combined_soh,
    load_combined_transfer,
    normalize_barcode,
    normalize_site,
    require_combined_inputs,
)


def _parse_mixed_date(series: pd.Series) -> pd.Series:
    s = series.copy()
    dt_text = pd.to_datetime(s, errors="coerce", dayfirst=True)
    num = pd.to_numeric(s, errors="coerce")
    dt_excel = pd.to_datetime(num, unit="D", origin="1899-12-30", errors="coerce")
    dt = dt_text.where(dt_text.notna(), dt_excel)
    return dt.where(dt.dt.year >= 2000)


def _clean_sku(series: pd.Series) -> pd.Series:
    out = series.astype(str).str.strip().str.upper()
    return out.replace({"NAN": "", "NONE": "", "<NA>": "", "NULL": ""})


def build_store_soh(store_df: pd.DataFrame) -> pd.DataFrame:
    s = store_df.copy()
    s["site_key"] = normalize_site(s["Site_id"])
    s["barcode_key"] = normalize_barcode(s["Barcode"])
    s["_qty"] = pd.to_numeric(s["Current Stock Qty"], errors="coerce").fillna(0)

    agg = s.groupby(["site_key", "barcode_key"], dropna=False, as_index=False).agg(
        store_soh_qty=("_qty", "sum"),
        Source=("Source", "first") if "Source" in s.columns else ("_qty", "count"),
    )
    return agg


def build_sales_site_sku_keys(sales_df: pd.DataFrame, item_map: pd.DataFrame) -> pd.DataFrame:
    """Distinct (site, SKU) pairs that have at least one sale in COMBINED_SALES."""
    x = sales_df.copy()
    x["site_key"] = normalize_site(x["Site_ID"])
    x["barcode_key"] = normalize_barcode(x["Barcode"])

    sales_sku = _clean_sku(x["SKU"]) if "SKU" in x.columns else pd.Series("", index=x.index)
    if not item_map.empty and "barcode_key" in item_map.columns and "SKU" in item_map.columns:
        im = item_map[["barcode_key", "SKU"]].drop_duplicates(subset=["barcode_key"], keep="first")
        im = im.rename(columns={"SKU": "_sku_item"})
        x = x.merge(im, on="barcode_key", how="left")
        item_sku = _clean_sku(x["_sku_item"].fillna(""))
        x["sku_key"] = sales_sku.where(sales_sku.ne(""), item_sku)
        x = x.drop(columns=["_sku_item"], errors="ignore")
    else:
        x["sku_key"] = sales_sku

    x = x[x["sku_key"].astype(str).str.len() > 0]
    return x[["site_key", "sku_key"]].drop_duplicates()


def build_site_name_lookup(sales_df: pd.DataFrame) -> pd.DataFrame:
    x = sales_df.copy()
    x["site_key"] = normalize_site(x["Site_ID"])
    if "Site_Name" not in x.columns:
        return pd.DataFrame(columns=["site_key", "Site_Name"])
    return (
        x.groupby("site_key", as_index=False)
        .agg(Site_Name=("Site_Name", "first"))
    )


def build_transfer_max_dc(transfer_df: pd.DataFrame) -> pd.DataFrame:
    t = transfer_df.copy()
    t["site_key"] = normalize_site(t["site_id"])
    t["barcode_key"] = normalize_barcode(t["Barcode"])
    t["_dc"] = _parse_mixed_date(t["Transfer_Date"])
    t["_inv_qty"] = pd.to_numeric(t["Quantity"], errors="coerce").fillna(0)

    return (
        t[t["site_key"].astype(str).str.len() > 0]
        .groupby(["site_key", "barcode_key"], dropna=False, as_index=False)
        .agg(
            max_dc_date=("_dc", "max"),
            transfer_invoice_qty_sum=("_inv_qty", "sum"),
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "SOH>0 & site+SKU never sold in combined sales; "
            "max transfer date + days to reference (output stays barcode-level)"
        )
    )
    parser.add_argument("--out", type=Path, default=SOH_NULL_OUT, help="Output CSV path")
    parser.add_argument(
        "--reference-date",
        type=str,
        default=None,
        help="As-of date for day count (YYYY-MM-DD). Default: today's date.",
    )
    args = parser.parse_args()

    require_combined_inputs()
    ref_str = args.reference_date or get_as_of_date()
    reference = pd.to_datetime(ref_str, errors="raise").normalize()
    print(f"Reference date (as-of): {reference.date()}", flush=True)

    store = load_combined_soh()
    try:
        from pipeline_inputs import filter_soh_by_source, filter_df_by_store_type
    except ImportError:
        from pipeline.pipeline_inputs import (  # type: ignore
            filter_soh_by_source,
            filter_df_by_store_type,
        )

    store = filter_soh_by_source(store, log_prefix="")
    try:
        from pipeline_inputs import derive_store_type, get_selected_store_types
    except ImportError:
        from pipeline.pipeline_inputs import derive_store_type, get_selected_store_types  # type: ignore

    selected = get_selected_store_types()
    sales = load_combined_sales()
    if selected and "Site_ID" in sales.columns and "Site_Name" in sales.columns:
        before = len(sales)
        sales = sales.copy()
        sales["_st"] = [
            derive_store_type(n, s) for n, s in zip(sales["Site_Name"], sales["Site_ID"])
        ]
        sales = sales[sales["_st"].isin(selected)].drop(columns=["_st"])
        print(f"soh_null: sales filtered {before:,} -> {len(sales):,}", flush=True)

    transfer = load_combined_transfer()
    if selected and "Channel" in transfer.columns:
        before_t = len(transfer)
        transfer = transfer[transfer["Channel"].astype(str).str.upper().isin(selected)].copy()
        print(f"soh_null: transfer filtered {before_t:,} -> {len(transfer):,}", flush=True)

    item_map = build_item_map()
    soh = build_store_soh(store)
    # Attach SKU early — no-sales gate is site+SKU
    if not item_map.empty:
        soh = soh.merge(
            item_map[["barcode_key", "MRP", "SKU", "SIZE"]],
            on="barcode_key",
            how="left",
        )
    else:
        soh["MRP"] = pd.NA
        soh["SKU"] = ""
        soh["SIZE"] = ""
    soh["sku_key"] = _clean_sku(soh["SKU"].fillna(""))
    soh.loc[soh["sku_key"].eq(""), "SKU"] = ""

    sales_keys = build_sales_site_sku_keys(sales, item_map)
    print(
        f"soh_null: distinct site+SKU with sales: {len(sales_keys):,} "
        f"(no-sales rule is site+SKU, not site+barcode)",
        flush=True,
    )
    site_names = build_site_name_lookup(sales)
    xfer = build_transfer_max_dc(transfer)

    # Keep barcode rows only when that site+SKU never sold (any size/barcode)
    has_sku = soh["sku_key"].astype(str).str.len() > 0
    merged = soh.merge(
        sales_keys.assign(_in_sales=True),
        on=["site_key", "sku_key"],
        how="left",
    )
    result = merged[
        (merged["store_soh_qty"] > 0) & has_sku & merged["_in_sales"].isna()
    ].drop(columns=["_in_sales"], errors="ignore")

    result = result.merge(xfer, on=["site_key", "barcode_key"], how="left")
    result = result.merge(site_names, on="site_key", how="left")

    result["reference_date"] = reference.strftime("%Y-%m-%d")
    result["days_since_last_transfer"] = (
        reference - pd.to_datetime(result["max_dc_date"], errors="coerce")
    ).dt.days
    result["Dead_Stock_Status"] = "active"
    result.loc[result["days_since_last_transfer"] > 35, "Dead_Stock_Status"] = "dead"

    result = result.rename(
        columns={
            "site_key": "Site_ID",
            "barcode_key": "Barcode",
            "store_soh_qty": "Store_Soh",
            "Site_Name": "Site",
            "max_dc_date": "Last_Transfer_Date",
            "transfer_invoice_qty_sum": "Total_Qty_Purchase",
            "SIZE": "Size",
        }
    )
    result = result.drop(columns=["sku_key"], errors="ignore")

    _ltd = pd.to_datetime(result["Last_Transfer_Date"], errors="coerce")
    result["Last_Transfer_Date"] = _ltd.dt.strftime("%Y-%m-%d").fillna("")

    wh_map = build_warehouse_soh_map()
    if not wh_map.empty:
        result = result.merge(wh_map, left_on="Barcode", right_on="barcode_key", how="left")
        result = result.drop(columns=["barcode_key"], errors="ignore")
    result["wh_soh"] = pd.to_numeric(result.get("wh_soh", 0), errors="coerce").fillna(0)

    result["Store_Soh"] = pd.to_numeric(result["Store_Soh"], errors="coerce").fillna(0)
    result["MRP"] = pd.to_numeric(result.get("MRP", 0), errors="coerce")
    result["Total_Amt"] = result["Store_Soh"] * result["MRP"]
    result = enrich_with_customer_master(result, site_id_col="Site_ID")

    # enrich_with_customer_master may already add store_grade; use suffixes to avoid _x/_y.
    grade_map = build_grade_map()
    if not grade_map.empty:
        result["_grade_site_key"] = normalize_site(result["Site_ID"])
        result = result.merge(grade_map, on="_grade_site_key", how="left", suffixes=("", "_sl"))
        result = result.drop(columns=["_grade_site_key"], errors="ignore")
        if "store_grade_sl" in result.columns:
            if "store_grade" not in result.columns:
                result["store_grade"] = result["store_grade_sl"]
            else:
                blank = result["store_grade"].astype(str).str.strip().isin(["", "nan", "None"])
                result.loc[blank, "store_grade"] = result.loc[blank, "store_grade_sl"]
            result = result.drop(columns=["store_grade_sl"])
    if "store_grade" not in result.columns:
        result["store_grade"] = "None"
    else:
        result["store_grade"] = result["store_grade"].fillna("None")
        result["store_grade"] = (
            result["store_grade"].astype(str).str.strip().replace({"": "None", "nan": "None"})
        )

    # enrich uses Site_Name to derive store_type; soh_null renamed that to Site.
    if "store_type" not in result.columns:
        if "Source" in result.columns:
            result["store_type"] = result["Source"].astype(str).str.upper().str.strip()
        else:
            site_names_s = result["Site"] if "Site" in result.columns else pd.Series("", index=result.index)
            result["store_type"] = [
                derive_store_type(n, s) for n, s in zip(site_names_s, result["Site_ID"])
            ]
        if "channel" in result.columns:
            blank = result["store_type"].astype(str).str.strip().eq("")
            result.loc[blank, "store_type"] = (
                result.loc[blank, "channel"].fillna("").astype(str).str.upper().str.strip()
            )
    for c in ("city", "state", "Site", "SKU", "Size"):
        if c not in result.columns:
            result[c] = ""

    result = result.sort_values(["Site_ID", "SKU", "Barcode"])
    result = filter_df_by_store_type(result, col="store_type", log_prefix="")
    result = result[
        [
            "Site_ID",
            "Barcode",
            "Store_Soh",
            "wh_soh",
            "Site",
            "city",
            "store_type",
            "state",
            "store_grade",
            "Last_Transfer_Date",
            "Total_Qty_Purchase",
            "SKU",
            "Size",
            "MRP",
            "days_since_last_transfer",
            "Dead_Stock_Status",
            "Total_Amt",
        ]
    ]

    args.out.parent.mkdir(parents=True, exist_ok=True)
    try:
        from pipeline_inputs import save_output_pair
    except ImportError:
        from pipeline.pipeline_inputs import save_output_pair  # type: ignore
    save_output_pair(result, args.out, utf8_sig=True)
    print(
        f"Rows (store_soh>0 & site+SKU not in combined sales): {len(result):,}"
    )
    print(f"Reference date: {reference.date()}")
    print(f"Saved: {args.out}")


if __name__ == "__main__":
    main()

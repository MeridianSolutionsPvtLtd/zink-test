"""
Build store_soh_new.csv from combined pipeline files (SELECTED store types only).

Sources:
  - output_files/COMBINED_SOH.xlsx   (already selected channels from combine_soh)
  - output_files/COMBINED_SALES.xlsx (filter to selected store_type)
  - item master (SIZE + SKU)

Output: output_files/store_soh_new.csv  (+ published to blob output/)
"""

from __future__ import annotations

import re

import pandas as pd

from pipeline_inputs import (
    STORE_SOH_NEW_OUT,
    build_item_map,
    derive_store_type,
    filter_soh_by_source,
    get_selected_store_types,
    load_combined_sales,
    load_combined_soh,
    normalize_barcode,
    normalize_site,
    require_combined_inputs,
)


def main() -> None:
    require_combined_inputs()

    selected = get_selected_store_types()
    store = filter_soh_by_source(load_combined_soh(), selected=selected or None, log_prefix="")
    sales = load_combined_sales()
    if selected and not sales.empty and "Site_ID" in sales.columns:
        # COMBINED_SALES may still hold mixed channels in shared workbook path
        name_col = "Site_Name" if "Site_Name" in sales.columns else None
        if name_col:
            before = len(sales)
            sales = sales.copy()
            sales["_st"] = [
                derive_store_type(n, s)
                for n, s in zip(sales[name_col], sales["Site_ID"])
            ]
            sales = sales[sales["_st"].isin(selected)].drop(columns=["_st"])
            print(
                f"store_soh_new: sales filtered to {sorted(selected)}: "
                f"{before:,} -> {len(sales):,}",
                flush=True,
            )
        print(f"store_soh_new: SOH Source filter selected={sorted(selected) or ['ALL']}", flush=True)

    item_map = build_item_map().rename(columns={"barcode_key": "_barcode_key"})

    store = store.copy()
    store["_site_key"] = normalize_site(store["Site_id"])
    store["_barcode_key"] = normalize_barcode(store["Barcode"])
    store["_qty_agg"] = pd.to_numeric(store["Current Stock Qty"], errors="coerce").fillna(0)

    skip = {"_site_key", "_barcode_key", "_qty_agg", "Current Stock Qty"}
    cols_first = [c for c in store.columns if c not in skip]
    agg: dict[str, str] = {"_qty_agg": "sum"}
    for c in cols_first:
        agg[str(c)] = "first"

    base = (
        store.groupby(["_site_key", "_barcode_key"], as_index=False, dropna=False)
        .agg(agg)
        .rename(columns={"_qty_agg": "Current_Stock_Qty"})
    )
    base = base[
        (base["_site_key"].astype(str).str.len() > 0)
        & (base["_barcode_key"].astype(str).str.len() > 0)
    ]

    # Site name from sales (optional display column)
    if "Site_Name" in sales.columns:
        sales_names = (
            sales.assign(_site_key=normalize_site(sales["Site_ID"]))
            .groupby("_site_key", as_index=False)
            .agg(Site_Name=("Site_Name", "first"))
        )
        base = base.merge(sales_names, on="_site_key", how="left")

    # Sales totals per site+barcode
    sales_map = sales.copy()
    sales_map["_site_key"] = normalize_site(sales_map["Site_ID"])
    sales_map["_barcode_key"] = normalize_barcode(sales_map["Barcode"])
    qty_col = "Qty_Sold" if "Qty_Sold" in sales_map.columns else None
    if qty_col is None:
        raise ValueError("COMBINED_SALES missing Qty_Sold")
    sales_map["_qty_sold"] = pd.to_numeric(sales_map[qty_col], errors="coerce").fillna(0)
    sales_map = sales_map[
        (sales_map["_site_key"].astype(str).str.len() > 0)
        & (sales_map["_barcode_key"].astype(str).str.len() > 0)
    ]
    sales_map = sales_map.groupby(["_site_key", "_barcode_key"], as_index=False).agg(
        Sum_of_Sales=("_qty_sold", "sum")
    )

    out = base.merge(item_map, on="_barcode_key", how="left")
    out = out.merge(sales_map, on=["_site_key", "_barcode_key"], how="left")
    out["Sum_of_Sales"] = pd.to_numeric(out["Sum_of_Sales"], errors="coerce").fillna(0)

    out = out.drop(columns=["_site_key", "_barcode_key"], errors="ignore")
    out = out.rename(columns={c: re.sub(r"\s+", "_", str(c).strip()) for c in out.columns})

    STORE_SOH_NEW_OUT.parent.mkdir(parents=True, exist_ok=True)
    try:
        from pipeline_inputs import save_output_pair
    except ImportError:
        from pipeline.pipeline_inputs import save_output_pair  # type: ignore
    save_output_pair(out, STORE_SOH_NEW_OUT, utf8_sig=True)
    print(f"Done. Saved: {STORE_SOH_NEW_OUT}")
    print(f"Rows: {len(out):,}")
    print("Columns:", list(out.columns))


if __name__ == "__main__":
    main()

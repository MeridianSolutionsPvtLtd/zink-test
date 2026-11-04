"""
Add SOH + grade columns to weekly_aggregated_sales_new.

Sources:
  - store_soh (current)      : output_files/COMBINED_SOH.xlsx  (selected channels only)
  - last_week_store_soh      : per-store input/.../history/*_YYYY-MM-DD.xlsx
                               (today-7, else nearest ±2 days; combined_soh fallback)
  - wh_soh / size / sku / store_grade : masters

Output: same weekly CSV (in place update).
"""

from pathlib import Path

import pandas as pd

from pipeline_inputs import (
    COMBINED_SOH,
    STORE_LIST,
    TOP_DOORS,
    warehouse_register_exists,
    build_item_map,
    build_warehouse_soh_map,
    find_column as _find_column,
    normalize_barcode,
    normalize_site,
)

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output_files"

WEEKLY_CSV = OUTPUT_DIR / "weekly_aggregated_sales_new.csv"


def _log(msg: str) -> None:
    print(msg, flush=True)


def _coalesce_text_from_master(existing: pd.Series | None, master: pd.Series, index: pd.Index) -> pd.Series:
    src = master.astype(str).str.strip().replace({"nan": "", "None": "", "<na>": ""})
    if existing is None:
        return src
    ex = existing.astype(str).str.strip().replace({"nan": "", "None": "", "<na>": ""})
    return ex.where(ex != "", src).reindex(index)


def _build_store_qty_map(soh_path: Path, qty_col: str) -> pd.DataFrame:
    """Read combined SOH xlsx → site+barcode qty map."""
    soh = pd.read_excel(soh_path)
    site = _find_column(soh, "Site_id", "site_id", "Site_ID")
    bc = _find_column(soh, "Barcode", "barcode")
    qty = _find_column(soh, "Current Stock Qty", "current_stock_qty", "store_qty")
    out = pd.DataFrame(
        {
            "_site_key": normalize_site(soh[site]),
            "_barcode_key": normalize_barcode(soh[bc]),
            qty_col: pd.to_numeric(soh[qty], errors="coerce").fillna(0),
        }
    )
    out = out[out["_barcode_key"].astype(str).str.len() > 0]
    return out.groupby(["_site_key", "_barcode_key"], as_index=False)[qty_col].sum()


def build_current_store_map() -> pd.DataFrame:
    """Current store_soh from COMBINED_SOH (csv preferred over stale xlsx)."""
    try:
        from pipeline_inputs import load_combined_soh
    except ImportError:
        from pipeline.pipeline_inputs import load_combined_soh  # type: ignore

    soh = load_combined_soh()
    site = _find_column(soh, "Site_id", "site_id", "Site_ID")
    bc = _find_column(soh, "Barcode", "barcode")
    qty = _find_column(soh, "Current Stock Qty", "current_stock_qty", "store_qty")
    out = pd.DataFrame(
        {
            "_site_key": normalize_site(soh[site]),
            "_barcode_key": normalize_barcode(soh[bc]),
            "store_qty": pd.to_numeric(soh[qty], errors="coerce").fillna(0),
        }
    )
    out = out[out["_barcode_key"].astype(str).str.len() > 0]
    return out.groupby(["_site_key", "_barcode_key"], as_index=False)["store_qty"].sum()


def build_last_week_store_map() -> pd.DataFrame:
    """Last-week store_soh from per-store history (today-7 / nearest ±2d)."""
    try:
        from store_soh_history import build_last_week_store_map_per_store
    except ImportError:
        from pipeline.store_soh_history import build_last_week_store_map_per_store  # type: ignore

    out, info = build_last_week_store_map_per_store()
    used = info.get("used") or []
    missing = info.get("still_missing") or []
    if used:
        for u in used:
            _log(f"      [last_week] {u}")
    if missing:
        _log(
            f"      [last_week] no history for {missing} "
            "→ last_week_store_soh = 0 for those stores"
        )
    if not used and not missing:
        _log(
            f"      [last_week] not found (target={info.get('target_date')}) "
            "— last_week_store_soh = 0"
        )
    return out


def build_wh_map() -> pd.DataFrame:
    wh = build_warehouse_soh_map()
    if wh.empty:
        return pd.DataFrame(columns=["_barcode_key", "wh_soh"])
    return wh.rename(columns={"barcode_key": "_barcode_key"})


def build_size_map() -> pd.DataFrame:
    item = build_item_map()
    out = item.rename(columns={"barcode_key": "_barcode_key", "SIZE": "size_src", "SKU": "sku_src"})
    return out[["_barcode_key", "size_src", "sku_src"]]


def build_grade_map() -> pd.DataFrame:
    """Grade from Store List June 26 (primary) + Top Doors List cross-check/fill."""
    def read_grades(path: Path) -> pd.DataFrame:
        if not path.exists():
            return pd.DataFrame(columns=["_site_key", "store_grade"])
        try:
            sl = pd.read_excel(path)
            site = _find_column(sl, "Site_Code", "site_code", "SITE CODE", "Site_ID", "Site_id", "site_id")
            grade = _find_column(sl, "Grade", "grade", "GRADE", "Store_Grade", "STORE_GRADE")
            g = pd.DataFrame(
                {
                    "_site_key": normalize_site(sl[site]),
                    "store_grade": sl[grade].astype(str).str.strip().replace({"nan": "", "None": "", "<na>": ""}),
                }
            )
            g = g[g["_site_key"] != ""]
            return g.drop_duplicates(subset=["_site_key"], keep="first")
        except Exception as exc:
            _log(f"      [grade warning] {path.name}: {exc}")
            return pd.DataFrame(columns=["_site_key", "store_grade"])

    sl_map = read_grades(STORE_LIST)
    td_map = read_grades(TOP_DOORS)

    if not sl_map.empty and not td_map.empty:
        merged = sl_map.merge(td_map, on="_site_key", how="outer", suffixes=("_sl", "_td"))
        mismatch = merged[
            (merged["store_grade_sl"].notna())
            & (merged["store_grade_td"].notna())
            & (merged["store_grade_sl"].astype(str) != merged["store_grade_td"].astype(str))
        ]
        if len(mismatch):
            _log(f"      [grade cross-check] {len(mismatch)} site(s) me Store List vs Top Doors grade alag:")
            for _, r in mismatch.head(20).iterrows():
                _log(f"        {r['_site_key']}: StoreList={r['store_grade_sl']!r} vs TopDoors={r['store_grade_td']!r}")
        # Store List primary; jaha Store List me nahi hai waha Top Doors se fill
        merged["store_grade"] = merged["store_grade_sl"].where(
            merged["store_grade_sl"].astype(str).str.strip().replace({"nan": ""}) != "",
            merged["store_grade_td"],
        )
        return merged[["_site_key", "store_grade"]]

    return sl_map if not sl_map.empty else td_map


def _combined_soh_exists() -> bool:
    return (
        COMBINED_SOH.exists()
        or COMBINED_SOH.with_suffix(".csv").exists()
        or (OUTPUT_DIR / "combined_soh" / "current" / "COMBINED_SOH.csv").exists()
        or (OUTPUT_DIR / "combined_soh" / "current" / "COMBINED_SOH.xlsx").exists()
    )


def main() -> None:
    weekly_path = None
    if WEEKLY_CSV.exists() and WEEKLY_CSV.stat().st_size > 0:
        weekly_path = WEEKLY_CSV
    elif WEEKLY_CSV.with_suffix(".xlsx").exists() and WEEKLY_CSV.with_suffix(".xlsx").stat().st_size > 0:
        weekly_path = WEEKLY_CSV.with_suffix(".xlsx")
    else:
        raise FileNotFoundError(
            f"Required file not found: {WEEKLY_CSV} or {WEEKLY_CSV.with_suffix('.xlsx')}. "
            "Please run 2.weekly_aggregate.py first."
        )
    if not _combined_soh_exists():
        raise FileNotFoundError(
            f"Required file not found: {COMBINED_SOH} or {COMBINED_SOH.with_suffix('.csv')}"
        )
    if not warehouse_register_exists():
        raise FileNotFoundError(
            "Required warehouse file not found: input_files/WAREHOUSE_SOH.csv or WAREHOUSE_SOH.xlsx"
        )

    _log(f"[1/4] reading weekly data ({weekly_path.name}) ...")
    if weekly_path.suffix.lower() == ".xlsx":
        weekly = pd.read_excel(weekly_path)
    else:
        weekly = pd.read_csv(weekly_path, low_memory=False)
    if "site_id" not in weekly.columns or "barcode" not in weekly.columns:
        raise ValueError("weekly CSV must contain 'site_id' and 'barcode' columns.")

    # Purani SOH columns hata do (store_type weekly ka accha hai -> preserve).
    weekly = weekly.drop(
        columns=["store_soh", "last_week_store_soh", "wh_soh", "store_grade"],
        errors="ignore",
    )
    weekly["_site_key"] = normalize_site(weekly["site_id"])
    weekly["_barcode_key"] = normalize_barcode(weekly["barcode"])

    _log("[2/4] building SOH / size / grade maps ...")
    current_map = build_current_store_map()
    last_week_map = build_last_week_store_map()
    wh_map = build_wh_map()
    size_map = build_size_map()
    grade_map = build_grade_map()

    _log("[3/4] merging ...")
    out = weekly.merge(current_map, on=["_site_key", "_barcode_key"], how="left")
    out = out.merge(last_week_map, on=["_site_key", "_barcode_key"], how="left")
    out = out.merge(wh_map, on="_barcode_key", how="left")
    out = out.merge(size_map, on="_barcode_key", how="left")
    if not grade_map.empty:
        out = out.merge(grade_map, on="_site_key", how="left")
        out["store_grade"] = out["store_grade"].fillna("None")
    else:
        out["store_grade"] = "None"

    out["store_soh"] = pd.to_numeric(out["store_qty"], errors="coerce").fillna(0)
    out["last_week_store_soh"] = pd.to_numeric(out["last_week_store_qty"], errors="coerce").fillna(0)
    out["wh_soh"] = pd.to_numeric(out["wh_soh"], errors="coerce").fillna(0)

    if "store_type" not in out.columns:
        out["store_type"] = ""
    out["size"] = _coalesce_text_from_master(
        out["size"] if "size" in out.columns else None, out["size_src"], out.index
    )
    out["sku"] = _coalesce_text_from_master(
        out["sku"] if "sku" in out.columns else None, out["sku_src"], out.index
    )

    out = out.drop(
        columns=["store_qty", "last_week_store_qty", "size_src", "sku_src", "_site_key", "_barcode_key"],
        errors="ignore",
    )

    # SOH + grade columns ko Sum_of_Sales ke baad rakho
    cols = list(out.columns)
    for c in ["store_soh", "last_week_store_soh", "wh_soh", "store_grade"]:
        if c in cols:
            cols.remove(c)
    insert_at = cols.index("Sum_of_Sales") + 1 if "Sum_of_Sales" in cols else len(cols)
    out = out[cols[:insert_at] + ["store_soh", "last_week_store_soh", "wh_soh", "store_grade"] + cols[insert_at:]]

    # CSV + XLSX (large weekly can be slow for xlsx — helper warns if xlsx fails)
    _log("[4/4] writing CSV + XLSX ...")
    try:
        from pipeline_inputs import save_output_pair
    except ImportError:
        from pipeline.pipeline_inputs import save_output_pair  # type: ignore
    save_output_pair(out, WEEKLY_CSV, sheet_name="weekly")

    _log(f"Done. Rows: {len(out):,}")
    _log(f"  store_soh > 0          : {(out['store_soh'] > 0).sum():,}")
    _log(f"  last_week_store_soh > 0: {(out['last_week_store_soh'] > 0).sum():,}")
    _log(f"  wh_soh > 0             : {(out['wh_soh'] > 0).sum():,}")
    _log(f"  store_grade filled     : {(~out['store_grade'].astype(str).str.strip().isin(['', 'None', 'nan'])).sum():,}")
    _log(f"  size non-empty         : {(out['size'].astype(str).str.strip() != '').sum():,}")
    if "store_type" in out.columns:
        _log("  last_week_store_soh by store_type:")
        tmp = out.copy()
        tmp["_lw"] = tmp["last_week_store_soh"] > 0
        _log(tmp.groupby("store_type")["_lw"].sum().to_string())


if __name__ == "__main__":
    main()

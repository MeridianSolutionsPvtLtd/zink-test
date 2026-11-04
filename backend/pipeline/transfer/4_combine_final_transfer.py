"""
4_combine_final_transfer.py
───────────────────────────
Combines main stack transfers (from 2_recommend_transfers.py) and broken-size
transfers (from 3_broken_size_transfer.py) into one final sizewise CSV + XLSX.

Run after:
  python transfer/1_build_potential_donors_receivers.py
  python transfer/2_recommend_transfers.py
  python transfer/3_broken_size_transfer.py

Output:
  transfer/output/final_transfer_sizewise.csv
  transfer/output/final_transfer_sizewise.xlsx
"""
from pathlib import Path
import os
import sys

import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]
OUT_DIR  = BASE_DIR / "transfer" / "output"

IN_MAIN   = OUT_DIR / "recommended_transfers_sizewise.csv"
IN_BROKEN = OUT_DIR / "broken_size_transfers.csv"
OUT_CSV   = OUT_DIR / "final_transfer_sizewise.csv"
OUT_XLSX  = OUT_DIR / "final_transfer_sizewise.xlsx"
TRANSFER_CURRENT = BASE_DIR / "output_files" / "transfer" / "current" / "final_transfer_sizewise.csv"
TRANSFER_HISTORY = BASE_DIR / "output_files" / "transfer" / "history"

# Grade lookup sources
_PRED_CANDIDATES = [
    BASE_DIR / "output_files" / "prediction" / "current" / "SS26_weekly_predictions_formula_next_week_with_refill_recommendation.csv",
    BASE_DIR / "output_files" / "SS26_weekly_predictions_formula_next_week_with_refill_recommendation.csv",
]
PREDICTION_FILE = next((p for p in _PRED_CANDIDATES if p.exists()), _PRED_CANDIDATES[-1])
SOH_NULL_FILE   = BASE_DIR / "output_files" / "soh_null_no_sales_transfer_days.csv"


def _build_grade_map() -> dict[str, str]:
    grade_map: dict[str, str] = {}

    if PREDICTION_FILE.exists():
        pred = pd.read_csv(PREDICTION_FILE, low_memory=False, usecols=["site_id", "store_grade"])
        pred["site_id"]     = pred["site_id"].astype(str).str.strip().str.upper()
        pred["store_grade"] = pred["store_grade"].astype(str).str.strip().replace({"nan": "", "None": ""})
        grade_map.update(
            pred.drop_duplicates("site_id").set_index("site_id")["store_grade"].to_dict()
        )

    if SOH_NULL_FILE.exists():
        soh = pd.read_csv(SOH_NULL_FILE, low_memory=False, usecols=["Site_ID", "store_grade"])
        soh["Site_ID"]     = soh["Site_ID"].astype(str).str.strip().str.upper()
        soh["store_grade"] = soh["store_grade"].astype(str).str.strip().replace({"nan": "", "None": ""})
        for _, row in soh.drop_duplicates("Site_ID").iterrows():
            if not grade_map.get(row["Site_ID"]):
                grade_map[row["Site_ID"]] = row["store_grade"]

    return grade_map


FINAL_COLS = [
    "receiver_site_name",
    "receiver_city",
    "receiver_state",
    "receiver_store_grade",
    "donor_site_name",
    "donor_city",
    "donor_state",
    "donor_store_grade",
    "sku",
    "size",
    "barcode",
    "required_qty",
    "transfer_qty",
    "receiver_soh",
    "donor_soh_before",
    "priority_score",
    "receiver_last_week_sales",
    "receiver_sold_qty_35d",
    "receiver_total_sales",
    "donor_last_week_sales",
    "donor_sold_qty_35d",
    "donor_total_sales",
    "city_match",
    "state_match",
    "other_state",
    "round",
    "status",
    "created_at",
    "receiver_site_id",
    "donor_site_id",
]

TYPE_ORDER = {
    "STACK":               0,
    "BROKEN_STACK":        1,
    "BROKEN_NEW_RECEIVER": 2,
    "BROKEN_DESIGNATED":   3,
}


def _read_csv_or_empty(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, low_memory=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def main() -> None:
    if not IN_MAIN.exists() and not IN_BROKEN.exists():
        raise FileNotFoundError(
            "Run transfer steps 2 and 3 first.\n"
            f"Missing: {IN_MAIN} and {IN_BROKEN}"
        )

    # ── Load ──────────────────────────────────────────────────────────────────
    if IN_MAIN.exists():
        main_df = _read_csv_or_empty(IN_MAIN)
    else:
        print(f"[warn] {IN_MAIN.name} missing — treating main stack transfers as empty.", flush=True)
        main_df = pd.DataFrame()

    if IN_BROKEN.exists():
        broken_df = _read_csv_or_empty(IN_BROKEN)
    else:
        print(f"[warn] {IN_BROKEN.name} missing — treating broken transfers as empty.", flush=True)
        broken_df = pd.DataFrame()

    # Drop ISOLATED_SIZE — nothing actually moves
    if not broken_df.empty and "transfer_type" in broken_df.columns:
        broken_df = broken_df[broken_df["transfer_type"] != "ISOLATED_SIZE"].copy()

    # ── Align main (stack) transfers ──────────────────────────────────────────
    if not main_df.empty:
        main_df["transfer_type"] = "STACK"
        if "donor_soh" in main_df.columns and "donor_soh_before" not in main_df.columns:
            main_df = main_df.rename(columns={"donor_soh": "donor_soh_before"})

    # ── Align broken transfers ────────────────────────────────────────────────
    if not broken_df.empty:
        if "required_qty"             not in broken_df.columns:
            broken_df["required_qty"]             = broken_df["transfer_qty"]
        if "round"                    not in broken_df.columns:
            broken_df["round"]                    = ""
        if "receiver_last_week_sales" not in broken_df.columns:
            broken_df["receiver_last_week_sales"] = ""
        if "receiver_sold_qty_35d"    not in broken_df.columns:
            broken_df["receiver_sold_qty_35d"]    = ""
        if "receiver_total_sales"     not in broken_df.columns:
            broken_df["receiver_total_sales"]     = ""
        if "priority_score"           not in broken_df.columns:
            broken_df["priority_score"]           = broken_df.get("receiver_priority_score", "")

    # Ensure all final columns exist in both (keep transfer_type for sorting)
    cols_to_keep = FINAL_COLS + ["transfer_type"]
    for df in (main_df, broken_df):
        if df.empty:
            continue
        for c in cols_to_keep:
            if c not in df.columns:
                df[c] = ""

    parts = [df for df in (main_df, broken_df) if not df.empty]
    if parts:
        combined = pd.concat([df[cols_to_keep] for df in parts], ignore_index=True)
    else:
        combined = pd.DataFrame(columns=cols_to_keep)

    # ── Filter to selected replenishment store types (receiver side) ──────────
    try:
        from pipeline_inputs import get_selected_store_types
        selected = sorted(get_selected_store_types())
    except ImportError:
        try:
            from pipeline.pipeline_inputs import get_selected_store_types
            selected = sorted(get_selected_store_types())
        except ImportError:
            selected = [
                t.strip().upper()
                for t in (os.environ.get("PIPELINE_STORE_TYPES") or "").split(",")
                if t.strip()
            ]
    if selected and PREDICTION_FILE.exists():
        pred = pd.read_csv(PREDICTION_FILE, low_memory=False, usecols=lambda c: c.lower() in {"site_id", "store_type"})
        pred.columns = pred.columns.str.strip()
        site_col = "site_id" if "site_id" in pred.columns else pred.columns[0]
        type_col = "store_type" if "store_type" in pred.columns else None
        if type_col:
            pred[site_col] = pred[site_col].astype(str).str.strip().str.upper()
            pred[type_col] = pred[type_col].astype(str).str.strip().str.upper()
            allowed_sites = set(
                pred.loc[pred[type_col].isin(selected), site_col].tolist()
            )
            before = len(combined)
            combined["receiver_site_id"] = combined["receiver_site_id"].astype(str).str.strip().str.upper()
            combined = combined[combined["receiver_site_id"].isin(allowed_sites)].copy()
            print(
                f"Filtered transfers to selected store_types={selected}: "
                f"{before:,} -> {len(combined):,} rows (by receiver site)",
                flush=True,
            )

    # ── Enrich store_grade from lookup ────────────────────────────────────────
    grade_map = _build_grade_map()
    if grade_map:
        for site_col, grade_col in [("receiver_site_id", "receiver_store_grade"),
                                     ("donor_site_id",    "donor_store_grade")]:
            if grade_col in combined.columns:
                combined[site_col] = combined[site_col].astype(str).str.strip().str.upper()
                blank = combined[grade_col].astype(str).str.strip().isin(["", "nan", "None"])
                combined.loc[blank, grade_col] = (
                    combined.loc[blank, site_col].map(grade_map).fillna("")
                )

    # ── Sort ──────────────────────────────────────────────────────────────────
    combined["_sort"] = combined["transfer_type"].map(TYPE_ORDER).fillna(9)
    combined = (
        combined
        .sort_values(["_sort", "receiver_site_id", "sku", "size"])
        .drop(columns=["_sort"])
        .reset_index(drop=True)
    )

    # ── Rename donor_soh_before → donor_soh for output ────────────────────────
    if "donor_soh_before" in combined.columns:
        combined = combined.rename(columns={"donor_soh_before": "donor_soh"})
        FINAL_COLS_OUT = [c.replace("donor_soh_before", "donor_soh") for c in FINAL_COLS]
    else:
        FINAL_COLS_OUT = FINAL_COLS

    try:
        from sku_scope import is_remapped_scope, remapped_sku_names, merge_prior_other_skus
    except ImportError:
        sys.path.insert(0, str(BASE_DIR))
        from sku_scope import (  # type: ignore
            is_remapped_scope,
            remapped_sku_names,
            merge_prior_other_skus,
        )
    if is_remapped_scope():
        names = remapped_sku_names()
        prior = TRANSFER_CURRENT if TRANSFER_CURRENT.exists() else OUT_CSV
        combined = merge_prior_other_skus(combined, prior, names=names)

    # ── Save ──────────────────────────────────────────────────────────────────
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for c in FINAL_COLS_OUT:
        if c not in combined.columns:
            combined[c] = ""
    combined[FINAL_COLS_OUT].to_csv(OUT_CSV, index=False, encoding="utf-8-sig")
    try:
        combined[FINAL_COLS_OUT].to_excel(OUT_XLSX, index=False)
        xlsx_saved = OUT_XLSX
    except PermissionError:
        fallback = OUT_DIR / "final_transfer_sizewise_new.xlsx"
        combined[FINAL_COLS_OUT].to_excel(fallback, index=False)
        xlsx_saved = fallback
        print(f"[WARN] XLSX was open — saved as: {fallback.name}")

    # ── Summary ───────────────────────────────────────────────────────────────
    total_units = int(
        pd.to_numeric(combined["transfer_qty"], errors="coerce").fillna(0).sum()
    )
    r_grade = int((combined["receiver_store_grade"].astype(str).str.strip() != "").sum()) if "receiver_store_grade" in combined.columns else 0
    d_grade = int((combined["donor_store_grade"].astype(str).str.strip() != "").sum()) if "donor_store_grade" in combined.columns else 0

    print("=" * 55)
    print("FINAL TRANSFER FILE — SUMMARY")
    print("=" * 55)
    print(f"Total rows        : {len(combined):,}")
    print(f"Total units moved : {total_units:,}")
    print(f"Columns included  : {len(FINAL_COLS_OUT)}")
    print()
    for t_type, count in combined["transfer_type"].value_counts().items():
        print(f"  {t_type:<28}: {count:,}")
    print()
    if "receiver_store_grade" in combined.columns:
        print(f"receiver_store_grade filled : {r_grade:,} / {len(combined):,}")
    if "donor_store_grade" in combined.columns:
        print(f"donor_store_grade filled    : {d_grade:,} / {len(combined):,}")
    print()
    print(f"Saved CSV  -> {OUT_CSV}")
    print(f"Saved XLSX -> {xlsx_saved}")

    try:
        from dated_output_store import publish_with_history

        publish_with_history(
            OUT_CSV,
            current_path=TRANSFER_CURRENT,
            history_dir=TRANSFER_HISTORY,
            history_basename="final_transfer_sizewise",
            history_ext=".csv",
            keep=5,
            also_copy_to=[OUT_CSV],
        )
    except Exception as e:
        print(f"[warn] transfer history publish failed: {e}", flush=True)

    print("=" * 55)


if __name__ == "__main__":
    main()

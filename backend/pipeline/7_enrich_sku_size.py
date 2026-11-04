"""
7_enrich_sku_size.py

Fill blank sku / size on replenishment (refill) output from Item Master by barcode.
Runs after 6.build_refill.py.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pandas as pd

from pipeline_inputs import build_item_map, normalize_barcode

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output_files"

# Prefer prediction/current, fall back to legacy flat path
CANDIDATES = [
    OUTPUT_DIR / "prediction" / "current" / "SS26_weekly_predictions_formula_next_week_with_refill_recommendation.csv",
    OUTPUT_DIR / "SS26_weekly_predictions_formula_next_week_with_refill_recommendation.csv",
]


def _blank_mask(series: pd.Series) -> pd.Series:
    s = series.astype(str).str.strip()
    return s.isna() | s.isin(["", "nan", "None", "<NA>", "NaN"])


def _safe_to_csv(df: pd.DataFrame, path: Path, *, retries: int = 5) -> None:
    """Write via temp file then replace — avoids Windows file-lock PermissionError."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp.{os.getpid()}")
    last_err: Exception | None = None
    try:
        df.to_csv(tmp, index=False)
        for attempt in range(retries):
            try:
                os.replace(tmp, path)
                return
            except PermissionError as e:
                last_err = e
                time.sleep(0.6 * (attempt + 1))
        # Last resort: alternate filename if still locked
        alt = path.with_name(path.stem + "_enriched" + path.suffix)
        os.replace(tmp, alt)
        print(
            f"[warn] {path.name} locked — wrote {alt.name} instead. "
            "Close Excel/files locking the original if needed.",
            flush=True,
        )
        return
    finally:
        if tmp.exists():
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
    if last_err:
        raise last_err


def main() -> None:
    path = next((p for p in CANDIDATES if p.exists()), None)
    if path is None:
        raise FileNotFoundError(
            "Refill prediction CSV not found. Run 6.build_refill.py first.\n"
            f"Tried: {CANDIDATES}"
        )

    print(f"[1/3] reading {path.name} ...", flush=True)
    df = pd.read_csv(path, low_memory=False)
    df.columns = df.columns.str.strip()

    # Normalize column names for sku/size
    col_map = {c.lower(): c for c in df.columns}
    sku_col = col_map.get("sku") or col_map.get("sku_code")
    size_col = col_map.get("size")
    if sku_col is None:
        df["sku"] = ""
        sku_col = "sku"
    if size_col is None:
        df["size"] = ""
        size_col = "size"

    bc_col = col_map.get("barcode")
    if bc_col is None:
        raise ValueError("prediction file missing barcode column")

    print("[2/3] loading item master map ...", flush=True)
    item = build_item_map()
    item = item.rename(columns={"SKU": "_im_sku", "SIZE": "_im_size"})
    df["_barcode_key"] = normalize_barcode(df[bc_col])
    merged = df.merge(
        item[["barcode_key", "_im_sku", "_im_size"]],
        left_on="_barcode_key",
        right_on="barcode_key",
        how="left",
    )

    sku_blank = _blank_mask(merged[sku_col])
    size_blank = _blank_mask(merged[size_col])
    filled_sku = int(sku_blank.sum())
    filled_size = int(size_blank.sum())
    merged.loc[sku_blank, sku_col] = merged.loc[sku_blank, "_im_sku"].fillna("")
    merged.loc[size_blank, size_col] = merged.loc[size_blank, "_im_size"].fillna("")

    out = merged.drop(
        columns=["_barcode_key", "barcode_key", "_im_sku", "_im_size"],
        errors="ignore",
    )

    print("[3/3] writing enriched file (CSV + XLSX) ...", flush=True)
    try:
        from pipeline_inputs import save_output_pair
    except ImportError:
        from pipeline.pipeline_inputs import save_output_pair  # type: ignore
    save_output_pair(out, path)
    # Keep compat flat path in sync if we edited prediction/current
    compat = OUTPUT_DIR / "SS26_weekly_predictions_formula_next_week_with_refill_recommendation.csv"
    if path.resolve() != compat.resolve():
        save_output_pair(out, compat)

    # Refresh history stamp for today with enriched content
    try:
        from dated_output_store import publish_with_history

        publish_with_history(
            path,
            current_path=OUTPUT_DIR
            / "prediction"
            / "current"
            / "SS26_weekly_predictions_formula_next_week_with_refill_recommendation.csv",
            history_dir=OUTPUT_DIR / "prediction" / "history",
            history_basename="SS26_weekly_predictions_formula_next_week_with_refill_recommendation",
            history_ext=".csv",
            keep=5,
            also_copy_to=[compat],
        )
    except Exception as e:
        print(f"[warn] history republish skipped: {e}", flush=True)

    print(
        f"Done. Filled blank sku~{filled_sku} size~{filled_size} rows from item master. Saved: {path}",
        flush=True,
    )


if __name__ == "__main__":
    main()

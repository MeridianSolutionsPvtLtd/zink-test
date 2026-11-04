"""
Combine EBO, SSL, PT, ICONIC, and CENTRO SOH files into one unified file.

Usage:
    python combine_soh.py --show-columns    # har file ke columns dikhao
    python combine_soh.py --show-mapping    # pipeline mapping dikhao
    python combine_soh.py                   # combine karke output_files/ mein save karo
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
INPUT_DIR = BASE_DIR / "input_files"
OUTPUT_DIR = BASE_DIR / "output_files"
OUTPUT_FILE = OUTPUT_DIR / "COMBINED_SOH.xlsx"

# ── Source file config: (fixed_name, legacy_names, sheet, header_row, column_map) ─
SOH_SOURCES: dict[str, tuple[str, tuple[str, ...], str, int, dict[str, str]]] = {
    "EBO": (
        "EBO_SOH.xlsx",
        ("@EBO CLOSING STOCK REGISTER 9th july.xlsx",),
        "EBO CLOSING STOCK REGISTER_SP@C",
        3,
        {
            "MANUAL CODE": "Site_id",
            "SITE CODE": "Site_id",
            "SITE_CODE": "Site_id",
            "Store Code": "Site_id",
            "STORE CODE": "Site_id",
            "SITE SHORT CODE": "Site_id",
            "Site_id": "Site_id",
            "Site": "Site_id",
            "BARCODE": "Barcode",
            "Barcode": "Barcode",
            "BAR CODE": "Barcode",
            "EAN": "Barcode",
            "EAN_CODE": "Barcode",
            "EAN CODE": "Barcode",
            "CLOSING_STOCK_QTY": "Current Stock Qty",
            "CLOSING STOCK QTY": "Current Stock Qty",
            "Closing Stock Qty": "Current Stock Qty",
            "Current Stock Qty": "Current Stock Qty",
            "CURRENT_STOCK_QTY": "Current Stock Qty",
            "QTY": "Current Stock Qty",
            "Quantity": "Current Stock Qty",
            "STOCK_QTY": "Current Stock Qty",
            "Total Stock Quantity": "Current Stock Qty",
        },
    ),
    "SSL": (
        "SHOPPERS_STOP_SOH.xlsx",
        ("shopper stop soh 9th.xlsx",),
        "StockDetails.rdl",
        0,
        {
            "Store No.": "Site_id",
            "STORE NO": "Site_id",
            "STORE CODE": "Site_id",
            "Store Code": "Site_id",
            "Site_id": "Site_id",
            "Site": "Site_id",
            "GS1 NUMBER": "Barcode",
            "GS1_NUMBER": "Barcode",
            "BARCODE": "Barcode",
            "Barcode": "Barcode",
            "EAN": "Barcode",
            "EAN_CODE": "Barcode",
            "Qty Available": "Current Stock Qty",
            "QTY AVAILABLE": "Current Stock Qty",
            "Available Qty": "Current Stock Qty",
            "Current Stock Qty": "Current Stock Qty",
            "CLOSING_STOCK_QTY": "Current Stock Qty",
            "QTY": "Current Stock Qty",
        },
    ),
    "PT": (
        "PANTALOONS_SOH.xlsx",
        ("Pantaloons soh 9th.xlsx",),
        "Weekly Stock Report",
        1,
        {
            "Site": "Site_id",
            "SITE": "Site_id",
            "Site Code": "Site_id",
            "SITE CODE": "Site_id",
            "Store Code": "Site_id",
            "Site_id": "Site_id",
            "Article EAN": "Barcode",
            "ARTICLE EAN": "Barcode",
            "Article Ean": "Barcode",
            "BARCODE": "Barcode",
            "Barcode": "Barcode",
            "EAN": "Barcode",
            "EAN_CODE": "Barcode",
            "Current Stock Qty": "Current Stock Qty",
            "CURRENT STOCK QTY": "Current Stock Qty",
            "Current Stock Qty.": "Current Stock Qty",
            "QTY": "Current Stock Qty",
            "Quantity": "Current Stock Qty",
            "CLOSING_STOCK_QTY": "Current Stock Qty",
        },
    ),
    "ICONIC": (
        "ICONIC_SOH.xlsx",
        ("Iconic soh 6th.xlsx",),
        "Sheet1",
        0,
        {
            "branch_code": "Site_id",
            "BRANCH_CODE": "Site_id",
            "Branch Code": "Site_id",
            "Site": "Site_id",
            "Site_id": "Site_id",
            "Store Code": "Site_id",
            "ean_code": "Barcode",
            "EAN_CODE": "Barcode",
            "Ean Code": "Barcode",
            "BARCODE": "Barcode",
            "Barcode": "Barcode",
            "EAN": "Barcode",
            "closing_stc_qty": "Current Stock Qty",
            "CLOSING_STC_QTY": "Current Stock Qty",
            "Closing Stock Qty": "Current Stock Qty",
            "Current Stock Qty": "Current Stock Qty",
            "QTY": "Current Stock Qty",
        },
    ),
    "CENTRO": (
        "CENTRO_SOH.xlsx",
        ("Centro soh 6th.xlsx",),
        "Sheet1",
        0,
        {
            "SITE": "Site_id",
            "Site": "Site_id",
            "Site_id": "Site_id",
            "SITE CODE": "Site_id",
            "Store Code": "Site_id",
            "EAN UPC": "Barcode",
            "EAN_UPC": "Barcode",
            "Ean Upc": "Barcode",
            "BARCODE": "Barcode",
            "Barcode": "Barcode",
            "EAN": "Barcode",
            "TOTAL_STOCK_QTY": "Current Stock Qty",
            "Total Stock Quantity": "Current Stock Qty",
            "Unrestricted Stock Quantity": "Current Stock Qty",
            "Current Stock Qty": "Current Stock Qty",
            "CLOSING_STOCK_QTY": "Current Stock Qty",
            "TOTAL_STOCK_VALUE": "Current Stock Qty",
            "Total Stock Value": "Current Stock Qty",
        },
    ),
}

PIPELINE_COLUMNS = ["Site_id", "Barcode", "Current Stock Qty"]


def _resolve_soh_path(fixed: str, legacy: tuple[str, ...]) -> Path:
    """
    Prefer merge-canonical CSV over raw XLSX when both exist.
    Raw EBO/PT Excel often has title rows; merge writes clean CSV with header on row 0.
    If both exist, pick the newest mtime.
    """
    stem = Path(fixed).stem
    names: list[str] = []
    for name in (f"{stem}.csv", fixed, *legacy):
        if name not in names:
            names.append(name)
    existing = [INPUT_DIR / n for n in names if (INPUT_DIR / n).exists()]
    if not existing:
        raise FileNotFoundError(
            f"Missing SOH file. Tried: {', '.join(names)}"
        )
    if len(existing) == 1:
        return existing[0]
    return max(existing, key=lambda p: p.stat().st_mtime)


def read_soh(source_label: str) -> pd.DataFrame:
    """Read one SOH source file (calamine + only mapped columns when possible)."""
    try:
        from pipeline_inputs import read_excel_fast
    except ImportError:
        from pipeline.pipeline_inputs import read_excel_fast  # type: ignore

    fixed, legacy, sheet, header_row, column_map = SOH_SOURCES[source_label]
    path = _resolve_soh_path(fixed, legacy)
    print(f"  reading {path.name} (header~{header_row}) ...", flush=True)
    hints = list(column_map.keys()) + [
        "Site_id",
        "Barcode",
        "Site",
        "Site ",
        "MANUAL CODE",
        "CLOSING_STOCK_QTY",
        "NAME",
        "SITE SHORT CODE",
    ]
    try:
        return read_excel_fast(
            path,
            sheet_name=sheet,
            column_hints=hints,
            preferred_header=header_row,
            preferred_sheet=sheet if isinstance(sheet, str) else None,
        )
    except Exception as exc:
        print(
            f"  [warn] {source_label} preferred sheet/header failed ({exc}); "
            f"retry with preferred_header={header_row}",
            flush=True,
        )
        return read_excel_fast(
            path,
            sheet_name=0,
            column_hints=hints,
            preferred_header=header_row,
            auto_sheet=True,
        )


def show_columns() -> None:
    """Print all columns from each SOH source file."""
    for label, (fixed, legacy, sheet, _, _) in SOH_SOURCES.items():
        path = _resolve_soh_path(fixed, legacy)
        df = read_soh(label)
        print("=" * 70)
        print(f"SOURCE: {label}")
        print(f"File  : {path.name}")
        print(f"Sheet : {sheet}")
        print(f"Rows  : {len(df):,}")
        print("=" * 70)
        for i, col in enumerate(df.columns, 1):
            print(f"  {i:2}. {col}")
        print()


def show_mapping() -> None:
    """Print mapping from each source to pipeline columns."""
    print("Pipeline columns:")
    print("  1. Site_id           - Site key")
    print("  2. Barcode           - Product key")
    print("  3. Current Stock Qty - store_soh (refill pehle store se)")
    print()
    print("-" * 70)
    print(f"{'Pipeline Column':<22} " + " ".join(f"{s:<18}" for s in SOH_SOURCES))
    print("-" * 70)

    for pipeline_col in PIPELINE_COLUMNS:
        row = f"{pipeline_col:<22} "
        for label, (_, _, _, _, column_map) in SOH_SOURCES.items():
            src = next((k for k, v in column_map.items() if v == pipeline_col), "-")
            df = read_soh(label)
            status = "OK" if src in df.columns else "MISSING"
            row += f"{src:<12}{status:<6} "
        print(row)


def _normalize_to_pipeline(
    df: pd.DataFrame, column_map: dict[str, str], source_label: str
) -> pd.DataFrame:
    """Map source columns to pipeline schema (case/strip-tolerant header match)."""
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    # Map keys may have trailing spaces / different case from Excel exports
    stripped_map = {str(k).strip(): v for k, v in column_map.items()}
    by_target: dict[str, list[str]] = {}
    for src, tgt in stripped_map.items():
        by_target.setdefault(tgt, []).append(src)

    picks: dict[str, str] = {}
    missing_targets: list[str] = []
    col_lookup = {str(c).strip().lower(): str(c).strip() for c in df.columns}
    for tgt, srcs in by_target.items():
        found = next((col_lookup[s.lower()] for s in srcs if s.lower() in col_lookup), None)
        if not found:
            if tgt == "Site_id":
                found = next((col_lookup[c] for c in col_lookup if any(k in c for k in ("site", "store", "branch", "manual code"))), None)
            elif tgt == "Barcode":
                found = next((col_lookup[c] for c in col_lookup if any(k in c for k in ("barcode", "ean", "upc", "gs1", "article"))), None)
            elif tgt == "Current Stock Qty":
                found = next((col_lookup[c] for c in col_lookup if any(k in c for k in ("closing", "qty", "stock", "quantity", "available"))), None)

        if not found:
            missing_targets.append(tgt)
        else:
            picks[tgt] = found

    if missing_targets:
        # Retry by scanning top 10 rows for actual headers (in case file has banner title rows)
        for row_idx in range(min(10, len(df))):
            row_vals = [str(v).strip() for v in df.iloc[row_idx].values]
            row_val_str = " ".join(v.lower() for v in row_vals)
            if any(k in row_val_str for k in ("site", "store", "manual code", "barcode", "ean", "qty", "closing", "stock")):
                col_lookup_sub = {v.lower(): v for v in row_vals}
                sub_picks = {}
                sub_missing = []
                for tgt, srcs in by_target.items():
                    sub_found = next((col_lookup_sub[s.lower()] for s in srcs if s.lower() in col_lookup_sub), None)
                    if not sub_found:
                        if tgt == "Site_id":
                            sub_found = next((col_lookup_sub[c] for c in col_lookup_sub if any(k in c for k in ("site", "store", "branch", "manual code"))), None)
                        elif tgt == "Barcode":
                            sub_found = next((col_lookup_sub[c] for c in col_lookup_sub if any(k in c for k in ("barcode", "ean", "upc", "gs1", "article"))), None)
                        elif tgt == "Current Stock Qty":
                            sub_found = next((col_lookup_sub[c] for c in col_lookup_sub if any(k in c for k in ("closing", "qty", "stock", "quantity", "available"))), None)
                    if sub_found:
                        sub_picks[tgt] = sub_found
                    else:
                        sub_missing.append(tgt)
                if not sub_missing:
                    # Found valid header at row_idx! Promote it.
                    sub_df = df.iloc[row_idx + 1:].copy()
                    sub_df.columns = row_vals
                    return _normalize_to_pipeline(sub_df, column_map, source_label)

        raise ValueError(
            f"{source_label}: missing pipeline columns {missing_targets}. "
            f"Tried {stripped_map}. Found: {list(df.columns)[:20]}"
        )

    out = df[[picks[t] for t in PIPELINE_COLUMNS]].rename(
        columns={picks[t]: t for t in PIPELINE_COLUMNS}
    ).copy()
    out["Source"] = source_label

    out["Site_id"] = out["Site_id"].astype(str).str.strip()
    out["Barcode"] = out["Barcode"].astype(str).str.strip()
    out["Current Stock Qty"] = pd.to_numeric(out["Current Stock Qty"], errors="coerce")

    return out[PIPELINE_COLUMNS + ["Source"]]


def combine_per_channel_frames() -> pd.DataFrame:
    """Combine ONLY selected store SOH files (no save, no SOH-Master sync)."""
    try:
        from pipeline_inputs import get_selected_store_types, STORE_TYPE_TO_SOH_SOURCE
    except ImportError:
        from pipeline.pipeline_inputs import (  # type: ignore
            get_selected_store_types,
            STORE_TYPE_TO_SOH_SOURCE,
        )

    selected = get_selected_store_types()
    if selected:
        allow = {STORE_TYPE_TO_SOH_SOURCE.get(t, t) for t in selected}
        labels = [lab for lab in SOH_SOURCES if lab in allow]
        print(
            f"[combine_soh] selected={sorted(selected)} -> sources={labels}",
            flush=True,
        )
        if not labels:
            raise ValueError(
                f"No SOH sources match selected store_types={sorted(selected)}"
            )
    else:
        labels = list(SOH_SOURCES.keys())
        print(
            "[combine_soh] no selection -> ALL sources "
            f"{labels}",
            flush=True,
        )

    frames = []
    for label in labels:
        _, _, _, _, column_map = SOH_SOURCES[label]
        df = read_soh(label)
        normalized = _normalize_to_pipeline(df, column_map, label)
        del df
        frames.append(normalized)
        print(f"{label:<8}: {len(normalized):,} rows", flush=True)

    skipped = [lab for lab in SOH_SOURCES if lab not in labels]
    for lab in skipped:
        print(f"{lab:<8}: skipped (not selected)", flush=True)

    if not frames:
        raise ValueError("No SOH frames to combine — check selected stores and input files")

    combined = pd.concat(frames, ignore_index=True)
    combined = combined.sort_values(["Site_id", "Barcode"]).reset_index(drop=True)
    print(f"Combined : {len(combined):,} rows from {labels}", flush=True)
    return combined


def combine_soh(save: bool = True) -> pd.DataFrame:
    """
    Dual-path SOH combine:
      - SOH-Master (blob daily) when latest
      - Per-channel manual uploads → combine + refresh SOH-Master
    """
    try:
        from soh_reconcile import build_combined_soh
    except ImportError:
        from pipeline.soh_reconcile import build_combined_soh  # type: ignore

    return build_combined_soh(save=save)


def main() -> None:
    parser = argparse.ArgumentParser(description="Combine SOH files from all channels")
    parser.add_argument(
        "--show-columns",
        action="store_true",
        help="Har SOH file ke columns dikhao",
    )
    parser.add_argument(
        "--show-mapping",
        action="store_true",
        help="Pipeline column mapping dikhao",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Combine karo lekin file save mat karo",
    )
    args = parser.parse_args()

    if args.show_columns:
        show_columns()
    elif args.show_mapping:
        show_mapping()
    else:
        combine_soh(save=not args.no_save)


if __name__ == "__main__":
    main()

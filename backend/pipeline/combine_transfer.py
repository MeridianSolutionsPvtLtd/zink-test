"""
Combine warehouse transfer/invoice files into one unified file.

Sources:
  - @SALES INVOICE TRACKING WITH DC RATE... till 08-07.xlsx  (PT / SSL / CENTRO / ICONIC)
  - EBO INVOICE FROM WAREHOUSE.xlsx                          (EBO stores)

Usage:
    python combine_transfer.py --show-columns
    python combine_transfer.py --show-mapping
    python combine_transfer.py
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

import pandas as pd

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
INPUT_DIR = BASE_DIR / "input_files"
OUTPUT_DIR = BASE_DIR / "output_files"
OUTPUT_FILE = OUTPUT_DIR / "COMBINED_TRANSFER.xlsx"

WAREHOUSE_FILE_CANDIDATES = [
    INPUT_DIR / "SALES_INVOICE_TRACKING.csv",
    INPUT_DIR / "SALES_INVOICE_TRACKING.xlsx",
    INPUT_DIR / "@SALES INVOICE TRACKING.xlsx",
    INPUT_DIR
    / "@SALES INVOICE TRACKING WITH DC RATE_SP@PACKING - 2026-06-04T182607.685 (1) march 25 to till 08-07.xlsx",
]
WAREHOUSE_SHEET = "SALES INVOICE TRACKING WITH DC "
WAREHOUSE_HEADER_ROW = 3

EBO_FILE_CANDIDATES = [
    INPUT_DIR / "EBO_INVOICE_TRACKING.xlsx",
    INPUT_DIR / "EBO INVOICE FROM WAREHOUSE.xlsx",
]
EBO_SHEET = "HSN WISE TRANSFER OUT REGISTER_"
EBO_HEADER_ROW = 3


def _resolve_input(*candidates: Path) -> Path:
    for p in candidates:
        if p.exists():
            return p
    raise FileNotFoundError(
        "Missing transfer input. Tried:\n" + "\n".join(f"  - {p}" for p in candidates)
    )


def _is_merged_canonical(path: Path) -> bool:
    return path.name.lower() in {
        "sales_invoice_tracking.xlsx",
        "@sales invoice tracking.xlsx",
        "ebo_invoice_tracking.xlsx",
        "ebo invoice from warehouse.xlsx",
    }

# EBO transfer: sirf ye 3 destination stores (U.P. = warehouse, exclude)
EBO_SITE_MAP: dict[str, str] = {
    "Fresco Global Pvt Ltd-Malleshwaram": "528",
    "Fresco Global Pvt Ltd-Inorbit Mall Vadodra": "964",
    "Fresco Global Pvt Ltd-Kurla": "972",
}
EBO_WAREHOUSE_NAME = "Fresco Global Pvt Ltd (U.P.)"

# Warehouse: sirf ye retail channels chahiye (baaki B2B/distributor drop)
RETAIL_CHANNELS = {"PT", "SSL", "CENTRO", "ICONIC"}

# CENTRO = Reliance Retail (RRL) stores — inke naam mein hamesha "CENTRO" nahi hota,
# isliye site code (T####) se pehchaante hain.
CENTRO_CODES = {
    "T8VX", "T8VY", "T8WA", "T8WB", "T8WC", "T8WD", "T8WE", "T8WF", "T8WG",
    "T8WH", "T8WI", "T8WJ", "T8WL", "T8WM", "T8WN", "T8WP", "T8WR", "T8WS",
    "T8WU", "T8WV", "T8WW", "T8WY", "T8XA", "T8XB", "TC3M", "TC4A",
}

PIPELINE_COLUMNS = [
    "site_id",
    "Barcode",
    "Transfer_Date",
    "Quantity",
    "SEASON",
]


def detect_channel(site_name: object, site_short_name: object = None, site_id: str = "") -> str:
    """Classify a warehouse site into PT / SSL / CENTRO / ICONIC / OTHER."""
    name = "" if pd.isna(site_name) else str(site_name).strip()
    short = "" if pd.isna(site_short_name) else str(site_short_name).strip()
    upper = f"{name} {short}".upper()
    code = str(site_id).strip().upper()

    # CENTRO = RRL stores by code list, ya naam mein "CENTRO"
    if code in CENTRO_CODES or "CENTRO" in upper:
        return "CENTRO"
    if "ICONIC" in upper:
        return "ICONIC"
    if "SHOPPER" in upper:
        return "SSL"
    # PT stores: SITE NAME starts with PT / PW, ya PANTALOON
    if re.match(r"^\s*(PT|PW)\b", name, re.IGNORECASE) or "PANTALOON" in upper:
        return "PT"
    return "OTHER"


def parse_site_id_from_warehouse(
    site_name: object, site_short_name: object = None
) -> str:
    """
    Extract site_id from warehouse SITE NAME / SITE SHORT NAME.

    Priority patterns (SSL/PT/CENTRO/ICONIC + others):
    1. Bracket at end:  "... (P125)" / "( 359 )"
    2. Last bracket:     "... (SoS05)", "(DL 01)"
    3. Leading numeric:  "8563 - SSL-Rajahmundry" -> 8563
    4. Leading PT code:  "P746-SEAPORT..." -> P746
    5. Comma suffix:     "PT Vasco, Goa,P426" -> P426
    6. Trailing hyphen:  "SS-VARANASI-225" -> 225
    """
    def _from_text(text: object) -> str:
        if pd.isna(text):
            return ""
        s = str(text).strip()
        if not s:
            return ""

        match = re.search(r"\(\s*([^)]+?)\s*\)\s*$", s)
        if match:
            return match.group(1).strip().upper()

        brackets = re.findall(r"\(\s*([^)]+?)\s*\)", s)
        if brackets:
            return brackets[-1].strip().upper()

        match = re.match(r"^(\d{3,5})\s*[-\s]", s)
        if match:
            return match.group(1).upper()

        match = re.match(r"^(P\d{2,4})[-\s,]", s, re.IGNORECASE)
        if match:
            return match.group(1).upper()

        match = re.search(r",\s*([A-Za-z0-9]+)\s*$", s)
        if match:
            return match.group(1).upper()

        match = re.search(r"-\s*([A-Za-z0-9]+)\s*$", s)
        if match:
            return match.group(1).upper()

        return ""

    def _looks_like_code(code: str) -> bool:
        if not code:
            return False
        if re.fullmatch(r"\d{2,5}", code):
            return True
        if re.fullmatch(r"P\d{2,4}", code, re.IGNORECASE):
            return True
        if re.fullmatch(r"T[A-Z0-9]{2,5}", code, re.IGNORECASE):
            return True
        return len(code) <= 10

    from_name = _from_text(site_name)
    from_short = _from_text(site_short_name)

    if _looks_like_code(from_short) and not _looks_like_code(from_name):
        return from_short
    if from_name:
        return from_name
    if from_short:
        return from_short
    if pd.notna(site_name):
        return str(site_name).strip()
    return ""


_WAREHOUSE_COLS = [
    "SITE NAME",
    "SITE SHORT NAME",
    "BARCODE",
    "DC_DATE",
    "INVOICE_QUANTITY",
    "CATEGORY6",
]


def read_warehouse_transfers() -> pd.DataFrame:
    from pipeline_inputs import read_excel_fast

    path = _resolve_input(*WAREHOUSE_FILE_CANDIDATES)
    print(f"[combine_transfer] reading {path.name} ...", flush=True)
    df = read_excel_fast(
        path,
        sheet_name=0,
        usecols=_WAREHOUSE_COLS,
        column_hints=_WAREHOUSE_COLS,
        preferred_header=WAREHOUSE_HEADER_ROW,
        preferred_sheet=WAREHOUSE_SHEET.strip(),
    )
    missing = [c for c in _WAREHOUSE_COLS if c not in df.columns]
    if missing:
        raise ValueError(
            f"WAREHOUSE transfer file missing columns {missing}. "
            f"Found: {list(df.columns)[:25]}"
        )
    return df


def read_ebo_transfers() -> pd.DataFrame:
    from pipeline_inputs import find_column, read_excel_fast

    path = _resolve_input(*EBO_FILE_CANDIDATES)
    print(f"[combine_transfer] reading {path.name} ...", flush=True)
    hints = ["To Site", "BARCODE", "Transfer Date", "TRANSFER_IN_DATE", "Quantity", "CAT 6"]
    df = read_excel_fast(
        path,
        sheet_name=0,
        column_hints=hints,
        preferred_header=EBO_HEADER_ROW,
        preferred_sheet=EBO_SHEET if isinstance(EBO_SHEET, str) else None,
    )
    find_column(
        df,
        "Transfer Date",
        "TRANSFER_IN_DATE",
        "Transfer_Date",
        "DC_DATE",
    )
    find_column(df, "To Site", "TO SITE")
    find_column(df, "BARCODE", "Barcode")

    to_site_col = find_column(df, "To Site", "TO SITE")
    if to_site_col in df.columns:
        return df[df[to_site_col] != EBO_WAREHOUSE_NAME].copy()
    return df


def show_columns() -> None:
    wh_path = _resolve_input(*WAREHOUSE_FILE_CANDIDATES)
    ebo_path = _resolve_input(*EBO_FILE_CANDIDATES)
    wh = read_warehouse_transfers()
    ebo = read_ebo_transfers()

    for label, df, path, sheet in [
        ("WAREHOUSE", wh, wh_path, WAREHOUSE_SHEET),
        ("EBO", ebo, ebo_path, EBO_SHEET),
    ]:
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
    wh = read_warehouse_transfers()
    ebo = read_ebo_transfers()

    warehouse_map = {
        "SITE NAME + SITE SHORT NAME": "site_id",
        "BARCODE": "Barcode",
        "DC_DATE": "Transfer_Date",
        "INVOICE_QUANTITY": "Quantity",
        "CATEGORY6": "SEASON",
    }
    ebo_map = {
        "To Site -> manual site_id": "site_id",
        "BARCODE": "Barcode",
        "Transfer Date": "Transfer_Date",
        "Quantity": "Quantity",
        "CAT 6": "SEASON",
    }

    print("Pipeline columns:")
    for i, col in enumerate(PIPELINE_COLUMNS, 1):
        print(f"  {i}. {col}")
    print()
    print("-" * 70)
    print(f"{'Pipeline':<18} {'WAREHOUSE Source':<30} {'EBO Source':<30}")
    print("-" * 70)

    for pipeline_col in PIPELINE_COLUMNS:
        wh_src = next((k for k, v in warehouse_map.items() if v == pipeline_col), "-")
        ebo_src = next((k for k, v in ebo_map.items() if v == pipeline_col), "-")
        print(f"{pipeline_col:<18} {wh_src:<30} {ebo_src:<30}")

    print()
    print(f"Excluded EBO warehouse: {EBO_WAREHOUSE_NAME}")
    print()
    print("EBO To Site -> site_id (manual):")
    for to_site, site_id in EBO_SITE_MAP.items():
        print(f"  {to_site:<45} -> {site_id}")

    print()
    print("WAREHOUSE site_id parse sample:")
    sample = wh[["SITE NAME", "SITE SHORT NAME"]].drop_duplicates().head(10).copy()
    sample["site_id"] = sample.apply(
        lambda r: parse_site_id_from_warehouse(r["SITE NAME"], r["SITE SHORT NAME"]),
        axis=1,
    )
    print(sample.to_string(index=False))


def _normalize_warehouse(df: pd.DataFrame) -> pd.DataFrame:
    required = [
        "SITE NAME",
        "SITE SHORT NAME",
        "BARCODE",
        "DC_DATE",
        "INVOICE_QUANTITY",
        "CATEGORY6",
    ]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"WAREHOUSE: missing columns {missing}")

    # pehle site_id parse karo (channel detection code par depend karta hai)
    site_id = df.apply(
        lambda r: parse_site_id_from_warehouse(r["SITE NAME"], r["SITE SHORT NAME"]),
        axis=1,
    )
    channel = df.apply(
        lambda r: detect_channel(
            r["SITE NAME"], r["SITE SHORT NAME"], site_id.loc[r.name]
        ),
        axis=1,
    )

    # sirf retail channels rakho (PT/SSL/CENTRO/ICONIC), baaki B2B drop
    keep = channel.isin(RETAIL_CHANNELS)
    dropped = (~keep).sum()
    print(f"WAREHOUSE channel filter : kept {keep.sum():,}, dropped {dropped:,} (B2B/other)")

    out = pd.DataFrame()
    out["site_id"] = site_id[keep]
    df = df[keep]
    channel = channel[keep]

    out["Barcode"] = df["BARCODE"].astype(str).str.strip()
    out["Transfer_Date"] = pd.to_datetime(df["DC_DATE"], errors="coerce")
    out["Quantity"] = pd.to_numeric(df["INVOICE_QUANTITY"], errors="coerce")
    out["SEASON"] = df["CATEGORY6"].astype(str).str.strip()
    out["Channel"] = channel.values
    out["Source"] = "WAREHOUSE"
    return out[PIPELINE_COLUMNS + ["Channel", "Source"]]


def _normalize_ebo(df: pd.DataFrame) -> pd.DataFrame:
    try:
        from pipeline_inputs import find_column
    except ImportError:
        from pipeline.pipeline_inputs import find_column  # type: ignore

    to_site_col = find_column(df, "To Site", "TO SITE")
    barcode_col = find_column(df, "BARCODE", "Barcode")
    date_col = find_column(
        df,
        "Transfer Date",
        "Transfer_Date",
        "TRANSFER_IN_DATE",
        "DC_DATE",
        "Date",
    )
    qty_col = find_column(df, "Quantity", "QUANTITY", "TRANSFER_IN_QUANTITY")
    season_col = find_column(df, "CAT 6", "CAT6", "CATEGORY6")

    out = pd.DataFrame()
    out["site_id"] = df[to_site_col].astype(str).str.strip().map(EBO_SITE_MAP)
    unknown = df.loc[out["site_id"].isna(), to_site_col].dropna().unique()
    if len(unknown):
        raise ValueError(f"EBO: unmapped To Site values: {list(unknown)}")
    out["Barcode"] = df[barcode_col].astype(str).str.strip()
    out["Transfer_Date"] = pd.to_datetime(df[date_col], errors="coerce")
    out["Quantity"] = pd.to_numeric(df[qty_col], errors="coerce")
    out["SEASON"] = df[season_col].astype(str).str.strip()
    out["Channel"] = "EBO"
    out["Source"] = "EBO"
    return out[PIPELINE_COLUMNS + ["Channel", "Source"]]


def combine_transfer(save: bool = True) -> pd.DataFrame:
    """
    Build COMBINED_TRANSFER.

    - EBO not selected → byte-copy warehouse invoice file (instant).
    - Only EBO → byte-copy EBO invoice file.
    - Both → parse + merge.
    """
    try:
        from pipeline_inputs import get_selected_store_types, write_combined_tabular
    except ImportError:
        from pipeline.pipeline_inputs import (  # type: ignore
            get_selected_store_types,
            write_combined_tabular,
        )
    import shutil

    selected = get_selected_store_types()
    need_ebo = (not selected) or ("EBO" in selected)
    shared = {"PT", "SSL", "CENTRO", "ICONIC"}
    need_wh = (not selected) or bool(selected & shared)

    print(f"[combine_transfer] selected={sorted(selected) or ['ALL']}", flush=True)

    def _copy(src: Path) -> None:
        OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        if src.suffix.lower() == ".csv":
            csv_dst = OUTPUT_FILE.with_suffix(".csv")
            shutil.copy2(src, csv_dst)
            print(f"Copied (no parse) : {src.name} -> {csv_dst.name}", flush=True)
            print(f"Size              : {csv_dst.stat().st_size / 1e6:.1f} MB", flush=True)
            xlsx_dst = OUTPUT_FILE.with_suffix(".xlsx")
            if xlsx_dst.exists():
                try:
                    xlsx_dst.unlink()
                except OSError:
                    pass
        else:
            shutil.copy2(src, OUTPUT_FILE)
            print(f"Copied (no parse) : {src.name} -> {OUTPUT_FILE.name}", flush=True)
            print(f"Size              : {OUTPUT_FILE.stat().st_size / 1e6:.1f} MB", flush=True)
            csv_path = OUTPUT_FILE.with_suffix(".csv")
            if csv_path.exists():
                try:
                    csv_path.unlink()
                    print(f"Removed stale CSV : {csv_path.name} (xlsx is source of truth)", flush=True)
                except OSError as e:
                    print(f"[warn] could not remove stale CSV {csv_path.name}: {e}", flush=True)

    if save and need_wh and not need_ebo:
        _copy(_resolve_input(*WAREHOUSE_FILE_CANDIDATES))
        print("Mode              : copy warehouse transfer only (EBO skipped)", flush=True)
        return pd.DataFrame()

    if save and need_ebo and not need_wh:
        ebo_norm = _normalize_ebo(read_ebo_transfers())
        print(f"EBO      : {len(ebo_norm):,} rows", flush=True)
        write_combined_tabular(ebo_norm, OUTPUT_FILE)
        print(f"Saved    : {OUTPUT_FILE.name} (normalized EBO transfer)", flush=True)
        print("Mode              : normalize EBO transfer only", flush=True)
        return ebo_norm

    frames = []
    if need_wh:
        wh_norm = _normalize_warehouse(read_warehouse_transfers())
        if selected:
            keep_ch = selected & shared
            if keep_ch:
                before = len(wh_norm)
                wh_norm = wh_norm[wh_norm["Channel"].astype(str).str.upper().isin(keep_ch)].copy()
                print(
                    f"WAREHOUSE store filter: {sorted(keep_ch)} "
                    f"{before:,} -> {len(wh_norm):,} rows"
                )
        frames.append(wh_norm)
        print(f"WAREHOUSE: {len(wh_norm):,} rows")
    if need_ebo:
        ebo_norm = _normalize_ebo(read_ebo_transfers())
        frames.append(ebo_norm)
        print(f"EBO      : {len(ebo_norm):,} rows")

    if not frames:
        raise ValueError(f"No transfer sources for selected store_types={sorted(selected)}")

    print("Mode     : merge warehouse + EBO")
    combined = pd.concat(frames, ignore_index=True)
    combined = combined.sort_values(
        ["Transfer_Date", "site_id", "Barcode"]
    ).reset_index(drop=True)
    print(f"Combined : {len(combined):,} rows")

    if save:
        write_combined_tabular(combined, OUTPUT_FILE)

    return combined


def main() -> None:
    parser = argparse.ArgumentParser(description="Combine warehouse transfer files")
    parser.add_argument("--show-columns", action="store_true")
    parser.add_argument("--show-mapping", action="store_true")
    parser.add_argument("--no-save", action="store_true")
    args = parser.parse_args()

    if args.show_columns:
        show_columns()
    elif args.show_mapping:
        show_mapping()
    else:
        combine_transfer(save=not args.no_save)


if __name__ == "__main__":
    main()

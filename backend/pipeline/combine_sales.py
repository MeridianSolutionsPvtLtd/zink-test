"""
Normalize the shared PT/SSL/CENTRO/ICONIC/EBO sales report into one unified file.

Usage:
    python combine_sales.py --show-columns    # sirf columns dikhao
    python combine_sales.py --show-mapping    # pipeline mapping dikhao
    python combine_sales.py                   # combine karke output_files/ mein save karo
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pandas as pd

# ── File paths ────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
INPUT_DIR = BASE_DIR / "input_files"
OUTPUT_DIR = BASE_DIR / "output_files"

# Prefer date-merged canonical names from Blob current/; fall back to legacy names
PT_FILE_CANDIDATES = [
    INPUT_DIR / "SSI_PT_CENTRO_ICONIC_EBO_SALES_MASTER.csv",
    INPUT_DIR / "SSI_PT_CENTRO_ICONIC_SALES_MASTER.csv",
    INPUT_DIR / "SSI_PT_CENTRO_ICONIC_SALES.csv",
    INPUT_DIR / "SSI_PT_CENTRO_ICONIC_SALES.xlsx",
    INPUT_DIR / "SSL_PT_CENTRO_ICONIC_SALE.xlsx",
    INPUT_DIR / "SSL ,PT ,CENTRO, ICONIC SALE till 8th.xlsx",
]
EBO_FILE_CANDIDATES = [
    INPUT_DIR / "EBO_SALES.csv",
    INPUT_DIR / "EBO_SALES.xlsx",
    INPUT_DIR / "@EBO SALE REGISTER.xlsx",
    INPUT_DIR / "@EBO SALE REGISTER till 8th july.xlsx",
]
OUTPUT_FILE = OUTPUT_DIR / "COMBINED_SALES.xlsx"

PT_SHEET = "UNMANAGED SITE SALE REGISTER_SP"
PT_HEADER_ROW = 3  # row 4 in Excel (title rows above) — legacy only

EBO_SHEET = "EBO SALE REGISTER WITH MOP"
EBO_HEADER_ROW = 3


def _resolve_input(*candidates: Path) -> Path:
    for p in candidates:
        if p.exists():
            return p
    raise FileNotFoundError(
        "Missing sales input. Tried:\n" + "\n".join(f"  - {p}" for p in candidates)
    )


def _is_merged_canonical(path: Path) -> bool:
    name = path.name.lower()
    return name in {
        "ssi_pt_centro_iconic_ebo_sales_master.csv",
        "ssi_pt_centro_iconic_sales_master.csv",
        "ssi_pt_centro_iconic_sales.csv",
        "ssi_pt_centro_iconic_sales.xlsx",
        "ssl_pt_centro_iconic_sale.xlsx",
        "ebo_sales.csv",
        "ebo_sales.xlsx",
        "@ebo sale register.xlsx",
    }

# ── Pipeline required columns ─────────────────────────────────────────────────
PIPELINE_COLUMNS = [
    "Date",
    "Site_ID",
    "Site_Name",
    "Barcode",
    "SKU",
    "Size",
    "Qty_Sold",
    "MRP",
    "Net_sales_amount",
    "Discount_amount",
    "TAX_AMOUNT",
    "SEASON",
    "Department",
    "Section",
]

# Optional source columns — present in sales files under varying names.
# Mapped when found; blank if missing (no Item Master lookup).
_SKU_ALIASES = ("SKU", "CATEGORY3", "CATEGORY 3", "ITEM CODE", "ITEM_CODE", "ARTICLE", "STYLE", "ICODE")
_SIZE_ALIASES = ("SIZE", "Size", "CATEGORY5", "CATEGORY 5", "ITEM SIZE", "SIZE CODE", "SIZE_CODE")
_TAX_ALIASES = ("TAX_AMOUNT", "TAX AMOUNT", "Tax_Amount", "Tax Amount", "GST_AMOUNT", "GST AMOUNT")
# PT: Department / Section; EBO: DIVISION ≈ department, SECTION
_DEPT_ALIASES = ("Department", "DEPARTMENT", "DIVISION")
_SECTION_ALIASES = ("Section", "SECTION")

# ── Column mapping: source column → pipeline column ───────────────────────────
# Update these after reviewing --show-columns output
PT_COLUMN_MAP = {
    "ENTRY_DATE": "Date",
    "STORE CODE": "Site_ID",
    "TRANSACTION_SITE": "Site_Name",
    "BARCODE": "Barcode",
    "CATEGORY3": "SKU",
    "CATEGORY5": "Size",
    "QUANTITY": "Qty_Sold",
    "RSP": "MRP",
    "NET_AMOUNT": "Net_sales_amount",
    "ITEM DISCOUNT_AMOUNT": "Discount_amount",
    "CATEGORY6": "SEASON",
}

EBO_COLUMN_MAP = {
    "BILL DATE": "Date",
    "SITE CODE": "Site_ID",
    "SITE NAME": "Site_Name",
    "BARCODE": "Barcode",
    "CATEGORY 3": "SKU",
    "CATEGORY 5": "Size",
    "BILL_QUANTITY": "Qty_Sold",
    "MRP": "MRP",
    "NET AMOUNT": "Net_sales_amount",
    "DISCOUNT AMOUNT": "Discount_amount",
    "CATEGORY 6": "SEASON",
}


def _pick_alias_column(df: pd.DataFrame, aliases: tuple[str, ...]) -> str | None:
    """Return first matching column name (case-insensitive strip)."""
    by_norm = {str(c).strip().upper(): c for c in df.columns}
    for alias in aliases:
        hit = by_norm.get(str(alias).strip().upper())
        if hit is not None:
            return str(hit)
    return None


def read_pt_sales() -> pd.DataFrame:
    """Read PT/SSL/CENTRO/ICONIC sales file (merged canonical or legacy)."""
    from pipeline_inputs import read_excel_fast

    path = _resolve_input(*PT_FILE_CANDIDATES)
    print(f"[combine_sales] reading {path.name} ...", flush=True)
    hints = list(PT_COLUMN_MAP.keys()) + list(_SKU_ALIASES) + list(_SIZE_ALIASES) + list(_TAX_ALIASES) + list(_DEPT_ALIASES) + list(_SECTION_ALIASES)
    # Prefer reading all hinted cols when present (SKU/Size optional).
    if _is_merged_canonical(path):
        return read_excel_fast(
            path,
            sheet_name=0,
            column_hints=hints,
            preferred_header=0,
        )
    return read_excel_fast(
        path,
        sheet_name=PT_SHEET,
        column_hints=hints,
        preferred_header=PT_HEADER_ROW,
        preferred_sheet=PT_SHEET,
    )


def read_ebo_sales() -> pd.DataFrame:
    """Read EBO sales file (merged canonical or legacy)."""
    from pipeline_inputs import read_excel_fast

    path = _resolve_input(*EBO_FILE_CANDIDATES)
    print(f"[combine_sales] reading {path.name} ...", flush=True)
    hints = list(EBO_COLUMN_MAP.keys()) + list(_SKU_ALIASES) + list(_SIZE_ALIASES) + list(_TAX_ALIASES) + list(_DEPT_ALIASES) + list(_SECTION_ALIASES)
    if _is_merged_canonical(path):
        return read_excel_fast(
            path,
            sheet_name=0,
            column_hints=hints,
            preferred_header=0,
        )
    return read_excel_fast(
        path,
        sheet_name=EBO_SHEET,
        column_hints=hints,
        preferred_header=EBO_HEADER_ROW,
        preferred_sheet=EBO_SHEET,
    )


def show_columns() -> None:
    """Print all columns from both source files."""
    pt_path = _resolve_input(*PT_FILE_CANDIDATES)
    ebo_path = _resolve_input(*EBO_FILE_CANDIDATES)
    pt_df = read_pt_sales()
    ebo_df = read_ebo_sales()

    print("=" * 70)
    print(f"FILE 1: {pt_path.name}")
    print(f"Rows  : {len(pt_df):,}")
    print("=" * 70)
    for i, col in enumerate(pt_df.columns, 1):
        print(f"  {i:2}. {col}")

    print()
    print("=" * 70)
    print(f"FILE 2: {ebo_path.name}")
    print(f"Rows  : {len(ebo_df):,}")
    print("=" * 70)
    for i, col in enumerate(ebo_df.columns, 1):
        print(f"  {i:2}. {col}")


def show_mapping() -> None:
    """Print suggested mapping from source columns to pipeline columns."""
    pt_df = read_pt_sales()
    ebo_df = read_ebo_sales()

    print("Pipeline MUST columns:")
    print("-" * 70)
    print(f"{'Pipeline Column':<20} {'PT Source':<25} {'EBO Source':<25}")
    print("-" * 70)

    for pipeline_col in PIPELINE_COLUMNS:
        pt_src = next((k for k, v in PT_COLUMN_MAP.items() if v == pipeline_col), "—")
        ebo_src = next((k for k, v in EBO_COLUMN_MAP.items() if v == pipeline_col), "—")

        pt_ok = "OK" if pt_src in pt_df.columns else "MISSING"
        ebo_ok = "OK" if ebo_src in ebo_df.columns else "MISSING"

        print(
            f"{pipeline_col:<20} {pt_src:<25} {pt_ok}  "
            f"{ebo_src:<25} {ebo_ok}"
        )

    print()
    print("Extra PT columns (not mapped):")
    mapped_pt = set(PT_COLUMN_MAP.keys())
    for col in pt_df.columns:
        if col not in mapped_pt:
            print(f"  - {col}")

    print()
    print("Extra EBO columns (not mapped):")
    mapped_ebo = set(EBO_COLUMN_MAP.keys())
    for col in ebo_df.columns:
        if col not in mapped_ebo:
            print(f"  - {col}")


def _normalize_to_pipeline(
    df: pd.DataFrame, column_map: dict[str, str], source_label: str
) -> pd.DataFrame:
    """Rename source columns to pipeline schema and add source tag."""
    # Attempt new 33 sales columns schema normalization first
    try:
        try:
            from sales_columns import map_sales_to_pipeline_schema
        except ImportError:
            from pipeline.sales_columns import map_sales_to_pipeline_schema  # type: ignore
        mapped = map_sales_to_pipeline_schema(df)
        if not mapped.empty and "Date" in mapped.columns and mapped["Date"].dropna().shape[0] > 0:
            mapped["Source"] = source_label
            # Clean up types and strings
            try:
                from pipeline_inputs import parse_sales_dates
            except ImportError:
                from pipeline.pipeline_inputs import parse_sales_dates  # type: ignore
            mapped["Date"] = parse_sales_dates(mapped["Date"])
            mapped["Site_ID"] = mapped["Site_ID"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
            mapped["Site_Name"] = mapped["Site_Name"].astype(str).str.strip()
            mapped["Barcode"] = mapped["Barcode"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
            mapped["SEASON"] = mapped["SEASON"].astype(str).str.strip()
            mapped["SKU"] = mapped["SKU"].astype(str).str.strip().replace({"nan": "", "None": "", "<NA>": ""})
            mapped["Size"] = mapped["Size"].astype(str).str.strip().replace({"nan": "", "None": "", "<NA>": ""})
            mapped["Department"] = mapped["Department"].astype(str).str.strip().replace({"nan": "", "None": "", "<NA>": ""})
            mapped["Section"] = mapped["Section"].astype(str).str.strip().replace({"nan": "", "None": "", "<NA>": ""})
            for col in ["Qty_Sold", "MRP", "Net_sales_amount", "Discount_amount", "TAX_AMOUNT"]:
                mapped[col] = pd.to_numeric(mapped[col], errors="coerce").fillna(0)
            return mapped[PIPELINE_COLUMNS + ["Source"]]
    except Exception as e:
        print(f"[{source_label}] sales_columns schema map fallback: {e}", flush=True)

    missing = [src for src in column_map if src not in df.columns]
    if missing:
        raise ValueError(
            f"{source_label}: missing columns {missing}. "
            f"Update column_map in combine_sales.py"
        )

    out = df[list(column_map.keys())].rename(columns=column_map).copy()
    out["Source"] = source_label

    if "SKU" not in out.columns:
        sku_src = _pick_alias_column(df, _SKU_ALIASES)
        if sku_src:
            out["SKU"] = df[sku_src].astype(str).str.strip()
            print(f"[{source_label}] SKU from column: {sku_src}", flush=True)
        else:
            out["SKU"] = ""
            print(f"[{source_label}] SKU column not found — left blank", flush=True)
    else:
        out["SKU"] = out["SKU"].astype(str).str.strip()
        print(f"[{source_label}] SKU from CATEGORY3 / mapped column", flush=True)

    if "Size" not in out.columns:
        size_src = _pick_alias_column(df, _SIZE_ALIASES)
        if size_src:
            out["Size"] = df[size_src].astype(str).str.strip()
            print(f"[{source_label}] Size from column: {size_src}", flush=True)
        else:
            out["Size"] = ""
            print(f"[{source_label}] Size column not found — left blank", flush=True)
    else:
        out["Size"] = out["Size"].astype(str).str.strip()
        print(f"[{source_label}] Size from CATEGORY5 / mapped column", flush=True)

    tax_src = _pick_alias_column(df, _TAX_ALIASES)
    if tax_src:
        out["TAX_AMOUNT"] = pd.to_numeric(df[tax_src], errors="coerce")
        print(f"[{source_label}] TAX_AMOUNT from column: {tax_src}", flush=True)
    else:
        out["TAX_AMOUNT"] = pd.NA
        print(f"[{source_label}] TAX_AMOUNT column not found — left blank", flush=True)

    dept_src = _pick_alias_column(df, _DEPT_ALIASES)
    if dept_src:
        out["Department"] = df[dept_src].astype(str).str.strip().replace({"nan": "", "None": "", "<NA>": ""})
        print(f"[{source_label}] Department from column: {dept_src}", flush=True)
    else:
        out["Department"] = ""
        print(f"[{source_label}] Department column not found — left blank", flush=True)

    section_src = _pick_alias_column(df, _SECTION_ALIASES)
    if section_src:
        out["Section"] = df[section_src].astype(str).str.strip().replace({"nan": "", "None": "", "<NA>": ""})
        print(f"[{source_label}] Section from column: {section_src}", flush=True)
    else:
        out["Section"] = ""
        print(f"[{source_label}] Section column not found — left blank", flush=True)

    try:
        from pipeline_inputs import parse_sales_dates
    except ImportError:
        from pipeline.pipeline_inputs import parse_sales_dates  # type: ignore

    out["Date"] = parse_sales_dates(out["Date"])
    out["Site_ID"] = out["Site_ID"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    out["Site_Name"] = out["Site_Name"].astype(str).str.strip()
    out["Barcode"] = out["Barcode"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    out["SEASON"] = out["SEASON"].astype(str).str.strip()
    out["SKU"] = out["SKU"].replace({"nan": "", "None": "", "<NA>": ""})
    out["Size"] = out["Size"].replace({"nan": "", "None": "", "<NA>": ""})
    out["Department"] = out["Department"].replace({"nan": "", "None": "", "<NA>": ""})
    out["Section"] = out["Section"].replace({"nan": "", "None": "", "<NA>": ""})

    for col in ["Qty_Sold", "MRP", "Net_sales_amount", "Discount_amount", "TAX_AMOUNT"]:
        out[col] = pd.to_numeric(out[col], errors="coerce")

    return out[PIPELINE_COLUMNS + ["Source"]]


def combine_sales(save: bool = True) -> pd.DataFrame:
    """
    Build COMBINED_SALES.

    Always read/normalize selected sources so SKU/Size (CATEGORY3/CATEGORY5) are saved
    into COMBINED_SALES.csv. Combines PT, SSL, CENTRO, ICONIC and EBO.
    """
    try:
        from pipeline_inputs import get_selected_store_types, write_combined_tabular
    except ImportError:
        from pipeline.pipeline_inputs import (  # type: ignore
            get_selected_store_types,
            write_combined_tabular,
        )

    env_types = (os.environ.get("PIPELINE_STORE_TYPES") or "").strip()
    if env_types:
        selected = {t.strip().upper() for t in env_types.split(",") if t.strip()}
    else:
        selected = {"PT", "SSL", "CENTRO", "ICONIC", "EBO"}

    print(f"[combine_sales] selected={sorted(selected) or ['ALL']}", flush=True)
    if not selected.intersection({"PT", "SSL", "CENTRO", "ICONIC", "EBO"}):
        raise ValueError(f"No sales sources for selected store_types={sorted(selected)}")

    # The shared report includes EBO, including when only EBO is selected.
    # Never append the retired separate EBO source (it can duplicate sales).
    pt_df = read_pt_sales()
    print(f"[combine_sales] shared raw rows: {len(pt_df):,}", flush=True)
    combined = _normalize_to_pipeline(pt_df, PT_COLUMN_MAP, "PT_SSL_CENTRO_ICONIC")
    del pt_df

    # Filter sales from min date (default: 2024-04-01 to keep full 2-year history from max date)
    min_date_filter = os.environ.get("COMBINED_SALES_MIN_DATE", "2024-04-01").strip()
    if min_date_filter:
        before_count = len(combined)
        combined = combined[combined["Date"] >= pd.Timestamp(min_date_filter)].reset_index(drop=True)
        print(
            f"[combine_sales] Filtered Date >= {min_date_filter}: {before_count:,} -> {len(combined):,} rows",
            flush=True,
        )

    # Format Date column as YYYY-MM-DD
    if pd.api.types.is_datetime64_any_dtype(combined["Date"]):
        combined["Date"] = combined["Date"].dt.strftime("%Y-%m-%d")

    combined = combined.sort_values(["Date", "Site_ID", "Barcode"]).reset_index(drop=True)
    print(f"COMBINED_SALES rows        : {len(combined):,}", flush=True)

    if save:
        write_combined_tabular(combined, OUTPUT_FILE)
        # Also copy/save to root output_files
        root_out = BASE_DIR.parent / "output_files" / "COMBINED_SALES.csv"
        try:
            root_out.parent.mkdir(parents=True, exist_ok=True)
            combined.to_csv(root_out, index=False)
            print(f"Synced to root output_files: {root_out} ({len(combined):,} rows)", flush=True)
        except Exception as e:
            print(f"[warn] root output_files copy failed: {e}", flush=True)

    return combined


def main() -> None:
    parser = argparse.ArgumentParser(description="Combine PT and EBO sales files")
    parser.add_argument(
        "--show-columns",
        action="store_true",
        help="Sirf dono files ke columns dikhao",
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
        combine_sales(save=not args.no_save)


if __name__ == "__main__":
    main()

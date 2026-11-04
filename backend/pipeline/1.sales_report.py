"""
Build final sales report from Sales (PT, SSL, Centro, Iconic, EBO) + COMBINED_TRANSFER.

Sources:
  - Sales (PT, SSL, Centro, Iconic, EBO)/current/SSI_PT_CENTRO_ICONIC_SALES.*
  - COMBINED_TRANSFER.xlsx  -> transfer   (site_id + Transfer_Date + Quantity)

Logic:
  transfer se per (site_id + barcode): max(Transfer_Date) = last_purchase_date,
  sum(Quantity) = total_purchase_qty. Ye sales par join hote hain.
  Discount sirf sales se: discount_amt = MRP x Sold_QTY - net sales.
  Default: ek row per (calendar date + site_id + barcode).
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import numpy as np
import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = SCRIPT_DIR / "output_files"

DEFAULT_SALES_FILE = ""  # resolved via load_combined_sales → Sales (PT, SSL, Centro, Iconic, EBO)
DEFAULT_TRANSFER_FILE = OUTPUT_DIR / "COMBINED_TRANSFER.xlsx"
DEFAULT_OUTPUT_NAME = "SALES_REPORT.xlsx"

# EBO site ids/names (store_type ke liye)
EBO_SITE_IDS = {"528", "964", "972", "FGPL-MALLESHWARAM", "FGPL-VADODRA", "FGPL-KURLA"}

# Report mein ye channels chahiye — Fashion Factory / B2B / online / blank drop
RETAIL_CHANNELS = {"PT", "SSL", "CENTRO", "ICONIC", "EBO"}

OUTPUT_COLUMNS = [
    "date",
    "site_id",
    "site_name",
    "barcode",
    "MRP",
    "net_sales_value",
    "Sold_QTY",
    "year",
    "month",
    "iso_week",
    "discount_amt",
    "discount_percentage",
    "season",
    "last_purchase_date",
    "total_purchase_qty",
    "store_type",
]


def _as_series(col: pd.Series | pd.DataFrame) -> pd.Series:
    """Duplicate column names make df[col] a DataFrame — keep densest (most non-null)."""
    if isinstance(col, pd.DataFrame):
        counts = [int(col.iloc[:, i].notna().sum()) for i in range(col.shape[1])]
        best = int(max(range(len(counts)), key=lambda i: counts[i])) if counts else 0
        return col.iloc[:, best]
    return col


def _dedupe_columns(df: pd.DataFrame) -> pd.DataFrame:
    try:
        from pipeline_inputs import _dedupe_frame_columns
    except ImportError:
        from pipeline.pipeline_inputs import _dedupe_frame_columns  # type: ignore
    return _dedupe_frame_columns(df)


def _norm_key(s: pd.Series | pd.DataFrame) -> pd.Series:
    """Join key normalize: str, strip, trailing '.0' hatao, nan/None -> ''."""
    out = _as_series(s).astype(str).str.strip()
    out = out.str.replace(r"\.0$", "", regex=True)
    return out.replace({"nan": "", "None": "", "NaT": ""})


def derive_store_type(site_name: object, site_id: object | None = None) -> str:
    """Classify: EBO / PT / SSL / CENTRO / ICONIC / FASHION_FACTORY / PW."""
    code = "" if site_id is None else str(site_id).strip().upper().replace(".0", "")
    name = str(site_name).strip()
    upper = name.upper()
    # Leading store-code/digits/dashes hatao: "8563 - SSL-..." -> "SSL-..."
    stripped = re.sub(r"^[\d\.\-\s]+", "", upper)

    # EBO
    if code in EBO_SITE_IDS or code.startswith("FGPL") or "FRESCO GLOBAL PVT LTD" in upper or upper.startswith("FRESCO"):
        return "EBO"
    # Fashion Factory (Reliance ka alag off-price banner) — CENTRO se PEHLE check
    # (kuch FF stores ke T-code hote hain jaise T3IY/TAU9, isliye order important)
    if "FASHION FACTORY" in upper:
        return "FASHION_FACTORY"
    # CENTRO (Reliance Retail / Centro) — T-code ya naam
    if re.fullmatch(r"T[A-Z0-9]{2,5}", code) or "CENTRO" in upper or upper.startswith("RRL") or "RELIANCE RETAIL" in upper:
        return "CENTRO"
    # ICONIC
    if "ICONIC" in upper:
        return "ICONIC"
    # PT (Pantaloons) — P-code/W-code (P412/P738/W020), ya naam (PT-/PW-/PANTALOONS)
    # PW-... aur W-code stores bhi Pantaloons hi hain, isliye PT mein aate hain
    if (
        re.match(r"^[PW]\d", code)
        or "PANTALOONS" in upper
        or stripped.startswith(("PT-", "PT ", "PW-", "PW "))
        or re.match(r"^P\d", stripped)
    ):
        return "PT"
    # SSL (Shoppers Stop) — SHOPPER/SHOPER spellings, ya SS-/SSL prefix (leading code hatane ke baad)
    if (
        "SHOPPER" in upper
        or "SHOPER" in upper
        or stripped.startswith(("SSL", "SS-", "SS "))
    ):
        return "SSL"
    return ""


def aggregate_transfer(
    transfer_df: pd.DataFrame,
    *,
    site_col: str,
    barcode_col: str,
    date_col: str,
    qty_col: str,
) -> pd.DataFrame:
    """Per (site_id + barcode): max transfer date + total qty."""
    tf = _dedupe_columns(transfer_df)
    work = pd.DataFrame(
        {
            "_k_site": _norm_key(tf[site_col]),
            "_k_bc": _norm_key(tf[barcode_col]),
            "_dt": pd.to_datetime(_as_series(tf[date_col]), errors="coerce", dayfirst=True),
            "_qty": pd.to_numeric(_as_series(tf[qty_col]), errors="coerce").fillna(0),
        }
    )
    work = work[work["_k_bc"].str.len() > 0]
    return work.groupby(["_k_site", "_k_bc"], as_index=False).agg(
        max_dc_date=("_dt", "max"),
        total_invoice_qty=("_qty", "sum"),
    )


def aggregate_by_date_site_barcode(df: pd.DataFrame) -> pd.DataFrame:
    """Same day + site + barcode: qty & net sales sum, ek row."""
    x = df.copy()
    x["_day"] = pd.to_datetime(x["Date"], errors="coerce").dt.normalize()
    _m = pd.to_numeric(x["MRP"], errors="coerce").fillna(0)
    _q = pd.to_numeric(x["Qty_Sold"], errors="coerce").fillna(0)
    x["_line_gross"] = _m * _q
    grouped = x.groupby(
        ["_day", "Site_ID", "Barcode"], as_index=False, dropna=False
    ).agg(
        Site_Name=("Site_Name", "first"),
        MRP=("MRP", "first"),
        Net_sales_amount=("Net_sales_amount", "sum"),
        Qty_Sold=("Qty_Sold", "sum"),
        SEASON=("SEASON", "first"),
        store_type=("store_type", "first"),
        max_dc_date=("max_dc_date", "max"),
        total_invoice_qty=("total_invoice_qty", "max"),
        _gross_list_sum=("_line_gross", "sum"),
    )
    return grouped.rename(columns={"_day": "Date"})


def build_narrow_report(merged: pd.DataFrame) -> pd.DataFrame:
    """16-column report banao."""
    sale_dt = pd.to_datetime(merged["Date"], errors="coerce")
    mrp_base = pd.to_numeric(merged["MRP"], errors="coerce")
    sold_qty = pd.to_numeric(merged["Qty_Sold"], errors="coerce")
    net_sales_amt = pd.to_numeric(merged["Net_sales_amount"], errors="coerce")

    if "_gross_list_sum" in merged.columns:
        gross_list_amount = pd.to_numeric(merged["_gross_list_sum"], errors="coerce").fillna(0)
        mrp_out = np.where(sold_qty > 0, gross_list_amount / sold_qty, mrp_base.to_numpy(dtype=float))
    else:
        gross_list_amount = mrp_base * sold_qty
        mrp_out = mrp_base.to_numpy(dtype=float)

    mrp_series = pd.Series(np.asarray(mrp_out).ravel(), index=merged.index)

    discount_amt = gross_list_amount - net_sales_amt
    with np.errstate(divide="ignore", invalid="ignore"):
        discount_pct = np.where(gross_list_amount > 0, (discount_amt / gross_list_amount) * 100.0, 0.0)
    discount_pct = np.nan_to_num(discount_pct, nan=0.0, posinf=0.0, neginf=0.0)
    discount_amt = pd.Series(discount_amt, index=merged.index).fillna(0.0).round(2)
    discount_pct = np.round(discount_pct.astype(float), 2)

    iso = sale_dt.dt.isocalendar()

    report = pd.DataFrame(
        {
            "date": sale_dt,
            "site_id": merged["Site_ID"],
            "site_name": merged["Site_Name"],
            "barcode": merged["Barcode"],
            "MRP": mrp_series,
            "net_sales_value": net_sales_amt,
            "Sold_QTY": sold_qty,
            "year": sale_dt.dt.year,
            "month": sale_dt.dt.month,
            "iso_week": iso.week.astype("Int64"),
            "discount_amt": discount_amt,
            "discount_percentage": discount_pct,
            "season": merged["SEASON"],
            "last_purchase_date": pd.to_datetime(merged["max_dc_date"], errors="coerce"),
            "total_purchase_qty": pd.to_numeric(merged["total_invoice_qty"], errors="coerce"),
            "store_type": merged["store_type"],
        }
    )
    return report[OUTPUT_COLUMNS]


def build_report(
    sales_df: pd.DataFrame,
    transfer_df: pd.DataFrame,
    *,
    date_aggregate: bool = True,
) -> pd.DataFrame:
    agg = aggregate_transfer(
        transfer_df,
        site_col="site_id",
        barcode_col="Barcode",
        date_col="Transfer_Date",
        qty_col="Quantity",
    )

    merged = _dedupe_columns(sales_df)
    site_name = _as_series(merged["Site_Name"]) if "Site_Name" in merged.columns else pd.Series("", index=merged.index)
    site_id = _as_series(merged["Site_ID"]) if "Site_ID" in merged.columns else pd.Series("", index=merged.index)
    # Vectorized classify (avoid apply + duplicate-col DataFrame rows)
    merged["store_type"] = [
        derive_store_type(n, i) for n, i in zip(site_name.tolist(), site_id.tolist())
    ]
    # Sirf PT/SSL/CENTRO/ICONIC/EBO — Fashion Factory / B2B / online / blank drop
    merged = merged[merged["store_type"].isin(RETAIL_CHANNELS)].copy()
    merged["_k_site"] = _norm_key(merged["Site_ID"])
    merged["_k_bc"] = _norm_key(merged["Barcode"])
    merged = merged.merge(agg, on=["_k_site", "_k_bc"], how="left")
    merged = merged.drop(columns=["_k_site", "_k_bc"])

    if date_aggregate:
        merged = aggregate_by_date_site_barcode(merged)

    report = build_narrow_report(merged)
    try:
        from pipeline_inputs import filter_df_by_store_type
    except ImportError:
        from pipeline.pipeline_inputs import filter_df_by_store_type  # type: ignore

    return filter_df_by_store_type(report, col="store_type", log_prefix="[sales_report] ")


def main() -> None:
    p = argparse.ArgumentParser(description="Combined sales + transfer -> final report")
    p.add_argument(
        "--sales",
        default="",
        help="Optional sales file; default = Sales (PT, SSL, Centro, Iconic, EBO) via load_combined_sales()",
    )
    p.add_argument(
        "--transfer",
        default="",
        help="Optional transfer file; default = live Transfer (PT/SSL/…) via load_combined_transfer()",
    )
    p.add_argument("--output", default="", help=f"Default: output_files/{DEFAULT_OUTPUT_NAME}")
    p.add_argument(
        "--no-date-aggregate",
        action="store_true",
        help="Har sales row rakho (same date+site+barcode combine mat karo).",
    )
    args = p.parse_args()

    sales_path = Path(args.sales) if args.sales else None
    transfer_path = Path(args.transfer) if args.transfer else None
    # Prefer CSV siblings when an explicit path is given
    if sales_path is not None:
        csv_p = sales_path.with_suffix(".csv")
        if csv_p.exists() and (not sales_path.exists() or sales_path.suffix.lower() == ".xlsx"):
            sales_path = csv_p
    if transfer_path is not None:
        csv_p = transfer_path.with_suffix(".csv")
        if csv_p.exists() and (not transfer_path.exists() or transfer_path.suffix.lower() == ".xlsx"):
            transfer_path = csv_p
    if sales_path is not None and not sales_path.exists():
        raise FileNotFoundError(f"Sales file not found: {sales_path}")
    if transfer_path is not None and not transfer_path.exists():
        raise FileNotFoundError(f"Transfer file not found: {transfer_path}")

    try:
        from pipeline_inputs import (
            _normalize_sales_frame,
            _normalize_transfer_frame,
            load_combined_sales,
            load_combined_transfer,
            resolve_pipeline_sales_path,
            resolve_pipeline_transfer_path,
        )

        # Default: live Sales + Transfer masters (not stale COMBINED_* in output_files)
        if sales_path is None:
            sales_df = load_combined_sales()
            sales_path = resolve_pipeline_sales_path()
        elif sales_path.suffix.lower() == ".csv":
            sales_df = _normalize_sales_frame(pd.read_csv(sales_path, low_memory=False))
        else:
            sales_df = _normalize_sales_frame(pd.read_excel(sales_path))

        if transfer_path is None:
            transfer_df = load_combined_transfer()
            try:
                transfer_path = resolve_pipeline_transfer_path()
            except FileNotFoundError:
                transfer_path = OUTPUT_DIR / "COMBINED_TRANSFER.xlsx"
        elif transfer_path.name.upper().startswith("COMBINED_TRANSFER"):
            # Explicit COMBINED_TRANSFER path → still prefer live Transfer folder
            transfer_df = load_combined_transfer()
            try:
                transfer_path = resolve_pipeline_transfer_path()
            except FileNotFoundError:
                pass
        elif transfer_path.suffix.lower() == ".csv":
            transfer_df = _normalize_transfer_frame(pd.read_csv(transfer_path, low_memory=False))
        else:
            transfer_df = _normalize_transfer_frame(pd.read_excel(transfer_path))
    except Exception:
        if sales_path is None or transfer_path is None:
            raise
        if sales_path.suffix.lower() == ".csv":
            sales_df = pd.read_csv(sales_path, low_memory=False)
        else:
            sales_df = pd.read_excel(sales_path)
        if transfer_path.suffix.lower() == ".csv":
            transfer_df = pd.read_csv(transfer_path, low_memory=False)
        else:
            transfer_df = pd.read_excel(transfer_path)

    print(f"Sales input    : {sales_path.name if sales_path else 'Sales (PT, SSL, Centro, Iconic, EBO)'}", flush=True)
    print(
        f"Transfer input : {transfer_path.name if transfer_path else 'Transfer (PT, SSL, Centro, Iconic)'}",
        flush=True,
    )

    report = build_report(
        sales_df, transfer_df, date_aggregate=not args.no_date_aggregate
    )

    out_path = Path(args.output) if args.output else OUTPUT_DIR / DEFAULT_OUTPUT_NAME
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from pipeline_inputs import save_output_pair
    except ImportError:
        from pipeline.pipeline_inputs import save_output_pair  # type: ignore
    save_output_pair(report, out_path)

    matched = report["total_purchase_qty"].notna().sum()
    print(f"Rows           : {len(report):,}")
    print(f"With purchase  : {matched:,}  (transfer match)")
    print(f"store_type dist:\n{report['store_type'].value_counts().to_string()}")
    print(f"Saved XLSX     : {out_path.with_suffix('.xlsx')}")
    print(f"Saved CSV      : {out_path.with_suffix('.csv')}")


if __name__ == "__main__":
    main()

"""
Map Ginesys dispatch export columns → legacy merge/combine column names.

New Ginesys warehouse dispatch files use INVOICE DATE, CUSTOMER SITE NAME,
STORE CODE, QTY, CAT 3, CAT 5, CAT 6, HSN CODE, INVOICE_RATE, GROSS  AMOUNT — older pipeline expects canonical
35-column schema with DC_DATE, SITE NAME, SITE SHORT NAME, INVOICE_QUANTITY, etc.
"""
from __future__ import annotations

import pandas as pd

CANONICAL_TRANSFER_COLUMNS: list[str] = [
    "NAME",
    "SITE SHORT NAME",
    "SITE NAME",
    "INVOICE_DATE",
    "INVOICE_NO",
    "DOCUMENT DATE",
    "DOCUMENT NO",
    "BARCODE",
    "CATEGORY6",
    "CATEGORY5",
    "CATEGORY4",
    "CATEGORY3",
    "CATEGORY2",
    "DEPARTMENT",
    "SECTION",
    "UOM",
    "HSN",
    "MRP",
    "DC_DATE",
    "DC_NO",
    "INVOICE_QUANTITY",
    "DC BASIC RATE",
    "DC RATE",
    "DISCOUNT AMOUNT",
    "GROSS AMOUNT",
    "TAX AMOUNT",
    "TAXABLE AMOUNT",
    "NET AMOUNT",
    "ZONE",
    "CGST RATE",
    "SGST RATE",
    "IGST RATE",
    "CGST AMOUNT",
    "SGST AMOUNT",
    "IGST AMOUNT",
]

# Mapping of raw dispatch headers (upper stripped) → canonical destination column
_DISPATCH_MAPPINGS: list[tuple[list[str], str]] = [
    (["CUSTOMER NAME", "CUSTOMER_NAME"], "NAME"),
    (["STORE CODE", "STORE_CODE", "SITE CODE"], "SITE SHORT NAME"),
    (["CUSTOMER SITE NAME", "CUSTOMER_SITE_NAME"], "SITE NAME"),
    (["INVOICE DATE", "INVOICE_DATE"], "INVOICE_DATE"),
    (["INVOICE NO", "INVOICE_NO"], "INVOICE_NO"),
    (["DOCUMENT DATE", "DOCUMENT_DATE", "DOC_DATE"], "DOCUMENT DATE"),
    (["DOCUMENT NO", "DOCUMENT_NO", "DOC_NO"], "DOCUMENT NO"),
    (["CAT 6", "CAT6", "CATEGORY 6", "CATEGORY6"], "CATEGORY6"),
    (["CAT 5", "CAT5", "CATEGORY 5", "CATEGORY5"], "CATEGORY5"),
    (["CAT 4", "CAT4", "CATEGORY 4", "CATEGORY4"], "CATEGORY4"),
    (["CAT 3", "CAT3", "CATEGORY 3", "CATEGORY3"], "CATEGORY3"),
    (["CAT 2", "CAT2", "CATEGORY 2", "CATEGORY2"], "CATEGORY2"),
    (["HSN CODE", "HSN_CODE", "HSN"], "HSN"),
    (["MRP", " MRP", "MRP_RATE", "MRP RATE"], "MRP"),
    (["INVOICE DATE", "INVOICE_DATE", "DOCUMENT DATE", "DOC_DATE", "TRANSFER DATE", "TRANSFER_DATE"], "DC_DATE"),
    (["INVOICE NO", "INVOICE_NO", "DOCUMENT NO", "DOC_NO", "DC_NO"], "DC_NO"),
    (["QTY", "QUANTITY", "DISPATCH_QTY", "INVOICE_QUANTITY"], "INVOICE_QUANTITY"),
    (["INVOICE_RATE", "INVOICE RATE", "DC_RATE", "DC RATE"], "DC RATE"),
    (["GROSS  AMOUNT", "GROSS_AMOUNT", "GROSS AMOUNT"], "GROSS AMOUNT"),
]


def _column_lookup(df: pd.DataFrame) -> dict[str, str]:
    return {str(c).strip().upper(): str(c).strip() for c in df.columns}


def ensure_transfer_dc_date_column(df: pd.DataFrame) -> pd.DataFrame:
    """
    Transfer date & doc rule:
      - Fill missing DC_DATE / INVOICE_DATE / DOCUMENT DATE from each other.
      - Fill missing DC_NO / INVOICE_NO / DOCUMENT NO from each other.
    """
    if df is None or df.empty:
        return df
    out = df.copy()
    out.columns = [str(c).strip() for c in out.columns]
    lookup = _column_lookup(out)

    # Date backfills
    dc_col = lookup.get("DC_DATE")
    inv_date_col = lookup.get("INVOICE_DATE") or lookup.get("INVOICE DATE")
    doc_date_col = lookup.get("DOCUMENT DATE") or lookup.get("DOCUMENT_DATE")

    primary_date = (
        (out[dc_col] if dc_col and dc_col in out.columns else None)
        if dc_col
        else None
    )
    if primary_date is None and inv_date_col and inv_date_col in out.columns:
        primary_date = out[inv_date_col]
    if primary_date is None and doc_date_col and doc_date_col in out.columns:
        primary_date = out[doc_date_col]

    if primary_date is not None:
        if "DC_DATE" not in out.columns or out["DC_DATE"].isna().all():
            out["DC_DATE"] = primary_date
        else:
            out["DC_DATE"] = out["DC_DATE"].fillna(primary_date)

        if "INVOICE_DATE" not in out.columns or out["INVOICE_DATE"].isna().all():
            out["INVOICE_DATE"] = out["DC_DATE"]
        else:
            out["INVOICE_DATE"] = out["INVOICE_DATE"].fillna(out["DC_DATE"])

        if "DOCUMENT DATE" not in out.columns or out["DOCUMENT DATE"].isna().all():
            out["DOCUMENT DATE"] = out["DC_DATE"]
        else:
            out["DOCUMENT DATE"] = out["DOCUMENT DATE"].fillna(out["DC_DATE"])

    # Doc number backfills
    inv_no_col = lookup.get("INVOICE_NO") or lookup.get("INVOICE NO")
    dc_no_col = lookup.get("DC_NO") or lookup.get("DC NO")
    doc_no_col = lookup.get("DOCUMENT NO") or lookup.get("DOCUMENT_NO")

    primary_no = (
        (out[inv_no_col] if inv_no_col and inv_no_col in out.columns else None)
        if inv_no_col
        else None
    )
    if primary_no is None and dc_no_col and dc_no_col in out.columns:
        primary_no = out[dc_no_col]
    if primary_no is None and doc_no_col and doc_no_col in out.columns:
        primary_no = out[doc_no_col]

    if primary_no is not None:
        if "INVOICE_NO" not in out.columns or out["INVOICE_NO"].isna().all():
            out["INVOICE_NO"] = primary_no
        else:
            out["INVOICE_NO"] = out["INVOICE_NO"].fillna(primary_no)

        if "DC_NO" not in out.columns or out["DC_NO"].isna().all():
            out["DC_NO"] = out["INVOICE_NO"]
        else:
            out["DC_NO"] = out["DC_NO"].fillna(out["INVOICE_NO"])

        if "DOCUMENT NO" not in out.columns or out["DOCUMENT NO"].isna().all():
            out["DOCUMENT NO"] = out["INVOICE_NO"]
        else:
            out["DOCUMENT NO"] = out["DOCUMENT NO"].fillna(out["INVOICE_NO"])

    return out


def has_transfer_dispatch_shape(df: pd.DataFrame) -> bool:
    """True when dataframe looks like Ginesys dispatch export."""
    if df is None or df.empty:
        return False
    cols = set(_column_lookup(df))
    dispatch_indicators = {
        "INVOICE DATE",
        "INVOICE_DATE",
        "CUSTOMER SITE NAME",
        "STORE CODE",
        "QTY",
        "CAT 6",
        "CAT6",
        "CAT 3",
        "CAT 5",
        "HSN CODE",
        "INVOICE_RATE",
    }
    return bool(cols & dispatch_indicators)


def normalize_transfer_dispatch_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Rename Ginesys dispatch columns to canonical names for date merge.

    Prevents creation of duplicate columns (e.g. INVOICE DATE vs DC_DATE)
    so pd.concat doesn't shift columns or append extra columns on the right.
    """
    if df is None or df.empty:
        return df

    out = df.copy()
    out.columns = [str(c).strip() for c in out.columns]

    for aliases, canonical in _DISPATCH_MAPPINGS:
        lookup = _column_lookup(out)
        matching_cols = [lookup[a] for a in aliases if a in lookup and lookup[a] in out.columns]

        if not matching_cols:
            continue

        if canonical in out.columns:
            for src_col in matching_cols:
                if src_col != canonical:
                    out[canonical] = out[canonical].fillna(out[src_col])
                    out.drop(columns=[src_col], inplace=True)
        else:
            first_src = matching_cols[0]
            out.rename(columns={first_src: canonical}, inplace=True)
            for src_col in matching_cols[1:]:
                if src_col in out.columns:
                    out[canonical] = out[canonical].fillna(out[src_col])
                    out.drop(columns=[src_col], inplace=True)

    out = ensure_transfer_dc_date_column(out)

    # Ensure all canonical columns exist (fill missing with NaN) and retain ONLY canonical columns
    for c in CANONICAL_TRANSFER_COLUMNS:
        if c not in out.columns:
            out[c] = None

    # Keep canonical columns in order + any extra unknown non-standard columns (drop duplicates)
    final_cols = [c for c in CANONICAL_TRANSFER_COLUMNS if c in out.columns]
    extra_cols = [c for c in out.columns if c not in CANONICAL_TRANSFER_COLUMNS]
    if extra_cols:
        # Drop known alias duplicates if any remained
        for ec in extra_cols:
            if ec.upper() in {"INVOICE NO", "CUSTOMER NAME", "CAT 3", "CAT 5", "HSN CODE", "INVOICE_RATE", "GROSS  AMOUNT"}:
                out.drop(columns=[ec], inplace=True)
            else:
                final_cols.append(ec)

    return out[final_cols]


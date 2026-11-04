"""
Sales column definitions and normalization for the canonical 33-column sales table.

Maps new Ginesys daily exports (ENTRY_DATE, NET_AMOUNT, TRANSACTION_SITE, …)
onto the legacy Sales current/master headers (Invoice Date, Item Net Amt, Site, …)
before merge/append so values land in the correct columns.
"""
from __future__ import annotations

import logging
import re
from typing import Dict, List, Tuple

import pandas as pd

log = logging.getLogger("pipeline.sales_columns")

NEW_SALES_COLUMNS: List[str] = [
    "Site",
    "Zone",
    "Site Type",
    "Site Code",
    "Invoice No",
    "Invoice Date",
    "Type",
    "Channel Name",
    "Barcode",
    "Division",
    "Secion",
    "Department",
    "Cat3",
    "Cat6",
    "Cat5",
    "RSP",
    "Qty",
    "Rate",
    "CGST Rate",
    "SGST Rate",
    "IGST Rate",
    "CGST Amt",
    "SGST Amt",
    "IGST Amt",
    "Tax Charge Amt",
    "Taxable Amt",
    "Gross Amt",
    "Discount Amt",
    "Item Discount",
    "Bill Discount",
    "Item Net Amt",
    "Cost Rate",
    "Cost Amt",
]

# Canonical pipeline columns expected downstream by combine_sales, 1.sales_report, etc.
CANONICAL_PIPELINE_MAP: Dict[str, str] = {
    "Invoice Date": "Date",
    "Site Code": "Site_ID",
    "Site": "Site_Name",
    "Barcode": "Barcode",
    "Cat3": "SKU",
    "Cat5": "Size",
    "Qty": "Qty_Sold",
    "RSP": "MRP",
    "Item Net Amt": "Net_sales_amount",
    "Discount Amt": "Discount_amount",
    "Tax Charge Amt": "TAX_AMOUNT",
    "Cat6": "SEASON",
    "Department": "Department",
    "Secion": "Section",
    "Zone": "Zone",
    "Invoice No": "Invoice_No",
    "Channel Name": "Channel",
    "Gross Amt": "Gross_Amt",
    "Cost Amt": "Cost_Amt",
}

# Raw input column variations (normalized upper stripped) -> Standard New Sales Column
# Order matters within each alias list: first match wins as the preferred source.
_HEADER_ALIASES: List[Tuple[List[str], str]] = [
    (
        [
            "SITE",
            "SITE NAME",
            "TRANSACTION_SITE",
            "TRANSACTION SITE",
            "SITE_NAME",
            "CUSTOMER SITE NAME",
            "STORE NAME",
        ],
        "Site",
    ),
    (["ZONE"], "Zone"),
    (["SITE TYPE", "STORE TYPE", "CHANNEL TYPE", "CHANNEL_TYPE"], "Site Type"),
    (
        [
            "SITE CODE",
            "SITE_ID",
            "SITE ID",
            "STORE CODE",
            "STORE_CODE",
            "SITE_CODE",
        ],
        "Site Code",
    ),
    (
        [
            "INVOICE NO",
            "INVOICE_NO",
            "ENTRY_NO",
            "ENTRY NO",
            "BILL NO",
            "BILL_NO",
            "INVOICE NUMBER",
            "BILL NUMBER",
            "DOCUMENT NO",
        ],
        "Invoice No",
    ),
    (
        [
            "INVOICE DATE",
            "INVOICE_DATE",
            "ENTRY_DATE",
            "ENTRY DATE",
            "BILL DATE",
            "BILL_DATE",
            "DOCUMENT DATE",
            "DATE",
        ],
        "Invoice Date",
    ),
    (["TYPE", "TRANSACTION TYPE", "BILL TYPE", "TRANSACTION_TYPE"], "Type"),
    (
        [
            "CHANNEL NAME",
            "CHANNEL_NAME",
            "CHANNEL",
            "SALES CHANNEL",
            "CUSTOMER_NAME",
            "CUSTOMER NAME",
        ],
        "Channel Name",
    ),
    (["BARCODE", "BAR CODE", "ITEM BARCODE"], "Barcode"),
    (["DIVISION"], "Division"),
    (["SECION", "SECTION"], "Secion"),
    (["DEPARTMENT"], "Department"),
    (
        [
            "CAT3",
            "CAT 3",
            "CATEGORY3",
            "CATEGORY 3",
            "SKU",
            "ARTICLE",
            "STYLE",
            "ITEM CODE",
            "ITEM_CODE",
            "ICODE",
        ],
        "Cat3",
    ),
    (["CAT6", "CAT 6", "CATEGORY6", "CATEGORY 6", "SEASON"], "Cat6"),
    (["CAT5", "CAT 5", "CATEGORY5", "CATEGORY 5", "SIZE", "ITEM SIZE", "SIZE CODE"], "Cat5"),
    (["RSP", "MRP", "RETAIL SELLING PRICE", "RSP RATE"], "RSP"),
    (["QTY", "QUANTITY", "BILL_QUANTITY", "QUANTITY SOLD", "QTY SOLD", "INVOICE QTY"], "Qty"),
    (["RATE", "SELLING RATE"], "Rate"),
    (["CGST RATE", "CGST_RATE", "CGST %", "CGST_PCT"], "CGST Rate"),
    (["SGST RATE", "SGST_RATE", "SGST %", "SGST_PCT"], "SGST Rate"),
    (["IGST RATE", "IGST_RATE", "IGST %", "IGST_PCT"], "IGST Rate"),
    (["CGST AMT", "CGST_AMT", "CGST AMOUNT", "CGST_AMOUNT"], "CGST Amt"),
    (["SGST AMT", "SGST_AMT", "SGST AMOUNT", "SGST_AMOUNT"], "SGST Amt"),
    (["IGST AMT", "IGST_AMT", "IGST AMOUNT", "IGST_AMOUNT"], "IGST Amt"),
    (
        [
            "TAX CHARGE AMT",
            "TAX CHARGE AMOUNT",
            "TAX_AMOUNT",
            "TAX AMOUNT",
            "GST AMOUNT",
            "GST_AMOUNT",
            "TAX_CHARGE_AMT",
        ],
        "Tax Charge Amt",
    ),
    (["TAXABLE AMT", "TAXABLE AMOUNT", "TAXABLE_AMOUNT", "TAXABLE_AMT"], "Taxable Amt"),
    (["GROSS AMT", "GROSS AMOUNT", "GROSS_AMOUNT", "GROSS SALES AMOUNT", "GROSS_AMT"], "Gross Amt"),
    (
        [
            "DISCOUNT AMT",
            "DISCOUNT AMOUNT",
            "DISCOUNT_AMOUNT",
            "DISCOUNT_AMT",
            "ITEM DISCOUNT_AMOUNT",
            "ITEM DISCOUNT AMOUNT",
        ],
        "Discount Amt",
    ),
    (["ITEM DISCOUNT", "ITEM DISCOUNT AMT", "ITEM_DISCOUNT", "ITEM_DISCOUNT_AMT"], "Item Discount"),
    (
        [
            "BILL DISCOUNT",
            "BILL DISCOUNT AMT",
            "BILL_DISCOUNT",
            "BILL_DISCOUNT_AMT",
            "BILL DISCOUNT AMT",
        ],
        "Bill Discount",
    ),
    (
        [
            "ITEM NET AMT",
            "ITEM NET AMOUNT",
            "NET AMOUNT",
            "NET_AMOUNT",
            "NET SALES AMOUNT",
            "NET_SALES_AMOUNT",
            "ITEM_NET_AMT",
        ],
        "Item Net Amt",
    ),
    (["COST RATE", "COST_RATE", "UNIT COST", "UNIT_COST"], "Cost Rate"),
    (["COST AMT", "COST AMOUNT", "COST_AMOUNT", "COST_AMT"], "Cost Amt"),
]

# After coalesce, drop these alias headers if the canonical target exists.
_DROP_ALIASES_AFTER = frozenset(
    {
        "NET_AMOUNT",
        "QUANTITY",
        "ENTRY_DATE",
        "ENTRY_NO",
        "BARCODE",
        "TRANSACTION_SITE",
        "STORE CODE",
        "STORE_CODE",
        "CATEGORY3",
        "CATEGORY5",
        "CATEGORY6",
        "CUSTOMER_NAME",
        "TAXABLE_AMOUNT",
        "TAX_AMOUNT",
        "SGST_RATE",
        "CGST_RATE",
        "IGST_RATE",
        "SGST_AMOUNT",
        "CGST_AMOUNT",
        "IGST_AMOUNT",
        "ITEM DISCOUNT_AMOUNT",
        "BILL DISCOUNT AMT",
        "CREATED BY",
        "CREATED ON",
        "ZONE",  # keep Zone canonical; drop uppercase ZONE duplicate if Site Zone exists
        "SITE TYPE",
        "Section",  # prefer Secion canonical after map
    }
)


def _norm_header(h: str) -> str:
    return re.sub(r"[\s_]+", " ", str(h).strip().upper())


def _is_empty_series(s: pd.Series) -> pd.Series:
    if s is None:
        return pd.Series(dtype=bool)
    if pd.api.types.is_numeric_dtype(s):
        return s.isna()
    as_str = s.astype(str).str.strip()
    return s.isna() | as_str.eq("") | as_str.str.lower().isin(("nan", "none", "<na>", "nat", "null"))


def normalize_sales_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Map any raw sales input DataFrame into the standard 33 sales columns.

    - Renames recognized aliases (ENTRY_DATE→Invoice Date, NET_AMOUNT→Item Net Amt, …)
    - If both old and new schemas are present (broken outer-concat), coalesces
      alias columns into the canonical target where the target is empty.
    """
    if df is None or getattr(df, "empty", True):
        return df

    out = df.copy()
    out.columns = [str(c).strip() for c in out.columns]

    coalesced = 0
    renamed = 0

    for aliases, target in _HEADER_ALIASES:
        # Collect all matching source columns currently on the frame
        sources: list[str] = []
        seen_norm: set[str] = set()
        lookup = {_norm_header(c): c for c in out.columns}
        for alias in aliases:
            key = _norm_header(alias)
            src = lookup.get(key)
            if src is None or key in seen_norm:
                continue
            seen_norm.add(key)
            sources.append(src)

        if not sources:
            continue

        # Prefer an existing exact target column as the destination
        if target in out.columns:
            dest = target
        else:
            # Rename the first source to target
            first = sources[0]
            if first != target:
                out = out.rename(columns={first: target})
                renamed += 1
                sources = [target if s == first else s for s in sources]
            dest = target

        # Coalesce remaining sources into dest
        for src in sources:
            if src == dest or src not in out.columns or dest not in out.columns:
                continue
            need = _is_empty_series(out[dest])
            if not bool(need.any()):
                continue
            out.loc[need, dest] = out.loc[need, src]
            coalesced += int(need.sum())

    # Drop pure alias leftovers when canonical target is present
    drop_cols: list[str] = []
    present = set(out.columns)
    for c in list(out.columns):
        if c in NEW_SALES_COLUMNS:
            continue
        if c in _DROP_ALIASES_AFTER or _norm_header(c) in {_norm_header(x) for x in _DROP_ALIASES_AFTER}:
            # Keep if no canonical counterpart exists
            # (ZONE uppercase vs Zone — drop ZONE if Zone exists)
            if c == "ZONE" and "Zone" in present:
                drop_cols.append(c)
            elif c == "SITE TYPE" and "Site Type" in present:
                drop_cols.append(c)
            elif c == "Section" and "Secion" in present:
                drop_cols.append(c)
            elif c in _DROP_ALIASES_AFTER:
                # only drop if we have a mapped canonical already
                drop_cols.append(c)

    if drop_cols:
        out = out.drop(columns=[c for c in drop_cols if c in out.columns], errors="ignore")

    if renamed or coalesced:
        log.info(
            "normalize_sales_columns: renamed=%s coalesced_cells=%s cols_now=%s",
            renamed,
            coalesced,
            len(out.columns),
        )
    return out


def map_sales_to_pipeline_schema(df: pd.DataFrame) -> pd.DataFrame:
    """
    Ensure df has normalized new 33 column headers, then produce pipeline internal schema.
    """
    df_norm = normalize_sales_columns(df)

    out = pd.DataFrame(index=df_norm.index)

    out["Date"] = (
        df_norm["Invoice Date"]
        if "Invoice Date" in df_norm.columns
        else (df_norm["Date"] if "Date" in df_norm.columns else "")
    )
    out["Site_ID"] = (
        df_norm["Site Code"]
        if "Site Code" in df_norm.columns
        else (df_norm["Site_ID"] if "Site_ID" in df_norm.columns else "")
    )
    out["Site_Name"] = (
        df_norm["Site"]
        if "Site" in df_norm.columns
        else (df_norm["Site_Name"] if "Site_Name" in df_norm.columns else "")
    )
    out["Barcode"] = df_norm["Barcode"] if "Barcode" in df_norm.columns else ""
    out["SKU"] = (
        df_norm["Cat3"]
        if "Cat3" in df_norm.columns
        else (df_norm["SKU"] if "SKU" in df_norm.columns else "")
    )
    out["Size"] = (
        df_norm["Cat5"]
        if "Cat5" in df_norm.columns
        else (df_norm["Size"] if "Size" in df_norm.columns else "")
    )
    out["Qty_Sold"] = (
        df_norm["Qty"]
        if "Qty" in df_norm.columns
        else (df_norm["Qty_Sold"] if "Qty_Sold" in df_norm.columns else 0)
    )
    out["MRP"] = (
        df_norm["RSP"]
        if "RSP" in df_norm.columns
        else (df_norm["MRP"] if "MRP" in df_norm.columns else 0)
    )
    out["Net_sales_amount"] = (
        df_norm["Item Net Amt"]
        if "Item Net Amt" in df_norm.columns
        else (
            df_norm["Net_sales_amount"]
            if "Net_sales_amount" in df_norm.columns
            else 0
        )
    )
    out["Discount_amount"] = (
        df_norm["Discount Amt"]
        if "Discount Amt" in df_norm.columns
        else (
            df_norm["Discount_amount"]
            if "Discount_amount" in df_norm.columns
            else 0
        )
    )
    out["TAX_AMOUNT"] = (
        df_norm["Tax Charge Amt"]
        if "Tax Charge Amt" in df_norm.columns
        else (df_norm["TAX_AMOUNT"] if "TAX_AMOUNT" in df_norm.columns else 0)
    )
    out["SEASON"] = (
        df_norm["Cat6"]
        if "Cat6" in df_norm.columns
        else (df_norm["SEASON"] if "SEASON" in df_norm.columns else "")
    )
    out["Department"] = (
        df_norm["Department"]
        if "Department" in df_norm.columns
        else (df_norm["Division"] if "Division" in df_norm.columns else "")
    )
    out["Section"] = (
        df_norm["Secion"]
        if "Secion" in df_norm.columns
        else (df_norm["Section"] if "Section" in df_norm.columns else "")
    )

    for target in NEW_SALES_COLUMNS:
        if target in df_norm.columns and target not in out.columns:
            out[target] = df_norm[target]

    return out

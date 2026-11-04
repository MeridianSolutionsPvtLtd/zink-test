"""
Category Blob merge: incoming/ → current/ (date-overlap or replace).

Layout per category under input/<Folder>/:
  incoming/<file>   ← Home uploads land here
  current/<file>    ← final master used by pipeline sync
  history/previous/<file> ← backup of old current before replace
  history/<STEM>_YYYY-MM-DD.xlsx ← dated snapshot (store SOH only; stamp = run_day-1)

Rules:
  - If incoming is empty for a category → skip (current stays as-is)
  - If incoming has files → backup current → merge/replace → write current → clear incoming
  - Store SOH replace also writes NEW current into dated history (keep ~10)
  - Date-merge categories: overlap days dropped from old, then append new
  - Replace categories: incoming overwrites current (keep incoming basename)
"""
from __future__ import annotations

import logging
import os
import time
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger("pipeline.merge_category_inputs")

TRANSFER_CATEGORY_IDS = frozenset(
    {
        "transfer-pt-ssl-centro-iconic",
        "transfer-master-pt-ssl-centro-iconic",
        "transfer-ebo",
    }
)

TRANSFER_DISPATCH_CATEGORY_IDS = frozenset(
    {
        "transfer-pt-ssl-centro-iconic",
        "transfer-master-pt-ssl-centro-iconic",
    }
)

# Daily + master sales — new Ginesys headers (ENTRY_DATE/NET_AMOUNT/…) → canonical
SALES_NORMALIZE_CATEGORY_IDS = frozenset(
    {
        "sales-pt-ssl-centro-iconic",
        "sales-master-pt-ssl-centro-iconic",
        "ebo-sales",
    }
)


def _transfer_columns_module():
    """Load transfer_columns.py from this folder (works in API threads + /app/pipeline/)."""
    import importlib.util

    path = Path(__file__).resolve().parent / "transfer_columns.py"
    if not path.is_file():
        return None
    mod_name = "_stockpulse_transfer_columns"
    cached = sys.modules.get(mod_name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _sales_columns_module():
    """Load sales_columns.py from this folder (works in API threads + /app/pipeline/)."""
    import importlib.util

    path = Path(__file__).resolve().parent / "sales_columns.py"
    if not path.is_file():
        return None
    mod_name = "_stockpulse_sales_columns"
    cached = sys.modules.get(mod_name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _normalize_transfer_dispatch_df(category_id: str, df: "pd.DataFrame") -> "pd.DataFrame":
    """Map new Ginesys dispatch columns → canonical old names before merge."""
    if category_id not in TRANSFER_DISPATCH_CATEGORY_IDS:
        return df
    mod = _transfer_columns_module()
    if mod is None:
        try:
            from transfer_columns import normalize_transfer_dispatch_columns
        except ImportError:
            try:
                from pipeline.transfer_columns import normalize_transfer_dispatch_columns  # type: ignore
            except ImportError:
                log.warning("transfer_columns unavailable — skipping dispatch column rename")
                return df
        else:
            return normalize_transfer_dispatch_columns(df)
    fn = getattr(mod, "normalize_transfer_dispatch_columns", None)
    if fn is None:
        log.warning("transfer_columns missing normalize_transfer_dispatch_columns")
        return df
    return fn(df)


def _normalize_sales_df(category_id: str, df: "pd.DataFrame") -> "pd.DataFrame":
    """Map new Ginesys sales columns → canonical Sales headers before merge/append."""
    if category_id not in SALES_NORMALIZE_CATEGORY_IDS:
        return df
    if df is None or getattr(df, "empty", True):
        return df
    mod = _sales_columns_module()
    if mod is None:
        try:
            from sales_columns import normalize_sales_columns
        except ImportError:
            try:
                from pipeline.sales_columns import normalize_sales_columns  # type: ignore
            except ImportError:
                log.warning("sales_columns unavailable — skipping sales column normalize")
                return df
        else:
            return normalize_sales_columns(df)
    fn = getattr(mod, "normalize_sales_columns", None)
    if fn is None:
        log.warning("sales_columns missing normalize_sales_columns")
        return df
    return fn(df)


def _normalize_category_df(category_id: str, df: "pd.DataFrame") -> "pd.DataFrame":
    """Apply transfer + sales column normalizers for the category."""
    df = _normalize_transfer_dispatch_df(category_id, df)
    df = _normalize_sales_df(category_id, df)
    return df


def _transfer_reexport_warning(
    *,
    category_id: str,
    cat_label: str,
    old_bounds: Dict[str, Any],
    new_bounds: Dict[str, Any],
) -> Optional[str]:
    if category_id not in TRANSFER_CATEGORY_IDS:
        return None
    o_min, o_max = old_bounds.get("min"), old_bounds.get("max")
    n_min, n_max = new_bounds.get("min"), new_bounds.get("max")
    if o_min is None or o_max is None or n_min is None or n_max is None:
        return None
    if n_max > o_max and n_min <= o_min:
        from date_merge import INCREMENTAL_TRANSFER_HINT

        return (
            f"{cat_label}: Merge used a file that includes old dates again "
            f"({new_bounds.get('min_str')}..{new_bounds.get('max_str')}). "
            f"Next time export only new DC_DATE rows. {INCREMENTAL_TRANSFER_HINT}"
        )
    return None


PIPELINE_DIR = Path(__file__).resolve().parent
MERGED_BACKEND_DIR = PIPELINE_DIR.parent
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))
if str(MERGED_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(MERGED_BACKEND_DIR))

# category_id → merge config
CATEGORY_MERGE_CONFIG: Dict[str, Dict[str, Any]] = {
    "financial-week-info": {
        "folder": "Financial Week Info",
        "mode": "calendar_merge",
        "canonical_name": "FINANCIAL_WEEK_INFO.csv",
        "header_row": 0,
        "column_hints": ["Date", "F. Year", "FY WK"],
    },
    # Rolling sales / invoice — date overlap replace
    "ebo-sales": {
        "folder": "EBO Sales",
        "mode": "date_merge",
        "date_candidates": ["Invoice Date", "INVOICE DATE", "BILL DATE", "Date", "ENTRY_DATE", "Bill Date", "BILL_DATE"],
        "header_row": 3,
        "sheet_name": "EBO SALE REGISTER WITH MOP",
        "canonical_name": "EBO_SALES.csv",
        "column_hints": [
            "Invoice Date",
            "Site Code",
            "Channel Name",
            "Barcode",
            "Cat3",
            "Cat5",
            "Cat6",
            "RSP",
            "Qty",
            "Item Net Amt",
            "Discount Amt",
            "Tax Charge Amt",
            "Cost Rate",
            "Cost Amt",
            "BILL DATE",
            "BARCODE",
            "STORE CODE",
            "SITE_ID",
            "QUANTITY",
            "MRP",
            "NET SALES",
        ],
    },
    "sales-master-pt-ssl-centro-iconic": {
        "folder": "Sales-Master (PT, SSL, Centro, Iconic, EBO)",
        "mode": "date_merge",
        "date_candidates": ["Invoice Date", "INVOICE DATE", "ENTRY_DATE", "Date", "Bill Date", "BILL DATE"],
        "header_row": 0,
        "sheet_name": "UNMANAGED SITE SALE REGISTER_SP",
        "canonical_name": "SSI_PT_CENTRO_ICONIC_EBO_SALES_MASTER.csv",
        "column_hints": [
            "Invoice Date",
            "Site Code",
            "Channel Name",
            "Barcode",
            "Cat3",
            "Cat5",
            "Cat6",
            "RSP",
            "Qty",
            "Item Net Amt",
            "Discount Amt",
            "Tax Charge Amt",
            "Cost Rate",
            "Cost Amt",
            "ENTRY_DATE",
            "BARCODE",
            "STORE CODE",
            "TRANSACTION_SITE",
            "QUANTITY",
            "MRP",
            "CATEGORY3",
            "CATEGORY5",
            "CATEGORY6",
            "NET_AMOUNT",
        ],
    },
    "sales-pt-ssl-centro-iconic": {
        "folder": "Sales (PT, SSL, Centro, Iconic, EBO)",
        "mode": "date_merge",
        "date_candidates": ["Invoice Date", "INVOICE DATE", "ENTRY_DATE", "Date", "Bill Date", "BILL DATE"],
        "header_row": 0,
        "sheet_name": "UNMANAGED SITE SALE REGISTER_SP",
        "canonical_name": "SSI_PT_CENTRO_ICONIC_SALES.csv",
        "column_hints": [
            "Invoice Date",
            "Site Code",
            "Channel Name",
            "Barcode",
            "Cat3",
            "Cat5",
            "Cat6",
            "RSP",
            "Qty",
            "Item Net Amt",
            "Discount Amt",
            "Tax Charge Amt",
            "Cost Rate",
            "Cost Amt",
            "ENTRY_DATE",
            "ENTRY_NO",
            "BARCODE",
            "STORE CODE",
            "TRANSACTION_SITE",
            "CUSTOMER_NAME",
            "QUANTITY",
            "MRP",
            "CATEGORY3",
            "CATEGORY5",
            "CATEGORY6",
            "NET_AMOUNT",
        ],
    },
    "transfer-ebo": {
        "folder": "Transfer - EBO",
        "mode": "date_merge",
        "date_candidates": ["DOCDT", "DOC DATE", "Doc Date", "Transfer Date", "Transfer_Date", "DC_DATE", "Date"],
        "header_row": 0,
        "canonical_name": "EBO_INVOICE_TRACKING.csv",
        "column_hints": [
            "DOCDT",
            "DOCNO",
            "SLNAME",
            "ICODE",
            "BARCODE",
            "Barcode",
            "STFQTY",
            "STFRATE",
            "OUTSITE",
            "INSITE",
            "Transfer Date",
            "DC_DATE",
            "Site",
            "Site_id",
        ],
    },
    "transfer-master-pt-ssl-centro-iconic": {
        "folder": "Transfer-Master (PT, SSL, Centro, Iconic)",
        "mode": "date_merge",
        "date_candidates": [
            "Invoice Date",
            "INVOICE DATE",
            "INVOICE_DATE",
            "DOCDT",
            "DOC DATE",
            "Doc Date",
            "DC_DATE",
            "DOCUMENT DATE",
            "Transfer Date",
            "Transfer_Date",
            "Date",
        ],
        # Soft hint — header_detect picks real header (row 0 for flat exports, row 3 for Ginesys)
        "header_row": 0,
        "sheet_name": "SALES INVOICE TRACKING WITH DC ",
        "canonical_name": "SALES_INVOICE_TRACKING_MASTER.csv",
        "column_hints": [
            "Invoice Date",
            "INVOICE DATE",
            "INVOICE_DATE",
            "Site Code",
            "SITE CODE",
            "Barcode",
            "BARCODE",
            "Qty",
            "QTY",
            "CAT3",
            "CAT5",
            "CAT6",
            "Owner Site",
            "Customer",
            "Customer Code",
            "Ref Site",
            "Invoice No",
            "Invoice Type",
            "NET AMOUNT",
            "MRP",
            "DOCDT",
            "DOCNO",
            "SLNAME",
            "ICODE",
            "STFQTY",
            "STFRATE",
            "OUTSITE",
            "INSITE",
            "DC_DATE",
            "CUSTOMER SITE NAME",
            "STORE CODE",
            "SITE NAME",
            "INVOICE_QUANTITY",
            "CATEGORY6",
            "DOCUMENT DATE",
            "Transfer Date",
        ],
    },
    "transfer-pt-ssl-centro-iconic": {
        "folder": "Transfer - (PT, SSL, Centro, Iconic)",
        "mode": "date_merge",
        "date_candidates": [
            "DOCDT",
            "DOC DATE",
            "Doc Date",
            "DC_DATE",
            "INVOICE DATE",
            "INVOICE_DATE",
            "DOCUMENT DATE",
            "Transfer Date",
            "Transfer_Date",
            "Date",
        ],
        "header_row": 3,
        "sheet_name": "SALES INVOICE TRACKING WITH DC ",
        "canonical_name": "SALES_INVOICE_TRACKING.csv",
        "column_hints": [
            "INVOICE DATE",
            "INVOICE_DATE",
            "DC_DATE",
            "CUSTOMER SITE NAME",
            "STORE CODE",
            "SITE NAME",
            "SITE SHORT NAME",
            "BARCODE",
            "QTY",
            "INVOICE_QUANTITY",
            "CAT 6",
            "CATEGORY6",
            "DOCUMENT DATE",
            "Transfer Date",
        ],
    },
    # Snapshot / masters — replace only when incoming present
    "pantaloons-soh": {
        "folder": "Pantaloons - SOH",
        "mode": "replace",
        "canonical_name": "PANTALOONS_SOH.csv",
        "header_row": 1,
        "sheet_name": "Weekly Stock Report",
        "column_hints": ["Site", "Article EAN", "Current Stock Qty", "Article ID"],
    },
    "shoppers-stop-soh": {
        "folder": "Shoppers Stop - SOH",
        "mode": "replace",
        "canonical_name": "SHOPPERS_STOP_SOH.csv",
        "header_row": 0,
        "sheet_name": "StockDetails.rdl",
        "column_hints": ["Store No.", "GS1 NUMBER", "Qty Available"],
    },
    "iconic-soh": {
        "folder": "Iconic - SOH",
        "mode": "replace",
        "canonical_name": "ICONIC_SOH.csv",
        "header_row": 0,
        "column_hints": ["branch_code", "ean_code", "closing_stc_qty"],
    },
    "ebo-soh": {
        "folder": "EBO - SOH",
        "mode": "replace",
        "canonical_name": "EBO_SOH.csv",
        "header_row": 3,
        "sheet_name": "EBO CLOSING STOCK REGISTER_SP@C",
        "column_hints": [
            "MANUAL CODE",
            "SITE CODE",
            "SITE_CODE",
            "Store Code",
            "Site_id",
            "BARCODE",
            "Barcode",
            "EAN",
            "CLOSING_STOCK_QTY",
            "Current Stock Qty",
            "QTY",
            "Quantity",
        ],
    },
    "centro-soh": {
        "folder": "Centro - SOH",
        "mode": "replace",
        "canonical_name": "CENTRO_SOH.csv",
        "header_row": 0,
        "sheet_name": "Sheet1",
        "column_hints": [
            "SITE",
            "Site",
            "SITE_NAME",
            "EAN UPC",
            "EAN_UPC",
            "TOTAL_STOCK_QTY",
            "Total Stock Quantity",
            "TOTAL_STOCK_VALUE",
            "Unrestricted Stock Quantity",
        ],
    },
    "warehouse-soh": {"folder": "Warehouse SOH", "mode": "replace", "canonical_name": "WAREHOUSE_SOH.csv"},
    "soh-master": {
        "folder": "SOH-Master",
        # Daily stock template is a full all-store snapshot — keep latest only.
        # Manual per-channel SOH uploads (Pantaloons/EBO/…) stay available separately.
        "mode": "replace",
        "date_candidates": ["Date", "DATE", "SOH_DATE", "STOCK_DATE", "Stock Date", "As On Date", "AS_ON_DATE", "AS_OF_DATE", "As Of Date"],
        "header_row": 0,
        "canonical_name": "COMBINED_SOH_MASTER.csv",
        "column_hints": [
            "Date",
            "Site Code",
            "Site_id",
            "SITE CODE",
            "SITE_CODE",
            "Store Code",
            "STORE CODE",
            "SITE",
            "Site",
            "EAN",
            "Barcode",
            "BARCODE",
            "QTY",
            "Quantity",
            "Current Stock Qty",
            "CURRENT STOCK QTY",
            "CLOSING_STOCK_QTY",
            "Closing Stock Qty",
            "Total Stock Quantity",
            "TOTAL_STOCK_QTY",
        ],
    },
    "item-master": {"folder": "Item Master", "mode": "replace", "canonical_name": "ITEM_MASTER.csv"},
    "customer-master": {"folder": "Customer Master", "mode": "replace", "canonical_name": "CUSTOMER_MASTER.csv"},
    "store-grade-list": {"folder": "Store Grade List", "mode": "replace", "canonical_name": "STORE_GRADE.csv"},
    "top-sellers-list": {"folder": "Top Sellers List", "mode": "replace", "canonical_name": "TOP_SELLERS.csv"},
    "sku-remap": {
        "folder": "SKU Remap",
        "mode": "replace",
        "canonical_name": "SKU_REMAP.csv",
        "header_row": 0,
        "column_hints": ["OLD SKU", "NEW SKU", "OLD_SKU", "NEW_SKU"],
    },
    "combined-soh": {
        "folder": "Combined SOH",
        "mode": "replace",
        "canonical_name": "COMBINED_SOH.csv",
        "header_row": 0,
        "column_hints": ["Site Code", "Site_id", "EAN", "Barcode", "QTY", "Date"],
    },
    "target-lfs": {
        "folder": "Target_LFS",
        "mode": "replace",
        "canonical_name": "TARGET_LFS.csv",
        "header_row": 1,
        "column_hints": ["Date", "Site Code", "STORE NAME", "Format", "Target"],
    },
    # Transactional dumps — same date_merge pattern as sales (incoming dates → current master)
    "rtv-data": {
        "folder": "RTV Data",
        "mode": "date_merge",
        "date_candidates": [
            "SALES RETURN DATE",
            "SALES_RETURN_DATE",
            "DOCDT",
            "DOC DATE",
            "Doc Date",
            "DOCUMENT_DATE",
            "DOCUMENT DATE",
            "INVOICE DATE",
            "INVOICE_DATE",
            "RETURN DATE",
            "ENTRY_DATE",
            "Date",
            "DATE",
        ],
        "header_row": 3,
        "canonical_name": "RTV_DATA.csv",
        "column_hints": [
            "CUSTOMER NAME",
            "CUSTOMER SITE NAME",
            "GSTIN NO",
            "STATE NAME",
            "STATE CODE",
            "ENTRY TYPE",
            "SALES RETURN DATE",
            "SALES RETURN NO",
            "INVOICE DATE",
            "INVOICE NO",
            "DC NO",
            "DOCUMENT_DATE",
            "DOCUMENT_NO",
            "BARCODE",
            "CAT 3",
            "CAT 5",
            "CAT 6",
            "HSN_CODE",
            "MRP",
            "RETURN_QUANTITY",
            "SGST_RATE",
            "CGST_RATE",
            "IGST_RATE",
            "SGST_AMOUNT",
            "CGST_AMOUNT",
            "IGST_AMOUNT",
            "TAX AMOUNT",
            "TAXABLE AMOUNT",
            "NET_AMOUNT",
            "REMARKS",
            "DOCDT",
            "DOCNO",
            "SLNAME",
            "ICODE",
            "Barcode",
            "CNAME3",
            "CNAME5",
            "CNAME6",
            "COSTRATE",
            "SCHEME_DOCNO",
            "SITE_COSTRATE",
            "OUTSITE",
            "INSITE",
            "STFQTY",
            "STFRATE",
        ],
    },
    "ibt-data": {
        "folder": "IBT Data",
        "mode": "date_merge",
        "date_candidates": [
            "DOCDT",
            "DOC DATE",
            "Doc Date",
            "TRANSFER DATE",
            "Transfer Date",
            "IBT DATE",
            "DOCUMENT_DATE",
            "DOCUMENT DATE",
            "ENTRY_DATE",
            "Date",
            "DATE",
        ],
        "header_row": 0,
        "canonical_name": "IBT_DATA.csv",
        "column_hints": [
            "DOCDT",
            "DOCNO",
            "SLNAME",
            "ICODE",
            "BARCODE",
            "Barcode",
            "CNAME3",
            "CNAME5",
            "CNAME6",
            "COSTRATE",
            "SCHEME_DOCNO",
            "SITE_COSTRATE",
            "OUTSITE",
            "INSITE",
            "STFQTY",
            "STFRATE",
            "REMARKS",
            "TRANSFER DATE",
            "Transfer Date",
            "IBT DATE",
            "DOCUMENT_DATE",
            "FROM SITE",
            "TO SITE",
            "STORE CODE",
            "SITE CODE",
            "EAN",
            "QTY",
            "QUANTITY",
            "TRANSFER QTY",
        ],
    },
    "grc-report": {
        "folder": "GRC Report",
        "mode": "date_merge",
        "date_candidates": [
            "RECEIVE_DATE",
            "RECEIVE DATE",
            "DOCDT",
            "DOC DATE",
            "Doc Date",
            "GRC DATE",
            "GRN DATE",
            "DOCUMENT_DATE",
            "DOCUMENT DATE",
            "RECEIPT DATE",
            "ENTRY_DATE",
            "Date",
            "DATE",
        ],
        "header_row": 2,
        "canonical_name": "GRC_REPORT.csv",
        "column_hints": [
            "Site",
            "SUPPLIER_NAME",
            "RECEIVE_NO",
            "RECEIVE_DATE",
            "DOCUMENT_NO",
            "BARCODE",
            "Division",
            "Section",
            "Reference Number",
            "Season",
            "Size",
            "CNAME2",
            "Material  type",
            "CNAME5",
            "Sku Code",
            "RECEIVED_QUANTITY",
            "RECEIVED_RATE",
            "amount",
            "DOCDT",
            "DOCNO",
            "SLNAME",
            "ICODE",
            "Barcode",
            "CNAME3",
            "CNAME6",
            "COSTRATE",
            "SCHEME_DOCNO",
            "SITE_COSTRATE",
            "OUTSITE",
            "INSITE",
            "STFQTY",
            "STFRATE",
            "REMARKS",
            "GRC DATE",
            "GRN DATE",
            "DOCUMENT_DATE",
            "RECEIPT DATE",
            "STORE CODE",
            "SITE CODE",
            "EAN",
            "QTY",
            "QUANTITY",
            "RECEIVED QTY",
        ],
    },
}


def _store_soh_ids() -> set:
    """Category ids that get dated history snapshots (like combined_soh)."""
    base = {
        "pantaloons-soh",
        "shoppers-stop-soh",
        "iconic-soh",
        "ebo-soh",
        "centro-soh",
        "warehouse-soh",
        "soh-master",
        "combined-soh",
    }
    try:
        from pipeline_inputs import STORE_SOH_CATEGORY_IDS

        return base.union(set(STORE_SOH_CATEGORY_IDS))
    except Exception:
        return base


def _env(*keys: str, default: str = "") -> str:
    for k in keys:
        v = os.environ.get(k)
        if v:
            return v
    return default


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(MERGED_BACKEND_DIR / ".env")
    except ImportError:
        pass


def _norm_prefix(prefix: str) -> str:
    p = (prefix or "").strip().replace("\\", "/")
    if p and not p.endswith("/"):
        p += "/"
    return p.lstrip("/")


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _guess_content_type(name: str) -> str:
    lower = name.lower()
    if lower.endswith(".csv"):
        return "text/csv"
    if lower.endswith(".xlsx"):
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if lower.endswith(".xls"):
        return "application/vnd.ms-excel"
    return "application/octet-stream"


def _same_tabular_ext(a: str, b: str) -> bool:
    return Path(a).suffix.lower() == Path(b).suffix.lower()


def _bytes_for_canonical(
    data: bytes,
    incoming_name: str,
    canonical: str,
    *,
    date_candidates: Optional[List[str]] = None,
    preferred_header: int = 0,
    preferred_sheet: Any = None,
    column_hints: Optional[List[str]] = None,
    category_id: Optional[str] = None,
) -> Tuple[bytes, str]:
    """
    Accept CSV or Excel incoming; always emit bytes matching canonical extension.
    Prevents storing CSV under .xlsx (BadZipFile) and keeps pipeline names stable.
    """
    from date_merge import read_excel_smart, write_tabular_bytes

    must_normalize = category_id in TRANSFER_DISPATCH_CATEGORY_IDS or (
        category_id in SALES_NORMALIZE_CATEGORY_IDS
    )
    if _same_tabular_ext(incoming_name, canonical) and not must_normalize:
        return data, _guess_content_type(canonical)

    dates = date_candidates or ["Date", "DC_DATE"]
    df, _meta = read_excel_smart(
        data,
        incoming_name,
        date_candidates=dates,
        preferred_header=preferred_header,
        preferred_sheet=preferred_sheet,
        column_hints=column_hints,
    )
    if category_id:
        df = _normalize_category_df(category_id, df)
    return write_tabular_bytes(df, canonical)


def _get_container():
    from azure.storage.blob import BlobServiceClient

    conn = _env("AZURE_STORAGE_CONNECTION_STRING")
    if not conn:
        raise RuntimeError("Set AZURE_STORAGE_CONNECTION_STRING in .env")
    container_name = _env("AZURE_STORAGE_CONTAINER", default="zinc-data")
    input_prefix = _norm_prefix(_env("AZURE_BLOB_INPUT_PREFIX", default="input/"))
    svc = BlobServiceClient.from_connection_string(conn)
    cc = svc.get_container_client(container_name)
    return cc, container_name, input_prefix


def _list_files(cc, prefix: str) -> List[Any]:
    out = []
    for b in cc.list_blobs(name_starts_with=prefix):
        name = b.name
        if name.endswith("/") or Path(name).name in (".keep", ".gitkeep"):
            continue
        if Path(name).name.startswith("~$"):
            continue
        if (b.size or 0) <= 0:
            continue
        # only direct children of prefix (not nested further)
        rel = name[len(prefix) :] if name.startswith(prefix) else name
        if "/" in rel.replace("\\", "/"):
            continue
        out.append(b)
    return out


_INCOMING_BYTES_CACHE: Dict[str, bytes] = {}


def stash_incoming_bytes(blob_name: str, data: bytes) -> None:
    """Avoid re-downloading the file we just uploaded to incoming/."""
    if blob_name and data:
        _INCOMING_BYTES_CACHE[blob_name] = data


def clear_incoming_bytes_cache() -> None:
    _INCOMING_BYTES_CACHE.clear()


def _download(cc, blob_name: str) -> bytes:
    cached = _INCOMING_BYTES_CACHE.pop(blob_name, None)
    if cached is not None:
        return cached
    return cc.get_blob_client(blob_name).download_blob().readall()


def _storage_account_creds() -> tuple[str, str] | None:
    """AccountName + AccountKey from AZURE_STORAGE_CONNECTION_STRING (if present)."""
    conn = _env("AZURE_STORAGE_CONNECTION_STRING") or ""
    parts: Dict[str, str] = {}
    for part in conn.split(";"):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        parts[k.strip()] = v.strip()
    name = parts.get("AccountName") or ""
    key = parts.get("AccountKey") or ""
    if name and key:
        return name, key
    return None


def _blob_read_sas_url(cc, blob_name: str, *, minutes: int = 60) -> str:
    """
    Authenticated source URL for server-side copy.

    Bare blob URLs often fail with CannotVerifyCopySource / NoAuthenticationInformation
    on App Service — then we fall back to download+upload and OOM the worker on large masters.
    """
    from datetime import timedelta
    from azure.storage.blob import BlobSasPermissions, generate_blob_sas

    creds = _storage_account_creds()
    if not creds:
        return cc.get_blob_client(blob_name).url
    account, key = creds
    container = getattr(cc, "container_name", None) or _env("AZURE_STORAGE_CONTAINER", default="zinc-data")
    sas = generate_blob_sas(
        account_name=account,
        container_name=str(container),
        blob_name=blob_name,
        account_key=key,
        permission=BlobSasPermissions(read=True),
        expiry=datetime.now(timezone.utc) + timedelta(minutes=max(5, int(minutes))),
    )
    return f"https://{account}.blob.core.windows.net/{container}/{blob_name}?{sas}"


def _server_copy_blob(cc, src_blob: str, dest_blob: str) -> bool:
    """
    Same-account Azure copy (no download through app). Returns True on success.
    Falls back to False so caller can download+upload.
    """
    dest = cc.get_blob_client(dest_blob)
    # Try SAS URL first (hosted-safe), then plain URL as last attempt.
    source_urls = [_blob_read_sas_url(cc, src_blob), cc.get_blob_client(src_blob).url]
    last_err: Exception | None = None
    for source_url in source_urls:
        try:
            if hasattr(dest, "upload_blob_from_url"):
                dest.upload_blob_from_url(source_url=source_url, overwrite=True)
                return True
            poller = dest.start_copy_from_url(source_url)
            _ = getattr(poller, "status", None)
            props = dest.get_blob_properties()
            copy = getattr(props, "copy", None)
            copy_status = getattr(copy, "status", None) if copy is not None else None
            if copy_status and str(copy_status).lower() not in ("success", "pending"):
                for _ in range(40):
                    props = dest.get_blob_properties()
                    copy = getattr(props, "copy", None)
                    copy_status = getattr(copy, "status", None) if copy is not None else "success"
                    if str(copy_status).lower() == "success":
                        return True
                    if str(copy_status).lower() == "failed":
                        break
                    time.sleep(0.25)
                continue
            return True
        except Exception as e:
            last_err = e
            continue
    log.info("server copy failed %s → %s (%s); will fallback", src_blob, dest_blob, last_err)
    return False


def _backup_blob(cc, src_blob: str, dest_blob: str) -> None:
    """Backup current → history via server copy; fallback to download+upload."""
    if _server_copy_blob(cc, src_blob, dest_blob):
        return
    data = _download(cc, src_blob)
    _upload(cc, dest_blob, data, _guess_content_type(src_blob))


def _blob_exists(cc, blob_name: str) -> bool:
    try:
        return bool(cc.get_blob_client(blob_name).exists())
    except Exception:
        return False


def _download_optional(cc, blob_name: str) -> bytes | None:
    """Download blob or return None when missing (no throw)."""
    try:
        return _download(cc, blob_name)
    except Exception as e:
        msg = str(e).lower()
        if "blobnotfound" in msg or "not exist" in msg or "404" in msg:
            log.warning("Blob missing (skip): %s (%s)", blob_name, e)
            return None
        raise


def _upload(cc, blob_name: str, data: bytes, content_type: str) -> None:
    from azure.storage.blob import ContentSettings

    timeout_s = int(_env("AZURE_BLOB_UPLOAD_TIMEOUT", default="600") or "600")
    attempts = int(_env("AZURE_BLOB_UPLOAD_RETRIES", default="3") or "3")
    backoff_base_s = float(_env("AZURE_BLOB_UPLOAD_RETRY_BACKOFF_BASE", default="1.5") or "1.5")

    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            cc.upload_blob(
                name=blob_name,
                data=data,
                overwrite=True,
                content_settings=ContentSettings(content_type=content_type),
                timeout=timeout_s,
                max_concurrency=2,
            )
            return
        except Exception as e:  # azure can raise several types (TimeoutError/ServiceRequestError/etc.)
            last_exc = e
            msg = str(e).lower()
            is_network_err = (
                "timeout" in msg
                or "timed out" in msg
                or "connection" in msg
                or "socket" in msg
                or "ssl" in msg
                or "operation did not complete" in msg
                or "aborted" in msg
                or "reset" in msg
                or "broken pipe" in msg
            )
            if not is_network_err or attempt >= attempts:
                raise
            # retry with exponential backoff
            log.warning("Upload attempt %d/%d failed for %s (%s); retrying...", attempt, attempts, blob_name, e)
            time.sleep(backoff_base_s ** (attempt - 1))

    # Should never reach here, but keep readers calm.
    if last_exc is not None:
        raise last_exc


def _delete(cc, blob_name: str) -> None:
    try:
        cc.delete_blob(blob_name)
    except Exception as e:
        log.warning("delete failed %s: %s", blob_name, e)


def _date_bounds_meta_blob(current_prefix: str, canonical: str) -> str:
    return f"{current_prefix}{Path(canonical).stem}.date_bounds.json"


def _load_date_bounds_meta_raw(cc, current_prefix: str, canonical: str) -> Optional[Dict[str, Any]]:
    """Load sidecar JSON without requiring max_str (keeps coverage fields)."""
    import json

    from date_merge import bounds_from_meta

    blob = _date_bounds_meta_blob(current_prefix, canonical)
    raw = _download_optional(cc, blob)
    if not raw:
        return None
    try:
        meta = json.loads(raw.decode("utf-8"))
        if not isinstance(meta, dict):
            return None
        return bounds_from_meta(meta)
    except Exception as e:
        log.info("date bounds meta unreadable %s: %s", blob, e)
        return None


def _load_date_bounds_meta(cc, current_prefix: str, canonical: str) -> Optional[Dict[str, Any]]:
    meta = _load_date_bounds_meta_raw(cc, current_prefix, canonical)
    if not meta or not meta.get("max_str"):
        return None
    return meta


def _save_date_bounds_meta(
    cc,
    current_prefix: str,
    canonical: str,
    bounds: Dict[str, Any],
    *,
    dry_run: bool = False,
    coverage_extra: Optional[Dict[str, Any]] = None,
) -> bool:
    import json

    from date_merge import bounds_public

    if dry_run or not bounds:
        return False
    payload = bounds_public(bounds)
    if coverage_extra:
        if coverage_extra.get("assumed_covered_through"):
            payload["assumed_covered_through"] = str(coverage_extra["assumed_covered_through"])[:10]
        dates = coverage_extra.get("merged_file_dates")
        if isinstance(dates, (list, tuple, set)):
            payload["merged_file_dates"] = sorted({str(d)[:10] for d in dates if d})
    # Preserve coverage fields already on bounds
    if bounds.get("assumed_covered_through") and "assumed_covered_through" not in payload:
        payload["assumed_covered_through"] = str(bounds["assumed_covered_through"])[:10]
    if bounds.get("merged_file_dates") and "merged_file_dates" not in payload:
        payload["merged_file_dates"] = sorted(
            {str(d)[:10] for d in bounds["merged_file_dates"] if d}
        )
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    blob = _date_bounds_meta_blob(current_prefix, canonical)
    try:
        _upload(
            cc,
            blob,
            json.dumps(payload, indent=2).encode("utf-8"),
            "application/json",
        )
        log.info(
            "Saved date bounds meta %s → %s..%s (merged_dates=%s)",
            blob,
            payload.get("min_str"),
            payload.get("max_str"),
            len(payload.get("merged_file_dates") or []),
        )
        return True
    except Exception as e:
        log.warning("date bounds meta save failed %s: %s", blob, e)
        return False


def _compute_and_save_date_bounds(
    cc,
    *,
    current_prefix: str,
    canonical: str,
    data: bytes,
    cfg: Dict[str, Any],
    category_id: str,
    prior_meta: Optional[Dict[str, Any]] = None,
    incoming_filename: Optional[str] = None,
    dry_run: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    Scan merged current bytes → write *.date_bounds.json sidecar.

    Always run after a successful current/ write so coverage/UI never
    sees a master file without a snapshot JSON (transfer UTF-8 / replace
    paths used to skip this).
    """
    date_candidates = list(cfg.get("date_candidates") or [])
    if not date_candidates or not data:
        return None
    try:
        from date_merge import excel_date_bounds_fast
        from header_detect import merge_column_hints, resolve_category_layout
    except ImportError:
        from pipeline.date_merge import excel_date_bounds_fast  # type: ignore
        from pipeline.header_detect import merge_column_hints, resolve_category_layout  # type: ignore

    try:
        layout = resolve_category_layout(cfg, data, Path(canonical).name)
        bounds = excel_date_bounds_fast(
            data,
            Path(canonical).name,
            date_candidates=date_candidates,
            preferred_header=int(layout.get("header_row") or cfg.get("header_row") or 0),
            preferred_sheet=layout.get("sheet") or cfg.get("sheet_name"),
            column_hints=layout.get("column_hints")
            or merge_column_hints(cfg)
            or cfg.get("column_hints"),
        )
    except Exception as e:
        log.warning(
            "date bounds scan failed for %s (%s): %s",
            category_id,
            canonical,
            e,
        )
        return None

    if not bounds or not bounds.get("max_str"):
        return None

    # Sparse coverage: keep prior fields; add this file's date when known
    incoming_dates = _incoming_file_dates_for_coverage(
        incoming_filename or canonical, bounds
    )
    if prior_meta is not None or incoming_dates:
        bounds = _enrich_bounds_coverage(
            bounds,
            prior_meta=prior_meta,
            incoming_file_dates=incoming_dates,
        )
    elif not bounds.get("assumed_covered_through"):
        # First snapshot for this master — freeze continuous history through max
        bounds["assumed_covered_through"] = str(bounds["max_str"])[:10]
        bounds["merged_file_dates"] = [
            str(d) for d in incoming_dates
        ] if incoming_dates else []

    ok = _save_date_bounds_meta(
        cc, current_prefix, canonical, bounds, dry_run=dry_run
    )
    return bounds if ok else None


def backfill_missing_date_bounds(
    *,
    only_ids: Optional[List[str]] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Create *.date_bounds.json for current/ masters that are missing it."""
    _load_dotenv()
    cc, container_name, input_prefix = _get_container()
    ids = only_ids or list(CATEGORY_MERGE_CONFIG.keys())
    written: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    errors: List[str] = []

    for cid in ids:
        cfg = CATEGORY_MERGE_CONFIG.get(cid)
        if not cfg:
            continue
        if not cfg.get("date_candidates"):
            skipped.append({"category_id": cid, "reason": "no_date_candidates"})
            continue
        folder = cfg["folder"]
        canonical = cfg.get("canonical_name") or ""
        paths = _category_prefixes(input_prefix, folder)
        existing = _load_date_bounds_meta_raw(cc, paths["current"], canonical)
        if existing and existing.get("max_str"):
            skipped.append(
                {
                    "category_id": cid,
                    "reason": "already_exists",
                    "max_str": existing.get("max_str"),
                }
            )
            continue
        current = _list_files(cc, paths["current"])
        current_blob = None
        for b in current:
            if Path(b.name).name == canonical:
                current_blob = b
                break
        if current_blob is None and current:
            current_blob = current[0]
        if current_blob is None:
            skipped.append({"category_id": cid, "reason": "no_current_file"})
            continue
        try:
            data = _download(cc, current_blob.name)
            bounds = _compute_and_save_date_bounds(
                cc,
                current_prefix=paths["current"],
                canonical=canonical or Path(current_blob.name).name,
                data=data,
                cfg=cfg,
                category_id=cid,
                dry_run=dry_run,
            )
            if bounds:
                written.append(
                    {
                        "category_id": cid,
                        "blob": _date_bounds_meta_blob(
                            paths["current"], canonical or Path(current_blob.name).name
                        ),
                        "min_str": bounds.get("min_str"),
                        "max_str": bounds.get("max_str"),
                        "total_rows": bounds.get("total_rows"),
                    }
                )
            else:
                errors.append(f"{cid}: scan/save returned empty bounds")
        except Exception as e:
            log.exception("backfill date bounds failed %s", cid)
            errors.append(f"{cid}: {e}")

    return {
        "ok": len(errors) == 0,
        "container": container_name,
        "written": written,
        "skipped": skipped,
        "errors": errors,
        "dry_run": dry_run,
    }


def _list_raw_daily_dates_for_category(category_id: str) -> List[Any]:
    """Best-effort Raw_Files dates for sparse coverage bootstrap."""
    try:
        try:
            from ginesys_raw import (
                _env,
                _get_container,
                _list_raw_with_fallback,
                _norm_prefix,
                is_history_or_bulk_dump,
                parse_file_date,
            )
        except ImportError:
            from pipeline.ginesys_raw import (
                _env,
                _get_container,
                _list_raw_with_fallback,
                _norm_prefix,
                is_history_or_bulk_dump,
                parse_file_date,
            )

        cc, _ = _get_container()
        preferred = _norm_prefix(_env("AZURE_BLOB_RAW_PREFIX", default="Raw_Files/"))
        _, listed, _ = _list_raw_with_fallback(cc, preferred)
        out = []
        for rec in listed:
            if rec.get("category_id") != category_id:
                continue
            fname = str(rec.get("filename") or "")
            if is_history_or_bulk_dump(fname):
                continue
            fd = parse_file_date(fname)
            if fd is not None:
                out.append(fd)
        return out
    except Exception as e:
        log.info("raw daily dates bootstrap skipped for %s: %s", category_id, e)
        return []


def _incoming_file_dates_for_coverage(filename: str, bounds: Optional[Dict[str, Any]] = None) -> List[Any]:
    try:
        try:
            from ginesys_raw import parse_file_date
        except ImportError:
            from pipeline.ginesys_raw import parse_file_date
    except Exception:
        parse_file_date = None  # type: ignore

    dates: List[Any] = []
    if parse_file_date is not None:
        fd = parse_file_date(Path(filename).name)
        if fd is not None:
            dates.append(fd)
    if not dates and bounds:
        mn = bounds.get("min_str") or bounds.get("min")
        mx = bounds.get("max_str") or bounds.get("max")
        if mn is not None and mx is not None and str(mn)[:10] == str(mx)[:10]:
            try:
                from datetime import date as _date

                dates.append(_date.fromisoformat(str(mn)[:10]))
            except ValueError:
                dates.append(str(mn)[:10])
    return dates


def _enrich_bounds_coverage(
    bounds: Dict[str, Any],
    *,
    prior_meta: Optional[Dict[str, Any]],
    incoming_file_dates: Optional[List[Any]],
) -> Dict[str, Any]:
    from date_merge import update_coverage_after_merge

    cov = update_coverage_after_merge(
        prior_meta,
        old_max=(prior_meta or {}).get("max") if prior_meta else None,
        incoming_file_dates=incoming_file_dates,
    )
    out = dict(bounds or {})
    if cov.get("assumed_covered_through"):
        out["assumed_covered_through"] = cov["assumed_covered_through"]
    out["merged_file_dates"] = cov.get("merged_file_dates") or []
    return out


def _ensure_coverage_meta(
    category_id: str, old_bounds: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    from date_merge import bootstrap_coverage_meta

    meta = dict(old_bounds or {})
    if meta.get("assumed_covered_through") or meta.get("merged_file_dates"):
        return bootstrap_coverage_meta(meta)
    raw_dates = _list_raw_daily_dates_for_category(category_id)
    return bootstrap_coverage_meta(
        meta, raw_file_dates=raw_dates, current_max=meta.get("max_str") or meta.get("max")
    )


def _category_prefixes(input_prefix: str, folder: str) -> Dict[str, str]:
    base = f"{input_prefix}{folder}/"
    return {
        "base": base,
        "incoming": f"{base}incoming/",
        "current": f"{base}current/",
        "history": f"{base}history/",
    }


def _history_keep() -> int:
    """How many timestamped history runs to retain (default 1 = only previous)."""
    try:
        return max(1, int(_env("BLOB_HISTORY_KEEP", default="1") or "1"))
    except ValueError:
        return 1


def _list_history_run_prefixes(cc, history_prefix: str) -> List[str]:
    """
    Return sorted run folder prefixes under history/ (e.g. .../history/20260718_120000/).
    """
    runs = set()
    for b in cc.list_blobs(name_starts_with=history_prefix):
        rel = b.name[len(history_prefix) :] if b.name.startswith(history_prefix) else ""
        if not rel:
            continue
        parts = rel.replace("\\", "/").split("/")
        if not parts or not parts[0] or parts[0] in (".keep", ".gitkeep"):
            continue
        # skip fixed slot names
        if parts[0] in ("previous", "previous_incoming"):
            continue
        # timestamp folders look like 20260718_121530
        if len(parts[0]) >= 8 and parts[0][0].isdigit():
            runs.add(f"{history_prefix}{parts[0]}/")
    return sorted(runs)


def prune_history(category_folder: str, *, keep: Optional[int] = None) -> Dict[str, Any]:
    """Delete oldest history/<timestamp>/ runs beyond keep count."""
    _load_dotenv()
    cc, _, input_prefix = _get_container()
    paths = _category_prefixes(input_prefix, category_folder)
    keep_n = _history_keep() if keep is None else max(1, keep)
    runs = _list_history_run_prefixes(cc, paths["history"])
    deleted: List[str] = []
    if len(runs) <= keep_n:
        return {"ok": True, "kept": runs, "deleted": deleted, "keep": keep_n}
    # sorted ascending → oldest first
    to_delete = runs[: len(runs) - keep_n]
    for run_prefix in to_delete:
        for b in list(cc.list_blobs(name_starts_with=run_prefix)):
            _delete(cc, b.name)
            deleted.append(b.name)
    return {"ok": True, "kept": runs[len(runs) - keep_n :], "deleted": deleted, "keep": keep_n}


def archive_incoming_to_history(category_folder: str, *, dry_run: bool = False) -> Dict[str, Any]:
    """
    Move any existing files under incoming/ → history/previous_incoming/ (overwrite).
    Only one previous incoming backup is kept.
    """
    _load_dotenv()
    cc, container_name, input_prefix = _get_container()
    logging.getLogger("azure").setLevel(logging.WARNING)
    paths = _category_prefixes(input_prefix, category_folder)
    incoming = _list_files(cc, paths["incoming"])
    hist_prefix = f"{paths['history']}previous_incoming/"
    moved: List[Dict[str, str]] = []
    errors: List[str] = []

    if not incoming:
        return {
            "ok": True,
            "container": container_name,
            "folder": category_folder,
            "moved_count": 0,
            "moved": [],
            "errors": [],
            "history_prefix": None,
        }

    # Clear previous_incoming slot first (single backup only)
    if not dry_run:
        for old in cc.list_blobs(name_starts_with=hist_prefix):
            if Path(old.name).name not in (".keep", ".gitkeep"):
                _delete(cc, old.name)

    for b in incoming:
        dest = f"{hist_prefix}{Path(b.name).name}"
        moved.append({"from": b.name, "to": dest})
        if dry_run:
            continue
        try:
            data = _download(cc, b.name)
            _upload(cc, dest, data, _guess_content_type(b.name))
            _delete(cc, b.name)
        except Exception as e:
            errors.append(f"{b.name}: {e}")

    return {
        "ok": len(errors) == 0,
        "container": container_name,
        "folder": category_folder,
        "moved_count": len(moved),
        "moved": moved,
        "errors": errors,
        "history_prefix": hist_prefix if moved else None,
        "retention": "previous_incoming only (overwrite)",
    }


def merge_one_category(
    category_id: str,
    *,
    dry_run: bool = False,
    keep_incoming: bool = False,
    on_progress: Optional[Callable[[int, str], None]] = None,
    folder_override: Optional[str] = None,
) -> Dict[str, Any]:
    def _p(pct: int, msg: str) -> None:
        if on_progress:
            try:
                on_progress(pct, msg)
            except Exception:
                pass

    _load_dotenv()
    cfg = CATEGORY_MERGE_CONFIG.get(category_id)
    if not cfg:
        return {"ok": False, "category_id": category_id, "skipped": True, "reason": "unknown_category"}

    folder = folder_override or cfg["folder"]
    mode = cfg.get("mode", "replace")
    _p(5, f"{folder}: checking Blob…")
    cc, container_name, input_prefix = _get_container()
    logging.getLogger("azure").setLevel(logging.WARNING)

    paths = _category_prefixes(input_prefix, folder)
    incoming = _list_files(cc, paths["incoming"])
    current = _list_files(cc, paths["current"])

    result: Dict[str, Any] = {
        "ok": True,
        "category_id": category_id,
        "folder": folder,
        "mode": mode,
        "container": container_name,
        "incoming_count": len(incoming),
        "current_count": len(current),
        "skipped": False,
        "backed_up": [],
        "written": None,
        "cleared_incoming": [],
        "stats": None,
        "errors": [],
        "warnings": [],
    }

    if not incoming:
        result["skipped"] = True
        result["reason"] = "no_incoming — kept existing current"
        return result

    # Pick primary current (canonical name preferred, else first)
    canonical = cfg.get("canonical_name") or Path(incoming[0].name).name
    if not str(canonical).lower().endswith((".xlsx", ".xls", ".csv")):
        canonical = f"{canonical}.xlsx"

    current_blob = None
    for b in current:
        if Path(b.name).name == canonical:
            current_blob = b
            break
    if current_blob is None:
        # Also match same stem with other tabular ext (e.g. .csv vs .xlsx)
        stem = Path(canonical).stem.lower()
        for b in current:
            if Path(b.name).stem.lower() == stem and Path(b.name).suffix.lower() in (
                ".xlsx",
                ".xls",
                ".csv",
            ):
                current_blob = b
                break
    if current_blob is None and current:
        current_blob = current[0]
        # keep existing current filename when merging into it
        canonical = Path(current_blob.name).name
    # Prefer configured canonical extension for final write (usually .xlsx)
    cfg_canonical = cfg.get("canonical_name")
    if cfg_canonical and str(cfg_canonical).lower().endswith((".xlsx", ".xls", ".csv")):
        # If we matched a stem-sibling, still write to the configured name
        if Path(canonical).stem.lower() == Path(str(cfg_canonical)).stem.lower():
            canonical = str(cfg_canonical)

    # If current/ was wiped by a prior failed merge, restore from history/previous/
    if current_blob is None and not dry_run:
        prev_prefix = f"{paths['history']}previous/"
        prev_files = _list_files(cc, prev_prefix)
        prev_match = None
        for b in prev_files:
            if Path(b.name).name == canonical or Path(b.name).stem.lower() == Path(canonical).stem.lower():
                prev_match = b
                break
        if prev_match is None and prev_files:
            prev_match = prev_files[0]
        if prev_match is not None:
            restored = _download_optional(cc, prev_match.name)
            if restored:
                dest = f"{paths['current']}{canonical}"
                _upload(cc, dest, restored, _guess_content_type(canonical))
                log.warning(
                    "Restored missing current for %s from %s → %s (%s bytes)",
                    category_id,
                    prev_match.name,
                    dest,
                    len(restored),
                )
                result["warnings"].append(
                    f"Restored missing current/ from history/previous ({Path(prev_match.name).name})"
                )
                # refresh current listing / pointer
                current = _list_files(cc, paths["current"])
                current_blob = None
                for b in current:
                    if Path(b.name).name == canonical:
                        current_blob = b
                        break
                if current_blob is None and current:
                    current_blob = current[0]

    # Same file re-upload: identical size → skip merge (no download / pandas)
    if (
        mode == "date_merge"
        and current_blob is not None
        and len(incoming) == 1
    ):
        cur_sz = int(getattr(current_blob, "size", 0) or 0)
        inc_sz = int(getattr(incoming[0], "size", 0) or 0)
        if cur_sz > 0 and cur_sz == inc_sz:
            log.info(
                "Skip merge %s — same size as current (%s bytes)",
                category_id,
                cur_sz,
            )
            result["skipped"] = True
            result["reason"] = "same_file_size — merge skipped"
            result["written"] = current_blob.name
            result["stats"] = {
                "mode": "skip_same_size",
                "bytes": cur_sz,
                "current": current_blob.name,
                "incoming": incoming[0].name,
            }
            if not keep_incoming and not dry_run:
                for b in incoming:
                    _delete(cc, b.name)
                    result["cleared_incoming"].append(b.name)
            return result

    # ── DATE GATE (before backup): only read last dates, not the whole master ──
    # Prefer sidecar *.date_bounds.json for current max; download master only if
    # meta missing OR we actually need to merge/append.
    prefetched_new_bytes: Optional[bytes] = None
    prefetched_new_bounds: Optional[Dict[str, Any]] = None
    prefetched_old_bounds: Optional[Dict[str, Any]] = None
    if mode == "date_merge" and current_blob is not None and len(incoming) == 1:
        from date_merge import (
            excel_date_bounds_fast,
            incoming_covers_current,
            is_file_date_covered,
            should_skip_merge_by_dates,
        )
        from header_detect import merge_column_hints, resolve_category_layout

        date_candidates = cfg.get("date_candidates") or ["Date"]
        cat_label = folder
        primary = incoming[0]
        _p(12, f"{cat_label}: reading new file dates…")

        try:
            prefetched_new_bytes = _download(cc, primary.name)
            layout = resolve_category_layout(
                cfg, prefetched_new_bytes, Path(primary.name).name
            )
            header_row = int(layout["header_row"])
            preferred_sheet = layout.get("sheet") or cfg.get("sheet_name")
            column_hints = layout.get("column_hints") or merge_column_hints(cfg)
            prefetched_new_bounds = excel_date_bounds_fast(
                prefetched_new_bytes,
                Path(primary.name).name,
                date_candidates=date_candidates,
                preferred_header=header_row,
                preferred_sheet=preferred_sheet,
                column_hints=column_hints,
            )
            old_bounds = _load_date_bounds_meta(cc, paths["current"], canonical)
            if old_bounds is None:
                # One-time bootstrap: scan current once, save meta, never again until merge
                log.info(
                    "No date meta for %s — scanning current once for last date",
                    category_id,
                )
                old_bytes_once = _download_optional(cc, current_blob.name)
                if old_bytes_once is None:
                    raise FileNotFoundError(f"current missing: {current_blob.name}")
                old_bounds = excel_date_bounds_fast(
                    old_bytes_once,
                    Path(current_blob.name).name,
                    date_candidates=date_candidates,
                    preferred_header=header_row,
                    preferred_sheet=preferred_sheet,
                    column_hints=column_hints,
                )
                # Freeze continuous history as covered through current max
                if old_bounds.get("max_str") and not old_bounds.get("assumed_covered_through"):
                    old_bounds["assumed_covered_through"] = str(old_bounds["max_str"])[:10]
                    old_bounds["merged_file_dates"] = []
                _save_date_bounds_meta(
                    cc, paths["current"], canonical, old_bounds, dry_run=dry_run
                )
                del old_bytes_once

            cov_meta = _ensure_coverage_meta(category_id, old_bounds)
            # Keep coverage fields on the in-memory old_bounds for later saves
            old_bounds = {**old_bounds, **{
                k: cov_meta[k]
                for k in ("assumed_covered_through", "merged_file_dates")
                if k in cov_meta
            }}
            prefetched_old_bounds = old_bounds
            incoming_dates = _incoming_file_dates_for_coverage(
                Path(primary.name).name, prefetched_new_bounds
            )
            allow_gap_fill = False
            if incoming_dates:
                allow_gap_fill = not is_file_date_covered(incoming_dates[0], cov_meta)
            elif prefetched_new_bounds.get("max") is not None and old_bounds.get("max") is not None:
                # No filename date — allow gap fill when incoming range is older than current max
                allow_gap_fill = prefetched_new_bounds["max"] < old_bounds["max"]

            log.info(
                "Date gate %s — current_max=%s incoming=%s..%s gap_fill=%s (no full master read for skip)",
                category_id,
                old_bounds.get("max_str"),
                prefetched_new_bounds.get("min_str"),
                prefetched_new_bounds.get("max_str"),
                allow_gap_fill,
            )
            skip, skip_reason = should_skip_merge_by_dates(
                old_bounds,
                prefetched_new_bounds,
                allow_gap_fill=allow_gap_fill,
            )
            if skip:
                log.info("Skip merge %s (last-date gate) — %s", category_id, skip_reason)
                result["skipped"] = True
                result["reason"] = skip_reason
                result["written"] = current_blob.name
                result["stats"] = {
                    "mode": "skip_by_last_date",
                    "reason": skip_reason,
                    "current_dates": {
                        "min": old_bounds.get("min_str"),
                        "max": old_bounds.get("max_str"),
                    },
                    "incoming_dates": {
                        "min": prefetched_new_bounds.get("min_str"),
                        "max": prefetched_new_bounds.get("max_str"),
                    },
                }
                if not keep_incoming and not dry_run:
                    for b in incoming:
                        _delete(cc, b.name)
                        result["cleared_incoming"].append(b.name)
                return result

            # Incoming fully covers current → replace without merging old rows
            if incoming_covers_current(old_bounds, prefetched_new_bounds):
                log.info(
                    "Full replace %s — incoming %s..%s covers current %s..%s",
                    category_id,
                    prefetched_new_bounds.get("min_str"),
                    prefetched_new_bounds.get("max_str"),
                    old_bounds.get("min_str"),
                    old_bounds.get("max_str"),
                )
                history_prefix = f"{paths['history']}previous/"
                for b in current:
                    dest = f"{history_prefix}{Path(b.name).name}"
                    result["backed_up"].append({"from": b.name, "to": dest})
                    if not dry_run:
                        try:
                            _backup_blob(cc, b.name, dest)
                        except Exception as be:
                            log.warning("backup skipped %s: %s", b.name, be)
                out_blob = f"{paths['current']}{canonical}"
                out_bytes, ctype = _bytes_for_canonical(
                    prefetched_new_bytes,
                    Path(primary.name).name,
                    canonical,
                    date_candidates=date_candidates,
                    preferred_header=header_row,
                    preferred_sheet=preferred_sheet,
                    column_hints=column_hints,
                    category_id=category_id,
                )
                result["written"] = out_blob
                result["stats"] = {
                    "mode": "fast_full_replace",
                    "bytes": len(out_bytes),
                    "current_dates": {
                        "min": old_bounds.get("min_str"),
                        "max": old_bounds.get("max_str"),
                    },
                    "incoming_dates": {
                        "min": prefetched_new_bounds.get("min_str"),
                        "max": prefetched_new_bounds.get("max_str"),
                    },
                }
                reexport = _transfer_reexport_warning(
                    category_id=category_id,
                    cat_label=cat_label,
                    old_bounds=old_bounds,
                    new_bounds=prefetched_new_bounds,
                )
                if reexport:
                    result["warnings"].append(reexport)
                if not dry_run:
                    _upload(cc, out_blob, out_bytes, ctype)
                    save_bounds = _enrich_bounds_coverage(
                        prefetched_new_bounds,
                        prior_meta=old_bounds,
                        incoming_file_dates=incoming_dates,
                    )
                    _save_date_bounds_meta(
                        cc,
                        paths["current"],
                        canonical,
                        save_bounds,
                        dry_run=False,
                    )
                    for b in current:
                        # Keep sidecar meta (and delta helper files) — otherwise
                        # preview/UI has to re-scan the full master again.
                        if (
                            b.name != out_blob
                            and not b.name.endswith(".date_bounds.json")
                            and not b.name.endswith(".delta.csv")
                        ):
                            _delete(cc, b.name)
                    if not keep_incoming:
                        for b in incoming:
                            _delete(cc, b.name)
                            result["cleared_incoming"].append(b.name)
                return result

            # Strictly newer days → download current bytes + append rows only
            # (no pandas load of full master, no full rewrite of historical rows)
            old_max = old_bounds.get("max")
            new_min = prefetched_new_bounds.get("min")
            new_max = prefetched_new_bounds.get("max")
            # Gap-fill (incoming older than current max) → fall through to full merge
            if (
                old_max is not None
                and new_max is not None
                and new_max < old_max
                and allow_gap_fill
            ):
                log.info(
                    "Gap-fill queued %s — incoming_max(%s) < current_max(%s); full merge path",
                    category_id,
                    prefetched_new_bounds.get("max_str"),
                    old_bounds.get("max_str"),
                )
                # Keep prefetched_* for full merge below; do not return
            elif old_max is not None and new_min is not None and new_min > old_max:
                from date_merge import append_rows_to_tabular_bytes, read_excel_smart

                log.info(
                    "Binary append %s — incoming_min(%s) > current_max(%s)",
                    category_id,
                    prefetched_new_bounds.get("min_str"),
                    old_bounds.get("max_str"),
                )
                _p(
                    28,
                    f"{cat_label}: appending newer rows "
                    f"(after {old_bounds.get('max_str')})…",
                )
                history_prefix = f"{paths['history']}previous/"
                old_bytes: Optional[bytes] = None
                for b in current:
                    dest = f"{history_prefix}{Path(b.name).name}"
                    result["backed_up"].append({"from": b.name, "to": dest})
                    if not dry_run:
                        try:
                            _backup_blob(cc, b.name, dest)
                        except Exception as be:
                            log.warning("backup skipped %s: %s", b.name, be)

                _p(40, f"{cat_label}: downloading current master…")
                old_bytes = _download(cc, current_blob.name)
                _p(
                    48,
                    f"{cat_label}: converting master → CSV + appending "
                    f"(avoids Excel hang)…",
                )
                new_df, new_meta = read_excel_smart(
                    prefetched_new_bytes,
                    Path(primary.name).name,
                    date_candidates=date_candidates,
                    preferred_header=header_row,
                    preferred_sheet=preferred_sheet,
                    column_hints=column_hints,
                )
                new_df = _normalize_category_df(category_id, new_df)
                out_bytes, ctype, st = append_rows_to_tabular_bytes(
                    old_bytes,
                    Path(current_blob.name).name,
                    new_df,
                    date_candidates=date_candidates,
                    old_max=old_max,
                    preferred_header=header_row,
                    preferred_sheet=preferred_sheet,
                    column_hints=column_hints,
                )
                appended = int(st.get("appended_rows") or 0)
                if appended <= 0:
                    result["skipped"] = True
                    result["reason"] = "no_newer_rows_after_cut — kept existing current"
                    result["written"] = current_blob.name
                    result["stats"] = {
                        "mode": "binary_append_skip",
                        "reason": result["reason"],
                        "step": st,
                        "read_meta": new_meta,
                    }
                    if not keep_incoming and not dry_run:
                        for b in incoming:
                            _delete(cc, b.name)
                            result["cleared_incoming"].append(b.name)
                    return result

                out_blob = f"{paths['current']}{canonical}"
                final_bounds = {
                    "min": old_bounds.get("min"),
                    "max": prefetched_new_bounds.get("max"),
                    "min_str": old_bounds.get("min_str"),
                    "max_str": prefetched_new_bounds.get("max_str"),
                    "date_column": old_bounds.get("date_column")
                    or prefetched_new_bounds.get("date_column"),
                    "total_rows": int(old_bounds.get("total_rows") or 0) + appended,
                }
                st["incoming_file"] = primary.name
                st["read_meta"] = new_meta
                st["old_rows_before"] = int(old_bounds.get("total_rows") or 0)
                st["merged_rows"] = final_bounds["total_rows"]
                result["written"] = out_blob
                result["stats"] = {
                    "mode": "binary_append",
                    "bytes": len(out_bytes),
                    "appended_rows": appended,
                    "final_rows": final_bounds["total_rows"],
                    "current_dates": {
                        "min": old_bounds.get("min_str"),
                        "max": old_bounds.get("max_str"),
                    },
                    "incoming_dates": {
                        "min": prefetched_new_bounds.get("min_str"),
                        "max": prefetched_new_bounds.get("max_str"),
                    },
                    "step": st,
                }
                if not dry_run:
                    _p(
                        72,
                        f"{cat_label}: writing master "
                        f"(+{appended:,} rows → {final_bounds['total_rows']:,})…",
                    )
                    _upload(cc, out_blob, out_bytes, ctype)
                    _p(85, f"{cat_label}: saving date snapshot…")
                    save_bounds = _enrich_bounds_coverage(
                        final_bounds,
                        prior_meta=old_bounds,
                        incoming_file_dates=incoming_dates,
                    )
                    _save_date_bounds_meta(
                        cc,
                        paths["current"],
                        canonical,
                        save_bounds,
                        dry_run=False,
                    )
                    for b in current:
                        # Keep sidecar meta (and delta helper files) — otherwise
                        # preview/UI has to re-scan the full master again.
                        if (
                            b.name != out_blob
                            and not b.name.endswith(".date_bounds.json")
                            and not b.name.endswith(".delta.csv")
                        ):
                            _delete(cc, b.name)
                    if not keep_incoming:
                        for b in incoming:
                            _delete(cc, b.name)
                            result["cleared_incoming"].append(b.name)
                _p(88, f"{cat_label}: append complete")
                return result
        except Exception as gate_exc:
            log.info(
                "Date gate fallback for %s (will use full merge): %s",
                category_id,
                gate_exc,
            )
            prefetched_new_bytes = None
            prefetched_new_bounds = None
            prefetched_old_bounds = None

    # Backup current → history/previous/ (only when we will actually write)
    history_prefix = f"{paths['history']}previous/"
    backed_names: set[str] = set()
    for b in list(current):
        dest = f"{history_prefix}{Path(b.name).name}"
        if not dry_run:
            try:
                if not _blob_exists(cc, b.name):
                    result["warnings"].append(f"current blob missing, skipped: {b.name}")
                    current = [x for x in current if x.name != b.name]
                    if current_blob is not None and current_blob.name == b.name:
                        current_blob = current[0] if current else None
                    continue
                _backup_blob(cc, b.name, dest)
                backed_names.add(Path(b.name).name)
                result["backed_up"].append({"from": b.name, "to": dest})
            except Exception as e:
                backup_msg = f"backup {b.name}: {e}"
                if mode != "date_merge":
                    log.warning(backup_msg)
                    result["warnings"].append(backup_msg)
                else:
                    result["errors"].append(backup_msg)
                    result["ok"] = False
                    return result
        else:
            result["backed_up"].append({"from": b.name, "to": dest})
            backed_names.add(Path(b.name).name)

    # Prune other leftovers in previous/ (keep the backup we just wrote)
    if not dry_run and backed_names:
        for old in cc.list_blobs(name_starts_with=history_prefix):
            if Path(old.name).name in (".keep", ".gitkeep"):
                continue
            if Path(old.name).name not in backed_names:
                _delete(cc, old.name)

    # Prune any leftover old timestamp folders (from earlier versions)
    if not dry_run:
        try:
            prune_history(folder)
        except Exception as pe:
            log.warning("history prune failed: %s", pe)

    try:
        if mode == "calendar_merge":
            from pipeline.financial_calendar import read_calendar, merge_calendar
            merged = read_calendar(_download(cc, current_blob.name), Path(current_blob.name).name) if current_blob else None
            for blob in sorted(incoming, key=lambda b: (str(getattr(b, "last_modified", "")), b.name)):
                new = read_calendar(_download(cc, blob.name), Path(blob.name).name)
                merged = merge_calendar(merged, new)
            out_blob = f"{paths['current']}{canonical}"
            data = merged.to_csv(index=False, date_format="%Y-%m-%d").encode("utf-8")
            if not dry_run:
                _upload(cc, out_blob, data, "text/csv")
            result["written"] = out_blob
            result["stats"] = {"mode": mode, "unique_dates": len(merged)}
        elif mode == "date_merge":
            from date_merge import (
                compare_columns,
                dataframe_date_bounds,
                is_file_date_covered,
                merge_append_newer_only,
                merge_by_date_overlap,
                merge_fill_dates,
                merge_overlap_last_date,
                read_excel_smart,
                should_skip_merge_by_dates,
                write_tabular_bytes,
            )

            date_candidates = cfg.get("date_candidates") or ["Date"]
            cat_label = folder
            from header_detect import merge_column_hints, resolve_category_layout

            column_hints = merge_column_hints(cfg)
            header_row = int(cfg.get("header_row", 0))
            preferred_sheet = cfg.get("sheet_name")
            layout_bytes: Optional[bytes] = prefetched_new_bytes
            if layout_bytes is None and incoming:
                layout_bytes = _download(cc, incoming[0].name)
            if layout_bytes is not None and incoming:
                layout = resolve_category_layout(
                    cfg, layout_bytes, Path(incoming[0].name).name
                )
                header_row = int(layout["header_row"])
                preferred_sheet = layout.get("sheet") or preferred_sheet
                column_hints = layout.get("column_hints") or column_hints

            # Date skip / full-replace already handled in DATE GATE above.
            # Continue with append / overlap / gap-fill merge using prefetched bounds when present.

            # FAST PATH: first file into empty current → copy bytes as-is (no pandas rewrite)
            if current_blob is None and len(incoming) == 1:
                primary = incoming[0]
                _p(20, f"{cat_label}: first upload — promoting to current…")
                data = _download(cc, primary.name)
                # Light header-only peek (nrows) — do not re-parse full workbook
                try:
                    import io as _io

                    import pandas as _pd

                    lower = Path(primary.name).name.lower()
                    if lower.endswith(".csv") and data[:2] != b"PK":
                        from date_merge import read_csv_bytes as _read_csv_bytes

                        peek_df = _read_csv_bytes(data, nrows=5)
                    else:
                        kwargs: Dict[str, Any] = {"engine": "openpyxl", "nrows": 5}
                        if preferred_sheet is not None:
                            kwargs["sheet_name"] = preferred_sheet
                        if header_row is not None:
                            kwargs["header"] = header_row
                        peek_df = _pd.read_excel(_io.BytesIO(data), **kwargs)
                    peek_df.columns = [str(c).strip() for c in peek_df.columns]
                    result["warnings"].extend(
                        compare_columns(
                            None,
                            peek_df,
                            date_candidates=date_candidates,
                            category_label=cat_label,
                        )
                    )
                except Exception as pe:
                    msg = str(pe)
                    if "date column" in msg.lower():
                        result["warnings"].append(
                            f"{cat_label}: Check the column name — date column not found. ({msg})"
                        )
                    else:
                        log.info("promote column peek skipped %s: %s", category_id, pe)
                out_blob = f"{paths['current']}{canonical}"
                out_bytes, ctype = _bytes_for_canonical(
                    data,
                    Path(primary.name).name,
                    canonical,
                    date_candidates=date_candidates,
                    preferred_header=header_row,
                    preferred_sheet=preferred_sheet,
                    column_hints=column_hints,
                    category_id=category_id,
                )
                result["written"] = out_blob
                result["stats"] = {
                    "mode": "promote_normalized",
                    "from": primary.name,
                    "bytes": len(out_bytes),
                    "note": "first current — CSV/Excel normalized to canonical",
                }
                if not dry_run:
                    _upload(cc, out_blob, out_bytes, ctype)
                    try:
                        from date_merge import excel_date_bounds_fast

                        promo_bounds = excel_date_bounds_fast(
                            out_bytes,
                            canonical,
                            date_candidates=date_candidates,
                            preferred_header=0,
                            preferred_sheet=None,
                            column_hints=column_hints,
                        )
                        if promo_bounds.get("max_str"):
                            promo_bounds["assumed_covered_through"] = str(
                                promo_bounds["max_str"]
                            )[:10]
                            promo_bounds["merged_file_dates"] = [
                                str(d)
                                for d in _incoming_file_dates_for_coverage(
                                    Path(primary.name).name, promo_bounds
                                )
                            ]
                        _save_date_bounds_meta(
                            cc, paths["current"], canonical, promo_bounds, dry_run=False
                        )
                    except Exception as me:
                        log.info("promote date meta skipped: %s", me)
                    for b in current:
                        # Keep sidecar meta (and delta helper files) — otherwise
                        # preview/UI has to re-scan the full master again.
                        if (
                            b.name != out_blob
                            and not b.name.endswith(".date_bounds.json")
                            and not b.name.endswith(".delta.csv")
                        ):
                            _delete(cc, b.name)
                    if not keep_incoming:
                        for b in incoming:
                            _delete(cc, b.name)
                            result["cleared_incoming"].append(b.name)
                return result

            old_df = None
            old_bounds: Optional[Dict[str, Any]] = (
                prefetched_old_bounds if prefetched_old_bounds is not None else None
            )
            if current_blob is not None:
                _p(30, f"{folder}: reading current master (full merge)…")
                old_bytes = _download(cc, current_blob.name)
                old_df, old_meta = read_excel_smart(
                    old_bytes,
                    Path(current_blob.name).name,
                    date_candidates=date_candidates,
                    preferred_header=header_row,
                    preferred_sheet=preferred_sheet,
                    column_hints=column_hints,
                )
                old_df = _normalize_category_df(category_id, old_df)
                if old_bounds is None:
                    old_bounds = dataframe_date_bounds(old_df, date_candidates)
                old_bounds = _ensure_coverage_meta(category_id, old_bounds)
                log.info(
                    "Read current %s with %s | dates %s..%s rows=%s",
                    current_blob.name,
                    old_meta,
                    old_bounds.get("min_str"),
                    old_bounds.get("max_str"),
                    old_bounds.get("total_rows"),
                )

            all_stats: List[Dict[str, Any]] = []
            merged_df = old_df
            any_merged = False
            coverage_prior = dict(old_bounds or {})
            merged_file_dates_acc: List[Any] = list(
                (old_bounds or {}).get("merged_file_dates") or []
            )

            def _bounds_public(bounds: Dict[str, Any]) -> Dict[str, Any]:
                return {
                    k: bounds.get(k)
                    for k in ("min_str", "max_str", "total_rows", "date_column")
                }

            for idx, b in enumerate(sorted(incoming, key=lambda x: x.name)):
                # Reuse already-downloaded incoming bytes from DATE GATE when possible
                if (
                    idx == 0
                    and len(incoming) == 1
                    and prefetched_new_bytes is not None
                ):
                    new_bytes = prefetched_new_bytes
                else:
                    new_bytes = _download(cc, b.name)
                new_df, new_meta = read_excel_smart(
                    new_bytes,
                    Path(b.name).name,
                    date_candidates=date_candidates,
                    preferred_header=header_row,
                    preferred_sheet=preferred_sheet,
                    column_hints=column_hints,
                )
                new_df = _normalize_category_df(category_id, new_df)
                new_bounds = (
                    prefetched_new_bounds
                    if (
                        idx == 0
                        and len(incoming) == 1
                        and prefetched_new_bounds is not None
                    )
                    else dataframe_date_bounds(new_df, date_candidates)
                )
                log.info(
                    "Read incoming %s with %s | dates %s..%s rows=%s",
                    b.name,
                    new_meta,
                    new_bounds.get("min_str"),
                    new_bounds.get("max_str"),
                    new_bounds.get("total_rows"),
                )

                col_warns = compare_columns(
                    merged_df,
                    new_df,
                    date_candidates=date_candidates,
                    category_label=cat_label,
                )
                result["warnings"].extend(col_warns)

                incoming_dates = _incoming_file_dates_for_coverage(
                    Path(b.name).name, new_bounds
                )
                # Named for a later day, but invoices only cover a day already
                # in current (e.g. 27 Sep file with 10 rows of 26 Sep). Do not
                # replace that whole day with the smaller extract.
                if (
                    incoming_dates
                    and new_bounds.get("max") is not None
                    and old_bounds
                    and old_bounds.get("max") is not None
                ):
                    try:
                        fd0 = incoming_dates[0]
                        fd_s = fd0.isoformat() if hasattr(fd0, "isoformat") else str(fd0)[:10]
                        cmax_s = str(new_bounds.get("max_str") or new_bounds.get("max"))[:10]
                        omax_s = str(old_bounds.get("max_str") or old_bounds.get("max"))[:10]
                        if fd_s > cmax_s and cmax_s <= omax_s:
                            log.info(
                                "Skip partial next-day file %s — invoices through %s, name %s, current %s",
                                b.name,
                                cmax_s,
                                fd_s,
                                omax_s,
                            )
                            all_stats.append(
                                {
                                    "mode": "skip_partial_next_day",
                                    "incoming_file": b.name,
                                    "reason": (
                                        f"filename {fd_s} but invoices only through {cmax_s} "
                                        "already in current"
                                    ),
                                }
                            )
                            continue
                    except Exception:
                        pass
                allow_gap_fill = False
                if old_bounds is not None and incoming_dates:
                    allow_gap_fill = not is_file_date_covered(
                        incoming_dates[0], old_bounds
                    )
                elif (
                    old_bounds is not None
                    and new_bounds.get("max") is not None
                    and old_bounds.get("max") is not None
                    and new_bounds["max"] < old_bounds["max"]
                ):
                    allow_gap_fill = True

                if old_bounds is not None:
                    skip, reason = should_skip_merge_by_dates(
                        old_bounds, new_bounds, allow_gap_fill=allow_gap_fill
                    )
                    if skip:
                        log.info("Skip merge for %s — %s", b.name, reason)
                        all_stats.append(
                            {
                                "mode": "skip_by_date",
                                "incoming_file": b.name,
                                "reason": reason,
                                "current_dates": _bounds_public(old_bounds),
                                "incoming_dates": _bounds_public(new_bounds),
                                "read_meta": new_meta,
                                "column_warnings": col_warns,
                            }
                        )
                        continue

                # Strictly newer days → append only (no full old date scan / overlap drop)
                old_max = old_bounds.get("max") if old_bounds else None
                new_min = new_bounds.get("min")
                new_max = new_bounds.get("max")
                if (
                    old_max is not None
                    and new_max is not None
                    and new_max < old_max
                    and allow_gap_fill
                    and merged_df is not None
                ):
                    log.info(
                        "Gap-fill merge %s — incoming_max(%s) < current_max(%s)",
                        b.name,
                        new_bounds.get("max_str"),
                        old_bounds.get("max_str") if old_bounds else None,
                    )
                    merged_df, st = merge_fill_dates(
                        merged_df,
                        new_df,
                        date_candidates=date_candidates,
                    )
                elif (
                    old_max is not None
                    and new_min is not None
                    and new_min > old_max
                    and merged_df is not None
                ):
                    log.info(
                        "Append-only merge %s — incoming_min(%s) > current_max(%s)",
                        b.name,
                        new_bounds.get("min_str"),
                        old_bounds.get("max_str"),
                    )
                    merged_df, st = merge_append_newer_only(
                        merged_df,
                        new_df,
                        date_candidates=date_candidates,
                        old_max=old_max,
                    )
                elif (
                    old_max is not None
                    and new_max is not None
                    and new_max == old_max
                    and merged_df is not None
                ):
                    # Same last date → replace only that day (missed txs)
                    log.info(
                        "Last-date overlap %s — replace current_max=%s only",
                        b.name,
                        old_bounds.get("max_str"),
                    )
                    merged_df, st = merge_overlap_last_date(
                        merged_df,
                        new_df,
                        date_candidates=date_candidates,
                        last_date=old_max,
                    )
                else:
                    merged_df, st = merge_by_date_overlap(
                        merged_df,
                        new_df,
                        date_candidates=date_candidates,
                    )
                st["incoming_file"] = b.name
                st["read_meta"] = new_meta
                st["incoming_dates"] = _bounds_public(new_bounds)
                if col_warns:
                    st["column_warnings"] = col_warns
                all_stats.append(st)
                any_merged = True
                merged_file_dates_acc.extend(incoming_dates)
                # Next incoming compares against updated merged range (+ coverage)
                old_bounds = dataframe_date_bounds(merged_df, date_candidates)
                old_bounds = _enrich_bounds_coverage(
                    old_bounds,
                    prior_meta=coverage_prior,
                    incoming_file_dates=merged_file_dates_acc,
                )
                coverage_prior = dict(old_bounds)

            if not any_merged:
                result["skipped"] = True
                result["reason"] = "no_newer_dates — kept existing current"
                result["written"] = current_blob.name if current_blob else None
                result["stats"] = {"mode": "skip_by_date", "steps": all_stats}
                if not keep_incoming and not dry_run:
                    for b in incoming:
                        _delete(cc, b.name)
                        result["cleared_incoming"].append(b.name)
                return result

            assert merged_df is not None
            out_bytes, ctype = write_tabular_bytes(merged_df, canonical)
            out_blob = f"{paths['current']}{canonical}"
            result["stats"] = {"steps": all_stats, "final_rows": int(len(merged_df))}
            result["written"] = out_blob
            if not dry_run:
                _upload(cc, out_blob, out_bytes, ctype)
                final_bounds = old_bounds or dataframe_date_bounds(
                    merged_df, date_candidates
                )
                final_bounds = _enrich_bounds_coverage(
                    final_bounds,
                    prior_meta=coverage_prior,
                    incoming_file_dates=merged_file_dates_acc,
                )
                _save_date_bounds_meta(
                    cc, paths["current"], canonical, final_bounds, dry_run=False
                )
                for b in current:
                    # Keep sidecar meta (and delta helper files) — otherwise
                    # preview/UI has to re-scan the full master again.
                    if (
                        b.name != out_blob
                        and not b.name.endswith(".date_bounds.json")
                        and not b.name.endswith(".delta.csv")
                    ):
                        _delete(cc, b.name)

        else:
            # replace: last incoming wins — promote bytes (no full parse of old master)
            primary = sorted(incoming, key=lambda x: x.name)[-1]
            data = _download(cc, primary.name)
            in_name = Path(primary.name).name

            # Always normalize CSV/Excel → configured canonical (usually .xlsx).
            # Same extension → bytes pass-through (no pandas rewrite).
            out_bytes, ctype = _bytes_for_canonical(
                data,
                in_name,
                canonical,
                preferred_header=int(cfg.get("header_row", 0) or 0),
                preferred_sheet=cfg.get("sheet_name"),
                column_hints=list(cfg.get("column_hints") or []),
                date_candidates=list(cfg.get("date_candidates") or ["Date"]),
                category_id=category_id,
            )
            out_blob = f"{paths['current']}{canonical}"
            result["written"] = out_blob
            result["stats"] = {
                "mode": "replace",
                "from": primary.name,
                "bytes": len(out_bytes),
                "passthrough": _same_tabular_ext(in_name, canonical),
            }
            if not dry_run:
                for b in current:
                    if b.name == out_blob or b.name.endswith(canonical):
                        dest_prev = f"{paths['history']}previous/{Path(b.name).name}"
                        try:
                            _backup_blob(cc, b.name, dest_prev)
                            log.info("Backed up previous current %s -> %s", b.name, dest_prev)
                        except Exception as be:
                            log.warning("backup of previous current failed %s: %s", b.name, be)

                _upload(cc, out_blob, out_bytes, ctype)
                for b in current:
                    # Keep sidecar meta (and delta helper files) — otherwise
                    # preview/UI has to re-scan the full master again.
                    if (
                        b.name != out_blob
                        and not b.name.endswith(".date_bounds.json")
                        and not b.name.endswith(".delta.csv")
                    ):
                        _delete(cc, b.name)
                # Snapshot JSON for categories with a date column (SOH-Master, etc.)
                if cfg.get("date_candidates"):
                    snap = _compute_and_save_date_bounds(
                        cc,
                        current_prefix=paths["current"],
                        canonical=canonical,
                        data=out_bytes,
                        cfg=cfg,
                        category_id=category_id,
                        incoming_filename=in_name,
                        dry_run=False,
                    )
                    if snap:
                        result["stats"]["date_bounds"] = {
                            "min": snap.get("min_str"),
                            "max": snap.get("max_str"),
                            "rows": snap.get("total_rows"),
                        }
                    else:
                        result["warnings"].append(
                            f"{folder}: merged file written but date_bounds.json snapshot failed"
                        )
                if category_id == "warehouse-soh":
                    try:
                        from warehouse_ledger import reset_ledger

                        reset_ledger()
                        result["warnings"].append(
                            "WH remaining ledger reset (new warehouse closing file uploaded)"
                        )
                    except Exception as le:
                        log.warning("warehouse ledger reset failed: %s", le)
                # Per-store SOH: also archive NEW current to dated history (run_day - 1)
                if category_id in _store_soh_ids():
                    try:
                        from store_soh_history import publish_store_soh_dated_history

                        hist_info = publish_store_soh_dated_history(
                            cc,
                            history_prefix=paths["history"],
                            canonical_name=canonical,
                            data=out_bytes,
                            content_type=ctype,
                            upload_fn=_upload,
                        )
                        result["dated_history"] = hist_info
                    except Exception as he:
                        log.warning("dated SOH history failed %s: %s", category_id, he)
                        result["warnings"].append(f"dated history: {he}")

        # Safety net: master written but snapshot JSON missing → create it now
        if (
            not dry_run
            and result.get("ok", True)
            and result.get("written")
            and not result.get("skipped")
            and cfg.get("date_candidates")
        ):
            meta_now = _load_date_bounds_meta(cc, paths["current"], canonical)
            if not meta_now or not meta_now.get("max_str"):
                try:
                    written_blob = str(result["written"])
                    data_for_meta = _download(cc, written_blob)
                    snap = _compute_and_save_date_bounds(
                        cc,
                        current_prefix=paths["current"],
                        canonical=canonical,
                        data=data_for_meta,
                        cfg=cfg,
                        category_id=category_id,
                        prior_meta=prefetched_old_bounds,
                        incoming_filename=(
                            Path(incoming[0].name).name if incoming else None
                        ),
                        dry_run=False,
                    )
                    if snap:
                        result.setdefault("stats", {})["date_bounds_repaired"] = {
                            "min": snap.get("min_str"),
                            "max": snap.get("max_str"),
                        }
                        log.info(
                            "Repaired missing date_bounds.json for %s → %s..%s",
                            category_id,
                            snap.get("min_str"),
                            snap.get("max_str"),
                        )
                    else:
                        result["warnings"].append(
                            f"{folder}: file merged but date_bounds.json still missing"
                        )
                except Exception as repair_exc:
                    log.warning(
                        "date_bounds repair failed %s: %s", category_id, repair_exc
                    )
                    result["warnings"].append(
                        f"{folder}: date_bounds.json repair failed ({repair_exc})"
                    )

        # Clear incoming
        if not keep_incoming and not dry_run:
            for b in incoming:
                _delete(cc, b.name)
                result["cleared_incoming"].append(b.name)

    except Exception as e:
        log.exception("merge failed %s", category_id)
        result["ok"] = False
        msg = str(e)
        if isinstance(e, ModuleNotFoundError) or "No module named" in msg:
            friendly = f"{folder}: merge failed — backend module missing. ({msg})"
        elif "date column" in msg.lower():
            friendly = f"{folder}: Check the column name — merge failed. ({msg})"
        else:
            friendly = msg
        result["errors"].append(friendly)
        if "date column" in msg.lower():
            result["warnings"].append(friendly)

    return result


def merge_all_categories(
    *,
    dry_run: bool = False,
    keep_incoming: bool = False,
    only_ids: Optional[List[str]] = None,
    on_progress: Optional[Callable[[int, str], None]] = None,
) -> Dict[str, Any]:
    """
    Merge every configured category that has incoming files.
    Categories with empty incoming are skipped (current preserved).
    """
    _load_dotenv()
    ids = only_ids or list(CATEGORY_MERGE_CONFIG.keys())
    ids = [cid for cid in ids if cid in CATEGORY_MERGE_CONFIG]
    results = []
    n = max(len(ids), 1)
    for i, cid in enumerate(ids):
        label = CATEGORY_MERGE_CONFIG[cid].get("folder") or cid
        base = int(8 + (70 * i) / n)
        if on_progress:
            try:
                on_progress(base, f"Merging {label} ({i + 1}/{len(ids)})…")
            except Exception:
                pass

        def _child(pct: int, msg: str, _base: int = base, _i: int = i) -> None:
            if not on_progress:
                return
            # Map category-local 0–100 into this category's slice of overall bar
            span = max(1, int(70 / n))
            mapped = min(88, _base + int((max(0, min(100, pct)) / 100) * span))
            try:
                on_progress(mapped, msg)
            except Exception:
                pass

        results.append(
            merge_one_category(
                cid,
                dry_run=dry_run,
                keep_incoming=keep_incoming,
                on_progress=_child if on_progress else None,
            )
        )

    merged = [r for r in results if not r.get("skipped") and r.get("ok")]
    skipped = [r for r in results if r.get("skipped")]
    failed = [r for r in results if not r.get("ok") and not r.get("skipped")]
    warnings: List[str] = []
    for r in results:
        for w in r.get("warnings") or []:
            if w and w not in warnings:
                warnings.append(str(w))

    return {
        "ok": len(failed) == 0,
        "dry_run": dry_run,
        "merged_count": len(merged),
        "skipped_count": len(skipped),
        "failed_count": len(failed),
        "merged": merged,
        "skipped": skipped,
        "failed": failed,
        "warnings": warnings,
        "hint": (
            "Only categories with files in incoming/ are merged. "
            "Others keep existing current/ unchanged."
        ),
    }


def sync_current_to_local(
    *,
    dry_run: bool = False,
    only_ids: Optional[List[str]] = None,
    skip_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Download input/<Category>/current/* → pipeline/input_files/<filename>
    so combine_* scripts can read them.
    Pass only_ids to sync just the categories that changed (much faster).
    Pass skip_ids to leave a category on blob (replenishment skips Sales-Master;
    the 9 AM daily run still syncs it for the sales dashboard).
    """
    _load_dotenv()
    from pipeline_inputs import INPUT_DIR

    cc, container_name, input_prefix = _get_container()
    logging.getLogger("azure").setLevel(logging.WARNING)

    downloaded: List[str] = []
    skipped: List[str] = []
    errors: List[str] = []
    if not dry_run:
        INPUT_DIR.mkdir(parents=True, exist_ok=True)

    skip = {str(s).strip() for s in (skip_ids or []) if str(s).strip()}
    pairs = list(CATEGORY_MERGE_CONFIG.items())
    if only_ids:
        want = set(only_ids)
        pairs = [(cid, cfg) for cid, cfg in pairs if cid in want]
    if skip:
        skipped_cats = [cid for cid, _cfg in pairs if cid in skip]
        pairs = [(cid, cfg) for cid, cfg in pairs if cid not in skip]
        for cid in skipped_cats:
            log.info("Skip local sync for category %s (not used by this run)", cid)
    else:
        skipped_cats = []

    for _cid, cfg in pairs:
        folder = cfg["folder"]
        current_prefix = f"{input_prefix}{folder}/current/"
        for b in _list_files(cc, current_prefix):
            # Skip delta helpers — keep masters + date_bounds.json snapshots
            name = Path(b.name).name.lower()
            if name.endswith(".delta.csv"):
                continue
            dest = INPUT_DIR / Path(b.name).name
            try:
                if dry_run:
                    downloaded.append(f"{b.name} → {dest}")
                    continue
                # Skip unchanged locals (size match) — avoids re-downloading 30–90MB masters
                try:
                    props = cc.get_blob_client(b.name).get_blob_properties()
                    remote_size = int(getattr(props, "size", 0) or 0)
                except Exception:
                    remote_size = 0
                if (
                    remote_size > 0
                    and dest.exists()
                    and dest.is_file()
                    and dest.stat().st_size == remote_size
                ):
                    skipped.append(str(dest))
                    log.info("Skip sync (size match) %s", dest.name)
                    continue
                data = _download(cc, b.name)
                dest.write_bytes(data)
                downloaded.append(str(dest))
                log.info("Synced %s → %s", b.name, dest)
                # Also mirror under <Category>/current/ so Dispatch Timing / Breakeven
                # (which prefer */current/ by mtime) cannot keep serving a stale copy.
                try:
                    current_dest = INPUT_DIR / folder / "current" / Path(b.name).name
                    current_dest.parent.mkdir(parents=True, exist_ok=True)
                    if (
                        not current_dest.exists()
                        or current_dest.stat().st_size != len(data)
                        or current_dest.stat().st_mtime < dest.stat().st_mtime
                    ):
                        current_dest.write_bytes(data)
                        log.info("Synced mirror %s → %s", b.name, current_dest)
                except Exception as mirror_exc:
                    log.warning("current/ mirror failed for %s: %s", b.name, mirror_exc)
                if dest.name.upper() == "WAREHOUSE_SOH.CSV":
                    try:
                        from pipeline_inputs import mirror_warehouse_csv_to_xlsx

                        mirror_warehouse_csv_to_xlsx()
                        log.info("Mirrored WAREHOUSE_SOH.csv → WAREHOUSE_SOH.xlsx")
                    except Exception as me:
                        log.warning("WH csv→xlsx mirror failed: %s", me)
            except Exception as e:
                errors.append(f"{b.name}: {e}")

    # Even when sync skipped (size match), refresh xlsx if csv is newer than xlsx
    try:
        from pipeline_inputs import WAREHOUSE_FILE, WAREHOUSE_FILE_CSV, mirror_warehouse_csv_to_xlsx

        if WAREHOUSE_FILE_CSV.exists() and (
            not WAREHOUSE_FILE.exists()
            or WAREHOUSE_FILE_CSV.stat().st_mtime > WAREHOUSE_FILE.stat().st_mtime
        ):
            mirror_warehouse_csv_to_xlsx()
            log.info("Refreshed WAREHOUSE_SOH.xlsx from local CSV (post-sync)")
    except Exception as me:
        log.warning("WH post-sync xlsx refresh failed: %s", me)

    return {
        "ok": len(errors) == 0,
        "container": container_name,
        "local_input_dir": str(INPUT_DIR),
        "downloaded": downloaded,
        "skipped": skipped,
        "skipped_categories": skipped_cats,
        "errors": errors,
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    import argparse
    import json

    p = argparse.ArgumentParser(description="Merge Blob category incoming → current")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--keep-incoming", action="store_true")
    p.add_argument("--sync-local", action="store_true", help="Also sync current/ → local input_files/")
    p.add_argument("--category", action="append", help="Only these category ids")
    p.add_argument(
        "--backfill-date-bounds",
        action="store_true",
        help="Create missing *.date_bounds.json sidecars for current/ masters",
    )
    args = p.parse_args()
    if args.backfill_date_bounds:
        result = backfill_missing_date_bounds(
            only_ids=args.category,
            dry_run=args.dry_run,
        )
        print(json.dumps(result, indent=2, default=str))
        return
    result = merge_all_categories(
        dry_run=args.dry_run,
        keep_incoming=args.keep_incoming,
        only_ids=args.category,
    )
    if args.sync_local and not args.dry_run:
        result["local_sync"] = sync_current_to_local(dry_run=False)
    print(json.dumps(result, indent=2, default=str))
    raise SystemExit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()

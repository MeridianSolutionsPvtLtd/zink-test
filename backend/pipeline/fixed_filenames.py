"""
Fixed Blob / local filenames per upload category.

Client may upload any original name — backend always renames to these.
CSV uploads keep .csv so merge does not try to open them as Excel zip.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

# category_id → fixed filename stored in incoming/ and current/
FIXED_FILENAMES: Dict[str, str] = {
    "ebo-sales": "EBO_SALES.xlsx",
    "sales-pt-ssl-centro-iconic": "SSI_PT_CENTRO_ICONIC_SALES.xlsx",
    # Sales-Master (PT, SSL, Centro, Iconic, EBO) — long-lived master for dashboards
    "sales-master-pt-ssl-centro-iconic": "SSI_PT_CENTRO_ICONIC_EBO_SALES_MASTER.xlsx",
    "ebo-soh": "EBO_SOH.xlsx",
    "centro-soh": "CENTRO_SOH.xlsx",
    "iconic-soh": "ICONIC_SOH.xlsx",
    "pantaloons-soh": "PANTALOONS_SOH.xlsx",
    "shoppers-stop-soh": "SHOPPERS_STOP_SOH.xlsx",
    "warehouse-soh": "WAREHOUSE_SOH.xlsx",
    "transfer-ebo": "EBO_INVOICE_TRACKING.xlsx",
    "transfer-pt-ssl-centro-iconic": "SALES_INVOICE_TRACKING.xlsx",
    "transfer-master-pt-ssl-centro-iconic": "SALES_INVOICE_TRACKING_MASTER.xlsx",
    "customer-master": "CUSTOMER_MASTER.xlsx",
    "item-master": "ITEM_MASTER.xlsx",
    "store-grade-list": "STORE_GRADE.xlsx",
    "top-sellers-list": "TOP_SELLERS.xlsx",
    "sku-remap": "SKU_REMAP.xlsx",
    "combined-soh": "COMBINED_SOH.xlsx",
    "soh-master": "COMBINED_SOH_MASTER.csv",
    "target-lfs": "TARGET_LFS.xlsx",
    "rtv-data": "RTV_DATA.csv",
    "ibt-data": "IBT_DATA.xlsx",
    "grc-report": "GRC_REPORT.csv",
    # legacy ids
    "pantaloons-sales": "SSI_PT_CENTRO_ICONIC_SALES.xlsx",
    "shoppers-stop-sales": "SSI_PT_CENTRO_ICONIC_SALES.xlsx",
    "iconic-sales": "SSI_PT_CENTRO_ICONIC_SALES.xlsx",
    "centro-sales": "SSI_PT_CENTRO_ICONIC_SALES.xlsx",
    "sales-pt-ebo-centro-iconic": "SSI_PT_CENTRO_ICONIC_SALES.xlsx",
}


def looks_like_csv(data: bytes) -> bool:
    """True when bytes are text/CSV, not an Office zip (.xlsx)."""
    if not data:
        return False
    if data[:2] == b"PK":  # zip / xlsx / xlsm
        return False
    sample = data[:4096].lstrip(b"\xef\xbb\xbf")
    try:
        text = sample.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = sample.decode("latin-1")
        except Exception:
            return False
    # Reject binary-looking samples
    if "\x00" in text:
        return False
    return ("," in text) or ("\t" in text)


def fixed_name_for(
    category_id: str,
    fallback: str | None = None,
    *,
    original_name: str | None = None,
    data: Optional[bytes] = None,
) -> str:
    name = FIXED_FILENAMES.get(category_id)
    if not name:
        if fallback:
            return fallback
        raise KeyError(f"No fixed filename for category_id={category_id!r}")

    want_csv = False
    if original_name and Path(original_name).suffix.lower() == ".csv":
        want_csv = True
    elif data is not None and looks_like_csv(data):
        want_csv = True

    if want_csv:
        return str(Path(name).with_suffix(".csv"))
    return name

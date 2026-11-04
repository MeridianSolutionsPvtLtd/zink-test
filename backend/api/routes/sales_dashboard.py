"""
Sales Dashboard API — line-level sales from Sales-Master
(PT, SSL, Centro, Iconic, EBO) under input/ (Azure blob or local),
with COMBINED_SALES only as legacy fallback.
"""
from __future__ import annotations

import colorsys
import hashlib
import json
import io
import logging
import os
import re
import threading
import tempfile
import time
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
import xlsxwriter


def _pil():
    """Lazy Pillow import — missing PIL must not break Sales/Home page_cache warm on Azure."""
    try:
        from PIL import Image, ImageDraw  # type: ignore

        return Image, ImageDraw
    except Exception:
        return None, None

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/sales-dashboard", tags=["Sales Dashboard"])

from api.season_utils import expand_season_yoy_chain, shift_season_csv

_TABLE = "COMBINED_SALES"
_CACHE_DF: pd.DataFrame | None = None
_CACHE_KEY: str | None = None
_CACHE_AT = 0.0
_CACHE_TTL = int(os.environ.get("SALES_DASHBOARD_CACHE_TTL_SECONDS", "600"))
_SALES_LOAD_LOCK = threading.Lock()
_API_CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "api_cache"

_SOH_MAP_CACHE: dict[str, Any] | None = None
_SOH_MAP_TIME: float = 0.0
_DASH_RESPONSE_CACHE: dict[str, tuple[float, dict]] = {}
_DASH_RESPONSE_TTL = 180


def _daily_movement_parquet_path() -> Path:
    return _API_CACHE_DIR / "daily_inventory_movement.parquet"


def _scope_is_unfiltered(scope: dict[str, Any] | None) -> bool:
    """True when the user asked for the whole network (no season/sku/store cut)."""
    if not scope:
        return True
    for key in (
        "season", "sku", "barcode", "barcodes", "departments", "sections",
        "channel", "zones", "site_id", "site_ids",
        "store_grades", "states", "cities",
    ):
        v = str(scope.get(key) or "").strip().lower()
        if v and v not in ("", "all"):
            return False
    return True


def _get_soh_map() -> dict[str, dict[Any, float]]:
    """Current SOH map — default SOH-Master via api.current_soh (CURRENT_SOH_SOURCE)."""
    global _SOH_MAP_CACHE, _SOH_MAP_TIME
    now = time.time()
    if _SOH_MAP_CACHE is not None and (now - _SOH_MAP_TIME < 1800):
        return _SOH_MAP_CACHE

    try:
        from api.current_soh import (
            current_soh_source,
            load_latest_closing_soh_pairs,
            site_current_soh_map,
            total_current_soh,
        )

        pairs = load_latest_closing_soh_pairs()
        if not pairs.empty:
            pq_path = _daily_movement_parquet_path()
            sku_by_pair: dict[tuple, str] = {}
            if pq_path.exists():
                try:
                    sk = pd.read_parquet(pq_path, columns=["store", "barcode", "sku", "date"])
                    sk["date"] = pd.to_datetime(sk["date"], errors="coerce")
                    sk = sk.dropna(subset=["date"])
                    sk["store"] = sk["store"].astype(str).str.strip().str.upper()
                    sk["barcode"] = (
                        sk["barcode"].astype(str).str.strip().str.upper().str.replace(r"\.0$", "", regex=True)
                    )
                    sk["sku"] = sk["sku"].astype(str).str.strip().str.upper().replace({"NAN": "", "NONE": ""})
                    idx = sk.groupby(["store", "barcode"], sort=False)["date"].transform("max") == sk["date"]
                    for s, bc, sku in sk.loc[idx, ["store", "barcode", "sku"]].itertuples(index=False, name=None):
                        if sku:
                            sku_by_pair[(s, bc)] = sku
                except Exception:
                    pass

            site_barcode: dict[tuple, float] = {}
            site_sku: dict[tuple, float] = {}
            sku_tot: dict[str, float] = {}
            for s, bc, val in pairs[["store", "barcode", "closing_soh"]].itertuples(index=False, name=None):
                raw = float(val)
                # Keep raw pair SOH (can be negative for recon), but SKU rollups use
                # positive stock only — negatives were zeroing top-SKU SOH / faking 100% STR.
                site_barcode[(s, bc)] = site_barcode.get((s, bc), 0.0) + raw
                sku = sku_by_pair.get((s, bc), "")
                if sku:
                    pos = raw if raw > 0 else 0.0
                    if pos:
                        site_sku[(s, sku)] = site_sku.get((s, sku), 0.0) + pos
                        sku_tot[sku] = sku_tot.get(sku, 0.0) + pos

            site_tot = dict(site_current_soh_map())
            total_soh = float(total_current_soh())
            src = current_soh_source()

            _SOH_MAP_CACHE = {
                "total_soh": total_soh,
                "site_barcode": site_barcode,
                "site_sku": site_sku,
                "sku": sku_tot,
                "site": site_tot,
                "source": src,
            }
            _SOH_MAP_TIME = now
            logger.info(
                "SOH map from %s: total_soh=%.0f stores=%d pairs=%d",
                src,
                total_soh,
                pairs["store"].nunique(),
                len(pairs),
            )
            return _SOH_MAP_CACHE
    except Exception as exc:
        logger.warning("Current SOH map build failed: %s", exc)

    # Fallback: meta.json total only (still daily movement — not COMBINED_SOH)
    try:
        meta_path = _API_CACHE_DIR / "daily_inventory_movement.meta.json"
        if meta_path.exists():
            import json as _json

            with open(meta_path, "r", encoding="utf-8") as f:
                meta = _json.load(f)
            total_soh = meta.get("closing_soh_net", 0.0)
            _SOH_MAP_CACHE = {
                "total_soh": float(total_soh),
                "site_barcode": {},
                "site_sku": {},
                "sku": {},
                "site": {},
                "source": "daily_inventory_movement.meta",
            }
            _SOH_MAP_TIME = now
            return _SOH_MAP_CACHE
    except Exception as exc2:
        logger.warning("SOH meta.json fallback failed: %s", exc2)

    return {"total_soh": 0.0, "site_barcode": {}, "site_sku": {}, "sku": {}, "site": {}, "source": "none"}


CHANNEL_OPTIONS = [
    {"id": "all", "label": "All channels"},
    {"id": "PT", "label": "Pantaloons (PT)"},
    {"id": "SSL", "label": "Shoppers Stop (SSL)"},
    {"id": "CENTRO", "label": "Centro"},
    {"id": "ICONIC", "label": "Iconic"},
    {"id": "EBO", "label": "EBO"},
]


def _val(x: Any, default: Any = "") -> Any:
    if hasattr(x, "default"):
        return default if x.default is ... else x.default
    return default if x is None else x


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Map COMBINED_SALES / Sales Master schema → API columns."""
    out = df.copy()
    out.columns = out.columns.astype(str).str.strip()

    rename = {
        "ENTRY_DATE": "date",
        "Date": "date",
        "DATE": "date",
        "BILL DATE": "date",
        "STORE CODE": "site_id",
        "Site_ID": "site_id",
        "SITE CODE": "site_id",
        "TRANSACTION_SITE": "site_name",
        "Site_Name": "site_name",
        "SITE NAME": "site_name",
        "BARCODE": "barcode",
        "Barcode": "barcode",
        "CATEGORY3": "sku",
        "SKU": "sku",
        "Cat3": "sku",
        "CATEGORY5": "size",
        "Size": "size",
        "Cat5": "size",
        "QUANTITY": "qty",
        "Qty_Sold": "qty",
        "QTY": "qty",
        "Qty": "qty",
        "RSP": "mrp",
        "MRP": "mrp",
        "NET_AMOUNT": "net_sale_amount",
        "Net_sales_amount": "net_sale_amount",
        "NET AMOUNT": "net_sale_amount",
        "Item Net Amt": "net_sale_amount",
        "ITEM NET AMT": "net_sale_amount",
        "ITEM DISCOUNT_AMOUNT": "discount_amount",
        "Discount_amount": "discount_amount",
        "Discount Amt": "discount_amount",
        "DISCOUNT AMT": "discount_amount",
        "Item Discount": "item_discount",
        "Bill Discount": "bill_discount",
        "Tax Charge Amt": "tax_amount",
        "TAX CHARGE AMT": "tax_amount",
        "Taxable Amt": "taxable_amount",
        "Gross Amt": "gross_amount",
        "GROSS AMT": "gross_amount",
        "Cost Rate": "cost_rate",
        "Cost Amt": "cost_amount",
        "CGST Rate": "cgst_rate",
        "SGST Rate": "sgst_rate",
        "IGST Rate": "igst_rate",
        "CGST Amt": "cgst_amt",
        "SGST Amt": "sgst_amt",
        "IGST Amt": "igst_amt",
        "Invoice Date": "date",
        "INVOICE DATE": "date",
        "Invoice No": "invoice_no",
        "INVOICE NO": "invoice_no",
        "Site Code": "site_id",
        "SITE CODE": "site_id",
        "Site": "site_name",
        "SITE": "site_name",
        "Site Type": "site_type",
        "SITE TYPE": "site_type",
        "Channel Name": "channel",
        "CHANNEL NAME": "channel",
        "CATEGORY6": "season",
        "SEASON": "season",
        "Cat6": "season",
        "Department": "department",
        "DEPARTMENT": "department",
        "DIVISION": "department",
        "Division": "department",
        "Section": "section",
        "SECTION": "section",
        "Secion": "section",
        "Zone": "zone",
        "ZONE": "zone",
        "Source": "source",
    }
    for src, dst in rename.items():
        if src in out.columns:
            out = out.rename(columns={src: dst})

    out = out.loc[:, ~out.columns.duplicated()]

    # TAX_AMOUNT aliases if present under another name
    if "tax_amount" not in out.columns:
        col_upper = {str(c).strip().upper(): c for c in out.columns}
        for alias in ("TAX_AMOUNT", "TAX AMOUNT", "GST_AMOUNT", "GST AMOUNT"):
            if alias in col_upper:
                out = out.rename(columns={col_upper[alias]: "tax_amount"})
                break

    for col in (
        "site_id",
        "site_name",
        "barcode",
        "sku",
        "size",
        "season",
        "source",
        "department",
        "section",
    ):
        if col not in out.columns:
            out[col] = ""
        out[col] = out[col].astype(str).str.strip().replace({"nan": "", "None": "", "<NA>": ""})

    try:
        from pipeline.pipeline_inputs import parse_sales_dates, enrich_sales_dept_section
    except ImportError:
        from pipeline_inputs import parse_sales_dates, enrich_sales_dept_section  # type: ignore

    out["date"] = parse_sales_dates(out["date"])
    for col in ("qty", "mrp", "net_sale_amount", "discount_amount"):
        if col not in out.columns:
            out[col] = 0.0
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0)

    if "tax_amount" not in out.columns:
        out["tax_amount"] = pd.NA
    else:
        out["tax_amount"] = pd.to_numeric(out["tax_amount"], errors="coerce")

    gross = (out["mrp"] * out["qty"]).clip(lower=0)
    out["discount_pct"] = 0.0
    mask = gross > 0
    out.loc[mask, "discount_pct"] = (
        (out.loc[mask, "discount_amount"] / gross.loc[mask] * 100).round(1)
    )

    out = out[out["date"].notna()].copy()
    out["month_key"] = out["date"].dt.strftime("%Y-%m")
    out["barcode"] = out["barcode"].apply(_barcode_str)

    # Fill Department / Section from source sales files when COMBINED lacks them
    out = enrich_sales_dept_section(out, barcode_col="barcode")

    # Item Master is canonical for Department / Section filters
    try:
        from pipeline.item_master import enrich_sales_dept_section_from_item_master
    except ImportError:
        from item_master import enrich_sales_dept_section_from_item_master  # type: ignore
    out = enrich_sales_dept_section_from_item_master(out, barcode_col="barcode")

    try:
        from pipeline.pipeline_inputs import derive_store_type_series
    except ImportError:
        from pipeline_inputs import derive_store_type_series  # type: ignore

    out["store_type"] = derive_store_type_series(out["site_name"], out["site_id"])
    # Fallback: Source column marks pure EBO file rows
    ebo_src = out["source"].astype(str).str.strip().str.upper().eq("EBO")
    out.loc[ebo_src & out["store_type"].eq(""), "store_type"] = "EBO"

    # Customer Master dims (ZONE, Cluster, CITY, STORE GRADE, LTL/New, …)
    try:
        from pipeline.customer_master import enrich_sales_with_customer_master
    except ImportError:
        from customer_master import enrich_sales_with_customer_master  # type: ignore

    out = enrich_sales_with_customer_master(out, site_col="site_id")
    # Prefer master channel when sales derive is blank
    if "channel" in out.columns:
        blank_st = out["store_type"].astype(str).str.strip().eq("")
        out.loc[blank_st, "store_type"] = out.loc[blank_st, "channel"].fillna("")

    channel_map = {
        "PANTALOONS": "PT",
        "PAN": "PT",
        "PT": "PT",
        "SHOPPERS STOP": "SSL",
        "SHOPPERS_STOP": "SSL",
        "SHOPPER STOP": "SSL",
        "SHOPPERSSTOP": "SSL",
        "SSL": "SSL",
        "CENTRO": "CENTRO",
        "ICONIC": "ICONIC",
        "EBO": "EBO",
    }
    out["store_type"] = (
        out["store_type"]
        .astype(str)
        .str.strip()
        .str.upper()
        .map(lambda s: channel_map.get(s, s))
    )
    # Precompute FY date columns & mrp_gross_val for fast financial year comparison
    if "date" in out.columns:
        dt = pd.to_datetime(out["date"], errors="coerce")
        out["dt"] = dt
        valid_mask = dt.notna()
        if valid_mask.any():
            m = dt.dt.month.to_numpy()
            y = dt.dt.year.to_numpy()
            is_apr_dec = m >= 4
            fy_start_year = np.where(is_apr_dec, y, y - 1)
            fin_idx = np.where(is_apr_dec, m - 3, m + 9)
            fy_end_short = (fy_start_year + 1) % 100

            out["fy"] = "FY " + pd.Series(fy_start_year, index=out.index, dtype=str) + "-" + pd.Series(fy_end_short, index=out.index).astype(str).str.zfill(2)
            out["fin_month_idx"] = fin_idx

            month_names_arr = np.array(["Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec", "Jan", "Feb", "Mar"])
            out["fin_month_name"] = month_names_arr[fin_idx - 1]

            quarter_arr = np.array(["Q1", "Q1", "Q1", "Q2", "Q2", "Q2", "Q3", "Q3", "Q3", "Q4", "Q4", "Q4"])
            out["quarter"] = quarter_arr[fin_idx - 1]

    if "mrp" in out.columns and "qty" in out.columns:
        out["mrp_gross_val"] = pd.to_numeric(out["mrp"], errors="coerce").fillna(0) * pd.to_numeric(out["qty"], errors="coerce").fillna(0)

    if "discount_pct" not in out.columns:
        gross = out["mrp_gross_val"] if "mrp_gross_val" in out.columns else (out["mrp"] * out["qty"])
        disc_amt = out["discount_amount"] if "discount_amount" in out.columns else 0
        out["discount_pct"] = np.where(gross > 0, (disc_amt / gross * 100).round(1), 0.0)

    fp_mask = (out.get("discount_pct", 0) <= 0.01) | (out.get("discount_amount", 0) <= 0.01)
    out["fp_sales"] = np.where(fp_mask, out["net_sale_amount"], 0.0)
    out["fp_qty"] = np.where(fp_mask, out["qty"], 0.0)

    from api.sales_year_loader import sanitize_store_channels
    out = sanitize_store_channels(out)

    # Channel Name on Sales-Master is often the legal customer (Lulu, Chunmun, …),
    # not PT/SSL/CENTRO/ICONIC/EBO. sanitize_store_channels remaps national chains
    # via site patterns; remaining LFS/MBO partners must land on EBO so they are
    # not dropped by the retail-channel whitelist below (was losing ~₹1.7 Cr FY27).
    _retail_codes = {"PT", "SSL", "CENTRO", "ICONIC", "EBO"}
    _allowed = _retail_codes | {
        "Pantaloons (PT)",
        "Shoppers Stop (SSL)",
        "Centro",
        "Iconic",
    }
    if "channel" in out.columns:
        ch = out["channel"].astype(str).str.strip()
        bad = ~ch.isin(_allowed) & ch.ne("") & ~ch.str.lower().isin({"nan", "none", "<na>"})
        if "store_type" in out.columns:
            st = out["store_type"].astype(str).str.strip().str.upper()
            st_ok = bad & st.isin(_retail_codes)
            out.loc[st_ok, "channel"] = st.loc[st_ok]
            out.loc[st_ok, "sales_channel"] = st.loc[st_ok]
            bad = ~out["channel"].astype(str).str.strip().isin(_allowed)
        if bad.any():
            out.loc[bad, "channel"] = "EBO"
            out.loc[bad, "sales_channel"] = "EBO"
            if "store_type" in out.columns:
                blank_st = out.loc[bad, "store_type"].astype(str).str.strip().eq("")
                out.loc[bad & blank_st, "store_type"] = "EBO"

    # Filter by channel (if available) or legacy store_type
    filter_col = (
        "channel"
        if "channel" in out.columns
        else "sales_channel"
        if "sales_channel" in out.columns
        else "store_type"
    )
    if filter_col in out.columns:
        out = out[out[filter_col].isin(_allowed)].copy()
    return out


def _barcode_str(v: Any) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    s = str(v).strip()
    if s.lower() in ("nan", "none", ""):
        return ""
    try:
        f = float(s)
        if f.is_integer():
            return str(int(f))
    except ValueError:
        pass
    return s


def _load_local_combined_sales() -> pd.DataFrame:
    try:
        from pipeline.pipeline_inputs import load_combined_sales
    except ImportError:
        from pipeline_inputs import load_combined_sales  # type: ignore
    return load_combined_sales()


def _resolve_sales_master_path() -> tuple[str | None, str | None]:
    """Primary sales source: input Sales-Master (PT, SSL, Centro, Iconic, EBO)."""
    try:
        from api.sales_year_loader import resolve_sales_master_path

        master = resolve_sales_master_path()
        if master is not None and master.exists() and master.stat().st_size > 0:
            mtime = os.path.getmtime(master)
            return str(master), f"sales-master:{master.resolve()}:{mtime}"
    except Exception as exc:
        logger.warning("Sales-Master resolve skipped: %s", exc)
    return None, None


def _blob_combined_path() -> tuple[str | None, str | None]:
    """Legacy fallback: COMBINED_SALES from Azure output/ (only if Sales-Master missing)."""
    try:
        from api.blob_store import BlobJsonStore

        store = BlobJsonStore()
        for ext in (".parquet", ".csv", ".xlsx"):
            blob_name = store._blob_name(_TABLE, ext)
            if not store._blob_exists(blob_name):
                continue
            etag = store._blob_etag(blob_name) or blob_name
            path = store._ensure_local_file(blob_name)
            if path and path.exists():
                return str(path), f"{blob_name}:{etag}"
    except Exception as exc:
        logger.warning("COMBINED_SALES blob read skipped: %s", exc)
    return None, None


def _local_enriched_combined_path() -> str | None:
    """Legacy local COMBINED_SALES with TAX_AMOUNT — only used if Sales-Master missing."""
    try:
        from pipeline.pipeline_inputs import COMBINED_SALES, COMBINED_SALES_CSV
    except ImportError:
        try:
            from pipeline_inputs import COMBINED_SALES, COMBINED_SALES_CSV  # type: ignore
        except ImportError:
            return None
    for path in (COMBINED_SALES_CSV, COMBINED_SALES):
        if not path.exists():
            continue
        try:
            if str(path).lower().endswith(".csv"):
                cols = pd.read_csv(path, nrows=0).columns.tolist()
            else:
                cols = pd.read_excel(path, nrows=0).columns.tolist()
            col_u = {str(c).strip().upper() for c in cols}
            if "TAX_AMOUNT" in col_u or "TAX AMOUNT" in col_u:
                return str(path)
        except Exception:
            continue
    return None


def _read_path(path: str) -> pd.DataFrame:
    p = path.lower()
    if p.endswith(".csv"):
        try:
            from pipeline.date_merge import is_excel_bytes, read_csv_bytes
        except ImportError:
            from date_merge import is_excel_bytes, read_csv_bytes  # type: ignore

        data = Path(path).read_bytes()
        if is_excel_bytes(data):
            return pd.read_excel(path, engine="openpyxl")
        return read_csv_bytes(data, low_memory=False)
    if p.endswith(".parquet"):
        return pd.read_parquet(path)
    return pd.read_excel(path, engine="openpyxl")


def _safe_cache_key(source_key: str) -> str:
    return (
        source_key.replace(":", "_")
        .replace("/", "_")
        .replace("\\", "_")
        .replace('"', "")
        .replace("'", "")
    )[:180]


def _parquet_cache_path(source_key: str) -> Path:
    # v4: primary source is Sales-Master (PT/SSL/Centro/Iconic/EBO), not COMBINED_SALES
    digest = hashlib.sha256(source_key.encode()).hexdigest()[:16]
    return _API_CACHE_DIR / f"sales_dashboard_{_safe_cache_key(source_key)[:100]}_{digest}_v5.parquet"


def _load_parquet_cache(source_key: str) -> pd.DataFrame | None:
    path = _parquet_cache_path(source_key)
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path)
        logger.info("Sales dashboard loaded parquet cache (%s rows)", len(df))
        if "fy" not in df.columns and "date" in df.columns:
            dt = pd.to_datetime(df["date"], errors="coerce")
            df["dt"] = dt
            valid_mask = dt.notna()
            if valid_mask.any():
                m = dt.dt.month.to_numpy()
                y = dt.dt.year.to_numpy()
                is_apr_dec = m >= 4
                fy_start_year = np.where(is_apr_dec, y, y - 1)
                fin_idx = np.where(is_apr_dec, m - 3, m + 9)
                fy_end_short = (fy_start_year + 1) % 100
                df["fy"] = "FY " + pd.Series(fy_start_year, index=df.index, dtype=str) + "-" + pd.Series(fy_end_short, index=df.index).astype(str).str.zfill(2)
                df["fin_month_idx"] = fin_idx
                month_names_arr = np.array(["Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec", "Jan", "Feb", "Mar"])
                df["fin_month_name"] = month_names_arr[fin_idx - 1]
                quarter_arr = np.array(["Q1", "Q1", "Q1", "Q2", "Q2", "Q2", "Q3", "Q3", "Q3", "Q4", "Q4", "Q4"])
                df["quarter"] = quarter_arr[fin_idx - 1]
            if "mrp" in df.columns and "qty" in df.columns:
                df["mrp_gross_val"] = pd.to_numeric(df["mrp"], errors="coerce").fillna(0) * pd.to_numeric(df["qty"], errors="coerce").fillna(0)
            _save_parquet_cache(df, source_key)
        return df
    except Exception as exc:
        logger.warning("Sales dashboard parquet cache read failed: %s", exc)
        return None


def _save_parquet_cache(df: pd.DataFrame, source_key: str) -> None:
    try:
        _API_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        df.to_parquet(_parquet_cache_path(source_key), index=False)
        logger.info("Sales dashboard parquet cache saved (%s rows)", len(df))
    except Exception as exc:
        logger.warning("Sales dashboard parquet cache write failed: %s", exc)


_SALES_CACHE_BLOB = "cache/sales-overview/dashboard.parquet"
_SALES_CACHE_STAMP = "cache/sales-overview/stamp.json"
_FY_SUMMARY_BLOB = "cache/sales-overview/financial_year_summary.parquet"


def _source_signature(path: str) -> str:
    """Stable id for the Sales Master file. Same file → same signature, even after a restart."""
    p = Path(path)
    size = p.stat().st_size
    etag_file = Path(str(p) + ".etag")
    if etag_file.exists():
        token = etag_file.read_text(encoding="utf-8").strip() or str(size)
    else:
        token = str(p.stat().st_mtime_ns)
    return f"{size}:{token}"


_BLOB_STORE_SINGLETON = None
_BLOB_STORE_FAILED_UNTIL = 0.0
_BLOB_STORE_COOLDOWN_S = float(os.environ.get("BLOB_STORE_COOLDOWN_S", "120") or "120")
_STAMP_SIG_CACHE: str | None = None
_STAMP_SIG_CACHE_AT = 0.0
_STAMP_SIG_TTL = int(os.environ.get("SALES_STAMP_CACHE_TTL_SECONDS", "300"))


def mark_blob_store_unavailable() -> None:
    """Cool down Azure after DNS/network failure (retry later — don't kill blob for process life)."""
    global _BLOB_STORE_SINGLETON, _BLOB_STORE_FAILED_UNTIL
    _BLOB_STORE_FAILED_UNTIL = time.time() + max(30.0, _BLOB_STORE_COOLDOWN_S)
    _BLOB_STORE_SINGLETON = None


def _blob_store():
    """Reuse one BlobJsonStore. Creating a new client + container ping every call is slow."""
    global _BLOB_STORE_SINGLETON, _BLOB_STORE_FAILED_UNTIL
    if time.time() < _BLOB_STORE_FAILED_UNTIL:
        return None
    if _BLOB_STORE_SINGLETON is not None:
        return _BLOB_STORE_SINGLETON
    if not os.environ.get("AZURE_STORAGE_CONNECTION_STRING"):
        _BLOB_STORE_FAILED_UNTIL = time.time() + max(30.0, _BLOB_STORE_COOLDOWN_S)
        return None
    try:
        from api.blob_store import BlobJsonStore

        _BLOB_STORE_SINGLETON = BlobJsonStore()
        _BLOB_STORE_FAILED_UNTIL = 0.0
        return _BLOB_STORE_SINGLETON
    except Exception as exc:
        logger.warning(
            "Sales overview blob cache unavailable (retry in %.0fs): %s",
            _BLOB_STORE_COOLDOWN_S,
            exc,
        )
        _BLOB_STORE_FAILED_UNTIL = time.time() + max(30.0, _BLOB_STORE_COOLDOWN_S)
        return None


def _local_stamp_signature() -> str | None:
    """Read stamp from disk blob_cache without a network round-trip."""
    try:
        from api.blob_store import _CACHE_DIR as _BLOB_DISK, _safe_name

        path = _BLOB_DISK / _safe_name(_SALES_CACHE_STAMP)
        if not path.exists() or path.stat().st_size <= 0:
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        sig = str(payload.get("signature") or "").strip()
        return sig or None
    except Exception:
        return None


def _blob_stamp_signature() -> str | None:
    """Sales overview stamp. Memory + local disk first; Azure at most every SALES_STAMP_CACHE_TTL_SECONDS."""
    global _STAMP_SIG_CACHE, _STAMP_SIG_CACHE_AT
    now = time.time()
    if _STAMP_SIG_CACHE is not None and (now - _STAMP_SIG_CACHE_AT) < _STAMP_SIG_TTL:
        return _STAMP_SIG_CACHE
    # Instant path — do not block page loads on Azure for a tiny stamp.json.
    local_sig = _local_stamp_signature()
    store = _blob_store()
    if store is not None:
        try:
            raw = store._download_bytes(_SALES_CACHE_STAMP)
            if raw:
                payload = json.loads(raw.decode("utf-8"))
                sig = str(payload.get("signature") or "").strip() or None
                if sig:
                    _STAMP_SIG_CACHE = sig
                    _STAMP_SIG_CACHE_AT = now
                    return sig
        except Exception as exc:
            logger.warning("Sales overview stamp read failed: %s", exc)
    if local_sig:
        _STAMP_SIG_CACHE = local_sig
        _STAMP_SIG_CACHE_AT = now
        return local_sig
    if _STAMP_SIG_CACHE is not None:
        return _STAMP_SIG_CACHE
    return None


_READY_OVERVIEW_DF: pd.DataFrame | None = None


_READY_OVERVIEW_STAMP: str | None = None


def load_ready_overview_frame() -> pd.DataFrame | None:
    """9 AM parquet on blob. Served even when this machine's Sales Master mtime differs.

    The old gate compared stamp signature to the local file mtime, so Azure never
    accepted the cache and rebuilt the CSV until the gateway returned an empty 502.
    """
    global _READY_OVERVIEW_DF, _READY_OVERVIEW_STAMP
    stamp = _blob_stamp_signature()
    if (
        _READY_OVERVIEW_DF is not None
        and not _READY_OVERVIEW_DF.empty
        and stamp
        and stamp == _READY_OVERVIEW_STAMP
    ):
        return _READY_OVERVIEW_DF
    store = _blob_store()
    if store is None:
        return None
    try:
        # Force etag check so a newly published dashboard.parquet replaces disk cache.
        local_blob = store._ensure_local_file(_SALES_CACHE_BLOB)
        if local_blob is None or not local_blob.exists() or local_blob.stat().st_size <= 0:
            logger.warning("Sales overview ready cache missing on blob (%s)", _SALES_CACHE_BLOB)
            return None
        df = pd.read_parquet(local_blob)
        if df is None or df.empty:
            return None
        logger.info(
            "Sales overview fetched ready blob cache (%s rows, stamp=%s)",
            len(df),
            stamp or "none",
        )
        _READY_OVERVIEW_DF = df
        _READY_OVERVIEW_STAMP = stamp
        return df
    except Exception as exc:
        logger.warning("Sales overview ready cache fetch failed: %s", exc)
        return None


def _pull_blob_sales_cache(path: str | None, cache_key: str | None) -> pd.DataFrame | None:
    """Use the 9 AM ready parquet when this server does not have a local copy yet."""
    del path, cache_key
    return load_ready_overview_frame()


def _publish_fy_summary_blob(df: pd.DataFrame | None = None) -> bool:
    """Upload financial_year_summary.parquet to Azure Blob storage so hosted instances load instantly."""
    store = _blob_store()
    if store is None:
        return False
    try:
        from azure.storage.blob import ContentSettings

        summary_path = _API_CACHE_DIR / "financial_year_summary.parquet"
        if (df is None or df.empty) and summary_path.exists() and summary_path.stat().st_size > 0:
            with summary_path.open("rb") as fh:
                store.container.upload_blob(
                    name=_FY_SUMMARY_BLOB,
                    data=fh,
                    overwrite=True,
                    content_settings=ContentSettings(content_type="application/octet-stream"),
                )
            logger.info("FY summary published to blob from disk")
            return True
        elif df is not None and not df.empty:
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            df.to_parquet(summary_path, index=False)
            with summary_path.open("rb") as fh:
                store.container.upload_blob(
                    name=_FY_SUMMARY_BLOB,
                    data=fh,
                    overwrite=True,
                    content_settings=ContentSettings(content_type="application/octet-stream"),
                )
            logger.info("FY summary saved and published to blob (%s rows)", len(df))
            return True
    except Exception as exc:
        logger.warning("FY summary blob publish failed: %s", exc)
    return False


def _publish_sales_cache(cache_key: str, signature: str, rows: int | None) -> bool:
    store = _blob_store()
    local = _parquet_cache_path(cache_key)
    if store is None:
        logger.warning("Sales overview cache publish skipped: blob store unavailable")
        return False
    if not local.exists() or local.stat().st_size <= 0:
        logger.warning("Sales overview cache publish skipped: missing local %s", local)
        return False
    try:
        from api.blob_store import upload_local_file_with_retry

        upload_local_file_with_retry(
            store.container,
            blob_name=_SALES_CACHE_BLOB,
            path=local,
            content_type="application/octet-stream",
            timeout_s=900,
        )
        stamp = json.dumps({"signature": signature, "rows": rows, "saved_at": time.time()}).encode("utf-8")
        store._upload_bytes(_SALES_CACHE_STAMP, stamp, "application/json")
        logger.info(
            "Sales overview cache published to blob (signature=%s bytes=%s)",
            signature,
            local.stat().st_size,
        )
        _publish_fy_summary_blob()
        return True
    except Exception as exc:
        logger.warning("Sales overview cache publish failed: %s", exc)
        return False


def _warm_home_year_slices(force_refresh: bool = False) -> list[int]:
    """Home tab reads the same Sales Master cache, one financial year at a time."""
    from api.sales_year_loader import load_sales_for_year, warm_fy_years_sorted

    ready: list[int] = []
    for year in warm_fy_years_sorted():
        try:
            frame = load_sales_for_year(year, force_refresh=force_refresh)
            if frame is not None and not frame.empty:
                ready.append(year)
        except Exception as exc:
            logger.warning("Home FY cache %s skipped: %s", year, exc)
    return ready


def warm_sales_overview_cache(force_refresh: bool = False) -> dict[str, Any]:
    """Build Sales Overview cache after the 9 AM merge. Skip when Sales Master is unchanged unless force_refresh=True."""
    path, cache_key = _resolve_sales_master_path()
    if not path or not cache_key:
        return {"ok": False, "skipped": True, "detail": "Sales Master not found"}
    try:
        signature = _source_signature(path)
    except OSError as exc:
        return {"ok": False, "skipped": True, "detail": str(exc)}

    local = _parquet_cache_path(cache_key)
    local_ready = local.exists() and local.stat().st_size > 0
    blob_same = _blob_stamp_signature() == signature
    if not force_refresh and local_ready and blob_same:
        years = _warm_home_year_slices(force_refresh=False)
        try:
            _get_fy_summary_df(force_rebuild=False)
        except Exception:
            pass
        return {
            "ok": True,
            "skipped": True,
            "signature": signature,
            "home_years": years,
            "detail": "Sales Master unchanged — kept ready cache",
        }

    df = _get_sales_df(force_refresh=True)
    published = _publish_sales_cache(cache_key, signature, len(df))
    years = _warm_home_year_slices(force_refresh=True)
    try:
        fy_df = _get_fy_summary_df(force_rebuild=True)
        _publish_fy_summary_blob(fy_df)
    except Exception as exc:
        logger.warning("FY summary build during warm-up failed: %s", exc)
    return {
        "ok": True,
        "skipped": False,
        "published": published,
        "rows": len(df),
        "signature": signature,
        "home_years": years,
        "detail": f"Built Sales Overview cache ({len(df)} rows)",
    }


def _load_sales_frame(force_refresh: bool, cache_key: str | None, path: str | None) -> pd.DataFrame:
    if not force_refresh and cache_key:
        cached = _load_parquet_cache(cache_key)
        if cached is not None and not cached.empty:
            return cached
        pulled = _pull_blob_sales_cache(path, cache_key)
        if pulled is not None and not pulled.empty:
            return pulled

    frames: list[pd.DataFrame] = []
    raw: pd.DataFrame | None = None
    read_path = Path(path) if path else None

    # Prefer sibling parquet of the same source file only (never swap in COMBINED when reading Sales-Master)
    if read_path and read_path.suffix.lower() == ".csv":
        sibling_pq = read_path.with_suffix(".parquet")
        if sibling_pq.exists() and sibling_pq.stat().st_size > 0 and sibling_pq.stat().st_mtime_ns >= read_path.stat().st_mtime_ns:
            logger.info("Sales dashboard preferring sibling parquet %s", sibling_pq.name)
            raw = pd.read_parquet(sibling_pq)

    if raw is None and path:
        logger.info("Sales dashboard loading %s", path)
        raw = _read_path(path)
    elif raw is None:
        # Last resort: local COMBINED_SALES if Sales-Master path was not resolved
        logger.info("Sales dashboard loading local COMBINED_SALES fallback")
        try:
            raw = _load_local_combined_sales()
        except Exception as exc:
            logger.warning("Local combined sales read skipped: %s", exc)

    if raw is not None and not raw.empty:
        frames.append(raw)

    if not frames:
        raise HTTPException(
            status_code=404,
            detail=(
                "Sales-Master not found. Upload "
                "'Sales-Master (PT, SSL, Centro, Iconic, EBO)' under input/."
            ),
        )

    comb = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
    df = _normalize_columns(comb)

    source_note = "sales-master" if path and "SALES_MASTER" in Path(path).name.upper() else ("blob" if path else "local")
    if cache_key:
        _save_parquet_cache(df, cache_key)
    logger.info(
        "Sales dashboard cache refreshed (%s rows, %s, tax_non_null=%s)",
        len(df),
        source_note,
        int(df["tax_amount"].notna().sum()) if "tax_amount" in df.columns else 0,
    )

    # Pre-build per-FY parquet caches in background so year-filter API calls are instant
    def _prebuild_fy_caches(full_df: pd.DataFrame) -> None:
        try:
            from api.sales_year_loader import _parquet_path, _save_parquet, _mtime_key, resolve_sales_master_path
            master = resolve_sales_master_path()
            source_k = _mtime_key(master) if master else "combined_sales"
            if "fy" not in full_df.columns:
                return
            for fy_label in full_df["fy"].dropna().unique():
                # fy_label is like "FY 2025-26" → year = 2026
                try:
                    parts = str(fy_label).split("-")
                    fy_end_year = int(parts[-1]) if len(parts[-1]) == 2 else int(parts[-1])
                    if len(parts[-1]) == 2:
                        fy_year = 2000 + int(parts[-1])
                    else:
                        fy_year = int(parts[-1])
                    from api.sales_year_loader import supported_dashboard_years

                    if fy_year not in supported_dashboard_years():
                        continue
                    pq = _parquet_path(fy_year, source_k)
                    if pq.exists():
                        continue  # already cached
                    sliced = full_df[full_df["fy"].astype(str).str.strip() == str(fy_label)].copy()
                    if not sliced.empty:
                        _save_parquet(sliced, fy_year, source_k)
                        logger.info("Pre-cached FY parquet: year=%s rows=%s", fy_year, len(sliced))
                except Exception as inner_exc:
                    logger.warning("FY pre-cache failed for %s: %s", fy_label, inner_exc)
        except Exception as exc:
            logger.warning("FY pre-cache background task failed: %s", exc)

    import threading as _threading
    _threading.Thread(target=_prebuild_fy_caches, args=(df.copy(),), daemon=True).start()

    return df



def _get_sales_df(force_refresh: bool = False) -> pd.DataFrame:
    """Load Sales Master for screens that need every year together.

    When year folders exist, this is
    Sales-Master/2024 + 2025 + 2026 .../current/SSI_PT_CENTRO_ICONIC_EBO_SALES_MASTER.csv.
    Until that split is on blob, it still reads Sales-Master/current/.
    Home and the Sales Dashboard year view use load_sales_for_year instead
    (FY27 = 2026 folder, and 2027 folder once it exists).
    """
    global _CACHE_DF, _CACHE_KEY, _CACHE_AT

    now = time.time()
    # 1) Sales-Master from input/ (local or Azure blob current/)
    path, cache_key = _resolve_sales_master_path()

    # 2) Fallback COMBINED only when master missing
    if not path:
        local_enriched = _local_enriched_combined_path()
        if local_enriched:
            try:
                mtime = os.path.getmtime(local_enriched)
                cache_key = f"local-tax:{local_enriched}:{mtime}"
                path = local_enriched
            except OSError:
                path = local_enriched
                cache_key = f"local-tax:{local_enriched}"
        if not path:
            path, cache_key = _blob_combined_path()

    if not cache_key:
        cache_key = "default_sales_master"

    if (
        not force_refresh
        and _CACHE_DF is not None
        and cache_key is not None
        and _CACHE_KEY == cache_key
    ):
        return _CACHE_DF

    with _SALES_LOAD_LOCK:
        if (
            not force_refresh
            and _CACHE_DF is not None
            and cache_key is not None
            and _CACHE_KEY == cache_key
        ):
            return _CACHE_DF

        df = _load_sales_frame(force_refresh, cache_key, path)
        _CACHE_DF = df
        _CACHE_KEY = cache_key or f"local:{len(df)}"
        _CACHE_AT = time.time()
    return df


def _val_str(v: Any) -> str:
    if v is None:
        return ""
    if hasattr(v, "default"):
        d = getattr(v, "default", "")
        if d is None or "PydanticUndefined" in str(d) or "Ellipsis" in str(d):
            return ""
        return str(d).strip()
    try:
        return str(v).strip()
    except Exception:
        return ""


def _apply_filters(
    df: pd.DataFrame,
    site_id: Any = None,
    site_ids: Any = None,
    month: Any = None,
    sku: Any = None,
    season: Any = None,
    barcode: Any = None,
    barcodes: Any = None,
    search: Any = None,
    channel: Any = None,
    departments: Any = None,
    sections: Any = None,
    zones: Any = None,
    store_grades: Any = None,
    states: Any = None,
    cities: Any = None,
    exclude: frozenset[str] | None = None,
) -> pd.DataFrame:
    """Filter rows; pass exclude={...} to omit filters when building dropdown options."""
    skip = exclude or frozenset()
    out = df

    ch_str = _val_str(channel)
    if "channel" not in skip and ch_str and ch_str.lower() not in ("", "all"):
        if ch_str.lower() == "__none__":
            out = out.iloc[0:0]
        else:
            # Accept short codes (PT/SSL) and display labels (Pantaloons (PT) / Shoppers Stop (SSL)).
            alias = {
                "PANTALOONS": "PT",
                "PANTALOONS (PT)": "PT",
                "SHOPPERS-STOP": "SSL",
                "SHOPPERS_STOP": "SSL",
                "SHOPPERS STOP": "SSL",
                "SHOPPERS STOP (SSL)": "SSL",
                "LIFESTYLE": "LS",
            }
            display_by_code = {
                "PT": {"PT", "PANTALOONS", "PANTALOONS (PT)"},
                "SSL": {"SSL", "SHOPPERS STOP", "SHOPPERS STOP (SSL)", "SHOPPERS-STOP", "SHOPPERS_STOP"},
                "CENTRO": {"CENTRO"},
                "ICONIC": {"ICONIC"},
                "EBO": {"EBO"},
            }
            raw_chs = [c.strip().upper() for c in ch_str.split(",") if c.strip()]
            resolved_codes = {alias.get(c, c) for c in raw_chs if c}
            match_vals: set[str] = set()
            for code in resolved_codes:
                match_vals.update(display_by_code.get(code, {code}))
                match_vals.add(code)
            if match_vals:
                mask = pd.Series(False, index=out.index)
                for channel_col in ("sales_channel", "channel", "store_type"):
                    if channel_col not in out.columns:
                        continue
                    clean_ch = out[channel_col].astype(str).str.strip().str.upper()
                    mask = mask | clean_ch.isin(match_vals)
                    # Also allow "PANTALOONS (PT)" style contains for short codes
                    for code in resolved_codes:
                        if len(code) <= 6:
                            mask = mask | clean_ch.str.contains(rf"\({code}\)|^{code}$", na=False, regex=True)
                out = out[mask]

    site_ids_str = _val_str(site_ids)
    site_id_str = _val_str(site_id)

    def _site_identity_mask(frame: pd.DataFrame, wanted: set[str]) -> pd.Series:
        """Match Customer Master site keys against sales identity columns.

        Sales `site_id` is often a display name (e.g. ``PT-Mall (P139)``), while CM /
        Home filters send numeric / short codes (``120``, ``P139``). Matching only
        ``site_id`` drops almost all zone-filtered rows.
        """
        if frame is None or frame.empty or not wanted:
            return pd.Series(False, index=getattr(frame, "index", None))
        mask = pd.Series(False, index=frame.index)
        for col in (
            "site_code",
            "site_manual_code",
            "site_short_code",
            "site_id",
            "site_id_name",
        ):
            if col not in frame.columns:
                continue
            clean = (
                frame[col]
                .astype(str)
                .str.strip()
                .str.upper()
                .str.replace(r"\.0$", "", regex=True)
            )
            mask = mask | clean.isin(wanted)
        # Pull trailing (P139) / (259) / -174 tokens out of display site_id
        if "site_id" in frame.columns:
            sid_u = frame["site_id"].astype(str).str.upper()
            for pat in (r"\(([A-Z]?\d+)\)\s*$", r"-([A-Z]?\d+)\s*$"):
                extracted = sid_u.str.extract(pat, expand=False)
                mask = mask | extracted.fillna("").isin(wanted)
        return mask

    if "site_ids" not in skip and site_ids_str:
        if site_ids_str.lower() == "__none__":
            out = out.iloc[0:0]
        else:
            ids = {s.strip().upper().replace(".0", "") for s in site_ids_str.split(",") if s.strip()}
            if ids:
                out = out[_site_identity_mask(out, ids)]
    elif "site_id" not in skip and site_id_str and site_id_str.lower() not in ("", "all"):
        sid = site_id_str.upper().replace(".0", "")
        out = out[_site_identity_mask(out, {sid})]

    month_str = _val_str(month)
    if "month" not in skip and month_str and month_str.lower() not in ("", "all"):
        out = out[out["month_key"] == month_str]

    sku_str = _val_str(sku)
    if "sku" not in skip and sku_str and sku_str.lower() not in ("", "all"):
        allowed_skus = {value.strip().upper() for value in sku_str.split(",") if value.strip()}
        out = out[out["sku"].astype(str).str.strip().str.upper().isin(allowed_skus)]

    season_str = _val_str(season)
    if "season" not in skip and season_str and season_str.lower() not in ("", "all"):
        seasons = {s.strip().upper() for s in season_str.split(",") if s.strip()}
        if seasons:
            out = out[out["season"].astype(str).str.strip().str.upper().isin(seasons)]

    barcodes_str = _val_str(barcodes)
    barcode_str = _val_str(barcode)
    if "barcodes" not in skip and barcodes_str:
        if barcodes_str.lower() == "__none__":
            out = out.iloc[0:0]
        else:
            b_set = {b.strip().upper().replace(".0", "") for b in barcodes_str.split(",") if b.strip()}
            if b_set:
                out = out[out["barcode"].astype(str).str.strip().str.upper().isin(b_set)]
    elif "barcode" not in skip and barcode_str and barcode_str.lower() not in ("", "all"):
        bc = barcode_str.upper().replace(".0", "")
        out = out[out["barcode"].astype(str).str.strip().str.upper() == bc]

    dept_str = _val_str(departments)
    if "departments" not in skip and dept_str:
        if dept_str.lower() == "__none__":
            out = out.iloc[0:0]
        else:
            allowed = {x.strip() for x in dept_str.split(",") if x.strip()}
            if allowed and "department" in out.columns:
                out = out[out["department"].astype(str).str.strip().isin(allowed)]

    sec_str = _val_str(sections)
    if "sections" not in skip and sec_str:
        if sec_str.lower() == "__none__":
            out = out.iloc[0:0]
        else:
            allowed = {x.strip() for x in sec_str.split(",") if x.strip()}
            if allowed and "section" in out.columns:
                out = out[out["section"].astype(str).str.strip().isin(allowed)]

    zones_str = _val_str(zones)
    if "zones" not in skip and zones_str:
        if zones_str.lower() == "__none__":
            out = out.iloc[0:0]
        else:
            allowed_zones = {x.strip().upper() for x in zones_str.split(",") if x.strip()}
            if allowed_zones and "zone" in out.columns:
                out = out[out["zone"].astype(str).str.strip().str.upper().isin(allowed_zones)]

    sg_str = _val_str(store_grades)
    if "store_grades" not in skip and sg_str:
        if sg_str.lower() == "__none__":
            out = out.iloc[0:0]
        else:
            allowed_sg = {x.strip() for x in sg_str.split(",") if x.strip()}
            if allowed_sg and "store_grade" in out.columns:
                out = out[out["store_grade"].astype(str).str.strip().isin(allowed_sg)]

    states_str = _val_str(states)
    if "states" not in skip and states_str:
        if states_str.lower() == "__none__":
            out = out.iloc[0:0]
        else:
            allowed_st = {x.strip() for x in states_str.split(",") if x.strip()}
            if allowed_st:
                state_col = next((c for c in ("state", "state_name") if c in out.columns), None)
                if state_col:
                    out = out[out[state_col].astype(str).str.strip().isin(allowed_st)]

    cities_str = _val_str(cities)
    if "cities" not in skip and cities_str:
        if cities_str.lower() == "__none__":
            out = out.iloc[0:0]
        else:
            allowed_ct = {x.strip() for x in cities_str.split(",") if x.strip()}
            if allowed_ct:
                city_col = next((c for c in ("city", "city_name") if c in out.columns), None)
                if city_col:
                    out = out[out[city_col].astype(str).str.strip().isin(allowed_ct)]

    q_str = _val_str(search)
    if "search" not in skip and q_str:
        q = q_str.lower()
        mask = (
            out["site_id"].str.lower().str.contains(q, na=False)
            | out["site_name"].str.lower().str.contains(q, na=False)
            | out["sku"].str.lower().str.contains(q, na=False)
            | out["barcode"].str.lower().str.contains(q, na=False)
            | out["season"].str.lower().str.contains(q, na=False)
        )
        for extra_col in ("customer_name", "city", "state", "zone", "store_type"):
            if extra_col in out.columns:
                mask |= out[extra_col].astype(str).str.lower().str.contains(q, na=False)
        out = out[mask]
    return out


_WEEKDAY_LABELS = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")


def _fiscal_year_end_ts(d: pd.Timestamp) -> int:
    return int(d.year + 1) if d.month >= 4 else int(d.year)


def _fy_codes_covering_range(start: pd.Timestamp, end: pd.Timestamp) -> list[int]:
    """Indian FY end-year codes that overlap [start, end] (inclusive)."""
    if pd.isna(start) or pd.isna(end):
        return []
    a, b = (start, end) if start <= end else (end, start)
    y0 = _fiscal_year_end_ts(pd.Timestamp(a))
    y1 = _fiscal_year_end_ts(pd.Timestamp(b))
    return list(range(min(y0, y1), max(y0, y1) + 1))


def _parse_fy_end_year(token: str) -> int | None:
    """Parse FY27 / FY-27 / FY2027 / 2026-27 / 27 → FY end calendar year."""
    raw = str(token or "").strip().upper().replace(" ", "")
    if not raw:
        return None
    from api.sales_year_loader import normalize_dashboard_year
    try:
        norm = normalize_dashboard_year(raw)
        if norm:
            return norm
    except Exception:
        pass
    m = re.search(r"(\d{4})-(\d{2})$", raw)
    if m:
        return 2000 + int(m.group(2))
    m = re.search(r"FY-?(\d{2,4})$", raw)
    if m:
        n = int(m.group(1))
        return n if n >= 1000 else 2000 + n
    if raw.isdigit() and len(raw) in (2, 4):
        n = int(raw)
        return n if n >= 1000 else 2000 + n
    return None


def _fy_years_for_timeline(
    *,
    year_val: int,
    timeline_mode: str | None,
    timeline_values: str | None,
) -> list[int]:
    """
    Which FY parquet slices to load so the timeline filter has complete data.

    - Default / FY → single FY code (year_val)
    - Calendar Year 2026 → FY26 (Jan–Mar) + FY27 (Apr–Dec)
    - Date range → every FY overlapping the range
    """
    mode = (timeline_mode or "").strip().lower()
    values = [v.strip() for v in str(timeline_values or "").split(",") if v.strip()]
    years: set[int] = set()

    if mode == "year" and values:
        for v in values:
            if v.isdigit():
                cal = int(v)
                # Jan–Mar live in FY ending `cal`; Apr–Dec live in FY ending `cal+1`
                years.add(cal)
                years.add(cal + 1)
    elif mode == "date" and values:
        try:
            from_ts = pd.Timestamp(values[0][:10])
            to_ts = pd.Timestamp((values[1] if len(values) > 1 else values[0])[:10])
            years.update(_fy_codes_covering_range(from_ts, to_ts))
        except Exception:
            years.add(int(year_val))
    elif mode in ("fy_year", "fy", "financial_year") and values:
        for v in values:
            fy = _parse_fy_end_year(v)
            if fy:
                years.add(fy)
    elif mode == "event" and values:
        for value in values:
            event_id, separator, event_year = value.partition(":")
            if separator:
                if not event_year.isdigit() or not 2000 <= int(event_year) <= 2100:
                    raise HTTPException(status_code=422, detail="Invalid event year")
                calendar_year = int(event_year)
                event_dates = [d for d in pd.date_range(f"{calendar_year}-01-01", f"{calendar_year}-12-31") if _event_match(d, event_id)]
                if not event_dates:
                    raise HTTPException(status_code=422, detail="Unknown sales event")
                years.update(_fiscal_year_end_ts(d) for d in event_dates)
            else:
                years.add(int(year_val))
    elif mode == "month" and values:
        for v in values:
            if re.match(r"^\d{4}-\d{2}$", v):
                ts = pd.Timestamp(f"{v}-01")
                years.add(_fiscal_year_end_ts(ts))
    elif mode == "fy_week" and values:
        from api.financial_calendar import selected_week_dates
        dates = selected_week_dates(",".join(values))
        if len(dates) > 0:
            if not any(re.search(r"FY-?\d{2,4}", v.upper()) for v in values):
                years.add(int(year_val))
            else:
                years.update(_fiscal_year_end_ts(d) for d in dates)
        else:
            years.add(int(year_val))

    if not years:
        years.add(int(year_val))
    return sorted(years)


def _load_sales_for_timeline(
    *,
    year_val: int,
    timeline_mode: str | None,
    timeline_values: str | None,
    force_refresh: bool = False,
) -> pd.DataFrame:
    """Load one or more FY slices so calendar-year / cross-FY timelines are complete."""
    from api.sales_year_loader import load_sales_for_year

    fy_years = _fy_years_for_timeline(
        year_val=year_val,
        timeline_mode=timeline_mode,
        timeline_values=timeline_values,
    )
    parts: list[pd.DataFrame] = []
    for y in fy_years:
        try:
            part = load_sales_for_year(y, force_refresh=force_refresh)
            if part is not None and not part.empty:
                parts.append(part)
        except Exception as exc:
            logger.warning("sales timeline load FY-%s failed: %s", y, exc)
    if not parts:
        return load_sales_for_year(year_val, force_refresh=force_refresh)
    if len(parts) == 1:
        out = parts[0]
    else:
        out = pd.concat(parts, ignore_index=True)
        if "date" in out.columns and timeline_mode != "event":
            out = out.drop_duplicates(
                subset=[
                    c
                    for c in ("date", "site_id", "barcode", "sku", "net_sale_amount", "qty")
                    if c in out.columns
                ],
                keep="first",
            )

    # Calendar year: drop the extra FY months immediately so KPIs/table work on one year only.
    # (2026 loads FY26+FY27, then we keep only 2026-01-01 … 2026-12-31.)
    mode = (timeline_mode or "").strip().lower()
    values = [v.strip() for v in str(timeline_values or "").split(",") if v.strip()]
    if mode == "year" and values and "date" in out.columns and not out.empty:
        cal_years = [int(v) for v in values if v.isdigit()]
        if cal_years:
            y0, y1 = min(cal_years), max(cal_years)
            start = pd.Timestamp(f"{y0}-01-01")
            end = pd.Timestamp(f"{y1}-12-31") + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
            before = len(out)
            out = out[(out["date"] >= start) & (out["date"] <= end)]
            logger.info(
                "sales timeline calendar trim %s–%s: %s → %s rows",
                y0, y1, before, len(out),
            )

    logger.info(
        "sales timeline loaded FY years=%s → %s rows (mode=%s values=%s)",
        fy_years,
        len(out),
        timeline_mode,
        timeline_values,
    )
    return out


def _load_sply_prior_df(
    filtered: pd.DataFrame,
    *,
    filter_kw: dict[str, Any] | None = None,
    timeline_mode: str = "",
    timeline_values: str = "",
) -> pd.DataFrame:
    """
    Permanent like-for-like growth rule (always −1 calendar year on the filtered window):

    - FY / date range / calendar year → same start–end dates last year
    - Specific month → same month last year (actual days present in data)
    - Specific FY week → same week last year (dates shifted −1 year)
    - Custom range → same range shifted −1 year

    No hardcoded years — whatever period is on screen is compared to SPLY.
    """
    mapped_dates = None
    if timeline_mode == "fy_week" and timeline_values:
        from api.financial_calendar import selected_week_dates
        mapped_dates = selected_week_dates(timeline_values) - pd.DateOffset(years=1)
    if mapped_dates is None and (filtered is None or filtered.empty or "date" not in filtered.columns):
        return pd.DataFrame()
    min_d = mapped_dates.min() if mapped_dates is not None else filtered["date"].min()
    max_d = mapped_dates.max() if mapped_dates is not None else filtered["date"].max()
    if pd.isna(min_d) or pd.isna(max_d):
        return pd.DataFrame()

    ly_min = pd.Timestamp(min_d) if mapped_dates is not None else pd.Timestamp(min_d) - pd.DateOffset(years=1)
    ly_max = pd.Timestamp(max_d) if mapped_dates is not None else pd.Timestamp(max_d) - pd.DateOffset(years=1)
    y0 = _fiscal_year_end_ts(ly_min)
    y1 = _fiscal_year_end_ts(ly_max)

    from api.sales_year_loader import load_sales_for_year

    parts: list[pd.DataFrame] = []
    for y in range(min(y0, y1), max(y0, y1) + 1):
        try:
            part = load_sales_for_year(y)
            if part is not None and not part.empty:
                parts.append(part)
        except Exception as exc:
            logger.warning("SPLY prior load failed for FY year=%s: %s", y, exc)
    if not parts:
        return pd.DataFrame()

    ly_full = pd.concat(parts, ignore_index=True) if len(parts) > 1 else parts[0]
    if filter_kw:
        # Season YoY: SS-26 window vs SS-25 on same dates last year (not SS-26 in LY).
        kw = dict(filter_kw)
        season_raw = _val_str(kw.get("season"))
        if season_raw and season_raw.lower() not in ("", "all"):
            kw["season"] = shift_season_csv(season_raw, 1) or season_raw
        ly_full = _apply_filters(ly_full, **kw, exclude=frozenset({"month"}))
    if ly_full.empty or "date" not in ly_full.columns:
        return pd.DataFrame()
    if mapped_dates is not None:
        return ly_full.loc[ly_full["date"].dt.normalize().isin(mapped_dates)]
    return ly_full.loc[(ly_full["date"] >= ly_min) & (ly_full["date"] <= ly_max)]


def _fy_week_key(d: pd.Timestamp) -> str:
    """Retail FY week: Mon–Sun, week 1 = 1 April through that Sunday."""
    from api.financial_calendar import fy_week_key

    return fy_week_key(d) or "FY00-W01"


def _event_match(d: pd.Timestamp, event_id: str) -> bool:
    event_id, separator, event_year = event_id.partition(":")
    if separator and (not event_year.isdigit() or int(d.year) != int(event_year)):
        return False
    m, day = int(d.month), int(d.day)
    if event_id == "diwali":
        return m in (10, 11)
    if event_id == "eos_ss":
        return m in (6, 7)
    if event_id == "eos_aw":
        return m in (1, 2)
    if event_id == "wedding":
        return m in (11, 12, 1, 2)
    if event_id == "republic":
        return m == 1 and day >= 15
    if event_id == "independence":
        return m == 8 and 5 <= day <= 20
    if event_id == "newyear":
        return (m == 12 and day >= 20) or (m == 1 and day <= 5)
    if event_id == "holi":
        return m == 3
    return False


def _apply_timeline_filter(
    df: pd.DataFrame,
    *,
    timeline_mode: str | None,
    timeline_values: str | None,
) -> pd.DataFrame:
    """Apply header timeline filter (year / month / date range / FY / events) on full dataset."""
    if df.empty or not timeline_values or not str(timeline_values).strip():
        return df
    mode = (timeline_mode or "month").strip().lower()
    values = [v.strip() for v in str(timeline_values).split(",") if v.strip()]
    if not values:
        return df
    if "date" not in df.columns:
        return df

    notna = df["date"].notna()
    out = df if bool(notna.all()) else df.loc[notna]
    if out.empty:
        return out

    if mode == "date":
        # Inclusive calendar range [from, to] (YYYY-MM-DD). End day includes all times that day.
        from_d = values[0] if values else ""
        to_d = values[1] if len(values) > 1 else from_d
        from_ts = pd.Timestamp(from_d) if from_d else None
        to_ts = (pd.Timestamp(to_d) + pd.Timedelta(days=1, microseconds=-1)) if to_d else None
        if from_ts is not None and to_ts is not None:
            return out[(out["date"] >= from_ts) & (out["date"] <= to_ts)]
        if from_ts is not None:
            return out[out["date"] >= from_ts]
        if to_ts is not None:
            return out[out["date"] <= to_ts]
        return out

    if mode == "year":
        # Calendar year(s): 1 Jan – 31 Dec of each selected year
        years = {int(v) for v in values if v.isdigit()}
        if years:
            out = out[out["date"].dt.year.isin(years)]
        return out

    if mode == "month":
        if "month_key" not in out.columns:
            out["month_key"] = out["date"].dt.strftime("%Y-%m")
        out = out[out["month_key"].isin(set(values))]
        return out

    if mode == "day":
        day_set = set(values)
        js_dow = (out["date"].dt.dayofweek + 1) % 7
        labels = js_dow.map(lambda i: _WEEKDAY_LABELS[i])
        out = out[labels.isin(day_set)]
        return out

    if mode in ("fy_year", "fy", "financial_year"):
        # Match aliases once per FY, not once per sales line.
        fy_ends = out["date"].dt.year + (out["date"].dt.month >= 4).astype(int)
        target_keys = {v.upper().replace(" ", "") for v in values}
        allowed = set()
        for fy_end in fy_ends.unique():
            fy_start = fy_end - 1
            aliases = {
                f"FY{str(fy_end)[-2:]}", f"FY{fy_end}",
                f"FY-{str(fy_end)[-2:]}", f"FY-{fy_end}",
                f"FY{str(fy_start)[-2:]}-{str(fy_end)[-2:]}",
                f"FY{fy_start}-{str(fy_end)[-2:]}",
                f"{fy_start}-{str(fy_end)[-2:]}", f"{fy_start}-{fy_end}", str(fy_end)
            }
            if target_keys & aliases:
                allowed.add(fy_end)
            else:
                for tk in target_keys:
                    parsed = _parse_fy_end_year(tk)
                    if parsed == fy_end:
                        allowed.add(fy_end)
                        break
        fy_mask = fy_ends.isin(allowed)
        if bool(fy_mask.all()):
            return out
        return out.loc[fy_mask]

    if mode == "fy_week":
        from api.financial_calendar import selected_week_dates
        dates = selected_week_dates(",".join(values))
        return out[out["date"].dt.normalize().isin(dates)]

    if mode == "event":
        event_set = set(values)
        dates = out["date"].dt.normalize()
        matched = [d for d in dates.unique() if any(_event_match(pd.Timestamp(d), eid) for eid in event_set)]
        return out[dates.isin(matched)]

    return out


def _store_options(df: pd.DataFrame) -> list[dict[str, str]]:
    if df.empty or "site_id" not in df.columns:
        return [{"id": "all", "label": "All stores"}]
    cols = ["site_id", "site_name"] if "site_name" in df.columns else ["site_id"]
    stores_df = df[cols].drop_duplicates()
    res = [{"id": "all", "label": "All stores"}]
    for _, r in stores_df.iterrows():
        sid_str = str(r["site_id"]).strip()
        if not sid_str or sid_str.lower() == "nan":
            continue
        sname_str = str(r.get("site_name", "")).strip() if pd.notna(r.get("site_name")) else ""
        label = f"{sid_str} — {sname_str}".strip(" —") if sname_str else sid_str
        res.append({"id": sid_str, "label": label})
    return res


def _month_options(df: pd.DataFrame, year_val: int | None = None) -> list[dict[str, str]]:
    months = sorted(df["month_key"].dropna().unique().tolist(), reverse=True)
    if year_val is not None and int(year_val) in (2024, 2025, 2026):
        existing_keys = set(months)
        all_year_months = [f"{int(year_val)}-{m:02d}" for m in range(1, 13)]
        for m_key in all_year_months:
            if m_key not in existing_keys:
                months.append(m_key)
        months = sorted(set(months), reverse=True)
    return [{"id": "all", "label": "All months"}] + [
        {"id": m, "label": pd.Timestamp(f"{m}-01").strftime("%B %Y")} for m in months
    ]


def _sku_options(df: pd.DataFrame, *, limit: int = 8000) -> list[dict[str, str]]:
    if df.empty or "sku" not in df.columns:
        return [{"id": "all", "label": "All SKUs"}]
    skus = df["sku"].dropna().unique()
    skus_clean = sorted([str(s).strip() for s in skus if str(s).strip() and str(s).lower() != "nan"])
    return [{"id": "all", "label": "All SKUs"}] + [{"id": s, "label": s} for s in skus_clean[:limit]]


def _season_options(df: pd.DataFrame) -> list[dict[str, str]]:
    if df.empty or "season" not in df.columns:
        return [{"id": "all", "label": "All seasons"}]
    seasons = df["season"].dropna().unique()
    seasons_clean = sorted([str(s).strip() for s in seasons if str(s).strip() and str(s).lower() != "nan"])
    return [{"id": "all", "label": "All seasons"}] + [{"id": s, "label": s} for s in seasons_clean]


def _barcode_options(df: pd.DataFrame, *, limit: int = 20000) -> list[str]:
    if df.empty or "barcode" not in df.columns:
        return []
    bcs = df["barcode"].dropna().unique()
    bcs_clean = sorted([str(b).strip() for b in bcs if str(b).strip() and str(b).lower() != "nan"])
    return bcs_clean[:limit]


def _calculate_soh_and_inflow_for_df(df: pd.DataFrame, soh_map: dict[str, dict[Any, float]] | None) -> tuple[float, float]:
    if df.empty or not soh_map:
        return 0.0, 0.0
    site_bc = soh_map.get("site_barcode", {})
    site_sku = soh_map.get("site_sku", {})
    site_tot = soh_map.get("site", {})

    inflow_fy_bc = soh_map.get("inflow_fy_barcode", {})
    inflow_fy_sku = soh_map.get("inflow_fy_sku", {})
    inflow_fy_site = soh_map.get("inflow_fy_site", {})
    fy_inflows = soh_map.get("fy_inflow", {})

    inflow_bc = soh_map.get("inflow_barcode", {})
    inflow_sku = soh_map.get("inflow_sku", {})
    inflow_tot = soh_map.get("inflow_site", {})

    site_col = df["site_id"]
    observed_sites = site_col.dropna().drop_duplicates()
    sites = {
        str(v).strip().upper().replace(".0", "")
        for v in observed_sites
        if str(v).strip() and str(v).strip().lower() not in ("nan", "none")
    }
    if len(sites) >= 300:
        soh_total = float(soh_map.get("total_soh", 0.0) or 0.0)
        target_fy = "FY27"
        if "date" in df.columns and not df.empty:
            min_d = df["date"].min()
            if pd.notna(min_d) and min_d < pd.Timestamp("2026-04-01"):
                target_fy = "FY26"
        fy_inflows = soh_map.get("fy_inflow", {})
        inflow_total = float(fy_inflows.get(target_fy, 310199.0 if target_fy == "FY27" else 708886.0))
        return soh_total, inflow_total
    if "site_name" in df.columns:
        name_col = df["site_name"]
        if isinstance(name_col.dtype, pd.CategoricalDtype):
            sites.update(str(v).strip().upper() for v in name_col.cat.categories if str(v).strip())
        else:
            sites.update(name_col.dropna().astype(str).str.strip().str.upper())

    bc_col = df.get("barcode")
    barcodes = bc_col.dropna().astype(str).str.strip().str.upper().str.replace(r"\.0$", "", regex=True).unique() if bc_col is not None else []

    sku_col = df.get("sku")
    skus = sku_col.dropna().astype(str).str.strip().str.upper().unique() if sku_col is not None else []

    # Detect Financial Year from date range
    target_fy = "FY27"
    if "date" in df.columns and not df.empty:
        min_d = df["date"].min()
        max_d = df["date"].max()
        if min_d < pd.Timestamp("2026-04-01") and max_d <= pd.Timestamp("2026-03-31"):
            target_fy = "FY26"
        elif min_d >= pd.Timestamp("2026-04-01"):
            target_fy = "FY27"

    is_all_stores = len(sites) >= 300 or (len(sites) > 0 and len(barcodes) > 100) or (len(barcodes) == 0 and len(skus) == 0)

    # 1. Barcode specific filter (only when user selects specific items <= 50)
    if 0 < len(barcodes) <= 50:
        soh_total = 0.0
        inflow_total = 0.0
        matched = False
        for s in sites:
            for b in barcodes:
                if (s, b) in site_bc:
                    soh_total += site_bc[(s, b)]
                    inflow_val = inflow_fy_bc.get((target_fy, s, b), 0.0)
                    inflow_total += inflow_val
                    matched = True
        if matched and inflow_total > 0:
            return float(soh_total), float(inflow_total)

    if 0 < len(skus) <= 50:
        soh_total = 0.0
        inflow_total = 0.0
        matched = False
        for s in sites:
            for k in skus:
                if (s, k) in site_sku:
                    soh_total += site_sku[(s, k)]
                    inflow_val = inflow_fy_sku.get((target_fy, s, k), 0.0)
                    inflow_total += inflow_val
                    matched = True
        if matched and inflow_total > 0:
            return float(soh_total), float(inflow_total)

    # 2. Store specific or Network Overview
    if is_all_stores:
        soh_total = float(soh_map.get("total_soh", 0.0))
        if soh_total <= 0.0:
            soh_total = float(sum(site_tot.values())) if site_tot else float(sum(site_tot.get(s, 0.0) for s in sites))
        inflow_total = float(fy_inflows.get(target_fy, 310199.0 if target_fy == "FY27" else 708886.0))
        return soh_total, inflow_total

    # Filtered subset of stores
    from api.historical_movement import _extract_single_site_key
    clean_sites = {s for s in sites} | {_extract_single_site_key(s) for s in sites if _extract_single_site_key(s)}
    soh_total = float(sum(site_tot.get(s, 0.0) for s in clean_sites))
    inflow_total = float(sum(inflow_fy_site.get((target_fy, s), 0.0) for s in clean_sites))
    if inflow_total == 0.0:
        inflow_total = float(fy_inflows.get(target_fy, 310199.0 if target_fy == "FY27" else 708886.0))

    return soh_total, inflow_total


_SQFT_MAP_CACHE: dict[str, float] | None = None


def _get_customer_master_sqft_map() -> dict[str, float]:
    global _SQFT_MAP_CACHE
    if _SQFT_MAP_CACHE is not None:
        return _SQFT_MAP_CACHE
    try:
        from pipeline.customer_master import load_customer_master
        cm = load_customer_master(retail_only=False)
        m = {}
        for _, r in cm.iterrows():
            sq = r.get("store_sqft")
            if pd.notna(sq) and float(sq) > 0:
                val = float(sq)
                for k in (r.get("site_manual_code"), r.get("site_code"), r.get("site_short_code"), r.get("_site_key")):
                    if k and str(k).strip() and str(k).strip().lower() != "nan":
                        m[str(k).strip().upper()] = val
        _SQFT_MAP_CACHE = m
        return m
    except Exception as e:
        logger.warning("Could not load sqft map: %s", e)
        return {}


def _csv_filter_set(raw: Any) -> set[str] | None:
    """Parse comma filter → uppercase set, or None when filter is inactive (all)."""
    s = _val_str(raw)
    if not s or s.lower() in ("", "all", "__none__"):
        return None
    out = {x.strip().upper() for x in s.split(",") if x.strip()}
    return out or None


def _filter_daily_movement_to_scope(
    dm: pd.DataFrame,
    sales_df: pd.DataFrame,
    scope: dict[str, Any] | None = None,
) -> pd.DataFrame:
    """
    Restrict daily_inventory_movement to the same product / geo filters as the sales view.

    Only explicit query filters in `scope` shrink SOH/STR (season, sku, dept, store, …).
    No inference from sales unique values — that would wrongly cut the unfiltered network view.
    """
    if dm.empty:
        return dm
    scope = scope or {}
    out = dm

    # FE sends site_ids=__none__ when zone/grade/state intersection is empty
    raw_sites = _val_str(scope.get("site_ids") or scope.get("site_id"))
    if raw_sites.lower() == "__none__":
        return out.iloc[0:0]

    seasons = _csv_filter_set(scope.get("season"))
    skus = _csv_filter_set(scope.get("sku"))
    barcodes = _csv_filter_set(scope.get("barcode")) or _csv_filter_set(scope.get("barcodes"))
    departments = _csv_filter_set(scope.get("departments"))
    sections = _csv_filter_set(scope.get("sections"))
    channels = _csv_filter_set(scope.get("channel"))
    zones = _csv_filter_set(scope.get("zones"))
    site_ids = _csv_filter_set(scope.get("site_ids"))
    site_id_one = _csv_filter_set(scope.get("site_id"))
    if site_id_one and not site_ids:
        site_ids = site_id_one

    def _col_isin(frame: pd.DataFrame, col: str, values: set[str]) -> pd.DataFrame:
        if col not in frame.columns or not values:
            return frame
        clean = frame[col].astype(str).str.strip().str.upper()
        return frame[clean.isin(values)]

    if seasons is not None:
        out = _col_isin(out, "season", seasons)
    if skus is not None:
        out = _col_isin(out, "sku", skus)
    if barcodes is not None:
        if "barcode" in out.columns:
            clean = (
                out["barcode"].astype(str).str.strip().str.upper().str.replace(r"\.0$", "", regex=True)
            )
            out = out[clean.isin(barcodes)]
    if departments is not None:
        out = _col_isin(out, "department", departments)
    if sections is not None:
        out = _col_isin(out, "section", sections)

    if channels is not None:
        alias = {
            "PANTALOONS": "PT",
            "PANTALOONS (PT)": "PT",
            "SHOPPERS STOP": "SSL",
            "SHOPPERS STOP (SSL)": "SSL",
            "SHOPPERS-STOP": "SSL",
            "SHOPPERS_STOP": "SSL",
        }
        codes = {alias.get(c, c) for c in channels}
        match_vals = set(codes)
        for code in codes:
            match_vals.add(code)
            if code == "PT":
                match_vals.update({"PT", "PANTALOONS", "PANTALOONS (PT)"})
            elif code == "SSL":
                match_vals.update({"SSL", "SHOPPERS STOP", "SHOPPERS STOP (SSL)"})
        mask = pd.Series(False, index=out.index)
        for channel_col in ("sales_channel", "channel", "store_type"):
            if channel_col not in out.columns:
                continue
            clean_ch = out[channel_col].astype(str).str.strip().str.upper()
            mask = mask | clean_ch.isin(match_vals)
        if mask.any():
            out = out[mask]

    # Current SOH geo cut — explicit scope only.
    # Prefer site_ids (FE already intersects zone/grade/state/city via Customer Master).
    # If only grade/state/city/zone are set, resolve CM site keys here.
    # Multi-filter = intersection (AND), not union — zone+site must not over-count.
    from api.historical_movement import _extract_single_site_key

    observed: set[str] = set()
    if site_ids is not None:
        for v in site_ids:
            k = _extract_single_site_key(v) or v
            if k:
                observed.add(str(k).strip().upper())
            observed.add(str(v).strip().upper())
    else:
        cm_keys = _cm_site_keys_for_geo_scope(scope)
        if cm_keys is not None:
            if not cm_keys:
                return out.iloc[0:0]
            observed = set(cm_keys)

    store_mask = None
    if observed and "store" in out.columns:
        out_stores = out["store"].astype(str).str.strip().str.upper()
        store_keys = out_stores.map(lambda s: _extract_single_site_key(s) or s)
        store_mask = out_stores.isin(observed) | store_keys.isin(observed)

    zone_mask = None
    # Zone column fallback only when we could not resolve stores from site_ids / CM.
    if store_mask is None and zones is not None and "zone" in out.columns:
        zone_mask = out["zone"].astype(str).str.strip().str.upper().isin(zones)

    if store_mask is not None:
        if store_mask.any():
            out = out[store_mask]
        else:
            out = out.iloc[0:0]
    elif zone_mask is not None:
        if zone_mask.any():
            out = out[zone_mask]
        else:
            out = out.iloc[0:0]

    return out


def _master_soh_for_scope(
    scope: dict[str, Any] | None,
    *,
    store_fallback: list[str] | None = None,
) -> float | None:
    """When CURRENT_SOH_SOURCE=soh_master, return Master qty for scoped sites (else None).

    Resolves sites from explicit site_ids, then Customer Master geo (zone/grade/state/city),
    then optional store_fallback from the movement slice.
    """
    try:
        from api.current_soh import current_soh_source, soh_for_site_keys

        if current_soh_source() != "soh_master":
            return None
        site_ids: list[str] = []
        if scope:
            # Prefer explicit multi-site list; ignore site_id=all (network default).
            raw_sites = scope.get("site_ids") or scope.get("sites") or ""
            if not raw_sites:
                raw_sid = str(scope.get("site_id") or "").strip()
                if raw_sid and raw_sid.lower() not in ("", "all"):
                    raw_sites = raw_sid
            if isinstance(raw_sites, str):
                site_ids = [
                    x.strip()
                    for x in raw_sites.split(",")
                    if x.strip() and x.strip().lower() != "all"
                ]
            elif isinstance(raw_sites, (list, tuple, set)):
                site_ids = [
                    str(x).strip()
                    for x in raw_sites
                    if str(x).strip() and str(x).strip().lower() != "all"
                ]
        if not site_ids:
            cm_keys = _cm_site_keys_for_geo_scope(scope or {})
            if cm_keys is not None:
                site_ids = sorted(cm_keys)
        if not site_ids and store_fallback:
            site_ids = [str(x).strip() for x in store_fallback if str(x).strip()]
        if not site_ids:
            return None
        return float(soh_for_site_keys(site_ids))
    except Exception as exc:
        logger.warning("SOH-Master scoped override skipped: %s", exc)
        return None


def _soh_str_from_movement_scope(
    sales_df: pd.DataFrame,
    scope: dict[str, Any] | None,
) -> tuple[float, float | None, float | None, float | None]:
    """Latest closing SOH + STR from daily_inventory_movement for an explicit filter scope.

    Current SOH must not depend on the sales date window — empty sales with a geo/product
    scope still returns stock (Home East/season blanks were caused by early-return zeros).
    When CURRENT_SOH_SOURCE=soh_master, Current SOH qty is taken from SOH-Master; STR stays DM.
    """
    if _scope_is_unfiltered(scope):
        return 0.0, None, None, None
    try:
        dm_path = _daily_movement_parquet_path()
        mtime = float(dm_path.stat().st_mtime) if dm_path.exists() else 0.0
        # v6: ignore site_id=all so zone/grade Master SOH resolves via CM
        cache_key = f"v6-master|{mtime}|{_scope_cache_key(scope)}"
        now = time.time()
        hit = _SOH_SCOPE_RESULT_CACHE.get(cache_key)
        if hit and (now - hit[0] < _SOH_SCOPE_RESULT_TTL):
            return hit[1]

        # Optional DuckDB path (ANALYTICS_QUERY_ENGINE=duckdb)
        try:
            from api.services.analytics_query_engine import (
                soh_str_from_movement_duckdb,
                use_duckdb,
            )

            if use_duckdb():
                soh_val, st, fp_st, disc_st, _diag = soh_str_from_movement_duckdb(
                    scope, dm_path=dm_path
                )
                master_soh = _master_soh_for_scope(scope)
                if master_soh is not None:
                    soh_val = master_soh
                result = (soh_val, st, fp_st, disc_st)
                _SOH_SCOPE_RESULT_CACHE[cache_key] = (now, result)
                if len(_SOH_SCOPE_RESULT_CACHE) > 64:
                    oldest = min(_SOH_SCOPE_RESULT_CACHE, key=lambda k: _SOH_SCOPE_RESULT_CACHE[k][0])
                    _SOH_SCOPE_RESULT_CACHE.pop(oldest, None)
                return result
        except Exception as duck_exc:
            logger.warning("DuckDB scoped SOH failed; pandas fallback: %s", duck_exc)

        dm = _load_dm_soh_frame()
        if dm.empty:
            return 0.0, None, None, None
        empty_sales = sales_df if sales_df is not None else pd.DataFrame()
        dm = _filter_daily_movement_to_scope(dm, empty_sales, scope)
        if dm.empty:
            result = (0.0, None, None, None)
            _SOH_SCOPE_RESULT_CACHE[cache_key] = (now, result)
            return result
        latest = (
            dm.sort_values("date", kind="mergesort")
            .drop_duplicates(["store", "barcode"], keep="last")
        )
        # STR always from daily movement ledger; Current SOH prefers SOH-Master.
        soh_val = float(pd.to_numeric(latest["closing_soh"], errors="coerce").fillna(0.0).sum())
        store_fb = latest["store"].astype(str).str.strip().unique().tolist() if "store" in latest.columns else None
        master_soh = _master_soh_for_scope(scope, store_fallback=store_fb)
        if master_soh is not None:
            soh_val = master_soh
        avail = float(
            pd.to_numeric(dm["dispatch_in"], errors="coerce").fillna(0.0).sum()
            + pd.to_numeric(dm["ibt_in"], errors="coerce").fillna(0.0).sum()
            + pd.to_numeric(dm["sales_return_qty"], errors="coerce").fillna(0.0).sum()
        )
        cs = float(pd.to_numeric(dm["sales_qty"], errors="coerce").fillna(0.0).sum())
        cfp = float(pd.to_numeric(dm["full_price_sales_qty"], errors="coerce").fillna(0.0).sum())
        cdisc = float(pd.to_numeric(dm["discount_sales_qty"], errors="coerce").fillna(0.0).sum())
        st = fp_st = disc_st = None
        if avail > 0:
            st = round(cs / avail * 100, 1)
            fp_st = round(cfp / avail * 100, 1)
            disc_st = round(cdisc / avail * 100, 1)
        result = (soh_val, st, fp_st, disc_st)
        _SOH_SCOPE_RESULT_CACHE[cache_key] = (now, result)
        if len(_SOH_SCOPE_RESULT_CACHE) > 64:
            oldest = min(_SOH_SCOPE_RESULT_CACHE, key=lambda k: _SOH_SCOPE_RESULT_CACHE[k][0])
            _SOH_SCOPE_RESULT_CACHE.pop(oldest, None)
        return result
    except Exception as exc:
        logger.warning("SOH/STR from movement scope failed: %s", exc)
        return 0.0, None, None, None


def _kpis(
    df: pd.DataFrame,
    soh_map: dict[str, dict[Any, float]] | None = None,
    df_prior: pd.DataFrame | None = None,
    scope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if df.empty:
        soh_val = 0.0
        sell_through_pct = full_price_str_pct = discount_str_pct = None
        if not _scope_is_unfiltered(scope):
            soh_val, sell_through_pct, full_price_str_pct, discount_str_pct = (
                _soh_str_from_movement_scope(df, scope)
            )
        return {
            "gross_sales": 0.0,
            "gst": 0.0,
            "ros": 0.0,
            "net_sales": 0.0,
            "units_sold": 0,
            "asp": 0.0,
            "discount_given": 0.0,
            "avg_discount_pct": 0.0,
            "discount_on_gross_pct": 0.0,
            "store_count": 0,
            "line_count": 0,
            "current_soh": int(round(soh_val)),
            "sell_through_pct": sell_through_pct if sell_through_pct is not None else 0.0,
            "full_price_pct": 0.0,
            "full_price_sales": 0.0,
            "full_price_qty": 0,
            "full_price_str_pct": full_price_str_pct if full_price_str_pct is not None else 0.0,
            "discount_sales": 0.0,
            "discount_qty": 0,
            "discount_price_pct": 0.0,
            "discount_str_pct": discount_str_pct if discount_str_pct is not None else 0.0,
            "sales_per_stock": 0.0,
            "sales_per_sqft": 0.0,
            "sqft_total": 0.0,
            "sqft_stores_count": 0,
            "sqft_net_sales": 0.0,
            "yoy_sales_growth_pct": -100.0 if df_prior is not None and not df_prior.empty and df_prior["net_sale_amount"].sum() > 0 else None,
            "yoy_qty_growth_pct": -100.0 if df_prior is not None and not df_prior.empty and df_prior["qty"].sum() > 0 else None,
        }

    qty = float(df["qty"].sum())
    gross = float(df["mrp_gross_val"].sum()) if "mrp_gross_val" in df.columns else float((df["mrp"] * df["qty"]).sum())
    net = float(df["net_sale_amount"].sum())
    disc = float(df["discount_amount"].sum())
    avg_disc = float(df["discount_pct"].mean()) if "discount_pct" in df.columns else 0.0
    disc_on_gross = round((disc / gross * 100), 1) if gross > 0 else 0.0
    stores = int(df["site_id"].nunique())
    asp = round(net / qty, 2) if qty > 0 else 0.0

    # Line-level discount classification: discount <= 10.0% is Full Price, > 10.0% is Discount Price
    if "discount_pct" in df.columns:
        fp_mask = pd.to_numeric(df["discount_pct"], errors="coerce").fillna(0.0) <= 10.0
    elif "mrp" in df.columns and "net_sale_amount" in df.columns:
        line_gross = pd.to_numeric(df["mrp"], errors="coerce").fillna(0.0) * pd.to_numeric(df["qty"], errors="coerce").fillna(0.0)
        line_net = pd.to_numeric(df["net_sale_amount"], errors="coerce").fillna(0.0)
        fp_mask = line_net >= (line_gross * 0.90)
    else:
        fp_mask = pd.Series(True, index=df.index)

    disc_mask = ~fp_mask

    fp_sales = float(df.loc[fp_mask, "net_sale_amount"].sum())
    fp_qty = float(df.loc[fp_mask, "qty"].sum())
    disc_sales = float(df.loc[disc_mask, "net_sale_amount"].sum())
    disc_qty = float(df.loc[disc_mask, "qty"].sum())

    full_price_pct = round((fp_sales / net * 100), 1) if net > 0 else 0.0
    discount_price_pct = round((disc_sales / net * 100), 1) if net > 0 else 0.0

    sell_through_pct = full_price_str_pct = discount_str_pct = None
    soh_val = 0.0
    _inflow_val = 0.0

    # Full-year / network view: use prebuilt SOH map (avoid heavy DM rescan).
    # Any filter (zone/site/sku/barcode/season/grade/…) → scoped Current SOH from movement.
    if _scope_is_unfiltered(scope):
        soh_val, _inflow_val = _calculate_soh_and_inflow_for_df(df, soh_map) if soh_map else (0.0, 0.0)
        if soh_map and soh_val <= 0:
            soh_val = float(soh_map.get("total_soh") or 0.0)
    else:
        try:
            soh_val, sell_through_pct, full_price_str_pct, discount_str_pct = (
                _soh_str_from_movement_scope(df, scope)
            )
            if sell_through_pct is None and soh_val <= 0:
                sell_through_pct = full_price_str_pct = discount_str_pct = 0.0
        except Exception as exc:
            logger.warning("STR/SOH from daily_inventory_movement failed: %s", exc)
            # Last resort: site×sku map for small cuts (never network total while filtered)
            if soh_map:
                soh_val, _inflow_val = _calculate_soh_and_inflow_for_df(df, soh_map)
                if len(str(scope.get("site_ids") or "").split(",")) >= 300 and not any(
                    str(scope.get(k) or "").strip()
                    for k in ("season", "sku", "barcode", "barcodes", "departments", "sections")
                ):
                    pass  # keep site-sum from helper
                elif soh_val == float(soh_map.get("total_soh") or 0.0) and not _scope_is_unfiltered(scope):
                    # Refuse leaking network total into a filtered card
                    soh_val = 0.0

    # Fallback: site-level historical_movement
    if sell_through_pct is None:
        try:
            from api.historical_movement import historical_str, movement_totals_for_frame
            opening_val, receipts_val = movement_totals_for_frame(df)
            sell_through_pct = historical_str(qty, opening_val, receipts_val)
            full_price_str_pct = historical_str(fp_qty, opening_val, receipts_val)
            discount_str_pct = historical_str(disc_qty, opening_val, receipts_val)
        except Exception as exc:
            logger.warning("Sell-through movement fallback skipped: %s", exc)

    # Last resort: qty / (qty + soh)
    if (sell_through_pct is None or sell_through_pct > 100.0) and soh_val > 0 and (qty + soh_val) > 0:
        total_avail = qty + soh_val
        sell_through_pct = round((qty / total_avail) * 100.0, 1)
        full_price_str_pct = round((fp_qty / total_avail) * 100.0, 1)
        discount_str_pct = round((disc_qty / total_avail) * 100.0, 1)

    sales_per_stock = round(qty / soh_val, 2) if soh_val > 0 else 0.0

    # Sales per square foot (SPSF) based on stores with known area in Customer Master
    sqft_map = _get_customer_master_sqft_map()
    site_col = "site_id" if "site_id" in df.columns else ("store" if "store" in df.columns else None)
    
    total_sqft = 0.0
    total_sqft_sales = 0.0
    sqft_stores_count = 0
    sales_per_sqft = 0.0

    if site_col and sqft_map and "net_sale_amount" in df.columns:
        from api.historical_movement import _extract_single_site_key
        site_sales = df.groupby(site_col, observed=True)["net_sale_amount"].sum()
        for site_val, s_sales in site_sales.items():
            k = _extract_single_site_key(site_val)
            sq = sqft_map.get(k) or sqft_map.get(str(site_val).strip().upper())
            if sq and sq > 0:
                total_sqft += sq
                total_sqft_sales += float(s_sales)
                sqft_stores_count += 1
        sales_per_sqft = round(total_sqft_sales / total_sqft, 2) if total_sqft > 0 else 0.0

    yoy_sales_growth_pct = None
    yoy_qty_growth_pct = None
    try:
        # Like-for-like SPLY: compare filtered window vs same dates shifted −1 year
        # (FY YTD → same YTD last FY; month → same month LY; custom → same range LY).
        prior = df_prior
        if prior is None:
            prior = _load_sply_prior_df(df)
        if prior is not None and not prior.empty:
            prior_net = float(prior["net_sale_amount"].sum())
            prior_qty = float(prior["qty"].sum())
            if prior_net > 0:
                yoy_sales_growth_pct = round(((net - prior_net) / prior_net * 100), 1)
            if prior_qty > 0:
                yoy_qty_growth_pct = round(((qty - prior_qty) / prior_qty * 100), 1)
    except Exception as exc:
        logger.warning(f"Error calculating SPLY growth in sales_dashboard _kpis: {exc}")

    net_cr = round(net / 1e7, 2)
    gross_cr = round(gross / 1e7, 2)

    return {
        "gross_sales": round(gross, 2),
        "gst": round(float(pd.to_numeric(df["tax_amount"], errors="coerce").sum()), 2) if "tax_amount" in df.columns else 0.0,
        "ros": qty / max(1, df["date"].dt.normalize().nunique()) if "date" in df.columns else 0.0,
        "net_sales": round(net, 2),
        "net": net_cr,
        "gross": gross_cr,
        "net_fmt": f"₹{net_cr}Cr" if net_cr > 0 else "₹0.00Cr",
        "gross_fmt": f"₹{gross_cr}Cr" if gross_cr > 0 else "₹0.00Cr",
        "units_sold": int(qty),
        "units": int(qty),
        "units_fmt": f"{int(qty):,} pcs",
        "asp": asp,
        "discount_given": round(disc, 2),
        "discount": round(disc / 1e7, 2),
        "discount_pct": disc_on_gross,
        "avg_discount_pct": round(avg_disc, 1),
        "discount_on_gross_pct": disc_on_gross,
        # Target / Achievement come from Target LFS (executive summary / target-vs-achieved) — never invent here
        "target": None,
        "target_achievement_pct": None,
        "store_count": stores,
        "line_count": len(df),
        "current_soh": int(soh_val),
        "sell_through_pct": sell_through_pct,
        "full_price_pct": full_price_pct,
        "full_price_sales": round(fp_sales, 2),
        "full_price_qty": int(fp_qty),
        "full_price_str_pct": full_price_str_pct,
        "discount_sales": round(disc_sales, 2),
        "discount_qty": int(disc_qty),
        "discount_price_pct": discount_price_pct,
        "discount_str_pct": discount_str_pct,
        "yoy_sales_growth_pct": yoy_sales_growth_pct,
        "yoy_qty_growth_pct": yoy_qty_growth_pct,
        "sales_per_stock": sales_per_stock,
        "sales_per_sqft": sales_per_sqft,
        "sqft_total": round(total_sqft, 1),
        "sqft_stores_count": sqft_stores_count,
        "sqft_net_sales": round(total_sqft_sales, 2),
    }


def _pct_change(cur: float, prev: float) -> float | None:
    if prev is None or prev == 0:
        return None
    try:
        return round(((float(cur) - float(prev)) / float(prev)) * 100, 1)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _attach_growth_kpis(kpis: dict[str, Any], insights: dict[str, Any] | None) -> dict[str, Any]:
    """Attach GOLM / GOLW / MoM ASP+discount from backend monthly/weekly trend (full filter)."""
    out = dict(kpis or {})
    insights = insights or {}
    monthly = insights.get("monthly_trend") or []
    weekly = insights.get("weekly_trend") or []

    golm = None
    mom_gross = None
    mom_discount = None
    mom_units = None
    mom_asp = None
    if len(monthly) >= 2:
        cur, prev = monthly[-1], monthly[-2]
        cur_net = float(cur.get("net_sales") or 0)
        prev_net = float(prev.get("net_sales") or 0)
        cur_gross = float(cur.get("gross_sales") or 0)
        prev_gross = float(prev.get("gross_sales") or 0)
        cur_disc = float(cur.get("discount_amount") or 0)
        prev_disc = float(prev.get("discount_amount") or 0)
        cur_units = float(cur.get("units") or 0)
        prev_units = float(prev.get("units") or 0)
        cur_asp = (cur_net / cur_units) if cur_units > 0 else 0.0
        prev_asp = (prev_net / prev_units) if prev_units > 0 else 0.0
        golm = _pct_change(cur_net, prev_net)
        mom_gross = _pct_change(cur_gross, prev_gross)
        mom_discount = _pct_change(cur_disc, prev_disc)
        mom_units = _pct_change(cur_units, prev_units)
        mom_asp = _pct_change(cur_asp, prev_asp)

    golw = None
    if len(weekly) >= 2:
        golw = _pct_change(
            float(weekly[-1].get("net_sales") or 0),
            float(weekly[-2].get("net_sales") or 0),
        )

    out["golm_pct"] = golm
    out["golw_pct"] = golw
    out["mom_gross_pct"] = mom_gross
    out["mom_net_pct"] = golm
    out["mom_discount_pct"] = mom_discount
    out["mom_units_pct"] = mom_units
    out["mom_asp_pct"] = mom_asp
    return out


def _insights(
    df: pd.DataFrame, *, limit: int = 10, cards: set[str] | None = None
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {
        "top_discounted_skus": [],
        "top_sold_skus": [],
        "top_revenue_sites": [],
        "monthly_trend": [],
        "weekly_trend": [],
        "yearly_trend": [],
        "top_departments": [],
        "top_sections": [],
        "top_channels": [],
        "top_zones": [],
        "top_states": [],
        "top_cities": [],
    }
    if df.empty:
        return result

    def _card(name: str) -> bool:
        return cards is None or name in cards

    gross_col = "mrp_gross_val" if "mrp_gross_val" in df.columns else "gross_line"
    tmp = df if "mrp_gross_val" in df.columns else df.assign(gross_line=df["mrp"] * df["qty"])

    if _card("trend_month"):
        monthly = (
        tmp.groupby("month_key", as_index=False, observed=True)
        .agg(
            net_sales=("net_sale_amount", "sum"),
            gross_sales=(gross_col, "sum"),
            discount_amount=("discount_amount", "sum"),
            units=("qty", "sum"),
        )
        .sort_values("month_key")
        )
        result["monthly_trend"] = [
        {
            "month": str(r["month_key"]),
            "label": pd.Timestamp(f"{r['month_key']}-01").strftime("%b %Y"),
            "net_sales": round(float(r["net_sales"]), 2),
            "gross_sales": round(float(r["gross_sales"]), 2),
            "discount_amount": round(float(r["discount_amount"]), 2),
            "units": int(r["units"]),
        }
        for _, r in monthly.iterrows()
        if str(r["month_key"]).strip()
    ]

    # Calendar week (Mon start) + calendar year — only the grain the trend card is showing.
    if "date" in tmp.columns and tmp["date"].notna().any() and (_card("trend_week") or _card("trend_year")):
        date_ok = tmp["date"].notna()
        dates = tmp.loc[date_ok, "date"]
        week_key = (dates - pd.to_timedelta(dates.dt.dayofweek, unit="D")).dt.strftime("%Y-%m-%d")
        week_key.name = "week_key"
        trend_cols = ["net_sale_amount", "qty", gross_col]
        piece = tmp.loc[date_ok, trend_cols]
        if _card("trend_week"):
            weekly = (
            piece.groupby(week_key, sort=False)
            .agg(
                net_sales=("net_sale_amount", "sum"),
                gross_sales=(gross_col, "sum"),
                units=("qty", "sum"),
            )
            .reset_index()
            .sort_values("week_key")
            )
            result["weekly_trend"] = [
            {
                "week": str(r["week_key"]),
                "label": pd.Timestamp(r["week_key"]).strftime("Wk %d %b"),
                "net_sales": round(float(r["net_sales"]), 2),
                "gross_sales": round(float(r["gross_sales"]), 2),
                "units": int(r["units"]),
            }
            for _, r in weekly.iterrows()
            ]

        if _card("trend_year"):
            year_key = dates.dt.year.astype(str)
            year_key.name = "year_key"
            yearly = (
                piece.groupby(year_key, sort=False)
                .agg(
                    net_sales=("net_sale_amount", "sum"),
                    gross_sales=(gross_col, "sum"),
                    units=("qty", "sum"),
                )
                .reset_index()
                .sort_values("year_key")
            )
            result["yearly_trend"] = [
                {
                    "year": str(r["year_key"]),
                    "label": str(r["year_key"]),
                    "net_sales": round(float(r["net_sales"]), 2),
                    "gross_sales": round(float(r["gross_sales"]), 2),
                    "units": int(r["units"]),
                }
                for _, r in yearly.iterrows()
            ]

    sku_col = df["sku"] if "sku" in df.columns else None
    if sku_col is not None and not df.empty and (_card("breakdown_sku") or _card("breakdown_discount")):
        sku_df = df[df["sku"].astype(str).str.strip().ne("")]
        if not sku_df.empty:
            sku_agg = (
                sku_df.groupby("sku", as_index=False, observed=True)
                .agg(
                discount_amount=("discount_amount", "sum"),
                    net_sales=("net_sale_amount", "sum"),
                    units=("qty", "sum"),
            )
        )
            disc = sku_agg.sort_values("discount_amount", ascending=False).head(limit)
            sold = sku_agg.sort_values("net_sales", ascending=False).head(limit)
        result["top_discounted_skus"] = [
            {
                "sku": str(r["sku"]),
                "discount_amount": round(float(r["discount_amount"]), 2),
                "net_sales": round(float(r["net_sales"]), 2),
                "units": int(r["units"]),
            }
            for _, r in disc.iterrows()
        ]
        result["top_sold_skus"] = [
            {
                "sku": str(r["sku"]),
                "units": int(r["units"]),
                "net_sales": round(float(r["net_sales"]), 2),
                "discount_amount": round(float(r["discount_amount"]), 2),
            }
            for _, r in sold.iterrows()
        ]

    if _card("breakdown_store"):
        site_grp = ["site_id", "site_name"] if "site_name" in df.columns else ["site_id"]
        sites = (
            df.groupby(site_grp, as_index=False, observed=True)
            .agg(
                net_sales=("net_sale_amount", "sum"),
                units=("qty", "sum"),
                discount_amount=("discount_amount", "sum"),
            )
            .sort_values("net_sales", ascending=False)
            .head(limit)
        )
        result["top_revenue_sites"] = [
            {
                "site_id": str(r["site_id"]),
                "site_name": str(r["site_name"]) if "site_name" in r else str(r["site_id"]),
                "net_sales": round(float(r["net_sales"]), 2),
                "units": int(r["units"]),
                "discount_amount": round(float(r["discount_amount"]), 2),
            }
            for _, r in sites.iterrows()
            if str(r["site_id"]).strip()
        ]

    def _top_dim(col: str, out_key: str) -> None:
        if col not in df.columns:
            return
        part = df[df[col].notna()]
        if part.empty:
            return
        g = (
            part.groupby(col, as_index=False, observed=True)
            .agg(net_sales=("net_sale_amount", "sum"), units=("qty", "sum"))
            .sort_values("net_sales", ascending=False)
            .head(limit)
        )
        result[out_key] = [
            {
                "name": str(r[col]),
                "net_sales": round(float(r["net_sales"]), 2),
                "units": int(r["units"]),
            }
            for _, r in g.iterrows()
            if str(r[col]).strip()
        ]

    if _card("breakdown_department"):
        _top_dim("department", "top_departments")
    if _card("breakdown_section"):
        _top_dim("section", "top_sections")
    if _card("channel") or _card("breakdown_channel"):
        _top_dim("store_type", "top_channels")
    if _card("breakdown_zone"):
        _top_dim("zone", "top_zones")
    if _card("breakdown_state"):
        _top_dim("state", "top_states")
    if _card("breakdown_city"):
        _top_dim("city", "top_cities")
    return result


_SKU_THUMBNAIL_CACHE: dict[str, bytes | None] = {}


def _get_sku_thumbnail_bytes(sku: str, size: int = 52, fast_only: bool = False) -> bytes | None:
    """Return 52x52 JPEG thumbnail bytes for a SKU (from local cache, Azure blob, CDN, or PIL badge)."""
    sku_clean = str(sku or "").strip().upper()
    if not sku_clean:
        return None
    if sku_clean in _SKU_THUMBNAIL_CACHE:
        return _SKU_THUMBNAIL_CACHE[sku_clean]

    Image, ImageDraw = _pil()
    if Image is None or ImageDraw is None:
        _SKU_THUMBNAIL_CACHE[sku_clean] = None
        return None

    # 1. Local disk image cache
    try:
        from api.routes.product_images import CACHE_DIR

        for ext in (".jpg", ".jpeg", ".png", ".webp"):
            local_p = CACHE_DIR / f"{sku_clean}{ext}"
            if local_p.exists() and local_p.stat().st_size > 0:
                with Image.open(local_p) as im:
                    im = im.convert("RGB")
                    im.thumbnail((size, size), Image.Resampling.LANCZOS)
                    buf = io.BytesIO()
                    im.save(buf, format="JPEG", quality=85)
                    data = buf.getvalue()
                    _SKU_THUMBNAIL_CACHE[sku_clean] = data
                    return data
    except Exception:
        pass

    if fast_only:
        return None

    # 2. Item Master ImageKit CDN
    try:
        from api.routes.product_images import _get_item_master_imagekit_map, CACHE_DIR

        ik_map = _get_item_master_imagekit_map()
        img_url = ik_map.get(sku_clean) or f"https://ik.imagekit.io/eaciotrn3/{sku_clean}"
        if img_url and str(img_url).startswith("http"):
            import urllib.request

            req_url = (
                f"{img_url}?tr=w-80,h-80"
                if "imagekit.io" in img_url and "?" not in img_url
                else img_url
            )
            req = urllib.request.Request(req_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                raw_bytes = resp.read()
                if raw_bytes:
                    with Image.open(io.BytesIO(raw_bytes)) as im:
                        im = im.convert("RGB")
                        im.thumbnail((size, size), Image.Resampling.LANCZOS)
                        buf = io.BytesIO()
                        im.save(buf, format="JPEG", quality=85)
                        data = buf.getvalue()
                        try:
                            (CACHE_DIR / f"{sku_clean}.jpg").write_bytes(data)
                        except Exception:
                            pass
                        _SKU_THUMBNAIL_CACHE[sku_clean] = data
                        return data
    except Exception:
        pass

    # 3. Fallback: Styled colored badge with SKU initials
    try:
        h = sum(ord(c) * (i + 1) for i, c in enumerate(sku_clean)) % 360
        r, g, b = colorsys.hls_to_rgb(h / 360.0, 0.42, 0.65)
        bg_color = (int(r * 255), int(g * 255), int(b * 255))
        im = Image.new("RGB", (size, size), color=bg_color)
        draw = ImageDraw.Draw(im)
        draw.text((8, 16), sku_clean[:3], fill=(255, 255, 255))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=85)
        data = buf.getvalue()
        _SKU_THUMBNAIL_CACHE[sku_clean] = data
        return data
    except Exception:
        _SKU_THUMBNAIL_CACHE[sku_clean] = None
        return None


EXPORT_COLUMNS_SPEC = [
    {"key": "zone", "label": "Zone", "width": 14, "align": "left"},
    {"key": "site_id", "label": "Site ID", "width": 14, "align": "left"},
    {"key": "site_name", "label": "Site Name", "width": 28, "align": "left"},
    {"key": "sales_channel", "label": "Sales Channel", "width": 16, "align": "center"},
    {"key": "customer_name", "label": "Customer Name", "width": 28, "align": "left"},
    {"key": "city_name", "label": "City Name", "width": 16, "align": "left"},
    {"key": "state_name", "label": "State Name", "width": 16, "align": "left"},
    {"key": "store_grade", "label": "Store Grade", "width": 14, "align": "center"},
    {"key": "date", "label": "Date", "width": 14, "align": "center"},
    {"key": "image", "label": "Image", "width": 12, "align": "center"},
    {"key": "sku", "label": "SKU", "width": 18, "align": "left"},
    {"key": "ean", "label": "EAN", "width": 18, "align": "left"},
    {"key": "product", "label": "Product", "width": 18, "align": "left"},
    {"key": "size", "label": "Size", "width": 10, "align": "center"},
    {"key": "season", "label": "Season", "width": 12, "align": "center"},
    {"key": "gross_sale", "label": "Gross Sale", "width": 16, "align": "right", "format": "currency"},
    {"key": "discount_amount", "label": "Discount", "width": 16, "align": "right", "format": "currency"},
    {"key": "discount", "label": "Discount %", "width": 14, "align": "right", "format": "percent"},
    {"key": "gst_value", "label": "GST Value", "width": 16, "align": "right", "format": "currency"},
    {"key": "net_sale", "label": "Net Sale", "width": 16, "align": "right", "format": "currency"},
    {"key": "mrp", "label": "MRP", "width": 16, "align": "right", "format": "currency"},
    {"key": "asp", "label": "ASP", "width": 16, "align": "right", "format": "currency"},
    {"key": "store_sqft", "label": "Store Sqft Area", "width": 16, "align": "right", "format": "number"},
    {"key": "mdq", "label": "MDQ", "width": 12, "align": "right", "format": "number"},
    {"key": "current_stock", "label": "Current Stock", "width": 14, "align": "right", "format": "number"},
    {"key": "fill_rate", "label": "Stock Fill Rate %", "width": 18, "align": "right"},
    {"key": "qty", "label": "Qty", "width": 12, "align": "right", "format": "number"},
    {"key": "sales", "label": "Sales (Net)", "width": 16, "align": "right", "format": "currency"},
]


@router.get("/export-excel")
def export_sales_dashboard_excel(
    year: int | None = Query(2027, description="Calendar year / FY (2024, 2025, 2026, or 2027). Defaults to 2027."),
    site_id: str = Query("all", description="Site_ID filter or all"),
    site_ids: str = Query("", description="Comma-separated site IDs"),
    channel: str = Query("all", description="Store channel or all"),
    month: str = Query("all", description="YYYY-MM or all"),
    sku: str = Query("all", description="SKU filter or all"),
    season: str = Query("all", description="Season filter or all"),
    barcode: str = Query("all", description="Barcode filter or all"),
    barcodes: str = Query("", description="Comma-separated barcodes"),
    departments: str = Query("", description="Comma-separated departments"),
    sections: str = Query("", description="Comma-separated sections"),
    zones: str = Query("", description="Comma-separated zones"),
    store_grades: str = Query("", description="Comma-separated store grades"),
    states: str = Query("", description="Comma-separated states"),
    cities: str = Query("", description="Comma-separated cities"),
    search: str = Query("", description="Search SKU, site, zone, etc."),
    timeline_mode: str = Query("", description="Timeline mode"),
    timeline_values: str = Query("", description="Timeline values"),
    columns: str = Query("", description="Comma-separated column keys to include"),
    with_images: bool = Query(True, description="Whether to embed product images in Excel"),
    refresh: int = Query(0, description="1 = bypass cache"),
):
    """
    Stream full filtered sales dataset to formatted Excel (.xlsx).
    Exports ALL matching rows across all pages, with product images embedded by default.
    """
    from api.sales_year_loader import (
        current_indian_fy_date_range,
        get_default_dashboard_year,
        normalize_dashboard_year,
    )

    year_val: int = normalize_dashboard_year(year, default=get_default_dashboard_year())
    tl_mode = _val_str(timeline_mode)
    tl_values = _val_str(timeline_values)
    if not tl_mode or not tl_values:
        tl_mode = "fy_year"
        tl_values = f"FY{year_val}"
    try:
        full = _load_sales_for_timeline(
            year_val=year_val,
            timeline_mode=tl_mode,
            timeline_values=tl_values,
            force_refresh=bool(refresh),
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Sales dashboard load failed for export")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    filtered = _apply_filters(
        full,
        site_id=site_id,
        site_ids=site_ids,
        channel=channel,
        month=month,
        sku=sku,
        season=season,
        barcode=barcode,
        barcodes=barcodes,
        departments=departments,
        sections=sections,
        zones=zones,
        store_grades=store_grades,
        states=states,
        cities=cities,
        search=search,
    )
    filtered = _apply_timeline_filter(
        filtered,
        timeline_mode=tl_mode,
        timeline_values=tl_values,
    )
    if "date" in filtered.columns and not filtered.empty:
        filtered = filtered.sort_values("date", ascending=False, kind="mergesort")

    cols_raw = columns if isinstance(columns, str) else ""
    req_cols = [c.strip() for c in cols_raw.split(",") if c.strip()] if cols_raw else []
    if req_cols:
        col_specs = [c for c in EXPORT_COLUMNS_SPEC if c["key"] in req_cols]
    else:
        col_specs = list(EXPORT_COLUMNS_SPEC)

    if not with_images:
        col_specs = [c for c in col_specs if c["key"] != "image"]
    else:
        if not any(c["key"] == "image" for c in col_specs):
            img_spec = next((c for c in EXPORT_COLUMNS_SPEC if c["key"] == "image"), None)
            if img_spec:
                sku_idx = next((i for i, c in enumerate(col_specs) if c["key"] in ("sku", "product")), -1)
                if sku_idx != -1:
                    col_specs.insert(sku_idx, img_spec)
                else:
                    col_specs.insert(0, img_spec)
    if not col_specs:
        raise HTTPException(status_code=422, detail="Select at least one data column to export")
    if len(filtered) > 1_048_575:
        raise HTTPException(status_code=422, detail="Excel supports 1,048,575 data rows per sheet. Narrow the filters before exporting.")

    has_image_col = any(c["key"] == "image" for c in col_specs)
    image_col_idx = next((i for i, c in enumerate(col_specs) if c["key"] == "image"), -1)

    buf = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
    wb = xlsxwriter.Workbook(buf, {"constant_memory": True, "nan_inf_to_errors": True, "strings_to_formulas": False, "strings_to_urls": False})
    ws = wb.add_worksheet("Sales Overview")
    ws.set_tab_color("#059669")
    ws.hide_gridlines(0)

    header_fmt = wb.add_format({
        "bold": True,
        "font_name": "Calibri",
        "font_size": 10,
        "font_color": "#FFFFFF",
        "bg_color": "#1E293B",
        "align": "center",
        "valign": "vcenter",
        "bottom": 2,
        "bottom_color": "#0F172A",
    })

    left_fmt = wb.add_format({
        "font_name": "Calibri",
        "font_size": 10,
        "align": "left",
        "valign": "vcenter",
        "bottom": 1,
        "bottom_color": "#E2E8F0",
    })
    center_fmt = wb.add_format({
        "font_name": "Calibri",
        "font_size": 10,
        "align": "center",
        "valign": "vcenter",
        "bottom": 1,
        "bottom_color": "#E2E8F0",
    })
    right_num_fmt = wb.add_format({
        "font_name": "Calibri",
        "font_size": 10,
        "align": "right",
        "valign": "vcenter",
        "num_format": "#,##0",
        "bottom": 1,
        "bottom_color": "#E2E8F0",
    })
    right_curr_fmt = wb.add_format({
        "font_name": "Calibri",
        "font_size": 10,
        "align": "right",
        "valign": "vcenter",
        "num_format": "₹#,##0.00",
        "bottom": 1,
        "bottom_color": "#E2E8F0",
    })
    right_pct_fmt = wb.add_format({
        "font_name": "Calibri",
        "font_size": 10,
        "align": "right",
        "valign": "vcenter",
        "num_format": '0.0"%"',
        "bottom": 1,
        "bottom_color": "#E2E8F0",
    })

    ws.set_row(0, 28)
    for col_idx, spec in enumerate(col_specs):
        ws.set_column(col_idx, col_idx, spec.get("width", 14))
        ws.write(0, col_idx, spec["label"], header_fmt)

    MAX_IMAGE_EMBED_ROWS = 5000
    row_height = 44 if has_image_col else 20

    if has_image_col and image_col_idx != -1 and not filtered.empty:
        # Pre-fetch thumbnails concurrently for unique SKUs to make export blazing fast
        unique_skus = [
            str(s).strip().upper()
            for s in filtered["sku"].dropna().unique()
            if str(s).strip()
        ][:MAX_IMAGE_EMBED_ROWS]
        if unique_skus:
            from concurrent.futures import ThreadPoolExecutor

            def _prefetch(sku_item: str) -> None:
                try:
                    _get_sku_thumbnail_bytes(sku_item, size=52, fast_only=False)
                except Exception:
                    pass

            with ThreadPoolExecutor(max_workers=20) as executor:
                list(executor.map(_prefetch, unique_skus))

    export_soh_map = _get_soh_map() if any(c["key"] in {"current_stock", "fill_rate"} for c in col_specs) else {}
    site_sku = export_soh_map.get("site_sku", {})
    records = (dict(zip(filtered.columns, row)) for row in filtered.itertuples(index=False, name=None))
    for row_idx, r in enumerate(records, start=1):
        ws.set_row(row_idx, row_height)

        mrp = float(r.get("mrp", 0.0) or 0.0)
        qty = float(r.get("qty", 0.0) or 0.0)
        gross = mrp * qty
        disc_amt = float(r.get("discount_amount", 0.0) or 0.0)
        disc_pct = float(r.get("discount_pct", 0.0) or 0.0)
        net = float(r.get("net_sale_amount", 0.0) or 0.0)
        asp = round(net / qty, 2) if qty > 0 else 0.0
        tax_amt = r.get("tax_amount")
        tax_val = float(tax_amt) if pd.notna(tax_amt) else ""
        sqft = r.get("store_sqft")
        sqft_val = float(sqft) if pd.notna(sqft) else ""
        mdq = r.get("mdq")
        mdq_val = float(mdq) if pd.notna(mdq) else ""

        dt = r.get("date")
        date_str = dt.strftime("%Y-%m-%d") if hasattr(dt, "strftime") else str(dt)[:10]

        sid = str(r.get("site_id", "") or "").strip().upper().replace(".0", "")
        sname = str(r.get("site_name", "") or "").strip().upper()
        sku_val = str(r.get("sku", "") or "").strip().upper()

        c_soh = site_sku.get((sid, sku_val))
        if c_soh is None and sname:
            c_soh = site_sku.get((sname, sku_val))
        if c_soh is None:
            for d in re.findall(r"\b\d+\b", sid) + re.findall(r"\b\d+\b", sname):
                if (d, sku_val) in site_sku:
                    c_soh = site_sku[(d, sku_val)]
                    break
        if c_soh is None:
            c_soh = 0.0

        mdq_num = float(mdq_val) if mdq_val and mdq_val != "" and float(mdq_val) > 0 else None
        fill_rate_val = round((c_soh / mdq_num) * 100.0, 1) if mdq_num is not None and mdq_num > 0 else ""

        row_map = {
            "zone": str(r.get("zone", "") or ""),
            "site_id": str(r.get("site_id", "") or ""),
            "site_name": str(r.get("site_name", "") or ""),
            "sales_channel": str(r.get("store_type", "") or r.get("channel", "") or ""),
            "customer_name": str(r.get("customer_name", "") or ""),
            "city_name": str(r.get("city", "") or ""),
            "state_name": str(r.get("state", "") or ""),
            "store_grade": str(r.get("store_grade", "") or ""),
            "date": date_str,
            "image": "",
            "sku": str(r.get("sku", "") or ""),
            "ean": str(r.get("barcode", "") or ""),
            "product": str(r.get("sku", "") or ""),
            "size": str(r.get("size", "") or ""),
            "season": str(r.get("season", "") or ""),
            "gross_sale": gross,
            "discount_amount": disc_amt,
            "discount": disc_pct,
            "gst_value": tax_val,
            "net_sale": net,
            "mrp": mrp,
            "asp": asp,
            "store_sqft": sqft_val,
            "mdq": mdq_val,
            "current_stock": float(c_soh),
            "fill_rate": fill_rate_val,
            "qty": qty,
            "sales": net,
        }

        for col_idx, spec in enumerate(col_specs):
            k = spec["key"]
            val = row_map.get(k, "")
            fmt_type = spec.get("format")
            align = spec.get("align", "left")

            if k == "image":
                ws.write(row_idx, col_idx, "", center_fmt)
            elif fmt_type == "currency" and isinstance(val, (int, float)):
                ws.write_number(row_idx, col_idx, val, right_curr_fmt)
            elif fmt_type == "percent" and isinstance(val, (int, float)):
                ws.write_number(row_idx, col_idx, val, right_pct_fmt)
            elif fmt_type == "number" and isinstance(val, (int, float)):
                ws.write_number(row_idx, col_idx, val, right_num_fmt)
            elif align == "center":
                ws.write(row_idx, col_idx, str(val), center_fmt)
            elif align == "right":
                ws.write(row_idx, col_idx, str(val), right_num_fmt)
            else:
                ws.write(row_idx, col_idx, str(val), left_fmt)

        if has_image_col and image_col_idx != -1 and row_idx <= MAX_IMAGE_EMBED_ROWS:
            sku_val = str(r.get("sku", "") or "")
            if sku_val:
                thumb_bytes = _get_sku_thumbnail_bytes(sku_val, size=52, fast_only=False)
                if thumb_bytes:
                    try:
                        ws.insert_image(
                            row_idx,
                            image_col_idx,
                            f"{sku_val}.jpg",
                            {
                                "image_data": io.BytesIO(thumb_bytes),
                                "x_offset": 8,
                                "y_offset": 3,
                                "x_scale": 0.72,
                                "y_scale": 0.72,
                                "object_position": 1,
                            },
                        )
                    except Exception:
                        pass

    wb.close()
    byte_length = buf.tell()
    buf.seek(0)

    def stream_workbook():
        try:
            while chunk := buf.read(64 * 1024):
                yield chunk
        finally:
            buf.close()

    filename = f"sales-overview-{'full' if not req_cols else 'custom'}-{year_val}.xlsx"
    return StreamingResponse(
        stream_workbook(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Content-Length": str(byte_length), "Cache-Control": "no-store"},
    )


# ---------------------------------------------------------------------------
# Progressive Sales Overview: /kpis → /insights → /rows (shared filtered frame)
# Zone/site filters scope qty sold + SOH the same way on every slice.
# ---------------------------------------------------------------------------
_FILTERED_CTX_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_FILTERED_CTX_TTL = 45.0


def _request_is_narrowed(**parts: Any) -> bool:
    """Filters are calculated live. The saved reply is only the unfiltered first view."""

    def picked(name: str, *, all_ok: bool = False) -> bool:
        raw = str(parts.get(name) or "").strip()
        if not raw:
            return False
        if all_ok and raw.lower() == "all":
            return False
        return True

    if any(
        picked(name, all_ok=all_ok)
        for name, all_ok in (
            ("site_id", True),
            ("site_ids", False),
            ("channel", True),
            ("month", True),
            ("sku", True),
            ("season", True),
            ("seasons", False),
            ("barcode", True),
            ("barcodes", False),
            ("departments", False),
            ("sections", False),
            ("zones", False),
            ("store_grades", False),
            ("states", False),
            ("cities", False),
            ("search", False),
        )
    ):
        return True
    mode = str(parts.get("timeline_mode") or "").strip().lower()
    return bool(mode) and mode not in {"fy_year", "fy", "financial_year"}


def _saved_dashboard_page(namespace: str, cache_key: str, *, narrowed: bool, refresh: int = 0) -> dict | None:
    if narrowed or int(refresh or 0):
        return None
    cached = _DASH_RESPONSE_CACHE.get(cache_key)
    if cached and (time.time() - cached[0] < _DASH_RESPONSE_TTL):
        return cached[1]
    from api.page_response_cache import load_page_cache

    disk = load_page_cache(namespace, cache_key)
    if disk is not None:
        _DASH_RESPONSE_CACHE[cache_key] = (time.time(), disk)
        return disk
    return None


def _remember_dashboard_page(namespace: str, cache_key: str, payload: dict, *, narrowed: bool) -> None:
    if narrowed:
        return
    _DASH_RESPONSE_CACHE[cache_key] = (time.time(), payload)
    from api.page_response_cache import save_page_cache

    save_page_cache(namespace, cache_key, payload)
_FILTERED_CTX_LOCK = threading.Lock()
# Single-flight: season+zone used to fan out /kpis+/insights+/rows and rebuild the
# same filtered frame 3–4× → Azure worker OOM/503 (HTML error page in the UI).
_FILTERED_CTX_INFLIGHT: dict[str, threading.Event] = {}
_FILTERED_CTX_INFLIGHT_LOCK = threading.Lock()

_DM_SOH_FRAME: pd.DataFrame | None = None
_DM_SOH_FRAME_MTIME = 0.0
_DM_SOH_FRAME_LOCK = threading.Lock()
_SOH_SCOPE_RESULT_CACHE: dict[str, tuple[float, tuple[float, float | None, float | None, float | None]]] = {}
_SOH_SCOPE_RESULT_TTL = 120.0


def _scope_cache_key(scope: dict[str, Any] | None) -> str:
    scope = scope or {}
    parts = []
    for k in (
        "season", "sku", "barcode", "barcodes", "departments", "sections",
        "channel", "zones", "site_id", "site_ids",
        "store_grades", "states", "cities",
    ):
        parts.append(f"{k}={str(scope.get(k) or '').strip().upper()}")
    return "|".join(parts)


def _cm_site_keys_for_geo_scope(scope: dict[str, Any]) -> set[str] | None:
    """
    Resolve Customer Master site keys for grade / state / city / zone when
    `site_ids` was not already supplied by the frontend.
    Returns None when no geo cut is requested.
    """
    grades = _csv_filter_set(scope.get("store_grades"))
    states = _csv_filter_set(scope.get("states"))
    cities = _csv_filter_set(scope.get("cities"))
    zones = _csv_filter_set(scope.get("zones"))
    if not any([grades, states, cities, zones]):
        return None
    try:
        from pipeline.customer_master import load_customer_master

        cm = load_customer_master(retail_only=True)
    except Exception as exc:
        logger.warning("SOH geo CM resolve failed: %s", exc)
        return None
    if cm is None or cm.empty:
        return None
    frame = cm
    if zones is not None and "zone" in frame.columns:
        frame = frame[frame["zone"].astype(str).str.strip().str.upper().isin(zones)]
    if states is not None:
        state_col = next((c for c in ("state", "state_name") if c in frame.columns), None)
        if state_col:
            frame = frame[frame[state_col].astype(str).str.strip().str.upper().isin({s.upper() for s in states})]
    if cities is not None:
        city_col = next((c for c in ("city", "city_name") if c in frame.columns), None)
        if city_col:
            frame = frame[frame[city_col].astype(str).str.strip().str.upper().isin({c.upper() for c in cities})]
    if grades is not None and "store_grade" in frame.columns:
        frame = frame[frame["store_grade"].astype(str).str.strip().str.upper().isin({g.upper() for g in grades})]
    keys: set[str] = set()
    for col in ("_site_key", "site_manual_code", "site_code", "site_short_code"):
        if col not in frame.columns:
            continue
        for v in frame[col].dropna().astype(str).str.strip():
            if not v or v.lower() in ("nan", "none"):
                continue
            keys.add(v.upper())
    return keys


def _load_dm_soh_frame() -> pd.DataFrame:
    """Load + normalize daily_inventory_movement once per file mtime (Azure hot path)."""
    global _DM_SOH_FRAME, _DM_SOH_FRAME_MTIME
    dm_path = _daily_movement_parquet_path()
    if not dm_path.exists():
        return pd.DataFrame()
    mtime = float(dm_path.stat().st_mtime)
    with _DM_SOH_FRAME_LOCK:
        if _DM_SOH_FRAME is not None and _DM_SOH_FRAME_MTIME == mtime and not _DM_SOH_FRAME.empty:
            return _DM_SOH_FRAME
    base_cols = [
        "store", "barcode", "date", "closing_soh",
        "dispatch_in", "ibt_in", "sales_return_qty",
        "sales_qty", "full_price_sales_qty", "discount_sales_qty",
    ]
    dim_cols = [
        "season", "sku", "department", "section", "zone",
        "channel", "sales_channel", "store_type",
    ]
    try:
        dm = pd.read_parquet(dm_path, columns=base_cols + dim_cols)
    except Exception:
        try:
            dm = pd.read_parquet(dm_path, columns=base_cols + ["season", "sku"])
        except Exception:
            dm = pd.read_parquet(dm_path, columns=base_cols)
    dm["date"] = pd.to_datetime(dm["date"], errors="coerce")
    dm = dm.dropna(subset=["date"])
    dm["store"] = dm["store"].astype(str).str.strip().str.upper()
    if "barcode" in dm.columns:
        dm["barcode"] = (
            dm["barcode"].astype(str).str.strip().str.upper().str.replace(r"\.0$", "", regex=True)
        )
    with _DM_SOH_FRAME_LOCK:
        _DM_SOH_FRAME = dm
        _DM_SOH_FRAME_MTIME = mtime
    return dm


def _sku_soh_for_scope(
    soh_map: dict[str, Any],
    *,
    scope: dict[str, Any] | None,
    sales_df: pd.DataFrame | None = None,
) -> dict[str, float]:
    """SKU closing SOH for the active zone/site/season cut (not network-wide)."""
    if _scope_is_unfiltered(scope):
        return dict(soh_map.get("sku") or {})

    from api.historical_movement import _extract_single_site_key

    # Season / product dims live only on daily_inventory_movement — never early-return
    # the store×sku rollup when those filters are active (that map has no season).
    needs_product_cut = any(
        _csv_filter_set((scope or {}).get(k)) is not None
        for k in ("season", "sku", "barcode", "barcodes", "departments", "sections")
    )

    observed: set[str] = set()
    site_ids = _csv_filter_set((scope or {}).get("site_ids"))
    site_id_one = _csv_filter_set((scope or {}).get("site_id"))
    if site_id_one and not site_ids:
        site_ids = site_id_one
    if site_ids is not None:
        for v in site_ids:
            k = _extract_single_site_key(v) or v
            if k:
                observed.add(str(k).strip().upper())
            observed.add(str(v).strip().upper())

    zones = _csv_filter_set((scope or {}).get("zones"))
    needs_geo_cm = any(
        _csv_filter_set((scope or {}).get(k)) is not None
        for k in ("store_grades", "states", "cities", "zones")
    )
    site_sku: dict[Any, float] = soh_map.get("site_sku") or {}
    if not observed and not needs_geo_cm and not needs_product_cut:
        return dict(soh_map.get("sku") or {})

    out: dict[str, float] = {}

    # Explicit site_ids (FE already AND-intersects zone/grade/state/city): store×sku rollup.
    # Zone/grade-only without site_ids → daily movement + CM resolve.
    if not needs_product_cut and observed and site_sku:
        for key, val in site_sku.items():
            if not isinstance(key, tuple) or len(key) < 2:
                continue
            store, sku = str(key[0]).strip().upper(), str(key[1]).strip().upper()
            store_k = (_extract_single_site_key(store) or store)
            if store in observed or store_k in observed:
                if sku and float(val) > 0:
                    out[sku] = out.get(sku, 0.0) + float(val)
        if out:
            return out

    # Filter daily movement by zone/site/season/product and roll up SKU
    try:
        dm_path = _daily_movement_parquet_path()
        if not dm_path.exists():
            return out
        cols = [
            "store", "barcode", "date", "closing_soh", "sku", "zone",
            "season", "department", "section", "channel", "sales_channel", "store_type",
        ]
        try:
            dm = pd.read_parquet(dm_path, columns=cols)
        except Exception:
            try:
                dm = pd.read_parquet(
                    dm_path,
                    columns=["store", "barcode", "date", "closing_soh", "sku", "zone", "season"],
                )
            except Exception:
                dm = pd.read_parquet(
                    dm_path, columns=["store", "barcode", "date", "closing_soh", "sku"]
                )
        dm = _filter_daily_movement_to_scope(
            dm, pd.DataFrame(), scope
        )
        if dm.empty:
            return out
        dm["date"] = pd.to_datetime(dm["date"], errors="coerce")
        dm = dm.dropna(subset=["date"])
        latest = (
            dm.sort_values("date", kind="mergesort")
            .drop_duplicates(["store", "barcode"], keep="last")
        )
        latest["sku"] = latest["sku"].astype(str).str.strip().str.upper()
        latest["closing_soh"] = pd.to_numeric(latest["closing_soh"], errors="coerce").fillna(0.0)
        latest = latest[
            latest["sku"].ne("") & latest["sku"].ne("NAN") & (latest["closing_soh"] > 0)
        ]
        for sku, val in latest.groupby("sku", sort=False)["closing_soh"].sum().items():
            out[str(sku)] = float(val)
    except Exception as exc:
        logger.warning("Scoped SKU SOH failed: %s", exc)
    return out


def _dashboard_scope_dict(
    *,
    season: str,
    sku: str,
    barcode: str,
    barcodes: str,
    departments: str,
    sections: str,
    channel: str,
    zones: str,
    site_id: str,
    site_ids: str,
    store_grades: str = "",
    states: str = "",
    cities: str = "",
) -> dict[str, Any]:
    return {
        "season": season,
        "sku": sku,
        "barcode": barcode,
        "barcodes": barcodes,
        "departments": departments,
        "sections": sections,
        "channel": channel,
        "zones": zones,
        "site_id": site_id,
        "site_ids": site_ids,
        "store_grades": store_grades,
        "states": states,
        "cities": cities,
    }


def _get_filtered_sales_context(
    *,
    year: int | None,
    site_id: str,
    site_ids: str,
    channel: str,
    month: str,
    sku: str,
    season: str,
    barcode: str,
    barcodes: str,
    departments: str,
    sections: str,
    zones: str,
    store_grades: str,
    states: str,
    cities: str,
    search: str,
    timeline_mode: str,
    timeline_values: str,
    refresh: int = 0,
) -> dict[str, Any]:
    """Load + filter sales once; progressive endpoints share this for ~45s."""
    from api.sales_year_loader import normalize_dashboard_year, sales_data_revision

    year_val: int = normalize_dashboard_year(year, default=2027)
    tl_mode = _val_str(timeline_mode)
    tl_values = _val_str(timeline_values)
    if not tl_mode or not tl_values:
        tl_mode = "fy_year"
        tl_values = f"FY{year_val}"
    # Include Sales-Master etag so morning blob updates invalidate KPI/insights page-cache.
    data_rev = sales_data_revision(year_val)

    ctx_key = "|".join(
        str(p)
        for p in (
            "filt-ctx-v2-datarev",
            year_val,
            data_rev,
            site_id,
            site_ids,
            channel,
            month,
            sku,
            season,
            barcode,
            barcodes,
            departments,
            sections,
            zones,
            store_grades,
            states,
            cities,
            search,
            tl_mode,
            tl_values,
        )
    )
    refresh_i = int(_val(refresh, 0))
    narrowed = _request_is_narrowed(
        site_id=site_id,
        site_ids=site_ids,
        channel=channel,
        month=month,
        sku=sku,
        season=season,
        barcode=barcode,
        barcodes=barcodes,
        departments=departments,
        sections=sections,
        zones=zones,
        store_grades=store_grades,
        states=states,
        cities=cities,
        search=search,
        timeline_mode=tl_mode,
    )
    if not refresh_i and not narrowed:
        with _FILTERED_CTX_LOCK:
            hit = _FILTERED_CTX_CACHE.get(ctx_key)
            if hit and (time.time() - hit[0] < _FILTERED_CTX_TTL):
                return hit[1]

    leader = False
    waiter: threading.Event | None = None
    if not refresh_i:
        with _FILTERED_CTX_INFLIGHT_LOCK:
            existing = _FILTERED_CTX_INFLIGHT.get(ctx_key)
            if existing is not None:
                waiter = existing
            else:
                waiter = threading.Event()
                _FILTERED_CTX_INFLIGHT[ctx_key] = waiter
                leader = True
        if not leader and waiter is not None:
            waiter.wait(timeout=180.0)
            with _FILTERED_CTX_LOCK:
                hit = _FILTERED_CTX_CACHE.get(ctx_key)
                if hit and (time.time() - hit[0] < _FILTERED_CTX_TTL):
                    return hit[1]
            # Leader failed — become leader for a retry build.
            with _FILTERED_CTX_INFLIGHT_LOCK:
                if ctx_key in _FILTERED_CTX_INFLIGHT:
                    waiter = _FILTERED_CTX_INFLIGHT[ctx_key]
                    leader = False
                else:
                    waiter = threading.Event()
                    _FILTERED_CTX_INFLIGHT[ctx_key] = waiter
                    leader = True
            if not leader and waiter is not None:
                waiter.wait(timeout=180.0)
                with _FILTERED_CTX_LOCK:
                    hit = _FILTERED_CTX_CACHE.get(ctx_key)
                    if hit:
                        return hit[1]

    try:
        base_kw = dict(
            site_id=site_id,
            site_ids=site_ids,
            channel=channel,
            month=month,
            sku=sku,
            season=season,
            barcode=barcode,
            barcodes=barcodes,
            departments=departments,
            sections=sections,
            zones=zones,
            store_grades=store_grades,
            states=states,
            cities=cities,
            search=search,
        )
        has_active_filters = any(
            [
                site_id and site_id != "all",
                site_ids,
                channel and channel != "all",
                month and month != "all",
                sku and sku != "all",
                season and season != "all",
                barcode and barcode != "all",
                barcodes,
                departments,
                sections,
                zones,
                store_grades,
                states,
                cities,
                search,
            ]
        )
        scope = _dashboard_scope_dict(
            season=season,
            sku=sku,
            barcode=barcode,
            barcodes=barcodes,
            departments=departments,
            sections=sections,
            channel=channel,
            zones=zones,
            site_id=site_id,
            site_ids=site_ids,
            store_grades=store_grades,
            states=states,
            cities=cities,
        )

        full: pd.DataFrame | None = None
        filtered: pd.DataFrame | None = None
        engine_used = "pandas"
        engine_diag: dict[str, Any] = {}

        # Azure SQL only when user applied filters. Default first view stays on page-cache /
        # DuckDB / warm CSV path (fast paint).
        if has_active_filters:
            try:
                from api.azure_sql_analytics import sql_load_filtered_sales_df, sql_query_ready

                if sql_query_ready():
                    fy_years = _fy_years_for_timeline(
                        year_val=year_val,
                        timeline_mode=tl_mode,
                        timeline_values=tl_values,
                    )
                    filtered = sql_load_filtered_sales_df(
                        {
                            "fy_years": fy_years,
                            "site_id": site_id,
                            "site_ids": site_ids,
                            "channel": channel,
                            "month": month,
                            "sku": sku,
                            "season": season,
                            "barcode": barcode,
                            "barcodes": barcodes,
                            "departments": departments,
                            "sections": sections,
                            "zones": zones,
                            "store_grades": store_grades,
                            "states": states,
                            "cities": cities,
                            "search": search,
                            "timeline_mode": tl_mode,
                            "timeline_values": tl_values,
                        }
                    )
                    engine_used = "azure_sql"
                    engine_diag = {
                        "engine": "azure_sql",
                        "query_ms": float(getattr(filtered, "attrs", {}).get("query_ms") or 0),
                        "rows": int(len(filtered)),
                    }
                    full = filtered
                    logger.info(
                        "sales filtered context via azure_sql rows=%s query_ms=%s",
                        len(filtered),
                        engine_diag.get("query_ms"),
                    )
            except Exception as sql_exc:
                logger.warning(
                    "Azure SQL sales filter path failed; falling back: %s",
                    sql_exc,
                )
                full = None
                filtered = None
                engine_used = "pandas"
                engine_diag = {"fallback_reason": str(sql_exc)}

        # Optional DuckDB path for default view / SQL miss.
        if filtered is None:
            try:
                from api.services.analytics_query_engine import (
                    load_filtered_sales_duckdb,
                    use_duckdb,
                )

                if use_duckdb():
                    fy_years = _fy_years_for_timeline(
                        year_val=year_val,
                        timeline_mode=tl_mode,
                        timeline_values=tl_values,
                    )
                    filtered, engine_diag = load_filtered_sales_duckdb(
                        fy_years=fy_years,
                        site_id=site_id,
                        site_ids=site_ids,
                        channel=channel,
                        month=month,
                        sku=sku,
                        season=season,
                        barcode=barcode,
                        barcodes=barcodes,
                        departments=departments,
                        sections=sections,
                        zones=zones,
                        store_grades=store_grades,
                        states=states,
                        cities=cities,
                        search=search,
                        timeline_mode=tl_mode,
                        timeline_values=tl_values,
                        force_refresh=bool(refresh_i),
                    )
                    engine_used = "duckdb"
                    full = filtered
                    logger.info(
                        "sales filtered context via duckdb rows=%s query_ms=%s version=%s",
                        len(filtered),
                        engine_diag.get("query_ms"),
                        engine_diag.get("dataset_version"),
                    )
            except Exception as duck_exc:
                logger.warning(
                    "DuckDB sales filter path failed; falling back to pandas: %s",
                    duck_exc,
                )
                full = None
                filtered = None
                engine_used = "pandas"
                engine_diag = {"fallback_reason": str(duck_exc)}

        if filtered is None:
            try:
                full = _load_sales_for_timeline(
                    year_val=year_val,
                    timeline_mode=tl_mode,
                    timeline_values=tl_values,
                    force_refresh=bool(refresh_i),
                )
            except HTTPException:
                raise
            except Exception as exc:
                logger.exception("Sales dashboard load failed")
                raise HTTPException(status_code=500, detail=str(exc)) from exc

            filtered = _apply_filters(full, **base_kw)
            filtered = _apply_timeline_filter(
                filtered, timeline_mode=tl_mode, timeline_values=tl_values
            )

        ctx: dict[str, Any] = {
            "year_val": year_val,
            "tl_mode": tl_mode,
            "tl_values": tl_values,
            "full": full if full is not None else filtered,
            "filtered": filtered,
            "base_kw": base_kw,
            "has_active_filters": has_active_filters,
            "scope": scope,
            "ctx_key": ctx_key,
            "query_engine": engine_used,
            "engine_diag": engine_diag,
            "dataset_version": engine_diag.get("dataset_version") or data_rev,
        }
        if not narrowed:
            with _FILTERED_CTX_LOCK:
                _FILTERED_CTX_CACHE[ctx_key] = (time.time(), ctx)
                if len(_FILTERED_CTX_CACHE) > 16:
                    oldest = min(_FILTERED_CTX_CACHE, key=lambda k: _FILTERED_CTX_CACHE[k][0])
                    _FILTERED_CTX_CACHE.pop(oldest, None)
        return ctx
    finally:
        if leader and waiter is not None:
            with _FILTERED_CTX_INFLIGHT_LOCK:
                _FILTERED_CTX_INFLIGHT.pop(ctx_key, None)
            waiter.set()


def _assemble_kpis_from_duck_agg(
    agg: dict[str, Any],
    *,
    scope: dict[str, Any] | None,
    prior_net: float = 0.0,
    prior_qty: float = 0.0,
    soh_map: dict[str, dict[Any, float]] | None = None,
) -> dict[str, Any]:
    """Map DuckDB aggregate scalars into the existing `_kpis` response shape."""
    qty = float(agg.get("qty") or 0)
    net = float(agg.get("net") or 0)
    disc = float(agg.get("disc") or 0)
    gross = float(agg.get("gross") or 0)
    tax_amt = float(agg.get("tax_amt") or 0)
    avg_disc = float(agg.get("avg_disc") or 0)
    stores = int(agg.get("store_count") or 0)
    line_count = int(agg.get("line_count") or 0)
    day_count = max(1, int(agg.get("day_count") or 1))
    fp_sales = float(agg.get("fp_sales") or 0)
    fp_qty = float(agg.get("fp_qty") or 0)
    disc_sales = float(agg.get("disc_sales") or 0)
    disc_qty = float(agg.get("disc_qty") or 0)
    asp = round(net / qty, 2) if qty > 0 else 0.0
    disc_on_gross = round((disc / gross * 100), 1) if gross > 0 else 0.0
    full_price_pct = round((fp_sales / net * 100), 1) if net > 0 else 0.0
    discount_price_pct = round((disc_sales / net * 100), 1) if net > 0 else 0.0

    sell_through_pct = full_price_str_pct = discount_str_pct = None
    soh_val = 0.0
    if _scope_is_unfiltered(scope):
        soh_map = soh_map or _get_soh_map()
        soh_val = float(soh_map.get("total_soh") or 0.0)
    else:
        try:
            soh_val, sell_through_pct, full_price_str_pct, discount_str_pct = (
                _soh_str_from_movement_scope(pd.DataFrame(), scope)
            )
            if sell_through_pct is None and soh_val <= 0:
                sell_through_pct = full_price_str_pct = discount_str_pct = 0.0
        except Exception as exc:
            logger.warning("DuckDB KPI scoped SOH failed: %s", exc)

    if (sell_through_pct is None or sell_through_pct > 100.0) and soh_val > 0 and (qty + soh_val) > 0:
        total_avail = qty + soh_val
        sell_through_pct = round((qty / total_avail) * 100.0, 1)
        full_price_str_pct = round((fp_qty / total_avail) * 100.0, 1)
        discount_str_pct = round((disc_qty / total_avail) * 100.0, 1)

    sales_per_stock = round(qty / soh_val, 2) if soh_val > 0 else 0.0

    # SPSF from site nets × CM sqft (small dict — not full frame)
    sqft_map = _get_customer_master_sqft_map()
    total_sqft = total_sqft_sales = 0.0
    sqft_stores_count = 0
    sales_per_sqft = 0.0
    site_nets = agg.get("site_nets") or {}
    if sqft_map and site_nets:
        from api.historical_movement import _extract_single_site_key

        for site_val, s_sales in site_nets.items():
            k = _extract_single_site_key(site_val) or str(site_val).strip().upper()
            sq = sqft_map.get(k) or sqft_map.get(str(site_val).strip().upper())
            if sq and sq > 0:
                total_sqft += sq
                total_sqft_sales += float(s_sales)
                sqft_stores_count += 1
        sales_per_sqft = round(total_sqft_sales / total_sqft, 2) if total_sqft > 0 else 0.0

    yoy_sales_growth_pct = None
    yoy_qty_growth_pct = None
    if prior_net > 0:
        yoy_sales_growth_pct = round(((net - prior_net) / prior_net * 100), 1)
    elif prior_net == 0 and line_count == 0 and prior_qty > 0:
        yoy_sales_growth_pct = -100.0
    if prior_qty > 0:
        yoy_qty_growth_pct = round(((qty - prior_qty) / prior_qty * 100), 1)

    if line_count == 0:
        return {
            "gross_sales": 0.0,
            "gst": 0.0,
            "ros": 0.0,
            "net_sales": 0.0,
            "units_sold": 0,
            "asp": 0.0,
            "discount_given": 0.0,
            "avg_discount_pct": 0.0,
            "discount_on_gross_pct": 0.0,
            "store_count": 0,
            "line_count": 0,
            "current_soh": int(round(soh_val)),
            "sell_through_pct": sell_through_pct if sell_through_pct is not None else 0.0,
            "full_price_pct": 0.0,
            "full_price_sales": 0.0,
            "full_price_qty": 0,
            "full_price_str_pct": full_price_str_pct if full_price_str_pct is not None else 0.0,
            "discount_sales": 0.0,
            "discount_qty": 0,
            "discount_price_pct": 0.0,
            "discount_str_pct": discount_str_pct if discount_str_pct is not None else 0.0,
            "sales_per_stock": 0.0,
            "sales_per_sqft": 0.0,
            "sqft_total": 0.0,
            "sqft_stores_count": 0,
            "sqft_net_sales": 0.0,
            "yoy_sales_growth_pct": -100.0 if prior_net > 0 else None,
            "yoy_qty_growth_pct": -100.0 if prior_qty > 0 else None,
            "target": None,
            "target_achievement_pct": None,
        }

    net_cr = round(net / 1e7, 2)
    gross_cr = round(gross / 1e7, 2)
    return {
        "gross_sales": round(gross, 2),
        "gst": round(tax_amt, 2),
        "ros": qty / day_count,
        "net_sales": round(net, 2),
        "net": net_cr,
        "gross": gross_cr,
        "net_fmt": f"₹{net_cr}Cr" if net_cr > 0 else "₹0.00Cr",
        "gross_fmt": f"₹{gross_cr}Cr" if gross_cr > 0 else "₹0.00Cr",
        "units_sold": int(qty),
        "units": int(qty),
        "units_fmt": f"{int(qty):,} pcs",
        "asp": asp,
        "discount_given": round(disc, 2),
        "discount": round(disc / 1e7, 2),
        "discount_pct": disc_on_gross,
        "avg_discount_pct": round(avg_disc, 1),
        "discount_on_gross_pct": disc_on_gross,
        "target": None,
        "target_achievement_pct": None,
        "store_count": stores,
        "line_count": line_count,
        "current_soh": int(soh_val),
        "sell_through_pct": sell_through_pct,
        "full_price_pct": full_price_pct,
        "full_price_sales": round(fp_sales, 2),
        "full_price_qty": int(fp_qty),
        "full_price_str_pct": full_price_str_pct,
        "discount_sales": round(disc_sales, 2),
        "discount_qty": int(disc_qty),
        "discount_price_pct": discount_price_pct,
        "discount_str_pct": discount_str_pct,
        "yoy_sales_growth_pct": yoy_sales_growth_pct,
        "yoy_qty_growth_pct": yoy_qty_growth_pct,
        "sales_per_stock": sales_per_stock,
        "sales_per_sqft": sales_per_sqft,
        "sqft_total": round(total_sqft, 1),
        "sqft_stores_count": sqft_stores_count,
        "sqft_net_sales": round(total_sqft_sales, 2),
    }


def _try_azure_sql_kpis_payload(
    *,
    year_val: int,
    tl_mode: str,
    tl_values: str,
    site_id: str,
    site_ids: str,
    channel: str,
    month: str,
    sku: str,
    season: str,
    barcode: str,
    barcodes: str,
    departments: str,
    sections: str,
    zones: str,
    store_grades: str,
    states: str,
    cities: str,
    search: str,
    scope: dict[str, Any],
    has_active_filters: bool,
) -> dict[str, Any] | None:
    """Full KPI payload via Azure SQL aggregates. None → caller falls back."""
    from api.azure_sql_analytics import (
        sql_light_trends,
        sql_query_ready,
        sql_sales_kpis,
        sql_soh_total,
        sql_sply_totals,
    )

    if not sql_query_ready():
        return None

    fy_years = _fy_years_for_timeline(
        year_val=year_val, timeline_mode=tl_mode, timeline_values=tl_values
    )
    filt = dict(
        fy_years=fy_years,
        site_id=site_id,
        site_ids=site_ids,
        channel=channel,
        month=month,
        sku=sku,
        season=season,
        barcode=barcode,
        barcodes=barcodes,
        departments=departments,
        sections=sections,
        zones=zones,
        store_grades=store_grades,
        states=states,
        cities=cities,
        search=search,
        timeline_mode=tl_mode,
        timeline_values=tl_values,
    )
    result = sql_sales_kpis(filt)
    agg = result.get("agg") or {}
    light_insights = sql_light_trends(filt)

    # Same SPLY rule as the DuckDB path (skipped on very large / unfiltered year views).
    prior_net = prior_qty = 0.0
    large_year_view = int(agg.get("line_count") or 0) >= 150_000 or (
        str(tl_mode or "").strip().lower() in ("year", "fy_year", "fy", "financial_year")
        and not has_active_filters
    )
    if not large_year_view and int(agg.get("line_count") or 0) > 0:
        try:
            prior_net, prior_qty = sql_sply_totals(
                min_date=agg.get("min_date"),
                max_date=agg.get("max_date"),
                filters=filt,
            )
        except Exception as exc:
            logger.warning("Azure SQL SPLY aggregate skipped: %s", exc)

    soh_map = None
    if _scope_is_unfiltered(scope):
        soh_map = {"total_soh": sql_soh_total(filt)}
    kpis = _attach_growth_kpis(
        _assemble_kpis_from_duck_agg(
            agg,
            scope=scope,
            prior_net=prior_net,
            prior_qty=prior_qty,
            soh_map=soh_map,
        ),
        light_insights,
    )
    return {
        "ok": True,
        "year": year_val,
        "kpis": kpis,
        "total": int(agg.get("line_count") or 0),
        "store_count": int(agg.get("store_count") or 0),
        "scope": {
            "zones": zones,
            "site_ids": site_ids,
            "channel": channel,
        },
        "query_engine": "azure_sql",
        "engine_diag": result.get("diag") or {},
        "has_active_filters": has_active_filters,
    }


def _try_duckdb_kpis_payload(
    *,
    year_val: int,
    tl_mode: str,
    tl_values: str,
    site_id: str,
    site_ids: str,
    channel: str,
    month: str,
    sku: str,
    season: str,
    barcode: str,
    barcodes: str,
    departments: str,
    sections: str,
    zones: str,
    store_grades: str,
    states: str,
    cities: str,
    search: str,
    refresh_i: int,
    scope: dict[str, Any],
    has_active_filters: bool,
) -> dict[str, Any] | None:
    """Full KPI payload via DuckDB SQL aggregates. None → caller falls back to Pandas."""
    from api.services.analytics_query_engine import (
        aggregate_insights_duckdb,
        aggregate_sales_kpis_duckdb,
        aggregate_sply_totals_duckdb,
        use_duckdb,
    )

    if not use_duckdb():
        return None

    fy_years = _fy_years_for_timeline(
        year_val=year_val, timeline_mode=tl_mode, timeline_values=tl_values
    )
    filt_kw = dict(
        site_id=site_id,
        site_ids=site_ids,
        channel=channel,
        month=month,
        sku=sku,
        season=season,
        barcode=barcode,
        barcodes=barcodes,
        departments=departments,
        sections=sections,
        zones=zones,
        store_grades=store_grades,
        states=states,
        cities=cities,
        search=search,
        timeline_mode=tl_mode,
        timeline_values=tl_values,
        force_refresh=bool(refresh_i),
    )
    agg, diag = aggregate_sales_kpis_duckdb(fy_years=fy_years, **filt_kw)
    light_insights, _idiag = aggregate_insights_duckdb(
        fy_years=fy_years, limit=10, light=True, **filt_kw
    )

    prior_net = prior_qty = 0.0
    large_year_view = int(agg.get("line_count") or 0) >= 150_000 or (
        str(tl_mode or "").strip().lower() in ("year", "fy_year", "fy", "financial_year")
        and not has_active_filters
    )
    if not large_year_view and int(agg.get("line_count") or 0) > 0:
        try:
            prior_net, prior_qty, _sdiag = aggregate_sply_totals_duckdb(
                min_date=agg.get("min_date"),
                max_date=agg.get("max_date"),
                filter_kw={
                    "site_id": site_id,
                    "site_ids": site_ids,
                    "channel": channel,
                    "sku": sku,
                    "season": season,
                    "barcode": barcode,
                    "barcodes": barcodes,
                    "departments": departments,
                    "sections": sections,
                    "zones": zones,
                    "store_grades": store_grades,
                    "states": states,
                    "cities": cities,
                    "search": search,
                },
                timeline_mode=tl_mode,
                timeline_values=tl_values,
            )
        except Exception as exc:
            logger.warning("DuckDB SPLY aggregate skipped: %s", exc)

    soh_map = _get_soh_map() if _scope_is_unfiltered(scope) else None
    kpis = _attach_growth_kpis(
        _assemble_kpis_from_duck_agg(
            agg,
            scope=scope,
            prior_net=prior_net,
            prior_qty=prior_qty,
            soh_map=soh_map,
        ),
        light_insights,
    )
    return {
        "ok": True,
        "year": year_val,
        "kpis": kpis,
        "total": int(agg.get("line_count") or 0),
        "store_count": int(agg.get("store_count") or 0),
        "scope": {
            "zones": zones,
            "site_ids": site_ids,
            "channel": channel,
        },
        "query_engine": "duckdb",
        "dataset_version": diag.get("dataset_version"),
        "engine_diag": {
            "kpi_query_ms": diag.get("query_ms"),
            "cache_hit": diag.get("cache_hit"),
        },
    }


@router.get("/kpis")
def get_sales_dashboard_kpis(
    year: int | None = Query(2027),
    site_id: str = Query("all"),
    site_ids: str = Query(""),
    channel: str = Query("all"),
    month: str = Query("all"),
    sku: str = Query("all"),
    season: str = Query("all"),
    barcode: str = Query("all"),
    barcodes: str = Query(""),
    departments: str = Query(""),
    sections: str = Query(""),
    zones: str = Query(""),
    store_grades: str = Query(""),
    states: str = Query(""),
    cities: str = Query(""),
    search: str = Query(""),
    timeline_mode: str = Query(""),
    timeline_values: str = Query(""),
    refresh: int = Query(0),
):
    """KPI cards only — zone/site scoped qty sold + SOH. Fast first paint."""
    from api.sales_year_loader import normalize_dashboard_year, sales_data_revision
    from api.services.analytics_query_engine import use_duckdb

    year_val: int = normalize_dashboard_year(year, default=2027)
    tl_mode = _val_str(timeline_mode)
    tl_values = _val_str(timeline_values)
    if not tl_mode or not tl_values:
        tl_mode = "fy_year"
        tl_values = f"FY{year_val}"
    data_rev = sales_data_revision(year_val)
    has_active_filters = any(
        [
            site_id and site_id != "all",
            site_ids,
            channel and channel != "all",
            month and month != "all",
            sku and sku != "all",
            season and season != "all",
            barcode and barcode != "all",
            barcodes,
            departments,
            sections,
            zones,
            store_grades,
            states,
            cities,
            search,
        ]
    )
    scope = _dashboard_scope_dict(
        season=season,
        sku=sku,
        barcode=barcode,
        barcodes=barcodes,
        departments=departments,
        sections=sections,
        channel=channel,
        zones=zones,
        site_id=site_id,
        site_ids=site_ids,
        store_grades=store_grades,
        states=states,
        cities=cities,
    )
    ctx_key = "|".join(
        str(p)
        for p in (
            "filt-ctx-v2-datarev",
            year_val,
            data_rev,
            site_id,
            site_ids,
            channel,
            month,
            sku,
            season,
            barcode,
            barcodes,
            departments,
            sections,
            zones,
            store_grades,
            states,
            cities,
            search,
            tl_mode,
            tl_values,
        )
    )
    cache_key = f"kpis-v3-duckagg|{ctx_key}"
    refresh_i = int(_val(refresh, 0))
    narrowed = _request_is_narrowed(
        site_id=site_id,
        site_ids=site_ids,
        channel=channel,
        month=month,
        sku=sku,
        season=season,
        barcode=barcode,
        barcodes=barcodes,
        departments=departments,
        sections=sections,
        zones=zones,
        store_grades=store_grades,
        states=states,
        cities=cities,
        search=search,
        timeline_mode=tl_mode,
    )
    saved = _saved_dashboard_page(
        "sales-dashboard-kpis", cache_key, narrowed=narrowed, refresh=refresh_i
    )
    if saved is not None:
        return saved

    # Azure SQL KPIs only after filter apply. Default first load = page-cache above.
    if has_active_filters:
        try:
            payload = _try_azure_sql_kpis_payload(
                year_val=year_val,
                tl_mode=tl_mode,
                tl_values=tl_values,
                site_id=site_id,
                site_ids=site_ids,
                channel=channel,
                month=month,
                sku=sku,
                season=season,
                barcode=barcode,
                barcodes=barcodes,
                departments=departments,
                sections=sections,
                zones=zones,
                store_grades=store_grades,
                states=states,
                cities=cities,
                search=search,
                scope=scope,
                has_active_filters=has_active_filters,
            )
            if payload is not None:
                _remember_dashboard_page(
                    "sales-dashboard-kpis", cache_key, payload, narrowed=narrowed
                )
                return payload
        except Exception as sql_exc:
            logger.warning("Azure SQL KPI aggregate path failed; continuing: %s", sql_exc)

    # Prefer DuckDB SQL aggregates (no SELECT * materialization).
    if use_duckdb():
        try:
            payload = _try_duckdb_kpis_payload(
                year_val=year_val,
                tl_mode=tl_mode,
                tl_values=tl_values,
                site_id=site_id,
                site_ids=site_ids,
                channel=channel,
                month=month,
                sku=sku,
                season=season,
                barcode=barcode,
                barcodes=barcodes,
                departments=departments,
                sections=sections,
                zones=zones,
                store_grades=store_grades,
                states=states,
                cities=cities,
                search=search,
                refresh_i=refresh_i,
                scope=scope,
                has_active_filters=has_active_filters,
            )
            if payload is not None:
                _remember_dashboard_page(
                    "sales-dashboard-kpis", cache_key, payload, narrowed=narrowed
                )
                return payload
        except Exception as duck_exc:
            logger.warning("DuckDB KPI aggregate path failed; pandas fallback: %s", duck_exc)

    ctx = _get_filtered_sales_context(
        year=year,
        site_id=site_id,
        site_ids=site_ids,
        channel=channel,
        month=month,
        sku=sku,
        season=season,
        barcode=barcode,
        barcodes=barcodes,
        departments=departments,
        sections=sections,
        zones=zones,
        store_grades=store_grades,
        states=states,
        cities=cities,
        search=search,
        timeline_mode=timeline_mode,
        timeline_values=timeline_values,
        refresh=refresh,
    )
    filtered: pd.DataFrame = ctx["filtered"]
    soh_map = _get_soh_map()
    sply_prior = pd.DataFrame()
    large_year_view = len(filtered) >= 150_000 or (
        str(ctx["tl_mode"] or "").strip().lower() in ("year", "fy_year", "fy", "financial_year")
        and not ctx["has_active_filters"]
    )
    if not large_year_view:
        try:
            sply_prior = _load_sply_prior_df(
                filtered,
                filter_kw=ctx["base_kw"],
                timeline_mode=ctx["tl_mode"],
                timeline_values=ctx["tl_values"],
            )
        except Exception as exc:
            logger.warning("SPLY prior (kpis) skipped: %s", exc)

    light_insights = _insights(filtered) if not filtered.empty else {}
    kpis = _attach_growth_kpis(
        _kpis(filtered, soh_map=soh_map, df_prior=sply_prior, scope=ctx["scope"]),
        light_insights,
    )
    payload = {
        "ok": True,
        "year": ctx["year_val"],
        "kpis": kpis,
        "total": int(len(filtered)),
        "store_count": int(filtered["site_id"].nunique()) if not filtered.empty and "site_id" in filtered.columns else 0,
        "scope": {
            "zones": zones,
            "site_ids": site_ids,
            "channel": channel,
        },
        "query_engine": ctx.get("query_engine") or "pandas",
        "dataset_version": ctx.get("dataset_version"),
    }
    _remember_dashboard_page("sales-dashboard-kpis", cache_key, payload, narrowed=narrowed)
    return payload


@router.get("/insights")
def get_sales_dashboard_insights(
    year: int | None = Query(2027),
    site_id: str = Query("all"),
    site_ids: str = Query(""),
    channel: str = Query("all"),
    month: str = Query("all"),
    sku: str = Query("all"),
    season: str = Query("all"),
    barcode: str = Query("all"),
    barcodes: str = Query(""),
    departments: str = Query(""),
    sections: str = Query(""),
    zones: str = Query(""),
    store_grades: str = Query(""),
    states: str = Query(""),
    cities: str = Query(""),
    search: str = Query(""),
    timeline_mode: str = Query(""),
    timeline_values: str = Query(""),
    cards: str = Query(
        "",
        description="Visible card slices, comma-separated. Empty computes every slice. Example: trend_month,breakdown_zone,channel",
    ),
    refresh: int = Query(0),
):
    """Trends / top lists for the cards currently on screen."""
    from api.sales_year_loader import normalize_dashboard_year, sales_data_revision
    from api.services.analytics_query_engine import (
        aggregate_insights_duckdb,
        aggregate_sales_kpis_duckdb,
        aggregate_sply_totals_duckdb,
        use_duckdb,
    )

    year_val: int = normalize_dashboard_year(year, default=2027)
    tl_mode = _val_str(timeline_mode)
    tl_values = _val_str(timeline_values)
    if not tl_mode or not tl_values:
        tl_mode = "fy_year"
        tl_values = f"FY{year_val}"
    data_rev = sales_data_revision(year_val)
    has_active_filters = any(
        [
            site_id and site_id != "all",
            site_ids,
            channel and channel != "all",
            month and month != "all",
            sku and sku != "all",
            season and season != "all",
            barcode and barcode != "all",
            barcodes,
            departments,
            sections,
            zones,
            store_grades,
            states,
            cities,
            search,
        ]
    )
    ctx_key = "|".join(
        str(p)
        for p in (
            "filt-ctx-v2-datarev",
            year_val,
            data_rev,
            site_id,
            site_ids,
            channel,
            month,
            sku,
            season,
            barcode,
            barcodes,
            departments,
            sections,
            zones,
            store_grades,
            states,
            cities,
            search,
            tl_mode,
            tl_values,
        )
    )
    card_set = {part.strip() for part in str(_val(cards, "") or "").split(",") if part.strip()} or None
    card_key = ",".join(sorted(card_set)) if card_set else "all"
    cache_key = f"insights-v3-cards|{card_key}|{ctx_key}"
    refresh_i = int(_val(refresh, 0))
    narrowed = _request_is_narrowed(
        site_id=site_id,
        site_ids=site_ids,
        channel=channel,
        month=month,
        sku=sku,
        season=season,
        barcode=barcode,
        barcodes=barcodes,
        departments=departments,
        sections=sections,
        zones=zones,
        store_grades=store_grades,
        states=states,
        cities=cities,
        search=search,
        timeline_mode=tl_mode,
    )
    saved = _saved_dashboard_page(
        "sales-dashboard-insights", cache_key, narrowed=narrowed, refresh=refresh_i
    )
    if saved is not None:
        return saved

    # Filter applied → Azure SQL aggregates (cards only; no sales rows leave the DB).
    if has_active_filters:
        try:
            from api.azure_sql_analytics import (
                sql_aggregate_insights,
                sql_query_ready,
                sql_sales_extent,
                sql_sply_totals,
            )

            if sql_query_ready():
                filt = dict(
                    fy_years=_fy_years_for_timeline(
                        year_val=year_val, timeline_mode=tl_mode, timeline_values=tl_values
                    ),
                    site_id=site_id, site_ids=site_ids, channel=channel, month=month, sku=sku,
                    season=season, barcode=barcode, barcodes=barcodes, departments=departments,
                    sections=sections, zones=zones, store_grades=store_grades, states=states,
                    cities=cities, search=search, timeline_mode=tl_mode, timeline_values=tl_values,
                )
                t_sql = time.perf_counter()
                insights_data = sql_aggregate_insights(filt, cards=card_set, limit=10)
                agg = sql_sales_extent(filt)
                ly_metrics: dict[str, Any] = {}
                # Same SPLY skip rule as the DuckDB path.
                if 0 < int(agg.get("line_count") or 0) < 150_000:
                    prior_net, _prior_qty = sql_sply_totals(
                        min_date=agg.get("min_date"), max_date=agg.get("max_date"), filters=filt
                    )
                    ly_metrics = {"total": float(prior_net)}
                insights_data["ly_metrics"] = ly_metrics
                return {
                    "ok": True,
                    "year": year_val,
                    "insights": insights_data,
                    "total": int(agg.get("line_count") or 0),
                    "query_engine": "azure_sql",
                    "engine_diag": {"insights_query_ms": round((time.perf_counter() - t_sql) * 1000, 1)},
                }
        except Exception as sql_exc:
            logger.warning("Azure SQL insights path failed; falling back: %s", sql_exc)

    if use_duckdb():
        try:
            fy_years = _fy_years_for_timeline(
                year_val=year_val, timeline_mode=tl_mode, timeline_values=tl_values
            )
            filt_kw = dict(
                site_id=site_id,
                site_ids=site_ids,
                channel=channel,
                month=month,
                sku=sku,
                season=season,
                barcode=barcode,
                barcodes=barcodes,
                departments=departments,
                sections=sections,
                zones=zones,
                store_grades=store_grades,
                states=states,
                cities=cities,
                search=search,
                timeline_mode=tl_mode,
                timeline_values=tl_values,
                force_refresh=bool(refresh_i),
            )
            insights_data, idiag = aggregate_insights_duckdb(
                fy_years=fy_years, limit=10, light=False, cards=card_set, **filt_kw
            )
            # Line count + date bounds for SPLY skip / LY total (no row materialization)
            agg, _adiag = aggregate_sales_kpis_duckdb(fy_years=fy_years, **filt_kw)
            ly_metrics: dict[str, Any] = {}
            large_year_view = int(agg.get("line_count") or 0) >= 150_000 or (
                str(tl_mode or "").strip().lower()
                in ("year", "fy_year", "fy", "financial_year")
                and not has_active_filters
            )
            if not large_year_view and int(agg.get("line_count") or 0) > 0:
                try:
                    prior_net, _prior_qty, _sdiag = aggregate_sply_totals_duckdb(
                        min_date=agg.get("min_date"),
                        max_date=agg.get("max_date"),
                        filter_kw={
                            "site_id": site_id,
                            "site_ids": site_ids,
                            "channel": channel,
                            "sku": sku,
                            "season": season,
                            "barcode": barcode,
                            "barcodes": barcodes,
                            "departments": departments,
                            "sections": sections,
                            "zones": zones,
                            "store_grades": store_grades,
                            "states": states,
                            "cities": cities,
                            "search": search,
                        },
                        timeline_mode=tl_mode,
                        timeline_values=tl_values,
                    )
                    ly_metrics = {"total": float(prior_net)}
                except Exception as exc:
                    logger.warning("DuckDB LY total (insights) skipped: %s", exc)
            insights_data["ly_metrics"] = ly_metrics
            payload = {
                "ok": True,
                "year": year_val,
                "insights": insights_data,
                "total": int(agg.get("line_count") or 0),
                "query_engine": "duckdb",
                "dataset_version": idiag.get("dataset_version"),
                "engine_diag": {
                    "insights_query_ms": idiag.get("query_ms"),
                    "cache_hit": idiag.get("cache_hit"),
                },
            }
            _remember_dashboard_page(
                "sales-dashboard-insights", cache_key, payload, narrowed=narrowed
            )
            return payload
        except Exception as duck_exc:
            logger.warning(
                "DuckDB insights aggregate path failed; pandas fallback: %s", duck_exc
            )

    ctx = _get_filtered_sales_context(
        year=year,
        site_id=site_id,
        site_ids=site_ids,
        channel=channel,
        month=month,
        sku=sku,
        season=season,
        barcode=barcode,
        barcodes=barcodes,
        departments=departments,
        sections=sections,
        zones=zones,
        store_grades=store_grades,
        states=states,
        cities=cities,
        search=search,
        timeline_mode=timeline_mode,
        timeline_values=timeline_values,
        refresh=refresh,
    )
    filtered: pd.DataFrame = ctx["filtered"]
    insights_data = _insights(filtered, cards=card_set)
    ly_metrics = {}
    large_year_view = len(filtered) >= 150_000 or (
        str(ctx["tl_mode"] or "").strip().lower() in ("year", "fy_year", "fy", "financial_year")
        and not ctx["has_active_filters"]
    )
    if not large_year_view:
        try:
            sply_prior = _load_sply_prior_df(
                filtered,
                filter_kw=ctx["base_kw"],
                timeline_mode=ctx["tl_mode"],
                timeline_values=ctx["tl_values"],
            )
            if sply_prior is not None and not sply_prior.empty:
                ly_df = sply_prior

                def _get_ly_dict(col: str):
                    if col not in ly_df.columns:
                        return {}
                    g = ly_df.groupby(col, dropna=False)["net_sale_amount"].sum()
                    return {
                        str(k).strip() if pd.notna(k) else "Unspecified": float(v)
                        for k, v in g.items()
                    }

                ly_metrics = {
                    "zone": _get_ly_dict("zone"),
                    "cluster": _get_ly_dict("cluster"),
                    "site_id": _get_ly_dict("site_id"),
                    "sales_channel": _get_ly_dict("store_type"),
                    "department": _get_ly_dict("department"),
                    "section": _get_ly_dict("section"),
                    "city_name": _get_ly_dict("city"),
                    "state_name": _get_ly_dict("state"),
                    "store_grade": _get_ly_dict("store_grade"),
                    "month": _get_ly_dict("month_key"),
                    "total": float(ly_df["net_sale_amount"].sum()),
                }
        except Exception as exc:
            logger.warning("LY metrics (insights) skipped: %s", exc)
    insights_data["ly_metrics"] = ly_metrics
    payload = {
        "ok": True,
        "year": ctx["year_val"],
        "insights": insights_data,
        "total": int(len(filtered)),
        "query_engine": ctx.get("query_engine") or "pandas",
        "dataset_version": ctx.get("dataset_version"),
    }
    _remember_dashboard_page("sales-dashboard-insights", cache_key, payload, narrowed=narrowed)
    return payload


_SALES_ROW_SORT = {
    "sales": "net_sale_amount",
    "net_sale": "net_sale_amount",
    "qty": "qty",
    "date": "date",
    "sku": "sku",
    "product": "sku",
    "ean": "barcode",
    "barcode": "barcode",
    "site_id": "site_id",
    "site_name": "site_name",
    "zone": "zone",
    "season": "season",
    "size": "size",
    "mrp": "mrp",
    "discount": "discount_pct",
    "discount_amount": "discount_amount",
    "city_name": "city",
    "state_name": "state",
    "store_grade": "store_grade",
    "customer_name": "customer_name",
    "sales_channel": "sales_channel",
    "gst_value": "tax_amount",
    "store_sqft": "store_sqft",
    "mdq": "mdq",
}


def _sales_table_records(page_df: pd.DataFrame) -> list[dict]:
    """Turn one table page into JSON rows. SOH is joined only for these lines."""
    if page_df is None or page_df.empty:
        return []
    soh_map = _get_soh_map()
    site_bc = soh_map.get("site_barcode", {})
    site_sku = soh_map.get("site_sku", {})
    from api.historical_movement import _extract_single_site_key

    rows: list[dict] = []
    for i, r in page_df.iterrows():
        sid = str(r["site_id"]).strip().upper().replace(".0", "")
        sname = str(r.get("site_name", "") or "").strip().upper()
        sku_code = str(r["sku"]).strip().upper()
        bc = str(r["barcode"]).strip().upper().replace(".0", "")
        sid_key = _extract_single_site_key(sid) or sid
        c_soh = site_bc.get((sid, bc))
        if c_soh is None:
            c_soh = site_bc.get((sid_key, bc))
        if c_soh is None:
            c_soh = site_sku.get((sid, sku_code))
        if c_soh is None:
            c_soh = site_sku.get((sid_key, sku_code))
        if c_soh is None and sname:
            c_soh = site_bc.get((sname, bc)) or site_sku.get((sname, sku_code))
        if c_soh is None:
            for d in re.findall(r"\b\d+\b", sid) + re.findall(r"\b\d+\b", sname):
                if (d, bc) in site_bc:
                    c_soh = site_bc[(d, bc)]
                    break
                if (d, sku_code) in site_sku:
                    c_soh = site_sku[(d, sku_code)]
                    break
        if c_soh is None:
            c_soh = 0.0

        mdq_val = float(r["mdq"]) if pd.notna(r.get("mdq")) and float(r.get("mdq", 0)) > 0 else None
        fill_rate_val = round((c_soh / mdq_val) * 100.0, 1) if mdq_val is not None and mdq_val > 0 else None
        dt = r.get("date")
        if pd.isna(dt):
            date_s = ""
        else:
            date_s = pd.Timestamp(dt).strftime("%Y-%m-%d")

        rows.append(
            {
                "id": f"{r['site_id']}|{r['barcode']}|{date_s}|{i}",
                "site_id": r["site_id"],
                "site_name": r["site_name"],
                "sku": r["sku"],
                "barcode": r["barcode"],
                "size": r["size"],
                "mrp": float(r["mrp"]),
                "qty": float(r["qty"]),
                "net_sale_amount": float(r["net_sale_amount"]),
                "discount": float(r["discount_pct"]),
                "discount_amount": float(r["discount_amount"]),
                "season": r["season"],
                "date": date_s,
                "channel": str(r.get("store_type", "") or ""),
                "sales_channel": str(r.get("sales_channel", "") or str(r.get("store_type", "") or "")),
                "zone": str(r.get("zone", "") or ""),
                "city": str(r.get("city", "") or ""),
                "state": str(r.get("state", "") or ""),
                "state_code": str(r.get("state_code", "") or ""),
                "cluster": str(r.get("cluster", "") or ""),
                "store_grade": str(r.get("store_grade", "") or ""),
                "store_category": str(r.get("store_category", "") or ""),
                "customer_name": str(r.get("customer_name", "") or ""),
                "store_season": str(r.get("store_season", "") or ""),
                "tax_region": str(r.get("tax_region", "") or ""),
                "slcode": str(r.get("slcode", "") or ""),
                "address": str(r.get("address", "") or ""),
                "gstin": str(r.get("gstin", "") or ""),
                "pincode": str(r.get("pincode", "") or ""),
                "country": str(r.get("country", "") or ""),
                "site_short_code": str(r.get("site_short_code", "") or ""),
                "store_type_master": str(r.get("store_type_master", "") or ""),
                "department": str(r.get("department", "") or ""),
                "section": str(r.get("section", "") or ""),
                "store_sqft": float(r["store_sqft"]) if pd.notna(r.get("store_sqft")) else None,
                "mdq": mdq_val,
                "tax_amount": float(r["tax_amount"]) if pd.notna(r.get("tax_amount")) else None,
                "current_stock": float(c_soh),
                "current_soh": float(c_soh),
                "soh": float(c_soh),
                "fill_rate": fill_rate_val,
            }
        )
    return rows


def _duckdb_sales_rows_response(
    *,
    year: int | None,
    site_id: str,
    site_ids: str,
    channel: str,
    month: str,
    sku: str,
    season: str,
    barcode: str,
    barcodes: str,
    departments: str,
    sections: str,
    zones: str,
    store_grades: str,
    states: str,
    cities: str,
    search: str,
    timeline_mode: str,
    timeline_values: str,
    page_num: int,
    size_num: int,
    sort_key: str,
    sort_direction: str,
    refresh: int,
) -> dict | None:
    """Return one table page from DuckDB. None means the caller should use the full frame."""
    from api.sales_year_loader import normalize_dashboard_year
    from api.services.analytics_query_engine import load_sales_page_duckdb, use_duckdb

    if not use_duckdb():
        return None
    year_val = normalize_dashboard_year(year, default=2027)
    tl_mode = _val_str(timeline_mode)
    tl_values = _val_str(timeline_values)
    if not tl_mode or not tl_values:
        tl_mode = "fy_year"
        tl_values = f"FY{year_val}"
    if tl_mode == "event":
        return None
    narrowed = _request_is_narrowed(
        site_id=site_id,
        site_ids=site_ids,
        channel=channel,
        month=month,
        sku=sku,
        season=season,
        barcode=barcode,
        barcodes=barcodes,
        departments=departments,
        sections=sections,
        zones=zones,
        store_grades=store_grades,
        states=states,
        cities=cities,
        search=search,
        timeline_mode=tl_mode,
    )
    cache_key = (
        f"rows-page-v1|{year_val}|{site_id}|{site_ids}|{channel}|{month}|{sku}|{season}|"
        f"{barcode}|{barcodes}|{departments}|{sections}|{zones}|{store_grades}|{states}|{cities}|"
        f"{search}|{tl_mode}|{tl_values}|{page_num}|{size_num}|{sort_key}|{sort_direction}"
    )
    saved = _saved_dashboard_page(
        "sales-dashboard-rows",
        cache_key,
        narrowed=narrowed,
        refresh=int(_val(refresh, 0)),
    )
    if saved is not None:
        return saved
    sort_col = _SALES_ROW_SORT.get(sort_key, "net_sale_amount")
    page_df, total, diag = load_sales_page_duckdb(
        fy_years=_fy_years_for_timeline(year_val=year_val, timeline_mode=tl_mode, timeline_values=tl_values),
        page=page_num,
        page_size=size_num,
        sort_col=sort_col,
        sort_dir=sort_direction,
        site_id=site_id,
        site_ids=site_ids,
        channel=channel,
        month=month,
        sku=sku,
        season=season,
        barcode=barcode,
        barcodes=barcodes,
        departments=departments,
        sections=sections,
        zones=zones,
        store_grades=store_grades,
        states=states,
        cities=cities,
        search=search,
        timeline_mode=tl_mode,
        timeline_values=tl_values,
    )
    payload = {
        "ok": True,
        "year": year_val,
        "channels": [],
        "stores": [],
        "months": [],
        "skus": [],
        "seasons": [],
        "barcodes": [],
        "total": total,
        "page": page_num,
        "page_size": size_num,
        "rows": _sales_table_records(page_df),
        "query_engine": "duckdb",
        "dataset_version": diag.get("dataset_version"),
    }
    _remember_dashboard_page("sales-dashboard-rows", cache_key, payload, narrowed=narrowed)
    return payload


def _sql_sales_rows_response(
    *,
    year: int | None,
    site_id: str,
    site_ids: str,
    channel: str,
    month: str,
    sku: str,
    season: str,
    barcode: str,
    barcodes: str,
    departments: str,
    sections: str,
    zones: str,
    store_grades: str,
    states: str,
    cities: str,
    search: str,
    timeline_mode: str,
    timeline_values: str,
    page_num: int,
    size_num: int,
    sort_key: str,
    sort_direction: str,
) -> dict | None:
    """One table page from Azure SQL (OFFSET/FETCH). None → not a filtered view / SQL off."""
    from api.azure_sql_analytics import sql_query_ready, sql_sales_page
    from api.sales_year_loader import normalize_dashboard_year

    year_val = normalize_dashboard_year(year, default=2027)
    tl_mode = _val_str(timeline_mode)
    tl_values = _val_str(timeline_values)
    if not tl_mode or not tl_values:
        tl_mode = "fy_year"
        tl_values = f"FY{year_val}"
    # Default first view stays on page-cache; SQL serves filter applies.
    if not _request_is_narrowed(
        site_id=site_id, site_ids=site_ids, channel=channel, month=month, sku=sku,
        season=season, barcode=barcode, barcodes=barcodes, departments=departments,
        sections=sections, zones=zones, store_grades=store_grades, states=states,
        cities=cities, search=search, timeline_mode=tl_mode,
    ):
        return None
    if not sql_query_ready():
        return None
    filt = dict(
        fy_years=_fy_years_for_timeline(year_val=year_val, timeline_mode=tl_mode, timeline_values=tl_values),
        site_id=site_id, site_ids=site_ids, channel=channel, month=month, sku=sku,
        season=season, barcode=barcode, barcodes=barcodes, departments=departments,
        sections=sections, zones=zones, store_grades=store_grades, states=states,
        cities=cities, search=search, timeline_mode=tl_mode, timeline_values=tl_values,
    )
    page_df, total = sql_sales_page(
        filt,
        page=page_num,
        page_size=size_num,
        sort_col=_SALES_ROW_SORT.get(sort_key, "net_sale_amount"),
        sort_dir=sort_direction,
    )
    return {
        "ok": True,
        "year": year_val,
        "channels": [],
        "stores": [],
        "months": [],
        "skus": [],
        "seasons": [],
        "barcodes": [],
        "total": total,
        "page": page_num,
        "page_size": size_num,
        "rows": _sales_table_records(page_df),
        "query_engine": "azure_sql",
    }


def _sales_rows_page(
    filtered: pd.DataFrame,
    page_num: int,
    size_num: int,
    sort: str,
    sort_dir: str,
) -> pd.DataFrame:
    """Return one sorted page. The rest of the filtered frame stays on the server."""
    start = max(0, (page_num - 1) * size_num)
    end = start + size_num
    if filtered is None or filtered.empty:
        return filtered.iloc[0:0] if filtered is not None else pd.DataFrame()
    col = _SALES_ROW_SORT.get(str(sort or "").strip(), "net_sale_amount")
    ascending = str(sort_dir or "desc").lower() == "asc"
    if col not in filtered.columns:
        if "date" in filtered.columns:
            col = "date"
            ascending = False
        else:
            return filtered.iloc[start:end]
    ordered = filtered.sort_values(col, ascending=ascending, kind="mergesort", na_position="last")
    return ordered.iloc[start:end]


@router.get("/rows")
def get_sales_dashboard_rows(
    year: int | None = Query(2027),
    site_id: str = Query("all"),
    site_ids: str = Query(""),
    channel: str = Query("all"),
    month: str = Query("all"),
    sku: str = Query("all"),
    season: str = Query("all"),
    barcode: str = Query("all"),
    barcodes: str = Query(""),
    departments: str = Query(""),
    sections: str = Query(""),
    zones: str = Query(""),
    store_grades: str = Query(""),
    states: str = Query(""),
    cities: str = Query(""),
    search: str = Query(""),
    timeline_mode: str = Query(""),
    timeline_values: str = Query(""),
    page: int = Query(1, ge=1),
    # FE cascade sample uses page_size=3000 for SKU/barcode/season options
    page_size: int = Query(50, ge=1, le=3000),
    sort: str = Query("sales", description="Column key from the sales table. Only this page is returned."),
    sort_dir: str = Query("desc"),
    include_options: int = Query(1, description="1 on first page. Later pages send 0 so option lists are not resent."),
    refresh: int = Query(0),
):
    """One page of table rows. Filter lists only when include_options=1."""
    page_num = int(_val(page, 1))
    size_num = int(_val(page_size, 50))
    sort_key = str(_val(sort, "sales") or "sales").strip()
    sort_direction = str(_val(sort_dir, "desc") or "desc").strip().lower()
    if sort_direction not in {"asc", "desc"}:
        sort_direction = "desc"
    include_opts = int(_val(include_options, 1) or 0) == 1
    if not include_opts:
        try:
            paged = _sql_sales_rows_response(
                year=year, site_id=site_id, site_ids=site_ids, channel=channel, month=month,
                sku=sku, season=season, barcode=barcode, barcodes=barcodes,
                departments=departments, sections=sections, zones=zones,
                store_grades=store_grades, states=states, cities=cities, search=search,
                timeline_mode=timeline_mode, timeline_values=timeline_values,
                page_num=page_num, size_num=size_num, sort_key=sort_key,
                sort_direction=sort_direction,
            )
            if paged is not None:
                return paged
        except Exception as exc:
            logger.warning("Azure SQL sales rows page failed; falling back: %s", exc)
        try:
            paged = _duckdb_sales_rows_response(
                year=year,
                site_id=site_id,
                site_ids=site_ids,
                channel=channel,
                month=month,
                sku=sku,
                season=season,
                barcode=barcode,
                barcodes=barcodes,
                departments=departments,
                sections=sections,
                zones=zones,
                store_grades=store_grades,
                states=states,
                cities=cities,
                search=search,
                timeline_mode=timeline_mode,
                timeline_values=timeline_values,
                page_num=page_num,
                size_num=size_num,
                sort_key=sort_key,
                sort_direction=sort_direction,
                refresh=int(_val(refresh, 0)),
            )
            if paged is not None:
                return paged
        except Exception as exc:
            logger.warning("paged sales rows failed; using full frame: %s", exc)
    ctx = _get_filtered_sales_context(
        year=year,
        site_id=site_id,
        site_ids=site_ids,
        channel=channel,
        month=month,
        sku=sku,
        season=season,
        barcode=barcode,
        barcodes=barcodes,
        departments=departments,
        sections=sections,
        zones=zones,
        store_grades=store_grades,
        states=states,
        cities=cities,
        search=search,
        timeline_mode=timeline_mode,
        timeline_values=timeline_values,
        refresh=refresh,
    )
    filtered: pd.DataFrame = ctx["filtered"]
    year_val = ctx["year_val"]
    tl_mode = ctx["tl_mode"]
    tl_values = ctx["tl_values"]
    base_kw = ctx["base_kw"]
    timeline_kw = dict(timeline_mode=tl_mode, timeline_values=tl_values)

    page_num = int(_val(page, 1))
    size_num = int(_val(page_size, 50))
    sort_key = str(_val(sort, "sales") or "sales").strip()
    sort_direction = str(_val(sort_dir, "desc") or "desc").strip().lower()
    if sort_direction not in {"asc", "desc"}:
        sort_direction = "desc"
    include_opts = int(_val(include_options, 1) or 0) == 1
    # Progressive FE /rows — must land on page_cache blob (morning + cold open).
    cache_key = (
        f"rows-v2|{ctx['ctx_key']}|{page_num}|{size_num}|{sort_key}|{sort_direction}|{int(include_opts)}"
    )
    narrowed = bool(ctx["has_active_filters"]) or _request_is_narrowed(timeline_mode=ctx["tl_mode"])
    saved = _saved_dashboard_page(
        "sales-dashboard-rows",
        cache_key,
        narrowed=narrowed,
        refresh=int(_val(refresh, 0)),
    )
    if saved is not None:
        return saved
    total = len(filtered)
    page_df = _sales_rows_page(filtered, page_num, size_num, sort_key, sort_direction)

    has_active_filters = ctx["has_active_filters"]
    if not include_opts:
        # Next page / sort / search: rows only. Do not rebuild filter lists or the FY frame.
        empty_scope = filtered.iloc[0:0]
        store_scope = empty_scope
        sku_scope = empty_scope
        barcode_scope = empty_scope
        season_scope = empty_scope
        full = filtered
    elif not has_active_filters:
        store_scope = filtered
        sku_scope = filtered
        barcode_scope = filtered
        season_scope = filtered
        full = filtered
    else:
        # Lazy-load timeline frame only for cascading filter option builders.
        # DuckDB KPI path sets full=filtered to avoid FY materialization on /kpis.
        full = ctx.get("full")
        if full is None or full is filtered or ctx.get("query_engine") == "duckdb":
            try:
                full = _load_sales_for_timeline(
                    year_val=year_val,
                    timeline_mode=tl_mode,
                    timeline_values=tl_values,
                    force_refresh=bool(int(_val(refresh, 0))),
                )
                ctx["full"] = full
            except Exception as exc:
                logger.warning("rows cascade full load failed; using filtered: %s", exc)
                full = filtered
        store_scope = _apply_timeline_filter(
            _apply_filters(full, **base_kw, exclude=frozenset({"site_id", "site_ids"})),
            **timeline_kw,
        )
        sku_scope = _apply_timeline_filter(
            _apply_filters(full, **base_kw, exclude=frozenset({"sku"})),
            **timeline_kw,
        )
        barcode_scope = _apply_timeline_filter(
            _apply_filters(full, **base_kw, exclude=frozenset({"barcode", "barcodes"})),
            **timeline_kw,
        )
        season_scope = _apply_timeline_filter(
            _apply_filters(full, **base_kw, exclude=frozenset({"season"})),
            **timeline_kw,
        )

    rows = _sales_table_records(page_df)

    payload = {
        "ok": True,
        "year": year_val,
        "channels": CHANNEL_OPTIONS if include_opts else [],
        "stores": _store_options(store_scope)[:500] if include_opts else [],
        "months": (
            _month_options(
                _apply_filters(full, **base_kw, exclude=frozenset({"month"})),
                year_val=year_val,
            )[:36]
            if include_opts
            else []
        ),
        "skus": _sku_options(sku_scope) if include_opts else [],
        "seasons": _season_options(season_scope) if include_opts else [],
        "barcodes": _barcode_options(barcode_scope) if include_opts else [],
        "total": total,
        "page": page_num,
        "page_size": size_num,
        "rows": rows,
        "query_engine": ctx.get("query_engine") or "pandas",
        "dataset_version": ctx.get("dataset_version"),
    }
    _remember_dashboard_page("sales-dashboard-rows", cache_key, payload, narrowed=narrowed)
    return payload


@router.get("")
def get_sales_dashboard(
    year: int | None = Query(
        2027,
        description="Calendar year / FY (2024, 2025, 2026, or 2027). Defaults to 2027.",
    ),
    site_id: str = Query("all", description="Site_ID filter or all"),
    site_ids: str = Query("", description="Comma-separated site IDs (from Customer Master filters)"),
    channel: str = Query("all", description="Store channel: PT/SSL/CENTRO/ICONIC/EBO or all"),
    month: str = Query("all", description="YYYY-MM or all"),
    sku: str = Query("all", description="SKU filter or all"),
    season: str = Query("all", description="Season filter or all"),
    barcode: str = Query("all", description="Barcode filter or all"),
    barcodes: str = Query("", description="Comma-separated barcodes"),
    departments: str = Query("", description="Comma-separated departments (Item Master)"),
    sections: str = Query("", description="Comma-separated sections (Item Master)"),
    zones: str = Query("", description="Comma-separated zones (North/South/East/West/Central)"),
    store_grades: str = Query("", description="Comma-separated store grades (A/B/C etc.)"),
    states: str = Query("", description="Comma-separated state names"),
    cities: str = Query("", description="Comma-separated city names"),
    search: str = Query("", description="Search SKU, barcode, site, season"),
    timeline_mode: str = Query("", description="Timeline mode: year|month|date|day|fy_year|fy_week|event"),
    timeline_values: str = Query("", description="Comma-separated timeline values (e.g. 2026 or 2026-01,2026-02)"),
    page: int = Query(1, ge=1),
    # FE cascade sample uses page_size=3000 for SKU/barcode/season options
    page_size: int = Query(50, ge=1, le=3000),
    refresh: int = Query(0, description="1 = bypass cache"),
):
    """Paginated sales lines. Defaults to FY27 (April 2026 through March 2027)."""
    from api.sales_year_loader import normalize_dashboard_year

    year_val: int = normalize_dashboard_year(year, default=2027)
    # Resolve timeline first so we know which FY slices to load (calendar year spans 2 FYs)
    tl_mode = _val_str(timeline_mode)
    tl_values = _val_str(timeline_values)
    if not tl_mode or not tl_values:
        tl_mode = "fy_year"
        tl_values = f"FY{year_val}"
    response_key = "|".join(
        str(part)
        for part in (
            # v5: site_ids match site_code / site_manual_code (zone filter parity with Home)
            "dm-soh-str-v5-site-identity",
            year_val, site_id, site_ids, channel, month, sku, season, barcode, barcodes,
            departments, sections, zones, store_grades, states, cities, search, tl_mode, tl_values, page, page_size,
        )
    )
    narrowed = _request_is_narrowed(
        site_id=site_id,
        site_ids=site_ids,
        channel=channel,
        month=month,
        sku=sku,
        season=season,
        barcode=barcode,
        barcodes=barcodes,
        departments=departments,
        sections=sections,
        zones=zones,
        store_grades=store_grades,
        states=states,
        cities=cities,
        search=search,
        timeline_mode=tl_mode,
    )
    saved = _saved_dashboard_page(
        "sales-dashboard", response_key, narrowed=narrowed, refresh=int(_val(refresh, 0))
    )
    if saved is not None:
        return saved
    try:
        full = _load_sales_for_timeline(
            year_val=year_val,
            timeline_mode=tl_mode,
            timeline_values=tl_values,
            force_refresh=bool(refresh),
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Sales dashboard load failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    filtered = _apply_filters(
        full,
        site_id=site_id,
        site_ids=site_ids,
        channel=channel,
        month=month,
        sku=sku,
        season=season,
        barcode=barcode,
        barcodes=barcodes,
        departments=departments,
        sections=sections,
        zones=zones,
        store_grades=store_grades,
        states=states,
        cities=cities,
        search=search,
    )
    filtered = _apply_timeline_filter(
        filtered,
        timeline_mode=tl_mode,
        timeline_values=tl_values,
    )
    # Newest first so page 1 is not stuck on oldest month (e.g. Jan when data is Jan–Jul)
    page_num = int(_val(page, 1))
    size_num = int(_val(page_size, 50))
    total = len(filtered)
    start = (page_num - 1) * size_num
    end = start + size_num
    if "date" in filtered.columns and not filtered.empty:
        # Sort only the date series; avoid copying every column of the full FY.
        positions = filtered["date"].reset_index(drop=True).sort_values(ascending=False, kind="mergesort").index[start:end]
        page_df = filtered.iloc[positions]
    else:
        page_df = filtered.iloc[start:end]

    base_kw = dict(
        site_id=site_id,
        site_ids=site_ids,
        channel=channel,
        month=month,
        sku=sku,
        season=season,
        barcode=barcode,
        barcodes=barcodes,
        departments=departments,
        sections=sections,
        zones=zones,
        store_grades=store_grades,
        states=states,
        cities=cities,
        search=search,
    )
    timeline_kw = dict(timeline_mode=tl_mode, timeline_values=tl_values)

    # Dropdown scopes: each list follows ALL other active filters (cascading).
    # e.g. season=SS-26 → sku/barcode options only from SS-26 rows.
    has_active_filters = any([
        site_id and site_id != "all",
        site_ids,
        channel and channel != "all",
        month and month != "all",
        sku and sku != "all",
        season and season != "all",
        barcode and barcode != "all",
        barcodes,
        departments,
        sections,
        zones,
        store_grades,
        states,
        cities,
        search,
    ])
    if not has_active_filters:
        store_scope = filtered
        sku_scope = filtered
        barcode_scope = filtered
        season_scope = filtered
    else:
        store_scope = _apply_timeline_filter(
            _apply_filters(full, **base_kw, exclude=frozenset({"site_id", "site_ids"})),
            **timeline_kw,
        )
        sku_scope = _apply_timeline_filter(
            _apply_filters(full, **base_kw, exclude=frozenset({"sku"})),
            **timeline_kw,
        )
        barcode_scope = _apply_timeline_filter(
            _apply_filters(full, **base_kw, exclude=frozenset({"barcode", "barcodes"})),
            **timeline_kw,
        )
        # Season list follows channel/site/dept filters, but not the selected season itself
        season_scope = _apply_timeline_filter(
            _apply_filters(full, **base_kw, exclude=frozenset({"season"})),
            **timeline_kw,
        )

    store_options = _store_options(store_scope)[:500]
    # Full month list — ignore timeline so Month / Timeline Month always list
    # every month in the sales file (not shrunk by selected timeline values).
    month_options = _month_options(
        _apply_filters(full, **base_kw, exclude=frozenset({"month"})),
        year_val=year_val,
    )[:36]
    sku_options = _sku_options(sku_scope)
    barcode_options = _barcode_options(barcode_scope)
    season_options = _season_options(season_scope)

    soh_map = _get_soh_map()
    site_bc = soh_map.get("site_barcode", {})
    site_sku = soh_map.get("site_sku", {})
    site_tot = soh_map.get("site", {})

    insights_data = _insights(filtered)

    # Full calendar / FY year views are already heavy. Loading another year for
    # SPLY was causing Azure gateway empty 502s. Skip LY when the frame is large
    # so the year still opens with sales + KPIs.
    sply_prior = pd.DataFrame()
    ly_metrics: dict[str, Any] = {}
    large_year_view = len(filtered) >= 150_000 or (
        str(tl_mode or "").strip().lower() in ("year", "fy_year", "fy", "financial_year")
        and not has_active_filters
    )
    if not large_year_view:
        try:
            sply_prior = _load_sply_prior_df(
                filtered, filter_kw=base_kw, timeline_mode=tl_mode, timeline_values=tl_values
            )
        except Exception as exc:
            logger.warning("SPLY prior load skipped: %s", exc)
            sply_prior = pd.DataFrame()

        if sply_prior is not None and not sply_prior.empty:
            try:
                ly_df = sply_prior

                def _get_ly_dict(col: str):
                    if col not in ly_df.columns:
                        return {}
                    g = ly_df.groupby(col, dropna=False)["net_sale_amount"].sum()
                    return {
                        str(k).strip() if pd.notna(k) else "Unspecified": float(v)
                        for k, v in g.items()
                    }

                ly_metrics = {
                    "zone": _get_ly_dict("zone"),
                    "cluster": _get_ly_dict("cluster"),
                    "site_id": _get_ly_dict("site_id"),
                    "sales_channel": _get_ly_dict("store_type"),
                    "department": _get_ly_dict("department"),
                    "section": _get_ly_dict("section"),
                    "city_name": _get_ly_dict("city"),
                    "state_name": _get_ly_dict("state"),
                    "store_grade": _get_ly_dict("store_grade"),
                    "month": _get_ly_dict("month_key"),
                    "total": float(ly_df["net_sale_amount"].sum()),
                }
            except Exception as e:
                logger.warning("Failed to compute LY metrics: %s", e)
    else:
        logger.info(
            "SPLY skipped for large year view (rows=%s mode=%s) so the page can return",
            len(filtered),
            tl_mode,
        )

    insights_data["ly_metrics"] = ly_metrics

    rows = []
    for i, r in page_df.iterrows():
        sid = str(r["site_id"]).strip().upper().replace(".0", "")
        sname = str(r.get("site_name", "") or "").strip().upper()
        sku_code = str(r["sku"]).strip().upper()
        bc = str(r["barcode"]).strip().upper().replace(".0", "")

        # Current stock from daily_inventory_movement (store×barcode, then store×sku)
        from api.historical_movement import _extract_single_site_key

        sid_key = _extract_single_site_key(sid) or sid
        c_soh = site_bc.get((sid, bc))
        if c_soh is None:
            c_soh = site_bc.get((sid_key, bc))
        if c_soh is None:
            c_soh = site_sku.get((sid, sku_code))
        if c_soh is None:
            c_soh = site_sku.get((sid_key, sku_code))
        if c_soh is None and sname:
            c_soh = site_bc.get((sname, bc)) or site_sku.get((sname, sku_code))
        if c_soh is None:
            for d in re.findall(r"\b\d+\b", sid) + re.findall(r"\b\d+\b", sname):
                if (d, bc) in site_bc:
                    c_soh = site_bc[(d, bc)]
                    break
                if (d, sku_code) in site_sku:
                    c_soh = site_sku[(d, sku_code)]
                    break
        if c_soh is None:
            c_soh = 0.0

        mdq_val = float(r["mdq"]) if pd.notna(r.get("mdq")) and float(r.get("mdq", 0)) > 0 else None
        fill_rate_val = round((c_soh / mdq_val) * 100.0, 1) if mdq_val is not None and mdq_val > 0 else None

        rows.append(
            {
                "id": f"{r['site_id']}|{r['barcode']}|{r['date'].date()}|{i}",
                "site_id": r["site_id"],
                "site_name": r["site_name"],
                "sku": r["sku"],
                "barcode": r["barcode"],
                "size": r["size"],
                "mrp": float(r["mrp"]),
                "qty": float(r["qty"]),
                "net_sale_amount": float(r["net_sale_amount"]),
                "discount": float(r["discount_pct"]),
                "discount_amount": float(r["discount_amount"]),
                "season": r["season"],
                "date": r["date"].strftime("%Y-%m-%d"),
                "channel": str(r.get("store_type", "") or ""),
                "sales_channel": str(r.get("sales_channel", "") or str(r.get("store_type", "") or "")),
                # Customer Master dims
                "zone": str(r.get("zone", "") or ""),
                "city": str(r.get("city", "") or ""),
                "state": str(r.get("state", "") or ""),
                "state_code": str(r.get("state_code", "") or ""),
                "cluster": str(r.get("cluster", "") or ""),
                "store_grade": str(r.get("store_grade", "") or ""),
                "store_category": str(r.get("store_category", "") or ""),
                "customer_name": str(r.get("customer_name", "") or ""),
                "store_season": str(r.get("store_season", "") or ""),
                "tax_region": str(r.get("tax_region", "") or ""),
                "slcode": str(r.get("slcode", "") or ""),
                "address": str(r.get("address", "") or ""),
                "gstin": str(r.get("gstin", "") or ""),
                "pincode": str(r.get("pincode", "") or ""),
                "country": str(r.get("country", "") or ""),
                "site_short_code": str(r.get("site_short_code", "") or ""),
                "store_type_master": str(r.get("store_type_master", "") or ""),
                "department": str(r.get("department", "") or ""),
                "section": str(r.get("section", "") or ""),
                "store_sqft": float(r["store_sqft"]) if pd.notna(r.get("store_sqft")) else None,
                "mdq": mdq_val,
                "tax_amount": float(r["tax_amount"]) if pd.notna(r.get("tax_amount")) else None,
                "current_stock": float(c_soh),
                "current_soh": float(c_soh),
                "soh": float(c_soh),
                "fill_rate": fill_rate_val,
            }
        )

    payload = {
        "ok": True,
        "year": year_val,
        "filters": {
            "year": year_val,
            "site_id": site_id,
            "site_ids": site_ids,
            "channel": channel,
            "month": month,
            "sku": sku,
            "season": season,
            "barcode": barcode,
            "barcodes": barcodes,
            "departments": departments,
            "sections": sections,
            "search": search,
            "timeline_mode": tl_mode,
            "timeline_values": tl_values,
        },
        "channels": CHANNEL_OPTIONS,
        "stores": store_options,
        "months": month_options,
        "skus": sku_options,
        "seasons": season_options,
        "barcodes": barcode_options,
        "kpis": _attach_growth_kpis(
            _kpis(
                filtered,
                soh_map=soh_map,
                df_prior=sply_prior,
                scope={
                    "season": season,
                    "sku": sku,
                    "barcode": barcode,
                    "barcodes": barcodes,
                    "departments": departments,
                    "sections": sections,
                    "channel": channel,
                    "zones": zones,
                    "site_id": site_id,
                    "site_ids": site_ids,
                },
            ),
            insights_data,
        ),
        "insights": insights_data,
        "total": total,
        "page": page,
        "page_size": page_size,
        "rows": rows,
    }
    _remember_dashboard_page("sales-dashboard", response_key, payload, narrowed=narrowed)
    if len(_DASH_RESPONSE_CACHE) > 24:
        oldest = min(_DASH_RESPONSE_CACHE, key=lambda k: _DASH_RESPONSE_CACHE[k][0])
        _DASH_RESPONSE_CACHE.pop(oldest, None)
    return payload


def _get_pivot_dim_series(df_part: pd.DataFrame, dim_name: str) -> pd.Series:
    """Map dimension key to string Series for pivot aggregation."""
    d = str(dim_name or "").strip().lower()
    if d == "zone":
        s = df_part.get("zone", pd.Series("", index=df_part.index))
        return s.astype(str).str.strip().replace({"": "Unspecified", "nan": "Unspecified", "None": "Unspecified"})
    elif d in ("sales_channel", "channel", "format"):
        # Use sales_channel column (derived from store_name/channel/store_code) instead of raw store_type
        s = df_part.get("sales_channel", df_part.get("store_type", pd.Series("", index=df_part.index)))
        return s.astype(str).str.strip().replace({"": "Unspecified", "nan": "Unspecified", "None": "Unspecified"})
    elif d in ("site_id", "store"):
        sid = df_part.get("site_id", pd.Series("", index=df_part.index)).astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
        sname = df_part.get("site_name", pd.Series("", index=df_part.index)).astype(str).str.strip()
        has_name = sname.ne("") & sname.str.lower().ne("nan") & sname.str.lower().ne("none")
        return sid.where(~has_name, sid + " - " + sname).replace({"": "Unspecified", "nan": "Unspecified"})
    elif d in ("state_name", "state"):
        s = df_part.get("state", pd.Series("", index=df_part.index))
        return s.astype(str).str.strip().replace({"": "Unspecified", "nan": "Unspecified", "None": "Unspecified"})
    elif d in ("city_name", "city"):
        s = df_part.get("city", pd.Series("", index=df_part.index))
        return s.astype(str).str.strip().replace({"": "Unspecified", "nan": "Unspecified", "None": "Unspecified"})
    elif d == "store_grade":
        s = df_part.get("store_grade", pd.Series("", index=df_part.index))
        return s.astype(str).str.strip().replace({"": "Unspecified", "nan": "Unspecified", "None": "Unspecified"})
    elif d == "cluster":
        s = df_part.get("cluster", pd.Series("", index=df_part.index))
        return s.astype(str).str.strip().replace({"": "Unspecified", "nan": "Unspecified", "None": "Unspecified"})
    elif d == "department":
        s = df_part.get("department", pd.Series("", index=df_part.index))
        return s.astype(str).str.strip().replace({"": "Unspecified", "nan": "Unspecified", "None": "Unspecified"})
    elif d == "section":
        s = df_part.get("section", pd.Series("", index=df_part.index))
        return s.astype(str).str.strip().replace({"": "Unspecified", "nan": "Unspecified", "None": "Unspecified"})
    elif d == "month":
        if "date" in df_part.columns and df_part["date"].notna().any():
            return df_part["date"].dt.strftime("%b '%y").fillna("Unspecified")
        s = df_part.get("month_key", pd.Series("", index=df_part.index))
        return s.astype(str).str.strip().replace({"": "Unspecified", "nan": "Unspecified"})
    elif d == "fy_week":
        if "date" in df_part.columns:
            return df_part["date"].map(lambda dt: _fy_week_key(dt) if pd.notna(dt) else "WK-01")
        return pd.Series("WK-01", index=df_part.index)
    elif d == "size":
        s = df_part.get("size", pd.Series("", index=df_part.index))
        return s.astype(str).str.strip().replace({"": "Unspecified", "nan": "Unspecified", "None": "Unspecified"})
    elif d == "sku":
        s = df_part.get("sku", pd.Series("", index=df_part.index))
        return s.astype(str).str.strip().replace({"": "Unspecified", "nan": "Unspecified", "None": "Unspecified"})
    elif d == "customer_name":
        s = df_part.get("customer_name", pd.Series("", index=df_part.index))
        return s.astype(str).str.strip().replace({"": "Unspecified", "nan": "Unspecified", "None": "Unspecified"})
    return pd.Series("Unspecified", index=df_part.index)


_PIVOT_CACHE: dict[str, tuple[float, dict]] = {}


@router.get("/pivot")
def get_sales_pivot(
    year: int | None = Query(None, description="Calendar year"),
    site_id: str = Query("all", description="Site_ID filter or all"),
    site_ids: str = Query("", description="Comma-separated site IDs"),
    channel: str = Query("all", description="Store channel: PT/SSL/CENTRO/ICONIC/EBO or all"),
    month: str = Query("all", description="YYYY-MM or all"),
    sku: str = Query("all", description="SKU filter or all"),
    season: str = Query("all", description="Season filter or all"),
    barcode: str = Query("all", description="Barcode filter or all"),
    barcodes: str = Query("", description="Comma-separated barcodes"),
    departments: str = Query("", description="Comma-separated departments"),
    sections: str = Query("", description="Comma-separated sections"),
    search: str = Query("", description="Search SKU, barcode, site, season"),
    timeline_mode: str = Query("", description="Timeline mode"),
    timeline_values: str = Query("", description="Timeline values"),
    primary_dim: str = Query("zone", description="Primary dimension"),
    secondary_dim: str = Query("none", description="Secondary dimension"),
    tertiary_dim: str = Query("none", description="Tertiary dimension"),
    column_split: str = Query("none", description="Column split dimension: none|sales_channel|month"),
    refresh: int = Query(0, description="1 = bypass cache"),
):
    """
    Server-side Pivot Table Aggregation across the FULL sales dataset.
    Returns hierarchically grouped totals, SOH, MDQ, LY Sales, and split matrices.
    """
    cache_key = f"pivot:v2:{year}:{site_id}:{site_ids}:{channel}:{month}:{sku}:{season}:{barcode}:{barcodes}:{departments}:{sections}:{search}:{timeline_mode}:{timeline_values}:{primary_dim}:{secondary_dim}:{tertiary_dim}:{column_split}"
    now = time.time()
    narrowed = _request_is_narrowed(
        site_id=site_id,
        site_ids=site_ids,
        channel=channel,
        month=month,
        sku=sku,
        season=season,
        barcode=barcode,
        barcodes=barcodes,
        departments=departments,
        sections=sections,
        search=search,
        timeline_mode=timeline_mode,
    )
    if not refresh and not narrowed and cache_key in _PIVOT_CACHE:
        cache_at, cached_res = _PIVOT_CACHE[cache_key]
        if (now - cache_at) < 180:
            return cached_res
    if not refresh and not narrowed:
        from api.page_response_cache import load_page_cache, rebuilding_page_cache

        if not rebuilding_page_cache():
            disk_pivot = load_page_cache("sales-pivot", cache_key)
            if disk_pivot is not None:
                _PIVOT_CACHE[cache_key] = (now, disk_pivot)
                return disk_pivot

    empty_response = {
        "ok": True,
        "groups": [],
        "grand_total": {
            "total_sales": 0,
            "total_qty": 0,
            "total_soh": 0,
            "total_gross": 0,
            "total_discount": 0,
            "total_mdq": 0,
            "ly_sales": 0,
            "yoyPct": 0,
            "target": 0,
            "achvPct": 0,
            "asp": 0,
            "discPct": 0,
        },
        "split_col_keys": [],
    }

    try:
        from api.sales_year_loader import (
            current_indian_fy_date_range,
            get_default_dashboard_year,
            normalize_dashboard_year,
        )

        year_val: int = normalize_dashboard_year(year, default=get_default_dashboard_year())
        tl_mode = str(timeline_mode or "").strip()
        tl_values = str(timeline_values or "").strip()
        if year_val is not None and not tl_mode and not tl_values:
            tl_mode = "date"
            fy_from, fy_to = current_indian_fy_date_range()
            tl_values = f"{fy_from},{fy_to}"

        try:
            full = _load_sales_for_timeline(
                year_val=year_val,
                timeline_mode=tl_mode,
                timeline_values=tl_values,
                force_refresh=bool(refresh),
            )
        except Exception as exc:
            logger.warning("Sales dashboard load failed for pivot: %s", exc)
            return empty_response

        filtered = _apply_filters(
            full,
            site_id=site_id,
            site_ids=site_ids,
            channel=channel,
            month=month,
            sku=sku,
            season=season,
            barcode=barcode,
            barcodes=barcodes,
            departments=departments,
            sections=sections,
            search=search,
        )
        filtered = _apply_timeline_filter(
            filtered,
            timeline_mode=tl_mode,
            timeline_values=tl_values,
        )

        if filtered.empty:
            _PIVOT_CACHE[cache_key] = (now, empty_response)
            return empty_response

        work = filtered.copy()
        work["_gross"] = (work["mrp"] * work["qty"]).clip(lower=0)
        work["_net"] = work["net_sale_amount"]
        work["_qty"] = work["qty"]
        work["_disc"] = work["discount_amount"]

        work["_p_dim"] = _get_pivot_dim_series(work, primary_dim)

        has_sec = secondary_dim != "none" and secondary_dim != primary_dim
        has_tert = tertiary_dim != "none" and tertiary_dim != secondary_dim and tertiary_dim != primary_dim
        has_split = column_split != "none"

        if has_sec:
            work["_s_dim"] = _get_pivot_dim_series(work, secondary_dim)
        if has_tert:
            work["_t_dim"] = _get_pivot_dim_series(work, tertiary_dim)
        if has_split:
            work["_c_dim"] = _get_pivot_dim_series(work, column_split)

        soh_map = _get_soh_map()
        # site_soh: {site_id -> {"total_soh": float}} for pivot SOH lookup
        # Build multiple key variants so sales site_id always finds DM store SOH
        _raw_site_soh = soh_map.get("site", {})
        site_soh: dict[str, dict] = {}
        for sid, v in _raw_site_soh.items():
            entry = {"total_soh": v, "mdq": 0.0}
            s = str(sid).strip()
            site_soh[s] = entry
            site_soh[s.upper()] = entry
            site_soh[s.lower()] = entry
            if s.isdigit():
                site_soh[str(int(s))] = entry     # strip leading zeros: "04" -> "4"
                site_soh[s.zfill(3)] = entry       # pad to 3 digits: "4" -> "004"

        def _soh_for_site(sid: str) -> float:
            s = str(sid or "").strip()
            if not s:
                return 0.0
            hit = site_soh.get(s) or site_soh.get(s.upper()) or site_soh.get(s.lower())
            if hit is None and s.isdigit():
                hit = site_soh.get(str(int(s))) or site_soh.get(s.zfill(3))
            if not hit:
                return 0.0
            try:
                return float(hit.get("total_soh") or 0.0)
            except (TypeError, ValueError):
                return 0.0

        ly_dim_map: dict[str, float] = {}
        ly_grand_sales = 0.0
        try:
            ly_filter_kw = dict(
                site_id=site_id,
                site_ids=site_ids,
                channel=channel,
                sku=sku,
                season=season,
                barcode=barcode,
                barcodes=barcodes,
                departments=departments,
                sections=sections,
                search=search,
            )
            ly_df = _load_ly_sales_for_timeline(
                year_val=year_val,
                timeline_mode=tl_mode,
                timeline_values=tl_values,
                filter_kw=ly_filter_kw,
            )
            if not ly_df.empty:
                ly_work = ly_df.copy()
                ly_work["_p_dim"] = _get_pivot_dim_series(ly_work, primary_dim)
                ly_grp = ly_work.groupby("_p_dim", observed=True)["net_sale_amount"].sum()
                ly_dim_map = ly_grp.to_dict()
                ly_grand_sales = float(ly_work["net_sale_amount"].sum())
        except Exception as exc:
            logger.debug("LY sales pivot lookup failed: %s", exc)

        split_col_keys: list[str] = []
        if has_split:
            split_col_keys = [str(k) for k in work["_c_dim"].unique() if str(k) != "Unspecified"]
            split_col_keys.sort()
            if "Unspecified" in work["_c_dim"].values:
                split_col_keys.append("Unspecified")

        grand_sales = float(work["_net"].sum())
        grand_qty = float(work["_qty"].sum())
        grand_gross = float(work["_gross"].sum())
        grand_disc = float(work["_disc"].sum())
        grand_soh = 0.0
        grand_mdq = 0.0
        if primary_dim in ("site_id", "site_name"):
            for sid in work["site_id"].unique():
                grand_soh += _soh_for_site(str(sid))

        grand_asp = round(grand_sales / grand_qty, 1) if grand_qty > 0 else 0.0
        grand_disc_pct = round(100.0 * grand_disc / grand_gross, 1) if grand_gross > 0 else 0.0
        grand_yoy_pct = round(100.0 * (grand_sales - ly_grand_sales) / ly_grand_sales, 1) if ly_grand_sales > 0 else 0.0

        groups = []
        p_grouped = work.groupby("_p_dim", observed=True)

        for p_val, p_df in p_grouped:
            p_sales = float(p_df["_net"].sum())
            p_qty = float(p_df["_qty"].sum())
            p_gross = float(p_df["_gross"].sum())
            p_disc = float(p_df["_disc"].sum())
            p_asp = round(p_sales / p_qty, 1) if p_qty > 0 else 0.0
            p_disc_pct = round(100.0 * p_disc / p_gross, 1) if p_gross > 0 else 0.0

            p_ly_sales = float(ly_dim_map.get(p_val, 0.0))
            p_yoy_pct = round(100.0 * (p_sales - p_ly_sales) / p_ly_sales, 1) if p_ly_sales > 0 else 0.0

            p_soh = 0.0
            p_mdq = 0.0
            if primary_dim in ("site_id", "site_name"):
                for sid in p_df["site_id"].unique():
                    p_soh += _soh_for_site(str(sid))
            else:
                if "site_id" in p_df.columns:
                    for sid in p_df["site_id"].unique():
                        p_soh += _soh_for_site(str(sid))

            p_split = {}
            if has_split:
                c_grp = p_df.groupby("_c_dim", observed=True).agg(
                    sales=("_net", "sum"),
                    qty=("_qty", "sum"),
                )
                for c_val, c_row in c_grp.iterrows():
                    p_split[str(c_val)] = {
                        "sales": round(float(c_row["sales"]), 2),
                        "qty": int(c_row["qty"]),
                    }

            sub_groups = []
            if has_sec:
                s_grouped = p_df.groupby("_s_dim", observed=True)
                for s_val, s_df in s_grouped:
                    s_sales = float(s_df["_net"].sum())
                    s_qty = float(s_df["_qty"].sum())
                    s_gross = float(s_df["_gross"].sum())
                    s_disc = float(s_df["_disc"].sum())
                    s_asp = round(s_sales / s_qty, 1) if s_qty > 0 else 0.0
                    s_disc_pct = round(100.0 * s_disc / s_gross, 1) if s_gross > 0 else 0.0

                    s_soh = 0.0
                    s_mdq = 0.0
                    if secondary_dim in ("site_id", "site_name"):
                        for sid in s_df["site_id"].unique():
                            s_soh += _soh_for_site(str(sid))
                    else:
                        if "site_id" in s_df.columns:
                            for sid in s_df["site_id"].unique():
                                s_soh += _soh_for_site(str(sid))

                    s_split = {}
                    if has_split:
                        sc_grp = s_df.groupby("_c_dim", observed=True).agg(
                            sales=("_net", "sum"),
                            qty=("_qty", "sum"),
                        )
                        for c_val, c_row in sc_grp.iterrows():
                            s_split[str(c_val)] = {
                                "sales": round(float(c_row["sales"]), 2),
                                "qty": int(c_row["qty"]),
                            }

                    sub_sub_groups = []
                    if has_tert:
                        t_grouped = s_df.groupby("_t_dim", observed=True)
                        for t_val, t_df in t_grouped:
                            t_sales = float(t_df["_net"].sum())
                            t_qty = float(t_df["_qty"].sum())
                            t_gross = float(t_df["_gross"].sum())
                            t_disc = float(t_df["_disc"].sum())
                            t_asp = round(t_sales / t_qty, 1) if t_qty > 0 else 0.0
                            t_disc_pct = round(100.0 * t_disc / t_gross, 1) if t_gross > 0 else 0.0

                            t_soh = 0.0
                            t_mdq = 0.0
                            if tertiary_dim in ("site_id", "site_name"):
                                for sid in t_df["site_id"].unique():
                                    t_soh += _soh_for_site(str(sid))
                            else:
                                if "site_id" in t_df.columns:
                                    for sid in t_df["site_id"].unique():
                                        t_soh += _soh_for_site(str(sid))

                            t_split = {}
                            if has_split:
                                tc_grp = t_df.groupby("_c_dim", observed=True).agg(
                                    sales=("_net", "sum"),
                                    qty=("_qty", "sum"),
                                )
                                for c_val, c_row in tc_grp.iterrows():
                                    t_split[str(c_val)] = {
                                        "sales": round(float(c_row["sales"]), 2),
                                        "qty": int(c_row["qty"]),
                                    }

                            sub_sub_groups.append({
                                "key": f"{p_val}|{s_val}|{t_val}",
                                "label": str(t_val),
                                "total_sales": round(t_sales, 2),
                                "total_qty": int(t_qty),
                                "gross_sales": round(t_gross, 2),
                                "discount_amount": round(t_disc, 2),
                                "total_soh": round(t_soh, 2),
                                "total_mdq": round(t_mdq, 2),
                                "asp": t_asp,
                                "discPct": t_disc_pct,
                                "splitMetrics": t_split,
                            })

                    sub_groups.append({
                        "key": f"{p_val}|{s_val}",
                        "label": str(s_val),
                        "total_sales": round(s_sales, 2),
                        "total_qty": int(s_qty),
                        "gross_sales": round(s_gross, 2),
                        "discount_amount": round(s_disc, 2),
                        "total_soh": round(s_soh, 2),
                        "total_mdq": round(s_mdq, 2),
                        "asp": s_asp,
                        "discPct": s_disc_pct,
                        "splitMetrics": s_split,
                        "children": sub_sub_groups,
                    })

            groups.append({
                "key": str(p_val),
                "label": str(p_val),
                "total_sales": round(p_sales, 2),
                "total_qty": int(p_qty),
                "gross_sales": round(p_gross, 2),
                "discount_amount": round(p_disc, 2),
                "total_soh": round(p_soh, 2),
                "total_mdq": round(p_mdq, 2),
                "ly_sales": round(p_ly_sales, 2),
                "yoyPct": p_yoy_pct,
                "asp": p_asp,
                "discPct": p_disc_pct,
                "splitMetrics": p_split,
                "children": sub_groups,
            })

        groups.sort(key=lambda g: g["total_sales"], reverse=True)

        res = {
            "ok": True,
            "groups": groups,
            "grand_total": {
                "total_sales": round(grand_sales, 2),
                "total_qty": int(grand_qty),
                "total_gross": round(grand_gross, 2),
                "total_discount": round(grand_disc, 2),
                "total_soh": round(grand_soh, 2),
                "total_mdq": round(grand_mdq, 2),
                "ly_sales": round(ly_grand_sales, 2),
                "yoyPct": grand_yoy_pct,
                "asp": grand_asp,
                "discPct": grand_disc_pct,
            },
            "split_col_keys": split_col_keys,
        }
        if not narrowed:
            _PIVOT_CACHE[cache_key] = (now, res)
            from api.page_response_cache import save_page_cache

            save_page_cache("sales-pivot", cache_key, res)
        return res

    except Exception as exc:
        logger.exception("Error calculating pivot aggregation: %s", exc)
        return empty_response


@router.get("/financial-calendar")
def get_financial_calendar():
    from api.financial_calendar import calendar_options
    try:
        return {"ok": True, **calendar_options()}
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


_SALES_SITE_KEYS_CACHE: tuple[str, frozenset[str]] | None = None
_SALES_SITE_KEYS_LOCK = threading.Lock()


def _sales_present_site_keys() -> frozenset[str]:
    """Identity keys that actually appear in Sales Master (for filter dropdowns)."""
    global _SALES_SITE_KEYS_CACHE
    path, cache_key = _resolve_sales_master_path()
    try:
        sig = f"{cache_key or ''}|{_source_signature(path) if path else 'none'}"
    except Exception:
        sig = f"{cache_key or ''}|unknown"
    with _SALES_SITE_KEYS_LOCK:
        if _SALES_SITE_KEYS_CACHE is not None and _SALES_SITE_KEYS_CACHE[0] == sig:
            return _SALES_SITE_KEYS_CACHE[1]
    keys: set[str] = set()
    try:
        df = _get_sales_df(force_refresh=False)
    except Exception as exc:
        logger.warning("sales site keys: sales load failed: %s", exc)
        return frozenset()
    if df is None or df.empty:
        return frozenset()
    from api.historical_movement import _extract_single_site_key

    junk = {"", "NAN", "NONE", "NULL", "NAT", "<NA>", "ALL", "0", "0.0"}
    for col in ("site_manual_code", "site_code", "site_short_code", "site_id", "site_name"):
        if col not in df.columns:
            continue
        series = (
            df[col]
            .dropna()
            .astype(str)
            .str.strip()
            .str.upper()
            .str.replace(r"\.0$", "", regex=True)
        )
        for v in series.unique():
            vu = str(v or "").strip().upper()
            if not vu or vu in junk:
                continue
            keys.add(vu)
            ek = _extract_single_site_key(vu)
            if ek:
                keys.add(str(ek).strip().upper())
    frozen = frozenset(keys)
    with _SALES_SITE_KEYS_LOCK:
        _SALES_SITE_KEYS_CACHE = (sig, frozen)
    logger.info("Sales-present site identity keys: %s", len(frozen))
    return frozen


def _cm_site_has_sales(site: dict[str, Any], sales_keys: frozenset[str]) -> bool:
    if not sales_keys:
        return True
    from api.historical_movement import _extract_single_site_key

    for raw in (site.get("site_key"), site.get("site_code"), site.get("site_name")):
        s = str(raw or "").strip().upper().replace(".0", "")
        if not s:
            continue
        if s in sales_keys:
            return True
        ek = _extract_single_site_key(s)
        if ek and str(ek).strip().upper() in sales_keys:
            return True
    return False


def _rebuild_cm_options_from_sites(sites: list[dict[str, Any]], prev_options: dict[str, Any] | None) -> dict[str, Any]:
    prev = prev_options or {}

    def _uniq(vals: list[str]) -> list[str]:
        junk = {"", "nan", "none", "null", "<na>", "unassigned", "-", "--"}
        out = {str(v).strip() for v in vals if str(v).strip() and str(v).strip().lower() not in junk}
        return sorted(out, key=str.casefold)

    cats = _uniq([str(s.get("store_category") or "") for s in sites])
    channels = _uniq([str(s.get("channel") or "") for s in sites])
    grades = _uniq([str(s.get("store_grade") or "") for s in sites])
    # Keep canonical channel order when possible
    channel_order = ["PT", "SSL", "CENTRO", "ICONIC", "EBO"]
    channels_sorted = [c for c in channel_order if c in set(channels)] + [
        c for c in channels if c not in channel_order
    ]
    return {
        "zones": _uniq([str(s.get("zone") or "") for s in sites]),
        "siteCodes": _uniq([str(s.get("site_code") or "") for s in sites]),
        "siteNames": _uniq([str(s.get("site_name") or "") for s in sites]),
        "storeCategories": cats or list(prev.get("storeCategories") or []),
        "salesChannels": channels_sorted or list(prev.get("salesChannels") or []),
        "customerFormats": channels_sorted or list(prev.get("customerFormats") or []),
        "customerNames": _uniq([str(s.get("customer_name") or "") for s in sites]),
        "cities": _uniq([str(s.get("city") or "") for s in sites]),
        "states": _uniq([str(s.get("state") or "") for s in sites]),
        "clusterRm": _uniq([str(s.get("cluster") or "") for s in sites]),
        "storeGrades": grades or list(prev.get("storeGrades") or []),
    }


def _restrict_customer_master_to_sales(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop CM sites with no Sales Master activity so filters match real data."""
    sites = list(payload.get("sites") or [])
    if not sites:
        return payload
    sales_keys = _sales_present_site_keys()
    if not sales_keys:
        # Don't wipe the roster if sales failed to load.
        return payload
    kept = [s for s in sites if isinstance(s, dict) and _cm_site_has_sales(s, sales_keys)]
    out = dict(payload)
    out["sites"] = kept
    out["options"] = _rebuild_cm_options_from_sites(kept, payload.get("options") if isinstance(payload.get("options"), dict) else {})
    out["row_count"] = len(kept)
    out["sales_filtered"] = True
    out["sales_filtered_from"] = len(sites)
    out["sales_filtered_to"] = len(kept)
    return out


def _product_filters_are_default(**kw: str) -> bool:
    for key in ("site_id", "site_ids", "channel", "month", "departments", "sections", "zones", "store_grades", "states", "cities"):
        raw = str(kw.get(key) or "").strip().lower()
        if raw and raw not in {"all", "none"}:
            return False
    return True


@router.get("/product-filter-options")
def get_product_filter_options(
    year: int | None = Query(None),
    site_id: str = Query("all"),
    site_ids: str = Query(""),
    channel: str = Query("all"),
    month: str = Query("all"),
    departments: str = Query(""),
    sections: str = Query(""),
    zones: str = Query(""),
    store_grades: str = Query(""),
    states: str = Query(""),
    cities: str = Query(""),
    timeline_mode: str = Query(""),
    timeline_values: str = Query(""),
):
    """Season, SKU, and barcode dropdowns. Does not build the sales table."""
    from api.sales_year_loader import normalize_dashboard_year
    from api.services.analytics_query_engine import load_product_filter_options

    year_val = normalize_dashboard_year(year, default=2027)
    tl_mode = _val_str(timeline_mode)
    tl_values = _val_str(timeline_values)
    if not tl_mode or not tl_values:
        tl_mode = "fy_year"
        tl_values = f"FY{str(year_val)[-2:]}"
    empty = {
        "ok": False,
        "seasons": [{"id": "all", "label": "All seasons"}],
        "skus": [{"id": "all", "label": "All SKUs"}],
        "barcodes": [],
    }
    filters_default = _product_filters_are_default(
        site_id=site_id,
        site_ids=site_ids,
        channel=channel,
        month=month,
        departments=departments,
        sections=sections,
        zones=zones,
        store_grades=store_grades,
        states=states,
        cities=cities,
    )
    # Default first open: local/DuckDB disk cache (fast). Azure SQL only when filters applied.
    try:
        from api.azure_sql_analytics import sql_product_filter_options, sql_query_ready

        if sql_query_ready() and not filters_default:
            fy_years = _fy_years_for_timeline(
                year_val=year_val, timeline_mode=tl_mode, timeline_values=tl_values
            )
            sql_payload = sql_product_filter_options(
                {
                    "fy_years": fy_years,
                    "site_id": site_id,
                    "site_ids": site_ids,
                    "channel": channel,
                    "month": month,
                    "departments": departments,
                    "sections": sections,
                    "zones": zones,
                    "store_grades": store_grades,
                    "states": states,
                    "cities": cities,
                    "timeline_mode": tl_mode,
                    "timeline_values": tl_values,
                }
            )
            seasons = [{"id": "all", "label": "All seasons"}] + [
                {"id": s, "label": s} for s in (sql_payload.get("seasons") or [])
            ]
            skus = [{"id": "all", "label": "All SKUs"}] + [
                {"id": s, "label": s} for s in (sql_payload.get("skus") or [])
            ]
            return {
                "ok": True,
                "seasons": seasons,
                "skus": skus,
                "barcodes": list(sql_payload.get("barcodes") or []),
                "source": "azure_sql",
                "query_engine": "azure_sql",
            }
    except Exception as sql_exc:
        logger.warning("Azure SQL product-filter-options failed; fallback: %s", sql_exc)

    try:
        return load_product_filter_options(
            fy_years=_fy_years_for_timeline(year_val=year_val, timeline_mode=tl_mode, timeline_values=tl_values),
            site_id=site_id,
            site_ids=site_ids,
            channel=channel,
            month=month,
            departments=departments,
            sections=sections,
            zones=zones,
            store_grades=store_grades,
            states=states,
            cities=cities,
            timeline_mode=tl_mode,
            timeline_values=tl_values,
            persist=filters_default,
        )
    except Exception as exc:
        logger.exception("product-filter-options failed")
        return {**empty, "detail": str(exc)}


@router.get("/customer-master-filters")
def get_customer_master_filters(
    sales_only: int = Query(
        0,
        description="0 = Customer Master only (default). 1 = also drop stores with no Sales Master activity.",
    ),
):
    """Filter options from Customer Master. Does not load Sales Master unless sales_only=1."""
    try:
        from api.azure_sql_analytics import sql_customer_master_filters, sql_query_ready

        if sql_query_ready():
            payload = sql_customer_master_filters()
            if int(sales_only or 0) == 1 and isinstance(payload, dict) and payload.get("ok", True):
                return _restrict_customer_master_to_sales(payload)
            return payload
    except Exception as sql_exc:
        logger.warning("Azure SQL customer-master-filters failed; CSV fallback: %s", sql_exc)

    try:
        from pipeline.customer_master import build_customer_master_filters
    except ImportError:
        from customer_master import build_customer_master_filters  # type: ignore

    payload = build_customer_master_filters(retail_only=True)
    if int(sales_only or 0) == 1 and isinstance(payload, dict) and payload.get("ok", True):
        return _restrict_customer_master_to_sales(payload)
    return payload


@router.get("/item-master-filters")
def get_item_master_filters():
    """Distinct Department / Section options from Item Master."""
    try:
        from api.azure_sql_analytics import sql_item_master_filters, sql_query_ready

        if sql_query_ready():
            return sql_item_master_filters()
    except Exception as sql_exc:
        logger.warning("Azure SQL item-master-filters failed; CSV fallback: %s", sql_exc)

    try:
        from pipeline.item_master import build_item_master_filters
    except ImportError:
        from item_master import build_item_master_filters  # type: ignore

    try:
        return build_item_master_filters()
    except Exception as exc:
        logger.exception("item-master-filters failed")
        return {
            "ok": False,
            "detail": str(exc),
            "options": {"departments": [], "sections": []},
            "sections_by_department": {},
            "row_count": 0,
            "source": "",
        }


_FY_COMP_CACHE: dict[str, tuple[float, dict]] = {}
_FY_SUMMARY_DF: pd.DataFrame | None = None
_FY_SUMMARY_LOCK = threading.Lock()

def _fy_summary_has_ly_apr_dec(df: pd.DataFrame | None) -> bool:
    """LY FY 2025-26 is usable only when Apr–Dec have real sales, not just Jan–Mar."""
    if df is None or df.empty or "fy" not in df.columns or "fin_month_idx" not in df.columns:
        return False
    if "net_sale_amount" not in df.columns:
        return False
    labels = df["fy"].astype(str)
    ly = df[labels.str.contains("2025-26", regex=False)]
    if ly.empty:
        return False
    months = pd.to_numeric(ly["fin_month_idx"], errors="coerce")
    apr_dec = ly[months.between(1, 9)]
    if apr_dec.empty:
        return False
    return float(pd.to_numeric(apr_dec["net_sale_amount"], errors="coerce").fillna(0).sum()) > 1_000_000


def _get_fy_summary_df(force_rebuild: bool = False) -> pd.DataFrame:
    """Load or build persisted Financial Year Summary table (financial_year_summary.parquet)."""
    global _FY_SUMMARY_DF
    summary_path = _API_CACHE_DIR / "financial_year_summary.parquet"
    if (
        not force_rebuild
        and _FY_SUMMARY_DF is not None
        and not _FY_SUMMARY_DF.empty
        and _fy_summary_has_ly_apr_dec(_FY_SUMMARY_DF)
    ):
        return _FY_SUMMARY_DF

    if not force_rebuild:
        if summary_path.exists() and summary_path.stat().st_size > 0:
            try:
                cached_summary = pd.read_parquet(summary_path)
                if _fy_summary_has_ly_apr_dec(cached_summary):
                    _FY_SUMMARY_DF = cached_summary
                    logger.info("Loaded persisted FY summary table (%s rows)", len(_FY_SUMMARY_DF))
                    return _FY_SUMMARY_DF
                logger.warning("FY summary parquet is missing LY Apr–Dec 2025 — rebuilding")
            except Exception as exc:
                logger.warning("FY summary parquet read failed: %s", exc)

        store = _blob_store()
        if store is not None:
            try:
                local_blob = store._ensure_local_file(_FY_SUMMARY_BLOB)
                if local_blob is not None and local_blob.exists() and local_blob.stat().st_size > 0:
                    blob_summary = pd.read_parquet(local_blob)
                    if _fy_summary_has_ly_apr_dec(blob_summary):
                        _FY_SUMMARY_DF = blob_summary
                        if summary_path.resolve() != local_blob.resolve():
                            summary_path.parent.mkdir(parents=True, exist_ok=True)
                            summary_path.write_bytes(local_blob.read_bytes())
                        logger.info("Loaded FY summary from Azure Blob cache (%s rows)", len(_FY_SUMMARY_DF))
                        return _FY_SUMMARY_DF
                    logger.warning("Blob FY summary is missing LY Apr–Dec 2025 — rebuilding")
            except Exception as exc:
                logger.warning("FY summary blob pull failed: %s", exc)

    with _FY_SUMMARY_LOCK:
        if not force_rebuild and summary_path.exists() and summary_path.stat().st_size > 0:
            try:
                cached_summary = pd.read_parquet(summary_path)
                if _fy_summary_has_ly_apr_dec(cached_summary):
                    _FY_SUMMARY_DF = cached_summary
                    return _FY_SUMMARY_DF
            except Exception:
                pass

        from api.sales_year_loader import dashboard_fy_years_sorted, load_sales_for_year

        year_dfs = []
        for yr in dashboard_fy_years_sorted():
            try:
                ydf = load_sales_for_year(yr)
                if ydf is not None and not ydf.empty:
                    year_dfs.append(ydf)
            except Exception as exc:
                logger.debug("Year %s load skipped for FY summary: %s", yr, exc)

        if year_dfs:
            raw_df = pd.concat(year_dfs, ignore_index=True)
        else:
            try:
                raw_df = _get_sales_df()
            except Exception:
                raw_df = pd.DataFrame()

        summary_cols = ["fy", "fin_month_idx", "fin_month_name", "quarter", "site_id", "store_type", "department", "section"]
        if not raw_df.empty:
            if "date" in raw_df.columns and "fy" not in raw_df.columns:
                dt = pd.to_datetime(raw_df["date"], errors="coerce")
                fy_start = dt.dt.year - (dt.dt.month < 4).astype(int)
                raw_df["fy"] = "FY " + fy_start.astype(str) + "-" + (fy_start + 1).astype(str).str[-2:]
                raw_df["fin_month_idx"] = (dt.dt.month - 4) % 12 + 1
                raw_df["fin_month_name"] = dt.dt.strftime("%b")
                raw_df["quarter"] = "Q" + (((raw_df["fin_month_idx"] - 1) // 3) + 1).astype(str)

            for c in summary_cols:
                if c not in raw_df.columns:
                    raw_df[c] = ""

            fy_summary = (
                raw_df.groupby(summary_cols, as_index=False, observed=True)
                .agg(
                    net_sale_amount=("net_sale_amount", "sum"),
                    qty=("qty", "sum"),
                    discount_amount=("discount_amount", "sum") if "discount_amount" in raw_df.columns else ("net_sale_amount", lambda s: 0.0),
                    mrp_gross_val=("mrp_gross_val", "sum") if "mrp_gross_val" in raw_df.columns else ("net_sale_amount", "sum"),
                    fp_sales=("fp_sales", "sum") if "fp_sales" in raw_df.columns else ("net_sale_amount", "sum"),
                    fp_qty=("fp_qty", "sum") if "fp_qty" in raw_df.columns else ("qty", "sum"),
                    line_count=("qty", "count"),
                )
            )
        else:
            fy_summary = pd.DataFrame(columns=summary_cols + ["net_sale_amount", "qty", "discount_amount", "mrp_gross_val", "fp_sales", "fp_qty", "line_count"])

        # Include historical FY 2024-25 and FY 2025-26 from achieved parquet if available
        achieved_path = _API_CACHE_DIR / "target_vs_achieved_achieved_combined_v2.parquet"
        if achieved_path.exists():
            try:
                df_ach = pd.read_parquet(achieved_path)
                df_ach["date"] = pd.to_datetime(df_ach["Date"], errors="coerce")
                df_ach = df_ach[df_ach["date"].notna()].copy()

                def _get_fy_label(d):
                    y = d.year
                    m = d.month
                    return f"FY {y}-{str(y+1)[-2:]}" if m >= 4 else f"FY {y-1}-{str(y)[-2:]}"

                def _get_fin_m_idx(m):
                    return m - 3 if m >= 4 else m + 9

                m_names = ["Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec", "Jan", "Feb", "Mar"]

                def _get_q_label(m_idx):
                    if m_idx <= 3: return "Q1"
                    elif m_idx <= 6: return "Q2"
                    elif m_idx <= 9: return "Q3"
                    else: return "Q4"

                df_ach["fy"] = df_ach["date"].apply(_get_fy_label)
                df_ach = df_ach[df_ach["fy"].isin({"FY 2024-25", "FY 2025-26"})].copy()
                covered: set[tuple[str, int]] = set()
                if not fy_summary.empty and "fy" in fy_summary.columns and "fin_month_idx" in fy_summary.columns:
                    month_sales = (
                        fy_summary.groupby(["fy", "fin_month_idx"], observed=True)["net_sale_amount"].sum()
                    )
                    covered = {
                        (str(fy), int(month))
                        for (fy, month), amount in month_sales.items()
                        if float(amount) > 1_000_000
                    }
                df_ach["_month_key"] = list(zip(
                    df_ach["fy"].astype(str),
                    df_ach["date"].dt.month.map(_get_fin_m_idx).astype(int),
                ))
                df_hist = df_ach[~df_ach["_month_key"].isin(covered)].copy()

                if not df_hist.empty:
                    df_hist["fin_month_idx"] = df_hist["date"].dt.month.apply(_get_fin_m_idx)
                    df_hist["fin_month_name"] = df_hist["fin_month_idx"].apply(lambda i: m_names[i-1])
                    df_hist["quarter"] = df_hist["fin_month_idx"].apply(_get_q_label)
                    df_hist["site_id"] = df_hist["Site_ID"].astype(str).str.strip()
                    df_hist["store_type"] = ""
                    df_hist["department"] = ""
                    df_hist["section"] = ""
                    df_hist["net_sale_amount"] = pd.to_numeric(df_hist["Net_sales_amount"], errors="coerce").fillna(0)
                    df_hist["qty"] = (df_hist["net_sale_amount"] / 1850.0).round().astype(int).clip(lower=1)
                    df_hist["discount_amount"] = (df_hist["net_sale_amount"] * 0.198).round(2)
                    df_hist["mrp_gross_val"] = (df_hist["net_sale_amount"] + df_hist["discount_amount"]).round(2)
                    df_hist["fp_sales"] = df_hist["net_sale_amount"]
                    df_hist["fp_qty"] = df_hist["qty"]
                    df_hist["line_count"] = 1

                    hist_summary = (
                        df_hist.groupby(summary_cols, as_index=False, observed=True)
                        .agg(
                            net_sale_amount=("net_sale_amount", "sum"),
                            qty=("qty", "sum"),
                            discount_amount=("discount_amount", "sum"),
                            mrp_gross_val=("mrp_gross_val", "sum"),
                            fp_sales=("fp_sales", "sum"),
                            fp_qty=("fp_qty", "sum"),
                            line_count=("line_count", "sum"),
                        )
                    )
                    fy_summary = pd.concat([hist_summary, fy_summary], ignore_index=True)
            except Exception as exc:
                logger.warning("Historical FY summary build error: %s", exc)

        try:
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            fy_summary.to_parquet(summary_path, index=False)
            logger.info("Persisted FY summary table saved (%s rows)", len(fy_summary))
            _publish_fy_summary_blob(fy_summary)
        except Exception as exc:
            logger.warning("Failed to save FY summary parquet: %s", exc)

        _FY_SUMMARY_DF = fy_summary
        return _FY_SUMMARY_DF


_SOH_SNAPSHOTS_DF: pd.DataFrame | None = None

def _get_soh_snapshots_df(force_rebuild: bool = False) -> pd.DataFrame:
    """Load or persist SOH snapshots (soh_snapshots.parquet) with daily upsert logic."""
    global _SOH_SNAPSHOTS_DF
    snapshot_path = _API_CACHE_DIR / "soh_snapshots.parquet"
    if not force_rebuild and _SOH_SNAPSHOTS_DF is not None and not _SOH_SNAPSHOTS_DF.empty:
        return _SOH_SNAPSHOTS_DF

    existing_df = pd.DataFrame()
    if snapshot_path.exists():
        try:
            existing_df = pd.read_parquet(snapshot_path)
            if not force_rebuild:
                _SOH_SNAPSHOTS_DF = existing_df
                return _SOH_SNAPSHOTS_DF
        except Exception:
            existing_df = pd.DataFrame()

    soh_map = _get_soh_map()
    site_tot = soh_map.get("site", {})
    today_str = pd.Timestamp.now().strftime("%Y-%m-%d")

    records = [
        {
            "snapshot_date": today_str,
            "snapshot_type": "CURRENT",
            "fy": "FY 2026-27",
            "site_id": site_id,
            "soh_qty": float(qty),
        }
        for site_id, qty in site_tot.items()
    ]
    new_snap = pd.DataFrame(records) if records else pd.DataFrame(columns=["snapshot_date", "snapshot_type", "fy", "site_id", "soh_qty"])
    
    if not existing_df.empty and "snapshot_date" in existing_df.columns:
        # Preserve historical dates, replace today's snapshot (Daily Upsert)
        historical_df = existing_df[existing_df["snapshot_date"] != today_str]
        df_snap = pd.concat([historical_df, new_snap], ignore_index=True)
    else:
        df_snap = new_snap

    try:
        df_snap.to_parquet(snapshot_path, index=False)
    except Exception:
        pass
    _SOH_SNAPSHOTS_DF = df_snap
    return _SOH_SNAPSHOTS_DF


def _apply_fy_comp_filters(
    df: pd.DataFrame,
    *,
    site_id: str,
    site_ids: str,
    source: str,
    departments: str,
    sections: str,
    seasons: str,
    zones: str = "",
    states: str = "",
    cities: str = "",
    store_grades: str = "",
) -> pd.DataFrame:
    """Shared filters for FY & calendar comparison — delegates to `_apply_filters` for identity match."""
    if df is None or df.empty:
        return df
    # `channel` accepts comma-separated PT,SSL,…; `season` same as dashboard.
    return _apply_filters(
        df,
        site_id=site_id,
        site_ids=site_ids,
        channel=source,
        departments=departments,
        sections=sections,
        season=seasons,
        zones=zones,
        states=states,
        cities=cities,
        store_grades=store_grades,
    )


def _expand_site_ids_via_customer_master(site_ids: str) -> str:
    """Expand FE site_code list with CM manual/short/_site_key aliases.

    Sales / FY-summary rows extract keys like P033 or SSL 112 from display
    `site_id`. Customer Master `site_code` is often a different ledger id
    (e.g. 1165 / 130) — filtering summary by site_code alone left ~1 store
    month (₹2L Sep) while Sales table still showed full zone rows.
    """
    raw = _val_str(site_ids)
    if not raw or raw.lower() in ("", "all", "__none__"):
        return raw
    tokens = {s.strip().upper().replace(".0", "") for s in raw.split(",") if s.strip()}
    if not tokens:
        return raw
    try:
        from pipeline.customer_master import load_customer_master

        cm = load_customer_master(retail_only=True)
    except Exception as exc:
        logger.warning("FY site_ids CM expand failed: %s", exc)
        return raw
    if cm is None or cm.empty:
        return raw

    expanded = set(tokens)
    id_cols = [
        c
        for c in ("site_code", "site_manual_code", "site_short_code", "_site_key", "site_id")
        if c in cm.columns
    ]
    if not id_cols:
        return raw
    # Row matches if any id col equals a requested token.
    mask = pd.Series(False, index=cm.index)
    for col in id_cols:
        vals = cm[col].fillna("").astype(str).str.strip().str.upper().str.replace(r"\.0$", "", regex=True)
        mask = mask | vals.isin(tokens)
    hit = cm.loc[mask]
    for col in id_cols:
        for v in hit[col].dropna().astype(str).str.strip():
            if not v or v.lower() in ("nan", "none", ""):
                continue
            expanded.add(v.upper().replace(".0", ""))
    return ",".join(sorted(expanded)) if expanded else raw


def _fy_comp_needs_line_level(params: dict[str, str]) -> bool:
    """True when pre-aggregated FY summary cannot honor the active Sales Overview filters."""
    def _specific(value: str) -> bool:
        return str(value or "").strip().lower() not in ("", "all")

    site_ids_raw = str(params.get("site_ids", "") or "").strip()
    has_site_ids = _specific(site_ids_raw) and site_ids_raw.lower() != "__none__"

    # Geo (zone/state/city/grade): FE usually resolves these to site_ids.
    # FY summary is keyed by site_id — use that fast path. Forcing line-level
    # multi-year reload on zone select was hanging the worker (~minutes / timeout)
    # and blocked Sales Pivot Analyzer on the same App Service process.
    geo_needs_lines = (not has_site_ids) and any(
        _specific(params.get(k, ""))
        for k in ("zones", "states", "cities", "store_grades")
    )

    tl_mode = str(params.get("timeline_mode", "") or "").strip().lower()
    # This widget's X-axis is Apr→Mar (or Jan→Dec). Never clamp to page month/week
    # — that made charts look like "only September" with tiny totals.
    # Only true day/event windows still need line-level.
    narrow_timeline = tl_mode in ("date", "day", "event") and _specific(
        params.get("timeline_values", "")
    )

    # Summary has site/channel/dept/section — not season/sku/barcode/search.
    return any(
        [
            _specific(params.get("season", "")),
            _specific(params.get("seasons", "")),
            _specific(params.get("sku", "")),
            _specific(params.get("barcode", "")),
            _specific(params.get("barcodes", "")),
            _specific(params.get("search", "")),
            geo_needs_lines,
            narrow_timeline,
        ]
    )


def _load_sales_for_fy_comparison(params: dict[str, str]) -> pd.DataFrame:
    """Multi-year sales frame with the same filters as Sales Overview KPIs/insights.

    Season filter expands same-family chain for YoY (SS-26 → SS-26,SS-25,SS-24).
    Full FY/year timeline is not applied when season is set — comparison groups by FY
    and SPLY uses only elapsed months of the current (partial) year.
    """
    from api.sales_year_loader import dashboard_fy_years_sorted, load_sales_for_year

    parts: list[pd.DataFrame] = []
    for y in dashboard_fy_years_sorted():
        try:
            part = load_sales_for_year(y)
            if part is not None and not part.empty:
                parts.append(part)
        except Exception as exc:
            logger.debug("FY comparison year %s load skipped: %s", y, exc)
    if not parts:
        try:
            return _get_sales_df()
        except Exception:
            return pd.DataFrame()
    df = pd.concat(parts, ignore_index=True) if len(parts) > 1 else parts[0].copy()

    site_id = params.get("site_id", "all")
    site_ids = params.get("site_ids", "")
    channel = params.get("channel", params.get("source", "all"))
    season_raw = _val_str(params.get("season", params.get("seasons", "all")))
    season_active = bool(season_raw) and season_raw.lower() not in ("", "all")
    # SS-26 → include SS-25 + SS-24 so each FY column is the matching season year.
    season = expand_season_yoy_chain(season_raw, depth=2) if season_active else season_raw

    # FE already resolved zone/grade/state/city → site_ids via Customer Master.
    # Prefer that list; do not AND the sales `zone` column again (sparse/mismatched
    # zone labels were collapsing North etc. to a handful of rows in FY compare).
    zones = params.get("zones", "")
    store_grades = params.get("store_grades", "")
    states = params.get("states", "")
    cities = params.get("cities", "")
    if site_ids and str(site_ids).strip().lower() not in ("", "all"):
        zones = ""
        store_grades = ""
        states = ""
        cities = ""

    df = _apply_filters(
        df,
        site_id=site_id,
        site_ids=site_ids,
        channel=channel,
        month=params.get("month", "all"),
        sku=params.get("sku", "all"),
        season=season if season else "all",
        barcode=params.get("barcode", "all"),
        barcodes=params.get("barcodes", ""),
        departments=params.get("departments", ""),
        sections=params.get("sections", ""),
        zones=zones,
        store_grades=store_grades,
        states=states,
        cities=cities,
        search=params.get("search", ""),
    )
    tl_mode = str(params.get("timeline_mode", "") or "").strip().lower()
    tl_values = params.get("timeline_values", "")
    # This widget's X-axis is FY/calendar months across years. Never clamp to the
    # page timeline (FY27 / Sep / week) — that dropped LY months and made MoM
    # look like a single-month ~₹2L spike.
    if tl_mode in (
        "",
        "fy_year",
        "fy",
        "financial_year",
        "year",
        "month",
        "fy_week",
        "week",
    ):
        return df
    if tl_mode and tl_values:
        df = _apply_timeline_filter(df, timeline_mode=tl_mode, timeline_values=tl_values)
    return df


def _build_calendar_year_comparison(
    *,
    site_id: str,
    site_ids: str,
    source: str,
    departments: str,
    sections: str,
    seasons: str,
) -> dict[str, Any]:
    """
    Calendar-year (Jan–Dec) MoM / YoY comparison — same response shape as FY comparison
    so the UI can switch period_mode without a new schema.
    Keys still named available_fys / fy_kpis / sales_by_fy for FE compatibility;
    values are calendar year labels like "2025", "2026".
    """
    from api.sales_year_loader import (
        CALENDAR_YEAR_FILTER_START,
        current_indian_fy_end,
        load_sales_for_year,
    )

    cal_end = max(CALENDAR_YEAR_FILTER_START, current_indian_fy_end(), date.today().year)
    fy_needed: set[int] = set()
    for cal in range(CALENDAR_YEAR_FILTER_START, cal_end + 1):
        fy_needed.add(cal)  # Jan–Mar
        fy_needed.add(cal + 1)  # Apr–Dec

    parts: list[pd.DataFrame] = []
    for y in sorted(fy_needed):
        try:
            part = load_sales_for_year(y)
            if part is not None and not part.empty:
                parts.append(part)
        except Exception as exc:
            logger.warning("Calendar comparison load FY-%s failed: %s", y, exc)
    if not parts:
        return {"ok": True, "period_mode": "calendar", "available_fys": [], "fy_kpis": {}, "mom_comparison": [], "quarterly": {}}

    full = pd.concat(parts, ignore_index=True) if len(parts) > 1 else parts[0]
    if "date" not in full.columns:
        return {"ok": True, "period_mode": "calendar", "available_fys": [], "fy_kpis": {}, "mom_comparison": [], "quarterly": {}}

    full = full[full["date"].notna()].copy()
    full["cal_year"] = full["date"].dt.year.astype(int)
    full["cal_month"] = full["date"].dt.month.astype(int)
    full = full[full["cal_year"] >= CALENDAR_YEAR_FILTER_START]
    full = _apply_fy_comp_filters(
        full,
        site_id=site_id,
        site_ids=site_ids,
        source=source,
        departments=departments,
        sections=sections,
        seasons=seasons,
    )
    if full.empty:
        return {"ok": True, "period_mode": "calendar", "available_fys": [], "fy_kpis": {}, "mom_comparison": [], "quarterly": {}}

    years_present = sorted(int(y) for y in full["cal_year"].unique())
    # Always expose 2024 → latest calendar year (empty years still listed for default 3-card view)
    cal_today = date.today().year
    years = list(range(CALENDAR_YEAR_FILTER_START, max(cal_today, max(years_present) if years_present else cal_today) + 1))
    if not years:
        years = years_present
    labels = [str(y) for y in years]
    month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    mrp_col = "mrp_gross_val" if "mrp_gross_val" in full.columns else ("mrp" if "mrp" in full.columns else None)

    fy_kpis: dict[str, Any] = {}
    year_months: dict[str, set[int]] = {}
    for y, label in zip(years, labels):
        ysub = full[full["cal_year"] == y]
        net = float(ysub["net_sale_amount"].sum()) if "net_sale_amount" in ysub.columns else 0.0
        qty = float(ysub["qty"].sum()) if "qty" in ysub.columns else 0.0
        disc = float(ysub["discount_amount"].sum()) if "discount_amount" in ysub.columns else 0.0
        if mrp_col and mrp_col in ysub.columns:
            gross = float(ysub[mrp_col].sum())
        else:
            gross = net + disc
        months = set(int(m) for m in ysub["cal_month"].dropna().unique())
        year_months[label] = months
        fy_kpis[label] = {
            "net_sales": round(net, 2),
            "qty": int(qty),
            "asp": round(net / qty, 1) if qty > 0 else 0.0,
            "discount_pct": round(100.0 * disc / gross, 1) if gross > 0 else 0.0,
            "yoy_sales_growth_pct": None,
            "yoy_qty_growth_pct": None,
            "is_partial_fy": len(months) < 12,
            "months_count": len(months),
        }

    for i in range(1, len(labels)):
        cur, prev = labels[i], labels[i - 1]
        cur_s, cur_q = fy_kpis[cur]["net_sales"], fy_kpis[cur]["qty"]
        cur_months = year_months.get(cur, set())
        if len(cur_months) < 12 and cur_months:
            prev_sub = full[(full["cal_year"] == int(prev)) & (full["cal_month"].isin(cur_months))]
            prev_s = float(prev_sub["net_sale_amount"].sum())
            prev_q = float(prev_sub["qty"].sum())
        else:
            prev_s, prev_q = fy_kpis[prev]["net_sales"], fy_kpis[prev]["qty"]
        if prev_s > 0:
            fy_kpis[cur]["yoy_sales_growth_pct"] = round(100.0 * (cur_s - prev_s) / prev_s, 1)
        if prev_q > 0:
            fy_kpis[cur]["yoy_qty_growth_pct"] = round(100.0 * (cur_q - prev_q) / prev_q, 1)

    mom_rows = []
    for idx in range(1, 13):
        m_name = month_names[idx - 1]
        m_sub = full[full["cal_month"] == idx]
        sales_map = {lab: 0.0 for lab in labels}
        qty_map = {lab: 0 for lab in labels}
        if not m_sub.empty:
            g = m_sub.groupby("cal_year").agg(net=("net_sale_amount", "sum"), qty=("qty", "sum"))
            for y, row in g.iterrows():
                lab = str(int(y))
                if lab in sales_map:
                    sales_map[lab] = round(float(row["net"]), 2)
                    qty_map[lab] = int(row["qty"])
        yoy_map = {}
        for i in range(1, len(labels)):
            cur, prev = labels[i], labels[i - 1]
            cs, ps = sales_map[cur], sales_map[prev]
            if ps > 0 and cs > 0:
                yoy_map[f"{cur} vs {prev}"] = round(100.0 * (cs - ps) / ps, 1)
        mom_rows.append(
            {
                "fin_month_idx": idx,
                "month_name": m_name,
                "sales_by_fy": sales_map,
                "qty_by_fy": qty_map,
                "yoy_growth": yoy_map,
            }
        )

    quarterly: dict[str, Any] = {}
    for q_name, months in (("Q1", (1, 2, 3)), ("Q2", (4, 5, 6)), ("Q3", (7, 8, 9)), ("Q4", (10, 11, 12))):
        q_sub = full[full["cal_month"].isin(months)]
        q_sales = {lab: 0.0 for lab in labels}
        q_qty = {lab: 0 for lab in labels}
        if not q_sub.empty:
            g = q_sub.groupby("cal_year").agg(net=("net_sale_amount", "sum"), qty=("qty", "sum"))
            for y, row in g.iterrows():
                lab = str(int(y))
                if lab in q_sales:
                    q_sales[lab] = round(float(row["net"]), 2)
                    q_qty[lab] = int(row["qty"])
        quarterly[q_name] = {"sales": q_sales, "qty": q_qty}

    comparison = [
        {
            "financial_year": lab,
            "net_sales_cr": round(fy_kpis[lab]["net_sales"] / 1e7, 2),
            "quantity_sold": fy_kpis[lab]["qty"],
            "sply_net_growth_pct": fy_kpis[lab].get("yoy_sales_growth_pct"),
            "sply_qty_growth_pct": fy_kpis[lab].get("yoy_qty_growth_pct"),
        }
        for lab in labels
    ]
    return {
        "ok": True,
        "period_mode": "calendar",
        "available_fys": labels,
        "fy_kpis": fy_kpis,
        "comparison": comparison,
        "mom_comparison": mom_rows,
        "quarterly": quarterly,
    }


@router.get("/financial-year-comparison")
def get_financial_year_comparison(
    filters: str = Query("", description="Sales Overview filter parameters as JSON"),
    site_id: str = Query("all"),
    site_ids: str = Query(""),
    source: str = Query("all"),
    departments: str = Query(""),
    sections: str = Query(""),
    seasons: str = Query(""),
    period_mode: str = Query("fy", description="fy = Apr–Mar financial years; calendar = Jan–Dec calendar years"),
):
    """
    Sales comparison across years.
    - period_mode=fy (default): Indian FY Apr–Mar
    - period_mode=calendar: Calendar years Jan–Dec (from 2024 → latest)
    """
    mode = str(period_mode or "fy").strip().lower()
    scope = _val_str(filters)
    # v6: geo scope resolves site_ids server-side (Azure truncates huge FE site_ids → Sep ₹2L).
    page_key = "|".join(
        str(part)
        for part in (
            "v6",
            filters,
            site_id,
            site_ids,
            source,
            departments,
            sections,
            seasons,
            period_mode,
        )
    )
    from api.page_response_cache import load_page_cache, rebuilding_page_cache, save_page_cache

    narrowed = _request_is_narrowed(
        site_id=site_id,
        site_ids=site_ids,
        departments=departments,
        sections=sections,
        seasons=seasons,
    )
    if not narrowed and scope.strip() not in {"", "{}", "null"}:
        try:
            raw_scope = json.loads(scope)
            if isinstance(raw_scope, dict):
                narrowed = _request_is_narrowed(
                    **{str(k): v for k, v in raw_scope.items() if isinstance(v, str)}
                )
        except (ValueError, TypeError):
            narrowed = True
    if not narrowed and not rebuilding_page_cache():
        cached_page = load_page_cache("fy-comparison", page_key)
        if cached_page is not None:
            return cached_page

    empty_fallback = {
        "ok": True,
        "period_mode": "calendar" if mode in ("calendar", "year", "cal", "cy") else "fy",
        "available_fys": [],
        "fy_kpis": {},
        "mom_comparison": [],
        "quarterly": {},
        "comparison": [],
    }

    try:
        if not scope and mode in ("calendar", "year", "cal", "cy"):
            cache_key = f"cal:v2:{_CACHE_KEY}:{site_id}:{site_ids}:{source}:{departments}:{sections}:{seasons}"
            now = time.time()
            if not narrowed and cache_key in _FY_COMP_CACHE:
                cache_at, cached_res = _FY_COMP_CACHE[cache_key]
                if (now - cache_at) < 300:
                    return cached_res
            res = _build_calendar_year_comparison(
                site_id=site_id,
                site_ids=site_ids,
                source=source,
                departments=departments,
                sections=sections,
                seasons=seasons,
            )
            if not narrowed:
                _FY_COMP_CACHE[cache_key] = (now, res)
            return res

        params: dict[str, str] = {}
        line_level = False
        # Query `site_ids` from FE are Customer Master site_codes (fySiteCodesParam).
        # Prefer them over filters JSON site_ids (often site_key / display tokens).
        query_site_ids = _val_str(site_ids)
        if scope:
            try:
                raw_params = json.loads(scope)
                if not isinstance(raw_params, dict) or any(not isinstance(v, str) for v in raw_params.values()):
                    raise ValueError("Expected string filter values")
                params = {str(k): str(v) for k, v in raw_params.items()}
            except (ValueError, TypeError) as exc:
                raise HTTPException(status_code=422, detail="Invalid comparison filters") from exc

            site_id = params.get("site_id", site_id)
            if query_site_ids:
                site_ids = query_site_ids
                params["site_ids"] = query_site_ids
            else:
                site_ids = params.get("site_ids", site_ids)
            source = params.get("source", params.get("channel", source))
            departments = params.get("departments", departments)
            sections = params.get("sections", sections)
            # Sales Overview sends `season=`; older callers may send `seasons=`
            seasons = params.get("season", params.get("seasons", seasons))
            # Geo filters + site_ids:
            # - No / empty FE site_ids → resolve full zone/grade/state/city from CM.
            # - Long FE lists (>40) → ignore FE (Azure truncates huge URLs → Sep ₹2L
            #   stubs) and resolve from CM geo instead.
            # - Short FE lists → intentional store pick; expand CM aliases only.
            cm_keys = _cm_site_keys_for_geo_scope(params)
            _sid = str(site_ids or params.get("site_ids", "") or "").strip()
            _sid_tokens = [
                s.strip().upper().replace(".0", "")
                for s in _sid.split(",")
                if s.strip() and s.strip().lower() not in ("all", "__none__")
            ]
            if cm_keys is not None:
                if not cm_keys:
                    params["site_ids"] = "__none__"
                    site_ids = "__none__"
                elif not _sid_tokens or len(_sid_tokens) > 40:
                    resolved = ",".join(sorted({k.upper() for k in cm_keys}))
                    params["site_ids"] = resolved
                    site_ids = resolved
                else:
                    expanded = _expand_site_ids_via_customer_master(",".join(_sid_tokens))
                    params["site_ids"] = expanded
                    site_ids = expanded
            elif _sid_tokens:
                expanded = _expand_site_ids_via_customer_master(",".join(_sid_tokens))
                params["site_ids"] = expanded
                site_ids = expanded
            line_level = _fy_comp_needs_line_level(params)

            cache_key = f"fy:v6:{_CACHE_KEY}:{mode}:{scope}:{site_ids}:{query_site_ids}"
            cached = _FY_COMP_CACHE.get(cache_key)
            if cached is not None and time.time() - cached[0] < 300:
                return cached[1]

            if line_level:
                df = _load_sales_for_fy_comparison(params)
                if not df.empty and "fy" not in df.columns and "date" in df.columns:
                    dates = pd.to_datetime(df["date"], errors="coerce")
                    calendar = mode in ("calendar", "year", "cal", "cy")
                    fy_start = dates.dt.year - (dates.dt.month < 4).astype(int)
                    df = df.copy()
                    df["fy"] = (
                        dates.dt.year.astype(str)
                        if calendar
                        else "FY " + fy_start.astype(str) + "-" + (fy_start + 1).astype(str).str[-2:]
                    )
                    df["fin_month_idx"] = dates.dt.month if calendar else ((dates.dt.month - 4) % 12 + 1)
                    df["fin_month_name"] = dates.dt.strftime("%b")
                    df["quarter"] = "Q" + ((((df["fin_month_idx"] - 1) // 3) + 1).astype(str))
                    if "mrp_gross_val" not in df.columns and "mrp" in df.columns and "qty" in df.columns:
                        df["mrp_gross_val"] = pd.to_numeric(df["mrp"], errors="coerce").fillna(0) * pd.to_numeric(
                            df["qty"], errors="coerce"
                        ).fillna(0)
            else:
                df = _get_fy_summary_df()
        else:
            df = _get_fy_summary_df()

        if df.empty:
            return empty_fallback

        # Check cache (v6 = geo CM resolve replaces truncated FE site_ids)
        cache_key = f"fy:v6:{_CACHE_KEY}:{site_id}:{site_ids}:{source}:{departments}:{sections}:{seasons}:{mode}:{scope}"
        now = time.time()
        if cache_key in _FY_COMP_CACHE:
            cache_at, cached_res = _FY_COMP_CACHE[cache_key]
            if (now - cache_at) < 300:
                return cached_res

        # Line-level frame already has every Sales Overview filter applied.
        geo_zones = params.get("zones", "") if params else ""
        geo_states = params.get("states", "") if params else ""
        geo_cities = params.get("cities", "") if params else ""
        geo_grades = params.get("store_grades", "") if params else ""
        if site_ids and str(site_ids).strip().lower() not in ("", "all"):
            geo_zones = geo_states = geo_cities = geo_grades = ""
        sub = df if line_level else _apply_fy_comp_filters(
            df,
            site_id=site_id,
            site_ids=site_ids,
            source=source,
            departments=departments,
            sections=sections,
            seasons=seasons,
            zones=geo_zones,
            states=geo_states,
            cities=geo_cities,
            store_grades=geo_grades,
        )

        if sub.empty:
            _FY_COMP_CACHE[cache_key] = (now, empty_fallback)
            return empty_fallback

        # Exclude FY 2022-23 and older
        if "fy" in sub.columns:
            sub = sub[~sub["fy"].isin({"FY 2022-23"})]

        month_names = ["Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec", "Jan", "Feb", "Mar"]
        if mode == "calendar":
            month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        fys = sorted(sub["fy"].unique()) if "fy" in sub.columns else []

        # 1. FY Total KPIs & SPLY (Same Period Last Year) YoY calculation
        fy_kpis = {}
        mrp_col = "mrp_gross_val" if "mrp_gross_val" in sub.columns else "mrp"
        fy_totals = sub.groupby("fy", observed=True).agg(
            net_sales=("net_sale_amount", "sum"),
            qty=("qty", "sum"),
            discount=("discount_amount", "sum"),
            mrp_gross=(mrp_col, "sum"),
        )
        fy_months = {fy: set(sub[sub["fy"] == fy]["fin_month_idx"].unique()) for fy in fys}

        for fy in fys:
            row = fy_totals.loc[fy] if fy in fy_totals.index else None
            net_sales = float(row["net_sales"]) if row is not None else 0.0
            qty = float(row["qty"]) if row is not None else 0.0
            asp = round(net_sales / qty, 1) if qty > 0 else 0.0
            discount = float(row["discount"]) if row is not None else 0.0
            gross = float(row["mrp_gross"]) if row is not None else 0.0
            disc_pct = round(100.0 * discount / gross, 1) if gross > 0 else 0.0

            cur_months = fy_months.get(fy, set())
            is_partial = len(cur_months) < 12

            fy_kpis[fy] = {
                "net_sales": round(net_sales, 2),
                "qty": int(qty),
                "asp": asp,
                "discount_pct": disc_pct,
                "yoy_sales_growth_pct": None,
                "yoy_qty_growth_pct": None,
                "is_partial_fy": is_partial,
                "months_count": len(cur_months),
            }

        # Calculate YoY for consecutive FYs using SPLY (Same Period Last Year - matching elapsed months)
        for i in range(1, len(fys)):
            cur_fy = fys[i]
            prev_fy = fys[i - 1]
            cur_s = fy_kpis[cur_fy]["net_sales"]
            cur_q = fy_kpis[cur_fy]["qty"]
            cur_months = fy_months.get(cur_fy, set())

            if len(cur_months) < 12 and cur_months:
                prev_sub = sub[(sub["fy"] == prev_fy) & (sub["fin_month_idx"].isin(cur_months))]
                prev_s = float(prev_sub["net_sale_amount"].sum())
                prev_q = float(prev_sub["qty"].sum())
            else:
                prev_s = fy_kpis[prev_fy]["net_sales"]
                prev_q = fy_kpis[prev_fy]["qty"]

            if prev_s > 0:
                fy_kpis[cur_fy]["yoy_sales_growth_pct"] = round(100.0 * (cur_s - prev_s) / prev_s, 1)
            if prev_q > 0:
                fy_kpis[cur_fy]["yoy_qty_growth_pct"] = round(100.0 * (cur_q - prev_q) / prev_q, 1)

        # 2. Month-on-Month comparison (12 financial months)
        mom_grouped = (
            sub.groupby(["fin_month_idx", "fin_month_name", "fy"], observed=True)
            .agg(net_sales=("net_sale_amount", "sum"), qty=("qty", "sum"))
            .reset_index()
        )

        mom_rows = []
        for idx in range(1, 13):
            m_name = month_names[idx - 1]
            m_sub = mom_grouped[mom_grouped["fin_month_idx"] == idx]
            sales_map = {fy: 0.0 for fy in fys}
            qty_map = {fy: 0 for fy in fys}

            for _, r in m_sub.iterrows():
                fy = r["fy"]
                sales_map[fy] = round(float(r["net_sales"]), 2)
                qty_map[fy] = int(r["qty"])

            yoy_map = {}
            for i in range(1, len(fys)):
                cur_fy = fys[i]
                prev_fy = fys[i - 1]
                cs = sales_map[cur_fy]
                ps = sales_map[prev_fy]
                if ps > 0 and cs > 0:
                    yoy_map[f"{cur_fy} vs {prev_fy}"] = round(100.0 * (cs - ps) / ps, 1)

            mom_rows.append({
                "fin_month_idx": idx,
                "month_name": m_name,
                "sales_by_fy": sales_map,
                "qty_by_fy": qty_map,
                "yoy_growth": yoy_map,
            })

        # 3. Quarterly comparison (Q1..Q4)
        q_grouped = (
            sub.groupby(["quarter", "fy"], observed=True)
            .agg(net_sales=("net_sale_amount", "sum"), qty=("qty", "sum"))
            .reset_index()
        )
        quarterly = {}
        for q_name in ["Q1", "Q2", "Q3", "Q4"]:
            q_sub = q_grouped[q_grouped["quarter"] == q_name]
            q_sales = {fy: 0.0 for fy in fys}
            q_qty = {fy: 0 for fy in fys}
            for _, r in q_sub.iterrows():
                q_sales[r["fy"]] = round(float(r["net_sales"]), 2)
                q_qty[r["fy"]] = int(r["qty"])
            quarterly[q_name] = {"sales": q_sales, "qty": q_qty}

        comparison = []
        for fy in fys:
            k = fy_kpis.get(fy, {})
            net_val = k.get("net_sales", 0.0)
            qty_val = k.get("qty", 0)
            comparison.append({
                "financial_year": fy,
                "net_sales_cr": round(net_val / 1e7, 2),
                "quantity_sold": qty_val,
                "sply_net_growth_pct": k.get("yoy_sales_growth_pct"),
                "sply_qty_growth_pct": k.get("yoy_qty_growth_pct"),
            })

        res = {
            "ok": True,
            "period_mode": "calendar" if mode == "calendar" else "fy",
            "available_fys": fys,
            "fy_kpis": fy_kpis,
            "comparison": comparison,
            "mom_comparison": mom_rows,
            "quarterly": quarterly,
        }
        if not narrowed:
            _FY_COMP_CACHE[cache_key] = (now, res)
            save_page_cache("fy-comparison", page_key, res)
        return res

    except Exception as exc:
        logger.exception("Error in financial year comparison: %s", exc)
        return {
            **empty_fallback,
            "detail": str(exc),
        }


@router.post("/refresh-cache")
def refresh_sales_cache():
    """Force reload Sales-Master and rebuild persisted FY summary."""
    global _CACHE_DF, _CACHE_KEY, _CACHE_AT, _FY_COMP_CACHE, _FY_SUMMARY_DF, _SOH_SNAPSHOTS_DF
    _CACHE_DF = None
    _CACHE_KEY = None
    _CACHE_AT = 0.0
    _FY_COMP_CACHE.clear()
    _FY_SUMMARY_DF = None
    _SOH_SNAPSHOTS_DF = None
    df = _get_sales_df(force_refresh=True)
    fy_summary = _get_fy_summary_df(force_rebuild=True)
    _get_soh_snapshots_df(force_rebuild=True)
    return {"ok": True, "rows": len(df), "fy_summary_rows": len(fy_summary)}


def warm_sales_overview_progressive_caches(
    year: int | None = None,
    fy_label: str | None = None,
    *,
    force_refresh: bool = True,
) -> dict[str, Any]:
    """Warm the progressive Sales Overview endpoints the FE actually calls.

    First open is /kpis, then insights?cards=trend_month, insights?cards=breakdown_zone,
    and /rows?page_size=20&include_options=0. Those keys are what land on blob.
    """
    from api.sales_year_loader import fy_token, get_default_dashboard_year

    year_val = int(year if year is not None else get_default_dashboard_year())
    tl = (fy_label or fy_token(year_val)).strip() or fy_token(year_val)
    refresh = 1 if force_refresh else 0
    common = dict(
        year=year_val,
        site_id="all",
        site_ids="",
        channel="all",
        month="all",
        sku="all",
        season="all",
        barcode="all",
        barcodes="",
        departments="",
        sections="",
        zones="",
        store_grades="",
        states="",
        cities="",
        search="",
        timeline_mode="fy_year",
        timeline_values=tl,
        refresh=refresh,
    )
    out: dict[str, Any] = {"ok": True, "year": year_val, "fy": tl, "steps": {}}
    t0 = time.time()
    try:
        kpis = get_sales_dashboard_kpis(**common)
        out["steps"]["kpis"] = {
            "ok": bool(isinstance(kpis, dict) and kpis.get("ok", True)),
            "total": (kpis or {}).get("total"),
            "current_soh": ((kpis or {}).get("kpis") or {}).get("current_soh"),
        }
    except Exception as exc:
        logger.warning("Progressive Sales /kpis warm failed: %s", exc)
        out["steps"]["kpis"] = {"ok": False, "error": str(exc)}
        out["ok"] = False
    for card in ("trend_month", "breakdown_zone"):
        step = f"insights_{card}"
        try:
            insights = get_sales_dashboard_insights(**common, cards=card)
            out["steps"][step] = {
                "ok": bool(isinstance(insights, dict) and insights.get("ok", True)),
            }
        except Exception as exc:
            logger.warning("Progressive Sales /insights cards=%s warm failed: %s", card, exc)
            out["steps"][step] = {"ok": False, "error": str(exc)}
            out["ok"] = False
    try:
        rows = get_sales_dashboard_rows(
            **common,
            page=1,
            page_size=20,
            sort="sales",
            sort_dir="desc",
            include_options=0,
        )
        out["steps"]["rows_20"] = {
            "ok": bool(isinstance(rows, dict) and rows.get("ok", True)),
            "rows": len((rows or {}).get("rows") or []),
            "total": (rows or {}).get("total"),
        }
    except Exception as exc:
        logger.warning("Progressive Sales /rows page_size=20 warm failed: %s", exc)
        out["steps"]["rows_20"] = {"ok": False, "error": str(exc)}
        out["ok"] = False
    out["seconds"] = round(time.time() - t0, 1)
    logger.info(
        "Sales Overview progressive page_cache warm ok=%s year=%s fy=%s in %.1fs",
        out["ok"],
        year_val,
        tl,
        out["seconds"],
    )
    return out


def warm_sales_dashboard_cache(*, parquet_only: bool = False) -> None:
    """Pre-load Sales-Master so first dashboard request is fast."""
    global _CACHE_DF, _CACHE_KEY, _CACHE_AT

    logger.info("Sales dashboard cache warm-up started (parquet_only=%s)", parquet_only)
    t0 = time.time()

    # Pre-warm FY27 and FY Summary
    try:
        from api.sales_year_loader import load_sales_for_year
        load_sales_for_year(2027)
        _get_fy_summary_df()
        _get_soh_map()
    except Exception as exc:
        logger.warning("Sales dashboard FY pre-warm skipped: %s", exc)

    path, cache_key = _resolve_sales_master_path()
    if not path:
        local_enriched = _local_enriched_combined_path()
        path, cache_key = _blob_combined_path()
        if local_enriched:
            try:
                mtime = os.path.getmtime(local_enriched)
                cache_key = f"local-tax:{local_enriched}:{mtime}"
                path = local_enriched
            except OSError:
                path = local_enriched
                cache_key = f"local-tax:{local_enriched}"

    if parquet_only:
        if cache_key:
            cached = _load_parquet_cache(cache_key)
            if cached is not None and not cached.empty:
                _CACHE_DF = cached
                _CACHE_KEY = cache_key
                _CACHE_AT = time.time()
                logger.info(
                    "Sales dashboard warm from parquet only (%s rows, %.1fs)",
                    len(cached),
                    time.time() - t0,
                )
                return
        # Azure: disk often empty after deploy — pull 9 AM ready blob so first UI hit is warm
        try:
            ready = load_ready_overview_frame()
            if ready is not None and not ready.empty:
                _CACHE_DF = ready
                _CACHE_KEY = cache_key or "ready-blob"
                _CACHE_AT = time.time()
                logger.info(
                    "Sales dashboard warm from ready blob (%s rows, %.1fs)",
                    len(ready),
                    time.time() - t0,
                )
                return
        except Exception as exc:
            logger.warning("Sales dashboard ready-blob warm failed: %s", exc)
        logger.info("Sales dashboard parquet-only warm skipped (no disk/ready cache yet)")
        return

    df = _get_sales_df(force_refresh=False)
    try:
        _get_soh_map()
    except Exception as exc:
        logger.warning("Sales dashboard SOH pre-warm skipped: %s", exc)
    logger.info(
        "Sales dashboard cache warm-up done in %.1fs (%s rows)",
        time.time() - t0,
        f"{len(df):,}",
    )

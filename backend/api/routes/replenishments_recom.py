"""
Replenishments Recommendations API
Reads replenishment recommendation data from Azure Blob
(default `output/prediction/current/SS26_..._with_refill_recommendation.csv`;
override with env REPLENISHMENTS_RECOM_TABLE). Falls back JSON snapshot `{TABLE}_snapshot` if Delta fails.

Actual columns (discovered / extended):
  Site_Code, Site, Barcode, Sku, Size, Season, Week, Month,
  Fill_Percent, SSL_Clr, PTC, Rank, Store_Soh, Sum_of_Sales,
  WH_Soh, sold_qty_prev_35d (last 35 days sold), sold_qty_prev_5w (legacy), lag1_sold_qty (last week sold), Refill, Recommendation_Comment,
  last_selling_date, days_since_last_sale / days_from_last_selling_date, dead_stock_status (values e.g. active / dead)
"""
from fastapi import APIRouter, Query, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from api.onelake_store import OneLakeJsonStore
from api.download_logger import log_download
import asyncio
import logging
import math
import os
import re
import threading
import time
import pandas as pd
from io import BytesIO
from decimal import Decimal, InvalidOperation
from functools import partial
from pathlib import Path

router = APIRouter(prefix="/api/replenishments-recom", tags=["Replenishments Recommendation"])
logger = logging.getLogger(__name__)
store = OneLakeJsonStore()
_SNAPSHOT_CACHE_DF = None
_SNAPSHOT_CACHE_AT = 0.0
_SNAPSHOT_CACHE_SIG: str | None = None
# Bump when snapshot shaping / KPI / dedupe logic changes — stale in-memory/disk cache is skipped.
_SNAPSHOT_LOGIC_VERSION = 38  # v38: Site_ID name→compact code (T8WW/W021) for export
_SNAPSHOT_CACHE_LOGIC_VER: int | None = None
# Soft TTL for RAM only; disk + source signature is the durable page cache.
_SNAPSHOT_CACHE_TTL_SECONDS = int(os.environ.get("REPLENISHMENTS_CACHE_TTL_SECONDS", "3600"))
_SNAPSHOT_LOCK = threading.Lock()
_API_CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "api_cache"
_DISK_PQ_NAME = "replenishments_recom_snapshot_v1.parquet"
_DISK_KEY_NAME = "replenishments_recom_snapshot_v1.key"
_DISK_EXPORT_CSV = "replenishments_recom_export_v1.csv"
_DISK_EXPORT_S2S_CSV = "replenishments_recom_export_s2s_v1.csv"
_BLOB_SNAPSHOT_NAME = "cache/api_cache/replenishments_recom_snapshot_v1.parquet"
_BLOB_SNAPSHOT_KEY = "cache/api_cache/replenishments_recom_snapshot_v1.key"
_BLOB_EXPORT_CSV = "cache/api_cache/replenishments_recom_export_v1.csv"
_BLOB_EXPORT_S2S_CSV = "cache/api_cache/replenishments_recom_export_s2s_v1.csv"
_REFILL_CSV_NAME = (
    "SS26_weekly_predictions_formula_next_week_with_refill_recommendation.csv"
)
_PIPELINE_OUTPUT = Path(__file__).resolve().parents[2] / "pipeline" / "output_files"

_DEFAULT_REPLEN_TABLE = (
    "prediction/current/SS26_weekly_predictions_formula_next_week_with_refill_recommendation"
)
TABLE = (
    os.environ.get("REPLENISHMENTS_RECOM_TABLE", _DEFAULT_REPLEN_TABLE) or _DEFAULT_REPLEN_TABLE
).strip()
# Optional: separate grade table in OneLake (e.g. "store_grades"). Leave blank to skip.
GRADE_TABLE = (os.environ.get("REPLENISHMENTS_GRADE_TABLE", "store_grades") or "").strip()
# Must match Frontend-V4 `SITE_TRANSFER_REFILL_SOURCE` / Lakehouse pipeline.
SITE_TO_SITE_REFILL_SOURCE = "SITE_TO_SITE_TRANSFER_REQUIRED"

# In-memory grade lookup: {site_id_upper: grade_str}
_GRADE_MAP: dict[str, str] = {}
_GRADE_MAP_AT: float = 0.0
_GRADE_MAP_TTL: int = int(os.environ.get("REPLENISHMENTS_GRADE_TTL_SECONDS", "3600"))

# Lakehouse `predicted_qty` — optional column aliases (table / exports); KPI forecasted uses components below.
PREDICTED_QTY_COLUMN_ALIASES: list[str] = [
    "predicted_qty",
    "Predicted_Qty",
    "Predicted_QTY",
    "PREDICTED_QTY",
    "predicted_Qty",
    "PredictedQty",
    "predictedQty",
]


def _barcode_to_str(v: object) -> str:
    """
    Convert barcode to plain digit string (avoid scientific notation like 8.91E+12).
    """
    if v is None:
        return ""
    if isinstance(v, bool):
        return ""
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if pd.isna(v):
            return ""
        return str(int(v))
    s = str(v).strip()
    if not s:
        return ""
    low = s.lower()
    if low in ("nan", "none", "null"):
        return ""
    try:
        d = Decimal(s)
        # format with 'f' avoids exponent; then remove trailing decimal zeros
        out = format(d, "f")
        if "." in out:
            out = out.rstrip("0").rstrip(".")
        return out
    except (InvalidOperation, ValueError):
        return s


def _optional_int_cell(val: object) -> int | None:
    """Coerce to int or None when missing / NaN (for optional lake columns like days_since_last_sale)."""
    if val is None:
        return None
    x = pd.to_numeric(val, errors="coerce")
    if isinstance(x, (pd.Series, pd.DataFrame)):
        return None
    try:
        if pd.isna(x):
            return None
    except (ValueError, TypeError):
        return None
    try:
        return int(x)
    except (ValueError, TypeError, OverflowError):
        return None


def _optional_float_out(val: object) -> float | None:
    """API JSON float or None; missing / NaN → None."""
    if val is None:
        return None
    x = pd.to_numeric(val, errors="coerce")
    if isinstance(x, (pd.Series, pd.DataFrame)):
        return None
    try:
        if pd.isna(x):
            return None
    except (ValueError, TypeError):
        return None
    try:
        return float(x)
    except (ValueError, TypeError, OverflowError):
        return None


def _format_lake_date_cell(val: object) -> str:
    """Normalize lake date/timestamp cells to YYYY-MM-DD or empty string."""
    if val is None:
        return ""
    if isinstance(val, float) and pd.isna(val):
        return ""
    ts = pd.to_datetime(val, errors="coerce")
    if pd.isna(ts):
        s = str(val).strip()
        low = s.lower()
        if not s or low in ("nan", "none", "null", "nat"):
            return ""
        return s[:10] if len(s) >= 10 and s[4] == "-" else s
    try:
        return pd.Timestamp(ts).strftime("%Y-%m-%d")
    except Exception:
        s = str(val).strip()
        return s


def _int_from_cell(val: object, default: int = 0) -> int:
    """Coerce a lake/table cell to int. NaN is truthy in Python so `(nan or 0)` stays NaN and breaks `int()`."""
    x = pd.to_numeric(val, errors="coerce")
    if isinstance(x, (pd.Series, pd.DataFrame)):
        return default
    try:
        if pd.isna(x):
            return default
    except (ValueError, TypeError):
        return default
    try:
        return int(x)
    except (ValueError, TypeError, OverflowError):
        return default


def _str_series_blank_mask(s: pd.Series) -> pd.Series:
    """True where value is missing or meaningless string (after strip)."""
    if s is None or len(s) == 0:
        return pd.Series([True] * len(s), index=s.index)
    t = s.astype(str).str.strip()
    low = t.str.lower()
    return s.isna() | t.eq("") | low.isin(["nan", "none", "null", ""])


def _normalize_filter_sku_key(val: object) -> str:
    """
    Match query-param / dropdown SKU to lake `SKU` cells.
    Delta/Excel often yields 12345.0 or 1.23e+12 strings while UI sends 12345 — strict == would return zero rows.
    """
    if val is None or val is pd.NA:
        return ""
    if isinstance(val, float):
        if pd.isna(val):
            return ""
        try:
            if val.is_integer() and math.isfinite(val) and abs(val) < 1e15:
                return str(int(val)).upper()
        except (ValueError, OverflowError, AttributeError):
            pass
    s = str(val).strip()
    if not s or s.lower() in ("nan", "none", "null"):
        return ""
    try:
        f = float(s)
        if (
            math.isfinite(f)
            and f == int(f)
            and abs(f) < 1e15
            and "e" not in s.lower()
        ):
            return str(int(f)).upper()
    except (ValueError, OverflowError):
        pass
    return s.upper()


def _normalize_filter_week_key(val: object) -> str:
    """Match week filter to lake `Week` (16 vs 16.0 vs W16)."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    if isinstance(val, float):
        try:
            if val.is_integer() and math.isfinite(val) and abs(val) < 1e4:
                return str(int(val))
        except (ValueError, OverflowError, AttributeError):
            pass
    s = str(val).strip().upper().replace("W", "")
    if not s or s in ("NAN", "NONE"):
        return ""
    try:
        f = float(s)
        if math.isfinite(f) and f == int(f) and abs(f) < 1e4:
            return str(int(f))
    except (ValueError, OverflowError):
        pass
    return s


def _backfill_sparse_site_id(out: pd.DataFrame) -> pd.DataFrame:
    """
    Fabric often has both `Site_ID` (sparse) and `Site_Code` / `STORE_CODE` (filled).
    Alias logic skips mapping when `Site_ID` already exists as a column → blanks never fill.
    """
    if "Site_ID" not in out.columns or out.empty:
        return out
    for src in (
        "Site_Code",
        "site_code",
        "Site_Code",
        "STORE_CODE",
        "store_id",
        "Store_Id",
        "Store_ID",
        "LOCATION_CODE",
        "Location_Code",
        "Plant_Code",
        "Retail_Store_Code",
        "STORE_ID",
        "StoreKey",
        "store_key",
        "SiteKey",
        "site_key",
    ):
        if src not in out.columns or src == "Site_ID":
            continue
        blank = _str_series_blank_mask(out["Site_ID"])
        if not bool(blank.any()):
            break
        cand = out[src].astype(str).str.strip()
        good = ~_str_series_blank_mask(out[src])
        fill = blank & good
        if not bool(fill.any()):
            continue
        out = out.copy()
        out.loc[fill, "Site_ID"] = cand.loc[fill]
    return out


# Compact retail codes: 115, P070, T8WW, W021, TC3M — not full store labels.
_COMPACT_SITE_CODE_RE = re.compile(r"^[A-Za-z0-9]{2,10}$")
_PAREN_SITE_CODE_RE = re.compile(r"\(\s*([A-Za-z0-9]{2,10})\s*\)\s*$")
_LEADING_SITE_CODE_RE = re.compile(r"^([A-Za-z]?\d{2,6})\b")


def _site_id_looks_like_name(val: object) -> bool:
    """True when Site_ID is a store label (spaces / long text) instead of a compact code."""
    s = str(val or "").strip()
    if not s or s.lower() in ("nan", "none", "<na>", "null"):
        return False
    # Pure alnum short token = code (115, P070, T8WW, TC3M).
    if _COMPACT_SITE_CODE_RE.fullmatch(s) and " " not in s and "-" not in s:
        return False
    if " " in s or "-" in s or len(s) > 12:
        return True
    return bool(re.search(r"[A-Za-z]{4,}", s))


def _extract_compact_site_code(label: object) -> str:
    """Pull T8WW / W021 / P070 / TC3M / 115 out of a store name or mis-filed Site_ID."""
    s = str(label or "").strip()
    if not s or s.lower() in ("nan", "none", "<na>", "null"):
        return ""
    if _COMPACT_SITE_CODE_RE.fullmatch(s) and " " not in s and "-" not in s:
        return s.upper()
    m = _PAREN_SITE_CODE_RE.search(s)
    if m:
        return str(m.group(1)).strip().upper()
    m2 = _LEADING_SITE_CODE_RE.match(s)
    if m2:
        return str(m2.group(1)).strip().upper()
    return ""


def _normalize_site_id_vs_name(out: pd.DataFrame) -> pd.DataFrame:
    """Ensure Site = name, Site_ID = compact code (Centro/RRL often dump name into both)."""
    if out is None or out.empty or "Site_ID" not in out.columns:
        return out
    sid = out["Site_ID"].astype(str).str.strip()
    bad = sid.map(_site_id_looks_like_name)
    if not bool(bad.any()):
        return out
    out = out.copy()
    site = (
        out["Site"].astype(str).str.strip()
        if "Site" in out.columns
        else pd.Series("", index=out.index, dtype=object)
    )
    # Prefer code from Site_ID label, then from Site name.
    from_sid = sid.map(_extract_compact_site_code)
    from_site = site.map(_extract_compact_site_code)
    fixed = from_sid.where(from_sid.astype(str).str.len() > 0, from_site)
    # Keep Site as the human label when it was blank / equal to the bad id.
    if "Site" in out.columns:
        need_name = bad & (
            site.eq("")
            | site.str.lower().isin(["nan", "none", "null"])
            | site.str.upper().eq(sid.str.upper())
        )
        out.loc[need_name, "Site"] = sid.loc[need_name]
    fill = bad & fixed.astype(str).str.len().gt(0)
    out.loc[fill, "Site_ID"] = fixed.loc[fill]
    logger.info(
        "Normalized Site_ID name→code on %s rows (%s unique)",
        int(fill.sum()),
        int(fixed.loc[fill].nunique()),
    )
    return out


def _backfill_sparse_sku(out: pd.DataFrame) -> pd.DataFrame:
    """Same pattern as Site_ID when `SKU` exists but empty and style/article columns hold the code."""
    if "SKU" not in out.columns or out.empty:
        return out
    for src in (
        "Article",
        "article",
        "ARTICLE",
        "Style_Code",
        "style_code",
        "STYLE_CODE",
        "Item_Code",
        "ITEM_CODE",
        "EAN_SKU",
        "Merch_SKU",
        "Parent_SKU",
        "Sku_Code",
        "SKU_Code",
    ):
        if src not in out.columns or src == "SKU":
            continue
        blank = _str_series_blank_mask(out["SKU"])
        if not bool(blank.any()):
            break
        good = ~_str_series_blank_mask(out[src])
        fill = blank & good
        if not bool(fill.any()):
            continue
        out = out.copy()
        out.loc[fill, "SKU"] = out.loc[fill, src].astype(str).str.strip()
    return out


_STYLE_IN_SIZE_RE = re.compile(r"^[A-Za-z]{1,3}\d{3,}$")


def _promote_size_to_sku_when_style_like(out: pd.DataFrame) -> pd.DataFrame:
    """
    Lakehouse rows often leave `SKU` empty but put the style/merch code in `Size` (e.g. T09839 next to EAN barcode).
    UI columns then look 'shifted' even though the table is aligned. Promote only when barcode looks like a long EAN.
    """
    if "SKU" not in out.columns or "Size" not in out.columns or "Barcode" not in out.columns or out.empty:
        return out
    sku_blank = _str_series_blank_mask(out["SKU"])
    if not bool(sku_blank.any()):
        return out
    sz = out["Size"].astype(str).str.strip()
    style_like = sz.str.match(_STYLE_IN_SIZE_RE, na=False)
    bc = out["Barcode"].map(_barcode_to_str).astype(str).str.strip()
    good_bc = bc.str.match(r"^\d{8,}$", na=False)
    fill = sku_blank & style_like & good_bc
    if not bool(fill.any()):
        return out
    out = out.copy()
    out.loc[fill, "SKU"] = sz.loc[fill]
    out.loc[fill, "Size"] = ""
    return out


def _backfill_refill_source(out: pd.DataFrame) -> pd.DataFrame:
    if "refill_source" not in out.columns or out.empty:
        return out
    for src in (
        "Refill_Status",
        "refill_status",
        "REFILL_STATUS",
        "Allocation_Status",
        "allocation_status",
        "refill_type",
        "Refill_Type",
        "Fill_Status",
        "fill_status",
    ):
        if src not in out.columns or src == "refill_source":
            continue
        blank = _str_series_blank_mask(out["refill_source"])
        if not bool(blank.any()):
            break
        good = ~_str_series_blank_mask(out[src])
        fill = blank & good
        if not bool(fill.any()):
            continue
        out = out.copy()
        out.loc[fill, "refill_source"] = out.loc[fill, src].astype(str).str.strip()
    return out


def _crossfill_recommendation_texts(out: pd.DataFrame) -> pd.DataFrame:
    """Cross-fill between comment / note — disabled to avoid wrong values bleeding across columns."""
    return out


def _find_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    col_map = {str(c).strip().lower(): str(c) for c in df.columns}
    for cand in candidates:
        got = col_map.get(cand.strip().lower())
        if got:
            return got
    return None


def _repair_merch_sku_and_size_columns(out: pd.DataFrame) -> pd.DataFrame:
    """
    Fabric / Delta exports often include a surrogate numeric ``SKU`` (row keys like 0,1,2) while the
    planner-facing style code lives in ``Sku``, ``MERCH_SKU``, or ``Article``. The alias pass skips
    ``Sku`` when ``SKU`` already exists — merge those here.

    ``Size`` can be a float placeholder (0.0) while the real label is ``Std_Size`` / ``Garment_Size`` / etc.
    """
    if out.empty:
        return out
    out = out.copy()

    if "SKU" in out.columns:
        cur = out["SKU"].astype(str).str.strip()
        cur_num = pd.to_numeric(cur, errors="coerce")
        # Small integer / short numeric-only keys are almost never the merch style the UI should show.
        digit_only = cur.str.match(r"^\d+(\.0+)?$", na=False)
        looks_like_internal_id = digit_only & cur_num.notna() & (cur_num >= 0) & (cur_num < 1_000_000)
        blank_sku = _str_series_blank_mask(out["SKU"])
        for src in (
            "MERCH_SKU",
            "Merch_SKU",
            "Merch_Sku",
            "Sku",
            "STYLE_SKU",
            "STYLE_CODE",
            "Style_Code",
            "style_code",
            "Article",
            "article",
            "ARTICLE",
        ):
            if src not in out.columns:
                continue
            alt = out[src].astype(str).str.strip()
            al = alt.str.lower()
            good = ~out[src].isna() & ~alt.eq("") & ~al.isin(["nan", "none", "null"])
            fill = good & (looks_like_internal_id | blank_sku)
            if not bool(fill.any()):
                continue
            n = int(fill.sum())
            logger.info("%s: promoting planner SKU from %s for %s row(s)", TABLE, src, n)
            out.loc[fill, "SKU"] = alt.loc[fill]
            cur = out["SKU"].astype(str).str.strip()
            cur_num = pd.to_numeric(cur, errors="coerce")
            digit_only = cur.str.match(r"^\d+(\.0+)?$", na=False)
            looks_like_internal_id = digit_only & cur_num.notna() & (cur_num >= 0) & (cur_num < 1_000_000)
            blank_sku = _str_series_blank_mask(out["SKU"])

    if "Size" in out.columns:
        main_s = out["Size"].astype(str).str.strip()
        low = main_s.str.lower()
        bad = (
            out["Size"].isna()
            | main_s.eq("")
            | low.isin(["nan", "none", "null", "0", "0.0", "0.00"])
        )
        for src in (
            "Std_Size",
            "Standard_Size",
            "Garment_Size",
            "Product_Size",
            "Size_Desc",
            "Size_Name",
            "SIZE",
            "size",
        ):
            if src not in out.columns or src == "Size":
                continue
            alt = out[src].astype(str).str.strip()
            al = alt.str.lower()
            good = ~out[src].isna() & ~alt.eq("") & ~al.isin(["nan", "none", "null", "0", "0.0"])
            fill = bad & good
            if not bool(fill.any()):
                continue
            logger.info("%s: filling Size from %s for %s row(s)", TABLE, src, int(fill.sum()))
            out.loc[fill, "Size"] = alt.loc[fill]
            main_s = out["Size"].astype(str).str.strip()
            low = main_s.str.lower()
            bad = (
                out["Size"].isna()
                | main_s.eq("")
                | low.isin(["nan", "none", "null", "0", "0.0", "0.00"])
            )
        sn = pd.to_numeric(out["Size"], errors="coerce")
        z = sn.notna() & (sn == 0)
        if bool(z.any()):
            out.loc[z, "Size"] = ""

    return out


def _apply_repl_column_aliases(df: pd.DataFrame) -> pd.DataFrame:
    """
    OneLake rows: add missing canonical columns from known name variants, then collapse
    duplicate grain (store×barcode×week[/sku]) so SUMs match Fabric-style distinct rows.
    """
    alias_groups: dict[str, list[str]] = {
        "Site": ["Site", "site", "site_name", "Site_Name", "SiteName", "siteName", "store_name", "Store_Name", "SITE_NAME"],
        "Site_ID": ["Site_ID", "site_id", "site_code", "Site_Code", "store_code", "Store_Code"],
        "store_type": [
            "store_type",
            "Store_Type",
            "STORE_TYPE",
            "storetype",
            "StoreType",
            "channel",
            "Channel",
            "outlet_type",
        ],
        "store_grade": ["store_grade", "Store_Grade", "STORE_GRADE", "grade", "Grade", "GRADE"],
        "SKU": ["SKU", "Sku", "sku"],
        "Barcode": ["Barcode", "barcode"],
        "Size": ["Size", "size"],
        "Season": ["Season", "season"],
        "Week": ["Week", "week"],
        "Month": ["Month", "month"],
        "Sum_of_Sales": [
            "Sum_of_Sales",
            "sum_of_sales",
            "total_net_sales",
            "total_sales",
            "sales_column",
            "Sales_Column",
        ],
        "sold_qty_prev_5w": [
            "sold_qty_prev_5w",
            "Sold_Qty_Prev_5w",
            "Sold_Qty_Prev_5W",
            "sold_qty_prev_5W",
            "SoldQtyPrev5w",
        ],
        "sold_qty_prev_35d": [
            "sold_qty_prev_35d",
            "Sold_Qty_Prev_35d",
            "Sold_Qty_Prev_35D",
            "sold_qty_prev_35D",
            "sold_qty_last_35d",
            "Sold_Qty_Last_35d",
            "sold_qty_last_35_days",
            "last_35_days_sold_qty",
            "Last_35_Days_Sold_Qty",
        ],
        "lag1_sold_qty": [
            "lag1_sold_qty",
            "Lag1_Sold_Qty",
            "LAG1_Sold_Qty",
            "last_week_sold_qty",
            "Last_Week_Sold_Qty",
        ],
        "last_selling_date": [
            "last_selling_date",
            "Last_Selling_Date",
            "LAST_SELLING_DATE",
            "Last_Sale_Date",
            "last_sale_date",
        ],
        "days_since_last_sale": [
            "days_since_last_sale",
            "Days_Since_Last_Sale",
            "DAYS_SINCE_LAST_SALE",
            "days_from_last_selling_date",
            "Days_From_Last_Selling_Date",
            "days_since_last_sales",
            "Days_Since_Last_Sales",
        ],
        "dead_stock_status": [
            "dead_stock_status",
            "Dead_Stock_Status",
            "DEAD_STOCK_STATUS",
            "dead_stock_Status",
            # legacy / alternate lake names
            "stock_status",
            "Stock_Status",
            "STOCK_STATUS",
            "inventory_status",
            "Inventory_Status",
            "Store_Stock_Status",
            "store_stock_status",
        ],
        "Store_Soh": [
            "Store_Soh",
            "store_soh",
            "Store_SOH",
            "STORE_SOH",
            "Store_Stock",
            "store_soh_on_hand",
        ],
        "last_week_store_soh": [
            "last_week_store_soh",
            "Last_Week_Store_SOH",
            "last_week_soh",
            "last_week_store_stock",
            "Last_Week_Store_Stock",
        ],
        "WH_Soh": [
            "WH_Soh",
            "wh_soh",
            "WH_SOH",
            "wh_SOH",
            "Wh_Soh",
            "Warehouse_Soh",
            "warehouse_soh",
            "WH_Stock",
        ],
        "wh_soh_end_barcode": [
            "wh_soh_end_barcode",
            "WH_SOH_End_Barcode",
            "Wh_Soh_End_Barcode",
            "warehouse_soh_after_allocation",
            "wh_soh_after_allocation",
        ],
        # Native predicted measure — same candidate order as KPI SUM.
        "predicted_qty": list(PREDICTED_QTY_COLUMN_ALIASES),
        "forecasted_refill": [
            "forecasted_refill",
            "forecasted_Refill",
            "forecasted_refil",
            "predicted_qty",
            "Predicted_Qty",
        ],
        "total_fulfilled_qty": [
            "total_fulfilled_qty",
            "Total_Fulfilled_Qty",
            "total_fulfilled",
        ],
        "covered_by_store_soh": [
            "covered_by_store_soh",
            "Covered_By_Store_Soh",
            "Covered_By_Store_SOH",
            "qty_from_store_soh",
        ],
        "covered_by_wh_soh": [
            "covered_by_wh_soh",
            "Covered_By_Wh_Soh",
            "Covered_By_WH_Soh",
            "Covered_By_Wh_SOH",
            "qty_from_wh_soh",
        ],
        "unfilled_qty": [
            "unfilled_qty",
            "Unfilled_Qty",
            "unfilled",
            "Unfilled",
        ],
        "refill_source": ["refill_source", "Refill_Source", "refill_Source"],
        "recommended_refill": [
            "recommended_refill",
            "recommendation_refill_qty",
            "Recommendation_Refill_Qty",
            "Refill",
            "refill",
            "REFILL",
            "refill_recommandation",
            "refill_recommendation",
            "need_qty",
            "panel_total_qty",
        ],
        "priority_score": [
            "priority_score",
            "Priority_Score",
            "PRIORITY_SCORE",
            "PriorityScore",
            "priority",
            "Priority",
        ],
        "priority_score_raw": [
            "priority_score_raw",
            "Priority_Score_Raw",
            "priority_score_Raw",
            "PRIORITY_SCORE_RAW",
        ],
        "total_sold_qty": [
            "total_sold_qty",
            "Total_Sold_Qty",
            "total_sold",
            "Total_Sold",
            "TOTAL_SOLD_QTY",
        ],
        "priority_expl_timing_bonus": [
            "priority_expl_timing_bonus",
            "Priority_Expl_Timing_Bonus",
            "priority_explanation_timing_bonus",
        ],
        "replen_site_transfer_qty": [
            "replen_site_transfer_qty",
            "Replen_Site_Transfer_Qty",
            "site_transfer_qty",
        ],
        "Week_Date_Range": ["Week_Date_Range", "week_date_range"],
        "Recommendation_Comment": ["Recommendation_Comment", "recommendation_comment", "refill_comment"],
        "refill_note": ["refill_note", "refill_status", "refill_comment"],
        "Fill_Percent": ["Fill_Percent", "fill_percent", "weekly_sold_speed"],
        "Rank": ["Rank", "rank"],
        "DEPARTMENT": ["DEPARTMENT", "department", "Category", "category"],
    }

    out = df.copy()
    for canonical, aliases in alias_groups.items():
        if canonical in out.columns:
            continue
        src = _find_col(out, aliases)
        if src:
            out[canonical] = out[src]

    # Prefer native last-35-days sold; if the lake has not added it yet, reuse last-5-weeks measure.
    if "sold_qty_prev_35d" not in out.columns:
        if "sold_qty_prev_5w" in out.columns:
            out["sold_qty_prev_35d"] = pd.to_numeric(out["sold_qty_prev_5w"], errors="coerce").fillna(0)
        else:
            out["sold_qty_prev_35d"] = 0

    # Ensure core fields always exist so downstream code doesn't break.
    for core in ["Site", "Site_ID", "store_grade", "SKU", "Store_Soh", "WH_Soh", "wh_soh_end_barcode", "recommended_refill", "Sum_of_Sales", "priority_score"]:
        if core not in out.columns:
            out[core] = None

    # Mirror Sum_of_Sales for API sort key `total_sales` (snapshot / pandas path).
    if "Sum_of_Sales" in out.columns and "total_sales" not in out.columns:
        out["total_sales"] = pd.to_numeric(out["Sum_of_Sales"], errors="coerce")

    # Normalize barcode display to non-scientific string.
    if "Barcode" in out.columns:
        out["Barcode"] = out["Barcode"].apply(_barcode_to_str)

    # Merge parallel merch columns when surrogate ``SKU`` / placeholder ``Size`` already exist.
    out = _repair_merch_sku_and_size_columns(out)

    # Lake often keeps store code / status in parallel columns while canonical column exists but is empty.
    out = _backfill_sparse_site_id(out)
    out = _normalize_site_id_vs_name(out)
    out = _backfill_sparse_sku(out)
    out = _promote_size_to_sku_when_style_like(out)
    out = _backfill_refill_source(out)
    out = _crossfill_recommendation_texts(out)

    try:
        from pipeline.pipeline_inputs import exclude_closed_replenishment_sites
    except ImportError:
        from pipeline_inputs import exclude_closed_replenishment_sites  # type: ignore
    out = exclude_closed_replenishment_sites(out, site_id_col="Site_ID", site_name_col="Site")

    return _dedupe_recom_grain(out)


def _dedupe_recom_grain(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate rows by store × barcode × week instead of dropping them, ensuring sums match the source."""
    if df.empty or "Barcode" not in df.columns:
        return df
    
    # Identify key columns for grouping
    site_key = "_disp_site_id" # Temporary internal key
    df[site_key] = _effective_site_series(df).astype(str).str.strip().str.upper()
    
    group_cols = [site_key, "Barcode"]
    if "Week" in df.columns:
        group_cols.append("Week")
    if "SKU" in df.columns:
        group_cols.append("SKU")
        
    # Categorical columns to keep (first value)
    categorical_cols = [
        "Site", "Site_ID", "store_type", "store_grade", "Barcode", "SKU", "Size", "Season", "Week", "Month", 
        "Week_Date_Range", "Recommendation_Comment", "refill_note", "refill_source", 
        "last_selling_date", "dead_stock_status"
    ]
    # Metric columns to sum
    sum_cols = [
        "Sum_of_Sales", "forecasted_refill", "recommended_refill",
        "covered_by_store_soh", "covered_by_wh_soh", "total_fulfilled_qty", "unfilled_qty",
        "sold_qty_prev_5w", "sold_qty_prev_35d", "lag1_sold_qty",
        "last_week_store_soh",
        "priority_score_raw",
        "total_sold_qty",
        "priority_expl_timing_bonus",
        # Split lake rows: sum SOH with other measures so export/KPIs match source totals, not a single max line.
        "Store_Soh",
        "WH_Soh",
    ]
    # Other metrics (max)
    max_cols = ["priority_score", "days_since_last_sale", "wh_soh_end_barcode"]
    
    available_cats = [c for c in categorical_cols if c in df.columns]
    available_sums = [c for c in sum_cols if c in df.columns]
    available_maxs = [c for c in max_cols if c in df.columns]
    
    agg_dict = {}
    for c in available_cats: agg_dict[c] = "first"
    for c in available_sums: agg_dict[c] = "sum"
    for c in available_maxs: agg_dict[c] = "max"
    
    # Perform aggregation
    agg_df = df.groupby(group_cols, as_index=False).agg(agg_dict)
    
    # Cleanup temp key
    if site_key in agg_df.columns:
        agg_df = agg_df.drop(columns=[site_key])
    if site_key in df.columns:
        df.drop(columns=[site_key], inplace=True)
        
    return agg_df.reset_index(drop=True)


def _norm_grade_site_key(val: object) -> str:
    s = str(val or "").strip().upper()
    if s.lower() in ("", "nan", "none", "<na>", "null"):
        return ""
    # Strip trailing .0 from numeric Excel/CSV site codes
    if s.endswith(".0") and s[:-2].replace(".", "", 1).isdigit():
        s = s[:-2]
    return s


def _load_grade_map() -> dict[str, str]:
    """
    Load site_id → store_grade mapping from (first hit wins per key, later sources fill gaps):
      1. STORE_GRADE.xlsx / blob Store Grade List (same as Sales filters)
      2. Customer Master store_grade
      3. Optional OneLake GRADE_TABLE (env REPLENISHMENTS_GRADE_TABLE)
    """
    global _GRADE_MAP, _GRADE_MAP_AT
    now = time.time()
    if _GRADE_MAP and (now - _GRADE_MAP_AT) < _GRADE_MAP_TTL:
        return _GRADE_MAP

    grade_map: dict[str, str] = {}

    # 1) Canonical STORE_GRADE file (local / blob) — primary source for dashboards
    try:
        from pipeline.store_grade import load_store_grade_map

        gm = load_store_grade_map()
        if gm is not None and not gm.empty:
            for _, r in gm.iterrows():
                key = _norm_grade_site_key(r.get("site_code"))
                grade = str(r.get("store_grade") or "").strip()
                if key and grade and grade.lower() not in ("nan", "none"):
                    grade_map[key] = grade
            logger.info("Loaded %s grade entries from STORE_GRADE file.", len(grade_map))
    except Exception as exc:
        logger.warning("STORE_GRADE map load failed: %s", exc)

    # 2) Customer Master — fill any sites missing from STORE_GRADE
    try:
        from pipeline.customer_master import load_customer_master

        cm = load_customer_master(retail_only=True)
        if cm is not None and not cm.empty and "store_grade" in cm.columns:
            added = 0
            for col in ("site_code", "site_manual_code", "site_key", "site_id"):
                if col not in cm.columns:
                    continue
                sub = cm[[col, "store_grade"]].copy()
                sub["_k"] = sub[col].map(_norm_grade_site_key)
                sub["_g"] = sub["store_grade"].astype(str).str.strip()
                sub = sub[
                    sub["_k"].ne("")
                    & sub["_g"].ne("")
                    & ~sub["_g"].str.lower().isin({"nan", "none"})
                ]
                for key, grade in zip(sub["_k"], sub["_g"]):
                    if key not in grade_map:
                        grade_map[key] = grade
                        added += 1
            if added:
                logger.info("Filled %s extra grades from Customer Master.", added)
    except Exception as exc:
        logger.debug("Customer Master grade fallback skipped: %s", exc)

    # 3) Optional OneLake / blob grade table (legacy)
    if GRADE_TABLE:
        rows: list[dict] = []
        try:
            rows = store.read_delta_table(GRADE_TABLE)
        except Exception:
            pass
        if not rows:
            try:
                rows = store.read_table(f"{GRADE_TABLE}_snapshot")
            except Exception:
                pass
        if rows:
            gdf = pd.DataFrame(rows)
            gdf.columns = [str(c).strip() for c in gdf.columns]
            site_col = next(
                (
                    c
                    for c in gdf.columns
                    if c.lower().replace("_", "") in ("siteid", "sitecode", "site")
                ),
                None,
            )
            grade_col = next(
                (
                    c
                    for c in gdf.columns
                    if c.lower().replace("_", "") in ("storegrade", "grade")
                ),
                None,
            )
            if site_col and grade_col:
                lake_added = 0
                for _, row in gdf[[site_col, grade_col]].drop_duplicates(subset=[site_col]).iterrows():
                    key = _norm_grade_site_key(row[site_col])
                    grade = str(row[grade_col] or "").strip()
                    if key and grade and grade.lower() not in ("nan", "none") and key not in grade_map:
                        grade_map[key] = grade
                        lake_added += 1
                if lake_added:
                    logger.info(
                        "Filled %s grades from lake table '%s'.", lake_added, GRADE_TABLE
                    )

    if not grade_map:
        logger.warning(
            "No store grades found (STORE_GRADE / Customer Master / %s) — Grade column blank.",
            GRADE_TABLE or "lake",
        )

    _GRADE_MAP = grade_map
    _GRADE_MAP_AT = now
    return grade_map


def _enrich_store_grade(df: pd.DataFrame) -> pd.DataFrame:
    """
    Fill blank / missing store_grade values using STORE_GRADE + Customer Master.
    Matches on Site_ID (and Site leading code when Site_ID blank).
    """
    grade_map = _load_grade_map()
    if not grade_map:
        if "store_grade" not in df.columns:
            df["store_grade"] = ""
        return df

    if "store_grade" not in df.columns:
        df["store_grade"] = ""
    else:
        # Normalize float nan → blank so we can fill
        df["store_grade"] = (
            df["store_grade"]
            .astype(str)
            .str.strip()
            .replace({"nan": "", "None": "", "NaN": "", "<NA>": "", "null": ""})
        )

    site_id_series = (
        df.get("Site_ID", pd.Series("", index=df.index))
        .map(_norm_grade_site_key)
    )
    # Fallback: "8563 - SSL-Rajahmundry" → 8563
    if "Site" in df.columns:
        site_from_name = (
            df["Site"]
            .astype(str)
            .str.strip()
            .str.extract(r"^([A-Za-z]?\d+)", expand=False)
            .map(_norm_grade_site_key)
        )
        site_id_series = site_id_series.where(site_id_series.astype(str).str.len() > 0, site_from_name)

    blank_mask = df["store_grade"].astype(str).str.strip().isin(["", "nan", "None", "NaN"])
    if blank_mask.any():
        mapped = site_id_series[blank_mask].map(grade_map)
        df.loc[blank_mask, "store_grade"] = mapped.fillna("").astype(str)

    return df


def clear_replenishments_snapshot_cache() -> None:
    """Drop in-memory replenishment snapshot (disk parquet kept until source signature changes)."""
    global _SNAPSHOT_CACHE_DF, _SNAPSHOT_CACHE_AT, _SNAPSHOT_CACHE_LOGIC_VER, _SNAPSHOT_CACHE_SIG
    with _SNAPSHOT_LOCK:
        _SNAPSHOT_CACHE_DF = None
        _SNAPSHOT_CACHE_AT = 0.0
        _SNAPSHOT_CACHE_LOGIC_VER = None
        _SNAPSHOT_CACHE_SIG = None


def _local_refill_candidates() -> list[Path]:
    return [
        _PIPELINE_OUTPUT / "prediction" / "current" / _REFILL_CSV_NAME,
        _PIPELINE_OUTPUT / "prediction" / _REFILL_CSV_NAME,
        _PIPELINE_OUTPUT / _REFILL_CSV_NAME,
    ]


def _newest_local_refill() -> Path | None:
    found: list[Path] = []
    for p in _local_refill_candidates():
        try:
            if p.exists() and p.stat().st_size > 0:
                found.append(p)
        except OSError:
            continue
    if not found:
        return None
    return max(found, key=lambda p: p.stat().st_mtime_ns)


def _prefer_blob_refill() -> bool:
    """
    Local demo should show the same Azure blob prediction file as hosted.
    Opt out with REPLENISHMENTS_PREFER_LOCAL=1 (use pipeline/output_files only).
    """
    if (os.environ.get("REPLENISHMENTS_PREFER_LOCAL") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        return False
    return bool((os.environ.get("AZURE_STORAGE_CONNECTION_STRING") or "").strip())


def _ensure_blob_refill_path() -> Path | None:
    """Download output/prediction/... refill file into blob_cache (csv/parquet/xlsx)."""
    try:
        path = store.ensure_output_table(TABLE, exts=(".csv", ".parquet", ".xlsx"))
        if path is not None and path.exists() and path.stat().st_size > 0:
            return path
    except Exception as exc:
        logger.warning("[snapshot] blob refill ensure failed: %s", exc)
    return None


def _blob_refill_etag() -> str | None:
    """Current blob etag for the replenishment recommendation table (csv/parquet/xlsx)."""
    try:
        for ext in (".csv", ".parquet", ".xlsx"):
            blob_name = store._blob_name(TABLE, ext)
            etag = store._blob_etag(blob_name)
            if etag:
                return f"{ext}:{etag}"
    except Exception as exc:
        logger.debug("replenishment blob etag lookup failed: %s", exc)
    return None


def _disk_cache_paths() -> tuple[Path, Path]:
    return _API_CACHE_DIR / _DISK_PQ_NAME, _API_CACHE_DIR / _DISK_KEY_NAME


def _export_disk_paths() -> tuple[Path, Path]:
    return _API_CACHE_DIR / _DISK_EXPORT_CSV, _API_CACHE_DIR / _DISK_EXPORT_S2S_CSV


def _write_csv_export_file(path: Path, export_df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    # utf-8-sig → BOM so Excel opens correctly; text mode avoids pandas binary+encoding issues.
    with tmp.open("w", encoding="utf-8-sig", newline="") as fh:
        export_df.to_csv(fh, index=False)
    os.replace(tmp, path)


def _build_prebuilt_export_df(df: pd.DataFrame, *, is_site_to_site: bool) -> pd.DataFrame:
    """Same slice / columns as unfiltered `/download-recommendations`."""
    if df is None or df.empty:
        return pd.DataFrame()
    refill = SITE_TO_SITE_REFILL_SOURCE if is_site_to_site else None
    ordered = _apply_recommendation_row_filters(
        df,
        None,
        None,
        None,
        None,
        None,
        refill,
        None,
        exclude_site_transfer_zero_fill=not is_site_to_site,
    )
    if ordered.empty:
        return pd.DataFrame()
    ordered = ordered.assign(_fc_disp=_forecasted_qty_display_series(ordered))
    ordered = _sort_recommendations_df(
        ordered, sort_by="forecasted_refill", sort_order="DESC"
    )
    return _export_recommendations_df(ordered, is_site_to_site=is_site_to_site)


def _write_prebuilt_exports_from_snapshot(df: pd.DataFrame) -> dict:
    """Materialize full-table CSV exports so hosted download is FileResponse, not rebuild."""
    out: dict = {"ok": False}
    if df is None or df.empty:
        out["error"] = "empty snapshot"
        return out
    try:
        t0 = time.perf_counter()
        default_path, s2s_path = _export_disk_paths()
        default_df = _build_prebuilt_export_df(df, is_site_to_site=False)
        if not default_df.empty:
            _write_csv_export_file(default_path, default_df)
        s2s_df = _build_prebuilt_export_df(df, is_site_to_site=True)
        if not s2s_df.empty:
            _write_csv_export_file(s2s_path, s2s_df)
        out = {
            "ok": default_path.exists() and default_path.stat().st_size > 0,
            "export_bytes": int(default_path.stat().st_size) if default_path.exists() else 0,
            "s2s_bytes": int(s2s_path.stat().st_size) if s2s_path.exists() else 0,
            "rows": int(len(default_df)),
            "elapsed_s": round(time.perf_counter() - t0, 2),
        }
        logger.info(
            "[snapshot] wrote prebuilt exports rows=%s bytes=%s s2s=%s in %ss",
            out.get("rows"),
            out.get("export_bytes"),
            out.get("s2s_bytes"),
            out.get("elapsed_s"),
        )
        return out
    except Exception as exc:
        logger.warning("[snapshot] prebuilt export write failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def _ensure_prebuilt_exports(*, force: bool = False) -> Path | None:
    """Ensure default export CSV exists (build from snapshot if missing)."""
    default_path, _s2s = _export_disk_paths()
    if (
        not force
        and default_path.exists()
        and default_path.stat().st_size > 50_000
    ):
        return default_path
    df = _snapshot_df()
    if df is None or df.empty:
        return default_path if default_path.exists() else None
    _write_prebuilt_exports_from_snapshot(df)
    return default_path if default_path.exists() and default_path.stat().st_size > 0 else None


def _pull_blob_cache_file(store_b, blob_name: str, dest: Path, *, min_bytes: int = 1) -> bool:
    try:
        local = store_b._ensure_local_file(blob_name)
        if local is None or not local.exists() or local.stat().st_size < min_bytes:
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        if local.resolve() != dest.resolve():
            dest.write_bytes(local.read_bytes())
        return dest.exists() and dest.stat().st_size >= min_bytes
    except Exception as exc:
        logger.debug("[snapshot] pull %s failed: %s", blob_name, exc)
        return False


def publish_replenishment_snapshot_to_blob() -> dict:
    """Upload shaped ~3MB parquet + prebuilt export CSVs so hosted download is instant."""
    pq, key = _disk_cache_paths()
    if not pq.exists() or pq.stat().st_size <= 0:
        return {"ok": False, "error": f"missing {pq.name}"}
    try:
        from api.blob_store import upload_local_file_with_retry
        from api.routes.sales_dashboard import _blob_store

        store_b = _blob_store()
        if store_b is None:
            return {"ok": False, "error": "blob store unavailable"}
        upload_local_file_with_retry(
            store_b.container,
            blob_name=_BLOB_SNAPSHOT_NAME,
            path=pq,
            content_type="application/octet-stream",
            attempts=4,
            timeout_s=300,
        )
        if key.exists():
            upload_local_file_with_retry(
                store_b.container,
                blob_name=_BLOB_SNAPSHOT_KEY,
                path=key,
                content_type="text/plain",
                attempts=2,
                timeout_s=60,
            )
        export_path, s2s_path = _export_disk_paths()
        # Build exports if snapshot parquet exists but CSV was never materialized.
        if not export_path.exists() or export_path.stat().st_size < 50_000:
            try:
                _write_prebuilt_exports_from_snapshot(pd.read_parquet(pq))
            except Exception as build_exc:
                logger.warning("[snapshot] export build before publish failed: %s", build_exc)
        published_exports: dict[str, int] = {}
        for local_path, blob_name in (
            (export_path, _BLOB_EXPORT_CSV),
            (s2s_path, _BLOB_EXPORT_S2S_CSV),
        ):
            if local_path.exists() and local_path.stat().st_size > 0:
                upload_local_file_with_retry(
                    store_b.container,
                    blob_name=blob_name,
                    path=local_path,
                    content_type="text/csv; charset=utf-8",
                    attempts=3,
                    timeout_s=300,
                )
                published_exports[blob_name] = int(local_path.stat().st_size)
        return {
            "ok": True,
            "blob": _BLOB_SNAPSHOT_NAME,
            "bytes": pq.stat().st_size,
            "exports": published_exports,
        }
    except Exception as exc:
        logger.warning("Replenishment snapshot blob publish failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def _pull_replenishment_snapshot_from_blob() -> bool:
    """Hosted cold start: pull prepared parquet (+ export CSVs) before the refill CSV."""
    pq, key = _disk_cache_paths()
    try:
        from api.routes.sales_dashboard import _blob_store

        store_b = _blob_store()
        if store_b is None:
            return False
        if not _pull_blob_cache_file(store_b, _BLOB_SNAPSHOT_NAME, pq, min_bytes=10_000):
            return False
        _pull_blob_cache_file(store_b, _BLOB_SNAPSHOT_KEY, key, min_bytes=1)
        if not key.exists() or key.stat().st_size <= 0:
            key.write_text("blob", encoding="utf-8")
        export_path, s2s_path = _export_disk_paths()
        _pull_blob_cache_file(store_b, _BLOB_EXPORT_CSV, export_path, min_bytes=50_000)
        _pull_blob_cache_file(store_b, _BLOB_EXPORT_S2S_CSV, s2s_path, min_bytes=1_000)
        logger.info(
            "[snapshot] pulled from blob %s (%s bytes) export=%s",
            _BLOB_SNAPSHOT_NAME,
            pq.stat().st_size,
            export_path.stat().st_size if export_path.exists() else 0,
        )
        return pq.exists() and pq.stat().st_size > 10_000
    except Exception as exc:
        logger.warning("[snapshot] blob pull failed: %s", exc)
        return False


def _local_blob_cache_sig() -> str | None:
    """mtime/size of local blob_cache refill file — no Azure round-trip."""
    try:
        from api.blob_store import _CACHE_DIR, _safe_name
    except Exception:
        return None
    for ext in (".csv", ".parquet", ".xlsx"):
        blob_name = f"output/{TABLE}{ext}"
        path = _CACHE_DIR / _safe_name(blob_name)
        if path.exists() and path.stat().st_size > 0:
            try:
                st = path.stat()
                return f"blobcache:{path.name}:{st.st_mtime_ns}:{st.st_size}"
            except OSError:
                continue
    return None


def _current_source_sig() -> str:
    """
    Signature of the replenishment file the page should show.

    Default (Azure configured): track local blob_cache mtime (same file as hosted).
    Azure etag only when REPLENISHMENTS_CHECK_BLOB_ETAG=1 (avoids request hang).
    REPLENISHMENTS_PREFER_LOCAL=1: track pipeline/output_files only.
    """
    parts = [f"v{_SNAPSHOT_LOGIC_VERSION}", f"table:{TABLE}"]
    check_azure = (os.environ.get("REPLENISHMENTS_CHECK_BLOB_ETAG") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if _prefer_blob_refill():
        # Prefer blob_cache signature — do not include pipeline local: so page tracks hosted file.
        local_blob = _local_blob_cache_sig()
        if local_blob:
            parts.append(local_blob)
        else:
            parts.append("blob:pending")
        if check_azure:
            blob_etag = _blob_refill_etag()
            if blob_etag:
                parts.append(f"blob:{blob_etag}")
    else:
        local = _newest_local_refill()
        if local is not None:
            try:
                st = local.stat()
                parts.append(f"local:{st.st_mtime_ns}:{st.st_size}")
            except OSError:
                pass
        local_blob = _local_blob_cache_sig()
        if local_blob:
            parts.append(local_blob)
        if check_azure:
            blob_etag = _blob_refill_etag()
            if blob_etag:
                parts.append(f"blob:{blob_etag}")
    if len(parts) <= 2:
        parts.append("missing")
    return "|".join(parts)


def _shape_snapshot_df(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    df = _dedupe_duplicate_columns(df)
    df = _apply_repl_column_aliases(df)
    df = _enrich_store_grade(df)
    return df


def _read_refill_file(path: Path, label: str) -> pd.DataFrame | None:
    try:
        t0 = time.perf_counter()
        suffix = path.suffix.lower()
        if suffix in {".xlsx", ".xls"}:
            df = pd.read_excel(path)
        elif suffix == ".parquet":
            df = pd.read_parquet(path)
        else:
            # Blob may keep .csv extension while bytes are xlsx
            head = b""
            try:
                with path.open("rb") as fh:
                    head = fh.read(8)
            except OSError:
                head = b""
            if head[:2] == b"PK":
                df = pd.read_excel(path)
            else:
                df = pd.read_csv(path, low_memory=False)
        df = _shape_snapshot_df(df)
        if df.empty:
            return None
        grade_filled = (
            int((df["store_grade"].astype(str).str.strip() != "").sum())
            if "store_grade" in df.columns
            else 0
        )
        logger.info(
            "[snapshot] %s refill %s → %d rows (store_grade filled %s) in %.1fs",
            label,
            path.name,
            len(df),
            grade_filled,
            time.perf_counter() - t0,
        )
        return df
    except Exception as exc:
        logger.warning("[snapshot] %s refill read failed (%s): %s", label, path, exc)
        return None


def _load_snapshot_from_blob_table() -> pd.DataFrame:
    rows: list = []
    last_delta_err = None
    for _ in range(2):
        try:
            rows = store.read_delta_table(TABLE)
            if rows:
                break
        except Exception as e:
            last_delta_err = e
            time.sleep(0.5)
    if last_delta_err and not rows:
        logger.warning("delta read failed for %s (falling back to snapshot): %s", TABLE, last_delta_err)
    if not rows:
        try:
            rows = store.read_table(f"{TABLE}_snapshot")
        except Exception:
            rows = []
    if not rows:
        return pd.DataFrame()
    df = _shape_snapshot_df(pd.DataFrame(rows))
    grade_filled = (
        int((df["store_grade"].astype(str).str.strip() != "").sum())
        if "store_grade" in df.columns
        else 0
    )
    logger.info(
        "[snapshot] blob/table %s → %d rows (store_grade filled %s / %s)",
        TABLE,
        len(df),
        grade_filled,
        len(df),
    )
    return df


def _load_snapshot_from_source() -> pd.DataFrame:
    """Read replenishment recommendation — prefer Azure blob (hosted file), else local pipeline."""
    if _prefer_blob_refill():
        blob_path = _ensure_blob_refill_path()
        if blob_path is not None:
            df = _read_refill_file(blob_path, "blob")
            if df is not None:
                return df
        df = _load_snapshot_from_blob_table()
        if not df.empty:
            return df
        logger.warning("[snapshot] blob prefer failed — falling back to local pipeline refill")

    local = _newest_local_refill()
    if local is not None:
        df = _read_refill_file(local, "local")
        if df is not None:
            return df

    if not _prefer_blob_refill():
        return _load_snapshot_from_blob_table()
    return pd.DataFrame()


def _write_disk_snapshot(df: pd.DataFrame, sig: str) -> None:
    if df is None or df.empty:
        return
    try:
        _API_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        pq, key = _disk_cache_paths()
        df.to_parquet(pq, index=False)
        key.write_text(sig, encoding="utf-8")
        logger.info(
            "[snapshot] wrote disk cache %s (%d rows, sig=%s)",
            pq.name,
            len(df),
            sig[:80],
        )
        _write_prebuilt_exports_from_snapshot(df)
        pub = publish_replenishment_snapshot_to_blob()
        if not pub.get("ok"):
            logger.warning("[snapshot] disk saved but blob publish skipped: %s", pub.get("error"))
    except Exception as exc:
        logger.warning("[snapshot] disk cache write failed: %s", exc)


def _read_disk_snapshot(expected_sig: str) -> pd.DataFrame | None:
    pq, key = _disk_cache_paths()
    prefer_blob = _prefer_blob_refill()
    if not pq.exists() or pq.stat().st_size <= 0:
        if not _pull_replenishment_snapshot_from_blob():
            return None
    try:
        saved = key.read_text(encoding="utf-8").strip() if key.exists() else ""
        # Local was showing pipeline CSV while hosted uses blob — switch cache to blob snapshot.
        if prefer_blob and saved and "local:" in saved:
            logger.info("[snapshot] discarding local-pipeline disk cache — pulling blob snapshot")
            if _pull_replenishment_snapshot_from_blob():
                try:
                    key.write_text(expected_sig, encoding="utf-8")
                except Exception:
                    pass
                df = pd.read_parquet(pq)
                if df is not None and not df.empty:
                    logger.info("[snapshot] blob prepared cache hit (%d rows)", len(df))
                    return df
            return None
        if saved and saved != expected_sig:
            # Serve existing parquet when only sig recipe changed (blob etag → local blobcache)
            # or logic bumped with same underlying refill file.
            # Hosted blob pulls often have "blob" / older keys — still serve if parquet is healthy.
            def _core(s: str) -> str:
                return "|".join(
                    p
                    for p in s.split("|")
                    if not p.startswith("blob:")
                    and not p.startswith("v")
                    and p != "missing"
                    and p != "blob:pending"
                )

            same_source = _core(saved) == _core(expected_sig) and _core(expected_sig)
            near_ver = (
                saved in {"", "blob"}
                or saved.startswith("v34|")
                or saved.startswith("v35|")
                or saved.startswith(f"v{_SNAPSHOT_LOGIC_VERSION}|")
            )
            if not (same_source or (near_ver and ("table:" in saved or saved in {"", "blob"}))):
                # Prefer fresh blob prepared parquet over a stale multi-MB local rebuild.
                if prefer_blob and _pull_replenishment_snapshot_from_blob():
                    try:
                        key.write_text(expected_sig, encoding="utf-8")
                    except Exception:
                        pass
                    df = pd.read_parquet(pq)
                    if df is not None and not df.empty:
                        logger.info("[snapshot] blob prepared cache replaced disk (%d rows)", len(df))
                        return df
                if pq.stat().st_size < 100_000:
                    return None
            try:
                key.write_text(expected_sig, encoding="utf-8")
            except Exception:
                pass
        df = pd.read_parquet(pq)
        if df is None or df.empty:
            return None
        logger.info("[snapshot] disk cache hit (%d rows)", len(df))
        return df
    except Exception as exc:
        logger.warning("[snapshot] disk cache read failed: %s", exc)
        return None


def _snapshot_df(*, force_refresh: bool = False) -> pd.DataFrame:
    """
    Shaped replenishment recommendation frame for the page.

    Durable cache: data/api_cache/replenishments_recom_snapshot_v1.parquet keyed by
    local file mtime/size and/or blob etag. Rebuilds only when the source file changes
    (or force_refresh=True).
    """
    global _SNAPSHOT_CACHE_DF, _SNAPSHOT_CACHE_AT, _SNAPSHOT_CACHE_LOGIC_VER, _SNAPSHOT_CACHE_SIG
    now = time.time()
    sig = _current_source_sig()

    with _SNAPSHOT_LOCK:
        if (
            not force_refresh
            and _SNAPSHOT_CACHE_DF is not None
            and not _SNAPSHOT_CACHE_DF.empty
            and _SNAPSHOT_CACHE_LOGIC_VER == _SNAPSHOT_LOGIC_VERSION
            and _SNAPSHOT_CACHE_SIG == sig
            and (now - _SNAPSHOT_CACHE_AT) < _SNAPSHOT_CACHE_TTL_SECONDS
        ):
            return _SNAPSHOT_CACHE_DF

    if not force_refresh:
        disk_df = _read_disk_snapshot(sig)
        if disk_df is not None:
            with _SNAPSHOT_LOCK:
                _SNAPSHOT_CACHE_DF = disk_df
                _SNAPSHOT_CACHE_AT = now
                _SNAPSHOT_CACHE_LOGIC_VER = _SNAPSHOT_LOGIC_VERSION
                _SNAPSHOT_CACHE_SIG = sig
            return disk_df

    t0 = time.perf_counter()
    df = _load_snapshot_from_source()
    if df.empty:
        # Keep previous good RAM/disk if rebuild failed transiently.
        with _SNAPSHOT_LOCK:
            if _SNAPSHOT_CACHE_DF is not None and not _SNAPSHOT_CACHE_DF.empty:
                return _SNAPSHOT_CACHE_DF
        disk_fallback = None
        pq, _key = _disk_cache_paths()
        if pq.exists():
            try:
                disk_fallback = pd.read_parquet(pq)
            except Exception:
                disk_fallback = None
        if disk_fallback is not None and not disk_fallback.empty:
            logger.warning("[snapshot] source empty — serving previous disk cache")
            return disk_fallback
        return pd.DataFrame()

    _write_disk_snapshot(df, sig)
    with _SNAPSHOT_LOCK:
        _SNAPSHOT_CACHE_DF = df
        _SNAPSHOT_CACHE_AT = time.time()
        _SNAPSHOT_CACHE_LOGIC_VER = _SNAPSHOT_LOGIC_VERSION
        _SNAPSHOT_CACHE_SIG = sig
    logger.info(
        "[snapshot] cache ready rows=%d in %.1fs (force=%s)",
        len(df),
        time.perf_counter() - t0,
        force_refresh,
    )
    return df


@router.post("/warm-cache")
@router.get("/warm-cache")
def warm_replenishments_http(force_refresh: bool = False):
    """Demo / ops: ensure replenishment snapshot is in RAM + disk."""
    return warm_replenishments_recom_cache(force_refresh=force_refresh)


def warm_replenishments_recom_cache(*, force_refresh: bool = False) -> dict:
    """
    Build / refresh the replenishment page disk cache from the current refill file.
    Call after pipeline publishes a new recommendation CSV.
    Also materializes prebuilt export CSVs for fast hosted download.
    """
    t0 = time.perf_counter()
    if force_refresh:
        clear_replenishments_snapshot_cache()
    df = _snapshot_df(force_refresh=force_refresh)
    export_info = _write_prebuilt_exports_from_snapshot(df) if df is not None and not df.empty else {
        "ok": False,
        "error": "empty",
    }
    pub = publish_replenishment_snapshot_to_blob()
    return {
        "ok": bool(df is not None and not df.empty),
        "rows": 0 if df is None else int(len(df)),
        "sig": _SNAPSHOT_CACHE_SIG,
        "elapsed_s": round(time.perf_counter() - t0, 2),
        "disk": str(_disk_cache_paths()[0]),
        "export": export_info,
        "published": pub,
    }


def _dedupe_duplicate_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Delta/JSON exports sometimes repeat the same column name → double-counted SUMs."""
    if df.empty or df.columns.size == 0:
        return df
    dup = pd.Index(df.columns).duplicated(keep="first")
    if dup.any():
        n = int(dup.sum())
        logger.warning("%s: dropping %s duplicate column name(s) from snapshot", TABLE, n)
        df = df.loc[:, ~dup].copy()
    return df


def _effective_site_series(df: pd.DataFrame) -> pd.Series:
    """Per-row display key matching SQL TRIM(COALESCE(Site, Site_ID))."""
    idx = df.index
    if "Site" in df.columns and "Site_ID" in df.columns:
        s = df["Site"].where(df["Site"].notna(), "").astype(str).str.strip()
        s = s.replace({"nan": "", "None": ""}, regex=False)
        sid = df["Site_ID"].where(df["Site_ID"].notna(), "").astype(str).str.strip()
        sid = sid.replace({"nan": "", "None": ""}, regex=False)
        return s.mask(s == "", sid).str.strip()
    if "Site" in df.columns:
        return df["Site"].where(df["Site"].notna(), "").astype(str).str.strip()
    if "Site_ID" in df.columns:
        return df["Site_ID"].where(df["Site_ID"].notna(), "").astype(str).str.strip()
    return pd.Series([""] * len(df), index=idx)


# ─── Filters (optional ?site=&site_id=&sku=&month=&week= narrow sibling dropdowns) ───
def _repl_q_site(site: str | None) -> str:
    s = (site or "").strip()
    return s if s and s not in ("All Sites", "All") else "All Sites"


def _repl_q_site_id(sid: str | None) -> str | None:
    s = (sid or "").strip()
    if not s or s.upper() in ("ALL SITE IDS", "ALL", ""):
        return None
    return s


def _repl_q_sku(sku: str | None) -> str:
    s = (sku or "").strip()
    return s if s and s not in ("All SKUs", "All") else "All SKUs"


def _repl_q_month(m: str | None) -> str:
    s = (m or "").strip()
    return s if s and s not in ("All Months", "All") else "All Months"


def _repl_q_week(w: str | None) -> str:
    s = (w or "").strip()
    return s if s and s not in ("All Weeks", "All", "") else "All Weeks"


def _distinct_sku_filter_tokens(work: pd.DataFrame) -> list[str]:
    if work.empty or "SKU" not in work.columns:
        return []
    seen: set[str] = set()
    norm_vals: list[str] = []
    for v in work["SKU"].dropna().tolist():
        nk = _normalize_filter_sku_key(v)
        if not nk or nk in seen:
            continue
        seen.add(nk)
        norm_vals.append(nk)
    if "Size" in work.columns:
        sz = work["Size"].astype(str).str.strip()
        style_m = sz.str.match(_STYLE_IN_SIZE_RE, na=False)
        sku_empty = work["SKU"].map(_normalize_filter_sku_key).eq("")
        for v in work.loc[style_m & sku_empty, "Size"].tolist():
            nk = _normalize_filter_sku_key(v)
            if not nk or nk in seen:
                continue
            seen.add(nk)
            norm_vals.append(nk)
    return sorted(norm_vals)


@router.get("/filters/months")
async def get_filter_months(
    site: str | None = Query(None),
    site_id: str | None = Query(None),
    sku: str | None = Query(None),
    week: str | None = Query(None),
    refill_source: str | None = Query(None),
):
    try:
        df = _snapshot_df()
        work = _apply_summary_filters(
            df,
            _repl_q_site(site),
            _repl_q_sku(sku),
            "All Months",
            _repl_q_week(week),
            _repl_q_site_id(site_id),
        )
        work = _apply_refill_source_slice_for_filter_lists(work, refill_source)
        if work.empty or "Month" not in work.columns:
            return [{"id": "All Months", "label": "All Months"}]
        month_map = {1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun", 7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec"}
        vals = sorted({int(v) for v in work["Month"].dropna().tolist() if str(v).strip().isdigit()})
        return [{"id": "All Months", "label": "All Months"}] + [{"id": str(v), "label": month_map.get(v, str(v))} for v in vals]
    except Exception as e:
        logger.error(f"filters/months error: {e}")
        return [{"id": "All Months", "label": "All Months"}]


@router.get("/filters/weeks")
async def get_filter_weeks(
    site: str | None = Query(None),
    site_id: str | None = Query(None),
    sku: str | None = Query(None),
    month: str | None = Query(None),
    refill_source: str | None = Query(None),
):
    try:
        df = _snapshot_df()
        work = _apply_summary_filters(
            df,
            _repl_q_site(site),
            _repl_q_sku(sku),
            _repl_q_month(month),
            "All Weeks",
            _repl_q_site_id(site_id),
        )
        work = _apply_refill_source_slice_for_filter_lists(work, refill_source)
        if work.empty or "Week" not in work.columns:
            return [{"id": "All Weeks", "label": "All Weeks"}]
        wk = work.dropna(subset=["Week"])
        if wk.empty:
            return [{"id": "All Weeks", "label": "All Weeks"}]
        wrr = wk["Week_Date_Range"] if "Week_Date_Range" in wk.columns else pd.Series([None] * len(wk), index=wk.index)
        records = []
        seen = set()
        for w, wr in zip(wk["Week"], wrr):
            key = str(w)
            if key in seen:
                continue
            seen.add(key)
            label = key if key.startswith("W") else f"W{key}"
            if wr and str(wr).strip() not in ("", "nan", "None"):
                label = f"{label} ({wr})"
            records.append({"id": key, "label": label})
        return [{"id": "All Weeks", "label": "All Weeks"}] + records
    except Exception as e:
        logger.error(f"filters/weeks error: {e}")
        return [{"id": "All Weeks", "label": "All Weeks"}]


@router.get("/filters/sites")
async def get_filter_sites(
    site_id: str | None = Query(None),
    sku: str | None = Query(None),
    month: str | None = Query(None),
    week: str | None = Query(None),
    refill_source: str | None = Query(None),
):
    try:
        df = _snapshot_df()
        work = _apply_summary_filters(
            df,
            "All Sites",
            _repl_q_sku(sku),
            _repl_q_month(month),
            _repl_q_week(week),
            _repl_q_site_id(site_id),
        )
        work = _apply_refill_source_slice_for_filter_lists(work, refill_source)
        if work.empty:
            return [{"id": "All Sites", "label": "All Sites"}]
        eff = _effective_site_series(work)
        vals = sorted({str(v) for v in eff.tolist() if str(v).strip() and str(v).lower() not in ("nan", "none")})
        return [{"id": "All Sites", "label": "All Sites"}] + [{"id": v, "label": v} for v in vals]
    except Exception as e:
        logger.error(f"filters/sites error: {e}")
        return [{"id": "All Sites", "label": "All Sites"}]


@router.get("/filters/site-ids")
async def get_filter_site_ids(
    site: str | None = Query(None),
    sku: str | None = Query(None),
    month: str | None = Query(None),
    week: str | None = Query(None),
    refill_source: str | None = Query(None),
):
    """Distinct `Site_ID` / site_code values from the replen recommendation table for table filter."""
    try:
        df = _snapshot_df()
        work = _apply_summary_filters(
            df,
            _repl_q_site(site),
            _repl_q_sku(sku),
            _repl_q_month(month),
            _repl_q_week(week),
            None,
        )
        work = _apply_refill_source_slice_for_filter_lists(work, refill_source)
        if work.empty or "Site_ID" not in work.columns:
            return [{"id": "All Site IDs", "label": "All Site IDs"}]
        vals = sorted(
            {
                str(v).strip()
                for v in work["Site_ID"].dropna().tolist()
                if str(v).strip() and str(v).strip().lower() not in ("nan", "none")
            }
        )
        return [{"id": "All Site IDs", "label": "All Site IDs"}] + [{"id": v, "label": v} for v in vals]
    except Exception as e:
        logger.error(f"filters/site-ids error: {e}")
        return [{"id": "All Site IDs", "label": "All Site IDs"}]


@router.get("/filters/skus")
async def get_filter_skus(
    site: str | None = Query(None),
    site_id: str | None = Query(None),
    month: str | None = Query(None),
    week: str | None = Query(None),
    refill_source: str | None = Query(None),
):
    try:
        df = _snapshot_df()
        work = _apply_summary_filters(
            df,
            _repl_q_site(site),
            "All SKUs",
            _repl_q_month(month),
            _repl_q_week(week),
            _repl_q_site_id(site_id),
        )
        work = _apply_refill_source_slice_for_filter_lists(work, refill_source)
        if work.empty or "SKU" not in work.columns:
            return [{"id": "All SKUs", "label": "All SKUs"}]
        vals = _distinct_sku_filter_tokens(work)
        return [{"id": "All SKUs", "label": "All SKUs"}] + [{"id": v, "label": v} for v in vals]
    except Exception as e:
        logger.error(f"filters/skus error: {e}")
        return [{"id": "All SKUs", "label": "All SKUs"}]

def _empty_summary_payload(status: str) -> dict:
    return {
        "skus_needing_refill": 0,
        "total_refill_qty": 0,
        "skus_out_of_stock": 0,
        "high_priority_skus": 0,
        "forecasted_refill_qty": 0,
        "forecasted_refill": 0,
        "predicted_qty_sum": 0,
        "predicted_qty_source_column": None,
        "forecasted_refill_qty_components": {
            "covered_by_store_soh": 0,
            "covered_by_wh_soh": 0,
            "unfilled_qty": 0,
        },
        "predicted_qty_filtered_row_count": 0,
        "total_store_soh": 0,
        "wh_fillable_qty": 0,
        "store_soh_fillable_qty": 0,
        "unfilled_after_store_wh_qty": 0,
        "unfilled_after_store_wh_count": 0,
        "site_to_site_unfilled_qty": 0,
        "unfilled_gap_qty": 0,
        "coverage_pct": 0.0,
        "critical_refill_skus": 0,
        "status": status,
    }


def _apply_summary_filters(
    work: pd.DataFrame,
    site: str,
    sku: str,
    month: str,
    week: str,
    site_id: str | None = None,
) -> pd.DataFrame:
    out = work.copy()
    if site and site.strip() not in ("All Sites", "All", ""):
        eff = _effective_site_series(out).str.upper()
        out = out[eff == site.strip().upper()]
    sid_arg = (site_id or "").strip()
    if sid_arg and sid_arg.upper() not in ("ALL SITE IDS", "ALL", "") and "Site_ID" in out.columns:
        sid_series = out["Site_ID"].astype(str).str.strip().str.upper()
        out = out[sid_series == sid_arg.upper()]
    if sku and sku.strip() not in ("All SKUs", "All", "") and "SKU" in out.columns:
        want = _normalize_filter_sku_key(sku.strip())
        if want:
            sku_hit = out["SKU"].map(_normalize_filter_sku_key) == want
            if "Size" in out.columns:
                sz = out["Size"].astype(str).str.strip()
                style_m = sz.str.match(_STYLE_IN_SIZE_RE, na=False)
                sz_hit = style_m & (sz.map(_normalize_filter_sku_key) == want)
                out = out[sku_hit | sz_hit]
            else:
                out = out[sku_hit]
    if month and month.strip() not in ("All Months", "All", "") and "Month" in out.columns:
        m = month.strip().upper()
        col = out["Month"]
        month_match = col.astype(str).str.strip().str.upper() == m
        if not month_match.any() and m.isdigit():
            month_match = col.astype(str).str.strip() == month.strip()
        out = out[month_match]
    if week and week.strip() not in ("All Weeks", "All", "") and "Week" in out.columns:
        want_w = _normalize_filter_week_key(week.strip())
        if want_w:
            week_s = out["Week"].map(_normalize_filter_week_key)
            out = out[week_s == want_w]
    return out


def _apply_refill_source_slice_for_filter_lists(
    ordered: pd.DataFrame,
    refill_source: str | None,
) -> pd.DataFrame:
    """
    When UI passes ?refill_source= (e.g. site→site tab), narrow distinct /filters/* lists to that slice.
    When param omitted or All, return `ordered` unchanged (filters stay full snapshot for sibling narrowing).
    """
    rs_raw = (refill_source or "").strip()
    if not rs_raw or rs_raw.upper() in ("ALL",):
        return ordered
    out = ordered
    if "refill_source" in out.columns:
        rs = rs_raw.upper()
        out = out[out["refill_source"].astype(str).str.strip().str.upper() == rs]
    return out


def _forecasted_refill_qty_component_series(work: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Store / WH fillable + unfilled from replen pipeline columns (non-negative)."""
    cs = _num_series(
        work,
        "covered_by_store_soh",
        "Covered_By_Store_Soh",
        "Covered_By_Store_SOH",
        clip_lower=True,
    )
    cw = _num_series(
        work,
        "covered_by_wh_soh",
        "Covered_By_Wh_Soh",
        "Covered_By_WH_Soh",
        "Covered_By_Wh_SOH",
        clip_lower=True,
    )
    uf = _num_series(work, "unfilled_qty", "Unfilled_Qty", "unfilled", "Unfilled", clip_lower=True)
    return cs, cw, uf


def _forecasted_qty_display_series(df: pd.DataFrame) -> pd.Series:
    """Per-row Forecasted Refill = covered_by_store_soh + covered_by_wh_soh + unfilled_qty (same as KPI sum)."""
    cs, cw, uf = _forecasted_refill_qty_component_series(df)
    return (cs + cw + uf).fillna(0)


def _num_series(work: pd.DataFrame, *col_names: str, clip_lower: bool = False) -> pd.Series:
    """First existing column among `col_names` as float Series aligned to `work.index`."""
    idx = work.index
    for name in col_names:
        if name in work.columns:
            raw = work[name]
            if isinstance(raw, pd.DataFrame):
                raw = raw.iloc[:, 0]
            s = pd.to_numeric(raw, errors="coerce").fillna(0)
            if clip_lower:
                s = s.clip(lower=0)
            return s
    s = pd.Series(0.0, index=idx, dtype=float)
    return s.clip(lower=0) if clip_lower else s


def _summary_metrics_from_work(work: pd.DataFrame) -> dict:
    rr = _num_series(work, "recommended_refill", clip_lower=True)
    fr = _num_series(work, "forecasted_refill", clip_lower=True)
    wh = _num_series(work, "WH_Soh", "wh_soh", clip_lower=True)
    soh = _num_series(work, "Store_Soh", "store_soh", clip_lower=True)
    sales = _num_series(work, "Sum_of_Sales", "total_sales")
    sku_series = work["SKU"].astype(str) if "SKU" in work.columns else pd.Series("", index=work.index, dtype=str)

    # KPI: Store / WH fillable + unfilled from lake; Forecasted Refill Qty = sum of those three totals.
    covered_store, covered_wh, uf = _forecasted_refill_qty_component_series(work)
    store_fill_sum = int(float(covered_store.sum()))
    wh_fill_sum = int(float(covered_wh.sum()))
    site_transfer_sum = int(float(uf.sum()))
    forecasted_refill_qty_val = store_fill_sum + wh_fill_sum + site_transfer_sum

    # Legacy derived metrics (charts / other cards) — keep behaviour close to before.
    store_fillable_forecast = pd.concat([fr, soh], axis=1).min(axis=1).fillna(0)
    store_fillable = pd.concat([rr, soh], axis=1).min(axis=1).fillna(0)
    remaining_after_store = (rr - store_fillable).clip(lower=0)
    wh_fillable_after_store = pd.concat([remaining_after_store, wh], axis=1).min(axis=1).fillna(0)
    unfilled_after_store_wh = (remaining_after_store - wh_fillable_after_store).clip(lower=0)
    wh_fillable = pd.concat([fr, wh], axis=1).min(axis=1).fillna(0)
    unfilled_gap = (fr - wh_fillable).clip(lower=0)
    coverage_pct = float((wh_fillable.sum() / fr.sum()) * 100) if float(fr.sum()) > 0 else 0.0
    critical_mask = (rr > 1) & (soh == 0) & (wh == 0)

    return {
        "skus_needing_refill": int(sku_series[rr > 0].nunique()),
        "total_refill_qty": int(rr.sum()),
        "skus_out_of_stock": int(sku_series[wh == 0].nunique()),
        "high_priority_skus": int(sku_series[(sales > 0) & (wh == 0)].nunique()),
        "forecasted_refill_qty": forecasted_refill_qty_val,
        "forecasted_refill": forecasted_refill_qty_val,
        "predicted_qty_sum": forecasted_refill_qty_val,
        "predicted_qty_source_column": None,
        "forecasted_refill_qty_components": {
            "covered_by_store_soh": store_fill_sum,
            "covered_by_wh_soh": wh_fill_sum,
            "unfilled_qty": site_transfer_sum,
        },
        "predicted_qty_filtered_row_count": int(len(work)),
        "total_store_soh": int(soh.sum()),
        "wh_fillable_qty": wh_fill_sum,
        "store_soh_fillable_qty": store_fill_sum,
        "unfilled_after_store_wh_qty": int(unfilled_after_store_wh.sum()),
        # UI: "Site to site transfer (unfilled qty)" — SUM(unfilled_qty) over the same filtered rows.
        "unfilled_after_store_wh_count": site_transfer_sum,
        "site_to_site_unfilled_qty": site_transfer_sum,
        "unfilled_gap_qty": int(unfilled_gap.sum()),
        "coverage_pct": round(coverage_pct, 2),
        "critical_refill_skus": int(sku_series[critical_mask].nunique()),
        "status": "success",
    }


def _apply_recommendation_row_filters(
    df: pd.DataFrame,
    site: str | None,
    sku: str | None,
    month: str | None,
    week: str | None,
    site_id: str | None,
    refill_source: str | None,
    search: str | None,
    exclude_site_transfer_zero_fill: bool = False,
) -> pd.DataFrame:
    ordered = _apply_summary_filters(df, site, sku, month, week, site_id)
    if refill_source and str(refill_source).strip() not in ("", "All", "ALL"):
        if "refill_source" in ordered.columns:
            rs = str(refill_source).strip().upper()
            ordered = ordered[
                ordered["refill_source"].astype(str).str.strip().str.upper() == rs
            ]

    # --- Tab routing ---
    # WH/Store (first tab): no row split — show the full replen table slice for the chosen
    # site / sku / month / week / search (same as above filters only).
    # Site-to-Site: only rows with unfilled_qty > 0, after lake `refill_source` match when applicable.
    rs_val = str(refill_source or "").strip().upper()
    if rs_val == SITE_TO_SITE_REFILL_SOURCE.upper():
        _, _, uf_series = _forecasted_refill_qty_component_series(ordered)
        ordered = ordered[uf_series > 0]
    if search and str(search).strip():
        qn = str(search).strip().lower()
        site_a = (
            ordered["Site"].astype(str)
            if "Site" in ordered.columns
            else pd.Series("", index=ordered.index)
        )
        site_b = (
            ordered["Site_ID"].astype(str)
            if "Site_ID" in ordered.columns
            else pd.Series("", index=ordered.index)
        )
        site_hay = (site_a + " " + site_b).str.lower()
        sku_hay = (
            ordered["SKU"].astype(str).str.lower()
            if "SKU" in ordered.columns
            else pd.Series("", index=ordered.index)
        )
        bc_hay = (
            ordered["Barcode"].map(_barcode_to_str).astype(str).str.lower()
            if "Barcode" in ordered.columns
            else pd.Series("", index=ordered.index)
        )
        stock_hay = (
            ordered["dead_stock_status"].astype(str).str.lower()
            if "dead_stock_status" in ordered.columns
            else pd.Series("", index=ordered.index)
        )
        qd = qn.replace(" ", "")
        m = (
            site_hay.str.contains(qn, regex=False, na=False)
            | sku_hay.str.contains(qn, regex=False, na=False)
            | bc_hay.str.contains(qd, regex=False, na=False)
            | stock_hay.str.contains(qn, regex=False, na=False)
        )
        ordered = ordered[m]
    return ordered


_RECOMMENDATIONS_SORT_COL_MAP = {
    "Refill": "recommended_refill",
    "refill": "recommended_refill",
    "Recommended_Refill": "recommended_refill",
    "recommended_refill": "recommended_refill",
    "WH_Soh": "WH_Soh",
    "wh_soh": "WH_Soh",
    "wh_soh_end_barcode": "wh_soh_end_barcode",
    "warehouse_soh_after_allocation": "wh_soh_end_barcode",
    "Rank": "Rank",
    "rank": "Rank",
    "Sum_of_Sales": "Sum_of_Sales",
    "total_sales": "Sum_of_Sales",
    "Store_Soh": "Store_Soh",
    "last_week_store_soh": "last_week_store_soh",
    "Last_Week_Store_SOH": "last_week_store_soh",
    "last_week_soh": "last_week_store_soh",
    "Fill_Percent": "Fill_Percent",
    "Site": "Site",
    "site": "Site",
    "Site_ID": "Site_ID",
    "site_id": "Site_ID",
    "store_grade": "store_grade",
    "grade": "store_grade",
    "SKU": "SKU",
    "sku": "SKU",
    "Month": "Month",
    "month": "Month",
    "Size": "Size",
    "size": "Size",
    "Season": "Season",
    "season": "Season",
    "Week": "Week",
    "week": "Week",
    "Barcode": "Barcode",
    "barcode": "Barcode",
    "forecasted_refill": "_fc_disp",
    "predicted_qty": "_fc_disp",
    "Recommendation_Comment": "Recommendation_Comment",
    "refill_note": "refill_note",
    "sold_qty_prev_5w": "sold_qty_prev_5w",
    "sold_qty_prev_35d": "sold_qty_prev_35d",
    "lag1_sold_qty": "lag1_sold_qty",
    "covered_by_store_soh": "covered_by_store_soh",
    "covered_by_wh_soh": "covered_by_wh_soh",
    "total_fulfilled_qty": "total_fulfilled_qty",
    "unfilled_qty": "unfilled_qty",
    "refill_source": "refill_source",
    "last_selling_date": "last_selling_date",
    "days_since_last_sale": "days_since_last_sale",
    "dead_stock_status": "dead_stock_status",
    "priority_score": "priority_score",
    "priority_score_raw": "priority_score_raw",
    "total_sold_qty": "total_sold_qty",
    "priority_expl_timing_bonus": "priority_expl_timing_bonus",
}


def _df_col_series(df: pd.DataFrame, *names: str) -> pd.Series:
    for name in names:
        if name in df.columns:
            raw = df[name]
            if isinstance(raw, pd.DataFrame):
                return raw.iloc[:, 0]
            return raw
    return pd.Series([None] * len(df), index=df.index)


def _sort_recommendations_df(
    ordered: pd.DataFrame,
    *,
    sort_by: str,
    sort_order: str,
) -> pd.DataFrame:
    if ordered.empty:
        return ordered
    sort_col = _RECOMMENDATIONS_SORT_COL_MAP.get(sort_by, "_fc_disp")
    if sort_col not in ordered.columns:
        sort_col = (
            "recommended_refill"
            if "recommended_refill" in ordered.columns
            else ordered.columns[0]
        )
    asc = sort_order.upper() != "DESC"
    try:
        if sort_by == "last_selling_date" and "last_selling_date" in ordered.columns:
            ordered = ordered.assign(
                _sort_lsd=pd.to_datetime(ordered["last_selling_date"], errors="coerce")
            )
            ordered = ordered.sort_values("_sort_lsd", ascending=asc, na_position="last")
            return ordered.drop(columns=["_sort_lsd"], errors="ignore")
        return ordered.sort_values(sort_col, ascending=asc)
    except Exception:
        return ordered


def _recommendations_work_df(
    *,
    site: str | None,
    sku: str | None,
    month: str | None,
    week: str | None,
    site_id: str | None,
    refill_source: str | None,
    search: str | None,
    sort_by: str,
    sort_order: str,
    full_table: bool = False,
) -> pd.DataFrame:
    df = _snapshot_df()
    if df.empty:
        return df
    if full_table:
        exclude_site_queue_zero_fill = False
    else:
        rs_arg = (refill_source or "").strip()
        exclude_site_queue_zero_fill = rs_arg.upper() != SITE_TO_SITE_REFILL_SOURCE.upper()
    ordered = _apply_recommendation_row_filters(
        df,
        site,
        sku,
        month,
        week,
        site_id,
        refill_source,
        search,
        exclude_site_transfer_zero_fill=exclude_site_queue_zero_fill,
    )
    ordered = ordered.assign(_fc_disp=_forecasted_qty_display_series(ordered))
    return _sort_recommendations_df(ordered, sort_by=sort_by, sort_order=sort_order)


def _export_recommendations_df(ordered: pd.DataFrame, *, is_site_to_site: bool) -> pd.DataFrame:
    """Vectorized export columns (same layout as legacy Excel export)."""
    if ordered.empty:
        return pd.DataFrame()
    cs, cw, uf = _forecasted_refill_qty_component_series(ordered)
    fc = _forecasted_qty_display_series(ordered)
    st = _df_col_series(ordered, "store_type").astype(str).str.strip()
    st = st.replace({"nan": "", "None": "", "null": ""}, regex=False)

    lsd = pd.to_datetime(_df_col_series(ordered, "last_selling_date"), errors="coerce")
    lsd_str = lsd.dt.strftime("%Y-%m-%d").fillna("")

    prio = pd.to_numeric(
        _df_col_series(ordered, "priority_score", "Priority_Score"),
        errors="coerce",
    ).round(2)

    # Defense in depth: never export a store label in the Site ID column.
    export_frame = _normalize_site_id_vs_name(ordered.copy())
    out = pd.DataFrame(
        {
            "Site": _effective_site_series(export_frame).astype(str),
            "Site ID": _df_col_series(export_frame, "Site_ID").astype(str).str.strip(),
            "Store Type": st,
            "Grade": _df_col_series(ordered, "store_grade").astype(str).str.strip(),
            "SKU": _df_col_series(ordered, "SKU").astype(str).str.strip(),
            "Barcode": _df_col_series(ordered, "Barcode").map(_barcode_to_str).astype(str).str.strip(),
            "Size": _df_col_series(ordered, "Size").astype(str).str.strip(),
            "Season": _df_col_series(ordered, "Season").astype(str).str.strip(),
            "Week": _df_col_series(ordered, "Week").astype(str).str.strip(),
            "Week Date Range": _df_col_series(ordered, "Week_Date_Range", "week_date_range")
            .astype(str)
            .str.strip(),
            "Overall Total Sales": _num_series(ordered, "Sum_of_Sales", "total_sales").astype(int),
            "Sold Units- last 35 days": _num_series(ordered, "sold_qty_prev_35d").astype(int),
            "Sold Units- Last week": _num_series(ordered, "lag1_sold_qty").astype(int),
            "Last Selling Date": lsd_str,
            "Days Since Last Sale": pd.to_numeric(
                _df_col_series(ordered, "days_since_last_sale"), errors="coerce"
            ),
            "Stock Status": _df_col_series(ordered, "dead_stock_status").astype(str).str.strip(),
            "Store SOH": _num_series(ordered, "Store_Soh", "store_soh").astype(int),
            "Last Week Store SOH": _num_series(
                ordered, "last_week_store_soh", "Last_Week_Store_SOH", "last_week_soh"
            ).astype(int),
            "WH SOH": _num_series(ordered, "WH_Soh", "wh_soh").astype(int),
            "WH SOH After Allocation": pd.to_numeric(
                _df_col_series(ordered, "wh_soh_end_barcode"), errors="coerce"
            ),
            "Forecasted Sales": fc.fillna(0).astype(int),
            "Priority Score": prio,
        }
    )
    if is_site_to_site:
        st_qty = _num_series(ordered, "replen_site_transfer_qty", "unfilled_qty").astype(int)
        out["SITE_TO_SITE_TRANSFER_REQUIRED(QTY)"] = st_qty
    else:
        out["Covered by Store SOH"] = cs.fillna(0).astype(int)
        out["Covered by WH SOH"] = cw.fillna(0).astype(int)
        tf = _num_series(ordered, "total_fulfilled_qty").astype(int)
        out["total_covered(store+wh_soh)"] = tf
        out["SITE_TO_SITE_TRANSFER_REQUIRED(QTY)"] = uf.fillna(0).astype(int)
    out["Refill source"] = _df_col_series(ordered, "refill_source").astype(str).str.strip()
    out["Recommendation Comment"] = _df_col_series(ordered, "Recommendation_Comment").astype(
        str
    ).str.strip()
    out["Refill Note"] = _df_col_series(ordered, "refill_note").astype(str).str.strip()
    return out


def _download_filters_are_default(
    *,
    site: str | None,
    sku: str | None,
    month: str | None,
    week: str | None,
    site_id: str | None,
    search: str | None,
    sort_by: str,
    sort_order: str,
) -> bool:
    """True when Export matches the prebuilt full-table CSV (default sort)."""
    def _blank(v: str | None) -> bool:
        s = (v or "").strip()
        return s == "" or s.lower() in {
            "all",
            "all sites",
            "all site ids",
            "all skus",
            "all months",
            "all weeks",
        }

    if not all(_blank(x) for x in (site, sku, month, week, site_id, search)):
        return False
    sb = (sort_by or "forecasted_refill").strip().lower()
    so = (sort_order or "DESC").strip().upper()
    return sb in {"forecasted_refill", "forecasted_sales", "refill"} and so == "DESC"


def _prebuilt_export_path_for(*, is_site_to_site: bool) -> Path:
    default_path, s2s_path = _export_disk_paths()
    return s2s_path if is_site_to_site else default_path


def _resolve_prebuilt_export_path(*, is_site_to_site: bool) -> Path | None:
    """Locate warm CSV on disk (pull from blob / build once if needed)."""
    path = _prebuilt_export_path_for(is_site_to_site=is_site_to_site)
    if path.exists() and path.stat().st_size >= 1_000:
        return path
    # Hosted cold: pull export with snapshot, else build once from parquet.
    if _prefer_blob_refill():
        _pull_replenishment_snapshot_from_blob()
    if path.exists() and path.stat().st_size >= 1_000:
        return path
    _ensure_prebuilt_exports(force=False)
    if path.exists() and path.stat().st_size >= 1_000:
        return path
    return None


def _build_recommendations_download_bytes(
    *,
    site: str | None,
    sku: str | None,
    month: str | None,
    week: str | None,
    site_id: str | None,
    refill_source: str | None,
    search: str | None,
    sort_by: str,
    sort_order: str,
    export_format: str,
) -> tuple[bytes, str, str]:
    is_site_to_site = (
        (refill_source or "").strip().upper() == SITE_TO_SITE_REFILL_SOURCE.upper()
    )
    work = _recommendations_work_df(
        site=site,
        sku=sku,
        month=month,
        week=week,
        site_id=site_id,
        refill_source=refill_source,
        search=search,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    export_df = _export_recommendations_df(work, is_site_to_site=is_site_to_site)
    stamp = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
    fmt = (export_format or "csv").strip().lower()
    if fmt == "xlsx":
        output = BytesIO()
        with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
            sheet = "Site_to_Site" if is_site_to_site else "Replenishments"
            export_df.to_excel(writer, index=False, sheet_name=sheet[:31])
        output.seek(0)
        return (
            output.getvalue(),
            f"replenishments_recommendations_{stamp}.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    buf = BytesIO()
    buf.write("\ufeff".encode("utf-8"))
    export_df.to_csv(buf, index=False, encoding="utf-8")
    buf.seek(0)
    return (
        buf.getvalue(),
        f"replenishments_recommendations_{stamp}.csv",
        "text/csv; charset=utf-8",
    )


# ─── KPI Summary ─────────────────────────────────────────────────────────────

@router.get("/summary")
async def get_summary(
    site: str = None,
    sku: str = None,
    month: str = None,
    week: str = None,
    site_id: str = None,
    refresh_snapshot: bool = False,
):
    """KPI cards: OneLake replen snapshot (`TABLE`), filtered by site / site_id / sku / month / week only."""
    try:
        if refresh_snapshot:
            clear_replenishments_snapshot_cache()
        df = _snapshot_df()
        if df.empty:
            return _empty_summary_payload("empty")

        work = _apply_summary_filters(df, site, sku, month, week, site_id)
        if work.empty:
            return _empty_summary_payload("empty")

        return _summary_metrics_from_work(work)
    except Exception as e:
        logger.error(f"summary error: {e}")
        out = _empty_summary_payload("error")
        return out


@router.get("/unfilled-cross-verify")
async def get_unfilled_cross_verify(
    limit: int = 50,
    offset: int = 0,
    site: str = None,
    sku: str = None,
    month: str = None,
    week: str = None,
    site_id: str = None,
):
    """
    Row-level verification for remaining unfilled qty after Store + WH allocation.
    """
    try:
        df = _snapshot_df()
        if df.empty:
            return {"data": [], "total_rows": 0, "total_unfilled_qty": 0, "status": "success"}

        work = _apply_summary_filters(df, site, sku, month, week, site_id)

        if work.empty:
            return {"data": [], "total_rows": 0, "total_unfilled_qty": 0, "status": "success"}

        fr = _num_series(work, "forecasted_refill", clip_lower=True)
        soh = _num_series(work, "Store_Soh", "store_soh", clip_lower=True)
        wh = _num_series(work, "WH_Soh", "wh_soh", clip_lower=True)
        rr = _num_series(work, "recommended_refill", clip_lower=True)

        # Verification split based on recommended_refill (actionable qty).
        store_fillable = pd.concat([rr, soh], axis=1).min(axis=1).fillna(0)
        remaining_after_store = (rr - store_fillable).clip(lower=0)
        wh_fillable_after_store = pd.concat([remaining_after_store, wh], axis=1).min(axis=1).fillna(0)
        unfilled_after_store_wh = (remaining_after_store - wh_fillable_after_store).clip(lower=0)

        site_disp = _effective_site_series(work).astype(str)
        sku_disp = work["SKU"].astype(str) if "SKU" in work.columns else pd.Series("", index=work.index, dtype=str)
        out = pd.DataFrame({
            "site": site_disp,
            "sku": sku_disp,
            "forecasted_refill": fr.astype(float),
            "recommended_refill": rr.astype(float),
            "store_soh": soh.astype(float),
            "wh_soh": wh.astype(float),
            "store_fillable": store_fillable.astype(float),
            "wh_fillable_after_store": wh_fillable_after_store.astype(float),
            "unfilled_after_store_wh": unfilled_after_store_wh.astype(float),
        })
        out = out[out["unfilled_after_store_wh"] > 0].sort_values("unfilled_after_store_wh", ascending=False)
        total_rows = int(len(out))
        total_unfilled_qty = int(out["unfilled_after_store_wh"].sum()) if total_rows > 0 else 0
        paged = out.iloc[offset: offset + limit]

        data = []
        for _, r in paged.iterrows():
            data.append({
                "site": str(r.get("site", "") or ""),
                "sku": str(r.get("sku", "") or ""),
                "forecasted_refill": int(r.get("forecasted_refill", 0) or 0),
                "recommended_refill": int(r.get("recommended_refill", 0) or 0),
                "store_soh": int(r.get("store_soh", 0) or 0),
                "wh_soh": int(r.get("wh_soh", 0) or 0),
                "store_fillable": int(r.get("store_fillable", 0) or 0),
                "wh_fillable_after_store": int(r.get("wh_fillable_after_store", 0) or 0),
                "unfilled_after_store_wh": int(r.get("unfilled_after_store_wh", 0) or 0),
            })
        return {
            "data": data,
            "total_rows": total_rows,
            "total_unfilled_qty": total_unfilled_qty,
            "status": "success",
        }
    except Exception as e:
        logger.error(f"unfilled-cross-verify error: {e}")
        return {"data": [], "total_rows": 0, "total_unfilled_qty": 0, "status": "success"}


@router.get("/by-site")
async def get_by_site(
    site: str = None,
    sku: str = None,
    month: str = None,
    week: str = None,
    site_id: str = None,
):
    """Top stores ranked by total Sales quantity."""
    try:
        df = _apply_summary_filters(_snapshot_df(), site, sku, month, week, site_id)
        if df.empty:
            return {"data": [], "status": "success"}
        g = (
            df.assign(
                site_key=_effective_site_series(df),
                total_sales=_num_series(df, "Sum_of_Sales", "total_sales"),
                total_refill=_num_series(df, "recommended_refill"),
                total_wh_soh=_num_series(df, "WH_Soh", "wh_soh"),
            )
            .groupby("site_key", dropna=True)[["total_sales", "total_refill", "total_wh_soh"]]
            .sum()
            .sort_values("total_sales", ascending=False)
            .head(5)
            .reset_index()
        )
        data = [
            {
                "site": str(r["site_key"] or "Unknown"),
                "total_sales": int(r["total_sales"]),
                "total_refill": int(r["total_refill"]),
                "wh_soh": int(r["total_wh_soh"]),
            }
            for _, r in g.iterrows()
        ]
        return {"data": data, "status": "success"}
    except Exception as e:
        logger.error(f"by-site error: {e}")
        return {"data": [], "status": "success"}


@router.get("/by-sku")
async def get_by_sku(
    site: str = None,
    sku: str = None,
    month: str = None,
    week: str = None,
    site_id: str = None,
):
    """Top SKUs by total Sales quantity."""
    try:
        df = _apply_summary_filters(_snapshot_df(), site, sku, month, week, site_id)
        if df.empty or "SKU" not in df.columns:
            return {"data": [], "status": "success"}
        g = (
            df.assign(
                total_sales=_num_series(df, "Sum_of_Sales", "total_sales"),
                total_refill=_num_series(df, "recommended_refill"),
                total_wh_soh=_num_series(df, "WH_Soh", "wh_soh"),
            )
            .groupby("SKU", dropna=True)[["total_sales", "total_refill", "total_wh_soh"]]
            .sum()
            .sort_values("total_sales", ascending=False)
            .head(5)
            .reset_index()
        )
        data = [
            {
                "sku": str(r["SKU"]),
                "total_sales": int(r["total_sales"]),
                "total_refill": int(r["total_refill"]),
                "wh_soh": int(r["total_wh_soh"]),
            }
            for _, r in g.iterrows()
        ]
        return {"data": data, "status": "success"}
    except Exception as e:
        logger.error(f"by-sku error: {e}")
        return {"data": [], "status": "success"}

@router.get("/drilldown")
async def get_drilldown(
    type: str = Query(None),
    filter_val: str = Query(None),
    site: str = Query(None),
    sku: str = Query(None),
    month: str = Query(None),
    week: str = Query(None),
    site_id: str = Query(None),
):
    """Drilldown table for Top Stores / Top SKUs charts (OneLake snapshot)."""
    try:
        real_site = site
        real_sku = sku
        if type == "site":
            real_site = filter_val
        elif type == "sku":
            real_sku = filter_val

        df = _snapshot_df()
        if df.empty:
            return {"data": [], "status": "success"}

        work = _apply_summary_filters(df, real_site, real_sku, month, week, site_id)
        if work.empty:
            return {"data": [], "status": "success"}


        work = work.copy()
        for col in [
            "Sum_of_Sales",
            "sold_qty_prev_5w",
            "sold_qty_prev_35d",
            "lag1_sold_qty",
            "Store_Soh",
            "WH_Soh",
            "predicted_qty",
            "forecasted_refill",
            "recommended_refill",
            "total_fulfilled_qty",
            "unfilled_qty",
            "covered_by_store_soh",
            "covered_by_wh_soh",
        ]:
            if col in work.columns:
                work[col] = pd.to_numeric(work[col], errors="coerce").fillna(0)

        work = work.assign(
            _disp_site=_effective_site_series(work),
            _fc_disp=_forecasted_qty_display_series(work),
        )
        sort_col = "Sum_of_Sales" if "Sum_of_Sales" in work.columns else None
        if sort_col:
            work = work.sort_values(sort_col, ascending=False)
        work = work.head(200)

        data = []
        for _, row in work.iterrows():
            _dd_ps = _optional_float_out(row.get("priority_score"))
            if _dd_ps is None:
                _dd_ps = _optional_float_out(row.get("Priority_Score"))
            data.append({
                "sku": str(row.get("SKU") or ""),
                "size": str(row.get("Size") or ""),
                "season": str(row.get("Season") or ""),
                "barcode": _barcode_to_str(row.get("Barcode")) or "—",
                "total_sales": _int_from_cell(row.get("Sum_of_Sales", 0)),
                "sold_qty_prev_5w": _int_from_cell(row.get("sold_qty_prev_5w", 0)),
                "sold_qty_prev_35d": _int_from_cell(row.get("sold_qty_prev_35d", 0)),
                "lag1_sold_qty": _int_from_cell(row.get("lag1_sold_qty", 0)),
                "soh": _int_from_cell(row.get("Store_Soh", 0)),
                "wh_soh": _int_from_cell(row.get("WH_Soh", 0)),
                "forecasted": _int_from_cell(row.get("_fc_disp", 0)),
                "refill": _int_from_cell(row.get("recommended_refill", 0)),
                "covered_by_store_soh": _int_from_cell(row.get("covered_by_store_soh", 0)),
                "covered_by_wh_soh": _int_from_cell(row.get("covered_by_wh_soh", 0)),
                "total_fulfilled_qty": _int_from_cell(row.get("total_fulfilled_qty", 0)),
                "unfilled_qty": _int_from_cell(row.get("unfilled_qty", 0)),
                "refill_source": str(row.get("refill_source", "") or "").strip(),
                "week": str(row.get("Week")) if row.get("Week") is not None else None,
                "week_date_range": str(row.get("Week_Date_Range") or ""),
                "recommendation_comment": str(row.get("Recommendation_Comment") or "—"),
                "refill_note": str(row.get("refill_note") or "—"),
                "site": str(row.get("_disp_site") or "—"),
                "site_id": str(row.get("Site_ID", "") or "").strip(),
                "last_selling_date": _format_lake_date_cell(row.get("last_selling_date")),
                "days_since_last_sale": _optional_int_cell(row.get("days_since_last_sale")),
                "dead_stock_status": str(row.get("dead_stock_status") or "").strip(),
                "priority_score": round(_dd_ps, 2) if _dd_ps is not None else None,
                "priority_score_raw": _optional_float_out(row.get("priority_score_raw")),
                "total_sold_qty": _optional_float_out(row.get("total_sold_qty")),
                "priority_expl_timing_bonus": _optional_float_out(
                    row.get("priority_expl_timing_bonus")
                ),
            })
        return {"data": data, "status": "success"}
    except Exception as e:
        logger.error(f"drilldown error: {e}")
        return {"data": [], "status": "success"}

# ─── Refill by Season ─────────────────────────────────────────────────────────

@router.get("/by-season")
async def get_by_season():
    """Refill vs WH SoH vs Sales breakdown by season."""
    try:
        df = _snapshot_df()
        if df.empty:
            return {"data": [], "status": "success"}
        season_col = (
            df["Season"].fillna("Unknown").astype(str)
            if "Season" in df.columns
            else pd.Series(["Unknown"] * len(df), index=df.index)
        )
        season_col = season_col.replace({"nan": "Unknown", "None": "Unknown"})
        w = df.assign(
            _season=season_col,
            rec_count=1,
            total_refill=_num_series(df, "recommended_refill"),
            total_wh_soh=_num_series(df, "WH_Soh", "wh_soh"),
            total_sales=_num_series(df, "Sum_of_Sales", "total_sales"),
            total_sob=_num_series(df, "Store_Soh", "store_soh"),
        )
        g = (
            w.groupby("_season")
            .agg(
                rec_count=("rec_count", "sum"),
                total_refill=("total_refill", "sum"),
                total_wh_soh=("total_wh_soh", "sum"),
                total_sales=("total_sales", "sum"),
                total_sob=("total_sob", "sum"),
            )
            .reset_index()
            .sort_values("total_refill", ascending=False)
        )
        data = [
            {
                "season": str(r["_season"]),
                "count": int(r["rec_count"]),
                "total_refill": int(r["total_refill"]),
                "wh_soh": int(r["total_wh_soh"]),
                "total_sales": int(r["total_sales"]),
                "total_soh": int(r["total_sob"]),
            }
            for _, r in g.iterrows()
        ]
        return {"data": data, "status": "success"}
    except Exception as e:
        logger.error(f"by-season error: {e}")
        return {"data": [], "status": "success"}


# ─── Refill by Month ──────────────────────────────────────────────────────────

@router.get("/by-month")
async def get_by_month():
    """Refill trend by month (useful for time-series line chart)."""
    try:
        df = _snapshot_df()
        if df.empty or "Month" not in df.columns:
            return {"data": [], "status": "success"}
        sub = df[df["Month"].notna()].copy()
        sub["_m"] = pd.to_numeric(sub["Month"], errors="coerce")
        sub = sub[sub["_m"].notna()]
        if sub.empty:
            return {"data": [], "status": "success"}
        w = sub.assign(
            total_refill=_num_series(sub, "recommended_refill"),
            total_wh_soh=_num_series(sub, "WH_Soh", "wh_soh"),
            total_sales=_num_series(sub, "Sum_of_Sales", "total_sales"),
            rec_count=1,
        )
        g = (
            w.groupby("_m")
            .agg(
                total_refill=("total_refill", "sum"),
                total_wh_soh=("total_wh_soh", "sum"),
                total_sales=("total_sales", "sum"),
                rec_count=("rec_count", "sum"),
            )
            .reset_index()
            .sort_values("_m")
        )
        month_map = {
            1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
            7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec",
        }
        data = []
        for _, r in g.iterrows():
            mn = int(r["_m"])
            data.append(
                {
                    "month": month_map.get(mn, str(mn)),
                    "month_num": mn,
                    "total_refill": int(r["total_refill"]),
                    "wh_soh": int(r["total_wh_soh"]),
                    "total_sales": int(r["total_sales"]),
                    "rec_count": int(r["rec_count"]),
                }
            )
        return {"data": data, "status": "success"}
    except Exception as e:
        logger.error(f"by-month error: {e}")
        return {"data": [], "status": "success"}


# ─── Refill by Size ───────────────────────────────────────────────────────────

@router.get("/by-size")
async def get_by_size():
    """Refill distribution by size — helps understand size-curve gaps."""
    try:
        df = _snapshot_df()
        if df.empty:
            return {"data": [], "status": "success"}
        size_col = (
            df["Size"].fillna("Unknown").astype(str)
            if "Size" in df.columns
            else pd.Series(["Unknown"] * len(df), index=df.index)
        )
        size_col = size_col.replace({"nan": "Unknown", "None": "Unknown"})
        w = df.assign(
            _size=size_col,
            rec_count=1,
            total_refill=_num_series(df, "recommended_refill"),
            total_wh_soh=_num_series(df, "WH_Soh", "wh_soh"),
        )
        g = (
            w.groupby("_size")
            .agg(
                rec_count=("rec_count", "sum"),
                total_refill=("total_refill", "sum"),
                total_wh_soh=("total_wh_soh", "sum"),
            )
            .reset_index()
            .sort_values("total_refill", ascending=False)
        )
        data = [
            {
                "size": str(r["_size"]),
                "count": int(r["rec_count"]),
                "total_refill": int(r["total_refill"]),
                "wh_soh": int(r["total_wh_soh"]),
            }
            for _, r in g.iterrows()
        ]
        return {"data": data, "status": "success"}
    except Exception as e:
        logger.error(f"by-size error: {e}")
        return {"data": [], "status": "success"}


@router.get("/by-category")
async def get_by_category():
    """Sales, SOB, and Refill aggregated by Category (DEPARTMENT)."""
    try:
        df = _snapshot_df()
        if df.empty:
            return {"data": [], "status": "success"}
        if "DEPARTMENT" not in df.columns:
            return {"data": [], "status": "success"}
        cat_col = df["DEPARTMENT"].fillna("Other").astype(str).replace({"nan": "Other", "None": "Other"})
        w = df.assign(
            _cat=cat_col,
            total_sales=_num_series(df, "Sum_of_Sales", "total_sales"),
            total_soh=_num_series(df, "Store_Soh", "store_soh"),
            total_refill=_num_series(df, "recommended_refill"),
        )
        g = (
            w.groupby("_cat")
            .agg(
                total_sales=("total_sales", "sum"),
                total_soh=("total_soh", "sum"),
                total_refill=("total_refill", "sum"),
            )
            .reset_index()
            .sort_values("total_refill", ascending=False)
            .head(10)
        )
        data = [
            {
                "category": str(r["_cat"]),
                "total_sales": int(r["total_sales"]),
                "soh": int(r["total_soh"]),
                "refill": int(r["total_refill"]),
            }
            for _, r in g.iterrows()
        ]
        return {"data": data, "status": "success"}
    except Exception as e:
        logger.error(f"by-category error: {e}")
        return {"data": [], "status": "success"}


def _fill_pct_to_bucket(val: object) -> str | None:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    try:
        v = float(str(val).replace("%", "").strip())
    except ValueError:
        return None
    if v <= 25:
        return "0-25%"
    if v <= 50:
        return "25-50%"
    if v <= 75:
        return "50-75%"
    if v <= 100:
        return "75-100%"
    return "100%+"


# ─── Fill % Buckets ───────────────────────────────────────────────────────────

@router.get("/fill-percent-buckets")
async def get_fill_percent_buckets():
    """
    Distribution of Fill_Percent values in buckets:
    0-25%, 25-50%, 50-75%, 75-100%, 100%+
    """
    try:
        df = _snapshot_df()
        if df.empty or "Fill_Percent" not in df.columns:
            return {"data": [], "status": "success"}
        w = df.assign(_bucket=df["Fill_Percent"].map(_fill_pct_to_bucket))
        w = w[w["_bucket"].notna()]
        if w.empty:
            return {"data": [], "status": "success"}
        w = w.assign(
            rec_count=1,
            total_refill=_num_series(w, "recommended_refill"),
        )
        order = ["0-25%", "25-50%", "50-75%", "75-100%", "100%+"]
        g = w.groupby("_bucket").agg(
            rec_count=("rec_count", "sum"), total_refill=("total_refill", "sum")
        )
        g = g.reindex(order).fillna(0).reset_index()
        data = [
            {"bucket": str(r["_bucket"]), "count": int(r["rec_count"]), "total_refill": int(r["total_refill"])}
            for _, r in g.iterrows()
        ]
        return {"data": data, "status": "success"}
    except Exception as e:
        logger.error(f"fill-percent-buckets error: {e}")
        return {"data": [], "status": "success"}


# ─── Rank Distribution ────────────────────────────────────────────────────────

@router.get("/rank-distribution")
async def get_rank_distribution():
    """Top 10 ranks by count and total refill — for priority triage."""
    try:
        df = _snapshot_df()
        if df.empty or "Rank" not in df.columns:
            return {"data": [], "status": "success"}
        sub = df[df["Rank"].notna()].copy()
        sub["_r"] = pd.to_numeric(sub["Rank"], errors="coerce")
        sub = sub[sub["_r"].notna()]
        if sub.empty:
            return {"data": [], "status": "success"}
        w = sub.assign(
            rec_count=1,
            total_refill=_num_series(sub, "recommended_refill"),
            total_wh_soh=_num_series(sub, "WH_Soh", "wh_soh"),
        )
        g = (
            w.groupby("_r")
            .agg(
                rec_count=("rec_count", "sum"),
                total_refill=("total_refill", "sum"),
                total_wh_soh=("total_wh_soh", "sum"),
            )
            .reset_index()
            .sort_values("_r")
            .head(10)
        )
        data = [
            {
                "rank": int(r["_r"]),
                "count": int(r["rec_count"]),
                "total_refill": int(r["total_refill"]),
                "wh_soh": int(r["total_wh_soh"]),
            }
            for _, r in g.iterrows()
        ]
        return {"data": data, "status": "success"}
    except Exception as e:
        logger.error(f"rank-distribution error: {e}")
        return {"data": [], "status": "success"}


# ─── Detailed Table ───────────────────────────────────────────────────────────

@router.get("/recommendations")
async def get_recommendations(
    limit: int = 50,
    offset: int = 0,
    site: str = None,
    sku: str = None,
    month: str = None,
    week: str = None,
    site_id: str = None,
    refill_source: str = None,
    search: str = None,
    sort_by: str = "forecasted_refill",
    sort_order: str = "DESC",
    refresh_snapshot: bool = False,
    full_table: bool = False,
):
    """Full paginated recommendations table with filters (OneLake snapshot).

    `full_table=True` skips dropping site-to-site rows with total_fulfilled_qty==0 (used for Excel export
    to match a complete replenishment table slice).
    """
    try:
        if refresh_snapshot:
            clear_replenishments_snapshot_cache()
        ordered = _recommendations_work_df(
            site=site,
            sku=sku,
            month=month,
            week=week,
            site_id=site_id,
            refill_source=refill_source,
            search=search,
            sort_by=sort_by,
            sort_order=sort_order,
            full_table=full_table,
        )
        if ordered.empty:
            return {"data": [], "total": 0, "status": "success"}

        ordered = ordered.assign(_disp_site=_effective_site_series(ordered))
        total = int(len(ordered))
        paged = ordered.iloc[offset : offset + limit]
        data = []
        for _, r in paged.iterrows():
            data.append(
                {
                    "site": str(r.get("_disp_site", "") or ""),
                    "site_id": str(r.get("Site_ID", "") or "").strip(),
                    "store_type": str(r.get("store_type", "") or "").strip(),
                    "store_grade": str(r.get("store_grade", "") or "").strip(),
                    "barcode": str(r.get("Barcode", "") or "").strip(),
                    "sku": str(r.get("SKU", "") or "").strip(),
                    "size": str(r.get("Size", "") or "").strip(),
                    "season": str(r.get("Season", "") or "").strip(),
                    "month": str(r.get("Month", "") or "").strip(),
                    "week": str(r.get("Week", "") or "") if r.get("Week") is not None else None,
                    "total_sales": _int_from_cell(r.get("Sum_of_Sales", 0)),
                    "sold_qty_prev_35d": _int_from_cell(r.get("sold_qty_prev_35d", 0)),
                    "lag1_sold_qty": _int_from_cell(r.get("lag1_sold_qty", 0)),
                    "last_selling_date": _format_lake_date_cell(r.get("last_selling_date")),
                    "days_since_last_sale": _optional_int_cell(r.get("days_since_last_sale")),
                    "dead_stock_status": str(r.get("dead_stock_status") or "").strip(),
                    "soh": _int_from_cell(r.get("Store_Soh", 0)),
                    "last_week_store_soh": _int_from_cell(
                        r.get("last_week_store_soh")
                        or r.get("Last_Week_Store_SOH")
                        or r.get("last_week_soh")
                        or 0
                    ),
                    "wh_soh": _int_from_cell(r.get("WH_Soh", 0)),
                    "wh_soh_end_barcode": _optional_int_cell(r.get("wh_soh_end_barcode")),
                    "forecasted": _int_from_cell(r.get("forecasted_refill", 0)),
                    "refill": _int_from_cell(r.get("recommended_refill", 0)),
                    "covered_by_store_soh": _int_from_cell(r.get("covered_by_store_soh", 0)),
                    "covered_by_wh_soh": _int_from_cell(r.get("covered_by_wh_soh", 0)),
                    "total_fulfilled_qty": _int_from_cell(r.get("total_fulfilled_qty", 0)),
                    "unfilled_qty": _int_from_cell(r.get("unfilled_qty", 0)),
                    "refill_source": str(r.get("refill_source", "") or "").strip(),
                    "week_date_range": str(r.get("Week_Date_Range", "") or ""),
                    "recommendation_comment": str(r.get("Recommendation_Comment", "") or ""),
                    "refill_note": str(r.get("refill_note", "") or ""),
                    "priority_score": round(float(r.get("priority_score") or r.get("Priority_Score") or 0.0), 2),
                    "priority_score_raw": _optional_float_out(r.get("priority_score_raw")),
                    "total_sold_qty": _optional_float_out(r.get("total_sold_qty")),
                    "priority_expl_timing_bonus": _optional_float_out(
                        r.get("priority_expl_timing_bonus")
                    ),
                }
            )
        return {"data": data, "total": total, "status": "success"}
    except Exception as e:
        logger.error(f"recommendations error: {e}")
        return {"data": [], "total": 0, "status": "success"}


@router.get("/download-recommendations")
async def download_recommendations(
    request: Request,
    site: str = None,
    sku: str = None,
    month: str = None,
    week: str = None,
    site_id: str = None,
    refill_source: str = None,
    search: str = None,
    sort_by: str = "forecasted_refill",
    sort_order: str = "DESC",
    format: str = Query("csv", description="csv (prebuilt when unfiltered) or xlsx"),
):
    """Download filtered recommendations (same slice as UI table).

    Unfiltered CSV uses a prebuilt file written with the page snapshot (~instant start).
    Filtered / xlsx still builds on demand.
    """
    export_format = (format or "csv").strip().lower()
    if export_format not in ("csv", "xlsx"):
        raise HTTPException(status_code=400, detail="format must be csv or xlsx")
    is_site_to_site = (
        (refill_source or "").strip().upper() == SITE_TO_SITE_REFILL_SOURCE.upper()
    )
    try:
        if export_format == "csv" and _download_filters_are_default(
            site=site,
            sku=sku,
            month=month,
            week=week,
            site_id=site_id,
            search=search,
            sort_by=sort_by,
            sort_order=sort_order,
        ):
            # Only default / site-to-site full tabs hit the warm file.
            if is_site_to_site or not (refill_source or "").strip():
                loop = asyncio.get_running_loop()
                path = await loop.run_in_executor(
                    None,
                    partial(_resolve_prebuilt_export_path, is_site_to_site=is_site_to_site),
                )
                if path is not None:
                    stamp = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
                    filename = (
                        f"replenishments_site_to_site_{stamp}.csv"
                        if is_site_to_site
                        else f"replenishments_recommendations_{stamp}.csv"
                    )
                    log_download("Replenishment Recommendations", filename, request)
                    logger.info(
                        "[download] serving prebuilt export %s (%s bytes)",
                        path.name,
                        path.stat().st_size,
                    )
                    return FileResponse(
                        path,
                        media_type="text/csv; charset=utf-8",
                        filename=filename,
                        headers={
                            "Cache-Control": "no-store",
                            "X-Replenishment-Export": "prebuilt",
                        },
                    )

        loop = asyncio.get_running_loop()
        build = partial(
            _build_recommendations_download_bytes,
            site=site,
            sku=sku,
            month=month,
            week=week,
            site_id=site_id,
            refill_source=refill_source,
            search=search,
            sort_by=sort_by,
            sort_order=sort_order,
            export_format=export_format,
        )
        body, filename, media_type = await loop.run_in_executor(None, build)
        log_download("Replenishment Recommendations", filename, request)
        return StreamingResponse(
            BytesIO(body),
            media_type=media_type,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-Replenishment-Export": "generated",
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"download recommendations error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to generate export file: {str(e)}")


# ─── Filters ──────────────────────────────────────────────────────────────────

@router.get("/filters/seasons")
async def get_seasons():
    try:
        df = _snapshot_df()
        if df.empty or "Season" not in df.columns:
            return ["All"]
        vals = sorted(
            {str(v) for v in df["Season"].dropna().tolist() if str(v).strip() and str(v).lower() not in ("nan", "none")}
        )
        return ["All"] + vals
    except Exception:
        return ["All"]


@router.post("/actions/clear-snapshot-cache")
async def clear_snapshot_cache_endpoint():
    """Drop in-memory replenishment snapshot cache so the next API call re-reads OneLake/Delta."""
    clear_replenishments_snapshot_cache()
    return {"status": "success", "cleared": True}


@router.get("/columns")
async def get_columns():
    """Probe actual table columns — useful for debugging."""
    try:
        df = _snapshot_df()
        if df.empty:
            return {"columns": [], "sample": [], "status": "success"}
        cols = df.columns.tolist()
        sample = df.iloc[0].to_dict() if len(df) > 0 else {}
        return {"columns": cols, "sample": sample, "status": "success"}
    except Exception as e:
        logger.error(f"columns error: {e}")
        return {"columns": [], "sample": [], "status": "success"}

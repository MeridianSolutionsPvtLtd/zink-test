"""
Stock Overview API — Current SOH for Store Inventory from daily_inventory_movement.

COMBINED_SOH / SOH-Master remain available for reconciliation (actual side) only.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/stock-overview", tags=["Stock Overview"])

_TABLE = "COMBINED_SOH"
_CACHE_DF: pd.DataFrame | None = None
_CACHE_KEY: str | None = None
_CACHE_AT = 0.0
_CACHE_TTL = int(os.environ.get("STOCK_OVERVIEW_CACHE_TTL_SECONDS", "3600"))
_DAILY_SOH_DF: pd.DataFrame | None = None
_DAILY_SOH_AT = 0.0
_DAILY_SOH_SIG: str | None = None
_DAILY_SOH_TTL = float(os.environ.get("STOCK_OVERVIEW_DAILY_TTL_SECONDS", "1800"))
_DAILY_SOH_DISK = "stock_overview_daily_movement_v1.parquet"
_DAILY_SOH_DISK_KEY = "stock_overview_daily_movement_v1.key"
# as-of date → (loaded_at, df) for Store Inventory single-date SOH
_ASOF_SOH_CACHE: dict[str, tuple[float, pd.DataFrame]] = {}
_ASOF_SOH_TTL = float(os.environ.get("STOCK_OVERVIEW_ASOF_TTL_SECONDS", "600"))
_ASOF_SOH_LOCK = threading.Lock()
_SITE_NAME_LOOKUP: dict[str, str] | None = None
_SITE_NAME_LOOKUP_FAILED = False
_SITE_NAME_CACHE_KEY: str | None = None
_SITE_NAME_LOCK = threading.Lock()
_SOH_LOAD_LOCK = threading.Lock()
_ITEM_MAP_CACHE: pd.DataFrame | None = None
_API_CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "api_cache"

# Fixed 5 store channels (same as replenishment selection)
CHANNEL_OPTIONS = [
    {"id": "all", "label": "All channels"},
    {"id": "PT", "label": "Pantaloons (PT)"},
    {"id": "SSL", "label": "Shoppers Stop (SSL)"},
    {"id": "CENTRO", "label": "Centro"},
    {"id": "ICONIC", "label": "Iconic"},
    {"id": "EBO", "label": "EBO"},
]
_VALID_CHANNELS = {c["id"] for c in CHANNEL_OPTIONS if c["id"] != "all"}


def _site_id_str(v: Any) -> str:
    """Normalize site id — strip Excel float decimals (1274.0 → 1274)."""
    return _barcode_str(v)


def _barcode_str(v: Any) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    s = str(v).strip()
    if not s or s.lower() in ("nan", "none", "<na>"):
        return ""
    if s.endswith(".0"):
        s = s[:-2]
    return s


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = out.columns.astype(str).str.strip()

    rename = {
        "Site_id": "site_id",
        "Site_ID": "site_id",
        "SITE_ID": "site_id",
        "Site Code": "site_id",
        "SITE CODE": "site_id",
        "Site_Code": "site_id",
        "SITE_CODE": "site_id",
        "Store Code": "site_id",
        "STORE CODE": "site_id",
        "Barcode": "barcode",
        "BARCODE": "barcode",
        "EAN": "barcode",
        "Ean": "barcode",
        "Current Stock Qty": "qty",
        "CURRENT STOCK QTY": "qty",
        "CLOSING_STOCK_QTY": "qty",
        "QTY": "qty",
        "Qty": "qty",
        "Quantity": "qty",
        "QUANTITY": "qty",
        "Qty Available": "qty",
        "TOTAL_STOCK_QTY": "qty",
        "Total Stock Quantity": "qty",
        "Unrestricted Stock Quantity": "qty",
        "Source": "source",
        "SOURCE": "source",
        "SKU": "sku",
        "Size": "size",
        "SIZE": "size",
        "Site Name": "site_name",
        "SITE_NAME": "site_name",
        "Site_Name": "site_name",
    }
    for src, dst in rename.items():
        if src in out.columns and dst not in out.columns:
            out = out.rename(columns={src: dst})

    for col in ("site_id", "barcode", "source", "sku", "size", "site_name"):
        if col not in out.columns:
            out[col] = ""
        out[col] = out[col].astype(str).str.strip().replace({"nan": "", "None": "", "<NA>": ""})

    if "qty" not in out.columns or out["qty"].isna().all():
        qty_col = next(
            (
                c
                for c in out.columns
                if str(c).strip().lower()
                in (
                    "qty",
                    "quantity",
                    "current stock qty",
                    "closing_stock_qty",
                    "total_stock_qty",
                    "qty available",
                )
            ),
            None,
        )
        out["qty"] = pd.to_numeric(out[qty_col], errors="coerce") if qty_col else 0.0
    else:
        out["qty"] = pd.to_numeric(out["qty"], errors="coerce")

    out["barcode"] = out["barcode"].apply(_barcode_str)
    out["site_id"] = out["site_id"].apply(_site_id_str)
    out["qty"] = out["qty"].fillna(0).clip(lower=0)
    out = out[out["site_id"].astype(str).str.strip().ne("")].copy()

    # Optional SOH as-of date (SOH-Master / dated snapshots)
    date_col = next(
        (
            c
            for c in out.columns
            if str(c).strip().lower()
            in (
                "date",
                "soh_date",
                "stock_date",
                "as on date",
                "as_on_date",
                "asof",
                "as_of",
            )
        ),
        None,
    )
    if date_col is not None:
        out["date"] = pd.to_datetime(out[date_col], errors="coerce")
    elif "date" not in out.columns:
        out["date"] = pd.NaT

    # SOH-Master often has no Source — derive PT/SSL/CENTRO/ICONIC/EBO from site
    blank_src = out["source"].astype(str).str.strip().eq("")
    if blank_src.any():
        try:
            from pipeline.pipeline_inputs import derive_store_type_series
        except ImportError:
            from pipeline_inputs import derive_store_type_series  # type: ignore
        derived = derive_store_type_series(out["site_name"], out["site_id"])
        out.loc[blank_src, "source"] = derived.loc[blank_src].astype(str).str.strip()

    return out


def _enrich_sku(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "sku" not in out.columns:
        out["sku"] = ""
    if "size" not in out.columns:
        out["size"] = ""

    need_sku = out["sku"].astype(str).str.strip().eq("").all()
    if not need_sku:
        return out

    try:
        from pipeline.pipeline_inputs import normalize_barcode
    except ImportError:
        from pipeline_inputs import normalize_barcode  # type: ignore

    global _ITEM_MAP_CACHE
    try:
        if _ITEM_MAP_CACHE is None:
            try:
                from pipeline.pipeline_inputs import build_item_map
            except ImportError:
                from pipeline_inputs import build_item_map  # type: ignore
            _ITEM_MAP_CACHE = build_item_map()
        item_map = _ITEM_MAP_CACHE
        out["_barcode_key"] = normalize_barcode(out["barcode"])
        merged = out.merge(
            item_map.rename(columns={"SKU": "sku_map", "SIZE": "size_map"}),
            left_on="_barcode_key",
            right_on="barcode_key",
            how="left",
        )
        merged["sku"] = merged["sku"].where(
            merged["sku"].astype(str).str.strip().ne(""),
            merged["sku_map"].fillna("").astype(str).str.strip(),
        )
        merged["size"] = merged["size"].where(
            merged["size"].astype(str).str.strip().ne(""),
            merged["size_map"].fillna("").astype(str).str.strip(),
        )
        out = merged.drop(columns=["_barcode_key", "barcode_key", "sku_map", "size_map", "MRP"], errors="ignore")
    except Exception as exc:
        logger.warning("SKU enrich from item master skipped: %s", exc)

    return out


def _local_blob_soh_path() -> tuple[str | None, str | None]:
    base = Path(__file__).resolve().parents[2] / "data" / "blob_cache"
    for name in ("output_COMBINED_SOH.xlsx", "output_COMBINED_SOH.csv", "output_COMBINED_SOH.parquet"):
        p = base / name
        if not p.exists():
            continue
        etag_path = p.with_suffix(p.suffix + ".etag")
        if etag_path.exists():
            etag = etag_path.read_text(encoding="utf-8").strip().strip('"').strip("'")
        else:
            etag = str(int(p.stat().st_mtime))
        return str(p), f"combined:{name}:{etag}"
    return None, None


def _local_soh_master_path() -> tuple[str | None, str | None]:
    """Local SOH-Master current file (synced from blob input/SOH-Master/current/)."""
    backend = Path(__file__).resolve().parents[2]
    candidates = [
        backend / "pipeline" / "input_files" / "COMBINED_SOH_MASTER.csv",
        backend / "pipeline" / "input_files" / "COMBINED_SOH_MASTER.xlsx",
        backend / "data" / "blob_cache" / "input_SOH-Master_current_COMBINED_SOH_MASTER.csv",
        backend / "data" / "blob_cache" / "input_SOH-Master_current_COMBINED_SOH_MASTER.xlsx",
    ]
    for p in candidates:
        if p.exists() and p.stat().st_size > 0:
            return str(p), f"soh-master-local:{p.name}:{int(p.stat().st_mtime)}"
    return None, None


def _blob_soh_master_path() -> tuple[str | None, str | None]:
    """Azure input/SOH-Master/current/COMBINED_SOH_MASTER.*"""
    try:
        from api.routes.sales_dashboard import _blob_store

        store = _blob_store()
        if store is None:
            return _local_soh_master_path()
        input_prefix = getattr(store, "input_prefix", None) or "input/"
        if not str(input_prefix).endswith("/"):
            input_prefix = f"{input_prefix}/"
        prefix = f"{input_prefix}SOH-Master/current/"
        blobs = list(store.container.list_blobs(name_starts_with=prefix))
        files = [
            b
            for b in blobs
            if str(b.name).lower().endswith((".csv", ".xlsx", ".xls", ".parquet"))
            and not str(b.name).lower().endswith((".date_bounds.json", ".delta.csv"))
        ]
        if not files:
            return None, None
        # Prefer canonical COMBINED_SOH_MASTER name, else largest / latest
        files.sort(
            key=lambda b: (
                0 if "combined_soh_master" in Path(b.name).name.lower() else 1,
                -int(getattr(b, "size", 0) or 0),
                str(getattr(b, "last_modified", "") or ""),
            )
        )
        blob_name = files[0].name
        etag = store._blob_etag(blob_name) or blob_name
        path = store._ensure_local_file(blob_name)
        if path and path.exists():
            return str(path), f"soh-master:{blob_name}:{etag}"
    except Exception as exc:
        logger.warning("SOH-Master blob read skipped: %s", exc)
    return _local_soh_master_path()


def _local_blob_sales_path() -> str | None:
    backend = Path(__file__).resolve().parents[2]
    for p in (
        backend / "pipeline" / "input_files" / "SSI_PT_CENTRO_ICONIC_EBO_SALES_MASTER.csv",
        backend / "pipeline" / "input_files" / "SSI_PT_CENTRO_ICONIC_SALES_MASTER.csv",
        backend / "data" / "blob_cache" / "output_COMBINED_SALES.csv",
    ):
        if p.exists() and p.stat().st_size > 0:
            return str(p)
    return None


def _sales_csv_path() -> tuple[str | None, str | None]:
    try:
        from api.sales_year_loader import resolve_sales_master_path

        master = resolve_sales_master_path()
        if master is not None and master.exists() and master.stat().st_size > 0:
            return str(master), f"sales-master:{master.name}:{int(master.stat().st_mtime)}"
    except Exception as exc:
        logger.debug("Sales-Master path skipped: %s", exc)

    try:
        from api.routes.sales_dashboard import _blob_store

        store = _blob_store()
        if store is not None:
            blob_name = store._blob_name("COMBINED_SALES", ".csv")
            if store._blob_exists(blob_name):
                etag = store._blob_etag(blob_name) or blob_name
                path = store._ensure_local_file(blob_name)
                if path and path.exists():
                    return str(path), f"{blob_name}:{etag}"
    except Exception as exc:
        logger.debug("COMBINED_SALES blob path skipped: %s", exc)

    local = _local_blob_sales_path()
    if local:
        return local, f"local:{local}"
    try:
        from pipeline.pipeline_inputs import COMBINED_SALES
    except ImportError:
        from pipeline_inputs import COMBINED_SALES  # type: ignore
    if COMBINED_SALES.exists():
        return str(COMBINED_SALES), f"local:{COMBINED_SALES}"
    return None, None


def _safe_cache_key(source_key: str) -> str:
    return (
        source_key.replace(":", "_")
        .replace("/", "_")
        .replace("\\", "_")
        .replace('"', "")
        .replace("'", "")
    )


def _parquet_cache_path(source_key: str) -> Path:
    # v6 = Customer Master + Item Master + Channel auto-fill enriched
    return _API_CACHE_DIR / f"stock_overview_soh_{_safe_cache_key(source_key)}_v6.parquet"


def _load_parquet_cache(source_key: str) -> pd.DataFrame | None:
    path = _parquet_cache_path(source_key)
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path)
        logger.info("Stock overview loaded parquet cache (%s rows)", len(df))
        return df
    except Exception as exc:
        logger.warning("Stock overview parquet cache read failed: %s", exc)
        return None


def _save_parquet_cache(df: pd.DataFrame, source_key: str) -> None:
    try:
        _API_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        df.to_parquet(_parquet_cache_path(source_key), index=False)
        logger.info("Stock overview parquet cache saved (%s rows)", len(df))
    except Exception as exc:
        logger.warning("Stock overview parquet cache write failed: %s", exc)


def _load_site_names_from_csv(path: str) -> dict[str, str]:
    header = _read_csv_path(path, nrows=0)
    col_map = {c.strip().lower(): c for c in header.columns}
    sid_col = col_map.get("site_id")
    name_col = col_map.get("site_name")
    if not sid_col or not name_col:
        return {}

    sub = _read_csv_path(path, usecols=[sid_col, name_col])
    sub.columns = ["site_id", "site_name"]
    sub["site_id"] = sub["site_id"].apply(_site_id_str)
    sub["site_name"] = sub["site_name"].astype(str).str.strip()
    sub = sub[
        sub["site_id"].ne("")
        & sub["site_name"].ne("")
        & ~sub["site_name"].str.lower().isin(("nan", "none"))
    ]
    if sub.empty:
        return {}
    grouped = sub.drop_duplicates("site_id", keep="first")
    return {str(r["site_id"]): str(r["site_name"]) for _, r in grouped.iterrows()}


def _get_site_name_lookup() -> dict[str, str]:
    """site_id → site_name from COMBINED_SALES; reloads when blob ETag changes."""
    global _SITE_NAME_LOOKUP, _SITE_NAME_LOOKUP_FAILED, _SITE_NAME_CACHE_KEY
    path, cache_key = _sales_csv_path()
    if (
        _SITE_NAME_LOOKUP is not None
        and cache_key is not None
        and _SITE_NAME_CACHE_KEY == cache_key
    ):
        return _SITE_NAME_LOOKUP
    if _SITE_NAME_LOOKUP_FAILED and path is None:
        return _SITE_NAME_LOOKUP or {}

    with _SITE_NAME_LOCK:
        if (
            _SITE_NAME_LOOKUP is not None
            and cache_key is not None
            and _SITE_NAME_CACHE_KEY == cache_key
        ):
            return _SITE_NAME_LOOKUP

        if not path:
            _SITE_NAME_LOOKUP_FAILED = True
            return {}

        try:
            lookup = _load_site_names_from_csv(path)
            _SITE_NAME_LOOKUP = lookup
            _SITE_NAME_CACHE_KEY = cache_key
            _SITE_NAME_LOOKUP_FAILED = False
            logger.info("Site name lookup ready (%s stores)", len(lookup))
            return lookup
        except Exception as exc:
            logger.warning("Site name lookup failed: %s", exc)
            _SITE_NAME_LOOKUP_FAILED = True
            return {}


def _normalize_channel_code(val: Any) -> str:
    s = str(val or "").strip().upper()
    if not s or s in ("NAN", "NONE"):
        return ""
    if "SHOPPER" in s or "SHOPER" in s or s == "SSL":
        return "SSL"
    if "PANTALOON" in s or s == "PT":
        return "PT"
    if "CENTRO" in s:
        return "CENTRO"
    if "ICONIC" in s:
        return "ICONIC"
    if "EBO" in s:
        return "EBO"
    return s


def _apply_site_names(df: pd.DataFrame) -> pd.DataFrame:
    lookup = _get_site_name_lookup()
    out = df.copy()
    if "site_name" not in out.columns:
        out["site_name"] = ""
    out["site_name"] = out["site_name"].astype(str).str.strip()
    missing = out["site_name"].eq("")
    if missing.any() and lookup:
        out.loc[missing, "site_name"] = out.loc[missing, "site_id"].map(
            lambda s: lookup.get(str(s), "")
        )

    # Ensure source / channel column is populated
    if "source" not in out.columns:
        out["source"] = ""
    if "sales_channel" in out.columns:
        blank_source = out["source"].astype(str).str.strip().eq("")
        out.loc[blank_source, "source"] = (
            out.loc[blank_source, "sales_channel"].fillna("").astype(str).str.strip()
        )
    blank_source = out["source"].astype(str).str.strip().eq("")
    if blank_source.any():
        site_upper = out["site_name"].astype(str).str.upper()
        cust_upper = (
            out["customer_name"].astype(str).str.upper()
            if "customer_name" in out.columns
            else pd.Series("", index=out.index)
        )
        combined_text = site_upper + " " + cust_upper

        is_ssl = blank_source & (combined_text.str.contains("SHOPPER", na=False) | combined_text.str.contains("SHOPER", na=False) | combined_text.str.contains("SSL", na=False))
        is_pt = blank_source & (combined_text.str.contains("PANTALOONS", na=False) | combined_text.str.contains("PT", na=False))
        is_centro = blank_source & combined_text.str.contains("CENTRO", na=False)
        is_iconic = blank_source & combined_text.str.contains("ICONIC", na=False)

        out.loc[is_ssl, "source"] = "SSL"
        out.loc[is_pt, "source"] = "PT"
        out.loc[is_centro, "source"] = "CENTRO"
        out.loc[is_iconic, "source"] = "ICONIC"

    # Normalize all source / channel values to canonical codes (SSL, PT, CENTRO, ICONIC, EBO)
    out["source"] = out["source"].apply(_normalize_channel_code)
    return out


def _load_local_combined_soh() -> pd.DataFrame:
    try:
        from pipeline.pipeline_inputs import COMBINED_SOH, load_combined_soh
    except ImportError:
        from pipeline_inputs import COMBINED_SOH, load_combined_soh  # type: ignore

    if COMBINED_SOH.exists():
        return load_combined_soh()
    alt = COMBINED_SOH.parent / "combined_soh" / "current" / "COMBINED_SOH.xlsx"
    if alt.exists():
        df = pd.read_excel(alt)
        df.columns = df.columns.astype(str).str.strip()
        return df
    raise FileNotFoundError(f"COMBINED_SOH not found at {COMBINED_SOH} or {alt}")


def _is_excel_path(path: str) -> bool:
    return str(path).lower().endswith((".xlsx", ".xls"))


def _blob_combined_path() -> tuple[str | None, str | None]:
    try:
        from api.routes.sales_dashboard import _blob_store

        store = _blob_store()
        if store is None:
            return _local_blob_soh_path()
        for ext in (".csv", ".xlsx", ".parquet"):
            blob_name = store._blob_name(_TABLE, ext)
            if not store._blob_exists(blob_name):
                continue
            etag = store._blob_etag(blob_name) or blob_name
            path = store._ensure_local_file(blob_name)
            if path and path.exists():
                return str(path), f"combined:{blob_name}:{etag}"
    except Exception as exc:
        logger.warning("COMBINED_SOH blob read skipped: %s", exc)
    return _local_blob_soh_path()


def _preferred_soh_source(explicit: Any = None) -> str:
    """auto | combined | soh-master"""
    s = explicit
    if not isinstance(s, str):
        s = getattr(s, "default", "auto")
    if not isinstance(s, str):
        s = "auto"
    raw = (s or os.environ.get("STOCK_OVERVIEW_SOH_SOURCE") or "auto").strip().lower()
    if raw in ("soh-master", "soh_master", "master"):
        return "soh-master"
    if raw in ("combined", "combined-soh", "combined_soh"):
        return "combined"
    return "auto"


def _resolve_soh_path(prefer: str | None = None) -> tuple[str | None, str | None, str]:
    """
    Resolve SOH file for Inventory Analytics / Stock Overview.

    prefer:
      - combined → COMBINED_SOH only (then fail)
      - soh-master → SOH-Master current only (then fail)
      - auto → SOH-Master (daily all-store) first, else COMBINED_SOH
    Returns (path, cache_key, source_label).
    """
    mode = _preferred_soh_source(prefer)

    if mode == "soh-master":
        path, key = _blob_soh_master_path()
        if path:
            return path, key, "soh-master"
        return None, None, "soh-master"

    if mode == "combined":
        path, key = _blob_combined_path()
        if path:
            return path, key, "combined"
        return None, None, "combined"

    # auto: prefer all-store SOH-Master (daily_stock_template), then COMBINED_SOH
    path, key = _blob_soh_master_path()
    if path:
        return path, key, "soh-master"
    path, key = _blob_combined_path()
    if path:
        logger.info("SOH-Master missing — using COMBINED_SOH for stock overview")
        return path, key, "combined"
    return None, None, "auto"


def _read_excel_path(path: str) -> pd.DataFrame:
    try:
        return pd.read_excel(path, engine="calamine")
    except Exception:
        return pd.read_excel(path, engine="openpyxl")


def _read_csv_path(path: str, **kwargs: Any) -> pd.DataFrame:
    """Read CSV with encoding fallbacks (Ginesys exports often cp1252/latin-1)."""
    try:
        from pipeline.date_merge import is_excel_bytes, read_csv_bytes
    except ImportError:
        from date_merge import is_excel_bytes, read_csv_bytes  # type: ignore

    data = Path(path).read_bytes()
    if is_excel_bytes(data):
        # Blob/canonical name can be .csv while bytes are still xlsx
        return _read_excel_path(path)
    return read_csv_bytes(data, **kwargs)


def _read_path(path: str) -> pd.DataFrame:
    p = path.lower()
    if p.endswith(".parquet"):
        return pd.read_parquet(path)
    if p.endswith(".csv"):
        return _read_csv_path(path)
    return _read_excel_path(path)


def _load_soh_frame(
    force_refresh: bool = False,
    *,
    path: str | None = None,
    cache_key: str | None = None,
    prefer: str | None = None,
) -> tuple[pd.DataFrame, str | None, str]:
    source_label = "combined"
    if path is None and cache_key is None:
        path, cache_key, source_label = _resolve_soh_path(prefer)
    elif cache_key:
        source_label = (
            "soh-master"
            if str(cache_key).startswith("soh-master")
            else "combined"
        )

    if not force_refresh and cache_key:
        cached = _load_parquet_cache(cache_key)
        if cached is not None and not cached.empty:
            return cached, cache_key, source_label

    if path:
        logger.info("Stock overview loading %s (%s)", path, source_label)
        raw = _read_path(path)
    else:
        # Last resort: local pipeline COMBINED_SOH, then local SOH-Master
        try:
            logger.info("Stock overview loading local COMBINED_SOH")
            raw = _load_local_combined_soh()
            source_label = "combined"
            cache_key = cache_key or f"local-pipeline:{len(raw) if raw is not None else 0}"
        except FileNotFoundError:
            master_path, master_key = _local_soh_master_path()
            if not master_path:
                raise HTTPException(
                    status_code=404,
                    detail=(
                        "No SOH file found. Need COMBINED_SOH (pipeline combine_soh) "
                        "or SOH-Master current (COMBINED_SOH_MASTER.csv)."
                    ),
                )
            logger.info("Stock overview loading local SOH-Master %s", master_path)
            raw = _read_path(master_path)
            source_label = "soh-master"
            cache_key = master_key

    df = _enrich_sku(_normalize_columns(raw))
    if "site_name" not in df.columns:
        df["site_name"] = ""

    # Customer Master dims (ZONE, CITY, STORE GRADE, LTL/New, …) — same as Sales Overview
    try:
        from pipeline.customer_master import enrich_sales_with_customer_master
    except ImportError:
        from customer_master import enrich_sales_with_customer_master  # type: ignore
    df = enrich_sales_with_customer_master(df, site_col="site_id")
    # Prefer master site name when SOH row name is blank
    if "site_name_master" in df.columns:
        blank_name = df["site_name"].astype(str).str.strip().eq("")
        df.loc[blank_name, "site_name"] = (
            df.loc[blank_name, "site_name_master"].fillna("").astype(str).str.strip()
        )

    # Item Master Department / Section (same as Sales Dashboard)
    try:
        from pipeline.item_master import enrich_sales_dept_section_from_item_master
    except ImportError:
        from item_master import enrich_sales_dept_section_from_item_master  # type: ignore
    df = enrich_sales_dept_section_from_item_master(df, barcode_col="barcode")

    # Apply site name lookups and channel normalization
    df = _apply_site_names(df)

    if cache_key:
        _save_parquet_cache(df, cache_key)
    return df, cache_key, source_label


_CACHE_SOURCE_LABEL: str = "combined"


def _first_nonblank(series: pd.Series) -> str:
    for v in series.tolist():
        s = str(v or "").strip()
        if s and s.lower() not in ("nan", "none", "<na>", "null"):
            return s
    return ""


def _enrich_department_section(df: pd.DataFrame, dm_history: pd.DataFrame | None = None) -> pd.DataFrame:
    """Fill blank department/section from daily history + sales barcode map."""
    out = df.copy()
    for c in ("department", "section"):
        if c not in out.columns:
            out[c] = ""
        out[c] = out[c].astype(str).str.strip().replace({"nan": "", "None": "", "<NA>": "", "NaT": ""})

    # 1) Non-blank dept/section from any date in daily movement (same barcode)
    if dm_history is not None and not dm_history.empty and "barcode" in dm_history.columns:
        hist = dm_history.copy()
        hist["barcode"] = hist["barcode"].astype(str).str.strip().str.upper().str.replace(r"\.0$", "", regex=True)
        if "department" in hist.columns:
            hist["department"] = hist["department"].astype(str).str.strip().replace({"nan": "", "None": "", "<NA>": ""})
            dept_map = hist.groupby("barcode", sort=False)["department"].agg(_first_nonblank)
            blank = out["department"].eq("")
            if blank.any():
                out.loc[blank, "department"] = out.loc[blank, "barcode"].map(dept_map).fillna("")
        if "section" in hist.columns:
            hist["section"] = hist["section"].astype(str).str.strip().replace({"nan": "", "None": "", "<NA>": ""})
            sec_map = hist.groupby("barcode", sort=False)["section"].agg(_first_nonblank)
            blank = out["section"].eq("")
            if blank.any():
                out.loc[blank, "section"] = out.loc[blank, "barcode"].map(sec_map).fillna("")

    # 2) Sales / item barcode → Department / Section map for remaining blanks
    try:
        from pipeline.pipeline_inputs import enrich_sales_dept_section

        out = enrich_sales_dept_section(out, barcode_col="barcode")
    except Exception as exc:
        logger.warning("dept/section enrich from sales barcode map skipped: %s", exc)

    for c in ("department", "section"):
        out[c] = out[c].astype(str).str.strip().replace({"nan": "", "None": "", "<NA>": ""})
    return out


def _norm_channel_source(val: Any) -> str:
    u = str(val or "").strip().upper()
    if not u or u in ("NAN", "NONE", "<NA>", "NULL"):
        return ""
    if u in ("PANTALOONS", "PT"):
        return "PT"
    if u in ("SHOPPERS STOP", "SHOPPERS-STOP", "SHOPPERS_STOP", "SSL", "SS"):
        return "SSL"
    if "CENTRO" in u:
        return "CENTRO"
    if "ICONIC" in u:
        return "ICONIC"
    if u in ("EBO",) or "EBO" in u or "FRESCO" in u:
        return "EBO"
    if u in _VALID_CHANNELS:
        return u
    return u


def _daily_movement_sig(path: Path) -> str:
    if not path.exists():
        return "missing"
    st = path.stat()
    return f"dm:{st.st_mtime_ns}:{st.st_size}"


def _empty_daily_soh_df() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "site_id", "site_name", "barcode", "sku", "size", "qty", "source",
            "date", "zone", "city", "state", "store_grade", "store_category",
            "customer_name", "department", "section",
        ]
    )


def _normalize_daily_soh_slice(latest: pd.DataFrame, dm_history: pd.DataFrame | None) -> pd.DataFrame:
    """Map daily_inventory_movement store×barcode rows → stock-overview schema."""
    from api.historical_movement import site_key_series

    if latest is None or latest.empty:
        return _empty_daily_soh_df()

    latest = latest.copy()
    latest["store"] = latest["store"].astype(str).str.strip().str.upper()
    latest["barcode"] = (
        latest["barcode"].astype(str).str.strip().str.upper().str.replace(r"\.0$", "", regex=True)
    )
    latest["closing_soh"] = pd.to_numeric(latest["closing_soh"], errors="coerce").fillna(0.0)
    latest = latest.dropna(subset=["store", "barcode"])
    latest = latest[latest["store"].ne("") & latest["barcode"].ne("")]

    site_keys = site_key_series(latest["store"]).astype(str).str.strip().str.upper()
    site_keys = site_keys.mask(site_keys.eq(""), latest["store"])

    ch = latest["sales_channel"] if "sales_channel" in latest.columns else pd.Series("", index=latest.index)
    ch = ch.where(ch.astype(str).str.strip().ne(""), latest["channel"] if "channel" in latest.columns else "")
    if "store_type" in latest.columns:
        ch = ch.where(ch.astype(str).str.strip().ne(""), latest["store_type"])

    out = pd.DataFrame({
        "site_id": site_keys.map(_site_id_str),
        "site_name": (
            latest["store_name"].astype(str).str.strip()
            if "store_name" in latest.columns
            else ""
        ),
        "barcode": latest["barcode"].map(_barcode_str),
        "sku": latest["sku"].astype(str).str.strip() if "sku" in latest.columns else "",
        "size": latest["size"].astype(str).str.strip() if "size" in latest.columns else "",
        "qty": latest["closing_soh"],
        "source": ch.map(_norm_channel_source),
        "date": latest["date"] if "date" in latest.columns else pd.NaT,
        "zone": latest["zone"].astype(str).str.strip() if "zone" in latest.columns else "",
        "city": "",
        "state": "",
        "store_grade": "",
        "store_category": "",
        "customer_name": "",
        "department": (
            latest["department"].astype(str).str.strip() if "department" in latest.columns else ""
        ),
        "section": (
            latest["section"].astype(str).str.strip() if "section" in latest.columns else ""
        ),
    })
    for c in (
        "site_id", "site_name", "barcode", "sku", "size", "source", "zone",
        "city", "state", "store_grade", "store_category", "customer_name",
        "department", "section",
    ):
        out[c] = out[c].astype(str).str.strip().replace({"nan": "", "None": "", "<NA>": "", "NaT": ""})
    out["qty"] = pd.to_numeric(out["qty"], errors="coerce").fillna(0.0)
    meta_cols = [c for c in out.columns if c not in ("qty",)]
    out = (
        out.groupby(["site_id", "barcode"], as_index=False, sort=False)
        .agg({
            "qty": "sum",
            **{c: "first" for c in meta_cols if c not in ("site_id", "barcode")},
        })
    )
    out = out[out["site_id"].ne("")].copy()
    out = _apply_site_names(out)
    blank_before = int((out["department"].astype(str).str.strip() == "").sum())
    if blank_before > 0:
        out = _enrich_department_section(out, dm_history=dm_history)
    else:
        out = _enrich_department_section(out, dm_history=None)
    return out.sort_values("qty", ascending=False, kind="mergesort").reset_index(drop=True)


def _read_daily_movement_raw() -> tuple[pd.DataFrame, Path]:
    """Load daily_inventory_movement columns needed for Store SOH."""
    try:
        from api.current_soh import ensure_daily_movement_from_blob

        ensure_daily_movement_from_blob()
    except Exception:
        pass

    path = _API_CACHE_DIR / "daily_inventory_movement.parquet"
    if not path.exists():
        return pd.DataFrame(), path

    want = [
        "date", "store", "barcode", "closing_soh", "sku", "size", "season",
        "department", "section", "store_name", "zone", "sales_channel", "channel",
        "store_type",
    ]
    try:
        import pyarrow.parquet as pq
        available = set(pq.ParquetFile(str(path)).schema_arrow.names)
    except Exception:
        available = set(pd.read_parquet(path).head(0).columns)

    use_cols = [c for c in want if c in available]
    if "store" not in use_cols or "barcode" not in use_cols or "closing_soh" not in use_cols:
        raise RuntimeError("daily_inventory_movement missing store/barcode/closing_soh")

    dm = pd.read_parquet(path, columns=use_cols)
    if "date" in dm.columns:
        dm["date"] = pd.to_datetime(dm["date"], errors="coerce")
    else:
        dm["date"] = pd.NaT
    return dm, path


def _get_daily_movement_soh_as_of(as_of: str, force_refresh: bool = False) -> pd.DataFrame:
    """
    Closing SOH as of a specific calendar day: last ledger row on/before as_of
    per store × barcode (not a date-range filter on the latest snapshot).
    """
    global _CACHE_SOURCE_LABEL
    day = str(as_of or "").strip()[:10]
    if not day:
        return _get_daily_movement_soh_df(force_refresh=force_refresh)

    now = time.time()
    with _ASOF_SOH_LOCK:
        hit = _ASOF_SOH_CACHE.get(day)
        if (
            not force_refresh
            and hit
            and (now - hit[0]) < _ASOF_SOH_TTL
            and hit[1] is not None
            and not hit[1].empty
        ):
            _CACHE_SOURCE_LABEL = f"daily_inventory_movement@as_of={day}"
            return hit[1]

    try:
        from api.current_soh import ensure_daily_movement_from_blob

        ensure_daily_movement_from_blob()
    except Exception:
        pass

    path = _API_CACHE_DIR / "daily_inventory_movement.parquet"
    sig = _daily_movement_sig(path)
    disk_pq = _API_CACHE_DIR / f"stock_overview_asof_{day}_v1.parquet"
    disk_key = _API_CACHE_DIR / f"stock_overview_asof_{day}_v1.key"
    cache_token = f"{sig}|{day}"

    if (
        not force_refresh
        and disk_pq.exists()
        and disk_key.exists()
        and disk_key.read_text(encoding="utf-8").strip() == cache_token
    ):
        try:
            cached = pd.read_parquet(disk_pq)
            if cached is not None and not cached.empty and "qty" in cached.columns:
                with _ASOF_SOH_LOCK:
                    _ASOF_SOH_CACHE[day] = (now, cached)
                _CACHE_SOURCE_LABEL = f"daily_inventory_movement@as_of={day}"
                logger.info(
                    "Stock overview as-of %s disk cache hit (%d rows, soh=%.0f)",
                    day,
                    len(cached),
                    float(pd.to_numeric(cached["qty"], errors="coerce").fillna(0).sum()),
                )
                return cached
        except Exception as exc:
            logger.warning("Stock overview as-of disk cache read failed: %s", exc)

    try:
        t0 = time.perf_counter()
        dm, _path = _read_daily_movement_raw()
        if dm.empty or "date" not in dm.columns:
            return _empty_daily_soh_df()
        # inclusive calendar day: date < next midnight
        cutoff_next = pd.Timestamp(day).normalize() + pd.Timedelta(days=1)
        sliced = dm[dm["date"].notna() & (dm["date"] < cutoff_next)]
        if sliced.empty:
            logger.warning("Store SOH as-of %s: no movement rows on/before that date", day)
            out = _empty_daily_soh_df()
        else:
            latest = (
                sliced.sort_values("date", kind="mergesort")
                .groupby(["store", "barcode"], sort=False, as_index=False)
                .tail(1)
                .copy()
            )
            # Skip full-history dept scan — as-of slice can be huge; latest-row dims are enough.
            out = _normalize_daily_soh_slice(latest, dm_history=None)
        try:
            _API_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            out.to_parquet(disk_pq, index=False)
            disk_key.write_text(cache_token, encoding="utf-8")
        except Exception as save_exc:
            logger.warning("Stock overview as-of disk cache write failed: %s", save_exc)
        with _ASOF_SOH_LOCK:
            _ASOF_SOH_CACHE[day] = (now, out)
            if len(_ASOF_SOH_CACHE) > 14:
                oldest = sorted(_ASOF_SOH_CACHE.items(), key=lambda kv: kv[1][0])[:5]
                for k, _ in oldest:
                    _ASOF_SOH_CACHE.pop(k, None)
        _CACHE_SOURCE_LABEL = f"daily_inventory_movement@as_of={day}"
        logger.info(
            "Stock overview as-of %s: rows=%d total_soh=%.0f stores=%d in %.1fs",
            day,
            len(out),
            float(pd.to_numeric(out["qty"], errors="coerce").fillna(0).sum()) if not out.empty else 0.0,
            int(out["site_id"].nunique()) if not out.empty else 0,
            time.perf_counter() - t0,
        )
        return out
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed building Store Inventory SOH as-of %s: %s", day, exc)
        raise HTTPException(
            status_code=500,
            detail=f"daily_inventory_movement SOH as-of load failed: {exc}",
        ) from exc


def _get_daily_movement_soh_df(force_refresh: bool = False) -> pd.DataFrame:
    """
    Latest closing_soh per store × barcode from daily_inventory_movement.parquet.
    Same Current SOH source as Sales Overview / api.current_soh.
    Disk-cached so Inventory Analytics Store tab is not a ~2min rebuild every time.
    """
    global _DAILY_SOH_DF, _DAILY_SOH_AT, _DAILY_SOH_SIG, _CACHE_SOURCE_LABEL

    now = time.time()
    try:
        from api.current_soh import ensure_daily_movement_from_blob

        ensure_daily_movement_from_blob()
    except Exception:
        pass

    path = _API_CACHE_DIR / "daily_inventory_movement.parquet"
    sig = _daily_movement_sig(path)

    if (
        not force_refresh
        and _DAILY_SOH_DF is not None
        and _DAILY_SOH_SIG == sig
        and (now - _DAILY_SOH_AT) < _DAILY_SOH_TTL
        and not _DAILY_SOH_DF.empty
    ):
        return _DAILY_SOH_DF

    disk_pq = _API_CACHE_DIR / _DAILY_SOH_DISK
    disk_key = _API_CACHE_DIR / _DAILY_SOH_DISK_KEY
    if (
        not force_refresh
        and disk_pq.exists()
        and disk_key.exists()
        and disk_key.read_text(encoding="utf-8").strip() == sig
    ):
        try:
            cached = pd.read_parquet(disk_pq)
            if cached is not None and not cached.empty and "qty" in cached.columns:
                # Older disk caches may be unsorted — normalize once on load.
                cached = cached.sort_values("qty", ascending=False, kind="mergesort").reset_index(drop=True)
                _DAILY_SOH_DF = cached
                _DAILY_SOH_AT = now
                _DAILY_SOH_SIG = sig
                _CACHE_SOURCE_LABEL = "daily_inventory_movement"
                logger.info(
                    "Stock overview daily SOH disk cache hit (%d rows, soh=%.0f)",
                    len(cached),
                    float(pd.to_numeric(cached["qty"], errors="coerce").fillna(0).sum()),
                )
                return cached
        except Exception as exc:
            logger.warning("Stock overview daily SOH disk cache read failed: %s", exc)

    if not path.exists():
        logger.warning("daily_inventory_movement.parquet missing — Store Inventory SOH empty")
        empty = _empty_daily_soh_df()
        _DAILY_SOH_DF = empty
        _DAILY_SOH_AT = now
        _DAILY_SOH_SIG = sig
        _CACHE_SOURCE_LABEL = "daily_inventory_movement"
        return empty

    try:
        t0 = time.perf_counter()
        dm, _p = _read_daily_movement_raw()
        if dm.empty:
            out = _empty_daily_soh_df()
        elif "date" in dm.columns and dm["date"].notna().any():
            latest = (
                dm.sort_values("date", kind="mergesort")
                .groupby(["store", "barcode"], sort=False, as_index=False)
                .tail(1)
                .copy()
            )
            out = _normalize_daily_soh_slice(latest, dm_history=dm)
        else:
            latest = dm.groupby(["store", "barcode"], sort=False).tail(1).copy()
            out = _normalize_daily_soh_slice(latest, dm_history=None)

        try:
            _API_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            out.to_parquet(disk_pq, index=False)
            disk_key.write_text(sig, encoding="utf-8")
        except Exception as save_exc:
            logger.warning("Stock overview daily SOH disk cache write failed: %s", save_exc)

        _DAILY_SOH_DF = out
        _DAILY_SOH_AT = now
        _DAILY_SOH_SIG = sig
        _CACHE_SOURCE_LABEL = "daily_inventory_movement"
        with _AGG_CACHE_LOCK:
            _AGG_CACHE.clear()
        blank_dept = int((out["department"].astype(str).str.strip() == "").sum()) if not out.empty else 0
        logger.info(
            "Stock overview from daily_inventory_movement: rows=%d total_soh=%.0f stores=%d blank_dept=%d in %.1fs",
            len(out),
            float(out["qty"].sum()) if not out.empty else 0.0,
            out["site_id"].nunique() if not out.empty else 0,
            blank_dept,
            time.perf_counter() - t0,
        )
        return out
    except Exception as exc:
        logger.exception("Failed building Store Inventory SOH from daily movement: %s", exc)
        raise HTTPException(
            status_code=500,
            detail=f"daily_inventory_movement SOH load failed: {exc}",
        ) from exc


def _get_soh_df(force_refresh: bool = False, prefer: str | None = None) -> pd.DataFrame:
    """
    Always re-check Azure blob ETag so dashboard shows the current blob file.
    Re-download only when the blob changed; otherwise reuse memory/parquet.
    Falls back to SOH-Master when COMBINED_SOH is missing (prefer=auto).
    """
    global _CACHE_DF, _CACHE_KEY, _CACHE_AT, _CACHE_SOURCE_LABEL

    with _SOH_LOAD_LOCK:
        path, cache_key, source_label = _resolve_soh_path(prefer)
        now = time.time()
        if (
            not force_refresh
            and _CACHE_DF is not None
            and cache_key is not None
            and _CACHE_KEY == cache_key
        ):
            return _CACHE_DF

        df, cache_key, source_label = _load_soh_frame(
            force_refresh=force_refresh,
            path=path,
            cache_key=cache_key,
            prefer=prefer,
        )
        _CACHE_DF = _apply_site_names(df)
        _CACHE_KEY = cache_key or f"local:{len(_CACHE_DF)}"
        _CACHE_SOURCE_LABEL = source_label
        _CACHE_AT = now
        logger.info(
            "Stock overview cache refreshed (%s rows, source=%s)",
            len(_CACHE_DF),
            source_label,
        )
        return _CACHE_DF


def _clean_str(val: Any) -> str:
    if isinstance(val, str):
        return val.strip()
    if val is None:
        return ""
    d = getattr(val, "default", "")
    if isinstance(d, str):
        return d.strip()
    return ""


def _site_id_str(val: Any) -> str:
    s = str(val).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s


def _barcode_str(val: Any) -> str:
    s = str(val).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s



def _apply_filters(
    df: pd.DataFrame,
    *,
    site_id: str | Any = None,
    site_ids: str | Any = None,
    source: str | Any = None,
    search: str | Any = None,
    date_from: str | Any = None,
    date_to: str | Any = None,
    departments: str | Any = None,
    sections: str | Any = None,
    skus: str | Any = None,
    barcodes: str | Any = None,
    sizes: str | Any = None,
    exclude: frozenset[str] | None = None,
) -> pd.DataFrame:
    skip = exclude or frozenset()
    out = df
    s_site_id = _clean_str(site_id)
    s_site_ids = _clean_str(site_ids)
    s_source = _clean_str(source)
    s_search = _clean_str(search)
    s_date_from = _clean_str(date_from)
    s_date_to = _clean_str(date_to)
    s_departments = _clean_str(departments)
    s_sections = _clean_str(sections)
    s_skus = _clean_str(skus)
    s_barcodes = _clean_str(barcodes)
    s_sizes = _clean_str(sizes)

    if "site_id" not in skip and s_site_ids:
        if s_site_ids == "__none__":
            return out.iloc[0:0].copy()
        allowed = {_site_id_str(x) for x in s_site_ids.split(",") if x.strip()}
        if allowed:
            out = out[out["site_id"].astype(str).str.strip().isin(allowed)]
    elif "site_id" not in skip and s_site_id and s_site_id.lower() not in ("", "all"):
        sid = _site_id_str(s_site_id)
        out = out[out["site_id"].astype(str).str.strip() == sid]

    if "source" not in skip and s_source and s_source.lower() not in ("", "all"):
        ch = s_source.upper()
        # Accept UI ids like pantaloons / shoppers-stop as well as PT / SSL
        alias = {
            "PANTALOONS": "PT",
            "SHOPPERS-STOP": "SSL",
            "SHOPPERS_STOP": "SSL",
            "CENTRO": "CENTRO",
            "ICONIC": "ICONIC",
            "EBO": "EBO",
            "PT": "PT",
            "SSL": "SSL",
        }
        ch = alias.get(ch, ch)
        out = out[out["source"].astype(str).str.strip().str.upper() == ch]

    if "date" not in skip and "date" in out.columns and out["date"].notna().any():
        day = out["date"].dt.normalize()
        if s_date_from:
            out = out[day >= pd.Timestamp(s_date_from).normalize()]
            day = out["date"].dt.normalize()
        if s_date_to:
            out = out[day <= pd.Timestamp(s_date_to).normalize()]

    if "departments" not in skip and s_departments:
        if s_departments == "__none__":
            return out.iloc[0:0].copy()
        allowed = {x.strip() for x in s_departments.split(",") if x.strip()}
        if allowed and "department" in out.columns:
            out = out[out["department"].astype(str).str.strip().isin(allowed)]

    if "sections" not in skip and s_sections:
        if s_sections == "__none__":
            return out.iloc[0:0].copy()
        allowed = {x.strip() for x in s_sections.split(",") if x.strip()}
        if allowed and "section" in out.columns:
            out = out[out["section"].astype(str).str.strip().isin(allowed)]

    if "skus" not in skip and s_skus:
        if s_skus == "__none__":
            return out.iloc[0:0].copy()
        allowed = {x.strip() for x in s_skus.split(",") if x.strip()}
        if allowed and "sku" in out.columns:
            out = out[out["sku"].astype(str).str.strip().isin(allowed)]

    if "barcodes" not in skip and s_barcodes:
        if s_barcodes == "__none__":
            return out.iloc[0:0].copy()
        allowed = {_barcode_str(x) for x in s_barcodes.split(",") if x.strip()}
        if allowed and "barcode" in out.columns:
            out = out[out["barcode"].astype(str).str.strip().isin(allowed)]

    if "sizes" not in skip and s_sizes:
        if s_sizes == "__none__":
            return out.iloc[0:0].copy()
        allowed = {x.strip().upper() for x in s_sizes.split(",") if x.strip()}
        if allowed and "size" in out.columns:
            out = out[out["size"].astype(str).str.strip().str.upper().isin(allowed)]

    if "search" not in skip and s_search:
        q = s_search.lower()
        mask = (
            out["site_id"].str.lower().str.contains(q, na=False)
            | out["site_name"].str.lower().str.contains(q, na=False)
            | out["barcode"].str.lower().str.contains(q, na=False)
            | out["sku"].str.lower().str.contains(q, na=False)
            | out["source"].str.lower().str.contains(q, na=False)
        )
        if "zone" in out.columns:
            mask = mask | out["zone"].astype(str).str.lower().str.contains(q, na=False)
        if "city" in out.columns:
            mask = mask | out["city"].astype(str).str.lower().str.contains(q, na=False)
        out = out[mask]

    return out


def _insights(df: pd.DataFrame, *, limit: int = 10) -> dict[str, list[dict[str, Any]]]:
    if df.empty:
        return {"top_stores": [], "top_skus": []}

    by_store = (
        df.groupby(["site_id", "site_name"], as_index=False)["qty"]
        .sum()
        .sort_values("qty", ascending=False)
        .head(limit)
    )
    sku_key = df["sku"].where(df["sku"].astype(str).str.strip().ne(""), df["barcode"])
    tmp = df.copy()
    tmp["_sku_key"] = sku_key.astype(str).str.strip()
    by_sku = (
        tmp.groupby("_sku_key", as_index=False)["qty"]
        .sum()
        .sort_values("qty", ascending=False)
        .head(limit)
    )
    return {
        "top_stores": [
            {
                "site_id": str(r["site_id"]),
                "site_name": str(r["site_name"]).strip(),
                "qty": int(r["qty"]),
            }
            for _, r in by_store.iterrows()
        ],
        "top_skus": [
            {"sku": str(r["_sku_key"]), "qty": int(r["qty"])}
            for _, r in by_sku.iterrows()
        ],
    }


def _summary(df: pd.DataFrame) -> dict[str, int]:
    if df.empty:
        return {"total_soh": 0, "store_count": 0, "sku_count": 0, "barcode_count": 0}
    sku_key = df["sku"].where(df["sku"].astype(str).str.strip().ne(""), df["barcode"])
    if "barcode" in df.columns:
        bc = df["barcode"].astype(str).str.strip()
        barcode_count = int(bc[(bc != "") & (bc.str.lower() != "nan")].nunique())
    else:
        barcode_count = 0
    return {
        "total_soh": int(df["qty"].sum()),
        "store_count": int(df["site_id"].nunique()),
        "sku_count": int(sku_key.astype(str).str.strip().nunique()),
        "barcode_count": barcode_count,
    }


_SITE_MDQ_CACHE: dict[str, float] | None = None
# Cached store/dept aggregates for a given filter signature (avoids re-groupby on 400k rows).
_AGG_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_AGG_CACHE_TTL = float(os.environ.get("STOCK_OVERVIEW_AGG_TTL_SECONDS", "120"))
_AGG_CACHE_LOCK = threading.Lock()


def _get_site_mdq_map() -> dict[str, float]:
    global _SITE_MDQ_CACHE
    if _SITE_MDQ_CACHE is not None:
        return _SITE_MDQ_CACHE
    try:
        from pipeline.customer_master import load_customer_master
        cm = load_customer_master(retail_only=False)
        if not cm.empty and "mdq" in cm.columns:
            valid = cm[cm["mdq"].notna()]
            res = {}
            for _, r in valid.iterrows():
                key = str(r["_site_key"]).strip().upper()
                if key:
                    res[key] = float(r["mdq"] or 0.0)
            _SITE_MDQ_CACHE = res
            return res
    except Exception as exc:
        logger.warning("Could not load MDQ map: %s", exc)
    return {}


def _store_summaries(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df.empty:
        return []
    total_qty = float(df["qty"].sum()) or 1.0
    cols = ["site_id", "site_name"]
    d_copy = df.copy()
    for c in ["department", "section", "sku", "barcode"]:
        if c not in d_copy.columns:
            d_copy[c] = ""
    grouped = d_copy.groupby(cols, as_index=False).agg(
        soh=("qty", "sum"),
        dept_count=("department", "nunique"),
        sec_count=("section", "nunique"),
        sku_count=("sku", "nunique"),
        barcode_count=("barcode", "nunique"),
    )
    grouped["pct_of_total"] = (grouped["soh"] / total_qty) * 100
    grouped = grouped.sort_values("soh", ascending=False)
    
    mdq_map = _get_site_mdq_map()
    
    result = []
    for _, r in grouped.iterrows():
        site_id_s = str(r["site_id"]).strip()
        site_key = site_id_s.upper()
        mdq_target = mdq_map.get(site_key, 0.0)
        soh_val = int(r["soh"])
        mdq_fill_pct = round((float(soh_val) / mdq_target) * 100, 1) if mdq_target > 0 else None

        result.append({
            "siteId": site_id_s,
            "siteName": str(r["site_name"]).strip() or site_id_s,
            "soh": soh_val,
            "mdq": int(mdq_target) if mdq_target > 0 else None,
            "mdqFillPct": mdq_fill_pct,
            "pctOfTotal": round(float(r["pct_of_total"]), 2),
            "deptCount": int(r["dept_count"]),
            "secCount": int(r["sec_count"]),
            "skuCount": int(r["sku_count"]),
            "barcodeCount": int(r["barcode_count"]),
        })
    return result


def _dept_summaries(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df.empty:
        return []
    d_copy = df.copy()
    for c in ["department", "section", "site_id", "sku", "barcode"]:
        if c not in d_copy.columns:
            d_copy[c] = ""
    grouped = d_copy.groupby(["department", "section"], as_index=False).agg(
        current_soh=("qty", "sum"),
        store_count=("site_id", "nunique"),
        sku_count=("sku", "nunique"),
        barcode_count=("barcode", "nunique"),
    )
    grouped = grouped.sort_values("current_soh", ascending=False)
    return [
        {
            "department": str(r["department"]).strip() or "General",
            "section": str(r["section"]).strip() or "General",
            "storeCount": int(r["store_count"]),
            "skuCount": int(r["sku_count"]),
            "barcodeCount": int(r["barcode_count"]),
            "currentSoh": int(r["current_soh"]),
        }
        for _, r in grouped.iterrows()
    ]


def _resolve_as_of_day(as_of: str = "", date_from: str = "", date_to: str = "") -> str:
    """Prefer explicit as_of; else single-day legacy date_from/date_to → as-of day."""
    day = str(as_of or "").strip()[:10]
    if day:
        return day
    df = str(date_from or "").strip()[:10]
    dt = str(date_to or "").strip()[:10]
    if df and dt and df == dt:
        return df
    if dt and not df:
        return dt
    if df and not dt:
        return df
    return ""


@router.get("")
def get_stock_overview(
    site_id: str = Query("all", description="Site_id filter or all"),
    site_ids: str = Query("", description="Comma-separated site IDs (Customer Master filters)"),
    source: str = Query("all", description="Source channel or all"),
    search: str = Query("", description="Search site, barcode, SKU"),
    as_of: str = Query("", description="Closing SOH as of YYYY-MM-DD (daily movement)"),
    date_from: str = Query("", description="Legacy; if same as date_to, treated as as_of"),
    date_to: str = Query("", description="Legacy; if alone or same as date_from, treated as as_of"),
    departments: str = Query("", description="Comma-separated departments (Item Master)"),
    sections: str = Query("", description="Comma-separated sections (Item Master)"),
    skus: str = Query("", description="Comma-separated SKUs"),
    barcodes: str = Query("", description="Comma-separated barcodes/EANs"),
    sizes: str = Query("", description="Comma-separated sizes"),
    soh_source: str = Query(
        "daily",
        description="Current SOH source: daily (daily_inventory_movement) | legacy combined/soh-master/auto",
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    refresh: int = Query(0, description="1 = bypass cache"),
):
    _page = int(getattr(page, "default", 1)) if not isinstance(page, (int, str)) else int(page)
    _page_size = int(getattr(page_size, "default", 50)) if not isinstance(page_size, (int, str)) else int(page_size)
    _refresh = int(getattr(refresh, "default", 0)) if not isinstance(refresh, (int, str)) else int(refresh)
    _soh_src = str(soh_source or "daily").strip().lower()
    as_of_day = _resolve_as_of_day(as_of, date_from, date_to)

    # Full JSON response cache — Inventory Analytics first paint must not re-aggregate every open
    page_key = (
        f"v1|{_soh_src}|{_page}|{_page_size}|{site_id}|{site_ids}|{source}|{search}|"
        f"{as_of_day or ''}|{date_from}|{date_to}|{departments}|{sections}|{skus}|{barcodes}|{sizes}"
    )
    if not _refresh:
        try:
            from api.page_response_cache import load_page_cache, rebuilding_page_cache

            if not rebuilding_page_cache():
                cached_page = load_page_cache("stock-overview", page_key)
                if isinstance(cached_page, dict) and cached_page.get("ok") and "rows" in cached_page:
                    return cached_page
        except Exception as exc:
            logger.debug("Stock overview page_cache load skipped: %s", exc)

    try:
        # Default / Store Inventory: daily movement closing_soh (never COMBINED_SOH)
        if _soh_src in ("", "daily", "movement", "daily_inventory_movement"):
            if as_of_day:
                full = _get_daily_movement_soh_as_of(as_of_day, force_refresh=bool(_refresh))
            else:
                full = _get_daily_movement_soh_df(force_refresh=bool(_refresh))
        else:
            full = _apply_site_names(
                _get_soh_df(force_refresh=bool(_refresh), prefer=soh_source)
            )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Stock overview load failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    # as-of already rebuilt SOH; do not also slice the snapshot by date_from/date_to
    filt_from = "" if as_of_day else date_from
    filt_to = "" if as_of_day else date_to
    filter_kw = dict(
        site_id=site_id,
        site_ids=site_ids,
        source=source,
        search=search,
        date_from=filt_from,
        date_to=filt_to,
        departments=departments,
        sections=sections,
        skus=skus,
        barcodes=barcodes,
        sizes=sizes,
    )
    filtered = _apply_filters(full, **filter_kw)
    # Daily SOH DF is pre-sorted by qty desc; only re-sort when filters may disorder it.
    needs_sort = any(
        str(v or "").strip() not in ("", "all")
        for v in (
            site_id,
            site_ids,
            source,
            search,
            as_of_day,
            filt_from,
            filt_to,
            departments,
            sections,
            skus,
            barcodes,
            sizes,
        )
    )
    if needs_sort:
        filtered = filtered.sort_values("qty", ascending=False, kind="mergesort")
    # Avoid reset_index on ~400k rows — iloc works on any index.
    total = len(filtered)
    start = (_page - 1) * _page_size
    page_df = filtered.iloc[start : start + _page_size]

    # Always expose the 5 store channels (even if a channel has 0 SOH rows)
    source_options = list(CHANNEL_OPTIONS)

    agg_key = "|".join(
        [
            str(_soh_src),
            str(site_id or ""),
            str(site_ids or ""),
            str(source or ""),
            str(search or ""),
            str(as_of_day or ""),
            str(filt_from or ""),
            str(filt_to or ""),
            str(departments or ""),
            str(sections or ""),
            str(skus or ""),
            str(barcodes or ""),
            str(sizes or ""),
            str(_DAILY_SOH_SIG or _CACHE_KEY or ""),
        ]
    )
    now_ts = time.time()
    cached_agg: dict[str, Any] | None = None
    with _AGG_CACHE_LOCK:
        hit = _AGG_CACHE.get(agg_key)
        if hit and (now_ts - hit[0]) < _AGG_CACHE_TTL:
            cached_agg = hit[1]

    if cached_agg is not None:
        store_options = cached_agg["stores"]
        summary = cached_agg["summary"]
        store_summaries = cached_agg["store_summaries"]
        dept_summaries = cached_agg["dept_summaries"]
        insights = cached_agg["insights"]
    else:
        # Store dropdown: derive from store_summaries (already grouped) — skip second full groupby.
        store_summaries = _store_summaries(filtered)
        dept_summaries = _dept_summaries(filtered)
        summary = _summary(filtered)
        insights = _insights(filtered)
        store_options = [
            {"id": "all", "label": "All stores"},
            *[
                {
                    "id": str(s["siteId"]),
                    "label": (
                        f"{s['siteId']} — {s['siteName']}".strip(" —")
                        if str(s.get("siteName") or "").strip()
                        else str(s["siteId"])
                    ),
                }
                for s in store_summaries
                if str(s.get("siteId") or "").strip()
            ],
        ][:500]
        with _AGG_CACHE_LOCK:
            _AGG_CACHE[agg_key] = (
                now_ts,
                {
                    "stores": store_options,
                    "summary": summary,
                    "store_summaries": store_summaries,
                    "dept_summaries": dept_summaries,
                    "insights": insights,
                },
            )

    def _cell_val(v: Any) -> str:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return ""
        return str(v).strip()

    rows = []
    records = page_df.to_dict("records")
    for i, r in enumerate(records):
        dval = r.get("date")
        date_s = ""
        if dval is not None and not (isinstance(dval, float) and pd.isna(dval)):
            try:
                date_s = pd.Timestamp(dval).strftime("%Y-%m-%d")
            except Exception:
                date_s = str(dval)[:10]
        sid = _cell_val(r.get("site_id"))
        bc = _cell_val(r.get("barcode"))
        rows.append(
            {
                "id": f"{sid}|{bc}|{start + i}",
                "site_id": sid,
                "site_name": _cell_val(r.get("site_name")),
                "barcode": bc,
                "sku": _cell_val(r.get("sku")),
                "size": _cell_val(r.get("size")),
                "qty": int(pd.to_numeric(r.get("qty"), errors="coerce") or 0),
                "source": _cell_val(r.get("source")),
                "date": date_s,
                "zone": _cell_val(r.get("zone")),
                "city": _cell_val(r.get("city")),
                "state": _cell_val(r.get("state")),
                "store_grade": _cell_val(r.get("store_grade")),
                "store_category": _cell_val(r.get("store_category")),
                "customer_name": _cell_val(r.get("customer_name")),
                "department": _cell_val(r.get("department")),
                "section": _cell_val(r.get("section")),
            }
        )

    payload = {
        "ok": True,
        "filters": {
            "site_id": site_id,
            "site_ids": site_ids,
            "source": source,
            "search": search,
            "as_of": as_of_day,
            "date_from": date_from,
            "date_to": date_to,
            "departments": departments,
            "sections": sections,
            "skus": skus,
            "barcodes": barcodes,
            "sizes": sizes,
            "soh_source": soh_source,
        },
        "soh_file_source": _CACHE_SOURCE_LABEL,
        "stores": store_options,
        "sources": source_options,
        "channels": source_options,
        # Full unique barcode/sku lists are unused by Inventory UI and were ~30s on 400k rows.
        "barcodes": [],
        "skus": [],
        "summary": summary,
        "store_summaries": store_summaries,
        "dept_summaries": dept_summaries,
        "insights": insights,
        "total": total,
        "page": _page,
        "page_size": _page_size,
        "rows": rows,
    }
    try:
        from api.page_response_cache import save_page_cache

        save_page_cache("stock-overview", page_key, payload)
    except Exception as exc:
        logger.debug("Stock overview page_cache save skipped: %s", exc)
    return payload


@router.get("/export.csv")
def export_stock_overview_csv(
    site_id: str = Query("all"),
    site_ids: str = Query(""),
    source: str = Query("all"),
    search: str = Query(""),
    as_of: str = Query(""),
    date_from: str = Query(""),
    date_to: str = Query(""),
    departments: str = Query(""),
    sections: str = Query(""),
    skus: str = Query(""),
    barcodes: str = Query(""),
    sizes: str = Query(""),
    soh_source: str = Query("daily"),
    refresh: int = Query(0),
    positive_only: int = Query(0, description="1 = only rows with Current SOH > 0"),
):
    """Full Store×Barcode×SKU Current SOH CSV (all rows, not page-limited)."""
    import csv
    import io
    from datetime import date as _date

    _refresh = int(getattr(refresh, "default", 0)) if not isinstance(refresh, (int, str)) else int(refresh)
    _soh_src = str(soh_source or "daily").strip().lower()
    as_of_day = _resolve_as_of_day(as_of, date_from, date_to)
    try:
        if _soh_src in ("", "daily", "movement", "daily_inventory_movement"):
            if as_of_day:
                full = _get_daily_movement_soh_as_of(as_of_day, force_refresh=bool(_refresh))
            else:
                full = _get_daily_movement_soh_df(force_refresh=bool(_refresh))
        else:
            full = _apply_site_names(_get_soh_df(force_refresh=bool(_refresh), prefer=soh_source))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    filt_from = "" if as_of_day else date_from
    filt_to = "" if as_of_day else date_to
    filtered = _apply_filters(
        full,
        site_id=site_id,
        site_ids=site_ids,
        source=source,
        search=search,
        date_from=filt_from,
        date_to=filt_to,
        departments=departments,
        sections=sections,
        skus=skus,
        barcodes=barcodes,
        sizes=sizes,
    )
    if int(positive_only or 0) == 1:
        filtered = filtered[pd.to_numeric(filtered["qty"], errors="coerce").fillna(0) > 0]
    filtered = filtered.sort_values("qty", ascending=False).reset_index(drop=True)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "Store Code", "Store Name", "Barcode", "SKU", "Size",
        "Department", "Section", "Channel", "Zone", "Current SOH", "As Of Date",
    ])
    for _, r in filtered.iterrows():
        dval = r.get("date")
        date_s = ""
        if dval is not None and not (isinstance(dval, float) and pd.isna(dval)):
            try:
                date_s = pd.Timestamp(dval).strftime("%Y-%m-%d")
            except Exception:
                date_s = str(dval)[:10]
        writer.writerow([
            str(r.get("site_id", "")).strip(),
            str(r.get("site_name", "")).strip(),
            str(r.get("barcode", "")).strip(),
            str(r.get("sku", "")).strip(),
            str(r.get("size", "")).strip(),
            str(r.get("department", "")).strip(),
            str(r.get("section", "")).strip(),
            str(r.get("source", "")).strip(),
            str(r.get("zone", "")).strip(),
            int(float(r.get("qty") or 0)),
            date_s,
        ])

    fname = f"store_barcode_sku_soh_{_date.today().isoformat()}.csv"
    payload = buf.getvalue().encode("utf-8-sig")
    return StreamingResponse(
        iter([payload]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.post("/refresh-cache")
def refresh_stock_cache(
    soh_source: str = Query("daily", description="daily | auto | combined | soh-master"),
):
    global _CACHE_DF, _CACHE_KEY, _CACHE_AT, _CACHE_SOURCE_LABEL
    global _DAILY_SOH_DF, _DAILY_SOH_AT
    global _SITE_NAME_LOOKUP, _SITE_NAME_LOOKUP_FAILED, _SITE_NAME_CACHE_KEY
    _CACHE_DF = None
    _CACHE_KEY = None
    _CACHE_AT = 0.0
    _DAILY_SOH_DF = None
    _DAILY_SOH_AT = 0.0
    _SITE_NAME_LOOKUP = None
    _SITE_NAME_LOOKUP_FAILED = False
    _SITE_NAME_CACHE_KEY = None
    src = str(soh_source or "daily").strip().lower()
    if src in ("", "daily", "movement", "daily_inventory_movement"):
        df = _get_daily_movement_soh_df(force_refresh=True)
    else:
        _CACHE_SOURCE_LABEL = "combined"
        df = _get_soh_df(force_refresh=True, prefer=soh_source)
    return {
        "ok": True,
        "rows": len(df),
        "soh_file_source": _CACHE_SOURCE_LABEL,
        "total_soh": int(df["qty"].sum()) if not df.empty and "qty" in df.columns else 0,
    }


def warm_stock_overview_cache(*, parquet_only: bool = False) -> None:
    """Pre-load daily-movement Current SOH + default Inventory JSON page_cache."""
    logger.info("Stock overview cache warm-up started (parquet_only=%s)", parquet_only)
    t0 = time.time()
    try:
        # Prefer disk cache on startup — force rebuild only when missing/stale
        df = _get_daily_movement_soh_df(force_refresh=False)
        logger.info(
            "Stock overview SOH frame warm in %.1fs (daily rows=%d total_soh=%.0f)",
            time.time() - t0,
            len(df),
            float(df["qty"].sum()) if not df.empty else 0.0,
        )
        # Inventory Analytics opens page_size=500 — persist that JSON so first paint is cache-hit
        try:
            from api.page_response_cache import _REBUILD

            token = _REBUILD.set(True)
            try:
                page = get_stock_overview(
                    site_id="all",
                    site_ids="",
                    source="all",
                    search="",
                    as_of="",
                    date_from="",
                    date_to="",
                    departments="",
                    sections="",
                    skus="",
                    barcodes="",
                    sizes="",
                    soh_source="daily",
                    page=1,
                    page_size=500,
                    refresh=1,
                )
                logger.info(
                    "Stock overview page_cache warm ok=%s rows=%s total=%s",
                    page.get("ok"),
                    len(page.get("rows") or []),
                    page.get("total"),
                )
            finally:
                _REBUILD.reset(token)
        except Exception as page_exc:
            logger.warning("Stock overview page_cache warm failed: %s", page_exc)
        logger.info("Stock overview cache warm-up done in %.1fs", time.time() - t0)
        return
    except Exception as exc:
        logger.warning("Daily SOH warm-up failed (%s); falling back to legacy SOH file", exc)
    if parquet_only:
        try:
            path, cache_key, _label = _resolve_soh_path(None)
            if cache_key:
                cached = _load_parquet_cache(cache_key)
                if cached is not None and not cached.empty:
                    global _CACHE_DF, _CACHE_KEY, _CACHE_AT, _CACHE_SOURCE_LABEL
                    _CACHE_DF = cached
                    _CACHE_KEY = cache_key
                    _CACHE_AT = time.time()
                    _CACHE_SOURCE_LABEL = _label
                    _get_site_name_lookup()
                    logger.info(
                        "Stock overview warm from parquet only (%s rows, %.1fs)",
                        len(cached),
                        time.time() - t0,
                    )
                    return
        except Exception as exc:
            logger.warning("Stock overview parquet-only warm failed: %s", exc)
        logger.info("Stock overview parquet-only warm skipped (no disk cache yet)")
        return
    _get_soh_df(force_refresh=False)
    _get_site_name_lookup()
    logger.info("Stock overview cache warm-up done in %.1fs", time.time() - t0)


@router.get("/summary")
def get_stock_overview_summary(
    soh_source: str = Query(
        "daily",
        description="daily (daily_inventory_movement) | auto | combined | soh-master",
    ),
    refresh: int = Query(0, description="1 = bypass cache"),
):
    """Lightweight KPIs only — Current SOH from daily movement by default."""
    try:
        src = str(soh_source or "daily").strip().lower()
        if src in ("", "daily", "movement", "daily_inventory_movement"):
            full = _get_daily_movement_soh_df(force_refresh=bool(refresh))
        else:
            full = _apply_site_names(
                _get_soh_df(force_refresh=bool(refresh), prefer=soh_source)
            )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Stock overview summary failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {
        "ok": True,
        "summary": _summary(full),
        "soh_file_source": _CACHE_SOURCE_LABEL,
    }


@router.get("/reconciliation")
def get_soh_reconciliation(
    site_id: str = Query("all", description="Site_id filter or all"),
    source: str = Query("all", description="Source channel or all"),
    search: str = Query("", description="Search site, barcode, SKU"),
    departments: str = Query("", description="Comma-separated departments"),
    sections: str = Query("", description="Comma-separated sections"),
    discrepancy_only: int = Query(0, description="1 = show only items with calculated != actual"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
):
    """
    SOH Reconciliation Endpoint:
    Compares System Calculated Closing SOH (from daily_inventory_movement.parquet)
    against Store-reported Actual SOH (from COMBINED_SOH / COMBINED_SOH_MASTER).
    """
    _site_id = str(getattr(site_id, "default", site_id) if not isinstance(site_id, str) else site_id).strip()
    _source = str(getattr(source, "default", source) if not isinstance(source, str) else source).strip()
    _search = str(getattr(search, "default", search) if not isinstance(search, str) else search).strip()
    _depts = str(getattr(departments, "default", departments) if not isinstance(departments, str) else departments).strip()
    _secs = str(getattr(sections, "default", sections) if not isinstance(sections, str) else sections).strip()
    _disc_only = int(getattr(discrepancy_only, "default", discrepancy_only) if not isinstance(discrepancy_only, (int, str)) else int(discrepancy_only))
    _page = int(getattr(page, "default", page) if not isinstance(page, (int, str)) else int(page))
    _page_size = int(getattr(page_size, "default", page_size) if not isinstance(page_size, (int, str)) else int(page_size))

    try:
        pq_path = _API_CACHE_DIR / "daily_inventory_movement.parquet"
        if not pq_path.exists():
            return {
                "ok": False,
                "error": "daily_inventory_movement.parquet cache not found. Please run daily inventory movement build script.",
                "summary": {
                    "total_items": 0,
                    "total_calculated_soh": 0,
                    "total_actual_soh": 0,
                    "total_variance_qty": 0,
                    "exact_matches_count": 0,
                    "discrepancies_count": 0,
                    "system_accuracy_pct": 0.0,
                },
                "rows": [],
                "total": 0,
                "page": _page,
                "page_size": _page_size,
            }

        # ── 0. Build CUSTOMER_MASTER: store_name_upper → SITE MANUAL CODE ────────
        # SITE MANUAL CODE matches COMBINED_SOH_MASTER's "Site Code" column (401 overlaps).
        _cm_sitename_to_manual: dict[str, str] = {}  # SITE NAME.upper() → SITE MANUAL CODE
        _cm_sitename_to_display: dict[str, str] = {}  # SITE MANUAL CODE → human-readable SITE NAME
        try:
            _cm_path = Path(__file__).resolve().parents[2] / "pipeline" / "input_files" / "CUSTOMER_MASTER.csv"
            if _cm_path.exists():
                with _cm_path.open("rb") as _fh:
                    _magic = _fh.read(4)
                if _magic[:2] == b"PK":
                    _cm = pd.read_excel(_cm_path, header=3, dtype=str, engine="openpyxl")
                else:
                    _cm = pd.read_csv(_cm_path, header=3, dtype=str, encoding="latin1")
                _cm.columns = _cm.columns.astype(str).str.strip()
                _sn_col = next((c for c in _cm.columns if c.upper() == "SITE NAME"), None)
                _sm_col = next((c for c in _cm.columns if "MANUAL" in c.upper() and "CODE" in c.upper()), None)
                if _sn_col and _sm_col:
                    _cm[_sn_col] = _cm[_sn_col].astype(str).str.strip()
                    _cm[_sm_col] = _cm[_sm_col].astype(str).str.strip()
                    _cm = _cm[_cm[_sn_col].ne("") & _cm[_sm_col].ne("") & ~_cm[_sm_col].str.upper().eq("NAN")]
                    _cm_sitename_to_manual = dict(zip(_cm[_sn_col].str.upper(), _cm[_sm_col]))
                    _cm_sitename_to_display = dict(zip(_cm[_sm_col], _cm[_sn_col]))  # code → display name
                    logger.info("CUSTOMER_MASTER site name→manual_code map: %s entries", len(_cm_sitename_to_manual))
        except Exception as _exc:
            logger.warning("CUSTOMER_MASTER load skipped: %s", _exc)

        # ── 1. Load latest calculated SOH per (store, barcode) ─────────────────
        # daily_inventory_movement.store is already a site code (e.g. P033, 303) —
        # NOT a customer-master SITE NAME. Join key = normalized site code.
        want_cols = [
            "store", "barcode", "sku", "department", "section", "closing_soh",
            "dispatch_in", "ibt_in", "sales_qty", "date", "store_name",
        ]
        try:
            import pyarrow.parquet as pq
            available = set(pq.ParquetFile(str(pq_path)).schema_arrow.names)
            use_cols = [c for c in want_cols if c in available]
        except Exception:
            use_cols = ["store", "barcode", "sku", "department", "section", "closing_soh",
                        "dispatch_in", "ibt_in", "sales_qty", "date"]
        calc_df = pd.read_parquet(pq_path, columns=use_cols)
        calc_df["date"] = pd.to_datetime(calc_df["date"], errors="coerce")
        calc_df["store"] = calc_df["store"].astype(str).str.strip()
        calc_df["barcode"] = (
            calc_df["barcode"].astype(str).str.strip().str.upper().str.replace(r"\.0$", "", regex=True)
        )
        calc_df["closing_soh"] = pd.to_numeric(calc_df["closing_soh"], errors="coerce").fillna(0.0)

        from api.historical_movement import site_key_series

        # Prefer site_key; also accept raw store / customer-master manual code aliases
        site_keys = site_key_series(calc_df["store"]).astype(str).str.strip().str.upper()
        site_keys = site_keys.mask(site_keys.eq(""), calc_df["store"].str.upper())
        # If store looks like a long name, map via CUSTOMER_MASTER SITE NAME → manual code
        mapped_from_name = calc_df["store"].str.upper().map(_cm_sitename_to_manual).fillna("")
        site_keys = site_keys.where(mapped_from_name.eq(""), mapped_from_name.astype(str).str.strip())
        calc_df["site_id"] = site_keys.map(_site_id_str)

        if "date" in calc_df.columns and calc_df["date"].notna().any():
            idx = calc_df.groupby(["site_id", "barcode"], sort=False)["date"].transform("max") == calc_df["date"]
            latest_calc = calc_df.loc[idx].copy()
        else:
            latest_calc = calc_df.groupby(["site_id", "barcode"], sort=False).tail(1).copy()

        latest_calc = (
            latest_calc.groupby(["site_id", "barcode"], as_index=False, sort=False)
            .agg({
                "closing_soh": "sum",
                "sku": "first",
                "department": "first",
                "section": "first",
                "dispatch_in": "sum",
                "ibt_in": "sum",
                "sales_qty": "sum",
                **({"store_name": "first"} if "store_name" in latest_calc.columns else {}),
                **({"store": "first"} if "store" in latest_calc.columns else {}),
            })
        )
        latest_calc.rename(columns={"closing_soh": "calculated_soh"}, inplace=True)
        # Display name: parquet store_name → customer master → site_id
        if "store_name" in latest_calc.columns:
            store_name_map = dict(zip(latest_calc["site_id"], latest_calc["store_name"].astype(str)))
        else:
            store_name_map = {
                sid: _cm_sitename_to_display.get(sid, sid)
                for sid in latest_calc["site_id"].unique()
            }
        for sid, name in _cm_sitename_to_display.items():
            store_name_map.setdefault(str(sid).strip(), name)

        # ── 2. Load actual SOH from COMBINED_SOH_MASTER ────────────────────────
        _soh_master_path = Path(__file__).resolve().parents[2] / "pipeline" / "input_files" / "COMBINED_SOH_MASTER.csv"
        # Also check blob cache path
        _blob_soh = (Path(__file__).resolve().parents[2] / "data" / "blob_cache" /
                     "input_SOH-Master_current_COMBINED_SOH_MASTER.csv")
        if not _soh_master_path.exists() or _soh_master_path.stat().st_size == 0:
            _soh_master_path = _blob_soh

        if not _soh_master_path.exists() or _soh_master_path.stat().st_size == 0:
            return {
                "ok": False,
                "error": "COMBINED_SOH_MASTER.csv not found. Upload latest SOH data first.",
                "summary": {"total_items": 0, "total_calculated_soh": 0, "total_actual_soh": 0,
                            "total_variance_qty": 0, "exact_matches_count": 0,
                            "discrepancies_count": 0, "system_accuracy_pct": 0.0},
                "rows": [], "total": 0, "page": _page, "page_size": _page_size,
            }

        with _soh_master_path.open("rb") as _fh:
            _magic_soh = _fh.read(4)
        if _magic_soh[:2] == b"PK":
            actual_raw = pd.read_excel(_soh_master_path, dtype=str, engine="openpyxl")
        else:
            actual_raw = pd.read_csv(_soh_master_path, dtype=str, encoding="latin1", low_memory=False)
        actual_raw.columns = actual_raw.columns.astype(str).str.strip()

        # Detect column names (Site_id / Site Code / EAN / Barcode / Current Stock Qty)
        def _col_match(*needles: str) -> str | None:
            for c in actual_raw.columns:
                u = str(c).strip().upper().replace(" ", "_")
                if all(n in u for n in needles):
                    return c
            return None

        _bc_col_a = (
            _col_match("BARCODE")
            or _col_match("EAN")
            or next((c for c in actual_raw.columns if "EAN" in c.upper() or "BARCODE" in c.upper()), None)
        )
        _site_col_a = (
            _col_match("SITE", "CODE")
            or _col_match("SITE_ID")
            or _col_match("SITEID")
            or next((c for c in actual_raw.columns if "SITE" in c.upper()), None)
        )
        _qty_col_a = (
            _col_match("CURRENT", "STOCK")
            or _col_match("QTY")
            or next(
                (c for c in actual_raw.columns if "QTY" in c.upper() or "QUANTITY" in c.upper() or "STOCK" in c.upper()),
                None,
            )
        )

        if not _bc_col_a or not _site_col_a or not _qty_col_a:
            logger.warning("COMBINED_SOH_MASTER column detection failed: %s", actual_raw.columns.tolist())
            # fallback to _get_soh_df
            actual_raw = _get_soh_df(force_refresh=False)
            actual_raw = _apply_site_names(actual_raw)
            _bc_col_a = "barcode"
            _site_col_a = "site_id"
            _qty_col_a = "qty"

        actual_df = actual_raw.rename(columns={
            _bc_col_a: "barcode",
            _site_col_a: "site_id",
            _qty_col_a: "qty",
        }).copy()
        actual_df["barcode"] = actual_df["barcode"].astype(str).str.strip().str.upper().str.replace(r"\.0$", "", regex=True)
        actual_df["site_id"] = actual_df["site_id"].map(_site_id_str)
        actual_df["qty"] = pd.to_numeric(actual_df["qty"], errors="coerce").fillna(0.0)
        logger.info("COMBINED_SOH_MASTER loaded: %s rows (site_col=%s)", len(actual_df), _site_col_a)

        actual_agg = (
            actual_df.groupby(["site_id", "barcode"], as_index=False)
            .agg(actual_soh=("qty", "sum"))
        )
        # Map site code → display name from CUSTOMER_MASTER
        actual_agg["site_name_actual"] = actual_agg["site_id"].map(
            lambda x: _cm_sitename_to_display.get(str(x), _cm_sitename_to_display.get(str(x).upper(), ""))
        ).fillna("")

        # ── 3. Join calculated vs actual ──────────────────────────────────────
        merged = pd.merge(
            latest_calc,
            actual_agg,
            on=["site_id", "barcode"],
            how="outer"
        )

        merged["calculated_soh"] = pd.to_numeric(merged["calculated_soh"], errors="coerce").fillna(0.0)
        merged["actual_soh"] = pd.to_numeric(merged["actual_soh"], errors="coerce").fillna(0.0)
        merged["diff_soh"] = merged["calculated_soh"] - merged["actual_soh"]
        merged["sku"] = merged["sku"].fillna("").astype(str)
        merged["department"] = merged["department"].fillna("General").astype(str)
        merged["section"] = merged["section"].fillna("General").astype(str)
        # Site name: use store_name from calc (long name), else reverse lookup
        merged["store_name"] = merged["site_id"].map(store_name_map).fillna("")
        merged["site_name"] = merged["store_name"].where(
            merged["store_name"].ne(""),
            merged.get("site_name_actual", "").fillna("") if "site_name_actual" in merged.columns else "",
        )
        merged["site_name"] = merged["site_name"].fillna("").astype(str)
        # Derive channel / source (PT, SSL, CENTRO, ICONIC, EBO)
        try:
            from pipeline.pipeline_inputs import derive_store_type_series
            merged["source"] = derive_store_type_series(merged["site_name"], merged["site_id"]).fillna("Retail").replace("", "Retail")
        except Exception:
            merged["source"] = "Retail"

        # Filter out rows where both calculated and actual are 0 / junk keys
        merged["site_id"] = merged["site_id"].astype(str).str.strip()
        merged["barcode"] = merged["barcode"].astype(str).str.strip().str.upper()
        bad_key = {"", "NAN", "NONE", "<NA>", "NULL", "NAT"}
        merged = merged[
            ~merged["site_id"].str.upper().isin(bad_key)
            & ~merged["barcode"].isin(bad_key)
            & ((merged["calculated_soh"] > 0) | (merged["actual_soh"] > 0))
        ].copy()

        # Apply Filters
        if _site_id and _site_id != "all":
            merged = merged[merged["site_id"].str.upper() == _site_id.upper()]
        if _source and _source != "all":
            merged = merged[merged["source"].str.upper() == _source.upper()]
        if _depts:
            d_set = {x.strip().lower() for x in _depts.split(",") if x.strip()}
            merged = merged[merged["department"].str.lower().isin(d_set)]
        if _secs:
            s_set = {x.strip().lower() for x in _secs.split(",") if x.strip()}
            merged = merged[merged["section"].str.lower().isin(s_set)]
        if _search:
            q = _search.lower()
            mask = (
                merged["site_id"].str.lower().str.contains(q)
                | merged["site_name"].str.lower().str.contains(q)
                | merged["barcode"].str.lower().str.contains(q)
                | merged["sku"].str.lower().str.contains(q)
            )
            merged = merged[mask]
        if _disc_only == 1:
            merged = merged[merged["diff_soh"].abs() > 0]

        # Summary Metrics
        total_items = len(merged)
        # Rows where BOTH sides have data (meaningful comparison)
        matched_mask = (merged["calculated_soh"] > 0) & (merged["actual_soh"] > 0)
        matched_items = int(matched_mask.sum())
        # Items only in actual (store has stock we don't track)
        only_actual = int(((merged["calculated_soh"] == 0) & (merged["actual_soh"] > 0)).sum())
        # Items only in calc (we tracked stock not in store snapshot)
        only_calc = int(((merged["calculated_soh"] > 0) & (merged["actual_soh"] == 0)).sum())

        tot_calc = float(merged["calculated_soh"].sum())
        tot_act = float(merged["actual_soh"].sum())
        tot_var = float(merged["diff_soh"].abs().sum())
        # Exact match = diff==0, only counted among matched items
        exact_matches = int(((merged["diff_soh"] == 0) & matched_mask).sum())
        discrepancies = matched_items - exact_matches
        # Accuracy = exact matches among items present in BOTH systems
        accuracy_pct = round((exact_matches / max(matched_items, 1)) * 100.0, 2)
        # Coverage = how many actual SOH items could we calculate vs total actual
        coverage_pct = round((matched_items / max(matched_items + only_actual, 1)) * 100.0, 2)

        summary = {
            "total_items": total_items,
            "matched_items": matched_items,          # Items in both calc & actual
            "only_in_actual": only_actual,           # Actual has stock, we missed
            "only_in_calc": only_calc,               # We calculated, actual=0
            "total_calculated_soh": tot_calc,
            "total_actual_soh": tot_act,
            "total_variance_qty": tot_var,
            "exact_matches_count": exact_matches,
            "discrepancies_count": discrepancies,
            "system_accuracy_pct": accuracy_pct,    # Among matched items: % exact match
            "coverage_pct": coverage_pct,            # % of actual items we could reconcile
        }

        # Sort & Paginate
        merged = merged.sort_values(by="diff_soh", key=abs, ascending=False).reset_index(drop=True)
        start_idx = (page - 1) * page_size
        page_df = merged.iloc[start_idx : start_idx + page_size]

        rows = []
        for i, r in page_df.iterrows():
            diff = float(r["diff_soh"])
            status = "MATCH" if diff == 0 else ("CALCULATED_EXCESS" if diff > 0 else "STORE_EXCESS")
            disp_in = float(r["dispatch_in"]) if pd.notna(r.get("dispatch_in")) else 0.0
            ibt_in_val = float(r["ibt_in"]) if pd.notna(r.get("ibt_in")) else 0.0
            inflow = disp_in + ibt_in_val
            sales_val = float(r["sales_qty"]) if pd.notna(r.get("sales_qty")) else 0.0
            calc_soh = float(r["calculated_soh"]) if pd.notna(r.get("calculated_soh")) else 0.0
            act_soh = float(r["actual_soh"]) if pd.notna(r.get("actual_soh")) else 0.0
            # ST% from movement on this latest row (informative only)
            st_pct = round((sales_val / max(sales_val + max(calc_soh, 0.0), 1.0)) * 100.0, 1)
            # Gap % vs actual (or vs calc when actual=0)
            base = act_soh if act_soh > 0 else (calc_soh if calc_soh > 0 else 0.0)
            gap_pct = round((diff / base) * 100.0, 1) if base > 0 else None

            rows.append({
                "id": f"{r['site_id']}|{r['barcode']}|{i}",
                "site_id": r["site_id"],
                "site_name": r["site_name"],
                "barcode": r["barcode"],
                "sku": r["sku"],
                "department": r["department"],
                "section": r["section"],
                "source": r["source"],
                "calculated_soh": int(calc_soh),
                "actual_soh": int(act_soh),
                "diff_soh": int(diff),
                "gap_pct": gap_pct,
                "status": status,
                "inflow": int(inflow),
                "sales_qty": int(sales_val),
                "sell_through_pct": st_pct,
            })

        return {
            "ok": True,
            "summary": summary,
            "total": total_items,
            "page": page,
            "page_size": page_size,
            "rows": rows,
        }
    except Exception as exc:
        logger.exception("SOH reconciliation failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


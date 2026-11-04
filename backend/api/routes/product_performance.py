"""
Product Performance — Comprehensive SKU & Store velocity metrics from live sales,
Item Master, Warehouse SOH, and Floor SOH.

Endpoint: GET /api/product-performance/overview
"""
from __future__ import annotations

import datetime
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from fastapi import APIRouter

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/product-performance", tags=["Product Performance"])

_BACKEND = Path(__file__).resolve().parents[2]
_CACHE_DIR = _BACKEND / "data" / "api_cache"
_BLOB_DIR = _BACKEND / "data" / "blob_cache"
_INPUT_DIR = _BACKEND / "pipeline" / "input_files"
_OUTPUT_DIR = _BACKEND / "pipeline" / "output_files"

_CACHE_LOCK = threading.RLock()
_MEMORY_SALES: tuple[str, pd.DataFrame] | None = None
_MEMORY_ITEM_MASTER: tuple[str, pd.DataFrame] | None = None
_MEMORY_WH_SOH: tuple[str, pd.DataFrame] | None = None
_MEMORY_FLOOR_SOH: tuple[str, pd.DataFrame] | None = None


def _clean_site_label(site_id: str, site_name: str) -> str:
    sid = str(site_id or "").strip().strip("()")
    sn = str(site_name or "").strip()
    if not sn or sn.lower() in ("nan", "none", "<na>"):
        return sid or "Unknown Site"
    if sid:
        sn = re.sub(r"\(\s*" + re.escape(sid) + r"\s*\)", "", sn, flags=re.IGNORECASE).strip()
    sn = re.sub(r"\s+", " ", sn).strip()
    if sid:
        return f"{sn} ({sid})" if not sn.endswith(f"({sid})") else sn
    return sn


def _clean_label(v: Any) -> str:
    s = str(v or "").strip()
    if s.lower() in {"", "nan", "none", "<na>"}:
        return "—"
    return s


# ─────────────────────────────────────────────────────────────────────────────
# 1. ITEM MASTER LOADER & CACHE
# ─────────────────────────────────────────────────────────────────────────────
def _load_item_master_meta() -> pd.DataFrame:
    """Load SKU level metadata (ref_no, product_type, story_name, cost, mrp, launch_date)."""
    global _MEMORY_ITEM_MASTER
    p = _BLOB_DIR / "input_Item_Master_current_ITEM_MASTER.csv"
    if not p.exists() or p.stat().st_size == 0:
        p = _INPUT_DIR / "ITEM_MASTER.xlsx"

    if not p.exists() or p.stat().st_size == 0:
        return pd.DataFrame(
            columns=["sku", "ref_no", "product_type", "story_name", "department", "section", "season", "cost", "mrp", "launch_date"]
        )

    key = f"{p.resolve()}|{p.stat().st_mtime_ns}|{p.stat().st_size}_v5"
    with _CACHE_LOCK:
        if _MEMORY_ITEM_MASTER and _MEMORY_ITEM_MASTER[0] == key:
            return _MEMORY_ITEM_MASTER[1]

    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    disk = _CACHE_DIR / "item_master_sku_meta.parquet"
    meta_key = _CACHE_DIR / "item_master_sku_meta.key"

    if disk.exists() and meta_key.exists() and meta_key.read_text(encoding="utf-8") == key:
        try:
            df = pd.read_parquet(disk)
            with _CACHE_LOCK:
                _MEMORY_ITEM_MASTER = (key, df)
            return df
        except Exception as exc:
            logger.warning("Item master parquet read error: %s", exc)

    try:
        import sys
        if str(_BACKEND / "pipeline") not in sys.path:
            sys.path.append(str(_BACKEND / "pipeline"))
        from item_master import load_item_master_df

        raw = load_item_master_df()
        if raw is None or raw.empty:
            return pd.DataFrame()

        raw.columns = raw.columns.astype(str).str.strip()
        # Prefer exact style/SKU identifiers — never fuzzy-match fabric/size columns.
        sku_col = None
        for cand in ["SKU CODE", "SKU", "ITEM CODE", "STYLE ID", "STYLE CODE"]:
            if cand in raw.columns:
                sku_col = cand
                break
        if not sku_col:
            for c in raw.columns:
                cl = c.lower().strip()
                if cl in {"sku", "sku code", "item code", "style id", "style code"}:
                    sku_col = c
                    break

        if not sku_col:
            return pd.DataFrame()

        df = pd.DataFrame()
        df["sku"] = (
            raw[sku_col]
            .astype(str)
            .str.strip()
            .str.upper()
            .replace({"NAN": "", "NONE": "", "<NA>": "", "NULL": ""})
        )
        df["ref_no"] = raw["REF. NO."].astype(str).replace({"nan": "", "None": "", "<NA>": ""}).str.strip() if "REF. NO." in raw.columns else ""
        df["product_type"] = raw["PRODUCT TYPE"].astype(str).replace({"nan": "", "None": "", "<NA>": ""}).str.strip() if "PRODUCT TYPE" in raw.columns else ""
        df["story_name"] = raw["STORY NAME"].astype(str).replace({"nan": "", "None": "", "<NA>": ""}).str.strip() if "STORY NAME" in raw.columns else ""
        df["department"] = raw["DEPARTMENT"].astype(str).replace({"nan": "", "None": "", "<NA>": ""}).str.strip() if "DEPARTMENT" in raw.columns else ""
        df["section"] = raw["SECTION"].astype(str).replace({"nan": "", "None": "", "<NA>": ""}).str.strip() if "SECTION" in raw.columns else ""
        df["season"] = raw["SEASON"].astype(str).replace({"nan": "", "None": "", "<NA>": ""}).str.strip() if "SEASON" in raw.columns else ""
        df["cost"] = pd.to_numeric(raw.get("STD RATE"), errors="coerce").fillna(0.0)
        df["mrp"] = pd.to_numeric(raw.get("MRP"), errors="coerce").fillna(0.0)

        launch_raw = raw.get("Created On") if "Created On" in raw.columns else raw.get("MANUFACTRUR DATE")
        if launch_raw is not None:
            df["launch_date"] = pd.to_datetime(launch_raw, errors="coerce").dt.strftime("%Y-%m-%d").fillna("")
        else:
            df["launch_date"] = ""

        # Drop fabric/size junk that previously polluted the SKU grain cache.
        sku_s = df["sku"]
        junk = (
            sku_s.eq("")
            | sku_s.str.contains("%", regex=False)
            | sku_s.str.contains('"', regex=False)
            | sku_s.str.contains("COTTON|POLYESTER|VISCOSE|ACRYLIC|NYLON|LYCRA|MTRS", case=False, regex=True)
        )
        df = df[~junk]
        dedup = df.groupby("sku", as_index=False).first()
        try:
            dedup.to_parquet(disk, index=False)
            meta_key.write_text(key, encoding="utf-8")
        except Exception as write_err:
            logger.warning("Item Master cache write error: %s", write_err)

        with _CACHE_LOCK:
            _MEMORY_ITEM_MASTER = (key, dedup)
        return dedup
    except Exception as exc:
        logger.error("Failed to parse Item Master: %s", exc)
        return pd.DataFrame()


# ─────────────────────────────────────────────────────────────────────────────
# 2. WAREHOUSE SOH & STORE SOH LOADERS
# ─────────────────────────────────────────────────────────────────────────────
def _load_wh_soh_sku() -> pd.DataFrame:
    """Load SKU level Warehouse closing stock quantity (H.O. SOH) with disk Parquet caching.

    Hosted Azure often has no local pipeline/input_files/WAREHOUSE_SOH.* — resolve via
    pipeline_inputs.warehouse_register_path() which pulls input/Warehouse SOH/current/ from blob.
    """
    global _MEMORY_WH_SOH
    try:
        import sys

        if str(_BACKEND / "pipeline") not in sys.path:
            sys.path.append(str(_BACKEND / "pipeline"))
        from pipeline_inputs import load_warehouse_register, warehouse_register_path

        wh_path = warehouse_register_path()
    except Exception as exc:
        logger.warning("Warehouse SOH path resolve failed: %s", exc)
        return pd.DataFrame(columns=["sku", "ho_soh_qty"])

    if wh_path is None or not wh_path.exists() or wh_path.stat().st_size == 0:
        return pd.DataFrame(columns=["sku", "ho_soh_qty"])

    key = f"{wh_path.resolve()}|{wh_path.stat().st_mtime_ns}|{wh_path.stat().st_size}_v7"
    with _CACHE_LOCK:
        if _MEMORY_WH_SOH and _MEMORY_WH_SOH[0] == key:
            return _MEMORY_WH_SOH[1]

    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    disk = _CACHE_DIR / "warehouse_soh_sku.parquet"
    meta_key = _CACHE_DIR / "warehouse_soh_sku.key"

    if disk.exists() and meta_key.exists() and meta_key.read_text(encoding="utf-8") == key:
        try:
            df = pd.read_parquet(disk)
            with _CACHE_LOCK:
                _MEMORY_WH_SOH = (key, df)
            return df
        except Exception as exc:
            logger.warning("WH SOH parquet cache read error: %s", exc)

    try:
        wh = load_warehouse_register()
        if wh is None or wh.empty:
            return pd.DataFrame(columns=["sku", "ho_soh_qty"])

        wh.columns = wh.columns.astype(str).str.strip()
        sku_col = next(
            (
                c
                for c in ("STYLE ID", "STYLEID", "SKU", "SKU CODE", "ITEM CODE", "ARTICLE")
                if c in wh.columns
            ),
            None,
        )
        qty_col = next(
            (
                c
                for c in ("CLOSINGSTOCKQTY", "CLOSING STOCK QTY", "CLOSING_STOCK_QTY", "QTY")
                if c in wh.columns
            ),
            None,
        )
        if not sku_col or not qty_col:
            logger.warning(
                "Warehouse SOH missing STYLE/SKU or qty cols — have %s",
                list(wh.columns)[:20],
            )
            return pd.DataFrame(columns=["sku", "ho_soh_qty"])

        wh["sku"] = (
            wh[sku_col]
            .astype(str)
            .str.strip()
            .str.upper()
            .replace({"NAN": "", "NONE": "", "<NA>": "", "NULL": ""})
        )
        wh["qty"] = pd.to_numeric(wh[qty_col], errors="coerce").fillna(0)
        wh = wh[wh["sku"].str.len() > 0]
        agg = wh.groupby("sku", as_index=False).agg(ho_soh_qty=("qty", "sum"))
        agg["ho_soh_qty"] = agg["ho_soh_qty"].clip(lower=0).astype(int)

        try:
            agg.to_parquet(disk, index=False)
            meta_key.write_text(key, encoding="utf-8")
        except Exception as write_err:
            logger.warning("WH SOH cache write error: %s", write_err)

        with _CACHE_LOCK:
            _MEMORY_WH_SOH = (key, agg)
        logger.info(
            "Product Performance WH SOH: path=%s rows=%d qty=%d",
            wh_path.name,
            len(agg),
            int(agg["ho_soh_qty"].sum()),
        )
        return agg
    except Exception as exc:
        logger.warning("Failed to load warehouse SOH: %s", exc)
        return pd.DataFrame(columns=["sku", "ho_soh_qty"])


def _daily_movement_path() -> Path:
    try:
        from api.current_soh import ensure_daily_movement_from_blob, daily_movement_parquet_path

        ensure_daily_movement_from_blob()
        return daily_movement_parquet_path()
    except Exception:
        return _CACHE_DIR / "daily_inventory_movement.parquet"


def _dm_cache_sig(path: Path) -> str:
    if not path.exists():
        return "missing"
    st = path.stat()
    return f"dm:{st.st_mtime_ns}:{st.st_size}"


def _load_floor_soh_sku() -> pd.DataFrame:
    """Store On-Floor Current SOH from daily_inventory_movement (latest closing_soh).

    Same source as Sales Overview / Analyzer — not COMBINED_SOH.
    """
    global _MEMORY_FLOOR_SOH
    path = _daily_movement_path()
    if not path.exists() or path.stat().st_size <= 0:
        logger.warning("daily_inventory_movement missing — Product Performance floor SOH empty")
        return pd.DataFrame(columns=["sku", "barcode", "site_id", "floor_soh_qty"])

    key = f"floor_dm_v1|{_dm_cache_sig(path)}"
    with _CACHE_LOCK:
        if _MEMORY_FLOOR_SOH and _MEMORY_FLOOR_SOH[0] == key:
            return _MEMORY_FLOOR_SOH[1]

    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    disk = _CACHE_DIR / "floor_soh_sku.parquet"
    meta_key = _CACHE_DIR / "floor_soh_sku.key"
    if disk.exists() and meta_key.exists() and meta_key.read_text(encoding="utf-8").strip() == key:
        try:
            df = pd.read_parquet(disk)
            with _CACHE_LOCK:
                _MEMORY_FLOOR_SOH = (key, df)
            return df
        except Exception as exc:
            logger.warning("Floor SOH parquet cache read error: %s", exc)

    try:
        from api.current_soh import load_latest_closing_soh_pairs
        from api.historical_movement import site_key_series

        pairs = load_latest_closing_soh_pairs(force_refresh=False)
        if pairs is None or pairs.empty:
            return pd.DataFrame(columns=["sku", "barcode", "site_id", "floor_soh_qty"])

        want = ["store", "barcode", "sku", "date", "closing_soh"]
        try:
            import pyarrow.parquet as pq

            available = set(pq.ParquetFile(str(path)).schema_arrow.names)
        except Exception:
            available = set(pd.read_parquet(path).head(0).columns)
        use_cols = [c for c in want if c in available]
        dm = pd.read_parquet(path, columns=use_cols)
        dm["store"] = dm["store"].astype(str).str.strip().str.upper()
        dm["barcode"] = (
            dm["barcode"].astype(str).str.strip().str.upper().str.replace(r"\.0$", "", regex=True)
        )
        if "sku" not in dm.columns:
            dm["sku"] = ""
        dm["sku"] = dm["sku"].astype(str).str.strip().str.upper().replace(
            {"NAN": "", "NONE": "", "<NA>": "", "NULL": ""}
        )

        # Latest closing per store×barcode (same as current_soh pairs)
        latest = pairs[["store", "barcode", "closing_soh"]].copy()
        latest["store"] = latest["store"].astype(str).str.strip().str.upper()
        latest["barcode"] = (
            latest["barcode"].astype(str).str.strip().str.upper().str.replace(r"\.0$", "", regex=True)
        )
        sku_map = (
            dm.groupby(["store", "barcode"], sort=False)["sku"]
            .agg(lambda s: next((x for x in s if str(x).strip()), ""))
            .reset_index()
        )
        latest = latest.merge(sku_map, on=["store", "barcode"], how="left")
        latest["sku"] = latest["sku"].fillna("").astype(str).str.strip().str.upper()

        missing_sku = latest["sku"].eq("")
        if missing_sku.any():
            try:
                import sys

                if str(_BACKEND / "pipeline") not in sys.path:
                    sys.path.append(str(_BACKEND / "pipeline"))
                from item_master import build_barcode_item_details_map

                bc_map = build_barcode_item_details_map()
                if not bc_map.empty and "barcode_key" in bc_map.columns and "sku" in bc_map.columns:
                    bc_to_sku = (
                        bc_map.assign(
                            barcode_key=bc_map["barcode_key"]
                            .astype(str)
                            .str.strip()
                            .str.upper()
                            .str.replace(r"\.0$", "", regex=True)
                        )
                        .set_index("barcode_key")["sku"]
                        .astype(str)
                        .str.strip()
                        .str.upper()
                        .to_dict()
                    )
                    latest.loc[missing_sku, "sku"] = (
                        latest.loc[missing_sku, "barcode"].map(bc_to_sku).fillna("")
                    )
            except Exception as b_err:
                logger.warning("Barcode to SKU map failed in DM floor SOH: %s", b_err)

        site_keys = site_key_series(latest["store"]).astype(str).str.strip().str.upper()
        site_keys = site_keys.mask(site_keys.eq(""), latest["store"])
        agg = pd.DataFrame(
            {
                "sku": latest["sku"],
                "barcode": latest["barcode"],
                "site_id": site_keys,
                "floor_soh_qty": pd.to_numeric(latest["closing_soh"], errors="coerce")
                .fillna(0.0)
                .clip(lower=0.0),
            }
        )
        agg = agg[agg["sku"].ne("") | agg["barcode"].ne("")]
        agg = (
            agg.groupby(["sku", "barcode", "site_id"], as_index=False, sort=False)
            .agg(floor_soh_qty=("floor_soh_qty", "sum"))
        )
        agg["floor_soh_qty"] = agg["floor_soh_qty"].round(0).astype(int)

        try:
            agg.to_parquet(disk, index=False)
            meta_key.write_text(key, encoding="utf-8")
        except Exception as write_err:
            logger.warning("Floor SOH cache write error: %s", write_err)

        with _CACHE_LOCK:
            _MEMORY_FLOOR_SOH = (key, agg)
        logger.info(
            "Product Performance floor SOH from daily_inventory_movement: rows=%d soh=%.0f skus=%d",
            len(agg),
            float(agg["floor_soh_qty"].sum()),
            agg.loc[agg["sku"].ne(""), "sku"].nunique(),
        )
        return agg
    except Exception as exc:
        logger.warning("Failed to load store floor SOH from daily movement: %s", exc)
        return pd.DataFrame(columns=["sku", "barcode", "site_id", "floor_soh_qty"])


_MEMORY_DM_STR: tuple[str, dict[str, dict[str, float]]] | None = None


def _load_daily_str_by_sku() -> dict[str, dict[str, float]]:
    """SKU → lifetime STR metrics from daily_inventory_movement (Sales Overview formula).

    available = dispatch_in + ibt_in + sales_return_qty
    sell_through_pct = sales_qty / available × 100 (capped at 100)
    """
    global _MEMORY_DM_STR
    path = _daily_movement_path()
    if not path.exists() or path.stat().st_size <= 0:
        return {}

    key = f"str_sku_dm_v1|{_dm_cache_sig(path)}"
    with _CACHE_LOCK:
        if _MEMORY_DM_STR and _MEMORY_DM_STR[0] == key:
            return _MEMORY_DM_STR[1]

    try:
        cols = [
            "sku", "barcode", "store",
            "dispatch_in", "ibt_in", "sales_return_qty", "sales_qty", "full_price_sales_qty",
        ]
        try:
            import pyarrow.parquet as pq

            available = set(pq.ParquetFile(str(path)).schema_arrow.names)
        except Exception:
            available = set(pd.read_parquet(path).head(0).columns)
        use_cols = [c for c in cols if c in available]
        if "dispatch_in" not in use_cols or "sales_qty" not in use_cols:
            return {}

        dm = pd.read_parquet(path, columns=use_cols)
        if "sku" not in dm.columns:
            dm["sku"] = ""
        dm["sku"] = dm["sku"].astype(str).str.strip().str.upper().replace(
            {"NAN": "", "NONE": "", "<NA>": "", "NULL": ""}
        )
        dm["barcode"] = (
            dm["barcode"].astype(str).str.strip().str.upper().str.replace(r"\.0$", "", regex=True)
            if "barcode" in dm.columns
            else ""
        )
        for c in ("dispatch_in", "ibt_in", "sales_return_qty", "sales_qty", "full_price_sales_qty"):
            if c not in dm.columns:
                dm[c] = 0.0
            else:
                dm[c] = pd.to_numeric(dm[c], errors="coerce").fillna(0.0)

        # Fill blank SKU from barcode map once
        missing = dm["sku"].eq("")
        if missing.any():
            try:
                import sys

                if str(_BACKEND / "pipeline") not in sys.path:
                    sys.path.append(str(_BACKEND / "pipeline"))
                from item_master import build_barcode_item_details_map

                bc_map = build_barcode_item_details_map()
                if not bc_map.empty and "barcode_key" in bc_map.columns and "sku" in bc_map.columns:
                    bc_to_sku = (
                        bc_map.assign(
                            barcode_key=bc_map["barcode_key"]
                            .astype(str)
                            .str.strip()
                            .str.upper()
                            .str.replace(r"\.0$", "", regex=True)
                        )
                        .set_index("barcode_key")["sku"]
                        .astype(str)
                        .str.strip()
                        .str.upper()
                        .to_dict()
                    )
                    dm.loc[missing, "sku"] = dm.loc[missing, "barcode"].map(bc_to_sku).fillna("")
            except Exception as exc:
                logger.warning("DM STR barcode→SKU map failed: %s", exc)

        dm = dm[dm["sku"].ne("")]
        g = (
            dm.groupby("sku", sort=False)
            .agg(
                dispatch_in=("dispatch_in", "sum"),
                ibt_in=("ibt_in", "sum"),
                sales_return_qty=("sales_return_qty", "sum"),
                sales_qty=("sales_qty", "sum"),
                full_price_sales_qty=("full_price_sales_qty", "sum"),
            )
            .reset_index()
        )
        g["available"] = g["dispatch_in"] + g["ibt_in"] + g["sales_return_qty"]

        def _pct(num: float, den: float) -> float:
            if not den or den <= 0:
                return 0.0
            return round(min(100.0, float(num) / float(den) * 100.0), 1)

        out: dict[str, dict[str, float]] = {}
        for r in g.itertuples(index=False):
            avail = float(r.available)
            sales = float(r.sales_qty)
            disp = float(r.dispatch_in)
            out[str(r.sku)] = {
                "dispatch_in": disp,
                "ibt_in": float(r.ibt_in),
                "sales_return_qty": float(r.sales_return_qty),
                "available": avail,
                "sales_qty": sales,
                "full_price_sales_qty": float(r.full_price_sales_qty),
                "sell_through_pct": _pct(sales, avail),
                "dispatch_st_pct": _pct(sales, disp),
            }

        with _CACHE_LOCK:
            _MEMORY_DM_STR = (key, out)
        logger.info(
            "Product Performance STR from daily_inventory_movement: skus=%d",
            len(out),
        )
        return out
    except Exception as exc:
        logger.warning("Failed to load DM STR by SKU: %s", exc)
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────
# 3. SALES & WEEKLY VELOCITY LOADER (FY-AWARE)
# ─────────────────────────────────────────────────────────────────────────────
def _fy_end_for_ts(ts: pd.Timestamp) -> int:
    """Indian FY end calendar year for a timestamp (Apr–Mar)."""
    return int(ts.year + 1) if int(ts.month) >= 4 else int(ts.year)


def _years_for_product_load(year_val: int, start_date: str = "", end_date: str = "") -> list[int]:
    """FY codes to load — date range wins so custom From/To can span FYs."""
    from api.sales_year_loader import (
        is_all_years,
        normalize_dashboard_year,
        supported_dashboard_years,
    )

    sd = (start_date or "").strip()
    ed = (end_date or "").strip()
    if sd or ed:
        try:
            a = pd.Timestamp(sd or ed)
            b = pd.Timestamp(ed or sd)
            if pd.isna(a) or pd.isna(b):
                raise ValueError("invalid date")
            y0 = _fy_end_for_ts(min(a, b))
            y1 = _fy_end_for_ts(max(a, b))
            allowed = supported_dashboard_years()
            years = [y for y in range(min(y0, y1), max(y0, y1) + 1) if y in allowed]
            if years:
                return years
        except Exception:
            pass
    if is_all_years(year_val):
        return sorted(supported_dashboard_years())
    return [normalize_dashboard_year(year_val, default=2027)]


def _load_full_sales_data(
    year_val: int = 2027,
    start_date: str = "",
    end_date: str = "",
) -> pd.DataFrame:
    """Load normalized sales for FY / All Years / date-spanning FYs."""
    global _MEMORY_SALES
    from api.sales_year_loader import load_sales_for_year

    years = _years_for_product_load(year_val, start_date, end_date)
    key = f"fy_{'_'.join(str(y) for y in years)}_sales_v5"

    with _CACHE_LOCK:
        if _MEMORY_SALES and _MEMORY_SALES[0] == key:
            return _MEMORY_SALES[1]

    raw = pd.DataFrame()
    try:
        from api.sales_year_loader import is_all_years, supported_dashboard_years

        # Prefer single All-Years cache when every FY is requested
        if (
            is_all_years(year_val)
            and not (start_date or "").strip()
            and not (end_date or "").strip()
            and set(years) == set(supported_dashboard_years())
        ):
            raw = load_sales_for_year(0)
        else:
            parts: list[pd.DataFrame] = []
            for y in years:
                part = load_sales_for_year(y)
                if part is not None and not part.empty:
                    parts.append(part)
            if parts:
                raw = pd.concat(parts, ignore_index=True) if len(parts) > 1 else parts[0]
    except Exception as exc:
        logger.warning("Failed to load_sales_for_year(%s): %s", years, exc)

    if raw is None or raw.empty:
        # Fallback to Sales-Master or COMBINED_SALES
        p: Path | None = None
        try:
            from api.sales_year_loader import resolve_sales_master_path
            master = resolve_sales_master_path()
            if master is not None and master.exists() and master.stat().st_size > 0:
                p = master
        except Exception as exc:
            logger.warning("Product performance Sales-Master resolve skipped: %s", exc)

        if p is None:
            p = _BLOB_DIR / "output_COMBINED_SALES.parquet"
            if not p.exists() or p.stat().st_size == 0:
                p = _BLOB_DIR / "output_COMBINED_SALES.csv"
            if not p.exists() or p.stat().st_size == 0:
                p = _OUTPUT_DIR / "COMBINED_SALES.csv"

        if p is not None and p.exists() and p.stat().st_size > 0:
            if p.suffix.lower() == ".parquet":
                raw = pd.read_parquet(p)
            else:
                raw = pd.read_csv(p, low_memory=False, encoding="latin1")

    if raw is None or raw.empty:
        return pd.DataFrame()

    raw.columns = raw.columns.astype(str).str.strip()
    cols = {c.lower(): c for c in raw.columns}

    date_c = (
        cols.get("date")
        or cols.get("invoice date")
        or cols.get("entry_date")
        or cols.get("transaction_date")
        or cols.get("bill_date")
        or cols.get("bill date")
    )
    site_id_c = (
        cols.get("site_id")
        or cols.get("site code")
        or cols.get("site_code")
        or cols.get("store code")
    )
    site_name_c = (
        cols.get("site_name")
        or cols.get("site name")
        or cols.get("site_name_master")
        or cols.get("transaction_site")
        or cols.get("ref site")
    )
    sku_c = cols.get("sku") or cols.get("cat3") or cols.get("category3")
    bc_c = cols.get("barcode") or cols.get("ean")
    dept_c = cols.get("department")
    sec_c = cols.get("section")
    season_c = cols.get("season") or cols.get("cat6") or cols.get("category6")
    sales_c = (
        cols.get("net_sale_amount")
        or cols.get("net_sales_amount")
        or cols.get("net amount")
        or cols.get("net_amount")
        or cols.get("item net amt")
    )
    qty_c = cols.get("qty") or cols.get("qty_sold") or cols.get("quantity")
    disc_c = (
        cols.get("discount_amount")
        or cols.get("discount amount")
        or cols.get("discount amt")
        or cols.get("item_discount")
    )
    mrp_c = cols.get("mrp") or cols.get("rsp")

    work = pd.DataFrame()
    work["date"] = pd.to_datetime(raw[date_c], errors="coerce") if date_c else pd.NaT
    work["site_id"] = raw[site_id_c].astype(str).str.strip() if site_id_c else ""
    work["site_name"] = raw[site_name_c].astype(str).str.strip() if site_name_c else ""
    work["sku"] = raw[sku_c].astype(str).str.strip() if sku_c else ""
    work["barcode"] = raw[bc_c].astype(str).str.strip() if bc_c else ""
    work["department"] = raw[dept_c].astype(str).str.strip() if dept_c else ""
    work["section"] = raw[sec_c].astype(str).str.strip() if sec_c else ""
    work["season"] = raw[season_c].astype(str).str.strip() if season_c else ""
    work["sales"] = pd.to_numeric(raw[sales_c], errors="coerce").fillna(0.0) if sales_c else 0.0
    work["qty"] = pd.to_numeric(raw[qty_c], errors="coerce").fillna(0) if qty_c else 0
    work["discount"] = pd.to_numeric(raw[disc_c], errors="coerce").fillna(0.0) if disc_c else 0.0
    work["mrp"] = pd.to_numeric(raw[mrp_c], errors="coerce").fillna(0.0) if mrp_c else 0.0

    for col in ("site_id", "site_name", "sku", "barcode", "department", "section", "season"):
        work[col] = work[col].replace({"nan": "", "None": "", "<NA>": ""}).fillna("")

    work["sku_key"] = work["sku"].where(work["sku"].str.len() > 0, work["barcode"])
    work = work[work["sku_key"].astype(str).str.len() > 0]
    work["site_label"] = [
        _clean_site_label(sid, sn) for sid, sn in zip(work["site_id"], work["site_name"])
    ]

    with _CACHE_LOCK:
        _MEMORY_SALES = (key, work)
    return work


# ─────────────────────────────────────────────────────────────────────────────
# 4. OVERVIEW API ENDPOINT
# ─────────────────────────────────────────────────────────────────────────────
_OVERVIEW_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_OVERVIEW_CACHE_TTL = 3600.0  # 1h RAM; durable page_cache/blob survives restarts


@router.get("/overview")
def get_product_performance_overview(
    year: int = 2027,
    limit: int = 1500,
    site: str = "",
    department: str = "",
    section: str = "",
    season: str = "",
    search: str = "",
    mover_type: str = "",
    start_date: str = "",
    end_date: str = "",
    refresh: int = 0,
):
    """Comprehensive Product Performance with all 37 enterprise columns and FY-aware date range filtering."""
    # dm_v3: blob-aware WH SOH + reject all-zero SOH page_cache (Azure hosted)
    cache_key = f"dm_v3|{year}|{limit}|{site.strip()}|{department.strip()}|{section.strip()}|{season.strip()}|{search.strip()}|{mover_type.strip()}|{start_date.strip()}|{end_date.strip()}"
    now_ts = datetime.datetime.now().timestamp()
    _refresh = int(refresh or 0) == 1

    def _soh_cache_stale(payload: dict[str, Any]) -> bool:
        """Reject large caches where every sample row has 0 WH + 0 Floor (pre-SOH snapshots)."""
        rows = payload.get("rows") or []
        if len(rows) < 100:
            return False
        sample = rows[:40]
        has_soh = any(
            int(r.get("ho_soh_qty") or 0) > 0 or int(r.get("floor_soh_qty") or 0) > 0 for r in sample
        )
        return not has_soh

    if not _refresh:
        with _CACHE_LOCK:
            if cache_key in _OVERVIEW_CACHE:
                cached_time, cached_res = _OVERVIEW_CACHE[cache_key]
                if (
                    now_ts - cached_time < _OVERVIEW_CACHE_TTL
                    and isinstance(cached_res, dict)
                    and not _soh_cache_stale(cached_res)
                ):
                    return cached_res
                if isinstance(cached_res, dict) and _soh_cache_stale(cached_res):
                    _OVERVIEW_CACHE.pop(cache_key, None)
        try:
            from api.page_response_cache import load_page_cache, rebuilding_page_cache

            if not rebuilding_page_cache():
                disk = load_page_cache("product-performance", cache_key)
                if isinstance(disk, dict) and disk.get("ok") and (disk.get("rows") is not None):
                    if _soh_cache_stale(disk):
                        logger.warning(
                            "Product performance page_cache stale (all-zero SOH) — rebuilding key=%s",
                            cache_key[:80],
                        )
                    else:
                        with _CACHE_LOCK:
                            _OVERVIEW_CACHE[cache_key] = (now_ts, disk)
                        return disk
        except Exception as exc:
            logger.warning("Product performance page_cache load skipped: %s", exc)

    base_frame = _load_full_sales_data(
        year_val=year, start_date=start_date, end_date=end_date
    )
    if base_frame.empty:
        return {
            "ok": False,
            "error": f"Sales data not found for FY-{str(year)[-2:] if year else 'All'}. Please upload sales records first.",
            "summary": {},
            "byDepartment": [],
            "bySection": [],
            "fastMovers": [],
            "slowMovers": [],
            "rows": [],
            "filters": {"sites": [], "departments": [], "sections": [], "seasons": [], "minDate": "", "maxDate": "", "year": year, "financialYear": f"FY{str(year)[-2:]}"},
            "total": 0,
        }

    # Load Item Master, WH SOH, Floor SOH (DM) + SKU STR (DM)
    item_master_df = _load_item_master_meta()
    wh_soh_df = _load_wh_soh_sku()
    floor_soh_df = _load_floor_soh_sku()
    dm_str_by_sku = _load_daily_str_by_sku()

    # Pre-index metadata dictionaries instantly without iterrows (SKU keys uppercased)
    im_dict: dict[str, dict[str, Any]] = {}
    if not item_master_df.empty:
        _im = item_master_df.copy()
        _im["sku"] = _im["sku"].astype(str).str.strip().str.upper()
        im_dict = _im.drop_duplicates("sku", keep="first").set_index("sku").to_dict(orient="index")

    wh_dict: dict[str, int] = {}
    if not wh_soh_df.empty and "sku" in wh_soh_df.columns:
        _wh = wh_soh_df.copy()
        _wh["sku"] = _wh["sku"].astype(str).str.strip().str.upper()
        wh_dict = _wh.groupby("sku")["ho_soh_qty"].sum().astype(int).to_dict()

    # Floor SOH - Filter by site if site is selected, else aggregate globally
    floor_soh_filtered = floor_soh_df.copy()
    if site.strip() and not floor_soh_filtered.empty and "site_id" in floor_soh_filtered.columns:
        s_target = site.strip().lower()
        floor_soh_filtered = floor_soh_filtered[floor_soh_filtered["site_id"].astype(str).str.lower() == s_target]

    floor_sku_dict: dict[str, int] = {}
    floor_bc_dict: dict[str, int] = {}
    if not floor_soh_filtered.empty:
        if "sku" in floor_soh_filtered.columns:
            valid_sku_rows = floor_soh_filtered[floor_soh_filtered["sku"].astype(str).str.len() > 0].copy()
            valid_sku_rows["sku"] = valid_sku_rows["sku"].astype(str).str.strip().str.upper()
            floor_sku_dict = valid_sku_rows.groupby("sku")["floor_soh_qty"].sum().astype(int).to_dict()
        if "barcode" in floor_soh_filtered.columns:
            valid_bc_rows = floor_soh_filtered[floor_soh_filtered["barcode"].astype(str).str.len() > 0].copy()
            valid_bc_rows["barcode"] = (
                valid_bc_rows["barcode"]
                .astype(str)
                .str.strip()
                .str.upper()
                .str.replace(r"\.0$", "", regex=True)
            )
            floor_bc_dict = valid_bc_rows.groupby("barcode")["floor_soh_qty"].sum().astype(int).to_dict()

    # Overall dataset date bounds
    all_valid_dates = base_frame["date"].dropna()
    min_date_str = all_valid_dates.min().strftime("%Y-%m-%d") if not all_valid_dates.empty else ""
    max_date_str = all_valid_dates.max().strftime("%Y-%m-%d") if not all_valid_dates.empty else ""

    # Filter distinct options using fast dictionary conversion
    site_tuples = base_frame[["site_id", "site_name", "site_label"]].drop_duplicates().sort_values("site_label")
    site_records = site_tuples.to_dict(orient="records")
    all_sites = [
        {"id": str(r.get("site_id", "")), "name": str(r.get("site_name", "")), "label": str(r.get("site_label", ""))}
        for r in site_records
        if str(r.get("site_label", "")).strip() and str(r.get("site_label")) != "Unknown Site"
    ]
    depts = sorted({_clean_label(x) for x in base_frame["department"].unique() if _clean_label(x) != "—"})
    secs = sorted({_clean_label(x) for x in base_frame["section"].unique() if _clean_label(x) != "—"})
    seasons = sorted({_clean_label(x) for x in base_frame["season"].unique() if _clean_label(x) != "—"})

    # Scope view
    view = base_frame.copy()
    if start_date.strip():
        try:
            sd = pd.to_datetime(start_date.strip())
            view = view[view["date"] >= sd]
        except Exception:
            pass
    if end_date.strip():
        try:
            ed = pd.to_datetime(end_date.strip())
            view = view[view["date"] <= ed]
        except Exception:
            pass
    if site.strip():
        s_val = site.strip().lower()
        view = view[
            (view["site_id"].astype(str).str.lower() == s_val)
            | (view["site_label"].astype(str).str.lower() == s_val)
            | (view["site_name"].astype(str).str.lower() == s_val)
        ]
    if department.strip():
        view = view[view["department"].astype(str).str.strip() == department.strip()]
    if section.strip():
        view = view[view["section"].astype(str).str.strip() == section.strip()]
    if season.strip():
        view = view[view["season"].astype(str).str.strip() == season.strip()]
    if search.strip():
        q = search.strip().lower()
        mask = (
            view["sku"].astype(str).str.lower().str.contains(q, na=False)
            | view["barcode"].astype(str).str.lower().str.contains(q, na=False)
            | view["department"].astype(str).str.lower().str.contains(q, na=False)
            | view["section"].astype(str).str.lower().str.contains(q, na=False)
        )
        view = view[mask]

    # Calculate Weekly boundaries for W-1, W-2, W-3
    valid_dates = view["date"].dropna()
    if not valid_dates.empty:
        max_date = valid_dates.max()
        w1_end = max_date
        w1_start = w1_end - pd.Timedelta(days=7)
        w2_start = w1_start - pd.Timedelta(days=7)
        w3_start = w2_start - pd.Timedelta(days=7)

        w1_mask = (view["date"] > w1_start) & (view["date"] <= w1_end)
        w2_mask = (view["date"] > w2_start) & (view["date"] <= w1_start)
        w3_mask = (view["date"] > w3_start) & (view["date"] <= w2_start)
    else:
        w1_mask = pd.Series(False, index=view.index)
        w2_mask = pd.Series(False, index=view.index)
        w3_mask = pd.Series(False, index=view.index)

    view["w1_qty"] = view["qty"].where(w1_mask, 0)
    view["w2_qty"] = view["qty"].where(w2_mask, 0)
    view["w3_qty"] = view["qty"].where(w3_mask, 0)
    view["gross_amt"] = view["qty"] * view["mrp"]

    # Aggregate to SKU grain
    sku_grp = (
        view.groupby("sku_key", as_index=False)
        .agg(
            sku=("sku", "first"),
            barcode=("barcode", "first"),
            department=("department", "first"),
            section=("section", "first"),
            season=("season", "first"),
            sales=("sales", "sum"),
            qty=("qty", "sum"),
            discount=("discount", "sum"),
            gross_amt=("gross_amt", "sum"),
            mrp=("mrp", "max"),
            w1_qty=("w1_qty", "sum"),
            w2_qty=("w2_qty", "sum"),
            w3_qty=("w3_qty", "sum"),
            first_sale=("date", "min"),
        )
    )

    today = datetime.date.today()
    today_str = today.strftime("%Y-%m-%d")

    # Velocity Thresholds
    pos_qty = sku_grp.loc[sku_grp["qty"] > 0, "qty"]
    if not pos_qty.empty:
        q75 = float(pos_qty.quantile(0.75))
        q25 = float(pos_qty.quantile(0.25))
        fast_threshold = max(q75, 2.0 if site.strip() else 5.0)
        slow_threshold = max(min(q25, 2.0), 1.0)
    else:
        fast_threshold = 2.0
        slow_threshold = 1.0

    # Build enriched records using fast dictionary iteration
    enriched_rows: list[dict[str, Any]] = []
    sku_records = sku_grp.to_dict(orient="records")

    for r in sku_records:
        sku_val = str(r["sku"]).strip().upper()
        bc_val = str(r["barcode"]).strip().upper()
        if bc_val.endswith(".0"):
            bc_val = bc_val[:-2]
        key_val = str(r["sku_key"]).strip().upper()

        # Item master enrichment
        meta_item = im_dict.get(sku_val) or im_dict.get(key_val) or {}
        ref_no = meta_item.get("ref_no") or ""
        prod_type = meta_item.get("product_type") or _clean_label(r["section"])
        story_name = meta_item.get("story_name") or ""
        costing = float(meta_item.get("cost") or 0.0)
        mrp_val = float(meta_item.get("mrp") or r["mrp"] or 0.0)
        launch_date = str(meta_item.get("launch_date") or "").strip()

        if not launch_date and pd.notna(r["first_sale"]):
            try:
                launch_date = pd.to_datetime(r["first_sale"]).strftime("%Y-%m-%d")
            except Exception:
                launch_date = ""

        # Shelf life in days
        shelf_life_days = 0
        if launch_date:
            try:
                ld = datetime.datetime.strptime(launch_date[:10], "%Y-%m-%d").date()
                shelf_life_days = max(0, (today - ld).days)
            except Exception:
                pass

        # SOH calculations (current WH + On-Floor — independent of sales FY filter)
        ho_soh_qty = int(wh_dict.get(sku_val, 0) or wh_dict.get(key_val, 0) or 0)
        floor_soh_qty = int(
            floor_sku_dict.get(sku_val, 0)
            or floor_sku_dict.get(key_val, 0)
            or floor_bc_dict.get(bc_val, 0)
            or 0
        )

        # Sales & Financials (all offline LFS/EBO)
        offline_sold_qty = int(r["qty"])
        offline_net_amt = round(float(r["sales"]), 1)
        offline_gross_amt = round(float(r["gross_amt"] if r["gross_amt"] > 0 else offline_sold_qty * mrp_val), 1)
        discount_amt = round(float(r["discount"]), 1)

        disc_pct = 0.0
        if offline_gross_amt > 0:
            disc_pct = round(max(0.0, (1.0 - (offline_net_amt / offline_gross_amt)) * 100.0), 1)
        elif offline_sold_qty > 0 and discount_amt > 0:
            disc_pct = round(min(100.0, (discount_amt / (offline_net_amt + discount_amt)) * 100.0), 1)

        # Daily movement: dispatch + STR (same definition as Sales Overview / Analyzer)
        dm_m = dm_str_by_sku.get(sku_val) or dm_str_by_sku.get(key_val) or {}
        dm_dispatch = float(dm_m.get("dispatch_in") or 0.0)
        dm_available = float(dm_m.get("available") or 0.0)
        dm_sales_qty = float(dm_m.get("sales_qty") or 0.0)
        # Prefer true dispatch_in from daily movement; fallback only if DM missing for SKU
        dispatch_qty = int(round(dm_dispatch)) if dm_dispatch > 0 else int(floor_soh_qty + offline_sold_qty)
        production_qty = ho_soh_qty + floor_soh_qty + offline_sold_qty

        # Monetary derived values
        prod_amt_cost = round(production_qty * costing, 1)
        prod_amt_mrp = round(production_qty * mrp_val, 1)
        ho_amt_cost = round(ho_soh_qty * costing, 1)
        ho_amt_mrp = round(ho_soh_qty * mrp_val, 1)
        floor_amt_cost = round(floor_soh_qty * costing, 1)
        floor_amt_mrp = round(floor_soh_qty * mrp_val, 1)

        # Sell-through from daily_inventory_movement:
        # STR = sales_qty ÷ (dispatch_in + ibt_in + sales_return_qty) × 100
        if dm_available > 0 or dm_dispatch > 0:
            offline_st_dispatch_pct = float(dm_m.get("dispatch_st_pct") or 0.0)
            total_sell_through_pct = float(dm_m.get("sell_through_pct") or 0.0)
        else:
            offline_st_dispatch_pct = (
                round((offline_sold_qty / dispatch_qty * 100.0), 1) if dispatch_qty > 0 else 0.0
            )
            total_sell_through_pct = (
                round((offline_sold_qty / production_qty * 100.0), 1) if production_qty > 0 else 0.0
            )

        # Weekly sales
        w1_sold = int(r["w1_qty"])
        w2_sold = int(r["w2_qty"])
        w3_sold = int(r["w3_qty"])
        ros = round((w1_sold + w2_sold + w3_sold) / 3.0, 1)

        # Velocity & Offer
        if offline_sold_qty <= 0:
            mover_type_val = "NON_MOVER"
        elif offline_sold_qty >= fast_threshold:
            mover_type_val = "FAST_MOVER"
        elif offline_sold_qty <= slow_threshold:
            mover_type_val = "SLOW_MOVER"
        else:
            mover_type_val = "REGULAR"

        offer_status = "Offer" if disc_pct > 10.0 else "Regular"
        breakeven_val = round(offline_net_amt - prod_amt_cost, 1)

        enriched_rows.append(
            {
                # Product Master
                "ref_no": ref_no,
                "sku": sku_val or key_val,
                "barcode": bc_val,
                "product_type": prod_type,
                "category": _clean_label(r["department"]),
                "department": _clean_label(r["department"]),
                "section": _clean_label(r["section"]),
                "season": _clean_label(r["season"]),
                "story_name": story_name,
                # Timeline
                "launch_date": launch_date,
                "current_date": today_str,
                "shelf_life_days": shelf_life_days,
                "style_analysis": mover_type_val,
                "offer_status": offer_status,
                # Costing & MRP
                "costing": costing,
                "mrp": mrp_val,
                # Production
                "production_qty": production_qty,
                "production_amt_cost": prod_amt_cost,
                "production_amt_mrp": prod_amt_mrp,
                # HO / Warehouse SOH
                "ho_soh_qty": ho_soh_qty,
                "wh_soh": ho_soh_qty,  # alias
                "ho_amt_cost": ho_amt_cost,
                "ho_amt_mrp": ho_amt_mrp,
                # Dispatch & Floor SOH
                "dispatch_qty": dispatch_qty,
                "floor_soh_qty": floor_soh_qty,
                "on_floor": floor_soh_qty,  # alias
                "floor_amt_cost": floor_amt_cost,
                "floor_amt_mrp": floor_amt_mrp,
                # Offline Sales
                "offline_sold_qty": offline_sold_qty,
                "offline_gross_amt": offline_gross_amt,
                "discount_pct": disc_pct,
                "offline_net_amt": offline_net_amt,
                "offline_st_dispatch_pct": offline_st_dispatch_pct,
                # Total Sales (Offline is primary)
                "total_sales_qty": offline_sold_qty,
                "total_net_sales": offline_net_amt,
                "sell_through_pct": total_sell_through_pct,
                # Weekly Velocity
                "ros": ros,
                "wk_minus_1_qty": w1_sold,
                "wk_minus_2_qty": w2_sold,
                "wk_minus_3_qty": w3_sold,
                # Breakeven
                "breakeven_val": breakeven_val,
                # Backward compatibility aliases
                "sales": offline_net_amt,
                "qty": offline_sold_qty,
                "asp": round(offline_net_amt / max(offline_sold_qty, 1), 1),
                "discountPct": disc_pct,
                "mover_type": mover_type_val,
            }
        )

    # Filter mover type if requested
    m_filter = mover_type.strip().upper()
    if m_filter in ("FAST", "FAST_MOVER"):
        filtered_rows = [r for r in enriched_rows if r["style_analysis"] == "FAST_MOVER"]
    elif m_filter in ("SLOW", "SLOW_MOVER"):
        filtered_rows = [r for r in enriched_rows if r["style_analysis"] == "SLOW_MOVER"]
    elif m_filter in ("REGULAR", "MEDIUM"):
        filtered_rows = [r for r in enriched_rows if r["style_analysis"] == "REGULAR"]
    elif m_filter in ("NON_MOVER", "ZERO"):
        filtered_rows = [r for r in enriched_rows if r["style_analysis"] == "NON_MOVER"]
    else:
        filtered_rows = enriched_rows

    # Sort: Fast Movers first, then by net sales descending
    rank_map = {"FAST_MOVER": 1, "SLOW_MOVER": 2, "REGULAR": 3, "NON_MOVER": 4}
    filtered_rows.sort(key=lambda x: (rank_map.get(x["style_analysis"], 5), -x["total_net_sales"]))

    fast_movers = [r for r in enriched_rows if r["style_analysis"] == "FAST_MOVER"]
    fast_movers.sort(key=lambda x: -x["total_net_sales"])
    slow_movers = [r for r in enriched_rows if r["style_analysis"] == "SLOW_MOVER"]
    slow_movers.sort(key=lambda x: (x["offline_sold_qty"], x["total_net_sales"]))
    regular_movers = [r for r in enriched_rows if r["style_analysis"] == "REGULAR"]
    non_movers = [r for r in enriched_rows if r["style_analysis"] == "NON_MOVER"]

    # Summary Totals
    total_net_sales = sum(r["offline_net_amt"] for r in enriched_rows)
    total_qty_sold = sum(r["offline_sold_qty"] for r in enriched_rows)
    total_gross = sum(r["offline_gross_amt"] for r in enriched_rows)
    avg_discount = round(max(0.0, (1.0 - (total_net_sales / total_gross)) * 100.0), 1) if total_gross > 0 else 0.0

    # Department & Section Aggregations
    tmp = view.copy()
    tmp["dept_name"] = tmp["department"].map(_clean_label)
    tmp["sec_name"] = tmp["section"].map(_clean_label)

    by_dept = (
        tmp.groupby("dept_name", as_index=False)
        .agg(sales=("sales", "sum"), qty=("qty", "sum"))
        .rename(columns={"dept_name": "name"})
        .sort_values("sales", ascending=False)
        .head(12)
    )
    by_sec = (
        tmp.groupby("sec_name", as_index=False)
        .agg(sales=("sales", "sum"), qty=("qty", "sum"))
        .rename(columns={"sec_name": "name"})
        .sort_values("sales", ascending=False)
        .head(12)
    )

    by_dept_records = by_dept.to_dict(orient="records")
    by_sec_records = by_sec.to_dict(orient="records")

    res = {
        "ok": True,
        "source": "Sales-Master + ITEM_MASTER + WAREHOUSE_SOH",
        "scope": {
            "site": site.strip(),
            "department": department.strip(),
            "section": section.strip(),
            "season": season.strip(),
            "mover_type": mover_type.strip(),
            "startDate": start_date.strip(),
            "endDate": end_date.strip(),
        },
        "summary": {
            "sales": round(total_net_sales, 1),
            "qty": int(total_qty_sold),
            "asp": round(total_net_sales / max(total_qty_sold, 1), 1),
            "discountPct": avg_discount,
            "skuCount": len(enriched_rows),
            "deptCount": int(view["department"].nunique()),
            "siteCount": int(view["site_id"].nunique()),
            "fastMoverCount": len(fast_movers),
            "fastMoverSales": round(sum(r["total_net_sales"] for r in fast_movers), 1),
            "slowMoverCount": len(slow_movers),
            "slowMoverSales": round(sum(r["total_net_sales"] for r in slow_movers), 1),
            "regularMoverCount": len(regular_movers),
            "regularMoverSales": round(sum(r["total_net_sales"] for r in regular_movers), 1),
            "nonMoverCount": len(non_movers),
            "nonMoverSales": round(sum(r["total_net_sales"] for r in non_movers), 1),
        },
        "byDepartment": [
            {"name": str(r["name"]), "sales": round(float(r["sales"]), 1), "qty": int(r["qty"])}
            for r in by_dept_records
        ],
        "bySection": [
            {"name": str(r["name"]), "sales": round(float(r["sales"]), 1), "qty": int(r["qty"])}
            for r in by_sec_records
        ],
        "fastMovers": fast_movers[:50],
        "slowMovers": slow_movers[:50],
        "rows": filtered_rows[:limit],
        "filters": {
            "sites": all_sites,
            "departments": depts,
            "sections": secs,
            "seasons": seasons,
            "minDate": min_date_str,
            "maxDate": max_date_str,
            "year": year,
            "financialYear": f"FY{str(year)[-2:]}",
        },
        "total": len(filtered_rows),
    }

    with _CACHE_LOCK:
        _OVERVIEW_CACHE[cache_key] = (now_ts, res)
    try:
        from api.page_response_cache import save_page_cache

        if res.get("ok"):
            save_page_cache("product-performance", cache_key, res)
    except Exception as exc:
        logger.warning("Product performance page_cache save skipped: %s", exc)

    return res


def warm_product_performance_overview(year: int = 2027, limit: int = 1500) -> dict[str, Any]:
    """Pre-build default Product Performance overview into page_cache (disk + blob)."""
    from api.page_response_cache import _REBUILD

    token = _REBUILD.set(True)
    t0 = time.perf_counter()
    try:
        with _CACHE_LOCK:
            _OVERVIEW_CACHE.clear()
        payload = get_product_performance_overview(
            year=year,
            limit=limit,
            site="",
            department="",
            section="",
            season="",
            search="",
            mover_type="",
            start_date="",
            end_date="",
            refresh=1,
        )
        ok = bool(isinstance(payload, dict) and payload.get("ok"))
        logger.info(
            "Product performance warm ok=%s rows=%s in %.1fs",
            ok,
            len(payload.get("rows") or []) if isinstance(payload, dict) else 0,
            time.perf_counter() - t0,
        )
        return {"ok": ok, "rows": len(payload.get("rows") or []) if isinstance(payload, dict) else 0}
    except Exception as exc:
        logger.warning("Product performance warm failed: %s", exc)
        return {"ok": False, "error": str(exc)}
    finally:
        _REBUILD.reset(token)


# ─────────────────────────────────────────────────────────────────────────────
# 5. FAST SKU HOVER CARD INTELLIGENCE ENDPOINT
# ─────────────────────────────────────────────────────────────────────────────
_SKU_CARD_CACHE: dict[str, dict[str, Any]] = {}
_SKU_CARD_CACHE_KEY: str = ""


@router.get("/sku-card/{sku}")
def get_sku_hover_card(sku: str) -> dict[str, Any]:
    """
    Get comprehensive instant SKU intelligence for UI hover popovers:
    Includes Item Master metadata, SOH, FY-27 velocity, FY-26 baseline,
    YoY growth, sell-through %, and breakeven surplus/deficit.
    """
    global _SKU_CARD_CACHE, _SKU_CARD_CACHE_KEY
    clean_sku = str(sku or "").strip().upper()
    if not clean_sku:
        return {"ok": False, "error": "SKU required"}

    # 1. Load Data
    im_df = _load_item_master_meta()
    wh_df = _load_wh_soh_sku()
    fl_df = _load_floor_soh_sku()
    sales_df = _load_full_sales_data(year_val=2027)

    # Cache key based on file/data stamps
    cache_key = f"{len(im_df)}_{len(sales_df)}_{len(wh_df)}_{len(fl_df)}"
    with _CACHE_LOCK:
        if _SKU_CARD_CACHE_KEY != cache_key:
            _SKU_CARD_CACHE.clear()
            _SKU_CARD_CACHE_KEY = cache_key
        if clean_sku in _SKU_CARD_CACHE:
            return _SKU_CARD_CACHE[clean_sku]

    # Item Master Lookup
    im_row = {}
    if not im_df.empty and "sku" in im_df.columns:
        m = im_df[im_df["sku"].astype(str).str.strip().str.upper() == clean_sku]
        if not m.empty:
            im_row = m.iloc[0].to_dict()
        elif "barcode" in im_df.columns:
            m_bc = im_df[im_df["barcode"].astype(str).str.strip().str.upper() == clean_sku]
            if not m_bc.empty:
                im_row = m_bc.iloc[0].to_dict()

    # WH SOH Lookup
    ho_soh_qty = 0
    if not wh_df.empty and "sku" in wh_df.columns:
        m_wh = wh_df[wh_df["sku"].astype(str).str.strip().str.upper() == clean_sku]
        if not m_wh.empty:
            ho_soh_qty = int(m_wh["ho_soh_qty"].sum())

    # Floor SOH Lookup
    floor_soh_qty = 0
    if not fl_df.empty:
        if "sku" in fl_df.columns:
            m_fl = fl_df[fl_df["sku"].astype(str).str.strip().str.upper() == clean_sku]
            if not m_fl.empty:
                floor_soh_qty = int(m_fl["floor_soh_qty"].sum())
        if floor_soh_qty == 0 and "barcode" in fl_df.columns:
            m_fl_bc = fl_df[fl_df["barcode"].astype(str).str.strip().str.upper() == clean_sku]
            if not m_fl_bc.empty:
                floor_soh_qty = int(m_fl_bc["floor_soh_qty"].sum())

    # Sales Lookup
    sku_sales = pd.DataFrame()
    if not sales_df.empty:
        sku_mask = (
            (sales_df["sku"].astype(str).str.strip().str.upper() == clean_sku)
            | (sales_df["sku_key"].astype(str).str.strip().str.upper() == clean_sku)
            | (sales_df["barcode"].astype(str).str.strip().str.upper() == clean_sku)
        )
        sku_sales = sales_df[sku_mask].copy()

    # Basic Info
    sku_actual = str(im_row.get("sku") or (sku_sales["sku"].iloc[0] if not sku_sales.empty else clean_sku)).strip()
    barcode_actual = str(im_row.get("barcode") or (sku_sales["barcode"].iloc[0] if not sku_sales.empty and "barcode" in sku_sales.columns else "")).strip()
    ref_no = str(im_row.get("ref_no") or "").strip()
    prod_type = str(im_row.get("product_type") or (sku_sales["section"].iloc[0] if not sku_sales.empty and "section" in sku_sales.columns else "Apparel")).strip()
    story_name = str(im_row.get("story_name") or "").strip()
    department = str(im_row.get("department") or (sku_sales["department"].iloc[0] if not sku_sales.empty and "department" in sku_sales.columns else "—")).strip()
    section = str(im_row.get("section") or (sku_sales["section"].iloc[0] if not sku_sales.empty and "section" in sku_sales.columns else "—")).strip()
    season = str(im_row.get("season") or (sku_sales["season"].iloc[0] if not sku_sales.empty and "season" in sku_sales.columns else "—")).strip()
    costing = float(im_row.get("cost") or 0.0)
    mrp_val = float(im_row.get("mrp") or (sku_sales["mrp"].max() if not sku_sales.empty and "mrp" in sku_sales.columns else 0.0))
    launch_date = str(im_row.get("launch_date") or "").strip()

    if not launch_date and not sku_sales.empty:
        v_dates = sku_sales["date"].dropna()
        if not v_dates.empty:
            launch_date = v_dates.min().strftime("%Y-%m-%d")

    # Shelf Life
    shelf_life_days = 0
    if launch_date:
        try:
            ld = pd.to_datetime(launch_date)
            shelf_life_days = max(0, (pd.Timestamp.now() - ld).days)
        except Exception:
            pass

    # Sales Breakdown by Fiscal Year
    fy27_start = pd.Timestamp("2026-04-01")
    fy27_end = pd.Timestamp("2027-03-31 23:59:59")
    fy26_start = pd.Timestamp("2025-04-01")
    fy26_end = pd.Timestamp("2026-03-31 23:59:59")

    # FY-27 metrics
    fy27_df = sku_sales[(sku_sales["date"] >= fy27_start) & (sku_sales["date"] <= fy27_end)] if not sku_sales.empty else pd.DataFrame()
    fy27_qty = int(fy27_df["qty"].sum()) if not fy27_df.empty else 0
    fy27_sales = round(float(fy27_df["sales"].sum()), 1) if not fy27_df.empty else 0.0
    fy27_gross = round(float((fy27_df["qty"] * fy27_df["mrp"]).sum()), 1) if not fy27_df.empty and "mrp" in fy27_df.columns else round(fy27_qty * mrp_val, 1)
    fy27_asp = round(fy27_sales / max(fy27_qty, 1), 1) if fy27_qty > 0 else 0.0
    fy27_discount_pct = round(max(0.0, (1.0 - (fy27_sales / max(fy27_gross, 1.0))) * 100.0), 1) if fy27_gross > 0 else 0.0

    # FY-26 metrics
    fy26_df = sku_sales[(sku_sales["date"] >= fy26_start) & (sku_sales["date"] <= fy26_end)] if not sku_sales.empty else pd.DataFrame()
    fy26_qty = int(fy26_df["qty"].sum()) if not fy26_df.empty else 0
    fy26_sales = round(float(fy26_df["sales"].sum()), 1) if not fy26_df.empty else 0.0
    fy26_gross = round(float((fy26_df["qty"] * fy26_df["mrp"]).sum()), 1) if not fy26_df.empty and "mrp" in fy26_df.columns else round(fy26_qty * mrp_val, 1)
    fy26_asp = round(fy26_sales / max(fy26_qty, 1), 1) if fy26_qty > 0 else 0.0
    fy26_discount_pct = round(max(0.0, (1.0 - (fy26_sales / max(fy26_gross, 1.0))) * 100.0), 1) if fy26_gross > 0 else 0.0

    # YoY Growth Calculation
    yoy_qty_growth_pct: float | None = None
    yoy_sales_growth_pct: float | None = None
    if fy26_qty > 0:
        yoy_qty_growth_pct = round(((fy27_qty - fy26_qty) / fy26_qty) * 100.0, 1)
    if fy26_sales > 0:
        yoy_sales_growth_pct = round(((fy27_sales - fy26_sales) / fy26_sales) * 100.0, 1)

    # All-time / Primary Offline totals
    total_sold_qty = int(sku_sales["qty"].sum()) if not sku_sales.empty else 0
    total_net_sales = round(float(sku_sales["sales"].sum()), 1) if not sku_sales.empty else 0.0
    total_gross_sales = round(float((sku_sales["qty"] * sku_sales["mrp"]).sum()), 1) if not sku_sales.empty and "mrp" in sku_sales.columns else round(total_sold_qty * mrp_val, 1)
    total_discount_pct = round(max(0.0, (1.0 - (total_net_sales / max(total_gross_sales, 1.0))) * 100.0), 1) if total_gross_sales > 0 else 0.0

    # Daily movement STR + dispatch (same as Sales Overview)
    _dm_str_map = _load_daily_str_by_sku()
    dm_str = _dm_str_map.get(clean_sku) or _dm_str_map.get(str(sku_actual).strip().upper()) or {}
    dm_dispatch = float(dm_str.get("dispatch_in") or 0.0)
    dm_available = float(dm_str.get("available") or 0.0)
    dispatch_qty = int(round(dm_dispatch)) if dm_dispatch > 0 else int(floor_soh_qty + total_sold_qty)
    production_qty = ho_soh_qty + floor_soh_qty + total_sold_qty
    prod_amt_cost = round(production_qty * costing, 1)
    prod_amt_mrp = round(production_qty * mrp_val, 1)
    ho_amt_cost = round(ho_soh_qty * costing, 1)
    floor_amt_cost = round(floor_soh_qty * costing, 1)

    # Sell-Through from daily_inventory_movement
    if dm_available > 0 or dm_dispatch > 0:
        total_st_pct = float(dm_str.get("sell_through_pct") or 0.0)
        dispatch_st_pct = float(dm_str.get("dispatch_st_pct") or 0.0)
    else:
        total_st_pct = round((total_sold_qty / max(production_qty, 1)) * 100.0, 2) if production_qty > 0 else 0.0
        dispatch_st_pct = round((total_sold_qty / max(dispatch_qty, 1)) * 100.0, 2) if dispatch_qty > 0 else 0.0

    # Rate of Sale & Weekly Velocity
    active_stores = int(sku_sales["site_id"].nunique()) if not sku_sales.empty and "site_id" in sku_sales.columns else (1 if total_sold_qty > 0 else 0)
    ros = round(total_sold_qty / max(shelf_life_days / 7.0, 1.0), 1) if shelf_life_days > 0 else float(total_sold_qty)

    # Weekly buckets
    w1_qty, w2_qty, w3_qty = 0, 0, 0
    if not sku_sales.empty:
        v_dates = sku_sales["date"].dropna()
        if not v_dates.empty:
            max_d = v_dates.max()
            w1_start = max_d - pd.Timedelta(days=7)
            w2_start = w1_start - pd.Timedelta(days=7)
            w3_start = w2_start - pd.Timedelta(days=7)
            w1_qty = int(sku_sales[(sku_sales["date"] > w1_start) & (sku_sales["date"] <= max_d)]["qty"].sum())
            w2_qty = int(sku_sales[(sku_sales["date"] > w2_start) & (sku_sales["date"] <= w1_start)]["qty"].sum())
            w3_qty = int(sku_sales[(sku_sales["date"] > w3_start) & (sku_sales["date"] <= w2_start)]["qty"].sum())

    # Style Analysis & Breakeven
    style_analysis = "REGULAR"
    if total_sold_qty >= 10:
        style_analysis = "FAST_MOVER"
    elif total_sold_qty <= 2 and total_sold_qty > 0:
        style_analysis = "SLOW_MOVER"
    elif total_sold_qty == 0:
        style_analysis = "NON_MOVER"

    breakeven_val = round(total_net_sales - prod_amt_cost, 1)

    result = {
        "ok": True,
        "sku": sku_actual,
        "barcode": barcode_actual,
        "ref_no": ref_no,
        "product_type": prod_type,
        "category": department if department != "—" else section,
        "department": department,
        "section": section,
        "season": season,
        "story_name": story_name,
        "image_url": f"/api/product-images/{sku_actual}",
        "launch_date": launch_date,
        "shelf_life_days": shelf_life_days,
        "style_analysis": style_analysis,
        "offer_status": "Offer" if total_discount_pct >= 25 else "Regular",
        "costing": costing,
        "mrp": mrp_val,
        # Inventory SOH
        "ho_soh_qty": ho_soh_qty,
        "ho_amt_cost": ho_amt_cost,
        "floor_soh_qty": floor_soh_qty,
        "floor_amt_cost": floor_amt_cost,
        "dispatch_qty": dispatch_qty,
        "production_qty": production_qty,
        "production_amt_cost": prod_amt_cost,
        "production_amt_mrp": prod_amt_mrp,
        # FY-27 Current Performance
        "fy27": {
            "qty": fy27_qty,
            "sales": fy27_sales,
            "gross": fy27_gross,
            "asp": fy27_asp,
            "discount_pct": fy27_discount_pct,
        },
        # FY-26 Baseline Performance
        "fy26": {
            "qty": fy26_qty,
            "sales": fy26_sales,
            "gross": fy26_gross,
            "asp": fy26_asp,
            "discount_pct": fy26_discount_pct,
        },
        # YoY Growth
        "yoy_qty_growth_pct": yoy_qty_growth_pct,
        "yoy_sales_growth_pct": yoy_sales_growth_pct,
        # All-time / Aggregate Sales
        "total_sold_qty": total_sold_qty,
        "total_net_sales": total_net_sales,
        "total_discount_pct": total_discount_pct,
        "sell_through_pct": total_st_pct,
        "offline_st_dispatch_pct": dispatch_st_pct,
        "ros": ros,
        "wk_minus_1_qty": w1_qty,
        "wk_minus_2_qty": w2_qty,
        "wk_minus_3_qty": w3_qty,
        "breakeven_val": breakeven_val,
        "active_stores_count": active_stores,
    }

    with _CACHE_LOCK:
        _SKU_CARD_CACHE[clean_sku] = result

    return result

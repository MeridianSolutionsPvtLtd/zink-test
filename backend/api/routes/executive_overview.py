"""
Executive Overview Real-Time Analytics API
Provides:
  1. Multi-Year Sales Comparison (2024 vs 2025 vs 2026 YTD) from SALES_MASTER & COMBINED_SALES
  2. Target vs Achievement grouped by Zone and Region with 2025/2026 year toggle
  3. Real-Time Executive Summary KPIs with actual YoY Growth (2025 vs 2026)
  4. Real-Time Breakdowns by Channel, Zone, Department, and Top/Bottom Stores
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd
from fastapi import APIRouter, Query, HTTPException, Request

from api.routes import target_vs_achieved
from api.sales_year_loader import DEFAULT_YEAR, load_sales_for_year, normalize_dashboard_year
from pipeline.customer_master import load_customer_master
from pipeline.item_master import build_sku_image_map
from api.season_utils import get_previous_season

logger = logging.getLogger(__name__)

router = APIRouter()

_HOME_SALES_CACHE: dict[str, tuple[float, pd.DataFrame]] = {}
_HOME_SALES_ENGINE_CACHE: dict[str, str] = {}
_HOME_SALES_INFLIGHT: dict[str, threading.Event] = {}
_HOME_SALES_LOCK = threading.Lock()
_HOME_SALES_TTL = 300.0
_HOME_SALES_CACHE_MAX = 6

_EXEC_CACHE: dict[str, tuple[float, Any]] = {}
_EXEC_CACHE_LOCK = threading.Lock()
_EXEC_CACHE_TTL = 300.0
_EXEC_INFLIGHT: dict[str, threading.Event] = {}


def _user_filter_active(request: Request) -> bool:
    """A site, zone, channel, or other cut must be calculated, not replayed from cache."""
    q = request.query_params

    def picked(name: str, *, all_ok: bool = False) -> bool:
        raw = str(q.get(name) or "").strip()
        if not raw:
            return False
        if all_ok and raw.lower() == "all":
            return False
        return True

    if any(
        picked(name, all_ok=all_ok)
        for name, all_ok in (
            ("site_ids", False),
            ("site_id", True),
            ("zones", False),
            ("zone", True),
            ("channel", True),
            ("season", True),
            ("sku", True),
            ("barcode", True),
            ("barcodes", False),
            ("departments", False),
            ("sections", False),
            ("store_grades", False),
            ("states", False),
            ("cities", False),
            ("search", False),
            ("month", True),
        )
    ):
        return True
    mode = str(q.get("timeline_mode") or "").strip().lower()
    return bool(mode) and mode not in {"fy_year", "fy", "financial_year"}


def _query_items_for_cache(request: Request) -> list[tuple[str, str]]:
    """Stable query tuple — `refresh` must not mint a second cache file."""
    return sorted(
        (str(k), str(v))
        for k, v in request.query_params.items()
        if str(k).strip().lower() != "refresh"
    )


def _home_cache_key(prefix: str, request: Request, year: int) -> str:
    """Stable across App Service workers; busts when morning daily stamp advances.

    Do NOT embed sales_data_revision here — each worker's local Sales Master etag
    can differ mid-day, which minted divergent keys so Refresh on one instance
    never replaced the JSON other instances kept serving.
    """
    from api.page_response_cache import _local_daily_stamp_token

    stamp = _local_daily_stamp_token() or "nostamp"
    # year kept so FY toggles cannot collide if stamp is empty/shared.
    return f"{prefix}:y{int(year)}:{stamp}:{_query_items_for_cache(request)}"


def _refresh_requested(request: Request | None) -> bool:
    if request is None:
        return False
    raw = str(request.query_params.get("refresh") or "").strip().lower()
    return raw in {"1", "true", "yes"}


def _exec_cached(request: Request, namespace: str, cache_key: str) -> dict | None:
    from api.page_response_cache import load_page_cache, rebuilding_page_cache

    if request is not None and _user_filter_active(request):
        return None
    if _refresh_requested(request):
        return None
    if rebuilding_page_cache():
        return None
    disk = load_page_cache(namespace, cache_key)
    if isinstance(disk, dict):
        if namespace == "home-breakdowns" and "top_skus" in disk:
            top = disk.get("top_skus")
            if top and isinstance(top, list) and isinstance(top[0], dict) and "stock" not in top[0]:
                return None
        with _EXEC_CACHE_LOCK:
            _EXEC_CACHE[cache_key] = (time.time(), disk)
        return disk
    now = time.time()
    with _EXEC_CACHE_LOCK:
        hit = _EXEC_CACHE.get(cache_key)
        if hit and (now - hit[0] < _EXEC_CACHE_TTL):
            if namespace == "home-breakdowns" and "top_skus" in hit[1]:
                top = hit[1].get("top_skus")
                if top and isinstance(top, list) and isinstance(top[0], dict) and "stock" not in top[0]:
                    return None
            return hit[1]
    return None


def _exec_claim(cache_key: str) -> tuple[bool, threading.Event]:
    """Single-flight: only one thread rebuilds a Home page-cache key."""
    with _EXEC_CACHE_LOCK:
        existing = _EXEC_INFLIGHT.get(cache_key)
        if existing is not None:
            return False, existing
        ready = threading.Event()
        _EXEC_INFLIGHT[cache_key] = ready
        return True, ready


def _exec_release(cache_key: str, ready: threading.Event) -> None:
    with _EXEC_CACHE_LOCK:
        if _EXEC_INFLIGHT.get(cache_key) is ready:
            _EXEC_INFLIGHT.pop(cache_key, None)
    ready.set()


def _exec_wait_or_build(
    request: Request, namespace: str, cache_key: str
) -> tuple[dict | None, bool, threading.Event | None]:
    """Return (cached, is_leader, ready). Followers wait for the leader's store."""
    cached = _exec_cached(request, namespace, cache_key)
    if cached is not None:
        return cached, False, None
    if request is not None and _user_filter_active(request):
        return None, True, None
    if _refresh_requested(request):
        return None, True, None
    leader, ready = _exec_claim(cache_key)
    if leader:
        return None, True, ready
    ready.wait(timeout=120.0)
    cached = _exec_cached(request, namespace, cache_key)
    if cached is not None:
        return cached, False, None
    # Leader failed or timed out — compute ourselves without holding their event.
    return None, True, None


def _exec_store(request: Request, namespace: str, cache_key: str, res: dict) -> None:
    from api.page_response_cache import force_page_cache_blob_sync, save_page_cache

    if request is not None and _user_filter_active(request):
        return
    with _EXEC_CACHE_LOCK:
        _EXEC_CACHE[cache_key] = (time.time(), res)
    # refresh=1: sync blob so every App Service worker picks up the new Home JSON.
    if _refresh_requested(request):
        with force_page_cache_blob_sync():
            save_page_cache(namespace, cache_key, res)
        return
    save_page_cache(namespace, cache_key, res)


# Last Home sales query engine for response diagnostics (per-thread).
_HOME_QUERY_ENGINE = threading.local()


def _home_sales_query_engine() -> str:
    return getattr(_HOME_QUERY_ENGINE, "value", "pandas") or "pandas"


def _home_sales_cached(cache_key: str) -> pd.DataFrame | None:
    now = time.time()
    with _HOME_SALES_LOCK:
        hit = _HOME_SALES_CACHE.get(cache_key)
        if not hit or (now - hit[0]) >= _HOME_SALES_TTL:
            return None
        _HOME_QUERY_ENGINE.value = _HOME_SALES_ENGINE_CACHE.get(cache_key, "pandas")
        return hit[1].copy()


def _home_sales_claim(cache_key: str) -> tuple[bool, threading.Event]:
    with _HOME_SALES_LOCK:
        existing = _HOME_SALES_INFLIGHT.get(cache_key)
        if existing is not None:
            return False, existing
        ready = threading.Event()
        _HOME_SALES_INFLIGHT[cache_key] = ready
        return True, ready


def _home_sales_store(cache_key: str, frame: pd.DataFrame, engine: str) -> None:
    with _HOME_SALES_LOCK:
        _HOME_SALES_CACHE[cache_key] = (time.time(), frame)
        _HOME_SALES_ENGINE_CACHE[cache_key] = engine
        while len(_HOME_SALES_CACHE) > _HOME_SALES_CACHE_MAX:
            oldest = min(_HOME_SALES_CACHE, key=lambda key: _HOME_SALES_CACHE[key][0])
            _HOME_SALES_CACHE.pop(oldest, None)
            _HOME_SALES_ENGINE_CACHE.pop(oldest, None)


def _home_sales_release(cache_key: str, ready: threading.Event) -> None:
    with _HOME_SALES_LOCK:
        if _HOME_SALES_INFLIGHT.get(cache_key) is ready:
            _HOME_SALES_INFLIGHT.pop(cache_key, None)
    ready.set()


_HOME_SALES_FRAME_PARAMS = (
    "site_ids", "channel", "season", "sku", "barcodes", "departments", "sections",
    "zones", "store_grades", "states", "cities",
)


def _home_sql_filters(request: Request, year: int) -> dict[str, Any] | None:
    """Azure SQL filter dict for a filtered Home view; None on the default (unfiltered) view."""
    from api.routes.sales_dashboard import _fy_years_for_timeline

    q = request.query_params
    mode = q.get("timeline_mode", "fy_year")
    values = q.get("timeline_values", f"FY{year % 100:02d}")
    zone = q.get("zone", "all").strip()
    f = {k: q.get(k) or "" for k in (
        "site_ids", "channel", "season", "sku", "barcodes", "departments", "sections",
        "store_grades", "states", "cities",
    )}
    f["zones"] = q.get("zones") or (zone if zone and zone.lower() != "all" else "")
    active = any(
        str(v).strip() and not (k in ("channel", "sku") and str(v).strip().lower() == "all")
        for k, v in f.items()
    )
    if not active:
        return None
    f["fy_years"] = _fy_years_for_timeline(year_val=year, timeline_mode=mode, timeline_values=values)
    f["timeline_mode"] = mode
    f["timeline_values"] = values
    return f


def _home_sales(request: Request, year: int, *, prior: bool = False) -> pd.DataFrame:
    """Use the same full-data timeline and product filters as Sales Overview."""
    from api.sales_year_loader import sales_data_revision

    q = request.query_params
    mode = q.get("timeline_mode", "fy_year")
    values = q.get("timeline_values", f"FY{year % 100:02d}")
    zone = q.get("zone", "all").strip()
    # Bust in-memory Home sales when Sales Master etag/mtime changes.
    rev = sales_data_revision(year if not prior else int(year) - 1)
    # Only params that shape the frame: page/sort/search/view/group_by must not split the
    # cache, otherwise every Home card re-pulls the same filtered rows from SQL.
    q_items = tuple((k, q.get(k) or "") for k in _HOME_SALES_FRAME_PARAMS)
    cache_key = f"{year}|{prior}|{mode}|{values}|{zone}|{rev}|{q_items}"
    # Filtered cards share this frame in memory. The finished JSON is still not
    # written to page cache, so a filter is never served as the saved Home page.
    if not _refresh_requested(request):
        cached = _home_sales_cached(cache_key)
        if cached is not None:
            return cached
    else:
        with _HOME_SALES_LOCK:
            _HOME_SALES_CACHE.pop(cache_key, None)
            _HOME_SALES_ENGINE_CACHE.pop(cache_key, None)
    leader, ready = _home_sales_claim(cache_key)
    if not leader:
        ready.wait(timeout=120.0)
        cached = _home_sales_cached(cache_key)
        if cached is not None:
            return cached

    try:
        return _home_sales_load(
            request,
            year,
            prior=prior,
            cache_key=cache_key,
            leader=leader,
            mode=mode,
            values=values,
            zone=zone,
        )
    finally:
        if leader:
            _home_sales_release(cache_key, ready)


def _home_sales_load(
    request: Request,
    year: int,
    *,
    prior: bool,
    cache_key: str,
    leader: bool,
    mode: str,
    values: str,
    zone: str,
) -> pd.DataFrame:
    from api.routes.sales_dashboard import (
        _load_sales_for_timeline, _apply_filters, _apply_timeline_filter,
        _load_sply_prior_df,
    )
    q = request.query_params
    filters = {k: q.get(k) for k in (
        "site_ids", "channel", "season", "sku", "barcodes", "departments", "sections",
    )}
    extra_filters: dict[str, Any] = {}
    if q.get("zones"):
        extra_filters["zones"] = q.get("zones")
    if q.get("store_grades"):
        extra_filters["store_grades"] = q.get("store_grades")
    if q.get("states"):
        extra_filters["states"] = q.get("states")
    if q.get("cities"):
        extra_filters["cities"] = q.get("cities")
    prior_filter_kw = {**filters, **extra_filters}

    frame: pd.DataFrame | None = None
    engine_used = "pandas"
    zones_csv = extra_filters.get("zones") or (
        zone if zone and zone.lower() != "all" else ""
    )

    # Azure SQL only when Home filters are applied. Default first view uses page-cache.
    sql_used = False
    sql_filters = _home_sql_filters(request, year)
    if sql_filters is not None:
        try:
            from api.azure_sql_analytics import (
                sql_load_filtered_sales_df,
                sql_load_sply_sales_df,
                sql_query_ready,
                sql_sales_date_bounds,
            )

            if sql_query_ready():
                if prior:
                    # SPLY anchor = current filtered date range. MIN/MAX in SQL lets the
                    # prior frame load in parallel with the current one.
                    min_d, max_d = sql_sales_date_bounds(sql_filters)
                    frame = sql_load_sply_sales_df(min_date=min_d, max_date=max_d, filters=sql_filters)
                else:
                    frame = sql_load_filtered_sales_df(sql_filters)
                engine_used = "azure_sql"
                sql_used = True
        except Exception as sql_exc:
            logger.warning("Executive Azure SQL sales path failed; fallback: %s", sql_exc)
            frame = None
            engine_used = "pandas"

    if frame is None and not prior:
        try:
            from api.services.analytics_query_engine import load_filtered_sales_duckdb, use_duckdb
            from api.routes.sales_dashboard import _fy_years_for_timeline

            if use_duckdb():
                fy_years = _fy_years_for_timeline(
                    year_val=year, timeline_mode=mode, timeline_values=values
                )
                frame, _diag = load_filtered_sales_duckdb(
                    fy_years=fy_years,
                    site_ids=filters.get("site_ids") or "",
                    channel=filters.get("channel") or "",
                    season=filters.get("season") or "",
                    sku=filters.get("sku") or "",
                    barcodes=filters.get("barcodes") or "",
                    departments=filters.get("departments") or "",
                    sections=filters.get("sections") or "",
                    zones=zones_csv or "",
                    store_grades=extra_filters.get("store_grades") or "",
                    states=extra_filters.get("states") or "",
                    cities=extra_filters.get("cities") or "",
                    timeline_mode=mode,
                    timeline_values=values,
                )
                engine_used = "duckdb"
        except Exception as duck_exc:
            logger.warning("Executive DuckDB sales path failed; pandas fallback: %s", duck_exc)
            frame = None
            engine_used = "pandas"

    if frame is None:
        engine_used = "pandas"
        frame = _load_sales_for_timeline(
            year_val=year,
            timeline_mode=mode,
            timeline_values=values,
            force_refresh=_refresh_requested(request),
        )
        frame = _apply_filters(frame, **filters, **extra_filters)
        frame = _apply_timeline_filter(frame, timeline_mode=mode, timeline_values=values)
        if zone.lower() != "all" and "zone" in frame.columns:
            frame = frame[frame["zone"].astype(str).str.strip().str.upper().eq(zone.upper())]
    if prior and not sql_used:
        frame = _load_sply_prior_df(
            frame,
            filter_kw=prior_filter_kw,
            timeline_mode=mode,
            timeline_values=values,
        )
        if zone.lower() != "all" and "zone" in frame.columns:
            frame = frame[frame["zone"].astype(str).str.strip().str.upper().eq(zone.upper())]
    # Azure SQL / DuckDB frames expose channel/sales_channel; Home charts need store_type.
    if frame is not None and not frame.empty and "store_type" not in frame.columns:
        for src in ("sales_channel", "channel"):
            if src in frame.columns:
                frame = frame.copy()
                frame["store_type"] = frame[src].astype(str).str.strip()
                break
    if not prior:
        _HOME_QUERY_ENGINE.value = engine_used
    if leader:
        _home_sales_store(cache_key, frame, engine_used if (not prior or sql_used) else "pandas")
    return frame.copy()


def _home_targets(request: Request, frame: pd.DataFrame) -> pd.DataFrame:
    from api.routes.sales_dashboard import _apply_timeline_filter
    return _apply_timeline_filter(frame.copy(),
        timeline_mode=request.query_params.get("timeline_mode"),
        timeline_values=request.query_params.get("timeline_values"))

# Max calendar month available for 2026 YTD comparisons (Aug)
_YTD_MONTH_CUTOFF_2026 = 8

_VALID_CHANNELS = {"PT", "SSL", "CENTRO", "ICONIC", "EBO"}

_CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "api_cache"
_HISTORICAL_CACHE = _CACHE_DIR / "historical_sales_summary.parquet"


def _build_historical_summary() -> pd.DataFrame:
    """Build lightweight (~15k rows) monthly historical summary per store from 2024 to 2026."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    backend_root = Path(__file__).resolve().parents[2]
    
    try:
        from pipeline.combine_transfer import detect_channel, CENTRO_CODES
    except ImportError:
        from combine_transfer import detect_channel, CENTRO_CODES  # type: ignore

    # Load Customer Master for site metadata (channel, zone, state, city)
    cm = load_customer_master(retail_only=False)
    cm_map: dict[str, dict[str, str]] = {}
    for _, r in cm.iterrows():
        ch = str(r["channel"]).strip() if pd.notna(r["channel"]) else ""
        if not ch or ch in ["nan", "None"]:
            ch = str(r.get("store_type_master", "")).strip()
        zn = str(r["zone"]).strip() if pd.notna(r["zone"]) else ""
        st = str(r["state"]).strip() if pd.notna(r["state"]) else ""
        ct = str(r["city"]).strip() if pd.notna(r["city"]) else ""
        sn = str(r["site_name_master"]).strip() if pd.notna(r["site_name_master"]) else ""

        keys = [
            str(r.get("site_code", "")),
            str(r.get("site_short_code", "")),
            str(r.get("site_manual_code", "")),
            str(r.get("_site_key", "")),
        ]
        for k in keys:
            if k and k != "nan":
                k_clean = k.replace(".0", "").strip().upper()
                if k_clean and k_clean not in cm_map:
                    cm_map[k_clean] = {"channel": ch, "zone": zn, "state": st, "city": ct, "site_name": sn}

    records: list[pd.DataFrame] = []

    # 1. SALES_MASTER (covers 2024 and 2025)
    sales_master_paths = [
        backend_root / "pipeline" / "input_files" / "SSI_PT_CENTRO_ICONIC_EBO_SALES_MASTER.csv",
        backend_root / "pipeline" / "input_files" / "SSI_PT_CENTRO_ICONIC_SALES_MASTER.csv",
        backend_root / "input" / "SSI_PT_CENTRO_ICONIC_EBO_SALES_MASTER.csv",
        backend_root / "input" / "SSI_PT_CENTRO_ICONIC_SALES_MASTER.csv",
        backend_root / "input_files" / "SSI_PT_CENTRO_ICONIC_EBO_SALES_MASTER.csv",
        backend_root / "input_files" / "SSI_PT_CENTRO_ICONIC_SALES_MASTER.csv",
    ]
    master_csv = next((p for p in sales_master_paths if p.exists()), None)
    if master_csv:
        logger.info("Aggregating historical sales (2024/2025) from %s...", master_csv)
        try:
            from pipeline.pipeline_inputs import parse_sales_dates
        except ImportError:
            from pipeline_inputs import parse_sales_dates  # type: ignore

        cols = ["STORE CODE", "TRANSACTION_SITE", "ZONE", "SITE TYPE", "ENTRY_DATE", "NET_AMOUNT", "QUANTITY", "RSP", "Site Code", "Site", "Invoice Date", "Item Net Amt", "Qty", "Gross Amt"]
        header = pd.read_csv(master_csv, nrows=0).columns.tolist()
        header_set = {str(c).strip() for c in header}
        usecols = [c for c in cols if c in header_set]

        for chunk in pd.read_csv(master_csv, usecols=usecols, chunksize=200000, low_memory=False):
            date_col = next((c for c in ["ENTRY_DATE", "Invoice Date", "Date", "BILL DATE"] if c in chunk.columns), None)
            if not date_col:
                continue
            chunk["date"] = parse_sales_dates(chunk[date_col])
            chunk["year"] = chunk["date"].dt.year
            chunk["month"] = chunk["date"].dt.month
            code_col = next((c for c in ["STORE CODE", "Site Code", "Site_ID"] if c in chunk.columns), None)
            if not code_col:
                continue
            chunk = chunk.dropna(subset=["year", "month", code_col])
            chunk["site_id"] = chunk[code_col].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
            chunk["year"] = chunk["year"].astype(int)
            chunk["month"] = chunk["month"].astype(int)

            # Keep 2024 and 2025 from SALES_MASTER
            chunk = chunk[chunk["year"].isin([2024, 2025])]
            if chunk.empty:
                continue

            channels: list[str] = []
            zones: list[str] = []
            states: list[str] = []
            cities: list[str] = []
            site_names: list[str] = []

            for _, r in chunk.iterrows():
                sid = str(r["site_id"]).upper()
                site_name = str(r.get("TRANSACTION_SITE", r.get("Site", "")))
                site_type = str(r.get("SITE TYPE", r.get("Site Type", ""))).upper()
                raw_zone = str(r.get("ZONE", r.get("Zone", ""))).strip() if pd.notna(r.get("ZONE", r.get("Zone", None))) else ""

                meta = cm_map.get(sid)
                ch = meta["channel"] if meta and meta["channel"] else ""
                if not ch:
                    ch = detect_channel(site_name, sid, sid)
                if ch in ("OTHER", "", "nan"):
                    upper_name = site_name.upper()
                    if "PANTALOON" in upper_name or "PT" in site_type:
                        ch = "PT"
                    elif "SHOPPER" in upper_name or "SSL" in site_type:
                        ch = "SSL"
                    elif "CENTRO" in upper_name or sid in CENTRO_CODES:
                        ch = "CENTRO"
                    elif "ICONIC" in upper_name:
                        ch = "ICONIC"
                    elif "EBO" in site_type or "EBO" in upper_name:
                        ch = "EBO"
                    elif "LULU" in upper_name or "SPENCER" in upper_name or "GLOBUS" in upper_name or "LFS" in site_type:
                        ch = "LFS"
                    elif "M/S" in upper_name or "MBO" in site_type or "BINDAL" in upper_name:
                        ch = "MBO"
                    else:
                        ch = "MBO"

                zn = meta["zone"] if meta and meta["zone"] and meta["zone"] != "Unknown" else raw_zone
                if not zn or zn in ["nan", "None"]:
                    upper_name = site_name.upper()
                    zn = (
                        "West" if "MUMBAI" in upper_name or "THANE" in upper_name or "PUNE" in upper_name
                        else "North" if "DELHI" in upper_name or "NOIDA" in upper_name or "GURGAON" in upper_name
                        else "South" if "BANGALORE" in upper_name or "HYDERABAD" in upper_name or "CHENNAI" in upper_name
                        else "East" if "KOLKATA" in upper_name or "SILIGURI" in upper_name
                        else "Unknown"
                    )

                st = meta["state"] if meta and meta["state"] else "Unknown"
                ct = meta["city"] if meta and meta["city"] else "Unknown"
                sn = meta["site_name"] if meta and meta["site_name"] else (site_name if site_name else sid)

                channels.append(ch)
                zones.append(zn)
                states.append(st)
                cities.append(ct)
                site_names.append(sn)

            chunk["channel"] = channels
            chunk["zone"] = zones
            chunk["state"] = states
            chunk["city"] = cities
            chunk["site_name"] = site_names

            net_col_raw = next((c for c in ["NET_AMOUNT", "Item Net Amt", "net_sales"] if c in chunk.columns), None) or "net_sales"
            rsp_col = next((c for c in ["RSP", "Gross Amt", "mrp"] if c in chunk.columns), None) or net_col_raw
            qty_col_raw = next((c for c in ["QUANTITY", "Qty", "units"] if c in chunk.columns), None) or "units"
            if net_col_raw not in chunk.columns:
                chunk[net_col_raw] = 0.0
            if rsp_col not in chunk.columns:
                chunk[rsp_col] = chunk[net_col_raw]
            if qty_col_raw not in chunk.columns:
                chunk[qty_col_raw] = 0

            agg = (
                chunk.groupby(["year", "month", "site_id", "channel", "zone", "state", "city", "site_name"], as_index=False)
                .agg({
                    net_col_raw: "sum",
                    rsp_col: "sum",
                    qty_col_raw: "sum",
                })
                .rename(columns={net_col_raw: "net_sales", rsp_col: "gross_sales", qty_col_raw: "units"})
            )
            records.append(agg)

    # 2. Recent FY folders from year-sliced cache (same source as Sales Dashboard)
    from api.sales_year_loader import dashboard_fy_years_sorted

    _fy_years = dashboard_fy_years_sorted()
    _cal_years = list(range(min(_fy_years) - 1, max(_fy_years) + 1)) if _fy_years else []
    for y_code in _fy_years:
        try:
            df_y = load_sales_for_year(y_code)
            if df_y is not None and not df_y.empty and "date" in df_y.columns:
                df_y = df_y.copy()
                df_y["year"] = df_y["date"].dt.year
                df_y["month"] = df_y["date"].dt.month
                if _cal_years:
                    df_y = df_y[df_y["year"].isin(_cal_years)]
                if not df_y.empty:
                    df_y["site_id"] = df_y["site_id"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)

                    mrp_col = "mrp" if "mrp" in df_y.columns else "net_sale_amount"
                    qty_col = "qty" if "qty" in df_y.columns else "units"
                    net_col = "net_sale_amount" if "net_sale_amount" in df_y.columns else "net_sales"

                    df_y["gross_line"] = df_y[mrp_col] * df_y[qty_col]
                    df_y["channel"] = df_y["channel"].fillna(df_y.get("store_type", "OTHER")).astype(str)
                    df_y["zone"] = df_y["zone"].fillna("Unknown").astype(str).str.strip()
                    df_y["state"] = df_y["state"].fillna("Unknown").astype(str).str.strip()
                    df_y["city"] = df_y["city"].fillna("Unknown").astype(str).str.strip()
                    df_y["site_name"] = df_y["site_name"].fillna(df_y["site_id"]).astype(str)

                    c_agg = (
                        df_y.groupby(["year", "month", "site_id", "channel", "zone", "state", "city", "site_name"], as_index=False)
                        .agg({
                            net_col: "sum",
                            "gross_line": "sum",
                            qty_col: "sum",
                        })
                        .rename(columns={net_col: "net_sales", "gross_line": "gross_sales", qty_col: "units"})
                    )
                    records.append(c_agg)
        except Exception as exc:
            logger.warning("Year %s aggregation for historical summary skipped: %s", y_code, exc)

    if records:
        master_summary = pd.concat(records, ignore_index=True)
        master_summary = (
            master_summary.groupby(["year", "month", "site_id", "channel", "zone", "state", "city", "site_name"], as_index=False)
            .agg({
                "net_sales": "sum",
                "gross_sales": "sum",
                "units": "sum",
            })
        )
    else:
        master_summary = pd.DataFrame(columns=["year", "month", "site_id", "channel", "zone", "state", "city", "site_name", "net_sales", "gross_sales", "units"])

    try:
        master_summary.to_parquet(_HISTORICAL_CACHE, index=False)
        logger.info("Saved historical summary cache (%d rows) to %s", len(master_summary), _HISTORICAL_CACHE)
    except Exception as exc:
        logger.warning("Could not save historical summary parquet: %s", exc)

    return master_summary


def _get_historical_summary() -> pd.DataFrame:
    """Return fast cached DataFrame of store historical sales summary."""
    backend_root = Path(__file__).resolve().parents[2]
    candidates = [
        _HISTORICAL_CACHE,
        backend_root / "data" / "api_cache" / "historical_sales_summary.parquet",
        Path.cwd() / "data" / "api_cache" / "historical_sales_summary.parquet",
        backend_root / "data" / "historical_sales_summary.parquet",
    ]
    for p in candidates:
        if p.exists():
            try:
                return pd.read_parquet(p)
            except Exception as exc:
                logger.warning("Failed to read historical cache parquet at %s: %s", p, exc)
    return _build_historical_summary()


def _val(x: Any, default: Any = "") -> Any:
    if hasattr(x, "default"):
        return default if x.default is ... else x.default
    return default if x is None else x


def _channel_codes(raw: str | None) -> set[str] | None:
    """Parse comma-separated channels into match codes (PT/SSL/… + aliases)."""
    ch_str = str(_val(raw, "all")).strip()
    if not ch_str or ch_str.lower() in ("", "all"):
        return None
    alias = {
        "PANTALOONS": "PT",
        "PANTALOONS (PT)": "PT",
        "SHOPPERS STOP": "SSL",
        "SHOPPERS STOP (SSL)": "SSL",
        "SHOPPERS-STOP": "SSL",
        "SHOPPERS_STOP": "SSL",
    }
    codes = {
        alias.get(c.strip().upper(), c.strip().upper())
        for c in ch_str.split(",")
        if c.strip()
    }
    match_vals: set[str] = set()
    for code in codes:
        match_vals.add(code)
        if code == "PT":
            match_vals.update({"PT", "PANTALOONS", "PANTALOONS (PT)"})
        elif code == "SSL":
            match_vals.update({"SSL", "SHOPPERS STOP", "SHOPPERS STOP (SSL)"})
    return match_vals


def _filter_summary(
    df: pd.DataFrame,
    *,
    zone: str | None = None,
    zones: str | None = None,
    channel: str | None = None,
    site_ids: str | None = None,
) -> pd.DataFrame:
    """Scope target / summary frames by zone(s), multi-channel, and site_ids."""
    out = df
    if out is None or out.empty:
        return out

    # Multi-zone (CSV) preferred; single `zone` still supported
    zones_raw = str(_val(zones, "")).strip()
    z_one = str(_val(zone, "all")).strip()
    allowed_zones: set[str] = set()
    if zones_raw and zones_raw.lower() not in ("", "all"):
        allowed_zones = {z.strip().upper() for z in zones_raw.split(",") if z.strip()}
    elif z_one and z_one.lower() != "all":
        allowed_zones = {z_one.upper()}
    if allowed_zones and "zone" in out.columns:
        out = out[out["zone"].astype(str).str.strip().str.upper().isin(allowed_zones)]

    ch_codes = _channel_codes(channel)
    if ch_codes is not None:
        ch_col = "channel" if "channel" in out.columns else ("format" if "format" in out.columns else None)
        if ch_col:
            clean = out[ch_col].astype(str).str.strip().str.upper()
            out = out[clean.isin(ch_codes)]

    sids_str = str(_val(site_ids, "")).strip()
    if sids_str:
        raw_ids = {s.strip().upper().replace(".0", "") for s in sids_str.split(",") if s.strip()}
        if "__NONE__" in raw_ids or "__none__" in raw_ids:
            return out.iloc[0:0]
        if raw_ids and "site_id" in out.columns:
            from api.historical_movement import _extract_single_site_key

            # Expand tokens (SS-Gurgaon-110 → 110) so Target LFS codes match sales/CM keys.
            expanded: set[str] = set()
            for tok in raw_ids:
                expanded.add(tok)
                key = _extract_single_site_key(tok)
                if key:
                    expanded.add(str(key).strip().upper())
            sid = (
                out["site_id"]
                .astype(str)
                .str.strip()
                .str.upper()
                .str.replace(r"\.0$", "", regex=True)
            )
            sid_key = sid.map(lambda s: (_extract_single_site_key(s) or s))
            out = out[sid.isin(expanded) | sid_key.isin(expanded)]
    return out


@router.get("/multi-year-trend")
def get_multi_year_trend(request: Request, year: int = Query(DEFAULT_YEAR)):
    """Monthly selected-period sales and the same dates in the previous two years."""
    # v3: prior/older years keep zones/grades/states/cities + season/channel/site
    cache_key = _home_cache_key("trend-v6-data-rev", request, year)
    cached = _exec_cached(request, "home-trend", cache_key)
    if cached is not None:
        return cached

    from api.routes.sales_dashboard import _load_sply_prior_df
    year_val = normalize_dashboard_year(year)
    current = _home_sales(request, year_val)
    prior = _home_sales(request, year_val, prior=True)
    q = request.query_params
    filters = {k: q.get(k) for k in (
        "site_ids", "channel", "season", "sku", "barcodes", "departments", "sections",
        "zones", "store_grades", "states", "cities",
    )}
    zone = q.get("zone", "all").strip()
    def monthly(frame):
        if frame.empty or "date" not in frame.columns:
            return {}
        return frame.groupby(frame["date"].dt.month)["net_sale_amount"].sum().to_dict()
    old = None
    if prior.attrs.get("query_engine") == "azure_sql" and not prior.empty and "date" in prior.columns:
        # Filtered Home: two-years-ago is a SQL monthly aggregate, not a full FY pandas load.
        try:
            from api.azure_sql_analytics import sql_sply_monthly_sales

            sql_f = {k: v or "" for k, v in filters.items()}
            if not sql_f["zones"] and zone.lower() != "all":
                sql_f["zones"] = zone
            old = sql_sply_monthly_sales(
                min_date=prior["date"].min(), max_date=prior["date"].max(), filters=sql_f
            )
        except Exception as exc:
            logger.warning("Executive trend Azure SQL older-year path failed; fallback: %s", exc)
            old = None
    if old is None:
        older = _load_sply_prior_df(prior, filter_kw=filters)
        if zone.lower() != "all" and "zone" in older.columns:
            older = older[older["zone"].astype(str).str.strip().str.upper().eq(zone.upper())]
        old = monthly(older)
    cur, prev = monthly(current), monthly(prior)
    labels = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    months = list(range(4, 13)) + list(range(1, 4))
    cur_total, prev_total = float(sum(cur.values())), float(sum(prev.values()))
    month_rows = []
    for m in months:
        s_cur = round(float(cur.get(m, 0)), 2)
        s_prev = round(float(prev.get(m, 0)), 2)
        s_old = round(float(old.get(m, 0)), 2)
        month_rows.append(
            {
                "month": m,
                "label": labels[m - 1],
                "sales_2026": s_cur,
                "sales_2025": s_prev,
                "sales_2024": s_old,
                "yoy_pct": round(((s_cur - s_prev) / s_prev) * 100, 1) if s_prev > 0 else None,
            }
        )
    res = {
        "ok": True,
        "period_labels": {"sales_2026": "Selected period", "sales_2025": "Last year", "sales_2024": "Two years ago"},
        "months": month_rows,
        "ytd_2026": round(cur_total, 2), "ytd_2025": round(prev_total, 2),
        "total_2024": round(float(sum(old.values())), 2), "total_2025": round(prev_total, 2),
        "yoy_growth_pct": round((cur_total / prev_total - 1) * 100, 1) if prev_total > 0 else 0.0,
    }
    _exec_store(request, "home-trend", cache_key, res)
    return res


def _clean_site_id(val: Any) -> str:
    s = str(val).strip() if val is not None else ""
    if s.endswith(".0"):
        s = s[:-2]
    return s


def _build_site_geo_lookup(
    sales: pd.DataFrame | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    """
    Map transaction / target site codes → zone & state.
    Target LFS uses sales site ids (121, T8WI…); CM primary keys are often different
    (1165…). Prefer CM site_code / site_manual_code / _site_key, then sales zone/state.
    """
    zone_map: dict[str, str] = {}
    state_map: dict[str, str] = {}

    def _put(key: str, zone: str, state: str) -> None:
        k = _clean_site_id(key)
        if not k or k.lower() in {"nan", "none", ""}:
            return
        z = str(zone).strip() if zone is not None and str(zone).strip().lower() not in {"nan", "none", ""} else ""
        st = str(state).strip() if state is not None and str(state).strip().lower() not in {"nan", "none", ""} else ""
        if z and k not in zone_map:
            zone_map[k] = z
        if st and k not in state_map:
            state_map[k] = st

    try:
        cm = load_customer_master()
        for _, r in cm.iterrows():
            z = r.get("zone", "")
            st = r.get("state", "")
            for col in ("site_code", "site_manual_code", "_site_key", "slcode"):
                if col in cm.columns:
                    _put(r.get(col, ""), z, st)
    except Exception as exc:
        logger.warning("site geo CM lookup failed: %s", exc)

    if sales is not None and not sales.empty:
        sid_col = "site_id" if "site_id" in sales.columns else None
        if sid_col:
            gcols = [sid_col]
            if "zone" in sales.columns:
                gcols.append("zone")
            if "state" in sales.columns:
                gcols.append("state")
            if len(gcols) > 1:
                for _, r in sales[gcols].drop_duplicates(subset=[sid_col]).iterrows():
                    _put(
                        r[sid_col],
                        r["zone"] if "zone" in r.index else "",
                        r["state"] if "state" in r.index else "",
                    )

    return zone_map, state_map


def _filter_target_by_fy(target_df: pd.DataFrame, year_val: int) -> pd.DataFrame:
    """Filter Target LFS for given Indian FY end-year (e.g. 2027 -> Apr 2026 to Mar 2027)."""
    if target_df.empty or "date" not in target_df.columns:
        return target_df.iloc[0:0]
    fy_start = pd.Timestamp(f"{year_val - 1}-04-01")
    fy_end = pd.Timestamp(f"{year_val}-03-31 23:59:59")
    t_yr = target_df[(target_df["date"] >= fy_start) & (target_df["date"] <= fy_end)].copy()
    if t_yr.empty:
        # Fallback to calendar year if not formatted as FY
        t_yr = target_df[target_df["date"].dt.year == year_val].copy()
    return t_yr


def _target_period_end(dates: pd.Series) -> pd.Timestamp:
    """Last instant covered by the target rows. A 1st-of-month date covers that whole month."""
    parsed = pd.to_datetime(dates, errors="coerce").dropna()
    if parsed.empty:
        return pd.Timestamp.max
    latest = pd.Timestamp(parsed.max())
    day_mode = int(parsed.dt.day.mode().iloc[0])
    if day_mode == 1:
        latest = pd.Timestamp(latest + pd.offsets.MonthEnd(0))
    return latest.normalize() + pd.Timedelta(days=1, microseconds=-1)


def _norm_site_token(val: Any) -> str:
    s = str(val).strip().upper() if val is not None else ""
    if s.endswith(".0"):
        s = s[:-2]
    if s in {"", "NAN", "NONE", "<NA>", "NAT"}:
        return ""
    return s


def _sales_keys_for_targets(sales: pd.DataFrame, sites: set[str]) -> pd.Series:
    """Pick the sales token that equals a Target LFS site code.

    Sales site ids look like SS-Gurgaon-110 or PT-...(P017). Target rows use 110 / P017.
    """
    key = pd.Series(pd.NA, index=sales.index, dtype=object)

    def _take(cand: pd.Series) -> None:
        nonlocal key
        token = (
            cand.astype(str)
            .str.strip()
            .str.upper()
            .str.replace(r"\.0$", "", regex=True)
            .str.replace(r"\s+", "", regex=True)
        )
        token = token.mask(token.isin({"", "NAN", "NONE", "<NA>", "NAT"}))
        hit = key.isna() & token.notna() & token.isin(sites)
        key.loc[hit] = token.loc[hit]

    if "site_id" in sales.columns:
        sid = sales["site_id"].astype(str)
        _take(sid.str.extract(r"\(([A-Z0-9]+)\)", expand=False))
        _take(sid.str.extract(r"[-_\s]([A-Z0-9]+)\s*$", expand=False))
        _take(sales["site_id"])
    if "site_name" in sales.columns:
        _take(sales["site_name"].astype(str).str.extract(r"\(\s*([A-Z0-9]+)\s*\)", expand=False))
    for col in ("site_code", "site_short_code", "site_manual_code"):
        if col in sales.columns:
            _take(sales[col])
    return key.fillna("")


def _target_scope(targets: pd.DataFrame | None) -> tuple[set[str], pd.Timestamp, pd.Timestamp] | None:
    """(target site tokens, first day, last instant) covered by positive target rows."""
    if targets is None or targets.empty or "date" not in targets.columns:
        return None
    scoped = targets
    if "target" in scoped.columns:
        scoped = scoped[pd.to_numeric(scoped["target"], errors="coerce").fillna(0) > 0]
    if scoped.empty:
        return None
    site_col = "site_id" if "site_id" in scoped.columns else ("site_code" if "site_code" in scoped.columns else "")
    if not site_col:
        return None
    sites = {_norm_site_token(v) for v in scoped[site_col].tolist()}
    sites.discard("")
    if not sites:
        return None
    target_dates = pd.to_datetime(scoped["date"], errors="coerce").dropna()
    if target_dates.empty:
        return None
    return sites, pd.Timestamp(target_dates.min()).normalize(), _target_period_end(target_dates)


def _sales_matching_targets(sales: pd.DataFrame, targets: pd.DataFrame) -> pd.DataFrame:
    """Sales for stores that have a target, only across the dates that target covers."""
    if sales is None or sales.empty or targets is None or targets.empty:
        return sales.iloc[0:0] if isinstance(sales, pd.DataFrame) else pd.DataFrame()
    if "date" not in sales.columns or "date" not in targets.columns:
        return sales.iloc[0:0]
    scope = _target_scope(targets)
    if scope is None:
        return sales.iloc[0:0]
    sites, start, end = scope
    out = sales.copy()
    out["_site"] = _sales_keys_for_targets(out, sites)
    if not pd.api.types.is_datetime64_any_dtype(out["date"]):
        out["_date"] = pd.to_datetime(out["date"], errors="coerce")
    else:
        out["_date"] = out["date"]
    if getattr(out["_date"].dt, "tz", None) is not None:
        out["_date"] = out["_date"].dt.tz_localize(None)
    out = out[out["_site"].ne("") & out["_date"].notna() & (out["_date"] >= start) & (out["_date"] <= end)]
    out["site_id"] = out["_site"]
    return out.drop(columns=["_site", "_date"])


def _ranked_target_stores(
    sales: pd.DataFrame,
    year_val: int,
    *,
    top_n: int = 10,
    channel: str = "all",
    zone: str = "all",
    site_ids: str = "",
    request: Request | None = None,
    prior_df: pd.DataFrame | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Top / bottom stores among sites that have a Target LFS for the year,
    ranked by achievement % (actual sales / target).
    """
    empty: list[dict[str, Any]] = []
    try:
        target_df = target_vs_achieved._load_target_df()
    except Exception as exc:
        logger.warning("ranked target stores: target load failed: %s", exc)
        return empty, empty

    t_yr = _filter_target_by_fy(target_df, year_val)
    if request is not None:
        t_yr = _home_targets(request, t_yr)
    if t_yr.empty:
        return empty, empty

    t_yr["site_id"] = t_yr["site_code"].map(_clean_site_id)
    t_yr["channel"] = t_yr["format"].astype(str)

    # Multi-channel CSV (PT,SSL) — same as Sales Overview / _filter_summary
    ch_codes = _channel_codes(channel)
    if ch_codes is not None:
        t_yr = t_yr[t_yr["channel"].astype(str).str.strip().str.upper().isin(ch_codes)]

    sids_str = str(_val(site_ids, "")).strip()
    if sids_str and sids_str.lower() not in {"", "all"}:
        raw_ids = {_clean_site_id(s).upper() for s in sids_str.split(",") if s.strip()}
        if "__NONE__" in raw_ids:
            return empty, empty
        t_yr = t_yr[t_yr["site_id"].str.upper().isin(raw_ids)]

    # Multi-zone from request when present (zone=all but zones=North,South)
    if request is not None:
        zones_csv = str(request.query_params.get("zones") or "").strip()
        if zones_csv and zones_csv.lower() not in ("", "all") and "zone" not in t_yr.columns:
            zmap, _ = _build_site_geo_lookup(sales if sales is not None else None)
            t_yr = t_yr.copy()
            t_yr["zone"] = t_yr["site_id"].map(lambda s: zmap.get(str(s), "") or zmap.get(_clean_site_id(s), ""))
        if zones_csv and zones_csv.lower() not in ("", "all") and "zone" in t_yr.columns:
            allowed = {z.strip().upper() for z in zones_csv.split(",") if z.strip()}
            t_yr = t_yr[t_yr["zone"].astype(str).str.strip().str.upper().isin(allowed)]

    if t_yr.empty:
        return empty, empty

    tgt = (
        t_yr.groupby("site_id", as_index=False)
        .agg(target=("target", "sum"), store_name_t=("store_name", "first"), channel=("channel", "first"))
    )
    tgt = tgt[tgt["target"] > 0]
    if tgt.empty:
        return empty, empty

    sales_by = pd.DataFrame(columns=["site_id", "sales", "units", "store", "zone"])
    if sales is not None and not sales.empty:
        s = _sales_matching_targets(sales, t_yr)
        if not s.empty:
            s = s.copy()
            s["site_id"] = s["site_id"].map(_clean_site_id)
            name_col = "site_name" if "site_name" in s.columns else "site_id"
            agg: dict[str, Any] = {
                "sales": ("net_sale_amount", "sum"),
                "units": ("qty", "sum"),
                "store": (name_col, "first"),
            }
            if "zone" in s.columns:
                agg["zone"] = ("zone", "first")
            sales_by = s.groupby("site_id", as_index=False).agg(**agg)

    merged = tgt.merge(sales_by, on="site_id", how="left")  # every targeted store, sales only inside the target window
    if merged.empty:
        return empty, empty
    merged["sales"] = merged["sales"].fillna(0.0)
    merged["units"] = merged["units"].fillna(0).astype(int)
    if "store" not in merged.columns:
        merged["store"] = merged["store_name_t"]
    else:
        merged["store"] = merged["store"].fillna(merged["store_name_t"]).astype(str)
        blank = merged["store"].str.strip().isin({"", "nan", "None"})
        merged.loc[blank, "store"] = merged.loc[blank, "store_name_t"].astype(str)
    merged = merged[merged["store"].astype(str).str.strip().isin({"", "nan", "None"}) == False]
    if merged.empty:
        return empty, empty
    if "zone" not in merged.columns:
        merged["zone"] = ""
    else:
        merged["zone"] = merged["zone"].fillna("").astype(str)

    need_zone = merged["zone"].str.strip().isin({"", "nan", "None", "Unknown"})
    if need_zone.any():
        zmap, _ = _build_site_geo_lookup(sales if sales is not None else None)
        merged.loc[need_zone, "zone"] = merged.loc[need_zone, "site_id"].map(
            lambda sid: zmap.get(sid, "Unknown")
        )

    z_str = str(_val(zone, "all")).strip()
    if z_str and z_str.lower() != "all":
        merged = merged[merged["zone"].astype(str).str.strip().str.upper() == z_str.upper()]
        if merged.empty:
            return empty, empty

    merged["achievement"] = np.where(
        merged["target"] > 0,
        (merged["sales"] / merged["target"] * 100).round(1),
        0.0,
    )

    # Prior growth: same YTD cutover as current sales
    prior_map: dict[str, float] = {}
    try:
        prior = prior_df if prior_df is not None else (_home_sales(request, year_val, prior=True) if request is not None else load_sales_for_year(year_val - 1))
        if prior is not None and not prior.empty:
            prior = prior.copy()
            prior["site_id"] = prior["site_id"].map(_clean_site_id)
            shifted = t_yr.copy()
            shifted["date"] = pd.to_datetime(shifted["date"], errors="coerce") - pd.DateOffset(years=1)
            prior = _sales_matching_targets(prior, shifted)
            if not prior.empty:
                prior_map = prior.groupby("site_id")["net_sale_amount"].sum().to_dict()
    except Exception:
        prior_map = {}

    prior_series = merged["site_id"].map(prior_map).fillna(0.0)
    merged["growth"] = np.where(
        prior_series > 0,
        ((merged["sales"] - prior_series) / prior_series * 100).round(1),
        0.0,
    )

    # Leaders / laggards among meaningful targets (avoid tiny-target % outliers)
    med_tgt = float(merged["target"].median()) if len(merged) else 0.0
    min_target = max(1_000_000.0, med_tgt * 0.5) if len(merged) >= 20 else max(500_000.0, med_tgt * 0.25)
    eligible = merged[merged["target"] >= min_target].copy()
    if len(eligible) < top_n * 2:
        eligible = merged[merged["target"] >= max(500_000.0, med_tgt * 0.25)].copy()
    if len(eligible) < top_n:
        eligible = merged.copy()

    ranked = eligible.sort_values(["achievement", "sales"], ascending=[False, False]).reset_index(drop=True)

    def _rows(df: pd.DataFrame, start_rank: int = 1) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for i, (_, r) in enumerate(df.iterrows()):
            out.append(
                {
                    "rank": start_rank + i,
                    "store": str(r["store"]),
                    "zone": str(r["zone"]).strip() if str(r["zone"]).strip() else "—",
                    "sales": round(float(r["sales"]), 2),
                    "target": round(float(r["target"]), 2),
                    "achievement": float(r["achievement"]),
                    "growth": float(r["growth"]),
                    "units": int(r["units"]),
                    "site_id": str(r["site_id"]),
                }
            )
        return out

    top_df = ranked.head(top_n)
    top_ids = set(top_df["site_id"].astype(str))
    bottom_pool = ranked if len(ranked) <= top_n * 2 else ranked[~ranked["site_id"].astype(str).isin(top_ids)]
    bottom_df = bottom_pool.sort_values(["achievement", "sales"], ascending=[True, True]).head(top_n)

    return _rows(top_df, 1), _rows(bottom_df, 1)


@router.get("/target-vs-achievement")
def get_target_vs_achievement(
    request: Request,
    year: int = Query(DEFAULT_YEAR, description="Target year: 2024, 2025, 2026 or 2027"),
    group_by: str = Query("zone", description="Group by: zone or region"),
    zone: str = Query("all"),
    channel: str = Query("all"),
    site_ids: str = Query(""),
):
    cache_key = _home_cache_key("target-v6-data-rev", request, year)
    cached = _exec_cached(request, "home-target", cache_key)
    if cached is not None:
        return cached

    year_val = normalize_dashboard_year(year)
    group_val = str(_val(group_by, "zone")).lower()
    zones_csv = str(request.query_params.get("zones") or "").strip()
    channel_q = channel or request.query_params.get("channel", "all")
    site_ids_q = site_ids or request.query_params.get("site_ids", "")

    target_df = target_vs_achieved._load_target_df()
    t_yr = _filter_target_by_fy(target_df, year_val)
    t_yr = _home_targets(request, t_yr)
    t_yr["site_id"] = t_yr["site_code"].map(_clean_site_id)
    t_yr["channel"] = t_yr["format"].astype(str)

    # Actuals from year-scoped sales (align with Home/Sales Overview)
    try:
        sales = _home_sales(request, year_val)
    except Exception as exc:
        logger.warning("target-vs-achievement sales load failed: %s", exc)
        sales = None

    if sales is not None:
        s_yr = sales.copy()
        s_yr["net_sales"] = s_yr["net_sale_amount"]
        if "state" not in s_yr.columns:
            s_yr["state"] = "Unknown"
    else:
        hist_df = _get_historical_summary()
        s_yr = hist_df[hist_df["year"] == year_val].copy()
        sales = None

    zone_map, state_map = _build_site_geo_lookup(s_yr if s_yr is not None else sales)
    t_yr["zone"] = t_yr["site_id"].map(lambda sid: zone_map.get(sid, "Unknown"))
    t_yr["state"] = t_yr["site_id"].map(lambda sid: state_map.get(sid, "Unknown"))

    # Same multi-channel / multi-zone / site cut as the rest of Home
    t_sub = _filter_summary(
        t_yr, zone=zone, zones=zones_csv, channel=channel_q, site_ids=site_ids_q
    )
    z_str = str(_val(zone, "all")).strip()

    s_sub = _sales_matching_targets(s_yr, t_sub)
    if not s_sub.empty:
        s_sub = s_sub.copy()
        mapped_zone = s_sub["site_id"].map(lambda sid: zone_map.get(sid) or zone_map.get(_clean_site_id(sid)) or "")
        mapped_state = s_sub["site_id"].map(lambda sid: state_map.get(sid) or state_map.get(_clean_site_id(sid)) or "")
        zone_hit = mapped_zone.astype(str).str.strip().ne("")
        state_hit = mapped_state.astype(str).str.strip().ne("")
        s_sub.loc[zone_hit, "zone"] = mapped_zone.loc[zone_hit]
        s_sub.loc[state_hit, "state"] = mapped_state.loc[state_hit]
    ch_codes = _channel_codes(channel_q)
    sids_str = str(_val(site_ids_q, "")).strip()
    if ch_codes is not None and not s_sub.empty:
        mask = pd.Series(False, index=s_sub.index)
        for col in ("store_type", "channel", "sales_channel"):
            if col not in s_sub.columns:
                continue
            mask = mask | s_sub[col].astype(str).str.strip().str.upper().isin(ch_codes)
        if mask.any():
            s_sub = s_sub[mask]
    if z_str and z_str.lower() != "all" and "zone" in s_sub.columns:
        s_sub = s_sub[s_sub["zone"].astype(str).str.strip().str.upper() == z_str.upper()]
    elif zones_csv and zones_csv.lower() not in ("", "all") and "zone" in s_sub.columns:
        allowed_z = {z.strip().upper() for z in zones_csv.split(",") if z.strip()}
        s_sub = s_sub[s_sub["zone"].astype(str).str.strip().str.upper().isin(allowed_z)]
    if sids_str:
        raw_ids = {s.strip().upper().replace(".0", "") for s in sids_str.split(",") if s.strip()}
        if "__NONE__" in raw_ids or "__none__" in raw_ids:
            s_sub = s_sub.iloc[0:0]
        elif raw_ids and "site_id" in s_sub.columns:
            clean = s_sub["site_id"].astype(str).str.strip().str.upper().str.replace(r"\.0$", "", regex=True)
            s_sub = s_sub[clean.isin(raw_ids)]

    group_col = "zone" if group_val == "zone" else "state"
    t_grp = t_sub.groupby(group_col)["target"].sum()
    s_grp = s_sub.groupby(group_col)["net_sales"].sum() if group_col in s_sub.columns else pd.Series(dtype=float)

    all_keys = sorted(set(t_grp.index).union(set(s_grp.index)))
    all_keys = [k for k in all_keys if str(k).strip() and str(k).lower() not in {"unknown", "nan", "-"}]

    result_rows = []
    for key in all_keys:
        tgt = float(t_grp.get(key, 0.0))
        act = float(s_grp.get(key, 0.0))
        ach_pct = round((act / tgt * 100), 1) if tgt > 0 else 0.0
        result_rows.append({
            "name": key,
            "target": round(tgt, 2),
            "actual": round(act, 2),
            "achievement": ach_pct,
        })

    result_rows.sort(key=lambda r: r["actual"], reverse=True)

    res = {
        "ok": True,
        "year": year_val,
        "group_by": group_val,
        "data": result_rows,
    }
    _exec_store(request, "home-target", cache_key, res)
    return res


@router.get("/summary")
def get_executive_summary(
    request: Request,
    year: int = Query(DEFAULT_YEAR, description="Calendar year: 2024, 2025, or 2026"),
    zone: str = Query("all"),
    channel: str = Query("all"),
    site_ids: str = Query(""),
    season: str = Query("", description="Season filter (comma-separated) or empty for all"),
):
    """
    Real-time Executive Dashboard KPIs for the selected calendar year.
    YoY: 2026 vs 2025 YTD (through Aug); 2025 vs 2024 full year when available.
    """
    # v5: target_unavailable null (no Target LFS for filter → not ₹0 / 0%)
    cache_key = _home_cache_key("summary-v6-data-rev", request, year)
    cached, _summary_leader, _summary_ready = _exec_wait_or_build(
        request, "home-summary", cache_key
    )
    if cached is not None:
        return cached
    try:
        return _get_executive_summary_uncached(
            request, year=year, zone=zone, channel=channel, site_ids=site_ids, season=season,
            cache_key=cache_key,
        )
    finally:
        if _summary_ready is not None:
            _exec_release(cache_key, _summary_ready)


def _summary_kpi_scope(request: Request, *, zone: str, channel: str, site_ids: str, season_str: str) -> dict[str, Any]:
    q = request.query_params
    # Multi-channel / multi-zone from query (same as Sales Overview)
    return {
        "season": season_str or q.get("season", ""),
        "sku": q.get("sku", ""),
        "barcode": q.get("barcode", ""),
        "barcodes": q.get("barcodes", ""),
        "departments": q.get("departments", ""),
        "sections": q.get("sections", ""),
        "channel": q.get("channel", channel) or channel,
        "zones": q.get("zones", "") or (zone if str(zone).lower() not in ("", "all") else ""),
        "site_id": "all",
        "site_ids": site_ids or q.get("site_ids", ""),
        "store_grades": q.get("store_grades", ""),
        "states": q.get("states", ""),
        "cities": q.get("cities", ""),
    }


def _summary_targets(
    request: Request,
    year_val: int,
    *,
    zone: str,
    channel: str,
    site_ids: str,
    sales_for_geo: pd.DataFrame | None,
    matched_net: Callable[[pd.DataFrame], float],
) -> tuple[float, float | None, bool]:
    """(target total, achievement %, target unavailable) for the Home summary card."""
    target_df = target_vs_achieved._load_target_df()
    t_year = _filter_target_by_fy(target_df, year_val)
    t_year = _home_targets(request, t_year)
    # Multi-key CM map (site_code / site_manual_code / _site_key) — site_code-only
    # left East/Target rows as Unknown and blanked Target Achievement on zone filter.
    zone_map, _ = _build_site_geo_lookup(sales_for_geo)
    zone_map_u = {str(k).strip().upper(): v for k, v in zone_map.items() if str(k).strip()}
    t_year = t_year.copy()
    t_year["site_id"] = t_year["site_code"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    t_year["zone"] = (
        t_year["site_id"].map(zone_map)
        .fillna(t_year["site_id"].str.upper().map(zone_map_u))
        .fillna("Unknown")
    )
    t_year["channel"] = t_year["format"].astype(str)

    t_sub = _filter_summary(
        t_year,
        zone=zone,
        zones=request.query_params.get("zones", ""),
        channel=channel or request.query_params.get("channel", "all"),
        site_ids=site_ids or request.query_params.get("site_ids", ""),
    )
    target_total = float(t_sub["target"].sum()) if not t_sub.empty and "target" in t_sub.columns else 0.0
    # No Target LFS rows for this filter → null (UI: "Target not available"), never ₹0 / 0%.
    target_unavailable = target_total <= 0
    achievement_pct = (
        None
        if target_unavailable
        else round((matched_net(t_sub) / target_total * 100), 1)
    )
    return target_total, achievement_pct, target_unavailable


def _executive_summary_sql(
    request: Request,
    *,
    year_val: int,
    sql_f: dict[str, Any],
    zone: str,
    channel: str,
    site_ids: str,
    season_str: str,
) -> dict[str, Any] | None:
    """Filtered Home summary from Azure SQL aggregates — no sales rows leave the DB.
    Same numbers as the frame path; None → caller uses the frame path."""
    from api.azure_sql_analytics import (
        sql_distinct_sku_count,
        sql_query_ready,
        sql_sales_kpis,
        sql_sales_site_rows,
        sql_site_frame_between,
        sql_sply_totals,
    )
    from api.routes.sales_dashboard import _assemble_kpis_from_duck_agg

    if not sql_query_ready():
        return None
    agg = sql_sales_kpis(sql_f).get("agg") or {}
    line_count = int(agg.get("line_count") or 0)
    if line_count == 0 and str(sql_f.get("timeline_mode") or "").lower() == "fy_week":
        return None  # fy_week SPLY needs no current rows; leave that edge to the frame path
    prior_net = prior_qty = 0.0
    if line_count > 0:
        prior_net, prior_qty = sql_sply_totals(
            min_date=agg.get("min_date"), max_date=agg.get("max_date"), filters=sql_f
        )

    site_rows = sql_sales_site_rows((agg.get("site_nets") or {}).keys())
    if site_rows is None:
        return None
    unmatched = False

    def _matched_net(t_sub: pd.DataFrame) -> float:
        nonlocal unmatched
        scope = _target_scope(t_sub)
        if scope is None or line_count == 0:
            return 0.0
        sites, start, end = scope
        frame = sql_site_frame_between(sql_f, start.date(), end.date())
        if frame is None:
            unmatched = True
            return 0.0
        keys = _sales_keys_for_targets(frame, sites)
        return float(frame.loc[keys.ne(""), "net_sale_amount"].sum())

    target_total, achievement_pct, target_unavailable = _summary_targets(
        request, year_val, zone=zone, channel=channel, site_ids=site_ids,
        sales_for_geo=site_rows if not site_rows.empty else None, matched_net=_matched_net,
    )
    if unmatched:
        return None

    sales_kpis = _assemble_kpis_from_duck_agg(
        agg,
        scope=_summary_kpi_scope(request, zone=zone, channel=channel, site_ids=site_ids, season_str=season_str),
        prior_net=prior_net,
        prior_qty=prior_qty,
    )
    net_sales = float(sales_kpis.get("net_sales") or 0.0)
    units_sold = int(sales_kpis.get("units_sold") or 0)
    prior_units = int(prior_qty)
    return {
        "ok": True,
        "year": year_val,
        "prior_year": year_val - 1,
        "net_sales": round(net_sales, 2),
        "gross_sales": round(float(sales_kpis.get("gross_sales") or 0.0), 2),
        "target": None if target_unavailable else round(target_total, 2),
        "achievement_pct": achievement_pct,
        "target_unavailable": bool(target_unavailable),
        "units_sold": units_sold,
        "active_stores": int(agg.get("store_count") or 0),
        "yoy_growth_pct": round(((net_sales - prior_net) / prior_net * 100), 1) if prior_net > 0 else 0.0,
        "units_yoy_growth_pct": (
            round(((units_sold - prior_units) / prior_units * 100), 1) if prior_units > 0 else 0.0
        ),
        "prior_net_sales": round(prior_net, 2),
        "prior_units_sold": prior_units,
        "total_soh": int(sales_kpis.get("current_soh", 0) or 0),
        "sku_count": sql_distinct_sku_count(sql_f),
        "query_engine": "azure_sql",
        "sales_kpis": sales_kpis,
    }


def _get_executive_summary_uncached(
    request: Request,
    *,
    year: int,
    zone: str,
    channel: str,
    site_ids: str,
    season: str,
    cache_key: str,
):
    year_val = normalize_dashboard_year(year)
    prior_year = year_val - 1
    season_str = str(_val(season, "")).strip()

    sql_f = _home_sql_filters(request, year_val)
    if sql_f is not None:
        try:
            res = _executive_summary_sql(
                request, year_val=year_val, sql_f=sql_f, zone=zone, channel=channel,
                site_ids=site_ids, season_str=season_str,
            )
        except Exception as exc:
            logger.warning("Executive summary Azure SQL aggregate path failed; frame fallback: %s", exc)
            res = None
        if res is not None:
            _exec_store(request, "home-summary", cache_key, res)
            return res
    use_line = True
    sql_summary_used = False
    sub_year = pd.DataFrame()
    sub_prior = pd.DataFrame()

    # Filtered Home: _home_sales() already reads current + SPLY frames from Azure SQL
    # (see _home_sales_load), so the line path below is the single source of truth for
    # totals and like-for-like YoY. A separate SQL totals pass used full prior FY and
    # then loaded both frames anyway.

    if not sql_summary_used and use_line:
        from api.routes.sales_dashboard import _apply_filters

        df_year = _home_sales(request, year_val)
        sub_year = df_year

        net_sales = (
            float(sub_year["net_sale_amount"].sum())
            if not sub_year.empty and "net_sale_amount" in sub_year.columns
            else 0.0
        )
        mrp_col = "mrp" if "mrp" in sub_year.columns else "net_sale_amount"
        qty_col = "qty" if "qty" in sub_year.columns else "units"
        gross_sales = (
            float((sub_year[mrp_col] * sub_year[qty_col]).sum())
            if not sub_year.empty and mrp_col in sub_year.columns and qty_col in sub_year.columns
            else net_sales
        )
        units_sold = (
            int(sub_year[qty_col].sum())
            if not sub_year.empty and qty_col in sub_year.columns
            else 0
        )
        active_stores = (
            int(sub_year["site_id"].nunique())
            if not sub_year.empty and "site_id" in sub_year.columns
            else 0
        )

        sub_prior = pd.DataFrame()
        try:
            df_prior = _home_sales(request, year_val, prior=True)
            sub_prior = df_prior
            prior_net = (
                float(sub_prior["net_sale_amount"].sum())
                if not sub_prior.empty and "net_sale_amount" in sub_prior.columns
                else 0.0
            )
            prior_qty_col = "qty" if "qty" in sub_prior.columns else ("units" if "units" in sub_prior.columns else "")
            prior_units = (
                int(sub_prior[prior_qty_col].sum())
                if not sub_prior.empty and prior_qty_col and prior_qty_col in sub_prior.columns
                else 0
            )
        except Exception:
            prior_net = 0.0
            prior_units = 0
    elif not sql_summary_used:
        df = _get_historical_summary()
        sub = _filter_summary(df, zone=zone, channel=channel, site_ids=site_ids)
        sub_year = sub[sub["year"] == year_val]
        net_sales = float(sub_year["net_sales"].sum()) if not sub_year.empty else 0.0
        gross_sales = (
            float(sub_year["gross_sales"].sum())
            if not sub_year.empty and "gross_sales" in sub_year.columns
            else net_sales
        )
        units_sold = int(sub_year["units"].sum()) if not sub_year.empty and "units" in sub_year.columns else 0
        active_stores = int(sub_year["site_id"].nunique()) if not sub_year.empty else 0

        # Match exact SPLY months present in current year historical summary
        cur_months = set(sub_year["month"].unique()) if not sub_year.empty and "month" in sub_year.columns else set()
        prior = sub[sub["year"] == prior_year]
        if cur_months and len(cur_months) < 12 and "month" in prior.columns:
            prior = prior[prior["month"].isin(cur_months)]

        prior_net = float(prior["net_sales"].sum()) if not prior.empty else 0.0
        prior_units = int(prior["units"].sum()) if not prior.empty and "units" in prior.columns else 0

    yoy_growth = (
        round(((net_sales - prior_net) / prior_net * 100), 1) if prior_net > 0 else 0.0
    )
    units_yoy_growth = (
        round(((units_sold - prior_units) / prior_units * 100), 1) if prior_units > 0 else 0.0
    )

    sales_for_geo = sub_year if isinstance(sub_year, pd.DataFrame) and not sub_year.empty else None

    def _matched_net(t_sub: pd.DataFrame) -> float:
        matched_sales = _sales_matching_targets(sub_year, t_sub)
        return (
            float(matched_sales["net_sale_amount"].sum())
            if not matched_sales.empty and "net_sale_amount" in matched_sales.columns
            else 0.0
        )

    target_total, achievement_pct, target_unavailable = _summary_targets(
        request, year_val, zone=zone, channel=channel, site_ids=site_ids,
        sales_for_geo=sales_for_geo, matched_net=_matched_net,
    )

    from api.routes.sales_dashboard import _kpis, _get_soh_map
    sales_kpis = _kpis(
        sub_year,
        soh_map=_get_soh_map(),
        df_prior=sub_prior,
        scope=_summary_kpi_scope(request, zone=zone, channel=channel, site_ids=site_ids, season_str=season_str),
    )
    # Single source of truth with Sales Overview cards
    gross_sales = float(sales_kpis.get("gross_sales", gross_sales) or 0.0)
    net_sales = float(sales_kpis.get("net_sales", net_sales) or 0.0)
    units_sold = int(sales_kpis.get("units_sold", units_sold) or 0)
    total_soh = int(sales_kpis.get("current_soh", 0) or 0)
    sku_count = int(sub_year["sku"].nunique()) if "sku" in sub_year.columns else 0
    # Recompute YoY using scoped net (prior already filter-scoped via _home_sales)
    yoy_growth = (
        round(((net_sales - prior_net) / prior_net * 100), 1) if prior_net > 0 else 0.0
    )
    units_yoy_growth = (
        round(((units_sold - prior_units) / prior_units * 100), 1) if prior_units > 0 else 0.0
    )

    res = {
        "ok": True,
        "year": year_val,
        "prior_year": prior_year,
        "net_sales": round(net_sales, 2),
        "gross_sales": round(gross_sales, 2),
        "target": None if target_unavailable else round(target_total, 2),
        "achievement_pct": achievement_pct,
        "target_unavailable": bool(target_unavailable),
        "units_sold": units_sold,
        "active_stores": active_stores,
        "yoy_growth_pct": yoy_growth,
        "units_yoy_growth_pct": units_yoy_growth,
        "prior_net_sales": round(prior_net, 2),
        "prior_units_sold": prior_units,
        "total_soh": total_soh,
        "sku_count": sku_count,
        "query_engine": _home_sales_query_engine(),
        "sales_kpis": sales_kpis,
    }
    _exec_store(request, "home-summary", cache_key, res)
    return res


@router.get("/breakdowns")
def get_executive_breakdowns(
    request: Request,
    year: int = Query(DEFAULT_YEAR, description="Calendar year: 2024, 2025, or 2026"),
    zone: str = Query("all"),
    channel: str = Query("all"),
    site_ids: str = Query(""),
    season: str = Query("", description="Season filter (comma-separated) or empty for all"),
):
    """
    Channel / Zone / Categories / SKUs from year-scoped sales lines
    (Sales-Master for 2024/2025, COMBINED_SALES for 2026) — same store_type channels as Sales Overview.
    """
    # v11: charts only. Store and SKU tables are separate paged endpoints.
    cache_key = _home_cache_key("breakdowns-v12-data-rev", request, year)
    cached, _bd_leader, _bd_ready = _exec_wait_or_build(request, "home-breakdowns", cache_key)
    if cached is not None:
        return cached

    try:
        return _get_executive_breakdowns_uncached(
            request,
            year=year,
            zone=zone,
            channel=channel,
            site_ids=site_ids,
            season=season,
            cache_key=cache_key,
        )
    finally:
        if _bd_ready is not None:
            _exec_release(cache_key, _bd_ready)


def _get_executive_breakdowns_uncached(
    request: Request,
    *,
    year: int,
    zone: str,
    channel: str,
    site_ids: str,
    season: str,
    cache_key: str,
):
    year_val = normalize_dashboard_year(year)
    raw_site_ids = str(_val(site_ids, "")).strip()
    raw_channel = str(_val(channel, "all")).strip()
    raw_zone = str(_val(zone, "all")).strip()

    try:
        df_sales = _home_sales(request, year_val)
    except Exception as exc:
        logger.exception("breakdowns sales load failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    # `_home_sales` already applies site_ids / zone / channel / season from the request.
    # Do NOT re-filter site_ids against display `site_id` names — that dropped North from
    # ~₹11.5Cr to ~₹1.5L (CM codes live on site_code / site_manual_code).
    sub = df_sales if df_sales is not None else pd.DataFrame()
    seasons_list: list[str] = []
    if not sub.empty and "season" in sub.columns:
        seasons_list = sorted(
            {
                str(s).strip()
                for s in sub["season"].dropna().astype(str).tolist()
                if str(s).strip() and str(s).strip().lower() not in {"nan", "none", "", "all"}
            }
        )

    # 1. Channel — use store_type (PT/SSL/CENTRO/ICONIC/EBO), not CM "OTHER" join
    channel_sales: list[dict[str, Any]] = []
    if not sub.empty and "store_type" not in sub.columns:
        for src in ("sales_channel", "channel"):
            if src in sub.columns:
                sub = sub.copy()
                sub["store_type"] = sub[src].astype(str).str.strip()
                break
    if not sub.empty and "store_type" in sub.columns:
        ch_grp = (
            sub.groupby("store_type", as_index=False, observed=True)
            .agg(net_sales=("net_sale_amount", "sum"), units=("qty", "sum"))
            .sort_values("net_sales", ascending=False)
        )
        channel_sales = [
            {
                "name": str(r["store_type"]),
                "value": round(float(r["net_sales"]), 2),
                "units": int(r["units"]),
            }
            for _, r in ch_grp.iterrows()
            if str(r["store_type"]).strip().upper() in _VALID_CHANNELS
        ]
    channel_total = float(sum(c["value"] for c in channel_sales))
    for c in channel_sales:
        c["share_pct"] = round((c["value"] / channel_total) * 100, 1) if channel_total > 0 else 0.0

    # Pre-load prior_df once for zone sales and store growth
    prior_df = None
    try:
        prior_df = _home_sales(request, year_val, prior=True)
    except Exception:
        prior_df = None

    # 2. Zone
    zone_sales: list[dict[str, Any]] = []
    if not sub.empty and "zone" in sub.columns:
        z_agg: dict[str, Any] = {"net_sales": ("net_sale_amount", "sum"), "units": ("qty", "sum")}
        if "site_id" in sub.columns:
            z_agg["stores"] = ("site_id", "nunique")
        z_grp = (
            sub.groupby("zone", as_index=False, observed=True)
            .agg(**z_agg)
            .sort_values("net_sales", ascending=False)
        )
        prior_map: dict[str, float] = {}
        if prior_df is not None and not prior_df.empty and "zone" in prior_df.columns:
            prior_map = (
                prior_df.groupby(prior_df["zone"].astype(str).str.strip(), observed=True)["net_sale_amount"]
                .sum()
                .to_dict()
            )
        for _, r in z_grp.iterrows():
            z_name = str(r["zone"]).strip()
            if not z_name or z_name.lower() in {"unknown", "nan", "-", ""}:
                continue
            cur = float(r["net_sales"])
            prev = float(prior_map.get(z_name, 0.0))
            growth = round(((cur - prev) / prev * 100), 1) if prev > 0 else 0.0
            zone_sales.append(
                {
                    "zone": z_name,
                    "sales": round(cur, 2),
                    "units": int(r["units"]),
                    "growth": growth,
                    "stores": int(r["stores"]) if "stores" in r.index and pd.notna(r["stores"]) else 0,
                    "target": 0.0,
                    "achievement": 0.0,
                    "share_pct": 0.0,
                }
            )
        zone_total = float(sum(z["sales"] for z in zone_sales))
        for z in zone_sales:
            z["share_pct"] = round((z["sales"] / zone_total) * 100, 1) if zone_total > 0 else 0.0

    # Attach real Target LFS totals per zone — same channel/site/zones cut as sales
    try:
        target_df = target_vs_achieved._load_target_df()
        t_yr = _filter_target_by_fy(target_df, year_val)
        t_yr = _home_targets(request, t_yr)
        if not t_yr.empty:
            t_yr = t_yr.copy()
            t_yr["site_id"] = t_yr["site_code"].map(_clean_site_id)
            t_yr["channel"] = t_yr["format"].astype(str)
            zone_map, _ = _build_site_geo_lookup(sub)
            t_yr["zone"] = t_yr["site_id"].map(
                lambda s: zone_map.get(str(s), "") or zone_map.get(_clean_site_id(s), "")
            )
            zones_csv = str(request.query_params.get("zones") or "").strip()
            t_yr = _filter_summary(
                t_yr,
                zone=raw_zone,
                zones=zones_csv,
                channel=raw_channel or request.query_params.get("channel", "all"),
                site_ids=raw_site_ids or request.query_params.get("site_ids", ""),
            )
            z_tgt = (
                t_yr[t_yr["zone"].astype(str).str.strip().ne("")]
                .groupby(t_yr["zone"].astype(str).str.strip(), observed=True)["target"]
                .sum()
                .to_dict()
            )
            for row in zone_sales:
                tgt = float(z_tgt.get(row["zone"], 0.0) or 0.0)
                row["target"] = round(tgt, 2)
                row["achievement"] = (
                    round((float(row["sales"]) / tgt) * 100, 1) if tgt > 0 else 0.0
                )
    except Exception as exc:
        logger.warning("zone target attach failed: %s", exc)

    # 2b. State & City sales for Map widget
    state_sales: list[dict[str, Any]] = []
    if not sub.empty and "state" in sub.columns:
        st_cols = ["state"]
        if "zone" in sub.columns:
            st_cols.append("zone")
        st_grp = (
            sub.groupby(st_cols, as_index=False, observed=True)
            .agg(net_sales=("net_sale_amount", "sum"), units=("qty", "sum"))
            .sort_values("net_sales", ascending=False)
        )
        for _, r in st_grp.iterrows():
            st_name = str(r["state"]).strip()
            if not st_name or st_name.lower() in {"unknown", "nan", "-", "", "none", "null"}:
                continue
            z_name = str(r["zone"]).strip() if "zone" in r and pd.notna(r["zone"]) else "Central"
            state_sales.append(
                {
                    "state": st_name,
                    "zone": z_name,
            "sales": round(float(r["net_sales"]), 2),
            "units": int(r["units"]),
        }
            )

    city_sales: list[dict[str, Any]] = []
    # Prefer city_name (Customer Master) when present — raw city is often blank.
    city_col = next((c for c in ("city", "city_name") if c in sub.columns), None)
    if not sub.empty and city_col:
        ct_cols = [city_col]
        if "state" in sub.columns:
            ct_cols.append("state")
        elif "state_name" in sub.columns:
            ct_cols.append("state_name")
        if "zone" in sub.columns:
            ct_cols.append("zone")
        ct_agg: dict[str, Any] = {"net_sales": ("net_sale_amount", "sum"), "units": ("qty", "sum")}
        if "site_id" in sub.columns:
            ct_agg["stores"] = ("site_id", "nunique")
        ct_grp = (
            sub.groupby(ct_cols, as_index=False, observed=True)
            .agg(**ct_agg)
            .sort_values("net_sales", ascending=False)
            .head(35)
        )
        for _, r in ct_grp.iterrows():
            c_name = str(r[city_col]).strip()
            if not c_name or c_name.lower() in {"unknown", "nan", "-", "", "none", "null"}:
                continue
            st_name = ""
            if "state" in r and pd.notna(r.get("state")):
                st_name = str(r["state"]).strip()
            elif "state_name" in r and pd.notna(r.get("state_name")):
                st_name = str(r["state_name"]).strip()
            z_name = str(r["zone"]).strip() if "zone" in r and pd.notna(r["zone"]) else "Central"
            city_sales.append(
                {
                    "city": c_name,
                    "state": st_name,
                    "zone": z_name,
                    "sales": round(float(r["net_sales"]), 2),
                    "units": int(r["units"]),
                    "stores": int(r["stores"]) if "stores" in r.index and pd.notna(r["stores"]) else 0,
                }
            )

    # Store and SKU tables are NOT included here. Each table has its own paged API
    # (/top-stores, /bottom-stores, /skus) so the first response is not the whole list.
    top_stores: list[dict[str, Any]] = []
    bottom_stores: list[dict[str, Any]] = []

    # 4. Departments / categories (chart-sized). SKU table rows load from /skus.
    top_skus: list[dict[str, Any]] = []
    skus_list: list[str] = []
    barcodes_list: list[str] = []
    departments_list: list[dict[str, Any]] = []
    category_sales: list[dict[str, Any]] = []
    if not sub.empty:
        if "sku" in sub.columns and "department" in sub.columns:
            skus_list = sorted([str(x) for x in sub["sku"].dropna().unique() if str(x).strip()])[:1200]
        if "barcode" in sub.columns:
            barcodes_list = sorted(
                [str(x).replace(".0", "") for x in sub["barcode"].dropna().unique() if str(x).strip()]
            )[:1200]
        if "department" in sub.columns:
                dept_grp = (
                sub.groupby("department", as_index=False, observed=True)
                .agg(net_sale_amount=("net_sale_amount", "sum"))
                    .sort_values("net_sale_amount", ascending=False)
                .head(15)
                )
                departments_list = [
                    {"name": str(r["department"]), "sales": round(float(r["net_sale_amount"]), 2)}
                    for _, r in dept_grp.iterrows()
                    if str(r["department"]).strip()
                ]

        sec_col = "section" if "section" in sub.columns else ("category" if "category" in sub.columns else None)
        if sec_col:
            sec_grp = (
                sub.groupby(sec_col, as_index=False, observed=True)
                .agg(net_sale_amount=("net_sale_amount", "sum"))
                .sort_values("net_sale_amount", ascending=False)
                .head(6)
            )
            category_sales = [
                {"name": str(r[sec_col]), "size": round(float(r["net_sale_amount"]), 2), "sales": round(float(r["net_sale_amount"]), 2)}
                for _, r in sec_grp.iterrows()
                if str(r[sec_col]).strip()
            ]
        else:
            category_sales = [
                {"name": d["name"], "size": d["sales"], "sales": d["sales"]} for d in departments_list
            ]
        cat_total = float(sum(float(c.get("sales") or c.get("size") or 0) for c in category_sales))
        for c in category_sales:
            sales_v = float(c.get("sales") or c.get("size") or 0)
            c["share_pct"] = round((sales_v / cat_total) * 100, 1) if cat_total > 0 else 0.0

    res = {
        "ok": True,
        "year": year_val,
        "channel_sales": channel_sales,
        "channel_total": round(float(sum(c["value"] for c in channel_sales)), 2),
        "zone_sales": zone_sales,
        "zone_total": round(float(sum(z["sales"] for z in zone_sales)), 2),
        "state_sales": state_sales,
        "city_sales": city_sales,
        "top_stores": top_stores,
        "bottom_stores": bottom_stores,
        "top_skus": top_skus,
        "skus": skus_list,
        "barcodes": barcodes_list,
        "seasons": seasons_list,
        "departments": departments_list,
        "category_sales": category_sales,
        "category_total": round(
            float(sum(float(c.get("sales") or c.get("size") or 0) for c in category_sales)), 2
        ),
    }
    _exec_store(request, "home-breakdowns", cache_key, res)
    return res


def _page_slice(rows: list[dict[str, Any]], page: int, page_size: int) -> tuple[list[dict[str, Any]], int]:
    total = len(rows)
    start = (max(1, page) - 1) * page_size
    return rows[start : start + page_size], total


def _sort_table_rows(rows: list[dict[str, Any]], sort: str, sort_dir: str, default: str) -> list[dict[str, Any]]:
    key = sort if rows and sort in rows[0] else default
    reverse = str(sort_dir or "asc").lower() == "desc"

    def _key(row: dict[str, Any]) -> tuple:
        value = row.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return (0, float(value))
        return (1, str(value or "").lower())

    return sorted(rows, key=_key, reverse=reverse)


def _store_table_page(
    request: Request,
    *,
    year: int,
    variant: str,
    zone: str,
    channel: str,
    site_ids: str,
    page: int,
    page_size: int,
    search: str,
    sort: str,
    sort_dir: str,
) -> dict[str, Any]:
    cache_key = _home_cache_key(f"stores-{variant}-v2-data-rev", request, year)
    cached = _exec_cached(request, "home-stores", cache_key)
    if cached is not None:
        return cached

    year_val = normalize_dashboard_year(year)
    try:
        sub = _home_sales(request, year_val)
    except Exception as exc:
        logger.exception("store table sales load failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    prior_df = None
    try:
        prior_df = _home_sales(request, year_val, prior=True)
    except Exception:
        prior_df = None
    top_rows, bottom_rows = _ranked_target_stores(
        sub if sub is not None else pd.DataFrame(),
        year_val,
        top_n=10,
        channel=str(_val(channel, "all")),
        zone=str(_val(zone, "all")),
        site_ids=str(_val(site_ids, "")),
        request=request,
        prior_df=prior_df,
    )
    rows = top_rows if variant == "top" else bottom_rows
    query = str(_val(search, "") or "").strip().lower()
    if query:
        rows = [
            row
            for row in rows
            if query in str(row.get("store", "")).lower() or query in str(row.get("zone", "")).lower()
        ]
    rows = _sort_table_rows(rows, str(_val(sort, "rank") or "rank"), str(_val(sort_dir, "asc") or "asc"), "rank")
    page_num = int(_val(page, 1) or 1)
    size_num = max(1, min(int(_val(page_size, 5) or 5), 50))
    page_rows, total = _page_slice(rows, page_num, size_num)
    res = {
        "ok": True,
        "variant": variant,
        "year": year_val,
        "rows": page_rows,
        "total": total,
        "page": page_num,
        "page_size": size_num,
    }
    _exec_store(request, "home-stores", cache_key, res)
    return res


def _enrich_sku_page(sub: pd.DataFrame, request: Request, page_df: pd.DataFrame) -> list[dict[str, Any]]:
    if page_df is None or page_df.empty:
        return []
    sku_img_map = build_sku_image_map()
    from api.routes.sales_dashboard import _get_soh_map, _sku_soh_for_scope

    q = request.query_params
    zones_q = str(q.get("zones") or "").strip()
    zone = str(q.get("zone") or "").strip()
    if not zones_q and zone and zone.lower() not in ("", "all"):
        zones_q = zone
    sku_soh_dict = _sku_soh_for_scope(
        _get_soh_map(),
        scope={
            "zones": zones_q,
            "site_ids": str(q.get("site_ids") or ""),
            "site_id": "",
            "channel": str(q.get("channel") or "all"),
            "season": str(q.get("season") or ""),
            "sku": str(q.get("sku") or ""),
            "barcode": "",
            "barcodes": str(q.get("barcodes") or ""),
            "departments": str(q.get("departments") or ""),
            "sections": str(q.get("sections") or ""),
        },
        sales_df=sub,
    )
    out: list[dict[str, Any]] = []
    for _, r in page_df.iterrows():
        sku_name = str(r["sku"]).strip()
        sales_val = round(float(r["net_sale_amount"]), 2)
        gross_val = float(r.get("gross_mrp", 0.0) or 0.0)
        units_val = int(r["qty"])
        raw_stock = float(sku_soh_dict.get(sku_name.upper(), sku_soh_dict.get(sku_name, 0.0)) or 0.0)
        stock_val = int(raw_stock) if raw_stock > 0 else 0
        margin_pct = round((sales_val / gross_val * 100), 1) if gross_val > 0 else 0.0
        denom = units_val + stock_val
        str_pct = round(min(100.0, (units_val / denom) * 100.0), 1) if denom > 0 else 0.0
        out.append(
            {
                "sku": sku_name,
                "category": str(r["department"]).strip() if str(r["department"]).strip() else "Apparel",
                "sales": sales_val,
                "units": units_val,
                "stock": stock_val,
                "margin": margin_pct,
                "margin_pct": margin_pct,
                "sellThrough": str_pct,
                "sell_through": str_pct,
                "image_url": sku_img_map.get(sku_name, "")
                or sku_img_map.get(sku_name.upper(), "")
                or f"/api/product-images/{sku_name}",
            }
        )
    return out


@router.get("/top-stores")
def get_top_stores(
    request: Request,
    year: int = Query(DEFAULT_YEAR),
    zone: str = Query("all"),
    channel: str = Query("all"),
    site_ids: str = Query(""),
    page: int = Query(1, ge=1),
    page_size: int = Query(5, ge=1, le=50),
    search: str = Query(""),
    sort: str = Query("rank"),
    sort_dir: str = Query("asc"),
):
    """First page of top stores. Next page, search, and sort each hit this endpoint again."""
    return _store_table_page(
        request,
        year=year,
        variant="top",
        zone=zone,
        channel=channel,
        site_ids=site_ids,
        page=page,
        page_size=page_size,
        search=search,
        sort=sort,
        sort_dir=sort_dir,
    )


@router.get("/bottom-stores")
def get_bottom_stores(
    request: Request,
    year: int = Query(DEFAULT_YEAR),
    zone: str = Query("all"),
    channel: str = Query("all"),
    site_ids: str = Query(""),
    page: int = Query(1, ge=1),
    page_size: int = Query(5, ge=1, le=50),
    search: str = Query(""),
    sort: str = Query("rank"),
    sort_dir: str = Query("asc"),
):
    """First page of bottom stores. Later pages are a separate request."""
    return _store_table_page(
        request,
        year=year,
        variant="bottom",
        zone=zone,
        channel=channel,
        site_ids=site_ids,
        page=page,
        page_size=page_size,
        search=search,
        sort=sort,
        sort_dir=sort_dir,
    )


@router.get("/skus")
def get_home_skus(
    request: Request,
    year: int = Query(DEFAULT_YEAR),
    view: str = Query("top", description="top | bottom | all"),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=500),
    search: str = Query(""),
):
    """One page of the SKU table. view=top/bottom is 10 rows; view=all is paged."""
    cache_key = _home_cache_key("skus-v2-data-rev", request, year)
    cached = _exec_cached(request, "home-skus", cache_key)
    if cached is not None:
        return cached

    year_val = normalize_dashboard_year(year)
    view_name = str(_val(view, "top") or "top").strip().lower()
    if view_name not in {"top", "bottom", "all"}:
        view_name = "top"
    page_num = int(_val(page, 1) or 1)
    size_num = max(1, min(int(_val(page_size, 10) or 10), 500))
    try:
        sub = _home_sales(request, year_val)
    except Exception as exc:
        logger.exception("sku table sales load failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if sub is None:
        sub = pd.DataFrame()

    grouped = pd.DataFrame(columns=["sku", "department", "net_sale_amount", "gross_mrp", "qty"])
    if not sub.empty and "sku" in sub.columns and "department" in sub.columns:
        work = sub
        if "gross_mrp" not in work.columns:
            work = work.copy()
            mrp_s = (
                pd.to_numeric(work["mrp"], errors="coerce").fillna(0)
                if "mrp" in work.columns
                else pd.to_numeric(work["net_sale_amount"], errors="coerce").fillna(0)
            )
            qty_s = pd.to_numeric(work["qty"], errors="coerce").fillna(0) if "qty" in work.columns else 1
            work["gross_mrp"] = mrp_s * qty_s
        grouped = (
            work.groupby(["sku", "department"], as_index=False, observed=True)
            .agg(
                net_sale_amount=("net_sale_amount", "sum"),
                gross_mrp=("gross_mrp", "sum"),
                qty=("qty", "sum"),
            )
        )
        query = str(_val(search, "") or "").strip().lower()
        if query:
            sku_s = grouped["sku"].astype(str).str.lower()
            dept_s = grouped["department"].astype(str).str.lower()
            grouped = grouped[sku_s.str.contains(query, regex=False) | dept_s.str.contains(query, regex=False)]
        ascending = view_name == "bottom"
        grouped = grouped.sort_values("net_sale_amount", ascending=ascending, kind="mergesort")
        if view_name in {"top", "bottom"}:
            grouped = grouped.head(10)

    total = int(len(grouped))
    start = (max(1, page_num) - 1) * size_num
    page_df = grouped.iloc[start : start + size_num]
    res = {
        "ok": True,
        "year": year_val,
        "view": view_name,
        "rows": _enrich_sku_page(sub, request, page_df),
        "total": total,
        "page": page_num,
        "page_size": size_num,
    }
    _exec_store(request, "home-skus", cache_key, res)
    return res


def warm_home_page_caches(year: int | None = None) -> None:
    """Pre-build Home KPI / map / trend / target responses so first UI paint is a cache hit."""
    from api.page_response_cache import _frontend_request

    t0 = time.perf_counter()
    y = int(normalize_dashboard_year(year, default=DEFAULT_YEAR))
    home_q = f"year={y}&timeline_mode=fy_year&timeline_values=FY{str(y)[-2:]}"
    req = _frontend_request(home_q)
    target_req = _frontend_request(home_q + "&group_by=zone")
    steps = (
        ("summary", lambda: get_executive_summary(req, year=y, zone="all", channel="all", site_ids="", season="")),
        ("breakdowns", lambda: get_executive_breakdowns(req, year=y, zone="all", channel="all", site_ids="", season="")),
        ("trend", lambda: get_multi_year_trend(req, year=y)),
        ("target", lambda: get_target_vs_achievement(target_req, year=y, group_by="zone", zone="all", channel="all", site_ids="")),
        ("top-stores", lambda: get_top_stores(req, year=y, page=1, page_size=5)),
        ("bottom-stores", lambda: get_bottom_stores(req, year=y, page=1, page_size=5)),
        ("skus", lambda: get_home_skus(req, year=y, view="top", page=1, page_size=10)),
    )
    for name, fn in steps:
        try:
            fn()
            logger.info("Home warm %s ok", name)
        except Exception as exc:
            logger.warning("Home warm %s failed: %s", name, exc)
    logger.info("Home page caches warm done in %.2fs", time.perf_counter() - t0)

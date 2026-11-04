"""
Manager Performance API — aggregates COMBINED_SALES by Cluster (RM / Area Manager) from Customer Master.

Endpoints:
  GET /api/managers/overview   — ranked managers list with store drilldown + filter options
  GET /api/managers/export     — Excel export (Manager Summary + Store Details sheets)
"""
from __future__ import annotations

import io
import logging
import os
import re
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
import xlsxwriter

import time
import threading

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/managers", tags=["Manager Performance"])

_API_CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "api_cache"

_CACHE_LOCK = threading.RLock()
_SALES_CACHE_DF: dict[int | str, tuple[float, pd.DataFrame]] = {}
_SALES_CACHE_TTL: float = 600.0  # 10 minutes

_OVERVIEW_RESULT_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_OVERVIEW_CACHE_TTL: float = 3600.0  # 1h RAM; page_cache/blob is durable across restarts

_TARGET_MAP_CACHE: dict[str, tuple[float, dict[str, float]]] = {}
_SOH_MAP_CACHE: dict[str, tuple[float, dict[str, int]]] = {}
_STORE_SUMMARY_CACHE: dict[str, tuple[float, pd.DataFrame]] = {}
_SEASONS_BY_YEAR: dict[int, list[str]] = {}
# year → (from_date ISO, to_date ISO) for loaded sales (actual YTD within FY)
_SALES_DATE_BOUNDS: dict[int, tuple[str | None, str | None]] = {}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _default_fy_year() -> int:
    try:
        from api.sales_year_loader import get_default_dashboard_year

        return int(get_default_dashboard_year())
    except Exception:
        return 2027


def _sales_period_bounds(year: int) -> tuple[str | None, str | None]:
    """Actual min/max sales dates for the FY cards badge (YTD when year is open)."""
    if year in _SALES_DATE_BOUNDS:
        return _SALES_DATE_BOUNDS[year]
    if year and year > 0:
        return f"{year - 1}-04-01", f"{year}-03-31"
    return None, None


def _fy_years_covering_dates(date_from: str | None, date_to: str | None) -> list[int]:
    """Indian FY end-years overlapping an optional custom date range."""
    if not date_from and not date_to:
        return []
    try:
        a = pd.Timestamp(date_from or date_to)
        b = pd.Timestamp(date_to or date_from)
        if pd.isna(a) or pd.isna(b):
            return []
        lo, hi = (a, b) if a <= b else (b, a)

        def _fy_end(ts: pd.Timestamp) -> int:
            return int(ts.year + 1) if int(ts.month) >= 4 else int(ts.year)

        y0, y1 = _fy_end(lo), _fy_end(hi)
        from api.sales_year_loader import supported_dashboard_years

        allowed = supported_dashboard_years()
        return [y for y in range(min(y0, y1), max(y0, y1) + 1) if y in allowed]
    except Exception:
        return []


def _load_sales(
    year: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> pd.DataFrame:
    """Load sales for the requested financial year (2027 = FY27, 2026 = FY26, 0 = All Years)."""
    global _SALES_CACHE_DF
    if year is None:
        year = _default_fy_year()
    # Custom date range on All Years → only FYs that overlap the range (faster + correct)
    covering = _fy_years_covering_dates(date_from, date_to) if year == 0 else []
    cache_key: int | str = (
        f"range:{','.join(str(y) for y in covering)}|{date_from or ''}|{date_to or ''}"
        if covering
        else year
    )
    now = time.time()
    with _CACHE_LOCK:
        if cache_key in _SALES_CACHE_DF:
            cached_time, cached_df = _SALES_CACHE_DF[cache_key]
            if (
                (now - cached_time < _SALES_CACHE_TTL)
                and not cached_df.empty
                and "season" in cached_df.columns
                and "date" in cached_df.columns
            ):
                return cached_df

    df = pd.DataFrame()
    try:
        from api.sales_year_loader import load_sales_for_year

        if covering:
            parts: list[pd.DataFrame] = []
            for y in covering:
                part = load_sales_for_year(y)
                if part is not None and not part.empty:
                    parts.append(part)
            if parts:
                df = pd.concat(parts, ignore_index=True) if len(parts) > 1 else parts[0]
        else:
            df = load_sales_for_year(year)
        if df is not None and not df.empty:
            logger.info("Manager performance loaded %s rows for year %s", len(df), year)
    except Exception as exc:
        logger.warning("Failed to load_sales_for_year(%s): %s", year, exc)

    if df is None or df.empty:
        try:
            from api.routes.sales_dashboard import load_ready_overview_frame
            ready = load_ready_overview_frame()
            if ready is not None and not ready.empty:
                df = ready
                if year != 0 and "date" in df.columns:
                    min_d = pd.Timestamp(f"{year-1}-04-01")
                    max_d = pd.Timestamp(f"{year}-03-31 23:59:59")
                    dt = pd.to_datetime(df["date"], errors="coerce")
                    df = df[(dt >= min_d) & (dt <= max_d)].copy()
        except Exception as exc:
            logger.warning("Ready overview frame fallback skipped: %s", exc)

    if df is None or df.empty:
        try:
            from api.routes.sales_dashboard import _get_sales_df
            df = _get_sales_df()
        except Exception as exc:
            logger.warning("Could not load sales via _get_sales_df: %s — trying parquet cache", exc)

    if df is None or df.empty:
        # Fallback: latest parquet file from cache dir
        files = sorted(
            _API_CACHE_DIR.glob("sales_dashboard_*.parquet"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if files:
            try:
                df = pd.read_parquet(files[0])
            except Exception:
                df = pd.DataFrame()

    if df is not None and not df.empty:
        # Enrich cluster / customer master if missing
        if "cluster" not in df.columns or df["cluster"].astype(str).str.strip().replace({"nan": "", "None": "", "<NA>": "", "<na>": ""}).eq("").all():
            try:
                from pipeline.customer_master import enrich_sales_with_customer_master
                df = enrich_sales_with_customer_master(df, site_col="site_id")
            except Exception as exc:
                logger.warning("Customer Master enrichment fallback in manager_performance: %s", exc)

        # Column aliases from sales-year / analyzer frames
        if "net_sale_amount" not in df.columns and "sales" in df.columns:
            df["net_sale_amount"] = pd.to_numeric(df["sales"], errors="coerce").fillna(0.0)
        if "qty" not in df.columns and "quantity" in df.columns:
            df["qty"] = pd.to_numeric(df["quantity"], errors="coerce").fillna(0.0)
        if (
            "discount_amount" not in df.columns
            and "gross_sales" in df.columns
            and "net_sale_amount" in df.columns
        ):
            df["discount_amount"] = (
                pd.to_numeric(df["gross_sales"], errors="coerce").fillna(0.0)
                - pd.to_numeric(df["net_sale_amount"], errors="coerce").fillna(0.0)
            ).clip(lower=0.0)

        # Actual data window for KPI period (not nominal 31-Mar when FY still open)
        if "date" in df.columns:
            try:
                dt = pd.to_datetime(df["date"], errors="coerce")
                if dt.notna().any():
                    _SALES_DATE_BOUNDS[year] = (
                        pd.Timestamp(dt.min()).normalize().strftime("%Y-%m-%d"),
                        pd.Timestamp(dt.max()).normalize().strftime("%Y-%m-%d"),
                    )
                elif year and year > 0:
                    _SALES_DATE_BOUNDS[year] = (f"{year - 1}-04-01", f"{year}-03-31")
            except Exception:
                if year and year > 0:
                    _SALES_DATE_BOUNDS[year] = (f"{year - 1}-04-01", f"{year}-03-31")
        elif year and year > 0:
            _SALES_DATE_BOUNDS[year] = (f"{year - 1}-04-01", f"{year}-03-31")

        # Keep season + date so metrics can be scoped without dropping the manager roster
        needed = [
            "cluster", "zone", "city", "state", "channel", "mrp", "qty", "net_sale_amount",
            "discount_amount", "site_id", "site_name", "season", "date",
        ]
        cols = [c for c in needed if c in df.columns]
        df = df[cols].copy()

        for c in df.columns:
            if isinstance(df[c].dtype, pd.CategoricalDtype):
                df[c] = df[c].astype(str)

        if "season" in df.columns:
            seasons = sorted(
                {
                    str(v).strip()
                    for v in df["season"].dropna().astype(str)
                    if str(v).strip() and str(v).strip().lower() not in ("nan", "none", "", "null")
                },
                key=str.upper,
            )
            _SEASONS_BY_YEAR[year if isinstance(cache_key, int) else 0] = seasons

        with _CACHE_LOCK:
            _SALES_CACHE_DF[cache_key] = (now, df)

    return df if df is not None else pd.DataFrame()


def _load_soh_by_site(seasons: list[str] | None = None) -> dict[str, int]:
    """Return {site_id: total_soh} from daily_inventory_movement (optionally season-scoped)."""
    seasons = seasons or []
    cache_key = ",".join(sorted(s.strip().upper() for s in seasons if s.strip())) or "__all__"
    now = time.time()
    with _CACHE_LOCK:
        hit = _SOH_MAP_CACHE.get(cache_key)
        if hit is not None and (now - hit[0] < 300.0):
            return hit[1]

    try:
        from api.current_soh import site_soh_int_map, site_soh_int_map_for_seasons, total_current_soh

        res = site_soh_int_map_for_seasons(seasons) if seasons else site_soh_int_map()
        logger.info(
            "Manager SOH map from daily_inventory_movement: stores=%d seasons=%s",
            len(res),
            seasons or "ALL",
        )
        if not seasons:
            total_current_soh()  # warm / log network total
    except Exception as exc:
        logger.warning("Manager daily SOH load failed: %s", exc)
        res = {}

    with _CACHE_LOCK:
        _SOH_MAP_CACHE[cache_key] = (now, res)
    return res


def _load_target_by_site(
    year: int = 2027,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, float]:
    """Return {site_id: total_target} from Target_LFS for FY / optional date range."""
    cache_key = f"{year}|{date_from or ''}|{date_to or ''}"
    now = time.time()
    with _CACHE_LOCK:
        hit = _TARGET_MAP_CACHE.get(cache_key)
        if hit is not None and (now - hit[0] < 300.0):
            return hit[1]

    min_date = date_from or (f"{year - 1}-04-01" if year > 0 else None)
    max_date = date_to or (f"{year}-03-31" if year > 0 else None)

    try:
        from api.routes.target_vs_achieved import _load_target_df
        tdf = _load_target_df()
        if tdf.empty:
            return {}
        tdf = tdf.copy()
        if min_date is not None and "date" in tdf.columns:
            tdf = tdf[tdf["date"] >= pd.Timestamp(min_date)]
        if max_date is not None and "date" in tdf.columns:
            tdf = tdf[tdf["date"] <= pd.Timestamp(str(max_date) + " 23:59:59")]
        grouped = tdf.groupby("site_code", as_index=False)["target"].sum()
        res: dict[str, float] = {}
        for _, r in grouped.iterrows():
            sc = str(r["site_code"]).strip()
            tgt = float(r["target"])
            res[sc] = res.get(sc, 0.0) + tgt
            res[sc.upper()] = res.get(sc.upper(), 0.0) + tgt
            parts = sc.split("-")
            if len(parts) > 1 and parts[-1].isdigit():
                suffix = parts[-1].strip()
                res[suffix] = res.get(suffix, 0.0) + tgt
                res[suffix.upper()] = res.get(suffix.upper(), 0.0) + tgt
        with _CACHE_LOCK:
            _TARGET_MAP_CACHE[cache_key] = (now, res)
        return res
    except Exception as exc:
        logger.warning("Target load failed for manager performance: %s", exc)
        return {}


def _aggregate_lines_to_stores(raw_df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate sales lines → one row per manager × store."""
    if raw_df is None or raw_df.empty:
        return pd.DataFrame()

    df = pd.DataFrame()
    for col in ("cluster", "zone", "city", "state", "channel", "site_id", "site_name"):
        if col in raw_df.columns:
            df[col] = _clean_series(raw_df[col])
        else:
            df[col] = ""

    mrp_s = pd.to_numeric(raw_df.get("mrp", 0.0), errors="coerce").fillna(0.0)
    qty_s = pd.to_numeric(raw_df.get("qty", 0.0), errors="coerce").fillna(0.0)
    gross_s = mrp_s * qty_s
    net_sales_s = pd.to_numeric(raw_df.get("net_sale_amount", 0.0), errors="coerce").fillna(0.0)
    if "discount_amount" in raw_df.columns:
        disc_amt_s = pd.to_numeric(raw_df["discount_amount"], errors="coerce").fillna(0.0)
    else:
        disc_amt_s = gross_s - net_sales_s

    df["gross_line"] = gross_s
    df["discount_amount"] = disc_amt_s
    df["qty"] = qty_s
    df["net_sale_amount"] = net_sales_s

    disc_pct_series = ((gross_s - net_sales_s) / gross_s.replace(0, float("nan")) * 100.0).fillna(0.0)
    is_fp = disc_pct_series <= 10.0
    df["fp_qty"] = qty_s.where(is_fp, 0.0)
    df["fp_sales"] = net_sales_s.where(is_fp, 0.0)

    _JUNK_MGR = {"", "0", "0.0", "nan", "none", "<na>", "null", "unassigned", "-", "--"}
    raw_mgr = df["cluster"]
    df["manager"] = raw_mgr.where(~raw_mgr.str.lower().isin(_JUNK_MGR), "")
    df = df[df["manager"].ne("")]
    if df.empty:
        return pd.DataFrame()

    group_cols = ["manager", "site_id", "site_name", "city", "zone", "state", "channel"]
    return (
        df.groupby(group_cols, as_index=False, observed=True)
        .agg(
            gross_sales=("gross_line", "sum"),
            discount_amount=("discount_amount", "sum"),
            net_sales=("net_sale_amount", "sum"),
            units=("qty", "sum"),
            fp_units=("fp_qty", "sum"),
            fp_sales=("fp_sales", "sum"),
        )
    )


def _filter_sales_metrics(
    raw_df: pd.DataFrame,
    seasons: list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> pd.DataFrame:
    """Filter line-level sales by season SKUs and/or date range (metrics only)."""
    if raw_df is None or raw_df.empty:
        return raw_df
    out = raw_df
    seasons = [s.strip() for s in (seasons or []) if str(s).strip()]
    if seasons and "season" in out.columns:
        allowed = {s.upper() for s in seasons}
        out = out[_clean_series(out["season"]).str.upper().isin(allowed)]
    if (date_from or date_to) and "date" in out.columns:
        dt = pd.to_datetime(out["date"], errors="coerce")
        mask = dt.notna()
        if date_from:
            mask &= dt >= pd.Timestamp(date_from)
        if date_to:
            mask &= dt <= pd.Timestamp(str(date_to) + " 23:59:59")
        out = out[mask]
    return out


def _get_store_summary_df(
    year: int = 2027,
    seasons: list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> pd.DataFrame:
    """
    Manager × store roster always from full FY sales.
    Sales / units / discount metrics left-joined after season + date filters,
    so the manager list stays complete while numbers follow the selected scope.
    """
    seasons = seasons or []
    cache_key = f"{year}|{','.join(sorted(s.upper() for s in seasons))}|{date_from or ''}|{date_to or ''}"
    now = time.time()
    with _CACHE_LOCK:
        hit = _STORE_SUMMARY_CACHE.get(cache_key)
        if hit is not None and (now - hit[0] < _SALES_CACHE_TTL) and not hit[1].empty:
            return hit[1]

    raw_df = _load_sales(year, date_from=date_from, date_to=date_to)
    if raw_df.empty:
        return pd.DataFrame()

    roster = _aggregate_lines_to_stores(raw_df)
    if roster.empty:
        return pd.DataFrame()

    keys = ["manager", "site_id", "site_name", "city", "zone", "state", "channel"]
    metric_cols = ["gross_sales", "discount_amount", "net_sales", "units", "fp_units", "fp_sales"]

    if seasons or date_from or date_to:
        metrics_raw = _filter_sales_metrics(raw_df, seasons=seasons, date_from=date_from, date_to=date_to)
        metrics = _aggregate_lines_to_stores(metrics_raw)
        if metrics.empty:
            store_grp = roster[keys].copy()
            for c in metric_cols:
                store_grp[c] = 0.0
        else:
            store_grp = roster[keys].merge(metrics, on=keys, how="left")
            for c in metric_cols:
                store_grp[c] = pd.to_numeric(store_grp[c], errors="coerce").fillna(0.0)
    else:
        store_grp = roster

    with _CACHE_LOCK:
        _STORE_SUMMARY_CACHE[cache_key] = (now, store_grp)
    return store_grp


def _norm_str(v: Any) -> str:
    s = str(v or "").strip()
    return "" if s.lower() in ("nan", "none", "<na>", "null") else s


def _clean_series(s: pd.Series) -> pd.Series:
    if isinstance(s.dtype, pd.CategoricalDtype):
        s = s.astype(str)
    return s.astype(str).str.strip().replace({"nan": "", "None": "", "<NA>": "", "<na>": "", "null": ""})


def _apply_filters(df: pd.DataFrame, zones: list[str], states: list[str], cities: list[str], channels: list[str], managers: list[str]) -> pd.DataFrame:
    if df.empty:
        return df
    if zones:
        z = [z.strip().upper() for z in zones if z.strip()]
        if z:
            df = df[_clean_series(df["zone"]).str.upper().isin(z)]
    if states:
        st = [s.strip().upper() for s in states if s.strip()]
        if st:
            df = df[_clean_series(df["state"]).str.upper().isin(st)]
    if cities:
        c = [c.strip().upper() for c in cities if c.strip()]
        if c:
            df = df[_clean_series(df["city"]).str.upper().isin(c)]
    if channels:
        ch = [ch.strip().upper() for ch in channels if ch.strip()]
        if ch:
            df = df[_clean_series(df["channel"]).str.upper().isin(ch)]
    if managers:
        m = [m.strip().upper() for m in managers if m.strip()]
        if m:
            df = df[_clean_series(df["manager"]).str.upper().isin(m)]
    return df


_SITE_KEYS_LOOKUP_CACHE: dict[str, list[str]] | None = None


def _get_site_keys_lookup() -> dict[str, list[str]]:
    global _SITE_KEYS_LOOKUP_CACHE
    if _SITE_KEYS_LOOKUP_CACHE is not None:
        return _SITE_KEYS_LOOKUP_CACHE
    try:
        from pipeline.customer_master import load_customer_master
        cm = load_customer_master(retail_only=False)
        m: dict[str, list[str]] = {}
        for _, r in cm.iterrows():
            keys = [
                str(k).strip().upper()
                for k in (r.get("site_manual_code"), r.get("site_code"), r.get("site_short_code"), r.get("_site_key"))
                if k and str(k).strip() and str(k).strip().lower() != "nan"
            ]
            if not keys:
                continue
            for name_field in ("site_name_master", "customer_name", "site_code", "site_manual_code", "site_short_code"):
                v = str(r.get(name_field) or "").strip().upper()
                if v and v != "NAN":
                    m.setdefault(v, []).extend(keys)
        _SITE_KEYS_LOOKUP_CACHE = {k: list(dict.fromkeys(v)) for k, v in m.items()}
        return _SITE_KEYS_LOOKUP_CACHE
    except Exception as exc:
        logger.warning("Could not build site keys lookup for manager targets: %s", exc)
        return {}


def _resolve_store_metric(site_id: Any, site_name: Any, val_map: dict[str, Any], default: Any = 0.0) -> Any:
    """Resolve target / generic site metrics (allows Customer Master name aliases)."""
    if not val_map:
        return default
    s_raw = str(site_id or "").strip()
    s_upper = s_raw.upper()
    if s_raw in val_map:
        return val_map[s_raw]
    if s_upper in val_map:
        return val_map[s_upper]

    # 1. Check parenthesized code in site_id e.g. "PT-Nasik (P044)" -> "P044" or "( 101 )" -> "101"
    match = re.search(r"\(([^)]+)\)", s_raw)
    if match:
        code = match.group(1).strip()
        if code in val_map:
            return val_map[code]
        if code.upper() in val_map:
            return val_map[code.upper()]

    # 2. Check parenthesized code in site_name
    n_raw = str(site_name or "").strip()
    if n_raw:
        match_n = re.search(r"\(([^)]+)\)", n_raw)
        if match_n:
            code = match_n.group(1).strip()
            if code in val_map:
                return val_map[code]
            if code.upper() in val_map:
                return val_map[code.upper()]

    # 3. Check Customer Master alias map
    lookup = _get_site_keys_lookup()
    for name_cand in (s_upper, n_raw.upper()):
        if name_cand in lookup:
            for k in lookup[name_cand]:
                if k in val_map:
                    return val_map[k]
                if k.upper() in val_map:
                    return val_map[k.upper()]
    return default


def _soh_lookup_keys(site_id: Any, site_name: Any) -> list[str]:
    """Strict site codes for SOH/STR — never city/name aliases."""
    from api.current_soh import site_lookup_keys

    return site_lookup_keys(site_id, site_name)


def _resolve_site_soh(site_id: Any, site_name: Any, soh_map: dict[str, int], default: int = 0) -> int:
    """Map a store to daily_inventory_movement SOH via site codes only."""
    if not soh_map:
        return default
    for k in _soh_lookup_keys(site_id, site_name):
        if k in soh_map:
            return int(soh_map[k])
    return default


def _canonical_site_key(
    site_id: Any,
    site_name: Any,
    soh_map: dict[str, int] | None = None,
    str_map: dict[str, Any] | None = None,
) -> str:
    """Prefer the daily-movement site code (P139 / 259) so filter rollups never double-count."""
    keys = _soh_lookup_keys(site_id, site_name)
    if not keys:
        return str(site_id or "").strip().upper()
    for k in keys:
        if soh_map and k in soh_map:
            return k
        if str_map and k in str_map:
            return k
    # Prefer short paren codes over long display names
    for k in keys:
        if len(k) <= 8 and any(ch.isdigit() for ch in k):
            return k
    return keys[0]


def _build_response(
    store_df: pd.DataFrame,
    soh_map: dict[str, int],
    target_map: dict[str, float],
    sort_by: str,
    search: str,
    year: int = 2027,
    *,
    filter_scoped: bool = False,
    seasons: list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, Any]:
    if store_df.empty:
        return {
            "summary": {"total_target": 0, "total_gross_sales": 0, "total_net_sales": 0, "total_discount_amount": 0,
                        "overall_discount_pct": 0, "overall_achievement_pct": 0.0, "total_units": 0, "total_soh": 0,
                        "total_dispatch_qty": 0, "total_available_qty": 0, "total_sales_qty_daily": 0,
                        "overall_sell_through_pct": 0.0, "overall_full_price_str_pct": 0.0,
                        "total_managers": 0, "total_stores": 0, "top_performer": None},
            "managers": [],
            "filter_options": {"zones": [], "states": [], "cities": [], "channels": [], "managers": []},
        }

    store_grp = store_df.copy()
    store_grp["target"] = [
        round(float(_resolve_store_metric(r["site_id"], r["site_name"], target_map, 0.0)), 2)
        for _, r in store_grp.iterrows()
    ]
    store_grp["achievement_pct"] = (
        store_grp["net_sales"] / store_grp["target"].replace(0, float("nan")) * 100
    ).fillna(0.0).round(1)
    store_grp["discount_pct"] = (
        store_grp["discount_amount"] / store_grp["gross_sales"].replace(0, float("nan")) * 100
    ).fillna(0).round(1)
    # Dispatch / Sold / STR from daily — same season + date window as CFY sales
    from api.current_soh import load_daily_str_by_site_for_scope, resolve_daily_str

    str_map = load_daily_str_by_site_for_scope(
        seasons=seasons or [],
        date_from=date_from,
        date_to=date_to,
    )
    store_grp["soh"] = [
        int(_resolve_site_soh(r["site_id"], r["site_name"], soh_map, 0))
        for _, r in store_grp.iterrows()
    ]
    # Dedupe by canonical daily site code (never long display name)
    store_grp["_soh_key"] = [
        _canonical_site_key(r["site_id"], r["site_name"], soh_map, str_map)
        for _, r in store_grp.iterrows()
    ]
    store_str = [
        resolve_daily_str(r["site_id"], r["site_name"], str_map)
        for _, r in store_grp.iterrows()
    ]
    store_grp["sell_through_pct"] = [float(m.get("sell_through_pct", 0.0)) for m in store_str]
    store_grp["full_price_str_pct"] = [float(m.get("full_price_str_pct", 0.0)) for m in store_str]
    store_grp["_dm_dispatch"] = [float(m.get("dispatch_in", 0.0)) for m in store_str]
    store_grp["_dm_available"] = [float(m.get("available", 0.0)) for m in store_str]
    store_grp["_dm_sales_qty"] = [float(m.get("sales_qty", 0.0)) for m in store_str]
    store_grp["_dm_fp_sales_qty"] = [float(m.get("full_price_sales_qty", 0.0)) for m in store_str]
    # When season/date scoped: CFY qty = same daily period sold (align with Dispatch)
    metrics_period_scoped = bool(seasons or date_from or date_to)
    if metrics_period_scoped:
        store_grp["units"] = pd.to_numeric(store_grp["_dm_sales_qty"], errors="coerce").fillna(0).round(0).astype(int)
    store_grp = store_grp.sort_values("net_sales", ascending=False)
    # One row per physical site — prevents CFY summing aliases while Dispatch/STR dedupe
    store_grp = store_grp.drop_duplicates(subset=["manager", "_soh_key"], keep="first").copy()

    # Build manager→stores lookup
    stores_by_mgr: dict[str, list[dict]] = {}
    for _, r in store_grp.iterrows():
        mgr_key = str(r["manager"])
        stores_by_mgr.setdefault(mgr_key, []).append({
            "site_id": _norm_str(r["site_id"]),
            "site_name": _norm_str(r["site_name"]),
            "city": _norm_str(r["city"]),
            "zone": _norm_str(r["zone"]),
            "state": _norm_str(r["state"]),
            "channel": _norm_str(r["channel"]),
            "soh": int(r["soh"]),
            "dispatch_qty": int(round(float(r["_dm_dispatch"]))),
            "available_qty": int(round(float(r["_dm_available"]))),
            "sales_qty_daily": int(round(float(r["_dm_sales_qty"]))),
            "fp_sales_qty_daily": int(round(float(r["_dm_fp_sales_qty"]))),
            "target": round(float(r["target"]), 2),
            "gross_sales": round(float(r["gross_sales"]), 2),
            "discount_amount": round(float(r["discount_amount"]), 2),
            "discount_pct": float(r["discount_pct"]),
            "net_sales": round(float(r["net_sales"]), 2),
            "achievement_pct": float(r["achievement_pct"]),
            "units": int(r["units"]),
            "cfy_qty_sold": int(r["units"]),
            "sell_through_pct": float(r["sell_through_pct"]),
            "full_price_str_pct": float(r["full_price_str_pct"]),
        })

    # ── Manager-level aggregation ──────────────────────────────────────────
    # SOH: one value per physical site key within the manager (no double-count)
    soh_by_mgr = (
        store_grp.drop_duplicates(subset=["manager", "_soh_key"])
        .groupby("manager", as_index=False, observed=True)["soh"]
        .sum()
        .rename(columns={"soh": "soh"})
    )
    mgr_grp = (
        store_grp.groupby("manager", as_index=False, observed=True)
        .agg(
            gross_sales=("gross_sales", "sum"),
            discount_amount=("discount_amount", "sum"),
            net_sales=("net_sales", "sum"),
            units=("units", "sum"),
            fp_units=("fp_units", "sum"),
            fp_sales=("fp_sales", "sum"),
            target=("target", "sum"),
            store_count=("site_id", "nunique"),
        )
    )
    mgr_grp = mgr_grp.merge(soh_by_mgr, on="manager", how="left")
    mgr_grp["soh"] = pd.to_numeric(mgr_grp["soh"], errors="coerce").fillna(0).astype(int)
    mgr_grp["discount_pct"] = (
        mgr_grp["discount_amount"] / mgr_grp["gross_sales"].replace(0, float("nan")) * 100
    ).fillna(0).round(1)
    mgr_grp["achievement_pct"] = (
        mgr_grp["net_sales"] / mgr_grp["target"].replace(0, float("nan")) * 100
    ).fillna(0.0).round(1)
    # Manager STR = sum(daily sales_qty) / sum(daily available) for unique stores under RM
    dm_by_mgr = (
        store_grp.drop_duplicates(subset=["manager", "_soh_key"])
        .groupby("manager", as_index=False, observed=True)
        .agg(
            _dm_dispatch=("_dm_dispatch", "sum"),
            _dm_available=("_dm_available", "sum"),
            _dm_sales_qty=("_dm_sales_qty", "sum"),
            _dm_fp_sales_qty=("_dm_fp_sales_qty", "sum"),
        )
    )
    mgr_grp = mgr_grp.merge(dm_by_mgr, on="manager", how="left")
    _dispatch = pd.to_numeric(mgr_grp["_dm_dispatch"], errors="coerce").fillna(0.0)
    _avail = pd.to_numeric(mgr_grp["_dm_available"], errors="coerce").fillna(0.0)
    _sales = pd.to_numeric(mgr_grp["_dm_sales_qty"], errors="coerce").fillna(0.0)
    _fp = pd.to_numeric(mgr_grp["_dm_fp_sales_qty"], errors="coerce").fillna(0.0)
    mgr_grp["dispatch_qty"] = _dispatch.round(0).astype(int)
    mgr_grp["available_qty"] = _avail.round(0).astype(int)
    mgr_grp["sales_qty_daily"] = _sales.round(0).astype(int)
    mgr_grp["fp_sales_qty_daily"] = _fp.round(0).astype(int)
    mgr_grp["sell_through_pct"] = (_sales / _avail.replace(0, float("nan")) * 100).fillna(0.0).round(1)
    mgr_grp["full_price_str_pct"] = (_fp / _avail.replace(0, float("nan")) * 100).fillna(0.0).round(1)

    # Zones and cities per manager (comma-separated list)
    def _uniq_list(series: pd.Series) -> str:
        return ", ".join(sorted({_norm_str(v) for v in series if _norm_str(v)}))

    mgr_zone = store_grp.groupby("manager", observed=True)["zone"].apply(_uniq_list).rename("zones").reset_index()
    mgr_city = store_grp.groupby("manager", observed=True)["city"].apply(_uniq_list).rename("cities").reset_index()
    mgr_grp = mgr_grp.merge(mgr_zone, on="manager").merge(mgr_city, on="manager")

    # Search: manager name OR site id / site name
    search_str = search.strip() if isinstance(search, str) else ""
    if search_str:
        sq = search_str.upper()
        mgr_name_hit = mgr_grp["manager"].astype(str).str.upper().str.contains(sq, na=False)
        site_hit = (
            store_grp["site_id"].astype(str).str.upper().str.contains(sq, na=False)
            | store_grp["site_name"].astype(str).str.upper().str.contains(sq, na=False)
        )
        mgrs_with_site = set(store_grp.loc[site_hit, "manager"].astype(str))
        mgr_grp = mgr_grp[mgr_name_hit | mgr_grp["manager"].astype(str).isin(mgrs_with_site)].copy()
        # If query matched stores (not only manager names), keep only those store rows
        if site_hit.any() and not bool(mgr_name_hit.any()):
            store_grp = store_grp[site_hit].copy()
            # Rebuild manager aggregates for the narrowed store set
            soh_by_mgr = (
                store_grp.drop_duplicates(subset=["manager", "_soh_key"])
                .groupby("manager", as_index=False, observed=True)["soh"]
                .sum()
            )
            dm_by_mgr = (
                store_grp.drop_duplicates(subset=["manager", "_soh_key"])
                .groupby("manager", as_index=False, observed=True)
                .agg(
                    _dm_dispatch=("_dm_dispatch", "sum"),
                    _dm_available=("_dm_available", "sum"),
                    _dm_sales_qty=("_dm_sales_qty", "sum"),
                    _dm_fp_sales_qty=("_dm_fp_sales_qty", "sum"),
                )
            )
            mgr_grp = (
                store_grp.groupby("manager", as_index=False, observed=True)
                .agg(
                    gross_sales=("gross_sales", "sum"),
                    discount_amount=("discount_amount", "sum"),
                    net_sales=("net_sales", "sum"),
                    units=("units", "sum"),
                    fp_units=("fp_units", "sum"),
                    fp_sales=("fp_sales", "sum"),
                    target=("target", "sum"),
                    store_count=("site_id", "nunique"),
                )
                .merge(soh_by_mgr, on="manager", how="left")
                .merge(dm_by_mgr, on="manager", how="left")
            )
            mgr_grp["soh"] = pd.to_numeric(mgr_grp["soh"], errors="coerce").fillna(0).astype(int)
            _dispatch = pd.to_numeric(mgr_grp["_dm_dispatch"], errors="coerce").fillna(0.0)
            _avail = pd.to_numeric(mgr_grp["_dm_available"], errors="coerce").fillna(0.0)
            _sales = pd.to_numeric(mgr_grp["_dm_sales_qty"], errors="coerce").fillna(0.0)
            _fp = pd.to_numeric(mgr_grp["_dm_fp_sales_qty"], errors="coerce").fillna(0.0)
            mgr_grp["dispatch_qty"] = _dispatch.round(0).astype(int)
            mgr_grp["available_qty"] = _avail.round(0).astype(int)
            mgr_grp["sales_qty_daily"] = _sales.round(0).astype(int)
            mgr_grp["fp_sales_qty_daily"] = _fp.round(0).astype(int)
            mgr_grp["discount_pct"] = (
                mgr_grp["discount_amount"] / mgr_grp["gross_sales"].replace(0, float("nan")) * 100
            ).fillna(0).round(1)
            mgr_grp["achievement_pct"] = (
                mgr_grp["net_sales"] / mgr_grp["target"].replace(0, float("nan")) * 100
            ).fillna(0.0).round(1)
            mgr_grp["sell_through_pct"] = (_sales / _avail.replace(0, float("nan")) * 100).fillna(0.0).round(1)
            mgr_grp["full_price_str_pct"] = (_fp / _avail.replace(0, float("nan")) * 100).fillna(0.0).round(1)
            mgr_zone = store_grp.groupby("manager", observed=True)["zone"].apply(_uniq_list).rename("zones").reset_index()
            mgr_city = store_grp.groupby("manager", observed=True)["city"].apply(_uniq_list).rename("cities").reset_index()
            mgr_grp = mgr_grp.merge(mgr_zone, on="manager").merge(mgr_city, on="manager")
            stores_by_mgr = {}
            for _, r in store_grp.iterrows():
                mgr_key = str(r["manager"])
                stores_by_mgr.setdefault(mgr_key, []).append({
                    "site_id": _norm_str(r["site_id"]),
                    "site_name": _norm_str(r["site_name"]),
                    "city": _norm_str(r["city"]),
                    "zone": _norm_str(r["zone"]),
                    "state": _norm_str(r["state"]),
                    "channel": _norm_str(r["channel"]),
                    "soh": int(r["soh"]),
                    "dispatch_qty": int(round(float(r["_dm_dispatch"]))),
                    "available_qty": int(round(float(r["_dm_available"]))),
                    "sales_qty_daily": int(round(float(r["_dm_sales_qty"]))),
                    "fp_sales_qty_daily": int(round(float(r["_dm_fp_sales_qty"]))),
                    "target": round(float(r["target"]), 2),
                    "gross_sales": round(float(r["gross_sales"]), 2),
                    "discount_amount": round(float(r["discount_amount"]), 2),
                    "discount_pct": float(r["discount_pct"]),
                    "net_sales": round(float(r["net_sales"]), 2),
                    "achievement_pct": float(r["achievement_pct"]),
                    "units": int(r["units"]),
                    "cfy_qty_sold": int(r["units"]),
                    "sell_through_pct": float(r["sell_through_pct"]),
                    "full_price_str_pct": float(r["full_price_str_pct"]),
                })

    # Sort
    sort_str = sort_by if isinstance(sort_by, str) else "net_sales"
    valid_sorts = {
        "net_sales", "target", "achievement_pct", "gross_sales", "discount_pct",
        "store_count", "units", "cfy_qty_sold", "sell_through_pct", "full_price_str_pct",
        "dispatch_qty", "sales_qty_daily",
    }
    if sort_str == "cfy_qty_sold":
        sort_str = "units"
    sort_col = sort_str if sort_str in valid_sorts else "net_sales"
    mgr_grp = mgr_grp.sort_values(sort_col, ascending=False).reset_index(drop=True)
    mgr_grp["rank"] = mgr_grp.index + 1

    # Restrict store rows / summary to managers still visible after search
    kept_mgrs = set(mgr_grp["manager"].astype(str))
    store_scoped = store_grp[store_grp["manager"].astype(str).isin(kept_mgrs)].copy()

    # ── Summary KPIs (always filter-scoped — Zone/Channel/City/Manager/Search) ──
    total_target = float(mgr_grp["target"].sum())
    total_gross = float(mgr_grp["gross_sales"].sum())
    total_net = float(mgr_grp["net_sales"].sum())
    overall_ach_pct = round(total_net / total_target * 100, 1) if total_target > 0 else 0.0
    total_disc = float(mgr_grp["discount_amount"].sum())
    overall_disc_pct = round(total_disc / total_gross * 100, 1) if total_gross > 0 else 0.0
    total_units = int(mgr_grp["units"].sum())  # CFY qty sold
    uniq = store_scoped.drop_duplicates(subset=["_soh_key"])
    if filter_scoped:
        total_soh = int(pd.to_numeric(uniq["soh"], errors="coerce").fillna(0).sum())
        total_dispatch_qty = int(round(float(pd.to_numeric(uniq["_dm_dispatch"], errors="coerce").fillna(0).sum())))
        total_available_qty = int(round(float(pd.to_numeric(uniq["_dm_available"], errors="coerce").fillna(0).sum())))
        total_sales_qty_daily = int(round(float(pd.to_numeric(uniq["_dm_sales_qty"], errors="coerce").fillna(0).sum())))
        total_fp_sales_daily = float(pd.to_numeric(uniq["_dm_fp_sales_qty"], errors="coerce").fillna(0).sum())
        overall_str_pct = (
            round(total_sales_qty_daily / total_available_qty * 100, 1) if total_available_qty > 0 else 0.0
        )
        overall_fp_str_pct = (
            round(total_fp_sales_daily / total_available_qty * 100, 1) if total_available_qty > 0 else 0.0
        )
    else:
        # No geo filters → network (or season/date-scoped network) Current SOH / STR / Dispatch
        from api.current_soh import (
            network_daily_str,
            network_daily_str_for_scope,
            total_current_soh,
        )

        if seasons or date_from or date_to:
            total_soh = int(pd.to_numeric(uniq["soh"], errors="coerce").fillna(0).sum())
            net_str = network_daily_str_for_scope(
                seasons=seasons, date_from=date_from, date_to=date_to
            )
        else:
            try:
                total_soh = int(round(total_current_soh()))
            except Exception:
                total_soh = int(pd.to_numeric(uniq["soh"], errors="coerce").fillna(0).sum())
            net_str = network_daily_str()
        total_dispatch_qty = int(round(float(net_str.get("dispatch_in", 0.0))))
        total_available_qty = int(round(float(net_str.get("available", 0.0))))
        total_sales_qty_daily = int(round(float(net_str.get("sales_qty", 0.0))))
        overall_str_pct = float(net_str.get("sell_through_pct", 0.0))
        overall_fp_str_pct = float(net_str.get("full_price_str_pct", 0.0))
        if metrics_period_scoped:
            total_units = total_sales_qty_daily
    total_managers = len(mgr_grp)
    total_stores = int(mgr_grp["store_count"].sum())

    top = mgr_grp.iloc[0] if not mgr_grp.empty else None
    top_performer = None
    if top is not None:
        top_performer = {
            "manager": str(top["manager"]),
            "target": round(float(top["target"]), 2),
            "net_sales": round(float(top["net_sales"]), 2),
            "achievement_pct": float(top["achievement_pct"]),
            "gross_sales": round(float(top["gross_sales"]), 2),
            "discount_pct": float(top["discount_pct"]),
            "store_count": int(top["store_count"]),
            "zones": str(top["zones"]),
            "cities": str(top["cities"]),
            "sell_through_pct": float(top["sell_through_pct"]),
            "full_price_str_pct": float(top["full_price_str_pct"]),
        }

    # Build manager rows
    managers_list = []
    for _, r in mgr_grp.iterrows():
        mgr_name = str(r["manager"])
        managers_list.append({
            "rank": int(r["rank"]),
            "manager": mgr_name,
            "store_count": int(r["store_count"]),
            "zones": str(r["zones"]),
            "cities": str(r["cities"]),
            "target": round(float(r["target"]), 2),
            "gross_sales": round(float(r["gross_sales"]), 2),
            "discount_amount": round(float(r["discount_amount"]), 2),
            "discount_pct": float(r["discount_pct"]),
            "net_sales": round(float(r["net_sales"]), 2),
            "achievement_pct": float(r["achievement_pct"]),
            "units": int(r["units"]),
            "cfy_qty_sold": int(r["units"]),
            "soh": int(r["soh"]),
            "dispatch_qty": int(r["dispatch_qty"]),
            "available_qty": int(r["available_qty"]),
            "sales_qty_daily": int(r["sales_qty_daily"]),
            "fp_sales_qty_daily": int(r["fp_sales_qty_daily"]),
            "sell_through_pct": float(r["sell_through_pct"]),
            "full_price_str_pct": float(r["full_price_str_pct"]),
            "sales_share_pct": round(float(r["net_sales"]) / total_net * 100, 1) if total_net > 0 else 0.0,
            "stores": stores_by_mgr.get(mgr_name, []),
        })

    # Zone rollups for Zone Analysis tab — computed here so UI does not re-aggregate.
    zone_buckets: dict[str, dict[str, float]] = {}
    for m in managers_list:
        zones_raw = str(m.get("zones") or "").strip()
        zone_names = [z.strip() for z in zones_raw.split(",") if z.strip()] or ["Unzoned"]
        for z in zone_names:
            bucket = zone_buckets.setdefault(
                z,
                {
                    "zone": z,
                    "manager_count": 0,
                    "store_count": 0,
                    "target": 0.0,
                    "gross_sales": 0.0,
                    "net_sales": 0.0,
                    "discount_amount": 0.0,
                    "soh": 0.0,
                    "dispatch_qty": 0.0,
                },
            )
            bucket["manager_count"] += 1
            bucket["store_count"] += int(m.get("store_count") or 0)
            bucket["target"] += float(m.get("target") or 0)
            bucket["gross_sales"] += float(m.get("gross_sales") or 0)
            bucket["net_sales"] += float(m.get("net_sales") or 0)
            bucket["discount_amount"] += float(m.get("discount_amount") or 0)
            bucket["soh"] += float(m.get("soh") or 0)
            bucket["dispatch_qty"] += float(m.get("dispatch_qty") or 0)
    zone_summary = []
    for z, b in sorted(zone_buckets.items(), key=lambda kv: kv[1]["net_sales"], reverse=True):
        tgt = float(b["target"])
        gross = float(b["gross_sales"])
        net = float(b["net_sales"])
        zone_summary.append(
            {
                "zone": z,
                "manager_count": int(b["manager_count"]),
                "store_count": int(b["store_count"]),
                "target": round(tgt, 2),
                "gross_sales": round(gross, 2),
                "net_sales": round(net, 2),
                "discount_amount": round(float(b["discount_amount"]), 2),
                "achievement_pct": round(net / tgt * 100, 1) if tgt > 0 else 0.0,
                "discount_pct": round(float(b["discount_amount"]) / gross * 100, 1) if gross > 0 else 0.0,
                "soh": int(round(float(b["soh"]))),
                "dispatch_qty": int(round(float(b["dispatch_qty"]))),
            }
        )

    return {
        "summary": {
            "total_target": round(total_target, 2),
            "total_gross_sales": round(total_gross, 2),
            "total_net_sales": round(total_net, 2),
            "overall_achievement_pct": overall_ach_pct,
            "total_discount_amount": round(total_disc, 2),
            "overall_discount_pct": overall_disc_pct,
            "total_units": total_units,
            "cfy_qty_sold": total_units,
            "total_soh": total_soh,
            "total_dispatch_qty": total_dispatch_qty,
            "total_available_qty": total_available_qty,
            "total_sales_qty_daily": total_sales_qty_daily,
            "overall_sell_through_pct": overall_str_pct,
            "overall_full_price_str_pct": overall_fp_str_pct,
            "total_managers": total_managers,
            "total_stores": total_stores,
            "top_performer": top_performer,
        },
        "managers": managers_list,
        "zone_summary": zone_summary,
        "filter_options": {"zones": [], "states": [], "cities": [], "channels": [], "managers": []},
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/overview")
def get_managers_overview(
    year: int = Query(None, description="Financial year ending year (e.g. 2027 for FY27). Default = current FY."),
    financial_year: str = Query("", description="FY code e.g. FY27, FY26, FY25"),
    zones: str = Query("", description="Comma-separated zone names"),
    states: str = Query("", description="Comma-separated state names"),
    cities: str = Query("", description="Comma-separated city names"),
    channels: str = Query("", description="Comma-separated channel codes (PT,SSL,CENTRO,ICONIC,EBO)"),
    managers: str = Query("", description="Comma-separated manager/cluster names"),
    seasons: str = Query("", description="Comma-separated seasons (SS-26, AW-25). Metrics scoped; manager list stays full."),
    date_from: str = Query("", description="Sales/target start date YYYY-MM-DD"),
    date_to: str = Query("", description="Sales/target end date YYYY-MM-DD"),
    search: str = Query("", description="Search within manager or store names"),
    sort_by: str = Query("net_sales", description="Sort managers by: net_sales, target, achievement_pct, gross_sales, discount_pct, store_count, units"),
) -> dict[str, Any]:
    from api.sales_year_loader import normalize_dashboard_year
    default_fy = _default_fy_year()
    fy_str = financial_year if isinstance(financial_year, str) else ""
    y_val = year if isinstance(year, (int, str)) and not hasattr(year, "default") else default_fy
    if y_val is None or (isinstance(y_val, str) and not str(y_val).strip()):
        y_val = default_fy
    search_str = search if isinstance(search, str) else ""
    sort_str = sort_by if isinstance(sort_by, str) else "net_sales"

    if fy_str.strip().lower() in ("all", "0", "all years") or str(y_val).strip() in ("0", "all"):
        effective_year = 0
    else:
        effective_year = normalize_dashboard_year(fy_str or y_val, default=default_fy)

    def _parse(s: Any) -> list[str]:
        if not isinstance(s, str):
            return []
        return [x.strip() for x in s.split(",") if x.strip()] if s.strip() else []

    season_list = _parse(seasons)
    d_from = str(date_from).strip() if isinstance(date_from, str) else ""
    d_to = str(date_to).strip() if isinstance(date_to, str) else ""
    # Season without dates → align daily Dispatch/Sold with CFY using FY (or YTD) bounds
    if season_list and not d_from and not d_to and effective_year > 0:
        bound_from, bound_to = _sales_period_bounds(effective_year)
        d_from = bound_from or f"{effective_year - 1}-04-01"
        d_to = bound_to or f"{effective_year}-03-31"

    cache_key = (
        f"v15-zone-summary|{effective_year}|{zones}|{states}|{cities}|{channels}|{managers}|"
        f"{','.join(season_list)}|{d_from}|{d_to}|{search_str}|{sort_str}"
    )
    now = time.time()
    from api.page_response_cache import load_page_cache, rebuilding_page_cache, save_page_cache

    def _cache_usable(payload: dict[str, Any] | None) -> bool:
        """Reject stale caches that zeroed out daily SOH / STR when sales exist."""
        if not isinstance(payload, dict) or not payload.get("managers"):
            return False
        summary = payload.get("summary") or {}
        if "total_net_sales" not in summary:
            return False
        # Zone Analysis tab needs backend zone_summary — reject pre-v15 cache blobs.
        if not isinstance(payload.get("zone_summary"), list):
            logger.warning("Ignoring store-managers page_cache missing zone_summary")
            return False
        # Unscoped FY warm must never serve sales-OK + SOH/dispatch all zero
        # (hosted blob was stuck on a cold-start warm before daily movement was ready).
        season_scoped = bool(season_list)
        try:
            net = float(summary.get("total_net_sales") or 0)
            soh = float(summary.get("total_soh") or 0)
            disp = float(summary.get("total_dispatch_qty") or 0)
        except (TypeError, ValueError):
            return False
        if not season_scoped and net > 0 and soh <= 0 and disp <= 0:
            logger.warning(
                "Ignoring stale store-managers page_cache (net=%.0f soh=0 dispatch=0)",
                net,
            )
            return False
        return True

    if not rebuilding_page_cache():
        with _CACHE_LOCK:
            cached = _OVERVIEW_RESULT_CACHE.get(cache_key)
            if cached and (now - cached[0] < _OVERVIEW_CACHE_TTL) and _cache_usable(cached[1]):
                return cached[1]
        disk_overview = load_page_cache("store-managers", cache_key)
        if _cache_usable(disk_overview):
            with _CACHE_LOCK:
                _OVERVIEW_RESULT_CACHE[cache_key] = (now, disk_overview)
            return disk_overview

    try:
        # Roster + metrics (season/date scope metrics only — list stays complete)
        df = _get_store_summary_df(
            effective_year, seasons=season_list, date_from=d_from or None, date_to=d_to or None
        )
        if df.empty:
            return {
                "summary": {"total_target": 0, "total_gross_sales": 0, "total_net_sales": 0, "total_discount_amount": 0,
                            "overall_discount_pct": 0, "overall_achievement_pct": 0.0, "total_units": 0, "total_managers": 0, "total_stores": 0,
                            "top_performer": None},
                "managers": [],
                "filter_options": {"zones": [], "states": [], "cities": [], "channels": [], "managers": [], "seasons": []},
                "period": {
                    "year": effective_year,
                    "financial_year": f"FY{str(effective_year)[-2:]}" if effective_year > 0 else "All Years",
                },
                "error": "Sales data not available. Please run the pipeline first.",
            }

        # Full roster (no season/date) for filter option lists
        df_full = _get_store_summary_df(effective_year)

        z_list, st_list, c_list, ch_list, m_list = (
            _parse(zones), _parse(states), _parse(cities), _parse(channels), _parse(managers)
        )
        df_filtered = _apply_filters(df, z_list, st_list, c_list, ch_list, m_list)
        # Geo/manager/search shrink the list; season/date only change metric values
        filter_scoped = bool(
            z_list or st_list or c_list or ch_list or m_list or search_str.strip() or season_list or d_from or d_to
        )

        target_map = _load_target_by_site(effective_year, date_from=d_from or None, date_to=d_to or None)
        soh_map = _load_soh_by_site(season_list)

        result = _build_response(
            df_filtered,
            soh_map,
            target_map,
            sort_by,
            search,
            effective_year,
            filter_scoped=filter_scoped,
            seasons=season_list,
            date_from=d_from or None,
            date_to=d_to or None,
        )

        _JUNK = {"", "0", "0.0", "nan", "none", "<na>", "null", "unassigned", "-", "--"}

        def _uniq_sorted(series: pd.Series) -> list[str]:
            return sorted(
                {str(v).strip() for v in _clean_series(series) if str(v).strip().lower() not in _JUNK},
                key=str.upper,
            )

        result["filter_options"] = {
            "zones": _uniq_sorted(df_full["zone"]),
            "states": _uniq_sorted(df_full["state"]),
            "cities": _uniq_sorted(df_full["city"]),
            "channels": _uniq_sorted(df_full["channel"]),
            "managers": _uniq_sorted(df_full["manager"]),
            "seasons": _SEASONS_BY_YEAR.get(effective_year) or [],
        }
        from_d, to_d = _sales_period_bounds(effective_year)
        result["period"] = {
            "year": effective_year,
            "financial_year": f"FY{str(effective_year)[-2:]}" if effective_year > 0 else "All Years",
            "from_date": d_from or from_d,
            "to_date": d_to or to_d,
            "date_from": d_from or None,
            "date_to": d_to or None,
            "seasons": season_list,
            "metrics_scoped": bool(season_list or d_from or d_to),
            "is_default_fy": effective_year == default_fy,
        }
        if season_list:
            result["summary"]["product_target_note"] = (
                "Targets are store-level (Target LFS). Season filters sales/SOH/STR; achievement uses date-range store target."
            )
        with _CACHE_LOCK:
            _OVERVIEW_RESULT_CACHE[cache_key] = (now, result)
        if not result.get("error"):
            save_page_cache("store-managers", cache_key, result)
        return result

    except Exception as exc:
        logger.exception("Error in get_managers_overview: %s", exc)
        return {
            "summary": {"total_target": 0, "total_gross_sales": 0, "total_net_sales": 0, "total_discount_amount": 0,
                        "overall_discount_pct": 0, "overall_achievement_pct": 0.0, "total_units": 0, "total_managers": 0, "total_stores": 0,
                        "top_performer": None},
            "managers": [],
            "filter_options": {"zones": [], "states": [], "cities": [], "channels": [], "managers": []},
            "error": str(exc),
        }


def warm_managers_overview_cache(year: int | None = None) -> dict[str, Any]:
    """
    Pre-build default /managers overview into page_cache (disk + memory).
    Called after the 09:00 daily pipeline once daily_inventory_movement is ready.
    """
    from api.page_response_cache import _REBUILD

    y = int(year) if year and int(year) > 0 else _default_fy_year()
    with _CACHE_LOCK:
        _OVERVIEW_RESULT_CACHE.clear()
        _STORE_SUMMARY_CACHE.clear()
        _SOH_MAP_CACHE.clear()
        _TARGET_MAP_CACHE.clear()
        _SALES_CACHE_DF.clear()

    token = _REBUILD.set(True)
    t0 = time.perf_counter()
    try:
        try:
            from api.current_soh import ensure_daily_movement_from_blob

            ensure_daily_movement_from_blob()
        except Exception as dm_exc:
            logger.warning("Managers warm: daily movement ensure skipped: %s", dm_exc)
        payload = get_managers_overview(
            year=y,
            financial_year="",
            zones="",
            states="",
            cities="",
            channels="",
            managers="",
            seasons="",
            date_from="",
            date_to="",
            search="",
            sort_by="net_sales",
        )
        summary = (payload or {}).get("summary") or {}
        try:
            soh_n = float(summary.get("total_soh") or 0)
            disp_n = float(summary.get("total_dispatch_qty") or 0)
            net_n = float(summary.get("total_net_sales") or 0)
        except (TypeError, ValueError):
            soh_n = disp_n = net_n = 0.0
        soh_ok = not (net_n > 0 and soh_n <= 0 and disp_n <= 0)
        ok = bool(
            isinstance(payload, dict)
            and not payload.get("error")
            and len(payload.get("managers") or []) > 0
            and soh_ok
        )
        elapsed = round(time.perf_counter() - t0, 1)
        logger.info(
            "Managers overview cache warm year=%s ok=%s managers=%s soh=%.0f dispatch=%.0f in %.1fs",
            y,
            ok,
            len((payload or {}).get("managers") or []),
            soh_n,
            disp_n,
            elapsed,
        )
        return {
            "ok": ok,
            "year": y,
            "seconds": elapsed,
            "managers": len((payload or {}).get("managers") or []),
            "total_soh": soh_n,
            "total_dispatch_qty": disp_n,
        }
    except Exception as exc:
        logger.exception("Managers overview cache warm failed: %s", exc)
        return {"ok": False, "year": y, "error": str(exc)}
    finally:
        _REBUILD.reset(token)


@router.get("/export")
def export_managers_excel(
    year: int = Query(None, description="Financial year ending year. Default = current FY."),
    financial_year: str = Query("", description="FY code"),
    zones: str = Query(""),
    states: str = Query(""),
    cities: str = Query(""),
    channels: str = Query(""),
    managers: str = Query(""),
    seasons: str = Query(""),
    date_from: str = Query(""),
    date_to: str = Query(""),
    sort_by: str = Query("net_sales"),
) -> StreamingResponse:
    from api.sales_year_loader import normalize_dashboard_year
    default_fy = _default_fy_year()
    fy_str = financial_year if isinstance(financial_year, str) else ""
    y_val = year if isinstance(year, (int, str)) and not hasattr(year, "default") else default_fy
    if y_val is None or (isinstance(y_val, str) and not str(y_val).strip()):
        y_val = default_fy
    sort_str = sort_by if isinstance(sort_by, str) else "net_sales"

    if fy_str.strip().lower() in ("all", "0", "all years") or str(y_val).strip() in ("0", "all"):
        effective_year = 0
    else:
        effective_year = normalize_dashboard_year(fy_str or y_val, default=default_fy)

    def _parse(s: Any) -> list[str]:
        if not isinstance(s, str):
            return []
        return [x.strip() for x in s.split(",") if x.strip()] if s.strip() else []

    season_list = _parse(seasons)
    d_from = str(date_from).strip() if isinstance(date_from, str) else ""
    d_to = str(date_to).strip() if isinstance(date_to, str) else ""
    if season_list and not d_from and not d_to and effective_year > 0:
        bound_from, bound_to = _sales_period_bounds(effective_year)
        d_from = bound_from or f"{effective_year - 1}-04-01"
        d_to = bound_to or f"{effective_year}-03-31"
    df = _get_store_summary_df(
        effective_year, seasons=season_list, date_from=d_from or None, date_to=d_to or None
    )

    z_list, st_list, c_list, ch_list, m_list = (
        _parse(zones), _parse(states), _parse(cities), _parse(channels), _parse(managers)
    )
    df = _apply_filters(df, z_list, st_list, c_list, ch_list, m_list)
    filter_scoped = bool(z_list or st_list or c_list or ch_list or m_list or season_list or d_from or d_to)
    target_map = _load_target_by_site(effective_year, date_from=d_from or None, date_to=d_to or None)
    soh_map = _load_soh_by_site(season_list)
    data = _build_response(
        df,
        soh_map,
        target_map,
        sort_by,
        "",
        effective_year,
        filter_scoped=filter_scoped,
        seasons=season_list,
        date_from=d_from or None,
        date_to=d_to or None,
    )

    # ── Build Excel ──────────────────────────────────────────────────────
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        workbook = writer.book

        header_fmt = workbook.add_format({
            "bold": True, "bg_color": "#1e293b", "font_color": "#ffffff",
            "border": 1, "align": "center", "valign": "vcenter", "font_size": 10,
        })
        cell_fmt = workbook.add_format({"font_size": 9, "valign": "vcenter"})
        money_fmt = workbook.add_format({"font_size": 9, "num_format": "₹#,##0.00", "valign": "vcenter"})
        pct_fmt = workbook.add_format({"font_size": 9, "num_format": "0.0%", "valign": "vcenter"})
        int_fmt = workbook.add_format({"font_size": 9, "num_format": "#,##0", "valign": "vcenter"})

        # Sheet 1: Manager Summary
        mgr_rows = []
        for m in data.get("managers", []):
            mgr_rows.append({
                "Rank": m["rank"],
                "Manager / RM": m["manager"],
                "Stores": m["store_count"],
                "Zones": m["zones"],
                "Cities": m["cities"],
                "Target (₹)": m["target"],
                "Gross Sales (₹)": m["gross_sales"],
                "Discount Amt (₹)": m["discount_amount"],
                "Discount %": m["discount_pct"] / 100.0,
                "Net Sales (₹)": m["net_sales"],
                "Achievement %": m["achievement_pct"] / 100.0,
                "Sales Share %": m["sales_share_pct"] / 100.0,
                "CFY Qty Sold": m.get("cfy_qty_sold", m["units"]),
                "Current SOH": m["soh"],
                "Total Dispatch": m.get("dispatch_qty", 0),
                "Available Qty": m.get("available_qty", 0),
                "Total Qty Sold": m.get("sales_qty_daily", 0),
                "Sell-Through %": m["sell_through_pct"] / 100.0,
                "Full Price STR %": m["full_price_str_pct"] / 100.0,
            })
        df_mgr = pd.DataFrame(mgr_rows)
        df_mgr.to_excel(writer, sheet_name="Manager Summary", index=False)
        ws_mgr = writer.sheets["Manager Summary"]
        ws_mgr.set_row(0, 22, header_fmt)
        for i, col in enumerate(df_mgr.columns):
            max_len = max(df_mgr[col].astype(str).map(len).max() if not df_mgr.empty else 0, len(col)) + 4
            ws_mgr.set_column(i, i, min(max(max_len, 10), 35), cell_fmt)

        # Sheet 2: Store Details
        store_rows = []
        for m in data.get("managers", []):
            for s in m.get("stores", []):
                store_rows.append({
                    "Manager / RM": m["manager"],
                    "Site ID": s["site_id"],
                    "Site Name": s["site_name"],
                    "Channel": s["channel"],
                    "City": s["city"],
                    "Zone": s["zone"],
                    "State": s["state"],
                    "Target (₹)": s["target"],
                    "Gross Sales (₹)": s["gross_sales"],
                    "Discount Amt (₹)": s["discount_amount"],
                    "Discount %": s["discount_pct"] / 100.0,
                    "Net Sales (₹)": s["net_sales"],
                    "Achievement %": s["achievement_pct"] / 100.0,
                    "CFY Qty Sold": s.get("cfy_qty_sold", s["units"]),
                    "Current SOH": s["soh"],
                    "Total Dispatch": s.get("dispatch_qty", 0),
                    "Available Qty": s.get("available_qty", 0),
                    "Total Qty Sold": s.get("sales_qty_daily", 0),
                    "Sell-Through %": s["sell_through_pct"] / 100.0,
                    "Full Price STR %": s["full_price_str_pct"] / 100.0,
                })
        df_store = pd.DataFrame(store_rows)
        df_store.to_excel(writer, sheet_name="Store Details", index=False)
        ws_store = writer.sheets["Store Details"]
        ws_store.set_row(0, 22, header_fmt)
        for i, col in enumerate(df_store.columns):
            max_len = max(df_store[col].astype(str).map(len).max() if not df_store.empty else 0, len(col)) + 4
            ws_store.set_column(i, i, min(max(max_len, 10), 35), cell_fmt)

    output.seek(0)
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="Manager_Performance_Report.xlsx"'},
    )

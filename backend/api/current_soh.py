"""
Canonical Current SOH for KPI cards / managers.

Default source: SOH-Master (physical snapshot) — matches Ginesys closing stock.
Sell-through / movement ledger still uses daily_inventory_movement separately.

Set CURRENT_SOH_SOURCE=daily to force the Apr'25+ movement ledger instead.
"""
from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

import pandas as pd

logger = logging.getLogger(__name__)

_API_CACHE_DIR = Path(__file__).resolve().parents[1] / "data" / "api_cache"
_BACKEND_DIR = Path(__file__).resolve().parents[1]
_DAILY_NAME = "daily_inventory_movement.parquet"
# Published from a machine that already built the ledger (local / 9 AM) so Azure
# App Service can pull it without re-running the full movement pipeline.
_DM_BLOB_NAME = "cache/api_cache/daily_inventory_movement.parquet"
_DM_META_BLOB_NAME = "cache/api_cache/daily_inventory_movement.meta.json"

# soh_master (default) | daily
_CURRENT_SOH_SOURCE = (
    os.environ.get("CURRENT_SOH_SOURCE", "soh_master") or "soh_master"
).strip().lower()

_PAIRS_CACHE: Optional[pd.DataFrame] = None
_PAIRS_TIME: float = 0.0
_PAIRS_SOURCE: str = ""
_SITE_CACHE: Optional[dict[str, float]] = None
_SITE_TIME: float = 0.0
_CACHE_TTL = 300.0
_DM_BLOB_OK = False
_DM_BLOB_FAIL_UNTIL = 0.0
_DM_BLOB_RETRY_S = float(os.environ.get("DM_BLOB_RETRY_S", "120") or "120")


def current_soh_source() -> str:
    if _CURRENT_SOH_SOURCE in ("daily", "daily_movement", "dm", "movement"):
        return "daily"
    return "soh_master"


def daily_movement_parquet_path() -> Path:
    return _API_CACHE_DIR / _DAILY_NAME


def ensure_daily_movement_from_blob(force: bool = False) -> Path:
    """If local DM parquet is missing, download the published blob copy.

    Success is sticky for the process. Failures cool down briefly then retry —
    never permanently skip (hosted cold-start used to mark checked before pull).
    """
    global _DM_BLOB_OK, _DM_BLOB_FAIL_UNTIL
    path = daily_movement_parquet_path()
    if path.exists() and path.stat().st_size > 0 and not force:
        _DM_BLOB_OK = True
        return path
    if _DM_BLOB_OK and not force:
        return path
    now = time.time()
    if not force and now < _DM_BLOB_FAIL_UNTIL:
        return path
    try:
        from api.routes.sales_dashboard import _blob_store

        store = _blob_store()
        if store is None:
            _DM_BLOB_FAIL_UNTIL = now + max(30.0, _DM_BLOB_RETRY_S)
            return path
        local = store._ensure_local_file(_DM_BLOB_NAME)
        if local is None or not local.exists() or local.stat().st_size <= 0:
            logger.warning("daily_inventory_movement blob missing (%s)", _DM_BLOB_NAME)
            _DM_BLOB_FAIL_UNTIL = now + max(30.0, _DM_BLOB_RETRY_S)
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        if local.resolve() != path.resolve():
            path.write_bytes(local.read_bytes())
        try:
            meta_local = store._ensure_local_file(_DM_META_BLOB_NAME)
            if meta_local is not None and meta_local.exists():
                (path.parent / "daily_inventory_movement.meta.json").write_bytes(meta_local.read_bytes())
        except Exception:
            pass
        _DM_BLOB_OK = True
        _DM_BLOB_FAIL_UNTIL = 0.0
        logger.info(
            "Pulled daily_inventory_movement from blob → %s (%.1f MB)",
            path,
            path.stat().st_size / 1e6,
        )
    except Exception as exc:
        _DM_BLOB_FAIL_UNTIL = now + max(30.0, _DM_BLOB_RETRY_S)
        logger.warning(
            "daily_inventory_movement blob pull failed (retry in %.0fs): %s",
            _DM_BLOB_RETRY_S,
            exc,
        )
    return path


def publish_daily_movement_to_blob() -> dict[str, Any]:
    """Upload local DM parquet (+ meta) so hosted can pull without rebuilding."""
    path = daily_movement_parquet_path()
    if not path.exists() or path.stat().st_size <= 0:
        return {"ok": False, "error": f"missing local {path}"}
    try:
        from api.blob_store import upload_local_file_with_retry
        from api.routes.sales_dashboard import _blob_store

        store = _blob_store()
        if store is None:
            return {"ok": False, "error": "blob store unavailable"}
        upload_local_file_with_retry(
            store.container,
            blob_name=_DM_BLOB_NAME,
            path=path,
            content_type="application/octet-stream",
        )
        meta = path.parent / "daily_inventory_movement.meta.json"
        if meta.exists():
            upload_local_file_with_retry(
                store.container,
                blob_name=_DM_META_BLOB_NAME,
                path=meta,
                content_type="application/json",
                attempts=2,
                timeout_s=120,
            )
        return {
            "ok": True,
            "blob": _DM_BLOB_NAME,
            "bytes": path.stat().st_size,
            "mb": round(path.stat().st_size / 1e6, 2),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def site_lookup_keys(site_id: Any, site_name: Any = None) -> list[str]:
    """Strict site codes for SOH/STR — never city/name aliases."""
    keys: list[str] = []
    try:
        from api.historical_movement import _extract_single_site_key, site_key_series
    except Exception:
        _extract_single_site_key = None  # type: ignore
        site_key_series = None  # type: ignore

    for raw in (site_id, site_name):
        s = str(raw or "").strip()
        if not s or s.lower() in ("nan", "none", "<na>", "null"):
            continue
        su = s.upper()
        keys.append(su)
        if _extract_single_site_key is not None:
            try:
                k = _extract_single_site_key(s)
                if k:
                    keys.append(str(k).strip().upper())
            except Exception:
                pass
        if site_key_series is not None:
            try:
                k2 = site_key_series(pd.Series([s])).iloc[0]
                if k2 and str(k2).strip():
                    keys.append(str(k2).strip().upper())
            except Exception:
                pass
        for m in re.finditer(r"\(\s*([A-Z]?\d{2,6})\s*\)", su):
            keys.append(m.group(1).upper())

    out: list[str] = []
    seen: set[str] = set()
    for k in keys:
        if k and k not in seen and k not in {"(", ")", "-"}:
            seen.add(k)
            out.append(k)
    return out


def _soh_master_path() -> Optional[Path]:
    """Resolve SOH-Master current file (blob sync → local cache → input_files)."""
    try:
        from api.routes.stock_overview import _blob_soh_master_path

        path_s, _key = _blob_soh_master_path()
        if path_s and Path(path_s).exists() and Path(path_s).stat().st_size > 0:
            return Path(path_s)
    except Exception as exc:
        logger.warning("SOH-Master blob path skipped: %s", exc)
    candidates = [
        _BACKEND_DIR / "pipeline" / "input_files" / "SOH-Master" / "current" / "COMBINED_SOH_MASTER.csv",
        _BACKEND_DIR / "pipeline" / "input_files" / "COMBINED_SOH_MASTER.csv",
        _BACKEND_DIR / "pipeline" / "input_files" / "COMBINED_SOH_MASTER.xlsx",
        _BACKEND_DIR / "data" / "blob_cache" / "input_SOH-Master_current_COMBINED_SOH_MASTER.csv",
        _BACKEND_DIR / "data" / "blob_cache" / "input_SOH-Master_current_COMBINED_SOH_MASTER.xlsx",
    ]
    for p in candidates:
        if p.exists() and p.stat().st_size > 0:
            return p
    return None


def _read_soh_master_table(path: Path) -> pd.DataFrame:
    """SOH-Master is often an .xlsx saved with a .csv name (ZIP/PK header)."""
    raw = path.read_bytes()[:8]
    is_xlsx = raw.startswith(b"PK") or path.suffix.lower() in (".xlsx", ".xls")
    if is_xlsx:
        return pd.read_excel(path, engine="openpyxl")
    try:
        return pd.read_csv(path, dtype=str, low_memory=False)
    except Exception:
        return pd.read_excel(path, engine="openpyxl")


def load_soh_master_pairs(force_refresh: bool = False) -> pd.DataFrame:
    """Physical SOH from SOH-Master: store × EAN × QTY → closing_soh shape."""
    path = _soh_master_path()
    empty = pd.DataFrame(columns=["store", "barcode", "closing_soh", "site_key"])
    if path is None:
        logger.warning("SOH-Master file not found for Current SOH")
        return empty
    try:
        df = _read_soh_master_table(path)
        df.columns = [str(c).strip() for c in df.columns]
        colmap = {c.lower(): c for c in df.columns}

        def _col(*names: str) -> Optional[str]:
            for n in names:
                if n.lower() in colmap:
                    return colmap[n.lower()]
            return None

        c_site = _col("Site Code", "SITE CODE", "STORE", "SITE")
        c_ean = _col("EAN", "Barcode", "BARCODE", "ITEM BARCODE")
        c_qty = _col("QTY", "SOH", "CLOSING_SOH", "QUANTITY", "OH QTY")
        if not c_site or not c_ean or not c_qty:
            logger.warning(
                "SOH-Master missing columns (need Site Code/EAN/QTY); got %s",
                list(df.columns)[:20],
            )
            return empty

        out = pd.DataFrame(
            {
                "store": df[c_site].astype(str).str.strip().str.upper(),
                "barcode": (
                    df[c_ean]
                    .astype(str)
                    .str.strip()
                    .str.upper()
                    .str.replace(r"\.0$", "", regex=True)
                ),
                "closing_soh": pd.to_numeric(df[c_qty], errors="coerce").fillna(0.0),
            }
        )
        out = out.dropna(subset=["store", "barcode"])
        out = out[out["store"].ne("") & out["barcode"].ne("") & out["store"].str.lower().ne("nan")]

        from api.historical_movement import site_key_series

        out["site_key"] = site_key_series(out["store"]).astype(str).str.strip().str.upper()
        out["site_key"] = out["site_key"].mask(out["site_key"].eq(""), out["store"])
        out = (
            out.groupby(["store", "barcode", "site_key"], sort=False)["closing_soh"]
            .sum()
            .reset_index()
        )
        logger.info(
            "Current SOH pairs from SOH-Master (%s): total=%.0f stores=%d pairs=%d",
            path.name,
            float(out["closing_soh"].sum()),
            out["site_key"].nunique(),
            len(out),
        )
        return out
    except Exception as exc:
        logger.warning("load_soh_master_pairs failed: %s", exc)
        return empty


def load_daily_closing_soh_pairs(force_refresh: bool = False) -> pd.DataFrame:
    """Latest closing_soh per store × barcode from daily_inventory_movement."""
    path = ensure_daily_movement_from_blob()
    empty = pd.DataFrame(columns=["store", "barcode", "closing_soh", "site_key"])
    if not path.exists():
        logger.warning("daily_inventory_movement.parquet missing at %s", path)
        return empty

    try:
        cols = ["store", "barcode", "closing_soh"]
        try:
            dm = pd.read_parquet(path, columns=cols + ["date"])
            dm["date"] = pd.to_datetime(dm["date"], errors="coerce")
            if dm["date"].notna().any():
                idx = dm.groupby(["store", "barcode"], sort=False)["date"].transform("max") == dm["date"]
                latest = dm.loc[idx, cols].copy()
            else:
                latest = dm.groupby(["store", "barcode"], sort=False)["closing_soh"].last().reset_index()
        except Exception:
            dm = pd.read_parquet(path, columns=cols)
            latest = dm.groupby(["store", "barcode"], sort=False)["closing_soh"].last().reset_index()

        latest["store"] = latest["store"].astype(str).str.strip().str.upper()
        latest["barcode"] = (
            latest["barcode"].astype(str).str.strip().str.upper().str.replace(r"\.0$", "", regex=True)
        )
        latest["closing_soh"] = pd.to_numeric(latest["closing_soh"], errors="coerce").fillna(0.0)
        latest = latest.dropna(subset=["store", "barcode"])
        latest = latest[latest["store"].ne("") & latest["barcode"].ne("")]

        from api.historical_movement import site_key_series

        latest["site_key"] = site_key_series(latest["store"]).astype(str).str.strip().str.upper()
        latest["site_key"] = latest["site_key"].mask(latest["site_key"].eq(""), latest["store"])
        latest = (
            latest.groupby(["store", "barcode", "site_key"], sort=False)["closing_soh"]
            .sum()
            .reset_index()
        )
        logger.info(
            "Current SOH pairs from daily movement: total=%.0f stores=%d pairs=%d",
            float(latest["closing_soh"].sum()),
            latest["site_key"].nunique(),
            len(latest),
        )
        return latest
    except Exception as exc:
        logger.warning("load_daily_closing_soh_pairs failed: %s", exc)
        return empty


def load_latest_closing_soh_pairs(force_refresh: bool = False) -> pd.DataFrame:
    """
    Canonical Current SOH pairs (store, barcode, closing_soh, site_key).

    Default: SOH-Master. Use CURRENT_SOH_SOURCE=daily for movement ledger.
    """
    global _PAIRS_CACHE, _PAIRS_TIME, _PAIRS_SOURCE
    now = time.time()
    src = current_soh_source()
    if (
        not force_refresh
        and _PAIRS_CACHE is not None
        and _PAIRS_SOURCE == src
        and (now - _PAIRS_TIME) < _CACHE_TTL
        and not _PAIRS_CACHE.empty
    ):
        return _PAIRS_CACHE

    if src == "daily":
        latest = load_daily_closing_soh_pairs(force_refresh=force_refresh)
    else:
        latest = load_soh_master_pairs(force_refresh=force_refresh)
        if latest.empty:
            logger.warning("SOH-Master empty — falling back to daily movement for Current SOH")
            latest = load_daily_closing_soh_pairs(force_refresh=force_refresh)
            src = "daily_fallback"

    _PAIRS_CACHE = latest
    _PAIRS_TIME = now
    _PAIRS_SOURCE = src
    return latest


def site_current_soh_map(force_refresh: bool = False) -> dict[str, float]:
    """site_key / store code → total current SOH. Same totals everywhere."""
    global _SITE_CACHE, _SITE_TIME
    now = time.time()
    if (
        not force_refresh
        and _SITE_CACHE is not None
        and (now - _SITE_TIME) < _CACHE_TTL
    ):
        return _SITE_CACHE

    pairs = load_latest_closing_soh_pairs(force_refresh=force_refresh)
    if pairs.empty:
        _SITE_CACHE = {}
        _SITE_TIME = now
        return _SITE_CACHE

    by_key = pairs.groupby("site_key", sort=False)["closing_soh"].sum()
    by_store = pairs.groupby("store", sort=False)["closing_soh"].sum()
    out: dict[str, float] = {}
    for k, v in by_key.items():
        out[str(k)] = float(v)
    for k, v in by_store.items():
        sk = str(k)
        # Don't overwrite a different site_key total with raw store label unless new
        out.setdefault(sk, float(v))
    _SITE_CACHE = out
    _SITE_TIME = now
    return out


def soh_for_site_keys(site_keys: list[str] | set[str] | None) -> float:
    """Sum Current SOH for the given site codes / keys (Master or daily per config)."""
    if not site_keys:
        return 0.0
    smap = site_current_soh_map()
    total = 0.0
    seen: set[str] = set()
    for raw in site_keys:
        for k in site_lookup_keys(raw):
            if k in seen:
                continue
            if k in smap:
                total += float(smap[k])
                seen.add(k)
                break
    return total


def total_current_soh(force_refresh: bool = False) -> float:
    pairs = load_latest_closing_soh_pairs(force_refresh=force_refresh)
    if pairs.empty:
        return 0.0
    return float(pairs["closing_soh"].sum())


def site_soh_int_map(force_refresh: bool = False) -> dict[str, int]:
    """Integer site→SOH map for manager cards / exports."""
    return {k: int(round(v)) for k, v in site_current_soh_map(force_refresh=force_refresh).items()}


def site_soh_int_map_for_seasons(seasons: list[str] | None = None) -> dict[str, int]:
    """Current SOH by site, restricted to selected season SKUs in daily_inventory_movement."""
    seasons_u = {str(s).strip().upper() for s in (seasons or []) if str(s).strip()}
    if not seasons_u:
        return site_soh_int_map()

    path = daily_movement_parquet_path()
    if not path.exists():
        return {}
    try:
        cols = ["store", "barcode", "closing_soh", "season", "date"]
        dm = pd.read_parquet(path, columns=cols)
        dm["season"] = dm["season"].astype(str).str.strip().str.upper()
        dm = dm[dm["season"].isin(seasons_u)]
        if dm.empty:
            return {}
        dm["date"] = pd.to_datetime(dm["date"], errors="coerce")
        if dm["date"].notna().any():
            idx = dm.groupby(["store", "barcode"], sort=False)["date"].transform("max") == dm["date"]
            latest = dm.loc[idx, ["store", "barcode", "closing_soh"]].copy()
        else:
            latest = dm.groupby(["store", "barcode"], sort=False)["closing_soh"].last().reset_index()
        latest["store"] = latest["store"].astype(str).str.strip().str.upper()
        latest["closing_soh"] = pd.to_numeric(latest["closing_soh"], errors="coerce").fillna(0.0)
        from api.historical_movement import site_key_series

        latest["site_key"] = site_key_series(latest["store"]).astype(str).str.strip().str.upper()
        latest["site_key"] = latest["site_key"].mask(latest["site_key"].eq(""), latest["store"])
        by_key = latest.groupby("site_key", sort=False)["closing_soh"].sum()
        by_store = latest.groupby("store", sort=False)["closing_soh"].sum()
        out: dict[str, int] = {}
        for k, v in by_key.items():
            out[str(k)] = int(round(float(v)))
        for k, v in by_store.items():
            out.setdefault(str(k), int(round(float(v))))
        return out
    except Exception as exc:
        logger.warning("site_soh_int_map_for_seasons failed: %s", exc)
        return {}


def load_daily_str_by_site_for_seasons(seasons: list[str] | None = None) -> dict[str, dict[str, float]]:
    """Backward-compatible wrapper — season only, no date window."""
    return load_daily_str_by_site_for_scope(seasons=seasons)


def load_daily_str_by_site_for_scope(
    seasons: list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, dict[str, float]]:
    """
    Per-site Dispatch / Sold / STR for season + optional date window.

    Available = opening_soh (first day in window per store×barcode)
              + dispatch_in + ibt_in + sales_return_qty
    so STR does not exceed 100% just because opening stock was sold.
    """
    seasons_u = {str(s).strip().upper() for s in (seasons or []) if str(s).strip()}
    if not seasons_u and not date_from and not date_to:
        return load_daily_str_by_site()

    path = daily_movement_parquet_path()
    if not path.exists():
        return {}
    try:
        cols = [
            "store", "barcode", "season", "date", "opening_soh",
            "dispatch_in", "ibt_in", "sales_return_qty", "sales_qty", "full_price_sales_qty",
        ]
        dm = pd.read_parquet(path, columns=cols)
        if seasons_u and "season" in dm.columns:
            dm["season"] = dm["season"].astype(str).str.strip().str.upper()
            dm = dm[dm["season"].isin(seasons_u)]
        if dm.empty:
            return {}

        dm["date"] = pd.to_datetime(dm["date"], errors="coerce")
        if date_from:
            dm = dm[dm["date"] >= pd.Timestamp(date_from)]
        if date_to:
            dm = dm[dm["date"] <= pd.Timestamp(str(date_to) + " 23:59:59")]
        if dm.empty:
            return {}

        dm["store"] = dm["store"].astype(str).str.strip().str.upper()
        dm["barcode"] = (
            dm["barcode"].astype(str).str.strip().str.upper().str.replace(r"\.0$", "", regex=True)
        )
        for c in ("opening_soh", "dispatch_in", "ibt_in", "sales_return_qty", "sales_qty", "full_price_sales_qty"):
            dm[c] = pd.to_numeric(dm[c], errors="coerce").fillna(0.0)

        # Opening stock once per store×barcode = opening on earliest date in window
        dm = dm.sort_values(["store", "barcode", "date"])
        first = dm.groupby(["store", "barcode"], sort=False, as_index=False).first()
        opening = first.groupby("store", sort=False)["opening_soh"].sum()

        g = (
            dm.groupby("store", sort=False)
            .agg(
                dispatch_in=("dispatch_in", "sum"),
                ibt_in=("ibt_in", "sum"),
                sales_return_qty=("sales_return_qty", "sum"),
                sales_qty=("sales_qty", "sum"),
                full_price_sales_qty=("full_price_sales_qty", "sum"),
            )
            .reset_index()
        )
        g["opening_soh"] = g["store"].map(opening).fillna(0.0)
        g["available"] = (
            g["opening_soh"].clip(lower=0.0)
            + g["dispatch_in"]
            + g["ibt_in"]
            + g["sales_return_qty"]
        )

        from api.historical_movement import site_key_series

        g["site_key"] = site_key_series(g["store"]).astype(str).str.strip().str.upper()
        g["site_key"] = g["site_key"].mask(g["site_key"].eq(""), g["store"])
        out: dict[str, dict[str, float]] = {}

        def _put(key: str, row: Any) -> None:
            if not key:
                return
            prev = out.get(key)
            dispatch = float(row.dispatch_in)
            ibt = float(row.ibt_in)
            returns = float(row.sales_return_qty)
            opening_v = float(getattr(row, "opening_soh", 0.0) or 0.0)
            avail = float(row.available)
            sales = float(row.sales_qty)
            fp = float(row.full_price_sales_qty)
            if prev is None:
                out[key] = {
                    "dispatch_in": dispatch,
                    "ibt_in": ibt,
                    "sales_return_qty": returns,
                    "opening_soh": opening_v,
                    "available": avail,
                    "sales_qty": sales,
                    "full_price_sales_qty": fp,
                    "sell_through_pct": _pct(sales, avail),
                    "full_price_str_pct": _pct(fp, avail),
                }
            else:
                d2 = prev["dispatch_in"] + dispatch
                i2 = prev["ibt_in"] + ibt
                r2 = prev["sales_return_qty"] + returns
                o2 = float(prev.get("opening_soh", 0.0)) + opening_v
                a2 = prev["available"] + avail
                s2 = prev["sales_qty"] + sales
                f2 = prev["full_price_sales_qty"] + fp
                out[key] = {
                    "dispatch_in": d2,
                    "ibt_in": i2,
                    "sales_return_qty": r2,
                    "opening_soh": o2,
                    "available": a2,
                    "sales_qty": s2,
                    "full_price_sales_qty": f2,
                    "sell_through_pct": _pct(s2, a2),
                    "full_price_str_pct": _pct(f2, a2),
                }

        for row in g.itertuples(index=False):
            _put(str(row.store), row)
            if str(row.site_key) != str(row.store):
                _put(str(row.site_key), row)
        return out
    except Exception as exc:
        logger.warning("load_daily_str_by_site_for_scope failed: %s", exc)
        return {}


def network_daily_str_for_seasons(seasons: list[str] | None = None) -> dict[str, float]:
    return network_daily_str_for_scope(seasons=seasons)


def network_daily_str_for_scope(
    seasons: list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, float]:
    """Network Dispatch / Sold / STR for season + optional date window (opening-inclusive)."""
    by_site = load_daily_str_by_site_for_scope(seasons=seasons, date_from=date_from, date_to=date_to)
    if not by_site:
        return _empty_str_metrics()
    # Avoid double-count: prefer short site keys / unique store keys only
    # Sum using store keys that look like raw store labels is hard — sum unique available via
    # re-read would be safer; use first-seen unique by preferring keys without spaces as primary.
    seen_avail: dict[str, dict[str, float]] = {}
    for k, m in by_site.items():
        # Prefer short codes as canonical (P139, 259); skip long display duplicates later
        canon = k
        if len(k) > 12 and any(ch.isdigit() for ch in k):
            # keep but may duplicate — dedupe by matching sales_qty+dispatch signature
            pass
        sig = (round(m["dispatch_in"], 1), round(m["sales_qty"], 1), round(m["available"], 1))
        if sig in {(round(v["dispatch_in"], 1), round(v["sales_qty"], 1), round(v["available"], 1)) for v in seen_avail.values()}:
            continue
        seen_avail[canon] = m

    # Simpler: recompute from parquet once for network totals (no double-count)
    seasons_u = {str(s).strip().upper() for s in (seasons or []) if str(s).strip()}
    path = daily_movement_parquet_path()
    if not path.exists():
        return _empty_str_metrics()
    try:
        cols = [
            "store", "barcode", "season", "date", "opening_soh",
            "dispatch_in", "ibt_in", "sales_return_qty", "sales_qty", "full_price_sales_qty",
        ]
        dm = pd.read_parquet(path, columns=cols)
        if seasons_u:
            dm["season"] = dm["season"].astype(str).str.strip().str.upper()
            dm = dm[dm["season"].isin(seasons_u)]
        dm["date"] = pd.to_datetime(dm["date"], errors="coerce")
        if date_from:
            dm = dm[dm["date"] >= pd.Timestamp(date_from)]
        if date_to:
            dm = dm[dm["date"] <= pd.Timestamp(str(date_to) + " 23:59:59")]
        if dm.empty:
            return _empty_str_metrics()
        for c in ("opening_soh", "dispatch_in", "ibt_in", "sales_return_qty", "sales_qty", "full_price_sales_qty"):
            dm[c] = pd.to_numeric(dm[c], errors="coerce").fillna(0.0)
        dm = dm.sort_values(["store", "barcode", "date"])
        first = dm.groupby(["store", "barcode"], sort=False, as_index=False).first()
        opening = float(first["opening_soh"].clip(lower=0.0).sum())
        dispatch = float(dm["dispatch_in"].sum())
        ibt = float(dm["ibt_in"].sum())
        returns = float(dm["sales_return_qty"].sum())
        sales = float(dm["sales_qty"].sum())
        fp = float(dm["full_price_sales_qty"].sum())
        avail = opening + dispatch + ibt + returns
        return {
            "dispatch_in": dispatch,
            "ibt_in": ibt,
            "sales_return_qty": returns,
            "opening_soh": opening,
            "available": avail,
            "sales_qty": sales,
            "full_price_sales_qty": fp,
            "sell_through_pct": _pct(sales, avail),
            "full_price_str_pct": _pct(fp, avail),
        }
    except Exception as exc:
        logger.warning("network_daily_str_for_scope failed: %s", exc)
        return _empty_str_metrics()


# ── Sell-through from daily_inventory_movement (same formula as Sales Overview) ──
# STR % = sales_qty ÷ (dispatch_in + ibt_in + sales_return_qty) × 100
# FP STR % = full_price_sales_qty ÷ same available × 100

_STR_BY_SITE_CACHE: Optional[dict[str, dict[str, float]]] = None
_STR_BY_SITE_TIME: float = 0.0
_STR_NETWORK_CACHE: Optional[dict[str, float]] = None
_STR_NETWORK_TIME: float = 0.0


def _empty_str_metrics() -> dict[str, float]:
    return {
        "dispatch_in": 0.0,
        "ibt_in": 0.0,
        "sales_return_qty": 0.0,
        "available": 0.0,
        "sales_qty": 0.0,
        "full_price_sales_qty": 0.0,
        "sell_through_pct": 0.0,
        "full_price_str_pct": 0.0,
    }


def _pct(num: float, den: float) -> float:
    """Sell-through %; cap at 100 when sold > measured available (missing inflow / prior stock)."""
    if not den or den <= 0:
        return 0.0
    return round(min(100.0, float(num) / float(den) * 100.0), 1)


def load_daily_str_by_site(force_refresh: bool = False) -> dict[str, dict[str, float]]:
    """
    site_key / store → {available, sales_qty, full_price_sales_qty, sell_through_pct, full_price_str_pct}
    Lifetime sums from daily_inventory_movement — same definition as Sales Overview KPIs.
    """
    global _STR_BY_SITE_CACHE, _STR_BY_SITE_TIME
    now = time.time()
    if (
        not force_refresh
        and _STR_BY_SITE_CACHE is not None
        and (now - _STR_BY_SITE_TIME) < _CACHE_TTL
    ):
        return _STR_BY_SITE_CACHE

    path = daily_movement_parquet_path()
    if not path.exists():
        _STR_BY_SITE_CACHE = {}
        _STR_BY_SITE_TIME = now
        return _STR_BY_SITE_CACHE

    try:
        cols = ["store", "dispatch_in", "ibt_in", "sales_return_qty", "sales_qty", "full_price_sales_qty"]
        dm = pd.read_parquet(path, columns=cols)
        dm["store"] = dm["store"].astype(str).str.strip().str.upper()
        for c in ("dispatch_in", "ibt_in", "sales_return_qty", "sales_qty", "full_price_sales_qty"):
            dm[c] = pd.to_numeric(dm[c], errors="coerce").fillna(0.0)

        g = (
            dm.groupby("store", sort=False)
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

        from api.historical_movement import site_key_series

        g["site_key"] = site_key_series(g["store"]).astype(str).str.strip().str.upper()
        g["site_key"] = g["site_key"].mask(g["site_key"].eq(""), g["store"])

        out: dict[str, dict[str, float]] = {}

        def _put(
            key: str,
            dispatch: float,
            ibt: float,
            returns: float,
            avail: float,
            sales: float,
            fp: float,
        ) -> None:
            if not key:
                return
            prev = out.get(key)
            if prev is None:
                out[key] = {
                    "dispatch_in": float(dispatch),
                    "ibt_in": float(ibt),
                    "sales_return_qty": float(returns),
                    "available": float(avail),
                    "sales_qty": float(sales),
                    "full_price_sales_qty": float(fp),
                    "sell_through_pct": _pct(sales, avail),
                    "full_price_str_pct": _pct(fp, avail),
                }
            else:
                dispatch2 = float(prev["dispatch_in"]) + float(dispatch)
                ibt2 = float(prev["ibt_in"]) + float(ibt)
                returns2 = float(prev["sales_return_qty"]) + float(returns)
                avail2 = float(prev["available"]) + float(avail)
                sales2 = float(prev["sales_qty"]) + float(sales)
                fp2 = float(prev["full_price_sales_qty"]) + float(fp)
                out[key] = {
                    "dispatch_in": dispatch2,
                    "ibt_in": ibt2,
                    "sales_return_qty": returns2,
                    "available": avail2,
                    "sales_qty": sales2,
                    "full_price_sales_qty": fp2,
                    "sell_through_pct": _pct(sales2, avail2),
                    "full_price_str_pct": _pct(fp2, avail2),
                }

        for row in g.itertuples(index=False):
            payload = (
                float(row.dispatch_in),
                float(row.ibt_in),
                float(row.sales_return_qty),
                float(row.available),
                float(row.sales_qty),
                float(row.full_price_sales_qty),
            )
            _put(str(row.store), *payload)
            if str(row.site_key) != str(row.store):
                _put(str(row.site_key), *payload)

        _STR_BY_SITE_CACHE = out
        _STR_BY_SITE_TIME = now
        return out
    except Exception as exc:
        logger.warning("load_daily_str_by_site failed: %s", exc)
        _STR_BY_SITE_CACHE = {}
        _STR_BY_SITE_TIME = now
        return _STR_BY_SITE_CACHE


def network_daily_str(force_refresh: bool = False) -> dict[str, float]:
    """Network STR / FP STR from daily_inventory_movement — identical to Sales Overview cards."""
    global _STR_NETWORK_CACHE, _STR_NETWORK_TIME
    now = time.time()
    if (
        not force_refresh
        and _STR_NETWORK_CACHE is not None
        and (now - _STR_NETWORK_TIME) < _CACHE_TTL
    ):
        return _STR_NETWORK_CACHE

    path = daily_movement_parquet_path()
    if not path.exists():
        _STR_NETWORK_CACHE = _empty_str_metrics()
        _STR_NETWORK_TIME = now
        return _STR_NETWORK_CACHE

    try:
        cols = ["dispatch_in", "ibt_in", "sales_return_qty", "sales_qty", "full_price_sales_qty"]
        dm = pd.read_parquet(path, columns=cols)
        dispatch = float(pd.to_numeric(dm["dispatch_in"], errors="coerce").fillna(0.0).sum())
        ibt = float(pd.to_numeric(dm["ibt_in"], errors="coerce").fillna(0.0).sum())
        returns = float(pd.to_numeric(dm["sales_return_qty"], errors="coerce").fillna(0.0).sum())
        avail = dispatch + ibt + returns
        sales = float(pd.to_numeric(dm["sales_qty"], errors="coerce").fillna(0.0).sum())
        fp = float(pd.to_numeric(dm["full_price_sales_qty"], errors="coerce").fillna(0.0).sum())
        _STR_NETWORK_CACHE = {
            "dispatch_in": dispatch,
            "ibt_in": ibt,
            "sales_return_qty": returns,
            "available": avail,
            "sales_qty": sales,
            "full_price_sales_qty": fp,
            "sell_through_pct": _pct(sales, avail),
            "full_price_str_pct": _pct(fp, avail),
        }
        _STR_NETWORK_TIME = now
        logger.info(
            "Network STR from daily: avail=%.0f st=%.1f fp=%.1f",
            avail,
            _STR_NETWORK_CACHE["sell_through_pct"],
            _STR_NETWORK_CACHE["full_price_str_pct"],
        )
        return _STR_NETWORK_CACHE
    except Exception as exc:
        logger.warning("network_daily_str failed: %s", exc)
        _STR_NETWORK_CACHE = _empty_str_metrics()
        _STR_NETWORK_TIME = now
        return _STR_NETWORK_CACHE


def resolve_daily_str(
    site_id: Any,
    site_name: Any,
    str_map: dict[str, dict[str, float]] | None = None,
) -> dict[str, float]:
    """Lookup daily STR metrics for a store using the same site-code keys as SOH."""
    if str_map is None:
        str_map = load_daily_str_by_site()
    if not str_map:
        return _empty_str_metrics()
    for k in site_lookup_keys(site_id, site_name):
        if k and k in str_map:
            return dict(str_map[k])
    return _empty_str_metrics()


def aggregate_daily_str(
    site_rows: list[tuple[Any, Any]],
    str_map: dict[str, dict[str, float]] | None = None,
) -> dict[str, float]:
    """Sum available / sales / fp across unique site keys, then compute STR %."""
    if str_map is None:
        str_map = load_daily_str_by_site()
    seen: set[str] = set()
    dispatch = ibt = returns = avail = sales = fp = 0.0
    for site_id, site_name in site_rows:
        keys = site_lookup_keys(site_id, site_name)
        dedupe = keys[0] if keys else str(site_id)
        if dedupe in seen:
            continue
        seen.add(dedupe)
        m = resolve_daily_str(site_id, site_name, str_map)
        dispatch += float(m.get("dispatch_in", 0.0))
        ibt += float(m.get("ibt_in", 0.0))
        returns += float(m.get("sales_return_qty", 0.0))
        avail += float(m.get("available", 0.0))
        sales += float(m.get("sales_qty", 0.0))
        fp += float(m.get("full_price_sales_qty", 0.0))
    return {
        "dispatch_in": dispatch,
        "ibt_in": ibt,
        "sales_return_qty": returns,
        "available": avail,
        "sales_qty": sales,
        "full_price_sales_qty": fp,
        "sell_through_pct": _pct(sales, avail),
        "full_price_str_pct": _pct(fp, avail),
    }

"""
Focus Day Festival Event Analytics — Multi-year sales & growth comparison
for DurgaPuja, Diwali, Christmas, and Valentine using exact date windows.

Endpoint: GET /api/focus-day/festival-comparison
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import APIRouter

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/focus-day", tags=["Focus Day"])

_BACKEND = Path(__file__).resolve().parents[2]
_API_CACHE_DIR = _BACKEND / "data" / "api_cache"
_DISK_CACHE_FILE = _API_CACHE_DIR / "festival_comparison.json"
_DISK_META_FILE = _API_CACHE_DIR / "festival_comparison.meta"
_BLOB_CACHE_NAME = "cache/api_cache/festival_comparison.json"
_BLOB_META_NAME = "cache/api_cache/festival_comparison.meta"

_CACHE_LOCK = threading.RLock()
_BUILD_LOCK = threading.Lock()
_MEMORY_CACHE: tuple[str, dict[str, Any]] | None = None
_MEMORY_YEAR_SALES: dict[int, tuple[str, pd.DataFrame]] = {}
_STALE_REFRESH_RUNNING = False
_STALE_CHECK_AT = 0.0
_STALE_CHECK_EVERY_SECONDS = 600.0


def _publish_festival_cache_to_blob() -> dict[str, Any]:
    """Upload tiny festival JSON so hosted App Service can serve without rebuilding sales."""
    if not _DISK_CACHE_FILE.exists() or _DISK_CACHE_FILE.stat().st_size <= 0:
        return {"ok": False, "error": "missing local festival_comparison.json"}
    try:
        from api.blob_store import upload_local_file_with_retry
        from api.routes.sales_dashboard import _blob_store

        store = _blob_store()
        if store is None:
            return {"ok": False, "error": "blob store unavailable"}
        upload_local_file_with_retry(
            store.container,
            blob_name=_BLOB_CACHE_NAME,
            path=_DISK_CACHE_FILE,
            content_type="application/json",
            attempts=3,
            timeout_s=120,
        )
        if _DISK_META_FILE.exists():
            upload_local_file_with_retry(
                store.container,
                blob_name=_BLOB_META_NAME,
                path=_DISK_META_FILE,
                content_type="text/plain",
                attempts=2,
                timeout_s=60,
            )
        return {"ok": True, "blob": _BLOB_CACHE_NAME, "bytes": _DISK_CACHE_FILE.stat().st_size}
    except Exception as exc:
        logger.warning("Festival cache blob publish failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def _pull_festival_cache_from_blob() -> bool:
    """Hosted cold start: pull festival JSON before scanning Sales-Master."""
    try:
        from api.routes.sales_dashboard import _blob_store

        store = _blob_store()
        if store is None:
            return False
        local = store._ensure_local_file(_BLOB_CACHE_NAME)
        if local is None or not local.exists() or local.stat().st_size <= 0:
            return False
        _API_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        if local.resolve() != _DISK_CACHE_FILE.resolve():
            _DISK_CACHE_FILE.write_bytes(local.read_bytes())
        meta_local = store._ensure_local_file(_BLOB_META_NAME)
        if meta_local is not None and meta_local.exists() and meta_local.stat().st_size > 0:
            if meta_local.resolve() != _DISK_META_FILE.resolve():
                _DISK_META_FILE.write_bytes(meta_local.read_bytes())
        elif not _DISK_META_FILE.exists():
            # Blob payload alone is enough to serve; meta used only for stale checks.
            _DISK_META_FILE.write_text("blob", encoding="utf-8")
        logger.info("Festival comparison pulled from blob (%s bytes)", _DISK_CACHE_FILE.stat().st_size)
        return _DISK_CACHE_FILE.exists() and _DISK_CACHE_FILE.stat().st_size > 0
    except Exception as exc:
        logger.warning("Festival cache blob pull failed: %s", exc)
        return False

# Exact Event Windows defined by Business Rules
FESTIVAL_EVENTS = [
    {
        "id": "durgapuja",
        "name": "DurgaPuja",
        "dates": {
            "2023": {"from": "12-09-2023", "to": "24-10-2023", "isoFrom": "2023-09-12", "isoTo": "2023-10-24"},
            "2024": {"from": "01-09-2024", "to": "13-10-2024", "isoFrom": "2024-09-01", "isoTo": "2024-10-13"},
            "2025": {"from": "23-08-2025", "to": "02-10-2025", "isoFrom": "2025-08-23", "isoTo": "2025-10-02"},
            "2026": {"from": "08-09-2026", "to": "20-10-2026", "isoFrom": "2026-09-08", "isoTo": "2026-10-20"},
        },
    },
    {
        "id": "diwali",
        "name": "Diwali",
        "dates": {
            "2023": {"from": "25-10-2023", "to": "12-11-2023", "isoFrom": "2023-10-25", "isoTo": "2023-11-12"},
            "2024": {"from": "14-10-2024", "to": "31-10-2024", "isoFrom": "2024-10-14", "isoTo": "2024-10-31"},
            "2025": {"from": "03-10-2025", "to": "18-10-2025", "isoFrom": "2025-10-03", "isoTo": "2025-10-18"},
            "2026": {"from": "21-10-2026", "to": "08-11-2026", "isoFrom": "2026-10-21", "isoTo": "2026-11-08"},
        },
    },
    {
        "id": "christmas",
        "name": "Christmas",
        "dates": {
            "2023": {"from": "05-12-2023", "to": "25-12-2023", "isoFrom": "2023-12-05", "isoTo": "2023-12-25"},
            "2024": {"from": "05-12-2024", "to": "25-12-2024", "isoFrom": "2024-12-05", "isoTo": "2024-12-25"},
            "2025": {"from": "05-12-2025", "to": "25-12-2025", "isoFrom": "2025-12-05", "isoTo": "2025-12-25"},
            "2026": {"from": "05-12-2026", "to": "25-12-2026", "isoFrom": "2026-12-05", "isoTo": "2026-12-25"},
        },
    },
    {
        "id": "valentine",
        "name": "Valentine",
        "dates": {
            "2023": {"from": "01-02-2023", "to": "14-02-2023", "isoFrom": "2023-02-01", "isoTo": "2023-02-14"},
            "2024": {"from": "01-02-2024", "to": "14-02-2024", "isoFrom": "2024-02-01", "isoTo": "2024-02-14"},
            "2025": {"from": "01-02-2025", "to": "14-02-2025", "isoFrom": "2025-02-01", "isoTo": "2025-02-14"},
            "2026": {"from": "01-02-2026", "to": "14-02-2026", "isoFrom": "2026-02-01", "isoTo": "2026-02-14"},
        },
    },
]


def _combined_candidates() -> list[Path]:
    """Sales source candidates — Sales-Master first, then legacy COMBINED."""
    blob_dir = _BACKEND / "data" / "blob_cache"
    input_dir = _BACKEND / "pipeline" / "input_files"
    return [
        input_dir / "SSI_PT_CENTRO_ICONIC_EBO_SALES_MASTER.csv",
        input_dir / "SSI_PT_CENTRO_ICONIC_SALES_MASTER.csv",
        blob_dir / "input_Sales-Master_PT_SSL_Centro_Iconic_EBO_current_SSI_PT_CENTRO_ICONIC_EBO_SALES_MASTER.csv",
        blob_dir / "output_COMBINED_SALES.parquet",
        blob_dir / "output_COMBINED_SALES.csv",
        _BACKEND / "pipeline" / "output_files" / "COMBINED_SALES.csv",
        _BACKEND / "output_files" / "COMBINED_SALES.csv",
    ]


def _ensure_combined_from_blob() -> Path | None:
    """Pull Sales-Master (preferred) or COMBINED_SALES into local cache."""
    try:
        from api.sales_year_loader import resolve_sales_master_path

        master = resolve_sales_master_path()
        if master is not None and master.exists() and master.stat().st_size > 0:
            return master
    except Exception as exc:
        logger.warning("Festival Sales-Master ensure skipped: %s", exc)
    try:
        from api.blob_store import BlobJsonStore

        path = BlobJsonStore().ensure_output_table("COMBINED_SALES")
        if path is not None and path.exists() and path.stat().st_size > 0:
            return path
    except Exception as exc:
        logger.warning("Festival COMBINED_SALES blob pull skipped: %s", exc)
    return None


def _best_combined_path() -> Path | None:
    """Newest Sales-Master / COMBINED file wins."""
    _ensure_combined_from_blob()
    existing = [p for p in _combined_candidates() if p.exists() and p.stat().st_size > 0]
    try:
        from api.sales_year_loader import resolve_sales_master_path

        master = resolve_sales_master_path()
        if master is not None and master.exists() and master not in existing:
            existing.append(master)
    except Exception:
        pass
    if not existing:
        return None
    return max(existing, key=lambda p: (p.stat().st_mtime, p.stat().st_size))


def _source_key() -> str:
    parts: list[str] = []
    for p in _combined_candidates():
        if p.exists():
            parts.append(f"{p.name}:{p.stat().st_mtime_ns}:{p.stat().st_size}")
    blob_dir = _BACKEND / "data" / "blob_cache"
    for yr in (2024, 2025):
        yp = blob_dir / f"sales_{yr}.parquet"
        if yp.exists():
            parts.append(f"{yp.name}:{yp.stat().st_mtime_ns}:{yp.stat().st_size}")
    try:
        from api.sales_year_loader import resolve_sales_master_path

        master = resolve_sales_master_path()
        if master is not None and master.exists():
            parts.append(f"master:{master.name}:{master.stat().st_mtime_ns}:{master.stat().st_size}")
    except Exception:
        pass
    # Include any safe-named COMBINED cache files
    try:
        for p in blob_dir.glob("*COMBINED_SALES*"):
            if p.is_file() and p.suffix.lower() in {".parquet", ".csv", ".xlsx"} and not p.name.endswith(".etag"):
                parts.append(f"{p.name}:{p.stat().st_mtime_ns}:{p.stat().st_size}")
    except Exception:
        pass
    return "||".join(parts)


def _load_unified_sales_df() -> pd.DataFrame:
    """Load and normalize sales data across FY25, FY26, FY27 for festival comparisons."""
    from api.sales_year_loader import load_sales_for_year

    dfs: list[pd.DataFrame] = []
    for fy in (2025, 2026, 2027):
        try:
            df = load_sales_for_year(fy)
            if df is not None and not df.empty:
                df = df.copy()
                df["date"] = pd.to_datetime(df["date"], errors="coerce")
                df["net_sales"] = pd.to_numeric(df.get("net_sale_amount", 0), errors="coerce").fillna(0)
                df["qty"] = pd.to_numeric(df.get("qty", 0), errors="coerce").fillna(0)
                dept_s = df.get("department")
                df["dept"] = dept_s.astype(str).replace("nan", "").str.strip() if dept_s is not None else ""
                sec_s = df.get("section")
                df["sec"] = sec_s.astype(str).replace("nan", "").str.strip() if sec_s is not None else ""
                site = df.get("site_name", df.get("store_name", df.get("transaction_site")))
                df["site_name"] = site.astype(str).replace("nan", "").str.strip() if site is not None else ""
                sku = df.get("sku", df.get("barcode"))
                df["sku"] = sku.astype(str).replace("nan", "").str.strip() if sku is not None else ""
                disc = df.get("discount_amount", df.get("item_discount_amount"))
                df["discount"] = (
                    pd.to_numeric(disc, errors="coerce").fillna(0) if disc is not None else 0.0
                )
                dfs.append(df[["date", "net_sales", "qty", "dept", "sec", "site_name", "sku", "discount"]])
        except Exception as exc:
            logger.warning("Failed to load FY%s in festival comparison: %s", fy, exc)

    if not dfs:
        return pd.DataFrame(
            columns=["date", "net_sales", "qty", "dept", "sec", "site_name", "sku", "discount"]
        )
    out = pd.concat(dfs, ignore_index=True)
    out = out[out["date"].notna()].sort_values("date").reset_index(drop=True)
    return out


def _window_slice(df: pd.DataFrame, iso_from: str, iso_to: str) -> pd.DataFrame:
    if df is None or df.empty or "date" not in df.columns:
        return pd.DataFrame()
    start = pd.Timestamp(iso_from).normalize()
    # Inclusive end date (whole calendar day), not midnight-only
    end = pd.Timestamp(iso_to).normalize() + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
    return df[(df["date"] >= start) & (df["date"] <= end)]


def _top_group(sub: pd.DataFrame, key: str, limit: int = 10) -> list[dict[str, Any]]:
    if sub.empty or key not in sub.columns:
        return []
    g = (
        sub.groupby(key, as_index=False)
        .agg(sales=("net_sales", "sum"), qty=("qty", "sum"))
        .sort_values("sales", ascending=False)
        .head(limit)
    )
    out: list[dict[str, Any]] = []
    for _, r in g.iterrows():
        label = str(r[key] or "").strip()
        if not label or label.lower() in {"nan", "none"}:
            continue
        out.append({"name": label, "sales": round(float(r["sales"]), 1), "qty": int(r["qty"])})
    return out


def _calculate_growth(current: float, previous: float) -> float | None:
    if previous > 0:
        return round(100.0 * (current - previous) / previous, 1)
    return None


def _read_disk_payload() -> tuple[str, dict[str, Any]] | None:
    if not _DISK_CACHE_FILE.exists() or not _DISK_META_FILE.exists():
        return None
    try:
        saved_key = _DISK_META_FILE.read_text(encoding="utf-8").strip()
        with open(_DISK_CACHE_FILE, "r", encoding="utf-8") as f:
            cached_data = json.load(f)
        if not isinstance(cached_data, dict) or not cached_data.get("events"):
            return None
        return saved_key, cached_data
    except Exception as exc:
        logger.warning("Could not read festival disk cache: %s", exc)
        return None


def _persist_payload(key: str, response: dict[str, Any]) -> None:
    try:
        _API_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp_json = _API_CACHE_DIR / "festival_comparison.tmp.json"
        tmp_meta = _API_CACHE_DIR / "festival_comparison.tmp.meta"
        with open(tmp_json, "w", encoding="utf-8") as f:
            json.dump(response, f)
        tmp_meta.write_text(key, encoding="utf-8")
        os.replace(tmp_json, _DISK_CACHE_FILE)
        os.replace(tmp_meta, _DISK_META_FILE)
        logger.info("Persisted festival comparison to disk cache")
        pub = _publish_festival_cache_to_blob()
        if not pub.get("ok"):
            logger.warning("Festival disk saved but blob publish skipped: %s", pub.get("error"))
    except Exception as exc:
        logger.warning("Could not persist festival disk cache: %s", exc)


def _serve_cached() -> dict[str, Any] | None:
    """Return memory, local disk, or Azure blob payload — never scan sales on a cache hit."""
    global _MEMORY_CACHE
    with _CACHE_LOCK:
        if _MEMORY_CACHE and _MEMORY_CACHE[1].get("events"):
            return _MEMORY_CACHE[1]
    disk = _read_disk_payload()
    if disk is None:
        if _pull_festival_cache_from_blob():
            disk = _read_disk_payload()
    if disk is None:
        return None
    key, data = disk
    with _CACHE_LOCK:
        _MEMORY_CACHE = (key, data)
    logger.info("Festival comparison loaded from disk/blob cache")
    return data


def _maybe_refresh_in_background(served_key: str) -> None:
    """If sales files moved, rebuild off the request path so the page stays fast."""
    global _STALE_REFRESH_RUNNING, _STALE_CHECK_AT
    now = time.time()
    with _CACHE_LOCK:
        if _STALE_REFRESH_RUNNING or (now - _STALE_CHECK_AT) < _STALE_CHECK_EVERY_SECONDS:
            return
        _STALE_REFRESH_RUNNING = True
        _STALE_CHECK_AT = now

    def _run() -> None:
        global _STALE_REFRESH_RUNNING
        try:
            try:
                from api.sales_year_loader import resolve_sales_master_path

                resolve_sales_master_path()
            except Exception as exc:
                logger.warning("Festival Sales-Master ensure skipped: %s", exc)
            _ensure_combined_from_blob()
            key = _source_key()
            if key == served_key:
                return
            logger.info("Festival comparison source changed — rebuilding in background")
            _rebuild_festival_comparison_locked(force=True)
        except Exception as exc:
            logger.warning("Festival comparison background refresh failed: %s", exc)
        finally:
            with _CACHE_LOCK:
                _STALE_REFRESH_RUNNING = False

    threading.Thread(target=_run, name="festival-comparison-refresh", daemon=True).start()


def _rebuild_festival_comparison_locked(*, force: bool = False) -> dict[str, Any]:
    global _MEMORY_CACHE
    with _BUILD_LOCK:
        if not force:
            with _CACHE_LOCK:
                if _MEMORY_CACHE and _MEMORY_CACHE[1].get("events"):
                    return _MEMORY_CACHE[1]

        try:
            from api.sales_year_loader import resolve_sales_master_path

            resolve_sales_master_path()
        except Exception as exc:
            logger.warning("Festival Sales-Master ensure skipped: %s", exc)
        _ensure_combined_from_blob()
        key = _source_key()

        unified_sales = _load_unified_sales_df()
        response = _festival_response_from_sales(unified_sales)
        with _CACHE_LOCK:
            _MEMORY_CACHE = (key, response)
        _persist_payload(key, response)
        return response


def warm_festival_comparison_cache(*, force_refresh: bool = False) -> dict[str, Any]:
    """Prebuild + publish festival JSON so hosted Festival Day opens instantly."""
    try:
        if not force_refresh and _serve_cached() is not None:
            pub = _publish_festival_cache_to_blob()
            logger.info("Festival comparison cache already ready — published=%s", pub.get("ok"))
            return {"ok": True, "skipped": True, "published": pub}
        payload = get_festival_comparison(force_refresh=True)
        pub = _publish_festival_cache_to_blob()
        logger.info(
            "Festival comparison cache warmed events=%s published=%s",
            len((payload or {}).get("events") or []),
            pub.get("ok"),
        )
        return {
            "ok": bool((payload or {}).get("ok", True) and (payload or {}).get("events")),
            "skipped": False,
            "published": pub,
            "events": len((payload or {}).get("events") or []),
        }
    except Exception as exc:
        logger.warning("Festival comparison cache warm failed: %s", exc)
        return {"ok": False, "error": str(exc)}


@router.get("/festival-comparison")
def get_festival_comparison(force_refresh: bool = False) -> dict[str, Any]:
    """
    Returns Focus Day event sales & YoY growth comparison (2024, 2025, 2026)
    for DurgaPuja, Diwali, Christmas, Valentine — from real sales masters.

    A ready cache is returned immediately. Blob checks and a full sales scan
    never sit on the request that can already be served.
    """
    if not force_refresh:
        cached = _serve_cached()
        if cached is not None:
            served_key = ""
            with _CACHE_LOCK:
                if _MEMORY_CACHE:
                    served_key = _MEMORY_CACHE[0]
            if served_key:
                _maybe_refresh_in_background(served_key)
            return cached
        return _rebuild_festival_comparison_locked(force=False)

    return _rebuild_festival_comparison_locked(force=True)


def _festival_response_from_sales(unified_sales: pd.DataFrame) -> dict[str, Any]:
    """Aggregate festival windows. No cache or blob I/O."""

    event_results: list[dict[str, Any]] = []

    for ev in FESTIVAL_EVENTS:
        ev_id = ev["id"]
        ev_name = ev["name"]
        dates = ev["dates"]

        metrics_by_year: dict[str, dict[str, Any]] = {}
        dept_breakdown: list[dict[str, Any]] = []
        top_stores: list[dict[str, Any]] = []
        top_skus: list[dict[str, Any]] = []
        detail_year: int | None = None

        for yr_str in ("2023", "2024", "2025", "2026"):
            yr_int = int(yr_str)
            d_info = dates[yr_str]
            iso_from, iso_to = d_info["isoFrom"], d_info["isoTo"]
            days = (pd.Timestamp(iso_to) - pd.Timestamp(iso_from)).days + 1

            sub = _window_slice(unified_sales, iso_from, iso_to)
            total_sales = float(sub["net_sales"].sum()) if not sub.empty else 0.0
            total_qty = float(sub["qty"].sum()) if not sub.empty else 0.0
            total_disc = float(sub["discount"].sum()) if not sub.empty and "discount" in sub.columns else 0.0
            avg_daily = round(total_sales / max(days, 1), 1)
            asp = round(total_sales / total_qty, 1) if total_qty > 0 else 0.0
            discount_pct = round(100.0 * total_disc / total_sales, 1) if total_sales > 0 else 0.0

            if not sub.empty and total_sales > 0 and (detail_year is None or yr_int >= detail_year):
                detail_year = yr_int
                dept_breakdown = [
                    {
                        "department": r["name"],
                        "sales": r["sales"],
                        "qty": r["qty"],
                        "year": yr_int,
                    }
                    for r in _top_group(sub, "dept", 8)
                ]
                top_stores = _top_group(sub, "site_name", 10)
                top_skus = _top_group(sub, "sku", 10)

            metrics_by_year[yr_str] = {
                "sales": round(total_sales, 1),
                "qty": int(total_qty),
                "days": days,
                "avgDailySales": avg_daily,
                "asp": asp,
                "discountPct": discount_pct,
                "fromDate": d_info["from"],
                "toDate": d_info["to"],
                "isoFrom": iso_from,
                "isoTo": iso_to,
            }

        growth_2025_vs_2024 = _calculate_growth(
            metrics_by_year["2025"]["sales"], metrics_by_year["2024"]["sales"]
        )
        growth_2026_vs_2025 = _calculate_growth(
            metrics_by_year["2026"]["sales"], metrics_by_year["2025"]["sales"]
        )
        unit_growth_2025_vs_2024 = _calculate_growth(
            metrics_by_year["2025"]["qty"], metrics_by_year["2024"]["qty"]
        )
        unit_growth_2026_vs_2025 = _calculate_growth(
            metrics_by_year["2026"]["qty"], metrics_by_year["2025"]["qty"]
        )

        event_results.append(
            {
                "id": ev_id,
                "name": ev_name,
                "dates": dates,
                "metrics": metrics_by_year,
                "growth": {
                    "growth2025vs2024": growth_2025_vs_2024,
                    "growth2026vs2025": growth_2026_vs_2025,
                    "unitGrowth2025vs2024": unit_growth_2025_vs_2024,
                    "unitGrowth2026vs2025": unit_growth_2026_vs_2025,
                },
                "topDepartments": dept_breakdown,
                "topStores": top_stores,
                "topSkus": top_skus,
                "detailYear": detail_year,
            }
        )

    total_sales_2024 = sum(e["metrics"]["2024"]["sales"] for e in event_results)
    total_sales_2025 = sum(e["metrics"]["2025"]["sales"] for e in event_results)
    total_sales_2026 = sum(e["metrics"]["2026"]["sales"] for e in event_results)
    total_qty_2024 = sum(e["metrics"]["2024"]["qty"] for e in event_results)
    total_qty_2025 = sum(e["metrics"]["2025"]["qty"] for e in event_results)
    total_qty_2026 = sum(e["metrics"]["2026"]["qty"] for e in event_results)

    return {
        "ok": True,
        "source": "sales_master",
        "events": event_results,
        "summary": {
            "totalFestiveSales2024": round(total_sales_2024, 1),
            "totalFestiveSales2025": round(total_sales_2025, 1),
            "totalFestiveSales2026": round(total_sales_2026, 1),
            "totalQty2024": int(total_qty_2024),
            "totalQty2025": int(total_qty_2025),
            "totalQty2026": int(total_qty_2026),
            "totalGrowth2025vs2024": _calculate_growth(total_sales_2025, total_sales_2024),
            "totalGrowth2026vs2025": _calculate_growth(total_sales_2026, total_sales_2025),
            "asp2024": round(total_sales_2024 / total_qty_2024, 1) if total_qty_2024 > 0 else 0.0,
            "asp2025": round(total_sales_2025 / total_qty_2025, 1) if total_qty_2025 > 0 else 0.0,
            "asp2026": round(total_sales_2026 / total_qty_2026, 1) if total_qty_2026 > 0 else 0.0,
        },
    }

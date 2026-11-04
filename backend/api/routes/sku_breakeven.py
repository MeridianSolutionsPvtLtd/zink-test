"""
SKU Breakeven Analysis
- Produced qty: GRC Finished Goods (via dispatch-timing lot dataset)
- Cost: Item Master STD RATE
- MRP: Item Master MRP (fallback RSP)
- Landed = cost × (1 + GST% + 5% logistics + 40% commission)
  GST%: 18% if MRP > 2625 else 5%
- Breakeven qty = (produced × landed) / MRP
- Also surfaces dispatch vs breakeven with action suggestions
"""
from __future__ import annotations

import logging
import math
import sys
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from fastapi import APIRouter

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/inventory-analytics", tags=["Inventory Analytics - Breakeven"])

_BACKEND = Path(__file__).resolve().parents[2]
_PIPELINE = _BACKEND / "pipeline"
_INPUT_DIR = _PIPELINE / "input_files"
_CACHE_DIR = _BACKEND / "data" / "api_cache"

if str(_PIPELINE) not in sys.path:
    sys.path.insert(0, str(_PIPELINE))

_CACHE_LOCK = threading.RLock()
_MEMORY: tuple[str, pd.DataFrame] | None = None
_REPORT_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}

LOGISTICS_PCT = 0.05
COMMISSION_PCT = 0.40
GST_THRESHOLD_MRP = 2625.0


def _gst_pct(mrp: float) -> float:
    return 0.18 if mrp > GST_THRESHOLD_MRP else 0.05


def _suggestion(
    produced: float,
    dispatched: float,
    be_qty: float,
    lot_remaining: float,
    landed: float,
    mrp: float,
) -> tuple[str, str]:
    """Return (code, message).

    Math: BE = (landed × produced) / MRP.
    You cannot sell/dispatch more than produced — if BE > produced,
    even 100% sell at MRP will not recover full cost (price/cost problem).
    Dispatch target is therefore min(BE, produced).
    """
    if produced <= 0:
        return "no_produce", "No finished-goods inward found for this SKU."
    if be_qty <= 0 or landed <= 0 or mrp <= 0:
        return "no_be", "Cost or MRP missing — cannot compute breakeven qty."

    be_i = int(math.ceil(be_qty))
    prod_i = int(round(produced))
    disp_i = int(round(dispatched))
    # Max useful dispatch for cost recovery path = what you actually made
    dispatch_target = min(be_i, prod_i)
    gap = max(0, dispatch_target - disp_i)
    cannot_recover = be_i > prod_i

    if cannot_recover:
        base = (
            f"Full cost ₹{landed:,.0f}/pc > MRP ₹{mrp:,.0f} — math needs {be_i:,} sold, "
            f"but only {prod_i:,} were produced. Even 100% sell-through will not recover cost. "
            f"Check MRP / cost in Item Master."
        )
        if disp_i < prod_i and lot_remaining > 0:
            return (
                "margin_shortfall",
                base + f" You can still dispatch remaining WH stock (~{int(lot_remaining):,}).",
            )
        return ("margin_shortfall", base)

    if disp_i <= 0:
        return (
            "zero_dispatch",
            f"Produced {prod_i:,}; need ~{be_i:,} sold to recover cost (full cost ÷ MRP). "
            f"Zero store dispatch so far — send ~{dispatch_target:,} pcs to stores first.",
        )
    if disp_i < dispatch_target:
        return (
            "under_dispatch",
            f"Dispatched {disp_i:,} / recovery target {dispatch_target:,}. "
            f"Send ~{gap:,} more to stores"
            + (f" (WH still holds ~{int(lot_remaining):,} open)" if lot_remaining > 0 else "")
            + ".",
        )
    if lot_remaining > produced * 0.05:
        return (
            "dispatch_ok_wh_left",
            f"Dispatch already covers recovery target ({dispatch_target:,}). "
            f"Focus on sell-through; ~{int(lot_remaining):,} still open at WH.",
        )
    return (
        "dispatch_ok",
        f"Dispatch already covers recovery target ({dispatch_target:,}). Focus on sell-through.",
    )


def _find_col(df: pd.DataFrame, *candidates: str) -> str | None:
    if df is None or df.empty:
        return None
    cols = {str(c).strip().upper(): str(c) for c in df.columns}
    for cand in candidates:
        hit = cols.get(str(cand).strip().upper())
        if hit is not None:
            return hit
    # soft contains match
    for cand in candidates:
        key = str(cand).strip().upper()
        for up, orig in cols.items():
            if key and key in up:
                return orig
    return None


def _ensure_sku_meta_from_blob() -> Path:
    """Pull published Item Master SKU meta when local cache is missing (Azure)."""
    disk = _CACHE_DIR / "item_master_sku_meta.parquet"
    if disk.exists() and disk.stat().st_size > 0:
        return disk
    try:
        from api.routes.sales_dashboard import _blob_store

        store = _blob_store()
        if store is None:
            return disk
        local = store._ensure_local_file("cache/api_cache/item_master_sku_meta.parquet")
        if local is not None and local.exists() and local.stat().st_size > 0:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            disk.write_bytes(local.read_bytes())
            logger.info("Pulled item_master_sku_meta from blob (%.1f KB)", disk.stat().st_size / 1e3)
    except Exception as exc:
        logger.warning("item_master_sku_meta blob pull failed: %s", exc)
    return disk


def _load_breakeven_cost_mrp() -> pd.DataFrame:
    """
    SKU → cost (STD RATE) + MRP for breakeven.
    Prefer fast disk cache (product_performance meta); fall back to Item Master read.
    Hosted App Service often cannot open the full ITEM_MASTER workbook in time —
    without this fallback every SKU shows ₹0 / Missing data.
    """
    # 1) Shared sku-meta parquet (already used by Product Performance)
    disk = _ensure_sku_meta_from_blob()
    if disk.exists() and disk.stat().st_size > 0:
        try:
            meta = pd.read_parquet(disk)
            if not meta.empty and "sku" in meta.columns:
                out = pd.DataFrame(
                    {
                        "sku": meta["sku"].astype(str).str.strip().str.upper(),
                        "cost_price": pd.to_numeric(meta.get("cost"), errors="coerce"),
                        "mrp": pd.to_numeric(meta.get("mrp"), errors="coerce"),
                    }
                )
                out = out[out["sku"].ne("") & ~out["sku"].isin(["NAN", "NONE", "NULL"])]
                out = out.groupby("sku", as_index=False).agg(
                    cost_price=("cost_price", "median"),
                    mrp=("mrp", "median"),
                )
                if (out["cost_price"].fillna(0) > 0).sum() > 0 or (out["mrp"].fillna(0) > 0).sum() > 0:
                    logger.info(
                        "Breakeven prices from item_master_sku_meta (%d SKUs, cost>0=%d mrp>0=%d)",
                        len(out),
                        int((out["cost_price"].fillna(0) > 0).sum()),
                        int((out["mrp"].fillna(0) > 0).sum()),
                    )
                    return out
        except Exception as exc:
            logger.warning("Breakeven sku-meta parquet read failed: %s", exc)

    # 2) product_performance loader (blob/local aware)
    try:
        from api.routes.product_performance import _load_item_master_meta

        meta = _load_item_master_meta()
        if meta is not None and not meta.empty and "sku" in meta.columns:
            out = pd.DataFrame(
                {
                    "sku": meta["sku"].astype(str).str.strip().str.upper(),
                    "cost_price": pd.to_numeric(meta.get("cost"), errors="coerce"),
                    "mrp": pd.to_numeric(meta.get("mrp"), errors="coerce"),
                }
            )
            out = out[out["sku"].ne("") & ~out["sku"].isin(["NAN", "NONE", "NULL"])]
            return out.groupby("sku", as_index=False).agg(
                cost_price=("cost_price", "median"),
                mrp=("mrp", "median"),
            )
    except Exception as exc:
        logger.warning("Breakeven _load_item_master_meta failed: %s", exc)

    # 3) Direct Item Master with flexible column names
    try:
        from item_master import load_item_master_df

        im = load_item_master_df()
    except Exception:
        try:
            from pipeline_inputs import load_item_master

            im = load_item_master()
        except Exception as exc:
            logger.warning("Item master load failed for breakeven: %s", exc)
            im = pd.DataFrame()

    if im is None or im.empty:
        return pd.DataFrame(columns=["sku", "cost_price", "mrp"])

    im = im.copy()
    im.columns = [str(c).strip() for c in im.columns]
    sku_col = _find_col(im, "SKU CODE", "SKU", "STYLE ID", "ITEM CODE", "ICODE")
    cost_col = _find_col(im, "STD RATE", "STANDARD RATE", "COST", "FACTORY COST", "RATE")
    mrp_col = _find_col(im, "MRP", "RSP", "SELLING PRICE", "RSP RATE")
    if not sku_col:
        return pd.DataFrame(columns=["sku", "cost_price", "mrp"])

    out = pd.DataFrame(
        {
            "sku": im[sku_col].astype(str).str.strip().str.upper(),
            "cost_price": pd.to_numeric(im[cost_col], errors="coerce") if cost_col else np.nan,
            "mrp": pd.to_numeric(im[mrp_col], errors="coerce") if mrp_col else np.nan,
        }
    )
    # If MRP missing but RSP present under another name already tried; keep NaN.
    out = out[out["sku"].ne("") & ~out["sku"].isin(["NAN", "NONE", "NULL"])]
    return out.groupby("sku", as_index=False).agg(
        cost_price=("cost_price", "median"),
        mrp=("mrp", "median"),
    )


def _iso_day(val: Any) -> str:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    try:
        ts = pd.Timestamp(val)
        if pd.isna(ts):
            return ""
        return ts.strftime("%Y-%m-%d")
    except Exception:
        return str(val)[:10]


_BLOB_BREAKEVEN_PQ = "cache/api_cache/sku_breakeven.parquet"
_BLOB_BREAKEVEN_KEY = "cache/api_cache/sku_breakeven.key"


def publish_sku_breakeven_to_blob() -> dict:
    disk_file = _CACHE_DIR / "sku_breakeven.parquet"
    meta_key = _CACHE_DIR / "sku_breakeven.key"
    if not disk_file.exists() or disk_file.stat().st_size <= 0:
        return {"ok": False, "error": "missing local sku_breakeven.parquet"}
    try:
        from api.blob_store import upload_local_file_with_retry
        from api.routes.sales_dashboard import _blob_store

        store = _blob_store()
        if store is None:
            return {"ok": False, "error": "blob store unavailable"}
        upload_local_file_with_retry(
            store.container,
            blob_name=_BLOB_BREAKEVEN_PQ,
            path=disk_file,
            content_type="application/octet-stream",
            attempts=3,
            timeout_s=120,
        )
        if meta_key.exists():
            upload_local_file_with_retry(
                store.container,
                blob_name=_BLOB_BREAKEVEN_KEY,
                path=meta_key,
                content_type="text/plain",
                attempts=2,
                timeout_s=60,
            )
        return {"ok": True, "blob": _BLOB_BREAKEVEN_PQ, "bytes": disk_file.stat().st_size}
    except Exception as exc:
        logger.warning("SKU breakeven blob publish failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def _pull_sku_breakeven_from_blob() -> bool:
    disk_file = _CACHE_DIR / "sku_breakeven.parquet"
    meta_key = _CACHE_DIR / "sku_breakeven.key"
    try:
        from api.routes.sales_dashboard import _blob_store

        store = _blob_store()
        if store is None:
            return False
        local = store._ensure_local_file(_BLOB_BREAKEVEN_PQ)
        if local is None or not local.exists() or local.stat().st_size <= 0:
            return False
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        if local.resolve() != disk_file.resolve():
            disk_file.write_bytes(local.read_bytes())
        meta = store._ensure_local_file(_BLOB_BREAKEVEN_KEY)
        if meta is not None and meta.exists() and meta.stat().st_size > 0:
            if meta.resolve() != meta_key.resolve():
                meta_key.write_bytes(meta.read_bytes())
        elif not meta_key.exists():
            meta_key.write_text("blob", encoding="utf-8")
        logger.info("SKU breakeven pulled from blob (%s bytes)", disk_file.stat().st_size)
        return disk_file.exists() and disk_file.stat().st_size > 0
    except Exception as exc:
        logger.warning("SKU breakeven blob pull failed: %s", exc)
        return False


def _breakeven_df_usable(df: pd.DataFrame | None) -> bool:
    if df is None or df.empty:
        return False
    if "cost_price" in df.columns and "mrp" in df.columns:
        good = int(((df["cost_price"].fillna(0) > 0) & (df["mrp"].fillna(0) > 0)).sum())
        if good == 0 and len(df) > 10:
            return False
    return True


def _compute_breakeven_dataset(*, force_refresh: bool = False) -> pd.DataFrame:
    global _MEMORY

    from api.routes.dispatch_lead_time import _compute_lead_time_dataset

    disk_file = _CACHE_DIR / "sku_breakeven.parquet"
    meta_key = _CACHE_DIR / "sku_breakeven.key"
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if not force_refresh and (not disk_file.exists() or disk_file.stat().st_size <= 0):
        _pull_sku_breakeven_from_blob()

    # Fast path: serve ready parquet/memory WITHOUT loading lead-time.
    # Old code always called _compute_lead_time_dataset first to build a cache key,
    # so blob-pulled files (key="blob") never matched and hosted timed out → 502.
    if not force_refresh:
        with _CACHE_LOCK:
            if _MEMORY and isinstance(_MEMORY[1], pd.DataFrame) and _breakeven_df_usable(_MEMORY[1]):
                return _MEMORY[1]
        if disk_file.exists() and disk_file.stat().st_size > 0:
            try:
                df = pd.read_parquet(disk_file)
                if _breakeven_df_usable(df):
                    stamp = meta_key.read_text(encoding="utf-8").strip() if meta_key.exists() else "disk"
                    with _CACHE_LOCK:
                        _MEMORY = (stamp or "disk", df)
                    logger.info("Breakeven served from disk/blob cache (%s rows)", len(df))
                    return df
            except Exception as exc:
                logger.warning("Breakeven parquet read failed: %s", exc)

    # Keep last good dataset — never leave UI on 0 SKUs if a rebuild fails.
    prev_good: pd.DataFrame | None = None
    with _CACHE_LOCK:
        if _MEMORY and isinstance(_MEMORY[1], pd.DataFrame) and not _MEMORY[1].empty:
            prev_good = _MEMORY[1]
    if prev_good is None and disk_file.exists():
        try:
            loaded = pd.read_parquet(disk_file)
            if loaded is not None and not loaded.empty:
                prev_good = loaded
        except Exception:
            pass

    # Rebuild path only — lead-time has its own Refresh.
    dlt = _compute_lead_time_dataset(force_refresh=False)
    if dlt is None or dlt.empty:
        logger.warning("Breakeven: lead-time dataset empty — serving previous cache if any")
        return prev_good if prev_good is not None else pd.DataFrame()

    im_path = _INPUT_DIR / "ITEM_MASTER.xlsx"
    if not im_path.exists():
        im_path = _INPUT_DIR / "ITEM_MASTER.csv"
    if not im_path.exists():
        im_path = _INPUT_DIR / "Item Master" / "current" / "ITEM_MASTER.csv"
    im_mtime = im_path.stat().st_mtime_ns if im_path.exists() else 0
    meta_pq = _CACHE_DIR / "item_master_sku_meta.parquet"
    meta_mtime = meta_pq.stat().st_mtime_ns if meta_pq.exists() else 0

    # Tie to dispatch dataset identity via row count + mtimes inside dlt cache key indirectly
    key = f"be_v6_{len(dlt)}_{int(dlt['inward_qty'].sum())}_{im_mtime}_{meta_mtime}"

    if force_refresh:
        with _CACHE_LOCK:
            _MEMORY = None
            _REPORT_CACHE.clear()
        # Rebuild Item Master pricing meta (do not delete breakeven parquet until new DF ready)
        try:
            meta_key_pp = _CACHE_DIR / "item_master_sku_meta.key"
            if meta_pq.exists():
                meta_pq.unlink()
            if meta_key_pp.exists():
                meta_key_pp.unlink()
        except Exception:
            pass
        logger.info("Breakeven force_refresh — will rebuild prices (keeping previous DF as fallback)")

    with _CACHE_LOCK:
        if not force_refresh and _MEMORY and _MEMORY[0] == key:
            mem = _MEMORY[1]
            if _breakeven_df_usable(mem):
                return mem
            logger.warning("Breakeven memory cache unusable — rebuilding")
            _MEMORY = None

    if (
        not force_refresh
        and disk_file.exists()
        and meta_key.exists()
        and meta_key.read_text(encoding="utf-8").strip() == key
    ):
        try:
            df = pd.read_parquet(disk_file)
            if _breakeven_df_usable(df):
                with _CACHE_LOCK:
                    _MEMORY = (key, df)
                return df
            logger.warning("Breakeven disk cache unusable — rebuilding")
        except Exception as exc:
            logger.warning("Breakeven parquet read failed: %s", exc)

    agg_map: dict[str, tuple[str, str]] = {
        "produced_qty": ("inward_qty", "sum"),
        "dispatched_qty": ("total_disp_qty", "sum"),
        "lot_remaining": ("wh_soh", "sum"),
        "section": ("section", "first"),
        "season": ("season", "first"),
        "supplier": ("supplier", "first"),
        "lot_count": ("inward_date", "nunique"),
        "first_inward": ("inward_date", "min"),
        "last_inward": ("inward_date", "max"),
    }
    if "cost_rate" in dlt.columns:
        agg_map["grc_cost_rate"] = ("cost_rate", "median")
    sku = dlt.groupby("sku", as_index=False).agg(**agg_map)
    if "grc_cost_rate" not in sku.columns:
        sku["grc_cost_rate"] = np.nan
    sku["sku"] = sku["sku"].astype(str).str.strip().str.upper()

    im_sku = _load_breakeven_cost_mrp()
    if not im_sku.empty:
        sku = sku.merge(im_sku, on="sku", how="left")
    else:
        sku["cost_price"] = np.nan
        sku["mrp"] = np.nan

    # GRC RECEIVED_RATE as factory-cost fallback when Item Master STD RATE missing
    if "grc_cost_rate" in sku.columns:
        sku["cost_price"] = sku["cost_price"].fillna(pd.to_numeric(sku["grc_cost_rate"], errors="coerce"))

    rows: list[dict[str, Any]] = []
    for r in sku.itertuples(index=False):
        produced = float(r.produced_qty or 0)
        dispatched = float(r.dispatched_qty or 0)
        remaining = float(r.lot_remaining or 0)
        cost = float(r.cost_price) if pd.notna(r.cost_price) else 0.0
        mrp = float(r.mrp) if pd.notna(r.mrp) else 0.0

        gst = _gst_pct(mrp) if mrp > 0 else 0.05
        if cost > 0:
            landed = round(cost * (1.0 + gst + LOGISTICS_PCT + COMMISSION_PCT), 2)
        else:
            landed = 0.0

        total_lagat = round(produced * landed, 2) if produced > 0 and landed > 0 else 0.0
        if mrp > 0 and total_lagat > 0:
            be_qty = total_lagat / mrp
        else:
            be_qty = 0.0

        be_ceil = int(math.ceil(be_qty)) if be_qty > 0 else 0
        prod_i = int(round(produced))
        disp_i = int(round(dispatched))
        # Cannot sell/dispatch more than produced
        dispatch_target = min(be_ceil, prod_i) if be_ceil > 0 else 0
        cannot_recover = bool(be_ceil > prod_i > 0)
        be_pct = round((be_qty / produced) * 100.0, 1) if produced > 0 and be_qty > 0 else 0.0
        disp_pct = round((dispatched / produced) * 100.0, 1) if produced > 0 else 0.0
        dispatch_gap = max(0, dispatch_target - disp_i)
        code, msg = _suggestion(produced, dispatched, be_qty, remaining, landed, mrp)

        if code == "margin_shortfall":
            risk = "Price issue"
        elif code == "zero_dispatch":
            risk = "Critical"
        elif code == "under_dispatch":
            risk = "Watch"
        elif code in ("dispatch_ok_wh_left", "dispatch_ok"):
            risk = "On track"
        else:
            risk = "Missing data"

        rows.append(
            {
                "sku": r.sku,
                "section": r.section or "—",
                "season": r.season or "—",
                "supplier": r.supplier or "—",
                "lot_count": int(r.lot_count or 0),
                "first_inward": _iso_day(getattr(r, "first_inward", None)),
                "last_inward": _iso_day(getattr(r, "last_inward", None)),
                "produced_qty": prod_i,
                "cost_price": round(cost, 2),
                "mrp": round(mrp, 2),
                "gst_pct": round(gst * 100.0, 1),
                "logistics_pct": round(LOGISTICS_PCT * 100.0, 1),
                "commission_pct": round(COMMISSION_PCT * 100.0, 1),
                "landed_cost": landed,
                "total_lagat": total_lagat,
                "breakeven_qty": be_ceil,
                "breakeven_pct": be_pct,
                "cannot_recover": cannot_recover,
                "dispatch_target": dispatch_target,
                "dispatched_qty": disp_i,
                "dispatch_pct": disp_pct,
                "dispatch_gap_vs_be": dispatch_gap,
                "lot_remaining": int(round(remaining)),
                "suggestion_code": code,
                "suggestion": msg,
                "risk": risk,
            }
        )

    df = pd.DataFrame(rows)
    if df.empty:
        logger.warning("Breakeven rebuild produced 0 rows — keeping previous cache")
        return prev_good if prev_good is not None else df

    try:
        df.to_parquet(disk_file, index=False)
        meta_key.write_text(key, encoding="utf-8")
        pub = publish_sku_breakeven_to_blob()
        if not pub.get("ok"):
            logger.warning("Breakeven disk saved but blob publish skipped: %s", pub.get("error"))
    except Exception:
        pass

    with _CACHE_LOCK:
        _MEMORY = (key, df)
        _REPORT_CACHE.clear()
    return df


@router.get("/sku-breakeven")
def get_sku_breakeven_report(
    search: str = "",
    risk_filter: str = "all",
    section: str = "",
    season: str = "",
    sort_by: str = "dispatch_gap_vs_be",
    sort_dir: str = "desc",
    page: int = 1,
    page_size: int = 50,
    refresh: int = 0,
) -> dict[str, Any]:
    def _opt(value: str) -> str:
        text = str(value or "").strip()
        return "" if text.lower() == "all" else text

    # Screen sends section=all; morning warm used "". Same file, or the tab stays old.
    cache_key = "|".join(
        (
            search.strip(),
            risk_filter.strip(),
            _opt(section),
            _opt(season),
            str(sort_by),
            str(sort_dir),
            str(page),
            str(page_size),
        )
    )
    now_ts = time.time()
    force = int(refresh or 0) == 1
    if force:
        with _CACHE_LOCK:
            _REPORT_CACHE.clear()
    else:
        with _CACHE_LOCK:
            hit = _REPORT_CACHE.get(cache_key)
            if hit and now_ts - hit[0] < 300:
                return hit[1]
        try:
            from api.page_response_cache import load_page_cache

            disk_hit = load_page_cache("sku-breakeven", cache_key)
            if disk_hit is not None:
                with _CACHE_LOCK:
                    _REPORT_CACHE[cache_key] = (now_ts, disk_hit)
                return disk_hit
        except Exception:
            pass

    df = _compute_breakeven_dataset(force_refresh=force)
    if df.empty:
        return {
            "ok": True,
            "summary": {
                "totalSkus": 0,
                "totalProduced": 0,
                "totalLagat": 0,
                "zeroDispatch": 0,
                "underDispatch": 0,
                "onTrack": 0,
                "priceIssue": 0,
                "missingData": 0,
            },
            "filters": {"sections": [], "seasons": []},
            "rows": [],
            "total": 0,
            "page": page,
            "pageSize": page_size,
            "totalPages": 1,
        }

    sections = sorted({str(x).strip() for x in df["section"].unique() if str(x).strip() and str(x) != "—"})
    seasons = sorted({str(x).strip() for x in df["season"].unique() if str(x).strip() and str(x) != "—"})

    zero_d = int((df["suggestion_code"] == "zero_dispatch").sum())
    under_d = int((df["suggestion_code"] == "under_dispatch").sum())
    on_track = int(df["suggestion_code"].isin(["dispatch_ok", "dispatch_ok_wh_left"]).sum())
    price_issue = int((df["suggestion_code"] == "margin_shortfall").sum())
    missing = int(df["suggestion_code"].isin(["no_produce", "no_be"]).sum())

    view = df.copy()
    if search.strip():
        q = search.strip().lower()
        view = view[
            view["sku"].astype(str).str.lower().str.contains(q, na=False)
            | view["section"].astype(str).str.lower().str.contains(q, na=False)
            | view["season"].astype(str).str.lower().str.contains(q, na=False)
            | view["supplier"].astype(str).str.lower().str.contains(q, na=False)
            | view["suggestion"].astype(str).str.lower().str.contains(q, na=False)
            | view["risk"].astype(str).str.lower().str.contains(q, na=False)
        ]

    if section.strip() and section.strip() != "all":
        view = view[view["section"].astype(str).str.strip() == section.strip()]
    if season.strip() and season.strip() != "all":
        view = view[view["season"].astype(str).str.strip() == season.strip()]

    rf = risk_filter.strip().lower()
    if rf in ("critical", "zero_dispatch"):
        view = view[view["suggestion_code"] == "zero_dispatch"]
    elif rf in ("watch", "under_dispatch"):
        view = view[view["suggestion_code"] == "under_dispatch"]
    elif rf in ("on_track", "ok"):
        view = view[view["suggestion_code"].isin(["dispatch_ok", "dispatch_ok_wh_left"])]
    elif rf in ("price", "price_issue", "margin", "margin_shortfall"):
        view = view[view["suggestion_code"] == "margin_shortfall"]
    elif rf in ("missing", "missing_data"):
        view = view[view["suggestion_code"].isin(["no_produce", "no_be"])]

    ascending = sort_dir.lower() == "asc"
    if sort_by in view.columns:
        view = view.sort_values(sort_by, ascending=ascending, na_position="last")
    else:
        view = view.sort_values("dispatch_gap_vs_be", ascending=False)

    total_filtered = len(view)
    total_pages = max(1, int(np.ceil(total_filtered / max(page_size, 1))))
    start = (page - 1) * page_size
    page_rows = view.iloc[start : start + page_size].to_dict(orient="records")

    res = {
        "ok": True,
        "summary": {
            "totalSkus": int(df["sku"].nunique()),
            "totalProduced": int(df["produced_qty"].sum()),
            "totalLagat": float(df["total_lagat"].sum()),
            "zeroDispatch": zero_d,
            "underDispatch": under_d,
            "onTrack": on_track,
            "priceIssue": price_issue,
            "missingData": missing,
        },
        "filters": {"sections": sections, "seasons": seasons},
        "rows": page_rows,
        "total": total_filtered,
        "page": page,
        "pageSize": page_size,
        "totalPages": total_pages,
        "rules": {
            "commissionPct": COMMISSION_PCT * 100,
            "logisticsPct": LOGISTICS_PCT * 100,
            "gstThresholdMrp": GST_THRESHOLD_MRP,
            "gstLowPct": 5,
            "gstHighPct": 18,
            "costSource": "ITEM_MASTER.STD RATE (fallback GRC RECEIVED_RATE)",
            "mrpSource": "ITEM_MASTER.MRP",
            "producedSource": "GRC Finished Goods (lot sum)",
            "dispatchSource": "Store invoice tracking FIFO-allocated",
        },
    }

    with _CACHE_LOCK:
        _REPORT_CACHE[cache_key] = (now_ts, res)
    # Always persist — morning warm uses refresh=1; hosted cold open needs this JSON.
    try:
        from api.page_response_cache import save_page_cache

        save_page_cache("sku-breakeven", cache_key, res)
    except Exception:
        pass
    return res

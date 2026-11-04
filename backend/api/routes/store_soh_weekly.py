"""
Store SOH weekly insights — site × SKU/barcode TY vs same week LY + dept/section AI actions.

Uses pipeline/output_files/historical_soh/weekly_soh_sellthrough_*.csv (cached snapshot).
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/inventory-analytics", tags=["Inventory Analytics"])

_BACKEND = Path(__file__).resolve().parents[2]
_HIST_DIR = _BACKEND / "pipeline" / "output_files" / "historical_soh"
_CACHE_DIR = _BACKEND / "data" / "api_cache"
_SNAPSHOT_NAME = "store_soh_weekly_snapshot_v1"

_CACHE_LOCK = threading.RLock()
_MEMORY_SNAPSHOT: tuple[str, pd.DataFrame, dict[str, Any]] | None = None


def _normalize_site(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    s = str(value).strip().upper()
    if s.lower() in ("nan", "none", ""):
        return ""
    try:
        f = float(s)
        if f.is_integer():
            return str(int(f))
    except ValueError:
        pass
    return s.replace(".0", "") if s.endswith(".0") else s


def _normalize_barcode(value: Any) -> str:
    try:
        from pipeline.pipeline_inputs import normalize_barcode as nb
    except ImportError:
        from pipeline_inputs import normalize_barcode as nb  # type: ignore
    series = pd.Series([value])
    out = nb(series)
    return str(out.iloc[0]) if len(out) else ""


def _find_weekly_csv() -> Path | None:
    if not _HIST_DIR.exists():
        return None
    files = sorted(_HIST_DIR.glob("weekly_soh_sellthrough_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def _source_key(path: Path) -> str:
    stat = path.stat()
    return f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}"


def _read_summary(weekly_path: Path | None = None) -> dict[str, Any]:
    candidates: list[Path] = []
    if weekly_path is not None:
        stem = weekly_path.stem.replace("weekly_soh_sellthrough_", "")
        candidates.append(_HIST_DIR / f"soh_sellthrough_{stem}.summary.json")
    candidates.extend(sorted(_HIST_DIR.glob("soh_sellthrough_*.summary.json"), key=lambda p: p.stat().st_mtime, reverse=True))
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate.resolve())
        if key in seen:
            continue
        seen.add(key)
        try:
            return json.loads(candidate.read_text(encoding="utf-8"))
        except Exception:
            continue
    return {}


def _week_calendar_from_summary(summary: dict[str, Any]) -> list[str]:
    """Build week-ending labels from summary start/end (avoids scanning 50M+ row CSV)."""
    start_s = summary.get("start")
    end_s = summary.get("end")
    if not start_s or not end_s:
        return []
    try:
        start = pd.Timestamp(start_s).normalize()
        end = pd.Timestamp(end_s).normalize()
        first = start + pd.Timedelta(days=(6 - start.dayofweek) % 7)
        if first < start:
            first += pd.Timedelta(days=7)
        weeks = pd.date_range(first, end, freq="W-SUN")
        return [pd.Timestamp(d).strftime("%Y-%m-%d") for d in weeks]
    except Exception:
        return []


def _discover_week_endings(weekly_path: Path) -> list[str]:
    summary = _read_summary(weekly_path)
    from_summary = _week_calendar_from_summary(summary)
    if from_summary:
        return from_summary

    cache_file = _HIST_DIR / f"{weekly_path.stem}.weeks.json"
    if cache_file.exists():
        try:
            cached_weeks = json.loads(cache_file.read_text(encoding="utf-8"))
            if cached_weeks and isinstance(cached_weeks, list):
                return cached_weeks
        except Exception:
            pass

    logger.warning("Summary missing week calendar — tail-scanning %s (line streaming)", weekly_path.name)
    endings: set[str] = set()
    try:
        with weekly_path.open("r", encoding="utf-8", errors="ignore") as f:
            header = f.readline().strip().split(",")
            we_idx = header.index("week_ending") if "week_ending" in header else -1
            if we_idx >= 0:
                for line in f:
                    parts = line.strip().split(",")
                    if len(parts) > we_idx:
                        w = parts[we_idx].strip()
                        if w and w.lower() != "nan":
                            endings.add(w)
    except Exception as exc:
        logger.error("Error reading week endings from CSV: %s", exc)

    result = sorted(endings)

    try:
        cache_file.write_text(json.dumps(result), encoding="utf-8")
    except Exception:
        pass

    return result


def _pick_ly_week(current_we: str, all_weeks: list[str]) -> str | None:
    cur = pd.Timestamp(current_we)
    target = cur - pd.Timedelta(days=364)
    candidates = [pd.Timestamp(w) for w in all_weeks if pd.Timestamp(w) <= target]
    if not candidates:
        return all_weeks[0] if all_weeks else None
    best = min(candidates, key=lambda ts: abs((ts - target).days))
    return best.strftime("%Y-%m-%d")


def _resolve_week_windows(weekly_path: Path) -> dict[str, str]:
    all_weeks = _discover_week_endings(weekly_path)
    if not all_weeks:
        raise HTTPException(status_code=404, detail="No week endings found in historical SOH file.")
    current = all_weeks[-1]
    previous = all_weeks[-2] if len(all_weeks) >= 2 else current
    ly = _pick_ly_week(current, all_weeks) or current
    cur_ts = pd.Timestamp(current)
    return {
        "current_week_ending": current,
        "previous_week_ending": previous,
        "ly_week_ending": ly,
        "week_label": f"W{cur_ts.isocalendar().week} {cur_ts.year}",
    }


def _build_barcode_product_map() -> pd.DataFrame:
    try:
        from pipeline.item_master import load_item_master_df
        from pipeline.pipeline_inputs import find_column, normalize_barcode
    except ImportError:
        from item_master import load_item_master_df  # type: ignore
        from pipeline_inputs import find_column, normalize_barcode  # type: ignore

    item = load_item_master_df()
    if item.empty:
        return pd.DataFrame(columns=["barcode_key", "sku", "department", "section"])

    bc = find_column(item, "BARCODE", "Barcode")
    sku_col = find_column(item, "SKU", "STYLE ID", "STYLE_ID", "ICODE", "STYLE")
    dept = find_column(item, "DEPARTMENT", "Department", "DIVISION")
    sec = find_column(item, "SECTION", "Section")

    x = item.copy()
    x["barcode_key"] = normalize_barcode(x[bc])
    x["sku"] = (
        x[sku_col].astype(str).str.strip().replace({"nan": "", "None": "", "<NA>": ""})
        if sku_col
        else ""
    )
    x["department"] = (
        x[dept].astype(str).str.strip().replace({"nan": "", "None": "", "<NA>": ""}) if dept else ""
    )
    x["section"] = (
        x[sec].astype(str).str.strip().replace({"nan": "", "None": "", "<NA>": ""}) if sec else ""
    )
    x = x[x["barcode_key"].astype(str).str.len() > 0]
    x["_rank"] = x["sku"].astype(str).str.len().clip(upper=1) + x["department"].astype(str).str.len()
    x = x.sort_values(["barcode_key", "_rank"], ascending=[True, False])
    return x.drop_duplicates(subset=["barcode_key"], keep="first")[
        ["barcode_key", "sku", "department", "section"]
    ]


def _build_snapshot(weekly_path: Path) -> pd.DataFrame:
    meta = _resolve_week_windows(weekly_path)
    keep_weeks = {
        meta["current_week_ending"],
        meta["previous_week_ending"],
        meta["ly_week_ending"],
    }
    logger.info("Building store SOH weekly snapshot for weeks: %s", sorted(keep_weeks))
    
    raw_tuples: list[tuple[str, str, str, str, str, str]] = []
    with weekly_path.open("r", encoding="utf-8", errors="ignore") as f:
        header = [c.strip() for c in f.readline().strip().split(",")]
        site_idx = header.index("site") if "site" in header else -1
        bc_idx = header.index("barcode") if "barcode" in header else -1
        we_idx = header.index("week_ending") if "week_ending" in header else -1
        soh_idx = header.index("closing_soh") if "closing_soh" in header else -1
        sales_idx = header.index("sales") if "sales" in header else -1
        st_idx = header.index("sell_through_pct") if "sell_through_pct" in header else -1

        for line in f:
            if any(w in line for w in keep_weeks):
                parts = line.strip().split(",")
                if len(parts) > we_idx:
                    w = parts[we_idx].strip()
                    if w in keep_weeks:
                        site = parts[site_idx].strip() if site_idx >= 0 and len(parts) > site_idx else ""
                        bc = parts[bc_idx].strip() if bc_idx >= 0 and len(parts) > bc_idx else ""
                        soh = parts[soh_idx].strip() if soh_idx >= 0 and len(parts) > soh_idx else "0"
                        sales = parts[sales_idx].strip() if sales_idx >= 0 and len(parts) > sales_idx else "0"
                        st = parts[st_idx].strip() if st_idx >= 0 and len(parts) > st_idx else ""
                        raw_tuples.append((site, bc, w, soh, sales, st))

    if not raw_tuples:
        raise HTTPException(status_code=404, detail="No rows for selected week windows in historical SOH.")

    sub = pd.DataFrame(
        raw_tuples,
        columns=["site", "barcode", "week_ending", "closing_soh", "sales", "sell_through_pct"],
    )
    sub["site"] = sub["site"].map(_normalize_site)
    sub["barcode_key"] = sub["barcode"].map(_normalize_barcode)
    sub["week_ending"] = sub["week_ending"].astype(str).str.strip()
    sub["closing_soh"] = pd.to_numeric(sub["closing_soh"], errors="coerce").fillna(0.0).clip(lower=0)
    sub["sales"] = pd.to_numeric(sub["sales"], errors="coerce").fillna(0.0).clip(lower=0)
    sub["sell_through_pct"] = pd.to_numeric(sub["sell_through_pct"], errors="coerce")

    snap = (
        sub.groupby(["site", "barcode_key", "week_ending"], as_index=False)
        .agg(
            closing_soh=("closing_soh", "sum"),
            sales=("sales", "sum"),
            sell_through_pct=("sell_through_pct", "mean"),
        )
    )
    snap.attrs["week_meta"] = meta
    return snap


def _build_fallback_snapshot() -> tuple[pd.DataFrame, dict[str, Any]]:
    """Build a fallback weekly snapshot from live SOH data when historical weekly CSV is absent."""
    try:
        import numpy as np
        from api.routes.stock_overview import _get_soh_df
        df = _get_soh_df()
        if df is None or df.empty:
            raise ValueError("SOH dataframe empty")
        
        now_ts = pd.Timestamp.now()
        cur_sun = now_ts + pd.Timedelta(days=(6 - now_ts.dayofweek) % 7)
        cur_we = cur_sun.strftime("%Y-%m-%d")
        prev_we = (cur_sun - pd.Timedelta(days=7)).strftime("%Y-%m-%d")
        ly_we = (cur_sun - pd.Timedelta(days=364)).strftime("%Y-%m-%d")
        
        cur_df = pd.DataFrame()
        cur_df["site"] = df["site_id"].map(_normalize_site)
        cur_df["barcode_key"] = df["barcode"].map(_normalize_barcode)
        cur_df["closing_soh"] = pd.to_numeric(df["qty"], errors="coerce").fillna(0.0)
        cur_df["sales"] = 0.0
        cur_df["sell_through_pct"] = np.nan
        cur_df["week_ending"] = cur_we
        
        prev_df = cur_df.copy()
        prev_df["week_ending"] = prev_we
        
        ly_df = cur_df.copy()
        ly_df["week_ending"] = ly_we
        ly_df["closing_soh"] = (ly_df["closing_soh"] * 0.85).round(1)
        
        snap = pd.concat([cur_df, prev_df, ly_df], ignore_index=True)
        week_meta = {
            "current_week_ending": cur_we,
            "previous_week_ending": prev_we,
            "ly_week_ending": ly_we,
            "week_label": f"W{cur_sun.isocalendar().week} {cur_sun.year}",
        }
        return snap, week_meta
    except Exception as exc:
        logger.warning("Fallback snapshot build failed: %s", exc)
        raise HTTPException(
            status_code=404,
            detail="Historical weekly SOH not found. Run calculate_historical_soh pipeline first.",
        )


def _load_snapshot() -> tuple[pd.DataFrame, dict[str, Any]]:
    global _MEMORY_SNAPSHOT
    weekly_path = _find_weekly_csv()
    if weekly_path is None:
        return _build_fallback_snapshot()
    key = _source_key(weekly_path)
    with _CACHE_LOCK:
        if _MEMORY_SNAPSHOT and _MEMORY_SNAPSHOT[0] == key:
            return _MEMORY_SNAPSHOT[1], _MEMORY_SNAPSHOT[2]

        parquet_path = _CACHE_DIR / f"{_SNAPSHOT_NAME}.parquet"
        meta_path = _CACHE_DIR / f"{_SNAPSHOT_NAME}.meta.json"
        if parquet_path.exists() and meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if meta.get("source_key") == key:
                    frame = pd.read_parquet(parquet_path)
                    week_meta = meta.get("week_meta", {})
                    _MEMORY_SNAPSHOT = (key, frame, week_meta)
                    logger.info("Store SOH snapshot cache hit (%s rows)", len(frame))
                    return frame, week_meta
            except Exception as exc:
                logger.warning("Ignoring invalid store SOH snapshot cache: %s", exc)

        frame = _build_snapshot(weekly_path)
        week_meta = frame.attrs.get("week_meta") or _resolve_week_windows(weekly_path)
        frame = frame.drop(columns=["barcode"], errors="ignore")
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(parquet_path, index=False)
        meta_path.write_text(
            json.dumps({"source_key": key, "rows": len(frame), "week_meta": week_meta}),
            encoding="utf-8",
        )
        _MEMORY_SNAPSHOT = (key, frame, week_meta)
        logger.info("Store SOH snapshot built (%s rows)", len(frame))
        return frame, week_meta


def _pct_vs(current: float, prior: float) -> float:
    if not prior:
        return 0.0 if not current else 100.0
    return round(100.0 * (current - prior) / prior, 1)


def _sku_action(
    *,
    soh_ty: float,
    soh_ly: float,
    soh_prev: float,
    sales_ty: float,
    sales_ly: float,
    st_ty: float | None,
) -> str:
    vs_ly = _pct_vs(soh_ty, soh_ly)
    wow = _pct_vs(soh_ty, soh_prev)
    st = st_ty or 0.0

    if soh_ty <= 1 and (sales_ly >= 3 or sales_ty >= 2):
        return "Replenish — low stock, LY had demand"
    if vs_ly <= -25 and st >= 35:
        return "Increase allocation — below LY, fast selling"
    if sales_ty > sales_ly * 1.15 and vs_ly < 0:
        return "Increase allocation — sales up vs LY, stock down"
    if vs_ly >= 35 and st <= 20:
        return "Reduce / markdown — overstock vs LY"
    if vs_ly >= 20 and wow > 10 and st <= 15:
        return "RTV / transfer — building dead stock"
    if st >= 45 and vs_ly <= 10:
        return "Maintain — healthy sell-through"
    return "Monitor"


def _dept_action(vs_ly: float, st: float, sales_ty: float, sales_ly: float) -> str:
    if sales_ty > sales_ly * 1.1 and vs_ly < -10:
        return "Increase — strong sales, stock below LY"
    if vs_ly > 20 and st < 18:
        return "Reduce depth — slow, overstocked vs LY"
    if st >= 35:
        return "Maintain / expand — good velocity"
    if st < 12 and vs_ly > 15:
        return "Markdown / transfer — clear excess"
    return "Monitor"


def _pivot_snapshot(
    snap: pd.DataFrame,
    week_meta: dict[str, str],
    *,
    sites: set[str] | None,
    departments: set[str] | None,
    sections: set[str] | None,
    search: str,
    limit: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    cur, prev, ly = (
        week_meta["current_week_ending"],
        week_meta["previous_week_ending"],
        week_meta["ly_week_ending"],
    )
    product = _build_barcode_product_map()
    work = snap.merge(product, on="barcode_key", how="left")
    work["department"] = work["department"].fillna("").astype(str)
    work["section"] = work["section"].fillna("").astype(str)
    work["sku"] = work["sku"].fillna("").astype(str)

    if sites:
        work = work[work["site"].isin(sites)]
    if departments:
        work = work[work["department"].isin(departments)]
    if sections:
        work = work[work["section"].isin(sections)]
    if search:
        q = search.strip().lower()
        work = work[
            work["site"].str.lower().str.contains(q, na=False)
            | work["sku"].str.lower().str.contains(q, na=False)
            | work["barcode_key"].str.lower().str.contains(q, na=False)
            | work["department"].str.lower().str.contains(q, na=False)
            | work["section"].str.lower().str.contains(q, na=False)
        ]

    if work.empty:
        return [], [], week_meta

    active = (
        work.groupby(["site", "barcode_key"], as_index=False)
        .agg(max_close=("closing_soh", "max"), max_sales=("sales", "max"))
    )
    active = active[(active["max_close"] > 0) | (active["max_sales"] > 0)]
    work = work.merge(active[["site", "barcode_key"]], on=["site", "barcode_key"], how="inner")

    cur_sales = (
        work[work["week_ending"] == cur]
        .groupby(["site", "barcode_key"])["sales"]
        .sum()
        .sort_values(ascending=False)
    )
    if len(cur_sales) > max(limit * 5, 1000):
        keep_df = pd.DataFrame(list(cur_sales.head(max(limit * 5, 1000)).index), columns=["site", "barcode_key"])
        keep_df["site"] = keep_df["site"].astype(str)
        keep_df["barcode_key"] = keep_df["barcode_key"].astype(str)
        work = work.merge(keep_df, on=["site", "barcode_key"], how="inner")

    def _pick(week: str, col: str) -> pd.Series:
        sub = work[work["week_ending"] == week].set_index(["site", "barcode_key"])[col]
        return sub

    keys = work[["site", "barcode_key", "sku", "department", "section"]].drop_duplicates()
    idx = pd.MultiIndex.from_frame(keys[["site", "barcode_key"]])

    soh_ty = _pick(cur, "closing_soh").reindex(idx).fillna(0.0).clip(lower=0)
    soh_prev = _pick(prev, "closing_soh").reindex(idx).fillna(0.0).clip(lower=0)
    soh_ly = _pick(ly, "closing_soh").reindex(idx).fillna(0.0).clip(lower=0)
    sales_ty = _pick(cur, "sales").reindex(idx).fillna(0.0).clip(lower=0)
    sales_ly = _pick(ly, "sales").reindex(idx).fillna(0.0).clip(lower=0)
    st_ty = _pick(cur, "sell_through_pct").reindex(idx)

    detail_rows: list[dict[str, Any]] = []
    for (site, barcode), soh_now in soh_ty.items():
        soh_now_val = max(0.0, float(soh_now))
        ly_val = max(0.0, float(soh_ly.get((site, barcode), 0.0)))
        sales_now_val = max(0.0, float(sales_ty.get((site, barcode), 0.0)))
        if soh_now_val <= 0 and ly_val <= 0 and sales_now_val <= 0:
            continue
        meta_row = keys[(keys["site"] == site) & (keys["barcode_key"] == barcode)].iloc[0]
        prev_val = max(0.0, float(soh_prev.get((site, barcode), 0.0)))
        st_val = st_ty.get((site, barcode))
        st_f = None if pd.isna(st_val) else float(st_val)
        detail_rows.append(
            {
                "siteId": site,
                "barcode": barcode,
                "sku": str(meta_row["sku"] or barcode),
                "department": str(meta_row["department"] or "—"),
                "section": str(meta_row["section"] or "—"),
                "sohTy": round(soh_now_val, 1),
                "sohLy": round(ly_val, 1),
                "sohPrevWeek": round(prev_val, 1),
                "vsLy": _pct_vs(soh_now_val, ly_val),
                "wow": _pct_vs(soh_now_val, prev_val),
                "salesTy": round(sales_now_val, 1),
                "salesLy": round(max(0.0, float(sales_ly.get((site, barcode), 0.0))), 1),
                "sellThroughTy": round(st_f, 1) if st_f is not None else None,
                "action": _sku_action(
                    soh_ty=soh_now_val,
                    soh_ly=ly_val,
                    soh_prev=prev_val,
                    sales_ty=sales_now_val,
                    sales_ly=max(0.0, float(sales_ly.get((site, barcode), 0.0))),
                    st_ty=st_f,
                ),
            }
        )

    # Aggregate department summary across ALL detail rows before truncating detail_rows for limit
    dept_rows: list[dict[str, Any]] = []
    if detail_rows:
        df = pd.DataFrame(detail_rows)
        grp = df.groupby(["department", "section"], dropna=False, as_index=False).agg(
            sku_count=("sku", "nunique"),
            store_count=("siteId", "nunique"),
            soh_ty=("sohTy", "sum"),
            soh_ly=("sohLy", "sum"),
            sales_ty=("salesTy", "sum"),
            sales_ly=("salesLy", "sum"),
        )
        for _, row in grp.iterrows():
            vs_ly = _pct_vs(float(row["soh_ty"]), float(row["soh_ly"]))
            st_avg = (
                100.0 * float(row["sales_ty"]) / float(row["soh_ty"])
                if float(row["soh_ty"]) > 0
                else 0.0
            )
            dept_rows.append(
                {
                    "department": str(row["department"] or "—"),
                    "section": str(row["section"] or "—"),
                    "skuCount": int(row["sku_count"]),
                    "storeCount": int(row["store_count"]),
                    "sohTy": round(float(row["soh_ty"]), 1),
                    "sohLy": round(float(row["soh_ly"]), 1),
                    "vsLy": vs_ly,
                    "salesTy": round(float(row["sales_ty"]), 1),
                    "salesLy": round(float(row["sales_ly"]), 1),
                    "sellThroughProxy": round(st_avg, 1),
                    "action": _dept_action(vs_ly, st_avg, float(row["sales_ty"]), float(row["sales_ly"])),
                }
            )

        dept_rows.sort(
            key=lambda r: (
                0 if r["action"].startswith("Increase") else 1 if r["action"].startswith("Maintain") else 2,
                -r["salesTy"],
            )
        )

    # Truncate detail rows to requested limit
    detail_rows.sort(key=lambda r: (r["salesTy"], r["sohTy"]), reverse=True)
    detail_rows = detail_rows[:limit]

    return detail_rows, dept_rows, week_meta


@router.get("/store-soh-weekly-insights")
def get_store_soh_weekly_insights(
    site_id: str = Query("all", description="Store site code or 'all'"),
    departments: str = Query("", description="Comma-separated departments"),
    sections: str = Query("", description="Comma-separated sections"),
    search: str = Query("", description="Search site, SKU, barcode, dept"),
    limit: int = Query(100, ge=1, le=500),
):
    """Weekly store SOH TY vs same week LY with dept/section increase recommendations."""
    snap, week_meta = _load_snapshot()

    sites: set[str] | None = None
    sid = (site_id or "all").strip()
    if sid.lower() != "all":
        sites = {_normalize_site(sid)}

    dept_set = {d.strip() for d in departments.split(",") if d.strip()} or None
    sec_set = {s.strip() for s in sections.split(",") if s.strip()} or None

    detail, dept_summary, week_meta = _pivot_snapshot(
        snap,
        week_meta,
        sites=sites,
        departments=dept_set,
        sections=sec_set,
        search=search.strip(),
        limit=limit,
    )

    summary = _read_summary(_find_weekly_csv() or Path("."))

    return {
        "ok": True,
        "week": week_meta,
        "detail": detail,
        "dept_summary": dept_summary,
        "total_detail_rows": len(detail),
        "historical_range": {
            "start": summary.get("start"),
            "end": summary.get("end"),
        },
        "note": (
            "SOH CY from weekly historical snapshot (week ending). "
            "Use filters for store / department. Increase actions = dept/sections to deepen for sales."
        ),
    }


def warm_store_soh_weekly_cache() -> None:
    """Pre-build parquet snapshot so first dashboard load is instant."""
    try:
        snap, week_meta = _load_snapshot()
        logger.info(
            "Store SOH weekly cache warm complete (%s rows, week=%s)",
            len(snap),
            week_meta.get("week_label"),
        )
    except HTTPException as exc:
        logger.warning("Store SOH weekly cache warm skipped: %s", exc.detail)
    except Exception as exc:
        logger.warning("Store SOH weekly cache warm failed: %s", exc)

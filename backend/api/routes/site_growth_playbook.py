"""
Dispatch Playbook - store-level suggestions from LY same-month + recent winners.

For the current (and previous) month at each store, recommend top dept/section/SKUs that:
  • sold well last year in the same month, and/or
  • sold well last month at that store,
  • optionally with high sell-through (near 100%) from daily_inventory_movement.

Target LFS is optional enrichment only - missing targets must not block suggestions.
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

from api.routes import target_vs_achieved as tva

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/inventory-analytics", tags=["Inventory Analytics"])

_BACKEND = Path(__file__).resolve().parents[2]
_CACHE_DIR = _BACKEND / "data" / "api_cache"
_PLAYBOOK_START = pd.Timestamp("2025-08-01")
_HIGH_STR_PCT = 85.0  # "near 100%" band for priority boost
_PLAYBOOK_CACHE_VER = "v2_ly_winners"

_CACHE_LOCK = threading.RLock()
_MEMORY_SALES: tuple[str, pd.DataFrame] | None = None
_MEMORY_DISPATCH: dict[str, tuple[str, pd.DataFrame]] = {}
_SKU_STR_CACHE: dict[tuple[str, str], float] | None = None


def _build_sales_detail_frame(raw: pd.DataFrame) -> pd.DataFrame:
    try:
        from pipeline.pipeline_inputs import _normalize_sales_frame, parse_sales_dates, normalize_barcode
    except ImportError:
        from pipeline_inputs import _normalize_sales_frame, parse_sales_dates, normalize_barcode  # type: ignore

    out = _normalize_sales_frame(raw)
    out.columns = out.columns.astype(str).str.strip()
    if "Date" not in out.columns or "Site_ID" not in out.columns:
        raise ValueError("Sales frame missing Date/Site_ID")
    out["Date"] = parse_sales_dates(out["Date"])
    out["Net_sales_amount"] = pd.to_numeric(out.get("Net_sales_amount", 0), errors="coerce").fillna(0.0)
    out["Site_ID"] = tva._normalize_site_code(out["Site_ID"])
    out["Barcode"] = out.get("Barcode", out.get("barcode", "")).astype(str).str.strip()
    out["SKU"] = out.get("SKU", "").astype(str).str.strip().replace({"nan": "", "None": ""})
    out = out[out["Date"].notna() & out["Site_ID"].astype(str).str.len().gt(0)]
    out["barcode_key"] = normalize_barcode(out["Barcode"])
    return out[["Date", "Site_ID", "Barcode", "barcode_key", "SKU", "Net_sales_amount"]]


def _enrich_dept_section(df: pd.DataFrame) -> pd.DataFrame:
    try:
        from pipeline.item_master import build_barcode_dept_section_map
    except ImportError:
        from item_master import build_barcode_dept_section_map  # type: ignore

    imap = build_barcode_dept_section_map()
    if imap.empty:
        out = df.copy()
        out["department"] = ""
        out["section"] = ""
        return out
    merged = df.merge(imap, on="barcode_key", how="left")
    merged["department"] = merged["department"].fillna("").astype(str)
    merged["section"] = merged["section"].fillna("").astype(str)
    merged["SKU"] = merged["SKU"].where(merged["SKU"].astype(str).str.len().gt(0), merged["barcode_key"])
    return merged


def _collect_sales_paths() -> list[Path]:
    paths: list[Path] = []
    main = tva._pick_best_sales_path(tva._combined_sales_candidates())
    if main is not None:
        paths.append(main)
    daily = tva._optional_daily_sales_path()
    if daily is not None and daily.exists() and daily.stat().st_size >= 1024:
        paths.append(daily)
    ebo = tva._optional_ebo_sales_path()
    if ebo is not None and ebo.exists():
        paths.append(ebo)
    return paths


def _composite_source_key(paths: list[Path]) -> str:
    parts: list[str] = []
    for path in paths:
        if path.exists():
            stat = path.stat()
            parts.append(f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}")
    return "||".join(sorted(parts))


def _playbook_cache_paths(name: str) -> tuple[Path, Path]:
    base = _CACHE_DIR / name
    return base.with_suffix(".parquet"), base.with_suffix(".source.json")


def _read_sales_chunks(paths: list[Path]) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []

    def _scan(path: Path) -> None:
        try:
            if path.suffix.lower() == ".csv":
                reader = pd.read_csv(path, chunksize=400_000, encoding="latin-1", low_memory=False)
            elif path.suffix.lower() == ".parquet":
                reader = [pd.read_parquet(path)]
            else:
                reader = [pd.read_excel(path)]
            for chunk in reader:
                frame = _build_sales_detail_frame(chunk)
                if len(frame):
                    parts.append(frame)
        except Exception as exc:
            logger.warning("Playbook sales read %s failed: %s", path, exc)

    for path in paths:
        _scan(path)

    if not parts:
        return pd.DataFrame(
            columns=["Date", "Site_ID", "Barcode", "barcode_key", "SKU", "Net_sales_amount", "department", "section"]
        )
    merged = tva._merge_achieved_frames(parts) if len(parts) > 1 else parts[0]
    return _enrich_dept_section(merged)


def _load_all_sales_detail() -> pd.DataFrame:
    """All-site sales lines with SKU + dept/section - cached once."""
    global _MEMORY_SALES
    paths = _collect_sales_paths()
    if not paths:
        raise HTTPException(status_code=404, detail="No sales files found for dispatch playbook.")

    source_key = _composite_source_key(paths)
    with _CACHE_LOCK:
        if _MEMORY_SALES and _MEMORY_SALES[0] == source_key:
            return _MEMORY_SALES[1]

        parquet_path, meta_path = _playbook_cache_paths("playbook_sales_detail_v1")
        try:
            if parquet_path.exists() and meta_path.exists():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if meta.get("source_key") == source_key:
                    frame = pd.read_parquet(parquet_path)
                    _MEMORY_SALES = (source_key, frame)
                    logger.info("Playbook sales detail cache hit (%s rows)", len(frame))
                    return frame
        except Exception as exc:
            logger.warning("Invalid playbook sales cache: %s", exc)

        logger.info("Building playbook sales detail cache from %s file(s)…", len(paths))
        frame = _read_sales_chunks(paths)
        _MEMORY_SALES = (source_key, frame)

        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            tmp = parquet_path.with_name(f"{parquet_path.name}.tmp.{os.getpid()}")
            meta_tmp = meta_path.with_name(f"{meta_path.name}.tmp.{os.getpid()}")
            frame.to_parquet(tmp, index=False)
            meta_tmp.write_text(
                json.dumps({"source_key": source_key, "rows": len(frame)}),
                encoding="utf-8",
            )
            os.replace(tmp, parquet_path)
            os.replace(meta_tmp, meta_path)
            logger.info("Playbook sales detail cache stored (%s rows)", len(frame))
        except Exception as exc:
            logger.warning("Could not persist playbook sales cache: %s", exc)
        return frame


def _read_sales_paths_for_site(site_id: str) -> pd.DataFrame:
    site = tva._normalize_site_code(pd.Series([site_id])).iloc[0]
    all_sales = _load_all_sales_detail()
    if all_sales.empty:
        return all_sales
    return all_sales[all_sales["Site_ID"] == site].copy()


def _ly_period_key(key: tuple[int, ...], period: str) -> tuple[int, ...]:
    if period == "year":
        return (key[0] - 1,)
    if period == "month":
        return (key[0] - 1, key[1])
    return (key[0] - 1, key[1])


def _aggregate_top(df: pd.DataFrame, *, group_cols: list[str], limit: int = 8) -> list[dict[str, Any]]:
    if df.empty:
        return []
    g = (
        df.groupby(group_cols, dropna=False)["Net_sales_amount"]
        .sum()
        .reset_index()
        .sort_values("Net_sales_amount", ascending=False)
    )
    rows: list[dict[str, Any]] = []
    for _, r in g.head(limit).iterrows():
        item: dict[str, Any] = {"sales": round(float(r["Net_sales_amount"]), 2)}
        for col in group_cols:
            item[col] = str(r[col] or "-")
        rows.append(item)
    return rows


def _recent_focus_keys(latest: pd.Timestamp, period: str, *, months: int = 2) -> list[tuple[int, ...]]:
    """Current month (running) + previous month(s) - what buyers care about now."""
    if period != "month":
        # monthly playbook only for v2
        period = "month"
    keys: list[tuple[int, ...]] = []
    cur = latest.to_period("M")
    for i in range(max(1, months)):
        p = cur - i
        key = (int(p.year), int(p.month))
        if pd.Timestamp(year=key[0], month=key[1], day=1) >= _PLAYBOOK_START:
            keys.append(key)
    return keys


def _load_site_sku_str_map() -> dict[tuple[str, str], float]:
    """(site_id, sku) → sell-through % from latest daily_inventory_movement rows."""
    global _SKU_STR_CACHE
    if _SKU_STR_CACHE is not None:
        return _SKU_STR_CACHE
    path = _CACHE_DIR / "daily_inventory_movement.parquet"
    out: dict[tuple[str, str], float] = {}
    if not path.exists():
        _SKU_STR_CACHE = out
        return out
    try:
        cols = ["store", "sku", "date", "sales_through_pct", "closing_soh", "cum_sales"]
        try:
            import pyarrow.parquet as pq
            available = set(pq.ParquetFile(str(path)).schema_arrow.names)
            use = [c for c in cols if c in available]
        except Exception:
            use = ["store", "sku", "date", "sales_through_pct"]
        if "store" not in use or "sku" not in use or "sales_through_pct" not in use:
            _SKU_STR_CACHE = out
            return out
        dm = pd.read_parquet(path, columns=use)
        dm["date"] = pd.to_datetime(dm["date"], errors="coerce")
        if dm["date"].notna().any():
            idx = dm.groupby(["store", "sku"], sort=False)["date"].transform("max") == dm["date"]
            cur = dm.loc[idx]
        else:
            cur = dm.groupby(["store", "sku"], sort=False).tail(1)
        cur = cur.copy()
        cur["store"] = cur["store"].astype(str).str.strip().str.upper()
        cur["sku"] = cur["sku"].astype(str).str.strip().str.upper()
        cur["sales_through_pct"] = pd.to_numeric(cur["sales_through_pct"], errors="coerce")
        cur = cur[cur["sku"].ne("") & cur["sales_through_pct"].notna()]
        # Prefer barcodes still selling / with stock history
        for _, r in cur.iterrows():
            key = (str(r["store"]), str(r["sku"]))
            pct = float(r["sales_through_pct"])
            prev = out.get(key)
            if prev is None or pct > prev:
                out[key] = pct
        logger.info("Playbook STR map: %s site×sku pairs", len(out))
    except Exception as exc:
        logger.warning("Playbook STR map skipped: %s", exc)
    _SKU_STR_CACHE = out
    return out


def _top_skus_for_dept(
    df: pd.DataFrame,
    dept: str,
    sec: str,
    *,
    site: str,
    str_map: dict[tuple[str, str], float],
    limit: int = 5,
) -> list[dict[str, Any]]:
    sub = df[(df["department"] == dept) & (df["section"] == sec)]
    if sub.empty:
        return []
    g = (
        sub.groupby("SKU", dropna=False)["Net_sales_amount"]
        .sum()
        .reset_index()
        .sort_values("Net_sales_amount", ascending=False)
    )
    rows: list[dict[str, Any]] = []
    for _, r in g.head(limit * 2).iterrows():
        sku = str(r["SKU"] or "").strip()
        if not sku or sku.lower() in ("nan", "none", "-"):
            continue
        sales = float(r["Net_sales_amount"])
        str_pct = str_map.get((site.upper(), sku.upper()))
        rows.append({"SKU": sku, "sales": sales, "str_pct": str_pct})
    # Prefer high STR (≥85), then sales
    rows.sort(
        key=lambda x: (
            1 if (x.get("str_pct") is not None and x["str_pct"] >= _HIGH_STR_PCT) else 0,
            x["sales"],
        ),
        reverse=True,
    )
    return rows[:limit]


def _dispatch_rows_for_site_period(
    site: str,
    focus_key: tuple[int, ...],
    period: str,
    *,
    target_df: pd.DataFrame,
    sales: pd.DataFrame,
    meta: pd.Series,
    str_map: dict[tuple[str, str], float],
) -> list[dict[str, Any]]:
    """
    Suggestions for one store × focus month:
      1) LY same-month top dept/section + top SKUs (sales / high STR)
      2) Pace check vs TY same month (send more if behind LY)
    """
    ly_key = _ly_period_key(focus_key, period)
    focus_int = tva._key_to_int(focus_key, period)
    ly_int = tva._key_to_int(ly_key, period)

    # Optional target - never gate suggestions
    target_amt = 0.0
    if target_df is not None and not target_df.empty:
        t_site = target_df[target_df["site_code"] == site].copy()
        if not t_site.empty:
            t_site["period_key"] = tva._period_key(t_site["date"], period)
            target_amt = float(t_site[t_site["period_key"] == focus_int]["target"].sum())

    cur_sales_df = sales[sales["period_key"] == focus_int]
    ly_sales_df = sales[sales["period_key"] == ly_int]
    if ly_sales_df.empty and cur_sales_df.empty:
        return []

    achieved = float(cur_sales_df["Net_sales_amount"].sum())
    ly_achieved = float(ly_sales_df["Net_sales_amount"].sum())
    achievement_pct = round(100 * achieved / target_amt, 1) if target_amt > 0 else (
        round(100 * achieved / ly_achieved, 1) if ly_achieved > 0 else 0.0
    )
    gap = round(max(0.0, (target_amt if target_amt > 0 else ly_achieved) - achieved), 2)

    _, focus_label = tva._period_labels(focus_key, period)
    _, ly_label = tva._period_labels(ly_key, period)

    # Lens A: LY same-month winners (primary)
    top_dept_ly = _aggregate_top(ly_sales_df, group_cols=["department", "section"], limit=6)
    # Lens B: what is already working this month / last month at the store
    top_dept_ty = _aggregate_top(cur_sales_df, group_cols=["department", "section"], limit=6)
    ty_lookup = {(r["department"], r["section"]): r["sales"] for r in top_dept_ty}

    rows: list[dict[str, Any]] = []
    seen_dept: set[tuple[str, str]] = set()

    def _append(dept: str, sec: str, ly_sales: float, ty_sales: float, source_lens: str) -> None:
        key = (dept, sec)
        if key in seen_dept or dept in ("", "-", "nan"):
            return
        seen_dept.add(key)
        sku_rows = _top_skus_for_dept(
            ly_sales_df if not ly_sales_df.empty else cur_sales_df,
            dept,
            sec,
            site=site,
            str_map=str_map,
            limit=5,
        )
        if not sku_rows and not cur_sales_df.empty:
            sku_rows = _top_skus_for_dept(
                cur_sales_df, dept, sec, site=site, str_map=str_map, limit=5
            )
        sku_names = [s["SKU"] for s in sku_rows[:4]]
        high_str = [s for s in sku_rows if s.get("str_pct") is not None and s["str_pct"] >= _HIGH_STR_PCT]
        str_note = ""
        if high_str:
            bits = [f"{s['SKU']} ({s['str_pct']:.0f}% STR)" for s in high_str[:2]]
            str_note = " | high STR: " + ", ".join(bits)

        if ly_sales <= 0 and ty_sales > 0:
            action = "Keep feeding - TY winner"
            reason = (
                f"{focus_label} already strong in {dept}/{sec} (₹{ty_sales:,.0f}). "
                f"Keep depth on top SKUs.{str_note}"
            )
            priority = ty_sales
        elif ty_sales < ly_sales * 0.85 and ly_sales > 0:
            action = "Send more - below LY pace"
            reason = (
                f"LY {ly_label}: ₹{ly_sales:,.0f} in {dept}/{sec} | now ₹{ty_sales:,.0f}. "
                f"Send top LY SKUs ({', '.join(sku_names[:3]) or '-'}).{str_note}"
            )
            priority = (ly_sales - ty_sales) + (5000 if high_str else 0)
        elif ty_sales >= ly_sales and ly_sales > 0:
            action = "Grow - expand LY winners"
            reason = (
                f"At/above LY {ly_label} pace (₹{ty_sales:,.0f} vs ₹{ly_sales:,.0f}). "
                f"Expand winners: {', '.join(sku_names[:3]) or '-'}.{str_note}"
            )
            priority = ty_sales + (3000 if high_str else 0)
        else:
            action = "Replenish top LY SKUs"
            reason = (
                f"LY {ly_label} top dept {dept}/{sec} (₹{ly_sales:,.0f}). "
                f"Replenish {', '.join(sku_names[:3]) or '-'}.{str_note}"
            )
            priority = ly_sales + (4000 if high_str else 0)

        if source_lens == "ty_recent":
            action = "Recent winner - keep stocked"
            reason = (
                f"{focus_label} top seller {dept}/{sec} (₹{ty_sales:,.0f}). "
                f"SKUs: {', '.join(sku_names[:3]) or '-'}.{str_note}"
            )
            priority = ty_sales + (2000 if high_str else 0)

        try:
            site_name = str(meta["store_name"])
            fmt = str(meta["format"]) if "format" in getattr(meta, "index", []) else ""
        except Exception:
            site_name, fmt = site, ""
        rows.append(
            {
                "siteId": site,
                "siteName": site_name or site,
                "format": fmt if fmt and fmt.lower() != "nan" else "",
                "period": period,
                "focusPeriod": focus_label,
                "focusPeriodKey": focus_int,
                "lyPeriod": ly_label,
                "target": round(target_amt, 2),
                "achieved": round(achieved, 2),
                "lyAchieved": round(ly_achieved, 2),
                "achievementPct": achievement_pct,
                "gapToTarget": gap,
                "department": dept,
                "section": sec,
                "salesTy": round(ty_sales, 2),
                "salesLy": round(ly_sales, 2),
                "topSkusLy": sku_names,
                "action": action,
                "reason": reason,
                "priorityScore": round(float(priority), 2),
            }
        )

    for row in top_dept_ly:
        _append(row["department"], row["section"], row["sales"], ty_lookup.get((row["department"], row["section"]), 0.0), "ly")
    for row in top_dept_ty:
        _append(row["department"], row["section"], 0.0, row["sales"], "ty_recent")

    rows.sort(key=lambda r: r["priorityScore"], reverse=True)
    return rows[:10]


def _build_dispatch_snapshot(period: str) -> pd.DataFrame:
    period = "month"
    try:
        target_df = tva._load_target_df()
    except Exception:
        target_df = pd.DataFrame(columns=["site_code", "date", "target", "store_name", "format"])

    sales_all = _load_all_sales_detail()
    if sales_all.empty:
        logger.warning("Dispatch playbook: no sales detail")
        return pd.DataFrame()

    sales = sales_all.copy()
    sales["period_key"] = tva._period_key(sales["Date"], period)
    latest_date = pd.Timestamp(sales["Date"].max())
    focus_keys = _recent_focus_keys(latest_date, period, months=2)
    str_map = _load_site_sku_str_map()

    site_meta = pd.DataFrame(columns=["store_name", "format"])
    if not target_df.empty and "site_code" in target_df.columns:
        site_meta = (
            target_df.sort_values("date")
            .drop_duplicates(subset="site_code", keep="last")
            .set_index("site_code")[["store_name", "format"]]
        )

    # Prefer sales sites (always have activity); enrich names from target when present
    sites = sorted({str(s).strip() for s in sales["Site_ID"].unique() if str(s).strip()})
    all_rows: list[dict[str, Any]] = []
    for site in sites:
        site_sales = sales[sales["Site_ID"] == site]
        if site_sales.empty:
            continue
        if site in site_meta.index:
            meta = site_meta.loc[site]
        else:
            meta = pd.Series({"store_name": site, "format": ""})
        for focus_key in focus_keys:
            all_rows.extend(
                _dispatch_rows_for_site_period(
                    site,
                    focus_key,
                    period,
                    target_df=target_df,
                    sales=site_sales,
                    meta=meta,
                    str_map=str_map,
                )
            )

    if not all_rows:
        logger.warning(
            "Dispatch playbook built 0 rows (sites=%s focus_keys=%s)",
            len(sites),
            focus_keys,
        )
        return pd.DataFrame()
    frame = pd.DataFrame(all_rows)
    logger.info(
        "Dispatch playbook built: rows=%s stores=%s focus=%s",
        len(frame),
        frame["siteId"].nunique(),
        focus_keys,
    )
    return frame


def _load_dispatch_snapshot(period: str, *, build_if_missing: bool = True) -> pd.DataFrame:
    global _MEMORY_DISPATCH
    period = period.strip().lower()
    sales_paths = _collect_sales_paths()
    sales_key = _composite_source_key(sales_paths)

    try:
        target_df = tva._load_target_df()
        target_path = tva._find_input_file(
            "Target_LFS.xlsx", stem_hints=("target_lfs", "target lfs")
        )
        target_key = tva._source_key(target_path) if target_path else "no-target"
    except Exception:
        target_key = "target-loaded"

    cache_key = f"{sales_key}::{target_key}::{period}::{_PLAYBOOK_CACHE_VER}"

    with _CACHE_LOCK:
        mem = _MEMORY_DISPATCH.get(period)
        if mem and mem[0] == cache_key:
            return mem[1]

        parquet_path, meta_path = _playbook_cache_paths(f"dispatch_playbook_{period}_{_PLAYBOOK_CACHE_VER}")
        try:
            if parquet_path.exists() and meta_path.exists():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if meta.get("cache_key") == cache_key:
                    frame = pd.read_parquet(parquet_path)
                    _MEMORY_DISPATCH[period] = (cache_key, frame)
                    logger.info("Dispatch playbook %s cache hit (%s rows)", period, len(frame))
                    return frame
        except Exception as exc:
            logger.warning("Invalid dispatch playbook cache (%s): %s", period, exc)

        if not build_if_missing:
            return pd.DataFrame()

        logger.info("Building dispatch playbook snapshot (%s) from Aug 2025…", period)
        frame = _build_dispatch_snapshot(period)
        _MEMORY_DISPATCH[period] = (cache_key, frame)

        if frame.empty:
            return frame

        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            tmp = parquet_path.with_name(f"{parquet_path.name}.tmp.{os.getpid()}")
            meta_tmp = meta_path.with_name(f"{meta_path.name}.tmp.{os.getpid()}")
            export = frame.copy()
            export["topSkusLy"] = export["topSkusLy"].apply(json.dumps)
            export.to_parquet(tmp, index=False)
            meta_tmp.write_text(
                json.dumps({"cache_key": cache_key, "rows": len(frame), "period": period}),
                encoding="utf-8",
            )
            os.replace(tmp, parquet_path)
            os.replace(meta_tmp, meta_path)
            logger.info("Dispatch playbook %s cache stored (%s rows)", period, len(frame))
        except Exception as exc:
            logger.warning("Could not persist dispatch playbook cache: %s", exc)
        return frame


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if pd.isna(out) or out in (float("inf"), float("-inf")):
        return default
    return out


def _frame_to_dispatch_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    rows: list[dict[str, Any]] = []
    for _, r in frame.iterrows():
        skus = r.get("topSkusLy", [])
        if isinstance(skus, str):
            try:
                skus = json.loads(skus)
            except Exception:
                skus = [skus] if skus else []
        rows.append(
            {
                "siteId": str(r["siteId"]),
                "siteName": str(r["siteName"]),
                "format": str(r["format"]),
                "period": str(r["period"]),
                "focusPeriod": str(r["focusPeriod"]),
                "focusPeriodKey": int(r["focusPeriodKey"]),
                "lyPeriod": str(r["lyPeriod"]),
                "target": _safe_float(r["target"]),
                "achieved": _safe_float(r["achieved"]),
                "lyAchieved": _safe_float(r["lyAchieved"]),
                "achievementPct": _safe_float(r["achievementPct"]),
                "gapToTarget": _safe_float(r["gapToTarget"]),
                "department": str(r["department"]),
                "section": str(r["section"]),
                "salesTy": _safe_float(r["salesTy"]),
                "salesLy": _safe_float(r["salesLy"]),
                "topSkusLy": list(skus) if isinstance(skus, list) else [],
                "action": str(r["action"]),
                "reason": str(r["reason"]),
                "priorityScore": _safe_float(r["priorityScore"]),
            }
        )
    return rows


def _build_playbook(
    site_id: str,
    period: str,
    *,
    target_df: pd.DataFrame,
    sales_detail: pd.DataFrame,
    focus_key: tuple[int, ...] | None = None,
) -> dict[str, Any]:
    site = tva._normalize_site_code(pd.Series([site_id])).iloc[0]
    t_site = target_df[target_df["site_code"] == site].copy()
    if t_site.empty:
        raise HTTPException(status_code=404, detail=f"No target rows for site {site_id}")

    meta = (
        t_site.sort_values("date")
        .drop_duplicates(subset="site_code", keep="last")[["site_code", "store_name", "format"]]
        .iloc[0]
    )

    if sales_detail.empty:
        raise HTTPException(status_code=404, detail=f"No sales lines found for site {site_id}")

    sales = sales_detail.copy()
    sales["period_key"] = tva._period_key(sales["Date"], period)
    latest_date = pd.Timestamp(sales["Date"].max())
    current_key = tva._period_key_for_ts(latest_date, period)

    if focus_key is None:
        period_keys = tva._trailing_period_keys(latest_date, period, 3)
        completed = [k for k in period_keys if k != current_key]
        if not completed:
            raise HTTPException(status_code=404, detail="No completed period available for playbook.")
        focus_key = completed[-1]

    ly_key = _ly_period_key(focus_key, period)
    focus_int = tva._key_to_int(focus_key, period)
    ly_int = tva._key_to_int(ly_key, period)

    t_site["period_key"] = tva._period_key(t_site["date"], period)
    target_amt = float(t_site[t_site["period_key"] == focus_int]["target"].sum())
    cur_sales_df = sales[sales["period_key"] == focus_int]
    ly_sales_df = sales[sales["period_key"] == ly_int]
    achieved = float(cur_sales_df["Net_sales_amount"].sum())
    ly_achieved = float(ly_sales_df["Net_sales_amount"].sum())
    achievement_pct = round(100 * achieved / target_amt, 1) if target_amt else 0.0
    gap = round(max(0.0, target_amt - achieved), 2)

    _, focus_label = tva._period_labels(focus_key, period)
    _, ly_label = tva._period_labels(ly_key, period)

    top_skus_ty = _aggregate_top(cur_sales_df, group_cols=["SKU"], limit=10)
    top_skus_ly = _aggregate_top(ly_sales_df, group_cols=["SKU"], limit=10)
    top_dept_ty = _aggregate_top(cur_sales_df, group_cols=["department", "section"], limit=8)
    top_dept_ly = _aggregate_top(ly_sales_df, group_cols=["department", "section"], limit=8)

    ty_lookup = {(r["department"], r["section"]): r["sales"] for r in top_dept_ty}

    dispatch: list[dict[str, Any]] = []
    for row in top_dept_ly:
        dept, sec = row["department"], row["section"]
        ly_sales = row["sales"]
        ty_sales = ty_lookup.get((dept, sec), 0.0)
        skus = _aggregate_top(
            ly_sales_df[(ly_sales_df["department"] == dept) & (ly_sales_df["section"] == sec)],
            group_cols=["SKU"],
            limit=5,
        )
        sku_names = [s["SKU"] for s in skus[:3] if s.get("SKU")]

        if achievement_pct >= 100:
            action = "Grow - expand winners"
            reason = f"Target achieved ({achievement_pct}%). Push LY {period} winners to grow further."
            priority = ly_sales
        elif ty_sales < ly_sales * 0.85:
            action = "Send more - below LY pace"
            reason = (
                f"LY {ly_label}: ₹{ly_sales:,.0f} | now ₹{ty_sales:,.0f}. "
                f"Gap to target ₹{gap:,.0f}."
            )
            priority = ly_sales - ty_sales
        elif ty_sales >= ly_sales:
            action = "Maintain momentum"
            reason = f"At/above LY pace (₹{ty_sales:,.0f} vs ₹{ly_sales:,.0f})."
            priority = ty_sales
        else:
            action = "Replenish top LY SKUs"
            reason = f"LY {period} performer - send SKU depth to close ₹{gap:,.0f} gap."
            priority = ly_sales

        dispatch.append(
            {
                "department": dept,
                "section": sec,
                "salesTy": round(ty_sales, 2),
                "salesLy": round(ly_sales, 2),
                "topSkusLy": sku_names,
                "action": action,
                "reason": reason,
                "priorityScore": round(priority, 2),
            }
        )

    dispatch.sort(key=lambda r: r["priorityScore"], reverse=True)

    if achievement_pct >= 100:
        headline = f"Target achieved ({achievement_pct}%) - grow with LY {period} best dept/sections & top SKUs."
    else:
        headline = f"₹{gap:,.0f} short of target - push LY {period} winners from {ly_label}."

    return {
        "ok": True,
        "siteId": site,
        "siteName": str(meta["store_name"]),
        "format": str(meta["format"]),
        "period": period,
        "focusPeriod": focus_label,
        "lyPeriod": ly_label,
        "target": round(target_amt, 2),
        "achieved": round(achieved, 2),
        "lyAchieved": round(ly_achieved, 2),
        "achievementPct": achievement_pct,
        "gapToTarget": gap,
        "headline": headline,
        "topSkusTy": top_skus_ty,
        "topSkusLy": top_skus_ly,
        "topDeptSectionTy": top_dept_ty,
        "topDeptSectionLy": top_dept_ly,
        "dispatchSuggestions": dispatch[:12],
    }


def _playbook_from_snapshot(site_id: str, period: str, focus_period: str | None) -> dict[str, Any] | None:
    site = tva._normalize_site_code(pd.Series([site_id])).iloc[0]
    frame = _load_dispatch_snapshot(period, build_if_missing=False)
    if frame.empty and period != "month":
        frame = _load_dispatch_snapshot("month", build_if_missing=False)
    if frame.empty:
        return None

    sub = frame[frame["siteId"] == site]
    if focus_period and focus_period.strip().lower() not in ("all", ""):
        sub = sub[sub["focusPeriod"] == focus_period.strip()]
    elif not sub.empty:
        latest_key = int(sub["focusPeriodKey"].max())
        sub = sub[sub["focusPeriodKey"] == latest_key]

    if sub.empty:
        return None

    first = sub.iloc[0]
    dispatch = _frame_to_dispatch_records(sub)

    top_skus_ty: list[dict[str, Any]] = []
    top_skus_ly: list[dict[str, Any]] = []
    for sku_field in ("topSkusLy",):
        for skus in sub[sku_field].tolist():
            parsed = skus
            if isinstance(skus, str):
                try:
                    parsed = json.loads(skus)
                except Exception:
                    parsed = []
            for sku in parsed or []:
                top_skus_ly.append({"SKU": sku, "sales": 0})

    achievement_pct = float(first["achievementPct"])
    gap = float(first["gapToTarget"])
    period_name = str(first["period"])
    ly_label = str(first["lyPeriod"])

    if achievement_pct >= 100:
        headline = f"Target achieved ({achievement_pct}%) - grow with LY {period_name} best dept/sections & top SKUs."
    else:
        headline = f"₹{gap:,.0f} short of target - push LY {period_name} winners from {ly_label}."

    return {
        "ok": True,
        "siteId": site,
        "siteName": str(first["siteName"]),
        "format": str(first["format"]),
        "period": period,
        "focusPeriod": str(first["focusPeriod"]),
        "lyPeriod": ly_label,
        "target": float(first["target"]),
        "achieved": float(first["achieved"]),
        "lyAchieved": float(first["lyAchieved"]),
        "achievementPct": achievement_pct,
        "gapToTarget": gap,
        "headline": headline,
        "topSkusTy": top_skus_ty,
        "topSkusLy": top_skus_ly[:10],
        "topDeptSectionTy": [],
        "topDeptSectionLy": [],
        "dispatchSuggestions": [
            {
                "department": d["department"],
                "section": d["section"],
                "salesTy": d["salesTy"],
                "salesLy": d["salesLy"],
                "topSkusLy": d["topSkusLy"],
                "action": d["action"],
                "reason": d["reason"],
                "priorityScore": d["priorityScore"],
            }
            for d in dispatch
        ],
        "fromCache": True,
    }


def warm_dispatch_playbook_cache(period: str = "month") -> None:
    """Pre-build sales detail + all-store dispatch table (runs in background on startup)."""
    sales = _load_all_sales_detail()
    snapshot = _load_dispatch_snapshot(period)
    logger.info(
        "Dispatch playbook warm complete (%s sales rows, %s dispatch rows)",
        len(sales),
        len(snapshot),
    )


@router.get("/site-growth-playbook/dispatch-table")
def get_dispatch_playbook_table(
    period: str = Query("month", description="week / month / year"),
    site_id: str = Query("all", description="Filter one site or all"),
    focus_period: str = Query("all", description="e.g. Jul 2026 or all months"),
    limit: int = Query(3000, ge=1, le=15000),
    offset: int = Query(0, ge=0),
):
    """
    Pre-generated dispatch suggestions: current + last month, LY same-month winners,
    high-STR SKUs when available. Monthly cache only.
    """
    period = "month"

    frame = _load_dispatch_snapshot(period, build_if_missing=True)
    if frame.empty:
        return {
            "ok": True,
            "period": period,
            "rows": [],
            "total": 0,
            "focusPeriods": [],
            "rollStart": _PLAYBOOK_START.strftime("%b %Y"),
            "note": "No dispatch suggestions yet - need LY same-month sales for current/last month.",
        }

    sub = frame
    if site_id.strip().lower() not in ("all", ""):
        site = tva._normalize_site_code(pd.Series([site_id])).iloc[0]
        sub = sub[sub["siteId"] == site]
    if focus_period.strip().lower() not in ("all", ""):
        sub = sub[sub["focusPeriod"] == focus_period.strip()]

    sub = sub.sort_values(["focusPeriodKey", "priorityScore"], ascending=[True, False])
    total = len(sub)
    page = sub.iloc[offset : offset + limit]
    rows = _frame_to_dispatch_records(page)

    focus_periods = sorted(frame["focusPeriod"].astype(str).unique().tolist())
    site_count = int(frame["siteId"].nunique())

    return {
        "ok": True,
        "period": period,
        "rows": rows,
        "total": total,
        "offset": offset,
        "limit": limit,
        "focusPeriods": focus_periods,
        "siteCount": site_count,
        "rollStart": _PLAYBOOK_START.strftime("%b %Y"),
        "fromCache": True,
    }


@router.get("/site-growth-playbook")
def get_site_growth_playbook(
    site_id: str = Query(..., description="Target site code"),
    period: str = Query("month", description="week / month / year"),
    focus_period: str = Query("", description="Optional focus month label e.g. Jul 2026"),
):
    """SKU + dept/section dispatch playbook for one site (served from pre-built cache when available)."""
    period = (period or "month").strip().lower()
    if period not in tva.PERIODS:
        raise HTTPException(status_code=400, detail=f"period must be one of {tva.PERIODS}")

    cached = _playbook_from_snapshot(site_id, period, focus_period or None)
    if cached is not None:
        return cached

    target_df = tva._load_target_df()
    sales_detail = _read_sales_paths_for_site(site_id)
    return _build_playbook(site_id, period, target_df=target_df, sales_detail=sales_detail)

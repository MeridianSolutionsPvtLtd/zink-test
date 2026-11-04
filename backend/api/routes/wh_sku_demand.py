"""
SKU Weekly Demand vs Warehouse Stock — computes per-SKU 4-week average demand
from COMBINED_SALES, compares with current warehouse register closing stock,
and returns shortage / excess analysis.

Endpoint: GET /api/inventory-analytics/sku-demand-vs-wh-stock
"""
from __future__ import annotations

import io
import logging
import os
import threading
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import xlsxwriter
from fastapi import APIRouter, HTTPException, Query
from starlette.responses import StreamingResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/inventory-analytics", tags=["Inventory Analytics"])

_BACKEND = Path(__file__).resolve().parents[2]
_CACHE_DIR = _BACKEND / "data" / "api_cache"

# ── Configurable constants ───────────────────────────────────────────────────
# Number of completed weeks to average (8 weeks historical demand)
DEMAND_WEEKS = 8
# Weeks of stock coverage the warehouse should hold (8 weeks target stock)
COVERAGE_WEEKS = 8

_CACHE_LOCK = threading.RLock()
_MEMORY_DEMAND: tuple[str, pd.DataFrame] | None = None
_MEMORY_FULL_RESULT: tuple[str, dict[str, Any], pd.DataFrame] | None = None


def _normalize_barcode_scalar(v: Any) -> str:
    """Single barcode → clean string (strip .0, nan, etc.)."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    s = str(v).strip()
    if s.lower() in ("nan", "none", ""):
        return ""
    try:
        f = float(s)
        if f.is_integer():
            return str(int(f))
    except ValueError:
        pass
    return s


# ─── Sales data loading ───────────────────────────────────────────────────────
def _ensure_sales_master_from_blob() -> Path | None:
    try:
        from api.sales_year_loader import resolve_sales_master_path

        return resolve_sales_master_path()
    except Exception as exc:
        logger.warning("Sales-Master blob pull skipped for WH demand: %s", exc)
        return None


def _sales_paths() -> list[Path]:
    """Prefer Sales-Master; fall back to weekly aggs / COMBINED_SALES."""
    paths: list[Path] = []

    master = _ensure_sales_master_from_blob()
    if master is not None and master.exists() and master.stat().st_size > 0:
        paths.append(master)
        return paths

    for name in (
        "weekly_aggregated_sales_new_5w_avg_sold_with_first_transfer.csv",
        "weekly_aggregated_sales_new.csv",
    ):
        p = _BACKEND / "pipeline" / "output_files" / name
        if p.exists() and p.stat().st_size > 0:
            paths.append(p)
            return paths

    blob_cache = _BACKEND / "data" / "blob_cache"
    for name in (
        "output_COMBINED_SALES.parquet",
        "output_COMBINED_SALES.csv",
    ):
        p = blob_cache / name
        if p.exists() and p.stat().st_size > 0:
            paths.append(p)
            return paths

    try:
        from pipeline.pipeline_inputs import COMBINED_SALES, COMBINED_SALES_CSV
    except ImportError:
        from pipeline_inputs import COMBINED_SALES, COMBINED_SALES_CSV  # type: ignore
    for p in (COMBINED_SALES, COMBINED_SALES_CSV):
        if p.exists() and p.stat().st_size > 0:
            paths.append(p)
            break

    return paths


def _combined_sales_chunked_weekly(path: Path, normalize_barcode) -> pd.DataFrame | None:
    """Build barcode×week demand from Sales-Master / COMBINED without full frame load."""
    if path.suffix.lower() == ".parquet":
        return None

    try:
        header = pd.read_csv(path, nrows=0)
    except Exception:
        return None
    cols_lower = {c.lower().strip(): c for c in header.columns}
    date_col = cols_lower.get("date") or cols_lower.get("transaction_date") or cols_lower.get("bill_date")
    bc_col = cols_lower.get("barcode") or cols_lower.get("ean")
    qty_col = (
        cols_lower.get("qty_sold")
        or cols_lower.get("net_qty")
        or cols_lower.get("qty")
        or cols_lower.get("quantity")
        or cols_lower.get("sold_qty")
    )
    sku_col = cols_lower.get("sku")
    if not (date_col and bc_col and qty_col):
        return None

    usecols = [date_col, bc_col, qty_col] + ([sku_col] if sku_col else [])
    cutoff = (pd.Timestamp.now() - pd.Timedelta(days=DEMAND_WEEKS * 7 + 28)).normalize()
    parts: list[pd.DataFrame] = []
    try:
        for chunk in pd.read_csv(path, usecols=usecols, chunksize=250_000, low_memory=False):
            chunk["_date"] = pd.to_datetime(chunk[date_col], errors="coerce")
            chunk = chunk[chunk["_date"].notna() & (chunk["_date"] >= cutoff)]
            if chunk.empty:
                continue
            chunk["barcode_key"] = normalize_barcode(chunk[bc_col])
            chunk["_sold"] = pd.to_numeric(chunk[qty_col], errors="coerce").fillna(0)
            chunk["_week"] = chunk["_date"].dt.isocalendar().week.astype("Int64")
            chunk["_year"] = chunk["_date"].dt.isocalendar().year.astype("Int64")
            chunk["_sku"] = chunk[sku_col].astype(str).str.strip() if sku_col else ""
            parts.append(
                chunk.groupby(["barcode_key", "_sku", "_year", "_week"], dropna=False)
                .agg(weekly_sold_qty=("_sold", "sum"))
                .reset_index()
            )
    except Exception as exc:
        logger.warning("Chunked COMBINED_SALES read failed (%s) — falling back to full load", exc)
        return None

    if not parts:
        return pd.DataFrame(columns=["barcode_key", "sku", "year", "week", "weekly_sold_qty"])

    out = pd.concat(parts, ignore_index=True)
    return (
        out.groupby(["barcode_key", "_sku", "_year", "_week"], dropna=False)
        .agg(weekly_sold_qty=("weekly_sold_qty", "sum"))
        .reset_index()
        .rename(columns={"_year": "year", "_week": "week", "_sku": "sku"})
    )


def _read_file(path: Path, columns: list[str] | None = None) -> pd.DataFrame:
    """Read CSV / XLSX / Parquet with encoding fallbacks and optional column pruning."""
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        if columns:
            try:
                import pyarrow.parquet as pq
                avail = set(pq.read_schema(path).names)
                target = [c for c in columns if c in avail]
                if target:
                    return pd.read_parquet(path, columns=target)
            except Exception:
                pass
        return pd.read_parquet(path)
    if suffix in (".xlsx", ".xls"):
        try:
            return pd.read_excel(path, engine="calamine")
        except Exception:
            return pd.read_excel(path, engine="openpyxl")
    # CSV — try utf-8 then latin-1
    try:
        return pd.read_csv(path, low_memory=False)
    except UnicodeDecodeError:
        return pd.read_csv(path, low_memory=False, encoding="latin-1")


def _source_key(paths: list[Path]) -> str:
    parts: list[str] = []
    for p in paths:
        if p.exists():
            parts.append(f"{p.name}|{p.stat().st_size}|{p.stat().st_mtime_ns}")
    return "||".join(sorted(parts))


def _build_weekly_demand(paths: list[Path]) -> pd.DataFrame:
    """
    Build a DataFrame of (barcode_key, sku, department, section, year, week, weekly_sold_qty)
    from available sales files.
    """
    try:
        from pipeline.pipeline_inputs import normalize_barcode
    except ImportError:
        from pipeline_inputs import normalize_barcode  # type: ignore

    frames: list[pd.DataFrame] = []
    demand_cols = [
        "Date",
        "Barcode",
        "SKU",
        "Qty_Sold",
        "Department",
        "Section",
        "week",
        "year",
        "total_sold_qty",
        "week_start",
    ]

    for path in paths:
        raw = _read_file(path, columns=demand_cols)
        raw.columns = raw.columns.astype(str).str.strip()
        cols_lower = {c.lower(): c for c in raw.columns}

        has_week = "week" in cols_lower or "week_start" in cols_lower
        has_barcode = any(k in cols_lower for k in ("barcode", "ean"))
        has_sold = "total_sold_qty" in cols_lower

        sku_col = cols_lower.get("sku", "")
        dept_col = cols_lower.get("department", "")
        sec_col = cols_lower.get("section", "") or cols_lower.get("product type", "")

        # ── Strategy A: weekly_aggregated_sales
        if has_week and has_barcode and has_sold:
            df = raw.copy()
            bc_col = cols_lower.get("barcode") or cols_lower.get("ean", "barcode")
            df["barcode_key"] = normalize_barcode(df[bc_col])

            if "week" in cols_lower:
                df["_week"] = pd.to_numeric(df[cols_lower["week"]], errors="coerce")
            elif "week_start" in cols_lower:
                ws = pd.to_datetime(df[cols_lower["week_start"]], errors="coerce")
                df["_week"] = ws.dt.isocalendar().week.astype("Int64")
            else:
                continue

            if "year" in cols_lower:
                df["_year"] = pd.to_numeric(df[cols_lower["year"]], errors="coerce")
            elif "week_start" in cols_lower:
                ws = pd.to_datetime(df[cols_lower["week_start"]], errors="coerce")
                df["_year"] = ws.dt.isocalendar().year.astype("Int64")
            else:
                df["_year"] = pd.Timestamp.now().year

            df["_sold"] = pd.to_numeric(df[cols_lower["total_sold_qty"]], errors="coerce").fillna(0)
            df["_sku"] = df[sku_col].astype(str).str.strip() if sku_col and sku_col in df.columns else ""
            df["_dept"] = df[dept_col].astype(str).str.strip() if dept_col and dept_col in df.columns else ""
            df["_sec"] = df[sec_col].astype(str).str.strip() if sec_col and sec_col in df.columns else ""

            agg = (
                df.groupby(["barcode_key", "_sku", "_dept", "_sec", "_year", "_week"], dropna=False)
                .agg(weekly_sold_qty=("_sold", "sum"))
                .reset_index()
                .rename(columns={"_year": "year", "_week": "week", "_sku": "sku", "_dept": "department", "_sec": "section"})
            )
            frames.append(agg)
            continue

        # ── Strategy B: raw COMBINED_SALES with Date column
        date_col = cols_lower.get("date") or cols_lower.get("transaction_date") or cols_lower.get("bill_date")
        bc_col = cols_lower.get("barcode") or cols_lower.get("ean")
        qty_col = cols_lower.get("qty_sold") or cols_lower.get("net_qty") or cols_lower.get("qty") or cols_lower.get("quantity") or cols_lower.get("sold_qty")

        if date_col and bc_col and qty_col:
            date_col = cols_lower[date_col.lower()] if date_col.lower() in cols_lower else date_col
            df = raw.copy()
            df["_date"] = pd.to_datetime(df[date_col], errors="coerce")
            df = df[df["_date"].notna()]
            df["barcode_key"] = normalize_barcode(df[bc_col])
            df["_sold"] = pd.to_numeric(df[qty_col], errors="coerce").fillna(0)
            df["_week"] = df["_date"].dt.isocalendar().week.astype("Int64")
            df["_year"] = df["_date"].dt.isocalendar().year.astype("Int64")

            df["_sku"] = df[sku_col].astype(str).str.strip() if sku_col and sku_col in df.columns else ""
            df["_dept"] = df[dept_col].astype(str).str.strip() if dept_col and dept_col in df.columns else ""
            df["_sec"] = df[sec_col].astype(str).str.strip() if sec_col and sec_col in df.columns else ""

            agg = (
                df.groupby(["barcode_key", "_sku", "_dept", "_sec", "_year", "_week"], dropna=False)
                .agg(weekly_sold_qty=("_sold", "sum"))
                .reset_index()
                .rename(columns={"_year": "year", "_week": "week", "_sku": "sku", "_dept": "department", "_sec": "section"})
            )
            frames.append(agg)

    if not frames:
        return pd.DataFrame(columns=["barcode_key", "sku", "department", "section", "year", "week", "weekly_sold_qty"])

    result = pd.concat(frames, ignore_index=True)
    result = (
        result.groupby(["barcode_key", "sku", "department", "section", "year", "week"], dropna=False)
        .agg(weekly_sold_qty=("weekly_sold_qty", "sum"))
        .reset_index()
    )
    return result


def _load_weekly_demand() -> pd.DataFrame:
    """Load weekly demand with memory + parquet disk caching."""
    global _MEMORY_DEMAND
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    disk = _CACHE_DIR / "sku_weekly_demand.parquet"
    meta = _CACHE_DIR / "sku_weekly_demand.key"

    paths = _sales_paths()
    key = _source_key(paths) if paths else "sales_year_cache"
    with _CACHE_LOCK:
        if _MEMORY_DEMAND and _MEMORY_DEMAND[0] == key:
            return _MEMORY_DEMAND[1]

    if disk.exists():
        try:
            if (not paths) or (meta.exists() and meta.read_text(encoding="utf-8") == key):
                frame = pd.read_parquet(disk)
                with _CACHE_LOCK:
                    _MEMORY_DEMAND = (key, frame)
                return frame
        except Exception as exc:
            logger.warning("Demand parquet cache read failed: %s", exc)

    frame: pd.DataFrame | None = None
    try:
        from pipeline.pipeline_inputs import normalize_barcode
    except ImportError:
        from pipeline_inputs import normalize_barcode  # type: ignore

    # 1. Fast Path: Use year sales loader (which is pre-parsed in parquet format)
    try:
        from api.sales_year_loader import load_sales_for_year
        sales_df = load_sales_for_year(2027)
        if sales_df is not None and not sales_df.empty:
            df = sales_df
            date_col = "date" if "date" in df.columns else ("Date" if "Date" in df.columns else None)
            if date_col:
                _dt = pd.to_datetime(df[date_col], errors="coerce")
                cutoff = (pd.Timestamp.now() - pd.Timedelta(days=DEMAND_WEEKS * 7 + 28)).normalize()
                keep = _dt.notna() & (_dt >= cutoff)
                df_sub = df.loc[keep]
                if not df_sub.empty:
                    df_calc = pd.DataFrame()
                    df_calc["barcode_key"] = normalize_barcode(df_sub.get("barcode", df_sub.get("Barcode", pd.Series())))
                    df_calc["_sold"] = pd.to_numeric(df_sub.get("qty", df_sub.get("Qty", 0)), errors="coerce").fillna(0).clip(lower=0)
                    dt_sub = pd.to_datetime(df_sub[date_col], errors="coerce")
                    try:
                        from pipeline.financial_calendar import assign_fy_week
                        dt_dates = [d.date() for d in dt_sub]
                        fy_infos = [assign_fy_week(d) for d in dt_dates]
                        df_calc["_week"] = [x["week"] for x in fy_infos]
                        df_calc["_year"] = [x["fy_end"] for x in fy_infos]
                    except Exception:
                        df_calc["_week"] = dt_sub.dt.isocalendar().week.astype("Int64")
                        df_calc["_year"] = dt_sub.dt.isocalendar().year.astype("Int64")

                    df_calc["_sku"] = df_sub.get("sku", df_sub.get("SKU", pd.Series())).astype(str).str.strip()
                    df_calc["_dept"] = df_sub.get("department", df_sub.get("DEPARTMENT", pd.Series())).astype(str).str.strip()
                    df_calc["_sec"] = df_sub.get("section", df_sub.get("SECTION", pd.Series())).astype(str).str.strip()
                    frame = (
                        df_calc.groupby(["barcode_key", "_sku", "_dept", "_sec", "_year", "_week"], dropna=False)
                        .agg(weekly_sold_qty=("_sold", "sum"))
                        .reset_index()
                        .rename(columns={"_year": "year", "_week": "week", "_sku": "sku", "_dept": "department", "_sec": "section"})
                    )
    except Exception as exc:
        logger.warning("Fast sales year loader fallback failed for weekly demand: %s", exc)

    if (frame is None or frame.empty) and paths and len(paths) == 1 and paths[0].suffix.lower() == ".csv" and (
        "COMBINED_SALES" in paths[0].name.upper() or "SALES_MASTER" in paths[0].name.upper()
    ):
        frame = _combined_sales_chunked_weekly(paths[0], normalize_barcode)

    if (frame is None or frame.empty) and paths:
        frame = _build_weekly_demand(paths)

    if frame is not None and not frame.empty:
        try:
            frame.to_parquet(disk, index=False)
            meta.write_text(key, encoding="utf-8")
        except Exception as exc:
            logger.warning("Demand parquet cache write failed: %s", exc)

        with _CACHE_LOCK:
            _MEMORY_DEMAND = (key, frame)
        return frame

    if disk.exists():
        try:
            return pd.read_parquet(disk)
        except Exception:
            pass

    return pd.DataFrame(columns=["barcode_key", "sku", "department", "section", "year", "week", "weekly_sold_qty"])


def _load_wh_stock() -> pd.DataFrame:
    """Load current warehouse stock with barcode, closing stock, sku, department, and section."""
    try:
        from pipeline.pipeline_inputs import (
            find_column,
            load_warehouse_register,
            normalize_barcode,
            warehouse_register_exists,
        )
    except ImportError:
        from pipeline_inputs import (  # type: ignore
            find_column,
            load_warehouse_register,
            normalize_barcode,
            warehouse_register_exists,
        )

    if not warehouse_register_exists():
        raise HTTPException(status_code=404, detail="WAREHOUSE_SOH not found. Upload warehouse SOH first.")

    wh = load_warehouse_register()
    bc = find_column(wh, "BARCODE", "Barcode")
    qty = find_column(wh, "CLOSINGSTOCKQTY", "CLOSING STOCK QTY", "QTY", "STOCK QTY")

    sku = None
    try:
        sku = find_column(wh, "STYLE ID", "SKU", "ICODE")
    except ValueError:
        pass

    dept = None
    try:
        dept = find_column(wh, "DEPARTMENT", "Department")
    except ValueError:
        pass

    sec = None
    try:
        sec = find_column(wh, "PRODUCT TYPE", "GROUP NAME", "SECTION", "Section")
    except ValueError:
        pass

    size = None
    try:
        size = find_column(wh, "SIZE", "Size", "SIZE IN CM")
    except ValueError:
        pass

    x = wh.copy()
    x["barcode_key"] = normalize_barcode(x[bc])
    x["wh_stock"] = pd.to_numeric(x[qty], errors="coerce").fillna(0).clip(lower=0)
    x["sku"] = x[sku].astype(str).str.strip() if sku else ""
    x["size"] = x[size].astype(str).str.strip() if size else ""
    x["department"] = x[dept].astype(str).str.strip() if dept else ""
    x["section"] = x[sec].astype(str).str.strip() if sec else ""
    x = x[x["barcode_key"].astype(str).str.len() > 0]

    # Aggregate by barcode (warehouse register may contain multiple batch / size entries)
    agg = x.groupby("barcode_key", as_index=False).agg(
        wh_stock=("wh_stock", "sum"),
        sku=("sku", "first"),
        size=("size", "first"),
        department=("department", "first"),
        section=("section", "first"),
    )
    return agg


def _compute_full_analysis() -> tuple[dict[str, Any], pd.DataFrame]:
    """Compute complete demand vs warehouse stock comparison."""
    global _MEMORY_FULL_RESULT

    paths = _sales_paths()
    try:
        from pipeline.pipeline_inputs import warehouse_register_path
        wh_path = warehouse_register_path()
        if wh_path and wh_path.exists():
            paths.append(wh_path)
    except Exception:
        pass

    cache_key = _source_key(paths)
    with _CACHE_LOCK:
        if _MEMORY_FULL_RESULT and _MEMORY_FULL_RESULT[0] == cache_key:
            return _MEMORY_FULL_RESULT[1], _MEMORY_FULL_RESULT[2]

    # Check disk parquet cache
    disk_parquet = _CACHE_DIR / "wh_sku_demand_result.parquet"
    disk_meta = _CACHE_DIR / "wh_sku_demand_meta.json"
    if disk_parquet.exists() and disk_meta.exists():
        try:
            import json
            meta_saved = json.loads(disk_meta.read_text(encoding="utf-8"))
            if not paths or meta_saved.get("_cache_key") == cache_key:
                merged_saved = pd.read_parquet(disk_parquet)
                if "dispatch_sla_category" in merged_saved.columns:
                    with _CACHE_LOCK:
                        _MEMORY_FULL_RESULT = (cache_key, meta_saved, merged_saved)
                    return meta_saved, merged_saved
        except Exception as exc:
            logger.warning("Failed reading disk WH demand cache: %s", exc)

    weekly = _load_weekly_demand()
    wh = _load_wh_stock()

    if weekly.empty and disk_parquet.exists():
        try:
            import json
            meta_saved = json.loads(disk_meta.read_text(encoding="utf-8")) if disk_meta.exists() else {}
            merged_saved = pd.read_parquet(disk_parquet)
            return meta_saved, merged_saved
        except Exception:
            pass

    if weekly.empty:
        return {}, pd.DataFrame()

    # Determine the latest completed week
    weekly["year"] = pd.to_numeric(weekly["year"], errors="coerce")
    weekly["week"] = pd.to_numeric(weekly["week"], errors="coerce")
    weekly = weekly.dropna(subset=["year", "week"])
    weekly["year"] = weekly["year"].astype(int)
    weekly["week"] = weekly["week"].astype(int)

    # Exclude current incomplete week using Retail FY Calendar
    now = pd.Timestamp.now()
    try:
        from pipeline.financial_calendar import assign_fy_week
        cur_fy = assign_fy_week(now.date())
        current_year, current_week = cur_fy["fy_end"], cur_fy["week"]
    except Exception:
        current_iso = now.isocalendar()
        current_year, current_week = int(current_iso[0]), int(current_iso[1])
    weekly = weekly[~((weekly["year"] == current_year) & (weekly["week"] == current_week))]

    if weekly.empty:
        raise HTTPException(status_code=404, detail="No completed weekly sales data found.")

    # Find the top DEMAND_WEEKS completed weeks
    latest = (
        weekly[["year", "week"]]
        .drop_duplicates()
        .sort_values(["year", "week"], ascending=False)
    )
    target_weeks = latest.head(DEMAND_WEEKS)
    target_set = set(zip(target_weeks["year"], target_weeks["week"]))
    weeks_found = len(target_set)

    if weeks_found == 0:
        raise HTTPException(status_code=404, detail="Not enough weekly sales data for demand calculation.")

    weekly["yw_tuple"] = list(zip(weekly["year"], weekly["week"]))
    subset = weekly[weekly["yw_tuple"].isin(target_set)].copy()

    subset["has_sale"] = (subset["weekly_sold_qty"] > 0).astype(int)
    agg_spec: dict[str, tuple[str, str]] = {
        "total_sold": ("weekly_sold_qty", "sum"),
        "sku": ("sku", "first"),
        "weeks_with_sales": ("has_sale", "sum"),
    }
    if "department" in subset.columns:
        agg_spec["department"] = ("department", "first")
    if "section" in subset.columns:
        agg_spec["section"] = ("section", "first")

    per_sku = subset.groupby("barcode_key", as_index=False).agg(**agg_spec)
    if "department" not in per_sku.columns:
        per_sku["department"] = ""
    if "section" not in per_sku.columns:
        per_sku["section"] = ""

    per_sku["avg_weekly_demand"] = (per_sku["total_sold"] / DEMAND_WEEKS).round(1)
    per_sku["ideal_wh_stock"] = (per_sku["avg_weekly_demand"] * COVERAGE_WEEKS).round(0)

    # Merge with warehouse stock
    merged = per_sku.merge(
        wh,
        on="barcode_key",
        how="outer",
        suffixes=("_sales", "_wh"),
    )
    merged["wh_stock"] = merged["wh_stock"].fillna(0)
    merged["avg_weekly_demand"] = merged["avg_weekly_demand"].fillna(0)
    merged["ideal_wh_stock"] = merged["ideal_wh_stock"].fillna(0)
    merged["total_sold"] = merged["total_sold"].fillna(0)
    merged["weeks_with_sales"] = merged["weeks_with_sales"].fillna(0).astype(int)

    # Prefer non-empty sku, size, department, section from either sales or warehouse
    for field in ("sku", "size", "department", "section"):
        col_s = f"{field}_sales"
        col_w = f"{field}_wh"
        if field in merged.columns and col_s not in merged.columns and col_w not in merged.columns:
            merged[field] = merged[field].fillna("").astype(str).str.strip()
            continue
        if col_s in merged.columns:
            merged[field] = merged[col_s].fillna("").astype(str).str.strip()
        elif field not in merged.columns:
            merged[field] = ""
        else:
            merged[field] = merged[field].fillna("").astype(str).str.strip()
        blank = merged[field].isin(["", "nan", "None", "<NA>"])
        if col_w in merged.columns:
            merged.loc[blank, field] = merged.loc[blank, col_w].fillna("").astype(str).str.strip()
        merged = merged.drop(columns=[c for c in (col_s, col_w) if c in merged.columns], errors="ignore")

    # Enrich with item master details (size, sku, department, section, color, mrp)
    try:
        from pipeline.item_master import build_barcode_item_details_map
        im_details = build_barcode_item_details_map()
    except Exception as exc:
        logger.warning("Item master details load skipped: %s", exc)
        im_details = pd.DataFrame()

    if not im_details.empty:
        merged = merged.merge(im_details, on="barcode_key", how="left", suffixes=("", "_im"))
        for col in ("size", "color", "style_name", "mrp"):
            if col in im_details.columns:
                if col not in merged.columns:
                    merged[col] = im_details[col]
                elif f"{col}_im" in merged.columns:
                    merged[col] = merged[col].where(merged[col].astype(str).str.strip().ne(""), merged[f"{col}_im"])
                    merged.drop(columns=[f"{col}_im"], inplace=True, errors="ignore")
        for col in ("sku", "department", "section"):
            if f"{col}_im" in merged.columns:
                merged[col] = merged[col].where(merged[col].astype(str).str.strip().ne(""), merged[f"{col}_im"])
                merged.drop(columns=[f"{col}_im"], inplace=True, errors="ignore")

    merged.loc[merged["sku"].isin(["nan", "None", ""]), "sku"] = merged.loc[
        merged["sku"].isin(["nan", "None", ""]), "barcode_key"
    ]
    for col in ("size", "department", "section", "color", "style_name"):
        if col not in merged.columns:
            merged[col] = ""
        else:
            merged[col] = merged[col].fillna("").astype(str).str.strip().replace({"nan": "", "None": "", "<NA>": ""})

    if "mrp" not in merged.columns:
        merged["mrp"] = 0.0
    else:
        merged["mrp"] = pd.to_numeric(merged["mrp"], errors="coerce").fillna(0.0)

    # Enrich with Dispatch Timing status (worst lot per SKU: Red > Orange > Yellow > Green)
    try:
        from api.routes.dispatch_lead_time import _compute_lead_time_dataset
        dlt_df = _compute_lead_time_dataset()
        if not dlt_df.empty and "sku" in dlt_df.columns and "sla_category" in dlt_df.columns:
            _prio = {"Red": 0, "Orange": 1, "Yellow": 2, "Green": 3}

            def _worst_sla(series: pd.Series) -> str:
                best = "Green"
                best_p = 99
                for v in series.astype(str):
                    p = _prio.get(v, 99)
                    if p < best_p:
                        best_p = p
                        best = v
                return best

            sku_sla_map = (
                dlt_df.assign(sku=dlt_df["sku"].astype(str).str.strip().str.upper())
                .groupby("sku", as_index=True)["sla_category"]
                .agg(_worst_sla)
                .to_dict()
            )
            merged["dispatch_sla_category"] = (
                merged["sku"].astype(str).str.strip().str.upper().map(sku_sla_map).fillna("Green")
            )
        else:
            merged["dispatch_sla_category"] = "Green"
    except Exception as exc:
        logger.warning("Failed to enrich WH demand with Dispatch Lead Time SLA: %s", exc)
        merged["dispatch_sla_category"] = "Green"

    # Stock gap = ideal - current
    merged["stock_gap"] = (merged["ideal_wh_stock"] - merged["wh_stock"]).round(0)
    merged = merged.drop_duplicates(subset=["barcode_key"], keep="first")
    merged = merged.sort_values("stock_gap", ascending=False).reset_index(drop=True)

    week_labels = [f"W{int(w)}" for _, w in sorted(target_set, key=lambda t: (t[0], t[1]))]

    total_shortage = int(merged[merged["stock_gap"] > 0]["stock_gap"].sum())
    total_excess = int(abs(merged[merged["stock_gap"] < 0]["stock_gap"].sum()))
    shortage_skus = int((merged["stock_gap"] > 0).sum())
    excess_skus = int((merged["stock_gap"] < 0).sum())
    balanced_skus = int((merged["stock_gap"] == 0).sum())
    total_wh_stock = int(merged["wh_stock"].sum())
    wh_skus_in_stock = int((merged["wh_stock"] > 0).sum())

    # Distinct filter options helper
    def _clean_uniq(series: pd.Series) -> list[str]:
        return sorted(
            {
                str(v).strip()
                for v in series
                if str(v).strip() and str(v).strip().lower() not in ("nan", "none", "<na>")
            },
            key=str.casefold,
        )

    # 6 Most Common Apparel Sizes
    COMMON_SIZES = ["1XS", "2S", "3M", "4L", "5XL", "6XXL"]

    max_w = int(max([w for _, w in target_set])) if target_set else 27
    forecast_week_labels = [f"W{max_w + i + 1:02d}" for i in range(COVERAGE_WEEKS)]

    meta = {
        "ok": True,
        "demandWeeks": DEMAND_WEEKS,
        "coverageWeeks": COVERAGE_WEEKS,
        "weeksUsed": weeks_found,
        "weekLabels": week_labels,
        "forecastWeekLabels": forecast_week_labels,
        "forecastPeriodText": f"{forecast_week_labels[0]} – {forecast_week_labels[-1]}",
        "historicalPeriodText": f"{week_labels[0]} – {week_labels[-1]}",
        "totalShortageUnits": total_shortage,
        "totalExcessUnits": total_excess,
        "shortageSkuCount": shortage_skus,
        "excessSkuCount": excess_skus,
        "balancedSkuCount": balanced_skus,
        "totalWhStockUnits": total_wh_stock,
        "whSkusInStockCount": wh_skus_in_stock,
        "totalCatalogSkus": len(merged),
        "filter_options": {
            "departments": _clean_uniq(merged["department"]),
            "sections": _clean_uniq(merged["section"]),
            "sizes": COMMON_SIZES,
        },
    }

    # Save to disk cache for ultra-fast startup & subsequent queries
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        disk_parquet = _CACHE_DIR / "wh_sku_demand_result.parquet"
        disk_meta = _CACHE_DIR / "wh_sku_demand_meta.json"
        merged.to_parquet(disk_parquet, index=False)
        import json
        disk_meta.write_text(json.dumps({**meta, "_cache_key": cache_key}), encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed to persist WH demand cache to disk: %s", exc)

    with _CACHE_LOCK:
        _MEMORY_FULL_RESULT = (cache_key, meta, merged)

    return meta, merged


SIZE_ALIASES: dict[str, list[str]] = {
    "1XS": ["1XS", "XS"],
    "2S": ["2S", "S"],
    "3M": ["3M", "M"],
    "4L": ["4L", "L"],
    "5XL": ["5XL", "XL"],
    "6XXL": ["6XXL", "XXL", "2XL"],
}


@router.get("/sku-demand-vs-wh-stock")
def get_sku_demand_vs_wh_stock(
    limit: int = Query(1000, ge=1, le=10000),
    show: str = Query("all", description="all | shortage | excess | balanced | in_stock"),
    department: str = Query("", description="Filter by department"),
    section: str = Query("", description="Filter by section"),
    size: str = Query("", description="Filter by size (1XS, 2S, 3M, 4L, 5XL, 6XXL)"),
    search: str = Query("", description="Search barcode, sku, department, section, size"),
    sort_by: str = Query("stock_gap", description="stock_gap | wh_stock | avg_weekly_demand | ideal_wh_stock | sku | barcode | size"),
    sort_dir: str = Query("desc", description="desc | asc"),
):
    """
    Per-SKU 4-week average demand vs current warehouse stock (with Size from Item Master).

    - Average Weekly Demand = sum of last 4 completed weeks' sales / 4
    - Ideal WH Stock = Average Weekly Demand × 4  (COVERAGE_WEEKS)
    - Stock Gap = Ideal WH Stock - Current WH Stock
    - Positive gap = shortage, negative = excess
    """
    meta, merged = _compute_full_analysis()

    show = show if isinstance(show, str) else "all"
    department = department if isinstance(department, str) else ""
    section = section if isinstance(section, str) else ""
    size = size if isinstance(size, str) else ""
    search = search if isinstance(search, str) else ""
    sort_by = sort_by if isinstance(sort_by, str) else "stock_gap"
    sort_dir = sort_dir if isinstance(sort_dir, str) else "desc"

    filtered = merged.copy()

    # Show filters
    if show == "shortage":
        filtered = filtered[filtered["stock_gap"] > 0]
    elif show == "excess":
        filtered = filtered[filtered["stock_gap"] < 0]
    elif show == "balanced":
        filtered = filtered[filtered["stock_gap"] == 0]
    elif show == "in_stock":
        filtered = filtered[filtered["wh_stock"] > 0]

    # Department filter
    if department.strip():
        depts = [d.strip().upper() for d in department.split(",") if d.strip()]
        if depts:
            filtered = filtered[filtered["department"].str.upper().isin(depts)]

    # Section filter
    if section.strip():
        secs = [s.strip().upper() for s in section.split(",") if s.strip()]
        if secs:
            filtered = filtered[filtered["section"].str.upper().isin(secs)]

    # Size filter (matches 6 common sizes and their aliases)
    if size.strip():
        raw_sizes = [s.strip().upper() for s in size.split(",") if s.strip()]
        expanded_sizes: set[str] = set()
        for sz in raw_sizes:
            expanded_sizes.update([a.upper() for a in SIZE_ALIASES.get(sz, [sz])])
            expanded_sizes.add(sz)
        if expanded_sizes:
            filtered = filtered[filtered["size"].str.upper().isin(expanded_sizes)]

    # Search filter
    if search.strip():
        sq = search.strip().upper()
        mask = (
            filtered["barcode_key"].astype(str).str.upper().str.contains(sq, na=False)
            | filtered["sku"].astype(str).str.upper().str.contains(sq, na=False)
            | filtered["department"].astype(str).str.upper().str.contains(sq, na=False)
            | filtered["section"].astype(str).str.upper().str.contains(sq, na=False)
            | filtered["size"].astype(str).str.upper().str.contains(sq, na=False)
        )
        filtered = filtered[mask]

    # Sorting
    sort_col_map = {
        "stock_gap": "stock_gap",
        "wh_stock": "wh_stock",
        "wh_soh": "wh_stock",
        "avg_weekly_demand": "avg_weekly_demand",
        "ideal_wh_stock": "ideal_wh_stock",
        "sku": "sku",
        "barcode": "barcode_key",
        "size": "size",
    }
    col = sort_col_map.get(sort_by, "stock_gap")
    ascending = (sort_dir.lower() == "asc")
    filtered = filtered.sort_values(col, ascending=ascending)

    rows: list[dict[str, Any]] = []
    head_df = filtered.head(limit)
    records = head_df.to_dict(orient="records")

    for r in records:
        gap = float(r["stock_gap"])
        if gap > 0:
            status = "Shortage"
        elif gap < 0:
            status = "Excess"
        else:
            status = "Balanced"

        barcode_val = str(r["barcode_key"])
        sku_val = str(r["sku"])
        if sku_val in ("", "nan", "None"):
            sku_val = barcode_val

        rows.append({
            "barcode": barcode_val,
            "sku": sku_val,
            "size": str(r.get("size", "")),
            "department": str(r.get("department", "")),
            "section": str(r.get("section", "")),
            "color": str(r.get("color", "")),
            "mrp": float(r.get("mrp", 0.0)),
            "avgWeeklyDemand": round(float(r["avg_weekly_demand"]), 1),
            "whStock": int(r["wh_stock"]),
            "wh_soh": int(r["wh_stock"]),
            "idealWhStock": int(r["ideal_wh_stock"]),
            "stockGap": int(gap),
            "status": status,
            "dispatch_sla_category": str(r.get("dispatch_sla_category", "On Track")),
            "weeksWithSales": int(r["weeks_with_sales"]),
            "totalSold4w": int(r["total_sold"]),
        })

    response = dict(meta)
    response["rows"] = rows
    response["filteredTotal"] = len(filtered)
    response["total"] = len(rows)
    return response


@router.get("/export-wh-demand-excel")
def export_wh_demand_excel(
    show: str = Query("all", description="all | shortage | excess | balanced | in_stock"),
    department: str = Query(""),
    section: str = Query(""),
    size: str = Query(""),
    search: str = Query(""),
    sort_by: str = Query("stock_gap"),
    sort_dir: str = Query("desc"),
) -> StreamingResponse:
    """Export Warehouse Demand & SOH analysis as a styled Excel file."""
    meta, merged = _compute_full_analysis()

    show_str = show if isinstance(show, str) else "all"
    department = department if isinstance(department, str) else ""
    section = section if isinstance(section, str) else ""
    size = size if isinstance(size, str) else ""
    search = search if isinstance(search, str) else ""
    sort_by = sort_by if isinstance(sort_by, str) else "stock_gap"
    sort_dir = sort_dir if isinstance(sort_dir, str) else "desc"

    filtered = merged.copy()

    show_items = [s.strip().lower() for s in show_str.split(",") if s.strip()]
    if show_items and "all" not in show_items:
        masks = []
        for s in show_items:
            if s == "shortage":
                masks.append(filtered["stock_gap"] > 0)
            elif s == "excess":
                masks.append(filtered["stock_gap"] < 0)
            elif s == "balanced":
                masks.append(filtered["stock_gap"] == 0)
            elif s == "in_stock":
                masks.append(filtered["wh_stock"] > 0)
            elif "dispatch_sla_category" in filtered.columns:
                masks.append(filtered["dispatch_sla_category"].astype(str).str.lower() == s)
        if masks:
            combined_mask = masks[0]
            for m in masks[1:]:
                combined_mask |= m
            filtered = filtered[combined_mask]

    if department.strip():
        depts = [d.strip().upper() for d in department.split(",") if d.strip()]
        if depts:
            filtered = filtered[filtered["department"].str.upper().isin(depts)]
    if section.strip():
        secs = [s.strip().upper() for s in section.split(",") if s.strip()]
        if secs:
            filtered = filtered[filtered["section"].str.upper().isin(secs)]
    if size.strip():
        raw_sizes = [s.strip().upper() for s in size.split(",") if s.strip()]
        expanded_sizes: set[str] = set()
        for sz in raw_sizes:
            expanded_sizes.update([a.upper() for a in SIZE_ALIASES.get(sz, [sz])])
            expanded_sizes.add(sz)
        if expanded_sizes:
            filtered = filtered[filtered["size"].str.upper().isin(expanded_sizes)]
    if search.strip():
        sq = search.strip().upper()
        mask = (
            filtered["barcode_key"].astype(str).str.upper().str.contains(sq, na=False)
            | filtered["sku"].astype(str).str.upper().str.contains(sq, na=False)
            | filtered["department"].astype(str).str.upper().str.contains(sq, na=False)
            | filtered["section"].astype(str).str.upper().str.contains(sq, na=False)
            | filtered["size"].astype(str).str.upper().str.contains(sq, na=False)
        )
        filtered = filtered[mask]

    sort_col_map = {
        "stock_gap": "stock_gap",
        "wh_stock": "wh_stock",
        "wh_soh": "wh_stock",
        "avg_weekly_demand": "avg_weekly_demand",
        "ideal_wh_stock": "ideal_wh_stock",
        "sku": "sku",
        "barcode": "barcode_key",
        "size": "size",
    }
    col = sort_col_map.get(sort_by, "stock_gap")
    ascending = (sort_dir.lower() == "asc")
    filtered = filtered.sort_values(col, ascending=ascending)

    output = io.BytesIO()
    wb = xlsxwriter.Workbook(output, {"in_memory": True})
    ws = wb.add_worksheet("Warehouse Demand & SOH")

    # Formats
    title_fmt = wb.add_format({"bold": True, "font_size": 14, "font_color": "#1e3a5f"})
    sub_fmt = wb.add_format({"font_size": 9, "font_color": "#555555", "italic": True})
    hdr_fmt = wb.add_format({"bold": True, "bg_color": "#1e3a5f", "font_color": "#FFFFFF", "border": 1, "align": "center"})
    int_fmt = wb.add_format({"num_format": "#,##0", "align": "right"})
    dec_fmt = wb.add_format({"num_format": "0.0", "align": "right"})
    shortage_fmt = wb.add_format({"num_format": "+#,##0", "align": "right", "font_color": "#b91c1c", "bold": True})
    excess_fmt = wb.add_format({"num_format": "-#,##0", "align": "right", "font_color": "#047857"})

    ws.write(0, 0, "Warehouse Demand & SOH Report", title_fmt)
    ws.write(1, 0, f"Generated: {pd.Timestamp.now().strftime('%d-%b-%Y %H:%M')} | Total SKUs: {len(filtered)}", sub_fmt)

    headers = ["SKU", "Barcode", "Size", "Department", "Section", "Color", "MRP", "Current WH SOH", "Avg Weekly Demand", "Ideal WH Stock", "Stock Gap", "Status", "SLA Category"]
    for c_idx, h in enumerate(headers):
        ws.write(3, c_idx, h, hdr_fmt)

    r_idx = 4
    for _, r in filtered.iterrows():
        gap = float(r["stock_gap"])
        status = "Shortage" if gap > 0 else ("Excess" if gap < 0 else "Balanced")
        sla_cat = str(r.get("dispatch_sla_category", "Green"))
        ws.write(r_idx, 0, str(r.get("sku", "")))
        ws.write(r_idx, 1, str(r.get("barcode_key", "")))
        ws.write(r_idx, 2, str(r.get("size", "")))
        ws.write(r_idx, 3, str(r.get("department", "")))
        ws.write(r_idx, 4, str(r.get("section", "")))
        ws.write(r_idx, 5, str(r.get("color", "")))
        ws.write(r_idx, 6, float(r.get("mrp", 0.0)), dec_fmt)
        ws.write(r_idx, 7, int(r.get("wh_stock", 0)), int_fmt)
        ws.write(r_idx, 8, float(r.get("avg_weekly_demand", 0.0)), dec_fmt)
        ws.write(r_idx, 9, int(r.get("ideal_wh_stock", 0)), int_fmt)
        ws.write(r_idx, 10, int(gap), shortage_fmt if gap > 0 else excess_fmt)
        ws.write(r_idx, 11, status)
        ws.write(r_idx, 12, sla_cat)
        r_idx += 1

    ws.set_column(0, 0, 16)
    ws.set_column(1, 1, 16)
    ws.set_column(2, 2, 8)
    ws.set_column(3, 4, 18)
    ws.set_column(5, 5, 14)
    ws.set_column(6, 10, 16)
    ws.set_column(11, 11, 12)

    wb.close()
    output.seek(0)
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=Warehouse_Demand_And_SOH.xlsx"},
    )


# Alias for background server warmup
_compute_sku_demand_dataset = _compute_full_analysis

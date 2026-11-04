"""
Target vs Achieved API — Target LFS (daily rupee target × site) vs achieved net
sales from Sales-Master / Sales Dashboard (same lake as Sales Overview).

Achieved is counted ONLY for sites that exist in Target LFS (after channel/site
filter). Sites with a target but no sales in the period contribute 0 achieved.

Data sources:
  - TARGET_LFS.xlsx / TARGET_LFS.csv (category: target-lfs)
  - Sales-Master (SSI_PT_CENTRO_ICONIC_EBO_SALES_MASTER) via Sales Dashboard loader
  - Fallback: COMBINED_SALES / legacy PT master

Join key: Target "Site Code" == Customer Master site_manual_code (then site_code).
Raw sales Site_ID / short names do NOT match Target LFS codes.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/inventory-analytics", tags=["Inventory Analytics"])

FORMAT_UNSPECIFIED = "UNSPECIFIED"
PREFERRED_FORMAT_ORDER = ("PT", "SSL", "CENTRO", "ICONIC", "EBO", "GLOBUS", FORMAT_UNSPECIFIED)
# Prefer CSV (merge/upload canonical); xlsx kept as fallback for older blobs.
_TARGET_FILE = "TARGET_LFS.csv"
_TARGET_FILE_FALLBACKS = ("TARGET_LFS.xlsx", "Target_LFS.xlsx", "Target_LFS.csv")
_SALES_FILE = "SSI_PT_CENTRO_ICONIC_SALES.xlsx"

_CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "api_cache"
_CACHE_LOCK = threading.RLock()
_MEMORY_CACHE: dict[str, tuple[str, pd.DataFrame]] = {}


def _source_key(path: Path) -> str:
    """Fingerprint a local master so uploads invalidate its derived cache."""
    stat = path.stat()
    return f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}"


def _cache_paths(name: str) -> tuple[Path, Path]:
    base = _CACHE_DIR / f"target_vs_achieved_{name}"
    return base.with_suffix(".parquet"), base.with_suffix(".source.json")


def _load_cached_frame(
    name: str,
    source_path: Path,
    builder: Callable[[], pd.DataFrame],
) -> pd.DataFrame:
    """
    Return a normalized dataframe from RAM or Parquet.

    The cache key includes source path, size and nanosecond mtime, so a newly
    synced upload is picked up without a manual cache-clear action.
    """
    key = _source_key(source_path)
    with _CACHE_LOCK:
        memory = _MEMORY_CACHE.get(name)
        if memory and memory[0] == key:
            return memory[1]

        parquet_path, meta_path = _cache_paths(name)
        try:
            if parquet_path.exists() and meta_path.exists():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if meta.get("source_key") == key:
                    frame = pd.read_parquet(parquet_path)
                    _MEMORY_CACHE[name] = (key, frame)
                    logger.info(
                        "Target-vs-achieved %s cache hit (%s rows, parquet)",
                        name,
                        len(frame),
                    )
                    return frame
        except Exception as exc:
            logger.warning("Ignoring invalid target-vs-achieved %s cache: %s", name, exc)

        logger.info("Building target-vs-achieved %s cache from %s", name, source_path)
        frame = builder()
        _MEMORY_CACHE[name] = (key, frame)

        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            parquet_tmp = parquet_path.with_name(
                f"{parquet_path.name}.tmp.{os.getpid()}.{threading.get_ident()}"
            )
            meta_tmp = meta_path.with_name(
                f"{meta_path.name}.tmp.{os.getpid()}.{threading.get_ident()}"
            )
            frame.to_parquet(parquet_tmp, index=False)
            meta_tmp.write_text(
                json.dumps({"source_key": key, "rows": len(frame)}),
                encoding="utf-8",
            )
            os.replace(parquet_tmp, parquet_path)
            os.replace(meta_tmp, meta_path)
            logger.info(
                "Target-vs-achieved %s cache stored (%s rows, parquet)",
                name,
                len(frame),
            )
        except Exception as exc:
            logger.warning("Could not persist target-vs-achieved %s cache: %s", name, exc)
        return frame


def _input_dir() -> Path:
    try:
        from pipeline.pipeline_inputs import INPUT_DIR
    except ImportError:
        from pipeline_inputs import INPUT_DIR  # type: ignore
    return INPUT_DIR


def _find_input_file(preferred_name: str, *, stem_hints: tuple[str, ...] = ()) -> Path | None:
    """
    Locate a category master file under INPUT_DIR, tolerant of case/spacing
    drift in the filename — e.g. a file placed directly into blob current/
    by hand (Target_LFS.xlsx / TARGET_LFS.xlsx / "Target LFS.xlsx") rather
    than written by the merge pipeline's canonical-name step.

    When several matches exist (Windows case-insensitive xlsx + newer csv),
    pick the newest by mtime so a fresh Target LFS replace is not ignored.
    """
    input_dir = _input_dir()
    if not input_dir.exists():
        return None

    hints = {h.lower().replace(" ", "_") for h in (*stem_hints, Path(preferred_name).stem)}
    candidates: list[Path] = []
    exact = input_dir / preferred_name
    if exact.exists() and exact.is_file():
        candidates.append(exact.resolve())

    for f in input_dir.rglob("*"):
        if not f.is_file() or f.suffix.lower() not in (".xlsx", ".xls", ".csv"):
            continue
        stem = f.stem.lower().replace(" ", "_").replace("-", "_")
        if stem in hints or any(h in stem for h in hints):
            try:
                candidates.append(f.resolve())
            except OSError:
                candidates.append(f)

    if not candidates:
        return None

    # De-dupe (Windows may resolve TARGET_LFS.xlsx == Target_LFS.xlsx)
    uniq: dict[str, Path] = {}
    for p in candidates:
        uniq[str(p).lower()] = p
    ranked = sorted(
        uniq.values(),
        key=lambda p: (p.stat().st_mtime_ns if p.exists() else 0, p.suffix.lower() == ".csv"),
        reverse=True,
    )
    return ranked[0]


def _read_excel(path: Path, *, column_hints: list[str] | None = None) -> pd.DataFrame:
    try:
        from pipeline.pipeline_inputs import read_excel_fast
    except ImportError:
        from pipeline_inputs import read_excel_fast  # type: ignore
    return read_excel_fast(path, column_hints=column_hints)


def _normalize_site_code(s: pd.Series) -> pd.Series:
    return (
        s.astype(str)
        .str.strip()
        .str.upper()
        .str.replace(r"\.0$", "", regex=True)
        .replace({"NAN": "", "NONE": "", "<NA>": "", "NAT": ""})
    )


def _dashboard_sales_to_achieved(df: pd.DataFrame) -> pd.DataFrame:
    """
    Map Sales Dashboard / Sales-Master frame → Date, Site_ID, Net_sales_amount.

    Target LFS "Site Code" matches Customer Master site_manual_code (then site_code),
    NOT the human-readable sales Site_ID / short name.
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=["Date", "Site_ID", "Net_sales_amount"])

    out = pd.DataFrame(index=df.index)
    date_col = "date" if "date" in df.columns else ("Date" if "Date" in df.columns else None)
    if date_col is None:
        return pd.DataFrame(columns=["Date", "Site_ID", "Net_sales_amount"])
    out["Date"] = pd.to_datetime(df[date_col], errors="coerce")

    net_col = (
        "net_sale_amount"
        if "net_sale_amount" in df.columns
        else ("Net_sales_amount" if "Net_sales_amount" in df.columns else None)
    )
    out["Net_sales_amount"] = (
        pd.to_numeric(df[net_col], errors="coerce").fillna(0.0) if net_col else 0.0
    )

    # Prefer keys that match Target LFS Site Code
    key = pd.Series("", index=df.index, dtype=object)
    for col in ("site_manual_code", "site_code", "site_id", "Site_ID"):
        if col not in df.columns:
            continue
        cand = _normalize_site_code(df[col])
        fill = key.eq("") & cand.ne("")
        key = key.where(~fill, cand)

    out["Site_ID"] = key
    out = out[out["Date"].notna() & out["Site_ID"].ne("")].copy()
    return out[["Date", "Site_ID", "Net_sales_amount"]]


def _build_target_df(path: Path) -> pd.DataFrame:
    df = _read_excel(path, column_hints=["Date", "Site Code", "STORE NAME", "Format", "Target"])
    df.columns = df.columns.astype(str).str.strip()

    rename: dict[str, str] = {}
    for c in df.columns:
        cu = c.upper()
        if cu == "DATE":
            rename[c] = "date"
        elif cu == "SITE CODE":
            rename[c] = "site_code"
        elif cu.startswith("STORE NAME"):
            rename[c] = "store_name"
        elif cu == "FORMAT":
            rename[c] = "format"
        elif cu == "TARGET":
            rename[c] = "target"
    df = df.rename(columns=rename)

    for col in ("date", "site_code", "format", "target"):
        if col not in df.columns:
            raise HTTPException(
                status_code=500,
                detail=f"Target LFS is missing an expected column ({col}). Check the sheet headers.",
            )
    if "store_name" not in df.columns:
        df["store_name"] = ""

    out = df[["date", "site_code", "store_name", "format", "target"]].copy()
    try:
        from pipeline.pipeline_inputs import parse_sales_dates
    except ImportError:
        try:
            from pipeline_inputs import parse_sales_dates  # type: ignore
        except ImportError:
            parse_sales_dates = None  # type: ignore
    if parse_sales_dates is not None:
        out["date"] = parse_sales_dates(out["date"])
    else:
        out["date"] = pd.to_datetime(out["date"].astype(str).str.strip(), format="mixed", errors="coerce")
    out["target"] = pd.to_numeric(out["target"], errors="coerce").fillna(0.0)
    out["site_code"] = _normalize_site_code(out["site_code"])
    out["format"] = (
        out["format"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
        .replace({"": FORMAT_UNSPECIFIED, "NAN": FORMAT_UNSPECIFIED, "NONE": FORMAT_UNSPECIFIED})
    )
    out["store_name"] = out["store_name"].astype(str).str.strip().replace({"nan": "", "None": ""})

    out = out[out["date"].notna()]
    return out


_TARGET_SQL_MEM: pd.DataFrame | None = None
_TARGET_SQL_MEM_AT = 0.0
_TARGET_SQL_TTL_S = 600.0


def _load_target_df() -> pd.DataFrame:
    """Prefer local/blob Target file for default speed; SQL is cached in-memory."""
    global _TARGET_SQL_MEM, _TARGET_SQL_MEM_AT

    # Fast path: local/blob file (same as before SQL cutover) — best for first view.
    path = _find_input_file(
        _TARGET_FILE,
        stem_hints=("target_lfs", "target-lfs", "targetlfs"),
    )
    if path is None:
        for alt in _TARGET_FILE_FALLBACKS:
            path = _find_input_file(alt, stem_hints=("target_lfs", "target-lfs", "targetlfs"))
            if path is not None:
                break
    if path is None:
        try:
            from api.blob_store import BlobJsonStore

            store = BlobJsonStore()
            # Prefer newly published CSV (replace upload) over legacy xlsx.
            for blob_key in (
                "input/Target_LFS/current/TARGET_LFS.csv",
                "input/Target_LFS/current/Target_LFS.csv",
                "input/Target_LFS/TARGET_LFS.csv",
                "input/Target_LFS/current/TARGET_LFS.xlsx",
                "input/Target_LFS/current/Target_LFS.xlsx",
                "input/Target_LFS/Target_LFS.xlsx",
            ):
                if store._blob_exists(blob_key):
                    bp = store._ensure_local_file(blob_key)
                    if bp and bp.exists():
                        path = bp
                        break
        except Exception as exc:
            logger.warning("Target LFS blob download skipped: %s", exc)

    if path is None:
        # Last resort: Azure SQL (memory-cached) when local/blob file missing.
        try:
            import time as _time
            from api.azure_sql_analytics import sql_load_target_df, sql_query_ready

            now = _time.time()
            if (
                _TARGET_SQL_MEM is not None
                and not _TARGET_SQL_MEM.empty
                and (now - _TARGET_SQL_MEM_AT) < _TARGET_SQL_TTL_S
            ):
                return _TARGET_SQL_MEM.copy()
            if sql_query_ready():
                df = sql_load_target_df()
                if df is not None and not df.empty:
                    _TARGET_SQL_MEM = df
                    _TARGET_SQL_MEM_AT = now
                    logger.info("Target LFS loaded from Azure SQL (%s rows)", len(df))
                    return df.copy()
        except Exception as sql_exc:
            logger.warning("Azure SQL Target LFS failed: %s", sql_exc)
        raise HTTPException(
            status_code=404,
            detail=(
                "Target LFS not synced to local yet. If you placed the file directly in "
                "Blob input/Target_LFS/current/, trigger a sync via POST /api/upload/merge-inputs "
                "(or upload it via Manual Upload / Upload from Blob, category: Target (LFS))."
            ),
        )
    # A distinct cache name prevents reuse of the older PT/SSL/CENTRO/ICONIC-only frame.
    return _load_cached_frame("target_all_formats_v2", path, lambda: _build_target_df(path))


# Ignore tiny blob stubs (<5 MB) — they often contain a partial COMBINED_SALES sample
# and make Target vs Achieved show ~0% achievement for almost all sites.
_MIN_SALES_BYTES = 5_000_000


def _combined_sales_candidates() -> list[Path]:
    """Sales sources for achieved net sales — Sales-Master first."""
    backend = Path(__file__).resolve().parents[2]
    input_dir = _input_dir()
    candidates: list[Path] = [
        input_dir / "SSI_PT_CENTRO_ICONIC_EBO_SALES_MASTER.csv",
        input_dir / "SSI_PT_CENTRO_ICONIC_SALES_MASTER.csv",
        input_dir / "SSI_PT_CENTRO_ICONIC_SALES.csv",
        backend / "output_files" / "COMBINED_SALES.csv",
    ]
    try:
        from api.sales_year_loader import resolve_sales_master_path

        master = resolve_sales_master_path()
        if master is not None:
            candidates.insert(0, master)
    except Exception as exc:
        logger.warning("Sales-Master resolve for target_vs_achieved skipped: %s", exc)
    try:
        from pipeline.pipeline_inputs import COMBINED_SALES_CSV, COMBINED_SALES
    except ImportError:
        try:
            from pipeline_inputs import COMBINED_SALES_CSV, COMBINED_SALES  # type: ignore
        except ImportError:
            COMBINED_SALES_CSV = COMBINED_SALES = None  # type: ignore
        else:
            candidates.extend([COMBINED_SALES_CSV, COMBINED_SALES])
    else:
        candidates.extend([COMBINED_SALES_CSV, COMBINED_SALES])
    candidates.append(backend / "data" / "blob_cache" / "output_COMBINED_SALES.csv")
    try:
        from api.blob_store import BlobJsonStore

        store = BlobJsonStore()
        for ext in (".csv", ".parquet"):
            bname = store._blob_name("COMBINED_SALES", ext)
            if store._blob_exists(bname):
                bp = store._ensure_local_file(bname)
                if bp and bp.exists():
                    candidates.append(bp)
                    break
    except Exception as exc:
        logger.warning("COMBINED_SALES blob candidate check skipped: %s", exc)
    seen: set[str] = set()
    out: list[Path] = []
    for path in candidates:
        if path is None or not path.exists():
            continue
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def _pick_best_sales_path(candidates: list[Path]) -> Path | None:
    """Prefer a substantial sales file over a small blob-cache stub."""
    existing = [path for path in candidates if path.exists()]
    if not existing:
        return None
    substantial = [path for path in existing if path.stat().st_size >= _MIN_SALES_BYTES]
    pool = substantial or existing
    return max(pool, key=lambda path: path.stat().st_size)


def _optional_daily_sales_path() -> Path | None:
    path = _input_dir() / "SSI_PT_CENTRO_ICONIC_SALES.csv"
    return path if path.exists() else None


def _optional_ebo_sales_path() -> Path | None:
    return _find_input_file(
        "EBO_SALES.csv",
        stem_hints=("ebo_sales", "ebo sales"),
    )


def _merge_achieved_frames(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """Stack sales sources; when a history master exists, only append newer rows from incremental files."""
    usable = [frame for frame in frames if frame is not None and not frame.empty]
    if not usable:
        return pd.DataFrame(columns=["Date", "Site_ID", "Net_sales_amount"])
    if len(usable) == 1:
        return usable[0].copy()

    base = usable[0].copy()
    cutoff = pd.Timestamp(base["Date"].max()) if base["Date"].notna().any() else None
    extras: list[pd.DataFrame] = []
    for frame in usable[1:]:
        part = frame.copy()
        if cutoff is not None:
            part = part[part["Date"] > cutoff]
        if not part.empty:
            extras.append(part)
    if not extras:
        return base
    return pd.concat([base, *extras], ignore_index=True)


def _build_combined_achieved_df(path: Path) -> pd.DataFrame:
    """Normalize COMBINED_SALES → Date, Site_ID, Net_sales_amount."""
    try:
        from pipeline.pipeline_inputs import _normalize_sales_frame, parse_sales_dates
    except ImportError:
        from pipeline_inputs import _normalize_sales_frame, parse_sales_dates  # type: ignore

    if path.suffix.lower() == ".csv":
        raw = pd.read_csv(path, low_memory=False)
    elif path.suffix.lower() == ".parquet":
        raw = pd.read_parquet(path)
    else:
        raw = pd.read_excel(path, engine="openpyxl")

    out = _normalize_sales_frame(raw)
    out.columns = out.columns.astype(str).str.strip()
    if "Date" not in out.columns or "Site_ID" not in out.columns:
        raise ValueError(f"COMBINED_SALES missing Date/Site_ID columns: {path.name}")

    out["Date"] = parse_sales_dates(out["Date"])
    out["Net_sales_amount"] = pd.to_numeric(
        out.get("Net_sales_amount", 0.0), errors="coerce"
    ).fillna(0.0)
    out["Site_ID"] = _normalize_site_code(out["Site_ID"])
    out = out[out["Date"].notna()].copy()
    return out[["Date", "Site_ID", "Net_sales_amount"]]


def _build_sales_df(path: Path) -> pd.DataFrame:
    try:
        from pipeline.pipeline_inputs import _normalize_sales_frame
    except ImportError:
        from pipeline_inputs import _normalize_sales_frame  # type: ignore

    raw = _read_excel(
        path,
        column_hints=["ENTRY_DATE", "STORE CODE", "TRANSACTION_SITE", "BARCODE", "QUANTITY", "MRP"],
    )
    out = _normalize_sales_frame(raw)
    out.columns = out.columns.astype(str).str.strip()
    if "Date" not in out.columns or "Site_ID" not in out.columns:
        raise HTTPException(
            status_code=500,
            detail="Sales (PT, SSL, Centro, Iconic, EBO) master could not be normalized (unexpected columns).",
        )

    out["Date"] = pd.to_datetime(out["Date"].astype(str).str.strip(), format="mixed", errors="coerce")
    out["Net_sales_amount"] = pd.to_numeric(
        out.get("Net_sales_amount", 0.0), errors="coerce"
    ).fillna(0.0)
    out["Site_ID"] = _normalize_site_code(out["Site_ID"])
    out = out[out["Date"].notna()]
    return out[["Date", "Site_ID", "Net_sales_amount"]]


def _load_sales_df(
    *,
    date_from: pd.Timestamp | None = None,
    date_to: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """
    Achieved net sales keyed to Target LFS Site Code.

    Prefer Sales-Master via sales_year_loader (same source as Sales Overview Net Sales).
    Join key = site_manual_code / site_code (Target LFS Site Code), not store names.
    """
    # ── 1. Same FY slices as Sales Dashboard KPI cards (fast local/parquet) ──
    try:
        from api.sales_year_loader import load_sales_for_year

        years: set[int] = set()
        if date_from is not None and pd.notna(date_from):
            years.add(int(date_from.year + 1) if date_from.month >= 4 else int(date_from.year))
        if date_to is not None and pd.notna(date_to):
            years.add(int(date_to.year + 1) if date_to.month >= 4 else int(date_to.year))
        if not years:
            # Trailing view — cover recent FYs present in Target LFS
            years = {2025, 2026, 2027}

        parts: list[pd.DataFrame] = []
        for y in sorted(years):
            try:
                part = load_sales_for_year(y)
                mapped = _dashboard_sales_to_achieved(part)
                if not mapped.empty:
                    parts.append(mapped)
            except Exception as exc:
                logger.warning("Target-vs-achieved year load %s failed: %s", y, exc)
        if parts:
            out = pd.concat(parts, ignore_index=True) if len(parts) > 1 else parts[0]
            logger.info(
                "Target-vs-achieved achieved source: sales_year_loader years=%s (%s rows, %s sites)",
                sorted(years),
                len(out),
                out["Site_ID"].nunique(),
            )
            return out
    except Exception as exc:
        logger.warning("Target-vs-achieved year-loader path failed: %s", exc)

    # ── 2. Full Sales Dashboard frame ────────────────────────────────────────
    try:
        from api.routes.sales_dashboard import _get_sales_df

        dash = _get_sales_df()
        mapped = _dashboard_sales_to_achieved(dash)
        if not mapped.empty:
            logger.info(
                "Target-vs-achieved achieved source: Sales Dashboard frame (%s rows, %s sites)",
                len(mapped),
                mapped["Site_ID"].nunique(),
            )
            return mapped
    except Exception as exc:
        logger.warning("Target-vs-achieved dashboard sales path failed: %s", exc)

    # ── 3. Fallback: file candidates + CM enrich ──────────────────────────────
    frames: list[pd.DataFrame] = []
    candidates = _combined_sales_candidates()
    main_path = _pick_best_sales_path(candidates)
    if main_path is not None:
        try:
            frames.append(
                _load_cached_frame(
                    "achieved_combined_v3_manual",
                    main_path,
                    lambda p=main_path: _build_combined_achieved_df(p),
                )
            )
            logger.info("Target-vs-achieved history source: %s", main_path.name)
        except Exception as exc:
            logger.warning("Achieved load from %s failed: %s", main_path, exc)

    daily_path = _optional_daily_sales_path()
    if daily_path is not None and daily_path.stat().st_size >= 1024:
        try:
            frames.append(
                _load_cached_frame(
                    "achieved_daily_v3_manual",
                    daily_path,
                    lambda p=daily_path: _build_combined_achieved_df(p),
                )
            )
            logger.info("Target-vs-achieved incremental source: %s", daily_path.name)
        except Exception as exc:
            logger.warning("Achieved load from daily %s failed: %s", daily_path, exc)

    ebo_path = _optional_ebo_sales_path()
    if ebo_path is not None:
        try:
            frames.append(
                _load_cached_frame(
                    "achieved_ebo_v3_manual",
                    ebo_path,
                    lambda p=ebo_path: _build_combined_achieved_df(p),
                )
            )
            logger.info("Target-vs-achieved EBO source: %s", ebo_path.name)
        except Exception as exc:
            logger.warning("Achieved load from EBO %s failed: %s", ebo_path, exc)

    merged = _merge_achieved_frames(frames)
    if not merged.empty:
        try:
            from pipeline.customer_master import load_customer_master

            cm = load_customer_master(retail_only=False)
            if cm is not None and not cm.empty:
                maps: dict[str, str] = {}
                for _, r in cm.iterrows():
                    manual = str(r.get("site_manual_code") or "").strip().upper().replace(".0", "")
                    code = str(r.get("site_code") or "").strip().upper().replace(".0", "")
                    short = str(r.get("site_short_code") or "").strip().upper()
                    target_key = manual or code
                    if not target_key:
                        continue
                    for alias in (manual, code, short):
                        if alias and alias not in ("NAN", "NONE", ""):
                            maps[alias] = target_key
                if maps:
                    remapped = merged["Site_ID"].map(maps)
                    hit = remapped.notna() & remapped.ne("")
                    merged = merged.copy()
                    merged.loc[hit, "Site_ID"] = remapped.loc[hit]
                    logger.info(
                        "Target-vs-achieved CM remap applied to %s / %s achieved rows",
                        int(hit.sum()),
                        len(merged),
                    )
        except Exception as exc:
            logger.warning("Target-vs-achieved CM remap skipped: %s", exc)
        return merged

    path = _find_input_file(
        _SALES_FILE, stem_hints=("ssi_pt_centro_iconic_sales", "sales_pt_ssl_centro_iconic")
    )
    if path is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "COMBINED_SALES and Sales (PT, SSL, Centro, Iconic, EBO) master not found locally. "
                "Run combine_sales or sync inputs first."
            ),
        )
    logger.warning("Achieved falling back to legacy PT sales master: %s", path.name)
    return _load_cached_frame("sales_legacy_pt_v3", path, lambda: _build_sales_df(path))


def warm_target_vs_achieved_cache() -> None:
    """Prebuild both normalized caches so the first chart request is fast."""
    target = _load_target_df()
    sales = _load_sales_df()
    logger.info(
        "Target-vs-achieved cache warm complete (target=%s, sales=%s rows)",
        len(target),
        len(sales),
    )


PERIODS = ("week", "month", "year")
_DEFAULT_COUNT = {"week": 12, "month": 12, "year": 5}


def _period_key(ts: pd.Series, period: str) -> pd.Series:
    """Groupby key as a single sortable int per period type."""
    if period == "year":
        return ts.dt.year.astype(int)
    if period == "month":
        return ts.dt.year.astype(int) * 100 + ts.dt.month.astype(int)
    iso = ts.dt.isocalendar()
    return iso["year"].astype(int) * 100 + iso["week"].astype(int)


def _period_key_for_ts(ts: pd.Timestamp, period: str) -> tuple[int, ...]:
    if period == "year":
        return (int(ts.year),)
    if period == "month":
        return (int(ts.year), int(ts.month))
    iso = ts.isocalendar()
    return (int(iso.year), int(iso.week))


def _key_to_int(key: tuple[int, ...], period: str) -> int:
    if period == "year":
        return key[0]
    return key[0] * 100 + key[1]


def _period_labels(key: tuple[int, ...], period: str) -> tuple[str, str]:
    """(short label for axis, full label with year for tooltip/subtitle)."""
    if period == "year":
        y = key[0]
        return str(y), str(y)
    if period == "month":
        y, m = key
        name = pd.Timestamp(year=y, month=m, day=1).strftime("%b")
        return name, f"{name} {y}"
    y, w = key
    return f"W{w}", f"W{w} {y}"


def _trailing_period_keys(latest: pd.Timestamp, period: str, count: int) -> list[tuple[int, ...]]:
    out: list[tuple[int, ...]] = []
    if period == "year":
        for i in range(count - 1, -1, -1):
            out.append((int(latest.year) - i,))
    elif period == "month":
        anchor_period = latest.to_period("M")
        for i in range(count - 1, -1, -1):
            p = anchor_period - i
            out.append((int(p.year), int(p.month)))
    else:
        for i in range(count - 1, -1, -1):
            anchor = latest - timedelta(weeks=i)
            iso = anchor.isocalendar()
            out.append((int(iso.year), int(iso.week)))
    return out


def _build_site_growth_table(
    target: pd.DataFrame,
    sales: pd.DataFrame,
    period: str,
    period_keys: list[tuple[int, ...]],
    current_key: tuple[int, ...],
) -> list[dict[str, Any]]:
    """Rank sites by sustained over-achievement across completed periods only."""
    completed_keys = [key for key in period_keys if key != current_key]
    if not completed_keys:
        return []

    completed_ints = [_key_to_int(key, period) for key in completed_keys]
    labels = {
        _key_to_int(key, period): _period_labels(key, period)[1]
        for key in completed_keys
    }
    target_by_site = target.groupby(["site_code", "period_key"])["target"].sum()
    achieved_by_site = (
        sales.groupby(["Site_ID", "period_key"])["Net_sales_amount"].sum()
        if not sales.empty and "period_key" in sales.columns
        else pd.Series(dtype=float)
    )
    site_meta = (
        target.sort_values("date")
        .drop_duplicates(subset="site_code", keep="last")
        .set_index("site_code")[["store_name", "format"]]
    )

    rows: list[dict[str, Any]] = []
    for site_code in sorted(set(target["site_code"])):
        points = [
            {
                "period_key": int_key,
                "target": float(target_by_site.get((site_code, int_key), 0.0)),
                "achieved": float(achieved_by_site.get((site_code, int_key), 0.0)),
            }
            for int_key in completed_ints
        ]
        evaluated = [point for point in points if point["target"] > 0]
        if not evaluated or points[-1]["target"] <= 0:
            continue

        streak = 0
        for point in reversed(points):
            if point["target"] > 0 and point["achieved"] >= point["target"]:
                streak += 1
            else:
                break

        over_target_periods = sum(
            1 for point in evaluated if point["achieved"] >= point["target"]
        )
        total_target = sum(point["target"] for point in evaluated)
        total_achieved = sum(point["achieved"] for point in evaluated)
        average_pct = 100 * total_achieved / total_target if total_target else 0.0

        latest = points[-1]
        latest_pct = 100 * latest["achieved"] / latest["target"] if latest["target"] else 0.0
        previous = points[-2] if len(points) > 1 else None
        sales_growth_pct = None
        if previous and previous["achieved"] > 0:
            sales_growth_pct = 100 * (latest["achieved"] - previous["achieved"]) / previous["achieved"]

        recent = evaluated[-3:]
        recent_target = sum(point["target"] for point in recent)
        recent_achieved = sum(point["achieved"] for point in recent)
        recent_pct = 100 * recent_achieved / recent_target if recent_target else 0.0
        suggested_increase_pct = 0.0
        if streak >= 3 and recent_pct > 100:
            # Use half of sustained over-achievement and cap the step-up at 25%.
            suggested_increase_pct = min(25.0, max(0.0, (recent_pct - 100) * 0.5))

        if suggested_increase_pct > 0:
            recommendation = "Increase target"
        elif streak >= 2:
            recommendation = "Monitor growth"
        elif latest_pct >= 100:
            recommendation = "Above target"
        else:
            recommendation = "Maintain / review"

        meta = site_meta.loc[site_code]
        recommended_target = latest["target"] * (1 + suggested_increase_pct / 100)
        rows.append(
            {
                "siteId": site_code,
                "siteName": str(meta["store_name"]),
                "format": str(meta["format"]),
                "latestCompletedPeriod": labels[latest["period_key"]],
                "periodsEvaluated": len(evaluated),
                "overTargetPeriods": over_target_periods,
                "consecutiveOverTarget": streak,
                "averageAchievementPct": round(average_pct, 1),
                "latestTarget": round(latest["target"], 2),
                "latestAchieved": round(latest["achieved"], 2),
                "latestAchievementPct": round(latest_pct, 1),
                "salesGrowthPct": round(sales_growth_pct, 1) if sales_growth_pct is not None else None,
                "suggestedIncreasePct": round(suggested_increase_pct, 1),
                "recommendedTarget": round(recommended_target, 2),
                "recommendation": recommendation,
            }
        )

    rank = {"Increase target": 0, "Monitor growth": 1, "Above target": 2, "Maintain / review": 3}
    rows.sort(
        key=lambda row: (
            rank[row["recommendation"]],
            -row["consecutiveOverTarget"],
            -row["averageAchievementPct"],
            row["siteId"],
        )
    )
    return rows


@router.get("/target-vs-achieved")
def get_target_vs_achieved(
    site_id: str = Query("all", description="Target Site Code, or 'all' for overall"),
    timeline_mode: str = Query(""),
    timeline_values: str = Query(""),
    site_ids: str | None = Query(None, description="Comma-separated site codes from master filters (store category, grade, zone, etc.)"),
    channel: str = Query("all", description="Target LFS format, or 'all' (ignored when site_id is set)"),
    period: str = Query("week", description="week / month / year"),
    count: int = Query(0, ge=0, le=60, description="How many trailing periods to return (0 = sensible default for the period type)"),
    date_from: str = Query("", description="Inclusive start YYYY-MM-DD (FY / custom range)"),
    date_to: str = Query("", description="Inclusive end YYYY-MM-DD (FY / custom range)"),
):
    """Target (rupee) vs Achieved (real net sales) by week/month/year — site or overall/channel/category."""
    def _val(x, default):
        if hasattr(x, "default"):
            return default if x.default is ... else x.default
        return default if x is None else x

    page_key = "|".join(
        str(part)
        for part in (
            site_id, timeline_mode, timeline_values, site_ids, channel, period, count, date_from, date_to,
        )
    )
    from api.page_response_cache import load_page_cache, rebuilding_page_cache, save_page_cache

    if not rebuilding_page_cache():
        cached_target = load_page_cache("target-vs-achieved", page_key)
        if cached_target is not None:
            return cached_target

    def _remember(payload: dict) -> dict:
        save_page_cache("target-vs-achieved", page_key, payload)
        return payload

    period = str(_val(period, "week")).strip().lower()
    if period not in PERIODS:
        raise HTTPException(status_code=400, detail=f"period must be one of {PERIODS}")
    n = int(_val(count, 0)) or _DEFAULT_COUNT[period]

    target_df = _load_target_df()
    if target_df.empty:
        raise HTTPException(
            status_code=404,
            detail="No valid target rows found in Target LFS.",
        )

    site_lookup = (
        target_df[["site_code", "store_name", "format"]]
        .drop_duplicates(subset="site_code")
        .sort_values(["format", "store_name"])
    )
    site_options = [{"id": "all", "label": "Overall (all sites)"}] + [
        {
            "id": r["site_code"],
            "label": f"{r['site_code']} — {r['store_name']}".strip(" —") or r["site_code"],
            "format": r["format"],
        }
        for _, r in site_lookup.iterrows()
    ]
    formats_present = set(target_df["format"].dropna().astype(str))
    order = {name: index for index, name in enumerate(PREFERRED_FORMAT_ORDER)}
    available_formats = sorted(
        formats_present,
        key=lambda name: (order.get(name, len(order)), name),
    )
    channel_options = [{"id": "all", "label": "All formats"}] + [
        {
            "id": value,
            "label": (
                "Unspecified"
                if value == FORMAT_UNSPECIFIED
                else value if value in {"PT", "SSL", "EBO"} else value.title()
            ),
        }
        for value in available_formats
    ]

    t = target_df
    sid = str(_val(site_id, "all")).strip()
    raw_sids = _val(site_ids, None)
    ch = str(_val(channel, "all")).strip().upper()

    if sid.lower() != "all":
        sid_norm = sid.upper()
        t = t[t["site_code"] == sid_norm]
    elif raw_sids:
        raw_ids = {
            s.strip().upper().replace(".0", "")
            for s in str(raw_sids).split(",")
            if s.strip()
        }
        if raw_ids:
            t = t[t["site_code"].isin(raw_ids)]
    if ch != "ALL":
        channels = {value.strip() for value in ch.split(",") if value.strip()}
        t = t[t["format"].isin(channels)]

    if _val(timeline_mode, "") and _val(timeline_values, ""):
        from api.routes.sales_dashboard import _apply_timeline_filter
        t = _apply_timeline_filter(t.assign(month_key=t["date"].dt.strftime("%Y-%m")), timeline_mode=_val(timeline_mode, ""), timeline_values=_val(timeline_values, ""))

    if t.empty:
        if site_ids:
            return _remember({
                "ok": True,
                "filters": {"site_id": site_id, "channel": channel, "period": period, "count": n},
                "site_options": site_options[:2000],
                "channel_options": channel_options,
                "site_count": 0,
                "sites_with_target": 0,
                "sites_with_achieved": 0,
                "achievement_scope": "No Target LFS rows match the selected filter criteria",
                "sales_data_as_of": None,
                "current": {"key": [], "label": "", "periodLabel": "", "target": 0.0, "achieved": 0.0, "achievementPct": 0.0, "isCurrent": True},
                "series": [],
                "range_summary": None,
                "growth_analysis": {},
                "growth_table": [],
            })
        raise HTTPException(
            status_code=404,
            detail="No Target LFS rows match this site/channel filter.",
        )

    # Optional FY / custom date window (from Sales Overview timeline)
    df_from = str(_val(date_from, "") or "").strip()[:10]
    df_to = str(_val(date_to, "") or "").strip()[:10]
    range_from_ts = pd.Timestamp(df_from) if df_from and len(df_from) >= 8 else None
    range_to_ts = None
    if df_to and len(df_to) >= 8:
        range_to_ts = pd.Timestamp(df_to) + pd.Timedelta(days=1, microseconds=-1)

    if range_from_ts is not None:
        t = t[t["date"] >= range_from_ts]
    if range_to_ts is not None:
        t = t[t["date"] <= range_to_ts]

    if t.empty:
        return _remember({
            "ok": True,
            "filters": {
                "site_id": site_id,
                "channel": channel,
                "period": period,
                "count": n,
                "date_from": df_from or None,
                "date_to": df_to or None,
            },
            "site_options": site_options[:2000],
            "channel_options": channel_options,
            "site_count": 0,
            "sites_with_target": 0,
            "sites_with_achieved": 0,
            "achievement_scope": "No Target LFS rows in the selected date range",
            "sales_data_as_of": None,
            "current": {"key": [], "label": "", "periodLabel": "", "target": 0.0, "achieved": 0.0, "achievementPct": 0.0, "isCurrent": True},
            "series": [],
            "range_summary": None,
            "growth_analysis": {},
            "growth_table": [],
        })

    valid_sites = set(t["site_code"])
    t_min_date = pd.Timestamp(t["date"].min()) if not t.empty else None
    t_max_date = pd.Timestamp(t["date"].max()) if not t.empty else None

    # Load sales within the requested date window, but bound by available target dates
    # so we don't count sales for future untargeted months against 0 targets.
    effective_from_ts = range_from_ts
    effective_to_ts = range_to_ts
    if t_max_date is not None and effective_to_ts is not None:
        effective_to_ts = min(effective_to_ts, t_max_date + pd.Timedelta(days=1, microseconds=-1))

    sales_df = _load_sales_df(date_from=effective_from_ts, date_to=effective_to_ts)
    s = sales_df[sales_df["Site_ID"].isin(valid_sites)].copy()
    if effective_from_ts is not None and not s.empty:
        s = s[s["Date"] >= effective_from_ts]
    if effective_to_ts is not None and not s.empty:
        s = s[s["Date"] <= effective_to_ts]

    if _val(timeline_mode, "") and _val(timeline_values, "") and not s.empty:
        s = _apply_timeline_filter(s.assign(date=s["Date"], month_key=s["Date"].dt.strftime("%Y-%m")), timeline_mode=_val(timeline_mode, ""), timeline_values=_val(timeline_values, ""))

    t = t.copy()
    t["period_key"] = _period_key(t["date"], period)
    target_by_period = t.groupby("period_key")["target"].sum()

    achieved_by_period: pd.Series
    if s.empty:
        achieved_by_period = pd.Series(dtype=float)
        latest_date = t_max_date or pd.Timestamp.now()
    else:
        s = s.copy()
        s["period_key"] = _period_key(s["Date"], period)
        achieved_by_period = s.groupby("period_key")["Net_sales_amount"].sum()
        latest_date = min(pd.Timestamp(s["Date"].max()), t_max_date) if t_max_date is not None else pd.Timestamp(s["Date"].max())

    current_key = _period_key_for_ts(latest_date, period)
    period_keys = _trailing_period_keys(latest_date, period, n)

    series: list[dict[str, Any]] = []
    for key in period_keys:
        int_key = _key_to_int(key, period)
        short_label, full_label = _period_labels(key, period)
        tgt = float(target_by_period.get(int_key, 0.0))
        ach = float(achieved_by_period.get(int_key, 0.0))
        pct = round(100 * ach / tgt, 1) if tgt else 0.0
        series.append(
            {
                "key": list(key),
                "label": short_label,
                "periodLabel": full_label,
                "target": round(tgt, 2),
                "achieved": round(ach, 2),
                "achievementPct": pct,
                "isCurrent": key == current_key,
            }
        )

    current = series[-1] if series else None
    growth_table = _build_site_growth_table(t, s, period, period_keys, current_key)

    sites_with_target = int(t["site_code"].nunique())
    sites_with_achieved = int(s["Site_ID"].nunique()) if not s.empty else 0

    # Range summary = available target in window vs sales of those same Target LFS stores
    range_target = float(t["target"].sum())
    range_achieved = float(s["Net_sales_amount"].sum()) if not s.empty else 0.0
    range_pct = round(100 * range_achieved / range_target, 1) if range_target > 0 else 0.0
    if range_from_ts is not None or range_to_ts is not None:
        from_lbl = (range_from_ts.strftime("%d %b %Y") if range_from_ts is not None else (t_min_date.strftime("%d %b %Y") if t_min_date is not None else "…"))
        actual_end = min(range_to_ts, t_max_date) if range_to_ts is not None and t_max_date is not None else (t_max_date or range_to_ts)
        to_lbl = actual_end.strftime("%d %b %Y") if actual_end is not None else "…"
        range_label = f"{from_lbl} – {to_lbl}"
    else:
        range_label = None
    range_summary = (
        {
            "target": round(range_target, 2),
            "achieved": round(range_achieved, 2),
            "achievementPct": range_pct,
            "periodLabel": range_label,
            "sitesWithTarget": sites_with_target,
            "sitesWithAchieved": sites_with_achieved,
        }
        if range_target > 0
        else None
    )

    return _remember({
        "ok": True,
        "filters": {
            "site_id": site_id,
            "channel": channel,
            "period": period,
            "count": n,
            "date_from": df_from or None,
            "date_to": df_to or None,
        },
        "site_options": site_options[:2000],
        "channel_options": channel_options,
        "site_count": sites_with_target,
        "sites_with_target": sites_with_target,
        "sites_with_achieved": sites_with_achieved,
        "achievement_scope": (
            "Target LFS sites only — achieved = Sales-Master net sales for the same "
            "Site Codes (via site_manual_code), for available target dates in range"
        ),
        "sales_data_as_of": latest_date.strftime("%Y-%m-%d") if pd.notna(latest_date) else None,
        "current": current,
        "series": series,
        "range_summary": range_summary,
        "growth_analysis": {
            "excludes_current_period": True,
            "minimum_streak_for_increase": 3,
            "suggestion_rule": "Half of the latest 3-period over-achievement, capped at 25%.",
        },
        "growth_table": growth_table,
    })

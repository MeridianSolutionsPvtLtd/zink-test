"""
Year-aware sales frame loader for dashboards.

Indian FY (Apr–Mar): FY-27 = Apr 2026 – Mar 2027 → code 2027.
Default dashboard year = current Indian FY end year (dynamic).
"""
from __future__ import annotations

import logging
import hashlib
import os
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import HTTPException

logger = logging.getLogger(__name__)

# Floor for calendar-year filter / comparison (2024 → latest)
CALENDAR_YEAR_FILTER_START = 2024


def current_indian_fy_end(as_of: date | None = None) -> int:
    """Indian FY end calendar year for `as_of` (default: today). Apr→Mar → end year."""
    d = as_of or date.today()
    return int(d.year + 1) if d.month >= 4 else int(d.year)


def current_indian_fy_date_range(as_of: date | None = None) -> tuple[str, str]:
    """Inclusive FY date bounds YYYY-MM-DD for the current Indian FY."""
    fy_end = current_indian_fy_end(as_of)
    return f"{fy_end - 1}-04-01", f"{fy_end}-03-31"


def get_default_dashboard_year() -> int:
    return current_indian_fy_end()


def supported_dashboard_years() -> frozenset[int]:
    """FY codes we will attempt to load (2024 … current FY + 1)."""
    cur = current_indian_fy_end()
    return frozenset(range(CALENDAR_YEAR_FILTER_START, cur + 2))


def dashboard_fy_years_sorted() -> list[int]:
    """Sorted FY end-years for warm/publish/filter loops (never hardcode 2025–2027)."""
    return sorted(supported_dashboard_years())


def warm_fy_years_sorted() -> list[int]:
    """FY years to pre-warm (2024 … current Indian FY). Skips next empty FY."""
    cur = current_indian_fy_end()
    return list(range(CALENDAR_YEAR_FILTER_START, cur + 1))


# Earliest FY end year offered in filters. FY26 = 1 Apr 2025 – 31 Mar 2026.
# Older tokens (FY23, FY24, …) stay out of dropdowns; Sales Master has no rows there.
FY_FILTER_START = 2026


def visible_fy_years() -> list[int]:
    """FY end-years shown in filters: FY26 through the current Indian FY."""
    cur = current_indian_fy_end()
    start = min(FY_FILTER_START, cur)
    return list(range(start, cur + 1))


def analyzer_visible_years() -> list[int]:
    """FY end-years shown in Sales Pivot Analyzer filters (FY26 … current)."""
    return visible_fy_years()


def analyzer_build_years() -> list[int]:
    """
    FY end-years to build Analyzer prepared frames for.

    Includes the year before the oldest visible FY so LY / growth still have a frame.
    That prior year is not shown in filters.
    """
    visible = visible_fy_years()
    prior = visible[0] - 1
    return sorted({prior, *visible})


def fy_token(fy_end: int | None = None) -> str:
    """FY27-style label for an FY end year."""
    y = int(fy_end) if fy_end is not None else get_default_dashboard_year()
    return f"FY{str(y)[-2:]}"


# Back-compat aliases (resolved at import; prefer get_default_dashboard_year())
DEFAULT_YEAR = get_default_dashboard_year()
SUPPORTED_YEARS = supported_dashboard_years()

FY_ALIASES: dict[str, int] = {
    "FY24": 2024,
    "FY-24": 2024,
    "2023-24": 2024,
    "FY25": 2025,
    "FY-25": 2025,
    "2024-25": 2025,
    "FY26": 2026,
    "FY-26": 2026,
    "2025-26": 2026,
    "FY27": 2027,
    "FY-27": 2027,
    "2026-27": 2027,
    "FY28": 2028,
    "FY-28": 2028,
    "2027-28": 2028,
}


def is_all_years(year: Any) -> bool:
    """True for All Years / combined (0, 'all', 'all years')."""
    if year is None:
        return False
    if isinstance(year, (int, float)) and not isinstance(year, bool):
        return int(year) == 0
    if isinstance(year, str):
        return year.strip().lower() in ("0", "all", "all years", "combined")
    return False


def normalize_dashboard_year(year: Any, default: int | None = None) -> int:
    """Coerce query year or FY string to an FY end-year code."""
    fallback = default if default is not None else get_default_dashboard_year()
    allowed = supported_dashboard_years()
    if year is None:
        return fallback
    if is_all_years(year):
        # Callers that need All Years must use load_sales_for_year(0) / is_all_years().
        return fallback
    if isinstance(year, str):
        cleaned = year.strip().upper()
        if cleaned in FY_ALIASES:
            return FY_ALIASES[cleaned]
        # FY27 / FY2027
        import re

        m = re.match(r"^FY-?(\d{2,4})$", cleaned.replace(" ", ""))
        if m:
            n = int(m.group(1))
            y = n if n >= 1000 else 2000 + n
            return y if y in allowed else fallback
    try:
        y = int(year)
    except (TypeError, ValueError):
        return fallback
    if y in allowed:
        return y
    return fallback


_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_API_CACHE_DIR = _BACKEND_ROOT / "data" / "api_cache"

_YEAR_CACHE: dict[int, tuple[str, pd.DataFrame, float]] = {}
_YEAR_LOCK = threading.Lock()

_MASTER_USECOLS = [
    "STORE CODE",
    "TRANSACTION_SITE",
    "ENTRY_DATE",
    "BARCODE",
    "CATEGORY3",
    "CATEGORY5",
    "CATEGORY6",
    "QUANTITY",
    "RSP",
    "NET_AMOUNT",
    "ITEM DISCOUNT_AMOUNT",
    "TAX_AMOUNT",
    "Department",
    "Section",
]


def _sales_master_paths() -> list[Path]:
    return [
        _BACKEND_ROOT / "pipeline" / "input_files" / "SSI_PT_CENTRO_ICONIC_EBO_SALES_MASTER.csv",
        _BACKEND_ROOT / "pipeline" / "input_files" / "SSI_PT_CENTRO_ICONIC_SALES_MASTER.csv",
        _BACKEND_ROOT / "input" / "SSI_PT_CENTRO_ICONIC_EBO_SALES_MASTER.csv",
        _BACKEND_ROOT / "input" / "SSI_PT_CENTRO_ICONIC_SALES_MASTER.csv",
        _BACKEND_ROOT / "input_files" / "SSI_PT_CENTRO_ICONIC_EBO_SALES_MASTER.csv",
        _BACKEND_ROOT / "input_files" / "SSI_PT_CENTRO_ICONIC_SALES_MASTER.csv",
    ]


_BLOB_SALES_MASTER_CANDIDATES = (
    "Sales-Master (PT, SSL, Centro, Iconic, EBO)/current/SSI_PT_CENTRO_ICONIC_EBO_SALES_MASTER.csv",
    "Sales-Master (PT, SSL, Centro, Iconic, EBO)/current/SSI_PT_CENTRO_ICONIC_EBO_SALES_MASTER.xlsx",
    "Sales-Master (PT, SSL, Centro, Iconic, EBO)/current/SSI_PT_CENTRO_ICONIC_SALES_MASTER.csv",
    "Sales-Master (PT, SSL, Centro, Iconic, EBO)/current/SSI_PT_CENTRO_ICONIC_SALES_MASTER.xlsx",
    "Sales-Master (PT, SSL, Centro, Iconic)/current/SSI_PT_CENTRO_ICONIC_SALES_MASTER.csv",
    "Sales-Master (PT, SSL, Centro, Iconic)/current/SSI_PT_CENTRO_ICONIC_SALES_MASTER.xlsx",
    "Sales-Master (PT, SSL, Centro, Iconic)/current/SSI_PT_CENTRO_ICONIC_SALES_MASTER.xls",
)


def _find_sales_master_from_blob() -> Path | None:
    """Prefer Sales-Master from Azure blob current/ (hosted App Service has no local input_files)."""
    try:
        from api.routes.sales_dashboard import _blob_store

        store = _blob_store()
        if store is None:
            return None
        return store.ensure_input_candidates(_BLOB_SALES_MASTER_CANDIDATES)
    except Exception as exc:
        logger.warning("Sales-Master blob read skipped: %s", exc)
        return None


_BUNDLE_MISS_UNTIL = 0.0


def _bundle_year_masters() -> Path | None:
    """One local CSV of every calendar-year Sales Master file, when the split is complete."""
    global _BUNDLE_MISS_UNTIL
    if time.time() < _BUNDLE_MISS_UNTIL:
        return None
    try:
        from pipeline.year_stores import ensure_year_files_local, live_calendar_year
    except Exception:
        return None
    years = list(range(2024, live_calendar_year() + 1))
    paths = ensure_year_files_local("sales", years)
    if len(paths) != len(years):
        _BUNDLE_MISS_UNTIL = time.time() + 60
        return None
    stamp = "|".join(_mtime_key(p) for p in paths)
    _API_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out = _API_CACHE_DIR / "sales_master_year_bundle.csv"
    stamp_path = _API_CACHE_DIR / "sales_master_year_bundle.stamp"
    if out.exists() and out.stat().st_size > 0 and stamp_path.exists() and stamp_path.read_text(encoding="utf-8") == stamp:
        return out
    with out.open("wb") as dest:
        first = True
        for path in paths:
            with path.open("rb") as src:
                if first:
                    dest.write(src.read())
                    first = False
                    continue
                src.readline()
                dest.write(src.read())
    stamp_path.write_text(stamp, encoding="utf-8")
    logger.info("Sales Master year bundle written (%s files)", len(paths))
    return out


def resolve_sales_master_path() -> Path | None:
    """Freshest Sales Master (blob current/ vs local). Never raises — dashboard can use ready cache."""
    local = next((p for p in _sales_master_paths() if p.exists() and p.stat().st_size > 0), None)
    blob_master = _find_sales_master_from_blob()
    if blob_master is not None and local is not None:
        try:
            # Stale local input_files must not win over a newer Azure current/ dump.
            if blob_master.stat().st_mtime >= local.stat().st_mtime or blob_master.stat().st_size > local.stat().st_size:
                return blob_master
        except OSError:
            return blob_master
        return local
    if blob_master is not None:
        return blob_master
    if local is not None:
        return local
    bundled = _bundle_year_masters()
    if bundled is not None:
        return bundled
    logger.warning("Sales-Master file unavailable; screens will use the ready sales cache")
    return None


def _mtime_key(path: Path) -> str:
    try:
        return f"{path}:{os.path.getmtime(path)}"
    except OSError:
        return str(path)


def _parquet_path(year: int, source_key: str) -> Path:
    safe = (
        source_key.replace(":", "_")
        .replace("/", "_")
        .replace("\\", "_")
        .replace('"', "")
        .replace("'", "")
    )[:160]
    digest = hashlib.sha256(source_key.encode()).hexdigest()[:16]
    return _API_CACHE_DIR / f"sales_year_{year}_{safe[:100]}_{digest}_v3.parquet"


def _load_parquet(year: int, source_key: str) -> pd.DataFrame | None:
    path = _parquet_path(year, source_key)
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path)
        logger.info("sales_year_loader: year=%s parquet cache hit (%s rows)", year, len(df))
        return df
    except Exception as exc:
        logger.warning("sales_year_loader: parquet read failed: %s", exc)
        return None


def _save_parquet(df: pd.DataFrame, year: int, source_key: str) -> None:
    try:
        _API_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        df.to_parquet(_parquet_path(year, source_key), index=False)
        logger.info("sales_year_loader: year=%s parquet saved (%s rows)", year, len(df))
    except Exception as exc:
        logger.warning("sales_year_loader: parquet write failed: %s", exc)


def _normalize_raw(raw: pd.DataFrame):
    from api.routes.sales_dashboard import _normalize_columns

    return _normalize_columns(raw)


def _filter_calendar_year(df: pd.DataFrame, year: int) -> pd.DataFrame:
    if df is None or df.empty or "date" not in df.columns:
        return df
    return df[df["date"].dt.year == int(year)].copy()


def _filter_financial_year(df: pd.DataFrame, year: int) -> pd.DataFrame:
    if df is None or df.empty or "date" not in df.columns:
        return df
    return df[df["date"].ge(pd.Timestamp(year - 1, 4, 1)) & df["date"].lt(pd.Timestamp(year, 4, 1))].copy()


def _year_folder_paths(fy_end: int, *, refresh: bool = False) -> list[Path]:
    """
    Files for one financial year (Apr–Mar).

    FY27 → Sales-Master/.../2026/current/SSI_PT_CENTRO_ICONIC_EBO_SALES_MASTER.csv
            and .../2027/current/... when that folder exists.
    FY26 → 2025 + 2026. FY25 → 2024 + 2025.
    """
    try:
        from pipeline.year_stores import calendar_years_for_fy, ensure_year_files_local

        return ensure_year_files_local(
            "sales", calendar_years_for_fy(int(fy_end)), refresh=refresh
        )
    except Exception as exc:
        logger.warning("sales year folders unavailable for FY%s: %s", fy_end, exc)
        return []


def sales_data_revision(year: int | Any = DEFAULT_YEAR) -> str:
    """Etag/mtime fingerprint of FY source files — busts page-cache when blob Sales Master changes.

    Disk-only (no Azure). Calling ensure_year_files_local here was re-HEADing blob on
    every KPI request and saturating App Service.
    """
    try:
        from pipeline.year_stores import _cached_year_file, calendar_years_for_fy

        y = normalize_dashboard_year(year)
        paths: list[Path] = []
        for cy in calendar_years_for_fy(y):
            p = _cached_year_file("sales", cy)
            if p is not None:
                paths.append(p)
        if not paths:
            master = resolve_sales_master_path()
            if master is not None:
                paths = [master]
        parts: list[str] = []
        for p in paths:
            etag = Path(str(p) + ".etag")
            if etag.exists():
                parts.append(f"{p.name}:{etag.read_text(encoding='utf-8').strip()}")
            else:
                try:
                    parts.append(f"{p.name}:{int(p.stat().st_mtime_ns)}:{p.stat().st_size}")
                except OSError:
                    parts.append(p.name)
        return "|".join(parts) if parts else "none"
    except Exception:
        return "none"


def _load_sales_master_year(year: int, *, force_refresh: bool = False) -> pd.DataFrame:
    year_paths = _year_folder_paths(year, refresh=force_refresh)
    master = year_paths[0] if year_paths else resolve_sales_master_path()
    if master is None:
        raise HTTPException(
            status_code=404,
            detail="Sales-Master file not found. Upload Sales-Master for 2024/2025 data.",
        )

    sources = year_paths or [master]
    source_key = "|".join(_mtime_key(p) for p in sources)
    if not force_refresh:
        cached = _load_parquet(year, source_key)
        if cached is not None and not cached.empty:
            return cached

    logger.info(
        "sales_year_loader: FY%s from %s",
        year,
        ", ".join(str(p) for p in sources),
    )
    try:
        from pipeline.pipeline_inputs import parse_sales_dates
    except ImportError:
        from pipeline_inputs import parse_sales_dates  # type: ignore

    chunks: list[pd.DataFrame] = []
    for source in sources:
        header = pd.read_csv(source, nrows=0).columns.tolist()
        header_set = {str(c).strip() for c in header}
        date_col = next((c for c in ["Invoice Date", "ENTRY_DATE", "Date", "BILL DATE"] if c in header_set), None)
        site_col = next((c for c in ["Site Code", "STORE CODE", "Site_ID", "Site Short Code"] if c in header_set), None)
        if not date_col or not site_col:
            raise HTTPException(
                status_code=500,
                detail=f"Sales-Master missing date/site columns. Header: {header[:10]}",
            )
        for chunk in pd.read_csv(source, chunksize=250_000, low_memory=False):
            dates = parse_sales_dates(chunk[date_col])
            mask = dates.ge(pd.Timestamp(year - 1, 4, 1)) & dates.lt(pd.Timestamp(year, 4, 1))
            if not mask.any():
                continue
            part = chunk.loc[mask].copy()
            part[date_col] = dates.loc[mask]
            chunks.append(part)

    if not chunks:
        empty = pd.DataFrame()
        _save_parquet(empty, year, source_key)
        return empty

    raw = pd.concat(chunks, ignore_index=True)
    df = _normalize_raw(raw)
    df = _filter_financial_year(df, year)
    _save_parquet(df, year, source_key)
    return df


def _load_combined_year(year: int, *, force_refresh: bool = False) -> pd.DataFrame:
    from api.routes.sales_dashboard import _get_sales_df

    full = _get_sales_df(force_refresh=force_refresh)
    return _filter_calendar_year(full, year)


def _slice_ready_overview(year: int) -> pd.DataFrame | None:
    """FY slice of cache/sales-overview/dashboard.parquet. None if blob cache is absent."""
    try:
        from api.routes.sales_dashboard import load_ready_overview_frame
    except Exception as exc:
        logger.warning("ready overview cache import failed: %s", exc)
        return None
    ready = load_ready_overview_frame()
    if ready is None or ready.empty:
        return None
    fy_str = f"FY {int(year) - 1}-{str(int(year))[-2:]}"
    sliced: pd.DataFrame | None = None
    if "fy" in ready.columns:
        sliced = ready[ready["fy"].astype(str).str.strip().eq(fy_str)]
    if (sliced is None or sliced.empty) and "financial_year" in ready.columns:
        sliced = ready[ready["financial_year"].astype(str).str.strip().eq(f"FY{str(int(year))[-2:]}")]
    if sliced is None or sliced.empty:
        sliced = _filter_financial_year(ready, int(year))
    if sliced is None or sliced.empty:
        logger.warning("ready overview cache has no rows for FY%s", year)
        return None
    logger.info("sales_year_loader: FY%s from ready blob cache (%s rows)", year, len(sliced))
    return sliced.copy()


def sanitize_store_channels(df: pd.DataFrame) -> pd.DataFrame:
    """Authoritative store channel sanitization for all retail frames."""
    if df is None or df.empty:
        return df

    out = df
    for col in ("channel", "sales_channel", "store_type", "site_id", "site_name", "site_id_name", "customer_name"):
        if col in out.columns and isinstance(out[col].dtype, pd.CategoricalDtype):
            out[col] = out[col].astype(str)

    site_str = (out["site_id"].astype(str) if "site_id" in out.columns else pd.Series("", index=out.index)).str.strip().str.upper()
    name_str = (out["site_name"].astype(str) if "site_name" in out.columns else pd.Series("", index=out.index)).str.strip().str.upper()
    comb = site_str + " " + name_str

    is_ssl = comb.str.contains(r"SHOPPERS?|HOPPER|\bSS[- ]|\bSSL\b", regex=True, na=False)
    is_pt = comb.str.contains(r"PANTALOON|\bPT[- ]|\bPW[- ]|\bPT\b|^P\d{2,5}\b|ADITYA BIRLA", regex=True, na=False) & ~is_ssl
    is_centro = comb.str.contains(r"CENTRO|\bRRL[- ]|^TC3|^T8|RELIANCE", regex=True, na=False) & ~is_ssl & ~is_pt
    is_iconic = comb.str.contains(r"ICONIC", regex=True, na=False) & ~is_ssl & ~is_pt
    # Whole-token EBO codes only — "528" must not match SSL store "8528".
    is_ebo = comb.str.contains(r"EBO|FRESCO|^FGPL|\b(?:528|964|972)\b", regex=True, na=False) & ~is_ssl & ~is_pt

    if "channel" not in out.columns:
        out["channel"] = ""
    if "sales_channel" not in out.columns:
        out["sales_channel"] = ""

    out.loc[is_ssl, "channel"] = "SSL"
    out.loc[is_ssl, "sales_channel"] = "Shoppers Stop (SSL)"

    out.loc[is_pt, "channel"] = "PT"
    out.loc[is_pt, "sales_channel"] = "Pantaloons (PT)"

    out.loc[is_centro, "channel"] = "CENTRO"
    out.loc[is_centro, "sales_channel"] = "Centro"

    out.loc[is_iconic, "channel"] = "ICONIC"
    out.loc[is_iconic, "sales_channel"] = "Iconic"

    out.loc[is_ebo, "channel"] = "EBO"
    out.loc[is_ebo, "sales_channel"] = "EBO"

    if "site_id_name" not in out.columns:
        if "site_id" in out.columns and "site_name" in out.columns:
            out["site_id_name"] = out["site_id"].fillna("").astype(str).str.strip() + " - " + out["site_name"].fillna("").astype(str).str.strip()
            out["site_id_name"] = out["site_id_name"].str.strip(" -")

    return out


def load_sales_for_year(
    year: int | Any = DEFAULT_YEAR,
    *,
    force_refresh: bool = False,
) -> pd.DataFrame:
    """
    Return normalized, CM-enriched sales rows for a financial year.
    Fast path: in-memory → FY parquet → COMBINED_SALES slice → Sales-Master CSV.
    Pass year=0 / "all" to concatenate every supported FY (2024 → current).
    """
    if is_all_years(year):
        cache_key = 0
        if not force_refresh:
            hit = _YEAR_CACHE.get(cache_key)
            if hit is not None and hit[1] is not None and not getattr(hit[1], "empty", True):
                return hit[1]
        parts: list[pd.DataFrame] = []
        for fy in sorted(supported_dashboard_years()):
            try:
                part = load_sales_for_year(fy, force_refresh=force_refresh)
                if part is not None and not part.empty:
                    parts.append(part)
            except Exception as exc:
                logger.warning("sales_year_loader: All Years skip FY%s: %s", fy, exc)
        if not parts:
            empty = pd.DataFrame()
            _YEAR_CACHE[cache_key] = ("all_years_empty", empty, time.time())
            return empty
        out = pd.concat(parts, ignore_index=True) if len(parts) > 1 else parts[0]
        if "date" in out.columns:
            out = out.drop_duplicates(
                subset=[
                    c
                    for c in ("date", "site_id", "barcode", "sku", "net_sale_amount", "qty")
                    if c in out.columns
                ],
                keep="first",
            )
        _YEAR_CACHE[cache_key] = ("all_years", out, time.time())
        logger.info("sales_year_loader: All Years → %s rows from %s FY slices", len(out), len(parts))
        return out

    y = normalize_dashboard_year(year)
    year_paths = _year_folder_paths(y, refresh=force_refresh)
    master = year_paths[0] if year_paths else resolve_sales_master_path()
    source_key = (
        "|".join(_mtime_key(p) for p in year_paths)
        if year_paths
        else (_mtime_key(master) if master else "combined_sales")
    )

    # ── 0. In-memory cache (fastest) — only if source files are unchanged.
    # Hosted used to return the first FY frame forever even after Sales Master
    # was replaced, so Home KPIs stayed on yesterday's numbers.
    if not force_refresh:
        hit = _YEAR_CACHE.get(y)
        if (
            hit is not None
            and hit[0] == source_key
            and hit[1] is not None
            and not getattr(hit[1], "empty", True)
        ):
            return hit[1]

    # ── 1. FY-specific parquet cache (built by sales_dashboard pre-cacher) ───
    #   Key: use mtime of the resolved master file
    if not force_refresh:
        cached = _load_parquet(y, source_key)
        if cached is not None and not cached.empty:
            cached = sanitize_store_channels(cached)
            _YEAR_CACHE[y] = (source_key, cached, time.time())
            logger.info("sales_year_loader: year=%s served from FY parquet cache (%s rows)", y, len(cached))
            return cached

        # Never slice sales-overview/dashboard.parquet when year folders exist —
        # flat current/ often lags (ENTRY_DATE) and would pin FY max to ~Sep.
        if not year_paths:
            ready = _slice_ready_overview(y)
            if ready is not None and not ready.empty:
                ready = sanitize_store_channels(ready)
                _save_parquet(ready, y, source_key)
                _YEAR_CACHE[y] = (source_key, ready, time.time())
                return ready

    # ── 2. Year folders for this FY (Home + Sales Dashboard) ────────────────
    #    Skip the full current/ file when 2024/2025/2026 folders are present.
    if year_paths:
        with _YEAR_LOCK:
            hit = _YEAR_CACHE.get(y)
            if hit is not None and hit[0] == source_key and not force_refresh:
                return hit[1]
            df = _load_sales_master_year(y, force_refresh=force_refresh)
            df = sanitize_store_channels(df)
            _YEAR_CACHE[y] = (source_key, df, time.time())
            return df

    # ── 3. Slice from the full Sales Master frame ───────────────────────────
    try:
        from api.routes.sales_dashboard import _get_sales_df
        full = _get_sales_df(force_refresh=force_refresh)
        if full is not None and not full.empty:
            fy_str = f"FY {str(y-1)}-{str(y)[-2:]}"
            sliced: pd.DataFrame | None = None
            if "fy" in full.columns:
                sliced = full[full["fy"].astype(str).str.strip().eq(fy_str)].copy()
            if (sliced is None or sliced.empty) and "financial_year" in full.columns:
                fy_label = f"FY{str(y)[-2:]}"
                sliced = full[full["financial_year"].astype(str).str.strip().eq(fy_label)].copy()
            if sliced is None or sliced.empty:
                sliced = _filter_financial_year(full, y)
            if sliced is not None and not sliced.empty:
                sliced = sanitize_store_channels(sliced)
                # Persist as FY parquet so next startup is instant
                _save_parquet(sliced, y, source_key)
                _YEAR_CACHE[y] = (source_key, sliced, time.time())
                return sliced
    except Exception as exc:
        logger.warning("sales_year_loader: failed to slice COMBINED_SALES for year %s: %s", y, exc)

    # ── 3. Fallback: read from Sales-Master CSV (slow) ───────────────────────
    mem_key = source_key
    with _YEAR_LOCK:
        # double-check inside lock
        hit = _YEAR_CACHE.get(y)
        if hit is not None and hit[0] == source_key and not force_refresh:
            return hit[1]

        if force_refresh and master is not None:
            try:
                p = _parquet_path(y, source_key)
                if p.exists():
                    p.unlink()
            except OSError:
                pass

        df = _load_sales_master_year(y)
        df = sanitize_store_channels(df)
        _YEAR_CACHE[y] = (mem_key, df, time.time())
        return df

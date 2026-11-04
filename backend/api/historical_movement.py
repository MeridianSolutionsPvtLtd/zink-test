"""
Store movement for sell-through.

Sell-through % = sales qty / (opening SOH + receipts) × 100

opening = receipts − sales − returns-out before the period starts
receipts = warehouse invoice qty, dated invoice date + 2 transit days,
           plus stock transferred into the store
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parents[1]
INPUT_DIR = BACKEND_DIR / "pipeline" / "input_files"
OUT_DIR = INPUT_DIR.parent / "output_files" / "historical_soh"
DAILY_PATH = OUT_DIR / "daily_movement.parquet"
CACHE_DIR = BACKEND_DIR / "data" / "api_cache"
CACHE_DAILY_PATH = CACHE_DIR / "daily_movement.parquet"
CACHE_STAMP_PATH = CACHE_DIR / "daily_movement.stamp"

SALES_PATH = INPUT_DIR / "SSI_PT_CENTRO_ICONIC_EBO_SALES_MASTER.csv"
TRANSFER_PATH = INPUT_DIR / "SALES_INVOICE_TRACKING_MASTER.csv"
RTV_PATH = INPUT_DIR / "RTV_DATA.csv"
BUNDLE_PATH = CACHE_DIR / "sales_master_year_bundle.csv"

TRANSIT_DAYS = 2
_PAREN = re.compile(r"\(([^)]+)\)")
_TIME_DIMS = frozenset({"month", "fy_week", "date", "financial_year", "year"})
_STORE_DIMS = frozenset({
    "site_id",
    "site_name",
    "site_id_name",
    "zone",
    "sales_channel",
    "city_name",
    "state_name",
    "store_grade",
    "cluster",
    "manager",  # same RM roster as cluster — needed for Manager → Store sell-through
    "customer_name",
})
MOVEMENT_DIMS = _STORE_DIMS | _TIME_DIMS

_DAILY_CACHE: Optional[pd.DataFrame] = None
_DAILY_STAMP: Optional[str] = None


def historical_str(qty: float, opening: Optional[float], receipts: Optional[float]) -> Optional[float]:
    """sales / (opening + receipts) × 100. Blank when the denominator is not positive."""
    if opening is None or receipts is None:
        return None
    try:
        denom = max(0.0, float(opening)) + max(0.0, float(receipts))
    except (TypeError, ValueError):
        return None
    if denom <= 0:
        return None
    return round(float(qty) / denom * 100.0, 1)


_SITE_KEY_MEMO: dict[str, str] = {}


def _extract_single_site_key(val: Any) -> str:
    if val is None:
        return ""
    val_str = str(val).strip().upper()
    if not val_str or val_str in ("", "NAN", "NONE", "NULL", "NAT", "<NA>"):
        return ""
    if val_str in _SITE_KEY_MEMO:
        return _SITE_KEY_MEMO[val_str]

    clean = val_str
    if clean.endswith(".0"):
        clean = clean[:-2]

    m = _PAREN.search(clean)
    if m:
        extracted = re.sub(r"\s+", "", m.group(1))
        if extracted and extracted not in ("NAN", "NONE", "NULL", "NAT"):
            _SITE_KEY_MEMO[val_str] = extracted
            return extracted

    m_tail = re.search(r"([A-Z0-9]+)\s*$", clean)
    if m_tail:
        extracted = m_tail.group(1)
        if extracted and extracted not in ("NAN", "NONE", "NULL", "NAT"):
            _SITE_KEY_MEMO[val_str] = extracted
            return extracted

    _SITE_KEY_MEMO[val_str] = ""
    return ""


def site_key_series(values: pd.Series) -> pd.Series:
    """Store code inside parentheses, otherwise the last token (106, P001, KAYBEES)."""
    if values is None or values.empty:
        return pd.Series("", index=getattr(values, "index", None), dtype=object)
    uniques = values.dropna().unique()
    mapping = {u: _extract_single_site_key(u) for u in uniques}
    return values.map(mapping).fillna("")


def frame_site_keys(df: pd.DataFrame) -> pd.Series:
    if df is None or df.empty:
        return pd.Series(dtype=object)
    key = pd.Series("", index=df.index, dtype=object)
    for col in ("site_id", "site_code", "site_manual_code", "site_short_code", "site_name", "site_id_name"):
        if col not in df.columns:
            continue
        part = site_key_series(df[col])
        key = key.mask(key.eq("") & part.ne(""), part)
        if not (key == "").any():
            break
    return key


def frame_unique_site_keys(df: pd.DataFrame) -> set[str]:
    if df is None or df.empty:
        return set()
    keys = set()
    for col in ("site_id", "site_code", "site_manual_code", "site_short_code", "site_name", "site_id_name"):
        if col not in df.columns:
            continue
        for u in df[col].dropna().unique():
            k = _extract_single_site_key(u)
            if k:
                keys.add(k)
        if keys:
            break
    return keys


def _parse_dates(values: pd.Series, *, dayfirst: bool) -> pd.Series:
    return pd.to_datetime(values, errors="coerce", dayfirst=dayfirst).dt.normalize()


def _empty_daily() -> pd.DataFrame:
    return pd.DataFrame(columns=["site_key", "date", "receipts", "sales", "rtv_out"])


def _is_movement_ledger(daily: pd.DataFrame | None) -> bool:
    """True only for the site×day receipts ledger, not daily_inventory_movement."""
    return (
        daily is not None
        and not daily.empty
        and {"site_key", "date", "receipts"}.issubset(daily.columns)
    )


def _qty(frame: pd.DataFrame, name: str) -> pd.Series:
    if frame is None or name not in frame.columns:
        return pd.Series(0.0, index=getattr(frame, "index", None))
    return pd.to_numeric(frame[name], errors="coerce").fillna(0.0)


def _chunk_sum(parts: list[pd.DataFrame]) -> pd.DataFrame:
    if not parts:
        return _empty_daily()
    out = pd.concat(parts, ignore_index=True)
    return (
        out.groupby(["site_key", "date"], as_index=False)[["receipts", "sales", "rtv_out"]]
        .sum()
    )


def _sales_parts(path: Path, *, date_col: str, qty_col: str, site_cols: Iterable[str], dayfirst: bool, after: Optional[pd.Timestamp] = None) -> pd.DataFrame:
    if not path.exists():
        return _empty_daily()
    header = pd.read_csv(path, nrows=0).columns
    use = [c for c in [date_col, qty_col, *site_cols] if c in header]
    if date_col not in use or qty_col not in use:
        return _empty_daily()
    parts: list[pd.DataFrame] = []
    for chunk in pd.read_csv(path, usecols=use, dtype=str, chunksize=250_000):
        date = _parse_dates(chunk[date_col], dayfirst=dayfirst)
        if after is not None:
            keep = date.notna() & (date > after)
            chunk = chunk.loc[keep]
            date = date.loc[keep]
        if chunk.empty:
            continue
        key = pd.Series("", index=chunk.index, dtype=object)
        for col in site_cols:
            if col not in chunk.columns:
                continue
            part = site_key_series(chunk[col])
            key = key.mask(key.eq("") & part.ne(""), part)
        qty = pd.to_numeric(chunk[qty_col], errors="coerce").fillna(0.0).abs()
        ok = key.ne("") & date.notna() & qty.gt(0)
        if not ok.any():
            continue
        part_df = pd.DataFrame({
            "site_key": key[ok].to_numpy(),
            "date": date[ok].to_numpy(),
            "receipts": 0.0,
            "sales": qty[ok].to_numpy(),
            "rtv_out": 0.0,
        })
        parts.append(part_df.groupby(["site_key", "date"], as_index=False)[["receipts", "sales", "rtv_out"]].sum())
    return _chunk_sum(parts)


def _transfer_parts(path: Path) -> pd.DataFrame:
    if not path.exists():
        return _empty_daily()
    use = ["INVOICE_DATE", "SITE SHORT NAME", "SITE NAME", "INVOICE_QUANTITY"]
    parts: list[pd.DataFrame] = []
    for chunk in pd.read_csv(path, usecols=use, dtype=str, chunksize=250_000):
        date = _parse_dates(chunk["INVOICE_DATE"], dayfirst=True) + pd.Timedelta(days=TRANSIT_DAYS)
        key = site_key_series(chunk["SITE SHORT NAME"])
        name_key = site_key_series(chunk["SITE NAME"])
        key = key.mask(key.eq("") & name_key.ne(""), name_key)
        qty = pd.to_numeric(chunk["INVOICE_QUANTITY"], errors="coerce").fillna(0.0).abs()
        ok = key.ne("") & date.notna() & qty.gt(0)
        if not ok.any():
            continue
        part_df = pd.DataFrame({
            "site_key": key[ok].to_numpy(),
            "date": date[ok].to_numpy(),
            "receipts": qty[ok].to_numpy(),
            "sales": 0.0,
            "rtv_out": 0.0,
        })
        parts.append(part_df.groupby(["site_key", "date"], as_index=False)[["receipts", "sales", "rtv_out"]].sum())
    return _chunk_sum(parts)


def _rtv_parts(path: Path) -> pd.DataFrame:
    """
    Supports both RTV shapes:
      - Transfer-like: OUTSITE / INSITE / STFQTY
      - Ginesys sales-return: CUSTOMER SITE NAME / RETURN_QUANTITY
    """
    if not path.exists():
        return _empty_daily()
    header = list(pd.read_csv(path, nrows=0).columns)
    date_col = "SALES RETURN DATE" if "SALES RETURN DATE" in header else None
    qty_col = (
        "RETURN_QUANTITY"
        if "RETURN_QUANTITY" in header
        else ("STFQTY" if "STFQTY" in header else None)
    )
    site_out = (
        "OUTSITE"
        if "OUTSITE" in header
        else ("CUSTOMER SITE NAME" if "CUSTOMER SITE NAME" in header else None)
    )
    if not date_col or not qty_col or not site_out:
        return _empty_daily()
    use = [date_col, qty_col, site_out] + (["INSITE"] if "INSITE" in header else [])
    parts: list[pd.DataFrame] = []
    for chunk in pd.read_csv(path, usecols=use, dtype=str, chunksize=250_000):
        date = _parse_dates(chunk[date_col], dayfirst=False)
        qty = pd.to_numeric(chunk[qty_col], errors="coerce").fillna(0.0).abs()
        out_key = site_key_series(chunk[site_out])
        ok_out = out_key.ne("") & date.notna() & qty.gt(0)
        frames = []
        if ok_out.any():
            frames.append(pd.DataFrame({
                "site_key": out_key[ok_out].to_numpy(),
                "date": date[ok_out].to_numpy(),
                "receipts": 0.0,
                "sales": 0.0,
                "rtv_out": qty[ok_out].to_numpy(),
            }))
        if "INSITE" in chunk.columns:
            in_key = site_key_series(chunk["INSITE"])
            ok_in = in_key.ne("") & date.notna() & qty.gt(0)
            if ok_in.any():
                frames.append(pd.DataFrame({
                    "site_key": in_key[ok_in].to_numpy(),
                    "date": date[ok_in].to_numpy(),
                    "receipts": qty[ok_in].to_numpy(),
                    "sales": 0.0,
                    "rtv_out": 0.0,
                }))
        if frames:
            parts.append(pd.concat(frames, ignore_index=True).groupby(["site_key", "date"], as_index=False)[["receipts", "sales", "rtv_out"]].sum())
    return _chunk_sum(parts)


def _source_stamp() -> str:
    bits = []
    for path in (SALES_PATH, TRANSFER_PATH, RTV_PATH, BUNDLE_PATH):
        if path.exists():
            stat = path.stat()
            bits.append(f"{path.name}:{stat.st_size}:{stat.st_mtime_ns}")
    return "|".join(bits)


def build_daily_movement() -> pd.DataFrame:
    """Rebuild the site × day ledger from the local masters."""
    sales = _sales_parts(
        SALES_PATH,
        date_col="ENTRY_DATE",
        qty_col="Qty",
        site_cols=("Site Code", "Site"),
        dayfirst=False,
    )
    after = None
    if not sales.empty:
        after = pd.Timestamp(sales["date"].max())
    extra = _sales_parts(
        BUNDLE_PATH,
        date_col="Invoice Date",
        qty_col="Qty",
        site_cols=("Site Code", "Site"),
        dayfirst=False,
        after=after,
    )
    transfer = _transfer_parts(TRANSFER_PATH)
    rtv = _rtv_parts(RTV_PATH)
    daily = _chunk_sum([sales, extra, transfer, rtv])
    daily = daily[daily["site_key"].ne("")].copy()
    daily["date"] = pd.to_datetime(daily["date"], errors="coerce")
    daily = daily.dropna(subset=["date"]).sort_values(["site_key", "date"]).reset_index(drop=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    daily.to_parquet(DAILY_PATH, index=False)
    daily.to_parquet(CACHE_DAILY_PATH, index=False)
    stamp_txt = _source_stamp()
    (OUT_DIR / "daily_movement.stamp").write_text(stamp_txt, encoding="utf-8")
    (CACHE_DIR / "daily_movement.stamp").write_text(stamp_txt, encoding="utf-8")
    log.info(
        "Movement ledger %s rows, receipts=%s sales=%s → %s",
        f"{len(daily):,}",
        f"{daily['receipts'].sum():,.0f}",
        f"{daily['sales'].sum():,.0f}",
        DAILY_PATH,
    )
    return daily


def load_daily_movement() -> pd.DataFrame:
    """Cached site × day movement. Auto-rebuilds when source sales/transfer files are updated."""
    global _DAILY_CACHE, _DAILY_STAMP
    current_stamp = _source_stamp()
    if _DAILY_CACHE is not None and _DAILY_STAMP == current_stamp:
        return _DAILY_CACHE
    
    # 1. Check output_files or api_cache daily_movement.parquet
    for p_file, s_file in ((DAILY_PATH, OUT_DIR / "daily_movement.stamp"), (CACHE_DAILY_PATH, CACHE_STAMP_PATH)):
        if p_file.exists():
            try:
                # If stamp matches or if running in packaged environment without raw CSVs
                if not s_file.exists() or s_file.read_text(encoding="utf-8").strip() == current_stamp or (not SALES_PATH.exists() and not TRANSFER_PATH.exists()):
                    daily = pd.read_parquet(p_file)
                    daily["date"] = pd.to_datetime(daily["date"], errors="coerce")
                    if not _is_movement_ledger(daily):
                        log.warning(
                            "Skipping %s — missing site_key/receipts (not a movement ledger)",
                            p_file.name,
                        )
                        continue
                    _DAILY_CACHE = daily
                    _DAILY_STAMP = current_stamp
                    log.info("load_daily_movement hit %s (%d rows)", p_file.name, len(daily))
                    return daily
            except Exception as exc:
                log.warning("daily_movement read %s failed: %s", p_file, exc)
    
    if not SALES_PATH.exists() and not TRANSFER_PATH.exists():
        # Fallback to daily_inventory_movement.parquet if present in api_cache
        alt_daily = CACHE_DIR / "daily_inventory_movement.parquet"
        if alt_daily.exists():
            try:
                daily = pd.read_parquet(alt_daily)
                if _is_movement_ledger(daily):
                    daily["date"] = pd.to_datetime(daily["date"], errors="coerce")
                    _DAILY_CACHE = daily
                    _DAILY_STAMP = current_stamp
                    log.info("load_daily_movement hit fallback %s (%d rows)", alt_daily.name, len(daily))
                    return daily
                log.warning(
                    "Ignoring %s for sell-through ledger — columns are inventory movement, not receipts",
                    alt_daily.name,
                )
            except Exception:
                pass
        # Remember the miss. Re-reading this parquet on every STR cell took ~4s x 4.
        _DAILY_CACHE = _empty_daily()
        _DAILY_STAMP = current_stamp
        return _DAILY_CACHE
    daily = build_daily_movement()
    _DAILY_CACHE = daily
    _DAILY_STAMP = current_stamp
    return daily


def _net(frame: pd.DataFrame) -> pd.Series:
    return _qty(frame, "receipts") - _qty(frame, "sales") - _qty(frame, "rtv_out")


_FY_ANCHOR = pd.Timestamp("2026-04-01")


def opening_by_site(daily: pd.DataFrame, before: pd.Timestamp) -> pd.Series:
    if not _is_movement_ledger(daily) or before <= _FY_ANCHOR:
        return pd.Series(dtype=float)
    pre = daily[(daily["date"] >= _FY_ANCHOR) & (daily["date"] < before)]
    if pre.empty:
        return pd.Series(dtype=float)
    # Stock cannot be negative. Extra sales before the receipt history starts at zero.
    return _net(pre).groupby(pre["site_key"]).sum().clip(lower=0.0)


def receipts_by_site(daily: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    if not _is_movement_ledger(daily):
        return pd.Series(dtype=float)
    effective_start = max(pd.Timestamp(start).normalize(), _FY_ANCHOR)
    win = daily[(daily["date"] >= effective_start) & (daily["date"] <= end)]
    if win.empty or "site_key" not in win.columns:
        return pd.Series(dtype=float)
    return _qty(win, "receipts").groupby(win["site_key"]).sum()


def _label_dates(dates: pd.Series) -> pd.DataFrame:
    dt = pd.to_datetime(dates, errors="coerce")
    fy_year = np.where(dt.dt.month >= 4, dt.dt.year + 1, dt.dt.year)
    fy = pd.Series(fy_year, index=dates.index)
    out = pd.DataFrame(index=dates.index)
    out["year"] = dt.dt.year
    out["month"] = dt.dt.strftime("%b")
    out["date"] = dt.dt.normalize()
    out["financial_year"] = "FY" + fy.fillna(0).astype(int).astype(str).str[-2:]
    try:
        from api.financial_calendar import load_calendar

        mapping = load_calendar().set_index("Date")["week_key"]
        out["fy_week"] = dt.dt.normalize().map(mapping).fillna("Unmapped")
    except Exception:
        out["fy_week"] = "Unmapped"
    return out


_TOTALS_CACHE: dict[tuple, tuple[Optional[float], Optional[float]]] = {}


def movement_totals(start, end, site_keys: Optional[Iterable[str]] = None) -> tuple[Optional[float], Optional[float]]:
    daily = load_daily_movement()
    if not _is_movement_ledger(daily):
        return None, None
    start_ts = pd.Timestamp(start).normalize()
    end_ts = pd.Timestamp(end).normalize()
    if site_keys is None:
        key_part: tuple | None = None
    else:
        key_part = tuple(sorted({str(k) for k in site_keys if str(k)}))
    cache_key = (str(start_ts.date()), str(end_ts.date()), key_part)
    cached = _TOTALS_CACHE.get(cache_key)
    if cached is not None:
        return cached
    # Reuse site-window frame cache shared with group_opening_receipts.
    parts = _site_window_frame(daily, start_ts, end_ts)
    if key_part is not None:
        keys = set(key_part)
        parts = parts[parts["site_key"].astype(str).isin(keys)]
    result = (
        float(parts["opening"].sum()) if not parts.empty else 0.0,
        float(parts["receipts"].sum()) if not parts.empty else 0.0,
    )
    if len(_TOTALS_CACHE) > 32:
        _TOTALS_CACHE.clear()
    _TOTALS_CACHE[cache_key] = result
    return result


def movement_totals_for_frame(df: pd.DataFrame) -> tuple[Optional[float], Optional[float]]:
    if df is None or df.empty or "date" not in df.columns:
        return None, None
    date_col = df["date"]
    if pd.api.types.is_datetime64_any_dtype(date_col):
        min_d = date_col.min()
        max_d = date_col.max()
    else:
        # Avoid parsing 1.1M rows if min/max can be extracted from sample or unique dates
        uniques = date_col.dropna().unique()
        if len(uniques) == 0:
            return None, None
        parsed_uniques = pd.to_datetime(uniques, errors="coerce")
        min_d = parsed_uniques.min()
        max_d = parsed_uniques.max()
    if pd.isna(min_d) or pd.isna(max_d):
        return None, None
    return movement_totals(min_d, max_d, frame_unique_site_keys(df))


_SITE_WINDOW_CACHE: dict[tuple[str, str, str], pd.DataFrame] = {}
_SITE_WINDOW_CACHE_MAX = 8


def _site_window_frame(daily: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    start_ts = pd.Timestamp(start).normalize()
    end_ts = pd.Timestamp(end).normalize()
    cache_key = (
        str(_DAILY_STAMP or id(daily)),
        start_ts.strftime("%Y-%m-%d"),
        end_ts.strftime("%Y-%m-%d"),
    )
    hit = _SITE_WINDOW_CACHE.get(cache_key)
    if hit is not None:
        return hit

    opening = opening_by_site(daily, start_ts).rename("opening")
    receipts = receipts_by_site(daily, start_ts, end_ts).rename("receipts")
    out = pd.concat([opening, receipts], axis=1).fillna(0.0).reset_index()
    if "site_key" not in out.columns:
        out = out.rename(columns={out.columns[0]: "site_key"})
    if len(_SITE_WINDOW_CACHE) >= _SITE_WINDOW_CACHE_MAX:
        _SITE_WINDOW_CACHE.clear()
    _SITE_WINDOW_CACHE[cache_key] = out
    return out


def group_opening_receipts(
    sales_df: pd.DataFrame,
    group_cols: list[str],
    start,
    end,
) -> Optional[pd.DataFrame]:
    """Sum opening and receipts onto the same group keys as the sales pivot."""
    if not group_cols or any(col not in MOVEMENT_DIMS for col in group_cols):
        return None
    if sales_df is None or sales_df.empty:
        return None
    daily = load_daily_movement()
    if not _is_movement_ledger(daily):
        return None
    start_ts = pd.Timestamp(start).normalize()
    end_ts = pd.Timestamp(end).normalize()
    attr_cols = [c for c in group_cols if c in _STORE_DIMS and c in sales_df.columns]
    # Avoid copying the full sales frame (250k+ rows) — only keys + needed attrs.
    work = sales_df.loc[:, attr_cols].copy() if attr_cols else pd.DataFrame(index=sales_df.index)
    work["_site_key"] = frame_site_keys(sales_df).values
    work = work[work["_site_key"].astype(str).str.strip().ne("")]
    if work.empty:
        return None
    site_attr = (
        work.groupby("_site_key", as_index=False)[attr_cols].first()
        if attr_cols
        else work[["_site_key"]].drop_duplicates()
    )
    keys = set(site_attr["_site_key"].astype(str))
    time_cols = [c for c in group_cols if c in _TIME_DIMS]

    if not time_cols:
        parts = _site_window_frame(daily, start_ts, end_ts)
        parts = parts[parts["site_key"].astype(str).isin(keys)]
        merged = parts.merge(site_attr, left_on="site_key", right_on="_site_key", how="inner")
        if merged.empty or any(c not in merged.columns for c in group_cols):
            return None
        return merged.groupby(group_cols, observed=True)[["opening", "receipts"]].sum()

    labeled = daily[(daily["date"] >= start_ts) & (daily["date"] <= end_ts)].copy()
    if labeled.empty:
        return None
    labels = _label_dates(labeled["date"])
    for col in time_cols:
        labeled[col] = labels[col].to_numpy()
    # Opening is the stock on the first day of each time bucket, not the FY opening.
    bucket_start = labeled.groupby(time_cols, observed=True)["date"].min()
    pieces = []
    for bucket, when in bucket_start.items():
        opening = opening_by_site(daily, pd.Timestamp(when)).rename("opening")
        if len(time_cols) == 1:
            rec = labeled.loc[labeled[time_cols[0]] == bucket].groupby("site_key")["receipts"].sum()
        else:
            key_df = labeled.set_index(time_cols)
            rec = key_df.loc[bucket].groupby("site_key")["receipts"].sum() if bucket in key_df.index else pd.Series(dtype=float)
        frame = pd.concat([opening, rec.rename("receipts")], axis=1).fillna(0.0).reset_index()
        if "site_key" not in frame.columns:
            frame = frame.rename(columns={frame.columns[0]: "site_key"})
        if len(time_cols) == 1:
            frame[time_cols[0]] = bucket
        else:
            for col, val in zip(time_cols, bucket):
                frame[col] = val
        pieces.append(frame)
    if not pieces:
        return None
    parts = pd.concat(pieces, ignore_index=True)
    parts = parts[parts["site_key"].astype(str).isin(keys)]
    merged = parts.merge(site_attr, left_on="site_key", right_on="_site_key", how="inner")
    if merged.empty or any(c not in merged.columns for c in group_cols):
        return None
    return merged.groupby(group_cols, observed=True)[["opening", "receipts"]].sum()

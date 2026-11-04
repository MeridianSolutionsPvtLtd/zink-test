"""Generated retail financial calendar (no Excel upload).

Monday–Sunday weeks. Financial year starts 1 April. Week 1 runs from
1 April through that Sunday; later weeks are full Monday–Sunday.
"""
from __future__ import annotations

import threading
from datetime import date

import pandas as pd

from pipeline.financial_calendar import assign_fy_week, build_calendar, week_bounds

_LOCK = threading.RLock()
_FRAME: pd.DataFrame | None = None
_MAX_DATE_CACHE: tuple[str, date | None] | None = None
_OPTIONS_CACHE: tuple[str, dict] | None = None

# Stable cache-key token. Must not depend on get_max_sales_date() (that loads all years).
CALENDAR_VERSION = "generated-mon-sun-from-1-apr"


def load_calendar() -> pd.DataFrame:
    global _FRAME
    with _LOCK:
        if _FRAME is None or _FRAME.empty:
            _FRAME = build_calendar()
        return _FRAME


def fy_week_key(d: pd.Timestamp | date) -> str:
    if d is None or (isinstance(d, pd.Timestamp) and pd.isna(d)):
        return ""
    day = d.date() if isinstance(d, pd.Timestamp) else d
    return assign_fy_week(day)["week_key"]


def selected_week_dates(values: str) -> pd.DatetimeIndex:
    import re
    frame = load_calendar()
    if not values or not str(values).strip():
        return pd.DatetimeIndex([])
    raw_tokens = [v.strip().upper() for v in str(values).split(",") if v.strip()]
    if not raw_tokens:
        return pd.DatetimeIndex([])

    matched_masks = []
    for tok in raw_tokens:
        clean = tok.replace(" ", "")
        # 1. Exact match on week_key (e.g. FY27-W01)
        m = frame["week_key"].str.upper() == clean
        if m.any():
            matched_masks.append(m)
            continue
        # 2. Match with/without hyphen (e.g. FY-27-W01 vs FY27-W01)
        clean_no_dash = clean.replace("FY-", "FY")
        m_dash = frame["week_key"].str.upper() == clean_no_dash
        if m_dash.any():
            matched_masks.append(m_dash)
            continue
        # 3. Match W1, W01, WK-01, WK01
        m_wk = re.search(r"W(?:K|-)?0*(\d{1,2})$", clean)
        if m_wk:
            wnum = int(m_wk.group(1))
            fy_m = re.search(r"FY-?(\d{2,4})", clean)
            if fy_m:
                fy_val = int(fy_m.group(1))
                fy_full = fy_val if fy_val >= 1000 else 2000 + fy_val
                m_found = (frame["week"] == wnum) & (frame["year"] == fy_full)
            else:
                m_found = frame["week"] == wnum
            if m_found.any():
                matched_masks.append(m_found)
                continue

    if not matched_masks:
        return pd.DatetimeIndex([])

    combined_mask = matched_masks[0]
    for mask in matched_masks[1:]:
        combined_mask = combined_mask | mask

    dates = frame.loc[combined_mask, "Date"]
    return pd.DatetimeIndex(pd.to_datetime(dates).unique())


def get_max_sales_date() -> date | None:
    """Return max sales date for calendar capping.

    Only peeks the latest FY (never year=0 / all years). Result is cached by
    source mtime so /financial-calendar stays fast on page load.
    """
    global _MAX_DATE_CACHE
    try:
        from api.sales_year_loader import (
            _mtime_key,
            _year_folder_paths,
            get_default_dashboard_year,
            load_sales_for_year,
        )

        year = get_default_dashboard_year()
        paths = _year_folder_paths(year)
        sig = "|".join(_mtime_key(p) for p in paths) if paths else f"fy:{year}"
        with _LOCK:
            if _MAX_DATE_CACHE is not None and _MAX_DATE_CACHE[0] == sig:
                return _MAX_DATE_CACHE[1]

        max_day: date | None = None
        df = load_sales_for_year(year)
        if df is not None and not df.empty:
            col = "date" if "date" in df.columns else ("Date" if "Date" in df.columns else None)
            if col:
                m = pd.to_datetime(df[col], errors="coerce").max()
                if pd.notna(m):
                    max_day = m.date() if isinstance(m, pd.Timestamp) else m

        with _LOCK:
            _MAX_DATE_CACHE = (sig, max_day)
        return max_day
    except Exception:
        return date.today()


def calendar_options(max_date: date | pd.Timestamp | None = None) -> dict:
    global _OPTIONS_CACHE
    # Page-load path: cap at today so we never block on sales-year I/O.
    # Explicit max_date still wins (warmers / tests can pass a real data max).
    if max_date is None:
        max_date = date.today()
    elif isinstance(max_date, pd.Timestamp):
        max_date = max_date.date()

    cache_sig = f"{CALENDAR_VERSION}:{max_date.isoformat()}"
    with _LOCK:
        if _OPTIONS_CACHE is not None and _OPTIONS_CACHE[0] == cache_sig:
            return _OPTIONS_CACHE[1]

    frame = load_calendar()
    if max_date is not None:
        capped_frame = frame[frame["Date"].dt.date <= max_date]
        if not capped_frame.empty:
            frame = capped_frame

    from api.sales_year_loader import FY_FILTER_START, current_indian_fy_end

    fy_cap = current_indian_fy_end()
    years = sorted(
        (int(y) for y in frame["year"].unique() if FY_FILTER_START <= int(y) <= fy_cap),
        reverse=True,
    )
    weeks = []
    for (year, key), group in frame.groupby(["year", "week_key"], sort=True):
        start, end = week_bounds(int(year), int(group["week"].iloc[0]))
        if int(year) < FY_FILTER_START or int(year) > fy_cap:
            continue
        if max_date is not None and start > max_date:
            continue
        start_lbl = f"{start.day} {start.strftime('%b')}"
        end_lbl = f"{end.day} {end.strftime('%b')}"
        weeks.append(
            {
                "id": key,
                "year": int(year),
                "week": int(group["week"].iloc[0]),
                "label": f"FY{str(int(year))[-2:]} Wk {int(group['week'].iloc[0])} ({start_lbl} – {end_lbl})",
                "dates": group["Date"].dt.strftime("%Y-%m-%d").tolist(),
            }
        )
    payload = {
        "years": [
            {"id": f"FY{str(y)[-2:]}", "year": int(y), "label": f"FY-{str(y)[-2:]}"}
            for y in years
        ],
        "latest_year": int(years[0]) if years else None,
        "max_date": max_date.strftime("%Y-%m-%d") if max_date else None,
        "weeks": weeks,
        "version": CALENDAR_VERSION,
    }
    with _LOCK:
        _OPTIONS_CACHE = (cache_sig, payload)
    return payload

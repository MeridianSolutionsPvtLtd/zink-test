"""Retail financial weeks: Monday–Sunday, FY starts 1 April.

Week 1 of a financial year is 1 April through that week's Sunday (short if
1 April is not a Monday). Later weeks are full Monday–Sunday. The next
1 April starts WK-01 again. Days before 1 April stay in the previous FY.
"""
from __future__ import annotations

import io
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

CATEGORY_ID = "financial-week-info"
FOLDER = "Financial Week Info"
CANONICAL_NAME = "FINANCIAL_WEEK_INFO.csv"
# Generate weeks from this FY-end year through the FY that contains "today" + 1.
CALENDAR_START_FY_END = 2020


def fy_end_year(d: date) -> int:
    """FY label year: 1 Apr 2025–31 Mar 2026 → 2026."""
    return d.year + 1 if d.month >= 4 else d.year


def _fy_bounds(fy_end: int) -> tuple[date, date, date]:
    """Return (1 Apr, Sunday ending week 1, 31 Mar)."""
    fy_start = date(fy_end - 1, 4, 1)
    fy_last = date(fy_end, 3, 31)
    # weekday: Mon=0 … Sun=6. Wednesday → 4 days to Sunday.
    days_to_sunday = (6 - fy_start.weekday()) % 7
    week1_end = fy_start + timedelta(days=days_to_sunday)
    return fy_start, week1_end, fy_last


def week_bounds(fy_end: int, week: int) -> tuple[date, date]:
    """Inclusive start/end of FY week `week` (1-based)."""
    fy_start, week1_end, fy_last = _fy_bounds(fy_end)
    if week <= 1:
        return fy_start, week1_end
    first_monday = week1_end + timedelta(days=1)
    week_start = first_monday + timedelta(days=(week - 2) * 7)
    week_end = min(week_start + timedelta(days=6), fy_last)
    return week_start, week_end


def assign_fy_week(d: date) -> dict:
    """One calendar date → FY week key, number, and inclusive date span."""
    end = fy_end_year(d)
    fy_start, week1_end, fy_last = _fy_bounds(end)
    if d <= week1_end:
        week = 1
        start, stop = fy_start, week1_end
    else:
        first_monday = week1_end + timedelta(days=1)
        week = 2 + (d - first_monday).days // 7
        start, stop = week_bounds(end, week)
    key = f"FY{str(end)[-2:]}-W{week:02d}"
    return {
        "date": d,
        "fy_end": end,
        "week": week,
        "week_key": key,
        "start": start,
        "end": stop,
        "fy_label": f"FY-{str(end)[-2:]}",
        "week_label": f"WK-{week:02d}",
    }


def weeks_in_fy(fy_end: int) -> list[dict]:
    """Every retail week from 1 Apr through 31 Mar, in order."""
    _, _, fy_last = _fy_bounds(fy_end)
    out: list[dict] = []
    cursor = date(fy_end - 1, 4, 1)
    seen: set[str] = set()
    while cursor <= fy_last:
        info = assign_fy_week(cursor)
        if info["week_key"] not in seen:
            seen.add(info["week_key"])
            out.append(info)
        cursor = info["end"] + timedelta(days=1)
    return out


def build_calendar(start_fy_end: int = CALENDAR_START_FY_END, end_fy_end: int | None = None) -> pd.DataFrame:
    """One row per date, from `start_fy_end` through the FY of today (plus one)."""
    if end_fy_end is None:
        end_fy_end = fy_end_year(date.today()) + 1
    rows: list[dict] = []
    for fy_end in range(start_fy_end, end_fy_end + 1):
        for info in weeks_in_fy(fy_end):
            cursor = info["start"]
            while cursor <= info["end"]:
                rows.append(
                    {
                        "Date": pd.Timestamp(cursor),
                        "F. Year": info["fy_label"],
                        "FY WK": info["week_label"],
                        "year": info["fy_end"],
                        "week": info["week"],
                        "week_key": info["week_key"],
                    }
                )
                cursor += timedelta(days=1)
    return pd.DataFrame(rows)


def normalize_calendar(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.rename(columns={c: str(c).strip() for c in frame.columns})
    required = ["Date", "F. Year", "FY WK"]
    if not set(required).issubset(frame.columns):
        raise ValueError("Financial Week Info requires Date, F. Year and FY WK columns")
    out = frame[required].dropna(how="all").copy()
    out["Date"] = pd.to_datetime(out["Date"], dayfirst=True, format="mixed", errors="coerce").dt.normalize()
    years = out["F. Year"].astype(str).str.strip().str.upper().str.extract(r"^FY[\s-]*(\d{2}|\d{4})$", expand=False)
    weeks = out["FY WK"].astype(str).str.strip().str.upper().str.extract(r"^(?:WEEK|WK|W)[\s-]*(\d{1,2})$", expand=False)
    if out["Date"].isna().any() or years.isna().any() or weeks.isna().any():
        raise ValueError("Invalid date, financial year or week in Financial Week Info")
    years = years.astype(int).map(lambda y: y + 2000 if y < 100 else y)
    weeks = weeks.astype(int)
    if not weeks.between(1, 53).all():
        raise ValueError("Financial week must be between WK-01 and WK-53")
    out["F. Year"] = "FY-" + years.astype(str).str[-2:]
    out["FY WK"] = "WK-" + weeks.astype(str).str.zfill(2)
    out = out.drop_duplicates()
    if out["Date"].duplicated().any():
        raise ValueError("Conflicting FY/week assignments for the same date")
    return out.sort_values("Date").reset_index(drop=True)


def read_calendar(data: bytes, filename: str) -> pd.DataFrame:
    source = io.BytesIO(data)
    frame = pd.read_csv(source) if Path(filename).suffix.lower() == ".csv" else pd.read_excel(source)
    return normalize_calendar(frame)


def merge_calendar(old: pd.DataFrame | None, new: pd.DataFrame) -> pd.DataFrame:
    """Incoming dates replace their mapping; retain other historical dates."""
    new = normalize_calendar(new)
    if old is None or old.empty:
        return new
    old = normalize_calendar(old)
    return pd.concat([old[~old["Date"].isin(new["Date"])], new], ignore_index=True).sort_values("Date")

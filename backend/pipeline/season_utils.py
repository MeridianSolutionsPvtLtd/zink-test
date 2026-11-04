"""
Season Utilities - Universal Season Minus Logic & Season Parsing
Supports long-term dynamic season operations:
  AW-26 -> AW-25
  SS-26 -> SS-25
  AW-27 -> AW-26
  SS-27 -> SS-26
  AW26  -> AW25
  SS 26 -> SS 25
  AW-2026 -> AW-2025
"""
from __future__ import annotations

import re


def get_previous_season(season_str: str) -> str:
    """
    Derives the previous year season string for any season string format.

    Examples:
        'AW-26'   -> 'AW-25'
        'SS-26'   -> 'SS-25'
        'AW-27'   -> 'AW-26'
        'SS-27'   -> 'SS-26'
        'AW26'    -> 'AW25'
        'SS 26'   -> 'SS 25'
        'AW-2026' -> 'AW-2025'
    """
    if not season_str or not isinstance(season_str, str):
        return ""

    s = season_str.strip()
    if not s:
        return ""

    match = re.search(r"^([A-Za-z\s]+)([\-_]*\s*)(\d{2,4})$", s)
    if match:
        prefix, sep, yr_str = match.groups()
        yr = int(yr_str)
        prev_yr = yr - 1
        prev_yr_str = f"{prev_yr:0{len(yr_str)}d}"
        return f"{prefix}{sep}{prev_yr_str}".upper()

    return s.upper()


def get_season_years_ago(season_str: str, years_back: int = 1) -> str:
    """SS-26, years_back=2 → SS-24."""
    s = (season_str or "").strip()
    if not s:
        return ""
    n = max(0, int(years_back or 0))
    for _ in range(n):
        nxt = get_previous_season(s)
        if not nxt or nxt == s:
            break
        s = nxt
    return s


def expand_season_yoy_chain(season_csv: str, depth: int = 2) -> str:
    """
    Same family across years for YoY / FY comparison.
    SS-26 → SS-26,SS-25,SS-24 (depth=2). AW-26 → AW-26,AW-25,AW-24.
    """
    out: list[str] = []
    seen: set[str] = set()
    for raw in str(season_csv or "").split(","):
        s = raw.strip()
        if not s or s.lower() in ("all",):
            continue
        cur = s.upper()
        for i in range(max(0, int(depth or 0)) + 1):
            if i == 0:
                cand = cur
            else:
                cand = get_season_years_ago(s, i)
            if not cand:
                continue
            key = cand.upper()
            if key in seen:
                continue
            seen.add(key)
            out.append(cand)
    return ",".join(out)


def shift_season_csv(season_csv: str, years_back: int = 1) -> str:
    """Shift every season code back N years (SPLY prior: SS-26 → SS-25)."""
    parts: list[str] = []
    for raw in str(season_csv or "").split(","):
        s = raw.strip()
        if not s or s.lower() in ("all",):
            continue
        shifted = get_season_years_ago(s, years_back)
        if shifted:
            parts.append(shifted)
    return ",".join(parts)


def parse_season_info(season_str: str) -> dict[str, str | int]:
    """
    Parse a season string into code prefix, year integer, and normalized format.
    """
    if not season_str or not isinstance(season_str, str):
        return {"prefix": "", "year": 0, "previous_season": "", "normalized": ""}

    s = season_str.strip().upper()
    match = re.search(r"^([A-Za-z\s]+)([\-_]*\s*)(\d{2,4})$", s)
    if match:
        prefix, sep, yr_str = match.groups()
        yr = int(yr_str)
        if len(yr_str) == 2:
            yr_full = 2000 + yr
        else:
            yr_full = yr
        prev = get_previous_season(s)
        return {
            "prefix": prefix.strip(),
            "year": yr_full,
            "previous_season": prev,
            "normalized": f"{prefix.strip()}-{yr_str}",
        }

    return {"prefix": s, "year": 0, "previous_season": s, "normalized": s}

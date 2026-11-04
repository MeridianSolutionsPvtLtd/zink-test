"""
Store Grade list for dashboards — from STORE_GRADE file (input folder / blob).

Columns: ZONE, Site_Code, Site, Store Category, Grade
Join key: Site_Code → Customer Master SITE MANUAL CODE
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd

PIPELINE_DIR = Path(__file__).resolve().parent
INPUT_DIR = PIPELINE_DIR / "input_files"

# Prefer xlsx — blob CSV is often headerless (first row = data)
BLOB_CANDIDATES = (
    "Store Grade List/current/STORE_GRADE.xlsx",
    "Store Grade List/current/STORE_GRADE.xls",
    "Store Grade List/current/STORE_GRADE.csv",
)

LOCAL_CANDIDATES = (
    INPUT_DIR / "Store Grade List" / "current" / "STORE_GRADE.xlsx",
    INPUT_DIR / "Store Grade List" / "current" / "STORE_GRADE.csv",
    INPUT_DIR / "STORE_GRADE.xlsx",
    INPUT_DIR / "STORE_GRADE.csv",
)

HEADERLESS_COLS = ["ZONE", "Site_Code", "Site", "Store Category", "Grade"]
_ZONES = frozenset({"EAST", "WEST", "NORTH", "SOUTH", "CENTRAL"})


def _norm_site_code(val: object) -> str:
    import re

    s = str(val or "").strip()
    if s.lower() in ("nan", "none", "<na>", ""):
        return ""
    return re.sub(r"\.0$", "", s)


def _has_named_columns(df: pd.DataFrame) -> bool:
    cols = {str(c).strip().lower() for c in df.columns}
    has_site = any(k in cols for k in ("site_code", "site code", "site_id"))
    has_grade = any(k in cols for k in ("grade", "store grade", "store_grade"))
    return has_site and has_grade


def _looks_headerless(df: pd.DataFrame) -> bool:
    if df.empty or len(df.columns) < 5:
        return False
    first = str(df.columns[0]).strip().upper()
    return first in _ZONES


def _read_csv_flexible(path: Path) -> pd.DataFrame:
    last_err: Exception | None = None
    for enc in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
        try:
            df = pd.read_csv(path, encoding=enc, low_memory=False)
            df.columns = [str(c).strip() for c in df.columns]
            if _has_named_columns(df):
                return df
            if _looks_headerless(df):
                df = pd.read_csv(
                    path,
                    header=None,
                    names=HEADERLESS_COLS,
                    encoding=enc,
                    low_memory=False,
                )
                return df
            return df
        except Exception as e:
            last_err = e
    if last_err:
        raise last_err
    return pd.read_csv(path, encoding="latin-1", low_memory=False, on_bad_lines="skip")


def _read_store_grade(path: Path) -> pd.DataFrame:
    data = path.read_bytes()[:4]
    is_xlsx = path.suffix.lower() in (".xlsx", ".xls") or data[:2] == b"PK"
    if is_xlsx:
        df = pd.read_excel(path)
        df.columns = [str(c).strip() for c in df.columns]
        return df
    return _read_csv_flexible(path)


def _parse_store_grade_df(raw: pd.DataFrame) -> pd.DataFrame:
    cols = {c.lower(): c for c in raw.columns}
    site_col = cols.get("site_code") or cols.get("site code") or cols.get("site_id")
    grade_col = cols.get("grade") or cols.get("store grade") or cols.get("store_grade")
    if not site_col or not grade_col:
        return pd.DataFrame(columns=["site_code", "store_grade", "grade_zone", "grade_site_name"])

    zone_col = cols.get("zone")
    name_col = cols.get("site") or cols.get("site name")

    out = pd.DataFrame()
    out["site_code"] = raw[site_col].map(_norm_site_code)
    out["store_grade"] = raw[grade_col].astype(str).str.strip().replace({"nan": "", "None": "", "<NA>": ""})
    out["grade_zone"] = raw[zone_col].astype(str).str.strip() if zone_col else ""
    out["grade_site_name"] = raw[name_col].astype(str).str.strip() if name_col else ""
    out = out[(out["site_code"].astype(str).str.len() > 0) & (out["store_grade"].astype(str).str.len() > 0)]
    return out.drop_duplicates(subset=["site_code"], keep="first").reset_index(drop=True)


def _find_store_grade_path() -> Optional[Path]:
    try:
        from api.blob_store import BlobJsonStore

        store = BlobJsonStore()
        prefix = store.input_prefix
        for rel in BLOB_CANDIDATES:
            blob_name = f"{prefix}{rel}"
            if not store._blob_exists(blob_name):
                continue
            path = store._ensure_local_file(blob_name)
            if path is None or not path.exists() or path.stat().st_size <= 0:
                continue
            try:
                parsed = _parse_store_grade_df(_read_store_grade(path))
                if not parsed.empty:
                    return path
            except Exception:
                continue
    except Exception:
        pass
    for p in LOCAL_CANDIDATES:
        if not p.exists() or p.stat().st_size <= 0:
            continue
        try:
            parsed = _parse_store_grade_df(_read_store_grade(p))
            if not parsed.empty:
                return p
        except Exception:
            continue
    return None


def load_store_grade_map() -> pd.DataFrame:
    """Return site_code → store_grade (one row per Site_Code)."""
    path = _find_store_grade_path()
    if path is None:
        return pd.DataFrame(columns=["site_code", "store_grade", "grade_zone", "grade_site_name"])
    try:
        return _parse_store_grade_df(_read_store_grade(path))
    except Exception:
        return pd.DataFrame(columns=["site_code", "store_grade", "grade_zone", "grade_site_name"])


def store_grade_filter_options() -> list[str]:
    """Distinct Grade values from STORE_GRADE file."""
    gm = load_store_grade_map()
    if gm.empty:
        return []
    grades = gm["store_grade"].astype(str).str.strip()
    grades = grades[grades.ne("") & ~grades.str.lower().isin({"nan", "none"})]
    return sorted(grades.unique().tolist(), key=str.casefold)


def grade_for_site(site_code: str, grade_map: pd.DataFrame | None = None) -> str:
    code = _norm_site_code(site_code)
    if not code:
        return ""
    gm = grade_map if grade_map is not None else load_store_grade_map()
    if gm.empty:
        return ""
    hit = gm.loc[gm["site_code"].astype(str).str.upper() == code.upper(), "store_grade"]
    return str(hit.iloc[0]).strip() if len(hit) else ""

"""
Date-aware merge for rolling sales / invoice Excel files.

Rule:
  - Find date column in old & new (prefer name match; content scan last resort)
  - Overlap dates dropped from OLD, then concat OLD + NEW
  - Fast Excel read: preferred sheet/header first, early exit
"""
from __future__ import annotations

import io
import re
import warnings
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd


def _norm_col(c: object) -> str:
    return str(c).strip().lower()


def _dedupe_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Drop duplicate column names (keeps first) — avoids reindex errors on merge."""
    if df is None or getattr(df, "empty", True):
        return df
    out = df.copy()
    out.columns = [str(c).strip() for c in out.columns]
    if out.columns.is_unique:
        return out
    return out.loc[:, ~out.columns.duplicated()].copy()


def is_excel_bytes(data: bytes) -> bool:
    """True for .xlsx zip or legacy .xls OLE — even if filename says .csv."""
    if not data:
        return False
    if data[:2] == b"PK":
        return True
    # OLE Compound Document (old .xls)
    return data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def read_csv_bytes(data: bytes, **kwargs: Any) -> pd.DataFrame:
    """
    Read CSV with encoding fallbacks (Ginesys exports often cp1252 / latin-1).
    Avoids: UnicodeDecodeError: 'utf-8' codec can't decode byte 0xee …
    """
    # Drop caller encoding / low_memory so we don't pass them twice
    kwargs = {
        k: v
        for k, v in kwargs.items()
        if k not in ("encoding", "encoding_errors", "low_memory")
    }
    kwargs.setdefault("on_bad_lines", "skip")
    last_err: Exception | None = None
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return pd.read_csv(io.BytesIO(data), encoding=enc, low_memory=False, **kwargs)
        except UnicodeDecodeError as exc:
            last_err = exc
            continue
        except Exception as exc:
            msg = str(exc).lower()
            if "codec can't decode" in msg or "utf-8" in msg:
                last_err = exc
                continue
            raise
    try:
        return pd.read_csv(
            io.BytesIO(data),
            encoding="latin-1",
            encoding_errors="replace",
            low_memory=False,
            **kwargs,
        )
    except TypeError:
        # Older pandas without encoding_errors
        if last_err:
            raise last_err
        return pd.read_csv(io.BytesIO(data), encoding="latin-1", low_memory=False, **kwargs)


def _should_read_as_csv(filename: str, data: bytes) -> bool:
    lower = (filename or "").lower()
    if is_excel_bytes(data):
        return False
    if lower.endswith((".xlsx", ".xls", ".xlsm")):
        return False
    return lower.endswith(".csv") or not lower.endswith((".xlsx", ".xls", ".xlsm"))


def find_date_column_by_name(df: pd.DataFrame, candidates: Sequence[str]) -> Optional[str]:
    lookup = {_norm_col(c): c for c in df.columns}
    for cand in candidates:
        key = cand.strip().lower()
        if key in lookup:
            return lookup[key]
    for c in df.columns:
        if "date" in _norm_col(c):
            return c
    return None


def find_date_column(df: pd.DataFrame, candidates: Sequence[str]) -> str:
    named = find_date_column_by_name(df, candidates)
    if named is not None:
        return named

    # Content scan only as last resort (quiet, small sample, skip obvious non-dates)
    best_col = None
    best_score = 0
    sample_n = min(len(df), 40)
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=UserWarning)
        for c in df.columns:
            name = _norm_col(c)
            if name.startswith("unnamed"):
                continue
            s = df[c].head(sample_n)
            if pd.api.types.is_bool_dtype(s):
                continue
            try:
                parsed = pd.to_datetime(s, errors="coerce", format="mixed")
            except (TypeError, ValueError):
                parsed = pd.to_datetime(s, errors="coerce")
            score = int(parsed.notna().sum())
            if score > best_score and score >= max(3, int(sample_n * 0.4)):
                best_score = score
                best_col = c
    if best_col is not None:
        return best_col
    raise ValueError(
        f"No date column found. Tried {list(candidates)}. Columns={list(df.columns)}"
    )


def to_day_series(series: pd.Series) -> pd.Series:
    """Parse day-first dates (DD-MM-YYYY) used in Ginesys / India exports."""
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=UserWarning)
        try:
            return pd.to_datetime(
                series, errors="coerce", format="mixed", dayfirst=True
            ).dt.normalize()
        except (TypeError, ValueError):
            return pd.to_datetime(series, errors="coerce", dayfirst=True).dt.normalize()


def dataframe_date_bounds(
    df: pd.DataFrame,
    date_candidates: Sequence[str],
) -> Dict[str, Any]:
    """Cheap min/max date + row count for skip decisions before merge."""
    col = find_date_column(df, date_candidates)
    days = to_day_series(df[col]).dropna()
    if days.empty:
        return {
            "date_column": str(col),
            "min": None,
            "max": None,
            "rows_with_date": 0,
            "total_rows": int(len(df)),
        }
    dmin, dmax = days.min(), days.max()
    return {
        "date_column": str(col),
        "min": dmin,
        "max": dmax,
        "min_str": str(dmin.date()) if hasattr(dmin, "date") else str(dmin),
        "max_str": str(dmax.date()) if hasattr(dmax, "date") else str(dmax),
        "rows_with_date": int(len(days)),
        "total_rows": int(len(df)),
    }


def _col_compare_key(name: str) -> str:
    n = _norm_col(name)
    if n.startswith("unnamed") or re.fullmatch(r"\d+(\.\d+)?", n):
        return ""
    return re.sub(r"[^a-z0-9]+", "", n)


# Semantic SOH roles (store stock snapshots — many export layouts)
_SOH_ROLE_ALIASES: Dict[str, frozenset[str]] = {
    "site": frozenset(
        {"site", "sitename", "siteid", "storeno", "store", "manualcode", "branchcode", "storecode"}
    ),
    "barcode": frozenset(
        {"eanupc", "articlean", "barcode", "gs1number", "eancode", "ean", "upc"}
    ),
    "qty": frozenset(
        {
            "totalstockvalue",
            "unrestrictedstockquantity",
            "totalstockqty",
            "currentstockqty",
            "qtyavailable",
            "closingstockqty",
            "closingstcqty",
            "stockqty",
        }
    ),
}


def _soh_roles_from_columns(cols: Sequence[object]) -> set[str]:
    keys = {_col_compare_key(str(c)) for c in cols}
    keys.discard("")
    roles: set[str] = set()
    for role, aliases in _SOH_ROLE_ALIASES.items():
        if keys & aliases:
            roles.add(role)
    return roles


def _is_soh_category_label(category_label: str) -> bool:
    lab = (category_label or "").lower()
    return "soh" in lab or lab.endswith(" - soh")


def compare_columns(
    old_df: Optional[pd.DataFrame],
    new_df: pd.DataFrame,
    *,
    date_candidates: Sequence[str],
    category_label: str = "",
) -> List[str]:
    """
    Soft warnings for column / date-header issues.
    Does not block merge — frontend can show "check column names".
    """
    warnings_out: List[str] = []
    prefix = f"{category_label}: " if category_label else ""

    if new_df is None:
        warnings_out.append(f"{prefix}Uploaded file looks empty — check the file / sheet.")
        return warnings_out

    new_cols = [str(c).strip() for c in new_df.columns]
    if not new_cols:
        warnings_out.append(f"{prefix}Uploaded file looks empty — check the file / sheet.")
        return warnings_out
    if date_candidates and find_date_column_by_name(new_df, date_candidates) is None:
        # Still may find date via content scan, but warn the user
        sample = ", ".join(new_cols[:12])
        more = f" (+{len(new_cols) - 12} more)" if len(new_cols) > 12 else ""
        expected = ", ".join(str(c) for c in date_candidates[:5])
        warnings_out.append(
            f"{prefix}Date column name not found (expected like: {expected}). "
            f"Please check the column name. Found columns: {sample}{more}"
        )

    if old_df is None or old_df.empty:
        return warnings_out

    old_set = {str(c).strip() for c in old_df.columns}
    new_set = set(new_cols)
    # Ignore internal helper cols if any
    old_set.discard("_merge_day")
    new_set.discard("_merge_day")
    # Ignore Excel placeholder columns (wrong header row / merged title rows)
    old_set = {c for c in old_set if _col_compare_key(c)}
    new_set = {c for c in new_set if _col_compare_key(c)}

    if _is_soh_category_label(category_label) and not date_candidates:
        old_roles = _soh_roles_from_columns(old_set)
        new_roles = _soh_roles_from_columns(new_set)
        missing_roles = sorted(old_roles - new_roles)
        extra_roles = sorted(new_roles - old_roles)
        if missing_roles or extra_roles:
            parts: List[str] = [
                f"{prefix}SOH layout change — check site / barcode / qty columns."
            ]
            if missing_roles:
                parts.append(f"Missing roles in new file: {', '.join(missing_roles)}")
            if extra_roles:
                parts.append(f"Extra roles in new file: {', '.join(extra_roles)}")
            warnings_out.append(" ".join(parts))
        return warnings_out

    # Fuzzy set compare (spaces vs underscores, case)
    old_keys = {_col_compare_key(c): c for c in old_set if _col_compare_key(c)}
    new_keys = {_col_compare_key(c): c for c in new_set if _col_compare_key(c)}
    missing_keys = set(old_keys) - set(new_keys)
    extra_keys = set(new_keys) - set(old_keys)
    missing = sorted(old_keys[k] for k in missing_keys)
    extra = sorted(new_keys[k] for k in extra_keys)
    if missing or extra:
        parts: List[str] = [f"{prefix}Column mismatch vs current file — please check the column names."]
        if missing:
            show = ", ".join(missing[:8])
            more = f" (+{len(missing) - 8} more)" if len(missing) > 8 else ""
            parts.append(f"Missing in new file: {show}{more}")
        if extra:
            show = ", ".join(extra[:8])
            more = f" (+{len(extra) - 8} more)" if len(extra) > 8 else ""
            parts.append(f"Extra in new file: {show}{more}")
        warnings_out.append(" ".join(parts))

    return warnings_out


INCREMENTAL_TRANSFER_HINT = (
    "Transfer best practice: upload only the new DC_DATE period (e.g. 27–28 Jul). "
    "Do not re-upload full history or large old exports."
)


def bounds_public(bounds: Dict[str, Any]) -> Dict[str, Any]:
    """JSON-safe date bounds (for sidecar meta + API stats)."""
    if not bounds:
        return {}
    out: Dict[str, Any] = {
        "date_column": bounds.get("date_column"),
        "min_str": bounds.get("min_str"),
        "max_str": bounds.get("max_str"),
        "rows_with_date": bounds.get("rows_with_date"),
        "total_rows": bounds.get("total_rows"),
    }
    # Sparse daily coverage (gap-fill late files after current_max advanced)
    if bounds.get("assumed_covered_through"):
        out["assumed_covered_through"] = str(bounds.get("assumed_covered_through"))[:10]
    dates = bounds.get("merged_file_dates")
    if isinstance(dates, (list, tuple, set)):
        out["merged_file_dates"] = sorted({str(d)[:10] for d in dates if d})
    return out


def bounds_from_meta(meta: Dict[str, Any]) -> Dict[str, Any]:
    """Rebuild Timestamp min/max from sidecar JSON for skip/merge decisions."""
    out = dict(meta or {})
    for key, skey in (("min", "min_str"), ("max", "max_str")):
        raw = out.get(skey) or out.get(key)
        if raw is None or raw == "":
            out[key] = None
            continue
        ts = pd.Timestamp(raw)
        out[key] = ts
        if not out.get(skey):
            out[skey] = str(ts.date()) if hasattr(ts, "date") else str(ts)
    dates = out.get("merged_file_dates")
    if isinstance(dates, list):
        out["merged_file_dates"] = sorted({str(d)[:10] for d in dates if d})
    return out


def _parse_meta_day(raw: Any) -> Optional[date]:
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        try:
            return pd.Timestamp(raw).date()
        except Exception:
            return None


def _normalize_day_set(dates: Optional[Sequence[Any]]) -> List[date]:
    out: List[date] = []
    for d in dates or []:
        if d is None or d == "":
            continue
        try:
            if isinstance(d, date):
                out.append(d)
            elif hasattr(d, "date") and callable(d.date):
                out.append(d.date())
            else:
                out.append(pd.Timestamp(d).date())
        except Exception:
            continue
    return sorted(set(out))


def is_file_date_covered(fd: date, meta: Optional[Dict[str, Any]]) -> bool:
    """
    True if this daily file date is already in current coverage.

    Historical continuous range is frozen as assumed_covered_through.
    Later discrete days live in merged_file_dates.
    Missing days (e.g. 18 Aug arriving after 21 Aug was merged) stay uncovered.
    """
    if meta is None:
        return False
    covered_through = _parse_meta_day(meta.get("assumed_covered_through"))
    merged = {str(d)[:10] for d in (meta.get("merged_file_dates") or []) if d}
    if covered_through is not None and fd <= covered_through:
        return True
    if fd.isoformat() in merged:
        return True
    # Legacy sidecar (no sparse fields): treat continuous max as fully covered
    if covered_through is None and not merged:
        mx = _parse_meta_day(meta.get("max_str") or meta.get("max"))
        if mx is not None and fd <= mx:
            return True
    return False


def bootstrap_coverage_meta(
    meta: Optional[Dict[str, Any]],
    *,
    raw_file_dates: Optional[Sequence[Any]] = None,
    current_max: Any = None,
) -> Dict[str, Any]:
    """
    One-time sparse coverage init so late gap days can still merge.

    Example: Raw has 17,20,21 and current max=21 → assumed_covered_through=16,
    merged_file_dates=[17,20,21]. When 18 arrives later it is NOT covered → gap-fill.
    """
    out = dict(meta or {})
    has_assumed = bool(out.get("assumed_covered_through"))
    has_merged = isinstance(out.get("merged_file_dates"), list)
    if has_assumed or has_merged:
        if not has_merged:
            out["merged_file_dates"] = []
        return out

    mx = _parse_meta_day(current_max) or _parse_meta_day(out.get("max_str") or out.get("max"))
    raw_days = _normalize_day_set(raw_file_dates)
    if raw_days:
        out["assumed_covered_through"] = (raw_days[0] - timedelta(days=1)).isoformat()
        out["merged_file_dates"] = [
            d.isoformat() for d in raw_days if mx is None or d <= mx
        ]
    elif mx is not None:
        out["assumed_covered_through"] = mx.isoformat()
        out["merged_file_dates"] = []
    else:
        out["merged_file_dates"] = []
    return out


def update_coverage_after_merge(
    meta: Optional[Dict[str, Any]],
    *,
    old_max: Any = None,
    incoming_file_dates: Optional[Sequence[Any]] = None,
) -> Dict[str, Any]:
    """
    After merging daily Stock Pulse files, record which file-dates are covered.

    On first sparse merge: freeze assumed_covered_through = previous current max
    so late gap days (between covered_through and new max) can still be filled.
    """
    out = dict(meta or {})
    prev_dates = {str(d)[:10] for d in (out.get("merged_file_dates") or []) if d}
    new_dates = {d.isoformat() for d in _normalize_day_set(incoming_file_dates)}

    if not out.get("assumed_covered_through") and old_max is not None:
        try:
            om = pd.Timestamp(old_max).date()
            out["assumed_covered_through"] = om.isoformat()
        except Exception:
            pass

    out["merged_file_dates"] = sorted(prev_dates | new_dates)
    return out


def should_skip_merge_by_dates(
    old_bounds: Dict[str, Any],
    new_bounds: Dict[str, Any],
    *,
    allow_gap_fill: bool = False,
) -> Tuple[bool, str]:
    """
    Skip only when incoming ends before current max (no last-day refresh possible).

    If incoming_max == current_max → do NOT skip: overlap/replace that last date
    (missed transactions on the latest day are common).
    If incoming_max > current_max → merge (append / overlap).
    If allow_gap_fill and incoming is older than max → still merge (fill missing day).
    """
    old_max, new_max = old_bounds.get("max"), new_bounds.get("max")
    if old_max is None or new_max is None:
        return False, "missing_dates"

    if new_max < old_max:
        if allow_gap_fill:
            return False, "need_gap_fill"
        return True, (
            f"incoming_max({new_bounds.get('max_str')}) < current_max({old_bounds.get('max_str')})"
        )

    if new_max == old_max:
        return False, "need_overlap_last_date"

    return False, "need_merge"


def merge_fill_dates(
    old_df: Optional[pd.DataFrame],
    new_df: pd.DataFrame,
    *,
    date_candidates: Sequence[str],
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """
    Insert / replace rows for every date present in incoming (gap-fill).

    Used when a late daily file arrives after current_max already moved forward
    (e.g. merge 17,20,21 first — then 18 arrives later).
    """
    if new_df is None or new_df.empty:
        raise ValueError("New dataframe is empty — nothing to merge")

    new = _dedupe_columns(new_df.copy())
    new.columns = [str(c).strip() for c in new.columns]
    new_date_col = find_date_column(new, date_candidates)
    new["_merge_day"] = to_day_series(new[new_date_col])
    new = new[new["_merge_day"].notna()].copy()
    days = sorted({d for d in new["_merge_day"].tolist() if pd.notna(d)})
    stats: Dict[str, Any] = {
        "mode": "gap_fill_dates",
        "date_column": str(new_date_col),
        "fill_dates": [str(pd.Timestamp(d).date()) for d in days],
        "new_rows": int(len(new)),
        "old_rows_before": 0,
        "old_rows_removed": 0,
    }

    if old_df is None or old_df.empty:
        out = new.drop(columns=["_merge_day"])
        stats["merged_rows"] = int(len(out))
        stats["note"] = "no_existing_current — promoted gap-fill rows"
        return out, stats

    old = _dedupe_columns(old_df.copy())
    old.columns = [str(c).strip() for c in old.columns]
    old_date_col = find_date_column(old, date_candidates)
    old["_merge_day"] = to_day_series(old[old_date_col])
    old = old[old["_merge_day"].notna()].copy()
    stats["old_rows_before"] = int(len(old))

    day_set = set(days)
    before = len(old)
    old = old[~old["_merge_day"].isin(day_set)].copy()
    stats["old_rows_removed"] = int(before - len(old))

    old_clean = old.drop(columns=["_merge_day"])
    new_clean = new.drop(columns=["_merge_day"])
    if (
        old_date_col != new_date_col
        and old_date_col in old_clean.columns
        and new_date_col in new_clean.columns
    ):
        old_clean = old_clean.rename(columns={old_date_col: new_date_col})

    out = pd.concat([old_clean, new_clean], ignore_index=True, sort=False)
    stats["merged_rows"] = int(len(out))
    return out, stats


def merge_overlap_last_date(
    old_df: Optional[pd.DataFrame],
    new_df: pd.DataFrame,
    *,
    date_candidates: Sequence[str],
    last_date: Any = None,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """
    Replace only the last date's rows in current with incoming rows for that day.

    Used when incoming_max == current_max so a re-export can fill missed
    transactions on the latest day without rewriting older history.
    """
    if new_df is None or new_df.empty:
        raise ValueError("New dataframe is empty — nothing to merge")

    new = _dedupe_columns(new_df.copy())
    new.columns = [str(c).strip() for c in new.columns]
    new_date_col = find_date_column(new, date_candidates)
    new["_merge_day"] = to_day_series(new[new_date_col])
    new = new[new["_merge_day"].notna()].copy()

    if last_date is None:
        if new.empty:
            raise ValueError("No dated rows in incoming for last-date overlap")
        cut = new["_merge_day"].max()
    else:
        cut = pd.Timestamp(last_date).normalize()

    new_last = new[new["_merge_day"] == cut].copy()
    stats: Dict[str, Any] = {
        "mode": "overlap_last_date",
        "date_column": str(new_date_col),
        "last_date": str(cut.date()),
        "new_rows": int(len(new_last)),
        "old_rows_before": 0,
        "old_rows_removed": 0,
        "note": "replaced current rows for last date only (missed-tx refresh)",
    }

    if old_df is None or old_df.empty:
        out = new_last.drop(columns=["_merge_day"])
        stats["merged_rows"] = int(len(out))
        stats["note"] = "no_existing_current — promoted last-date rows"
        return out, stats

    old = _dedupe_columns(old_df.copy())
    old.columns = [str(c).strip() for c in old.columns]
    old_date_col = find_date_column(old, date_candidates)
    old["_merge_day"] = to_day_series(old[old_date_col])
    old = old[old["_merge_day"].notna()].copy()
    stats["old_rows_before"] = int(len(old))

    before = len(old)
    old = old[old["_merge_day"] != cut].copy()
    stats["old_rows_removed"] = int(before - len(old))

    old_clean = old.drop(columns=["_merge_day"])
    new_clean = new_last.drop(columns=["_merge_day"])
    if (
        old_date_col != new_date_col
        and old_date_col in old_clean.columns
        and new_date_col in new_clean.columns
    ):
        old_clean = old_clean.rename(columns={old_date_col: new_date_col})

    if new_clean.empty:
        stats["note"] = "incoming has no rows on last date — kept current"
        out = old_clean
    else:
        out = pd.concat([old_clean, new_clean], ignore_index=True, sort=False)

    stats["merged_rows"] = int(len(out))
    return out, stats


def excel_date_bounds_fast(
    data: bytes,
    filename: str,
    *,
    date_candidates: Sequence[str],
    preferred_header: int = 0,
    preferred_sheet: Any = None,
    column_hints: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """
    Min/max dates without loading the full workbook into pandas.
    Used before heavy date_merge to skip or promote bytes as-is.
    """
    lower = filename.lower()
    hints = list(column_hints or []) + list(date_candidates or [])
    if _should_read_as_csv(filename, data):
        header_idx = int(preferred_header or 0)
        peek = read_csv_bytes(
            data, header=None, nrows=max(header_idx + 2, 6)
        )
        best = header_idx
        best_score = -10**9
        for i in range(min(len(peek), 8)):
            cells = [str(c).strip() if pd.notna(c) else "" for c in peek.iloc[i].tolist()]
            joined = " ".join(cells).lower()
            score = 0
            for cand in list(date_candidates) + list(hints):
                if str(cand).strip().lower() in joined:
                    score += 50
            if any(x in joined for x in ("barcode", "dc_date", "site name", "invoice")):
                score += 20
            if score > best_score:
                best_score = score
                best = i
        if best_score >= 50:
            header_idx = best
        df = read_csv_bytes(data, header=header_idx)
        df.columns = [str(c).strip() for c in df.columns]
        return dataframe_date_bounds(df, date_candidates)

    from header_detect import detect_excel_header_row

    header_idx, sheet_name = detect_excel_header_row(
        data,
        column_hints=hints,
        preferred_sheet=preferred_sheet,
        preferred_header=int(preferred_header),
    )
    return _excel_date_bounds_fast_once(
        data,
        filename,
        date_candidates=date_candidates,
        header_idx=header_idx,
        preferred_sheet=sheet_name,
    )


def _excel_date_bounds_fast_once(
    data: bytes,
    filename: str,
    *,
    date_candidates: Sequence[str],
    header_idx: int,
    preferred_sheet: Any = None,
) -> Dict[str, Any]:
    import datetime as dt

    lower = filename.lower()
    if _should_read_as_csv(filename, data):
        df = read_csv_bytes(
            data,
            header=int(header_idx or 0),
            usecols=None,
        )
        df.columns = [str(c).strip() for c in df.columns]
        return dataframe_date_bounds(df, date_candidates)

    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        names = wb.sheetnames
        if not names:
            raise ValueError(f"No sheets in {filename}")
        sheet_name = preferred_sheet if preferred_sheet in names else names[0]
        ws = wb[sheet_name]

        header_row: Optional[List[str]] = None
        date_col_idx: Optional[int] = None
        parsed_days: List[dt.date] = []

        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i == header_idx:
                header_row = [
                    str(c).strip() if c is not None else "" for c in (row or ())
                ]
                lookup = {_norm_col(h): j for j, h in enumerate(header_row)}
                for cand in date_candidates:
                    key = cand.strip().lower()
                    if key in lookup:
                        date_col_idx = lookup[key]
                        break
                if date_col_idx is None:
                    for j, h in enumerate(header_row):
                        if "date" in _norm_col(h):
                            date_col_idx = j
                            break
                if date_col_idx is None:
                    raise ValueError(
                        f"No date column in header row {header_idx}. "
                        f"Columns={header_row[:20]}"
                    )
                continue
            if i < header_idx or date_col_idx is None:
                continue
            if not row or date_col_idx >= len(row):
                continue
            val = row[date_col_idx]
            if val is None or val == "":
                continue
            day: Optional[dt.date] = None
            if isinstance(val, dt.datetime):
                day = val.date()
            elif isinstance(val, dt.date):
                day = val
            else:
                try:
                    ts = pd.to_datetime(val, errors="coerce")
                    if pd.notna(ts):
                        day = ts.date() if hasattr(ts, "date") else None
                except Exception:
                    day = None
            if day is not None:
                parsed_days.append(day)

        if not parsed_days:
            raise ValueError(f"No parseable dates in column index {date_col_idx}")

        dmin, dmax = min(parsed_days), max(parsed_days)
        return {
            "date_column": header_row[date_col_idx] if header_row else "",
            "min": pd.Timestamp(dmin),
            "max": pd.Timestamp(dmax),
            "min_str": str(dmin),
            "max_str": str(dmax),
            "rows_with_date": len(parsed_days),
            "total_rows": len(parsed_days),
        }
    finally:
        wb.close()


def incoming_covers_current(
    old_bounds: Dict[str, Any],
    new_bounds: Dict[str, Any],
) -> bool:
    """Weekly full export: new file date span covers existing current."""
    o_min, o_max = old_bounds.get("min"), old_bounds.get("max")
    n_min, n_max = new_bounds.get("min"), new_bounds.get("max")
    if o_min is None or o_max is None or n_min is None or n_max is None:
        return False
    return n_min <= o_min and n_max >= o_max


def _name_header_score(df: pd.DataFrame, candidates: Sequence[str]) -> int:
    """Fast score using column NAMES only (no value parsing)."""
    if df is None or df.empty or len(df.columns) == 0:
        return -1
    score = 0
    cols = [_norm_col(c) for c in df.columns]
    unnamed = sum(1 for c in cols if c.startswith("unnamed") or re.fullmatch(r"\d+(\.\d+)?", c))
    score -= unnamed * 3
    for cand in candidates:
        if cand.strip().lower() in cols:
            score += 100
    if any("date" in c for c in cols):
        score += 40
    score += min(len(df.columns), 40)
    return score


def read_excel_smart(
    data: bytes,
    filename: str,
    *,
    date_candidates: Sequence[str],
    preferred_header: int = 0,
    preferred_sheet: Any = None,
    column_hints: Optional[Sequence[str]] = None,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """
    Read Excel: auto-detect header row (first rows scan), then load once.
    """
    hints = list(column_hints or []) + list(date_candidates or [])
    lower = filename.lower()
    # Filename may say .csv while bytes are still xlsx (or vice-versa)
    if _should_read_as_csv(filename, data):
        header_idx = int(preferred_header or 0)
        peek = read_csv_bytes(
            data, header=None, nrows=max(header_idx + 2, 6)
        )
        best = header_idx
        best_score = -10**9
        for i in range(min(len(peek), 8)):
            cells = [str(c).strip() if pd.notna(c) else "" for c in peek.iloc[i].tolist()]
            joined = " ".join(cells).lower()
            score = 0
            for cand in list(date_candidates) + list(hints):
                if str(cand).strip().lower() in joined:
                    score += 50
            if any(x in joined for x in ("barcode", "dc_date", "site name", "invoice")):
                score += 20
            if score > best_score:
                best_score = score
                best = i
        if best_score >= 50:
            header_idx = best
        df = read_csv_bytes(data, header=header_idx)
        df.columns = [str(c).strip() for c in df.columns]
        return df, {
            "sheet": None,
            "header_row": header_idx,
            "quality": _name_header_score(df, date_candidates),
        }

    from header_detect import detect_excel_header_row

    header_idx, sheet = detect_excel_header_row(
        data,
        column_hints=hints,
        preferred_sheet=preferred_sheet,
        preferred_header=int(preferred_header),
    )
    # Prefer calamine (Rust-based reader, already a project dependency) — it is
    # dramatically faster than openpyxl on large xlsx masters (Sales/Transfer
    # here are 30-85MB and this is the function re-read in full on the
    # "overlap last date" merge path). Fall back to openpyxl if calamine
    # can't read this particular file (e.g. an edge-case format it doesn't
    # support) so behavior/compatibility is unchanged, only speed improves.
    try:
        df = pd.read_excel(
            io.BytesIO(data),
            sheet_name=sheet,
            header=header_idx,
            engine="calamine",
        )
    except Exception:
        try:
            df = pd.read_excel(
                io.BytesIO(data),
                sheet_name=sheet,
                header=header_idx,
                engine="openpyxl",
            )
        except Exception as exc:
            raise ValueError(f"Could not read Excel: {filename}") from exc
    if df is None or df.empty:
        raise ValueError(f"Empty sheet after header detect: {filename} sheet={sheet} header={header_idx}")
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    q = _name_header_score(df, date_candidates)
    meta = {
        "sheet": sheet,
        "header_row": header_idx,
        "quality": q,
        "auto_header": True,
    }
    try:
        find_date_column(df, date_candidates)
    except ValueError as e:
        raise ValueError(
            f"No date column found after header auto-detect "
            f"(sheet={sheet}, header={header_idx}). Columns={list(df.columns)[:25]}"
        ) from e
    return df, meta


def write_tabular_bytes(df: pd.DataFrame, filename: str) -> Tuple[bytes, str]:
    lower = filename.lower()
    if lower.endswith(".csv"):
        buf = io.StringIO()
        df.to_csv(buf, index=False)
        return buf.getvalue().encode("utf-8"), "text/csv"
    out = io.BytesIO()
    # xlsxwriter is much faster than openpyxl for large sheets
    try:
        with pd.ExcelWriter(out, engine="xlsxwriter") as writer:
            df.to_excel(writer, index=False, sheet_name="Sheet1")
    except Exception:
        with pd.ExcelWriter(out, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Sheet1")
    return (
        out.getvalue(),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def _cell_for_xlsx(val: Any) -> Any:
    if val is None:
        return None
    try:
        if pd.isna(val):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(val, pd.Timestamp):
        return val.to_pydatetime()
    return val


def _align_new_rows_for_append(
    new_df: pd.DataFrame,
    header_labels: Sequence[str],
    *,
    date_candidates: Sequence[str],
    old_max: Any = None,
) -> Tuple[List[List[Any]], Dict[str, Any]]:
    """Filter newer rows and align to existing sheet/csv column order."""
    new = _dedupe_columns(new_df.copy())
    new.columns = [str(c).strip() for c in new.columns]
    date_col = find_date_column(new, date_candidates)
    days = to_day_series(new[date_col])
    mask = days.notna()
    if old_max is not None:
        cut = pd.Timestamp(old_max).normalize()
        mask = mask & (days > cut)
    new = new.loc[mask].copy()
    days = days.loc[mask]

    stats: Dict[str, Any] = {
        "mode": "binary_append",
        "date_column": str(date_col),
        "new_rows": int(len(new)),
        "old_rows_removed": 0,
        "note": "append onto existing file bytes (no full rewrite of old rows)",
    }
    if new.empty:
        stats["appended_rows"] = 0
        stats["note"] = "no newer rows after cut — kept current"
        return [], stats

    lookup = {_norm_col(c): c for c in new.columns}
    rows: List[List[Any]] = []
    for _, rec in new.iterrows():
        row: List[Any] = []
        for label in header_labels:
            key = _norm_col(label)
            src = lookup.get(key)
            row.append(_cell_for_xlsx(rec[src]) if src is not None else None)
        rows.append(row)

    stats["appended_rows"] = int(len(rows))
    stats["new_day_min"] = str(days.min().date()) if len(days) else None
    stats["new_day_max"] = str(days.max().date()) if len(days) else None
    return rows, stats


def _xlsx_stream_to_csv_text(
    old_bytes: bytes,
    *,
    header_idx: int,
    sheet_name: Optional[str],
) -> Tuple[str, List[str]]:
    """
    Stream an xlsx (read_only) into CSV text — avoids openpyxl writable load/save
    which hangs on ~100MB+ masters with 1M rows.
    """
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(old_bytes), read_only=True, data_only=True)
    try:
        names = wb.sheetnames
        if not names:
            raise ValueError("No sheets in workbook")
        if sheet_name not in names:
            sheet_name = names[0]
        ws = wb[sheet_name]
        buf = io.StringIO()
        writer = None
        header_labels: List[str] = []
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            cells = [("" if c is None else c) for c in row]
            if i < header_idx:
                continue
            if i == header_idx:
                header_labels = [str(c).strip() if c is not None else "" for c in cells]
                while header_labels and header_labels[-1] == "":
                    header_labels.pop()
                if not any(header_labels):
                    raise ValueError(f"Header row {header_idx} empty")
                # Keep only columns that have a label (trim trailing empties already done)
                import csv as _csv

                writer = _csv.writer(buf, lineterminator="\n")
                writer.writerow(header_labels)
                continue
            if writer is None:
                continue
            # Pad / trim to header width
            vals = list(cells[: len(header_labels)])
            while len(vals) < len(header_labels):
                vals.append("")
            # Normalize timestamps for CSV
            out_row = []
            for v in vals:
                if isinstance(v, pd.Timestamp):
                    out_row.append(v.isoformat(sep=" "))
                elif hasattr(v, "isoformat") and not isinstance(v, str):
                    try:
                        out_row.append(v.isoformat(sep=" "))
                    except Exception:
                        out_row.append(v)
                else:
                    out_row.append(v)
            writer.writerow(out_row)
        if not header_labels:
            raise ValueError(f"Header row {header_idx} not found while streaming xlsx")
        return buf.getvalue(), header_labels
    finally:
        wb.close()


def append_rows_to_tabular_bytes(
    old_bytes: bytes,
    old_filename: str,
    new_df: pd.DataFrame,
    *,
    date_candidates: Sequence[str],
    old_max: Any = None,
    preferred_header: int = 0,
    preferred_sheet: Any = None,
    column_hints: Optional[Sequence[str]] = None,
) -> Tuple[bytes, str, Dict[str, Any]]:
    """
    Fast path: keep existing current file bytes and append only newer rows.

    Large xlsx masters (>~20MB) are converted to CSV during append — openpyxl
    writable load+save hangs / OOMs on million-row workbooks.
    """
    lower = old_filename.lower()
    hints = list(column_hints or []) + list(date_candidates or [])

    if _should_read_as_csv(old_filename, old_bytes):
        header_idx = int(preferred_header or 0)
        peek = read_csv_bytes(
            old_bytes,
            header=None,
            nrows=max(header_idx + 2, 6),
        )
        header_labels = [
            str(c).strip() if pd.notna(c) else "" for c in peek.iloc[header_idx].tolist()
        ]
        if not any(header_labels):
            raise ValueError(f"CSV header empty at row {header_idx}: {old_filename}")
        rows, stats = _align_new_rows_for_append(
            new_df,
            header_labels,
            date_candidates=date_candidates,
            old_max=old_max,
        )
        if not rows:
            return old_bytes, "text/csv", stats

        # Append CSV lines without rewriting the existing body.
        text = old_bytes.decode("utf-8-sig", errors="replace")
        # If file was latin-1 / cp1252, re-decode with best effort for round-trip
        if "\ufffd" in text[:4096]:
            for enc in ("cp1252", "latin-1"):
                try:
                    cand = old_bytes.decode(enc)
                    if cand:
                        text = cand
                        break
                except Exception:
                    pass
        if text and not text.endswith("\n"):
            text += "\n"
        buf = io.StringIO()
        pd.DataFrame(rows, columns=list(header_labels)).to_csv(
            buf, index=False, header=False
        )
        out = (text + buf.getvalue()).encode("utf-8")
        stats["merged_bytes"] = int(len(out))
        return out, "text/csv", stats

    from header_detect import detect_excel_header_row

    header_idx, sheet_name = detect_excel_header_row(
        old_bytes,
        column_hints=hints,
        preferred_sheet=preferred_sheet,
        preferred_header=int(preferred_header),
    )

    # xlsx append: stream → CSV (never openpyxl writable load/save — hangs on 100MB+ masters)
    csv_text, header_labels = _xlsx_stream_to_csv_text(
        old_bytes, header_idx=header_idx, sheet_name=sheet_name
    )
    rows, stats = _align_new_rows_for_append(
        new_df,
        header_labels,
        date_candidates=date_candidates,
        old_max=old_max,
    )
    stats["mode"] = "binary_append_xlsx_to_csv"
    stats["sheet"] = str(sheet_name)
    stats["header_row"] = int(header_idx)
    if not rows:
        out = csv_text.encode("utf-8")
        stats["merged_bytes"] = int(len(out))
        stats["note"] = "converted xlsx→csv, no newer rows"
        return out, "text/csv", stats
    if csv_text and not csv_text.endswith("\n"):
        csv_text += "\n"
    buf = io.StringIO()
    pd.DataFrame(rows, columns=list(header_labels)).to_csv(
        buf, index=False, header=False
    )
    out = (csv_text + buf.getvalue()).encode("utf-8")
    stats["merged_bytes"] = int(len(out))
    stats["converted_from"] = "xlsx"
    return out, "text/csv", stats


def merge_append_newer_only(
    old_df: pd.DataFrame,
    new_df: pd.DataFrame,
    *,
    date_candidates: Sequence[str],
    old_max: Any = None,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """
    Fast path when incoming dates are strictly after current max:
    append only — do not scan/filter the whole old frame for overlaps.
    """
    new = _dedupe_columns(new_df.copy())
    new.columns = [str(c).strip() for c in new.columns]
    new_date_col = find_date_column(new, date_candidates)
    new["_merge_day"] = to_day_series(new[new_date_col])
    new = new[new["_merge_day"].notna()].copy()
    if old_max is not None:
        cut = pd.Timestamp(old_max).normalize()
        new = new[new["_merge_day"] > cut].copy()

    stats: Dict[str, Any] = {
        "mode": "append_newer_only",
        "date_column": str(new_date_col),
        "new_rows": int(len(new)),
        "old_rows_before": int(len(old_df)) if old_df is not None else 0,
        "old_rows_removed": 0,
        "note": "new_min > old_max — pure append (no old date scan)",
    }
    if new.empty:
        out = old_df.copy() if old_df is not None else new.drop(columns=["_merge_day"], errors="ignore")
        stats["merged_rows"] = int(len(out))
        stats["note"] = "no newer rows after cut — kept current"
        return out, stats

    new_clean = new.drop(columns=["_merge_day"])
    old = old_df.copy() if old_df is not None else pd.DataFrame()
    old.columns = [str(c).strip() for c in old.columns]
    # Align date column name if needed
    try:
        old_date_col = find_date_column_by_name(old, date_candidates) if not old.empty else None
        if (
            old_date_col
            and old_date_col != new_date_col
            and old_date_col in old.columns
            and new_date_col in new_clean.columns
        ):
            old = old.rename(columns={old_date_col: new_date_col})
    except Exception:
        pass

    merged = pd.concat([old, new_clean], ignore_index=True, sort=False)
    stats["merged_rows"] = int(len(merged))
    stats["new_day_min"] = str(new["_merge_day"].min().date()) if len(new) else None
    stats["new_day_max"] = str(new["_merge_day"].max().date()) if len(new) else None
    return merged, stats


def merge_by_date_overlap(
    old_df: Optional[pd.DataFrame],
    new_df: pd.DataFrame,
    *,
    date_candidates: Sequence[str],
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    if new_df is None or new_df.empty:
        raise ValueError("New dataframe is empty — nothing to merge")

    new = _dedupe_columns(new_df.copy())
    new.columns = [str(c).strip() for c in new.columns]
    new_date_col = find_date_column(new, date_candidates)
    new["_merge_day"] = to_day_series(new[new_date_col])
    new = new[new["_merge_day"].notna()].copy()
    new_days = set(new["_merge_day"].unique())

    stats: Dict[str, Any] = {
        "mode": "date_overlap_replace",
        "date_column": str(new_date_col),
        "new_rows": int(len(new)),
        "new_day_min": str(min(new_days).date()) if new_days else None,
        "new_day_max": str(max(new_days).date()) if new_days else None,
        "overlap_days": [],
        "old_rows_before": 0,
        "old_rows_removed": 0,
        "merged_rows": 0,
    }

    if old_df is None or old_df.empty:
        out = new.drop(columns=["_merge_day"])
        stats["old_rows_before"] = 0
        stats["merged_rows"] = int(len(out))
        stats["note"] = "no_existing_current — promoted incoming"
        return out, stats

    old = _dedupe_columns(old_df.copy())
    old.columns = [str(c).strip() for c in old.columns]
    old_date_col = find_date_column(old, date_candidates)

    old["_merge_day"] = to_day_series(old[old_date_col])
    old = old[old["_merge_day"].notna()].copy()
    old_days = set(old["_merge_day"].unique())
    overlap = sorted(old_days & new_days)

    stats["old_rows_before"] = int(len(old))
    stats["old_day_min"] = str(min(old_days).date()) if old_days else None
    stats["old_day_max"] = str(max(old_days).date()) if old_days else None
    stats["overlap_days"] = [str(d.date()) for d in overlap]

    if overlap:
        before = len(old)
        old = old[~old["_merge_day"].isin(overlap)]
        stats["old_rows_removed"] = int(before - len(old))
    else:
        stats["old_rows_removed"] = 0
        stats["note"] = "no_overlap — pure append"

    old_clean = old.drop(columns=["_merge_day"])
    new_clean = new.drop(columns=["_merge_day"])
    if old_date_col != new_date_col and old_date_col in old_clean.columns and new_date_col in new_clean.columns:
        old_clean = old_clean.rename(columns={old_date_col: new_date_col})

    merged = pd.concat([old_clean, new_clean], ignore_index=True, sort=False)

    try:
        sort_col = find_date_column_by_name(merged, date_candidates) or find_date_column(merged, date_candidates)
        merged = merged.sort_values(by=sort_col, kind="mergesort").reset_index(drop=True)
    except Exception:
        pass

    stats["merged_rows"] = int(len(merged))
    return merged, stats

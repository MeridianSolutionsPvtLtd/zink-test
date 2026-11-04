"""
Ginesys Raw_Files → category incoming/ (incremental preview + copy).

Daily reports keep dated copies in Raw_Files/. Pipeline still uses
input/<Category>/incoming → current merge (same as Home upload).

Rules:
  date_merge (sales, transfer):
    - filename date not yet in coverage → plan merge (append or gap-fill)
    - missing days can arrive later (after current max advanced) and still merge
  replace (SOH, warehouse, item master, combined SOH): latest file only
"""
from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from azure.storage.blob import ContentSettings

log = logging.getLogger("pipeline.ginesys_raw")

PIPELINE_DIR = Path(__file__).resolve().parent
BACKEND_DIR = PIPELINE_DIR.parent

# Filename pattern → pipeline category
# One daily report supplies PT, SSL, Centro, Iconic and EBO.
# Legacy "3rd Party Sales ... Stock Pulse" reports are intentionally unmapped.
GINESYS_RULES: List[Tuple[str, str]] = [
    (r"fy[\s_-]*year[\s_&-]*(?:and[\s_-]*)?fy[\s_-]*week|financial[\s_-]*week[\s_-]*info", "financial-week-info"),
    (r"^3rd\s+party\s+and\s+ebo\s+sale\s+report\s+stock\s+pulse(?=[\s_\\-]|$)", "sales-pt-ssl-centro-iconic"),
    (r"dispatch\s*report\s*stock\s*pulse|dispatch\s*report", "transfer-pt-ssl-centro-iconic"),
    (r"warehouse\s*closing\s*stock", "warehouse-soh"),
    (r"item[_\s-]*master", "item-master"),
    (r"customer[_\s-]*master", "customer-master"),
    (r"store[_\s-]*grade", "store-grade-list"),
    (r"top[_\s-]*seller", "top-sellers-list"),
    (r"ebo\s*closing\s*stock", "ebo-soh"),
    # All-store daily SOH (replaces per-channel SOH for blob auto-fetch)
    (r"daily[_\s-]*stock|stock[_\s-]*template|all[_\s-]*store[_\s-]*soh", "soh-master"),
    (r"combined[_\s-]*soh|soh[_\s-]*reconciliation", "combined-soh"),
    (r"grc\s*report\s*stock\s*pulse|grc[_\s-]*report|grn[_\s-]*report", "grc-report"),
    (r"rtv[_\s-]*data|rtv[_\s-]*report|sales\s*return|return\s*to\s*vendor", "rtv-data"),
    # Store↔store transfers (till-date + intermittent dumps)
    (r"\bibt[_\s-]*(report|data)\b|lfs\s*site\s*to\s*site", "ibt-data"),
]

# Categories that may need a till-date / march→now bulk dump as current baseline
# (daily Stock_Pulse alone must not sit on a corrupt thin master).
BULK_BASELINE_CATEGORY_IDS = frozenset({"rtv-data", "ibt-data"})

RAW_PREFIX_CANDIDATES = ("Raw_Files/", "raw_files/", "Raw_files/", "RAW_FILES/")


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(BACKEND_DIR / ".env")
    except ImportError:
        pass


def _env(*keys: str, default: str = "") -> str:
    for k in keys:
        v = os.environ.get(k)
        if v:
            return v
    return default


def _norm_prefix(prefix: str) -> str:
    p = (prefix or "").strip().replace("\\", "/")
    if p and not p.endswith("/"):
        p += "/"
    return p.lstrip("/")


def ginesys_category(filename: str) -> Optional[str]:
    low = filename.lower()
    for pat, cid in GINESYS_RULES:
        if re.search(pat, low):
            return cid
    return None


def parse_file_date(filename: str) -> Optional[date]:
    """Extract report date from Ginesys names: 2026-08-14 or 14-08-2026."""
    m = re.search(r"(20\d{2})-(\d{2})-(\d{2})", filename)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    m = re.search(r"(\d{1,2})-(\d{1,2})-(20\d{2})", filename)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return date(y, mo, d)
        except ValueError:
            try:
                return date(y, d, mo)
            except ValueError:
                return None
    return None


def is_history_or_bulk_dump(filename: str) -> bool:
    """
    Full-year / multi-month history workbooks must NOT enter daily date_merge.

    Example that was breaking blob merge:
      3rd Party Sales Jan'25 to Dec'25_ _2026-08-21_ .xlsx
    Filename date looks "new" (2026-08-21) but content is a whole-year dump —
    appending it hangs/OOM and can corrupt current/.
    """
    low = (filename or "").lower().replace("’", "'")
    if re.search(r"jan\s*'?\s*\d{2}\s*to\s*dec", low):
        return True
    if re.search(r"\b(fy|full[\s_-]*year|ytd[\s_-]*history|sales[\s_-]*history)\b", low):
        return True
    if re.search(r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s*'?\s*\d{2}\s+to", low):
        return True
    if re.search(r"till[\s_-]*date", low):
        return True
    return False


def _get_container():
    from azure.storage.blob import BlobServiceClient

    conn = _env("AZURE_STORAGE_CONNECTION_STRING")
    if not conn:
        raise RuntimeError("Set AZURE_STORAGE_CONNECTION_STRING in .env")
    container = _env("AZURE_STORAGE_CONTAINER", default="zinc-data")
    svc = BlobServiceClient.from_connection_string(conn)
    cc = svc.get_container_client(container)
    if not cc.exists():
        raise RuntimeError(f"Blob container not found: {container}")
    return cc, container


def _parse_iso_date_str(raw: Any) -> Optional[date]:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw)[:10]).date()
    except ValueError:
        try:
            return datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()
        except ValueError:
            return None


def current_max_date(cc, input_prefix: str, category_id: str) -> Optional[date]:
    """
    Best known "data as of" date for a category's current/ file.

    Order of preference:
      1) *.date_bounds.json sidecar (instant, no download).
      2) One-time bootstrap: scan the current file's date column directly
         (only for categories with a real date column) and save the sidecar
         so every later call is instant again. This mirrors the bootstrap
         merge_one_category() already does mid-merge — done here too because
         some categories never hit that code path (e.g. their first merge
         short-circuited via the same-file-size skip, before any sidecar was
         ever written), which otherwise leaves current_max permanently
         unknown (None) for them.
      3) Snapshot/master categories with no date column (SOH, Item Master,
         Customer Master, etc.) → fall back to the current file's blob
         last-modified time, so the UI always has *some* "as of" answer.
    """
    try:
        from pipeline.merge_category_inputs import (
            CATEGORY_MERGE_CONFIG,
            _load_date_bounds_meta,
            _save_date_bounds_meta,
            _category_prefixes,
            _list_files,
        )
    except ImportError:
        from merge_category_inputs import (
            CATEGORY_MERGE_CONFIG,
            _load_date_bounds_meta,
            _save_date_bounds_meta,
            _category_prefixes,
            _list_files,
        )

    cfg = CATEGORY_MERGE_CONFIG.get(category_id)
    if not cfg:
        return None
    folder = cfg["folder"]
    canonical = cfg.get("canonical_name") or ""
    paths = _category_prefixes(input_prefix, folder)

    meta = _load_date_bounds_meta(cc, paths["current"], canonical)
    found = _parse_iso_date_str(meta.get("max_str") or meta.get("max")) if meta else None
    if found:
        return found

    current_files = _list_files(cc, paths["current"])
    if not current_files:
        return None
    current_blob = None
    for b in current_files:
        if Path(b.name).name == canonical:
            current_blob = b
            break
    if current_blob is None:
        current_blob = current_files[0]

    date_candidates = cfg.get("date_candidates")
    if date_candidates:
        try:
            try:
                from date_merge import excel_date_bounds_fast
            except ImportError:
                from pipeline.date_merge import excel_date_bounds_fast
            try:
                from header_detect import resolve_category_layout
            except ImportError:
                from pipeline.header_detect import resolve_category_layout

            data = cc.get_blob_client(current_blob.name).download_blob().readall()
            layout = resolve_category_layout(cfg, data, Path(current_blob.name).name)
            bounds = excel_date_bounds_fast(
                data,
                Path(current_blob.name).name,
                date_candidates=date_candidates,
                preferred_header=int(layout["header_row"]),
                preferred_sheet=layout.get("sheet") or cfg.get("sheet_name"),
                column_hints=layout.get("column_hints") or cfg.get("column_hints"),
            )
            found = _parse_iso_date_str(bounds.get("max_str") or bounds.get("max"))
            if found:
                _save_date_bounds_meta(cc, paths["current"], canonical, bounds, dry_run=False)
                return found
        except Exception:
            log.info(
                "current_max_date bootstrap scan failed for %s (%s)",
                category_id,
                current_blob.name,
                exc_info=True,
            )

    # No usable date column (or scan failed) — last-modified of current file
    # is still a truthful "data as of" answer for snapshot/master categories.
    lm = getattr(current_blob, "last_modified", None)
    if lm is not None:
        try:
            return lm.date()
        except AttributeError:
            return None
    return None


def _list_raw_files(cc, raw_prefix: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for b in cc.list_blobs(name_starts_with=raw_prefix):
        if b.name.endswith("/") or Path(b.name).name.startswith("~$"):
            continue
        if Path(b.name).suffix.lower() not in {".xlsx", ".xls", ".csv"}:
            continue
        fname = Path(b.name).name
        cid = ginesys_category(fname)
        fd = parse_file_date(fname)
        out.append(
            {
                "blob": b.name,
                "filename": fname,
                "size": int(b.size or 0),
                "last_modified": str(getattr(b, "last_modified", "") or ""),
                "category_id": cid,
                "file_date": fd.isoformat() if fd else None,
            }
        )
    return sorted(out, key=lambda r: (r["filename"], r["blob"]))


def _raw_prefix_candidates(preferred: str) -> List[str]:
    out: List[str] = []
    for p in (preferred, *RAW_PREFIX_CANDIDATES):
        n = _norm_prefix(p or "Raw_Files/")
        if n not in out:
            out.append(n)
    return out


def _list_raw_with_fallback(cc, preferred: str) -> Tuple[str, List[Dict[str, Any]], List[str]]:
    tried = _raw_prefix_candidates(preferred)
    for p in tried:
        listed = _list_raw_files(cc, p)
        if listed:
            return p, listed, tried
    return _norm_prefix(preferred or "Raw_Files/"), [], tried


def _latest_replace_file(files: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not files:
        return None
    dated = [f for f in files if f.get("file_date")]
    pool = dated or files

    def _sort_key(r: Dict[str, Any]):
        f_date = r.get("file_date") or ""
        fname = (r.get("filename") or "").lower()
        # Explicitly prioritize "stock pulse" named reports (e.g. Warehouse Closing Stock Stock Pulse)
        is_sp = 1 if "stock pulse" in fname or "stockpulse" in fname else 0
        l_mod = r.get("last_modified") or ""
        return (f_date, is_sp, l_mod)

    return max(pool, key=_sort_key)


# Categories where we report calendar date coverage / missing daily files
COVERAGE_CATEGORY_IDS = (
    "sales-pt-ssl-centro-iconic",
    "ebo-sales",
    "transfer-pt-ssl-centro-iconic",
    "transfer-ebo",
    "grc-report",
    "rtv-data",
    "ibt-data",
)


def _daterange_inclusive(start: date, end: date) -> List[date]:
    if end < start:
        return []
    out: List[date] = []
    cur = start
    while cur <= end:
        out.append(cur)
        cur += timedelta(days=1)
    return out


def _holes_in_sorted_dates(dates: List[date]) -> List[str]:
    """Calendar gaps between first and last known file dates."""
    if len(dates) < 2:
        return []
    holes: List[str] = []
    for a, b in zip(dates, dates[1:]):
        cur = a + timedelta(days=1)
        while cur < b:
            holes.append(cur.isoformat())
            cur += timedelta(days=1)
    return holes


def _load_bounds_minmax(cc, input_prefix: str, category_id: str) -> Tuple[Optional[date], Optional[date]]:
    try:
        from pipeline.merge_category_inputs import (
            CATEGORY_MERGE_CONFIG,
            _category_prefixes,
            _load_date_bounds_meta,
        )
    except ImportError:
        from merge_category_inputs import (  # type: ignore
            CATEGORY_MERGE_CONFIG,
            _category_prefixes,
            _load_date_bounds_meta,
        )

    cfg = CATEGORY_MERGE_CONFIG.get(category_id) or {}
    folder = cfg.get("folder")
    canonical = cfg.get("canonical_name") or ""
    if not folder or not canonical:
        return None, None
    paths = _category_prefixes(input_prefix, folder)
    meta = _load_date_bounds_meta(cc, paths["current"], canonical) or {}
    dmin = _parse_iso_date_str(meta.get("min_str") or meta.get("min"))
    dmax = _parse_iso_date_str(meta.get("max_str") or meta.get("max"))
    return dmin, dmax


def build_date_coverage(
    cc,
    input_prefix: str,
    listed: List[Dict[str, Any]],
    planned: List[Dict[str, Any]],
    *,
    as_of: Optional[date] = None,
) -> Dict[str, Any]:
    """
    For Sales / Transfer: show current date_bounds range, files about to merge,
    and missing daily dates (no Raw_Files dump for that day).

    Example: current max=2026-08-16, today=2026-08-24, no file for 18 → missing includes 2026-08-18.
    """
    end = as_of or (date.today() - timedelta(days=1))  # expect files through yesterday
    by_cat_raw: Dict[str, List[date]] = {}
    for rec in listed:
        cid = rec.get("category_id")
        if cid not in COVERAGE_CATEGORY_IDS:
            continue
        fname = str(rec.get("filename") or "")
        if is_history_or_bulk_dump(fname):
            continue
        fd = date.fromisoformat(rec["file_date"]) if rec.get("file_date") else None
        if fd is None:
            continue
        by_cat_raw.setdefault(str(cid), []).append(fd)

    planned_by_cat: Dict[str, List[str]] = {}
    for rec in planned:
        cid = rec.get("category_id")
        if cid not in COVERAGE_CATEGORY_IDS:
            continue
        if rec.get("file_date"):
            planned_by_cat.setdefault(str(cid), []).append(str(rec["file_date"]))

    report: Dict[str, Any] = {}
    for cid in COVERAGE_CATEGORY_IDS:
        dmin, dmax = _load_bounds_minmax(cc, input_prefix, cid)
        raw_dates = sorted(set(by_cat_raw.get(cid, [])))
        raw_set = set(raw_dates)
        merge_dates = sorted(set(planned_by_cat.get(cid, [])))

        # After current max through yesterday: which daily files never arrived?
        missing_after_current: List[str] = []
        if dmax is not None:
            for d in _daterange_inclusive(dmax + timedelta(days=1), end):
                if d not in raw_set:
                    missing_after_current.append(d.isoformat())

        # Holes inside Raw_Files sequence (have 17 & 19, missing 18)
        holes_in_raw = _holes_in_sorted_dates(raw_dates)

        # Also: days still only in Raw (will merge) vs already current
        waiting = [d.isoformat() for d in raw_dates if dmax is None or d > dmax]

        report[cid] = {
            "category_id": cid,
            "current_min": dmin.isoformat() if dmin else None,
            "current_max": dmax.isoformat() if dmax else None,
            "raw_file_dates": [d.isoformat() for d in raw_dates],
            "will_merge_dates": merge_dates or waiting,
            "missing_dates": missing_after_current,
            "gaps_in_raw_files": holes_in_raw,
            "expected_through": end.isoformat(),
            "ok": len(missing_after_current) == 0 and len(holes_in_raw) == 0,
        }
    return report


def _load_coverage_meta(
    cc, input_prefix: str, category_id: str, raw_dates: List[date]
) -> Dict[str, Any]:
    """Load date_bounds sidecar + bootstrap sparse coverage for gap-fill planning."""
    try:
        from pipeline.merge_category_inputs import (
            CATEGORY_MERGE_CONFIG,
            _category_prefixes,
            _load_date_bounds_meta_raw,
        )
        from pipeline.date_merge import bootstrap_coverage_meta
    except ImportError:
        from merge_category_inputs import (  # type: ignore
            CATEGORY_MERGE_CONFIG,
            _category_prefixes,
            _load_date_bounds_meta_raw,
        )
        from date_merge import bootstrap_coverage_meta  # type: ignore

    cfg = CATEGORY_MERGE_CONFIG.get(category_id) or {}
    folder = cfg.get("folder")
    canonical = cfg.get("canonical_name") or ""
    meta: Dict[str, Any] = {}
    if folder and canonical:
        paths = _category_prefixes(input_prefix, folder)
        meta = _load_date_bounds_meta_raw(cc, paths["current"], canonical) or {}
    return bootstrap_coverage_meta(meta, raw_file_dates=raw_dates, current_max=meta.get("max_str"))


def _best_bulk_raw_file(files: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Largest till-date / multi-month dump among Raw_Files rows for a category."""
    bulks = [
        f
        for f in files
        if is_history_or_bulk_dump(str(f.get("filename") or ""))
    ]
    if not bulks:
        return None
    till = [f for f in bulks if "till" in str(f.get("filename") or "").lower()]
    pool = till or bulks
    return max(
        pool,
        key=lambda r: (
            int(r.get("size") or 0),
            str(r.get("file_date") or ""),
            str(r.get("last_modified") or ""),
        ),
    )


def _needs_bulk_baseline(cc, input_prefix: str, category_id: str) -> Tuple[bool, str]:
    """
    RTV: corrupt CSV (IBT-shaped / almost no RETURN_QUANTITY).
    IBT: missing current file.
    """
    if category_id == "rtv-data":
        try:
            from pipeline.repair_movement_masters import current_rtv_is_corrupt

            bad, info = current_rtv_is_corrupt(cc, input_prefix)
            if bad:
                return True, str((info or {}).get("reason") or "rtv_corrupt")
            return False, "ok"
        except Exception as exc:
            return True, f"rtv_check_error:{exc}"
    if category_id == "ibt-data":
        try:
            from pipeline.merge_category_inputs import (
                CATEGORY_MERGE_CONFIG,
                _category_prefixes,
                _list_files,
            )

            cfg = CATEGORY_MERGE_CONFIG.get(category_id) or {}
            paths = _category_prefixes(input_prefix, cfg.get("folder") or "IBT Data")
            cur = _list_files(cc, paths["current"])
            if not cur:
                return True, "ibt_current_missing"
            return False, "ok"
        except Exception as exc:
            return True, f"ibt_check_error:{exc}"
    return False, "n/a"


def preview_raw_fetch() -> Dict[str, Any]:
    """
    List Raw_Files and mark which ones would be copied to incoming/.
    Does not copy or merge.
    """
    _load_dotenv()
    try:
        from pipeline.merge_category_inputs import CATEGORY_MERGE_CONFIG
        from pipeline.date_merge import is_file_date_covered
    except ImportError:
        from merge_category_inputs import CATEGORY_MERGE_CONFIG
        from date_merge import is_file_date_covered

    logging.getLogger("azure").setLevel(logging.WARNING)
    cc, container = _get_container()
    input_prefix = _norm_prefix(_env("AZURE_BLOB_INPUT_PREFIX", default="input/"))
    preferred_raw = _norm_prefix(_env("AZURE_BLOB_RAW_PREFIX", default="Raw_Files/"))

    raw_prefix, listed, tried_prefixes = _list_raw_with_fallback(cc, preferred_raw)
    by_cat: Dict[str, List[Dict[str, Any]]] = {}
    for rec in listed:
        cid = rec["category_id"] or "unmapped"
        by_cat.setdefault(cid, []).append(rec)

    # Only compute current_max for date_merge categories that actually appear
    # in Raw_Files listing. This keeps preview fast and avoids scanning many
    # unrelated snapshot/master categories.
    current_max: Dict[str, Optional[str]] = {}
    for cid in by_cat.keys():
        cfg = CATEGORY_MERGE_CONFIG.get(cid)
        if not cfg or (cfg.get("mode") or "replace") != "date_merge":
            continue
        mx = current_max_date(cc, input_prefix, cid)
        current_max[cid] = mx.isoformat() if mx else None

    planned: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []

    for cid, files in by_cat.items():
        cfg = CATEGORY_MERGE_CONFIG.get(cid)
        if not cfg:
            for rec in files:
                skipped.append({**rec, "reason": "unmapped_filename"})
            continue
        mode = cfg.get("mode") or "replace"
        if mode == "date_merge":
            mx_s = current_max.get(cid)
            mx = date.fromisoformat(mx_s) if mx_s else None
            raw_dates: List[date] = []
            for rec in files:
                fname = str(rec.get("filename") or "")
                if is_history_or_bulk_dump(fname):
                    continue
                if rec.get("file_date"):
                    try:
                        raw_dates.append(date.fromisoformat(str(rec["file_date"])))
                    except ValueError:
                        pass
            cov_meta = _load_coverage_meta(cc, input_prefix, cid, raw_dates)

            # RTV/IBT: if current master is corrupt/missing, promote best till-date
            # bulk dump as baseline (daily merge alone cannot fix a polluted CSV).
            bulk_baseline: Optional[Dict[str, Any]] = None
            if cid in BULK_BASELINE_CATEGORY_IDS:
                need_bulk, bulk_why = _needs_bulk_baseline(cc, input_prefix, cid)
                if need_bulk:
                    bulk_baseline = _best_bulk_raw_file(files)
                    if bulk_baseline:
                        planned.append(
                            {
                                **bulk_baseline,
                                "action": "replace_bulk_baseline",
                                "current_max": mx.isoformat() if mx else None,
                                "folder": cfg["folder"],
                                "baseline_reason": bulk_why,
                            }
                        )
                        # After baseline replace, treat coverage max as bulk filename
                        # date so later Stock_Pulse days still plan in the same run
                        # only when their file_date is after the bulk stamp.
                        bfd = bulk_baseline.get("file_date")
                        if bfd:
                            try:
                                mx = date.fromisoformat(str(bfd))
                                mx_s = mx.isoformat()
                                current_max[cid] = mx_s
                                if isinstance(cov_meta, dict):
                                    cov_meta = dict(cov_meta)
                                    cov_meta["assumed_covered_through"] = mx_s
                                    cov_meta["max_str"] = mx_s
                            except ValueError:
                                pass
                    else:
                        skipped.append(
                            {
                                "category_id": cid,
                                "reason": f"need_bulk_baseline_but_none_in_raw ({bulk_why})",
                            }
                        )

            for rec in sorted(files, key=lambda r: r.get("file_date") or ""):
                fname = str(rec.get("filename") or "")
                if is_history_or_bulk_dump(fname):
                    # Already planned as replace_bulk_baseline (or skipped)
                    if bulk_baseline and rec.get("blob") == bulk_baseline.get("blob"):
                        continue
                    skipped.append(
                        {
                            **rec,
                            "reason": "history_bulk_dump_skip_daily_merge",
                        }
                    )
                    continue
                fd = date.fromisoformat(rec["file_date"]) if rec.get("file_date") else None
                if fd is None:
                    skipped.append({**rec, "reason": "no_date_in_filename"})
                    continue
                if mx is not None and is_file_date_covered(fd, cov_meta):
                    skipped.append(
                        {
                            **rec,
                            "reason": f"already_in_current (max={mx.isoformat()})",
                            "current_max": mx.isoformat(),
                        }
                    )
                    continue
                action = "date_merge_append"
                if mx is not None and fd <= mx:
                    action = "date_merge_gap_fill"
                planned.append(
                    {
                        **rec,
                        "action": action,
                        "current_max": mx.isoformat() if mx else None,
                        "folder": cfg["folder"],
                    }
                )
        else:
            latest = _latest_replace_file(files)
            if not latest:
                skipped.extend({**f, "reason": "empty"} for f in files)
                continue
            planned.append(
                {
                    **latest,
                    "action": "replace_latest",
                    "current_max": None,
                    "folder": cfg["folder"],
                }
            )
            for rec in files:
                if rec["blob"] != latest["blob"]:
                    skipped.append({**rec, "reason": "older_than_latest"})

    empty_hint = None
    if not listed:
        empty_hint = (
            f"No Excel/CSV files under blob prefix {raw_prefix!r} "
            f"(tried {', '.join(tried_prefixes)}). "
            "Ginesys dumps should be in container zinc-data folder Raw_Files/."
        )

    for master_id in (
        "sales-master-pt-ssl-centro-iconic",
        "transfer-master-pt-ssl-centro-iconic",
    ):
        if current_max.get(master_id):
            continue
        try:
            mx = current_max_date(cc, input_prefix, master_id)
            current_max[master_id] = mx.isoformat() if mx else None
        except Exception:
            current_max[master_id] = None

    coverage = build_date_coverage(cc, input_prefix, listed, planned)

    return {
        "ok": True,
        "container": container,
        "raw_prefix": raw_prefix,
        "tried_prefixes": tried_prefixes,
        "current_max": current_max,
        "raw_file_count": len(listed),
        "planned": planned,
        "skipped": skipped,
        "coverage": coverage,
        "empty_hint": empty_hint,
        "hint": (
            "Sales/Transfer: merge whatever daily dates arrived; missing days merge later "
            "(gap-fill even after current max moved ahead). History year-dumps skipped. "
            "SOH: daily_stock_template → SOH-Master (latest replace). "
            "Item/Customer/Store Grade/Top Sellers: latest replace. "
            "Per-channel SOH still available via Manual Upload. "
            "coverage.missing_dates = daily files not yet in Raw_Files after current max."
        ),
    }


def fetch_raw_to_incoming(
    *,
    dry_run: bool = False,
    only_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Copy planned Raw_Files into input/<Category>/incoming/ then caller merges
    with merge_all_categories (same as POST /upload).

    only_ids: if set, copy/merge only these category ids.
    """
    _load_dotenv()
    try:
        from pipeline.merge_category_inputs import (
            CATEGORY_MERGE_CONFIG,
            _category_prefixes,
            archive_incoming_to_history,
            stash_incoming_bytes,
        )
    except ImportError:
        from merge_category_inputs import (
            CATEGORY_MERGE_CONFIG,
            _category_prefixes,
            archive_incoming_to_history,
            stash_incoming_bytes,
        )

    preview = preview_raw_fetch()
    planned: List[Dict[str, Any]] = list(preview.get("planned") or [])
    want = {str(x).strip() for x in (only_ids or []) if str(x).strip()} or None
    if want:
        planned = [r for r in planned if r.get("category_id") in want]

    cc, container = _get_container()
    input_prefix = _norm_prefix(_env("AZURE_BLOB_INPUT_PREFIX", default="input/"))

    copied: List[Dict[str, Any]] = []
    errors: List[str] = []
    archived: List[Dict[str, Any]] = []
    touched: List[str] = []

    by_cat: Dict[str, List[Dict[str, Any]]] = {}
    for rec in planned:
        cid = rec.get("category_id")
        if not cid or cid not in CATEGORY_MERGE_CONFIG:
            continue
        by_cat.setdefault(cid, []).append(rec)

    for cid, recs in by_cat.items():
        cfg = CATEGORY_MERGE_CONFIG[cid]
        folder = cfg["folder"]
        canonical = cfg.get("canonical_name") or Path(recs[0]["filename"]).name
        paths = _category_prefixes(input_prefix, folder)
        try:
            if not dry_run:
                arch = archive_incoming_to_history(folder, dry_run=False)
                archived.append({"category_id": cid, **(arch if isinstance(arch, dict) else {})})
        except Exception as e:
            errors.append(f"archive {folder}: {e}")

        # Bulk baseline first (clears corrupt current), then dated dailies
        bulk_recs = [r for r in recs if r.get("action") == "replace_bulk_baseline"]
        daily_recs = [r for r in recs if r.get("action") != "replace_bulk_baseline"]
        daily_recs = sorted(daily_recs, key=lambda r: r.get("file_date") or "")

        if bulk_recs and not dry_run:
            # Wipe current so merge promotes the till-date dump as new master
            try:
                for b in cc.list_blobs(name_starts_with=paths["current"]):
                    if str(b.name).lower().endswith(
                        (".csv", ".xlsx", ".xls", ".parquet", ".json")
                    ):
                        # keep history sidecars only under history/
                        cc.delete_blob(b.name)
                        log.info("Cleared current before bulk baseline: %s", b.name)
            except Exception as e:
                errors.append(f"clear current {folder}: {e}")

        ordered = bulk_recs[:1] + daily_recs  # at most one bulk baseline
        for rec in ordered:
            src = rec["blob"]
            fd = rec.get("file_date") or "undated"
            stem = Path(canonical).stem
            src_ext = Path(rec["filename"]).suffix or ".xlsx"
            if rec.get("action") == "replace_bulk_baseline":
                # Keep source extension; merge normalizes to canonical CSV/XLSX
                dest_name = f"{stem}{src_ext}"
            elif cid == "financial-week-info":
                dest_name = f"{stem}{src_ext}"
            elif len(ordered) > 1:
                dest_name = f"{stem}_{fd}{src_ext}"
            else:
                dest_name = canonical if Path(canonical).suffix else f"{stem}{src_ext}"
            dest = f"{paths['incoming']}{dest_name}"
            try:
                if dry_run:
                    copied.append({**rec, "dest": dest, "dry_run": True})
                    continue
                data = cc.get_blob_client(src).download_blob().readall()
                # RTV till-date xlsx → CSV bytes so DM + canonical stay aligned
                if (
                    cid == "rtv-data"
                    and rec.get("action") == "replace_bulk_baseline"
                    and (src_ext.lower() in (".xlsx", ".xls") or data[:2] == b"PK")
                ):
                    try:
                        from pipeline.repair_movement_masters import _xlsx_rtv_to_csv_bytes

                        data, _meta = _xlsx_rtv_to_csv_bytes(data)
                        dest_name = f"{stem}.csv"
                        dest = f"{paths['incoming']}{dest_name}"
                    except Exception as conv_exc:
                        log.warning("RTV xlsx→csv normalize skipped: %s", conv_exc)
                ctype = (
                    "text/csv"
                    if dest.lower().endswith(".csv")
                    else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
                cc.upload_blob(
                    name=dest,
                    data=data,
                    overwrite=True,
                    content_settings=ContentSettings(content_type=ctype),
                )
                stash_incoming_bytes(dest, data)
                copied.append({**rec, "dest": dest, "bytes": len(data)})
            except Exception as e:
                errors.append(f"{src} -> {dest}: {e}")
                log.exception("copy failed %s", src)
        if ordered:
            touched.append(cid)

    return {
        "ok": len(errors) == 0,
        "dry_run": dry_run,
        "container": container,
        "preview": preview,
        "copied": copied,
        "archived": archived,
        "touched_categories": touched,
        "only_ids": list(want) if want else None,
        "errors": errors,
    }


def collect_data_gap_warnings(
    *,
    preview: Optional[Dict[str, Any]] = None,
    as_of: Optional[date] = None,
) -> List[str]:
    """
    Human-readable warnings when daily Raw_Files / snapshot dates are missing.
    Does not block the pipeline — caller logs these and continues.
    """
    try:
        from pipeline.merge_category_inputs import CATEGORY_MERGE_CONFIG
    except ImportError:
        from merge_category_inputs import CATEGORY_MERGE_CONFIG  # type: ignore

    prev = preview if preview is not None else preview_raw_fetch()
    coverage = prev.get("coverage") or {}
    expected = as_of or (date.today() - timedelta(days=1))
    warnings: List[str] = []

    def _label(cid: str) -> str:
        cfg = CATEGORY_MERGE_CONFIG.get(cid) or {}
        return str(cfg.get("folder") or cid)

    for cid, cov in coverage.items():
        if not isinstance(cov, dict):
            continue
        label = _label(str(cid))
        miss = list(cov.get("missing_dates") or [])
        gaps = list(cov.get("gaps_in_raw_files") or [])
        if miss:
            sample = ", ".join(miss[:12])
            extra = f" (+{len(miss) - 12} more)" if len(miss) > 12 else ""
            warnings.append(
                f"WARNING [{label}]: missing daily Raw_Files dates "
                f"(expected through {cov.get('expected_through') or expected.isoformat()}): "
                f"{sample}{extra}"
            )
        if gaps:
            sample = ", ".join(gaps[:12])
            extra = f" (+{len(gaps) - 12} more)" if len(gaps) > 12 else ""
            warnings.append(
                f"WARNING [{label}]: gaps inside Raw_Files date sequence: {sample}{extra}"
            )

    # Snapshot / replace categories: warn if latest file older than yesterday
    latest_by_cat: Dict[str, Optional[date]] = {}
    for rec in list(prev.get("planned") or []) + list(prev.get("skipped") or []):
        cid = str(rec.get("category_id") or "")
        if not cid or cid not in CATEGORY_MERGE_CONFIG:
            continue
        mode = (CATEGORY_MERGE_CONFIG[cid].get("mode") or "replace").strip()
        if mode == "date_merge":
            continue
        fd = None
        if rec.get("file_date"):
            try:
                fd = date.fromisoformat(str(rec["file_date"]))
            except ValueError:
                fd = None
        if fd is None:
            continue
        prev_fd = latest_by_cat.get(cid)
        if prev_fd is None or fd > prev_fd:
            latest_by_cat[cid] = fd

    for cid, fd in latest_by_cat.items():
        if fd is None:
            continue
        if fd < expected:
            warnings.append(
                f"WARNING [{_label(cid)}]: latest Raw_Files dump is {fd.isoformat()} "
                f"(older than expected {expected.isoformat()}) — check Ginesys upload"
            )

    return warnings

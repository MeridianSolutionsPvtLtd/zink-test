"""
COMBINED_SOH current + dated history for last_week_store_soh.

Layout:
  output_files/COMBINED_SOH.xlsx                      ← compat mirror (pipeline reads this)
  output_files/combined_soh/current/COMBINED_SOH.xlsx ← current
  output_files/combined_soh/history/COMBINED_SOH_YYYY-MM-DD.xlsx
  output_files/combined_soh/COMBINED_SOH.meta.json

Rules:
  1) Pehli / har pipeline run:
       - write NEW file → current/
       - SAME file → history/ with stamp = (run_day - 1)
       - same stamp same day → overwrite
  2) last_week_store_soh:
       - prefer history for (today - 7)
       - else nearest within ±2 days
  3) Keep ~10 newest history files (prune older).
"""
from __future__ import annotations

import json
import logging
import re
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

log = logging.getLogger("pipeline.combined_soh_store")

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output_files"

# Compat path used across pipeline scripts
COMPAT_CURRENT = OUTPUT_DIR / "COMBINED_SOH.xlsx"
COMPAT_CURRENT_CSV = OUTPUT_DIR / "COMBINED_SOH.csv"

STORE_DIR = OUTPUT_DIR / "combined_soh"
CURRENT_DIR = STORE_DIR / "current"
CURRENT_FILE = CURRENT_DIR / "COMBINED_SOH.xlsx"
CURRENT_FILE_CSV = CURRENT_DIR / "COMBINED_SOH.csv"
HISTORY_DIR = STORE_DIR / "history"
META_FILE = STORE_DIR / "COMBINED_SOH.meta.json"
LEGACY_LAST_WEEK = BASE_DIR / "old_input_files" / "COMBINED_SOH.xlsx"

HISTORY_KEEP = 10  # ~1–1.5 weeks of daily backups
LAST_WEEK_WINDOW_DAYS = 2

_HISTORY_NAME_RE = re.compile(r"^COMBINED_SOH_(\d{4}-\d{2}-\d{2})\.xlsx$", re.I)


def _parse_iso_date(value: str) -> Optional[date]:
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


def history_stamp_for_run(run_day: Optional[date] = None) -> date:
    """Filename date = one calendar day before the pipeline run."""
    day = run_day or date.today()
    return day - timedelta(days=1)


# Back-compat name used by combine_soh
def as_of_for_run(run_day: Optional[date] = None) -> date:
    return history_stamp_for_run(run_day)


def history_path_for(as_of: date) -> Path:
    return HISTORY_DIR / f"COMBINED_SOH_{as_of.isoformat()}.xlsx"


def read_meta() -> Dict[str, Any]:
    if not META_FILE.exists():
        return {}
    try:
        return json.loads(META_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_meta(*, as_of: date, run_day: date, written_at: Optional[datetime] = None) -> None:
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "as_of": as_of.isoformat(),  # history stamp = run_day - 1
        "run_day": run_day.isoformat(),
        "written_at": (written_at or datetime.now()).isoformat(timespec="seconds"),
        "current_file": str(CURRENT_FILE.as_posix()),
        "note": "as_of = day before pipeline run (history filename date)",
    }
    META_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def list_history_snapshots() -> List[Tuple[date, Path]]:
    if not HISTORY_DIR.exists():
        return []
    out: List[Tuple[date, Path]] = []
    for p in HISTORY_DIR.glob("COMBINED_SOH_*.xlsx"):
        m = _HISTORY_NAME_RE.match(p.name)
        if not m:
            continue
        d = _parse_iso_date(m.group(1))
        if d is not None:
            out.append((d, p))
    out.sort(key=lambda x: x[0], reverse=True)
    return out


def prune_history(keep: int = HISTORY_KEEP) -> List[str]:
    snaps = list_history_snapshots()
    removed: List[str] = []
    for _, path in snaps[keep:]:
        try:
            path.unlink(missing_ok=True)
            removed.append(path.name)
        except OSError as e:
            log.warning("Could not prune %s: %s", path, e)
    if removed:
        log.info("Pruned old SOH history: %s", removed)
    return removed


def _mirror_compat(src: Path) -> None:
    """Keep output_files/COMBINED_SOH.xlsx in sync for existing pipeline code."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, COMPAT_CURRENT)


def publish_combined_soh(
    source_path: Path,
    *,
    run_day: Optional[date] = None,
    also_csv_df: Optional[pd.DataFrame] = None,
) -> Dict[str, Any]:
    """
    Publish a freshly built COMBINED_SOH:
      - current/COMBINED_SOH.xlsx (+ .csv)
      - history/COMBINED_SOH_<run-1>.xlsx (+ .csv)
      - compat mirror at output_files/COMBINED_SOH.xlsx (+ .csv)
    """
    if not source_path.exists():
        raise FileNotFoundError(f"COMBINED_SOH source missing: {source_path}")

    run = run_day or date.today()
    stamp = history_stamp_for_run(run)

    CURRENT_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)

    shutil.copy2(source_path, CURRENT_FILE)
    hist = history_path_for(stamp)
    shutil.copy2(source_path, hist)
    _mirror_compat(CURRENT_FILE)
    write_meta(as_of=stamp, run_day=run)
    pruned = prune_history()

    compat_csv = None
    current_csv = None
    history_csv = None
    if also_csv_df is not None:
        try:
            also_csv_df.to_csv(COMPAT_CURRENT_CSV, index=False)
            also_csv_df.to_csv(CURRENT_FILE_CSV, index=False)
            history_csv_path = hist.with_suffix(".csv")
            also_csv_df.to_csv(history_csv_path, index=False)
            compat_csv = str(COMPAT_CURRENT_CSV)
            current_csv = str(CURRENT_FILE_CSV)
            history_csv = str(history_csv_path)
        except Exception as e:
            log.warning("COMBINED_SOH CSV mirror failed: %s", e)

    log.info(
        "Published COMBINED_SOH current + history/%s (run_day=%s)",
        hist.name,
        run.isoformat(),
    )
    print(f"Current  : {CURRENT_FILE}")
    print(f"History  : {hist}  (stamp = run_day - 1 = {stamp.isoformat()})")
    if pruned:
        print(f"Pruned   : {len(pruned)} old history file(s)")

    return {
        "current": str(CURRENT_FILE),
        "compat": str(COMPAT_CURRENT),
        "history": str(hist),
        "compat_csv": compat_csv,
        "current_csv": current_csv,
        "history_csv": history_csv,
        "as_of": stamp.isoformat(),
        "run_day": run.isoformat(),
        "pruned": pruned,
    }


def seed_history_from_legacy(*, today: Optional[date] = None) -> Optional[Path]:
    if list_history_snapshots():
        return None
    if not LEGACY_LAST_WEEK.exists():
        return None
    day = today or date.today()
    stamp = day - timedelta(days=7)
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    dest = history_path_for(stamp)
    shutil.copy2(LEGACY_LAST_WEEK, dest)
    log.info("Seeded history from legacy → %s", dest.name)
    print(f"Seeded SOH history from old_input_files → {dest.name}")
    return dest


def resolve_last_week_soh_path(
    *,
    today: Optional[date] = None,
    window_days: int = LAST_WEEK_WINDOW_DAYS,
) -> Tuple[Optional[Path], Dict[str, Any]]:
    """
    Prefer today-7 history file (same weekday last week).
    If today=Monday → target=previous Monday; if you mainly care about
    Sunday stamps (run-1), nearest ±2 days still finds last Sunday.
    """
    day = today or date.today()
    seed_history_from_legacy(today=day)
    target = day - timedelta(days=7)
    info: Dict[str, Any] = {
        "target_date": target.isoformat(),
        "window_days": window_days,
        "mode": None,
        "matched_date": None,
        "path": None,
    }

    snaps = list_history_snapshots()
    by_date = {d: p for d, p in snaps}

    if target in by_date:
        path = by_date[target]
        info.update(mode="exact", matched_date=target.isoformat(), path=str(path))
        return path, info

    candidates: List[Tuple[int, int, date, Path]] = []
    for d, p in snaps:
        delta = abs((d - target).days)
        if delta <= window_days:
            prefer_past = 0 if d <= target else 1
            candidates.append((delta, prefer_past, d, p))
    if candidates:
        candidates.sort(key=lambda x: (x[0], x[1], -x[2].toordinal()))
        _, _, d, p = candidates[0]
        info.update(mode="nearest", matched_date=d.isoformat(), path=str(p))
        return p, info

    if LEGACY_LAST_WEEK.exists():
        info.update(mode="legacy_old_input_files", matched_date=None, path=str(LEGACY_LAST_WEEK))
        return LEGACY_LAST_WEEK, info

    info["mode"] = "missing"
    return None, info

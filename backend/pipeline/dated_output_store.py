"""
Generic current + dated-history publisher for pipeline outputs.

Stamp in history filename = (run_day - 1). Same day → overwrite.
Keeps the newest `keep` history files.
"""
from __future__ import annotations

import logging
import re
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

log = logging.getLogger("pipeline.dated_output_store")


def history_stamp(run_day: Optional[date] = None) -> date:
    return (run_day or date.today()) - timedelta(days=1)


def publish_with_history(
    source_path: Path,
    *,
    current_path: Path,
    history_dir: Path,
    history_basename: str,
    history_ext: str,
    keep: int = 5,
    run_day: Optional[date] = None,
    also_copy_to: Optional[Sequence[Path]] = None,
) -> Dict[str, Any]:
    """
    Copy source → current_path and history/<basename>_<run-1><ext>.
    also_copy_to: optional compat mirrors (e.g. legacy flat output path).
    """
    if not source_path.exists():
        raise FileNotFoundError(f"Source missing: {source_path}")

    run = run_day or date.today()
    stamp = history_stamp(run)
    current_path.parent.mkdir(parents=True, exist_ok=True)
    history_dir.mkdir(parents=True, exist_ok=True)

    shutil.copy2(source_path, current_path)
    hist_name = f"{history_basename}_{stamp.isoformat()}{history_ext}"
    hist_path = history_dir / hist_name
    shutil.copy2(source_path, hist_path)

    for dest in also_copy_to or []:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, dest)

    pruned = _prune(history_dir, history_basename, history_ext, keep=keep)
    log.info("Published %s + history/%s", current_path.name, hist_path.name)
    print(f"Current  : {current_path}")
    print(f"History  : {hist_path} (stamp=run-1={stamp.isoformat()})")
    if pruned:
        print(f"Pruned   : {len(pruned)} old history file(s)")

    return {
        "current": str(current_path),
        "history": str(hist_path),
        "as_of": stamp.isoformat(),
        "run_day": run.isoformat(),
        "pruned": pruned,
        "written_at": datetime.now().isoformat(timespec="seconds"),
    }


def _prune(history_dir: Path, basename: str, ext: str, *, keep: int) -> List[str]:
    pat = re.compile(
        rf"^{re.escape(basename)}_(\d{{4}}-\d{{2}}-\d{{2}}){re.escape(ext)}$",
        re.I,
    )
    items: List[tuple[date, Path]] = []
    for p in history_dir.glob(f"{basename}_*{ext}"):
        m = pat.match(p.name)
        if not m:
            continue
        try:
            d = date.fromisoformat(m.group(1))
        except ValueError:
            continue
        items.append((d, p))
    items.sort(key=lambda x: x[0], reverse=True)
    removed: List[str] = []
    for _, path in items[keep:]:
        try:
            path.unlink(missing_ok=True)
            removed.append(path.name)
        except OSError as e:
            log.warning("prune failed %s: %s", path, e)
    return removed

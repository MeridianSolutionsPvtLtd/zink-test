"""
Live progress for scheduled / manual daily pipeline runs (auto_daily_run).

Poll via GET /api/pipeline/daily-run/status
"""
from __future__ import annotations

import json
import os
import threading
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

PIPELINE_DIR = Path(__file__).resolve().parent
LOG_DIR = PIPELINE_DIR / "output_files" / "auto_runs"
LIVE_PROGRESS_FILE = LOG_DIR / "live_progress.json"
LOCK_FILE = LOG_DIR / "daily_run.lock"
LOCK_STALE_SEC = 4 * 3600  # 4h — treat stale lock as crashed run


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_lock() -> Optional[Dict[str, Any]]:
    if not LOCK_FILE.exists():
        return None
    try:
        return json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"pid": None, "started_at": None}


def _lock_is_stale(lock: Optional[Dict[str, Any]]) -> bool:
    if not lock:
        return True
    started = lock.get("started_at")
    if not started:
        return True
    try:
        ts = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - ts.astimezone(timezone.utc)).total_seconds()
        return age > LOCK_STALE_SEC
    except Exception:
        return True


def _write_lock(trigger: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    LOCK_FILE.write_text(
        json.dumps(
            {"pid": os.getpid(), "trigger": trigger, "started_at": _utc_now()},
            indent=2,
        ),
        encoding="utf-8",
    )


def _clear_lock() -> None:
    try:
        LOCK_FILE.unlink(missing_ok=True)
    except OSError:
        pass

STEP_DEFS: List[Dict[str, str]] = [
    {"id": "preview", "label": "Preview Raw_Files coverage"},
    {"id": "fetch", "label": "Fetch Raw_Files → incoming/"},
    {"id": "merge", "label": "Merge incoming → current/"},
    {"id": "local_sync", "label": "Sync current/ → local input_files"},
    {"id": "post_merge", "label": "Masters + SOH reconcile (skip historical unless forced)"},
    {"id": "sales_cache", "label": "Sales Overview ready cache"},
    {"id": "daily_movement", "label": "Daily inventory movement (SOH)"},
    {"id": "analyzer_cache", "label": "Sales Pivot prepared frames → Blob"},
    {"id": "page_cache", "label": "Home + Sales Dashboard page cache"},
    {"id": "pipeline", "label": "Combine inputs (sales + SOH + transfer)"},
]

# Overall progress slice per step (start_pct, end_pct)
_STEP_RANGE: Dict[str, tuple[int, int]] = {
    "preview": (0, 4),
    "fetch": (4, 12),
    "merge": (12, 40),
    "local_sync": (40, 48),
    "post_merge": (48, 58),
    "sales_cache": (58, 66),
    "daily_movement": (66, 74),
    "analyzer_cache": (74, 84),
    "page_cache": (84, 94),
    "pipeline": (94, 100),
}

_lock = threading.Lock()
_running = False
_trigger: str = "none"
_started_at: Optional[str] = None
_finished_at: Optional[str] = None
_progress_pct: int = 0
_current_phase: str = "idle"
_current_message: str = ""
_steps: List[Dict[str, Any]] = []
_result: Optional[Dict[str, Any]] = None
_artifact_path: Optional[str] = None


def _persist_locked() -> None:
    """Write current tracker snapshot for UI polling (survives brief reloads)."""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "running": _running,
            "trigger": _trigger,
            "started_at": _started_at,
            "finished_at": _finished_at,
            "progress_pct": _progress_pct,
            "current_phase": _current_phase,
            "current_message": _current_message,
            "steps": deepcopy(_steps),
            "result": deepcopy(_result),
            "artifact_path": _artifact_path,
            "updated_at": _utc_now(),
        }
        LIVE_PROGRESS_FILE.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    except Exception:
        pass


def _load_persisted() -> Optional[Dict[str, Any]]:
    if not LIVE_PROGRESS_FILE.exists():
        return None
    try:
        return json.loads(LIVE_PROGRESS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def _fresh_steps() -> List[Dict[str, Any]]:
    return [
        {
            "id": s["id"],
            "label": s["label"],
            "status": "pending",
            "detail": None,
            "started_at": None,
            "finished_at": None,
        }
        for s in STEP_DEFS
    ]


def _map_sub_pct(step_id: str, sub_pct: int) -> int:
    lo, hi = _STEP_RANGE.get(step_id, (0, 100))
    sub = max(0, min(100, int(sub_pct)))
    return lo + int((sub / 100) * max(1, hi - lo))


def begin_run(*, trigger: str = "manual") -> bool:
    global _running, _trigger, _started_at, _finished_at
    global _progress_pct, _current_phase, _current_message
    global _steps, _result, _artifact_path

    existing = _read_lock()
    if existing and not _lock_is_stale(existing):
        with _lock:
            if _running:
                return False
        # Another process holds the daily run lock
        return False

    with _lock:
        if _running:
            return False
        _running = True
        _trigger = trigger
        _started_at = _utc_now()
        _finished_at = None
        _progress_pct = 0
        _current_phase = "starting"
        _current_message = "Daily pipeline starting…"
        _steps = _fresh_steps()
        _result = None
        _artifact_path = None
        _persist_locked()
    _write_lock(trigger)
    return True


def start_step(step_id: str, message: Optional[str] = None) -> None:
    global _current_phase, _current_message, _progress_pct

    with _lock:
        lo, _ = _STEP_RANGE.get(step_id, (0, 0))
        _current_phase = step_id
        _current_message = message or next(
            (s["label"] for s in STEP_DEFS if s["id"] == step_id), step_id
        )
        _progress_pct = lo
        for step in _steps:
            if step["id"] == step_id:
                step["status"] = "running"
                step["started_at"] = _utc_now()
                if message:
                    step["detail"] = message
            elif step["status"] == "pending":
                pass
        _persist_locked()


def update_step(step_id: str, message: str, *, sub_pct: int = 0) -> None:
    global _current_phase, _current_message, _progress_pct

    with _lock:
        _current_phase = step_id
        _current_message = message
        _progress_pct = _map_sub_pct(step_id, sub_pct)
        for step in _steps:
            if step["id"] == step_id and step["status"] in ("pending", "running"):
                step["status"] = "running"
                step["detail"] = message
        _persist_locked()


def finish_step(step_id: str, *, ok: bool = True, detail: Optional[str] = None) -> None:
    global _progress_pct

    with _lock:
        _, hi = _STEP_RANGE.get(step_id, (0, 100))
        _progress_pct = max(_progress_pct, hi)
        for step in _steps:
            if step["id"] == step_id:
                step["status"] = "done" if ok else "failed"
                step["finished_at"] = _utc_now()
                if detail is not None:
                    step["detail"] = detail
        _persist_locked()


def skip_step(step_id: str, reason: str) -> None:
    with _lock:
        for step in _steps:
            if step["id"] == step_id:
                step["status"] = "skipped"
                step["detail"] = reason
                step["finished_at"] = _utc_now()
        _persist_locked()


def finish_run(result: Dict[str, Any], *, artifact_path: Optional[Path] = None) -> None:
    global _running, _finished_at, _result, _artifact_path, _progress_pct
    global _current_phase, _current_message

    with _lock:
        _running = False
        _finished_at = _utc_now()
        _result = deepcopy(result)
        _artifact_path = str(artifact_path) if artifact_path else None
        _progress_pct = 100 if result.get("ok") else _progress_pct
        _current_phase = "done" if result.get("ok") else "failed"
        _current_message = (
            "Daily pipeline completed successfully"
            if result.get("ok")
            else f"Daily pipeline failed: {result.get('error') or 'unknown error'}"
        )
        for step in _steps:
            if step["status"] == "running":
                step["status"] = "done" if result.get("ok") else "failed"
                step["finished_at"] = _utc_now()
        _persist_locked()
    _clear_lock()


def is_running() -> bool:
    with _lock:
        return _running


def get_status() -> Dict[str, Any]:
    persisted = _load_persisted()
    with _lock:
        latest_file = LOG_DIR / "latest.json"
        latest_from_disk: Optional[Dict[str, Any]] = None
        if latest_file.exists() and not _running:
            try:
                latest_from_disk = json.loads(latest_file.read_text(encoding="utf-8"))
            except Exception:
                latest_from_disk = None

        live = {
            "running": _running,
            "trigger": _trigger,
            "started_at": _started_at,
            "finished_at": _finished_at,
            "progress_pct": _progress_pct,
            "current_phase": _current_phase,
            "current_message": _current_message,
            "steps": deepcopy(_steps),
            "result": deepcopy(_result),
            "artifact_path": _artifact_path,
            "latest_artifact": str(latest_file) if latest_file.exists() else None,
            "last_run_from_disk": latest_from_disk,
        }

    # Prefer in-memory when running; else use persisted live file (e.g. after reload)
    if live.get("running") or not persisted:
        return live
    if persisted.get("running"):
        return {**live, **persisted}
    return live

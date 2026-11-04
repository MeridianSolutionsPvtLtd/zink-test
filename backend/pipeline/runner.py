"""
Orchestrate zinc replenishment + transfer pipeline steps.

Usage (from merged_backend/):
    python -m pipeline.runner --stage all
    python -m pipeline.runner --stage refill
    python -m pipeline.runner --stage transfer
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import os
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("pipeline.runner")

PIPELINE_DIR = Path(__file__).resolve().parent
MERGED_BACKEND_DIR = PIPELINE_DIR.parent
# Ensure pipeline scripts can import pipeline_inputs
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))


def _pipeline_module(name: str):
    """Import a sibling module whether runner runs as `pipeline.runner` or from pipeline/."""
    from importlib import import_module

    try:
        return import_module(f"pipeline.{name}")
    except ModuleNotFoundError as exc:
        if (exc.name or "").split(".")[0] != "pipeline":
            raise
        return import_module(name)


class PipelineCancelled(Exception):
    """Raised when a user requests stop of the running pipeline."""


@dataclass
class JobState:
    status: str = "idle"  # idle | running | success | failed | cancelled
    stage: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    current_step: str | None = None
    completed_steps: list[str] = field(default_factory=list)
    error: str | None = None
    log_tail: list[str] = field(default_factory=list)
    # Missing Raw_Files dates / stale snapshots — non-blocking
    warnings: list[str] = field(default_factory=list)
    worker_pid: int | None = None
    resume_from: str | None = None
    can_resume: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_JOB = JobState()
_JOB_LOCK = threading.Lock()
_WORKER: threading.Thread | None = None
_CANCEL = threading.Event()
_CURRENT_PROC: subprocess.Popen | None = None
_PROC_LOCK = threading.Lock()
_STATE_BLOB = "cache/pipeline-job-state.json"
_LOCAL_STATE_PATH = PIPELINE_DIR / "output_files" / "pipeline-job-state.json"
_last_persist_at = 0.0
_recovered_state = False
_auto_resume_scheduled = False


def _normalize_stage_name(stage: str | None) -> str:
    raw = str(stage or "all").strip() or "all"
    if "@from:" in raw:
        raw = raw.split("@from:", 1)[0].strip()
    return raw if raw in STAGE_MAP else "all"


def _script_from_step(step: str | None) -> str | None:
    """Extract resume script from 'combine_soh.py — Combine SOH' style labels."""
    text = str(step or "").strip()
    if not text:
        return None
    if text.startswith("sync_") or text.startswith("publish_"):
        return None
    head = text.split(" — ", 1)[0].strip() if " — " in text else text
    head = head.replace("\\", "/")
    name = Path(head).name
    if name.endswith(".py"):
        return name
    if head.endswith(".py"):
        return Path(head).name
    return None


def _pid_alive(pid: int | None) -> bool:
    if not pid or int(pid) <= 0:
        return False
    try:
        if sys.platform == "win32":
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
            )
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


def _load_saved_job() -> dict[str, Any] | None:
    """Prefer local state (fast); fall back to blob."""
    try:
        if _LOCAL_STATE_PATH.exists() and _LOCAL_STATE_PATH.stat().st_size > 0:
            saved = json.loads(_LOCAL_STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                return saved
    except Exception as exc:
        log.debug("local pipeline state read failed: %s", exc)
    try:
        from pipeline.publish_outputs_to_blob import _get_container

        cc, _container, _prefix = _get_container()
        raw = cc.download_blob(_STATE_BLOB).readall()
        saved = json.loads(raw.decode("utf-8"))
        if isinstance(saved, dict):
            return saved
    except Exception as exc:
        log.debug("blob pipeline state read failed: %s", exc)
    return None


def _persist_job(*, force: bool = False) -> None:
    """Remember the live job locally (+ blob) so reload/restart can resume."""
    global _last_persist_at
    now = time.time()
    if not force and (now - _last_persist_at) < 5:
        return
    _last_persist_at = now
    try:
        with _JOB_LOCK:
            payload = _JOB.to_dict()
        payload["log_tail"] = list(payload.get("log_tail") or [])[-40:]
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        raw = json.dumps(payload, default=str)
        try:
            _LOCAL_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            _LOCAL_STATE_PATH.write_text(raw, encoding="utf-8")
        except Exception as exc:
            log.debug("local pipeline job state save skipped: %s", exc)
        # Local laptop runs must NOT overwrite hosted UI state on blob.
        # Azure (or explicit PIPELINE_STATE_TO_BLOB=1) owns cache/pipeline-job-state.json.
        on_azure = bool(
            os.environ.get("WEBSITE_SITE_NAME") or os.environ.get("WEBSITE_INSTANCE_ID")
        )
        push_blob = on_azure or (
            (os.environ.get("PIPELINE_STATE_TO_BLOB") or "").strip().lower()
            in ("1", "true", "yes", "on")
        )
        if push_blob:
            try:
                from pipeline.publish_outputs_to_blob import _get_container
                from azure.storage.blob import ContentSettings

                cc, _container, _prefix = _get_container()
                cc.upload_blob(
                    name=_STATE_BLOB,
                    data=raw.encode("utf-8"),
                    overwrite=True,
                    content_settings=ContentSettings(content_type="application/json"),
                )
            except Exception as exc:
                log.debug("blob pipeline job state save skipped: %s", exc)
    except Exception as exc:
        log.debug("pipeline job state save skipped: %s", exc)


def _schedule_auto_resume(stage: str, resume_from: str | None) -> None:
    """After uvicorn --reload kills an in-process run, restart from the interrupted step."""
    global _auto_resume_scheduled
    if _auto_resume_scheduled:
        return
    flag = os.environ.get("PIPELINE_AUTO_RESUME", "1").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return
    if not resume_from:
        return
    _auto_resume_scheduled = True

    def _run() -> None:
        time.sleep(3.0)
        try:
            with _JOB_LOCK:
                if _JOB.status == "running":
                    return
            log.warning(
                "Auto-resuming pipeline stage=%s from=%s after server restart",
                stage,
                resume_from,
            )
            start_stage_async(
                stage,
                sync_blob_inputs=False,
                start_from=resume_from,
            )
        except Exception as exc:
            log.warning("Auto-resume failed: %s", exc)

    threading.Thread(target=_run, daemon=True, name="pipeline-auto-resume").start()


def _recover_interrupted_job() -> None:
    """If last job was running when the API process died, resume or mark failed."""
    global _JOB, _recovered_state
    if _recovered_state:
        return
    _recovered_state = True
    with _JOB_LOCK:
        if _JOB.status == "running":
            return
    saved = _load_saved_job()
    if not isinstance(saved, dict) or saved.get("status") != "running":
        # Still hydrate idle/success/failed from disk so UI sees last result
        if isinstance(saved, dict) and saved.get("status") in ("success", "failed", "cancelled"):
            with _JOB_LOCK:
                if _JOB.status == "idle":
                    _JOB = JobState(
                        status=str(saved.get("status") or "idle"),
                        stage=str(saved.get("stage") or ""),
                        started_at=saved.get("started_at"),
                        finished_at=saved.get("finished_at"),
                        current_step=saved.get("current_step"),
                        completed_steps=list(saved.get("completed_steps") or []),
                        error=saved.get("error"),
                        log_tail=list(saved.get("log_tail") or [])[-40:],
                        warnings=list(saved.get("warnings") or []),
                        worker_pid=saved.get("worker_pid"),
                        resume_from=saved.get("resume_from"),
                        can_resume=bool(saved.get("can_resume")),
                    )
        return

    step = str(saved.get("current_step") or "the last step")
    resume_from = saved.get("resume_from") or _script_from_step(step)
    stage = _normalize_stage_name(saved.get("stage"))
    worker_pid = saved.get("worker_pid")
    try:
        worker_pid = int(worker_pid) if worker_pid is not None else None
    except (TypeError, ValueError):
        worker_pid = None

    # Detached worker still alive → keep showing running and poll its state.
    if _pid_alive(worker_pid):
        with _JOB_LOCK:
            if _JOB.status == "running":
                return
            _JOB = JobState(
                status="running",
                stage=str(saved.get("stage") or stage),
                started_at=saved.get("started_at"),
                finished_at=None,
                current_step=saved.get("current_step"),
                completed_steps=list(saved.get("completed_steps") or []),
                error=None,
                log_tail=list(saved.get("log_tail") or [])[-40:],
                warnings=list(saved.get("warnings") or []),
                worker_pid=worker_pid,
                resume_from=resume_from,
                can_resume=False,
            )
        log.info("Pipeline worker pid=%s still alive after API reload — keeping status=running", worker_pid)
        return

    with _JOB_LOCK:
        if _JOB.status == "running":
            return
        _JOB = JobState(
            status="failed",
            stage=stage,
            started_at=saved.get("started_at"),
            finished_at=datetime.now(timezone.utc).isoformat(),
            current_step=saved.get("current_step"),
            completed_steps=list(saved.get("completed_steps") or []),
            error=(
                "Server restarted while the pipeline was still running "
                f"(stopped around: {step}). "
                + (
                    f"Resuming automatically from {resume_from}…"
                    if resume_from
                    else "Click Resume or run the pipeline again."
                )
            ),
            log_tail=list(saved.get("log_tail") or [])[-40:],
            warnings=list(saved.get("warnings") or []),
            worker_pid=None,
            resume_from=resume_from,
            can_resume=bool(resume_from),
        )
    _persist_job(force=True)
    _schedule_auto_resume(stage, resume_from)


def _ensure_not_cancelled() -> None:
    if _CANCEL.is_set():
        raise PipelineCancelled("Pipeline stopped by user")


def _terminate_process(proc: subprocess.Popen) -> None:
    """Best-effort kill of the current script process (and Windows process tree)."""
    if proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            # Kill the whole tree so child python workers die too
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                text=True,
                timeout=15,
            )
        else:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
    except Exception as exc:
        log.warning("Failed to terminate pipeline process pid=%s: %s", proc.pid, exc)
        try:
            proc.kill()
        except Exception:
            pass


def request_stop(reason: str | None = None) -> dict[str, Any]:
    """
    Signal the running pipeline to stop and kill the active subprocess if any.
    Safe to call when idle — returns ok=False.
    """
    msg = (reason or "").strip() or "Stopped by user"
    with _JOB_LOCK:
        if _JOB.status != "running":
            return {
                "ok": False,
                "message": "No pipeline is currently running",
                "job": get_job_state(),
            }
        worker_pid = _JOB.worker_pid

    _CANCEL.set()
    _append_log(f"── STOP requested: {msg} ──")

    with _PROC_LOCK:
        proc = _CURRENT_PROC
    if proc is not None:
        _terminate_process(proc)

    # Detached worker (survives API reload) — kill whole process tree.
    if worker_pid and worker_pid != os.getpid() and _pid_alive(worker_pid):
        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(worker_pid)],
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
            else:
                os.kill(int(worker_pid), 15)
        except Exception as exc:
            log.warning("Failed to stop detached pipeline pid=%s: %s", worker_pid, exc)

    with _JOB_LOCK:
        if _JOB.status == "running":
            _JOB.status = "cancelled"
            _JOB.error = msg
            _JOB.finished_at = datetime.now(timezone.utc).isoformat()
            _JOB.can_resume = bool(_JOB.resume_from)
    _persist_job(force=True)

    return {
        "ok": True,
        "message": "Stop requested — pipeline will halt shortly",
        "job": get_job_state(),
    }


def _refresh_external_worker_state() -> None:
    """When pipeline runs detached, keep API in-memory job synced from local state file."""
    global _JOB
    with _JOB_LOCK:
        in_thread = _WORKER is not None and _WORKER.is_alive()
        status = _JOB.status
        pid = _JOB.worker_pid
    if in_thread:
        return
    saved = _load_saved_job()
    if not isinstance(saved, dict):
        return
    saved_status = str(saved.get("status") or "")
    if status == "running" or saved_status == "running":
        try:
            saved_pid = int(saved.get("worker_pid")) if saved.get("worker_pid") is not None else None
        except (TypeError, ValueError):
            saved_pid = None
        # If we thought it was running but worker died without final status, mark failed.
        if saved_status == "running" and saved_pid and not _pid_alive(saved_pid) and saved_pid != os.getpid():
            resume_from = saved.get("resume_from") or _script_from_step(saved.get("current_step"))
            with _JOB_LOCK:
                _JOB = JobState(
                    status="failed",
                    stage=_normalize_stage_name(saved.get("stage")),
                    started_at=saved.get("started_at"),
                    finished_at=datetime.now(timezone.utc).isoformat(),
                    current_step=saved.get("current_step"),
                    completed_steps=list(saved.get("completed_steps") or []),
                    error=(
                        "Pipeline worker process ended unexpectedly "
                        f"(last step: {saved.get('current_step') or 'unknown'}). "
                        + (f"Resume from {resume_from}." if resume_from else "Run the pipeline again.")
                    ),
                    log_tail=list(saved.get("log_tail") or [])[-40:],
                    warnings=list(saved.get("warnings") or []),
                    resume_from=resume_from,
                    can_resume=bool(resume_from),
                )
            _persist_job(force=True)
            return
        with _JOB_LOCK:
            _JOB = JobState(
                status=saved_status or _JOB.status,
                stage=str(saved.get("stage") or _JOB.stage),
                started_at=saved.get("started_at"),
                finished_at=saved.get("finished_at"),
                current_step=saved.get("current_step"),
                completed_steps=list(saved.get("completed_steps") or []),
                error=saved.get("error"),
                log_tail=list(saved.get("log_tail") or [])[-40:],
                warnings=list(saved.get("warnings") or []),
                worker_pid=saved_pid,
                resume_from=saved.get("resume_from"),
                can_resume=bool(saved.get("can_resume")),
            )


def get_job_state() -> dict[str, Any]:
    _recover_interrupted_job()
    _refresh_external_worker_state()
    with _JOB_LOCK:
        data = _JOB.to_dict()

    stage = data.get("stage") or "all"
    planned = STAGE_MAP.get(stage) or STAGE_MAP["all"]
    planned_steps = [{"script": s, "label": lab} for s, lab in planned]
    completed = list(data.get("completed_steps") or [])
    current = str(data.get("current_step") or "")
    current_script = ""
    if current.startswith("sync_current"):
        current_script = "sync_current"
    elif current.startswith("sync_soh"):
        current_script = "sync_soh_history"
    elif current.startswith("publish_replenishment"):
        current_script = "publish_replenishment"
    elif current.startswith("publish_outputs"):
        current_script = "publish_outputs"
    elif " — " in current:
        current_script = current.split(" — ", 1)[0].strip()
    else:
        current_script = current.strip()

    # Replenishment final file → blob right after SKU/SIZE enrich, before transfer
    planned_with_mid: list[dict[str, Any]] = []
    for st in planned_steps:
        planned_with_mid.append(st)
        if st["script"] == "7_enrich_sku_size.py":
            planned_with_mid.append(
                {
                    "script": "publish_replenishment",
                    "label": "Publish replenishment → Blob",
                }
            )

    ui_steps: list[dict[str, Any]] = [
        {"script": "sync_current", "label": "Sync Blob current/ → local"},
        {"script": "sync_soh_history", "label": "Sync SOH history from Blob"},
    ] + planned_with_mid + [
        {"script": "publish_outputs", "label": "Publish outputs → Blob"},
    ]

    past_sync = bool(completed) or (
        current_script
        and current_script not in ("sync_current", "sync_soh_history")
        and data.get("status") == "running"
    ) or data.get("status") == "success"

    enriched: list[dict[str, Any]] = []
    for st in ui_steps:
        script = st["script"]
        if script == "sync_current":
            if current_script == "sync_current":
                state = "running"
            elif past_sync or current_script == "sync_soh_history":
                state = "done"
            elif data.get("status") == "running":
                state = "running"
            else:
                state = "waiting"
        elif script == "sync_soh_history":
            if current_script == "sync_soh_history":
                state = "running"
            elif past_sync or (completed and current_script != "sync_current"):
                state = "done"
            elif current_script == "sync_current":
                state = "waiting"
            elif data.get("status") == "running" and not completed:
                state = "waiting"
            else:
                state = "waiting"
        elif script == "publish_replenishment":
            if current_script == "publish_replenishment":
                state = "running"
            elif "publish_replenishment" in completed or data.get("status") == "success":
                state = "done"
            elif any(
                c.startswith("store_soh") or c.startswith("soh_null") or c.startswith("transfer/")
                for c in completed
            ):
                state = "done"
            else:
                state = "waiting"
        elif script == "publish_outputs":
            if current_script == "publish_outputs":
                state = "running"
            elif data.get("status") == "success":
                state = "done"
            else:
                state = "waiting"
        elif script in completed:
            state = "done"
        elif script == current_script or (current_script and current.startswith(script)):
            state = "running"
        else:
            state = "waiting"
        enriched.append({**st, "state": state})

    done_n = sum(1 for s in enriched if s["state"] == "done")
    total_n = max(len(enriched), 1)
    running_idx = next((i for i, s in enumerate(enriched) if s["state"] == "running"), None)
    progress = int(round(100 * done_n / total_n))
    if data.get("status") == "running" and running_idx is not None:
        progress = min(99, int(round(100 * (done_n + 0.4) / total_n)))
    if data.get("status") == "success":
        progress = 100

    phase = "idle"
    blob = (
        current
        + " "
        + (enriched[running_idx]["script"] if running_idx is not None else "")
    ).lower()
    if data.get("status") == "success":
        phase = "done"
    elif data.get("status") == "failed":
        phase = "failed"
    elif data.get("status") == "cancelled":
        phase = "cancelled"
    elif "sync" in blob:
        phase = "sync"
    elif "combine" in blob:
        phase = "combine"
    elif "predict" in blob or "5.predict" in blob:
        phase = "ml"
    elif "publish_replenishment" in blob:
        phase = "refill"
    elif "refill" in blob or "6.build" in blob or "enrich" in blob:
        phase = "refill"
    elif "transfer/" in blob or "store_soh" in blob or "soh_null" in blob:
        phase = "transfer"
    elif "publish" in blob:
        phase = "done" if data.get("status") == "success" else "refill"
    elif any(x in blob for x in ("sales_report", "weekly", "add_soh", "combine_soh", "first_transfer")):
        phase = "analyze"
    elif data.get("status") == "running":
        phase = "analyze"

    data["planned_steps"] = enriched
    data["progress_pct"] = progress
    data["phase"] = phase
    data["current_label"] = (
        enriched[running_idx]["label"]
        if running_idx is not None
        else (data.get("current_step") or "")
    )
    return data


def _append_log(msg: str) -> None:
    with _JOB_LOCK:
        _JOB.log_tail.append(msg)
        if len(_JOB.log_tail) > 400:
            _JOB.log_tail = _JOB.log_tail[-400:]
    _persist_job(force=_JOB.status != "running")


def _append_warning(msg: str) -> None:
    msg = (msg or "").strip()
    if not msg:
        return
    with _JOB_LOCK:
        if msg not in _JOB.warnings:
            _JOB.warnings.append(msg)
    _append_log(msg)


def _log_data_gap_warnings() -> list[str]:
    """Scan Raw_Files coverage and surface missing-date warnings (non-blocking)."""
    try:
        collect = _pipeline_module("ginesys_raw").collect_data_gap_warnings
        warns = list(collect() or [])
    except Exception as exc:
        msg = f"WARNING: could not check missing Raw_Files dates: {exc}"
        _append_warning(msg)
        return [msg]
    if not warns:
        _append_log("── Data coverage: no missing daily Raw_Files dates detected ──")
        return []
    _append_log(f"── Data coverage: {len(warns)} warning(s) (pipeline continues) ──")
    for w in warns:
        _append_warning(w)
    return warns


STEPS_COMBINE = [
    ("apply_sku_remap.py", "Apply SKU remap → sales masters + Blob"),
    # combine_sales skipped — pipeline reads Sales (PT, SSL, Centro, Iconic, EBO) directly
    ("combine_soh.py", "Combine SOH"),
    # combine_transfer skipped — pipeline reads Transfer - (PT, SSL, Centro, Iconic) directly
]

STEPS_REFILL = [
    ("1.sales_report.py", "Sales report"),
    ("2.weekly_aggregate.py", "Weekly aggregate"),
    ("3.add_soh_weekly.py", "Add SOH weekly"),
    ("4.add_first_transfer.py", "Add first transfer"),
    ("5.predict_weekly.py", "Predict weekly"),
    ("6.build_refill.py", "Build refill"),
    ("7_enrich_sku_size.py", "Enrich SKU/SIZE from item master"),
    ("store_soh_new.py", "Store SOH new"),
    ("soh_null.py", "SOH null / no-sales donors"),
]

STEPS_TRANSFER = [
    ("transfer/1_build_potential_donors_receivers.py", "Potential donors/receivers"),
    ("transfer/2_recommend_transfers.py", "Recommend transfers"),
    ("transfer/3_broken_size_transfer.py", "Broken size transfer"),
    ("transfer/4_combine_final_transfer.py", "Combine final transfer"),
]

STAGE_MAP = {
    "combine": STEPS_COMBINE,
    "refill": STEPS_REFILL,
    "transfer": STEPS_TRANSFER,
    "all": STEPS_COMBINE + STEPS_REFILL + STEPS_TRANSFER,
}


# Windows STATUS_CONTROL_C_EXIT — process killed by console/job signal (not a script bug).
_WIN_STATUS_CONTROL_C_EXIT = 3221225786  # 0xC000013A


def _windows_popen_flags(*, breakaway: bool = True) -> int:
    """Flags so step scripts survive parent reload / console Ctrl events."""
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    CREATE_NO_WINDOW = 0x08000000
    CREATE_BREAKAWAY_FROM_JOB = 0x01000000
    flags = CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    if breakaway:
        flags |= CREATE_BREAKAWAY_FROM_JOB
    return flags


def _is_transient_kill(returncode: int | None) -> bool:
    if returncode is None:
        return False
    # Signed/unsigned forms of STATUS_CONTROL_C_EXIT
    if int(returncode) in (_WIN_STATUS_CONTROL_C_EXIT, -1073741510):
        return True
    return False


def _run_script(rel_path: str, *, _attempt: int = 1) -> None:
    global _CURRENT_PROC
    _ensure_not_cancelled()

    script = PIPELINE_DIR / rel_path
    if not script.exists():
        raise FileNotFoundError(f"Script not found: {script}")

    # transfer scripts expect cwd = transfer/ for relative imports sometimes;
    # we always set PYTHONPATH=PIPELINE_DIR and cwd=PIPELINE_DIR (or transfer/)
    if rel_path.startswith("transfer/"):
        cwd = PIPELINE_DIR / "transfer"
        cmd = [sys.executable, "-u", script.name]
    else:
        cwd = PIPELINE_DIR
        cmd = [sys.executable, "-u", script.name]

    env = dict(**{k: v for k, v in os.environ.items()})
    env["PYTHONPATH"] = str(PIPELINE_DIR) + (
        (os.pathsep + env["PYTHONPATH"]) if env.get("PYTHONPATH") else ""
    )
    # Avoid Windows cp1252 UnicodeEncodeError on arrows / special chars in prints
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")

    _append_log(f"$ {' '.join(cmd)}  (cwd={cwd})" + (f"  [retry {_attempt}]" if _attempt > 1 else ""))
    popen_kwargs: dict[str, Any] = {
        "cwd": str(cwd),
        "env": env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if sys.platform == "win32":
        # Own process group + no console → avoids exit 3221225786 (0xC000013A) when
        # API --reload / job object sends Ctrl events to the parent group.
        popen_kwargs["creationflags"] = _windows_popen_flags(breakaway=True)

    proc = subprocess.Popen(cmd, **popen_kwargs)
    with _PROC_LOCK:
        _CURRENT_PROC = proc

    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []

    def _drain(stream: Any, chunks: list[str]) -> None:
        try:
            for line in iter(stream.readline, ""):
                chunks.append(line)
        except Exception:
            pass

    t_out = threading.Thread(target=_drain, args=(proc.stdout, stdout_chunks), daemon=True)
    t_err = threading.Thread(target=_drain, args=(proc.stderr, stderr_chunks), daemon=True)
    t_out.start()
    t_err.start()

    try:
        while proc.poll() is None:
            if _CANCEL.is_set():
                _terminate_process(proc)
                raise PipelineCancelled("Pipeline stopped by user")
            time.sleep(0.25)

        t_out.join(timeout=2)
        t_err.join(timeout=2)
        stdout = "".join(stdout_chunks)
        stderr = "".join(stderr_chunks)
        if stdout:
            for line in stdout.strip().splitlines()[-40:]:
                _append_log(line)
        if proc.returncode != 0:
            if _CANCEL.is_set():
                raise PipelineCancelled("Pipeline stopped by user")
            err = (stderr or stdout or "").strip()[-2000:]
            _append_log(f"[ERROR] exit={proc.returncode}")
            # Transient Windows kill while reading large SOH files — retry once.
            if _is_transient_kill(proc.returncode) and _attempt < 2:
                _append_log(
                    f"WARNING: {rel_path} was killed by Windows signal "
                    f"(exit {proc.returncode} / 0xC000013A). Retrying once…"
                )
                time.sleep(2.0)
                return _run_script(rel_path, _attempt=_attempt + 1)
            if _is_transient_kill(proc.returncode):
                raise RuntimeError(
                    f"{rel_path} was terminated by Windows (exit {proc.returncode} = "
                    "STATUS_CONTROL_C_EXIT). Usually API --reload or a Stop clicked mid-step — "
                    f"not bad SOH data. Click Resume from {Path(rel_path).name}.\n{err}"
                )
            raise RuntimeError(f"{rel_path} failed (exit {proc.returncode}):\n{err}")
    finally:
        with _PROC_LOCK:
            if _CURRENT_PROC is proc:
                _CURRENT_PROC = None
        try:
            if proc.stdout:
                proc.stdout.close()
            if proc.stderr:
                proc.stderr.close()
        except Exception:
            pass


def _filter_steps_from(
    steps: list[tuple[str, str]],
    start_from: str | None,
) -> list[tuple[str, str]]:
    """Resume from a script name/substring (e.g. 7_enrich_sku_size.py)."""
    if not start_from:
        return steps
    needle = start_from.strip().lower().replace("\\", "/")
    for i, (rel, _) in enumerate(steps):
        rel_l = rel.lower()
        if rel_l == needle or rel_l.endswith("/" + needle) or Path(rel_l).name == needle:
            return steps[i:]
        if needle in rel_l:
            return steps[i:]
    raise ValueError(
        f"start_from={start_from!r} not found in stage steps: {[s for s, _ in steps]}"
    )


def _output_file_ready(*names: str) -> bool:
    out = PIPELINE_DIR / "output_files"
    for name in names:
        p = out / name
        if p.exists() and p.stat().st_size > 0:
            return True
    return False


def _sales_report_local_ready() -> bool:
    return _output_file_ready("SALES_REPORT.csv", "SALES_REPORT.xlsx")


def _weekly_local_ready() -> bool:
    return _output_file_ready(
        "weekly_aggregated_sales_new.csv",
        "weekly_aggregated_sales_new.xlsx",
    )


def _combined_soh_local_ready() -> bool:
    out = PIPELINE_DIR / "output_files"
    candidates = (
        out / "COMBINED_SOH.csv",
        out / "COMBINED_SOH.xlsx",
        out / "combined_soh" / "current" / "COMBINED_SOH.csv",
        out / "combined_soh" / "current" / "COMBINED_SOH.xlsx",
    )
    return any(p.exists() and p.stat().st_size > 0 for p in candidates)


def _sync_intermediates_for_resume() -> None:
    try:
        sync_fn = _pipeline_module("publish_outputs_to_blob").sync_pipeline_intermediates_from_blob
        synced = sync_fn(only_missing=True)
        log.info(
            "Resume pre-sync intermediates: downloaded=%s",
            (synced or {}).get("downloaded"),
        )
    except Exception as exc:
        log.warning("Resume intermediate sync failed: %s", exc)


def _rewind_to(stage: str, script_name: str, start_from: str, reason: str) -> str:
    steps = STAGE_MAP.get(stage) or []
    for rel, _ in steps:
        if Path(rel.replace("\\", "/")).name.lower() == script_name.lower():
            log.warning(
                "Resume from %s but %s — rewinding to %s",
                start_from,
                reason,
                script_name,
            )
            return script_name
    return start_from


def _resolve_start_from_for_resume(
    stage: str,
    start_from: str | None,
) -> str | None:
    """
    On App Service, mid-step resume often lands on empty local disk.
    Pull intermediates from blob; rewind when required upstream outputs are missing.
    """
    if not start_from:
        return start_from
    name = Path(str(start_from).replace("\\", "/")).name.lower()
    if name in ("1.sales_report.py", "apply_sku_remap.py", "combine_soh.py"):
        return start_from

    needs_weekly = name not in (
        "1.sales_report.py",
        "2.weekly_aggregate.py",
        "apply_sku_remap.py",
        "combine_soh.py",
    )
    needs_combined = name in (
        "3.add_soh_weekly.py",
        "4.add_first_transfer.py",
        "5.predict_weekly.py",
        "6.build_refill.py",
        "7_enrich_sku_size.py",
    )

    if (not _sales_report_local_ready()) or (
        needs_weekly and not _weekly_local_ready()
    ) or (needs_combined and not _combined_soh_local_ready()):
        _sync_intermediates_for_resume()

    if not _sales_report_local_ready() and name not in (
        "2.weekly_aggregate.py",
    ):
        return _rewind_to(
            stage,
            "1.sales_report.py",
            start_from,
            "SALES_REPORT missing locally/blob",
        )

    if needs_weekly and not _weekly_local_ready():
        return _rewind_to(
            stage,
            "2.weekly_aggregate.py",
            start_from,
            "weekly_aggregated_sales_new missing",
        )

    if needs_combined and not _combined_soh_local_ready():
        return _rewind_to(
            stage,
            "combine_soh.py",
            start_from,
            "COMBINED_SOH missing",
        )

    return start_from


def run_stage(
    stage: str,
    *,
    sync_blob_inputs: bool | None = None,
    start_from: str | None = None,
    warehouse_mode: str | None = None,
    sku_scope: str | None = None,
) -> None:
    global _JOB
    steps = STAGE_MAP.get(stage)
    if not steps:
        raise ValueError(f"Unknown stage: {stage}. Choose from {list(STAGE_MAP)}")
    start_from = _resolve_start_from_for_resume(stage, start_from)
    steps = _filter_steps_from(list(steps), start_from)

    # Do not force-insert combine_sales — sales come from Sales (PT, SSL, Centro, Iconic, EBO)

    # Apply warehouse mode for this run (affects warehouse_ledger behavior).
    prev_wh_reset = os.environ.get("WH_LEDGER_RESET")
    prev_sku_scope = os.environ.get("PIPELINE_SKU_SCOPE")
    if sku_scope:
        sc = str(sku_scope).strip().lower()
        if sc not in ("all", "remapped", "remap", "renamed"):
            raise ValueError("sku_scope must be all | remapped")
        if sc in ("remapped", "remap", "renamed"):
            os.environ["PIPELINE_SKU_SCOPE"] = "remapped"
        else:
            os.environ.pop("PIPELINE_SKU_SCOPE", None)
    if warehouse_mode:
        wm = str(warehouse_mode).strip().lower()
        if wm == "fresh":
            os.environ["WH_LEDGER_RESET"] = "1"
        elif wm == "incremental":
            os.environ.pop("WH_LEDGER_RESET", None)
        else:
            raise ValueError("warehouse_mode must be incremental | fresh")

    # Default: ALWAYS pull Azure Blob input/ → local input_files on every single pipeline run
    if sync_blob_inputs is None:
        flag = (os.environ.get("PIPELINE_SYNC_BLOB_INPUTS", "1") or "1").strip().lower()
        sync_blob_inputs = flag not in ("0", "false", "no", "off")

    with _JOB_LOCK:
        if _JOB.status == "running" and _pid_alive(_JOB.worker_pid) and _JOB.worker_pid != os.getpid():
            raise RuntimeError("Pipeline job already running")
        _CANCEL.clear()
        _JOB = JobState(
            status="running",
            stage=stage if not start_from else f"{stage}@from:{start_from}",
            started_at=datetime.now(timezone.utc).isoformat(),
            worker_pid=os.getpid(),
            resume_from=start_from,
            can_resume=False,
        )
    _persist_job(force=True)

    try:
        # Load store selection FIRST so sync/filter use the same set
        try:
            from pipeline_inputs import load_replenishment_store_selection

            sel = load_replenishment_store_selection()
            types = sel.get("store_types") or []
            if types:
                os.environ["PIPELINE_STORE_TYPES"] = ",".join(types)
                os.environ["PIPELINE_STORE_IDS"] = ",".join(sel.get("stores") or [])
                _append_log(
                    f"── Replenishment stores: {', '.join(sel.get('labels') or types)} "
                    f"(types={','.join(types)}) ──"
                )
            else:
                _append_log(
                    "── Replenishment stores: none selected "
                    f"(expected file: {sel.get('path')}) ──"
                )
            if (os.environ.get("PIPELINE_SKU_SCOPE") or "").strip().lower() == "remapped":
                _append_log(
                    "── SKU scope: remapped SKUs only (current SKU_REMAP.xlsx, not all SKUs) ──"
                )
        except Exception as sel_exc:
            _append_log(f"WARNING: could not load replenishment_stores.json: {sel_exc}")

        # Missing daily dates / stale Raw_Files — warn but do not stop
        _log_data_gap_warnings()

        if sync_blob_inputs:
            _ensure_not_cancelled()
            # Ingest any pending raw daily dumps from Raw_Files/ to incoming/ and merge to current/
            try:
                fetch_raw_to_incoming = _pipeline_module("ginesys_raw").fetch_raw_to_incoming
                merge_all_categories = _pipeline_module("merge_category_inputs").merge_all_categories
                with _JOB_LOCK:
                    _JOB.current_step = "sync_current — ingest Raw_Files → incoming"
                raw_copied = fetch_raw_to_incoming(dry_run=False)
                touched = list(raw_copied.get("touched_categories") or [])
                if touched:
                    _append_log(f"── Auto-ingested Raw_Files for: {', '.join(touched)} ──")
                    with _JOB_LOCK:
                        _JOB.current_step = (
                            f"sync_current — merge live current/ ({len(touched)} categories)"
                        )

                    def _merge_progress(pct: int, msg: str) -> None:
                        _append_log(f"  merge [{pct}%] {msg}")
                        with _JOB_LOCK:
                            _JOB.current_step = f"sync_current — {msg}"

                    merge_res = merge_all_categories(
                        dry_run=False,
                        only_ids=touched,
                        on_progress=_merge_progress,
                    )
                    failed = merge_res.get("failed") or []
                    if failed:
                        for fr in failed[:8]:
                            _append_log(
                                f"  MERGE FAIL [{fr.get('category_id') or fr.get('folder')}]: "
                                f"{fr.get('error') or fr}"
                            )
                    _append_log(
                        f"── Live merge done: merged={merge_res.get('merged_count', 0)} "
                        f"skipped={merge_res.get('skipped_count', 0)} "
                        f"failed={merge_res.get('failed_count', 0)} ──"
                    )
                else:
                    _append_log("── No new Raw_Files to ingest (using existing Blob current/) ──")
            except Exception as raw_exc:
                log.info("Raw_Files auto-merge check: %s", raw_exc)
                _append_log(f"WARNING: Raw_Files auto-merge: {raw_exc}")

            with _JOB_LOCK:
                _JOB.current_step = "sync_current — Azure Blob category current/ only"
            _append_log(
                "── Sync Blob current/ only (skip history/incoming) → local input_files/ ──"
            )
            try:
                sync_current_to_local = _pipeline_module(
                    "merge_category_inputs"
                ).sync_current_to_local

                # Sales-Master is the dashboard source. The 9 AM daily run syncs it.
                # Replenishment reads Sales (...)/current/SSI_PT_CENTRO_ICONIC_SALES.*.
                cur_sync = sync_current_to_local(
                    dry_run=False,
                    skip_ids=["sales-master-pt-ssl-centro-iconic"],
                )
                n = len(cur_sync.get("downloaded") or [])
                n_skip = len(cur_sync.get("skipped") or [])
                skipped_cats = cur_sync.get("skipped_categories") or []
                if skipped_cats:
                    _append_log(
                        "Skipped Sales-Master local sync "
                        f"({', '.join(skipped_cats)}) — daily run covers the dashboard"
                    )
                _append_log(f"Current sync: {n} file(s) downloaded, {n_skip} skipped (unchanged)")
                for err in (cur_sync.get("errors") or [])[:10]:
                    _append_log(f"  sync error: {err}")
                if cur_sync.get("errors") and n == 0 and n_skip == 0:
                    raise RuntimeError(f"Blob current/ sync failed: {cur_sync.get('errors')}")
                if n == 0 and n_skip == 0:
                    _append_log(
                        "WARNING: No files under blob input/<Category>/current/. "
                        "Upload + merge first, or check Azure current/ folders."
                    )
                _append_log("✓ Blob current/ sync")
            except PipelineCancelled:
                raise
            except Exception as cur_exc:
                raise RuntimeError(f"Blob current/ sync failed: {cur_exc}") from cur_exc

            _ensure_not_cancelled()
            # Pull COMBINED_SOH history from blob so last_week_store_soh uses blob rules
            try:
                with _JOB_LOCK:
                    _JOB.current_step = "sync_soh_history — Blob output/combined_soh"
                sync_combined_soh_from_blob = _pipeline_module(
                    "publish_outputs_to_blob"
                ).sync_combined_soh_from_blob

                soh_sync = sync_combined_soh_from_blob()
                _append_log(
                    f"SOH history sync: {len(soh_sync.get('downloaded') or [])} file(s) "
                    f"from blob://{soh_sync.get('container')}/{soh_sync.get('prefix')}"
                )
                for err in (soh_sync.get("errors") or [])[:5]:
                    _append_log(f"  soh sync error: {err}")
                _append_log("✓ Blob combined_soh sync")
            except PipelineCancelled:
                raise
            except Exception as soh_exc:
                _append_log(
                    f"WARNING: combined_soh blob sync skipped: {soh_exc} "
                    "(last_week may fall back to local/legacy)"
                )

            _ensure_not_cancelled()
            # Per-store SOH history (input/<Store SOH>/history/*_YYYY-MM-DD.xlsx)
            try:
                with _JOB_LOCK:
                    _JOB.current_step = "sync_store_soh_history — Blob input/*/history dated"
                sync_store_soh_history_to_local = _pipeline_module(
                    "store_soh_history"
                ).sync_store_soh_history_to_local
                from pipeline_inputs import get_selected_store_types

                # Prefer selected types; if none yet, sync all 5 channels
                types = get_selected_store_types() or None
                store_hist = sync_store_soh_history_to_local(store_types=types)
                _append_log(
                    f"Store SOH history sync: {len(store_hist.get('downloaded') or [])} file(s)"
                )
                for err in (store_hist.get("errors") or [])[:5]:
                    _append_log(f"  store soh hist error: {err}")
                _append_log("✓ Blob per-store SOH history sync")
            except PipelineCancelled:
                raise
            except Exception as sh_exc:
                _append_log(
                    f"WARNING: per-store SOH history sync skipped: {sh_exc}"
                )

        # Mid-resume / Azure recycle: restore SALES_REPORT + weekly flats from blob
        if start_from:
            try:
                with _JOB_LOCK:
                    _JOB.current_step = "sync_intermediates — Blob output/ → local"
                _append_log(
                    "── Sync mid-pipeline outputs from Blob (resume / ephemeral disk) ──"
                )
                sync_intermediates = _pipeline_module(
                    "publish_outputs_to_blob"
                ).sync_pipeline_intermediates_from_blob
                mid_sync = sync_intermediates(only_missing=True)
                n_dl = len(mid_sync.get("downloaded") or [])
                n_sk = len(mid_sync.get("skipped") or [])
                _append_log(
                    f"Intermediate sync: {n_dl} downloaded, {n_sk} skipped (already local)"
                )
                for name in (mid_sync.get("downloaded") or [])[:15]:
                    _append_log(f"  ↓ {name}")
                for err in (mid_sync.get("errors") or [])[:8]:
                    _append_log(f"  intermediate sync error: {err}")
                _append_log("✓ Blob intermediate output sync")
            except Exception as mid_exc:
                _append_log(
                    f"WARNING: intermediate output sync skipped: {mid_exc}"
                )

        for rel, label in steps:
            _ensure_not_cancelled()
            with _JOB_LOCK:
                _JOB.current_step = f"{rel} — {label}"
                _JOB.resume_from = Path(rel.replace("\\", "/")).name
                _JOB.worker_pid = os.getpid()
            _persist_job(force=True)
            _append_log(f"── {label} ({rel}) ──")
            _run_script(rel)
            with _JOB_LOCK:
                _JOB.completed_steps.append(rel)
            _append_log(f"✓ {label}")
            _persist_job(force=True)

            # Checkpoint key flats to blob so Azure resume survives recycle mid-run
            try:
                checkpoint = _pipeline_module(
                    "publish_outputs_to_blob"
                ).checkpoint_outputs_to_blob
                ck = checkpoint(rel)
                uploaded = ck.get("uploaded") or []
                if uploaded:
                    _append_log(
                        f"Checkpoint → Blob: {', '.join(uploaded[:6])}"
                        + ("…" if len(uploaded) > 6 else "")
                    )
                for err in (ck.get("errors") or [])[:5]:
                    _append_log(f"  checkpoint error: {err}")
            except Exception as ck_exc:
                _append_log(f"WARNING: output checkpoint skipped: {ck_exc}")

            # After refill + SKU/SIZE: push replenishment final file to blob, then continue → transfer
            if rel == "7_enrich_sku_size.py":
                _ensure_not_cancelled()
                try:
                    with _JOB_LOCK:
                        _JOB.current_step = "publish_replenishment — Blob prediction/current"
                    _append_log(
                        "── Publish replenishment → Azure Blob (before transfer) ──"
                    )
                    publish_replenishment = _pipeline_module(
                        "publish_outputs_to_blob"
                    ).publish_replenishment_outputs_to_blob
                    mid = publish_replenishment()
                    _append_log(
                        f"Replenishment blob: {len(mid.get('uploaded') or [])} uploaded, "
                        f"{len(mid.get('skipped') or [])} skipped"
                    )
                    for name in (mid.get("uploaded") or [])[:20]:
                        _append_log(f"  ↑ {name}")
                    for err in (mid.get("errors") or [])[:10]:
                        _append_log(f"  replenishment publish error: {err}")
                    if mid.get("errors") and not mid.get("uploaded"):
                        raise RuntimeError(
                            f"Replenishment blob publish failed: {mid.get('errors')}"
                        )
                    with _JOB_LOCK:
                        _JOB.completed_steps.append("publish_replenishment")
                    _append_log("✓ Publish replenishment → Blob")
                except PipelineCancelled:
                    raise
                except Exception as mid_exc:
                    raise RuntimeError(
                        f"Replenishment blob publish failed: {mid_exc}"
                    ) from mid_exc

        # Push all outputs (incl. transfer) to blob, then wipe local temp copies
        _ensure_not_cancelled()
        try:
            with _JOB_LOCK:
                _JOB.current_step = "publish_outputs — Blob output/"
            _append_log("── Publish outputs → Azure Blob output/ ──")
            publish_and_clear = _pipeline_module("publish_outputs_to_blob").publish_and_clear

            clear_flag = (
                os.environ.get("PIPELINE_CLEAR_LOCAL_OUTPUTS", "1") or "1"
            ).strip().lower()
            do_clear = clear_flag not in ("0", "false", "no", "off")
            pub_result = publish_and_clear(clear_local=do_clear)
            pub = pub_result.get("publish") or {}
            _append_log(
                f"Blob publish: {len(pub.get('uploaded') or [])} uploaded, "
                f"{len(pub.get('skipped') or [])} skipped"
            )
            for name in (pub.get("uploaded") or [])[:25]:
                _append_log(f"  ↑ {name}")
            for err in (pub.get("errors") or [])[:10]:
                _append_log(f"  publish error: {err}")
            if pub.get("errors") and not pub.get("uploaded"):
                raise RuntimeError(f"Blob output publish failed: {pub.get('errors')}")
            cleared = pub_result.get("cleared")
            if cleared:
                _append_log(
                    f"Local outputs cleared ({len(cleared.get('removed') or [])} path(s))"
                )
            _append_log("✓ Blob output publish")
        except PipelineCancelled:
            raise
        except Exception as pub_exc:
            raise RuntimeError(f"Blob output publish failed: {pub_exc}") from pub_exc

        with _JOB_LOCK:
            _JOB.status = "success"
            _JOB.current_step = None
            _JOB.finished_at = datetime.now(timezone.utc).isoformat()
        _append_log("Pipeline finished OK")
    except PipelineCancelled as exc:
        with _JOB_LOCK:
            _JOB.status = "cancelled"
            _JOB.error = str(exc) or "Stopped by user"
            _JOB.finished_at = datetime.now(timezone.utc).isoformat()
        _append_log(f"CANCELLED: {exc}")
        raise
    except Exception as exc:
        with _JOB_LOCK:
            _JOB.status = "failed"
            _JOB.error = str(exc)
            _JOB.finished_at = datetime.now(timezone.utc).isoformat()
            _JOB.can_resume = bool(_JOB.resume_from)
        _append_log(f"FAILED: {exc}")
        _append_log(traceback.format_exc()[-1500:])
        _persist_job(force=True)
        raise
    finally:
        # Restore previous env so future operations are unaffected.
        if prev_wh_reset is None:
            os.environ.pop("WH_LEDGER_RESET", None)
        else:
            os.environ["WH_LEDGER_RESET"] = prev_wh_reset
        if prev_sku_scope is None:
            os.environ.pop("PIPELINE_SKU_SCOPE", None)
        else:
            os.environ["PIPELINE_SKU_SCOPE"] = prev_sku_scope


def _start_detached_worker(
    stage: str,
    *,
    sync_blob_inputs: bool | None = None,
    start_from: str | None = None,
    warehouse_mode: str | None = None,
    sku_scope: str | None = None,
) -> dict[str, Any]:
    """
    Spawn pipeline in a separate process so uvicorn --reload cannot kill Combine SOH mid-run.
    Job progress is shared via local/blob pipeline-job-state.json.
    """
    cmd = [sys.executable, "-u", "-m", "pipeline.runner", "--stage", str(stage)]
    if start_from:
        cmd.extend(["--from", str(start_from)])
    if sync_blob_inputs is False:
        cmd.append("--skip-blob-sync")

    env = dict(os.environ)
    env["PYTHONPATH"] = str(MERGED_BACKEND_DIR) + (
        (os.pathsep + env["PYTHONPATH"]) if env.get("PYTHONPATH") else ""
    )
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    # Child must not try to spawn another detached worker
    env["PIPELINE_DETACHED"] = "0"
    env["PIPELINE_AUTO_RESUME"] = "0"

    if warehouse_mode:
        wm = str(warehouse_mode).strip().lower()
        if wm == "fresh":
            env["WH_LEDGER_RESET"] = "1"
        elif wm == "incremental":
            env.pop("WH_LEDGER_RESET", None)
    if sku_scope:
        sc = str(sku_scope).strip().lower()
        if sc in ("remapped", "remap", "renamed"):
            env["PIPELINE_SKU_SCOPE"] = "remapped"
        elif sc == "all":
            env.pop("PIPELINE_SKU_SCOPE", None)

    log_path = PIPELINE_DIR / "output_files" / "pipeline-worker.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_fh = open(log_path, "a", encoding="utf-8")
    log_fh.write(
        f"\n===== pipeline start {datetime.now(timezone.utc).isoformat()} "
        f"stage={stage} from={start_from or ''} =====\n"
    )
    log_fh.flush()

    popen_kwargs: dict[str, Any] = {
        "cwd": str(MERGED_BACKEND_DIR),
        "env": env,
        "stdout": log_fh,
        "stderr": subprocess.STDOUT,
    }
    if sys.platform == "win32":
        # Break away from uvicorn/IDE job object so --reload does not CTRL-kill steps.
        # Avoid DETACHED_PROCESS here — it made child combine_soh exit 0xC000013A on localhost.
        popen_kwargs["creationflags"] = _windows_popen_flags(breakaway=True)
        popen_kwargs["close_fds"] = False
    else:
        popen_kwargs["start_new_session"] = True

    with _JOB_LOCK:
        _JOB = JobState(
            status="running",
            stage=stage if not start_from else f"{stage}@from:{start_from}",
            started_at=datetime.now(timezone.utc).isoformat(),
            current_step=f"{start_from} — starting" if start_from else "starting",
            resume_from=start_from,
            can_resume=False,
        )
    _persist_job(force=True)

    try:
        proc = subprocess.Popen(cmd, **popen_kwargs)
    except Exception:
        try:
            log_fh.close()
        except Exception:
            pass
        raise

    with _JOB_LOCK:
        _JOB.worker_pid = proc.pid
    _persist_job(force=True)
    log.info("Started detached pipeline worker pid=%s stage=%s from=%s", proc.pid, stage, start_from)

    msg = f"Started stage={stage}" + (f" from={start_from}" if start_from else "") + " (detached)"
    return {"ok": True, "message": msg, "job": get_job_state()}


def start_stage_async(
    stage: str,
    *,
    sync_blob_inputs: bool | None = None,
    start_from: str | None = None,
    warehouse_mode: str | None = None,
    sku_scope: str | None = None,
) -> dict[str, Any]:
    global _WORKER
    _recover_interrupted_job()
    with _JOB_LOCK:
        if _JOB.status == "running" and (
            (_WORKER is not None and _WORKER.is_alive())
            or _pid_alive(_JOB.worker_pid)
        ):
            return {"ok": False, "message": "Job already running", "job": _JOB.to_dict()}

    # Default: detached process so API --reload does not kill Combine SOH.
    use_detached = (os.environ.get("PIPELINE_DETACHED", "1") or "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )
    # Avoid nested detach when this process IS the worker (`python -m pipeline.runner`)
    if os.environ.get("PIPELINE_WORKER") == "1":
        use_detached = False

    if use_detached:
        try:
            return _start_detached_worker(
                stage,
                sync_blob_inputs=sync_blob_inputs,
                start_from=start_from,
                warehouse_mode=warehouse_mode,
                sku_scope=sku_scope,
            )
        except Exception as exc:
            log.warning("Detached pipeline spawn failed (%s) — falling back to in-process thread", exc)

    def _target() -> None:
        os.environ["PIPELINE_WORKER"] = "1"
        try:
            run_stage(
                stage,
                sync_blob_inputs=sync_blob_inputs,
                start_from=start_from,
                warehouse_mode=warehouse_mode,
                sku_scope=sku_scope,
            )
        except Exception:
            pass  # state already recorded

    _WORKER = threading.Thread(target=_target, daemon=True, name=f"pipeline-{stage}")
    _WORKER.start()
    msg = f"Started stage={stage}" + (f" from={start_from}" if start_from else "")
    return {"ok": True, "message": msg, "job": get_job_state()}


def list_key_outputs() -> list[dict[str, Any]]:
    """Key output files on blob (primary) + local temp if still present."""
    from pipeline_inputs import OUTPUT_DIR, BASE_DIR, PIPELINE_SALES_CANDIDATES

    files = [
        PIPELINE_SALES_CANDIDATES[0],
        OUTPUT_DIR / "COMBINED_SOH.xlsx",
        OUTPUT_DIR / "COMBINED_TRANSFER.xlsx",
        OUTPUT_DIR / "SS26_weekly_predictions_formula_next_week_from_5w_avg_selected_stores.csv",
        OUTPUT_DIR / "SS26_weekly_predictions_formula_next_week_with_refill_recommendation.csv",
        OUTPUT_DIR / "prediction" / "current" / "SS26_weekly_predictions_formula_next_week_with_refill_recommendation.csv",
        OUTPUT_DIR / "transfer" / "current" / "final_transfer_sizewise.csv",
        BASE_DIR / "transfer" / "output" / "potential_donors.csv",
        BASE_DIR / "transfer" / "output" / "potential_receivers.csv",
        BASE_DIR / "transfer" / "output" / "final_transfer_sizewise.csv",
    ]
    out = []
    for p in files:
        out.append({
            "name": p.name,
            "path": str(p),
            "exists": p.exists(),
            "size_mb": round(p.stat().st_size / 1e6, 2) if p.exists() else 0,
            "mtime": datetime.fromtimestamp(p.stat().st_mtime).isoformat() if p.exists() else None,
            "note": "local temp (cleared after blob publish)",
        })

    # Also list blob output/ when configured
    try:
        _blob = _pipeline_module("publish_outputs_to_blob")
        _get_container, _list_under = _blob._get_container, _blob._list_under

        cc, container, out_pfx = _get_container()
        for blob_name in _list_under(cc, out_pfx)[:80]:
            out.append({
                "name": Path(blob_name).name,
                "path": f"blob://{container}/{blob_name}",
                "exists": True,
                "size_mb": None,
                "mtime": None,
                "note": "azure blob",
            })
    except Exception as e:
        out.append({
            "name": "_blob_list_error",
            "path": "",
            "exists": False,
            "size_mb": 0,
            "mtime": None,
            "note": str(e),
        })
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    try:
        from dotenv import load_dotenv

        load_dotenv(MERGED_BACKEND_DIR / ".env")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="Run zinc pipeline stages")
    parser.add_argument(
        "--stage",
        choices=list(STAGE_MAP.keys()),
        default="all",
        help="combine | refill | transfer | all",
    )
    parser.add_argument(
        "--skip-blob-sync",
        action="store_true",
        help="Do not download Azure Blob input/ before running (use local files only)",
    )
    parser.add_argument(
        "--sync-only",
        action="store_true",
        help="Only sync Blob input/ → local input_files, then exit",
    )
    parser.add_argument(
        "--from",
        dest="start_from",
        default=None,
        help="Resume from this script (e.g. 7_enrich_sku_size.py). Skips blob sync.",
    )
    args = parser.parse_args()
    # This process owns the job — never detach again from here.
    os.environ["PIPELINE_WORKER"] = "1"
    os.environ["PIPELINE_DETACHED"] = "0"
    os.environ["PIPELINE_AUTO_RESUME"] = "0"
    if args.sync_only:
        sync_inputs_from_blob = _pipeline_module("sync_blob_inputs").sync_inputs_from_blob

        result = sync_inputs_from_blob()
        print(json.dumps(result, indent=2, default=str))
        raise SystemExit(0 if result.get("ok") else 1)
    run_stage(
        args.stage,
        sync_blob_inputs=not args.skip_blob_sync,
        start_from=args.start_from,
    )
    print(json.dumps(get_job_state(), indent=2))


if __name__ == "__main__":
    main()

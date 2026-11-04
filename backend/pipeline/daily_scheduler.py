"""
In-process daily scheduler: fetch + merge + combine at a fixed local time.

Default: 09:00 Asia/Kolkata (IST). Override with DAILY_PIPELINE_HOUR if Raw_Files land later.
Refill / prediction / transfer run only when user clicks Run Pipeline (Manual Upload).

Env:
  DAILY_PIPELINE_SCHEDULER=1|0     (default 1)
  DAILY_PIPELINE_STAGE=combine     (default combine; not all)
  DAILY_PIPELINE_HOUR=9
  DAILY_PIPELINE_MINUTE=0
  DAILY_PIPELINE_CATCHUP_MINUTES=90
  DAILY_PIPELINE_TZ=Asia/Kolkata
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

log = logging.getLogger("pipeline.daily_scheduler")

_lock = threading.Lock()
_last_run_date: Optional[str] = None
_running = False
_last_result: Optional[Dict[str, Any]] = None
_started = False


def _enabled() -> bool:
    return os.environ.get("DAILY_PIPELINE_SCHEDULER", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _schedule_tz() -> ZoneInfo:
    name = os.environ.get("DAILY_PIPELINE_TZ", "Asia/Kolkata").strip() or "Asia/Kolkata"
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("Asia/Kolkata")


def _run_hour_minute() -> tuple[int, int]:
    try:
        hour = int(os.environ.get("DAILY_PIPELINE_HOUR", "9"))
    except ValueError:
        hour = 9
    try:
        minute = int(os.environ.get("DAILY_PIPELINE_MINUTE", "0"))
    except ValueError:
        minute = 0
    return max(0, min(23, hour)), max(0, min(59, minute))


_STAMP_NAME = "pipeline/last_scheduled_date.txt"


def _stamp_path() -> Path:
    return Path(__file__).resolve().parent / "output_files" / "auto_runs" / "last_scheduled_date.txt"


def _read_stamp_text() -> str:
    try:
        local = _stamp_path().read_text(encoding="utf-8").strip()
    except OSError:
        local = ""
    if len(local) >= 10:
        return local[:10]
    try:
        from api.routes.sales_dashboard import _blob_store

        store = _blob_store()
        if store is None:
            return ""
        raw = store._download_bytes(f"{store.output_prefix}{_STAMP_NAME}")
        if not raw:
            return ""
        return raw.decode("utf-8").strip()[:10]
    except Exception:
        log.warning("Could not read scheduled run date from blob", exc_info=True)
        return ""


def _load_last_run_date() -> None:
    """Remember today's run across the restart that frees RAM."""
    global _last_run_date
    text = _read_stamp_text()
    if len(text) >= 10:
        _last_run_date = text[:10]


def _save_last_run_date(day: str) -> None:
    path = _stamp_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(day, encoding="utf-8")
    except OSError:
        log.warning("Could not persist scheduled run date %s", day)
    try:
        from api.routes.sales_dashboard import _blob_store

        store = _blob_store()
        if store is None:
            return
        store._upload_bytes(
            f"{store.output_prefix}{_STAMP_NAME}",
            day.encode("utf-8"),
            "text/plain",
        )
    except Exception:
        log.warning("Could not persist scheduled run date %s to blob", day, exc_info=True)


def _exit_for_clean_ram() -> None:
    """Python keeps the merge tables after the job. Exiting returns that RAM to the OS."""
    on_azure = bool(os.environ.get("WEBSITE_SITE_NAME") or os.environ.get("WEBSITE_INSTANCE_ID"))
    if not on_azure:
        log.info("Scheduled pipeline finished; process restart skipped off Azure")
        return
    log.info("Scheduled pipeline finished; exiting so App Service restarts with free RAM")
    logging.shutdown()
    os._exit(0)


def _catchup_minutes() -> int:
    """Maximum post-schedule startup delay that may trigger today's run."""
    try:
        value = int(os.environ.get("DAILY_PIPELINE_CATCHUP_MINUTES", "90"))
    except ValueError:
        value = 90
    return max(0, min(180, value))


def get_scheduler_status() -> Dict[str, Any]:
    tz = _schedule_tz()
    hour, minute = _run_hour_minute()
    now = datetime.now(tz)
    daily_run = {}
    try:
        from pipeline.daily_run_tracker import get_status as get_daily_run_status

        daily_run = get_daily_run_status()
    except Exception:
        daily_run = {}
    return {
        "enabled": _enabled(),
        "started": _started,
        "running": _running or daily_run.get("running", False),
        "schedule": f"{hour:02d}:{minute:02d}",
        "catchup_minutes": _catchup_minutes(),
        "timezone": str(tz),
        "now": now.isoformat(),
        "last_run_date": _last_run_date,
        "last_result_ok": (_last_result or {}).get("ok") if _last_result else daily_run.get("result", {}).get("ok") if daily_run.get("result") else None,
        "daily_stage": _daily_stage(),
        "daily_run": daily_run,
    }


def _daily_stage() -> str:
    stage = (os.environ.get("DAILY_PIPELINE_STAGE") or "combine").strip().lower()
    return stage if stage in ("combine", "all", "refill", "transfer") else "combine"


def trigger_daily_run(*, trigger: str = "manual") -> Dict[str, Any]:
    """Start daily pipeline now in background (for UI test / manual run)."""
    from pipeline.daily_run_tracker import begin_run, is_running

    if is_running():
        return {"ok": False, "error": "Daily pipeline already running", "running": True}

    if not begin_run(trigger=trigger):
        return {"ok": False, "error": "Could not start — already running", "running": True}

    def _bg() -> None:
        global _last_result
        try:
            result = run_scheduled_job_inner()
            _last_result = result
        except Exception as exc:
            log.exception("Manual daily trigger failed")
            _last_result = {"ok": False, "error": str(exc)}

    threading.Thread(target=_bg, daemon=True, name="daily-pipeline-manual").start()
    return {"ok": True, "started": True, "message": "Daily pipeline started"}


def run_scheduled_job_inner() -> Dict[str, Any]:
    """Run fetch → merge → pipeline with tracker (shared by scheduler + manual trigger)."""
    global _running, _last_result

    with _lock:
        if _running:
            return {"skipped": True, "reason": "already_running"}
        _running = True

    try:
        from pipeline.auto_daily_run import run_auto_daily

        log.info("── Daily pipeline starting (stage=%s) ──", _daily_stage())
        result = run_auto_daily(
            stage=_daily_stage(),
            warehouse_mode="fresh",
            track_progress=True,
        )
        _last_result = result
        log.info("── Daily pipeline finished ok=%s ──", result.get("ok"))
        return result
    except Exception as exc:
        log.exception("Daily pipeline failed")
        _last_result = {"ok": False, "error": str(exc)}
        try:
            from pipeline.daily_run_tracker import finish_run as tracker_finish_run

            tracker_finish_run(_last_result)
        except ImportError:
            pass
        return _last_result
    finally:
        with _lock:
            _running = False


def run_scheduled_job() -> Dict[str, Any]:
    """Run fetch → merge → SOH reconcile → combine (no auto refill/prediction)."""
    from pipeline.daily_run_tracker import begin_run, is_running

    if is_running():
        log.warning("Daily pipeline already running — skip duplicate trigger")
        return {"skipped": True, "reason": "already_running"}

    if not begin_run(trigger="scheduled"):
        return {"skipped": True, "reason": "already_running"}

    try:
        return run_scheduled_job_inner()
    finally:
        _exit_for_clean_ram()


def _scheduler_loop() -> None:
    global _last_run_date

    _load_last_run_date()
    tz = _schedule_tz()
    hour, minute = _run_hour_minute()
    log.info(
        "Daily pipeline scheduler active — %02d:%02d %s (DAILY_PIPELINE_SCHEDULER=%s)",
        hour,
        minute,
        tz,
        os.environ.get("DAILY_PIPELINE_SCHEDULER", "1"),
    )

    while True:
        try:
            now = datetime.now(tz)
            today_key = now.strftime("%Y-%m-%d")
            # Catch up only shortly after schedule. An App Service deploy/restart
            # hours later must not launch this memory-heavy job while users browse.
            scheduled_minutes = hour * 60 + minute
            now_minutes = now.hour * 60 + now.minute
            delay_minutes = now_minutes - scheduled_minutes
            in_run_window = 0 <= delay_minutes <= _catchup_minutes()
            if in_run_window and _last_run_date != today_key:
                with _lock:
                    if _last_run_date == today_key:
                        pass
                    else:
                        _last_run_date = today_key
                        _save_last_run_date(today_key)
                        threading.Thread(
                            target=run_scheduled_job,
                            daemon=True,
                            name="daily-pipeline-run",
                        ).start()
        except Exception:
            log.exception("Daily scheduler tick failed")
        time.sleep(30)


def start_daily_scheduler() -> None:
    """Start background scheduler thread (idempotent)."""
    global _started

    _load_last_run_date()
    if not _enabled():
        log.info("Daily pipeline scheduler disabled (DAILY_PIPELINE_SCHEDULER=0)")
        return

    with _lock:
        if _started:
            return
        _started = True

    t = threading.Thread(target=_scheduler_loop, daemon=True, name="daily-scheduler")
    t.start()
    log.info("Daily pipeline scheduler thread started")

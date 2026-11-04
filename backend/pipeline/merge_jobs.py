"""
Background merge jobs for Home-tab uploads.

Upload writes to Blob incoming/ quickly; merge runs in a worker thread so
HTTP gateways do not time out with 502/504 on large Excel merges.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger("pipeline.merge_jobs")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class MergeJobState:
    job_id: str
    status: str = "pending"  # pending | running | success | failed
    category_ids: List[str] = field(default_factory=list)
    sync_local: bool = False
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    elapsed_s: Optional[float] = None
    progress_pct: int = 0
    progress_message: str = "Queued…"
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


_JOBS: Dict[str, MergeJobState] = {}
_LOCK = threading.Lock()
_WORKER: Optional[threading.Thread] = None
_PENDING: List[Dict[str, Any]] = []


def _update_progress(job: MergeJobState, pct: int, msg: str) -> None:
    with _LOCK:
        job.progress_pct = max(0, min(100, int(pct)))
        job.progress_message = str(msg or "Merging…")


def _run_merge_job(job: MergeJobState) -> None:
    from pipeline.merge_category_inputs import (
        clear_incoming_bytes_cache,
        merge_all_categories,
        sync_current_to_local,
    )

    job.status = "running"
    job.started_at = _utc_now()
    _update_progress(job, 1, f"Starting merge ({len(job.category_ids)} categor{'y' if len(job.category_ids) == 1 else 'ies'})…")
    t0 = time.perf_counter()
    try:

        def _on_progress(pct: int, msg: str) -> None:
            _update_progress(job, pct, msg)

        merge_result = merge_all_categories(
            dry_run=False,
            keep_incoming=False,
            only_ids=job.category_ids,
            on_progress=_on_progress,
        )
        if merge_result.get("ok"):
            actually_merged = [
                r.get("category_id")
                for r in (merge_result.get("merged") or [])
                if r.get("category_id") and not r.get("skipped")
            ]
            if job.sync_local and actually_merged:
                _update_progress(
                    job,
                    90,
                    f"Syncing {len(actually_merged)} merged file(s) to local input_files…",
                )
                merge_result["local_sync"] = sync_current_to_local(
                    dry_run=False,
                    only_ids=actually_merged,
                )
            elif job.sync_local:
                merge_result["local_sync"] = {
                    "skipped": True,
                    "reason": "all_merges_skipped_current_unchanged",
                }
            else:
                merge_result["local_sync"] = {"skipped": True, "reason": "sync_local=0"}

            # Upload merge is done — mark 100% before optional post-merge housekeeping
            # (fold daily → master, SOH refresh) which can take many more minutes.
            job.result = merge_result
            job.status = "success"
            _update_progress(job, 100, "Merge complete")

            def _post_merge_refresh_worker() -> None:
                try:
                    from pipeline.post_merge_refresh import post_merge_refresh

                    refresh = post_merge_refresh(
                        merged_category_ids=actually_merged or job.category_ids,
                        run_historical_soh=True,
                        historical_soh_async=True,
                    )
                    merge_result["post_merge_refresh"] = {
                        "ok": refresh.get("ok"),
                        "masters_merged": (refresh.get("masters") or {}).get(
                            "masters_merged"
                        ),
                        "historical_soh": refresh.get("historical_soh"),
                        "soh_reconcile": refresh.get("soh_reconcile"),
                        "errors": (refresh.get("masters") or {}).get("errors") or [],
                    }
                except Exception as refresh_exc:
                    log.exception("post-merge refresh after upload merge failed")
                    merge_result["post_merge_refresh"] = {
                        "ok": False,
                        "error": str(refresh_exc),
                    }
                finally:
                    job.result = merge_result

            threading.Thread(
                target=_post_merge_refresh_worker,
                daemon=True,
                name="post-merge-refresh",
            ).start()
        else:
            job.result = merge_result
            job.status = "failed"
            _update_progress(job, job.progress_pct, "Merge finished with errors")
            failed = merge_result.get("failed") or []
            bits = []
            for f in failed:
                bits.append(
                    f"{f.get('category_id')}: "
                    f"{'; '.join(f.get('errors') or [f.get('reason') or 'merge failed'])}"
                )
            job.error = " | ".join(bits) if bits else "merge failed"
    except Exception as exc:
        log.exception("Background merge failed")
        job.status = "failed"
        job.error = str(exc)
        job.result = {"ok": False, "error": str(exc)}
        _update_progress(job, job.progress_pct, f"Failed: {exc}")
    finally:
        clear_incoming_bytes_cache()
        job.finished_at = _utc_now()
        job.elapsed_s = round(time.perf_counter() - t0, 2)


def _worker_loop() -> None:
    while True:
        with _LOCK:
            if not _PENDING:
                break
            spec = _PENDING.pop(0)
            job = spec["job"]
        _run_merge_job(job)


def start_merge_async(
    category_ids: List[str],
    *,
    sync_local: bool = False,
) -> MergeJobState:
    """Queue merge for category ids; returns job handle immediately."""
    global _WORKER

    job = MergeJobState(
        job_id=str(uuid.uuid4()),
        category_ids=list(category_ids),
        sync_local=bool(sync_local),
        status="pending",
    )
    with _LOCK:
        _JOBS[job.job_id] = job
        # Keep last ~20 jobs for status polling
        if len(_JOBS) > 20:
            for old_id in list(_JOBS.keys())[:-20]:
                if _JOBS[old_id].status in ("success", "failed"):
                    _JOBS.pop(old_id, None)
        _PENDING.append({"job": job})
        need_worker = _WORKER is None or not _WORKER.is_alive()

    if need_worker:
        _WORKER = threading.Thread(target=_worker_loop, daemon=True, name="merge-upload")
        _WORKER.start()

    return job


def get_merge_job(job_id: str) -> Optional[MergeJobState]:
    with _LOCK:
        return _JOBS.get(job_id)


def get_latest_merge_job() -> Optional[MergeJobState]:
    with _LOCK:
        if not _JOBS:
            return None
        return list(_JOBS.values())[-1]

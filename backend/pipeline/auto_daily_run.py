"""
Daily automatic pipeline:
  1) Preview Raw_Files → warn on missing / gap dates
  2) Fetch Raw_Files → category incoming/
  3) Merge incoming → current/ + sync local input_files/
  4) Fold daily sales/transfer → masters + refresh historical weekly/monthly SOH
  5) Run combine stage only (sales + SOH + transfer inputs) — refill/prediction is manual

Usage (from meridianai-stockpulse-backend/):
  python -m pipeline.auto_daily_run
  python -m pipeline.auto_daily_run --stage combine
  python -m pipeline.auto_daily_run --stage all --warehouse-mode fresh   # full run (manual only)
  python -m pipeline.auto_daily_run --skip-pipeline   # fetch+merge+masters+SOH only
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

PIPELINE_DIR = Path(__file__).resolve().parent
BACKEND_DIR = PIPELINE_DIR.parent
LOG_DIR = PIPELINE_DIR / "output_files" / "auto_runs"

if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

log = logging.getLogger("pipeline.auto_daily_run")


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(BACKEND_DIR / ".env")
    except ImportError:
        pass


def _write_run_artifacts(payload: Dict[str, Any]) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = LOG_DIR / f"auto_daily_{stamp}.json"
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    latest = LOG_DIR / "latest.json"
    latest.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


def _merge_detail(merge: Dict[str, Any]) -> str:
    merged_n = len([r for r in (merge.get("merged") or []) if not r.get("skipped")])
    skipped = merge.get("skipped_count", 0)
    failed = merge.get("failed") or []
    bits = [f"{merged_n} merged · {skipped} skipped"]
    for f in failed[:3]:
        cid = f.get("category_id") or "?"
        errs = f.get("errors") or []
        err = errs[0] if errs else f.get("reason") or "merge failed"
        if "BlobNotFound" in str(err) or "does not exist" in str(err):
            err = "incoming file missing on Blob (already merged/cleared — retry fetch+merge)"
        bits.append(f"FAILED {cid}: {err}")
    return " · ".join(bits)


def run_auto_daily(
    *,
    stage: str = "all",
    warehouse_mode: str = "fresh",
    skip_pipeline: bool = False,
    dry_run: bool = False,
    track_progress: bool = True,
) -> Dict[str, Any]:
    _load_dotenv()
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")

    tracker = None
    if track_progress:
        try:
            from pipeline.daily_run_tracker import (
                finish_run as tracker_finish_run,
                finish_step as tracker_finish_step,
                skip_step as tracker_skip_step,
                start_step as tracker_start_step,
                update_step as tracker_update_step,
            )

            tracker = {
                "start": tracker_start_step,
                "update": tracker_update_step,
                "finish": tracker_finish_step,
                "skip": tracker_skip_step,
                "finish_run": tracker_finish_run,
            }
        except ImportError:
            tracker = None

    def _start(step_id: str, msg: str | None = None) -> None:
        if tracker:
            tracker["start"](step_id, msg)

    def _update(step_id: str, msg: str, sub_pct: int = 0) -> None:
        if tracker:
            tracker["update"](step_id, msg, sub_pct=sub_pct)

    def _done(step_id: str, ok: bool = True, detail: str | None = None) -> None:
        if tracker:
            tracker["finish"](step_id, ok=ok, detail=detail)

    def _skip(step_id: str, reason: str) -> None:
        if tracker:
            tracker["skip"](step_id, reason)

    from pipeline.ginesys_raw import (
        collect_data_gap_warnings,
        fetch_raw_to_incoming,
        preview_raw_fetch,
    )
    from pipeline.merge_category_inputs import merge_all_categories, sync_current_to_local
    from pipeline.runner import get_job_state, run_stage

    started = datetime.now(timezone.utc).isoformat()
    result: Dict[str, Any] = {
        "ok": False,
        "started_at": started,
        "stage": stage,
        "warehouse_mode": warehouse_mode,
        "dry_run": dry_run,
        "warnings": [],
        "fetch": None,
        "merge": None,
        "local_sync": None,
        "pipeline": None,
        "error": None,
    }

    artifact_path: Path | None = None

    try:
        _start("preview", "Scanning Raw_Files coverage…")
        log.info("── Auto daily: preview Raw_Files coverage ──")
        preview = preview_raw_fetch()
        warnings = collect_data_gap_warnings(preview=preview)
        result["warnings"] = warnings
        warn_detail = f"{len(warnings)} warning(s)" if warnings else "No missing dates"
        _done("preview", ok=True, detail=warn_detail)
        for w in warnings:
            log.warning("%s", w)
        if not warnings:
            log.info("No missing daily Raw_Files dates detected")

        if dry_run:
            result["ok"] = True
            result["fetch"] = {"dry_run": True, "preview": preview}
            result["finished_at"] = datetime.now(timezone.utc).isoformat()
            artifact_path = _write_run_artifacts(result)
            for sid in (
                "fetch",
                "merge",
                "local_sync",
                "post_merge",
                "sales_cache",
                "daily_movement",
                "analyzer_cache",
                "page_cache",
                "pipeline",
            ):
                _skip(sid, "dry_run preview only")
            if track_progress:
                from pipeline.daily_run_tracker import finish_run as tracker_finish_run

                tracker_finish_run(result, artifact_path=artifact_path)
            return result

        # RTV/IBT: if current master is corrupt (e.g. RTV CSV mixed with IBT rows),
        # promote Raw_Files till-date dump before normal daily fetch/merge.
        try:
            from pipeline.repair_movement_masters import ensure_movement_masters

            log.info("── Auto daily: repair RTV/IBT masters if corrupt ──")
            repair = ensure_movement_masters()
            result["movement_masters_repair"] = repair
            if repair.get("rtv", {}).get("replaced"):
                log.info(
                    "RTV master repaired from Raw_Files till-date (rows=%s max=%s)",
                    (repair.get("rtv") or {}).get("normalized", {}).get("rows"),
                    (repair.get("rtv") or {}).get("normalized", {}).get("max_str"),
                )
        except Exception as repair_exc:
            log.warning("movement master repair skipped: %s", repair_exc)
            result["movement_masters_repair"] = {"ok": False, "error": str(repair_exc)}

        _start("fetch", "Copying new Raw_Files to incoming/…")
        log.info("── Auto daily: fetch Raw_Files → incoming/ ──")
        fetch = fetch_raw_to_incoming(dry_run=False)
        copied_n = len(fetch.get("copied") or [])
        touched_list = list(fetch.get("touched_categories") or [])
        result["fetch"] = {
            "ok": fetch.get("ok"),
            "touched_categories": touched_list,
            "copied_count": copied_n,
            "errors": fetch.get("errors") or [],
        }
        _done(
            "fetch",
            ok=bool(fetch.get("ok")),
            detail=f"{copied_n} file(s) copied · {len(touched_list)} categor{'y' if len(touched_list) == 1 else 'ies'}",
        )
        if fetch.get("errors"):
            for e in fetch["errors"][:20]:
                log.error("fetch error: %s", e)

        touched: List[str] = touched_list
        if touched:
            _start("merge", f"Merging {len(touched)} categor{'y' if len(touched) == 1 else 'ies'}…")
            log.info("── Auto daily: merge categories %s ──", touched)

            def _merge_progress(pct: int, msg: str) -> None:
                _update("merge", msg, sub_pct=pct)

            merge = merge_all_categories(
                dry_run=False,
                keep_incoming=False,
                only_ids=touched,
                on_progress=_merge_progress,
            )
            result["merge"] = merge
            _done(
                "merge",
                ok=merge.get("ok", False),
                detail=_merge_detail(merge),
            )
            for w in merge.get("warnings") or []:
                if w and w not in result["warnings"]:
                    result["warnings"].append(str(w))
                    log.warning("%s", w)

            actually = [
                r.get("category_id")
                for r in (merge.get("merged") or [])
                if r.get("category_id") and not r.get("skipped")
            ]
            if actually:
                _start("local_sync", f"Downloading {len(actually)} merged file(s) locally…")
                sync = sync_current_to_local(dry_run=False, only_ids=actually)
                result["local_sync"] = {
                    "ok": sync.get("ok"),
                    "downloaded": len(sync.get("downloaded") or []),
                    "errors": sync.get("errors") or [],
                }
                _done(
                    "local_sync",
                    ok=bool(sync.get("ok")),
                    detail=f"{len(sync.get('downloaded') or [])} file(s) synced",
                )
                log.info(
                    "Local sync: %s file(s)",
                    len(sync.get("downloaded") or []),
                )
            else:
                _skip("local_sync", "No categories merged — kept existing local files")
                result["local_sync"] = {"skipped": True, "reason": "no_merges"}
        else:
            _skip("merge", "No new Raw_Files to merge")
            log.info("No new Raw_Files to merge — keeping existing current/")
            _start("local_sync", "Syncing all current/ files to local…")
            sync = sync_current_to_local(dry_run=False)
            result["local_sync"] = {
                "ok": sync.get("ok"),
                "downloaded": len(sync.get("downloaded") or []),
                "errors": sync.get("errors") or [],
            }
            _done(
                "local_sync",
                ok=bool(sync.get("ok")),
                detail=f"{len(sync.get('downloaded') or [])} file(s) synced",
            )

        try:
            from pipeline.post_merge_refresh import DAILY_TO_MASTER, post_merge_refresh

            touched_for_refresh = list(fetch.get("touched_categories") or [])
            # Always fold Sales-Master + Transfer-Master alongside daily.
            # Delta comes from Raw_Files or (if cleared) daily current/ so masters
            # catch up even when Raw_Files were already consumed.
            for daily_id in DAILY_TO_MASTER:
                if daily_id not in touched_for_refresh:
                    touched_for_refresh.append(daily_id)
            _start("post_merge", "Folding daily → Sales/Transfer masters + SOH reconcile…")
            log.info(
                "── Auto daily: fold masters %s + SOH reconcile (historical SOH skipped by default) ──",
                list(DAILY_TO_MASTER.values()),
            )
            # Historical weekly/monthly SOH already built under output_files/historical_soh/.
            # Daily job only refreshes masters + COMBINED_SOH — set DAILY_HISTORICAL_SOH=1 to rebuild.
            daily_historical = os.environ.get("DAILY_HISTORICAL_SOH", "0").strip().lower() in (
                "1",
                "true",
                "yes",
            )
            refresh = post_merge_refresh(
                merged_category_ids=touched_for_refresh,
                run_historical_soh=daily_historical,
                historical_soh_async=True if daily_historical else False,
                refresh_inventory_movement_async=False,
            )
            result["post_merge_refresh"] = refresh
            masters = (refresh.get("masters") or {}).get("masters_merged") or []
            soh_rec = (refresh.get("soh_reconcile") or {}).get("action") or "—"
            hist = refresh.get("historical_soh") or {}
            hist_note = (
                "historical skipped (already built)"
                if hist.get("skipped")
                else "historical rebuilt"
                if hist.get("ok")
                else "historical async"
                if hist.get("started_async")
                else "no historical"
            )
            _done(
                "post_merge",
                ok=bool(refresh.get("ok")),
                detail=f"masters={len(masters)} · SOH={soh_rec} · {hist_note}",
            )
            if not refresh.get("ok"):
                for e in (refresh.get("masters") or {}).get("errors") or []:
                    log.warning("post-merge: %s", e)
                soh_err = (refresh.get("historical_soh") or {}).get("error")
                if soh_err:
                    log.warning("historical SOH: %s", soh_err)
            else:
                log.info(
                    "Post-merge OK masters=%s soh=%s",
                    masters,
                    bool((refresh.get("historical_soh") or {}).get("ok")
                         or (refresh.get("historical_soh") or {}).get("started_async")
                         or (refresh.get("historical_soh") or {}).get("skipped")),
                )
        except Exception as refresh_exc:
            log.exception("post-merge refresh failed (continuing to pipeline)")
            result["post_merge_refresh"] = {"ok": False, "error": str(refresh_exc)}
            _done("post_merge", ok=False, detail=str(refresh_exc))

        # Sales pages read Sales-Master, not the daily Sales file. Warm only
        # after post_merge_refresh has folded daily rows into Sales-Master.
        try:
            _start("sales_cache", "Preparing Sales Overview cache from updated master…")
            log.info("── Auto daily: Sales Overview cache warm (after master fold) ──")
            from api.routes.sales_dashboard import (
                warm_sales_overview_cache,
                warm_sales_overview_progressive_caches,
            )

            warm = warm_sales_overview_cache(force_refresh=True)
            # FE hits /kpis|/insights|/rows — warm those page_cache blobs every morning.
            progressive = warm_sales_overview_progressive_caches(force_refresh=True)
            result["sales_cache"] = {
                **(warm if isinstance(warm, dict) else {"ok": bool(warm)}),
                "progressive": progressive,
            }
            sales_ok = bool((warm or {}).get("ok")) and bool((progressive or {}).get("ok"))
            warm_detail = warm.get("detail") if isinstance(warm, dict) else warm
            prog_sec = progressive.get("seconds") if isinstance(progressive, dict) else "?"
            _done(
                "sales_cache",
                ok=sales_ok,
                detail=f"master={warm_detail} · progressive={prog_sec}s",
            )
            log.info(
                "Sales Overview cache: %s progressive=%s",
                warm.get("detail") if isinstance(warm, dict) else warm,
                progressive,
            )
        except Exception as cache_exc:
            log.exception("Sales Overview cache warm failed (continuing)")
            result["sales_cache"] = {"ok": False, "error": str(cache_exc)}
            _done("sales_cache", ok=False, detail=str(cache_exc))

        # Managers / Analyzer SOH need daily_inventory_movement — build it sync before page cache
        # Build synchronously so /managers cache is correct before page-cache warm.
        try:
            _start("daily_movement", "Building daily inventory movement (SOH) for Managers/Analyzer…")
            from pipeline.build_daily_inventory_movement import build as build_inv_movement

            inv = build_inv_movement()
            dm_pub: dict = {}
            try:
                from api.current_soh import publish_daily_movement_to_blob

                dm_pub = publish_daily_movement_to_blob()
                log.info("Daily inventory movement published to blob: %s", dm_pub)
            except Exception as pub_exc:
                dm_pub = {"ok": False, "error": str(pub_exc)}
                log.warning("Daily inventory movement blob publish failed: %s", pub_exc)
            result["daily_inventory_movement"] = {
                "ok": bool((inv or {}).get("ok", True) if isinstance(inv, dict) else True),
                "detail": "built before page_cache",
                "published": dm_pub,
            }
            _done(
                "daily_movement",
                ok=True,
                detail=f"ready · blob_pub={bool(dm_pub.get('ok'))}",
            )
            log.info("Daily inventory movement ready before page cache")
        except Exception as inv_exc:
            log.exception("Daily inventory movement sync build failed (page cache may have stale SOH)")
            result["daily_inventory_movement"] = {"ok": False, "error": str(inv_exc)}
            _done("daily_movement", ok=False, detail=str(inv_exc))

        # Sales Pivot Analyzer: rebuild prepared fact frames from updated masters → blob.
        # Hosted queries pick these parquet/key files instead of cold Sales-Master prepare.
        try:
            _start("analyzer_cache", "Building Sales Pivot prepared frames → Blob…")
            log.info("── Auto daily: Sales Pivot Analyzer prepared cache (force + blob publish) ──")
            from api.routes.sales_analyzer import refresh_analyzer_prepared_caches

            analyzer_cache = refresh_analyzer_prepared_caches(
                force=True,
                years=None,  # dynamic supported FY list
                publish=True,
            )
            result["analyzer_cache"] = analyzer_cache
            _done(
                "analyzer_cache",
                ok=bool(analyzer_cache.get("ok")),
                detail=str(analyzer_cache.get("detail") or ""),
            )
            log.info("Analyzer prepared cache: %s", analyzer_cache.get("detail"))
        except Exception as an_exc:
            log.exception("Analyzer prepared cache failed (continuing)")
            result["analyzer_cache"] = {"ok": False, "error": str(an_exc)}
            _done("analyzer_cache", ok=False, detail=str(an_exc))

        # Always pull latest Target LFS from blob before Home/target page caches.
        # Target is replace-mode and usually not in daily Raw_Files — without this,
        # a Manual Upload that landed on blob after last local sync (or only on Azure)
        # would leave Home Target/Achievement on yesterday's file until someone re-syncs.
        try:
            _start("target_sync", "Syncing Target LFS from blob current/…")
            from pipeline.merge_category_inputs import sync_current_to_local as _sync_target

            tgt_sync = _sync_target(dry_run=False, only_ids=["target-lfs"])
            result["target_lfs_sync"] = {
                "ok": bool(tgt_sync.get("ok")),
                "downloaded": len(tgt_sync.get("downloaded") or []),
                "skipped": len(tgt_sync.get("skipped") or []),
                "errors": tgt_sync.get("errors") or [],
            }
            # Drop in-memory target frame so warm rebuilds from the synced file.
            try:
                from api.routes import target_vs_achieved as _tva

                _tva._MEMORY_CACHE.clear()
            except Exception:
                pass
            _done(
                "target_sync",
                ok=bool(tgt_sync.get("ok")),
                detail=(
                    f"dl={len(tgt_sync.get('downloaded') or [])} "
                    f"skip={len(tgt_sync.get('skipped') or [])}"
                ),
            )
            log.info("Target LFS sync before page cache: %s", result["target_lfs_sync"])
        except Exception as tgt_exc:
            log.warning("Target LFS sync before page cache failed: %s", tgt_exc)
            result["target_lfs_sync"] = {"ok": False, "error": str(tgt_exc)}
            _done("target_sync", ok=False, detail=str(tgt_exc))

        try:
            _start("page_cache", "Building Home + Sales + Analyzer + Managers cache…")
            log.info("── Auto daily: Home + Sales + Analyzer + Managers page cache ──")
            from api.page_response_cache import warm_default_page_caches

            page_cache = warm_default_page_caches()
            result["page_cache"] = page_cache
            mgr_step = (page_cache.get("steps") or {}).get("store_managers") or {}
            stamp = ((page_cache.get("publish") or {}).get("daily_stamp") or {})
            stamp_cr = stamp.get("stamp", {}).get("home_net_sales_cr") if isinstance(stamp, dict) else None
            _done(
                "page_cache",
                ok=bool(page_cache.get("ok")) and bool(stamp.get("ok", True)),
                detail=(
                    f"{page_cache.get('seconds')}s · managers_ok={mgr_step.get('ok')}"
                    f" · stamp_cr={stamp_cr} · stamp_pub={stamp.get('published')}"
                ),
            )
            log.info(
                "Page cache: steps=%s stamp=%s",
                page_cache.get("steps"),
                stamp,
            )
            if not page_cache.get("ok"):
                log.error("Page cache warm reported ok=false — hosted may stay on prior day")
        except Exception as page_exc:
            log.exception("Home/Sales/Managers page cache failed — retrying critical warms")
            result["page_cache"] = {"ok": False, "error": str(page_exc)}
            # Critical FE surfaces must still land on blob even if full warm import fails.
            retry: dict = {}
            try:
                from api.routes.sales_dashboard import warm_sales_overview_progressive_caches

                retry["sales_progressive"] = warm_sales_overview_progressive_caches(
                    force_refresh=True
                )
            except Exception as retry_exc:
                retry["sales_progressive"] = {"ok": False, "error": str(retry_exc)}
            try:
                from api.routes.dispatch_lead_time import get_dispatch_lead_time_report
                from api.routes.sku_breakeven import get_sku_breakeven_report

                retry["dispatch_lead_time"] = get_dispatch_lead_time_report(
                    search="",
                    alert_filter="all",
                    section="all",
                    season="all",
                    sort_by="inward_date",
                    sort_dir="desc",
                    page=1,
                    page_size=20,
                    refresh=1,
                )
                retry["sku_breakeven"] = get_sku_breakeven_report(
                    search="",
                    risk_filter="all",
                    section="all",
                    season="all",
                    sort_by="dispatch_gap_vs_be",
                    sort_dir="desc",
                    page=1,
                    page_size=50,
                    refresh=1,
                )
            except Exception as inv_exc:
                retry["inventory"] = {"ok": False, "error": str(inv_exc)}
            result["page_cache"]["retry"] = retry
            retry_ok = bool((retry.get("sales_progressive") or {}).get("ok"))
            _done(
                "page_cache",
                ok=retry_ok,
                detail=f"full_warm_failed={page_exc}; retry_progressive={retry_ok}",
            )

        if skip_pipeline:
            result["ok"] = True
            result["finished_at"] = datetime.now(timezone.utc).isoformat()
            artifact_path = _write_run_artifacts(result)
            _skip("pipeline", "skip_pipeline requested")
            log.info("Skip pipeline requested. Artifacts: %s", artifact_path)
            if track_progress:
                from pipeline.daily_run_tracker import finish_run as tracker_finish_run

                tracker_finish_run(result, artifact_path=artifact_path)
            return result

        _start("pipeline", f"Running combine stage ({stage})…")
        log.info("── Auto daily: run pipeline stage=%s ──", stage)

        poll_stop = threading.Event()

        def _poll_pipeline() -> None:
            while not poll_stop.is_set():
                try:
                    job = get_job_state()
                    if job.get("status") == "running":
                        label = job.get("current_label") or job.get("current_step") or "Running…"
                        pct = int(job.get("progress_pct") or 0)
                        _update("pipeline", str(label), sub_pct=pct)
                except Exception:
                    pass
                poll_stop.wait(2.0)

        poller = threading.Thread(target=_poll_pipeline, daemon=True, name="daily-pipeline-poller")
        poller.start()
        try:
            run_stage(stage, sync_blob_inputs=True, warehouse_mode=warehouse_mode)
        finally:
            poll_stop.set()
            poller.join(timeout=3)

        job = get_job_state()
        completed = job.get("completed_steps") or []
        result["pipeline"] = {
            "status": job.get("status"),
            "error": job.get("error"),
            "warnings": job.get("warnings") or [],
            "completed_steps": completed,
        }
        _done(
            "pipeline",
            ok=job.get("status") == "success",
            detail=(
                f"combine done · {len(completed)} step(s)"
                if stage == "combine"
                else f"{len(completed)} step(s) · status={job.get('status')}"
            ),
        )
        for w in job.get("warnings") or []:
            if w and w not in result["warnings"]:
                result["warnings"].append(str(w))

        result["ok"] = job.get("status") == "success"
        if not result["ok"]:
            result["error"] = job.get("error") or f"pipeline status={job.get('status')}"
    except Exception as exc:
        result["ok"] = False
        result["error"] = str(exc)
        result["traceback"] = traceback.format_exc()
        log.exception("auto daily failed")

    result["finished_at"] = datetime.now(timezone.utc).isoformat()
    artifact_path = _write_run_artifacts(result)
    log.info("Auto daily finished ok=%s artifacts=%s", result["ok"], artifact_path)

    if track_progress:
        try:
            from pipeline.daily_run_tracker import finish_run as tracker_finish_run

            tracker_finish_run(result, artifact_path=artifact_path)
        except ImportError:
            pass

    return result


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    p = argparse.ArgumentParser(description="Daily Raw_Files fetch + merge + pipeline")
    p.add_argument("--stage", default="combine", choices=["all", "combine", "refill", "transfer"])
    p.add_argument(
        "--warehouse-mode",
        default="fresh",
        choices=["fresh", "incremental"],
        help="fresh = WH_LEDGER_RESET (recommended after nightly WH dump)",
    )
    p.add_argument("--skip-pipeline", action="store_true", help="Only fetch+merge")
    p.add_argument("--dry-run", action="store_true", help="Preview coverage only")
    args = p.parse_args()
    out = run_auto_daily(
        stage=args.stage,
        warehouse_mode=args.warehouse_mode,
        skip_pipeline=args.skip_pipeline,
        dry_run=args.dry_run,
    )
    print(json.dumps(out, indent=2, default=str))
    raise SystemExit(0 if out.get("ok") else 1)


if __name__ == "__main__":
    main()

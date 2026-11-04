"""
Local zinc pipeline APIs — run batch jobs and download outputs.

Endpoints:
  GET  /api/pipeline/health
  GET  /api/pipeline/status
  GET  /api/pipeline/outputs
  GET  /api/pipeline/stages
  POST /api/pipeline/sync-inputs   # Blob input/ → local input_files
  POST /api/pipeline/merge-inputs  # incoming → history backup → merge → input/ → local sync
  POST /api/pipeline/run?stage=all|combine|refill|transfer
  POST /api/pipeline/stop          # cancel ongoing run
  GET  /api/pipeline/download?name=final_transfer_sizewise.xlsx
"""

from __future__ import annotations

import sys
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

# Ensure pipeline package is importable
_MERGED = Path(__file__).resolve().parents[2]
_PIPELINE = _MERGED / "pipeline"
if str(_PIPELINE) not in sys.path:
    sys.path.insert(0, str(_PIPELINE))
if str(_MERGED) not in sys.path:
    sys.path.insert(0, str(_MERGED))

from pipeline.runner import (  # noqa: E402
    STAGE_MAP,
    get_job_state,
    list_key_outputs,
    request_stop,
    start_stage_async,
)

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])


class StopPipelineBody(BaseModel):
    reason: str | None = Field(
        default=None,
        description="Optional note for why the pipeline was stopped",
    )


@router.get("/health")
def pipeline_health():
    from pipeline_inputs import BASE_DIR, INPUT_DIR, OUTPUT_DIR, get_as_of_date

    scheduler = {}
    soh_source = {}
    try:
        from pipeline.daily_scheduler import get_scheduler_status

        scheduler = get_scheduler_status()
    except Exception as exc:
        scheduler = {"error": str(exc)}
    try:
        from pipeline.soh_reconcile import pick_soh_source, _master_freshness, _channel_freshness

        master_ts, master_label = _master_freshness()
        channel_ts, channel_files = _channel_freshness()
        soh_source = {
            "active_source": pick_soh_source(),
            "master_freshness": master_ts,
            "master_as_of": master_label,
            "channel_freshness": channel_ts,
            "channel_files": channel_files,
        }
    except Exception as exc:
        soh_source = {"error": str(exc)}

    return {
        "status": "ok",
        "as_of_date_helper": get_as_of_date(),
        "pipeline_data_root": str(BASE_DIR),
        "input_dir_exists": INPUT_DIR.exists(),
        "output_dir_exists": OUTPUT_DIR.exists(),
        "blob_input_sync": "enabled (PIPELINE_SYNC_BLOB_INPUTS=1 by default)",
        "daily_scheduler": scheduler,
        "soh_source": soh_source,
    }


@router.get("/stages")
def pipeline_stages():
    return {
        "stages": {
            name: [{"script": s, "label": l} for s, l in steps]
            for name, steps in STAGE_MAP.items()
        }
    }


@router.get("/status")
def pipeline_status():
    return get_job_state()


@router.get("/outputs")
def pipeline_outputs():
    return {"files": list_key_outputs()}


@router.post("/sync-inputs")
def pipeline_sync_inputs(dry_run: int = Query(0, description="1 = list only, no download")):
    """Download Azure Blob `input/` into local `input_files/` (+ old_input_files)."""
    try:
        from pipeline.sync_blob_inputs import sync_inputs_from_blob

        return sync_inputs_from_blob(dry_run=bool(dry_run))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/merge-inputs")
def pipeline_merge_inputs(
    dry_run: int = Query(0, description="1 = plan only, no writes"),
    keep_incoming: int = Query(0, description="1 = keep files in input/incoming/ after merge"),
    sync_local: int = Query(1, description="1 = also download final input/ to local input_files/"),
):
    """
    Full Blob input merge:
      1) Backup matching input/<file> → input/history/<timestamp>/
      2) Merge input/incoming/<file> into input/<file> (append + full-row dedupe for csv/xlsx)
      3) Write final file to input/
      4) Optionally clear incoming
      5) Sync input/ → pipeline/input_files/

    Incoming filename must match the existing input/ filename to append-merge.
    """
    try:
        from pipeline.merge_blob_inputs import merge_incoming_to_input

        return merge_incoming_to_input(
            dry_run=bool(dry_run),
            keep_incoming=bool(keep_incoming),
            sync_local=bool(sync_local),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/run")
def pipeline_run(
    stage: str = Query("all", description="combine | refill | transfer | all"),
    skip_blob_sync: int = Query(0, description="1 = use local files only, skip Blob download"),
    warehouse_mode: str = Query(
        "incremental",
        description="incremental = use remaining WH ledger; fresh = reset and use full WH register",
    ),
    sku_scope: str = Query(
        "all",
        description="all = every SKU; remapped = only SKUs in SKU_REMAP.xlsx",
    ),
    start_from: str | None = Query(
        None,
        description="Resume from this script, e.g. 6.build_refill.py",
    ),
):
    stage = (stage or "all").strip().lower()
    if stage not in STAGE_MAP:
        raise HTTPException(status_code=400, detail=f"Invalid stage. Use one of: {list(STAGE_MAP)}")
    wh_mode = (warehouse_mode or "incremental").strip().lower()
    if wh_mode not in ("incremental", "fresh"):
        raise HTTPException(status_code=400, detail="Invalid warehouse_mode. Use: incremental | fresh")
    sc = (sku_scope or "all").strip().lower()
    if sc not in ("all", "remapped", "remap", "renamed"):
        raise HTTPException(status_code=400, detail="Invalid sku_scope. Use: all | remapped")
    if sc in ("remapped", "remap", "renamed"):
        sc = "remapped"
        try:
            from sku_scope import remapped_sku_names
        except ImportError:
            from pipeline.sku_scope import remapped_sku_names
        names = remapped_sku_names()
        if not names:
            raise HTTPException(
                status_code=400,
                detail="No SKU Remap mapping found. Upload SKU Remap (OLD SKU / NEW SKU) first.",
            )

    try:
        return start_stage_async(
            stage,
            sync_blob_inputs=not bool(skip_blob_sync),
            warehouse_mode=wh_mode,
            sku_scope=sc,
            start_from=start_from or None,
        )
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/stop")
def pipeline_stop(body: StopPipelineBody | None = None):
    """Stop the currently running pipeline (kills active step process)."""
    reason = body.reason if body else None
    result = request_stop(reason)
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result.get("message") or "Nothing to stop")
    return result


@router.get("/daily-run/status")
def daily_run_status():
    """Live status of scheduled / manual daily pipeline (fetch → merge → full run)."""
    try:
        from pipeline.daily_run_tracker import get_status
        from pipeline.daily_scheduler import get_scheduler_status

        return {
            "ok": True,
            "scheduler": get_scheduler_status(),
            **get_status(),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/daily-run/trigger")
def daily_run_trigger():
    """Run the scheduled daily job now (background) — for testing/recovery."""
    try:
        from pipeline.daily_scheduler import trigger_daily_run

        out = trigger_daily_run(trigger="manual")
        if not out.get("ok"):
            raise HTTPException(status_code=409, detail=out.get("error") or "Already running")
        return out
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/download")
def pipeline_download(name: str = Query(..., description="Output file name")):
    name = Path(name).name  # prevent path traversal
    files = {f["name"]: f for f in list_key_outputs()}
    meta = files.get(name)
    if not meta or not meta["exists"]:
        raise HTTPException(status_code=404, detail=f"File not found: {name}")
    path = Path(meta["path"])
    return FileResponse(path, filename=path.name)

"""
Home-tab file upload → Azure Blob category folders under input/.

POST /api/upload
  multipart: files[] + file_categories[] (paired)
  also accepts files_<category-id> fields from the UI

Maps frontend category ids → Blob folders created under input/:
  e.g. ebo-soh → input/EBO - SOH/<filename>
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from azure.storage.blob import ContentSettings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["upload"])

# Frontend destination id → exact Blob folder name under input/
CATEGORY_FOLDER_MAP: Dict[str, str] = {
    "financial-week-info": "Financial Week Info",
    "sales-master-pt-ssl-centro-iconic": "Sales-Master (PT, SSL, Centro, Iconic, EBO)",
    "sales-pt-ssl-centro-iconic": "Sales (PT, SSL, Centro, Iconic, EBO)",
    "ebo-sales": "EBO Sales",
    "pantaloons-soh": "Pantaloons - SOH",
    "shoppers-stop-soh": "Shoppers Stop - SOH",
    "iconic-soh": "Iconic - SOH",
    "ebo-soh": "EBO - SOH",
    "centro-soh": "Centro - SOH",
    "warehouse-soh": "Warehouse SOH",
    "transfer-ebo": "Transfer - EBO",
    "transfer-pt-ssl-centro-iconic": "Transfer - (PT, SSL, Centro, Iconic)",
    "transfer-master-pt-ssl-centro-iconic": "Transfer-Master (PT, SSL, Centro, Iconic)",
    "item-master": "Item Master",
    "customer-master": "Customer Master",
    "store-grade-list": "Store Grade List",
    "top-sellers-list": "Top Sellers List",
    "sku-remap": "SKU Remap",
    "combined-soh": "Combined SOH",
    "soh-master": "SOH-Master",
    "target-lfs": "Target_LFS",
    "rtv-data": "RTV Data",
    "ibt-data": "IBT Data",
    "grc-report": "GRC Report",
    # legacy ids (older UI) → combined sales folder
    "pantaloons-sales": "Sales (PT, SSL, Centro, Iconic, EBO)",
    "shoppers-stop-sales": "Sales (PT, SSL, Centro, Iconic, EBO)",
    "iconic-sales": "Sales (PT, SSL, Centro, Iconic, EBO)",
    "centro-sales": "Sales (PT, SSL, Centro, Iconic, EBO)",
    "sales-pt-ebo-centro-iconic": "Sales (PT, SSL, Centro, Iconic, EBO)",
}


def _env(*keys: str, default: str = "") -> str:
    for k in keys:
        v = os.environ.get(k)
        if v:
            return v
    return default


# Starlette default max_part_size is 1MB — raise for large Excel inputs.
# Override with UPLOAD_MAX_MB in .env (default 500).
def _max_part_bytes() -> int:
    try:
        mb = int(_env("UPLOAD_MAX_MB", default="500") or "500")
    except ValueError:
        mb = 500
    return max(1, mb) * 1024 * 1024


def _norm_prefix(prefix: str) -> str:
    p = (prefix or "").strip().replace("\\", "/")
    if p and not p.endswith("/"):
        p += "/"
    return p.lstrip("/")


def _safe_filename(name: str) -> str:
    base = Path(name).name  # strip any path traversal
    base = base.replace("\\", "/").split("/")[-1]
    if not base or base in (".", "..") or base.startswith("~$"):
        raise ValueError(f"Invalid filename: {name!r}")
    # block absolute weirdness
    if re.search(r"[\x00-\x1f]", base):
        raise ValueError(f"Invalid filename characters: {name!r}")
    return base


def _guess_content_type(name: str) -> str:
    lower = name.lower()
    if lower.endswith(".csv"):
        return "text/csv"
    if lower.endswith(".xlsx"):
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if lower.endswith(".xls"):
        return "application/vnd.ms-excel"
    if lower.endswith(".json"):
        return "application/json"
    if lower.endswith(".parquet"):
        return "application/octet-stream"
    return "application/octet-stream"


def _get_container():
    try:
        from azure.storage.blob import BlobServiceClient
    except ImportError as e:
        raise RuntimeError("Install azure-storage-blob: pip install azure-storage-blob") from e

    conn = _env("AZURE_STORAGE_CONNECTION_STRING")
    if not conn:
        raise RuntimeError("Set AZURE_STORAGE_CONNECTION_STRING in .env")

    container_name = _env("AZURE_STORAGE_CONTAINER", default="zinc-data")
    svc = BlobServiceClient.from_connection_string(conn)
    cc = svc.get_container_client(container_name)
    if not cc.exists():
        raise RuntimeError(f"Blob container not found: {container_name}")
    return cc, container_name, _norm_prefix(_env("AZURE_BLOB_INPUT_PREFIX", default="input/"))


async def _load_form(request: Request):
    """
    Parse multipart form with a raised part-size limit when the installed
    Starlette supports max_part_size; otherwise fall back cleanly.
    """
    import inspect

    max_size = _max_part_bytes()
    form_fn = request.form
    kwargs: Dict[str, Any] = {}
    try:
        params = inspect.signature(form_fn).parameters
        if "max_part_size" in params:
            kwargs["max_part_size"] = max_size
        if "max_files" in params:
            kwargs["max_files"] = 2000
        if "max_fields" in params:
            kwargs["max_fields"] = 2000
    except (TypeError, ValueError):
        params = {}

    try:
        return await form_fn(**kwargs)
    except TypeError:
        # Older / wrapped Request.form without max_part_size
        return await form_fn()


async def _collect_uploads(request: Request) -> List[Tuple[str, str, bytes]]:
    """
    Returns list of (category_id, filename, content_bytes).
    Supports:
      - files + file_categories (paired lists)  [preferred]
      - files_<category_id> fields              [fallback]
    """
    form = await _load_form(request)
    items: List[Tuple[str, str, bytes]] = []

    # 1) Pair generic files[] with file_categories[] (preferred — avoids double upload)
    files = form.getlist("files")
    cats = form.getlist("file_categories")
    if files and cats:
        for f, cat in zip(files, cats):
            if not hasattr(f, "read"):
                continue
            cat_id = str(cat).strip()
            data = await f.read()
            items.append((cat_id, getattr(f, "filename", None) or "upload.bin", data))
        return items

    # 2) Fallback: files_<category_id>
    for key in form.keys():
        if not key.startswith("files_"):
            continue
        cat_id = key[len("files_") :]
        for f in form.getlist(key):
            if not hasattr(f, "read"):
                continue
            fname = getattr(f, "filename", None) or "upload.bin"
            data = await f.read()
            items.append((cat_id, fname, data))

    return items


@router.get("/upload/categories")
def list_upload_categories():
    """List category id → Blob folder mapping."""
    input_prefix = _norm_prefix(_env("AZURE_BLOB_INPUT_PREFIX", default="input/"))
    return {
        "container": _env("AZURE_STORAGE_CONTAINER", default="zinc-data"),
        "input_prefix": input_prefix,
        "categories": [
            {"id": cid, "folder": folder, "blob_path": f"{input_prefix}{folder}/"}
            for cid, folder in CATEGORY_FOLDER_MAP.items()
            if cid
            not in (
                "sales-pt-ebo-centro-iconic",
                "pantaloons-sales",
                "shoppers-stop-sales",
                "iconic-sales",
                "centro-sales",
            )
        ],
    }


@router.get("/upload/merge-status")
def upload_merge_status(job_id: str = Query(..., description="Merge job id from POST /upload")):
    """Poll background merge started by POST /upload (wait_merge=0)."""
    try:
        from pipeline.merge_jobs import get_merge_job
    except ImportError:
        from merge_jobs import get_merge_job

    job = get_merge_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Unknown merge job: {job_id}")
    payload = job.to_dict()
    payload["ok"] = job.status == "success" and bool((job.result or {}).get("ok"))
    return payload


@router.get("/upload/blob-preview")
def upload_blob_preview():
    """List Ginesys Raw_Files and which ones would be fetched (incremental)."""
    try:
        from pipeline.ginesys_raw import preview_raw_fetch
    except ImportError:
        from ginesys_raw import preview_raw_fetch
    try:
        return preview_raw_fetch()
    except Exception as exc:
        logger.exception("blob preview failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/upload/blob-fetch")
async def upload_blob_fetch(
    dry_run: int = Query(0, description="1 = preview copy only"),
    wait_merge: int = Query(0, description="0 = background merge, same as file upload"),
    sync_local: int = Query(1),
):
    """
    Copy planned Raw_Files into category incoming/, then merge into current/
    using the same merge job as manual upload.
    """
    try:
        from pipeline.ginesys_raw import fetch_raw_to_incoming
    except ImportError:
        from ginesys_raw import fetch_raw_to_incoming
    try:
        from pipeline.merge_jobs import start_merge_async
    except ImportError:
        from merge_jobs import start_merge_async
    from pipeline.merge_category_inputs import merge_all_categories, sync_current_to_local

    try:
        copied = await asyncio.to_thread(fetch_raw_to_incoming, dry_run=bool(dry_run))
    except Exception as exc:
        logger.exception("blob fetch failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if dry_run:
        return {**copied, "merge_started": False}

    touched = list(copied.get("touched_categories") or [])
    if not touched:
        return {
            **copied,
            "merge_started": False,
            "message": "No new Raw_Files to fetch (all skipped as already in current or not latest).",
        }

    if wait_merge:
        merge_result = await asyncio.to_thread(
            merge_all_categories,
            dry_run=False,
            keep_incoming=False,
            only_ids=touched,
        )
        if merge_result.get("ok") and sync_local:
            actually = [
                r.get("category_id")
                for r in (merge_result.get("merged") or [])
                if r.get("category_id") and not r.get("skipped")
            ]
            if actually:
                merge_result["local_sync"] = await asyncio.to_thread(
                    sync_current_to_local, dry_run=False, only_ids=actually
                )
        return {**copied, "merge_started": False, "merge": merge_result}

    job = start_merge_async(touched, sync_local=bool(sync_local))
    return {
        **copied,
        "merge_started": True,
        "job_id": job.job_id,
        "status": job.status,
        "hint": "Poll GET /api/upload/merge-status?job_id=... then Confirm / Run pipeline as usual.",
    }


@router.post("/upload")
async def upload_to_blob_categories(
    request: Request,
    sync_local: int = Query(1, description="1 = sync current/ → local input_files after merge"),
    wait_merge: int = Query(
        0,
        description="1 = block until merge finishes (legacy); 0 = background merge + poll",
    ),
):
    """
    Upload Home-tab files into category incoming/, then merge into current/.

    Per category:
      1) If incoming/ already has files → move them to history/<ts>/incoming_prev/
      2) Write new upload(s) to incoming/
      3) Merge incoming + current → current/ (date-overlap or replace)
      4) Clear incoming; optionally sync current/ → local input_files/

    Pipeline run is separate (store Confirm).
    Pass sync_local=0 for intermediate multi-file uploads (UI progress); last call uses 1.
    """
    try:
        uploads = await _collect_uploads(request)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid multipart form: {exc}") from exc

    if not uploads:
        raise HTTPException(
            status_code=400,
            detail="No files received. Send files[] + file_categories[] or files_<category-id>.",
        )

    try:
        cc, container_name, input_prefix = _get_container()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    from pipeline.merge_category_inputs import (
        _upload,
        archive_incoming_to_history,
        clear_incoming_bytes_cache,
        merge_all_categories,
        stash_incoming_bytes,
        sync_current_to_local,
    )
    try:
        from pipeline.fixed_filenames import fixed_name_for
    except ImportError:
        from fixed_filenames import fixed_name_for

    uploaded: List[Dict[str, Any]] = []
    errors: List[str] = []
    archived: List[Dict[str, Any]] = []
    touched_categories: List[str] = []

    # Group uploads by category so we archive incoming once per category
    by_cat: Dict[str, List[Tuple[str, bytes, str]]] = {}
    for cat_id, raw_name, data in uploads:
        folder = CATEGORY_FOLDER_MAP.get(cat_id)
        if not folder:
            errors.append(f"{raw_name}: unknown category id '{cat_id}'")
            continue
        try:
            original = _safe_filename(raw_name)  # validate original name only
            # Keep .csv when client uploads CSV (or content is CSV) — never
            # store CSV bytes under .xlsx (openpyxl → "File is not a zip file").
            fname = fixed_name_for(cat_id, original_name=original, data=data)
        except (ValueError, KeyError) as e:
            errors.append(str(e))
            continue
        if not data:
            errors.append(f"{fname}: empty file skipped")
            continue
        by_cat.setdefault(cat_id, []).append((fname, data, Path(raw_name).name))

    for cat_id, files in by_cat.items():
        folder = CATEGORY_FOLDER_MAP[cat_id]
        # 1) Old incoming → history
        try:
            arch = archive_incoming_to_history(folder, dry_run=False)
            archived.append(arch)
            if not arch.get("ok"):
                errors.extend(arch.get("errors") or [])
        except Exception as e:
            errors.append(f"archive {folder}: {e}")
            logger.exception("archive incoming failed %s", folder)

        # 2) New files → incoming/ with FIXED category filename (last wins if multi)
        for fname, data, original_name in files:
            blob_name = f"{input_prefix}{folder}/incoming/{fname}"
            stash_incoming_bytes(blob_name, data)
            azure_synced = False
            try:
                _upload(cc, blob_name, data, _guess_content_type(fname))
                azure_synced = True
                logger.info(
                    "Uploaded %s → %s as %s (%s bytes)",
                    original_name,
                    blob_name,
                    fname,
                    len(data),
                )
            except Exception as e:
                logger.warning(
                    "Azure blob upload failed for %s (%s); proceeding with stashed data for merge",
                    blob_name,
                    e,
                )

            uploaded.append(
                {
                    "category_id": cat_id,
                    "folder": folder,
                    "original_filename": original_name,
                    "filename": fname,
                    "blob": blob_name,
                    "bytes": len(data),
                    "azure_synced": azure_synced,
                }
            )
        touched_categories.append(cat_id)

    # 3) Merge only touched categories → current/
    merge_result = None
    merge_job = None
    try:
        from pipeline.merge_jobs import get_merge_job, start_merge_async
    except ImportError:
        from merge_jobs import get_merge_job, start_merge_async

    if not touched_categories:
        merge_result = {"ok": True, "skipped": True, "reason": "no_categories"}
    elif wait_merge:
        try:
            t0 = time.perf_counter()
            logger.info("Merge starting for categories: %s", touched_categories)
            merge_result = await asyncio.to_thread(
                merge_all_categories,
                dry_run=False,
                keep_incoming=False,
                only_ids=touched_categories,
            )
            logger.info(
                "Merge finished in %.1fs (ok=%s merged=%s skipped=%s failed=%s)",
                time.perf_counter() - t0,
                merge_result.get("ok"),
                merge_result.get("merged_count"),
                merge_result.get("skipped_count"),
                merge_result.get("failed_count"),
            )
            if merge_result.get("ok"):
                actually_merged = [
                    r.get("category_id")
                    for r in (merge_result.get("merged") or [])
                    if r.get("category_id") and not r.get("skipped")
                ]
                if sync_local and actually_merged:
                    t1 = time.perf_counter()
                    merge_result["local_sync"] = await asyncio.to_thread(
                        sync_current_to_local,
                        dry_run=False,
                        only_ids=actually_merged,
                    )
                    logger.info(
                        "Local sync finished in %.1fs (categories=%s)",
                        time.perf_counter() - t1,
                        actually_merged,
                    )
                elif sync_local:
                    merge_result["local_sync"] = {
                        "skipped": True,
                        "reason": "all_merges_skipped_current_unchanged",
                    }
                    logger.info("Local sync skipped — current/ unchanged (dates already present)")
                else:
                    merge_result["local_sync"] = {"skipped": True, "reason": "sync_local=0"}
            else:
                failed = merge_result.get("failed") or []
                detail_bits = []
                for f in failed:
                    detail_bits.append(
                        f"{f.get('category_id')}: {'; '.join(f.get('errors') or [f.get('reason') or 'merge failed'])}"
                    )
                errors.append("merge failed: " + " | ".join(detail_bits) if detail_bits else "merge failed")
        except Exception as e:
            errors.append(f"merge: {e}")
            logger.exception("merge after upload failed")
        finally:
            clear_incoming_bytes_cache()
    else:
        # Fast response: merge runs in background; client polls /upload/merge-status
        merge_job = start_merge_async(touched_categories, sync_local=bool(sync_local))
        merge_result = {
            "ok": True,
            "status": "running",
            "job_id": merge_job.job_id,
            "category_ids": touched_categories,
            "hint": "Poll GET /api/upload/merge-status?job_id=... until status is success or failed",
        }
        logger.info(
            "Background merge queued job=%s categories=%s sync_local=%s",
            merge_job.job_id,
            touched_categories,
            sync_local,
        )

    ok = len(uploaded) > 0 and not errors
    if wait_merge:
        ok = (
            ok
            and bool(merge_result and merge_result.get("ok"))
            and not any(str(e).startswith("merge") for e in errors)
        )
    elif merge_job is not None:
        ok = ok and merge_result.get("status") == "running"

    payload = {
        "ok": ok,
        "container": container_name,
        "input_prefix": input_prefix,
        "uploaded_count": len(uploaded),
        "uploaded": uploaded,
        "archived_previous_incoming": archived,
        "merge": merge_result,
        "merge_job_id": merge_job.job_id if merge_job else None,
        "warnings": list(merge_result.get("warnings") or []) if merge_result else [],
        "errors": errors,
        "hint": (
            "Upload accepts CSV or Excel. Files rename to fixed category names "
            "(CSV stays .csv in incoming/; current/ is normalized to canonical .xlsx). "
            "Previous incoming → history/; merge → current/. "
            "Store Confirm only saves store types + starts pipeline."
        ),
    }
    if not ok:
        raise HTTPException(status_code=400, detail=payload)
    if merge_job is not None:
        return JSONResponse(status_code=202, content=payload)
    return payload


@router.post("/upload/merge-inputs")
def merge_category_inputs(
    dry_run: int = Query(0),
    keep_incoming: int = Query(0),
    sync_local: int = Query(1),
):
    """
    For each category: if incoming/ has files → backup current → merge/replace → current/.
    Categories with empty incoming are skipped (existing current kept).
    """
    try:
        from pipeline.merge_category_inputs import merge_all_categories, sync_current_to_local

        result = merge_all_categories(
            dry_run=bool(dry_run),
            keep_incoming=bool(keep_incoming),
        )
        if sync_local and not dry_run and result.get("ok"):
            result["local_sync"] = sync_current_to_local(dry_run=False)
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ── Post-upload: choose which of the 5 store channels to replenish ───────────
REPLENISHMENT_STORE_OPTIONS: List[Dict[str, str]] = [
    {"id": "pantaloons", "label": "Pantaloons (PT)"},
    {"id": "shoppers-stop", "label": "Shoppers Stop (SSL)"},
    {"id": "centro", "label": "Centro"},
    {"id": "iconic", "label": "Iconic"},
    {"id": "ebo", "label": "EBO"},
]
_VALID_STORE_IDS = {o["id"] for o in REPLENISHMENT_STORE_OPTIONS}


@router.get("/upload/replenishment-stores")
def list_replenishment_stores():
    """Return the 5 store channels available for replenishment selection."""
    try:
        from pipeline_inputs import load_replenishment_store_selection
    except ImportError:
        from pipeline.pipeline_inputs import load_replenishment_store_selection

    current = load_replenishment_store_selection()
    return {"stores": REPLENISHMENT_STORE_OPTIONS, "current": current}


@router.post("/upload/replenishment-stores")
async def save_replenishment_stores_local(request: Request):
    """
    Save selected replenishment store channels to local disk for pipeline input.
    File: pipeline/input_files/replenishment_stores.json  (NOT Blob)

    Body: {
      "stores": ["pantaloons", "ebo", ...],
      "start_pipeline": true,
      "warehouse_mode": "incremental" | "fresh",
      "sku_scope": "all" | "remapped",
      "skip_blob_sync": true,
      "start_from": "combine_soh.py",
    }
    When start_pipeline is true (default), kicks off pipeline stage=all.
    sku_scope=remapped refills only SKUs from SKU_REMAP.xlsx (OLD → NEW).
    """
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON body: {exc}") from exc

    raw = body.get("stores") if isinstance(body, dict) else None
    if not isinstance(raw, list) or not raw:
        raise HTTPException(status_code=400, detail="Send stores: string[] with at least one id")

    selected = []
    for s in raw:
        sid = str(s).strip().lower()
        if sid not in _VALID_STORE_IDS:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown store id '{s}'. Valid: {sorted(_VALID_STORE_IDS)}",
            )
        if sid not in selected:
            selected.append(sid)

    if bool(body.get("start_pipeline", True)):
        from pipeline.runner import get_job_state
        if get_job_state().get("status") == "running":
            raise HTTPException(status_code=409, detail="Pipeline is already running. Wait for it to finish, then confirm the selected stores again.")

    labels = {o["id"]: o["label"] for o in REPLENISHMENT_STORE_OPTIONS}
    try:
        try:
            from pipeline_inputs import save_replenishment_store_selection
        except ImportError:
            from pipeline.pipeline_inputs import save_replenishment_store_selection

        saved = save_replenishment_store_selection(
            selected,
            labels=[labels[s] for s in selected],
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Local save failed: {exc}") from exc

    # Merge already happens on Upload. Confirm only starts pipeline with store types.
    start_pipeline = True
    if isinstance(body, dict) and "start_pipeline" in body:
        start_pipeline = bool(body.get("start_pipeline"))

    warehouse_mode = "incremental"
    if isinstance(body, dict) and body.get("warehouse_mode") is not None:
        wm = str(body.get("warehouse_mode")).strip().lower()
        if wm not in ("incremental", "fresh"):
            raise HTTPException(
                status_code=400,
                detail="Invalid warehouse_mode. Use: incremental | fresh",
            )
        warehouse_mode = wm

    sku_scope = "all"
    if isinstance(body, dict) and body.get("sku_scope") is not None:
        sc = str(body.get("sku_scope")).strip().lower()
        if sc not in ("all", "remapped", "remap", "renamed"):
            raise HTTPException(
                status_code=400,
                detail="Invalid sku_scope. Use: all | remapped",
            )
        sku_scope = "remapped" if sc in ("remapped", "remap", "renamed") else "all"

    if sku_scope == "remapped":
        try:
            from sku_scope import remapped_sku_names
        except ImportError:
            from pipeline.sku_scope import remapped_sku_names
        if not remapped_sku_names():
            raise HTTPException(
                status_code=400,
                detail="No SKU Remap mapping found. Upload SKU Remap (OLD SKU / NEW SKU) first.",
            )

    pipeline_job = None
    if start_pipeline:
        try:
            from pipeline.runner import start_stage_async

            skip_blob = False
            if isinstance(body, dict) and body.get("skip_blob_sync") is not None:
                skip_blob = bool(body.get("skip_blob_sync"))
            start_from = None
            if isinstance(body, dict) and body.get("start_from"):
                start_from = str(body.get("start_from")).strip() or None

            pipeline_job = start_stage_async(
                "all",
                sync_blob_inputs=not skip_blob,
                warehouse_mode=warehouse_mode,
                sku_scope=sku_scope,
                start_from=start_from,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Stores saved, but pipeline failed to start: {exc}",
            ) from exc

    if start_pipeline and not (pipeline_job or {}).get("ok"):
        raise HTTPException(status_code=409, detail=(pipeline_job or {}).get("message") or "Stores saved, but pipeline did not start. Please retry after the current run finishes.")

    return {
        "ok": True,
        "saved_to_blob": False,
        "saved_local": True,
        "path": saved.get("path"),
        "stores": saved.get("stores"),
        "store_types": saved.get("store_types"),
        "labels": saved.get("labels"),
        "pipeline_started": bool(start_pipeline and (pipeline_job or {}).get("ok")),
        "warehouse_mode": warehouse_mode,
        "sku_scope": sku_scope,
        "pipeline": pipeline_job,
        "hint": "Upload already merged incoming→current. Confirm only saves stores + starts pipeline.",
    }

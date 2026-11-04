"""
After blob category merge: fold daily sales/transfer into masters, then refresh
historical weekly/monthly SOH for comparison.

Wired from:
  - pipeline.auto_daily_run (nightly)
  - pipeline.merge_jobs (Home upload / blob-fetch merge)
"""

from __future__ import annotations

import logging
import os
import threading
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

log = logging.getLogger("pipeline.post_merge_refresh")

PIPELINE_DIR = Path(__file__).resolve().parent
BACKEND_DIR = PIPELINE_DIR.parent

# Daily Ginesys dumps → long-lived masters (date_merge)
DAILY_TO_MASTER: Dict[str, str] = {
    "sales-pt-ssl-centro-iconic": "sales-master-pt-ssl-centro-iconic",
    "transfer-pt-ssl-centro-iconic": "transfer-master-pt-ssl-centro-iconic",
}


def _env_flag(name: str, default: str = "1") -> bool:
    return (os.environ.get(name, default) or default).strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _soh_end_date() -> str:
    """Rolling end = yesterday (IST-friendly: use local date − 1)."""
    return (date.today() - timedelta(days=1)).isoformat()


def _soh_start_date() -> str:
    return (os.environ.get("HISTORICAL_SOH_START") or "2025-04-01").strip()


def _collect_newer_delta_frames(
    cc,
    input_prefix: str,
    daily_id: str,
    master_cfg: Dict[str, Any],
    *,
    cut: "pd.Timestamp",
    year: int,
) -> tuple[list, list[str], list[str], str]:
    """Rows after cut for this calendar year.

    Prefer Raw_Files still sitting in the drop zone; if those were already
    merged/cleared into daily current/, fall back to daily current itself so
    masters catch up alongside (not stay stuck on an older max_str).
    """
    import pandas as pd

    from pipeline.date_merge import find_date_column, read_excel_smart, to_day_series
    from pipeline.ginesys_raw import (
        ginesys_category,
        is_history_or_bulk_dump,
        parse_file_date,
    )
    from pipeline.merge_category_inputs import (
        CATEGORY_MERGE_CONFIG,
        _category_prefixes,
        _download,
        _list_files,
    )

    daily_cfg = CATEGORY_MERGE_CONFIG.get(daily_id) or {}
    date_candidates = daily_cfg.get("date_candidates") or master_cfg.get("date_candidates") or ["Date"]
    frames: list = []
    used_files: List[str] = []
    used_dates: List[str] = []
    source = "raw_files"

    for blob in cc.list_blobs(name_starts_with="Raw_Files/"):
        name = Path(blob.name).name
        if ginesys_category(name) != daily_id or is_history_or_bulk_dump(name):
            continue
        fd = parse_file_date(name)
        if fd is None or fd <= cut.date():
            continue
        data = cc.download_blob(blob.name).readall()
        frame, _meta = read_excel_smart(
            data,
            name,
            date_candidates=date_candidates,
            preferred_header=int(daily_cfg.get("header_row") or 0),
            preferred_sheet=daily_cfg.get("sheet_name"),
            column_hints=daily_cfg.get("column_hints"),
        )
        col = find_date_column(frame, date_candidates)
        days = to_day_series(frame[col])
        keep = days.notna() & (days > cut) & (days.dt.year == year)
        part = frame.loc[keep].copy()
        if part.empty:
            continue
        frames.append(part)
        used_files.append(name)
        if fd.year == year:
            used_dates.append(fd.isoformat())

    if frames:
        return frames, used_files, used_dates, source

    # Fallback: daily current already has the new days (Raw_Files cleared after merge).
    source = "daily_current"
    daily_folder = str(daily_cfg.get("folder") or "").strip()
    daily_canonical = str(daily_cfg.get("canonical_name") or "").strip()
    if not daily_folder or not daily_canonical:
        return [], [], [], source
    daily_paths = _category_prefixes(input_prefix, daily_folder)
    stem = Path(daily_canonical).stem.lower()
    for blob in _list_files(cc, daily_paths["current"]):
        bname = Path(blob.name).name
        if bname.lower() != daily_canonical.lower() and Path(bname).stem.lower() != stem:
            continue
        try:
            data = _download(cc, blob.name)
            frame, _meta = read_excel_smart(
                data,
                bname,
                date_candidates=date_candidates,
                preferred_header=int(daily_cfg.get("header_row") or 0),
                preferred_sheet=daily_cfg.get("sheet_name"),
                column_hints=daily_cfg.get("column_hints"),
            )
        except Exception as exc:
            log.warning("daily current read failed for master fold %s: %s", bname, exc)
            continue
        col = find_date_column(frame, date_candidates)
        if not col:
            continue
        days = to_day_series(frame[col])
        keep = days.notna() & (days > cut) & (days.dt.year == year)
        part = frame.loc[keep].copy()
        if part.empty:
            continue
        frames.append(part)
        used_files.append(f"daily:{bname}")
        used_dates.append(str(days.loc[keep].max().date()))
        break

    return frames, used_files, used_dates, source


def _dedupe_delta_frame(delta: "pd.DataFrame") -> "pd.DataFrame":
    key_names = {
        "invoice no",
        "invoice number",
        "barcode",
        "invoice date",
        "qty",
        "quantity",
        "item net amt",
    }
    keys = [c for c in delta.columns if str(c).strip().lower() in key_names]
    if len(keys) >= 2:
        return delta.drop_duplicates(subset=keys)
    return delta


def _stage_latest_year_delta(
    cc,
    input_prefix: str,
    daily_id: str,
    master_cfg: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Put only this calendar year's new days into <master>/<year>/incoming/.

    2024 and 2025 folders are never opened. Parent incoming leftovers
    (full-history dumps) are deleted so they cannot rewrite the old master.
    """
    import io

    import pandas as pd

    from pipeline.date_merge import find_date_column, to_day_series
    from pipeline.merge_category_inputs import (
        CATEGORY_MERGE_CONFIG,
        _category_prefixes,
        _delete,
        _list_files,
        _load_date_bounds_meta,
        _upload,
    )
    from pipeline.year_stores import live_calendar_year

    year = live_calendar_year()
    base = master_cfg["folder"]
    live_folder = f"{base}/{year}"
    live_paths = _category_prefixes(input_prefix, live_folder)
    parent_paths = _category_prefixes(input_prefix, base)
    canonical = master_cfg.get("canonical_name") or "master.csv"

    for blob in _list_files(cc, parent_paths["incoming"]):
        log.info("Drop parent incoming leftover %s", blob.name)
        _delete(cc, blob.name)

    meta = _load_date_bounds_meta(cc, live_paths["current"], canonical) or {}
    max_str = str(meta.get("max_str") or "")[:10]
    if not max_str:
        return {
            "ok": False,
            "skipped": True,
            "reason": "live_year_folder_missing",
            "live_folder": live_folder,
        }

    cut = pd.Timestamp(max_str)
    daily_cfg = CATEGORY_MERGE_CONFIG.get(daily_id) or {}
    date_candidates = daily_cfg.get("date_candidates") or master_cfg.get("date_candidates") or ["Date"]
    frames, used_files, used_dates, source = _collect_newer_delta_frames(
        cc, input_prefix, daily_id, master_cfg, cut=cut, year=year
    )

    for blob in _list_files(cc, live_paths["incoming"]):
        _delete(cc, blob.name)

    if not frames:
        return {
            "ok": True,
            "skipped": True,
            "reason": "no_newer_rows_for_live_year",
            "live_folder": live_folder,
            "current_max": max_str,
            "source": source,
        }

    delta = _dedupe_delta_frame(pd.concat(frames, ignore_index=True))
    buf = io.StringIO()
    delta.to_csv(buf, index=False)
    new_max = str(to_day_series(delta[find_date_column(delta, date_candidates)]).max().date())
    dest_name = f"{Path(canonical).stem}_delta_{new_max}.csv"
    dest_blob = f"{live_paths['incoming']}{dest_name}"
    _upload(cc, dest_blob, buf.getvalue().encode("utf-8"), "text/csv")
    log.info(
        "Staged %s rows for %s from %s (%s)",
        len(delta),
        live_folder,
        ", ".join(used_files),
        source,
    )
    return {
        "ok": True,
        "skipped": False,
        "live_folder": live_folder,
        "rows": int(len(delta)),
        "files": used_files,
        "file_dates": sorted(set(used_dates)),
        "incoming": dest_blob,
        "current_max": max_str,
        "source": source,
    }


def _stage_parent_master_delta(
    cc,
    input_prefix: str,
    daily_id: str,
    master_cfg: Dict[str, Any],
    master_id: str,
) -> Dict[str, Any]:
    """Stage rows newer than master current/ so that file catches up.

    Sources: Raw_Files first, else daily current/ (so morning master fold works
    even after Raw_Files were cleared into the daily category).
    Only days after the master's own max date are staged — full daily copy
    overlaps and date_merge will not append.
    """
    import io

    import pandas as pd

    from pipeline.date_merge import find_date_column, to_day_series
    from pipeline.ginesys_raw import current_max_date
    from pipeline.merge_category_inputs import (
        CATEGORY_MERGE_CONFIG,
        _category_prefixes,
        _delete,
        _list_files,
        _load_date_bounds_meta,
        _upload,
    )
    from pipeline.year_stores import live_calendar_year

    year = live_calendar_year()
    base = master_cfg["folder"]
    parent_paths = _category_prefixes(input_prefix, base)
    canonical = master_cfg.get("canonical_name") or "master.csv"
    meta = _load_date_bounds_meta(cc, parent_paths["current"], canonical) or {}
    max_str = str(meta.get("max_str") or "")[:10]
    if not max_str:
        mx = current_max_date(cc, input_prefix, master_id)
        max_str = mx.isoformat() if mx else ""
    if not max_str:
        return {
            "ok": False,
            "skipped": True,
            "reason": "parent_current_max_unknown",
            "live_folder": base,
        }
    if not _list_files(cc, parent_paths["current"]):
        return {
            "ok": False,
            "skipped": True,
            "reason": "parent_current_missing",
            "live_folder": base,
        }

    cut = pd.Timestamp(max_str)
    daily_cfg = CATEGORY_MERGE_CONFIG.get(daily_id) or {}
    date_candidates = daily_cfg.get("date_candidates") or master_cfg.get("date_candidates") or ["Date"]
    frames, used_files, used_dates, source = _collect_newer_delta_frames(
        cc, input_prefix, daily_id, master_cfg, cut=cut, year=year
    )

    for blob in _list_files(cc, parent_paths["incoming"]):
        log.info("Drop parent incoming leftover %s", blob.name)
        _delete(cc, blob.name)

    if not frames:
        return {
            "ok": True,
            "skipped": True,
            "reason": "no_newer_rows_for_parent_current",
            "live_folder": base,
            "current_max": max_str,
            "source": source,
        }

    delta = _dedupe_delta_frame(pd.concat(frames, ignore_index=True))
    buf = io.StringIO()
    delta.to_csv(buf, index=False)
    new_max = str(to_day_series(delta[find_date_column(delta, date_candidates)]).max().date())
    dest_name = f"{Path(canonical).stem}_parent_delta_{new_max}.csv"
    dest_blob = f"{parent_paths['incoming']}{dest_name}"
    _upload(cc, dest_blob, buf.getvalue().encode("utf-8"), "text/csv")
    log.info(
        "Staged %s rows for parent %s/current (after %s) from %s (%s)",
        len(delta),
        base,
        max_str,
        ", ".join(used_files),
        source,
    )
    return {
        "ok": True,
        "skipped": False,
        "live_folder": base,
        "rows": int(len(delta)),
        "files": used_files,
        "file_dates": sorted(set(used_dates)),
        "incoming": dest_blob,
        "current_max": max_str,
        "source": source,
    }


def fold_daily_into_masters(
    merged_category_ids: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """
    Append days newer than each master current/ file (alongside daily merge).

    Sales also writes the live year folder. Transfer-Master current/ gets the
    same append, so it does not stay stuck on an older max date.

    Delta source: Raw_Files if still present, else daily category current/ so
    masters catch up after Raw_Files were cleared into daily.

    If merged_category_ids is None, attempt both pairs.
    """
    from pipeline.merge_category_inputs import (
        CATEGORY_MERGE_CONFIG,
        _get_container,
        merge_one_category,
        sync_current_to_local,
    )

    merged_set = {str(x).strip() for x in (merged_category_ids or []) if str(x).strip()}
    want_pairs: List[tuple[str, str]] = []
    for daily_id, master_id in DAILY_TO_MASTER.items():
        if not merged_set or daily_id in merged_set or master_id in merged_set:
            want_pairs.append((daily_id, master_id))

    result: Dict[str, Any] = {
        "ok": True,
        "pairs": [],
        "masters_merged": [],
        "errors": [],
        "skipped": [],
    }
    if not want_pairs:
        result["skipped"].append("no_daily_master_pairs_for_merged_ids")
        return result

    cc, _container, input_prefix = _get_container()
    master_ids_done: List[str] = []

    for daily_id, master_id in want_pairs:
        pair: Dict[str, Any] = {
            "daily_id": daily_id,
            "master_id": master_id,
            "ok": False,
        }
        try:
            daily_cfg = CATEGORY_MERGE_CONFIG.get(daily_id) or {}
            master_cfg = CATEGORY_MERGE_CONFIG.get(master_id) or {}
            if not daily_cfg or not master_cfg:
                pair["skipped"] = "missing_category_config"
                result["skipped"].append(f"{daily_id}->{master_id}: missing config")
                result["pairs"].append(pair)
                continue

            # Sales also writes the live year folder. Every master current/ file
            # (Sales-Master and Transfer-Master) gets only the days after its own max.
            if daily_id != "sales-pt-ssl-centro-iconic":
                parent_staged = _stage_parent_master_delta(
                    cc, input_prefix, daily_id, master_cfg, master_id
                )
                pair["parent_stage"] = {
                    "live_folder": parent_staged.get("live_folder"),
                    "rows": parent_staged.get("rows"),
                    "files": parent_staged.get("files"),
                    "reason": parent_staged.get("reason"),
                    "current_max": parent_staged.get("current_max"),
                    "source": parent_staged.get("source"),
                }
                merge_res = {
                    "ok": True,
                    "skipped": True,
                    "reason": parent_staged.get("reason") or "stage_failed",
                }
                if not parent_staged.get("skipped") and parent_staged.get("ok"):
                    merge_res = merge_one_category(
                        master_id,
                        dry_run=False,
                        keep_incoming=False,
                    )
            else:
                staged = _stage_latest_year_delta(cc, input_prefix, daily_id, master_cfg)
                pair["stage"] = {
                    "live_folder": staged.get("live_folder"),
                    "rows": staged.get("rows"),
                    "files": staged.get("files"),
                    "reason": staged.get("reason"),
                    "source": staged.get("source"),
                }
                merge_res: Dict[str, Any] = {
                    "ok": True,
                    "skipped": True,
                    "reason": staged.get("reason") or "stage_failed",
                }
                if not staged.get("skipped") and staged.get("ok"):
                    merge_res = merge_one_category(
                        master_id,
                        dry_run=False,
                        keep_incoming=False,
                        folder_override=staged["live_folder"],
                    )
                parent_staged = _stage_parent_master_delta(
                    cc, input_prefix, daily_id, master_cfg, master_id
                )
                pair["parent_stage"] = {
                    "live_folder": parent_staged.get("live_folder"),
                    "rows": parent_staged.get("rows"),
                    "files": parent_staged.get("files"),
                    "reason": parent_staged.get("reason"),
                    "current_max": parent_staged.get("current_max"),
                    "source": parent_staged.get("source"),
                }
                parent_merge: Optional[Dict[str, Any]] = None
                if not parent_staged.get("skipped") and parent_staged.get("ok"):
                    parent_merge = merge_one_category(
                        master_id,
                        dry_run=False,
                        keep_incoming=False,
                    )
                    pair["parent_merge"] = {
                        "ok": parent_merge.get("ok"),
                        "skipped": parent_merge.get("skipped"),
                        "reason": parent_merge.get("reason"),
                        "errors": parent_merge.get("errors") or [],
                        "written": parent_merge.get("written"),
                    }
                if parent_merge is not None and not parent_merge.get("ok"):
                    result["ok"] = False
                    errs = parent_merge.get("errors") or [
                        parent_merge.get("reason") or "parent current merge failed"
                    ]
                    result["errors"].extend([f"{master_id} current/: {e}" for e in errs])
                elif parent_merge is not None and not parent_merge.get("skipped"):
                    if master_id not in master_ids_done:
                        master_ids_done.append(master_id)
                    pair["ok"] = True
            pair["merge"] = {
                "ok": merge_res.get("ok"),
                "skipped": merge_res.get("skipped"),
                "reason": merge_res.get("reason"),
                "errors": merge_res.get("errors") or [],
                "written": merge_res.get("written"),
            }
            if not merge_res.get("ok"):
                result["ok"] = False
                errs = merge_res.get("errors") or [merge_res.get("reason") or "master merge failed"]
                result["errors"].extend([f"{master_id}: {e}" for e in errs])
            elif not merge_res.get("skipped"):
                if master_id not in master_ids_done:
                    master_ids_done.append(master_id)
                pair["ok"] = True
            elif not pair.get("ok"):
                pair["skipped"] = merge_res.get("reason") or "merge_skipped"
            result["pairs"].append(pair)
        except Exception as exc:
            log.exception("fold daily→master failed for %s", daily_id)
            result["ok"] = False
            result["errors"].append(f"{daily_id}->{master_id}: {exc}")
            pair["error"] = str(exc)
            result["pairs"].append(pair)

    result["masters_merged"] = master_ids_done
    if master_ids_done:
        try:
            sync = sync_current_to_local(dry_run=False, only_ids=master_ids_done)
            result["local_sync"] = {
                "ok": sync.get("ok"),
                "downloaded": len(sync.get("downloaded") or []),
                "errors": sync.get("errors") or [],
            }
        except Exception as sync_exc:
            result["local_sync"] = {"ok": False, "error": str(sync_exc)}
            result["errors"].append(f"master local_sync: {sync_exc}")

    return result


def _publish_soh_comparison_artifacts(summary: Dict[str, Any]) -> Dict[str, Any]:
    """Upload summary + monthly (skip huge weekly) under blob output/historical_soh/."""
    try:
        from azure.storage.blob import BlobServiceClient, ContentSettings
    except ImportError as e:
        return {"ok": False, "error": f"azure sdk missing: {e}"}

    try:
        from dotenv import load_dotenv

        load_dotenv(BACKEND_DIR / ".env")
    except ImportError:
        pass

    conn = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if not conn:
        return {"ok": False, "error": "AZURE_STORAGE_CONNECTION_STRING missing"}
    container = os.environ.get("AZURE_STORAGE_CONTAINER", "zinc-data")
    out_pfx = (os.environ.get("AZURE_BLOB_OUTPUT_PREFIX") or "output/").rstrip("/") + "/"
    base = f"{out_pfx}historical_soh/"

    uploaded: List[str] = []
    errors: List[str] = []
    svc = BlobServiceClient.from_connection_string(conn)
    cc = svc.get_container_client(container)

    def put(local: Path, rel: str) -> None:
        if not local.exists() or not local.is_file():
            return
        # Cap: skip files > 800 MB (weekly can be multi-GB)
        if local.stat().st_size > 800 * 1024 * 1024:
            log.info("Skip blob publish (too large): %s", local.name)
            return
        blob = f"{base}{rel}".replace("\\", "/")
        ctype = "application/json" if local.suffix.lower() == ".json" else "text/csv"
        try:
            with open(local, "rb") as fh:
                cc.upload_blob(
                    name=blob,
                    data=fh,
                    overwrite=True,
                    content_settings=ContentSettings(content_type=ctype),
                )
            uploaded.append(rel)
        except Exception as e:
            errors.append(f"{rel}: {e}")

    monthly = summary.get("monthly_output")
    weekly = summary.get("weekly_output")
    # summary path next to outputs
    if monthly:
        mpath = Path(monthly)
        put(mpath, mpath.name)
        # also "latest" aliases for easy comparison pulls
        put(mpath, "latest_monthly_soh_sellthrough.csv")
    if weekly:
        wpath = Path(weekly)
        # only if reasonably sized
        put(wpath, wpath.name)
    # write summary json file if not already
    summary_path = None
    if monthly:
        summary_path = Path(monthly).with_name(
            Path(monthly).name.replace("monthly_soh_sellthrough_", "soh_sellthrough_").replace(
                ".csv", ".summary.json"
            )
        )
        # actual name pattern from script: soh_sellthrough_<start>_<end>.summary.json
        start = summary.get("start")
        end = summary.get("end")
        if start and end:
            summary_path = Path(monthly).parent / f"soh_sellthrough_{start}_{end}.summary.json"
    if summary_path and summary_path.exists():
        put(summary_path, summary_path.name)
        put(summary_path, "latest_soh_sellthrough.summary.json")

    return {"ok": len(errors) == 0, "uploaded": uploaded, "errors": errors, "prefix": base}


def refresh_historical_soh(*, reuse_txns: bool = False) -> Dict[str, Any]:
    """Rebuild weekly+monthly SOH for comparison (rolling end = yesterday)."""
    import sys

    scripts_dir = BACKEND_DIR / "scripts"
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    from calculate_historical_soh import run as soh_run

    start = _soh_start_date()
    end = _soh_end_date()
    local_only = _env_flag("HISTORICAL_SOH_LOCAL_CACHE_ONLY", "0")
    site_chunk = int(os.environ.get("HISTORICAL_SOH_SITE_CHUNK") or "80")

    log.info(
        "Historical SOH refresh start=%s end=%s reuse_txns=%s local_only=%s chunk=%s",
        start,
        end,
        reuse_txns,
        local_only,
        site_chunk,
    )
    try:
        summary = soh_run(
            start=start,
            end=end,
            local_cache_only=local_only,
            reuse_txns=reuse_txns,
            site_chunk=site_chunk,
        )
        pub = _publish_soh_comparison_artifacts(summary)
        return {"ok": True, "summary": summary, "blob_publish": pub}
    except Exception as exc:
        log.exception("historical SOH refresh failed")
        return {
            "ok": False,
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }


def post_merge_refresh(
    *,
    merged_category_ids: Optional[Sequence[str]] = None,
    run_historical_soh: Optional[bool] = None,
    historical_soh_async: bool = False,
    refresh_inventory_movement_async: bool = True,
) -> Dict[str, Any]:
    """
    Full post-merge hook:
      1) daily sales/transfer → masters (date_merge)
      2) optional historical weekly/monthly SOH refresh for comparison
    """
    if run_historical_soh is None:
        run_historical_soh = _env_flag("HISTORICAL_SOH_ON_MERGE", "1")

    out: Dict[str, Any] = {
        "ok": True,
        "masters": None,
        "historical_soh": None,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }

    # Always try master fold when sales/transfer daily may have updated;
    # if merged_ids empty (full sync path), still attempt both pairs.
    masters = fold_daily_into_masters(merged_category_ids)
    out["masters"] = masters
    if not masters.get("ok"):
        out["ok"] = False

    # SOH dual-path: manual per-channel → update SOH-Master; blob master when newer
    try:
        from pipeline.soh_reconcile import reconcile_soh_master

        soh_rec = reconcile_soh_master(
            merged_category_ids=merged_category_ids,
            dry_run=False,
            run_combine=True,
        )
        out["soh_reconcile"] = soh_rec
        if not soh_rec.get("ok"):
            out["ok"] = False
    except Exception as soh_exc:
        log.exception("SOH reconcile after merge failed")
        out["soh_reconcile"] = {"ok": False, "error": str(soh_exc)}

    need_soh = bool(run_historical_soh)
    # If only unrelated categories merged, still OK to skip SOH unless forced
    if merged_category_ids is not None:
        related = set(DAILY_TO_MASTER) | set(DAILY_TO_MASTER.values())
        if not (set(merged_category_ids) & related) and not masters.get("masters_merged"):
            # no sales/transfer activity — skip heavy SOH unless env forces always
            if not _env_flag("HISTORICAL_SOH_ALWAYS", "0"):
                need_soh = False
                out["historical_soh"] = {"skipped": True, "reason": "no_sales_transfer_merge"}

    if need_soh:
        if historical_soh_async:
            def _bg() -> None:
                try:
                    refresh_historical_soh(reuse_txns=False)
                except Exception:
                    log.exception("async historical SOH failed")

            threading.Thread(target=_bg, name="historical-soh-refresh", daemon=True).start()
            out["historical_soh"] = {"started_async": True}
        else:
            soh = refresh_historical_soh(reuse_txns=False)
            out["historical_soh"] = soh
            if not soh.get("ok"):
                out["ok"] = False

    # Refresh daily inventory movement parquet cache (SOH + Dispatches + Transfers + Sell-Through).
    # auto_daily_run does this synchronously before page_cache, so it disables this
    # async copy to avoid building the same large ledger twice at the same time.
    if refresh_inventory_movement_async:
        try:
            from pipeline.build_daily_inventory_movement import build as build_inv_movement

            def _bg_inv_mov() -> None:
                try:
                    build_inv_movement()
                    log.info("Daily inventory movement cache refreshed after merge.")
                except Exception:
                    log.exception("Daily inventory movement refresh failed after merge.")
                try:
                    from api.historical_movement import build_daily_movement
                    build_daily_movement()
                    log.info("Daily movement ledger (historical sell-through) refreshed after merge.")
                except Exception:
                    log.exception("Daily movement ledger refresh failed after merge.")
                # Re-warm /managers after SOH file is ready (covers async path if page_cache ran early)
                try:
                    from api.routes.manager_performance import warm_managers_overview_cache

                    mgr = warm_managers_overview_cache()
                    log.info("Managers overview re-warm after daily movement: %s", mgr)
                except Exception:
                    log.exception("Managers overview re-warm after daily movement failed.")

            threading.Thread(target=_bg_inv_mov, name="daily-inv-movement-refresh", daemon=True).start()
            out["inventory_movement_refresh"] = {"started_async": True}
        except Exception as inv_exc:
            log.exception("Failed to start daily inventory movement refresh")
            out["inventory_movement_refresh"] = {"ok": False, "error": str(inv_exc)}
    else:
        out["inventory_movement_refresh"] = {
            "skipped": True,
            "reason": "caller_builds_synchronously",
        }

    # Sync updates GRC / transfer invoices — bust Dispatch Timing + Breakeven so UI
    # does not keep serving a stale Oct-26 parquet until manual refresh.
    try:
        related = {
            "grc-report",
            "transfer-pt-ssl-centro-iconic",
            "transfer-master-pt-ssl-centro-iconic",
            "transfer-ebo",
            "item-master",
            "warehouse-soh",
        }
        ids = set(merged_category_ids or [])
        should_refresh_inv = (not ids) or bool(ids & related) or bool(
            (masters.get("masters_merged") if isinstance(masters, dict) else None)
        )

        def _bg_inventory_analytics() -> None:
            try:
                from api.routes import dispatch_lead_time, sku_breakeven

                dispatch_lead_time._compute_lead_time_dataset(force_refresh=True)
                sku_breakeven._compute_breakeven_dataset(force_refresh=True)
                # Parquet alone does not update the tab. Rewrite the page the UI opens.
                dispatch_lead_time.get_dispatch_lead_time_report(
                    search="",
                    alert_filter="all",
                    section="all",
                    season="all",
                    sort_by="inward_date",
                    sort_dir="desc",
                    page=1,
                    page_size=20,
                    refresh=0,
                )
                sku_breakeven.get_sku_breakeven_report(
                    search="",
                    risk_filter="all",
                    section="all",
                    season="all",
                    sort_by="dispatch_gap_vs_be",
                    sort_dir="desc",
                    page=1,
                    page_size=50,
                    refresh=0,
                )
                log.info("Inventory Analytics (lead-time + breakeven) rebuilt after sync/merge.")
            except Exception:
                log.exception("Inventory Analytics rebuild after sync failed.")

        if should_refresh_inv:
            threading.Thread(
                target=_bg_inventory_analytics,
                name="inventory-analytics-refresh",
                daemon=True,
            ).start()
            out["inventory_analytics_refresh"] = {"started_async": True}
        else:
            out["inventory_analytics_refresh"] = {"skipped": True, "reason": "unrelated_categories"}
    except Exception as inv_an_exc:
        log.exception("Failed to start inventory analytics refresh")
        out["inventory_analytics_refresh"] = {"ok": False, "error": str(inv_an_exc)}

    out["finished_at"] = datetime.now(timezone.utc).isoformat()
    return out


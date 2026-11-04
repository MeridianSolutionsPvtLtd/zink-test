"""
Merge Azure Blob input/incoming → input/ (with history backup), then sync local.

Blob layout (container zinc-data):
  input/<file>                 ← final merged files
  input/incoming/<file>        ← new uploads (same filename as input/ to merge)
  input/history/<ts>/<file>    ← auto backup of previous input/ files

Usage:
  python -m pipeline.merge_blob_inputs
  python -m pipeline.merge_blob_inputs --dry-run
  python -m pipeline.merge_blob_inputs --keep-incoming
"""
from __future__ import annotations

import io
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Tuple

log = logging.getLogger("pipeline.merge_blob_inputs")

PIPELINE_DIR = Path(__file__).resolve().parent
MERGED_BACKEND_DIR = PIPELINE_DIR.parent

if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))
if str(MERGED_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(MERGED_BACKEND_DIR))

RESERVED_SUBDIRS = ("incoming", "history", "old", "old_input_files")
TABULAR_EXTS = {".csv", ".xlsx", ".xls"}


def _env(*keys: str, default: str = "") -> str:
    for k in keys:
        v = os.environ.get(k)
        if v:
            return v
    return default


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(MERGED_BACKEND_DIR / ".env")
    except ImportError:
        pass


def _norm_prefix(prefix: str) -> str:
    p = (prefix or "").strip().replace("\\", "/")
    if p and not p.endswith("/"):
        p += "/"
    return p.lstrip("/")


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _is_reserved_rel(rel: str) -> bool:
    lower = rel.replace("\\", "/").lower()
    first = lower.split("/", 1)[0]
    return first in RESERVED_SUBDIRS


def _read_tabular(data: bytes, name: str):
    import pandas as pd

    lower = name.lower()
    bio = io.BytesIO(data)
    if lower.endswith(".csv"):
        return pd.read_csv(bio, low_memory=False)
    if lower.endswith(".xlsx") or lower.endswith(".xls"):
        # Prefer first sheet; keep headers as-is for append
        return pd.read_excel(bio, engine="openpyxl")
    raise ValueError(f"Not a tabular file: {name}")


def _write_tabular(df, name: str) -> Tuple[bytes, str]:
    import pandas as pd

    lower = name.lower()
    if lower.endswith(".csv"):
        buf = io.StringIO()
        df.to_csv(buf, index=False)
        return buf.getvalue().encode("utf-8"), "text/csv"
    if lower.endswith(".xlsx") or lower.endswith(".xls"):
        out = io.BytesIO()
        with pd.ExcelWriter(out, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Sheet1")
        return out.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    raise ValueError(f"Not a tabular file: {name}")


def _merge_frames(old_df, new_df):
    import pandas as pd

    if old_df is None or old_df.empty:
        merged = new_df.copy()
    elif new_df is None or new_df.empty:
        merged = old_df.copy()
    else:
        merged = pd.concat([old_df, new_df], ignore_index=True, sort=False)

    before = len(merged)
    merged = merged.drop_duplicates(keep="last")
    removed = before - len(merged)
    return merged, {"rows_before_dedupe": before, "rows_after_dedupe": len(merged), "duplicates_removed": removed}


def merge_incoming_to_input(
    *,
    dry_run: bool = False,
    keep_incoming: bool = False,
    sync_local: bool = True,
) -> Dict[str, Any]:
    """
    Full process:
      1) Backup current input/ root files → input/history/<timestamp>/
      2) For each input/incoming/<file>: merge with input/<file> (append+dedupe) or promote
      3) Write final file to input/<file>
      4) Optionally delete incoming originals
      5) Sync input/ → local pipeline/input_files/
    """
    _load_dotenv()
    try:
        from azure.storage.blob import BlobServiceClient, ContentSettings
    except ImportError as e:
        raise RuntimeError("Install azure-storage-blob: pip install azure-storage-blob") from e

    conn = _env("AZURE_STORAGE_CONNECTION_STRING")
    if not conn:
        raise RuntimeError("Set AZURE_STORAGE_CONNECTION_STRING in merged_backend/.env")

    container_name = _env("AZURE_STORAGE_CONTAINER", default="zinc-data")
    input_prefix = _norm_prefix(_env("AZURE_BLOB_INPUT_PREFIX", default="input/"))
    incoming_prefix = f"{input_prefix}incoming/"
    history_prefix = f"{input_prefix}history/"

    logging.getLogger("azure").setLevel(logging.WARNING)
    logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)

    svc = BlobServiceClient.from_connection_string(conn)
    cc = svc.get_container_client(container_name)

    stamp = _ts()
    history_run_prefix = f"{history_prefix}{stamp}/"

    all_blobs = list(cc.list_blobs(name_starts_with=input_prefix))
    incoming = [
        b
        for b in all_blobs
        if b.name.startswith(incoming_prefix)
        and not b.name.endswith("/")
        and (b.size or 0) > 0
        and not Path(b.name).name.startswith("~$")
        and Path(b.name).name not in (".keep", ".gitkeep")
    ]
    current_root = [
        b
        for b in all_blobs
        if b.name.startswith(input_prefix)
        and not b.name.startswith(incoming_prefix)
        and not b.name.startswith(history_prefix)
        and not b.name.endswith("/")
        and (b.size or 0) > 0
    ]
    # Only flat files directly under input/ (skip old/, etc.)
    current_files: Dict[str, Any] = {}
    for b in current_root:
        rel = b.name[len(input_prefix) :]
        if not rel or "/" in rel.replace("\\", "/"):
            # nested under reserved/other dirs — skip from merge targets
            if _is_reserved_rel(rel):
                continue
            # nested non-reserved: still treat basename under input/ for backup? skip
            continue
        current_files[Path(rel).name] = b

    result: Dict[str, Any] = {
        "ok": True,
        "dry_run": dry_run,
        "container": container_name,
        "input_prefix": input_prefix,
        "history_run_prefix": history_run_prefix,
        "incoming_count": len(incoming),
        "backed_up": [],
        "merged": [],
        "promoted": [],
        "replaced_binary": [],
        "deleted_incoming": [],
        "skipped": [],
        "errors": [],
        "local_sync": None,
    }

    if not incoming:
        result["ok"] = True
        result["message"] = (
            "No files in input/incoming/. Upload new files there "
            "(same filename as input/<file> to append-merge)."
        )
        if sync_local and not dry_run:
            from pipeline.sync_blob_inputs import sync_inputs_from_blob

            result["local_sync"] = sync_inputs_from_blob(dry_run=False)
        return result

    # 1) Backup current input root files that will be touched (or all root files)
    touch_names = {Path(b.name).name for b in incoming}
    to_backup = [current_files[n] for n in touch_names if n in current_files]
    # Also backup all current root if we want full snapshot — backup only files being merged
    for b in to_backup:
        name = Path(b.name).name
        dest = f"{history_run_prefix}{name}"
        result["backed_up"].append({"from": b.name, "to": dest})
        if dry_run:
            continue
        try:
            src = cc.get_blob_client(b.name)
            dst = cc.get_blob_client(dest)
            data = src.download_blob().readall()
            props = src.get_blob_properties()
            ctype = getattr(getattr(props, "content_settings", None), "content_type", None) or "application/octet-stream"
            dst.upload_blob(
                data,
                overwrite=True,
                content_settings=ContentSettings(content_type=ctype),
            )
            log.info("Backup %s → %s", b.name, dest)
        except Exception as e:
            result["errors"].append(f"backup {b.name}: {e}")
            log.exception("Backup failed %s", b.name)

    # 2) Merge / promote each incoming file
    for b in incoming:
        name = Path(b.name).name
        final_blob = f"{input_prefix}{name}"
        ext = Path(name).suffix.lower()
        try:
            new_bytes = cc.get_blob_client(b.name).download_blob().readall()
            old_blob = current_files.get(name)

            if old_blob is None:
                # No existing file with same name → promote incoming as-is
                result["promoted"].append({"incoming": b.name, "final": final_blob, "bytes": len(new_bytes)})
                if not dry_run:
                    cc.upload_blob(
                        name=final_blob,
                        data=new_bytes,
                        overwrite=True,
                        content_settings=ContentSettings(
                            content_type=_guess_content_type(name)
                        ),
                    )
                continue

            old_bytes = cc.get_blob_client(old_blob.name).download_blob().readall()

            if ext in TABULAR_EXTS:
                old_df = _read_tabular(old_bytes, name)
                new_df = _read_tabular(new_bytes, name)
                merged_df, stats = _merge_frames(old_df, new_df)
                out_bytes, ctype = _write_tabular(merged_df, name)
                result["merged"].append(
                    {
                        "file": name,
                        "incoming": b.name,
                        "final": final_blob,
                        "old_rows": int(len(old_df)),
                        "new_rows": int(len(new_df)),
                        **stats,
                    }
                )
                if not dry_run:
                    cc.upload_blob(
                        name=final_blob,
                        data=out_bytes,
                        overwrite=True,
                        content_settings=ContentSettings(content_type=ctype),
                    )
                    log.info(
                        "Merged %s: old=%s new=%s → %s rows",
                        name,
                        len(old_df),
                        len(new_df),
                        len(merged_df),
                    )
            else:
                # Non-tabular: keep old in history, replace with incoming
                result["replaced_binary"].append(
                    {"file": name, "incoming": b.name, "final": final_blob}
                )
                if not dry_run:
                    cc.upload_blob(
                        name=final_blob,
                        data=new_bytes,
                        overwrite=True,
                        content_settings=ContentSettings(content_type=_guess_content_type(name)),
                    )
        except Exception as e:
            result["errors"].append(f"{b.name}: {e}")
            log.exception("Merge failed %s", b.name)

    # 3) Clear incoming (optional)
    if not keep_incoming and not dry_run:
        for b in incoming:
            # Only delete if no error for that blob
            if any(b.name in err for err in result["errors"]):
                result["skipped"].append(b.name + " (kept; had error)")
                continue
            try:
                cc.delete_blob(b.name)
                result["deleted_incoming"].append(b.name)
            except Exception as e:
                result["errors"].append(f"delete {b.name}: {e}")

    # 4) Sync to local input_files
    if sync_local and not dry_run:
        try:
            from pipeline.sync_blob_inputs import sync_inputs_from_blob

            result["local_sync"] = sync_inputs_from_blob(dry_run=False)
        except Exception as e:
            result["errors"].append(f"local_sync: {e}")
            log.exception("Local sync failed")

    result["ok"] = len(result["errors"]) == 0
    result["hint"] = (
        "Upload new files to input/incoming/ using the SAME filename as input/<file> "
        "so they append-merge. Unique rows are kept (full-row dedupe)."
    )
    return result


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
    return "application/octet-stream"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    import argparse
    import json

    p = argparse.ArgumentParser(description="Merge Blob incoming → input/ with history backup")
    p.add_argument("--dry-run", action="store_true", help="Plan only, no writes")
    p.add_argument("--keep-incoming", action="store_true", help="Do not delete incoming after merge")
    p.add_argument("--no-local-sync", action="store_true", help="Skip download to pipeline/input_files")
    args = p.parse_args()
    result = merge_incoming_to_input(
        dry_run=args.dry_run,
        keep_incoming=args.keep_incoming,
        sync_local=not args.no_local_sync,
    )
    print(json.dumps(result, indent=2, default=str))
    raise SystemExit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()

"""
Sync pipeline inputs from Azure Blob `input/` → local folders.

Blob layout (container zinc-data):
  input/<file>                      → pipeline/input_files/<file>
  input/old/<file>                  → pipeline/old_input_files/<file>
  input/old_input_files/<file>      → pipeline/old_input_files/<file>

Usage:
  python -m pipeline.sync_blob_inputs
  python -m pipeline.runner --stage all          # syncs first (default)
  python -m pipeline.runner --stage all --skip-blob-sync
"""
from __future__ import annotations

import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("pipeline.sync_blob_inputs")

PIPELINE_DIR = Path(__file__).resolve().parent
MERGED_BACKEND_DIR = PIPELINE_DIR.parent

if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))
if str(MERGED_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(MERGED_BACKEND_DIR))


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


def _safe_rel(name: str) -> Path:
    """Prevent path traversal when writing under local dirs."""
    parts = [p for p in Path(name).parts if p not in ("", ".", "..")]
    if not parts:
        raise ValueError(f"Invalid blob relative path: {name!r}")
    return Path(*parts)


def local_input_dir() -> Path:
    from pipeline_inputs import INPUT_DIR

    return Path(INPUT_DIR)


def local_old_input_dir() -> Path:
    from pipeline_inputs import BASE_DIR

    return Path(BASE_DIR) / "old_input_files"


def _map_blob_to_local(blob_name: str, input_prefix: str) -> Optional[Path]:
    """
    Map blob path under input/ to a local file path.
    Returns None for folder markers / ignored paths.

    Pipeline does not need Blob history/incoming — merge already happened on upload.
    last_week_store_soh uses local output_files/combined_soh/history only.
    """
    if not blob_name.startswith(input_prefix):
        return None
    rel = blob_name[len(input_prefix) :]
    if not rel or rel.endswith("/"):
        return None

    # Nested old snapshots
    lower = rel.replace("\\", "/").lower()
    if lower.startswith("old_input_files/"):
        rest = rel.split("/", 1)[1]
        return local_old_input_dir() / _safe_rel(rest)
    if lower.startswith("old/"):
        rest = rel.split("/", 1)[1]
        return local_old_input_dir() / _safe_rel(rest)

    # Skip merge staging anywhere under category folders
    # e.g. Sales (...)/history/previous/...  or  .../incoming/...
    parts = [p for p in lower.split("/") if p]
    if "history" in parts or "incoming" in parts:
        return None

    # Category layout: only current/ is useful for pipeline (masters already merged)
    # e.g. EBO Sales/current/EBO_SALES.xlsx
    if len(parts) >= 2 and "current" in parts:
        # Keep relative path so files land under input_files/<Category>/current/...
        # Runner also flattens via sync_current_to_local → input_files/<basename>
        return local_input_dir() / _safe_rel(rel)

    # Flat file directly under input/ (legacy)
    if "/" not in lower:
        return local_input_dir() / _safe_rel(rel)

    # Other nested paths (unknown) — skip to keep sync fast
    return None


def sync_inputs_from_blob(*, dry_run: bool = False) -> Dict[str, Any]:
    """
    Download all blobs under AZURE_BLOB_INPUT_PREFIX into local pipeline folders.
    Skips download when local file size + etag match (etag sidecar).
    """
    _load_dotenv()
    try:
        from azure.storage.blob import BlobServiceClient
    except ImportError as e:
        raise RuntimeError("Install azure-storage-blob: pip install azure-storage-blob") from e

    conn = _env("AZURE_STORAGE_CONNECTION_STRING")
    if not conn:
        raise RuntimeError("Set AZURE_STORAGE_CONNECTION_STRING in merged_backend/.env")

    container_name = _env("AZURE_STORAGE_CONTAINER", default="zinc-data")
    input_prefix = _norm_prefix(_env("AZURE_BLOB_INPUT_PREFIX", default="input/"))

    svc = BlobServiceClient.from_connection_string(conn)
    cc = svc.get_container_client(container_name)
    # Quiet verbose Azure HTTP logs during sync
    logging.getLogger("azure").setLevel(logging.WARNING)
    logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)

    downloaded: List[str] = []
    skipped: List[str] = []
    errors: List[str] = []
    mapped: List[Dict[str, str]] = []

    in_dir = local_input_dir()
    old_dir = local_old_input_dir()
    if not dry_run:
        in_dir.mkdir(parents=True, exist_ok=True)
        old_dir.mkdir(parents=True, exist_ok=True)

    blobs = list(cc.list_blobs(name_starts_with=input_prefix))
    for b in blobs:
        name = b.name
        if name.endswith("/") or (b.size is not None and b.size == 0 and name.endswith("/")):
            continue
        try:
            dest = _map_blob_to_local(name, input_prefix)
        except ValueError as e:
            errors.append(f"{name}: {e}")
            continue
        if dest is None:
            continue

        mapped.append({"blob": name, "local": str(dest)})
        etag_path = Path(str(dest) + ".etag")
        etag = str(getattr(b, "etag", None) or b.size or "")

        if dest.exists() and etag_path.exists() and etag_path.read_text(encoding="utf-8").strip() == etag:
            skipped.append(name)
            continue
        # Also skip if size matches and no etag file yet (first migrate)
        if dest.exists() and b.size is not None and dest.stat().st_size == b.size and etag_path.exists():
            skipped.append(name)
            continue

        # Skip Excel lock / temp names
        if dest.name.startswith("~$") or Path(name).name.startswith("~$"):
            skipped.append(name)
            continue

        if dry_run:
            downloaded.append(name)
            continue

        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + ".blobtmp")
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
            client = cc.get_blob_client(name)
            with open(tmp, "wb") as fh:
                stream = client.download_blob()
                for chunk in stream.chunks():
                    fh.write(chunk)
            try:
                tmp.replace(dest)
            except PermissionError:
                # File open in Excel — copy over then remove tmp
                import shutil
                import time

                last_err: Exception | None = None
                for attempt in range(5):
                    try:
                        shutil.copyfile(tmp, dest)
                        last_err = None
                        break
                    except PermissionError as pe:
                        last_err = pe
                        time.sleep(1.0 + attempt)
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
                if last_err is not None:
                    if dest.exists() and dest.stat().st_size > 0:
                        log.warning(
                            "Locked file kept (Excel open?): %s — using existing local copy",
                            dest.name,
                        )
                        skipped.append(name + " (locked; kept local)")
                        try:
                            tmp.unlink(missing_ok=True)
                        except OSError:
                            pass
                        continue
                    raise PermissionError(
                        f"Cannot overwrite {dest.name} — close it in Excel and retry. ({last_err})"
                    ) from last_err
            etag_path.write_text(etag, encoding="utf-8")
            downloaded.append(name)
            log.info("Downloaded %s → %s (%.1f MB)", name, dest, (b.size or 0) / 1e6)
        except PermissionError as e:
            if dest.exists() and dest.stat().st_size > 0:
                log.warning("Locked — keeping local %s (%s)", dest.name, e)
                skipped.append(name + " (locked; kept local)")
            else:
                errors.append(f"{name}: {e}")
                log.exception("Failed %s", name)
        except Exception as e:
            errors.append(f"{name}: {e}")
            log.exception("Failed %s", name)

    return {
        "ok": len(errors) == 0,
        "container": container_name,
        "input_prefix": input_prefix,
        "blob_count": len(blobs),
        "downloaded": downloaded,
        "skipped_unchanged": skipped,
        "mapped": mapped,
        "errors": errors,
        "local_input_dir": str(in_dir),
        "local_old_input_dir": str(old_dir),
        "hint": (
            "Upload pipeline Excel/CSV files under blob path input/ "
            "(same names as local input_files/). Put COMBINED_SOH.xlsx under "
            "input/old/ or input/old_input_files/ for last-week SOH."
        ),
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    import argparse
    import json

    p = argparse.ArgumentParser(description="Sync pipeline inputs from Azure Blob")
    p.add_argument("--dry-run", action="store_true", help="List mapping only, no download")
    args = p.parse_args()
    result = sync_inputs_from_blob(dry_run=args.dry_run)
    print(json.dumps(result, indent=2, default=str))
    raise SystemExit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()

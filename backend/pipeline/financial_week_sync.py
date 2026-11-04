"""Poll raw financial calendar versions and queue the existing upload merge worker."""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

log = logging.getLogger(__name__)
_START_LOCK = threading.Lock()
_STARTED = False


def start_financial_week_sync() -> None:
    global _STARTED
    with _START_LOCK:
        if _STARTED or not os.environ.get("AZURE_STORAGE_CONNECTION_STRING"):
            return
        if os.environ.get("FINANCIAL_WEEK_AUTO_SYNC", "1").lower() in {"0", "false", "off"}:
            return
        _STARTED = True
    threading.Thread(target=_watch, daemon=True, name="financial-week-raw-sync").start()


def _watch() -> None:
    from azure.storage.blob import ContentSettings
    from pipeline.ginesys_raw import _get_container, _list_raw_with_fallback
    from pipeline.merge_jobs import start_merge_async
    from pipeline.financial_calendar import CATEGORY_ID, FOLDER

    state_path = Path(__file__).resolve().parents[1] / "data" / "api_cache" / "financial_week_raw_versions.json"
    try:
        processed = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        processed = {}
    interval = max(15, int(os.environ.get("FINANCIAL_WEEK_SYNC_SECONDS", "60")))
    stop = threading.Event()
    pending = None
    pending_versions = {}
    while True:
        try:
            if pending is not None:
                if pending.status == "success":
                    processed.update(pending_versions)
                    state_path.parent.mkdir(parents=True, exist_ok=True)
                    tmp = state_path.with_suffix(".tmp")
                    tmp.write_text(json.dumps(processed), encoding="utf-8")
                    tmp.replace(state_path)
                    pending = None
                elif pending.status == "failed":
                    log.error("Financial Week Info merge failed: %s", pending.error)
                    pending = None
            if pending is None:
                cc, _ = _get_container()
                _, files, _ = _list_raw_with_fallback(cc, os.environ.get("AZURE_BLOB_RAW_PREFIX", "Raw_Files/"))
                versions = {}
                prefix = os.environ.get("AZURE_BLOB_INPUT_PREFIX", "input/").strip("/")
                for rec in sorted(files, key=lambda f: (f["last_modified"], f["blob"])):
                    if rec.get("category_id") != CATEGORY_ID:
                        continue
                    client = cc.get_blob_client(rec["blob"])
                    version = str(client.get_blob_properties().etag)
                    if processed.get(rec["blob"]) == version:
                        continue
                    data = client.download_blob().readall()
                    filename = Path(rec["filename"]).name
                    cc.upload_blob(name=f"{prefix}/{FOLDER}/incoming/{filename}", data=data, overwrite=True,
                                   content_settings=ContentSettings(content_type="text/csv" if filename.lower().endswith(".csv") else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))
                    versions[rec["blob"]] = version
                if versions:
                    pending_versions = versions
                    pending = start_merge_async([CATEGORY_ID])
        except Exception:
            log.exception("Financial Week Info raw sync failed; will retry")
        stop.wait(interval)

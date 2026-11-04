"""
Upload caught-up Sales/Transfer masters to blob, rebuild dependent caches, publish.

Run from backend root:
  python scripts/upload_masters_and_publish_caches.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

INPUT = ROOT / "pipeline" / "input_files"
RESULTS: dict = {"started_at": datetime.now(timezone.utc).isoformat(), "steps": {}}


def _log(msg: str) -> None:
    print(msg, flush=True)


def _container():
    from azure.storage.blob import BlobServiceClient

    conn = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if not conn:
        raise RuntimeError("AZURE_STORAGE_CONNECTION_STRING missing")
    container = os.environ.get("AZURE_STORAGE_CONTAINER", "zinc-data")
    input_prefix = (os.environ.get("AZURE_BLOB_INPUT_PREFIX") or "input/").strip()
    if input_prefix and not input_prefix.endswith("/"):
        input_prefix += "/"
    svc = BlobServiceClient.from_connection_string(conn)
    return svc.get_container_client(container), input_prefix


def upload_masters() -> dict:
    from api.blob_store import upload_local_file_with_retry
    from pipeline.merge_category_inputs import CATEGORY_MERGE_CONFIG
    from pipeline.year_stores import live_calendar_year

    cc, input_prefix = _container()
    year = live_calendar_year()
    uploaded: list[str] = []
    errors: list[str] = []

    specs = [
        ("sales-master-pt-ssl-centro-iconic", True),  # also year folder
        ("transfer-master-pt-ssl-centro-iconic", False),
    ]
    for cid, also_year in specs:
        cfg = CATEGORY_MERGE_CONFIG[cid]
        folder = cfg["folder"]
        name = cfg["canonical_name"]
        local = INPUT / name
        bounds = INPUT / f"{Path(name).stem}.date_bounds.json"
        if not local.exists():
            errors.append(f"missing {local.name}")
            continue
        # Parent current/
        blob = f"{input_prefix}{folder}/current/{name}"
        _log(f"UPLOAD {local.name} ({local.stat().st_size/1e6:.1f} MB) -> {blob}")
        try:
            upload_local_file_with_retry(
                cc, blob_name=blob, path=local, content_type="text/csv", attempts=4, timeout_s=1200
            )
            uploaded.append(blob)
        except Exception as exc:
            errors.append(f"{blob}: {exc}")
            _log(f"FAIL {blob}: {exc}")
            continue
        if bounds.exists():
            bblob = f"{input_prefix}{folder}/current/{bounds.name}"
            _log(f"UPLOAD {bounds.name} -> {bblob}")
            try:
                upload_local_file_with_retry(
                    cc, blob_name=bblob, path=bounds, content_type="application/json", attempts=3
                )
                uploaded.append(bblob)
            except Exception as exc:
                errors.append(f"{bblob}: {exc}")

        if also_year:
            # Live calendar year folder — only that year's rows (not full multi-year master).
            yblob = f"{input_prefix}{folder}/{year}/current/{name}"
            try:
                import pandas as pd
                from pipeline.date_merge import find_date_column, to_day_series

                _log(f"Building year/{year} slice from {local.name} …")
                df = pd.read_csv(local, low_memory=False)
                df.columns = [str(c).strip() for c in df.columns]
                dcol = find_date_column(
                    df, cfg.get("date_candidates") or ["ENTRY_DATE", "Invoice Date", "Date"]
                )
                days = to_day_series(df[dcol])
                ydf = df.loc[days.notna() & (days.dt.year == int(year))].copy()
                ytmp = INPUT / f"_tmp_{Path(name).stem}_y{year}.csv"
                ydf.to_csv(ytmp, index=False)
                y_min = str(days.loc[ydf.index].min().date()) if len(ydf) else None
                y_max = str(days.loc[ydf.index].max().date()) if len(ydf) else None
                _log(f"UPLOAD year/{year} rows={len(ydf)} {y_min}..{y_max} -> {yblob}")
                upload_local_file_with_retry(
                    cc, blob_name=yblob, path=ytmp, content_type="text/csv", attempts=4, timeout_s=1200
                )
                uploaded.append(yblob)
                ybounds = {
                    "date_column": str(dcol),
                    "min_str": y_min,
                    "max_str": y_max,
                    "total_rows": int(len(ydf)),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "source": "local_master_year_slice",
                }
                yb_path = INPUT / f"_tmp_{Path(name).stem}_y{year}.date_bounds.json"
                yb_path.write_text(json.dumps(ybounds, indent=2), encoding="utf-8")
                yb = f"{input_prefix}{folder}/{year}/current/{Path(name).stem}.date_bounds.json"
                upload_local_file_with_retry(
                    cc, blob_name=yb, path=yb_path, content_type="application/json", attempts=3
                )
                uploaded.append(yb)
                ytmp.unlink(missing_ok=True)
                yb_path.unlink(missing_ok=True)
            except Exception as exc:
                errors.append(f"{yblob}: {exc}")
                _log(f"FAIL year upload: {exc}")

    return {"ok": not errors, "uploaded": uploaded, "errors": errors}


def rebuild_daily_movement() -> dict:
    from api.current_soh import publish_daily_movement_to_blob
    from pipeline.build_daily_inventory_movement import build as build_inv

    _log("Building daily_inventory_movement …")
    t0 = time.perf_counter()
    summary = build_inv()
    pub = publish_daily_movement_to_blob()
    return {
        "ok": bool(summary) and bool(pub.get("ok")),
        "seconds": round(time.perf_counter() - t0, 1),
        "date_max": (summary or {}).get("date_max"),
        "end_date": (summary or {}).get("end_date"),
        "closing_soh_net": (summary or {}).get("closing_soh_net"),
        "publish": pub,
    }


def rebuild_and_publish_caches() -> dict:
    from api.page_response_cache import warm_default_page_caches
    from api.routes.sales_analyzer import (
        publish_prepared_analyzer_caches_to_blob,
        refresh_analyzer_prepared_caches,
        warm_default_analyzer_queries,
    )
    from api.routes.sales_dashboard import warm_sales_overview_cache
    from api.sales_year_loader import get_default_dashboard_year, warm_fy_years_sorted

    out: dict = {}
    years = tuple(warm_fy_years_sorted())
    _log(f"Refreshing analyzer prepared caches years={years} …")
    out["analyzer_prepared"] = refresh_analyzer_prepared_caches(
        force=True, years=years, publish=True
    )
    _log(f"analyzer_prepared: {out['analyzer_prepared'].get('ok')} {out['analyzer_prepared'].get('detail')}")

    _log("Warming default analyzer queries …")
    out["analyzer_queries"] = warm_default_analyzer_queries()

    _log("Warming Sales Overview cache …")
    out["sales_overview"] = warm_sales_overview_cache(force_refresh=True)

    _log("Warming + publishing default page caches …")
    out["page_caches"] = warm_default_page_caches()
    # Ensure prepared analyzer publish even if page_caches skipped it
    out["prepared_publish"] = publish_prepared_analyzer_caches_to_blob()
    out["dash_year"] = get_default_dashboard_year()
    return out


def main() -> int:
    t0 = time.perf_counter()
    try:
        _log("=== 1) Upload masters to blob ===")
        RESULTS["steps"]["upload_masters"] = upload_masters()
        _log(json.dumps(RESULTS["steps"]["upload_masters"], indent=2, default=str))

        _log("=== 2) Rebuild daily inventory movement + publish ===")
        RESULTS["steps"]["daily_movement"] = rebuild_daily_movement()
        _log(json.dumps(RESULTS["steps"]["daily_movement"], indent=2, default=str))

        _log("=== 3) Rebuild analyzer/sales/page caches + publish ===")
        RESULTS["steps"]["caches"] = rebuild_and_publish_caches()
        # Compact log
        caches = RESULTS["steps"]["caches"]
        compact = {
            "analyzer_prepared_ok": (caches.get("analyzer_prepared") or {}).get("ok"),
            "analyzer_queries_ok": (caches.get("analyzer_queries") or {}).get("ok"),
            "sales_overview": caches.get("sales_overview"),
            "page_caches_ok": (caches.get("page_caches") or {}).get("ok"),
            "page_publish": (caches.get("page_caches") or {}).get("publish"),
            "prepared_publish": caches.get("prepared_publish"),
        }
        _log(json.dumps(compact, indent=2, default=str))
    except Exception as exc:
        RESULTS["ok"] = False
        RESULTS["error"] = str(exc)
        _log(f"FATAL: {exc}")
        raise
    finally:
        RESULTS["seconds"] = round(time.perf_counter() - t0, 1)
        RESULTS["finished_at"] = datetime.now(timezone.utc).isoformat()
        up = RESULTS.get("steps", {}).get("upload_masters") or {}
        dm = RESULTS.get("steps", {}).get("daily_movement") or {}
        RESULTS["ok"] = bool(up.get("ok")) and bool(dm.get("ok", True))
        out = ROOT / "pipeline" / "output_files" / "upload_masters_publish_caches.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(RESULTS, indent=2, default=str), encoding="utf-8")
        _log(f"wrote {out}")
        _log(f"DONE ok={RESULTS.get('ok')} in {RESULTS['seconds']}s")

    return 0 if RESULTS.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

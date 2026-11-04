"""
Publish pipeline outputs to Azure Blob `output/` and keep local clean.

Blob layout (under AZURE_BLOB_OUTPUT_PREFIX, default output/):
  COMBINED_SALES.xlsx
  COMBINED_TRANSFER.xlsx
  COMBINED_SOH.xlsx                          (compat flat)
  combined_soh/current/COMBINED_SOH.xlsx
  combined_soh/history/COMBINED_SOH_YYYY-MM-DD.xlsx
  combined_soh/COMBINED_SOH.meta.json
  prediction/current/<with_refill>.csv
  prediction/history/<with_refill>_YYYY-MM-DD.csv
  prediction/SS26_..._from_5w_avg_selected_stores.csv
  transfer/current/final_transfer_sizewise.csv
  transfer/history/final_transfer_sizewise_YYYY-MM-DD.csv
  transfer/potential_donors.csv
  transfer/potential_receivers.csv
  transfer/final_transfer_sizewise.csv       (flat convenience)

Mid-run: after 7_enrich_sku_size.py, publish_replenishment_outputs_to_blob()
pushes prediction/refill only (no clear), then transfer steps run; full publish+clear at end.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("pipeline.publish_outputs_to_blob")

BASE_DIR = Path(__file__).resolve().parent
MERGED_BACKEND_DIR = BASE_DIR.parent
OUTPUT_DIR = BASE_DIR / "output_files"
TRANSFER_OUT = BASE_DIR / "transfer" / "output"

SOH_HISTORY_KEEP = 10
PRED_HISTORY_KEEP = 5
TRANSFER_HISTORY_KEEP = 5

REFILL_CSV = "SS26_weekly_predictions_formula_next_week_with_refill_recommendation.csv"
SELECTED_CSV = "SS26_weekly_predictions_formula_next_week_from_5w_avg_selected_stores.csv"


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(MERGED_BACKEND_DIR / ".env")
    except Exception:
        pass


def _env(*keys: str, default: str = "") -> str:
    _load_dotenv()
    for k in keys:
        v = (os.environ.get(k) or "").strip()
        if v:
            return v
    return default


def _norm_prefix(prefix: str) -> str:
    p = (prefix or "output/").replace("\\", "/").strip()
    if p and not p.endswith("/"):
        p += "/"
    return p.lstrip("/")


def _guess_content_type(name: str) -> str:
    lower = name.lower()
    if lower.endswith(".csv"):
        return "text/csv"
    if lower.endswith(".xlsx"):
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if lower.endswith(".json"):
        return "application/json"
    return "application/octet-stream"


def _get_container():
    from azure.storage.blob import BlobServiceClient

    conn = _env("AZURE_STORAGE_CONNECTION_STRING")
    if not conn:
        raise RuntimeError("Set AZURE_STORAGE_CONNECTION_STRING in .env")
    container_name = _env("AZURE_STORAGE_CONTAINER", default="zinc-data")
    output_prefix = _norm_prefix(_env("AZURE_BLOB_OUTPUT_PREFIX", default="output/"))
    svc = BlobServiceClient.from_connection_string(conn)
    cc = svc.get_container_client(container_name)
    return cc, container_name, output_prefix


def _upload_file(cc, blob_name: str, path: Path) -> None:
    from azure.storage.blob import ContentSettings

    data = path.read_bytes()
    cc.upload_blob(
        name=blob_name,
        data=data,
        overwrite=True,
        content_settings=ContentSettings(content_type=_guess_content_type(path.name)),
    )


def _download_file(cc, blob_name: str, dest: Path) -> bool:
    try:
        client = cc.get_blob_client(blob_name)
        if not client.exists():
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(client.download_blob().readall())
        return True
    except Exception as e:
        log.warning("download failed %s: %s", blob_name, e)
        return False


def _list_under(cc, prefix: str) -> List[str]:
    names: List[str] = []
    for b in cc.list_blobs(name_starts_with=prefix):
        name = b.name
        if name.endswith("/") or (b.size or 0) <= 0:
            continue
        names.append(name)
    return names


def _prune_dated_blobs(
    cc,
    history_prefix: str,
    *,
    basename: str,
    ext: str,
    keep: int,
) -> List[str]:
    """Delete oldest dated blobs, keep newest `keep`."""
    pat = re.compile(
        rf"^{re.escape(basename)}_(\d{{4}}-\d{{2}}-\d{{2}}){re.escape(ext)}$",
        re.I,
    )
    items: List[Tuple[date, str]] = []
    for full in _list_under(cc, history_prefix):
        name = Path(full).name
        m = pat.match(name)
        if not m:
            continue
        try:
            d = date.fromisoformat(m.group(1))
        except ValueError:
            continue
        items.append((d, full))
    items.sort(key=lambda x: x[0], reverse=True)
    removed: List[str] = []
    for _, blob_name in items[keep:]:
        try:
            cc.delete_blob(blob_name)
            removed.append(Path(blob_name).name)
        except Exception as e:
            log.warning("blob prune failed %s: %s", blob_name, e)
    return removed


# Mid-pipeline artifacts needed when App Service resumes after recycle (ephemeral disk).
_CHECKPOINT_FLAT: Tuple[str, ...] = (
    "SALES_REPORT.csv",
    "SALES_REPORT.xlsx",
    "weekly_aggregated_sales_new.csv",
    "weekly_aggregated_sales_new.xlsx",
    "weekly_aggregated_sales_new_5w_avg_sold_with_first_transfer.csv",
    "weekly_aggregated_sales_new_5w_avg_sold_with_first_transfer.xlsx",
    "COMBINED_SALES.csv",
    "COMBINED_SALES.xlsx",
    "COMBINED_TRANSFER.csv",
    "COMBINED_TRANSFER.xlsx",
    "COMBINED_SOH.csv",
    "COMBINED_SOH.xlsx",
)

# script basename → files to upload right after that step succeeds
_CHECKPOINT_AFTER_SCRIPT: Dict[str, Tuple[str, ...]] = {
    "combine_soh.py": (
        "COMBINED_SOH.csv",
        "COMBINED_SOH.xlsx",
    ),
    "1.sales_report.py": ("SALES_REPORT.csv", "SALES_REPORT.xlsx"),
    "2.weekly_aggregate.py": (
        "weekly_aggregated_sales_new.csv",
        "weekly_aggregated_sales_new.xlsx",
    ),
    "3.add_soh_weekly.py": (
        "weekly_aggregated_sales_new.csv",
        "weekly_aggregated_sales_new.xlsx",
    ),
    "4.add_first_transfer.py": (
        "weekly_aggregated_sales_new_5w_avg_sold_with_first_transfer.csv",
        "weekly_aggregated_sales_new_5w_avg_sold_with_first_transfer.xlsx",
    ),
}


def sync_pipeline_intermediates_from_blob(*, only_missing: bool = True) -> Dict[str, Any]:
    """
    Pull mid-pipeline flat outputs from blob → local output_files/.

    Hosted resume often starts at weekly_aggregate+ after a recycle; local disk is empty
    even though output/SALES_REPORT.* still exists on blob from the last successful publish.
    """
    cc, container, out_pfx = _get_container()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    downloaded: List[str] = []
    skipped: List[str] = []
    errors: List[str] = []

    for name in _CHECKPOINT_FLAT:
        dest = OUTPUT_DIR / name
        if only_missing and dest.exists() and dest.stat().st_size > 0:
            skipped.append(name)
            continue
        blob_name = f"{out_pfx}{name}"
        try:
            if _download_file(cc, blob_name, dest):
                downloaded.append(name)
            else:
                skipped.append(name)
        except Exception as e:
            errors.append(f"{name}: {e}")

    log.info(
        "Synced pipeline intermediates from blob://%s/%s (%d downloaded, %d skipped)",
        container,
        out_pfx,
        len(downloaded),
        len(skipped),
    )
    return {
        "ok": not errors,
        "container": container,
        "prefix": out_pfx,
        "downloaded": downloaded,
        "skipped": skipped,
        "errors": errors,
    }


def checkpoint_outputs_to_blob(script_rel: str) -> Dict[str, Any]:
    """Upload key outputs right after a script so Azure resume can restore them."""
    base = Path(str(script_rel).replace("\\", "/")).name
    names = _CHECKPOINT_AFTER_SCRIPT.get(base)
    if not names:
        return {"ok": True, "uploaded": [], "skipped": [], "errors": [], "script": base}

    cc, container, out_pfx = _get_container()
    put, uploaded, skipped, errors = _blob_putters(cc, out_pfx)
    for name in names:
        put(OUTPUT_DIR / name, name)
    log.info(
        "Checkpoint %s → blob://%s/%s (%d uploaded)",
        base,
        container,
        out_pfx,
        len(uploaded),
    )
    return {
        "ok": not errors,
        "container": container,
        "prefix": out_pfx,
        "script": base,
        "uploaded": uploaded,
        "skipped": skipped,
        "errors": errors,
    }


def sync_combined_soh_from_blob() -> Dict[str, Any]:
    """
    Pull combined_soh current + history from blob → local (temp workspace).
    Needed so last_week_store_soh uses the same blob history rules.
    """
    cc, container, out_pfx = _get_container()
    soh_pfx = f"{out_pfx}combined_soh/"
    local_store = OUTPUT_DIR / "combined_soh"
    downloaded: List[str] = []
    errors: List[str] = []

    for blob_name in _list_under(cc, soh_pfx):
        rel = blob_name[len(soh_pfx) :] if blob_name.startswith(soh_pfx) else Path(blob_name).name
        dest = local_store / rel.replace("/", os.sep)
        try:
            if _download_file(cc, blob_name, dest):
                downloaded.append(rel)
        except Exception as e:
            errors.append(f"{rel}: {e}")

    # Flat compat mirror
    current_local = local_store / "current" / "COMBINED_SOH.xlsx"
    compat = OUTPUT_DIR / "COMBINED_SOH.xlsx"
    if current_local.exists():
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(current_local, compat)
        downloaded.append("COMBINED_SOH.xlsx (compat)")

    log.info(
        "Synced combined_soh from blob://%s/%s (%d file(s))",
        container,
        soh_pfx,
        len(downloaded),
    )
    return {
        "ok": not errors,
        "container": container,
        "prefix": soh_pfx,
        "downloaded": downloaded,
        "errors": errors,
    }


def _blob_putters(cc, out_pfx: str):
    """Shared put helpers for publish helpers. Returns (put, uploaded, skipped, errors)."""
    uploaded: List[str] = []
    skipped: List[str] = []
    errors: List[str] = []

    def put(local: Path, blob_rel: str) -> None:
        if not local.exists() or not local.is_file():
            skipped.append(blob_rel)
            return
        blob_name = f"{out_pfx}{blob_rel.replace(chr(92), '/')}"
        try:
            _upload_file(cc, blob_name, local)
            uploaded.append(blob_rel)
        except Exception as e:
            errors.append(f"{blob_rel}: {e}")

    return put, uploaded, skipped, errors


def publish_replenishment_outputs_to_blob() -> Dict[str, Any]:
    """
    Mid-pipeline publish: enriched refill in prediction/current only (no local clear).
    Intentionally skips bulky history / legacy flat duplicates so Azure upload stays fast.
    """
    cc, container, out_pfx = _get_container()
    put, uploaded, skipped, errors = _blob_putters(cc, out_pfx)

    current_csv = OUTPUT_DIR / "prediction" / "current" / REFILL_CSV
    current_xlsx = current_csv.with_suffix(".xlsx")
    flat_csv = OUTPUT_DIR / REFILL_CSV
    flat_xlsx = OUTPUT_DIR / Path(REFILL_CSV).with_suffix(".xlsx").name

    # Prefer current (post-enrich). Sync small flat aliases from current when flat is missing/stale.
    if current_csv.exists():
        put(current_csv, f"prediction/current/{REFILL_CSV}")
        put(current_csv, f"prediction/{REFILL_CSV}")
        put(current_csv, REFILL_CSV)
        try:
            if (not flat_csv.exists()) or flat_csv.stat().st_size != current_csv.stat().st_size:
                import shutil

                shutil.copy2(current_csv, flat_csv)
        except Exception:
            pass
    elif flat_csv.exists():
        put(flat_csv, f"prediction/current/{REFILL_CSV}")
        put(flat_csv, f"prediction/{REFILL_CSV}")
        put(flat_csv, REFILL_CSV)

    if current_xlsx.exists():
        put(current_xlsx, f"prediction/{current_xlsx.name}")
        put(current_xlsx, current_xlsx.name)
    elif flat_xlsx.exists() and flat_xlsx.stat().st_size < 20_000_000:
        # Skip huge stale xlsx (previous full-catalog runs ~50MB+) during mid-publish
        put(flat_xlsx, f"prediction/{flat_xlsx.name}")
        put(flat_xlsx, flat_xlsx.name)
    else:
        skipped.append(f"prediction/{Path(REFILL_CSV).with_suffix('.xlsx').name} (skipped bulky/stale)")

    try:
        from warehouse_ledger import BLOB_LEDGER_REL, LEDGER_FILE

        put(LEDGER_FILE, BLOB_LEDGER_REL)
    except Exception as le:
        errors.append(f"warehouse ledger: {le}")

    result = {
        "ok": len(errors) == 0,
        "container": container,
        "prefix": out_pfx,
        "uploaded": uploaded,
        "skipped": skipped,
        "pruned": {},
        "errors": errors,
        "scope": "replenishment",
    }
    log.info(
        "Published replenishment %d file(s) to blob://%s/%s (skipped=%d errors=%d)",
        len(uploaded),
        container,
        out_pfx,
        len(skipped),
        len(errors),
    )

    # Build page disk cache from the new refill file so /replenishments serves instantly.
    if uploaded or current_csv.exists() or flat_csv.exists():
        try:
            import sys

            backend_dir = MERGED_BACKEND_DIR
            if str(backend_dir) not in sys.path:
                sys.path.insert(0, str(backend_dir))
            from api.routes.replenishments_recom import warm_replenishments_recom_cache

            warm = warm_replenishments_recom_cache(force_refresh=True)
            result["page_cache"] = warm
            log.info(
                "Replenishment page cache warmed: rows=%s elapsed=%ss",
                warm.get("rows"),
                warm.get("elapsed_s"),
            )
        except Exception as warm_exc:
            result["page_cache"] = {"ok": False, "error": str(warm_exc)}
            log.warning("Replenishment page cache warm failed: %s", warm_exc)

    return result


def publish_pipeline_outputs_to_blob() -> Dict[str, Any]:
    """Upload key artifacts to blob output/. Local files remain until clear_local_*."""
    cc, container, out_pfx = _get_container()
    put, uploaded, skipped, errors = _blob_putters(cc, out_pfx)
    pruned: Dict[str, List[str]] = {}

    # --- Combined masters ---
    put(OUTPUT_DIR / "COMBINED_SALES.csv", "COMBINED_SALES.csv")
    put(OUTPUT_DIR / "COMBINED_SALES.xlsx", "COMBINED_SALES.xlsx")
    put(OUTPUT_DIR / "COMBINED_TRANSFER.csv", "COMBINED_TRANSFER.csv")
    put(OUTPUT_DIR / "COMBINED_TRANSFER.xlsx", "COMBINED_TRANSFER.xlsx")
    put(OUTPUT_DIR / "COMBINED_SOH.csv", "COMBINED_SOH.csv")
    put(OUTPUT_DIR / "COMBINED_SOH.xlsx", "COMBINED_SOH.xlsx")
    put(OUTPUT_DIR / "SALES_REPORT.csv", "SALES_REPORT.csv")
    put(OUTPUT_DIR / "SALES_REPORT.xlsx", "SALES_REPORT.xlsx")
    put(OUTPUT_DIR / "weekly_aggregated_sales_new.csv", "weekly_aggregated_sales_new.csv")
    put(OUTPUT_DIR / "weekly_aggregated_sales_new.xlsx", "weekly_aggregated_sales_new.xlsx")

    soh_local = OUTPUT_DIR / "combined_soh"
    put(soh_local / "current" / "COMBINED_SOH.xlsx", "combined_soh/current/COMBINED_SOH.xlsx")
    put(soh_local / "current" / "COMBINED_SOH.csv", "combined_soh/current/COMBINED_SOH.csv")
    put(soh_local / "COMBINED_SOH.meta.json", "combined_soh/COMBINED_SOH.meta.json")
    hist_dir = soh_local / "history"
    if hist_dir.exists():
        for p in sorted(hist_dir.glob("COMBINED_SOH_*.xlsx")):
            put(p, f"combined_soh/history/{p.name}")
        for p in sorted(hist_dir.glob("COMBINED_SOH_*.csv")):
            put(p, f"combined_soh/history/{p.name}")
    pruned["combined_soh"] = _prune_dated_blobs(
        cc,
        f"{out_pfx}combined_soh/history/",
        basename="COMBINED_SOH",
        ext=".xlsx",
        keep=SOH_HISTORY_KEEP,
    )
    pruned["combined_soh_csv"] = _prune_dated_blobs(
        cc,
        f"{out_pfx}combined_soh/history/",
        basename="COMBINED_SOH",
        ext=".csv",
        keep=SOH_HISTORY_KEEP,
    )

    # --- Prediction / replenishment (same as mid-publish) ---
    put(OUTPUT_DIR / SELECTED_CSV, f"prediction/{SELECTED_CSV}")
    put(OUTPUT_DIR / SELECTED_CSV, SELECTED_CSV)  # flat alias too
    put(
        OUTPUT_DIR / "prediction" / "current" / REFILL_CSV,
        f"prediction/current/{REFILL_CSV}",
    )
    put(OUTPUT_DIR / REFILL_CSV, f"prediction/{REFILL_CSV}")
    put(OUTPUT_DIR / REFILL_CSV, REFILL_CSV)
    refill_xlsx = Path(REFILL_CSV).with_suffix(".xlsx").name
    put(OUTPUT_DIR / refill_xlsx, f"prediction/{refill_xlsx}")
    put(OUTPUT_DIR / refill_xlsx, refill_xlsx)
    pred_hist = OUTPUT_DIR / "prediction" / "history"
    if pred_hist.exists():
        for p in sorted(pred_hist.glob("*.csv")):
            put(p, f"prediction/history/{p.name}")
    pruned["prediction"] = _prune_dated_blobs(
        cc,
        f"{out_pfx}prediction/history/",
        basename=Path(REFILL_CSV).stem,
        ext=".csv",
        keep=PRED_HISTORY_KEEP,
    )

    # --- Warehouse WH ledger (remaining after prior refill allocations) ---
    try:
        from warehouse_ledger import BLOB_LEDGER_REL, LEDGER_FILE

        put(LEDGER_FILE, BLOB_LEDGER_REL)
    except Exception as le:
        errors.append(f"warehouse ledger: {le}")

    # --- Transfer ---
    put(TRANSFER_OUT / "potential_donors.csv", "transfer/potential_donors.csv")
    put(TRANSFER_OUT / "potential_donors.xlsx", "transfer/potential_donors.xlsx")
    put(TRANSFER_OUT / "potential_receivers.csv", "transfer/potential_receivers.csv")
    put(TRANSFER_OUT / "potential_receivers.xlsx", "transfer/potential_receivers.xlsx")
    put(TRANSFER_OUT / "recommended_transfers.csv", "transfer/recommended_transfers.csv")
    put(TRANSFER_OUT / "recommended_transfers.xlsx", "transfer/recommended_transfers.xlsx")
    put(TRANSFER_OUT / "recommended_transfers_grouped.csv", "transfer/recommended_transfers_grouped.csv")
    put(TRANSFER_OUT / "recommended_transfers_grouped.xlsx", "transfer/recommended_transfers_grouped.xlsx")
    put(TRANSFER_OUT / "recommended_transfers_sizewise.csv", "transfer/recommended_transfers_sizewise.csv")
    put(TRANSFER_OUT / "recommended_transfers_sizewise.xlsx", "transfer/recommended_transfers_sizewise.xlsx")
    put(TRANSFER_OUT / "broken_size_transfers.csv", "transfer/broken_size_transfers.csv")
    put(TRANSFER_OUT / "broken_size_transfers.xlsx", "transfer/broken_size_transfers.xlsx")
    put(TRANSFER_OUT / "final_transfer_sizewise.csv", "transfer/final_transfer_sizewise.csv")
    put(TRANSFER_OUT / "final_transfer_sizewise.xlsx", "transfer/final_transfer_sizewise.xlsx")
    put(
        OUTPUT_DIR / "transfer" / "current" / "final_transfer_sizewise.csv",
        "transfer/current/final_transfer_sizewise.csv",
    )
    put(
        OUTPUT_DIR / "transfer" / "current" / "final_transfer_sizewise.xlsx",
        "transfer/current/final_transfer_sizewise.xlsx",
    )
    # prefer enriched current if present else transfer/output
    xf_hist = OUTPUT_DIR / "transfer" / "history"
    if xf_hist.exists():
        for p in sorted(xf_hist.glob("final_transfer_sizewise_*.csv")):
            put(p, f"transfer/history/{p.name}")
    pruned["transfer"] = _prune_dated_blobs(
        cc,
        f"{out_pfx}transfer/history/",
        basename="final_transfer_sizewise",
        ext=".csv",
        keep=TRANSFER_HISTORY_KEEP,
    )

    # --- No-sales donors + store SOH (transfer UI / transfer scripts) ---
    put(OUTPUT_DIR / "soh_null_no_sales_transfer_days.csv", "soh_null_no_sales_transfer_days.csv")
    put(OUTPUT_DIR / "soh_null_no_sales_transfer_days.xlsx", "soh_null_no_sales_transfer_days.xlsx")
    put(OUTPUT_DIR / "store_soh_new.csv", "store_soh_new.csv")
    put(OUTPUT_DIR / "store_soh_new.xlsx", "store_soh_new.xlsx")
    put(
        OUTPUT_DIR / "SS26_weekly_predictions_formula_next_week_from_5w_avg.csv",
        "SS26_weekly_predictions_formula_next_week_from_5w_avg.csv",
    )
    put(
        OUTPUT_DIR / "SS26_weekly_predictions_formula_next_week_from_5w_avg.xlsx",
        "SS26_weekly_predictions_formula_next_week_from_5w_avg.xlsx",
    )
    put(
        OUTPUT_DIR / "weekly_aggregated_sales_new_5w_avg_sold_with_first_transfer.csv",
        "weekly_aggregated_sales_new_5w_avg_sold_with_first_transfer.csv",
    )
    put(
        OUTPUT_DIR / "weekly_aggregated_sales_new_5w_avg_sold_with_first_transfer.xlsx",
        "weekly_aggregated_sales_new_5w_avg_sold_with_first_transfer.xlsx",
    )

    result = {
        "ok": len(errors) == 0,
        "container": container,
        "prefix": out_pfx,
        "uploaded": uploaded,
        "skipped": skipped,
        "pruned": pruned,
        "errors": errors,
    }
    log.info(
        "Published %d file(s) to blob://%s/%s (skipped=%d errors=%d)",
        len(uploaded),
        container,
        out_pfx,
        len(skipped),
        len(errors),
    )
    return result


def clear_local_pipeline_outputs() -> Dict[str, Any]:
    """
    Remove local working outputs after blob publish.
    Pipeline can recreate them next run; SOH history lives on blob.
    """
    removed: List[str] = []
    errors: List[str] = []

    def _rm_tree(path: Path) -> None:
        if not path.exists():
            return
        try:
            if path.is_file():
                path.unlink(missing_ok=True)
                removed.append(str(path))
                return
            shutil.rmtree(path, ignore_errors=False)
            removed.append(str(path))
        except Exception as e:
            errors.append(f"{path}: {e}")

    # Wipe whole output_files tree (temp workspace)
    if OUTPUT_DIR.exists():
        for child in list(OUTPUT_DIR.iterdir()):
            _rm_tree(child)

    # Wipe transfer/output contents but keep folder
    if TRANSFER_OUT.exists():
        for child in list(TRANSFER_OUT.iterdir()):
            _rm_tree(child)
    else:
        TRANSFER_OUT.mkdir(parents=True, exist_ok=True)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    return {"ok": not errors, "removed": removed, "errors": errors}


def publish_and_clear(*, clear_local: bool = True) -> Dict[str, Any]:
    pub = publish_pipeline_outputs_to_blob()
    cleared: Optional[Dict[str, Any]] = None
    # Clear local if anything landed on blob (partial ok — missing ones stay local)
    if clear_local and pub.get("uploaded"):
        cleared = clear_local_pipeline_outputs()
    elif clear_local and not pub.get("uploaded"):
        log.warning("Skip local clear: nothing uploaded")
    return {"publish": pub, "cleared": cleared}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import json
    import sys

    cmd = (sys.argv[1] if len(sys.argv) > 1 else "publish").strip().lower()
    if cmd == "sync-soh":
        print(json.dumps(sync_combined_soh_from_blob(), indent=2, default=str))
    elif cmd == "clear":
        print(json.dumps(clear_local_pipeline_outputs(), indent=2, default=str))
    else:
        print(json.dumps(publish_and_clear(clear_local=True), indent=2, default=str))

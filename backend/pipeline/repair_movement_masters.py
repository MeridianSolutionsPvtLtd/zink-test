"""
Repair RTV / IBT masters when blob current/ is corrupt or missing real history.

Problem we hit:
  - RTV_DATA.csv got mixed with IBT-like STFQTY rows; real RETURN_QUANTITY
    only covered ~2 weeks, while Raw_Files still had March'25→till-date xlsx.
  - Till-date dumps are skipped by daily date_merge (is_history_or_bulk_dump),
    so they never replaced current/ unless we force a bulk baseline replace.

This module:
  1) Detects corrupt / thin RTV (or empty IBT) current
  2) Promotes the best Raw_Files till-date dump → current/
  3) Re-merges later daily Stock_Pulse files after the bulk filename date
"""
from __future__ import annotations

import io
import json
import logging
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("pipeline.repair_movement_masters")

PIPELINE_DIR = Path(__file__).resolve().parent
BACKEND_DIR = PIPELINE_DIR.parent

BULK_BASELINE_CATEGORIES = ("rtv-data", "ibt-data")


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(BACKEND_DIR / ".env")
    except ImportError:
        pass


def _env(*keys: str, default: str = "") -> str:
    for k in keys:
        v = os.environ.get(k)
        if v:
            return v
    return default


def _get_container():
    from azure.storage.blob import BlobServiceClient

    conn = _env("AZURE_STORAGE_CONNECTION_STRING")
    if not conn:
        raise RuntimeError("AZURE_STORAGE_CONNECTION_STRING not set")
    name = _env("AZURE_STORAGE_CONTAINER", "BLOB_CONTAINER", default="zinc-data")
    cc = BlobServiceClient.from_connection_string(conn).get_container_client(name)
    return cc, name


def _norm_prefix(prefix: str) -> str:
    p = (prefix or "").strip().replace("\\", "/")
    if p and not p.endswith("/"):
        p += "/"
    return p.lstrip("/")


def rtv_bytes_look_corrupt(data: bytes, name: str = "RTV_DATA.csv") -> Tuple[bool, Dict[str, Any]]:
    """
    True when file is mostly transfer-shaped (STFQTY) with little RETURN_QUANTITY,
    or too thin to be a Mar'25→now master.
    """
    import pandas as pd

    info: Dict[str, Any] = {"bytes": len(data), "name": name}
    try:
        lower = name.lower()
        if lower.endswith((".xlsx", ".xls")) or data[:2] == b"PK":
            peek = pd.read_excel(io.BytesIO(data), header=None, nrows=8, dtype=str)
            hdr = None
            for i in range(len(peek)):
                cells = {str(c).strip().upper() for c in peek.iloc[i].tolist()}
                if "RETURN_QUANTITY" in cells or "BARCODE" in cells:
                    hdr = i
                    break
            if hdr is None:
                info["reason"] = "no_rtv_header"
                return True, info
            df = pd.read_excel(io.BytesIO(data), header=hdr, dtype=str, nrows=5000)
        else:
            # CSV may start with header row 0 or junk — try RETURN_QUANTITY sniff
            df = pd.read_csv(io.BytesIO(data), dtype=str, nrows=5000, low_memory=False)
        df.columns = [str(c).strip() for c in df.columns]
        cols_u = {c.upper() for c in df.columns}
        has_ret_col = "RETURN_QUANTITY" in cols_u
        has_stf = "STFQTY" in cols_u
        ret = pd.to_numeric(df.get("RETURN_QUANTITY"), errors="coerce") if has_ret_col else None
        stf = pd.to_numeric(df.get("STFQTY"), errors="coerce") if has_stf else None
        ret_nz = int((ret.fillna(0).abs() > 0).sum()) if ret is not None else 0
        stf_nz = int((stf.fillna(0).abs() > 0).sum()) if stf is not None else 0
        info.update(
            {
                "has_return_quantity_col": has_ret_col,
                "ret_nonzero_sample": ret_nz,
                "stf_nonzero_sample": stf_nz,
                "sample_rows": len(df),
            }
        )
        if not has_ret_col:
            info["reason"] = "missing_RETURN_QUANTITY_column"
            return True, info
        # Polluted: more STFQTY than returns in sample
        if stf_nz > 0 and ret_nz < max(50, int(0.2 * stf_nz)):
            info["reason"] = "ibt_shaped_pollution"
            return True, info
        if ret_nz < 20 and len(data) < 8_000_000:
            info["reason"] = "too_few_real_rtv_rows"
            return True, info
        info["reason"] = "ok"
        return False, info
    except Exception as exc:
        info["reason"] = f"read_error:{exc}"
        return True, info


def current_rtv_is_corrupt(cc, input_prefix: str = "input/") -> Tuple[bool, Dict[str, Any]]:
    from azure.core.exceptions import ResourceNotFoundError

    prefix = _norm_prefix(input_prefix) + "RTV Data/current/"
    # Prefer csv canonical, then xlsx
    candidates = [
        f"{prefix}RTV_DATA.csv",
        f"{prefix}RTV_DATA.xlsx",
    ]
    for blob_name in candidates:
        try:
            data = cc.download_blob(blob_name).readall()
        except ResourceNotFoundError:
            continue
        except Exception as exc:
            return True, {"blob": blob_name, "reason": f"download_error:{exc}"}
        bad, info = rtv_bytes_look_corrupt(data, Path(blob_name).name)
        info["blob"] = blob_name
        return bad, info
    return True, {"reason": "no_current_rtv"}


def _list_raw_category(cc, raw_prefix: str, category_id: str) -> List[Dict[str, Any]]:
    from pipeline.ginesys_raw import ginesys_category, is_history_or_bulk_dump, parse_file_date

    out: List[Dict[str, Any]] = []
    for b in cc.list_blobs(name_starts_with=raw_prefix):
        name = Path(b.name).name
        if not name.lower().endswith((".xlsx", ".xls", ".csv")):
            continue
        cid = ginesys_category(name)
        if cid != category_id:
            continue
        out.append(
            {
                "blob": b.name,
                "filename": name,
                "size": int(getattr(b, "size", 0) or 0),
                "last_modified": str(getattr(b, "last_modified", "") or ""),
                "file_date": parse_file_date(name).isoformat() if parse_file_date(name) else None,
                "is_bulk": is_history_or_bulk_dump(name),
            }
        )
    return out


def best_bulk_raw(files: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Largest till-date / march-to dump wins."""
    bulks = [f for f in files if f.get("is_bulk")]
    if not bulks:
        return None
    # Prefer names containing till date
    till = [f for f in bulks if "till" in (f.get("filename") or "").lower()]
    pool = till or bulks
    return max(
        pool,
        key=lambda r: (
            int(r.get("size") or 0),
            str(r.get("file_date") or ""),
            str(r.get("last_modified") or ""),
        ),
    )


def _xlsx_rtv_to_csv_bytes(data: bytes) -> Tuple[bytes, Dict[str, Any]]:
    """Normalize Ginesys RTV workbook → UTF-8 CSV for DM loader."""
    import pandas as pd

    peek = pd.read_excel(io.BytesIO(data), header=None, nrows=15, dtype=str)
    hdr = None
    for i in range(len(peek)):
        cells = {str(c).strip().upper() for c in peek.iloc[i].tolist()}
        if "RETURN_QUANTITY" in cells and ("BARCODE" in cells or "CUSTOMER SITE NAME" in cells):
            hdr = i
            break
    if hdr is None:
        raise ValueError("RTV workbook: RETURN_QUANTITY header not found")
    df = pd.read_excel(io.BytesIO(data), header=hdr, dtype=str)
    df.columns = [str(c).strip() for c in df.columns]
    # Drop fully empty
    df = df.dropna(how="all")
    if "SALES RETURN DATE" in df.columns:
        dt = pd.to_datetime(df["SALES RETURN DATE"], errors="coerce", format="mixed")
        df = df[dt.notna()].copy()
        df["SALES RETURN DATE"] = dt[dt.notna()].dt.strftime("%Y-%m-%d")
        dmin, dmax = str(df["SALES RETURN DATE"].min()), str(df["SALES RETURN DATE"].max())
    else:
        dmin = dmax = None
    ret = pd.to_numeric(df.get("RETURN_QUANTITY"), errors="coerce").fillna(0).abs()
    meta = {
        "rows": int(len(df)),
        "return_qty_sum": float(ret.sum()),
        "min_str": dmin,
        "max_str": dmax,
        "header_row": hdr,
    }
    out = df.to_csv(index=False).encode("utf-8")
    return out, meta


def _upload_rtv_current(cc, csv_bytes: bytes, bounds: Dict[str, Any], input_prefix: str = "input/") -> None:
    from azure.storage.blob import ContentSettings

    prefix = _norm_prefix(input_prefix) + "RTV Data/current/"
    cc.upload_blob(
        f"{prefix}RTV_DATA.csv",
        csv_bytes,
        overwrite=True,
        content_settings=ContentSettings(content_type="text/csv"),
    )
    meta = {
        "date_column": "SALES RETURN DATE",
        "min_str": bounds.get("min_str"),
        "max_str": bounds.get("max_str"),
        "rows_with_date": bounds.get("rows"),
        "total_rows": bounds.get("rows"),
        "assumed_covered_through": bounds.get("max_str"),
        "merged_file_dates": [],
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "note": bounds.get("note") or "bulk baseline replace from Raw_Files till-date",
    }
    cc.upload_blob(
        f"{prefix}RTV_DATA.date_bounds.json",
        json.dumps(meta, indent=2).encode("utf-8"),
        overwrite=True,
        content_settings=ContentSettings(content_type="application/json"),
    )
    # Drop stale xlsx current so sync/DM prefer CSV
    try:
        cc.delete_blob(f"{prefix}RTV_DATA.xlsx")
    except Exception:
        pass


def _sync_rtv_local(csv_bytes: bytes, bounds: Dict[str, Any]) -> None:
    local = BACKEND_DIR / "pipeline" / "input_files"
    (local / "RTV_DATA.csv").write_bytes(csv_bytes)
    meta = {
        "date_column": "SALES RETURN DATE",
        "min_str": bounds.get("min_str"),
        "max_str": bounds.get("max_str"),
        "rows_with_date": bounds.get("rows"),
        "total_rows": bounds.get("rows"),
        "assumed_covered_through": bounds.get("max_str"),
        "merged_file_dates": [],
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "note": bounds.get("note") or "bulk baseline replace",
    }
    (local / "RTV_DATA.date_bounds.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    mirror = local / "RTV Data" / "current"
    mirror.mkdir(parents=True, exist_ok=True)
    (mirror / "RTV_DATA.csv").write_bytes(csv_bytes)
    (mirror / "RTV_DATA.date_bounds.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")


def repair_rtv_from_raw(*, dry_run: bool = False, force: bool = False) -> Dict[str, Any]:
    """
    If current RTV is corrupt (or force=True), replace from best Raw_Files till-date dump,
    then merge daily Raw_Files dated after that dump's filename date.
    """
    _load_dotenv()
    from pipeline.ginesys_raw import fetch_raw_to_incoming
    from pipeline.merge_category_inputs import merge_all_categories, sync_current_to_local

    cc, container = _get_container()
    input_prefix = _norm_prefix(_env("AZURE_BLOB_INPUT_PREFIX", default="input/"))
    raw_prefix = _norm_prefix(_env("AZURE_BLOB_RAW_PREFIX", default="Raw_Files/"))

    corrupt, diagnose = current_rtv_is_corrupt(cc, input_prefix)
    result: Dict[str, Any] = {
        "ok": True,
        "category_id": "rtv-data",
        "container": container,
        "corrupt": corrupt,
        "diagnose": diagnose,
        "dry_run": dry_run,
        "replaced": False,
        "daily_merge": None,
    }
    if not corrupt and not force:
        result["skipped"] = True
        result["reason"] = "current_rtv_ok"
        return result

    files = _list_raw_category(cc, raw_prefix, "rtv-data")
    bulk = best_bulk_raw(files)
    if not bulk:
        # Fall back to local xlsx if present
        local_xlsx = (
            BACKEND_DIR / "pipeline" / "input_files" / "RTV Data" / "current" / "RTV_DATA.xlsx"
        )
        if local_xlsx.exists() and local_xlsx.stat().st_size > 1_000_000:
            data = local_xlsx.read_bytes()
            bulk = {
                "blob": f"local:{local_xlsx}",
                "filename": local_xlsx.name,
                "size": local_xlsx.stat().st_size,
                "file_date": "2026-09-09",
                "is_bulk": True,
            }
            result["source"] = "local_xlsx"
        else:
            result["ok"] = False
            result["error"] = "no_till_date_rtv_in_Raw_Files"
            return result
    else:
        data = cc.download_blob(bulk["blob"]).readall()
        result["source"] = bulk["blob"]

    result["bulk"] = {k: bulk.get(k) for k in ("blob", "filename", "size", "file_date")}
    csv_bytes, meta = _xlsx_rtv_to_csv_bytes(data)
    meta["note"] = f"bulk baseline from {bulk.get('filename')}"
    result["normalized"] = meta

    if dry_run:
        result["would_write_bytes"] = len(csv_bytes)
        return result

    _upload_rtv_current(cc, csv_bytes, meta, input_prefix=input_prefix)
    _sync_rtv_local(csv_bytes, meta)
    result["replaced"] = True
    log.info(
        "RTV current replaced: rows=%s %s..%s from %s",
        meta.get("rows"),
        meta.get("min_str"),
        meta.get("max_str"),
        bulk.get("filename"),
    )

    # Merge daily reports after bulk filename date (Stock_Pulse etc.)
    bulk_fd = bulk.get("file_date")
    try:
        bulk_date = date.fromisoformat(str(bulk_fd)) if bulk_fd else None
    except ValueError:
        bulk_date = None

    # Force re-fetch: temporarily the coverage max is bulk content max;
    # daily files with later filename dates should plan.
    fetch = fetch_raw_to_incoming(dry_run=False, only_ids=["rtv-data"])
    result["daily_fetch"] = {
        "copied": len(fetch.get("copied") or []),
        "touched": fetch.get("touched_categories"),
        "errors": fetch.get("errors") or [],
    }
    if fetch.get("touched_categories"):
        merge = merge_all_categories(
            dry_run=False, keep_incoming=False, only_ids=["rtv-data"]
        )
        result["daily_merge"] = {
            "ok": merge.get("ok"),
            "merged_count": merge.get("merged_count"),
            "skipped_count": merge.get("skipped_count"),
            "warnings": merge.get("warnings") or [],
        }
        sync_current_to_local(dry_run=False)
    result["bulk_filename_date"] = bulk_fd
    result["bulk_content_max"] = meta.get("max_str")
    return result


def ensure_movement_masters(*, force_rtv: bool = False) -> Dict[str, Any]:
    """Entry point for auto_daily_run — repair RTV (and log IBT gaps)."""
    out: Dict[str, Any] = {"ok": True, "rtv": None, "ibt_note": None}
    try:
        out["rtv"] = repair_rtv_from_raw(force=force_rtv)
        if not out["rtv"].get("ok"):
            out["ok"] = False
    except Exception as exc:
        log.exception("RTV repair failed")
        out["ok"] = False
        out["rtv"] = {"ok": False, "error": str(exc)}

    # IBT: ensure category is mapped; note coverage only (repair optional)
    try:
        cc, _ = _get_container()
        raw_prefix = _norm_prefix(_env("AZURE_BLOB_RAW_PREFIX", default="Raw_Files/"))
        ibt_files = _list_raw_category(cc, raw_prefix, "ibt-data")
        out["ibt_note"] = {
            "raw_file_count": len(ibt_files),
            "bulk": best_bulk_raw(ibt_files),
            "hint": "IBT till-date dumps auto-plan via ginesys_raw bulk baseline when mapped",
        }
    except Exception as exc:
        out["ibt_note"] = {"error": str(exc)}
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    import argparse

    p = argparse.ArgumentParser(description="Repair RTV master from Raw_Files till-date")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true")
    args = p.parse_args()
    result = repair_rtv_from_raw(dry_run=args.dry_run, force=args.force)
    print(json.dumps(result, indent=2, default=str))
    raise SystemExit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()

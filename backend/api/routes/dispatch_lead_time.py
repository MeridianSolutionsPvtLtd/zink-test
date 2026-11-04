"""
Inventory Dispatch Timing API
Calculates dispatch velocity from GRC Inward lots to Store Dispatch.

Grain: one row per SKU × inward_date (lot). Store dispatches are allocated
FIFO across lots (oldest open lot first; dispatch date must be >= lot inward).

Status rules (per lot):
- Stuck (Red): age > 90 days AND lot remaining ≥ 5% of lot inward
- Slow start (Orange): < 50% of lot qty dispatched within 7 days of that lot
- Behind (Yellow): < 80% of lot qty dispatched within 15 days of that lot
- On track (Green): none of the above
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from fastapi import APIRouter

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/inventory-analytics", tags=["Inventory Analytics - Dispatch Lead Time"])

_BACKEND = Path(__file__).resolve().parents[2]
_INPUT_DIR = _BACKEND / "pipeline" / "input_files"
_CACHE_DIR = _BACKEND / "data" / "api_cache"

_CACHE_LOCK = threading.RLock()
_MEMORY_LEAD_TIME: tuple[str, pd.DataFrame] | None = None
_REPORT_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}

_BLOB_GRC_CANDIDATES = (
    "GRC Report/current/GRC_REPORT.csv",
    "GRC Report/current/GRC_REPORT.xlsx",
)
_BLOB_LFS_INVOICE_CANDIDATES = (
    "Transfer - (PT, SSL, Centro, Iconic)/current/SALES_INVOICE_TRACKING.csv",
    "Transfer - (PT, SSL, Centro, Iconic)/current/SALES_INVOICE_TRACKING.xlsx",
    "Transfer-Master (PT, SSL, Centro, Iconic)/current/SALES_INVOICE_TRACKING_MASTER.csv",
)
_BLOB_EBO_INVOICE_CANDIDATES = (
    "Transfer - EBO/current/EBO_INVOICE_TRACKING.csv",
    "Transfer - EBO/current/EBO_INVOICE_TRACKING.xlsx",
)
_BLOB_WH_SOH_CANDIDATES = (
    "Warehouse SOH/current/WAREHOUSE_SOH.csv",
    "Warehouse SOH/current/WAREHOUSE_SOH.xlsx",
)


def _path_freshness(path: Path | None) -> tuple[int, int]:
    if path is None or not path.exists():
        return (0, 0)
    try:
        st = path.stat()
        return (int(st.st_mtime_ns), int(st.st_size))
    except OSError:
        return (0, 0)


def _best_existing(*paths: Path | None) -> Path | None:
    found = [p for p in paths if p is not None and p.exists() and p.stat().st_size > 0]
    if not found:
        return None
    return max(found, key=_path_freshness)


def _sidecar_max_date(path: Path | None) -> str | None:
    """Read *.date_bounds.json next to an input file (pipeline sync metadata)."""
    if path is None:
        return None
    for cand in (
        path.with_suffix(path.suffix + ".date_bounds.json"),
        path.parent / f"{path.name}.date_bounds.json",
        path.parent / f"{path.stem}.date_bounds.json",
        _INPUT_DIR / f"{path.stem}.date_bounds.json",
    ):
        try:
            if not cand.exists():
                continue
            import json

            data = json.loads(cand.read_text(encoding="utf-8"))
            mx = str(data.get("max_str") or "").strip()
            if mx:
                return mx[:10]
        except Exception:
            continue
    return None


def _local_looks_stale(path: Path | None, *, max_age_days: int = 5) -> bool:
    """True when date_bounds max is older than max_age_days (host sync lag)."""
    mx = _sidecar_max_date(path)
    if not mx:
        return False
    try:
        from datetime import date, datetime, timedelta

        bound = datetime.strptime(mx[:10], "%Y-%m-%d").date()
        return bound < (date.today() - timedelta(days=max_age_days))
    except Exception:
        return False


def _resolve_input_path(
    local_names: tuple[str, ...],
    blob_candidates: tuple[str, ...],
    current_rel: tuple[str, ...] = (),
) -> Path | None:
    """Pick freshest among flat local + */current/ mirror; blob only if missing/stale.

    Hosted App Service often keeps a stale flat copy under input_files/.
    Prefer newer */current/ mirrors; pull Azure only when local is absent or
    date_bounds show the file is stale (avoids 400MB re-download every restart).
    """
    locals_: list[Path] = []
    for name in local_names:
        p = _INPUT_DIR / name
        if p.exists() and p.stat().st_size > 0:
            locals_.append(p)
    for rel in current_rel:
        p = _INPUT_DIR / Path(rel)
        if p.exists() and p.stat().st_size > 0:
            locals_.append(p)

    local_best = _best_existing(*locals_)
    need_blob = local_best is None or _local_looks_stale(local_best)
    if not need_blob:
        return local_best

    blob_path: Path | None = None
    try:
        from api.routes.sales_dashboard import _blob_store

        store = _blob_store()
        if store is not None:
            blob_path = store.ensure_input_candidates(blob_candidates)
    except Exception as exc:
        logger.warning("Blob input resolve failed for %s: %s", local_names[0] if local_names else "?", exc)

    return _best_existing(local_best, blob_path)


def _local_input_path(names: tuple[str, ...], current_rel: tuple[str, ...] = ()) -> Path | None:
    paths: list[Path] = []
    for name in names:
        p = _INPUT_DIR / name
        if p.exists() and p.stat().st_size > 0:
            paths.append(p)
    for rel in current_rel:
        p = _INPUT_DIR / Path(rel)
        if p.exists() and p.stat().st_size > 0:
            paths.append(p)
    return _best_existing(*paths)


_GRC_CURRENT = ("GRC Report/current/GRC_REPORT.csv", "GRC Report/current/GRC_REPORT.xlsx")
_LFS_CURRENT = (
    "Transfer - (PT, SSL, Centro, Iconic)/current/SALES_INVOICE_TRACKING.csv",
    "Transfer - (PT, SSL, Centro, Iconic)/current/SALES_INVOICE_TRACKING.xlsx",
)
_EBO_CURRENT = (
    "Transfer - EBO/current/EBO_INVOICE_TRACKING.csv",
    "Transfer - EBO/current/EBO_INVOICE_TRACKING.xlsx",
)


def _lead_time_cache_key(grc_path: Path, lfs_path: Path | None, ebo_path: Path | None) -> str:
    return (
        f"lot_v2_"
        f"{grc_path.stat().st_mtime_ns}_"
        f"{lfs_path.stat().st_mtime_ns if lfs_path and lfs_path.exists() else 0}_"
        f"{ebo_path.stat().st_mtime_ns if ebo_path and ebo_path.exists() else 0}"
    )


def lead_time_parquet_max_date():
    """Max inward_date on the prepared lead-time parquet, or None."""
    disk_file = _CACHE_DIR / "dispatch_lead_time.parquet"
    if not disk_file.exists() or disk_file.stat().st_size <= 0:
        return None
    try:
        df = pd.read_parquet(disk_file, columns=["inward_date"])
        mx = pd.to_datetime(df["inward_date"], errors="coerce").max()
        if pd.isna(mx):
            return None
        return mx.date()
    except Exception:
        return None


def dispatch_page_cache_stale(payload: dict | None, *, max_age_days: int = 5) -> bool:
    """True when the JSON page still stops before the parquet (host stuck on an old inward max)."""
    if not isinstance(payload, dict):
        return False
    raw = payload.get("dataThrough") or (payload.get("summary") or {}).get("dataThrough")
    if not raw:
        return True
    try:
        from datetime import date, datetime, timedelta

        bound = datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()
    except Exception:
        return False
    if bound < (date.today() - timedelta(days=max_age_days)):
        return True
    parquet_max = lead_time_parquet_max_date()
    return bool(parquet_max and bound < parquet_max)


def _lead_time_df_stale(df: pd.DataFrame | None, *, max_age_days: int = 5) -> bool:
    """True when cached inward max is older than max_age_days (host stuck on Oct-26 etc.)."""
    if df is None or getattr(df, "empty", True) or "inward_date" not in df.columns:
        return False
    try:
        from datetime import date, timedelta

        mx = pd.to_datetime(df["inward_date"], errors="coerce").max()
        if pd.isna(mx):
            return False
        return mx.date() < (date.today() - timedelta(days=max_age_days))
    except Exception:
        return False


_BLOB_LEAD_TIME_PQ = "cache/api_cache/dispatch_lead_time.parquet"
_BLOB_LEAD_TIME_KEY = "cache/api_cache/dispatch_lead_time.key"


def publish_dispatch_lead_time_to_blob() -> dict:
    """Upload tiny lead-time parquet so hosted Inventory Analytics opens without GRC rebuild."""
    disk_file = _CACHE_DIR / "dispatch_lead_time.parquet"
    meta_key = _CACHE_DIR / "dispatch_lead_time.key"
    if not disk_file.exists() or disk_file.stat().st_size <= 0:
        return {"ok": False, "error": "missing local dispatch_lead_time.parquet"}
    try:
        from api.blob_store import upload_local_file_with_retry
        from api.routes.sales_dashboard import _blob_store

        store = _blob_store()
        if store is None:
            return {"ok": False, "error": "blob store unavailable"}
        upload_local_file_with_retry(
            store.container,
            blob_name=_BLOB_LEAD_TIME_PQ,
            path=disk_file,
            content_type="application/octet-stream",
            attempts=3,
            timeout_s=120,
        )
        if meta_key.exists():
            upload_local_file_with_retry(
                store.container,
                blob_name=_BLOB_LEAD_TIME_KEY,
                path=meta_key,
                content_type="text/plain",
                attempts=2,
                timeout_s=60,
            )
        return {"ok": True, "blob": _BLOB_LEAD_TIME_PQ, "bytes": disk_file.stat().st_size}
    except Exception as exc:
        logger.warning("Dispatch lead-time blob publish failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def _pull_dispatch_lead_time_from_blob() -> bool:
    disk_file = _CACHE_DIR / "dispatch_lead_time.parquet"
    meta_key = _CACHE_DIR / "dispatch_lead_time.key"
    try:
        from api.routes.sales_dashboard import _blob_store

        store = _blob_store()
        if store is None:
            return False
        local = store._ensure_local_file(_BLOB_LEAD_TIME_PQ)
        if local is None or not local.exists() or local.stat().st_size <= 0:
            return False
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        if local.resolve() != disk_file.resolve():
            disk_file.write_bytes(local.read_bytes())
        meta = store._ensure_local_file(_BLOB_LEAD_TIME_KEY)
        if meta is not None and meta.exists() and meta.stat().st_size > 0:
            if meta.resolve() != meta_key.resolve():
                meta_key.write_bytes(meta.read_bytes())
        elif not meta_key.exists():
            meta_key.write_text("blob", encoding="utf-8")
        logger.info(
            "Dispatch lead-time pulled from blob (%s bytes)",
            disk_file.stat().st_size,
        )
        return disk_file.exists() and disk_file.stat().st_size > 0
    except Exception as exc:
        logger.warning("Dispatch lead-time blob pull failed: %s", exc)
        return False


def _compute_lead_time_dataset(*, force_refresh: bool = False) -> pd.DataFrame:
    """Compute consolidated Inward vs Dispatch Lead Time DataFrame."""
    global _MEMORY_LEAD_TIME

    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    disk_file = _CACHE_DIR / "dispatch_lead_time.parquet"
    meta_key = _CACHE_DIR / "dispatch_lead_time.key"

    # Hosted cold start: pull prepared parquet before downloading GRC/invoices.
    if not force_refresh and (not disk_file.exists() or disk_file.stat().st_size <= 0):
        _pull_dispatch_lead_time_from_blob()

    # Hot path: freshest local/current copies + memory/disk.
    # Blob pull only on cache miss / stale bounds (see resolve below).
    local_grc = _local_input_path(("GRC_REPORT.csv", "GRC_REPORT.xlsx"), _GRC_CURRENT)
    local_lfs = _local_input_path(
        ("SALES_INVOICE_TRACKING.csv", "SALES_INVOICE_TRACKING.xlsx"), _LFS_CURRENT
    )
    local_ebo = _local_input_path(
        ("EBO_INVOICE_TRACKING.csv", "EBO_INVOICE_TRACKING.xlsx"), _EBO_CURRENT
    )
    local_key = (
        _lead_time_cache_key(local_grc, local_lfs, local_ebo) if local_grc is not None else None
    )
    inputs_stale = _local_looks_stale(local_grc) or _local_looks_stale(local_lfs)

    prev_good: pd.DataFrame | None = None
    with _CACHE_LOCK:
        if _MEMORY_LEAD_TIME is not None and not _MEMORY_LEAD_TIME[1].empty:
            prev_good = _MEMORY_LEAD_TIME[1]
    if prev_good is None and disk_file.exists():
        try:
            loaded = pd.read_parquet(disk_file)
            if loaded is not None and not loaded.empty:
                prev_good = loaded
        except Exception:
            pass

    if force_refresh:
        # Invalidate memory only — keep disk until a successful rebuild writes a new file.
        with _CACHE_LOCK:
            _MEMORY_LEAD_TIME = None
            _REPORT_CACHE.clear()
        logger.info("Dispatch lead-time force_refresh — rebuilding (disk kept as fallback)")

    with _CACHE_LOCK:
        if not force_refresh and _MEMORY_LEAD_TIME is not None:
            mem_key, mem_df = _MEMORY_LEAD_TIME
            if (local_key is None or mem_key == local_key) and not inputs_stale and not _lead_time_df_stale(mem_df):
                return mem_df
            if inputs_stale or _lead_time_df_stale(mem_df):
                logger.warning(
                    "Dispatch lead-time memory cache stale (inputs_stale=%s) — rebuilding",
                    inputs_stale,
                )
                _MEMORY_LEAD_TIME = None

    if not force_refresh and disk_file.exists() and meta_key.exists():
        saved_key = meta_key.read_text(encoding="utf-8").strip()
        if local_key is None or saved_key == local_key:
            try:
                df = pd.read_parquet(disk_file)
                if inputs_stale or _lead_time_df_stale(df):
                    logger.warning(
                        "Dispatch lead-time disk cache stale (max inward old / bounds stale) — rebuilding from blob"
                    )
                else:
                    with _CACHE_LOCK:
                        _MEMORY_LEAD_TIME = (saved_key or local_key or "disk", df)
                    logger.info("Dispatch lead-time disk cache hit (%d rows)", len(df))
                    return df
            except Exception as exc:
                logger.warning("Parquet read error in dispatch lead time: %s", exc)

    # On rebuild: also compare Azure blob cache — hosted stale flat files no longer win.
    grc_path = _resolve_input_path(
        ("GRC_REPORT.csv", "GRC_REPORT.xlsx"), _BLOB_GRC_CANDIDATES, _GRC_CURRENT
    ) or local_grc
    lfs_path = _resolve_input_path(
        ("SALES_INVOICE_TRACKING.csv", "SALES_INVOICE_TRACKING.xlsx"),
        _BLOB_LFS_INVOICE_CANDIDATES,
        _LFS_CURRENT,
    ) or local_lfs
    ebo_path = _resolve_input_path(
        ("EBO_INVOICE_TRACKING.csv", "EBO_INVOICE_TRACKING.xlsx"),
        _BLOB_EBO_INVOICE_CANDIDATES,
        _EBO_CURRENT,
    ) or local_ebo
    # WH SOH only needed when rebuilding (not on cache hit)
    _resolve_input_path(("WAREHOUSE_SOH.csv", "WAREHOUSE_SOH.xlsx"), _BLOB_WH_SOH_CANDIDATES)

    if grc_path is None or not grc_path.exists():
        logger.warning("Dispatch lead-time: GRC missing — serving previous cache if any")
        return prev_good if prev_good is not None else pd.DataFrame()

    key = _lead_time_cache_key(grc_path, lfs_path, ebo_path)

    with _CACHE_LOCK:
        if _MEMORY_LEAD_TIME and _MEMORY_LEAD_TIME[0] == key:
            return _MEMORY_LEAD_TIME[1]

    if disk_file.exists() and meta_key.exists() and meta_key.read_text(encoding="utf-8").strip() == key:
        try:
            df = pd.read_parquet(disk_file)
            with _CACHE_LOCK:
                _MEMORY_LEAD_TIME = (key, df)
            return df
        except Exception as exc:
            logger.warning("Parquet read error in dispatch lead time: %s", exc)

    # 1. Load GRC (Inward Register)
    if grc_path.suffix.lower() in {".xlsx", ".xls"}:
        grc = pd.read_excel(grc_path, engine="openpyxl")
    else:
        grc = pd.read_csv(grc_path, low_memory=False, encoding="latin1")
    # Strictly filter for Material type = Finished Goods only
    mat_col = next((c for c in grc.columns if "material" in c.lower() and "type" in c.lower()), None)
    if mat_col:
        grc = grc[grc[mat_col].astype(str).str.strip().str.lower() == "finished goods"]
    
    grc["inward_date"] = pd.to_datetime(grc.get("RECEIVE_DATE"), format="mixed", errors="coerce").dt.normalize()
    grc["inward_qty"] = pd.to_numeric(grc.get("RECEIVED_QUANTITY"), errors="coerce").fillna(0)
    grc["sku"] = grc.get("Sku Code", "").astype(str).str.strip().str.upper()
    grc["barcode"] = grc.get("BARCODE", "").astype(str).str.split(".").str[0].str.strip()
    grc["ref_no"] = grc.get("Reference Number", "").astype(str).replace({"nan": "", "None": "", "<NA>": ""}).str.strip()
    grc["section"] = grc.get("Section", "").astype(str).replace({"nan": "", "None": "", "<NA>": ""}).str.strip()
    grc["season"] = grc.get("Season", "").astype(str).replace({"nan": "", "None": "", "<NA>": ""}).str.strip()
    grc["supplier"] = grc.get("SUPPLIER_NAME", "").astype(str).replace({"nan": "", "None": "", "<NA>": ""}).str.strip()
    grc["rate"] = pd.to_numeric(grc.get("RECEIVED_RATE"), errors="coerce").fillna(0.0)

    grc = grc[
        (grc["sku"].str.len() > 0)
        & (~grc["sku"].isin(["NAN", "NONE", "NULL"]))
        & grc["inward_date"].notna()
        & (grc["inward_qty"] > 0)
    ]
    if grc.empty:
        return pd.DataFrame()

    # Aggregate GRC per SKU × inward_date (lot)
    lot_grc = (
        grc.groupby(["sku", "inward_date"], as_index=False)
        .agg(
            inward_qty=("inward_qty", "sum"),
            ref_no=("ref_no", "first"),
            section=("section", "first"),
            season=("season", "first"),
            supplier=("supplier", "first"),
            cost_rate=("rate", "mean"),
            barcodes=("barcode", lambda s: list({str(x) for x in s if str(x).strip() and str(x) not in ("nan", "None")})),
        )
    )

    # 2. Load Dispatches (LFS + EBO)
    disp_records = []
    if lfs_path is not None and lfs_path.exists():
        try:
            if lfs_path.suffix.lower() in {".xlsx", ".xls"}:
                lfs = pd.read_excel(lfs_path, engine="openpyxl")
            else:
                lfs = pd.read_csv(lfs_path, low_memory=False, encoding="latin1")
            # Prefer DC_DATE (warehouse dispatch) over invoice/document date
            dt_col = next(
                (
                    c
                    for c in ("DC_DATE", "INVOICE_DATE", "DOCUMENT DATE", "DATE")
                    if c in lfs.columns
                ),
                None,
            )
            lfs_dt = (
                pd.to_datetime(lfs[dt_col], format="mixed", errors="coerce")
                if dt_col
                else pd.Series(pd.NaT, index=lfs.index)
            )
            lfs_q = pd.to_numeric(lfs.get("INVOICE_QUANTITY", 0), errors="coerce").fillna(0)
            lfs_bc = lfs["BARCODE"].astype(str).str.split(".").str[0].str.strip() if "BARCODE" in lfs.columns else pd.Series("", index=lfs.index)
            sku_col = "CATEGORY3" if "CATEGORY3" in lfs.columns else ("CATEGORY6" if "CATEGORY6" in lfs.columns else "SKU")
            lfs_sku = lfs[sku_col].astype(str).str.strip().str.upper() if sku_col in lfs.columns else pd.Series("", index=lfs.index)
            lfs_df = pd.DataFrame({"sku": lfs_sku, "barcode": lfs_bc, "dispatch_date": lfs_dt, "dispatch_qty": lfs_q})
            disp_records.append(lfs_df.dropna(subset=["dispatch_date"]))
        except Exception as exc:
            logger.warning("Failed to load LFS dispatch tracking: %s", exc)

    if ebo_path is not None and ebo_path.exists():
        try:
            if ebo_path.suffix.lower() in {".xlsx", ".xls"}:
                ebo = pd.read_excel(ebo_path, engine="openpyxl")
            else:
                ebo = pd.read_csv(ebo_path, low_memory=False, encoding="latin1")
            dt_col = "Transfer Date" if "Transfer Date" in ebo.columns else "Date"
            ebo_dt = pd.to_datetime(ebo[dt_col], format="mixed", errors="coerce") if dt_col in ebo.columns else pd.Series(pd.NaT, index=ebo.index)
            q_col = "Quantity" if "Quantity" in ebo.columns else ("Transfer Qty" if "Transfer Qty" in ebo.columns else "Qty")
            ebo_q = pd.to_numeric(ebo[q_col], errors="coerce").fillna(0) if q_col in ebo.columns else pd.Series(0, index=ebo.index)
            ebo_bc = ebo["BARCODE"].astype(str).str.split(".").str[0].str.strip() if "BARCODE" in ebo.columns else pd.Series("", index=ebo.index)
            sku_col = "CAT 3" if "CAT 3" in ebo.columns else ("CAT 6" if "CAT 6" in ebo.columns else "SKU")
            ebo_sku = ebo[sku_col].astype(str).str.strip().str.upper() if sku_col in ebo.columns else pd.Series("", index=ebo.index)
            ebo_df = pd.DataFrame({"sku": ebo_sku, "barcode": ebo_bc, "dispatch_date": ebo_dt, "dispatch_qty": ebo_q})
            disp_records.append(ebo_df.dropna(subset=["dispatch_date"]))
        except Exception as exc:
            logger.warning("Failed to load EBO dispatch tracking: %s", exc)

    all_disp = pd.concat(disp_records, ignore_index=True) if disp_records else pd.DataFrame(columns=["sku", "barcode", "dispatch_date", "dispatch_qty"])

    # 3. Load WH SOH
    wh_map_sku: dict[str, int] = {}
    try:
        import sys
        if str(_BACKEND / "pipeline") not in sys.path:
            sys.path.append(str(_BACKEND / "pipeline"))
        from pipeline_inputs import load_warehouse_register
        wh = load_warehouse_register()
        wh["sku"] = wh["STYLE ID"].astype(str).str.strip().str.upper()
        wh["wh_soh"] = pd.to_numeric(wh["CLOSINGSTOCKQTY"], errors="coerce").fillna(0).clip(lower=0)
        wh_map_sku = wh.groupby("sku")["wh_soh"].sum().to_dict()
    except Exception as exc:
        logger.warning("Failed to load warehouse SOH in lead time: %s", exc)

    # 4. Pre-index dispatches by SKU and by Barcode
    disp_by_sku: dict[str, list[tuple[pd.Timestamp, float]]] = {}
    disp_by_bc: dict[str, list[tuple[pd.Timestamp, float]]] = {}
    if not all_disp.empty:
        all_disp = all_disp.copy()
        all_disp["dispatch_date"] = pd.to_datetime(all_disp["dispatch_date"], errors="coerce").dt.normalize()
        all_disp = all_disp.dropna(subset=["dispatch_date"])
        for dr in all_disp.itertuples(index=False):
            dt = dr.dispatch_date
            q = float(dr.dispatch_qty)
            s = str(dr.sku).strip().upper()
            b = str(dr.barcode).strip()
            if s and s not in ("NAN", "NONE", "NULL"):
                disp_by_sku.setdefault(s, []).append((dt, q))
            if b and b not in ("NAN", "NONE") and len(b) > 3:
                disp_by_bc.setdefault(b, []).append((dt, q))

    now = pd.Timestamp.now().normalize()
    rows: list[dict[str, Any]] = []

    # 5. Per SKU: FIFO-allocate dispatches onto inward lots (oldest first)
    for sku, sku_lots in lot_grc.groupby("sku", sort=False):
        sku = str(sku).strip()
        lots = sku_lots.sort_values("inward_date").reset_index(drop=True)
        if lots.empty:
            continue

        bcs: set[str] = set()
        for bc_list in lots["barcodes"]:
            if isinstance(bc_list, list):
                bcs.update(bc_list)

        if sku in disp_by_sku and disp_by_sku[sku]:
            raw_events = list(disp_by_sku[sku])
        else:
            raw_events = []
            for bc in bcs:
                raw_events.extend(disp_by_bc.get(bc, []))

        # Collapse same-day events, then chronological
        if raw_events:
            day_map: dict[pd.Timestamp, float] = {}
            for dt, q in raw_events:
                if pd.isna(dt) or q <= 0:
                    continue
                day_map[dt] = day_map.get(dt, 0.0) + float(q)
            disp_events = sorted(day_map.items(), key=lambda x: x[0])
        else:
            disp_events = []

        n = len(lots)
        open_qty = [float(q) for q in lots["inward_qty"].tolist()]
        d7 = [0.0] * n
        d15 = [0.0] * n
        d30 = [0.0] * n
        tot = [0.0] * n
        in_dates = [lots.loc[i, "inward_date"] for i in range(n)]
        lim7 = [d + pd.Timedelta(days=7) for d in in_dates]
        lim15 = [d + pd.Timedelta(days=15) for d in in_dates]
        lim30 = [d + pd.Timedelta(days=30) for d in in_dates]

        for dt, q in disp_events:
            left = float(q)
            for i in range(n):
                if left <= 0:
                    break
                if dt < in_dates[i] or open_qty[i] <= 0:
                    continue
                take = min(left, open_qty[i])
                open_qty[i] -= take
                tot[i] += take
                if dt <= lim7[i]:
                    d7[i] += take
                if dt <= lim15[i]:
                    d15[i] += take
                if dt <= lim30[i]:
                    d30[i] += take
                left -= take

        sku_wh = float(wh_map_sku.get(sku, 0))

        for i in range(n):
            r = lots.iloc[i]
            in_d = in_dates[i]
            in_qty = float(r["inward_qty"])
            if in_qty <= 0 or pd.isna(in_d):
                continue

            remaining = max(0.0, open_qty[i])  # FIFO residual = not yet dispatched from this lot
            remain_pct = round((remaining / in_qty) * 100.0, 1)
            disp_7d_pct = round((d7[i] / in_qty) * 100.0, 1)
            disp_15d_pct = round((d15[i] / in_qty) * 100.0, 1)
            disp_30d_pct = round((d30[i] / in_qty) * 100.0, 1)
            total_disp_pct = round((tot[i] / in_qty) * 100.0, 1)
            age_days = max(0, int((now - in_d).days))

            # Stuck uses lot remaining (FIFO), not full SKU WH SOH on every lot
            red_alert = bool((age_days > 90) and (remain_pct >= 5.0))
            orange_alert = bool(disp_7d_pct < 50.0)
            yellow_alert = bool(disp_15d_pct < 80.0)

            if red_alert:
                sla_category = "Red"
                status = "RED_90D_REMAIN_GE_5PCT"
            elif orange_alert:
                sla_category = "Orange"
                status = "ORANGE_7D_LT_50PCT"
            elif yellow_alert:
                sla_category = "Yellow"
                status = "YELLOW_15D_LT_80PCT"
            else:
                sla_category = "Green"
                status = "GREEN_ON_TRACK"

            rows.append({
                "sku": sku,
                "ref_no": r["ref_no"] or "—",
                "section": r["section"] or "—",
                "season": r["season"] or "—",
                "supplier": r["supplier"] or "—",
                "cost_rate": round(float(r["cost_rate"]), 1),
                "inward_date": in_d.strftime("%Y-%m-%d"),
                "inward_qty": int(in_qty),
                "disp_7d_qty": int(d7[i]),
                "disp_7d_pct": disp_7d_pct,
                "disp_15d_qty": int(d15[i]),
                "disp_15d_pct": disp_15d_pct,
                "disp_30d_qty": int(d30[i]),
                "disp_30d_pct": disp_30d_pct,
                "total_disp_qty": int(tot[i]),
                "total_disp_pct": total_disp_pct,
                # lot open qty (FIFO); sku_wh_soh kept for reference
                "wh_soh": int(round(remaining)),
                "wh_soh_pct": remain_pct,
                "sku_wh_soh": int(round(sku_wh)),
                "age_days": age_days,
                "alert_orange": orange_alert,
                "alert_yellow": yellow_alert,
                "alert_red": red_alert,
                "sla_category": sla_category,
                "status": status,
            })

    df = pd.DataFrame(rows)
    if df.empty:
        logger.warning("Dispatch lead-time rebuild produced 0 rows — keeping previous cache")
        return prev_good if prev_good is not None else df

    try:
        df.to_parquet(disk_file, index=False)
        meta_key.write_text(key, encoding="utf-8")
        pub = publish_dispatch_lead_time_to_blob()
        if not pub.get("ok"):
            logger.warning("Dispatch lead-time disk saved but blob publish skipped: %s", pub.get("error"))
    except Exception:
        pass

    with _CACHE_LOCK:
        _MEMORY_LEAD_TIME = (key, df)
        _REPORT_CACHE.clear()

    return df


@router.get("/dispatch-lead-time")
def get_dispatch_lead_time_report(
    search: str = "",
    alert_filter: str = "all",
    section: str = "",
    season: str = "",
    sort_by: str = "inward_date",
    sort_dir: str = "desc",
    page: int = 1,
    page_size: int = 50,
    refresh: int = 0,
) -> dict[str, Any]:
    """
    Get Inventory Dispatch Lead Time & Inward SLA Alerts.
    Filters:
    - alert_filter: 'all', 'red', 'orange', 'yellow', 'green' (or 'critical', 'lagging', 'on_track')
    - refresh=1 clears disk/memory cache and rebuilds (pulls fresher blob inputs when stale)
    """
    # Screen sends section=all / season=all; the morning warm used to pass "".
    # Those must be one cache file or the tab keeps yesterday's JSON.
    def _opt(value: str) -> str:
        text = str(value or "").strip()
        return "" if text.lower() == "all" else text

    cache_key = "|".join(
        (
            search.strip(),
            alert_filter.strip(),
            _opt(section),
            _opt(season),
            str(sort_by),
            str(sort_dir),
            str(page),
            str(page_size),
        )
    )
    import time
    now_ts = time.time()
    force = int(refresh or 0) == 1
    if force:
        with _CACHE_LOCK:
            _REPORT_CACHE.clear()
    else:
        with _CACHE_LOCK:
            if cache_key in _REPORT_CACHE:
                ts, cached_res = _REPORT_CACHE[cache_key]
                if now_ts - ts < 300:
                    return cached_res
        # Hosted cold start: serve morning-published JSON (FE default page_size=20).
        try:
            from api.page_response_cache import load_page_cache

            disk_hit = load_page_cache("dispatch-lead-time", cache_key)
            if disk_hit is not None:
                with _CACHE_LOCK:
                    _REPORT_CACHE[cache_key] = (now_ts, disk_hit)
                return disk_hit
        except Exception:
            pass

    df = _compute_lead_time_dataset(force_refresh=force)
    if df.empty:
        return {
            "ok": True,
            "summary": {
                "totalSkus": 0,
                "totalLots": 0,
                "totalInwardQty": 0,
                "redAlerts": 0,
                "orangeAlerts": 0,
                "yellowAlerts": 0,
                "greenAlerts": 0,
                "criticalAlerts": 0,
                "laggingAlerts": 0,
                "onTrackSkus": 0,
                "healthySkus": 0,
            },
            "filters": {"sections": [], "seasons": []},
            "rows": [],
            "total": 0,
            "page": page,
            "totalPages": 1,
        }

    # Distinct filters
    sections = sorted({str(x).strip() for x in df["section"].unique() if str(x).strip() and str(x) != "—"})
    seasons = sorted({str(x).strip() for x in df["season"].unique() if str(x).strip() and str(x) != "—"})

    # Summary: totalSkus = unique SKUs; alert counts = lots (row grain)
    total_lots = len(df)
    total_skus = int(df["sku"].nunique())
    total_inward = int(df["inward_qty"].sum())
    red_cnt = int((df["sla_category"] == "Red").sum())
    orange_cnt = int((df["sla_category"] == "Orange").sum())
    yellow_cnt = int((df["sla_category"] == "Yellow").sum())
    green_cnt = int((df["sla_category"] == "Green").sum())

    # Apply Search & Dropdown Filters
    view = df.copy()
    # Apply Global Search Across All Columns
    if search.strip():
        q = search.strip().lower()
        view = view[
            view["sku"].astype(str).str.lower().str.contains(q, na=False)
            | view["ref_no"].astype(str).str.lower().str.contains(q, na=False)
            | view["section"].astype(str).str.lower().str.contains(q, na=False)
            | view["season"].astype(str).str.lower().str.contains(q, na=False)
            | view["supplier"].astype(str).str.lower().str.contains(q, na=False)
            | view["inward_date"].astype(str).str.lower().str.contains(q, na=False)
            | view["sla_category"].astype(str).str.lower().str.contains(q, na=False)
            | view["inward_qty"].astype(str).str.contains(q, na=False)
            | view["total_disp_qty"].astype(str).str.contains(q, na=False)
            | view["wh_soh"].astype(str).str.contains(q, na=False)
            | view["age_days"].astype(str).str.contains(q, na=False)
        ]

    if section.strip() and section.strip() != "all":
        view = view[view["section"].astype(str).str.strip() == section.strip()]

    if season.strip() and season.strip() != "all":
        view = view[view["season"].astype(str).str.strip() == season.strip()]

    # 4-Category Alert Filter
    af = alert_filter.strip().lower()
    if af in ("red", "critical"):
        view = view[view["sla_category"] == "Red"]
    elif af in ("orange", "7d"):
        view = view[view["sla_category"] == "Orange"]
    elif af in ("yellow", "15d"):
        view = view[view["sla_category"] == "Yellow"]
    elif af == "lagging":
        view = view[view["sla_category"].isin(["Orange", "Yellow"])]
    elif af in ("green", "on_track", "clean", "healthy"):
        view = view[view["sla_category"] == "Green"]

    # Sorting
    ascending = sort_dir.lower() == "asc"
    if sort_by in view.columns:
        view = view.sort_values(sort_by, ascending=ascending)
    else:
        view = view.sort_values("inward_date", ascending=False)

    total_filtered = len(view)
    total_pages = max(1, int(np.ceil(total_filtered / max(page_size, 1))))
    start = (page - 1) * page_size
    end = start + page_size
    page_rows = view.iloc[start:end].to_dict(orient="records")

    try:
        inward_max = pd.to_datetime(df["inward_date"], errors="coerce").max()
        data_through = inward_max.strftime("%Y-%m-%d") if pd.notna(inward_max) else None
    except Exception:
        data_through = None

    res = {
        "ok": True,
        "summary": {
            "totalSkus": total_skus,
            "totalLots": total_lots,
            "totalInwardQty": total_inward,
            "redAlerts": red_cnt,
            "orangeAlerts": orange_cnt,
            "yellowAlerts": yellow_cnt,
            "greenAlerts": green_cnt,
            "criticalAlerts": red_cnt,
            "laggingAlerts": orange_cnt + yellow_cnt,
            "onTrackSkus": green_cnt,
            "healthySkus": green_cnt,
            "dataThrough": data_through,
        },
        "filters": {
            "sections": sections,
            "seasons": seasons,
        },
        "dataThrough": data_through,
        "rows": page_rows,
        "total": total_filtered,
        "page": page,
        "pageSize": page_size,
        "totalPages": total_pages,
    }

    with _CACHE_LOCK:
        _REPORT_CACHE[cache_key] = (now_ts, res)
    try:
        from api.page_response_cache import save_page_cache

        save_page_cache("dispatch-lead-time", cache_key, res)
    except Exception:
        pass

    return res

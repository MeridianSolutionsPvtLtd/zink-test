"""
Per-store SOH dated history (same rules as combined_soh).

Blob layout (per store category under input/<Folder>/):
  current/<CANONICAL>.xlsx
  history/<CANONICAL_STEM>_YYYY-MM-DD.xlsx   ← stamp = run_day - 1
  history/previous/<CANONICAL>.xlsx          ← rollback (existing)

Local sync:
  input_files/soh_history/<CANONICAL_STEM>_YYYY-MM-DD.xlsx

last_week_store_soh:
  For each selected store_type, resolve history for today-7 (else nearest ±2),
  read raw store SOH format, normalize via combine_soh, concat.
"""
from __future__ import annotations

import logging
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

log = logging.getLogger("pipeline.store_soh_history")

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

INPUT_DIR = BASE_DIR / "input_files"
LOCAL_HISTORY_DIR = INPUT_DIR / "soh_history"

HISTORY_KEEP = 10
LAST_WEEK_WINDOW_DAYS = 2

_HISTORY_RE = re.compile(r"^(.+)_(\d{4}-\d{2}-\d{2})\.(xlsx|xls|csv)$", re.I)

# store_type → (category_id, combine_soh Source label)
from pipeline_inputs import (  # noqa: E402
    STORE_SOH_CATEGORY_BY_TYPE,
    STORE_TYPE_TO_SOH_SOURCE,
    get_selected_store_types,
)


def history_stamp(run_day: Optional[date] = None) -> date:
    return (run_day or date.today()) - timedelta(days=1)


def dated_history_blob_name(canonical_name: str, stamp: date) -> str:
    p = Path(canonical_name)
    return f"{p.stem}_{stamp.isoformat()}{p.suffix}"


def prune_dated_history_blobs(
    cc,
    history_prefix: str,
    *,
    canonical_name: str,
    keep: int = HISTORY_KEEP,
) -> List[str]:
    stem = Path(canonical_name).stem
    ext = Path(canonical_name).suffix
    pat = re.compile(
        rf"^{re.escape(stem)}_(\d{{4}}-\d{{2}}-\d{{2}}){re.escape(ext)}$",
        re.I,
    )
    items: List[Tuple[date, str]] = []
    for b in cc.list_blobs(name_starts_with=history_prefix):
        name = Path(b.name).name
        if name in (".keep", ".gitkeep") or name.startswith("~$"):
            continue
        # Skip previous/ nested and previous_incoming
        rel = b.name[len(history_prefix) :] if b.name.startswith(history_prefix) else name
        if "/" in rel.replace("\\", "/"):
            continue
        m = pat.match(name)
        if not m:
            continue
        try:
            d = date.fromisoformat(m.group(1))
        except ValueError:
            continue
        items.append((d, b.name))
    items.sort(key=lambda x: x[0], reverse=True)
    removed: List[str] = []
    for _, blob_name in items[keep:]:
        try:
            cc.delete_blob(blob_name)
            removed.append(Path(blob_name).name)
        except Exception as e:
            log.warning("prune failed %s: %s", blob_name, e)
    return removed


def publish_store_soh_dated_history(
    cc,
    *,
    history_prefix: str,
    canonical_name: str,
    data: bytes,
    content_type: str,
    run_day: Optional[date] = None,
    upload_fn=None,
) -> Dict[str, Any]:
    """
    Write NEW current bytes also to history/<stem>_<run-1><ext>.
    Same-day overwrite. Prune oldest beyond HISTORY_KEEP.
    """
    from azure.storage.blob import ContentSettings

    stamp = history_stamp(run_day)
    hist_name = dated_history_blob_name(canonical_name, stamp)
    blob_name = f"{history_prefix}{hist_name}"
    if upload_fn is not None:
        upload_fn(cc, blob_name, data, content_type)
    else:
        cc.upload_blob(
            name=blob_name,
            data=data,
            overwrite=True,
            content_settings=ContentSettings(content_type=content_type),
        )
    pruned = prune_dated_history_blobs(
        cc, history_prefix, canonical_name=canonical_name, keep=HISTORY_KEEP
    )
    log.info("Store SOH history -> %s (stamp=%s)", blob_name, stamp.isoformat())
    return {"history_blob": blob_name, "as_of": stamp.isoformat(), "pruned": pruned}


def _list_local_history(stem: str) -> List[Tuple[date, Path]]:
    if not LOCAL_HISTORY_DIR.exists():
        return []
    out: List[Tuple[date, Path]] = []
    for p in list(LOCAL_HISTORY_DIR.glob(f"{stem}_*.xlsx")) + list(
        LOCAL_HISTORY_DIR.glob(f"{stem}_*.csv")
    ):
        m = _HISTORY_RE.match(p.name)
        if not m or m.group(1).upper() != stem.upper():
            continue
        try:
            d = date.fromisoformat(m.group(2))
        except ValueError:
            continue
        out.append((d, p))
    out.sort(key=lambda x: x[0], reverse=True)
    return out


def _needs_history_blob_sync(
    store_types: set[str],
    target: date,
    window_days: int,
) -> bool:
    """True when any selected store lacks local dated history near target."""
    from merge_category_inputs import CATEGORY_MERGE_CONFIG

    for stype in store_types:
        cat_id = STORE_SOH_CATEGORY_BY_TYPE.get(stype)
        if not cat_id:
            continue
        cfg = CATEGORY_MERGE_CONFIG.get(cat_id) or {}
        canonical = cfg.get("canonical_name") or f"{stype}_SOH.xlsx"
        stem = Path(canonical).stem
        if _pick_nearest(_list_local_history(stem), target, window_days) is None:
            return True
    return False


def _pick_nearest(
    snaps: List[Tuple[date, Path]],
    target: date,
    window_days: int,
) -> Optional[Tuple[date, Path, str]]:
    by_date = {d: p for d, p in snaps}
    if target in by_date:
        return target, by_date[target], "exact"
    candidates: List[Tuple[int, int, date, Path]] = []
    for d, p in snaps:
        delta = abs((d - target).days)
        if delta <= window_days:
            prefer_past = 0 if d <= target else 1
            candidates.append((delta, prefer_past, d, p))
    if not candidates:
        return None
    candidates.sort(key=lambda x: (x[0], x[1], -x[2].toordinal()))
    _, _, d, p = candidates[0]
    return d, p, "nearest"


def resolve_last_week_paths_for_types(
    store_types: Optional[set[str]] = None,
    *,
    today: Optional[date] = None,
    window_days: int = LAST_WEEK_WINDOW_DAYS,
) -> Dict[str, Any]:
    """
    For each selected store_type, resolve a local history file path.
    Tries local soh_history first; if missing, attempts blob sync for that stem.
    """
    types = store_types if store_types is not None else get_selected_store_types()
    if not types:
        types = set(STORE_SOH_CATEGORY_BY_TYPE.keys())

    day = today or date.today()
    target = day - timedelta(days=7)
    per_type: Dict[str, Any] = {}

    # Blob sync only when local dated files are missing (runner no longer pre-syncs).
    if _needs_history_blob_sync(types, target, window_days):
        try:
            sync_store_soh_history_to_local(store_types=types)
        except Exception as e:
            log.warning("sync store SOH history failed: %s", e)
    else:
        log.info("Local SOH history OK for target=%s — skipping blob sync", target.isoformat())

    from merge_category_inputs import CATEGORY_MERGE_CONFIG

    for stype in sorted(types):
        cat_id = STORE_SOH_CATEGORY_BY_TYPE.get(stype)
        if not cat_id:
            per_type[stype] = {"mode": "unknown_type", "path": None}
            continue
        cfg = CATEGORY_MERGE_CONFIG.get(cat_id) or {}
        canonical = cfg.get("canonical_name") or f"{stype}_SOH.xlsx"
        stem = Path(canonical).stem
        snaps = _list_local_history(stem)
        picked = _pick_nearest(snaps, target, window_days)
        if picked is None:
            per_type[stype] = {
                "mode": "missing",
                "target_date": target.isoformat(),
                "path": None,
                "stem": stem,
            }
            continue
        d, path, mode = picked
        per_type[stype] = {
            "mode": mode,
            "target_date": target.isoformat(),
            "matched_date": d.isoformat(),
            "path": str(path),
            "stem": stem,
        }
    return {"target_date": target.isoformat(), "types": per_type}


def _looks_like_pipeline_soh(columns: List[str]) -> bool:
    cols = {str(c).strip().lower() for c in columns}
    has_site = bool(cols & {"site_id", "site id", "site"})
    has_bc = bool(cols & {"barcode", "article ean", "ean"})
    has_qty = bool(
        cols
        & {
            "current stock qty",
            "current_stock_qty",
            "store_qty",
            "closing_stock_qty",
        }
    )
    return has_site and has_bc and has_qty


def _read_history_tabular(path: Path, *, header=0, sheet_name=0, nrows: int | None = None) -> pd.DataFrame:
    """Read dated history (.csv or misnamed xlsx) via shared fast loader."""
    try:
        from pipeline_inputs import read_excel_fast
    except ImportError:
        from pipeline.pipeline_inputs import read_excel_fast  # type: ignore

    kwargs: dict[str, Any] = {"sheet_name": sheet_name, "header": header, "auto_header": False}
    if nrows is not None:
        kwargs["nrows"] = nrows
    try:
        return read_excel_fast(path, **kwargs)
    except TypeError:
        kwargs.pop("nrows", None)
        df = read_excel_fast(path, **kwargs)
        return df.head(nrows) if nrows is not None else df


def _read_history_soh_normalized(path: Path, source_label: str) -> pd.DataFrame:
    """
    Read a dated store-history file.

    Supports:
      1) Already-normalized pipeline schema (Site_id, Barcode, Current Stock Qty [, Source])
      2) Raw vendor workbook (combine_soh SOH_SOURCES column_map / header row)
    """
    from combine_soh import SOH_SOURCES, _normalize_to_pipeline

    # Peek without assuming vendor header_row — older history may already be normalized.
    peek = _read_history_tabular(path, header=0, nrows=3)
    peek_cols = [str(c).strip() for c in peek.columns]
    if _looks_like_pipeline_soh(peek_cols):
        df = _read_history_tabular(path, header=0)
        df.columns = [str(c).strip() for c in df.columns]
        site = next(c for c in df.columns if c.strip().lower() in ("site_id", "site id", "site"))
        bc = next(c for c in df.columns if c.strip().lower() in ("barcode", "article ean", "ean"))
        qty = next(
            c
            for c in df.columns
            if c.strip().lower()
            in (
                "current stock qty",
                "current_stock_qty",
                "store_qty",
                "closing_stock_qty",
            )
        )
        out = pd.DataFrame(
            {
                "Site_id": df[site],
                "Barcode": df[bc],
                "Current Stock Qty": pd.to_numeric(df[qty], errors="coerce"),
                "Source": df["Source"]
                if "Source" in df.columns
                else source_label,
            }
        )
        return out

    if source_label not in SOH_SOURCES:
        raise ValueError(f"Unknown SOH source {source_label!r} for {path.name}")

    _fixed, _legacy, sheet, header_row, column_map = SOH_SOURCES[source_label]

    # History CSVs often already have the real header on row 0 (e.g. SSL:
    # Store No. / GS1 NUMBER / Qty Available). Auto header-detect can skip that
    # and treat the first data row as columns — try header=0 first.
    df0 = _read_history_tabular(path, header=0)
    df0.columns = [str(c).strip().replace("\n", " ").replace("\r", " ") for c in df0.columns]
    try:
        return _normalize_to_pipeline(df0, column_map, source_label)
    except ValueError:
        pass

    hints = list(column_map.keys()) + ["Site_id", "Barcode", "Site", "Site "]
    try:
        from header_detect import detect_header_row_path

        hdr = detect_header_row_path(
            path,
            sheet_name=sheet,
            column_hints=hints,
            preferred_header=header_row,
        )
    except Exception:
        hdr = header_row
    try:
        df = _read_history_tabular(path, sheet_name=sheet, header=hdr)
    except Exception:
        try:
            df = _read_history_tabular(path, header=hdr)
        except Exception:
            df = _read_history_tabular(path, header=0)
    df.columns = [str(c).strip().replace("\n", " ").replace("\r", " ") for c in df.columns]
    # Some exports land as title-row Excel (Unnamed:*) — promote first data row to headers.
    if (
        not _looks_like_pipeline_soh(list(df.columns))
        and any(str(c).startswith("Unnamed") for c in df.columns)
        and len(df) > 0
    ):
        hdr_vals = [str(v).strip() for v in df.iloc[0].tolist()]
        body = df.iloc[1:].copy()
        body.columns = hdr_vals
        df = body
        df.columns = [str(c).strip().replace("\n", " ").replace("\r", " ") for c in df.columns]
    return _normalize_to_pipeline(df, column_map, source_label)


def build_last_week_store_map_per_store(
    store_types: Optional[set[str]] = None,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """
    Build site+barcode last_week_store_qty from per-store history files.

    If a store_type has no history at today-7 / nearest ±2 days → that store is
    omitted from the map; weekly merge fillna(0) sets last_week_store_soh = 0.
    No combined_soh fallback (avoids mixing stale other-channel SOH).
    """
    from pipeline_inputs import normalize_barcode, normalize_site, find_column

    types = store_types if store_types is not None else get_selected_store_types()
    if not types:
        types = set(STORE_SOH_CATEGORY_BY_TYPE.keys())

    resolved = resolve_last_week_paths_for_types(types)
    frames: List[pd.DataFrame] = []
    used: List[str] = []
    missing: List[str] = []

    for stype, info in (resolved.get("types") or {}).items():
        path_s = info.get("path")
        source_label = STORE_TYPE_TO_SOH_SOURCE.get(stype, stype)
        if not path_s or not source_label:
            missing.append(stype)
            used.append(f"{stype}: missing -> last_week_store_soh=0")
            continue
        path = Path(path_s)
        if not path.exists():
            missing.append(stype)
            used.append(f"{stype}: file gone -> last_week_store_soh=0")
            continue
        try:
            normalized = _read_history_soh_normalized(path, source_label)
            frames.append(normalized)
            used.append(
                f"{stype}:{info.get('mode')}@{info.get('matched_date')} ({path.name})"
            )
        except Exception as e:
            log.warning("last_week read failed %s %s: %s", stype, path, e)
            missing.append(stype)
            used.append(f"{stype}: read_error -> last_week_store_soh=0 ({e})")

    info_out = {
        **resolved,
        "used": used,
        "still_missing": missing,
        "missing_fill": "0",
    }
    if not frames:
        return (
            pd.DataFrame(columns=["_site_key", "_barcode_key", "last_week_store_qty"]),
            info_out,
        )

    combined = pd.concat(frames, ignore_index=True)
    site = find_column(combined, "Site_id", "site_id", "Site_ID")
    bc = find_column(combined, "Barcode", "barcode")
    qty = find_column(combined, "Current Stock Qty", "current_stock_qty", "store_qty")
    out = pd.DataFrame(
        {
            "_site_key": normalize_site(combined[site]),
            "_barcode_key": normalize_barcode(combined[bc]),
            "last_week_store_qty": pd.to_numeric(combined[qty], errors="coerce").fillna(0),
        }
    )
    out = out[out["_barcode_key"].astype(str).str.len() > 0]
    out = out.groupby(["_site_key", "_barcode_key"], as_index=False)["last_week_store_qty"].sum()
    return out, info_out


def sync_store_soh_history_to_local(
    *,
    store_types: Optional[set[str]] = None,
    dry_run: bool = False,
    seed_from_current_if_empty: bool = True,
) -> Dict[str, Any]:
    """Download dated SOH history blobs → input_files/soh_history/.

    If a store has no dated history yet but has current/, optionally seed
    history/<stem>_<run-1>.xlsx from current (bootstrap once).
    """
    from merge_category_inputs import (
        CATEGORY_MERGE_CONFIG,
        _download,
        _get_container,
        _load_dotenv,
        _guess_content_type,
        _upload,
    )

    _load_dotenv()
    cc, container_name, input_prefix = _get_container()
    types = store_types if store_types is not None else set(STORE_SOH_CATEGORY_BY_TYPE.keys())
    downloaded: List[str] = []
    seeded: List[str] = []
    errors: List[str] = []

    if not dry_run:
        LOCAL_HISTORY_DIR.mkdir(parents=True, exist_ok=True)

    for stype in sorted(types):
        cat_id = STORE_SOH_CATEGORY_BY_TYPE.get(stype)
        if not cat_id:
            continue
        cfg = CATEGORY_MERGE_CONFIG.get(cat_id)
        if not cfg:
            continue
        folder = cfg["folder"]
        canonical = cfg.get("canonical_name") or ""
        stem = Path(canonical).stem
        hist_prefix = f"{input_prefix}{folder}/history/"
        current_prefix = f"{input_prefix}{folder}/current/"
        found_dated = 0
        try:
            for b in cc.list_blobs(name_starts_with=hist_prefix):
                name = Path(b.name).name
                if name in (".keep", ".gitkeep") or (b.size or 0) <= 0:
                    continue
                rel = b.name[len(hist_prefix) :] if b.name.startswith(hist_prefix) else name
                if "/" in rel.replace("\\", "/"):
                    continue
                m = _HISTORY_RE.match(name)
                if not m or m.group(1).upper() != stem.upper():
                    continue
                found_dated += 1
                dest = LOCAL_HISTORY_DIR / name
                if dry_run:
                    downloaded.append(f"{b.name} -> {dest}")
                    continue
                dest.write_bytes(_download(cc, b.name))
                downloaded.append(str(dest))

            if seed_from_current_if_empty and found_dated == 0 and not dry_run:
                # Bootstrap: copy current → dated history with stamp = yesterday
                for b in cc.list_blobs(name_starts_with=current_prefix):
                    name = Path(b.name).name
                    if name.lower() != canonical.lower():
                        continue
                    data = _download(cc, b.name)
                    info = publish_store_soh_dated_history(
                        cc,
                        history_prefix=hist_prefix,
                        canonical_name=canonical,
                        data=data,
                        content_type=_guess_content_type(canonical),
                        upload_fn=_upload,
                    )
                    local_name = Path(info["history_blob"]).name
                    dest = LOCAL_HISTORY_DIR / local_name
                    dest.write_bytes(data)
                    seeded.append(local_name)
                    downloaded.append(str(dest))
                    break
        except Exception as e:
            errors.append(f"{stype}: {e}")

    return {
        "ok": not errors,
        "container": container_name,
        "local_dir": str(LOCAL_HISTORY_DIR),
        "downloaded": downloaded,
        "seeded": seeded,
        "errors": errors,
    }

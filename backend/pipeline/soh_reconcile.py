"""
Reconcile SOH-Master (blob daily all-store) vs per-channel manual uploads.

Rules (selected stores from replenishment_stores.json):
  - Per selected channel (PT, SSL, …): compare SOH stock **date** in manual file
    vs SOH-Master for that channel — newer wins (hybrid combine allowed).
  - No store selection → legacy rule: compare overall master vs channel freshness.

Freshness uses date_bounds sidecar, Date column in file, or file mtime fallback.
"""
from __future__ import annotations

import io
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

import pandas as pd

log = logging.getLogger("pipeline.soh_reconcile")

PIPELINE_COLUMNS = ["Site_id", "Barcode", "Current Stock Qty", "Source"]

try:
    from pipeline.pipeline_inputs import (
        INPUT_DIR,
        OUTPUT_DIR,
        STORE_SOH_CATEGORY_BY_TYPE,
        STORE_SOH_CATEGORY_IDS,
        STORE_TYPE_TO_SOH_SOURCE,
        filter_soh_by_source,
        get_selected_store_types,
    )
except ImportError:
    from pipeline_inputs import (  # type: ignore
        INPUT_DIR,
        OUTPUT_DIR,
        STORE_SOH_CATEGORY_BY_TYPE,
        STORE_SOH_CATEGORY_IDS,
        STORE_TYPE_TO_SOH_SOURCE,
        filter_soh_by_source,
        get_selected_store_types,
    )

SOH_STOCK_DATE_CANDIDATES = (
    "Date",
    "SOH_DATE",
    "STOCK_DATE",
    "As On Date",
    "AS_ON_DATE",
    "Stock Date",
    "Report Date",
)

SOH_MASTER_CSV = INPUT_DIR / "COMBINED_SOH_MASTER.csv"
SOH_MASTER_XLSX = INPUT_DIR / "COMBINED_SOH_MASTER.xlsx"
COMBINED_SOH_INPUT = INPUT_DIR / "COMBINED_SOH.csv"

PER_CHANNEL_FILES = {
    "EBO": ("EBO_SOH.xlsx", "EBO_SOH.csv"),
    "SSL": ("SHOPPERS_STOP_SOH.xlsx", "SHOPPERS_STOP_SOH.csv"),
    "PT": ("PANTALOONS_SOH.xlsx", "PANTALOONS_SOH.csv"),
    "ICONIC": ("ICONIC_SOH.xlsx", "ICONIC_SOH.csv"),
    "CENTRO": ("CENTRO_SOH.xlsx", "CENTRO_SOH.csv"),
}


def _path_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime if path.exists() else 0.0
    except OSError:
        return 0.0


def _resolve_first(*names: str) -> Path | None:
    for name in names:
        p = INPUT_DIR / name
        if p.exists() and p.stat().st_size > 0:
            return p
    return None


def _master_paths() -> list[Path]:
    out: list[Path] = []
    for p in (SOH_MASTER_CSV, SOH_MASTER_XLSX, COMBINED_SOH_INPUT):
        if p.exists() and p.stat().st_size > 0:
            out.append(p)
    return out


def _master_freshness() -> tuple[float, str | None]:
    """Return (unix mtime, optional as-of date from date_bounds.json)."""
    best = 0.0
    label: str | None = None
    for p in _master_paths():
        best = max(best, _path_mtime(p))
    bounds = INPUT_DIR / "COMBINED_SOH_MASTER.date_bounds.json"
    if not bounds.exists():
        bounds = INPUT_DIR / "COMBINED_SOH.date_bounds.json"
    if bounds.exists():
        try:
            meta = json.loads(bounds.read_text(encoding="utf-8"))
            max_str = meta.get("max_str") or meta.get("max")
            if max_str:
                label = str(max_str)
                try:
                    ts = pd.Timestamp(max_str).timestamp()
                    best = max(best, ts)
                except Exception:
                    pass
        except Exception:
            pass
    return best, label


def _channel_freshness() -> tuple[float, list[str]]:
    touched: list[str] = []
    best = 0.0
    for _stype, names in PER_CHANNEL_FILES.items():
        p = _resolve_first(*names)
        if p:
            touched.append(p.name)
            best = max(best, _path_mtime(p))
    return best, touched


def _sidecar_stock_date(path: Path) -> Optional[pd.Timestamp]:
    for sidecar in (
        path.with_name(f"{path.stem}.date_bounds.json"),
        INPUT_DIR / f"{path.stem}.date_bounds.json",
    ):
        if not sidecar.exists():
            continue
        try:
            meta = json.loads(sidecar.read_text(encoding="utf-8"))
            max_str = meta.get("max_str") or meta.get("max")
            if max_str:
                return pd.Timestamp(max_str).normalize()
        except Exception:
            pass
    return None


def _read_csv_safe(path: Path, **kwargs) -> pd.DataFrame:
    """Read CSV with utf-8-sig / latin-1 fallback (Windows Excel exports)."""
    for enc in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
        try:
            return pd.read_csv(path, low_memory=False, encoding=enc, **kwargs)
        except UnicodeDecodeError:
            continue
    return pd.read_csv(path, low_memory=False, encoding="latin-1", errors="replace", **kwargs)


def _scan_file_stock_date(path: Path) -> Optional[pd.Timestamp]:
    try:
        from date_merge import find_date_column, to_day_series
    except ImportError:
        from pipeline.date_merge import find_date_column, to_day_series  # type: ignore

    try:
        if path.suffix.lower() == ".csv":
            sample = _read_csv_safe(path, nrows=5000)
        else:
            try:
                from pipeline_inputs import read_excel_fast
            except ImportError:
                from pipeline.pipeline_inputs import read_excel_fast  # type: ignore

            sample = read_excel_fast(path, sheet_name=0)
    except Exception:
        try:
            sample = _read_csv_safe(path, nrows=5000)
        except Exception:
            return None

    sample.columns = [str(c).strip() for c in sample.columns]
    try:
        col = find_date_column(sample, SOH_STOCK_DATE_CANDIDATES)
    except ValueError:
        return None
    days = to_day_series(sample[col]).dropna()
    if days.empty:
        return None
    return days.max().normalize()


def _file_stock_date(path: Path | None) -> tuple[Optional[pd.Timestamp], str]:
    if path is None or not path.exists():
        return None, "missing"
    sidecar = _sidecar_stock_date(path)
    if sidecar is not None:
        return sidecar, "sidecar"
    scanned = _scan_file_stock_date(path)
    if scanned is not None:
        return scanned, "file_date_column"
    try:
        return (
            pd.Timestamp.fromtimestamp(path.stat().st_mtime).normalize(),
            "file_mtime",
        )
    except OSError:
        return None, "missing"


def _master_global_stock_date() -> tuple[Optional[pd.Timestamp], str]:
    _, label = _master_freshness()
    if label:
        try:
            return pd.Timestamp(label).normalize(), "master_sidecar"
        except Exception:
            pass
    for p in _master_paths():
        try:
            return (
                pd.Timestamp.fromtimestamp(p.stat().st_mtime).normalize(),
                "master_mtime",
            )
        except OSError:
            pass
    return None, "missing"


def _master_stock_date_for_channel(label: str) -> tuple[Optional[pd.Timestamp], str]:
    """Latest stock date in SOH-Master for one channel (PT/SSL/…)."""
    global_date, global_via = _master_global_stock_date()
    path = _resolve_first(
        "COMBINED_SOH_MASTER.csv",
        "COMBINED_SOH_MASTER.xlsx",
        "COMBINED_SOH.csv",
    )
    if path is None:
        return global_date, global_via

    label_u = label.strip().upper()
    try:
        if path.suffix.lower() == ".csv":
            df = _read_csv_safe(
                path,
                usecols=lambda c: str(c).strip().lower()
                in ("source", "date", "channel", "soh_date", "stock_date"),
            )
        else:
            raw = _read_tabular(path)
            keep = [
                c
                for c in raw.columns
                if str(c).strip().lower()
                in ("source", "date", "channel", "soh_date", "stock_date")
            ]
            df = raw[keep] if keep else raw.iloc[:, :0]
    except Exception:
        try:
            df = _read_csv_safe(
                path,
                usecols=lambda c: str(c).strip().lower()
                in ("source", "date", "channel", "soh_date", "stock_date"),
            )
        except Exception:
            return global_date, global_via

    df.columns = [str(c).strip() for c in df.columns]
    try:
        from date_merge import find_date_column, to_day_series
    except ImportError:
        from pipeline.date_merge import find_date_column, to_day_series  # type: ignore

    try:
        date_col = find_date_column(df, SOH_STOCK_DATE_CANDIDATES)
    except ValueError:
        return global_date, global_via

    source_col = next(
        (c for c in df.columns if str(c).strip().lower() in ("source", "channel")),
        None,
    )
    if source_col:
        src = df[source_col].astype(str).str.upper().str.strip()
        mask = src.isin({label_u, "MASTER"})
        if label_u != "MASTER":
            mask = src.isin({label_u})
        part = df.loc[mask]
        if not part.empty and label_u != "MASTER":
            days = to_day_series(part[date_col]).dropna()
            if not days.empty:
                return days.max().normalize(), "master_by_source"

    return global_date, global_via


def _site_ids_for_store_type(store_type: str) -> set[str]:
    """Site_id keys for a retail channel from Customer Master."""
    try:
        from customer_master import load_customer_master
    except ImportError:
        try:
            from pipeline.customer_master import load_customer_master
        except ImportError:
            return set()

    try:
        cm = load_customer_master(retail_only=True)
    except Exception:
        return set()
    if cm is None or cm.empty or "channel" not in cm.columns:
        return set()

    st = store_type.strip().upper()
    sub = cm[cm["channel"].astype(str).str.upper().str.strip() == st]
    if sub.empty:
        return set()

    keys: set[str] = set()
    for col in ("site_manual_code", "site_code", "site_short_code", "_site_key"):
        if col in sub.columns:
            keys |= {
                str(x).strip()
                for x in sub[col].tolist()
                if str(x).strip() and str(x).strip().lower() not in ("nan", "none")
            }
    return keys


def pick_soh_plan(
    *,
    selected: Optional[set[str]] = None,
    merged_category_ids: Optional[Sequence[str]] = None,
) -> dict[str, dict[str, Any]]:
    """
    Per selected store type: pick 'manual' (per-channel file) or 'master'.
    Compares SOH stock **dates**, not upload mtime.
    """
    types = {str(t).strip().upper() for t in (selected or get_selected_store_types()) if str(t).strip()}
    plan: dict[str, dict[str, Any]] = {}
    if not types:
        return plan

    merged = {str(x).strip() for x in (merged_category_ids or []) if str(x).strip()}

    for stype in sorted(types):
        label = STORE_TYPE_TO_SOH_SOURCE.get(stype, stype)
        names = PER_CHANNEL_FILES.get(label, ())
        manual_path = _resolve_first(*names) if names else None
        manual_date, manual_via = _file_stock_date(manual_path)
        master_date, master_via = _master_stock_date_for_channel(label)

        pick = "master"
        reason = "default_master"

        # Explicit merge this run: manual upload/merge for this channel → prefer manual
        cat_id = STORE_SOH_CATEGORY_BY_TYPE.get(stype)
        if cat_id and cat_id in merged:
            pick = "manual"
            reason = "merged_this_run"
        elif manual_path is not None:
            # Per-channel upload present locally → always use it (not SOH-Master).
            pick = "manual"
            reason = "manual_file_present"
        elif manual_date is None and master_date is None:
            pick = "master"
            reason = "no_dates_fallback"
        elif manual_date is None:
            pick = "master"
            reason = "manual_missing"
        elif master_date is None:
            pick = "manual"
            reason = "master_missing"
        elif manual_date > master_date:
            pick = "manual"
            reason = "manual_newer"
        elif master_date > manual_date:
            pick = "master"
            reason = "master_newer"
        else:
            pick = "master"
            reason = "tie_master"

        plan[stype] = {
            "pick": pick,
            "source_label": label,
            "manual_date": manual_date.date().isoformat() if manual_date is not None else None,
            "master_date": master_date.date().isoformat() if master_date is not None else None,
            "manual_via": manual_via,
            "master_via": master_via,
            "reason": reason,
            "manual_file": manual_path.name if manual_path else None,
        }
    return plan


def _summarize_plan(plan: dict[str, dict[str, Any]]) -> str:
    if not plan:
        return "soh-master"
    picks = {v.get("pick") for v in plan.values()}
    if picks == {"master"}:
        return "soh-master"
    if picks == {"manual"}:
        return "per-channel"
    return "hybrid"


def pick_soh_source(*, merged_category_ids: Optional[Sequence[str]] = None) -> str:
    """
    Return 'soh-master' | 'per-channel' | 'hybrid'.
    When stores are selected, uses pick_soh_plan (date-based per channel).
    """
    plan = pick_soh_plan(merged_category_ids=merged_category_ids)
    if plan:
        return _summarize_plan(plan)

    merged = {str(x).strip() for x in (merged_category_ids or []) if str(x).strip()}
    if merged & STORE_SOH_CATEGORY_IDS:
        return "per-channel"
    if "soh-master" in merged and not (merged & STORE_SOH_CATEGORY_IDS):
        return "soh-master"

    master_ts, _ = _master_freshness()
    channel_ts, _ = _channel_freshness()

    if master_ts <= 0 and channel_ts <= 0:
        return "per-channel"
    if master_ts > 0 and channel_ts <= 0:
        return "soh-master"
    if channel_ts > 0 and master_ts <= 0:
        return "per-channel"
    if master_ts >= channel_ts:
        return "soh-master"
    return "per-channel"


def _read_manual_channel_frame(label: str) -> pd.DataFrame:
    try:
        from combine_soh import SOH_SOURCES, _normalize_to_pipeline, read_soh
    except ImportError:
        from pipeline.combine_soh import SOH_SOURCES, _normalize_to_pipeline, read_soh  # type: ignore

    if label not in SOH_SOURCES:
        raise ValueError(f"Unknown SOH channel label: {label}")
    _, _, _, _, column_map = SOH_SOURCES[label]
    df = read_soh(label)
    return _normalize_to_pipeline(df, column_map, label)


def _master_rows_for_channel(
    master_df: pd.DataFrame, *, label: str, store_type: str
) -> pd.DataFrame:
    label_u = label.strip().upper()
    if "Source" in master_df.columns:
        src = master_df["Source"].astype(str).str.upper().str.strip()
        mask = src == label_u
        if mask.any():
            return master_df.loc[mask].copy()

    sites = _site_ids_for_store_type(store_type)
    if sites:
        out = master_df[
            master_df["Site_id"].astype(str).str.strip().isin(sites)
        ].copy()
        if not out.empty:
            out["Source"] = label
            return out

    return master_df.iloc[0:0].copy()


def build_combined_soh_from_plan(
    plan: dict[str, dict[str, Any]], *, master_df: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Build COMBINED_SOH using per-channel manual vs master picks."""
    frames: list[pd.DataFrame] = []
    master = master_df

    for stype, entry in sorted(plan.items()):
        label = str(entry.get("source_label") or STORE_TYPE_TO_SOH_SOURCE.get(stype, stype))
        pick = entry.get("pick") or "master"
        if pick == "manual":
            try:
                part = _read_manual_channel_frame(label)
                frames.append(part)
                print(
                    f"[soh_reconcile] {stype}: manual file ({entry.get('manual_date')}) "
                    f"-> {len(part):,} rows",
                    flush=True,
                )
            except Exception as exc:
                log.warning("Manual SOH read failed for %s: %s", stype, exc)
                if master is None:
                    master = read_soh_master_pipeline_df()
                part = _master_rows_for_channel(master, label=label, store_type=stype)
                if part.empty:
                    raise
                frames.append(part)
                print(
                    f"[soh_reconcile] {stype}: manual failed - fallback master "
                    f"({entry.get('master_date')}) -> {len(part):,} rows",
                    flush=True,
                )
        else:
            if master is None:
                master = read_soh_master_pipeline_df()
            part = _master_rows_for_channel(master, label=label, store_type=stype)
            used = "master"
            if part.empty:
                log.warning(
                    "Master SOH empty for %s - falling back to manual file", stype
                )
                try:
                    part = _read_manual_channel_frame(label)
                    used = "manual_fallback"
                except Exception as exc:
                    raise ValueError(
                        f"Master SOH empty for {stype} and manual SOH failed: {exc}"
                    ) from exc
            frames.append(part)
            print(
                f"[soh_reconcile] {stype}: {used} ({entry.get('master_date')}) "
                f"-> {len(part):,} rows",
                flush=True,
            )

    if not frames:
        raise ValueError("SOH plan produced no rows - check selected stores and inputs")

    combined = pd.concat(frames, ignore_index=True)
    combined = combined.sort_values(["Site_id", "Barcode"]).reset_index(drop=True)
    combined = filter_soh_by_source(combined, log_prefix="[soh_reconcile] ")
    return combined[PIPELINE_COLUMNS]


def _read_tabular(path: Path) -> pd.DataFrame:
    try:
        from pipeline_inputs import read_excel_fast
    except ImportError:
        from pipeline.pipeline_inputs import read_excel_fast  # type: ignore

    # read_excel_fast handles real CSV (utf-8/latin-1) and misnamed Excel (.csv that is xlsx).
    return read_excel_fast(path, sheet_name=0, auto_header=False)


def _to_pipeline_frame(
    raw: pd.DataFrame, *, default_source: str = "MASTER", filter_selected: bool = True
) -> pd.DataFrame:
    df = raw.copy()
    df.columns = df.columns.astype(str).str.strip()

    col_map = {
        "site_id": "Site_id",
        "Site_ID": "Site_id",
        "SITE_ID": "Site_id",
        "Site Code": "Site_id",
        "SITE CODE": "Site_id",
        "Store Code": "Site_id",
        "STORE CODE": "Site_id",
        "Barcode": "Barcode",
        "BARCODE": "Barcode",
        "EAN": "Barcode",
        "EAN UPC": "Barcode",
        "Current Stock Qty": "Current Stock Qty",
        "CLOSINGSTOCKQTY": "Current Stock Qty",
        "CLOSING STOCK QTY": "Current Stock Qty",
        "QTY": "Current Stock Qty",
        "Quantity": "Current Stock Qty",
        "TOTAL_STOCK_QTY": "Current Stock Qty",
        "Source": "Source",
        "SOURCE": "Source",
        "Channel": "Source",
        "CHANNEL": "Source",
    }
    for src, dst in col_map.items():
        if src in df.columns and dst not in df.columns:
            df = df.rename(columns={src: dst})

    for col in PIPELINE_COLUMNS:
        if col not in df.columns:
            df[col] = default_source if col == "Source" else ""

    df["Site_id"] = df["Site_id"].astype(str).str.strip()
    df["Barcode"] = df["Barcode"].astype(str).str.strip()
    df["Current Stock Qty"] = pd.to_numeric(df["Current Stock Qty"], errors="coerce").fillna(0)
    df["Source"] = df["Source"].astype(str).str.strip().replace({"nan": "", "None": ""})
    df.loc[df["Source"].eq(""), "Source"] = default_source

    df = df[df["Site_id"].astype(str).str.len().gt(0)].copy()
    if filter_selected:
        df = filter_soh_by_source(df, log_prefix="[soh_reconcile] ")
    return df[PIPELINE_COLUMNS]


def read_soh_master_pipeline_df() -> pd.DataFrame:
    path = _resolve_first(
        "COMBINED_SOH_MASTER.csv",
        "COMBINED_SOH_MASTER.xlsx",
        "COMBINED_SOH.csv",
        "COMBINED_SOH.xlsx",
    )
    if path is None:
        raise FileNotFoundError(
            "COMBINED_SOH_MASTER not found locally. Run blob fetch or manual SOH upload first."
        )
    log.info("Reading SOH-Master from %s", path.name)
    raw = _read_tabular(path)
    out = _to_pipeline_frame(raw, default_source="MASTER", filter_selected=False)
    log.info("SOH-Master pipeline frame: %s rows", f"{len(out):,}")
    return out


def write_local_soh_master(df: pd.DataFrame) -> Path:
    """Mirror combined SOH into input_files/COMBINED_SOH_MASTER.csv."""
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    export = df.copy()
    export.to_csv(SOH_MASTER_CSV, index=False, encoding="utf-8")
    log.info("Wrote local SOH-Master: %s (%s rows)", SOH_MASTER_CSV.name, f"{len(export):,}")
    return SOH_MASTER_CSV


def publish_soh_master_to_blob(df: pd.DataFrame) -> dict[str, Any]:
    """Push combined frame to blob SOH-Master/incoming → merge → sync local.

    Always writes local master first. Blob publish is best-effort — missing
    AZURE_STORAGE_CONNECTION_STRING or upload errors must not fail combine_soh.
    """
    result: dict[str, Any] = {"ok": False, "local": str(write_local_soh_master(df))}
    try:
        from merge_category_inputs import (
            _category_prefixes,
            _get_container,
            _upload,
            merge_one_category,
            sync_current_to_local,
        )
    except ImportError:
        try:
            from pipeline.merge_category_inputs import (  # type: ignore
                _category_prefixes,
                _get_container,
                _upload,
                merge_one_category,
                sync_current_to_local,
            )
        except ImportError:
            result["skipped"] = "merge_category_inputs unavailable"
            return result

    try:
        cc_info = _get_container()
    except Exception as exc:
        # _get_container raises when connection string missing — skip blob, keep local.
        log.warning("SOH-Master blob publish skipped: %s", exc)
        result["skipped"] = str(exc)
        return result

    if cc_info is None:
        result["skipped"] = "no blob container"
        return result
    cc, _container_name, input_prefix = cc_info

    try:
        prefixes = _category_prefixes(input_prefix, "SOH-Master")
        incoming = prefixes["incoming"]
        dest = f"{incoming}COMBINED_SOH_MASTER.csv"
        buf = io.BytesIO()
        df.to_csv(buf, index=False, encoding="utf-8")
        _upload(cc, dest, buf.getvalue(), "text/csv")
        merge_res = merge_one_category("soh-master", dry_run=False, keep_incoming=False)
        result["merge"] = {
            "ok": merge_res.get("ok"),
            "written": merge_res.get("written"),
            "skipped": merge_res.get("skipped"),
        }
        if merge_res.get("ok") and not merge_res.get("skipped"):
            sync = sync_current_to_local(dry_run=False, only_ids=["soh-master"])
            result["sync"] = {"downloaded": len(sync.get("downloaded") or [])}
        result["ok"] = bool(merge_res.get("ok"))
    except Exception as exc:
        log.warning("SOH-Master blob publish failed: %s", exc)
        result["error"] = str(exc)
    return result


def _combine_per_channel() -> pd.DataFrame:
    """Delegate to combine_soh per-channel logic (no save, no master sync)."""
    try:
        from combine_soh import combine_per_channel_frames
    except ImportError:
        from pipeline.combine_soh import combine_per_channel_frames  # type: ignore

    return combine_per_channel_frames()


def build_combined_soh(*, save: bool = True, merged_category_ids: Optional[Sequence[str]] = None) -> pd.DataFrame:
    """
    Dual-path SOH combine for pipeline output_files/COMBINED_SOH.xlsx.
    Selected stores: per-channel date vs master date (hybrid allowed).
    """
    source = pick_soh_source(merged_category_ids=merged_category_ids)
    plan = pick_soh_plan(merged_category_ids=merged_category_ids)
    master_ts, master_label = _master_freshness()
    channel_ts, channel_files = _channel_freshness()

    if plan:
        print(f"[soh_reconcile] SOH plan ({source}):", flush=True)
        for stype, entry in sorted(plan.items()):
            print(
                f"  {stype}: {entry.get('pick')} "
                f"(manual={entry.get('manual_date')} master={entry.get('master_date')} "
                f"reason={entry.get('reason')})",
                flush=True,
            )
        combined = build_combined_soh_from_plan(plan)
        used_manual = any(v.get("pick") == "manual" for v in plan.values())
    elif source == "soh-master":
        combined = read_soh_master_pipeline_df()
        print(
            f"[soh_reconcile] source=SOH-Master (master_ts={master_ts}, "
            f"channel_ts={channel_ts}, as_of={master_label or 'file mtime'})",
            flush=True,
        )
        used_manual = False
    else:
        combined = _combine_per_channel()
        print(
            f"[soh_reconcile] source=per-channel ({channel_files}) -> will update SOH-Master",
            flush=True,
        )
        used_manual = True

    if save and used_manual and not combined.empty:
        try:
            pub = publish_soh_master_to_blob(combined)
            print(f"[soh_reconcile] SOH-Master updated: {pub}", flush=True)
        except Exception as exc:
            # Local COMBINED_SOH save must still proceed even if master sync fails.
            log.warning("SOH-Master sync failed (continuing combine): %s", exc)
            print(f"[soh_reconcile] SOH-Master sync skipped: {exc}", flush=True)

    if save:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        try:
            from pipeline.combined_soh_store import publish_combined_soh
        except ImportError:
            from combined_soh_store import publish_combined_soh  # type: ignore

        tmp = OUTPUT_DIR / "_COMBINED_SOH_building.xlsx"
        try:
            combined.to_excel(tmp, index=False, engine="xlsxwriter")
        except Exception:
            combined.to_excel(tmp, index=False)
        result = publish_combined_soh(tmp, also_csv_df=combined)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        print(f"[soh_reconcile] COMBINED_SOH saved: {result.get('compat')}", flush=True)

    return combined


def reconcile_soh_master(
    *,
    merged_category_ids: Optional[Sequence[str]] = None,
    dry_run: bool = False,
    run_combine: bool = True,
) -> dict[str, Any]:
    """
    Post-merge hook: align SOH-Master with latest per-channel uploads OR keep blob master.
    """
    source = pick_soh_source(merged_category_ids=merged_category_ids)
    plan = pick_soh_plan(merged_category_ids=merged_category_ids)
    master_ts, master_label = _master_freshness()
    channel_ts, channel_files = _channel_freshness()

    out: dict[str, Any] = {
        "ok": True,
        "source": source,
        "soh_plan": plan,
        "master_freshness": master_ts,
        "master_as_of": master_label,
        "channel_freshness": channel_ts,
        "channel_files": channel_files,
        "action": "none",
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }

    soh_touched = (
        bool(set(merged_category_ids or []) & STORE_SOH_CATEGORY_IDS)
        or "soh-master" in set(merged_category_ids or [])
        or "combined-soh" in set(merged_category_ids or [])
    )
    if not soh_touched and source == "soh-master" and master_ts > 0:
        out["action"] = "keep_blob_master"
        return out

    if dry_run:
        out["action"] = f"would_use_{source}"
        return out

    if source == "soh-master" and not plan and not (
        set(merged_category_ids or []) & STORE_SOH_CATEGORY_IDS
    ):
        out["action"] = "keep_blob_master"
        return out

    if not run_combine:
        out["action"] = "deferred_to_pipeline"
        return out

    try:
        combined = build_combined_soh(save=True, merged_category_ids=merged_category_ids)
        if plan:
            manual_types = [k for k, v in plan.items() if v.get("pick") == "manual"]
            master_types = [k for k, v in plan.items() if v.get("pick") == "master"]
            if manual_types and master_types:
                out["action"] = f"hybrid_manual_{'+'.join(manual_types)}_master_{'+'.join(master_types)}"
            elif manual_types:
                out["action"] = "combined_and_updated_master"
            else:
                out["action"] = "master_from_plan"
        else:
            out["action"] = "combined_and_updated_master"
        out["rows"] = len(combined)
    except Exception as exc:
        log.exception("SOH reconcile combine failed")
        out["ok"] = False
        out["action"] = "error"
        out["error"] = str(exc)

    return out

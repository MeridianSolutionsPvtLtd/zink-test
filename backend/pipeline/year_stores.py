"""
Calendar-year folders under an existing master.

2024 and 2025 stay frozen. Daily merge writes only the latest calendar year:

  input/<Master>/<year>/current/<canonical>.csv
  input/<Master>/<year>/incoming/

A financial year (Apr–Mar) is two calendar folders. FY27 reads 2026 + 2027,
then keeps 1 Apr 2026 through 31 Mar 2027.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import date
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

# Soft etag revalidation — every request was HEADing Azure and stalling App Service (502s).
_YEAR_ETAG_CHECKED_AT: dict[tuple[str, int], float] = {}
_YEAR_ETAG_TTL_S = float(os.environ.get("YEAR_FILES_ETAG_TTL_S", "900") or "900")
_YEAR_MISS_UNTIL: dict[tuple[str, int], float] = {}

SALES_FOLDER = "Sales-Master (PT, SSL, Centro, Iconic, EBO)"
SALES_FILE = "SSI_PT_CENTRO_ICONIC_EBO_SALES_MASTER.csv"
TRANSFER_FOLDER = "Transfer-Master (PT, SSL, Centro, Iconic)"
TRANSFER_FILE = "SALES_INVOICE_TRACKING_MASTER.csv"

MASTERS = {
    "sales": {"folder": SALES_FOLDER, "filename": SALES_FILE},
    "transfer": {"folder": TRANSFER_FOLDER, "filename": TRANSFER_FILE},
}


def live_calendar_year(as_of: date | None = None) -> int:
    return int((as_of or date.today()).year)


def calendar_years_for_fy(fy_end: int) -> List[int]:
    """FY end-year 2027 → calendar 2026 and 2027."""
    return [int(fy_end) - 1, int(fy_end)]


def year_folder(base_folder: str, year: int) -> str:
    return f"{base_folder}/{int(year)}"


def year_blob_rel(base_folder: str, year: int, filename: str, area: str = "current") -> str:
    return f"{base_folder}/{int(year)}/{area}/{filename}"


def master_year_rels(kind: str, years: Sequence[int], area: str = "current") -> List[str]:
    spec = MASTERS[kind]
    folder = spec["folder"]
    fname = spec["filename"]
    stem = Path(fname).stem
    candidates = []
    for y in years:
        # Standard: folder/year/current/filename.csv
        candidates.append(f"{folder}/{y}/{area}/{fname}")
        candidates.append(f"{folder}/{y}/{fname}")
        candidates.append(f"{folder}/{y}/{area}/{stem}.xlsx")
        candidates.append(f"{folder}/{y}/{stem}.xlsx")
        candidates.append(f"{folder}/{y}/{area}/{stem}.csv")
        candidates.append(f"{folder}/{y}/{stem}.csv")
        candidates.append(f"{folder}/{y}.csv")
        candidates.append(f"{folder}/{y}.xlsx")
        # Also legacy folder name without EBO if applicable
        if "EBO" in folder:
            alt_folder = folder.replace(", EBO", "").replace("EBO, ", "").replace("EBO", "").strip()
            candidates.append(f"{alt_folder}/{y}/{area}/{fname}")
            candidates.append(f"{alt_folder}/{y}/{fname}")
    return candidates


def _cached_year_file(kind: str, year: int) -> Optional[Path]:
    """Local blob cache for one calendar year. No network."""
    spec = MASTERS[kind]
    folder = spec["folder"]
    fname = spec["filename"]
    backend_dir = Path(__file__).resolve().parents[1]
    cache_dir = Path(os.environ.get("BLOB_CACHE_DIR") or (backend_dir / "data" / "blob_cache"))
    for rel in (
        f"input/{folder}/{int(year)}/current/{fname}",
        f"input/{folder}/{int(year)}/{fname}",
    ):
        path = cache_dir / re.sub(r"[^\w.\-]+", "_", rel)
        if path.exists() and path.stat().st_size > 0:
            return path
    return None


def ensure_year_files_local(kind: str, years: Iterable[int], *, refresh: bool = False) -> List[Path]:
    """Download each year's current file. Missing years are skipped.

    Fast path: serve local blob_cache. Soft-refresh from Azure at most every
    YEAR_FILES_ETAG_TTL_S (default 15m) so morning merges land without per-request
    Azure HEAD storms (which were causing hosted 502 / multi-minute loads).
    """
    year_list = [int(y) for y in years]
    found_by_year: dict[int, Path] = {}
    now = time.time()
    ttl = max(60.0, _YEAR_ETAG_TTL_S)

    # 1) Local cache first (no network)
    if not refresh:
        for year in year_list:
            cached = _cached_year_file(kind, year)
            if cached is not None:
                found_by_year[year] = cached

    # 2) Azure only for missing years, forced refresh, or TTL-expired soft check
    need_azure: list[int] = []
    for year in year_list:
        key = (kind, year)
        miss_until = _YEAR_MISS_UNTIL.get(key, 0.0)
        if not refresh and now < miss_until:
            continue
        cached = found_by_year.get(year)
        last = _YEAR_ETAG_CHECKED_AT.get(key, 0.0)
        if refresh or cached is None or (now - last) >= ttl:
            need_azure.append(year)

    if need_azure and os.environ.get("AZURE_STORAGE_CONNECTION_STRING"):
        try:
            from api.blob_store import BlobJsonStore

            store = BlobJsonStore()
            for year in need_azure:
                key = (kind, year)
                # Prefer the canonical current/ path first — avoid 8× exists() probes.
                spec = MASTERS[kind]
                primary = [
                    f"{spec['folder']}/{year}/current/{spec['filename']}",
                    f"{spec['folder']}/{year}/{spec['filename']}",
                ]
                path = store.ensure_input_candidates(primary)
                if path is None:
                    path = store.ensure_input_candidates(master_year_rels(kind, [year]))
                _YEAR_ETAG_CHECKED_AT[key] = time.time()
                if path is not None and path.exists() and path.stat().st_size > 0:
                    found_by_year[year] = path
                    _YEAR_MISS_UNTIL.pop(key, None)
                else:
                    # Missing calendar year (e.g. 2027 folder) — don't re-probe every request.
                    _YEAR_MISS_UNTIL[key] = time.time() + ttl
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "Blob store unavailable for year files (%s) — checking local cache", exc
            )

    still_missing = [year for year in year_list if year not in found_by_year]
    if still_missing:
        # Local fallback: check data/blob_cache/ or pipeline/input_files/
        backend_dir = Path(__file__).resolve().parents[1]
        cache_dir = backend_dir / "data" / "blob_cache"
        input_dir = backend_dir / "pipeline" / "input_files"
        for year in still_missing:
            for d in (cache_dir, input_dir):
                if not d.exists():
                    continue
                matched = False
                for f in d.iterdir():
                    if not f.is_file() or f.stat().st_size <= 0:
                        continue
                    name_l = f.name.lower()
                    # Skip analyzer prepared caches / keys that also contain "sales" + year.
                    if f.suffix.lower() in {".key", ".json", ".etag"}:
                        continue
                    if "prepared" in name_l or "analyzer" in name_l or "page-response" in name_l:
                        continue
                    if str(year) not in f.name:
                        continue
                    if "sales" in name_l or "ssi" in name_l:
                        found_by_year[year] = f
                        matched = True
                        break
                if matched:
                    break
    return [found_by_year[year] for year in year_list if year in found_by_year]


def write_year_bounds(
    cc,
    input_prefix: str,
    base_folder: str,
    year: int,
    filename: str,
    *,
    date_column: str,
    min_str: str,
    max_str: str,
    rows: int,
) -> None:
    from pipeline.merge_category_inputs import _upload

    blob = (
        f"{input_prefix}{base_folder}/{int(year)}/current/"
        f"{Path(filename).stem}.date_bounds.json"
    )
    payload = {
        "date_column": date_column,
        "min_str": min_str,
        "max_str": max_str,
        "rows_with_date": int(rows),
        "total_rows": int(rows),
        "assumed_covered_through": max_str,
        "merged_file_dates": [],
        "updated_at": date.today().isoformat(),
    }
    _upload(cc, blob, json.dumps(payload).encode("utf-8"), "application/json")


def split_current_master(kind: str) -> dict:
    """One-time: copy current master into calendar-year folders. Parent file stays."""
    import os
    from collections import defaultdict

    import pandas as pd
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    from azure.storage.blob import BlobServiceClient

    spec = MASTERS[kind]
    candidates = {
        "sales": ["Invoice Date", "ENTRY_DATE", "Date", "BILL DATE"],
        "transfer": [
            "Invoice Date",
            "INVOICE DATE",
            "DOCDT",
            "DOC DATE",
            "DC_DATE",
            "Transfer Date",
            "Date",
        ],
    }[kind]
    prefix = os.environ.get("AZURE_BLOB_INPUT_PREFIX", "input/")
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    blob_name = f"{prefix}{spec['folder']}/current/{spec['filename']}"
    cc = BlobServiceClient.from_connection_string(
        os.environ["AZURE_STORAGE_CONNECTION_STRING"]
    ).get_container_client(os.environ.get("AZURE_STORAGE_CONTAINER", "zinc-data"))
    blob = cc.get_blob_client(blob_name)
    if not blob.exists():
        return {"ok": False, "kind": kind, "reason": f"missing {blob_name}"}

    tmp_dir = Path(__file__).resolve().parents[1] / "data" / "year_split" / kind
    tmp_dir.mkdir(parents=True, exist_ok=True)
    src = tmp_dir / spec["filename"]
    print(f"download {blob_name}", flush=True)
    with src.open("wb") as fh:
        blob.download_blob().readinto(fh)

    header = pd.read_csv(src, nrows=0).columns.tolist()
    date_col = next((c for c in candidates if c in header), None)
    if not date_col:
        return {"ok": False, "kind": kind, "reason": f"no date column in {header[:12]}"}

    handles: dict = {}
    stats: dict = defaultdict(lambda: {"rows": 0, "min": None, "max": None})
    undated = 0
    live = live_calendar_year()

    def _handle(year: int):
        if year not in handles:
            path = tmp_dir / f"{year}.csv"
            handles[year] = path.open("w", encoding="utf-8", newline="")
            pd.DataFrame(columns=header).to_csv(handles[year], index=False)
        return handles[year]

    print(f"split {kind} on {date_col}", flush=True)
    sample = pd.read_csv(src, nrows=3, low_memory=False)
    print("sample dates", sample[date_col].tolist(), flush=True)
    for chunk in pd.read_csv(src, chunksize=200_000, low_memory=False):
        days = pd.to_datetime(chunk[date_col], errors="coerce", dayfirst=False)
        years = days.dt.year
        for year_value, part in chunk.groupby(years, dropna=False):
            if pd.isna(year_value):
                year = live
                undated += int(len(part))
            else:
                year = int(year_value)
            part.to_csv(_handle(year), index=False, header=False)
            part_days = days.loc[part.index].dropna()
            rec = stats[year]
            rec["rows"] += int(len(part))
            if not part_days.empty:
                dmin = part_days.min().date().isoformat()
                dmax = part_days.max().date().isoformat()
                rec["min"] = dmin if rec["min"] is None or dmin < rec["min"] else rec["min"]
                rec["max"] = dmax if rec["max"] is None or dmax > rec["max"] else rec["max"]

    for fh in handles.values():
        fh.close()

    uploaded = []
    for year, rec in sorted(stats.items()):
        path = tmp_dir / f"{year}.csv"
        dest = f"{prefix}{year_blob_rel(spec['folder'], year, spec['filename'])}"
        print(f"upload {dest} rows={rec['rows']}", flush=True)
        with path.open("rb") as fh:
            cc.upload_blob(dest, fh, overwrite=True)
        if rec["min"] and rec["max"]:
            write_year_bounds(
                cc,
                prefix,
                spec["folder"],
                year,
                spec["filename"],
                date_column=date_col,
                min_str=rec["min"],
                max_str=rec["max"],
                rows=rec["rows"],
            )
        uploaded.append({"year": year, **rec, "blob": dest})

    return {"ok": True, "kind": kind, "date_column": date_col, "undated_rows": undated, "years": uploaded}

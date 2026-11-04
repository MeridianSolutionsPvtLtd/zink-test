"""
Shared input paths + loaders for the retail pipeline.

Replaces old retail_input04 12.xlsx sheet reads with:
  - output_files/COMBINED_SOH.xlsx
  - input Sales (PT, SSL, Centro, Iconic, EBO) — pipeline sales source
  - output_files/COMBINED_TRANSFER.xlsx
  - input_files/@ITEM MASTER DETAILS REGISTER_SP@PACKING.xlsx
  - input_files/@WAREHOUSE CLOSING STOCK REGISTER_SP_09-07-2026.xlsx
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Optional

import pandas as pd

# Scripts live in merged_backend/pipeline/. Data dirs are junctions to zinc
# project input_files/output_files (or override via PIPELINE_DATA_ROOT).
_SCRIPT_DIR = Path(__file__).resolve().parent
BASE_DIR = Path(os.environ.get("PIPELINE_DATA_ROOT", str(_SCRIPT_DIR))).resolve()
INPUT_DIR = BASE_DIR / "input_files"
OUTPUT_DIR = BASE_DIR / "output_files"

COMBINED_SOH = OUTPUT_DIR / "COMBINED_SOH.xlsx"
COMBINED_SALES = OUTPUT_DIR / "COMBINED_SALES.xlsx"
COMBINED_SALES_CSV = OUTPUT_DIR / "COMBINED_SALES.csv"
COMBINED_TRANSFER = OUTPUT_DIR / "COMBINED_TRANSFER.xlsx"
COMBINED_TRANSFER_CSV = OUTPUT_DIR / "COMBINED_TRANSFER.csv"

# Pipeline sales source — Sales (PT, SSL, Centro, Iconic, EBO), not COMBINED_SALES
_SALES_FOLDER = "Sales (PT, SSL, Centro, Iconic, EBO)"
PIPELINE_SALES_CANDIDATES = (
    INPUT_DIR / _SALES_FOLDER / "current" / "SSI_PT_CENTRO_ICONIC_SALES.csv",
    INPUT_DIR / _SALES_FOLDER / "current" / "SSI_PT_CENTRO_ICONIC_SALES.xlsx",
    INPUT_DIR / "SSI_PT_CENTRO_ICONIC_SALES.csv",
    INPUT_DIR / "SSI_PT_CENTRO_ICONIC_SALES.xlsx",
)
_BLOB_PIPELINE_SALES_CANDIDATES = (
    f"{_SALES_FOLDER}/current/SSI_PT_CENTRO_ICONIC_SALES.csv",
    f"{_SALES_FOLDER}/current/SSI_PT_CENTRO_ICONIC_SALES.xlsx",
)

# Pipeline transfer source — Transfer - (PT, SSL, Centro, Iconic)/current/
# Canonical blob name is SALES_INVOICE_TRACKING.* (NOT COMBINED_TRANSFER / TRANSFER_PT_*)
_TRANSFER_FOLDER = "Transfer - (PT, SSL, Centro, Iconic)"
_TRANSFER_EBO_FOLDER = "Transfer - EBO"
PIPELINE_TRANSFER_CANDIDATES = (
    INPUT_DIR / _TRANSFER_FOLDER / "current" / "SALES_INVOICE_TRACKING.csv",
    INPUT_DIR / _TRANSFER_FOLDER / "current" / "SALES_INVOICE_TRACKING.xlsx",
    INPUT_DIR / "SALES_INVOICE_TRACKING.csv",
    INPUT_DIR / "SALES_INVOICE_TRACKING.xlsx",
    # legacy filenames (older syncs)
    INPUT_DIR / _TRANSFER_FOLDER / "current" / "TRANSFER_PT_SSL_CENTRO_ICONIC.csv",
    INPUT_DIR / _TRANSFER_FOLDER / "current" / "TRANSFER_PT_SSL_CENTRO_ICONIC.xlsx",
)
_BLOB_PIPELINE_TRANSFER_CANDIDATES = (
    f"{_TRANSFER_FOLDER}/current/SALES_INVOICE_TRACKING.csv",
    f"{_TRANSFER_FOLDER}/current/SALES_INVOICE_TRACKING.xlsx",
    f"{_TRANSFER_FOLDER}/current/TRANSFER_PT_SSL_CENTRO_ICONIC.csv",
    f"{_TRANSFER_FOLDER}/current/TRANSFER_PT_SSL_CENTRO_ICONIC.xlsx",
)
PIPELINE_TRANSFER_EBO_CANDIDATES = (
    INPUT_DIR / _TRANSFER_EBO_FOLDER / "current" / "EBO_INVOICE_TRACKING.csv",
    INPUT_DIR / _TRANSFER_EBO_FOLDER / "current" / "EBO_INVOICE_TRACKING.xlsx",
    INPUT_DIR / "EBO_INVOICE_TRACKING.csv",
    INPUT_DIR / "EBO_INVOICE_TRACKING.xlsx",
)
_BLOB_PIPELINE_TRANSFER_EBO_CANDIDATES = (
    f"{_TRANSFER_EBO_FOLDER}/current/EBO_INVOICE_TRACKING.csv",
    f"{_TRANSFER_EBO_FOLDER}/current/EBO_INVOICE_TRACKING.xlsx",
)
ITEM_MASTER_FILE = INPUT_DIR / "ITEM_MASTER.xlsx"
ITEM_MASTER_SHEET = "ITEM MASTER UPDATED"
ITEM_MASTER_HEADER = 3
ITEM_MASTER_HINTS = ["BARCODE", "ICODE", "SKU", "SIZE", "MRP", "EXTINT"]
WAREHOUSE_FILE = INPUT_DIR / "WAREHOUSE_SOH.xlsx"
WAREHOUSE_FILE_CSV = INPUT_DIR / "WAREHOUSE_SOH.csv"

_BLOB_WAREHOUSE_CANDIDATES = (
    "Warehouse SOH/current/WAREHOUSE_SOH.csv",
    "Warehouse SOH/current/WAREHOUSE_SOH.xlsx",
    "Warehouse SOH/current/WAREHOUSE_SOH.xls",
)

_WAREHOUSE_BLOB_CACHE: Path | None = None


def _find_warehouse_from_blob() -> Path | None:
    """Download WAREHOUSE_SOH from Azure blob current/ when local input_files is empty."""
    global _WAREHOUSE_BLOB_CACHE
    if _WAREHOUSE_BLOB_CACHE is not None and _WAREHOUSE_BLOB_CACHE.exists() and _WAREHOUSE_BLOB_CACHE.stat().st_size > 0:
        return _WAREHOUSE_BLOB_CACHE
    try:
        from api.blob_store import BlobJsonStore

        path = BlobJsonStore().ensure_input_candidates(_BLOB_WAREHOUSE_CANDIDATES)
        if path is not None and path.exists() and path.stat().st_size > 0:
            _WAREHOUSE_BLOB_CACHE = path
            return path
    except Exception:
        return None
    return None


def warehouse_register_exists() -> bool:
    if WAREHOUSE_FILE_CSV.exists() or WAREHOUSE_FILE.exists():
        return True
    return _find_warehouse_from_blob() is not None


def warehouse_register_path() -> Path:
    """
    Resolve warehouse closing stock file.

    Blob sync writes WAREHOUSE_SOH.csv (canonical). Prefer CSV over stale local
    WAREHOUSE_SOH.xlsx so build_refill always uses the latest blob current/.
    On Azure App Service, pull from blob when local input_files is missing.
    """
    if WAREHOUSE_FILE_CSV.exists():
        return WAREHOUSE_FILE_CSV
    if WAREHOUSE_FILE.exists():
        return WAREHOUSE_FILE
    blob_path = _find_warehouse_from_blob()
    if blob_path is not None:
        return blob_path
    raise FileNotFoundError(
        f"Warehouse file not found: {WAREHOUSE_FILE_CSV} or {WAREHOUSE_FILE}"
    )
CUSTOMER_MASTER = INPUT_DIR / "CUSTOMER_MASTER.xlsx"
CUSTOMER_MASTER_CSV = INPUT_DIR / "CUSTOMER_MASTER.csv"
STORE_LIST = INPUT_DIR / "STORE_GRADE.xlsx"
TOP_DOORS = INPUT_DIR / "TOP_SELLERS.xlsx"
# Written by Home upload Confirm — which channels to replenish this run
REPLENISHMENT_STORES_FILE = INPUT_DIR / "replenishment_stores.json"

SOH_NULL_OUT = OUTPUT_DIR / "soh_null_no_sales_transfer_days.csv"
STORE_SOH_NEW_OUT = OUTPUT_DIR / "store_soh_new.csv"

# Numeric Ginesys codes + Sales-Master Site Code (FGPL-*) for Fresco/EBO
EBO_SITE_IDS = {
    "528",
    "964",
    "972",
    "FGPL-MALLESHWARAM",
    "FGPL-VADODRA",
    "FGPL-KURLA",
}
EBO_SITE_CODE_TO_ID = {
    "FGPL-MALLESHWARAM": "528",
    "FGPL-VADODRA": "964",
    "FGPL-KURLA": "972",
}

# Closed stores — sales/SOH reporting mein rahenge, replenishment/transfer nahi.
CLOSED_REPLENISHMENT_SITE_IDS = frozenset({"972"})  # Fresco Global Pvt Ltd-Kurla
CLOSED_REPLENISHMENT_SITE_NAME_MARKERS = ("KURLA",)

# UI store channel id → pipeline store_type code
REPLENISHMENT_STORE_TYPE_MAP = {
    "pantaloons": "PT",
    "shoppers-stop": "SSL",
    "centro": "CENTRO",
    "iconic": "ICONIC",
    "ebo": "EBO",
}

# Store SOH blob categories (exclude warehouse) — dated history + last_week source
STORE_SOH_CATEGORY_BY_TYPE: dict[str, str] = {
    "PT": "pantaloons-soh",
    "SSL": "shoppers-stop-soh",
    "ICONIC": "iconic-soh",
    "EBO": "ebo-soh",
    "CENTRO": "centro-soh",
}
STORE_SOH_CATEGORY_IDS = set(STORE_SOH_CATEGORY_BY_TYPE.values())
# combine_soh Source label ↔ store_type (1:1)
STORE_TYPE_TO_SOH_SOURCE = {
    "PT": "PT",
    "SSL": "SSL",
    "ICONIC": "ICONIC",
    "EBO": "EBO",
    "CENTRO": "CENTRO",
}


def get_selected_store_types() -> set[str]:
    """
    Selected replenishment channels for this pipeline run (upper-case codes).
    Env PIPELINE_STORE_TYPES wins; else replenishment_stores.json.
    Empty set = no filter (all channels).
    """
    raw = (os.environ.get("PIPELINE_STORE_TYPES") or "").strip()
    if raw:
        return {t.strip().upper() for t in raw.split(",") if t.strip()}
    sel = load_replenishment_store_selection()
    return {str(t).strip().upper() for t in (sel.get("store_types") or []) if str(t).strip()}


def filter_df_by_store_type(
    df: pd.DataFrame,
    *,
    col: str = "store_type",
    selected: set[str] | None = None,
    log_prefix: str = "",
) -> pd.DataFrame:
    """Keep rows whose store_type is in the selected set. No-op if selection empty."""
    types = selected if selected is not None else get_selected_store_types()
    if not types or df is None or getattr(df, "empty", True):
        return df
    if col not in df.columns:
        return df
    before = len(df)
    out = df[df[col].astype(str).str.upper().str.strip().isin(types)].copy()
    if log_prefix:
        print(
            f"{log_prefix}Filtered store_type={sorted(types)}: {before:,} -> {len(out):,} rows",
            flush=True,
        )
    return out


def filter_soh_by_source(
    df: pd.DataFrame,
    *,
    source_col: str = "Source",
    selected: set[str] | None = None,
    log_prefix: str = "",
) -> pd.DataFrame:
    """Filter COMBINED_SOH-like frames by Source label (PT/SSL/...)."""
    types = selected if selected is not None else get_selected_store_types()
    if not types or df is None or getattr(df, "empty", True) or source_col not in df.columns:
        return df
    allowed = {STORE_TYPE_TO_SOH_SOURCE.get(t, t) for t in types}
    before = len(df)
    out = df[df[source_col].astype(str).str.upper().str.strip().isin(allowed)].copy()
    if log_prefix:
        print(
            f"{log_prefix}Filtered SOH Source={sorted(allowed)}: {before:,} -> {len(out):,} rows",
            flush=True,
        )
    return out


def load_replenishment_store_selection() -> dict:
    """
    Load local replenishment store selection (from Home Confirm).
    Returns dict with keys: stores, store_types, labels, saved_at (may be empty).
    """
    import json
    from datetime import datetime, timezone

    if not REPLENISHMENT_STORES_FILE.exists():
        return {"stores": [], "store_types": [], "labels": [], "saved_at": None, "path": str(REPLENISHMENT_STORES_FILE)}
    try:
        data = json.loads(REPLENISHMENT_STORES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"stores": [], "store_types": [], "labels": [], "saved_at": None, "path": str(REPLENISHMENT_STORES_FILE)}
    if not isinstance(data, dict):
        data = {}
    stores = [str(s).strip().lower() for s in (data.get("stores") or []) if str(s).strip()]
    store_types = data.get("store_types")
    if not store_types:
        store_types = [REPLENISHMENT_STORE_TYPE_MAP[s] for s in stores if s in REPLENISHMENT_STORE_TYPE_MAP]
    return {
        "stores": stores,
        "store_types": list(store_types or []),
        "labels": list(data.get("labels") or []),
        "saved_at": data.get("saved_at"),
        "path": str(REPLENISHMENT_STORES_FILE),
    }


def save_replenishment_store_selection(store_ids: list[str], labels: list[str] | None = None) -> dict:
    """Persist selection under pipeline/input_files/replenishment_stores.json (local only)."""
    import json
    from datetime import datetime, timezone

    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    stores = []
    for s in store_ids:
        sid = str(s).strip().lower()
        if sid in REPLENISHMENT_STORE_TYPE_MAP and sid not in stores:
            stores.append(sid)
    store_types = [REPLENISHMENT_STORE_TYPE_MAP[s] for s in stores]
    label_map = {
        "pantaloons": "Pantaloons (PT)",
        "shoppers-stop": "Shoppers Stop (SSL)",
        "centro": "Centro",
        "iconic": "Iconic",
        "ebo": "EBO",
    }
    out_labels = labels if labels is not None else [label_map[s] for s in stores]
    payload = {
        "stores": stores,
        "store_types": store_types,
        "labels": out_labels,
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }
    REPLENISHMENT_STORES_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    # Also expose for child pipeline scripts
    os.environ["PIPELINE_STORE_TYPES"] = ",".join(store_types)
    os.environ["PIPELINE_STORE_IDS"] = ",".join(stores)
    return {**payload, "path": str(REPLENISHMENT_STORES_FILE)}


def get_as_of_date() -> str:
    """Pipeline run ki current date (YYYY-MM-DD). Days-since / windows isi se."""
    return pd.Timestamp.today().normalize().strftime("%Y-%m-%d")


def format_week_date_range(week_start: pd.Timestamp) -> str:
    """Mon–Sun display range, e.g. '31 august - 6 september'."""
    week_end = week_start + pd.Timedelta(days=6)
    return (
        f"{week_start.day} {week_start.strftime('%B').lower()} - "
        f"{week_end.day} {week_end.strftime('%B').lower()}"
    )


def get_replenishment_week_anchor(reference: pd.Timestamp | str | None = None) -> dict[str, Any]:
    """
    ISO week containing the pipeline run date (Monday–Sunday).

    Replenishment Tue–Fri in the same calendar week keeps the same week number
    and date range (anchored to that week's Monday), not a new week per run day.
    """
    if reference is None:
        ref = pd.Timestamp(get_as_of_date())
    elif isinstance(reference, str):
        ref = pd.Timestamp(reference).normalize()
    else:
        ref = pd.Timestamp(reference).normalize()

    week_start = ref - pd.Timedelta(days=int(ref.dayofweek))
    week_end = week_start + pd.Timedelta(days=6)
    iso = week_start.isocalendar()
    year = int(iso.year)
    week = int(iso.week)
    return {
        "week_start": week_start,
        "week_end": week_end,
        "year": year,
        "week": week,
        "week_label": f"W{week} {year}",
        "week_start_date": week_start.strftime("%Y-%m-%d"),
        "week_date_range": format_week_date_range(week_start),
        "month": int(week_start.month),
    }


def norm_header_key(name: str) -> str:
    return re.sub(r"[\s_]+", "", str(name).strip().lower())


def find_column(df: pd.DataFrame, *candidates: str) -> str:
    lookup = {norm_header_key(c): c for c in df.columns}
    for cand in candidates:
        k = norm_header_key(cand)
        if k in lookup:
            return lookup[k]
    raise ValueError(f"Need a column like one of {candidates!r}. Found: {list(df.columns)}")


def normalize_barcode(series: pd.Series) -> pd.Series:
    num = pd.to_numeric(series, errors="coerce")
    out = pd.Series("", index=series.index, dtype="object")
    mask = num.notna()
    out.loc[mask] = num.loc[mask].astype("Int64").astype(str)
    out.loc[~mask] = series.loc[~mask].astype(str).str.strip()
    return out.str.replace(".0", "", regex=False)


def normalize_site_id_code(value: object) -> str:
    """
    Canonical site id for SOH / sales joins.

    Sales-Master often sends verbose codes:
      PT-Gariahat-(P001)  → P001
      SS-ORION MALL-294   → 294
      FGPL-Malleshwaram   → 528
    COMBINED_SOH uses compact P-codes / numeric SSL / EBO ids.
    """
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    s = str(value).strip().upper().replace(".0", "")
    if not s or s in {"NAN", "NONE", "<NA>", "NAT"}:
        return ""

    # Fresco / EBO FGPL-* → numeric
    if s in EBO_SITE_CODE_TO_ID:
        return EBO_SITE_CODE_TO_ID[s]
    if s.startswith("FGPL"):
        return EBO_SITE_CODE_TO_ID.get(s, s)

    # Already compact Pantaloons code
    if re.fullmatch(r"P\d{2,5}", s):
        return s
    # Pure numeric (SSL / EBO / other)
    if re.fullmatch(r"\d{2,6}", s):
        return s

    # (P123) anywhere
    m = re.search(r"\(\s*(P\d{2,5})\s*\)", s)
    if m:
        return m.group(1)
    # leading P123-
    m = re.match(r"(P\d{2,5})\b", s)
    if m:
        return m.group(1)

    # ( 294 ) or (8529)
    m = re.search(r"\(\s*(\d{2,6})\s*\)", s)
    if m:
        return m.group(1)
    # trailing -294 / - 484
    m = re.search(r"[\-\s](\d{2,6})\s*$", s)
    if m:
        return m.group(1)

    return s


def normalize_site(series: pd.Series) -> pd.Series:
    """Vectorized canonical site keys for joins (sales ↔ COMBINED_SOH)."""
    return series.map(normalize_site_id_code)


def is_replenishment_excluded_site(
    site_id: object = None,
    site_name: object = None,
) -> bool:
    """True for closed stores that must not receive replenishment or transfers."""
    if normalize_site_id_code(site_id) in CLOSED_REPLENISHMENT_SITE_IDS:
        return True
    name = str(site_name or "").strip().upper()
    return any(m in name for m in CLOSED_REPLENISHMENT_SITE_NAME_MARKERS)


def exclude_closed_replenishment_sites(
    df: pd.DataFrame,
    *,
    site_id_col: str | None = None,
    site_name_col: str | None = None,
    log_prefix: str = "",
) -> pd.DataFrame:
    """Drop closed-store rows from replenishment / transfer frames."""
    if df is None or getattr(df, "empty", True):
        return df
    sid_col = site_id_col or next(
        (c for c in df.columns if str(c).strip().lower() in ("site_id", "site code", "store code")),
        None,
    )
    sname_col = site_name_col or next(
        (c for c in df.columns if str(c).strip().lower() in ("site_name", "site name", "site")),
        None,
    )
    if sid_col is None and sname_col is None:
        return df
    sid = df[sid_col].map(normalize_site_id_code) if sid_col else pd.Series("", index=df.index)
    sname = df[sname_col].astype(str).str.upper() if sname_col else pd.Series("", index=df.index)
    closed = sid.isin(CLOSED_REPLENISHMENT_SITE_IDS)
    for marker in CLOSED_REPLENISHMENT_SITE_NAME_MARKERS:
        closed = closed | sname.str.contains(marker, na=False)
    before = len(df)
    out = df.loc[~closed].copy()
    if log_prefix and len(out) < before:
        print(
            f"{log_prefix}Excluded closed replenishment sites: {before:,} -> {len(out):,} rows",
            flush=True,
        )
    return out


def derive_store_type(site_name: object, site_id: object | None = None) -> str:
    code = "" if site_id is None else str(site_id).strip().upper().replace(".0", "")
    name = str(site_name).strip().upper()
    comb = f"{code} {name}".strip()

    # 1. Shoppers Stop (SSL)
    if re.search(r"SHOPPER|HOPPER|\bSS[- ]|\bSSL\b", comb):
        return "SSL"
    # 2. Pantaloons (PT)
    if re.search(r"PANTALOON|\bPT[- ]|\bPW[- ]|\bPT\b|^P\d{2,5}\b|ADITYA BIRLA", comb):
        return "PT"
    # 3. Centro
    if re.search(r"CENTRO|\bRRL[- ]|^TC3|^T8|RELIANCE", comb):
        return "CENTRO"
    # 4. Iconic
    if re.search(r"ICONIC", comb):
        return "ICONIC"
    # 5. EBO
    if code in EBO_SITE_IDS or re.search(r"EBO|FRESCO|^FGPL|\b(?:528|964|972)\b", comb):
        return "EBO"
    if "FASHION FACTORY" in comb:
        return "FASHION_FACTORY"
    return ""


def derive_store_type_series(site_name: pd.Series, site_id: pd.Series) -> pd.Series:
    """Vectorized store_type from Site_Name + Site_ID (fast filter for large frames).

    Priority matches scalar ``derive_store_type`` (first match wins).
    """
    code = (
        site_id.astype(str)
        .str.strip()
        .str.upper()
        .str.replace(r"\.0$", "", regex=True)
    )
    name = site_name.astype(str).str.strip().str.upper()
    comb = code + " " + name

    out = pd.Series("", index=site_name.index, dtype="object")
    blank = lambda: out.eq("")

    # 1. Shoppers Stop (SSL)
    out = out.mask(
        blank() & comb.str.contains(r"SHOPPER|HOPPER|\bSS[- ]|\bSSL\b", regex=True, na=False),
        "SSL",
    )
    # 2. Pantaloons (PT)
    out = out.mask(
        blank() & comb.str.contains(r"PANTALOON|\bPT[- ]|\bPW[- ]|\bPT\b|^P\d{2,5}\b|ADITYA BIRLA", regex=True, na=False),
        "PT",
    )
    # 3. Centro
    out = out.mask(
        blank() & comb.str.contains(r"CENTRO|\bRRL[- ]|^TC3|^T8|RELIANCE", regex=True, na=False),
        "CENTRO",
    )
    # 4. Iconic
    out = out.mask(blank() & comb.str.contains(r"ICONIC", regex=True, na=False), "ICONIC")
    # 5. EBO
    out = out.mask(
        blank() & (code.isin(EBO_SITE_IDS) | comb.str.contains(r"EBO|FRESCO|^FGPL|\b(?:528|964|972)\b", regex=True, na=False)),
        "EBO",
    )
    out = out.mask(blank() & comb.str.contains("FASHION FACTORY", regex=False, na=False), "FASHION_FACTORY")
    return out


def read_excel_fast(
    path: Path,
    *,
    sheet_name: Any = 0,
    header: int | None = None,
    usecols: list[str] | None = None,
    column_hints: list[str] | None = None,
    auto_header: bool = True,
    auto_sheet: bool = True,
    preferred_header: int | None = None,
    preferred_sheet: Any = None,
) -> pd.DataFrame:
    """Prefer calamine; auto-detect header row and (for Excel) sheet via column_hints."""
    path = Path(path)
    hints = column_hints or (list(usecols) if usecols else None)
    hdr = header
    sheet = sheet_name
    lower = path.suffix.lower()

    # Misnamed Excel blobs (ITEM_MASTER.csv that is actually .xlsx)
    head = b""
    try:
        with path.open("rb") as fh:
            head = fh.read(8)
    except OSError:
        head = b""
    is_excel_bytes = head[:2] == b"PK" or head[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    if is_excel_bytes and lower == ".csv":
        lower = ".xlsx"

    if hdr is None and auto_header and lower == ".csv":
        try:
            from pipeline.header_detect import detect_excel_layout_path
        except ImportError:
            from header_detect import detect_excel_layout_path

        # Multiline-quoted CSV headers need the csv.reader-based detector
        # (preferred_header is a soft hint inside detect_csv_header_row).
        hdr, _ = detect_excel_layout_path(
            path,
            column_hints=hints,
            preferred_header=preferred_header,
        )
    elif hdr is None and auto_header and lower in (".xlsx", ".xls", ".xlsm"):
        try:
            from pipeline.header_detect import detect_excel_layout_path
        except ImportError:
            from header_detect import detect_excel_layout_path

        pref_sheet = preferred_sheet
        if pref_sheet is None and isinstance(sheet_name, str):
            pref_sheet = sheet_name
        hdr, detected = detect_excel_layout_path(
            path,
            sheet_name=sheet_name,
            column_hints=hints,
            preferred_header=preferred_header,
            preferred_sheet=pref_sheet,
            auto_sheet=auto_sheet,
        )
        if detected is not None:
            sheet = detected
    if hdr is None:
        hdr = preferred_header if preferred_header is not None else 0

    if lower == ".csv" and not is_excel_bytes:
        try:
            df = pd.read_csv(path, header=hdr, low_memory=False, encoding="utf-8-sig")
        except UnicodeDecodeError:
            df = pd.read_csv(path, header=hdr, low_memory=False, encoding="latin-1")
        df.columns = df.columns.astype(str).str.strip()
        return df

    kwargs: dict = {"sheet_name": sheet, "header": hdr}
    if usecols is not None:
        kwargs["usecols"] = usecols
    try:
        df = pd.read_excel(path, engine="calamine", **kwargs)
    except Exception:
        df = pd.read_excel(path, engine="openpyxl", **kwargs)
    if isinstance(df, dict):
        df = next(iter(df.values()))
    df.columns = df.columns.astype(str).str.strip()
    return df


def write_combined_tabular(df: pd.DataFrame, xlsx_path: Path) -> Path:
    """
    Always write both CSV and XLSX for combined masters (sales / transfer / SOH).
    Returns csv_path (preferred for fast downstream loaders).
    """
    xlsx_path = Path(xlsx_path)
    csv_path = xlsx_path.with_suffix(".csv")
    xlsx_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(csv_path, index=False)
    print(f"Saved CSV                  : {csv_path} ({len(df):,} rows)", flush=True)
    try:
        with pd.ExcelWriter(xlsx_path, engine="xlsxwriter", engine_kwargs={"options": {"constant_memory": True}}) as writer:
            df.to_excel(writer, index=False)
        print(f"Saved XLSX                 : {xlsx_path}", flush=True)
    except Exception as e:
        try:
            df.to_excel(xlsx_path, index=False)
            print(f"Saved XLSX                 : {xlsx_path}", flush=True)
        except Exception as e2:
            print(f"[warn] xlsx write failed: {e}; retry: {e2}", flush=True)
    return csv_path


def save_output_pair(
    df: pd.DataFrame,
    out_path: Path,
    *,
    sheet_name: str = "Sheet1",
    utf8_sig: bool = False,
) -> Path:
    """Write pipeline output as CSV; also XLSX when path is or has .xlsx sibling."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.suffix.lower() == ".csv":
        csv_path = out_path
        xlsx_path = out_path.with_suffix(".xlsx")
    else:
        xlsx_path = out_path if out_path.suffix.lower() in (".xlsx", ".xls") else out_path.with_suffix(".xlsx")
        csv_path = xlsx_path.with_suffix(".csv")
    enc = "utf-8-sig" if utf8_sig else "utf-8"
    df.to_csv(csv_path, index=False, encoding=enc)
    try:
        df.to_excel(xlsx_path, index=False, sheet_name=sheet_name, engine="xlsxwriter")
    except Exception:
        try:
            df.to_excel(xlsx_path, index=False, sheet_name=sheet_name)
        except Exception as e:
            print(f"[warn] xlsx write skipped for {xlsx_path.name}: {e}", flush=True)
    return csv_path


def parse_sales_dates(series: Any) -> pd.Series:
    """Parse sales Date values when PT (YYYY-MM-DD) and EBO (YYYY-MM-DD HH:MM:SS) are combined."""
    if isinstance(series, pd.DataFrame):
        series = series.iloc[:, 0]
    if pd.api.types.is_datetime64_any_dtype(series):
        return series
    s = series.astype(str).str.strip().replace({"nan": "", "None": "", "<NA>": ""})
    s = s.mask(s.eq(""), pd.NA)
    return pd.to_datetime(s, format="mixed", errors="coerce")


def build_barcode_dept_section_map() -> pd.DataFrame:
    """
    barcode_key → Department, Section from PT/EBO sales source files.
    PT: Department + Section; EBO: DIVISION → Department, SECTION → Section.
    """
    from pathlib import Path

    frames: list[pd.DataFrame] = []
    cache = Path(__file__).resolve().parent.parent / "data" / "blob_cache"
    candidates = [
        (
            cache / "input_Sales_PT_SSL_Centro_Iconic_current_SSI_PT_CENTRO_ICONIC_SALES.csv",
            "Department",
            "Section",
        ),
        (
            cache / "input_EBO_Sales_current_EBO_SALES.csv",
            "DIVISION",
            "SECTION",
        ),
    ]
    # Also try pipeline input_files / input folders
    extra_roots = [
        Path(__file__).resolve().parent.parent / "input",
        Path(__file__).resolve().parent / "input_files",
    ]
    for root in extra_roots:
        if not root.exists():
            continue
        for p in root.rglob("*.csv"):
            name = p.name.upper()
            if "SSI_PT" in name or "CENTRO_ICONIC_SALES" in name:
                candidates.append((p, "Department", "Section"))
            elif "EBO_SALES" in name:
                candidates.append((p, "DIVISION", "SECTION"))

    seen: set[str] = set()
    for path, dept_col, sec_col in candidates:
        key = str(path.resolve()) if path.exists() else ""
        if not key or key in seen:
            continue
        seen.add(key)
        try:
            cols = pd.read_csv(path, nrows=0).columns.tolist()
            by_u = {str(c).strip().upper(): c for c in cols}
            bc = by_u.get("BARCODE")
            if not bc:
                continue
            dept_src = by_u.get(dept_col.upper()) or by_u.get("DEPARTMENT") or by_u.get("DIVISION")
            sec_src = by_u.get(sec_col.upper()) or by_u.get("SECTION")
            usecols = [bc] + ([dept_src] if dept_src else []) + ([sec_src] if sec_src else [])
            raw = pd.read_csv(path, usecols=usecols, low_memory=False)
            part = pd.DataFrame({"barcode_key": normalize_barcode(raw[bc])})
            part["Department"] = (
                raw[dept_src].astype(str).str.strip().replace({"nan": "", "None": "", "<NA>": ""})
                if dept_src
                else ""
            )
            part["Section"] = (
                raw[sec_src].astype(str).str.strip().replace({"nan": "", "None": "", "<NA>": ""})
                if sec_src
                else ""
            )
            part = part[part["barcode_key"].astype(str).str.len() > 0]
            frames.append(part)
        except Exception:
            continue

    if not frames:
        return pd.DataFrame(columns=["barcode_key", "Department", "Section"])

    all_rows = pd.concat(frames, ignore_index=True)
    # Prefer non-blank department/section per barcode
    all_rows["_score"] = (
        all_rows["Department"].astype(str).str.len().clip(upper=1)
        + all_rows["Section"].astype(str).str.len().clip(upper=1)
    )
    all_rows = all_rows.sort_values("_score", ascending=False)
    return (
        all_rows.drop_duplicates(subset=["barcode_key"], keep="first")
        .drop(columns=["_score"])
        .reset_index(drop=True)
    )


def enrich_sales_dept_section(sales: pd.DataFrame, *, barcode_col: str = "barcode") -> pd.DataFrame:
    """Fill blank department/section on sales rows from barcode map (source sales files)."""
    out = sales.copy()
    col_u = {str(c).strip().upper(): c for c in out.columns}
    if "DEPARTMENT" in col_u and "department" not in out.columns:
        out = out.rename(columns={col_u["DEPARTMENT"]: "department"})
    if "SECTION" in col_u and "section" not in out.columns:
        out = out.rename(columns={col_u["SECTION"]: "section"})
    if "department" not in out.columns:
        out["department"] = ""
    if "section" not in out.columns:
        out["section"] = ""
    out["department"] = out["department"].astype(str).str.strip().replace({"nan": "", "None": "", "<NA>": ""})
    out["section"] = out["section"].astype(str).str.strip().replace({"nan": "", "None": "", "<NA>": ""})

    if barcode_col not in out.columns:
        return out
    need = out["department"].eq("") | out["section"].eq("")
    if not need.any():
        return out

    try:
        amap = build_barcode_dept_section_map()
    except Exception:
        return out
    if amap.empty:
        return out

    keys = normalize_barcode(out[barcode_col])
    lookup_d = dict(zip(amap["barcode_key"].astype(str), amap["Department"].astype(str)))
    lookup_s = dict(zip(amap["barcode_key"].astype(str), amap["Section"].astype(str)))
    mapped_d = keys.map(lookup_d).fillna("")
    mapped_s = keys.map(lookup_s).fillna("")
    blank_d = out["department"].eq("")
    blank_s = out["section"].eq("")
    out.loc[blank_d, "department"] = mapped_d.loc[blank_d].values
    out.loc[blank_s, "section"] = mapped_s.loc[blank_s].values
    return out


def _densest_series(block: pd.DataFrame | pd.Series, name: str) -> pd.Series:
    """Pick the column slice with the most non-null values."""
    if isinstance(block, pd.Series):
        return block.rename(name)
    counts = [int(block.iloc[:, i].notna().sum()) for i in range(block.shape[1])]
    best = int(max(range(len(counts)), key=lambda i: counts[i])) if counts else 0
    return block.iloc[:, best].rename(name)


def _dedupe_frame_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Collapse duplicate / case-variant headers; keep the densest (most non-null) series.

    SSI sales exports often ship both ``Barcode`` (populated) and ``BARCODE`` (empty).
    Renaming BARCODE→Barcode then naively keeping the first duplicate can wipe barcodes
    and collapse the replenishment grid to one row per site (~200 rows, ~200KB).
    """
    out = df.copy()
    out.columns = out.columns.astype(str).str.strip()
    if out.columns.empty:
        return out

    # Exact-name duplicates — prefer densest column in each name group.
    if not out.columns.is_unique:
        pieces: list[pd.Series] = []
        seen: set[str] = set()
        for name in out.columns:
            key = str(name)
            if key in seen:
                continue
            seen.add(key)
            pieces.append(_densest_series(out.loc[:, out.columns == name], key))
        out = pd.concat(pieces, axis=1)

    # Case-insensitive duplicates (Barcode vs BARCODE).
    upper_groups: dict[str, list[str]] = {}
    for c in out.columns:
        upper_groups.setdefault(str(c).upper(), []).append(str(c))
    drop: list[str] = []
    renames: dict[str, str] = {}
    for upper, names in upper_groups.items():
        if len(names) < 2:
            continue
        scored = sorted(names, key=lambda n: int(out[n].notna().sum()), reverse=True)
        keep = scored[0]
        drop.extend(scored[1:])
        if upper == "BARCODE" and keep != "Barcode":
            renames[keep] = "Barcode"
    if drop:
        out = out.drop(columns=drop)
    if renames:
        out = out.rename(columns=renames)
    return out


def _normalize_sales_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Map raw shared/EBO sales columns → pipeline schema if needed."""
    df = _dedupe_frame_columns(df)
    cols = {str(c).strip(): c for c in df.columns}
    if "Site_ID" in cols and "Date" in cols:
        out = _dedupe_frame_columns(df)
        col_upper = {str(c).strip().upper(): c for c in out.columns}
        sku_aliases = ("SKU", "CATEGORY3", "CATEGORY 3", "ITEM CODE", "ITEM_CODE", "ARTICLE", "STYLE", "ICODE")
        size_aliases = ("SIZE", "Size", "CATEGORY5", "CATEGORY 5", "ITEM SIZE", "SIZE CODE", "SIZE_CODE")
        if "SKU" not in out.columns:
            sku_src = next((col_upper[a.upper()] for a in sku_aliases if a.upper() in col_upper), None)
            out["SKU"] = out[sku_src] if sku_src else ""
        if "Size" not in out.columns:
            size_src = next((col_upper[a.upper()] for a in size_aliases if a.upper() in col_upper), None)
            out["Size"] = out[size_src] if size_src else ""
        tax_aliases = ("TAX_AMOUNT", "TAX AMOUNT", "Tax_Amount", "Tax Amount", "GST_AMOUNT", "GST AMOUNT")
        if "TAX_AMOUNT" not in out.columns:
            tax_src = next((col_upper[a.upper()] for a in tax_aliases if a.upper() in col_upper), None)
            if tax_src:
                out["TAX_AMOUNT"] = out[tax_src]
        dept_aliases = ("DEPARTMENT", "DIVISION")
        section_aliases = ("SECTION",)
        if "Department" not in out.columns:
            dept_src = next((col_upper[a] for a in dept_aliases if a in col_upper), None)
            if dept_src:
                out["Department"] = out[dept_src]
        if "Section" not in out.columns:
            sec_src = next((col_upper[a] for a in section_aliases if a in col_upper), None)
            if sec_src:
                out["Section"] = out[sec_src]
        for c in ("SKU", "Size"):
            out[c] = out[c].astype(str).str.strip().replace({"nan": "", "None": "", "<NA>": ""})
        if "Site_ID" in out.columns:
            sid = out["Site_ID"].map(normalize_site_id_code)
            if "Site_Name" in out.columns:
                from_name = out["Site_Name"].map(normalize_site_id_code)
                weak = ~sid.str.fullmatch(r"(P\d{2,5}|\d{2,6})", na=False)
                sid = sid.where(~weak, from_name.where(from_name.str.len() > 0, sid))
            out["Site_ID"] = sid
        return _dedupe_frame_columns(out)

    # SSI shared export is often a dual-schema dump: populated Site/Site Code/Barcode/Qty
    # beside empty STORE CODE/TRANSACTION_SITE/BARCODE/QUANTITY leftovers. Map both;
    # _dedupe_frame_columns keeps the densest series after rename.
    pt_map = {
        "ENTRY_DATE": "Date",
        "STORE CODE": "Site_ID",
        "Site Code": "Site_ID",
        "SITE CODE": "Site_ID",
        "Site Short Code": "Site_ID",
        "TRANSACTION_SITE": "Site_Name",
        "Site": "Site_Name",
        "Site Name": "Site_Name",
        "Barcode": "Barcode",
        "BARCODE": "Barcode",
        "CATEGORY3": "SKU",
        "Cat3": "SKU",
        "CATEGORY5": "Size",
        "Cat5": "Size",
        "QUANTITY": "Qty_Sold",
        "Qty": "Qty_Sold",
        "RSP": "MRP",
        "MRP": "MRP",
        "NET_AMOUNT": "Net_sales_amount",
        "Item Net Amt": "Net_sales_amount",
        "ITEM DISCOUNT_AMOUNT": "Discount_amount",
        "Discount Amt": "Discount_amount",
        "CATEGORY6": "SEASON",
        "Cat6": "SEASON",
    }
    ebo_map = {
        "BILL DATE": "Date",
        "SITE CODE": "Site_ID",
        "SITE NAME": "Site_Name",
        "Barcode": "Barcode",
        "BARCODE": "Barcode",
        "CATEGORY 3": "SKU",
        "CATEGORY 5": "Size",
        "BILL_QUANTITY": "Qty_Sold",
        "MRP": "MRP",
        "NET AMOUNT": "Net_sales_amount",
        "DISCOUNT AMOUNT": "Discount_amount",
        "CATEGORY 6": "SEASON",
    }
    # Sales-Master (PT/SSL/Centro/Iconic/EBO) flat export
    master_map = {
        "Invoice Date": "Date",
        "INVOICE DATE": "Date",
        "Site Code": "Site_ID",
        "SITE CODE": "Site_ID",
        "Site Short Code": "Site_ID",
        "Ref Site": "Site_Name",
        "Site Name": "Site_Name",
        "Site": "Site_Name",  # Sales-Master uses "Site" (e.g. Fresco Global Pvt Ltd-Kurla)
        "TRANSACTION_SITE": "Site_Name",
        "Barcode": "Barcode",
        "BARCODE": "Barcode",
        "Cat3": "SKU",
        "CAT3": "SKU",
        "CATEGORY3": "SKU",
        "Cat5": "Size",
        "CAT5": "Size",
        "CATEGORY5": "Size",
        "Qty": "Qty_Sold",
        "QTY": "Qty_Sold",
        "QUANTITY": "Qty_Sold",
        "Bill Qty": "Qty_Sold",
        "BILL QTY": "Qty_Sold",
        "RSP": "MRP",
        "MRP": "MRP",
        "Item Net Amt": "Net_sales_amount",
        "NET AMOUNT": "Net_sales_amount",
        "NET_AMOUNT": "Net_sales_amount",
        "Discount Amt": "Discount_amount",
        "DISCOUNT AMOUNT": "Discount_amount",
        "Cat6": "SEASON",
        "CAT6": "SEASON",
        "CATEGORY6": "SEASON",
        "Channel Name": "Channel",
    }
    use_map = None
    has_barcode = "BARCODE" in cols or "Barcode" in cols
    has_pt_site = any(k in cols for k in ("STORE CODE", "Site Code", "SITE CODE", "Site Short Code"))
    if ("Invoice Date" in cols or "INVOICE DATE" in cols) and (
        "Site Code" in cols or "SITE CODE" in cols or "Site Short Code" in cols
    ):
        use_map = master_map
    elif "ENTRY_DATE" in cols and has_pt_site and has_barcode:
        use_map = pt_map
    elif "BILL DATE" in cols and "SITE CODE" in cols and has_barcode:
        use_map = ebo_map
    if use_map is None:
        return df

    rename = {cols[k]: v for k, v in use_map.items() if k in cols}
    out = _dedupe_frame_columns(df.rename(columns=rename))

    # SKU / Size from sales file columns when present (no Item Master).
    sku_aliases = ("SKU", "CATEGORY3", "CATEGORY 3", "ITEM CODE", "ITEM_CODE", "ARTICLE", "STYLE", "ICODE")
    size_aliases = ("SIZE", "Size", "CATEGORY5", "CATEGORY 5", "ITEM SIZE", "SIZE CODE", "SIZE_CODE")
    tax_aliases = ("TAX_AMOUNT", "TAX AMOUNT", "Tax_Amount", "Tax Amount", "GST_AMOUNT", "GST AMOUNT")
    col_upper = {str(c).strip().upper(): c for c in df.columns}
    sku_src = next((col_upper[a] for a in sku_aliases if a.upper() in col_upper), None)
    size_src = next((col_upper[a] for a in size_aliases if a.upper() in col_upper), None)
    tax_src = next((col_upper[a.upper()] for a in tax_aliases if a.upper() in col_upper), None)
    if sku_src is not None and "SKU" not in out.columns:
        out["SKU"] = df[sku_src]
    if size_src is not None and "Size" not in out.columns:
        out["Size"] = df[size_src]
    if tax_src is not None and "TAX_AMOUNT" not in out.columns:
        out["TAX_AMOUNT"] = df[tax_src]

    keep = [
        "Date",
        "Site_ID",
        "Site_Name",
        "Barcode",
        "SKU",
        "Size",
        "Qty_Sold",
        "MRP",
        "Net_sales_amount",
        "Discount_amount",
        "TAX_AMOUNT",
        "SEASON",
    ]
    for c in keep:
        if c not in out.columns:
            out[c] = "" if c in ("Site_Name", "SEASON", "SKU", "Size") else pd.NA
    out = _dedupe_frame_columns(out[keep])
    out["Date"] = parse_sales_dates(out["Date"])
    for c in ("Qty_Sold", "MRP", "Net_sales_amount", "Discount_amount", "TAX_AMOUNT"):
        out[c] = pd.to_numeric(out[c], errors="coerce")
    for c in ("SKU", "Size"):
        out[c] = out[c].astype(str).str.strip().replace({"nan": "", "None": "", "<NA>": ""})
    # Canonical Site_ID for SOH joins (P001 / 294 / 528 — not verbose Sales-Master labels)
    if "Site_ID" in out.columns:
        sid = out["Site_ID"].map(normalize_site_id_code)
        if "Site_Name" in out.columns:
            from_name = out["Site_Name"].map(normalize_site_id_code)
            # If Site_ID stayed verbose (no P-code/digits extracted), try Site_Name
            weak = ~sid.str.fullmatch(r"(P\d{2,5}|\d{2,6})", na=False)
            sid = sid.where(~weak, from_name.where(from_name.str.len() > 0, sid))
        out["Site_ID"] = sid
    return out


def _is_ebo_site(site_name: object, site_id: object | None = None) -> bool:
    """True for Fresco/EBO numeric ids, FGPL-* codes, or Fresco site names."""
    code = "" if site_id is None else str(site_id).strip().upper().replace(".0", "")
    name = str(site_name or "").strip().upper()
    if code in {x.upper() for x in EBO_SITE_IDS} or code in EBO_SITE_CODE_TO_ID:
        return True
    if code.startswith("FGPL"):
        return True
    return "FRESCO GLOBAL PVT LTD" in name or name.startswith("FRESCO")


def _load_transfer_columns_helpers() -> tuple[Any, Any]:
    """Return (has_transfer_dispatch_shape, normalize_transfer_dispatch_columns) or (None, None)."""
    import importlib.util
    import sys

    path = _SCRIPT_DIR / "transfer_columns.py"
    if path.is_file():
        mod_name = "_stockpulse_transfer_columns_inputs"
        mod = sys.modules.get(mod_name)
        if mod is None:
            spec = importlib.util.spec_from_file_location(mod_name, path)
            if spec is not None and spec.loader is not None:
                mod = importlib.util.module_from_spec(spec)
                sys.modules[mod_name] = mod
                spec.loader.exec_module(mod)
        if mod is not None:
            return (
                getattr(mod, "has_transfer_dispatch_shape", None),
                getattr(mod, "normalize_transfer_dispatch_columns", None),
            )

    try:
        from transfer_columns import (  # type: ignore
            has_transfer_dispatch_shape,
            normalize_transfer_dispatch_columns,
        )
        return has_transfer_dispatch_shape, normalize_transfer_dispatch_columns
    except ImportError:
        try:
            from pipeline.transfer_columns import (  # type: ignore
                has_transfer_dispatch_shape,
                normalize_transfer_dispatch_columns,
            )
            return has_transfer_dispatch_shape, normalize_transfer_dispatch_columns
        except ImportError:
            return None, None


def _normalize_transfer_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Map raw warehouse/EBO transfer columns → pipeline schema if needed."""
    cols = {str(c).strip(): c for c in df.columns}
    if "site_id" in cols and "Transfer_Date" in cols and "Barcode" in cols:
        out = df.copy()
        out.columns = out.columns.astype(str).str.strip()
        return out

    has_transfer_dispatch_shape, normalize_transfer_dispatch_columns = _load_transfer_columns_helpers()

    if has_transfer_dispatch_shape and normalize_transfer_dispatch_columns and has_transfer_dispatch_shape(df):
        df = normalize_transfer_dispatch_columns(df)
        cols = {str(c).strip(): c for c in df.columns}

    if "DC_DATE" in cols and "BARCODE" in cols and "INVOICE_QUANTITY" in cols:
        out = pd.DataFrame()
        try:
            from combine_transfer import parse_site_id_from_warehouse

            name_s = df[cols["SITE NAME"]] if "SITE NAME" in cols else pd.Series([""] * len(df))
            short_s = (
                df[cols["SITE SHORT NAME"]] if "SITE SHORT NAME" in cols else pd.Series([""] * len(df))
            )
            out["site_id"] = [
                parse_site_id_from_warehouse(n, s) for n, s in zip(name_s, short_s)
            ]
        except Exception:
            if "SITE SHORT NAME" in cols:
                out["site_id"] = df[cols["SITE SHORT NAME"]].astype(str).str.strip()
            elif "SITE NAME" in cols:
                out["site_id"] = df[cols["SITE NAME"]].astype(str).str.strip()
            else:
                out["site_id"] = ""
        out["Barcode"] = df[cols["BARCODE"]].astype(str).str.strip()
        out["Transfer_Date"] = pd.to_datetime(df[cols["DC_DATE"]], errors="coerce", dayfirst=True)
        out["Quantity"] = pd.to_numeric(df[cols["INVOICE_QUANTITY"]], errors="coerce")
        out["SEASON"] = (
            df[cols["CATEGORY6"]].astype(str).str.strip() if "CATEGORY6" in cols else ""
        )
        return out

    to_site = next(
        (cols[k] for k in cols if norm_header_key(k) in ("tosite",)),
        None,
    )
    transfer_date = next(
        (
            cols[k]
            for k in cols
            if norm_header_key(k)
            in ("transferdate", "transferindate", "dcdate", "date")
        ),
        None,
    )
    if transfer_date and "BARCODE" in cols and to_site:
        ebo_map = {
            "Fresco Global Pvt Ltd-Malleshwaram": "528",
            "Fresco Global Pvt Ltd-Inorbit Mall Vadodra": "964",
            "Fresco Global Pvt Ltd-Kurla": "972",
        }
        out = pd.DataFrame()
        out["site_id"] = df[to_site].astype(str).str.strip().map(ebo_map)
        out["Barcode"] = df[cols["BARCODE"]].astype(str).str.strip()
        out["Transfer_Date"] = pd.to_datetime(df[transfer_date], errors="coerce")
        qty_col = cols.get("Quantity") or cols.get("QUANTITY") or cols.get("TRANSFER_IN_QUANTITY")
        out["Quantity"] = pd.to_numeric(df[qty_col], errors="coerce") if qty_col else 0
        cat = cols.get("CAT 6") or cols.get("CATEGORY6")
        out["SEASON"] = df[cat].astype(str).str.strip() if cat else ""
        return out

    return df


def load_tabular_prefer_csv(xlsx_path: Path, *, label: str = "file") -> pd.DataFrame:
    csv_path = xlsx_path.with_suffix(".csv")
    if csv_path.exists():
        df = pd.read_csv(csv_path, low_memory=False)
        df.columns = df.columns.astype(str).str.strip()
        return df
    if xlsx_path.exists():
        df = read_excel_fast(xlsx_path)
        return df
    raise FileNotFoundError(f"Run combine first. Missing: {csv_path} or {xlsx_path} ({label})")


def load_combined_soh() -> pd.DataFrame:
    df = load_tabular_prefer_csv(COMBINED_SOH, label="COMBINED_SOH")
    df.columns = df.columns.astype(str).str.strip()
    return df


def resolve_pipeline_sales_path() -> Path:
    """Sales (PT, SSL, Centro, Iconic, EBO) current file — pipeline sales input."""
    for p in PIPELINE_SALES_CANDIDATES:
        if p.exists() and p.stat().st_size > 0:
            return p
    try:
        from api.blob_store import BlobJsonStore

        path = BlobJsonStore().ensure_input_candidates(_BLOB_PIPELINE_SALES_CANDIDATES)
        if path is not None and path.exists() and path.stat().st_size > 0:
            return path
    except Exception:
        pass
    tried = "\n".join(f"  - {p}" for p in PIPELINE_SALES_CANDIDATES)
    raise FileNotFoundError(
        "Sales (PT, SSL, Centro, Iconic, EBO) not found. Upload SSI_PT_CENTRO_ICONIC_SALES.\n"
        + tried
    )


def _resolve_ebo_sales_path() -> Path | None:
    """Optional EBO_SALES.* (flat or EBO Sales/current) — concatenated into pipeline sales."""
    candidates = (
        INPUT_DIR / "EBO_SALES.csv",
        INPUT_DIR / "EBO_SALES.xlsx",
        INPUT_DIR / "EBO Sales" / "current" / "EBO_SALES.csv",
        INPUT_DIR / "EBO Sales" / "current" / "EBO_SALES.xlsx",
        INPUT_DIR / "EBO - Sales" / "current" / "EBO_SALES.csv",
        INPUT_DIR / "EBO - Sales" / "current" / "EBO_SALES.xlsx",
    )
    for p in candidates:
        if p.exists() and p.stat().st_size > 0:
            return p
    try:
        from api.blob_store import BlobJsonStore

        path = BlobJsonStore().ensure_input_candidates(
            (
                "EBO Sales/current/EBO_SALES.csv",
                "EBO Sales/current/EBO_SALES.xlsx",
                "EBO - Sales/current/EBO_SALES.csv",
                "EBO - Sales/current/EBO_SALES.xlsx",
            )
        )
        if path is not None and path.exists() and path.stat().st_size > 0:
            return path
    except Exception:
        pass
    return None


def load_combined_sales() -> pd.DataFrame:
    """Load PT/SSL/Centro/Iconic sales + optional EBO_SALES concat — not COMBINED_SALES."""
    path = resolve_pipeline_sales_path()
    if path.suffix.lower() == ".csv":
        df = pd.read_csv(path, low_memory=False)
    else:
        df = read_excel_fast(path)
    df.columns = df.columns.astype(str).str.strip()
    main = _normalize_sales_frame(df)

    # The shared sales report is authoritative for all five channels.
    return main


def resolve_pipeline_transfer_path() -> Path:
    """Transfer - (PT, SSL, Centro, Iconic)/current/SALES_INVOICE_TRACKING.* — live master only."""
    for p in PIPELINE_TRANSFER_CANDIDATES:
        if p.exists() and p.stat().st_size > 0:
            return p
    # Any non-sidecar file under Transfer current/
    cur = INPUT_DIR / _TRANSFER_FOLDER / "current"
    if cur.is_dir():
        for p in sorted(cur.iterdir()):
            if not p.is_file() or p.stat().st_size <= 0:
                continue
            low = p.name.lower()
            if low.endswith((".etag", ".json")) or ".date_bounds" in low or ".delta." in low:
                continue
            if low.endswith((".csv", ".xlsx", ".xls")):
                return p
    try:
        from api.blob_store import BlobJsonStore

        path = BlobJsonStore().ensure_input_candidates(_BLOB_PIPELINE_TRANSFER_CANDIDATES)
        if path is not None and path.exists() and path.stat().st_size > 0:
            return path
    except Exception:
        pass
    tried = "\n".join(f"  - {p}" for p in PIPELINE_TRANSFER_CANDIDATES)
    raise FileNotFoundError(
        "Transfer - (PT, SSL, Centro, Iconic) not found (expected SALES_INVOICE_TRACKING.*).\n"
        + tried
    )


def resolve_pipeline_transfer_ebo_path() -> Path | None:
    """Optional Transfer - EBO/current/EBO_INVOICE_TRACKING.*."""
    for p in PIPELINE_TRANSFER_EBO_CANDIDATES:
        if p.exists() and p.stat().st_size > 0:
            return p
    try:
        from api.blob_store import BlobJsonStore

        path = BlobJsonStore().ensure_input_candidates(_BLOB_PIPELINE_TRANSFER_EBO_CANDIDATES)
        if path is not None and path.exists() and path.stat().st_size > 0:
            return path
    except Exception:
        pass
    return None


def _read_transfer_file(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".csv":
        df = pd.read_csv(path, low_memory=False)
    else:
        df = read_excel_fast(path)
    df.columns = df.columns.astype(str).str.strip()
    return _normalize_transfer_frame(df)


def load_combined_transfer() -> pd.DataFrame:
    """
    Live transfer only:
      - Transfer - (PT, SSL, Centro, Iconic)/current/SALES_INVOICE_TRACKING.*
        (Contains all channels including EBO)
    Never uses output_files/COMBINED_TRANSFER or separate EBO_INVOICE_TRACKING files.
    """
    path = resolve_pipeline_transfer_path()
    df = _read_transfer_file(path)
    print(f"[transfer] loaded {path.name} ({len(df):,} rows)", flush=True)
    return df


def _item_master_has_barcode(df: pd.DataFrame) -> bool:
    return any("barcode" in str(c).strip().lower() for c in df.columns)


def resolve_item_master_path() -> Path:
    """Prefer ITEM_MASTER.xlsx; fall back to ITEM_MASTER.csv (often a misnamed xlsx)."""
    xlsx = ITEM_MASTER_FILE
    csv = ITEM_MASTER_FILE.with_suffix(".csv")
    if xlsx.exists():
        return xlsx
    if csv.exists():
        return csv
    raise FileNotFoundError(f"Item master not found: {xlsx} or {csv}")


def _item_master_sheet(path: Path | None = None) -> Any:
    path = path or resolve_item_master_path()
    try:
        names = pd.ExcelFile(path).sheet_names
    except Exception:
        return ITEM_MASTER_SHEET
    if ITEM_MASTER_SHEET in names:
        return ITEM_MASTER_SHEET
    return names[0] if names else ITEM_MASTER_SHEET


def load_item_master() -> pd.DataFrame:
    path = resolve_item_master_path()

    sheet = _item_master_sheet(path)
    df = read_excel_fast(
        path,
        sheet_name=sheet,
        column_hints=list(ITEM_MASTER_HINTS),
        preferred_header=ITEM_MASTER_HEADER,
        preferred_sheet=sheet,
    )
    if _item_master_has_barcode(df):
        return df

    # Vendor exports ship with a varying number of title rows above the header.
    raw = pd.read_excel(path, sheet_name=sheet, header=None)
    for i in range(min(len(raw), 15)):
        labels = [str(c).strip() for c in raw.iloc[i].tolist()]
        if any(x.lower() == "barcode" for x in labels):
            out = raw.iloc[i + 1 :].reset_index(drop=True)
            out.columns = labels
            return out

    raise ValueError(
        f"Item master header row not found in {path.name} "
        f"(sheet={sheet!r}); expected a 'BARCODE' column."
    )


def build_item_map(item_df: pd.DataFrame | None = None) -> pd.DataFrame:
    """One row per barcode: MRP, SKU, SIZE."""
    item = item_df if item_df is not None else load_item_master()
    bc = find_column(item, "BARCODE", "Barcode")
    mrp = find_column(item, "MRP", "Mrp")
    sku = find_column(item, "SKU", "SKU CODE", "ITEM_CODE", "Sku")
    size = find_column(item, "SIZE", "Size")

    x = item.copy()
    x["barcode_key"] = normalize_barcode(x[bc])
    x["MRP"] = pd.to_numeric(x[mrp], errors="coerce")
    x["SKU"] = x[sku].astype(str).str.strip().str.upper()
    x["SIZE"] = x[size].astype(str).str.strip().str.upper()
    for col in ("SKU", "SIZE"):
        x.loc[x[col].str.lower().isin(["nan", "none", ""]), col] = ""
    x = x[x["barcode_key"].astype(str).str.len() > 0]
    return (
        x.sort_values(["barcode_key"])
        .drop_duplicates(subset=["barcode_key"], keep="first")[["barcode_key", "MRP", "SKU", "SIZE"]]
    )


def mirror_warehouse_csv_to_xlsx() -> Path | None:
    """Keep WAREHOUSE_SOH.xlsx in sync with synced CSV (compat for legacy readers)."""
    if not WAREHOUSE_FILE_CSV.exists():
        return None
    df = pd.read_csv(WAREHOUSE_FILE_CSV, low_memory=False)
    try:
        df.to_excel(WAREHOUSE_FILE, index=False, engine="xlsxwriter")
    except Exception:
        df.to_excel(WAREHOUSE_FILE, index=False)
    return WAREHOUSE_FILE


def load_warehouse_register() -> pd.DataFrame:
    """Load WH closing stock — CSV preferred (blob sync), else xlsx.

    Blob exports are often .csv with XLSX bytes and a 2-row title block before
    STOCKPOINTNAME/BARCODE/CLOSINGSTOCKQTY — detect the real header row.
    """
    path = warehouse_register_path()

    with path.open("rb") as fh:
        magic = fh.read(4)

    if magic[:2] == b"PK":
        raw = pd.read_excel(path, engine="openpyxl", header=None)
    else:
        raw = pd.read_csv(path, header=None, low_memory=False)

    header_idx: int | None = None
    for idx in range(min(15, len(raw))):
        row_vals = [str(v).strip() for v in raw.iloc[idx].tolist()]
        keys = {norm_header_key(v) for v in row_vals if v and v.lower() not in {"nan", "none"}}
        if "barcode" in keys and (
            "closingstockqty" in keys
            or "stockpointname" in keys
            or "department" in keys
        ):
            header_idx = idx
            break

    if header_idx is None:
        df = pd.read_excel(path, engine="openpyxl") if magic[:2] == b"PK" else pd.read_csv(path, low_memory=False)
        df.columns = df.columns.astype(str).str.strip()
        return df

    df = raw.iloc[header_idx + 1 :].copy()
    df.columns = [str(v).strip() for v in raw.iloc[header_idx].tolist()]
    df = df.dropna(how="all")
    return df


def build_warehouse_soh_map(warehouse_df: pd.DataFrame | None = None) -> pd.DataFrame:
    if warehouse_df is None:
        if not warehouse_register_exists():
            return pd.DataFrame(columns=["barcode_key", "wh_soh"])
        warehouse_df = load_warehouse_register()

    bc = find_column(warehouse_df, "BARCODE", "Barcode")
    qty = find_column(
        warehouse_df,
        "CLOSINGSTOCKQTY",
        "CLOSING STOCK QTY",
        "CLOSING_STOCK_QTY",
        "QTY",
        "STOCK QTY",
    )
    w = warehouse_df.copy()
    w["barcode_key"] = normalize_barcode(w[bc])
    w["wh_soh"] = pd.to_numeric(w[qty], errors="coerce").fillna(0)
    w = w[w["barcode_key"].astype(str).str.len() > 0]
    return w.groupby("barcode_key", as_index=False)["wh_soh"].sum()


def site_key_for_customer_merge(series: pd.Series) -> pd.Series:
    s = series.astype(str).str.strip().str.upper()
    s = s.replace({"NAN": "", "NONE": "", "<NA>": ""})
    n = pd.to_numeric(s, errors="coerce")
    out = pd.Series("", index=series.index, dtype="object")
    m_num = n.notna() & (s != "")
    out.loc[m_num] = n.loc[m_num].astype("Int64").astype(str)
    tail = s.str.replace(r"^P+", "", regex=True)
    n2 = pd.to_numeric(tail, errors="coerce")
    m_p = (~m_num) & n2.notna() & (tail != "")
    out.loc[m_p] = n2.loc[m_p].astype("Int64").astype(str)
    rest = out.eq("") & (s != "")
    out.loc[rest] = s.loc[rest]
    return out


def enrich_with_customer_master(result_df: pd.DataFrame, site_id_col: str = "Site_ID") -> pd.DataFrame:
    """Enrich refill/sales frames with Customer Master dims (city/state/zone/…)."""
    try:
        from customer_master import enrich_sales_with_customer_master
    except ImportError:
        from pipeline.customer_master import enrich_sales_with_customer_master  # type: ignore

    # Map Site_ID → site_id for helper, then map back if needed
    out = result_df.copy()
    col = site_id_col if site_id_col in out.columns else ("site_id" if "site_id" in out.columns else None)
    if col is None:
        return out
    enriched = enrich_sales_with_customer_master(out, site_col=col)
    # Keep legacy store_type derive when blank
    if "store_type" not in enriched.columns and "Site_Name" in enriched.columns:
        enriched["store_type"] = enriched.apply(
            lambda r: derive_store_type(r.get("Site_Name", ""), r.get(col, "")),
            axis=1,
        )
    elif "store_type" in enriched.columns and "channel" in enriched.columns:
        blank = enriched["store_type"].astype(str).str.strip().eq("")
        enriched.loc[blank, "store_type"] = enriched.loc[blank, "channel"].fillna("")
    return enriched


def build_grade_map(store_list_path: Path | None = None) -> pd.DataFrame:
    path = store_list_path or STORE_LIST
    if not path.exists():
        return pd.DataFrame(columns=["_grade_site_key", "store_grade"])

    sl = pd.read_excel(path)
    sl.columns = sl.columns.astype(str).str.strip()
    site_col = find_column(sl, "Site_Code", "site_code", "SITE CODE")
    grade_col = find_column(sl, "Grade", "grade", "GRADE")
    gm = sl[[site_col, grade_col]].copy()
    gm = gm.rename(columns={site_col: "_grade_site_key", grade_col: "store_grade"})
    gm["_grade_site_key"] = normalize_site(gm["_grade_site_key"])
    gm["store_grade"] = gm["store_grade"].astype(str).str.strip().replace({"nan": "", "None": "", "<na>": ""})
    gm = gm[(gm["_grade_site_key"].astype(str).str.len() > 0)]
    return gm.drop_duplicates(subset=["_grade_site_key"], keep="first")


def require_combined_inputs() -> None:
    missing = []
    if not COMBINED_SOH.exists():
        missing.append(COMBINED_SOH)
    try:
        resolve_pipeline_sales_path()
    except FileNotFoundError:
        missing.append(PIPELINE_SALES_CANDIDATES[0])
    try:
        resolve_pipeline_transfer_path()
    except FileNotFoundError:
        missing.append(PIPELINE_TRANSFER_CANDIDATES[0])
    if missing:
        raise FileNotFoundError(
            "Missing pipeline inputs. Need COMBINED_SOH, Sales (PT/SSL/…), "
            "Transfer - (PT, SSL, Centro, Iconic)/SALES_INVOICE_TRACKING.\n"
            + "\n".join(f"  - {p}" for p in missing)
        )

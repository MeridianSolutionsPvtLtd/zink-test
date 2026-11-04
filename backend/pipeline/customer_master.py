"""
Customer Master dim for dashboards (PT / SSL / CENTRO / ICONIC / EBO).

Loads CUSTOMER_MASTER.csv or .xlsx from pipeline/input_files and returns a
site-keyed frame with normalized dashboard columns.
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any, Optional

import pandas as pd

PIPELINE_DIR = Path(__file__).resolve().parent
INPUT_DIR = PIPELINE_DIR / "input_files"

CANDIDATES = (
    INPUT_DIR / "CUSTOMER_MASTER.csv",
    INPUT_DIR / "CUSTOMER_MASTER.xlsx",
    INPUT_DIR / "@CUSTOMER MASTER REGISTER_NEW (1).xlsx",
)

BLOB_CUSTOMER_MASTER_CANDIDATES = (
    "Customer Master/current/CUSTOMER_MASTER.csv",
    "Customer Master/current/CUSTOMER_MASTER.xlsx",
    "Customer Master/current/CUSTOMER_MASTER.xls",
)

_FILTER_CACHE_KEY: str | None = None
_FILTER_REFRESHING = False
_FILTER_REFRESH_LOCK = threading.Lock()
_FILTER_CACHE_VAL: dict | None = None


def _find_customer_master_from_blob() -> Optional[Path]:
    """Pull Customer Master from Azure blob current/ when local file is missing."""
    try:
        # Reuse Sales Dashboard singleton — new BlobJsonStore() pings Azure every call (~3s+)
        from api.routes.sales_dashboard import _blob_store

        store = _blob_store()
        if store is None:
            return None
        prefix = store.input_prefix  # e.g. input/
        for rel in BLOB_CUSTOMER_MASTER_CANDIDATES:
            blob_name = f"{prefix}{rel}"
            if not store._blob_exists(blob_name):
                continue
            path = store._ensure_local_file(blob_name)
            if path is not None and path.exists() and path.stat().st_size > 0:
                return path
    except Exception:
        return None
    return None


def _find_customer_master_path() -> Optional[Path]:
    # Local-first (same as Item Master) — hosted pages must not wait on Azure when
    # pipeline/input_files already has CUSTOMER_MASTER from deploy / previous pull.
    for p in CANDIDATES:
        if p.exists() and p.stat().st_size > 0:
            return p
    blob_path = _find_customer_master_from_blob()
    if blob_path is not None:
        return blob_path
    return None

# User column → API / dashboard field
COL_MAP = {
    "sitecode": "site_code",
    "site code": "site_code",
    "site type": "site_type_code",
    "customer name": "customer_name",
    "site name": "site_name_master",
    "site short code": "site_short_code",
    "site short name": "site_short_code",
    "address": "address",
    "gst_identification_no": "gstin",
    "gst identification no": "gstin",
    "site manual code": "site_manual_code",
    "city": "city",
    "state name": "state",
    "state": "state",
    "state code": "state_code",
    "country": "country",
    "pincode": "pincode",
    "pin code": "pincode",
    "store size": "store_size",
    "store size(sqr ft)": "store_sqft",
    "store size (sqr ft)": "store_sqft",
    "store grade": "store_grade",
    "mobile no": "mobile_no",
    "store season": "store_season",
    "store type": "store_type_master",
    "ltl/new": "store_category",
    "ltl / new": "store_category",
    "cluster": "cluster",
    "zone": "zone",
    "slcode": "slcode",
    "tax region": "tax_region",
    "mdq": "mdq",
}

RETAIL_CHANNELS = frozenset({"PT", "SSL", "CENTRO", "ICONIC", "EBO"})
SALES_CHANNEL_OPTIONS = ["PT", "SSL", "CENTRO", "ICONIC", "EBO"]
STORE_CATEGORY_OPTIONS = ["LTL", "EXIT", "NEW"]


def _norm_store_category(val: object) -> str:
    s = str(val or "").strip().upper()
    if s in ("LTL", "EXIT", "NEW"):
        return s
    return ""


_JUNK_VALS = frozenset({"", "0", "0.0", "nan", "none", "<na>", "null", "unassigned", "-", "--"})

# Real store grades look like A / A+ / 1A+ / 7D / D-W2 — never person names.
_STORE_GRADE_RE = re.compile(r"^(?:\d{0,2})?[A-D]\+?(?:-W\d+)?$", re.IGNORECASE)

_CANONICAL_CUSTOMER_BY_CHANNEL = {
    "PT": "M/S Aditya Birla Fashion and Retail Ltd(Frmly Knwn As PRFL)",
    "SSL": "Shoppers Stop Ltd",
    "CENTRO": "RELIANCE RETAIL LIMITED",
    "ICONIC": "ICONIC FASHION RETAILING PRIVATE LIMITED",
    "EBO": "Fresco Global Pvt Ltd",
}


def _distinct_vals(series: pd.Series) -> list[str]:
    vals = series.astype(str).str.strip()
    vals = vals[~vals.str.lower().isin(_JUNK_VALS)]
    return sorted(vals.unique().tolist(), key=str.casefold)


def _is_junk_master_name(name: str) -> bool:
    """Drop placeholder / closed-site markers from Customer Master."""
    s = (name or "").strip()
    if not s or s.lower() in _JUNK_VALS:
        return True
    u = s.upper()
    if u.startswith("XXX") or u.startswith("XX_"):
        return True
    if u.startswith("XX ") or u.startswith("XX-"):
        return True
    return False


def _is_valid_store_grade(val: object) -> bool:
    s = str(val or "").strip()
    if not s or s.lower() in _JUNK_VALS:
        return False
    return bool(_STORE_GRADE_RE.match(s))


def normalize_customer_name(val: object, channel: object = "") -> str:
    """Collapse SSL/PT spelling variants and map EBO person-names to company."""
    raw = str(val or "").strip()
    ch = str(channel or "").strip().upper()
    if raw.lower() in _JUNK_VALS:
        raw = ""
    u = raw.upper()

    if "SHOPPER" in u or ch == "SSL":
        return _CANONICAL_CUSTOMER_BY_CHANNEL["SSL"]
    if "ICONIC" in u or ch == "ICONIC":
        return _CANONICAL_CUSTOMER_BY_CHANNEL["ICONIC"]
    if "RELIANCE" in u or "AJIO" in u or ch == "CENTRO":
        return _CANONICAL_CUSTOMER_BY_CHANNEL["CENTRO"]
    if "ADITYA BIRLA" in u or "PANTALOON" in u or ch == "PT":
        # Keep rare non-ABFRL PT accounts only when clearly different company.
        if raw and "PAHARIA" in u:
            return raw
        return _CANONICAL_CUSTOMER_BY_CHANNEL["PT"]
    if "FRESCO" in u or ch == "EBO":
        return _CANONICAL_CUSTOMER_BY_CHANNEL["EBO"]
    if raw and not _is_junk_master_name(raw):
        return raw
    return _CANONICAL_CUSTOMER_BY_CHANNEL.get(ch, raw)


def _distinct_site_names(series: pd.Series) -> list[str]:
    vals = _distinct_vals(series)
    return [v for v in vals if not _is_junk_master_name(v)]


def _customer_filter_disk_paths() -> tuple[Path, Path]:
    cache_json = Path(__file__).resolve().parents[1] / "data" / "api_cache" / "customer_master_filters.json"
    return cache_json, cache_json.with_suffix(".key")


def _read_customer_filter_disk() -> dict | None:
    cache_json, _key_path = _customer_filter_disk_paths()
    if not cache_json.exists() or cache_json.stat().st_size <= 0:
        return None
    try:
        with open(cache_json, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    if isinstance(data, dict) and data.get("options"):
        return data
    return None


def _persist_customer_filters(result: dict, cache_key: str) -> None:
    cache_json, key_path = _customer_filter_disk_paths()
    try:
        cache_json.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_json, "w", encoding="utf-8") as f:
            json.dump(result, f)
        key_path.write_text(cache_key, encoding="utf-8")
    except Exception:
        pass


def _schedule_customer_filter_rebuild(cache_key: str, retail_only: bool) -> None:
    global _FILTER_REFRESHING
    with _FILTER_REFRESH_LOCK:
        if _FILTER_REFRESHING:
            return
        _FILTER_REFRESHING = True

    def run() -> None:
        global _FILTER_CACHE_KEY, _FILTER_CACHE_VAL, _FILTER_REFRESHING
        try:
            result = _build_customer_master_filters_uncached(retail_only=retail_only)
            _FILTER_CACHE_KEY = cache_key
            _FILTER_CACHE_VAL = result
            _persist_customer_filters(result, cache_key)
        except Exception:
            pass
        finally:
            with _FILTER_REFRESH_LOCK:
                _FILTER_REFRESHING = False

    threading.Thread(target=run, daemon=True).start()


def build_customer_master_filters(*, retail_only: bool = True, force_refresh: bool = False) -> dict:
    """
    Distinct filter dropdown values + per-site rows for master-driven filtering.
    Cluster / RM filter uses Cluster column only (distinct values from master).
    Store Grade comes from STORE_GRADE file (input folder / blob), not Customer Master.

    A saved list is returned immediately. If the source file changed, a fresh
    list is built in the background and used on the next request.
    """
    global _FILTER_CACHE_KEY, _FILTER_CACHE_VAL

    try:
        from pipeline.pipeline_inputs import INPUT_DIR
    except ImportError:
        from pipeline_inputs import INPUT_DIR  # type: ignore

    cache_parts: list[str] = []
    for name in ("CUSTOMER_MASTER.csv", "CUSTOMER_MASTER.xlsx", "STORE_GRADE.csv", "STORE_GRADE.xlsx"):
        p = INPUT_DIR / name
        if p.exists():
            st = p.stat()
            cache_parts.append(f"{p.resolve()}|{st.st_size}|{st.st_mtime_ns}")
    # v2: canonical customer names + valid store-grade filter values
    cache_key = f"v2|{retail_only}|{'||'.join(sorted(cache_parts))}"
    if not force_refresh and _FILTER_CACHE_VAL is not None and _FILTER_CACHE_KEY == cache_key:
        return _FILTER_CACHE_VAL

    _cache_json, key_path = _customer_filter_disk_paths()
    saved_key = ""
    if key_path.exists():
        try:
            saved_key = key_path.read_text(encoding="utf-8").strip()
        except Exception:
            saved_key = ""
    disk = None if force_refresh else _read_customer_filter_disk()
    if disk is not None and saved_key == cache_key:
        _FILTER_CACHE_VAL = disk
        _FILTER_CACHE_KEY = cache_key
        return disk
    if disk is not None:
        # Serve the saved list now. A file change rebuilds in the background.
        if _FILTER_CACHE_VAL is None:
            _FILTER_CACHE_VAL = disk
        _schedule_customer_filter_rebuild(cache_key, retail_only)
        return _FILTER_CACHE_VAL

    result = _build_customer_master_filters_uncached(retail_only=retail_only)
    _FILTER_CACHE_KEY = cache_key
    _FILTER_CACHE_VAL = result
    _persist_customer_filters(result, cache_key)
    return result


def _build_customer_master_filters_uncached(*, retail_only: bool = True) -> dict:
    try:
        from store_grade import load_store_grade_map, store_grade_filter_options
    except ImportError:
        from pipeline.store_grade import load_store_grade_map, store_grade_filter_options  # type: ignore

    df = load_customer_master(retail_only=retail_only)
    grade_map = load_store_grade_map()
    grade_opts = store_grade_filter_options()
    grade_lookup: dict[str, str] = {}
    if not grade_map.empty:
        grade_lookup = {
            str(r["site_code"]).strip().upper(): str(r["store_grade"]).strip()
            for _, r in grade_map.iterrows()
            if str(r["site_code"]).strip()
        }

    empty_opts = {
        "zones": [],
        "siteCodes": [],
        "siteNames": [],
        "storeCategories": STORE_CATEGORY_OPTIONS.copy(),
        "salesChannels": SALES_CHANNEL_OPTIONS.copy(),
        "customerFormats": [],
        "customerNames": [],
        "cities": [],
        "states": [],
        "clusterRm": [],
        "storeGrades": grade_opts,
    }
    if df.empty:
        return {"ok": False, "detail": "Customer Master not found", "sites": [], "options": empty_opts}

    # Site Code filter = SITE MANUAL CODE only (never SITECODE / short-code fallback)
    master_sites = df[df["site_manual_code"].astype(str).str.strip().ne("")].copy()

    sites: list[dict] = []
    for _, r in master_sites.iterrows():
        manual = str(r["site_manual_code"] or "").strip()
        if not manual:
            continue
        # Grade from STORE_GRADE file (Site_Code = SITE MANUAL CODE) or Customer Master
        sg = grade_lookup.get(manual.upper()) or str(r.get("store_grade") or "").strip()
        if not _is_valid_store_grade(sg):
            sg = ""
        cl_val = str(r["cluster"] or "").strip()
        if cl_val.lower() in _JUNK_VALS:
            cl_val = ""
        ch = str(r["channel"] or "").strip().upper()
        sites.append(
            {
                "site_key": manual,
                "site_code": manual,
                "site_name": str(r["site_name_master"]),
                "zone": str(r["zone"]),
                "city": str(r["city"]),
                "state": str(r["state"]),
                "cluster": cl_val,
                "store_category": _norm_store_category(r["store_category"]),
                "store_grade": sg,
                "customer_name": normalize_customer_name(r.get("customer_name"), ch),
                "channel": ch,
                "store_type_master": str(r["store_type_master"]),
            }
        )

    site_name_opts = _distinct_site_names(master_sites["site_name_master"])
    # One clean company name per retail channel (no SSL spelling splits / EBO person names).
    customer_name_opts = sorted(
        {normalize_customer_name(s["customer_name"], s["channel"]) for s in sites if s.get("channel")},
        key=str.casefold,
    )
    customer_name_opts = [c for c in customer_name_opts if c and not _is_junk_master_name(c)]
    site_code_opts = _distinct_vals(master_sites["site_manual_code"])
    store_grade_opts = grade_opts or sorted(
        {s["store_grade"] for s in sites if _is_valid_store_grade(s["store_grade"])},
        key=str.casefold,
    )
    options = {
        "zones": _distinct_vals(master_sites["zone"]),
        "siteCodes": site_code_opts,
        "siteNames": site_name_opts,
        "storeCategories": STORE_CATEGORY_OPTIONS.copy(),
        "salesChannels": SALES_CHANNEL_OPTIONS.copy(),
        "customerFormats": SALES_CHANNEL_OPTIONS.copy(),
        "customerNames": customer_name_opts,
        "cities": _distinct_vals(master_sites["city"]),
        "states": _distinct_vals(master_sites["state"]),
        "clusterRm": _distinct_vals(master_sites["cluster"]),
        "storeGrades": store_grade_opts,
    }
    return {
        "ok": True,
        "sites": sites,
        "options": options,
        "row_count": len(master_sites),
        "source": str(_find_customer_master_path() or ""),
        "store_grade_source": str(_find_store_grade_path() or ""),
    }


def _find_store_grade_path() -> Optional[Path]:
    try:
        from store_grade import _find_store_grade_path as _sg_path
    except ImportError:
        from pipeline.store_grade import _find_store_grade_path as _sg_path  # type: ignore
    return _sg_path()


def _norm_header(c: object) -> str:
    return " ".join(str(c).replace("\n", " ").replace("\r", " ").split()).strip().lower()


def _norm_site(s: pd.Series) -> pd.Series:
    out = s.astype(str).str.strip()
    out = out.str.replace(r"\.0$", "", regex=True)
    return out.replace({"nan": "", "None": "", "NaT": "", "<NA>": "", "NONE": ""})


def _read_raw(path: Path) -> pd.DataFrame:
    data = path.read_bytes()[:4]
    # Misnamed xlsx saved as .csv (PK zip)
    is_xlsx = path.suffix.lower() in (".xlsx", ".xls") or data[:2] == b"PK"
    if is_xlsx:
        last_err: Exception | None = None
        for header in (3, 0, 1, 2, 4):
            try:
                df = pd.read_excel(path, header=header)
                cols = [_norm_header(c) for c in df.columns]
                if any(c in cols for c in ("sitecode", "site manual code", "site code", "site name")):
                    return df
            except Exception as e:
                last_err = e
        if last_err:
            raise last_err
        return pd.read_excel(path, header=3)
    for enc in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
        try:
            return pd.read_csv(path, encoding=enc, low_memory=False)
        except UnicodeDecodeError:
            continue
    return pd.read_csv(path, encoding="latin-1", low_memory=False, on_bad_lines="skip")


def load_customer_master(*, retail_only: bool = True) -> pd.DataFrame:
    """
    Return one row per site with dashboard fields.
    Join keys: site_manual_code, site_code, site_short_code (all normalized).
    """
    path = _find_customer_master_path()
    if path is None:
        return pd.DataFrame()

    raw = _read_raw(path)
    raw.columns = [_norm_header(c) for c in raw.columns]

    rename = {}
    for src, dst in COL_MAP.items():
        if src in raw.columns and dst not in rename.values():
            rename[src] = dst
    df = raw.rename(columns=rename)

    # Prefer STORE SIZE(Sqr Ft) when both size cols exist
    if "store_sqft" not in df.columns and "store_size" in df.columns:
        df["store_sqft"] = df["store_size"]

    for c in (
        "site_code",
        "site_manual_code",
        "site_short_code",
        "customer_name",
        "site_name_master",
        "city",
        "state",
        "state_code",
        "country",
        "pincode",
        "store_grade",
        "store_season",
        "store_type_master",
        "store_category",
        "cluster",
        "zone",
        "slcode",
        "tax_region",
        "address",
        "gstin",
        "site_type_code",
        "mobile_no",
    ):
        if c not in df.columns:
            df[c] = ""
        df[c] = df[c].astype(str).str.strip().replace({"nan": "", "None": "", "<NA>": ""})

    for c in ("store_sqft", "store_size", "mdq"):
        if c not in df.columns:
            df[c] = pd.NA
        df[c] = pd.to_numeric(df[c], errors="coerce")

    for c in ("site_code", "site_manual_code", "site_short_code"):
        df[c] = _norm_site(df[c])

    # Primary join key: SITE MANUAL CODE, else SITECODE, else SHORT CODE
    df["_site_key"] = df["site_manual_code"]
    blank = df["_site_key"].eq("")
    df.loc[blank, "_site_key"] = df.loc[blank, "site_code"]
    blank = df["_site_key"].eq("")
    df.loc[blank, "_site_key"] = df.loc[blank, "site_short_code"]
    df = df[df["_site_key"].astype(str).str.len() > 0].copy()

    # Channel from STORE TYPE / name (EBO-F, PT, SSL, …)
    st = df["store_type_master"].str.upper()
    name = df["site_name_master"].str.upper()
    # Channel mapping for all retail formats
    channel = pd.Series([""] * len(df), index=df.index, dtype=object)
    sh_code = df["site_short_code"].astype(str).str.upper()
    cust_name = df["customer_name"].astype(str).str.upper()
    is_ebo = (
        df["_site_key"].isin({"528", "964"})
        | st.str.contains("EBO", na=False)
        | st.str.contains("MS-OO-CM", na=False)
        | name.str.contains("FRESCO", na=False)
        | name.str.contains("EBO", na=False)
        | sh_code.str.contains("FGPL", na=False)
        | cust_name.str.contains("FRESCO", na=False)
    )
    channel = channel.mask(is_ebo, "EBO")
    is_ssl = (
        st.str.contains(r"SHOPPER|HOPPER|\bSS[- ]|\bSSL\b", na=False, regex=True)
        | name.str.contains(r"SHOPPER|HOPPER|\bSS[- ]|\bSSL\b", na=False, regex=True)
        | sh_code.str.contains(r"SHOPPER|HOPPER|\bSS[- ]|\bSSL\b", na=False, regex=True)
        | cust_name.str.contains(r"SHOPPER|HOPPER|\bSS[- ]|\bSSL\b", na=False, regex=True)
    )
    channel = channel.mask(channel.eq("") & is_ssl, "SSL")
    is_pt = (
        st.str.contains(r"PANTALOON|\bPT[- ]|\bPW[- ]|\bPT\b|^P\d{2,5}\b", na=False, regex=True)
        | name.str.contains(r"PANTALOON|\bPT[- ]|\bPW[- ]|\bPT\b|^P\d{2,5}\b", na=False, regex=True)
        | sh_code.str.contains(r"PANTALOON|\bPT[- ]|\bPW[- ]|\bPT\b|^P\d{2,5}\b", na=False, regex=True)
        | cust_name.str.contains(r"ADITYA BIRLA|PANTALOON", na=False, regex=True)
    ) & ~is_ssl
    channel = channel.mask(channel.eq("") & is_pt, "PT")
    is_centro = (st.str.contains(r"CENTRO|\bRRL[- ]|^TC3|^T8|RELIANCE", na=False, regex=True) | name.str.contains(r"CENTRO|\bRRL[- ]|^TC3|^T8|RELIANCE", na=False, regex=True)) & ~is_ssl & ~is_pt
    channel = channel.mask(channel.eq("") & is_centro, "CENTRO")
    is_iconic = (st.str.contains("ICONIC", na=False) | name.str.contains("ICONIC", na=False)) & ~is_ssl & ~is_pt
    channel = channel.mask(channel.eq("") & is_iconic, "ICONIC")
    all_retail = frozenset({"PT", "SSL", "CENTRO", "ICONIC", "EBO"})
    channel = channel.mask(channel.eq("") & st.isin(all_retail), st)
    df["channel"] = channel

    # Clean display customer name and site name (remove XXX, XX_ wrappers)
    df["customer_name"] = df["customer_name"].astype(str).str.replace(r"^XXX[_\s-]*", "", regex=True).str.replace(r"[_\s-]*XXX$", "", regex=True).str.strip()
    df["site_name_master"] = df["site_name_master"].astype(str).str.replace(r"^XXX[_\s-]*", "", regex=True).str.replace(r"[_\s-]*XXX$", "", regex=True).str.strip()
    df["customer_name"] = [
        normalize_customer_name(n, c) for n, c in zip(df["customer_name"], df["channel"])
    ]
    # Drop person-names / junk wrongly parked in STORE GRADE column
    sg = df["store_grade"].astype(str).str.strip()
    df["store_grade"] = sg.where(sg.map(_is_valid_store_grade), "")

    if retail_only:
        df = df[df["channel"].isin(RETAIL_CHANNELS)].copy()

    # Keep first row per site key
    df = df.drop_duplicates(subset=["_site_key"], keep="first")
    return df.reset_index(drop=True)


_BLANK_PLACE = frozenset({"", "nan", "none", "<na>", "null"})


def _place_text(val: object) -> str:
    s = str(val or "").strip()
    return "" if s.lower() in _BLANK_PLACE else s


def fill_city_state(df: pd.DataFrame, site_col: str = "site_id") -> pd.DataFrame:
    """Copy Customer Master city/state onto rows.

    Pipeline site ids are SITECODE (P001 / 294), not SITE MANUAL CODE.
    SITECODE wins when the same token also appears as a manual code.
    site_id itself is left unchanged.
    """
    out = df.copy()
    if site_col not in out.columns:
        return out
    cm = load_customer_master(retail_only=False)
    if cm.empty:
        print("Customer master not found (skip city/state)", flush=True)
        return out

    lookup: dict[str, tuple[str, str]] = {}

    def _put(key: object, city: object, state: object) -> None:
        k = _place_text(key).upper().replace(".0", "")
        if not k:
            return
        lookup[k] = (_place_text(city), _place_text(state))

    # Low priority first so SITECODE overwrites a colliding manual/short code.
    for col in ("site_short_code", "site_manual_code", "site_code"):
        if col not in cm.columns:
            continue
        for rec in cm[[col, "city", "state"]].itertuples(index=False):
            _put(rec[0], rec[1], rec[2])

    keys = (
        out[site_col]
        .astype(str)
        .str.strip()
        .str.upper()
        .str.replace(r"\.0$", "", regex=True)
    )
    mapped = keys.map(lookup)
    city_s = mapped.map(lambda v: v[0] if isinstance(v, tuple) else "")
    state_s = mapped.map(lambda v: v[1] if isinstance(v, tuple) else "")
    for col, mapped_s in (("city", city_s), ("state", state_s)):
        if col not in out.columns:
            out[col] = mapped_s
            continue
        cur = out[col].map(_place_text)
        blank = cur.eq("")
        out[col] = cur
        out.loc[blank, col] = mapped_s.loc[blank]
    have_city = int(out["city"].map(_place_text).ne("").sum())
    print(
        f"City/state from Customer Master ({site_col}): {have_city:,} / {len(out):,} rows have city",
        flush=True,
    )
    return out


def enrich_sales_with_customer_master(sales: pd.DataFrame, *, site_col: str = "site_id") -> pd.DataFrame:
    """Left-join customer master dims onto sales rows."""
    cm = load_customer_master(retail_only=False)
    if cm.empty or site_col not in sales.columns:
        out = sales.copy()
        for c in (
            "zone",
            "city",
            "state",
            "cluster",
            "store_grade",
            "store_category",
            "customer_name",
            "store_sqft",
            "mdq",
            "store_season",
            "tax_region",
            "slcode",
            "address",
            "gstin",
            "state_code",
            "pincode",
            "country",
            "site_short_code",
            "store_type_master",
        ):
            if c not in out.columns:
                out[c] = "" if c not in ("store_sqft", "mdq") else pd.NA
        return out

    # Build channel-partitioned and global lookup maps for accurate multi-key resolution
    lookup_by_channel: dict[str, dict[str, str]] = {
        "SSL": {},
        "PT": {},
        "CENTRO": {},
        "ICONIC": {},
        "EBO": {},
    }
    lookup_manual: dict[str, str] = {}
    lookup_all: dict[str, str] = {}

    for _, r in cm.iterrows():
        target = str(r.get("_site_key", "")).strip()
        if not target:
            continue
        ch = str(r.get("channel", "")).strip().upper()
        m = str(r.get("site_manual_code", "")).strip().upper()
        sc = str(r.get("site_code", "")).strip().upper()
        sh = str(r.get("site_short_code", "")).strip().upper()
        nm = str(r.get("site_name_master", "")).strip().upper()

        if ch in lookup_by_channel:
            if m: lookup_by_channel[ch][m] = target
            if sc: lookup_by_channel[ch][sc] = target
            if sh: lookup_by_channel[ch][sh] = target
            if nm: lookup_by_channel[ch][nm] = target

        if m: lookup_manual[m] = target
        if sh: lookup_manual[sh] = target
        if nm: lookup_manual[nm] = target

    # Sitecode map (SITECODE wins bare-numeric collisions with SSL manuals — same as fill_city_state).
    lookup_sitecode: dict[str, str] = {}
    for _, r in cm.iterrows():
        target = str(r.get("_site_key", "")).strip()
        sc = str(r.get("site_code", "")).strip().upper()
        if sc and target:
            lookup_sitecode[sc] = target

    lookup_all.update(lookup_manual)
    for sc, target in lookup_sitecode.items():
        if sc not in lookup_all:
            lookup_all[sc] = target

    import re
    def _extract_master_key(val: Any) -> str:
        s = str(val or "").strip()
        u = s.upper()
        if not u or u in ("NAN", "NONE", "0", "0.0", "<NA>"):
            return ""

        ch_hint = None
        if re.search(r"SHOPPER|HOPPER|\bSS[- ]|\bSSL\b", u):
            ch_hint = "SSL"
        elif re.search(r"PANTALOON|\bPT[- ]|\bPW[- ]|\bPT\b|^P\d{2,5}\b|ADITYA BIRLA", u):
            ch_hint = "PT"
        elif re.search(r"CENTRO|\bRRL[- ]|^TC3|^T8|RELIANCE", u):
            ch_hint = "CENTRO"
        elif "ICONIC" in u:
            ch_hint = "ICONIC"
        elif re.search(r"EBO|FRESCO|^FGPL|\b(?:528|964|972)\b", u):
            ch_hint = "EBO"

        # 1. Try channel-specific map first
        if ch_hint and ch_hint in lookup_by_channel:
            c_map = lookup_by_channel[ch_hint]
            if u in c_map:
                return c_map[u]
            m1 = re.search(r"\(\s*([A-Za-z0-9]+)\s*\)", s)
            if m1:
                code = m1.group(1).upper()
                if code in c_map: return c_map[code]
                num_code = re.sub(r"^[A-Z]+", "", code)
                if num_code in c_map: return c_map[num_code]
            m2 = re.search(r"[-_\s]([A-Za-z0-9]+)$", s)
            if m2:
                code = m2.group(1).upper()
                if code in c_map: return c_map[code]
                num_code = re.sub(r"^[A-Z]+", "", code)
                if num_code in c_map: return c_map[num_code]

        # 2. Bare tokens with no channel hint: prefer SITECODE over colliding SSL manuals.
        if not ch_hint and re.fullmatch(r"[A-Z0-9]+", u) and u in lookup_sitecode:
            return lookup_sitecode[u]

        # 3. Global lookup (manuals + remaining sitecodes)
        if u in lookup_all:
            return lookup_all[u]
        m1 = re.search(r"\(\s*([A-Za-z0-9]+)\s*\)", s)
        if m1:
            code = m1.group(1).upper()
            if not ch_hint and code in lookup_sitecode:
                return lookup_sitecode[code]
            if code in lookup_all: return lookup_all[code]
            num_code = re.sub(r"^[A-Z]+", "", code)
            if not ch_hint and num_code in lookup_sitecode:
                return lookup_sitecode[num_code]
            if num_code in lookup_all: return lookup_all[num_code]
        m2 = re.search(r"[-_\s]([A-Za-z0-9]+)$", s)
        if m2:
            code = m2.group(1).upper()
            if not ch_hint and code in lookup_sitecode:
                return lookup_sitecode[code]
            if code in lookup_all: return lookup_all[code]
            num_code = re.sub(r"^[A-Z]+", "", code)
            if not ch_hint and num_code in lookup_sitecode:
                return lookup_sitecode[num_code]
            if num_code in lookup_all: return lookup_all[num_code]

        return _norm_site(pd.Series([s])).iloc[0]

    out = sales.copy()
    unique_sites = out[site_col].dropna().unique()
    site_map = {s: _extract_master_key(s) for s in unique_sites}
    out["_site_key"] = out[site_col].map(site_map).fillna("")
    right = cm.drop_duplicates("_site_key")
    drop_cols = [c for c in right.columns if c != "_site_key" and c in out.columns]
    if drop_cols:
        out = out.drop(columns=drop_cols)
    out = out.merge(right, on="_site_key", how="left")
    out = out.drop(columns=["_site_key"])

    # Fill blanks
    for c in (
        "zone",
        "city",
        "state",
        "cluster",
        "store_grade",
        "store_category",
        "customer_name",
        "store_season",
        "tax_region",
        "slcode",
        "address",
        "gstin",
        "state_code",
        "pincode",
        "country",
        "site_short_code",
        "store_type_master",
    ):
        out[c] = out[c].fillna("").astype(str).replace({"nan": ""}).str.strip()

    out["store_category"] = out["store_category"].map(_norm_store_category)

    # Store Grade from STORE_GRADE file (Site_Code), not Customer Master STORE GRADE col
    try:
        from store_grade import load_store_grade_map
    except ImportError:
        from pipeline.store_grade import load_store_grade_map  # type: ignore

    gm = load_store_grade_map()
    if not gm.empty and site_col in sales.columns:
        lookup = {
            str(r["site_code"]).strip().upper(): str(r["store_grade"]).strip()
            for _, r in gm.iterrows()
            if str(r["site_code"]).strip()
        }
        keys = _norm_site(sales[site_col]).str.upper()
        mapped = keys.map(lookup)
        if "store_grade" in out.columns:
            out["store_grade"] = mapped.fillna(out["store_grade"]).fillna("")
        else:
            out["store_grade"] = mapped.fillna("")

    # Prefer master site name when sales name blank
    if "site_name" in out.columns:
        blank = out["site_name"].astype(str).str.strip().eq("")
        out.loc[blank, "site_name"] = out.loc[blank, "site_name_master"].fillna("")

    # Channel and customer_name consistency enforcement based on raw store tokens
    site_series = out[site_col].astype(str) if site_col in out.columns else pd.Series("", index=out.index)
    name_series = out["site_name"].astype(str) if "site_name" in out.columns else pd.Series("", index=out.index)
    combined_site = (site_series + " " + name_series).str.upper()

    is_ssl_raw = combined_site.str.contains(r"SHOPPER|HOPPER|^SS[- ]|\bSSL\b", regex=True, na=False)
    is_pt_raw = combined_site.str.contains(r"PANTALOON|^PT[- ]|^PW[- ]|\bPT\b", regex=True, na=False)
    is_centro_raw = combined_site.str.contains(r"CENTRO", regex=True, na=False)
    is_iconic_raw = combined_site.str.contains(r"ICONIC", regex=True, na=False)
    is_ebo_raw = combined_site.str.contains(r"EBO|FRESCO", regex=True, na=False)

    if "channel" in out.columns:
        out.loc[is_ssl_raw, "channel"] = "SSL"
        out.loc[is_pt_raw & ~is_ssl_raw, "channel"] = "PT"
        out.loc[is_centro_raw & ~is_ssl_raw & ~is_pt_raw, "channel"] = "CENTRO"
        out.loc[is_iconic_raw & ~is_ssl_raw & ~is_pt_raw, "channel"] = "ICONIC"
        out.loc[is_ebo_raw & ~is_ssl_raw & ~is_pt_raw, "channel"] = "EBO"

    if "customer_name" in out.columns:
        ssl_rows = is_ssl_raw | (out.get("channel", pd.Series("", index=out.index)) == "SSL")
        bad_ssl_cust = (
            out["customer_name"].isna()
            | out["customer_name"].str.strip().isin({"", "nan", "None", "Jmdee", "WIP Warehouse", "IBT Warehouse"})
            | out["customer_name"].str.contains("Aditya Birla|PANTALOON", case=False, na=False)
        )
        out.loc[ssl_rows & bad_ssl_cust, "customer_name"] = "Shoppers Stop Ltd"

        pt_rows = (out.get("channel", pd.Series("", index=out.index)) == "PT")
        bad_pt_cust = out["customer_name"].isna() | out["customer_name"].str.strip().isin({"", "nan", "None"})
        out.loc[pt_rows & bad_pt_cust, "customer_name"] = "M/S Aditya Birla Fashion and Retail Ltd"

    return out

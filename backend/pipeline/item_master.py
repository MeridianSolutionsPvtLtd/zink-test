"""
Item Master dims for dashboards — Department / Section by barcode.

Loads ITEM_MASTER from blob current/ or local pipeline/input_files.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Optional

import pandas as pd

PIPELINE_DIR = Path(__file__).resolve().parent
INPUT_DIR = PIPELINE_DIR / "input_files"

BLOB_ITEM_MASTER_CANDIDATES = (
    "Item Master/current/ITEM_MASTER.xlsx",
    "Item Master/current/ITEM_MASTER.xls",
    "Item Master/current/ITEM_MASTER.csv",
)

CANDIDATES = (
    INPUT_DIR / "Item Master" / "current" / "ITEM_MASTER.xlsx",
    INPUT_DIR / "ITEM_MASTER.xlsx",
    INPUT_DIR / "ITEM_MASTER.csv",
    INPUT_DIR / "@ITEM MASTER DETAILS REGISTER_SP@PACKING.xlsx",
)

_FILTERS_CACHE: dict | None = None
_FILTERS_CACHE_KEY: str | None = None
_FILTERS_REFRESHING = False
_FILTERS_REFRESH_LOCK = threading.Lock()
_BARCODE_MAP_CACHE: pd.DataFrame | None = None
_BARCODE_MAP_CACHE_KEY: str | None = None
_SKU_IMAGE_MAP_CACHE: dict[str, str] | None = None
_SKU_IMAGE_MAP_CACHE_KEY: str | None = None


def _path_cache_key(path: Path | None) -> str | None:
    if path is None or not path.exists():
        return None
    stat = path.stat()
    return f"{path.resolve()}:{stat.st_mtime_ns}:{stat.st_size}"


def _find_item_master_from_blob() -> Optional[Path]:
    if not os.environ.get("AZURE_STORAGE_CONNECTION_STRING"):
        return None
    try:
        from api.routes.sales_dashboard import _blob_store

        store = _blob_store()
        if store is None:
            return None
        prefix = store.input_prefix
        for rel in BLOB_ITEM_MASTER_CANDIDATES:
            blob_name = f"{prefix}{rel}"
            if not store._blob_exists(blob_name):
                continue
            path = store._ensure_local_file(blob_name)
            if path is not None and path.exists() and path.stat().st_size > 0:
                return path
    except Exception:
        return None
    return None


def _find_item_master_path() -> Optional[Path]:
    for p in CANDIDATES:
        if p.exists() and p.stat().st_size > 0:
            return p
    blob_path = _find_item_master_from_blob()
    if blob_path is not None:
        return blob_path
    return None


def _distinct_vals(series: pd.Series) -> list[str]:
    vals = series.astype(str).str.strip()
    vals = vals[vals.ne("") & ~vals.str.lower().isin({"nan", "none", "<na>"})]
    return sorted(vals.unique().tolist(), key=str.casefold)


def _read_item_master(path: Path) -> pd.DataFrame:
    try:
        from pipeline.pipeline_inputs import (
            ITEM_MASTER_HEADER,
            ITEM_MASTER_HINTS,
            ITEM_MASTER_SHEET,
            read_excel_fast,
        )
    except ImportError:
        from pipeline_inputs import (  # type: ignore
            ITEM_MASTER_HEADER,
            ITEM_MASTER_HINTS,
            ITEM_MASTER_SHEET,
            read_excel_fast,
        )

    suffix = path.suffix.lower()
    head = b""
    try:
        with path.open("rb") as fh:
            head = fh.read(8)
    except OSError:
        head = b""
    is_excel = head[:2] == b"PK" or head[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

    if suffix == ".csv" and not is_excel:
        try:
            from pipeline.date_merge import read_csv_bytes
        except ImportError:
            from date_merge import read_csv_bytes  # type: ignore
        try:
            return read_csv_bytes(path.read_bytes(), low_memory=False)
        except Exception:
            return pd.read_csv(
                path,
                encoding="latin-1",
                engine="python",
                on_bad_lines="skip",
            )

    sheet = ITEM_MASTER_SHEET
    try:
        names = pd.ExcelFile(path).sheet_names
        if ITEM_MASTER_SHEET in names:
            sheet = ITEM_MASTER_SHEET
        elif names:
            sheet = names[0]
    except Exception:
        pass

    df = read_excel_fast(
        path,
        sheet_name=sheet,
        column_hints=list(ITEM_MASTER_HINTS),
        preferred_header=ITEM_MASTER_HEADER,
        preferred_sheet=sheet,
    )
    if any("barcode" in str(c).strip().lower() for c in df.columns):
        return df

    raw = pd.read_excel(path, sheet_name=sheet, header=None)
    for i in range(min(len(raw), 15)):
        labels = [str(c).strip() for c in raw.iloc[i].tolist()]
        if any(x.lower() == "barcode" for x in labels):
            out = raw.iloc[i + 1 :].reset_index(drop=True)
            out.columns = labels
            return out
    return df


def load_item_master_df() -> pd.DataFrame:
    path = _find_item_master_path()
    if path is None:
        return pd.DataFrame()
    try:
        return _read_item_master(path)
    except Exception as exc:
        import logging

        logging.getLogger(__name__).warning("Item Master read failed (%s): %s", path, exc)
        return pd.DataFrame()


def build_barcode_dept_section_map(*, force_refresh: bool = False) -> pd.DataFrame:
    """One row per barcode: department, section (from Item Master)."""
    global _BARCODE_MAP_CACHE, _BARCODE_MAP_CACHE_KEY

    path = _find_item_master_path()
    cache_key = _path_cache_key(path)
    if (
        not force_refresh
        and _BARCODE_MAP_CACHE is not None
        and cache_key is not None
        and _BARCODE_MAP_CACHE_KEY == cache_key
    ):
        return _BARCODE_MAP_CACHE

    try:
        from pipeline.pipeline_inputs import find_column, normalize_barcode
    except ImportError:
        from pipeline_inputs import find_column, normalize_barcode  # type: ignore

    item = load_item_master_df()
    if item.empty:
        empty = pd.DataFrame(columns=["barcode_key", "department", "section"])
        _BARCODE_MAP_CACHE = empty
        _BARCODE_MAP_CACHE_KEY = cache_key
        return empty

    bc = find_column(item, "BARCODE", "Barcode")
    dept = find_column(item, "DEPARTMENT", "Department", "DIVISION")
    sec = find_column(item, "SECTION", "Section")

    x = item.copy()
    x["barcode_key"] = normalize_barcode(x[bc])
    x["department"] = (
        x[dept].astype(str).str.strip().replace({"nan": "", "None": "", "<NA>": ""})
        if dept
        else ""
    )
    x["section"] = (
        x[sec].astype(str).str.strip().replace({"nan": "", "None": "", "<NA>": ""})
        if sec
        else ""
    )
    x = x[x["barcode_key"].astype(str).str.len() > 0]
    x["_rank"] = (
        x["department"].astype(str).str.len().clip(upper=1)
        + x["section"].astype(str).str.len().clip(upper=1)
    )
    x = x.sort_values(["barcode_key", "_rank"], ascending=[True, False])
    result = (
        x.drop_duplicates(subset=["barcode_key"], keep="first")[["barcode_key", "department", "section"]]
        .reset_index(drop=True)
    )
    _BARCODE_MAP_CACHE = result
    _BARCODE_MAP_CACHE_KEY = cache_key
    return result


_ITEM_DETAILS_CACHE: pd.DataFrame | None = None
_ITEM_DETAILS_CACHE_KEY: str | None = None


def build_barcode_item_details_map(*, force_refresh: bool = False) -> pd.DataFrame:
    """One row per barcode: sku, size, department, section, color, mrp, style_name (from Item Master)."""
    global _ITEM_DETAILS_CACHE, _ITEM_DETAILS_CACHE_KEY

    path = _find_item_master_path()
    cache_key = _path_cache_key(path)
    if (
        not force_refresh
        and _ITEM_DETAILS_CACHE is not None
        and cache_key is not None
        and _ITEM_DETAILS_CACHE_KEY == cache_key
    ):
        return _ITEM_DETAILS_CACHE

    cache_dir = Path(__file__).resolve().parents[1] / "data" / "api_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    parquet_path = cache_dir / "barcode_item_details.parquet"
    key_path = cache_dir / "barcode_item_details.key"

    if not force_refresh and parquet_path.exists() and key_path.exists() and cache_key:
        try:
            if key_path.read_text(encoding="utf-8").strip() == cache_key:
                df_cached = pd.read_parquet(parquet_path)
                _ITEM_DETAILS_CACHE = df_cached
                _ITEM_DETAILS_CACHE_KEY = cache_key
                return df_cached
        except Exception:
            pass

    try:
        from pipeline.pipeline_inputs import find_column, normalize_barcode
    except ImportError:
        from pipeline_inputs import find_column, normalize_barcode  # type: ignore

    item = load_item_master_df()
    if item.empty:
        empty = pd.DataFrame(columns=["barcode_key", "sku", "size", "department", "section", "color", "mrp", "style_name"])
        _ITEM_DETAILS_CACHE = empty
        _ITEM_DETAILS_CACHE_KEY = cache_key
        return empty

    bc = find_column(item, "BARCODE", "Barcode")
    size_col = None
    try:
        size_col = find_column(item, "SIZE", "Size", "SIZE IN CM")
    except ValueError:
        pass

    sku_col = None
    try:
        sku_col = find_column(item, "SKU", "SKU CODE", "Sku Code", "ICODE")
    except ValueError:
        pass

    dept_col = None
    try:
        dept_col = find_column(item, "DEPARTMENT", "Department", "DIVISION")
    except ValueError:
        pass

    sec_col = None
    try:
        sec_col = find_column(item, "SECTION", "Section", "PRODUCT TYPE")
    except ValueError:
        pass

    color_col = None
    try:
        color_col = find_column(item, "COLOUR FAMILY", "COLOR", "COLOUR", "Color")
    except ValueError:
        pass

    mrp_col = None
    try:
        mrp_col = find_column(item, "MRP", "RSP", "STD RATE")
    except ValueError:
        pass

    style_col = None
    try:
        style_col = find_column(item, "STYLE NAME", "ARTICLE CODE", "Style")
    except ValueError:
        pass

    x = item.copy()
    x["barcode_key"] = normalize_barcode(x[bc])
    x["size"] = x[size_col].astype(str).str.strip().replace({"nan": "", "None": "", "<NA>": ""}) if size_col else ""
    x["sku"] = x[sku_col].astype(str).str.strip().replace({"nan": "", "None": "", "<NA>": ""}) if sku_col else ""
    x["department"] = x[dept_col].astype(str).str.strip().replace({"nan": "", "None": "", "<NA>": ""}) if dept_col else ""
    x["section"] = x[sec_col].astype(str).str.strip().replace({"nan": "", "None": "", "<NA>": ""}) if sec_col else ""
    x["color"] = x[color_col].astype(str).str.strip().replace({"nan": "", "None": "", "<NA>": ""}) if color_col else ""
    x["style_name"] = x[style_col].astype(str).str.strip().replace({"nan": "", "None": "", "<NA>": ""}) if style_col else ""
    x["mrp"] = pd.to_numeric(x[mrp_col], errors="coerce").fillna(0.0) if mrp_col else 0.0

    x = x[x["barcode_key"].astype(str).str.len() > 0]
    result = x.drop_duplicates(subset=["barcode_key"], keep="first")[
        ["barcode_key", "sku", "size", "department", "section", "color", "mrp", "style_name"]
    ].reset_index(drop=True)

    if cache_key:
        try:
            result.to_parquet(parquet_path, index=False)
            key_path.write_text(cache_key, encoding="utf-8")
        except Exception:
            pass

    _ITEM_DETAILS_CACHE = result
    _ITEM_DETAILS_CACHE_KEY = cache_key
    return result


def build_sku_image_map(*, force_refresh: bool = False) -> dict[str, str]:
    """Map SKU CODE -> Image URL from Item Master."""
    global _SKU_IMAGE_MAP_CACHE, _SKU_IMAGE_MAP_CACHE_KEY
    path = _find_item_master_path()
    cache_key = _path_cache_key(path)
    if (
        not force_refresh
        and _SKU_IMAGE_MAP_CACHE is not None
        and cache_key is not None
        and _SKU_IMAGE_MAP_CACHE_KEY == cache_key
    ):
        return _SKU_IMAGE_MAP_CACHE

    m = {}
    # 1. Include Azure Blob Storage Raw_Files/ItemImage/ product image URLs
    try:
        from api.routes.product_images import _get_blob_image_map

        blob_skus = _get_blob_image_map()
        for sku_code in blob_skus:
            m[sku_code] = f"/api/product-images/{sku_code}"
    except Exception:
        pass

    # 2. Include Item Master ImageKit URLs for remaining SKUs
    m.update(_item_master_image_urls(path, cache_key))

    _SKU_IMAGE_MAP_CACHE = m
    _SKU_IMAGE_MAP_CACHE_KEY = cache_key
    return m


def _item_master_image_urls(path: Path | None, cache_key: str | None) -> dict[str, str]:
    """SKU → http(s) image URL from Item Master. Disk-cached per file version: the Excel
    read takes minutes and every worker / restart would otherwise repeat it."""
    import json

    cache_json = Path(__file__).resolve().parents[1] / "data" / "api_cache" / "sku_image_urls.json"
    key_path = cache_json.with_suffix(".key")
    if cache_key:
        try:
            if key_path.read_text(encoding="utf-8") == cache_key:
                return json.loads(cache_json.read_text(encoding="utf-8"))
        except Exception:
            pass

    m: dict[str, str] = {}
    item = _read_item_master(path) if path else pd.DataFrame()
    if not item.empty:
        try:
            from pipeline.pipeline_inputs import find_column
        except ImportError:
            from pipeline_inputs import find_column  # type: ignore

        try:
            sku_col = find_column(item, "SKU CODE", "SKU", "Sku Code", "sku_code", "sku")
        except Exception:
            sku_col = None
        try:
            img_col = find_column(item, "Image URL", "image_url", "IMAGE_URL", "Image")
        except Exception:
            img_col = None

        if sku_col and img_col:
            df_valid = item[[sku_col, img_col]].dropna()
            df_valid[sku_col] = df_valid[sku_col].astype(str).str.strip().str.upper()
            df_valid[img_col] = df_valid[img_col].astype(str).str.strip()
            df_valid = df_valid[df_valid[sku_col].ne("") & df_valid[img_col].ne("")]

            for _, r in df_valid.drop_duplicates(subset=[sku_col]).iterrows():
                k = str(r[sku_col])
                if str(r[img_col]).lower().startswith(('https://', 'http://')):
                    m[k] = str(r[img_col])

    if cache_key:
        try:
            cache_json.parent.mkdir(parents=True, exist_ok=True)
            cache_json.write_text(json.dumps(m), encoding="utf-8")
            key_path.write_text(cache_key, encoding="utf-8")
        except Exception:
            pass
    return m


def _filters_disk_cache_paths() -> tuple[Path, Path]:
    cache_json = Path(__file__).resolve().parents[1] / "data" / "api_cache" / "item_master_filters.json"
    return cache_json, cache_json.with_suffix(".key")


def _memory_filters_still_match_file() -> bool:
    """True when the in-memory list was built from the file that is on disk now."""
    key = _FILTERS_CACHE_KEY
    if not key or _FILTERS_CACHE is None or key == "cached_json":
        return False
    try:
        path_s, mtime_s, size_s = key.rsplit(":", 2)
    except ValueError:
        return False
    path = Path(path_s)
    if not path.exists():
        return False
    stat = path.stat()
    return str(stat.st_mtime_ns) == mtime_s and str(stat.st_size) == size_s


def _schedule_item_filter_rebuild() -> None:
    global _FILTERS_REFRESHING
    with _FILTERS_REFRESH_LOCK:
        if _FILTERS_REFRESHING:
            return
        _FILTERS_REFRESHING = True

    def run() -> None:
        global _FILTERS_REFRESHING
        try:
            build_item_master_filters(force_refresh=True)
        except Exception:
            pass
        finally:
            with _FILTERS_REFRESH_LOCK:
                _FILTERS_REFRESHING = False

    threading.Thread(target=run, daemon=True).start()


def build_item_master_filters(*, force_refresh: bool = False) -> dict:
    """Distinct Department / Section dropdown values from Item Master."""
    global _FILTERS_CACHE, _FILTERS_CACHE_KEY

    if not force_refresh and _memory_filters_still_match_file():
        return _FILTERS_CACHE  # type: ignore[return-value]

    path = _find_item_master_path()
    cache_key = _path_cache_key(path)
    cache_json, cache_key_path = _filters_disk_cache_paths()
    if (
        not force_refresh
        and cache_key
        and cache_json.exists()
        and cache_json.stat().st_size > 0
    ):
        try:
            import json

            saved_key = cache_key_path.read_text(encoding="utf-8").strip() if cache_key_path.exists() else ""
            with open(cache_json, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data and data.get("ok") and data.get("options"):
                if saved_key == cache_key:
                    _FILTERS_CACHE = data
                    _FILTERS_CACHE_KEY = cache_key
                    return data
                if _FILTERS_CACHE is None:
                    _FILTERS_CACHE = data
                _schedule_item_filter_rebuild()
                return _FILTERS_CACHE
        except Exception:
            pass

    empty = {
        "ok": False,
        "detail": "Item Master not found",
        "options": {"departments": [], "sections": []},
        "sections_by_department": {},
        "row_count": 0,
        "source": "",
    }
    if path is None:
        return empty

    try:
        from pipeline.pipeline_inputs import find_column
    except ImportError:
        from pipeline_inputs import find_column  # type: ignore

    item = _read_item_master(path)
    if item.empty:
        return {**empty, "detail": "Item Master empty"}

    dept_col = find_column(item, "DEPARTMENT", "Department", "DIVISION")
    sec_col = find_column(item, "SECTION", "Section")
    if not dept_col and not sec_col:
        return {**empty, "detail": "Item Master missing DEPARTMENT / SECTION columns"}

    work = item.copy()
    if dept_col:
        work["_dept"] = work[dept_col].astype(str).str.strip().replace({"nan": "", "None": "", "<NA>": ""})
    else:
        work["_dept"] = ""
    if sec_col:
        work["_sec"] = work[sec_col].astype(str).str.strip().replace({"nan": "", "None": "", "<NA>": ""})
    else:
        work["_sec"] = ""

    departments = _distinct_vals(work.loc[work["_dept"].ne(""), "_dept"])
    sections = _distinct_vals(work.loc[work["_sec"].ne(""), "_sec"])

    sections_by_department: dict[str, list[str]] = {}
    paired = work[(work["_dept"].ne("")) & (work["_sec"].ne(""))][["_dept", "_sec"]].drop_duplicates()
    for dept, grp in paired.groupby("_dept"):
        sections_by_department[str(dept)] = _distinct_vals(grp["_sec"])

    payload = {
        "ok": True,
        "options": {
            "departments": departments,
            "sections": sections,
        },
        "sections_by_department": sections_by_department,
        "row_count": len(item),
        "source": str(path),
    }
    _FILTERS_CACHE = payload
    _FILTERS_CACHE_KEY = cache_key
    if cache_key:
        try:
            import json

            cache_json.parent.mkdir(parents=True, exist_ok=True)
            with open(cache_json, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            cache_key_path.write_text(cache_key, encoding="utf-8")
        except Exception:
            pass
    return payload


def enrich_sales_dept_section_from_item_master(
    sales: pd.DataFrame,
    *,
    barcode_col: str = "barcode",
    prefer_item_master: bool = True,
) -> pd.DataFrame:
    """Join Item Master Department / Section onto sales by barcode."""
    if sales.empty or barcode_col not in sales.columns:
        return sales

    if "department" not in sales.columns:
        sales = sales.copy()
        sales["department"] = ""
    if "section" not in sales.columns:
        sales = sales.copy()
        sales["section"] = ""

    try:
        from pipeline.pipeline_inputs import normalize_barcode
    except ImportError:
        from pipeline_inputs import normalize_barcode  # type: ignore

    amap = build_barcode_dept_section_map()
    if amap.empty:
        return sales

    out = sales.copy()
    out["_barcode_key"] = normalize_barcode(out[barcode_col])
    right = amap.rename(columns={"department": "department_im", "section": "section_im"})
    merged = out.merge(
        right,
        left_on="_barcode_key",
        right_on="barcode_key",
        how="left",
    )

    im_dept = merged["department_im"].fillna("").astype(str).str.strip()
    im_sec = merged["section_im"].fillna("").astype(str).str.strip()
    cur_dept = merged["department"].fillna("").astype(str).str.strip()
    cur_sec = merged["section"].fillna("").astype(str).str.strip()

    if prefer_item_master:
        merged["department"] = im_dept.where(im_dept.ne(""), cur_dept)
        merged["section"] = im_sec.where(im_sec.ne(""), cur_sec)
    else:
        merged["department"] = cur_dept.where(cur_dept.ne(""), im_dept)
        merged["section"] = cur_sec.where(cur_sec.ne(""), im_sec)

    return merged.drop(
        columns=["_barcode_key", "barcode_key", "department_im", "section_im"],
        errors="ignore",
    )

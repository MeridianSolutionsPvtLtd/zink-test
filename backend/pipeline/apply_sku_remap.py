"""
Apply OLD→NEW SKU remap on sales masters before combine/predict.

- Mapping file: input_files/SKU_REMAP.xlsx (from Blob current/ via sync)
- Columns (flexible): OLD SKU / NEW SKU
- Remaps SKU text only (barcode unchanged)
- Rewrites local sales masters + overwrites Blob input/.../current/
- No-op when map missing or empty
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
INPUT_DIR = BASE_DIR / "input_files"
OUTPUT_DIR = BASE_DIR / "output_files"

SKU_REMAP_FILE = INPUT_DIR / "SKU_REMAP.xlsx"
SKU_REMAP_CSV = INPUT_DIR / "SKU_REMAP.csv"

# Local sales masters that feed combine_sales / history continuity
SALES_TARGETS: List[Dict[str, Any]] = [
    {
        "local": INPUT_DIR / "SSI_PT_CENTRO_ICONIC_SALES.xlsx",
        "category_id": "sales-pt-ssl-centro-iconic",
        "canonical": "SSI_PT_CENTRO_ICONIC_SALES.xlsx",
        "preferred_header": 0,
        "preferred_sheet": "UNMANAGED SITE SALE REGISTER_SP",
        "column_hints": ["ENTRY_DATE", "BARCODE", "CATEGORY3", "SKU"],
    },
    {
        "local": INPUT_DIR / "EBO_SALES.xlsx",
        "category_id": "ebo-sales",
        "canonical": "EBO_SALES.xlsx",
        "preferred_header": 3,
        "preferred_sheet": "EBO SALE REGISTER WITH MOP",
        "column_hints": ["BILL DATE", "BARCODE", "SKU"],
    },
]

_SKU_COL_CANDIDATES = (
    "SKU",
    "CATEGORY3",
    "CATEGORY 3",
    "ITEM CODE",
    "ITEM_CODE",
    "ARTICLE",
    "STYLE",
    "ICODE",
)


def _log(msg: str) -> None:
    print(msg, flush=True)


def _norm_sku(val: object) -> str:
    s = str(val).strip().upper().replace(".0", "")
    if s.lower() in ("", "nan", "none", "<na>"):
        return ""
    return s


def load_sku_remap(path: Optional[Path] = None) -> Dict[str, str]:
    """Return {OLD_SKU: NEW_SKU} (uppercase). Empty dict if no file."""
    candidates = []
    if path is not None:
        candidates.append(Path(path))
    candidates.extend([SKU_REMAP_FILE, SKU_REMAP_CSV])
    # Also allow nested current sync path if flat copy missing
    nested = INPUT_DIR / "SKU Remap" / "current" / "SKU_REMAP.xlsx"
    candidates.append(nested)

    src = next((p for p in candidates if p.exists() and p.stat().st_size > 0), None)
    if src is None:
        return {}

    if src.suffix.lower() == ".csv":
        df = pd.read_csv(src, low_memory=False)
    else:
        try:
            from pipeline_inputs import read_excel_fast

            df = read_excel_fast(
                src,
                column_hints=["OLD SKU", "NEW SKU", "OLD_SKU", "NEW_SKU"],
                preferred_header=0,
            )
        except Exception:
            df = pd.read_excel(src)

    df.columns = [str(c).strip() for c in df.columns]
    col_l = {c.lower().replace("_", " "): c for c in df.columns}
    old_col = (
        col_l.get("old sku")
        or col_l.get("oldsku")
        or col_l.get("from sku")
        or col_l.get("previous sku")
        or col_l.get("sku old")
    )
    new_col = (
        col_l.get("new sku")
        or col_l.get("newsku")
        or col_l.get("to sku")
        or col_l.get("sku new")
        or col_l.get("current sku")
    )
    if not old_col or not new_col:
        # fallback: first two columns
        if len(df.columns) < 2:
            raise ValueError(
                f"SKU remap needs OLD SKU / NEW SKU columns. Found: {list(df.columns)}"
            )
        old_col, new_col = df.columns[0], df.columns[1]
        _log(f"[sku_remap] using first two columns as old/new: {old_col!r}, {new_col!r}")

    mapping: Dict[str, str] = {}
    for _, row in df.iterrows():
        old = _norm_sku(row[old_col])
        new = _norm_sku(row[new_col])
        if not old or not new or old == new:
            continue
        mapping[old] = new
    return mapping


def _sku_columns_present(df: pd.DataFrame) -> List[str]:
    lookup = {str(c).strip().upper(): c for c in df.columns}
    out: List[str] = []
    for cand in _SKU_COL_CANDIDATES:
        key = cand.upper()
        if key in lookup and lookup[key] not in out:
            out.append(lookup[key])
    return out


def apply_sku_remap_df(df: pd.DataFrame, mapping: Dict[str, str]) -> Tuple[pd.DataFrame, int]:
    if not mapping:
        return df, 0
    out = df.copy()
    cols = _sku_columns_present(out)
    if not cols:
        return out, 0
    changed = 0
    for col in cols:
        series = out[col].map(lambda v: _norm_sku(v) if pd.notna(v) else "")
        mask = series.isin(mapping.keys())
        n = int(mask.sum())
        if n:
            out.loc[mask, col] = series.loc[mask].map(mapping)
            changed += n
    return out, changed


def _write_sales_bytes(df: pd.DataFrame, filename: str) -> Tuple[bytes, str]:
    from date_merge import write_tabular_bytes

    return write_tabular_bytes(df, filename)


def _read_sales_file(path: Path, cfg: Dict[str, Any]) -> pd.DataFrame:
    from pipeline_inputs import read_excel_fast

    return read_excel_fast(
        path,
        sheet_name=cfg.get("preferred_sheet") or 0,
        column_hints=list(cfg.get("column_hints") or []),
        preferred_header=int(cfg.get("preferred_header") or 0),
        preferred_sheet=cfg.get("preferred_sheet"),
    )


def _upload_to_blob_current(category_id: str, canonical: str, data: bytes) -> str:
    from merge_category_inputs import (
        CATEGORY_MERGE_CONFIG,
        _get_container,
        _guess_content_type,
        _upload,
        _category_prefixes,
    )

    cfg = CATEGORY_MERGE_CONFIG.get(category_id) or {}
    folder = cfg.get("folder")
    if not folder:
        raise RuntimeError(f"Unknown sales category for blob upload: {category_id}")
    cc, _container, input_prefix = _get_container()
    paths = _category_prefixes(input_prefix, folder)
    blob = f"{paths['current']}{canonical}"
    _upload(cc, blob, data, _guess_content_type(canonical))
    return blob


def remap_sales_masters(
    mapping: Dict[str, str],
    *,
    write_blob: bool = True,
) -> Dict[str, Any]:
    results: List[Dict[str, Any]] = []
    for cfg in SALES_TARGETS:
        path: Path = cfg["local"]
        if not path.exists():
            results.append(
                {
                    "file": path.name,
                    "skipped": True,
                    "reason": "local_file_missing",
                }
            )
            continue
        _log(f"[sku_remap] reading {path.name} ...")
        df = _read_sales_file(path, cfg)
        remapped, n_changed = apply_sku_remap_df(df, mapping)
        info: Dict[str, Any] = {
            "file": path.name,
            "rows": int(len(remapped)),
            "sku_cells_remapped": int(n_changed),
            "skipped": n_changed == 0,
        }
        if n_changed == 0:
            _log(f"[sku_remap] {path.name}: no matching OLD SKUs — left unchanged")
            results.append(info)
            continue

        out_bytes, _ctype = _write_sales_bytes(remapped, cfg["canonical"])
        path.write_bytes(out_bytes)
        info["local_written"] = str(path)
        _log(f"[sku_remap] {path.name}: remapped {n_changed:,} SKU cell(s) → local saved")

        if write_blob:
            try:
                blob = _upload_to_blob_current(
                    cfg["category_id"], cfg["canonical"], out_bytes
                )
                info["blob_written"] = blob
                _log(f"[sku_remap] uploaded → blob://{blob}")
            except Exception as e:
                info["blob_error"] = str(e)
                _log(f"[sku_remap] WARN blob upload failed for {path.name}: {e}")
        results.append(info)

    # Also remap COMBINED_SALES if already present (resume / re-run safety)
    for comb in (
        OUTPUT_DIR / "COMBINED_SALES.csv",
        OUTPUT_DIR / "COMBINED_SALES.xlsx",
    ):
        if not comb.exists():
            continue
        _log(f"[sku_remap] reading {comb.name} ...")
        if comb.suffix.lower() == ".csv":
            cdf = pd.read_csv(comb, low_memory=False)
        else:
            cdf = pd.read_excel(comb)
        cdf2, n2 = apply_sku_remap_df(cdf, mapping)
        if n2:
            if comb.suffix.lower() == ".csv":
                cdf2.to_csv(comb, index=False)
            else:
                b, _ = _write_sales_bytes(cdf2, comb.name)
                comb.write_bytes(b)
            _log(f"[sku_remap] {comb.name}: remapped {n2:,} SKU cell(s)")
        results.append(
            {
                "file": comb.name,
                "rows": int(len(cdf2)),
                "sku_cells_remapped": int(n2),
                "local_written": str(comb) if n2 else None,
            }
        )

    return {"ok": True, "mapping_size": len(mapping), "files": results}


def main() -> None:
    mapping = load_sku_remap()
    if not mapping:
        _log("[sku_remap] no SKU_REMAP file / empty map — skip (sales unchanged)")
        return

    _log(f"[sku_remap] loaded {len(mapping)} OLD→NEW mapping(s)")
    for i, (old, new) in enumerate(list(mapping.items())[:20]):
        _log(f"  {old} → {new}")
    if len(mapping) > 20:
        _log(f"  ... +{len(mapping) - 20} more")

    result = remap_sales_masters(mapping, write_blob=True)
    changed_files = [
        f for f in result["files"] if int(f.get("sku_cells_remapped") or 0) > 0
    ]
    _log(
        f"[sku_remap] done. files_touched={len(changed_files)} / {len(result['files'])}"
    )


if __name__ == "__main__":
    main()

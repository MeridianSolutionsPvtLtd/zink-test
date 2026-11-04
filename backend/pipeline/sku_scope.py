"""SKU remap replenishment scope (NEW names after OLD→NEW file)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Set

import pandas as pd


def is_remapped_scope() -> bool:
    return (os.environ.get("PIPELINE_SKU_SCOPE") or "").strip().lower() in {
        "remapped",
        "remap",
        "renamed",
    }


def remapped_sku_names() -> Set[str]:
    """OLD + NEW SKU names from the current SKU_REMAP file only (replaced on each upload)."""
    try:
        from apply_sku_remap import load_sku_remap, _norm_sku
    except ImportError:
        from pipeline.apply_sku_remap import load_sku_remap, _norm_sku  # type: ignore

    mapping = load_sku_remap()
    names: Set[str] = set()
    for old, new in mapping.items():
        o = _norm_sku(old)
        n = _norm_sku(new)
        if o:
            names.add(o)
        if n:
            names.add(n)
    return names


def sku_series(df: pd.DataFrame) -> pd.Series:
    for col in ("sku", "SKU", "CATEGORY3", "CATEGORY 3"):
        if col in df.columns:
            s = df[col].astype(str).str.strip().str.upper().str.replace(r"\.0$", "", regex=True)
            s = s.replace({"NAN": "", "NONE": "", "<NA>": ""})
            return s
    return pd.Series([""] * len(df), index=df.index)


def mask_remapped_skus(df: pd.DataFrame, names: Optional[Set[str]] = None) -> pd.Series:
    names = names if names is not None else remapped_sku_names()
    if not names:
        return pd.Series([False] * len(df), index=df.index)
    return sku_series(df).isin(names)


def merge_prior_other_skus(
    new_df: pd.DataFrame,
    prior_path: Path,
    *,
    names: Optional[Set[str]] = None,
) -> pd.DataFrame:
    """Keep previous rows for SKUs that were not remapped; use new_df for remapped SKUs."""
    names = names if names is not None else remapped_sku_names()
    if not names or not prior_path.exists() or prior_path.stat().st_size <= 0:
        return new_df
    prev = pd.read_csv(prior_path, low_memory=False)
    prev.columns = prev.columns.astype(str).str.strip()
    keep = prev.loc[~mask_remapped_skus(prev, names)].copy()
    fresh = new_df.loc[mask_remapped_skus(new_df, names)].copy() if len(new_df) else new_df
    if keep.empty:
        return new_df
    cols = list(new_df.columns)
    for c in cols:
        if c not in keep.columns:
            keep[c] = ""
    for c in keep.columns:
        if c not in fresh.columns:
            fresh = fresh.copy()
            fresh[c] = ""
            if c not in cols:
                cols.append(c)
    combined = pd.concat([keep[cols], fresh[cols]], ignore_index=True)
    print(
        f"[sku_scope] kept {len(keep):,} prior rows · {len(fresh):,} remapped rows → {len(combined):,}",
        flush=True,
    )
    return combined

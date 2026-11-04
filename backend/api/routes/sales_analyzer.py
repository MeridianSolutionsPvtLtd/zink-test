"""
Sales Analyzer API — Self-service dynamic pivot query engine.
Allows flexible ad-hoc multi-dimensional grouping, column splits, multi-measure aggregations,
and Current Year vs Last Year growth calculations.
Updated: 2026-09-20T17:43:20
"""
from __future__ import annotations

import hashlib
import logging
import math
import os
import sys
import threading
import time
from pathlib import Path
import re
from typing import Any, Dict, List, Literal, Optional, Sequence, Union
import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from api.historical_movement import (
    MOVEMENT_DIMS,
    frame_site_keys,
    group_opening_receipts,
    historical_str,
    movement_totals_for_frame,
)
from api.routes.sales_dashboard import _get_sales_df, _get_customer_master_sqft_map
from api.sales_year_loader import load_sales_for_year
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sales-analyzer", tags=["Sales Analyzer"])


def _on_azure() -> bool:
    """App Service workers OOM on cold Sales-Master / DM rebuilds — refuse those paths."""
    return bool(os.environ.get("WEBSITE_SITE_NAME") or os.environ.get("WEBSITE_INSTANCE_ID"))

# Whitelisted Dimension & Measure Metadata
DIMENSIONS_META = [
    {"field": "barcode", "label": "Barcode", "category": "Product", "dataType": "string"},
    {"field": "zone", "label": "Zone", "category": "Store", "dataType": "string"},
    {"field": "sales_channel", "label": "Channel / Format", "category": "Store", "dataType": "string"},
    {"field": "site_id_name", "label": "Store (Code - Name)", "category": "Store", "dataType": "string"},
    {"field": "site_id", "label": "Store Code", "category": "Store", "dataType": "string"},
    {"field": "site_name", "label": "Store Name", "category": "Store", "dataType": "string"},
    {"field": "city_name", "label": "City", "category": "Location", "dataType": "string"},
    {"field": "state_name", "label": "State", "category": "Location", "dataType": "string"},
    {"field": "store_grade", "label": "Store Grade", "category": "Store", "dataType": "string"},
    {"field": "cluster", "label": "Manager / Cluster", "category": "Store", "dataType": "string"},
    {"field": "manager", "label": "Manager (RM)", "category": "Store", "dataType": "string"},
    {"field": "brand", "label": "Brand", "category": "Product", "dataType": "string"},
    {"field": "department", "label": "Department", "category": "Product", "dataType": "string"},
    {"field": "section", "label": "Section", "category": "Product", "dataType": "string"},
    {"field": "sku", "label": "SKU / Style", "category": "Product", "dataType": "string"},
    {"field": "size", "label": "Size", "category": "Product", "dataType": "string"},
    {"field": "season", "label": "Season", "category": "Product", "dataType": "string"},
    {"field": "financial_year", "label": "Financial Year (FY)", "category": "Time", "dataType": "string"},
    {"field": "year", "label": "Year", "category": "Time", "dataType": "number"},
    {"field": "month", "label": "Month", "category": "Time", "dataType": "string"},
    {"field": "fy_week", "label": "FY Week", "category": "Time", "dataType": "string"},
    {"field": "date", "label": "Date", "category": "Time", "dataType": "date"},
    {"field": "customer_name", "label": "Customer Name", "category": "Store", "dataType": "string"},
]

MEASURES_META = [
    {
        "field": "sales",
        "label": "Sales (Net GSV)",
        "dataType": "currency",
        "allowedAggregations": ["sum", "avg", "count", "min", "max"],
        "defaultAggregation": "sum",
    },
    {
        "field": "sales_ly",
        "label": "Sales LY",
        "dataType": "currency",
        "allowedAggregations": ["sum"],
        "defaultAggregation": "sum",
        "isLy": True,
    },
    {
        "field": "sales_growth_pct",
        "label": "Sales Growth % (YoY)",
        "dataType": "percentage",
        "isCalculated": True,
    },
    {
        "field": "dispatch_qty",
        "label": "Dispatch Qty",
        "dataType": "number",
        "allowedAggregations": ["sum", "avg"],
        "defaultAggregation": "sum",
    },
    {
        "field": "quantity",
        "label": "Quantity Sold",
        "dataType": "number",
        "allowedAggregations": ["sum", "avg", "count", "min", "max"],
        "defaultAggregation": "sum",
    },
    {
        "field": "quantity_ly",
        "label": "Quantity LY",
        "dataType": "number",
        "allowedAggregations": ["sum"],
        "defaultAggregation": "sum",
        "isLy": True,
    },
    {
        "field": "qty_growth_pct",
        "label": "Qty Growth % (YoY)",
        "dataType": "percentage",
        "isCalculated": True,
    },
    {
        "field": "sell_through_pct",
        "label": "Sell-Through % (STR)",
        "dataType": "percentage",
        "allowedAggregations": ["avg", "calculated"],
        "defaultAggregation": "calculated",
        "isCalculated": True,
    },
    {
        "field": "sell_through_ly_pct",
        "label": "Sell-Through % LY (STR LY)",
        "dataType": "percentage",
        "allowedAggregations": ["avg", "calculated"],
        "defaultAggregation": "calculated",
        "isCalculated": True,
        "isLy": True,
    },
    {
        "field": "gross_sales",
        "label": "Gross Sales (MRP)",
        "dataType": "currency",
        "allowedAggregations": ["sum", "avg"],
        "defaultAggregation": "sum",
    },
    {
        "field": "discount_amount",
        "label": "Discount Amount",
        "dataType": "currency",
        "allowedAggregations": ["sum", "avg"],
        "defaultAggregation": "sum",
    },
    {
        "field": "discount_pct",
        "label": "Discount %",
        "dataType": "percentage",
        "allowedAggregations": ["avg", "min", "max"],
        "defaultAggregation": "avg",
    },
    {
        "field": "asp",
        "label": "ASP (Avg Selling Price)",
        "dataType": "currency",
        "allowedAggregations": ["avg"],
        "defaultAggregation": "avg",
        "isCalculated": True,
    },
    {
        "field": "asp_ly",
        "label": "ASP LY",
        "dataType": "currency",
        "isCalculated": True,
        "isLy": True,
    },
    {
        "field": "asp_growth_pct",
        "label": "ASP Growth % (YoY)",
        "dataType": "percentage",
        "isCalculated": True,
    },
    {
        "field": "sales_per_sqft",
        "label": "Sales / Sq Ft (SPSF)",
        "dataType": "currency",
        "allowedAggregations": ["avg", "calculated"],
        "defaultAggregation": "calculated",
        "isCalculated": True,
    },
    {
        "field": "store_sqft",
        "label": "Store Sqft Area",
        "dataType": "number",
        "allowedAggregations": ["sum", "avg"],
        "defaultAggregation": "sum",
    },
    {
        "field": "current_soh",
        "label": "Current SOH",
        "dataType": "number",
        "allowedAggregations": ["sum", "avg"],
        "defaultAggregation": "sum",
    },
    {
        "field": "mdq",
        "label": "Target MDQ",
        "dataType": "number",
        "allowedAggregations": ["sum", "avg"],
        "defaultAggregation": "sum",
    },
    {
        "field": "mdq_fill_pct",
        "label": "MDQ Fill %",
        "dataType": "percentage",
        "allowedAggregations": ["avg", "calculated"],
        "defaultAggregation": "calculated",
        "isCalculated": True,
    },
    {
        "field": "mdq_deficit",
        "label": "MDQ Deficit",
        "dataType": "number",
        "allowedAggregations": ["sum"],
        "defaultAggregation": "sum",
        "isCalculated": True,
    },
]

ALLOWED_DIMENSION_KEYS = frozenset(d["field"] for d in DIMENSIONS_META)
ALLOWED_MEASURE_KEYS = frozenset(m["field"] for m in MEASURES_META)
ALLOWED_AGGREGATIONS = frozenset({"sum", "avg", "count", "min", "max", "calculated"})

# Customer Master MDQ is one target per store. It can be summed on store cuts
# (zone, channel, site). It is not a department/SKU/month figure.
_STORE_LEVEL_DIMS = frozenset({
    "site_id",
    "site_name",
    "site_id_name",
    "zone",
    "sales_channel",
    "city_name",
    "state_name",
    "store_grade",
    "cluster",
    "manager",
    "customer_name",
})
_BLANK_DIM_TOKENS = frozenset({"", "nan", "none", "<na>", "nat", "null", "stockpulse"})
_JUNK_MANAGER_TOKENS = frozenset({
    "", "0", "0.0", "nan", "none", "<na>", "null", "unassigned", "all", "-", "--", "n/a",
})
_DIM_FILL_COLS = (
    "zone",
    "sales_channel",
    "site_id",
    "site_name",
    "site_id_name",
    "city_name",
    "state_name",
    "store_grade",
    "cluster",
    "manager",
    "customer_name",
    "brand",
    "department",
    "section",
    "sku",
    "size",
    "season",
    "month",
    "fy_week",
    "financial_year",
)


def _apply_manager_cluster_dims(df: pd.DataFrame) -> pd.DataFrame:
    """Fill manager/cluster from Customer Master Cluster (RM) so Pivot can cut by manager → stores.

    Sales / STR / SOH all join on store keys; manager is a store attribute, so manager-wise
    pivots roll up every store under that RM.
    """
    if df is None or getattr(df, "empty", True):
        return df
    out = df
    try:
        from pipeline.customer_master import enrich_sales_with_customer_master

        if "site_id" in out.columns:
            out = enrich_sales_with_customer_master(out, site_col="site_id")
    except Exception as enrich_exc:
        logger.warning("Manager/cluster CM enrich skipped: %s", enrich_exc)

    if "cluster" not in out.columns:
        out["cluster"] = ""
    cluster = out["cluster"].astype(str).str.strip()
    cluster = cluster.replace({"nan": "", "None": "", "<NA>": "", "<na>": ""})
    bad = cluster.str.lower().isin(_JUNK_MANAGER_TOKENS)
    cluster = cluster.mask(bad, "Unassigned")
    out["cluster"] = cluster
    # Explicit manager dim (same RM roster as Managers page)
    out["manager"] = cluster
    return out


def _fill_blank_dims(df: pd.DataFrame) -> pd.DataFrame:
    """Keep blank dimension values in the pivot so row totals match the grand total."""
    if df is None or df.empty:
        return df
    for col in _DIM_FILL_COLS:
        if col not in df.columns:
            continue
        s = df[col].fillna("").astype(str).str.strip()
        df[col] = s.mask(s.str.lower().isin(_BLANK_DIM_TOKENS), "Unassigned")
    return df


def _canonical_store_key(df: pd.DataFrame) -> pd.Series:
    """One key per store. SOH site_id is not stable, so prefer the customer-master code."""
    key = pd.Series("", index=df.index, dtype=object)
    bad = _BLANK_DIM_TOKENS | {"unassigned"}
    for col in ("site_manual_code", "site_code", "site_short_code", "site_name", "site_id_name", "site_id"):
        if col not in df.columns:
            continue
        s = df[col].fillna("").astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
        s = s.mask(s.str.lower().isin(bad), "")
        key = key.mask(key.eq("") & s.ne(""), s)
    return key


def _collapse_repeated_mdq(soh_df: pd.DataFrame) -> pd.DataFrame:
    """Customer-master MDQ is copied onto every barcode. Keep it once per store."""
    if soh_df is None or soh_df.empty or "mdq" not in soh_df.columns:
        return soh_df
    soh_df["mdq"] = pd.to_numeric(soh_df["mdq"], errors="coerce").fillna(0.0)
    key = _canonical_store_key(soh_df)
    keep = ~key.duplicated() & key.ne("")
    soh_df.loc[~keep, "mdq"] = 0.0
    return soh_df


def _site_mdq_frame(soh_df: pd.DataFrame) -> Optional[pd.DataFrame]:
    """One MDQ per store. The customer-master join copies that target onto every barcode."""
    if soh_df is None or soh_df.empty or "mdq" not in soh_df.columns:
        return None
    tmp = _collapse_repeated_mdq(soh_df.copy())
    tmp["_store_key"] = _canonical_store_key(tmp)
    tmp = tmp[tmp["_store_key"].ne("")]
    cols = ["_store_key", "mdq"] + [c for c in _STORE_LEVEL_DIMS if c in tmp.columns]
    return tmp.drop_duplicates(subset=["_store_key"])[cols]


_SITE_CODE_TOKEN = re.compile(
    r"\(([A-Z]{0,6}\s*[A-Z]?\d{1,6}|[A-Z]{2,6}\d{0,4}|ONLINE)\)\s*$",
    re.IGNORECASE,
)
_SITE_HAS_CODE = re.compile(r"\([^)]*\d[^)]*\)", re.IGNORECASE)
# Same 2–6 digit store code on both sides of " - ", or two parenthetical labels joined.
_DOUBLED_SITE_LABEL_RE = re.compile(r"(\d{2,6}).* - .*\1|\)\s*-\s*.+\(")


def _doubled_site_label_mask(cur: pd.Series) -> pd.Series:
    """Rows whose store label repeats the same code. Vectorized prefilter, then a small check."""
    rough = cur.str.contains(" - ", na=False) | cur.str.contains(r"\)\s*-\s*", regex=True, na=False)
    if not bool(rough.any()):
        return rough
    checked = cur.loc[rough].map(lambda text: bool(_DOUBLED_SITE_LABEL_RE.search(text)))
    out = pd.Series(False, index=cur.index)
    out.loc[rough] = checked.to_numpy()
    return out


def _norm_store_code(token: str) -> str:
    return re.sub(r"\s+", "", (token or "").upper())


def _prefer_store_display_label(site_id: Any, site_name: Any) -> str:
    """Build one clean store label; avoid 'PT-A (P033) - PT-B (P033)' duplication."""
    sid = "" if site_id is None else str(site_id).strip()
    sname = "" if site_name is None else str(site_name).strip()
    if sid.lower() in _BLANK_DIM_TOKENS:
        sid = ""
    if sname.lower() in _BLANK_DIM_TOKENS:
        sname = ""
    if not sid and not sname:
        return "Unassigned"
    if sid and not sname:
        return sid
    if sname and not sid:
        return sname
    su, nu = sid.upper(), sname.upper()
    if su in nu:
        return sname if len(sname) <= len(sid) * 1.5 else sid
    if nu in su:
        return sid
    c1 = _SITE_CODE_TOKEN.search(sid)
    c2 = _SITE_CODE_TOKEN.search(sname)
    if c1 and c2 and _norm_store_code(c1.group(1)) == _norm_store_code(c2.group(1)):
        return sid if len(sid) <= len(sname) else sname
    # site_id already carries a store code/name — don't append a second full label.
    if _SITE_HAS_CODE.search(sid) and _SITE_HAS_CODE.search(sname):
        return sid if len(sid) <= len(sname) else sname
    if _SITE_HAS_CODE.search(sid) and len(sid) >= 6:
        return sid
    shared_codes = set(re.findall(r"\d{2,6}", su)) & set(re.findall(r"\d{2,6}", nu))
    if shared_codes:
        # Same store written as code and as legal name. Keep one label.
        return sid if len(sid) <= len(sname) else sname
    return f"{sid} - {sname}".strip(" -")


def _dedupe_site_id_name_text(label: Any) -> str:
    """Collapse 'PT-A (P033) - PT-B (P033)' → shorter single store label."""
    s = "" if label is None else str(label).strip()
    if not s or s.lower() in _BLANK_DIM_TOKENS:
        return "Unassigned"
    if " - " not in s:
        return s
    left, right = s.split(" - ", 1)
    return _prefer_store_display_label(left.strip(), right.strip())


def _ensure_clean_site_id_name(df: pd.DataFrame) -> pd.DataFrame:
    """Read-side normalize site_id_name (no pipeline rewrite required)."""
    if df is None or df.empty:
        return df
    out = df
    has_id = "site_id" in out.columns
    has_name = "site_name" in out.columns
    if not has_id and not has_name and "site_id_name" not in out.columns:
        out = out.copy()
        out["site_id_name"] = "Unassigned"
        return out

    if has_id or has_name:
        # Already-clean labels are the common case. Do not walk every sales line
        # with a Python regex — that made Zone → Channel → Store → Manager
        # time out and surface as "cache is warming".
        sid = out["site_id"] if has_id else pd.Series([""] * len(out), index=out.index)
        sname = out["site_name"] if has_name else pd.Series([""] * len(out), index=out.index)
        if "site_id_name" in out.columns:
            cur = out["site_id_name"].fillna("").astype(str).str.strip()
            # Only real duplicates (same store code written twice). A normal
            # "CODE - Name" must not be rewritten — that walked ~90k lines and
            # the hierarchy request timed out as "cache is warming".
            needs = cur.eq("") | _doubled_site_label_mask(cur)
            if not bool(needs.any()):
                return out
            dirty_idx = needs[needs].index
            cleaned_dirty = [
                _prefer_store_display_label(a, b)
                for a, b in zip(sid.loc[dirty_idx].tolist(), sname.loc[dirty_idx].tolist())
            ]
            out = out.copy()
            out.loc[dirty_idx, "site_id_name"] = cleaned_dirty
            still = _doubled_site_label_mask(out["site_id_name"].astype(str))
            if bool(still.any()):
                sid_f = sid.fillna("").astype(str).str.strip()
                fallback = sid_f.where(sid_f.ne("") & ~sid_f.str.lower().isin(_BLANK_DIM_TOKENS), other=pd.NA)
                fallback = fallback.fillna(out["site_id_name"].map(_dedupe_site_id_name_text))
                out.loc[still, "site_id_name"] = fallback.loc[still].values
            return out
        out = out.copy()
        out["site_id_name"] = [
            _prefer_store_display_label(a, b)
            for a, b in zip(sid.tolist(), sname.tolist())
        ]
        return out

    # Only site_id_name present — still collapse duplicated full labels.
    cur = out["site_id_name"].fillna("").astype(str).str.strip()
    needs = cur.eq("") | _doubled_site_label_mask(cur)
    if bool(needs.any()):
        out = out.copy()
        out.loc[needs, "site_id_name"] = cur.loc[needs].map(_dedupe_site_id_name_text).values
    return out


def _align_soh_product_dims(soh_df: pd.DataFrame, sales_df: pd.DataFrame) -> pd.DataFrame:
    """Fill blank product dims on SOH from sales barcode mode (vectorized; assigns result)."""
    if (
        soh_df is None
        or soh_df.empty
        or sales_df is None
        or sales_df.empty
        or "barcode" not in soh_df.columns
        or "barcode" not in sales_df.columns
    ):
        return soh_df
    out = soh_df
    soh_bc = out["barcode"].astype(str).str.strip()
    for col in ("season", "brand", "department", "section", "sku", "size"):
        if col not in sales_df.columns:
            continue
        if col not in out.columns:
            out = out.copy()
            out[col] = ""
        cur = out[col].fillna("").astype(str).str.strip()
        blank = cur.str.lower().isin(_BLANK_DIM_TOKENS)
        if not bool(blank.any()):
            continue
        src = sales_df.loc[:, ["barcode", col]].copy()
        src["_bc"] = src["barcode"].astype(str).str.strip()
        src["_v"] = src[col].fillna("").astype(str).str.strip()
        src = src.loc[~src["_v"].str.lower().isin(_BLANK_DIM_TOKENS)]
        if src.empty:
            continue
        # First non-null label per barcode — avoids slow Python value_counts lambdas.
        mode = src.groupby("_bc", sort=False)["_v"].first()
        mapped = soh_bc.map(mode)
        fill_mask = blank & mapped.notna() & mapped.astype(str).str.strip().ne("")
        if bool(fill_mask.any()):
            if out is soh_df:
                out = out.copy()
                soh_bc = out["barcode"].astype(str).str.strip()
            out.loc[fill_mask, col] = mapped.loc[fill_mask].astype(str).str.strip().values
    return out


def _pick_best_store_label(values: Any) -> str:
    """Prefer SS-/PT- style store labels over bare numeric codes."""
    cleaned: List[str] = []
    for v in values:
        s = str(v).strip()
        if not s or s.lower() in _BLANK_DIM_TOKENS:
            continue
        if re.search(r"\s-\s*nan\s*$", s, flags=re.IGNORECASE):
            continue
        cleaned.append(s)
    if not cleaned:
        return "Unassigned"

    def _score(s: str) -> tuple:
        u = s.upper()
        pref = 1 if re.match(r"^(SS-|SSL-|PT-|PW-|P\d)", u) else 0
        has_name = 1 if " - " in s else 0
        return (pref, has_name, len(s))

    return max(cleaned, key=_score)


def _pick_mode_dim_label(values: Any) -> str:
    cleaned = [
        str(v).strip()
        for v in values
        if str(v).strip() and str(v).strip().lower() not in _BLANK_DIM_TOKENS
    ]
    preferred = [v for v in cleaned if v.lower() != "unassigned"]
    use = preferred or cleaned
    if not use:
        return "Unassigned"
    return str(pd.Series(use).mode().iloc[0])


def _channel_code_series(df: pd.DataFrame) -> pd.Series:
    """Normalize channel / sales_channel to PT|SSL|CENTRO|ICONIC|EBO|OTHER (vectorized)."""
    ch = (
        df["channel"].fillna("").astype(str).str.strip().str.upper()
        if "channel" in df.columns
        else pd.Series("", index=df.index, dtype=object)
    )
    known = {"PT", "SSL", "CENTRO", "ICONIC", "EBO"}
    blank = ch.eq("") | ~ch.isin(known)
    if "sales_channel" in df.columns:
        sc = df["sales_channel"].fillna("").astype(str).str.strip().str.upper()
        from_sc = pd.Series("", index=df.index, dtype=object)
        from_sc = from_sc.mask(sc.isin(known), sc)
        from_sc = from_sc.mask(from_sc.eq("") & sc.str.contains("SHOPPER|\\bSSL\\b", regex=True, na=False), "SSL")
        from_sc = from_sc.mask(from_sc.eq("") & sc.str.contains("PANTALOON|\\(PT\\)", regex=True, na=False), "PT")
        from_sc = from_sc.mask(from_sc.eq("") & sc.str.contains("CENTRO|RELIANCE", regex=True, na=False), "CENTRO")
        from_sc = from_sc.mask(from_sc.eq("") & sc.str.contains("ICONIC", regex=True, na=False), "ICONIC")
        from_sc = from_sc.mask(from_sc.eq("") & sc.str.contains("EBO|FRESCO", regex=True, na=False), "EBO")
        ch = ch.mask(blank, from_sc)
    ch = ch.where(ch.isin(known), "OTHER")
    return ch


def _analyzer_store_join_key(df: pd.DataFrame) -> pd.Series:
    """Cross-frame sales↔SOH join key (ignores divergent prepared ``_pivot_sk``).

    EBO prepared sales often stash ``EBO|964`` while SOH stashes
    ``EBO|FRESCOGLOBAL…``. Joining on cached ``_pivot_sk`` yields SOH=0 even when
    the store exists on both frames. Prefer compact numeric/P-codes when present,
    otherwise alphanumeric site_name/site_id — matching DuckDB SOH align SQL.
    """
    if df is None or df.empty:
        return pd.Series(dtype=object)
    ch = _channel_code_series(df)
    # Prepared SOH often keeps a stale ``channel`` (e.g. PT) while ``sales_channel``
    # correctly says Shoppers Stop (SSL). Prefer sales_channel for the join key so
    # SSL stock attaches to SSL sales (DuckDB already keys off sales_channel only).
    if "sales_channel" in df.columns:
        sc = df["sales_channel"].fillna("").astype(str).str.strip().str.upper()
        known = {"PT", "SSL", "CENTRO", "ICONIC", "EBO"}
        from_sc = pd.Series("", index=df.index, dtype=object)
        from_sc = from_sc.mask(sc.isin(known), sc)
        from_sc = from_sc.mask(from_sc.eq("") & sc.str.contains(r"SHOPPER|\bSSL\b", regex=True, na=False), "SSL")
        from_sc = from_sc.mask(from_sc.eq("") & sc.str.contains(r"PANTALOON|\(PT\)", regex=True, na=False), "PT")
        from_sc = from_sc.mask(from_sc.eq("") & sc.str.contains(r"CENTRO|RELIANCE", regex=True, na=False), "CENTRO")
        from_sc = from_sc.mask(from_sc.eq("") & sc.str.contains(r"ICONIC", regex=True, na=False), "ICONIC")
        from_sc = from_sc.mask(from_sc.eq("") & sc.str.contains(r"EBO|FRESCO", regex=True, na=False), "EBO")
        ch = from_sc.where(from_sc.isin(known), ch)
    # Store-label overrides — same idea as _channel_qualified_site_keys.
    parts = []
    for col in ("site_id_name", "site_id", "site_name", "customer_name"):
        if col in df.columns:
            parts.append(df[col].fillna("").astype(str))
    if parts:
        lab_u = (
            parts[0].str.cat(parts[1:], sep=" ").str.upper().str.strip()
            if len(parts) > 1
            else parts[0].str.upper().str.strip()
        )
        is_ssl = lab_u.str.contains(r"SHOPPERS?|HOPPER|\bSS[- ]|\bSSL\b", regex=True, na=False)
        is_pt = lab_u.str.contains(r"PANTALOON|\bPT[- ]|\bPW[- ]|\(P\d{2,5}\)", regex=True, na=False) & ~is_ssl
        ch = ch.mask(is_ssl, "SSL")
        ch = ch.mask(is_pt, "PT")
    compact = pd.Series("", index=df.index, dtype=object)
    try:
        from pipeline.pipeline_inputs import normalize_site_id_code

        for col in ("site_id_name", "site_id", "site_code", "site_manual_code", "site_name"):
            if col not in df.columns:
                continue
            norm = (
                df[col]
                .map(normalize_site_id_code)
                .fillna("")
                .astype(str)
                .str.strip()
                .str.upper()
            )
            is_compact = norm.str.fullmatch(r"\d{2,6}|P\d{2,5}", na=False)
            have_compact = compact.str.fullmatch(r"\d{2,6}|P\d{2,5}", na=False)
            compact = norm.where(is_compact & ~have_compact, compact)
    except Exception:
        pass
    name = pd.Series("", index=df.index, dtype=object)
    for col in ("site_name", "site_id", "site_id_name"):
        if col not in df.columns:
            continue
        raw = df[col].fillna("").astype(str).str.strip()
        name = name.mask(name.eq("") & raw.ne(""), raw)
    alnum = name.str.upper().str.replace(r"[^A-Z0-9]", "", regex=True)
    # Prefer site_name alnum when meaningful — EBO sales stash compact codes in
    # _pivot_sk / site_id_name (964) while SOH uses FRESCOGLOBAL… tokens.
    # Compact P-codes / short digits remain the fallback for PT/SSL.
    token = alnum.where(alnum.str.len() >= 8, compact)
    token = token.mask(token.eq(""), compact)
    token = token.mask(token.eq(""), alnum)
    return ch.astype(str) + "|" + token.astype(str)


def _channel_qualified_site_keys(df: pd.DataFrame) -> pd.Series:
    """Site key qualified by channel so SSL manual 114 and PT sitecode 114 never collapse."""
    # Prepared frames stash this once — avoid re-running regex over 250k+ rows per query.
    if "_pivot_sk" in df.columns:
        cached = df["_pivot_sk"]
        if len(cached) == len(df):
            return cached.astype(str)
    sk = frame_site_keys(df).astype(str).str.strip().str.upper()
    # Prefer compact codes (P233 / 114) from site_id_name — site_id alone can be "L&T-Vashi" → VASHI.
    try:
        from pipeline.pipeline_inputs import normalize_site_id_code

        best = pd.Series("", index=df.index, dtype=object)
        for col in ("site_id_name", "site_id", "site_code", "site_manual_code", "site_name"):
            if col not in df.columns:
                continue
            norm = (
                df[col]
                .map(normalize_site_id_code)
                .fillna("")
                .astype(str)
                .str.strip()
                .str.upper()
            )
            compact = norm.str.fullmatch(r"\d{2,6}|P\d{2,5}", na=False)
            best_compact = best.str.fullmatch(r"\d{2,6}|P\d{2,5}", na=False)
            best = norm.where(compact & ~best_compact, best)
            best = best.mask(best.eq("") & norm.ne(""), norm)
        sk = best.where(best.ne(""), sk)
    except Exception:
        pass

    ch = _channel_code_series(df)
    # Sales-channel labels are often wrong (SSL rows tagged Pantaloons). Prefer store name prefixes.
    # Use full concatenated label — site_id alone is often numeric ("269") while
    # site_id_name is "269 - SS-PUNJAGUTTA HYD (269)".
    parts = []
    for col in ("site_id_name", "site_id", "site_name", "customer_name"):
        if col in df.columns:
            parts.append(df[col].fillna("").astype(str))
    lab_u = (
        parts[0].str.cat(parts[1:], sep=" ").str.upper().str.strip()
        if parts
        else pd.Series("", index=df.index, dtype=object)
    )
    is_ssl = lab_u.str.contains(r"SHOPPERS?|HOPPER|\bSS[- ]|\bSSL\b", regex=True, na=False)
    is_pt = lab_u.str.contains(r"PANTALOON|\bPT[- ]|\bPW[- ]|\(P\d{2,5}\)", regex=True, na=False) & ~is_ssl
    ch = ch.mask(is_ssl, "SSL")
    ch = ch.mask(is_pt, "PT")
    return ch + "|" + sk


def _canon_map_from_source(src: pd.DataFrame, col: str) -> pd.Series:
    """Fast one-label-per-_pivot_sk map (no Python groupby.agg)."""
    s = src[col].fillna("").astype(str).str.strip()
    tmp = pd.DataFrame({"_pivot_sk": src["_pivot_sk"].values, col: s.values})
    tmp = tmp[tmp[col].ne("") & ~tmp[col].str.lower().isin(_BLANK_DIM_TOKENS)]
    if tmp.empty:
        return pd.Series(dtype=object)
    if col == "site_id_name":
        tmp = tmp[~tmp[col].str.contains(r"\s-\s*nan\s*$", case=False, regex=True, na=False)]
        if tmp.empty:
            return pd.Series(dtype=object)
        u = tmp[col].str.upper()
        tmp["_score"] = (
            u.str.match(r"^(SS-|SSL-|PT-|PW-|P\d)", na=False).astype("int16") * 10000
            + tmp[col].str.contains(" - ", regex=False, na=False).astype("int16") * 1000
            + tmp[col].str.len().clip(upper=900).astype("int16")
        )
        tmp = tmp.sort_values("_score", ascending=False)
    else:
        # Prefer real labels over Unassigned, keep first seen after that.
        tmp["_score"] = (~tmp[col].str.lower().eq("unassigned")).astype("int8")
        tmp = tmp.sort_values("_score", ascending=False)
    return tmp.drop_duplicates("_pivot_sk", keep="first").set_index("_pivot_sk")[col]


def _canonicalize_store_dims(
    df: pd.DataFrame,
    label_source: Optional[pd.DataFrame] = None,
    *,
    return_keys: bool = False,
):
    """One zone/channel/site_id_name per channel|store key so sales↔SOH pivot keys match."""
    if df is None or df.empty:
        return (df, pd.Series(dtype=object)) if return_keys else df

    out = df
    out_keys = _channel_qualified_site_keys(out)

    src = label_source if label_source is not None and not label_source.empty else out
    src_keys = out_keys if src is out else _channel_qualified_site_keys(src)
    src_view = src
    # Avoid full DataFrame copy — only keep columns needed for canon maps.
    dim_cols = [
        c
        for c in ("site_id_name", "sales_channel", "zone", "city_name", "state_name", "customer_name")
        if c in src_view.columns
    ]
    if not dim_cols:
        return (out, out_keys) if return_keys else out

    src_small = src_view.loc[:, dim_cols].copy()
    src_small["_pivot_sk"] = src_keys.values
    src_small = src_small[src_small["_pivot_sk"].astype(str).str.strip().ne("")]
    src_small = src_small[~src_small["_pivot_sk"].astype(str).str.endswith("|")]
    if src_small.empty:
        return (out, out_keys) if return_keys else out

    # Mutate in place when possible to avoid copying 250k+ sales rows.
    mutated = False
    for col in dim_cols:
        canon = _canon_map_from_source(src_small, col)
        if canon.empty:
            continue
        mapped = out_keys.map(canon)
        has_map = mapped.notna() & mapped.astype(str).str.strip().ne("")
        if not has_map.any():
            continue
        if not mutated:
            out = out.copy()
            mutated = True
        if col not in out.columns:
            out[col] = mapped
        else:
            out.loc[has_map, col] = mapped[has_map]
    return (out, out_keys) if return_keys else out


def _store_metric_map(soh_df: pd.DataFrame, col: str) -> pd.Series:
    """Channel|site-key → metric sum from prepared daily-movement SOH frame."""
    if soh_df is None or soh_df.empty or col not in soh_df.columns:
        return pd.Series(dtype=float)
    # Use join keys that match sales stores even when prepared _pivot_sk diverges.
    keys = _analyzer_store_join_key(soh_df)
    qty = pd.to_numeric(soh_df[col], errors="coerce").fillna(0.0)
    tmp = pd.DataFrame({"_sk": keys, col: qty})
    tmp = tmp[tmp["_sk"].astype(str).str.strip().ne("")]
    tmp = tmp[~tmp["_sk"].astype(str).str.endswith("|")]
    if tmp.empty:
        return pd.Series(dtype=float)
    return tmp.groupby("_sk", sort=False)[col].sum()


def _store_current_soh_map(soh_df: pd.DataFrame) -> pd.Series:
    """Channel|site-key → current SOH. Negative master qty is not sellable stock."""
    return _store_metric_map(soh_df, "current_soh").clip(lower=0.0)


def _build_soh_group_summary(
    soh_df: pd.DataFrame,
    sales_df: pd.DataFrame,
    grp_cols: List[str],
    *,
    store_soh: Optional[pd.Series] = None,
    store_disp: Optional[pd.Series] = None,
    store_avail: Optional[pd.Series] = None,
    sales_sk: Optional[pd.Series] = None,
    cy_store_keys: Optional[set] = None,
) -> Optional[pd.DataFrame]:
    """
    Store-level cuts: attach SOH/dispatch/available by channel|site key.
    Product cuts: sum barcode metrics after store dims were synced from sales.
    """
    if soh_df is None or soh_df.empty or not grp_cols:
        return None
    if sales_df is None or sales_df.empty:
        return None
    if not all(c in sales_df.columns for c in grp_cols):
        return None

    metric_cols = [c for c in ("current_soh", "dispatch_qty", "available_qty") if c in soh_df.columns]
    if not metric_cols:
        return None
    agg = {c: (c, "sum") for c in metric_cols}

    product_dims = [c for c in grp_cols if c not in _STORE_LEVEL_DIMS]
    if product_dims:
        if not all(c in soh_df.columns for c in grp_cols):
            return None
        return soh_df.groupby(grp_cols, observed=True).agg(**agg)

    if store_soh is None:
        store_soh = _store_current_soh_map(soh_df)
    if store_soh is None or store_soh.empty:
        return None
    if store_disp is None and "dispatch_qty" in metric_cols:
        store_disp = _store_metric_map(soh_df, "dispatch_qty")
    if store_avail is None and "available_qty" in metric_cols:
        store_avail = _store_metric_map(soh_df, "available_qty")

    tmp = sales_df.loc[:, list(grp_cols)].copy()
    # Always join SOH via analyzer join key (not prepared _pivot_sk / sales_sk cache).
    sk = _analyzer_store_join_key(sales_df)
    tmp["_sk"] = pd.Series(sk, index=sales_df.index).astype(str).str.strip().values
    tmp = tmp[tmp["_sk"].ne("") & ~tmp["_sk"].str.endswith("|")]
    # LY-only stores are in the frame for sales_ly rows — do not attach current SOH
    # to them (matches DuckDB sales-cut align; keeps GT ≈ children).
    if cy_store_keys is not None:
        tmp = tmp[tmp["_sk"].isin(cy_store_keys)]
    tmp = tmp.drop_duplicates(subset=list(grp_cols) + ["_sk"])
    if tmp.empty:
        return None
    tmp["current_soh"] = tmp["_sk"].map(store_soh).fillna(0.0)
    if "dispatch_qty" in metric_cols and store_disp is not None and not store_disp.empty:
        tmp["dispatch_qty"] = tmp["_sk"].map(store_disp).fillna(0.0)
    elif "dispatch_qty" in metric_cols:
        tmp["dispatch_qty"] = 0.0
    if "available_qty" in metric_cols and store_avail is not None and not store_avail.empty:
        tmp["available_qty"] = tmp["_sk"].map(store_avail).fillna(0.0)
    elif "available_qty" in metric_cols:
        tmp["available_qty"] = 0.0
    out_agg = {c: (c, "sum") for c in metric_cols if c in tmp.columns}
    return tmp.groupby(grp_cols, observed=True).agg(**out_agg)


def _align_soh_store_dims(soh_df: pd.DataFrame, sales_df: pd.DataFrame) -> pd.DataFrame:
    """Align site_id_name, sales_channel, zone, city, state on SOH from sales frame so pivot joins 100%."""
    if soh_df is None or soh_df.empty or sales_df is None or sales_df.empty:
        return soh_df

    store_cols = ["site_id_name", "sales_channel", "zone", "city_name", "state_name", "customer_name"]
    valid_cols = [c for c in store_cols if c in sales_df.columns]
    if not valid_cols:
        return soh_df

    sales_small = sales_df.loc[:, valid_cols].copy()
    sales_small["_pivot_sk"] = _channel_qualified_site_keys(sales_df).values
    sales_small = sales_small[sales_small["_pivot_sk"].astype(str).str.strip().ne("")]
    sales_small = sales_small[~sales_small["_pivot_sk"].astype(str).str.endswith("|")]
    if sales_small.empty:
        return soh_df

    soh_keys = _channel_qualified_site_keys(soh_df)
    for col in valid_cols:
        canon = _canon_map_from_source(sales_small, col)
        if canon.empty:
            continue
        mapped = soh_keys.map(canon)
        has_map = mapped.notna() & mapped.astype(str).str.strip().ne("")
        if col not in soh_df.columns:
            soh_df[col] = mapped.fillna("")
        else:
            soh_df.loc[has_map, col] = mapped[has_map]

    return soh_df

# Channel codes (PT) ↔ display labels used in prepared analyzer frames
_CHANNEL_CODE_DISPLAY = {
    "pt": "pantaloons (pt)",
    "ssl": "shoppers stop (ssl)",
    "centro": "centro",
    "iconic": "iconic",
    "ebo": "ebo",
}


def _expand_channel_filter_vals(clean_vals: List[str]) -> List[str]:
    """Accept PT / Pantaloons (PT) interchangeably for sales_channel filters."""
    expanded: set[str] = set()
    for v in clean_vals:
        vv = str(v).strip().lower()
        if not vv:
            continue
        expanded.add(vv)
        if vv in _CHANNEL_CODE_DISPLAY:
            expanded.add(_CHANNEL_CODE_DISPLAY[vv])
        for code, label in _CHANNEL_CODE_DISPLAY.items():
            if vv == label or vv == code or f"({code})" in vv:
                expanded.add(code)
                expanded.add(label)
    return list(expanded)


def _apply_dimension_list_filter(frame: pd.DataFrame, field: str, clean_vals: List[str]) -> pd.DataFrame:
    if frame is None or frame.empty or not clean_vals:
        return frame
    if field == "sales_channel":
        vals = _expand_channel_filter_vals(clean_vals)
        col = frame[field].astype(str).str.strip().str.lower() if field in frame.columns else pd.Series("", index=frame.index)
        mask = col.isin(vals)
        # Also match short codes embedded in display labels
        for v in vals:
            if len(v) <= 6 and "(" not in v:
                mask = mask | col.str.contains(re.escape(v), na=False, regex=True)
        for alt_col in ("channel", "store_type", "store_type_master"):
            if alt_col in frame.columns:
                alt_s = frame[alt_col].astype(str).str.strip().str.lower()
                mask = mask | alt_s.isin(vals)
        return frame[mask]
    if field == "site_id":
        val_set: set[str] = set()
        for raw in clean_vals:
            val = str(raw).strip().lower()
            if val.endswith(".0"):
                val = val[:-2]
            if not val:
                continue
            val_set.add(val)
            match = re.search(r"\(([^)]+)\)", val)
            if match:
                val_set.add(re.sub(r"\s+", "", match.group(1)))
        if not val_set or val_set == {"__none__"}:
            return frame.iloc[0:0]
        site_cols = [c for c in ("site_id", "site_code", "site_manual_code", "site_short_code", "site_id_name", "site_name", "slcode") if c in frame.columns]
        if not site_cols:
            return frame
        mask = pd.Series(False, index=frame.index)
        for c in site_cols:
            s_series = frame[c].astype(str).str.strip().str.lower().str.replace(r"\.0$", "", regex=True)
            extracted = s_series.str.extract(r"\(([^)]+)\)", expand=False).fillna("").str.strip()
            mask = mask | s_series.isin(val_set) | extracted.isin(val_set)
        return frame[mask]
    if field in frame.columns:
        val_set = {str(x).strip().lower() for x in clean_vals if str(x).strip()}
        return frame[frame[field].astype(str).str.strip().str.lower().isin(val_set)]
    return frame


class ValueConfig(BaseModel):
    field: str
    aggregation: Optional[Literal["sum", "avg", "count", "min", "max", "calculated"]] = "sum"
    custom_label: Optional[str] = None


class SortConfig(BaseModel):
    field: str
    direction: Literal["asc", "desc"] = "desc"


class AnalyzerQueryRequest(BaseModel):
    rows: List[str] = Field(default_factory=list, description="Hierarchy row dimensions")
    columns: List[str] = Field(default_factory=list, description="Column split dimensions")
    values: List[ValueConfig] = Field(default_factory=list, description="Measures and aggregations")
    filters: Dict[str, Union[List[Any], Any]] = Field(default_factory=dict, description="Filter conditions")
    sort: Optional[List[SortConfig]] = None
    year: Optional[int] = 2027
    search: Optional[str] = None


@router.get("/metadata")
def get_metadata():
    """Return all approved dimensions, measures, and available aggregation functions."""
    return {
        "ok": True,
        "dimensions": DIMENSIONS_META,
        "measures": MEASURES_META,
        "aggregations": ["sum", "avg", "count", "min", "max"],
    }


# In-Memory Cache for prepared dataframes, filter options, and query results
_PREPARED_DF_CACHE: Dict[int, pd.DataFrame] = {}
_PREPARED_SOURCE_VERSIONS: Dict[int, str] = {}
_PREPARED_BLOB_CHECK_AT: Dict[int, float] = {}
_PREPARED_BLOB_CHECK_TTL_SEC = 300.0  # re-check morning blob key every 5 min
_FILTER_OPTIONS_CACHE: Dict[int, Dict[str, Any]] = {}
_QUERY_RESULT_CACHE: Dict[str, Dict[str, Any]] = {}
_CACHE_MAX_ENTRIES = 128
_DUCKDB_FALLBACK_COUNT = 0
_ANALYZER_PEAK_RSS_MB = 0.0


def _process_rss_mb() -> float:
    """Current process RSS in MB (−1 if unavailable)."""
    try:
        import psutil  # type: ignore

        return round(psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024), 1)
    except Exception:
        pass
    try:
        import resource  # Unix

        # Linux: KB; macOS: bytes
        rss = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        if sys.platform == "darwin":
            return round(rss / (1024 * 1024), 1)
        return round(rss / 1024.0, 1)
    except Exception:
        pass
    try:
        import ctypes
        from ctypes import wintypes

        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        GetCurrentProcess = ctypes.windll.kernel32.GetCurrentProcess
        GetProcessMemoryInfo = ctypes.windll.psapi.GetProcessMemoryInfo
        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
        if GetProcessMemoryInfo(GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            return round(counters.WorkingSetSize / (1024 * 1024), 1)
    except Exception:
        pass
    return -1.0


def _note_peak_rss(rss: float | None = None) -> float:
    global _ANALYZER_PEAK_RSS_MB
    cur = _process_rss_mb() if rss is None else float(rss)
    if cur > _ANALYZER_PEAK_RSS_MB:
        _ANALYZER_PEAK_RSS_MB = cur
    return cur


def _ms_since(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000.0, 1)


def _log_analyzer_query_timing(timing: dict[str, Any], *, filters: dict | None = None) -> None:
    filter_snip: dict[str, Any] = {}
    if filters:
        # Cap values — avoid logging huge site_id lists.
        for k in list(filters.keys())[:8]:
            v = filters.get(k)
            if isinstance(v, (list, tuple)) and len(v) > 8:
                filter_snip[k] = list(v)[:8] + ["…"]
            else:
                filter_snip[k] = v
    logger.info(
        "Analyzer timing | request_id=%s engine=%s cache_source=%s "
        "mem=%s page_mem=%s disk=%s blob=%s blob_meta=%s cache_total=%s "
        "sem_wait=%s conn=%s duckdb=%s grouping=%s split=%s ly=%s soh=%s tree=%s "
        "pandas_fallback=%s fallback_reason=%s fallback_count=%s "
        "json=%s total_api=%s rss=%s peak_rss=%s filters=%s",
        timing.get("request_id"),
        timing.get("engine"),
        timing.get("cache_source"),
        timing.get("memory_cache_lookup_ms"),
        timing.get("page_memory_lookup_ms"),
        timing.get("local_disk_lookup_ms"),
        timing.get("blob_lookup_ms"),
        timing.get("blob_meta_ms"),
        timing.get("cache_lookup_ms"),
        timing.get("semaphore_wait_ms"),
        timing.get("conn_acquire_ms"),
        timing.get("duckdb_query_ms"),
        timing.get("grouping_sets_ms"),
        timing.get("column_split_ms"),
        timing.get("ly_calc_ms"),
        timing.get("soh_align_ms"),
        timing.get("tree_build_ms"),
        timing.get("pandas_fallback_ms"),
        timing.get("duckdb_fallback_reason"),
        timing.get("duckdb_fallback_count"),
        timing.get("json_serialization_ms"),
        timing.get("total_api_ms"),
        timing.get("rss_mb"),
        timing.get("peak_rss_mb"),
        filter_snip,
    )

_PREPARED_SOH_CACHE: Optional[pd.DataFrame] = None
_PREPARED_SOH_TIME: float = 0.0
_PREPARED_SOH_SIG: Optional[str] = None
# v6: Current SOH follows CURRENT_SOH_SOURCE (soh_master default); dispatch/STR still from DM
_PREPARED_SOH_PARQUET = Path("data/api_cache/soh_analyzer_prepared_v6_current.parquet")
_PREPARED_SOH_META = Path("data/api_cache/soh_analyzer_prepared_v6_current.key")
_DM_SOH_PATH = Path("data/api_cache/daily_inventory_movement.parquet")


def _dm_soh_signature() -> str:
    """Fingerprint of daily movement file so prepared analyzer SOH cannot go stale on host."""
    if not _DM_SOH_PATH.exists():
        return "missing"
    st = _DM_SOH_PATH.stat()
    return f"dm:{st.st_mtime_ns}:{st.st_size}"


def _prepared_soh_signature() -> str:
    """Include Current SOH source so Master vs daily never soft-serve the wrong qty."""
    try:
        from api.current_soh import current_soh_source

        src = current_soh_source()
    except Exception:
        src = "soh_master"
    return f"src:{src}|{_dm_soh_signature()}|{_analyzer_schema_stamp()}"


def _dm_soh_size_token(sig: str | None = None) -> str:
    """Size-only token — Azure blob re-downloads rewrite mtime but keep byte size."""
    text = (sig or _dm_soh_signature()).strip()
    parts = text.split(":")
    if len(parts) >= 3 and parts[0] == "dm":
        return parts[-1]
    return ""


def _soh_meta_usable(meta: str, current_sig: str) -> bool:
    """Accept prepared SOH when key matches exactly or DM byte-size still matches.

    Morning publish stamps dm:{mtime_ns}:{size}. Hosted then re-pulls
    daily_inventory_movement.parquet (new mtime, same size) and used to treat
    that as a full cache miss → empty Current SOH / Dispatch / STR.
    """
    text = (meta or "").strip()
    if not text or current_sig == "missing":
        return False
    if text == current_sig:
        return True
    meta_size = _dm_soh_size_token(text)
    cur_size = _dm_soh_size_token(current_sig)
    if meta_size and cur_size and meta_size == cur_size and meta_size not in {"0", "missing"}:
        return True
    return text.endswith(f":{_analyzer_schema_stamp()}")


def _prepared_soh_frame_ok(cached: pd.DataFrame | None) -> bool:
    return (
        cached is not None
        and not cached.empty
        and "dispatch_qty" in cached.columns
        and "current_soh" in cached.columns
    )


def _read_prepared_soh_parquet() -> pd.DataFrame | None:
    try:
        cached = pd.read_parquet(_PREPARED_SOH_PARQUET)
    except Exception as read_exc:
        logger.warning("Failed to read prepared SOH parquet: %s", read_exc)
        return None
    if not _prepared_soh_frame_ok(cached):
        return None
    if "_pivot_sk" not in cached.columns:
        cached = cached.copy()
        cached["_pivot_sk"] = _channel_qualified_site_keys(cached)
    return cached


_PREPARED_SOH_BLOB_CHECK_AT: float = 0.0


def _sync_prepared_soh_blob(*, force: bool = False) -> bool:
    """Pull morning prepared SOH from blob when local key is missing/stale."""
    global _PREPARED_SOH_BLOB_CHECK_AT
    import time

    now = time.time()
    if not force and (now - _PREPARED_SOH_BLOB_CHECK_AT) < _PREPARED_BLOB_CHECK_TTL_SEC:
        return False
    _PREPARED_SOH_BLOB_CHECK_AT = now

    soh_blob, soh_key_blob = _blob_prepared_soh_names()
    _PREPARED_SOH_META.parent.mkdir(parents=True, exist_ok=True)
    tmp_key = _PREPARED_SOH_META.with_suffix(".blobkey")
    if not _try_pull_blob_file(soh_key_blob, tmp_key):
        return False
    try:
        blob_meta = tmp_key.read_text(encoding="utf-8").strip()
    except Exception:
        return False
    if not blob_meta:
        return False

    local_meta = ""
    if _PREPARED_SOH_META.exists():
        try:
            local_meta = _PREPARED_SOH_META.read_text(encoding="utf-8").strip()
        except Exception:
            local_meta = ""
    need_parquet = (
        force
        or blob_meta != local_meta
        or not _PREPARED_SOH_PARQUET.exists()
        or _PREPARED_SOH_PARQUET.stat().st_size <= 10_000
        or not _soh_meta_usable(local_meta, _dm_soh_signature())
    )
    if not need_parquet:
        return False
    if not _try_pull_blob_file(soh_blob, _PREPARED_SOH_PARQUET):
        return False
    try:
        _PREPARED_SOH_META.write_text(blob_meta, encoding="utf-8")
    except Exception:
        pass
    logger.info("Analyzer prepared SOH synced from blob meta=%s", blob_meta[:80])
    return _PREPARED_SOH_PARQUET.exists() and _PREPARED_SOH_PARQUET.stat().st_size > 10_000


def _get_prepared_soh_df(force_refresh: bool = False) -> pd.DataFrame:
    """Prepared Analyzer SOH: Current SOH from api.current_soh (Master default); dispatch from DM."""
    global _PREPARED_SOH_CACHE, _PREPARED_SOH_TIME, _PREPARED_SOH_SIG
    import time

    now = time.time()
    sig = _prepared_soh_signature()

    if (
        not force_refresh
        and _PREPARED_SOH_CACHE is not None
        and _PREPARED_SOH_SIG == sig
        and not _PREPARED_SOH_CACHE.empty
    ):
        return _PREPARED_SOH_CACHE

    # Soft RAM hit: same DM byte-size after Azure re-download rewrote mtime.
    if (
        not force_refresh
        and _PREPARED_SOH_CACHE is not None
        and not _PREPARED_SOH_CACHE.empty
        and _PREPARED_SOH_SIG
        and _soh_meta_usable(_PREPARED_SOH_SIG, sig)
    ):
        _PREPARED_SOH_SIG = sig
        _PREPARED_SOH_TIME = now
        return _PREPARED_SOH_CACHE

    if not force_refresh:
        local_meta = ""
        if _PREPARED_SOH_META.exists():
            try:
                local_meta = _PREPARED_SOH_META.read_text(encoding="utf-8").strip()
            except Exception:
                local_meta = ""
        if (
            not _PREPARED_SOH_PARQUET.exists()
            or not _PREPARED_SOH_META.exists()
            or not _soh_meta_usable(local_meta, sig)
        ):
            _sync_prepared_soh_blob(force=not _PREPARED_SOH_PARQUET.exists())

    if not force_refresh and _PREPARED_SOH_PARQUET.exists() and _PREPARED_SOH_META.exists():
        try:
            meta = _PREPARED_SOH_META.read_text(encoding="utf-8").strip()
            if _soh_meta_usable(meta, sig):
                cached = _read_prepared_soh_parquet()
                if cached is not None:
                    if meta != sig:
                        logger.info(
                            "Analyzer prepared SOH soft-accepted (DM size match, mtime drift) meta=%s sig=%s",
                            meta,
                            sig,
                        )
                    _PREPARED_SOH_CACHE = cached
                    _PREPARED_SOH_SIG = sig
                    _PREPARED_SOH_TIME = now
                    return cached
        except Exception as read_exc:
            logger.warning("Failed to read prepared SOH parquet: %s", read_exc)

    # Soft-serve local prepared SOH when DM sig drifted (blob re-download / mtime).
    # Avoid cold DM rebuild on the request path — that blocked Sales Overview Pivot
    # for 30–60s and the FE showed "cache is warming".
    if not force_refresh and _PREPARED_SOH_PARQUET.exists():
        cached = _read_prepared_soh_parquet()
        if cached is not None:
            logger.warning(
                "Analyzer prepared SOH soft-serving local parquet after sig miss (azure=%s)",
                _on_azure(),
            )
            _PREPARED_SOH_CACHE = cached
            _PREPARED_SOH_SIG = sig
            _PREPARED_SOH_TIME = now
            return cached

    # Hosted: never rebuild SOH from raw daily_inventory_movement on the request path —
    # that loads 400k+ pairs and takes the single worker down (502/503 cascade).
    if _on_azure() and not force_refresh:
        logger.warning("Analyzer prepared SOH miss on Azure — skipping cold DM rebuild")
        return pd.DataFrame()

    channel_map = {
        "PT": "Pantaloons (PT)",
        "SSL": "Shoppers Stop (SSL)",
        "CENTRO": "Centro",
        "ICONIC": "Iconic",
        "EBO": "EBO",
        "RELIANCE": "Centro",
    }

    try:
        try:
            from api.current_soh import ensure_daily_movement_from_blob

            ensure_daily_movement_from_blob()
        except Exception:
            pass
        if not _DM_SOH_PATH.exists():
            logger.warning("daily_inventory_movement.parquet missing — analyzer SOH empty")
            return pd.DataFrame()

        from api.current_soh import load_latest_closing_soh_pairs

        # Canonical current_soh — same pairs as Home / Sales KPIs (Master by default)
        latest = load_latest_closing_soh_pairs(force_refresh=True)
        if latest.empty:
            logger.warning("Current SOH pairs empty — analyzer SOH empty")
            return pd.DataFrame()

        cols = [
            "store", "barcode", "date",
            "dispatch_in", "ibt_in", "sales_return_qty", "sales_qty",
            "store_name", "channel", "sales_channel", "zone",
            "sku", "size", "season", "department", "section",
        ]
        dm = pd.read_parquet(_DM_SOH_PATH, columns=cols)
        dm["store"] = dm["store"].astype(str).str.strip().str.upper()
        dm["barcode"] = (
            dm["barcode"].astype(str).str.strip().str.upper().str.replace(r"\.0$", "", regex=True)
        )

        # Lifetime inflows / sales per store × barcode (dims + available/dispatch)
        life = (
            dm.groupby(["store", "barcode"], sort=False)
            .agg(
                dispatch_qty=("dispatch_in", "sum"),
                ibt_in=("ibt_in", "sum"),
                sales_return_qty=("sales_return_qty", "sum"),
                dm_sales_qty=("sales_qty", "sum"),
                store_name=("store_name", "first"),
                channel_raw=("channel", "first"),
                sales_channel_code=("sales_channel", "first"),
                zone=("zone", "first"),
                sku=("sku", "first"),
                size=("size", "first"),
                season=("season", "first"),
                department=("department", "first"),
                section=("section", "first"),
            )
            .reset_index()
        )

        # Right-join on daily pairs so Current SOH qty cannot include Combined gap-fill
        soh_df = latest[["store", "barcode", "closing_soh"]].merge(
            life, on=["store", "barcode"], how="left"
        )
        soh_df["closing_soh"] = pd.to_numeric(soh_df["closing_soh"], errors="coerce").fillna(0.0)
        soh_df["current_soh"] = soh_df["closing_soh"]
        soh_df["dispatch_qty"] = pd.to_numeric(soh_df["dispatch_qty"], errors="coerce").fillna(0.0)
        soh_df["ibt_in"] = pd.to_numeric(soh_df["ibt_in"], errors="coerce").fillna(0.0)
        soh_df["sales_return_qty"] = pd.to_numeric(soh_df["sales_return_qty"], errors="coerce").fillna(0.0)
        # Available = Opening(0) + Disp + IBT In + Sales Return
        soh_df["available_qty"] = soh_df["dispatch_qty"] + soh_df["ibt_in"] + soh_df["sales_return_qty"]

        soh_df["site_id"] = soh_df["store"]
        soh_df["site_name"] = soh_df["store_name"].fillna("").astype(str)
        # channel code for SSL|114 vs PT|114 keys
        code = soh_df["sales_channel_code"].fillna("").astype(str).str.strip().str.upper()
        code = code.replace({"RELIANCE": "CENTRO"})
        soh_df["channel"] = code.where(code.isin({"PT", "SSL", "CENTRO", "ICONIC", "EBO"}), "OTHER")
        soh_df["sales_channel"] = soh_df["channel"].map(channel_map).fillna(soh_df["channel"])
        soh_df["site_id_name"] = (
            soh_df["site_id"].astype(str).str.strip() + " - " + soh_df["site_name"].astype(str).str.strip()
        ).str.strip(" -")
        soh_df["customer_name"] = soh_df["sales_channel"]

        try:
            from pipeline.item_master import enrich_sales_dept_section_from_item_master

            soh_df = enrich_sales_dept_section_from_item_master(soh_df, barcode_col="barcode")
        except Exception as enrich_exc:
            logger.warning("SOH item enrich skipped: %s", enrich_exc)

        # Manager/cluster (+ zone/city/grade) so SOH/STR roll up under the same RM as sales.
        soh_df = _apply_manager_cluster_dims(soh_df)

        if "site_name_master" in soh_df.columns:
            blank = soh_df["site_name"].astype(str).str.strip().eq("")
            soh_df.loc[blank, "site_name"] = soh_df.loc[blank, "site_name_master"].astype(str)
            blank_id = soh_df["site_id_name"].isna() | soh_df["site_id_name"].astype(str).str.strip().eq("")
            soh_df.loc[blank_id, "site_id_name"] = (
                soh_df.loc[blank_id, "site_id"].astype(str).str.strip()
                + " - "
                + soh_df.loc[blank_id, "site_name"].astype(str).str.strip()
            ).str.strip(" -")

        if "city_name" not in soh_df.columns:
            soh_df["city_name"] = soh_df["city"] if "city" in soh_df.columns else ""
        if "state_name" not in soh_df.columns:
            soh_df["state_name"] = soh_df["state"] if "state" in soh_df.columns else ""
        if "mdq" not in soh_df.columns:
            soh_df["mdq"] = 0.0
        else:
            soh_df["mdq"] = pd.to_numeric(soh_df["mdq"], errors="coerce").fillna(0.0)

        # Keep display sales_channel labels; keep code in channel for keying
        soh_df = soh_df.drop(
            columns=["closing_soh", "store", "channel_raw", "sales_channel_code"],
            errors="ignore",
        )
        for c in soh_df.columns:
            if isinstance(soh_df[c].dtype, pd.CategoricalDtype):
                soh_df[c] = soh_df[c].astype(str)

        soh_df = _fill_blank_dims(soh_df)
        soh_df = _collapse_repeated_mdq(soh_df)
        if "_pivot_sk" in soh_df.columns:
            soh_df = soh_df.drop(columns=["_pivot_sk"])
        soh_df["_pivot_sk"] = _channel_qualified_site_keys(soh_df)
        try:
            _PREPARED_SOH_PARQUET.parent.mkdir(parents=True, exist_ok=True)
            soh_df.to_parquet(_PREPARED_SOH_PARQUET, index=False)
            _PREPARED_SOH_META.write_text(sig, encoding="utf-8")
        except Exception as save_exc:
            logger.warning("Failed to save prepared SOH parquet: %s", save_exc)
        _PREPARED_SOH_CACHE = soh_df
        _PREPARED_SOH_SIG = sig
        _PREPARED_SOH_TIME = now
        try:
            from api.current_soh import current_soh_source as _soh_src

            _src_label = _soh_src()
        except Exception:
            _src_label = "unknown"
        logger.info(
            "Analyzer SOH prepared (%s): rows=%d soh=%.0f dispatch=%.0f sig=%s",
            _src_label,
            len(soh_df),
            float(pd.to_numeric(soh_df["current_soh"], errors="coerce").fillna(0).sum()),
            float(pd.to_numeric(soh_df["dispatch_qty"], errors="coerce").fillna(0).sum()),
            sig,
        )
        return _PREPARED_SOH_CACHE
    except Exception as exc:
        logger.warning("Failed to prepare SOH dataframe for analyzer: %s", exc)
        return pd.DataFrame()


def _analyzer_schema_stamp() -> str:
    """Bump when prepared-frame columns / channel logic change (invalidates blob caches)."""
    from api.financial_calendar import CALENDAR_VERSION

    # ch8: Current SOH from SOH-Master (CURRENT_SOH_SOURCE) on prepared analyzer frames
    return f"{CALENDAR_VERSION}:ch8"


def _prepared_sales_disk_paths(year: int, version: str) -> tuple[Path, Path]:
    """Stable local names (same as blob) so hosted digests cannot miss morning parquet.

    Older builds used a content digest in the filename; Azure then looked for a
    different digest than the published blob and returned empty → 503 warming.
    """
    _ = version  # version is stored in the .key file, not the filename
    base = Path("data/api_cache")
    return (
        base / f"sales_analyzer_prepared_v4_y{year}.parquet",
        base / f"sales_analyzer_prepared_v4_y{year}.key",
    )


def _find_prepared_sales_parquet(year: int) -> Path | None:
    """Prefer stable path, else newest legacy digest parquet for the year."""
    stable = Path("data/api_cache") / f"sales_analyzer_prepared_v4_y{year}.parquet"
    if stable.exists() and stable.stat().st_size > 10_000:
        return stable
    matches = sorted(
        Path("data/api_cache").glob(f"sales_analyzer_prepared_v4_y{year}_*.parquet"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for path in matches:
        if path.stat().st_size > 10_000:
            return path
    return None


_PREPARED_SALES_ALWAYS_COLS = (
    "sales",
    "quantity",
    "gross_sales",
    "discount_amount",
    "date",
    "financial_year",
    "_pivot_sk",
    "site_id",
    "site_id_name",
    "sales_channel",
)


def _prepared_sales_columns_for_query(
    *,
    rows: Sequence[str] | None = None,
    columns: Sequence[str] | None = None,
    filters: dict[str, Any] | None = None,
    value_fields: Sequence[str] | None = None,
) -> list[str] | None:
    """Column prune list for pandas fallback. None = read all (safe default)."""
    needed: set[str] = set(_PREPARED_SALES_ALWAYS_COLS)
    for dim in list(rows or []) + list(columns or []):
        if dim:
            needed.add(str(dim))
    for field in (filters or {}):
        if field in (
            "timeline_mode",
            "timeline_values",
            "search",
            "from_date",
            "to_date",
            "date_range",
        ):
            continue
        needed.add(str(field))
    for vf in value_fields or []:
        needed.add(str(vf))
    # Timeline / FY filters need calendar helpers present on prepared frames.
    needed.update({"month", "week", "season", "barcode", "sku", "department", "zone"})
    return sorted(needed)


def _read_prepared_sales_parquet(
    path: Path,
    *,
    columns: Sequence[str] | None = None,
) -> pd.DataFrame | None:
    try:
        if columns:
            # Only request columns that exist — parquet schema may lag a deploy.
            try:
                import pyarrow.parquet as pq

                available = set(pq.ParquetFile(path).schema_arrow.names)
                use_cols = [c for c in columns if c in available]
                if "sales" not in use_cols and "sales" in available:
                    use_cols.append("sales")
                prepared = pd.read_parquet(path, columns=use_cols or None)
            except Exception:
                prepared = pd.read_parquet(path)
        else:
            prepared = pd.read_parquet(path)
    except Exception as read_exc:
        logger.warning("Analyzer prepared sales read failed path=%s: %s", path.name, read_exc)
        return None
    if prepared is None or prepared.empty or "sales" not in prepared.columns:
        return None
    if "_pivot_sk" not in prepared.columns:
        prepared = prepared.copy()
        prepared["_pivot_sk"] = _channel_qualified_site_keys(prepared)
    return prepared


_PREPARED_BLOB_PREFIX = "cache/api_cache/"


def _blob_prepared_sales_names(year: int) -> tuple[str, str]:
    """Stable blob names (no digest) so hosted can pull after each daily warm."""
    return (
        f"{_PREPARED_BLOB_PREFIX}sales_analyzer_prepared_v4_y{year}.parquet",
        f"{_PREPARED_BLOB_PREFIX}sales_analyzer_prepared_v4_y{year}.key",
    )


def _blob_prepared_soh_names() -> tuple[str, str]:
    return (
        f"{_PREPARED_BLOB_PREFIX}soh_analyzer_prepared_v6_current.parquet",
        f"{_PREPARED_BLOB_PREFIX}soh_analyzer_prepared_v6_current.key",
    )


def _meta_matches_schema(meta: str, *, version: str | None = None) -> bool:
    """True when local/blob key is exact version or at least same schema stamp.

    Daily publish runs on a machine whose sales-file mtimes differ from Azure.
    Trust morning blob when CALENDAR_VERSION + channel stamp still match.
    """
    text = (meta or "").strip()
    if not text:
        return False
    if version and text == version:
        return True
    return text.endswith(f":{_analyzer_schema_stamp()}")


def _sync_prepared_sales_blob(year: int, parquet_path: Path, meta_path: Path) -> bool:
    """Pull prepared sales from blob when remote key is newer / different.

    Returns True when parquet/key were refreshed from blob (caller should drop RAM cache).
    Concurrent callers for the same year coalesce on one lock so we do not download twice.
    """
    lock = _prepared_sync_lock(year)
    if not lock.acquire(blocking=False):
        # Another request is already syncing this year — wait briefly then re-check disk.
        acquired = lock.acquire(timeout=30.0)
        if not acquired:
            return False
        try:
            return parquet_path.exists() and parquet_path.stat().st_size > 10_000
        finally:
            lock.release()
    try:
        blob_pq, blob_key = _blob_prepared_sales_names(year)
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_key = meta_path.with_suffix(".blobkey")
        if not _try_pull_blob_file(blob_key, tmp_key):
            return False
        try:
            blob_meta = tmp_key.read_text(encoding="utf-8").strip()
        except Exception:
            return False
        local_missing = not parquet_path.exists() or parquet_path.stat().st_size <= 10_000
        # Local miss: pull even if stamp is one generation behind — empty local → 503 warming.
        if not _meta_matches_schema(blob_meta):
            if not local_missing:
                return False
            logger.warning(
                "Analyzer prepared sales blob stamp lag year=%s meta=%s — pulling anyway (local miss)",
                year,
                (blob_meta or "")[-40:],
            )
        local_meta = ""
        if meta_path.exists():
            try:
                local_meta = meta_path.read_text(encoding="utf-8").strip()
            except Exception:
                local_meta = ""
        need_parquet = blob_meta != local_meta or local_missing
        if not need_parquet:
            return False
        if not _try_pull_blob_file(blob_pq, parquet_path):
            return False
        # Metadata only after parquet is verified on disk.
        if not (parquet_path.exists() and parquet_path.stat().st_size > 10_000):
            return False
        try:
            meta_path.write_text(blob_meta, encoding="utf-8")
        except Exception:
            pass
        logger.info("Analyzer prepared sales synced from blob year=%s", year)
        return True
    finally:
        lock.release()


_PREPARED_SYNC_LOCKS: Dict[str, threading.Lock] = {}
_PREPARED_SYNC_LOCKS_GUARD = threading.Lock()


def _prepared_sync_lock(year: int) -> threading.Lock:
    key = f"y{int(year)}"
    with _PREPARED_SYNC_LOCKS_GUARD:
        lock = _PREPARED_SYNC_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _PREPARED_SYNC_LOCKS[key] = lock
        return lock


def _try_pull_blob_file(blob_name: str, dest: Path) -> bool:
    """Download/copy blob to dest atomically (tmp → verify size → os.replace)."""
    try:
        import shutil

        from api.routes.sales_dashboard import _blob_store

        store = _blob_store()
        if store is None:
            return False
        local = store._ensure_local_file(blob_name)
        if local is None or not local.exists() or local.stat().st_size <= 0:
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        if local.resolve() == dest.resolve():
            return dest.exists() and dest.stat().st_size > 0
        # Stream copy to temp — never delete a good dest before the replacement is verified.
        tmp = dest.with_suffix(dest.suffix + f".tmp.{os.getpid()}")
        try:
            shutil.copyfile(local, tmp)
            if not tmp.exists() or tmp.stat().st_size <= 0:
                tmp.unlink(missing_ok=True)
                return False
            os.replace(tmp, dest)
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
        return dest.exists() and dest.stat().st_size > 0
    except Exception as exc:
        logger.warning("Blob pull failed for %s: %s", blob_name, exc)
        return False


def _read_prepared_sales_directly_from_blob(year: int) -> pd.DataFrame | None:
    """Download prepared sales into blob_cache and read it — no second api_cache copy."""
    try:
        from api.routes.sales_dashboard import _blob_store

        store = _blob_store()
        if store is None:
            return None
        blob_pq, blob_key = _blob_prepared_sales_names(year)
        local = store._ensure_local_file(blob_pq)
        if local is None or not local.exists() or local.stat().st_size <= 10_000:
            return None
        # Best-effort key sync for later soft-accept
        try:
            key_local = store._ensure_local_file(blob_key)
            meta_path = Path("data/api_cache") / f"sales_analyzer_prepared_v4_y{year}.key"
            if key_local is not None and key_local.exists():
                meta_path.parent.mkdir(parents=True, exist_ok=True)
                import shutil

                shutil.copyfile(key_local, meta_path)
        except Exception:
            pass
        return _read_prepared_sales_parquet(local)
    except Exception as exc:
        logger.warning("Direct blob prepared sales read failed year=%s: %s", year, exc)
        return None


def publish_prepared_analyzer_caches_to_blob() -> dict[str, Any]:
    """Upload prepared Analyzer SOH + sales year frames so Azure App Service cold-starts stay fast."""
    from api.blob_store import upload_local_file_with_retry
    from api.routes.sales_dashboard import _blob_store

    store = _blob_store()
    if store is None:
        return {"ok": False, "error": "blob store unavailable"}

    uploaded: list[str] = []
    errors: list[str] = []

    def _put(local: Path, blob_name: str) -> None:
        if not local.exists() or local.stat().st_size <= 0:
            errors.append(f"missing {local.name}")
            return
        try:
            ctype = (
                "application/json"
                if local.suffix.lower() in {".key", ".json"}
                else "application/octet-stream"
            )
            upload_local_file_with_retry(
                store.container,
                blob_name=blob_name,
                path=local,
                content_type=ctype,
            )
            uploaded.append(blob_name)
        except Exception as exc:
            errors.append(f"{blob_name}: {exc}")

    soh_pq, soh_key = _PREPARED_SOH_PARQUET, _PREPARED_SOH_META
    soh_blob, soh_key_blob = _blob_prepared_soh_names()
    _put(soh_pq, soh_blob)
    _put(soh_key, soh_key_blob)

    from api.sales_year_loader import analyzer_build_years

    for year in analyzer_build_years():
        # Stable path first (matches blob name); legacy digest files as fallback.
        local_pq = _find_prepared_sales_parquet(year)
        key_path = Path("data/api_cache") / f"sales_analyzer_prepared_v4_y{year}.key"
        blob_pq, blob_key = _blob_prepared_sales_names(year)
        if local_pq is not None:
            _put(local_pq, blob_pq)
            _put(key_path, blob_key)
        else:
            # Empty/missing prior FY (e.g. cur-2) is expected — do not fail publish.
            logger.info("Prepared analyzer publish skip year=%s (no local parquet)", year)

    ok = bool(uploaded) and not any("missing soh" in e for e in errors)
    logger.info(
        "Prepared analyzer caches publish ok=%s uploaded=%s errors=%s",
        ok,
        len(uploaded),
        errors[:5],
    )
    return {"ok": ok, "uploaded": uploaded, "errors": errors}


def _get_prepared_sales_df(year: int = 2027, force_refresh: bool = False) -> pd.DataFrame:
    """Sales Pivot frame from the FY year-folder files (2026+2027 for FY27)."""
    from api.sales_year_loader import (
        _mtime_key,
        _year_folder_paths,
        get_default_dashboard_year,
        is_all_years,
        load_sales_for_year,
    )

    # Option B: Analyzer no longer supports All Years (year=0). Map to current FY.
    if is_all_years(year) or int(year or 0) == 0:
        year = int(get_default_dashboard_year())
    years = [int(year)]
    sig = []
    for y in years:
        paths = _year_folder_paths(y)
        sig.append("|".join(_mtime_key(p) for p in paths) if paths else f"current:{y}")
    # Schema stamp must match _analyzer_schema_stamp() / morning blob publish.
    version = f"{'+'.join(sig)}:{_analyzer_schema_stamp()}"
    parquet_path, meta_path = _prepared_sales_disk_paths(year, version)

    def _cache_hit(prepared: pd.DataFrame, *, note: str) -> pd.DataFrame:
        # Label cleanup runs after the store-grain collapse (~500 rows).
        # Doing it here walks every sales line and the pivot request times out.
        _PREPARED_DF_CACHE[year] = prepared
        _PREPARED_SOURCE_VERSIONS[year] = version
        logger.info(
            "Analyzer prepared sales %s year=%s rows=%d",
            note,
            year,
            len(prepared),
        )
        return prepared

    if not force_refresh:
        now = time.time()
        last_check = _PREPARED_BLOB_CHECK_AT.get(year, 0.0)
        local_ready = parquet_path.exists() and parquet_path.stat().st_size > 10_000
        # Local/dev: never block the query path on Azure blob sync when disk cache
        # already exists — that hang was surfacing as FE "cache is warming".
        # Hosted: always re-sync on RAM miss / TTL so morning publishes land.
        should_sync_blob = _on_azure() or not local_ready
        if should_sync_blob and (
            year not in _PREPARED_DF_CACHE or (now - last_check) >= _PREPARED_BLOB_CHECK_TTL_SEC
        ):
            _PREPARED_BLOB_CHECK_AT[year] = now
            if _sync_prepared_sales_blob(year, parquet_path, meta_path):
                _PREPARED_DF_CACHE.pop(year, None)
                _PREPARED_SOURCE_VERSIONS.pop(year, None)
                _STORE_GRAIN_CACHE.clear()

    if not force_refresh and year in _PREPARED_DF_CACHE and not _PREPARED_DF_CACHE[year].empty:
        # Soft RAM hit: blob schema stamp may not equal local year-folder mtime version.
        cached = _PREPARED_DF_CACHE[year]
        if (
            _PREPARED_SOURCE_VERSIONS.get(year) == version
            or _on_azure()
            or _meta_matches_schema(_PREPARED_SOURCE_VERSIONS.get(year) or "", version=version)
        ):
            return cached

    if not force_refresh:
        meta_text = ""
        if meta_path.exists():
            try:
                meta_text = meta_path.read_text(encoding="utf-8").strip()
            except Exception:
                meta_text = ""
        candidate = _find_prepared_sales_parquet(year)
        # Prefer any valid local parquet over a cold Sales-Master rebuild.
        if candidate is not None:
            t0 = time.perf_counter()
            prepared = _read_prepared_sales_parquet(candidate)
            if prepared is not None:
                note = (
                    "disk-hit"
                    if _meta_matches_schema(meta_text, version=version) or not meta_text
                    else "disk-soft"
                )
                logger.info(
                    "Analyzer prepared sales %s year=%s rows=%d in %.2fs path=%s",
                    note,
                    year,
                    len(prepared),
                    time.perf_counter() - t0,
                    candidate.name,
                )
                return _cache_hit(prepared, note=note)

    # Hosted: cold Sales-Master prepare OOMs the worker → Azure 502 on Analyzer + images.
    if _on_azure() and not force_refresh:
        # One more forced blob pull to stable path, then any local parquet.
        if _sync_prepared_sales_blob(year, parquet_path, meta_path):
            prepared = _read_prepared_sales_parquet(parquet_path)
            if prepared is not None:
                return _cache_hit(prepared, note="azure-blob-retry")
        # Read straight from blob_cache download (avoids api_cache duplicate + copy OOM).
        prepared = _read_prepared_sales_directly_from_blob(year)
        if prepared is not None:
            return _cache_hit(prepared, note="azure-direct-blob")
        fallback = _find_prepared_sales_parquet(year)
        if fallback is not None:
            prepared = _read_prepared_sales_parquet(fallback)
            if prepared is not None:
                logger.warning(
                    "Analyzer prepared sales soft-serving local parquet on Azure year=%s path=%s",
                    year,
                    fallback.name,
                )
                return _cache_hit(prepared, note="azure-soft")
        logger.warning(
            "Analyzer prepared sales miss on Azure year=%s — skipping cold Sales-Master build",
            year,
        )
        return pd.DataFrame()

    t0 = time.perf_counter()
    frames = []
    for y in years:
        part = load_sales_for_year(y, force_refresh=force_refresh)
        if part is not None and not part.empty:
            frames.append(part)
    if not frames:
        return pd.DataFrame()
    raw_df = frames[0] if len(frames) == 1 else pd.concat(frames, ignore_index=True)
    prepared = _prepare_analyzer_dataframe(raw_df)
    _PREPARED_DF_CACHE[year] = prepared
    _PREPARED_SOURCE_VERSIONS[year] = version
    try:
        parquet_path.parent.mkdir(parents=True, exist_ok=True)
        # Drop older prepared files for this year to limit disk growth
        for pattern in (
            f"sales_analyzer_prepared_v2_y{year}_*.parquet",
            f"sales_analyzer_prepared_v3_y{year}_*",
            f"sales_analyzer_prepared_v4_y{year}_*.parquet",
        ):
            for stale in parquet_path.parent.glob(pattern):
                if stale != parquet_path:
                    try:
                        stale.unlink(missing_ok=True)
                    except Exception:
                        pass
        tmp = parquet_path.with_suffix(".tmp.parquet")
        prepared.to_parquet(tmp, index=False)
        os.replace(tmp, parquet_path)
        meta_path.write_text(version, encoding="utf-8")
        logger.info(
            "Analyzer prepared sales saved year=%s rows=%d bytes=%d path=%s",
            year,
            len(prepared),
            parquet_path.stat().st_size,
            parquet_path.name,
        )
    except Exception as save_exc:
        logger.warning("Analyzer prepared sales disk save failed year=%s: %s", year, save_exc)
    logger.info(
        "Analyzer prepared sales built year=%s rows=%d in %.2fs",
        year,
        len(prepared),
        time.perf_counter() - t0,
    )
    return prepared


def _analyzer_sql_enabled() -> bool:
    """Analyzer runs its own frame prep (channel labels, site_id_name, manager clean-up,
    LY / SOH / MDQ joins) that the SQL pivot does not reproduce, and the DuckDB/page-cache
    path already answers in ~1-3 s. Keep SQL off unless explicitly enabled."""
    return (os.environ.get("AZURE_SQL_ANALYZER") or "").strip().lower() in ("1", "true", "yes", "on")


@router.get("/filter-options")
def get_filter_options(year: int = 2027):
    """Return unique available values for dimension filters with in-memory caching."""
    if year in _FILTER_OPTIONS_CACHE:
        return {"ok": True, "options": _FILTER_OPTIONS_CACHE[year]}

    try:
        from api.azure_sql_analytics import sql_analyzer_filter_options, sql_query_ready

        if _analyzer_sql_enabled() and sql_query_ready():
            payload = sql_analyzer_filter_options(int(year))
            options = payload.get("options") or {}
            _FILTER_OPTIONS_CACHE[year] = options
            return {"ok": True, "options": options, "source": "azure_sql"}
    except Exception as sql_exc:
        logger.warning("Azure SQL analyzer filter-options failed; fallback: %s", sql_exc)

    try:
        df = _get_prepared_sales_df(year)
        if df is None or df.empty:
            return {"ok": True, "options": {}}

        options: Dict[str, List[Any]] = {}

        for dim in [
            "zone",
            "sales_channel",
            "brand",
            "department",
            "section",
            "store_grade",
            "cluster",
            "manager",
            "state_name",
            "city_name",
            "season",
            "financial_year",
        ]:
            if dim in df.columns:
                unique_vals = sorted([str(x) for x in df[dim].dropna().unique() if str(x).strip() and str(x).lower() != "nan"])
                options[dim] = unique_vals[:300]

        from api.sales_year_loader import analyzer_visible_years, fy_token

        visible = analyzer_visible_years()
        # Analyzer filter UI: FY26 through the current FY.
        options["financial_year"] = [fy_token(y) for y in visible]

        if "site_id" in df.columns:
            site_pairs = (
                df[["site_id", "site_name"]]
                .dropna(subset=["site_id"])
                .drop_duplicates()
                .head(500)
                .to_dict(orient="records")
            )
            options["site_id"] = [f"{s['site_id']} - {s.get('site_name', '')}" for s in site_pairs]

        options["year"] = list(visible)
        options["month"] = ["Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec", "Jan", "Feb", "Mar"]
        _FILTER_OPTIONS_CACHE[year] = options
        return {"ok": True, "options": options}
    except Exception as exc:
        logger.exception("Failed to get filter options: %s", exc)
        return {"ok": False, "error": str(exc), "options": {}}


def _prepare_analyzer_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Standardize column names, calculate fields (brand, asp, month, etc.)."""
    out = df.copy()
    for c in out.columns:
        if isinstance(out[c].dtype, pd.CategoricalDtype):
            out[c] = out[c].astype(str)

    # Manager (RM) + store grade / zone / city from Customer Master — needed for manager-wise Pivot.
    out = _apply_manager_cluster_dims(out)

    if "sales" not in out.columns and "net_sale_amount" in out.columns:
        out["sales"] = pd.to_numeric(out["net_sale_amount"], errors="coerce").fillna(0.0)
    elif "sales" not in out.columns and "net_amount" in out.columns:
        out["sales"] = pd.to_numeric(out["net_amount"], errors="coerce").fillna(0.0)
    elif "sales" in out.columns:
        out["sales"] = pd.to_numeric(out["sales"], errors="coerce").fillna(0.0)

    if "quantity" not in out.columns and "qty" in out.columns:
        out["quantity"] = pd.to_numeric(out["qty"], errors="coerce").fillna(0.0)
    elif "quantity" in out.columns:
        out["quantity"] = pd.to_numeric(out["quantity"], errors="coerce").fillna(0.0)

    if "gross_sales" not in out.columns:
        if "mrp" in out.columns and "quantity" in out.columns:
            out["gross_sales"] = pd.to_numeric(out["mrp"], errors="coerce").fillna(0.0) * out["quantity"]
        else:
            out["gross_sales"] = out["sales"]
    else:
        out["gross_sales"] = pd.to_numeric(out["gross_sales"], errors="coerce").fillna(0.0)

    if "discount_amount" not in out.columns:
        out["discount_amount"] = (out["gross_sales"] - out["sales"]).clip(lower=0.0)
    else:
        out["discount_amount"] = pd.to_numeric(out["discount_amount"], errors="coerce").fillna(0.0)

    if "discount_pct" not in out.columns:
        out["discount_pct"] = np.where(out["gross_sales"] > 0, (out["discount_amount"] / out["gross_sales"]) * 100, 0.0)

    # Time dimensions & Indian Financial Year (1 Apr - 31 Mar)
    if "date" in out.columns:
        dt = pd.to_datetime(out["date"], errors="coerce")
        out["year"] = dt.dt.year
        out["month"] = dt.dt.strftime("%b")
        from api.financial_calendar import load_calendar
        mapping = load_calendar().set_index("Date")["week_key"]
        out["fy_week"] = dt.dt.normalize().map(mapping).fillna("Unmapped")
        fy_years = np.where(dt.dt.month >= 4, dt.dt.year + 1, dt.dt.year)
        fy_series = pd.Series(fy_years, index=out.index).fillna(2027).astype(int).astype(str)
        out["financial_year"] = "FY" + fy_series.str[-2:]

    # Channel / Format mapping — prefer Customer Master `channel` + store name over LFS/format codes.
    _CHANNEL_DISPLAY_MAP = {
        "PT": "Pantaloons (PT)",
        "SSL": "Shoppers Stop (SSL)",
        "CENTRO": "Centro",
        "ICONIC": "Iconic",
        "EBO": "EBO",
    }
    # Ensure columns are string (not categorical) before channel assignment
    for col in ("channel", "sales_channel", "store_type", "site_id", "site_name", "site_id_name", "customer_name"):
        if col in out.columns and isinstance(out[col].dtype, pd.CategoricalDtype):
            out[col] = out[col].astype(str)

    # Full label: site_id is often numeric ("269"); SS-/PT- lives in site_name / site_id_name.
    site_str = (
        out["site_id"].astype(str) if "site_id" in out.columns else pd.Series("", index=out.index)
    ).str.strip().str.upper()
    name_str = (
        out["site_name"].astype(str) if "site_name" in out.columns else pd.Series("", index=out.index)
    ).str.strip().str.upper()
    idn_str = (
        out["site_id_name"].astype(str) if "site_id_name" in out.columns else pd.Series("", index=out.index)
    ).str.strip().str.upper()
    cust_str = (
        out["customer_name"].astype(str) if "customer_name" in out.columns else pd.Series("", index=out.index)
    ).str.strip().str.upper()
    comb_str = (site_str + " " + name_str + " " + idn_str + " " + cust_str).str.replace(
        r"\s+", " ", regex=True
    )

    # SHOPERS = common data typo for SHOPPERS. EBO site codes must be whole tokens
    # (\b528\b) — bare "528" wrongly matched SSL store 8528 (substring).
    is_ssl_store = comb_str.str.contains(r"SHOPPERS?|HOPPER|\bSS[- ]|\bSSL\b", regex=True, na=False)
    is_pt_store = comb_str.str.contains(
        r"PANTALOON|\bPT[- ]|\bPW[- ]|\bPT\b|^P\d{2,5}\b|\(P\d{2,5}\)|ADITYA BIRLA",
        regex=True,
        na=False,
    ) & ~is_ssl_store
    is_centro_store = comb_str.str.contains(
        r"CENTRO|\bRRL[- ]|^TC3|^T8|RELIANCE", regex=True, na=False
    ) & ~is_ssl_store & ~is_pt_store
    is_iconic_store = comb_str.str.contains(r"ICONIC", regex=True, na=False) & ~is_ssl_store & ~is_pt_store
    is_ebo_store = (
        comb_str.str.contains(r"EBO|FRESCO|^FGPL|\b(?:528|964|972)\b", regex=True, na=False)
        & ~is_ssl_store
        & ~is_pt_store
    )

    if "sales_channel" not in out.columns:
        out["sales_channel"] = ""
    if "channel" not in out.columns:
        out["channel"] = ""

    # Snapshot Customer Master channel before we rewrite the column.
    cm_ch_orig = out["channel"].fillna("").astype(str).str.strip().str.upper()

    out.loc[is_ssl_store, "sales_channel"] = "Shoppers Stop (SSL)"
    out.loc[is_ssl_store, "channel"] = "SSL"

    out.loc[is_pt_store, "sales_channel"] = "Pantaloons (PT)"
    out.loc[is_pt_store, "channel"] = "PT"

    out.loc[is_centro_store, "sales_channel"] = "Centro"
    out.loc[is_centro_store, "channel"] = "CENTRO"

    out.loc[is_iconic_store, "sales_channel"] = "Iconic"
    out.loc[is_iconic_store, "channel"] = "ICONIC"

    out.loc[is_ebo_store, "sales_channel"] = "EBO"
    out.loc[is_ebo_store, "channel"] = "EBO"

    # Trust clean channel codes only — LFS is a format (large format), NOT Pantaloons.
    _type_to_channel = {
        "SSL": ("Shoppers Stop (SSL)", "SSL"),
        "PT": ("Pantaloons (PT)", "PT"),
        "CENTRO": ("Centro", "CENTRO"),
        "ICONIC": ("Iconic", "ICONIC"),
        "EBO": ("EBO", "EBO"),
    }
    for type_col in ("store_type", "store_type_master"):
        if type_col not in out.columns:
            continue
        type_u = out[type_col].fillna("").astype(str).str.strip().str.upper()
        for code, (disp, ch) in _type_to_channel.items():
            # Never overwrite a name-detected SSL/PT with a conflicting type code.
            hit = type_u.eq(code)
            if code == "PT":
                hit = hit & ~is_ssl_store
            elif code == "SSL":
                hit = hit & ~is_pt_store
            if hit.any():
                out.loc[hit, "sales_channel"] = disp
                out.loc[hit, "channel"] = ch

    # Customer Master `channel` (pre-rewrite) wins when it is a known retail channel.
    for code, disp in _CHANNEL_DISPLAY_MAP.items():
        hit = cm_ch_orig.eq(code)
        if code == "PT":
            hit = hit & ~is_ssl_store
        if hit.any():
            out.loc[hit, "sales_channel"] = disp
            out.loc[hit, "channel"] = code

    # Final: SS-/PT- tokens anywhere in the full store label beat wrong store_type.
    out.loc[is_ssl_store, "sales_channel"] = "Shoppers Stop (SSL)"
    out.loc[is_ssl_store, "channel"] = "SSL"
    out.loc[is_pt_store, "sales_channel"] = "Pantaloons (PT)"
    out.loc[is_pt_store, "channel"] = "PT"

    # Ensure no blank labels remain
    blank_final = out["sales_channel"].astype(str).str.strip().isin({"", "nan", "None"})
    out.loc[blank_final, "sales_channel"] = "Other"

    # Store display label — never concatenate two full "PT-… (P0xx)" labels.
    out = _ensure_clean_site_id_name(out)

    # Location mappings
    if "city_name" not in out.columns:
        out["city_name"] = out["city"] if "city" in out.columns else ""
    if "state_name" not in out.columns:
        out["state_name"] = out["state"] if "state" in out.columns else ""

    # Customer Name mapping
    if "customer_name" not in out.columns:
        if "sales_channel" in out.columns:
            out["customer_name"] = out["sales_channel"]
        elif "site_name" in out.columns:
            out["customer_name"] = out["site_name"]
        else:
            out["customer_name"] = ""
    else:
        # Backfill customer name for Shoppers Stop stores if blank or misattributed
        bad_ssl_cust = (
            out["customer_name"].isna()
            | out["customer_name"].astype(str).str.strip().isin({"", "nan", "None", "Jmdee", "WIP Warehouse", "IBT Warehouse"})
            | out["customer_name"].astype(str).str.contains("Aditya Birla|PANTALOON", case=False, na=False)
        )
        out.loc[is_ssl_store & bad_ssl_cust, "customer_name"] = "Shoppers Stop Ltd"

    # Size mapping
    if "size" not in out.columns:
        if "sku" in out.columns:
            def _extract_size(s: Any) -> str:
                s_str = str(s).strip()
                if not s_str or s_str.lower() in ("nan", "none", ""):
                    return "Free Size"
                parts = s_str.replace("_", "-").split("-")
                if len(parts) >= 2:
                    return parts[-1].strip().upper()
                return s_str
            out["size"] = out["sku"].apply(_extract_size)
        else:
            out["size"] = "Free Size"

    # Zone: infer from state if blank, then label remaining blanks as "Unassigned"
    _STATE_ZONE_MAP = {
        # North
        "delhi": "North", "haryana": "North", "punjab": "North", "rajasthan": "North",
        "uttar pradesh": "North", "uttarakhand": "North", "himachal pradesh": "North",
        "jammu": "North", "jammu and kashmir": "North", "chandigarh": "North",
        "j&k": "North", "ladakh": "North",
        # East
        "west bengal": "East", "bihar": "East", "jharkhand": "East", "odisha": "East",
        "assam": "East", "meghalaya": "East", "tripura": "East", "manipur": "East",
        "nagaland": "East", "arunachal pradesh": "East", "mizoram": "East", "sikkim": "East",
        "orissa": "East",
        # South
        "tamil nadu": "South", "karnataka": "South", "kerala": "South",
        "andhra pradesh": "South", "telangana": "South", "puducherry": "South",
        "pondicherry": "South", "goa": "South",
        # West
        "maharashtra": "West", "gujarat": "West",
        # Central
        "madhya pradesh": "Central", "chhattisgarh": "Central",
    }
    if "zone" not in out.columns:
        out["zone"] = ""
    # Normalise existing zone values
    out["zone"] = out["zone"].astype(str).str.strip().replace({"nan": "", "None": "", "<NA>": ""})
    blank_zone = out["zone"].eq("")
    if blank_zone.any() and "state" in out.columns:
        state_lower = out.loc[blank_zone, "state"].astype(str).str.strip().str.lower()
        inferred = state_lower.map(_STATE_ZONE_MAP).fillna("")
        out.loc[blank_zone & inferred.ne(""), "zone"] = inferred[inferred.ne("")]
    # Any still-blank zones → Unassigned
    out.loc[out["zone"].eq(""), "zone"] = "Unassigned"

    # Store Grade fallback
    if "store_grade" not in out.columns:
        out["store_grade"] = "Standard"

    # Manager / Cluster already applied via _apply_manager_cluster_dims; keep non-empty.
    if "cluster" not in out.columns:
        out["cluster"] = "Unassigned"
    if "manager" not in out.columns:
        out["manager"] = out["cluster"]

    # Store Sqft mapping
    try:
        sqft_map = _get_customer_master_sqft_map()
        site_col = "site_id" if "site_id" in out.columns else "site_code" if "site_code" in out.columns else None
        if site_col:
            site_clean = out[site_col].astype(str).str.strip().str.upper()
            if "store_sqft" in out.columns:
                out["store_sqft"] = pd.to_numeric(out["store_sqft"], errors="coerce")
                mask_miss = out["store_sqft"].isna() | (out["store_sqft"] <= 0)
                out.loc[mask_miss, "store_sqft"] = site_clean[mask_miss].map(sqft_map)
            else:
                out["store_sqft"] = site_clean.map(sqft_map)
            out["store_sqft"] = pd.to_numeric(out["store_sqft"], errors="coerce").fillna(0.0)
        else:
            out["store_sqft"] = 0.0
    except Exception as sq_err:
        logger.warning("Could not map store_sqft in sales_analyzer: %s", sq_err)
        out["store_sqft"] = 0.0

    if "current_soh" not in out.columns:
        out["current_soh"] = 0.0

    if "mdq" not in out.columns:
        out["mdq"] = 0.0

    # Brand fallback — use sku prefix or a master brand name when not enriched
    if "brand" not in out.columns:
        if "sku" in out.columns:
            out["brand"] = out["sku"].astype(str).str.strip().apply(
                lambda s: s.split("-")[0].strip() if s and "-" in s else ("SSI" if s and s.lower() not in ("nan", "none", "") else "Unassigned")
            )
        else:
            out["brand"] = "SSI"
    else:
        blank_brand = out["brand"].astype(str).str.strip().isin({"", "nan", "None", "<NA>"})
        if blank_brand.any() and "sku" in out.columns:
            out.loc[blank_brand, "brand"] = out.loc[blank_brand, "sku"].astype(str).str.strip().apply(
                lambda s: s.split("-")[0].strip() if s and "-" in s else "SSI"
            )

    for c in out.columns:
        if isinstance(out[c].dtype, pd.CategoricalDtype):
            out[c] = out[c].astype(str)

    out = _fill_blank_dims(out)
    # Stash once — query path maps SOH/stores many times on the same keys.
    if "_pivot_sk" in out.columns:
        out = out.drop(columns=["_pivot_sk"])
    out["_pivot_sk"] = _channel_qualified_site_keys(out)
    return out


def _known_number(val: Optional[float]) -> bool:
    if val is None:
        return False
    try:
        return math.isfinite(float(val))
    except (TypeError, ValueError):
        return False


def _metrics_from_soh_stat(stat: Any) -> tuple[float, float, float]:
    """current_soh, dispatch_qty, available_qty from a soh groupby row."""
    if stat is None:
        return 0.0, 0.0, 0.0
    try:
        if isinstance(stat, pd.DataFrame):
            stat = stat.iloc[0]
        soh = float(stat.get("current_soh", 0.0) or 0.0)
        disp = float(stat.get("dispatch_qty", 0.0) or 0.0)
        avail = float(stat.get("available_qty", 0.0) or 0.0)
        return max(0.0, soh), disp, avail
    except Exception:
        return 0.0, 0.0, 0.0


def _compute_measure_values(
    sales_val: float,
    qty_val: float,
    gross_val: float,
    disc_amt: float,
    soh_val: Optional[float],
    mdq_val: Optional[float],
    rec_count: int,
    ly_sales_val: float = 0.0,
    ly_qty_val: float = 0.0,
    active_values: Optional[List[ValueConfig]] = None,
    sqft_val: float = 0.0,
    sqft_sales: Optional[float] = None,
    opening_val: Optional[float] = None,
    receipts_val: Optional[float] = None,
    ly_opening_val: Optional[float] = None,
    ly_receipts_val: Optional[float] = None,
    dispatch_val: Optional[float] = None,
    available_val: Optional[float] = None,
) -> Dict[str, Any]:
    sales_val = float(sales_val) if _known_number(sales_val) else 0.0
    qty_val = float(qty_val) if _known_number(qty_val) else 0.0
    gross_val = float(gross_val) if _known_number(gross_val) else 0.0
    disc_amt = float(disc_amt) if _known_number(disc_amt) else 0.0
    ly_sales_val = float(ly_sales_val) if _known_number(ly_sales_val) else 0.0
    ly_qty_val = float(ly_qty_val) if _known_number(ly_qty_val) else 0.0
    sqft_val = float(sqft_val) if _known_number(sqft_val) else 0.0
    soh_known = _known_number(soh_val)
    mdq_known = _known_number(mdq_val)
    soh_num = float(soh_val) if soh_known else 0.0
    mdq_num = float(mdq_val) if mdq_known else 0.0
    dispatch_num = float(dispatch_val) if _known_number(dispatch_val) else 0.0
    available_num = float(available_val) if _known_number(available_val) else 0.0

    asp_val = round(sales_val / qty_val, 2) if qty_val > 0 else 0.0
    ly_asp_val = round(ly_sales_val / ly_qty_val, 2) if ly_qty_val > 0 else 0.0

    sales_growth = round(((sales_val - ly_sales_val) / ly_sales_val * 100), 1) if ly_sales_val > 0 else None
    qty_growth = round(((qty_val - ly_qty_val) / ly_qty_val * 100), 1) if ly_qty_val > 0 else None
    asp_growth = round(((asp_val - ly_asp_val) / ly_asp_val * 100), 1) if ly_asp_val > 0 else None

    sqft_basis = sales_val if sqft_sales is None else sqft_sales
    sales_per_sqft_val = round(sqft_basis / sqft_val, 2) if sqft_val > 0 else 0.0

    # Historical STR: Daily Movement opening+receipts for the selected period.
    # Prefer period movement (historical_str). Fall back to DM lifetime available
    # (dispatch+IBT+returns on prepared SOH) only when opening/receipts are unavailable.
    # NEVER use Current SOH in the STR denominator (qty/(qty+current_soh) is forbidden).
    st_pct = historical_str(qty_val, opening_val, receipts_val)
    if st_pct is None and available_num > 0:
        st_pct = round((qty_val / available_num) * 100.0, 1)
    elif st_pct is None and dispatch_num > 0:
        st_pct = round((qty_val / dispatch_num) * 100.0, 1)
    if st_pct is not None and st_pct > 100.0:
        st_pct = None  # unmonitored / bad receipts — blank instead of inflated %

    st_ly_pct = historical_str(ly_qty_val, ly_opening_val, ly_receipts_val)
    if st_ly_pct is not None and st_ly_pct > 100.0:
        st_ly_pct = None

    # MDQ is a store target. Leave it blank when this cut cannot hold one MDQ per store.
    if not mdq_known:
        mdq_fill_pct_val = None
        mdq_deficit_val = None
    elif mdq_num > 0:
        mdq_fill_pct_val = min(999.0, max(0.0, round((soh_num / mdq_num * 100), 1)))
        mdq_deficit_val = round(max(0.0, mdq_num - soh_num), 0)
    elif soh_num > 0:
        mdq_fill_pct_val = 100.0
        mdq_deficit_val = 0.0
    else:
        mdq_fill_pct_val = 0.0
        mdq_deficit_val = 0.0

    res: Dict[str, Any] = {}
    vals = active_values or [ValueConfig(field="sales", aggregation="sum")]

    for v in vals:
        f = v.field
        if f == "sales":
            res[f] = round(sales_val, 2)
        elif f == "sales_ly":
            res[f] = round(ly_sales_val, 2)
        elif f == "sales_growth_pct":
            res[f] = sales_growth
        elif f == "dispatch_qty":
            res[f] = round(dispatch_num, 0)
        elif f == "quantity":
            res[f] = round(qty_val, 0)
        elif f == "quantity_ly":
            res[f] = round(ly_qty_val, 0)
        elif f == "qty_growth_pct":
            res[f] = qty_growth
        elif f == "sell_through_pct":
            res[f] = st_pct
        elif f == "sell_through_ly_pct":
            res[f] = st_ly_pct
        elif f == "gross_sales":
            res[f] = round(gross_val, 2)
        elif f == "discount_amount":
            res[f] = round(disc_amt, 2)
        elif f == "discount_pct":
            res[f] = round((disc_amt / gross_val * 100), 1) if gross_val > 0 else 0.0
        elif f == "asp":
            res[f] = asp_val
        elif f == "asp_ly":
            res[f] = ly_asp_val
        elif f == "asp_growth_pct":
            res[f] = asp_growth
        elif f == "sales_per_sqft":
            res[f] = sales_per_sqft_val
        elif f == "store_sqft":
            res[f] = round(sqft_val, 1)
        elif f == "current_soh":
            res[f] = round(soh_num, 0) if soh_known else None
        elif f == "mdq":
            res[f] = round(mdq_num, 0) if mdq_known else None
        elif f == "mdq_fill_pct":
            res[f] = mdq_fill_pct_val
        elif f == "mdq_deficit":
            res[f] = mdq_deficit_val
        else:
            res[f] = 0.0

    return res


_EXPLICIT_TIMELINE_MODES = {"fy_week", "month", "date", "day", "year", "event"}


def _fy_end_from_request(target_year: int, filters: Dict[str, Any]) -> int:
    """FY ending year. FY27 = 1 Apr 2026 – 31 Mar 2027."""
    mode = str((filters or {}).get("timeline_mode") or "").strip().lower()
    raw = str((filters or {}).get("timeline_values") or "")
    if mode in ("", "fy_year", "fy", "financial_year") and raw:
        match = re.search(r"(\d{2,4})", raw.upper().replace(" ", ""))
        if match:
            fy_end = int(match.group(1))
            return fy_end + 2000 if fy_end < 100 else fy_end
    return int(target_year or 2027)


def _fy_compare_window(df: pd.DataFrame, fy_end: int) -> tuple[pd.Timestamp, pd.Timestamp]:
    """
    Date-to-date window for an FY.
    Matches all dates in the requested FY (from 1 Apr through latest sales date).
    """
    start = pd.Timestamp(year=fy_end - 1, month=4, day=1)
    nominal_end = pd.Timestamp(year=fy_end, month=3, day=31)
    if df is not None and not df.empty and "date" in df.columns:
        dates = pd.to_datetime(df["date"], errors="coerce")
        in_window = dates[(dates >= start) & (dates <= nominal_end)]
        if in_window.notna().any():
            return start, pd.Timestamp(in_window.max()).normalize()
    return start, nominal_end


def _clip_dates(frame: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    if frame is None or frame.empty or "date" not in frame.columns:
        return frame
    dates = pd.to_datetime(frame["date"], errors="coerce")
    end_inclusive = pd.Timestamp(end).normalize() + pd.Timedelta(days=1, microseconds=-1)
    return frame.loc[(dates >= pd.Timestamp(start).normalize()) & (dates <= end_inclusive)].copy()


_STORE_GRAIN_CACHE: dict[str, pd.DataFrame] = {}
_STORE_GRAIN_CACHE_MAX = 24


def _store_grain_cache_key(year: int, filters: dict, search: str, kind: str) -> str:
    """Key collapsed store rows by filters, not by which hierarchy fields are selected."""
    import json

    blob = json.dumps(filters or {}, sort_keys=True, default=str)
    return f"v2|{kind}|{int(year)}|{search or ''}|{blob}"


def _aggregate_store_grain(df: pd.DataFrame, dim_cols: List[str]) -> pd.DataFrame:
    """
    Collapse line-level sales to one row per channel|store before store-only pivots.
    255k FY lines → ~500 stores; groupby/tree/SOH joins then stay cheap.
    """
    if df is None or df.empty:
        return df
    # Prefer prepared _pivot_sk — avoids re-running channel|site regex over 250k rows.
    if "_pivot_sk" in df.columns:
        sk = df["_pivot_sk"].astype(str).str.strip()
    else:
        sk = _channel_qualified_site_keys(df).astype(str).str.strip()
    work = df
    # Avoid mutating the prepared in-memory cache frame.
    keep_first = []
    for c in list(dim_cols) + [
        "site_id",
        "site_name",
        "site_id_name",
        "zone",
        "sales_channel",
        "channel",
        "city_name",
        "state_name",
        "store_grade",
        "cluster",
        "manager",
        "customer_name",
        "store_sqft",
        "_pivot_sk",
    ]:
        if c in work.columns and c not in keep_first:
            keep_first.append(c)
    sum_cols = [c for c in ("sales", "quantity", "gross_sales", "discount_amount") if c in work.columns]
    if not sum_cols:
        return df

    use_cols: list[str] = []
    for c in keep_first + sum_cols + (["date"] if "date" in work.columns else []):
        if c not in use_cols:
            use_cols.append(c)
    tmp = work.loc[:, use_cols].copy()
    tmp["_sk"] = sk.values
    tmp = tmp[tmp["_sk"].ne("") & ~tmp["_sk"].str.endswith("|")]
    if tmp.empty:
        return df.iloc[0:0]

    agg: Dict[str, Any] = {c: "sum" for c in sum_cols}
    for c in keep_first:
        if c != "_pivot_sk":
            agg[c] = "first"
    if "date" in tmp.columns:
        agg["date"] = "max"
    out = tmp.groupby("_sk", as_index=False, sort=False).agg(agg)
    out["_pivot_sk"] = out["_sk"]
    return out.drop(columns=["_sk"], errors="ignore")


def _align_ly_store_dims(cy: pd.DataFrame, ly: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Put last-year sales on the current store's zone / channel / name.

    Grouping LY by the raw label missed stores whose name changed year to year.
    Those rupees stayed in the grand total and the store row showed 0.
    Stores that sold last year and not this year are added with zero current sales
    so the grand total equals the sum of the rows.
    """
    if cy is None or ly is None or cy.empty or ly.empty:
        return cy, ly
    if "_pivot_sk" not in cy.columns or "_pivot_sk" not in ly.columns:
        return cy, ly
    cy = cy.copy()
    ly = ly.copy()
    cy_sk = cy["_pivot_sk"].astype(str).str.strip()
    ly_sk = ly["_pivot_sk"].astype(str).str.strip()
    label_cols = [
        c
        for c in (
            "zone",
            "sales_channel",
            "site_id_name",
            "site_id",
            "site_name",
            "cluster",
            "manager",
            "city_name",
            "state_name",
            "store_grade",
            "customer_name",
        )
        if c in cy.columns and c in ly.columns
    ]
    valid_cy = cy_sk.ne("") & ~cy_sk.str.endswith("|")
    if label_cols and bool(valid_cy.any()):
        canon = (
            cy.loc[valid_cy]
            .assign(_sk=cy_sk[valid_cy])
            .drop_duplicates("_sk")
            .set_index("_sk")[label_cols]
        )
        for col in label_cols:
            mapped = ly_sk.map(canon[col])
            hit = mapped.notna() & mapped.astype(str).str.strip().ne("")
            hit &= ~mapped.astype(str).str.lower().isin(_BLANK_DIM_TOKENS)
            if bool(hit.any()):
                ly.loc[hit, col] = mapped[hit].to_numpy()
    valid_ly = ly_sk.ne("") & ~ly_sk.str.endswith("|")
    ly_only = valid_ly & ~ly_sk.isin(set(cy_sk[valid_cy]))
    if bool(ly_only.any()):
        extra = ly.loc[ly_only].drop_duplicates("_pivot_sk").copy()
        for col in ("sales", "quantity", "gross_sales", "discount_amount"):
            if col in extra.columns:
                extra[col] = 0.0
        cy = pd.concat([cy, extra.reindex(columns=list(cy.columns))], ignore_index=True)
    return cy, ly


def warm_analyzer_prepared_frames(year: int = 2027, *, force_refresh: bool = False) -> None:
    """Warm prepared CY/LY sales + SOH so first Pivot Analyzer click is disk/RAM hit."""
    t0 = time.perf_counter()
    try:
        _get_prepared_soh_df(force_refresh=force_refresh)
    except Exception as exc:
        logger.warning("Analyzer SOH warm failed: %s", exc)
    try:
        _get_prepared_sales_df(year, force_refresh=force_refresh)
    except Exception as exc:
        logger.warning("Analyzer sales warm year=%s failed: %s", year, exc)
    try:
        if year and year > 2000:
            _get_prepared_sales_df(year - 1, force_refresh=force_refresh)
    except Exception as exc:
        logger.warning("Analyzer LY sales warm failed: %s", exc)
    logger.info("Analyzer prepared frames warm done in %.2fs", time.perf_counter() - t0)


def _default_analyzer_value_configs() -> list[ValueConfig]:
    return [
        ValueConfig(field="sales", aggregation="sum"),
        ValueConfig(field="sales_ly", aggregation="sum"),
        ValueConfig(field="sales_growth_pct", aggregation="calculated"),
        ValueConfig(field="dispatch_qty", aggregation="sum"),
        ValueConfig(field="quantity", aggregation="sum"),
        ValueConfig(field="current_soh", aggregation="sum"),
        ValueConfig(field="sell_through_pct", aggregation="calculated"),
        ValueConfig(field="asp", aggregation="avg"),
    ]


def warm_default_analyzer_queries() -> dict[str, Any]:
    """Build + publish default + store-hierarchy pivots so hosted first open is a page-cache hit."""
    from api.page_response_cache import _REBUILD
    from api.sales_year_loader import fy_token, get_default_dashboard_year

    t0 = time.perf_counter()
    values = _default_analyzer_value_configs()
    results: dict[str, Any] = {}
    year_val = get_default_dashboard_year()
    fy_label = fy_token(year_val)
    # Cover every common Store (Code - Name) cut the UI can select.
    warm_specs: list[tuple[str, list[str], dict[str, Any]]] = []
    for filt_label, filters in (
        ("empty", {}),
        ("fy", {"timeline_mode": "fy_year", "timeline_values": fy_label}),
    ):
        for rows_label, rows in (
            ("zone_channel", ["zone", "sales_channel"]),
            ("by_store", ["site_id_name"]),
            ("zone_store", ["zone", "site_id_name"]),
            ("zone_channel_store", ["zone", "sales_channel", "site_id_name"]),
            (
                "zone_channel_store_manager",
                ["zone", "sales_channel", "site_id_name", "cluster"],
            ),
            ("store_code", ["site_id"]),
            ("zone_store_code", ["zone", "site_id"]),
        ):
            warm_specs.append((f"{rows_label}_{filt_label}", rows, filters))

    token = _REBUILD.set(True)
    try:
        for label, rows, filters in warm_specs:
            try:
                payload = execute_analyzer_query(
                    AnalyzerQueryRequest(
                        rows=rows,
                        columns=[],
                        values=values,
                        filters=filters,
                        sort=[SortConfig(field="sales", direction="desc")],
                        year=year_val,
                    )
                )
                gt = ((payload or {}).get("grand_total") or {}).get("metrics") or {}
                results[label] = {
                    "ok": bool((payload or {}).get("ok")),
                    "sales": gt.get("sales"),
                    "soh": gt.get("current_soh"),
                    "dispatch": gt.get("dispatch_qty"),
                    "rows": int((payload or {}).get("total_records") or 0),
                }
            except Exception as exc:
                results[label] = {"ok": False, "error": str(exc)}
                logger.warning("Default analyzer warm %s failed: %s", label, exc)
    finally:
        _REBUILD.reset(token)
    logger.info(
        "Default analyzer query warm done in %.2fs results=%s",
        time.perf_counter() - t0,
        results,
    )
    return {"ok": all(bool(v.get("ok")) for v in results.values()), "results": results}


def refresh_analyzer_prepared_caches(
    *,
    force: bool = True,
    years: tuple[int, ...] | list[int] | None = None,
    publish: bool = True,
) -> dict[str, Any]:
    """Daily pipeline: rebuild prepared Pivot frames from latest masters and publish to blob.

    Hosted App Service then picks these parquet/key files from blob on query (fast path).
    """
    global _PREPARED_SOH_CACHE, _PREPARED_SOH_SIG, _PREPARED_SOH_TIME
    from api.sales_year_loader import analyzer_build_years

    if years is None:
        years = tuple(analyzer_build_years())

    t0 = time.perf_counter()
    _PREPARED_DF_CACHE.clear()
    _STORE_GRAIN_CACHE.clear()
    _PREPARED_SOURCE_VERSIONS.clear()
    _PREPARED_BLOB_CHECK_AT.clear()
    _FILTER_OPTIONS_CACHE.clear()
    _QUERY_RESULT_CACHE.clear()
    _PREPARED_SOH_CACHE = None
    _PREPARED_SOH_SIG = None
    _PREPARED_SOH_TIME = 0.0

    try:
        from api import sales_year_loader

        sales_year_loader._YEAR_CACHE.clear()
    except Exception:
        pass

    try:
        from api.current_soh import ensure_daily_movement_from_blob

        ensure_daily_movement_from_blob(force=bool(force))
    except Exception as dm_exc:
        logger.warning("Analyzer cache: daily movement blob pull failed: %s", dm_exc)

    built: dict[str, Any] = {"years": {}, "soh": None}
    for y in years:
        try:
            df = _get_prepared_sales_df(int(y), force_refresh=bool(force))
            rows_n = int(len(df)) if df is not None else 0
            if rows_n == 0:
                # Empty prior FY (e.g. FY24-style) — skip silently; do not fail overall ok.
                built["years"][str(y)] = {"ok": True, "rows": 0, "skipped_empty": True}
                logger.info("Analyzer cache year=%s skipped (0 rows)", y)
            else:
                built["years"][str(y)] = {"ok": True, "rows": rows_n}
        except Exception as y_exc:
            built["years"][str(y)] = {"ok": False, "error": str(y_exc)}
            logger.warning("Analyzer cache year=%s failed: %s", y, y_exc)

    try:
        soh = _get_prepared_soh_df(force_refresh=bool(force))
        built["soh"] = {
            "ok": bool(soh is not None and not soh.empty),
            "rows": int(len(soh)) if soh is not None else 0,
        }
    except Exception as soh_exc:
        built["soh"] = {"ok": False, "error": str(soh_exc)}
        logger.warning("Analyzer cache SOH failed: %s", soh_exc)

    pub: dict[str, Any] = {"ok": False, "skipped": True}
    if publish:
        try:
            pub = publish_prepared_analyzer_caches_to_blob()
        except Exception as pub_exc:
            pub = {"ok": False, "error": str(pub_exc)}
            logger.warning("Analyzer cache blob publish failed: %s", pub_exc)

    # Empty skipped years do not poison overall analyzer_cache ok.
    year_ok = True
    for v in (built.get("years") or {}).values():
        if v.get("skipped_empty"):
            continue
        if not bool(v.get("ok")):
            year_ok = False
            break
    if not built.get("years"):
        year_ok = False
    soh_ok = bool((built.get("soh") or {}).get("ok"))
    ok = year_ok and soh_ok and bool(pub.get("ok", not publish))
    detail = (
        f"years={list(built['years'])} soh_rows={(built.get('soh') or {}).get('rows')} "
        f"blob_uploaded={len(pub.get('uploaded') or [])}"
    )
    logger.info(
        "Analyzer prepared caches refresh ok=%s in %.1fs (%s)",
        ok,
        time.perf_counter() - t0,
        detail,
    )
    return {
        "ok": ok,
        "seconds": round(time.perf_counter() - t0, 1),
        "detail": detail,
        "built": built,
        "publish": pub,
    }


def _load_prepared_sales_for_query(
    *,
    target_year: int,
    timeline_mode: str | None,
    timeline_values: str | None,
) -> tuple[pd.DataFrame, bool]:
    """Prefer morning blob/disk prepared frames; fall back to cold timeline load + prepare.

    Returns (frame, source_ok). source_ok=True when a prepared/cold frame was loaded —
    even if timeline filters emptied it. source_ok=False means a true cache miss
    (Azure should 503-warm; local may cold-build).
    """
    mode = (timeline_mode or "").strip().lower()
    vals = (timeline_values or "").strip()

    from api.routes.sales_dashboard import (
        _apply_timeline_filter,
        _fy_years_for_timeline,
        _load_sales_for_timeline,
    )

    year_val = int(target_year) if target_year else 2027
    try:
        fy_years = _fy_years_for_timeline(
            year_val=year_val,
            timeline_mode=mode or None,
            timeline_values=vals or None,
        )
    except Exception:
        fy_years = [year_val]
    if not fy_years:
        fy_years = [year_val]

    frames: list[pd.DataFrame] = []
    for y in sorted({int(x) for x in fy_years}):
        try:
            part = _get_prepared_sales_df(y)
            if part is not None and not part.empty:
                frames.append(part)
        except Exception as exc:
            logger.warning("Prepared sales for query year=%s failed: %s", y, exc)

    if frames:
        raw = frames[0] if len(frames) == 1 else pd.concat(frames, ignore_index=True)
        if mode and vals:
            raw = _apply_timeline_filter(raw, timeline_mode=mode, timeline_values=vals)
        return raw, True

    # Hosted: never cold-load Sales-Master on the query path (worker dies → 502).
    if _on_azure():
        logger.warning(
            "Prepared analyzer sales miss on Azure — refusing cold timeline load year=%s mode=%s",
            year_val,
            mode,
        )
        return pd.DataFrame(), False

    # Fallback: cold path (blob sales master → prepare) — local/dev only
    logger.warning("Prepared analyzer sales miss — cold timeline load year=%s mode=%s", year_val, mode)
    raw = _load_sales_for_timeline(
        year_val=year_val,
        timeline_mode=mode or None,
        timeline_values=vals or None,
    )
    if mode and vals:
        raw = _apply_timeline_filter(raw, timeline_mode=mode, timeline_values=vals)
    prepared = _prepare_analyzer_dataframe(raw)
    return prepared, not (prepared is None or prepared.empty)


@router.post("/query")
def execute_analyzer_query(req: AnalyzerQueryRequest):
    """
    Execute high-speed dynamic multidimensional pivot query.
    Uses vectorized Pandas GroupBy aggregation + multi-index lookups (10ms - 30ms latency).
    """
    global _DUCKDB_FALLBACK_COUNT
    _t_query = time.perf_counter()
    _rss0 = _note_peak_rss()
    request_id = hashlib.sha1(f"{time.time_ns()}:{id(req)}".encode("utf-8")).hexdigest()[:12]
    timing: dict[str, Any] = {
        "request_id": request_id,
        "memory_cache_lookup_ms": 0.0,
        "page_memory_lookup_ms": 0.0,
        "local_disk_lookup_ms": 0.0,
        "blob_lookup_ms": 0.0,
        "blob_meta_ms": 0.0,
        "cache_lookup_ms": 0.0,
        "prepared_ram_lookup_ms": 0.0,
        "prepared_parquet_read_ms": 0.0,
        "prepared_blob_sync_ms": 0.0,
        "parquet_access_ms": 0.0,
        "duckdb_query_ms": 0.0,
        "semaphore_wait_ms": 0.0,
        "conn_acquire_ms": 0.0,
        "grouping_sets_ms": 0.0,
        "column_split_ms": 0.0,
        "ly_calc_ms": 0.0,
        "soh_align_ms": 0.0,
        "tree_build_ms": 0.0,
        "pandas_fallback_ms": 0.0,
        "pivot_aggregation_ms": 0.0,
        "json_serialization_ms": 0.0,
        "total_api_ms": 0.0,
        "engine": "unknown",
        "cache_source": None,
        "duckdb_fallback": False,
        "duckdb_fallback_reason": None,
        "duckdb_fallback_count": _DUCKDB_FALLBACK_COUNT,
        "rss_mb_start": _rss0,
        "rss_mb": _rss0,
        "peak_rss_mb": _ANALYZER_PEAK_RSS_MB,
    }

    def _finish(payload: dict, *, engine: str) -> dict:
        timing["engine"] = engine
        timing["total_api_ms"] = _ms_since(_t_query)
        timing["rss_mb"] = _note_peak_rss()
        timing["peak_rss_mb"] = _ANALYZER_PEAK_RSS_MB
        timing["duckdb_fallback_count"] = _DUCKDB_FALLBACK_COUNT
        out = dict(payload or {})
        out["timing"] = dict(timing)
        _log_analyzer_query_timing(timing, filters=dict(req.filters or {}))
        return out

    # 1. Whitelist validation
    for r in req.rows:
        if r not in ALLOWED_DIMENSION_KEYS:
            raise HTTPException(status_code=400, detail=f"Invalid row dimension: '{r}'")

    for c in req.columns:
        if c not in ALLOWED_DIMENSION_KEYS:
            raise HTTPException(status_code=400, detail=f"Invalid column dimension: '{c}'")

    for val in req.values:
        if val.field not in ALLOWED_MEASURE_KEYS:
            raise HTTPException(status_code=400, detail=f"Invalid measure: '{val.field}'")
        if val.aggregation and val.aggregation not in ALLOWED_AGGREGATIONS:
            raise HTTPException(status_code=400, detail=f"Invalid aggregation: '{val.aggregation}'")

    # Cache key must not download Sales-Master. A blob miss used to raise 503 and
    # Azure then returned an empty 503 while the worker was still syncing.
    # Use CALENDAR_VERSION constant — calendar_options() calls get_max_sales_date()
    # and was loading all year files before the page-cache lookup.
    from api.financial_calendar import CALENDAR_VERSION
    from api.sales_year_loader import (
        analyzer_visible_years,
        get_default_dashboard_year,
        is_all_years,
    )

    # Option B: Analyzer UI only supports previous + current FY. Out-of-scope /
    # All Years (0) → fall back to current FY (no 500).
    cur_fy = int(get_default_dashboard_year())
    visible = set(analyzer_visible_years())
    requested_year_raw = req.year
    try:
        requested_year = (
            int(requested_year_raw) if requested_year_raw is not None else cur_fy
        )
    except (TypeError, ValueError):
        requested_year = cur_fy
    if is_all_years(requested_year_raw) or requested_year not in visible:
        effective_year = cur_fy
    else:
        effective_year = requested_year
    year_fallback = int(effective_year) != int(requested_year) or is_all_years(
        requested_year_raw
    )

    req_for_cache = req.model_copy(update={"year": int(effective_year)})
    # v18: Azure-safe LY load (never OOM/503 the CY pivot when prior-year frame is heavy).
    # v19: Analyzer visible-FY scope + year fallback fields.
    # v20: LY follows the store key, so grand total matches the sum of store rows.
    cache_key = f"v20:{CALENDAR_VERSION}:{req_for_cache.model_dump_json()}"
    _t_cache = time.perf_counter()
    _t_mem = time.perf_counter()
    if cache_key in _QUERY_RESULT_CACHE:
        cached = _QUERY_RESULT_CACHE[cache_key]
        timing["memory_cache_lookup_ms"] = _ms_since(_t_mem)
        timing["cache_lookup_ms"] = _ms_since(_t_cache)
        timing["cache_source"] = "query_memory"
        if isinstance(cached, dict):
            cached = {
                **cached,
                "requested_year": requested_year,
                "effective_year": effective_year,
                "year_fallback": year_fallback,
            }
        return _finish(cached, engine="memory_cache")
    timing["memory_cache_lookup_ms"] = _ms_since(_t_mem)
    from api.page_response_cache import load_page_cache, rebuilding_page_cache, save_page_cache

    def _soh_cache_looks_empty(payload: dict) -> bool:
        """True when sales exist but SOH/dispatch measures are blank (stale warm)."""
        want = {v.field for v in req.values}
        if not want.intersection({"current_soh", "dispatch_qty", "sell_through_pct"}):
            return False
        metrics = ((payload or {}).get("grand_total") or {}).get("metrics") or {}
        sales = metrics.get("sales")
        try:
            sales_n = float(sales or 0)
        except (TypeError, ValueError):
            sales_n = 0.0
        if sales_n <= 0:
            return False
        soh = metrics.get("current_soh")
        disp = metrics.get("dispatch_qty")
        # UI shows "—" for null SOH; hosted stale warm had sales OK + SOH null.
        # Numeric 0 is valid (empty stock) — do not reject / skip page-cache save.
        if "current_soh" in want and soh in (None, ""):
            return True
        if (
            "dispatch_qty" in want
            and "current_soh" in want
            and soh in (None, "")
            and disp in (None, "")
        ):
            return True
        # Reject dual-label store caches from before site_id_name cleanup.
        if "site_id_name" in set(req.rows or []):
            try:
                rows = (payload or {}).get("rows") or []
                # Spot-check first zone → channel → stores for "A (P0) - B (P0)" labels.
                for z in rows[:3]:
                    for ch in (z.get("children") or [])[:2]:
                        for leaf in (ch.get("children") or [])[:8]:
                            lab = str(leaf.get("value") or "")
                            if re.search(r"\)\s*-\s*.+\(", lab):
                                return True
            except Exception:
                pass
        return False

    def _with_year_meta(payload: dict) -> dict:
        out = dict(payload or {})
        out["requested_year"] = requested_year
        out["effective_year"] = effective_year
        out["year_fallback"] = year_fallback
        return out

    if not rebuilding_page_cache():
        page_timing: dict[str, float] = {}
        disk_query = load_page_cache("sales-analyzer", cache_key, timing_out=page_timing)
        timing["page_memory_lookup_ms"] = float(page_timing.get("page_memory_lookup_ms") or 0.0)
        timing["local_disk_lookup_ms"] = float(page_timing.get("local_disk_lookup_ms") or 0.0)
        timing["blob_lookup_ms"] = float(page_timing.get("blob_lookup_ms") or 0.0)
        timing["blob_meta_ms"] = float(page_timing.get("blob_meta_ms") or 0.0)
        timing["cache_lookup_ms"] = _ms_since(_t_cache)
        if disk_query is not None and not _soh_cache_looks_empty(disk_query):
            disk_query = _with_year_meta(disk_query)
            _QUERY_RESULT_CACHE[cache_key] = disk_query
            if timing["page_memory_lookup_ms"] > 0 and timing["local_disk_lookup_ms"] == 0 and timing["blob_lookup_ms"] == 0:
                timing["cache_source"] = "page_memory"
            elif timing["blob_lookup_ms"] > 0:
                timing["cache_source"] = "page_blob"
            else:
                timing["cache_source"] = "page_disk"
            return _finish(disk_query, engine="page_cache")
        if disk_query is not None and _soh_cache_looks_empty(disk_query):
            logger.warning("Ignoring stale analyzer page_cache with empty SOH/dispatch")
    else:
        timing["cache_lookup_ms"] = _ms_since(_t_cache)

    active_rows = req.rows if req.rows else ["zone"]
    active_cols = req.columns
    active_values = req.values if req.values else [ValueConfig(field="sales", aggregation="sum")]
    target_year = int(effective_year)
    timing["year"] = target_year

    # Opt-in DuckDB pivot path (ANALYZER_QUERY_ENGINE / ANALYTICS_QUERY_ENGINE=duckdb).
    # Falls back to Pandas only for classified failures — not for valid empty pivots.
    duckdb_attempted = False

    def _mark_duckdb_fallback(reason: str, exc: BaseException | None = None) -> None:
        global _DUCKDB_FALLBACK_COUNT
        _DUCKDB_FALLBACK_COUNT += 1
        timing["duckdb_fallback"] = True
        timing["duckdb_fallback_reason"] = reason
        timing["duckdb_fallback_count"] = _DUCKDB_FALLBACK_COUNT
        if exc is not None:
            logger.warning(
                "Analyzer DuckDB fallback reason=%s request_id=%s count=%s err=%s",
                reason,
                request_id,
                _DUCKDB_FALLBACK_COUNT,
                exc,
            )
        else:
            logger.warning(
                "Analyzer DuckDB fallback reason=%s request_id=%s count=%s",
                reason,
                request_id,
                _DUCKDB_FALLBACK_COUNT,
            )

    try:
        from api.services.analytics_query_engine import DuckDBSemaphoreTimeout as _DuckSemTimeout
        from api.services.sales_pivot_query_engine import (
            _prepared_sales_path,
            try_execute_pivot_duckdb,
            use_analyzer_duckdb,
        )
    except Exception as import_exc:
        logger.warning("Analyzer DuckDB imports unavailable: %s", import_exc)
        _DuckSemTimeout = TimeoutError  # type: ignore[misc, assignment]
        use_analyzer_duckdb = lambda: False  # type: ignore[assignment]
        try_execute_pivot_duckdb = None  # type: ignore[assignment]
        _prepared_sales_path = lambda _y: None  # type: ignore[assignment]

    # Azure SQL pivot only when dimension filters applied.
    # Default FY timeline alone is NOT a filter — that must stay on page-cache / DuckDB.
    _analyzer_filters = dict(req.filters or {})
    _analyzer_timeline_keys = {
        "timeline_mode",
        "timeline_values",
        "from_date",
        "to_date",
        "date_range",
        "financial_year",
        "year",
    }
    def _filter_value_set(v: Any) -> bool:
        items = v if isinstance(v, (list, tuple, set)) else [v]
        return any(
            x is not None and str(x).strip() and str(x).strip().lower() not in {"all", "*", "none"}
            for x in items
        )

    _analyzer_has_filters = any(
        _filter_value_set(v)
        for k, v in _analyzer_filters.items()
        if k not in _analyzer_timeline_keys
    )
    if _analyzer_has_filters and _analyzer_sql_enabled():
        try:
            from api.azure_sql_analytics import sql_query_ready, try_execute_pivot_azure_sql

            if sql_query_ready():
                _t_sql = time.perf_counter()
                sql_payload = try_execute_pivot_azure_sql(
                    rows=list(active_rows),
                    columns=list(active_cols or []),
                    value_fields=[v.field for v in active_values],
                    filters=_analyzer_filters,
                    year=int(target_year),
                )
                timing["duckdb_query_ms"] = _ms_since(_t_sql)
                if isinstance(sql_payload, dict) and sql_payload.get("ok"):
                    sql_payload = _with_year_meta(sql_payload)
                    if len(_QUERY_RESULT_CACHE) > _CACHE_MAX_ENTRIES:
                        _QUERY_RESULT_CACHE.clear()
                    _QUERY_RESULT_CACHE[cache_key] = sql_payload
                    timing["pivot_aggregation_ms"] = _ms_since(_t_sql)
                    return _finish(sql_payload, engine="azure_sql")
        except Exception as sql_exc:
            logger.warning("Analyzer Azure SQL pivot failed; DuckDB/pandas fallback: %s", sql_exc)

    try:
        if use_analyzer_duckdb():
            duckdb_attempted = True
            _t_duck = time.perf_counter()
            # Parquet open/scan is inside DuckDB read_parquet — attribute file access
            # to the same window (diag.query_ms is also returned when available).
            _t_pq = time.perf_counter()
            duck_payload = try_execute_pivot_duckdb(
                rows=list(active_rows),
                columns=list(active_cols or []),
                value_fields=[v.field for v in active_values],
                filters=dict(req.filters or {}),
                year=int(target_year),
            )
            timing["duckdb_query_ms"] = _ms_since(_t_duck)
            if isinstance(duck_payload, dict) and duck_payload.get("ok"):
                diag = duck_payload.get("engine_diag") or {}
                if isinstance(diag, dict):
                    for k in (
                        "semaphore_wait_ms",
                        "conn_acquire_ms",
                        "grouping_sets_ms",
                        "column_split_ms",
                        "ly_calc_ms",
                        "soh_align_ms",
                        "tree_build_ms",
                    ):
                        if diag.get(k) is not None:
                            timing[k] = float(diag.get(k) or 0.0)
                    if diag.get("query_ms") is not None:
                        timing["parquet_access_ms"] = float(diag.get("query_ms") or 0.0)
                        timing["pivot_aggregation_ms"] = max(
                            0.0,
                            round(timing["duckdb_query_ms"] - timing["parquet_access_ms"], 1),
                        )
                    else:
                        timing["parquet_access_ms"] = _ms_since(_t_pq)
                else:
                    timing["parquet_access_ms"] = _ms_since(_t_pq)
                _t_json = time.perf_counter()
                duck_payload = _with_year_meta(duck_payload)
                if len(_QUERY_RESULT_CACHE) > _CACHE_MAX_ENTRIES:
                    _QUERY_RESULT_CACHE.clear()
                _QUERY_RESULT_CACHE[cache_key] = duck_payload
                try:
                    save_page_cache("sales-analyzer", cache_key, duck_payload)
                except Exception:
                    pass
                timing["json_serialization_ms"] = _ms_since(_t_json)
                timing["cache_source"] = "compute_duckdb"
                return _finish(duck_payload, engine="duckdb")
            if duck_payload is None:
                if _prepared_sales_path(int(target_year)) is None:
                    _mark_duckdb_fallback("prepared_parquet_missing")
                else:
                    _mark_duckdb_fallback("duckdb_returned_none")
            else:
                _mark_duckdb_fallback("duckdb_not_ok")
    except Exception as duck_exc:
        if not duckdb_attempted:
            pass
        elif isinstance(duck_exc, _DuckSemTimeout):
            _mark_duckdb_fallback("semaphore_timeout", duck_exc)
        elif isinstance(duck_exc, ValueError):
            _mark_duckdb_fallback("unsupported_dimensions", duck_exc)
        else:
            name = type(duck_exc).__name__
            msg = str(duck_exc).lower()
            if "resourceinuse" in name.lower() or "resourceinuse" in msg:
                _mark_duckdb_fallback("connection_in_use", duck_exc)
            elif "column" in msg and ("not found" in msg or "does not exist" in msg):
                _mark_duckdb_fallback("missing_required_columns", duck_exc)
            elif "connection" in msg or "connect" in msg:
                _mark_duckdb_fallback("connection_error", duck_exc)
            else:
                _mark_duckdb_fallback("sql_execution_error", duck_exc)

    # Only pull DM from blob when prepared SOH is missing — never stall Pivot
    # behind a blob HEAD/download when local analyzer caches already exist.
    try:
        if not _PREPARED_SOH_PARQUET.exists() or _PREPARED_SOH_PARQUET.stat().st_size <= 10_000:
            from api.current_soh import ensure_daily_movement_from_blob

            ensure_daily_movement_from_blob()
    except Exception:
        pass
    # Map target_year to financial year label: 2027 -> FY27, 2026 -> FY26, etc.
    # FY27 = Apr 2026 - Mar 2027 (FY ending year). 0 means All Years.
    _fy_label = f"FY{str(target_year)[-2:]}" if target_year != 0 else None

    _t_pandas = time.perf_counter()
    try:
        # 2. Load prepared Pivot frame (morning pipeline → blob → disk/RAM).
        # Prefer prepared parquet for timeline filters too — avoids full Sales-Master prepare.
        # On RAM miss, prefer column-pruned disk read for this query only (do not poison
        # _PREPARED_DF_CACHE with a partial schema).
        source_ok = True
        _t_parquet = time.perf_counter()
        prune_cols = _prepared_sales_columns_for_query(
            rows=list(active_rows),
            columns=list(active_cols or []),
            filters=dict(req.filters or {}),
            value_fields=[v.field for v in active_values],
        )
        raw_df = None
        if target_year in _PREPARED_DF_CACHE and not _PREPARED_DF_CACHE[target_year].empty:
            t_ram = time.perf_counter()
            raw_df = _PREPARED_DF_CACHE[target_year]
            timing["prepared_ram_lookup_ms"] = _ms_since(t_ram)
            source_ok = True
            # Timeline still applied inside _load_prepared_sales_for_query semantics below.
            if req.filters.get("timeline_mode"):
                from api.routes.sales_dashboard import _apply_timeline_filter

                mode = str(req.filters["timeline_mode"])
                vals = str(req.filters.get("timeline_values", ""))
                raw_df = _apply_timeline_filter(raw_df, timeline_mode=mode, timeline_values=vals)
        elif req.filters.get("timeline_mode"):
            mode = str(req.filters["timeline_mode"])
            vals = str(req.filters.get("timeline_values", ""))
            raw_df, source_ok = _load_prepared_sales_for_query(
                target_year=target_year,
                timeline_mode=mode,
                timeline_values=vals,
            )
        else:
            candidate = _find_prepared_sales_parquet(int(target_year))
            if candidate is not None:
                t_pr = time.perf_counter()
                pruned = _read_prepared_sales_parquet(candidate, columns=prune_cols)
                timing["prepared_parquet_read_ms"] = _ms_since(t_pr)
                if pruned is not None and not pruned.empty:
                    raw_df = pruned
                    source_ok = True
            if raw_df is None or raw_df.empty:
                raw_df = _get_prepared_sales_df(target_year)
                source_ok = raw_df is not None and not raw_df.empty
            if not source_ok and _on_azure():
                try:
                    raw_df = _get_prepared_sales_df(target_year, force_refresh=False)
                    if raw_df is None or raw_df.empty:
                        direct = _read_prepared_sales_directly_from_blob(int(target_year))
                        if direct is not None and not direct.empty:
                            raw_df = direct
                    source_ok = raw_df is not None and not raw_df.empty
                except Exception as retry_exc:
                    logger.warning("Analyzer prepared sales Azure retry failed: %s", retry_exc)
        timing["parquet_access_ms"] = _ms_since(_t_parquet)
        _t_pivot = time.perf_counter()
        if raw_df is None or raw_df.empty:
            # True cache miss on Azure → 503. Timeline/FY filter that emptied a loaded
            # frame must return empty success (not "warming").
            if _on_azure() and not source_ok:
                # Kick background warm so the client's longer retry can hit disk/RAM.
                try:
                    import threading

                    y = int(target_year)

                    def _bg_warm() -> None:
                        try:
                            warm_analyzer_prepared_frames(y, force_refresh=False)
                        except Exception as warm_exc:
                            logger.warning("Background analyzer warm failed: %s", warm_exc)

                    threading.Thread(target=_bg_warm, daemon=True, name="analyzer-warm").start()
                except Exception:
                    pass
                raise HTTPException(
                    status_code=503,
                    detail="Sales Analyzer cache is warming. Please retry in a minute.",
                )
            timing["pivot_aggregation_ms"] = _ms_since(_t_pivot)
            if duckdb_attempted or timing.get("duckdb_fallback"):
                timing["pandas_fallback_ms"] = _ms_since(_t_pandas)
            return _finish(
                _with_year_meta(
                    {"ok": True, "rows": [], "columns": [], "grand_total": {}, "total_records": 0}
                ),
                engine="pandas",
            )

        df = raw_df

        # ── DEFAULT FY FILTER ─────────────────────────────────────────────────
        # Apply financial year filter automatically unless user has already
        # specified a financial_year or date filter explicitly, or selected All Years (target_year=0),
        # or included financial_year / year in active rows or columns.
        _user_has_fy_filter = (
            bool(req.filters and (
                "timeline_mode" in req.filters
                or "financial_year" in req.filters
                or "year" in req.filters
                or any(k in ("date", "date_range") for k in req.filters)
            ))
            or ("financial_year" in active_rows or "financial_year" in active_cols)
            or ("year" in active_rows or "year" in active_cols)
        )
        if not _user_has_fy_filter and target_year != 0 and _fy_label and "financial_year" in df.columns:
            _fy_mask = df["financial_year"].astype(str).str.strip().eq(_fy_label)
            if _fy_mask.any():
                df = df[_fy_mask]
            else:
                # Fallback: filter by fy column (format: "FY 2026-27")
                _fy_col_str = f"FY {str(target_year - 1)}-{str(target_year)[-2:]}"
                if "fy" in df.columns:
                    df = df[df["fy"].astype(str).str.strip().eq(_fy_col_str)]
        # ─────────────────────────────────────────────────────────────────────

        # Current SOH: only when measures need it (store-only sales pivots stay fast).
        _soh_fields = {
            "current_soh",
            "dispatch_qty",
            "available_qty",
            "sell_through_pct",
            "sell_through_ly_pct",
            "mdq",
        }
        _needs_soh = any(v.field in _soh_fields for v in active_values)
        # Detect store-only early so we skip barcode×product SOH alignment (huge on Azure).
        _early_pivot_dims = list(active_rows) + list(active_cols)
        _early_store_only = bool(_early_pivot_dims) and all(
            c in _STORE_LEVEL_DIMS for c in _early_pivot_dims
        )
        if _needs_soh:
            soh_df = _get_prepared_soh_df()
            if not _early_store_only:
                soh_df = _align_soh_product_dims(soh_df, df)
                soh_df = _fill_blank_dims(soh_df)
            elif soh_df is not None and not soh_df.empty:
                # Store cuts only need channel|site keys — light fill on store label cols.
                _soh_fill = {}
                for _col in (
                    "zone",
                    "sales_channel",
                    "site_id_name",
                    "site_id",
                    "site_name",
                    "manager",
                    "cluster",
                ):
                    if _col in soh_df.columns:
                        _s = soh_df[_col].fillna("").astype(str).str.strip()
                        _soh_fill[_col] = _s.mask(
                            _s.str.lower().isin(_BLANK_DIM_TOKENS), "Unassigned"
                        )
                if _soh_fill:
                    soh_df = soh_df.assign(**_soh_fill)
        else:
            soh_df = pd.DataFrame()

        # Apply Filters efficiently to CY sales and SOH
        if req.filters:
            for field, fval in req.filters.items():
                if field not in ALLOWED_DIMENSION_KEYS and field not in ("search", "from_date", "to_date", "date_range"):
                    continue
                if fval is None or fval == "" or fval == []:
                    continue
                
                # Date range filter handling: ["2026-05-01", "2026-06-30"] or [start, end]
                if field in ("date", "date_range") and isinstance(fval, list) and len(fval) == 2 and "date" in df.columns:
                    try:
                        d_start = pd.to_datetime(fval[0], errors="coerce")
                        d_end = pd.to_datetime(fval[1], errors="coerce")
                        if pd.notna(d_start) and pd.notna(d_end):
                            d_end_inclusive = d_end + pd.Timedelta(days=1, microseconds=-1)
                            df = df[(df["date"] >= d_start) & (df["date"] <= d_end_inclusive)]
                            continue
                    except Exception:
                        pass

                if isinstance(fval, list):
                    clean_vals = [str(x).strip().lower() for x in fval if str(x).strip()]
                    if clean_vals:
                        if field in df.columns:
                            df = _apply_dimension_list_filter(df, field, clean_vals)
                        if soh_df is not None and not soh_df.empty and field in soh_df.columns:
                            soh_df = _apply_dimension_list_filter(soh_df, field, clean_vals)
                elif isinstance(fval, (str, int, float)):
                    if field == "sales_channel":
                        clean_one = _expand_channel_filter_vals([str(fval).strip().lower()])
                        if field in df.columns:
                            df = _apply_dimension_list_filter(df, field, clean_one)
                        if soh_df is not None and not soh_df.empty and field in soh_df.columns:
                            soh_df = _apply_dimension_list_filter(soh_df, field, clean_one)
                    else:
                        if field in df.columns:
                            df = df[df[field].astype(str).str.strip().str.lower() == str(fval).strip().lower()]
                        if soh_df is not None and not soh_df.empty and field in soh_df.columns:
                            soh_df = soh_df[soh_df[field].astype(str).str.strip().str.lower() == str(fval).strip().lower()]

        # Search filter
        if req.search and req.search.strip():
            sq = req.search.strip().lower()
            mask = pd.Series(False, index=df.index)
            for r_field in active_rows + active_cols:
                if r_field in df.columns:
                    mask = mask | df[r_field].astype(str).str.lower().str.contains(sq, na=False, regex=False)
            df = df[mask]

            if soh_df is not None and not soh_df.empty:
                soh_mask = pd.Series(False, index=soh_df.index)
                for r_field in active_rows + active_cols:
                    if r_field in soh_df.columns:
                        soh_mask = soh_mask | soh_df[r_field].astype(str).str.lower().str.contains(sq, na=False, regex=False)
                soh_df = soh_df[soh_mask]

        # Default FY-27 (and any FY year) is date-to-date through the latest
        # sales date we actually have — not through 31 Mar of a still-open year.
        timeline_mode = str((req.filters or {}).get("timeline_mode") or "").strip().lower()
        compare_window = None
        if "date" in df.columns and timeline_mode not in _EXPLICIT_TIMELINE_MODES:
            fy_end = _fy_end_from_request(target_year, req.filters or {})
            cy_start, cy_end = _fy_compare_window(df, fy_end)
            df = _clip_dates(df, cy_start, cy_end)
            compare_window = (cy_start, cy_end, fy_end)
            if df.empty:
                return {"ok": True, "rows": [], "columns": [], "grand_total": {}, "total_records": 0}

        # Collapse duplicate store labels (e.g. "166 - …" vs "SS-RAIPUR-166 - …").
        # Store-only pivots: collapse 255k lines → ~500 stores BEFORE canonicalize.
        _pivot_dims_now = list(active_rows) + list(active_cols)
        _needs_product_soh_dims = any(c not in _STORE_LEVEL_DIMS for c in _pivot_dims_now)
        _line_records = len(df)
        _cached_sales_sk = None
        if _needs_product_soh_dims:
            df, _cached_sales_sk = _canonicalize_store_dims(df, return_keys=True)
            df = _fill_blank_dims(df)
            if soh_df is not None and not soh_df.empty:
                soh_df = _align_soh_store_dims(soh_df, df)
                soh_df = _canonicalize_store_dims(soh_df, label_source=df)
                soh_df = _fill_blank_dims(soh_df)
            _cached_sales_sk = _channel_qualified_site_keys(df)
        else:
            # Only touch store label columns used by the tree (much cheaper than full dim fill).
            # Use assign so we never mutate the in-memory prepared-sales cache frame.
            _store_fill = {}
            for _col in (
                "zone",
                "sales_channel",
                "site_id_name",
                "site_id",
                "site_name",
                "cluster",
                "manager",
            ):
                if _col in df.columns:
                    _s = df[_col].fillna("").astype(str).str.strip()
                    _store_fill[_col] = _s.mask(_s.str.lower().isin(_BLANK_DIM_TOKENS), "Unassigned")
            # Collapse lines → stores first, then fix labels on ~500 stores.
            # Cleaning site_id_name on the full frame is what stalled
            # Zone → Channel → Store → Manager / Cluster.
            grain_key = _store_grain_cache_key(target_year, req.filters or {}, getattr(req, "search", "") or "", "cy")
            cached_grain = _STORE_GRAIN_CACHE.get(grain_key)
            if cached_grain is not None and not cached_grain.empty:
                df = cached_grain.copy()
                logger.info("Analyzer store-grain cache hit CY stores=%d", len(df))
            else:
                if _store_fill:
                    df = df.assign(**_store_fill)
                df = _aggregate_store_grain(df, list(active_rows) + list(active_cols))
                df = _ensure_clean_site_id_name(df)
                if len(_STORE_GRAIN_CACHE) >= _STORE_GRAIN_CACHE_MAX:
                    _STORE_GRAIN_CACHE.clear()
                _STORE_GRAIN_CACHE[grain_key] = df.copy()
                logger.info(
                    "Analyzer store-grain collapse CY lines=%d → stores=%d",
                    _line_records,
                    len(df),
                )
            df, _cached_sales_sk = _canonicalize_store_dims(df, return_keys=True)
            # Store SOH joins on channel|site key, not the display label.
            # Rewriting every SOH line here was the multi-minute hierarchy stall.

        # 3. Load LY data if needed — same dates one year earlier
        # (same rule as sales_dashboard._load_sply_prior_df).
        # Do NOT shift the nominal filter end (e.g. FY ends 31-Mar) when CY data only
        # exists through ~today — that compared YTD CY vs full prior FY (~−50% fake drop).
        needs_ly = any(
            v.field.endswith("_ly")
            or v.field.endswith("_ly_pct")
            or "growth" in v.field
            or "_ly_" in v.field
            for v in active_values
        )
        ly_df = None
        if needs_ly:
            try:
                from api.season_utils import shift_season_csv
                from api.routes.sales_dashboard import _fy_codes_covering_range

                # SPLY window first — same calendar dates one year earlier
                # (open FY: 1 Apr → latest in-FY date, not data-min / not 31 Mar).
                ly_start = ly_end = None
                # Explicit month/date/… timelines: use the same window helper as DuckDB.
                # Do NOT derive LY from post-store-grain dates — grain keeps max(date) per
                # store, which raises cy_min and shrinks LY (e.g. Oct 21–28 vs Oct 1–28).
                if timeline_mode in _EXPLICIT_TIMELINE_MODES:
                    try:
                        from api.services.sales_pivot_query_engine import (
                            _prepared_sales_path,
                            _same_window_last_year,
                        )

                        cy_path = _prepared_sales_path(int(target_year)) if target_year else None
                        cy_max_for_ly = None
                        if not df.empty and "date" in df.columns:
                            _mx = pd.to_datetime(df["date"], errors="coerce").max()
                            if pd.notna(_mx):
                                cy_max_for_ly = _mx
                        if cy_path is not None:
                            win = _same_window_last_year(
                                cy_path,
                                int(target_year),
                                req.filters or {},
                                cy_max=cy_max_for_ly,
                            )
                            if win:
                                ly_start = pd.Timestamp(win[0]).normalize()
                                ly_end = pd.Timestamp(win[1]).normalize()
                    except Exception as win_exc:
                        logger.warning("Analyzer LY timeline window failed: %s", win_exc)
                if ly_start is None and compare_window is not None:
                    cy_start, cy_end, _fy_end = compare_window
                    ly_start = pd.Timestamp(cy_start) - pd.DateOffset(years=1)
                    ly_end = pd.Timestamp(cy_end) - pd.DateOffset(years=1)
                elif ly_start is None and not df.empty and "date" in df.columns:
                    cy_min = pd.to_datetime(df["date"], errors="coerce").min()
                    cy_max = pd.to_datetime(df["date"], errors="coerce").max()
                    if pd.notna(cy_min) and pd.notna(cy_max):
                        ly_start = pd.Timestamp(cy_min).normalize() - pd.DateOffset(years=1)
                        ly_end = pd.Timestamp(cy_max).normalize() - pd.DateOffset(years=1)

                # Load every FY folder covering the LY window (Overview parity).
                prior_y = target_year - 1 if target_year and target_year > 2000 else 0
                ly_years = [prior_y] if prior_y else []
                if ly_start is not None and ly_end is not None:
                    ly_years = _fy_codes_covering_range(ly_start, ly_end) or ly_years
                ly_parts: list[pd.DataFrame] = []
                for y in ly_years:
                    try:
                        part = _get_prepared_sales_df(int(y))
                        if part is not None and not part.empty:
                            ly_parts.append(part)
                    except Exception as y_exc:
                        logger.warning("Analyzer LY prepared load failed year=%s: %s", y, y_exc)
                if not ly_parts and prior_y:
                    try:
                        fallback = _get_prepared_sales_df(prior_y)
                        if fallback is not None and not fallback.empty:
                            ly_parts.append(fallback)
                    except Exception as y_exc:
                        logger.warning("Analyzer LY prior-year fallback failed: %s", y_exc)
                if not ly_parts:
                    # Option B: no All Years (0). Try remaining analyzer build years.
                    try:
                        from api.sales_year_loader import analyzer_build_years

                        for y in analyzer_build_years():
                            if int(y) == int(target_year):
                                continue
                            try:
                                fallback = _get_prepared_sales_df(int(y))
                                if fallback is not None and not fallback.empty:
                                    ly_parts.append(fallback)
                                    break
                            except Exception as y_exc:
                                logger.warning(
                                    "Analyzer LY build-year fallback failed year=%s: %s",
                                    y,
                                    y_exc,
                                )
                    except Exception as y_exc:
                        logger.warning("Analyzer LY build-years fallback failed: %s", y_exc)

                if ly_parts:
                    ly_df = ly_parts[0] if len(ly_parts) == 1 else pd.concat(ly_parts, ignore_index=True)
                    # Do not swap store-grain cache in before dim filters + date clip —
                    # grain drops product dims and collapses date to max/store, which
                    # would skip or corrupt the LY window.

                    def _ly_season_filter_val(fval: Any) -> Any:
                        """SS-26 → SS-25 (same as sales_dashboard._load_sply_prior_df)."""
                        if isinstance(fval, list):
                            out: list[str] = []
                            for x in fval:
                                raw = str(x).strip()
                                if not raw:
                                    continue
                                shifted = shift_season_csv(raw, 1) or raw
                                out.extend([p.strip() for p in shifted.split(",") if p.strip()])
                            return out or fval
                        if isinstance(fval, str):
                            return shift_season_csv(fval, 1) or fval
                        return fval

                    # Apply same non-date dimension filters as CY — season shifted −1y.
                    if req.filters:
                        for field, fval in req.filters.items():
                            if field in (
                                "year",
                                "financial_year",
                                "fy_week",
                                "month",
                                "date",
                                "date_range",
                                "from_date",
                                "to_date",
                                "search",
                                "timeline_mode",
                                "timeline_values",
                            ):
                                continue
                            if fval is None or fval == "" or fval == []:
                                continue
                            if field not in ly_df.columns and field not in ALLOWED_DIMENSION_KEYS:
                                continue
                            if field not in ly_df.columns:
                                continue
                            use_val = _ly_season_filter_val(fval) if field == "season" else fval
                            if isinstance(use_val, list):
                                c_vals = [str(x).strip().lower() for x in use_val if str(x).strip()]
                                if c_vals:
                                    ly_df = _apply_dimension_list_filter(ly_df, field, c_vals)
                            elif isinstance(use_val, (str, int, float)):
                                if field == "sales_channel":
                                    ly_df = _apply_dimension_list_filter(
                                        ly_df, field, _expand_channel_filter_vals([str(use_val).strip().lower()])
                                    )
                                elif field == "season":
                                    # Comma-separated shifted seasons (multi-select)
                                    parts = [p.strip().lower() for p in str(use_val).split(",") if p.strip()]
                                    if len(parts) > 1:
                                        ly_df = _apply_dimension_list_filter(ly_df, field, parts)
                                    elif parts:
                                        ly_df = ly_df[
                                            ly_df[field].astype(str).str.strip().str.lower() == parts[0]
                                        ]
                                else:
                                    ly_df = ly_df[
                                        ly_df[field].astype(str).str.strip().str.lower()
                                        == str(use_val).strip().lower()
                                    ]

                    if (
                        ly_df is not None
                        and not ly_df.empty
                        and "date" in ly_df.columns
                        and ly_start is not None
                        and ly_end is not None
                    ):
                        ly_df = _clip_dates(ly_df, ly_start, ly_end)
                    if (
                        ly_df is not None
                        and not ly_df.empty
                        and not _needs_product_soh_dims
                    ):
                        ly_key = _store_grain_cache_key(
                            target_year,
                            {
                                **(req.filters or {}),
                                "_ly_window": f"{ly_start}|{ly_end}",
                            },
                            "",
                            "ly",
                        )
                        cached_ly = _STORE_GRAIN_CACHE.get(ly_key)
                        if cached_ly is not None and not cached_ly.empty:
                            ly_df = cached_ly.copy()
                            logger.info("Analyzer store-grain cache hit LY stores=%d", len(ly_df))
                        else:
                            _ly_lines = len(ly_df)
                            ly_df = _aggregate_store_grain(ly_df, list(active_rows) + list(active_cols))
                            ly_df = _ensure_clean_site_id_name(ly_df)
                            if len(_STORE_GRAIN_CACHE) >= _STORE_GRAIN_CACHE_MAX:
                                _STORE_GRAIN_CACHE.clear()
                            _STORE_GRAIN_CACHE[ly_key] = ly_df.copy()
                            logger.info(
                                "Analyzer store-grain collapse LY lines=%d → stores=%d",
                                _ly_lines,
                                len(ly_df),
                            )
            except MemoryError as ly_err:
                # Hosted App Service: prior-year parquet can OOM the single worker.
                # Keep CY+SOH response instead of taking the whole Analyzer down (503).
                logger.warning("Analyzer LY load OOM — continuing without LY: %s", ly_err)
                ly_df = None
                try:
                    import gc

                    gc.collect()
                except Exception:
                    pass
            except Exception as ly_err:
                logger.warning("Could not load LY dataframe: %s", ly_err)
                ly_df = None

        # 4. Matrix Split Column Identification
        split_col_keys = []
        primary_col_dim = active_cols[0] if active_cols else None
        if primary_col_dim and primary_col_dim in df.columns:
            split_col_keys = sorted([str(k) for k in df[primary_col_dim].dropna().unique() if str(k).strip() and str(k).lower() != "nan"])[:30]

        # Shared store-key / SOH maps for GT + hierarchy (compute once).
        _pivot_dims = list(active_rows) + list(active_cols)
        _store_only_pivot = bool(_pivot_dims) and all(c in _STORE_LEVEL_DIMS for c in _pivot_dims)
        # Snapshot CY sales-cut join keys BEFORE LY-only rows are appended — SOH/dispatch
        # follow the CY sales cut (DuckDB _soh_aligned_to_sales), not LY-only stores.
        _cy_soh_join_sk = (
            _analyzer_store_join_key(df) if not df.empty else pd.Series(dtype=object)
        )
        _cy_store_keys = {
            k for k in _cy_soh_join_sk.astype(str).str.strip()
            if k and not k.endswith("|")
        }
        if _store_only_pivot and ly_df is not None and not ly_df.empty:
            df, ly_df = _align_ly_store_dims(df, ly_df)
            _cached_sales_sk = None
        _pre_sales_sk = (
            _cached_sales_sk
            if _cached_sales_sk is not None and len(_cached_sales_sk) == len(df)
            else (_channel_qualified_site_keys(df) if not df.empty else pd.Series(dtype=object))
        )
        # SOH/dispatch maps are keyed by analyzer join keys (not prepared _pivot_sk).
        _pre_soh_join_sk = _cy_soh_join_sk
        _pre_store_soh = (
            _store_current_soh_map(soh_df)
            if soh_df is not None and not soh_df.empty and "current_soh" in soh_df.columns
            else pd.Series(dtype=float)
        )
        _pre_site_mdq = _site_mdq_frame(soh_df) if soh_df is not None and not soh_df.empty else None

        # 5. Grand Total (single pass vector aggregation)
        needs_sqft = any(v.field in ("sales_per_sqft", "store_sqft") for v in active_values)
        site_id_col = "site_id" if "site_id" in df.columns else None

        gt_sales = float(df["sales"].sum()) if "sales" in df.columns else 0.0
        gt_qty = float(df["quantity"].sum()) if "quantity" in df.columns else 0.0
        gt_gross = float(df["gross_sales"].sum()) if "gross_sales" in df.columns else 0.0
        gt_disc = float(df["discount_amount"].sum()) if "discount_amount" in df.columns else 0.0
        # Store-only pivots: GT SOH = stock for stores present in the sales cut (parent≈children).
        # Product pivots keep full barcode SOH after the same filters/dim sync.
        _pre_store_disp = (
            _store_metric_map(soh_df, "dispatch_qty")
            if soh_df is not None and not soh_df.empty and "dispatch_qty" in soh_df.columns
            else pd.Series(dtype=float)
        )
        _pre_store_avail = (
            _store_metric_map(soh_df, "available_qty")
            if soh_df is not None and not soh_df.empty and "available_qty" in soh_df.columns
            else pd.Series(dtype=float)
        )
        if _store_only_pivot and not _pre_store_soh.empty:
            _sales_store_keys = {
                k for k in _pre_soh_join_sk.astype(str).str.strip()
                if k and not k.endswith("|")
            }
            _keys_list = list(_sales_store_keys)
            gt_soh = float(_pre_store_soh.reindex(_keys_list).fillna(0.0).sum()) if _keys_list else 0.0
            gt_dispatch = float(_pre_store_disp.reindex(_keys_list).fillna(0.0).sum()) if _keys_list else 0.0
            gt_available = float(_pre_store_avail.reindex(_keys_list).fillna(0.0).sum()) if _keys_list else 0.0
        else:
            gt_soh = (
                float(soh_df["current_soh"].sum())
                if soh_df is not None and not soh_df.empty and "current_soh" in soh_df.columns
                else 0.0
            )
            gt_dispatch = (
                float(soh_df["dispatch_qty"].sum())
                if soh_df is not None and not soh_df.empty and "dispatch_qty" in soh_df.columns
                else 0.0
            )
            gt_available = (
                float(soh_df["available_qty"].sum())
                if soh_df is not None and not soh_df.empty and "available_qty" in soh_df.columns
                else 0.0
            )
        gt_mdq = float(_pre_site_mdq["mdq"].sum()) if _pre_site_mdq is not None else 0.0
        gt_ly_sales = float(ly_df["sales"].sum()) if ly_df is not None and "sales" in ly_df.columns else 0.0
        gt_ly_qty = float(ly_df["quantity"].sum()) if ly_df is not None and "quantity" in ly_df.columns else 0.0

        def _window(frame: Optional[pd.DataFrame]) -> tuple[Optional[pd.Timestamp], Optional[pd.Timestamp]]:
            if frame is None or frame.empty or "date" not in frame.columns:
                return None, None
            date_col = frame["date"]
            if pd.api.types.is_datetime64_any_dtype(date_col):
                dates = date_col
            else:
                # Unique-date parse — avoid scanning hundreds of thousands of row values.
                uniques = pd.to_datetime(date_col.dropna().unique(), errors="coerce")
                if not uniques.notna().any():
                    return None, None
                return pd.Timestamp(uniques.min()).normalize(), pd.Timestamp(uniques.max()).normalize()
            if not dates.notna().any():
                return None, None
            return pd.Timestamp(dates.min()).normalize(), pd.Timestamp(dates.max()).normalize()

        def _pair(summary: Optional[pd.DataFrame], key) -> tuple[Optional[float], Optional[float]]:
            if summary is None:
                return None, None
            try:
                stat = summary.loc[key]
                if isinstance(stat, pd.DataFrame):
                    stat = stat.iloc[0]
                return float(stat.get("opening", 0.0)), float(stat.get("receipts", 0.0))
            except Exception:
                return 0.0, 0.0

        cy_start, cy_end = _window(df)
        ly_start, ly_end = _window(ly_df)
        # Shares site-window cache with later group_opening_receipts (sell-through).
        gt_opening, gt_receipts = movement_totals_for_frame(df) if cy_start is not None else (None, None)
        gt_ly_opening, gt_ly_receipts = movement_totals_for_frame(ly_df) if ly_start is not None else (None, None)

        gt_sqft = 0.0
        gt_sqft_sales = None
        if needs_sqft and "store_sqft" in df.columns and site_id_col:
            site_sq = df.groupby(site_id_col, observed=True).agg(sales=("sales", "sum"), store_sqft=("store_sqft", "first"))
            site_sq = site_sq[pd.to_numeric(site_sq["store_sqft"], errors="coerce").fillna(0) > 0]
            gt_sqft = float(site_sq["store_sqft"].sum()) if not site_sq.empty else 0.0
            gt_sqft_sales = float(site_sq["sales"].sum()) if not site_sq.empty else 0.0

        grand_total_metrics = _compute_measure_values(
            gt_sales, gt_qty, gt_gross, gt_disc, gt_soh, gt_mdq, _line_records, gt_ly_sales, gt_ly_qty, active_values,
            sqft_val=gt_sqft, sqft_sales=gt_sqft_sales,
            opening_val=gt_opening, receipts_val=gt_receipts,
            ly_opening_val=gt_ly_opening, ly_receipts_val=gt_ly_receipts,
            dispatch_val=gt_dispatch, available_val=gt_available,
        )

        grand_split_metrics: Dict[str, Dict[str, Any]] = {}
        if split_col_keys and primary_col_dim in df.columns:
            col_grp = df.groupby(primary_col_dim, observed=True).agg({
                "sales": "sum", "quantity": "sum", "gross_sales": "sum", "discount_amount": "sum",
            })
            soh_col_summary = None
            mdq_col_grp = None
            if soh_df is not None and not soh_df.empty and primary_col_dim:
                soh_col_summary = _build_soh_group_summary(
                    soh_df,
                    df,
                    [primary_col_dim],
                    store_soh=_pre_store_soh,
                    store_disp=_pre_store_disp,
                    store_avail=_pre_store_avail,
                    sales_sk=_pre_sales_sk,
                    cy_store_keys=_cy_store_keys,
                )
                if primary_col_dim in _STORE_LEVEL_DIMS and _pre_site_mdq is not None and primary_col_dim in _pre_site_mdq.columns:
                    mdq_col_grp = _pre_site_mdq.groupby(primary_col_dim, observed=True)["mdq"].sum()
            ly_col_grp = ly_df.groupby(primary_col_dim, observed=True).agg({"sales": "sum", "quantity": "sum"}) if ly_df is not None and primary_col_dim in ly_df.columns else None
            mov_col_grp = group_opening_receipts(df, [primary_col_dim], cy_start, cy_end) if cy_start is not None and primary_col_dim in MOVEMENT_DIMS else None
            ly_mov_col_grp = group_opening_receipts(ly_df, [primary_col_dim], ly_start, ly_end) if ly_df is not None and ly_start is not None and primary_col_dim in MOVEMENT_DIMS else None

            sqft_col_grp = None
            if needs_sqft and "store_sqft" in df.columns:
                if site_id_col:
                    sqft_col_df = df.drop_duplicates(subset=[primary_col_dim, site_id_col])
                    sqft_col_grp = sqft_col_df.groupby(primary_col_dim, observed=True)["store_sqft"].sum()
                else:
                    sqft_col_grp = df.groupby(primary_col_dim, observed=True)["store_sqft"].sum()

            for col_k in split_col_keys:
                if col_k in col_grp.index:
                    row_data = col_grp.loc[col_k]
                    c_s = float(row_data.get("sales", 0.0))
                    c_q = float(row_data.get("quantity", 0.0))
                    c_g = float(row_data.get("gross_sales", 0.0))
                    c_d = float(row_data.get("discount_amount", 0.0))
                else:
                    c_s = c_q = c_g = c_d = 0.0

                c_soh = c_disp = c_avail = 0.0
                if soh_col_summary is not None and col_k in soh_col_summary.index:
                    c_soh, c_disp, c_avail = _metrics_from_soh_stat(soh_col_summary.loc[col_k])
                c_mdq = float(mdq_col_grp.get(col_k, 0.0)) if mdq_col_grp is not None and col_k in mdq_col_grp.index else 0.0
                c_ly_s = float(ly_col_grp.loc[col_k, "sales"]) if ly_col_grp is not None and col_k in ly_col_grp.index and "sales" in ly_col_grp.columns else 0.0
                c_ly_q = float(ly_col_grp.loc[col_k, "quantity"]) if ly_col_grp is not None and col_k in ly_col_grp.index and "quantity" in ly_col_grp.columns else 0.0
                c_sqft = float(sqft_col_grp.loc[col_k]) if sqft_col_grp is not None and col_k in sqft_col_grp.index else 0.0
                c_open, c_rec = _pair(mov_col_grp, col_k)
                c_ly_open, c_ly_rec = _pair(ly_mov_col_grp, col_k)

                grand_split_metrics[col_k] = _compute_measure_values(
                    c_s, c_q, c_g, c_d, c_soh, c_mdq, 0, c_ly_s, c_ly_q, active_values, sqft_val=c_sqft,
                    opening_val=c_open, receipts_val=c_rec, ly_opening_val=c_ly_open, ly_receipts_val=c_ly_rec,
                    dispatch_val=c_disp, available_val=c_avail,
                )

        # 6. Vectorized Multi-Level GroupBy Engine
        level_summaries: Dict[int, pd.DataFrame] = {}
        level_split_summaries: Dict[int, pd.DataFrame] = {}
        soh_level_summaries: Dict[int, pd.DataFrame] = {}
        soh_level_split_summaries: Dict[int, pd.DataFrame] = {}
        mov_level_summaries: Dict[int, pd.DataFrame] = {}
        mov_level_split_summaries: Dict[int, pd.DataFrame] = {}
        ly_mov_level_summaries: Dict[int, pd.DataFrame] = {}
        ly_mov_level_split_summaries: Dict[int, pd.DataFrame] = {}
        mdq_level_summaries: Dict[int, pd.DataFrame] = {}
        ly_level_summaries: Dict[int, pd.DataFrame] = {}
        ly_level_split_summaries: Dict[int, pd.DataFrame] = {}
        sqft_level_summaries: Dict[int, pd.Series] = {}
        sqft_level_split_summaries: Dict[int, pd.Series] = {}

        valid_rows = [r for r in active_rows if r in df.columns]

        # _pre_sales_sk / _pre_store_soh / _pre_site_mdq already computed above for GT.
        _needs_movement = any(
            v.field in ("sell_through_pct", "sell_through_ly_pct") for v in active_values
        )
        _store_only_rows = bool(valid_rows) and all(c in _STORE_LEVEL_DIMS for c in valid_rows)

        def _roll_mov_levels(
            leaf: Optional[pd.DataFrame],
            target: Dict[int, pd.DataFrame],
            cols: List[str],
        ) -> None:
            if leaf is None or leaf.empty or not cols:
                return
            work = leaf.reset_index() if not isinstance(leaf.index, pd.RangeIndex) else leaf.copy()
            # groupby result usually has group cols as index
            if not all(c in work.columns for c in cols):
                work = leaf.reset_index()
            if not all(c in work.columns for c in ("opening", "receipts")):
                return
            for depth in range(1, len(cols) + 1):
                gcols = cols[:depth]
                if not all(c in work.columns for c in gcols):
                    continue
                target[depth] = work.groupby(gcols, observed=True)[["opening", "receipts"]].sum()

        _leaf_mov = None
        _leaf_ly_mov = None
        if _needs_movement and _store_only_rows and cy_start is not None:
            _leaf_mov = group_opening_receipts(df, valid_rows, cy_start, cy_end)
            _roll_mov_levels(_leaf_mov, mov_level_summaries, valid_rows)
            if ly_df is not None and ly_start is not None:
                _leaf_ly_mov = group_opening_receipts(ly_df, valid_rows, ly_start, ly_end)
                _roll_mov_levels(_leaf_ly_mov, ly_mov_level_summaries, valid_rows)

        for k in range(1, len(valid_rows) + 1):
            grp_cols = valid_rows[:k]
            # CY Sales GroupBy
            level_summaries[k] = df.groupby(grp_cols, observed=True).agg({
                "sales": "sum", "quantity": "sum", "gross_sales": "sum", "discount_amount": "sum",
                "site_id": "count"
            }).rename(columns={"site_id": "rec_count"})

            if split_col_keys and primary_col_dim in df.columns:
                level_split_summaries[k] = df.groupby(grp_cols + [primary_col_dim], observed=True).agg({
                    "sales": "sum", "quantity": "sum", "gross_sales": "sum", "discount_amount": "sum",
                })

            # SQFT GroupBy
            if needs_sqft and "store_sqft" in df.columns:
                if site_id_col and site_id_col not in grp_cols:
                    distinct_df = df.drop_duplicates(subset=grp_cols + [site_id_col])
                    sqft_level_summaries[k] = distinct_df.groupby(grp_cols, observed=True)["store_sqft"].sum()
                else:
                    sqft_level_summaries[k] = df.groupby(grp_cols, observed=True)["store_sqft"].first()

                if split_col_keys and primary_col_dim in df.columns:
                    split_grp_cols = grp_cols + [primary_col_dim]
                    if site_id_col and site_id_col not in split_grp_cols:
                        distinct_split_df = df.drop_duplicates(subset=split_grp_cols + [site_id_col])
                        sqft_level_split_summaries[k] = distinct_split_df.groupby(split_grp_cols, observed=True)["store_sqft"].sum()
                    else:
                        sqft_level_split_summaries[k] = df.groupby(split_grp_cols, observed=True)["store_sqft"].first()

            # Current SOH: store cuts join by site key; product cuts use synced SOH dims.
            # MDQ is one customer-master target per store, so it is not summed per barcode.
            if soh_df is not None and not soh_df.empty:
                soh_summary = _build_soh_group_summary(
                    soh_df,
                    df,
                    grp_cols,
                    store_soh=_pre_store_soh,
                    store_disp=_pre_store_disp,
                    store_avail=_pre_store_avail,
                    sales_sk=_pre_sales_sk,
                    cy_store_keys=_cy_store_keys,
                )
                if soh_summary is not None:
                    soh_level_summaries[k] = soh_summary
                if split_col_keys and primary_col_dim:
                    soh_split = _build_soh_group_summary(
                        soh_df,
                        df,
                        grp_cols + [primary_col_dim],
                        store_soh=_pre_store_soh,
                        store_disp=_pre_store_disp,
                        store_avail=_pre_store_avail,
                        sales_sk=_pre_sales_sk,
                        cy_store_keys=_cy_store_keys,
                    )
                    if soh_split is not None:
                        soh_level_split_summaries[k] = soh_split
                if all(c in _STORE_LEVEL_DIMS for c in grp_cols):
                    if _pre_site_mdq is not None and all(c in _pre_site_mdq.columns for c in grp_cols):
                        mdq_level_summaries[k] = _pre_site_mdq.groupby(grp_cols, observed=True).agg(
                            mdq=("mdq", "sum")
                        )

            if not (_needs_movement and _store_only_rows):
                if cy_start is not None and all(c in MOVEMENT_DIMS for c in grp_cols):
                    mov_grp = group_opening_receipts(df, grp_cols, cy_start, cy_end)
                    if mov_grp is not None:
                        mov_level_summaries[k] = mov_grp
                    if split_col_keys and primary_col_dim in MOVEMENT_DIMS:
                        mov_split = group_opening_receipts(df, grp_cols + [primary_col_dim], cy_start, cy_end)
                        if mov_split is not None:
                            mov_level_split_summaries[k] = mov_split
                if ly_df is not None and ly_start is not None and all(c in MOVEMENT_DIMS for c in grp_cols):
                    ly_mov_grp = group_opening_receipts(ly_df, grp_cols, ly_start, ly_end)
                    if ly_mov_grp is not None:
                        ly_mov_level_summaries[k] = ly_mov_grp
                    if split_col_keys and primary_col_dim in MOVEMENT_DIMS and primary_col_dim in ly_df.columns:
                        ly_mov_split = group_opening_receipts(ly_df, grp_cols + [primary_col_dim], ly_start, ly_end)
                        if ly_mov_split is not None:
                            ly_mov_level_split_summaries[k] = ly_mov_split

            # LY Sales GroupBy
            if ly_df is not None:
                ly_grp_cols = [c for c in grp_cols if c in ly_df.columns]
                if ly_grp_cols:
                    ly_level_summaries[k] = ly_df.groupby(ly_grp_cols, observed=True).agg({
                        "sales": "sum", "quantity": "sum"
                    })
                    if split_col_keys and primary_col_dim in ly_df.columns:
                        ly_level_split_summaries[k] = ly_df.groupby(ly_grp_cols + [primary_col_dim], observed=True).agg({
                            "sales": "sum", "quantity": "sum"
                        })

        # 7. Fast Hierarchical Tree Builder from In-Memory Group Summaries
        sort_field = req.sort[0].field if req.sort and len(req.sort) > 0 else (active_values[0].field if active_values else "sales")
        sort_dir = req.sort[0].direction if req.sort and len(req.sort) > 0 else "desc"
        reverse_sort = (sort_dir == "desc")

        def build_tree_nodes(parent_prefix_tuple: tuple[str, ...], level_idx: int) -> List[Dict[str, Any]]:
            k = level_idx + 1
            if k not in level_summaries or level_summaries[k].empty:
                return []

            summary_df = level_summaries[k]
            soh_summary_df = soh_level_summaries.get(k)
            mdq_summary_df = mdq_level_summaries.get(k)
            ly_summary_df = ly_level_summaries.get(k)
            mov_summary_df = mov_level_summaries.get(k)
            ly_mov_summary_df = ly_mov_level_summaries.get(k)
            mov_split_summary_df = mov_level_split_summaries.get(k)
            ly_mov_split_summary_df = ly_mov_level_split_summaries.get(k)
            split_summary_df = level_split_summaries.get(k)
            soh_split_summary_df = soh_level_split_summaries.get(k)
            ly_split_summary_df = ly_level_split_summaries.get(k)
            sqft_summary = sqft_level_summaries.get(k)
            sqft_split_summary = sqft_level_split_summaries.get(k)

            curr_dim = valid_rows[level_idx]
            nodes = []

            # Get matching index slices
            if level_idx == 0:
                index_keys = summary_df.index
            else:
                # Filter indices that start with parent_prefix_tuple
                if isinstance(summary_df.index, pd.MultiIndex):
                    try:
                        matching_sub = summary_df.loc[parent_prefix_tuple]
                        index_keys = matching_sub.index
                    except KeyError:
                        return []
                else:
                    return []

            for raw_val in index_keys:
                val_str = str(raw_val).strip()
                if not val_str or val_str.lower() == "nan":
                    continue

                full_tuple = parent_prefix_tuple + (raw_val,) if level_idx > 0 else (raw_val,)
                lookup_key = full_tuple[0] if len(full_tuple) == 1 else full_tuple

                try:
                    r_stats = summary_df.loc[lookup_key]
                    if isinstance(r_stats, pd.DataFrame):
                        r_stats = r_stats.iloc[0]
                    s_val = float(r_stats.get("sales", 0.0))
                    q_val = float(r_stats.get("quantity", 0.0))
                    g_val = float(r_stats.get("gross_sales", 0.0))
                    d_val = float(r_stats.get("discount_amount", 0.0))
                    r_cnt = int(r_stats.get("rec_count", 0))
                except Exception:
                    s_val = q_val = g_val = d_val = r_cnt = 0

                # SQFT Stats
                sqft_val = 0.0
                if sqft_summary is not None:
                    try:
                        sqft_stat = sqft_summary.loc[lookup_key]
                        if isinstance(sqft_stat, pd.Series):
                            sqft_stat = sqft_stat.iloc[0]
                        sqft_val = float(sqft_stat) if pd.notna(sqft_stat) else 0.0
                    except Exception:
                        sqft_val = 0.0

                # None when this cut cannot carry stock (month/week) or a store MDQ (SKU/season).
                soh_val = None
                disp_val = avail_val = 0.0
                if soh_summary_df is not None:
                    soh_val = 0.0
                    try:
                        soh_val, disp_val, avail_val = _metrics_from_soh_stat(soh_summary_df.loc[lookup_key])
                    except Exception:
                        soh_val = 0.0
                        disp_val = avail_val = 0.0
                mdq_val = None
                if mdq_summary_df is not None:
                    mdq_val = 0.0
                    try:
                        mdq_stat = mdq_summary_df.loc[lookup_key]
                        if isinstance(mdq_stat, pd.DataFrame):
                            mdq_stat = mdq_stat.iloc[0]
                        mdq_val = float(mdq_stat.get("mdq", 0.0))
                    except Exception:
                        mdq_val = 0.0

                # LY Stats
                ly_s_val = ly_q_val = 0.0
                if ly_summary_df is not None:
                    try:
                        ly_stats = ly_summary_df.loc[lookup_key]
                        if isinstance(ly_stats, pd.DataFrame):
                            ly_stats = ly_stats.iloc[0]
                        ly_s_val = float(ly_stats.get("sales", 0.0))
                        ly_q_val = float(ly_stats.get("quantity", 0.0))
                    except Exception:
                        pass

                open_val, rec_val = _pair(mov_summary_df, lookup_key)
                ly_open_val, ly_rec_val = _pair(ly_mov_summary_df, lookup_key)
                node_metrics = _compute_measure_values(
                    s_val, q_val, g_val, d_val, soh_val, mdq_val, r_cnt, ly_s_val, ly_q_val, active_values, sqft_val=sqft_val,
                    opening_val=open_val, receipts_val=rec_val, ly_opening_val=ly_open_val, ly_receipts_val=ly_rec_val,
                    dispatch_val=disp_val, available_val=avail_val,
                )

                # Split Metrics for Matrix
                node_split_metrics: Dict[str, Dict[str, Any]] = {}
                if split_col_keys and split_summary_df is not None:
                    for col_k in split_col_keys:
                        col_lookup = full_tuple + (col_k,)
                        try:
                            c_stat = split_summary_df.loc[col_lookup]
                            if isinstance(c_stat, pd.DataFrame):
                                c_stat = c_stat.iloc[0]
                            cs_val = float(c_stat.get("sales", 0.0))
                            cq_val = float(c_stat.get("quantity", 0.0))
                            cg_val = float(c_stat.get("gross_sales", 0.0))
                            cd_val = float(c_stat.get("discount_amount", 0.0))
                        except Exception:
                            cs_val = cq_val = cg_val = cd_val = 0.0

                        c_sqft_val = 0.0
                        if sqft_split_summary is not None:
                            try:
                                csqft_stat = sqft_split_summary.loc[col_lookup]
                                if isinstance(csqft_stat, pd.Series):
                                    csqft_stat = csqft_stat.iloc[0]
                                c_sqft_val = float(csqft_stat) if pd.notna(csqft_stat) else 0.0
                            except Exception:
                                c_sqft_val = 0.0

                        csoh_val = cmdq_val = cdisp_val = cavail_val = 0.0
                        if soh_split_summary_df is not None:
                            try:
                                csoh_val, cdisp_val, cavail_val = _metrics_from_soh_stat(
                                    soh_split_summary_df.loc[col_lookup]
                                )
                            except Exception:
                                csoh_val = cdisp_val = cavail_val = 0.0

                        cly_s = cly_q = 0.0
                        if ly_split_summary_df is not None:
                            try:
                                cly_stat = ly_split_summary_df.loc[col_lookup]
                                if isinstance(cly_stat, pd.DataFrame):
                                    cly_stat = cly_stat.iloc[0]
                                cly_s = float(cly_stat.get("sales", 0.0))
                                cly_q = float(cly_stat.get("quantity", 0.0))
                            except Exception:
                                pass

                        c_open, c_rec = _pair(mov_split_summary_df, col_lookup)
                        c_ly_open, c_ly_rec = _pair(ly_mov_split_summary_df, col_lookup)
                        node_split_metrics[col_k] = _compute_measure_values(
                            cs_val, cq_val, cg_val, cd_val, csoh_val, cmdq_val, 0, cly_s, cly_q, active_values, sqft_val=c_sqft_val,
                            opening_val=c_open, receipts_val=c_rec, ly_opening_val=c_ly_open, ly_receipts_val=c_ly_rec,
                            dispatch_val=cdisp_val, available_val=cavail_val,
                        )

                # Recursive Children
                children = []
                if level_idx + 1 < len(valid_rows):
                    children = build_tree_nodes(full_tuple, level_idx + 1)

                node_key = "__".join([f"{valid_rows[i]}:{full_tuple[i]}" for i in range(len(full_tuple))])

                nodes.append({
                    "key": node_key,
                    "dimension": curr_dim,
                    "value": val_str,
                    "level": level_idx,
                    "metrics": node_metrics,
                    "split_metrics": node_split_metrics,
                    "children": children,
                    "record_count": r_cnt,
                })

            # Sort nodes
            def get_sort_score(n: Dict[str, Any]):
                v = n["metrics"].get(sort_field)
                if v is None or (isinstance(v, float) and math.isnan(v)):
                    return -1e12 if reverse_sort else 1e12
                return v

            nodes.sort(key=get_sort_score, reverse=reverse_sort)
            return nodes

        tree_result = build_tree_nodes((), 0)

        period = None
        if compare_window is not None:
            cy_start, cy_end, fy_end = compare_window
            ly_start = pd.Timestamp(cy_start) - pd.DateOffset(years=1)
            ly_end = pd.Timestamp(cy_end) - pd.DateOffset(years=1)
            period = {
                "fy": f"FY{str(fy_end)[-2:]}",
                "cy_from": pd.Timestamp(cy_start).strftime("%Y-%m-%d"),
                "cy_to": pd.Timestamp(cy_end).strftime("%Y-%m-%d"),
                "ly_from": ly_start.strftime("%Y-%m-%d"),
                "ly_to": ly_end.strftime("%Y-%m-%d"),
            }

        result_payload = _with_year_meta(
            {
                "ok": True,
                "rows": tree_result,
                "period": period,
                "split_col_keys": split_col_keys,
                "split_col_dim": primary_col_dim,
                "grand_total": {
                    "metrics": grand_total_metrics,
                    "split_metrics": grand_split_metrics,
                    "record_count": _line_records,
                },
                "total_records": _line_records,
                "dimensions": active_rows,
                "measures": [v.model_dump() for v in active_values],
            }
        )

        from api.services.sales_pivot_query_engine import json_safe_payload

        timing["pivot_aggregation_ms"] = _ms_since(_t_pivot)
        _t_json = time.perf_counter()
        result_payload = json_safe_payload(result_payload)
        # Cache result
        if len(_QUERY_RESULT_CACHE) > _CACHE_MAX_ENTRIES:
            _QUERY_RESULT_CACHE.clear()
        _QUERY_RESULT_CACHE[cache_key] = result_payload
        save_page_cache("sales-analyzer", cache_key, result_payload)
        timing["json_serialization_ms"] = _ms_since(_t_json)
        if duckdb_attempted or timing.get("duckdb_fallback"):
            timing["pandas_fallback_ms"] = _ms_since(_t_pandas)
        else:
            # DuckDB not enabled — pandas is the primary engine; keep fallback=0
            timing["pandas_fallback_ms"] = 0.0

        logger.info(
            "Analyzer query done rows=%s cols=%s measures=%s records=%d grain=%d "
            "requested_year=%s effective_year=%s in %.2fs",
            active_rows,
            active_cols,
            [v.field for v in active_values],
            _line_records,
            len(df),
            requested_year,
            effective_year,
            time.perf_counter() - _t_query,
        )
        return _finish(result_payload, engine="pandas")

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Error executing sales analyzer query: %s", exc)
        # Exception type in the detail so a 500 is diagnosable from the browser Network tab.
        raise HTTPException(
            status_code=500,
            detail=f"Sales pivot could not be calculated ({type(exc).__name__}: {str(exc)[:160]}). Please retry.",
        ) from exc


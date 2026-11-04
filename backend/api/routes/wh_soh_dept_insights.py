"""
Warehouse SOH dept/section insights — latest WH register vs store-network LY + sales LY/CY.

CY: pipeline/input_files/WAREHOUSE_SOH.xlsx (DEPARTMENT + PRODUCT TYPE).
LY benchmark: monthly historical store closing SOH (same calendar month last year).
Sales: achieved sales master (same month CY vs LY) for demand context.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/inventory-analytics", tags=["Inventory Analytics"])

_BACKEND = Path(__file__).resolve().parents[2]
_HIST_DIR = _BACKEND / "pipeline" / "output_files" / "historical_soh"
_CACHE_DIR = _BACKEND / "data" / "api_cache"

# Typical WH stock as share of total store network SOH (tunable planning ratio).
_WH_NETWORK_RATIO = 0.08

_CACHE_LOCK = threading.RLock()
_MEMORY_NETWORK_LY: tuple[str, pd.DataFrame] | None = None


def _find_monthly_csv() -> Path | None:
    if not _HIST_DIR.exists():
        return None
    files = sorted(_HIST_DIR.glob("monthly_soh_sellthrough_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def _normalize_barcode(value: Any) -> str:
    try:
        from pipeline.pipeline_inputs import normalize_barcode as nb
    except ImportError:
        from pipeline_inputs import normalize_barcode as nb  # type: ignore
    out = nb(pd.Series([value]))
    return str(out.iloc[0]) if len(out) else ""


def _product_map() -> pd.DataFrame:
    try:
        from pipeline.item_master import load_item_master_df
        from pipeline.pipeline_inputs import find_column, normalize_barcode
    except ImportError:
        from item_master import load_item_master_df  # type: ignore
        from pipeline_inputs import find_column, normalize_barcode  # type: ignore

    item = load_item_master_df()
    if item.empty:
        return pd.DataFrame(columns=["barcode_key", "department", "section"])
    bc = find_column(item, "BARCODE", "Barcode")
    dept = find_column(item, "DEPARTMENT", "Department", "DIVISION")
    sec = find_column(item, "SECTION", "Section")
    x = item.copy()
    x["barcode_key"] = normalize_barcode(x[bc])
    x["department"] = x[dept].astype(str).str.strip() if dept else ""
    x["section"] = x[sec].astype(str).str.strip() if sec else ""
    x = x[x["barcode_key"].astype(str).str.len() > 0]
    return x.drop_duplicates(subset=["barcode_key"], keep="first")[["barcode_key", "department", "section"]]


def _load_wh_current() -> tuple[pd.DataFrame, dict[str, Any]]:
    try:
        from pipeline.pipeline_inputs import find_column, load_warehouse_register, warehouse_register_exists, warehouse_register_path
    except ImportError:
        from pipeline_inputs import find_column, load_warehouse_register, warehouse_register_exists, warehouse_register_path  # type: ignore

    if not warehouse_register_exists():
        raise HTTPException(status_code=404, detail="WAREHOUSE_SOH not found. Upload warehouse SOH first.")

    wh = load_warehouse_register()
    qty = find_column(wh, "CLOSINGSTOCKQTY", "CLOSING STOCK QTY", "QTY", "STOCK QTY")
    dept = find_column(wh, "DEPARTMENT", "Department", "DIVISION")
    sec = find_column(wh, "PRODUCT TYPE", "SECTION", "Section", "GROUP NAME")
    bc = find_column(wh, "BARCODE", "Barcode")

    x = wh.copy()
    x["barcode_key"] = x[bc].map(_normalize_barcode) if bc else ""
    x["department"] = x[dept].astype(str).str.strip().replace({"nan": "", "None": ""}) if dept else ""
    x["section"] = x[sec].astype(str).str.strip().replace({"nan": "", "None": ""}) if sec else ""
    x["wh_soh_cy"] = pd.to_numeric(x[qty], errors="coerce").fillna(0.0)

    # WH register already carries dept/section — only hit item master when missing.
    needs_map = (
        x["department"].astype(str).str.len().eq(0).mean() > 0.5
        or x["section"].astype(str).str.len().eq(0).mean() > 0.5
    )
    if needs_map:
        imap = _product_map()
        if not imap.empty:
            merged = x.merge(imap, on="barcode_key", how="left", suffixes=("", "_im"))
            merged["department"] = merged["department"].where(
                merged["department"].astype(str).str.len().gt(0), merged.get("department_im", "")
            )
            merged["section"] = merged["section"].where(
                merged["section"].astype(str).str.len().gt(0), merged.get("section_im", "")
            )
            x = merged

    x["department"] = x["department"].replace({"": "—", "nan": "—"})
    x["section"] = x["section"].replace({"": "—", "nan": "—"})

    as_of = pd.Timestamp.fromtimestamp(warehouse_register_path().stat().st_mtime).strftime("%d %b %Y")
    meta = {"as_of": as_of, "sku_rows": len(x), "total_wh_soh": float(x["wh_soh_cy"].sum())}
    return x, meta


def _ly_month_key(ref: pd.Timestamp) -> str:
    ly = ref - pd.DateOffset(years=1)
    return ly.strftime("%Y-%m")


def _load_network_soh_ly(month_key: str, monthly_path: Path) -> pd.DataFrame:
    global _MEMORY_NETWORK_LY
    source_key = f"{monthly_path.resolve()}|{monthly_path.stat().st_mtime_ns}|{month_key}"
    with _CACHE_LOCK:
        if _MEMORY_NETWORK_LY and _MEMORY_NETWORK_LY[0] == source_key:
            return _MEMORY_NETWORK_LY[1]

        parquet = _CACHE_DIR / f"network_soh_ly_{month_key.replace('-', '_')}.parquet"
        if parquet.exists():
            frame = pd.read_parquet(parquet)
            _MEMORY_NETWORK_LY = (source_key, frame)
            return frame

    imap = _product_map()
    parts: list[pd.DataFrame] = []
    for chunk in pd.read_csv(
        monthly_path,
        usecols=["barcode", "month", "closing_soh"],
        chunksize=800_000,
        low_memory=False,
    ):
        sub = chunk[chunk["month"].astype(str).str.strip() == month_key].copy()
        if sub.empty:
            continue
        sub["barcode_key"] = sub["barcode"].map(_normalize_barcode)
        sub["network_soh_ly"] = pd.to_numeric(sub["closing_soh"], errors="coerce").fillna(0.0).clip(lower=0)
        if not imap.empty:
            sub = sub.merge(imap, on="barcode_key", how="left")
        else:
            sub["department"] = "—"
            sub["section"] = "—"
        sub["department"] = sub["department"].fillna("—").astype(str)
        sub["section"] = sub["section"].fillna("—").astype(str)
        # Aggregate within chunk to save massive RAM
        sub_agg = (
            sub.groupby(["department", "section"], dropna=False)["network_soh_ly"]
            .sum()
            .reset_index()
        )
        parts.append(sub_agg)

    import gc
    gc.collect()

    if not parts:
        return pd.DataFrame(columns=["department", "section", "network_soh_ly"])

    frame = pd.concat(parts, ignore_index=True)
    frame = (
        frame.groupby(["department", "section"], dropna=False)["network_soh_ly"]
        .sum()
        .reset_index()
    )

    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(parquet, index=False)
    except Exception as exc:
        logger.warning("Could not cache network SOH LY: %s", exc)

    with _CACHE_LOCK:
        _MEMORY_NETWORK_LY = (source_key, frame)
    return frame


def _load_sales_by_dept(month_key: tuple[int, int], ly_month_key: tuple[int, int]) -> pd.DataFrame:
    """Aggregate sales CY/LY by dept/section from cached sales detail if available."""
    try:
        from api.routes import site_growth_playbook as sgp
    except ImportError:
        return pd.DataFrame(columns=["department", "section", "sales_cy", "sales_ly"])

    try:
        sales = sgp._load_all_sales_detail()
    except Exception:
        return pd.DataFrame(columns=["department", "section", "sales_cy", "sales_ly"])

    if sales.empty:
        return pd.DataFrame(columns=["department", "section", "sales_cy", "sales_ly"])

    s = sales.copy()
    s["month_key"] = s["Date"].dt.year.astype(str) + "-" + s["Date"].dt.month.astype(str).str.zfill(2)
    cy_k = f"{month_key[0]}-{month_key[1]:02d}"
    ly_k = f"{ly_month_key[0]}-{ly_month_key[1]:02d}"

    cy = s[s["month_key"] == cy_k]
    ly = s[s["month_key"] == ly_k]
    if cy.empty and ly.empty:
        return pd.DataFrame(columns=["department", "section", "sales_cy", "sales_ly"])

    cy_g = cy.groupby(["department", "section"], dropna=False)["Net_sales_amount"].sum().reset_index(name="sales_cy")
    ly_g = ly.groupby(["department", "section"], dropna=False)["Net_sales_amount"].sum().reset_index(name="sales_ly")
    merged = cy_g.merge(ly_g, on=["department", "section"], how="outer").fillna(0.0)
    merged["department"] = merged["department"].fillna("—").astype(str)
    merged["section"] = merged["section"].fillna("—").astype(str)
    return merged


def _build_rows(
    wh: pd.DataFrame,
    network_ly: pd.DataFrame,
    sales: pd.DataFrame,
    *,
    focus_month: str,
    ly_month: str,
) -> list[dict[str, Any]]:
    wh_g = (
        wh.groupby(["department", "section"], dropna=False)
        .agg(wh_soh_cy=("wh_soh_cy", "sum"), sku_count=("barcode_key", "nunique"))
        .reset_index()
    )

    merged = wh_g.merge(network_ly, on=["department", "section"], how="outer").fillna(0.0)
    if not sales.empty:
        merged = merged.merge(sales, on=["department", "section"], how="left").fillna(0.0)
    else:
        merged["sales_cy"] = 0.0
        merged["sales_ly"] = 0.0

    merged["wh_soh_cy"] = merged["wh_soh_cy"].fillna(0.0)
    merged["network_soh_ly"] = merged["network_soh_ly"].fillna(0.0)
    merged["sku_count"] = merged["sku_count"].fillna(0).astype(int)

    rows: list[dict[str, Any]] = []
    for _, r in merged.iterrows():
        dept = str(r["department"] or "—")
        sec = str(r["section"] or "—")
        cy = float(r["wh_soh_cy"])
        net_ly = float(r["network_soh_ly"])
        sales_cy = float(r["sales_cy"])
        sales_ly = float(r["sales_ly"])

        wh_target = net_ly * _WH_NETWORK_RATIO if net_ly > 0 else 0.0
        vs_network_ly_pct = round(100 * (cy - net_ly) / net_ly, 1) if net_ly > 0 else None
        sales_growth_pct = round(100 * (sales_cy - sales_ly) / sales_ly, 1) if sales_ly > 0 else None

        if wh_target > 0 and cy < wh_target * 0.9:
            increase_pct = round(100 * (wh_target - cy) / max(cy, 1.0), 1)
            action = "Increase WH depth"
            reason = (
                f"WH has {cy:,.0f} units · store network LY ({ly_month}) had {net_ly:,.0f}. "
                f"Target WH ~{wh_target:,.0f} ({int(_WH_NETWORK_RATIO * 100)}% of network) → +{increase_pct}% suggested."
            )
        elif sales_growth_pct is not None and sales_growth_pct > 5 and cy < net_ly * _WH_NETWORK_RATIO:
            increase_pct = round(max(0.0, sales_growth_pct * 0.5), 1)
            action = "Increase — sales outpacing WH"
            reason = f"Sales CY vs LY +{sales_growth_pct}% but WH depth low vs network LY."
        elif cy > wh_target * 1.25 and wh_target > 0:
            increase_pct = 0.0
            action = "Reduce / redeploy"
            reason = f"WH {cy:,.0f} above target {wh_target:,.0f} vs network LY benchmark."
        else:
            increase_pct = 0.0
            action = "Maintain"
            reason = "WH depth aligned with network LY benchmark and sales pace."

        rows.append(
            {
                "department": dept,
                "section": sec,
                "whSohCy": round(cy, 0),
                "networkSohLy": round(net_ly, 0),
                "whTargetFromLy": round(wh_target, 0),
                "vsNetworkLyPct": vs_network_ly_pct,
                "salesCy": round(sales_cy, 2),
                "salesLy": round(sales_ly, 2),
                "salesGrowthPct": sales_growth_pct,
                "suggestedIncreasePct": increase_pct,
                "skuCount": int(r["sku_count"]),
                "action": action,
                "reason": reason,
            }
        )

    rows.sort(key=lambda x: (x["suggestedIncreasePct"], x["whSohCy"]), reverse=True)
    return rows


@router.get("/wh-soh-dept-insights")
def get_wh_soh_dept_insights(
    limit: int = Query(200, ge=1, le=2000),
):
    """
    Latest warehouse SOH by dept/section vs store-network LY (same month) + sales CY/LY.
    Suggested increase % = gap to WH target derived from network LY depth.
    """
    wh, wh_meta = _load_wh_current()
    monthly = _find_monthly_csv()
    ref = pd.Timestamp.now()
    ly_month = _ly_month_key(ref)
    focus_month = ref.strftime("%Y-%m")

    note_bits = [
        "WH CY from latest WAREHOUSE_SOH.",
    ]
    if monthly is None:
        network_ly = pd.DataFrame(columns=["department", "section", "network_soh_ly"])
        note_bits.append(
            "LY network benchmark unavailable — run calculate_historical_soh "
            "(monthly_soh_sellthrough_*.csv missing)."
        )
        sales = pd.DataFrame(columns=["department", "section", "sales_cy", "sales_ly"])
    else:
        network_ly = _load_network_soh_ly(ly_month, monthly)
        ly_ts = ref - pd.DateOffset(years=1)
        sales = _load_sales_by_dept((ref.year, ref.month), (ly_ts.year, ly_ts.month))
        note_bits.append(
            f"LY benchmark = store-network closing SOH same month ({ly_month}). "
            f"Increase % closes gap to {int(_WH_NETWORK_RATIO * 100)}% of network LY."
        )

    rows = _build_rows(
        wh,
        network_ly,
        sales,
        focus_month=focus_month,
        ly_month=ly_month,
    )

    return {
        "ok": True,
        "focusMonth": focus_month,
        "lyMonth": ly_month,
        "whAsOf": wh_meta["as_of"],
        "totalWhSohCy": wh_meta["total_wh_soh"],
        "whNetworkRatioPct": int(_WH_NETWORK_RATIO * 100),
        "note": " ".join(note_bits),
        "rows": rows[:limit],
        "total": len(rows),
    }

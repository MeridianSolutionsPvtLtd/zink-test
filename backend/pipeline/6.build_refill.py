"""
Refill recommendation — prediction ko store SOH + warehouse SOH se fulfil karo.

Allocation logic:
  Step 1: forecast (predicted_qty) ko store_soh se cover karo.
  Step 2: bacha forecast warehouse (pooled per barcode) se — grade, phir priority_score;
          jitna WH stock ho utna allocate (forecast se zyada nahi).

Warehouse stock: input_files/WAREHOUSE_SOH.xlsx (+ optional ledger for multi-pass refill).

Input : output_files/SS26_weekly_predictions_formula_next_week_from_5w_avg.csv
Output: output_files/SS26_weekly_predictions_formula_next_week_with_refill_recommendation.csv (+ xlsx)
"""

import os
import re
from pathlib import Path

import pandas as pd

from pipeline_inputs import (
    CLOSED_REPLENISHMENT_SITE_IDS,
    CLOSED_REPLENISHMENT_SITE_NAME_MARKERS,
    get_as_of_date,
    get_replenishment_week_anchor,
    warehouse_register_exists,
    warehouse_register_path,
    load_warehouse_register,
    normalize_site_id_code,
)


BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output_files"
INPUT_DIR = BASE_DIR / "input_files"

INPUT_PREDICTION = OUTPUT_DIR / "SS26_weekly_predictions_formula_next_week_from_5w_avg.csv"
OUTPUT_CSV = OUTPUT_DIR / "SS26_weekly_predictions_formula_next_week_with_refill_recommendation.csv"
OUTPUT_XLSX = OUTPUT_DIR / "SS26_weekly_predictions_formula_next_week_with_refill_recommendation.xlsx"
PRED_CURRENT = (
    OUTPUT_DIR
    / "prediction"
    / "current"
    / "SS26_weekly_predictions_formula_next_week_with_refill_recommendation.csv"
)
PRED_HISTORY_DIR = OUTPUT_DIR / "prediction" / "history"

# Resolved at runtime via warehouse_register_path() (CSV from blob preferred).
WAREHOUSE_FILE = INPUT_DIR / "WAREHOUSE_SOH.xlsx"

# EBO site ids (store_type fallback ke liye)
EBO_SITE_IDS = {"528", "964", "972", "FGPL-MALLESHWARAM", "FGPL-VADODRA", "FGPL-KURLA"}

# Store types jinke paas store SOH nahi hai — inpe refill / WH-push / transfer
# nahi karenge (kyunki store_soh=0 ko "empty store" samajh lene se galat push hota hai).
# Centro SOH ab available hai, isliye ye khaali hai (sab channels par refill hoga).
SKIP_REFILL_STORE_TYPES = frozenset()

GRADE_PRIORITY_ORDER = {
    "1A+": 1,
    "A+": 2,
    "A": 3,
    "B": 4,
    "C": 5,
    "D": 6,
}


def derive_store_type(site_name: object, site_id: object | None = None) -> str:
    """Classify: EBO / PT / SSL / CENTRO / ICONIC / FASHION_FACTORY."""
    code = "" if site_id is None else str(site_id).strip().upper().replace(".0", "")
    name = str(site_name).strip()
    upper = name.upper()
    stripped = re.sub(r"^[\d\.\-\s]+", "", upper)

    if code in EBO_SITE_IDS or code.startswith("FGPL") or "FRESCO GLOBAL PVT LTD" in upper or upper.startswith("FRESCO"):
        return "EBO"
    if "FASHION FACTORY" in upper:
        return "FASHION_FACTORY"
    if re.fullmatch(r"T[A-Z0-9]{2,5}", code) or "CENTRO" in upper or upper.startswith("RRL") or "RELIANCE RETAIL" in upper:
        return "CENTRO"
    if "ICONIC" in upper:
        return "ICONIC"
    if (
        re.match(r"^[PW]\d", code)
        or "PANTALOONS" in upper
        or stripped.startswith(("PT-", "PT ", "PW-", "PW "))
        or re.match(r"^P\d", stripped)
    ):
        return "PT"
    if "SHOPPER" in upper or "SHOPER" in upper or stripped.startswith(("SSL", "SS-", "SS ")):
        return "SSL"
    return ""


def _norm(name: str) -> str:
    return str(name).strip().lower().replace("_", "").replace(" ", "")


def resolve_column(df: pd.DataFrame, aliases: list[str], label: str) -> str:
    mapping = {_norm(c): c for c in df.columns}
    for a in aliases:
        if _norm(a) in mapping:
            return mapping[_norm(a)]
    raise ValueError(f"Could not find column for {label}. Tried aliases: {aliases}")


def _norm_barcode(series: pd.Series) -> pd.Series:
    return series.astype(str).str.strip().str.upper().str.replace(r"\.0$", "", regex=True)


def derive_store_type_local(site_name: object) -> str:
    return derive_store_type(site_name)


def load_warehouse_stock() -> dict[str, float]:
    """Pooled warehouse SOH per barcode from the WH register file."""
    wh = load_warehouse_register()
    bc = resolve_column(wh, ["BARCODE", "Barcode"], "barcode")
    qty = resolve_column(
        wh, ["CLOSINGSTOCKQTY", "CLOSING STOCK QTY", "CLOSING_STOCK_QTY", "QTY", "STOCK QTY"], "qty"
    )
    m = pd.DataFrame(
        {
            "barcode": _norm_barcode(wh[bc]),
            "qty": pd.to_numeric(wh[qty], errors="coerce").fillna(0),
        }
    )
    m = m[m["barcode"] != ""]
    return m.groupby("barcode")["qty"].sum().to_dict()


def _merge_prior_refill_for_other_store_types(
    out: pd.DataFrame, selected: list[str]
) -> pd.DataFrame:
    """
    Previously re-attached prior rows for non-selected store types (e.g. EBO)
    into the current refill file. Home selection is the source of truth: when
    PT/SSL/CENTRO are selected, output must stay those channels only.
    """
    if selected:
        print(
            f"Skip merging prior other store types — output scoped to {sorted({s.strip().upper() for s in selected if s.strip()})}",
            flush=True,
        )
    return out


def to_num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").fillna(0)


def _grade_rank(series: pd.Series) -> pd.Series:
    grade = series.astype(str).str.strip().str.upper().str.replace(" ", "", regex=False)
    return grade.map(GRADE_PRIORITY_ORDER).fillna(999).astype(int)


def _priority_sort(group: pd.DataFrame) -> pd.DataFrame:
    return group.sort_values(
        ["store_grade_rank", "priority_sort_metric", "need_after_store", "predicted_qty_num"],
        ascending=[True, False, False, False],
    )


def allocate_wh_for_barcode(group: pd.DataFrame, wh_start: float) -> tuple[dict[int, float], float]:
    """Allocate WH only for forecast shortfall after store SOH (per row, pooled by barcode)."""
    alloc: dict[int, float] = {int(idx): 0.0 for idx in group.index}
    wh_remaining = float(wh_start)

    wh_needed = group[group["need_after_store"] > 0]
    if wh_needed.empty or wh_remaining <= 0:
        return alloc, wh_remaining

    for row in _priority_sort(wh_needed).itertuples():
        if wh_remaining <= 0:
            break
        give = min(float(row.need_after_store), wh_remaining)
        if give <= 0:
            continue
        alloc[int(row.Index)] += give
        wh_remaining -= give

    return alloc, wh_remaining


def main() -> None:
    if not INPUT_PREDICTION.exists():
        raise FileNotFoundError(f"Input file not found: {INPUT_PREDICTION}")
    if not warehouse_register_exists():
        raise FileNotFoundError(
            "Warehouse file not found. Expected input_files/WAREHOUSE_SOH.csv "
            "or WAREHOUSE_SOH.xlsx (run blob sync or upload warehouse SOH)."
        )
    wh_path = warehouse_register_path()
    print(f"Using warehouse register: {wh_path}", flush=True)

    print("[1/4] reading prediction file ...", flush=True)
    df = pd.read_csv(INPUT_PREDICTION, low_memory=False)
    df.columns = df.columns.str.strip()

    required_cols = {"site_id", "barcode", "predicted_qty", "store_soh", "avg_sold_qty_5w"}
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"Missing columns in prediction file: {sorted(missing)}")

    out = df.copy()
    # Always stamp replenishment week from run date (Mon–Sun), never carry a stale
    # week_date_range from an older prediction CSV.
    repl_week = get_replenishment_week_anchor(get_as_of_date())
    stale = (
        str(out["week_date_range"].iloc[0]).strip()
        if "week_date_range" in out.columns and len(out)
        else ""
    )
    out["predict_year"] = repl_week["year"]
    out["predict_week"] = repl_week["week"]
    out["week_start"] = repl_week["week_start"]
    out["week_start_date"] = repl_week["week_start_date"]
    out["week_date_range"] = repl_week["week_date_range"]
    out["year"] = repl_week["year"]
    out["week"] = repl_week["week"]
    out["month"] = repl_week["month"]
    if stale and stale != repl_week["week_date_range"]:
        print(
            f"WARNING: prediction week was stale ({stale!r}); "
            f"restamped to {repl_week['week_date_range']!r} "
            f"(W{repl_week['week']:02d} / {repl_week['week_start_date']})",
            flush=True,
        )
    else:
        print(
            f"Replenishment week: {repl_week['week_date_range']} "
            f"(W{repl_week['week']:02d} / {repl_week['week_start_date']})",
            flush=True,
        )
    out["site_id"] = out["site_id"].astype(str).str.strip().str.upper()
    out["barcode"] = _norm_barcode(out["barcode"])
    out["predicted_qty_num"] = to_num(out["predicted_qty"]).clip(lower=0)
    out["store_soh_num"] = to_num(out["store_soh"]).clip(lower=0)

    if "store_type" not in out.columns:
        site_name_series = out["site_name"] if "site_name" in out.columns else pd.Series("", index=out.index)
        out["store_type"] = [
            derive_store_type(n, s) for n, s in zip(site_name_series, out["site_id"])
        ]

    # Keep only Home-selected store channels for WH refill (get_selected_store_types fallback to replenishment_stores.json)
    try:
        from pipeline_inputs import get_selected_store_types
        selected = sorted(get_selected_store_types())
    except ImportError:
        try:
            from pipeline.pipeline_inputs import get_selected_store_types
            selected = sorted(get_selected_store_types())
        except ImportError:
            selected = [
                t.strip().upper()
                for t in (os.environ.get("PIPELINE_STORE_TYPES") or "").split(",")
                if t.strip()
            ]
    if selected:
        before = len(out)
        out = out[out["store_type"].astype(str).str.upper().str.strip().isin(selected)].copy()
        print(
            f"Filtered prediction to selected store_types={selected}: "
            f"{before:,} -> {len(out):,} rows",
            flush=True,
        )
        if out.empty:
            raise ValueError(
                f"No prediction rows left after store_type filter {selected}. "
                "Check replenishment store selection."
            )
        # Persist filtered prediction so downstream sees same scope
        filtered_pred = INPUT_PREDICTION.with_name(
            INPUT_PREDICTION.stem + "_selected_stores.csv"
        )
        out.drop(
            columns=["predicted_qty_num", "store_soh_num"],
            errors="ignore",
        ).to_csv(filtered_pred, index=False)
        print(f"Wrote filtered prediction: {filtered_pred}", flush=True)
    else:
        print(
            "WARNING: PIPELINE_STORE_TYPES empty - WH refill will use ALL store types",
            flush=True,
        )

    print("[2/4] loading warehouse stock ...", flush=True)
    try:
        from warehouse_ledger import (
            effective_warehouse_stock,
            sync_ledger_from_blob,
            update_ledger_after_allocation,
        )
    except ImportError:
        from pipeline.warehouse_ledger import (  # type: ignore
            effective_warehouse_stock,
            sync_ledger_from_blob,
            update_ledger_after_allocation,
        )

    remap_skus: set[str] = set()
    try:
        from sku_scope import is_remapped_scope, remapped_sku_names, merge_prior_other_skus
    except ImportError:
        from pipeline.sku_scope import (  # type: ignore
            is_remapped_scope,
            remapped_sku_names,
            merge_prior_other_skus,
        )

    if is_remapped_scope():
        remap_skus = remapped_sku_names()
        if not remap_skus:
            raise ValueError(
                "SKU scope is remapped but SKU_REMAP.xlsx has no OLD→NEW rows. "
                "Upload SKU Remap first."
            )
        wh_note = (
            "fresh full WH register"
            if (os.environ.get("WH_LEDGER_RESET") or "").strip().lower() in ("1", "true", "yes")
            else "remaining WH stock (incremental)"
        )
        print(
            f"SKU scope: remapped only ({len(remap_skus)} names). WH stock = {wh_note}. "
            "Other SKUs keep last refill.",
            flush=True,
        )
        if "sku" in out.columns:
            out["_skip_wh_remap"] = (
                ~out["sku"].astype(str).str.strip().str.upper().isin(remap_skus)
            )
            n_hit = int((~out["_skip_wh_remap"]).sum())
            print(f"  Rows that get WH allocation: {n_hit:,} / {len(out):,}", flush=True)
        else:
            out["_skip_wh_remap"] = False

    sync_ledger_from_blob()
    register_wh = load_warehouse_stock()
    # Keep FULL prediction table (all site × barcode). WH allocation still uses
    # register stock only — barcodes not in WH simply get wh_soh=0.
    wh_barcodes = {b for b, q in register_wh.items() if float(q or 0) > 0}
    if not wh_barcodes:
        wh_barcodes = set(register_wh.keys())
    n_wh_hit = int(out["barcode"].astype(str).isin(wh_barcodes).sum())
    print(
        f"Full prediction table kept: {len(out):,} rows "
        f"({n_wh_hit:,} rows have WH-register barcodes with stock; "
        f"{len(wh_barcodes)} WH barcodes)",
        flush=True,
    )
    if out.empty:
        raise ValueError(
            "No prediction rows left after store_type filter. "
            "Check replenishment store selection / prediction input."
        )
    if "sku" in out.columns:
        print(
            "SKUs in this refill pass: "
            + ", ".join(sorted(out["sku"].astype(str).str.strip().unique().tolist())[:30]),
            flush=True,
        )
    prior_refill = PRED_CURRENT if PRED_CURRENT.exists() else OUTPUT_CSV
    wh_stock, wh_info = effective_warehouse_stock(
        register_wh,
        wh_path,
        refill_csv_fallback=prior_refill,
    )
    print(f"WH ledger: {wh_info}", flush=True)
    reg_total = sum(register_wh.values())
    eff_total = sum(wh_stock.values())
    if reg_total > 0 and eff_total < reg_total * 0.999:
        print(
            f"Effective WH after prior allocations: {eff_total:,.0f} units "
            f"(register file {reg_total:,.0f}, reserved {reg_total - eff_total:,.0f})",
            flush=True,
        )

    out["wh_soh_num"] = out["barcode"].map(lambda b: float(wh_stock.get(b, 0.0))).clip(lower=0)
    out["wh_soh"] = out["wh_soh_num"].astype(int)

    # SOH-less store types (e.g. Centro) + closed stores — refill/WH-push/transfer se bahar.
    site_names = out["site_name"].astype(str).str.upper() if "site_name" in out.columns else pd.Series("", index=out.index)
    closed_store = out["site_id"].map(normalize_site_id_code).isin(CLOSED_REPLENISHMENT_SITE_IDS)
    for marker in CLOSED_REPLENISHMENT_SITE_NAME_MARKERS:
        closed_store = closed_store | site_names.str.contains(marker, na=False)
    out["_closed_store"] = closed_store
    out["_skip_refill"] = (
        out["store_type"].astype(str).str.upper().str.strip().isin(SKIP_REFILL_STORE_TYPES)
        | out["_closed_store"]
    )
    if closed_store.any():
        print(
            f"Excluded closed stores from replenishment: "
            f"{int(closed_store.sum()):,} rows (site_ids={sorted(CLOSED_REPLENISHMENT_SITE_IDS)})",
            flush=True,
        )

    if "sold_qty_prev_35d" in out.columns:
        out["sold_qty_prev_35d_num"] = to_num(out["sold_qty_prev_35d"]).clip(lower=0)
    else:
        out["sold_qty_prev_35d_num"] = 0.0
    if "store_grade" in out.columns:
        out["store_grade_rank"] = _grade_rank(out["store_grade"])
    else:
        out["store_grade_rank"] = 999
    if "priority_score" in out.columns:
        out["priority_sort_metric"] = to_num(out["priority_score"]).fillna(0)
    else:
        out["priority_sort_metric"] = out["sold_qty_prev_35d_num"]

    # Step 1: satisfy forecast using store SOH first.
    out["covered_by_store_soh"] = out[["predicted_qty_num", "store_soh_num"]].min(axis=1)
    out["need_after_store"] = (out["predicted_qty_num"] - out["covered_by_store_soh"]).clip(lower=0)

    # Step 2: allocate pooled WH SOH per barcode: grade first, then priority_score.
    out["covered_by_wh_soh"] = 0.0
    out["wh_min_2_35d_push_qty"] = 0.0
    out["wh_store_zero_push_qty"] = 0.0
    out["wh_soh_start_barcode"] = out["barcode"].map(lambda b: float(wh_stock.get(b, 0.0)))
    out["wh_soh_before_alloc"] = 0.0
    out["wh_soh_after_alloc"] = 0.0
    out["wh_soh_end_barcode"] = 0.0
    out["wh_soh_after_allocation"] = 0.0

    print("[3/4] allocating warehouse per barcode ...", flush=True)
    barcode_remaining: dict[str, float] = {
        str(bc).upper(): float(q) for bc, q in wh_stock.items()
    }
    for barcode, group_idx in out.groupby("barcode").groups.items():
        idx = list(group_idx)
        group = out.loc[idx].copy()
        # SOH-less store types (Centro) ko WH allocation se bahar rakho —
        # taaki unka stock real stores (PT/SSL/etc.) ke liye bacha rahe.
        group = group[~group["_skip_refill"]]
        if "_skip_wh_remap" in group.columns:
            group = group[~group["_skip_wh_remap"]]
        wh_start = float(wh_stock.get(barcode, 0.0))
        if group.empty:
            out.loc[idx, "wh_soh_before_alloc"] = wh_start
            out.loc[idx, "wh_soh_after_alloc"] = wh_start
            out.loc[idx, "wh_soh_end_barcode"] = wh_start
            out.loc[idx, "wh_soh_after_allocation"] = wh_start
            barcode_remaining[str(barcode).upper()] = float(wh_start)
            continue
        alloc_map, wh_remaining = allocate_wh_for_barcode(group, wh_start)

        out.loc[list(alloc_map.keys()), "covered_by_wh_soh"] = list(alloc_map.values())
        out.loc[idx, "wh_soh_before_alloc"] = wh_start
        out.loc[idx, "wh_soh_after_alloc"] = wh_remaining
        out.loc[idx, "wh_soh_end_barcode"] = wh_remaining
        out.loc[idx, "wh_soh_after_allocation"] = wh_remaining
        barcode_remaining[str(barcode).upper()] = float(wh_remaining)

    # Phase 2: New SKU / Virgin Store Introduction (surplus WH stock -> 1 pc per available size by Store Grade order)
    new_intro_allocated_units = 0
    out["wh_push_extra_qty"] = 0
    if "sku" in out.columns and not out.empty:
        print("[3b/4] evaluating new SKU introduction for non-introduced stores (Grade-wise) ...", flush=True)
        # Fast lookup mapping: (site_id, barcode) -> row index in `out`
        site_bc_to_idx: dict[tuple[str, str], int] = {}
        for row_i, (s_id, b_code) in enumerate(zip(out["site_id"], out["barcode"])):
            site_bc_to_idx[(str(s_id).strip().upper(), str(b_code).strip().upper())] = row_i

        # Group by SKU to distribute surplus stock across stores that never had this SKU
        for sku_val, sku_group in out.groupby("sku"):
            sku_name = str(sku_val).strip().upper()
            if not sku_name or sku_name in ("NAN", "NONE", ""):
                continue

            # Check if WH has any surplus stock for this SKU's barcodes
            sku_barcodes = [str(b).strip().upper() for b in sku_group["barcode"].unique() if str(b).strip()]
            avail_bcs = [b for b in sku_barcodes if barcode_remaining.get(b, 0.0) >= 1.0]
            if not avail_bcs:
                continue

            # Candidate stores for this SKU: stores where this SKU was never active
            # (predicted_qty == 0, store_soh == 0, sold_qty_prev_35d == 0, not skipped, not closed)
            site_agg = (
                sku_group.groupby("site_id")
                .agg(
                    total_pred=("predicted_qty_num", "sum"),
                    total_soh=("store_soh_num", "sum"),
                    total_sold_35d=("sold_qty_prev_35d_num", "sum"),
                    grade_rank=("store_grade_rank", "min"),
                    priority_metric=("priority_sort_metric", "max"),
                    skip_refill=("_skip_refill", "first"),
                )
                .reset_index()
            )

            # Virgin stores: no demand, no stock, no recent sales
            candidate_sites = site_agg[
                (~site_agg["skip_refill"])
                & (site_agg["total_pred"] <= 0)
                & (site_agg["total_soh"] <= 0)
                & (site_agg["total_sold_35d"] <= 0)
            ].copy()

            if candidate_sites.empty:
                continue

            # Sort candidate stores by Grade priority (A+ -> A -> B -> C -> D) then priority metric
            candidate_sites = candidate_sites.sort_values(
                ["grade_rank", "priority_metric"],
                ascending=[True, False],
            )

            for cand in candidate_sites.itertuples():
                cand_site = str(cand.site_id).strip().upper()
                # Check which sizes/barcodes are currently available in WH (at least 1 pc)
                current_avail = [b for b in sku_barcodes if barcode_remaining.get(b, 0.0) >= 1.0]
                if not current_avail:
                    break  # no more surplus stock for this SKU

                for bc in current_avail:
                    if barcode_remaining.get(bc, 0.0) < 1.0:
                        continue
                    row_idx = site_bc_to_idx.get((cand_site, bc))
                    if row_idx is not None:
                        out.loc[row_idx, "covered_by_wh_soh"] += 1.0
                        out.loc[row_idx, "wh_push_extra_qty"] += 1
                        barcode_remaining[bc] -= 1.0
                        new_intro_allocated_units += 1

                        wh_end = barcode_remaining[bc]
                        out.loc[row_idx, "wh_soh_after_alloc"] = wh_end
                        out.loc[row_idx, "wh_soh_end_barcode"] = wh_end
                        out.loc[row_idx, "wh_soh_after_allocation"] = wh_end

        if new_intro_allocated_units > 0:
            print(f"New SKU introduction allocated: {new_intro_allocated_units:,} units across candidate stores", flush=True)

    update_ledger_after_allocation(
        register_wh,
        wh_path,
        barcode_remaining=barcode_remaining,
        store_types=selected or ["ALL"],
        wh_allocated_units=int(out["covered_by_wh_soh"].sum()) if "covered_by_wh_soh" in out.columns else 0,
    )
    print("WH remaining ledger updated (warehouse/wh_remaining_ledger.json)", flush=True)

    demand_from_wh = out[["covered_by_wh_soh", "need_after_store"]].min(axis=1)
    out["total_fulfilled_qty"] = out["covered_by_store_soh"] + demand_from_wh + out["wh_push_extra_qty"]
    out["unfilled_qty"] = (
        out["predicted_qty_num"] - out["covered_by_store_soh"] - demand_from_wh
    ).clip(lower=0)

    # Final recommendation columns.
    out["recommendation_refill_qty"] = out["unfilled_qty"] + out["wh_push_extra_qty"]
    out["refill_source"] = "NO_ACTION"
    out.loc[(out["predicted_qty_num"] > 0) & (out["unfilled_qty"] == 0), "refill_source"] = (
        "STORE_OR_WH_COVERED"
    )
    out.loc[(out["predicted_qty_num"] > 0) & (out["unfilled_qty"] > 0), "refill_source"] = (
        "SITE_TO_SITE_TRANSFER_REQUIRED"
    )
    out.loc[(out["wh_push_extra_qty"] > 0), "refill_source"] = "NEW_INTRODUCTION_PUSH"

    def build_refill_note(row: pd.Series) -> str:
        pred = int(row["predicted_qty_num"])
        store = int(row["store_soh_num"])
        wh_before = int(row["wh_soh_before_alloc"])
        wh_after = int(row["wh_soh_after_alloc"])
        cover_store = int(row["covered_by_store_soh"])
        cover_wh = int(row["covered_by_wh_soh"])
        extra_push = int(row["wh_push_extra_qty"])
        unfilled = int(row["unfilled_qty"])

        if extra_push > 0:
            return (
                f"New SKU introduction: 1 piece allocated from surplus warehouse stock "
                f"based on Store Grade priority (warehouse remaining: {wh_after})."
            )
        if pred <= 0:
            return "Forecast is 0, so no refill is required."
        if unfilled <= 0:
            if cover_wh > 0:
                return (
                    f"Forecast is {pred}. Store stock covered {cover_store} (store stock: {store}). "
                    f"Balance {pred - cover_store} was filled from warehouse "
                    f"(warehouse before: {wh_before}, allocated: {cover_wh}, after: {wh_after})."
                )
            return (
                f"Forecast is {pred}. Store stock is {store}, so demand is fully covered from store. "
                "No warehouse allocation was needed."
            )
        return (
            f"Forecast is {pred}. Store stock covered {cover_store} (store stock: {store}). "
            f"Warehouse allocated {cover_wh} (warehouse before: {wh_before}, after: {wh_after}). "
            f"Still short by {unfilled}. Recommend site-to-site transfer for this shortage."
        )

    out["refill_note"] = out.apply(build_refill_note, axis=1)

    # SOH-less store types (Centro) + closed stores: koi refill/WH-push/transfer nahi.
    skip_mask = out["_skip_refill"]
    closed_mask = out["_closed_store"]
    if skip_mask.any():
        for c in [
            "covered_by_store_soh", "covered_by_wh_soh", "wh_min_2_35d_push_qty",
            "wh_store_zero_push_qty", "wh_push_extra_qty", "total_fulfilled_qty",
            "unfilled_qty", "recommendation_refill_qty",
        ]:
            out.loc[skip_mask, c] = 0
        centro_mask = skip_mask & ~closed_mask
        out.loc[closed_mask, "refill_source"] = "SKIPPED_CLOSED_STORE"
        out.loc[closed_mask, "refill_note"] = (
            "Store is closed — no replenishment, warehouse push, or transfer is recommended."
        )
        out.loc[centro_mask, "refill_source"] = "SKIPPED_NO_STORE_SOH"
        out.loc[centro_mask, "refill_note"] = (
            "Store SOH is not available for this store type (Centro), so no refill, "
            "warehouse push, or transfer is recommended."
        )

    # Keep the full prediction table for UI (all site × barcode × SKU).
    # Earlier we only published WH-action rows; product needs the complete grid.
    print(
        f"Final file = full prediction+refill table: {len(out):,} rows "
        f"(WH allocated on {int((to_num(out['covered_by_wh_soh']) > 0).sum()):,} rows)",
        flush=True,
    )
    if out.empty:
        print(
            "WARNING: No rows — output will be empty headers only.",
            flush=True,
        )

    for c in [
        "covered_by_store_soh", "covered_by_wh_soh", "wh_min_2_35d_push_qty",
        "wh_store_zero_push_qty", "wh_push_extra_qty", "total_fulfilled_qty",
        "unfilled_qty", "recommendation_refill_qty", "wh_soh_start_barcode",
        "wh_soh_before_alloc", "wh_soh_after_alloc", "wh_soh_end_barcode",
        "wh_soh_after_allocation",
    ]:
        if c in out.columns:
            out[c] = out[c].astype(int)

    out = out.drop(
        columns=[
            "predicted_qty_num",
            "store_soh_num",
            "wh_soh_num",
            "store_grade_rank",
            "priority_sort_metric",
            "sold_qty_prev_35d_num",
            "need_after_store",
            "_skip_refill",
            "_closed_store",
            "_skip_wh_remap",
        ],
        errors="ignore",
    )
    try:
        from customer_master import fill_city_state
    except ImportError:
        from pipeline.customer_master import fill_city_state  # type: ignore
    out = fill_city_state(out, site_col="site_id")

    out = _merge_prior_refill_for_other_store_types(out, selected)
    if remap_skus:
        prior_path = PRED_CURRENT if PRED_CURRENT.exists() else OUTPUT_CSV
        # Drop helper flag before merge so prior CSV columns align
        out = out.drop(columns=["_skip_wh_remap"], errors="ignore")
        out = merge_prior_other_skus(out, prior_path, names=remap_skus)
    else:
        out = out.drop(columns=["_skip_wh_remap"], errors="ignore")
    out = out.sort_values(["site_id", "barcode"]).reset_index(drop=True)

    print("[4/4] writing CSV + XLSX + prediction history ...", flush=True)
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUTPUT_CSV, index=False)
    try:
        out.to_excel(OUTPUT_XLSX, index=False, engine="xlsxwriter")
        print(f"Saved XLSX: {OUTPUT_XLSX}", flush=True)
    except Exception as e:
        try:
            out.to_excel(OUTPUT_XLSX, index=False)
            print(f"Saved XLSX: {OUTPUT_XLSX}", flush=True)
        except Exception as e2:
            print(f"[warn] refill xlsx write failed: {e2}", flush=True)

    try:
        from dated_output_store import publish_with_history

        publish_with_history(
            OUTPUT_CSV,
            current_path=PRED_CURRENT,
            history_dir=PRED_HISTORY_DIR,
            history_basename="SS26_weekly_predictions_formula_next_week_with_refill_recommendation",
            history_ext=".csv",
            keep=5,
            also_copy_to=[OUTPUT_CSV],
        )
    except Exception as e:
        print(f"[warn] prediction history publish failed: {e}", flush=True)

    print(f"Done. Saved CSV: {OUTPUT_CSV}", flush=True)
    print(f"Rows: {len(out)}", flush=True)
    print(f"Site-to-site transfer needed : {int((out['unfilled_qty'] > 0).sum()):,}", flush=True)
    print(f"Total WH allocated (units)   : {int(out['covered_by_wh_soh'].sum()):,}", flush=True)


if __name__ == "__main__":
    main()

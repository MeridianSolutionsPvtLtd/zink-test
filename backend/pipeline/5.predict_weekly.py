"""
Weekly next-week prediction (formula: weighted 5-week avg + lag1 floor).

Sabhi stores/channels (PT/SSL/CENTRO/ICONIC/EBO) ke liye — poori weekly file process hoti hai.

Input : output_files/weekly_aggregated_sales_new.csv  (add_soh_weekly output)
        Legacy (disabled): weekly_aggregated_sales_new_5w_avg_sold_with_first_transfer.csv
Output: output_files/SS26_weekly_predictions_formula_next_week_from_5w_avg.csv
"""

from pathlib import Path

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output_files"
USE_FIRST_TRANSFER_INPUT = True
WEEKLY_SOH_FILE = OUTPUT_DIR / "weekly_aggregated_sales_new.csv"
WEEKLY_WITH_FIRST_TRANSFER = (
    OUTPUT_DIR / "weekly_aggregated_sales_new_5w_avg_sold_with_first_transfer.csv"
)
INPUT_FILE = (
    WEEKLY_WITH_FIRST_TRANSFER
    if USE_FIRST_TRANSFER_INPUT and WEEKLY_WITH_FIRST_TRANSFER.exists()
    else WEEKLY_SOH_FILE
)
OUTPUT_FILE = OUTPUT_DIR / "SS26_weekly_predictions_formula_next_week_from_5w_avg.csv"

DEAD_STOCK_DAYS = 35
PANEL_MONTH_COUNT = 2
ENABLE_NEW_LAUNCH_BOOST = True
# Refill can use this as a WH-only push quantity; prediction itself does not force it.
MIN_QTY_WHEN_35D_ACTIVE = 2

# True: predict for the latest ISO week present in weekly input (e.g. W27).
# False: predict for the ISO week after latest (legacy next-week behaviour).
PREDICT_LATEST_DATA_WEEK = False


def next_iso_week(year_val: int, week_val: int) -> tuple[int, int]:
    if week_val >= 52:
        return year_val + 1, 1
    return year_val, week_val + 1


def largest_remainder_round(float_vals: pd.Series) -> np.ndarray:
    arr = float_vals.to_numpy(dtype=float)
    target_sum = int(round(arr.sum()))
    positive_mask = arr > 0
    pos_vals = arr[positive_mask]
    if len(pos_vals) == 0:
        return np.zeros(len(arr), dtype=int)
    floored = np.floor(pos_vals).astype(int)
    remainder = pos_vals - floored
    deficit = max(0, target_sum - int(floored.sum()))
    if deficit > 0:
        top_idx = np.argsort(-remainder)[:deficit]
        floored[top_idx] += 1
    out = np.zeros(len(arr), dtype=int)
    out[positive_mask] = floored
    return out


def recommendation_comment(row: pd.Series) -> str:
    status = str(row.get("dead_stock_status", ""))
    pred_num = pd.to_numeric(row.get("predicted_qty"), errors="coerce")
    pred = 0 if pd.isna(pred_num) else int(pred_num)
    days = pd.to_numeric(row.get("days_since_last_sale"), errors="coerce")
    launch_boost = bool(row.get("new_launch_boost_applied", False))
    launch_max_num = pd.to_numeric(row.get("max_sales_last_2w"), errors="coerce")
    launch_max = 0 if pd.isna(launch_max_num) else int(launch_max_num)
    first_transfer_date = pd.to_datetime(row.get("first_transfer_date"), errors="coerce")
    override_applied = bool(row.get("zero_soh_zero_lag1_override_applied", False))
    inactive_35d_applied = bool(row.get("inactive_5w_35d_rule_applied", False))

    if status == "DEAD STOCK":
        if pd.notna(days):
            return (
                f"The last sale was {int(days)} days ago, so this item is marked as dead stock "
                "and the prediction is set to 0."
            )
        return (
            "Recent sale data is not available, so this item is marked as dead stock "
            "and the prediction is set to 0."
        )

    if launch_boost and launch_max > 0:
        transfer_txt = (
            first_transfer_date.strftime("%Y-%m-%d")
            if pd.notna(first_transfer_date)
            else "recent weeks"
        )
        return (
            f"First transfer date is {transfer_txt} (within the latest 2 weeks) and sales happened, "
            f"so launch boost is applied and prediction is set to {launch_max}."
        )

    if override_applied:
        lws = int(pd.to_numeric(row.get("last_week_store_soh"), errors="coerce") or 0)
        cur_soh = int(pd.to_numeric(row.get("store_soh"), errors="coerce") or 0)
        sold35 = int(pd.to_numeric(row.get("sold_qty_prev_35d"), errors="coerce") or 0)
        return (
            "Last week store SOH and current store SOH are 0, and last week sales are 0; "
            f"therefore override rule is applied: prediction = min(sold_qty_prev_35d, 2). "
            f"Values: last_week_store_soh={lws}, store_soh={cur_soh}, sold_qty_prev_35d={sold35}, predicted={pred}."
        )

    if inactive_35d_applied:
        sold35 = int(pd.to_numeric(row.get("sold_qty_prev_35d"), errors="coerce") or 0)
        return (
            "There was no positive sale in the last 5 weekly lags, but sales exist in the "
            f"last 35 days ({sold35} unit(s)); prediction is set to max(sold_qty_prev_35d, "
            f"{MIN_QTY_WHEN_35D_ACTIVE}) = {pred}."
        )

    l1 = int(pd.to_numeric(row.get("lag1_sold_qty"), errors="coerce") or 0)
    l2 = int(pd.to_numeric(row.get("lag2_sold_qty"), errors="coerce") or 0)
    l3 = int(pd.to_numeric(row.get("lag3_sold_qty"), errors="coerce") or 0)

    return (
        f"Last week's sale was {l1}, the week before was {l2}, and the week before that was {l3}. "
        f"Based on this recent trend, the predicted demand is {pred}."
    )


def main() -> None:
    if not INPUT_FILE.exists():
        raise FileNotFoundError(f"Input file not found: {INPUT_FILE}")

    print("[1/3] reading weekly input ...", flush=True)
    df = pd.read_csv(INPUT_FILE, low_memory=False)
    df["site_id"] = df["site_id"].astype(str).str.strip()
    df["barcode"] = df["barcode"].astype(str).str.strip()
    df["week"] = pd.to_numeric(df["week"], errors="coerce")
    df["year"] = pd.to_numeric(df.get("year"), errors="coerce")
    df["month"] = pd.to_numeric(df.get("month"), errors="coerce")
    df["total_sold_qty"] = pd.to_numeric(df["total_sold_qty"], errors="coerce").fillna(0)
    df["week_start"] = pd.to_datetime(df.get("week_start"), errors="coerce")
    df["last_selling_date"] = pd.to_datetime(df.get("last_selling_date"), errors="coerce")
    df["max_dc_date"] = pd.to_datetime(df.get("max_dc_date"), errors="coerce")
    df["first_transfer_date"] = pd.to_datetime(df.get("first_transfer_date"), errors="coerce")
    df = df.sort_values(["site_id", "barcode", "week_start", "year", "week"]).reset_index(drop=True)

    # NOTE: We DO NOT use simple .shift() to calculate lags here.
    # .shift() ignores week gaps (e.g. if predicting W20, and a barcode's max week is W18,
    # shift(1) would wrongly grab W17 instead of 0).
    # Instead, we map lag1 to exactly the latest week, lag2 to latest-1, etc.

    latest_pair = (
        df[["year", "week"]]
        .dropna()
        .astype(int)
        .sort_values(["year", "week"])
        .iloc[-1]
    )
    latest_year = int(latest_pair["year"])
    latest_week = int(latest_pair["week"])
    if PREDICT_LATEST_DATA_WEEK:
        predict_year, predict_week = latest_year, latest_week
    else:
        predict_year, predict_week = next_iso_week(latest_year, latest_week)
    latest_week_start = pd.to_datetime(df["week_start"], errors="coerce").max()

    try:
        from pipeline_inputs import get_as_of_date, get_replenishment_week_anchor
    except ImportError:
        from pipeline.pipeline_inputs import get_as_of_date, get_replenishment_week_anchor  # type: ignore

    # Output week label/range = ISO week of run date (Mon anchor), so Tue–Fri runs
    # in the same calendar week stay on one week + one date range.
    repl_week = get_replenishment_week_anchor(get_as_of_date())
    week_start = repl_week["week_start"]

    panel_months = (
        df.loc[df["year"] == latest_year, "month"]
        .dropna()
        .astype(int)
        .drop_duplicates()
        .sort_values()
        .tolist()
    )
    panel_months = panel_months[-PANEL_MONTH_COUNT:]

    last_5_weeks = [latest_week - 4, latest_week - 3, latest_week - 2, latest_week - 1, latest_week]
    last_4_weeks = [latest_week - 3, latest_week - 2, latest_week - 1, latest_week]
    last_3_weeks = [latest_week - 2, latest_week - 1, latest_week]

    latest_row = (
        df.sort_values(["year", "week"])
        .groupby(["site_id", "barcode"], as_index=False)
        .tail(1)
        .copy()
    )

    _s25 = (
        pd.to_numeric(df["sold_qty_prev_25d"], errors="coerce").fillna(0)
        if "sold_qty_prev_25d" in df.columns
        else pd.Series(0, index=df.index, dtype="float64")
    )
    _s35 = (
        pd.to_numeric(df["sold_qty_prev_35d"], errors="coerce").fillna(0)
        if "sold_qty_prev_35d" in df.columns
        else pd.Series(0, index=df.index, dtype="float64")
    )
    _s60 = (
        pd.to_numeric(df["sold_qty_prev_60d"], errors="coerce").fillna(0)
        if "sold_qty_prev_60d" in df.columns
        else pd.Series(0, index=df.index, dtype="float64")
    )
    _s100 = (
        pd.to_numeric(df["sold_qty_prev_100d"], errors="coerce").fillna(0)
        if "sold_qty_prev_100d" in df.columns
        else pd.Series(0, index=df.index, dtype="float64")
    )
    _s5w = (
        pd.to_numeric(df["sold_qty_prev_5w"], errors="coerce").fillna(0)
        if "sold_qty_prev_5w" in df.columns
        else pd.Series(0, index=df.index, dtype="float64")
    )
    panel_totals = (
        df.assign(_s25=_s25, _s35=_s35, _s60=_s60, _s100=_s100, _s5w=_s5w)
        .groupby(["site_id", "barcode"], as_index=False)
        .agg(
            sold_qty_prev_25d=("_s25", "sum"),
            sold_qty_prev_35d=("_s35", "sum"),
            sold_qty_prev_60d=("_s60", "sum"),
            sold_qty_prev_100d=("_s100", "sum"),
            sold_qty_prev_5w=("_s5w", "sum"),
        )
    )
    _drop_panel = [
        c
        for c in (
            "sold_qty_prev_25d",
            "sold_qty_prev_35d",
            "sold_qty_prev_60d",
            "sold_qty_prev_100d",
            "sold_qty_prev_5w",
            "avg_sold_qty_35d",
            "avg_sold_qty_5w",
        )
        if c in latest_row.columns
    ]
    if _drop_panel:
        latest_row = latest_row.drop(columns=_drop_panel)
    latest_row = latest_row.merge(panel_totals, on=["site_id", "barcode"], how="left")
    latest_row["sold_qty_prev_25d"] = pd.to_numeric(
        latest_row.get("sold_qty_prev_25d"), errors="coerce"
    ).fillna(0)
    latest_row["sold_qty_prev_35d"] = pd.to_numeric(
        latest_row["sold_qty_prev_35d"], errors="coerce"
    ).fillna(0)
    latest_row["sold_qty_prev_60d"] = pd.to_numeric(
        latest_row.get("sold_qty_prev_60d"), errors="coerce"
    ).fillna(0)
    latest_row["sold_qty_prev_100d"] = pd.to_numeric(
        latest_row.get("sold_qty_prev_100d"), errors="coerce"
    ).fillna(0)
    latest_row["sold_qty_prev_5w"] = pd.to_numeric(
        latest_row["sold_qty_prev_5w"], errors="coerce"
    ).fillna(0)
    latest_row["avg_sold_qty_35d"] = latest_row["sold_qty_prev_35d"] / 5.0
    latest_row["avg_sold_qty_5w"] = latest_row["sold_qty_prev_5w"] / 5.0

    print("[2/3] building panel + prediction ...", flush=True)
    panel = df[
        (df["year"] == latest_year)
        & (df["week"].isin(last_5_weeks))
    ].copy()

    pivot = (
        panel.pivot_table(
            index=["site_id", "barcode"],
            columns="week",
            values="total_sold_qty",
            aggfunc="sum",
            fill_value=0,
        )
        .reset_index()
    )
    pivot.columns.name = None
    for w in last_5_weeks:
        if w not in pivot.columns:
            pivot[w] = 0

    # Lag mapping: lag1 = latest week, lag2 = latest-1, ...
    # Preserve negative return buckets instead of converting them to 0.
    pivot["pred_lag1_sold_qty"] = pivot[latest_week]
    pivot["pred_lag2_sold_qty"] = pivot[latest_week - 1]
    pivot["pred_lag3_sold_qty"] = pivot[latest_week - 2]
    pivot["pred_lag4_sold_qty"] = pivot[latest_week - 3]
    pivot["pred_lag5_sold_qty"] = pivot[latest_week - 4]

    pivot["panel_total_qty"] = pivot[last_4_weeks].sum(axis=1)

    pivot["sold_weeks_last4"] = (pivot[last_4_weeks] > 0).sum(axis=1)
    pivot["sold_weeks_last3"] = (pivot[last_3_weeks] > 0).sum(axis=1)
    pivot["sum_last4"] = pivot[last_4_weeks].sum(axis=1)
    pivot["sum_last3"] = pivot[last_3_weeks].sum(axis=1)

    pivot["prob_last4"] = pivot["sold_weeks_last4"] / 4.0
    pivot["prob_last3"] = pivot["sold_weeks_last3"] / 3.0

    pivot["avg_when_sold_4"] = np.where(
        pivot["sold_weeks_last4"] > 0,
        pivot["sum_last4"] / pivot["sold_weeks_last4"],
        0.0,
    )
    pivot["avg_when_sold_3"] = np.where(
        pivot["sold_weeks_last3"] > 0,
        pivot["sum_last3"] / pivot["sold_weeks_last3"],
        0.0,
    )

    # ── Prediction: weighted average + lag1 floor ───────────────────────────────
    LAG_COLS = [
        "pred_lag1_sold_qty",
        "pred_lag2_sold_qty",
        "pred_lag3_sold_qty",
        "pred_lag4_sold_qty",
        "pred_lag5_sold_qty",
    ]
    WEIGHTS = [0.60, 0.4, 0.3, 0.1, 0.03]  # sum = 1.0, most recent = highest

    weighted_avg = sum(
        w * pivot[col] for w, col in zip(WEIGHTS, LAG_COLS)
    )

    lag1 = pivot["pred_lag1_sold_qty"]
    predicted_float = np.maximum(weighted_avg, lag1)

    active_mask = (pivot[LAG_COLS] > 0).any(axis=1)
    predicted_float = np.where(active_mask, predicted_float, 0.0)

    pivot = pivot.merge(
        latest_row[["site_id", "barcode", "sold_qty_prev_35d"]],
        on=["site_id", "barcode"],
        how="left",
    )
    pivot["sold_qty_prev_35d"] = pd.to_numeric(pivot["sold_qty_prev_35d"], errors="coerce").fillna(0)

    # Agar 35 day sold qty 0 ya negative hai, to forecast sale 0 hogi
    predicted_float = np.where(pivot["sold_qty_prev_35d"] > 0, predicted_float, 0.0)

    inactive_5w_35d_rule = (~active_mask) & (pivot["sold_qty_prev_35d"] > 0)
    predicted_float = np.where(
        inactive_5w_35d_rule,
        np.maximum(pivot["sold_qty_prev_35d"], float(MIN_QTY_WHEN_35D_ACTIVE)),
        predicted_float,
    )

    pivot["formula_predicted_float"] = np.where(
        active_mask, np.maximum(weighted_avg, lag1), 0.0
    )
    pivot["formula_predicted_float"] = np.where(
        pivot["sold_qty_prev_35d"] > 0, pivot["formula_predicted_float"], 0.0
    )
    pivot["inactive_5w_35d_rule_applied"] = inactive_5w_35d_rule
    pivot["min_2_when_35d_active_applied"] = False
    pivot["predicted_float"] = predicted_float
    pivot["predicted_qty"] = largest_remainder_round(pd.Series(predicted_float))
    pivot.loc[~active_mask & ~inactive_5w_35d_rule, "predicted_qty"] = 0
    pivot.loc[pivot["sold_qty_prev_35d"] <= 0, "predicted_qty"] = 0

    # ── Merge back with latest row ──────────────────────────────────────────────
    out = latest_row.merge(
        pivot[
            [
                "site_id",
                "barcode",
                "predicted_float",
                "predicted_qty",
                "panel_total_qty",
                "sum_last3",
                "sum_last4",
                "sold_weeks_last3",
                "sold_weeks_last4",
                "prob_last3",
                "prob_last4",
                "avg_when_sold_3",
                "avg_when_sold_4",
                "pred_lag1_sold_qty",
                "pred_lag2_sold_qty",
                "pred_lag3_sold_qty",
                "pred_lag4_sold_qty",
                "pred_lag5_sold_qty",
                "formula_predicted_float",
                "inactive_5w_35d_rule_applied",
                "min_2_when_35d_active_applied",
            ]
        ],
        on=["site_id", "barcode"],
        how="left",
    )

    if pd.notna(latest_week_start):
        recent_two_week = df[
            (df["week_start"] >= (latest_week_start - pd.Timedelta(days=7)))
            & (df["week_start"] <= latest_week_start)
        ].copy()
        max_sales_last_2w = (
            recent_two_week.groupby(["site_id", "barcode"], as_index=False)["total_sold_qty"]
            .max()
            .rename(columns={"total_sold_qty": "max_sales_last_2w"})
        )
    else:
        max_sales_last_2w = pd.DataFrame(columns=["site_id", "barcode", "max_sales_last_2w"])

    out = out.merge(max_sales_last_2w, on=["site_id", "barcode"], how="left")
    out["max_sales_last_2w"] = (
        pd.to_numeric(out["max_sales_last_2w"], errors="coerce").fillna(0).astype(int)
    )

    out["predicted_float"] = pd.to_numeric(out["predicted_float"], errors="coerce").fillna(0)
    out["predicted_qty"] = (
        pd.to_numeric(out["predicted_qty"], errors="coerce").fillna(0).astype(int)
    )
    out["lag1_sold_qty"] = pd.to_numeric(out["pred_lag1_sold_qty"], errors="coerce").fillna(0)
    out["lag2_sold_qty"] = pd.to_numeric(out["pred_lag2_sold_qty"], errors="coerce").fillna(0)
    out["lag3_sold_qty"] = pd.to_numeric(out["pred_lag3_sold_qty"], errors="coerce").fillna(0)
    out["lag4_sold_qty"] = pd.to_numeric(out["pred_lag4_sold_qty"], errors="coerce").fillna(0)
    out["lag5_sold_qty"] = pd.to_numeric(out["pred_lag5_sold_qty"], errors="coerce").fillna(0)
    out = out.drop(
        columns=[
            "pred_lag1_sold_qty",
            "pred_lag2_sold_qty",
            "pred_lag3_sold_qty",
            "pred_lag4_sold_qty",
            "pred_lag5_sold_qty",
        ],
        errors="ignore",
    )

    out["days_since_last_sale"] = pd.to_numeric(
        out.get("days_since_last_sale"), errors="coerce"
    )
    out["days_since_last_sale"] = out["days_since_last_sale"].clip(lower=0)
    out["dead_stock_status"] = np.where(
        out["days_since_last_sale"].isna() | (out["days_since_last_sale"] > DEAD_STOCK_DAYS),
        "DEAD STOCK",
        "ACTIVE",
    )
    out.loc[out["dead_stock_status"] == "DEAD STOCK", "predicted_qty"] = 0

    # Priority score: rank recent demand by last-35-day sales.
    out["early_sale_bonus"] = pd.to_numeric(out.get("early_sale_bonus"), errors="coerce").fillna(0)
    out["priority_score_raw"] = pd.to_numeric(out.get("sold_qty_prev_35d"), errors="coerce").fillna(0)
    out["priority_score"] = 0.0
    prio_mask = (out["dead_stock_status"] != "DEAD STOCK") & (out["priority_score_raw"] > 0)
    if bool(prio_mask.any()):
        pmin = float(out.loc[prio_mask, "priority_score_raw"].min())
        pmax = float(out.loc[prio_mask, "priority_score_raw"].max())
        if pmax > pmin:
            out.loc[prio_mask, "priority_score"] = (
                10.0 * (out.loc[prio_mask, "priority_score_raw"] - pmin) / (pmax - pmin)
            )
        else:
            out.loc[prio_mask, "priority_score"] = 10.0
    out["priority_score_raw"] = pd.to_numeric(
        out["priority_score_raw"], errors="coerce"
    ).fillna(0).round(4)
    out["priority_score"] = pd.to_numeric(
        out["priority_score"], errors="coerce"
    ).fillna(0).round(2)
    out["priority_expl_formula"] = (
        "priority_score (0-10) = scaled(priority_score_raw), where "
        "priority_score_raw = sold_qty_prev_35d; "
        "sold_qty_prev_35d = total sold qty in the last 35 days."
    )

    # Launch override (disabled — no first_transfer_date / new-launch boost for now)
    if ENABLE_NEW_LAUNCH_BOOST and pd.notna(latest_week_start):
        window_start = latest_week_start - pd.Timedelta(days=7)
        launch_mask = (
            out["first_transfer_date"].notna()
            & (out["first_transfer_date"] >= window_start)
            & (out["first_transfer_date"] <= (latest_week_start + pd.Timedelta(days=6)))
            & (out["max_sales_last_2w"] > 0)
        )
    else:
        launch_mask = pd.Series(False, index=out.index)
    out["new_launch_boost_applied"] = launch_mask
    out.loc[launch_mask, "predicted_qty"] = out.loc[launch_mask, "max_sales_last_2w"]

    # Business override:
    # only when lag1 == 0 AND store_soh == 0 AND last_week_store_soh == 0
    if "last_week_store_soh" in out.columns:
        lws = pd.to_numeric(out["last_week_store_soh"], errors="coerce").fillna(0)
    else:
        lws = pd.Series(0, index=out.index, dtype="float64")
    if "store_soh" in out.columns:
        cur_store_soh = pd.to_numeric(out["store_soh"], errors="coerce").fillna(0)
    else:
        cur_store_soh = pd.Series(0, index=out.index, dtype="float64")
    if "sold_qty_prev_35d" in out.columns:
        sold_35d = pd.to_numeric(out["sold_qty_prev_35d"], errors="coerce").fillna(0)
    else:
        sold_35d = pd.Series(0, index=out.index, dtype="float64")
    lag1_vals = pd.to_numeric(out["lag1_sold_qty"], errors="coerce").fillna(0)

    override_mask = (
        (lws <= 0)
        & (cur_store_soh <= 0)
        & (lag1_vals <= 0)
        & (out["dead_stock_status"] != "DEAD STOCK")
        & (~out["new_launch_boost_applied"])
    )
    out["zero_soh_zero_lag1_override_applied"] = override_mask
    out.loc[override_mask, "predicted_qty"] = (
        sold_35d.loc[override_mask].clip(lower=0, upper=2).astype(int)
    )

    out["min_2_when_35d_active_applied"] = False

    out["predict_year"] = repl_week["year"]
    out["predict_week"] = repl_week["week"]
    out["week_start"] = week_start
    out["week_start_date"] = repl_week["week_start_date"]
    out["week_date_range"] = repl_week["week_date_range"]
    out["method"] = "weighted_avg_w1_0.50_w2_0.25_w3_0.15_w4_0.07_w5_0.03_lag1_floor"
    out["year"] = repl_week["year"]
    out["week"] = repl_week["week"]
    out["month"] = repl_week["month"]
    out["recommendation_comment"] = out.apply(recommendation_comment, axis=1)

    out = out.sort_values(["site_id", "barcode"]).reset_index(drop=True)
    print("[3/3] writing CSV + XLSX ...", flush=True)
    try:
        from pipeline_inputs import save_output_pair
    except ImportError:
        from pipeline.pipeline_inputs import save_output_pair  # type: ignore
    save_output_pair(out, OUTPUT_FILE)

    print(f"Done. Saved -> {OUTPUT_FILE}", flush=True)
    print(f"Latest data week: {latest_year}-W{latest_week:02d}", flush=True)
    print(
        f"Prediction target (from data): {predict_year}-W{predict_week:02d}",
        flush=True,
    )
    print(
        f"Replenishment week (run anchor): {repl_week['year']}-W{repl_week['week']:02d} "
        f"({repl_week['week_date_range']})",
        flush=True,
    )
    print(f"Rows: {len(out)}", flush=True)
    print(f"Total predicted qty: {int(out['predicted_qty'].sum())}", flush=True)
    print(f"Active panel combos: {int((out['panel_total_qty'] > 0).sum())}", flush=True)


if __name__ == "__main__":
    main()

"""
Weekly aggregation from SALES_REPORT.xlsx.

Input : output_files/SALES_REPORT.xlsx  (1.sales_report.py ka output)
Output: output_files/weekly_aggregated_sales_new.xlsx + .csv

Har (site_id, site_name, barcode, MRP, season) x ISO-week (Monday week_start) ke liye
weekly totals + lag/rolling/panel features banata hai.

Note: ebo_data import hata diya — store_type SALES_REPORT mein already hai; fallback ke liye
local derive_store_type rakha hai.
"""

import re
from pathlib import Path

import numpy as np
import pandas as pd


BASE_DIR = Path(__file__).parent
INPUT_FILE = BASE_DIR / "output_files" / "SALES_REPORT.xlsx"
OUTPUT_XLSX = BASE_DIR / "output_files" / "weekly_aggregated_sales_new.xlsx"
OUTPUT_CSV = BASE_DIR / "output_files" / "weekly_aggregated_sales_new.csv"

# Inclusive window on transaction `date`: last N days ending at reference_date.
# Temporary: 12 Jul 2026. Later set AS_OF_DATE = None for today's date.
AS_OF_DATE: str | None = None  # use get_as_of_date() (today) when None
PANEL_25D_SOLD_WINDOW_DAYS = 25
PANEL_SOLD_WINDOW_DAYS = 35
PANEL_60D_SOLD_WINDOW_DAYS = 60
PANEL_100D_SOLD_WINDOW_DAYS = 100

# EBO site ids (store_type fallback ke liye)
EBO_SITE_IDS = {"528", "964", "972", "FGPL-MALLESHWARAM", "FGPL-VADODRA", "FGPL-KURLA"}

# One bucket per ISO week (Monday week_start). Do NOT group by (year, month, week) —
# the same ISO week can span two calendar months, which was splitting weekly qty wrong.
PART_KEYS = ["site_id", "site_name", "barcode", "MRP", "season"]
GROUP_KEYS = [*PART_KEYS, "week_start"]

# Final export column order (size/sku filled later by 4_add_soh_weekly.py if used).
WEEKLY_OUTPUT_COLUMNS = [
    "site_id",
    "site_name",
    "barcode",
    "MRP",
    "season",
    "year",
    "month",
    "week",
    "last_purchase_date",
    "total_purchase_qty",
    "total_sold_qty",
    "total_net_sales",
    "weekly_avg_discount",
    "weekly_avg_discount_pct",
    "week_start",
    "Sum_of_Sales",
    "last_selling_date",
    "days_since_last_sale",
    "early_sale_bonus",
    "priority_score_raw",
    "priority_expl_weighted_qty_sum",
    "priority_expl_avg_sale_dow",
    "priority_expl_timing_bonus",
    "priority_score",
    "priority_scale_min_raw",
    "priority_scale_max_raw",
    "sold_qty_prev_25d",
    "sold_qty_prev_35d",
    "avg_sold_qty_35d",
    "sold_qty_prev_60d",
    "sold_qty_prev_100d",
    "total_sold_qty_num",
    "sold_qty_prev_5w",
    "avg_sold_qty_5w",
    "lag1_sold_qty",
    "lag2_sold_qty",
    "lag3_sold_qty",
    "lag4_sold_qty",
    "lag5_sold_qty",
    "roll3_avg_sold_qty",
    "roll4_avg_sold_qty",
    "weekly_sold_speed",
    "priority_expl_formula",
    "priority_rank",
    "size",
    "sku",
    "store_type",
]


def derive_store_type(site_name: object, site_id: object | None = None) -> str:
    """Classify: EBO / PT / SSL / CENTRO / ICONIC / FASHION_FACTORY. (fallback only)"""
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


def _finalize_weekly_export(df: pd.DataFrame) -> pd.DataFrame:
    """Only requested columns, fixed order; placeholders for size/sku."""
    out = df.copy()
    for c in WEEKLY_OUTPUT_COLUMNS:
        if c not in out.columns:
            out[c] = "" if c in ("size", "sku", "store_type") else np.nan
    return out[WEEKLY_OUTPUT_COLUMNS]


def _col_key(name: str) -> str:
    return re.sub(r"[\s_]+", "_", str(name).strip().lower()).strip("_")


def _column_lookup(df: pd.DataFrame) -> dict[str, str]:
    """lower_snake_key -> actual column name"""
    return {_col_key(c): c for c in df.columns}


def _resolve_col(lk: dict[str, str], *aliases: str) -> str | None:
    for a in aliases:
        k = _col_key(a)
        if k in lk:
            return lk[k]
    return None


def align_input_columns_to_weekly_schema(df: pd.DataFrame) -> None:
    """
    Rename common headers (incl. snake_case exports) to names this script expects.
    Fixes all-zero weekly totals when file has qty_sold vs Sold_QTY, etc.
    """
    lk = _column_lookup(df)
    renames: dict[str, str] = {}
    used: set[str] = set()

    def take(dest: str, *aliases: str) -> None:
        nonlocal renames, used
        for a in aliases:
            src = _resolve_col(lk, a)
            if src is None or src in used:
                continue
            used.add(src)
            if src != dest:
                renames[src] = dest
            return

    take("date", "date", "sale_date", "transaction_date")
    take("site_id", "site_id", "Site_ID")
    take("site_name", "site_name", "Site_Name")
    take("barcode", "barcode", "Barcode")
    take("Sold_QTY", "Sold_QTY", "Qty_Sold", "qty_sold", "sold_qty")
    take("net_sales_value", "net_sales_value", "Net_sales_amount", "net_sales_amount")
    take("MRP", "MRP", "mrp")
    take("discount_amt", "discount_amt", "discount_amount")
    take("discount_percentage", "discount_percentage", "discount_pct")
    take("last_purchase_date", "last_purchase_date", "max_dc_date", "dc_date")
    take("total_purchase_qty", "total_purchase_qty", "total_invoice_qty")
    take("season", "season", "SEASON")
    take("store_type", "store_type", "Store_Type", "STORE TYPE")

    if renames:
        df.rename(columns=renames, inplace=True)


def _to_num(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series([0] * len(df), index=df.index, dtype="float64")
    return pd.to_numeric(df[col], errors="coerce").fillna(0)


def _normalize_part_key_columns(frame: pd.DataFrame) -> None:
    """Align merge keys with add_sale_through / downstream (strip, case on ids)."""
    frame["site_id"] = frame["site_id"].astype(str).str.strip().str.upper()
    frame["site_name"] = frame["site_name"].astype(str).str.strip()
    frame["barcode"] = frame["barcode"].astype(str).str.strip().str.upper()
    frame["season"] = frame["season"].astype(str).str.strip()


def financial_year_start(reference_date: pd.Timestamp) -> pd.Timestamp:
    """1 April of the financial year that contains reference_date."""
    ref = pd.Timestamp(reference_date).normalize()
    year = int(ref.year) if int(ref.month) >= 4 else int(ref.year) - 1
    return pd.Timestamp(year=year, month=4, day=1)


def panel_last_nd_sold_by_line(
    df_sales: pd.DataFrame,
    reference_date: pd.Timestamp,
    window_days: int,
    out_col: str,
) -> pd.DataFrame:
    """Sum Sold_QTY over last `window_days` calendar days (inclusive of reference_date)."""
    ref = pd.Timestamp(reference_date).normalize()
    lookback_days = max(int(window_days) - 1, 0)
    start = ref - pd.Timedelta(days=lookback_days)
    day = df_sales["date"].dt.normalize()
    mask = df_sales["date"].notna() & (day >= start) & (day <= ref)
    if not mask.any():
        return pd.DataFrame(columns=[*PART_KEYS, out_col])
    out = (
        df_sales.loc[mask]
        .groupby(PART_KEYS, as_index=False, sort=False)["Sold_QTY"]
        .sum()
        .rename(columns={"Sold_QTY": out_col})
    )
    return out


def panel_last_35d_sold_by_line(df_sales: pd.DataFrame, reference_date: pd.Timestamp) -> pd.DataFrame:
    """Sum Sold_QTY over last PANEL_SOLD_WINDOW_DAYS (inclusive of reference_date)."""
    return panel_last_nd_sold_by_line(
        df_sales, reference_date, PANEL_SOLD_WINDOW_DAYS, "sold_qty_prev_35d"
    )


def _rolling_prev_5w_vectorized(agg: pd.DataFrame) -> pd.Series:
    """
    Last-5-calendar-week rolling sum of total_sold_qty_num per PART_KEYS.
    Gaps (missing weeks) count as 0 — same result as reindex+rolling(5), but
    fully vectorized (no per-group apply) so it scales to lakhs of rows.
    """
    keys_cols = [*PART_KEYS, "week_start"]
    x = agg.copy()
    x["week_start"] = pd.to_datetime(x["week_start"], errors="coerce")
    lookup = x.groupby(keys_cols)["total_sold_qty_num"].sum()

    total = np.zeros(len(x), dtype="float64")
    base = x[keys_cols]
    for off in range(5):  # current week + previous 4 calendar weeks
        shifted = base.copy()
        shifted["week_start"] = shifted["week_start"] - pd.to_timedelta(7 * off, unit="D")
        idx = pd.MultiIndex.from_frame(shifted[keys_cols])
        total += pd.Series(idx.map(lookup)).fillna(0).to_numpy()
    return pd.Series(total, index=agg.index)


def add_lag_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["week_start"] = pd.to_datetime(out["week_start"])
    out["_lag_qty"] = pd.to_numeric(out["total_sold_qty"], errors="coerce").fillna(0)

    # Same meaning as 6_ml.py: lag1 is the current/latest week sale for next-week prediction.
    # Missing calendar weeks are treated as 0; negative return buckets are preserved.
    qty_lookup = out.groupby([*PART_KEYS, "week_start"], sort=False)["_lag_qty"].sum()

    for lag in [1, 2, 3, 4, 5]:
        lookup_keys = out[[*PART_KEYS, "week_start"]].copy()
        lookup_keys["week_start"] = lookup_keys["week_start"] - pd.to_timedelta(
            7 * (lag - 1), unit="D"
        )
        lookup_index = pd.MultiIndex.from_frame(lookup_keys[[*PART_KEYS, "week_start"]])
        out[f"lag{lag}_sold_qty"] = pd.Series(
            lookup_index.map(qty_lookup), index=out.index
        ).fillna(0)

    out["roll3_avg_sold_qty"] = out[
        ["lag1_sold_qty", "lag2_sold_qty", "lag3_sold_qty"]
    ].mean(axis=1)
    out["roll4_avg_sold_qty"] = out[
        ["lag1_sold_qty", "lag2_sold_qty", "lag3_sold_qty", "lag4_sold_qty"]
    ].mean(axis=1)
    out["weekly_sold_speed"] = np.where(
        out["total_sold_qty"] > 0, out["total_sold_qty"] / 7.0, 0.0
    )
    return out.drop(columns=["_lag_qty"])


def _log(msg: str) -> None:
    print(msg, flush=True)


def main() -> None:
    try:
        from pipeline_inputs import load_tabular_prefer_csv
    except ImportError:
        from pipeline.pipeline_inputs import load_tabular_prefer_csv  # type: ignore

    _log("[1/6] reading SALES_REPORT (prefer CSV) ...")
    df = load_tabular_prefer_csv(INPUT_FILE, label="SALES_REPORT")
    _log(f"      read {len(df):,} rows")
    df.columns = df.columns.str.strip()
    align_input_columns_to_weekly_schema(df)

    if "date" not in df.columns:
        raise ValueError(
            f"Need a date column. Found: {list(df.columns)}"
        )

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.loc[df["date"].notna()].copy()
    file_latest = df["date"].max()
    if pd.isna(file_latest):
        raise ValueError("No valid transaction dates in input; cannot compute 35-day panel or as-of metrics.")
    file_latest = pd.Timestamp(file_latest).normalize()
    if AS_OF_DATE:
        reference_date = pd.to_datetime(AS_OF_DATE, errors="raise").normalize()
    else:
        from pipeline_inputs import get_as_of_date

        reference_date = pd.to_datetime(get_as_of_date(), errors="raise").normalize()
        _log(f"      AS_OF_DATE = today ({reference_date.date()})")
    # Support both old `dc_date` and new `last_purchase_date` naming.
    if "last_purchase_date" in df.columns:
        df["last_purchase_date"] = pd.to_datetime(
            df["last_purchase_date"], errors="coerce"
        )
    else:
        df["last_purchase_date"] = pd.NaT
    if "dc_date" in df.columns:
        df["dc_date"] = pd.to_datetime(df["dc_date"], errors="coerce")
    else:
        df["dc_date"] = df["last_purchase_date"]
    if "total_purchase_qty" not in df.columns:
        df["total_purchase_qty"] = 0
    df["total_purchase_qty"] = pd.to_numeric(
        df["total_purchase_qty"], errors="coerce"
    ).fillna(0)

    # ISO week bucket from transaction date only (ignore file year/month/iso_week for grouping).
    _d = df["date"].dt.normalize()
    df["week_start"] = _d - pd.to_timedelta(_d.dt.dayofweek, unit="D")
    iso = df["week_start"].dt.isocalendar()
    df["year"] = pd.to_numeric(iso["year"], errors="coerce").astype("Int64")
    df["week"] = pd.to_numeric(iso["week"], errors="coerce").astype("Int64")
    df["month"] = df["week_start"].dt.month.astype("Int64")
    if "season" not in df.columns:
        df["season"] = ""
    df["season"] = df["season"].astype(str).str.strip()
    if "store_type" not in df.columns:
        df["store_type"] = df.apply(
            lambda r: derive_store_type(r.get("site_name"), r.get("site_id")),
            axis=1,
        )
    else:
        df["store_type"] = df["store_type"].astype(str).str.strip().str.upper()
        empty_type = df["store_type"].isin(["", "NAN", "NONE"])
        df.loc[empty_type, "store_type"] = df.loc[empty_type].apply(
            lambda r: derive_store_type(r.get("site_name"), r.get("site_id")),
            axis=1,
        )

    df["Sold_QTY"] = _to_num(df, "Sold_QTY")
    df["net_sales_value"] = _to_num(df, "net_sales_value")
    df["discount_amt"] = _to_num(df, "discount_amt")
    df["discount_percentage"] = _to_num(df, "discount_percentage")
    df["MRP"] = _to_num(df, "MRP")

    # Calculate day of week (Monday=1, Sunday=7) and weighted quantity
    df["day_of_week"] = df["date"].dt.dayofweek + 1
    df["weighted_qty"] = df["Sold_QTY"] * df["day_of_week"]

    _normalize_part_key_columns(df)

    last_selling_lookup = (
        df[df["Sold_QTY"] > 0]
        .groupby(["site_id", "barcode"], as_index=False)["date"]
        .max()
        .rename(columns={"date": "last_selling_date"})
    )

    # Sum_of_Sales is pieces for this store + barcode from FY start through today,
    # not the whole sales file and not just the current week.
    fy_start = financial_year_start(reference_date)
    sale_day = df["date"].dt.normalize()
    fy_sales = df.loc[sale_day.ge(fy_start) & sale_day.le(reference_date)]
    _log(
        f"      Sum_of_Sales window {fy_start.date()} to {reference_date.date()} "
        f"({len(fy_sales):,} rows)"
    )
    sum_sales_lookup = (
        fy_sales.groupby(["site_id", "barcode"], as_index=False)["Sold_QTY"]
        .sum()
        .rename(columns={"Sold_QTY": "Sum_of_Sales"})
    )

    _log("[2/6] weekly groupby aggregation ...")
    agg = df.groupby(GROUP_KEYS, as_index=False).agg(
        last_purchase_date=("last_purchase_date", "max"),
        total_purchase_qty=("total_purchase_qty", "max"),
        total_sold_qty=("Sold_QTY", "sum"),
        total_net_sales=("net_sales_value", "sum"),
        weekly_avg_discount=("discount_amt", "mean"),
        weekly_avg_discount_pct=("discount_percentage", "mean"),
        sum_weighted_qty=("weighted_qty", "sum"),
    )
    _log(f"      {len(agg):,} weekly rows")
    agg["week_start"] = pd.to_datetime(agg["week_start"], errors="coerce")
    _iso_agg = agg["week_start"].dt.isocalendar()
    agg["year"] = pd.to_numeric(_iso_agg["year"], errors="coerce").astype("Int64")
    agg["week"] = pd.to_numeric(_iso_agg["week"], errors="coerce").astype("Int64")
    agg["month"] = agg["week_start"].dt.month.astype("Int64")

    agg = agg.merge(sum_sales_lookup, on=["site_id", "barcode"], how="left")
    agg = agg.merge(last_selling_lookup, on=["site_id", "barcode"], how="left")
    site_type = df.groupby("site_id", as_index=False)["store_type"].first()
    agg = agg.merge(site_type, on="site_id", how="left")
    agg["Sum_of_Sales"] = pd.to_numeric(agg["Sum_of_Sales"], errors="coerce").fillna(0)
    days_since = (
        reference_date - pd.to_datetime(agg["last_selling_date"], errors="coerce")
    ).dt.days
    agg["days_since_last_sale"] = days_since.where(days_since.notna(), "No Sales")
    has_days = agg["days_since_last_sale"] != "No Sales"
    agg.loc[has_days, "days_since_last_sale"] = (
        pd.to_numeric(agg.loc[has_days, "days_since_last_sale"], errors="coerce")
        .clip(lower=0)
        .astype(int)
    )

    # Keep early-sale signal only; priority scoring is now computed in 6_ml.py.
    agg["avg_day_of_sale"] = np.where(
        agg["total_sold_qty"] > 0,
        agg["sum_weighted_qty"] / agg["total_sold_qty"],
        7.0
    )
    agg["early_sale_bonus"] = (7.0 - agg["avg_day_of_sale"]) / 7.0
    # Keep explanatory timing columns for downstream visibility.
    agg["priority_expl_weighted_qty_sum"] = agg["sum_weighted_qty"]
    agg["priority_expl_avg_sale_dow"] = agg["avg_day_of_sale"]
    agg["priority_expl_timing_bonus"] = agg["early_sale_bonus"]

    # Identify latest row for each group to mask historical metrics
    agg["is_latest"] = agg.groupby(PART_KEYS)["week_start"].transform("max") == agg["week_start"]

    # Mask timing explainers for historical rows
    agg.loc[~agg["is_latest"], "early_sale_bonus"] = 0
    agg.loc[~agg["is_latest"], "priority_expl_weighted_qty_sum"] = 0
    agg.loc[~agg["is_latest"], "priority_expl_avg_sale_dow"] = 0
    agg.loc[~agg["is_latest"], "priority_expl_timing_bonus"] = 0
    agg = agg.drop(columns=["sum_weighted_qty", "avg_day_of_sale"])
    # Priority columns retained for schema compatibility; values are assigned in 6_ml.py.
    agg["priority_score_raw"] = 0.0
    agg["priority_score"] = 0.0
    agg["priority_scale_min_raw"] = np.nan
    agg["priority_scale_max_raw"] = np.nan

    _log("[3/6] panel windows (25/35/60/100d) ...")
    panel_25d = panel_last_nd_sold_by_line(
        df, reference_date, PANEL_25D_SOLD_WINDOW_DAYS, "sold_qty_prev_25d"
    )
    if not panel_25d.empty:
        agg = agg.merge(panel_25d, on=PART_KEYS, how="left")
    else:
        agg["sold_qty_prev_25d"] = 0.0
    agg["sold_qty_prev_25d"] = pd.to_numeric(agg["sold_qty_prev_25d"], errors="coerce").fillna(0)
    agg.loc[~agg["is_latest"], "sold_qty_prev_25d"] = 0

    panel_35d = panel_last_35d_sold_by_line(df, reference_date)
    if not panel_35d.empty:
        agg = agg.merge(panel_35d, on=PART_KEYS, how="left")
    else:
        agg["sold_qty_prev_35d"] = 0.0
    agg["sold_qty_prev_35d"] = pd.to_numeric(agg["sold_qty_prev_35d"], errors="coerce").fillna(0)

    # Mask historical rows for 35d metric
    agg.loc[~agg["is_latest"], "sold_qty_prev_35d"] = 0
    agg["avg_sold_qty_35d"] = agg["sold_qty_prev_35d"] / 5.0

    panel_60d = panel_last_nd_sold_by_line(
        df, reference_date, PANEL_60D_SOLD_WINDOW_DAYS, "sold_qty_prev_60d"
    )
    if not panel_60d.empty:
        agg = agg.merge(panel_60d, on=PART_KEYS, how="left")
    else:
        agg["sold_qty_prev_60d"] = 0.0
    agg["sold_qty_prev_60d"] = pd.to_numeric(agg["sold_qty_prev_60d"], errors="coerce").fillna(0)
    agg.loc[~agg["is_latest"], "sold_qty_prev_60d"] = 0

    panel_100d = panel_last_nd_sold_by_line(
        df, reference_date, PANEL_100D_SOLD_WINDOW_DAYS, "sold_qty_prev_100d"
    )
    if not panel_100d.empty:
        agg = agg.merge(panel_100d, on=PART_KEYS, how="left")
    else:
        agg["sold_qty_prev_100d"] = 0.0
    agg["sold_qty_prev_100d"] = pd.to_numeric(agg["sold_qty_prev_100d"], errors="coerce").fillna(0)
    agg.loc[~agg["is_latest"], "sold_qty_prev_100d"] = 0

    _log("[4/6] rolling prev-5w ...")
    agg["total_sold_qty_num"] = pd.to_numeric(agg["total_sold_qty"], errors="coerce").fillna(0)
    agg["sold_qty_prev_5w"] = _rolling_prev_5w_vectorized(agg).fillna(0)

    # Mask historical rows for 5w metric
    agg.loc[~agg["is_latest"], "sold_qty_prev_5w"] = 0
    agg["avg_sold_qty_5w"] = agg["sold_qty_prev_5w"] / 5.0

    # GROUP_KEYS can yield two rows for the same PART_KEYS + week_start when raw
    # year/month/week disagree but ISO week_start matches (e.g. W14 in Mar vs Apr).
    # is_latest is then True on both, so panel sums double-count — keep metrics on one row.
    latest = agg["is_latest"]
    if bool(latest.any()):
        keep_idx = (
            agg.loc[latest]
            .sort_values([*PART_KEYS, "week_start"])
            .drop_duplicates(subset=PART_KEYS, keep="first")
            .index
        )
        drop_idx = agg.index[latest].difference(keep_idx)
        panel_cols = [
            "sold_qty_prev_25d",
            "sold_qty_prev_35d",
            "avg_sold_qty_35d",
            "sold_qty_prev_60d",
            "sold_qty_prev_100d",
            "sold_qty_prev_5w",
            "avg_sold_qty_5w",
        ]
        agg.loc[drop_idx, panel_cols] = 0.0
        prio_dup_cols = [
            "priority_score",
            "priority_score_raw",
            "early_sale_bonus",
            "priority_expl_weighted_qty_sum",
            "priority_expl_avg_sale_dow",
            "priority_expl_timing_bonus",
        ]
        agg.loc[drop_idx, prio_dup_cols] = 0.0

    _log("[5/6] lag features ...")
    agg = add_lag_features(agg)

    # Mask weekly_sold_speed for historical weeks (as requested)
    agg.loc[~agg["is_latest"], "weekly_sold_speed"] = 0.0

    # One-line formula for Excel / UI tooltips (same text on every row).
    agg["priority_expl_formula"] = (
        "priority_score is assigned in 6_ml.py using lag1_sold_qty + early_sale_bonus."
    )

    # Ranking is also computed downstream where priority_score is assigned.
    agg["priority_rank"] = 0

    agg = agg.drop(columns=["is_latest"])

    _log("[6/6] writing output (csv + xlsx) ...")
    out = _finalize_weekly_export(agg)
    try:
        from pipeline_inputs import filter_df_by_store_type
    except ImportError:
        from pipeline.pipeline_inputs import filter_df_by_store_type  # type: ignore

    out = filter_df_by_store_type(out, col="store_type", log_prefix="      ")
    try:
        from pipeline_inputs import save_output_pair
    except ImportError:
        from pipeline.pipeline_inputs import save_output_pair  # type: ignore
    save_output_pair(out, OUTPUT_CSV, sheet_name="weekly")
    print(f"Rows           : {len(out):,}")
    print(f"Reference date : {reference_date.date()}")
    print(f"Done -> {OUTPUT_XLSX}")
    print(f"Saved: {OUTPUT_CSV}")


if __name__ == "__main__":
    main()

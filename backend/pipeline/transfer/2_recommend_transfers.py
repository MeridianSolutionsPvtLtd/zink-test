from __future__ import annotations
 
import logging

from pathlib import Path

from typing import Iterable
 
import pandas as pd
 
ALL_SIZES = ["1XS", "2S", "3M", "4L", "5XL", "6XXL"]
 
BASE_DIR = Path(__file__).resolve().parents[1]

LOGGER_NAME = "recommend_transfers"

log = logging.getLogger(LOGGER_NAME)
 
# ── Input paths ──────────────────────────────────────────────────────────────

IN_RECEIVERS  = BASE_DIR / "transfer" / "output" / "potential_receivers.csv"

IN_DONORS     = BASE_DIR / "transfer" / "output" / "potential_donors.csv"

IN_STORE_SOH  = BASE_DIR / "output_files" / "store_soh_new.csv"
 
# ── Output paths ─────────────────────────────────────────────────────────────

OUT_DIR       = BASE_DIR / "transfer" / "output"

OUT_RECOMMENDED           = OUT_DIR / "recommended_transfers.csv"

OUT_RECOMMENDED_GROUPED   = OUT_DIR / "recommended_transfers_grouped.csv"

OUT_RECOMMENDED_SIZEWISE  = OUT_DIR / "recommended_transfers_sizewise.csv"

OUT_RECOMMENDED_SIZEWISE_XLSX = OUT_DIR / "recommended_transfers_sizewise.xlsx"

OUT_SUMMARY   = OUT_DIR / "recommended_transfers_summary.txt"

OUT_RUN_LOG   = OUT_DIR / "recommended_transfers_run.log"
 
# Round-wise sizewise snapshots

OUT_ROUND_SIZEWISE_TEMPLATE = OUT_DIR / "round_{round}_sizewise.csv"
 
NUM_ROUNDS = 2   # ← change karo agar zyada rounds chahiye

# PT / CENTRO / ICONIC / EBO → same store type + same state only.
# City vs state priority handled by location_score (city=3, state=2).
# SSL → same store type; city first, then state, then other states allowed.
SAME_STATE_ONLY_STORE_TYPES = frozenset({"PT", "CENTRO", "ICONIC", "EBO"})
 
 
# ─────────────────────────────────────────────────────────────────────────────

# Logging

# ─────────────────────────────────────────────────────────────────────────────

def _setup_run_logging(log_path: Path) -> None:

    log_path.parent.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    logger = logging.getLogger(LOGGER_NAME)

    logger.setLevel(logging.INFO)

    logger.handlers.clear()

    fh = logging.FileHandler(log_path, encoding="utf-8")

    fh.setFormatter(fmt)

    sh = logging.StreamHandler()

    sh.setFormatter(fmt)

    logger.addHandler(fh)

    logger.addHandler(sh)

    logger.propagate = False
 
 
# ─────────────────────────────────────────────────────────────────────────────

# Small helpers

# ─────────────────────────────────────────────────────────────────────────────

def _to_num(series: pd.Series) -> pd.Series:

    return pd.to_numeric(series, errors="coerce").fillna(0.0)
 
def _clean_text(series: pd.Series) -> pd.Series:

    return series.astype(str).str.strip().replace({"nan": "", "None": "", "<NA>": "", "NaT": ""})
 
def _norm_days_since_last_sale(series: pd.Series) -> pd.Series:

    s = _clean_text(series).str.upper()

    return pd.to_numeric(s.replace({"NO SALES": "999"}), errors="coerce")
 
def _sanitize_numeric(df: pd.DataFrame, cols: Iterable[str]) -> pd.DataFrame:

    out = df.copy()

    for c in cols:

        if c in out.columns:

            out[c] = _to_num(out[c])

    return out
 
def _normalize_barcode(series: pd.Series) -> pd.Series:

    return _clean_text(series).str.replace(".0", "", regex=False)
 
def _norm_text_scalar(v: object) -> str:

    return str(v).strip() if v is not None else ""
 
def _safe_to_csv(df: pd.DataFrame, path: Path) -> Path:

    try:

        df.to_csv(path, index=False, encoding="utf-8-sig")

        return path

    except PermissionError:

        fallback = path.with_name(f"{path.stem}_latest{path.suffix}")

        df.to_csv(fallback, index=False, encoding="utf-8-sig")

        return fallback
 
def _safe_to_excel(df: pd.DataFrame, path: Path) -> Path:

    try:

        df.to_excel(path, index=False)

        return path

    except PermissionError:

        fallback = path.with_name(f"{path.stem}_latest{path.suffix}")

        df.to_excel(fallback, index=False)

        return fallback
 
 
# ─────────────────────────────────────────────────────────────────────────────

# Store SOH lookup

# ─────────────────────────────────────────────────────────────────────────────

def _build_store_soh_lookup(path: Path) -> dict[tuple[str, str], float]:

    if not path.exists():

        return {}

    s = pd.read_csv(path, low_memory=False)

    s.columns = s.columns.str.strip()

    site_col = "Site_id" if "Site_id" in s.columns else ("site_id" if "site_id" in s.columns else None)

    bc_col   = "Barcode" if "Barcode" in s.columns else ("barcode" if "barcode" in s.columns else None)

    qty_col  = ("Current_Stock_Qty" if "Current_Stock_Qty" in s.columns

                else ("store_soh" if "store_soh" in s.columns else None))

    if site_col is None or bc_col is None or qty_col is None:

        return {}

    x = s[[site_col, bc_col, qty_col]].copy()

    x["site_id"] = _clean_text(x[site_col]).str.upper()

    x["barcode"] = _normalize_barcode(x[bc_col])

    x["soh"]     = _to_num(x[qty_col])

    x = x[(x["site_id"] != "") & (x["barcode"] != "")]

    agg = x.groupby(["site_id", "barcode"])["soh"].sum()

    return {(site, bc): float(soh) for (site, bc), soh in agg.items()}
 
 
# ─────────────────────────────────────────────────────────────────────────────

# Data prep

# ─────────────────────────────────────────────────────────────────────────────

def _prepare_base(df: pd.DataFrame, is_receiver: bool) -> pd.DataFrame:

    x = df.copy()

    x.columns = x.columns.str.strip()

    for old, new in [

        ("total_sales",            "sum_of_sales"),

        ("last_week_sales",        "lag1_sold_qty"),

        ("last_35day_sold_qty",    "sold_qty_prev_35d"),

        ("days_from_last_transfer","days_since_last_transfer"),

        ("days_from_last_sales",   "days_since_last_sale"),

    ]:

        if new not in x.columns and old in x.columns:

            x[new] = x[old]
 
    for c in ["site_id","site_name","sku","size","barcode","city","state","store_type"]:

        if c not in x.columns:

            x[c] = ""

        x[c] = _clean_text(x[c])

    x["size"]    = x["size"].str.upper()

    x["site_id"] = x["site_id"].str.upper()

    x["sku"]     = x["sku"].str.upper()

    x["barcode"] = _normalize_barcode(x["barcode"])

    x = _sanitize_numeric(x, [

        "store_soh","wh_soh","lag1_sold_qty","sold_qty_prev_35d",

        "sum_of_sales","priority_score","qty_still_needed","days_since_last_transfer",

    ])

    if "days_since_last_sale" not in x.columns:

        x["days_since_last_sale"] = 0

    x["days_since_last_sale_num"] = _norm_days_since_last_sale(x["days_since_last_sale"]).fillna(0)

    if is_receiver and "qty_still_needed" not in x.columns:

        x["qty_still_needed"] = 0
 
    missing_bc = x["barcode"] == ""

    if missing_bc.any():

        log.warning("Missing barcode for %d rows (site-sku-size)", missing_bc.sum())

    return x
 
 
# ─────────────────────────────────────────────────────────────────────────────

# Wide pivot

# ─────────────────────────────────────────────────────────────────────────────

def build_wide(base: pd.DataFrame, prefix: str) -> pd.DataFrame:

    idx = ["site_id", "sku"]

    def _piv(col, agg):

        fill = "" if agg == "first" else 0

        return (base.pivot_table(index=idx, columns="size", values=col, aggfunc=agg)

                .reindex(columns=ALL_SIZES).fillna(fill).reset_index())
 
    p_barcode = _piv("barcode",               "first")

    p_soh     = _piv("store_soh",             "max")

    p_sales   = _piv("sum_of_sales",          "max")

    p_lag1    = _piv("lag1_sold_qty",         "max")

    p_sold35  = _piv("sold_qty_prev_35d",     "max")

    p_days    = _piv("days_since_last_sale_num","max")

    p_req     = _piv("qty_still_needed",      "max")
 
    out = p_barcode[idx].copy()

    for s in ALL_SIZES:

        out[f"{prefix}_barcode_{s}"] = p_barcode[s]

        out[f"{prefix}_soh_{s}"]     = p_soh[s]

        out[f"{prefix}_sales_{s}"]   = p_sales[s]

        out[f"{prefix}_lag1_{s}"]    = p_lag1[s]

        out[f"{prefix}_sold35_{s}"]  = p_sold35[s]

        out[f"{prefix}_days_{s}"]    = p_days[s]

        out[f"{prefix}_req_{s}"]     = p_req[s]

    return out
 
 
def enrich_sizes(primary: pd.DataFrame, fallback: pd.DataFrame, prefix: str) -> pd.DataFrame:

    out = primary.merge(fallback, on=["site_id","sku"], how="left", suffixes=("","_fb"))

    for s in ALL_SIZES:

        out[f"{prefix}_barcode_{s}"] = out[f"{prefix}_barcode_{s}"].where(

            _clean_text(out[f"{prefix}_barcode_{s}"]).ne(""), out.get(f"fb_barcode_{s}", ""))

        out[f"{prefix}_soh_{s}"]   = out[f"{prefix}_soh_{s}"].where(out[f"{prefix}_soh_{s}"] > 0,   out.get(f"fb_soh_{s}", 0))

        out[f"{prefix}_sales_{s}"] = out[f"{prefix}_sales_{s}"].where(out[f"{prefix}_sales_{s}"] > 0, out.get(f"fb_sales_{s}", 0))

        out[f"{prefix}_days_{s}"]  = out[f"{prefix}_days_{s}"].where(out[f"{prefix}_days_{s}"] > 0,  out.get(f"fb_days_{s}", 0))

    return out.drop(columns=[c for c in out.columns if c.startswith("fb_")])
 
 
# ─────────────────────────────────────────────────────────────────────────────

# Greedy fill — STRICT STACK TRANSFER

# ─────────────────────────────────────────────────────────────────────────────

def _greedy_fill(joined: pd.DataFrame) -> tuple[list[dict], dict]:

    """

    STRICT STACK TRANSFER LOGIC:

    ────────────────────────────

    Eligibility (STRICT):

        - For every REQUIRED size (req_qty > 0) of the receiver, donor MUST have

          live_soh >= 1 of that exact size. If even ONE required size is missing

          from donor's stock, donor is REJECTED (no PARTIAL fallback).
 
    Transfer (STACK):

        - For each REQUIRED size  : send min(donor_soh, required_qty)

        - For each OTHER size donor has (soh >= 1, not in required set):

              send min(donor_soh, req_max)

          where req_max = max required_qty across receiver's needed sizes.

        - This guarantees the donor's full available stack is consolidated

          and sent as a single package, capped by the receiver's biggest need.
 
    Pairing:

        - One receiver+sku pair → exactly ONE donor (best match).

        - Donor SOH is tracked live; only actually-transferred qty is deducted.
 
    Best-donor selection order:

        1. coverage_score      : count of required sizes donor can hit fully

                                 (donor_soh >= required_qty)   [DESC]

        2. location_score      : city(3) > state(2) > other(1) [DESC]

        3. donor_priority      : no_sales(1) > sales(2)        [ASC]

        4. donor_sold_qty_35d  : prefer lower-selling donors   [DESC currently

                                 — preserving original behaviour where higher

                                 sold35 was preferred; flip sign if needed]

        5. stock_age_score                                     [DESC]

    """
 
    # ── Step 1: shared live SOH tracker ──────────────────────────────────────

    donor_soh_live: dict[tuple[str, str], dict[str, float]] = {}

    for row in joined.to_dict(orient="records"):

        d_key = (str(row["donor_site_id"]).upper(), str(row["donor_sku"]).upper())

        if d_key not in donor_soh_live:

            donor_soh_live[d_key] = {

                s: float(row.get(f"don_soh_{s}", 0) or 0) for s in ALL_SIZES

            }
 
    # Group candidates by receiver (site_id, sku)

    candidates_by_receiver: dict[tuple[str, str], list[dict]] = {}

    for row in joined.to_dict(orient="records"):

        r_key = (str(row["site_id"]).upper(), str(row["sku"]).upper())

        candidates_by_receiver.setdefault(r_key, []).append(row)
 
    # ── Step 2: Ordered list of receivers by receiver_priority ───────────────

    joined_sorted_rec = joined.sort_values("receiver_priority", ascending=True)

    seen_r_keys: set[tuple[str, str]] = set()

    ordered_r_keys: list[tuple[str, str]] = []

    for row in joined_sorted_rec.to_dict(orient="records"):

        r_key = (str(row["site_id"]).upper(), str(row["sku"]).upper())

        if r_key not in seen_r_keys:

            seen_r_keys.add(r_key)

            ordered_r_keys.append(r_key)
 
    result_rows: list[dict] = []

    rejected_count = 0
 
    for r_key in ordered_r_keys:

        candidates = candidates_by_receiver[r_key]

        eligible_candidates: list[dict] = []
 
        for cand in candidates:

            d_key = (str(cand["donor_site_id"]).upper(), str(cand["donor_sku"]).upper())

            live_soh = donor_soh_live.get(d_key, {s: 0.0 for s in ALL_SIZES})
 
            # Required sizes from receiver (req > 0) and their required qty

            required: dict[str, float] = {}

            for s in ALL_SIZES:

                q = float(cand.get(f"req_{s}_qty", 0) or 0)

                if q > 0:

                    required[s] = q

            if not required:

                continue
 
            # ── STRICT eligibility: donor MUST have >=1 of EVERY required size ──

            if not all(live_soh.get(s, 0.0) >= 1 for s in required):

                continue  # REJECT this donor entirely
 
            # coverage_score = how many required sizes donor can fully satisfy

            coverage_score = sum(

                1 for s, need_q in required.items()

                if live_soh.get(s, 0.0) >= need_q

            )
 
            cand_copy = dict(cand)

            cand_copy["_coverage_score"] = coverage_score

            cand_copy["_required"]       = required

            eligible_candidates.append(cand_copy)
 
        if not eligible_candidates:

            rejected_count += 1

            continue  # No eligible donor for this receiver in this round
 
        # Pick best donor

        best_cand = sorted(

            eligible_candidates,

            key=lambda c: (

                -int(c["_coverage_score"]),

                -float(c.get("location_score", 1)),

                float(c.get("donor_priority", 99999)),

                -float(c.get("donor_sold_qty_35d", 0)),

                -float(c.get("stock_age_score", 0))

            )

        )[0]
 
        d_key   = (str(best_cand["donor_site_id"]).upper(), str(best_cand["donor_sku"]).upper())

        live_soh = donor_soh_live[d_key]

        required: dict[str, float] = best_cand["_required"]
 
        # req_max = biggest required qty across receiver's needed sizes

        req_max = max(required.values())

        if req_max <= 0:

            req_max = 1.0
 
        # ── Build transfer: REQUIRED sizes first ─────────────────────────────

        transfer_sizes: dict[str, float] = {}

        for s, need_q in required.items():

            take_qty = min(live_soh.get(s, 0.0), need_q)

            if take_qty > 0:

                transfer_sizes[s] = take_qty
 
        # ── Build transfer: OTHER sizes donor has (stack transfer) ───────────

        # Per requirement: every available size from donor → minimum 1 unit,

        # capped by req_max (max required qty across receiver's needed sizes).

        for s in ALL_SIZES:

            if s in required:

                continue

            avail = live_soh.get(s, 0.0)

            if avail >= 1:

                take_qty = min(avail, req_max)

                if take_qty > 0:

                    transfer_sizes[s] = take_qty
 
        # ── Deduct only actually-transferred qty from donor SOH ──────────────

        for s, qty in transfer_sizes.items():

            donor_soh_live[d_key][s] = max(0.0, donor_soh_live[d_key][s] - qty)
 
        # All required sizes are satisfied (eligibility guaranteed this).

        # fill_type kept for backward compatibility downstream.

        # FULL = every required size received >= its required qty.

        all_required_full = all(

            transfer_sizes.get(s, 0.0) >= need_q

            for s, need_q in required.items()

        )

        fill_type = "FULL" if all_required_full else "PARTIAL"
 
        # actual_transferable_stacks_at_transfer = bottleneck on required set

        avail_on_needed = min(live_soh.get(s, 0.0) + transfer_sizes.get(s, 0.0)

                              for s in required)  # pre-deduction value

        # Note: above re-adds the just-deducted qty to reflect pre-transfer view.

        avail_on_needed = min(avail_on_needed, req_max)
 
        out_row = dict(best_cand)

        out_row["fill_type"] = fill_type

        out_row["actual_transferable_stacks_at_transfer"] = avail_on_needed

        for s in ALL_SIZES:

            out_row[f"transfer_{s}_qty"] = transfer_sizes.get(s, 0)

        out_row["transfer_qty_total"] = sum(transfer_sizes.values())
 
        # Clean temp keys

        out_row.pop("_coverage_score", None)

        out_row.pop("_required", None)
 
        result_rows.append(out_row)
 
    log.info(

        "  greedy_fill done | matched=%s | FULL=%s | PARTIAL=%s | rejected_receivers=%s",

        len(result_rows),

        sum(1 for r in result_rows if r["fill_type"] == "FULL"),

        sum(1 for r in result_rows if r["fill_type"] == "PARTIAL"),

        rejected_count,

    )

    return result_rows, donor_soh_live
 
 
# ─────────────────────────────────────────────────────────────────────────────

# Single round

# ─────────────────────────────────────────────────────────────────────────────

def _run_one_round(

    receivers: pd.DataFrame,

    donors: pd.DataFrame,

    already_receiver_sku: set[tuple[str, str]],

) -> pd.DataFrame:
 
    # Donor eligibility: every size of the SKU at this site must have had
    # zero sales for at least NO_SALE_DAYS (25) days — consistent with
    # the threshold used in 1_build_potential_donors_receivers.py.
    NO_SALE_DAYS = 25

    donor_days = donors.groupby(["site_id","sku"], as_index=False).agg(

        donor_days_eligible=("days_since_last_sale_num", "min")

    )

    eligible_days = donor_days[donor_days["donor_days_eligible"] >= NO_SALE_DAYS][["site_id","sku","donor_days_eligible"]]

    log.info("  eligible donor site+sku: %s", len(eligible_days))
 
    fallback_base = pd.concat([

        receivers[["site_id","sku","size","barcode","store_soh","sum_of_sales",

                   "lag1_sold_qty","sold_qty_prev_35d","days_since_last_sale_num","qty_still_needed"]],

        donors.assign(qty_still_needed=0)[

                  ["site_id","sku","size","barcode","store_soh","sum_of_sales",

                   "lag1_sold_qty","sold_qty_prev_35d","days_since_last_sale_num","qty_still_needed"]],

    ], ignore_index=True)

    fb_wide  = build_wide(fallback_base, "fb")

    don_wide = enrich_sizes(build_wide(donors.assign(qty_still_needed=0), "don"), fb_wide, "don")

    rec_wide = enrich_sizes(build_wide(receivers, "rec"), fb_wide, "rec")
 
    # ── Receiver aggregate ───────────────────────────────────────────────────

    receiver_meta = receivers.groupby(

        ["site_id","site_name","sku","city","state","store_type"], as_index=False

    ).agg(

        receiver_sold_qty_35d=("sold_qty_prev_35d","max"),

        priority_score=("priority_score","max"),

        receiver_store_soh=("store_soh","max"),

        receiver_days_since_last_sale=("days_since_last_sale_num","max"),

        receiver_last_week_sales=("lag1_sold_qty","max"),

        receiver_total_sales=("sum_of_sales","max"),

    )
 
    req = (receivers.pivot_table(index=["site_id","sku"], columns="size",

                                  values="qty_still_needed", aggfunc="max")

           .reindex(columns=ALL_SIZES).fillna(0).reset_index())

    for s in ALL_SIZES:

        req = req.rename(columns={s: f"req_{s}_qty"})

    req["required_stack_qty"] = req[[f"req_{s}_qty" for s in ALL_SIZES]].max(axis=1)

    req = req[req["required_stack_qty"] > 0]
 
    receiver_sku = (receiver_meta.merge(req, on=["site_id","sku"], how="inner")

                                  .merge(rec_wide, on=["site_id","sku"], how="left"))

    receiver_sku = receiver_sku.sort_values(

        ["receiver_sold_qty_35d","priority_score"], ascending=[False, False]

    ).reset_index(drop=True)

    receiver_sku["receiver_priority"] = receiver_sku.index + 1
 
    if already_receiver_sku:

        pair_key = list(zip(

            receiver_sku["site_id"].str.upper(),

            receiver_sku["sku"].str.upper(),

        ))

        receiver_sku = receiver_sku[[k not in already_receiver_sku for k in pair_key]].reset_index(drop=True)

        receiver_sku["receiver_priority"] = receiver_sku.index + 1

    log.info("  receiver_sku rows this round: %s", len(receiver_sku))
 
    # ── Donor aggregate ──────────────────────────────────────────────────────

    donor_meta = donors.groupby(

        ["site_id","site_name","sku","city","state","store_type","donor_source"], as_index=False

    ).agg(

        donor_store_soh=("store_soh","max"),

        donor_sold_qty_35d=("sold_qty_prev_35d","max"),

        donor_last_week_sales=("lag1_sold_qty","max"),

        donor_total_sales=("sum_of_sales","max"),

        days_from_last_transfer=("days_since_last_transfer","max"),

        days_from_last_sales=("days_since_last_sale_num","max"),

    )

    donor_sku = (donor_meta.merge(don_wide, on=["site_id","sku"], how="inner")

                            .merge(eligible_days, on=["site_id","sku"], how="inner")

                            .rename(columns={

                                "site_id":    "donor_site_id",

                                "site_name":  "donor_site_name",

                                "city":       "donor_city",

                                "state":      "donor_state",

                                "store_type": "donor_store_type",

                                "sku":        "donor_sku",

                            }))

    log.info("  donor_sku rows (eligible): %s", len(donor_sku))
 
    if receiver_sku.empty or donor_sku.empty:

        log.info("  no receivers or donors — skipping")

        return pd.DataFrame()
 
    # ── Join ─────────────────────────────────────────────────────────────────

    joined = receiver_sku.merge(

        donor_sku, left_on=["sku","store_type"], right_on=["donor_sku","donor_store_type"], how="inner"

    )

    joined = joined[joined["site_id"] != joined["donor_site_id"]]

    _blank_place = {"", "nan", "none", "<na>", "null"}
    recv_state = joined["state"].astype(str).str.strip()
    donor_state = joined["donor_state"].astype(str).str.strip()
    recv_city = joined["city"].astype(str).str.strip()
    donor_city = joined["donor_city"].astype(str).str.strip()
    same_state = (
        ~recv_state.str.lower().isin(_blank_place)
        & ~donor_state.str.lower().isin(_blank_place)
        & (recv_state.str.lower() == donor_state.str.lower())
    )
    same_city = (
        ~recv_city.str.lower().isin(_blank_place)
        & ~donor_city.str.lower().isin(_blank_place)
        & (recv_city.str.lower() == donor_city.str.lower())
    )
    # PT / CENTRO / ICONIC / EBO stay inside one state. Blank state is not a match.
    joined = joined[
        ~joined["store_type"].isin(SAME_STATE_ONLY_STORE_TYPES)
        | same_state
    ].copy()
    recv_state = joined["state"].astype(str).str.strip()
    donor_state = joined["donor_state"].astype(str).str.strip()
    recv_city = joined["city"].astype(str).str.strip()
    donor_city = joined["donor_city"].astype(str).str.strip()
    same_state = (
        ~recv_state.str.lower().isin(_blank_place)
        & ~donor_state.str.lower().isin(_blank_place)
        & (recv_state.str.lower() == donor_state.str.lower())
    )
    same_city = (
        ~recv_city.str.lower().isin(_blank_place)
        & ~donor_city.str.lower().isin(_blank_place)
        & (recv_city.str.lower() == donor_city.str.lower())
    )

    joined["actual_transferable_stacks"] = joined[[f"don_soh_{s}" for s in ALL_SIZES]].min(axis=1)

    joined["location_score"] = 1

    joined.loc[same_state, "location_score"] = 2

    joined.loc[same_city, "location_score"] = 3

    joined["donor_priority"] = 2

    joined.loc[joined["donor_source"] == "no_sales_data_donors", "donor_priority"] = 1

    joined["stock_age_score"] = joined["days_from_last_sales"]

    joined.loc[joined["donor_source"] == "no_sales_data_donors", "stock_age_score"] = joined["days_from_last_transfer"]

    joined["is_city_match"] = same_city.astype(int)

    log.info("  joined candidates: %s rows", len(joined))
 
    # ── Greedy fill ──────────────────────────────────────────────────────────

    result_rows, donor_soh_live = _greedy_fill(joined)

    result = pd.DataFrame(result_rows)

    log.info("  total fill rows: %s", len(result))
 
    # ── Output columns ───────────────────────────────────────────────────────

    final_cols = [

        "site_id","site_name","city","state","store_type",

        "receiver_store_soh","receiver_days_since_last_sale","receiver_last_week_sales",

        "receiver_sold_qty_35d","receiver_total_sales","priority_score",

        "sku","required_stack_qty","receiver_priority",

        "donor_site_id","donor_site_name","donor_city","donor_state","donor_store_soh",

        "days_from_last_transfer","days_from_last_sales",

        "donor_last_week_sales","donor_sold_qty_35d","donor_total_sales",

        "donor_source","donor_days_eligible","actual_transferable_stacks",

        "location_score","stock_age_score","fill_type","transfer_qty_total",

    ]

    for s in ALL_SIZES: final_cols.append(f"req_{s}_qty")

    for s in ALL_SIZES: final_cols.append(f"don_barcode_{s}")

    for s in ALL_SIZES: final_cols.append(f"don_soh_{s}")

    for s in ALL_SIZES: final_cols.append(f"rec_barcode_{s}")

    for s in ALL_SIZES: final_cols.append(f"rec_soh_{s}")

    for s in ALL_SIZES: final_cols.append(f"rec_lag1_{s}")

    for s in ALL_SIZES: final_cols.append(f"rec_sold35_{s}")

    for s in ALL_SIZES: final_cols.append(f"rec_sales_{s}")

    for s in ALL_SIZES: final_cols.append(f"transfer_{s}_qty")
 
    if result.empty:

        return pd.DataFrame()

    for c in final_cols:

        if c not in result.columns:

            result[c] = 0 if (c.endswith("_qty") or c.endswith("_score")) else ""

    return result[final_cols].sort_values(["fill_type","receiver_priority","site_id","sku"])


def _recommended_output_columns() -> list[str]:
    """Column schema for recommended_transfers*.csv outputs."""
    cols = [
        "site_id", "site_name", "city", "state", "store_type",
        "receiver_store_soh", "receiver_days_since_last_sale", "receiver_last_week_sales",
        "receiver_sold_qty_35d", "receiver_total_sales", "priority_score",
        "sku", "required_stack_qty", "receiver_priority",
        "donor_site_id", "donor_site_name", "donor_city", "donor_state", "donor_store_soh",
        "days_from_last_transfer", "days_from_last_sales",
        "donor_last_week_sales", "donor_sold_qty_35d", "donor_total_sales",
        "donor_source", "donor_days_eligible", "actual_transferable_stacks",
        "location_score", "stock_age_score", "fill_type", "transfer_qty_total",
    ]
    for s in ALL_SIZES:
        cols.append(f"req_{s}_qty")
    for s in ALL_SIZES:
        cols.append(f"don_barcode_{s}")
    for s in ALL_SIZES:
        cols.append(f"don_soh_{s}")
    for s in ALL_SIZES:
        cols.append(f"rec_barcode_{s}")
    for s in ALL_SIZES:
        cols.append(f"rec_soh_{s}")
    for s in ALL_SIZES:
        cols.append(f"rec_lag1_{s}")
    for s in ALL_SIZES:
        cols.append(f"rec_sold35_{s}")
    for s in ALL_SIZES:
        cols.append(f"rec_sales_{s}")
    for s in ALL_SIZES:
        cols.append(f"transfer_{s}_qty")
    return cols


def _empty_recommended_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=_recommended_output_columns())


def _write_empty_recommended_outputs() -> None:
    """Persist empty transfer outputs so downstream steps do not fail."""
    empty = _empty_recommended_frame()
    _safe_to_csv(empty, OUT_RECOMMENDED)
    _safe_to_excel(empty, OUT_RECOMMENDED.with_suffix(".xlsx"))
    _safe_to_csv(_build_grouped_pairs(empty), OUT_RECOMMENDED_GROUPED)
    _safe_to_excel(_build_grouped_pairs(empty), OUT_RECOMMENDED_GROUPED.with_suffix(".xlsx"))
    empty_sw = pd.DataFrame(columns=[
        "receiver_site_id", "receiver_site_name", "receiver_city", "receiver_state",
        "donor_site_id", "donor_site_name", "donor_city", "donor_state",
        "sku", "size", "barcode", "required_qty", "transfer_qty",
        "receiver_soh", "donor_soh_before", "priority_score",
        "receiver_last_week_sales", "receiver_sold_qty_35d", "receiver_total_sales",
        "donor_last_week_sales", "donor_sold_qty_35d", "donor_total_sales",
        "city_match", "state_match", "other_state", "round", "status", "created_at",
    ])
    _safe_to_csv(empty_sw, OUT_RECOMMENDED_SIZEWISE)
    _safe_to_excel(empty_sw, OUT_RECOMMENDED_SIZEWISE_XLSX)
    summary_lines = [
        f"Receivers input : {IN_RECEIVERS}",
        f"Donors input    : {IN_DONORS}",
        f"Store SOH input : {IN_STORE_SOH if IN_STORE_SOH.exists() else 'NOT_FOUND'}",
        "Rounds run      : 0",
        "Final rows      : 0",
        "Sizewise rows   : 0",
        "Note            : No stack-transfer matches (empty outputs written).",
        f"Saved           : {OUT_RECOMMENDED}",
    ]
    OUT_SUMMARY.write_text("\n".join(summary_lines), encoding="utf-8")
    print("\n".join(summary_lines))
 

# SOH update between rounds

# ─────────────────────────────────────────────────────────────────────────────

def _update_soh_after_round(

    donors: pd.DataFrame,

    receivers: pd.DataFrame,

    result: pd.DataFrame,

) -> tuple[pd.DataFrame, pd.DataFrame]:

    if result.empty:

        return donors, receivers
 
    donors_out    = donors.copy()

    receivers_out = receivers.copy()
 
    donors_out["_site_sku_size"] = (

        donors_out["site_id"].str.upper() + "||" +

        donors_out["sku"].str.upper()     + "||" +

        donors_out["size"].str.upper()

    )

    receivers_out["_site_sku_size"] = (

        receivers_out["site_id"].str.upper() + "||" +

        receivers_out["sku"].str.upper()     + "||" +

        receivers_out["size"].str.upper()

    )
 
    size_rows: list[dict] = []

    for row in result.to_dict(orient="records"):

        donor_site = str(row["donor_site_id"]).upper()

        rec_site   = str(row["site_id"]).upper()

        sku        = str(row["sku"]).upper()

        for s in ALL_SIZES:

            qty = float(row.get(f"transfer_{s}_qty", 0) or 0)

            if qty > 0:

                size_rows.append({

                    "donor_key": f"{donor_site}||{sku}||{s}",

                    "rec_key":   f"{rec_site}||{sku}||{s}",

                    "qty":       qty,

                })
 
    if not size_rows:

        donors_out    = donors_out.drop(columns=["_site_sku_size"])

        receivers_out = receivers_out.drop(columns=["_site_sku_size"])

        return donors_out, receivers_out
 
    transfers = pd.DataFrame(size_rows)
 
    donor_deduct = transfers.groupby("donor_key")["qty"].sum().reset_index()

    donor_deduct.columns = ["_site_sku_size", "deduct_qty"]

    donors_out = donors_out.merge(donor_deduct, on="_site_sku_size", how="left")

    donors_out["deduct_qty"] = donors_out["deduct_qty"].fillna(0)

    donors_out["store_soh"]  = (donors_out["store_soh"] - donors_out["deduct_qty"]).clip(lower=0)

    donors_out = donors_out.drop(columns=["_site_sku_size", "deduct_qty"])
 
    rec_add = transfers.groupby("rec_key")["qty"].sum().reset_index()

    rec_add.columns = ["_site_sku_size", "add_qty"]

    receivers_out = receivers_out.merge(rec_add, on="_site_sku_size", how="left")

    receivers_out["add_qty"] = receivers_out["add_qty"].fillna(0)

    receivers_out["store_soh"] = receivers_out["store_soh"] + receivers_out["add_qty"]

    if "qty_still_needed" in receivers_out.columns:

        receivers_out["qty_still_needed"] = (

            receivers_out["qty_still_needed"] - receivers_out["add_qty"]

        ).clip(lower=0)

    receivers_out = receivers_out.drop(columns=["_site_sku_size", "add_qty"])
 
    donors_out    = donors_out[donors_out["store_soh"] > 0].copy()

    if "qty_still_needed" in receivers_out.columns:

        receivers_out = receivers_out[receivers_out["qty_still_needed"] > 0].copy()
 
    log.info(

        "  SOH updated | donors=%s rows | receivers=%s rows",

        len(donors_out), len(receivers_out),

    )

    return donors_out, receivers_out
 
 
# ─────────────────────────────────────────────────────────────────────────────

# Output helpers

# ─────────────────────────────────────────────────────────────────────────────

def _build_grouped_pairs(final: pd.DataFrame) -> pd.DataFrame:

    if final.empty:

        return pd.DataFrame(columns=[

            "donor_site_id","donor_site_name","receiver_site_id","receiver_site_name",

            "store_type","primary_match_type","sku_count","sku_list","total_transfer_qty",

        ])

    x = final.copy()

    x["receiver_site_id"]   = x["site_id"]

    x["receiver_site_name"] = x["site_name"]

    x["primary_match_type"] = x["location_score"].map({3:"CITY_MATCH", 2:"STATE_MATCH"}).fillna("OTHER_STATE")

    return (x.groupby(

                ["donor_site_id","donor_site_name","receiver_site_id","receiver_site_name",

                 "store_type","primary_match_type"], as_index=False)

             .agg(

                sku_count=("sku","nunique"),

                sku_list=("sku", lambda s: ", ".join(sorted(pd.unique(s.astype(str))))),

                total_transfer_qty=("transfer_qty_total","sum"),

             )

             .sort_values(["primary_match_type","total_transfer_qty","sku_count"],

                          ascending=[True, False, False]))
 
 
def _build_sizewise_rows(

    final: pd.DataFrame,

    store_soh_lookup: dict[tuple[str, str], float],

    round_num: int | None = None,

) -> pd.DataFrame:

    rows: list[dict] = []

    created_at = pd.Timestamp.now().strftime("%d-%m-%Y %H:%M")

    if final.empty:

        return pd.DataFrame()
 
    for row in final.to_dict(orient="records"):

        for s in ALL_SIZES:

            transfer_qty = float(row.get(f"transfer_{s}_qty", 0) or 0)

            req_qty      = float(row.get(f"rec_req_{s}", row.get(f"req_{s}_qty", 0)) or 0)

            r_soh        = float(row.get(f"rec_soh_{s}", 0) or 0)

            d_soh        = float(row.get(f"don_soh_{s}", 0) or 0)
 
            # Donor ka barcode prefer karo — stock donor se ja raha hai

            don_bc = _norm_text_scalar(row.get(f"don_barcode_{s}", "")).replace(".0", "")

            rec_bc = _norm_text_scalar(row.get(f"rec_barcode_{s}", "")).replace(".0", "")

            display_bc = don_bc if don_bc else rec_bc
 
            # Jo size receiver or donor mai available nahi hai, use show nahi karna hai

            if not display_bc:

                continue
 
            rows.append({

                "receiver_site_id":         row.get("site_id", ""),

                "receiver_site_name":       row.get("site_name", ""),

                "receiver_city":            row.get("city", ""),

                "receiver_state":           row.get("state", ""),

                "donor_site_id":            row.get("donor_site_id", ""),

                "donor_site_name":          row.get("donor_site_name", ""),

                "donor_city":               row.get("donor_city", ""),

                "donor_state":              row.get("donor_state", ""),

                "sku":                      row.get("sku", ""),

                "size":                     s,

                "barcode":                  display_bc,

                "required_qty":             req_qty,

                "transfer_qty":             transfer_qty,

                "receiver_soh":             r_soh,

                "donor_soh":                d_soh,

                "priority_score":           float(row.get("priority_score", 0) or 0),

                "receiver_last_week_sales": float(row.get(f"rec_lag1_{s}", 0) or 0),

                "receiver_sold_qty_35d":    float(row.get(f"rec_sold35_{s}", 0) or 0),

                "receiver_total_sales":     float(row.get(f"rec_sales_{s}", 0) or 0),

                "donor_last_week_sales":    float(row.get("donor_last_week_sales", 0) or 0),

                "donor_sold_qty_35d":       float(row.get("donor_sold_qty_35d", 0) or 0),

                "donor_total_sales":        float(row.get("donor_total_sales", 0) or 0),

                "city_match":  int(_norm_text_scalar(row.get("city","")).lower() == _norm_text_scalar(row.get("donor_city","")).lower()),

                "state_match": int(

                    _norm_text_scalar(row.get("state","")).lower() == _norm_text_scalar(row.get("donor_state","")).lower()

                    and _norm_text_scalar(row.get("city","")).lower() != _norm_text_scalar(row.get("donor_city","")).lower()

                ),

                "other_state": int(_norm_text_scalar(row.get("state","")).lower() != _norm_text_scalar(row.get("donor_state","")).lower()),

                "round":       round_num if round_num is not None else "",

                "status":      "PENDING",

                "created_at":  created_at,

            })
 
    if not rows:

        return pd.DataFrame()

    out = pd.DataFrame(rows)

    final_cols = [

        "receiver_site_name","receiver_city","receiver_state",

        "donor_site_name","donor_city","donor_state",

        "sku","size","barcode","required_qty","transfer_qty",

        "receiver_soh","donor_soh","priority_score",

        "receiver_last_week_sales","receiver_sold_qty_35d","receiver_total_sales",

        "donor_last_week_sales","donor_sold_qty_35d","donor_total_sales",

        "city_match","state_match","other_state","round","status","created_at",

        "receiver_site_id","donor_site_id",

    ]

    for c in final_cols:

        if c not in out.columns:

            out[c] = ""

    return (out[final_cols]

            .sort_values(["receiver_site_id","sku","size","donor_site_id"])

            .reset_index(drop=True))
 
 
# ─────────────────────────────────────────────────────────────────────────────

# MAIN

# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:

    _setup_run_logging(OUT_RUN_LOG)

    log.info("=== recommend transfers: start (rounds=%s) ===", NUM_ROUNDS)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
 
    if not IN_RECEIVERS.exists() or not IN_DONORS.exists():

        raise FileNotFoundError("Run 1_build_potential_donors_receivers.py first.")
 
    receivers_orig   = _prepare_base(pd.read_csv(IN_RECEIVERS, low_memory=False), is_receiver=True)

    donors_orig      = _prepare_base(pd.read_csv(IN_DONORS,    low_memory=False), is_receiver=False)

    store_soh_lookup = _build_store_soh_lookup(IN_STORE_SOH)

    log.info("loaded | receivers=%s | donors=%s | soh_keys=%s",

             len(receivers_orig), len(donors_orig), len(store_soh_lookup))
 
    all_results:          list[pd.DataFrame]   = []

    receivers_cur         = receivers_orig.copy()

    donors_cur            = donors_orig.copy()

    already_receiver_sku: set[tuple[str, str]] = set()
 
    for rnd in range(1, NUM_ROUNDS + 1):

        log.info("── ROUND %s/%s ──────────────────────────────────────────", rnd, NUM_ROUNDS)

        log.info("  donors_cur=%s rows | receivers_cur=%s rows", len(donors_cur), len(receivers_cur))
 
        if receivers_cur.empty or donors_cur.empty:

            log.info("  nothing left — stopping early at round %s", rnd)

            break
 
        result_rnd = _run_one_round(

            receivers=receivers_cur,

            donors=donors_cur,

            already_receiver_sku=already_receiver_sku,

        )
 
        if result_rnd.empty:

            log.info("  round %s produced 0 rows — stopping", rnd)

            break
 
        result_rnd = result_rnd.copy()

        result_rnd["round"] = rnd

        all_results.append(result_rnd)
 
        snap_sw   = _build_sizewise_rows(result_rnd, store_soh_lookup, round_num=rnd)

        snap_path = Path(str(OUT_ROUND_SIZEWISE_TEMPLATE).replace("{round}", str(rnd)))

        _safe_to_csv(snap_sw, snap_path)

        log.info("  round %s snapshot saved → %s (%s rows)", rnd, snap_path.name, len(snap_sw))
 
        full_this_round = result_rnd[result_rnd["fill_type"] == "FULL"]

        already_receiver_sku |= set(zip(

            full_this_round["site_id"].str.upper(),

            full_this_round["sku"].str.upper(),

        ))
 
        if rnd < NUM_ROUNDS:

            donors_cur, receivers_cur = _update_soh_after_round(

                donors_cur, receivers_cur, result_rnd

            )
 
    if not all_results:

        log.warning("No recommendations generated — writing empty output files.")

        _write_empty_recommended_outputs()

        log.info("=== done (empty) ===")

        return
 
    final = pd.concat(all_results, ignore_index=True, sort=False)

    log.info("combined final rows: %s across %s round(s)", len(final), len(all_results))
 
    grouped_pairs = _build_grouped_pairs(final)

    sizewise      = _build_sizewise_rows(final, store_soh_lookup)
 
    saved_recommended     = _safe_to_csv(final, OUT_RECOMMENDED)
    saved_recommended_xlsx = _safe_to_excel(final, OUT_RECOMMENDED.with_suffix(".xlsx"))

    saved_grouped         = _safe_to_csv(grouped_pairs, OUT_RECOMMENDED_GROUPED)
    saved_grouped_xlsx    = _safe_to_excel(grouped_pairs, OUT_RECOMMENDED_GROUPED.with_suffix(".xlsx"))

    saved_sizewise        = _safe_to_csv(sizewise, OUT_RECOMMENDED_SIZEWISE)

    saved_sizewise_xlsx   = _safe_to_excel(sizewise, OUT_RECOMMENDED_SIZEWISE_XLSX)
 
    summary_lines = [

        f"Receivers input : {IN_RECEIVERS}",

        f"Donors input    : {IN_DONORS}",

        f"Store SOH input : {IN_STORE_SOH if IN_STORE_SOH.exists() else 'NOT_FOUND'}",

        f"Rounds run      : {len(all_results)}",

        *(f"  Round {rnd} rows: {len(df):,}" for rnd, df in enumerate(all_results, 1)),

        f"Final rows      : {len(final):,}",

        f"Sizewise rows   : {len(sizewise):,}",

        f"Saved           : {saved_recommended}",

        f"Saved xlsx      : {saved_recommended_xlsx}",

        f"Saved grouped   : {saved_grouped}",

        f"Saved grouped x : {saved_grouped_xlsx}",

        f"Saved sizewise  : {saved_sizewise}",

        f"Saved xlsx      : {saved_sizewise_xlsx}",

    ]

    OUT_SUMMARY.write_text("\n".join(summary_lines), encoding="utf-8")

    print("\n".join(summary_lines))

    log.info("=== done ===")
 
 
if __name__ == "__main__":

    try:

        main()

    except Exception:

        logging.getLogger(LOGGER_NAME).exception("recommend transfers failed")

        raise
 
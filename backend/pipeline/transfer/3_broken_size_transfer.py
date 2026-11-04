"""
3_broken_size_transfer.py
─────────────────────────
Broken-size mopping pass that runs AFTER 2_recommend_transfers.py.

What is a "broken size"?
  After the main stack-transfer rounds a donor may have residual stock where
  only 1–(BROKEN_THRESHOLD-1) units remain for a given size.  That size can no
  longer anchor a full stack for the next greedy round, so it would stay frozen
  forever.  This script rescues those units.

Three-tier matching (in order):
  TIER 1 – BROKEN_STACK
      Two or more broken sizes from the same donor can together satisfy a
      receiver → send them as a mini-stack.  Preferred because the pair keeps
      the SKU coherent at the destination.

  TIER 2 – BROKEN_NEW_RECEIVER
      A single broken size can go to a new receiver that needs it, even if only
      one size is involved.

  TIER 3 – BROKEN_DESIGNATED
      No external receiver found.  Send the residual back to the receiver that
      already got the main stack from this donor (they have the context and the
      complementary sizes).

  ISOLATED_SIZE
      None of the above matched.  Flagged for manual review; nothing is moved.

Receiver priority (descending):
  1. Grade rank   : 1A+ > A+ > A > B > C > D  (stores with better grade first)
  2. priority_score (from ML pipeline)         (higher = more active store)

Geography rules (identical to main transfer script):
  • PT / CENTRO / ICONIC / EBO → same store type + SAME STATE only
    (city preferred first via location_score, then same state)
  • SSL → SSL : same city first → same state → any other state
  • A donor site is never its own receiver.

Run after:
  python transfer/2_recommend_transfers.py

Outputs (in transfer/output/):
  broken_size_transfers.csv / .xlsx  – size-wise transfer rows
  broken_size_summary.txt            – run statistics
  broken_size_run.log                – detailed execution log
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────
ALL_SIZES   = ["1XS", "2S", "3M", "4L", "5XL", "6XXL"]
BASE_DIR    = Path(__file__).resolve().parents[1]
LOGGER_NAME = "broken_size_transfer"
log         = logging.getLogger(LOGGER_NAME)

# ── Paths ─────────────────────────────────────────────────────────────────────
IN_RECOMMENDED = BASE_DIR / "transfer" / "output" / "recommended_transfers.csv"
IN_RECEIVERS   = BASE_DIR / "transfer" / "output" / "potential_receivers.csv"
IN_DONORS      = BASE_DIR / "transfer" / "output" / "potential_donors.csv"
OUT_DIR        = BASE_DIR / "transfer" / "output"
OUT_CSV        = OUT_DIR / "broken_size_transfers.csv"
OUT_XLSX       = OUT_DIR / "broken_size_transfers.xlsx"
OUT_SUMMARY    = OUT_DIR / "broken_size_summary.txt"
OUT_LOG        = OUT_DIR / "broken_size_run.log"

# A size is "broken" when its remaining SOH is strictly below this value.
# SOH = 1 or 2  →  broken.   SOH ≥ 3  →  still viable for the main rounds.
BROKEN_THRESHOLD: int = 3

# Grade → numeric priority rank (LOWER number = HIGHER priority).
GRADE_RANK: dict[str, int] = {
    "1A+": 0,
    "A+":  1,
    "A":   2,
    "B":   3,
    "C":   4,
    "D":   5,
}
DEFAULT_GRADE_RANK: int = 99   # unknown / blank grades go last

# PT / CENTRO / ICONIC / EBO — same store type + same state only (city first via score).
SAME_STATE_ONLY_STORE_TYPES = frozenset({"PT", "CENTRO", "ICONIC", "EBO"})


# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────
def _setup_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fmt    = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
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
# Tiny helpers
# ─────────────────────────────────────────────────────────────────────────────
def _to_num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").fillna(0.0)


def _clean(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip().replace({"nan": "", "None": "", "<NA>": "", "NaT": ""})


def _upper(s: pd.Series) -> pd.Series:
    return _clean(s).str.upper()


def _grade_rank(grade: str) -> int:
    return GRADE_RANK.get(str(grade).strip(), DEFAULT_GRADE_RANK)


def _safe_to_csv(df: pd.DataFrame, path: Path) -> Path:
    try:
        df.to_csv(path, index=False, encoding="utf-8-sig")
        return path
    except PermissionError:
        fb = path.with_name(f"{path.stem}_latest{path.suffix}")
        df.to_csv(fb, index=False, encoding="utf-8-sig")
        return fb


def _safe_to_excel(df: pd.DataFrame, path: Path) -> Path:
    try:
        df.to_excel(path, index=False)
        return path
    except PermissionError:
        fb = path.with_name(f"{path.stem}_latest{path.suffix}")
        df.to_excel(fb, index=False)
        return fb


# ─────────────────────────────────────────────────────────────────────────────
# Step 1 – Reconstruct donor live SOH after the main transfer rounds
# ─────────────────────────────────────────────────────────────────────────────
def _compute_donor_live_soh(
    donors_df: pd.DataFrame,
    recommended_df: pd.DataFrame,
) -> dict[tuple[str, str, str], float]:
    """
    Returns  {(site_id, sku, size): remaining_soh}  for every donor size.

    Logic:
      original_soh  (from potential_donors.csv, one row per site+sku+size)
    – transferred   (sum of transfer_{size}_qty from recommended_transfers.csv
                     grouped by donor_site_id + donor_sku)
    = live_soh
    """
    # Original SOH per donor size
    soh_map: dict[tuple[str, str, str], float] = {}
    for _, row in donors_df.iterrows():
        key = (
            str(row["site_id"]).strip().upper(),
            str(row["sku"]).strip().upper(),
            str(row["size"]).strip().upper(),
        )
        try:
            soh_val = float(row["store_soh"])
        except (ValueError, TypeError):
            soh_val = 0.0
        soh_map[key] = soh_map.get(key, 0.0) + soh_val

    if recommended_df.empty:
        return soh_map

    # Deduct what was already transferred
    for _, row in recommended_df.iterrows():
        d_site = str(row.get("donor_site_id", "")).strip().upper()
        sku    = str(row.get("sku", "")).strip().upper()
        for s in ALL_SIZES:
            qty = float(row.get(f"transfer_{s}_qty", 0) or 0)
            if qty <= 0:
                continue
            key = (d_site, sku, s)
            if key in soh_map:
                soh_map[key] = max(0.0, soh_map[key] - qty)

    return soh_map


# ─────────────────────────────────────────────────────────────────────────────
# Step 2 – Identify broken sizes per donor (site_id + sku)
# ─────────────────────────────────────────────────────────────────────────────
def _identify_broken_sizes(
    live_soh: dict[tuple[str, str, str], float],
) -> dict[tuple[str, str], dict[str, float]]:
    """
    Returns  {(site_id, sku): {size: remaining_qty}}
    Only includes sizes where  0 < qty < BROKEN_THRESHOLD.
    """
    broken: dict[tuple[str, str], dict[str, float]] = {}
    for (site, sku, size), qty in live_soh.items():
        if 0 < qty < BROKEN_THRESHOLD:
            broken.setdefault((site, sku), {})[size] = qty
    return broken


# ─────────────────────────────────────────────────────────────────────────────
# Step 3 – Compute receiver remaining needs after main transfers
# ─────────────────────────────────────────────────────────────────────────────
def _compute_receiver_needs(
    receivers_df: pd.DataFrame,
    recommended_df: pd.DataFrame,
) -> tuple[dict[tuple[str, str, str], float], dict[tuple[str, str, str], float]]:
    """
    Returns two dicts:
      orig_need_map      : {(site, sku, size): original qty_still_needed}
                           (never deducted — used for TIER 3 eligibility check)
      remaining_need_map : {(site, sku, size): need after main transfers}
                           (deducted by what was already sent — used in TIER 1/2)
    """
    orig_need_map: dict[tuple[str, str, str], float] = {}
    for _, row in receivers_df.iterrows():
        site = str(row["site_id"]).strip().upper()
        sku  = str(row["sku"]).strip().upper()
        size = str(row["size"]).strip().upper()
        need = float(row.get("qty_still_needed", 0) or 0)
        key  = (site, sku, size)
        orig_need_map[key] = orig_need_map.get(key, 0.0) + need

    # remaining_need starts as a copy of original
    remaining_need_map = dict(orig_need_map)

    if recommended_df.empty:
        return orig_need_map, remaining_need_map

    # Deduct what was already received in main transfer rounds
    for _, row in recommended_df.iterrows():
        r_site = str(row.get("site_id", "")).strip().upper()
        sku    = str(row.get("sku",     "")).strip().upper()
        for s in ALL_SIZES:
            qty = float(row.get(f"transfer_{s}_qty", 0) or 0)
            if qty <= 0:
                continue
            key = (r_site, sku, s)
            if key in remaining_need_map:
                remaining_need_map[key] = max(0.0, remaining_need_map[key] - qty)

    return orig_need_map, remaining_need_map


# ─────────────────────────────────────────────────────────────────────────────
# Step 4 – Build a lookup: donor (site, sku) → designated receiver (site)
# ─────────────────────────────────────────────────────────────────────────────
def _build_designated_receiver_map(
    recommended_df: pd.DataFrame,
) -> dict[tuple[str, str], str]:
    """
    For each (donor_site_id, sku) return the receiver site_id that received
    the main stack.  If multiple receivers exist, keep the one with the highest
    total transfer_qty_total (biggest stack).
    """
    if recommended_df.empty:
        return {}
    df = recommended_df.copy()
    df["donor_site_id"] = _upper(df["donor_site_id"].astype(str))
    df["site_id"]       = _upper(df["site_id"].astype(str))
    df["sku"]           = _upper(df["sku"].astype(str))
    df["transfer_qty_total"] = _to_num(df.get("transfer_qty_total", pd.Series(0, index=df.index)))

    best = (
        df.sort_values("transfer_qty_total", ascending=False)
        .drop_duplicates(subset=["donor_site_id", "sku"], keep="first")
    )
    result: dict[tuple[str, str], str] = {}
    for _, row in best.iterrows():
        result[(row["donor_site_id"], row["sku"])] = row["site_id"]
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Step 5 – Build a flat receiver metadata table (one row per site+sku)
# ─────────────────────────────────────────────────────────────────────────────
def _build_receiver_meta(receivers_df: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate receivers to one row per (site_id, sku) with grade_rank and
    priority_score for ordering, plus barcode per size for output.
    """
    rx = receivers_df.copy()
    rx["site_id"]        = _upper(rx["site_id"].astype(str))
    rx["sku"]            = _upper(rx["sku"].astype(str))
    rx["size"]           = _upper(rx["size"].astype(str))
    rx["store_soh"]      = _to_num(rx.get("store_soh",      pd.Series(0, index=rx.index)))
    rx["priority_score"] = _to_num(rx.get("priority_score", pd.Series(0, index=rx.index)))
    rx["store_grade"]    = _clean(rx.get("store_grade", pd.Series("", index=rx.index)))
    rx["grade_rank"]     = rx["store_grade"].apply(_grade_rank)

    # Aggregate meta
    meta = rx.groupby(["site_id", "sku"], as_index=False).agg(
        site_name     = ("site_name",     "first"),
        city          = ("city",          "first"),
        state         = ("state",         "first"),
        store_type    = ("store_type",    "first"),
        store_grade   = ("store_grade",   "first"),
        grade_rank    = ("grade_rank",    "min"),
        priority_score= ("priority_score","max"),
    )

    # Barcode per size (for output display)
    bc_pivot = (
        rx.pivot_table(
            index=["site_id", "sku"],
            columns="size",
            values="barcode",
            aggfunc="first",
        )
        .reindex(columns=ALL_SIZES)
        .fillna("")
        .reset_index()
    )
    bc_pivot.columns.name = None
    for s in ALL_SIZES:
        bc_pivot = bc_pivot.rename(columns={s: f"rec_barcode_{s}"})

    return meta.merge(bc_pivot, on=["site_id", "sku"], how="left")


# ─────────────────────────────────────────────────────────────────────────────
# Step 6 – Build donor metadata table (one row per site+sku)
# ─────────────────────────────────────────────────────────────────────────────
def _build_donor_meta(donors_df: pd.DataFrame) -> pd.DataFrame:
    dn = donors_df.copy()
    dn["site_id"]     = _upper(dn["site_id"].astype(str))
    dn["sku"]         = _upper(dn["sku"].astype(str))
    dn["store_grade"] = _clean(dn.get("store_grade", pd.Series("", index=dn.index)))

    meta = dn.groupby(["site_id", "sku"], as_index=False).agg(
        site_name            = ("site_name",            "first"),
        city                 = ("city",                 "first"),
        state                = ("state",                "first"),
        store_type           = ("store_type",           "first"),
        store_grade          = ("store_grade",          "first"),
        donor_sold_qty_35d   = ("last_35day_sold_qty",  "max"),
        donor_last_week_sales= ("last_week_sales",      "max"),
        donor_total_sales    = ("total_sales",          "max"),
        donor_source         = ("donor_source",         "first"),
    )

    # Barcode per size
    bc_pivot = (
        dn.pivot_table(
            index=["site_id", "sku"],
            columns="size",
            values="barcode",
            aggfunc="first",
        )
        .reindex(columns=ALL_SIZES)
        .fillna("")
        .reset_index()
    )
    bc_pivot.columns.name = None
    for s in ALL_SIZES:
        bc_pivot = bc_pivot.rename(columns={s: f"don_barcode_{s}"})

    return meta.merge(bc_pivot, on=["site_id", "sku"], how="left")


# ─────────────────────────────────────────────────────────────────────────────
# Geography rule check
# ─────────────────────────────────────────────────────────────────────────────
def _geography_ok(
    donor_store_type: str,
    donor_state: str,
    donor_city: str,
    donor_site_id: str,
    recv_store_type: str,
    recv_state: str,
    recv_site_id: str,
) -> bool:
    """
    Returns True if the donor→receiver geography is allowed.
    PT / CENTRO / ICONIC / EBO : same store type + SAME STATE only.
    SSL : same store type (city/state filtering is done in scoring, not blocking).
    No self-transfer.
    """
    if donor_site_id == recv_site_id:
        return False
    if donor_store_type != recv_store_type:
        return False
    if donor_store_type in SAME_STATE_ONLY_STORE_TYPES:
        ds = str(donor_state or "").strip().lower()
        rs = str(recv_state or "").strip().lower()
        if ds in {"", "nan", "none", "<na>", "null"} or rs in {"", "nan", "none", "<na>", "null"} or ds != rs:
            return False
    return True


def _location_score(
    donor_city: str, donor_state: str,
    recv_city: str,  recv_state: str,
) -> int:
    """3 = same city, 2 = same state diff city, 1 = different state."""
    dc = str(donor_city).strip().lower()
    ds = str(donor_state).strip().lower()
    rc = str(recv_city).strip().lower()
    rs = str(recv_state).strip().lower()
    if dc and rc and dc == rc:
        return 3
    if ds and rs and ds == rs:
        return 2
    return 1


# ─────────────────────────────────────────────────────────────────────────────
# Row builder helper (avoid repetition across tiers)
# ─────────────────────────────────────────────────────────────────────────────
def _make_row(
    t_type: str,
    r_site: str, recv_row: dict, r_city: str, r_state: str,
    r_type: str, r_grade: str, r_gprio: int, r_pscore: float,
    d_site: str, d_meta: dict, d_city: str, d_state: str, d_type: str,
    d_sku: str, s: str, barcode: str, qty: float,
    live_soh: dict, receiver_soh_map: dict, r_sku: str,
    loc_score: int, created_at: str,
) -> dict:
    return {
        "transfer_type":           t_type,
        "receiver_site_id":        r_site,
        "receiver_site_name":      recv_row.get("site_name", ""),
        "receiver_city":           r_city,
        "receiver_state":          r_state,
        "receiver_store_type":     r_type,
        "receiver_store_grade":    r_grade,
        "receiver_grade_rank":     r_gprio,
        "receiver_priority_score": r_pscore,
        "donor_site_id":           d_site,
        "donor_site_name":         d_meta.get("site_name", ""),
        "donor_city":              d_city,
        "donor_state":             d_state,
        "donor_store_type":        d_type,
        "donor_store_grade":       d_meta.get("store_grade", ""),
        "sku":                     d_sku,
        "size":                    s,
        "barcode":                 barcode,
        "transfer_qty":            int(qty),
        "donor_soh_before":        int(live_soh.get((d_site, d_sku, s), 0)),
        "receiver_soh":            int(receiver_soh_map.get((r_site, r_sku, s), 0)),
        "location_score":          loc_score,
        "city_match":              int(loc_score == 3),
        "state_match":             int(loc_score == 2),
        "other_state":             int(loc_score == 1),
        "donor_sold_qty_35d":      d_meta.get("donor_sold_qty_35d", 0),
        "donor_last_week_sales":   d_meta.get("donor_last_week_sales", 0),
        "donor_total_sales":       d_meta.get("donor_total_sales", 0),
        "donor_source":            d_meta.get("donor_source", ""),
        "status":                  "PENDING",
        "created_at":              created_at,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Core matching engine
# ─────────────────────────────────────────────────────────────────────────────
def _run_broken_matching(
    broken_donors:      dict[tuple[str, str], dict[str, float]],
    live_soh:           dict[tuple[str, str, str], float],
    need_map:           dict[tuple[str, str, str], float],
    receiver_meta:      pd.DataFrame,
    donor_meta_lookup:  dict[tuple[str, str], dict],
    designated_map:     dict[tuple[str, str], str],
    receiver_soh_map:   dict[tuple[str, str, str], float],
    orig_need_map:      dict[tuple[str, str, str], float],
) -> list[dict]:
    """
    Three-tier broken-size matching — OPTIMISED.

    Performance fix: receivers are pre-grouped by (sku, store_type) so each
    donor only iterates over the small slice of receivers that share its SKU
    and store-type, not the entire receiver table.

    TIER 1 – BROKEN_STACK        : 2+ broken sizes → one receiver (mini-stack).
    TIER 2 – BROKEN_NEW_RECEIVER  : 1 broken size  → new receiver.
    TIER 3 – BROKEN_DESIGNATED    : leftovers → designated receiver (all remaining qty).
    ISOLATED_SIZE                 : no match → flag for review.

    Qty rule (TIER 1 & 2): min(donor_broken_soh, receiver_remaining_need)
    Qty rule (TIER 3)     : send ALL remaining broken qty (last-resort clearance).
    """
    result_rows: list[dict] = []
    created_at  = pd.Timestamp.now().strftime("%d-%m-%Y %H:%M")

    # ── Pre-group receivers by (sku, store_type) sorted by priority ──────────
    # This avoids iterating all 3k+ receivers for every donor.
    recv_sorted = receiver_meta.sort_values(
        ["grade_rank", "priority_score"], ascending=[True, False]
    ).reset_index(drop=True)

    # Build lookup: (sku, store_type) → list of receiver row dicts
    recv_by_sku_type: dict[tuple[str, str], list[dict]] = {}
    for row in recv_sorted.to_dict(orient="records"):
        key = (
            str(row.get("sku",        "")).strip().upper(),
            str(row.get("store_type", "")).strip(),
        )
        recv_by_sku_type.setdefault(key, []).append(row)

    # ── Designated receiver lookup (dict for O(1) access) ────────────────────
    # (d_site, d_sku) → receiver row dict (pre-fetched)
    desig_recv_rows: dict[tuple[str, str], dict] = {}
    recv_by_site_sku: dict[tuple[str, str], dict] = {
        (str(r["site_id"]).upper(), str(r["sku"]).upper()): r
        for r in recv_sorted.to_dict(orient="records")
    }
    for (d_site, d_sku), r_site in designated_map.items():
        row = recv_by_site_sku.get((str(r_site).upper(), str(d_sku).upper()))
        if row:
            desig_recv_rows[(d_site, d_sku)] = row

    # ── Main loop: one pass per donor site+SKU ───────────────────────────────
    for (d_site, d_sku), broken in list(broken_donors.items()):
        d_meta  = donor_meta_lookup.get((d_site, d_sku), {})
        d_type  = str(d_meta.get("store_type", "")).strip()
        d_state = str(d_meta.get("state",      "")).strip()
        d_city  = str(d_meta.get("city",       "")).strip()

        remaining: dict[str, float] = {s: q for s, q in broken.items() if q > 0}
        if not remaining:
            continue

        # Candidate receivers: only those sharing SKU + store_type
        candidates = recv_by_sku_type.get((d_sku, d_type), [])

        # ── TIER 1 + TIER 2 ──────────────────────────────────────────────────
        for recv_row in candidates:
            if not remaining:
                break

            r_site   = str(recv_row["site_id"]).strip().upper()
            r_sku    = str(recv_row.get("sku",           d_sku)).strip().upper()
            r_type   = str(recv_row.get("store_type",    "")).strip()
            r_state  = str(recv_row.get("state",         "")).strip()
            r_city   = str(recv_row.get("city",          "")).strip()
            r_grade  = str(recv_row.get("store_grade",   "")).strip()
            r_gprio  = int(recv_row.get("grade_rank",    DEFAULT_GRADE_RANK))
            r_pscore = float(recv_row.get("priority_score", 0))

            if not _geography_ok(d_type, d_state, d_city, d_site,
                                  r_type, r_state, r_site):
                continue

            # Which broken sizes does this receiver still need?
            # Send min(donor_broken_soh, receiver_remaining_need) per size.
            overlap: dict[str, float] = {}
            for s, avail in remaining.items():
                need = need_map.get((r_site, r_sku, s), 0.0)
                if need > 0 and avail > 0:
                    overlap[s] = min(avail, need)

            if not overlap:
                continue

            t_type    = "BROKEN_STACK" if len(overlap) >= 2 else "BROKEN_NEW_RECEIVER"
            loc_score = _location_score(d_city, d_state, r_city, r_state)

            for s, qty in overlap.items():
                barcode = (
                    str(recv_row.get(f"rec_barcode_{s}", "")).strip()
                    or str(d_meta.get(f"don_barcode_{s}", "")).strip()
                )
                result_rows.append(_make_row(
                    t_type, r_site, recv_row,
                    r_city, r_state, r_type, r_grade, r_gprio, r_pscore,
                    d_site, d_meta, d_city, d_state, d_type,
                    d_sku, s, barcode, qty,
                    live_soh, receiver_soh_map, r_sku, loc_score, created_at,
                ))

            for s, qty in overlap.items():
                live_soh[(d_site, d_sku, s)]  = max(0.0, live_soh.get((d_site, d_sku, s), 0) - qty)
                need_map[(r_site, r_sku, s)]  = max(0.0, need_map.get((r_site, r_sku, s), 0) - qty)
                remaining[s]                   = max(0.0, remaining.get(s, 0) - qty)

            remaining = {s: q for s, q in remaining.items() if q > 0}

        # ── TIER 3: Designated receiver — send ALL remaining broken qty ───────
        if remaining:
            recv_row = desig_recv_rows.get((d_site, d_sku))
            if recv_row:
                r_site   = str(recv_row["site_id"]).strip().upper()
                r_sku    = d_sku
                r_type   = str(recv_row.get("store_type",    "")).strip()
                r_state  = str(recv_row.get("state",         "")).strip()
                r_city   = str(recv_row.get("city",          "")).strip()
                r_grade  = str(recv_row.get("store_grade",   "")).strip()
                r_gprio  = int(recv_row.get("grade_rank",    DEFAULT_GRADE_RANK))
                r_pscore = float(recv_row.get("priority_score", 0))

                if _geography_ok(d_type, d_state, d_city, d_site,
                                  r_type, r_state, r_site):
                    loc_score = _location_score(d_city, d_state, r_city, r_state)

                    for s, qty in list(remaining.items()):
                        if qty <= 0:
                            continue
                        orig_need = orig_need_map.get((r_site, r_sku, s), 0.0)
                        recv_soh  = receiver_soh_map.get((r_site, r_sku, s), 0.0)

                        # Don't dump stock where receiver never needed it AND has stock
                        if orig_need <= 0 and recv_soh > 0:
                            continue

                        barcode = (
                            str(recv_row.get(f"rec_barcode_{s}", "")).strip()
                            or str(d_meta.get(f"don_barcode_{s}", "")).strip()
                        )
                        result_rows.append(_make_row(
                            "BROKEN_DESIGNATED", r_site, recv_row,
                            r_city, r_state, r_type, r_grade, r_gprio, r_pscore,
                            d_site, d_meta, d_city, d_state, d_type,
                            d_sku, s, barcode, qty,
                            live_soh, receiver_soh_map, r_sku, loc_score, created_at,
                        ))
                        live_soh[(d_site, d_sku, s)] = max(
                            0.0, live_soh.get((d_site, d_sku, s), 0) - qty
                        )
                        remaining[s] = 0.0

                    remaining = {s: q for s, q in remaining.items() if q > 0}

        # ── ISOLATED: truly unplaceable ───────────────────────────────────
        for s, qty in remaining.items():
            if qty <= 0:
                continue
            barcode = str(d_meta.get(f"don_barcode_{s}", "")).strip()
            result_rows.append({
                "transfer_type":          "ISOLATED_SIZE",
                "receiver_site_id":       "",
                "receiver_site_name":     "",
                "receiver_city":          "",
                "receiver_state":         "",
                "receiver_store_type":    d_type,
                "receiver_store_grade":   "",
                "receiver_grade_rank":    DEFAULT_GRADE_RANK,
                "receiver_priority_score": 0,
                "donor_site_id":          d_site,
                "donor_site_name":        d_meta.get("site_name", ""),
                "donor_city":             d_city,
                "donor_state":            d_state,
                "donor_store_type":       d_type,
                "donor_store_grade":      d_meta.get("store_grade", ""),
                "sku":                    d_sku,
                "size":                   s,
                "barcode":                barcode,
                "transfer_qty":           int(qty),
                "donor_soh_before":       int(live_soh.get((d_site, d_sku, s), 0)),
                "receiver_soh":           0,
                "location_score":         0,
                "city_match":             0,
                "state_match":            0,
                "other_state":            0,
                "donor_sold_qty_35d":    d_meta.get("donor_sold_qty_35d", 0),
                "donor_last_week_sales": d_meta.get("donor_last_week_sales", 0),
                "donor_total_sales":     d_meta.get("donor_total_sales", 0),
                "donor_source":          d_meta.get("donor_source", ""),
                "status":     "REVIEW_NEEDED",
                "created_at": created_at,
            })

    return result_rows


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main() -> None:
    _setup_logging(OUT_LOG)
    log.info("=== broken_size_transfer: start (BROKEN_THRESHOLD=%s) ===", BROKEN_THRESHOLD)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # ── Load inputs ──────────────────────────────────────────────────────────
    if not IN_RECEIVERS.exists() or not IN_DONORS.exists():
        raise FileNotFoundError(
            f"Run 1_build_potential_donors_receivers.py first."
        )

    if IN_RECOMMENDED.exists():
        recommended_df = pd.read_csv(IN_RECOMMENDED, low_memory=False)
    else:
        log.warning(
            "recommended_transfers.csv not found — treating main stack transfers as empty."
        )
        recommended_df = pd.DataFrame()
    receivers_df   = pd.read_csv(IN_RECEIVERS,   low_memory=False)
    donors_df      = pd.read_csv(IN_DONORS,      low_memory=False)

    recommended_df.columns = recommended_df.columns.str.strip()
    receivers_df.columns   = receivers_df.columns.str.strip()
    donors_df.columns      = donors_df.columns.str.strip()

    log.info(
        "loaded | recommended=%s | receivers=%s | donors=%s",
        len(recommended_df), len(receivers_df), len(donors_df),
    )

    # Normalise key fields
    for df in (receivers_df, donors_df):
        df["site_id"] = _upper(df["site_id"].astype(str))
        df["sku"]     = _upper(df["sku"].astype(str))
        df["size"]    = _upper(df["size"].astype(str))
        if "barcode" in df.columns:
            df["barcode"] = _clean(df["barcode"].astype(str)).str.replace(".0", "", regex=False)

    # ── Reconstruct live SOH after main transfers ────────────────────────────
    live_soh = _compute_donor_live_soh(donors_df, recommended_df)
    log.info("live_soh keys computed: %s", len(live_soh))

    # ── Identify broken sizes ────────────────────────────────────────────────
    broken_donors = _identify_broken_sizes(live_soh)
    log.info(
        "donor site+sku with broken sizes: %s  |  total broken size-entries: %s",
        len(broken_donors),
        sum(len(v) for v in broken_donors.values()),
    )
    if not broken_donors:
        log.info("No broken sizes found — nothing to do.")
        summary = ["No broken sizes found after main transfer rounds."]
        OUT_SUMMARY.write_text("\n".join(summary), encoding="utf-8")
        empty = pd.DataFrame(columns=[
            "transfer_type", "receiver_site_name", "receiver_city", "receiver_state",
            "receiver_store_type", "receiver_store_grade", "receiver_grade_rank",
            "receiver_priority_score", "donor_site_name", "donor_city", "donor_state",
            "donor_store_type", "donor_store_grade", "sku", "size", "barcode",
            "transfer_qty", "donor_soh_before", "receiver_soh", "location_score",
            "city_match", "state_match", "other_state", "donor_sold_qty_35d",
            "donor_last_week_sales", "donor_total_sales", "donor_source",
            "status", "created_at", "receiver_site_id", "donor_site_id",
        ])
        _safe_to_csv(empty, OUT_CSV)
        _safe_to_excel(empty, OUT_XLSX)
        print("\n".join(summary))
        return

    # ── Compute receiver needs (original + remaining after main transfers) ──
    orig_need_map, need_map = _compute_receiver_needs(receivers_df, recommended_df)

    # ── Receiver SOH lookup (for Pass B eligibility check) ──────────────────
    receiver_soh_map: dict[tuple[str, str, str], float] = {}
    for _, row in receivers_df.iterrows():
        site = str(row["site_id"]).strip().upper()
        sku  = str(row["sku"]).strip().upper()
        size = str(row["size"]).strip().upper()
        soh  = float(row.get("store_soh", 0) or 0)
        receiver_soh_map[(site, sku, size)] = soh

    # ── Designated receiver map ──────────────────────────────────────────────
    designated_map = _build_designated_receiver_map(recommended_df)
    log.info("designated receiver pairs: %s", len(designated_map))

    # ── Build structured lookup tables ──────────────────────────────────────
    receiver_meta = _build_receiver_meta(receivers_df)
    donor_meta_df = _build_donor_meta(donors_df)
    donor_meta_lookup: dict[tuple[str, str], dict] = {
        (str(r["site_id"]).upper(), str(r["sku"]).upper()): r.to_dict()
        for _, r in donor_meta_df.iterrows()
    }

    log.info(
        "receiver_meta rows: %s | donor_meta rows: %s",
        len(receiver_meta), len(donor_meta_df),
    )

    # ── Run matching ─────────────────────────────────────────────────────────
    result_rows = _run_broken_matching(
        broken_donors      = broken_donors,
        live_soh           = live_soh,
        need_map           = need_map,
        receiver_meta      = receiver_meta,
        donor_meta_lookup  = donor_meta_lookup,
        designated_map     = designated_map,
        receiver_soh_map   = receiver_soh_map,
        orig_need_map      = orig_need_map,
    )

    if not result_rows:
        log.warning("Matching produced 0 rows (all broken sizes were ISOLATED).")
        summary = ["Broken size matching ran but all sizes were ISOLATED_SIZE."]
        OUT_SUMMARY.write_text("\n".join(summary), encoding="utf-8")
        empty = pd.DataFrame(columns=[
            "transfer_type", "receiver_site_name", "receiver_city", "receiver_state",
            "receiver_store_type", "receiver_store_grade", "receiver_grade_rank",
            "receiver_priority_score", "donor_site_name", "donor_city", "donor_state",
            "donor_store_type", "donor_store_grade", "sku", "size", "barcode",
            "transfer_qty", "donor_soh_before", "receiver_soh", "location_score",
            "city_match", "state_match", "other_state", "donor_sold_qty_35d",
            "donor_last_week_sales", "donor_total_sales", "donor_source",
            "status", "created_at", "receiver_site_id", "donor_site_id",
        ])
        _safe_to_csv(empty, OUT_CSV)
        _safe_to_excel(empty, OUT_XLSX)
        print("\n".join(summary))
        return

    # ── Build output DataFrame ───────────────────────────────────────────────
    final_cols = [
        "transfer_type",
        "receiver_site_name", "receiver_city", "receiver_state",
        "receiver_store_type", "receiver_store_grade", "receiver_grade_rank",
        "receiver_priority_score",
        "donor_site_name", "donor_city", "donor_state",
        "donor_store_type", "donor_store_grade",
        "sku", "size", "barcode",
        "transfer_qty", "donor_soh_before", "receiver_soh",
        "location_score", "city_match", "state_match", "other_state",
        "donor_sold_qty_35d", "donor_last_week_sales", "donor_total_sales",
        "donor_source",
        "status", "created_at",
        "receiver_site_id", "donor_site_id",
    ]

    out = pd.DataFrame(result_rows)
    for c in final_cols:
        if c not in out.columns:
            out[c] = "" if c not in {
                "transfer_qty","donor_soh_before","receiver_soh",
                "location_score","city_match","state_match","other_state",
                "receiver_grade_rank","receiver_priority_score",
            } else 0

    out = (
        out[final_cols]
        .sort_values(
            ["transfer_type", "receiver_grade_rank", "receiver_priority_score",
             "receiver_site_id", "sku", "size"],
            ascending=[True, True, False, True, True, True],
        )
        .reset_index(drop=True)
    )

    # ── Save outputs ─────────────────────────────────────────────────────────
    saved_csv  = _safe_to_csv(out, OUT_CSV)
    saved_xlsx = _safe_to_excel(out, OUT_XLSX)

    # ── Summary ──────────────────────────────────────────────────────────────
    type_counts = out["transfer_type"].value_counts().to_dict()
    summary_lines = [
        f"Broken-size transfer pass complete",
        f"BROKEN_THRESHOLD       : {BROKEN_THRESHOLD}",
        f"Donor site+sku pairs   : {len(broken_donors)}",
        f"Total transfer rows    : {len(out)}",
        *(f"  {k:<28}: {v}" for k, v in type_counts.items()),
        f"Total units moved      : {int(out[out['transfer_type'] != 'ISOLATED_SIZE']['transfer_qty'].sum())}",
        f"Isolated (no match)    : {int(out[out['transfer_type']=='ISOLATED_SIZE']['transfer_qty'].sum())} units",
        f"Saved CSV  : {saved_csv}",
        f"Saved XLSX : {saved_xlsx}",
    ]
    OUT_SUMMARY.write_text("\n".join(summary_lines), encoding="utf-8")
    log.info("=== done ===")
    print("\n".join(summary_lines))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logging.getLogger(LOGGER_NAME).exception("broken_size_transfer failed")
        raise

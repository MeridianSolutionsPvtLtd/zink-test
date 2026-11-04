import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional
from io import BytesIO
import math
import time
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
import pandas as pd

from api.onelake_store import OneLakeJsonStore
from api.download_logger import log_download

router = APIRouter(prefix="/api/transfers-recom", tags=["Transfers Recommendation"])
logger = logging.getLogger(__name__)
store = OneLakeJsonStore()
_ZERO_SOH_CACHE: List[Dict[str, Any]] = []
_ZERO_SOH_CACHE_AT = 0.0
_ZERO_SOH_CACHE_SOURCE_TABLE: str | None = None
_ZERO_SOH_CACHE_TTL_SECONDS = 180
# Prepared (filtered + summarized) pages — avoid rebuilding 60k rows on every offset.
_ZERO_SOH_PREPARED_CACHE: Dict[str, Dict[str, Any]] = {}
_ZERO_SOH_PREPARED_CACHE_AT: Dict[str, float] = {}
_ZERO_SOH_PREPARED_TTL_SECONDS = 180
_TRANSFER_TABLE_CACHE: Dict[str, List[Dict[str, Any]]] = {}
_TRANSFER_TABLE_CACHE_AT: Dict[str, float] = {}
# Long TTL: demo / hosted pages should serve from RAM after first local disk hit
_TRANSFER_TABLE_CACHE_TTL_SECONDS = int(os.environ.get("TRANSFERS_CACHE_TTL_SECONDS", "86400") or "86400")
_TRANSFER_LOCK = threading.RLock()
_API_CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "api_cache" / "transfers"
_CLEARANCE_REC_CACHE: Dict[int, Dict[str, Any]] = {}
# Blob output stems under output/ — pipeline publish paths.
_DEFAULT_REPLEN_BLOB = (
    "prediction/current/SS26_weekly_predictions_formula_next_week_with_refill_recommendation"
)
TRANSFER_SOURCE_TABLE = (
    os.environ.get("TRANSFER_SYNC_SOURCE_TABLE", _DEFAULT_REPLEN_BLOB) or _DEFAULT_REPLEN_BLOB
).strip()
# Optional: only classify donor/receiver from rows with this `refill_source` (e.g. SITE_TO_SITE_TRANSFER_REQUIRED
# matches Replenishments “Site → site” tab / `REPLEN_SITE_TRANSFER_REFILL_SOURCE` in Frontend-V4).
TRANSFER_SYNC_REFILL_SOURCE = (os.environ.get("TRANSFER_SYNC_REFILL_SOURCE", "") or "").strip()
# Blob: output/transfer/final_transfer_sizewise.csv
RECOMMENDED_TRANSFERS_TABLE = (
    os.environ.get("RECOMMENDED_TRANSFERS_TABLE", "transfer/final_transfer_sizewise")
    or "transfer/final_transfer_sizewise"
).strip()
POTENTIAL_DONORS_TABLE = (
    os.environ.get("POTENTIAL_DONORS_TABLE", "transfer/potential_donors")
    or "transfer/potential_donors"
).strip()
POTENTIAL_RECEIVERS_TABLE = (
    os.environ.get("POTENTIAL_RECEIVERS_TABLE", "transfer/potential_receivers")
    or "transfer/potential_receivers"
).strip()
# No Sales Donors tab + /zero-soh/export — managed Delta dbo.<name> (override with env ZERO_SOH_TABLE).
ZERO_SOH_TABLE = (
    os.environ.get("ZERO_SOH_TABLE", "soh_null_no_sales_transfer_days")
    or "soh_null_no_sales_transfer_days"
).strip()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        f = float(value)
        if math.isnan(f) or math.isinf(f):
            return default
        try:
            if pd.isna(f):
                return default
        except Exception:
            pass
        return int(f)
    except Exception:
        return default


def _safe_qty(value: Any, default: int = 0) -> int:
    """Treat negative quantities as 0 for stock math."""
    return max(_safe_int(value, default), 0)


def _norm_transfer_sku_filter(val: Any) -> str:
    """Align transfer filter SKU with row sku (12345.0 vs 12345)."""
    s = str(val or "").strip()
    if not s or s.lower() in ("nan", "none", "null"):
        return ""
    try:
        f = float(s)
        if math.isfinite(f) and f == int(f) and abs(f) < 1e15 and "e" not in s.lower():
            return str(int(f)).upper()
    except (ValueError, OverflowError):
        pass
    return s.upper()


def _norm_transfer_week_token(val: Any) -> str:
    s = str(val or "").strip().upper().replace("W", "")
    if not s or s in ("NAN", "NONE"):
        return ""
    try:
        f = float(s)
        if math.isfinite(f) and f == int(f) and abs(f) < 1e4:
            return str(int(f))
    except (ValueError, OverflowError):
        pass
    return s


def _soft_norm_site_label(s: str) -> str:
    """Uppercase, unify unicode dashes, normalize spaces around hyphens (PT- X vs PT - X)."""
    t = str(s or "")
    for ch in ("\u2013", "\u2014", "\u2212", "–", "—"):
        t = t.replace(ch, "-")
    t = t.upper()
    t = re.sub(r"\s*-\s*", " - ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _recv_site_filter_matches(want_raw: str | None, r: Dict[str, Any]) -> bool:
    """
    Replenishment header often sends display site name; transfer rows carry code + name.
    Match soft-normalized label, substring on long names, or store codes (P492). No fuzzy token-% overlap (that pulled wrong sites).
    """
    if want_raw is None:
        return True
    s = str(want_raw).strip()
    if not s or s in ("All Sites", "All Stores", "All"):
        return True
    want_sn = _soft_norm_site_label(s)
    candidates = [
        str(r.get("receiver_store") or "").strip(),
        str(r.get("receiver_site") or "").strip(),
        str(r.get("receiver_store_name") or "").strip(),
        str(r.get("receiver_site_id") or "").strip(),
    ]
    for c in candidates:
        if not c:
            continue
        cn = _soft_norm_site_label(c)
        if cn == want_sn or (want_sn and cn and (want_sn in cn or cn in want_sn)):
            return True
    name = str(r.get("receiver_store_name") or r.get("receiver_site") or "").strip()
    if name and len(want_sn) >= 6:
        nu = _soft_norm_site_label(name)
        if want_sn in nu or nu in want_sn:
            return True
    want_u = s.upper()
    for tok in re.findall(r"[A-Z0-9]{2,12}", want_u):
        for c in candidates:
            if c and tok == c.upper():
                return True
    return False


def _recv_site_id_filter_matches(want_raw: str | None, r: Dict[str, Any]) -> bool:
    """Site ID filter: exact match, or same numeric tail as P492 vs 492."""
    if want_raw is None:
        return True
    s = str(want_raw).strip()
    if not s or s.upper() in ("ALL SITE IDS", "ALL"):
        return True
    ws = s.upper()
    id_cands = [
        str(r.get("receiver_site_id") or "").strip(),
        str(r.get("receiver_store") or "").strip(),
    ]

    def _strip_p_num(s: str) -> str:
        s = s.upper().strip()
        if len(s) >= 2 and s[0] == "P" and s[1:].isdigit():
            return s[1:].lstrip("0") or s[1:]
        return s.lstrip("0")

    for c in id_cands:
        if not c:
            continue
        if c.upper() == ws:
            return True
    w2 = _strip_p_num(ws)
    for c in id_cands:
        if not c:
            continue
        c2 = _strip_p_num(c)
        if w2 and c2 and w2 == c2:
            return True
    return False


def _transfer_row_matches_recv_filters(
    r: Dict[str, Any],
    *,
    recv_site: str | None,
    recv_site_id: str | None,
    recv_sku: str | None,
    recv_month: str | None,
    recv_week: str | None,
) -> bool:
    if recv_sku and recv_sku not in ("All SKUs", "All"):
        if _norm_transfer_sku_filter(r.get("sku")) != _norm_transfer_sku_filter(recv_sku):
            return False
    if not _recv_site_filter_matches(recv_site, r):
        return False
    if not _recv_site_id_filter_matches(recv_site_id, r):
        return False
    if recv_week and recv_week not in ("All Weeks", "All", ""):
        want_w = _norm_transfer_week_token(recv_week)
        rw = str(
            r.get("receiver_week")
            or r.get("week")
            or r.get("Week")
            or r.get("receiver_lake_week")
            or ""
        ).strip()
        if rw and _norm_transfer_week_token(rw) != want_w:
            return False
    if recv_month and recv_month not in ("All Months", "All", ""):
        want_raw = str(recv_month).strip()
        rm = str(
            r.get("receiver_month")
            or r.get("month")
            or r.get("Month")
            or r.get("receiver_lake_month")
            or ""
        ).strip()
        if rm:
            if rm.upper() == want_raw.upper() or rm == want_raw:
                pass
            else:
                try:
                    if int(float(rm)) != int(float(want_raw)):
                        return False
                except Exception:
                    return False
    return True


def _pick_int(row: Dict[str, Any], *keys: str, default: int = 0) -> int:
    """Pick first available numeric-like field from possible aliases."""
    for k in keys:
        if k in row and row.get(k) not in (None, ""):
            return _safe_int(row.get(k), default)
    return default


def _lake_unfilled_qty_or_none(row: Dict[str, Any]) -> int | None:
    """
    Read **only** the canonical lake unfilled column (default `unfilled_qty`).
    We do not scan other names: columns like `qty_unfilled` / `sum_last4_*` were matching first and
    often holding placeholder `1` for every row, which broke the Transfers table.

    Override column name with env `TRANSFER_LAKE_UNFILLED_COLUMN` (lowercase, after `_normalize_row`).
    """
    key = (os.environ.get("TRANSFER_LAKE_UNFILLED_COLUMN", "") or "unfilled_qty").strip().lower() or "unfilled_qty"
    if key not in row:
        return None
    v = row.get(key)
    if v is None or (isinstance(v, str) and not str(v).strip()):
        return None
    return max(_safe_int(v), 0)


def _pick_sold_qty_last_35d(row: Dict[str, Any]) -> int:
    """
    Units sold in the last 35 days from lake columns (row keys are lower-cased by _normalize_row).
    Includes `sold_qty_prev_5w` from recom_neww3 / replenishments (~5 weeks).
    Falls back to generic `last_35_days_sales` only when no unit column matched (may be amount, not units).
    """
    v = _pick_int(
        row,
        "last_35day_sold_qty",
        "sold_qty_prev_5w",
        "sold_qty_prev_5w_units",
        "sold_qty_last_35d",
        "sold_qty_last_35_days",
        "last_35_days_sold_qty",
        "qty_sold_last_35d",
        "qty_sold_last_35_days",
        "sold_units_last_35d",
        "sold_units_35d",
        "last35d_sold_qty",
        "last_35d_sold_qty",
        "sold_qty_prev_35d",
        default=0,
    )
    if v != 0:
        return v
    return _pick_int(row, "last_35_days_sales", default=0)


def _pick_last_week_sold_qty(row: Dict[str, Any]) -> int:
    """Units sold in the most recent week (lag1_sold_qty)."""
    return _pick_int(
        row,
        "lag1_sold_qty",
        "lag1_sold",
        "last_week_sold_qty",
        "last_week_sales",
        default=0,
    )


def _pick_net_sales_last_35d(row: Dict[str, Any]) -> int:
    """Net sales / revenue in last 35 days when the pipeline exposes a dedicated amount column."""
    return _pick_int(
        row,
        "net_sales_last_35d",
        "last_35_days_net_sales",
        "net_sales_last_35_days",
        "sales_amount_last_35d",
        "revenue_last_35d",
        "total_net_sales_last_35d",
        # recom_neww3 / replenishments lake (often weekly grain; best available amount on the row)
        "sum_of_sales",
        "total_net_sales",
        "total_sales",
        default=0,
    )


def _days_since_transfer(row: Dict[str, Any], fallback_days: int = 0) -> int:
    """
    Prefer precomputed days_since_last_transfer, otherwise compute from last_transfer_date.
    """
    direct = _pick_int(row, "days_since_last_transfer", "Days_Since_Last_Transfer", default=-1)
    # Keep positive direct values; 0 often means missing/default in source.
    if direct > 0:
        return direct

    raw_date = (
        row.get("last_transfer_date")
        or row.get("Last_Transfer_Date")
        or row.get("last_transfer_dt")
        or row.get("Last_Transfer_Dt")
        or row.get("last_purchase_date")
        or row.get("Last_Purchase_Date")
        or row.get("last_purchase_dt")
        or row.get("Last_Purchase_Dt")
    )
    if raw_date in (None, ""):
        return max(_safe_int(fallback_days), 0)
    text = str(raw_date).strip()
    if text.lower() in ("nan", "none", "null", "nat"):
        return max(_safe_int(fallback_days), 0)

    parsed = None
    # Common explicit formats first.
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%Y/%m/%d", "%d-%b-%Y", "%d %b %Y"):
        try:
            parsed = datetime.strptime(text, fmt)
            break
        except Exception:
            continue
    if parsed is None:
        try:
            parsed = pd.to_datetime(text, errors="coerce", dayfirst=True).to_pydatetime()
        except Exception:
            parsed = None
    if parsed is None or str(parsed) == "NaT":
        return max(_safe_int(fallback_days), 0)
    return max((datetime.now() - parsed).days, 0)


def _row_refill_for_potential(r: Dict[str, Any]) -> int:
    """
    Refill / need qty on potential_donors / potential_receivers rows from lake exports.
    Prefers `qty_still_needed` (transfer snapshot), then unfilled / recommendation aliases.
    """
    return _pick_int(
        r,
        "qty_still_needed",
        "recommendation_refill",
        "unfilled_qty",
        "recommendation_refill_qty",
        "recommended_refill",
        "need_qty",
        default=0,
    )


def _row_warehouse_soh(r: Dict[str, Any]) -> int:
    return _safe_qty(_pick_int(r, "warehouse_soh", "wh_soh", default=0))


def _row_days_last_selling(r: Dict[str, Any]) -> int:
    return _pick_int(
        r,
        "days_from_last_sales",
        "days_from_last_selling_date",
        "days_since_last_sale",
        "days_since_last_sales",
        default=0,
    )


def _row_days_last_transfer(r: Dict[str, Any]) -> int:
    return _pick_int(
        r,
        "days_from_last_transfer",
        "days_from_last_transfer_date",
        "days_since_last_transfer",
        default=0,
    )


def _row_priority_score_value(r: Dict[str, Any]) -> float | None:
    v = r.get("priority_score")
    if v in (None, ""):
        v = r.get("Priority_Score")
    if v in (None, ""):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return round(f, 4)


def _row_classification_ok(r: Dict[str, Any], want: str) -> bool:
    """If `classification` is set (e.g. Fabric split), keep only matching rows; else allow all."""
    v = str(r.get("classification") or "").strip().lower()
    if not v:
        return True
    if want == "receiver":
        return v in ("receiver", "recv", "receivers", "potential_receiver", "potential_receivers")
    if want == "donor":
        return v in ("donor", "doner", "donors", "potential_donor", "potential_donors")
    return True


def _site_value(row: Dict[str, Any]) -> str:
    return str(row.get("site") or row.get("site_id") or "")


def _site_label(row: Dict[str, Any]) -> str:
    return str(row.get("site_name") or row.get("site") or row.get("site_id") or "")


def _parse_donor_sites_csv(raw: str | None) -> List[str]:
    if not raw:
        return []
    out: List[str] = []
    for part in str(raw).split(","):
        s = part.strip()
        if s and s not in ("All Sites", "All Stores", "All") and s not in out:
            out.append(s)
    return out


def _row_site_matches_any(row: Dict[str, Any], sites: List[str]) -> bool:
    if not sites:
        return True
    clean_sites = {s.strip().upper() for s in sites if s.strip() and s.strip().upper() not in ("ALL SITES", "ALL STORES", "ALL")}
    if not clean_sites:
        return True
    sid = str(row.get("site_id") or row.get("site") or "").strip().upper()
    sname = str(row.get("site_name") or "").strip().upper()
    if sid in clean_sites or sname in clean_sites:
        return True
    for cs in clean_sites:
        if sid and (sid == cs or sid.lstrip("P") == cs.lstrip("P") or f"({sid})" in cs or f"({sid.lstrip('P')})" in cs or sid in cs):
            return True
        if sname and (cs in sname or sname in cs):
            return True
    return False


def _rec_donor_site_matches(rec: Dict[str, Any], sites: List[str]) -> bool:
    if not sites:
        return True
    clean_sites = {s.strip().upper() for s in sites if s.strip() and s.strip().upper() not in ("ALL SITES", "ALL STORES", "ALL")}
    if not clean_sites:
        return True
    sid = str(rec.get("donor_store") or rec.get("donor_site_id") or "").strip().upper()
    sname = str(rec.get("donor_store_name") or "").strip().upper()
    if sid in clean_sites or sname in clean_sites:
        return True
    for cs in clean_sites:
        if sid and (sid == cs or sid.lstrip("P") == cs.lstrip("P") or f"({sid})" in cs or f"({sid.lstrip('P')})" in cs or sid in cs):
            return True
        if sname and (cs in sname or sname in cs):
            return True
    return False


def _rec_receiver_site_matches(rec: Dict[str, Any], sites: List[str]) -> bool:
    if not sites:
        return True
    clean_sites = {s.strip().upper() for s in sites if s.strip() and s.strip().upper() not in ("ALL SITES", "ALL STORES", "ALL")}
    if not clean_sites:
        return True
    sid = str(rec.get("receiver_store") or rec.get("receiver_site_id") or "").strip().upper()
    sname = str(rec.get("receiver_store_name") or rec.get("receiver_site_name") or "").strip().upper()
    if sid in clean_sites or sname in clean_sites:
        return True
    for cs in clean_sites:
        if sid and (sid == cs or sid.lstrip("P") == cs.lstrip("P") or f"({sid})" in cs or f"({sid.lstrip('P')})" in cs or sid in cs):
            return True
        if sname and (cs in sname or sname in cs):
            return True
    return False


def _sku_filter_ok(want_sku: str | None, row_sku: object) -> bool:
    if not want_sku or str(want_sku).strip() in ("All SKUs", "All", "", "ALL SKUS"):
        return True
    skus_set = {
        s.strip().upper()
        for s in str(want_sku).split(",")
        if s.strip() and s.strip().upper() not in ("ALL SKUS", "ALL")
    }
    if not skus_set:
        return True
    return str(row_sku or "").strip().upper() in skus_set


_CHANNEL_ALIASES = {
    "PANTALOONS": "PT",
    "PAN": "PT",
    "PT": "PT",
    "SHOPPERS STOP": "SSL",
    "SHOPPERS_STOP": "SSL",
    "SHOPPER STOP": "SSL",
    "SHOPPERSSTOP": "SSL",
    "SSL": "SSL",
    "CENTRO": "CENTRO",
    "ICONIC": "ICONIC",
    "EBO": "EBO",
}
_SKU_SEASON_CACHE: Dict[str, str] | None = None
_SKU_SEASON_CACHE_AT = 0.0


def _norm_channel(val: object) -> str:
    s = str(val or "").strip().upper()
    if s in {"", "NAN", "NONE", "<NA>", "NAT"}:
        return ""
    return _CHANNEL_ALIASES.get(s, s)


def _norm_season(val: object) -> str:
    s = str(val or "").strip().upper()
    if s in {"", "NAN", "NONE", "<NA>", "NAT", "ALL", "ALL SEASONS"}:
        return ""
    return s


def _selected_channels(raw: str | None) -> set[str]:
    return {c for c in (_norm_channel(p) for p in _parse_donor_sites_csv(raw)) if c}


def _selected_seasons(raw: str | None) -> set[str]:
    return {s for s in (_norm_season(p) for p in _parse_donor_sites_csv(raw)) if s}


def _sku_season_index() -> Dict[str, str]:
    """SKU -> season from donor/receiver rows, then the replenishment source."""
    global _SKU_SEASON_CACHE, _SKU_SEASON_CACHE_AT
    now = time.time()
    if _SKU_SEASON_CACHE is not None and (now - _SKU_SEASON_CACHE_AT) < _TRANSFER_TABLE_CACHE_TTL_SECONDS:
        return _SKU_SEASON_CACHE
    out: Dict[str, str] = {}
    for table in (POTENTIAL_DONORS_TABLE, POTENTIAL_RECEIVERS_TABLE, TRANSFER_SOURCE_TABLE):
        try:
            rows = _read_transfer_table(table) or []
        except Exception:
            rows = []
        for row in rows:
            sku = str(row.get("sku") or "").strip().upper()
            season = _norm_season(row.get("season"))
            if sku and season and sku not in out:
                out[sku] = season
    _SKU_SEASON_CACHE = out
    _SKU_SEASON_CACHE_AT = now
    return out


def _channel_filter_ok(row: Dict[str, Any], channels: set[str]) -> bool:
    if not channels:
        return True
    channel = _norm_channel(_row_store_type(row))
    if not channel:
        channel = _norm_channel(
            _derive_store_type_from_site(
                row.get("site_name")
                or row.get("donor_store_name")
                or row.get("receiver_store_name")
                or row.get("donor_site_name")
                or row.get("receiver_site_name"),
                row.get("site_id")
                or row.get("site")
                or row.get("donor_store")
                or row.get("receiver_store")
                or row.get("donor_site_id")
                or row.get("receiver_site_id"),
            )
        )
    return bool(channel) and channel in channels


def _season_filter_ok(row: Dict[str, Any], seasons: set[str], season_map: Dict[str, str]) -> bool:
    if not seasons:
        return True
    season = _norm_season(row.get("season") or row.get("donor_season") or row.get("receiver_season"))
    if not season:
        sku = str(row.get("sku") or "").strip().upper()
        season = season_map.get(sku, "")
    return bool(season) and season in seasons


_CLEARANCE_SAME_STATE_CHANNELS = frozenset({"PT", "CENTRO", "ICONIC", "EBO"})
_CLEARANCE_GEO_MODES = frozenset({"default", "in_state", "other_state"})


def _clearance_place(val: object) -> str:
    s = str(val or "").strip().lower()
    if s in {"", "nan", "none", "<na>", "null", "nat"}:
        return ""
    return s


def _clearance_geo_ok(
    donor_type: object,
    donor_state: object,
    recv_type: object,
    recv_state: object,
    mode: str,
    *,
    allow_cross_channel: bool = False,
) -> bool:
    """Default: PT/CENTRO/ICONIC/EBO stay in one state, SSL may cross state.

    in_state forces same state. other_state allows every state, including a
    different one. Blank state never counts as a same-state match.
    """
    dt = _norm_channel(donor_type)
    rt = _norm_channel(recv_type)
    if not allow_cross_channel and dt and rt and dt != rt:
        return False
    ds = _clearance_place(donor_state)
    rs = _clearance_place(recv_state)
    same_state = bool(ds and rs and ds == rs)
    geo = (mode or "default").strip().lower()
    if geo not in _CLEARANCE_GEO_MODES:
        geo = "default"
    if geo == "in_state":
        return same_state
    if geo == "other_state":
        return True
    channel = dt or rt
    if channel in _CLEARANCE_SAME_STATE_CHANNELS:
        return same_state
    return True


def _build_clearance_views(
    donor_sites: List[str],
    donor_sku: str | None,
    receiver_sites: List[str] | None = None,
    *,
    channels: List[str] | None = None,
    seasons: List[str] | None = None,
    geo_mode: str = "default",
    max_rows: int = 5000,
) -> Dict[str, Any]:
    """Donors, receivers, recommendations for one/many clearing stores, optional target receivers, and optional SKU."""
    donor_rows = list(_read_transfer_table(POTENTIAL_DONORS_TABLE) or [])
    receiver_rows = list(_read_transfer_table(POTENTIAL_RECEIVERS_TABLE) or [])
    rec_rows_raw = list(_read_transfer_table(RECOMMENDED_TRANSFERS_TABLE) or [])
    want_channels = {c for c in (_norm_channel(v) for v in (channels or [])) if c}
    want_seasons = {s for s in (_norm_season(v) for v in (seasons or [])) if s}
    geo = (geo_mode or "default").strip().lower()
    if geo not in _CLEARANCE_GEO_MODES:
        geo = "default"
    season_map = _sku_season_index() if want_seasons else {}

    donors_out: List[Dict[str, Any]] = []
    clearing_skus: set[str] = set()
    for r in donor_rows:
        nr = _normalize_row(_dedupe_keys_case_insensitive_prefer_lower(dict(r)))
        if not _row_classification_ok(nr, "donor"):
            continue
        if not _row_site_matches_any(nr, donor_sites):
            continue
        if not _channel_filter_ok(nr, want_channels):
            continue
        if not _season_filter_ok(nr, want_seasons, season_map):
            continue
        if not _sku_filter_ok(donor_sku, nr.get("sku")):
            continue
        sku_val = str(nr.get("sku") or "").strip()
        if sku_val:
            clearing_skus.add(sku_val.upper())
        donors_out.append(
            _json_safe_row(
                {
                    "site_id": str(nr.get("site_id") or nr.get("site") or "").strip(),
                    "site_name": str(nr.get("site_name") or nr.get("site") or "").strip(),
                    "sku": sku_val,
                    "barcode": str(nr.get("barcode") or nr.get("ean") or "").strip(),
                    "size": str(nr.get("size") or ""),
                    "store_soh": _safe_qty(nr.get("store_soh")),
                    "city": _row_city(nr),
                    "state": _row_state(nr),
                    "store_type": _row_store_type(nr),
                    "days_from_last_sales": _row_days_last_selling(nr),
                }
            )
        )
        if len(donors_out) >= max_rows:
            break

    target_receivers = [
        s for s in (receiver_sites or [])
        if s and s.strip() and s.strip().upper() not in ("ALL SITES", "ALL STORES", "ALL")
    ]

    receivers_out: List[Dict[str, Any]] = []
    for r in receiver_rows:
        nr = _normalize_row(_dedupe_keys_case_insensitive_prefer_lower(dict(r)))
        if not _row_classification_ok(nr, "receiver"):
            continue
        # Receiver store cannot be one of the clearing stores
        if _row_site_matches_any(nr, donor_sites):
            continue
        # If user specified target receivers, check that receiver matches one of them
        if target_receivers and not _row_site_matches_any(nr, target_receivers):
            continue
        if not _channel_filter_ok(nr, want_channels):
            continue
        if not _season_filter_ok(nr, want_seasons, season_map):
            continue
        sku_val = str(nr.get("sku") or "").strip()
        if not _sku_filter_ok(donor_sku, sku_val):
            if not clearing_skus or sku_val.upper() not in clearing_skus:
                continue
        elif not sku_val:
            continue
        # Crucial demand check: only consider receivers that actually need this SKU
        need_qty = _row_refill_for_potential(nr)
        if need_qty <= 0:
            continue
        receivers_out.append(
            _json_safe_row(
                {
                    "site_id": str(nr.get("site_id") or nr.get("site") or "").strip(),
                    "site_name": str(nr.get("site_name") or nr.get("site") or "").strip(),
                    "sku": sku_val,
                    "barcode": str(nr.get("barcode") or nr.get("ean") or "").strip(),
                    "size": str(nr.get("size") or ""),
                    "store_soh": _safe_qty(nr.get("store_soh")),
                    "qty_still_needed": need_qty,
                    "priority_score": nr.get("priority_score"),
                    "city": _row_city(nr),
                    "state": _row_state(nr),
                    "store_type": _row_store_type(nr),
                }
            )
        )
        if len(receivers_out) >= max_rows:
            break

    allow_cross_channel = bool(target_receivers)
    donor_geo: dict[str, tuple[str, str]] = {}
    for d in donors_out:
        sid = str(d.get("site_id") or "").strip().upper()
        if sid and sid not in donor_geo:
            donor_geo[sid] = (_norm_channel(d.get("store_type")), _clearance_place(d.get("state")))
    if donor_geo:
        receivers_out = [
            ro
            for ro in receivers_out
            if any(
                _clearance_geo_ok(
                    dtype,
                    dstate,
                    ro.get("store_type"),
                    ro.get("state"),
                    geo,
                    allow_cross_channel=allow_cross_channel,
                )
                for dtype, dstate in donor_geo.values()
            )
        ]

    rec_filtered = [
        r
        for r in rec_rows_raw
        if str(r.get("status", "PENDING")).upper() == "PENDING"
        and _rec_donor_site_matches(r, donor_sites)
        and (not target_receivers or _rec_receiver_site_matches(r, target_receivers))
        and _channel_filter_ok(r, want_channels)
        and _season_filter_ok(r, want_seasons, season_map)
        and _sku_filter_ok(donor_sku, r.get("sku"))
    ][:max_rows]

    try:
        replen_rows = _read_transfer_table(TRANSFER_SOURCE_TABLE)
        loc_idx = _replen_location_index(replen_rows) if replen_rows else {}
        site_name_idx = _site_name_index(replen_rows) if replen_rows else {}
    except Exception:
        loc_idx = {}
        site_name_idx = {}

    recommendations_out: List[Dict[str, Any]] = []
    seen_pairs: set[tuple] = set()

    for idx, r in enumerate(rec_filtered, start=1):
        row = dict(r)
        if "id" not in row:
            row["id"] = idx
        _map_rec_row(row)
        _enrich_recommendation_locations(row, loc_idx, site_name_idx)
        if not _clearance_geo_ok(
            row.get("donor_store_type") or row.get("store_type"),
            row.get("donor_state"),
            row.get("receiver_store_type") or row.get("store_type"),
            row.get("receiver_state"),
            geo,
            allow_cross_channel=allow_cross_channel,
        ):
            continue
        if not str(row.get("barcode") or "").strip():
            row["barcode"] = str(
                row.get("ean")
                or row.get("receiver_barcode")
                or row.get("donor_barcode")
                or row.get("rec_barcode")
                or ""
            ).strip()
        pair_k = (
            str(row.get("donor_store") or "").strip().upper(),
            str(row.get("receiver_store") or "").strip().upper(),
            str(row.get("sku") or "").strip().upper(),
            str(row.get("barcode") or "").strip().upper(),
        )
        seen_pairs.add(pair_k)
        recommendations_out.append(_json_safe_row(row))

    # Generate candidate transfer recommendations for clearing store SOH to potential receivers with demand
    if receivers_out and donors_out:
        rec_by_sku_bc: dict[tuple, list[dict[str, Any]]] = {}
        rec_by_sku_size: dict[tuple, list[dict[str, Any]]] = {}
        rec_by_sku: dict[str, list[dict[str, Any]]] = {}

        for ro in receivers_out:
            s_val = str(ro.get("sku") or "").strip().upper()
            b_val = str(ro.get("barcode") or "").strip().upper()
            sz_val = str(ro.get("size") or "").strip().upper()
            if s_val and b_val:
                rec_by_sku_bc.setdefault((s_val, b_val), []).append(ro)
            if s_val and sz_val:
                rec_by_sku_size.setdefault((s_val, sz_val), []).append(ro)
            if s_val:
                rec_by_sku.setdefault(s_val, []).append(ro)

        def _rec_sort_key(r_dict: dict[str, Any]) -> tuple[float, float]:
            try:
                ps = float(r_dict.get("priority_score") or 0.0)
            except Exception:
                ps = 0.0
            try:
                nd = float(r_dict.get("qty_still_needed") or 0.0)
            except Exception:
                nd = 0.0
            return (ps, nd)

        for bucket in rec_by_sku_bc.values():
            bucket.sort(key=_rec_sort_key, reverse=True)
        for bucket in rec_by_sku_size.values():
            bucket.sort(key=_rec_sort_key, reverse=True)
        for bucket in rec_by_sku.values():
            bucket.sort(key=_rec_sort_key, reverse=True)

        next_rec_id = len(recommendations_out) + 10001
        for d in donors_out:
            d_soh = int(d.get("store_soh") or 0)
            if d_soh <= 0:
                continue
            d_site = str(d.get("site_id") or d.get("site") or "").strip().upper()
            d_sku = str(d.get("sku") or "").strip().upper()
            d_bc = str(d.get("barcode") or "").strip().upper()
            d_size = str(d.get("size") or "").strip().upper()

            candidates: list[dict[str, Any]] = []
            seen_cand_ids: set[int] = set()

            if d_sku and d_bc and (d_sku, d_bc) in rec_by_sku_bc:
                for c in rec_by_sku_bc[(d_sku, d_bc)]:
                    cid = id(c)
                    if cid not in seen_cand_ids:
                        candidates.append(c)
                        seen_cand_ids.add(cid)

            if d_sku and d_size and (d_sku, d_size) in rec_by_sku_size:
                for c in rec_by_sku_size[(d_sku, d_size)]:
                    cid = id(c)
                    if cid not in seen_cand_ids:
                        candidates.append(c)
                        seen_cand_ids.add(cid)

            if not candidates and d_sku in rec_by_sku:
                for c in rec_by_sku[d_sku]:
                    cid = id(c)
                    if cid not in seen_cand_ids:
                        candidates.append(c)
                        seen_cand_ids.add(cid)

            candidates.sort(key=_rec_sort_key, reverse=True)

            for m_rec in candidates:
                if d_soh <= 0:
                    break
                m_site = str(m_rec.get("site_id") or m_rec.get("site") or "").strip().upper()
                if not m_site or m_site == d_site:
                    continue
                if not _clearance_geo_ok(
                    d.get("store_type"),
                    d.get("state"),
                    m_rec.get("store_type"),
                    m_rec.get("state"),
                    geo,
                    allow_cross_channel=allow_cross_channel,
                ):
                    continue
                pair_k = (
                    d_site,
                    m_site,
                    d_sku,
                    d_bc or d_size,
                )
                if pair_k in seen_pairs:
                    continue
                needed = int(m_rec.get("qty_still_needed") or 0)
                if needed <= 0:
                    continue
                tx_qty = min(d_soh, needed)
                if tx_qty <= 0:
                    continue
                seen_pairs.add(pair_k)
                d_soh -= tx_qty
                m_rec["qty_still_needed"] = needed - tx_qty
                rec_item = {
                    "id": next_rec_id,
                    "donor_store": d.get("site_id") or d.get("site"),
                    "donor_store_name": d.get("site_name") or d.get("site"),
                    "receiver_store": m_rec.get("site_id") or m_rec.get("site"),
                    "receiver_store_name": m_rec.get("site_name") or m_rec.get("site"),
                    "sku": d.get("sku") or m_rec.get("sku"),
                    "barcode": d.get("barcode") or m_rec.get("barcode"),
                    "size": d.get("size") or m_rec.get("size"),
                    "transfer_qty": tx_qty,
                    "transfer_qty_total": tx_qty,
                    "priority_score": m_rec.get("priority_score"),
                    "receiver_soh": m_rec.get("store_soh"),
                    "donor_soh": d.get("store_soh"),
                    "donor_city": d.get("city"),
                    "donor_state": d.get("state"),
                    "receiver_city": m_rec.get("city"),
                    "receiver_state": m_rec.get("state"),
                    "status": "PENDING",
                    "is_clearance": True,
                }
                _CLEARANCE_REC_CACHE[next_rec_id] = rec_item
                recommendations_out.append(_json_safe_row(rec_item))
                next_rec_id += 1

    donor_soh = sum(int(d.get("store_soh") or 0) for d in donors_out)
    unique_skus = len({d.get("sku") for d in donors_out if d.get("sku")})
    return {
        "donors": donors_out,
        "receivers": receivers_out,
        "recommendations": recommendations_out,
        "summary": {
            "donor_sites": donor_sites,
            "receiver_sites": target_receivers,
            "donor_sku": donor_sku or "All SKUs",
            "donor_rows": len(donors_out),
            "receiver_rows": len(receivers_out),
            "recommendation_rows": len(recommendations_out),
            "total_donor_soh": donor_soh,
            "unique_skus": unique_skus,
            "geo_mode": geo,
        },
    }


def _site_name_index(rows: List[Dict[str, Any]]) -> Dict[str, str]:
    """
    Build a lookup so recommendation rows can always resolve site_id/code to site_name.
    """
    idx: Dict[str, str] = {}
    for r in rows:
        site_id = str(r.get("site_id") or r.get("site") or "").strip()
        site_name = str(r.get("site_name") or "").strip()
        if not site_id or not site_name:
            continue
        idx[site_id] = site_name
        idx[site_id.lower()] = site_name
    return idx


def _receiver_refill_index(rows: List[Dict[str, Any]]) -> Dict[Tuple[str, str, str], int]:
    """
    Map (site_key, sku_key, size) -> recommendation_refill.
    Keys are uppercased site + normalized SKU so transfer rows match receiver / replen lookups.
    """
    idx: Dict[Tuple[str, str, str], int] = {}
    for r in rows:
        size = str(r.get("size") or "").strip().upper()
        refill = _row_refill_for_potential(r)
        sku_raw = str(r.get("sku") or "").strip()
        sku_n = _norm_transfer_sku_filter(r.get("sku")) or sku_raw.upper()
        site_keys: List[str] = []
        for k in ("site", "site_id", "site_name"):
            v = str(r.get(k) or "").strip()
            if v and v not in site_keys:
                site_keys.append(v)
        if not site_keys or not (sku_raw or sku_n):
            continue
        sku_variants = list({x for x in (sku_raw, sku_n) if x})
        for site in site_keys:
            su = site.upper()
            for sku_k in sku_variants:
                sk = _norm_transfer_sku_filter(sku_k) or str(sku_k).strip().upper()
                if not sk:
                    continue
                k3 = (su, sk, size)
                k2 = (su, sk, "")
                idx[k3] = max(refill, idx.get(k3, 0))
                idx[k2] = max(refill, idx.get(k2, 0))
    return idx


def _lookup_receiver_refill_from_index(idx: Dict[Tuple[str, str, str], int], rec: Dict[str, Any]) -> int:
    """Resolve recommendation_refill for a transfer row using receiver site / sku variants."""
    sku_raw = str(rec.get("sku") or "").strip()
    sku_n = _norm_transfer_sku_filter(rec.get("sku")) or sku_raw.upper()
    size = str(rec.get("size") or "").strip().upper()
    sites: List[str] = []
    for k in ("receiver_store", "receiver_site_id", "receiver_site", "receiver_store_name"):
        v = str(rec.get(k) or "").strip()
        if v and v not in sites:
            sites.append(v)
    best = 0
    for rs in sites:
        su = rs.upper()
        for sk in {x for x in (sku_raw, sku_n) if x}:
            skn = _norm_transfer_sku_filter(sk) or str(sk).strip().upper()
            if not skn:
                continue
            best = max(best, _safe_int(idx.get((su, skn, size))))
            best = max(best, _safe_int(idx.get((su, skn, ""))))
    return best


def _replen_receiver_lake_index(rows: List[Dict[str, Any]]) -> Dict[Tuple[str, str, str], Dict[str, Any]]:
    """
    (site_upper, sku_norm_upper, size_upper) -> OneLake receiver row facts for Replenishments-style columns.
    """
    idx: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for row in rows or []:
        sku_u = _norm_transfer_sku_filter(row.get("sku"))
        if not sku_u:
            continue
        size_u = str(row.get("size") or "").strip().upper()
        ts = _pick_int(row, "total_sales", "sum_of_sales", "sales")
        s5 = _pick_int(row, "sold_qty_prev_5w")
        l1 = _pick_int(row, "lag1_sold_qty", "last_week_sold_qty")
        week = str(row.get("week") or "").strip()
        wdr = str(row.get("week_date_range") or "").strip()
        lake_refill = _pick_int(
            row,
            "recommended_refill",
            "recommendation_refill",
            "refill",
            "forecasted_refill",
            "forecasted",
            "refill_recommandation",
            "refill_recommendation",
        )
        soh = _safe_qty(row.get("store_soh"))
        wh = _safe_qty(row.get("wh_soh"))
        season = str(row.get("season") or "").strip()
        bc = str(row.get("barcode") or "").strip()
        mon = str(row.get("month") or "").strip()
        fact: Dict[str, Any] = {
            "receiver_lake_total_sales": ts,
            "receiver_lake_sold_qty_prev_5w": s5,
            "receiver_lake_lag1_sold_qty": l1,
            "receiver_lake_week": week,
            "receiver_lake_week_date_range": wdr,
            "receiver_lake_refill": lake_refill,
            "receiver_lake_soh": soh,
            "receiver_lake_wh_soh": wh,
            "receiver_lake_season": season,
            "receiver_lake_barcode": bc,
            "receiver_lake_month": mon,
        }
        site_keys: set = set()
        for k in ("site_id", "site", "site_name"):
            v = str(row.get(k) or "").strip()
            if v:
                site_keys.add(v.upper())
        for su in site_keys:
            for sz_key in (size_u, ""):
                kt = (su, sku_u, sz_key)
                prev = idx.get(kt)
                if prev is None or ts >= _safe_int(prev.get("receiver_lake_total_sales", 0)):
                    idx[kt] = {**fact}
    return idx


def _enrich_rec_from_receiver_lake(rec: Dict[str, Any], lake_idx: Dict[Tuple[str, str, str], Dict[str, Any]]) -> None:
    if not lake_idx:
        return
    sku_u = _norm_transfer_sku_filter(rec.get("sku"))
    if not sku_u:
        return
    size = str(rec.get("size") or "").strip().upper()
    sites: List[str] = []
    for k in ("receiver_store", "receiver_site_id", "receiver_site", "receiver_store_name"):
        v = str(rec.get(k) or "").strip()
        if v and v not in sites:
            sites.append(v.upper())
    fact = None
    for su in sites:
        fact = lake_idx.get((su, sku_u, size)) or lake_idx.get((su, sku_u, ""))
        if fact:
            break
    if not fact:
        return
    for kk, vv in fact.items():
        rec[kk] = vv


def _movement_units_for_rec(rec: Dict[str, Any]) -> int:
    """Stable transfer qty for UI when total_units is missing / NaN-serialized."""
    tq = _safe_int(rec.get("transfer_qty"))
    if tq <= 0:
        tq = _safe_int(rec.get("transfer_qty_total"))
    if tq > 0:
        return tq
    tu = _safe_int(rec.get("total_units"))
    if tu > 0:
        return tu
    req = _safe_int(rec.get("required_size_units"))
    add = _safe_int(rec.get("additional_size_units"))
    if req + add > 0:
        return req + add
    refill = _safe_int(rec.get("receiver_recommended_refill"))
    rsoh = _safe_qty(rec.get("receiver_soh"))
    if rec.get("receiver_refill_is_net_unfilled") is True:
        need = max(refill, 0)
    else:
        need = max(refill - rsoh, 0)
    if need > 0:
        return max(need, 1)
    if req > 0:
        return req
    du = _safe_qty(rec.get("donor_store_soh") or rec.get("donor_soh"))
    if du > 0 and refill > 0:
        return min(max(refill, 1), max(du, 1))
    return max(tu, 1) if tu > 0 else 1


def _normalize_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {str(k).lower(): v for k, v in row.items()}


def _dedupe_keys_case_insensitive_prefer_lower(row: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parquet / Delta: schema rejects duplicate field names that differ only by case
    (e.g. Transfer_ID and transfer_id). Keep a single key per logical name; prefer all-lowercase.
    """
    groups: Dict[str, List[Tuple[str, Any]]] = {}
    for k, v in row.items():
        lk = str(k).lower()
        groups.setdefault(lk, []).append((k, v))
    out: Dict[str, Any] = {}
    for pairs in groups.values():
        if len(pairs) == 1:
            k0, v0 = pairs[0]
            out[k0] = v0
            continue
        lower_keys = [p for p in pairs if p[0] == str(p[0]).lower()]
        if lower_keys:
            k0, v0 = lower_keys[0]
            out[k0] = v0
        else:
            k0, v0 = pairs[0]
            out[k0] = v0
    return out


def _first_str_from_row(row: Dict[str, Any], *keys: str) -> str:
    for k in keys:
        v = row.get(k)
        if v is None:
            continue
        s = str(v).strip()
        low = s.lower()
        if s and low not in ("nan", "none", "null", "nat", ""):
            return s
    return ""


_STORE_LOC_CACHE: Dict[str, Tuple[str, str, str]] | None = None


def _store_location_index() -> Dict[str, Tuple[str, str, str]]:
    """site_code / site_manual_code / site_name -> (city, state, zone) from Customer Master."""
    global _STORE_LOC_CACHE
    if _STORE_LOC_CACHE is not None:
        return _STORE_LOC_CACHE
    out: Dict[str, Tuple[str, str, str]] = {}
    try:
        try:
            from customer_master import load_customer_master
        except ImportError:
            from pipeline.customer_master import load_customer_master  # type: ignore

        cm = load_customer_master(retail_only=False)
        if cm is not None and not getattr(cm, "empty", True):
            for _, r in cm.iterrows():
                city = str(r.get("city") or r.get("CITY") or "").strip()
                state = str(r.get("state") or r.get("state_name") or r.get("STATE NAME") or "").strip()
                zone = str(r.get("zone") or r.get("ZONE") or "").strip()
                if city.lower() in {"nan", "none", "<na>"}:
                    city = ""
                if state.lower() in {"nan", "none", "<na>"}:
                    state = ""
                if zone.lower() in {"nan", "none", "<na>"}:
                    zone = ""
                if not (city or state or zone):
                    continue
                loc = (city, state, zone)
                for col in ("site_code", "site_manual_code", "site_short_code"):
                    k = str(r.get(col) or "").strip().upper().replace(".0", "")
                    if k and k not in {"NAN", "NONE", "<NA>"}:
                        out[k] = loc
                nm = str(r.get("site_name_master") or r.get("site_name") or "").strip().upper()
                if nm and nm not in {"NAN", "NONE", "<NA>"}:
                    out[nm] = loc
    except Exception as exc:
        logger.debug("Store location index error: %s", exc)
    _STORE_LOC_CACHE = out
    return out


def _row_state(row: Dict[str, Any]) -> str:
    st = _first_str_from_row(
        row,
        "state",
        "site_state",
        "store_state",
        "statename",
        "state_name",
        "region",
        "province",
        "st",
        "cust_state",
        "ship_state",
        "billing_state",
        "statecode",
        "state_code",
    )
    if st:
        return st
    sid = _first_str_from_row(
        row, "site_id", "site", "receiver_site_id", "donor_site_id", "receiver_store", "donor_store"
    )
    if sid:
        loc = _store_location_index().get(sid.upper().replace(".0", ""))
        if loc and loc[1]:
            return loc[1]
    sname = _first_str_from_row(
        row, "site_name", "receiver_site_name", "donor_site_name", "receiver_store_name", "donor_store_name"
    )
    if sname:
        loc = _store_location_index().get(sname.upper())
        if loc and loc[1]:
            return loc[1]
    return ""


def _row_city(row: Dict[str, Any]) -> str:
    ct = _first_str_from_row(
        row,
        "city",
        "site_city",
        "store_city",
        "city_name",
        "location",
        "town",
        "cust_city",
        "ship_city",
        "billing_city",
    )
    if ct:
        return ct
    sid = _first_str_from_row(
        row, "site_id", "site", "receiver_site_id", "donor_site_id", "receiver_store", "donor_store"
    )
    if sid:
        loc = _store_location_index().get(sid.upper().replace(".0", ""))
        if loc and loc[0]:
            return loc[0]
    sname = _first_str_from_row(
        row, "site_name", "receiver_site_name", "donor_site_name", "receiver_store_name", "donor_store_name"
    )
    if sname:
        loc = _store_location_index().get(sname.upper())
        if loc and loc[0]:
            return loc[0]
    return ""


def _derive_store_type_from_site(site_name: object = "", site_id: object = None) -> str:
    """Fallback PT/SSL/EBO/… when lake row has no store_type."""
    try:
        from pipeline.pipeline_inputs import derive_store_type  # type: ignore
    except ImportError:
        try:
            from pipeline_inputs import derive_store_type  # type: ignore
        except ImportError:
            return ""
    try:
        return str(derive_store_type(site_name, site_id) or "").strip()
    except Exception:
        return ""


def _row_store_type(row: Dict[str, Any]) -> str:
    st = _first_str_from_row(
        row,
        "store_type",
        "storetype",
        "store_type_desc",
        "receiver_store_type",
        "donor_store_type",
        "channel",
        "outlet_type",
    )
    if st:
        return st
    site_id = _first_str_from_row(
        row, "site_id", "site", "receiver_site_id", "donor_site_id", "receiver_store", "donor_store"
    )
    site_name = _first_str_from_row(
        row, "site_name", "site", "receiver_site_name", "donor_site_name", "receiver_store_name", "donor_store_name"
    )
    return _derive_store_type_from_site(site_name, site_id)


def _infer_place_from_site(site: str) -> str:
    """
    When state/city columns are empty, derive a short human label from store/site text,
    e.g. 'SSL-GVK HYDERABAD( 122 )' -> 'GVK HYDERABAD'.
    """
    s = str(site or "").strip()
    if not s:
        return ""
    m = re.match(r"^\s*(?:SSL[-\s]+)?(.+?)\s*\(\s*\d+\s*\)\s*$", s, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    if s.lower().startswith("ssl-"):
        return s[4:].strip()
    return s


def _replen_location_index(rows: List[Dict[str, Any]]) -> Dict[Tuple[str, str, str], Tuple[str, str]]:
    """
    Map (site, sku, size) -> (state, city) from replenishment rows for enriching recommendations.
    """
    idx: Dict[Tuple[str, str, str], Tuple[str, str]] = {}
    for r in rows:
        site = _site_value(r)
        sku = str(r.get("sku") or "").strip()
        size = str(r.get("size") or "").strip().upper()
        if not site or not sku:
            continue
        st = _row_state(r)
        ct = _row_city(r)
        key = (site, sku, size)
        prev = idx.get(key, ("", ""))
        idx[key] = (st or prev[0], ct or prev[1])
        key_any = (site, sku, "")
        prev2 = idx.get(key_any, ("", ""))
        idx[key_any] = (st or prev2[0], ct or prev2[1])
    return idx


def _lookup_loc(
    idx: Dict[Tuple[str, str, str], Tuple[str, str]],
    site: str,
    sku: str,
    size: str,
) -> Tuple[str, str]:
    sku = str(sku or "").strip()
    size_u = str(size or "").strip().upper()
    st, ct = idx.get((site, sku, size_u), ("", ""))
    if st or ct:
        return st, ct
    return idx.get((site, sku, ""), ("", ""))


def _enrich_recommendation_locations(
    rec: Dict[str, Any],
    idx: Dict[Tuple[str, str, str], Tuple[str, str]],
    site_names: Dict[str, str] | None = None,
) -> None:
    """Fill missing donor/receiver state & city; add route labels for UI."""
    donor_site = str(rec.get("donor_store") or rec.get("donor_site_id") or "")
    recv_site = str(rec.get("receiver_store") or rec.get("receiver_site_id") or "")
    sku = str(rec.get("sku") or "")
    size = str(rec.get("size") or "")

    ds, dc = _lookup_loc(idx, donor_site, sku, size)
    rs, rc = _lookup_loc(idx, recv_site, sku, size)

    # If replen index didn't find city/state, fall back to Store Location Resolver
    locs = _store_location_index()
    if not dc or not ds:
        d_loc = locs.get(donor_site.upper().replace(".0", ""))
        if d_loc:
            dc = dc or d_loc[0]
            ds = ds or d_loc[1]
    if not rc or not rs:
        r_loc = locs.get(recv_site.upper().replace(".0", ""))
        if r_loc:
            rc = rc or r_loc[0]
            rs = rs or r_loc[1]

    if not str(rec.get("donor_state") or "").strip():
        rec["donor_state"] = ds or rec.get("donor_state")
    if not str(rec.get("donor_city") or "").strip():
        rec["donor_city"] = dc or rec.get("donor_city")
    if not str(rec.get("receiver_state") or "").strip():
        rec["receiver_state"] = rs or rec.get("receiver_state")
    if not str(rec.get("receiver_city") or "").strip():
        rec["receiver_city"] = rc or rec.get("receiver_city")

    # Ensure store names are available even when recommendation row has only site_id/code.
    site_names = site_names or {}
    donor_site_name = site_names.get(str(donor_site).strip()) or site_names.get(str(donor_site).strip().lower())
    recv_site_name = site_names.get(str(recv_site).strip()) or site_names.get(str(recv_site).strip().lower())
    if donor_site_name:
        rec["donor_store_name"] = donor_site_name
        rec["donor_site_name"] = donor_site_name
    if recv_site_name:
        rec["receiver_store_name"] = recv_site_name
        rec["receiver_site_name"] = recv_site_name

    rec["donor_place"] = str(rec.get("donor_city") or "").strip() or _infer_place_from_site(donor_site)
    rec["receiver_place"] = str(rec.get("receiver_city") or "").strip() or _infer_place_from_site(recv_site)
    rec["transfer_route_stores"] = f"{donor_site} → {recv_site}"
    rec["transfer_route_places"] = f"{rec['donor_place'] or '—'} → {rec['receiver_place'] or '—'}"


def _map_rec_row(r: Dict[str, Any], *, priority_score_scale_hint: str | None = None) -> Dict[str, Any]:
    """Formal mapping: Table Columns -> UI Keys."""
    if not r:
        return r
    r["donor_store"] = r.get("donor_site_id") or r.get("donor_store")
    r["donor_store_name"] = r.get("donor_site_name") or r.get("donor_store_name")
    r["receiver_store"] = r.get("receiver_site_id") or r.get("receiver_store")
    r["receiver_store_name"] = r.get("receiver_site_name") or r.get("receiver_store_name")
    if r.get("receiver_soh") in (None, ""):
        if r.get("receiver_store_soh") is not None:
            r["receiver_soh"] = r.get("receiver_store_soh")

    # Priority:
    # - Some lakes store `priority_score` in [0..1]
    # - Others store it in [0..10]
    # We support an optional hint so exports/UI can use your exact scale.
    used_score = False
    ps = r.get("priority_score")
    if ps is not None and str(ps).strip() not in ("", "nan", "none", "null"):
        pf = _to_float(ps)
        if math.isfinite(pf) and pf > 0:
            used_score = True
            # If hint provided, use it even if pf happens to be <= 1.
            if priority_score_scale_hint == "0_1":
                r["priority"] = "HIGH" if pf >= 0.7 - 1e-9 else "MEDIUM" if pf >= 0.4 - 1e-9 else "LOW"
            elif priority_score_scale_hint == "0_10":
                r["priority"] = "HIGH" if pf > 7 + 1e-9 else "MEDIUM" if pf >= 4 - 1e-9 else "LOW"
            else:
                # No hint: infer from magnitude.
                if pf <= 1.0 + 1e-9:
                    # New lake style: priority_score in [0..1]
                    r["priority"] = "HIGH" if pf >= 0.7 - 1e-9 else "MEDIUM" if pf >= 0.4 - 1e-9 else "LOW"
                else:
                    # Legacy style: priority_score in [0..10] (or outside typical range)
                    r["priority"] = "HIGH" if pf > 7 + 1e-9 else "MEDIUM" if pf >= 4 - 1e-9 else "LOW"
    if not used_score:
        raw_p = _safe_int(r.get("receiver_priority") or r.get("priority"), 0)
        if raw_p >= 10:
            r["priority"] = "HIGH"
        elif 5 <= raw_p <= 9:
            r["priority"] = "MEDIUM"
        else:
            r["priority"] = "LOW"

    line_move = r.get("transfer_qty")
    if line_move in (None, ""):
        line_move = r.get("transfer_qty_total")
    r["receiver_recommended_refill"] = _safe_int(line_move or r.get("receiver_recommended_refill"))
    # UI / older clients often read `transfer_qty` only — mirror lake `transfer_qty_total` when absent.
    if r.get("transfer_qty") in (None, "") and r.get("transfer_qty_total") is not None:
        r["transfer_qty"] = r.get("transfer_qty_total")
    if r.get("warehouse_soh_after_allocation") in (None, "") and r.get("wh_soh_end_barcode") is not None:
        r["warehouse_soh_after_allocation"] = r.get("wh_soh_end_barcode")
    if r.get("size") in (None,):
        r["size"] = ""
    return r


# dbo.current_transfers managed Delta — columns match PySpark StructType in transfers tab / approve API.
# Approve paths project to _build_spark_current_transfers_row() only (avoids writing full recommendation dict).


def _utc_naive_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _value_to_naive_timestamp(v: Any, fallback: Optional[datetime]) -> Optional[datetime]:
    """Coerce lake / API values to naive datetime for Delta TimestampType."""
    if v is None or v == "":
        return fallback
    if isinstance(v, datetime):
        return v.replace(tzinfo=None) if v.tzinfo else v
    try:
        fn = float(v)
        if not math.isfinite(fn):
            return fallback
        if fn > 1e12:
            fn /= 1000.0
        return datetime.utcfromtimestamp(fn)
    except (TypeError, ValueError, OverflowError):
        pass
    s = str(v).strip()
    if not s or s.lower() in ("nan", "none", "null", "nat"):
        return fallback
    try:
        iso = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso)
        return dt.replace(tzinfo=None) if dt.tzinfo else dt
    except Exception:
        return fallback


def _spark_str(rec: Dict[str, Any], *keys: str) -> str:
    for k in keys:
        if k not in rec:
            continue
        x = rec.get(k)
        if x is None:
            continue
        t = str(x).strip()
        if t and t.lower() not in ("nan", "none", "null"):
            return t
    return ""


def _build_spark_current_transfers_row(
    mapped: Dict[str, Any],
    *,
    transfer_id: int,
    status: str,
    created_at: datetime,
    transfer_date_value: datetime,
    recommendation_created_at_raw: Any,
) -> Dict[str, Any]:
    """
    Single row matching dbo.current_transfers DDL. `mapped` must already be `_map_rec_row`-processed
    (+ optional `_enrich_recommendation_locations`) so donor/receiver_* align with donor_site_* keys.
    """
    r = mapped
    qty = r.get("transfer_qty")
    if qty in (None, ""):
        qty = r.get("transfer_qty_total")

    rc_at = _value_to_naive_timestamp(recommendation_created_at_raw, transfer_date_value)
    td = transfer_date_value
    pf = _to_float(r.get("priority_score"))

    row = {
        "transfer_id": max(0, _safe_int(transfer_id, 0)),
        "receiver_site_id": _spark_str(r, "receiver_site_id", "receiver_store"),
        "receiver_site_name": _spark_str(r, "receiver_site_name", "receiver_store_name"),
        "donor_site_id": _spark_str(r, "donor_site_id", "donor_store"),
        "donor_site_name": _spark_str(r, "donor_site_name", "donor_store_name"),
        "sku": _spark_str(r, "sku"),
        "receiver_city": _spark_str(r, "receiver_city"),
        "receiver_state": _spark_str(r, "receiver_state"),
        "donor_city": _spark_str(r, "donor_city"),
        "donor_state": _spark_str(r, "donor_state"),
        "store_type": _spark_str(r, "store_type"),
        "transfer_qty": _to_float(qty or 0),
        "match_type": _spark_str(r, "match_type"),
        "priority_score": pf,
        "status": status,
        "transfer_date": td,
        "created_at": created_at,
        "recommendation_created_at": rc_at if rc_at is not None else td,
    }
    return _dedupe_keys_case_insensitive_prefer_lower(row)


def _json_safe(value: Any) -> Any:
    # Convert NaN/Infinity to None so FastAPI JSON rendering does not fail.
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    try:
        # Handles numpy/pandas scalar NaN/NaT as well.
        if pd.isna(value):
            return None
    except Exception:
        pass
    return value


def _json_safe_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {k: _json_safe(v) for k, v in row.items()}


# Excel "Current Transfers" sheet: same column set + order as Transfers tab In transit (TransfersTab IN_TRANSIT_ONE_LAKE_COLS).
_CURRENT_TRANSIT_TAB_EXPORT_SPEC: Tuple[Tuple[str, Tuple[str, ...], str], ...] = (
    ("transfer_id", ("transfer_id", "Transfer_ID", "transfer_ID"), "txt"),
    ("receiver_site_id", ("receiver_site_id", "receiver_store"), "txt"),
    ("receiver_site_name", ("receiver_site_name", "receiver_store_name"), "txt"),
    ("donor_site_id", ("donor_site_id", "donor_store"), "txt"),
    ("donor_site_name", ("donor_site_name", "donor_store_name"), "txt"),
    ("sku", ("sku",), "txt"),
    ("receiver_city", ("receiver_city",), "txt"),
    ("receiver_state", ("receiver_state",), "txt"),
    ("donor_city", ("donor_city",), "txt"),
    ("donor_state", ("donor_state",), "txt"),
    ("store_type", ("store_type",), "txt"),
    ("transfer_qty", ("transfer_qty", "transfer_qty_total", "receiver_recommended_refill"), "qty"),
    ("match_type", ("match_type",), "txt"),
    ("priority_score", ("priority_score",), "score"),
    ("status", ("status", "transfer_status"), "txt"),
    ("transfer_date", ("transfer_date", "Transfer_Date"), "dt_or_txt"),
    ("created_at (approved)", ("created_at",), "dt"),
    ("recommendation_created_at", ("recommendation_created_at",), "dt"),
)


def _pick_in_transit_export_value(r_lc: Dict[str, Any], keys: Tuple[str, ...]) -> Any:
    for want in keys:
        kk = str(want).lower()
        if kk not in r_lc:
            continue
        v = r_lc[kk]
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        return v
    return None


def _excel_ts_display(v: Any) -> str:
    if v is None or v == "":
        return ""
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    try:
        dt = _value_to_naive_timestamp(v, None)
        if dt is not None:
            return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        pass
    s = str(v).strip()
    return s


def _export_in_transit_cell(v: Any, kind: str) -> Any:
    if kind == "txt":
        return "" if v is None else str(v)
    if kind == "qty":
        if v is None:
            return ""
        try:
            x = float(v)
            if not math.isfinite(x):
                return ""
            if abs(x - round(x)) < 1e-9:
                return int(round(x))
            return round(x, 2)
        except Exception:
            return ""
    if kind == "score":
        if v is None or v == "":
            return ""
        try:
            return round(float(v), 2)
        except Exception:
            return ""
    if kind == "dt":
        return _excel_ts_display(v)
    if kind == "dt_or_txt":
        if v is None:
            return ""
        if isinstance(v, datetime):
            return v.strftime("%Y-%m-%d %H:%M:%S")
        s = str(v).strip()
        if not s:
            return ""
        dt = _value_to_naive_timestamp(v, None)
        if dt is not None:
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        return s
    return "" if v is None else str(v)


def _dataframe_current_transfers_in_transit_tab(rows_raw: List[Dict[str, Any]]) -> pd.DataFrame:
    """
    Build Current Transfers sheet with only the columns shown on the Transfers > In transit tab
    (mapping + enrich parity with GET /transfers-recom/current).
    """
    headers = [h for h, _, _ in _CURRENT_TRANSIT_TAB_EXPORT_SPEC]
    if not rows_raw:
        return pd.DataFrame(columns=headers)

    try:
        replen_rows = _read_transfer_table(TRANSFER_SOURCE_TABLE)
        loc_idx = _replen_location_index(replen_rows) if replen_rows else {}
        site_name_idx = _site_name_index(replen_rows) if replen_rows else {}
    except Exception:
        loc_idx = {}
        site_name_idx = {}

    out: List[Dict[str, Any]] = []
    for raw in rows_raw:
        rr = dict(raw)
        _map_rec_row(rr)
        _enrich_recommendation_locations(rr, loc_idx, site_name_idx)
        rr = _json_safe_row(rr)
        r_lc = {str(k).lower(): v for k, v in rr.items()}
        row_out: Dict[str, Any] = {}
        for hdr, keys, kind in _CURRENT_TRANSIT_TAB_EXPORT_SPEC:
            v = _pick_in_transit_export_value(r_lc, keys)
            if hdr == "store_type" and (v is None or str(v).strip() == ""):
                v = _row_store_type(r_lc)
            row_out[hdr] = _export_in_transit_cell(v, kind)
        out.append(row_out)

    df = pd.DataFrame(out, columns=headers)
    try:
        for col in df.select_dtypes(include=["datetimetz"]).columns:
            df[col] = df[col].dt.tz_localize(None)
    except Exception:
        pass
    return df


def _to_float(v: Any) -> float:
    try:
        if v is None:
            return 0.0
        if isinstance(v, str):
            s = v.strip().lower()
            if s in ("", "nan", "none", "null"):
                return 0.0
            x = float(s)
        else:
            x = float(v)
        try:
            if pd.isna(x):
                return 0.0
        except Exception:
            pass
        if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
            return 0.0
        return x
    except Exception:
        return 0.0


def _to_int(v: Any) -> int:
    x = _to_float(v)
    try:
        return int(x)
    except (ValueError, OverflowError):
        return 0


def _is_missing_transfer_date(row: Dict[str, Any]) -> bool:
    raw = (
        row.get("last_transfer_date")
        or row.get("Last_Transfer_Date")
        or row.get("last_transfer_dt")
        or row.get("Last_Transfer_Dt")
    )
    if raw is None:
        return True
    text = str(raw).strip().lower()
    return text in ("", "nan", "none", "null", "nat")


def _build_zero_soh_summary(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    total_soh = 0
    total_soh_no_transfer_date = 0
    total_revenue = 0.0
    sku_rollup: Dict[str, Dict[str, Any]] = {}
    site_rollup: Dict[str, Dict[str, Any]] = {}

    for row in rows:
        sku = str(row.get("sku") or row.get("SKU") or "").strip()
        soh = _to_int(row.get("store_soh") or row.get("Store_Soh"))
        amt = _to_float(row.get("total_amt") or row.get("Total_Amt"))
        total_soh += soh
        if _is_missing_transfer_date(row):
            total_soh_no_transfer_date += soh
        total_revenue += amt
        site = str(row.get("site") or row.get("Site") or row.get("site_id") or row.get("Site_ID") or "").strip()
        if site:
            site_item = site_rollup.setdefault(site, {"site": site, "total_stock_amount": 0.0, "total_soh": 0})
            site_item["total_stock_amount"] += amt
            site_item["total_soh"] += soh
        if not sku:
            continue
        item = sku_rollup.setdefault(sku, {"sku": sku, "total_soh": 0, "total_revenue": 0.0, "soh_count": 0, "total_stock_amount": 0.0})
        item["total_soh"] += soh
        item["total_revenue"] += amt
        item["total_stock_amount"] += amt
        item["soh_count"] += 1

    top_skus = sorted(sku_rollup.values(), key=lambda x: x["total_soh"], reverse=True)[:10]
    for s in top_skus:
        s["total_revenue"] = round(float(s["total_revenue"]), 2)
        s["total_stock_amount"] = round(float(s["total_stock_amount"]), 2)
    top_skus_by_amount = sorted(sku_rollup.values(), key=lambda x: x["total_stock_amount"], reverse=True)[:5]
    for s in top_skus_by_amount:
        s["total_revenue"] = round(float(s["total_revenue"]), 2)
        s["total_stock_amount"] = round(float(s["total_stock_amount"]), 2)

    top_sites_by_amount = sorted(site_rollup.values(), key=lambda x: x["total_stock_amount"], reverse=True)[:5]
    for s in top_sites_by_amount:
        s["total_stock_amount"] = round(float(s["total_stock_amount"]), 2)

    return {
        "total_soh": int(total_soh),
        "total_soh_no_transfer_date": int(total_soh_no_transfer_date),
        "total_soh_with_transfer_date": int(total_soh - total_soh_no_transfer_date),
        "total_revenue": round(float(total_revenue), 2),
        "top_skus": top_skus,
        "top_sites_by_amount": top_sites_by_amount,
        "top_skus_by_amount": top_skus_by_amount,
    }


def _safe_transfer_cache_name(table_name: str) -> str:
    return re.sub(r"[^\w.\-]+", "_", str(table_name or "").strip())


def _find_local_transfer_file(table_name: str) -> Path | None:
    """
    Local-first paths (no Azure round-trip):
      1) data/api_cache/transfers/<table>.parquet  (durable demo cache)
      2) data/blob_cache/output_<table>.{parquet,csv.parquet,csv,json}
    """
    try:
        from api.blob_store import _CACHE_DIR, _safe_name
    except Exception:
        _CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "blob_cache"

        def _safe_name(blob_name: str) -> str:  # type: ignore
            return re.sub(r"[^\w.\-]+", "_", blob_name)

    api_pq = _API_CACHE_DIR / f"{_safe_transfer_cache_name(table_name)}.parquet"
    if api_pq.exists() and api_pq.stat().st_size > 0:
        return api_pq

    for ext in (".parquet", ".csv", ".json"):
        blob_name = f"output/{table_name}{ext}"
        path = _CACHE_DIR / _safe_name(blob_name)
        if not path.exists() or path.stat().st_size <= 0:
            continue
        if ext == ".csv":
            sidecar = Path(str(path) + ".parquet")
            if sidecar.exists() and sidecar.stat().st_size > 0:
                return sidecar
        return path
    return None


def _rows_from_local_path(path: Path) -> List[Dict[str, Any]]:
    lower = path.name.lower()
    try:
        if lower.endswith(".parquet"):
            df = pd.read_parquet(path)
            return df.where(pd.notnull(df), None).to_dict(orient="records") if df is not None else []
        if lower.endswith(".csv"):
            df = pd.read_csv(path, low_memory=False)
            return df.where(pd.notnull(df), None).to_dict(orient="records") if df is not None else []
        if lower.endswith(".json"):
            import json

            parsed = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, dict) and isinstance(parsed.get("data"), list):
                return parsed["data"]
    except Exception as exc:
        logger.warning("Local transfer file read failed (%s): %s", path.name, exc)
    return []


def _persist_transfer_disk(table_name: str, rows: List[Dict[str, Any]]) -> None:
    """Write durable parquet under data/api_cache/transfers for next cold start."""
    if not rows:
        return
    try:
        _API_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        out = _API_CACHE_DIR / f"{_safe_transfer_cache_name(table_name)}.parquet"
        pd.DataFrame(rows).to_parquet(out, index=False)
        logger.info(
            "Transfer disk cache wrote %s (%d rows, %.1f MB)",
            out.name,
            len(rows),
            out.stat().st_size / 1e6,
        )
    except Exception as exc:
        logger.warning("Transfer disk cache write failed for %s: %s", table_name, exc)


def _read_transfer_table(table_name: str, *, bypass_cache: bool = False) -> List[Dict[str, Any]]:
    """
    Local disk → RAM first (demo/fast pages), then Azure blob/OneLake.
    bypass_cache=True (refresh=1) skips RAM but still prefers local files before network.
    """
    now = time.time()
    if not bypass_cache:
        with _TRANSFER_LOCK:
            cached_rows = _TRANSFER_TABLE_CACHE.get(table_name)
            cached_at = _TRANSFER_TABLE_CACHE_AT.get(table_name, 0.0)
            if cached_rows is not None and (now - cached_at) < _TRANSFER_TABLE_CACHE_TTL_SECONDS:
                return cached_rows

    # 1) Local files already on disk (blob_cache / api_cache) — no Azure ping
    local = _find_local_transfer_file(table_name)
    if local is not None:
        rows = _rows_from_local_path(local)
        if rows:
            normalized = [_normalize_row(r) for r in rows]
            with _TRANSFER_LOCK:
                _TRANSFER_TABLE_CACHE[table_name] = normalized
                _TRANSFER_TABLE_CACHE_AT[table_name] = now
            # Ensure durable api_cache copy exists for next restart
            api_pq = _API_CACHE_DIR / f"{_safe_transfer_cache_name(table_name)}.parquet"
            if not api_pq.exists():
                _persist_transfer_disk(table_name, normalized)
            logger.info(
                "Transfer table %s from local %s (%d rows)",
                table_name,
                local.name,
                len(normalized),
            )
            return normalized

    # 2) Azure / OneLake (may download into blob_cache)
    try:
        rows = store.read_delta_table(table_name)
        if rows:
            normalized = [_normalize_row(r) for r in rows]
            with _TRANSFER_LOCK:
                _TRANSFER_TABLE_CACHE[table_name] = normalized
                _TRANSFER_TABLE_CACHE_AT[table_name] = now
            _persist_transfer_disk(table_name, normalized)
            return normalized
    except Exception:
        pass
    try:
        rows = store.read_table(table_name)
        if rows:
            normalized = [_normalize_row(r) for r in rows]
            with _TRANSFER_LOCK:
                _TRANSFER_TABLE_CACHE[table_name] = normalized
                _TRANSFER_TABLE_CACHE_AT[table_name] = now
            _persist_transfer_disk(table_name, normalized)
            return normalized
    except Exception:
        pass
    return []


def warm_transfers_recom_cache(*, force_refresh: bool = False) -> dict:
    """
    Pre-load donors / receivers / recommendations / current / zero-SOH from local files
    into RAM + data/api_cache/transfers/*.parquet for instant Transfers tab.
    """
    t0 = time.perf_counter()
    tables = [
        RECOMMENDED_TRANSFERS_TABLE,
        POTENTIAL_DONORS_TABLE,
        POTENTIAL_RECEIVERS_TABLE,
        "current_transfers",
        ZERO_SOH_TABLE,
        # Last: large refill file used for location enrichment on /recommendations
        TRANSFER_SOURCE_TABLE,
    ]
    if force_refresh:
        with _TRANSFER_LOCK:
            for t in tables:
                _TRANSFER_TABLE_CACHE.pop(t, None)
                _TRANSFER_TABLE_CACHE_AT.pop(t, None)

    counts: dict[str, int] = {}
    for name in tables:
        try:
            rows = _read_transfer_table(name, bypass_cache=force_refresh)
            counts[name] = len(rows or [])
            if rows:
                _persist_transfer_disk(name, rows)
        except Exception as exc:
            counts[name] = -1
            logger.warning("Transfer warm failed for %s: %s", name, exc)

    elapsed = round(time.perf_counter() - t0, 1)
    ok = any(v > 0 for v in counts.values())
    logger.info("Transfers cache warm ok=%s in %.1fs counts=%s", ok, elapsed, counts)
    return {"ok": ok, "elapsed_s": elapsed, "tables": counts}


@router.post("/warm-cache")
@router.get("/warm-cache")
def warm_transfers_http(force_refresh: bool = False):
    """Demo / ops: load transfer tables from local disk into RAM + default page_cache."""
    result = warm_transfers_recom_cache(force_refresh=force_refresh)
    # Pre-build first UI pages (TransfersTab chunks: recs=200, data=300)
    try:
        import asyncio

        asyncio.run(get_recommended_transfers(limit=200, offset=0, refresh=1))
        result["recommendations_page_cache"] = True
    except Exception as exc:
        logger.warning("Transfer recommendations page_cache warm failed: %s", exc)
        result["recommendations_page_cache"] = False
    try:
        import asyncio

        asyncio.run(
            get_transfers_data(
                r_limit=300, d_limit=300, r_off=0, d_off=0, refresh=1
            )
        )
        result["data_page_cache"] = True
    except Exception as exc:
        logger.warning("Transfer data page_cache warm failed: %s", exc)
        result["data_page_cache"] = False
    return result


def _write_transfer_table(table_name: str, rows: List[Dict[str, Any]]) -> None:
    """
    Write transfer datasets directly to Lakehouse tables, fallback to Files.
    """
    try:
        store.replace_delta_table(table_name, rows)
    except Exception:
        store.replace_table(table_name, rows)
    # Invalidate and warm cache with fresh rows
    normalized = [_normalize_row(r) for r in rows]
    _TRANSFER_TABLE_CACHE[table_name] = normalized
    _TRANSFER_TABLE_CACHE_AT[table_name] = time.time()


def _receiver_residual_need_qty(receiver: Dict[str, Any]) -> int:
    """
    Units still needed for transfer scoring / qty caps.
    When sync stored `refill_is_net_unfilled`, `recommended_refill` is already lake unfilled (net of store+WH)
    — do not subtract store_soh again, and do not floor at 1 (that made every line look like need=1).
    Legacy path: gross recommended_refill minus store SOH (min 0).
    """
    refill = _safe_int(receiver.get("recommended_refill"))
    if receiver.get("refill_is_net_unfilled"):
        return max(refill, 0)
    return max(refill - _safe_qty(receiver.get("store_soh")), 0)


def _build_recommendations(donors: List[Dict[str, Any]], receivers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    donor_map: Dict[tuple, List[Dict[str, Any]]] = {}
    donor_stack_sizes: Dict[tuple, set] = {}
    donor_qty_by_size: Dict[tuple, int] = {}
    receiver_needed_sizes: Dict[tuple, set] = {}

    for donor in donors:
        sku = str(donor.get("sku") or "").strip()
        size = str(donor.get("size") or "").upper().strip()
        site = _site_value(donor)
        if not sku or not size or not site:
            continue
        donor_map.setdefault((sku, size), []).append(donor)
        if _safe_qty(donor.get("store_soh")) > 0:
            donor_stack_sizes.setdefault((site, sku), set()).add(size)
            donor_qty_by_size[(site, sku, size)] = _safe_qty(donor.get("store_soh"))

    for receiver in receivers:
        sku = str(receiver.get("sku") or "").strip()
        size = str(receiver.get("size") or "").upper().strip()
        site = _site_value(receiver)
        if not sku or not size or not site:
            continue
        need_qty = _receiver_residual_need_qty(receiver)
        if need_qty > 0:
            receiver_needed_sizes.setdefault((site, sku), set()).add(size)

    # Highest need first
    sorted_receivers = sorted(
        receivers,
        key=lambda r: (
            _receiver_residual_need_qty(r),
            -_safe_qty(r.get("store_soh")),
        ),
        reverse=True,
    )

    recommendations: List[Dict[str, Any]] = []
    now = OneLakeJsonStore.now_iso()
    next_id = 1
    used_donor_size_pool: Dict[tuple, int] = {}

    for receiver in sorted_receivers:
        r_sku = str(receiver.get("sku") or "").strip()
        r_size = str(receiver.get("size") or "").upper().strip()
        r_site = _site_value(receiver)
        if not r_sku or not r_size or not r_site:
            continue

        needed_qty = _receiver_residual_need_qty(receiver)
        if needed_qty <= 0:
            continue
        needed_sizes = receiver_needed_sizes.get((r_site, r_sku), {r_size})
        matches = donor_map.get((r_sku, r_size), [])
        if not matches:
            continue

        best = None
        best_score = -10**9

        for donor in matches:
            donor_site = _site_value(donor)
            if not donor_site or donor_site == r_site:
                continue
            if str(donor.get("store_type") or "") != str(receiver.get("store_type") or ""):
                continue

            donor_size_key = (donor_site, r_sku, r_size)
            allocated = used_donor_size_pool.get(donor_size_key, 0)
            available = max(_safe_qty(donor.get("store_soh")) - allocated - 1, 0)  # keep at least 1 unit safety
            if available <= 0:
                continue

            donor_sizes = donor_stack_sizes.get((donor_site, r_sku), set())
            covered = len(needed_sizes.intersection(donor_sizes))
            total_needed_sizes = max(len(needed_sizes), 1)
            coverage_ratio = covered / total_needed_sizes

            score = 0
            score += min(needed_qty, 10) * 10  # demand priority
            score += min(available, 10) * 4     # supply capability
            if coverage_ratio >= 1.0:
                score += 50                      # full stack support
            elif coverage_ratio >= 0.7:
                score += 25
            elif coverage_ratio >= 0.4:
                score += 10
            if str(donor.get("state") or "") and str(donor.get("state") or "") == str(receiver.get("state") or ""):
                score += 8                       # same-state logistics preference

            if score > best_score:
                best_score = score
                best = donor

        if not best:
            continue

        donor_site = _site_value(best)
        donor_sizes = donor_stack_sizes.get((donor_site, r_sku), set())
        covered = len(needed_sizes.intersection(donor_sizes))
        total_needed_sizes = max(len(needed_sizes), 1)
        full_stack_possible = covered >= total_needed_sizes

        donor_size_key = (donor_site, r_sku, r_size)
        allocated = used_donor_size_pool.get(donor_size_key, 0)
        available = max(_safe_qty(best.get("store_soh")) - allocated - 1, 0)
        transfer_units = max(min(needed_qty, available), 1)
        used_donor_size_pool[donor_size_key] = allocated + transfer_units

        # Bundle behavior: required size must transfer; send 1 unit of other sizes if available.
        additional_sizes_sent: List[str] = []
        additional_units = 0
        for sz in sorted(donor_sizes):
            if sz == r_size:
                continue
            key = (donor_site, r_sku, sz)
            raw_qty = donor_qty_by_size.get(key, 0)
            alloc_sz = used_donor_size_pool.get(key, 0)
            avail_sz = max(raw_qty - alloc_sz - 1, 0)  # keep donor safety 1
            if avail_sz <= 0:
                continue
            used_donor_size_pool[key] = alloc_sz + 1
            additional_sizes_sent.append(sz)
            additional_units += 1

        priority = "HIGH" if _safe_qty(receiver.get("store_soh")) <= 0 or needed_qty >= 3 else "MEDIUM"
        recommendations.append(
            {
                "id": next_id,
                "transfer_id": next_id,
                "transfer_status": "PENDING",
                "sku": r_sku,
                "size": receiver.get("size"),
                "barcode": best.get("barcode"),
                "donor_store": donor_site,
                "donor_site_id": best.get("site_id") or donor_site,
                "donor_site": str(best.get("site_name") or best.get("site") or donor_site),
                "donor_store_name": str(best.get("site_name") or best.get("site") or donor_site),
                "donor_state": _row_state(best) or best.get("state"),
                "donor_city": _row_city(best) or best.get("city"),
                "donor_soh": _safe_qty(best.get("store_soh")),
                "donor_store_soh": _safe_qty(best.get("store_soh")),
                "donor_wh_soh": _safe_qty(best.get("wh_soh")),
                "donor_recommended_refill": _safe_int(best.get("recommended_refill")),
                "donor_department": best.get("department"),
                "donor_season": best.get("season"),
                "donor_store_type": best.get("store_type"),
                "donor_days_since_last_sale": _safe_int(best.get("days_since_last_sale")),
                "donor_days_from_first_transfer_date": _safe_int(best.get("days_since_last_transfer")),
                "donor_sold_qty_last_5w": _safe_int(best.get("sold_qty_prev_5w")),
                "donor_net_sales_last_35d": _safe_int(best.get("net_sales_last_35d")),
                "donor_selling_rate": 0.0,
                "receiver_store": r_site,
                "receiver_site_id": receiver.get("site_id") or r_site,
                "receiver_site": str(receiver.get("site_name") or receiver.get("site") or r_site),
                "receiver_store_name": str(receiver.get("site_name") or receiver.get("site") or r_site),
                "receiver_state": _row_state(receiver) or receiver.get("state"),
                "receiver_city": _row_city(receiver) or receiver.get("city"),
                "receiver_soh": _safe_qty(receiver.get("store_soh")),
                "receiver_store_soh": _safe_qty(receiver.get("store_soh")),
                "receiver_wh_soh": _safe_qty(receiver.get("wh_soh")),
                "receiver_department": receiver.get("department"),
                "receiver_season": receiver.get("season"),
                "receiver_store_type": receiver.get("store_type"),
                "receiver_days_since_last_sale": _safe_int(receiver.get("days_since_last_sale")),
                "receiver_days_from_first_transfer_date": _safe_int(receiver.get("days_since_last_transfer")),
                "receiver_sold_qty_last_5w": _safe_int(receiver.get("sold_qty_prev_5w")),
                "receiver_net_sales_last_35d": _safe_int(receiver.get("net_sales_last_35d")),
                "receiver_selling_rate": 0.0,
                "receiver_recommended_refill": _safe_int(receiver.get("recommended_refill")),
                "receiver_refill_is_net_unfilled": bool(receiver.get("refill_is_net_unfilled")),
                "transfer_stacks": 1 if full_stack_possible else 0,
                "required_size_units": transfer_units,
                "additional_size_units": additional_units,
                "additional_sizes_sent": additional_sizes_sent,
                "total_units": transfer_units + additional_units,
                "priority": priority,
                "stack_coverage": round((covered / total_needed_sizes) * 100, 1),
                "status": "PENDING",
                "created_at": now,
            }
        )
        next_id += 1
        if len(recommendations) >= 200:
            break

    return recommendations

@router.get("/debug-cols")
async def debug_lakehouse_cols():
    try:
        rows = _read_transfer_table(TRANSFER_SOURCE_TABLE)
        cols = list(rows[0].keys()) if rows else []
        return {"columns": cols}
    except Exception as e:
        return {"error": str(e)}

@router.post("/sync")
async def sync_transfers_data():
    """
    Simplified sync: Logic moved to Fabric Notebook.
    This endpoint now just acts as a placeholder or can be used to trigger the notebook later.
    """
    try:
        logger.info("Sync triggered. Refreshing data from Lakehouse tables...")
        # Clear local caches to ensure fresh data is fetched from Lakehouse
        _TRANSFER_TABLE_CACHE.clear()
        _TRANSFER_TABLE_CACHE_AT.clear()
        
        return {
            "status": "success",
            "message": "Data refreshed from Lakehouse. (Notebook-driven pipeline)",
            "source_table": TRANSFER_SOURCE_TABLE
        }
    except Exception as e:
        logger.error(f"Sync refresh error: {e}")
        return {"status": "error", "message": f"Sync failed: {str(e)}"}

        return {
            "status": "success",
            "receivers": len(receivers),
            "donors": len(donors),
            "recommendations": len(recommendations),
            "source_table": TRANSFER_SOURCE_TABLE,
            "refill_source_filter": TRANSFER_SYNC_REFILL_SOURCE or None,
        }
    except Exception as e:
        logger.error(f"Sync error: {e}", exc_info=True)
        return {"status": "error", "message": f"Sync failed: {str(e)}"}

@router.get("/recommendations")
async def get_recommended_transfers(
    donor_site: str = None,
    donor_sku: str = None,
    recv_site: str = None,
    recv_sku: str = None,
    recv_site_id: str = None,
    recv_month: str = None,
    recv_week: str = None,
    limit: int | None = Query(
        None,
        ge=1,
        le=20_000,
        description="Page size. Omit to return the full list (default / legacy).",
    ),
    offset: int = Query(0, ge=0, description="Row offset for paginated response."),
    refresh: int = Query(0, description="1 = bypass page_cache"),
):
    page_key = (
        f"recs|v1|{donor_site}|{donor_sku}|{recv_site}|{recv_sku}|{recv_site_id}|"
        f"{recv_month}|{recv_week}|{limit}|{offset}"
    )
    if int(refresh or 0) != 1:
        try:
            from api.page_response_cache import load_page_cache, rebuilding_page_cache

            if not rebuilding_page_cache():
                hit = load_page_cache("transfers-recommendations", page_key)
                if isinstance(hit, dict) and hit.get("status") == "success" and "data" in hit:
                    # Drop internal page_cache marker before returning to UI
                    return {k: v for k, v in hit.items() if k != "ok"}
        except Exception:
            pass
    try:
        recs_all = _read_transfer_table(RECOMMENDED_TRANSFERS_TABLE)
        receiver_rows = _read_transfer_table(POTENTIAL_RECEIVERS_TABLE)
        receiver_refill_idx = _receiver_refill_index(receiver_rows)
        recs_all = [
            r
            for r in recs_all
            if (
                not donor_site
                or donor_site in ("All Sites", "All Stores", "All")
                or str(r.get("donor_store") or r.get("donor_site_id") or "").strip() == str(donor_site or "").strip()
            )
            and (not donor_sku or donor_sku in ("All SKUs", "All") or str(r.get("sku") or "") == donor_sku)
        ]
        recs_all = [r for r in recs_all if str(r.get("status", "PENDING")).upper() == "PENDING"]

        # Project decision: treat priority_score as 0..10 scale only.
        priority_scale_hint = "0_10"
        needs_recv_enrich = bool(
            (recv_month and str(recv_month).strip() not in ("", "All", "All Months"))
            or (recv_week and str(recv_week).strip() not in ("", "All", "All Weeks"))
            or (recv_site and str(recv_site).strip() not in ("", "All Sites", "All Stores", "All"))
            or (recv_site_id and str(recv_site_id).strip() not in ("", "All", "All Sites"))
            or (recv_sku and str(recv_sku).strip() not in ("", "All SKUs", "All"))
        )

        def _priority_rank(r: Dict[str, Any]) -> int:
            p_raw = r.get("priority")
            p = str(p_raw or "").strip().upper()
            if p.startswith("HIGH"):
                return 3
            if p.startswith("MED"):
                return 2
            if p.startswith("LOW"):
                return 1
            ps = r.get("priority_score", r.get("receiver_priority"))
            try:
                if ps is not None and str(ps).strip() != "":
                    v = float(ps)
                    if not math.isfinite(v):
                        return 0
                    if v >= 0.66 or v >= 6.6:
                        return 3
                    if v >= 0.33 or v >= 3.3:
                        return 2
                    if v >= 0.01 or v >= 0.1:
                        return 1
            except Exception:
                pass
            return 0

        def _enrich_page(rows: List[Dict[str, Any]]) -> None:
            try:
                replen_rows = _read_transfer_table(TRANSFER_SOURCE_TABLE)
                loc_idx = _replen_location_index(replen_rows) if replen_rows else {}
                site_name_idx = _site_name_index(replen_rows) if replen_rows else {}
                lake_idx = _replen_receiver_lake_index(replen_rows) if replen_rows else {}
            except Exception:
                loc_idx = {}
                site_name_idx = {}
                lake_idx = {}
            for idx, r in enumerate(rows, start=1):
                if "id" not in r:
                    r["id"] = idx
                _map_rec_row(r, priority_score_scale_hint=priority_scale_hint)
                _enrich_recommendation_locations(r, loc_idx, site_name_idx)
                refill = _safe_int(r.get("receiver_recommended_refill"), default=-1)
                if refill <= 0:
                    r["receiver_recommended_refill"] = _lookup_receiver_refill_from_index(
                        receiver_refill_idx, r
                    )
                _enrich_rec_from_receiver_lake(r, lake_idx)
                r["movement_units"] = _movement_units_for_rec(r)

        if needs_recv_enrich:
            # Filters need lake month/week/site — enrich all, then filter.
            _enrich_page(recs_all)
            recs = [
                r
                for r in recs_all
                if _transfer_row_matches_recv_filters(
                    r,
                    recv_site=recv_site,
                    recv_site_id=recv_site_id,
                    recv_sku=recv_sku,
                    recv_month=recv_month,
                    recv_week=recv_week,
                )
            ]
        else:
            # Demo-fast path: sort + page first, enrich only the returned slice.
            for idx, r in enumerate(recs_all, start=1):
                if "id" not in r:
                    r["id"] = idx
                _map_rec_row(r, priority_score_scale_hint=priority_scale_hint)
            recs = recs_all

        for pos, r in enumerate(recs, start=1):
            rid = r.get("id")
            if rid is None or str(rid).strip() == "":
                r["id"] = pos
            else:
                r["id"] = _safe_int(rid, pos)

        recs.sort(
            key=lambda x: (_priority_rank(x), str(x.get("created_at", ""))),
            reverse=True,
        )
        rec_total = len(recs)
        paged = limit is not None
        if paged:
            page = recs[offset : offset + int(limit or 0)]
            has_more = offset + len(page) < rec_total
        else:
            page = recs
            has_more = False
        if not needs_recv_enrich:
            _enrich_page(page)
        safe_recs = [_json_safe_row(r) for r in page]
        out: Dict[str, Any] = {"status": "success", "data": safe_recs, "total": rec_total}
        if paged:
            out["limit"] = limit
            out["offset"] = offset
            out["has_more"] = has_more
        try:
            from api.page_response_cache import save_page_cache

            save_payload = dict(out)
            save_payload["ok"] = True
            save_page_cache("transfers-recommendations", page_key, save_payload)
        except Exception:
            pass
        return out
    except Exception as e:
        return {"status": "error", "message": str(e), "data": []}

@router.get("/filters/sites")
async def get_transfers_sites():
    try:
        recv = _read_transfer_table(POTENTIAL_RECEIVERS_TABLE)
        donor = _read_transfer_table(POTENTIAL_DONORS_TABLE)
        sites_map: dict[str, str] = {}
        for r in [*recv, *donor]:
            sid = str(r.get("site_id") or r.get("site") or "").strip()
            sname = str(r.get("site_name") or "").strip()
            if not sid and not sname:
                continue
            if sid and sname:
                if f"({sid})" in sname or f"( {sid} )" in sname:
                    display = sname
                else:
                    display = f"{sname} ({sid})"
                sites_map[display] = display
            elif sname:
                sites_map[sname] = sname
            elif sid:
                sites_map[sid] = sid

        sorted_labels = sorted(sites_map.keys())
        return [{"id": "All Sites", "label": "All Sites"}] + [{"id": s, "label": s} for s in sorted_labels]
    except Exception:
        return [{"id": "All Sites", "label": "All Sites"}]

@router.get("/filters/skus")
async def get_transfers_skus(
    donor_site: str = None,
    donor_sites: str = None,
    seasons: str = None,
):
    try:
        donor = _read_transfer_table(POTENTIAL_DONORS_TABLE)
        rows = list(donor)
        sites = _parse_donor_sites_csv(donor_sites)
        if not sites and donor_site and donor_site not in ("All Sites", "All Stores", "All"):
            sites = _parse_donor_sites_csv(donor_site) or [str(donor_site).strip()]
        if sites:
            rows = [r for r in donor if _row_site_matches_any(r, sites)]
        want_seasons = _selected_seasons(seasons)
        if want_seasons:
            season_map = _sku_season_index()
            rows = [r for r in rows if _season_filter_ok(r, want_seasons, season_map)]
        skus = sorted({str(r.get("sku")) for r in rows if r.get("sku") is not None})
        return [{"id": "All SKUs", "label": "All SKUs (entire store)"}] + [
            {"id": s, "label": s} for s in skus
        ]
    except Exception:
        return [{"id": "All SKUs", "label": "All SKUs (entire store)"}]


_SITE_DIM_CACHE: Dict[str, tuple[str, str]] | None = None


def _site_dim_index() -> Dict[str, tuple[str, str]]:
    """site code -> (site name, channel) from Customer Master."""
    global _SITE_DIM_CACHE
    if _SITE_DIM_CACHE is not None:
        return _SITE_DIM_CACHE
    out: Dict[str, tuple[str, str]] = {}
    try:
        try:
            from customer_master import load_customer_master
        except ImportError:
            from pipeline.customer_master import load_customer_master  # type: ignore

        cm = load_customer_master(retail_only=False)
    except Exception:
        cm = None
    if cm is not None and not getattr(cm, "empty", True):
        for _, r in cm.iterrows():
            name = str(r.get("site_name_master") or "").strip()
            if name.lower() in {"nan", "none", "<na>"}:
                name = ""
            channel = _norm_channel(r.get("channel"))
            for col in ("site_short_code", "site_manual_code", "site_code"):
                key = str(r.get(col) or "").strip().upper().replace(".0", "")
                if not key or key in {"NAN", "NONE", "<NA>"}:
                    continue
                prev = out.get(key)
                if prev is None or (name and not prev[0]) or (channel and not prev[1]):
                    out[key] = (name or (prev[0] if prev else ""), channel or (prev[1] if prev else ""))
    _SITE_DIM_CACHE = out
    return out


def _store_option(row: Dict[str, Any]) -> Dict[str, str] | None:
    sid = str(row.get("site_id") or row.get("site") or "").strip()
    if sid.endswith(".0"):
        sid = sid[:-2]
    sname = str(row.get("site_name") or "").strip()
    if sname.lower() in {"nan", "none", "<na>"}:
        sname = ""
    channel = _norm_channel(_row_store_type(row))
    dim = _site_dim_index().get(sid.upper())
    if dim:
        if not sname:
            sname = dim[0]
        if not channel:
            channel = dim[1]
    if not sid and not sname:
        return None
    # Site code, store name, and channel all stay visible in the dropdown.
    label = " · ".join(part for part in (sid, sname, channel) if part)
    return {
        "id": label,
        "label": label,
        "site_code": sid,
        "site_name": sname,
        "channel": channel,
    }


@router.get("/filters/clearance")
async def get_clearance_filters():
    """Store, channel, and season options for the Store Clearance tab."""
    try:
        donor = list(_read_transfer_table(POTENTIAL_DONORS_TABLE) or [])
        recv = list(_read_transfer_table(POTENTIAL_RECEIVERS_TABLE) or [])
        season_map = _sku_season_index()
        
        # One option per store. Scanning every barcode row into the dropdown freezes the page.
        seen_sites: Dict[str, Dict[str, Any]] = {}
        seasons: set[str] = set()
        for row in [*donor, *recv]:
            season = _norm_season(row.get("season"))
            if not season:
                sku_val = str(row.get("sku") or "").strip().upper()
                season = season_map.get(sku_val, "")
            if season:
                seasons.add(season)
            sid = str(row.get("site_id") or row.get("site") or "").strip()
            if sid.endswith(".0"):
                sid = sid[:-2]
            key = sid.upper() or str(row.get("site_name") or "").strip().upper()
            if not key or key in seen_sites:
                continue
            seen_sites[key] = row
            
        if not seasons and season_map:
            seasons = {s for s in season_map.values() if s}
        if not seasons:
            seasons = {"AW-26", "SS-26", "AW-25", "SS-25", "AW-24", "SS-24", "AW-23", "SS-23", "AW-22", "SS-22", "EBO EXCL.", "ONLINE EXCL."}

        stores: Dict[str, Dict[str, str]] = {}
        channels: set[str] = set()
        for row in seen_sites.values():
            opt = _store_option(row)
            if not opt:
                continue
            if opt["channel"]:
                channels.add(opt["channel"])
            prev = stores.get(opt["label"])
            if prev is None or (opt["channel"] and not prev.get("channel")):
                stores[opt["label"]] = opt
        if not channels:
            channels = {"PT", "SSL", "CENTRO", "ICONIC", "EBO"}
        return {
            "stores": [stores[label] for label in sorted(stores)],
            "channels": sorted(channels),
            "seasons": sorted(seasons, reverse=True),
        }
    except Exception as exc:
        logger.warning("clearance filter options failed: %s", exc)
        return {
            "stores": [],
            "channels": ["PT", "SSL", "CENTRO", "ICONIC", "EBO"],
            "seasons": ["SS-26", "AW-26", "SS-25", "AW-25", "SS-24", "AW-24", "SS-23", "AW-23", "EBO EXCL.", "ONLINE EXCL."],
        }

@router.get("/data")
async def get_transfers_data(
    recv_site: str = None, recv_sku: str = None,
    donor_site: str = None, donor_sku: str = None,
    r_off: int = Query(0, ge=0, description="Offset into sorted receivers (paged mode)."),
    d_off: int = Query(0, ge=0, description="Offset into sorted donors (paged mode)."),
    r_limit: int | None = Query(
        None,
        ge=0,
        le=10_000,
        description="Receivers per page. Omit for full list (legacy). 0 = return no receiver rows (client finished this side).",
    ),
    d_limit: int | None = Query(
        None,
        ge=0,
        le=10_000,
        description="Donors per page. Omit for full list (legacy). 0 = return no donor rows (client finished this side).",
    ),
    refresh: int = Query(0, description="1 = bypass page_cache"),
):
    data_key = (
        f"data|v1|{recv_site}|{recv_sku}|{donor_site}|{donor_sku}|"
        f"{r_off}|{d_off}|{r_limit}|{d_limit}"
    )
    if int(refresh or 0) != 1:
        try:
            from api.page_response_cache import load_page_cache, rebuilding_page_cache

            if not rebuilding_page_cache():
                hit = load_page_cache("transfers-data", data_key)
                if isinstance(hit, dict) and hit.get("status") == "success":
                    return {k: v for k, v in hit.items() if k != "ok"}
        except Exception:
            pass
    try:
        receiver_rows = _read_transfer_table(POTENTIAL_RECEIVERS_TABLE)
        donor_rows = _read_transfer_table(POTENTIAL_DONORS_TABLE)

        def row_match(row: Dict[str, Any], site: str = None, sku: str = None) -> bool:
            row_site = str(row.get("site_id") or row.get("site") or "").strip()
            if site and site not in ("All Sites", "All Stores", "All"):
                if row_site != site and str(row.get("site_name") or "").strip() != site:
                    return False
            if sku and sku not in ("All SKUs", "All"):
                if str(row.get("sku") or "").strip() != sku:
                    return False
            return True

        receivers = []
        for r in receiver_rows:
            if not row_match(r, recv_site, recv_sku):
                continue
            if not _row_classification_ok(r, "receiver"):
                continue

            need = _row_refill_for_potential(r)
            if need <= 0:
                continue

            d_sl = _pick_int(
                r,
                "days_since_last_sale",
                "days_from_last_selling_date",
                "days_since_last_sales",
                default=0,
            )
            ts = _pick_int(
                r,
                "sum_of_sales",
                "total_sales",
                "Sum_of_Sales",
                "Total_Sales",
                default=0,
            )
            receivers.append(
                _json_safe_row(
                    {
                        "site_id": str(r.get("site_id") or r.get("site") or "").strip(),
                        "site_name": str(
                            r.get("site_name") or r.get("site_id") or "Unknown"
                        ).strip()
                        or "Unknown",
                        "store_type": _row_store_type(r),
                        "city": _row_city(r),
                        "state": _row_state(r),
                        "sku": str(r.get("sku") or "").strip(),
                        "size": str(r.get("size") or "").strip(),
                        "store_soh": _safe_qty(r.get("store_soh")),
                        "wh_soh": _safe_qty(r.get("wh_soh")),
                        "days_since_last_sale": d_sl,
                        "last_week_sales": _pick_last_week_sold_qty(r),
                        "last_35day_sold_qty": _pick_sold_qty_last_35d(r),
                        "total_sales": ts,
                        "qty_still_needed": need,
                        "priority_score": _row_priority_score_value(r),
                    }
                )
            )

        receivers.sort(key=lambda x: (x.get("store_soh", 0), x.get("wh_soh", 0)))

        donors = []
        for r in donor_rows:
            if not row_match(r, donor_site, donor_sku):
                continue
            if not _row_classification_ok(r, "donor"):
                continue

            ts = _pick_int(
                r,
                "sum_of_sales",
                "total_sales",
                "Sum_of_Sales",
                "Total_Sales",
                default=0,
            )
            donors.append(
                _json_safe_row(
                    {
                        "site_id": str(r.get("site_id") or r.get("site") or "").strip(),
                        "site_name": str(
                            r.get("site_name") or r.get("site_id") or "Unknown"
                        ).strip()
                        or "Unknown",
                        "store_type": _row_store_type(r),
                        "city": _row_city(r),
                        "state": _row_state(r),
                        "sku": str(r.get("sku") or "").strip(),
                        "size": str(r.get("size") or "").strip(),
                        "store_soh": _safe_qty(r.get("store_soh")),
                        "wh_soh": _safe_qty(r.get("wh_soh")),
                        "days_from_last_transfer": _row_days_last_transfer(r),
                        "days_from_last_sales": _row_days_last_selling(r),
                        "last_week_sales": _pick_last_week_sold_qty(r),
                        "last_35day_sold_qty": _pick_sold_qty_last_35d(r),
                        "total_sales": ts,
                        "donor_source": str(r.get("donor_source") or "").strip(),
                    }
                )
            )

        donors.sort(
            key=lambda x: (x.get("days_from_last_sales", 0), x.get("days_from_last_transfer", 0)),
            reverse=True,
        )

        paged = r_limit is not None or d_limit is not None
        if not paged:
            out = {"receivers": receivers, "donors": donors, "status": "success"}
        else:
            recv_total = len(receivers)
            donor_total = len(donors)

            if r_limit is not None:
                if r_limit > 0:
                    out_recv = receivers[r_off : r_off + r_limit]
                    recv_has_more = r_off + r_limit < recv_total
                else:
                    out_recv = []
                    recv_has_more = False
            else:
                out_recv = list(receivers)
                recv_has_more = False

            if d_limit is not None:
                if d_limit > 0:
                    out_donor = donors[d_off : d_off + d_limit]
                    donor_has_more = d_off + d_limit < donor_total
                else:
                    out_donor = []
                    donor_has_more = False
            else:
                out_donor = list(donors)
                donor_has_more = False

            out = {
                "receivers": out_recv,
                "donors": out_donor,
                "status": "success",
                "receivers_total": recv_total,
                "donors_total": donor_total,
                "receivers_has_more": recv_has_more,
                "donors_has_more": donor_has_more,
            }
        try:
            from api.page_response_cache import save_page_cache

            save_payload = dict(out)
            save_payload["ok"] = True
            save_page_cache("transfers-data", data_key, save_payload)
        except Exception:
            pass
        return out
    except Exception as e:
        return {"status": "error", "message": str(e), "receivers": [], "donors": []}

@router.post("/approve/{rec_id}")
async def approve_transfer(rec_id: int):
    try:
        recs = _read_transfer_table(RECOMMENDED_TRANSFERS_TABLE)
        rec_raw = None
        
        # Try finding by literal 'id' or row index as fallback
        for idx, r in enumerate(recs, start=1):
            effective_id = _safe_int(r.get("id"), idx)
            if effective_id == rec_id:
                rec_raw = r
                break
        
        if not rec_raw:
            rec_raw = _CLEARANCE_REC_CACHE.get(rec_id)

        if not rec_raw:
            return {"status": "error", "message": "Recommendation not found."}

        # Use formal mapping + lake enrich so *_city/*_state/site names match dbo.current_transfers columns.
        rec = _map_rec_row(dict(rec_raw))
        recommendation_created_at = rec_raw.get("created_at")
        try:
            replen_rows = _read_transfer_table(TRANSFER_SOURCE_TABLE)
            loc_idx = _replen_location_index(replen_rows) if replen_rows else {}
            site_name_idx = _site_name_index(replen_rows) if replen_rows else {}
        except Exception:
            loc_idx = {}
            site_name_idx = {}
        _enrich_recommendation_locations(rec, loc_idx, site_name_idx)

        # 1. Add to current_transfers (project to Spark dbo.current_transfers schema only)
        current = _read_transfer_table("current_transfers")
        existing_transfer_ids: List[int] = []
        for row in current:
            raw_tid = row.get("Transfer_ID", row.get("transfer_id"))
            tid = _safe_int(raw_tid, 0)
            if tid > 0:
                existing_transfer_ids.append(tid)
        next_transfer_id = (max(existing_transfer_ids) + 1) if existing_transfer_ids else 1
        
        ts = _utc_naive_now()
        new_transfer = _build_spark_current_transfers_row(
            rec,
            transfer_id=next_transfer_id,
            status="IN_TRANSIT",
            created_at=ts,
            transfer_date_value=ts,
            recommendation_created_at_raw=recommendation_created_at,
        )
        current.append(new_transfer)
        store.replace_delta_table("current_transfers", current)
        _TRANSFER_TABLE_CACHE["current_transfers"] = [_normalize_row(r) for r in current]
        _TRANSFER_TABLE_CACHE_AT["current_transfers"] = time.time()

        # 2. DELETE from recommended table, potential_donors, and potential_receivers
        sku = str(rec.get("sku") or "").strip()
        size = str(rec.get("size") or "").strip().upper()
        donor_site = str(rec.get("donor_store") or "").strip()
        receiver_site = str(rec.get("receiver_store") or "").strip()

        # Update Recommended Transfers (literal delete)
        new_recs = [r for r in recs if _safe_int(r.get("id")) != rec_id]
        _write_transfer_table(RECOMMENDED_TRANSFERS_TABLE, new_recs)

        def _row_matches_site_sku(
            row: Dict[str, Any], *, want_sku: str, want_size: str, want_site: str
        ) -> bool:
            d = _normalize_row(row)
            if d.get("sku") != want_sku:
                return False
            site_val = str(d.get("site") or d.get("site_id") or "").strip()
            if site_val != want_site:
                return False
            if not want_size:
                return True
            return str(d.get("size") or "").strip().upper() == want_size

        # Update Potential Donors
        donors = _read_transfer_table(POTENTIAL_DONORS_TABLE)
        new_donors = [
            d
            for d in donors
            if not _row_matches_site_sku(
                d, want_sku=sku, want_size=size, want_site=donor_site
            )
        ]
        _write_transfer_table(POTENTIAL_DONORS_TABLE, new_donors)

        # Update Potential Receivers
        receivers = _read_transfer_table(POTENTIAL_RECEIVERS_TABLE)
        new_receivers = [
            r
            for r in receivers
            if not _row_matches_site_sku(
                r, want_sku=sku, want_size=size, want_site=receiver_site
            )
        ]
        _write_transfer_table(POTENTIAL_RECEIVERS_TABLE, new_receivers)

        return {"status": "success", "message": "Transfer approved and inventory lists updated."}
    except Exception as e:
        logger.error(f"Approval error: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}


@router.post("/approve-all")
async def approve_all_transfers():
    try:
        recs = _read_transfer_table(RECOMMENDED_TRANSFERS_TABLE)
        pending_recs = [r for r in recs if str(r.get("status", "PENDING")).upper() == "PENDING"]
        if not pending_recs:
            return {"status": "success", "message": "No pending recommendations found.", "approved_count": 0}

        current = _read_transfer_table("current_transfers")
        existing_transfer_ids: List[int] = []
        for row in current:
            raw_tid = row.get("Transfer_ID", row.get("transfer_id"))
            tid = _safe_int(raw_tid, 0)
            if tid > 0:
                existing_transfer_ids.append(tid)
        next_transfer_id = (max(existing_transfer_ids) + 1) if existing_transfer_ids else 1

        try:
            replen_rows = _read_transfer_table(TRANSFER_SOURCE_TABLE)
            loc_idx = _replen_location_index(replen_rows) if replen_rows else {}
            site_name_idx = _site_name_index(replen_rows) if replen_rows else {}
        except Exception:
            loc_idx = {}
            site_name_idx = {}

        approved_count = 0
        for idx, rec in enumerate(recs, start=1):
            if str(rec.get("status", "PENDING")).upper() != "PENDING":
                continue

            matched_effective_id = _safe_int(rec.get("id"), idx)
            if any(_safe_int(r.get("source_rec_id")) == matched_effective_id for r in current):
                rec["id"] = matched_effective_id
                rec["status"] = "APPROVED"
                rec["approved_at"] = OneLakeJsonStore.now_iso()
                continue

            mapped = _map_rec_row(dict(rec))
            enriched = dict(mapped)
            _enrich_recommendation_locations(enriched, loc_idx, site_name_idx)
            ts = _utc_naive_now()
            spark_row = _build_spark_current_transfers_row(
                enriched,
                transfer_id=next_transfer_id,
                status="IN_TRANSIT",
                created_at=ts,
                transfer_date_value=ts,
                recommendation_created_at_raw=mapped.get("created_at"),
            )
            next_transfer_id += 1
            current.append(spark_row)
            rec["id"] = matched_effective_id
            rec["status"] = "APPROVED"
            rec["approved_at"] = OneLakeJsonStore.now_iso()
            approved_count += 1

        try:
            store.replace_delta_table("current_transfers", current)
            _TRANSFER_TABLE_CACHE["current_transfers"] = [_normalize_row(r) for r in current]
            _TRANSFER_TABLE_CACHE_AT["current_transfers"] = time.time()
        except Exception as e:
            logger.error(f"Failed writing dbo.current_transfers on approve-all: {e}", exc_info=True)
            return {"status": "error", "message": f"Failed to save approved transfers in dbo.current_transfers: {str(e)}"}

        _write_transfer_table(RECOMMENDED_TRANSFERS_TABLE, recs)
        return {"status": "success", "message": "All pending transfers approved.", "approved_count": approved_count}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def _potential_donor_export_row(r: Dict[str, Any]) -> Dict[str, Any]:
    nr = _normalize_row(_dedupe_keys_case_insensitive_prefer_lower(dict(r)))
    return {
        "site_id": str(nr.get("site_id") or nr.get("site") or "").strip(),
        "site_name": str(nr.get("site_name") or nr.get("site") or "").strip(),
        "barcode": str(nr.get("barcode") or ""),
        "sku": str(nr.get("sku") or ""),
        "size": str(nr.get("size") or ""),
        "store_soh": _safe_qty(nr.get("store_soh")),
        "wh_soh": _safe_qty(nr.get("wh_soh")),
        "city": _row_city(nr),
        "state": _row_state(nr),
        "store_type": _row_store_type(nr),
        "store_grade": str(nr.get("store_grade") or "").strip(),
        "days_from_last_sales": _row_days_last_selling(nr),
        "days_from_last_transfer": _row_days_last_transfer(nr),
        "last_week_sales": _pick_last_week_sold_qty(nr),
        "last_35day_sold_qty": _pick_sold_qty_last_35d(nr),
        "total_sales": _pick_int(nr, "sum_of_sales", "total_sales", "total_net_sales", default=0),
        "donor_source": str(nr.get("donor_source") or "").strip(),
    }


def _potential_receiver_export_row(r: Dict[str, Any]) -> Dict[str, Any]:
    nr = _normalize_row(_dedupe_keys_case_insensitive_prefer_lower(dict(r)))
    need = _row_refill_for_potential(nr)
    rec_send = _pick_int(
        nr,
        "recommended_qty_to_send",
        "receiver_recommended_refill",
        "recommended_refill",
        "recommendation_refill",
        default=0,
    )
    if rec_send <= 0 and need > 0:
        rec_send = need
    return {
        "site_id": str(nr.get("site_id") or nr.get("site") or "").strip(),
        "site_name": str(nr.get("site_name") or nr.get("site") or "").strip(),
        "barcode": str(nr.get("barcode") or ""),
        "sku": str(nr.get("sku") or ""),
        "size": str(nr.get("size") or ""),
        "store_soh": _safe_qty(nr.get("store_soh")),
        "wh_soh": _safe_qty(nr.get("wh_soh")),
        "city": _row_city(nr),
        "state": _row_state(nr),
        "store_type": _row_store_type(nr),
        "store_grade": str(nr.get("store_grade") or "").strip(),
        "days_since_last_sale": _row_days_last_selling(nr),
        "days_since_last_transfer": _days_since_transfer(nr),
        "last_week_sales": _pick_last_week_sold_qty(nr),
        "last_35day_sold_qty": _pick_sold_qty_last_35d(nr),
        "total_sales": _pick_int(nr, "sum_of_sales", "total_sales", "total_net_sales", default=0),
        "qty_still_needed": need,
        "recommended_qty_to_send": rec_send,
        "priority_score": _row_priority_score_value(nr) if _row_priority_score_value(nr) is not None else "",
    }


# Recommended Excel sheet: fixed column order only — values come straight from the lake row (no enrich / no joins).
RECOMMENDED_DOWNLOAD_COLUMNS: Tuple[str, ...] = (
    "store_type",
    "receiver_store_type",
    "donor_store_type",
    "receiver_site_name",
    "receiver_city",
    "receiver_state",
    "receiver_store_grade",
    "donor_site_name",
    "donor_city",
    "donor_state",
    "donor_store_grade",
    "sku",
    "size",
    "barcode",
    "required_qty",
    "transfer_qty",
    "receiver_soh",
    "donor_soh",
    "priority_score",
    "status",
    "created_at",
)

# When the lake row has no line-level qty fields, sum these stack columns (same names OneLake uses).
_REQ_QTY_STACK_KEYS: Tuple[str, ...] = (
    "req_1xs_qty",
    "req_2s_qty",
    "req_3m_qty",
    "req_4l_qty",
    "req_5xl_qty",
    "req_6xxl_qty",
)
_TRANSFER_QTY_STACK_KEYS: Tuple[str, ...] = (
    "transfer_1xs_qty",
    "transfer_1_xs_qty",
    "transfer_2s_qty",
    "transfer_3m_qty",
    "transfer_4l_qty",
    "transfer_5xl_qty",
    "transfer_6xxl_qty",
)
_REC_SOH_STACK_KEYS: Tuple[str, ...] = (
    "rec_soh_1xs",
    "rec_soh_1_xs",
    "rec_storesoh_1xs",
    "rec_storesoh_1_xs",
    "rec_soh_2s",
    "rec_storesoh_2s",
    "rec_soh_3m",
    "rec_storesoh_3m",
    "rec_soh_4l",
    "rec_storesoh_4l",
    "rec_soh_5xl",
    "rec_storesoh_5xl",
    "rec_soh_6xxl",
    "rec_soh_xxl",
    "rec_storesoh_6xxl",
    "rec_storesoh_xxl",
)
_DON_SOH_STACK_KEYS: Tuple[str, ...] = (
    "don_soh_1xs",
    "don_soh_1_xs",
    "don_storesoh_1xs",
    "don_storesoh_1_xs",
    "don_soh_2s",
    "don_storesoh_2s",
    "don_soh_3m",
    "don_storesoh_3m",
    "don_soh_4l",
    "don_storesoh_4l",
    "don_soh_5xl",
    "don_storesoh_5xl",
    "don_soh_6xxl",
    "don_soh_xxl",
    "don_storesoh_6xxl",
    "don_storesoh_xxl",
)


def _sum_int_keys(lc: Dict[str, Any], keys: Tuple[str, ...]) -> int:
    return sum(_safe_int(lc.get(k)) for k in keys)


def _recommended_sheet_row_from_lake(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    One Excel row per Delta row: same values as reading `RECOMMENDED_TRANSFERS_TABLE`, only reordered/aliased.
    Does not call _map_rec_row, enrich, or potential_receivers lookup (those changed numbers vs raw export).
    """
    safe = _json_safe_row(_dedupe_keys_case_insensitive_prefer_lower(dict(raw)))
    lc = {str(k).lower(): v for k, v in safe.items()}

    def gs(*keys: str) -> str:
        return _first_str_from_row(lc, *keys)

    req_line = _pick_int(
        lc,
        "required_qty",
        "receiver_recommended_refill",
        "recommended_refill",
        "receiver_required_refill",
        "recommendation_refill",
        "forecasted_refill",
        "refill",
    )
    req_stack = _sum_int_keys(lc, _REQ_QTY_STACK_KEYS)
    required_qty = req_line if req_line != 0 else req_stack

    tr_line = _pick_int(lc, "transfer_qty", "transfer_qty_total", "total_units")
    tr_stack = _sum_int_keys(lc, _TRANSFER_QTY_STACK_KEYS)
    transfer_qty = tr_line if tr_line != 0 else tr_stack

    wh_after_allocation: Any = ""
    for k in ("wh_soh_end_barcode", "warehouse_soh_after_allocation", "wh_soh_after_allocation"):
        if lc.get(k) not in (None, ""):
            wh_after_allocation = _safe_int(lc.get(k))
            break

    rs_line = _pick_int(lc, "receiver_soh", "receiver_store_soh", "receiver_lake_soh")
    rs_stack = _sum_int_keys(lc, _REC_SOH_STACK_KEYS)
    receiver_soh = rs_line if rs_line != 0 else rs_stack

    ds_line = _pick_int(lc, "donor_soh", "donor_store_soh", "don_soh", "donor_lake_soh")
    ds_stack = _sum_int_keys(lc, _DON_SOH_STACK_KEYS)
    donor_soh = ds_line if ds_line != 0 else ds_stack

    ps = lc.get("priority_score")
    ps_out: Any = ""
    if ps not in (None, ""):
        try:
            pf = float(ps)
            ps_out = round(pf, 4) if math.isfinite(pf) else str(ps)
        except (TypeError, ValueError):
            ps_out = str(ps)

    barcode = gs(
        "barcode",
        "rec_barcode",
        "receiver_barcode",
        "don_barcode",
        "donor_barcode",
        "receiver_lake_barcode",
    )

    receiver_site_name = gs("receiver_site_name", "receiver_store_name")
    donor_site_name = gs("donor_site_name", "donor_store_name")
    receiver_site_id = gs("receiver_site_id", "receiver_store")
    donor_site_id = gs("donor_site_id", "donor_store")

    receiver_store_type = gs("receiver_store_type") or _derive_store_type_from_site(
        receiver_site_name, receiver_site_id
    )
    donor_store_type = gs("donor_store_type") or _derive_store_type_from_site(
        donor_site_name, donor_site_id
    )
    store_type = (
        gs("store_type")
        or receiver_store_type
        or donor_store_type
        or _row_store_type(lc)
    )

    return {
        "store_type": store_type,
        "receiver_store_type": receiver_store_type,
        "donor_store_type": donor_store_type,
        "receiver_site_name": receiver_site_name,
        "receiver_city": gs("receiver_city"),
        "receiver_state": gs("receiver_state"),
        "receiver_store_grade": gs("receiver_store_grade"),
        "donor_site_name": donor_site_name,
        "donor_city": gs("donor_city"),
        "donor_state": gs("donor_state"),
        "donor_store_grade": gs("donor_store_grade"),
        "sku": gs("sku"),
        "size": str(lc.get("size") or "").strip(),
        "barcode": barcode,
        "required_qty": required_qty,
        "transfer_qty": transfer_qty,
        "receiver_soh": receiver_soh,
        "donor_soh": donor_soh,
        "priority_score": ps_out,
        "status": gs("status"),
        "created_at": _excel_ts_display(lc.get("created_at")) or str(lc.get("created_at") or ""),
    }


@router.get("/clearance/search")
async def search_store_clearance(
    donor_sites: str = Query(..., description="Comma-separated store ids/names to clear"),
    donor_sku: str = Query("All SKUs", description="SKU(s) to empty, or All SKUs for full store clearance"),
    receiver_sites: str | None = Query(None, description="Comma-separated target receiver store ids/names (optional)"),
    channels: str | None = Query(None, description="Comma-separated channels, e.g. PT,SSL"),
    seasons: str | None = Query(None, description="Comma-separated seasons"),
    geo_mode: str = Query("default", description="default | in_state | other_state"),
):
    """Multi-store + target receiver stores + optional multi/all-SKU clearance preview."""
    try:
        sites = _parse_donor_sites_csv(donor_sites)
        if not sites:
            raise HTTPException(status_code=400, detail="Select at least one clearing store")
        rec_sites = _parse_donor_sites_csv(receiver_sites) if receiver_sites else None
        payload = _build_clearance_views(
            sites,
            donor_sku,
            receiver_sites=rec_sites,
            channels=sorted(_selected_channels(channels)),
            seasons=sorted(_selected_seasons(seasons)),
            geo_mode=geo_mode,
        )
        return {"status": "success", **payload}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Clearance search error: {e}", exc_info=True)
        return {"status": "error", "message": str(e), "donors": [], "receivers": [], "recommendations": []}


class ClearanceApprovePayload(BaseModel):
    recommendation_ids: Optional[List[int]] = None
    recommendations: Optional[List[Dict[str, Any]]] = None


@router.post("/clearance/approve")
async def approve_clearance(payload: ClearanceApprovePayload):
    try:
        items_to_approve: List[Dict[str, Any]] = []
        if payload.recommendations:
            items_to_approve.extend(payload.recommendations)
        elif payload.recommendation_ids:
            lake_recs = _read_transfer_table(RECOMMENDED_TRANSFERS_TABLE)
            for rid in payload.recommendation_ids:
                cached = _CLEARANCE_REC_CACHE.get(rid)
                if cached:
                    items_to_approve.append(cached)
                else:
                    for idx, r in enumerate(lake_recs, start=1):
                        if _safe_int(r.get("id"), idx) == rid:
                            items_to_approve.append(dict(r))
                            break

        if not items_to_approve:
            return {"status": "error", "message": "No valid clearance recommendations provided to approve."}

        try:
            replen_rows = _read_transfer_table(TRANSFER_SOURCE_TABLE)
            loc_idx = _replen_location_index(replen_rows) if replen_rows else {}
            site_name_idx = _site_name_index(replen_rows) if replen_rows else {}
        except Exception:
            loc_idx = {}
            site_name_idx = {}

        current = _read_transfer_table("current_transfers")
        existing_transfer_ids = [
            _safe_int(r.get("Transfer_ID", r.get("transfer_id")), 0)
            for r in current
            if _safe_int(r.get("Transfer_ID", r.get("transfer_id")), 0) > 0
        ]
        next_transfer_id = (max(existing_transfer_ids) + 1) if existing_transfer_ids else 1
        ts = _utc_naive_now()

        donors = _read_transfer_table(POTENTIAL_DONORS_TABLE)
        receivers = _read_transfer_table(POTENTIAL_RECEIVERS_TABLE)
        lake_recs = _read_transfer_table(RECOMMENDED_TRANSFERS_TABLE)

        approved_count = 0
        for item in items_to_approve:
            rec = _map_rec_row(dict(item))
            _enrich_recommendation_locations(rec, loc_idx, site_name_idx)
            rec_id = _safe_int(item.get("id"))

            new_tx = _build_spark_current_transfers_row(
                rec,
                transfer_id=next_transfer_id,
                status="IN_TRANSIT",
                created_at=ts,
                transfer_date_value=ts,
                recommendation_created_at_raw=item.get("created_at"),
            )
            current.append(new_tx)
            next_transfer_id += 1
            approved_count += 1

            sku = str(rec.get("sku") or "").strip()
            size = str(rec.get("size") or "").strip().upper()
            donor_site = str(rec.get("donor_store") or "").strip()
            receiver_site = str(rec.get("receiver_store") or "").strip()

            if rec_id in _CLEARANCE_REC_CACHE:
                _CLEARANCE_REC_CACHE.pop(rec_id, None)

            def _matches_donor(d_row: Dict[str, Any]) -> bool:
                nr = _normalize_row(d_row)
                if nr.get("sku") != sku:
                    return False
                s_val = str(nr.get("site") or nr.get("site_id") or "").strip()
                if s_val != donor_site:
                    return False
                if not size:
                    return True
                return str(nr.get("size") or "").strip().upper() == size

            def _matches_receiver(r_row: Dict[str, Any]) -> bool:
                nr = _normalize_row(r_row)
                if nr.get("sku") != sku:
                    return False
                s_val = str(nr.get("site") or nr.get("site_id") or "").strip()
                if s_val != receiver_site:
                    return False
                if not size:
                    return True
                return str(nr.get("size") or "").strip().upper() == size

            donors = [d for d in donors if not _matches_donor(d)]
            receivers = [r for r in receivers if not _matches_receiver(r)]
            lake_recs = [r for r in lake_recs if _safe_int(r.get("id")) != rec_id]

        store.replace_delta_table("current_transfers", current)
        _TRANSFER_TABLE_CACHE["current_transfers"] = [_normalize_row(r) for r in current]
        _TRANSFER_TABLE_CACHE_AT["current_transfers"] = time.time()

        _write_transfer_table(POTENTIAL_DONORS_TABLE, donors)
        _write_transfer_table(POTENTIAL_RECEIVERS_TABLE, receivers)
        _write_transfer_table(RECOMMENDED_TRANSFERS_TABLE, lake_recs)

        return {
            "status": "success",
            "message": f"Successfully approved {approved_count} clearance transfer(s).",
            "approved_count": approved_count,
        }
    except Exception as e:
        logger.error(f"Clearance approval batch error: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}


@router.get("/clearance/export")
async def export_store_clearance(
    request: Request,
    donor_sites: str = Query(..., description="Comma-separated stores being cleared"),
    donor_sku: str = Query("All SKUs", description="SKU(s) to empty, or All SKUs for entire store(s)"),
    receiver_sites: str | None = Query(None, description="Comma-separated target receiver store ids/names (optional)"),
    channels: str | None = Query(None, description="Comma-separated channels, e.g. PT,SSL"),
    seasons: str | None = Query(None, description="Comma-separated seasons"),
    geo_mode: str = Query("default", description="default | in_state | other_state"),
    donor_site: str | None = Query(None, description="Legacy single-site param"),
):
    """
    Excel for store/SKU clearance: donor inventory, potential receivers, recommended transfers.
    Supports multiple clearing stores, target receiver stores, and multi/all SKUs.
    """
    try:
        sites = _parse_donor_sites_csv(donor_sites)
        if not sites and donor_site:
            sites = _parse_donor_sites_csv(donor_site) or [str(donor_site).strip()]
        if not sites:
            raise HTTPException(status_code=400, detail="Select at least one clearing store")

        rec_sites = _parse_donor_sites_csv(receiver_sites) if receiver_sites else None
        want_sku = str(donor_sku or "All SKUs").strip()
        views = _build_clearance_views(
            sites,
            want_sku,
            receiver_sites=rec_sites,
            channels=sorted(_selected_channels(channels)),
            seasons=sorted(_selected_seasons(seasons)),
            geo_mode=geo_mode,
            max_rows=20_000,
        )

        donors_data = []
        for d in views["donors"]:
            donors_data.append(
                {
                    "site_id": d.get("site_id"),
                    "site_name": d.get("site_name"),
                    "sku": d.get("sku"),
                    "barcode": d.get("barcode"),
                    "size": d.get("size"),
                    "store_soh": d.get("store_soh"),
                    "city": d.get("city"),
                    "state": d.get("state"),
                    "days_from_last_sales": d.get("days_from_last_sales"),
                }
            )

        receivers_data = []
        for r in views["receivers"]:
            receivers_data.append(
                {
                    "site_id": r.get("site_id"),
                    "site_name": r.get("site_name"),
                    "sku": r.get("sku"),
                    "barcode": r.get("barcode"),
                    "size": r.get("size"),
                    "store_soh": r.get("store_soh"),
                    "qty_still_needed": r.get("qty_still_needed"),
                    "priority_score": r.get("priority_score"),
                    "city": r.get("city"),
                    "state": r.get("state"),
                }
            )

        rec_filtered = views["recommendations"]
        recommended_fixed = [_recommended_sheet_row_from_lake(r) for r in rec_filtered]
        df_donors = pd.DataFrame(donors_data)
        df_receivers = pd.DataFrame(receivers_data)
        df_recommended = pd.DataFrame(recommended_fixed, columns=list(RECOMMENDED_DOWNLOAD_COLUMNS))

        output = BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df_donors.to_excel(writer, index=False, sheet_name="Clearing Store SOH")
            df_receivers.to_excel(writer, index=False, sheet_name="Potential Receivers")
            df_recommended.to_excel(writer, index=False, sheet_name="Recommended Transfers")

        safe_sku = "ALL_SKUS" if want_sku in ("All SKUs", "All") else re.sub(r"[^\w\-]+", "_", want_sku)[:30]
        filename = f"store_clearance_{len(sites)}sites_{safe_sku}_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        content = output.getvalue()
        log_download("Store Clearance Export", filename, request)
        return Response(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Store clearance export error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to export clearance Excel: {str(e)}")


@router.get("/download-transfer-details")
async def download_transfer_details(request: Request, max_rows: int = Query(50000, ge=200, le=50000)):
    """Download Excel: curated donor/receiver/in-transit; Recommended = lake rows as fixed-column projection (no transforms)."""
    try:
        # Export should be responsive for UI users; avoid forcing fresh OneLake reads for every sheet.
        # We already refresh table caches via sync/approve flows, so cached reads are usually current enough.
        bp = False
        # Potential lists: same tables + shapes as GET /data (Transfers UI grids).
        donor_rows = list(_read_transfer_table(POTENTIAL_DONORS_TABLE, bypass_cache=bp) or [])
        receiver_rows_all = list(_read_transfer_table(POTENTIAL_RECEIVERS_TABLE, bypass_cache=bp) or [])
        # Cap before heavy loops (slicing after building was a no-op and could time out / OOM).
        donor_rows = donor_rows[:max_rows]
        receiver_rows_all = receiver_rows_all[:max_rows]

        donors_data = []
        for r in donor_rows:
            nr = _normalize_row(_dedupe_keys_case_insensitive_prefer_lower(dict(r)))
            if not _row_classification_ok(nr, "donor"):
                continue
            donors_data.append(_potential_donor_export_row(r))

        receivers_data = []
        for r in receiver_rows_all:
            nr = _normalize_row(_dedupe_keys_case_insensitive_prefer_lower(dict(r)))
            if not _row_classification_ok(nr, "receiver"):
                continue
            if _row_refill_for_potential(nr) <= 0:
                continue
            receivers_data.append(_potential_receiver_export_row(r))

        # Recommended: one row per lake row; same underlying values as raw table read, fixed column order.
        rec_rows_raw = list(_read_transfer_table(RECOMMENDED_TRANSFERS_TABLE, bypass_cache=bp) or [])
        rec_rows_raw = rec_rows_raw[:max_rows]
        recommended_fixed = [_recommended_sheet_row_from_lake(r) for r in rec_rows_raw]
        df_recommended = pd.DataFrame(recommended_fixed, columns=list(RECOMMENDED_DOWNLOAD_COLUMNS))

        current_rows = list(_read_transfer_table("current_transfers", bypass_cache=bp) or [])
        current_rows = current_rows[:max_rows]

        df_donors = pd.DataFrame(donors_data)
        df_receivers = pd.DataFrame(receivers_data)
        try:
            for col in df_recommended.select_dtypes(include=["datetimetz"]).columns:
                df_recommended[col] = df_recommended[col].dt.tz_localize(None)
        except Exception:
            pass

        # --- Prepare Current Transfers DataFrame (same columns as Transfers UI > In transit tab only) ---
        df_current = _dataframe_current_transfers_in_transit_tab(current_rows or [])
        
        # --- Create Multi-Sheet Excel ---
        output = BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df_donors.to_excel(writer, index=False, sheet_name="Potential Donors")
            df_receivers.to_excel(writer, index=False, sheet_name="Potential Receivers")
            df_recommended.to_excel(writer, index=False, sheet_name="Recommended")
            df_current.to_excel(writer, index=False, sheet_name="In transit")

        filename = f"transfers_export_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        content = output.getvalue()
        log_download("Transfer Details", filename, request)
        return Response(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as e:
        logger.error(f"Generate consolidated Excel error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to generate consolidated Excel: {str(e)}")

@router.get("/sku-soh")
async def get_sku_soh(site: str, sku: str):
    """Fetch SOH for all sizes from OneLake snapshot (no SQL)."""
    try:
        source = _read_transfer_table(TRANSFER_SOURCE_TABLE)
        rows = []
        req_site = str(site or "").strip()
        req_sku = str(sku or "").strip().upper()
        for row in source:
            normalized = _normalize_row(row)
            row_site = str(normalized.get("site") or "").strip()
            row_site_id = str(normalized.get("site_id") or "").strip()
            row_site_name = str(normalized.get("site_name") or "").strip()
            row_sku = str(normalized.get("sku") or "").strip().upper()
            if row_sku != req_sku:
                continue
            # Site can arrive as code/id/name depending on recommendation row source.
            if req_site not in {row_site, row_site_id, row_site_name}:
                continue
            if row_sku == req_sku:
                rows.append(
                    {
                        "size": str(normalized.get("size") or ""),
                        "store_soh": _safe_qty(normalized.get("store_soh")),
                        "wh_soh": _safe_qty(normalized.get("wh_soh")),
                        "barcode": str(normalized.get("barcode") or ""),
                        "total_sales": _safe_int(normalized.get("sum_of_sales") or normalized.get("total_sales")),
                        "sold_qty_5w": _safe_int(normalized.get("sold_qty_prev_5w")),
                        "last_week_sales": _safe_int(normalized.get("lag1_sold_qty")),
                    }
                )
        data = rows
        return {"status": "success", "data": data}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.get("/current")
async def get_current_transfers(
    donor_site: str = None,
    donor_sku: str = None,
    recv_site: str = None,
    recv_sku: str = None,
    refresh: int = Query(0, description="1 = skip in-memory cache, re-read OneLake (use after you cleared the table)."),
):
    try:
        rows_raw = _read_transfer_table("current_transfers", bypass_cache=(refresh == 1))
        
        # Apply mapping to all rows
        rows = [_map_rec_row(dict(r)) for r in rows_raw]
        
        # Apply filters
        filtered_rows = [
            r for r in rows
            if (not donor_site or donor_site in ("All Sites", "All Stores", "All") or str(r.get("donor_store") or "") == donor_site)
            and (not donor_sku or donor_sku in ("All SKUs", "All") or str(r.get("sku") or "") == donor_sku)
            and (not recv_site or recv_site in ("All Sites", "All Stores", "All") or str(r.get("receiver_store") or "") == recv_site)
            and (not recv_sku or recv_sku in ("All SKUs", "All") or str(r.get("sku") or "") == recv_sku)
        ]

        filtered_rows.sort(key=lambda x: str(x.get("created_at", "")), reverse=True)
        
        # Limit to 50 for UI performance
        display_rows = filtered_rows[:50]
        
        # Enrich locations
        try:
            replen_rows = _read_transfer_table(TRANSFER_SOURCE_TABLE)
            loc_idx = _replen_location_index(replen_rows) if replen_rows else {}
            site_name_idx = _site_name_index(replen_rows) if replen_rows else {}
        except Exception:
            loc_idx = {}
            site_name_idx = {}

        for r in display_rows:
            _enrich_recommendation_locations(r, loc_idx, site_name_idx)

        # Fix: ensure NaN/Inf values are JSON-compliant
        safe_rows = [_json_safe_row(r) for r in display_rows]
        
        return {"status": "success", "data": safe_rows}
    except Exception as e:
        logger.error(f"Error fetching current transfers: {e}")
        return {"status": "error", "message": str(e), "data": []}


def _prepare_zero_soh_full(
    site: str | None,
    max_days: int | None,
) -> Dict[str, Any]:
    """
    Load no-sales SOH dataset (default `ZERO_SOH_TABLE`), apply site + N-days filter, return JSON-safe rows and summary.
    Shared by /zero-soh and /zero-soh/export.
    """
    global _ZERO_SOH_CACHE, _ZERO_SOH_CACHE_AT, _ZERO_SOH_CACHE_SOURCE_TABLE
    now = time.time()
    site_key = str(site or "").strip()
    days_key = "" if max_days is None else str(int(max_days))
    prepared_key = f"{ZERO_SOH_TABLE}|{site_key}|{days_key}"
    prep_at = _ZERO_SOH_PREPARED_CACHE_AT.get(prepared_key, 0.0)
    if (
        prepared_key in _ZERO_SOH_PREPARED_CACHE
        and (now - prep_at) < _ZERO_SOH_PREPARED_TTL_SECONDS
    ):
        return _ZERO_SOH_PREPARED_CACHE[prepared_key]

    cache_ok = (
        _ZERO_SOH_CACHE
        and _ZERO_SOH_CACHE_SOURCE_TABLE == ZERO_SOH_TABLE
        and (now - _ZERO_SOH_CACHE_AT) < _ZERO_SOH_CACHE_TTL_SECONDS
    )
    if cache_ok:
        rows = list(_ZERO_SOH_CACHE)
    else:
        rows = _read_transfer_table(ZERO_SOH_TABLE)
        _ZERO_SOH_CACHE = list(rows)
        _ZERO_SOH_CACHE_AT = now
        _ZERO_SOH_CACHE_SOURCE_TABLE = ZERO_SOH_TABLE
        _ZERO_SOH_PREPARED_CACHE.clear()
        _ZERO_SOH_PREPARED_CACHE_AT.clear()
    if site and site not in ("All Sites", "All Stores", "All"):
        rows = [
            r for r in rows
            if str(r.get("Site") or r.get("Site_ID") or r.get("site") or r.get("site_id") or "") == site
        ]

    max_days_available = 0
    for r in rows:
        d = _to_int(r.get("days_since_last_transfer") or r.get("Days_Since_Last_Transfer"))
        if d > max_days_available:
            max_days_available = d

    no_transfer_rows = [_json_safe_row(r) for r in rows if _is_missing_transfer_date(r)]
    no_transfer_columns = list(no_transfer_rows[0].keys()) if no_transfer_rows else []

    if max_days is not None:
        filtered: List[Dict[str, Any]] = []
        for r in rows:
            if _is_missing_transfer_date(r):
                # Keep blank transfer-date records visible in filtered view as well.
                filtered.append(r)
                continue
            days = _to_float(r.get("days_since_last_transfer") or r.get("Days_Since_Last_Transfer"))
            if days > 0 and days <= max_days:
                filtered.append(r)
        rows = filtered

    rows = [_json_safe_row(r) for r in rows]
    summary = _build_zero_soh_summary(rows)
    prepared = {
        "rows": rows,
        "summary": summary,
        "max_days_available": max_days_available,
        "no_transfer_rows": no_transfer_rows,
        "no_transfer_columns": no_transfer_columns,
    }
    _ZERO_SOH_PREPARED_CACHE[prepared_key] = prepared
    _ZERO_SOH_PREPARED_CACHE_AT[prepared_key] = now
    return prepared


@router.get("/zero-soh")
async def get_zero_soh_data(
    site: str = None,
    limit: int = Query(300, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    max_days: int | None = Query(None, ge=0),
):
    """Fetch zero SOH dataset from OneLake Files snapshot."""
    try:
        prepared = _prepare_zero_soh_full(site, max_days)
        rows = prepared["rows"]
        total = len(rows)
        page_rows = rows[offset: offset + limit]
        columns = list(page_rows[0].keys()) if page_rows else (list(rows[0].keys()) if rows else [])
        return {
            "status": "success",
            "data": page_rows,
            "columns": columns,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": (offset + limit) < total,
            "summary": prepared["summary"],
            "max_days_available": prepared["max_days_available"],
            "no_transfer_record_count": len(prepared["no_transfer_rows"]),
            "no_transfer_records": prepared["no_transfer_rows"][:100],
            "no_transfer_columns": prepared["no_transfer_columns"],
        }
    except Exception as e:
        logger.error(f"Error fetching zero_sales_soh snapshot: {e}")
        return {
            "status": "error",
            "message": f"Failed to read OneLake zero_sales_soh snapshot. Error: {str(e)}",
            "version": "2.0.0-onelake",
            "data": [],
            "columns": []
        }


@router.get("/zero-soh/export")
async def export_zero_soh_excel(
    request: Request,
    site: str = None,
    max_days: int | None = Query(None, ge=0),
):
    """Download full No Sales Donors table as Excel; same filters as the UI (N days)."""
    try:
        prepared = _prepare_zero_soh_full(site, max_days)
        rows = prepared["rows"]
        if not rows:
            raise HTTPException(status_code=404, detail="No rows to export for the current filters.")
        df = pd.DataFrame(rows)
        output = BytesIO()
        sheet = "No_sales_donors"[:31]
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name=sheet)
        output.seek(0)
        filename = f"no_sales_donors_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        log_download("No Sales Donors (Zero SOH)", filename, request)
        return StreamingResponse(
            output,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error exporting zero_sales_soh Excel: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

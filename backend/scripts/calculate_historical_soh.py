"""
Historical SOH + Sell-Through from Raw_Files (permanent pipeline utility).

SOH ledger (barcode × site):
  receipt  = Dispatch (WH→store), date = invoice_date + transit_days
  sale     = 3rd Party Sales (stock out)
  rtv      = Sales return (stock out) — used ONLY when BARCODE column exists
  closing  = cumulative (receipts − sales − rtv)
  opening  = prior week/month closing

Outputs (pipeline/output_files/historical_soh/):
  weekly_soh_sellthrough_<start>_<end>.csv
  monthly_soh_sellthrough_<start>_<end>.csv
  *.summary.json
  txns_ledger.parquet   (cached ledger for fast re-runs)

Usage:
  python scripts/calculate_historical_soh.py
  python scripts/calculate_historical_soh.py --start 2025-04-01 --end 2026-08-20
  python scripts/calculate_historical_soh.py --local-cache-only
  python scripts/calculate_historical_soh.py --reuse-txns   # skip Excel, use parquet
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

import numpy as np
import pandas as pd

BACKEND_DIR = Path(__file__).resolve().parents[1]
PIPELINE_DIR = BACKEND_DIR / "pipeline"
OUT_DIR = PIPELINE_DIR / "output_files" / "historical_soh"
CACHE_DIR = BACKEND_DIR / "data" / "raw_files_cache"
TXNS_PARQUET = OUT_DIR / "txns_ledger.parquet"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

log = logging.getLogger("calculate_historical_soh")

DISPATCH_BLOBS = [
    "Raw_Files/Dispatch data april'25 to 20'aug_2026-08-21.xlsx",
    "Raw_Files/Dispatch data april'25 to 20'aug.xlsx",
    "Raw_Files/Dispatch data 27'march to till date_2026-08-21.xlsx",
]
SALES_BLOBS_2025 = [
    "Raw_Files/3rd Party Sales Jan'25 to Dec'25_ _2026-08-21_ .xlsx",
    "Raw_Files/3rd Party Sales Jan'25 to Dec'25.xlsx",
]
SALES_BLOBS_2026 = [
    "Raw_Files/3rd Party Sales Jan'26 to 20th Aug.xlsx",
    "Raw_Files/3rd Party Sales Jan'26 to 20th Aug .xlsx",
]
RTV_BLOBS = [
    "Raw_Files/RTV Data 27',march to 20th aug.xlsx",
]

DEFAULT_START = "2025-04-01"
DEFAULT_END = ""  # blank = latest date in the local Sales / Transfer masters
TRANSIT_DAYS = 2
HEADER_ROW = 3
# Sites per chunk for weekly calendar expand (keeps RAM bounded)
SITE_CHUNK = 100


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(BACKEND_DIR / ".env")
    except ImportError:
        pass


def _norm_site(series: pd.Series) -> pd.Series:
    s = series.astype(str).str.strip().str.upper()
    s = s.replace({"NAN": "", "NONE": "", "<NA>": "", "NAT": ""})
    return s.str.replace(r"\.0$", "", regex=True)


def _norm_barcode(series: pd.Series) -> pd.Series:
    num = pd.to_numeric(series, errors="coerce")
    out = pd.Series("", index=series.index, dtype="object")
    mask = num.notna()
    out.loc[mask] = num.loc[mask].astype("Int64").astype(str)
    out.loc[~mask] = series.loc[~mask].astype(str).str.strip()
    out = out.str.replace(r"\.0$", "", regex=True)
    return out.replace({"nan": "", "None": "", "NAN": "", "NONE": ""})


def _find_col(df: pd.DataFrame, *candidates: str) -> Optional[str]:
    lookup = {re.sub(r"[\s_]+", "", str(c).strip().lower()): c for c in df.columns}
    for cand in candidates:
        k = re.sub(r"[\s_]+", "", cand.strip().lower())
        if k in lookup:
            return lookup[k]
    for cand in candidates:
        needle = cand.strip().lower()
        for c in df.columns:
            if needle in str(c).strip().lower():
                return c
    return None


def _download_blob(blob_name: str, *, force: bool = False) -> Path:
    from azure.storage.blob import BlobServiceClient

    _load_dotenv()
    conn = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if not conn:
        raise RuntimeError("Set AZURE_STORAGE_CONNECTION_STRING in .env")
    container = os.environ.get("AZURE_STORAGE_CONTAINER", "zinc-data")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w.\-]+", "_", blob_name.replace("Raw_Files/", ""))
    dest = CACHE_DIR / safe
    etag_path = Path(str(dest) + ".etag")

    svc = BlobServiceClient.from_connection_string(conn)
    client = svc.get_container_client(container).get_blob_client(blob_name)
    if not client.exists():
        raise FileNotFoundError(f"Blob not found: {blob_name}")
    props = client.get_blob_properties()
    etag = str(getattr(props, "etag", None) or props.size)
    if (
        not force
        and dest.exists()
        and etag_path.exists()
        and etag_path.read_text(encoding="utf-8").strip() == etag
    ):
        log.info("Cache hit %s", dest.name)
        return dest

    log.info("Downloading %s (%.1f MB)…", blob_name, (props.size or 0) / 1e6)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    with open(tmp, "wb") as fh:
        for chunk in client.download_blob().chunks():
            fh.write(chunk)
    os.replace(tmp, dest)
    etag_path.write_text(etag, encoding="utf-8")
    return dest


def _resolve_first(blobs: Sequence[str], *, local_only: bool) -> Path:
    errors: List[str] = []
    if local_only:
        for b in blobs:
            safe = re.sub(r"[^\w.\-]+", "_", b.replace("Raw_Files/", ""))
            p = CACHE_DIR / safe
            if p.exists() and p.stat().st_size > 0:
                return p
            errors.append(f"missing cache: {p.name}")
        raise FileNotFoundError("; ".join(errors))
    for b in blobs:
        try:
            return _download_blob(b)
        except Exception as e:
            errors.append(f"{b}: {e}")
    raise FileNotFoundError("None of the blobs resolved:\n  " + "\n  ".join(errors))


def _read_ginesys_excel(path: Path) -> pd.DataFrame:
    """Fast Excel via python_calamine; openpyxl fallback."""
    try:
        from python_calamine import CalamineWorkbook

        wb = CalamineWorkbook.from_path(str(path))
        sheet = wb.get_sheet_by_index(0)
        rows = sheet.to_python()
        if len(rows) <= HEADER_ROW:
            raise ValueError(f"Too few rows in {path.name}")
        header = [str(c).strip() if c is not None else "" for c in rows[HEADER_ROW]]
        df = pd.DataFrame(rows[HEADER_ROW + 1 :], columns=header)
    except Exception as e:
        log.warning("calamine read failed (%s) — openpyxl fallback", e)
        df = pd.read_excel(path, header=HEADER_ROW, engine="openpyxl")
        df.columns = [str(c).strip() for c in df.columns]

    df.columns = [str(c).strip() for c in df.columns]
    return df.loc[:, [c for c in df.columns if c and not str(c).startswith("Unnamed")]]


def load_dispatch(path: Path, *, transit_days: int = TRANSIT_DAYS) -> pd.DataFrame:
    raw = _read_ginesys_excel(path)
    date_c = _find_col(raw, "INVOICE DATE", "DATE")
    site_c = _find_col(raw, "STORE CODE", "SITE CODE", "SITE")
    bc_c = _find_col(raw, "BARCODE", "EAN")
    qty_c = _find_col(raw, "QTY", "QUANTITY", "DISPATCH QTY")
    missing = [n for n, c in [("date", date_c), ("site", site_c), ("barcode", bc_c), ("qty", qty_c)] if not c]
    if missing:
        raise ValueError(f"Dispatch missing {missing}. Found={list(raw.columns)}")

    out = pd.DataFrame(
        {
            "date": pd.to_datetime(raw[date_c], errors="coerce") + pd.Timedelta(days=transit_days),
            "site": _norm_site(raw[site_c]),
            "barcode": _norm_barcode(raw[bc_c]),
            "qty": pd.to_numeric(raw[qty_c], errors="coerce").fillna(0.0),
            "source": "dispatch",
        }
    )
    out = out[(out["site"] != "") & (out["barcode"] != "") & out["date"].notna()]
    out = out[out["qty"] != 0]
    log.info("Dispatch rows kept: %s (%s)", f"{len(out):,}", path.name)
    return out


def load_sales(paths: Iterable[Path]) -> pd.DataFrame:
    frames: List[pd.DataFrame] = []
    for path in paths:
        raw = _read_ginesys_excel(path)
        date_c = _find_col(raw, "ENTRY_DATE", "ENTRY DATE", "BILL DATE", "DATE")
        site_c = _find_col(raw, "STORE CODE", "SITE CODE", "SITE")
        bc_c = _find_col(raw, "BARCODE", "EAN")
        qty_c = _find_col(raw, "QUANTITY", "QTY", "SOLD QTY", "NET SALE QTY")
        missing = [n for n, c in [("date", date_c), ("site", site_c), ("barcode", bc_c), ("qty", qty_c)] if not c]
        if missing:
            raise ValueError(f"Sales missing {missing} in {path.name}")

        part = pd.DataFrame(
            {
                "date": pd.to_datetime(raw[date_c], errors="coerce"),
                "site": _norm_site(raw[site_c]),
                "barcode": _norm_barcode(raw[bc_c]),
                "qty": -pd.to_numeric(raw[qty_c], errors="coerce").fillna(0.0).abs(),
                "source": "sales",
            }
        )
        part = part[(part["site"] != "") & (part["barcode"] != "") & part["date"].notna()]
        part = part[part["qty"] != 0]
        log.info("Sales rows kept: %s (%s)", f"{len(part):,}", path.name)
        frames.append(part)
    if not frames:
        return pd.DataFrame(columns=["date", "site", "barcode", "qty", "source"])
    out = pd.concat(frames, ignore_index=True).drop_duplicates()
    log.info("Sales after dedupe: %s", f"{len(out):,}")
    return out


def load_rtv(path: Path) -> pd.DataFrame:
    """
    RTV reduces store SOH. Requires BARCODE (+ site + date + qty).
    Current Ginesys RTV dump is invoice-level (no barcode) → empty frame.
    When barcode appears in future dumps, this path activates automatically.
    """
    raw = _read_ginesys_excel(path)
    date_c = _find_col(raw, "SALES RETURN DATE", "RETURN DATE", "DOCUMENT_DATE", "DATE")
    site_c = _find_col(raw, "STORE CODE", "SITE CODE", "Site Code", "SITE")
    bc_c = _find_col(raw, "BARCODE", "EAN", "SKU")
    qty_c = _find_col(raw, "RETURN_QUANTITY", "RETURN QTY", "QTY", "QUANTITY")

    if bc_c is None:
        log.warning(
            "RTV has no BARCODE — barcode-level returns skipped until file includes barcode. "
            "cols=%s",
            list(raw.columns)[:12],
        )
        return pd.DataFrame(columns=["date", "site", "barcode", "qty", "source"])

    missing = [n for n, c in [("date", date_c), ("site", site_c), ("qty", qty_c)] if not c]
    if missing:
        log.warning("RTV missing %s — skipped", missing)
        return pd.DataFrame(columns=["date", "site", "barcode", "qty", "source"])

    out = pd.DataFrame(
        {
            "date": pd.to_datetime(raw[date_c], errors="coerce"),
            "site": _norm_site(raw[site_c]),
            "barcode": _norm_barcode(raw[bc_c]),
            "qty": -pd.to_numeric(raw[qty_c], errors="coerce").fillna(0.0).abs(),
            "source": "rtv",
        }
    )
    out = out[(out["site"] != "") & (out["barcode"] != "") & out["date"].notna()]
    out = out[out["qty"] != 0]
    log.info("RTV rows kept: %s (%s)", f"{len(out):,}", path.name)
    return out


def week_endings(start: pd.Timestamp, end: pd.Timestamp) -> List[pd.Timestamp]:
    first = start + pd.Timedelta(days=(6 - start.dayofweek) % 7)
    if first < start:
        first += pd.Timedelta(days=7)
    return [pd.Timestamp(d).normalize() for d in pd.date_range(first, end, freq="W-SUN")]


def _map_to_week_ending(dates: pd.Series, calendar: pd.DatetimeIndex) -> pd.Series:
    """Map each date → first calendar Sunday on/after date (clipped)."""
    idx = calendar.searchsorted(dates.to_numpy(dtype="datetime64[ns]"), side="left")
    idx = np.clip(idx, 0, len(calendar) - 1)
    return pd.Series(calendar.take(idx), index=dates.index)


def _sell_through(opening: pd.Series, receipts: pd.Series, sales: pd.Series) -> pd.Series:
    denom = (opening + receipts).clip(lower=0)
    st = sales / denom.replace(0, np.nan) * 100.0
    st = st.astype(float)
    st.loc[denom <= 0] = np.nan
    return st


def build_weekly_soh_sellthrough(
    txns: pd.DataFrame,
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
    site_chunk: int = SITE_CHUNK,
) -> pd.DataFrame:
    """
    Memory-safe weekly SOH: process by site chunks so we never allocate
    (all_keys × all_weeks) in one shot.
    """
    tx = txns.copy()
    tx["date"] = pd.to_datetime(tx["date"], errors="coerce").dt.normalize()
    tx = tx.dropna(subset=["date"])
    tx = tx[tx["date"] <= end]
    if tx.empty:
        raise ValueError("No transactions in range")

    tx["receipts"] = tx["qty"].clip(lower=0)
    tx["sales"] = (-tx["qty"]).clip(lower=0)

    daily = (
        tx.groupby(["site", "barcode", "date"], as_index=False, sort=False)
        .agg(net=("qty", "sum"), receipts=("receipts", "sum"), sales=("sales", "sum"))
        .sort_values(["site", "barcode", "date"])
    )
    daily["closing_soh"] = daily.groupby(["site", "barcode"], sort=False)["net"].cumsum()

    ends = week_endings(start, end)
    if not ends:
        raise ValueError(f"No Sunday week-endings between {start.date()} and {end.date()}")
    pre_end = ends[0] - pd.Timedelta(days=7)
    cal = pd.DatetimeIndex([pre_end] + ends)
    ends_ts = pd.DatetimeIndex(ends)

    # Week flow inside analysis window — datetime bins, NOT categorical (avoids OOM)
    tx_win = tx[(tx["date"] >= start) & (tx["date"] <= end)].copy()
    tx_win["week_ending"] = _map_to_week_ending(tx_win["date"], ends_ts)
    week_flow = (
        tx_win.groupby(["site", "barcode", "week_ending"], as_index=False, sort=False)
        .agg(receipts=("receipts", "sum"), sales=("sales", "sum"))
    )

    daily["week_ending"] = _map_to_week_ending(daily["date"], cal)
    week_close = (
        daily.groupby(["site", "barcode", "week_ending"], as_index=False, sort=False)
        .agg(closing_soh=("closing_soh", "last"))
    )

    sites = sorted(daily["site"].unique().tolist())
    n_keys = daily[["site", "barcode"]].drop_duplicates().shape[0]
    log.info(
        "Building %s weeks × %s site-barcode keys in chunks of %s sites (%s sites total)…",
        len(ends),
        f"{n_keys:,}",
        site_chunk,
        len(sites),
    )

    cal_df = pd.DataFrame({"week_ending": cal})
    parts: List[pd.DataFrame] = []
    for i in range(0, len(sites), site_chunk):
        batch = sites[i : i + site_chunk]
        keys = (
            daily.loc[daily["site"].isin(batch), ["site", "barcode"]]
            .drop_duplicates()
        )
        if keys.empty:
            continue
        keys = keys.assign(_k=1)
        grid = keys.merge(cal_df.assign(_k=1), on="_k").drop(columns="_k")

        wc = week_close[week_close["site"].isin(batch)]
        wf = week_flow[week_flow["site"].isin(batch)]

        closing = grid.merge(wc, on=["site", "barcode", "week_ending"], how="left")
        closing = closing.sort_values(["site", "barcode", "week_ending"])
        closing["closing_soh"] = (
            closing.groupby(["site", "barcode"], sort=False)["closing_soh"].ffill().fillna(0.0)
        )
        closing["opening_soh"] = (
            closing.groupby(["site", "barcode"], sort=False)["closing_soh"].shift(1).fillna(0.0)
        )
        closing = closing[closing["week_ending"] >= ends[0]]

        out = closing.merge(wf, on=["site", "barcode", "week_ending"], how="left")
        out["receipts"] = out["receipts"].fillna(0.0)
        out["sales"] = out["sales"].fillna(0.0)
        out["sell_through_pct"] = _sell_through(out["opening_soh"], out["receipts"], out["sales"])

        # Drop dead keys in this chunk (never any movement / stock)
        active = (
            out.groupby(["site", "barcode"], sort=False)[["receipts", "sales", "closing_soh"]]
            .transform(lambda s: s.abs().sum())
        )
        out = out[(active["receipts"] + active["sales"] + active["closing_soh"].abs()) > 0]
        parts.append(out)
        log.info("  chunk %s–%s / %s sites → %s rows", i + 1, min(i + site_chunk, len(sites)), len(sites), f"{len(out):,}")

    if not parts:
        raise ValueError("No weekly SOH rows produced")

    weekly = pd.concat(parts, ignore_index=True)
    weekly["week_ending"] = pd.to_datetime(weekly["week_ending"]).dt.strftime("%Y-%m-%d")
    cols = [
        "site",
        "barcode",
        "week_ending",
        "opening_soh",
        "receipts",
        "sales",
        "closing_soh",
        "sell_through_pct",
    ]
    return weekly[cols].sort_values(["site", "barcode", "week_ending"]).reset_index(drop=True)


def build_monthly_from_weekly(weekly: pd.DataFrame) -> pd.DataFrame:
    """
    Month grain from weekly:
      month_start opening = first week opening in month
      receipts/sales = sum
      closing = last week closing in month
      sell_through = sales / (opening + receipts)
    """
    w = weekly.copy()
    w["week_ending"] = pd.to_datetime(w["week_ending"])
    w["month"] = w["week_ending"].dt.to_period("M").astype(str)
    w = w.sort_values(["site", "barcode", "week_ending"])

    g = w.groupby(["site", "barcode", "month"], sort=False)
    monthly = g.agg(
        opening_soh=("opening_soh", "first"),
        receipts=("receipts", "sum"),
        sales=("sales", "sum"),
        closing_soh=("closing_soh", "last"),
        weeks=("week_ending", "count"),
    ).reset_index()
    monthly["sell_through_pct"] = _sell_through(
        monthly["opening_soh"], monthly["receipts"], monthly["sales"]
    )
    cols = [
        "site",
        "barcode",
        "month",
        "opening_soh",
        "receipts",
        "sales",
        "closing_soh",
        "sell_through_pct",
        "weeks",
    ]
    return monthly[cols].sort_values(["site", "barcode", "month"]).reset_index(drop=True)


def _load_txns(
    *,
    local_cache_only: bool,
    transit_days: int,
    reuse_txns: bool,
) -> tuple[pd.DataFrame, dict]:
    meta: dict = {}
    sales_master = BACKEND_DIR / "pipeline" / "input_files" / "SSI_PT_CENTRO_ICONIC_EBO_SALES_MASTER.csv"
    transfer_master = BACKEND_DIR / "pipeline" / "input_files" / "SALES_INVOICE_TRACKING_MASTER.csv"
    if not reuse_txns and (sales_master.exists() or transfer_master.exists()):
        from api.historical_movement import load_daily_movement

        daily = load_daily_movement()
        if daily is not None and not daily.empty:
            rec = daily.loc[daily["receipts"] > 0, ["date", "site_key", "receipts"]].rename(columns={"site_key": "site", "receipts": "qty"})
            rec["barcode"] = ""
            rec["source"] = "dispatch"
            sold = daily.loc[daily["sales"] > 0, ["date", "site_key", "sales"]].rename(columns={"site_key": "site", "sales": "qty"})
            sold["qty"] = -sold["qty"].abs()
            sold["barcode"] = ""
            sold["source"] = "sales"
            rtv = daily.loc[daily["rtv_out"] > 0, ["date", "site_key", "rtv_out"]].rename(columns={"site_key": "site", "rtv_out": "qty"})
            rtv["qty"] = -rtv["qty"].abs()
            rtv["barcode"] = ""
            rtv["source"] = "rtv"
            txns = pd.concat([rec, sold, rtv], ignore_index=True)
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            txns.to_parquet(TXNS_PARQUET, index=False)
            log.info("Txn ledger from local masters → %s (%s rows)", TXNS_PARQUET, f"{len(txns):,}")
            return txns, {
                "source": "local-masters",
                "sales_file": sales_master.name,
                "transfer_file": transfer_master.name,
                "transit_days": TRANSIT_DAYS,
                "note": "Sales Master + Transfer Master + RTV, not the 20 Aug Raw_Files dump",
            }

    if reuse_txns and TXNS_PARQUET.exists():
        log.info("Reusing txn ledger %s", TXNS_PARQUET)
        return pd.read_parquet(TXNS_PARQUET), {"source": "parquet", "path": str(TXNS_PARQUET)}

    dispatch_path = _resolve_first(DISPATCH_BLOBS, local_only=local_cache_only)
    uniq_sales: List[Path] = []
    for group in (SALES_BLOBS_2025, SALES_BLOBS_2026):
        try:
            uniq_sales.append(_resolve_first(group, local_only=local_cache_only))
        except FileNotFoundError:
            continue
    if not uniq_sales:
        raise FileNotFoundError("No sales Raw_Files resolved")

    rtv = pd.DataFrame(columns=["date", "site", "barcode", "qty", "source"])
    rtv_name = None
    try:
        rtv_path = _resolve_first(RTV_BLOBS, local_only=local_cache_only)
        rtv_name = rtv_path.name
        rtv = load_rtv(rtv_path)
    except FileNotFoundError:
        log.warning("RTV blob not found — continuing without returns")

    log.info("Loading dispatch from %s", dispatch_path.name)
    dispatch = load_dispatch(dispatch_path, transit_days=transit_days)
    log.info("Loading sales from %s", [p.name for p in uniq_sales])
    sales = load_sales(uniq_sales)

    frames = [dispatch, sales]
    if not rtv.empty:
        frames.append(rtv)
    txns = pd.concat(frames, ignore_index=True)
    log.info(
        "Total txn rows: %s (dispatch=%s sales=%s rtv=%s)",
        f"{len(txns):,}",
        f"{len(dispatch):,}",
        f"{len(sales):,}",
        f"{len(rtv):,}",
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    txns.to_parquet(TXNS_PARQUET, index=False)
    log.info("Cached ledger → %s", TXNS_PARQUET)

    meta = {
        "source": "excel",
        "dispatch_file": dispatch_path.name,
        "sales_files": [p.name for p in uniq_sales],
        "rtv_file": rtv_name,
        "rtv_rows": len(rtv),
        "transit_days": transit_days,
    }
    return txns, meta


def run(
    *,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    transit_days: int = TRANSIT_DAYS,
    local_cache_only: bool = False,
    reuse_txns: bool = False,
    site_chunk: int = SITE_CHUNK,
) -> dict:
    _load_dotenv()
    logging.getLogger("azure").setLevel(logging.WARNING)
    start_ts = pd.Timestamp(start).normalize()
    end_ts = pd.Timestamp(end).normalize() if str(end or "").strip() else None
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    txns, src_meta = _load_txns(
        local_cache_only=local_cache_only,
        transit_days=transit_days,
        reuse_txns=reuse_txns,
    )
    if end_ts is None:
        end_ts = pd.Timestamp(pd.to_datetime(txns["date"], errors="coerce").max()).normalize()
        log.info("End date set from local masters: %s", end_ts.date())

    weekly = build_weekly_soh_sellthrough(
        txns, start=start_ts, end=end_ts, site_chunk=site_chunk
    )
    weekly_path = OUT_DIR / f"weekly_soh_sellthrough_{start_ts.date()}_{end_ts.date()}.csv"
    weekly.to_csv(weekly_path, index=False)
    log.info("Wrote weekly %s rows → %s", f"{len(weekly):,}", weekly_path)

    monthly = build_monthly_from_weekly(weekly)
    monthly_path = OUT_DIR / f"monthly_soh_sellthrough_{start_ts.date()}_{end_ts.date()}.csv"
    monthly.to_csv(monthly_path, index=False)
    log.info("Wrote monthly %s rows → %s", f"{len(monthly):,}", monthly_path)

    summary = {
        "start": str(start_ts.date()),
        "end": str(end_ts.date()),
        "transit_days": transit_days,
        **src_meta,
        "weekly_rows": len(weekly),
        "monthly_rows": len(monthly),
        "sites": int(weekly["site"].nunique()),
        "barcodes": int(weekly["barcode"].nunique()),
        "weeks": int(weekly["week_ending"].nunique()),
        "months": int(monthly["month"].nunique()),
        "total_sales_units": float(weekly["sales"].sum()),
        "total_receipt_units": float(weekly["receipts"].sum()),
        "median_weekly_sell_through_pct": float(weekly["sell_through_pct"].median(skipna=True)),
        "weekly_output": str(weekly_path),
        "monthly_output": str(monthly_path),
        "txn_ledger": str(TXNS_PARQUET),
        "note": (
            "SOH = cumulative dispatch − sales − rtv(when barcode present). "
            "RTV without barcode is skipped automatically."
        ),
    }
    summary_path = OUT_DIR / f"soh_sellthrough_{start_ts.date()}_{end_ts.date()}.summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    log.info("Summary → %s", summary_path)
    print(json.dumps(summary, indent=2))
    return summary


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    p = argparse.ArgumentParser(
        description="Historical weekly+monthly SOH + sell-through from Dispatch/Sales/RTV"
    )
    p.add_argument("--start", default=DEFAULT_START)
    p.add_argument("--end", default=DEFAULT_END, help="Blank uses the latest date in the local masters")
    p.add_argument("--transit-days", type=int, default=TRANSIT_DAYS)
    p.add_argument("--local-cache-only", action="store_true")
    p.add_argument(
        "--reuse-txns",
        action="store_true",
        help="Skip Excel; reuse output_files/historical_soh/txns_ledger.parquet",
    )
    p.add_argument("--site-chunk", type=int, default=SITE_CHUNK, help="Sites per RAM chunk")
    args = p.parse_args()
    run(
        start=args.start,
        end=args.end,
        transit_days=args.transit_days,
        local_cache_only=args.local_cache_only,
        reuse_txns=args.reuse_txns,
        site_chunk=args.site_chunk,
    )


if __name__ == "__main__":
    main()

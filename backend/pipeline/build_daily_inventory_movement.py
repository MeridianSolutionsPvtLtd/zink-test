"""
build_daily_inventory_movement.py  (corrected version)
=======================================================
Daily Inventory Movement + SOH + Sell-through (Total / Full Price / Discount)

BUSINESS RULES (as agreed)
--------------------------
1. SCOPE
   * Agar stock site pe AAYA (DISPATCH in ya IBT in) → us din SOH badhta hai, agle din
     opening balance mein carry. Us store×barcode ko FULL monitor karte hain:
     saari sales / IBT out / RTV — capped / "sirf dispatch-jitni sales" NAHI.
   * RTV_OUT bhi cohort kholta hai (warna raw returns drop ho jati thin).
   * Sale-only pairs (bina kisi inflow/RTV) cohort NAHI kholte — pehle se pada /
     undocumented stock alag exceptions mein nahi ghumaate.
   * Opening SOH = 0; pehla DISPATCH/IBT in usi din closing mein add, phir opening.
   * 27-Mar .. 01-Apr-2025 ka dispatch store par 01-Apr-2025 ko pahuncha mana jata hai.
   * Sale / IBT / RTV sirf 01-Apr-2025 se ginte jaate hain.
   * Cohort STORE x BARCODE level par hai.

2. SALE BEFORE DISPATCH (invoice baad mein bana)
   * Agar kisi store-barcode par sale / IBT out / RTV ke waqt stock zero se neeche jaa raha
     ho aur aage dispatch invoice aana baaki ho, to wo dispatch usi din (sale ki date) par
     inflow mana jata hai ("pull forward"). Sirf utna hi pull hota hai jitna zaroori ho.
   * Phir bhi jahan total outflow > total inflow ho, wahan SOH negative rehta hai aur
     exceptions file mein aata hai (asli shortfall: missing dispatch / missing IBT).

3. MOVEMENT / SOH
   Closing = Opening + Dispatch In + IBT In - IBT Out - RTV Out - Gross Sale + Sale Return
   (Sale return = Qty minus wali rows; ye sale mein ADD nahi hoti, net sale kam karti hai.)
   RTV store se maal wapas jaata hai => store SOH minus.

4. SELL-THROUGH (cumulative, per row)
   Available (stock base) = Opening + Dispatch In + IBT In + Sales Return
     (lifetime cum from open: Opening=0 → cum(Disp In + IBT In + Sales Return))
   IBT Out / RTV Out SOH se nikalte hain, lekin STR denominator se MINUS nahi hote —
   wo alag outflow hain (net sale + ibt out + rtv + closing = available).
   SALES_THROUGH_PCT               = Cum Net Sale          / Cum Available * 100
   FULL_PRICE_SALES_THROUGH_PCT    = Cum Net Sale (<=10%)  / Cum Available * 100
   DISCOUNT_SALES_THROUGH_PCT      = Cum Net Sale (>10%)   / Cum Available * 100
   Weekly/Monthly/FY ke liye % ko sum/average mat karo: period ke sums se naya % banao.
   Discount % = (RSP*Qty - Item Net Amt) / (RSP*Qty)  (Item Net Amt tax-inclusive hai).

5. DATA CLEANING
   * Sirf 13-digit numeric barcodes (fabric/trims 6-digit codes hataye jaate hain).
   * Scientific-notation barcode (8.90757E+12) ko (SKU, Size) item master se repair karta hai;
     repair na ho paye to row drop (count log hota hai).
   * Fresco UP / Fresco Global (internal warehouse transfer) store inflow nahi hai.
   * Total / blank rows (store ya date khaali) hataye jaate hain.
   * Store ka common key bracket code se banta hai: "PT-X (P033)" -> P033,
     "P738-ALKAPURI-VADODARA" -> P738, "8563 - SSL-Rajahmundry" -> 8563.
   * Retail-only filter: dispatch / IBT / RTV sirf unhi stores ka jo sales file mein aate hain.

OUTPUT
   data/api_cache/daily_inventory_movement.parquet
   data/api_cache/daily_inventory_movement.meta.json
   data/api_cache/daily_inventory_exceptions.csv   (final negative SOH store x barcode)
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Paths / constants
# ---------------------------------------------------------------------------
BACKEND_DIR = Path(__file__).resolve().parents[1] if "__file__" in globals() else Path(".")
INPUT_DIR = BACKEND_DIR / "pipeline" / "input_files"
CACHE_DIR = BACKEND_DIR / "data" / "api_cache"

START_DATE = "2025-03-27"          # first dispatch date to monitor
STORE_OPEN_DATE = "2025-04-01"     # dispatch of 27-Mar..01-Apr is treated as arrived on this date
FULL_PRICE_MAX_DISC = 0.10         # discount <= 10% => full price sale
INTERNAL_SITE_PATTERN = r"^FRESCO GLOBAL PVT\.? ?LTD\.?(\s*\(U\.P\.\))?\s*$"   # internal warehouse transfers (not store inflow)

INFLOW = ("DISP_IN", "IBT_IN")
SIGN = {"DISP_IN": 1, "IBT_IN": 1, "IBT_OUT": -1, "RTV_OUT": -1, "SALE": -1, "SALE_RET": 1}

log = logging.getLogger("build_daily_inventory_movement")


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------
def _norm_barcode(s: pd.Series) -> pd.Series:
    out = s.astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    return out.where(~out.str.upper().isin(["NAN", "NONE", "<NA>", "NAT"]), "")


def _to_date(s: pd.Series) -> pd.Series:
    """
    Mixed-format date parser.
    * ISO (2026-09-05 / 2026-09-05 00:00:00) ko year-first padhta hai (dayfirst se ulta nahi hota).
    * Baaki (15-03-2026 / 05/03/2026) ko day-first padhta hai.
    """
    t = s.astype(str).str.strip()
    iso = t.str.match(r"^\d{4}-\d{1,2}-\d{1,2}")
    out = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")
    if iso.any():
        out[iso] = pd.to_datetime(t[iso].str[:10], format="%Y-%m-%d", errors="coerce")
    if (~iso).any():
        out[~iso] = pd.to_datetime(t[~iso], format="mixed", dayfirst=True, errors="coerce")
    return out.dt.normalize()


_FIND_SUBDIRS = (
    "",
    "RTV Data/current",
    "IBT Data/current",
    "Transfer-Master (PT, SSL, Centro, Iconic)/current",
    "Sales-Master (PT, SSL, Centro, Iconic, EBO)/current",
    "Sales-Master (PT, SSL, Centro, Iconic)/current",
)


def _find_file(names: Sequence[str]) -> Optional[Path]:
    for n in names:
        for sub in _FIND_SUBDIRS:
            p = INPUT_DIR / sub / n if sub else INPUT_DIR / n
            if p.exists():
                return p
    # Last resort: shallow recursive match by filename
    for n in names:
        hits = list(INPUT_DIR.rglob(n))
        if hits:
            return sorted(hits, key=lambda x: x.stat().st_mtime, reverse=True)[0]
    return None


def _read_table(path: Path, header_hints: Sequence[str], usecols: Optional[Sequence[str]] = None) -> pd.DataFrame:
    """
    CSV/XLSX padhta hai. Upar ke junk/title rows skip karta hai: pehli row jisme koi header_hint
    mile use header maanta hai. Sab columns string (dtype=str) mein aate hain.
    """
    hints = {h.strip().upper() for h in header_hints}
    if path.suffix.lower() in (".xlsx", ".xlsm", ".xls"):
        raw = pd.read_excel(path, header=None, dtype=str, nrows=15)
        reader = lambda skip: pd.read_excel(path, skiprows=skip, dtype=str)
    else:
        raw = pd.read_csv(path, header=None, dtype=str, nrows=15, low_memory=False, on_bad_lines="skip")
        reader = lambda skip: pd.read_csv(path, skiprows=skip, dtype=str, low_memory=False)
    hdr_row = None
    for i in range(len(raw)):
        cells = {str(c).strip().upper() for c in raw.iloc[i].tolist()}
        if hints & cells:
            hdr_row = i
            break
    if hdr_row is None:
        raise ValueError(f"{path.name}: header row nahi mili (expected one of {sorted(hints)})")
    df = reader(hdr_row)
    df.columns = [str(c).strip() for c in df.columns]
    return df


def _pick(df: pd.DataFrame, candidates: Sequence[str], required: bool = True, label: str = "") -> Optional[str]:
    """
    Column resolver. Pehle EXACT naam, phir case-insensitive.
    (BARCODE aur Barcode dono ek saath ho sakte hain - dono alag columns hain.)
    Required column na mile to error (silent zero nahi).
    """
    cols = [c.strip() for c in df.columns]
    for c in candidates:
        if c in cols:
            return df.columns[cols.index(c)]
    upper = [c.upper() for c in cols]
    for c in candidates:
        if c.upper() in upper:
            return df.columns[upper.index(c.upper())]
    if required:
        raise KeyError(f"{label or 'column'}: {list(candidates)} mein se koi nahi mila. Available: {list(df.columns)[:40]}")
    return None


def _code_or_none(x: object) -> Optional[str]:
    """Store naam se code nikalta hai. Code na mile to None."""
    t = str(x).strip().upper()
    if t in ("", "NAN", "NONE", "<NA>"):
        return None
    m = re.findall(r"\(\s*([A-Z0-9]+)\s*\)", t)
    if m:
        return m[-1]
    m2 = re.match(r"^([A-Z]?\d{2,4})\s*-", t)          # "P738-ALKAPURI..." / "8563 - SSL-..."
    if m2:
        return m2.group(1)
    m3 = re.search(r"[-,\s]([A-Z]?\d{2,4})$", t)         # "... - 8563"
    if m3:
        return m3.group(1)
    return None


def _store_key(*series: pd.Series) -> pd.Series:
    """Pehle available code, warna pehli series ka normalised naam."""
    first = series[0]
    res = pd.Series(np.nan, index=first.index, dtype="object")
    for s in series:
        uniq = s.dropna().unique()
        mp = {u: _code_or_none(u) for u in uniq}
        res = res.fillna(s.map(mp))
    fallback = first.fillna("").astype(str).str.upper().str.replace(r"[^A-Z0-9]", "", regex=True)
    return res.fillna(fallback).replace({"": np.nan})


def _empty_events() -> pd.DataFrame:
    return pd.DataFrame(columns=["date", "store", "bc", "type", "qty", "dq"])


def _events(date, store, bc, typ: str, qty, dq=0.0) -> pd.DataFrame:
    n = len(date)
    return pd.DataFrame({
        "date": pd.to_datetime(np.asarray(date)),
        "store": np.asarray(store, dtype=object),
        "bc": np.asarray(bc, dtype=object),
        "type": typ,
        "qty": np.asarray(qty, dtype=float),
        "dq": np.broadcast_to(np.asarray(dq, dtype=float), (n,)).copy(),
    })


# ---------------------------------------------------------------------------
# Item master (barcode repair)
# ---------------------------------------------------------------------------
def _get_item_master_sku_size_map() -> Dict[Tuple[str, str], str]:
    """(SKU, SIZE) -> real 13-digit barcode."""
    pq_details = CACHE_DIR / "barcode_item_details.parquet"
    if pq_details.exists():
        try:
            df = pd.read_parquet(pq_details, columns=["sku", "size", "barcode_key"])
            df["sku"] = df["sku"].astype(str).str.strip().str.upper()
            df["size"] = df["size"].astype(str).str.strip().str.upper()
            df["bc"] = _norm_barcode(df["barcode_key"])
            df = df[df["bc"].str.fullmatch(r"\d{13}")].drop_duplicates(["sku", "size"])
            return {(a, b): c for a, b, c in zip(df["sku"], df["size"], df["bc"])}
        except Exception as exc:  # pragma: no cover
            log.warning("barcode_item_details.parquet padh nahi paya: %s", exc)
    try:  # pragma: no cover
        from pipeline.pipeline_inputs import load_item_master
        item = load_item_master()
        sku_c = next(c for c in item.columns if "SKU" in c.upper() or "ICODE" in c.upper())
        size_c = next(c for c in item.columns if "SIZE" in c.upper())
        bc_c = next(c for c in item.columns if "BARCODE" in c.upper() or "EAN" in c.upper())
        m = item[[sku_c, size_c, bc_c]].dropna().copy()
        m.columns = ["sku", "size", "bc"]
        m["sku"] = m["sku"].astype(str).str.strip().str.upper()
        m["size"] = m["size"].astype(str).str.strip().str.upper()
        m["bc"] = _norm_barcode(m["bc"])
        m = m[m["bc"].str.fullmatch(r"\d{13}")].drop_duplicates(["sku", "size"])
        return {(a, b): c for a, b, c in zip(m["sku"], m["size"], m["bc"])}
    except Exception as exc:
        log.warning("Item master se barcode repair map nahi bana: %s", exc)
    return {}


# ---------------------------------------------------------------------------
# Source loaders  (each returns events + metadata + audit numbers)
# ---------------------------------------------------------------------------
def load_dispatch(start: pd.Timestamp, end: pd.Timestamp, open_date: pd.Timestamp,
                  drop_duplicates: bool) -> Tuple[pd.DataFrame, pd.DataFrame, dict]:
    path = _find_file(["SALES_INVOICE_TRACKING.csv", "SALES_INVOICE_TRACKING_MASTER.csv",
                       "SALES_INVOICE_TRACKING_MASTER (1).csv"])
    if path is None:
        raise FileNotFoundError("Dispatch file (SALES_INVOICE_TRACKING*.csv) nahi mili")
    log.info("Dispatch: %s", path.name)
    df = _read_table(path, ["INVOICE_DATE", "INVOICE_QUANTITY"])

    site_cols = [c for c in ("Ref Site", "SITE NAME", "SITE SHORT NAME") if c in df.columns]
    if not site_cols:
        raise KeyError("Dispatch: store column (Ref Site / SITE NAME) nahi mila")
    site_name = df[site_cols[0]]
    for c in site_cols[1:]:
        site_name = site_name.fillna(df[c])
    df["_site_name"] = site_name

    bc_cols = [c for c in ("BARCODE", "Barcode") if c in df.columns]      # dono alag columns hain
    if not bc_cols:
        raise KeyError("Dispatch: BARCODE / Barcode column nahi mila")
    # Barcode column ka koi bhi 13-digit valid value use karo (BARCODE kharab ho to Barcode, ya ulta)
    bc = pd.Series("", index=df.index, dtype="object")
    for c in bc_cols:
        cand = _norm_barcode(df[c])
        ok = cand.str.fullmatch(r"\d{13}")
        bc = bc.where(bc.str.fullmatch(r"\d{13}"), cand.where(ok, bc))
    df["bc"] = bc

    dcol = _pick(df, ["INVOICE_DATE", "DC_DATE", "DOCUMENT DATE"], label="dispatch date")
    date = _to_date(df[dcol])
    for alt in ("DC_DATE", "DOCUMENT DATE"):
        c = _pick(df, [alt], False)
        if c and c != dcol:
            date = date.fillna(_to_date(df[c]))
    df["date"] = date
    df["qty"] = pd.to_numeric(df[_pick(df, ["INVOICE_QUANTITY", "QTY"], label="dispatch qty")], errors="coerce").fillna(0.0).abs()

    c3, c5, c6 = (_pick(df, [n], False) for n in ("CATEGORY3", "CATEGORY5", "CATEGORY6"))
    df["_sku"] = df[c3].fillna("").str.strip() if c3 else ""
    df["_size"] = df[c5].fillna("").str.strip() if c5 else ""
    df["_season"] = df[c6].fillna("").str.strip() if c6 else ""

    audit: dict = {"dispatch_rows_raw": int(len(df))}

    # Barcode repair (scientific notation / truncated)
    bad = ~df["bc"].str.fullmatch(r"\d{13}")
    audit["dispatch_rows_bad_barcode"] = int(bad.sum())
    if bad.any() and c3 and c5:
        mp = _get_item_master_sku_size_map()
        if mp:
            keys = list(zip(df.loc[bad, "_sku"].str.upper(), df.loc[bad, "_size"].str.upper()))
            fixed = pd.Series([mp.get(k, "") for k in keys], index=df.index[bad])
            df.loc[bad, "bc"] = fixed.where(fixed != "", df.loc[bad, "bc"])
    bad2 = ~df["bc"].str.fullmatch(r"\d{13}")
    audit["dispatch_rows_dropped_unrepaired_or_non13"] = int(bad2.sum())
    audit["dispatch_qty_dropped_unrepaired_or_non13"] = float(df.loc[bad2, "qty"].sum())
    df = df[~bad2]

    df = df.dropna(subset=["date"])
    df = df[(df["date"] >= start) & (df["date"] <= end) & (df["qty"] > 0) & df["_site_name"].notna()]

    internal = df["_site_name"].str.upper().str.match(INTERNAL_SITE_PATTERN, na=False)
    audit["dispatch_internal_transfer_qty_excluded"] = float(df.loc[internal, "qty"].sum())
    df = df[~internal]

    if drop_duplicates:
        before = len(df)
        df = df.drop_duplicates()
        audit["dispatch_duplicate_rows_dropped"] = int(before - len(df))

    df["store"] = _store_key(df["_site_name"])
    # 27-Mar .. 01-Apr dispatch => 01-Apr ko store par
    arr = df["date"].where(df["date"] > open_date, open_date)
    ev = _events(arr, df["store"], df["bc"], "DISP_IN", df["qty"])

    meta = df[["bc", "_sku", "_size", "_season"]].rename(
        columns={"bc": "barcode", "_sku": "sku", "_size": "size", "_season": "season"})
    for col, nm in (("department", "DEPARTMENT"), ("section", "SECTION")):
        c = _pick(df, [nm], False)
        meta[col] = df[c].fillna("").str.strip() if c else ""
    meta = meta.drop_duplicates("barcode")
    audit["dispatch_qty_in_scope"] = float(ev["qty"].sum())
    log.info("Dispatch events: %s rows, qty %s", f"{len(ev):,}", f"{ev['qty'].sum():,.0f}")
    return ev, meta, audit


def load_sales(start: pd.Timestamp, end: pd.Timestamp, open_date: pd.Timestamp) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    path = _find_file(["SSI_PT_CENTRO_ICONIC_EBO_SALES_MASTER.csv", "SSI_PT_CENTRO_ICONIC_SALES_MASTER.csv"])
    if path is None:
        raise FileNotFoundError("Sales master file nahi mili")
    log.info("Sales: %s", path.name)
    df = _read_table(path, ["INVOICE DATE", "INVOICE_DATE", "QTY"])

    c_date = _pick(df, ["Invoice Date", "Invoice_Date", "INVOICE_DATE", "ENTRY_DATE"], label="sales date")
    c_site = _pick(df, ["Site", "Site Name", "TRANSACTION_SITE", "SITE NAME"], label="sales site")
    c_scode = _pick(df, ["Site Code", "SITE CODE"], False)
    c_bc = _pick(df, ["Barcode", "BARCODE"], label="sales barcode")
    c_qty = _pick(df, ["Qty", "QUANTITY", "QTY"], label="sales qty")
    c_rsp = _pick(df, ["RSP", "MSRP", "MRP"], label="sales RSP")
    c_net = _pick(df, ["Item Net Amt", "Item_Net_Amt", "NET_AMOUNT"], label="sales net amount")

    df["date"] = _to_date(df[c_date])
    df["store"] = _store_key(df[c_site], df[c_scode]) if c_scode else _store_key(df[c_site])
    df["bc"] = _norm_barcode(df[c_bc])
    qty = pd.to_numeric(df[c_qty], errors="coerce").fillna(0.0)          # SIGNED: return = minus
    rsp = pd.to_numeric(df[c_rsp], errors="coerce").fillna(0.0)
    net = pd.to_numeric(df[c_net], errors="coerce").fillna(0.0)

    audit = {"sales_rows_raw": int(len(df))}
    ok = df["date"].notna() & df["store"].notna() & df["bc"].str.fullmatch(r"\d{13}") & (qty != 0)
    audit["sales_rows_dropped_invalid"] = int((~ok).sum())
    df, qty, rsp, net = df[ok], qty[ok], rsp[ok], net[ok]

    # Metadata (barcode / store) before date filter
    c3, c6, c5 = (_pick(df, [n], False) for n in ("Cat3", "Cat6", "Cat5"))
    bc_meta = pd.DataFrame({"barcode": df["bc"],
                            "sku": df[c3].fillna("") if c3 else "",
                            "season": df[c6].fillna("") if c6 else "",
                            "size": df[c5].fillna("") if c5 else ""})
    for col, nm in (("department", "Department"), ("section", "Secion")):
        c = _pick(df, [nm, "Section"], False) if nm == "Secion" else _pick(df, [nm], False)
        bc_meta[col] = df[c].fillna("") if c else ""
    bc_meta = bc_meta.drop_duplicates("barcode")
    c_zone, c_st = _pick(df, ["Zone"], False), _pick(df, ["Site Type"], False)
    store_meta = pd.DataFrame({"store": df["store"],
                               "store_name": df[c_site],
                               "zone": df[c_zone].fillna("") if c_zone else "",
                               "store_type": df[c_st].fillna("") if c_st else ""}).drop_duplicates("store")
    c_ch = _pick(df, ["Channel Name"], False)
    if c_ch:
        store_meta = store_meta.merge(df[["store", c_ch]].drop_duplicates("store").rename(columns={c_ch: "channel"}),
                                      on="store", how="left")
    retail_stores = set(df["store"].unique())

    inwin = (df["date"] >= open_date) & (df["date"] <= end)
    audit["sales_net_qty_total_all_dates"] = float(qty.sum())
    df, qty, rsp, net = df[inwin], qty[inwin], rsp[inwin], net[inwin]

    # Pricing class (line level). Return line ka class bhi usi formula se.
    q_abs = qty.abs()
    gross = rsp * q_abs
    disc_pct = ((gross - net.abs()) / gross.where(gross > 0)).fillna(0.0)
    is_disc = (disc_pct > FULL_PRICE_MAX_DISC).to_numpy()
    q_abs_np = q_abs.to_numpy()
    sale_m = (qty > 0).to_numpy()

    ev = pd.concat([
        _events(df["date"][sale_m], df["store"][sale_m], df["bc"][sale_m], "SALE",
                q_abs_np[sale_m], np.where(is_disc[sale_m], q_abs_np[sale_m], 0.0)),
        _events(df["date"][~sale_m], df["store"][~sale_m], df["bc"][~sale_m], "SALE_RET",
                q_abs_np[~sale_m], np.where(is_disc[~sale_m], q_abs_np[~sale_m], 0.0)),
    ], ignore_index=True)
    audit["sales_gross_qty_in_window"] = float(ev.loc[ev["type"] == "SALE", "qty"].sum())
    audit["sales_return_qty_in_window"] = float(ev.loc[ev["type"] == "SALE_RET", "qty"].sum())
    audit["sales_discount_share_pct"] = round(
        float(ev.loc[ev["type"] == "SALE", "dq"].sum() / max(audit["sales_gross_qty_in_window"], 1) * 100), 2)
    log.info("Sales events: %s rows | gross %s | return %s", f"{len(ev):,}",
             f"{audit['sales_gross_qty_in_window']:,.0f}", f"{audit['sales_return_qty_in_window']:,.0f}")
    return ev, bc_meta, store_meta, {**audit, "_retail_stores": retail_stores}


def load_ibt(open_date: pd.Timestamp, end: pd.Timestamp) -> Tuple[pd.DataFrame, dict]:
    path = _find_file(["IBT_DATA.csv", "IBT_DATA.xlsx"])
    if path is None:
        log.warning("IBT file nahi mili - IBT zero")
        return _empty_events(), {}
    df = _read_table(path, ["DOCDT"])
    df["date"] = _to_date(df[_pick(df, ["DOCDT"], label="IBT date")])
    df["bc"] = _norm_barcode(df[_pick(df, ["Barcode", "BARCODE"], label="IBT barcode")])
    df["qty"] = pd.to_numeric(df[_pick(df, ["STFQTY", "QTY"], label="IBT qty")], errors="coerce").fillna(0.0).abs()
    out_c, in_c = _pick(df, ["OUTSITE"], label="IBT OUTSITE"), _pick(df, ["INSITE"], label="IBT INSITE")
    audit = {"ibt_rows_raw": int(len(df))}
    # Total / blank rows (date null) automatically hat jaati hain
    df = df.dropna(subset=["date"])
    df = df[(df["date"] >= open_date) & (df["date"] <= end) & df["bc"].str.fullmatch(r"\d{13}") & (df["qty"] > 0)]
    df["out"] = _store_key(df[out_c])
    df["in"] = _store_key(df[in_c])
    df = df[df["out"].notna() & df["in"].notna() & (df["out"] != df["in"])]
    ev = pd.concat([
        _events(df["date"], df["in"], df["bc"], "IBT_IN", df["qty"]),
        _events(df["date"], df["out"], df["bc"], "IBT_OUT", df["qty"]),
    ], ignore_index=True)
    audit["ibt_qty_total"] = float(df["qty"].sum())
    log.info("IBT: %s transfer rows (qty %s)", f"{len(df):,}", f"{df['qty'].sum():,.0f}")
    return ev, audit


def load_rtv(open_date: pd.Timestamp, end: pd.Timestamp) -> Tuple[pd.DataFrame, dict]:
    path = _find_file(["RTV_DATA.csv", "RTV_DATA.xlsx"])
    if path is None:
        log.warning("RTV file nahi mili - RTV zero")
        return _empty_events(), {}
    df = _read_table(path, ["RETURN_QUANTITY", "BARCODE"])
    # Date column ko fallback ke saath resolve karo; na mile to error (silent zero nahi)
    c_date = _pick(df, ["SALES RETURN DATE", "DOCUMENT_DATE", "DOCUMENT DATE", "RETURN DATE"], label="RTV date")
    df["date"] = _to_date(df[c_date])
    df["bc"] = _norm_barcode(df[_pick(df, ["BARCODE", "Barcode"], label="RTV barcode")])
    df["qty"] = pd.to_numeric(df[_pick(df, ["RETURN_QUANTITY", "QTY"], label="RTV qty")], errors="coerce").fillna(0.0).abs()
    c_site = _pick(df, ["CUSTOMER SITE NAME", "CUSTOMER NAME"], label="RTV store")
    audit = {"rtv_rows_raw": int(len(df)), "rtv_date_column": c_date}
    df = df.dropna(subset=["date"])
    df = df[(df["date"] >= open_date) & (df["date"] <= end) & df["bc"].str.fullmatch(r"\d{13}") & (df["qty"] > 0)]
    df["store"] = _store_key(df[c_site])
    df = df[df["store"].notna()]
    ev = _events(df["date"], df["store"], df["bc"], "RTV_OUT", df["qty"])
    audit["rtv_qty_13digit"] = float(df["qty"].sum())
    log.info("RTV: %s rows (qty %s)", f"{len(df):,}", f"{df['qty'].sum():,.0f}")
    return ev, audit


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------
def pull_forward_inflows(A: pd.DataFrame) -> pd.DataFrame:
    """
    A: long table [store, bc, date, type, qty, dq] (already aggregated per day/type).
    Jis store-barcode par stock zero se neeche jaata hai, wahan aage ke inflow (dispatch / IBT in)
    ko minimum zaroori hadd tak us din par kheench leta hai. Outflow kabhi change nahi hota.
    Adds columns: date0 (original date), pulled (qty jo aage se kheencha gaya).
    """
    A = A.sort_values(["store", "bc", "date"], kind="stable").reset_index(drop=True)
    A["mv"] = A["qty"] * A["type"].map(SIGN)
    A["cum"] = A.groupby(["store", "bc"], sort=False)["mv"].cumsum()
    cand = A.loc[A["cum"] < 0, ["store", "bc"]].drop_duplicates()
    cand_set = set(map(tuple, cand.to_numpy()))
    log.info("Pull-forward candidates (store x barcode going negative): %s", f"{len(cand_set):,}")

    dates = A["date"].to_numpy(dtype="datetime64[ns]").copy()
    orig = dates.copy()
    qtys = A["qty"].to_numpy()
    types = A["type"].to_numpy()
    is_in = np.isin(types, INFLOW)
    groups = A.groupby(["store", "bc"], sort=False).indices

    for key in cand_set:
        idx = groups[key]                    # already date sorted
        out_by: Dict[np.datetime64, float] = {}
        pend: List[int] = []
        for k in idx:
            if is_in[k]:
                pend.append(k)
            else:
                out_by[dates[k]] = out_by.get(dates[k], 0.0) - SIGN[types[k]] * qtys[k]
        stock = 0.0
        for d in sorted({dates[k] for k in idx}):
            while pend and dates[pend[0]] <= d:
                stock += qtys[pend.pop(0)]
            stock -= out_by.get(d, 0.0)
            while stock < 0 and pend:
                k = pend.pop(0)
                dates[k] = d
                stock += qtys[k]

    A["date0"] = orig
    A["date"] = dates
    A["pulled"] = np.where(A["date"] != A["date0"], A["qty"], 0.0)
    log.info("Inflow qty pulled forward to sale date: %s", f"{A['pulled'].sum():,.0f}")
    return A.drop(columns=["mv", "cum"])


def build_ledger(events: pd.DataFrame) -> Tuple[pd.DataFrame, dict]:
    audit: dict = {}

    # ---- cohort: arrivals (DISPATCH / IBT in) open the pair; then ALL sales/RTV/out count.
    # Not "only sales up to dispatch qty". Sale-only (no arrival/RTV) does not open cohort.
    disp_pairs = events.loc[events["type"] == "DISP_IN", ["store", "bc"]].drop_duplicates()
    ibt_pairs = events.loc[events["type"] == "IBT_IN", ["store", "bc"]].drop_duplicates()
    rtv_pairs = events.loc[events["type"] == "RTV_OUT", ["store", "bc"]].drop_duplicates()
    inflow_pairs = pd.concat([disp_pairs, ibt_pairs]).drop_duplicates()
    pairs = pd.concat([inflow_pairs, rtv_pairs]).drop_duplicates()
    pairs["_in"] = 1
    audit["cohort_pairs_dispatch"] = int(len(disp_pairs))
    audit["cohort_pairs_ibt_in"] = int(len(ibt_pairs))
    audit["cohort_pairs_ibt_only"] = int(
        len(ibt_pairs.merge(disp_pairs, on=["store", "bc"], how="left", indicator=True)
            .query('_merge == "left_only"'))
    )
    audit["cohort_pairs_rtv_added"] = int(len(rtv_pairs))
    audit["cohort_pairs_rtv_only"] = int(len(pairs) - len(inflow_pairs))
    # Sales on arrival/RTV pairs are kept in full (no qty cap vs dispatch).
    sale_on = events.loc[events["type"].isin(["SALE", "SALE_RET"])].merge(
        pairs[["store", "bc"]], on=["store", "bc"], how="inner"
    )
    audit["cohort_sales_qty_monitored"] = float(sale_on["qty"].sum()) if len(sale_on) else 0.0

    ev = events.merge(pairs, on=["store", "bc"], how="left")
    excluded = ev[ev["_in"].isna()]
    audit["excluded_out_of_cohort_qty"] = {k: float(v) for k, v in excluded.groupby("type")["qty"].sum().items()}
    ev = ev[ev["_in"] == 1].drop(columns="_in")

    A = ev.groupby(["store", "bc", "date", "type"], as_index=False)[["qty", "dq"]].sum()
    A = pull_forward_inflows(A)
    audit["inflow_pulled_forward_qty"] = float(A["pulled"].sum())

    types = ["DISP_IN", "IBT_IN", "IBT_OUT", "RTV_OUT", "SALE", "SALE_RET"]
    keys = ["store", "bc", "date"]
    P = A.pivot_table(index=keys, columns="type", values="qty", aggfunc="sum", fill_value=0.0).reindex(columns=types, fill_value=0.0)
    Q = (A[A["type"].isin(["SALE", "SALE_RET"])]
         .pivot_table(index=keys, columns="type", values="dq", aggfunc="sum", fill_value=0.0)
         .reindex(columns=["SALE", "SALE_RET"], fill_value=0.0)
         .rename(columns={"SALE": "SALE_DQ", "SALE_RET": "RET_DQ"}))
    P = P.join(Q, how="left").join(A.groupby(keys)["pulled"].sum()).fillna(0.0).reset_index()

    d = pd.DataFrame({
        "date": P["date"], "store": P["store"], "barcode": P["bc"],
        "dispatch_in": P["DISP_IN"], "ibt_in": P["IBT_IN"], "ibt_out": P["IBT_OUT"], "rtv_out": P["RTV_OUT"],
        "gross_sales_qty": P["SALE"], "sales_return_qty": P["SALE_RET"],
        "inflow_pulled_qty": P["pulled"],
    })
    d["sales_qty"] = d["gross_sales_qty"] - d["sales_return_qty"]                 # NET sale
    d["discount_sales_qty"] = P["SALE_DQ"] - P["RET_DQ"]                           # net of returns
    d["full_price_sales_qty"] = d["sales_qty"] - d["discount_sales_qty"]

    d = d.sort_values(["store", "barcode", "date"]).reset_index(drop=True)
    d["net_movement"] = (d["dispatch_in"] + d["ibt_in"] - d["ibt_out"] - d["rtv_out"] - d["sales_qty"])
    g = d.groupby(["store", "barcode"], sort=False)
    d["closing_soh"] = g["net_movement"].cumsum()
    d["opening_soh"] = d["closing_soh"] - d["net_movement"]                         # first inflow row: 0
    # Available for STR = Opening + Disp In + IBT In + Sales Return (IBT/RTV out alag outflow)
    d["available_stock"] = (
        d["opening_soh"] + d["dispatch_in"] + d["ibt_in"] + d["sales_return_qty"]
    )

    # cumulative sell-through — denom is stock base, NOT net of IBT/RTV out
    # lifetime open=0 ⇒ cum_available = cum(disp + ibt_in + sales_return)
    d["stock_in"] = d["dispatch_in"] + d["ibt_in"] + d["sales_return_qty"]
    d["net_received"] = d["stock_in"]  # alias kept for parquet / API consumers (now = available inflows)
    for src, dst in (("net_received", "cum_received"), ("sales_qty", "cum_sales"),
                     ("full_price_sales_qty", "cum_full_price_sales"), ("discount_sales_qty", "cum_discount_sales"),
                     ("ibt_out", "cum_ibt_out"), ("rtv_out", "cum_rtv_out")):
        d[dst] = g[src].cumsum()
    den = d["cum_received"].where(d["cum_received"] > 0)
    d["sales_through_pct"] = (d["cum_sales"] / den * 100).round(4).fillna(0.0)
    d["full_price_sales_through_pct"] = (d["cum_full_price_sales"] / den * 100).round(4).fillna(0.0)
    d["discount_sales_through_pct"] = (d["cum_discount_sales"] / den * 100).round(4).fillna(0.0)

    # first sale date (sell-through "pehli sale se" ke liye)
    first_sale = d[d["sales_qty"] != 0].groupby(["store", "barcode"])["date"].min().rename("first_sale_date")
    d = d.merge(first_sale, on=["store", "barcode"], how="left")
    d["days_since_first_sale"] = (d["date"] - d["first_sale_date"]).dt.days
    d["is_selling"] = (d["date"] >= d["first_sale_date"]).astype(int)

    d["negative_soh_flag"] = (d["closing_soh"] < 0).astype(int)
    d["soh_recon_diff"] = (d["opening_soh"] + d["dispatch_in"] + d["ibt_in"] - d["ibt_out"] - d["rtv_out"]
                           - d["sales_qty"] - d["closing_soh"]).round(6)
    return d, audit


def add_dimensions(d: pd.DataFrame, bc_meta: pd.DataFrame, store_meta: pd.DataFrame) -> pd.DataFrame:
    d = d.copy()
    iso = d["date"].dt.isocalendar()
    d["week_number"] = iso["week"].astype(int)
    d["week_year"] = iso["year"].astype(int)
    d["week_start"] = d["date"] - pd.to_timedelta(d["date"].dt.dayofweek, unit="D")
    d["week_ending"] = d["week_start"] + pd.Timedelta(days=6)
    d["month"] = d["date"].dt.month
    d["month_year"] = d["date"].dt.to_period("M").astype(str)
    d["quarter"] = "Q" + d["date"].dt.quarter.astype(str)
    d["year"] = d["date"].dt.year
    fy = d["date"].dt.year + (d["date"].dt.month >= 4).astype(int)
    d["financial_year"] = "FY" + ((fy - 1) % 100).astype(str).str.zfill(2) + "-" + (fy % 100).astype(str).str.zfill(2)
    d["fy_month"] = ((d["month"] - 4) % 12) + 1

    if not bc_meta.empty:
        d = d.merge(bc_meta.drop_duplicates("barcode"), on="barcode", how="left")
    if not store_meta.empty:
        d = d.merge(store_meta.drop_duplicates("store"), on="store", how="left")
    for col in ("sku", "season", "size", "department", "section", "zone", "store_type", "store_name", "channel"):
        d[col] = d[col].fillna("") if col in d.columns else ""

    # Derive proper sales channel (PT / SSL / CENTRO / ICONIC / EBO / Reliance / EBO-Other)
    # from store_name and channel (company name) — raw Site Type is not reliable
    def _derive_channel(store_name: pd.Series, channel: pd.Series, store_code: pd.Series) -> pd.Series:
        sn = store_name.astype(str).str.upper().str.strip()
        ch = channel.astype(str).str.upper().str.strip()
        sc = store_code.astype(str).str.upper().str.strip()

        result = pd.Series("EBO", index=store_name.index, dtype=object)

        # PT — Pantaloons: store name starts with "PT-" or store code starts with "P" + digits
        is_pt = (
            sn.str.startswith("PT-") |
            sn.str.startswith("PT -") |
            sc.str.match(r"^[PW]\d{2,4}") |
            ch.str.contains("ADITYA BIRLA|ABFRL|PRFL|PANTALOON", regex=True, na=False)
        )
        result[is_pt] = "PT"

        # CENTRO
        is_centro = (
            sn.str.contains("CENTRO", na=False) |
            ch.str.contains("CENTRO", na=False)
        )
        result[is_centro] = "CENTRO"

        # ICONIC
        is_iconic = (
            sn.str.contains("ICONIC", na=False) |
            ch.str.contains("ICONIC FASHION", na=False)
        )
        result[is_iconic] = "ICONIC"

        # Reliance (Fashion Factory / Reliance Trends)
        is_reliance = (
            sn.str.contains("RELIANCE|FASHION FACTORY|FF-", regex=True, na=False) |
            sc.str.match(r"^F1") |
            ch.str.contains("RELIANCE RETAIL", na=False)
        )
        result[is_reliance] = "Reliance"

        # SSL — Shoppers Stop: name contains keywords, starts with "SS-", or numeric 3-digit codes
        # Priority 1: SSL overrides PT if there's a conflict
        is_ssl = (
            sn.str.contains("SHOPPER|SHOPER|HOPPER", regex=True, na=False) |
            sn.str.match(r"^SS[\s-]", na=False) |
            ch.str.contains("SHOPPER|HOPPER", regex=True, na=False) |
            (sc.str.match(r"^\d{3}$", na=False) & ch.str.contains("SHOPPER|HOPPER", regex=True, na=False))
        )
        result[is_ssl] = "SSL"

        return result

    d["sales_channel"] = _derive_channel(d["store_name"], d["channel"], d["store"])


    alias = {
        "DATE": "date", "FY": "financial_year", "WEEK": "week_number", "MONTH": "month_year", "QUARTER": "quarter",
        "STORE": "store", "BARCODE": "barcode", "SKU": "sku", "SEASON": "season", "ZONE": "zone",
        "STORE_TYPE": "store_type", "DEPARTMENT": "department", "SECTION": "section",
        "OPENING_SOH_QTY": "opening_soh", "DISPATCH_IN_QTY": "dispatch_in", "IBT_IN_QTY": "ibt_in",
        "GROSS_SALES_QTY": "gross_sales_qty", "SALES_RETURN_QTY": "sales_return_qty",
        "SALES_QTY": "sales_qty", "FULL_PRICE_SALES_QTY": "full_price_sales_qty",
        "DISCOUNT_SALES_QTY": "discount_sales_qty", "IBT_OUT_QTY": "ibt_out", "RTV_OUT_QTY": "rtv_out",
        "CLOSING_SOH_QTY": "closing_soh", "AVAILABLE_STOCK_QTY": "available_stock",
        "SALES_THROUGH_PCT": "sales_through_pct", "FULL_PRICE_SALES_THROUGH_PCT": "full_price_sales_through_pct",
        "DISCOUNT_SALES_THROUGH_PCT": "discount_sales_through_pct",
        "SOH_NEGATIVE_FLAG": "negative_soh_flag", "SOH_RECON_DIFF": "soh_recon_diff",
    }
    return pd.concat([d, pd.DataFrame({k: d[v] for k, v in alias.items()})], axis=1)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def build(*, start: str = START_DATE, open_date: str = STORE_OPEN_DATE, end: Optional[str] = None,
          retail_only: bool = True, drop_duplicates: bool = False) -> dict:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    start_ts, open_ts = pd.Timestamp(start).normalize(), pd.Timestamp(open_date).normalize()
    end_ts = pd.Timestamp(end or datetime.today().strftime("%Y-%m-%d")).normalize()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    log.info("=== START | dispatch from %s | store arrival %s | end %s ===", start_ts.date(), open_ts.date(), end_ts.date())

    disp, disp_meta, a_disp = load_dispatch(start_ts, end_ts, open_ts, drop_duplicates)
    sales, bc_meta, store_meta, a_sales = load_sales(start_ts, end_ts, open_ts)
    ibt, a_ibt = load_ibt(open_ts, end_ts)
    rtv, a_rtv = load_rtv(open_ts, end_ts)
    retail_stores = a_sales.pop("_retail_stores")

    audit = {**a_disp, **a_sales, **a_ibt, **a_rtv}
    parts = [disp, sales, ibt, rtv]
    if retail_only:
        # Dispatch / IBT / RTV sirf unhi stores ke jo sales file mein hain (wholesale customers bahar)
        for name, p in (("dispatch", disp), ("ibt", ibt), ("rtv", rtv)):
            if not p.empty:
                audit[f"{name}_qty_non_retail_store_excluded"] = float(p.loc[~p["store"].isin(retail_stores), "qty"].sum())
        parts = [p[p["store"].isin(retail_stores)] if not p.empty else p for p in parts]
    events = pd.concat([p for p in parts if not p.empty], ignore_index=True)

    # Barcode metadata: dispatch + sales (dispatch ka season/sku/size pehle)
    meta_all = pd.concat([m for m in (disp_meta, bc_meta) if not m.empty], ignore_index=True).drop_duplicates("barcode")

    ledger, a_led = build_ledger(events)
    audit.update(a_led)
    ledger = add_dimensions(ledger, meta_all, store_meta)

    ledger.to_parquet(CACHE_DIR / "daily_inventory_movement.parquet", index=False, engine="pyarrow")

    # Exceptions: final negative SOH
    fin = (ledger.groupby(["store", "barcode"])
           .agg(closing_soh=("closing_soh", "last"), dispatch_in=("dispatch_in", "sum"), ibt_in=("ibt_in", "sum"),
                ibt_out=("ibt_out", "sum"), rtv_out=("rtv_out", "sum"), net_sales=("sales_qty", "sum"),
                season=("season", "first"), store_name=("store_name", "first")).reset_index())
    exc = fin[fin["closing_soh"] < 0].sort_values("closing_soh")
    exc.to_csv(CACHE_DIR / "daily_inventory_exceptions.csv", index=False)

    tot_recv = float(ledger["net_received"].sum())
    summary = {
        "start_date": str(start_ts.date()), "store_arrival_date": str(open_ts.date()), "end_date": str(end_ts.date()),
        "total_rows": int(len(ledger)), "stores": int(ledger["store"].nunique()),
        "barcodes": int(ledger["barcode"].nunique()), "pairs": int(len(fin)),
        "date_min": str(ledger["date"].min().date()), "date_max": str(ledger["date"].max().date()),
        "total_dispatch_in": float(ledger["dispatch_in"].sum()), "total_ibt_in": float(ledger["ibt_in"].sum()),
        "total_ibt_out": float(ledger["ibt_out"].sum()), "total_rtv_out": float(ledger["rtv_out"].sum()),
        "total_gross_sales_qty": float(ledger["gross_sales_qty"].sum()),
        "total_sales_return_qty": float(ledger["sales_return_qty"].sum()),
        "total_net_sales_qty": float(ledger["sales_qty"].sum()),
        "total_full_price_sales_qty": float(ledger["full_price_sales_qty"].sum()),
        "total_discount_sales_qty": float(ledger["discount_sales_qty"].sum()),
        "closing_soh_net": float(fin["closing_soh"].sum()),
        "closing_soh_positive": float(fin.loc[fin["closing_soh"] > 0, "closing_soh"].sum()),
        "closing_soh_negative": float(fin.loc[fin["closing_soh"] < 0, "closing_soh"].sum()),
        "negative_pairs_final": int((fin["closing_soh"] < 0).sum()),
        "negative_rows": int(ledger["negative_soh_flag"].sum()),
        "overall_sell_through_pct": round(float(ledger["sales_qty"].sum()) / tot_recv * 100, 2) if tot_recv else 0.0,
        "full_price_sell_through_pct": round(float(ledger["full_price_sales_qty"].sum()) / tot_recv * 100, 2) if tot_recv else 0.0,
        "discount_sell_through_pct": round(float(ledger["discount_sales_qty"].sum()) / tot_recv * 100, 2) if tot_recv else 0.0,
        "audit": audit,
        "built_at": datetime.now(timezone.utc).isoformat(),
    }
    (CACHE_DIR / "daily_inventory_movement.meta.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")

    log.info("=== DONE | rows %s | stores %s | barcodes %s ===", f"{summary['total_rows']:,}", summary["stores"], summary["barcodes"])
    log.info("Net sale %s | received %s | ST %s%% (FP %s%% / Disc %s%%)", f"{summary['total_net_sales_qty']:,.0f}",
             f"{tot_recv:,.0f}", summary["overall_sell_through_pct"], summary["full_price_sell_through_pct"],
             summary["discount_sell_through_pct"])
    log.info("Closing SOH net %s (positive %s / negative %s in %s pairs) -> exceptions file",
             f"{summary['closing_soh_net']:,.0f}", f"{summary['closing_soh_positive']:,.0f}",
             f"{summary['closing_soh_negative']:,.0f}", f"{summary['negative_pairs_final']:,}")
    return summary


def main() -> None:
    global INPUT_DIR, CACHE_DIR
    p = argparse.ArgumentParser(description="Daily inventory movement + SOH + sell-through (corrected)")
    p.add_argument("--start", default=START_DATE, help="First dispatch date to monitor (default 2025-03-27)")
    p.add_argument("--open-date", default=STORE_OPEN_DATE, help="Arrival date for dispatch before this date (default 2025-04-01)")
    p.add_argument("--end", default=None, help="End date YYYY-MM-DD (default today)")
    p.add_argument("--input-dir", default=None)
    p.add_argument("--cache-dir", default=None)
    p.add_argument("--all-stores", action="store_true", help="Retail-only filter band karo (wholesale customers bhi aayenge)")
    p.add_argument("--drop-dup-dispatch", action="store_true", help="Dispatch ki bilkul same rows hatao")
    a = p.parse_args()
    if a.input_dir:
        INPUT_DIR = Path(a.input_dir)
    if a.cache_dir:
        CACHE_DIR = Path(a.cache_dir)
    build(start=a.start, open_date=a.open_date, end=a.end, retail_only=not a.all_stores,
          drop_duplicates=a.drop_dup_dispatch)


if __name__ == "__main__":
    main()
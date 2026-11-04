"""
Load dashboard filter source files into Azure SQL (Phase 1).

Sources (local pipeline/input_files via existing loaders):
  - Sales Master      → stockpulse.sales_fact
  - Item Master       → stockpulse.item_master
  - Customer Master   → stockpulse.store_master
  - Store Grade       → stockpulse.store_grade (+ store_master.store_grade)
  - COMBINED_SOH      → stockpulse.soh_fact
  - TARGET_LFS        → stockpulse.target_lfs

Usage (from backend root, .env already has AZURE_SQL_*):
  python scripts/load_filter_sources_to_azure_sql.py
  python scripts/load_filter_sources_to_azure_sql.py --only sales,item,store,soh,target,grade
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

INPUT_DIR = BACKEND_ROOT / "pipeline" / "input_files"
BATCH = 8000


def _load_dotenv() -> None:
    env_path = BACKEND_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def _clean_str(val: Any) -> str:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    s = str(val).strip()
    if s.lower() in {"nan", "none", "<na>", "null"}:
        return ""
    return re.sub(r"\.0$", "", s)


def _fy_token(ts: pd.Timestamp) -> str:
    y = int(ts.year)
    end = y + 1 if int(ts.month) >= 4 else y
    return f"FY{str(end)[-2:]}"


def _conn():
    from api.azure_sql import azure_sql_connection

    return azure_sql_connection()


def _bulk_conn():
    """Long-lived connection with autocommit for bulk loads (avoids huge txn + reconnect cost)."""
    import pyodbc
    from api.azure_sql import connection_string

    return pyodbc.connect(connection_string(), autocommit=True)


def _apply_sql_file(path: Path) -> None:
    import pyodbc
    from api.azure_sql import connection_string

    text = path.read_text(encoding="utf-8")
    batches = [b.strip() for b in re.split(r"(?im)^\s*GO\s*$", text) if b.strip()]
    conn = pyodbc.connect(connection_string(), autocommit=True)
    try:
        cur = conn.cursor()
        for b in batches:
            cur.execute(b)
    finally:
        conn.close()


def _truncate(table: str, conn=None) -> None:
    if conn is None:
        with _conn() as c:
            c.cursor().execute(f"TRUNCATE TABLE {table}")
        return
    conn.cursor().execute(f"TRUNCATE TABLE {table}")


def _executemany(sql: str, rows: list[tuple], conn=None) -> Any:
    """Execute batch. If conn is a list [connection], reconnects in-place on link failure."""
    if not rows:
        return conn

    def _run(c):
        cur = c.cursor()
        cur.fast_executemany = True
        cur.executemany(sql, rows)

    if conn is None:
        with _conn() as c:
            _run(c)
        return None

    # Mutable holder: [pyodbc.Connection]
    holder = conn if isinstance(conn, list) else None
    c = conn[0] if holder is not None else conn
    try:
        _run(c)
        return holder if holder is not None else c
    except Exception as exc:
        msg = str(exc).lower()
        if "08s01" not in msg and "communication link" not in msg and "10054" not in msg:
            raise
        print(f"  reconnect after link failure: {exc}", flush=True)
        try:
            c.close()
        except Exception:
            pass
        c2 = _bulk_conn()
        if holder is not None:
            holder[0] = c2
        _run(c2)
        return holder if holder is not None else c2


def _batched(rows: Iterable[tuple], size: int = BATCH) -> Iterable[list[tuple]]:
    buf: list[tuple] = []
    for r in rows:
        buf.append(r)
        if len(buf) >= size:
            yield buf
            buf = []
    if buf:
        yield buf


def _set_watermark(name: str, rows: int, status: str, message: str = "", conn=None) -> None:
    sql = """
    MERGE stockpulse.etl_watermark AS t
    USING (SELECT ? AS pipeline_name) AS s
      ON t.pipeline_name = s.pipeline_name
    WHEN MATCHED THEN UPDATE SET
      last_success_at = SYSUTCDATETIME(),
      last_row_count = ?,
      last_status = ?,
      last_message = ?
    WHEN NOT MATCHED THEN INSERT
      (pipeline_name, last_success_at, last_row_count, last_status, last_message)
      VALUES (?, SYSUTCDATETIME(), ?, ?, ?);
    """
    params = (name, rows, status, message[:900], name, rows, status, message[:900])
    if conn is None:
        with _conn() as c:
            c.cursor().execute(sql, params)
        return
    conn.cursor().execute(sql, params)


def load_store_grade() -> int:
    from pipeline.store_grade import _find_store_grade_path, _parse_store_grade_df, _read_store_grade

    path = _find_store_grade_path()
    if path is None:
        print("store_grade: SKIP (file missing)")
        return 0
    raw = _read_store_grade(path)
    df = _parse_store_grade_df(raw)
    if df.empty:
        print("store_grade: SKIP (empty)")
        return 0
    _truncate("stockpulse.store_grade")
    insert = """
    INSERT INTO stockpulse.store_grade
      (site_id, store_grade, zone, site_name, store_category)
    VALUES (?, ?, ?, ?, ?)
    """
    rows = []
    for r in df.itertuples(index=False):
        site = _clean_str(getattr(r, "site_code", ""))
        if not site:
            continue
        rows.append(
            (
                site[:64],
                _clean_str(getattr(r, "store_grade", ""))[:32] or None,
                _clean_str(getattr(r, "grade_zone", ""))[:64] or None,
                _clean_str(getattr(r, "grade_site_name", ""))[:256] or None,
                None,
            )
        )
    n = 0
    for batch in _batched(rows):
        _executemany(insert, batch)
        n += len(batch)
    _set_watermark("store_grade", n, "ok", str(path.name))
    print(f"store_grade: {n} rows from {path.name}")
    return n


def load_stores() -> int:
    from pipeline.customer_master import load_customer_master
    from pipeline.store_grade import load_store_grade_map

    cm = load_customer_master(retail_only=False)
    grade_map = load_store_grade_map()
    if cm is None or cm.empty:
        print("store_master: SKIP (empty)")
        return 0
    _truncate("stockpulse.store_master")
    insert = """
    INSERT INTO stockpulse.store_master
      (site_id, site_name, sales_channel, zone, city, state_name,
       store_grade, cluster_name, customer_name, store_sqft, store_category, site_manual_code)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    rows = []
    seen: set[str] = set()
    for r in cm.itertuples(index=False):
        site = _clean_str(getattr(r, "site_manual_code", "") or getattr(r, "site_code", ""))
        if not site:
            site = _clean_str(getattr(r, "site_code", ""))
        if not site or site in seen:
            continue
        seen.add(site)
        grade = grade_map.get(site.upper()) or grade_map.get(site) or _clean_str(getattr(r, "store_grade", ""))
        sqft = getattr(r, "store_sqft", None)
        try:
            sqft_v = float(sqft) if sqft is not None and str(sqft).strip() not in ("", "nan") else None
        except Exception:
            sqft_v = None
        rows.append(
            (
                site[:64],
                _clean_str(getattr(r, "site_name_master", "") or getattr(r, "customer_name", ""))[:256] or None,
                _clean_str(getattr(r, "channel", ""))[:64] or None,
                _clean_str(getattr(r, "zone", ""))[:64] or None,
                _clean_str(getattr(r, "city", ""))[:128] or None,
                _clean_str(getattr(r, "state", ""))[:128] or None,
                _clean_str(grade)[:32] or None,
                _clean_str(getattr(r, "cluster", ""))[:128] or None,
                _clean_str(getattr(r, "customer_name", ""))[:256] or None,
                sqft_v,
                _clean_str(getattr(r, "store_category", ""))[:32] or None,
                _clean_str(getattr(r, "site_manual_code", ""))[:64] or None,
            )
        )
    n = 0
    for batch in _batched(rows):
        _executemany(insert, batch)
        n += len(batch)
    _set_watermark("store_master", n, "ok", "CUSTOMER_MASTER+STORE_GRADE")
    print(f"store_master: {n} rows")
    return n


def load_items() -> int:
    from pipeline.item_master import build_barcode_item_details_map, load_item_master_df
    from pipeline.pipeline_inputs import find_column, normalize_barcode

    details = build_barcode_item_details_map(force_refresh=False)
    item = load_item_master_df()
    season_by_bc: dict[str, str] = {}
    if item is not None and not item.empty:
        try:
            bc = find_column(item, "BARCODE", "Barcode")
            season_col = find_column(item, "SEASON", "Season")
            tmp = item[[bc, season_col]].copy()
            tmp["bk"] = normalize_barcode(tmp[bc])
            tmp["season"] = tmp[season_col].astype(str).str.strip()
            for bk, season in zip(tmp["bk"], tmp["season"]):
                bks = _clean_str(bk)
                if bks and bks not in season_by_bc:
                    s = _clean_str(season)
                    if s:
                        season_by_bc[bks] = s
        except Exception as exc:
            print(f"item season map skip: {exc}")

    if details is None or details.empty:
        print("item_master: SKIP (empty)")
        return 0
    _truncate("stockpulse.item_master")
    insert = """
    INSERT INTO stockpulse.item_master
      (barcode, sku, department, section, season, size_label, color, mrp, style_name)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    rows = []
    for r in details.itertuples(index=False):
        bc = _clean_str(getattr(r, "barcode_key", ""))
        if not bc:
            continue
        mrp = getattr(r, "mrp", None)
        try:
            mrp_v = float(mrp) if mrp is not None and str(mrp).strip() not in ("", "nan") else None
        except Exception:
            mrp_v = None
        rows.append(
            (
                bc[:64],
                _clean_str(getattr(r, "sku", ""))[:64] or None,
                _clean_str(getattr(r, "department", ""))[:128] or None,
                _clean_str(getattr(r, "section", ""))[:128] or None,
                (season_by_bc.get(bc) or None),
                _clean_str(getattr(r, "size", ""))[:64] or None,
                _clean_str(getattr(r, "color", ""))[:64] or None,
                mrp_v,
                _clean_str(getattr(r, "style_name", ""))[:256] or None,
            )
        )
    n = 0
    for batch in _batched(rows):
        _executemany(insert, batch)
        n += len(batch)
    _set_watermark("item_master", n, "ok", "ITEM_MASTER")
    print(f"item_master: {n} rows")
    return n


def load_soh() -> int:
    path = INPUT_DIR / "COMBINED_SOH_MASTER.csv"
    if not path.exists() or path.stat().st_size == 0:
        print("soh_fact: SKIP (COMBINED_SOH_MASTER missing)")
        return 0
    magic = path.read_bytes()[:2]
    if magic == b"PK":
        df = pd.read_excel(path)
    else:
        df = pd.read_csv(path, low_memory=False)
    df.columns = [str(c).strip() for c in df.columns]
    colmap = {c.lower(): c for c in df.columns}
    date_c = colmap.get("date")
    site_c = colmap.get("site code") or colmap.get("site_code") or colmap.get("site")
    ean_c = colmap.get("ean") or colmap.get("barcode") or colmap.get("article ean")
    qty_c = colmap.get("qty") or colmap.get("current stock qty") or colmap.get("current_soh")
    if not all([date_c, site_c, ean_c, qty_c]):
        print(f"soh_fact: SKIP (unexpected cols {list(df.columns)})")
        return 0
    work = pd.DataFrame(
        {
            "as_of_date": pd.to_datetime(df[date_c], errors="coerce"),
            "site_id": df[site_c].map(_clean_str),
            "barcode": df[ean_c].map(_clean_str),
            "qty": pd.to_numeric(df[qty_c], errors="coerce").fillna(0.0),
        }
    )
    work = work.dropna(subset=["as_of_date"])
    work = work[(work["site_id"].str.len() > 0) & (work["barcode"].str.len() > 0)]
    work = (
        work.groupby(["as_of_date", "site_id", "barcode"], as_index=False)["qty"]
        .sum()
    )
    _truncate("stockpulse.soh_fact")
    insert = """
    INSERT INTO stockpulse.soh_fact
      (as_of_date, site_id, barcode, sales_channel, department, current_soh, available_qty)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    """
    n = 0
    for start in range(0, len(work), BATCH):
        chunk = work.iloc[start : start + BATCH]
        rows = [
            (
                r.as_of_date.date() if hasattr(r.as_of_date, "date") else r.as_of_date,
                str(r.site_id)[:64],
                str(r.barcode)[:64],
                None,
                None,
                float(r.qty),
                float(r.qty),
            )
            for r in chunk.itertuples(index=False)
        ]
        if rows:
            _executemany(insert, rows)
            n += len(rows)
        if start and start % 50000 == 0:
            print(f"  soh_fact progress {start}/{len(work)}")
    _set_watermark("soh_fact", n, "ok", path.name)
    print(f"soh_fact: {n} rows from {path.name}")
    return n


def load_targets() -> int:
    path = INPUT_DIR / "TARGET_LFS.csv"
    if not path.exists():
        print("target_lfs: SKIP (missing)")
        return 0
    insert = """
    INSERT INTO stockpulse.target_lfs
      (target_date, site_id, store_name, format_name, target_amount)
    VALUES (?, ?, ?, ?, ?)
    """
    n = 0
    t0 = time.time()
    conn = _bulk_conn()
    try:
        _truncate("stockpulse.target_lfs", conn)
        for chunk in pd.read_csv(path, chunksize=BATCH, low_memory=False):
            chunk.columns = [str(c).strip() for c in chunk.columns]
            colmap = {c.lower(): c for c in chunk.columns}
            date_c = colmap.get("date")
            site_c = colmap.get("site code") or colmap.get("site_code")
            name_c = colmap.get("store name") or colmap.get("store_name")
            fmt_c = colmap.get("format")
            tgt_c = colmap.get("target")
            work = pd.DataFrame(
                {
                    "target_date": pd.to_datetime(chunk[date_c], errors="coerce"),
                    "site_id": chunk[site_c].map(_clean_str),
                    "store_name": chunk[name_c].map(_clean_str) if name_c else "",
                    "format_name": chunk[fmt_c].map(_clean_str) if fmt_c else "",
                    "target_amount": pd.to_numeric(chunk[tgt_c], errors="coerce").fillna(0.0),
                }
            )
            work = work.dropna(subset=["target_date"])
            work = work[work["site_id"].str.len() > 0]
            rows = [
                (
                    r.target_date.date(),
                    str(r.site_id)[:64],
                    (str(r.store_name)[:256] or None),
                    (str(r.format_name)[:64] or None),
                    float(r.target_amount),
                )
                for r in work.itertuples(index=False)
            ]
            if rows:
                _executemany(insert, rows, conn)
                n += len(rows)
            if n and n % 50000 < BATCH:
                print(f"  target_lfs progress {n} ({time.time() - t0:.0f}s)", flush=True)
        _set_watermark("target_lfs", n, "ok", path.name, conn)
    finally:
        conn.close()
    print(f"target_lfs: {n} rows in {time.time() - t0:.0f}s", flush=True)
    return n


_PREPARED_COLS = [
    "date", "site_id", "barcode", "sku", "sales_channel", "channel", "store_type", "zone",
    "department", "section", "season", "qty", "net_sale_amount", "gross_amount",
    "site_name", "site_code", "site_manual_code", "site_short_code", "site_id_name",
    "customer_name", "store_grade", "city", "state", "cluster",
    "mrp", "mrp_gross_val", "discount_amount", "discount_pct", "tax_amount",
    "size", "store_category", "store_sqft", "mdq",
]


def _prepared_sales_frames() -> Iterable[tuple[int, pd.DataFrame]]:
    """Yield (FY, prepared sales frame) — the same Customer-Master-enriched data the
    dashboard's DuckDB / pandas paths read, so SQL totals match them exactly."""
    from api.services.analytics_query_engine import resolve_fy_parquet_paths
    from api.sales_year_loader import load_sales_for_year, supported_dashboard_years

    for y in sorted(supported_dashboard_years()):
        df = None
        for p in resolve_fy_parquet_paths(y):
            try:
                import pyarrow.parquet as pq

                have = set(pq.read_schema(p).names)
                df = pd.read_parquet(p, columns=[c for c in _PREPARED_COLS if c in have])
                print(f"sales_fact: FY{y} parquet {p.name} rows={len(df):,}", flush=True)
            except Exception as exc:
                print(f"sales_fact: FY{y} parquet unreadable ({exc}); rebuilding", flush=True)
                df = None
        if df is None:
            try:
                df = load_sales_for_year(y)
                print(f"sales_fact: FY{y} built via load_sales_for_year rows={len(df):,}", flush=True)
            except Exception as exc:
                print(f"sales_fact: FY{y} SKIP ({exc})", flush=True)
                continue
        if df is not None and not df.empty:
            yield y, df


def _site_key(series: pd.Series, pattern: str) -> pd.Series:
    """Same extraction as sales_dashboard._site_identity_mask (on upper-cased site_id)."""
    return series.astype(str).str.upper().str.extract(pattern, expand=False).fillna("")


_SALES_LOAD_COLS = """sale_date, site_id, barcode, sku, sales_channel, zone,
       department, section, season, qty, net_sale_amount, gross_sale_amount, financial_year,
       site_name, site_code, site_manual_code, site_short_code, site_id_name,
       site_key_paren, site_key_dash, customer_name, channel_master, store_type,
       store_grade, city, state_name, cluster_name,
       mrp, mrp_gross_val, discount_amount, discount_pct, tax_amount,
       size_label, store_category, store_sqft, mdq"""

_STAGE_DDL = """
IF OBJECT_ID('stockpulse.sales_fact_stage', 'U') IS NULL
CREATE TABLE stockpulse.sales_fact_stage (
    sale_date DATE NOT NULL, site_id NVARCHAR(64) NOT NULL, barcode NVARCHAR(64) NOT NULL,
    sku NVARCHAR(64) NULL, sales_channel NVARCHAR(64) NULL, zone NVARCHAR(64) NULL,
    department NVARCHAR(128) NULL, section NVARCHAR(128) NULL, season NVARCHAR(64) NULL,
    qty DECIMAL(18, 4) NOT NULL, net_sale_amount DECIMAL(18, 2) NOT NULL,
    gross_sale_amount DECIMAL(18, 2) NULL, financial_year NVARCHAR(16) NULL,
    site_name NVARCHAR(256) NULL, site_code NVARCHAR(64) NULL, site_manual_code NVARCHAR(64) NULL,
    site_short_code NVARCHAR(64) NULL, site_id_name NVARCHAR(400) NULL,
    site_key_paren NVARCHAR(64) NULL, site_key_dash NVARCHAR(64) NULL,
    customer_name NVARCHAR(256) NULL, channel_master NVARCHAR(64) NULL, store_type NVARCHAR(64) NULL,
    store_grade NVARCHAR(32) NULL, city NVARCHAR(128) NULL, state_name NVARCHAR(128) NULL,
    cluster_name NVARCHAR(128) NULL, mrp FLOAT NULL, mrp_gross_val FLOAT NULL,
    discount_amount FLOAT NULL, discount_pct FLOAT NULL, tax_amount FLOAT NULL,
    size_label NVARCHAR(64) NULL, store_category NVARCHAR(32) NULL, store_sqft FLOAT NULL, mdq FLOAT NULL
)
"""


def _is_link_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(t in msg for t in ("08s01", "08001", "communication link", "10054", "10053", "login timeout", "not accessible"))


def _reliable_conn(attempts: int = 8):
    """Connect with backoff — serverless resume / scaling drops connections for a minute or two."""
    for i in range(attempts):
        try:
            return _bulk_conn()
        except Exception as exc:
            if i == attempts - 1 or not _is_link_error(exc):
                raise
            wait = min(15 * (i + 1), 90)
            print(f"  connect failed ({str(exc)[:90]}); retry in {wait}s", flush=True)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def _exec_retry(holder: list, sql: str, params: list | tuple | None = None, *, many: bool = False, attempts: int = 6):
    """Run one statement / batch inside its own transaction; on link failure reconnect and retry.
    A failed batch rolls back, so a retry never double-inserts."""
    for i in range(attempts):
        c = holder[0]
        try:
            c.autocommit = False
            cur = c.cursor()
            if many:
                cur.fast_executemany = True
                cur.executemany(sql, params)
            else:
                cur.execute(sql, params or [])
            c.commit()
            c.autocommit = True
            return
        except Exception as exc:
            if not _is_link_error(exc) or i == attempts - 1:
                raise
            print(f"  link failure, reconnecting ({str(exc)[:90]})", flush=True)
            try:
                c.close()
            except Exception:
                pass
            holder[0] = _reliable_conn()


def _scalar(holder: list, sql: str, params: list | None = None) -> Any:
    for i in range(6):
        try:
            cur = holder[0].cursor()
            cur.execute(sql, params or [])
            row = cur.fetchone()
            return row[0] if row else None
        except Exception as exc:
            if not _is_link_error(exc) or i == 5:
                raise
            holder[0] = _reliable_conn()


def load_sales(*, resume: bool = True) -> int:
    """sales_fact ← prepared FY parquet (Customer Master enriched).

    Batches go to a rowstore staging heap (~5k rows/s); each FY is then moved into the
    columnstore sales_fact with one INSERT…SELECT (TABLOCK → compressed row groups).
    Trickle-inserting straight into the columnstore ran at ~250 rows/s.
    Resumable: after a crash, FYs already complete are kept, a partial FY is redone.
    """
    from api.azure_sql_analytics import PREPARED_SALES_MARKER

    insert = f"""
    INSERT INTO stockpulse.sales_fact_stage
      ({_SALES_LOAD_COLS})
    VALUES ({", ".join("?" for _ in range(36))})
    """
    text_cols = {
        # source col: max len
        "site_id": 64, "barcode": 64, "sku": 64, "sales_channel": 64, "zone": 64,
        "department": 128, "section": 128, "season": 64, "site_name": 256,
        "site_code": 64, "site_manual_code": 64, "site_short_code": 64, "site_id_name": 400,
        "customer_name": 256, "channel": 64, "store_type": 64, "store_grade": 32,
        "city": 128, "state": 128, "cluster": 128, "size": 64, "store_category": 32,
    }
    num_cols = ("qty", "net_sale_amount", "gross_amount", "mrp", "mrp_gross_val",
                "discount_amount", "discount_pct", "tax_amount", "store_sqft", "mdq")

    def _num(v: Any) -> float | None:
        return None if v is None or pd.isna(v) else float(v)

    def _t(v: str) -> str | None:
        return v or None

    n = 0
    years: list[str] = []
    t0 = time.time()
    holder = [_reliable_conn()]
    try:
        status = _scalar(holder, "SELECT last_status FROM stockpulse.etl_watermark WHERE pipeline_name = 'sales_fact'")
        resuming = resume and status == "loading"
        _exec_retry(holder, _STAGE_DDL)
        _exec_retry(holder, "TRUNCATE TABLE stockpulse.sales_fact_stage")
        for ix in (
            "IX_sales_fact_channel_dept",
            "IX_sales_fact_barcode",
            "IX_sales_fact_site_date",
            "IX_sales_fact_sku",
            "IX_sales_fact_dept_section",
        ):
            try:
                _exec_retry(holder, f"DROP INDEX IF EXISTS {ix} ON stockpulse.sales_fact")
            except Exception as exc:
                print(f"  index drop skip {ix}: {exc}", flush=True)
        if resuming:
            print("sales_fact: resuming previous interrupted load", flush=True)
        else:
            _exec_retry(holder, "TRUNCATE TABLE stockpulse.sales_fact")
            # Not-ready while loading so the API falls back instead of serving a half table.
            _set_watermark("sales_fact", 0, "loading", "loading", holder[0])

        for fy, df in _prepared_sales_frames():
            fy_tok = f"FY{str(fy)[-2:]}"
            years.append(fy_tok)
            df = df.copy()
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
            df = df.dropna(subset=["date"])
            have = int(_scalar(holder, "SELECT COUNT_BIG(*) FROM stockpulse.sales_fact WHERE financial_year = ?", [fy_tok]) or 0)
            if have == len(df):
                print(f"sales_fact: {fy_tok} already complete ({have:,} rows) — skip", flush=True)
                n += have
                continue
            if have:
                print(f"sales_fact: {fy_tok} partial ({have:,}/{len(df):,}) — clearing", flush=True)
                while int(_scalar(holder, "SELECT COUNT_BIG(*) FROM stockpulse.sales_fact WHERE financial_year = ?", [fy_tok]) or 0):
                    _exec_retry(holder, "DELETE TOP (100000) FROM stockpulse.sales_fact WHERE financial_year = ?", [fy_tok])

            for c, ln in text_cols.items():
                s = df[c].map(_clean_str) if c in df.columns else pd.Series("", index=df.index)
                df[c] = s.str.slice(0, ln)
            for c in num_cols:
                df[c] = pd.to_numeric(df[c], errors="coerce") if c in df.columns else float("nan")
            df["site_key_paren"] = _site_key(df["site_id"], r"\(([A-Z]?\d+)\)\s*$")
            df["site_key_dash"] = _site_key(df["site_id"], r"-([A-Z]?\d+)\s*$")

            _exec_retry(holder, "TRUNCATE TABLE stockpulse.sales_fact_stage")
            staged = 0
            for start in range(0, len(df), BATCH):
                chunk = df.iloc[start : start + BATCH]
                rows = [
                    (
                        r.date.date(),
                        r.site_id,  # NOT NULL — '' keeps the row (DuckDB counts it too)
                        r.barcode,
                        _t(r.sku), _t(r.sales_channel), _t(r.zone),
                        _t(r.department), _t(r.section), _t(r.season),
                        _num(r.qty) or 0.0, _num(r.net_sale_amount) or 0.0, _num(r.gross_amount),
                        fy_tok,
                        _t(r.site_name), _t(r.site_code), _t(r.site_manual_code),
                        _t(r.site_short_code), _t(r.site_id_name),
                        _t(r.site_key_paren), _t(r.site_key_dash), _t(r.customer_name),
                        _t(r.channel), _t(r.store_type), _t(r.store_grade),
                        _t(r.city), _t(r.state), _t(r.cluster),
                        _num(r.mrp), _num(r.mrp_gross_val), _num(r.discount_amount),
                        _num(r.discount_pct), _num(r.tax_amount),
                        _t(r.size), _t(r.store_category), _num(r.store_sqft), _num(r.mdq),
                    )
                    for r in chunk.itertuples(index=False)
                ]
                _exec_retry(holder, insert, rows, many=True)
                staged += len(rows)
                if staged % 100000 < BATCH:
                    print(f"  {fy_tok} staged {staged:,}/{len(df):,} ({time.time() - t0:.0f}s)", flush=True)
            staged_db = int(_scalar(holder, "SELECT COUNT_BIG(*) FROM stockpulse.sales_fact_stage") or 0)
            if staged_db != len(df):
                raise RuntimeError(f"{fy_tok}: staged {staged_db:,} rows, expected {len(df):,}")
            # One set-based move per FY; TABLOCK lets ≥102,400-row groups compress directly.
            _exec_retry(
                holder,
                f"INSERT INTO stockpulse.sales_fact WITH (TABLOCK) ({_SALES_LOAD_COLS}) "
                f"SELECT {_SALES_LOAD_COLS} FROM stockpulse.sales_fact_stage",
            )
            moved = int(_scalar(holder, "SELECT COUNT_BIG(*) FROM stockpulse.sales_fact WHERE financial_year = ?", [fy_tok]) or 0)
            if moved != len(df):
                raise RuntimeError(f"{fy_tok}: sales_fact has {moved:,} rows, expected {len(df):,}")
            n += moved
            print(f"sales_fact: {fy_tok} loaded {moved:,} rows ({time.time() - t0:.0f}s)", flush=True)
        _exec_retry(holder, "TRUNCATE TABLE stockpulse.sales_fact_stage")

        cur = holder[0].cursor()
        # Small insert batches land in columnstore delta stores (rowstore); compress them
        # so filter queries get batch mode + segment elimination. No-op on a rowstore table.
        try:
            cur.execute(
                "IF EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID('stockpulse.sales_fact') "
                "AND name = 'IX_sales_fact_date_site' AND type_desc = 'CLUSTERED COLUMNSTORE') "
                "ALTER INDEX IX_sales_fact_date_site ON stockpulse.sales_fact "
                "REORGANIZE WITH (COMPRESS_ALL_ROW_GROUPS = ON)"
            )
        except Exception as exc:
            print(f"  columnstore reorganize: {exc}", flush=True)
        rebuild = {
            "IX_sales_fact_channel_dept": "(sales_channel, department) INCLUDE (net_sale_amount, qty)",
            "IX_sales_fact_barcode": "(barcode)",
            # Filter indexes from sql/phase2_filter_performance.sql
            "IX_sales_fact_site_date": "(site_id, sale_date) INCLUDE (qty, net_sale_amount, gross_sale_amount)",
            "IX_sales_fact_sku": "(sku, sale_date) INCLUDE (qty, net_sale_amount, gross_sale_amount)",
            "IX_sales_fact_dept_section": "(department, section, sale_date) INCLUDE (qty, net_sale_amount, gross_sale_amount)",
        }
        for ix, cols in rebuild.items():
            try:
                cur.execute(f"CREATE NONCLUSTERED INDEX {ix} ON stockpulse.sales_fact {cols}")
            except Exception as exc:
                print(f"  index rebuild {ix}: {exc}", flush=True)
        try:
            cur.execute("UPDATE STATISTICS stockpulse.sales_fact")
        except Exception as exc:
            print(f"  update statistics: {exc}", flush=True)
        build_site_lookup(holder)
        _set_watermark("sales_fact", n, "ok", f"{PREPARED_SALES_MARKER} {','.join(years)}", holder[0])
    finally:
        try:
            holder[0].close()
        except Exception:
            pass
    print(f"sales_fact: {n:,} rows ({', '.join(years)}) in {time.time() - t0:.0f}s", flush=True)
    return n


def build_site_lookup(holder: list | None = None) -> int:
    """stockpulse.site_lookup ← distinct store identity combos of sales_fact (~1k rows).
    The API resolves UI store codes against it, then filters sales_fact on site_id only."""
    from api.azure_sql_analytics import SITE_LOOKUP_SQL

    own = holder is None
    holder = holder or [_reliable_conn()]
    try:
        _exec_retry(holder, SITE_LOOKUP_SQL)
        n = int(_scalar(holder, "SELECT COUNT(*) FROM stockpulse.site_lookup") or 0)
        multi = int(_scalar(holder, "SELECT COUNT(DISTINCT site_id) FROM stockpulse.site_lookup WHERE combos > 1") or 0)
        print(f"site_lookup: {n:,} identity combos ({multi} site_ids with >1 combo use the exact path)", flush=True)
        return n
    finally:
        if own:
            holder[0].close()


def load_sales_raw_csv() -> int:
    """Old raw Sales Master CSV load (no Customer Master columns). Kept for reference;
    the API refuses to query a table loaded this way (see PREPARED_SALES_MARKER)."""
    from api.routes.sales_dashboard import _normalize_columns
    from api.sales_year_loader import resolve_sales_master_path, sanitize_store_channels

    path = resolve_sales_master_path()
    if path is None or not path.exists():
        # fallback local
        cand = INPUT_DIR / "SSI_PT_CENTRO_ICONIC_EBO_SALES_MASTER.csv"
        path = cand if cand.exists() else None
    if path is None:
        print("sales_fact: SKIP (Sales Master missing)")
        return 0

    print(f"sales_fact: reading {path} …", flush=True)
    insert = """
    INSERT INTO stockpulse.sales_fact
      (sale_date, site_id, barcode, sku, sales_channel, zone,
       department, section, season, qty, net_sale_amount, gross_sale_amount, financial_year)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    usecols_candidates = [
        "Invoice Date", "Site Code", "Site", "Zone", "Channel Name", "Site Type",
        "Barcode", "Cat3", "Division", "Department", "Secion", "Section", "Cat6",
        "Qty", "Item Net Amt", "Gross Amt",
    ]
    header = pd.read_csv(path, nrows=0)
    usecols = [c for c in usecols_candidates if c in header.columns]
    n = 0
    t0 = time.time()
    holder = [_bulk_conn()]
    try:
        # Drop nonclustered indexes for faster bulk; rebuild after.
        cur = holder[0].cursor()
        for ix in (
            "IX_sales_fact_channel_dept",
            "IX_sales_fact_barcode",
            "IX_sales_fact_site_date",
            "IX_sales_fact_sku",
            "IX_sales_fact_dept_section",
        ):
            try:
                cur.execute(f"DROP INDEX IF EXISTS {ix} ON stockpulse.sales_fact")
            except Exception as exc:
                print(f"  index drop skip {ix}: {exc}", flush=True)
        _truncate("stockpulse.sales_fact", holder[0])
        for chunk in pd.read_csv(path, usecols=usecols or None, chunksize=BATCH, low_memory=False):
            out = _normalize_columns(chunk)
            out = sanitize_store_channels(out)
            if "date" not in out.columns:
                continue
            out["date"] = pd.to_datetime(out["date"], errors="coerce")
            out = out.dropna(subset=["date"])
            if out.empty:
                continue
            site_s = out["site_id"].map(_clean_str) if "site_id" in out.columns else pd.Series([""] * len(out))
            bc_s = out["barcode"].map(_clean_str) if "barcode" in out.columns else pd.Series([""] * len(out))
            qty_s = pd.to_numeric(out["qty"], errors="coerce").fillna(0.0) if "qty" in out.columns else 0.0
            net_s = pd.to_numeric(out.get("net_sale_amount"), errors="coerce").fillna(0.0) if "net_sale_amount" in out.columns else 0.0
            gross_s = pd.to_numeric(out["gross_amount"], errors="coerce") if "gross_amount" in out.columns else None
            ch_s = out["channel"].map(_clean_str) if "channel" in out.columns else (
                out["sales_channel"].map(_clean_str) if "sales_channel" in out.columns else ""
            )
            sku_s = out["sku"].map(_clean_str) if "sku" in out.columns else ""
            zone_s = out["zone"].map(_clean_str) if "zone" in out.columns else ""
            dept_s = out["department"].map(_clean_str) if "department" in out.columns else ""
            sec_s = out["section"].map(_clean_str) if "section" in out.columns else ""
            season_s = out["season"].map(_clean_str) if "season" in out.columns else ""
            rows = []
            for i, d in enumerate(out["date"]):
                site = site_s.iloc[i] if hasattr(site_s, "iloc") else _clean_str(site_s)
                bc = bc_s.iloc[i] if hasattr(bc_s, "iloc") else _clean_str(bc_s)
                if not site or not bc:
                    continue
                d_py = d.date()
                g = None
                if gross_s is not None:
                    gv = gross_s.iloc[i]
                    g = float(gv) if pd.notna(gv) else None
                rows.append(
                    (
                        d_py,
                        site[:64],
                        bc[:64],
                        (sku_s.iloc[i][:64] or None) if hasattr(sku_s, "iloc") else None,
                        (ch_s.iloc[i][:64] or None) if hasattr(ch_s, "iloc") else None,
                        (zone_s.iloc[i][:64] or None) if hasattr(zone_s, "iloc") else None,
                        (dept_s.iloc[i][:128] or None) if hasattr(dept_s, "iloc") else None,
                        (sec_s.iloc[i][:128] or None) if hasattr(sec_s, "iloc") else None,
                        (season_s.iloc[i][:64] or None) if hasattr(season_s, "iloc") else None,
                        float(qty_s.iloc[i]) if hasattr(qty_s, "iloc") else float(qty_s or 0),
                        float(net_s.iloc[i]) if hasattr(net_s, "iloc") else float(net_s or 0),
                        g,
                        _fy_token(pd.Timestamp(d_py)),
                    )
                )
            if rows:
                _executemany(insert, rows, holder)
                n += len(rows)
            if n and n % 100000 < BATCH:
                print(f"  sales_fact progress {n} ({time.time() - t0:.0f}s)", flush=True)
        # rebuild indexes
        cur = holder[0].cursor()
        # Small insert batches land in columnstore delta stores (rowstore); compress them
        # so filter queries get batch mode + segment elimination. No-op on a rowstore table.
        try:
            cur.execute(
                "IF EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID('stockpulse.sales_fact') "
                "AND name = 'IX_sales_fact_date_site' AND type_desc = 'CLUSTERED COLUMNSTORE') "
                "ALTER INDEX IX_sales_fact_date_site ON stockpulse.sales_fact "
                "REORGANIZE WITH (COMPRESS_ALL_ROW_GROUPS = ON)"
            )
        except Exception as exc:
            print(f"  columnstore reorganize: {exc}", flush=True)
        rebuild = {
            "IX_sales_fact_channel_dept": "(sales_channel, department) INCLUDE (net_sale_amount, qty)",
            "IX_sales_fact_barcode": "(barcode)",
            # Filter indexes from sql/phase2_filter_performance.sql
            "IX_sales_fact_site_date": "(site_id, sale_date) INCLUDE (qty, net_sale_amount, gross_sale_amount)",
            "IX_sales_fact_sku": "(sku, sale_date) INCLUDE (qty, net_sale_amount, gross_sale_amount)",
            "IX_sales_fact_dept_section": "(department, section, sale_date) INCLUDE (qty, net_sale_amount, gross_sale_amount)",
        }
        for ix, cols in rebuild.items():
            try:
                cur.execute(f"CREATE NONCLUSTERED INDEX {ix} ON stockpulse.sales_fact {cols}")
            except Exception as exc:
                print(f"  index rebuild {ix}: {exc}", flush=True)
        try:
            cur.execute("UPDATE STATISTICS stockpulse.sales_fact")
        except Exception as exc:
            print(f"  update statistics: {exc}", flush=True)
        _set_watermark("sales_fact", n, "ok", path.name, holder[0])
    finally:
        try:
            holder[0].close()
        except Exception:
            pass
    print(f"sales_fact: {n} rows from {path.name} in {time.time() - t0:.0f}s", flush=True)
    return n


def count_tables() -> dict[str, int]:
    tables = [
        "stockpulse.store_master",
        "stockpulse.store_grade",
        "stockpulse.item_master",
        "stockpulse.sales_fact",
        "stockpulse.soh_fact",
        "stockpulse.target_lfs",
    ]
    out: dict[str, int] = {}
    with _conn() as conn:
        cur = conn.cursor()
        for t in tables:
            try:
                cur.execute(f"SELECT COUNT_BIG(*) FROM {t}")
                out[t] = int(cur.fetchone()[0])
            except Exception as exc:
                out[t] = -1
                print(f"count {t}: {exc}")
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--only",
        default="grade,store,item,soh,target,sales",
        help="Comma list: grade,store,item,soh,target,sales (site_lookup = rebuild only the store lookup)",
    )
    args = parser.parse_args()
    _load_dotenv()
    os.environ.setdefault("AZURE_SQL_ENABLED", "1")

    from api.azure_sql import ping_azure_sql

    ping = ping_azure_sql()
    if not ping.get("ok"):
        print("Azure SQL ping failed:", ping)
        return 1
    print("Connected:", ping.get("database"), ping.get("server"))

    for name in ("phase1_filter_tables.sql", "phase3_sales_fact_store_columns.sql"):
        schema_extra = BACKEND_ROOT / "sql" / name
        if schema_extra.exists():
            print("Applying", schema_extra.name)
            _apply_sql_file(schema_extra)

    wanted = {x.strip().lower() for x in args.only.split(",") if x.strip()}
    t0 = time.time()
    if "grade" in wanted:
        load_store_grade()
    if "store" in wanted:
        load_stores()
    if "item" in wanted:
        load_items()
    if "soh" in wanted:
        load_soh()
    if "target" in wanted:
        load_targets()
    if "sales" in wanted:
        load_sales()
    elif "site_lookup" in wanted:
        build_site_lookup()

    print("\n=== Row counts ===")
    for k, v in count_tables().items():
        print(f"  {k}: {v:,}" if v >= 0 else f"  {k}: ERR")
    print(f"Done in {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

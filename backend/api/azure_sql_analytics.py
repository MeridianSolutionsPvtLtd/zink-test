"""Azure SQL analytics for Home / Sales Overview / Analyzer / Target.

Opt-in via AZURE_SQL_QUERY=1 (also needs Azure SQL credentials).
On failure callers should fall back to CSV/Blob/DuckDB paths.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
import time
from collections import OrderedDict
from datetime import date, datetime, timedelta
from typing import Any, Iterable

import pandas as pd

logger = logging.getLogger(__name__)

_SOURCE = "azure_sql"

# Analyzer / filter dimension → SQL expression (sales alias s, store alias st)
_DIM_SQL: dict[str, str] = {
    # sales_fact rows carry Customer Master dims (loaded from the prepared FY parquet —
    # same enrichment DuckDB/pandas use), so no store_master join is needed.
    "zone": "s.zone",
    "sales_channel": "s.sales_channel",
    "channel": "s.channel_master",
    "department": "s.department",
    "section": "s.section",
    "season": "s.season",
    "site_id": "s.site_id",
    "site_name": "s.site_name",
    "store_grade": "s.store_grade",
    "cluster": "s.cluster_name",
    "manager": "s.cluster_name",
    "city_name": "s.city",
    "state_name": "s.state_name",
    "sku": "s.sku",
    "barcode": "s.barcode",
    "financial_year": "s.financial_year",
    "month": "FORMAT(s.sale_date, 'MMM')",
    "year": "YEAR(s.sale_date)",
}

_MEASURE_SQL: dict[str, str] = {
    "sales": "SUM(CAST(s.net_sale_amount AS FLOAT))",
    "quantity": "SUM(CAST(s.qty AS FLOAT))",
    "gross_sales": "SUM(CAST(COALESCE(s.gross_sale_amount, s.net_sale_amount) AS FLOAT))",
    "net_sales": "SUM(CAST(s.net_sale_amount AS FLOAT))",
    "qty": "SUM(CAST(s.qty AS FLOAT))",
    "record_count": "COUNT_BIG(*)",
}


def sql_query_enabled() -> bool:
    flag = (os.environ.get("AZURE_SQL_QUERY") or "").strip().lower()
    if flag not in ("1", "true", "yes", "on"):
        return False
    try:
        from api.azure_sql import azure_sql_enabled

        return azure_sql_enabled()
    except Exception:
        return False


def sql_query_ready() -> bool:
    """True when flag on and ping succeeds (cached briefly)."""
    if not sql_query_enabled():
        return False
    now = time.time()
    cached = getattr(sql_query_ready, "_cache", None)
    if cached and now - cached[0] < 120:
        return bool(cached[1])
    try:
        from api.azure_sql import ping_azure_sql

        ok = bool(ping_azure_sql().get("ok"))
    except Exception as exc:
        logger.warning("Azure SQL analytics ping failed: %s", exc)
        ok = False
    sql_query_ready._cache = (now, ok)  # type: ignore[attr-defined]
    return ok


class SqlFilterUnsupported(ValueError):
    """Filter / timeline this module cannot express in SQL — caller must fall back."""


def _csv_vals(raw: Any) -> list[str]:
    """CSV string or list (Analyzer sends lists) → clean values."""
    if raw is None or raw == "":
        return []
    parts: Iterable[Any]
    if isinstance(raw, (list, tuple, set, frozenset)):
        parts = raw
    else:
        parts = str(raw).split(",")
    out: list[str] = []
    for part in parts:
        if part is None:
            continue
        p = str(part).strip()
        if not p or p.lower() in {"all", "*", "none", "nan"}:
            continue
        out.append(re.sub(r"\.0$", "", p))
    return out


def _db_case_sensitive() -> bool:
    """True when the DB collation is case-sensitive (then filters must UPPER() columns)."""
    cached = getattr(_db_case_sensitive, "_cache", None)
    if cached is not None:
        return bool(cached)
    try:
        row = _fetchone("SELECT CONVERT(NVARCHAR(128), DATABASEPROPERTYEX(DB_NAME(), 'Collation'))")
        collation = str(row[0] if row else "")
        cs = "_CS_" in collation.upper() or collation.upper().endswith("_BIN") or "_BIN2" in collation.upper()
    except Exception as exc:
        logger.warning("Azure SQL collation check failed; assuming case-sensitive: %s", exc)
        return True
    _db_case_sensitive._cache = cs  # type: ignore[attr-defined]
    return cs


def _site_variants(values: list[str]) -> list[str]:
    """'A123', 'A123 - Store', 'Store (A123)', 'Store - A123' → every form as a match candidate."""
    out: set[str] = set()
    for v in values:
        u = v.strip().upper()
        if not u:
            continue
        out.add(u)
        for pat in (r"\(([A-Z]?\d+)\)\s*$", r"-\s*([A-Z]?\d+)\s*$", r"^([A-Z]?\d+)\s*-"):
            m = re.search(pat, u)
            if m:
                out.add(m.group(1))
    return sorted(out)


def _channel_values(values: list[str]) -> tuple[list[str], list[str]]:
    """Resolve channel aliases like DuckDB does → (exact match values, short codes for '(CODE)')."""
    try:
        from api.services.analytics_query_engine import _CHANNEL_ALIAS, _CHANNEL_DISPLAY
    except Exception:
        _CHANNEL_ALIAS, _CHANNEL_DISPLAY = {}, {}
    resolved = {_CHANNEL_ALIAS.get(c.upper(), c.upper()) for c in values if c}
    exact: set[str] = set()
    for code in resolved:
        exact.update(_CHANNEL_DISPLAY.get(code, {code}))
        exact.add(code)
    return sorted(exact), sorted(c for c in resolved if len(c) <= 6)


def _parse_fy_end(token: str) -> int | None:
    try:
        from api.services.analytics_query_engine import _parse_fy_end_year

        return _parse_fy_end_year(token)
    except Exception:
        m = re.search(r"(\d{2,4})", token or "")
        if not m:
            return None
        n = int(m.group(1))
        return n if n > 100 else 2000 + n


# SQL Server caps one request at 2100 parameters, and the UI sends ~450 store codes for a
# single channel. Long lists travel as ONE delimited parameter split server-side.
_LIST_SEP = "\x1f"
_INLINE_LIST_MAX = 40


def _list_sql(values: list[Any]) -> tuple[str, list[Any]]:
    """Operand for `x IN <operand>`: inline placeholders when short, else one STRING_SPLIT param."""
    if len(values) <= _INLINE_LIST_MAX:
        return "(" + ",".join("?" for _ in values) + ")", list(values)
    return "(SELECT value FROM STRING_SPLIT(?, NCHAR(31)))", [_LIST_SEP.join(str(v) for v in values)]


def _like_escape(s: str) -> str:
    return s.replace("[", "[[]").replace("%", "[%]").replace("_", "[_]")


def _fy_bounds(fy_end: int) -> tuple[date, date]:
    y = int(fy_end)
    return date(y - 1, 4, 1), date(y, 3, 31)


def _fy_token(fy_end: int) -> str:
    return f"FY{str(int(fy_end))[-2:]}"


# Short-lived result cache + in-flight dedupe. One filter apply fires /kpis, /insights and
# /rows together with identical filters — without this the same SQL runs 2-3 times.
_RESULT_CACHE_TTL_S = float(os.environ.get("AZURE_SQL_RESULT_CACHE_TTL_S") or 120)
_RESULT_CACHE_MAX_ROWS = int(os.environ.get("AZURE_SQL_RESULT_CACHE_MAX_ROWS") or 600_000)
_RESULT_CACHE: "OrderedDict[str, tuple[float, list[tuple]]]" = OrderedDict()
_RESULT_CACHE_LOCK = threading.Lock()
_RESULT_INFLIGHT: dict[str, threading.Event] = {}


def _result_cache_key(sql: str, params: list[Any] | tuple | None) -> str:
    raw = sql + "\x1f" + "\x1f".join(repr(p) for p in (params or []))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _result_cache_get(key: str) -> list[tuple] | None:
    with _RESULT_CACHE_LOCK:
        hit = _RESULT_CACHE.get(key)
        if hit is None:
            return None
        if time.time() - hit[0] > _RESULT_CACHE_TTL_S:
            _RESULT_CACHE.pop(key, None)
            return None
        _RESULT_CACHE.move_to_end(key)
        return hit[1]


def _result_cache_put(key: str, rows: list[tuple]) -> None:
    if _RESULT_CACHE_TTL_S <= 0 or len(rows) > _RESULT_CACHE_MAX_ROWS:
        return
    with _RESULT_CACHE_LOCK:
        _RESULT_CACHE[key] = (time.time(), rows)
        _RESULT_CACHE.move_to_end(key)
        total = sum(len(v[1]) for v in _RESULT_CACHE.values())
        while total > _RESULT_CACHE_MAX_ROWS and len(_RESULT_CACHE) > 1:
            _old_key, (_ts, old_rows) = _RESULT_CACHE.popitem(last=False)
            total -= len(old_rows)


def clear_result_cache() -> None:
    with _RESULT_CACHE_LOCK:
        _RESULT_CACHE.clear()


def _fetchall_uncached(sql: str, params: list[Any] | tuple | None = None) -> list[tuple]:
    from api.azure_sql import azure_sql_connection

    with azure_sql_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, params or [])
        return list(cur.fetchall())


def _fetchall(sql: str, params: list[Any] | tuple | None = None) -> list[tuple]:
    key = _result_cache_key(sql, params)
    hit = _result_cache_get(key)
    if hit is not None:
        return hit
    with _RESULT_CACHE_LOCK:
        waiter = _RESULT_INFLIGHT.get(key)
        leader = waiter is None
        if leader:
            waiter = threading.Event()
            _RESULT_INFLIGHT[key] = waiter
    if not leader:
        waiter.wait(timeout=120.0)
        hit = _result_cache_get(key)
        if hit is not None:
            return hit
        return _fetchall_uncached(sql, params)
    try:
        rows = _fetchall_uncached(sql, params)
        _result_cache_put(key, rows)
        return rows
    finally:
        with _RESULT_CACHE_LOCK:
            _RESULT_INFLIGHT.pop(key, None)
        waiter.set()


def _fetchone(sql: str, params: list[Any] | tuple | None = None) -> tuple | None:
    rows = _fetchall(sql, params)
    return rows[0] if rows else None


def _fetch_df(sql: str, params: list[Any] | tuple | None = None, columns: list[str] | None = None) -> pd.DataFrame:
    rows = _fetchall(sql, params)
    if not rows:
        return pd.DataFrame(columns=columns or [])
    if columns is None:
        return pd.DataFrame.from_records(rows)
    return pd.DataFrame.from_records(rows, columns=columns)


def _needs_store_join(filters: dict[str, Any], group_cols: Iterable[str] | None = None) -> bool:
    # store_master is ~500 rows joined on its PK — always joining keeps one code path.
    return True


_TIMELINE_KEYS = ("timeline_mode", "timeline_values", "date_from", "date_to")

# Filter keys _build_sales_where understands (dashboard CSV names + Analyzer dimension names).
SQL_FILTER_KEYS = frozenset(
    {
        "fy_years", "timeline_mode", "timeline_values", "date_from", "date_to",
        "site_id", "site_ids", "channel", "sales_channel",
        "department", "departments", "section", "sections", "season", "seasons",
        "sku", "skus", "barcode", "barcodes", "zone", "zones",
        "store_grade", "store_grades", "state", "states", "state_name",
        "city", "cities", "city_name", "cluster", "manager", "site_name",
        "financial_year", "year", "month", "search",
    }
)


def _date_ranges_sql(col: str, ranges: list[tuple[date, date]]) -> tuple[str, list[Any]]:
    """[start, end) half-open ranges on a raw date column — sargable, seeks the clustered index."""
    parts = [f"({col} >= ? AND {col} < ?)" for _ in ranges]
    params: list[Any] = []
    for start, end in ranges:
        params.extend([start, end])
    return "(" + " OR ".join(parts) + ")", params


def _fy_ranges(fy_years: Iterable[int]) -> list[tuple[date, date]]:
    out = []
    for y in sorted({int(v) for v in fy_years}):
        start, end = _fy_bounds(y)
        out.append((start, end + timedelta(days=1)))
    return out


def _timeline_sql(
    alias: str,
    mode: str,
    values_raw: str,
    fy_years: list[int],
) -> tuple[str, list[Any]] | None:
    """Mirror sales_dashboard._apply_timeline_filter. None → no timeline predicate."""
    values = [v.strip() for v in str(values_raw or "").split(",") if v.strip()]
    if not values:
        return None
    col = f"{alias}.sale_date"
    mode = (mode or "month").strip().lower()

    if mode == "date":
        from_d = pd.Timestamp(values[0][:10]).date()
        to_d = pd.Timestamp((values[1] if len(values) > 1 else values[0])[:10]).date()
        return _date_ranges_sql(col, [(from_d, to_d + timedelta(days=1))])

    if mode == "year":
        years = sorted({int(v) for v in values if v.isdigit()})
        if not years:
            return None
        return _date_ranges_sql(col, [(date(y, 1, 1), date(y + 1, 1, 1)) for y in years])

    if mode == "month":
        ranges = []
        for v in values:
            m = re.fullmatch(r"(\d{4})-(\d{2})", v)
            if not m:
                raise SqlFilterUnsupported(f"month timeline value {v!r}")
            y, mo = int(m.group(1)), int(m.group(2))
            ranges.append((date(y, mo, 1), date(y + (mo == 12), mo % 12 + 1, 1)))
        return _date_ranges_sql(col, ranges)

    if mode in ("fy_year", "fy", "financial_year"):
        fys = [y for y in (_parse_fy_end(v) for v in values) if y]
        if not fys:
            return None
        return _date_ranges_sql(col, _fy_ranges(fys))

    if mode == "day":
        label_map = {"Sun": 0, "Mon": 1, "Tue": 2, "Wed": 3, "Thu": 4, "Fri": 5, "Sat": 6}
        nums = sorted({label_map[v] for v in values if v in label_map})
        if not nums:
            return None
        # (DATEPART(weekday) + @@DATEFIRST - 1) % 7 → Sun=0 … Sat=6 whatever DATEFIRST is.
        ph = ",".join("?" for _ in nums)
        clause = f"((DATEPART(weekday, {col}) + @@DATEFIRST - 1) % 7) IN ({ph})"
        params: list[Any] = list(nums)
        if fy_years:
            # Pandas/DuckDB only see the loaded FY slices — bound the scan the same way.
            rng_sql, rng_params = _date_ranges_sql(col, _fy_ranges(fy_years))
            clause = f"{rng_sql} AND {clause}"
            params = rng_params + params
        return clause, params

    if mode == "fy_week":
        from api.financial_calendar import selected_week_dates

        dates = selected_week_dates(",".join(values))
        if dates is None or not len(dates):
            return "1=0", []
        ds = sorted({pd.Timestamp(d).date() for d in dates})
        ph = ",".join("?" for _ in ds)
        return f"{col} IN ({ph})", ds

    # event (and anything new): needs Python-side matching.
    raise SqlFilterUnsupported(f"timeline mode {mode!r}")


def _build_sales_where(
    filters: dict[str, Any],
    *,
    alias: str = "s",
) -> tuple[str, list[Any]]:
    """Return WHERE clause (without WHERE keyword) + params for sales_fact.

    Semantics follow DuckDB `_build_filter_sql` / pandas `_apply_filters` so filtered SQL
    numbers match the cached first view. Columns are compared raw (loader trims values) so
    SQL Server can use indexes; raises SqlFilterUnsupported when a filter can't be expressed.
    """
    _require_prepared_sales()
    clauses: list[str] = ["1=1"]
    params: list[Any] = []
    cs = _db_case_sensitive()

    def _col(expr: str) -> str:
        return f"UPPER({expr})" if cs else expr

    def _vals(vs: list[str]) -> list[str]:
        return [v.upper() for v in vs] if cs else list(vs)

    def _in(expr: str, values: list[str]) -> None:
        if not values:
            return
        if any(v.lower() == "__none__" for v in values):
            clauses.append("1=0")
            return
        operand, p = _list_sql(_vals(values))
        clauses.append(f"{_col(expr)} IN {operand}")
        params.extend(p)

    # ---- Date window: explicit timeline wins; fy_years only bounds the default view ----
    fy_years = filters.get("fy_years") or []
    if isinstance(fy_years, (int, str)) and str(fy_years).isdigit():
        fy_years = [int(fy_years)]
    fy_years = [int(y) for y in fy_years]
    tl_values = ",".join(_csv_vals(filters.get("timeline_values")))
    tl_mode = filters.get("timeline_mode") or ""
    if isinstance(tl_mode, (list, tuple)):
        tl_mode = tl_mode[0] if tl_mode else ""
    timeline = _timeline_sql(alias, str(tl_mode), tl_values, fy_years) if tl_values else None
    if timeline is not None:
        clauses.append(timeline[0])
        params.extend(timeline[1])
    elif fy_years:
        rng_sql, rng_params = _date_ranges_sql(f"{alias}.sale_date", _fy_ranges(fy_years))
        clauses.append(rng_sql)
        params.extend(rng_params)
    d0, d1 = filters.get("date_from"), filters.get("date_to")
    if d0:
        clauses.append(f"{alias}.sale_date >= ?")
        params.append(pd.Timestamp(str(d0)[:10]).date())
    if d1:
        clauses.append(f"{alias}.sale_date < ?")
        params.append(pd.Timestamp(str(d1)[:10]).date() + timedelta(days=1))

    # ---- Store (same identity columns as sales_dashboard._site_identity_mask) ----
    site_ids = _csv_vals(filters.get("site_ids"))
    if not site_ids:
        site_ids = _csv_vals(filters.get("site_id"))
    if any(v.lower() == "__none__" for v in site_ids):
        clauses.append("1=0")
    elif site_ids:
        wanted = sorted({v.strip().upper().replace(".0", "") for v in site_ids if v.strip()})
        resolved = _resolve_site_ids(wanted)
        if resolved is not None:
            # Every matched site_id has exactly one identity combo → filtering on site_id
            # alone selects the same rows as the 7-column OR, with one cheap predicate.
            if not resolved:
                clauses.append("1=0")
            else:
                operand, p = _list_sql(resolved)
                clauses.append(f"{alias}.site_id IN {operand}")
                params.extend(p)
        else:
            operand, p = _list_sql(wanted)
            clauses.append("(" + " OR ".join(f"{_col(f'{alias}.{c}')} IN {operand}" for c in _SITE_ID_COLS) + ")")
            for _c in _SITE_ID_COLS:
                params.extend(p)

    # Channel: pandas ORs sales_channel / channel / store_type, exact label or "(CODE)".
    channels = _csv_vals(filters.get("channel") or filters.get("sales_channel"))
    if any(v.lower() == "__none__" for v in channels):
        clauses.append("1=0")
    elif channels:
        exact, codes = _channel_values(channels)
        parts = []
        for c in ("sales_channel", "channel_master", "store_type"):
            ch_expr = _col(f"{alias}.{c}")
            parts.append(f"{ch_expr} IN ({','.join('?' for _ in exact)})")
            params.extend(_vals(exact))
            for code in codes:
                parts.append(f"{ch_expr} LIKE ?")
                params.append(f"%({_like_escape(code.upper() if cs else code)})%")
        clauses.append("(" + " OR ".join(parts) + ")")

    _in(f"{alias}.zone", _csv_vals(filters.get("zones") or filters.get("zone")))
    _in(f"{alias}.store_grade", _csv_vals(filters.get("store_grades") or filters.get("store_grade")))
    _in(
        f"{alias}.state_name",
        _csv_vals(filters.get("states") or filters.get("state") or filters.get("state_name")),
    )
    _in(
        f"{alias}.city",
        _csv_vals(filters.get("cities") or filters.get("city") or filters.get("city_name")),
    )
    _in(f"{alias}.cluster_name", _csv_vals(filters.get("cluster")) + _csv_vals(filters.get("manager")))
    _in(f"{alias}.site_name", _csv_vals(filters.get("site_name")))

    # ---- Product ----
    _in(f"{alias}.department", _csv_vals(filters.get("departments") or filters.get("department")))
    _in(f"{alias}.section", _csv_vals(filters.get("sections") or filters.get("section")))
    _in(f"{alias}.season", _csv_vals(filters.get("season") or filters.get("seasons")))
    _in(f"{alias}.sku", _csv_vals(filters.get("sku") or filters.get("skus")))
    barcodes = _csv_vals(filters.get("barcodes"))
    if not barcodes:
        barcodes = _csv_vals(filters.get("barcode"))
    _in(f"{alias}.barcode", barcodes)

    # ---- Calendar dims (Analyzer) ----
    fy_dim = [y for y in (_parse_fy_end(v) for v in _csv_vals(filters.get("financial_year"))) if y]
    if fy_dim:
        rng_sql, rng_params = _date_ranges_sql(f"{alias}.sale_date", _fy_ranges(fy_dim))
        clauses.append(rng_sql)
        params.extend(rng_params)
    years = [int(v) for v in _csv_vals(filters.get("year")) if v.isdigit()]
    if years:
        clauses.append(f"YEAR({alias}.sale_date) IN ({','.join('?' for _ in years)})")
        params.extend(years)

    month_vals = _csv_vals(filters.get("month"))
    if month_vals:
        name_map = {
            "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
            "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
        }
        month_nums: set[int] = set()
        month_ranges: list[tuple[date, date]] = []
        for m in month_vals:
            if re.fullmatch(r"\d{4}-\d{2}", m):
                y, mo = int(m[:4]), int(m[5:7])
                month_ranges.append((date(y, mo, 1), date(y + (mo == 12), mo % 12 + 1, 1)))
            elif m.lower()[:3] in name_map:
                month_nums.add(name_map[m.lower()[:3]])
            elif re.fullmatch(r"\d{1,2}", m):
                month_nums.add(int(m))
            else:
                raise SqlFilterUnsupported(f"month value {m!r}")
        parts = []
        if month_ranges:
            rng_sql, rng_params = _date_ranges_sql(f"{alias}.sale_date", month_ranges)
            parts.append(rng_sql)
            params.extend(rng_params)
        if month_nums:
            parts.append(f"MONTH({alias}.sale_date) IN ({','.join('?' for _ in month_nums)})")
            params.extend(sorted(month_nums))
        clauses.append("(" + " OR ".join(parts) + ")")

    # ---- Free-text search (same columns as DuckDB) ----
    search = str(filters.get("search") or "").strip()
    if search:
        like = f"%{_like_escape(search.upper() if cs else search)}%"
        cols = [
            f"{alias}.{c}"
            for c in ("site_id", "site_name", "sku", "barcode", "season", "customer_name",
                      "city", "state_name", "zone", "store_type")
        ]
        clauses.append("(" + " OR ".join(f"{_col(c)} LIKE ?" for c in cols) + ")")
        params.extend([like] * len(cols))

    return " AND ".join(clauses), params


def _sales_from_sql(where: str) -> str:
    return f"""
    FROM stockpulse.sales_fact s
    WHERE {where}
    """


# Marker the loader writes to etl_watermark.last_message after loading sales_fact from the
# prepared (Customer-Master-enriched) FY parquet. Older raw-CSV loads lack the store
# columns, and their site_id never matches UI site codes — SQL must not serve those.
PREPARED_SALES_MARKER = "prepared-parquet-v1"


# Store identity columns, as sales_dashboard._site_identity_mask matches them.
_SITE_ID_COLS = (
    "site_code", "site_manual_code", "site_short_code", "site_id", "site_id_name",
    "site_key_paren", "site_key_dash",
)

# Built by the loader after every sales_fact load: one row per distinct store identity
# combo (~1k rows), with how many combos share that site_id.
SITE_LOOKUP_SQL = f"""
DROP TABLE IF EXISTS stockpulse.site_lookup;
SELECT {", ".join(_SITE_ID_COLS)},
       COUNT(*) OVER (PARTITION BY site_id) AS combos
INTO stockpulse.site_lookup
FROM (SELECT DISTINCT {", ".join(_SITE_ID_COLS)} FROM stockpulse.sales_fact) d;
"""


def _site_lookup_ready() -> bool:
    now = time.time()
    cached = getattr(_site_lookup_ready, "_cache", None)
    if cached is None or now - cached[0] > 300:
        try:
            row = _fetchall_uncached("SELECT OBJECT_ID('stockpulse.site_lookup')")
            ok = bool(row) and row[0][0] is not None
        except Exception:
            ok = False
        cached = (now, ok)
        _site_lookup_ready._cache = cached  # type: ignore[attr-defined]
    return bool(cached[1])


def _resolve_site_ids(wanted: list[str]) -> list[str] | None:
    """UI store codes → sales_fact.site_id values via the small site_lookup table.

    None when the lookup is missing or a matched site_id has several identity combos —
    then filtering on site_id alone could differ from the per-column match, so the
    caller uses the exact 7-column predicate instead.
    """
    if not wanted or not _site_lookup_ready():
        return None
    operand, p = _list_sql(wanted)
    # Tiny table: UPPER() is free here and keeps the match exact under any collation.
    where = " OR ".join(f"UPPER({c}) IN {operand}" for c in _SITE_ID_COLS)
    rows = _fetchall(f"SELECT site_id, combos FROM stockpulse.site_lookup WHERE {where}", p * len(_SITE_ID_COLS))
    if any(int(r[1] or 0) > 1 for r in rows):
        return None
    return sorted({str(r[0]) for r in rows})


def _require_prepared_sales() -> None:
    now = time.time()
    cached = getattr(_require_prepared_sales, "_cache", None)
    if cached is None or now - cached[0] > 300:
        try:
            row = _fetchall_uncached(
                "SELECT last_message FROM stockpulse.etl_watermark WHERE pipeline_name = 'sales_fact'"
            )
            ok = bool(row) and str(row[0][0] or "").startswith(PREPARED_SALES_MARKER)
        except Exception as exc:
            logger.warning("Azure SQL sales_fact watermark check failed: %s", exc)
            ok = False
        cached = (now, ok)
        _require_prepared_sales._cache = cached  # type: ignore[attr-defined]
    if not cached[1]:
        raise SqlFilterUnsupported(
            "sales_fact not loaded from prepared parquet — run "
            "scripts/load_filter_sources_to_azure_sql.py --only sales"
        )


# ---------- Filters ----------

_FILTER_PAYLOAD_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_FILTER_PAYLOAD_TTL_S = 300.0


def _cached_filter_payload(key: str, builder):
    now = time.time()
    hit = _FILTER_PAYLOAD_CACHE.get(key)
    if hit and now - hit[0] < _FILTER_PAYLOAD_TTL_S:
        return hit[1]
    payload = builder()
    _FILTER_PAYLOAD_CACHE[key] = (now, payload)
    return payload


def sql_customer_master_filters() -> dict[str, Any]:
    return _cached_filter_payload("customer_master", _sql_customer_master_filters_uncached)


def _sql_customer_master_filters_uncached() -> dict[str, Any]:
    sites_sql = """
    SELECT
      COALESCE(NULLIF(LTRIM(RTRIM(site_manual_code)), ''), site_id) AS site_code,
      site_name, zone, city, state_name, cluster_name, store_category,
      store_grade, customer_name, sales_channel, store_sqft
    FROM stockpulse.store_master
    WHERE COALESCE(NULLIF(LTRIM(RTRIM(site_manual_code)), ''), site_id) IS NOT NULL
      AND LTRIM(RTRIM(COALESCE(NULLIF(LTRIM(RTRIM(site_manual_code)), ''), site_id))) <> ''
    ORDER BY site_id
    """
    rows = _fetchall(sites_sql)
    sites: list[dict[str, Any]] = []
    for r in rows:
        site_code = str(r[0] or "").strip()
        if not site_code:
            continue
        ch = str(r[9] or "").strip().upper()
        sites.append(
            {
                "site_key": site_code,
                "site_code": site_code,
                "site_name": str(r[1] or ""),
                "zone": str(r[2] or ""),
                "city": str(r[3] or ""),
                "state": str(r[4] or ""),
                "cluster": str(r[5] or ""),
                "store_category": str(r[6] or ""),
                "store_grade": str(r[7] or ""),
                "customer_name": str(r[8] or ""),
                "channel": ch,
                "store_type_master": "",
            }
        )

    def _distinct(key: str) -> list[str]:
        vals = sorted(
            {
                str(s.get(key) or "").strip()
                for s in sites
                if str(s.get(key) or "").strip() and str(s.get(key)).strip().lower() not in {"nan", "none"}
            },
            key=str.casefold,
        )
        return vals

    grade_rows = _fetchall(
        "SELECT DISTINCT store_grade FROM stockpulse.store_grade WHERE store_grade IS NOT NULL AND LTRIM(RTRIM(store_grade)) <> ''"
    )
    store_grades = sorted({str(r[0]).strip() for r in grade_rows if r and r[0]}, key=str.casefold) or _distinct("store_grade")

    options = {
        "zones": _distinct("zone"),
        "siteCodes": _distinct("site_code"),
        "siteNames": _distinct("site_name"),
        "storeCategories": ["LTL", "EXIT", "NEW"],
        "salesChannels": ["PT", "SSL", "CENTRO", "ICONIC", "EBO"],
        "customerFormats": ["PT", "SSL", "CENTRO", "ICONIC", "EBO"],
        "customerNames": _distinct("customer_name"),
        "cities": _distinct("city"),
        "states": _distinct("state"),
        "clusterRm": _distinct("cluster"),
        "storeGrades": store_grades,
    }
    return {
        "ok": True,
        "sites": sites,
        "options": options,
        "row_count": len(sites),
        "source": _SOURCE,
        "store_grade_source": _SOURCE,
    }


def sql_item_master_filters() -> dict[str, Any]:
    return _cached_filter_payload("item_master", _sql_item_master_filters_uncached)


def _sql_item_master_filters_uncached() -> dict[str, Any]:
    rows = _fetchall(
        """
        SELECT DISTINCT department, section
        FROM stockpulse.item_master
        WHERE department IS NOT NULL AND LTRIM(RTRIM(department)) <> ''
        """
    )
    departments: set[str] = set()
    sections: set[str] = set()
    sections_by_department: dict[str, list[str]] = {}
    for dept, sec in rows:
        d = str(dept or "").strip()
        s = str(sec or "").strip()
        if not d:
            continue
        departments.add(d)
        if s:
            sections.add(s)
            sections_by_department.setdefault(d, [])
            if s not in sections_by_department[d]:
                sections_by_department[d].append(s)
    for d in sections_by_department:
        sections_by_department[d] = sorted(sections_by_department[d], key=str.casefold)
    count_row = _fetchone("SELECT COUNT_BIG(*) FROM stockpulse.item_master")
    return {
        "ok": True,
        "options": {
            "departments": sorted(departments, key=str.casefold),
            "sections": sorted(sections, key=str.casefold),
        },
        "sections_by_department": sections_by_department,
        "row_count": int(count_row[0]) if count_row else 0,
        "source": _SOURCE,
        "detail": "",
    }


def sql_analyzer_filter_options(year: int) -> dict[str, Any]:
    from api.sales_year_loader import analyzer_visible_years, fy_token

    visible = analyzer_visible_years()
    where, params = _build_sales_where({"fy_years": [int(year)]})
    options: dict[str, list[Any]] = {}

    # One scan of sales_fact for every dimension (was one DISTINCT query per dimension).
    dim_exprs = {
        "zone": _DIM_SQL["zone"],
        "sales_channel": _DIM_SQL["sales_channel"],
        "department": "s.department",
        "section": "s.section",
        "season": "s.season",
        "store_grade": "s.store_grade",
        "cluster": "s.cluster_name",
        "state_name": "s.state_name",
        "city_name": "s.city",
    }
    keys = list(dim_exprs)
    select_cols = ", ".join(f"{expr} AS [{k}]" for k, expr in dim_exprs.items())
    gid_cols = ", ".join(dim_exprs.values())
    sets = ", ".join(f"({expr})" for expr in dim_exprs.values())
    distinct: dict[str, set[str]] = {k: set() for k in keys}
    try:
        rows = _fetchall(
            f"SELECT GROUPING_ID({gid_cols}) AS gid, {select_cols} "
            f"{_sales_from_sql(where)} GROUP BY GROUPING SETS ({sets})",
            params,
        )
        n = len(keys)
        for r in rows:
            gid = int(r[0])
            for i, k in enumerate(keys):
                # GROUPING_ID bit for column i is 0 when that column is the grouped one.
                if not (gid >> (n - 1 - i)) & 1:
                    v = str(r[1 + i] or "").strip()
                    if v and v.lower() != "nan":
                        distinct[k].add(v)
                    break
    except Exception as exc:
        logger.warning("SQL analyzer filter dims failed: %s", exc)
    for key in keys:
        vals = sorted(distinct[key], key=str.casefold)[:300]
        options[key] = vals
        if key == "sales_channel":
            options["channel"] = vals
        if key == "cluster":
            options["manager"] = vals

    # Sites
    try:
        site_sql = f"""
        SELECT DISTINCT TOP 500 s.site_id, s.site_name
        {_sales_from_sql(where)}
        AND s.site_id <> ''
        ORDER BY s.site_id
        """
        site_rows = _fetchall(site_sql, params)
        options["site_id"] = [
            f"{str(r[0]).strip()} - {str(r[1] or '').strip()}".strip(" -")
            for r in site_rows
            if r and r[0]
        ]
    except Exception as exc:
        logger.warning("SQL site filter options failed: %s", exc)
        options["site_id"] = []

    options["financial_year"] = [fy_token(y) for y in visible]
    options["year"] = list(visible)
    options["month"] = ["Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec", "Jan", "Feb", "Mar"]
    options["brand"] = []
    return {"ok": True, "options": options, "source": _SOURCE}


def sql_product_filter_options(filters: dict[str, Any]) -> dict[str, Any]:
    where, params = _build_sales_where(filters)
    seasons = [
        str(r[0]).strip()
        for r in _fetchall(
            f"SELECT DISTINCT TOP 500 s.season {_sales_from_sql(where)} "
            f"AND s.season IS NOT NULL AND LTRIM(RTRIM(s.season)) <> '' ORDER BY 1",
            params,
        )
        if r and r[0]
    ]
    skus = [
        str(r[0]).strip()
        for r in _fetchall(
            f"SELECT DISTINCT TOP 500 s.sku {_sales_from_sql(where)} "
            f"AND s.sku IS NOT NULL AND LTRIM(RTRIM(s.sku)) <> '' ORDER BY 1",
            params,
        )
        if r and r[0]
    ]
    barcodes = [
        str(r[0]).strip()
        for r in _fetchall(
            f"SELECT DISTINCT TOP 500 s.barcode {_sales_from_sql(where)} "
            f"AND s.barcode IS NOT NULL AND LTRIM(RTRIM(s.barcode)) <> '' ORDER BY 1",
            params,
        )
        if r and r[0]
    ]
    return {
        "ok": True,
        "seasons": seasons,
        "skus": skus,
        "barcodes": barcodes,
        "source": _SOURCE,
        "query_engine": _SOURCE,
    }


# ---------- Filtered sales frame (filter apply → rows from SQL) ----------

# Output column → SQL expression. Names follow the prepared FY parquet (what DuckDB /
# pandas frames carry) plus the city_name / state_name / manager aliases older helpers read.
_SALES_FRAME_SQL: dict[str, str] = {
    "date": "s.sale_date",
    "site_id": "s.site_id",
    "site_name": "s.site_name",
    "site_code": "s.site_code",
    "site_manual_code": "s.site_manual_code",
    "site_short_code": "s.site_short_code",
    "site_id_name": "s.site_id_name",
    "customer_name": "s.customer_name",
    "barcode": "s.barcode",
    "sku": "s.sku",
    "sales_channel": "s.sales_channel",
    "channel": "s.channel_master",
    "store_type": "s.store_type",
    "zone": "s.zone",
    "department": "s.department",
    "section": "s.section",
    "season": "s.season",
    "qty": "CAST(s.qty AS FLOAT)",
    "net_sale_amount": "CAST(s.net_sale_amount AS FLOAT)",
    "gross_amount": "CAST(s.gross_sale_amount AS FLOAT)",
    "mrp": "s.mrp",
    "mrp_gross_val": "s.mrp_gross_val",
    "discount_amount": "s.discount_amount",
    "discount_pct": "s.discount_pct",
    "tax_amount": "s.tax_amount",
    "financial_year": "s.financial_year",
    "store_grade": "s.store_grade",
    "city": "s.city",
    "state": "s.state_name",
    "cluster": "s.cluster_name",
    "size": "s.size_label",
    "store_category": "s.store_category",
    "store_sqft": "s.store_sqft",
    "mdq": "s.mdq",
}
_SALES_FRAME_COLS = list(_SALES_FRAME_SQL)


def sql_load_filtered_sales_df(filters: dict[str, Any]) -> pd.DataFrame:
    """Load filter-scoped sales lines from Azure SQL (not CSV/DuckDB).

    Returns columns aligned with Sales Overview / Home helpers.
    """
    where, params = _build_sales_where(filters)
    return _load_sales_frame(where, params)


# Store attributes are constant per site_id (checked: 990 sites, one combo each). Pulling
# them on every sales line made the frame ~3x slower over the wire, so the line query
# skips them and they are joined from a small cached per-site table.
_SITE_ATTR_COLS = (
    "site_name", "site_code", "site_manual_code", "site_short_code", "site_id_name",
    "customer_name", "sales_channel", "channel", "store_type", "zone", "store_grade",
    "city", "state", "cluster", "store_category", "store_sqft", "mdq",
)
_LINE_FRAME_SQL = {k: v for k, v in _SALES_FRAME_SQL.items() if k not in _SITE_ATTR_COLS}
_SITE_ATTR_TTL_S = 3600.0
_SITE_ATTR_CACHE: tuple[float, pd.DataFrame | None] | None = None
_SITE_ATTR_LOCK = threading.Lock()


def _site_attrs(*, refresh: bool = False) -> pd.DataFrame | None:
    """One row per site_id with the store columns, or None if any site_id is ambiguous."""
    global _SITE_ATTR_CACHE
    with _SITE_ATTR_LOCK:
        hit = _SITE_ATTR_CACHE
        if not refresh and hit is not None and time.time() - hit[0] < _SITE_ATTR_TTL_S:
            return hit[1]
        select = ", ".join(f"{_SALES_FRAME_SQL[c]} AS [{c}]" for c in _SITE_ATTR_COLS)
        rows = _fetchall_uncached(f"SELECT DISTINCT s.site_id, {select} FROM stockpulse.sales_fact s")
        attrs = pd.DataFrame.from_records(rows, columns=["site_id", *_SITE_ATTR_COLS])
        if attrs["site_id"].duplicated().any():
            logger.warning("Azure SQL site attributes not unique per site_id; using wide frame")
            attrs = None
        _SITE_ATTR_CACHE = (time.time(), attrs)
        return attrs


def _fetch_sales_lines(where: str, params: list[Any]) -> pd.DataFrame:
    """Sales lines in _SALES_FRAME_COLS order: narrow line query + cached site join."""
    attrs = _site_attrs()
    if attrs is not None:
        select = ",\n      ".join(f"{expr} AS [{name}]" for name, expr in _LINE_FRAME_SQL.items())
        lines = _fetch_df(f"SELECT {select} {_sales_from_sql(where)}", params, columns=list(_LINE_FRAME_SQL))
        known = set(attrs["site_id"])
        if not set(lines["site_id"].dropna()) <= known:
            # A site loaded after the cache was built: refresh once, else fall back.
            attrs = _site_attrs(refresh=True)
            known = set(attrs["site_id"]) if attrs is not None else set()
        if attrs is not None and set(lines["site_id"].dropna()) <= known:
            return lines.merge(attrs, on="site_id", how="left")[_SALES_FRAME_COLS]
    select = ",\n      ".join(f"{expr} AS [{name}]" for name, expr in _SALES_FRAME_SQL.items())
    return _fetch_df(f"SELECT {select} {_sales_from_sql(where)}", params, columns=_SALES_FRAME_COLS)


def _load_sales_frame(where: str, params: list[Any]) -> pd.DataFrame:
    t0 = time.perf_counter()
    df = _fetch_sales_lines(where, params)
    elapsed = (time.perf_counter() - t0) * 1000.0
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["month_key"] = df["date"].dt.strftime("%Y-%m")
    for c in ("qty", "net_sale_amount", "gross_amount", "mrp", "mrp_gross_val",
              "discount_amount", "discount_pct", "tax_amount", "store_sqft", "mdq"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    for c in ("site_name", "site_code", "site_manual_code", "site_short_code", "site_id_name",
              "customer_name", "sales_channel", "channel", "store_type", "zone", "store_grade",
              "city", "state", "cluster", "size", "store_category"):
        df[c] = df[c].fillna("")
    df["city_name"] = df["city"]
    df["state_name"] = df["state"]
    df["manager"] = df["cluster"]
    logger.info(
        "Azure SQL filtered sales rows=%s query_ms=%.1f",
        len(df),
        elapsed,
    )
    df.attrs["query_engine"] = _SOURCE
    df.attrs["query_ms"] = round(elapsed, 2)
    return df


# ---------- KPIs / SOH / Home ----------

def sql_sales_kpis(filters: dict[str, Any]) -> dict[str, Any]:
    where, params = _build_sales_where(filters)
    # Same formulas as aggregate_sales_kpis_duckdb: gross = mrp_gross_val, discount column,
    # avg of discount_pct, full-price = discount_pct <= 10.
    # GROUPING SETS: per-site nets (for SPSF) + grand total in one round trip / one plan.
    net = "CAST(s.net_sale_amount AS FLOAT)"
    qty = "CAST(s.qty AS FLOAT)"
    dp = "COALESCE(s.discount_pct, 0)"
    sql = f"""
    SELECT
      GROUPING(s.site_id),
      s.site_id,
      COALESCE(SUM({qty}), 0),
      COALESCE(SUM({net}), 0),
      COALESCE(SUM(COALESCE(s.discount_amount, 0)), 0),
      COALESCE(SUM(COALESCE(s.mrp_gross_val, 0)), 0),
      COUNT_BIG(*),
      COUNT(DISTINCT s.site_id),
      COUNT(DISTINCT s.sale_date),
      MIN(s.sale_date),
      MAX(s.sale_date),
      COALESCE(SUM(CASE WHEN {dp} <= 10.0 THEN {net} ELSE 0 END), 0),
      COALESCE(SUM(CASE WHEN {dp} <= 10.0 THEN {qty} ELSE 0 END), 0),
      COALESCE(SUM(CASE WHEN {dp} > 10.0 THEN {net} ELSE 0 END), 0),
      COALESCE(SUM(CASE WHEN {dp} > 10.0 THEN {qty} ELSE 0 END), 0),
      COALESCE(SUM(COALESCE(s.tax_amount, 0)), 0),
      COALESCE(AVG({dp}), 0)
    {_sales_from_sql(where)}
    GROUP BY GROUPING SETS ((s.site_id), ())
    """
    t0 = time.perf_counter()
    rows = _fetchall(sql, params)
    elapsed = (time.perf_counter() - t0) * 1000.0
    grand = next((r for r in rows if int(r[0] or 0) == 1), None)
    row = grand[2:] if grand is not None else None
    if not row or not int(row[4] or 0):
        agg = {
            "qty": 0, "net": 0, "disc": 0, "gross": 0, "tax_amt": 0, "avg_disc": 0,
            "line_count": 0, "store_count": 0, "day_count": 1,
            "min_date": None, "max_date": None,
            "fp_sales": 0, "fp_qty": 0, "disc_sales": 0, "disc_qty": 0,
            "site_nets": {},
        }
    else:
        site_nets = {
            str(r[1]).strip(): float(r[3] or 0)
            for r in rows
            if int(r[0] or 0) == 0 and r[1] is not None and str(r[1]).strip()
        }
        qty = float(row[0] or 0)
        net = float(row[1] or 0)
        disc = float(row[2] or 0)
        gross = float(row[3] or 0)
        agg = {
            "qty": qty,
            "net": net,
            "disc": disc,
            "gross": gross,
            "tax_amt": float(row[13] or 0),
            "avg_disc": float(row[14] or 0),
            "line_count": int(row[4] or 0),
            "store_count": int(row[5] or 0),
            "day_count": max(1, int(row[6] or 1)),
            "min_date": str(row[7]) if row[7] is not None else None,
            "max_date": str(row[8]) if row[8] is not None else None,
            "fp_sales": float(row[9] or 0),
            "fp_qty": float(row[10] or 0),
            "disc_sales": float(row[11] or 0),
            "disc_qty": float(row[12] or 0),
            "site_nets": site_nets,
        }
    return {
        "agg": agg,
        "diag": {"engine": _SOURCE, "query_ms": round(elapsed, 2)},
    }


def sql_sales_extent(filters: dict[str, Any]) -> dict[str, Any]:
    """Line count + date bounds only (what /insights needs for the SPLY window)."""
    where, params = _build_sales_where(filters)
    row = _fetchone(f"SELECT COUNT_BIG(*), MIN(s.sale_date), MAX(s.sale_date) {_sales_from_sql(where)}", params)
    return {
        "line_count": int(row[0] or 0) if row else 0,
        "min_date": str(row[1]) if row and row[1] is not None else None,
        "max_date": str(row[2]) if row and row[2] is not None else None,
    }


def sql_sply_totals(
    *,
    min_date: str | None,
    max_date: str | None,
    filters: dict[str, Any],
) -> tuple[float, float]:
    """Same-period-last-year net/qty — mirrors aggregate_sply_totals_duckdb.

    Date window = current [min_date, max_date] shifted −1 year (fy_week maps week dates),
    month filter dropped, season codes shifted −1 year.
    """
    if not min_date or not max_date:
        return 0.0, 0.0
    sw = _sply_where(min_date=min_date, max_date=max_date, filters=filters)
    if sw is None:
        return 0.0, 0.0
    where, params = sw
    row = _fetchone(
        f"SELECT COALESCE(SUM(CAST(s.net_sale_amount AS FLOAT)), 0), "
        f"COALESCE(SUM(CAST(s.qty AS FLOAT)), 0) {_sales_from_sql(where)}",
        params,
    )
    return (float(row[0] or 0), float(row[1] or 0)) if row else (0.0, 0.0)


def _sply_where(
    *,
    min_date: Any,
    max_date: Any,
    filters: dict[str, Any],
) -> tuple[str, list[Any]] | None:
    """WHERE for same-period-last-year — mirrors sales_dashboard._load_sply_prior_df:
    window −1 year (fy_week → same week dates LY), month filter dropped, season −1 year."""
    filters = filters or {}
    mode = str(filters.get("timeline_mode") or "").strip().lower()
    tl_values = str(filters.get("timeline_values") or "")
    ly_dates = None
    if mode == "fy_week" and tl_values:
        from api.financial_calendar import selected_week_dates

        ly_dates = selected_week_dates(tl_values) - pd.DateOffset(years=1)
    if (ly_dates is None or not len(ly_dates)) and (
        min_date is None or max_date is None or pd.isna(min_date) or pd.isna(max_date)
    ):
        return None
    f = {k: v for k, v in filters.items() if k not in _TIMELINE_KEYS and k not in ("fy_years", "month")}
    season = str(f.get("season") or "")
    if season and season.lower() != "all":
        try:
            from api.season_utils import shift_season_csv

            f["season"] = shift_season_csv(season, 1) or season
        except Exception:
            pass
    where, params = _build_sales_where(f)
    if ly_dates is not None and len(ly_dates):
        ds = sorted({pd.Timestamp(d).date() for d in ly_dates})
        where += f" AND s.sale_date IN ({','.join('?' for _ in ds)})"
        return where, params + ds
    ly_min = (pd.Timestamp(min_date) - pd.DateOffset(years=1)).date()
    ly_max = (pd.Timestamp(max_date) - pd.DateOffset(years=1)).date()
    where += " AND s.sale_date >= ? AND s.sale_date < ?"
    return where, params + [ly_min, ly_max + timedelta(days=1)]


def sql_load_sply_sales_df(
    *,
    min_date: Any,
    max_date: Any,
    filters: dict[str, Any],
) -> pd.DataFrame:
    """SPLY sales lines (same frame shape as sql_load_filtered_sales_df)."""
    sw = _sply_where(min_date=min_date, max_date=max_date, filters=filters)
    if sw is None:
        return pd.DataFrame(columns=_SALES_FRAME_COLS)
    return _load_sales_frame(*sw)


def sql_distinct_sku_count(filters: dict[str, Any]) -> int:
    where, params = _build_sales_where(filters)
    row = _fetchone(f"SELECT COUNT(DISTINCT s.sku) {_sales_from_sql(where)}", params)
    return int(row[0] or 0) if row else 0


def sql_site_frame_between(filters: dict[str, Any], start: date, end: date) -> pd.DataFrame | None:
    """Per-site net sales on [start, end] (inclusive dates) with the site columns attached,
    one row per site_id. None when site attributes are not unique (caller falls back)."""
    attrs = _site_attrs()
    if attrs is None:
        return None
    where, params = _build_sales_where(filters)
    rows = _fetchall(
        f"SELECT s.site_id, SUM(CAST(s.net_sale_amount AS FLOAT)) "
        f"{_sales_from_sql(f'({where}) AND s.sale_date >= ? AND s.sale_date <= ?')} GROUP BY s.site_id",
        params + [start, end],
    )
    nets = pd.DataFrame.from_records(rows, columns=["site_id", "net_sale_amount"])
    out = nets.merge(attrs, on="site_id", how="left")
    if out["site_name"].isna().any() and nets["site_id"].notna().any():
        attrs = _site_attrs(refresh=True)
        if attrs is None:
            return None
        out = nets.merge(attrs, on="site_id", how="left")
    return out


def sql_sales_site_rows(site_ids: Iterable[Any]) -> pd.DataFrame | None:
    """Cached site columns for the given sales site_ids (one row each)."""
    attrs = _site_attrs()
    if attrs is None:
        return None
    wanted = {str(s).strip() for s in site_ids}
    return attrs[attrs["site_id"].astype(str).str.strip().isin(wanted)]


def sql_sales_date_bounds(filters: dict[str, Any]) -> tuple[Any, Any]:
    """(min, max) sale_date of the filtered rows — the SPLY anchor without the rows."""
    where, params = _build_sales_where(filters)
    row = _fetchone(f"SELECT MIN(s.sale_date), MAX(s.sale_date) {_sales_from_sql(where)}", params)
    return (row[0], row[1]) if row else (None, None)


def sql_sply_monthly_sales(
    *,
    min_date: Any,
    max_date: Any,
    filters: dict[str, Any],
) -> dict[int, float]:
    """Calendar month → SPLY net sales, aggregated in SQL (no rows leave the DB)."""
    sw = _sply_where(min_date=min_date, max_date=max_date, filters=filters)
    if sw is None:
        return {}
    where, params = sw
    rows = _fetchall(
        f"SELECT MONTH(s.sale_date), SUM(CAST(s.net_sale_amount AS FLOAT)) "
        f"{_sales_from_sql(where)} GROUP BY MONTH(s.sale_date)",
        params,
    )
    return {int(m): float(v or 0) for m, v in rows if m is not None}


def sql_aggregate_insights(
    filters: dict[str, Any],
    *,
    cards: set[str] | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Insight cards as SQL aggregates — same payload shape as aggregate_insights_duckdb
    (light=False). Only the requested cards are queried; no sales rows leave the DB."""
    where, params = _build_sales_where(filters)
    frm = _sales_from_sql(where)
    net = "CAST(s.net_sale_amount AS FLOAT)"
    qty = "CAST(s.qty AS FLOAT)"
    gross = "COALESCE(s.mrp_gross_val, 0)"
    disc = "COALESCE(s.discount_amount, 0)"
    lim = int(limit)
    result: dict[str, Any] = {
        "top_discounted_skus": [], "top_sold_skus": [], "top_revenue_sites": [],
        "monthly_trend": [], "weekly_trend": [], "yearly_trend": [],
        "top_departments": [], "top_sections": [], "top_channels": [], "top_zones": [],
        "top_states": [], "top_cities": [],
    }

    def _card(name: str) -> bool:
        return cards is None or name in cards

    if _card("trend_month"):
        mk = "DATEFROMPARTS(YEAR(s.sale_date), MONTH(s.sale_date), 1)"
        for r in _fetchall(
            f"SELECT {mk}, SUM({net}), SUM({gross}), SUM({disc}), SUM({qty}) {frm} GROUP BY {mk} ORDER BY 1",
            params,
        ):
            ts = pd.Timestamp(r[0])
            result["monthly_trend"].append({
                "month": ts.strftime("%Y-%m"), "label": ts.strftime("%b %Y"),
                "net_sales": round(float(r[1] or 0), 2), "gross_sales": round(float(r[2] or 0), 2),
                "discount_amount": round(float(r[3] or 0), 2), "units": int(r[4] or 0),
            })

    if _card("trend_week"):
        wk = "DATEADD(day, (DATEDIFF(day, '19000101', s.sale_date) / 7) * 7, CAST('19000101' AS DATE))"
        for r in _fetchall(
            f"SELECT {wk}, SUM({net}), SUM({gross}), SUM({qty}) {frm} GROUP BY {wk} ORDER BY 1",
            params,
        ):
            ts = pd.Timestamp(r[0])
            result["weekly_trend"].append({
                "week": ts.strftime("%Y-%m-%d"), "label": ts.strftime("Wk %d %b"),
                "net_sales": round(float(r[1] or 0), 2), "gross_sales": round(float(r[2] or 0), 2),
                "units": int(r[3] or 0),
            })

    if _card("trend_year"):
        for r in _fetchall(
            f"SELECT YEAR(s.sale_date), SUM({net}), SUM({gross}), SUM({qty}) {frm} "
            f"GROUP BY YEAR(s.sale_date) ORDER BY 1",
            params,
        ):
            yk = str(r[0])
            result["yearly_trend"].append({
                "year": yk, "label": yk, "net_sales": round(float(r[1] or 0), 2),
                "gross_sales": round(float(r[2] or 0), 2), "units": int(r[3] or 0),
            })

    if _card("breakdown_sku") or _card("breakdown_discount"):
        # Two TOP-N lists from one grouped CTE (was: group in DB, sort in pandas).
        rows = _fetchall(
            f"""
            WITH g AS (
              SELECT s.sku AS sku, SUM({disc}) AS d, SUM({net}) AS n, SUM({qty}) AS u
              {frm} AND s.sku IS NOT NULL AND s.sku <> ''
              GROUP BY s.sku
            )
            SELECT 'disc', * FROM (SELECT TOP ({lim}) * FROM g ORDER BY d DESC) a
            UNION ALL
            SELECT 'sold', * FROM (SELECT TOP ({lim}) * FROM g ORDER BY n DESC) b
            """,
            params,
        )
        for kind, sku, d, n, u in rows:
            item = {"sku": str(sku), "discount_amount": round(float(d or 0), 2),
                    "net_sales": round(float(n or 0), 2), "units": int(u or 0)}
            result["top_discounted_skus" if kind == "disc" else "top_sold_skus"].append(item)
        result["top_discounted_skus"].sort(key=lambda x: -x["discount_amount"])
        result["top_sold_skus"].sort(key=lambda x: -x["net_sales"])

    if _card("breakdown_store"):
        for r in _fetchall(
            f"SELECT TOP ({lim}) s.site_id, s.site_name, SUM({net}) AS n, SUM({qty}), SUM({disc}) "
            f"{frm} GROUP BY s.site_id, s.site_name ORDER BY n DESC",
            params,
        ):
            if not str(r[0] or "").strip():
                continue
            result["top_revenue_sites"].append({
                "site_id": str(r[0]), "site_name": str(r[1] or r[0]),
                "net_sales": round(float(r[2] or 0), 2), "units": int(r[3] or 0),
                "discount_amount": round(float(r[4] or 0), 2),
            })

    def _top_dim(col: str, out_key: str) -> None:
        for r in _fetchall(
            f"SELECT TOP ({lim}) {col}, SUM({net}) AS n, SUM({qty}) {frm} "
            f"AND {col} IS NOT NULL AND {col} <> '' GROUP BY {col} ORDER BY n DESC",
            params,
        ):
            result[out_key].append({
                "name": str(r[0]), "net_sales": round(float(r[1] or 0), 2), "units": int(r[2] or 0),
            })

    if _card("breakdown_department"):
        _top_dim("s.department", "top_departments")
    if _card("breakdown_section"):
        _top_dim("s.section", "top_sections")
    if _card("channel") or _card("breakdown_channel"):
        _top_dim("s.store_type", "top_channels")
    if _card("breakdown_zone"):
        _top_dim("s.zone", "top_zones")
    if _card("breakdown_state"):
        _top_dim("s.state_name", "top_states")
    if _card("breakdown_city"):
        _top_dim("s.city", "top_cities")
    return result


# /rows sort key → sales_fact column (mirrors sales_dashboard._SALES_ROW_SORT targets).
_ROW_SORT_SQL: dict[str, str] = {
    "net_sale_amount": "s.net_sale_amount", "qty": "s.qty", "date": "s.sale_date",
    "sku": "s.sku", "barcode": "s.barcode", "site_id": "s.site_id", "site_name": "s.site_name",
    "zone": "s.zone", "season": "s.season", "size": "s.size_label", "mrp": "s.mrp",
    "discount_pct": "s.discount_pct", "discount_amount": "s.discount_amount", "city": "s.city",
    "state": "s.state_name", "store_grade": "s.store_grade", "customer_name": "s.customer_name",
    "sales_channel": "s.sales_channel", "tax_amount": "s.tax_amount", "store_sqft": "s.store_sqft",
    "mdq": "s.mdq",
}


def sql_sales_page(
    filters: dict[str, Any],
    *,
    page: int,
    page_size: int,
    sort_col: str,
    sort_dir: str,
) -> tuple[pd.DataFrame, int]:
    """One sorted table page via OFFSET/FETCH + total row count — only page_size rows leave the DB."""
    where, params = _build_sales_where(filters)
    order = _ROW_SORT_SQL.get(sort_col, "s.net_sale_amount")
    direction = "ASC" if str(sort_dir).lower() == "asc" else "DESC"
    offset = max(0, (int(page) - 1) * int(page_size))
    select = ",\n      ".join(f"{expr} AS [{name}]" for name, expr in _SALES_FRAME_SQL.items())
    # NULLs last (pandas na_position="last") + stable tiebreak so pages never overlap.
    sql = f"""
    SELECT {select}
    {_sales_from_sql(where)}
    ORDER BY CASE WHEN {order} IS NULL THEN 1 ELSE 0 END, {order} {direction},
             s.sale_date DESC, s.site_id, s.barcode
    OFFSET ? ROWS FETCH NEXT ? ROWS ONLY
    """
    page_df = _fetch_df(sql, params + [offset, int(page_size)], columns=_SALES_FRAME_COLS)
    total_row = _fetchone(f"SELECT COUNT_BIG(*) {_sales_from_sql(where)}", params)
    page_df["date"] = pd.to_datetime(page_df["date"], errors="coerce")
    for c in ("qty", "net_sale_amount", "gross_amount", "mrp", "mrp_gross_val",
              "discount_amount", "discount_pct", "tax_amount", "store_sqft", "mdq"):
        page_df[c] = pd.to_numeric(page_df[c], errors="coerce")
    for c in ("mrp", "qty", "net_sale_amount", "discount_amount", "discount_pct"):
        page_df[c] = page_df[c].fillna(0.0)
    for c in ("site_name", "sku", "barcode", "size", "season", "sales_channel", "store_type",
              "zone", "city", "state", "cluster", "store_grade", "store_category",
              "customer_name", "site_short_code", "department", "section"):
        page_df[c] = page_df[c].fillna("")
    return page_df, int(total_row[0]) if total_row else 0


def sql_light_trends(filters: dict[str, Any]) -> dict[str, Any]:
    """Last 2 months + last 2 Monday-weeks — what _attach_growth_kpis needs for GOLM/GOLW/MoM."""
    where, params = _build_sales_where(filters)
    gross = "COALESCE(s.mrp_gross_val, 0)"  # DuckDB _gross_expr
    month_rows = _fetchall(
        f"""
        SELECT TOP 2 DATEFROMPARTS(YEAR(s.sale_date), MONTH(s.sale_date), 1) AS mk,
               SUM(CAST(s.net_sale_amount AS FLOAT)), SUM({gross}),
               SUM(s.discount_amount),
               SUM(CAST(s.qty AS FLOAT))
        {_sales_from_sql(where)}
        GROUP BY DATEFROMPARTS(YEAR(s.sale_date), MONTH(s.sale_date), 1)
        ORDER BY mk DESC
        """,
        params,
    )
    # Monday week start, independent of @@DATEFIRST (1900-01-01 was a Monday).
    week_expr = "DATEADD(day, (DATEDIFF(day, '19000101', s.sale_date) / 7) * 7, CAST('19000101' AS DATE))"
    week_rows = _fetchall(
        f"""
        SELECT TOP 2 {week_expr} AS wk,
               SUM(CAST(s.net_sale_amount AS FLOAT)), SUM({gross}), SUM(CAST(s.qty AS FLOAT))
        {_sales_from_sql(where)}
        GROUP BY {week_expr}
        ORDER BY wk DESC
        """,
        params,
    )
    monthly = [
        {
            "month": pd.Timestamp(r[0]).strftime("%Y-%m"),
            "label": pd.Timestamp(r[0]).strftime("%b %Y"),
            "net_sales": round(float(r[1] or 0), 2),
            "gross_sales": round(float(r[2] or 0), 2),
            "discount_amount": round(float(r[3] or 0), 2),
            "units": int(r[4] or 0),
        }
        for r in reversed(month_rows)
    ]
    weekly = [
        {
            "week": pd.Timestamp(r[0]).strftime("%Y-%m-%d"),
            "label": pd.Timestamp(r[0]).strftime("Wk %d %b"),
            "net_sales": round(float(r[1] or 0), 2),
            "gross_sales": round(float(r[2] or 0), 2),
            "units": int(r[3] or 0),
        }
        for r in reversed(week_rows)
    ]
    return {"monthly_trend": monthly, "weekly_trend": weekly}


def sql_soh_total(filters: dict[str, Any] | None = None) -> float:
    filters = filters or {}
    clauses = ["1=1"]
    params: list[Any] = []
    cs = _db_case_sensitive()
    site_ids = _csv_vals(filters.get("site_ids")) or _csv_vals(filters.get("site_id"))
    if site_ids:
        operand, p = _list_sql(_site_variants(site_ids))
        clauses.append(f"{'UPPER(f.site_id)' if cs else 'f.site_id'} IN {operand}")
        params.extend(p)
    # The loader leaves soh_fact.sales_channel / department NULL — take them from the masters.
    channels = _csv_vals(filters.get("channel"))
    if channels:
        exact, _codes = _channel_values(channels)
        expr = "COALESCE(NULLIF(f.sales_channel, ''), st.sales_channel)"
        ph = ",".join("?" for _ in exact)
        clauses.append(f"{f'UPPER({expr})' if cs else expr} IN ({ph})")
        params.extend(exact)
    depts = _csv_vals(filters.get("departments"))
    if depts:
        ph = ",".join("?" for _ in depts)
        clauses.append(f"COALESCE(NULLIF(f.department, ''), im.department) IN ({ph})")
        params.extend(depts)
    # Latest snapshot date looked up once (PK leads with as_of_date → single seek), then
    # passed as a parameter so the main query seeks straight to that date.
    latest = _fetchone("SELECT MAX(as_of_date) FROM stockpulse.soh_fact")
    if not latest or latest[0] is None:
        return 0.0
    clauses.append("f.as_of_date = ?")
    params.append(latest[0])
    sql = f"""
    SELECT COALESCE(SUM(CAST(f.current_soh AS FLOAT)), 0)
    FROM stockpulse.soh_fact f
    LEFT JOIN stockpulse.store_master st ON st.site_id = f.site_id
    LEFT JOIN stockpulse.item_master im ON im.barcode = f.barcode
    WHERE {' AND '.join(clauses)}
    """
    row = _fetchone(sql, params)
    return float(row[0] or 0) if row else 0.0


def _for_fy(filters: dict[str, Any] | None, year: int) -> dict[str, Any]:
    """Pin the query to one FY. The request timeline is dropped — otherwise the prior-year
    call (year - 1) would still be bounded by the current year's timeline values."""
    f = {k: v for k, v in (filters or {}).items() if k not in _TIMELINE_KEYS}
    f["fy_years"] = [int(year)]
    return f


def sql_home_sales_totals(year: int, filters: dict[str, Any] | None = None) -> dict[str, Any]:
    f = _for_fy(filters, year)
    result = sql_sales_kpis(f)
    soh = sql_soh_total(f)
    agg = result["agg"]
    return {
        "ok": True,
        "year": int(year),
        "net_sales": float(agg.get("net") or 0),
        "units_sold": float(agg.get("qty") or 0),
        "store_count": int(agg.get("store_count") or 0),
        "line_count": int(agg.get("line_count") or 0),
        "current_soh": soh,
        "query_engine": _SOURCE,
        "diag": result["diag"],
    }


def sql_home_channel_breakdown(year: int, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    f = _for_fy(filters, year)
    where, params = _build_sales_where(f)
    sql = f"""
    SELECT COALESCE(NULLIF(s.sales_channel, ''), 'UNKNOWN') AS channel,
           SUM(CAST(s.net_sale_amount AS FLOAT)) AS net,
           SUM(CAST(s.qty AS FLOAT)) AS qty,
           COUNT(DISTINCT s.site_id) AS stores
    {_sales_from_sql(where)}
    GROUP BY COALESCE(NULLIF(s.sales_channel, ''), 'UNKNOWN')
    ORDER BY net DESC
    """
    rows = _fetchall(sql, params)
    return [
        {"channel": str(r[0]), "net_sales": float(r[1] or 0), "units_sold": float(r[2] or 0), "store_count": int(r[3] or 0)}
        for r in rows
    ]


# ---------- Target ----------

def sql_load_target_df(
    *,
    site_id: str = "all",
    site_ids: str | None = None,
    channel: str = "all",
    date_from: str = "",
    date_to: str = "",
) -> pd.DataFrame:
    clauses = ["1=1"]
    params: list[Any] = []
    cs = _db_case_sensitive()
    sid = str(site_id or "all").strip()
    ids = [sid] if sid.lower() != "all" else _csv_vals(site_ids)
    if ids:
        operand, p = _list_sql(sorted({i.upper() for i in ids}))
        clauses.append(f"{'UPPER(site_id)' if cs else 'site_id'} IN {operand}")
        params.extend(p)
    ch = str(channel or "all").strip().upper()
    if ch and ch != "ALL":
        channels = _csv_vals(ch)
        if channels:
            ph = ",".join("?" for _ in channels)
            clauses.append(f"{'UPPER(format_name)' if cs else 'format_name'} IN ({ph})")
            params.extend([c.upper() for c in channels])
    if date_from:
        clauses.append("target_date >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("target_date <= ?")
        params.append(date_to)
    sql = f"""
    SELECT target_date, site_id, store_name, format_name, target_amount
    FROM stockpulse.target_lfs
    WHERE {' AND '.join(clauses)}
    """
    df = _fetch_df(sql, params, columns=["date", "site_code", "store_name", "format", "target"])
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["site_code"] = df["site_code"].astype(str).str.strip().str.upper()
    df["store_name"] = df["store_name"].fillna("").astype(str)
    df["format"] = df["format"].fillna("").astype(str)
    df["target"] = pd.to_numeric(df["target"], errors="coerce").fillna(0.0)
    return df


def sql_achieved_sales_by_site_date(
    *,
    site_ids: list[str] | None = None,
    date_from: str | date | None = None,
    date_to: str | date | None = None,
) -> pd.DataFrame:
    clauses = ["1=1"]
    params: list[Any] = []
    if site_ids:
        operand, p = _list_sql(sorted({str(s).strip().upper() for s in site_ids if str(s).strip()}))
        clauses.append(f"{'UPPER(site_id)' if _db_case_sensitive() else 'site_id'} IN {operand}")
        params.extend(p)
    if date_from:
        clauses.append("sale_date >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("sale_date <= ?")
        params.append(date_to)
    sql = f"""
    SELECT sale_date, site_id, SUM(CAST(net_sale_amount AS FLOAT)) AS net_sale_amount
    FROM stockpulse.sales_fact
    WHERE {' AND '.join(clauses)}
    GROUP BY sale_date, site_id
    """
    return _fetch_df(sql, params, columns=["date", "site_code", "net_sale_amount"])


# ---------- Analyzer GROUP BY ----------

_SQL_SUPPORTED_DIMS = frozenset(_DIM_SQL.keys())


def sql_analyzer_dims_supported(dims: Iterable[str]) -> bool:
    return all(d in _SQL_SUPPORTED_DIMS for d in dims)


def sql_analyzer_grouped(
    *,
    filters: dict[str, Any],
    group_cols: list[str],
    measures: list[str] | None = None,
) -> pd.DataFrame:
    measures = measures or ["sales", "quantity", "record_count"]
    bad = [c for c in group_cols if c not in _DIM_SQL]
    if bad:
        raise ValueError(f"Unsupported SQL analyzer dims: {bad}")
    where, params = _build_sales_where(filters)
    select_parts = [f"{_DIM_SQL[c]} AS [{c}]" for c in group_cols]
    for m in measures:
        expr = _MEASURE_SQL.get(m)
        if not expr:
            continue
        select_parts.append(f"{expr} AS [{m}]")
    if not select_parts:
        select_parts = ["COUNT_BIG(*) AS [record_count]"]
    group_sql = ""
    if group_cols:
        group_exprs = [_DIM_SQL[c] for c in group_cols]
        group_sql = f" GROUP BY {', '.join(group_exprs)}"
    sql = f"""
    SELECT {", ".join(select_parts)}
    {_sales_from_sql(where)}
    {group_sql}
    """
    cols = list(group_cols) + [m for m in measures if m in _MEASURE_SQL]
    return _fetch_df(sql, params, columns=cols)


def try_execute_pivot_azure_sql(
    *,
    rows: list[str],
    columns: list[str],
    value_fields: list[str],
    filters: dict[str, Any],
    year: int,
) -> dict[str, Any] | None:
    """Build a simple Analyzer pivot payload from SQL GROUP BY. None → fallback."""
    row_dims = [r for r in rows if r]
    col_dim = columns[0] if columns else ""
    all_dims = list(row_dims) + ([col_dim] if col_dim else [])
    if not sql_analyzer_dims_supported(all_dims):
        return None
    want = set(value_fields or ["sales", "quantity"])
    # LY / growth / SOH / MDQ / ASP measures are built by the pandas path. Returning a
    # SQL pivot without them would show blank or 0 columns, so let the caller fall back.
    sql_measures = {"sales", "quantity", "gross_sales", "record_count"}
    if want - sql_measures:
        logger.info("Analyzer SQL pivot skipped: unsupported measures %s", sorted(want - sql_measures))
        return None
    measures = [m for m in ("sales", "quantity", "gross_sales", "record_count") if m in want or m in ("sales", "quantity")]
    if "sales" not in measures:
        measures.insert(0, "sales")
    if "quantity" not in measures:
        measures.append("quantity")
    if "record_count" not in measures:
        measures.append("record_count")

    f = {k: v for k, v in (filters or {}).items() if v is not None and v != "" and v != []}
    unknown = set(f) - SQL_FILTER_KEYS  # e.g. brand, size, site_id_name, from_date, date_range
    if unknown:
        logger.info("Analyzer SQL pivot skipped: unsupported filters %s", sorted(unknown))
        return None
    # FY bounds the default view; an explicit timeline (month / date / week …) overrides it.
    f["fy_years"] = [int(year)]
    if "sales_channel" in f and "channel" not in f:
        f["channel"] = f.pop("sales_channel")

    t0 = time.perf_counter()
    # Grand total
    grand_df = sql_analyzer_grouped(filters=f, group_cols=[], measures=measures)
    grand = {
        "sales": float(grand_df["sales"].sum()) if not grand_df.empty and "sales" in grand_df.columns else 0.0,
        "quantity": float(grand_df["quantity"].sum()) if not grand_df.empty and "quantity" in grand_df.columns else 0.0,
        "record_count": int(grand_df["record_count"].sum()) if not grand_df.empty and "record_count" in grand_df.columns else 0,
    }

    # Depth maps
    depth_frames: dict[int, pd.DataFrame] = {0: grand_df}
    for depth in range(1, len(row_dims) + 1):
        gcols = row_dims[:depth]
        depth_frames[depth] = sql_analyzer_grouped(filters=f, group_cols=gcols, measures=measures)

    split_df = None
    split_keys: list[str] = []
    if col_dim:
        split_cols = row_dims + [col_dim] if row_dims else [col_dim]
        split_df = sql_analyzer_grouped(filters=f, group_cols=split_cols, measures=measures)
        if col_dim in (split_df.columns if split_df is not None else []):
            split_keys = sorted(
                {
                    str(v)
                    for v in split_df[col_dim].dropna().unique()
                    if str(v).strip() and str(v).lower() != "nan"
                }
            )[:30]

    tree = _build_simple_tree(depth_frames, row_dims, measures=measures, split_df=split_df, col_dim=col_dim, split_keys=split_keys)
    elapsed = (time.perf_counter() - t0) * 1000.0
    return {
        "ok": True,
        "rows": tree,
        "period": {},
        "split_col_keys": split_keys,
        "split_col_dim": col_dim or "",
        "grand_total": grand,
        "total_records": int(grand.get("record_count") or 0),
        "dimensions": row_dims,
        "measures": [{"field": m, "aggregation": "sum", "custom_label": ""} for m in measures if m != "record_count"],
        "query_engine": _SOURCE,
        "engine_diag": {"engine": _SOURCE, "total_ms": round(elapsed, 1)},
    }


def _build_simple_tree(
    depth_frames: dict[int, pd.DataFrame],
    row_dims: list[str],
    *,
    measures: list[str],
    split_df: pd.DataFrame | None,
    col_dim: str,
    split_keys: list[str],
) -> list[dict[str, Any]]:
    if not row_dims:
        # single grand node already represented by caller
        return []

    leaf_depth = len(row_dims)
    leaf_df = depth_frames.get(leaf_depth)
    if leaf_df is None or leaf_df.empty:
        return []

    def _metrics(row: pd.Series) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for m in measures:
            if m not in row.index:
                continue
            val = row[m]
            if m == "record_count":
                out[m] = int(val or 0)
            else:
                out[m] = float(val or 0)
        return out

    # Index split rows once by (row path) → {col value: metrics}; was a full mask per leaf.
    split_index: dict[tuple[str, ...], dict[str, Any]] = {}
    if split_df is not None and col_dim and split_keys:
        wanted = set(split_keys)
        for _, srow in split_df.iterrows():
            ck = str(srow[col_dim])
            if ck not in wanted:
                continue
            path = tuple(str(srow[d]) for d in row_dims)
            split_index.setdefault(path, {}).setdefault(ck, _metrics(srow))

    # Build nested dict tree
    root: dict[str, Any] = {}
    for _, r in leaf_df.iterrows():
        cursor = root
        path_vals = [str(r[d]) if d in r.index and pd.notna(r[d]) else "" for d in row_dims]
        for i, dim in enumerate(row_dims):
            key = path_vals[i]
            if key not in cursor:
                node = {
                    "key": key,
                    "label": key,
                    "dimension": dim,
                    "metrics": {},
                    "children": {},
                    "split": {},
                }
                cursor[key] = node
            node = cursor[key]
            if i == leaf_depth - 1:
                node["metrics"] = _metrics(r)
                if split_index:
                    raw_path = tuple(str(r[d]) for d in row_dims)
                    node["split"] = dict(split_index.get(raw_path, {}))
            cursor = node["children"]

    # Fill parent metrics from depth frames
    for depth in range(1, leaf_depth):
        df = depth_frames.get(depth)
        if df is None or df.empty:
            continue
        gcols = row_dims[:depth]
        for _, r in df.iterrows():
            cursor = root
            node = None
            for d in gcols:
                key = str(r[d]) if pd.notna(r[d]) else ""
                node = cursor.get(key)
                if node is None:
                    break
                cursor = node["children"]
            if node is not None and not node.get("metrics"):
                node["metrics"] = _metrics(r)

    def _to_list(level: dict[str, Any]) -> list[dict[str, Any]]:
        out = []
        for _k, node in sorted(level.items(), key=lambda kv: str(kv[0]).casefold()):
            children = _to_list(node.get("children") or {})
            item = {
                "key": node["key"],
                "label": node["label"],
                "dimension": node["dimension"],
                "metrics": node.get("metrics") or {},
                "children": children,
            }
            if node.get("split"):
                item["split"] = node["split"]
            out.append(item)
        return out

    return _to_list(root)

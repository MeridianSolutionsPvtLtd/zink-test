"""
Optional DuckDB analytics query engine for dashboard filter/aggregation.

Controlled by ANALYTICS_QUERY_ENGINE=pandas|duckdb (default: pandas).
Queries existing published FY Parquet files; never modifies pipeline outputs.
Pandas remains the validated fallback path.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import pandas as pd

logger = logging.getLogger(__name__)

# ── Config ───────────────────────────────────────────────────────────────────
_ENGINE_ENV = "ANALYTICS_QUERY_ENGINE"
_DEFAULT_ENGINE = "pandas"
_DUCK_THREADS = max(1, min(2, int(os.environ.get("ANALYTICS_DUCKDB_THREADS", "2"))))
_DUCK_MEM = os.environ.get("ANALYTICS_DUCKDB_MEMORY", "2GB")
_QUERY_CACHE_TTL = int(os.environ.get("ANALYTICS_QUERY_CACHE_TTL_SECONDS", "120"))
_QUERY_CACHE_MAX = int(os.environ.get("ANALYTICS_QUERY_CACHE_MAX", "64"))
_SEM_MAX = max(1, int(os.environ.get("ANALYTICS_QUERY_CONCURRENCY", "2")))
_SEM_TIMEOUT_S = float(os.environ.get("ANALYTICS_QUERY_SEM_TIMEOUT_S", "60") or "60")


class DuckDBSemaphoreTimeout(TimeoutError):
    """No DuckDB concurrency slot within ANALYTICS_QUERY_SEM_TIMEOUT_S."""

_CHANNEL_ALIAS = {
    "PANTALOONS": "PT",
    "PANTALOONS (PT)": "PT",
    "SHOPPERS-STOP": "SSL",
    "SHOPPERS_STOP": "SSL",
    "SHOPPERS STOP": "SSL",
    "SHOPPERS STOP (SSL)": "SSL",
    "LIFESTYLE": "LS",
}
_CHANNEL_DISPLAY = {
    "PT": {"PT", "PANTALOONS", "PANTALOONS (PT)"},
    "SSL": {"SSL", "SHOPPERS STOP", "SHOPPERS STOP (SSL)", "SHOPPERS-STOP", "SHOPPERS_STOP"},
    "CENTRO": {"CENTRO"},
    "ICONIC": {"ICONIC"},
    "EBO": {"EBO"},
}

_conn_lock = threading.Lock()
_conn = None  # type: ignore[var-annotated]
_query_sem = threading.Semaphore(_SEM_MAX)
_result_cache: dict[str, tuple[float, str, pd.DataFrame]] = {}
_json_cache: dict[str, tuple[float, str, Any]] = {}
_result_cache_lock = threading.Lock()
_inflight: dict[str, threading.Event] = {}
_inflight_lock = threading.Lock()


def get_analytics_engine() -> str:
    """Return configured engine name: pandas | duckdb."""
    raw = (os.environ.get(_ENGINE_ENV) or _DEFAULT_ENGINE).strip().lower()
    if raw in ("duckdb", "duck", "ddb"):
        return "duckdb"
    return "pandas"


def duckdb_available() -> bool:
    try:
        import duckdb  # noqa: F401

        return True
    except ImportError:
        return False


def use_duckdb() -> bool:
    return get_analytics_engine() == "duckdb" and duckdb_available()


def dataset_version_for_years(years: list[int]) -> str:
    """Stable fingerprint of published FY parquet / source revision."""
    from api.sales_year_loader import sales_data_revision

    parts: list[str] = []
    for y in sorted(set(int(x) for x in years)):
        parts.append(f"{y}:{sales_data_revision(y)}")
        for p in resolve_fy_parquet_paths(y):
            try:
                st = p.stat()
                parts.append(f"{p.name}:{int(st.st_mtime_ns)}:{st.st_size}")
            except OSError:
                parts.append(p.name)
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]
    return digest


def resolve_fy_parquet_paths(year: int) -> list[Path]:
    """Locate existing FY sales parquet cache file(s) without loading into Pandas.

    Does not remap unsupported FY codes to the default dashboard year — missing
    years return an empty list so callers can fail closed instead of querying
    the wrong dataset.
    """
    from api.sales_year_loader import (
        _API_CACHE_DIR,
        _mtime_key,
        _parquet_path,
        _year_folder_paths,
        resolve_sales_master_path,
        supported_dashboard_years,
    )

    y = int(year)
    if y not in supported_dashboard_years():
        return []

    year_paths = _year_folder_paths(y, refresh=False)
    master = year_paths[0] if year_paths else resolve_sales_master_path()
    source_key = (
        "|".join(_mtime_key(p) for p in year_paths)
        if year_paths
        else (_mtime_key(master) if master else "combined_sales")
    )
    exact = _parquet_path(y, source_key)
    if exact.exists() and exact.stat().st_size > 0:
        return [exact]

    # Fallback: newest matching sales_year_{y}_*_v3.parquet
    pattern = f"sales_year_{y}_*_v3.parquet"
    candidates = sorted(
        _API_CACHE_DIR.glob(pattern),
        key=lambda p: p.stat().st_mtime if p.exists() else 0,
        reverse=True,
    )
    for c in candidates:
        if c.exists() and c.stat().st_size > 0:
            return [c]
    return []


def _get_conn():
    """Process-level DuckDB base connection (do not share across concurrent executes)."""
    global _conn
    if _conn is not None:
        return _conn
    with _conn_lock:
        if _conn is not None:
            return _conn
        import duckdb

        con = duckdb.connect(database=":memory:")
        con.execute(f"SET threads TO {_DUCK_THREADS}")
        con.execute(f"SET memory_limit='{_DUCK_MEM}'")
        try:
            con.execute("SET temp_directory='./data/duckdb_tmp'")
        except Exception:
            pass
        _conn = con
        logger.info(
            "DuckDB analytics engine ready (threads=%s memory_limit=%s concurrency=%s)",
            _DUCK_THREADS,
            _DUCK_MEM,
            _SEM_MAX,
        )
        return _conn


def _query_cursor():
    """Per-call cursor — concurrent execute on one connection raises ResourceInUseException."""
    base = _get_conn()
    cur = base.cursor()
    # Inherit session limits; cursor() is an independent connection to the same DB.
    try:
        cur.execute(f"SET threads TO {_DUCK_THREADS}")
        cur.execute(f"SET memory_limit='{_DUCK_MEM}'")
    except Exception:
        pass
    return cur


@contextmanager
def duckdb_query_session(
    timeout: float | None = None,
) -> Iterator[tuple[Any, dict[str, float]]]:
    """Acquire concurrency slot + thread-safe cursor; always release the semaphore.

    Yields ``(cursor, timing)`` where timing has semaphore_wait_ms and conn_acquire_ms.
    Raises DuckDBSemaphoreTimeout (distinct from SQL errors) when the slot wait expires.
    """
    wait_limit = float(_SEM_TIMEOUT_S if timeout is None else timeout)
    t_wait = time.perf_counter()
    acquired = _query_sem.acquire(timeout=wait_limit)
    wait_ms = round((time.perf_counter() - t_wait) * 1000.0, 1)
    if not acquired:
        logger.warning(
            "DuckDB semaphore timeout after %.1fms (limit=%s timeout_s=%.1f)",
            wait_ms,
            _SEM_MAX,
            wait_limit,
        )
        raise DuckDBSemaphoreTimeout(
            f"DuckDB concurrency limit timeout after {wait_ms}ms (limit={_SEM_MAX})"
        )
    timing: dict[str, float] = {"semaphore_wait_ms": wait_ms, "conn_acquire_ms": 0.0}
    try:
        t_conn = time.perf_counter()
        cur = _query_cursor()
        timing["conn_acquire_ms"] = round((time.perf_counter() - t_conn) * 1000.0, 1)
        yield cur, timing
    finally:
        _query_sem.release()


def _val_str(v: Any) -> str:
    if v is None:
        return ""
    if hasattr(v, "default"):
        d = getattr(v, "default", "")
        if d is None or "PydanticUndefined" in str(d) or "Ellipsis" in str(d):
            return ""
        return str(d).strip()
    try:
        return str(v).strip()
    except Exception:
        return ""


def _split_csv(raw: str) -> list[str]:
    return [x.strip() for x in raw.split(",") if x.strip()]


def _parse_fy_end_year(token: str) -> int | None:
    t = (token or "").strip().upper().replace(" ", "")
    if not t:
        return None
    m = re.match(r"^FY-?(\d{2,4})$", t)
    if m:
        n = int(m.group(1))
        return n if n >= 1000 else 2000 + n
    m = re.match(r"^(\d{4})-(\d{2})$", t)
    if m:
        return int(m.group(1)[:2] + m.group(2)) if int(m.group(1)) >= 1000 else int("20" + m.group(2))
    m = re.match(r"^(\d{4})-(\d{4})$", t)
    if m:
        return int(m.group(2))
    if t.isdigit() and len(t) in (2, 4):
        n = int(t)
        return n if n >= 1000 else 2000 + n
    return None


def _build_filter_sql(
    *,
    site_id: str = "",
    site_ids: str = "",
    channel: str = "",
    month: str = "",
    sku: str = "",
    season: str = "",
    barcode: str = "",
    barcodes: str = "",
    departments: str = "",
    sections: str = "",
    zones: str = "",
    store_grades: str = "",
    states: str = "",
    cities: str = "",
    search: str = "",
    exclude: frozenset[str] | None = None,
    available_cols: set[str] | None = None,
) -> tuple[str, list[Any]]:
    """Build WHERE clause fragments matching sales_dashboard._apply_filters semantics."""
    skip = exclude or frozenset()
    cols = available_cols or set()
    clauses: list[str] = []
    params: list[Any] = []

    def has(*names: str) -> bool:
        return any(n in cols for n in names) if cols else True

    ch_str = _val_str(channel)
    if "channel" not in skip and ch_str and ch_str.lower() not in ("", "all"):
        if ch_str.lower() == "__none__":
            clauses.append("1=0")
        else:
            raw_chs = [c.strip().upper() for c in ch_str.split(",") if c.strip()]
            resolved = {_CHANNEL_ALIAS.get(c, c) for c in raw_chs if c}
            match_vals: set[str] = set()
            for code in resolved:
                match_vals.update(_CHANNEL_DISPLAY.get(code, {code}))
                match_vals.add(code)
            if match_vals and has("sales_channel", "channel", "store_type"):
                ch_parts: list[str] = []
                for col in ("sales_channel", "channel", "store_type"):
                    if cols and col not in cols:
                        continue
                    placeholders = ", ".join(["?"] * len(match_vals))
                    ch_parts.append(
                        f"upper(trim(cast({col} AS VARCHAR))) IN ({placeholders})"
                    )
                    params.extend(sorted(match_vals))
                    for code in resolved:
                        if len(code) <= 6:
                            ch_parts.append(
                                f"regexp_matches(upper(trim(cast({col} AS VARCHAR))), ?)"
                            )
                            params.append(f"\\({re.escape(code)}\\)|^{re.escape(code)}$")
                if ch_parts:
                    clauses.append("(" + " OR ".join(ch_parts) + ")")

    site_ids_str = _val_str(site_ids)
    site_id_str = _val_str(site_id)
    wanted: set[str] = set()
    if "site_ids" not in skip and site_ids_str:
        if site_ids_str.lower() == "__none__":
            clauses.append("1=0")
        else:
            wanted = {s.strip().upper().replace(".0", "") for s in site_ids_str.split(",") if s.strip()}
    elif "site_id" not in skip and site_id_str and site_id_str.lower() not in ("", "all"):
        wanted = {site_id_str.upper().replace(".0", "")}

    if wanted and has(
        "site_code", "site_manual_code", "site_short_code", "site_id", "site_id_name"
    ):
        id_parts: list[str] = []
        placeholders = ", ".join(["?"] * len(wanted))
        wanted_sorted = sorted(wanted)
        for col in (
            "site_code",
            "site_manual_code",
            "site_short_code",
            "site_id",
            "site_id_name",
        ):
            if cols and col not in cols:
                continue
            id_parts.append(
                f"regexp_replace(upper(trim(cast({col} AS VARCHAR))), '\\.0$', '') "
                f"IN ({placeholders})"
            )
            params.extend(wanted_sorted)
        if (not cols) or ("site_id" in cols):
            id_parts.append(
                f"regexp_extract(upper(cast(site_id AS VARCHAR)), "
                f"'\\(([A-Z]?\\d+)\\)\\s*$', 1) IN ({placeholders})"
            )
            params.extend(wanted_sorted)
            id_parts.append(
                f"regexp_extract(upper(cast(site_id AS VARCHAR)), "
                f"'-([A-Z]?\\d+)\\s*$', 1) IN ({placeholders})"
            )
            params.extend(wanted_sorted)
        if id_parts:
            clauses.append("(" + " OR ".join(id_parts) + ")")

    month_str = _val_str(month)
    if "month" not in skip and month_str and month_str.lower() not in ("", "all") and has("month_key"):
        clauses.append("cast(month_key AS VARCHAR) = ?")
        params.append(month_str)

    sku_str = _val_str(sku)
    if "sku" not in skip and sku_str and sku_str.lower() not in ("", "all") and has("sku"):
        allowed = [v.strip().upper() for v in sku_str.split(",") if v.strip()]
        if allowed:
            placeholders = ", ".join(["?"] * len(allowed))
            clauses.append(f"upper(trim(cast(sku AS VARCHAR))) IN ({placeholders})")
            params.extend(allowed)

    season_str = _val_str(season)
    if "season" not in skip and season_str and season_str.lower() not in ("", "all") and has("season"):
        seasons = [s.strip().upper() for s in season_str.split(",") if s.strip()]
        if seasons:
            placeholders = ", ".join(["?"] * len(seasons))
            clauses.append(f"upper(trim(cast(season AS VARCHAR))) IN ({placeholders})")
            params.extend(seasons)

    barcodes_str = _val_str(barcodes)
    barcode_str = _val_str(barcode)
    if "barcodes" not in skip and barcodes_str:
        if barcodes_str.lower() == "__none__":
            clauses.append("1=0")
        else:
            b_set = [b.strip().upper().replace(".0", "") for b in barcodes_str.split(",") if b.strip()]
            if b_set and has("barcode"):
                placeholders = ", ".join(["?"] * len(b_set))
                clauses.append(
                    f"regexp_replace(upper(trim(cast(barcode AS VARCHAR))), '\\.0$', '') "
                    f"IN ({placeholders})"
                )
                params.extend(b_set)
    elif "barcode" not in skip and barcode_str and barcode_str.lower() not in ("", "all") and has("barcode"):
        clauses.append(
            "regexp_replace(upper(trim(cast(barcode AS VARCHAR))), '\\.0$', '') = ?"
        )
        params.append(barcode_str.upper().replace(".0", ""))

    dept_str = _val_str(departments)
    if "departments" not in skip and dept_str:
        if dept_str.lower() == "__none__":
            clauses.append("1=0")
        elif has("department"):
            allowed = _split_csv(dept_str)
            if allowed:
                placeholders = ", ".join(["?"] * len(allowed))
                clauses.append(f"trim(cast(department AS VARCHAR)) IN ({placeholders})")
                params.extend(allowed)

    sec_str = _val_str(sections)
    if "sections" not in skip and sec_str:
        if sec_str.lower() == "__none__":
            clauses.append("1=0")
        elif has("section"):
            allowed = _split_csv(sec_str)
            if allowed:
                placeholders = ", ".join(["?"] * len(allowed))
                clauses.append(f"trim(cast(section AS VARCHAR)) IN ({placeholders})")
                params.extend(allowed)

    zones_str = _val_str(zones)
    if "zones" not in skip and zones_str:
        if zones_str.lower() == "__none__":
            clauses.append("1=0")
        elif has("zone"):
            allowed = [x.strip().upper() for x in zones_str.split(",") if x.strip()]
            if allowed:
                placeholders = ", ".join(["?"] * len(allowed))
                clauses.append(f"upper(trim(cast(zone AS VARCHAR))) IN ({placeholders})")
                params.extend(allowed)

    sg_str = _val_str(store_grades)
    if "store_grades" not in skip and sg_str:
        if sg_str.lower() == "__none__":
            clauses.append("1=0")
        elif has("store_grade"):
            allowed = _split_csv(sg_str)
            if allowed:
                placeholders = ", ".join(["?"] * len(allowed))
                clauses.append(f"trim(cast(store_grade AS VARCHAR)) IN ({placeholders})")
                params.extend(allowed)

    states_str = _val_str(states)
    if "states" not in skip and states_str:
        if states_str.lower() == "__none__":
            clauses.append("1=0")
        else:
            allowed = _split_csv(states_str)
            state_col = "state" if (not cols or "state" in cols) else (
                "state_name" if (not cols or "state_name" in cols) else None
            )
            if allowed and state_col:
                placeholders = ", ".join(["?"] * len(allowed))
                clauses.append(f"trim(cast({state_col} AS VARCHAR)) IN ({placeholders})")
                params.extend(allowed)

    cities_str = _val_str(cities)
    if "cities" not in skip and cities_str:
        if cities_str.lower() == "__none__":
            clauses.append("1=0")
        else:
            allowed = _split_csv(cities_str)
            city_col = "city" if (not cols or "city" in cols) else (
                "city_name" if (not cols or "city_name" in cols) else None
            )
            if allowed and city_col:
                placeholders = ", ".join(["?"] * len(allowed))
                clauses.append(f"trim(cast({city_col} AS VARCHAR)) IN ({placeholders})")
                params.extend(allowed)

    q_str = _val_str(search)
    if "search" not in skip and q_str:
        q = f"%{q_str.lower()}%"
        search_parts = [
            "lower(cast(site_id AS VARCHAR)) LIKE ?",
            "lower(cast(site_name AS VARCHAR)) LIKE ?",
            "lower(cast(sku AS VARCHAR)) LIKE ?",
            "lower(cast(barcode AS VARCHAR)) LIKE ?",
            "lower(cast(season AS VARCHAR)) LIKE ?",
        ]
        params.extend([q] * 5)
        for extra in ("customer_name", "city", "state", "zone", "store_type"):
            if not cols or extra in cols:
                search_parts.append(f"lower(cast({extra} AS VARCHAR)) LIKE ?")
                params.append(q)
        clauses.append("(" + " OR ".join(search_parts) + ")")

    if not clauses:
        return "", params
    return " AND ".join(clauses), params


def _build_timeline_sql(
    *,
    timeline_mode: str | None,
    timeline_values: str | None,
) -> tuple[str, list[Any]]:
    """SQL predicates matching sales_dashboard._apply_timeline_filter."""
    if not timeline_values or not str(timeline_values).strip():
        return "", []
    mode = (timeline_mode or "month").strip().lower()
    values = [v.strip() for v in str(timeline_values).split(",") if v.strip()]
    if not values:
        return "", []

    clauses: list[str] = ["date IS NOT NULL"]
    params: list[Any] = []

    if mode == "date":
        from_d = values[0] if values else ""
        to_d = values[1] if len(values) > 1 else from_d
        if from_d:
            clauses.append("CAST(date AS TIMESTAMP) >= CAST(? AS TIMESTAMP)")
            params.append(from_d)
        if to_d:
            clauses.append("CAST(date AS TIMESTAMP) < CAST(? AS TIMESTAMP) + INTERVAL 1 DAY")
            params.append(to_d)
        return " AND ".join(clauses), params

    if mode == "year":
        years = [int(v) for v in values if v.isdigit()]
        if years:
            placeholders = ", ".join(["?"] * len(years))
            clauses.append(f"year(CAST(date AS TIMESTAMP)) IN ({placeholders})")
            params.extend(years)
        return " AND ".join(clauses), params

    if mode == "month":
        placeholders = ", ".join(["?"] * len(values))
        clauses.append(
            f"strftime(CAST(date AS TIMESTAMP), '%Y-%m') IN ({placeholders})"
        )
        params.extend(values)
        return " AND ".join(clauses), params

    if mode == "day":
        # JS DOW: Sun=0 … Sat=6; DuckDB isodow: Mon=1 … Sun=7 → (isodow % 7)
        # Map labels via CASE
        label_map = {"Sun": 0, "Mon": 1, "Tue": 2, "Wed": 3, "Thu": 4, "Fri": 5, "Sat": 6}
        nums = [label_map[v] for v in values if v in label_map]
        if nums:
            placeholders = ", ".join(["?"] * len(nums))
            clauses.append(
                f"(CAST(strftime(CAST(date AS TIMESTAMP), '%w') AS INTEGER)) IN ({placeholders})"
            )
            params.extend(nums)
        return " AND ".join(clauses), params

    if mode in ("fy_year", "fy", "financial_year"):
        target_keys = {v.upper().replace(" ", "") for v in values}
        allowed: set[int] = set()
        for tk in target_keys:
            parsed = _parse_fy_end_year(tk)
            if parsed is not None:
                allowed.add(parsed)
        if allowed:
            placeholders = ", ".join(["?"] * len(allowed))
            clauses.append(
                f"(year(CAST(date AS TIMESTAMP)) + "
                f"CASE WHEN month(CAST(date AS TIMESTAMP)) >= 4 THEN 1 ELSE 0 END) "
                f"IN ({placeholders})"
            )
            params.extend(sorted(allowed))
        return " AND ".join(clauses), params

    if mode == "fy_week":
        try:
            from api.financial_calendar import selected_week_dates

            dates = selected_week_dates(",".join(values))
            if dates is not None and len(dates):
                ds = [pd.Timestamp(d).strftime("%Y-%m-%d") for d in dates]
                placeholders = ", ".join(["?"] * len(ds))
                clauses.append(
                    f"CAST(date_trunc('day', CAST(date AS TIMESTAMP)) AS DATE) "
                    f"IN ({placeholders})"
                )
                params.extend(ds)
        except Exception as exc:
            logger.warning("DuckDB fy_week timeline skipped: %s", exc)
        return " AND ".join(clauses), params

    # event mode: fall through — caller may apply Pandas post-filter
    return " AND ".join(clauses), params


def _parquet_relation_sql(paths: list[Path]) -> str:
    if len(paths) == 1:
        p = str(paths[0]).replace("'", "''")
        return f"read_parquet('{p}')"
    quoted = ", ".join("'" + str(p).replace("'", "''") + "'" for p in paths)
    return f"read_parquet([{quoted}], union_by_name=true)"


def _probe_columns(paths: list[Path]) -> set[str]:
    con = _get_conn()
    rel = _parquet_relation_sql(paths)
    with _conn_lock:
        df = con.execute(f"SELECT * FROM {rel} LIMIT 0").fetchdf()
    return set(str(c) for c in df.columns)


def _cache_get(key: str, version: str) -> pd.DataFrame | None:
    with _result_cache_lock:
        hit = _result_cache.get(key)
        if not hit:
            return None
        ts, ver, df = hit
        if ver != version or (time.time() - ts) > _QUERY_CACHE_TTL:
            _result_cache.pop(key, None)
            return None
        return df


def _cache_put(key: str, version: str, df: pd.DataFrame) -> None:
    with _result_cache_lock:
        _result_cache[key] = (time.time(), version, df)
        if len(_result_cache) > _QUERY_CACHE_MAX:
            oldest = min(_result_cache, key=lambda k: _result_cache[k][0])
            _result_cache.pop(oldest, None)


def normalize_filter_cache_key(parts: dict[str, Any]) -> str:
    """Order-independent filter key for equivalent multi-selects."""
    items: list[str] = []
    for k in sorted(parts.keys()):
        v = parts[k]
        if v is None:
            continue
        s = str(v).strip()
        if not s or s.lower() in ("all",):
            continue
        if "," in s:
            toks = sorted({t.strip().upper() for t in s.split(",") if t.strip()})
            s = ",".join(toks)
        else:
            s = s.upper() if k in (
                "season", "sku", "zones", "channel", "site_id", "site_ids",
                "barcode", "barcodes",
            ) else s
        items.append(f"{k}={s}")
    return "|".join(items)


def load_filtered_sales_duckdb(
    *,
    fy_years: list[int],
    site_id: str = "",
    site_ids: str = "",
    channel: str = "",
    month: str = "",
    sku: str = "",
    season: str = "",
    barcode: str = "",
    barcodes: str = "",
    departments: str = "",
    sections: str = "",
    zones: str = "",
    store_grades: str = "",
    states: str = "",
    cities: str = "",
    search: str = "",
    timeline_mode: str = "",
    timeline_values: str = "",
    exclude: frozenset[str] | None = None,
    force_refresh: bool = False,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """
    Load filtered sales rows from FY parquet via DuckDB.

    Returns (dataframe, diagnostics). Raises on hard failure so callers can fall back.
    """
    if not duckdb_available():
        raise RuntimeError("duckdb package not installed")

    years = sorted({int(y) for y in fy_years})
    paths: list[Path] = []
    for y in years:
        paths.extend(resolve_fy_parquet_paths(y))
    if not paths:
        raise FileNotFoundError(f"No FY parquet for years={years}")

    version = dataset_version_for_years(years)
    filt_key = normalize_filter_cache_key(
        {
            "years": ",".join(str(y) for y in years),
            "site_id": site_id,
            "site_ids": site_ids,
            "channel": channel,
            "month": month,
            "sku": sku,
            "season": season,
            "barcode": barcode,
            "barcodes": barcodes,
            "departments": departments,
            "sections": sections,
            "zones": zones,
            "store_grades": store_grades,
            "states": states,
            "cities": cities,
            "search": search,
            "tl_mode": timeline_mode,
            "tl_values": timeline_values,
            "exclude": ",".join(sorted(exclude or ())),
        }
    )
    cache_key = f"duck-filt|{version}|{hashlib.sha256(filt_key.encode()).hexdigest()[:20]}"

    diag: dict[str, Any] = {
        "engine": "duckdb",
        "dataset_version": version,
        "parquet_paths": [str(p) for p in paths],
        "fy_years": years,
        "cache_hit": False,
        "query_ms": 0.0,
        "rows": 0,
    }

    if not force_refresh:
        cached = _cache_get(cache_key, version)
        if cached is not None:
            diag["cache_hit"] = True
            diag["rows"] = int(len(cached))
            return cached.copy(deep=False), diag

    # Single-flight for identical keys
    leader = False
    waiter: threading.Event | None = None
    if not force_refresh:
        with _inflight_lock:
            existing = _inflight.get(cache_key)
            if existing is not None:
                waiter = existing
            else:
                waiter = threading.Event()
                _inflight[cache_key] = waiter
                leader = True
        if not leader and waiter is not None:
            waiter.wait(timeout=180.0)
            cached = _cache_get(cache_key, version)
            if cached is not None:
                diag["cache_hit"] = True
                diag["rows"] = int(len(cached))
                return cached.copy(deep=False), diag

    try:
        cols = _probe_columns(paths)
        where_f, params_f = _build_filter_sql(
            site_id=site_id,
            site_ids=site_ids,
            channel=channel,
            month=month,
            sku=sku,
            season=season,
            barcode=barcode,
            barcodes=barcodes,
            departments=departments,
            sections=sections,
            zones=zones,
            store_grades=store_grades,
            states=states,
            cities=cities,
            search=search,
            exclude=exclude,
            available_cols=cols,
        )
        where_t, params_t = _build_timeline_sql(
            timeline_mode=timeline_mode,
            timeline_values=timeline_values,
        )
        wheres = [w for w in (where_f, where_t) if w]
        where_sql = (" WHERE " + " AND ".join(wheres)) if wheres else ""
        params = params_f + params_t
        rel = _parquet_relation_sql(paths)
        sql = f"SELECT * FROM {rel}{where_sql}"

        t0 = time.perf_counter()
        con = _get_conn()
        acquired = _query_sem.acquire(timeout=180.0)
        if not acquired:
            raise TimeoutError("DuckDB query concurrency limit timeout")
        try:
            with _conn_lock:
                df = con.execute(sql, params).fetchdf()
        finally:
            _query_sem.release()
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        # Align dtypes expected by existing KPI helpers
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
        if "month_key" not in df.columns and "date" in df.columns:
            df["month_key"] = df["date"].dt.strftime("%Y-%m")

        # Event timeline still needs Python helper (rare)
        mode = (timeline_mode or "").strip().lower()
        if mode == "event" and timeline_values:
            from api.routes.sales_dashboard import _apply_timeline_filter

            df = _apply_timeline_filter(
                df, timeline_mode=timeline_mode, timeline_values=timeline_values
            )

        diag["query_ms"] = round(elapsed_ms, 2)
        diag["rows"] = int(len(df))
        diag["sql_where"] = where_sql[:500]
        _cache_put(cache_key, version, df)
        logger.info(
            "DuckDB filtered sales years=%s rows=%s query_ms=%.1f cache_key=%s",
            years,
            len(df),
            elapsed_ms,
            cache_key[:40],
        )
        return df, diag
    finally:
        if leader and waiter is not None:
            with _inflight_lock:
                _inflight.pop(cache_key, None)
            waiter.set()


def load_sales_page_duckdb(
    *,
    fy_years: list[int],
    page: int,
    page_size: int,
    sort_col: str,
    sort_dir: str,
    site_id: str = "",
    site_ids: str = "",
    channel: str = "",
    month: str = "",
    sku: str = "",
    season: str = "",
    barcode: str = "",
    barcodes: str = "",
    departments: str = "",
    sections: str = "",
    zones: str = "",
    store_grades: str = "",
    states: str = "",
    cities: str = "",
    search: str = "",
    timeline_mode: str = "",
    timeline_values: str = "",
) -> tuple[pd.DataFrame, int, dict[str, Any]]:
    """One sorted page plus the filtered row count. Does not read the rest of the file into pandas."""
    if not duckdb_available():
        raise RuntimeError("duckdb package not installed")
    mode = (timeline_mode or "").strip().lower()
    if mode == "event":
        raise RuntimeError("event timeline is not paged in SQL")

    paths, rel, where_sql, params, cols, version = _resolve_paths_and_filters(
        fy_years=fy_years,
        site_id=site_id,
        site_ids=site_ids,
        channel=channel,
        month=month,
        sku=sku,
        season=season,
        barcode=barcode,
        barcodes=barcodes,
        departments=departments,
        sections=sections,
        zones=zones,
        store_grades=store_grades,
        states=states,
        cities=cities,
        search=search,
        timeline_mode=timeline_mode,
        timeline_values=timeline_values,
    )
    order_col = sort_col if sort_col in cols else ("date" if "date" in cols else "")
    if order_col and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", order_col):
        order_col = ""
    direction = "ASC" if str(sort_dir or "").lower() == "asc" else "DESC"
    order_sql = f' ORDER BY "{order_col}" {direction} NULLS LAST' if order_col else ""
    limit = max(1, int(page_size))
    offset = max(0, (max(1, int(page)) - 1) * limit)

    t0 = time.perf_counter()
    total_row = _execute_one(f"SELECT COUNT(*) FROM {rel}{where_sql}", list(params))
    total = int(total_row[0] or 0) if total_row else 0
    df = _execute_df(
        f"SELECT * FROM {rel}{where_sql}{order_sql} LIMIT {limit} OFFSET {offset}",
        list(params),
    )
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    diag = {
        "engine": "duckdb",
        "dataset_version": version,
        "query_ms": round(elapsed_ms, 2),
        "rows": int(len(df)),
        "total": total,
        "paged": True,
    }
    logger.info(
        "DuckDB sales page years=%s page_rows=%s total=%s query_ms=%.1f",
        fy_years,
        len(df),
        total,
        elapsed_ms,
    )
    return df, total, diag


_PRODUCT_OPT_CACHE: dict[str, dict[str, Any]] = {}
_PRODUCT_OPT_LOCK = threading.Lock()


def _clean_distinct(values: list[Any], *, limit: int) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in values:
        text = str(raw or "").strip()
        if not text or text.lower() in {"nan", "none", "<na>"}:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= limit:
            break
    out.sort(key=str.casefold)
    return out


def _execute_all(statements: list[tuple[str, list[Any]]]) -> list[list[tuple]]:
    con = _get_conn()
    acquired = _query_sem.acquire(timeout=180.0)
    if not acquired:
        raise TimeoutError("DuckDB query concurrency limit timeout")
    try:
        with _conn_lock:
            return [con.execute(sql, params).fetchall() for sql, params in statements]
    finally:
        _query_sem.release()


def load_product_filter_options(
    *,
    fy_years: list[int],
    site_id: str = "",
    site_ids: str = "",
    channel: str = "",
    month: str = "",
    departments: str = "",
    sections: str = "",
    zones: str = "",
    store_grades: str = "",
    states: str = "",
    cities: str = "",
    timeline_mode: str = "",
    timeline_values: str = "",
    persist: bool = False,
) -> dict[str, Any]:
    """Distinct season / SKU / barcode lists. One cached column scan, not the sales table frame."""
    paths, rel, where_sql, params, cols, version = _resolve_paths_and_filters(
        fy_years=fy_years,
        site_id=site_id,
        site_ids=site_ids,
        channel=channel,
        month=month,
        departments=departments,
        sections=sections,
        zones=zones,
        store_grades=store_grades,
        states=states,
        cities=cities,
        timeline_mode=timeline_mode,
        timeline_values=timeline_values,
        exclude=frozenset({"sku", "season", "barcode", "barcodes", "search"}),
    )
    sig = hashlib.sha256(
        f"{version}|{where_sql}|{params}".encode("utf-8")
    ).hexdigest()[:24]
    with _PRODUCT_OPT_LOCK:
        cached = _PRODUCT_OPT_CACHE.get(sig)
    if cached is not None:
        return cached

    disk = Path(__file__).resolve().parents[2] / "data" / "api_cache" / f"product_filter_options_{sig}.json"
    if disk.exists() and disk.stat().st_size > 0:
        try:
            import json

            payload = json.loads(disk.read_text(encoding="utf-8"))
            if payload.get("ok") and isinstance(payload.get("skus"), list):
                with _PRODUCT_OPT_LOCK:
                    _PRODUCT_OPT_CACHE[sig] = payload
                return payload
        except Exception:
            logger.warning("product filter disk cache unreadable: %s", disk, exc_info=True)

    t0 = time.perf_counter()
    # Three single-column DISTINCT scans. A grouped (season, sku, barcode) triple
    # would be as large as the sales file.
    statements: list[tuple[str, list[Any]]] = []
    kind: list[str] = []
    for col, limit in (("season", 500), ("sku", 8000), ("barcode", 20000)):
        if col not in cols:
            continue
        extra = (
            f"trim(CAST(\"{col}\" AS VARCHAR)) <> '' "
            f"AND lower(trim(CAST(\"{col}\" AS VARCHAR))) NOT IN ('nan', 'none', '<na>')"
        )
        where = f"{where_sql} AND {extra}" if where_sql else f" WHERE {extra}"
        statements.append(
            (
                f'SELECT DISTINCT trim(CAST("{col}" AS VARCHAR)) AS v '
                f"FROM {rel}{where} ORDER BY 1 LIMIT {limit}",
                list(params),
            )
        )
        kind.append(col)
    fetched = _execute_all(statements) if statements else []
    by_col = {name: _clean_distinct([row[0] for row in rows], limit=8000 if name == "sku" else 20000 if name == "barcode" else 500) for name, rows in zip(kind, fetched)}
    seasons = by_col.get("season", [])
    skus = by_col.get("sku", [])
    barcodes = by_col.get("barcode", [])
    payload = {
        "ok": True,
        "seasons": [{"id": "all", "label": "All seasons"}] + [{"id": s, "label": s} for s in seasons],
        "skus": [{"id": "all", "label": "All SKUs"}] + [{"id": s, "label": s} for s in skus],
        "barcodes": barcodes,
        "dataset_version": version,
        "query_ms": round((time.perf_counter() - t0) * 1000.0, 1),
    }
    with _PRODUCT_OPT_LOCK:
        _PRODUCT_OPT_CACHE[sig] = payload
    if persist:
        try:
            import json

            disk.parent.mkdir(parents=True, exist_ok=True)
            disk.write_text(json.dumps(payload), encoding="utf-8")
        except Exception:
            logger.warning("product filter disk cache write failed", exc_info=True)
    logger.info(
        "Product filter options years=%s seasons=%s skus=%s barcodes=%s query_ms=%s",
        fy_years,
        len(seasons),
        len(skus),
        len(barcodes),
        payload["query_ms"],
    )
    return payload


def clear_analytics_query_cache() -> None:
    with _result_cache_lock:
        _result_cache.clear()
        _json_cache.clear()


def _json_cache_get(key: str, version: str) -> Any | None:
    with _result_cache_lock:
        hit = _json_cache.get(key)
        if not hit:
            return None
        ts, ver, payload = hit
        if ver != version or (time.time() - ts) > _QUERY_CACHE_TTL:
            _json_cache.pop(key, None)
            return None
        return payload


def _json_cache_put(key: str, version: str, payload: Any) -> None:
    with _result_cache_lock:
        _json_cache[key] = (time.time(), version, payload)
        if len(_json_cache) > _QUERY_CACHE_MAX:
            oldest = min(_json_cache, key=lambda k: _json_cache[k][0])
            _json_cache.pop(oldest, None)


def _resolve_paths_and_filters(
    *,
    fy_years: list[int],
    site_id: str = "",
    site_ids: str = "",
    channel: str = "",
    month: str = "",
    sku: str = "",
    season: str = "",
    barcode: str = "",
    barcodes: str = "",
    departments: str = "",
    sections: str = "",
    zones: str = "",
    store_grades: str = "",
    states: str = "",
    cities: str = "",
    search: str = "",
    timeline_mode: str = "",
    timeline_values: str = "",
    exclude: frozenset[str] | None = None,
) -> tuple[list[Path], str, str, list[Any], set[str], str]:
    """Return (paths, rel_sql, where_sql, params, cols, dataset_version)."""
    years = sorted({int(y) for y in fy_years})
    paths: list[Path] = []
    for y in years:
        paths.extend(resolve_fy_parquet_paths(y))
    if not paths:
        raise FileNotFoundError(f"No FY parquet for years={years}")
    version = dataset_version_for_years(years)
    cols = _probe_columns(paths)
    where_f, params_f = _build_filter_sql(
        site_id=site_id,
        site_ids=site_ids,
        channel=channel,
        month=month,
        sku=sku,
        season=season,
        barcode=barcode,
        barcodes=barcodes,
        departments=departments,
        sections=sections,
        zones=zones,
        store_grades=store_grades,
        states=states,
        cities=cities,
        search=search,
        exclude=exclude,
        available_cols=cols,
    )
    where_t, params_t = _build_timeline_sql(
        timeline_mode=timeline_mode,
        timeline_values=timeline_values,
    )
    wheres = [w for w in (where_f, where_t) if w]
    where_sql = (" WHERE " + " AND ".join(wheres)) if wheres else ""
    return paths, _parquet_relation_sql(paths), where_sql, params_f + params_t, cols, version


def _execute_df(sql: str, params: list[Any]) -> pd.DataFrame:
    con = _get_conn()
    acquired = _query_sem.acquire(timeout=180.0)
    if not acquired:
        raise TimeoutError("DuckDB query concurrency limit timeout")
    try:
        with _conn_lock:
            return con.execute(sql, params).fetchdf()
    finally:
        _query_sem.release()


def _execute_one(sql: str, params: list[Any]) -> tuple:
    con = _get_conn()
    acquired = _query_sem.acquire(timeout=180.0)
    if not acquired:
        raise TimeoutError("DuckDB query concurrency limit timeout")
    try:
        with _conn_lock:
            return con.execute(sql, params).fetchone()
    finally:
        _query_sem.release()


def _gross_expr(cols: set[str]) -> str:
    if "mrp_gross_val" in cols:
        return "COALESCE(CAST(mrp_gross_val AS DOUBLE), 0)"
    if "mrp" in cols and "qty" in cols:
        return (
            "COALESCE(CAST(mrp AS DOUBLE), 0) * COALESCE(CAST(qty AS DOUBLE), 0)"
        )
    return "0"


def _disc_pct_expr(cols: set[str]) -> str:
    if "discount_pct" in cols:
        return "COALESCE(CAST(discount_pct AS DOUBLE), 0)"
    # Derive from gross when needed
    g = _gross_expr(cols)
    return (
        f"CASE WHEN ({g}) > 0 THEN "
        f"(COALESCE(CAST(discount_amount AS DOUBLE), 0) / ({g}) * 100) ELSE 0 END"
    )


def aggregate_sales_kpis_duckdb(
    *,
    fy_years: list[int],
    site_id: str = "",
    site_ids: str = "",
    channel: str = "",
    month: str = "",
    sku: str = "",
    season: str = "",
    barcode: str = "",
    barcodes: str = "",
    departments: str = "",
    sections: str = "",
    zones: str = "",
    store_grades: str = "",
    states: str = "",
    cities: str = "",
    search: str = "",
    timeline_mode: str = "",
    timeline_values: str = "",
    force_refresh: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Aggregate KPI scalars in DuckDB without materializing row frames."""
    if not duckdb_available():
        raise RuntimeError("duckdb package not installed")

    paths, rel, where_sql, params, cols, version = _resolve_paths_and_filters(
        fy_years=fy_years,
        site_id=site_id,
        site_ids=site_ids,
        channel=channel,
        month=month,
        sku=sku,
        season=season,
        barcode=barcode,
        barcodes=barcodes,
        departments=departments,
        sections=sections,
        zones=zones,
        store_grades=store_grades,
        states=states,
        cities=cities,
        search=search,
        timeline_mode=timeline_mode,
        timeline_values=timeline_values,
    )
    cache_key = (
        "duck-kpi-agg|"
        + version
        + "|"
        + hashlib.sha256(
            normalize_filter_cache_key(
                {
                    "years": ",".join(str(y) for y in sorted(set(fy_years))),
                    "site_id": site_id,
                    "site_ids": site_ids,
                    "channel": channel,
                    "month": month,
                    "sku": sku,
                    "season": season,
                    "barcode": barcode,
                    "barcodes": barcodes,
                    "departments": departments,
                    "sections": sections,
                    "zones": zones,
                    "store_grades": store_grades,
                    "states": states,
                    "cities": cities,
                    "search": search,
                    "tl_mode": timeline_mode,
                    "tl_values": timeline_values,
                }
            ).encode()
        ).hexdigest()[:20]
    )
    diag: dict[str, Any] = {
        "engine": "duckdb",
        "dataset_version": version,
        "parquet_paths": [str(p) for p in paths],
        "cache_hit": False,
        "query_ms": 0.0,
    }
    if not force_refresh:
        hit = _json_cache_get(cache_key, version)
        if hit is not None:
            diag["cache_hit"] = True
            return hit, diag

    g = _gross_expr(cols)
    dp = _disc_pct_expr(cols)
    tax = (
        "COALESCE(CAST(tax_amount AS DOUBLE), 0)"
        if "tax_amount" in cols
        else "0"
    )
    # Project only columns needed for aggregates (avoid SELECT * materialization).
    proj_cols = [
        "CAST(qty AS DOUBLE) AS qty",
        "CAST(net_sale_amount AS DOUBLE) AS net_sale_amount",
        "CAST(discount_amount AS DOUBLE) AS discount_amount",
        f"({g}) AS gross_v",
        f"({tax}) AS tax_v",
        f"({dp}) AS disc_pct",
        "CAST(site_id AS VARCHAR) AS site_id",
        "CAST(date AS TIMESTAMP) AS date_ts",
    ]
    sql = f"""
    WITH scoped AS (
      SELECT {", ".join(proj_cols)}
      FROM {rel}{where_sql}
    ),
    agg AS (
      SELECT
        COALESCE(SUM(qty), 0) AS qty,
        COALESCE(SUM(net_sale_amount), 0) AS net,
        COALESCE(SUM(discount_amount), 0) AS disc,
        COALESCE(SUM(gross_v), 0) AS gross,
        COALESCE(SUM(tax_v), 0) AS tax_amt,
        COALESCE(AVG(disc_pct), 0) AS avg_disc,
        COUNT(*) AS line_count,
        COUNT(DISTINCT site_id) AS store_count,
        COUNT(DISTINCT CAST(date_trunc('day', date_ts) AS DATE)) AS day_count,
        MIN(date_ts) AS min_date,
        MAX(date_ts) AS max_date,
        COALESCE(SUM(CASE WHEN disc_pct <= 10.0 THEN net_sale_amount ELSE 0 END), 0) AS fp_sales,
        COALESCE(SUM(CASE WHEN disc_pct <= 10.0 THEN qty ELSE 0 END), 0) AS fp_qty,
        COALESCE(SUM(CASE WHEN disc_pct > 10.0 THEN net_sale_amount ELSE 0 END), 0) AS disc_sales,
        COALESCE(SUM(CASE WHEN disc_pct > 10.0 THEN qty ELSE 0 END), 0) AS disc_qty
      FROM scoped
    ),
    sites AS (
      SELECT site_id, SUM(net_sale_amount) AS net_sales
      FROM scoped
      WHERE site_id IS NOT NULL AND trim(site_id) <> ''
      GROUP BY 1
    )
    SELECT
      a.qty, a.net, a.disc, a.gross, a.tax_amt, a.avg_disc, a.line_count, a.store_count,
      a.day_count, a.min_date, a.max_date, a.fp_sales, a.fp_qty, a.disc_sales, a.disc_qty,
      COALESCE(
        (SELECT LIST(STRUCT_PACK(site_id := site_id, net_sales := net_sales)) FROM sites),
        []
      ) AS site_nets
    FROM agg a
    """
    t0 = time.perf_counter()
    row = _execute_one(sql, params)
    elapsed = (time.perf_counter() - t0) * 1000.0
    diag["query_ms"] = round(elapsed, 2)

    qty = float(row[0] or 0)
    net = float(row[1] or 0)
    disc = float(row[2] or 0)
    gross = float(row[3] or 0)
    tax_amt = float(row[4] or 0)
    avg_disc = float(row[5] or 0)
    line_count = int(row[6] or 0)
    store_count = int(row[7] or 0)
    day_count = int(row[8] or 0) or 1
    min_date = row[9]
    max_date = row[10]
    fp_sales = float(row[11] or 0)
    fp_qty = float(row[12] or 0)
    disc_sales = float(row[13] or 0)
    disc_qty = float(row[14] or 0)
    site_list = row[15] if len(row) > 15 else None
    site_nets: dict[str, float] = {}
    if site_list:
        for item in site_list:
            try:
                if isinstance(item, dict):
                    sid = str(item.get("site_id") or "").strip()
                    sn = float(item.get("net_sales") or 0)
                else:
                    # DuckDB struct may arrive as tuple/list
                    sid = str(item[0] if item is not None else "").strip()
                    sn = float(item[1] if item is not None else 0)
                if sid:
                    site_nets[sid] = sn
            except Exception:
                continue
    payload = {
        "qty": qty,
        "net": net,
        "disc": disc,
        "gross": gross,
        "tax_amt": tax_amt,
        "avg_disc": avg_disc,
        "line_count": line_count,
        "store_count": store_count,
        "day_count": day_count,
        "min_date": str(min_date) if min_date is not None else None,
        "max_date": str(max_date) if max_date is not None else None,
        "fp_sales": fp_sales,
        "fp_qty": fp_qty,
        "disc_sales": disc_sales,
        "disc_qty": disc_qty,
        "site_nets": site_nets,
    }
    _json_cache_put(cache_key, version, payload)
    logger.info(
        "DuckDB KPI aggregate rows=%s net=%.2f query_ms=%.1f",
        line_count,
        net,
        elapsed,
    )
    return payload, diag


def aggregate_sply_totals_duckdb(
    *,
    min_date: str | None,
    max_date: str | None,
    filter_kw: dict[str, Any] | None = None,
    timeline_mode: str = "",
    timeline_values: str = "",
) -> tuple[float, float, dict[str, Any]]:
    """SPLY prior net/qty only (date window −1 year), without loading LY frames into Pandas."""
    if not min_date or not max_date:
        return 0.0, 0.0, {"skipped": "no_dates"}

    filter_kw = dict(filter_kw or {})
    mapped_dates = None
    if timeline_mode == "fy_week" and timeline_values:
        from api.financial_calendar import selected_week_dates

        mapped_dates = selected_week_dates(timeline_values) - pd.DateOffset(years=1)

    if mapped_dates is not None and len(mapped_dates):
        ly_min = pd.Timestamp(mapped_dates.min())
        ly_max = pd.Timestamp(mapped_dates.max())
    else:
        ly_min = pd.Timestamp(min_date) - pd.DateOffset(years=1)
        ly_max = pd.Timestamp(max_date) - pd.DateOffset(years=1)

    def _fy_end(ts: pd.Timestamp) -> int:
        return int(ts.year + 1) if ts.month >= 4 else int(ts.year)

    y0, y1 = _fy_end(ly_min), _fy_end(ly_max)
    fy_years = list(range(min(y0, y1), max(y0, y1) + 1))

    # Season YoY: shift season codes −1 year for LY comparison
    season = str(filter_kw.get("season") or "")
    if season and season.lower() not in ("", "all"):
        try:
            from api.season_utils import shift_season_csv

            season = shift_season_csv(season, 1) or season
        except Exception:
            pass

    paths: list[Path] = []
    for y in fy_years:
        paths.extend(resolve_fy_parquet_paths(y))
    if not paths:
        return 0.0, 0.0, {"skipped": "no_ly_parquet", "fy_years": fy_years}

    cols = _probe_columns(paths)
    where_f, params_f = _build_filter_sql(
        site_id=str(filter_kw.get("site_id") or ""),
        site_ids=str(filter_kw.get("site_ids") or ""),
        channel=str(filter_kw.get("channel") or ""),
        month="",  # SPLY excludes month filter (same as Pandas)
        sku=str(filter_kw.get("sku") or ""),
        season=season,
        barcode=str(filter_kw.get("barcode") or ""),
        barcodes=str(filter_kw.get("barcodes") or ""),
        departments=str(filter_kw.get("departments") or ""),
        sections=str(filter_kw.get("sections") or ""),
        zones=str(filter_kw.get("zones") or ""),
        store_grades=str(filter_kw.get("store_grades") or ""),
        states=str(filter_kw.get("states") or ""),
        cities=str(filter_kw.get("cities") or ""),
        search=str(filter_kw.get("search") or ""),
        exclude=frozenset({"month"}),
        available_cols=cols,
    )
    date_clause = (
        "CAST(date AS TIMESTAMP) >= CAST(? AS TIMESTAMP) AND "
        "CAST(date AS TIMESTAMP) <= CAST(? AS TIMESTAMP)"
    )
    params_d = [ly_min.strftime("%Y-%m-%d %H:%M:%S"), ly_max.strftime("%Y-%m-%d %H:%M:%S")]
    if mapped_dates is not None and len(mapped_dates):
        ds = [pd.Timestamp(d).strftime("%Y-%m-%d") for d in mapped_dates]
        placeholders = ", ".join(["?"] * len(ds))
        date_clause = (
            f"CAST(date_trunc('day', CAST(date AS TIMESTAMP)) AS DATE) IN ({placeholders})"
        )
        params_d = ds

    wheres = [w for w in (where_f, date_clause) if w]
    where_sql = " WHERE " + " AND ".join(wheres)
    params = params_f + params_d
    rel = _parquet_relation_sql(paths)
    sql = f"""
    SELECT
      COALESCE(SUM(CAST(net_sale_amount AS DOUBLE)), 0) AS net,
      COALESCE(SUM(CAST(qty AS DOUBLE)), 0) AS qty
    FROM {rel}{where_sql}
    """
    t0 = time.perf_counter()
    row = _execute_one(sql, params)
    ms = (time.perf_counter() - t0) * 1000.0
    prior_net = float(row[0] or 0)
    prior_qty = float(row[1] or 0)
    return prior_net, prior_qty, {
        "query_ms": round(ms, 2),
        "fy_years": fy_years,
        "ly_min": str(ly_min.date()),
        "ly_max": str(ly_max.date()),
        "prior_net": prior_net,
        "prior_qty": prior_qty,
    }


def aggregate_insights_duckdb(
    *,
    fy_years: list[int],
    site_id: str = "",
    site_ids: str = "",
    channel: str = "",
    month: str = "",
    sku: str = "",
    season: str = "",
    barcode: str = "",
    barcodes: str = "",
    departments: str = "",
    sections: str = "",
    zones: str = "",
    store_grades: str = "",
    states: str = "",
    cities: str = "",
    search: str = "",
    timeline_mode: str = "",
    timeline_values: str = "",
    limit: int = 10,
    light: bool = False,
    force_refresh: bool = False,
    cards: set[str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Insights trends / tops via DuckDB group-bys (no full-frame Pandas groupby).

    ``cards`` limits work to the slices currently on screen, for example
    ``trend_month``, ``breakdown_zone``, ``channel``. ``None`` keeps the full set.
    """
    paths, rel, where_sql, params, cols, version = _resolve_paths_and_filters(
        fy_years=fy_years,
        site_id=site_id,
        site_ids=site_ids,
        channel=channel,
        month=month,
        sku=sku,
        season=season,
        barcode=barcode,
        barcodes=barcodes,
        departments=departments,
        sections=sections,
        zones=zones,
        store_grades=store_grades,
        states=states,
        cities=cities,
        search=search,
        timeline_mode=timeline_mode,
        timeline_values=timeline_values,
    )
    card_key = ",".join(sorted(cards)) if cards else ("L" if light else "F")
    cache_key = (
        f"duck-ins-{card_key}|{version}|"
        + hashlib.sha256(
            (
                normalize_filter_cache_key(
                    {
                        "years": ",".join(str(y) for y in sorted(set(fy_years))),
                        "site_ids": site_ids,
                        "channel": channel,
                        "season": season,
                        "zones": zones,
                        "departments": departments,
                        "sku": sku,
                        "barcodes": barcodes,
                        "tl_mode": timeline_mode,
                        "tl_values": timeline_values,
                        "limit": str(limit),
                    }
                )
            ).encode()
        ).hexdigest()[:20]
    )
    diag: dict[str, Any] = {
        "engine": "duckdb",
        "dataset_version": version,
        "cache_hit": False,
        "query_ms": 0.0,
        "light": light,
    }
    if not force_refresh:
        hit = _json_cache_get(cache_key, version)
        if hit is not None:
            diag["cache_hit"] = True
            return hit, diag

    g = _gross_expr(cols)
    result: dict[str, Any] = {
        "top_discounted_skus": [],
        "top_sold_skus": [],
        "top_revenue_sites": [],
        "monthly_trend": [],
        "weekly_trend": [],
        "yearly_trend": [],
        "top_departments": [],
        "top_sections": [],
        "top_channels": [],
        "top_zones": [],
        "top_states": [],
        "top_cities": [],
    }

    def _card(name: str) -> bool:
        return cards is None or name in cards

    t0 = time.perf_counter()
    # Monthly / weekly — light KPI path only needs last 2 buckets for GOLM/GOLW.
    if _card("trend_month"):
        month_expr = (
            "CAST(month_key AS VARCHAR)"
            if "month_key" in cols
            else "strftime(CAST(date AS TIMESTAMP), '%Y-%m')"
        )
        month_order = "DESC LIMIT 2" if light and cards is None else "ASC"
        monthly_sql = f"""
        SELECT {month_expr} AS month_key,
               SUM(CAST(net_sale_amount AS DOUBLE)) AS net_sales,
               SUM({g}) AS gross_sales,
               SUM(CAST(discount_amount AS DOUBLE)) AS discount_amount,
               SUM(CAST(qty AS DOUBLE)) AS units
        FROM {rel}{where_sql}
        GROUP BY 1
        ORDER BY 1 {month_order}
        """
        mdf = _execute_df(monthly_sql, params)
        month_rows = list(mdf.itertuples(index=False))
        if light and cards is None:
            month_rows = list(reversed(month_rows))
        for r in month_rows:
            mk = str(r.month_key or "").strip()
            if not mk or mk.lower() in ("none", "nan"):
                continue
            try:
                label = pd.Timestamp(f"{mk}-01").strftime("%b %Y")
            except Exception:
                label = mk
            result["monthly_trend"].append(
                {
                    "month": mk,
                    "label": label,
                    "net_sales": round(float(r.net_sales or 0), 2),
                    "gross_sales": round(float(r.gross_sales or 0), 2),
                    "discount_amount": round(float(r.discount_amount or 0), 2),
                    "units": int(r.units or 0),
                }
            )

    # Weekly (Mon start) — date_trunc('week') in DuckDB is Monday
    if _card("trend_week"):
        week_order = "DESC LIMIT 2" if light and cards is None else "ASC"
        weekly_sql = f"""
        SELECT CAST(date_trunc('week', CAST(date AS TIMESTAMP)) AS DATE) AS week_key,
               SUM(CAST(net_sale_amount AS DOUBLE)) AS net_sales,
               SUM({g}) AS gross_sales,
               SUM(CAST(qty AS DOUBLE)) AS units
        FROM {rel}{where_sql}
        GROUP BY 1
        ORDER BY 1 {week_order}
        """
        wdf = _execute_df(weekly_sql, params)
        week_rows = list(wdf.itertuples(index=False))
        if light and cards is None:
            week_rows = list(reversed(week_rows))
        for r in week_rows:
            if r.week_key is None:
                continue
            wk = pd.Timestamp(r.week_key)
            result["weekly_trend"].append(
                {
                    "week": wk.strftime("%Y-%m-%d"),
                    "label": wk.strftime("Wk %d %b"),
                    "net_sales": round(float(r.net_sales or 0), 2),
                    "gross_sales": round(float(r.gross_sales or 0), 2),
                    "units": int(r.units or 0),
                }
            )

    if not (cards is None and light):
        if _card("trend_year"):
            yearly_sql = f"""
            SELECT CAST(year(CAST(date AS TIMESTAMP)) AS VARCHAR) AS year_key,
                   SUM(CAST(net_sale_amount AS DOUBLE)) AS net_sales,
                   SUM({g}) AS gross_sales,
                   SUM(CAST(qty AS DOUBLE)) AS units
            FROM {rel}{where_sql}
            GROUP BY 1
            ORDER BY 1
            """
            ydf = _execute_df(yearly_sql, params)
            for r in ydf.itertuples(index=False):
                yk = str(r.year_key)
                result["yearly_trend"].append(
                    {
                        "year": yk,
                        "label": yk,
                        "net_sales": round(float(r.net_sales or 0), 2),
                        "gross_sales": round(float(r.gross_sales or 0), 2),
                        "units": int(r.units or 0),
                    }
                )

        if "sku" in cols and (_card("breakdown_sku") or _card("breakdown_discount")):
            sku_where = (
                f"{where_sql} AND trim(CAST(sku AS VARCHAR)) <> ''"
                if where_sql
                else " WHERE trim(CAST(sku AS VARCHAR)) <> ''"
            )
            sku_sql = f"""
            SELECT CAST(sku AS VARCHAR) AS sku,
                   SUM(CAST(discount_amount AS DOUBLE)) AS discount_amount,
                   SUM(CAST(net_sale_amount AS DOUBLE)) AS net_sales,
                   SUM(CAST(qty AS DOUBLE)) AS units
            FROM {rel}{sku_where}
            GROUP BY 1
            """
            sdf = _execute_df(sku_sql, params)
            if not sdf.empty:
                disc = sdf.sort_values("discount_amount", ascending=False).head(limit)
                sold = sdf.sort_values("net_sales", ascending=False).head(limit)
                result["top_discounted_skus"] = [
                    {
                        "sku": str(r.sku),
                        "discount_amount": round(float(r.discount_amount or 0), 2),
                        "net_sales": round(float(r.net_sales or 0), 2),
                        "units": int(r.units or 0),
                    }
                    for r in disc.itertuples(index=False)
                ]
                result["top_sold_skus"] = [
                    {
                        "sku": str(r.sku),
                        "units": int(r.units or 0),
                        "net_sales": round(float(r.net_sales or 0), 2),
                        "discount_amount": round(float(r.discount_amount or 0), 2),
                    }
                    for r in sold.itertuples(index=False)
                ]

        if _card("breakdown_store"):
            site_name_sel = (
                "CAST(site_name AS VARCHAR) AS site_name,"
                if "site_name" in cols
                else "CAST(site_id AS VARCHAR) AS site_name,"
            )
            sites_sql = f"""
            SELECT CAST(site_id AS VARCHAR) AS site_id,
                   {site_name_sel}
                   SUM(CAST(net_sale_amount AS DOUBLE)) AS net_sales,
                   SUM(CAST(qty AS DOUBLE)) AS units,
                   SUM(CAST(discount_amount AS DOUBLE)) AS discount_amount
            FROM {rel}{where_sql}
            GROUP BY 1, 2
            ORDER BY net_sales DESC
            LIMIT {int(limit)}
            """
            sit = _execute_df(sites_sql, params)
            result["top_revenue_sites"] = [
                {
                    "site_id": str(r.site_id),
                    "site_name": str(r.site_name or r.site_id),
                    "net_sales": round(float(r.net_sales or 0), 2),
                    "units": int(r.units or 0),
                    "discount_amount": round(float(r.discount_amount or 0), 2),
                }
                for r in sit.itertuples(index=False)
                if str(r.site_id).strip()
            ]

        def _top_dim(col: str, out_key: str) -> None:
            if col not in cols:
                return
            dim_where = (
                f"{where_sql} AND {col} IS NOT NULL AND trim(CAST({col} AS VARCHAR)) <> ''"
                if where_sql
                else f" WHERE {col} IS NOT NULL AND trim(CAST({col} AS VARCHAR)) <> ''"
            )
            sql = f"""
            SELECT CAST({col} AS VARCHAR) AS name,
                   SUM(CAST(net_sale_amount AS DOUBLE)) AS net_sales,
                   SUM(CAST(qty AS DOUBLE)) AS units
            FROM {rel}{dim_where}
            GROUP BY 1
            ORDER BY net_sales DESC
            LIMIT {int(limit)}
            """
            ddf = _execute_df(sql, params)
            result[out_key] = [
                {
                    "name": str(r.name),
                    "net_sales": round(float(r.net_sales or 0), 2),
                    "units": int(r.units or 0),
                }
                for r in ddf.itertuples(index=False)
                if str(r.name).strip()
            ]

        if _card("breakdown_department"):
            _top_dim("department", "top_departments")
        if _card("breakdown_section"):
            _top_dim("section", "top_sections")
        if _card("channel") or _card("breakdown_channel"):
            _top_dim("store_type", "top_channels")
        if _card("breakdown_zone"):
            _top_dim("zone", "top_zones")
        if _card("breakdown_state"):
            _top_dim("state", "top_states")
        if _card("breakdown_city"):
            _top_dim("city", "top_cities")

    elapsed = (time.perf_counter() - t0) * 1000.0
    diag["query_ms"] = round(elapsed, 2)
    _json_cache_put(cache_key, version, result)
    return result, diag


def soh_str_from_movement_duckdb(
    scope: dict[str, Any] | None,
    *,
    dm_path: Path | None = None,
) -> tuple[float, float | None, float | None, float | None, dict[str, Any]]:
    """Latest closing SOH + STR from daily_inventory_movement via DuckDB.

    Preserves existing semantics: SOH = latest row per (store, barcode);
    available / sell-through use the full scoped movement history.
    Geo without site_ids is resolved via Customer Master in Python, then pushed as store keys.
    """
    if not duckdb_available():
        raise RuntimeError("duckdb package not installed")

    scope = scope or {}
    path = dm_path or (
        Path(__file__).resolve().parents[2] / "data" / "api_cache" / "daily_inventory_movement.parquet"
    )
    if not path.exists() or path.stat().st_size <= 0:
        raise FileNotFoundError(f"daily movement parquet missing: {path}")

    # Empty intersection sentinel
    raw_sites = _val_str(scope.get("site_ids") or scope.get("site_id"))
    if raw_sites.lower() == "__none__":
        return 0.0, None, None, None, {"engine": "duckdb", "rows": 0, "empty": True}

    clauses: list[str] = []
    params: list[Any] = []

    def _csv_set(raw: Any) -> list[str] | None:
        s = _val_str(raw)
        if not s or s.lower() in ("", "all"):
            return None
        return [x.strip().upper() for x in s.split(",") if x.strip()]

    for col, key, upper in (
        ("season", "season", True),
        ("sku", "sku", True),
        ("department", "departments", False),
        ("section", "sections", False),
    ):
        vals = _csv_set(scope.get(key))
        if vals is None:
            continue
        if upper:
            vals = [v.upper() for v in vals]
            placeholders = ", ".join(["?"] * len(vals))
            clauses.append(f"upper(trim(cast({col} AS VARCHAR))) IN ({placeholders})")
            params.extend(vals)
        else:
            placeholders = ", ".join(["?"] * len(vals))
            clauses.append(f"trim(cast({col} AS VARCHAR)) IN ({placeholders})")
            # preserve original case from scope csv
            raw = _val_str(scope.get(key))
            params.extend([x.strip() for x in raw.split(",") if x.strip()])

    barcodes = _csv_set(scope.get("barcodes")) or _csv_set(scope.get("barcode"))
    if barcodes is not None:
        placeholders = ", ".join(["?"] * len(barcodes))
        clauses.append(
            f"regexp_replace(upper(trim(cast(barcode AS VARCHAR))), '\\.0$', '') "
            f"IN ({placeholders})"
        )
        params.extend(barcodes)

    # Channel
    channels = _csv_set(scope.get("channel"))
    if channels is not None:
        resolved = {_CHANNEL_ALIAS.get(c, c) for c in channels}
        match_vals: set[str] = set()
        for code in resolved:
            match_vals.update(_CHANNEL_DISPLAY.get(code, {code}))
            match_vals.add(code)
        if match_vals:
            ch_parts: list[str] = []
            for col in ("sales_channel", "channel", "store_type"):
                placeholders = ", ".join(["?"] * len(match_vals))
                ch_parts.append(
                    f"upper(trim(cast({col} AS VARCHAR))) IN ({placeholders})"
                )
                params.extend(sorted(match_vals))
            clauses.append("(" + " OR ".join(ch_parts) + ")")

    # Store / geo
    from api.historical_movement import _extract_single_site_key

    observed: set[str] = set()
    site_ids = _csv_set(scope.get("site_ids")) or _csv_set(scope.get("site_id"))
    if site_ids is not None:
        for v in site_ids:
            k = _extract_single_site_key(v) or v
            if k:
                observed.add(str(k).strip().upper())
            observed.add(str(v).strip().upper())
    else:
        # Resolve CM geo (zone/grade/state/city) on the read side — same as Pandas path
        try:
            from api.routes.sales_dashboard import _cm_site_keys_for_geo_scope

            cm_keys = _cm_site_keys_for_geo_scope(scope)
            if cm_keys is not None:
                if not cm_keys:
                    return 0.0, None, None, None, {"engine": "duckdb", "rows": 0, "empty": True}
                observed = set(cm_keys)
        except Exception as exc:
            logger.warning("CM geo resolve for DuckDB SOH skipped: %s", exc)

    zones = _csv_set(scope.get("zones"))
    store_clause = ""
    if observed:
        placeholders = ", ".join(["?"] * len(observed))
        store_clause = (
            f"(upper(trim(cast(store AS VARCHAR))) IN ({placeholders}) "
            f"OR regexp_extract(upper(cast(store AS VARCHAR)), "
            f"'\\(([A-Z]?\\d+)\\)\\s*$', 1) IN ({placeholders}) "
            f"OR regexp_extract(upper(cast(store AS VARCHAR)), "
            f"'-([A-Z]?\\d+)\\s*$', 1) IN ({placeholders}))"
        )
        obs_sorted = sorted(observed)
        params.extend(obs_sorted)
        params.extend(obs_sorted)
        params.extend(obs_sorted)
        clauses.append(store_clause)
    elif zones is not None:
        placeholders = ", ".join(["?"] * len(zones))
        clauses.append(f"upper(trim(cast(zone AS VARCHAR))) IN ({placeholders})")
        params.extend(zones)

    where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    p = str(path).replace("'", "''")
    rel = f"read_parquet('{p}')"

    # Project only SOH/STR columns — preserve latest-snapshot SOH + full-history avail/STR.
    sql = f"""
    WITH scoped AS (
      SELECT
        store,
        barcode,
        CAST(date AS TIMESTAMP) AS date_ts,
        CAST(closing_soh AS DOUBLE) AS closing_soh,
        CAST(dispatch_in AS DOUBLE) AS dispatch_in,
        CAST(ibt_in AS DOUBLE) AS ibt_in,
        CAST(sales_return_qty AS DOUBLE) AS sales_return_qty,
        CAST(sales_qty AS DOUBLE) AS sales_qty,
        CAST(full_price_sales_qty AS DOUBLE) AS full_price_sales_qty,
        CAST(discount_sales_qty AS DOUBLE) AS discount_sales_qty
      FROM {rel}{where_sql}
    ),
    latest AS (
      SELECT closing_soh
      FROM scoped
      QUALIFY row_number() OVER (
        PARTITION BY store, barcode ORDER BY date_ts DESC
      ) = 1
    )
    SELECT
      (SELECT COALESCE(SUM(closing_soh), 0) FROM latest) AS soh_val,
      (SELECT COALESCE(SUM(dispatch_in), 0)
            + COALESCE(SUM(ibt_in), 0)
            + COALESCE(SUM(sales_return_qty), 0) FROM scoped) AS avail,
      (SELECT COALESCE(SUM(sales_qty), 0) FROM scoped) AS cs,
      (SELECT COALESCE(SUM(full_price_sales_qty), 0) FROM scoped) AS cfp,
      (SELECT COALESCE(SUM(discount_sales_qty), 0) FROM scoped) AS cdisc,
      (SELECT COUNT(*) FROM scoped) AS scoped_rows
    """

    t0 = time.perf_counter()
    con = _get_conn()
    acquired = _query_sem.acquire(timeout=180.0)
    if not acquired:
        raise TimeoutError("DuckDB SOH concurrency limit timeout")
    try:
        with _conn_lock:
            row = con.execute(sql, params).fetchone()
    finally:
        _query_sem.release()
    ms = (time.perf_counter() - t0) * 1000.0

    soh_val = float(row[0] or 0.0)
    avail = float(row[1] or 0.0)
    cs = float(row[2] or 0.0)
    cfp = float(row[3] or 0.0)
    cdisc = float(row[4] or 0.0)
    scoped_rows = int(row[5] or 0)

    st = fp_st = disc_st = None
    if avail > 0:
        st = round(cs / avail * 100, 1)
        fp_st = round(cfp / avail * 100, 1)
        disc_st = round(cdisc / avail * 100, 1)

    diag = {
        "engine": "duckdb",
        "query_ms": round(ms, 2),
        "rows": scoped_rows,
        "path": str(path),
    }
    logger.info(
        "DuckDB scoped SOH soh=%.0f avail=%.0f rows=%s query_ms=%.1f",
        soh_val,
        avail,
        scoped_rows,
        ms,
    )
    return soh_val, st, fp_st, disc_st, diag

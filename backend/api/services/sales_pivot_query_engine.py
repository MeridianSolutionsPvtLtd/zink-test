"""
DuckDB-backed Sales Analyzer pivot aggregation (opt-in).

Controlled by ANALYZER_QUERY_ENGINE (preferred) or ANALYTICS_QUERY_ENGINE.
Default remains pandas. Queries existing prepared Analyzer Parquet only —
never modifies pipeline outputs or source masters.
"""
from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

import pandas as pd

logger = logging.getLogger(__name__)


def json_safe_payload(value: Any) -> Any:
    """Replace NaN and infinity so FastAPI can encode the pivot response."""
    if isinstance(value, dict):
        return {str(k): json_safe_payload(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe_payload(v) for v in value]
    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return value
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value
    if number != number or number in (float("inf"), float("-inf")):
        return None
    if isinstance(value, float):
        return number
    # numpy scalars
    if type(value).__module__.startswith("numpy"):
        if number.is_integer():
            return int(number)
        return number
    return value

_ENGINE = "ANALYZER_QUERY_ENGINE"
_FALLBACK_ENGINE = "ANALYTICS_QUERY_ENGINE"

# Allowlisted physical columns on prepared sales / SOH parquet
_SALES_DIM_COLS = frozenset(
    {
        "barcode",
        "zone",
        "sales_channel",
        "site_id_name",
        "site_id",
        "site_name",
        "city_name",
        "state_name",
        "store_grade",
        "cluster",
        "manager",
        "brand",
        "department",
        "section",
        "sku",
        "size",
        "season",
        "financial_year",
        "year",
        "month",
        "fy_week",
        "date",
        "customer_name",
        "channel",
    }
)
_SALES_METRIC_COLS = {
    "sales": "CAST(sales AS DOUBLE)",
    "quantity": "CAST(quantity AS DOUBLE)",
    "gross_sales": "CAST(gross_sales AS DOUBLE)",
    "discount_amount": "CAST(discount_amount AS DOUBLE)",
}
_SOH_METRIC_COLS = {
    "current_soh": "CAST(current_soh AS DOUBLE)",
    "dispatch_qty": "CAST(dispatch_qty AS DOUBLE)",
    "available_qty": "CAST(available_qty AS DOUBLE)",
}
_STORE_LEVEL = frozenset(
    {
        "site_id",
        "site_id_name",
        "site_name",
        "zone",
        "sales_channel",
        "channel",
        "city_name",
        "state_name",
        "store_grade",
        "cluster",
        "manager",
        "customer_name",
    }
)


def use_analyzer_duckdb() -> bool:
    raw = (
        os.environ.get(_ENGINE)
        or os.environ.get(_FALLBACK_ENGINE)
        or "pandas"
    ).strip().lower()
    if raw not in ("duckdb", "duck", "ddb"):
        return False
    try:
        from api.services.analytics_query_engine import duckdb_available

        return duckdb_available()
    except Exception:
        return False


def _sql_ident(name: str) -> str:
    if name not in _SALES_DIM_COLS and name not in _SALES_METRIC_COLS and name not in _SOH_METRIC_COLS:
        raise ValueError(f"Unsupported column: {name}")
    return f'"{name}"'


def _prepared_sales_path(year: int) -> Optional[Path]:
    p = Path("data/api_cache") / f"sales_analyzer_prepared_v4_y{int(year)}.parquet"
    if p.exists() and p.stat().st_size > 0:
        return p
    return None


def _prepared_soh_path() -> Optional[Path]:
    p = Path("data/api_cache") / "soh_analyzer_prepared_v6_current.parquet"
    if p.exists() and p.stat().st_size > 0:
        return p
    # Older local name (pre Master switch)
    legacy = Path("data/api_cache") / "soh_analyzer_prepared_v5_daily_only.parquet"
    if legacy.exists() and legacy.stat().st_size > 0:
        return legacy
    return None


def _fy_label(year: int) -> str:
    return f"FY{str(int(year))[-2:]}"


_SITE_PAREN_RE = re.compile(r"\(([^)]+)\)")


def _site_match_tokens(cleaned: list[str]) -> list[str]:
    """Manual codes (P001) and full labels (PT-Gariahat-(P001)) match the same store."""
    tokens: list[str] = []
    seen: set[str] = set()
    for raw in cleaned:
        val = str(raw).strip().upper()
        if val.endswith(".0"):
            val = val[:-2]
        if not val or val == "ALL":
            continue
        if val not in seen:
            seen.add(val)
            tokens.append(val)
        match = _SITE_PAREN_RE.search(val)
        if match:
            key = re.sub(r"\s+", "", match.group(1))
            if key and key not in seen:
                seen.add(key)
                tokens.append(key)
    return tokens


def _site_id_predicate(tokens: list[str], params: list[Any]) -> str:
    placeholders = ", ".join(["?"] * len(tokens))
    parts: list[str] = []
    for col in ('"site_id"', '"site_id_name"'):
        parts.append(f"UPPER(TRIM(CAST({col} AS VARCHAR))) IN ({placeholders})")
        params.extend(tokens)
        parts.append(
            "UPPER(TRIM(regexp_extract(CAST("
            + col
            + " AS VARCHAR), '\\(([^)]+)\\)', 1))) IN ("
            + placeholders
            + ")"
        )
        params.extend(tokens)
    return "(" + " OR ".join(parts) + ")"


def _build_filter_sql(
    filters: dict[str, Any] | None,
    *,
    year: int,
    params: list[Any],
    apply_timeline: bool = True,
) -> str:
    """Return AND-joined predicates; pushes values into params.

    Timeline modes (month/year/date/FY/…) must match sales_dashboard._apply_timeline_filter
    / analytics_query_engine._build_timeline_sql. Skipping them previously made DuckDB
    ignore UI month filters while pandas applied them — totals diverged.

    When apply_timeline is False (LY path with an explicit date_between window), the
    CY timeline predicate must not be re-applied or last-year rows are wiped out.
    """
    clauses: list[str] = []
    filters = filters or {}

    mode = str(filters.get("timeline_mode") or "").strip()
    vals = str(filters.get("timeline_values") or "").strip()
    has_timeline = bool(apply_timeline and mode and vals)
    has_time = has_timeline or (not apply_timeline) or any(
        k in filters
        for k in ("financial_year", "year", "date", "date_range", "from_date", "to_date")
    )
    # Default FY unless explicit timeline / FY / date filter present
    if not has_time and int(year) != 0:
        clauses.append('TRIM(CAST("financial_year" AS VARCHAR)) = ?')
        params.append(_fy_label(year))

    if has_timeline:
        from api.services.analytics_query_engine import _build_timeline_sql

        t_sql, t_params = _build_timeline_sql(timeline_mode=mode, timeline_values=vals)
        if t_sql:
            # Prepared analyzer parquet quotes identifiers; align date refs.
            t_sql = t_sql.replace("date IS NOT NULL", '"date" IS NOT NULL')
            t_sql = t_sql.replace("CAST(date AS ", 'CAST("date" AS ')
            clauses.append(f"({t_sql})")
            params.extend(t_params)

    for field, raw in filters.items():
        if field in ("timeline_mode", "timeline_values", "search", "from_date", "to_date", "date_range"):
            continue
        if field not in _SALES_DIM_COLS:
            continue
        if raw is None or raw == "" or raw == []:
            continue
        vals_f = raw if isinstance(raw, (list, tuple, set)) else [raw]
        cleaned = [str(v).strip() for v in vals_f if str(v).strip() and str(v).strip().lower() != "all"]
        if not cleaned:
            continue
        if field == "site_id":
            tokens = _site_match_tokens(cleaned)
            if not tokens or tokens == ["__NONE__"]:
                clauses.append("FALSE")
                continue
            clauses.append(_site_id_predicate(tokens, params))
            continue
        placeholders = ", ".join(["?"] * len(cleaned))
        clauses.append(f'UPPER(TRIM(CAST({_sql_ident(field)} AS VARCHAR))) IN ({placeholders})')
        params.extend([v.upper() for v in cleaned])

    search = str((filters or {}).get("search") or "").strip()
    if search:
        like = f"%{search.upper()}%"
        clauses.append(
            "("
            + " OR ".join(
                [
                    'UPPER(TRIM(CAST("site_id" AS VARCHAR))) LIKE ?',
                    'UPPER(TRIM(CAST("site_id_name" AS VARCHAR))) LIKE ?',
                    'UPPER(TRIM(CAST("barcode" AS VARCHAR))) LIKE ?',
                    'UPPER(TRIM(CAST("sku" AS VARCHAR))) LIKE ?',
                ]
            )
            + ")"
        )
        params.extend([like, like, like, like])

    return (" AND ".join(clauses)) if clauses else "TRUE"


def _where_sql(
    filters: dict[str, Any] | None,
    year: int,
    params: list[Any],
    date_between: tuple[str, str] | None = None,
) -> str:
    # LY aggregates pass date_between for the comparable window — do not also
    # apply the CY timeline_mode (e.g. month=2026-10) or LY sales become empty.
    where = _build_filter_sql(
        filters,
        year=year,
        params=params,
        apply_timeline=date_between is None,
    )
    if date_between:
        params.extend([date_between[0], date_between[1]])
        where = (
            f"{where} AND CAST(\"date\" AS DATE) BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)"
        )
    return where


def _aggregate_sales_duckdb(
    *,
    sales_path: Path,
    row_dims: list[str],
    col_dim: Optional[str],
    year: int,
    filters: dict[str, Any] | None,
    date_between: tuple[str, str] | None = None,
) -> tuple[dict[int, pd.DataFrame], pd.DataFrame | None, dict[str, Any]]:
    """Return sales aggregates by hierarchy depth (0=grand total) + optional split leaf."""
    from api.services.analytics_query_engine import duckdb_query_session

    for d in row_dims:
        if d not in _SALES_DIM_COLS:
            raise ValueError(f"Unsupported row dimension: {d}")
    if col_dim and col_dim not in _SALES_DIM_COLS:
        raise ValueError(f"Unsupported column dimension: {col_dim}")

    path_sql = str(sales_path).replace("'", "''")
    metric_select = f"""
      COALESCE(SUM({_SALES_METRIC_COLS['sales']}), 0) AS sales,
      COALESCE(SUM({_SALES_METRIC_COLS['quantity']}), 0) AS quantity,
      COALESCE(SUM({_SALES_METRIC_COLS['gross_sales']}), 0) AS gross_sales,
      COALESCE(SUM({_SALES_METRIC_COLS['discount_amount']}), 0) AS discount_amount,
      COUNT(*) AS rec_count
    """

    t0 = time.perf_counter()
    by_depth: dict[int, pd.DataFrame] = {}
    split_df = None
    max_date = None
    grouping_ms = 0.0
    split_ms = 0.0
    with duckdb_query_session() as (con, sem_timing):
        # One scan for every hierarchy depth. Repeating the file per level is
        # what made a zone filter feel stuck.
        params0: list[Any] = []
        where0 = _where_sql(filters, year, params0, date_between)
        if row_dims:
            dim_sql = ", ".join(_sql_ident(c) for c in row_dims)
            grp_sql = ", ".join(f"GROUPING({_sql_ident(c)}) AS grp_{c}" for c in row_dims)
            sets = ["()"]
            for depth in range(1, len(row_dims) + 1):
                sets.append("(" + ", ".join(_sql_ident(c) for c in row_dims[:depth]) + ")")
            sql = f"""
            SELECT {dim_sql}, {grp_sql}, {metric_select},
                   MAX(CAST("date" AS DATE)) AS max_date
            FROM read_parquet('{path_sql}')
            WHERE {where0}
            GROUP BY GROUPING SETS ({", ".join(sets)})
            """
            t_g = time.perf_counter()
            flat = con.execute(sql, params0).df()
            grouping_ms = round((time.perf_counter() - t_g) * 1000.0, 1)
            if flat is None or flat.empty:
                by_depth[0] = pd.DataFrame(
                    [{"sales": 0.0, "quantity": 0.0, "gross_sales": 0.0, "discount_amount": 0.0, "rec_count": 0}]
                )
            else:
                grand_mask = pd.Series(True, index=flat.index)
                for c in row_dims:
                    grand_mask &= flat[f"grp_{c}"] == 1
                grand = flat.loc[grand_mask]
                by_depth[0] = grand.drop(columns=[f"grp_{c}" for c in row_dims] + row_dims, errors="ignore")
                if not grand.empty and "max_date" in grand.columns:
                    max_date = grand.iloc[0].get("max_date")
                for depth in range(1, len(row_dims) + 1):
                    mask = pd.Series(True, index=flat.index)
                    for i, c in enumerate(row_dims):
                        mask &= flat[f"grp_{c}"] == (0 if i < depth else 1)
                    cols = row_dims[:depth]
                    keep = cols + ["sales", "quantity", "gross_sales", "discount_amount", "rec_count"]
                    by_depth[depth] = flat.loc[mask, keep].reset_index(drop=True)
        else:
            gt_sql = f"""
            SELECT {metric_select}, MAX(CAST("date" AS DATE)) AS max_date
            FROM read_parquet('{path_sql}')
            WHERE {where0}
            """
            t_g = time.perf_counter()
            by_depth[0] = con.execute(gt_sql, params0).df()
            grouping_ms = round((time.perf_counter() - t_g) * 1000.0, 1)
            if by_depth[0] is not None and not by_depth[0].empty:
                max_date = by_depth[0].iloc[0].get("max_date")

        if col_dim and row_dims:
            params2: list[Any] = []
            where2 = _where_sql(filters, year, params2, date_between)
            leaf_cols = ", ".join(_sql_ident(c) for c in row_dims + [col_dim])
            split_sql = f"""
            SELECT {leaf_cols}, {metric_select}
            FROM read_parquet('{path_sql}')
            WHERE {where2}
            GROUP BY {leaf_cols}
            """
            t_s = time.perf_counter()
            split_df = con.execute(split_sql, params2).df()
            split_ms = round((time.perf_counter() - t_s) * 1000.0, 1)

    diag = {
        "engine": "duckdb",
        "query_ms": round((time.perf_counter() - t0) * 1000, 1),
        "semaphore_wait_ms": float(sem_timing.get("semaphore_wait_ms") or 0.0),
        "conn_acquire_ms": float(sem_timing.get("conn_acquire_ms") or 0.0),
        "grouping_sets_ms": grouping_ms,
        "column_split_ms": split_ms,
        "sales_path": sales_path.name,
        "row_dims": row_dims,
        "col_dim": col_dim,
        "max_date": None if max_date is None or pd.isna(max_date) else str(pd.Timestamp(max_date).date()),
    }
    return by_depth, split_df, diag


def _soh_aligned_to_sales(
    *,
    soh_path: Path,
    sales_path: Path,
    row_dims: list[str],
    year: int,
    filters: dict[str, Any] | None,
) -> dict[int, pd.DataFrame] | None:
    """Roll stock up on the sales store key, then onto the same labels as the pivot."""
    from api.services.analytics_query_engine import (
        DuckDBSemaphoreTimeout,
        duckdb_query_session,
    )

    if any(d not in _STORE_LEVEL for d in row_dims):
        return None
    soh_sql_path = str(soh_path).replace("'", "''")
    sales_sql_path = str(sales_path).replace("'", "''")
    dim_sql = ", ".join(
        f'ANY_VALUE("{d}") AS "{d}"' for d in row_dims
    ) if row_dims else ""
    dim_select = f", {dim_sql}" if dim_sql else ""
    params: list[Any] = []
    where = _build_filter_sql(filters, year=year, params=params)
    # Prepared sales/SOH often disagree on _pivot_sk (EBO|964 vs EBO|FRESCOGLOBAL…).
    # Match pandas `_analyzer_store_join_key`: normalize channel codes, prefer long
    # site_name alnum (≥8), else compact P-code / short digits.
    join_key = """(
      (
        CASE
          WHEN UPPER(TRIM(CAST("sales_channel" AS VARCHAR))) IN ('PT','SSL','CENTRO','ICONIC','EBO')
            THEN UPPER(TRIM(CAST("sales_channel" AS VARCHAR)))
          WHEN regexp_matches(UPPER(TRIM(CAST("sales_channel" AS VARCHAR))), 'SHOPPER|\\bSSL\\b')
            THEN 'SSL'
          WHEN regexp_matches(UPPER(TRIM(CAST("sales_channel" AS VARCHAR))), 'PANTALOON|\\(PT\\)')
            THEN 'PT'
          WHEN regexp_matches(UPPER(TRIM(CAST("sales_channel" AS VARCHAR))), 'CENTRO|RELIANCE')
            THEN 'CENTRO'
          WHEN regexp_matches(UPPER(TRIM(CAST("sales_channel" AS VARCHAR))), 'ICONIC')
            THEN 'ICONIC'
          WHEN regexp_matches(UPPER(TRIM(CAST("sales_channel" AS VARCHAR))), 'EBO|FRESCO')
            THEN 'EBO'
          ELSE 'OTHER'
        END
      ) || '|' ||
      (
        CASE
          WHEN length(regexp_replace(
            UPPER(TRIM(COALESCE(
              NULLIF(TRIM(CAST("site_name" AS VARCHAR)), ''),
              NULLIF(TRIM(CAST("site_id" AS VARCHAR)), ''),
              NULLIF(TRIM(CAST("site_id_name" AS VARCHAR)), ''),
              ''
            ))),
            '[^A-Z0-9]', '', 'g'
          )) >= 8
            THEN regexp_replace(
              UPPER(TRIM(COALESCE(
                NULLIF(TRIM(CAST("site_name" AS VARCHAR)), ''),
                NULLIF(TRIM(CAST("site_id" AS VARCHAR)), ''),
                NULLIF(TRIM(CAST("site_id_name" AS VARCHAR)), ''),
                ''
              ))),
              '[^A-Z0-9]', '', 'g'
            )
          WHEN COALESCE(
            NULLIF(regexp_extract(UPPER(TRIM(CAST(COALESCE("site_id_name", '') AS VARCHAR))), '(P[0-9]{2,5}|[0-9]{2,6})', 1), ''),
            NULLIF(regexp_extract(UPPER(TRIM(CAST(COALESCE("site_id", '') AS VARCHAR))), '(P[0-9]{2,5}|[0-9]{2,6})', 1), ''),
            ''
          ) <> ''
            THEN COALESCE(
              NULLIF(regexp_extract(UPPER(TRIM(CAST(COALESCE("site_id_name", '') AS VARCHAR))), '(P[0-9]{2,5}|[0-9]{2,6})', 1), ''),
              NULLIF(regexp_extract(UPPER(TRIM(CAST(COALESCE("site_id", '') AS VARCHAR))), '(P[0-9]{2,5}|[0-9]{2,6})', 1), '')
            )
          ELSE regexp_replace(
            UPPER(TRIM(COALESCE(
              NULLIF(TRIM(CAST("site_name" AS VARCHAR)), ''),
              NULLIF(TRIM(CAST("site_id" AS VARCHAR)), ''),
              NULLIF(TRIM(CAST("site_id_name" AS VARCHAR)), ''),
              ''
            ))),
            '[^A-Z0-9]', '', 'g'
          )
        END
      )
    )"""
    sales_sql = f"""
    SELECT {join_key} AS sk
           {dim_select}
    FROM read_parquet('{sales_sql_path}')
    WHERE {where}
      AND TRIM(CAST("sales_channel" AS VARCHAR)) <> ''
    GROUP BY 1
    """
    # SOH is a current snapshot — apply channel/dept/etc. but never the sales timeline.
    soh_params: list[Any] = []
    soh_where = _build_filter_sql(
        filters, year=year, params=soh_params, apply_timeline=False
    )
    soh_sql = f"""
    SELECT {join_key} AS sk,
           SUM(CAST(current_soh AS DOUBLE)) AS current_soh,
           SUM(CAST(dispatch_qty AS DOUBLE)) AS dispatch_qty,
           SUM(CAST(available_qty AS DOUBLE)) AS available_qty
    FROM read_parquet('{soh_sql_path}')
    WHERE ({soh_where})
      AND TRIM(CAST("sales_channel" AS VARCHAR)) <> ''
    GROUP BY 1
    """
    try:
        with duckdb_query_session() as (con, _timing):
            labels = con.execute(sales_sql, params).df()
            stock = con.execute(soh_sql, soh_params).df()
    except DuckDBSemaphoreTimeout:
        logger.warning("Analyzer DuckDB SOH align skipped — concurrency timeout")
        return None
    except Exception as exc:
        logger.warning("Analyzer DuckDB SOH align failed: %s", exc)
        return None
    if labels is None or labels.empty or stock is None or stock.empty:
        return None
    # Drop empty tokens (channel| with no site)
    labels = labels[labels["sk"].astype(str).str.contains(r"\|.+", regex=True, na=False)]
    stock = stock[stock["sk"].astype(str).str.contains(r"\|.+", regex=True, na=False)]
    if labels.empty or stock.empty:
        return None
    merged = labels.merge(stock, on="sk", how="left")
    for col in ("current_soh", "dispatch_qty", "available_qty"):
        merged[col] = pd.to_numeric(merged[col], errors="coerce").fillna(0.0)
    merged["current_soh"] = merged["current_soh"].clip(lower=0.0)
    by_depth: dict[int, pd.DataFrame] = {
        0: pd.DataFrame(
            [{
                "current_soh": float(merged["current_soh"].sum()),
                "dispatch_qty": float(merged["dispatch_qty"].sum()),
                "available_qty": float(merged["available_qty"].sum()),
            }]
        )
    }
    metrics = ["current_soh", "dispatch_qty", "available_qty"]
    for depth in range(1, len(row_dims) + 1):
        cols = row_dims[:depth]
        by_depth[depth] = merged.groupby(cols, dropna=False, as_index=False)[metrics].sum()
    return by_depth


def _aggregate_soh_duckdb(
    *,
    soh_path: Path,
    group_cols: list[str],
    filters: dict[str, Any] | None,
) -> pd.DataFrame:
    from api.services.analytics_query_engine import duckdb_query_session

    use_cols = [c for c in group_cols if c in _SALES_DIM_COLS]
    if not use_cols:
        # Network total
        sql = f"""
        SELECT
          SUM({_SOH_METRIC_COLS['current_soh']}) AS current_soh,
          SUM({_SOH_METRIC_COLS['dispatch_qty']}) AS dispatch_qty,
          SUM({_SOH_METRIC_COLS['available_qty']}) AS available_qty
        FROM read_parquet('{str(soh_path).replace("'", "''")}')
        """
        with duckdb_query_session() as (con, _timing):
            return con.execute(sql).df()

    # Store-level: distinct stock grain via site_id + barcode before dim rollup
    params: list[Any] = []
    clauses: list[str] = []
    filters = filters or {}
    for field, raw in filters.items():
        if field not in use_cols:
            continue
        vals = raw if isinstance(raw, (list, tuple, set)) else [raw]
        cleaned = [str(v).strip() for v in vals if str(v).strip() and str(v).strip().lower() != "all"]
        if not cleaned:
            continue
        placeholders = ", ".join(["?"] * len(cleaned))
        clauses.append(f'UPPER(TRIM(CAST("{field}" AS VARCHAR))) IN ({placeholders})')
        params.extend([v.upper() for v in cleaned])
    where_sql = " AND ".join(clauses) if clauses else "TRUE"
    gcols = ", ".join(f'"{c}"' for c in use_cols)
    path_sql = str(soh_path).replace("'", "''")
    # Dedup stock units: sum current_soh at barcode×site first, then roll to dims
    sql = f"""
    WITH grain AS (
      SELECT
        {gcols},
        "site_id",
        "barcode",
        MAX({_SOH_METRIC_COLS['current_soh']}) AS current_soh,
        MAX({_SOH_METRIC_COLS['dispatch_qty']}) AS dispatch_qty,
        MAX({_SOH_METRIC_COLS['available_qty']}) AS available_qty
      FROM read_parquet('{path_sql}')
      WHERE {where_sql}
      GROUP BY {gcols}, "site_id", "barcode"
    )
    SELECT
      {gcols},
      SUM(current_soh) AS current_soh,
      SUM(dispatch_qty) AS dispatch_qty,
      SUM(available_qty) AS available_qty
    FROM grain
    GROUP BY {gcols}
    """
    with duckdb_query_session() as (con, _timing):
        return con.execute(sql, params).df()


def _metrics_payload(
    *,
    sales: float,
    qty: float,
    gross: float,
    disc: float,
    soh: Optional[float],
    available: Optional[float],
    dispatch: Optional[float],
    opening: Optional[float],
    receipts: Optional[float],
    want: set[str],
    ly_sales: float = 0.0,
    ly_qty: float = 0.0,
) -> dict[str, Any]:
    from api.historical_movement import historical_str
    from api.routes.sales_analyzer import ValueConfig, _compute_measure_values

    vals = [ValueConfig(field=f, aggregation="sum") for f in want]
    return _compute_measure_values(
        sales_val=float(sales or 0),
        qty_val=float(qty or 0),
        gross_val=float(gross or 0),
        disc_amt=float(disc or 0),
        soh_val=soh,
        mdq_val=None,
        rec_count=0,
        active_values=vals,
        ly_sales_val=float(ly_sales or 0),
        ly_qty_val=float(ly_qty or 0),
        opening_val=opening,
        receipts_val=receipts,
        dispatch_val=dispatch,
        available_val=available,
    )


def _build_tree_from_depth_maps(
    sales_by_depth: dict[int, pd.DataFrame],
    row_dims: list[str],
    *,
    soh_by_depth: dict[int, pd.DataFrame],
    want: set[str],
    split_col_keys: list[str],
    split_df: pd.DataFrame | None,
    col_dim: Optional[str],
    ly_by_depth: dict[int, pd.DataFrame] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Assemble analyzer-compatible tree from per-depth GROUP BY results."""
    n = len(row_dims)
    gt_frame = sales_by_depth.get(0)
    gt = gt_frame.iloc[0] if gt_frame is not None and not gt_frame.empty else None
    gt_sales = float(gt["sales"]) if gt is not None else 0.0
    gt_qty = float(gt["quantity"]) if gt is not None else 0.0
    gt_gross = float(gt["gross_sales"]) if gt is not None else 0.0
    gt_disc = float(gt["discount_amount"]) if gt is not None else 0.0

    gt_soh = gt_avail = gt_disp = None
    if 0 in soh_by_depth and not soh_by_depth[0].empty:
        s0 = soh_by_depth[0].iloc[0]
        gt_soh = float(s0.get("current_soh") or 0)
        gt_avail = float(s0.get("available_qty") or 0)
        gt_disp = float(s0.get("dispatch_qty") or 0)

    ly_by_depth = ly_by_depth or {}
    ly_gt = ly_by_depth.get(0)
    ly_gt_row = ly_gt.iloc[0] if ly_gt is not None and not ly_gt.empty else None
    gt_ly_sales = float(ly_gt_row["sales"]) if ly_gt_row is not None else 0.0
    gt_ly_qty = float(ly_gt_row["quantity"]) if ly_gt_row is not None else 0.0

    ly_index: dict[int, dict[tuple[str, ...], tuple[float, float]]] = {}
    for depth, frame in ly_by_depth.items():
        if depth <= 0 or frame is None or frame.empty:
            continue
        cols = row_dims[:depth]
        if any(c not in frame.columns for c in cols):
            continue
        idx: dict[tuple[str, ...], tuple[float, float]] = {}
        for _, r in frame.iterrows():
            key = tuple(str(r[c]) for c in cols)
            idx[key] = (float(r.get("sales") or 0), float(r.get("quantity") or 0))
        ly_index[depth] = idx

    grand_metrics = _metrics_payload(
        sales=gt_sales,
        qty=gt_qty,
        gross=gt_gross,
        disc=gt_disc,
        soh=gt_soh,
        available=gt_avail,
        dispatch=gt_disp,
        opening=None,
        receipts=None,
        want=want,
        ly_sales=gt_ly_sales,
        ly_qty=gt_ly_qty,
    )

    if not row_dims:
        return [], {
            "metrics": grand_metrics,
            "split_metrics": {},
            "record_count": int(gt["rec_count"]) if gt is not None else 0,
        }

    # Index SOH frames for O(1) lookups
    soh_index: dict[int, dict[tuple[str, ...], tuple[float, float, float]]] = {}
    for depth, frame in soh_by_depth.items():
        if depth <= 0 or frame is None or frame.empty:
            continue
        cols = row_dims[:depth]
        idx: dict[tuple[str, ...], tuple[float, float, float]] = {}
        for _, r in frame.iterrows():
            key = tuple(str(r[c]) for c in cols)
            idx[key] = (
                float(r.get("current_soh") or 0),
                float(r.get("available_qty") or 0),
                float(r.get("dispatch_qty") or 0),
            )
        soh_index[depth] = idx

    def _soh_lookup(depth: int, key_tuple: tuple[Any, ...]) -> tuple[Optional[float], Optional[float], Optional[float]]:
        hit = soh_index.get(depth, {}).get(tuple(str(v) for v in key_tuple))
        if hit is None:
            return None, None, None
        return hit

    def _node(depth: int, prefix: tuple[Any, ...]) -> list[dict[str, Any]]:
        frame = sales_by_depth.get(depth)
        if frame is None or frame.empty:
            return []
        work = frame
        for i, val in enumerate(prefix):
            work = work[work[row_dims[i]].astype(str) == str(val)]
        if work.empty:
            return []
        leaf_col = row_dims[depth - 1]
        nodes: list[dict[str, Any]] = []
        for val, grp in work.groupby(leaf_col, sort=False, dropna=False):
            row = grp.iloc[0]
            key_tuple = prefix + (val,)
            soh, avail, disp = _soh_lookup(depth, key_tuple)
            ly_hit = ly_index.get(depth, {}).get(tuple(str(v) for v in key_tuple))
            metrics = _metrics_payload(
                sales=float(row.get("sales") or 0),
                qty=float(row.get("quantity") or 0),
                gross=float(row.get("gross_sales") or 0),
                disc=float(row.get("discount_amount") or 0),
                soh=soh,
                available=avail,
                dispatch=disp,
                opening=None,
                receipts=None,
                want=want,
                ly_sales=ly_hit[0] if ly_hit else 0.0,
                ly_qty=ly_hit[1] if ly_hit else 0.0,
            )
            children: list[dict[str, Any]] = []
            if depth < n:
                children = _node(depth + 1, key_tuple)
            split_metrics: dict[str, Any] = {}
            if split_df is not None and col_dim and depth == n and split_col_keys:
                smask = pd.Series(True, index=split_df.index)
                for c, v in zip(row_dims, key_tuple):
                    smask &= split_df[c].astype(str) == str(v)
                sub = split_df.loc[smask]
                for _, srow in sub.iterrows():
                    sk = str(srow[col_dim])
                    split_metrics[sk] = _metrics_payload(
                        sales=float(srow.get("sales") or 0),
                        qty=float(srow.get("quantity") or 0),
                        gross=float(srow.get("gross_sales") or 0),
                        disc=float(srow.get("discount_amount") or 0),
                        soh=None,
                        available=None,
                        dispatch=None,
                        opening=None,
                        receipts=None,
                        want=want,
                    )
            display = "" if val is None or (isinstance(val, float) and pd.isna(val)) else str(val)
            nodes.append(
                {
                    "key": "|".join(str(x) for x in key_tuple),
                    "dimension": leaf_col,
                    "value": display,
                    "level": depth - 1,
                    "metrics": metrics,
                    "split_metrics": split_metrics,
                    "children": children,
                    "record_count": int(row.get("rec_count") or 0),
                }
            )
        return nodes

    tree = _node(1, ())
    return tree, {
        "metrics": grand_metrics,
        "split_metrics": {},
        "record_count": int(gt["rec_count"]) if gt is not None else 0,
    }


_LY_FIELDS = {
    "sales_ly",
    "quantity_ly",
    "sales_growth_pct",
    "qty_growth_pct",
    "asp_ly",
    "asp_growth_pct",
    "sell_through_ly_pct",
}


def _cy_max_date(sales_path: Path, year: int, filters: dict[str, Any] | None) -> Any:
    from api.services.analytics_query_engine import (
        DuckDBSemaphoreTimeout,
        duckdb_query_session,
    )

    params: list[Any] = []
    where = _build_filter_sql(filters, year=year, params=params)
    path_sql = str(sales_path).replace("'", "''")
    sql = (
        f'SELECT MAX(CAST("date" AS DATE)) FROM read_parquet(\'{path_sql}\') '
        f"WHERE {where}"
    )
    try:
        with duckdb_query_session(timeout=30) as (con, _timing):
            row = con.execute(sql, params).fetchone()
            return row[0] if row else None
    except DuckDBSemaphoreTimeout:
        return None


def _same_window_last_year(
    sales_path: Path,
    year: int,
    filters: dict[str, Any] | None,
    cy_max: Any = None,
) -> tuple[str, str] | None:
    """Comparable last-year date window for LY measures.

    - Explicit month timeline (e.g. 2026-10) → same calendar month prior year
      (through CY max day when the month is still open) — matches pandas Analyzer.
    - Explicit date range → shift both ends by −1 year.
    - Default / FY → prior FY start through same calendar day as CY max sale.
    """
    filters = filters or {}
    mode = str(filters.get("timeline_mode") or "").strip().lower()
    raw_vals = str(filters.get("timeline_values") or "").strip()
    mx = cy_max if cy_max not in (None, "") else _cy_max_date(sales_path, year, filters)

    if mode == "month" and raw_vals:
        months = [v.strip() for v in raw_vals.split(",") if v.strip()]
        if len(months) == 1 and len(months[0]) >= 7 and months[0][4] == "-":
            y_str, m_str = months[0].split("-", 1)[:2]
            try:
                y_i, m_i = int(y_str), int(m_str)
            except ValueError:
                y_i = m_i = 0
            if y_i >= 2000 and 1 <= m_i <= 12:
                ly_y = y_i - 1
                start = f"{ly_y}-{m_i:02d}-01"
                month_end = pd.Timestamp(year=ly_y, month=m_i, day=1) + pd.offsets.MonthEnd(0)
                end = month_end
                if mx is not None:
                    cm = pd.Timestamp(mx)
                    if cm.year == y_i and cm.month == m_i:
                        end = cm - pd.DateOffset(years=1)
                return start, str(pd.Timestamp(end).date())
        # Multi-month: fall through to min/max from cy_max path below using FY window.

    if mode == "date" and raw_vals:
        parts = [v.strip() for v in raw_vals.split(",") if v.strip()]
        if parts:
            from_d = parts[0]
            to_d = parts[1] if len(parts) > 1 else parts[0]
            try:
                ly_from = pd.Timestamp(from_d) - pd.DateOffset(years=1)
                ly_to = pd.Timestamp(to_d) - pd.DateOffset(years=1)
                return str(ly_from.date()), str(ly_to.date())
            except Exception:
                pass

    if mx is None:
        return None
    ly_end = pd.Timestamp(mx) - pd.DateOffset(years=1)
    ly_start = f"{int(year) - 2}-04-01"
    return ly_start, str(ly_end.date())


def _cy_site_name_map(
    sales_path: Path, year: int, filters: dict[str, Any] | None
) -> dict[str, str]:
    from api.services.analytics_query_engine import (
        DuckDBSemaphoreTimeout,
        duckdb_query_session,
    )

    params: list[Any] = []
    where = _build_filter_sql(filters, year=year, params=params)
    path_sql = str(sales_path).replace("'", "''")
    sql = (
        "SELECT TRIM(CAST(site_id AS VARCHAR)) AS site_id, "
        "ANY_VALUE(NULLIF(TRIM(CAST(site_id_name AS VARCHAR)), '')) AS site_id_name "
        f"FROM read_parquet('{path_sql}') WHERE {where} "
        "GROUP BY 1"
    )
    try:
        with duckdb_query_session(timeout=30) as (con, _timing):
            frame = con.execute(sql, params).fetchdf()
    except DuckDBSemaphoreTimeout:
        return {}
    if frame is None or frame.empty:
        return {}
    return {
        str(r["site_id"]).strip(): str(r["site_id_name"]).strip()
        for _, r in frame.iterrows()
        if str(r.get("site_id") or "").strip() and str(r.get("site_id_name") or "").strip()
    }


def _remap_ly_store_labels(
    frames: dict[int, pd.DataFrame],
    row_dims: list[str],
    name_by_id: dict[str, str],
) -> dict[int, pd.DataFrame]:
    """Put last-year sales on this year's store label when the site code matches."""
    if "site_id_name" not in row_dims or not name_by_id:
        return frames
    out: dict[int, pd.DataFrame] = {}
    sum_cols = ("sales", "quantity", "gross_sales", "discount_amount", "rec_count")
    for depth, frame in frames.items():
        if depth <= 0 or frame is None or frame.empty or "site_id" not in frame.columns:
            out[depth] = frame
            continue
        work = frame.copy()
        mapped = work["site_id"].astype(str).str.strip().map(name_by_id)
        work["site_id_name"] = mapped.where(mapped.notna() & (mapped.astype(str).str.len() > 0), work["site_id"].astype(str))
        work = work.drop(columns=["site_id"])
        dims = [c for c in row_dims[:depth] if c in work.columns]
        keep = [c for c in sum_cols if c in work.columns]
        if dims and keep:
            work = work.groupby(dims, dropna=False, as_index=False)[keep].sum()
        out[depth] = work
    return out


def _include_ly_only_rows(
    sales_by_depth: dict[int, pd.DataFrame],
    ly_by_depth: dict[int, pd.DataFrame],
    row_dims: list[str],
) -> None:
    """Show a store that sold last year and not this year, with this-year sales at zero."""
    for depth, ly in ly_by_depth.items():
        if depth <= 0 or ly is None or ly.empty:
            continue
        cy = sales_by_depth.get(depth)
        if cy is None:
            continue
        dims = row_dims[:depth]
        if any(c not in ly.columns or c not in cy.columns for c in dims):
            continue
        cy_keys = {tuple(str(r[c]) for c in dims) for _, r in cy.iterrows()}
        extras: list[dict[str, Any]] = []
        for _, r in ly.iterrows():
            key = tuple(str(r[c]) for c in dims)
            if key in cy_keys:
                continue
            row = {c: r[c] for c in dims}
            row.update(
                sales=0.0,
                quantity=0.0,
                gross_sales=0.0,
                discount_amount=0.0,
                rec_count=0,
            )
            extras.append(row)
            cy_keys.add(key)
        if extras:
            sales_by_depth[depth] = pd.concat([cy, pd.DataFrame(extras)], ignore_index=True)


def _load_ly_depths(
    *,
    cy_path: Path,
    row_dims: list[str],
    year: int,
    filters: dict[str, Any] | None,
    cy_max: Any = None,
) -> dict[int, pd.DataFrame]:
    ly_path = _prepared_sales_path(int(year) - 1)
    if ly_path is None:
        logger.info("Analyzer DuckDB LY skipped — prior year parquet missing")
        return {}
    window = _same_window_last_year(cy_path, year, filters, cy_max=cy_max)
    ly_dims = [("site_id" if c == "site_id_name" else c) for c in row_dims]
    try:
        frames, _, _ = _aggregate_sales_duckdb(
            sales_path=ly_path,
            row_dims=ly_dims,
            col_dim=None,
            year=int(year) - 1,
            filters=filters,
            date_between=window,
        )
    except Exception as exc:
        logger.warning("Analyzer DuckDB LY aggregate failed: %s", exc)
        return {}
    if "site_id_name" in row_dims:
        names = _cy_site_name_map(cy_path, year, filters)
        frames = _remap_ly_store_labels(frames, row_dims, names)
    return frames


def try_execute_pivot_duckdb(
    *,
    rows: list[str],
    columns: list[str],
    value_fields: list[str],
    filters: dict[str, Any] | None,
    year: int,
) -> Optional[dict[str, Any]]:
    """
    Attempt DuckDB pivot. Returns analyzer-compatible payload or None to fall back.
    """
    if not use_analyzer_duckdb():
        return None

    # Skip extremely high-cardinality leaf expansions for now (Phase D handles lazy pages).
    # Still allow season→barcode but cap handled by caller via mode flag later.
    sales_path = _prepared_sales_path(year if year else 2027)
    if sales_path is None:
        logger.warning("DuckDB pivot: prepared sales parquet missing for year=%s", year)
        return None

    row_dims = [r for r in rows if r in _SALES_DIM_COLS]
    if rows and not row_dims:
        raise ValueError(f"Unsupported row dimensions: {rows}")
    col_dim = columns[0] if columns else None
    if col_dim and col_dim not in _SALES_DIM_COLS:
        raise ValueError(f"Unsupported column dimension: {col_dim}")

    want = set(value_fields) or {"sales"}
    # Last-year measures stay on DuckDB. The pandas frame is what Azure answers
    # with "cache is warming" when it cannot load the full sales table.

    t0 = time.perf_counter()
    fy_year = year if year else 2027
    sales_by_depth, split_df, diag = _aggregate_sales_duckdb(
        sales_path=sales_path,
        row_dims=row_dims,
        col_dim=col_dim,
        year=fy_year,
        filters=filters,
    )
    ly_by_depth: dict[int, pd.DataFrame] = {}
    t_ly = time.perf_counter()
    if want.intersection(_LY_FIELDS):
        ly_by_depth = _load_ly_depths(
            cy_path=sales_path,
            row_dims=row_dims,
            year=int(fy_year),
            filters=filters,
            cy_max=diag.get("max_date"),
        )
        _include_ly_only_rows(sales_by_depth, ly_by_depth, row_dims)
    diag["ly_calc_ms"] = round((time.perf_counter() - t_ly) * 1000.0, 1)

    soh_by_depth: dict[int, pd.DataFrame] = {}
    soh_path = _prepared_soh_path()
    t_soh = time.perf_counter()
    if soh_path is not None and want.intersection({"current_soh", "dispatch_qty", "sell_through_pct"}):
        aligned = _soh_aligned_to_sales(
            soh_path=soh_path,
            sales_path=sales_path,
            row_dims=row_dims,
            year=int(fy_year),
            filters=filters,
        )
        if aligned is not None:
            soh_by_depth = aligned
        elif any(d not in _STORE_LEVEL for d in row_dims):
            soh_by_depth[0] = _aggregate_soh_duckdb(soh_path=soh_path, group_cols=[], filters=filters)
            for depth in range(1, len(row_dims) + 1):
                gcols = row_dims[:depth]
                soh_by_depth[depth] = _aggregate_soh_duckdb(
                    soh_path=soh_path, group_cols=gcols, filters=filters
                )
    diag["soh_align_ms"] = round((time.perf_counter() - t_soh) * 1000.0, 1)

    split_keys: list[str] = []
    if split_df is not None and col_dim and col_dim in split_df.columns:
        split_keys = sorted(
            {
                str(v)
                for v in split_df[col_dim].dropna().unique()
                if str(v).strip() and str(v).lower() != "nan"
            }
        )[:30]

    t_tree = time.perf_counter()
    tree, grand = _build_tree_from_depth_maps(
        sales_by_depth,
        row_dims,
        soh_by_depth=soh_by_depth,
        want=want,
        split_col_keys=split_keys,
        split_df=split_df,
        col_dim=col_dim,
        ly_by_depth=ly_by_depth,
    )
    diag["tree_build_ms"] = round((time.perf_counter() - t_tree) * 1000.0, 1)

    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
    diag["total_ms"] = elapsed_ms
    diag.pop("max_date", None)
    logger.info(
        "Analyzer DuckDB pivot rows=%s cols=%s nodes=%s in %.0fms "
        "(sem_wait=%.1f grouping=%.1f split=%.1f ly=%.1f soh=%.1f tree=%.1f)",
        row_dims,
        columns,
        len(tree),
        elapsed_ms,
        float(diag.get("semaphore_wait_ms") or 0.0),
        float(diag.get("grouping_sets_ms") or 0.0),
        float(diag.get("column_split_ms") or 0.0),
        float(diag.get("ly_calc_ms") or 0.0),
        float(diag.get("soh_align_ms") or 0.0),
        float(diag.get("tree_build_ms") or 0.0),
    )
    return json_safe_payload({
        "ok": True,
        "rows": tree,
        "period": {},
        "split_col_keys": split_keys,
        "split_col_dim": col_dim or "",
        "grand_total": grand,
        "total_records": int(grand.get("record_count") or 0),
        "dimensions": row_dims,
        "measures": [{"field": f, "aggregation": "sum", "custom_label": ""} for f in sorted(want)],
        "query_engine": "duckdb",
        "engine_diag": diag,
    })

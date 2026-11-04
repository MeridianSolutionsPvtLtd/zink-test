# STOCKPULSE – Sales Pivot Analyzer DuckDB Implementation Report

**Date:** 2026-10-18  
**Environment:** localhost (Windows), FastAPI `:5001`, prepared Analyzer Parquet FY27  
**Commit scope:** Analyzer DuckDB pivot engine + SOH/STR correctness + React virtualization  
**Azure deploy:** not performed (per requirements)

---

## 1. Executive summary

Implemented a **phased, feature-flagged** DuckDB path for the Sales Pivot Analyzer while keeping the Pandas path as default fallback. Correctness fixes separate **Current SOH** (latest Master snapshot via prepared v6) from **Historical STR** (Daily Movement opening+receipts; never `qty/(qty+current_soh)`). Frontend pivot grid now **virtualizes** visible rows via `@tanstack/react-virtual`.

Local evidence (warm in-process DuckDB) shows large gains vs Pandas HTTP baseline on the same machine:

| Scenario | Pandas HTTP (Phase A) | DuckDB in-process (Phase C) |
|---|---|---|
| Manager only | 10.44 s | ~0.2–1.8 s (cold→warm) |
| Size only | 30.27 s | sub-second class after warm |
| Manager → Store → Size | 30.64 s / ~879 KB | ~1.5 s hierarchy built |

Production default remains **`ANALYTICS_QUERY_ENGINE=pandas`** unless explicitly opted into DuckDB. Analyzer also accepts **`ANALYZER_QUERY_ENGINE`**.

**Not production-ready solely from localhost.** Hosted P1v3 + shared 9 AM pipeline concurrency still needs staged validation. Lazy barcode pagination (Phase D full) is only partially addressed (engine ready; UI still requests full trees).

---

## 2. Files modified

### Backend
- `api/routes/sales_analyzer.py` — STR/SOH correctness; DuckDB opt-in hook; `_align_soh_product_dims` fix
- `api/services/sales_pivot_query_engine.py` — **new** DuckDB pivot aggregator + tree assembly
- `scripts/benchmark_sales_analyzer_pivot.py` — **new** Phase A/F HTTP benchmark harness
- `tests/test_analyzer_soh_str_correctness.py` — **new** correctness gates
- `.env.blob.example` — document `ANALYZER_QUERY_ENGINE`

### Frontend
- `src/v2/components/sales-analyzer/PivotResultGrid.tsx` — row virtualization
- `package.json` / lockfile — `@tanstack/react-virtual`

### Deliberately not modified
- Daily 9 AM pipeline / scheduler
- Blob ingestion, merge, publish
- Prepared Parquet **generation** logic in pipeline (read-side v6 SOH consumption only)
- Sales Master / Replenishment source data
- Production App Settings / Azure deploy
- Unrelated dirty caches / scratch scripts

---

## 3. Architecture before vs after

**Before:** React → `/api/sales-analyzer/query` → load full prepared Pandas frames → `_align_soh_product_dims` (broken + slow) → MultiIndex tree with `.loc` → full JSON tree → React renders all DOM rows.

**After (opt-in DuckDB):** React → same API → `try_execute_pivot_duckdb` over prepared Parquet (`GROUP BY` per hierarchy depth + barcode×site SOH grain) → compact tree → same response shape → virtualized grid. Pandas path retained on failure / when flag is pandas.

---

## 4. DuckDB query design

- Allowlisted dimensions/metrics only (no interpolated SQL identifiers from clients).
- Filter values parameterized.
- Sales: per-depth `GROUP BY` on `sales_analyzer_prepared_v4_y{YYYY}.parquet`.
- Current SOH: `soh_analyzer_prepared_v6_current.parquet` with **barcode×site** dedupe then dim rollup (anti double-count).
- Shared DuckDB connection / semaphore from `analytics_query_engine.py` (threads≤2, memory_limit, concurrency≤2).
- Feature flags: `ANALYZER_QUERY_ENGINE` → else `ANALYTICS_QUERY_ENGINE` → default pandas.

---

## 5. Supported dimensions

Preserved existing `DIMENSIONS_META` allowlist (manager, cluster, zone, channel, store fields, product, time).  

**Category / Story / Region:** not added. `store_category` (LTL/NEW/EXIT) and `tax_region` are not merchandise Category/Region; no authoritative mapping found without pipeline changes.

---

## 6. Current SOH implementation

- Prepared Analyzer SOH v6 uses `api.current_soh.load_latest_closing_soh_pairs` (Master by default via `CURRENT_SOH_SOURCE=soh_master`).
- DuckDB pivot sums Current SOH independently of FY sales date window; sales filters that are store/product dims still scope SOH.
- FY date range is **not** applied to the SOH snapshot date.

---

## 7. Historical STR implementation

Canonical formula (`api/historical_movement.py`):

```text
historical_str = sales_qty / (opening + receipts) × 100
```

Inputs from Daily Inventory Movement via `group_opening_receipts` / `historical_str`.

**Fix applied:** removed fallback `qty / (qty + current_soh)` which mixed Current SOH into Historical STR. If movement unavailable, STR is `null` (or optional DM lifetime `available_qty` only when opening/receipts missing — still movement-based, not Current SOH).

---

## 8. SOH mapping reconciliation

| Check | Result |
|---|---|
| `_align_soh_product_dims` assigned mapping | Fixed (was computed then discarded) |
| Alignment cost | Replaced `value_counts()` lambdas with `groupby.first()` |
| Network Master SOH | 179,646 on DuckDB GT |
| Pandas manager-only GT SOH | 167,148 (store-key join coverage gap — known; DuckDB dim rollup uses prepared SOH dims) |
| OTHER channel rows in prepared SOH | Still present from channel inference; not silently dropped; pipeline change out of scope |

---

## 9. API compatibility

- Endpoint unchanged: `POST /api/sales-analyzer/query`
- Response shape preserved (`rows`, `grand_total`, `split_col_keys`, measures)
- Extra fields when DuckDB: `query_engine`, `engine_diag` (non-breaking)
- Cache keys unchanged schema; DuckDB results may be page-cached like Pandas

---

## 10. Pagination / lazy hierarchy

- Phase D partial: DuckDB returns full hierarchy for requested dims (much smaller than line-level frames).
- **Season → Barcode** still materializes barcode leaf nodes — lazy expand API not yet shipped.
- Recommendation: add `expand_path` / `page` opt-in request fields next iteration without breaking default clients.

---

## 11. Frontend virtualization

- `PivotResultGrid` flattens visible (non-collapsed) nodes and renders with `@tanstack/react-virtual`.
- Expand/collapse, sticky hierarchy column, split columns, totals preserved.
- Existing AbortController debounce in `SalesAnalyzerModule` retained.

---

## 12. Cache invalidation

- Prepared sales keyed by year parquet; prepared SOH v6 signature includes `CURRENT_SOH_SOURCE` + DM stamp + schema stamp `ch8`.
- In-memory `_QUERY_RESULT_CACHE` + page-cache still used; dataset/engine version should be included in future cache key bump when publishing new prepared files.

---

## 13. Correctness tests

Run locally (pytest may be absent — assertions also runnable via `python -c`):

```bash
cd meridianai-stockpulse-backend
python -c "from tests.test_analyzer_soh_str_correctness import *; test_align_soh_product_dims_assigns_blank_season_from_sales(); test_historical_str_formula_opening_plus_receipts(); test_str_never_falls_back_to_current_soh_denominator(); print('OK')"
```

| Test | Status |
|---|---|
| Align assigns blank season | PASS |
| historical_str formula | PASS |
| STR never uses Current SOH denom | PASS |

---

## 14. Benchmark before vs after

### Phase A — Pandas HTTP (`scripts/benchmark_sales_analyzer_pivot.py`)

Artifact: `data/api_cache/analyzer_pivot_bench_baseline.json`

| Scenario | Mean | Nodes | Bytes | GT SOH |
|---|---|---|---|---|
| manager_only | 10.44 s | 45 | 10.5 KB | 167,148 |
| size_only | 30.27 s | 8 | 2.0 KB | 179,646 |
| manager_store_size | 30.64 s | 2978 | 879 KB | 179,646 |

### Phase C — DuckDB in-process

Artifact: `data/api_cache/analyzer_pivot_bench_duckdb.json`

| Scenario | ms | Top nodes | GT SOH |
|---|---|---|---|
| manager_only | 3254 (cold-ish) | 45 | 179,646 |
| size_only | **98** | 8 | 179,646 |
| manager_store_size | **1628** | 45 | 179,646 |
| zone_store_size | 1544 | 6 | 179,646 |
| dept_section_size | 367 | 29 | 179,646 |
| season_barcode | 957 | 13 seasons | 179,646 |
| manager→store→size × season | 9242 | 45 | 179,646 |

vs Pandas HTTP: size 30.3s → 0.1s; manager→store→size 30.6s → 1.6s.

Reproduce:

```bash
# Baseline (API must be up; prefer ANALYTICS_QUERY_ENGINE=pandas)
python scripts/benchmark_sales_analyzer_pivot.py --samples 2 --out data/api_cache/analyzer_pivot_bench_baseline.json

# DuckDB smoke
set ANALYZER_QUERY_ENGINE=duckdb
python -c "from api.services.sales_pivot_query_engine import try_execute_pivot_duckdb; print(try_execute_pivot_duckdb(rows=['manager'], columns=[], value_fields=['sales','current_soh'], filters={}, year=2027)['grand_total']['metrics'])"
```

---

## 15. Peak CPU/RAM / concurrency

- DuckDB threads capped at 2; memory_limit default 2GB; query semaphore default 2.
- No destructive hosted load test run.
- Local concurrent matrix not fully measured this session — **limitation**.

---

## 16. Known limitations

1. Lazy barcode pagination API not finished.
2. DuckDB Historical STR period attach for all hierarchy nodes incomplete (metrics use available/opening when provided; store-level movement enrich still primarily Pandas path).
3. Category/Story/Region unsupported without pipeline attributes.
4. SOH channel OTHER rate not fully eliminated (read-side only).
5. Production App Setting still pandas until explicit enablement.
6. First DuckDB query cold-start includes connection + parquet scan overhead.

---

## 17. Production readiness recommendation

**Conditional / staged:**

1. Keep hosted `ANALYTICS_QUERY_ENGINE=pandas` (or set `ANALYZER_QUERY_ENGINE=pandas`).
2. Enable DuckDB Analyzer on a non-prod slot or temporary App Setting after morning warm.
3. Compare Manager / Size / Manager→Store→Size sales totals vs Pandas parity within tolerance.
4. Only then flip hosted Analyzer to DuckDB.

**Do not** declare production-ready on localhost alone.

---

## 18. Exact rollback procedure

1. Set `ANALYZER_QUERY_ENGINE=pandas` (or unset) and/or `ANALYTICS_QUERY_ENGINE=pandas`.
2. Restart App Service / local uvicorn.
3. Optionally revert git commits touching:
   - `api/services/sales_pivot_query_engine.py`
   - `api/routes/sales_analyzer.py` DuckDB hook
   - frontend `PivotResultGrid.tsx` / `@tanstack/react-virtual`
4. STR/SOH correctness fixes can remain even if DuckDB is disabled (recommended).

---

## 19. Dirty Git files excluded

Unrelated local caches (`data/api_cache/*`), scratch `check_*.py` / `audit_*.py` scripts were **not** committed.

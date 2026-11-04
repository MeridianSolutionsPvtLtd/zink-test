# StockPulse Sales Analyzer — Production Readiness Checklist

Updated after DuckDB ↔ pandas correctness fixes (SOH / LY / hierarchy / page-cache).

## Correctness

- [x] October 2026 + EBO + Shirts: sales / qty / SOH / LY match DuckDB ↔ pandas
- [x] SOH join uses analyzer store join keys (not divergent prepared `_pivot_sk`)
- [x] GT SOH uses CY sales-cut stores only (LY-only rows do not inflate SOH)
- [x] Explicit month/date LY window matches DuckDB `_same_window_last_year` (not post-grain min/max)
- [x] Hierarchy node counts align for the primary month+channel+dept cut
- [x] Column-split path covered in regression
- [x] Empty department returns zeros without fallback storms

## Performance / concurrency

- [x] Page-cache RAM LRU + ETag refresh
- [x] DuckDB per-call cursor/session + semaphore
- [x] Concurrent 5-filter run without DuckDB fallback
- [x] Local cold Analyzer path ~sub-second for prepared parquet hits

## Cache safety

- [x] Daily stamp clears in-memory page cache
- [x] Hosted pulls `_daily_stamp.json` from blob (throttled) so morning warm lands
- [x] Home page-cache keys include `sales_data_revision` (Sales Master etag/mtime)
- [x] Home `refresh=1` bypasses page cache + forces sales reload
- [x] Timeline values included in page-cache key (month A ≠ month B)
- [x] Stale guard: blank/None SOH with sales>0 is rejected
- [x] Stale guard: numeric SOH=0 with sales>0 is **cacheable** (sold-through)
- [ ] Hosted: purge old pre-revision `home-*` page-cache JSON after deploy
- [ ] Hosted: confirm blob ETag pull after morning warm
- [ ] Hosted: confirm Home KPIs move after Sales Master upload + warm / Refresh

## Do not ship without

- [ ] Manual UI spot-check: Analyzer default FY + month EBO Shirts
- [ ] Confirm `ANALYTICS_QUERY_ENGINE=duckdb` on target App Service
- [ ] No automatic deploy from this branch until checklist signed off

## Known residual risks

1. Product-dimension pivots (barcode/season) still prefer pandas / may skip DuckDB SOH align.
2. Store-grain cache is process-local; workers can diverge until daily stamp / restart.
3. LY site remapping relies on site_id ↔ site_id_name; renamed stores without shared codes can still orphan LY rows.
4. SOH is a current snapshot (not as-of timeline date) — intentional, but filters must not apply sales timeline to SOH.
5. Page-cache key does not encode engine name — keep one engine per environment.

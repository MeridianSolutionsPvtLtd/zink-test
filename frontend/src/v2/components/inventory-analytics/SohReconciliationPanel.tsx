import React, { useCallback, useEffect, useState } from 'react';
import {
  AlertCircle,
  AlertTriangle,
  CheckCircle2,
  FilterX,
  Loader2,
  RefreshCw,
  Search,
} from 'lucide-react';
import { API_URL } from '../../../config';
import { SkuHoverCard } from '../common/SkuHoverCard';

type ReconSummary = {
  total_items: number;
  matched_items: number;
  only_in_actual: number;
  only_in_calc: number;
  total_calculated_soh: number;
  total_actual_soh: number;
  total_variance_qty: number;
  exact_matches_count: number;
  discrepancies_count: number;
  system_accuracy_pct: number;
  coverage_pct: number;
};

type ReconRow = {
  id: string;
  site_id: string;
  site_name: string;
  barcode: string;
  sku: string;
  department: string;
  section: string;
  source: string;
  calculated_soh: number;
  actual_soh: number;
  diff_soh: number;
  gap_pct?: number | null;
  status: 'MATCH' | 'CALCULATED_EXCESS' | 'STORE_EXCESS' | string;
  inflow: number;
  sales_qty: number;
  sell_through_pct: number;
};

function formatNum(val: number | null | undefined) {
  if (val == null || Number.isNaN(val)) return '—';
  return val.toLocaleString('en-IN');
}

function formatPct(val: number | null | undefined) {
  if (val == null || Number.isNaN(val)) return '—';
  return `${val}%`;
}

export function SohReconciliationPanel({ compact = false }: { compact?: boolean }) {
  const [siteId, setSiteId] = useState('all');
  const [sourceId, setSourceId] = useState('all');
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [discrepancyOnly, setDiscrepancyOnly] = useState(false);
  const [page, setPage] = useState(1);
  const pageSize = 50;

  const [summary, setSummary] = useState<ReconSummary | null>(null);
  const [rows, setRows] = useState<ReconRow[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const t = window.setTimeout(() => setDebouncedSearch(search.trim()), 350);
    return () => window.clearTimeout(t);
  }, [search]);

  const fetchData = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({
        site_id: siteId,
        source: sourceId,
        search: debouncedSearch,
        discrepancy_only: discrepancyOnly ? '1' : '0',
        page: String(page),
        page_size: String(pageSize),
      });
      const res = await fetch(`${API_URL}/stock-overview/reconciliation?${params}`, { signal });
      if (!res.ok) throw new Error(`Fetch failed (${res.status})`);
      const data = await res.json();
      if (signal?.aborted) return;
      if (data.ok) {
        setSummary(data.summary);
        setRows(data.rows || []);
        setTotal(data.total || 0);
      } else {
        throw new Error(data.error || 'Failed to load reconciliation data');
      }
    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === 'AbortError') return;
      setError(err instanceof Error ? err.message : 'Could not load reconciliation data');
      setRows([]);
      setSummary(null);
      setTotal(0);
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [siteId, sourceId, debouncedSearch, discrepancyOnly, page]);

  useEffect(() => {
    const controller = new AbortController();
    fetchData(controller.signal);
    return () => controller.abort();
  }, [fetchData]);

  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <div className={compact ? 'space-y-4' : 'space-y-6'}>
      {/* Filters Bar */}
      <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900/60 sm:p-5">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => {
                setDiscrepancyOnly(!discrepancyOnly);
                setPage(1);
              }}
              className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-semibold transition ${
                discrepancyOnly
                  ? 'border-slate-800 bg-slate-900 text-white dark:border-slate-200 dark:bg-slate-100 dark:text-slate-900'
                  : 'border-slate-200 bg-white text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300'
              }`}
            >
              <AlertTriangle className="h-3.5 w-3.5" />
              {discrepancyOnly ? 'Gaps only' : 'All items'}
            </button>
          </div>

          <div className="flex items-center gap-2">
            {(siteId !== 'all' || sourceId !== 'all' || search || discrepancyOnly) && (
              <button
                type="button"
                onClick={() => {
                  setSiteId('all');
                  setSourceId('all');
                  setSearch('');
                  setDiscrepancyOnly(false);
                  setPage(1);
                }}
                className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-semibold text-slate-600 transition hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300"
              >
                <FilterX className="h-3.5 w-3.5" />
                Clear Filters
              </button>
            )}
            <button
              type="button"
              onClick={() => fetchData()}
              disabled={loading}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
            >
              {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
              Refresh
            </button>
          </div>
        </div>

        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <label className="block">
            <span className="mb-1 block text-[10px] font-bold uppercase tracking-wider text-slate-400">
              Channel
            </span>
            <select
              value={sourceId}
              onChange={(e) => {
                setSourceId(e.target.value);
                setPage(1);
              }}
              className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-800 outline-none focus:ring-2 focus:ring-blue-500/20 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
            >
              <option value="all">All channels</option>
              <option value="PT">Pantaloons (PT)</option>
              <option value="SSL">Shoppers Stop (SSL)</option>
              <option value="CENTRO">Centro</option>
              <option value="ICONIC">Iconic</option>
              <option value="EBO">EBO</option>
            </select>
          </label>

          <label className="block sm:col-span-2">
            <span className="mb-1 block text-[10px] font-bold uppercase tracking-wider text-slate-400">
              Search
            </span>
            <div className="relative">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
              <input
                value={search}
                onChange={(e) => {
                  setSearch(e.target.value);
                  setPage(1);
                }}
                placeholder="Store name, site ID, barcode, SKU…"
                className="w-full rounded-xl border border-slate-200 bg-white py-2 pl-9 pr-3 text-sm text-slate-800 outline-none focus:ring-2 focus:ring-blue-500/20 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
              />
            </div>
          </label>
        </div>
      </div>

      {error && (
        <div className="flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-300">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {/* Reconciliation KPI Summary Cards */}
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
        <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900/60">
          <p className="text-xs font-medium text-slate-500 dark:text-slate-400">Calculated System SOH</p>
          <p className="mt-1 text-2xl font-bold text-sky-600 dark:text-sky-400">
            {loading ? <Loader2 className="h-6 w-6 animate-spin text-slate-400" /> : formatNum(summary?.total_calculated_soh)}
          </p>
          <p className="mt-1 text-[11px] text-slate-400">From daily_inventory_movement</p>
        </div>

        <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900/60">
          <p className="text-xs font-medium text-slate-500 dark:text-slate-400">Actual Store SOH</p>
          <p className="mt-1 text-2xl font-bold text-indigo-600 dark:text-indigo-400">
            {loading ? <Loader2 className="h-6 w-6 animate-spin text-slate-400" /> : formatNum(summary?.total_actual_soh)}
          </p>
          <p className="mt-1 text-[11px] text-slate-400">From COMBINED_SOH upload</p>
        </div>

        <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900/60">
          <p className="text-xs font-medium text-slate-500 dark:text-slate-400">Total Variance (Gap Qty)</p>
          <p className="mt-1 text-2xl font-bold text-amber-600 dark:text-amber-400">
            {loading ? <Loader2 className="h-6 w-6 animate-spin text-slate-400" /> : formatNum(summary?.total_variance_qty)}
          </p>
          <p className="mt-1 text-[11px] text-slate-400">Sum of absolute differences</p>
        </div>

        <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900/60">
          <p className="text-xs font-medium text-slate-500 dark:text-slate-400">Exact-match accuracy</p>
          <p className="mt-1 text-2xl font-bold text-slate-900 dark:text-white">
            {loading ? <Loader2 className="h-6 w-6 animate-spin text-slate-400" /> : formatPct(summary?.system_accuracy_pct)}
          </p>
          <p className="mt-1 text-[11px] text-slate-400">
            Exact matches / both-side pairs · coverage {formatPct(summary?.coverage_pct)}
          </p>
        </div>

        <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900/60">
          <p className="text-xs font-medium text-slate-500 dark:text-slate-400">Matches / Discrepancies</p>
          <p className="mt-1 text-2xl font-bold text-slate-900 dark:text-white">
            {loading ? <Loader2 className="h-6 w-6 animate-spin text-slate-400" /> : `${formatNum(summary?.exact_matches_count)} / ${formatNum(summary?.discrepancies_count)}`}
          </p>
          <p className="mt-1 text-[11px] text-slate-400">Exact match / Gap count (matched pairs)</p>
        </div>

        {/* Coverage breakdown */}
        <div className="sm:col-span-2 xl:col-span-5 grid grid-cols-3 gap-3 rounded-2xl border border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-900/40">
          <div className="text-center">
            <p className="text-[10px] font-bold uppercase tracking-wider text-slate-400">Matched Items</p>
            <p className="mt-0.5 text-xl font-bold text-slate-800 dark:text-white">{loading ? '—' : formatNum(summary?.matched_items)}</p>
            <p className="text-[10px] text-slate-400">Both calc &amp; actual present</p>
          </div>
          <div className="text-center">
            <p className="text-[10px] font-bold uppercase tracking-wider text-rose-400">Only in Actual</p>
            <p className="mt-0.5 text-xl font-bold text-rose-600 dark:text-rose-400">{loading ? '—' : formatNum(summary?.only_in_actual)}</p>
            <p className="text-[10px] text-slate-400">Store has stock, system missed</p>
          </div>
          <div className="text-center">
            <p className="text-[10px] font-bold uppercase tracking-wider text-amber-400">Only in Calc</p>
            <p className="mt-0.5 text-xl font-bold text-amber-600 dark:text-amber-400">{loading ? '—' : formatNum(summary?.only_in_calc)}</p>
            <p className="text-[10px] text-slate-400">System calculated, actual=0</p>
          </div>
        </div>
      </div>

      {/* Table */}
      <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900/60">
        <div className="border-b border-slate-200 px-5 py-4 dark:border-slate-800 flex items-center justify-between">
          <div>
            <h2 className="text-base font-semibold text-slate-900 dark:text-white">System Calculated SOH vs Actual Store SOH</h2>
            <p className="mt-0.5 text-xs text-slate-500">
              {loading ? 'Loading reconciliation rows…' : `Showing ${rows.length} of ${total.toLocaleString('en-IN')} barcode items`}
            </p>
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-full text-left text-sm">
            <thead className="bg-slate-50 text-[11px] font-bold uppercase tracking-wider text-slate-500 dark:bg-slate-900/70 dark:text-slate-400">
              <tr>
                <th className="whitespace-nowrap px-4 py-3">Site ID</th>
                <th className="whitespace-nowrap px-4 py-3">Site Name</th>
                <th className="whitespace-nowrap px-4 py-3">SKU</th>
                <th className="whitespace-nowrap px-4 py-3">Barcode</th>
                <th className="whitespace-nowrap px-4 py-3 text-right">Calculated SOH</th>
                <th className="whitespace-nowrap px-4 py-3 text-right">Actual SOH</th>
                <th className="whitespace-nowrap px-4 py-3 text-right">Gap qty</th>
                <th className="whitespace-nowrap px-4 py-3 text-right">Gap %</th>
                <th className="whitespace-nowrap px-4 py-3 text-center">Status</th>
                <th className="whitespace-nowrap px-4 py-3 text-right">Inflow</th>
                <th className="whitespace-nowrap px-4 py-3 text-right">Sales Qty</th>
                <th className="whitespace-nowrap px-4 py-3 text-right">ST %</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {loading && rows.length === 0 ? (
                <tr>
                  <td colSpan={12} className="px-4 py-16 text-center">
                    <Loader2 className="mx-auto h-8 w-8 animate-spin text-slate-400" />
                  </td>
                </tr>
              ) : rows.length === 0 ? (
                <tr>
                  <td colSpan={12} className="px-4 py-16 text-center text-sm text-slate-500">
                    No reconciliation data found for this filter.
                  </td>
                </tr>
              ) : (
                rows.map((row) => {
                  const isMatch = row.diff_soh === 0;
                  const isCalcExcess = row.diff_soh > 0;
                  return (
                    <tr key={row.id} className="hover:bg-slate-50/80 dark:hover:bg-slate-800/40">
                      <td className="whitespace-nowrap px-4 py-3.5 font-mono text-xs font-semibold text-slate-800 dark:text-slate-100">
                        {row.site_id}
                      </td>
                      <td className="px-4 py-3.5 text-slate-700 dark:text-slate-200">
                        {row.site_name || '—'}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3.5 font-medium text-slate-800 dark:text-slate-100">
                        {row.sku ? <SkuHoverCard sku={row.sku}>{row.sku}</SkuHoverCard> : '—'}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3.5 font-mono text-xs text-slate-500">
                        {row.barcode}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3.5 text-right font-semibold text-sky-700 dark:text-sky-400">
                        {row.calculated_soh.toLocaleString('en-IN')}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3.5 text-right font-semibold text-indigo-700 dark:text-indigo-400">
                        {row.actual_soh.toLocaleString('en-IN')}
                      </td>
                      <td className={`whitespace-nowrap px-4 py-3.5 text-right font-bold ${
                        isMatch ? 'text-slate-400' : isCalcExcess ? 'text-amber-600 dark:text-amber-400' : 'text-rose-600 dark:text-rose-400'
                      }`}>
                        {row.diff_soh > 0 ? `+${row.diff_soh.toLocaleString('en-IN')}` : row.diff_soh.toLocaleString('en-IN')}
                      </td>
                      <td className={`whitespace-nowrap px-4 py-3.5 text-right font-semibold ${
                        isMatch ? 'text-slate-400' : 'text-slate-700 dark:text-slate-200'
                      }`}>
                        {row.gap_pct == null ? '—' : `${row.gap_pct > 0 ? '+' : ''}${row.gap_pct}%`}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3.5 text-center">
                        {isMatch ? (
                          <span className="inline-flex items-center gap-1 rounded-md bg-slate-100 px-2 py-0.5 text-[10px] font-bold text-slate-700 dark:bg-slate-800 dark:text-slate-200">
                            <CheckCircle2 className="h-3 w-3" /> Match
                          </span>
                        ) : isCalcExcess ? (
                          <span className="inline-flex items-center gap-1 rounded-md bg-amber-50 px-2 py-0.5 text-[10px] font-bold text-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
                            Calc &gt; Actual
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 rounded-md bg-rose-50 px-2 py-0.5 text-[10px] font-bold text-rose-800 dark:bg-rose-950/40 dark:text-rose-200">
                            Actual &gt; Calc
                          </span>
                        )}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3.5 text-right font-medium text-slate-600 dark:text-slate-300">
                        {row.inflow.toLocaleString('en-IN')}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3.5 text-right font-medium text-slate-600 dark:text-slate-300">
                        {row.sales_qty.toLocaleString('en-IN')}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3.5 text-right font-semibold text-slate-700 dark:text-slate-200">
                        {row.sell_through_pct}%
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
        <div className="flex items-center justify-between border-t border-slate-200 px-5 py-3 dark:border-slate-800">
          <p className="text-xs text-slate-500 dark:text-slate-400">
            Page {page} of {totalPages} · {total.toLocaleString('en-IN')} total
          </p>
          <div className="flex gap-2">
            <button
              type="button"
              disabled={page <= 1 || loading}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-semibold text-slate-700 disabled:opacity-40 dark:border-slate-700 dark:text-slate-200"
            >
              Previous
            </button>
            <button
              type="button"
              disabled={page >= totalPages || loading}
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-semibold text-slate-700 disabled:opacity-40 dark:border-slate-700 dark:text-slate-200"
            >
              Next
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

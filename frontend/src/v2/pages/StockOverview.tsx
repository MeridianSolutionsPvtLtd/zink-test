import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertCircle,
  AlertTriangle,
  Award,
  BarChart3,
  Boxes,
  CheckCircle2,
  Clock,
  FilterX,
  GitCompare,
  Loader2,
  Package,
  Percent,
  RefreshCw,
  Scale,
  Search,
  Store,
  Target,
  TrendingDown,
  Truck,
  Warehouse,
} from 'lucide-react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import DashboardLayout from '../layouts/DashboardLayout';
import { SkuHoverCard } from '../components/common/SkuHoverCard';
import { API_URL } from '../../config';

type StockRow = {
  id: string;
  site_id: string;
  site_name: string;
  barcode: string;
  sku: string;
  size: string;
  qty: number;
  source: string;
};

type FilterOption = { id: string; label: string };

type StockInsights = {
  top_stores: { site_id: string; site_name: string; qty: number }[];
  top_skus: { sku: string; qty: number }[];
};

type StockSummary = {
  total_soh: number;
  store_count: number;
  sku_count: number;
};

type InventoryKpis = {
  store_closing_stocks: number;
  warehouse_closing_stocks: number | null;
  warehouse_dispatch: number | null;
  store_soh_last_year: number | null;
  store_soh_ly_articles: number | null;
  warehouse_dispatch_lead_time_days: number | null;
  oos_rate_pct: number | null;
  lost_sales_rate_pct: number | null;
  slow_mover_styles: number | null;
  slow_mover_actions: { discount: number; rtv: number; ibt: number };
  shrinkage_rate_pct: number | null;
  inventory_target: number | null;
  daily_inward_qty: number | null;
  daily_inward_date: string | null;
  daily_inward_rows: { date: string; qty: number }[];
  article_count: number;
};

type ReconSummary = {
  total_items: number;
  total_calculated_soh: number;
  total_actual_soh: number;
  total_variance_qty: number;
  exact_matches_count: number;
  discrepancies_count: number;
  system_accuracy_pct: number;
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
  status: 'MATCH' | 'CALCULATED_EXCESS' | 'STORE_EXCESS' | string;
  inflow: number;
  sales_qty: number;
  sell_through_pct: number;
};

type StockOverviewResponse = {
  ok: boolean;
  stores: FilterOption[];
  sources: FilterOption[];
  channels?: FilterOption[];
  summary: StockSummary;
  inventory_kpis?: InventoryKpis;
  insights: StockInsights;
  total: number;
  page: number;
  page_size: number;
  rows: StockRow[];
  detail?: string;
};

function formatNum(value: number | null | undefined) {
  if (value == null || Number.isNaN(value)) return '—';
  return value.toLocaleString('en-IN');
}

function formatPct(value: number | null | undefined) {
  if (value == null || Number.isNaN(value)) return '—';
  return `${value}%`;
}

function truncateLabel(value: string, max = 22) {
  const s = value.trim();
  return s.length > max ? `${s.slice(0, max)}…` : s;
}

function SohBarTooltip({ active, payload }: any) {
  if (!active || !payload?.length) return null;
  const row = payload[0].payload;
  return (
    <div className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs shadow-lg dark:border-slate-700 dark:bg-slate-900">
      <p className="font-semibold text-slate-800 dark:text-slate-100">{row.fullName}</p>
      {row.siteId && (
        <p className="text-slate-500 dark:text-slate-400">Site ID: {row.siteId}</p>
      )}
      <p className="mt-1 font-medium text-sky-600 dark:text-sky-400">
        SOH: {Number(payload[0].value).toLocaleString('en-IN')}
      </p>
    </div>
  );
}

type SohChartItem = {
  name: string;
  fullName: string;
  siteId?: string;
  value: number;
};

function SohBarChartCard({
  title,
  icon,
  color,
  data,
  loading,
  chartKey,
  yWidth = 150,
}: {
  title: string;
  icon: React.ReactNode;
  color: string;
  data: SohChartItem[];
  loading: boolean;
  chartKey: string;
  yWidth?: number;
}) {
  const height = Math.max(320, data.length * 36 + 48);
  return (
    <div className="rounded-2xl border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900/60">
      <div className="flex items-center gap-2 border-b border-slate-200 px-4 py-3 dark:border-slate-800">
        {icon}
        <h3 className="text-sm font-semibold text-slate-900 dark:text-white">{title}</h3>
      </div>
      <div className="p-3" style={{ height }}>
        {loading ? (
          <div className="flex h-full items-center justify-center">
            <Loader2 className="h-6 w-6 animate-spin text-slate-400" />
          </div>
        ) : data.length === 0 ? (
          <div className="flex h-full items-center justify-center text-xs text-slate-500">
            No data for current filters
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <BarChart
              key={chartKey}
              data={data}
              layout="vertical"
              margin={{ top: 4, right: 20, left: 8, bottom: 4 }}
            >
              <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" horizontal={false} />
              <XAxis
                type="number"
                tick={{ fontSize: 10, fill: '#64748b' }}
                axisLine={false}
                tickLine={false}
                label={{
                  value: 'SOH count',
                  position: 'insideBottom',
                  offset: -2,
                  fontSize: 11,
                  fill: '#94a3b8',
                }}
              />
              <YAxis
                type="category"
                dataKey="name"
                width={yWidth}
                tick={{ fontSize: 10, fill: '#64748b' }}
                axisLine={false}
                tickLine={false}
              />
              <Tooltip content={<SohBarTooltip />} cursor={{ fill: `${color}14` }} />
              <Bar
                dataKey="value"
                name="SOH"
                radius={[0, 8, 8, 0]}
                barSize={18}
                isAnimationActive
                animationDuration={900}
                animationEasing="ease-out"
              >
                {data.map((_, i) => (
                  <Cell key={i} fill={color} fillOpacity={0.92 - i * 0.05} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}

export default function StockOverview() {
  const [storeId, setStoreId] = useState('all');
  const [sourceId, setSourceId] = useState('all');
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [page, setPage] = useState(1);
  const pageSize = 50;

  // View Mode: Standard Overview vs SOH Reconciliation & Validation
  const [isReconMode, setIsReconMode] = useState(false);
  const [discrepancyOnly, setDiscrepancyOnly] = useState(false);
  const [reconSummary, setReconSummary] = useState<ReconSummary | null>(null);
  const [reconRows, setReconRows] = useState<ReconRow[]>([]);
  const [reconTotal, setReconTotal] = useState(0);

  const [stores, setStores] = useState<FilterOption[]>([{ id: 'all', label: 'All stores' }]);
  const [sources, setSources] = useState<FilterOption[]>([
    { id: 'all', label: 'All channels' },
    { id: 'PT', label: 'Pantaloons (PT)' },
    { id: 'SSL', label: 'Shoppers Stop (SSL)' },
    { id: 'CENTRO', label: 'Centro' },
    { id: 'ICONIC', label: 'Iconic' },
    { id: 'EBO', label: 'EBO' },
  ]);
  const [rows, setRows] = useState<StockRow[]>([]);
  const [insights, setInsights] = useState<StockInsights | null>(null);
  const [summary, setSummary] = useState<StockSummary | null>(null);
  const [inventoryKpis, setInventoryKpis] = useState<InventoryKpis | null>(null);
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
        site_id: storeId,
        source: sourceId,
        search: debouncedSearch,
        page: String(page),
        page_size: String(pageSize),
      });
      const res = await fetch(`${API_URL}/stock-overview?${params}`, { signal });
      if (!res.ok) {
        const data = (await res.json().catch(() => ({}))) as { detail?: string };
        throw new Error(
          typeof data.detail === 'string' ? data.detail : `Failed (${res.status})`
        );
      }
      const data = (await res.json()) as StockOverviewResponse;
      if (signal?.aborted) return;
      setStores(data.stores?.length ? data.stores : [{ id: 'all', label: 'All stores' }]);
      setSources(
        data.channels?.length
          ? data.channels
          : data.sources?.length
            ? data.sources
            : [
                { id: 'all', label: 'All channels' },
                { id: 'PT', label: 'Pantaloons (PT)' },
                { id: 'SSL', label: 'Shoppers Stop (SSL)' },
                { id: 'CENTRO', label: 'Centro' },
                { id: 'ICONIC', label: 'Iconic' },
                { id: 'EBO', label: 'EBO' },
              ]
      );
      setRows(data.rows || []);
      setInsights(data.insights ?? { top_stores: [], top_skus: [] });
      setSummary(data.summary ?? null);
      setInventoryKpis(data.inventory_kpis ?? null);
      setTotal(data.total || 0);
    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === 'AbortError') return;
      setError(err instanceof Error ? err.message : 'Could not load stock data');
      setRows([]);
      setInsights(null);
      setSummary(null);
      setInventoryKpis(null);
      setTotal(0);
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [storeId, sourceId, debouncedSearch, page]);

  const fetchReconData = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({
        site_id: storeId,
        source: sourceId,
        search: debouncedSearch,
        discrepancy_only: discrepancyOnly ? '1' : '0',
        page: String(page),
        page_size: String(pageSize),
      });
      const res = await fetch(`${API_URL}/stock-overview/reconciliation?${params}`, { signal });
      if (!res.ok) throw new Error(`Reconciliation fetch failed (${res.status})`);
      const data = await res.json();
      if (signal?.aborted) return;
      if (data.ok) {
        setReconSummary(data.summary);
        setReconRows(data.rows || []);
        setReconTotal(data.total || 0);
      } else {
        throw new Error(data.error || 'Failed to load reconciliation data');
      }
    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === 'AbortError') return;
      setError(err instanceof Error ? err.message : 'Could not load reconciliation data');
      setReconRows([]);
      setReconSummary(null);
      setReconTotal(0);
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [storeId, sourceId, debouncedSearch, discrepancyOnly, page]);

  useEffect(() => {
    const controller = new AbortController();
    if (isReconMode) {
      fetchReconData(controller.signal);
    } else {
      fetchData(controller.signal);
    }
    return () => controller.abort();
  }, [isReconMode, fetchData, fetchReconData]);

  useEffect(() => {
    if (storeId !== 'all' && stores.length > 1 && !stores.some((s) => s.id === storeId)) {
      setStoreId('all');
      setPage(1);
    }
  }, [stores, storeId]);

  useEffect(() => {
    if (sourceId !== 'all' && sources.length > 1 && !sources.some((s) => s.id === sourceId)) {
      setSourceId('all');
      setPage(1);
    }
  }, [sources, sourceId]);

  const activeTotal = isReconMode ? reconTotal : total;
  const totalPages = Math.max(1, Math.ceil(activeTotal / pageSize));
  const hasActiveFilters =
    storeId !== 'all' || sourceId !== 'all' || search.trim() !== '' || discrepancyOnly;

  const clearAllFilters = () => {
    setStoreId('all');
    setSourceId('all');
    setSearch('');
    setDiscrepancyOnly(false);
    setPage(1);
  };

  const storeChartData = useMemo(
    () =>
      (insights?.top_stores ?? []).map((item) => {
        const label = item.site_name?.trim() || item.site_id;
        return {
          name: truncateLabel(label, 24),
          fullName: label,
          siteId: item.site_id,
          value: item.qty,
        };
      }),
    [insights]
  );

  const skuChartData = useMemo(
    () =>
      (insights?.top_skus ?? []).map((item) => ({
        name: truncateLabel(item.sku, 16),
        fullName: item.sku,
        value: item.qty,
      })),
    [insights]
  );

  const chartLoading = loading && !insights;

  return (
    <DashboardLayout>
      <div className="mx-auto max-w-[1920px] space-y-6 pb-10">
        <div className="relative overflow-hidden rounded-2xl border border-slate-200/90 bg-white px-6 py-6 shadow-sm dark:border-slate-800 dark:bg-slate-900/60">
          <div
            className="pointer-events-none absolute inset-x-0 top-0 h-1 bg-gradient-to-r from-sky-500 via-indigo-500 to-violet-500 opacity-90"
            aria-hidden
          />
          <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
            <div>
              <h1 className="text-2xl font-semibold tracking-tight text-slate-900 dark:text-white sm:text-[1.65rem]">
                {isReconMode ? 'SOH Reconciliation & Validation' : 'Stock Overview'}
              </h1>
              <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                {isReconMode
                  ? 'Comparing System Movement Calculated SOH (daily_inventory_movement) vs Store Reported Actual SOH'
                  : 'Barcode-level SOH intelligence and store inventory metrics'}
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {/* Mode Toggle Button */}
              <button
                type="button"
                onClick={() => {
                  setIsReconMode(!isReconMode);
                  setPage(1);
                }}
                className={`inline-flex items-center gap-2 rounded-xl border px-4 py-2.5 text-sm font-semibold transition shadow-xs ${
                  isReconMode
                    ? 'border-emerald-500 bg-emerald-50 text-emerald-700 dark:border-emerald-500/50 dark:bg-emerald-950/40 dark:text-emerald-300'
                    : 'border-slate-200 bg-white text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200'
                }`}
              >
                <GitCompare className="h-4 w-4 text-emerald-600 dark:text-emerald-400" />
                {isReconMode ? 'Mode: Reconciliation View' : 'Validate Actual SOH'}
              </button>

              <button
                type="button"
                onClick={() => (isReconMode ? fetchReconData() : fetchData())}
                disabled={loading}
                className="inline-flex items-center justify-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
              >
                {loading ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <RefreshCw className="h-4 w-4" />
                )}
                Refresh
              </button>
            </div>
          </div>
        </div>

        <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900/60 sm:p-5">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            {isReconMode ? (
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => {
                    setDiscrepancyOnly(!discrepancyOnly);
                    setPage(1);
                  }}
                  className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-semibold transition ${
                    discrepancyOnly
                      ? 'border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-700 dark:bg-amber-950/40 dark:text-amber-200'
                      : 'border-slate-200 bg-white text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300'
                  }`}
                >
                  <AlertTriangle className="h-3.5 w-3.5 text-amber-500" />
                  {discrepancyOnly ? 'Filter: Discrepancies Only' : 'Filter: Show All Items'}
                </button>
              </div>
            ) : <div />}

            <button
              type="button"
              onClick={clearAllFilters}
              disabled={!hasActiveFilters || loading}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-semibold text-slate-600 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
            >
              <FilterX className="h-3.5 w-3.5" />
              Clear all filters
            </button>
          </div>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <label className="block">
              <span className="mb-1.5 flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-wider text-slate-400">
                <Warehouse className="h-3.5 w-3.5" />
                Channel (5 stores)
              </span>
              <select
                value={sourceId}
                onChange={(e) => {
                  setSourceId(e.target.value);
                  setStoreId('all');
                  setPage(1);
                }}
                className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm font-medium text-slate-800 outline-none ring-indigo-500/30 focus:ring-2 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
              >
                {sources.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="block">
              <span className="mb-1.5 flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-wider text-slate-400">
                <Store className="h-3.5 w-3.5" />
                Site
              </span>
              <select
                value={storeId}
                onChange={(e) => {
                  setStoreId(e.target.value);
                  setPage(1);
                }}
                className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm font-medium text-slate-800 outline-none ring-indigo-500/30 focus:ring-2 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
              >
                {stores.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="block sm:col-span-2">
              <span className="mb-1.5 flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-wider text-slate-400">
                <Search className="h-3.5 w-3.5" />
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
                  className="w-full rounded-xl border border-slate-200 bg-white py-2.5 pl-9 pr-3 text-sm text-slate-800 outline-none ring-indigo-500/30 placeholder:text-slate-400 focus:ring-2 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
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

        {/* ── RECONCILIATION SUMMARY & TABLE ── */}
        {isReconMode ? (
          <>
            {/* Reconciliation KPI Cards */}
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
              <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900/60">
                <p className="text-xs font-medium text-slate-500 dark:text-slate-400">Calculated System SOH</p>
                <p className="mt-1 text-2xl font-bold text-sky-600 dark:text-sky-400">
                  {loading ? <Loader2 className="h-6 w-6 animate-spin text-slate-400" /> : formatNum(reconSummary?.total_calculated_soh)}
                </p>
                <p className="mt-1 text-[11px] text-slate-400">From daily_inventory_movement</p>
              </div>

              <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900/60">
                <p className="text-xs font-medium text-slate-500 dark:text-slate-400">Actual Store SOH</p>
                <p className="mt-1 text-2xl font-bold text-indigo-600 dark:text-indigo-400">
                  {loading ? <Loader2 className="h-6 w-6 animate-spin text-slate-400" /> : formatNum(reconSummary?.total_actual_soh)}
                </p>
                <p className="mt-1 text-[11px] text-slate-400">From COMBINED_SOH upload</p>
              </div>

              <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900/60">
                <p className="text-xs font-medium text-slate-500 dark:text-slate-400">Total Variance (Gap Qty)</p>
                <p className="mt-1 text-2xl font-bold text-amber-600 dark:text-amber-400">
                  {loading ? <Loader2 className="h-6 w-6 animate-spin text-slate-400" /> : formatNum(reconSummary?.total_variance_qty)}
                </p>
                <p className="mt-1 text-[11px] text-slate-400">Sum of absolute differences</p>
              </div>

              <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900/60">
                <p className="text-xs font-medium text-slate-500 dark:text-slate-400">System Accuracy Rate</p>
                <p className="mt-1 text-2xl font-bold text-emerald-600 dark:text-emerald-400">
                  {loading ? <Loader2 className="h-6 w-6 animate-spin text-slate-400" /> : formatPct(reconSummary?.system_accuracy_pct)}
                </p>
                <p className="mt-1 text-[11px] text-slate-400">Percentage of 100% matching items</p>
              </div>

              <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900/60">
                <p className="text-xs font-medium text-slate-500 dark:text-slate-400">Matching vs Discrepant Barcodes</p>
                <p className="mt-1 text-2xl font-bold text-slate-900 dark:text-white">
                  {loading ? <Loader2 className="h-6 w-6 animate-spin text-slate-400" /> : `${formatNum(reconSummary?.exact_matches_count)} / ${formatNum(reconSummary?.discrepancies_count)}`}
                </p>
                <p className="mt-1 text-[11px] text-slate-400">Match Count / Discrepancy Count</p>
              </div>
            </div>

            {/* Reconciliation Table */}
            <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900/60">
              <div className="border-b border-slate-200 px-5 py-4 dark:border-slate-800 flex items-center justify-between">
                <div>
                  <h2 className="text-base font-semibold text-slate-900 dark:text-white">Calculated vs Actual SOH Reconciliation</h2>
                  <p className="mt-0.5 text-xs text-slate-500">
                    {loading ? 'Loading reconciliation rows…' : `Showing ${reconRows.length} of ${reconTotal.toLocaleString('en-IN')} barcode items`}
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
                      <th className="whitespace-nowrap px-4 py-3 text-right">Difference (Gap)</th>
                      <th className="whitespace-nowrap px-4 py-3 text-center">Status</th>
                      <th className="whitespace-nowrap px-4 py-3 text-right">Inflow</th>
                      <th className="whitespace-nowrap px-4 py-3 text-right">Sales Qty</th>
                      <th className="whitespace-nowrap px-4 py-3 text-right">ST %</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                    {loading && reconRows.length === 0 ? (
                      <tr>
                        <td colSpan={11} className="px-4 py-16 text-center">
                          <Loader2 className="mx-auto h-8 w-8 animate-spin text-emerald-500" />
                        </td>
                      </tr>
                    ) : reconRows.length === 0 ? (
                      <tr>
                        <td colSpan={11} className="px-4 py-16 text-center text-sm text-slate-500">
                          No reconciliation data found for this filter.
                        </td>
                      </tr>
                    ) : (
                      reconRows.map((row) => {
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
                            <td className="whitespace-nowrap px-4 py-3.5 text-center">
                              {isMatch ? (
                                <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 px-2.5 py-0.5 text-[10px] font-bold text-emerald-800 dark:bg-emerald-950/60 dark:text-emerald-300">
                                  <CheckCircle2 className="h-3 w-3" /> Exact Match
                                </span>
                              ) : isCalcExcess ? (
                                <span className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2.5 py-0.5 text-[10px] font-bold text-amber-800 dark:bg-amber-950/60 dark:text-amber-300">
                                  +System Excess
                                </span>
                              ) : (
                                <span className="inline-flex items-center gap-1 rounded-full bg-rose-100 px-2.5 py-0.5 text-[10px] font-bold text-rose-800 dark:bg-rose-950/60 dark:text-rose-300">
                                  -Store Excess
                                </span>
                              )}
                            </td>
                            <td className="whitespace-nowrap px-4 py-3.5 text-right font-medium text-slate-600 dark:text-slate-300">
                              {row.inflow.toLocaleString('en-IN')}
                            </td>
                            <td className="whitespace-nowrap px-4 py-3.5 text-right font-medium text-slate-600 dark:text-slate-300">
                              {row.sales_qty.toLocaleString('en-IN')}
                            </td>
                            <td className="whitespace-nowrap px-4 py-3.5 text-right font-semibold text-emerald-600 dark:text-emerald-400">
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
                  Page {page} of {totalPages} · {reconTotal.toLocaleString('en-IN')} total
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
          </>
        ) : (
          /* ── STANDARD OVERVIEW MODE ── */
          <>
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
              {(
                [
                  {
                    key: 'store_close',
                    label: 'Store Closing Stocks',
                    value: formatNum(inventoryKpis?.store_closing_stocks ?? summary?.total_soh),
                    sub: summary ? `${summary.store_count} stores · ${summary.sku_count} SKUs` : null,
                    icon: Store,
                    tone: 'sky',
                  },
                  {
                    key: 'wh_close',
                    label: 'Warehouse Closing Stocks',
                    value: formatNum(inventoryKpis?.warehouse_closing_stocks),
                    sub: inventoryKpis?.warehouse_closing_stocks == null ? 'Upload Warehouse SOH' : null,
                    icon: Warehouse,
                    tone: 'indigo',
                  },
                  {
                    key: 'wh_dispatch',
                    label: 'Warehouse Dispatch',
                    value: formatNum(inventoryKpis?.warehouse_dispatch),
                    sub: inventoryKpis?.warehouse_dispatch == null ? 'Run combine transfer' : 'Units dispatched',
                    icon: Truck,
                    tone: 'violet',
                  },
                  {
                    key: 'soh_ly',
                    label: 'Store SOH Last Year',
                    value: formatNum(inventoryKpis?.store_soh_last_year),
                    sub:
                      inventoryKpis?.store_soh_ly_articles != null
                        ? `${formatNum(inventoryKpis.store_soh_ly_articles)} articles`
                        : 'Article-wise LY file pending',
                    icon: Boxes,
                    tone: 'teal',
                  },
                  {
                    key: 'lead',
                    label: 'Warehouse Dispatch Lead Time',
                    value:
                      inventoryKpis?.warehouse_dispatch_lead_time_days != null
                        ? `${inventoryKpis.warehouse_dispatch_lead_time_days} days`
                        : '—',
                    sub: 'Median gap between dispatch dates',
                    icon: Clock,
                    tone: 'amber',
                  },
                  {
                    key: 'oos',
                    label: 'OOS / Lost Sales Rate',
                    value: formatPct(inventoryKpis?.oos_rate_pct ?? inventoryKpis?.lost_sales_rate_pct),
                    sub: 'Out-of-stock proxy vs warehouse assortment',
                    icon: Percent,
                    tone: 'rose',
                  },
                  {
                    key: 'slow',
                    label: 'Slow Mover Styles',
                    value: formatNum(inventoryKpis?.slow_mover_styles),
                    sub: inventoryKpis?.slow_mover_actions
                      ? `Action: Discount ${inventoryKpis.slow_mover_actions.discount} · RTV ${inventoryKpis.slow_mover_actions.rtv} · IBT ${inventoryKpis.slow_mover_actions.ibt}`
                      : 'High-SOH styles needing action',
                    icon: TrendingDown,
                    tone: 'amber',
                  },
                  {
                    key: 'shrink',
                    label: 'Shrinkage Rate',
                    value: formatPct(inventoryKpis?.shrinkage_rate_pct),
                    sub: 'Needs audit / cycle-count feed',
                    icon: AlertCircle,
                    tone: 'rose',
                  },
                  {
                    key: 'target',
                    label: 'Inventory Target',
                    value: formatNum(inventoryKpis?.inventory_target),
                    sub: 'Target master not configured',
                    icon: Target,
                    tone: 'indigo',
                  },
                  {
                    key: 'inward',
                    label: 'Daily Inward Details',
                    value: formatNum(inventoryKpis?.daily_inward_qty),
                    sub: inventoryKpis?.daily_inward_date
                      ? `Latest day · ${inventoryKpis.daily_inward_date}`
                      : 'No recent transfer inward',
                    icon: Package,
                    tone: 'emerald',
                  },
                ] as const
              ).map((card) => {
                const Icon = card.icon;
                const toneMap: Record<string, string> = {
                  sky: 'bg-sky-100 text-sky-600 dark:bg-sky-500/15 dark:text-sky-400',
                  indigo: 'bg-indigo-100 text-indigo-600 dark:bg-indigo-500/15 dark:text-indigo-400',
                  violet: 'bg-violet-100 text-violet-600 dark:bg-violet-500/15 dark:text-violet-400',
                  teal: 'bg-teal-100 text-teal-600 dark:bg-teal-500/15 dark:text-teal-400',
                  amber: 'bg-amber-100 text-amber-600 dark:bg-amber-500/15 dark:text-amber-400',
                  rose: 'bg-rose-100 text-rose-600 dark:bg-rose-500/15 dark:text-rose-400',
                  emerald: 'bg-emerald-100 text-emerald-600 dark:bg-emerald-500/15 dark:text-emerald-400',
                };
                return (
                  <div
                    key={card.key}
                    className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900/60"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="text-sm font-medium text-slate-500 dark:text-slate-400">
                          {card.label}
                        </p>
                        <p className="mt-1 truncate text-2xl font-bold text-slate-900 dark:text-white">
                          {loading && !inventoryKpis && !summary ? (
                            <Loader2 className="h-6 w-6 animate-spin text-slate-400" />
                          ) : (
                            card.value
                          )}
                        </p>
                        {card.sub && (
                          <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">{card.sub}</p>
                        )}
                      </div>
                      <div className={`shrink-0 rounded-lg p-3 ${toneMap[card.tone]}`}>
                        <Icon className="h-5 w-5" />
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>

            {!!inventoryKpis?.daily_inward_rows?.length && (
              <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900/60">
                <div className="border-b border-slate-200 px-5 py-4 dark:border-slate-800">
                  <h2 className="text-base font-semibold text-slate-900 dark:text-white">
                    Daily Inward Details
                  </h2>
                  <p className="mt-0.5 text-xs text-slate-500">Last 7 warehouse dispatch dates</p>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead className="bg-slate-50 text-left text-xs uppercase text-slate-500 dark:bg-slate-800/50">
                      <tr>
                        <th className="px-5 py-3 font-medium">Date</th>
                        <th className="px-5 py-3 text-right font-medium">Inward Qty</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                      {inventoryKpis.daily_inward_rows.map((row) => (
                        <tr key={row.date}>
                          <td className="px-5 py-3 text-slate-700 dark:text-slate-200">{row.date}</td>
                          <td className="px-5 py-3 text-right font-semibold text-slate-900 dark:text-white">
                            {row.qty.toLocaleString('en-IN')}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            <div className="grid gap-4 lg:grid-cols-2">
              <SohBarChartCard
                title="Top stores by SOH"
                icon={<BarChart3 className="h-4 w-4 text-sky-600" />}
                color="#0ea5e9"
                data={storeChartData}
                loading={chartLoading}
                chartKey={storeChartData.map((d) => d.siteId).join('|')}
                yWidth={150}
              />
              <SohBarChartCard
                title="Top SKUs by SOH"
                icon={<Award className="h-4 w-4 text-violet-600" />}
                color="#8b5cf6"
                data={skuChartData}
                loading={chartLoading}
                chartKey={skuChartData.map((d) => d.fullName).join('|')}
                yWidth={100}
              />
            </div>

            <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900/60">
              <div className="border-b border-slate-200 px-5 py-4 dark:border-slate-800 flex items-center justify-between">
                <div>
                  <h2 className="text-base font-semibold text-slate-900 dark:text-white">Stock lines</h2>
                  {loading && !insights && (
                    <p className="mt-1 text-xs text-amber-600 dark:text-amber-400">
                      Loading stock data… first time after server restart can take 1–2 minutes.
                    </p>
                  )}
                  {summary && (
                    <p className="mt-1 text-sm font-medium text-indigo-700 dark:text-indigo-400">
                      Total SOH: {summary.total_soh.toLocaleString('en-IN')}
                      <span className="ml-2 text-xs font-normal text-slate-500 dark:text-slate-400">
                        · {summary.store_count} stores · {summary.sku_count} SKUs
                      </span>
                    </p>
                  )}
                  <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                    {loading
                      ? 'Loading…'
                      : `Showing ${rows.length} of ${total.toLocaleString('en-IN')} rows · sorted by qty (high → low)`}
                  </p>
                </div>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full min-w-full text-left text-sm">
                  <thead className="bg-slate-50 text-[11px] font-bold uppercase tracking-wider text-slate-500 dark:bg-slate-900/70 dark:text-slate-400">
                    <tr>
                      <th className="whitespace-nowrap px-4 py-3">Site ID</th>
                      <th className="whitespace-nowrap px-4 py-3">Site name</th>
                      <th className="whitespace-nowrap px-4 py-3">SKU</th>
                      <th className="whitespace-nowrap px-4 py-3">Barcode</th>
                      <th className="whitespace-nowrap px-4 py-3">Size</th>
                      <th className="whitespace-nowrap px-4 py-3 text-right">Qty</th>
                      <th className="whitespace-nowrap px-4 py-3">Source</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                    {loading && rows.length === 0 ? (
                      <tr>
                        <td colSpan={7} className="px-4 py-16 text-center">
                          <Loader2 className="mx-auto h-8 w-8 animate-spin text-indigo-500" />
                        </td>
                      </tr>
                    ) : rows.length === 0 ? (
                      <tr>
                        <td colSpan={7} className="px-4 py-16 text-center text-sm text-slate-500">
                          No stock for this filter.
                        </td>
                      </tr>
                    ) : (
                      rows.map((row) => (
                        <tr key={row.id} className="hover:bg-indigo-50/40 dark:hover:bg-indigo-500/5">
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
                          <td className="px-4 py-3.5 text-slate-600">{row.size || '—'}</td>
                          <td className="whitespace-nowrap px-4 py-3.5 text-right font-semibold text-indigo-700 dark:text-indigo-400">
                            {row.qty.toLocaleString('en-IN')}
                          </td>
                          <td className="px-4 py-3.5">
                            <span className="inline-flex rounded-full bg-slate-100 px-2.5 py-0.5 text-[11px] font-bold text-slate-700 dark:bg-slate-800 dark:text-slate-200">
                              {row.source}
                            </span>
                          </td>
                        </tr>
                      ))
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
          </>
        )}
      </div>
    </DashboardLayout>
  );
}

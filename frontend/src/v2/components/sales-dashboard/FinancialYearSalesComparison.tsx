import React, { useEffect, useMemo, useState } from 'react';
import {
  AlertCircle,
  Calendar,
  ChevronRight,
  DollarSign,
  Loader2,
  Package,
  TrendingDown,
  TrendingUp,
} from 'lucide-react';
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  LabelList,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { API_URL } from '../../../config';

export interface FyKpi {
  net_sales: number;
  qty: number;
  asp: number;
  discount_pct: number;
  yoy_sales_growth_pct: number | null;
  yoy_qty_growth_pct: number | null;
  is_partial_fy?: boolean;
  months_count?: number;
}

export interface MomRow {
  fin_month_idx: number;
  month_name: string;
  sales_by_fy: Record<string, number>;
  qty_by_fy: Record<string, number>;
  yoy_growth: Record<string, number>;
}

export interface QuarterlyData {
  sales: Record<string, number>;
  qty: Record<string, number>;
}

export interface FyComparisonResponse {
  ok: boolean;
  period_mode?: 'fy' | 'calendar';
  available_fys: string[];
  fy_kpis: Record<string, FyKpi>;
  mom_comparison: MomRow[];
  quarterly: Record<string, QuarterlyData>;
  detail?: string;
}

const FY_COLORS = ['#3b82f6', '#10b981', '#f59e0b', '#8b5cf6', '#ec4899'];

/**
 * Role relative to the latest period on screen:
 * latest = CY (Current Year), −1 = LY, −2 = LLY.
 */
function periodRoleBadge(years: string[], index: number): 'CY' | 'LY' | 'LLY' | null {
  const fromEnd = years.length - 1 - index;
  if (fromEnd === 0) return 'CY';
  if (fromEnd === 1) return 'LY';
  if (fromEnd === 2) return 'LLY';
  return null;
}

function periodDisplayLabel(years: string[], index: number, isCalendar: boolean): string {
  const raw = years[index];
  const role = periodRoleBadge(years, index);
  const yearPart = isCalendar ? raw : raw;
  if (!role) return isCalendar ? yearPart : yearPart;
  return `${role} ${yearPart}`;
}

function formatAmount(val: number | null | undefined): string {
  const n = Number(val);
  if (!Number.isFinite(n)) return '—';
  if (n >= 10_000_000) {
    return `₹${(n / 10_000_000).toFixed(2)} Cr`;
  }
  if (n >= 100_000) {
    return `₹${(n / 100_000).toFixed(2)} L`;
  }
  return `₹${n.toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
}

function formatQty(val: number | null | undefined): string {
  const n = Number(val);
  if (!Number.isFinite(n)) return '—';
  if (n >= 100_000) {
    return `${(n / 100_000).toFixed(2)} L pcs`;
  }
  return `${n.toLocaleString('en-IN')} pcs`;
}

export type ComparisonPeriodMode = 'fy' | 'calendar';

interface FinancialYearSalesComparisonProps {
  filterQuery?: string;
  siteId?: string;
  siteIds?: string;
  sourceId?: string;
  departments?: string[];
  sections?: string[];
  seasons?: string[];
  /** When false, skip network fetch so main Sales Overview can load first. */
  enabled?: boolean;
  /** fy = Apr–Mar (default); calendar = Jan–Dec when Year filter is selected */
  periodMode?: ComparisonPeriodMode;
  /** Skip quarterly cards. Year cards and the MoM graph still show unless showYearView is false. */
  chartOnly?: boolean;
  /** FY summary cards and the MoM trend graph. */
  showYearView?: boolean;
  /** Detailed 12-month comparison table. */
  showDetailTable?: boolean;
}

export function FinancialYearSalesComparison({
  filterQuery,
  siteId = 'all',
  siteIds = '',
  sourceId = 'all',
  departments = [],
  sections = [],
  seasons = [],
  enabled = true,
  periodMode = 'fy',
  chartOnly = false,
  showYearView = true,
  showDetailTable,
}: FinancialYearSalesComparisonProps) {
  const [data, setData] = useState<FyComparisonResponse | null>(null);
  const [loading, setLoading] = useState(Boolean(enabled));
  const [error, setError] = useState<string | null>(null);
  const [metricMode, setMetricMode] = useState<'sales' | 'qty'>('sales');
  const [retryTrigger, setRetryTrigger] = useState(0);
  const isCalendar = periodMode === 'calendar';
  const detailOn = showDetailTable ?? !chartOnly;

  useEffect(() => {
    if (!enabled) {
      setLoading(true);
      return;
    }
    let cancelled = false;
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    setData(null);

    const params = new URLSearchParams({
      site_id: siteId,
      source: sourceId,
      period_mode: isCalendar ? 'calendar' : 'fy',
    });
    if (departments.length) params.set('departments', departments.join(','));
    if (sections.length) params.set('sections', sections.join(','));
    if (seasons.length) params.set('seasons', seasons.join(','));

    // Zone/grade/state/city: prefer short URLs. Huge site_ids lists get truncated
    // on Azure and FY collapses to a Sep-only ~₹2L stub — backend resolves geo.
    // Keep short site_ids (store code/name pick). Drop long lists from filters.
    const filterObj = filterQuery
      ? (Object.fromEntries(new URLSearchParams(filterQuery)) as Record<string, string>)
      : {};
    const hasGeo = Boolean(
      filterObj.zones || filterObj.states || filterObj.cities || filterObj.store_grades
    );
    const filterSiteCount = (filterObj.site_ids || '')
      .split(',')
      .map((s) => s.trim())
      .filter((s) => s && s.toLowerCase() !== 'all' && s !== '__none__').length;
    if (hasGeo && filterSiteCount > 40) {
      delete filterObj.site_ids;
    } else if (!hasGeo && siteIds) {
      const codeCount = siteIds.split(',').filter((s) => s.trim()).length;
      if (codeCount <= 40) params.set('site_ids', siteIds);
    } else if (hasGeo && filterSiteCount > 0 && filterSiteCount <= 40 && siteIds) {
      // Intentional few stores inside a zone — send expanded codes (short).
      const codeCount = siteIds.split(',').filter((s) => s.trim()).length;
      if (codeCount <= 40) params.set('site_ids', siteIds);
    }
    if (Object.keys(filterObj).length) {
      params.set('filters', JSON.stringify(filterObj));
    }

    const fetchWithRetry = async (attempt = 1): Promise<void> => {
      try {
        const r = await fetch(`${API_URL}/sales-dashboard/financial-year-comparison?${params}`, { signal: controller.signal });
        const text = await r.text();
        let res: FyComparisonResponse;
        try {
          res = text ? JSON.parse(text) : ({} as any);
        } catch {
          throw new Error(`Server returned status ${r.status}`);
        }
        if (cancelled) return;
        if (!r.ok || !res.ok) {
          if (attempt < 2 && (r.status === 502 || r.status === 503 || r.status === 504 || r.status === 500)) {
            await new Promise((resolve) => setTimeout(resolve, 1500));
            if (!cancelled) return fetchWithRetry(attempt + 1);
          }
          throw new Error(res.detail || `Server request failed (${r.status})`);
        }
        setData(res);
      } catch (err: unknown) {
        if (!cancelled) {
          if (attempt < 2 && err instanceof Error && !err.name.includes('Abort')) {
            await new Promise((resolve) => setTimeout(resolve, 1500));
            if (!cancelled) return fetchWithRetry(attempt + 1);
          }
          setError(
            err instanceof Error
              ? err.message
              : isCalendar
                ? 'Could not load calendar year comparison'
                : 'Could not load FY comparison data'
          );
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    // Let Sales /kpis warm the filtered frame first — parallel FY+season+zone was 503ing Azure.
    const timer = window.setTimeout(() => {
      if (!cancelled) void fetchWithRetry();
    }, 700);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [filterQuery, enabled, siteId, siteIds, sourceId, departments, sections, seasons, periodMode, isCalendar, retryTrigger]);

  const fys = data?.available_fys || [];
  const momRows = data?.mom_comparison || [];
  const fyKpis = data?.fy_kpis || {};
  const quarterly = data?.quarterly || {};

  // Chart data formatting
  const chartData = useMemo(() => {
    const latestFy = fys[fys.length - 1];
    const prevFy = fys.length >= 2 ? fys[fys.length - 2] : '';
    return momRows.map((r) => {
      const item: Record<string, string | number | null> = { month: r.month_name };
      fys.forEach((fy) => {
        item[fy] = metricMode === 'sales' ? r.sales_by_fy[fy] || 0 : r.qty_by_fy[fy] || 0;
      });
      if (latestFy && prevFy) {
        const cur = metricMode === 'sales' ? r.sales_by_fy[latestFy] || 0 : r.qty_by_fy[latestFy] || 0;
        const prev = metricMode === 'sales' ? r.sales_by_fy[prevFy] || 0 : r.qty_by_fy[prevFy] || 0;
        item.growthPct = prev > 0 && cur > 0 ? Math.round((1000 * (cur - prev)) / prev) / 10 : null;
      }
      return item;
    });
  }, [momRows, fys, metricMode]);

  const title = isCalendar ? 'Calendar Year Sales Comparison' : 'Financial Year (FY) Sales Comparison';
  const subtitle = isCalendar
    ? 'Month-on-Month (January to December) and Year-on-Year performance comparison'
    : 'Month-on-Month (April to March) and Year-on-Year performance comparison';
  const momTitle = isCalendar
    ? 'Month-on-Month (MoM) Trend Comparison (Jan → Dec)'
    : 'Month-on-Month (MoM) Trend Comparison (Apr → Mar)';
  const momSub = isCalendar
    ? `Comparing ${metricMode === 'sales' ? 'Net Sales Amount' : 'Units Sold Volume'} across Calendar Years`
    : `Comparing ${metricMode === 'sales' ? 'Net Sales Amount' : 'Units Sold Volume'} across Financial Years`;
  const loadingLabel = isCalendar ? 'Calculating Calendar Year metrics…' : 'Calculating Financial Year metrics…';
  const periodWord = isCalendar ? 'calendar year' : 'financial year';

  return (
    <div className="space-y-6">
      {showYearView && (
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between rounded-2xl border border-slate-200/80 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900/60">
        <div>
          <div className="flex items-center gap-2 text-sm font-bold text-slate-900 dark:text-white">
            <Calendar className="h-4 w-4 text-blue-600" />
            {title}
          </div>
          <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">{subtitle}</p>
        </div>

        {/* Toggle Sales / Units */}
        <div className="inline-flex rounded-xl border border-slate-200 bg-slate-50 p-1 dark:border-slate-700 dark:bg-slate-800">
          <button
            type="button"
            onClick={() => setMetricMode('sales')}
            className={`rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
              metricMode === 'sales'
                ? 'bg-blue-600 text-white shadow-sm'
                : 'text-slate-600 hover:text-slate-900 dark:text-slate-300 dark:hover:text-white'
            }`}
          >
            Net Sales (₹)
          </button>
          <button
            type="button"
            onClick={() => setMetricMode('qty')}
            className={`rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
              metricMode === 'qty'
                ? 'bg-blue-600 text-white shadow-sm'
                : 'text-slate-600 hover:text-slate-900 dark:text-slate-300 dark:hover:text-white'
            }`}
          >
            Units Sold (Qty)
          </button>
        </div>
      </div>
      )}

      {loading ? (
        <div className="flex h-64 flex-col items-center justify-center rounded-2xl border border-dashed border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900/60">
          <Loader2 className="h-7 w-7 animate-spin text-blue-600" />
          <span className="mt-3 text-xs font-medium text-slate-500">{loadingLabel}</span>
        </div>
      ) : error ? (
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 rounded-2xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700 dark:border-rose-900/50 dark:bg-rose-950/30 dark:text-rose-300">
          <div className="flex items-center gap-2">
            <AlertCircle className="h-5 w-5 shrink-0" />
            <span>{error}</span>
          </div>
          <button
            type="button"
            onClick={() => setRetryTrigger((c) => c + 1)}
            className="self-start sm:self-auto rounded-lg bg-rose-600 px-3 py-1.5 text-xs font-semibold text-white shadow-sm hover:bg-rose-700 transition"
          >
            Retry
          </button>
        </div>
      ) : (
        <>
          {showYearView && (
          <>
          {/* Year / FY Summary Cards */}
          <div className="grid gap-4 [grid-template-columns:repeat(auto-fit,minmax(min(100%,16rem),1fr))]">
            {fys.map((fy, i) => {
              const kpi = fyKpis[fy] || {
                net_sales: 0,
                qty: 0,
                asp: 0,
                discount_pct: 0,
                yoy_sales_growth_pct: null,
                yoy_qty_growth_pct: null,
              };
              const growth = metricMode === 'sales' ? kpi.yoy_sales_growth_pct : kpi.yoy_qty_growth_pct;
              const isPositive = (growth ?? 0) >= 0;
              const role = periodRoleBadge(fys, i);
              const displayLabel = periodDisplayLabel(fys, i, isCalendar);

              return (
                <div
                  key={fy}
                  className="relative overflow-hidden rounded-2xl border border-slate-200/80 bg-white p-5 shadow-sm transition hover:shadow-md dark:border-slate-800 dark:bg-slate-900/60"
                >
                  <div
                    className="absolute top-0 left-0 h-1 w-full"
                    style={{ backgroundColor: FY_COLORS[i % FY_COLORS.length] }}
                  />
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-1.5">
                      <span className="text-xs font-bold uppercase tracking-wider text-slate-500 dark:text-slate-400">
                        {displayLabel}
                      </span>
                      {role === 'CY' && (
                        <span className="rounded bg-blue-100 px-1.5 py-0.5 text-[10px] font-semibold text-blue-800 dark:bg-blue-950/60 dark:text-blue-300">
                          Current
                        </span>
                      )}
                      {kpi.is_partial_fy && (
                        <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-semibold text-amber-800 dark:bg-amber-950/60 dark:text-amber-300">
                          YTD ({kpi.months_count}M)
                        </span>
                      )}
                    </div>
                    {growth !== null && (
                      <span
                        title={
                          kpi.is_partial_fy
                            ? `Compared to same ${kpi.months_count} months in previous ${periodWord} (Same Period Last Year)`
                            : `Full ${isCalendar ? 'Calendar Year' : 'Year'} YoY Growth`
                        }
                        className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-[11px] font-bold ${
                          isPositive
                            ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/60 dark:text-emerald-300'
                            : 'bg-rose-100 text-rose-800 dark:bg-rose-950/60 dark:text-rose-300'
                        }`}
                      >
                        {isPositive ? (
                          <TrendingUp className="h-3 w-3" />
                        ) : (
                          <TrendingDown className="h-3 w-3" />
                        )}
                        {growth > 0 ? '+' : ''}
                        {growth}% {kpi.is_partial_fy ? 'YoY (SPLY)' : 'YoY'}
                      </span>
                    )}
                  </div>

                  <div className="mt-3">
                    <div className="text-2xl font-bold text-slate-900 dark:text-white">
                      {metricMode === 'sales' ? formatAmount(kpi.net_sales) : formatQty(kpi.qty)}
                    </div>
                    <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
                      {metricMode === 'sales' ? formatQty(kpi.qty) : formatAmount(kpi.net_sales)}
                    </p>
                  </div>

                  <div className="mt-4 flex items-center justify-between border-t border-slate-100 pt-3 text-[11px] text-slate-600 dark:border-slate-800 dark:text-slate-300">
                    <div>
                      <span className="text-slate-400">ASP: </span>
                      <span className="font-semibold text-slate-800 dark:text-slate-200">
                        ₹{kpi.asp}
                      </span>
                    </div>
                    <div>
                      <span className="text-slate-400">Discount: </span>
                      <span className="font-semibold text-slate-800 dark:text-slate-200">
                        {kpi.discount_pct}%
                      </span>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          {/* MoM Chart Comparison */}
          <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900/60">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
              <div>
                <h3 className="text-sm font-semibold text-slate-900 dark:text-white">{momTitle}</h3>
                <p className="text-xs text-slate-500 dark:text-slate-400">
                  {momSub}
                  {fys.length >= 2 ? '. Dashed line is growth % of the latest year versus the year before.' : ''}
                </p>
              </div>
            </div>

            <div className="h-[360px] w-full min-w-0 overflow-hidden">
              <ResponsiveContainer debounce={80} width="100%" height="100%" minWidth={0} minHeight={0} initialDimension={{ width: 400, height: 360 }}>
                <ComposedChart data={chartData} margin={{ top: 22, right: 12, left: 0, bottom: 0 }} barCategoryGap="18%" barGap={3}>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e2e8f0" />
                  <XAxis dataKey="month" tick={{ fontSize: 11, fill: '#64748b' }} axisLine={false} tickLine={false} />
                  <YAxis
                    yAxisId="left"
                    tick={{ fontSize: 11, fill: '#64748b' }}
                    axisLine={false}
                    tickLine={false}
                    width={48}
                    tickFormatter={(v: number) =>
                      metricMode === 'sales'
                        ? v >= 10_000_000
                          ? `${(v / 10_000_000).toFixed(1)}Cr`
                          : `${(v / 100_000).toFixed(0)}L`
                        : v >= 1_000
                          ? `${(v / 1_000).toFixed(0)}k`
                          : `${v}`
                    }
                  />
                  {fys.length >= 2 && (
                    <YAxis
                      yAxisId="right"
                      orientation="right"
                      tick={{ fontSize: 11, fill: '#d97706' }}
                      axisLine={false}
                      tickLine={false}
                      width={42}
                      tickFormatter={(v: number) => `${v}%`}
                    />
                  )}
                  <Tooltip
                    formatter={(val: number, name: string) => [
                      name === 'YoY Growth %'
                        ? `${val}%`
                        : metricMode === 'sales'
                          ? formatAmount(val)
                          : formatQty(val),
                      name,
                    ]}
                  />
                  <Legend wrapperStyle={{ fontSize: '11px', paddingTop: '10px' }} />
                  {fys.map((fy, i) => (
                    <Bar
                      key={fy}
                      yAxisId="left"
                      dataKey={fy}
                      name={periodDisplayLabel(fys, i, isCalendar)}
                      fill={FY_COLORS[i % FY_COLORS.length]}
                      radius={[4, 4, 0, 0]}
                      maxBarSize={22}
                    />
                  ))}
                  {fys.length >= 2 && (
                    <Line
                      yAxisId="right"
                      type="monotone"
                      dataKey="growthPct"
                      name="YoY Growth %"
                      stroke="#f59e0b"
                      strokeDasharray="5 5"
                      strokeWidth={2}
                      dot={{ r: 3, fill: '#f59e0b' }}
                      connectNulls={false}
                    >
                      <LabelList
                        dataKey="growthPct"
                        position="top"
                        formatter={(v: number) => (v == null || Number.isNaN(v) ? '' : `${v > 0 ? '+' : ''}${v}%`)}
                        style={{ fontSize: 9, fill: '#d97706', fontWeight: 700 }}
                      />
                    </Line>
                  )}
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          </div>
          </>
          )}

          {/* Quarterly Performance Cards */}
          {!chartOnly && (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(10rem,1fr))] gap-3">
                    {['Q1', 'Q2', 'Q3', 'Q4'].map((q) => {
              const qData = quarterly[q] || { sales: {}, qty: {} };
              const qMonths = isCalendar
                ? q === 'Q1'
                  ? 'Jan-Mar'
                  : q === 'Q2'
                    ? 'Apr-Jun'
                    : q === 'Q3'
                      ? 'Jul-Sep'
                      : 'Oct-Dec'
                : q === 'Q1'
                  ? 'Apr-Jun'
                  : q === 'Q2'
                    ? 'Jul-Sep'
                    : q === 'Q3'
                      ? 'Oct-Dec'
                      : 'Jan-Mar';
              return (
                <div
                  key={q}
                  className="rounded-xl border border-slate-200/80 bg-slate-50/80 p-4 dark:border-slate-800 dark:bg-slate-800/40"
                >
                  <div className="text-[11px] font-bold uppercase tracking-wider text-slate-500">
                    {q}{' '}
                    <span className="text-[10px] font-normal text-slate-400">({qMonths})</span>
                  </div>
                  <div className="mt-2 space-y-1.5">
                    {fys.map((fy, i) => {
                      const v = metricMode === 'sales' ? qData.sales[fy] || 0 : qData.qty[fy] || 0;
                      return (
                        <div key={fy} className="flex items-center justify-between text-xs">
                          <span className="text-slate-600 dark:text-slate-300">
                            {periodDisplayLabel(fys, i, isCalendar)}:
                          </span>
                          <span className="font-semibold text-slate-900 dark:text-white">
                            {metricMode === 'sales' ? formatAmount(v) : formatQty(v)}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>
          )}

          {detailOn && (
          <div className="overflow-hidden rounded-2xl border border-slate-200/80 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900/60">
            <div className="flex items-center justify-between border-b border-slate-100 bg-gradient-to-r from-blue-50/60 via-purple-50/40 to-transparent px-5 py-3.5 dark:border-slate-800 dark:from-blue-950/30 dark:via-purple-950/20">
              <div className="flex items-center gap-2">
                <div className="h-2 w-2 rounded-full bg-blue-600 animate-pulse" />
                <h3 className="text-xs font-bold uppercase tracking-wider text-slate-900 dark:text-white">
                  Detailed Month-on-Month Comparison Table
                </h3>
              </div>
              <span className="text-[11px] font-medium text-slate-500 dark:text-slate-400">
                {isCalendar ? '12 Calendar Months (Jan → Dec)' : '12 Financial Months (Apr → Mar)'}
              </span>
            </div>
            
            <div className="max-h-[300px] overflow-y-auto overflow-x-auto">
              <table className="w-full text-left text-xs border-collapse">
                <thead className="sticky top-0 z-10 bg-slate-100/95 backdrop-blur-sm text-[10px] uppercase tracking-wider text-slate-600 shadow-sm dark:bg-slate-800/95 dark:text-slate-300">
                  <tr>
                    <th className="px-4 py-2.5 font-bold">Month</th>
                    {fys.map((fy, i) => (
                      <th key={fy} className="px-4 py-2.5 text-right font-bold">
                        <span className="inline-flex items-center justify-end gap-1.5">
                          <span
                            className="h-2 w-2 rounded-full"
                            style={{ backgroundColor: FY_COLORS[i % FY_COLORS.length] }}
                          />
                          {periodDisplayLabel(fys, i, isCalendar)}{' '}
                          {metricMode === 'sales' ? '(Sales ₹)' : '(Units)'}
                        </span>
                      </th>
                    ))}
                    {fys.length >= 2 && (
                      <th className="px-4 py-2.5 text-center font-bold">YoY Growth %</th>
                    )}
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 text-[11px] dark:divide-slate-800/60">
                  {momRows.map((r, idx) => {
                    const latestFy = fys[fys.length - 1];
                    const prevFy = fys[fys.length - 2];
                    const yoyKey = `${latestFy} vs ${prevFy}`;
                    const yoy = r.yoy_growth[yoyKey];
                    const isEven = idx % 2 === 0;

                    return (
                      <tr
                        key={r.fin_month_idx}
                        className={`transition hover:bg-blue-50/40 dark:hover:bg-blue-950/20 ${
                          isEven ? 'bg-white dark:bg-slate-900/40' : 'bg-slate-50/50 dark:bg-slate-800/20'
                        }`}
                      >
                        <td className="px-4 py-2.5 font-bold text-slate-800 dark:text-slate-200">
                          <span className="inline-block rounded-md bg-blue-100/70 px-2 py-0.5 text-[10px] font-bold text-blue-900 dark:bg-blue-900/40 dark:text-blue-200">
                            {r.month_name}
                          </span>
                        </td>
                        {fys.map((fy, i) => {
                          const s = r.sales_by_fy[fy] || 0;
                          const q = r.qty_by_fy[fy] || 0;
                          const val = metricMode === 'sales' ? s : q;
                          const hasVal = val > 0;

                          return (
                            <td
                              key={fy}
                              className={`px-4 py-2.5 text-right font-semibold ${
                                hasVal
                                  ? 'text-slate-900 dark:text-slate-100'
                                  : 'text-slate-400 dark:text-slate-600'
                              }`}
                            >
                              {hasVal ? (
                                metricMode === 'sales' ? formatAmount(s) : formatQty(q)
                              ) : (
                                <span className="text-slate-400 dark:text-slate-600">₹0</span>
                              )}
                            </td>
                          );
                        })}
                        {fys.length >= 2 && (
                          <td className="px-4 py-2.5 text-center">
                            {yoy !== undefined ? (
                              <span
                                className={`inline-flex items-center gap-0.5 rounded-full px-2 py-0.5 text-[10px] font-bold shadow-xs ${
                                  yoy >= 0
                                    ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/80 dark:text-emerald-300'
                                    : 'bg-rose-100 text-rose-800 dark:bg-rose-950/80 dark:text-rose-300'
                                }`}
                              >
                                {yoy >= 0 ? '+' : ''}
                                {yoy}%
                              </span>
                            ) : (
                              <span className="text-slate-400">—</span>
                            )}
                          </td>
                        )}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
          )}
        </>
      )}
    </div>
  );
}

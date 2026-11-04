import React, { useEffect, useMemo, useState } from 'react';
import {
  CalendarDays,
  TrendingUp,
  TrendingDown,
  Sparkles,
  Loader2,
  AlertTriangle,
  ArrowUpRight,
  Flame,
} from 'lucide-react';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts';
import { API_URL } from '../../../config';
import { formatInr, formatNumber } from '../inventory-analytics/inventoryAnalytics';

type EventMetric = {
  sales: number;
  qty: number;
  days: number;
  avgDailySales: number;
  fromDate: string;
  toDate: string;
  isoFrom: string;
  isoTo: string;
};

type EventData = {
  id: string;
  name: string;
  dates: {
    '2023': { from: string; to: string };
    '2024': { from: string; to: string };
    '2025': { from: string; to: string };
    '2026': { from: string; to: string };
  };
  metrics: {
    '2023': EventMetric;
    '2024': EventMetric;
    '2025': EventMetric;
    '2026': EventMetric;
  };
  growth: {
    growth2025vs2024: number | null;
    growth2026vs2025: number | null;
    unitGrowth2025vs2024: number | null;
    unitGrowth2026vs2025: number | null;
  };
  topDepartments: Array<{
    department: string;
    sales: number;
    qty: number;
    year: number;
  }>;
};

type ApiResponse = {
  ok: boolean;
  events: EventData[];
  summary: {
    totalFestiveSales2024: number;
    totalFestiveSales2025: number;
    totalFestiveSales2026: number;
    totalGrowth2025vs2024: number | null;
  };
};

function formatCr(amount: number) {
  if (!amount) return '₹0';
  if (amount >= 1e7) return `₹${(amount / 1e7).toFixed(2)} Cr`;
  if (amount >= 1e5) return `₹${(amount / 1e5).toFixed(2)} L`;
  return formatInr(amount);
}

function GrowthBadge({ val }: { val: number | null }) {
  if (val === null || val === undefined) return <span className="text-slate-400">—</span>;
  const isPos = val >= 0;
  return (
    <span
      className={`inline-flex items-center gap-0.5 rounded-full px-2 py-0.5 text-[10px] font-bold ${
        isPos
          ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/60 dark:text-emerald-300'
          : 'bg-rose-100 text-rose-800 dark:bg-rose-950/60 dark:text-rose-300'
      }`}
    >
      {isPos ? <TrendingUp className="h-3 w-3" /> : <TrendingDown className="h-3 w-3" />}
      {isPos ? '+' : ''}
      {val}%
    </span>
  );
}

/** Focus Day Festival Growth & Sales Comparison View (DurgaPuja, Diwali, Christmas, Valentine). */
export function FocusFestivalComparisonView({
  refreshTrigger,
  initialData,
  skipFetch = false,
}: {
  refreshTrigger?: number;
  initialData?: ApiResponse | null;
  /** Parent already loads /api/focus-day/festival-comparison — do not call it again. */
  skipFetch?: boolean;
}) {
  const [data, setData] = useState<ApiResponse | null>(initialData || null);
  const [loading, setLoading] = useState(initialData ? false : true);
  const [error, setError] = useState<string | null>(null);
  const [selectedEvent, setSelectedEvent] = useState<string>('all');
  const [retryCount, setRetryCount] = useState(0);

  useEffect(() => {
    if (skipFetch || initialData) {
      setData(initialData ?? null);
      setLoading(!initialData);
      setError(null);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    fetch(`${API_URL}/focus-day/festival-comparison`, { signal: controller.signal })
      .then(async (res) => {
        const raw = await res.text();
        let json: ApiResponse & { detail?: string; error?: string };
        try {
          json = JSON.parse(raw);
        } catch {
          throw new Error(
            res.ok
              ? 'Backend returned HTML instead of JSON — please refresh or restart API'
              : `Server error (${res.status}): unexpected response format`
          );
        }
        if (!res.ok) throw new Error(String(json?.detail || json?.error || 'Failed to load focus day event comparison'));
        if (!controller.signal.aborted) {
          setData(json);
          setLoading(false);
        }
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setError(err instanceof Error ? err.message : 'Could not load focus day comparison');
        setLoading(false);
      });
    return () => controller.abort();
  }, [refreshTrigger, retryCount, initialData, skipFetch]);

  const events = data?.events ?? [];

  const chartData = useMemo(() => {
    return events.map((e) => ({
      name: e.name,
      '2024 Sales': e.metrics['2024'].sales,
      '2025 Sales': e.metrics['2025'].sales,
      '2026 Sales': e.metrics['2026'].sales,
    }));
  }, [events]);

  const filteredEvents = useMemo(() => {
    if (selectedEvent === 'all') return events;
    return events.filter((e) => e.id === selectedEvent);
  }, [events, selectedEvent]);

  return (
    <div className="space-y-6">
      {/* Header Banner */}
      <div className="overflow-hidden rounded-2xl border border-slate-200/80 bg-gradient-to-br from-slate-900 via-indigo-950 to-slate-900 p-6 text-white shadow-lg dark:border-slate-800">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 text-xs font-semibold tracking-wider text-amber-400 uppercase">
              <Flame className="h-4 w-4 animate-pulse" />
              Festival Day Growth Analytics (2024 – 2026)
            </div>
            <h2 className="mt-1 text-xl font-extrabold tracking-tight sm:text-2xl text-white">
              DurgaPuja · Diwali · Christmas · Valentine
            </h2>
            <p className="mt-1 max-w-2xl text-xs text-slate-300">
              Multi-year sales performance & YoY growth comparison calculated directly from sales master data across exact festival date windows.
            </p>
          </div>

          {/* KPI Summary Cards */}
          {data?.summary && (
            <div className="flex flex-wrap items-center gap-3">
              <div className="rounded-xl border border-white/10 bg-white/10 px-4 py-2.5 backdrop-blur-md">
                <div className="text-[10px] uppercase tracking-wider text-slate-300 font-medium">
                  2024 Festive Sales
                </div>
                <div className="text-lg font-bold text-amber-300">
                  {formatCr(data.summary.totalFestiveSales2024)}
                </div>
              </div>
              <div className="rounded-xl border border-white/10 bg-white/10 px-4 py-2.5 backdrop-blur-md">
                <div className="text-[10px] uppercase tracking-wider text-slate-300 font-medium">
                  2025 Festive Sales
                </div>
                <div className="text-lg font-bold text-emerald-300">
                  {formatCr(data.summary.totalFestiveSales2025)}
                </div>
              </div>
              {data.summary.totalGrowth2025vs2024 !== null && (
                <div className="rounded-xl border border-white/10 bg-white/10 px-4 py-2.5 backdrop-blur-md">
                  <div className="text-[10px] uppercase tracking-wider text-slate-300 font-medium">
                    2025 vs 2024 YoY
                  </div>
                  <div className="text-lg font-bold">
                    <GrowthBadge val={data.summary.totalGrowth2025vs2024} />
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {loading ? (
        <div className="flex flex-col items-center justify-center py-16 text-sm text-slate-500 bg-white rounded-2xl border border-slate-200 dark:bg-slate-900 dark:border-slate-800">
          <Loader2 className="mb-2 h-8 w-8 animate-spin text-blue-600" />
          Loading multi-year festival sales analysis…
        </div>
      ) : error ? (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-rose-200 bg-rose-50 p-6 text-sm text-rose-700 dark:bg-rose-950/40 dark:border-rose-900 dark:text-rose-300">
          <div className="flex items-center gap-2">
            <AlertTriangle className="h-5 w-5 shrink-0" />
            <span>{error}</span>
          </div>
          <button
            onClick={() => setRetryCount((c) => c + 1)}
            className="rounded-lg bg-rose-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-rose-700 transition"
          >
            Retry
          </button>
        </div>
      ) : (
        <>
          {/* Main Events Comparison Table */}
          <div className="overflow-hidden rounded-2xl border border-slate-200/80 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900/60">
            <div className="border-b border-slate-100 px-5 py-4 dark:border-slate-800 flex flex-wrap items-center justify-between gap-2">
              <div>
                <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-900 dark:text-white">
                  <CalendarDays className="h-4 w-4 text-blue-600" />
                  Festival Day Events — Date Ranges & YoY Growth Comparison
                </h3>
                <p className="mt-0.5 text-xs text-slate-500">
                  Exact event dates provided for 2023, 2024, 2025, 2026 with sales (₹), units, daily velocity, and growth %
                </p>
              </div>

              {/* Event Filter Pills */}
              <div className="flex flex-wrap items-center gap-1.5">
                <button
                  onClick={() => setSelectedEvent('all')}
                  className={`rounded-lg px-3 py-1 text-xs font-semibold transition-colors ${
                    selectedEvent === 'all'
                      ? 'bg-blue-600 text-white'
                      : 'bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300'
                  }`}
                >
                  All 4 Events
                </button>
                {events.map((e) => (
                  <button
                    key={e.id}
                    onClick={() => setSelectedEvent(e.id)}
                    className={`rounded-lg px-3 py-1 text-xs font-semibold transition-colors ${
                      selectedEvent === e.id
                        ? 'bg-blue-600 text-white'
                        : 'bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300'
                    }`}
                  >
                    {e.name}
                  </button>
                ))}
              </div>
            </div>

            <div className="p-4">
              <div className="overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-700">
                <table className="min-w-[1000px] w-full text-left text-[11px]">
                  <thead className="sticky top-0 z-10 bg-slate-50 text-[10px] uppercase tracking-wide text-slate-500 dark:bg-slate-800/90">
                    <tr>
                      <th className="px-3 py-3">Event</th>
                      <th className="px-3 py-3">2023 Dates</th>
                      <th className="px-3 py-3">2024 Window & Sales</th>
                      <th className="px-3 py-3">2025 Window & Sales</th>
                      <th className="px-3 py-3">2026 Window & Sales</th>
                      <th className="px-3 py-3 text-right">2025 vs 2024 YoY</th>
                      <th className="px-3 py-3 text-right">2026 vs 2025 YoY</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                    {filteredEvents.map((e) => {
                      const m24 = e.metrics['2024'];
                      const m25 = e.metrics['2025'];
                      const m26 = e.metrics['2026'];
                      return (
                        <tr
                          key={e.id}
                          className="hover:bg-slate-50/80 dark:hover:bg-slate-800/40 transition-colors"
                        >
                          {/* Event Name */}
                          <td className="px-3 py-3 font-bold text-slate-900 dark:text-white">
                            <div className="flex items-center gap-1.5">
                              <span className="h-2 w-2 rounded-full bg-amber-500"></span>
                              <span className="text-xs">{e.name}</span>
                            </div>
                          </td>

                          {/* 2023 Dates */}
                          <td className="px-3 py-3 text-slate-500">
                            <div className="font-mono text-[10px]">
                              {e.dates['2023'].from} to {e.dates['2023'].to}
                            </div>
                            <div className="text-[10px] text-slate-400">
                              {m24.days} days
                            </div>
                          </td>

                          {/* 2024 Sales */}
                          <td className="px-3 py-3">
                            <div className="font-mono text-[10px] text-slate-400">
                              {e.dates['2024'].from} to {e.dates['2024'].to}
                            </div>
                            <div className="font-bold text-slate-900 dark:text-white">
                              {formatCr(m24.sales)}
                            </div>
                            <div className="text-[10px] text-slate-500">
                              {formatNumber(m24.qty)} units · Avg {formatInr(m24.avgDailySales)}/day
                            </div>
                          </td>

                          {/* 2025 Sales */}
                          <td className="px-3 py-3">
                            <div className="font-mono text-[10px] text-slate-400">
                              {e.dates['2025'].from} to {e.dates['2025'].to}
                            </div>
                            <div className="font-bold text-emerald-700 dark:text-emerald-300">
                              {formatCr(m25.sales)}
                            </div>
                            <div className="text-[10px] text-slate-500">
                              {formatNumber(m25.qty)} units · Avg {formatInr(m25.avgDailySales)}/day
                            </div>
                          </td>

                          {/* 2026 Sales */}
                          <td className="px-3 py-3">
                            <div className="font-mono text-[10px] text-slate-400">
                              {e.dates['2026'].from} to {e.dates['2026'].to}
                            </div>
                            {m26.sales > 0 ? (
                              <>
                                <div className="font-bold text-blue-700 dark:text-blue-300">
                                  {formatCr(m26.sales)}
                                </div>
                                <div className="text-[10px] text-slate-500">
                                  {formatNumber(m26.qty)} units · Avg {formatInr(m26.avgDailySales)}/day
                                </div>
                              </>
                            ) : (
                              <span className="inline-flex rounded-full bg-slate-100 px-2 py-0.5 text-[9px] font-semibold text-slate-500 dark:bg-slate-800">
                                Upcoming / Live
                              </span>
                            )}
                          </td>

                          {/* YoY 2025 vs 2024 */}
                          <td className="px-3 py-3 text-right">
                            <GrowthBadge val={e.growth.growth2025vs2024} />
                            <div className="mt-0.5 text-[9px] text-slate-400">
                              Units: {e.growth.unitGrowth2025vs2024 !== null ? `${e.growth.unitGrowth2025vs2024}%` : '—'}
                            </div>
                          </td>

                          {/* YoY 2026 vs 2025 */}
                          <td className="px-3 py-3 text-right">
                            {m26.sales > 0 ? (
                              <>
                                <GrowthBadge val={e.growth.growth2026vs2025} />
                                <div className="mt-0.5 text-[9px] text-slate-400">
                                  Units: {e.growth.unitGrowth2026vs2025 !== null ? `${e.growth.unitGrowth2026vs2025}%` : '—'}
                                </div>
                              </>
                            ) : (
                              <span className="text-[10px] text-slate-400">—</span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          {/* Chart & Department Breakdown Grid */}
          <div className="grid gap-6 lg:grid-cols-2">
            {/* Multi-year Bar Chart */}
            <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900/60">
              <div className="mb-4 flex items-center justify-between">
                <div>
                  <h4 className="text-sm font-semibold text-slate-900 dark:text-white">
                    Festival Sales Comparison Chart
                  </h4>
                  <p className="text-xs text-slate-500">
                    Comparing total sales ₹ across 2024, 2025, 2026
                  </p>
                </div>
                <Sparkles className="h-4 w-4 text-amber-500" />
              </div>

              <ResponsiveContainer width="100%" height={280}>
                <BarChart data={chartData}>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="name" tick={{ fontSize: 11 }} />
                  <YAxis
                    tickFormatter={(v) => formatCr(v)}
                    tick={{ fontSize: 10 }}
                  />
                  <Tooltip formatter={(v: number) => [formatInr(v), 'Sales']} />
                  <Legend />
                  <Bar dataKey="2024 Sales" fill="#94a3b8" radius={[4, 4, 0, 0]} />
                  <Bar dataKey="2025 Sales" fill="#10b981" radius={[4, 4, 0, 0]} />
                  <Bar dataKey="2026 Sales" fill="#3b82f6" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>

            {/* Top Department Breakdown during Festive Periods */}
            <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900/60">
              <div className="mb-4 flex items-center justify-between">
                <div>
                  <h4 className="text-sm font-semibold text-slate-900 dark:text-white">
                    Top Department Drivers (Festive Season)
                  </h4>
                  <p className="text-xs text-slate-500">
                    Highest selling departments during DurgaPuja, Diwali, Christmas & Valentine
                  </p>
                </div>
                <ArrowUpRight className="h-4 w-4 text-blue-500" />
              </div>

              <div className="space-y-3">
                {events.map((e) => (
                  <div
                    key={e.id}
                    className="rounded-xl border border-slate-100 bg-slate-50/60 p-3 dark:border-slate-800 dark:bg-slate-800/40"
                  >
                    <div className="mb-2 flex items-center justify-between">
                      <span className="text-xs font-bold text-slate-900 dark:text-white">
                        {e.name}
                      </span>
                      <span className="text-[10px] font-semibold text-slate-500">
                        Top Categories
                      </span>
                    </div>

                    <div className="flex flex-wrap gap-1.5">
                      {e.topDepartments.length > 0 ? (
                        e.topDepartments.map((d) => (
                          <div
                            key={d.department}
                            className="inline-flex items-center gap-1.5 rounded-lg bg-white px-2.5 py-1 text-[11px] font-medium shadow-xs border border-slate-200/60 dark:bg-slate-800 dark:border-slate-700"
                          >
                            <span className="text-slate-800 dark:text-slate-200">
                              {d.department}
                            </span>
                            <span className="font-bold text-blue-600 dark:text-blue-400">
                              {formatCr(d.sales)}
                            </span>
                          </div>
                        ))
                      ) : (
                        <span className="text-[10px] text-slate-400">
                          Upcoming event
                        </span>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

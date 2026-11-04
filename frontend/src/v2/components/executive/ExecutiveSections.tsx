import React, { useEffect, useMemo, useState } from 'react';
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ComposedChart,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  Treemap,
  XAxis,
  YAxis,
} from 'recharts';
import { DASHBOARD_YEARS } from './ExecutiveHeader';
import { ChartCard } from './ChartCard';
import { CHART_COLORS, formatInr, formatPctPlain } from './utils';
import { BarChart3, Layers, TrendingUp, Download } from 'lucide-react';
import { IndiaRealMap } from './IndiaRealMap';
import { API_URL } from '../../../config';
import { SkuHoverCard } from '../common/SkuHoverCard';

export type MultiYearTrendMonth = {
  month: number;
  label: string;
  sales_2024: number;
  sales_2025: number;
  sales_2026: number | null;
  yoy_pct?: number | null;
};

export type MultiYearTrendData = {
  months: MultiYearTrendMonth[];
  ytd_2025: number;
  ytd_2026: number;
  yoy_growth_pct: number;
  total_2024: number;
  total_2025: number;
};

export function SalesTrendSection({
  multiYearData,
  loading,
}: {
  multiYearData?: MultiYearTrendData | null;
  loading?: boolean;
}) {
  const [viewMode, setViewMode] = useState<'bar' | 'line' | 'combo'>('bar');

  const chartData = useMemo(() => {
    if (!multiYearData?.months?.length) return [];
    return multiYearData.months.map((m) => ({
      ...m,
      sales_2026: m.sales_2026 !== null && m.sales_2026 !== undefined ? Number(m.sales_2026) : undefined,
      sales_2025: Number(m.sales_2025 || 0),
      sales_2024: Number(m.sales_2024 || 0),
    }));
  }, [multiYearData]);

  const yoy = multiYearData?.yoy_growth_pct ?? 0;

  const renderTooltip = ({ active, payload, label }: any) => {
    if (!active || !payload?.length) return null;
    const dataObj = payload[0]?.payload;
    const s26 = dataObj?.sales_2026;
    const s25 = dataObj?.sales_2025;
    const monthYoy =
      dataObj?.yoy_pct !== undefined && dataObj?.yoy_pct !== null && Number.isFinite(Number(dataObj.yoy_pct))
        ? Number(dataObj.yoy_pct).toFixed(1)
        : null;

    return (
      <div className="rounded-xl border border-slate-200 bg-white/95 p-3 text-xs shadow-xl backdrop-blur dark:border-slate-700 dark:bg-slate-900/95">
        <div className="flex items-center justify-between gap-4 border-b border-slate-100 pb-1.5 dark:border-slate-800">
          <p className="font-bold text-slate-800 dark:text-white">{label}</p>
          {monthYoy !== null && (
            <span
              className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${
                Number(monthYoy) >= 0
                  ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300'
                  : 'bg-rose-100 text-rose-700 dark:bg-rose-950 dark:text-rose-300'
              }`}
            >
              YoY {Number(monthYoy) >= 0 ? '+' : ''}
              {monthYoy}%
            </span>
          )}
        </div>
        <div className="mt-2 space-y-1.5">
          <div className="flex items-center justify-between gap-4">
            <span className="flex items-center gap-1.5 font-medium text-slate-600 dark:text-slate-300">
              <span className="inline-block h-2.5 w-2.5 rounded-full bg-indigo-500" />
              Selected period
            </span>
            <span className="font-semibold text-indigo-600 dark:text-indigo-400">
              {s26 !== null && s26 !== undefined ? formatInr(Number(s26)) : 'Upcoming'}
            </span>
          </div>
          <div className="flex items-center justify-between gap-4">
            <span className="flex items-center gap-1.5 font-medium text-slate-600 dark:text-slate-300">
              <span className="inline-block h-2.5 w-2.5 rounded-full bg-emerald-500" />
              Last year
            </span>
            <span className="font-semibold text-emerald-600 dark:text-emerald-400">
              {s25 !== undefined ? formatInr(Number(s25)) : '₹0'}
            </span>
          </div>
          <div className="flex items-center justify-between gap-4">
            <span className="flex items-center gap-1.5 font-medium text-slate-600 dark:text-slate-300">
              <span className="inline-block h-2.5 w-2.5 rounded-full bg-amber-500" />
              Two years ago
            </span>
            <span className="font-semibold text-amber-600 dark:text-amber-400">
              {dataObj?.sales_2024 !== undefined ? formatInr(Number(dataObj.sales_2024)) : '₹0'}
            </span>
          </div>
        </div>
      </div>
    );
  };

  return (
    <ChartCard
      title="Executive Overview"
      subtitle={
        multiYearData
          ? `Selected period vs prior two years · YoY ${yoy > 0 ? '+' : ''}${yoy}%`
          : 'Selected period compared with the same dates in the previous two years'
      }
      loading={loading}
      action={
        <div className="inline-flex rounded-lg border border-slate-200 bg-slate-100 p-0.5 dark:border-slate-700 dark:bg-slate-800">
          <button
            type="button"
            onClick={() => setViewMode('bar')}
            className={`flex items-center gap-1 rounded-md px-2.5 py-1 text-xs font-medium transition-all ${
              viewMode === 'bar'
                ? 'bg-white text-blue-600 shadow-sm dark:bg-slate-700 dark:text-blue-400'
                : 'text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-200'
            }`}
            title="Grouped Bar View (Clear Side-by-Side Comparison)"
          >
            <BarChart3 className="h-3.5 w-3.5" />
            <span>Bars</span>
          </button>
          <button
            type="button"
            onClick={() => setViewMode('line')}
            className={`flex items-center gap-1 rounded-md px-2.5 py-1 text-xs font-medium transition-all ${
              viewMode === 'line'
                ? 'bg-white text-blue-600 shadow-sm dark:bg-slate-700 dark:text-blue-400'
                : 'text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-200'
            }`}
            title="Line Trend View"
          >
            <TrendingUp className="h-3.5 w-3.5" />
            <span>Lines</span>
          </button>
          <button
            type="button"
            onClick={() => setViewMode('combo')}
            className={`flex items-center gap-1 rounded-md px-2.5 py-1 text-xs font-medium transition-all ${
              viewMode === 'combo'
                ? 'bg-white text-blue-600 shadow-sm dark:bg-slate-700 dark:text-blue-400'
                : 'text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-200'
            }`}
            title="Combo View (Bars + Selected Period Trend Line)"
          >
            <Layers className="h-3.5 w-3.5" />
            <span>Combo</span>
          </button>
        </div>
      }
    >
      <div className="h-[340px] min-h-[340px] w-full min-w-0 overflow-hidden">
        <ResponsiveContainer debounce={80} width="100%" height="100%" minHeight={340} minWidth={0}>
          {viewMode === 'bar' ? (
            <BarChart data={chartData} margin={{ top: 12, right: 12, left: 0, bottom: 0 }} barGap={3} barCategoryGap="20%">
              <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
              <XAxis dataKey="label" tick={{ fontSize: 11, fill: '#64748b' }} axisLine={false} tickLine={false} />
              <YAxis tick={{ fontSize: 11, fill: '#64748b' }} axisLine={false} tickLine={false} tickFormatter={(v) => formatInr(v, true)} width={58} />
              <Tooltip content={renderTooltip} />
              <Legend iconType="circle" wrapperStyle={{ fontSize: 11, paddingTop: 12 }} />
              <Bar dataKey="sales_2026" name="Selected period" fill="#6366f1" radius={[4, 4, 0, 0]} maxBarSize={32} />
              <Bar dataKey="sales_2025" name="Last year" fill="#10b981" radius={[4, 4, 0, 0]} maxBarSize={32} />
              <Bar dataKey="sales_2024" name="Two years ago" fill="#f59e0b" radius={[4, 4, 0, 0]} maxBarSize={32} />
            </BarChart>
          ) : viewMode === 'line' ? (
            <LineChart data={chartData} margin={{ top: 12, right: 12, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
              <XAxis dataKey="label" tick={{ fontSize: 11, fill: '#64748b' }} axisLine={false} tickLine={false} />
              <YAxis tick={{ fontSize: 11, fill: '#64748b' }} axisLine={false} tickLine={false} tickFormatter={(v) => formatInr(v, true)} width={58} />
              <Tooltip content={renderTooltip} />
              <Legend iconType="circle" wrapperStyle={{ fontSize: 11, paddingTop: 12 }} />
              <Line
                type="monotone"
                dataKey="sales_2026"
                name="Selected period"
                stroke="#6366f1"
                strokeWidth={3}
                dot={{ r: 4, fill: '#6366f1', strokeWidth: 2, stroke: '#ffffff' }}
                activeDot={{ r: 6 }}
                connectNulls
              />
              <Line
                type="monotone"
                dataKey="sales_2025"
                name="Last year"
                stroke="#10b981"
                strokeWidth={2.2}
                dot={{ r: 3, fill: '#10b981' }}
                connectNulls
              />
              <Line
                type="monotone"
                dataKey="sales_2024"
                name="Two years ago"
                stroke="#f59e0b"
                strokeWidth={2}
                strokeDasharray="4 4"
                dot={{ r: 3, fill: '#f59e0b' }}
                connectNulls
              />
            </LineChart>
          ) : (
            <ComposedChart data={chartData} margin={{ top: 12, right: 12, left: 0, bottom: 0 }} barGap={2} barCategoryGap="25%">
              <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
              <XAxis dataKey="label" tick={{ fontSize: 11, fill: '#64748b' }} axisLine={false} tickLine={false} />
              <YAxis tick={{ fontSize: 11, fill: '#64748b' }} axisLine={false} tickLine={false} tickFormatter={(v) => formatInr(v, true)} width={58} />
              <Tooltip content={renderTooltip} />
              <Legend iconType="circle" wrapperStyle={{ fontSize: 11, paddingTop: 12 }} />
              <Bar dataKey="sales_2024" name="Two years ago" fill="#cbd5e1" radius={[4, 4, 0, 0]} maxBarSize={28} />
              <Bar dataKey="sales_2025" name="Last year" fill="#10b981" opacity={0.8} radius={[4, 4, 0, 0]} maxBarSize={28} />
              <Line
                type="monotone"
                dataKey="sales_2026"
                name="Selected period"
                stroke="#6366f1"
                strokeWidth={3.2}
                dot={{ r: 5, fill: '#6366f1', strokeWidth: 2, stroke: '#ffffff' }}
                connectNulls
              />
            </ComposedChart>
          )}
        </ResponsiveContainer>
      </div>
    </ChartCard>
  );
}

export type TargetRow = { name: string; target: number; actual: number; achievement: number };

export function TargetAchievementSection({
  data,
  year = 2027,
  level = 'zone',
  onYearChange,
  onLevelChange,
  loading,
  onDrill,
}: {
  data: TargetRow[];
  year?: number;
  level?: 'zone' | 'region';
  onYearChange?: (year: number) => void;
  onLevelChange?: (level: 'zone' | 'region') => void;
  loading?: boolean;
  onDrill?: (name: string) => void;
}) {
  const fyYearLabel = `FY-${String(year).slice(-2)}`;

  return (
    <ChartCard
      title="Target vs Achievement"
      subtitle={`Grouped performance by ${level === 'zone' ? 'Zone' : 'Region (State)'} (${fyYearLabel})`}
      loading={loading}
      action={
        <div className="flex flex-wrap items-center gap-2">
          {/* Year Toggle */}
          <div className="flex rounded-xl border border-slate-200/80 bg-slate-50/80 p-0.5 dark:border-slate-700 dark:bg-slate-800/50">
            {[...DASHBOARD_YEARS].reverse().map((y) => {
              const label = `FY-${String(y).slice(-2)}`;
              return (
                <button
                  key={y}
                  type="button"
                  onClick={() => onYearChange?.(y)}
                  className={`rounded-lg px-2.5 py-1 text-[11px] font-semibold transition-all ${
                    year === y
                      ? 'bg-white text-blue-600 shadow-sm dark:bg-slate-900 dark:text-blue-400'
                      : 'text-slate-500 hover:text-slate-700 dark:text-slate-400'
                  }`}
                >
                  {label}
                </button>
              );
            })}
          </div>

          {/* Group By Toggle */}
          <div className="flex gap-1 rounded-xl border border-slate-200/80 bg-slate-50/80 p-0.5 dark:border-slate-700 dark:bg-slate-800/50">
            {(['zone', 'region'] as const).map((l) => (
              <button
                key={l}
                type="button"
                onClick={() => onLevelChange?.(l)}
                className={`rounded-lg px-2.5 py-1 text-[11px] font-semibold capitalize transition-all ${
                  level === l
                    ? 'bg-white text-blue-600 shadow-sm dark:bg-slate-900 dark:text-blue-400'
                    : 'text-slate-500 hover:text-slate-700 dark:text-slate-400'
                }`}
              >
                {l}
              </button>
            ))}
          </div>
        </div>
      }
    >
      <div className="h-[300px] min-h-[300px] w-full min-w-0 overflow-hidden">
        <ResponsiveContainer debounce={80} width="100%" height="100%" minHeight={300} minWidth={0}>
          <BarChart data={data} barGap={4} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
            <XAxis dataKey="name" tick={{ fontSize: 11, fill: '#64748b' }} axisLine={false} tickLine={false} />
            <YAxis tick={{ fontSize: 11, fill: '#64748b' }} axisLine={false} tickLine={false} tickFormatter={(v) => formatInr(v, true)} width={56} />
            <Tooltip
              content={({ active, payload, label }) => {
                if (!active || !payload?.length) return null;
                const row = payload[0].payload as TargetRow;
                return (
                  <div className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs shadow-xl dark:border-slate-700 dark:bg-slate-900">
                    <p className="font-semibold">{label}</p>
                    <p className="text-indigo-600">Actual ({year}): {formatInr(row.actual)}</p>
                    <p className="text-slate-500">Target ({year}): {formatInr(row.target)}</p>
                    <p className="text-emerald-600">Achievement: {formatPctPlain(row.achievement)}</p>
                    <p className="mt-1 text-[10px] text-slate-400">Click bar to drill down</p>
                  </div>
                );
              }}
            />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            <Bar dataKey="target" name={`Target (${year})`} fill="#cbd5e1" radius={[6, 6, 0, 0]} onClick={(d) => onDrill?.((d as TargetRow).name)} className="cursor-pointer" />
            <Bar dataKey="actual" name={`Actual (${year})`} radius={[6, 6, 0, 0]} onClick={(d) => onDrill?.((d as TargetRow).name)} className="cursor-pointer">
              {data.map((entry) => (
                <Cell key={entry.name} fill={entry.achievement >= 100 ? '#10b981' : entry.achievement >= 85 ? '#6366f1' : '#f43f5e'} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </ChartCard>
  );
}

type ZoneRow = { zone: string; sales: number; growth?: number };

export function SalesByZoneSection({
  data,
  selectedZone,
  onZoneClick,
  loading,
}: {
  data: ZoneRow[];
  selectedZone: string | null;
  onZoneClick: (zone: string) => void;
  loading?: boolean;
}) {
  return (
    <ChartCard title="Sales by Zone" subtitle="Click a zone to cross-filter all widgets" loading={loading}>
      <div className="h-[280px] min-h-[280px] w-full min-w-0 overflow-hidden">
        <ResponsiveContainer debounce={80} width="100%" height="100%" minHeight={280} minWidth={0}>
          <BarChart data={data} layout="vertical" margin={{ top: 4, right: 16, left: 4, bottom: 4 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" horizontal={false} />
            <XAxis type="number" tick={{ fontSize: 11, fill: '#64748b' }} axisLine={false} tickLine={false} tickFormatter={(v) => formatInr(v, true)} />
            <YAxis type="category" dataKey="zone" tick={{ fontSize: 11, fill: '#64748b' }} axisLine={false} tickLine={false} width={64} />
            <Tooltip
              content={({ active, payload }) => {
                if (!active || !payload?.length) return null;
                const row = payload[0].payload as ZoneRow;
                const hasGrowth =
                  typeof row?.growth === 'number' && Number.isFinite(row.growth);
                return (
                  <div className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs shadow-xl dark:border-slate-700 dark:bg-slate-900">
                    <p className="font-semibold">{row.zone}</p>
                    <p>Sales: {formatInr(row.sales)}</p>
                    <p
                      className={
                        !hasGrowth
                          ? 'font-medium text-slate-500'
                          : (row.growth as number) >= 0
                            ? 'font-medium text-emerald-600'
                            : 'font-medium text-rose-600'
                      }
                    >
                      Growth:{' '}
                      {!hasGrowth
                        ? '—'
                        : `${(row.growth as number) >= 0 ? '+' : ''}${(row.growth as number).toFixed(1)}%`}
                    </p>
                  </div>
                );
              }}
            />
            <Bar dataKey="sales" radius={[0, 8, 8, 0]} barSize={22} className="cursor-pointer" onClick={(d) => onZoneClick((d as ZoneRow).zone)}>
              {data.map((entry) => (
                <Cell
                  key={entry.zone}
                  fill={selectedZone === entry.zone ? '#4f46e5' : '#818cf8'}
                  opacity={selectedZone && selectedZone !== entry.zone ? 0.35 : 1}
                />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </ChartCard>
  );
}

const CHANNEL_COLOR_MAP: Record<string, string> = {
  PT: '#6366f1',
  SSL: '#0284c7',
  CENTRO: '#10b981',
  ICONIC: '#f59e0b',
  EBO: '#8b5cf6',
};
const CHANNEL_PALETTE = ['#6366f1', '#0284c7', '#10b981', '#f59e0b', '#8b5cf6', '#ec4899', '#06b6d4', '#64748b'];

export type ChannelRow = { name: string; value: number; units?: number; share_pct?: number; color?: string };

export function SalesByChannelSection({
  data,
  loading,
  totalSales,
}: {
  data: ChannelRow[];
  loading?: boolean;
  /** Backend channel_total — UI must not sum */
  totalSales?: number;
}) {
  const total =
    typeof totalSales === 'number' && Number.isFinite(totalSales)
      ? totalSales
      : data.reduce((s, d) => s + (Number(d.value) || 0), 0);

  return (
    <ChartCard title="Sales by Channel" subtitle="Omni-channel revenue mix" loading={loading}>
      <div className="relative h-[280px] w-full min-h-[280px] min-w-0">
        <ResponsiveContainer debounce={80} width="100%" height="100%" minHeight={280} minWidth={0}>
          <PieChart>
            <Pie data={data} dataKey="value" nameKey="name" cx="50%" cy="50%" innerRadius={72} outerRadius={108} paddingAngle={3} animationDuration={900}>
              {data.map((entry, idx) => {
                const cKey = String(entry.name || '').trim().toUpperCase();
                const fill = entry.color || CHANNEL_COLOR_MAP[cKey] || CHANNEL_PALETTE[idx % CHANNEL_PALETTE.length];
                return <Cell key={entry.name || idx} fill={fill} stroke="transparent" />;
              })}
            </Pie>
            <Tooltip
              formatter={(v: number, name: string) => {
                const item = data.find((d) => d.name === name);
                const pct =
                  item?.share_pct != null && Number.isFinite(Number(item.share_pct))
                    ? Number(item.share_pct).toFixed(1)
                    : '0';
                return [`${pct}% · ${formatInr(v)}`, name];
              }}
            />
            <Legend
              iconType="circle"
              wrapperStyle={{ fontSize: 11 }}
              formatter={(val: string) => {
                const item = data.find((d) => d.name === val);
                if (!item) return val;
                const pct = Number(item.share_pct);
                const pctStr = Number.isFinite(pct) ? (pct < 1 ? pct.toFixed(1) : pct.toFixed(0)) : '0';
                return `${val} (${pctStr}%)`;
              }}
            />
          </PieChart>
        </ResponsiveContainer>
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
          <p className="text-[10px] font-medium uppercase tracking-wider text-slate-400">Total Sales</p>
          <p className="text-lg font-bold text-slate-900 dark:text-white">{formatInr(total, true)}</p>
        </div>
      </div>
    </ChartCard>
  );
}

export type StoreRow = {
  rank: number;
  store: string;
  zone: string;
  sales: number;
  target: number;
  achievement: number;
  growth?: number;
};

function normalizeStoreRow(row: Partial<StoreRow> & { store?: string }, index: number): StoreRow {
  const sales = Number(row.sales);
  const target = Number(row.target);
  const achievementRaw = Number(row.achievement);
  const growthRaw = row.growth;
  const growth =
    growthRaw !== undefined && growthRaw !== null && Number.isFinite(Number(growthRaw))
      ? Number(growthRaw)
      : undefined;
  // Achievement must come from backend — never invent sales/target % on UI
  const achievement = Number.isFinite(achievementRaw) ? achievementRaw : 0;
  return {
    rank: Number.isFinite(Number(row.rank)) ? Number(row.rank) : index + 1,
    store: String(row.store ?? '').trim() || `Store ${index + 1}`,
    zone: String(row.zone ?? '').trim() || '—',
    sales: Number.isFinite(sales) ? sales : 0,
    target: Number.isFinite(target) ? target : 0,
    achievement,
    growth,
  };
}

export function StorePerformanceGrid({
  title,
  subtitle,
  variant,
  filterQuery,
}: {
  title: string;
  subtitle: string;
  variant: 'top' | 'bottom';
  filterQuery: string;
}) {
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [sortKey, setSortKey] = useState<keyof StoreRow>('rank');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc');
  const [page, setPage] = useState(1);
  const [rows, setRows] = useState<StoreRow[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const pageSize = 5;

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedSearch(search.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [search]);

  useEffect(() => {
    setPage(1);
  }, [filterQuery, debouncedSearch, sortKey, sortDir]);

  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams(filterQuery);
    params.set('page', String(page));
    params.set('page_size', String(pageSize));
    params.set('search', debouncedSearch);
    params.set('sort', String(sortKey));
    params.set('sort_dir', sortDir);
    setLoading(true);
    const path = variant === 'top' ? 'top-stores' : 'bottom-stores';
    fetch(`${API_URL}/executive-overview/${path}?${params}`, { signal: controller.signal })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (controller.signal.aborted || !data?.ok) return;
        const list = Array.isArray(data.rows) ? data.rows : [];
        setRows(list.map((row: StoreRow, i: number) => normalizeStoreRow(row, i)));
        setTotal(Number(data.total) || 0);
      })
      .catch((err: unknown) => {
        if ((err as { name?: string }).name === 'AbortError') return;
        setRows([]);
        setTotal(0);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [filterQuery, page, debouncedSearch, sortKey, sortDir, variant]);

  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const pageRows = rows;

  const toggleSort = (key: keyof StoreRow) => {
    if (sortKey === key) setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    else {
      setSortKey(key);
      setSortDir('desc');
    }
  };

  const accent = variant === 'top' ? 'text-emerald-600' : 'text-amber-600';
  const badgeBg = variant === 'top' ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-500/15' : 'bg-amber-50 text-amber-700 dark:bg-amber-500/15';

  return (
    <ChartCard title={title} subtitle={subtitle} loading={loading}>
      <div className="mb-3 flex items-center gap-2">
        <input
          value={search}
          onChange={(e) => { setSearch(e.target.value); setPage(1); }}
          placeholder="Search stores…"
          className="w-full max-w-xs rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs focus:border-indigo-400 focus:outline-none dark:border-slate-700 dark:bg-slate-950 dark:text-slate-200"
        />
      </div>
      <div className="min-w-0 overflow-x-auto rounded-xl border border-slate-100 dark:border-slate-800">
        <table className="w-full min-w-[640px] text-left text-xs">
          <thead>
            <tr className="border-b border-slate-100 bg-slate-50/80 dark:border-slate-800 dark:bg-slate-800/40">
              {(['rank', 'store', 'zone', 'sales', 'target', 'achievement', 'growth'] as const).map((col) => (
                <th key={col} className="px-3 py-2.5">
                  <button type="button" onClick={() => toggleSort(col)} className="font-semibold uppercase tracking-wider text-slate-500 hover:text-indigo-600">
                    {col === 'achievement' ? 'Ach. %' : col.charAt(0).toUpperCase() + col.slice(1)}
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {pageRows.map((row) => (
              <tr key={row.store} className="border-b border-slate-50 transition hover:bg-indigo-50/40 dark:border-slate-800/60 dark:hover:bg-indigo-500/5">
                <td className="px-3 py-2.5">
                  <span className={`inline-flex h-6 w-6 items-center justify-center rounded-lg text-[11px] font-bold ${badgeBg}`}>
                    {row.rank}
                  </span>
                </td>
                <td className="px-3 py-2.5 font-semibold text-slate-800 dark:text-slate-100">{row.store}</td>
                <td className="px-3 py-2.5 text-slate-500">{row.zone}</td>
                <td className="px-3 py-2.5 font-medium">{formatInr(row.sales, true)}</td>
                <td className="px-3 py-2.5 text-slate-500">{formatInr(row.target, true)}</td>
                <td className="px-3 py-2.5">
                  <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${row.achievement >= 100 ? 'bg-emerald-50 text-emerald-700' : row.achievement >= 85 ? 'bg-indigo-50 text-indigo-700' : 'bg-rose-50 text-rose-700'}`}>
                    {formatPctPlain(row.achievement)}
                  </span>
                </td>
                <td className="px-3 py-2.5 font-semibold text-slate-500">
                  {typeof row.growth === 'number' && Number.isFinite(row.growth) ? (
                    <span className={row.growth >= 0 ? accent : 'text-rose-600'}>
                      {row.growth >= 0 ? '+' : ''}{row.growth.toFixed(1)}%
                    </span>
                  ) : (
                    '—'
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mt-3 flex items-center justify-between text-[11px] text-slate-500">
        <span>Page {page} of {totalPages} · {total.toLocaleString('en-IN')} stores</span>
        <div className="flex gap-1">
          <button type="button" disabled={page <= 1} onClick={() => setPage((p) => p - 1)} className="rounded-lg border px-2 py-1 disabled:opacity-40">Prev</button>
          <button type="button" disabled={page >= totalPages} onClick={() => setPage((p) => p + 1)} className="rounded-lg border px-2 py-1 disabled:opacity-40">Next</button>
        </div>
      </div>
    </ChartCard>
  );
}

function TreemapContent(props: {
  x?: number;
  y?: number;
  width?: number;
  height?: number;
  name?: string;
  size?: number;
  fill?: string;
}) {
  const { x = 0, y = 0, width = 0, height = 0, name = '', size = 0, fill = '#6366f1' } = props;
  if (width < 4 || height < 4) return null;
  return (
    <g>
      <rect x={x} y={y} width={width} height={height} fill={fill} rx={8} className="transition-opacity hover:opacity-90" />
      {width > 50 && height > 36 && (
        <>
          <text x={x + 8} y={y + 18} fill="#fff" fontSize={11} fontWeight={600}>{name}</text>
          <text x={x + 8} y={y + 32} fill="rgba(255,255,255,0.85)" fontSize={10}>{formatInr(size, true)}</text>
        </>
      )}
    </g>
  );
}

const CAT_COLORS = ['#6366f1', '#3b82f6', '#10b981', '#f59e0b', '#8b5cf6', '#ec4899', '#06b6d4', '#64748b'];

export type CategoryNode = { name: string; size: number; sales?: number; share_pct?: number; fill?: string };

export function CategoryTreemapSection({
  data,
  loading,
  totalSales: totalFromApi,
}: {
  data: CategoryNode[];
  loading?: boolean;
  /** Backend category_total */
  totalSales?: number;
}) {
  const [viewMode, setViewMode] = useState<'donut' | 'bars' | 'treemap'>('donut');

  const totalSales =
    typeof totalFromApi === 'number' && Number.isFinite(totalFromApi)
      ? totalFromApi
      : data.reduce((acc, curr) => acc + (curr.size || 0), 0);

  const sortedData = useMemo(() => {
    return [...data]
      .map((item, idx) => ({
        ...item,
        fill: item.fill || CAT_COLORS[idx % CAT_COLORS.length],
        share:
          item.share_pct != null && Number.isFinite(Number(item.share_pct))
            ? Number(item.share_pct)
            : 0,
      }))
      .sort((a, b) => b.size - a.size);
  }, [data]);

  return (
    <ChartCard
      title="Top Categories"
      subtitle="Revenue contribution & category mix"
      loading={loading}
      action={
        <div className="flex items-center gap-1 rounded-lg bg-slate-100 p-0.5 dark:bg-slate-800">
          <button
            type="button"
            onClick={() => setViewMode('donut')}
            className={`rounded-md px-2 py-1 text-[11px] font-semibold transition ${
              viewMode === 'donut'
                ? 'bg-white text-blue-600 shadow-sm dark:bg-slate-900 dark:text-blue-400'
                : 'text-slate-500 hover:text-slate-900 dark:text-slate-400'
            }`}
          >
            Donut Mix
          </button>
          <button
            type="button"
            onClick={() => setViewMode('bars')}
            className={`rounded-md px-2 py-1 text-[11px] font-semibold transition ${
              viewMode === 'bars'
                ? 'bg-white text-blue-600 shadow-sm dark:bg-slate-900 dark:text-blue-400'
                : 'text-slate-500 hover:text-slate-900 dark:text-slate-400'
            }`}
          >
            Rank Bars
          </button>
          <button
            type="button"
            onClick={() => setViewMode('treemap')}
            className={`rounded-md px-2 py-1 text-[11px] font-semibold transition ${
              viewMode === 'treemap'
                ? 'bg-white text-blue-600 shadow-sm dark:bg-slate-900 dark:text-blue-400'
                : 'text-slate-500 hover:text-slate-900 dark:text-slate-400'
            }`}
          >
            Treemap
          </button>
        </div>
      }
    >
      <div className="h-[280px] min-h-[280px] w-full min-w-0 overflow-hidden">
        {viewMode === 'donut' && (
          <div className="grid h-full min-w-0 gap-4 [grid-template-columns:repeat(auto-fit,minmax(min(100%,14rem),1fr))]">
            <div className="relative h-full min-w-0 w-full">
              <ResponsiveContainer debounce={80} width="100%" height="100%" minWidth={0}>
                <PieChart>
                  <Pie
                    data={sortedData}
                    dataKey="size"
                    nameKey="name"
                    cx="50%"
                    cy="50%"
                    innerRadius={55}
                    outerRadius={85}
                    paddingAngle={3}
                    animationDuration={800}
                  >
                    {sortedData.map((entry) => (
                      <Cell key={entry.name} fill={entry.fill} stroke="transparent" />
                    ))}
                  </Pie>
                  <Tooltip formatter={(v: number) => formatInr(v)} />
                </PieChart>
              </ResponsiveContainer>
              <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
                <p className="text-[10px] font-medium uppercase tracking-wider text-slate-400">Total</p>
                <p className="text-sm font-bold text-slate-900 dark:text-white">{formatInr(totalSales, true)}</p>
              </div>
            </div>
            <div className="flex min-w-0 flex-col justify-center space-y-2 overflow-y-auto pr-1">
              {sortedData.slice(0, 6).map((item) => (
                <div key={item.name} className="flex flex-col space-y-1">
                  <div className="flex items-center justify-between text-xs">
                    <div className="flex items-center gap-2">
                      <span className="h-2.5 w-2.5 rounded-full shrink-0" style={{ backgroundColor: item.fill }} />
                      <span className="font-semibold text-slate-800 dark:text-slate-200 truncate max-w-[120px]">{item.name}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="font-bold text-slate-900 dark:text-white">{formatInr(item.size, true)}</span>
                      <span className="rounded-md bg-slate-100 px-1.5 py-0.5 text-[10px] font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                        {item.share.toFixed(1)}%
                      </span>
                    </div>
                  </div>
                  <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
                    <div
                      className="h-full rounded-full transition-all duration-500"
                      style={{ width: `${Math.min(100, Math.max(4, item.share))}%`, backgroundColor: item.fill }}
                    />
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {viewMode === 'bars' && (
          <ResponsiveContainer debounce={80} width="100%" height="100%" minWidth={0}>
            <BarChart data={sortedData} layout="vertical" margin={{ top: 4, right: 24, left: 8, bottom: 4 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" horizontal={false} />
              <XAxis type="number" tickFormatter={(v) => formatInr(v, true)} tick={{ fontSize: 11, fill: '#64748b' }} axisLine={false} tickLine={false} />
              <YAxis type="category" dataKey="name" tick={{ fontSize: 11, fill: '#64748b' }} width={95} axisLine={false} tickLine={false} />
              <Tooltip formatter={(v: number) => formatInr(v)} />
              <Bar dataKey="size" radius={[0, 8, 8, 0]} barSize={20}>
                {sortedData.map((entry) => (
                  <Cell key={entry.name} fill={entry.fill} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        )}

        {viewMode === 'treemap' && (
          <ResponsiveContainer debounce={80} width="100%" height="100%" minWidth={0}>
            <Treemap data={sortedData} dataKey="size" nameKey="name" content={<TreemapContent />} animationDuration={800} />
          </ResponsiveContainer>
        )}
      </div>
    </ChartCard>
  );
}

export function DepartmentBarSection({ data, loading }: { data: { name: string; sales: number }[]; loading?: boolean }) {
  return (
    <ChartCard title="Top Departments" subtitle="Category-level performance" loading={loading} className="h-full">
      <div className="h-[460px] min-h-[460px] w-full min-w-0 overflow-hidden">
        <ResponsiveContainer debounce={80} width="100%" height="100%" minHeight={460} minWidth={0}>
          <BarChart data={data} layout="vertical" margin={{ top: 4, right: 16, left: 4, bottom: 4 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" horizontal={false} />
            <XAxis type="number" tickFormatter={(v) => formatInr(v, true)} tick={{ fontSize: 11, fill: '#64748b' }} axisLine={false} tickLine={false} />
            <YAxis type="category" dataKey="name" tick={{ fontSize: 11, fill: '#64748b' }} width={100} axisLine={false} tickLine={false} />
            <Tooltip formatter={(v: number) => formatInr(v)} />
            <Bar dataKey="sales" radius={[0, 6, 6, 0]} barSize={16}>
              {data.map((_, i) => (
                <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </ChartCard>
  );
}

export type SkuRow = {
  sku: string;
  category: string;
  sales: number;
  stock: number;
  margin: number;
  sellThrough: number;
  sell_through?: number;
  units?: number;
  hue: number;
  image_url?: string;
};

function resolveImageUrl(sku: string, imageUrl?: string): string {
  const raw = (imageUrl || '').trim();
  if (raw.startsWith('http://') || raw.startsWith('https://')) return raw;
  const encodedSku = encodeURIComponent(String(sku || '').trim());
  const path = `/api/product-images/${encodedSku}`;
  const base = API_URL.replace(/\/api$/, '');
  return `${base}${path}`;
}

export function SkuImageThumbnail({ sku, imageUrl, hue, className = "h-10 w-10" }: { sku: string; imageUrl?: string; hue: number; className?: string }) {
  const [imgError, setImgError] = useState(false);
  const src = resolveImageUrl(sku, imageUrl);
  useEffect(() => setImgError(false), [src]);

  if (!imgError && src) {
    return (
      <img
        src={src}
        alt={sku}
        loading="lazy"
        className={`${className} rounded-xl object-cover shadow-sm border border-slate-200/60 dark:border-slate-700 bg-slate-100 transition-transform group-hover:scale-105`}
        onError={() => setImgError(true)}
      />
    );
  }

  return (
    <div
      className={`${className} rounded-xl shadow-sm flex items-center justify-center text-[10px] font-bold text-white/90 uppercase tracking-tighter overflow-hidden`}
      style={{ background: `linear-gradient(135deg, hsl(${hue}, 65%, 55%), hsl(${hue + 30}, 70%, 40%))` }}
    >
      {sku.substring(0, 3)}
    </div>
  );
}

export function TopSkuSection({ filterQuery }: { filterQuery: string }) {
  const [viewMode, setViewMode] = useState<'top' | 'bottom' | 'all'>('top');
  const [searchQuery, setSearchQuery] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [page, setPage] = useState(1);
  const [rows, setRows] = useState<SkuRow[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const pageSize = viewMode === 'all' ? 20 : 10;

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedSearch(searchQuery.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [searchQuery]);

  useEffect(() => {
    setPage(1);
  }, [filterQuery, debouncedSearch, viewMode]);

  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams(filterQuery);
    params.set('view', viewMode);
    params.set('page', String(page));
    params.set('page_size', String(pageSize));
    params.set('search', debouncedSearch);
    setLoading(true);
    fetch(`${API_URL}/executive-overview/skus?${params}`, { signal: controller.signal })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (controller.signal.aborted || !data?.ok) return;
        const list = Array.isArray(data.rows) ? data.rows : [];
        setRows(
          list.map((s: SkuRow, i: number) => ({
            sku: s.sku,
            category: s.category || '—',
            sales: Number(s.sales || 0),
            stock: Number(s.stock || 0),
            margin: Number(s.margin || 0),
            sellThrough: Number(s.sellThrough ?? s.sell_through ?? 0),
            image_url: s.image_url || '',
            hue: 220 + i * 18,
          }))
        );
        setTotal(Number(data.total) || 0);
      })
      .catch((err: unknown) => {
        if ((err as { name?: string }).name === 'AbortError') return;
        setRows([]);
        setTotal(0);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [filterQuery, viewMode, page, pageSize, debouncedSearch]);

  const sortedRows = rows;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  const handleDownloadCsv = async () => {
    const params = new URLSearchParams(filterQuery);
    params.set('view', viewMode);
    params.set('page', '1');
    params.set('page_size', '500');
    params.set('search', debouncedSearch);
    const res = await fetch(`${API_URL}/executive-overview/skus?${params}`);
    const data = await res.json().catch(() => null);
    const allSorted: SkuRow[] = Array.isArray(data?.rows) ? data.rows : rows;
    if (!allSorted.length) return;
    const headers = ['Rank', 'SKU Code', 'Category', 'Sales Amount (INR)', 'Stock Units', 'Margin %', 'Sell Through %'];
    const csvLines = [
      headers.join(','),
      ...allSorted.map((r, i) =>
        [
          i + 1,
          `"${r.sku}"`,
          `"${r.category}"`,
          r.sales,
          r.stock,
          `${r.margin}%`,
          `${r.sellThrough}%`,
        ].join(',')
      ),
    ];
    const blob = new Blob([csvLines.join('\n')], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.setAttribute('download', `SKU_Performance_Report_${new Date().toISOString().slice(0, 10)}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  return (
    <ChartCard
      title="SKU Performance Overview"
      subtitle="Complete ranking of SKUs by sales revenue and inventory health"
      loading={loading}
      action={
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex rounded-lg border border-slate-200 bg-slate-100 p-0.5 dark:border-slate-800 dark:bg-slate-900">
            <button
              onClick={() => setViewMode('top')}
              className={`rounded-md px-2.5 py-1 text-xs font-semibold transition ${
                viewMode === 'top'
                  ? 'bg-white text-slate-900 shadow-sm dark:bg-slate-800 dark:text-white'
                  : 'text-slate-500 hover:text-slate-900 dark:text-slate-400'
              }`}
            >
              🔥 Top 10 Best Sellers
            </button>
            <button
              onClick={() => setViewMode('bottom')}
              className={`rounded-md px-2.5 py-1 text-xs font-semibold transition ${
                viewMode === 'bottom'
                  ? 'bg-white text-slate-900 shadow-sm dark:bg-slate-800 dark:text-white'
                  : 'text-slate-500 hover:text-slate-900 dark:text-slate-400'
              }`}
            >
              ⚠️ Bottom 10 (Slow Moving)
            </button>
            <button
              onClick={() => setViewMode('all')}
              className={`rounded-md px-2.5 py-1 text-xs font-semibold transition ${
                viewMode === 'all'
                  ? 'bg-white text-slate-900 shadow-sm dark:bg-slate-800 dark:text-white'
                  : 'text-slate-500 hover:text-slate-900 dark:text-slate-400'
              }`}
            >
              📊 All SKUs ({total})
            </button>
          </div>

          <button
            onClick={handleDownloadCsv}
            className="flex items-center gap-1.5 rounded-lg border border-emerald-300 bg-emerald-50 px-3 py-1 text-xs font-bold text-emerald-700 hover:bg-emerald-100 dark:border-emerald-800/60 dark:bg-emerald-950/40 dark:text-emerald-300"
          >
            <Download className="h-3.5 w-3.5" />
            Download Excel
          </button>
        </div>
      }
    >
      {viewMode === 'all' && (
        <div className="mb-3">
          <input
            type="text"
            placeholder="Search SKU or Category..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full max-w-xs rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs text-slate-800 focus:border-indigo-500 focus:outline-none dark:border-slate-700 dark:bg-slate-900 dark:text-white"
          />
        </div>
      )}

      <div className="min-w-0 overflow-x-auto rounded-xl border border-slate-100 dark:border-slate-800">
        <table className="w-full min-w-[720px] text-left text-xs">
          <thead>
            <tr className="border-b border-slate-100 bg-slate-50/80 dark:border-slate-800 dark:bg-slate-800/40">
              <th className="px-3 py-2.5 font-semibold text-slate-500">Rank</th>
              <th className="px-3 py-2.5 font-semibold text-slate-500">Product</th>
              <th className="px-3 py-2.5 font-semibold text-slate-500">SKU</th>
              <th className="px-3 py-2.5 font-semibold text-slate-500">Category</th>
              <th className="px-3 py-2.5 font-semibold text-slate-500">Sales</th>
              <th className="px-3 py-2.5 font-semibold text-slate-500">Stock</th>
              <th className="px-3 py-2.5 font-semibold text-slate-500">Margin</th>
              <th className="px-3 py-2.5 font-semibold text-slate-500">Sell Through</th>
            </tr>
          </thead>
          <tbody>
            {sortedRows.map((row, idx) => (
              <tr key={row.sku} className="border-b border-slate-50 transition hover:bg-slate-50/80 dark:border-slate-800/60 dark:hover:bg-slate-800/30">
                <td className="px-3 py-2.5 font-mono text-slate-400">#{(page - 1) * pageSize + idx + 1}</td>
                <td className="px-3 py-2.5">
                  <SkuImageThumbnail sku={row.sku} imageUrl={row.image_url} hue={row.hue} />
                </td>
                <td className="px-3 py-2.5 font-semibold text-slate-800 dark:text-white">
                  <SkuHoverCard sku={row.sku}>{row.sku}</SkuHoverCard>
                </td>
                <td className="px-3 py-2.5 text-slate-500">{row.category}</td>
                <td className="px-3 py-2.5 font-medium">{formatInr(row.sales, true)}</td>
                <td className="px-3 py-2.5">{row.stock.toLocaleString('en-IN')}</td>
                <td className="px-3 py-2.5">
                  <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[10px] font-bold text-emerald-700">{formatPctPlain(row.margin)}</span>
                </td>
                <td className="px-3 py-2.5">{formatPctPlain(row.sellThrough)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mt-3 flex items-center justify-between text-[11px] text-slate-500">
        <span>Page {page} of {totalPages} · {total.toLocaleString('en-IN')} SKUs</span>
        <div className="flex gap-1">
          <button type="button" disabled={page <= 1} onClick={() => setPage((p) => p - 1)} className="rounded-lg border px-2 py-1 disabled:opacity-40">Prev</button>
          <button type="button" disabled={page >= totalPages} onClick={() => setPage((p) => p + 1)} className="rounded-lg border px-2 py-1 disabled:opacity-40">Next</button>
        </div>
      </div>
    </ChartCard>
  );
}

export type StyleCard = {
  id: string;
  name: string;
  sku: string;
  sales: string;
  growth?: number;
  hue: number;
  image_url?: string;
};

export function TopStylesSection({ filterQuery }: { filterQuery: string }) {
  const [items, setItems] = useState<StyleCard[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams(filterQuery);
    params.set('view', 'top');
    params.set('page', '1');
    params.set('page_size', '6');
    setLoading(true);
    fetch(`${API_URL}/executive-overview/skus?${params}`, { signal: controller.signal })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (controller.signal.aborted || !data?.ok) return;
        const list = Array.isArray(data.rows) ? data.rows : [];
        setItems(
          list.map((s: SkuRow, i: number) => ({
            id: String(s.sku || i),
            name: `${s.category || ''} ${s.sku || ''}`.trim(),
            sku: String(s.sku || ''),
            sales: formatInr(Number(s.sales || 0), true),
            hue: 220 + i * 18,
            image_url: s.image_url || '',
          }))
        );
      })
      .catch((err: unknown) => {
        if ((err as { name?: string }).name === 'AbortError') return;
        setItems([]);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [filterQuery]);

  return (
    <ChartCard title="Top Styles" subtitle="Hero products driving revenue" loading={loading}>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(9.5rem,1fr))] gap-3">
        {items.map((item) => (
          <div
            key={item.id}
            className="group overflow-hidden rounded-2xl border border-slate-200/70 bg-white transition-[transform,box-shadow] hover:-translate-y-1 hover:shadow-lg dark:border-slate-800 dark:bg-slate-900/60"
          >
            <div
              className="relative aspect-[4/5] overflow-hidden"
              style={{ background: `linear-gradient(160deg, hsl(${item.hue}, 55%, 62%), hsl(${item.hue + 40}, 60%, 38%))` }}
            >
              <img
                key={resolveImageUrl(item.sku, item.image_url)}
                loading="lazy"
                src={resolveImageUrl(item.sku, item.image_url)}
                alt={item.name}
                className="absolute inset-0 h-full w-full object-cover transition-transform duration-300 group-hover:scale-105"
                onError={(e) => {
                  (e.currentTarget as HTMLElement).style.display = 'none';
                }}
              />
              <div className="absolute inset-0 bg-gradient-to-t from-black/70 via-black/20 to-transparent opacity-90" />
              <div className="absolute bottom-2 left-2 right-2">
                <p className="truncate text-[11px] font-bold text-white">{item.name}</p>
                <SkuHoverCard sku={item.sku} className="text-[10px] text-white/90 hover:text-white" underline={false}>
                  {item.sku}
                </SkuHoverCard>
              </div>
            </div>
            <div className="p-2.5">
              <p className="text-sm font-bold text-slate-900 dark:text-white">{item.sales}</p>
              {typeof item.growth === 'number' && Number.isFinite(item.growth) && (
                <p className={`text-[11px] font-semibold ${item.growth >= 0 ? 'text-emerald-600' : 'text-rose-600'}`}>
                  {item.growth >= 0 ? '+' : ''}{item.growth.toFixed(1)}% growth
                </p>
              )}
            </div>
          </div>
        ))}
      </div>
    </ChartCard>
  );
}

export type RegionMapData = {
  id: string;
  name: string;
  sales: number;
  target: number;
  growth?: number;
  stores: number;
  path: string;
};

export type CityMapData = {
  id: string;
  name: string;
  zone: string;
  sales: number;
  growth?: number;
  stores: number;
  lat: number;
  lng: number;
};

export function RegionalMapSection({
  regions,
  cities,
  loading,
  selectedZone,
  onZoneClick,
  onCityClick,
}: {
  regions: RegionMapData[];
  cities: CityMapData[];
  loading?: boolean;
  selectedZone?: string | null;
  onZoneClick?: (zone: string) => void;
  onCityClick?: (city: string) => void;
}) {
  const [mode, setMode] = useState<'zone' | 'city'>('zone');

  return (
    <ChartCard
      title="Regional Performance"
      subtitle="Real India map · darker = higher sales"
      loading={loading}
      className="h-full"
      action={
        <div className="flex rounded-lg border border-slate-200 bg-slate-50 p-0.5 dark:border-slate-700 dark:bg-slate-800">
          {(
            [
              { id: 'zone' as const, label: 'Zone' },
              { id: 'city' as const, label: 'City' },
            ] as const
          ).map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => setMode(t.id)}
              className={`rounded-md px-2.5 py-1 text-[10px] font-semibold transition ${
                mode === t.id
                  ? 'bg-blue-600 text-white shadow-sm'
                  : 'text-slate-500 hover:text-blue-700'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
      }
    >
      <IndiaRealMap
        key={`india-sales-map-${mode}`}
        regions={regions}
        cities={cities}
        selectedZone={selectedZone}
        onZoneClick={onZoneClick}
        onCityClick={onCityClick}
        mode={mode}
      />
      <p className="mt-2 text-center text-[11px] text-slate-400">
        {mode === 'zone'
          ? 'Darker blue = higher sales · hover for ₹ · click zone to filter'
          : 'City bubbles: bigger + redder = higher sales'}
      </p>
    </ChartCard>
  );
}

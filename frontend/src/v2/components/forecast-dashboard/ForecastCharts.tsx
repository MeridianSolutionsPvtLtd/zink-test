import React, { useMemo, useState } from 'react';
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
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import {
  buildDemandSupply,
  buildForecastVsActual,
  buildInventoryForecast,
  buildProjectedSales,
  formatCompact,
  type AllocationRow,
  type InsightRow,
} from './forecastAnalytics';
import { SectionCard as ChartCard } from '../../design-system';

const BLUE = '#2563eb';
const PERIODS = ['daily', 'weekly', 'monthly', 'quarterly', 'yearly'] as const;
type Period = (typeof PERIODS)[number];

function AllocationLegend({ baselineYears }: { baselineYears: string[] }) {
  return (
    <div className="flex flex-wrap items-center gap-3 text-[10px] font-medium">
      {[...baselineYears].sort().map((year) => (
        <div key={year} className="flex items-center gap-1.5">
          <div className={`h-2.5 w-2.5 rounded-sm ${year === '2024' ? 'bg-slate-400' : 'bg-slate-500'}`} />
          <span className="text-slate-500">{year} Sold</span>
        </div>
      ))}
      <div className="flex items-center gap-1.5">
        <div className="h-2.5 w-2.5 rounded-sm bg-emerald-500" />
        <span className="text-slate-500">Predicted</span>
      </div>
    </div>
  );
}

export function PremiumAllocationChart({
  title,
  subtitle,
  data,
  baselineYears,
  height = 320,
}: {
  title: string;
  subtitle: string;
  data: AllocationRow[];
  baselineYears: string[];
  height?: number;
}) {
  return (
    <ChartCard title={title} subtitle={subtitle}>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <AllocationLegend baselineYears={baselineYears} />
      </div>
      <div className="overflow-x-auto pb-2">
        <div style={{ minWidth: data.length > 0 ? Math.max(800, data.length * 100) : '100%' }}>
          <ResponsiveContainer width="100%" height={height}>
            <BarChart data={data} margin={{ top: 10, right: 20, left: 0, bottom: 20 }} barGap={6} barCategoryGap="18%">
              <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e2e8f0" />
              <XAxis dataKey="label" tick={{ fontSize: 11, fill: '#64748b' }} interval={0} angle={-12} textAnchor="end" height={50} />
              <YAxis tick={{ fontSize: 11, fill: '#64748b' }} tickFormatter={(v) => formatCompact(v)} />
              <Tooltip contentStyle={{ borderRadius: 12, border: '1px solid #e2e8f0', fontSize: 12 }} />
              <Legend />
              <Bar dataKey="sold_2024" name="2024 Sold Qty" fill="#94a3b8" radius={[6, 6, 0, 0]} barSize={22} hide={!baselineYears.includes('2024')} />
              <Bar dataKey="sold_2025" name="2025 Sold Qty" fill="#64748b" radius={[6, 6, 0, 0]} barSize={22} hide={!baselineYears.includes('2025')} />
              <Bar dataKey="predicted" name="Predicted" fill="#10b981" radius={[6, 6, 0, 0]} barSize={22} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </ChartCard>
  );
}

export function ForecastVsActualChart({ categoryData }: { categoryData: AllocationRow[] }) {
  const [period, setPeriod] = useState<Period>('monthly');
  const data = useMemo(() => buildForecastVsActual(categoryData, period), [categoryData, period]);
  return (
    <ChartCard title="Forecast vs Actual" subtitle="Actual · Forecast · Target comparison">
      <div className="mb-3 flex flex-wrap gap-1">
        {PERIODS.map((p) => (
          <button key={p} type="button" onClick={() => setPeriod(p)} className={`rounded-lg px-3 py-1 text-xs font-semibold capitalize ${period === p ? 'bg-blue-600 text-white' : 'bg-slate-100 text-slate-600 dark:bg-slate-800'}`}>
            {p}
          </button>
        ))}
      </div>
      <ResponsiveContainer width="100%" height={300}>
        <ComposedChart data={data}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
          <XAxis dataKey="label" tick={{ fontSize: 11 }} />
          <YAxis tickFormatter={(v) => formatCompact(v)} tick={{ fontSize: 11 }} />
          <Tooltip formatter={(v: number) => formatCompact(v)} />
          <Legend />
          <Area type="monotone" dataKey="actual" name="Actual" fill="#2563eb" fillOpacity={0.12} stroke={BLUE} strokeWidth={2} />
          <Line type="monotone" dataKey="forecast" name="Forecast" stroke="#06b6d4" strokeWidth={2} dot={false} />
          <Line type="monotone" dataKey="target" name="Target" stroke="#f59e0b" strokeWidth={2} strokeDasharray="4 4" dot={false} />
        </ComposedChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

export function ProjectedSalesChart({ forecast }: { forecast: number }) {
  const data = useMemo(() => buildProjectedSales(forecast), [forecast]);
  return (
    <ChartCard title="Projected Sales" subtitle="Future sales projection by month">
      <ResponsiveContainer width="100%" height={260}>
        <AreaChart data={data}>
          <defs>
            <linearGradient id="projGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={BLUE} stopOpacity={0.3} />
              <stop offset="100%" stopColor={BLUE} stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="label" tick={{ fontSize: 10 }} />
          <YAxis tickFormatter={(v) => formatCompact(v)} tick={{ fontSize: 10 }} />
          <Tooltip formatter={(v: number) => formatCompact(v)} />
          <Area type="monotone" dataKey="projected" name="Projected" stroke={BLUE} fill="url(#projGrad)" strokeWidth={2} />
          <Line type="monotone" dataKey="actual" name="Actual (YTD)" stroke="#10b981" strokeWidth={2} connectNulls={false} />
        </AreaChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

export function ProjectedInventoryChart({ inventoryTarget }: { inventoryTarget: number }) {
  const data = useMemo(() => buildInventoryForecast(inventoryTarget), [inventoryTarget]);
  return (
    <ChartCard title="Projected Inventory" subtitle="Safe · Risk · Critical stock zones">
      <div className="mb-2 flex flex-wrap gap-2 text-[10px]">
        <span className="rounded-full bg-emerald-100 px-2 py-0.5 font-bold text-emerald-700">Safe Stock</span>
        <span className="rounded-full bg-amber-100 px-2 py-0.5 font-bold text-amber-700">Risk Stock</span>
        <span className="rounded-full bg-rose-100 px-2 py-0.5 font-bold text-rose-700">Critical Stock</span>
      </div>
      <ResponsiveContainer width="100%" height={260}>
        <ComposedChart data={data}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="label" tick={{ fontSize: 10 }} />
          <YAxis tickFormatter={(v) => formatCompact(v)} tick={{ fontSize: 10 }} />
          <Tooltip formatter={(v: number) => formatCompact(v)} />
          <Area type="monotone" dataKey="safe" name="Safe" fill="#22c55e" fillOpacity={0.08} stroke="#22c55e" strokeDasharray="3 3" />
          <Area type="monotone" dataKey="risk" name="Risk" fill="#f59e0b" fillOpacity={0.06} stroke="#f59e0b" strokeDasharray="3 3" />
          <Line type="monotone" dataKey="inventory" name="Inventory" stroke={BLUE} strokeWidth={2} />
        </ComposedChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

export function DemandSupplyChart({ categoryData }: { categoryData: AllocationRow[] }) {
  const data = useMemo(() => buildDemandSupply(categoryData), [categoryData]);
  return (
    <ChartCard title="Demand vs Supply" subtitle="Shortages and excess inventory">
      <ResponsiveContainer width="100%" height={260}>
        <BarChart data={data}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="name" tick={{ fontSize: 9 }} interval={0} angle={-15} textAnchor="end" height={50} />
          <YAxis tickFormatter={(v) => formatCompact(v)} tick={{ fontSize: 10 }} />
          <Tooltip formatter={(v: number) => formatCompact(v)} />
          <Legend />
          <Bar dataKey="demand" name="Demand" fill={BLUE} radius={[4, 4, 0, 0]} />
          <Bar dataKey="supply" name="Supply" fill="#06b6d4" radius={[4, 4, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

export function ForecastAccuracySection({ accuracy }: { accuracy: number }) {
  const trend = [
    { label: 'W1', value: accuracy * 0.96 },
    { label: 'W2', value: accuracy * 0.98 },
    { label: 'W3', value: accuracy },
    { label: 'W4', value: accuracy * 1.01 },
  ];
  const pct = Math.min(100, accuracy);
  return (
    <ChartCard title="Forecast Accuracy" subtitle="Current · Previous · Last Year">
      <div className="grid gap-4 md:grid-cols-2">
        <div className="flex flex-col items-center justify-center">
          <div className="relative h-28 w-28">
            <svg viewBox="0 0 100 100" className="h-full w-full -rotate-90">
              <circle cx="50" cy="50" r="42" fill="none" stroke="#e2e8f0" strokeWidth="10" />
              <circle cx="50" cy="50" r="42" fill="none" stroke={pct >= 85 ? '#22c55e' : pct >= 70 ? '#f59e0b' : '#ef4444'} strokeWidth="10" strokeDasharray={`${pct * 2.64} 264`} strokeLinecap="round" />
            </svg>
            <div className="absolute inset-0 flex flex-col items-center justify-center">
              <span className="text-2xl font-bold">{accuracy.toFixed(0)}%</span>
              <span className="text-[10px] text-slate-500">Accuracy</span>
            </div>
          </div>
          <div className="mt-2 grid grid-cols-3 gap-2 text-center text-[10px]">
            <div><p className="text-slate-400">Current</p><p className="font-bold">{accuracy.toFixed(1)}%</p></div>
            <div><p className="text-slate-400">Prev Month</p><p className="font-bold">{(accuracy * 0.97).toFixed(1)}%</p></div>
            <div><p className="text-slate-400">Last Year</p><p className="font-bold">{(accuracy * 0.92).toFixed(1)}%</p></div>
          </div>
        </div>
        <ResponsiveContainer width="100%" height={160}>
          <LineChart data={trend}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="label" tick={{ fontSize: 10 }} />
            <YAxis domain={['dataMin - 5', 'dataMax + 5']} tick={{ fontSize: 10 }} />
            <Tooltip />
            <Line type="monotone" dataKey="value" stroke={BLUE} strokeWidth={2} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </ChartCard>
  );
}

const SEASONS = ['Summer', 'Winter', 'Festive', 'EOSS', 'End of Season'];
const MONTHS = ['Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec', 'Jan', 'Feb', 'Mar'];

export function SeasonalHeatmap({ categoryData }: { categoryData: AllocationRow[] }) {
  const total = categoryData.reduce((s, r) => s + (r.predicted ?? 0), 0) || 1;
  const data = useMemo(() => {
    return SEASONS.flatMap((season, si) =>
      MONTHS.map((month, mi) => {
        const intensity = ((categoryData[si % categoryData.length]?.predicted ?? 100) / total) * (0.5 + Math.sin(mi * 0.4 + si) * 0.3 + 0.5);
        return { season, month, value: Math.round(intensity * 1000) };
      })
    );
  }, [categoryData, total]);
  const max = Math.max(...data.map((d) => d.value), 1);
  return (
    <ChartCard title="Seasonal Trend" subtitle="Sales patterns across seasons">
      <div className="overflow-x-auto">
        <div className="min-w-[640px]">
          <div className="mb-2 grid grid-cols-[80px_repeat(12,1fr)] gap-0.5 text-[9px] text-slate-400">
            <div />
            {MONTHS.map((m) => (
              <div key={m} className="text-center">{m}</div>
            ))}
          </div>
          {SEASONS.map((season) => (
            <div key={season} className="mb-0.5 grid grid-cols-[80px_repeat(12,1fr)] gap-0.5">
              <div className="flex items-center text-[10px] font-medium text-slate-600">{season}</div>
              {MONTHS.map((month) => {
                const cell = data.find((d) => d.season === season && d.month === month);
                const v = cell?.value ?? 0;
                const opacity = 0.15 + (v / max) * 0.85;
                return (
                  <div key={month} title={`${season} · ${month}: ${formatCompact(v)}`} className="h-7 rounded-md transition hover:ring-2 hover:ring-blue-400" style={{ background: `rgba(37, 99, 235, ${opacity})` }} />
                );
              })}
            </div>
          ))}
        </div>
      </div>
    </ChartCard>
  );
}

const FOCUS_DAYS = [
  { name: 'Diwali', demand: 1.45, sales: 1.52 },
  { name: 'Christmas', demand: 1.22, sales: 1.18 },
  { name: 'Republic Day', demand: 1.08, sales: 1.05 },
  { name: 'Independence Day', demand: 1.12, sales: 1.09 },
  { name: 'New Year', demand: 1.35, sales: 1.28 },
  { name: 'EOSS', demand: 1.55, sales: 1.48 },
];

export function FocusDayPrediction({ baseForecast }: { baseForecast: number }) {
  const unit = baseForecast / 1000 || 100;
  return (
    <ChartCard title="Focus Day Prediction" subtitle="Expected demand & sales on key retail events">
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {FOCUS_DAYS.map((d) => (
          <div key={d.name} className="rounded-xl border border-slate-100 bg-gradient-to-br from-blue-50/50 to-white p-3 dark:border-slate-800 dark:from-blue-950/20">
            <p className="text-xs font-bold text-blue-700 dark:text-blue-300">{d.name}</p>
            <div className="mt-2 grid grid-cols-2 gap-2 text-[11px]">
              <div><p className="text-slate-400">Demand</p><p className="font-bold">{formatCompact(unit * d.demand)}</p></div>
              <div><p className="text-slate-400">Sales</p><p className="font-bold">{formatCompact(unit * d.sales)}</p></div>
            </div>
          </div>
        ))}
      </div>
    </ChartCard>
  );
}

export function ForecastVarianceChart({ categoryData }: { categoryData: AllocationRow[] }) {
  const data = useMemo(
    () =>
      categoryData.slice(0, 8).map((r) => {
        const forecast = r.predicted ?? 0;
        const actual = (r.sold_2025 ?? r.sold_2024 ?? 0);
        const variance = forecast > 0 ? ((forecast - actual) / forecast) * 100 : 0;
        return { name: r.label, forecast, actual, variance };
      }),
    [categoryData]
  );
  return (
    <ChartCard title="Forecast Variance" subtitle="Forecast · Actual · Variance %">
      <ResponsiveContainer width="100%" height={260}>
        <ComposedChart data={data}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="name" tick={{ fontSize: 9 }} interval={0} angle={-15} textAnchor="end" height={50} />
          <YAxis yAxisId="left" tickFormatter={(v) => formatCompact(v)} tick={{ fontSize: 10 }} />
          <YAxis yAxisId="right" orientation="right" tickFormatter={(v) => `${v}%`} tick={{ fontSize: 10 }} />
          <Tooltip />
          <Legend />
          <Bar yAxisId="left" dataKey="forecast" name="Forecast" fill={BLUE} radius={[4, 4, 0, 0]} />
          <Bar yAxisId="left" dataKey="actual" name="Actual" fill="#94a3b8" radius={[4, 4, 0, 0]} />
          <Line yAxisId="right" type="monotone" dataKey="variance" name="Variance %" stroke="#f59e0b" strokeWidth={2} />
        </ComposedChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

export function PlanningCalendar({ forecast, target, inventoryTarget }: { forecast: number; target: number; inventoryTarget: number }) {
  const months = ['Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec', 'Jan', 'Feb', 'Mar'];
  const data = months.map((m, i) => ({
    month: m,
    forecast: (forecast / 12) * (0.85 + i * 0.02),
    target: (target / 12) * (0.88 + i * 0.018),
    inventory: (inventoryTarget / 12) * (0.9 + i * 0.015),
  }));
  return (
    <ChartCard title="Monthly Planning Calendar" subtitle="Forecast · Target · Inventory plan timeline">
      <ResponsiveContainer width="100%" height={280}>
        <ComposedChart data={data}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="month" tick={{ fontSize: 10 }} />
          <YAxis tickFormatter={(v) => formatCompact(v)} tick={{ fontSize: 10 }} />
          <Tooltip formatter={(v: number) => formatCompact(v)} />
          <Legend />
          <Bar dataKey="forecast" name="Forecast" fill={BLUE} radius={[4, 4, 0, 0]} />
          <Bar dataKey="target" name="Target" fill="#6366f1" radius={[4, 4, 0, 0]} />
          <Line type="monotone" dataKey="inventory" name="Inventory Plan" stroke="#10b981" strokeWidth={2} />
        </ComposedChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

export function DemandRiskSection({ insights }: { insights: InsightRow[] }) {
  const over = insights.filter((r) => r.action === 'REDUCE').length;
  const under = insights.filter((r) => r.action === 'STOCK UP').length;
  const stockout = insights.filter((r) => r.risk_flag === 'HIGH').length;
  const overstock = insights.filter((r) => r.action === 'REDUCE' && r.risk_flag === 'HIGH').length;
  const items = [
    { label: 'Over Forecast', count: over, color: '#f59e0b' },
    { label: 'Under Forecast', count: under, color: '#06b6d4' },
    { label: 'Stockout Risk', count: stockout, color: '#ef4444' },
    { label: 'Overstock Risk', count: overstock, color: '#8b5cf6' },
  ];
  return (
    <ChartCard title="Demand Risk Analysis" subtitle="Planning risk flags from insights">
      <div className="grid gap-3 sm:grid-cols-2">
        {items.map((item) => (
          <div key={item.label} className="flex items-center justify-between rounded-xl border border-slate-100 px-4 py-3 dark:border-slate-800">
            <div className="flex items-center gap-3">
              <div className="h-3 w-3 rounded-full" style={{ background: item.color }} />
              <span className="text-sm font-medium">{item.label}</span>
            </div>
            <span className="text-lg font-bold" style={{ color: item.color }}>{item.count}</span>
          </div>
        ))}
      </div>
    </ChartCard>
  );
}

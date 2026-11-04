import React, { useMemo, useState } from 'react';
import {
  Area,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ComposedChart,
  Legend,
  Line,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  Treemap,
  XAxis,
  YAxis,
} from 'recharts';
import {
  aggregateBy,
  buildEventComparison,
  buildEventTrend,
  buildHourlyHeatmap,
  buildStoreEventHeatmap,
  EVENTS,
  formatCompactInr,
  formatInr,
  type DrillLevel,
  type FocusRecord,
} from './focusDayData';
import { SectionCard as ChartCard } from '../../design-system';

const BLUE = '#2563eb';
const PERIODS = ['daily', 'weekly', 'hourly'] as const;
type Period = (typeof PERIODS)[number];

function TreemapContent(props: { x?: number; y?: number; width?: number; height?: number; name?: string; sales?: number }) {
  const { x = 0, y = 0, width = 0, height = 0, name = '', sales = 0 } = props;
  if (width < 30 || height < 20) return null;
  return (
    <g>
      <rect x={x} y={y} width={width} height={height} fill="#2563eb" fillOpacity={0.15 + (sales % 7) * 0.1} stroke="#fff" strokeWidth={2} rx={4} />
      {width > 50 && height > 28 && (
        <>
          <text x={x + 6} y={y + 14} fontSize={10} fontWeight={600} fill="#1e40af">{name}</text>
          <text x={x + 6} y={y + 28} fontSize={9} fill="#64748b">{formatCompactInr(sales)}</text>
        </>
      )}
    </g>
  );
}

export function DrillBreadcrumb({ path, onNavigate }: { path: string[]; onNavigate: (idx: number) => void }) {
  const labels = ['Zone', 'State', 'City', 'Store', 'Department', 'Category', 'Story', 'SKU'];
  return (
    <div className="flex flex-wrap items-center gap-1 rounded-xl border border-blue-100 bg-blue-50/50 px-3 py-2 text-xs dark:border-blue-900/40 dark:bg-blue-950/30">
      <span className="font-semibold text-blue-700 dark:text-blue-300">Drill:</span>
      {labels.map((label, i) => (
        <React.Fragment key={label}>
          {i > 0 && <span className="text-slate-400">→</span>}
          <button type="button" onClick={() => onNavigate(i)} className={`rounded-md px-2 py-0.5 font-medium transition ${i === path.length - 1 ? 'bg-blue-600 text-white' : i < path.length ? 'text-blue-600 hover:bg-blue-100' : 'text-slate-400'}`}>
            {path[i] ?? label}
          </button>
        </React.Fragment>
      ))}
    </div>
  );
}

export function DrillDownChart({ rows, level, onDrill }: { rows: FocusRecord[]; level: DrillLevel; onDrill: (v: string) => void }) {
  const data = useMemo(() => aggregateBy(rows, level).slice(0, 10), [rows, level]);
  return (
    <ChartCard title={`Drill Down — ${level}`} subtitle="Click a bar to drill deeper">
      <ResponsiveContainer width="100%" height={220}>
        <BarChart data={data}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="name" tick={{ fontSize: 9 }} interval={0} angle={-18} textAnchor="end" height={55} />
          <YAxis tickFormatter={(v) => formatCompactInr(v)} tick={{ fontSize: 10 }} />
          <Tooltip formatter={(v: number) => formatInr(v)} />
          <Bar dataKey="sales" fill={BLUE} radius={[6, 6, 0, 0]} cursor="pointer" onClick={(d) => { const p = d as { name?: string }; if (p?.name) onDrill(p.name); }} />
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

export function EventSalesTrend({ totalSales }: { totalSales: number }) {
  const [period, setPeriod] = useState<Period>('daily');
  const data = useMemo(() => buildEventTrend(totalSales, period), [totalSales, period]);
  return (
    <ChartCard title="Event Sales Trend" subtitle="Current · Previous · Target">
      <div className="mb-3 flex flex-wrap gap-1">
        {PERIODS.map((p) => (
          <button key={p} type="button" onClick={() => setPeriod(p)} className={`rounded-lg px-3 py-1 text-xs font-semibold capitalize ${period === p ? 'bg-blue-600 text-white' : 'bg-slate-100 text-slate-600 dark:bg-slate-800'}`}>{p}</button>
        ))}
      </div>
      <ResponsiveContainer width="100%" height={300}>
        <ComposedChart data={data}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="label" tick={{ fontSize: 10 }} />
          <YAxis tickFormatter={(v) => formatCompactInr(v)} tick={{ fontSize: 10 }} />
          <Tooltip formatter={(v: number) => formatInr(v)} />
          <Legend />
          <Area type="monotone" dataKey="current" name="Current Event" fill={BLUE} fillOpacity={0.12} stroke={BLUE} strokeWidth={2} />
          <Line type="monotone" dataKey="previous" name="Previous Event" stroke="#94a3b8" strokeWidth={2} dot={false} />
          <Line type="monotone" dataKey="target" name="Target" stroke="#f59e0b" strokeWidth={2} strokeDasharray="4 4" dot={false} />
        </ComposedChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

export function EventComparisonChart() {
  const data = useMemo(() => buildEventComparison(), []);
  return (
    <ChartCard title="Event Comparison" subtitle="Compare key retail festival days">
      <ResponsiveContainer width="100%" height={280}>
        <BarChart data={data}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="name" tick={{ fontSize: 10 }} />
          <YAxis tickFormatter={(v) => formatCompactInr(v)} tick={{ fontSize: 10 }} />
          <Tooltip formatter={(v: number) => formatInr(v)} />
          <Legend />
          <Bar dataKey="current" name="Current Year" fill={BLUE} radius={[4, 4, 0, 0]} />
          <Bar dataKey="previous" name="Previous Year" fill="#94a3b8" radius={[4, 4, 0, 0]} />
          <Bar dataKey="target" name="Target" fill="#10b981" radius={[4, 4, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

export function SalesByZoneChart({ rows }: { rows: FocusRecord[] }) {
  const data = useMemo(() => aggregateBy(rows, 'zone'), [rows]);
  return (
    <ChartCard title="Sales by Zone" subtitle="Interactive horizontal ranking">
      <ResponsiveContainer width="100%" height={260}>
        <BarChart data={data} layout="vertical" margin={{ left: 10 }}>
          <CartesianGrid strokeDasharray="3 3" horizontal={false} />
          <XAxis type="number" tickFormatter={(v) => formatCompactInr(v)} tick={{ fontSize: 10 }} />
          <YAxis type="category" dataKey="name" width={70} tick={{ fontSize: 10 }} />
          <Tooltip formatter={(v: number) => formatInr(v)} />
          <Bar dataKey="sales" fill={BLUE} radius={[0, 6, 6, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

export function StorePerformanceTables({ rows, onStoreClick }: { rows: FocusRecord[]; onStoreClick?: (s: string) => void }) {
  const byStore = useMemo(() => aggregateBy(rows, 'store'), [rows]);
  const top = byStore.slice(0, 6);
  const bottom = [...byStore].reverse().slice(0, 6);
  const renderTable = (title: string, data: typeof top, variant: 'top' | 'bottom') => (
    <ChartCard title={title} subtitle={variant === 'top' ? 'Highest event sales' : 'Underperforming stores'}>
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b text-left text-[10px] uppercase text-slate-400">
            <th className="pb-2">#</th><th className="pb-2">Store</th><th className="pb-2 text-right">Sales</th><th className="pb-2 text-right">Achievement</th>
          </tr>
        </thead>
        <tbody>
          {data.map((s, i) => {
            const ach = s.target > 0 ? (s.sales / s.target) * 100 : 0;
            return (
              <tr key={s.name} className={`border-b border-slate-50 ${variant === 'bottom' ? 'bg-rose-50/30' : ''}`}>
                <td className="py-2 font-bold text-slate-400">{i + 1}</td>
                <td className="py-2"><button type="button" onClick={() => onStoreClick?.(s.name)} className="font-semibold text-blue-600 hover:underline">{s.name}</button></td>
                <td className="py-2 text-right">{formatCompactInr(s.sales)}</td>
                <td className={`py-2 text-right font-bold ${ach >= 100 ? 'text-emerald-600' : 'text-rose-600'}`}>{ach.toFixed(0)}%</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </ChartCard>
  );
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      {renderTable('Top Performing Stores', top, 'top')}
      {renderTable('Bottom Performing Stores', bottom, 'bottom')}
    </div>
  );
}

function ProductThumb({ hue }: { hue: number }) {
  return <div className="flex h-9 w-9 items-center justify-center rounded-lg text-[10px] font-bold text-white" style={{ background: `linear-gradient(135deg, hsl(${hue},65%,45%), hsl(${hue + 30},55%,35%))` }}>IMG</div>;
}

export function ProductRankingTable({ title, rows, variant, onSkuClick }: { title: string; rows: FocusRecord[]; variant: 'top' | 'bottom'; onSkuClick?: (s: string) => void }) {
  const sorted = useMemo(() => {
    const map = new Map<string, FocusRecord & { totalSales: number }>();
    rows.forEach((r) => {
      const cur = map.get(r.sku);
      if (!cur) map.set(r.sku, { ...r, totalSales: r.sales });
      else cur.totalSales += r.sales;
    });
    return [...map.values()].sort((a, b) => variant === 'top' ? b.totalSales - a.totalSales : a.totalSales - b.totalSales).slice(0, 8);
  }, [rows, variant]);

  return (
    <ChartCard title={title} subtitle={variant === 'top' ? 'Winning products' : 'Poor performers'}>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b text-left text-[10px] uppercase text-slate-400">
              <th className="pb-2 pr-2">SKU</th><th className="pb-2 pr-2">Image</th><th className="pb-2 pr-2">Category</th>
              <th className="pb-2 pr-2 text-right">Sales</th><th className="pb-2 pr-2 text-right">Qty</th><th className="pb-2 pr-2 text-right">Margin</th><th className="pb-2 text-right">ST</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((p) => (
              <tr key={p.sku} className={`border-b ${variant === 'bottom' ? 'bg-rose-50/30' : ''}`}>
                <td className="py-2 pr-2"><button type="button" onClick={() => onSkuClick?.(p.sku)} className="font-semibold text-blue-600 hover:underline">{p.sku}</button></td>
                <td className="py-2 pr-2"><ProductThumb hue={p.hue} /></td>
                <td className="py-2 pr-2">{p.category}</td>
                <td className="py-2 pr-2 text-right">{formatCompactInr(p.totalSales)}</td>
                <td className="py-2 pr-2 text-right">{p.quantity}</td>
                <td className="py-2 pr-2 text-right">{formatCompactInr(p.margin)}</td>
                <td className="py-2 text-right">{p.sellThrough}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </ChartCard>
  );
}

export function DepartmentTreemap({ rows }: { rows: FocusRecord[] }) {
  const data = useMemo(() => aggregateBy(rows, 'department').map((d) => ({ name: d.name, size: d.sales, sales: d.sales })), [rows]);
  return (
    <ChartCard title="Department Contribution" subtitle="Sales share by department">
      <ResponsiveContainer width="100%" height={260}>
        <Treemap data={data} dataKey="size" aspectRatio={4 / 3} stroke="#fff" content={<TreemapContent />} />
      </ResponsiveContainer>
    </ChartCard>
  );
}

export function CategoryDistributionPieChart({ rows }: { rows: FocusRecord[] }) {
  const total = useMemo(() => rows.reduce((s, r) => s + r.sales, 0), [rows]);
  const data = useMemo(
    () =>
      aggregateBy(rows, 'category').slice(0, 6).map((c) => ({
        name: c.name,
        sales: c.sales,
        pct: total > 0 ? ((c.sales / total) * 100).toFixed(1) : '0',
      })),
    [rows, total]
  );
  const colors = ['#2563eb', '#06b6d4', '#8b5cf6', '#10b981', '#f59e0b', '#ec4899'];
  return (
    <ChartCard title="Category Distribution" subtitle="Sales contribution by product category">
      <ResponsiveContainer width="100%" height={300}>
        <PieChart>
          <Pie
            data={data}
            dataKey="sales"
            nameKey="name"
            cx="50%"
            cy="45%"
            outerRadius={85}
            innerRadius={0}
          >
            {data.map((_, i) => (
              <Cell key={i} fill={colors[i % colors.length]} />
            ))}
          </Pie>
          <Tooltip
            formatter={(v: number) => {
              const pct = total > 0 ? ((v / total) * 100).toFixed(1) : '0';
              return [`${formatInr(v)} (${pct}%)`, 'Sales'];
            }}
          />
          <Legend
            verticalAlign="bottom"
            wrapperStyle={{ fontSize: 11, paddingTop: 12 }}
          />
        </PieChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

export function TopPerformingStoresChart({
  rows,
  onStoreClick,
}: {
  rows: FocusRecord[];
  onStoreClick?: (store: string) => void;
}) {
  const data = useMemo(() => aggregateBy(rows, 'store').slice(0, 8), [rows]);
  return (
    <ChartCard title="Top Performing Stores" subtitle="Top stores by festival sales revenue">
      <ResponsiveContainer width="100%" height={300}>
        <BarChart data={data} layout="vertical" margin={{ top: 10, right: 24, left: 10, bottom: 5 }}>
          <CartesianGrid strokeDasharray="3 3" horizontal={false} />
          <XAxis type="number" tickFormatter={(v) => formatCompactInr(v)} tick={{ fontSize: 10 }} />
          <YAxis type="category" dataKey="name" tick={{ fontSize: 10 }} width={80} />
          <Tooltip formatter={(v: number) => [formatInr(v), 'Sales Revenue']} />
          <Bar
            dataKey="sales"
            fill="#2563eb"
            radius={[0, 4, 4, 0]}
            cursor="pointer"
            onClick={(d) => {
              const p = d as { name?: string };
              if (p?.name && onStoreClick) onStoreClick(p.name);
            }}
          >
            {data.map((_, i) => (
              <Cell
                key={i}
                fill={i === 0 ? '#1d4ed8' : i === 1 ? '#2563eb' : i === 2 ? '#3b82f6' : '#60a5fa'}
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

export function TopBestSkuChart({
  rows,
  onSkuClick,
}: {
  rows: FocusRecord[];
  onSkuClick?: (sku: string) => void;
}) {
  const data = useMemo(() => aggregateBy(rows, 'sku').slice(0, 8), [rows]);
  return (
    <ChartCard title="Top Best SKUs" subtitle="Top 8 best-performing SKUs by revenue">
      <ResponsiveContainer width="100%" height={300}>
        <BarChart data={data} layout="vertical" margin={{ top: 10, right: 24, left: 10, bottom: 5 }}>
          <CartesianGrid strokeDasharray="3 3" horizontal={false} />
          <XAxis type="number" tickFormatter={(v) => formatCompactInr(v)} tick={{ fontSize: 10 }} />
          <YAxis type="category" dataKey="name" tick={{ fontSize: 10 }} width={75} />
          <Tooltip
            formatter={(v: number) => [formatInr(v), 'Sales Revenue']}
            labelFormatter={(label) => `SKU: ${label}`}
          />
          <Bar
            dataKey="sales"
            fill="#059669"
            radius={[0, 4, 4, 0]}
            cursor="pointer"
            onClick={(d) => {
              const p = d as { name?: string };
              if (p?.name && onSkuClick) onSkuClick(p.name);
            }}
          >
            {data.map((_, i) => (
              <Cell
                key={i}
                fill={i === 0 ? '#047857' : i === 1 ? '#059669' : i === 2 ? '#10b981' : '#34d399'}
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

export function CategoryDonut({ rows }: { rows: FocusRecord[] }) {
  const data = useMemo(() => aggregateBy(rows, 'category').slice(0, 6), [rows]);
  const colors = ['#2563eb', '#06b6d4', '#8b5cf6', '#22c55e', '#f59e0b', '#ef4444'];
  return (
    <ChartCard title="Category Contribution" subtitle="Interactive category mix">
      <ResponsiveContainer width="100%" height={260}>
        <PieChart>
          <Pie data={data} dataKey="sales" nameKey="name" cx="50%" cy="50%" innerRadius={55} outerRadius={90}>
            {data.map((_, i) => <Cell key={i} fill={colors[i % colors.length]} />)}
          </Pie>
          <Tooltip formatter={(v: number) => formatInr(v)} />
          <Legend />
        </PieChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

export function DiscountImpactSection({ rows }: { rows: FocusRecord[] }) {
  const total = rows.reduce((s, r) => s + r.sales, 0);
  const discounted = rows.reduce((s, r) => s + r.discount, 0);
  const fullPrice = total - discounted;
  const markdownPct = total > 0 ? (discounted / total) * 100 : 0;
  const data = [{ name: 'Full Price', value: fullPrice }, { name: 'Discounted', value: discounted }];
  return (
    <ChartCard title="Discount Impact" subtitle="Full price vs discounted sales">
      <div className="mb-3 grid grid-cols-3 gap-2 text-center">
        <div className="rounded-xl bg-emerald-50 p-2"><p className="text-[10px] text-slate-500">Full Price</p><p className="text-sm font-bold text-emerald-700">{formatCompactInr(fullPrice)}</p></div>
        <div className="rounded-xl bg-amber-50 p-2"><p className="text-[10px] text-slate-500">Discount Sales</p><p className="text-sm font-bold text-amber-700">{formatCompactInr(discounted)}</p></div>
        <div className="rounded-xl bg-blue-50 p-2"><p className="text-[10px] text-slate-500">Markdown %</p><p className="text-sm font-bold text-blue-700">{markdownPct.toFixed(1)}%</p></div>
      </div>
      <ResponsiveContainer width="100%" height={200}>
        <BarChart data={data}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="name" /><YAxis tickFormatter={(v) => formatCompactInr(v)} />
          <Tooltip formatter={(v: number) => formatInr(v)} />
          <Bar dataKey="value" fill={BLUE} radius={[6, 6, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

export function HourlySalesHeatmap({ rows }: { rows: FocusRecord[] }) {
  const stores = useMemo(() => [...new Set(rows.map((r) => r.store))], [rows]);
  const cells = useMemo(() => buildHourlyHeatmap(stores), [stores]);
  const hours = [...new Set(cells.map((c) => c.hour))];
  const storeList = stores.slice(0, 6);
  const max = Math.max(...cells.map((c) => c.value), 1);
  return (
    <ChartCard title="Hourly Sales Heatmap" subtitle="Peak shopping hours by store">
      <div className="overflow-x-auto">
        <div className="min-w-[600px]">
          <div className="mb-1 grid grid-cols-[60px_repeat(6,1fr)] gap-0.5 text-[9px] text-slate-400">
            <div /><div className="col-span-6 text-center font-medium">Stores</div>
          </div>
          <div className="mb-1 grid grid-cols-[60px_repeat(6,1fr)] gap-0.5 text-[9px] text-slate-400">
            <div /><div className="col-span-6 grid grid-cols-6 gap-0.5">{storeList.map((s) => <div key={s} className="truncate text-center">{s.split(' ')[0]}</div>)}</div>
          </div>
          {hours.map((hour) => (
            <div key={hour} className="mb-0.5 grid grid-cols-[60px_repeat(6,1fr)] gap-0.5">
              <div className="flex items-center text-[10px] font-medium text-slate-600">{hour}</div>
              {storeList.map((store) => {
                const cell = cells.find((c) => c.hour === hour && c.store === store);
                const v = cell?.value ?? 0;
                const opacity = 0.15 + (v / max) * 0.85;
                return <div key={store} title={`${hour} · ${store}: ${formatCompactInr(v)}`} className="h-6 rounded-md" style={{ background: `rgba(37, 99, 235, ${opacity})` }} />;
              })}
            </div>
          ))}
        </div>
      </div>
    </ChartCard>
  );
}

export function StoreEventHeatmap({ rows }: { rows: FocusRecord[] }) {
  const cells = useMemo(() => buildStoreEventHeatmap(rows), [rows]);
  const stores = [...new Set(cells.map((c) => c.store))].slice(0, 8);
  const events = [...new Set(cells.map((c) => c.event))].slice(0, 6);
  const max = Math.max(...cells.map((c) => c.sales), 1);
  return (
    <ChartCard title="Store Performance Heatmap" subtitle="Store vs event sales intensity">
      <div className="overflow-x-auto">
        <div className="min-w-[640px]">
          <div className="mb-1 grid grid-cols-[100px_repeat(6,1fr)] gap-0.5 text-[9px] text-slate-400">
            <div />{events.map((e) => <div key={e} className="truncate text-center">{e}</div>)}
          </div>
          {stores.map((store) => (
            <div key={store} className="mb-0.5 grid grid-cols-[100px_repeat(6,1fr)] gap-0.5">
              <div className="truncate text-[10px] font-medium text-slate-600">{store}</div>
              {events.map((event) => {
                const cell = cells.find((c) => c.store === store && c.event === event);
                const v = cell?.sales ?? 0;
                const opacity = 0.12 + (v / max) * 0.88;
                return <div key={event} title={`${store} · ${event}: ${formatCompactInr(v)}`} className="h-7 rounded-md" style={{ background: `rgba(37, 99, 235, ${opacity})` }} />;
              })}
            </div>
          ))}
        </div>
      </div>
    </ChartCard>
  );
}

export function TargetVsAchievementChart({ rows }: { rows: FocusRecord[] }) {
  const data = useMemo(() => aggregateBy(rows, 'zone').map((d) => ({
    name: d.name,
    target: d.target,
    achievement: d.sales,
    pct: d.target > 0 ? (d.sales / d.target) * 100 : 0,
  })), [rows]);
  return (
    <ChartCard title="Target vs Achievement" subtitle="Zone-level performance">
      <ResponsiveContainer width="100%" height={260}>
        <BarChart data={data}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="name" tick={{ fontSize: 10 }} />
          <YAxis tickFormatter={(v) => formatCompactInr(v)} tick={{ fontSize: 10 }} />
          <Tooltip formatter={(v: number) => formatInr(v)} />
          <Legend />
          <Bar dataKey="target" name="Target" fill="#6366f1" radius={[4, 4, 0, 0]} />
          <Bar dataKey="achievement" name="Achievement" fill={BLUE} radius={[4, 4, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

export function FocusDayCalendar() {
  const statusStyle = {
    upcoming: 'border-blue-200 bg-blue-50 dark:border-blue-900 dark:bg-blue-950/30',
    running: 'border-emerald-200 bg-emerald-50 dark:border-emerald-900 dark:bg-emerald-950/30',
    completed: 'border-slate-200 bg-slate-50 dark:border-slate-800 dark:bg-slate-900/40',
  };
  const statusBadge = {
    upcoming: 'bg-blue-600 text-white',
    running: 'bg-emerald-600 text-white',
    completed: 'bg-slate-500 text-white',
  };
  return (
    <ChartCard title="Festival Day Calendar" subtitle="Upcoming · Running · Completed campaigns">
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {EVENTS.map((e) => (
          <div key={e.id} className={`rounded-xl border p-3 transition hover:shadow-md ${statusStyle[e.status]}`}>
            <div className="flex items-center justify-between">
              <p className="text-sm font-bold text-slate-900 dark:text-white">{e.name}</p>
              <span className={`rounded-full px-2 py-0.5 text-[9px] font-bold uppercase ${statusBadge[e.status]}`}>{e.status}</span>
            </div>
            <p className="mt-1 text-[11px] text-slate-500">{e.startDate} → {e.endDate}</p>
            <div className="mt-2 grid grid-cols-2 gap-2 text-[10px]">
              <div><span className="text-slate-400">LY Sales</span><p className="font-bold">{formatCompactInr(e.lySales)}</p></div>
              <div><span className="text-slate-400">Target</span><p className="font-bold">{formatCompactInr(e.target)}</p></div>
            </div>
          </div>
        ))}
      </div>
    </ChartCard>
  );
}

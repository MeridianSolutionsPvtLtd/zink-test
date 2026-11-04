import React, { useState } from 'react';
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, ComposedChart, Legend, Line,
  Pie, PieChart, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis,
} from 'recharts';
import { AlertTriangle, Bot, Package, Sparkles, TrendingUp, Warehouse } from 'lucide-react';
import {
  buildAgeingData, buildHealthScores, buildWeekCoverData, deriveCategory, deriveZone,
  formatInr, formatNumber,
} from './inventoryAnalytics';
import type { InventoryCategory, InventoryKpiData, InventorySummary, StockRow } from './types';

function Shell({ title, subtitle, children, height = 280, action }: { title: string; subtitle?: string; children: React.ReactNode; height?: number; action?: React.ReactNode }) {
  return (
    <div className="rounded-2xl border border-slate-200/80 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900/60">
      <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4 dark:border-slate-800">
        <div><h3 className="text-sm font-semibold text-slate-900 dark:text-white">{title}</h3>{subtitle && <p className="text-xs text-slate-500">{subtitle}</p>}</div>
        {action}
      </div>
      <div className="p-4" style={{ height }}>{children}</div>
    </div>
  );
}

export function WarehouseOverviewSection({ kpis, loading }: { kpis: InventoryKpiData; loading: boolean }) {
  if (loading) return null;
  const items = [
    { label: 'Warehouse Stock', value: formatNumber(kpis.warehouseStock) },
    { label: 'Inventory Value', value: formatInr(kpis.invValue) },
    { label: 'Warehouse Capacity', value: formatNumber(Math.round(kpis.warehouseStock * 1.35)) },
    { label: 'Utilization %', value: '74.2%' },
    { label: 'Dispatch (MTD)', value: formatNumber(Math.round(kpis.warehouseStock * 0.18)) },
    { label: 'Receipts (MTD)', value: formatNumber(Math.round(kpis.warehouseStock * 0.22)) },
  ];
  return (
    <Shell title="Warehouse Overview" subtitle="Central distribution summary">
      <div className="grid h-full grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
        {items.map((item) => (
          <div key={item.label} className="flex flex-col justify-center rounded-xl border border-blue-100 bg-blue-50/50 p-4 dark:border-blue-500/20 dark:bg-blue-500/10">
            <Warehouse className="mb-2 h-5 w-5 text-blue-600" />
            <p className="text-[10px] font-semibold uppercase text-slate-500">{item.label}</p>
            <p className="mt-1 text-xl font-bold text-slate-900 dark:text-white">{item.value}</p>
          </div>
        ))}
      </div>
    </Shell>
  );
}

export function WarehouseVsStoreChart({ kpis, rows, onDrill }: { kpis: InventoryKpiData; rows: StockRow[]; onDrill: (zone: string) => void }) {
  const byZone = ['North', 'South', 'East', 'West', 'Central'].map((z, i) => ({
    zone: z,
    warehouse: Math.round(kpis.warehouseStock * [0.28, 0.24, 0.18, 0.17, 0.13][i]),
    store: Math.round(kpis.storeStock * [0.28, 0.24, 0.18, 0.17, 0.13][i]),
  }));
  return (
    <Shell title="Warehouse vs Store Stock" subtitle="Click zone to drill down">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={byZone} barGap={4}>
          <CartesianGrid strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="zone" tick={{ fontSize: 11 }} />
          <YAxis tickFormatter={(v) => formatNumber(v)} tick={{ fontSize: 10 }} />
          <Tooltip formatter={(v: number) => formatNumber(v)} />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          <Bar dataKey="warehouse" name="Warehouse" fill="#2563eb" radius={[4, 4, 0, 0]} onClick={(d) => onDrill((d as { zone: string }).zone)} className="cursor-pointer" />
          <Bar dataKey="store" name="Store" fill="#60a5fa" radius={[4, 4, 0, 0]} onClick={(d) => onDrill((d as { zone: string }).zone)} className="cursor-pointer" />
        </BarChart>
      </ResponsiveContainer>
    </Shell>
  );
}

export function InventoryTrendSection({ totalSoh }: { totalSoh: number }) {
  const [period, setPeriod] = useState<'daily' | 'weekly' | 'monthly' | 'quarterly' | 'yearly'>('monthly');
  const tabs = ['daily', 'weekly', 'monthly', 'quarterly', 'yearly'] as const;
  const data = React.useMemo(() => {
    const base = totalSoh || 100000;
    const n = period === 'daily' ? 14 : period === 'weekly' ? 8 : period === 'monthly' ? 12 : period === 'quarterly' ? 4 : 5;
    return Array.from({ length: n }, (_, i) => ({
      label: period === 'daily' ? `D${i + 1}` : period === 'weekly' ? `W${i + 1}` : period === 'monthly' ? `M${i + 1}` : period === 'quarterly' ? `Q${i + 1}` : `FY${20 + i}`,
      opening: base * (0.85 + i * 0.02),
      closing: base * (0.87 + i * 0.025),
      inward: base * 0.04 * (1 + i * 0.05),
      dispatch: base * 0.035 * (1 + i * 0.04),
    }));
  }, [period, totalSoh]);

  return (
    <Shell title="Inventory Trend" action={
      <div className="flex rounded-lg border bg-slate-50 p-0.5 dark:bg-slate-800">
        {tabs.map((t) => <button key={t} type="button" onClick={() => setPeriod(t)} className={`rounded px-2 py-0.5 text-[10px] font-semibold capitalize ${period === t ? 'bg-white text-blue-600 shadow-sm' : 'text-slate-500'}`}>{t}</button>)}
      </div>
    } height={340}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data}>
          <CartesianGrid strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="label" tick={{ fontSize: 10 }} />
          <YAxis tickFormatter={(v) => formatNumber(v)} tick={{ fontSize: 10 }} width={72} />
          <Tooltip formatter={(v: number) => formatNumber(v)} />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          <Area type="monotone" dataKey="opening" name="Opening" stroke="#94a3b8" fill="transparent" strokeDasharray="4 4" />
          <Area type="monotone" dataKey="closing" name="Closing" stroke="#2563eb" fill="#2563eb22" />
          <Area type="monotone" dataKey="inward" name="Inward" stroke="#10b981" fill="transparent" />
          <Area type="monotone" dataKey="dispatch" name="Dispatch" stroke="#f59e0b" fill="transparent" />
        </AreaChart>
      </ResponsiveContainer>
    </Shell>
  );
}

export function AgeingSection({ categories }: { categories: InventoryCategory[] }) {
  const data = buildAgeingData(categories);
  return (
    <Shell title="Inventory Ageing" subtitle="Age bucket distribution">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data}>
          <CartesianGrid strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="name" tick={{ fontSize: 10 }} />
          <YAxis tickFormatter={(v) => formatNumber(v)} tick={{ fontSize: 10 }} />
          <Tooltip formatter={(v: number) => formatNumber(v)} />
          <Bar dataKey="value" radius={[6, 6, 0, 0]}>{data.map((d) => <Cell key={d.name} fill={d.fill} />)}</Bar>
        </BarChart>
      </ResponsiveContainer>
    </Shell>
  );
}

export function WeekCoverSection({ rows }: { rows: StockRow[] }) {
  const data = buildWeekCoverData(rows);
  const colors = { healthy: '#2563eb', medium: '#f59e0b', critical: '#ef4444' };
  return (
    <Shell title="Week Cover Analysis">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} layout="vertical">
          <XAxis type="number" hide />
          <YAxis type="category" dataKey="name" width={100} tick={{ fontSize: 10 }} />
          <Tooltip />
          <Bar dataKey="value" radius={[0, 6, 6, 0]}>{data.map((d) => <Cell key={d.name} fill={colors[d.status as keyof typeof colors]} />)}</Bar>
        </BarChart>
      </ResponsiveContainer>
    </Shell>
  );
}

export function OosSection({ rows, categories }: { rows: StockRow[]; categories: InventoryCategory[] }) {
  const topStores = [...new Map(rows.map((r) => [r.site_name, r])).values()].slice(0, 5);
  const topSkus = categories.filter((c) => c.sellThrough < 25).slice(0, 5);
  return (
    <Shell title="Out of Stock Analysis" height={320}>
      <div className="grid h-full gap-4 lg:grid-cols-2">
        <div>
          <p className="mb-2 text-xs font-semibold text-slate-500">Top OOS Stores</p>
          {topStores.map((s) => <div key={s.site_id} className="mb-1 flex justify-between rounded-lg bg-rose-50 px-3 py-2 text-xs dark:bg-rose-500/10"><span>{s.site_name}</span><span className="font-semibold text-rose-600">Critical</span></div>)}
        </div>
        <div>
          <p className="mb-2 text-xs font-semibold text-slate-500">Top OOS SKU</p>
          {topSkus.map((s) => <div key={s.sku} className="mb-1 flex justify-between rounded-lg bg-amber-50 px-3 py-2 text-xs dark:bg-amber-500/10"><span>{s.sku}</span><span>{s.sellThrough}% ST</span></div>)}
          <p className="mt-3 text-xs text-slate-500">Lost sales estimate: <strong className="text-blue-600">{formatInr(rows.length * 12500)}</strong></p>
        </div>
      </div>
    </Shell>
  );
}

export function MoversTables({ categories }: { categories: InventoryCategory[] }) {
  const slow = categories.filter((c) => c.health === 'Slow Moving' || c.sellThrough < 30).slice(0, 6);
  const fast = categories.filter((c) => c.health === 'Fast Moving' || c.sellThrough > 50).slice(0, 6);
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Shell title="Slow Movers" height={260}>
        <table className="w-full text-xs"><thead><tr className="text-left text-slate-500"><th className="pb-2">SKU</th><th>Stock</th><th>Days Idle</th><th>Action</th></tr></thead>
          <tbody>{slow.map((c) => <tr key={c.sku} className="border-t border-slate-100"><td className="py-2 font-semibold">{c.sku}</td><td>{formatNumber(c.stockUnits)}</td><td>{c.daysOfSupply ?? 45}+</td><td><span className="rounded bg-amber-100 px-2 py-0.5 text-amber-700">Markdown</span></td></tr>)}</tbody>
        </table>
      </Shell>
      <Shell title="Fast Movers" height={260}>
        <table className="w-full text-xs"><thead><tr className="text-left text-slate-500"><th className="pb-2">SKU</th><th>Sales</th><th>ROS</th><th>Priority</th></tr></thead>
          <tbody>{fast.map((c) => <tr key={c.sku} className="border-t border-slate-100"><td className="py-2 font-semibold">{c.sku}</td><td>{formatNumber(c.unitsSold ?? 0)}</td><td>{(c.dailyVelocity ?? 0).toFixed(1)}</td><td><span className="rounded bg-blue-100 px-2 py-0.5 text-blue-700">Replenish</span></td></tr>)}</tbody>
        </table>
      </Shell>
    </div>
  );
}

export function DispatchSection({ totalSoh }: { totalSoh: number }) {
  const data = Array.from({ length: 7 }, (_, i) => ({ day: `D${i + 1}`, dispatch: totalSoh * 0.008 * (0.9 + i * 0.03), inward: totalSoh * 0.009 * (0.85 + i * 0.04) }));
  return (
    <Shell title="Dispatch & Inward" subtitle="Warehouse movement trends" height={300}>
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={data}>
          <CartesianGrid strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="day" tick={{ fontSize: 10 }} />
          <YAxis tickFormatter={(v) => formatNumber(v)} tick={{ fontSize: 10 }} />
          <Tooltip formatter={(v: number) => formatNumber(v)} />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          <Bar dataKey="dispatch" name="Dispatch" fill="#2563eb" radius={[4, 4, 0, 0]} />
          <Line type="monotone" dataKey="inward" name="Inward" stroke="#10b981" strokeWidth={2} dot={false} />
        </ComposedChart>
      </ResponsiveContainer>
    </Shell>
  );
}

export function InventoryHeatmap({ rows }: { rows: StockRow[] }) {
  const stores = [...new Set(rows.map((r) => r.site_name))].slice(0, 6);
  const skus = [...new Set(rows.map((r) => r.sku || r.barcode))].slice(0, 8);
  const data = stores.flatMap((store) => skus.map((sku, si) => {
    const match = rows.find((r) => r.site_name === store && (r.sku === sku || r.barcode === sku));
    return { store: store.slice(0, 10), sku: sku.slice(0, 8), qty: match?.qty ?? Math.round(10 + si * 3), storeFull: store, skuFull: sku };
  }));
  return (
    <Shell title="Store vs SKU Heatmap" subtitle="Hover for stock level" height={320}>
      <ResponsiveContainer width="100%" height="100%">
        <ScatterChart margin={{ top: 8, right: 8, bottom: 8, left: 8 }}>
          <XAxis type="category" dataKey="sku" name="SKU" tick={{ fontSize: 8 }} interval={0} angle={-20} textAnchor="end" height={50} />
          <YAxis type="category" dataKey="store" name="Store" tick={{ fontSize: 9 }} width={72} />
          <ZAxis type="number" dataKey="qty" range={[60, 400]} />
          <Tooltip cursor={{ strokeDasharray: '3 3' }} formatter={(v: number) => formatNumber(v)} />
          <Scatter data={data} fill="#2563eb" fillOpacity={0.75} />
        </ScatterChart>
      </ResponsiveContainer>
    </Shell>
  );
}

export function HealthScoreSection({ summary }: { summary: InventorySummary | null }) {
  const scores = buildHealthScores(summary);
  return (
    <Shell title="Inventory Health Score" height={200}>
      <div className="grid h-full grid-cols-2 gap-3 sm:grid-cols-4">
        {scores.map((s) => (
          <div key={s.label} className="rounded-xl border border-slate-100 p-4 dark:border-slate-800">
            <div className={`mb-2 h-2 rounded-full ${s.color}`} style={{ width: `${s.pct}%` }} />
            <p className="text-xs font-semibold text-slate-500">{s.label}</p>
            <p className="text-2xl font-bold text-slate-900 dark:text-white">{formatNumber(s.count)}</p>
            <p className="text-[10px] text-slate-400">{s.pct}% of network</p>
          </div>
        ))}
      </div>
    </Shell>
  );
}

export function AIInventoryInsights({ summary, kpis }: { summary: InventorySummary | null; kpis: InventoryKpiData }) {
  const items = [
    { title: 'High OOS Risk', body: `${summary?.atRiskSkus ?? 0} SKUs below safety stock thresholds across the network.`, type: 'danger' },
    { title: 'Excess Inventory', body: 'Winterwear categories show 18% above optimal cover in East zone warehouses.', type: 'warning' },
    { title: 'Warehouse Bottlenecks', body: 'Central DC dispatch lead time elevated by 1.2 days vs SLA.', type: 'warning' },
    { title: 'Slow Moving Categories', body: `${summary?.slowMovingSkus ?? 0} SKUs in slow-moving band — consider promotional clearance.`, type: 'info' },
    { title: 'Fast Selling Products', body: `${summary?.fastMovingSkus ?? 0} fast movers driving ${kpis.sellThrough.toFixed(0)}% avg sell-through.`, type: 'success' },
    { title: 'Replenishment Recommendations', body: 'Prioritize top 20% SKUs with week cover below 2 weeks at flagship stores.', type: 'info' },
  ];
  return (
    <section className="overflow-hidden rounded-2xl border border-slate-200/80 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900/60">
      <div className="flex items-center gap-2 bg-gradient-to-r from-blue-600 to-indigo-700 px-5 py-4">
        <Bot className="h-4 w-4 text-white" /><h3 className="text-sm font-bold text-white">AI Inventory Insights</h3><Sparkles className="ml-auto h-4 w-4 text-blue-200" />
      </div>
      <div className="grid gap-3 p-4 sm:grid-cols-2 lg:grid-cols-3">{items.map((item) => (
        <div key={item.title} className={`rounded-xl border p-3 text-xs ${item.type === 'danger' ? 'border-rose-200 bg-rose-50/50' : item.type === 'warning' ? 'border-amber-200 bg-amber-50/50' : item.type === 'success' ? 'border-emerald-200 bg-emerald-50/50' : 'border-blue-200 bg-blue-50/50'}`}>
          <p className="font-bold text-slate-800">{item.title}</p><p className="mt-1 text-slate-600">{item.body}</p>
        </div>
      ))}</div>
    </section>
  );
}

export function InventoryAlertsPanel({ summary }: { summary: InventorySummary | null }) {
  const alerts = [
    { type: 'Low Stock', title: '12 SKUs below reorder point', color: 'bg-amber-100 text-amber-700' },
    { type: 'Critical OOS', title: `${summary?.atRiskSkus ?? 8} critical stockouts`, color: 'bg-rose-100 text-rose-700' },
    { type: 'High Shrinkage', title: '3 stores above shrinkage threshold', color: 'bg-orange-100 text-orange-700' },
    { type: 'Dead Inventory', title: `${Math.round((summary?.atRiskSkus ?? 0) * 0.4)} dead stock SKUs`, color: 'bg-slate-200 text-slate-700' },
    { type: 'Capacity Warning', title: 'Central DC at 89% capacity', color: 'bg-violet-100 text-violet-700' },
    { type: 'Delayed Dispatch', title: '2 warehouse dispatch delays', color: 'bg-red-100 text-red-700' },
  ];
  return (
    <Shell title="Inventory Alerts" height={180}>
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        {alerts.map((a) => (
          <div key={a.type} className="rounded-xl border border-slate-100 p-3 dark:border-slate-800">
            <span className={`rounded px-1.5 py-0.5 text-[9px] font-bold uppercase ${a.color}`}>{a.type}</span>
            <p className="mt-2 text-xs font-semibold text-slate-800 dark:text-white">{a.title}</p>
          </div>
        ))}
      </div>
    </Shell>
  );
}

export function DrillBreadcrumb({ path, onNav }: { path: string[]; onNav: (i: number) => void }) {
  if (!path.length) return null;
  return (
    <div className="flex flex-wrap items-center gap-1 rounded-xl border border-blue-200 bg-blue-50/50 px-3 py-2 text-xs text-blue-700">
      <span className="font-semibold">Drill:</span>
      {path.map((p, i) => (
        <button key={p} type="button" onClick={() => onNav(i)} className="font-semibold hover:underline">{i > 0 && ' → '}{p}</button>
      ))}
    </div>
  );
}

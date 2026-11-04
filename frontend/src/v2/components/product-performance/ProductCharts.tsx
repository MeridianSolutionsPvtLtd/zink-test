import React, { useMemo, useState } from 'react';
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  Treemap,
  XAxis,
  YAxis,
  ZAxis,
} from 'recharts';
import {
  aggregateBy,
  buildTrendData,
  formatCompactInr,
  formatInr,
  type DrillLevel,
  type ProductRecord,
} from './productData';
import { SectionCard as ChartCard } from '../../design-system';

const BLUE = '#2563eb';
const SLATE = '#94a3b8';
const PERIODS = ['daily', 'weekly', 'monthly', 'quarterly', 'yearly'] as const;
type Period = (typeof PERIODS)[number];

function ProductThumb({ hue }: { hue: number }) {
  return (
    <div
      className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-[10px] font-bold text-white shadow-sm"
      style={{ background: `linear-gradient(135deg, hsl(${hue},65%,45%), hsl(${hue + 30},55%,35%))` }}
    >
      IMG
    </div>
  );
}

export function DrillBreadcrumb({
  path,
  onNavigate,
}: {
  path: string[];
  onNavigate: (idx: number) => void;
}) {
  const labels = ['Department', 'Category', 'Section', 'Story', 'SKU', 'EAN'];
  return (
    <div className="flex flex-wrap items-center gap-1 rounded-xl border border-blue-100 bg-blue-50/50 px-3 py-2 text-xs dark:border-blue-900/40 dark:bg-blue-950/30">
      <span className="font-semibold text-blue-700 dark:text-blue-300">Drill:</span>
      {labels.map((label, i) => (
        <React.Fragment key={label}>
          {i > 0 && <span className="text-slate-400">→</span>}
          <button
            type="button"
            onClick={() => onNavigate(i)}
            className={`rounded-md px-2 py-0.5 font-medium transition ${
              i === path.length - 1
                ? 'bg-blue-600 text-white'
                : i < path.length
                  ? 'text-blue-600 hover:bg-blue-100 dark:hover:bg-blue-900/40'
                  : 'text-slate-400'
            }`}
          >
            {path[i] ?? label}
          </button>
        </React.Fragment>
      ))}
    </div>
  );
}

export function ProductSalesTrend({ totalSales }: { totalSales: number }) {
  const [period, setPeriod] = useState<Period>('monthly');
  const data = useMemo(() => buildTrendData(totalSales, period), [totalSales, period]);

  return (
    <ChartCard title="Product Sales Trend" subtitle="Sales & quantity across time periods">
      <div className="mb-3 flex flex-wrap gap-1">
        {PERIODS.map((p) => (
          <button
            key={p}
            type="button"
            onClick={() => setPeriod(p)}
            className={`rounded-lg px-3 py-1 text-xs font-semibold capitalize transition ${
              period === p ? 'bg-blue-600 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300'
            }`}
          >
            {p}
          </button>
        ))}
      </div>
      <ResponsiveContainer width="100%" height={280}>
        <AreaChart data={data}>
          <defs>
            <linearGradient id="salesGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={BLUE} stopOpacity={0.25} />
              <stop offset="100%" stopColor={BLUE} stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
          <XAxis dataKey="label" tick={{ fontSize: 11 }} />
          <YAxis tick={{ fontSize: 11 }} tickFormatter={(v) => formatCompactInr(v)} />
          <Tooltip formatter={(v: number) => formatInr(v)} />
          <Legend />
          <Area type="monotone" dataKey="sales" name="Sales" stroke={BLUE} fill="url(#salesGrad)" strokeWidth={2} />
          <Line type="monotone" dataKey="quantity" name="Quantity" stroke="#06b6d4" strokeWidth={2} dot={false} />
        </AreaChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

export function SkuRankingTable({
  title,
  products,
  variant,
  onSkuClick,
}: {
  title: string;
  products: ProductRecord[];
  variant: 'top' | 'bottom';
  onSkuClick?: (sku: string) => void;
}) {
  const sorted = useMemo(
    () =>
      [...products]
        .sort((a, b) => (variant === 'top' ? b.sales - a.sales : a.sales - b.sales))
        .slice(0, 8),
    [products, variant]
  );

  return (
    <ChartCard title={title} subtitle={variant === 'top' ? 'Highest revenue SKUs' : 'Underperforming SKUs'}>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-slate-100 text-left text-[10px] uppercase tracking-wider text-slate-400">
              <th className="pb-2 pr-2">#</th>
              <th className="pb-2 pr-2">SKU</th>
              <th className="pb-2 pr-2">Image</th>
              <th className="pb-2 pr-2 text-right">Sales</th>
              <th className="pb-2 pr-2 text-right">Qty</th>
              <th className="pb-2 pr-2 text-right">Margin</th>
              <th className="pb-2 pr-2 text-right">Sell Through</th>
              <th className="pb-2 text-right">Growth</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((p, i) => {
              return (
                <tr
                  key={p.id}
                  className={`border-b border-slate-50 transition hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/50 ${
                    variant === 'bottom' ? 'bg-rose-50/30 dark:bg-rose-950/10' : ''
                  }`}
                >
                  <td className="py-2.5 pr-2 font-bold text-slate-400">{i + 1}</td>
                  <td className="py-2.5 pr-2">
                    <button type="button" onClick={() => onSkuClick?.(p.sku)} className="font-semibold text-blue-600 hover:underline">
                      {p.sku}
                    </button>
                  </td>
                  <td className="py-2.5 pr-2"><ProductThumb hue={p.hue} /></td>
                  <td className="py-2.5 pr-2 text-right font-medium">{formatCompactInr(p.sales)}</td>
                  <td className="py-2.5 pr-2 text-right">{p.quantity}</td>
                  <td className="py-2.5 pr-2 text-right">{formatCompactInr(p.margin)}</td>
                  <td className="py-2.5 pr-2 text-right">{p.sellThrough}%</td>
                  <td className="py-2.5 text-right font-semibold text-slate-400">—</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </ChartCard>
  );
}

const ABC_COLORS = { A: '#2563eb', B: '#06b6d4', C: '#94a3b8' };

export function AbcAnalysisSection({ products }: { products: ProductRecord[] }) {
  const data = useMemo(() => {
    const counts = { A: 0, B: 0, C: 0 };
    const sales = { A: 0, B: 0, C: 0 };
    products.forEach((p) => {
      counts[p.abcClass]++;
      sales[p.abcClass] += p.sales;
    });
    return (['A', 'B', 'C'] as const).map((k) => ({
      name: `Class ${k}`,
      class: k,
      count: counts[k],
      sales: sales[k],
    }));
  }, [products]);

  return (
    <ChartCard title="ABC Analysis" subtitle="Sales contribution classification">
      <div className="grid gap-4 md:grid-cols-2">
        <ResponsiveContainer width="100%" height={220}>
          <PieChart>
            <Pie data={data} dataKey="sales" nameKey="name" cx="50%" cy="50%" innerRadius={50} outerRadius={80} paddingAngle={3}>
              {data.map((d) => (
                <Cell key={d.class} fill={ABC_COLORS[d.class]} />
              ))}
            </Pie>
            <Tooltip formatter={(v: number) => formatInr(v)} />
            <Legend />
          </PieChart>
        </ResponsiveContainer>
        <div className="space-y-2">
          {data.map((d) => (
            <div key={d.class} className="flex items-center justify-between rounded-xl border border-slate-100 px-3 py-2 dark:border-slate-800">
              <div className="flex items-center gap-2">
                <span className="flex h-8 w-8 items-center justify-center rounded-lg text-sm font-bold text-white" style={{ background: ABC_COLORS[d.class] }}>
                  {d.class}
                </span>
                <span className="text-sm font-medium">{d.count} SKUs</span>
              </div>
              <span className="text-sm font-bold">{formatCompactInr(d.sales)}</span>
            </div>
          ))}
        </div>
      </div>
    </ChartCard>
  );
}

const LIFECYCLE_COLORS: Record<ProductRecord['lifecycle'], string> = {
  'New Arrival': '#22c55e',
  Growing: '#06b6d4',
  'Best Seller': '#2563eb',
  Mature: '#8b5cf6',
  Declining: '#f59e0b',
  'Dead Stock': '#ef4444',
};

export function LifecycleSection({ products }: { products: ProductRecord[] }) {
  const data = useMemo(() => {
    const map = new Map<string, number>();
    products.forEach((p) => map.set(p.lifecycle, (map.get(p.lifecycle) ?? 0) + 1));
    return Object.keys(LIFECYCLE_COLORS).map((stage) => ({
      stage,
      count: map.get(stage) ?? 0,
    }));
  }, [products]);

  return (
    <ChartCard title="Product Lifecycle" subtitle="Stage distribution across portfolio">
      <ResponsiveContainer width="100%" height={240}>
        <BarChart data={data} layout="vertical" margin={{ left: 20 }}>
          <CartesianGrid strokeDasharray="3 3" horizontal={false} />
          <XAxis type="number" tick={{ fontSize: 11 }} />
          <YAxis type="category" dataKey="stage" width={100} tick={{ fontSize: 10 }} />
          <Tooltip />
          <Bar dataKey="count" radius={[0, 6, 6, 0]}>
            {data.map((d) => (
              <Cell key={d.stage} fill={LIFECYCLE_COLORS[d.stage as ProductRecord['lifecycle']]} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

function TreemapContent(props: { x?: number; y?: number; width?: number; height?: number; name?: string; sales?: number }) {
  const { x = 0, y = 0, width = 0, height = 0, name = '', sales = 0 } = props;
  if (width < 30 || height < 20) return null;
  return (
    <g>
      <rect x={x} y={y} width={width} height={height} fill="#2563eb" fillOpacity={0.15 + (sales % 5) * 0.12} stroke="#fff" strokeWidth={2} rx={4} />
      {width > 50 && height > 30 && (
        <>
          <text x={x + 6} y={y + 16} fontSize={10} fontWeight={600} fill="#1e40af">{name}</text>
          <text x={x + 6} y={y + 30} fontSize={9} fill="#64748b">{formatCompactInr(sales)}</text>
        </>
      )}
    </g>
  );
}

export function DepartmentTreemap({ products }: { products: ProductRecord[] }) {
  const data = useMemo(() => aggregateBy(products, 'department').map((d) => ({ name: d.name, size: d.sales, sales: d.sales })), [products]);
  return (
    <ChartCard title="Department Contribution" subtitle="Sales share by department">
      <ResponsiveContainer width="100%" height={260}>
        <Treemap data={data} dataKey="size" aspectRatio={4 / 3} stroke="#fff" content={<TreemapContent />} />
      </ResponsiveContainer>
    </ChartCard>
  );
}

export function CategoryDonut({ products }: { products: ProductRecord[] }) {
  const data = useMemo(() => aggregateBy(products, 'category').slice(0, 6), [products]);
  const colors = ['#2563eb', '#06b6d4', '#8b5cf6', '#22c55e', '#f59e0b', '#ef4444'];
  return (
    <ChartCard title="Category Contribution" subtitle="Interactive category mix">
      <ResponsiveContainer width="100%" height={260}>
        <PieChart>
          <Pie data={data} dataKey="sales" nameKey="name" cx="50%" cy="50%" innerRadius={55} outerRadius={90}>
            {data.map((_, i) => (
              <Cell key={i} fill={colors[i % colors.length]} />
            ))}
          </Pie>
          <Tooltip formatter={(v: number) => formatInr(v)} />
          <Legend />
        </PieChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

export function StoryPerformanceChart({ products }: { products: ProductRecord[] }) {
  const data = useMemo(() => aggregateBy(products, 'story').slice(0, 8), [products]);
  return (
    <ChartCard title="Story Performance" subtitle="Horizontal ranking by story">
      <ResponsiveContainer width="100%" height={260}>
        <BarChart data={data} layout="vertical" margin={{ left: 10 }}>
          <CartesianGrid strokeDasharray="3 3" horizontal={false} />
          <XAxis type="number" tickFormatter={(v) => formatCompactInr(v)} tick={{ fontSize: 10 }} />
          <YAxis type="category" dataKey="name" width={90} tick={{ fontSize: 10 }} />
          <Tooltip formatter={(v: number) => formatInr(v)} />
          <Bar dataKey="sales" fill={BLUE} radius={[0, 6, 6, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

export function SectionPerformanceChart({ products }: { products: ProductRecord[] }) {
  const data = useMemo(() => aggregateBy(products, 'section'), [products]);
  return (
    <ChartCard title="Section Performance" subtitle="Sales by section">
      <ResponsiveContainer width="100%" height={240}>
        <BarChart data={data}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="name" tick={{ fontSize: 10 }} />
          <YAxis tickFormatter={(v) => formatCompactInr(v)} tick={{ fontSize: 10 }} />
          <Tooltip formatter={(v: number) => formatInr(v)} />
          <Bar dataKey="sales" fill="#06b6d4" radius={[6, 6, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

export function DiscountAnalysisSection({ products }: { products: ProductRecord[] }) {
  const totalSales = products.reduce((s, p) => s + p.sales, 0);
  const discounted = products.reduce((s, p) => s + p.discount, 0);
  const fullPrice = totalSales - discounted;
  const markdownPct = totalSales > 0 ? (discounted / totalSales) * 100 : 0;
  const data = [
    { name: 'Full Price', value: fullPrice },
    { name: 'Discounted', value: discounted },
  ];

  return (
    <ChartCard title="Discount Analysis" subtitle="Full price vs discounted sales">
      <div className="mb-3 grid grid-cols-3 gap-2 text-center">
        <div className="rounded-xl bg-emerald-50 p-2 dark:bg-emerald-950/30">
          <p className="text-[10px] text-slate-500">Full Price</p>
          <p className="text-sm font-bold text-emerald-700">{formatCompactInr(fullPrice)}</p>
        </div>
        <div className="rounded-xl bg-amber-50 p-2 dark:bg-amber-950/30">
          <p className="text-[10px] text-slate-500">Discounted</p>
          <p className="text-sm font-bold text-amber-700">{formatCompactInr(discounted)}</p>
        </div>
        <div className="rounded-xl bg-blue-50 p-2 dark:bg-blue-950/30">
          <p className="text-[10px] text-slate-500">Markdown %</p>
          <p className="text-sm font-bold text-blue-700">{markdownPct.toFixed(1)}%</p>
        </div>
      </div>
      <ResponsiveContainer width="100%" height={200}>
        <BarChart data={data}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="name" />
          <YAxis tickFormatter={(v) => formatCompactInr(v)} />
          <Tooltip formatter={(v: number) => formatInr(v)} />
          <Bar dataKey="value" fill={BLUE} radius={[6, 6, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

export function MarginAnalysisSection({ products }: { products: ProductRecord[] }) {
  const gross = products.reduce((s, p) => s + p.margin, 0);
  const net = gross * 0.82;
  const sales = products.reduce((s, p) => s + p.sales, 0);
  const marginPct = sales > 0 ? (gross / sales) * 100 : 0;
  const data = [
    { name: 'Gross', value: gross },
    { name: 'Net', value: net },
    { name: 'Contrib %', value: marginPct * 1000 },
  ];

  return (
    <ChartCard title="Margin Analysis" subtitle="Gross, net & contribution">
      <div className="mb-2 grid grid-cols-2 gap-2 sm:grid-cols-4">
        {[
          { l: 'Gross Margin', v: formatCompactInr(gross) },
          { l: 'Net Margin', v: formatCompactInr(net) },
          { l: 'Margin %', v: `${marginPct.toFixed(1)}%` },
          { l: 'Contribution', v: `${(marginPct * 0.9).toFixed(1)}%` },
        ].map((x) => (
          <div key={x.l} className="rounded-lg border border-slate-100 px-2 py-1.5 dark:border-slate-800">
            <p className="text-[10px] text-slate-500">{x.l}</p>
            <p className="text-sm font-bold">{x.v}</p>
          </div>
        ))}
      </div>
      <ResponsiveContainer width="100%" height={200}>
        <LineChart data={data}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="name" />
          <YAxis tickFormatter={(v) => formatCompactInr(v)} />
          <Tooltip formatter={(v: number) => formatInr(v)} />
          <Line type="monotone" dataKey="value" stroke="#2563eb" strokeWidth={2} />
        </LineChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

export function SellThroughSection({ products }: { products: ProductRecord[] }) {
  const avg = products.length ? products.reduce((s, p) => s + p.sellThrough, 0) / products.length : 0;
  const top = useMemo(() => [...products].sort((a, b) => b.sellThrough - a.sellThrough).slice(0, 5), [products]);
  const pct = Math.min(100, avg);

  return (
    <ChartCard title="Sell Through Analysis" subtitle="Gauge, trend & ranking">
      <div className="grid gap-4 md:grid-cols-2">
        <div className="flex flex-col items-center justify-center">
          <div className="relative h-28 w-28">
            <svg viewBox="0 0 100 100" className="h-full w-full -rotate-90">
              <circle cx="50" cy="50" r="42" fill="none" stroke="#e2e8f0" strokeWidth="10" />
              <circle
                cx="50"
                cy="50"
                r="42"
                fill="none"
                stroke={pct >= 60 ? '#22c55e' : pct >= 40 ? '#f59e0b' : '#ef4444'}
                strokeWidth="10"
                strokeDasharray={`${pct * 2.64} 264`}
                strokeLinecap="round"
              />
            </svg>
            <div className="absolute inset-0 flex flex-col items-center justify-center">
              <span className="text-2xl font-bold">{avg.toFixed(0)}%</span>
              <span className="text-[10px] text-slate-500">Avg ST</span>
            </div>
          </div>
        </div>
        <div className="space-y-1">
          {top.map((p, i) => (
            <div key={p.id} className="flex items-center justify-between text-xs">
              <span className="truncate font-medium">{i + 1}. {p.sku}</span>
              <span className="font-bold text-blue-600">{p.sellThrough}%</span>
            </div>
          ))}
        </div>
      </div>
    </ChartCard>
  );
}

export function WeekCoverSection({ products }: { products: ProductRecord[] }) {
  const buckets = useMemo(() => {
    let healthy = 0;
    let medium = 0;
    let critical = 0;
    products.forEach((p) => {
      if (p.weekCover <= 4) healthy++;
      else if (p.weekCover <= 8) medium++;
      else critical++;
    });
    return [
      { name: 'Healthy', count: healthy, fill: '#22c55e' },
      { name: 'Medium', count: medium, fill: '#f59e0b' },
      { name: 'Critical', count: critical, fill: '#ef4444' },
    ];
  }, [products]);

  return (
    <ChartCard title="Week Cover" subtitle="Inventory health by cover weeks">
      <ResponsiveContainer width="100%" height={220}>
        <BarChart data={buckets}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="name" />
          <YAxis />
          <Tooltip />
          <Bar dataKey="count" radius={[6, 6, 0, 0]}>
            {buckets.map((b) => (
              <Cell key={b.name} fill={b.fill} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

export function InventoryVsSalesScatter({ products }: { products: ProductRecord[] }) {
  const data = useMemo(
    () =>
      products.slice(0, 40).map((p) => ({
        stock: p.stock,
        sales: p.sales,
        ros: p.ros,
        sku: p.sku,
      })),
    [products]
  );

  return (
    <ChartCard title="Inventory vs Sales" subtitle="Stock, sales & ROS correlation">
      <ResponsiveContainer width="100%" height={280}>
        <ScatterChart margin={{ bottom: 10 }}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis type="number" dataKey="stock" name="Stock" tick={{ fontSize: 10 }} />
          <YAxis type="number" dataKey="sales" name="Sales" tickFormatter={(v) => formatCompactInr(v)} tick={{ fontSize: 10 }} />
          <ZAxis type="number" dataKey="ros" range={[40, 400]} />
          <Tooltip cursor={{ strokeDasharray: '3 3' }} formatter={(v: number, n: string) => (n === 'Sales' ? formatInr(v) : v)} />
          <Scatter data={data} fill={BLUE} fillOpacity={0.6} />
        </ScatterChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

export function DrillDownChart({
  products,
  level,
  onDrill,
}: {
  products: ProductRecord[];
  level: DrillLevel;
  onDrill: (value: string) => void;
}) {
  const data = useMemo(() => aggregateBy(products, level).slice(0, 10), [products, level]);
  return (
    <ChartCard title={`Drill Down — ${level}`} subtitle="Click a bar to drill deeper">
      <ResponsiveContainer width="100%" height={220}>
        <BarChart data={data}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="name" tick={{ fontSize: 9 }} interval={0} angle={-20} textAnchor="end" height={60} />
          <YAxis tickFormatter={(v) => formatCompactInr(v)} tick={{ fontSize: 10 }} />
          <Tooltip formatter={(v: number) => formatInr(v)} />
          <Bar
            dataKey="sales"
            fill={BLUE}
            radius={[6, 6, 0, 0]}
            cursor="pointer"
            onClick={(d) => {
              const payload = d as { name?: string };
              if (payload?.name) onDrill(payload.name);
            }}
          />
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

import React from 'react';
import {
  AlertTriangle,
  Brain,
  Package,
  Sparkles,
  TrendingDown,
  TrendingUp,
  Zap,
} from 'lucide-react';
import { formatCompactInr, type ProductRecord } from './productData';

function InsightRow({
  icon: Icon,
  label,
  value,
  tone = 'default',
}: {
  icon: React.ElementType;
  label: string;
  value: string;
  tone?: 'default' | 'success' | 'warning' | 'danger';
  key?: React.Key;
}) {
  const tones = {
    default: 'border-slate-100 bg-slate-50/50 dark:border-slate-800 dark:bg-slate-900/40',
    success: 'border-emerald-100 bg-emerald-50/50 dark:border-emerald-900/40 dark:bg-emerald-950/20',
    warning: 'border-amber-100 bg-amber-50/50 dark:border-amber-900/40 dark:bg-amber-950/20',
    danger: 'border-rose-100 bg-rose-50/50 dark:border-rose-900/40 dark:bg-rose-950/20',
  };
  return (
    <div className={`rounded-xl border p-2.5 transition hover:shadow-sm ${tones[tone]}`}>
      <div className="flex items-start gap-2">
        <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-blue-600" />
        <div className="min-w-0 flex-1">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">{label}</p>
          <p className="truncate text-xs font-bold text-slate-900 dark:text-white">{value}</p>
        </div>
      </div>
    </div>
  );
}

export function ProductAIInsights({
  products,
  variant = 'bottom',
}: {
  products: ProductRecord[];
  variant?: 'sidebar' | 'bottom';
}) {
  const sorted = [...products].sort((a, b) => b.sales - a.sales);
  const byMargin = [...products].sort((a, b) => b.marginPct - a.marginPct);
  const bySt = [...products].sort((a, b) => a.sellThrough - b.sellThrough);
  const dead = products.filter((p) => p.lifecycle === 'Dead Stock');
  const fast = products.filter((p) => p.ros >= 8);
  const slow = products.filter((p) => p.ros <= 2);
  const replenish = products.filter((p) => p.weekCover <= 3 && p.ros >= 4);
  const markdown = [...products].sort((a, b) => b.discountPct - a.discountPct).slice(0, 3);
  const seasonWinners = products.filter((p) => p.season === 'AW-25' && p.sellThrough >= 60).slice(0, 3);

  const items = [
    { icon: TrendingUp, label: 'Best Selling SKU', value: `${sorted[0]?.sku} · ${formatCompactInr(sorted[0]?.sales ?? 0)}`, tone: 'success' as const },
    { icon: TrendingDown, label: 'Worst SKU', value: `${sorted[sorted.length - 1]?.sku} · ${formatCompactInr(sorted[sorted.length - 1]?.sales ?? 0)}`, tone: 'danger' as const },
    { icon: Zap, label: 'High Margin Products', value: `${byMargin[0]?.sku} · ${byMargin[0]?.marginPct}% margin`, tone: 'success' as const },
    { icon: Package, label: 'Needs Replenishment', value: replenish.length ? `${replenish.length} SKUs · ${replenish[0]?.sku}` : 'None critical', tone: 'warning' as const },
    { icon: AlertTriangle, label: 'Dead Inventory', value: `${dead.length} SKUs · ${formatCompactInr(dead.reduce((s, p) => s + p.sales, 0))}`, tone: 'danger' as const },
    { icon: TrendingUp, label: 'Fast Movers', value: `${fast.length} SKUs · avg ROS ${(fast.reduce((s, p) => s + p.ros, 0) / Math.max(fast.length, 1)).toFixed(1)}`, tone: 'success' as const },
    { icon: TrendingDown, label: 'Slow Movers', value: `${slow.length} SKUs · avg ROS ${(slow.reduce((s, p) => s + p.ros, 0) / Math.max(slow.length, 1)).toFixed(1)}`, tone: 'warning' as const },
    { icon: Sparkles, label: 'Season Winners', value: seasonWinners.length ? seasonWinners.map((p) => p.sku).join(', ') : 'AW-25 leaders pending', tone: 'success' as const },
    { icon: AlertTriangle, label: 'Markdown Candidates', value: markdown.map((p) => `${p.sku} (${p.discountPct}%)`).join(' · '), tone: 'warning' as const },
  ];

  const header = (
    <div className="border-b border-blue-100 bg-gradient-to-r from-blue-600 to-indigo-600 px-5 py-4 dark:border-blue-900">
      <div className="flex items-center gap-2 text-white">
        <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-white/20 backdrop-blur">
          <Brain className="h-4 w-4" />
        </div>
        <div>
          <h3 className="text-sm font-bold">AI Product Insights</h3>
          <p className="text-[10px] text-blue-100">Merchandising intelligence</p>
        </div>
        <Sparkles className="ml-auto h-4 w-4 text-blue-200" />
      </div>
    </div>
  );

  if (variant === 'bottom') {
    return (
      <section className="overflow-hidden rounded-2xl border border-slate-200/70 bg-gradient-to-b from-white to-slate-50/80 shadow-sm dark:border-slate-800/80 dark:from-slate-900 dark:to-slate-950">
        {header}
        <div className="grid gap-3 p-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {items.map((item) => (
            <InsightRow key={item.label} icon={item.icon} label={item.label} value={item.value} tone={item.tone} />
          ))}
          <div className="rounded-xl border border-dashed border-blue-200 bg-blue-50/30 p-2.5 sm:col-span-2 lg:col-span-3 xl:col-span-4 dark:border-blue-800 dark:bg-blue-950/20">
            <p className="text-[10px] font-semibold text-blue-700 dark:text-blue-300">Low sell-through alert</p>
            <p className="mt-1 text-xs text-slate-600 dark:text-slate-400">
              {bySt.slice(0, 3).map((p) => p.sku).join(', ')} below 40% ST — review pricing & placement.
            </p>
          </div>
        </div>
        <div className="border-t border-slate-100 px-5 py-3 dark:border-slate-800">
          <p className="text-[10px] leading-relaxed text-slate-400">
            Insights refresh with filters. Cross-filter charts to refine recommendations.
          </p>
        </div>
      </section>
    );
  }

  return (
    <div className="sticky top-16 flex max-h-[calc(100vh-5rem)] flex-col overflow-hidden rounded-2xl border border-blue-200/80 bg-white shadow-lg dark:border-blue-900/50 dark:bg-slate-900/95">
      {header}
      <div className="flex-1 space-y-2 overflow-y-auto p-3">
        {items.map((item) => (
          <InsightRow key={item.label} icon={item.icon} label={item.label} value={item.value} tone={item.tone} />
        ))}
        <div className="rounded-xl border border-dashed border-blue-200 bg-blue-50/30 p-2.5 dark:border-blue-800 dark:bg-blue-950/20">
          <p className="text-[10px] font-semibold text-blue-700 dark:text-blue-300">Low sell-through alert</p>
          <p className="mt-1 text-xs text-slate-600 dark:text-slate-400">
            {bySt.slice(0, 3).map((p) => p.sku).join(', ')} below 40% ST — review pricing & placement.
          </p>
        </div>
      </div>
    </div>
  );
}

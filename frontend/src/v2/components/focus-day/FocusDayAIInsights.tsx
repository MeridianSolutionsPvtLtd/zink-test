import React from 'react';
import { AlertTriangle, Brain, MapPin, Package, Sparkles, Store, TrendingDown, TrendingUp } from 'lucide-react';
import { aggregateBy, formatCompactInr, type FocusKpiData, type FocusRecord } from './focusDayData';

function InsightRow({ icon: Icon, label, value, tone = 'default' }: { icon: React.ElementType; label: string; value: string; tone?: 'default' | 'success' | 'warning' | 'danger'; key?: React.Key }) {
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
          <p className="text-xs font-bold text-slate-900 dark:text-white">{value}</p>
        </div>
      </div>
    </div>
  );
}

export function FocusDayAIInsights({ rows, kpis }: { rows: FocusRecord[]; kpis: FocusKpiData }) {
  const byEvent = aggregateBy(rows, 'event');
  const byCat = aggregateBy(rows, 'category');
  const byZone = aggregateBy(rows, 'zone');
  const bySku = aggregateBy(rows, 'sku');
  const byStore = aggregateBy(rows, 'store');
  const missed = byStore.filter((s) => s.target > 0 && s.sales < s.target * 0.85);
  const fast = [...rows].sort((a, b) => b.quantity - a.quantity).slice(0, 3);
  const slow = [...rows].sort((a, b) => a.quantity - b.quantity).slice(0, 3);
  const upcoming = rows.filter((r) => r.event === 'Republic Day' || r.event === 'Custom Campaign');

  const items = [
    { icon: Sparkles, label: 'Best Performing Event', value: `${byEvent[0]?.name ?? 'Diwali'} · ${formatCompactInr(byEvent[0]?.sales ?? 0)}`, tone: 'success' as const },
    { icon: Package, label: 'Best Selling Category', value: `${byCat[0]?.name ?? 'Casual'} · ${formatCompactInr(byCat[0]?.sales ?? 0)}`, tone: 'success' as const },
    { icon: TrendingUp, label: 'Best Selling SKU', value: `${bySku[0]?.name ?? 'SKU-3000'} · ${formatCompactInr(bySku[0]?.sales ?? 0)}`, tone: 'success' as const },
    { icon: MapPin, label: 'Best Performing Zone', value: `${byZone[0]?.name ?? 'North'} · ${formatCompactInr(byZone[0]?.sales ?? 0)}`, tone: 'success' as const },
    { icon: Store, label: 'Stores Missing Targets', value: missed.length ? `${missed.length} stores below 85% · ${missed[0]?.name}` : 'All stores on track', tone: missed.length ? 'danger' as const : 'success' as const },
    { icon: TrendingUp, label: 'Fast Moving Products', value: fast.map((p) => p.sku).join(', '), tone: 'success' as const },
    { icon: TrendingDown, label: 'Slow Moving Products', value: slow.map((p) => p.sku).join(', '), tone: 'warning' as const },
    { icon: Package, label: 'Replenishment Before Next Event', value: `${Math.round(kpis.quantity * 0.15).toLocaleString('en-IN')} units suggested for ${upcoming[0]?.event ?? 'Republic Day'}`, tone: 'warning' as const },
    { icon: AlertTriangle, label: 'Inventory Risk', value: `₹${formatCompactInr(kpis.invValue * 0.22)} at risk before upcoming campaign`, tone: 'danger' as const },
    { icon: Sparkles, label: 'Revenue Opportunity', value: `+${formatCompactInr(kpis.target - kpis.festivalSales)} gap to target if discount optimized`, tone: 'success' as const },
  ];

  return (
    <section className="overflow-hidden rounded-2xl border border-slate-200/70 bg-gradient-to-b from-white to-slate-50/80 shadow-sm dark:border-slate-800/80 dark:from-slate-900 dark:to-slate-950">
      <div className="border-b border-blue-100 bg-gradient-to-r from-blue-600 to-indigo-600 px-5 py-4 dark:border-blue-900">
        <div className="flex items-center gap-2 text-white">
          <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-white/20 backdrop-blur">
            <Brain className="h-4 w-4" />
          </div>
          <div>
            <h3 className="text-sm font-bold">AI Business Insights</h3>
            <p className="text-[10px] text-blue-100">Festival day intelligence for leadership</p>
          </div>
          <Sparkles className="ml-auto h-4 w-4 text-blue-200" />
        </div>
      </div>
      <div className="grid gap-3 p-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
        {items.map((item) => (
          <InsightRow key={item.label} icon={item.icon} label={item.label} value={item.value} tone={item.tone} />
        ))}
      </div>
    </section>
  );
}

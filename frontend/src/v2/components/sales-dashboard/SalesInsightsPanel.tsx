import React from 'react';
import { Bot, ChevronRight, Sparkles, TrendingDown, TrendingUp } from 'lucide-react';
import type { SalesInsights, SalesKpis, SalesRow } from './types';
import {
  aggregateByKey,
  deriveCategory,
  deriveZone,
  formatCompactInr,
} from './salesAnalytics';

export function DrillDownBreadcrumb({
  path,
  onNavigate,
}: {
  path: { level: string; value: string }[];
  onNavigate: (index: number) => void;
}) {
  if (!path.length) return null;
  return (
    <div className="flex flex-wrap items-center gap-1 rounded-xl border border-indigo-200/80 bg-indigo-50/50 px-3 py-2 text-xs dark:border-indigo-500/30 dark:bg-indigo-500/10">
      <span className="font-semibold text-indigo-700 dark:text-indigo-300">Drill-down:</span>
      {path.map((p, i) => (
        <React.Fragment key={`${p.level}-${p.value}`}>
          {i > 0 && <ChevronRight className="h-3 w-3 text-indigo-400" />}
          <button
            type="button"
            onClick={() => onNavigate(i)}
            className="font-semibold text-indigo-600 hover:underline dark:text-indigo-400"
          >
            {p.level}: {p.value}
          </button>
        </React.Fragment>
      ))}
    </div>
  );
}

export function SalesAIInsights({
  rows,
  kpis,
  insights,
}: {
  rows: SalesRow[];
  kpis: SalesKpis | null;
  insights: SalesInsights | null;
}) {
  const zones = aggregateByKey(rows, deriveZone);
  const categories = aggregateByKey(rows, deriveCategory);
  const bestZone = zones[0];
  const worstStore = insights?.top_revenue_sites?.[insights.top_revenue_sites.length - 1];
  const topGrowthCat = categories[0];
  const topMarginSku = insights?.top_sold_skus?.[0];

  const items = [
    {
      title: 'Top Performing Zone',
      body: bestZone ? `${bestZone.name} leads with ${formatCompactInr(bestZone.value)} in filtered sales.` : 'Apply filters to generate zone insights.',
      type: 'success' as const,
    },
    {
      title: 'Worst Performing Store',
      body: worstStore ? `${worstStore.site_name} is trailing in the current selection.` : 'No store ranking available for current filters.',
      type: 'warning' as const,
    },
    {
      title: 'Highest Growth Category',
      body: topGrowthCat ? `${topGrowthCat.name} shows strongest contribution in current data slice.` : 'Category growth data unavailable.',
      type: 'success' as const,
    },
    {
      title: 'Highest Margin SKU',
      body: topMarginSku ? `${topMarginSku.sku} drives ${formatCompactInr(topMarginSku.net_sales)} with high unit velocity.` : 'SKU margin analysis pending data.',
      type: 'info' as const,
    },
    {
      title: 'Sales Risk',
      body: kpis && kpis.discount_on_gross_pct > 12
        ? `Discount depth at ${kpis.discount_on_gross_pct}% may compress margin if sustained.`
        : 'Discount levels are within acceptable range for the period.',
      type: 'warning' as const,
    },
    {
      title: 'Inventory Opportunity',
      body: 'Replenish fast movers in top stores where sell-through exceeds 70% on current page.',
      type: 'info' as const,
    },
    {
      title: 'Sales Opportunity',
      body: `Expand assortment in ${bestZone?.name ?? 'leading'} zones based on department mix in current selection.`,
      type: 'info' as const,
    },
  ];

  return (
    <aside className="rounded-2xl border border-slate-200/80 bg-gradient-to-b from-white to-slate-50/80 shadow-sm dark:border-slate-800 dark:from-slate-900 dark:to-slate-950">
      <div className="flex items-center gap-2 border-b border-slate-100 bg-gradient-to-r from-blue-600 to-blue-700 px-4 py-3 dark:border-slate-800">
        <Bot className="h-4 w-4 text-white" />
        <div>
          <h3 className="text-sm font-bold text-white">AI Insights</h3>
          <p className="text-[10px] text-blue-100">Sales intelligence</p>
        </div>
        <Sparkles className="ml-auto h-4 w-4 text-blue-200" />
      </div>
      <div className="space-y-2 p-3">
        {items.map((item) => (
          <div
            key={item.title}
            className={`rounded-xl border p-3 text-xs ${
              item.type === 'success'
                ? 'border-blue-200/80 bg-blue-50/50 dark:border-blue-500/20 dark:bg-blue-500/10'
                : item.type === 'warning'
                  ? 'border-amber-200/80 bg-amber-50/50 dark:border-amber-500/20 dark:bg-amber-500/10'
                  : 'border-indigo-200/80 bg-indigo-50/50 dark:border-indigo-500/20 dark:bg-indigo-500/10'
            }`}
          >
            <div className="flex items-start gap-2">
              {item.type === 'warning' ? (
                <TrendingDown className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              ) : (
                <TrendingUp className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              )}
              <div>
                <p className="font-bold text-slate-800 dark:text-slate-100">{item.title}</p>
                <p className="mt-1 leading-relaxed text-slate-600 dark:text-slate-400">{item.body}</p>
              </div>
            </div>
          </div>
        ))}
      </div>
    </aside>
  );
}

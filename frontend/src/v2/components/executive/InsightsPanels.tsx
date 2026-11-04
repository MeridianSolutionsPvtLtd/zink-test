import React from 'react';
import {
  AlertTriangle,
  Bot,
  Lightbulb,
  Sparkles,
  TrendingDown,
  TrendingUp,
} from 'lucide-react';
import { formatInr } from './utils';

export type Insight = {
  id: string;
  type: 'success' | 'warning' | 'info' | 'danger';
  title: string;
  body: string;
  metric?: string;
};

const insightIcon = {
  success: TrendingUp,
  warning: AlertTriangle,
  info: Lightbulb,
  danger: TrendingDown,
};

const insightStyle = {
  success: 'border-emerald-200/80 bg-emerald-50/50 dark:border-emerald-500/20 dark:bg-emerald-500/10',
  warning: 'border-amber-200/80 bg-amber-50/50 dark:border-amber-500/20 dark:bg-amber-500/10',
  info: 'border-indigo-200/80 bg-indigo-50/50 dark:border-indigo-500/20 dark:bg-indigo-500/10',
  danger: 'border-rose-200/80 bg-rose-50/50 dark:border-rose-500/20 dark:bg-rose-500/10',
};

export function AIInsightsPanel({
  insights,
  variant = 'sidebar',
}: {
  insights: Insight[];
  variant?: 'sidebar' | 'bottom';
}) {
  if (variant === 'bottom') {
    return (
      <section className="overflow-hidden rounded-2xl border border-slate-200/70 bg-gradient-to-b from-white to-slate-50/80 shadow-sm dark:border-slate-800/80 dark:from-slate-900 dark:to-slate-950">
        <div className="border-b border-slate-100 bg-gradient-to-r from-indigo-600 to-violet-600 px-5 py-4 dark:border-slate-800">
          <div className="flex items-center gap-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-white/20 backdrop-blur">
              <Bot className="h-4 w-4 text-white" />
            </div>
            <div>
              <h3 className="text-sm font-bold text-white">AI Business Insights</h3>
              <p className="text-[10px] text-indigo-100">Powered by analytics engine</p>
            </div>
            <Sparkles className="ml-auto h-4 w-4 text-indigo-200" />
          </div>
        </div>
        <div className="grid gap-3 p-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {insights.map((item) => {
            const Icon = insightIcon[item.type];
            return (
              <div
                key={item.id}
                className={`rounded-xl border p-3 transition hover:shadow-md ${insightStyle[item.type]}`}
              >
                <div className="flex items-start gap-2">
                  <Icon className="mt-0.5 h-4 w-4 shrink-0 text-slate-600 dark:text-slate-300" />
                  <div className="min-w-0">
                    <p className="text-xs font-bold text-slate-800 dark:text-slate-100">{item.title}</p>
                    <p className="mt-1 text-[11px] leading-relaxed text-slate-600 dark:text-slate-400">{item.body}</p>
                    {item.metric && (
                      <p className="mt-1.5 text-[11px] font-bold text-indigo-600 dark:text-indigo-400">{item.metric}</p>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
        <div className="border-t border-slate-100 px-5 py-3 dark:border-slate-800">
          <p className="text-[10px] leading-relaxed text-slate-400">
            Recommendations refresh with each data sync. Cross-filter charts to refine insights.
          </p>
        </div>
      </section>
    );
  }

  return (
    <aside className="sticky top-20 hidden h-[calc(100vh-6rem)] w-[280px] shrink-0 flex-col overflow-hidden rounded-2xl border border-slate-200/70 bg-gradient-to-b from-white to-slate-50/80 shadow-[0_8px_40px_rgba(99,102,241,0.08)] dark:border-slate-800/80 dark:from-slate-900 dark:to-slate-950 xl:flex">
      <div className="border-b border-slate-100 bg-gradient-to-r from-indigo-600 to-violet-600 px-4 py-4 dark:border-slate-800">
        <div className="flex items-center gap-2">
          <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-white/20 backdrop-blur">
            <Bot className="h-4 w-4 text-white" />
          </div>
          <div>
            <h3 className="text-sm font-bold text-white">AI Business Insights</h3>
            <p className="text-[10px] text-indigo-100">Powered by analytics engine</p>
          </div>
          <Sparkles className="ml-auto h-4 w-4 text-indigo-200" />
        </div>
      </div>
      <div className="flex-1 space-y-2.5 overflow-y-auto p-3">
        {insights.map((item) => {
          const Icon = insightIcon[item.type];
          return (
            <div
              key={item.id}
              className={`rounded-xl border p-3 transition hover:shadow-md ${insightStyle[item.type]}`}
            >
              <div className="flex items-start gap-2">
                <Icon className="mt-0.5 h-4 w-4 shrink-0 text-slate-600 dark:text-slate-300" />
                <div>
                  <p className="text-xs font-bold text-slate-800 dark:text-slate-100">{item.title}</p>
                  <p className="mt-1 text-[11px] leading-relaxed text-slate-600 dark:text-slate-400">{item.body}</p>
                  {item.metric && (
                    <p className="mt-1.5 text-[11px] font-bold text-indigo-600 dark:text-indigo-400">{item.metric}</p>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>
      <div className="border-t border-slate-100 p-3 dark:border-slate-800">
        <p className="text-[10px] leading-relaxed text-slate-400">
          Recommendations refresh with each data sync. Cross-filter charts to refine insights.
        </p>
      </div>
    </aside>
  );
}

export type AlertItem = {
  id: string;
  type: 'oos' | 'target' | 'inventory' | 'warehouse' | 'growth';
  title: string;
  detail: string;
  severity: 'critical' | 'high' | 'medium';
  time: string;
};

const alertMeta = {
  oos: { label: 'OOS Alert', color: 'bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-400' },
  target: { label: 'Target Miss', color: 'bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-400' },
  inventory: { label: 'Inventory Risk', color: 'bg-orange-100 text-orange-700 dark:bg-orange-500/15 dark:text-orange-400' },
  warehouse: { label: 'Warehouse Delay', color: 'bg-violet-100 text-violet-700 dark:bg-violet-500/15 dark:text-violet-400' },
  growth: { label: 'Negative Growth', color: 'bg-red-100 text-red-700 dark:bg-red-500/15 dark:text-red-400' },
};

const severityDot = {
  critical: 'bg-rose-500',
  high: 'bg-amber-500',
  medium: 'bg-slate-400',
};

export function AlertsPanel({ alerts }: { alerts: AlertItem[] }) {
  return (
    <div className="rounded-2xl border border-slate-200/70 bg-white/90 shadow-sm dark:border-slate-800/80 dark:bg-slate-900/60">
      <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4 dark:border-slate-800">
        <div>
          <h3 className="text-sm font-semibold text-slate-900 dark:text-white">Executive Alerts</h3>
          <p className="text-xs text-slate-500">Critical signals requiring attention</p>
        </div>
        <span className="rounded-full bg-rose-100 px-2.5 py-0.5 text-[10px] font-bold text-rose-700 dark:bg-rose-500/15 dark:text-rose-400">
          {alerts.length} active
        </span>
      </div>
      <div className="grid gap-2 p-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
        {alerts.map((alert) => (
          <div
            key={alert.id}
            className="rounded-xl border border-slate-100 bg-slate-50/50 p-3 transition hover:border-indigo-200 hover:bg-white dark:border-slate-800 dark:bg-slate-800/30 dark:hover:border-indigo-500/30"
          >
            <div className="flex items-center gap-2">
              <span className={`h-2 w-2 rounded-full ${severityDot[alert.severity]}`} />
              <span className={`rounded-md px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide ${alertMeta[alert.type].color}`}>
                {alertMeta[alert.type].label}
              </span>
            </div>
            <p className="mt-2 text-xs font-semibold text-slate-800 dark:text-slate-100">{alert.title}</p>
            <p className="mt-1 text-[11px] text-slate-500 dark:text-slate-400">{alert.detail}</p>
            <p className="mt-2 text-[10px] text-slate-400">{alert.time}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

export function buildInsights(data: {
  bestZone: string;
  bestZoneSales: number;
  worstStore: string;
  salesTrend: number;
  inventoryRisk: string;
  stockoutSkus: number;
  opportunity: string;
}): Insight[] {
  return [
    {
      id: '1',
      type: 'success',
      title: 'Best Performing Zone',
      body: `${data.bestZone} leads network performance with strong sell-through and margin expansion.`,
      metric: formatInr(data.bestZoneSales, true),
    },
    {
      id: '2',
      type: 'danger',
      title: 'Lowest Performing Store',
      body: `${data.worstStore} is trailing target by a significant margin. Review staffing and assortment.`,
    },
    {
      id: '3',
      type: data.salesTrend >= 0 ? 'success' : 'warning',
      title: 'Sales Trend',
      body: data.salesTrend >= 0
        ? 'Net sales are trending upward versus the prior period across core channels.'
        : 'Net sales are softening week-on-week. Monitor discount depth and traffic conversion.',
      metric: `${data.salesTrend >= 0 ? '+' : ''}${data.salesTrend.toFixed(1)}% vs prior`,
    },
    {
      id: '4',
      type: 'warning',
      title: 'Inventory Risk',
      body: data.inventoryRisk,
    },
    {
      id: '5',
      type: 'danger',
      title: 'Stockout Warning',
      body: `${data.stockoutSkus} high-velocity SKUs are at critical stock levels across the network.`,
    },
    {
      id: '6',
      type: 'info',
      title: 'Opportunity Areas',
      body: data.opportunity,
    },
    {
      id: '7',
      type: 'info',
      title: 'Executive Recommendation',
      body: 'Prioritize replenishment for top 20% SKUs in West and South zones. Hold promotional spend in underperforming stores until fill rates recover above 92%.',
    },
  ];
}

export function buildAlerts(data: {
  oosCount: number;
  targetMissStores: number;
  inventoryRiskStores: number;
  warehouseDelays: number;
  negativeGrowthZones: number;
}): AlertItem[] {
  return [
    {
      id: 'a1',
      type: 'oos',
      title: `${data.oosCount} SKUs Out of Stock`,
      detail: 'Critical stockouts in fast-moving categories affecting conversion.',
      severity: 'critical',
      time: '2 min ago',
    },
    {
      id: 'a2',
      type: 'target',
      title: `${data.targetMissStores} Stores Below Target`,
      detail: 'Monthly revenue achievement below 85% threshold.',
      severity: 'high',
      time: '15 min ago',
    },
    {
      id: 'a3',
      type: 'inventory',
      title: `${data.inventoryRiskStores} Stores — Inventory Risk`,
      detail: 'Excess aging inventory detected in seasonal styles.',
      severity: 'medium',
      time: '32 min ago',
    },
    {
      id: 'a4',
      type: 'warehouse',
      title: `${data.warehouseDelays} Warehouse Delays`,
      detail: 'Inbound shipments delayed beyond SLA at central DC.',
      severity: 'high',
      time: '1 hr ago',
    },
    {
      id: 'a5',
      type: 'growth',
      title: `${data.negativeGrowthZones} Zones — Negative Growth`,
      detail: 'Week-on-week sales decline in selected geographies.',
      severity: 'critical',
      time: '2 hr ago',
    },
  ];
}

import React from 'react';
import { AlertTriangle, Brain, Package, Sparkles, TrendingDown, TrendingUp, Zap } from 'lucide-react';
import { formatCompact, type ForecastKpiData, type InsightRow } from './forecastAnalytics';

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
          <p className="text-xs font-bold text-slate-900 dark:text-white">{value}</p>
        </div>
      </div>
    </div>
  );
}

export function ForecastAIInsights({
  kpis,
  insights,
  targetSeason,
}: {
  kpis: ForecastKpiData;
  insights: InsightRow[];
  targetSeason: string;
}) {
  const stockUp = insights.filter((r) => r.action === 'STOCK UP');
  const highRisk = insights.filter((r) => r.risk_flag === 'HIGH');
  const topSku = [...insights].sort((a, b) => (b.predicted_qty ?? 0) - (a.predicted_qty ?? 0))[0];
  const lowConf = insights.filter((r) => !r.focus_comment).length;
  const highConf = insights.length - lowConf;

  const items = [
    { icon: TrendingUp, label: 'Demand Next Month', value: `+${Math.max(kpis.demandGrowth, 4).toFixed(1)}% expected uplift`, tone: 'success' as const },
    { icon: AlertTriangle, label: 'Potential Stock Shortage', value: highRisk.length ? `${highRisk.length} HIGH risk SKUs flagged` : 'No critical shortages', tone: highRisk.length ? 'danger' as const : 'success' as const },
    { icon: Package, label: 'Replenishment Required', value: stockUp.length ? `${stockUp.length} STOCK UP actions for ${targetSeason}` : 'Balanced inventory', tone: 'warning' as const },
    { icon: Zap, label: 'High Confidence Forecast', value: `${highConf} SKUs with strong signal`, tone: 'success' as const },
    { icon: TrendingDown, label: 'Low Confidence Forecast', value: `${lowConf} SKUs need review`, tone: 'warning' as const },
    { icon: Package, label: 'Inventory Adjustment', value: `Target ${formatCompact(kpis.inventoryTarget)} units`, tone: 'default' as const },
    { icon: Sparkles, label: 'Suggested Purchase Qty', value: topSku ? `${topSku.sku}: ${formatCompact(topSku.predicted_qty ?? 0)}` : 'Run filters to refine', tone: 'success' as const },
  ];

  return (
    <section className="overflow-hidden rounded-2xl border border-slate-200/70 bg-gradient-to-b from-white to-slate-50/80 shadow-sm dark:border-slate-800/80 dark:from-slate-900 dark:to-slate-950">
      <div className="border-b border-blue-100 bg-gradient-to-r from-blue-600 to-indigo-600 px-5 py-4 dark:border-blue-900">
        <div className="flex items-center gap-2 text-white">
          <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-white/20 backdrop-blur">
            <Brain className="h-4 w-4" />
          </div>
          <div>
            <h3 className="text-sm font-bold">AI Forecast Insights</h3>
            <p className="text-[10px] text-blue-100">Demand planning intelligence</p>
          </div>
          <Sparkles className="ml-auto h-4 w-4 text-blue-200" />
        </div>
      </div>
      <div className="grid gap-3 p-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {items.map((item) => (
          <InsightRow key={item.label} icon={item.icon} label={item.label} value={item.value} tone={item.tone} />
        ))}
      </div>
    </section>
  );
}

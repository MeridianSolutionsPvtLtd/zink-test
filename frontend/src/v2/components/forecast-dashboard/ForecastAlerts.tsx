import React, { useMemo } from 'react';
import {
  AlertTriangle,
  PackageX,
  Percent,
  TrendingDown,
  TrendingUp,
  Truck,
} from 'lucide-react';
import type { ForecastKpiData, InsightRow } from './forecastAnalytics';

const SEV = {
  high: 'border-rose-200 bg-rose-50 dark:border-rose-900/50 dark:bg-rose-950/30',
  medium: 'border-amber-200 bg-amber-50 dark:border-amber-900/50 dark:bg-amber-950/30',
  low: 'border-blue-200 bg-blue-50 dark:border-blue-900/50 dark:bg-blue-950/30',
};

export function ForecastAlertsPanel({ kpis, insights }: { kpis: ForecastKpiData; insights: InsightRow[] }) {
  const alerts = useMemo(() => {
    const deviation = Math.abs(kpis.variance);
    const shortage = insights.filter((r) => r.action === 'STOCK UP' && r.risk_flag === 'HIGH').length;
    const overstock = insights.filter((r) => r.action === 'REDUCE').length;
    const spike = insights.filter((r) => (r.predicted_qty ?? 0) > 500).length;
    const supplyDelay = insights.filter((r) => r.risk_flag === 'HIGH').length;
    const lowAccuracy = kpis.accuracy < 80;

    return [
      { id: 'dev', type: 'Forecast Deviation', icon: Percent, message: `${deviation.toFixed(1)}% variance from target`, count: deviation > 5 ? 1 : 0, severity: 'medium' as const },
      { id: 'short', type: 'Inventory Shortage', icon: PackageX, message: `${shortage} SKUs flagged for stock-up with HIGH risk`, count: shortage, severity: 'high' as const },
      { id: 'over', type: 'Overstock Warning', icon: TrendingDown, message: `${overstock} REDUCE recommendations active`, count: overstock, severity: 'medium' as const },
      { id: 'spike', type: 'Demand Spike', icon: TrendingUp, message: `${spike} SKUs with elevated predicted qty`, count: spike, severity: 'high' as const },
      { id: 'delay', type: 'Supply Delay', icon: Truck, message: `${supplyDelay} high-risk supply chain flags`, count: supplyDelay, severity: 'medium' as const },
      { id: 'acc', type: 'Low Forecast Accuracy', icon: AlertTriangle, message: lowAccuracy ? `Accuracy at ${kpis.accuracy.toFixed(1)}% — review baseline` : 'Accuracy within acceptable range', count: lowAccuracy ? 1 : 0, severity: 'high' as const },
    ].filter((a) => a.count > 0);
  }, [kpis, insights]);

  return (
    <div className="rounded-2xl border border-slate-200/80 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900/60">
      <div className="mb-3 flex items-center gap-2">
        <AlertTriangle className="h-4 w-4 text-amber-500" />
        <div>
          <h3 className="text-sm font-bold text-slate-900 dark:text-white">Alerts</h3>
          <p className="text-[11px] text-slate-500">{alerts.length} active planning alerts</p>
        </div>
      </div>
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {alerts.map((a) => {
          const Icon = a.icon;
          return (
            <div key={a.id} className={`flex items-start gap-3 rounded-xl border p-3 transition hover:shadow-sm ${SEV[a.severity]}`}>
              <Icon className="mt-0.5 h-4 w-4 shrink-0 text-slate-600" />
              <div>
                <p className="text-xs font-bold text-slate-900 dark:text-white">{a.type}</p>
                <p className="mt-0.5 text-[11px] text-slate-600 dark:text-slate-400">{a.message}</p>
              </div>
              <span className="ml-auto rounded-full bg-white/80 px-2 py-0.5 text-[10px] font-bold dark:bg-slate-900/80">{a.count}</span>
            </div>
          );
        })}
        {alerts.length === 0 && (
          <p className="col-span-full py-4 text-center text-xs text-slate-400">No active alerts for current filters.</p>
        )}
      </div>
    </div>
  );
}

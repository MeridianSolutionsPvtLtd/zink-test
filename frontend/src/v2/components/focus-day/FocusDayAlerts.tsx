import React, { useMemo } from 'react';
import { AlertTriangle, PackageX, Percent, TrendingDown, Warehouse } from 'lucide-react';
import type { FocusKpiData, FocusRecord } from './focusDayData';

const SEV = {
  high: 'border-rose-200 bg-rose-50 dark:border-rose-900/50 dark:bg-rose-950/30',
  medium: 'border-amber-200 bg-amber-50 dark:border-amber-900/50 dark:bg-amber-950/30',
  low: 'border-blue-200 bg-blue-50 dark:border-blue-900/50 dark:bg-blue-950/30',
};

export function FocusDayAlertsPanel({ rows, kpis }: { rows: FocusRecord[]; kpis: FocusKpiData }) {
  const alerts = useMemo(() => {
    const targetMiss = rows.filter((r) => r.sales < r.target * 0.9).length;
    const shortage = rows.filter((r) => r.stock < 20 && r.sellThrough > 50).length;
    const stockout = rows.filter((r) => r.stock < 10).length;
    const highDisc = rows.filter((r) => r.discountPct > 25).length;
    const negGrowth = kpis.lyComparison < 0;
    const slow = rows.filter((r) => r.sellThrough < 35).length;
    const overstock = rows.filter((r) => r.stock > r.quantity * 2).length;

    return [
      { id: 'tgt', type: 'Target Miss', icon: AlertTriangle, message: `${targetMiss} store-SKU combos below 90% of target`, count: targetMiss, severity: 'high' as const },
      { id: 'short', type: 'Inventory Shortage', icon: PackageX, message: `${shortage} high-velocity items with low stock`, count: shortage, severity: 'high' as const },
      { id: 'oos', type: 'Stockout Risk', icon: Warehouse, message: `${stockout} SKUs with stock below 10 units`, count: stockout, severity: 'high' as const },
      { id: 'disc', type: 'High Discount', icon: Percent, message: `${highDisc} items with discount above 25%`, count: highDisc, severity: 'medium' as const },
      { id: 'neg', type: 'Negative Growth', icon: TrendingDown, message: negGrowth ? `LY comparison at ${kpis.lyComparison.toFixed(1)}%` : 'Growth positive vs last year', count: negGrowth ? 1 : 0, severity: 'high' as const },
      { id: 'slow', type: 'Slow Movers', icon: TrendingDown, message: `${slow} products with sell-through below 35%`, count: slow, severity: 'medium' as const },
      { id: 'over', type: 'Overstock', icon: Warehouse, message: `${overstock} SKUs with excess inventory`, count: overstock, severity: 'medium' as const },
    ].filter((a) => a.count > 0);
  }, [rows, kpis]);

  return (
    <div className="rounded-2xl border border-slate-200/80 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900/60">
      <div className="mb-3 flex items-center gap-2">
        <AlertTriangle className="h-4 w-4 text-amber-500" />
        <div>
          <h3 className="text-sm font-bold text-slate-900 dark:text-white">Alerts Panel</h3>
          <p className="text-[11px] text-slate-500">{alerts.length} active festival day alerts</p>
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
        {alerts.length === 0 && <p className="col-span-full py-4 text-center text-xs text-slate-400">No active alerts for current filters.</p>}
      </div>
    </div>
  );
}

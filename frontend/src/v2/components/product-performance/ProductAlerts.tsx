import React, { useMemo } from 'react';
import {
  AlertTriangle,
  Ban,
  Percent,
  PackageX,
  ShoppingCart,
  TrendingDown,
  Warehouse,
} from 'lucide-react';
import { formatCompactInr, type ProductRecord } from './productData';

type Alert = {
  id: string;
  type: string;
  icon: React.ElementType;
  message: string;
  count: number;
  severity: 'high' | 'medium' | 'low';
};

const SEV_STYLES = {
  high: 'border-rose-200 bg-rose-50 dark:border-rose-900/50 dark:bg-rose-950/30',
  medium: 'border-amber-200 bg-amber-50 dark:border-amber-900/50 dark:bg-amber-950/30',
  low: 'border-blue-200 bg-blue-50 dark:border-blue-900/50 dark:bg-blue-950/30',
};

export function ProductAlertsPanel({ products }: { products: ProductRecord[] }) {
  const alerts = useMemo((): Alert[] => {
    const lowSt = products.filter((p) => p.sellThrough < 35);
    const highDisc = products.filter((p) => p.discountPct > 20);
    const dead = products.filter((p) => p.lifecycle === 'Dead Stock');
    const highInv = products.filter((p) => p.weekCover > 10);
    const stockout = products.filter((p) => p.stock < 15 && p.ros >= 5);
    const negMargin = products.filter((p) => p.marginPct < 15);

    return [
      {
        id: 'st',
        type: 'Low Sell Through',
        icon: TrendingDown,
        message: `${lowSt.length} SKUs below 35% sell-through`,
        count: lowSt.length,
        severity: 'high' as const,
      },
      {
        id: 'disc',
        type: 'High Discount',
        icon: Percent,
        message: `${highDisc.length} SKUs with discount > 20%`,
        count: highDisc.length,
        severity: 'medium' as const,
      },
      {
        id: 'dead',
        type: 'Dead Stock',
        icon: PackageX,
        message: `${dead.length} dead stock items · ${formatCompactInr(dead.reduce((s, p) => s + p.stock * 850, 0))} tied up`,
        count: dead.length,
        severity: 'high' as const,
      },
      {
        id: 'inv',
        type: 'High Inventory',
        icon: Warehouse,
        message: `${highInv.length} SKUs with week cover > 10w`,
        count: highInv.length,
        severity: 'medium' as const,
      },
      {
        id: 'oos',
        type: 'Stockout Risk',
        icon: ShoppingCart,
        message: `${stockout.length} fast movers at low stock`,
        count: stockout.length,
        severity: 'high' as const,
      },
      {
        id: 'margin',
        type: 'Negative Margin',
        icon: Ban,
        message: `${negMargin.length} SKUs with margin below 15%`,
        count: negMargin.length,
        severity: 'high' as const,
      },
    ].filter((a) => a.count > 0);
  }, [products]);

  return (
    <div className="rounded-2xl border border-slate-200/80 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900/60">
      <div className="mb-3 flex items-center gap-2">
        <AlertTriangle className="h-4 w-4 text-amber-500" />
        <div>
          <h3 className="text-sm font-bold text-slate-900 dark:text-white">Alerts</h3>
          <p className="text-[11px] text-slate-500">{alerts.length} active product alerts</p>
        </div>
      </div>
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {alerts.map((a) => {
          const Icon = a.icon;
          return (
            <div
              key={a.id}
              className={`flex items-start gap-3 rounded-xl border p-3 transition hover:shadow-sm ${SEV_STYLES[a.severity]}`}
            >
              <Icon className="mt-0.5 h-4 w-4 shrink-0 text-slate-600" />
              <div>
                <p className="text-xs font-bold text-slate-900 dark:text-white">{a.type}</p>
                <p className="mt-0.5 text-[11px] text-slate-600 dark:text-slate-400">{a.message}</p>
              </div>
              <span className="ml-auto rounded-full bg-white/80 px-2 py-0.5 text-[10px] font-bold dark:bg-slate-900/80">
                {a.count}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

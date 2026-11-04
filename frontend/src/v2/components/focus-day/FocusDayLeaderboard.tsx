import React, { useMemo, useState } from 'react';
import { Trophy } from 'lucide-react';
import { aggregateBy, formatCompactInr, type FocusRecord } from './focusDayData';

const TABS = ['Stores', 'Managers', 'Regions', 'Categories', 'Products'] as const;
type Tab = (typeof TABS)[number];

export function FocusDayLeaderboard({ rows }: { rows: FocusRecord[] }) {
  const [tab, setTab] = useState<Tab>('Stores');

  const data = useMemo(() => {
    const keyMap: Record<Tab, keyof FocusRecord> = {
      Stores: 'store',
      Managers: 'manager',
      Regions: 'zone',
      Categories: 'category',
      Products: 'sku',
    };
    return aggregateBy(rows, keyMap[tab]).slice(0, 10);
  }, [rows, tab]);

  return (
    <div className="rounded-2xl border border-slate-200/80 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900/60">
      <div className="border-b border-slate-100 px-4 py-3 dark:border-slate-800">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <Trophy className="h-4 w-4 text-amber-500" />
            <div>
              <h3 className="text-sm font-bold text-slate-900 dark:text-white">Event Leaderboard</h3>
              <p className="text-[11px] text-slate-500">Top performers across dimensions</p>
            </div>
          </div>
          <div className="flex flex-wrap gap-1">
            {TABS.map((t) => (
              <button key={t} type="button" onClick={() => setTab(t)} className={`rounded-lg px-2.5 py-1 text-[10px] font-semibold ${tab === t ? 'bg-blue-600 text-white' : 'bg-slate-100 text-slate-600 dark:bg-slate-800'}`}>
                {t}
              </button>
            ))}
          </div>
        </div>
      </div>
      <div className="p-4">
        <div className="space-y-2">
          {data.map((item, i) => {
            const pct = item.target > 0 ? (item.sales / item.target) * 100 : 0;
            return (
              <div key={item.name} className="flex items-center gap-3 rounded-xl border border-slate-100 px-3 py-2 transition hover:bg-blue-50/30 dark:border-slate-800">
                <span className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-xs font-bold ${i < 3 ? 'bg-amber-100 text-amber-700' : 'bg-slate-100 text-slate-500'}`}>
                  {i + 1}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-semibold text-slate-900 dark:text-white">{item.name}</p>
                  <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
                    <div className="h-full rounded-full bg-blue-600 transition-all" style={{ width: `${Math.min(100, pct)}%` }} />
                  </div>
                </div>
                <div className="text-right">
                  <p className="text-sm font-bold text-blue-600">{formatCompactInr(item.sales)}</p>
                  <p className="text-[10px] text-slate-400">{pct.toFixed(0)}% of target</p>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

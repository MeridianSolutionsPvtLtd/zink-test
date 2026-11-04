import React, { useMemo, useState } from 'react';
import { Download, Search } from 'lucide-react';
import { buildMonthlyDetail, formatCompact } from './forecastAnalytics';
import type { InsightRow } from './forecastAnalytics';

export function ForecastDetailTable({ insights }: { insights: InsightRow[] }) {
  const [search, setSearch] = useState('');
  const [sortKey, setSortKey] = useState<'month' | 'forecast' | 'actual' | 'variance' | 'accuracy'>('month');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc');

  const rows = useMemo(() => {
    let data = buildMonthlyDetail(insights);
    const q = search.toLowerCase();
    if (q) data = data.filter((r) => r.month.toLowerCase().includes(q));
    return [...data].sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      if (typeof av === 'number' && typeof bv === 'number') return sortDir === 'asc' ? av - bv : bv - av;
      return sortDir === 'asc' ? String(av).localeCompare(String(bv)) : String(bv).localeCompare(String(av));
    });
  }, [insights, search, sortKey, sortDir]);

  const toggleSort = (key: typeof sortKey) => {
    if (sortKey === key) setSortDir(sortDir === 'asc' ? 'desc' : 'asc');
    else {
      setSortKey(key);
      setSortDir('desc');
    }
  };

  const exportCsv = () => {
    const headers = ['Month', 'Forecast', 'Actual', 'Variance', 'Accuracy', 'Demand', 'Supply', 'Inventory Target'];
    const lines = [headers.join(',')];
    rows.forEach((r) => {
      lines.push([r.month, r.forecast, r.actual, r.variance.toFixed(1), r.accuracy.toFixed(1), r.demand, r.supply, r.inventoryTarget].join(','));
    });
    const blob = new Blob([lines.join('\n')], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'forecast-detail.csv';
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="rounded-2xl border border-slate-200/80 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900/60">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-4 py-3 dark:border-slate-800">
        <div>
          <h3 className="text-sm font-bold text-slate-900 dark:text-white">Forecast Detail Table</h3>
          <p className="text-[11px] text-slate-500">Month-level forecast performance</p>
        </div>
        <div className="flex items-center gap-2">
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
            <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search month..." className="rounded-lg border py-1.5 pl-8 pr-3 text-xs dark:border-slate-700 dark:bg-slate-900" />
          </div>
          <button type="button" onClick={exportCsv} className="inline-flex items-center gap-1 rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-blue-700">
            <Download className="h-3.5 w-3.5" /> Export
          </button>
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-full text-xs">
          <thead className="sticky top-0 z-10 bg-slate-50 dark:bg-slate-900">
            <tr className="border-b border-slate-200 text-left text-[10px] uppercase tracking-wider text-slate-500 dark:border-slate-800">
              {[
                { key: 'month' as const, label: 'Month', align: 'left' },
                { key: 'forecast' as const, label: 'Forecast', align: 'right' },
                { key: 'actual' as const, label: 'Actual', align: 'right' },
                { key: 'variance' as const, label: 'Variance', align: 'right' },
                { key: 'accuracy' as const, label: 'Accuracy', align: 'right' },
              ].map((col) => (
                <th key={col.key} className={`cursor-pointer whitespace-nowrap px-3 py-2.5 font-semibold hover:text-blue-600 ${col.align === 'right' ? 'text-right' : ''}`} onClick={() => toggleSort(col.key)}>
                  {col.label}{sortKey === col.key && (sortDir === 'asc' ? ' ↑' : ' ↓')}
                </th>
              ))}
              <th className="whitespace-nowrap px-3 py-2.5 text-right font-semibold">Demand</th>
              <th className="whitespace-nowrap px-3 py-2.5 text-right font-semibold">Supply</th>
              <th className="whitespace-nowrap px-3 py-2.5 text-right font-semibold">Inventory Target</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.month} className="border-b border-slate-50 hover:bg-blue-50/30 dark:border-slate-800">
                <td className="whitespace-nowrap px-3 py-2 font-medium">{r.month}</td>
                <td className="whitespace-nowrap px-3 py-2 text-right">{formatCompact(r.forecast)}</td>
                <td className="whitespace-nowrap px-3 py-2 text-right">{formatCompact(r.actual)}</td>
                <td className={`whitespace-nowrap px-3 py-2 text-right font-bold ${r.variance >= 0 ? 'text-amber-600' : 'text-emerald-600'}`}>{r.variance.toFixed(1)}%</td>
                <td className="whitespace-nowrap px-3 py-2 text-right">{r.accuracy.toFixed(1)}%</td>
                <td className="whitespace-nowrap px-3 py-2 text-right">{formatCompact(r.demand)}</td>
                <td className="whitespace-nowrap px-3 py-2 text-right">{formatCompact(r.supply)}</td>
                <td className="whitespace-nowrap px-3 py-2 text-right">{formatCompact(r.inventoryTarget)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

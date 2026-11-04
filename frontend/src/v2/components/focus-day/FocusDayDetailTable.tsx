import React, { useMemo, useState } from 'react';
import { ChevronDown, Download, Search } from 'lucide-react';
import { formatCompactInr, formatInr, type FocusRecord } from './focusDayData';

const ALL_COLUMNS = [
  { key: 'store', label: 'Store', align: 'left' as const },
  { key: 'zone', label: 'Zone', align: 'left' as const },
  { key: 'department', label: 'Department', align: 'left' as const },
  { key: 'category', label: 'Category', align: 'left' as const },
  { key: 'sku', label: 'SKU', align: 'left' as const },
  { key: 'sales', label: 'Sales', align: 'right' as const },
  { key: 'target', label: 'Target', align: 'right' as const },
  { key: 'achievement', label: 'Achievement', align: 'right' as const },
  { key: 'quantity', label: 'Quantity', align: 'right' as const },
  { key: 'discountPct', label: 'Discount', align: 'right' as const },
  { key: 'margin', label: 'Margin', align: 'right' as const },
  { key: 'sellThrough', label: 'Sell Through', align: 'right' as const },
];

export function FocusDayDetailTable({ rows }: { rows: FocusRecord[] }) {
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const [sortKey, setSortKey] = useState<keyof FocusRecord>('sales');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
  const [visibleCols, setVisibleCols] = useState(ALL_COLUMNS.map((c) => c.key));
  const [colsOpen, setColsOpen] = useState(false);
  const pageSize = 15;

  const filtered = useMemo(() => {
    const q = search.toLowerCase();
    let data = rows;
    if (q) data = data.filter((r) => r.store.toLowerCase().includes(q) || r.sku.toLowerCase().includes(q) || r.zone.toLowerCase().includes(q));
    return [...data].sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      if (typeof av === 'number' && typeof bv === 'number') return sortDir === 'asc' ? av - bv : bv - av;
      return sortDir === 'asc' ? String(av).localeCompare(String(bv)) : String(bv).localeCompare(String(av));
    });
  }, [rows, search, sortKey, sortDir]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const pageRows = filtered.slice((page - 1) * pageSize, page * pageSize);

  const exportCsv = () => {
    const headers = ALL_COLUMNS.filter((c) => visibleCols.includes(c.key)).map((c) => c.label);
    const keys = ALL_COLUMNS.filter((c) => visibleCols.includes(c.key)).map((c) => c.key);
    const lines = [headers.join(',')];
    filtered.forEach((r) => {
      const ach = r.target > 0 ? ((r.sales / r.target) * 100).toFixed(1) : '0';
      lines.push(keys.map((k) => (k === 'achievement' ? ach : String(r[k as keyof FocusRecord] ?? ''))).join(','));
    });
    const blob = new Blob([lines.join('\n')], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'focus-day-performance.csv';
    a.click();
    URL.revokeObjectURL(url);
  };

  const renderCell = (r: FocusRecord, key: string) => {
    switch (key) {
      case 'sales':
      case 'target':
      case 'margin':
        return formatInr(r[key as keyof FocusRecord] as number);
      case 'achievement':
        return `${r.target > 0 ? ((r.sales / r.target) * 100).toFixed(1) : 0}%`;
      case 'discountPct':
        return `${r.discountPct}%`;
      case 'sellThrough':
        return `${r.sellThrough}%`;
      default:
        return String(r[key as keyof FocusRecord] ?? '');
    }
  };

  return (
    <div className="rounded-2xl border border-slate-200/80 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900/60">
      <div className="relative z-30 flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-4 py-3 dark:border-slate-800">
        <div>
          <h3 className="text-sm font-bold text-slate-900 dark:text-white">Detailed Performance Table</h3>
          <p className="text-[11px] text-slate-500">{filtered.length} records</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
            <input value={search} onChange={(e) => { setSearch(e.target.value); setPage(1); }} placeholder="Search store, SKU, zone..." className="rounded-lg border py-1.5 pl-8 pr-3 text-xs dark:border-slate-700 dark:bg-slate-900" />
          </div>
          <div className="relative">
            <button type="button" onClick={() => setColsOpen(!colsOpen)} className="inline-flex items-center gap-1 rounded-lg border px-3 py-1.5 text-xs font-semibold dark:border-slate-700">
              Columns <ChevronDown className="h-3.5 w-3.5" />
            </button>
            {colsOpen && (
              <div className="absolute right-0 top-full z-50 mt-1 max-h-52 w-48 overflow-y-auto rounded-xl border bg-white p-2 shadow-lg dark:border-slate-700 dark:bg-slate-900">
                <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-slate-100 pb-1.5 px-1 text-[11px] font-semibold text-blue-600 dark:border-slate-800 dark:text-blue-400">
                  <button
                    type="button"
                    onClick={() => setVisibleCols(ALL_COLUMNS.map((c) => c.key))}
                    className="hover:underline"
                  >
                    Select all
                  </button>
                  <button
                    type="button"
                    onClick={() => setVisibleCols([])}
                    className="hover:underline"
                  >
                    Unselect all
                  </button>
                  <button
                    type="button"
                    onClick={() => setVisibleCols(ALL_COLUMNS.map((c) => c.key))}
                    className="ml-auto hover:underline"
                  >
                    Reset
                  </button>
                </div>
                {ALL_COLUMNS.map((c) => (
                  <label key={c.key} className="flex cursor-pointer items-center gap-2 px-2 py-1 text-xs hover:bg-slate-50 dark:hover:bg-slate-800">
                    <input
                      type="checkbox"
                      checked={visibleCols.includes(c.key)}
                      onChange={() =>
                        setVisibleCols((p) => {
                          if (p.includes(c.key)) {
                            return p.filter((k) => k !== c.key);
                          }
                          return [...p, c.key];
                        })
                      }
                    />
                    {c.label}
                  </label>
                ))}
              </div>
            )}
          </div>
          <button type="button" onClick={exportCsv} className="inline-flex items-center gap-1 rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-blue-700">
            <Download className="h-3.5 w-3.5" /> Export
          </button>
        </div>
      </div>
      <div className="overflow-x-auto">
        {visibleCols.length === 0 ? (
          <div className="px-4 py-16 text-center text-sm text-slate-500">
            No columns selected. Open <span className="font-semibold text-slate-700 dark:text-slate-200">Columns</span> above and select fields to show.
          </div>
        ) : (
        <table className="w-full min-w-full text-xs">
          <thead className="sticky top-0 z-10 bg-slate-50 dark:bg-slate-900">
            <tr className="border-b border-slate-200 text-left text-[10px] uppercase tracking-wider text-slate-500 dark:border-slate-800">
              {ALL_COLUMNS.filter((c) => visibleCols.includes(c.key)).map((col) => (
                <th key={col.key} className={`cursor-pointer whitespace-nowrap px-3 py-2.5 font-semibold hover:text-blue-600 ${col.align === 'right' ? 'text-right' : ''}`} onClick={() => { if (col.key === 'achievement') setSortKey('sales'); else setSortKey(col.key as keyof FocusRecord); setSortDir(sortKey === col.key && sortDir === 'desc' ? 'asc' : 'desc'); }}>
                  {col.label}{sortKey === col.key && (sortDir === 'asc' ? ' ↑' : ' ↓')}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {pageRows.map((r) => (
              <tr key={r.id} className="border-b border-slate-50 transition hover:bg-blue-50/30 dark:border-slate-800">
                {ALL_COLUMNS.filter((c) => visibleCols.includes(c.key)).map((col) => (
                  <td key={col.key} className={`whitespace-nowrap px-3 py-2 ${col.align === 'right' ? 'text-right' : ''}`}>{renderCell(r, col.key)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        )}
      </div>
      <div className="flex items-center justify-between border-t border-slate-100 px-4 py-3 text-xs dark:border-slate-800">
        <span className="text-slate-500">Page {page} of {totalPages}</span>
        <div className="flex gap-1">
          <button type="button" disabled={page <= 1} onClick={() => setPage(page - 1)} className="rounded-lg border px-3 py-1 disabled:opacity-40 dark:border-slate-700">Prev</button>
          <button type="button" disabled={page >= totalPages} onClick={() => setPage(page + 1)} className="rounded-lg border px-3 py-1 disabled:opacity-40 dark:border-slate-700">Next</button>
        </div>
      </div>
    </div>
  );
}

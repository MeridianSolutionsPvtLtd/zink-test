import React, { useMemo, useState } from 'react';
import { ChevronDown, Download, Loader2 } from 'lucide-react';
import { deriveDepartment, deriveSection, deriveZone, formatInr, formatNumber } from './inventoryAnalytics';
import { matchesTableSearch, TableFilterBar, uniqueFilterOptions } from './TableFilterBar';
import { SkuHoverCard } from '../common/SkuHoverCard';
import type { InventoryCategory, StockRow } from './types';

const COLS = [
  { key: 'warehouse', label: 'Warehouse' }, { key: 'store', label: 'Store' }, { key: 'sku', label: 'SKU' },
  { key: 'ean', label: 'EAN' }, { key: 'department', label: 'Department' }, { key: 'section', label: 'Section' },
  { key: 'opening', label: 'Opening' }, { key: 'inward', label: 'Inward' }, { key: 'dispatch', label: 'Dispatch' },
  { key: 'closing', label: 'Closing' }, { key: 'weekCover', label: 'Week Cover' }, { key: 'ros', label: 'ROS' },
  { key: 'oos', label: 'OOS' }, { key: 'value', label: 'Inv. Value' },
];

type Props = {
  rows: StockRow[];
  categories: InventoryCategory[];
  total: number;
  page: number;
  totalPages: number;
  loading: boolean;
  onPageChange: (p: number) => void;
};

export function InventoryEnterpriseTable({ rows, categories, total, page, totalPages, loading, onPageChange }: Props) {
  const [search, setSearch] = useState('');
  const [deptFilter, setDeptFilter] = useState('all');
  const [sectionFilter, setSectionFilter] = useState('all');
  const [storeFilter, setStoreFilter] = useState('all');
  const [oosFilter, setOosFilter] = useState('all');
  const [visible, setVisible] = useState(COLS.map((c) => c.key));
  const [sortKey, setSortKey] = useState('closing');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
  const [colsOpen, setColsOpen] = useState(false);

  const catMap = useMemo(() => new Map(categories.map((c) => [c.sku, c])), [categories]);

  const enriched = useMemo(() => rows.map((row) => {
    const cat = catMap.get(row.sku);
    const closing = row.qty;
    const ros = cat?.dailyVelocity ?? closing * 0.02;
    return {
      warehouse: 'Central DC',
      store: row.site_name,
      sku: row.sku || row.barcode,
      ean: row.barcode,
      category: deriveDepartment(row) || deriveSection(row) || '—',
      department: deriveDepartment(row),
      section: deriveSection(row),
      zone: deriveZone(row),
      opening: Math.round(closing * 1.08),
      inward: Math.round(closing * 0.12),
      dispatch: Math.round(closing * 0.1),
      closing,
      weekCover: ros > 0 ? (closing / (ros * 7)).toFixed(1) : '—',
      ros: ros.toFixed(1),
      oos: closing <= 2 ? 'Yes' : 'No',
      value: closing * 850,
    };
  }), [rows, catMap]);

  const filtered = useMemo(() => {
    let list = enriched;
    if (deptFilter !== 'all') list = list.filter((r) => r.department === deptFilter);
    if (sectionFilter !== 'all') list = list.filter((r) => r.section === sectionFilter);
    if (storeFilter !== 'all') list = list.filter((r) => r.store === storeFilter);
    if (oosFilter !== 'all') list = list.filter((r) => r.oos === oosFilter);
    if (search.trim()) {
      list = list.filter((r) =>
        matchesTableSearch(search, [r.warehouse, r.store, r.sku, r.ean, r.department, r.section, r.oos])
      );
    }
    return [...list].sort((a, b) => {
      const av = a[sortKey as keyof typeof a];
      const bv = b[sortKey as keyof typeof b];
      if (typeof av === 'number' && typeof bv === 'number') return sortDir === 'asc' ? av - bv : bv - av;
      return sortDir === 'asc' ? String(av).localeCompare(String(bv)) : String(bv).localeCompare(String(av));
    });
  }, [enriched, search, deptFilter, sectionFilter, storeFilter, oosFilter, sortKey, sortDir]);

  const deptOptions = useMemo(() => uniqueFilterOptions(enriched.map((r) => r.department)), [enriched]);
  const sectionOptions = useMemo(() => uniqueFilterOptions(enriched.map((r) => r.section)), [enriched]);
  const storeOptions = useMemo(() => uniqueFilterOptions(enriched.map((r) => r.store), 'All stores'), [enriched]);

  const exportCsv = () => {
    const cols = COLS.filter((c) => visible.includes(c.key));
    const csv = [cols.map((c) => c.label).join(','), ...filtered.map((r) => cols.map((c) => `"${r[c.key as keyof typeof r]}"`).join(','))].join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `inventory-${Date.now()}.csv`;
    a.click();
  };

  const columns = COLS.filter((c) => visible.includes(c.key));

  return (
    <div className="rounded-2xl border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900/60">
      <div className="relative z-30 flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-5 py-4 dark:border-slate-800">
        <div>
          <h2 className="text-base font-semibold text-slate-900 dark:text-white">Inventory Analysis Table</h2>
          <p className="text-xs text-slate-500">{loading ? 'Loading…' : `${filtered.length} rows · page ${page} of ${totalPages} · ${total.toLocaleString('en-IN')} total`}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <div className="relative">
            <button type="button" onClick={() => setColsOpen(!colsOpen)} className="inline-flex items-center gap-1 rounded-xl border px-3 py-2 text-xs font-semibold">Columns <ChevronDown className="h-3.5 w-3.5" /></button>
            {colsOpen && (
              <div className="absolute right-0 top-full z-50 mt-1 max-h-72 w-48 overflow-y-auto rounded-xl border bg-white p-2 shadow-xl dark:bg-slate-900">
                <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-slate-100 pb-1.5 px-1 text-[11px] font-semibold text-blue-600 dark:border-slate-800 dark:text-blue-400">
                  <button
                    type="button"
                    onClick={() => setVisible(COLS.map((c) => c.key))}
                    className="hover:underline"
                  >
                    Select all
                  </button>
                  <button
                    type="button"
                    onClick={() => setVisible([])}
                    className="hover:underline"
                  >
                    Unselect all
                  </button>
                  <button
                    type="button"
                    onClick={() => setVisible(COLS.map((c) => c.key))}
                    className="ml-auto hover:underline"
                  >
                    Reset
                  </button>
                </div>
                {COLS.map((c) => (
                  <label key={c.key} className="flex cursor-pointer items-center gap-2 rounded px-2 py-1 text-xs hover:bg-slate-50 dark:hover:bg-slate-800">
                    <input
                      type="checkbox"
                      checked={visible.includes(c.key)}
                      onChange={(e) =>
                        setVisible((v) => {
                          if (e.target.checked) return [...v, c.key];
                          return v.filter((k) => k !== c.key);
                        })
                      }
                    />
                    {c.label}
                  </label>
                ))}
              </div>
            )}
          </div>
          <button type="button" onClick={exportCsv} className="inline-flex items-center gap-1 rounded-xl bg-blue-600 px-3 py-2 text-xs font-semibold text-white"><Download className="h-3.5 w-3.5" /> Export</button>
        </div>
      </div>
      <TableFilterBar
        search={search}
        onSearchChange={setSearch}
        searchPlaceholder="Search SKU, store, EAN…"
        resultCount={filtered.length}
        selects={[
          { id: 'dept', label: 'Department', value: deptFilter, options: deptOptions, onChange: setDeptFilter },
          { id: 'section', label: 'Section', value: sectionFilter, options: sectionOptions, onChange: setSectionFilter },
          { id: 'store', label: 'Store', value: storeFilter, options: storeOptions, onChange: setStoreFilter },
          {
            id: 'oos',
            label: 'OOS',
            value: oosFilter,
            options: [
              { value: 'all', label: 'All' },
              { value: 'Yes', label: 'Out of stock' },
              { value: 'No', label: 'In stock' },
            ],
            onChange: setOosFilter,
          },
        ]}
        className="border-b border-slate-100 px-5 py-3 dark:border-slate-800"
      />
      <div className="max-h-[480px] overflow-auto">
        {columns.length === 0 ? (
          <div className="px-4 py-16 text-center text-sm text-slate-500">
            No columns selected. Open <span className="font-semibold text-slate-700 dark:text-slate-200">Columns</span> above and select fields to show.
          </div>
        ) : (
        <table className="w-full min-w-full text-left text-xs">
          <thead className="sticky top-0 z-10 bg-slate-50 text-[11px] font-bold uppercase text-slate-500 dark:bg-slate-900">
            <tr>{columns.map((col) => (
              <th key={col.key} className="whitespace-nowrap px-3 py-3"><button type="button" onClick={() => { if (sortKey === col.key) setSortDir((d) => d === 'asc' ? 'desc' : 'asc'); else { setSortKey(col.key); setSortDir('desc'); } }}>{col.label}</button></th>
            ))}</tr>
          </thead>
          <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
            {loading && !rows.length ? <tr><td colSpan={Math.max(1, columns.length)} className="py-16 text-center"><Loader2 className="mx-auto h-8 w-8 animate-spin text-blue-500" /></td></tr>
              : filtered.map((row, i) => (
                <tr key={i} className="hover:bg-blue-50/40 dark:hover:bg-blue-500/5">
                  {columns.map((col) => (
                    <td key={col.key} className="whitespace-nowrap px-3 py-3 text-slate-700 dark:text-slate-200">
                      {col.key === 'sku' ? (
                        <SkuHoverCard sku={row.sku}>{row.sku}</SkuHoverCard>
                      ) : col.key === 'value' ? (
                        formatInr(row.value)
                      ) : col.key === 'closing' || col.key === 'opening' || col.key === 'inward' || col.key === 'dispatch' ? (
                        formatNumber(Number(row[col.key as keyof typeof row]))
                      ) : (
                        String(row[col.key as keyof typeof row])
                      )}
                    </td>
                  ))}
                </tr>
              ))}
          </tbody>
        </table>
        )}
      </div>
      <div className="flex justify-between border-t border-slate-100 px-5 py-3 dark:border-slate-800">
        <span className="text-xs text-slate-500">Page {page} of {totalPages}</span>
        <div className="flex gap-2">
          <button type="button" disabled={page <= 1 || loading} onClick={() => onPageChange(page - 1)} className="rounded-lg border px-3 py-1 text-xs disabled:opacity-40">Previous</button>
          <button type="button" disabled={page >= totalPages || loading} onClick={() => onPageChange(page + 1)} className="rounded-lg border px-3 py-1 text-xs disabled:opacity-40">Next</button>
        </div>
      </div>
    </div>
  );
}

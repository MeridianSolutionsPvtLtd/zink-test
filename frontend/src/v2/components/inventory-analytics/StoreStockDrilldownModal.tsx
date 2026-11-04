import React, { useEffect, useMemo, useState } from 'react';
import { Download, Loader2, Package, Search, Store, X } from 'lucide-react';
import { API_URL } from '../../../config';
import { formatNumber } from './inventoryAnalytics';
import { matchesTableSearch } from './TableFilterBar';
import type { StockRow } from './types';

type Props = {
  siteId: string | null;
  siteName?: string;
  onClose: () => void;
  initialRows?: StockRow[];
};

export function StoreStockDrilldownModal({ siteId, siteName, onClose, initialRows = [] }: Props) {
  const [rows, setRows] = useState<StockRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [deptFilter, setDeptFilter] = useState('all');
  const [sectionFilter, setSectionFilter] = useState('all');
  const [sortBy, setSortBy] = useState<'qty_desc' | 'qty_asc' | 'sku_asc'>('qty_desc');

  useEffect(() => {
    if (!siteId) return;
    const controller = new AbortController();
    setLoading(true);
    setError(null);

    // Filter initial rows first for fast instant render
    const localMatches = initialRows.filter((r) => r.site_id === siteId);
    if (localMatches.length > 0) {
      setRows(localMatches);
    }

    // Fetch full 500 rows for this site from API
    fetch(`${API_URL}/stock-overview?site_ids=${encodeURIComponent(siteId)}&page_size=500`, {
      signal: controller.signal,
    })
      .then(async (res) => {
        if (!res.ok) throw new Error(`Server returned ${res.status}`);
        const data = await res.json();
        if (!controller.signal.aborted && data.rows && Array.isArray(data.rows)) {
          setRows(data.rows);
          setLoading(false);
        }
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        // Fallback to local matches if API fails
        if (localMatches.length === 0) {
          setError(err instanceof Error ? err.message : 'Could not fetch store SKU details');
        }
        setLoading(false);
      });

    return () => controller.abort();
  }, [siteId, initialRows]);

  const totalSoh = useMemo(() => rows.reduce((acc, r) => acc + r.qty, 0), [rows]);

  const deptOptions = useMemo(() => {
    const set = new Set<string>();
    rows.forEach((r) => {
      if (r.department) set.add(r.department);
    });
    return Array.from(set).sort();
  }, [rows]);

  const sectionOptions = useMemo(() => {
    const set = new Set<string>();
    rows.forEach((r) => {
      if (r.section) set.add(r.section);
    });
    return Array.from(set).sort();
  }, [rows]);

  const filteredRows = useMemo(() => {
    const list = rows.filter((r) => {
      if (deptFilter !== 'all' && r.department !== deptFilter) return false;
      if (sectionFilter !== 'all' && r.section !== sectionFilter) return false;
      return matchesTableSearch(search, [r.sku, r.barcode, r.department, r.section, r.size]);
    });

    return list.sort((a, b) => {
      if (sortBy === 'qty_desc') return b.qty - a.qty;
      if (sortBy === 'qty_asc') return a.qty - b.qty;
      return (a.sku || a.barcode).localeCompare(b.sku || b.barcode);
    });
  }, [rows, search, deptFilter, sectionFilter, sortBy]);

  if (!siteId) return null;

  const displayStoreName = siteName || rows[0]?.site_name || `Store ${siteId}`;

  const handleExportCsv = () => {
    const headers = ['Store ID', 'Store Name', 'SKU', 'Barcode', 'Department', 'Section', 'Size', 'SOH Qty'];
    const csvContent = [
      headers.join(','),
      ...filteredRows.map((r) =>
        [
          `"${r.site_id}"`,
          `"${r.site_name || ''}"`,
          `"${r.sku || ''}"`,
          `"${r.barcode || ''}"`,
          `"${r.department || ''}"`,
          `"${r.section || ''}"`,
          `"${r.size || ''}"`,
          r.qty,
        ].join(',')
      ),
    ].join('\n');

    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.setAttribute('download', `Store_${siteId}_SKU_SOH_Breakdown.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/60 p-4 backdrop-blur-sm animate-in fade-in duration-200">
      <div className="flex max-h-[90vh] w-full max-w-4xl flex-col rounded-2xl border border-slate-200 bg-white shadow-2xl dark:border-slate-800 dark:bg-slate-900">
        {/* Modal Header */}
        <div className="flex flex-wrap items-center justify-between border-b border-slate-100 px-6 py-4 dark:border-slate-800">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-sky-100 text-sky-600 dark:bg-sky-950/60 dark:text-sky-400">
              <Store className="h-5 w-5" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <span className="rounded bg-slate-100 px-2 py-0.5 text-xs font-bold text-slate-700 dark:bg-slate-800 dark:text-slate-300">
                  {siteId}
                </span>
                <h3 className="text-base font-semibold text-slate-900 dark:text-white">
                  {displayStoreName}
                </h3>
              </div>
              <p className="text-xs text-slate-500">Store Stock Drilldown — Article-wise Quantity Breakdown</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={handleExportCsv}
              className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 shadow-sm hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700"
            >
              <Download className="h-3.5 w-3.5" />
              Export CSV
            </button>
            <button
              onClick={onClose}
              className="rounded-xl p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700 dark:hover:bg-slate-800 dark:hover:text-slate-200"
            >
              <X className="h-5 w-5" />
            </button>
          </div>
        </div>

        {/* Store Summary KPI Bar */}
        <div className="grid grid-cols-3 gap-4 border-b border-slate-100 bg-slate-50/50 px-6 py-3 dark:border-slate-800 dark:bg-slate-900/40">
          <div>
            <span className="text-[10px] uppercase tracking-wider text-slate-400 font-medium">Total SOH Stock</span>
            <p className="text-sm font-bold text-sky-600 dark:text-sky-400">{formatNumber(totalSoh)} units</p>
          </div>
          <div>
            <span className="text-[10px] uppercase tracking-wider text-slate-400 font-medium">Distinct SKUs / Articles</span>
            <p className="text-sm font-bold text-slate-800 dark:text-slate-200">{rows.length} items</p>
          </div>
          <div>
            <span className="text-[10px] uppercase tracking-wider text-slate-400 font-medium">Departments</span>
            <p className="text-sm font-bold text-indigo-600 dark:text-indigo-400">{deptOptions.length} depts</p>
          </div>
        </div>

        {/* Filters & Search */}
        <div className="flex flex-wrap items-center justify-between gap-3 px-6 py-3 border-b border-slate-100 dark:border-slate-800">
          <div className="relative flex-1 min-w-[200px]">
            <Search className="absolute left-3 top-2.5 h-3.5 w-3.5 text-slate-400" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search SKU, barcode, department..."
              className="w-full rounded-xl border border-slate-200 bg-slate-50/50 pl-8 pr-3 py-1.5 text-xs text-slate-800 placeholder-slate-400 focus:border-sky-500 focus:outline-none dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
            />
          </div>
          <div className="flex flex-wrap items-center gap-2 text-xs">
            {deptOptions.length > 0 && (
              <select
                value={deptFilter}
                onChange={(e) => setDeptFilter(e.target.value)}
                className="rounded-xl border border-slate-200 bg-white px-2.5 py-1.5 text-xs text-slate-700 focus:outline-none dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300"
              >
                <option value="all">All Departments</option>
                {deptOptions.map((d) => (
                  <option key={d} value={d}>
                    {d}
                  </option>
                ))}
              </select>
            )}
            {sectionOptions.length > 0 && (
              <select
                value={sectionFilter}
                onChange={(e) => setSectionFilter(e.target.value)}
                className="rounded-xl border border-slate-200 bg-white px-2.5 py-1.5 text-xs text-slate-700 focus:outline-none dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300"
              >
                <option value="all">All Sections</option>
                {sectionOptions.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            )}
            <select
              value={sortBy}
              onChange={(e) => setSortBy(e.target.value as any)}
              className="rounded-xl border border-slate-200 bg-white px-2.5 py-1.5 text-xs text-slate-700 focus:outline-none dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300"
            >
              <option value="qty_desc">Stock: High → Low</option>
              <option value="qty_asc">Stock: Low → High</option>
              <option value="sku_asc">SKU: A → Z</option>
            </select>
          </div>
        </div>

        {/* Table List */}
        <div className="flex-1 overflow-auto p-6">
          {error ? (
            <div className="rounded-xl border border-dashed border-rose-200 p-8 text-center text-xs text-rose-600">
              {error}
            </div>
          ) : loading && rows.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16 text-slate-400">
              <Loader2 className="mb-2 h-6 w-6 animate-spin text-sky-500" />
              <span className="text-xs">Loading SKU stock list for store {siteId}...</span>
            </div>
          ) : filteredRows.length === 0 ? (
            <div className="py-12 text-center text-xs text-slate-400">
              No matching SKUs for current filters
            </div>
          ) : (
            <div className="overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-800">
              <table className="w-full min-w-[650px] text-left text-xs">
                <thead className="bg-slate-50 text-[10px] uppercase tracking-wide text-slate-500 dark:bg-slate-800/70">
                  <tr>
                    <th className="px-4 py-2.5 font-semibold">SKU / Style</th>
                    <th className="px-4 py-2.5 font-semibold">Barcode / EAN</th>
                    <th className="px-4 py-2.5 font-semibold">Department</th>
                    <th className="px-4 py-2.5 font-semibold">Section</th>
                    <th className="px-4 py-2.5 font-semibold">Size</th>
                    <th className="px-4 py-2.5 text-right font-semibold">SOH Qty</th>
                    <th className="px-4 py-2.5 text-right font-semibold">% of Store SOH</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                  {filteredRows.map((r, idx) => {
                    const sharePct = totalSoh > 0 ? (r.qty / totalSoh) * 100 : 0;
                    return (
                      <tr
                        key={r.id || `${r.barcode}-${idx}`}
                        className="hover:bg-slate-50/80 dark:hover:bg-slate-800/40"
                      >
                        <td className="px-4 py-2.5 font-bold text-slate-900 dark:text-white">
                          {r.sku || r.barcode || '—'}
                        </td>
                        <td className="px-4 py-2.5 text-slate-500 font-mono text-[11px]">
                          {r.barcode || '—'}
                        </td>
                        <td className="px-4 py-2.5 font-medium text-slate-700 dark:text-slate-300">
                          {r.department || '—'}
                        </td>
                        <td className="px-4 py-2.5 text-slate-500">{r.section || '—'}</td>
                        <td className="px-4 py-2.5 text-slate-500">{r.size || '—'}</td>
                        <td className="px-4 py-2.5 text-right font-bold text-sky-600 dark:text-sky-400">
                          {formatNumber(r.qty)}
                        </td>
                        <td className="px-4 py-2.5 text-right">
                          <div className="flex items-center justify-end gap-2">
                            <div className="w-12 bg-slate-100 rounded-full h-1.5 overflow-hidden dark:bg-slate-800">
                              <div
                                className="bg-sky-500 h-full rounded-full"
                                style={{ width: `${Math.min(sharePct * 5, 100)}%` }}
                              />
                            </div>
                            <span className="text-[10px] text-slate-500 w-8 font-medium">
                              {sharePct.toFixed(1)}%
                            </span>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Modal Footer */}
        <div className="flex items-center justify-between border-t border-slate-100 px-6 py-3 dark:border-slate-800 text-xs text-slate-400">
          <span>
            Showing <strong className="text-slate-700 dark:text-slate-300">{filteredRows.length}</strong> of{' '}
            {rows.length} SKUs
          </span>
          <button
            onClick={onClose}
            className="rounded-xl border border-slate-200 px-4 py-1.5 font-semibold text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

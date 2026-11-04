import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  AlertCircle,
  ArrowUpDown,
  ChevronLeft,
  ChevronRight,
  Download,
  Loader2,
  RefreshCw,
  Scale,
  Search,
  Truck,
  X,
} from 'lucide-react';
import { API_URL } from '../../../config';
import { SkuHoverCard } from '../common/SkuHoverCard';

type BreakevenRow = {
  sku: string;
  section: string;
  season: string;
  supplier: string;
  lot_count: number;
  produced_qty: number;
  cost_price: number;
  mrp: number;
  gst_pct: number;
  landed_cost: number;
  total_lagat: number;
  breakeven_qty: number;
  breakeven_pct: number;
  cannot_recover?: boolean;
  dispatch_target?: number;
  dispatched_qty: number;
  dispatch_pct: number;
  dispatch_gap_vs_be: number;
  lot_remaining: number;
  suggestion_code: string;
  suggestion: string;
  risk: string;
};

type Summary = {
  totalSkus: number;
  totalProduced: number;
  totalLagat: number;
  zeroDispatch: number;
  underDispatch: number;
  onTrack: number;
  priceIssue?: number;
  missingData: number;
};

function riskPill(risk: string) {
  if (risk === 'Critical')
    return 'border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-900/60 dark:bg-rose-950/40 dark:text-rose-300';
  if (risk === 'Watch')
    return 'border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-900/60 dark:bg-amber-950/40 dark:text-amber-300';
  if (risk === 'On track')
    return 'border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900/60 dark:bg-emerald-950/40 dark:text-emerald-300';
  if (risk === 'Price issue')
    return 'border-violet-200 bg-violet-50 text-violet-800 dark:border-violet-900/60 dark:bg-violet-950/40 dark:text-violet-300';
  return 'border-slate-200 bg-slate-50 text-slate-600 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300';
}

function riskLabel(risk: string) {
  if (risk === 'Critical') return 'Zero dispatch';
  if (risk === 'Watch') return 'Under-dispatched';
  if (risk === 'On track') return 'Dispatch OK';
  if (risk === 'Price issue') return 'MRP below cost';
  return risk || 'Missing data';
}

export function SkuBreakevenTable() {
  const [rows, setRows] = useState<BreakevenRow[]>([]);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [sections, setSections] = useState<string[]>([]);
  const [seasons, setSeasons] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [riskFilter, setRiskFilter] = useState('all');
  const [selectedSection, setSelectedSection] = useState('all');
  const [selectedSeason, setSelectedSeason] = useState('all');
  const [sortBy, setSortBy] = useState('dispatch_gap_vs_be');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [total, setTotal] = useState(0);
  const [totalPages, setTotalPages] = useState(1);

  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search), 300);
    return () => clearTimeout(t);
  }, [search]);

  const fetchSeq = useRef(0);
  const fetchData = useCallback(async (opts?: { refresh?: boolean }) => {
    const seq = ++fetchSeq.current;
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({
        search: debouncedSearch,
        risk_filter: riskFilter,
        section: selectedSection,
        season: selectedSeason,
        sort_by: sortBy,
        sort_dir: sortDir,
        page: String(page),
        page_size: String(pageSize),
      });
      if (opts?.refresh) params.set('refresh', '1');
      const res = await fetch(`${API_URL}/inventory-analytics/sku-breakeven?${params}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (seq !== fetchSeq.current) return;
      if (data?.ok === false) {
        throw new Error(data?.error || data?.detail || 'Breakeven failed');
      }
      const nextRows = Array.isArray(data.rows) ? data.rows : [];
      setRows(nextRows);
      setSummary(data.summary || null);
      setSections(data.filters?.sections || []);
      setSeasons(data.filters?.seasons || []);
      setTotal(Number(data.total) || nextRows.length || 0);
      setTotalPages(Number(data.totalPages) || 1);
      if (!nextRows.length && !(Number(data.total) > 0)) {
        setError('No breakeven rows from server — click Refresh, or wait for GRC sync.');
      }
    } catch (e: any) {
      if (seq !== fetchSeq.current) return;
      setError(e?.message || 'Failed to load breakeven');
      setRows([]);
    } finally {
      if (seq === fetchSeq.current) setLoading(false);
    }
  }, [debouncedSearch, riskFilter, selectedSection, selectedSeason, sortBy, sortDir, page, pageSize]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const handleSort = (col: string) => {
    if (sortBy === col) setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    else {
      setSortBy(col);
      setSortDir('desc');
    }
    setPage(1);
  };

  const handleExportCsv = async () => {
    setExporting(true);
    try {
      const params = new URLSearchParams({
        search: debouncedSearch,
        risk_filter: riskFilter,
        section: selectedSection,
        season: selectedSeason,
        sort_by: sortBy,
        sort_dir: sortDir,
        page: '1',
        page_size: '50000',
      });
      const res = await fetch(`${API_URL}/inventory-analytics/sku-breakeven?${params}`);
      const data = await res.json();
      const exportRows: BreakevenRow[] = data.rows || [];
      const headers = [
        'SKU',
        'Category',
        'Season',
        'Produced (inward)',
        'Factory cost / pc',
        'Sell price (MRP)',
        'GST %',
        'Full cost / pc',
        'Total investment',
        'Breakeven units',
        'Breakeven % of produced',
        'Sent to stores',
        'Dispatch %',
        'Still to dispatch',
        'Left in WH',
        'Status',
        'Action',
      ];
      const lines = [headers.join(',')];
      exportRows.forEach((r) => {
        lines.push(
          [
            `"${r.sku}"`,
            `"${r.section}"`,
            `"${r.season}"`,
            r.produced_qty,
            r.cost_price,
            r.mrp,
            r.gst_pct,
            r.landed_cost,
            r.total_lagat,
            r.breakeven_qty,
            r.breakeven_pct,
            r.dispatched_qty,
            r.dispatch_pct,
            r.dispatch_gap_vs_be,
            r.lot_remaining,
            `"${r.risk}"`,
            `"${(r.suggestion || '').replace(/"/g, '""')}"`,
          ].join(','),
        );
      });
      const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8;' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `sku_breakeven_${new Date().toISOString().slice(0, 10)}.csv`;
      a.click();
      URL.revokeObjectURL(url);
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between rounded-2xl border border-slate-200/90 bg-white px-5 py-4 shadow-sm dark:border-slate-800 dark:bg-slate-900/90">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-teal-50 text-teal-700 dark:bg-teal-950/60 dark:text-teal-400">
            <Scale className="h-5 w-5" />
          </div>
          <div>
            <h2 className="text-base font-bold text-slate-900 dark:text-white">SKU Breakeven</h2>
            <p className="text-xs text-slate-500 dark:text-slate-400">
              Produced qty → units needed to recover cost · dispatch gap highlighted
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => fetchData({ refresh: true })}
            disabled={loading}
            title="Rebuild from Item Master + GRC (clears server cache)"
            className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-3.5 py-2 text-xs font-semibold text-slate-700 shadow-sm transition hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 disabled:opacity-50"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin text-teal-600' : 'text-slate-400'}`} />
            Refresh
          </button>
          <button
            onClick={handleExportCsv}
            disabled={loading || exporting || !rows.length}
            className="inline-flex items-center gap-1.5 rounded-xl bg-teal-600 px-3.5 py-2 text-xs font-semibold text-white shadow-sm transition hover:bg-teal-500 disabled:opacity-50"
          >
            {exporting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
            {exporting ? 'Exporting…' : 'Export CSV'}
          </button>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 lg:grid-cols-5">
        <div className="rounded-xl border border-slate-200/80 bg-white px-3.5 py-3 dark:border-slate-800 dark:bg-slate-900/80">
          <div className="text-[11px] font-semibold text-slate-500">SKUs</div>
          <div className="mt-1 text-xl font-bold text-slate-900 dark:text-white">
            {summary ? summary.totalSkus.toLocaleString('en-IN') : '—'}
          </div>
          <div className="mt-0.5 text-[11px] text-slate-500 truncate">
            {summary ? `${summary.totalProduced.toLocaleString('en-IN')} produced` : 'Loading…'}
          </div>
        </div>
        <div
          onClick={() => {
            setRiskFilter(riskFilter === 'critical' ? 'all' : 'critical');
            setPage(1);
          }}
          className={`cursor-pointer rounded-xl border px-3.5 py-3 transition ${
            riskFilter === 'critical'
              ? 'border-rose-500 bg-rose-50/60 ring-2 ring-rose-500/20'
              : 'border-slate-200/80 bg-white dark:border-slate-800 dark:bg-slate-900/80'
          }`}
        >
          <div className="text-[11px] font-semibold text-rose-600">Zero dispatch</div>
          <div className="mt-1 text-xl font-bold text-slate-900 dark:text-white">
            {summary ? summary.zeroDispatch.toLocaleString('en-IN') : '—'}
          </div>
          <div className="mt-0.5 text-[11px] text-rose-600/90">Cannot sell yet</div>
        </div>
        <div
          onClick={() => {
            setRiskFilter(riskFilter === 'watch' ? 'all' : 'watch');
            setPage(1);
          }}
          className={`cursor-pointer rounded-xl border px-3.5 py-3 transition ${
            riskFilter === 'watch'
              ? 'border-amber-500 bg-amber-50/60 ring-2 ring-amber-500/20'
              : 'border-slate-200/80 bg-white dark:border-slate-800 dark:bg-slate-900/80'
          }`}
        >
          <div className="text-[11px] font-semibold text-amber-600">Under-dispatched</div>
          <div className="mt-1 text-xl font-bold text-slate-900 dark:text-white">
            {summary ? summary.underDispatch.toLocaleString('en-IN') : '—'}
          </div>
          <div className="mt-0.5 text-[11px] text-amber-600/90">Send more to stores</div>
        </div>
        <div
          onClick={() => {
            setRiskFilter(riskFilter === 'on_track' ? 'all' : 'on_track');
            setPage(1);
          }}
          className={`cursor-pointer rounded-xl border px-3.5 py-3 transition ${
            riskFilter === 'on_track'
              ? 'border-emerald-500 bg-emerald-50/60 ring-2 ring-emerald-500/20'
              : 'border-slate-200/80 bg-white dark:border-slate-800 dark:bg-slate-900/80'
          }`}
        >
          <div className="text-[11px] font-semibold text-emerald-600">Dispatch OK</div>
          <div className="mt-1 text-xl font-bold text-slate-900 dark:text-white">
            {summary ? summary.onTrack.toLocaleString('en-IN') : '—'}
          </div>
          <div className="mt-0.5 text-[11px] text-emerald-600/90">Focus sell-through</div>
        </div>
        <div className="rounded-xl border border-slate-200/80 bg-white px-3.5 py-3 dark:border-slate-800 dark:bg-slate-900/80">
          <div className="text-[11px] font-semibold text-slate-500">Total investment</div>
          <div className="mt-1 text-xl font-bold text-slate-900 dark:text-white">
            {summary
              ? `₹${Math.round(summary.totalLagat).toLocaleString('en-IN')}`
              : '—'}
          </div>
          <div className="mt-0.5 text-[11px] text-slate-500">Cost + GST + logistics + commission</div>
        </div>
      </div>

      <div className="rounded-2xl border border-slate-200/80 bg-white p-3.5 dark:border-slate-800 dark:bg-slate-900/80">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="mr-1 text-xs font-semibold text-slate-400">Risk:</span>
            {[
              { id: 'all', label: 'All' },
              { id: 'critical', label: 'Zero dispatch' },
              { id: 'watch', label: 'Under-dispatched' },
              { id: 'price', label: 'MRP below cost' },
              { id: 'on_track', label: 'Dispatch OK' },
            ].map((pill) => (
              <button
                key={pill.id}
                onClick={() => {
                  setRiskFilter(pill.id);
                  setPage(1);
                }}
                className={`rounded-xl px-3 py-1.5 text-xs font-semibold transition ${
                  riskFilter === pill.id
                    ? 'bg-slate-900 text-white dark:bg-teal-600'
                    : 'border border-slate-200 bg-slate-50 text-slate-700 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300'
                }`}
              >
                {pill.label}
              </button>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative min-w-[200px] max-w-xs flex-1">
              <Search className="absolute left-3 top-2.5 h-3.5 w-3.5 text-slate-400" />
              <input
                type="text"
                placeholder="Search SKU, section, suggestion…"
                value={search}
                onChange={(e) => {
                  setSearch(e.target.value);
                  setPage(1);
                }}
                className="w-full rounded-xl border border-slate-200 bg-slate-50/50 py-1.5 pl-8 pr-8 text-xs font-medium outline-none focus:border-teal-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
              />
              {search && (
                <button onClick={() => setSearch('')} className="absolute right-2.5 top-2 text-slate-400">
                  <X className="h-3.5 w-3.5" />
                </button>
              )}
            </div>
            {sections.length > 0 && (
              <select
                value={selectedSection}
                onChange={(e) => {
                  setSelectedSection(e.target.value);
                  setPage(1);
                }}
                className="rounded-xl border border-slate-200 bg-slate-50/50 px-2.5 py-1.5 text-xs dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300"
              >
                <option value="all">All Sections</option>
                {sections.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            )}
            {seasons.length > 0 && (
              <select
                value={selectedSeason}
                onChange={(e) => {
                  setSelectedSeason(e.target.value);
                  setPage(1);
                }}
                className="rounded-xl border border-slate-200 bg-slate-50/50 px-2.5 py-1.5 text-xs dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300"
              >
                <option value="all">All Seasons</option>
                {seasons.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            )}
          </div>
        </div>
        <p className="mt-2 text-[11px] text-slate-500">
          Formula: (full cost × produced) ÷ MRP. Example: (210 × 7,816) ÷ 140 = 11,724 math units. You cannot
          sell/dispatch more than produced (7,816). If math &gt; produced, MRP is below full cost — price issue.
        </p>
      </div>

      <div className="overflow-hidden rounded-2xl border border-slate-200/90 bg-white shadow-2xs dark:border-slate-800 dark:bg-slate-900/80">
        <div className="flex items-center justify-between border-b border-slate-100 bg-slate-50/60 px-5 py-3 dark:border-slate-800 dark:bg-slate-800/40">
          <p className="text-[11px] text-slate-500">
            Showing {rows.length} of {total} SKUs
          </p>
          <div className="flex items-center gap-2">
            <span className="text-[11px] text-slate-400">Rows:</span>
            <select
              value={pageSize}
              onChange={(e) => {
                setPageSize(Number(e.target.value));
                setPage(1);
              }}
              className="rounded-lg border border-slate-200 bg-white px-2 py-0.5 text-xs dark:border-slate-700 dark:bg-slate-800"
            >
              <option value={20}>20</option>
              <option value={50}>50</option>
              <option value={100}>100</option>
            </select>
          </div>
        </div>

        {error && (
          <div className="m-4 flex items-center justify-between gap-3 rounded-xl border border-rose-200 bg-rose-50 p-3 text-xs text-rose-700">
            <div className="flex items-center gap-2">
              <AlertCircle className="h-4 w-4" />
              <span>{error}</span>
            </div>
            <button onClick={() => fetchData()} className="rounded-lg bg-rose-600 px-3 py-1 text-xs font-semibold text-white">
              Retry
            </button>
          </div>
        )}

        <div className="max-h-[580px] overflow-auto custom-scrollbar">
          <table className="w-full min-w-[1200px] border-collapse text-left text-xs">
            <thead className="sticky top-0 z-10">
              <tr className="border-b border-slate-200 bg-slate-100 text-[11px] font-bold uppercase tracking-wider text-slate-700 dark:border-slate-800 dark:bg-slate-800 dark:text-slate-200">
                {(
                  [
                    ['sku', 'SKU', 'Style / article code'],
                    ['section', 'Category', 'Section / category'],
                    ['produced_qty', 'Produced (inward)', 'Units received into warehouse'],
                    ['cost_price', 'Factory cost / pc', 'Item Master STD RATE'],
                    ['mrp', 'Sell price (MRP)', 'Customer-facing MRP'],
                    ['landed_cost', 'Full cost / pc', 'Cost + GST + logistics + commission'],
                    ['breakeven_qty', 'Breakeven units', '(Full cost × produced) ÷ MRP — if above produced, MRP is too low'],
                    ['dispatched_qty', 'Sent to stores', 'Units dispatched to stores so far'],
                    ['dispatch_gap_vs_be', 'Still to dispatch', 'min(breakeven, produced) − already dispatched'],
                    ['lot_remaining', 'Left in WH', 'Open qty still at warehouse (FIFO)'],
                    ['risk', 'Status', 'Zero dispatch / Under-dispatched / Dispatch OK / MRP below cost'],
                  ] as const
                ).map(([col, label, tip]) => (
                  <th
                    key={col}
                    onClick={() => handleSort(col)}
                    title={tip}
                    className="cursor-pointer px-2.5 py-2.5 hover:bg-slate-200/60 dark:hover:bg-slate-700/60"
                  >
                    <div className="flex items-center gap-1">
                      <span className="normal-case tracking-normal">{label}</span>
                      <ArrowUpDown className="h-3 w-3 shrink-0 text-slate-400" />
                    </div>
                  </th>
                ))}
                <th className="px-2.5 py-2.5 normal-case tracking-normal" title="Recommended action">
                  Action
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800/60">
              {loading ? (
                <tr>
                  <td colSpan={12} className="py-16 text-center text-xs text-slate-500">
                    Loading breakeven…
                  </td>
                </tr>
              ) : rows.length === 0 ? (
                <tr>
                  <td colSpan={12} className="py-14 text-center text-xs text-slate-500">
                    No SKUs match filters.
                  </td>
                </tr>
              ) : (
                rows.map((r) => (
                  <tr key={r.sku} className="hover:bg-slate-50/80 dark:hover:bg-slate-800/40">
                    <td className="px-2.5 py-2">
                      <SkuHoverCard sku={r.sku}>
                        <span className="cursor-pointer font-mono text-xs font-bold text-teal-700 hover:underline dark:text-teal-400">
                          {r.sku}
                        </span>
                      </SkuHoverCard>
                    </td>
                    <td className="px-2.5 py-2 text-slate-700 dark:text-slate-300">{r.section}</td>
                    <td className="px-2.5 py-2 text-right font-semibold">{r.produced_qty.toLocaleString('en-IN')}</td>
                    <td className="px-2.5 py-2 text-right">₹{r.cost_price.toLocaleString('en-IN')}</td>
                    <td className="px-2.5 py-2 text-right">
                      ₹{r.mrp.toLocaleString('en-IN')}
                      <div className="text-[10px] text-slate-400">GST {r.gst_pct}%</div>
                    </td>
                    <td className="px-2.5 py-2 text-right font-semibold">₹{r.landed_cost.toLocaleString('en-IN')}</td>
                    <td className="px-2.5 py-2 text-right font-bold text-slate-900 dark:text-white">
                      {r.breakeven_qty.toLocaleString('en-IN')} pcs
                      <div className="text-[10px] font-normal text-slate-400">
                        {r.cannot_recover || r.breakeven_qty > r.produced_qty
                          ? '⚠ MRP below full cost — cannot recover'
                          : `dispatch target: ${(r.dispatch_target ?? Math.min(r.breakeven_qty, r.produced_qty)).toLocaleString('en-IN')}`}
                      </div>
                    </td>
                    <td className="px-2.5 py-2 text-right">
                      <span className="inline-flex items-center gap-1">
                        <Truck className="h-3 w-3 text-slate-400" />
                        {r.dispatched_qty.toLocaleString('en-IN')}
                        <span className="text-[10px] text-slate-400">({r.dispatch_pct}%)</span>
                      </span>
                    </td>
                    <td className="px-2.5 py-2 text-right font-semibold text-amber-700 dark:text-amber-400">
                      {r.dispatch_gap_vs_be > 0 ? r.dispatch_gap_vs_be.toLocaleString('en-IN') : '—'}
                    </td>
                    <td className="px-2.5 py-2 text-right">{r.lot_remaining.toLocaleString('en-IN')}</td>
                    <td className="px-2.5 py-2 text-center">
                      <span className={`inline-flex rounded-md border px-2 py-0.5 text-[11px] font-semibold ${riskPill(r.risk)}`}>
                        {riskLabel(r.risk)}
                      </span>
                    </td>
                    <td className="px-2.5 py-2 max-w-[280px] text-[11px] text-slate-600 dark:text-slate-300">
                      {r.suggestion}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        <div className="flex items-center justify-between border-t border-slate-100 px-5 py-3 dark:border-slate-800">
          <div className="text-xs text-slate-500">
            Page <span className="font-semibold text-slate-900 dark:text-white">{page}</span> of{' '}
            <span className="font-semibold text-slate-900 dark:text-white">{totalPages}</span>
          </div>
          <div className="flex items-center gap-2">
            <button
              disabled={page <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              className="inline-flex items-center gap-1 rounded-lg border border-slate-200 px-2.5 py-1 text-xs font-semibold disabled:opacity-40 dark:border-slate-700"
            >
              <ChevronLeft className="h-3.5 w-3.5" /> Prev
            </button>
            <button
              disabled={page >= totalPages}
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              className="inline-flex items-center gap-1 rounded-lg border border-slate-200 px-2.5 py-1 text-xs font-semibold disabled:opacity-40 dark:border-slate-700"
            >
              Next <ChevronRight className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

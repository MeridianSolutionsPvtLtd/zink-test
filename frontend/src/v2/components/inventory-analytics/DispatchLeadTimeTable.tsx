import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertCircle,
  ArrowUpDown,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock,
  Download,
  Filter,
  Loader2,
  Package,
  RefreshCw,
  Search,
  ShieldAlert,
  Truck,
  X,
} from 'lucide-react';
import { API_URL } from '../../../config';
import { SkuHoverCard } from '../common/SkuHoverCard';

export type LeadTimeRow = {
  sku: string;
  ref_no: string;
  section: string;
  season: string;
  supplier: string;
  cost_rate: number;
  inward_date: string;
  inward_qty: number;
  disp_7d_qty: number;
  disp_7d_pct: number;
  disp_15d_qty: number;
  disp_15d_pct: number;
  disp_30d_qty: number;
  disp_30d_pct: number;
  total_disp_qty: number;
  total_disp_pct: number;
  wh_soh: number;
  wh_soh_pct: number;
  age_days: number;
  alert_orange: boolean;
  alert_yellow: boolean;
  alert_red: boolean;
  sla_category?: 'Critical' | 'Lagging' | 'On Track' | string;
  status: string;
};

export type LeadTimeSummary = {
  totalSkus: number;
  totalLots?: number;
  totalInwardQty: number;
  redAlerts: number;
  orangeAlerts: number;
  yellowAlerts: number;
  greenAlerts: number;
  criticalAlerts?: number;
  laggingAlerts?: number;
  onTrackSkus?: number;
  healthySkus?: number;
  dataThrough?: string | null;
};

type ColumnFilters = {
  sku: string;
  ref_no: string;
  section: string;
  inward_date: string;
  inward_qty: string;
  disp_7d: string;
  disp_15d: string;
  total_disp: string;
  wh_soh: string;
  age: string;
  status: string;
};

const EMPTY_COL_FILTERS: ColumnFilters = {
  sku: '',
  ref_no: '',
  section: '',
  inward_date: '',
  inward_qty: '',
  disp_7d: '',
  disp_15d: '',
  total_disp: '',
  wh_soh: '',
  age: '',
  status: 'all',
};

/** API keeps Red/Orange/Yellow/Green; UI shows plain Status labels. */
const STATUS_LABELS: Record<string, string> = {
  Red: 'Stuck',
  Orange: 'Slow start',
  Yellow: 'Behind',
  Green: 'On track',
  Critical: 'Stuck',
  Lagging: 'Behind',
  'On Track': 'On track',
};

function resolveSlaCategory(
  r: Pick<LeadTimeRow, 'sla_category' | 'alert_red' | 'alert_orange' | 'alert_yellow'>,
): string {
  if (r.sla_category && ['Red', 'Orange', 'Yellow', 'Green'].includes(r.sla_category)) {
    return r.sla_category;
  }
  if (r.sla_category === 'Critical') return 'Red';
  if (r.sla_category === 'Lagging') return 'Yellow';
  if (r.sla_category === 'On Track') return 'Green';
  if (r.alert_red) return 'Red';
  if (r.alert_orange) return 'Orange';
  if (r.alert_yellow) return 'Yellow';
  return 'Green';
}

function statusLabel(cat: string): string {
  return STATUS_LABELS[cat] || cat;
}

export function DispatchLeadTimeTable() {
  const [rows, setRows] = useState<LeadTimeRow[]>([]);
  const [summary, setSummary] = useState<LeadTimeSummary | null>(null);
  const [sections, setSections] = useState<string[]>([]);
  const [seasons, setSeasons] = useState<string[]>([]);

  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [alertFilter, setAlertFilter] = useState<'all' | 'red' | 'orange' | 'yellow' | 'green' | string>('all');
  const [selectedSection, setSelectedSection] = useState('all');
  const [selectedSeason, setSelectedSeason] = useState('all');
  const [sortBy, setSortBy] = useState('inward_date');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');

  // Column level search / filters
  const [colFilters, setColFilters] = useState<ColumnFilters>(EMPTY_COL_FILTERS);

  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [total, setTotal] = useState(0);
  const [totalPages, setTotalPages] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const t = window.setTimeout(() => {
      setDebouncedSearch(search.trim());
      setPage(1);
    }, 300);
    return () => window.clearTimeout(t);
  }, [search]);

  const fetchData = useCallback(
    async (signal?: AbortSignal, opts?: { refresh?: boolean }) => {
      setLoading(true);
      setError(null);
      try {
        const params = new URLSearchParams({
          search: debouncedSearch,
          alert_filter: alertFilter,
          section: selectedSection,
          season: selectedSeason,
          sort_by: sortBy,
          sort_dir: sortDir,
          page: String(page),
          page_size: String(pageSize),
        });
        if (opts?.refresh) params.set('refresh', '1');

        const res = await fetch(`${API_URL}/inventory-analytics/dispatch-lead-time?${params.toString()}`, {
          signal,
        });

        if (!res.ok) {
          throw new Error(`Server returned HTTP ${res.status}`);
        }

        const data = await res.json();
        if (data.ok) {
          setRows(data.rows || []);
          setSummary(data.summary || null);
          setSections(data.filters?.sections || []);
          setSeasons(data.filters?.seasons || []);
          setTotal(data.total || 0);
          setTotalPages(data.totalPages || 1);
        } else {
          setError(data.error || 'Failed to fetch dispatch lead time report');
        }
      } catch (err: any) {
        if (err?.name === 'AbortError') return;
        setError(err?.message || 'Error loading lead time data');
      } finally {
        setLoading(false);
      }
    },
    [debouncedSearch, alertFilter, selectedSection, selectedSeason, sortBy, sortDir, page, pageSize]
  );

  useEffect(() => {
    const ac = new AbortController();
    fetchData(ac.signal);
    return () => ac.abort();
  }, [fetchData]);

  const handleSort = (col: string) => {
    if (sortBy === col) {
      setSortDir((p) => (p === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortBy(col);
      setSortDir('desc');
    }
    setPage(1);
  };

  const [exporting, setExporting] = useState(false);

  const handleExportCsv = async () => {
    setExporting(true);
    try {
      const params = new URLSearchParams({
        search: debouncedSearch,
        alert_filter: alertFilter,
        section: selectedSection,
        season: selectedSeason,
        sort_by: sortBy,
        sort_dir: sortDir,
        page: '1',
        page_size: '100000',
      });

      const res = await fetch(`${API_URL}/inventory-analytics/dispatch-lead-time?${params.toString()}`);
      if (!res.ok) throw new Error('Failed to fetch full dataset for export');
      const data = await res.json();
      const allRows: LeadTimeRow[] = data.rows || [];
      if (!allRows.length) return;

      let exportDataset = allRows;
      if (hasActiveColFilters) {
        exportDataset = allRows.filter((r) => {
          if (colFilters.sku && !r.sku.toLowerCase().includes(colFilters.sku.toLowerCase())) return false;
          if (colFilters.ref_no && !r.ref_no.toLowerCase().includes(colFilters.ref_no.toLowerCase())) return false;
          if (colFilters.section && !r.section.toLowerCase().includes(colFilters.section.toLowerCase())) return false;
          if (colFilters.inward_date && !r.inward_date.toLowerCase().includes(colFilters.inward_date.toLowerCase())) return false;
          if (colFilters.inward_qty && !String(r.inward_qty).includes(colFilters.inward_qty)) return false;
          if (colFilters.disp_7d && !String(r.disp_7d_pct).includes(colFilters.disp_7d) && !String(r.disp_7d_qty).includes(colFilters.disp_7d)) return false;
          if (colFilters.disp_15d && !String(r.disp_15d_pct).includes(colFilters.disp_15d) && !String(r.disp_15d_qty).includes(colFilters.disp_15d)) return false;
          if (colFilters.total_disp && !String(r.total_disp_qty).includes(colFilters.total_disp) && !String(r.total_disp_pct).includes(colFilters.total_disp)) return false;
          if (colFilters.wh_soh && !String(r.wh_soh).includes(colFilters.wh_soh) && !String(r.wh_soh_pct).includes(colFilters.wh_soh)) return false;
          if (colFilters.age && !String(r.age_days).includes(colFilters.age)) return false;
          if (colFilters.status && colFilters.status !== 'all') {
            const cat = resolveSlaCategory(r);
            if (colFilters.status === 'red' && cat !== 'Red') return false;
            if (colFilters.status === 'orange' && cat !== 'Orange') return false;
            if (colFilters.status === 'yellow' && cat !== 'Yellow') return false;
            if (colFilters.status === 'green' && cat !== 'Green') return false;
          }
          return true;
        });
      }

      const headers = [
        'SKU Code',
        'Ref No',
        'Section',
        'Season',
        'Supplier',
        'Inward Date',
        'Inward Qty',
        'Cost Rate',
        '7D Disp Qty',
        '7D Disp %',
        '15D Disp Qty',
        '15D Disp %',
        '30D Disp Qty',
        '30D Disp %',
        'Total Disp Qty',
        'Total Disp %',
        'Lot Remaining',
        'Lot Remaining %',
        'Inward Age (Days)',
        'Status',
      ];

      const csvRows = [headers.join(',')];
      exportDataset.forEach((r) => {
        const cat = resolveSlaCategory(r);

        const rowData = [
          `"${r.sku}"`,
          `"${r.ref_no}"`,
          `"${r.section}"`,
          `"${r.season}"`,
          `"${(r.supplier || '').replace(/"/g, '""')}"`,
          `"${r.inward_date}"`,
          r.inward_qty,
          r.cost_rate,
          r.disp_7d_qty,
          `${r.disp_7d_pct}%`,
          r.disp_15d_qty,
          `${r.disp_15d_pct}%`,
          r.disp_30d_qty,
          `${r.disp_30d_pct}%`,
          r.total_disp_qty,
          `${r.total_disp_pct}%`,
          r.wh_soh,
          `${r.wh_soh_pct}%`,
          r.age_days,
          `"${statusLabel(cat)}"`,
        ];
        csvRows.push(rowData.join(','));
      });

      const blob = new Blob([csvRows.join('\n')], { type: 'text/csv;charset=utf-8;' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', `dispatch_timing_${new Date().toISOString().slice(0, 10)}.csv`);
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
    } catch {
      alert('Export failed. Please try again.');
    } finally {
      setExporting(false);
    }
  };

  const updateColFilter = (field: keyof ColumnFilters, val: string) => {
    setColFilters((prev) => ({ ...prev, [field]: val }));
  };

  const clearColFilters = () => {
    setColFilters(EMPTY_COL_FILTERS);
  };

  const hasActiveColFilters = useMemo(() => {
    return Object.entries(colFilters).some(([k, v]) => (k === 'status' ? v !== 'all' : String(v || '').trim() !== ''));
  }, [colFilters]);

  // Client-side column level filtering (All inputs perform global search across row fields)
  const filteredRows = useMemo(() => {
    if (!hasActiveColFilters) return rows;
    return rows.filter((r) => {
      const activeTextTerms = Object.entries(colFilters)
        .filter(([k, v]) => k !== 'status' && String(v || '').trim() !== '')
        .map(([, v]) => String(v).trim().toLowerCase());

      const rowValuesStr = Object.values(r)
        .map((v) => (v === null || v === undefined ? '' : String(v).toLowerCase()))
        .join(' ');

      // Each typed search term must match somewhere in the row values
      for (const term of activeTextTerms) {
        if (!rowValuesStr.includes(term)) return false;
      }

      if (colFilters.status && colFilters.status !== 'all') {
        const cat = resolveSlaCategory(r);
        if (colFilters.status === 'red' && cat !== 'Red') return false;
        if (colFilters.status === 'orange' && cat !== 'Orange') return false;
        if (colFilters.status === 'yellow' && cat !== 'Yellow') return false;
        if (colFilters.status === 'green' && cat !== 'Green') return false;
      }
      return true;
    });
  }, [rows, colFilters, hasActiveColFilters]);

  const redCount = summary?.redAlerts ?? summary?.criticalAlerts ?? 0;
  const orangeCount = summary?.orangeAlerts ?? 0;
  const yellowCount = summary?.yellowAlerts ?? 0;
  const greenCount = summary?.greenAlerts ?? summary?.onTrackSkus ?? summary?.healthySkus ?? 0;
  const isInitialLoad = loading && !summary;

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between rounded-2xl border border-slate-200/90 bg-white px-5 py-4 shadow-sm dark:border-slate-800 dark:bg-slate-900/90">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-blue-50 text-blue-600 dark:bg-blue-950/60 dark:text-blue-400">
            <Truck className="h-5 w-5" />
          </div>
          <div>
            <h2 className="text-base font-bold text-slate-900 dark:text-white">
              Dispatch Timing
            </h2>
            <p className="text-xs text-slate-500 dark:text-slate-400">
              Per inward lot · FIFO dispatch · warehouse → store speed
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={() => fetchData(undefined, { refresh: true })}
            disabled={loading}
            title="Rebuild from GRC + invoice files (clears server cache; pulls blob if stale)"
            className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-3.5 py-2 text-xs font-semibold text-slate-700 shadow-sm transition hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700 disabled:opacity-50"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin text-blue-600' : 'text-slate-400'}`} />
            Refresh
          </button>
          <button
            onClick={handleExportCsv}
            disabled={loading || exporting || !rows.length}
            className="inline-flex items-center gap-1.5 rounded-xl bg-blue-600 px-3.5 py-2 text-xs font-semibold text-white shadow-sm shadow-blue-500/20 transition hover:bg-blue-500 disabled:opacity-50"
          >
            {exporting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
            {exporting ? 'Exporting…' : 'Export CSV'}
          </button>
        </div>
      </div>

      {isInitialLoad ? (
        <div className="flex min-h-[420px] flex-col items-center justify-center gap-3 rounded-2xl border border-slate-200/90 bg-white px-6 py-16 shadow-sm dark:border-slate-800 dark:bg-slate-900/90">
          <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
          <div className="text-center">
            <p className="text-sm font-semibold text-slate-800 dark:text-slate-100">Loading dispatch timing…</p>
            <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
              Fetching inward lots, dispatch %, and status
            </p>
          </div>
        </div>
      ) : error && !summary ? (
        <div className="flex min-h-[320px] flex-col items-center justify-center gap-3 rounded-2xl border border-rose-200 bg-rose-50 px-6 py-14 dark:border-rose-900/50 dark:bg-rose-950/30">
          <AlertCircle className="h-7 w-7 text-rose-600 dark:text-rose-400" />
          <p className="text-sm font-semibold text-rose-700 dark:text-rose-300">{error}</p>
          <button
            onClick={() => fetchData()}
            className="inline-flex items-center gap-1.5 rounded-xl bg-rose-600 px-3.5 py-2 text-xs font-semibold text-white shadow-sm hover:bg-rose-500"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            Retry
          </button>
        </div>
      ) : (
      <>
      {/* Compact KPI strip */}
      <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 lg:grid-cols-5">
        <div className="rounded-xl border border-slate-200/80 bg-white px-3.5 py-3 shadow-2xs dark:border-slate-800 dark:bg-slate-900/80">
          <div className="flex items-center justify-between gap-2">
            <span className="text-[11px] font-semibold text-slate-500 dark:text-slate-400">
              Total inward
            </span>
            <Package className="h-3.5 w-3.5 text-slate-400" />
          </div>
          <div className="mt-1.5 text-xl font-bold text-slate-900 dark:text-white">
            {summary
              ? (summary.totalLots ?? summary.totalSkus).toLocaleString('en-IN')
              : '—'}
            <span className="text-[11px] font-normal text-slate-400 ml-1">lots</span>
          </div>
          <div className="mt-0.5 text-[11px] text-slate-500 dark:text-slate-400 truncate">
            {summary
              ? `${summary.totalSkus.toLocaleString('en-IN')} SKUs · ${summary.totalInwardQty.toLocaleString('en-IN')} units${
                  summary.dataThrough ? ` · through ${summary.dataThrough}` : ''
                }`
              : 'Loading…'}
          </div>
        </div>

        <div
          onClick={() => {
            setAlertFilter(alertFilter === 'red' ? 'all' : 'red');
            setPage(1);
          }}
          className={`cursor-pointer rounded-xl border px-3.5 py-3 shadow-2xs transition ${
            alertFilter === 'red'
              ? 'border-rose-500 bg-rose-50/60 ring-2 ring-rose-500/20 dark:bg-rose-950/40'
              : 'border-slate-200/80 bg-white hover:border-slate-300 dark:border-slate-800 dark:bg-slate-900/80'
          }`}
        >
          <div className="flex items-center justify-between gap-2">
            <span className="text-[11px] font-semibold text-rose-600 dark:text-rose-400">
              Stuck
            </span>
            <ShieldAlert className="h-3.5 w-3.5 text-rose-500" />
          </div>
          <div className="mt-1.5 text-xl font-bold text-slate-900 dark:text-white">
            {summary ? redCount.toLocaleString('en-IN') : '—'}
            <span className="text-[11px] font-normal text-rose-500 ml-1">lots</span>
          </div>
          <div className="mt-0.5 text-[11px] text-rose-600/90 dark:text-rose-400/90 truncate" title="Lot age > 90 days and ≥5% of that lot still not dispatched">
            &gt;90d &amp; lot ≥5% left
          </div>
        </div>

        <div
          onClick={() => {
            setAlertFilter(alertFilter === 'orange' ? 'all' : 'orange');
            setPage(1);
          }}
          className={`cursor-pointer rounded-xl border px-3.5 py-3 shadow-2xs transition ${
            alertFilter === 'orange'
              ? 'border-orange-500 bg-orange-50/60 ring-2 ring-orange-500/20 dark:bg-orange-950/40'
              : 'border-slate-200/80 bg-white hover:border-slate-300 dark:border-slate-800 dark:bg-slate-900/80'
          }`}
        >
          <div className="flex items-center justify-between gap-2">
            <span className="text-[11px] font-semibold text-orange-600 dark:text-orange-400">
              Slow start
            </span>
            <Clock className="h-3.5 w-3.5 text-orange-500" />
          </div>
          <div className="mt-1.5 text-xl font-bold text-slate-900 dark:text-white">
            {summary ? orangeCount.toLocaleString('en-IN') : '—'}
            <span className="text-[11px] font-normal text-orange-500 ml-1">lots</span>
          </div>
          <div className="mt-0.5 text-[11px] text-orange-600/90 dark:text-orange-400/90 truncate" title="Less than 50% dispatched in first 7 days">
            &lt;50% in first 7d
          </div>
        </div>

        <div
          onClick={() => {
            setAlertFilter(alertFilter === 'yellow' ? 'all' : 'yellow');
            setPage(1);
          }}
          className={`cursor-pointer rounded-xl border px-3.5 py-3 shadow-2xs transition ${
            alertFilter === 'yellow'
              ? 'border-amber-500 bg-amber-50/60 ring-2 ring-amber-500/20 dark:bg-amber-950/40'
              : 'border-slate-200/80 bg-white hover:border-slate-300 dark:border-slate-800 dark:bg-slate-900/80'
          }`}
        >
          <div className="flex items-center justify-between gap-2">
            <span className="text-[11px] font-semibold text-amber-600 dark:text-amber-400">
              Behind
            </span>
            <Clock className="h-3.5 w-3.5 text-amber-500" />
          </div>
          <div className="mt-1.5 text-xl font-bold text-slate-900 dark:text-white">
            {summary ? yellowCount.toLocaleString('en-IN') : '—'}
            <span className="text-[11px] font-normal text-amber-500 ml-1">lots</span>
          </div>
          <div className="mt-0.5 text-[11px] text-amber-600/90 dark:text-amber-400/90 truncate" title="Less than 80% dispatched in first 15 days">
            &lt;80% in first 15d
          </div>
        </div>

        <div
          onClick={() => {
            setAlertFilter(alertFilter === 'green' ? 'all' : 'green');
            setPage(1);
          }}
          className={`cursor-pointer rounded-xl border px-3.5 py-3 shadow-2xs transition ${
            alertFilter === 'green'
              ? 'border-emerald-500 bg-emerald-50/60 ring-2 ring-emerald-500/20 dark:bg-emerald-950/40'
              : 'border-slate-200/80 bg-white hover:border-slate-300 dark:border-slate-800 dark:bg-slate-900/80'
          }`}
        >
          <div className="flex items-center justify-between gap-2">
            <span className="text-[11px] font-semibold text-emerald-600 dark:text-emerald-400">
              On track
            </span>
            <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />
          </div>
          <div className="mt-1.5 text-xl font-bold text-slate-900 dark:text-white">
            {summary ? greenCount.toLocaleString('en-IN') : '—'}
            <span className="text-[11px] font-normal text-emerald-500 ml-1">lots</span>
          </div>
          <div className="mt-0.5 text-[11px] text-emerald-600/90 dark:text-emerald-400/90 truncate" title="Meeting dispatch timing targets">
            Meeting targets
          </div>
        </div>
      </div>

      {/* Filter bar */}
      <div className="rounded-2xl border border-slate-200/80 bg-white p-3.5 shadow-2xs dark:border-slate-800 dark:bg-slate-900/80">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="mr-1 text-xs font-semibold text-slate-400">
              Status:
            </span>
            {[
              { id: 'all', label: 'All', count: summary?.totalLots ?? summary?.totalSkus },
              { id: 'red', label: 'Stuck', count: redCount },
              { id: 'orange', label: 'Slow start', count: orangeCount },
              { id: 'yellow', label: 'Behind', count: yellowCount },
              { id: 'green', label: 'On track', count: greenCount },
            ].map((pill) => {
              const active = alertFilter === pill.id;
              return (
                <button
                  key={pill.id}
                  onClick={() => {
                    setAlertFilter(pill.id as any);
                    setPage(1);
                  }}
                  className={`inline-flex items-center gap-1.5 rounded-xl px-3 py-1.5 text-xs font-semibold transition ${
                    active
                      ? 'bg-slate-900 text-white shadow-sm dark:bg-blue-600 dark:text-white'
                      : 'border border-slate-200 bg-slate-50 text-slate-700 hover:bg-slate-100 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700'
                  }`}
                >
                  {pill.label}
                  {pill.count !== undefined && (
                    <span
                      className={`rounded-full px-1.5 py-0.2 text-[10px] font-bold ${
                        active
                          ? 'bg-white/20 text-white'
                          : 'bg-slate-200 text-slate-700 dark:bg-slate-700 dark:text-slate-200'
                      }`}
                    >
                      {pill.count}
                    </span>
                  )}
                </button>
              );
            })}
          </div>

          {/* Search, Dropdowns & Reset */}
          <div className="flex flex-wrap items-center gap-2">
            {/* Global Search */}
            <div className="relative min-w-[200px] max-w-xs flex-1">
              <Search className="absolute left-3 top-2.5 h-3.5 w-3.5 text-slate-400" />
              <input
                type="text"
                placeholder="Global search (SKU, Ref No, Section, Supplier, Date, Status…)"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="w-full rounded-xl border border-slate-200 bg-slate-50/50 py-1.5 pl-8 pr-8 text-xs font-medium text-slate-800 placeholder-slate-400 outline-none transition focus:border-blue-500 focus:bg-white dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
              />
              {search && (
                <button
                  onClick={() => setSearch('')}
                  className="absolute right-2.5 top-2 text-slate-400 hover:text-slate-600"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              )}
            </div>

            {/* Section Dropdown */}
            {sections.length > 0 && (
              <select
                value={selectedSection}
                onChange={(e) => {
                  setSelectedSection(e.target.value);
                  setPage(1);
                }}
                className="rounded-xl border border-slate-200 bg-slate-50/50 px-2.5 py-1.5 text-xs font-medium text-slate-700 outline-none transition focus:border-blue-500 focus:bg-white dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300"
              >
                <option value="all">All Sections ({sections.length})</option>
                {sections.map((sec) => (
                  <option key={sec} value={sec}>
                    {sec}
                  </option>
                ))}
              </select>
            )}

            {/* Season Dropdown */}
            {seasons.length > 0 && (
              <select
                value={selectedSeason}
                onChange={(e) => {
                  setSelectedSeason(e.target.value);
                  setPage(1);
                }}
                className="rounded-xl border border-slate-200 bg-slate-50/50 px-2.5 py-1.5 text-xs font-medium text-slate-700 outline-none transition focus:border-blue-500 focus:bg-white dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300"
              >
                <option value="all">All Seasons ({seasons.length})</option>
                {seasons.map((seas) => (
                  <option key={seas} value={seas}>
                    {seas}
                  </option>
                ))}
              </select>
            )}

            {/* Reset */}
            {(search || alertFilter !== 'all' || selectedSection !== 'all' || selectedSeason !== 'all' || hasActiveColFilters) && (
              <button
                onClick={() => {
                  setSearch('');
                  setAlertFilter('all');
                  setSelectedSection('all');
                  setSelectedSeason('all');
                  clearColFilters();
                  setPage(1);
                }}
                className="rounded-xl border border-slate-200 bg-slate-50 px-2.5 py-1.5 text-xs font-semibold text-slate-600 transition hover:bg-slate-100 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300"
              >
                Reset All
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Main Professional Data Table */}
      <div className="overflow-hidden rounded-2xl border border-slate-200/90 bg-white shadow-2xs dark:border-slate-800 dark:bg-slate-900/80">
        <div className="flex items-center justify-between border-b border-slate-100 bg-slate-50/60 px-5 py-3 dark:border-slate-800 dark:bg-slate-800/40">
          <div className="flex items-center gap-3">
            <div>
              <h3 className="text-xs font-bold text-slate-900 dark:text-white">
                SKU Inward vs Dispatch Velocity
              </h3>
              <p className="text-[11px] text-slate-500">
                Showing {filteredRows.length} of {total} lots
              </p>
            </div>
            {hasActiveColFilters && (
              <button
                onClick={clearColFilters}
                className="inline-flex items-center gap-1 rounded-lg border border-slate-200 bg-white px-2 py-1 text-[10px] font-semibold text-blue-600 hover:bg-blue-50 dark:border-slate-700 dark:bg-slate-800 dark:text-blue-400"
              >
                <X className="h-3 w-3" />
                Clear Column Searches
              </button>
            )}
          </div>

          <div className="flex items-center gap-2">
            <span className="text-[11px] text-slate-400">Rows per page:</span>
            <select
              value={pageSize}
              onChange={(e) => {
                setPageSize(Number(e.target.value));
                setPage(1);
              }}
              className="rounded-lg border border-slate-200 bg-white px-2 py-0.5 text-xs font-semibold text-slate-700 outline-none dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300"
            >
              <option value={20}>20</option>
              <option value={50}>50</option>
              <option value={100}>100</option>
            </select>
          </div>
        </div>

        {error && (
          <div className="m-4 flex items-center justify-between gap-3 rounded-xl border border-rose-200 bg-rose-50 p-3 text-xs font-medium text-rose-700 dark:border-rose-900/50 dark:bg-rose-950/30 dark:text-rose-300">
            <div className="flex items-center gap-2">
              <AlertCircle className="h-4 w-4 shrink-0" />
              <span>{error}</span>
            </div>
            <button
              onClick={() => fetchData()}
              disabled={loading}
              className="inline-flex items-center gap-1 rounded-lg bg-rose-600 px-3 py-1 text-xs font-semibold text-white shadow-xs transition hover:bg-rose-700 disabled:opacity-50"
            >
              <RefreshCw className={`h-3 w-3 ${loading ? 'animate-spin' : ''}`} />
              Retry
            </button>
          </div>
        )}

        <div className="max-h-[580px] overflow-x-auto overflow-y-auto custom-scrollbar">
          <table className="w-full min-w-[1050px] border-collapse text-left text-xs">
            <thead className="sticky top-0 z-10 shadow-xs">
              {/* Primary Column Header Row with Sort */}
              <tr className="border-b border-slate-200 bg-slate-100 text-[11px] font-bold uppercase tracking-wider text-slate-700 dark:border-slate-800 dark:bg-slate-800 dark:text-slate-200">
                <th
                  onClick={() => handleSort('sku')}
                  className="cursor-pointer px-3 py-2.5 hover:bg-slate-100 dark:hover:bg-slate-700/60"
                >
                  <div className="flex items-center gap-1">
                    <span>SKU Code</span>
                    <ArrowUpDown className="h-3 w-3 text-slate-400" />
                  </div>
                </th>
                <th className="px-2.5 py-2.5">Ref No</th>
                <th className="px-2.5 py-2.5">Section</th>
                <th
                  onClick={() => handleSort('inward_date')}
                  className="cursor-pointer px-2.5 py-2.5 hover:bg-slate-100 dark:hover:bg-slate-700/60"
                >
                  <div className="flex items-center gap-1">
                    <span>Inward Date</span>
                    <ArrowUpDown className="h-3 w-3 text-slate-400" />
                  </div>
                </th>
                <th
                  onClick={() => handleSort('inward_qty')}
                  className="cursor-pointer px-2.5 py-2.5 text-right hover:bg-slate-100 dark:hover:bg-slate-700/60"
                >
                  <div className="flex items-center justify-end gap-1">
                    <span>Inward Qty</span>
                    <ArrowUpDown className="h-3 w-3 text-slate-400" />
                  </div>
                </th>
                <th
                  onClick={() => handleSort('disp_7d_pct')}
                  className="cursor-pointer px-2.5 py-2.5 text-center hover:bg-slate-100 dark:hover:bg-slate-700/60"
                >
                  <div className="flex items-center justify-center gap-1">
                    <span>7D Disp</span>
                    <ArrowUpDown className="h-3 w-3 text-slate-400" />
                  </div>
                </th>
                <th
                  onClick={() => handleSort('disp_15d_pct')}
                  className="cursor-pointer px-2.5 py-2.5 text-center hover:bg-slate-100 dark:hover:bg-slate-700/60"
                >
                  <div className="flex items-center justify-center gap-1">
                    <span>15D Disp</span>
                    <ArrowUpDown className="h-3 w-3 text-slate-400" />
                  </div>
                </th>
                <th
                  onClick={() => handleSort('total_disp_qty')}
                  className="cursor-pointer px-2.5 py-2.5 text-right hover:bg-slate-100 dark:hover:bg-slate-700/60"
                >
                  <div className="flex items-center justify-end gap-1">
                    <span>Total Dispatched</span>
                    <ArrowUpDown className="h-3 w-3 text-slate-400" />
                  </div>
                </th>
                <th
                  onClick={() => handleSort('wh_soh')}
                  className="cursor-pointer px-2.5 py-2.5 text-right hover:bg-slate-100 dark:hover:bg-slate-700/60"
                >
                  <div className="flex items-center justify-end gap-1">
                    <span>Lot left</span>
                    <ArrowUpDown className="h-3 w-3 text-slate-400" />
                  </div>
                </th>
                <th
                  onClick={() => handleSort('age_days')}
                  className="cursor-pointer px-2.5 py-2.5 text-center hover:bg-slate-100 dark:hover:bg-slate-700/60"
                >
                  <div className="flex items-center justify-center gap-1">
                    <span>Age</span>
                    <ArrowUpDown className="h-3 w-3 text-slate-400" />
                  </div>
                </th>
                <th className="px-3 py-2.5 text-center">Status</th>
              </tr>

              {/* Individual Column-Level Search Bar Row */}
              <tr className="border-b border-slate-200 bg-slate-100/70 p-1 dark:border-slate-800 dark:bg-slate-900">
                {/* SKU search */}
                <th className="p-1.5">
                  <div className="relative">
                    <input
                      type="text"
                      placeholder="Search SKU…"
                      value={colFilters.sku}
                      onChange={(e) => updateColFilter('sku', e.target.value)}
                      className="w-full rounded-md border border-slate-200 bg-white px-2 py-1 text-[11px] font-normal text-slate-800 placeholder-slate-400 outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500/20 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
                    />
                  </div>
                </th>

                {/* Ref No search */}
                <th className="p-1.5">
                  <input
                    type="text"
                    placeholder="Ref…"
                    value={colFilters.ref_no}
                    onChange={(e) => updateColFilter('ref_no', e.target.value)}
                    className="w-full rounded-md border border-slate-200 bg-white px-2 py-1 text-[11px] font-normal text-slate-800 placeholder-slate-400 outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500/20 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
                  />
                </th>

                {/* Section search */}
                <th className="p-1.5">
                  <input
                    type="text"
                    placeholder="Section…"
                    value={colFilters.section}
                    onChange={(e) => updateColFilter('section', e.target.value)}
                    className="w-full rounded-md border border-slate-200 bg-white px-2 py-1 text-[11px] font-normal text-slate-800 placeholder-slate-400 outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500/20 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
                  />
                </th>

                {/* Inward Date search */}
                <th className="p-1.5">
                  <input
                    type="text"
                    placeholder="Date…"
                    value={colFilters.inward_date}
                    onChange={(e) => updateColFilter('inward_date', e.target.value)}
                    className="w-full rounded-md border border-slate-200 bg-white px-2 py-1 text-[11px] font-normal text-slate-800 placeholder-slate-400 outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500/20 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
                  />
                </th>

                {/* Inward Qty search */}
                <th className="p-1.5">
                  <input
                    type="text"
                    placeholder="Qty…"
                    value={colFilters.inward_qty}
                    onChange={(e) => updateColFilter('inward_qty', e.target.value)}
                    className="w-full rounded-md border border-slate-200 bg-white px-2 py-1 text-right text-[11px] font-normal text-slate-800 placeholder-slate-400 outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500/20 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
                  />
                </th>

                {/* 7D Disp % search */}
                <th className="p-1.5">
                  <input
                    type="text"
                    placeholder="7D %…"
                    value={colFilters.disp_7d}
                    onChange={(e) => updateColFilter('disp_7d', e.target.value)}
                    className="w-full rounded-md border border-slate-200 bg-white px-2 py-1 text-center text-[11px] font-normal text-slate-800 placeholder-slate-400 outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500/20 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
                  />
                </th>

                {/* 15D Disp % search */}
                <th className="p-1.5">
                  <input
                    type="text"
                    placeholder="15D %…"
                    value={colFilters.disp_15d}
                    onChange={(e) => updateColFilter('disp_15d', e.target.value)}
                    className="w-full rounded-md border border-slate-200 bg-white px-2 py-1 text-center text-[11px] font-normal text-slate-800 placeholder-slate-400 outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500/20 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
                  />
                </th>

                {/* Total Dispatched search */}
                <th className="p-1.5">
                  <input
                    type="text"
                    placeholder="Total…"
                    value={colFilters.total_disp}
                    onChange={(e) => updateColFilter('total_disp', e.target.value)}
                    className="w-full rounded-md border border-slate-200 bg-white px-2 py-1 text-right text-[11px] font-normal text-slate-800 placeholder-slate-400 outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500/20 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
                  />
                </th>

                {/* WH SOH search */}
                <th className="p-1.5">
                  <input
                    type="text"
                    placeholder="Left…"
                    value={colFilters.wh_soh}
                    onChange={(e) => updateColFilter('wh_soh', e.target.value)}
                    className="w-full rounded-md border border-slate-200 bg-white px-2 py-1 text-right text-[11px] font-normal text-slate-800 placeholder-slate-400 outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500/20 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
                  />
                </th>

                {/* Age search */}
                <th className="p-1.5">
                  <input
                    type="text"
                    placeholder="Age…"
                    value={colFilters.age}
                    onChange={(e) => updateColFilter('age', e.target.value)}
                    className="w-full rounded-md border border-slate-200 bg-white px-2 py-1 text-center text-[11px] font-normal text-slate-800 placeholder-slate-400 outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500/20 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
                  />
                </th>

                {/* Status dropdown search */}
                <th className="p-1.5">
                  <select
                    value={colFilters.status}
                    onChange={(e) => updateColFilter('status', e.target.value)}
                    className="w-full rounded-md border border-slate-200 bg-white px-1.5 py-1 text-[11px] font-semibold text-slate-700 outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
                  >
                    <option value="all">All</option>
                    <option value="red">Stuck</option>
                    <option value="orange">Slow start</option>
                    <option value="yellow">Behind</option>
                    <option value="green">On track</option>
                  </select>
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800/60">
              {loading ? (
                <tr>
                  <td colSpan={11} className="py-16 text-center">
                    <div className="inline-flex flex-col items-center gap-2">
                      <Loader2 className="h-7 w-7 animate-spin text-blue-600" />
                      <span className="text-xs text-slate-500 font-medium">Updating results…</span>
                    </div>
                  </td>
                </tr>
              ) : filteredRows.length === 0 ? (
                <tr>
                  <td colSpan={11} className="py-14 text-center text-xs text-slate-500">
                    No records found matching your column searches.
                  </td>
                </tr>
              ) : (
                filteredRows.map((row) => {
                  const cat = resolveSlaCategory(row);
                  const isCritical = cat === 'Red' || row.alert_red;

                  return (
                    <tr
                      key={`${row.sku}|${row.inward_date}`}
                      className="transition-colors hover:bg-slate-50/80 dark:hover:bg-slate-800/40"
                    >
                      {/* SKU Code */}
                      <td className="px-3 py-2 font-semibold text-slate-900 dark:text-white">
                        <SkuHoverCard sku={row.sku}>
                          <span className="cursor-pointer font-mono text-xs font-bold text-blue-600 hover:underline dark:text-blue-400">
                            {row.sku}
                          </span>
                        </SkuHoverCard>
                      </td>

                      {/* Ref No */}
                      <td className="px-2.5 py-2 font-mono text-[11px] text-slate-600 dark:text-slate-300">
                        {row.ref_no || '—'}
                      </td>

                      {/* Section */}
                      <td className="px-2.5 py-2">
                        <span className="font-medium text-slate-800 dark:text-slate-200">
                          {row.section || '—'}
                        </span>
                      </td>

                      {/* Inward Date */}
                      <td className="px-2.5 py-2 whitespace-nowrap text-slate-600 dark:text-slate-300">
                        {row.inward_date}
                      </td>

                      {/* Inward Qty */}
                      <td className="px-2.5 py-2 text-right font-semibold text-slate-900 dark:text-white">
                        {row.inward_qty.toLocaleString('en-IN')}
                      </td>

                      {/* 7D Disp % */}
                      <td className="px-2.5 py-2 text-center">
                        <span
                          className={`inline-block rounded px-1.5 py-0.5 text-[11px] font-semibold ${
                            row.alert_orange
                              ? 'text-orange-700 bg-orange-50 border border-orange-200/80 dark:bg-orange-950/40 dark:text-orange-300 dark:border-orange-800/60'
                              : 'text-slate-700 dark:text-slate-300'
                          }`}
                        >
                          {row.disp_7d_pct}%
                        </span>
                      </td>

                      {/* 15D Disp % */}
                      <td className="px-2.5 py-2 text-center">
                        <span
                          className={`inline-block rounded px-1.5 py-0.5 text-[11px] font-semibold ${
                            row.alert_yellow
                              ? 'text-amber-800 bg-amber-50 border border-amber-200/80 dark:bg-amber-950/40 dark:text-amber-300 dark:border-amber-800/60'
                              : 'text-slate-700 dark:text-slate-300'
                          }`}
                        >
                          {row.disp_15d_pct}%
                        </span>
                      </td>

                      {/* Total Dispatched */}
                      <td className="px-2.5 py-2 text-right">
                        <span className="font-semibold text-slate-900 dark:text-white">
                          {row.total_disp_qty.toLocaleString('en-IN')}
                        </span>
                        <span className="ml-1 text-[10px] text-slate-400">({row.total_disp_pct}%)</span>
                      </td>

                      {/* WH SOH */}
                      <td className="px-2.5 py-2 text-right">
                        <span
                          className={`font-semibold ${
                            isCritical ? 'text-rose-600 dark:text-rose-400' : 'text-slate-800 dark:text-slate-200'
                          }`}
                        >
                          {row.wh_soh.toLocaleString('en-IN')}
                        </span>
                        <span className="ml-1 text-[10px] text-slate-400">({row.wh_soh_pct}%)</span>
                      </td>

                      {/* Inward Age (Days) */}
                      <td className="px-2.5 py-2 text-center text-slate-600 dark:text-slate-400">
                        {row.age_days}d
                      </td>

                      {/* Status */}
                      <td className="px-3 py-2 text-center">
                        {cat === 'Red' ? (
                          <span className="inline-flex items-center gap-1.5 rounded-md border border-rose-200 bg-rose-50 px-2 py-0.5 text-[11px] font-semibold text-rose-700 dark:border-rose-900/60 dark:bg-rose-950/40 dark:text-rose-300" title="Lot age > 90 days and ≥5% of lot still not dispatched">
                            <span className="h-1.5 w-1.5 rounded-full bg-rose-500" />
                            Stuck
                          </span>
                        ) : cat === 'Orange' ? (
                          <span className="inline-flex items-center gap-1.5 rounded-md border border-orange-200 bg-orange-50 px-2 py-0.5 text-[11px] font-semibold text-orange-700 dark:border-orange-900/60 dark:bg-orange-950/40 dark:text-orange-300" title="Less than 50% dispatched in first 7 days">
                            <span className="h-1.5 w-1.5 rounded-full bg-orange-500" />
                            Slow start
                          </span>
                        ) : cat === 'Yellow' ? (
                          <span className="inline-flex items-center gap-1.5 rounded-md border border-amber-200 bg-amber-50 px-2 py-0.5 text-[11px] font-semibold text-amber-800 dark:border-amber-900/60 dark:bg-amber-950/40 dark:text-amber-300" title="Less than 80% dispatched in first 15 days">
                            <span className="h-1.5 w-1.5 rounded-full bg-amber-400" />
                            Behind
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1.5 rounded-md border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[11px] font-semibold text-emerald-700 dark:border-emerald-900/60 dark:bg-emerald-950/40 dark:text-emerald-300" title="Meeting dispatch timing targets">
                            <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                            On track
                          </span>
                        )}
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination Footer */}
        <div className="flex items-center justify-between border-t border-slate-100 px-5 py-3 dark:border-slate-800">
          <div className="text-xs text-slate-500">
            Page <span className="font-semibold text-slate-900 dark:text-white">{page}</span> of{' '}
            <span className="font-semibold text-slate-900 dark:text-white">{totalPages}</span>
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page <= 1 || loading}
              className="inline-flex items-center gap-1 rounded-lg border border-slate-200 bg-white px-3 py-1 text-xs font-semibold text-slate-700 shadow-2xs transition hover:bg-slate-50 disabled:opacity-40 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
            >
              <ChevronLeft className="h-3.5 w-3.5" />
              Prev
            </button>
            <button
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page >= totalPages || loading}
              className="inline-flex items-center gap-1 rounded-lg border border-slate-200 bg-white px-3 py-1 text-xs font-semibold text-slate-700 shadow-2xs transition hover:bg-slate-50 disabled:opacity-40 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
            >
              Next
              <ChevronRight className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
      </div>
      </>
      )}
    </div>
  );
}

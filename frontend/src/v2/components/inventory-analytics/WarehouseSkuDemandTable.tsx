import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle,
  ArrowUpDown,
  Check,
  ChevronDown,
  Download,
  Filter,
  Loader2,
  PackageSearch,
  RefreshCw,
  Search,
  TrendingDown,
  Warehouse,
  X,
} from 'lucide-react';
import { API_URL } from '../../../config';
import { formatNumber } from './inventoryAnalytics';

export type SkuDemandRow = {
  barcode: string;
  sku: string;
  size: string;
  department: string;
  section: string;
  color?: string;
  mrp?: number;
  avgWeeklyDemand: number;
  whStock: number;
  wh_soh?: number;
  idealWhStock: number;
  stockGap: number;
  status: 'Shortage' | 'Excess' | 'Balanced';
  dispatch_sla_category?: 'Critical' | 'Lagging' | 'On Track' | string;
  weeksWithSales: number;
  totalSold4w: number;
};

export type ApiResponse = {
  ok: boolean;
  demandWeeks: number;
  coverageWeeks: number;
  weeksUsed: number;
  weekLabels: string[];
  forecastWeekLabels?: string[];
  forecastPeriodText?: string;
  historicalPeriodText?: string;
  totalShortageUnits: number;
  totalExcessUnits: number;
  shortageSkuCount: number;
  excessSkuCount: number;
  balancedSkuCount: number;
  totalWhStockUnits?: number;
  whSkusInStockCount?: number;
  totalCatalogSkus?: number;
  filter_options?: {
    departments: string[];
    sections: string[];
    sizes: string[];
  };
  rows: SkuDemandRow[];
  total: number;
  filteredTotal?: number;
  detail?: string;
};

function statusBadge(status: string) {
  if (status === 'Shortage')
    return 'bg-rose-100 text-rose-800 dark:bg-rose-950/70 dark:text-rose-200 border border-rose-300 dark:border-rose-800 font-bold';
  if (status === 'Excess')
    return 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/70 dark:text-emerald-200 border border-emerald-300 dark:border-emerald-800 font-bold';
  return 'bg-amber-100 text-amber-900 dark:bg-amber-950/70 dark:text-amber-200 border border-amber-300 dark:border-amber-800 font-bold';
}

const COMMON_SIZES = [
  { value: '1XS', label: 'XS (1XS)', aliases: ['1XS', 'XS'] },
  { value: '2S', label: 'S (2S)', aliases: ['2S', 'S'] },
  { value: '3M', label: 'M (3M)', aliases: ['3M', 'M'] },
  { value: '4L', label: 'L (4L)', aliases: ['4L', 'L'] },
  { value: '5XL', label: 'XL (5XL)', aliases: ['5XL', 'XL'] },
  { value: '6XXL', label: 'XXL (6XXL)', aliases: ['6XXL', 'XXL', '2XL'] },
];

/** Simple demand statuses — same meaning as the KPI cards */
const STATUS_OPTIONS = [
  { value: 'Shortage', label: 'Need stock' },
  { value: 'Balanced', label: 'OK' },
  { value: 'Excess', label: 'Extra stock' },
  { value: 'in_stock', label: 'Has stock' },
];

type FilterOption = {
  value: string;
  label: string;
};

/**
 * Dropdown component supporting BOTH Multi-Select (checkbox toggles)
 * and Single-Select (instant "Only" button).
 */
function WarehouseFilterDropdown({
  label,
  options,
  selected,
  onChange,
  placeholder = 'All',
  enableSearch = false,
}: {
  label: string;
  options: FilterOption[];
  selected: string[];
  onChange: (v: string[]) => void;
  placeholder?: string;
  enableSearch?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  const filtered = options.filter(
    (o) =>
      o.label.toLowerCase().includes(search.toLowerCase()) ||
      o.value.toLowerCase().includes(search.toLowerCase())
  );

  const toggle = (val: string) => {
    onChange(selected.length === 1 && selected[0] === val ? [] : [val]);
  };

  const selectOnly = (val: string, e: React.MouseEvent) => {
    e.stopPropagation();
    onChange([val]);
  };

  const isNoneSelected = selected.length === 0;

  const displayLabel = useMemo(() => {
    if (isNoneSelected) return placeholder;
    if (selected.length === 1) {
      const found = options.find((o) => o.value === selected[0]);
      return found ? `${label}: ${found.label}` : `${label}: ${selected[0]}`;
    }
    return `${label}: ${selected.length} selected`;
  }, [selected, options, placeholder, isNoneSelected, label]);

  return (
    <div ref={dropdownRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className={`flex items-center gap-1.5 rounded-xl border px-2.5 py-1.5 text-xs font-medium transition ${
          selected.length > 0
            ? 'border-blue-400 bg-blue-50/80 text-blue-800 shadow-xs dark:border-blue-600/60 dark:bg-blue-950/50 dark:text-blue-200'
            : 'border-slate-200 bg-white text-slate-700 hover:border-slate-300 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 dark:hover:border-slate-600'
        }`}
      >
        <span className="max-w-[150px] truncate">{displayLabel}</span>
        {selected.length > 1 && (
          <span className="flex h-4 min-w-4 items-center justify-center rounded-full bg-blue-600 px-1 text-[9px] font-bold text-white">
            {selected.length}
          </span>
        )}
        <ChevronDown
          className={`h-3 w-3 transition-transform ${open ? 'rotate-180 text-blue-600' : 'opacity-60'}`}
        />
      </button>

      {open && (
        <div className="absolute left-0 top-full z-50 mt-1.5 w-64 rounded-xl border border-slate-200 bg-white p-1.5 shadow-xl dark:border-slate-700 dark:bg-slate-900">
          {enableSearch && (
            <div className="mb-1 border-b border-slate-100 pb-1.5 dark:border-slate-800">
              <div className="flex items-center gap-1.5 rounded-lg bg-slate-50 px-2 py-1 dark:bg-slate-800">
                <Search className="h-3 w-3 text-slate-400 shrink-0" />
                <input
                  type="text"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder={`Search ${label.toLowerCase()}…`}
                  className="flex-1 bg-transparent text-xs text-slate-700 outline-none placeholder:text-slate-400 dark:text-slate-200"
                  autoFocus
                />
                {search && (
                  <button onClick={() => setSearch('')} className="text-slate-400 hover:text-slate-600">
                    <X className="h-3 w-3" />
                  </button>
                )}
              </div>
            </div>
          )}

          {/* Quick Action Toolbar */}
          <div className="flex items-center justify-between border-b border-slate-100 px-1 py-1 text-[11px] dark:border-slate-800">
            <button
              type="button"
              onClick={() => onChange([])}
              className="text-slate-500 hover:underline dark:text-slate-400"
            >
              Clear
            </button>
          </div>

          {/* Options List */}
          <div className="max-h-56 overflow-y-auto py-1">
            {filtered.length === 0 ? (
              <div className="py-3 text-center text-xs text-slate-400">No options found</div>
            ) : (
              filtered.map((opt) => {
                const isSelected = selected.includes(opt.value);
                const isOnly = selected.length === 1 && selected[0] === opt.value;
                return (
                  <div
                    key={opt.value}
                    onClick={() => toggle(opt.value)}
                    className={`group flex cursor-pointer items-center justify-between rounded-lg px-2 py-1.5 text-xs transition ${
                      isSelected
                        ? 'bg-blue-50/70 text-blue-900 dark:bg-blue-950/40 dark:text-blue-100'
                        : 'text-slate-700 hover:bg-slate-50 dark:text-slate-200 dark:hover:bg-slate-800'
                    }`}
                  >
                    <div className="flex items-center gap-2 truncate">
                      <span
                        className={`flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded border text-[10px] transition ${
                          isSelected
                            ? 'border-blue-600 bg-blue-600 text-white'
                            : 'border-slate-300 group-hover:border-slate-400 dark:border-slate-600'
                        }`}
                      >
                        {isSelected && <Check className="h-2.5 w-2.5 stroke-[3]" />}
                      </span>
                      <span className="truncate">{opt.label}</span>
                    </div>

                    <button
                      type="button"
                      title="Select only this item (Single Select)"
                      onClick={(e) => selectOnly(opt.value, e)}
                      className={`ml-2 rounded px-1.5 py-0.5 text-[10px] font-semibold transition ${
                        isOnly
                          ? 'bg-blue-600 text-white'
                          : 'opacity-0 text-blue-600 group-hover:opacity-100 hover:bg-blue-100 dark:text-blue-400 dark:hover:bg-blue-900/60'
                      }`}
                    >
                      Only
                    </button>
                  </div>
                );
              })
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export function WarehouseSkuDemandTable() {
  const [data, setData] = useState<ApiResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Filters (Multi-select arrays)
  const [search, setSearch] = useState('');
  const [deptFilter, setDeptFilter] = useState<string[]>([]);
  const [secFilter, setSecFilter] = useState<string[]>([]);
  const [sizeFilter, setSizeFilter] = useState<string[]>([]);
  const [statusFilter, setStatusFilter] = useState<string[]>([]);
  const [sortBy, setSortBy] = useState<'stock_gap' | 'wh_stock' | 'avg_weekly_demand' | 'sku' | 'size'>('stock_gap');
  const [sortDir, setSortDir] = useState<'desc' | 'asc'>('desc');
  const [pageSize, setPageSize] = useState<number>(100);
  const [currentPage, setCurrentPage] = useState<number>(1);

  const loadData = useCallback((signal?: AbortSignal) => {
    setLoading(true);
    setError(null);
    fetch(`${API_URL}/inventory-analytics/sku-demand-vs-wh-stock?limit=10000&show=all`, {
      signal,
    })
      .then(async (res) => {
        const raw = await res.text();
        let json: ApiResponse;
        try {
          json = JSON.parse(raw) as ApiResponse;
        } catch {
          throw new Error(
            res.ok
              ? 'Backend returned HTML instead of JSON — restart API'
              : `Failed (${res.status}): endpoint missing or backend not running`
          );
        }
        if (!res.ok) throw new Error(String(json.detail || `Failed (${res.status})`));
        if ((json as ApiResponse & { error?: string }).error) {
          throw new Error(String((json as ApiResponse & { error?: string }).error));
        }
        if (!signal?.aborted) {
          setData(json);
          setLoading(false);
        }
      })
      .catch((err: unknown) => {
        if (signal?.aborted) return;
        setError(err instanceof Error ? err.message : 'Could not load Warehouse Demand & SOH data');
        setLoading(false);
      });
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    loadData(controller.signal);
    return () => controller.abort();
  }, [loadData]);

  const handleExport = async () => {
    setExporting(true);
    try {
      const p = new URLSearchParams();
      if (statusFilter.length > 0) {
        p.set('show', statusFilter.join(','));
      } else {
        p.set('show', 'all');
      }
      if (deptFilter.length > 0) p.set('department', deptFilter.join(','));
      if (secFilter.length > 0) p.set('section', secFilter.join(','));
      if (sizeFilter.length > 0) p.set('size', sizeFilter.join(','));
      if (search.trim()) p.set('search', search.trim());
      p.set('sort_by', sortBy);
      p.set('sort_dir', sortDir);

      const res = await fetch(`${API_URL}/inventory-analytics/export-wh-demand-excel?${p}`);
      if (!res.ok) throw new Error('Export failed');
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `Warehouse_Demand_And_SOH_${new Date().toISOString().slice(0, 10)}.xlsx`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      alert('Failed to export Warehouse Demand & SOH. Please try again.');
    } finally {
      setExporting(false);
    }
  };

  const rows = useMemo(() => data?.rows ?? [], [data?.rows]);

  const filtered = useMemo(() => {
    let result = rows;

    if (deptFilter.length > 0) {
      result = result.filter((r) => deptFilter.includes(r.department));
    }
    if (secFilter.length > 0) {
      result = result.filter((r) => secFilter.includes(r.section));
    }
    if (sizeFilter.length > 0) {
      const activeAliases = new Set<string>();
      sizeFilter.forEach((val) => {
        const found = COMMON_SIZES.find((s) => s.value === val);
        if (found) {
          found.aliases.forEach((a) => activeAliases.add(a.toUpperCase()));
        } else {
          activeAliases.add(val.toUpperCase());
        }
      });
      result = result.filter((r) => activeAliases.has((r.size || '').trim().toUpperCase()));
    }
    if (statusFilter.length > 0) {
      result = result.filter((r) => {
        if (statusFilter.includes('in_stock') && r.whStock > 0) return true;
        if (statusFilter.includes(r.status)) return true;
        return false;
      });
    }
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      result = result.filter((r) =>
        Object.values(r).some(
          (val) => val !== null && val !== undefined && String(val).toLowerCase().includes(q)
        )
      );
    }

    // Sort — API/UI use camelCase fields; sortBy state keeps snake_case ids
    const sortKeyMap: Record<string, keyof SkuDemandRow> = {
      stock_gap: 'stockGap',
      wh_stock: 'whStock',
      avg_weekly_demand: 'avgWeeklyDemand',
      sku: 'sku',
      size: 'size',
    };
    const sorted = [...result].sort((a, b) => {
      const key = sortKeyMap[sortBy] || (sortBy as keyof SkuDemandRow);
      let vA: any = a[key];
      let vB: any = b[key];
      if (typeof vA === 'string') vA = vA.toLowerCase();
      if (typeof vB === 'string') vB = vB.toLowerCase();
      if (vA < vB) return sortDir === 'asc' ? -1 : 1;
      if (vA > vB) return sortDir === 'asc' ? 1 : -1;
      return 0;
    });

    return sorted;
  }, [rows, deptFilter, secFilter, sizeFilter, statusFilter, search, sortBy, sortDir]);

  // Dynamic aggregates for filtered dataset
  const filteredTotalWhStock = useMemo(() => {
    return filtered.reduce((acc, r) => acc + (r.whStock || 0), 0);
  }, [filtered]);

  const filteredInStockCount = useMemo(() => {
    return filtered.filter((r) => (r.whStock || 0) > 0).length;
  }, [filtered]);

  // Pagination slice
  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const paginatedRows = useMemo(() => {
    const start = (currentPage - 1) * pageSize;
    return filtered.slice(start, start + pageSize);
  }, [filtered, currentPage, pageSize]);

  // Filter option sets
  const deptFilterOptions = useMemo<FilterOption[]>(() => {
    const list = data?.filter_options?.departments ?? Array.from(new Set(rows.map((r) => r.department).filter(Boolean))).sort();
    return list.map((d) => ({ value: d, label: d }));
  }, [data?.filter_options?.departments, rows]);

  const secFilterOptions = useMemo<FilterOption[]>(() => {
    const list = data?.filter_options?.sections ?? Array.from(new Set(rows.map((r) => r.section).filter(Boolean))).sort();
    return list.map((s) => ({ value: s, label: s }));
  }, [data?.filter_options?.sections, rows]);

  const sizeFilterOptions = useMemo<FilterOption[]>(
    () => COMMON_SIZES.map((sz) => ({ value: sz.value, label: sz.label })),
    []
  );

  const subtitle = data
    ? `Based on last ${data.weeksUsed} weeks of sales · covering next ${data.coverageWeeks} weeks of demand · WH SOH ${formatNumber(data.totalWhStockUnits ?? 0)} units`
    : 'Loading Warehouse Demand & SOH dataset…';

  const hasActiveFilters =
    deptFilter.length > 0 ||
    secFilter.length > 0 ||
    sizeFilter.length > 0 ||
    statusFilter.length > 0 ||
    !!search.trim();

  return (
    <div className="overflow-hidden rounded-2xl border border-slate-200/90 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900/80">
      {/* Header */}
      <div className="border-b border-slate-100 px-5 py-4 dark:border-slate-800">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h3 className="flex items-center gap-2 text-sm font-bold text-slate-900 dark:text-white">
              <Warehouse className="h-4 w-4 text-blue-600 dark:text-blue-400" />
              Warehouse Demand & Current SOH
            </h3>
            <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">{subtitle}</p>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={handleExport}
              disabled={exporting || loading}
              className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 shadow-xs transition hover:bg-slate-50 disabled:opacity-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700"
            >
              {exporting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5 text-slate-500" />}
              Export Excel
            </button>
            <button
              type="button"
              onClick={() => loadData()}
              disabled={loading}
              className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-slate-50 px-2.5 py-1.5 text-xs font-semibold text-slate-600 transition hover:bg-slate-100 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700"
              title="Refresh dataset"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
            </button>
          </div>
        </div>
      </div>

      {/* KPI Summary Cards */}
      {data && !loading && (
        <div className="grid grid-cols-2 gap-3 border-b border-slate-100 px-5 py-3.5 sm:grid-cols-5 dark:border-slate-800 bg-slate-50/50 dark:bg-slate-900/40">
          <div className="rounded-xl border border-slate-200 bg-white p-3.5 shadow-sm dark:border-slate-700 dark:bg-slate-900">
            <div className="flex items-center justify-between gap-2">
              <p className="text-[10px] font-bold uppercase tracking-wider text-slate-500 dark:text-slate-400">
                Current WH SOH
              </p>
              {filtered.length !== rows.length && (
                <span className="rounded-md bg-slate-100 px-1.5 py-0.5 text-[9px] font-semibold text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                  Filtered
                </span>
              )}
            </div>
            <p className="mt-1 text-xl font-bold tracking-tight text-slate-900 dark:text-white font-mono">
              {formatNumber(filteredTotalWhStock)}
            </p>
            <p className="mt-1 text-[11px] leading-snug text-slate-500 dark:text-slate-400">
              Units in warehouse now
            </p>
            <p className="mt-0.5 text-[10px] text-slate-400 dark:text-slate-500">
              {formatNumber(filteredInStockCount)} SKUs with stock · {formatNumber(filtered.length)} SKUs in view
            </p>
          </div>

          <div className="rounded-xl border border-rose-200/80 bg-white p-3.5 shadow-sm dark:border-rose-900/40 dark:bg-slate-900">
            <p className="text-[10px] font-bold uppercase tracking-wider text-rose-600 dark:text-rose-400">
              Shortage
            </p>
            <p className="mt-1 text-xl font-bold tracking-tight text-rose-700 dark:text-rose-300 font-mono">
              {formatNumber(data.shortageSkuCount)}
              <span className="ml-1 text-xs font-semibold text-rose-500/80">SKUs</span>
            </p>
            <p className="mt-1 text-[11px] leading-snug text-slate-500 dark:text-slate-400">
              Need more stock vs demand
            </p>
            <p className="mt-0.5 text-[10px] font-semibold text-rose-600/90 dark:text-rose-400">
              {formatNumber(data.totalShortageUnits)} units short
            </p>
          </div>

          <div className="rounded-xl border border-emerald-200/80 bg-white p-3.5 shadow-sm dark:border-emerald-900/40 dark:bg-slate-900">
            <p className="text-[10px] font-bold uppercase tracking-wider text-emerald-600 dark:text-emerald-400">
              Excess
            </p>
            <p className="mt-1 text-xl font-bold tracking-tight text-emerald-700 dark:text-emerald-300 font-mono">
              {formatNumber(data.excessSkuCount)}
              <span className="ml-1 text-xs font-semibold text-emerald-500/80">SKUs</span>
            </p>
            <p className="mt-1 text-[11px] leading-snug text-slate-500 dark:text-slate-400">
              More stock than needed
            </p>
            <p className="mt-0.5 text-[10px] font-semibold text-emerald-600/90 dark:text-emerald-400">
              {formatNumber(data.totalExcessUnits)} units excess
            </p>
          </div>

          <div className="rounded-xl border border-slate-200 bg-white p-3.5 shadow-sm dark:border-slate-700 dark:bg-slate-900">
            <p className="text-[10px] font-bold uppercase tracking-wider text-slate-500 dark:text-slate-400">
              Balanced
            </p>
            <p className="mt-1 text-xl font-bold tracking-tight text-slate-900 dark:text-white font-mono">
              {formatNumber(data.balancedSkuCount)}
              <span className="ml-1 text-xs font-semibold text-slate-400">SKUs</span>
            </p>
            <p className="mt-1 text-[11px] leading-snug text-slate-500 dark:text-slate-400">
              Stock roughly matches demand
            </p>
            <p className="mt-0.5 text-[10px] text-slate-400 dark:text-slate-500">
              No major short or excess
            </p>
          </div>

          <div className="rounded-xl border border-slate-200 bg-white p-3.5 shadow-sm dark:border-slate-700 dark:bg-slate-900">
            <p className="text-[10px] font-bold uppercase tracking-wider text-slate-500 dark:text-slate-400">
              Forecast window
            </p>
            <p className="mt-1 text-xl font-bold tracking-tight text-slate-900 dark:text-white font-mono">
              {data.coverageWeeks}
              <span className="ml-1 text-xs font-semibold text-slate-400">weeks</span>
            </p>
            <p className="mt-1 text-[11px] leading-snug text-slate-500 dark:text-slate-400">
              Ideal WH stock = weekly demand × {data.coverageWeeks}
            </p>
            <p className="mt-0.5 text-[10px] text-slate-400 dark:text-slate-500">
              Rolling cover — week numbers not fixed
            </p>
          </div>
        </div>
      )}

      {/* Filter Toolbar */}
      <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 bg-slate-50/60 px-5 py-3 dark:border-slate-800 dark:bg-slate-900/30">
        {/* Search */}
        <div className="relative min-w-[200px] flex-1">
          <Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
          <input
            type="text"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setCurrentPage(1);
            }}
            placeholder="Search SKU, Barcode, Size, Dept…"
            className="w-full rounded-xl border border-slate-200 bg-white py-1.5 pl-9 pr-3 text-xs text-slate-800 placeholder-slate-400 outline-none transition focus:border-blue-500 focus:ring-1 focus:ring-blue-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
          />
          {search && (
            <button
              type="button"
              onClick={() => setSearch('')}
              className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </div>

        {/* Department Filter (Multi + Single Select) */}
        <WarehouseFilterDropdown
          label="Department"
          options={deptFilterOptions}
          selected={deptFilter}
          onChange={(val) => {
            setDeptFilter(val);
            setCurrentPage(1);
          }}
          placeholder={`All Departments (${deptFilterOptions.length})`}
          enableSearch={deptFilterOptions.length > 5}
        />

        {/* Section Filter (Multi + Single Select) */}
        <WarehouseFilterDropdown
          label="Section"
          options={secFilterOptions}
          selected={secFilter}
          onChange={(val) => {
            setSecFilter(val);
            setCurrentPage(1);
          }}
          placeholder={`All Sections (${secFilterOptions.length})`}
          enableSearch={secFilterOptions.length > 5}
        />

        {/* Size Filter - 6 Most Common Sizes Only (Multi + Single Select) */}
        <WarehouseFilterDropdown
          label="Size"
          options={sizeFilterOptions}
          selected={sizeFilter}
          onChange={(val) => {
            setSizeFilter(val);
            setCurrentPage(1);
          }}
          placeholder="All Sizes (6 Common)"
          enableSearch={false}
        />

        {/* Status Filter (Multi + Single Select) */}
        <WarehouseFilterDropdown
          label="Status"
          options={STATUS_OPTIONS}
          selected={statusFilter}
          onChange={(val) => {
            setStatusFilter(val);
            setCurrentPage(1);
          }}
          placeholder="All"
          enableSearch={false}
        />

        {/* Sort By */}
        <select
          value={sortBy}
          onChange={(e) => {
            setSortBy(e.target.value as any);
            setCurrentPage(1);
          }}
          className="rounded-xl border border-slate-200 bg-white px-2.5 py-1.5 text-xs text-slate-700 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
        >
          <option value="stock_gap">Sort: Stock Gap</option>
          <option value="wh_stock">Sort: Current WH SOH</option>
          <option value="avg_weekly_demand">Sort: Weekly Demand</option>
          <option value="sku">Sort: SKU Code</option>
          <option value="size">Sort: Size</option>
        </select>

        {/* Sort Direction Toggle */}
        <button
          type="button"
          onClick={() => setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))}
          className="inline-flex items-center gap-1 rounded-xl border border-slate-200 bg-slate-50 px-2.5 py-1.5 text-xs font-semibold text-slate-600 transition hover:bg-slate-100 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700"
          title={`Sorting ${sortDir === 'desc' ? 'Descending' : 'Ascending'}`}
        >
          <ArrowUpDown className="h-3 w-3" />
          {sortDir.toUpperCase()}
        </button>

        {/* Results Counter & Clear */}
        <div className="ml-auto flex items-center gap-2">
          <span className="text-xs text-slate-500 dark:text-slate-400">
            Showing <strong className="text-slate-800 dark:text-slate-200">{filtered.length}</strong> items
          </span>
          {hasActiveFilters && (
            <button
              type="button"
              onClick={() => {
                setDeptFilter([]);
                setSecFilter([]);
                setSizeFilter([]);
                setStatusFilter([]);
                setSearch('');
                setCurrentPage(1);
              }}
              className="text-xs font-medium text-rose-600 underline hover:text-rose-700 dark:text-rose-400"
            >
              Clear filters
            </button>
          )}
        </div>
      </div>

      {/* Main Table */}
      <div className="p-4">
        {error ? (
          <div className="flex flex-col items-center justify-center gap-3 rounded-xl border border-dashed border-rose-200 px-4 py-8 text-center text-sm text-rose-600 dark:border-rose-900/50 dark:bg-rose-950/20 dark:text-rose-300">
            <AlertTriangle className="h-6 w-6 text-rose-500" />
            <span>{error}</span>
            <button
              onClick={() => loadData()}
              disabled={loading}
              className="inline-flex items-center gap-1.5 rounded-lg bg-rose-600 px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-rose-700 disabled:opacity-50"
            >
              <Loader2 className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : 'hidden'}`} />
              Retry Now
            </button>
          </div>
        ) : loading ? (
          <div className="flex flex-col items-center justify-center py-16 text-sm text-slate-500">
            <Loader2 className="mb-2 h-7 w-7 animate-spin text-blue-600" />
            Computing Warehouse Demand & Current SOH with Item Master…
          </div>
        ) : (
          <div>
            <div className="max-h-[560px] overflow-auto rounded-xl border border-slate-200 dark:border-slate-800">
              <table className="min-w-[1000px] w-full text-left text-[11px]">
                <thead className="sticky top-0 z-10 bg-slate-100/90 backdrop-blur-xs text-[10px] font-bold uppercase tracking-wider text-slate-600 dark:bg-slate-800/95 dark:text-slate-300">
                  <tr>
                    <th className="px-3 py-2.5">SKU / Style</th>
                    <th className="px-3 py-2.5">Barcode</th>
                    <th className="px-3 py-2.5 text-center">Size</th>
                    <th className="px-3 py-2.5">Dept / Section</th>
                    <th className="px-3 py-2.5 text-right bg-blue-50/50 dark:bg-blue-950/20 text-blue-800 dark:text-blue-300 font-extrabold">
                      Current WH SOH
                    </th>
                    <th className="px-3 py-2.5 text-right">Avg Weekly Demand</th>
                    <th className="px-3 py-2.5 text-right">Ideal WH Stock</th>
                    <th className="px-3 py-2.5 text-right">Stock Gap</th>
                    <th className="px-3 py-2.5 text-center">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 dark:divide-slate-800 font-medium">
                  {paginatedRows.length === 0 ? (
                    <tr>
                      <td colSpan={9} className="px-3 py-10 text-center text-slate-400">
                        No items match current filters
                      </td>
                    </tr>
                  ) : (
                    paginatedRows.map((r) => (
                      <tr
                        key={r.barcode}
                        className="hover:bg-slate-50/90 dark:hover:bg-slate-800/50 transition-colors"
                      >
                        {/* SKU / Style — plain text, no status color fill */}
                        <td className="px-3 py-2.5">
                          <span className="font-mono text-[11.5px] font-semibold text-slate-800 dark:text-slate-100">
                            {r.sku !== r.barcode ? r.sku : '—'}
                          </span>
                        </td>

                        {/* Barcode */}
                        <td className="px-3 py-2.5">
                          <span className="font-mono text-[11px] text-slate-600 dark:text-slate-300 bg-slate-100 dark:bg-slate-800 px-1.5 py-0.5 rounded-md">
                            {r.barcode}
                          </span>
                        </td>

                        {/* Size from Item Master */}
                        <td className="px-3 py-2.5 text-center">
                          {r.size ? (
                            <span className="inline-flex items-center justify-center font-bold px-2 py-0.5 rounded-md bg-purple-50 text-purple-700 border border-purple-200/80 dark:bg-purple-950/40 dark:text-purple-300 dark:border-purple-800/40 text-[10px]">
                              {r.size}
                            </span>
                          ) : (
                            <span className="text-slate-300 dark:text-slate-600">—</span>
                          )}
                        </td>

                        {/* Department & Section */}
                        <td className="px-3 py-2.5">
                          <div className="text-slate-800 dark:text-slate-200 font-semibold">{r.department || '—'}</div>
                          {r.section && <div className="text-[10px] text-slate-400">{r.section}</div>}
                        </td>

                        {/* Current WH SOH */}
                        <td className="px-3 py-2.5 text-right font-mono font-bold bg-blue-50/30 dark:bg-blue-950/10 text-blue-700 dark:text-blue-300">
                          {r.whStock > 0 ? (
                            <span className="inline-flex items-center gap-1 font-extrabold text-blue-700 dark:text-blue-300">
                              {r.whStock.toLocaleString()}
                            </span>
                          ) : (
                            <span className="text-slate-400 dark:text-slate-600">0</span>
                          )}
                        </td>

                        {/* Avg Weekly Demand */}
                        <td className="px-3 py-2.5 text-right font-mono text-slate-700 dark:text-slate-300">
                          {r.avgWeeklyDemand.toLocaleString(undefined, { maximumFractionDigits: 1 })}
                        </td>

                        {/* Ideal WH Stock */}
                        <td className="px-3 py-2.5 text-right font-mono text-slate-600 dark:text-slate-400">
                          {r.idealWhStock.toLocaleString()}
                        </td>

                        {/* Stock Gap */}
                        <td
                          className={`px-3 py-2.5 text-right font-mono font-bold ${
                            r.stockGap > 0
                              ? 'text-rose-600 dark:text-rose-400'
                              : r.stockGap < 0
                                ? 'text-emerald-600 dark:text-emerald-400'
                                : 'text-slate-400'
                          }`}
                        >
                          {r.stockGap > 0 ? `+${r.stockGap.toLocaleString()}` : r.stockGap.toLocaleString()}
                        </td>

                        {/* Status */}
                        <td className="px-3 py-2.5 text-center">
                          <span
                            className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-[9.5px] font-bold ${statusBadge(
                              r.status
                            )}`}
                          >
                            {r.status === 'Shortage' && <span className="h-1.5 w-1.5 rounded-full bg-rose-600 animate-pulse" />}
                            {r.status === 'Balanced' && <span className="h-1.5 w-1.5 rounded-full bg-amber-600" />}
                            {r.status === 'Excess' && <span className="h-1.5 w-1.5 rounded-full bg-emerald-600" />}
                            {r.status === 'Shortage' ? 'Need stock' : r.status === 'Balanced' ? 'OK' : 'Extra stock'}
                          </span>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>

            {/* Pagination footer */}
            <div className="mt-3 flex flex-wrap items-center justify-between gap-2 px-1 text-xs text-slate-500">
              <div className="flex items-center gap-2">
                <span>Rows per page:</span>
                <select
                  value={pageSize}
                  onChange={(e) => {
                    setPageSize(Number(e.target.value));
                    setCurrentPage(1);
                  }}
                  className="rounded-lg border border-slate-200 bg-white px-2 py-1 text-xs text-slate-700 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
                >
                  <option value={50}>50</option>
                  <option value={100}>100</option>
                  <option value={250}>250</option>
                  <option value={500}>500</option>
                  <option value={1000}>1000</option>
                </select>
                <span>
                  Showing {Math.min(filtered.length, (currentPage - 1) * pageSize + 1)}–
                  {Math.min(filtered.length, currentPage * pageSize)} of {filtered.length}
                </span>
              </div>

              {totalPages > 1 && (
                <div className="flex items-center gap-1">
                  <button
                    type="button"
                    disabled={currentPage <= 1}
                    onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
                    className="rounded-lg border border-slate-200 px-2.5 py-1 font-semibold disabled:opacity-40 hover:bg-slate-50 dark:border-slate-700 dark:hover:bg-slate-800"
                  >
                    Prev
                  </button>
                  <span className="px-2">
                    Page <strong>{currentPage}</strong> of <strong>{totalPages}</strong>
                  </span>
                  <button
                    type="button"
                    disabled={currentPage >= totalPages}
                    onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
                    className="rounded-lg border border-slate-200 px-2.5 py-1 font-semibold disabled:opacity-40 hover:bg-slate-50 dark:border-slate-700 dark:hover:bg-slate-800"
                  >
                    Next
                  </button>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

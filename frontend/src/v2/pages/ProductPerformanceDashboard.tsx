import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Calendar,
  Check,
  ChevronDown,
  Clock,
  Columns3,
  Download,
  Flame,
  IndianRupee,
  Layers,
  Loader2,
  Percent,
  RotateCcw,
  Search,
  Shirt,
  ShoppingBag,
  Sparkles,
  Store,
  Tags,
  Warehouse,
} from 'lucide-react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import DashboardLayout from '../layouts/DashboardLayout';
import { DASHBOARD_YEARS } from '../components/executive/ExecutiveHeader';
import { DashboardShell, EnterpriseKpiCard, KpiGridShell, KpiSkeletonGrid, PageHeader } from '../design-system';
import { API_URL } from '../../config';
import { formatCompactInr, formatInr } from '../components/product-performance/productData';
import { SkuHoverCard } from '../components/common/SkuHoverCard';

export type ProductPerformanceRow = {
  ref_no: string;
  sku: string;
  barcode: string;
  product_type: string;
  category: string;
  department: string;
  section: string;
  season: string;
  story_name: string;
  launch_date: string;
  current_date: string;
  shelf_life_days: number;
  style_analysis: 'FAST_MOVER' | 'SLOW_MOVER' | 'REGULAR' | 'NON_MOVER' | string;
  offer_status: string;
  costing: number;
  mrp: number;
  production_qty: number;
  production_amt_cost: number;
  production_amt_mrp: number;
  ho_soh_qty: number;
  ho_amt_cost: number;
  ho_amt_mrp: number;
  dispatch_qty: number;
  floor_soh_qty: number;
  floor_amt_cost: number;
  floor_amt_mrp: number;
  offline_sold_qty: number;
  offline_gross_amt: number;
  discount_pct: number;
  offline_net_amt: number;
  offline_st_dispatch_pct: number;
  total_sales_qty: number;
  total_net_sales: number;
  sell_through_pct: number;
  ros: number;
  wk_minus_1_qty: number;
  wk_minus_2_qty: number;
  wk_minus_3_qty: number;
  breakeven_val: number;
  sales: number;
  qty: number;
  asp: number;
  discountPct: number;
  mover_type?: string;
};

type SiteOption = {
  id: string;
  name: string;
  label: string;
};

type OverviewResponse = {
  ok: boolean;
  error?: string;
  source?: string;
  summary: {
    sales: number;
    qty: number;
    asp: number;
    discountPct: number;
    skuCount: number;
    deptCount: number;
    siteCount?: number;
    fastMoverCount?: number;
    fastMoverSales?: number;
    slowMoverCount?: number;
    slowMoverSales?: number;
    regularMoverCount?: number;
    regularMoverSales?: number;
    nonMoverCount?: number;
    nonMoverSales?: number;
  };
  byDepartment: Array<{ name: string; sales: number; qty: number }>;
  bySection: Array<{ name: string; sales: number; qty: number }>;
  fastMovers?: ProductPerformanceRow[];
  slowMovers?: ProductPerformanceRow[];
  rows: ProductPerformanceRow[];
  filters: {
    sites?: SiteOption[];
    departments: string[];
    sections: string[];
    seasons: string[];
    minDate?: string;
    maxDate?: string;
  };
  total: number;
};

// Column definitions grouped by category
type ColumnDef = {
  key: keyof ProductPerformanceRow | 'image';
  label: string;
  shortLabel?: string;
  group: 'Master' | 'Timeline' | 'Cost & Production' | 'Warehouse' | 'Floor & Dispatch' | 'Sales & Revenue' | 'Velocity';
  align: 'left' | 'right' | 'center';
  compulsory?: boolean;
};

const ALL_TABLE_COLUMNS: ColumnDef[] = [
  // Product Master
  { key: 'ref_no', label: 'Ref No.', group: 'Master', align: 'left' },
  { key: 'sku', label: 'SKU Code', group: 'Master', align: 'left', compulsory: true },
  { key: 'image', label: 'Image', group: 'Master', align: 'center', compulsory: true },
  { key: 'category', label: 'Category', group: 'Master', align: 'left' },
  { key: 'product_type', label: 'Product Type', group: 'Master', align: 'left' },
  { key: 'story_name', label: 'Story Name', group: 'Master', align: 'left' },
  { key: 'season', label: 'Season', group: 'Master', align: 'left' },
  // Timeline & Velocity status
  { key: 'launch_date', label: 'Launch Date', group: 'Timeline', align: 'left' },
  { key: 'current_date', label: 'Current Date', group: 'Timeline', align: 'left' },
  { key: 'shelf_life_days', label: 'Shelf Life (Days)', group: 'Timeline', align: 'right' },
  { key: 'style_analysis', label: 'Style Analysis', group: 'Timeline', align: 'left', compulsory: true },
  // Costing & MRP
  { key: 'costing', label: 'Costing (INR)', group: 'Cost & Production', align: 'right' },
  { key: 'mrp', label: 'MRP (INR)', group: 'Cost & Production', align: 'right' },
  { key: 'production_qty', label: 'Production Qty', group: 'Cost & Production', align: 'right' },
  { key: 'production_amt_cost', label: 'Production Amount on Cost', group: 'Cost & Production', align: 'right' },
  { key: 'production_amt_mrp', label: 'Production Amount on MRP', group: 'Cost & Production', align: 'right' },
  // Warehouse H.O.
  { key: 'ho_soh_qty', label: 'H.O. SOH QTY (WH)', group: 'Warehouse', align: 'right' },
  { key: 'ho_amt_cost', label: 'H.O. Amount on Cost', group: 'Warehouse', align: 'right' },
  { key: 'ho_amt_mrp', label: 'H.O. Amount on MRP', group: 'Warehouse', align: 'right' },
  // Dispatch & Floor
  { key: 'dispatch_qty', label: 'Dispatch Qty', group: 'Floor & Dispatch', align: 'right' },
  { key: 'floor_soh_qty', label: 'On Floor SOH', group: 'Floor & Dispatch', align: 'right' },
  { key: 'floor_amt_cost', label: 'Floor Amount on Cost', group: 'Floor & Dispatch', align: 'right' },
  { key: 'floor_amt_mrp', label: 'Floor Amount on MRP', group: 'Floor & Dispatch', align: 'right' },
  // Offline Sales
  { key: 'offline_sold_qty', label: 'Offline Sale Qty (LFS/EBO)', group: 'Sales & Revenue', align: 'right' },
  { key: 'offline_gross_amt', label: 'Offline Gross Amount', group: 'Sales & Revenue', align: 'right' },
  { key: 'discount_pct', label: 'Discounting %', group: 'Sales & Revenue', align: 'right' },
  { key: 'offline_net_amt', label: 'Offline Net Amount', group: 'Sales & Revenue', align: 'right' },
  { key: 'offline_st_dispatch_pct', label: 'Offline ST from Dispatch %', group: 'Sales & Revenue', align: 'right' },
  { key: 'total_sales_qty', label: 'Total Sales Qty', group: 'Sales & Revenue', align: 'right' },
  { key: 'total_net_sales', label: 'Total Sales (Net INR)', group: 'Sales & Revenue', align: 'right' },
  { key: 'sell_through_pct', label: 'Sell Through %', group: 'Sales & Revenue', align: 'right' },
  // Velocity & Trends
  { key: 'ros', label: 'ROS (Rate of Sale)', group: 'Velocity', align: 'right' },
  { key: 'wk_minus_1_qty', label: 'WK (-1) Sold Qty', group: 'Velocity', align: 'right' },
  { key: 'wk_minus_2_qty', label: 'WK (-2) Sold Qty', group: 'Velocity', align: 'right' },
  { key: 'wk_minus_3_qty', label: 'WK (-3) Sold Qty', group: 'Velocity', align: 'right' },
  { key: 'breakeven_val', label: 'Pending / Surplus to Breakeven', group: 'Velocity', align: 'right' },
];

const PRESETS: Record<string, string[]> = {
  all: ALL_TABLE_COLUMNS.map((c) => c.key),
  executive: [
    'ref_no',
    'sku',
    'image',
    'category',
    'style_analysis',
    'mrp',
    'costing',
    'floor_soh_qty',
    'ho_soh_qty',
    'total_sales_qty',
    'total_net_sales',
    'discount_pct',
    'sell_through_pct',
    'ros',
  ],
  inventory: [
    'ref_no',
    'sku',
    'image',
    'category',
    'ho_soh_qty',
    'ho_amt_cost',
    'ho_amt_mrp',
    'dispatch_qty',
    'floor_soh_qty',
    'floor_amt_cost',
    'floor_amt_mrp',
    'total_sales_qty',
  ],
  sales: [
    'ref_no',
    'sku',
    'image',
    'category',
    'style_analysis',
    'mrp',
    'offline_sold_qty',
    'offline_gross_amt',
    'discount_pct',
    'offline_net_amt',
    'offline_st_dispatch_pct',
    'sell_through_pct',
    'ros',
  ],
  weekly: [
    'ref_no',
    'sku',
    'image',
    'style_analysis',
    'ros',
    'wk_minus_1_qty',
    'wk_minus_2_qty',
    'wk_minus_3_qty',
    'offline_sold_qty',
    'total_net_sales',
    'breakeven_val',
  ],
};

function exportAllToCsv(filename: string, rows: ProductPerformanceRow[], visibleKeys: string[]) {
  if (!rows || rows.length === 0) return;
  const cols = ALL_TABLE_COLUMNS.filter((c) => visibleKeys.includes(c.key) && c.key !== 'image');
  const headers = cols.map((c) => c.label);
  const csvLines = [
    headers.join(','),
    ...rows.map((r) =>
      cols
        .map((c) => {
          const val = r[c.key as keyof ProductPerformanceRow];
          if (val === undefined || val === null) return '""';
          if (typeof val === 'number') return val;
          return `"${String(val).replace(/"/g, '""')}"`;
        })
        .join(',')
    ),
  ];

  const blob = new Blob([csvLines.join('\n')], { type: 'text/csv;charset=utf-8;' });
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.setAttribute('download', `${filename}.csv`);
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
}

function SkuThumbnail({ sku }: { sku: string }) {
  const [err, setErr] = useState(false);
  const [isHovered, setIsHovered] = useState(false);

  if (err || !sku) {
    return (
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-slate-100 text-slate-400 dark:bg-slate-800 dark:text-slate-500">
        <Shirt className="h-4 w-4" />
      </div>
    );
  }

  const imgUrl = `${API_URL}/product-images/${encodeURIComponent(sku)}`;

  return (
    <div
      className="relative flex items-center justify-center"
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
    >
      <img
        src={imgUrl}
        alt={sku}
        loading="lazy"
        onError={() => setErr(true)}
        className="h-9 w-9 shrink-0 cursor-pointer rounded-lg border border-slate-200 bg-white object-cover shadow-xs transition-transform hover:scale-105 dark:border-slate-700"
      />
      {isHovered && (
        <div className="absolute left-10 top-0 z-50 flex flex-col items-center rounded-xl border border-slate-200 bg-white p-2 shadow-2xl dark:border-slate-700 dark:bg-slate-900">
          <img
            src={imgUrl}
            alt={sku}
            className="h-44 w-36 rounded-lg object-cover"
          />
          <span className="mt-1 text-[11px] font-bold text-slate-700 dark:text-slate-200">{sku}</span>
        </div>
      )}
    </div>
  );
}

function MoverBadge({ type }: { type?: string }) {
  if (type === 'FAST_MOVER' || type === 'FAST') {
    return (
      <span className="inline-flex items-center gap-1 rounded-md border border-emerald-200/80 bg-emerald-50 px-2 py-0.5 text-[11px] font-semibold text-emerald-700 dark:border-emerald-800/40 dark:bg-emerald-950/40 dark:text-emerald-300">
        <Flame className="h-3 w-3 text-emerald-600 dark:text-emerald-400" />
        Fast Mover
      </span>
    );
  }
  if (type === 'SLOW_MOVER' || type === 'SLOW') {
    return (
      <span className="inline-flex items-center gap-1 rounded-md border border-amber-200/80 bg-amber-50 px-2 py-0.5 text-[11px] font-semibold text-amber-700 dark:border-amber-800/40 dark:bg-amber-950/40 dark:text-amber-300">
        <Clock className="h-3 w-3 text-amber-600 dark:text-amber-400" />
        Slow Mover
      </span>
    );
  }
  if (type === 'NON_MOVER') {
    return (
      <span className="inline-flex items-center rounded-md border border-rose-200/80 bg-rose-50 px-2 py-0.5 text-[11px] font-semibold text-rose-700 dark:border-rose-800/40 dark:bg-rose-950/40 dark:text-rose-300">
        Non Mover
      </span>
    );
  }
  return (
    <span className="inline-flex items-center rounded-md bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-400">
      Regular
    </span>
  );
}

/** Distinct bar colors — cycles for long rank lists (dept / section charts). */
const RANK_CHART_PALETTES = {
  department: ['#0ea5e9', '#22c55e', '#f59e0b', '#ef4444', '#8b5cf6', '#14b8a6', '#f97316', '#ec4899'],
  section: ['#06b6d4', '#84cc16', '#eab308', '#f43f5e', '#a855f7', '#10b981', '#fb923c', '#d946ef'],
} as const;

function RankChart({
  title,
  subtitle,
  rows,
  palette = 'department',
}: {
  title: string;
  subtitle: string;
  rows: Array<{ name: string; sales: number }>;
  palette?: keyof typeof RANK_CHART_PALETTES;
}) {
  const colors = RANK_CHART_PALETTES[palette];
  const accent = palette === 'department' ? 'from-sky-500/15 to-emerald-500/10' : 'from-cyan-500/15 to-violet-500/10';
  // Enough height so every Y label fits (Recharts skips ticks when cramped).
  const chartHeight = Math.max(260, rows.length * 28 + 40);
  const yWidth = Math.min(
    160,
    Math.max(110, ...rows.map((r) => Math.min(String(r.name || '').length * 7, 160)))
  );

  return (
    <div className="overflow-hidden rounded-2xl border border-slate-200/80 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900/60">
      <div className={`border-b border-slate-100 bg-gradient-to-r ${accent} px-5 py-4 dark:border-slate-800`}>
        <h3 className="text-sm font-semibold text-slate-900 dark:text-white">{title}</h3>
        <p className="text-xs text-slate-500">{subtitle}</p>
      </div>
      <div className="max-h-[420px] overflow-y-auto p-4">
        {rows.length === 0 ? (
          <div className="flex h-[220px] items-center justify-center text-xs text-slate-400">No data</div>
        ) : (
          <div style={{ height: chartHeight, minHeight: chartHeight }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={rows} layout="vertical" margin={{ left: 4, right: 12, top: 4, bottom: 4 }}>
                <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="#e2e8f0" />
                <XAxis type="number" tickFormatter={(v) => formatCompactInr(Number(v))} tick={{ fontSize: 10 }} />
                <YAxis
                  type="category"
                  dataKey="name"
                  width={yWidth}
                  interval={0}
                  tick={{ fontSize: 11, fill: '#334155' }}
                />
                <Tooltip
                  formatter={(v: number) => [formatInr(v), 'Sales']}
                  contentStyle={{
                    borderRadius: 12,
                    border: '1px solid #e2e8f0',
                    boxShadow: '0 8px 24px rgba(15,23,42,0.08)',
                  }}
                />
                <Bar dataKey="sales" radius={[0, 8, 8, 0]} maxBarSize={22}>
                  {rows.map((_, i) => (
                    <Cell key={`${palette}-${i}`} fill={colors[i % colors.length]} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>
    </div>
  );
}

export default function ProductPerformanceDashboard() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [data, setData] = useState<OverviewResponse | null>(null);
  const [lastRefresh, setLastRefresh] = useState(new Date());
  const [refreshKey, setRefreshKey] = useState(0);

  // Filters
  const [selectedYear, setSelectedYear] = useState<number>(2027);
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [site, setSite] = useState('');
  const [department, setDepartment] = useState('');
  const [section, setSection] = useState('');
  const [season, setSeason] = useState('');
  const [search, setSearch] = useState('');
  const [searchDraft, setSearchDraft] = useState('');
  const [moverType, setMoverType] = useState<string>('ALL');

  // Column Chooser State
  const [visibleCols, setVisibleCols] = useState<string[]>(() => PRESETS.executive);
  const [colsOpen, setColsOpen] = useState(false);

  // Table pagination & sorting
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [sortKey, setSortKey] = useState<string>('total_net_sales');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');

  const setDatePreset = (start: string, end: string, yr: number = 2027) => {
    setSelectedYear(yr);
    setStartDate(start);
    setEndDate(end);
    setPage(1);
  };

  const load = useCallback(
    (opts?: { forceRefresh?: boolean }) => {
      const controller = new AbortController();
      setLoading(true);
      setError(null);
      const qs = new URLSearchParams({
        year: String(selectedYear),
        limit: '1500',
        site,
        department,
        section,
        season,
        search,
        mover_type: moverType === 'ALL' ? '' : moverType,
        start_date: startDate,
        end_date: endDate,
      });
      if (opts?.forceRefresh) qs.set('refresh', '1');
      fetch(`${API_URL}/product-performance/overview?${qs}`, { signal: controller.signal })
        .then(async (res) => {
          const raw = await res.text();
          let json: OverviewResponse & { detail?: string };
          try {
            json = JSON.parse(raw);
          } catch {
            throw new Error(
              res.ok
                ? 'Backend returned HTML — restart API with product-performance route'
                : `Failed (${res.status})`
            );
          }
          if (!res.ok) throw new Error(String(json.detail || json.error || `Failed (${res.status})`));
          if (json.error && !json.ok) throw new Error(String(json.error));
          if (!controller.signal.aborted) {
            setData(json);
            setLoading(false);
          }
        })
        .catch((err: unknown) => {
          if (controller.signal.aborted) return;
          setError(err instanceof Error ? err.message : 'Could not load product performance');
          setLoading(false);
        });
      return () => controller.abort();
    },
    [selectedYear, site, department, section, season, search, moverType, startDate, endDate]
  );

  const prevRefreshKey = useRef(0);
  useEffect(() => {
    const forceRefresh = refreshKey !== prevRefreshKey.current && refreshKey > 0;
    prevRefreshKey.current = refreshKey;
    const abort = load({ forceRefresh });
    return abort;
  }, [load, refreshKey]);

  const summary = data?.summary;
  const activeSiteName = useMemo(() => {
    if (!site) return '';
    const match = data?.filters?.sites?.find((s) => s.id === site || s.label === site || s.name === site);
    return match ? match.label : site;
  }, [site, data]);

  const deptChart = useMemo(
    () => (data?.byDepartment ?? []).map((d) => ({ name: String(d.name || '').trim() || '—', sales: d.sales })),
    [data]
  );
  const secChart = useMemo(
    () => (data?.bySection ?? []).map((d) => ({ name: String(d.name || '').trim() || '—', sales: d.sales })),
    [data]
  );

  // Filtered & Sorted Client-side Rows
  const sortedRows = useMemo(() => {
    const rows = [...(data?.rows ?? [])];
    return rows.sort((a, b) => {
      const av = a[sortKey as keyof ProductPerformanceRow];
      const bv = b[sortKey as keyof ProductPerformanceRow];
      if (typeof av === 'number' && typeof bv === 'number') {
        return sortDir === 'asc' ? av - bv : bv - av;
      }
      return sortDir === 'asc'
        ? String(av ?? '').localeCompare(String(bv ?? ''))
        : String(bv ?? '').localeCompare(String(av ?? ''));
    });
  }, [data, sortKey, sortDir]);

  const totalPages = Math.max(1, Math.ceil(sortedRows.length / pageSize));
  const paginatedRows = useMemo(() => {
    const start = (page - 1) * pageSize;
    return sortedRows.slice(start, start + pageSize);
  }, [sortedRows, page, pageSize]);

  const toggleSort = (key: string) => {
    if (key === 'image') return;
    if (sortKey === key) {
      setSortDir(sortDir === 'asc' ? 'desc' : 'asc');
    } else {
      setSortKey(key);
      setSortDir('desc');
    }
  };

  const toggleColumn = (key: string) => {
    const compulsory = ALL_TABLE_COLUMNS.find((c) => c.key === key)?.compulsory;
    if (compulsory) return;
    setVisibleCols((prev) =>
      prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]
    );
  };

  const renderCellContent = (row: ProductPerformanceRow, key: string) => {
    switch (key) {
      case 'image':
        return <SkuThumbnail sku={row.sku} />;
      case 'sku':
        return (
          <div className="font-semibold text-slate-800 dark:text-slate-100">
            <SkuHoverCard sku={row.sku}>{row.sku}</SkuHoverCard>
          </div>
        );
      case 'ref_no':
        return row.ref_no ? (
          <SkuHoverCard sku={row.sku} className="font-mono text-xs font-semibold text-slate-700 dark:text-slate-300">
            {row.ref_no}
          </SkuHoverCard>
        ) : (
          <span className="font-mono text-xs text-slate-400">—</span>
        );
      case 'style_analysis':
        return <MoverBadge type={row.style_analysis} />;
      case 'costing':
      case 'mrp':
      case 'production_amt_cost':
      case 'production_amt_mrp':
      case 'ho_amt_cost':
      case 'ho_amt_mrp':
      case 'floor_amt_cost':
      case 'floor_amt_mrp':
      case 'offline_gross_amt':
      case 'offline_net_amt':
      case 'total_net_sales':
      case 'sales':
      case 'asp':
        return formatInr(Number(row[key as keyof ProductPerformanceRow] || 0));
      case 'breakeven_val': {
        const val = row.breakeven_val || 0;
        return (
          <span className={`font-semibold ${val >= 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-rose-600 dark:text-rose-400'}`}>
            {val >= 0 ? `+${formatInr(val)}` : `-${formatInr(Math.abs(val))}`}
          </span>
        );
      }
      case 'discount_pct':
      case 'discountPct':
      case 'sell_through_pct':
      case 'offline_st_dispatch_pct': {
        const pct = Number(row[key as keyof ProductPerformanceRow] || 0);
        return `${pct.toFixed(1)}%`;
      }
      case 'shelf_life_days':
        return `${row.shelf_life_days || 0}d`;
      case 'production_qty':
      case 'ho_soh_qty':
      case 'dispatch_qty':
      case 'floor_soh_qty':
      case 'offline_sold_qty':
      case 'total_sales_qty':
      case 'qty':
      case 'wk_minus_1_qty':
      case 'wk_minus_2_qty':
      case 'wk_minus_3_qty':
        return Number(row[key as keyof ProductPerformanceRow] || 0).toLocaleString('en-IN');
      case 'ros':
        return Number(row.ros || 0).toFixed(1);
      default:
        return String(row[key as keyof ProductPerformanceRow] ?? '—');
    }
  };

  return (
    <DashboardLayout>
      <DashboardShell>
        <PageHeader
          icon={Shirt}
          title="Master Product Performance & SOH Intelligence"
          description="Offline retail analytics · Item Master, SOH Warehouse (H.O.), Floor Stock & Velocity Trends"
          badge="Product Performance"
          lastRefresh={lastRefresh}
          onRefresh={() => {
            setRefreshKey((k) => k + 1);
            setLastRefresh(new Date());
          }}
          onExport={() =>
            exportAllToCsv(
              `Master_Product_Performance_${new Date().toISOString().slice(0, 10)}`,
              sortedRows,
              visibleCols
            )
          }
        />

        {/* Active Filters Summary Banners */}
        {(site || startDate || endDate) && (
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-blue-200 bg-blue-50/80 px-4 py-2.5 dark:border-blue-900/60 dark:bg-blue-950/40">
            <div className="flex flex-wrap items-center gap-3 text-xs font-semibold text-blue-900 dark:text-blue-200">
              {site && (
                <div className="flex items-center gap-1.5">
                  <Store className="h-4 w-4 text-blue-600 dark:text-blue-400" />
                  <span>Store:</span>
                  <span className="rounded-md bg-blue-100 px-2 py-0.5 text-blue-800 dark:bg-blue-900/80 dark:text-blue-100">
                    {activeSiteName}
                  </span>
                </div>
              )}
              {(startDate || endDate) && (
                <div className="flex items-center gap-1.5">
                  <Calendar className="h-4 w-4 text-blue-600 dark:text-blue-400" />
                  <span>Date Range:</span>
                  <span className="rounded-md bg-blue-100 px-2 py-0.5 text-blue-800 dark:bg-blue-900/80 dark:text-blue-100">
                    {startDate ? startDate : 'Beginning'} &rarr; {endDate ? endDate : 'Latest'}
                  </span>
                </div>
              )}
            </div>
            <div className="flex items-center gap-2">
              {site && (
                <button
                  type="button"
                  onClick={() => setSite('')}
                  className="inline-flex items-center gap-1 rounded-lg bg-white px-2.5 py-1 text-xs font-semibold text-blue-700 shadow-xs hover:bg-blue-50 dark:bg-slate-800 dark:text-blue-300 dark:hover:bg-slate-700"
                >
                  <RotateCcw className="h-3 w-3" />
                  Clear Store
                </button>
              )}
              {(startDate || endDate) && (
                <button
                  type="button"
                  onClick={() => {
                    setStartDate('');
                    setEndDate('');
                    setPage(1);
                  }}
                  className="inline-flex items-center gap-1 rounded-lg bg-white px-2.5 py-1 text-xs font-semibold text-blue-700 shadow-xs hover:bg-blue-50 dark:bg-slate-800 dark:text-blue-300 dark:hover:bg-slate-700"
                >
                  <RotateCcw className="h-3 w-3" />
                  Clear Dates
                </button>
              )}
            </div>
          </div>
        )}

        {/* Filters bar */}
        <div className="flex flex-col gap-3 rounded-2xl border border-slate-200/80 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900/60">
          {/* Top Row: Date Range & Quick Presets */}
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 pb-3 dark:border-slate-800">
            <div className="flex flex-wrap items-center gap-2">
              <div className="flex items-center gap-1.5 text-xs font-semibold text-slate-700 dark:text-slate-300">
                <Calendar className="h-4 w-4 text-blue-600 dark:text-blue-400" />
                <span>Date Range:</span>
              </div>
              <input
                type="date"
                value={startDate}
                min="2025-04-01"
                max={endDate || data?.filters?.maxDate}
                onChange={(e) => {
                  setStartDate(e.target.value);
                  // Custom range may span FYs — backend loads covering slices
                  setSelectedYear(0);
                  setPage(1);
                }}
                className="rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs font-medium text-slate-800 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100"
              />
              <span className="text-xs text-slate-400">to</span>
              <input
                type="date"
                value={endDate}
                min={startDate || '2025-04-01'}
                max={data?.filters?.maxDate}
                onChange={(e) => {
                  setEndDate(e.target.value);
                  setSelectedYear(0);
                  setPage(1);
                }}
                className="rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs font-medium text-slate-800 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100"
              />
            </div>

            {/* Quick FY Presets — FY26 through current FY */}
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-[11px] font-medium text-slate-400">Financial Year:</span>
              {[...DASHBOARD_YEARS].reverse().map((y) => {
                const from = `${y - 1}-04-01`;
                const to = `${y}-03-31`;
                const short = String(y).slice(-2);
                const active = selectedYear === y && (y !== DASHBOARD_YEARS[DASHBOARD_YEARS.length - 1] || startDate === from || !startDate);
                return (
                  <button
                    key={y}
                    type="button"
                    onClick={() => setDatePreset(from, to, y)}
                    className={`rounded-lg px-2.5 py-1 text-xs font-medium transition-colors ${
                      active
                        ? 'bg-blue-600 text-white shadow-xs font-semibold'
                        : 'bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700'
                    }`}
                  >
                    {`FY ${y - 1}-${short} (FY${short})`}
                  </button>
                );
              })}
              <button
                type="button"
                onClick={() => setDatePreset('', '', selectedYear)}
                className={`rounded-lg px-2.5 py-1 text-xs font-medium transition-colors ${
                  !startDate && !endDate
                    ? 'bg-slate-200 text-slate-800 dark:bg-slate-700 dark:text-slate-100'
                    : 'bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700'
                }`}
              >
                Full Year
              </button>
            </div>
          </div>

          {/* Bottom Row: Search, Store, Department, Section, Season & Actions */}
          <div className="flex flex-wrap items-end gap-3">
            <label className="flex min-w-[140px] flex-1 flex-col gap-1 text-[10px] font-semibold uppercase tracking-wider text-slate-500">
              Search SKU / Ref / Barcode
              <input
                value={searchDraft}
                onChange={(e) => setSearchDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    setSearch(searchDraft.trim());
                    setPage(1);
                  }
                }}
                placeholder="SKU, Ref No, Story…"
                className="rounded-lg border border-slate-200 px-3 py-2 text-sm font-normal text-slate-800 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100"
              />
            </label>

            {/* Store Selector */}
            <label className="flex min-w-[180px] max-w-[280px] flex-col gap-1 text-[10px] font-semibold uppercase tracking-wider text-slate-500">
              Store / Site
              <select
                value={site}
                onChange={(e) => {
                  setSite(e.target.value);
                  setPage(1);
                }}
                className="truncate rounded-lg border border-slate-200 px-3 py-2 text-sm font-normal text-slate-800 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100"
              >
                <option value="">All Stores ({data?.filters?.sites?.length ?? 0})</option>
                {(data?.filters?.sites ?? []).map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.label}
                  </option>
                ))}
              </select>
            </label>

            <label className="flex min-w-[120px] flex-col gap-1 text-[10px] font-semibold uppercase tracking-wider text-slate-500">
              Department
              <select
                value={department}
                onChange={(e) => {
                  setDepartment(e.target.value);
                  setPage(1);
                }}
                className="rounded-lg border border-slate-200 px-3 py-2 text-sm font-normal text-slate-800 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100"
              >
                <option value="">All</option>
                {(data?.filters.departments ?? []).map((d) => (
                  <option key={d} value={d}>
                    {d}
                  </option>
                ))}
              </select>
            </label>

            <label className="flex min-w-[120px] flex-col gap-1 text-[10px] font-semibold uppercase tracking-wider text-slate-500">
              Section
              <select
                value={section}
                onChange={(e) => {
                  setSection(e.target.value);
                  setPage(1);
                }}
                className="rounded-lg border border-slate-200 px-3 py-2 text-sm font-normal text-slate-800 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100"
              >
                <option value="">All</option>
                {(data?.filters.sections ?? []).map((d) => (
                  <option key={d} value={d}>
                    {d}
                  </option>
                ))}
              </select>
            </label>

            <label className="flex min-w-[100px] flex-col gap-1 text-[10px] font-semibold uppercase tracking-wider text-slate-500">
              Season
              <select
                value={season}
                onChange={(e) => {
                  setSeason(e.target.value);
                  setPage(1);
                }}
                className="rounded-lg border border-slate-200 px-3 py-2 text-sm font-normal text-slate-800 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100"
              >
                <option value="">All</option>
                {(data?.filters.seasons ?? []).map((d) => (
                  <option key={d} value={d}>
                    {d}
                  </option>
                ))}
              </select>
            </label>

            <button
              type="button"
              onClick={() => {
                setSearch(searchDraft.trim());
                setPage(1);
              }}
              className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700"
            >
              Apply
            </button>
            <button
              type="button"
              onClick={() => {
                setStartDate('');
                setEndDate('');
                setSite('');
                setDepartment('');
                setSection('');
                setSeason('');
                setSearch('');
                setSearchDraft('');
                setMoverType('ALL');
                setPage(1);
              }}
              className="rounded-lg border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300"
            >
              Clear
            </button>
          </div>
        </div>

        {error && (
          <div className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
            {error}
          </div>
        )}

        {loading && !data ? (
          <KpiSkeletonGrid count={6} />
        ) : summary ? (
          <KpiGridShell>
            <EnterpriseKpiCard
              title="Net Sales"
              value={formatCompactInr(summary.sales)}
              previous={`${summary.skuCount.toLocaleString('en-IN')} SKUs`}
              icon={IndianRupee}
              accent="bg-gradient-to-br from-blue-600 to-blue-700"
              compact
            />
            <EnterpriseKpiCard
              title="Qty Sold"
              value={summary.qty.toLocaleString('en-IN')}
              previous={`${summary.siteCount ?? 0} Stores active`}
              icon={ShoppingBag}
              accent="bg-gradient-to-br from-sky-500 to-blue-600"
              compact
            />
            <EnterpriseKpiCard
              title="🚀 Fast Movers"
              value={String(summary.fastMoverCount ?? 0)}
              previous={formatCompactInr(summary.fastMoverSales ?? 0)}
              icon={Flame}
              accent="bg-gradient-to-br from-emerald-500 to-teal-600"
              compact
            />
            <EnterpriseKpiCard
              title="🐢 Slow Movers"
              value={String(summary.slowMoverCount ?? 0)}
              previous={formatCompactInr(summary.slowMoverSales ?? 0)}
              icon={Clock}
              accent="bg-gradient-to-br from-amber-500 to-orange-500"
              compact
            />
            <EnterpriseKpiCard
              title="Avg ASP"
              value={formatInr(summary.asp)}
              previous="Sales / unit"
              icon={IndianRupee}
              accent="bg-gradient-to-br from-indigo-500 to-violet-600"
              compact
            />
            <EnterpriseKpiCard
              title="Discount %"
              value={`${summary.discountPct.toFixed(1)}%`}
              previous="Of net sales"
              icon={Percent}
              accent="bg-gradient-to-br from-rose-500 to-pink-600"
              invertGrowth
              compact
            />
          </KpiGridShell>
        ) : null}

        {loading && !data ? (
          <div className="flex h-40 items-center justify-center gap-2 text-sm text-slate-500">
            <Loader2 className="h-5 w-5 animate-spin text-blue-600" />
            Loading Master SKU Performance data…
          </div>
        ) : (
          <>
            {/* Department & Section Distribution Charts */}
            <div className="grid gap-5 lg:grid-cols-2">
              <RankChart
                title="Sales by Department"
                subtitle="From COMBINED_SALES"
                rows={deptChart}
                palette="department"
              />
              <RankChart
                title="Sales by Section"
                subtitle="From COMBINED_SALES"
                rows={secChart}
                palette="section"
              />
            </div>

            {/* Comprehensive Master SKU Performance Table */}
            <div className="overflow-hidden rounded-2xl border border-slate-200/80 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900/60">
              <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-5 py-4 dark:border-slate-800">
                <div>
                  <h3 className="text-sm font-bold text-slate-900 dark:text-white">Master SKU Performance Detail</h3>
                  <p className="text-xs text-slate-500">
                    Showing {paginatedRows.length} of {sortedRows.length.toLocaleString('en-IN')} SKUs
                    {site ? ` · Store: ${activeSiteName}` : ''}
                    {moverType !== 'ALL' ? ` · Velocity: ${moverType}` : ''}
                  </p>
                </div>

                {/* Table Header Filter and Export Actions */}
                <div className="flex flex-wrap items-center gap-2">
                  {/* Inline Mover Filter Tabs */}
                  <div className="flex items-center gap-1 rounded-xl border border-slate-200 bg-slate-100 p-1 dark:border-slate-800 dark:bg-slate-800/60">
                    <button
                      type="button"
                      onClick={() => {
                        setMoverType('ALL');
                        setPage(1);
                      }}
                      className={`rounded-lg px-2.5 py-1 text-xs font-bold transition-all ${
                        moverType === 'ALL'
                          ? 'bg-white text-blue-700 shadow-sm dark:bg-blue-600 dark:text-white'
                          : 'text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-white'
                      }`}
                    >
                      All ({summary?.skuCount ?? 0})
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setMoverType('FAST');
                        setPage(1);
                      }}
                      className={`inline-flex items-center gap-1 rounded-lg px-2.5 py-1 text-xs font-bold transition-all ${
                        moverType === 'FAST'
                          ? 'bg-emerald-600 text-white shadow-sm'
                          : 'text-emerald-700 hover:bg-emerald-50 dark:text-emerald-400 dark:hover:bg-emerald-950/30'
                      }`}
                    >
                      <Flame className="h-3 w-3" />
                      Fast ({summary?.fastMoverCount ?? 0})
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setMoverType('SLOW');
                        setPage(1);
                      }}
                      className={`inline-flex items-center gap-1 rounded-lg px-2.5 py-1 text-xs font-bold transition-all ${
                        moverType === 'SLOW'
                          ? 'bg-amber-600 text-white shadow-sm'
                          : 'text-amber-700 hover:bg-amber-50 dark:text-amber-400 dark:hover:bg-amber-950/30'
                      }`}
                    >
                      <Clock className="h-3 w-3" />
                      Slow ({summary?.slowMoverCount ?? 0})
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setMoverType('REGULAR');
                        setPage(1);
                      }}
                      className={`rounded-lg px-2.5 py-1 text-xs font-bold transition-all ${
                        moverType === 'REGULAR'
                          ? 'bg-blue-600 text-white shadow-sm'
                          : 'text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-white'
                      }`}
                    >
                      Regular ({summary?.regularMoverCount ?? 0})
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setMoverType('NON_MOVER');
                        setPage(1);
                      }}
                      className={`rounded-lg px-2.5 py-1 text-xs font-bold transition-all ${
                        moverType === 'NON_MOVER'
                          ? 'bg-rose-600 text-white shadow-sm'
                          : 'text-rose-700 hover:bg-rose-50 dark:text-rose-400 dark:hover:bg-rose-950/30'
                      }`}
                    >
                      Non Mover ({summary?.nonMoverCount ?? 0})
                    </button>
                  </div>

                  {/* Column Chooser Dropdown */}
                  <div className="relative">
                    <button
                      type="button"
                      onClick={() => setColsOpen(!colsOpen)}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 shadow-xs hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700"
                    >
                      <Columns3 className="h-3.5 w-3.5 text-blue-600 dark:text-blue-400" />
                      Columns ({visibleCols.length})
                      <ChevronDown className="h-3 w-3 text-slate-400" />
                    </button>

                    {colsOpen && (
                      <div className="absolute right-0 top-full z-50 mt-1 max-h-96 w-72 overflow-y-auto rounded-2xl border border-slate-200 bg-white p-3 shadow-xl dark:border-slate-700 dark:bg-slate-900">
                        {/* Column Presets */}
                        <div className="mb-2.5 border-b border-slate-100 pb-2 dark:border-slate-800">
                          <div className="mb-1.5 text-[10px] font-bold uppercase tracking-wider text-slate-400">
                            Quick Column Presets
                          </div>
                          <div className="flex flex-wrap gap-1">
                            {Object.entries({
                              all: 'All (37)',
                              executive: 'Executive',
                              inventory: 'Inventory & SOH',
                              sales: 'Sales & ST%',
                              weekly: 'Weekly Velocity',
                            }).map(([k, label]) => (
                              <button
                                key={k}
                                type="button"
                                onClick={() => setVisibleCols(PRESETS[k] || PRESETS.all)}
                                className="rounded-md bg-slate-100 px-2 py-0.5 text-[11px] font-semibold text-slate-700 hover:bg-blue-50 hover:text-blue-700 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700"
                              >
                                {label}
                              </button>
                            ))}
                          </div>
                        </div>

                        {/* Individual Column List */}
                        <div className="space-y-1">
                          {ALL_TABLE_COLUMNS.map((col) => {
                            const isSelected = visibleCols.includes(col.key);
                            return (
                              <label
                                key={col.key}
                                className={`flex items-center justify-between rounded-lg px-2 py-1 text-xs transition-colors ${
                                  col.compulsory ? 'opacity-80' : 'cursor-pointer hover:bg-slate-50 dark:hover:bg-slate-800'
                                }`}
                              >
                                <div className="flex items-center gap-2">
                                  <input
                                    type="checkbox"
                                    checked={isSelected}
                                    disabled={col.compulsory}
                                    onChange={() => toggleColumn(col.key)}
                                    className="rounded border-slate-300 text-blue-600 focus:ring-blue-500"
                                  />
                                  <span className="text-slate-800 dark:text-slate-200">{col.label}</span>
                                </div>
                                <span className="text-[9px] uppercase tracking-wider text-slate-400">{col.group}</span>
                              </label>
                            );
                          })}
                        </div>
                      </div>
                    )}
                  </div>

                  {/* Export Table CSV Button */}
                  <button
                    type="button"
                    onClick={() =>
                      exportAllToCsv(
                        `Master_Product_Performance_${moverType}_${site || 'All'}_${new Date().toISOString().slice(0, 10)}`,
                        sortedRows,
                        visibleCols
                      )
                    }
                    disabled={sortedRows.length === 0}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 shadow-xs hover:bg-slate-50 disabled:opacity-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700"
                  >
                    <Download className="h-3.5 w-3.5 text-slate-500" />
                    Export CSV ({sortedRows.length})
                  </button>
                </div>
              </div>

              {/* Table Body */}
              <div className="max-h-[650px] overflow-y-auto overflow-x-auto">
                <table className="w-full min-w-[1100px] text-left text-xs">
                  <thead className="sticky top-0 z-10 bg-slate-50/95 backdrop-blur-xs dark:bg-slate-900/95">
                    <tr className="border-b border-slate-200 text-[10px] uppercase tracking-wider text-slate-500 dark:border-slate-800">
                      {ALL_TABLE_COLUMNS.filter((c) => visibleCols.includes(c.key)).map((col) => (
                        <th
                          key={col.key}
                          onClick={() => toggleSort(col.key)}
                          className={`whitespace-nowrap px-3 py-2.5 font-semibold ${
                            col.key !== 'image' ? 'cursor-pointer hover:text-blue-600 dark:hover:text-blue-400' : ''
                          } ${col.align === 'right' ? 'text-right' : col.align === 'center' ? 'text-center' : 'text-left'}`}
                        >
                          <div className={`inline-flex items-center gap-1 ${col.align === 'right' ? 'justify-end' : ''}`}>
                            <span>{col.label}</span>
                            {sortKey === col.key && (
                              <span className="text-blue-600 dark:text-blue-400">
                                {sortDir === 'asc' ? '↑' : '↓'}
                              </span>
                            )}
                          </div>
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100 dark:divide-slate-800/60">
                    {paginatedRows.length === 0 ? (
                      <tr>
                        <td
                          colSpan={ALL_TABLE_COLUMNS.filter((c) => visibleCols.includes(c.key)).length}
                          className="px-4 py-12 text-center text-slate-400"
                        >
                          No products found matching the criteria.
                        </td>
                      </tr>
                    ) : (
                      paginatedRows.map((r, idx) => (
                        <tr
                          key={`${r.sku}-${r.barcode}-${idx}`}
                          className="transition-colors hover:bg-blue-50/30 dark:hover:bg-blue-950/20"
                        >
                          {ALL_TABLE_COLUMNS.filter((c) => visibleCols.includes(c.key)).map((col) => (
                            <td
                              key={col.key}
                              className={`whitespace-nowrap px-3 py-2 ${
                                col.align === 'right'
                                  ? 'text-right'
                                  : col.align === 'center'
                                  ? 'text-center'
                                  : 'text-left'
                              }`}
                            >
                              {renderCellContent(r, col.key)}
                            </td>
                          ))}
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>

              {/* Table Pagination & Row Count Footer */}
              <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 px-5 py-3 text-xs dark:border-slate-800">
                <div className="flex items-center gap-2 text-slate-500">
                  <span>Rows per page:</span>
                  <select
                    value={pageSize}
                    onChange={(e) => {
                      setPageSize(Number(e.target.value));
                      setPage(1);
                    }}
                    className="rounded border border-slate-200 bg-white px-2 py-1 text-xs dark:border-slate-700 dark:bg-slate-900"
                  >
                    <option value={15}>15</option>
                    <option value={25}>25</option>
                    <option value={50}>50</option>
                    <option value={100}>100</option>
                  </select>
                  <span>
                    Showing {paginatedRows.length} of {sortedRows.length.toLocaleString('en-IN')} SKUs
                  </span>
                </div>

                <div className="flex items-center gap-1.5">
                  <span className="text-slate-500 mr-2">
                    Page {page} of {totalPages}
                  </span>
                  <button
                    type="button"
                    disabled={page <= 1}
                    onClick={() => setPage((p) => Math.max(1, p - 1))}
                    className="rounded-lg border border-slate-200 px-2.5 py-1 font-semibold disabled:opacity-40 hover:bg-slate-50 dark:border-slate-700 dark:hover:bg-slate-800"
                  >
                    Prev
                  </button>
                  <button
                    type="button"
                    disabled={page >= totalPages}
                    onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                    className="rounded-lg border border-slate-200 px-2.5 py-1 font-semibold disabled:opacity-40 hover:bg-slate-50 dark:border-slate-700 dark:hover:bg-slate-800"
                  >
                    Next
                  </button>
                </div>
              </div>
            </div>
          </>
        )}

        <div className="flex items-center justify-center gap-2 pt-2 text-[10px] text-slate-400">
          <Sparkles className="h-3 w-3" />
          Product Performance · live COMBINED_SALES + ITEM_MASTER + WAREHOUSE_SOH
          {data?.source ? ` · ${data.source}` : ''}
        </div>
      </DashboardShell>
    </DashboardLayout>
  );
}

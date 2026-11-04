import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertCircle,
  Building2,
  Calendar,
  ChevronDown,
  ChevronRight,
  Download,
  Filter,
  IndianRupee,
  Loader2,
  MapPin,
  Percent,
  RefreshCw,
  Search,
  Store,
  Tag,
  Target,
  TrendingDown,
  TrendingUp,
  Users,
} from 'lucide-react';
import DashboardLayout from '../layouts/DashboardLayout';
import { DASHBOARD_YEARS } from '../components/executive/ExecutiveHeader';
import { DashboardShell, PageHeader } from '../design-system';
import { API_URL } from '../../config';

// ─────────────────────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────────────────────

type StoreRow = {
  site_id: string;
  site_name: string;
  city: string;
  zone: string;
  state: string;
  channel: string;
  soh: number;
  target?: number;
  gross_sales: number;
  discount_amount: number;
  discount_pct: number;
  net_sales: number;
  achievement_pct?: number;
  units: number;
  cfy_qty_sold?: number;
  soh?: number;
  dispatch_qty?: number;
  available_qty?: number;
  sales_qty_daily?: number;
  fp_sales_qty_daily?: number;
  sell_through_pct: number;
  full_price_str_pct: number;
};

type ManagerRow = {
  rank: number;
  manager: string;
  store_count: number;
  zones: string;
  cities: string;
  target?: number;
  gross_sales: number;
  discount_amount: number;
  discount_pct: number;
  net_sales: number;
  achievement_pct?: number;
  units: number;
  cfy_qty_sold?: number;
  soh: number;
  dispatch_qty?: number;
  available_qty?: number;
  sales_qty_daily?: number;
  fp_sales_qty_daily?: number;
  sell_through_pct: number;
  full_price_str_pct: number;
  sales_share_pct: number;
  stores: StoreRow[];
};

type TopPerformer = {
  manager: string;
  target?: number;
  net_sales: number;
  achievement_pct?: number;
  gross_sales: number;
  discount_pct: number;
  store_count: number;
  zones: string;
  cities: string;
  sell_through_pct?: number;
  full_price_str_pct?: number;
};

type Summary = {
  total_target?: number;
  total_gross_sales: number;
  total_net_sales: number;
  overall_achievement_pct?: number;
  total_discount_amount: number;
  overall_discount_pct: number;
  total_units: number;
  cfy_qty_sold?: number;
  total_soh?: number;
  total_dispatch_qty?: number;
  total_available_qty?: number;
  total_sales_qty_daily?: number;
  overall_sell_through_pct?: number;
  overall_full_price_str_pct?: number;
  total_managers: number;
  total_stores: number;
  top_performer: TopPerformer | null;
};

type ZoneSummaryRow = {
  zone: string;
  manager_count: number;
  store_count: number;
  target: number;
  gross_sales: number;
  net_sales: number;
  discount_amount: number;
  achievement_pct: number;
  discount_pct: number;
  soh: number;
  dispatch_qty: number;
};

function formatPcs(n: number) {
  if (n >= 100_000) return `${(n / 100_000).toFixed(2)}L pcs`;
  return `${n.toLocaleString('en-IN')} pcs`;
}

type FilterOptions = {
  zones: string[];
  states: string[];
  cities: string[];
  channels: string[];
  managers: string[];
  seasons?: string[];
};

type ApiResponse = {
  summary: Summary;
  managers: ManagerRow[];
  zone_summary?: ZoneSummaryRow[];
  filter_options: FilterOptions;
  period?: {
    year: number;
    financial_year: string;
    from_date?: string;
    to_date?: string;
    date_from?: string | null;
    date_to?: string | null;
    seasons?: string[];
    metrics_scoped?: boolean;
  };
  error?: string;
};

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

function formatInr(n: number) {
  if (n >= 1e7) return `₹${(n / 1e7).toFixed(2)}Cr`;
  if (n >= 1e5) return `₹${(n / 1e5).toFixed(2)}L`;
  if (n >= 1e3) return `₹${(n / 1e3).toFixed(1)}K`;
  return `₹${n.toFixed(0)}`;
}

function formatInrFull(n: number) {
  return `₹${n.toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
}

// ─────────────────────────────────────────────────────────────────────────────
// Channel badge (Clean Professional Styling)
// ─────────────────────────────────────────────────────────────────────────────
function ChannelBadge({ ch }: { ch: string }) {
  return (
    <span className="inline-flex items-center rounded-md border border-slate-200 bg-slate-50 px-2 py-0.5 font-mono text-[10px] font-semibold text-slate-700 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300">
      {ch}
    </span>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Multi-select chip filter
// ─────────────────────────────────────────────────────────────────────────────
function MultiSelectFilter({
  label,
  options,
  selected,
  onChange,
}: {
  label: string;
  options: string[];
  selected: string[];
  onChange: (v: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');
  const filtered = options.filter((o) => o.toLowerCase().includes(search.toLowerCase()));

  const toggle = (v: string) =>
    onChange(selected.length === 1 && selected[0] === v ? [] : [v]);

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className={`flex items-center gap-1.5 rounded-xl border px-3 py-2 text-xs font-medium transition ${
          selected.length
            ? 'border-blue-500 bg-blue-50/70 text-blue-700 dark:border-blue-600/50 dark:bg-blue-950/40 dark:text-blue-300'
            : 'border-slate-200 bg-white text-slate-700 hover:border-slate-300 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300 dark:hover:border-slate-600'
        }`}
      >
        <Filter className="h-3 w-3 text-slate-400" />
        {label}
        {selected.length > 0 && (
          <span className="ml-1 rounded-full bg-blue-600 px-1.5 py-0.2 text-[10px] font-bold text-white">
            {selected.length}
          </span>
        )}
        <ChevronDown className="h-3 w-3 opacity-60" />
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div className="absolute left-0 top-full z-50 mt-1.5 w-64 rounded-xl border border-slate-200 bg-white shadow-xl dark:border-slate-700 dark:bg-slate-900">
            <div className="border-b border-slate-100 p-2 dark:border-slate-800">
              <div className="flex items-center gap-2 rounded-lg bg-slate-50 px-2 py-1 dark:bg-slate-800">
                <Search className="h-3 w-3 text-slate-400" />
                <input
                  className="flex-1 bg-transparent text-xs text-slate-700 outline-none placeholder:text-slate-400 dark:text-slate-200"
                  placeholder="Search…"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  autoFocus
                />
              </div>
            </div>
            <div className="flex gap-1 border-b border-slate-100 px-2 py-1.5 dark:border-slate-800">
              <button
                className="text-[10px] text-slate-500 hover:underline dark:text-slate-400"
                onClick={() => onChange([])}
              >
                Clear
              </button>
            </div>
            <div className="max-h-52 overflow-y-auto p-1 custom-scrollbar">
              {filtered.length === 0 && (
                <p className="py-3 text-center text-xs text-slate-400">No results</p>
              )}
              {filtered.map((o) => (
                <button
                  key={o}
                  type="button"
                  onClick={() => toggle(o)}
                  className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-xs text-slate-700 hover:bg-slate-50 dark:text-slate-200 dark:hover:bg-slate-800"
                >
                  <span
                    className={`flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded border text-[10px] ${
                      selected.includes(o)
                        ? 'border-blue-600 bg-blue-600 text-white'
                        : 'border-slate-300 dark:border-slate-600'
                    }`}
                  >
                    {selected.includes(o) && '✓'}
                  </span>
                  {o}
                </button>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Achievement Badge Helper
// ─────────────────────────────────────────────────────────────────────────────
function AchievementBadge({ pct }: { pct?: number }) {
  const val = pct ?? 0;
  if (val <= 0) {
    return (
      <span className="inline-flex items-center rounded-md border border-slate-200 bg-slate-100 px-2 py-0.5 font-mono text-[11px] font-semibold text-slate-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-400">
        0.0%
      </span>
    );
  }
  if (val >= 100) {
    return (
      <span className="inline-flex items-center gap-1 rounded-md border border-emerald-200 bg-emerald-50 px-2 py-0.5 font-mono text-[11px] font-bold text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950/50 dark:text-emerald-300">
        {val.toFixed(1)}%
      </span>
    );
  }
  if (val >= 80) {
    return (
      <span className="inline-flex items-center gap-1 rounded-md border border-amber-200 bg-amber-50 px-2 py-0.5 font-mono text-[11px] font-bold text-amber-700 dark:border-amber-800 dark:bg-amber-950/50 dark:text-amber-300">
        {val.toFixed(1)}%
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-md border border-rose-200 bg-rose-50 px-2 py-0.5 font-mono text-[11px] font-bold text-rose-700 dark:border-rose-800 dark:bg-rose-950/50 dark:text-rose-300">
      {val.toFixed(1)}%
    </span>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Summary KPI cards (Clean Enterprise Minimalist Design)
// ─────────────────────────────────────────────────────────────────────────────
function SummaryKpis({
  summary,
  loading,
  periodLabel,
  periodRange,
}: {
  summary: Summary | null;
  loading: boolean;
  periodLabel?: string;
  periodRange?: string;
}) {
  const cards = [
    {
      label: 'Target vs Achieved',
      value: summary ? `${(summary.overall_achievement_pct ?? 0).toFixed(1)}%` : '—',
      sub: summary ? `Target: ${formatInr(summary.total_target ?? 0)} · Achieved: ${formatInr(summary.total_net_sales)}` : '',
      icon: Target,
      highlight: true,
    },
    {
      label: 'Net Sales Realized',
      value: summary ? formatInr(summary.total_net_sales) : '—',
      sub: summary ? formatInrFull(summary.total_net_sales) : '',
      icon: TrendingUp,
    },
    {
      label: 'Gross Sales (MRP)',
      value: summary ? formatInr(summary.total_gross_sales) : '—',
      sub: summary ? formatInrFull(summary.total_gross_sales) : '',
      icon: IndianRupee,
    },
    {
      label: 'Discount Given',
      value: summary ? `${summary.overall_discount_pct.toFixed(1)}%` : '—',
      sub: summary ? `${formatInr(summary.total_discount_amount)} total discount` : '',
      icon: Percent,
    },
    {
      label: 'Current SOH',
      value: summary ? formatPcs(summary.total_soh ?? 0) : '—',
      sub: summary
        ? `${(summary.total_soh ?? 0).toLocaleString('en-IN')} pcs · ${summary.total_stores} stores (filter-scoped)`
        : '',
      icon: Store,
    },
    {
      label: 'Overall Sell-Through',
      value: summary ? `${(summary.overall_sell_through_pct ?? 0).toFixed(1)}%` : '—',
      sub: summary
        ? `Dispatch ${(summary.total_dispatch_qty ?? 0).toLocaleString('en-IN')} · Total sold ${(summary.total_sales_qty_daily ?? 0).toLocaleString('en-IN')} · CFY ${(summary.cfy_qty_sold ?? summary.total_units).toLocaleString('en-IN')}`
        : '',
      icon: TrendingUp,
    },
  ];

  return (
    <div className="space-y-2">
      {(periodLabel || periodRange) && (
        <div className="flex flex-wrap items-center gap-2 px-0.5">
          <span className="inline-flex items-center gap-1.5 rounded-lg border border-blue-200 bg-blue-50 px-2.5 py-1 text-[11px] font-bold text-blue-700 dark:border-blue-700/50 dark:bg-blue-950/40 dark:text-blue-300">
            <Calendar className="h-3 w-3" />
            {periodLabel || 'FY27'}
          </span>
          {periodRange && (
            <span className="text-[11px] font-medium text-slate-500 dark:text-slate-400">
              Cards show {periodRange} (managed stores)
            </span>
          )}
        </div>
      )}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        {loading
          ? Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="h-24 animate-pulse rounded-2xl bg-slate-100 dark:bg-slate-800/60" />
            ))
          : cards.map((c) => (
              <div
                key={c.label}
                className={`rounded-2xl border p-4 shadow-2xs ${
                  c.highlight
                    ? 'border-blue-200 bg-blue-50/40 dark:border-blue-800/60 dark:bg-blue-950/20'
                    : 'border-slate-200/90 bg-white dark:border-slate-800 dark:bg-slate-900/90'
                }`}
              >
                <div className="flex items-start justify-between">
                  <div className="min-w-0">
                    <p className="truncate text-xs font-medium text-slate-500 dark:text-slate-400">{c.label}</p>
                    <p className="mt-1 text-xl font-bold text-slate-900 dark:text-white">{c.value}</p>
                    {c.sub && <p className="mt-0.5 truncate text-[11px] text-slate-400 dark:text-slate-500" title={c.sub}>{c.sub}</p>}
                  </div>
                  <div className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl ${
                    c.highlight
                      ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/60 dark:text-blue-300'
                      : 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300'
                  }`}>
                    <c.icon className="h-4 w-4" />
                  </div>
                </div>
              </div>
            ))}
      </div>
    </div>
  );
}

function getInitials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return 'M';
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

// ─────────────────────────────────────────────────────────────────────────────
// Manager card (Target vs Achievement in Outer and Dropdown Views)
// ─────────────────────────────────────────────────────────────────────────────
function ManagerCard({ mgr, totalNet }: { mgr: ManagerRow; totalNet: number; key?: string }) {
  const [expanded, setExpanded] = useState(false);
  const shareWidth = Math.min(100, mgr.sales_share_pct);
  const initials = getInitials(mgr.manager);

  return (
    <div className="rounded-2xl border border-slate-200/90 bg-white shadow-2xs transition-all hover:border-slate-300 dark:border-slate-800 dark:bg-slate-900/90 dark:hover:border-slate-700">
      {/* Summary row */}
      <button
        type="button"
        className="flex w-full items-center gap-3.5 px-4 py-3.5 text-left"
        onClick={() => setExpanded((e) => !e)}
      >
        {/* Clean Corporate Avatar */}
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-slate-200 bg-slate-100 font-mono text-xs font-bold text-slate-700 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200">
          {initials}
        </div>

        {/* Manager info */}
        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-bold text-slate-900 dark:text-white">{mgr.manager}</span>
            <span className="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-slate-50 px-2 py-0.5 text-[11px] font-medium text-slate-600 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300">
              <Store className="h-3 w-3 text-slate-400" />
              {mgr.store_count} stores
            </span>
            {mgr.zones && (
              <span className="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-slate-50 px-2 py-0.5 text-[11px] font-medium text-slate-600 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300">
                <MapPin className="h-3 w-3 text-slate-400" />
                {mgr.zones}
              </span>
            )}
          </div>
          {/* Sales share bar */}
          <div className="mt-1.5 flex items-center gap-2">
            <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
              <div
                className="h-full rounded-full bg-blue-600 dark:bg-blue-500 transition-all"
                style={{ width: `${shareWidth}%` }}
              />
            </div>
            <span className="text-[10px] font-semibold text-slate-400 dark:text-slate-500">
              {mgr.sales_share_pct.toFixed(1)}% share
            </span>
          </div>
        </div>

        {/* Outer Metrics (Bahar) */}
        <div className="hidden sm:flex items-center gap-4.5 text-right">
          <div>
            <p className="text-[10px] uppercase tracking-wider font-semibold text-slate-400 dark:text-slate-500">Target</p>
            <p className="text-xs font-semibold text-slate-700 dark:text-slate-200">{formatInr(mgr.target ?? 0)}</p>
          </div>
          <div>
            <p className="text-[10px] uppercase tracking-wider font-semibold text-slate-400 dark:text-slate-500">Net Sales</p>
            <p className="text-sm font-bold text-slate-900 dark:text-white">{formatInr(mgr.net_sales)}</p>
          </div>
          <div>
            <p className="text-[10px] uppercase tracking-wider font-semibold text-slate-400 dark:text-slate-500">Achieved</p>
            <div className="mt-0.5"><AchievementBadge pct={mgr.achievement_pct} /></div>
          </div>
          <div>
            <p className="text-[10px] uppercase tracking-wider font-semibold text-slate-400 dark:text-slate-500">Gross</p>
            <p className="text-xs font-semibold text-slate-700 dark:text-slate-200">{formatInr(mgr.gross_sales)}</p>
          </div>
          <div>
            <p className="text-[10px] uppercase tracking-wider font-semibold text-slate-400 dark:text-slate-500">Discount</p>
            <p className={`text-xs font-semibold ${mgr.discount_pct > 25 ? 'text-rose-600 dark:text-rose-400' : 'text-slate-700 dark:text-slate-200'}`}>
              {mgr.discount_pct.toFixed(1)}%
            </p>
          </div>
          <div>
            <p className="text-[10px] uppercase tracking-wider font-semibold text-slate-400 dark:text-slate-500">SOH</p>
            <p className="text-xs font-semibold text-slate-700 dark:text-slate-200">{(mgr.soh ?? 0).toLocaleString('en-IN')}</p>
          </div>
          <div>
            <p className="text-[10px] uppercase tracking-wider font-semibold text-slate-400 dark:text-slate-500">Total Dispatch</p>
            <p className="text-xs font-semibold text-slate-700 dark:text-slate-200">{(mgr.dispatch_qty ?? 0).toLocaleString('en-IN')}</p>
          </div>
          <div>
            <p className="text-[10px] uppercase tracking-wider font-semibold text-slate-400 dark:text-slate-500">Total Qty Sold</p>
            <p className="text-xs font-semibold text-slate-700 dark:text-slate-200">{(mgr.sales_qty_daily ?? 0).toLocaleString('en-IN')}</p>
          </div>
          <div>
            <p className="text-[10px] uppercase tracking-wider font-semibold text-slate-400 dark:text-slate-500">CFY Qty Sold</p>
            <p className="text-xs font-semibold text-slate-700 dark:text-slate-200">{(mgr.cfy_qty_sold ?? mgr.units).toLocaleString('en-IN')}</p>
          </div>
          <div>
            <p className="text-[10px] uppercase tracking-wider font-semibold text-slate-400 dark:text-slate-500">STR %</p>
            <p className="text-xs font-bold text-blue-600 dark:text-blue-400">{(mgr.sell_through_pct ?? 0).toFixed(1)}%</p>
          </div>
          <div>
            <p className="text-[10px] uppercase tracking-wider font-semibold text-slate-400 dark:text-slate-500">FP STR %</p>
            <p className="text-xs font-bold text-emerald-600 dark:text-emerald-400">{(mgr.full_price_str_pct ?? 0).toFixed(1)}%</p>
          </div>
        </div>

        <ChevronDown
          className={`ml-1 h-4 w-4 shrink-0 text-slate-400 transition-transform ${expanded ? 'rotate-180' : ''}`}
        />
      </button>

      {/* Mobile metrics */}
      <div className="flex sm:hidden gap-2 px-4 pb-3 text-center border-t border-slate-100 pt-2 dark:border-slate-800 overflow-x-auto">
        <div className="flex-1 min-w-[70px]">
          <p className="text-[10px] text-slate-400">Target</p>
          <p className="text-xs font-semibold text-slate-700 dark:text-slate-300">{formatInr(mgr.target ?? 0)}</p>
        </div>
        <div className="flex-1 min-w-[75px]">
          <p className="text-[10px] text-slate-400">Net Sales</p>
          <p className="text-xs font-bold text-slate-900 dark:text-white">{formatInr(mgr.net_sales)}</p>
        </div>
        <div className="flex-1 min-w-[65px]">
          <p className="text-[10px] text-slate-400">Ach %</p>
          <div className="mt-0.5"><AchievementBadge pct={mgr.achievement_pct} /></div>
        </div>
        <div className="flex-1 min-w-[55px]">
          <p className="text-[10px] text-slate-400">Disc%</p>
          <p className="text-xs font-semibold text-slate-700 dark:text-slate-200">
            {mgr.discount_pct.toFixed(1)}%
          </p>
        </div>
        <div className="flex-1 min-w-[55px]">
          <p className="text-[10px] text-slate-400">STR%</p>
          <p className="text-xs font-bold text-blue-600 dark:text-blue-400">{(mgr.sell_through_pct ?? 0).toFixed(1)}%</p>
        </div>
      </div>

      {/* Expanded store table (Dropdown Table) */}
      {expanded && (
        <div className="border-t border-slate-100 px-4 pb-4 dark:border-slate-800">
          <p className="mt-3 text-[10px] text-slate-500 dark:text-slate-400">
            STR = Period Sold ÷ Available (Opening + Dispatch + IBT + Returns) from daily movement.
            With Season/Date: Total Dispatch, Total Qty Sold and CFY Qty Sold use the same period window.
          </p>
          <div className="mt-2 overflow-x-auto rounded-xl border border-slate-200/90 dark:border-slate-800">
            <table className="min-w-full text-xs">
              <thead>
                <tr className="bg-slate-50 dark:bg-slate-800/80">
                  {[
                    'Store',
                    'Channel',
                    'City',
                    'Zone',
                    'Target',
                    'Net Sales',
                    'Ach %',
                    'SOH',
                    'Total Dispatch',
                    'Total Qty Sold',
                    'CFY Qty Sold',
                    'Sell Through %',
                    'FP STR %',
                    'Gross Sales',
                    'Disc %',
                    'Disc Amt',
                  ].map((h) => (
                    <th
                      key={h}
                      title={
                        h === 'Sell Through %'
                          ? 'Total Qty Sold ÷ Available × 100 (daily_inventory_movement)'
                          : h === 'Total Qty Sold'
                            ? 'Lifetime sales qty from daily movement (STR numerator)'
                            : h === 'CFY Qty Sold'
                              ? 'Units sold in selected current financial year'
                              : h === 'Total Dispatch'
                                ? 'Lifetime dispatch_in from daily movement'
                                : undefined
                      }
                      className="whitespace-nowrap px-3 py-2.5 text-left text-[10px] font-bold uppercase tracking-wider text-slate-500 dark:text-slate-400"
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {mgr.stores.map((s, i) => (
                  <tr key={`${s.site_id}-${i}`} className="hover:bg-slate-50/80 dark:hover:bg-slate-800/40">
                    <td className="px-3 py-2.5">
                      <div className="font-semibold text-slate-900 dark:text-slate-100">{s.site_name || s.site_id}</div>
                      <div className="font-mono text-[10px] text-slate-400">{s.site_id}</div>
                    </td>
                    <td className="px-3 py-2.5"><ChannelBadge ch={s.channel} /></td>
                    <td className="px-3 py-2.5 text-slate-600 dark:text-slate-300">{s.city}</td>
                    <td className="px-3 py-2.5 text-slate-600 dark:text-slate-300">{s.zone}</td>
                    <td className="px-3 py-2.5 font-mono font-medium text-slate-700 dark:text-slate-200">{formatInr(s.target ?? 0)}</td>
                    <td className="px-3 py-2.5 font-mono font-bold text-slate-900 dark:text-white">{formatInr(s.net_sales)}</td>
                    <td className="px-3 py-2.5"><AchievementBadge pct={s.achievement_pct} /></td>
                    <td className="px-3 py-2.5 font-mono text-slate-700 dark:text-slate-200">{(s.soh ?? 0).toLocaleString('en-IN')}</td>
                    <td className="px-3 py-2.5 font-mono text-slate-700 dark:text-slate-200">{(s.dispatch_qty ?? 0).toLocaleString('en-IN')}</td>
                    <td className="px-3 py-2.5 font-mono font-semibold text-slate-800 dark:text-slate-100">{(s.sales_qty_daily ?? 0).toLocaleString('en-IN')}</td>
                    <td className="px-3 py-2.5 font-mono text-slate-600 dark:text-slate-300">{(s.cfy_qty_sold ?? s.units).toLocaleString('en-IN')}</td>
                    <td className="px-3 py-2.5 font-mono font-semibold text-blue-600 dark:text-blue-400">{(s.sell_through_pct ?? 0).toFixed(1)}%</td>
                    <td className="px-3 py-2.5 font-mono font-semibold text-emerald-600 dark:text-emerald-400">{(s.full_price_str_pct ?? 0).toFixed(1)}%</td>
                    <td className="px-3 py-2.5 font-mono text-slate-700 dark:text-slate-200">{formatInr(s.gross_sales)}</td>
                    <td className="px-3 py-2.5 font-mono text-slate-700 dark:text-slate-200">{s.discount_pct.toFixed(1)}%</td>
                    <td className="px-3 py-2.5 font-mono text-slate-500 dark:text-slate-400">{formatInr(s.discount_amount)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Zone Matrix View (Clean Enterprise Styling with Target vs Achievement)
// ─────────────────────────────────────────────────────────────────────────────
function ZoneMatrixView({
  zoneSummary,
  managers,
}: {
  zoneSummary: ZoneSummaryRow[];
  managers: ManagerRow[];
}) {
  // Manager lists under each zone come from API manager rows (display only — KPIs from zone_summary).
  const managersByZone: Record<string, ManagerRow[]> = {};
  for (const m of managers) {
    const zones = m.zones ? m.zones.split(',').map((z) => z.trim()) : ['Unzoned'];
    for (const z of zones) {
      if (!managersByZone[z]) managersByZone[z] = [];
      if (!managersByZone[z].find((x) => x.manager === m.manager)) managersByZone[z].push(m);
    }
  }

  const zones =
    zoneSummary.length > 0
      ? zoneSummary
      : Object.keys(managersByZone)
          .sort()
          .map((zone) => ({
            zone,
            manager_count: managersByZone[zone]?.length ?? 0,
            store_count: 0,
            target: 0,
            gross_sales: 0,
            net_sales: 0,
            discount_amount: 0,
            achievement_pct: 0,
            discount_pct: 0,
            soh: 0,
            dispatch_qty: 0,
          }));

  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      {zones.map((z) => {
        const mgrList = (managersByZone[z.zone] || []).slice().sort((a, b) => b.net_sales - a.net_sales);
        return (
          <div key={z.zone} className="rounded-2xl border border-slate-200/90 bg-white dark:border-slate-800 dark:bg-slate-900 overflow-hidden shadow-2xs">
            <div className="bg-slate-900 px-4 py-3 text-white dark:bg-slate-800 border-b border-slate-800 dark:border-slate-700">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-[10px] font-bold uppercase tracking-wider text-slate-400">Zone</p>
                  <p className="text-base font-bold text-white">{z.zone}</p>
                </div>
                <div className="text-right">
                  <p className="text-[10px] font-medium text-slate-400">Target · Ach</p>
                  <p className="text-sm font-bold text-white">{formatInr(z.net_sales)} <span className="text-xs font-semibold text-emerald-400">({(z.achievement_pct ?? 0).toFixed(0)}%)</span></p>
                </div>
              </div>
              <div className="mt-2 flex gap-4 text-xs text-slate-300">
                <span><Users className="inline h-3 w-3 mr-0.5 text-slate-400" />{z.manager_count} managers</span>
                <span>Target: {formatInr(z.target)}</span>
              </div>
            </div>
            <div className="divide-y divide-slate-100 dark:divide-slate-800 max-h-72 overflow-y-auto custom-scrollbar">
              {mgrList.map((m) => (
                <div key={m.manager} className="flex items-center justify-between px-3 py-2.5 hover:bg-slate-50 dark:hover:bg-slate-800/50">
                  <div className="min-w-0">
                    <p className="truncate text-xs font-semibold text-slate-800 dark:text-slate-200">{m.manager}</p>
                    <p className="text-[10px] text-slate-400">{m.store_count} stores · {(m.cities || '').split(',').slice(0, 2).join(', ')}</p>
                  </div>
                  <div className="text-right ml-2 shrink-0">
                    <p className="text-xs font-bold text-slate-900 dark:text-white">{formatInr(m.net_sales)}</p>
                    <p className="text-[10px] font-medium text-emerald-600 dark:text-emerald-400">{(m.achievement_pct ?? 0).toFixed(1)}% ach</p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Main Page
// ─────────────────────────────────────────────────────────────────────────────
export default function StoreManagersDashboard() {
  const [data, setData] = useState<ApiResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const [exporting, setExporting] = useState(false);

  /** Default = current Indian FY end year (FY27 → 2027). */
  const [selectedYear, setSelectedYear] = useState<number>(2027);
  const [selectedZones, setSelectedZones] = useState<string[]>([]);
  const [selectedStates, setSelectedStates] = useState<string[]>([]);
  const [selectedCities, setSelectedCities] = useState<string[]>([]);
  const [selectedChannels, setSelectedChannels] = useState<string[]>([]);
  const [selectedManagers, setSelectedManagers] = useState<string[]>([]);
  const [selectedSeasons, setSelectedSeasons] = useState<string[]>([]);
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [search, setSearch] = useState('');
  const [sortBy, setSortBy] = useState('net_sales');
  const [activeTab, setActiveTab] = useState<'managers' | 'zones'>('managers');

  const buildParams = useCallback(() => {
    const p = new URLSearchParams();
    p.set('year', String(selectedYear));
    if (selectedZones.length) p.set('zones', selectedZones.join(','));
    if (selectedStates.length) p.set('states', selectedStates.join(','));
    if (selectedCities.length) p.set('cities', selectedCities.join(','));
    if (selectedChannels.length) p.set('channels', selectedChannels.join(','));
    if (selectedManagers.length) p.set('managers', selectedManagers.join(','));
    if (selectedSeasons.length) p.set('seasons', selectedSeasons.join(','));
    if (dateFrom) p.set('date_from', dateFrom);
    if (dateTo) p.set('date_to', dateTo);
    if (search.trim()) p.set('search', search.trim());
    p.set('sort_by', sortBy);
    return p.toString();
  }, [
    selectedYear,
    selectedZones,
    selectedStates,
    selectedCities,
    selectedChannels,
    selectedManagers,
    selectedSeasons,
    dateFrom,
    dateTo,
    search,
    sortBy,
  ]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_URL}/managers/overview?${buildParams()}`);
      if (!res.ok) throw new Error(`Server error ${res.status}`);
      const json: ApiResponse = await res.json();
      setData(json);
      setLastRefresh(new Date());
    } catch (e: any) {
      setError(e?.message ?? 'Failed to load manager data');
    } finally {
      setLoading(false);
    }
  }, [buildParams]);

  useEffect(() => {
    load();
  }, [load]);

  const handleExport = async () => {
    setExporting(true);
    try {
      const p = new URLSearchParams();
      p.set('year', String(selectedYear));
      if (selectedZones.length) p.set('zones', selectedZones.join(','));
      if (selectedStates.length) p.set('states', selectedStates.join(','));
      if (selectedCities.length) p.set('cities', selectedCities.join(','));
      if (selectedChannels.length) p.set('channels', selectedChannels.join(','));
      if (selectedManagers.length) p.set('managers', selectedManagers.join(','));
      if (selectedSeasons.length) p.set('seasons', selectedSeasons.join(','));
      if (dateFrom) p.set('date_from', dateFrom);
      if (dateTo) p.set('date_to', dateTo);
      p.set('sort_by', sortBy);
      const res = await fetch(`${API_URL}/managers/export?${p}`);
      if (!res.ok) throw new Error('Export failed');
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `Manager_Performance_Report_FY${selectedYear > 0 ? String(selectedYear).slice(-2) : 'All'}.xlsx`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      alert('Export failed. Please try again.');
    } finally {
      setExporting(false);
    }
  };

  const opts = data?.filter_options;
  const summary = data?.summary ?? null;
  const managers = data?.managers ?? [];
  const totalNet = summary?.total_net_sales ?? 0;

  return (
    <DashboardLayout>
      <DashboardShell>
        {/* Page Header */}
        <PageHeader
          icon={Users}
          badge="Store Network"
          title="Store Managers Performance"
          description={
            selectedYear > 0
              ? `Default view: FY ${selectedYear - 1}-${String(selectedYear).slice(-2)} (FY-${String(selectedYear).slice(-2)}). Area Managers / RMs with assigned stores, sales, discount %, and achievement.`
              : 'All Years combined. Area Managers / RMs with assigned stores, sales, discount %, and achievement.'
          }
          loading={loading}
          lastRefresh={lastRefresh ?? undefined}
          onRefresh={load}
          onExport={handleExport}
        />

        {/* Error banner */}
        {error && !loading && (
          <div className="flex items-center gap-3 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-950/20 dark:text-red-400">
            <AlertCircle className="h-4 w-4 shrink-0" />
            <span>{error}</span>
            <button onClick={load} className="ml-auto text-xs underline">Retry</button>
          </div>
        )}

        {/* KPI Cards — FY27 by default */}
        <SummaryKpis
          summary={summary}
          loading={loading}
          periodLabel={data?.period?.financial_year || (selectedYear > 0 ? `FY${String(selectedYear).slice(-2)}` : 'All Years')}
          periodRange={
            data?.period?.from_date && data?.period?.to_date
              ? `${data.period.from_date} → ${data.period.to_date}`
              : undefined
          }
        />

        {/* Filters */}
        <div className="flex flex-wrap items-center gap-2 rounded-2xl border border-slate-200/80 bg-white px-4 py-3 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <span className="mr-1 text-xs font-semibold text-slate-500 dark:text-slate-400">Filters:</span>

          {/* Financial Year Selector */}
          <div className="flex items-center gap-1.5 rounded-xl border border-slate-200 bg-slate-50 px-2.5 py-1.5 text-xs text-slate-700 shadow-2xs dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200">
            <Calendar className="h-3.5 w-3.5 text-blue-600 dark:text-blue-400 shrink-0" />
            <span className="font-semibold text-slate-500 dark:text-slate-400">FY:</span>
            <select
              value={selectedYear}
              onChange={(e) => setSelectedYear(Number(e.target.value))}
              className="bg-transparent font-bold text-slate-800 outline-none cursor-pointer text-xs dark:text-white"
            >
              {[...DASHBOARD_YEARS].reverse().map((y) => (
                <option key={y} value={y} className="dark:bg-slate-800 text-slate-900 dark:text-white">
                  {`FY ${y - 1}-${String(y).slice(-2)} (FY-${String(y).slice(-2)})`}
                </option>
              ))}
            </select>
          </div>

          {data?.period && (
            <span className="inline-flex items-center gap-1 rounded-xl border border-blue-200 bg-blue-50/80 px-2.5 py-1.5 text-[11px] font-semibold text-blue-700 dark:border-blue-700/50 dark:bg-blue-950/40 dark:text-blue-300">
              <span>{data.period.financial_year}</span>
              {data.period.from_date && data.period.to_date && (
                <span className="opacity-75 font-mono text-[10px]">({data.period.from_date} to {data.period.to_date})</span>
              )}
            </span>
          )}

          {opts && (
            <>
              <MultiSelectFilter
                label="Season"
                options={opts.seasons ?? []}
                selected={selectedSeasons}
                onChange={setSelectedSeasons}
              />
              <div className="flex items-center gap-1.5 rounded-xl border border-slate-200 bg-slate-50 px-2.5 py-1.5 text-xs text-slate-700 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200">
                <Calendar className="h-3.5 w-3.5 text-blue-600 dark:text-blue-400 shrink-0" />
                <span className="font-semibold text-slate-500 dark:text-slate-400">From</span>
                <input
                  type="date"
                  value={dateFrom}
                  min="2025-04-01"
                  max={dateTo || undefined}
                  onChange={(e) => {
                    setDateFrom(e.target.value);
                    setSelectedYear(0);
                  }}
                  className="bg-transparent font-mono text-[11px] font-semibold text-slate-800 outline-none dark:text-white"
                />
                <span className="text-slate-400">→</span>
                <span className="font-semibold text-slate-500 dark:text-slate-400">To</span>
                <input
                  type="date"
                  value={dateTo}
                  min={dateFrom || '2025-04-01'}
                  onChange={(e) => {
                    setDateTo(e.target.value);
                    setSelectedYear(0);
                  }}
                  className="bg-transparent font-mono text-[11px] font-semibold text-slate-800 outline-none dark:text-white"
                />
              </div>
              <MultiSelectFilter label="Zone" options={opts.zones} selected={selectedZones} onChange={setSelectedZones} />
              <MultiSelectFilter label="State" options={opts.states ?? []} selected={selectedStates} onChange={setSelectedStates} />
              <MultiSelectFilter label="City" options={opts.cities} selected={selectedCities} onChange={setSelectedCities} />
              <MultiSelectFilter label="Channel" options={opts.channels} selected={selectedChannels} onChange={setSelectedChannels} />
              <MultiSelectFilter label="Manager" options={opts.managers} selected={selectedManagers} onChange={setSelectedManagers} />
            </>
          )}
          {/* Search */}
          <div className="ml-auto flex items-center gap-2 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 dark:border-slate-700 dark:bg-slate-800">
            <Search className="h-3.5 w-3.5 text-slate-400" />
            <input
              className="w-32 bg-transparent text-xs text-slate-700 outline-none placeholder:text-slate-400 dark:text-slate-200"
              placeholder="Search manager / store…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          {/* Sort */}
          <select
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value)}
            className="rounded-xl border border-slate-200 bg-white px-2 py-2 text-xs text-slate-700 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
          >
            <option value="net_sales">Sort: Net Sales ↓</option>
            <option value="achievement_pct">Sort: Achievement % ↓</option>
            <option value="target">Sort: Target ↓</option>
            <option value="gross_sales">Sort: Gross Sales ↓</option>
            <option value="discount_pct">Sort: Discount % ↓</option>
            <option value="store_count">Sort: Store Count ↓</option>
            <option value="units">Sort: CFY Qty Sold ↓</option>
            <option value="sales_qty_daily">Sort: Total Qty Sold ↓</option>
            <option value="dispatch_qty">Sort: Total Dispatch ↓</option>
            <option value="sell_through_pct">Sort: Sell Through % ↓</option>
            <option value="full_price_str_pct">Sort: Full Price STR % ↓</option>
          </select>
          {/* Clear filters */}
          {(selectedZones.length || selectedStates.length || selectedCities.length || selectedChannels.length || selectedManagers.length || selectedSeasons.length || dateFrom || dateTo || search) ? (
            <button
              type="button"
              onClick={() => {
                setSelectedZones([]);
                setSelectedStates([]);
                setSelectedCities([]);
                setSelectedChannels([]);
                setSelectedManagers([]);
                setSelectedSeasons([]);
                setDateFrom('');
                setDateTo('');
                setSearch('');
              }}
              className="text-xs text-slate-400 hover:text-red-500 underline"
            >
              Clear filters
            </button>
          ) : null}
        </div>

        {(selectedSeasons.length > 0 || dateFrom || dateTo) && (
          <p className="text-[11px] text-slate-500 dark:text-slate-400 px-0.5">
            Season / date: manager list stays full. Sales ₹, Target, Dispatch, Qty Sold, CFY Qty &amp; STR all use the same season + date window (Opening included in Available so STR stays ≤100% when stock was already on hand).
          </p>
        )}

        {/* Tabs */}
        <div className="flex gap-1 rounded-xl border border-slate-200 bg-slate-50 p-1 dark:border-slate-800 dark:bg-slate-900/50 w-fit">
          {(['managers', 'zones'] as const).map((tab) => (
            <button
              key={tab}
              type="button"
              onClick={() => setActiveTab(tab)}
              className={`rounded-lg px-4 py-2 text-xs font-semibold transition ${
                activeTab === tab
                  ? 'bg-white shadow text-blue-600 dark:bg-slate-800 dark:text-blue-400'
                  : 'text-slate-500 hover:text-slate-700 dark:text-slate-400'
              }`}
            >
              {tab === 'managers' ? (
                <span className="flex items-center gap-1.5"><Users className="h-3.5 w-3.5" /> Managers View</span>
              ) : (
                <span className="flex items-center gap-1.5"><MapPin className="h-3.5 w-3.5" /> Zone Analysis</span>
              )}
            </button>
          ))}
        </div>

        {/* Content */}
        {loading ? (
          <div className="flex h-64 items-center justify-center">
            <div className="flex flex-col items-center gap-3 text-slate-400">
              <Loader2 className="h-8 w-8 animate-spin text-blue-500" />
              <p className="text-sm">Loading manager performance data…</p>
            </div>
          </div>
        ) : managers.length === 0 ? (
          <div className="flex h-48 items-center justify-center text-slate-400">
            <div className="text-center">
              <Users className="mx-auto h-10 w-10 opacity-30 mb-2" />
              <p className="text-sm">No managers found. Adjust your filters or check your data pipeline.</p>
            </div>
          </div>
        ) : activeTab === 'managers' ? (
          <div className="space-y-2">
            <div className="flex items-center justify-between px-1">
              <p className="text-xs text-slate-400">
                Showing <span className="font-semibold text-slate-700 dark:text-slate-200">{managers.length}</span> managers
                · Click any row to expand store details
              </p>
              <p className="text-xs text-slate-400">
                {exporting ? (
                  <span className="flex items-center gap-1"><Loader2 className="h-3 w-3 animate-spin" /> Exporting…</span>
                ) : null}
              </p>
            </div>
            {managers.map((m) => (
              <ManagerCard key={m.manager} mgr={m} totalNet={totalNet} />
            ))}
          </div>
        ) : (
          <ZoneMatrixView zoneSummary={data?.zone_summary ?? []} managers={managers} />
        )}
      </DashboardShell>
    </DashboardLayout>
  );
}

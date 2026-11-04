import React, { useEffect, useMemo, useState } from 'react';
import { Bot, Loader2, Sparkles } from 'lucide-react';
import { API_URL } from '../../../config';
import { formatInr } from './inventoryAnalytics';
import { matchesTableSearch, TableFilterBar, uniqueFilterOptions } from './TableFilterBar';
import { SkuHoverCard } from '../common/SkuHoverCard';

export type DispatchRow = {
  siteId: string;
  siteName: string;
  format: string;
  focusPeriod: string;
  lyPeriod: string;
  target: number;
  achieved: number;
  achievementPct: number;
  gapToTarget: number;
  department: string;
  section: string;
  salesTy: number;
  salesLy: number;
  topSkusLy: string[];
  action: string;
  reason: string;
  priorityScore: number;
};

type ApiResponse = {
  ok: boolean;
  rows: DispatchRow[];
  total: number;
  focusPeriods: string[];
  siteCount?: number;
  rollStart?: string;
  note?: string;
};

function actionClass(action: string) {
  if (action.startsWith('Send') || action.startsWith('Replenish')) {
    return 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/50 dark:text-emerald-300';
  }
  if (action.startsWith('Grow')) {
    return 'bg-violet-100 text-violet-800 dark:bg-violet-950/50 dark:text-violet-300';
  }
  if (action.startsWith('Maintain')) {
    return 'bg-sky-100 text-sky-800 dark:bg-sky-950/50 dark:text-sky-300';
  }
  return 'bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300';
}

type Props = {
  highlightSiteId?: string | null;
};

/** LY same-month + recent winners → which dept/section/SKUs to send now. */
export function DispatchPlaybookTable({ highlightSiteId }: Props) {
  const [data, setData] = useState<ApiResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [siteFilter, setSiteFilter] = useState('all');
  const [formatFilter, setFormatFilter] = useState('all');
  const [monthFilter, setMonthFilter] = useState('all');
  const [deptFilter, setDeptFilter] = useState('all');
  const [actionFilter, setActionFilter] = useState('all');

  useEffect(() => {
    if (highlightSiteId) setSiteFilter(highlightSiteId);
  }, [highlightSiteId]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    const params = new URLSearchParams({ period: 'month', limit: '500' });
    fetch(`${API_URL}/inventory-analytics/site-growth-playbook/dispatch-table?${params}`, {
      signal: controller.signal,
    })
      .then(async (res) => {
        const text = await res.text();
        let json: ApiResponse & { detail?: string };
        try {
          json = JSON.parse(text) as ApiResponse & { detail?: string };
        } catch {
          throw new Error(
            res.ok
              ? 'Invalid response from server (not JSON). Try refreshing after backend cache warm-up.'
              : `Server error (${res.status}): ${text.slice(0, 160) || res.statusText}`,
          );
        }
        if (!res.ok) throw new Error(json.detail || `Failed (${res.status})`);
        if (!controller.signal.aborted) {
          setData(json);
          setLoading(false);
        }
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setError(err instanceof Error ? err.message : 'Could not load dispatch playbook');
        setData(null);
        setLoading(false);
      });
    return () => controller.abort();
  }, []);

  const rows = data?.rows ?? [];

  const filtered = useMemo(() => {
    return rows.filter((r) => {
      if (siteFilter !== 'all' && r.siteId !== siteFilter) return false;
      if (formatFilter !== 'all' && r.format !== formatFilter) return false;
      if (monthFilter !== 'all' && r.focusPeriod !== monthFilter) return false;
      if (deptFilter !== 'all' && r.department !== deptFilter) return false;
      if (actionFilter !== 'all' && !r.action.startsWith(actionFilter)) return false;
      return matchesTableSearch(search, [
        r.siteId,
        r.siteName,
        r.format,
        r.focusPeriod,
        r.department,
        r.section,
        r.action,
        ...r.topSkusLy,
      ]);
    });
  }, [rows, siteFilter, formatFilter, monthFilter, deptFilter, actionFilter, search]);

  const siteOptions = useMemo(
    () => uniqueFilterOptions(rows.map((r) => r.siteId), 'All stores'),
    [rows]
  );
  const formatOptions = useMemo(() => uniqueFilterOptions(rows.map((r) => r.format)), [rows]);
  const monthOptions = useMemo(() => {
    const periods = data?.focusPeriods ?? [...new Set(rows.map((r) => r.focusPeriod))].sort();
    return [{ value: 'all', label: 'All months' }, ...periods.map((p) => ({ value: p, label: p }))];
  }, [data, rows]);
  const deptOptions = useMemo(() => uniqueFilterOptions(rows.map((r) => r.department)), [rows]);

  const subtitle = data
    ? `${data.siteCount ?? '—'} stores · ${(data.total ?? rows.length).toLocaleString('en-IN')} suggestions · current + last month`
    : 'LY same-month winners + recent top dept/section/SKUs';

  return (
    <div className="overflow-hidden rounded-2xl border border-slate-200/80 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900/60">
      <div className="border-b border-slate-100 px-5 py-4 dark:border-slate-800">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-900 dark:text-white">
              <Sparkles className="h-4 w-4 text-violet-500" />
              Dispatch Playbook — What to send
            </h3>
            <p className="mt-0.5 text-xs text-slate-500">
              {subtitle}
              <span className="text-slate-400">
                {' '}
                · Top LY dept/section/SKUs (high sales / STR ≥85%) vs this year pace
              </span>
            </p>
          </div>
          <span className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2.5 py-1 text-[10px] font-semibold text-slate-600 dark:bg-slate-800 dark:text-slate-300">
            <Bot className="h-3 w-3" />
            LY winners
          </span>
        </div>
      </div>

      <div className="border-b border-slate-100 px-4 py-3 dark:border-slate-800">
        <TableFilterBar
          search={search}
          onSearchChange={setSearch}
          searchPlaceholder="Search store, dept, SKU…"
          resultCount={loading ? undefined : filtered.length}
          selects={[
            { id: 'site', label: 'Store', value: siteFilter, options: siteOptions, onChange: setSiteFilter },
            { id: 'format', label: 'Format', value: formatFilter, options: formatOptions, onChange: setFormatFilter },
            { id: 'month', label: 'Month', value: monthFilter, options: monthOptions, onChange: setMonthFilter },
            { id: 'dept', label: 'Dept', value: deptFilter, options: deptOptions, onChange: setDeptFilter },
            {
              id: 'action',
              label: 'Action',
              value: actionFilter,
              options: [
                { value: 'all', label: 'All' },
                { value: 'Send', label: 'Send more' },
                { value: 'Grow', label: 'Grow' },
                { value: 'Replenish', label: 'Replenish' },
                { value: 'Maintain', label: 'Maintain' },
              ],
              onChange: setActionFilter,
            },
          ]}
        />
      </div>

      <div className="p-4">
        {error ? (
          <div className="rounded-xl border border-dashed border-rose-200 px-4 py-8 text-center text-sm text-rose-600">
            {error}
          </div>
        ) : loading ? (
          <div className="flex flex-col items-center justify-center py-12 text-sm text-slate-500">
            <Loader2 className="mb-2 h-6 w-6 animate-spin text-blue-500" />
            Building dispatch playbook for all stores…
            <span className="mt-1 text-[10px] text-slate-400">First run may take a few minutes; then instant from cache</span>
          </div>
        ) : data?.note && rows.length === 0 ? (
          <div className="rounded-xl border border-dashed border-slate-200 px-4 py-8 text-center text-sm text-slate-500">
            {data.note}
          </div>
        ) : (
          <div className="max-h-[520px] overflow-auto rounded-xl border border-slate-200 dark:border-slate-700">
            <table className="min-w-[1280px] w-full text-left text-[11px]">
              <thead className="sticky top-0 z-10 bg-slate-50 text-[10px] uppercase tracking-wide text-slate-500 dark:bg-slate-800/90">
                <tr>
                  <th className="px-3 py-2.5">Store</th>
                  <th className="px-3 py-2.5">Month</th>
                  <th className="px-3 py-2.5">Dept / Section</th>
                  <th className="px-3 py-2.5 text-right">Ach %</th>
                  <th className="px-3 py-2.5 text-right">Gap</th>
                  <th className="px-3 py-2.5 text-right">Sales CY</th>
                  <th className="px-3 py-2.5 text-right">Sales LY</th>
                  <th className="px-3 py-2.5">Top LY SKUs</th>
                  <th className="px-3 py-2.5">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {filtered.length === 0 ? (
                  <tr>
                    <td colSpan={9} className="px-3 py-8 text-center text-slate-400">
                      No rows match filters
                    </td>
                  </tr>
                ) : (
                  filtered.map((r) => (
                    <tr
                      key={`${r.siteId}-${r.focusPeriod}-${r.department}-${r.section}-${r.action}`}
                      className={`hover:bg-slate-50/80 dark:hover:bg-slate-800/40 ${
                        highlightSiteId === r.siteId ? 'bg-violet-50/60 dark:bg-violet-950/20' : ''
                      }`}
                    >
                      <td className="px-3 py-2.5">
                        <div className="font-semibold text-slate-800 dark:text-slate-100">{r.siteId}</div>
                        <div className="max-w-[120px] truncate text-[10px] text-slate-500" title={r.siteName}>
                          {r.siteName}
                        </div>
                        <div className="text-[9px] text-slate-400">{r.format}</div>
                      </td>
                      <td className="px-3 py-2.5">
                        <div className="font-medium">{r.focusPeriod}</div>
                        <div className="text-[9px] text-slate-400">LY {r.lyPeriod}</div>
                      </td>
                      <td className="px-3 py-2.5">
                        <div className="font-medium">{r.department}</div>
                        <div className="text-[10px] text-slate-500">{r.section}</div>
                      </td>
                      <td className="px-3 py-2.5 text-right font-semibold">{r.achievementPct}%</td>
                      <td className="px-3 py-2.5 text-right text-rose-600">{formatInr(r.gapToTarget)}</td>
                      <td className="px-3 py-2.5 text-right">{formatInr(r.salesTy)}</td>
                      <td className="px-3 py-2.5 text-right text-slate-500">{formatInr(r.salesLy)}</td>
                      <td className="max-w-[160px] px-3 py-2.5 text-[10px]">
                        {r.topSkusLy.length > 0 ? (
                          <div className="flex flex-wrap gap-1">
                            {r.topSkusLy.map((skuCode, idx) => (
                              <SkuHoverCard key={idx} sku={skuCode} className="text-[10px]">
                                {skuCode}
                              </SkuHoverCard>
                            ))}
                          </div>
                        ) : (
                          '—'
                        )}
                      </td>
                      <td className="px-3 py-2.5">
                        <span className={`inline-flex rounded-full px-2 py-0.5 text-[9px] font-semibold ${actionClass(r.action)}`}>
                          {r.action}
                        </span>
                        <p className="mt-1 max-w-[180px] text-[9px] leading-snug text-slate-500">{r.reason}</p>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

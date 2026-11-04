import React, { useEffect, useMemo, useState } from 'react';
import { Bot, Loader2, Warehouse } from 'lucide-react';
import { API_URL } from '../../../config';
import { formatInr, formatNumber } from './inventoryAnalytics';
import { matchesTableSearch, TableFilterBar, uniqueFilterOptions } from './TableFilterBar';

type Row = {
  department: string;
  section: string;
  whSohCy: number;
  networkSohLy: number;
  whTargetFromLy: number;
  vsNetworkLyPct: number | null;
  salesCy: number;
  salesLy: number;
  salesGrowthPct: number | null;
  suggestedIncreasePct: number;
  skuCount: number;
  action: string;
  reason: string;
};

type ApiResponse = {
  ok: boolean;
  focusMonth?: string;
  lyMonth?: string;
  whAsOf?: string;
  totalWhSohCy?: number;
  whNetworkRatioPct?: number;
  note?: string;
  rows: Row[];
  total: number;
};

function actionClass(action: string) {
  if (action.startsWith('Increase')) return 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/50 dark:text-emerald-300';
  if (action.startsWith('Reduce')) return 'bg-rose-100 text-rose-800 dark:bg-rose-950/50 dark:text-rose-300';
  return 'bg-sky-100 text-sky-800 dark:bg-sky-950/50 dark:text-sky-300';
}

/** WH dept/section — latest register vs store-network LY + suggested increase %. */
export function WhSohDeptInsightsTable() {
  const [data, setData] = useState<ApiResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [deptFilter, setDeptFilter] = useState('all');
  const [actionFilter, setActionFilter] = useState('all');

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    fetch(`${API_URL}/inventory-analytics/wh-soh-dept-insights?limit=500`, { signal: controller.signal })
      .then(async (res) => {
        const raw = await res.text();
        let json: ApiResponse & { detail?: string; error?: string };
        try {
          json = JSON.parse(raw) as ApiResponse & { detail?: string; error?: string };
        } catch {
          throw new Error(
            res.ok
              ? 'Backend returned HTML instead of JSON — restart API (wrong process on :5001?)'
              : `Failed (${res.status}): endpoint missing or backend not running`
          );
        }
        if (!res.ok) throw new Error(String(json.detail || json.error || `Failed (${res.status})`));
        if (json.error) throw new Error(String(json.error));
        if (!controller.signal.aborted) {
          setData(json);
          setLoading(false);
        }
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setError(err instanceof Error ? err.message : 'Could not load WH dept insights');
        setLoading(false);
      });
    return () => controller.abort();
  }, []);

  const rows = data?.rows ?? [];

  const filtered = useMemo(() => {
    return rows.filter((r) => {
      if (deptFilter !== 'all' && r.department !== deptFilter) return false;
      if (actionFilter !== 'all' && !r.action.startsWith(actionFilter)) return false;
      return matchesTableSearch(search, [r.department, r.section, r.action, r.reason]);
    });
  }, [rows, deptFilter, actionFilter, search]);

  const deptOptions = useMemo(() => uniqueFilterOptions(rows.map((r) => r.department)), [rows]);

  const subtitle = data
    ? `WH as of ${data.whAsOf ?? '—'} · LY month ${data.lyMonth ?? '—'} · total WH SOH ${formatNumber(data.totalWhSohCy ?? 0)}`
    : 'Latest warehouse register vs last year';

  return (
    <div className="overflow-hidden rounded-2xl border border-slate-200/80 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900/60">
      <div className="border-b border-slate-100 px-5 py-4 dark:border-slate-800">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-900 dark:text-white">
              <Warehouse className="h-4 w-4 text-blue-600" />
              WH SOH — Dept / Section vs LY (Increase %)
            </h3>
            <p className="mt-0.5 text-xs text-slate-500">{subtitle}</p>
            {data?.note ? <p className="mt-1 text-[10px] text-slate-400">{data.note}</p> : null}
          </div>
          <span className="inline-flex items-center gap-1 rounded-full bg-blue-100 px-2.5 py-1 text-[10px] font-semibold text-blue-700 dark:bg-blue-950/60 dark:text-blue-300">
            <Bot className="h-3 w-3" />
            WH planning
          </span>
        </div>
      </div>

      <div className="border-b border-slate-100 px-4 py-3 dark:border-slate-800">
        <TableFilterBar
          search={search}
          onSearchChange={setSearch}
          searchPlaceholder="Search dept, section…"
          resultCount={loading ? undefined : filtered.length}
          selects={[
            { id: 'dept', label: 'Department', value: deptFilter, options: deptOptions, onChange: setDeptFilter },
            {
              id: 'action',
              label: 'Action',
              value: actionFilter,
              options: [
                { value: 'all', label: 'All' },
                { value: 'Increase', label: 'Increase' },
                { value: 'Reduce', label: 'Reduce' },
                { value: 'Maintain', label: 'Maintain' },
              ],
              onChange: setActionFilter,
            },
          ]}
        />
      </div>

      <div className="p-4">
        {error ? (
          <div className="rounded-xl border border-dashed border-rose-200 px-4 py-8 text-center text-sm text-rose-600">{error}</div>
        ) : loading ? (
          <div className="flex flex-col items-center justify-center py-12 text-sm text-slate-500">
            <Loader2 className="mb-2 h-6 w-6 animate-spin text-blue-500" />
            Loading WH dept comparison…
          </div>
        ) : (
          <div className="max-h-[480px] overflow-auto rounded-xl border border-slate-200 dark:border-slate-700">
            <table className="min-w-[1100px] w-full text-left text-[11px]">
              <thead className="sticky top-0 z-10 bg-slate-50 text-[10px] uppercase tracking-wide text-slate-500 dark:bg-slate-800/90">
                <tr>
                  <th className="px-3 py-2.5">Department / Section</th>
                  <th className="px-3 py-2.5 text-right">WH SOH (CY)</th>
                  <th className="px-3 py-2.5 text-right">Network SOH (LY)</th>
                  <th className="px-3 py-2.5 text-right">WH target</th>
                  <th className="px-3 py-2.5 text-right">Suggest +%</th>
                  <th className="px-3 py-2.5 text-right">Sales CY</th>
                  <th className="px-3 py-2.5 text-right">Sales LY</th>
                  <th className="px-3 py-2.5">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {filtered.length === 0 ? (
                  <tr>
                    <td colSpan={8} className="px-3 py-8 text-center text-slate-400">No rows match filters</td>
                  </tr>
                ) : (
                  filtered.map((r) => (
                    <tr key={`${r.department}-${r.section}`} className="hover:bg-slate-50/80 dark:hover:bg-slate-800/40">
                      <td className="px-3 py-2.5">
                        <div className="font-medium">{r.department}</div>
                        <div className="text-[10px] text-slate-500">{r.section} · {r.skuCount} SKUs</div>
                      </td>
                      <td className="px-3 py-2.5 text-right font-semibold">{formatNumber(r.whSohCy)}</td>
                      <td className="px-3 py-2.5 text-right text-slate-500">{formatNumber(r.networkSohLy)}</td>
                      <td className="px-3 py-2.5 text-right">{formatNumber(r.whTargetFromLy)}</td>
                      <td className="px-3 py-2.5 text-right font-bold text-emerald-700">
                        {r.suggestedIncreasePct > 0 ? `+${r.suggestedIncreasePct}%` : '—'}
                      </td>
                      <td className="px-3 py-2.5 text-right">{formatInr(r.salesCy)}</td>
                      <td className="px-3 py-2.5 text-right text-slate-500">{formatInr(r.salesLy)}</td>
                      <td className="px-3 py-2.5">
                        <span className={`inline-flex rounded-full px-2 py-0.5 text-[9px] font-semibold ${actionClass(r.action)}`}>
                          {r.action}
                        </span>
                        <p className="mt-1 max-w-[220px] text-[9px] leading-snug text-slate-500">{r.reason}</p>
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

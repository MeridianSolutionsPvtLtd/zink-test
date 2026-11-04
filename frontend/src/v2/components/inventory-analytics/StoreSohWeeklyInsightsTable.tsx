import React, { useEffect, useMemo, useState } from 'react';
import { Bot, Loader2, Sparkles } from 'lucide-react';
import { API_URL } from '../../../config';
import { formatNumber, pctVs } from './inventoryAnalytics';
import { matchesTableSearch, TableFilterBar, uniqueFilterOptions } from './TableFilterBar';
import type { StockRow } from './types';

type WeekMeta = {
  current_week_ending: string;
  previous_week_ending: string;
  ly_week_ending: string;
  week_label: string;
};

type DeptRow = {
  department: string;
  section: string;
  skuCount: number;
  storeCount: number;
  sohTy: number;
  sohLy: number;
  vsLy: number;
  salesTy: number;
  salesLy: number;
  sellThroughProxy: number;
  action: string;
};

type DetailRow = {
  siteId: string;
  barcode: string;
  sku: string;
  department: string;
  section: string;
  sohTy: number;
  sohLy: number;
  sohPrevWeek: number;
  vsLy: number;
  wow: number;
  salesTy: number;
  salesLy: number;
  sellThroughTy: number | null;
  action: string;
};

type ApiResponse = {
  ok: boolean;
  week?: WeekMeta;
  detail?: DetailRow[];
  dept_summary?: DeptRow[];
  note?: string;
  message?: string;
};

function Delta({ value }: { value: number }) {
  return (
    <span className={`font-semibold ${value >= 0 ? 'text-emerald-600' : 'text-rose-600'}`}>
      {value >= 0 ? '+' : ''}
      {value.toFixed(1)}%
    </span>
  );
}

function actionClass(action: string) {
  if (action.startsWith('Increase') || action.startsWith('Replenish')) {
    return 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/50 dark:text-emerald-300';
  }
  if (action.startsWith('Reduce') || action.startsWith('RTV') || action.startsWith('Markdown')) {
    return 'bg-rose-100 text-rose-800 dark:bg-rose-950/50 dark:text-rose-300';
  }
  if (action.startsWith('Maintain')) {
    return 'bg-sky-100 text-sky-800 dark:bg-sky-950/50 dark:text-sky-300';
  }
  return 'bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300';
}

type Props = {
  siteId: string;
  departments: string[];
  sections: string[];
  search: string;
  rows: StockRow[];
  weekLabel: string;
};

/** Real weekly SOH TY vs LY + dept/section AI recommendations from historical SOH. */
export function StoreSohWeeklyInsightsTable({
  siteId,
  departments,
  sections,
  search,
  rows,
  weekLabel,
}: Props) {
  const [data, setData] = useState<ApiResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deptSearch, setDeptSearch] = useState('');
  const [deptFilter, setDeptFilter] = useState('all');
  const [sectionFilter, setSectionFilter] = useState('all');
  const [deptActionFilter, setDeptActionFilter] = useState('all');
  const [detailSearch, setDetailSearch] = useState('');
  const [detailStoreFilter, setDetailStoreFilter] = useState('all');
  const [detailDeptFilter, setDetailDeptFilter] = useState('all');
  const [detailSectionFilter, setDetailSectionFilter] = useState('all');
  const [detailActionFilter, setDetailActionFilter] = useState('all');

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    const params = new URLSearchParams({ site_id: siteId || 'all', limit: '120' });
    if (departments.length) params.set('departments', departments.join(','));
    if (sections.length) params.set('sections', sections.join(','));
    if (search.trim()) params.set('search', search.trim());

    fetch(`${API_URL}/inventory-analytics/store-soh-weekly-insights?${params}`, {
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
              ? 'Invalid response from server (not JSON). Historical SOH snapshot may still be building.'
              : `Server error (${res.status}): ${text.slice(0, 160) || res.statusText}`,
          );
        }
        if (!res.ok) throw new Error(json.detail || json.message || `Failed (${res.status})`);
        if (!controller.signal.aborted) {
          setData(json);
          setLoading(false);
        }
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setError(err instanceof Error ? err.message : 'Could not load weekly SOH insights');
        setData(null);
        setLoading(false);
      });
    return () => controller.abort();
  }, [siteId, departments, sections, search]);

  const liveSohMap = useMemo(() => {
    const map = new Map<string, number>();
    rows.forEach((r) => {
      const key = `${r.site_id}|${(r.barcode || r.sku || '').trim()}`;
      map.set(key, (map.get(key) ?? 0) + r.qty);
    });
    return map;
  }, [rows]);

  const detail = useMemo(() => {
    const list = data?.detail ?? [];
    return list.map((row) => {
      const liveKey = `${row.siteId}|${row.barcode}`;
      const live = liveSohMap.get(liveKey);
      if (live === undefined) return row;
      return {
        ...row,
        sohTy: live,
        vsLy: pctVs(live, row.sohLy),
        wow: pctVs(live, row.sohPrevWeek),
      };
    });
  }, [data, liveSohMap]);

  const liveDeptSohMap = useMemo(() => {
    const map = new Map<string, { soh: number; stores: Set<string>; skus: Set<string> }>();
    rows.forEach((r) => {
      const d = (r.department || '').trim();
      const s = (r.section || '').trim();
      if (!d) return;
      const key = `${d}|${s}`;
      const cur = map.get(key) ?? { soh: 0, stores: new Set<string>(), skus: new Set<string>() };
      cur.soh += r.qty;
      if (r.site_id) cur.stores.add(r.site_id.trim());
      if (r.sku || r.barcode) cur.skus.add((r.sku || r.barcode).trim());
      map.set(key, cur);
    });
    return map;
  }, [rows]);

  const deptSummary = useMemo(() => {
    const base = data?.dept_summary ?? [];
    if (!liveDeptSohMap.size) return base;
    return base.map((r) => {
      const key = `${r.department}|${r.section}`;
      const live = liveDeptSohMap.get(key);
      if (!live) return r;
      const sohTy = live.soh;
      return {
        ...r,
        sohTy,
        storeCount: live.stores.size || r.storeCount,
        skuCount: live.skus.size || r.skuCount,
        vsLy: pctVs(sohTy, r.sohLy),
      };
    });
  }, [data, liveDeptSohMap]);

  const week = data?.week;

  const filteredDeptSummary = useMemo(() => {
    return deptSummary.filter((r) => {
      if (deptFilter !== 'all' && r.department !== deptFilter) return false;
      if (sectionFilter !== 'all' && r.section !== sectionFilter) return false;
      if (deptActionFilter !== 'all' && !r.action.startsWith(deptActionFilter)) return false;
      return matchesTableSearch(deptSearch, [r.department, r.section, r.action]);
    });
  }, [deptSummary, deptFilter, sectionFilter, deptActionFilter, deptSearch]);

  const filteredDetail = useMemo(() => {
    return detail.filter((r) => {
      if (detailStoreFilter !== 'all' && r.siteId !== detailStoreFilter) return false;
      if (detailDeptFilter !== 'all' && r.department !== detailDeptFilter) return false;
      if (detailSectionFilter !== 'all' && r.section !== detailSectionFilter) return false;
      if (detailActionFilter !== 'all' && !r.action.startsWith(detailActionFilter)) return false;
      return matchesTableSearch(detailSearch, [r.siteId, r.sku, r.barcode, r.department, r.section, r.action]);
    });
  }, [detail, detailStoreFilter, detailDeptFilter, detailSectionFilter, detailActionFilter, detailSearch]);

  const deptOptions = useMemo(() => uniqueFilterOptions(deptSummary.map((r) => r.department)), [deptSummary]);
  const sectionOptions = useMemo(() => uniqueFilterOptions(deptSummary.map((r) => r.section)), [deptSummary]);
  const storeOptions = useMemo(() => uniqueFilterOptions(detail.map((r) => r.siteId), 'All stores'), [detail]);
  const detailDeptOptions = useMemo(() => uniqueFilterOptions(detail.map((r) => r.department)), [detail]);
  const detailSectionOptions = useMemo(() => uniqueFilterOptions(detail.map((r) => r.section)), [detail]);

  const subtitle = week
    ? `${week.week_label} · week ending ${week.current_week_ending} · LY week ${week.ly_week_ending}`
    : weekLabel;

  return (
    <div className="rounded-2xl border border-slate-200/80 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900/60">
      <div className="border-b border-slate-100 px-5 py-4 dark:border-slate-800">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <h3 className="text-sm font-semibold text-slate-900 dark:text-white">
              Store SOH — Article-wise (Same Week LY)
            </h3>
            <p className="mt-0.5 text-xs text-slate-500">{subtitle}</p>
          </div>
          <span className="inline-flex items-center gap-1 rounded-full bg-indigo-100 px-2.5 py-1 text-[10px] font-semibold text-indigo-700 dark:bg-indigo-950/60 dark:text-indigo-300">
            <Bot className="h-3 w-3" />
            AI insights
          </span>
        </div>
      </div>

      <div className="p-4 space-y-5">
        {error ? (
          <div className="rounded-xl border border-dashed border-rose-200 px-4 py-8 text-center text-sm text-rose-600">
            {error}
          </div>
        ) : loading ? (
          <div className="flex flex-col items-center justify-center py-12 text-sm text-slate-500">
            <Loader2 className="mb-2 h-6 w-6 animate-spin text-blue-500" />
            Building weekly SOH snapshot…
          </div>
        ) : (
          <>
            {deptSummary.length > 0 ? (
              <div>
                <div className="mb-2 flex items-center gap-1.5">
                  <Sparkles className="h-3.5 w-3.5 text-violet-500" />
                  <h4 className="text-xs font-semibold text-slate-800 dark:text-slate-100">
                    Department / Section — where to increase for better sales
                  </h4>
                </div>
                <TableFilterBar
                  search={deptSearch}
                  onSearchChange={setDeptSearch}
                  searchPlaceholder="Search dept, section…"
                  resultCount={filteredDeptSummary.length}
                  selects={[
                    { id: 'dept', label: 'Department', value: deptFilter, options: deptOptions, onChange: setDeptFilter },
                    { id: 'section', label: 'Section', value: sectionFilter, options: sectionOptions, onChange: setSectionFilter },
                    {
                      id: 'action',
                      label: 'Action',
                      value: deptActionFilter,
                      options: [
                        { value: 'all', label: 'All' },
                        { value: 'Increase', label: 'Increase' },
                        { value: 'Reduce', label: 'Reduce' },
                        { value: 'Maintain', label: 'Maintain' },
                        { value: 'Monitor', label: 'Monitor' },
                      ],
                      onChange: setDeptActionFilter,
                    },
                  ]}
                />
                <div className="overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-700">
                  <table className="min-w-[920px] w-full text-left text-[11px]">
                    <thead className="bg-slate-50 text-[10px] uppercase tracking-wide text-slate-500 dark:bg-slate-800/70">
                      <tr>
                        <th className="px-3 py-2.5">Department</th>
                        <th className="px-3 py-2.5">Section</th>
                        <th className="px-3 py-2.5 text-right">Stores</th>
                        <th className="px-3 py-2.5 text-right">SKUs</th>
                        <th className="px-3 py-2.5 text-right">SOH CY</th>
                        <th className="px-3 py-2.5 text-right">SOH LY</th>
                        <th className="px-3 py-2.5 text-right">vs LY</th>
                        <th className="px-3 py-2.5 text-right">Sales CY</th>
                        <th className="px-3 py-2.5 text-right">Sales LY</th>
                        <th className="px-3 py-2.5">AI action</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                      {filteredDeptSummary.map((r) => (
                        <tr key={`${r.department}-${r.section}`} className="hover:bg-slate-50/80 dark:hover:bg-slate-800/40">
                          <td className="px-3 py-2.5 font-medium">{r.department}</td>
                          <td className="px-3 py-2.5">{r.section}</td>
                          <td className="px-3 py-2.5 text-right">{r.storeCount}</td>
                          <td className="px-3 py-2.5 text-right">{r.skuCount}</td>
                          <td className="px-3 py-2.5 text-right font-semibold">{formatNumber(r.sohTy)}</td>
                          <td className="px-3 py-2.5 text-right text-slate-500">{formatNumber(r.sohLy)}</td>
                          <td className="px-3 py-2.5 text-right"><Delta value={r.vsLy} /></td>
                          <td className="px-3 py-2.5 text-right">{formatNumber(r.salesTy)}</td>
                          <td className="px-3 py-2.5 text-right text-slate-500">{formatNumber(r.salesLy)}</td>
                          <td className="px-3 py-2.5">
                            <span className={`inline-flex rounded-full px-2 py-0.5 text-[10px] font-semibold ${actionClass(r.action)}`}>
                              {r.action}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ) : null}

            <div>
              <h4 className="mb-2 text-xs font-semibold text-slate-800 dark:text-slate-100">
                Store × SKU detail (weekly)
              </h4>
              <TableFilterBar
                search={detailSearch}
                onSearchChange={setDetailSearch}
                searchPlaceholder="Search store, SKU, barcode…"
                resultCount={filteredDetail.length}
                selects={[
                  { id: 'store', label: 'Store', value: detailStoreFilter, options: storeOptions, onChange: setDetailStoreFilter },
                  { id: 'dept', label: 'Department', value: detailDeptFilter, options: detailDeptOptions, onChange: setDetailDeptFilter },
                  { id: 'section', label: 'Section', value: detailSectionFilter, options: detailSectionOptions, onChange: setDetailSectionFilter },
                  {
                    id: 'action',
                    label: 'Action',
                    value: detailActionFilter,
                    options: [
                      { value: 'all', label: 'All' },
                      { value: 'Replenish', label: 'Replenish' },
                      { value: 'Increase', label: 'Increase' },
                      { value: 'Reduce', label: 'Reduce' },
                      { value: 'Maintain', label: 'Maintain' },
                      { value: 'Monitor', label: 'Monitor' },
                    ],
                    onChange: setDetailActionFilter,
                  },
                ]}
              />
              <div className="max-h-[420px] overflow-auto rounded-xl border border-slate-200 dark:border-slate-700">
                <table className="min-w-[1100px] w-full text-left text-[11px]">
                  <thead className="sticky top-0 z-10 bg-white text-[10px] uppercase tracking-wide text-slate-500 shadow-sm dark:bg-slate-900">
                    <tr>
                      <th className="px-3 py-2.5">Store</th>
                      <th className="px-3 py-2.5">SKU</th>
                      <th className="px-3 py-2.5">Department</th>
                      <th className="px-3 py-2.5">Section</th>
                      <th className="px-3 py-2.5 text-right">SOH now</th>
                      <th className="px-3 py-2.5 text-right">SOH LY</th>
                      <th className="px-3 py-2.5 text-right">vs LY</th>
                      <th className="px-3 py-2.5 text-right">WoW</th>
                      <th className="px-3 py-2.5 text-right">Sales CY</th>
                      <th className="px-3 py-2.5 text-right">ST%</th>
                      <th className="px-3 py-2.5">Action</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                    {filteredDetail.length === 0 ? (
                      <tr>
                        <td colSpan={11} className="px-3 py-8 text-center text-slate-400">
                          No rows for current filters
                        </td>
                      </tr>
                    ) : (
                      filteredDetail.map((r) => (
                        <tr key={`${r.siteId}-${r.barcode}`} className="hover:bg-slate-50/80 dark:hover:bg-slate-800/40">
                          <td className="px-3 py-2.5 font-semibold">{r.siteId}</td>
                          <td className="px-3 py-2.5">{r.sku}</td>
                          <td className="px-3 py-2.5">{r.department}</td>
                          <td className="px-3 py-2.5">{r.section}</td>
                          <td className="px-3 py-2.5 text-right font-semibold">{formatNumber(r.sohTy)}</td>
                          <td className="px-3 py-2.5 text-right text-slate-500">{formatNumber(r.sohLy)}</td>
                          <td className="px-3 py-2.5 text-right"><Delta value={r.vsLy} /></td>
                          <td className="px-3 py-2.5 text-right"><Delta value={r.wow} /></td>
                          <td className="px-3 py-2.5 text-right">{formatNumber(r.salesTy)}</td>
                          <td className="px-3 py-2.5 text-right">{r.sellThroughTy != null ? `${r.sellThroughTy.toFixed(1)}%` : '—'}</td>
                          <td className="px-3 py-2.5">
                            <span className={`inline-flex whitespace-nowrap rounded-full px-2 py-0.5 text-[10px] font-semibold ${actionClass(r.action)}`}>
                              {r.action}
                            </span>
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </div>

            {data?.note ? (
              <p className="text-[10px] text-slate-400">{data.note}</p>
            ) : null}
          </>
        )}
      </div>
    </div>
  );
}

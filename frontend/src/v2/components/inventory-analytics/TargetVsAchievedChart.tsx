import React, { useEffect, useMemo, useState } from 'react';
import { Bot, ChevronRight, Loader2, Sparkles } from 'lucide-react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  LabelList,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { API_URL } from '../../../config';
import { formatInr } from './inventoryAnalytics';
import { matchesTableSearch, TableFilterBar, uniqueFilterOptions } from './TableFilterBar';
import { DispatchPlaybookTable } from './DispatchPlaybookTable';
import { LazyMount } from './LazyMount';

type Period = 'week' | 'month' | 'year';

const PERIOD_TABS: { id: Period; label: string }[] = [
  { id: 'week', label: 'Weekly' },
  { id: 'month', label: 'Monthly' },
  { id: 'year', label: 'Yearly' },
];

const PERIOD_NOUN: Record<Period, string> = {
  week: 'week',
  month: 'month',
  year: 'year',
};

type TargetSiteOption = { id: string; label: string; format?: string };
type TargetChannelOption = { id: string; label: string };

type TargetPeriodPoint = {
  key: number[];
  label: string;
  periodLabel: string;
  target: number;
  achieved: number;
  achievementPct: number;
  isCurrent: boolean;
};

type SiteGrowthRow = {
  siteId: string;
  siteName: string;
  format: string;
  latestCompletedPeriod: string;
  periodsEvaluated: number;
  overTargetPeriods: number;
  consecutiveOverTarget: number;
  averageAchievementPct: number;
  latestTarget: number;
  latestAchieved: number;
  latestAchievementPct: number;
  salesGrowthPct: number | null;
  suggestedIncreasePct: number;
  recommendedTarget: number;
  recommendation: 'Increase target' | 'Monitor growth' | 'Above target' | 'Maintain / review';
};

type PlaybookDispatch = {
  department: string;
  section: string;
  salesTy: number;
  salesLy: number;
  topSkusLy: string[];
  action: string;
  reason: string;
  priorityScore: number;
};

type SitePlaybook = {
  ok: boolean;
  siteId: string;
  siteName: string;
  format: string;
  focusPeriod: string;
  lyPeriod: string;
  target: number;
  achieved: number;
  lyAchieved: number;
  achievementPct: number;
  gapToTarget: number;
  headline: string;
  topSkusTy: { SKU: string; sales: number }[];
  topSkusLy: { SKU: string; sales: number }[];
  dispatchSuggestions: PlaybookDispatch[];
};

type TargetVsAchievedResponse = {
  ok: boolean;
  site_options: TargetSiteOption[];
  channel_options: TargetChannelOption[];
  site_count: number;
  sites_with_achieved?: number;
  sales_data_as_of: string | null;
  current: TargetPeriodPoint | null;
  series: TargetPeriodPoint[];
  growth_table?: SiteGrowthRow[];
  detail?: string;
};

function recommendationClass(value: SiteGrowthRow['recommendation']) {
  if (value === 'Increase target') return 'bg-emerald-100 text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-300';
  if (value === 'Monitor growth') return 'bg-amber-100 text-amber-700 dark:bg-amber-950/60 dark:text-amber-300';
  if (value === 'Above target') return 'bg-sky-100 text-sky-700 dark:bg-sky-950/60 dark:text-sky-300';
  return 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300';
}

function dispatchClass(action: string) {
  if (action.startsWith('Grow') || action.startsWith('Send')) {
    return 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/50 dark:text-emerald-300';
  }
  if (action.startsWith('Maintain')) {
    return 'bg-sky-100 text-sky-800 dark:bg-sky-950/50 dark:text-sky-300';
  }
  return 'bg-amber-100 text-amber-800 dark:bg-amber-950/50 dark:text-amber-300';
}

function Shell({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-2xl border border-slate-200/80 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900/60">
      <div className="border-b border-slate-100 px-5 py-4 dark:border-slate-800">
        <h3 className="text-sm font-semibold text-slate-900 dark:text-white">{title}</h3>
        {subtitle && <p className="mt-0.5 text-xs text-slate-500">{subtitle}</p>}
      </div>
      <div className="p-4">{children}</div>
    </div>
  );
}

/** Week / Month / Year segmented toggle. Rendered in both the chart header and
 *  the Site Growth table header — both bound to the same `period` state so the
 *  two controls stay in sync and the view uses a single API call. */
function PeriodToggle({ value, onChange }: { value: Period; onChange: (p: Period) => void }) {
  return (
    <div className="flex rounded-lg border border-slate-200 bg-slate-50 p-0.5 dark:border-slate-700 dark:bg-slate-800">
      {PERIOD_TABS.map((tab) => (
        <button
          key={tab.id}
          type="button"
          onClick={() => onChange(tab.id)}
          className={`rounded-md px-2.5 py-1 text-[11px] font-semibold transition ${
            value === tab.id
              ? 'bg-white text-blue-600 shadow-sm dark:bg-slate-700 dark:text-blue-300'
              : 'text-slate-500 hover:text-slate-700 dark:text-slate-400'
          }`}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}

/**
 * Target (rupee, from all Target LFS formats) vs Achieved (real net sales from
 * the Sales master) — weekly / monthly / yearly, filterable by individual site
 * or overall/format. Styled to match the sibling "Weekly
 * Store SOH — This Year vs Last Year" chart: dashed slate area = reference
 * (Target here), solid line = the tracked metric (Achieved here).
 */
export function TargetVsAchievedChart() {
  const [period, setPeriod] = useState<Period>('week');
  const [channel, setChannel] = useState('all');
  const [siteId, setSiteId] = useState('all');
  const [data, setData] = useState<TargetVsAchievedResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedSite, setSelectedSite] = useState<string | null>(null);
  const [playbook, setPlaybook] = useState<SitePlaybook | null>(null);
  const [playbookLoading, setPlaybookLoading] = useState(false);
  const [playbookError, setPlaybookError] = useState<string | null>(null);
  const [growthSearch, setGrowthSearch] = useState('');
  const [growthFormat, setGrowthFormat] = useState('all');
  const [growthAction, setGrowthAction] = useState('all');
  const [growthAchievement, setGrowthAchievement] = useState('all');
  const [playbookSearch, setPlaybookSearch] = useState('');
  const [playbookDept, setPlaybookDept] = useState('all');
  const [playbookAction, setPlaybookAction] = useState('all');
  const [skuCompareSearch, setSkuCompareSearch] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    setData(null);
    const params = new URLSearchParams({ site_id: siteId, channel, period });
    fetch(`${API_URL}/inventory-analytics/target-vs-achieved?${params}`, {
      signal: controller.signal,
    })
      .then(async (res) => {
        const json = (await res.json()) as TargetVsAchievedResponse & { detail?: string; error?: string; status?: string; message?: string };
        if (!res.ok) throw new Error(json.detail || `Failed (${res.status})`);
        if (json.status === 'error') throw new Error(json.message || 'API Error');
        if (json.error) throw new Error(json.error);
        if (controller.signal.aborted) return;
        setData(json);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setError(err instanceof Error ? err.message : 'Could not load target data');
        setData(null);
        setLoading(false);
      });
    return () => controller.abort();
  }, [siteId, channel, period]);

  useEffect(() => {
    if (!selectedSite) {
      setPlaybook(null);
      setPlaybookError(null);
      return;
    }
    const controller = new AbortController();
    setPlaybookLoading(true);
    setPlaybookError(null);
    const params = new URLSearchParams({ site_id: selectedSite, period });
    fetch(`${API_URL}/inventory-analytics/site-growth-playbook?${params}`, { signal: controller.signal })
      .then(async (res) => {
        const json = (await res.json()) as SitePlaybook & { detail?: string };
        if (!res.ok) throw new Error(json.detail || 'Failed to load playbook');
        if (!controller.signal.aborted) {
          setPlaybook(json);
          setPlaybookLoading(false);
        }
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setPlaybook(null);
        setPlaybookError(err instanceof Error ? err.message : 'Could not load site playbook');
        setPlaybookLoading(false);
      });
    return () => controller.abort();
  }, [selectedSite, period]);

  // If the chosen channel no longer includes the selected site, fall back to Overall.
  useEffect(() => {
    if (siteId === 'all' || !data) return;
    const opt = data.site_options.find((o) => o.id === siteId);
    if (channel !== 'all' && opt && opt.format !== channel) setSiteId('all');
  }, [channel, data, siteId]);

  const siteOptions = useMemo(() => {
    const opts = data?.site_options ?? [{ id: 'all', label: 'Overall (all sites)' }];
    if (channel === 'all') return opts;
    return opts.filter((o) => o.id === 'all' || o.format === channel);
  }, [data, channel]);

  const channelOptions = data?.channel_options ?? [{ id: 'all', label: 'All formats' }];
  const current = data?.current ?? null;
  const growthRows = data?.growth_table ?? [];
  const increaseCandidates = growthRows.filter((row) => row.recommendation === 'Increase target').length;
  const noun = PERIOD_NOUN[period];

  const filteredGrowthRows = useMemo(() => {
    return growthRows.filter((row) => {
      if (growthFormat !== 'all' && row.format !== growthFormat) return false;
      if (growthAction !== 'all' && row.recommendation !== growthAction) return false;
      if (growthAchievement === 'hit' && row.latestAchievementPct < 100) return false;
      if (growthAchievement === 'below' && row.latestAchievementPct >= 100) return false;
      if (growthAchievement === 'increase' && row.recommendation !== 'Increase target') return false;
      return matchesTableSearch(growthSearch, [row.siteId, row.siteName, row.format, row.recommendation]);
    });
  }, [growthRows, growthFormat, growthAction, growthAchievement, growthSearch]);

  const growthFormatOptions = useMemo(
    () => uniqueFilterOptions(growthRows.map((r) => r.format)),
    [growthRows]
  );
  const growthActionOptions = useMemo(
    () => uniqueFilterOptions(growthRows.map((r) => r.recommendation), 'All actions'),
    [growthRows]
  );

  const filteredDispatch = useMemo(() => {
    if (!playbook) return [];
    return playbook.dispatchSuggestions.filter((d) => {
      if (playbookDept !== 'all' && d.department !== playbookDept) return false;
      if (playbookAction !== 'all' && !d.action.startsWith(playbookAction)) return false;
      return matchesTableSearch(playbookSearch, [d.department, d.section, d.action, ...d.topSkusLy]);
    });
  }, [playbook, playbookDept, playbookAction, playbookSearch]);

  const playbookDeptOptions = useMemo(
    () => uniqueFilterOptions((playbook?.dispatchSuggestions ?? []).map((d) => d.department)),
    [playbook]
  );

  const skuCompareRows = useMemo(() => {
    if (!playbook) return [];
    const len = Math.max(playbook.topSkusTy.length, playbook.topSkusLy.length, 1);
    return Array.from({ length: len })
      .slice(0, 8)
      .map((_, i) => ({
        idx: i,
        skuTy: playbook.topSkusTy[i]?.SKU ?? '—',
        salesTy: playbook.topSkusTy[i]?.sales,
        skuLy: playbook.topSkusLy[i]?.SKU ?? '—',
        salesLy: playbook.topSkusLy[i]?.sales,
      }));
  }, [playbook]);

  const filteredSkuCompare = useMemo(() => {
    return skuCompareRows.filter((r) =>
      matchesTableSearch(skuCompareSearch, [r.skuTy, r.skuLy, r.salesTy, r.salesLy])
    );
  }, [skuCompareRows, skuCompareSearch]);

  const subtitle = current
    ? `Same ${noun} number compare · Current ${current.periodLabel}${current.isCurrent && data?.sales_data_as_of ? ` (sales through ${data.sales_data_as_of})` : ''} · Target ${formatInr(
        current.target
      )} vs Achieved ${formatInr(current.achieved)} (${current.achievementPct.toFixed(1)}%)`
    : loading
      ? 'Loading…'
      : error || 'No target data yet';

  return (
    <>
    <Shell title="Target vs Achieved — Sales (All Target LFS Formats)" subtitle={subtitle}>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <PeriodToggle value={period} onChange={setPeriod} />
        <select
          value={channel}
          onChange={(e) => setChannel(e.target.value)}
          className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-700 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
        >
          {channelOptions.map((o) => (
            <option key={o.id} value={o.id}>
              {o.label}
            </option>
          ))}
        </select>
        <select
          value={siteId}
          onChange={(e) => setSiteId(e.target.value)}
          className="min-w-[220px] flex-1 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-700 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
        >
          {siteOptions.map((o) => (
            <option key={o.id} value={o.id}>
              {o.label}
            </option>
          ))}
        </select>
        {data?.site_count ? (
          <span className="ml-auto text-[11px] text-slate-400">
            {data.sites_with_achieved ?? data.site_count}/{data.site_count} sites with sales
            {data.sales_data_as_of ? ` · sales through ${data.sales_data_as_of}` : ''}
          </span>
        ) : null}
      </div>

      {error ? (
        <div className="flex h-[280px] items-center justify-center rounded-xl border border-dashed border-slate-200 text-sm text-rose-600 dark:border-slate-700">
          {error}
        </div>
      ) : loading && !data ? (
        <div className="flex h-[280px] flex-col items-center justify-center rounded-xl border border-dashed border-slate-200 text-sm text-slate-500 dark:border-slate-700">
          <Loader2 className="mb-2 h-6 w-6 animate-spin text-blue-500" />
          <span>Loading data...</span>
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={320}>
          <BarChart data={data?.series ?? []} margin={{ top: 48, right: 8, left: 4, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" vertical={false} />
            <XAxis dataKey="label" tick={{ fontSize: 10 }} />
            <YAxis tickFormatter={(v) => formatInr(v)} tick={{ fontSize: 9 }} width={88} />
            <Tooltip
              formatter={(v: number, name: string) => [formatInr(v), name]}
              labelFormatter={(_, payload) => {
                const p = payload?.[0]?.payload as TargetPeriodPoint | undefined;
                if (!p) return '';
                return `${p.periodLabel} · ${p.achievementPct.toFixed(1)}% achieved`;
              }}
            />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            <Bar
              dataKey="target"
              name="Target"
              fill="#94a3b8"
              radius={[5, 5, 0, 0]}
              maxBarSize={34}
            >
              <LabelList
                dataKey="target"
                position="top"
                formatter={(value: string | number) => formatInr(Number(value) || 0)}
                fill="#64748b"
                fontSize={9}
              />
            </Bar>
            <Bar
              dataKey="achieved"
              name="Achieved"
              fill="#0ea5e9"
              radius={[5, 5, 0, 0]}
              maxBarSize={34}
            >
              <LabelList
                dataKey="achieved"
                position="top"
                formatter={(value: string | number) => formatInr(Number(value) || 0)}
                fill="#0284c7"
                fontSize={9}
              />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      )}

      {!error && !loading && growthRows.length > 0 ? (
        <div className="mt-5 overflow-hidden rounded-xl border border-slate-200 dark:border-slate-700">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 bg-slate-50 px-4 py-3 dark:border-slate-700 dark:bg-slate-800/70">
            <div>
              <h4 className="text-xs font-semibold text-slate-900 dark:text-white">Site Growth &amp; Target Opportunity</h4>
              <p className="mt-0.5 text-[10px] text-slate-500 dark:text-slate-400">
                Click a site for SKU + dept/section dispatch plan · LY same {noun} vs current · target gap
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <PeriodToggle value={period} onChange={setPeriod} />
              <span className="rounded-full bg-emerald-100 px-2.5 py-1 text-[10px] font-semibold text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-300">
                {increaseCandidates} increase candidate{increaseCandidates === 1 ? '' : 's'}
              </span>
            </div>
          </div>
          <TableFilterBar
            search={growthSearch}
            onSearchChange={setGrowthSearch}
            searchPlaceholder="Search site, name, format…"
            resultCount={filteredGrowthRows.length}
            selects={[
              { id: 'format', label: 'Format', value: growthFormat, options: growthFormatOptions, onChange: setGrowthFormat },
              { id: 'action', label: 'Action', value: growthAction, options: growthActionOptions, onChange: setGrowthAction },
              {
                id: 'achievement',
                label: 'Achievement',
                value: growthAchievement,
                options: [
                  { value: 'all', label: 'All' },
                  { value: 'hit', label: 'Hit target (≥100%)' },
                  { value: 'below', label: 'Below target' },
                  { value: 'increase', label: 'Increase candidates' },
                ],
                onChange: setGrowthAchievement,
              },
            ]}
            className="border-b border-slate-200 px-4 py-2 dark:border-slate-700"
          />
          <div className="max-h-[480px] overflow-auto">
            <table className="min-w-[1320px] w-full text-left text-[11px]">
              <thead className="sticky top-0 z-10 bg-white text-[10px] uppercase tracking-wide text-slate-500 shadow-sm dark:bg-slate-900 dark:text-slate-400">
                <tr>
                  <th className="px-3 py-2.5">Site</th>
                  <th className="px-3 py-2.5">Format</th>
                  <th className="px-3 py-2.5">Latest completed</th>
                  <th className="px-3 py-2.5 text-right">Target</th>
                  <th className="px-3 py-2.5 text-right">Achieved</th>
                  <th className="px-3 py-2.5 text-right">Achievement</th>
                  <th className="px-3 py-2.5 text-right">Continuous streak</th>
                  <th className="px-3 py-2.5 text-right">Hit periods</th>
                  <th className="px-3 py-2.5 text-right">Avg achievement</th>
                  <th className="px-3 py-2.5 text-right">Sales growth</th>
                  <th className="px-3 py-2.5 text-right">Suggested increase</th>
                  <th className="px-3 py-2.5 text-right">Recommended target</th>
                  <th className="px-3 py-2.5">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {filteredGrowthRows.map((row) => (
                  <tr
                    key={row.siteId}
                    onClick={() => setSelectedSite(row.siteId === selectedSite ? null : row.siteId)}
                    className={`cursor-pointer bg-white hover:bg-blue-50/60 dark:bg-slate-900/20 dark:hover:bg-slate-800/50 ${
                      selectedSite === row.siteId ? 'ring-1 ring-inset ring-blue-400 bg-blue-50/40' : ''
                    }`}
                  >
                    <td className="max-w-[260px] px-3 py-2.5">
                      <div className="flex items-center gap-1 font-semibold text-slate-800 dark:text-slate-100">
                        <ChevronRight className={`h-3 w-3 shrink-0 transition ${selectedSite === row.siteId ? 'rotate-90 text-blue-600' : 'text-slate-400'}`} />
                        {row.siteId}
                      </div>
                      <div className="truncate text-[10px] text-slate-500" title={row.siteName}>{row.siteName || '—'}</div>
                    </td>
                    <td className="px-3 py-2.5 font-medium text-slate-600 dark:text-slate-300">{row.format}</td>
                    <td className="whitespace-nowrap px-3 py-2.5 text-slate-600 dark:text-slate-300">{row.latestCompletedPeriod}</td>
                    <td className="whitespace-nowrap px-3 py-2.5 text-right">{formatInr(row.latestTarget)}</td>
                    <td className="whitespace-nowrap px-3 py-2.5 text-right">{formatInr(row.latestAchieved)}</td>
                    <td className={`whitespace-nowrap px-3 py-2.5 text-right font-semibold ${row.latestAchievementPct >= 100 ? 'text-emerald-600' : 'text-slate-600 dark:text-slate-300'}`}>
                      {row.latestAchievementPct.toFixed(1)}%
                    </td>
                    <td className="px-3 py-2.5 text-right font-semibold text-slate-700 dark:text-slate-200">{row.consecutiveOverTarget}</td>
                    <td className="whitespace-nowrap px-3 py-2.5 text-right text-slate-600 dark:text-slate-300">{row.overTargetPeriods}/{row.periodsEvaluated}</td>
                    <td className="px-3 py-2.5 text-right text-slate-600 dark:text-slate-300">{row.averageAchievementPct.toFixed(1)}%</td>
                    <td className={`px-3 py-2.5 text-right ${row.salesGrowthPct != null && row.salesGrowthPct > 0 ? 'text-emerald-600' : 'text-slate-500'}`}>
                      {row.salesGrowthPct == null ? '—' : `${row.salesGrowthPct > 0 ? '+' : ''}${row.salesGrowthPct.toFixed(1)}%`}
                    </td>
                    <td className="px-3 py-2.5 text-right font-semibold text-emerald-600">
                      {row.suggestedIncreasePct > 0 ? `+${row.suggestedIncreasePct.toFixed(1)}%` : '—'}
                    </td>
                    <td className="whitespace-nowrap px-3 py-2.5 text-right font-semibold text-slate-800 dark:text-slate-100">{formatInr(row.recommendedTarget)}</td>
                    <td className="px-3 py-2.5">
                      <span className={`inline-flex whitespace-nowrap rounded-full px-2 py-1 text-[10px] font-semibold ${recommendationClass(row.recommendation)}`}>
                        {row.recommendation}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {selectedSite ? (
            <div className="border-t border-slate-200 bg-gradient-to-b from-indigo-50/40 to-white p-4 dark:border-slate-700 dark:from-indigo-950/20 dark:to-slate-900/40">
              <div className="mb-3 flex flex-wrap items-center gap-2">
                <Bot className="h-4 w-4 text-indigo-600" />
                <h5 className="text-xs font-semibold text-slate-900 dark:text-white">
                  Dispatch playbook — {selectedSite}
                  {playbook?.siteName ? ` · ${playbook.siteName}` : ''}
                </h5>
                {playbookLoading ? (
                  <Loader2 className="h-4 w-4 animate-spin text-blue-500" />
                ) : null}
              </div>

              {playbookError ? (
                <p className="text-xs text-rose-600">{playbookError}</p>
              ) : playbook ? (
                <div className="space-y-4">
                  <p className="text-xs text-slate-600 dark:text-slate-300">{playbook.headline}</p>
                  <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
                    <div className="rounded-lg border border-slate-200 bg-white px-3 py-2 dark:border-slate-700 dark:bg-slate-900/60">
                      <p className="text-[10px] text-slate-500">Focus {noun}</p>
                      <p className="text-sm font-bold">{playbook.focusPeriod}</p>
                    </div>
                    <div className="rounded-lg border border-slate-200 bg-white px-3 py-2 dark:border-slate-700 dark:bg-slate-900/60">
                      <p className="text-[10px] text-slate-500">Target / Achieved</p>
                      <p className="text-sm font-bold">{formatInr(playbook.achieved)} / {formatInr(playbook.target)}</p>
                      <p className="text-[10px] text-slate-500">{playbook.achievementPct}% · gap {formatInr(playbook.gapToTarget)}</p>
                    </div>
                    <div className="rounded-lg border border-slate-200 bg-white px-3 py-2 dark:border-slate-700 dark:bg-slate-900/60">
                      <p className="text-[10px] text-slate-500">Same {noun} LY ({playbook.lyPeriod})</p>
                      <p className="text-sm font-bold">{formatInr(playbook.lyAchieved)}</p>
                    </div>
                    <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 dark:border-emerald-900 dark:bg-emerald-950/30">
                      <p className="text-[10px] text-emerald-700">Top SKU (CY)</p>
                      <p className="truncate text-sm font-bold text-emerald-800">{playbook.topSkusTy[0]?.SKU ?? '—'}</p>
                    </div>
                  </div>

                  <div className="grid gap-4 lg:grid-cols-2">
                    <div className="overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-700">
                      <div className="border-b border-slate-100 bg-slate-50 px-3 py-2 text-[10px] font-semibold uppercase text-slate-500 dark:border-slate-700 dark:bg-slate-800/70">
                        <Sparkles className="mr-1 inline h-3 w-3" /> Send more — dept/section (LY winners → close gap)
                      </div>
                      <TableFilterBar
                        search={playbookSearch}
                        onSearchChange={setPlaybookSearch}
                        searchPlaceholder="Dept, section, SKU…"
                        resultCount={filteredDispatch.length}
                        selects={[
                          { id: 'dept', label: 'Dept', value: playbookDept, options: playbookDeptOptions, onChange: setPlaybookDept },
                          {
                            id: 'action',
                            label: 'Action',
                            value: playbookAction,
                            options: [
                              { value: 'all', label: 'All' },
                              { value: 'Send', label: 'Send more' },
                              { value: 'Grow', label: 'Grow' },
                              { value: 'Replenish', label: 'Replenish' },
                              { value: 'Maintain', label: 'Maintain' },
                            ],
                            onChange: setPlaybookAction,
                          },
                        ]}
                        className="px-3 py-2"
                      />
                      <table className="w-full text-left text-[11px]">
                        <thead>
                          <tr className="text-[10px] uppercase text-slate-400">
                            <th className="px-3 py-2">Dept / Section</th>
                            <th className="px-3 py-2 text-right">Sales CY</th>
                            <th className="px-3 py-2 text-right">Sales LY</th>
                            <th className="px-3 py-2">Top LY SKUs</th>
                            <th className="px-3 py-2">Action</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                          {filteredDispatch.map((d) => (
                            <tr key={`${d.department}-${d.section}`}>
                              <td className="px-3 py-2">
                                <div className="font-medium">{d.department}</div>
                                <div className="text-[10px] text-slate-500">{d.section}</div>
                              </td>
                              <td className="px-3 py-2 text-right">{formatInr(d.salesTy)}</td>
                              <td className="px-3 py-2 text-right text-slate-500">{formatInr(d.salesLy)}</td>
                              <td className="max-w-[140px] truncate px-3 py-2 text-[10px]" title={d.topSkusLy.join(', ')}>
                                {d.topSkusLy.join(', ') || '—'}
                              </td>
                              <td className="px-3 py-2">
                                <span className={`inline-flex rounded-full px-2 py-0.5 text-[9px] font-semibold ${dispatchClass(d.action)}`}>
                                  {d.action}
                                </span>
                                <p className="mt-1 text-[9px] text-slate-500">{d.reason}</p>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>

                    <div className="overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-700">
                      <div className="border-b border-slate-100 bg-slate-50 px-3 py-2 text-[10px] font-semibold uppercase text-slate-500 dark:border-slate-700 dark:bg-slate-800/70">
                        Best SKUs this {noun} vs LY same {noun}
                      </div>
                      <TableFilterBar
                        search={skuCompareSearch}
                        onSearchChange={setSkuCompareSearch}
                        searchPlaceholder="Search SKU…"
                        resultCount={filteredSkuCompare.length}
                        className="px-3 py-2"
                      />
                      <table className="w-full text-left text-[11px]">
                        <thead>
                          <tr className="text-[10px] uppercase text-slate-400">
                            <th className="px-3 py-2">SKU (CY)</th>
                            <th className="px-3 py-2 text-right">Sales</th>
                            <th className="px-3 py-2">SKU (LY)</th>
                            <th className="px-3 py-2 text-right">Sales LY</th>
                          </tr>
                        </thead>
                        <tbody>
                          {filteredSkuCompare.map((r) => (
                            <tr key={r.idx} className="border-t border-slate-50 dark:border-slate-800">
                              <td className="px-3 py-2 font-medium">{r.skuTy}</td>
                              <td className="px-3 py-2 text-right">{r.salesTy != null ? formatInr(r.salesTy) : '—'}</td>
                              <td className="px-3 py-2">{r.skuLy}</td>
                              <td className="px-3 py-2 text-right text-slate-500">{r.salesLy != null ? formatInr(r.salesLy) : '—'}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                </div>
              ) : playbookLoading ? (
                <p className="text-xs text-slate-500">Loading from pre-built cache…</p>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}
    </Shell>

    <div className="mt-5">
      <LazyMount minHeight={280}>
        <DispatchPlaybookTable highlightSiteId={selectedSite} />
      </LazyMount>
    </div>
    </>
  );
}

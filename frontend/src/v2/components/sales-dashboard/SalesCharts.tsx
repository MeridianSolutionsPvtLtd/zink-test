import React, { useState } from 'react';
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ComposedChart,
  LabelList,
  Legend,
  Line,
  Pie,
  PieChart,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  Treemap,
  XAxis,
  YAxis,
  ZAxis,
} from 'recharts';
import { Award, BarChart3, Percent, TrendingUp } from 'lucide-react';
import { SectionCard } from '../../design-system';
import type { SalesInsights, MonthlyTrendPoint, TargetSummary } from './types';
import {
  aggregateByKey,
  buildHeatmapData,
  buildParetoData,
  buildRealPeriodSeries,
  buildTimelineSeries,
  deriveCategory,
  deriveCity,
  deriveDepartment,
  deriveSalesChannel,
  deriveSection,
  deriveState,
  deriveStory,
  deriveZone,
  formatCompactInr,
  formatInr,
  timelineLabelForKey,
  timelineModeLabel,
  type TimelineMode,
} from './salesAnalytics';
import type { SalesRow } from './types';

export type { TimelineMode };

/** Sales Trend chart grain — independent of header timeline filter */
export type TrendGrain = 'month' | 'week' | 'year';
export type BreakdownDim = 'zone' | 'store' | 'state' | 'city' | 'channel' | 'sku' | 'department' | 'section' | 'discount';

const TREND_GRAIN_TABS: { id: TrendGrain; label: string }[] = [
  { id: 'month', label: 'Month' },
  { id: 'week', label: 'Week' },
  { id: 'year', label: 'Year' },
];

type ChartShellProps = {
  title: string;
  subtitle?: string;
  icon?: React.ReactNode;
  accent?: string;
  loading: boolean;
  height?: number;
  children: React.ReactNode;
  action?: React.ReactNode;
};

function ChartShell({
  title,
  subtitle,
  icon,
  accent,
  action,
  children,
  loading,
  height = 300,
}: ChartShellProps) {
  return (
    <SectionCard
      title={title}
      subtitle={subtitle}
      action={action ?? icon}
      loading={loading}
      hover={false}
    >
      <div style={{ height, minWidth: 0 }} className="w-full min-w-0 overflow-hidden">
        {children}
      </div>
    </SectionCard>
  );
}

export function SalesTrendTabs({
  rows,
  insights,
  loading,
  monthlyTrendData,
  targetSummary,
  grain: grainProp,
  onGrainChange,
}: {
  rows: SalesRow[];
  insights: SalesInsights | null;
  loading: boolean;
  monthlyTrendData: MonthlyTrendPoint[];
  /** Kept for API compat — grain is local to this chart */
  timelineMode?: TimelineMode;
  targetSummary?: TargetSummary | null;
  grain?: TrendGrain;
  onGrainChange?: (grain: TrendGrain) => void;
}) {
  const [localGrain, setLocalGrain] = useState<TrendGrain>('month');
  const grain = grainProp ?? localGrain;
  const setGrain = (next: TrendGrain) => {
    onGrainChange?.(next);
    if (grainProp === undefined) setLocalGrain(next);
  };

  const series = React.useMemo(() => {
    // Backend trend series only — never rebuild charts from paginated table rows.
    const raw = (() => {
      if (grain === 'month') {
        return monthlyTrendData.map((m) => ({
          label: m.label,
          month: m.month,
          net: m.net_sales,
        }));
      }
      if (grain === 'week') {
        return (insights?.weekly_trend ?? []).map((w) => ({
          label: w.label,
          net: w.net_sales,
        }));
      }
      return (insights?.yearly_trend ?? []).map((y) => ({
        label: y.label,
        net: y.net_sales,
      }));
    })();

    if (raw.length > 0) {
      const avgTarget = targetSummary && targetSummary.totalTarget > 0 ? Math.round(targetSummary.totalTarget / raw.length) : 0;
      const mtMap = targetSummary?.monthlyTargets;
      const hasMonthlyTargets = Boolean(mtMap && Object.keys(mtMap).length > 0);

      /** Sum Target LFS months into a calendar year or Indian FY (Apr–Mar). */
      const yearTargetFromMap = (label: string): number => {
        if (!mtMap) return 0;
        const lbl = (label || '').trim();
        if (mtMap[lbl]) return mtMap[lbl];

        // Calendar year label e.g. "2026"
        if (/^\d{4}$/.test(lbl)) {
          const y = Number(lbl);
          let sum = 0;
          for (let m = 1; m <= 12; m++) {
            sum += mtMap[`${y}-${String(m).padStart(2, '0')}`] || 0;
          }
          return sum;
        }

        // FY label e.g. "FY27", "FY 2026-27"
        const fyMatch = lbl.match(/FY\s*(\d{2,4})/i);
        if (fyMatch) {
          let fyEnd = Number(fyMatch[1]);
          if (fyEnd < 100) fyEnd += 2000;
          let sum = 0;
          for (let m = 4; m <= 12; m++) {
            sum += mtMap[`${fyEnd - 1}-${String(m).padStart(2, '0')}`] || 0;
          }
          for (let m = 1; m <= 3; m++) {
            sum += mtMap[`${fyEnd}-${String(m).padStart(2, '0')}`] || 0;
          }
          return sum;
        }
        return 0;
      };

      return raw.map((item) => {
        let tVal = 0;
        if (hasMonthlyTargets && mtMap) {
          const lbl = (item.label || '').trim();
          const parts = lbl.split(/[\s'-]+/);
          const mPrefix = parts[0] || '';

          const monthKey = 'month' in item ? String(item.month || '') : '';
          tVal =
            (monthKey && mtMap[monthKey]) ||
            mtMap[lbl] ||
            mtMap[lbl.toLowerCase()] ||
            mtMap[lbl.toUpperCase()] ||
            mtMap[mPrefix] ||
            mtMap[mPrefix.toLowerCase()] ||
            mtMap[mPrefix.toUpperCase()] ||
            mtMap[mPrefix.slice(0, 3)] ||
            0;

          // Year / FY grain: monthly keys won't match labels — roll up
          if (!tVal && (grain === 'year' || /^\d{4}$/.test(lbl) || /FY/i.test(lbl))) {
            tVal = yearTargetFromMap(lbl);
          }
        }

        // Single-period filter (e.g. one FY): use KPI total Target when series has one point
        if (!tVal && raw.length === 1 && targetSummary && targetSummary.totalTarget > 0) {
          tVal = Math.round(targetSummary.totalTarget);
        }

        if (!tVal && !hasMonthlyTargets) {
          tVal = avgTarget || 0;
        }

        const achPct = tVal > 0 ? Math.round(((item.net || 0) * 100) / tVal) : undefined;
        return {
          ...item,
          target: tVal > 0 ? tVal : undefined,
          achievementPct: achPct,
        };
      });
    }
    return raw;
  }, [grain, monthlyTrendData, insights?.weekly_trend, insights?.yearly_trend, targetSummary]);

  const targetDisplayVal = targetSummary && targetSummary.totalTarget > 0 ? targetSummary.totalTarget : 0;
  const targetAchievedPct = targetSummary?.achievement;

  const grainTabs = (
    <div className="flex flex-wrap items-center justify-end gap-2">
      {targetDisplayVal > 0 && (
        <div className="inline-flex items-center gap-1.5 rounded-lg border border-purple-200/80 bg-purple-50 px-2.5 py-1 text-[11px] font-bold text-purple-800 dark:border-purple-500/30 dark:bg-purple-950/40 dark:text-purple-300">
          <span>Target: {formatCompactInr(targetDisplayVal)}</span>
          {targetAchievedPct != null && (
            <>
              <span className="opacity-40">•</span>
              <span>Achieved: {targetAchievedPct}%</span>
            </>
          )}
        </div>
      )}
      <div className="flex items-center gap-1 rounded-lg bg-slate-100 p-0.5 dark:bg-slate-800">
        {TREND_GRAIN_TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => setGrain(t.id)}
            className={`rounded-md px-2.5 py-1 text-[11px] font-semibold transition ${
              grain === t.id
                ? 'bg-white text-slate-900 shadow-sm dark:bg-slate-700 dark:text-white'
                : 'text-slate-500 hover:text-slate-700 dark:text-slate-400'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
    </div>
  );

  return (
    <ChartShell
      title="Sales Trend"
      subtitle={`${TREND_GRAIN_TABS.find((t) => t.id === grain)?.label ?? 'Month'} view · follows page filters · Target vs Net Sales`}
      icon={<BarChart3 className="h-4 w-4 text-teal-600" />}
      accent="bg-teal-50/80 dark:bg-teal-500/10"
      loading={loading && !insights}
      height={320}
      action={grainTabs}
    >
      {series.length === 0 ? (
        <div className="flex h-full items-center justify-center text-xs text-slate-500">
          No data for current filters
        </div>
      ) : (
        <ResponsiveContainer debounce={80} width="100%" height="100%" minWidth={0}>
          <ComposedChart data={series} margin={{ top: 18, right: 20, left: 0, bottom: 4 }} barCategoryGap="20%" barGap={6}>
            <defs>
              <linearGradient id="netBarGradient" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#00c9a7" />
                <stop offset="100%" stopColor="#0d9488" />
              </linearGradient>
              <linearGradient id="targetBarGradient" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#8b5cf6" />
                <stop offset="100%" stopColor="#6366f1" />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
            <XAxis dataKey="label" tick={{ fontSize: 11, fill: '#64748b' }} axisLine={false} tickLine={false} />
            <YAxis
              yAxisId="left"
              tickFormatter={(v) => formatCompactInr(Number(v))}
              tick={{ fontSize: 11, fill: '#64748b' }}
              axisLine={false}
              tickLine={false}
              width={52}
            />
            <YAxis
              yAxisId="right"
              orientation="right"
              tickFormatter={(v) => `${v}%`}
              tick={{ fontSize: 10, fill: '#f59e0b' }}
              axisLine={false}
              tickLine={false}
              domain={[0, 120]}
              width={36}
            />
            <Tooltip
              formatter={(v: number, name: string) => [
                name.includes('%') ? `${v}%` : formatInr(v),
                name,
              ]}
              labelStyle={{ fontWeight: 600 }}
            />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            <Bar yAxisId="left" dataKey="net" name="Net Sales" fill="url(#netBarGradient)" radius={[6, 6, 0, 0]} maxBarSize={32}>
              <LabelList dataKey="net" position="top" formatter={(v: number) => (v ? formatCompactInr(v) : '')} style={{ fontSize: 10, fill: '#0d9488', fontWeight: 600 }} />
            </Bar>
            <Bar yAxisId="left" dataKey="target" name="Target Sales" fill="url(#targetBarGradient)" radius={[6, 6, 0, 0]} maxBarSize={32}>
              <LabelList dataKey="target" position="top" formatter={(v: number) => (v ? formatCompactInr(v) : '')} style={{ fontSize: 10, fill: '#7c3aed', fontWeight: 600 }} />
            </Bar>
            <Line
              yAxisId="right"
              type="monotone"
              dataKey="achievementPct"
              name="Achievement %"
              stroke="#f59e0b"
              strokeDasharray="5 5"
              strokeWidth={2.5}
              dot={{ r: 4, fill: '#f59e0b' }}
            >
              <LabelList dataKey="achievementPct" position="top" formatter={(v: number) => (v ? `${v}%` : '')} style={{ fontSize: 10, fill: '#d97706', fontWeight: 700 }} />
            </Line>
          </ComposedChart>
        </ResponsiveContainer>
      )}
    </ChartShell>
  );
}

/** Sales breakdown — follows Timeline mode; rows already timeline-filtered from parent */
export function SalesBreakdownSection({
  rows,
  insights,
  channelLabel,
  loading,
  onCrossFilter,
  activeKey,
  timelineMode,
  timelineValues = [],
  targetSummary,
  dim: dimProp,
  onDimChange,
}: {
  rows: SalesRow[];
  insights: SalesInsights | null;
  channelLabel: string;
  loading: boolean;
  onCrossFilter: (key: string, value: string) => void;
  activeKey: string | null;
  timelineMode: TimelineMode;
  timelineValues?: string[];
  targetSummary?: TargetSummary | null;
  dim?: BreakdownDim;
  onDimChange?: (dim: BreakdownDim) => void;
}) {
  const [localDim, setLocalDim] = useState<BreakdownDim>('zone');
  const dim = dimProp ?? localDim;
  const setDim = (next: BreakdownDim) => {
    onDimChange?.(next);
    if (dimProp === undefined) setLocalDim(next);
  };

  const periodWord = timelineModeLabel(timelineMode);
  const periodLabel = React.useMemo(() => {
    if (targetSummary?.periodLabel) return targetSummary.periodLabel;
    if (timelineValues.length === 1) return timelineLabelForKey(timelineMode, timelineValues[0]);
    if (timelineValues.length > 1) {
      return timelineValues
        .slice(0, 3)
        .map((v) => timelineLabelForKey(timelineMode, v))
        .join(', ');
    }
    return periodWord;
  }, [targetSummary?.periodLabel, timelineValues, timelineMode, periodWord]);

  // Charts use backend insights only — never re-aggregate the paginated table page.
  const zoneData = React.useMemo(
    () =>
      (insights?.top_zones ?? [])
        .filter((d) => d.name)
        .slice(0, 8)
        .map((d) => ({ name: d.name, value: d.net_sales, units: d.units })),
    [insights]
  );

  const storeData = React.useMemo(
    () =>
      (insights?.top_revenue_sites ?? [])
        .filter((s) => s.site_name)
        .slice(0, 8)
        .map((s) => ({ name: s.site_name, value: s.net_sales, units: s.units })),
    [insights]
  );

  const stateData = React.useMemo(
    () =>
      (insights?.top_states ?? [])
        .filter((d) => d.name)
        .slice(0, 8)
        .map((d) => ({ name: d.name, value: d.net_sales, units: d.units })),
    [insights]
  );

  const cityData = React.useMemo(
    () =>
      (insights?.top_cities ?? [])
        .filter((d) => d.name)
        .slice(0, 8)
        .map((d) => ({ name: d.name, value: d.net_sales, units: d.units })),
    [insights]
  );

  const skuData = React.useMemo(
    () =>
      (insights?.top_sold_skus ?? [])
        .filter((s) => s.sku)
        .slice(0, 8)
        .map((s) => ({ name: s.sku, value: s.net_sales, units: s.units })),
    [insights]
  );

  const departmentData = React.useMemo(
    () =>
      (insights?.top_departments ?? [])
        .filter((d) => d.name)
        .slice(0, 8)
        .map((d) => ({ name: d.name, value: d.net_sales, units: d.units })),
    [insights]
  );

  const sectionData = React.useMemo(
    () =>
      (insights?.top_sections ?? [])
        .filter((d) => d.name)
        .slice(0, 8)
        .map((d) => ({ name: d.name, value: d.net_sales, units: d.units })),
    [insights]
  );

  const channelData = React.useMemo(
    () =>
      (insights?.top_channels ?? [])
        .filter((d) => d.name)
        .slice(0, 8)
        .map((d) => ({ name: d.name, value: d.net_sales, units: d.units })),
    [insights]
  );

  const discountedSkuData = React.useMemo(
    () =>
      (insights?.top_discounted_skus ?? []).slice(0, 8).map((s) => ({
        name: s.sku,
        value: s.discount_amount,
        units: s.units,
      })),
    [insights]
  );

  const dims: { id: BreakdownDim; label: string; data: { name: string; value: number }[]; filterKey: string; hasTarget: boolean; valueLabel?: string }[] = [
    { id: 'zone', label: 'Zone', data: zoneData, filterKey: 'zone', hasTarget: true },
    { id: 'store', label: 'Store', data: storeData, filterKey: 'store', hasTarget: true },
    { id: 'state', label: 'State', data: stateData, filterKey: 'state', hasTarget: true },
    { id: 'city', label: 'City', data: cityData, filterKey: 'city', hasTarget: true },
    { id: 'channel', label: 'Channel', data: channelData, filterKey: 'channel', hasTarget: true },
    { id: 'sku', label: 'SKU', data: skuData, filterKey: 'sku', hasTarget: false },
    { id: 'department', label: 'Dept', data: departmentData, filterKey: 'department', hasTarget: false },
    { id: 'section', label: 'Section', data: sectionData, filterKey: 'section', hasTarget: false },
    { id: 'discount', label: 'Disc. SKU', data: discountedSkuData, filterKey: 'sku', hasTarget: false, valueLabel: 'Discount' },
  ];

  const active = dims.find((d) => d.id === dim)!;

  const chartData = React.useMemo(() => {
    const rawData = active.data;
    if (!rawData.length) return [];

    if (!active.hasTarget) {
      return rawData.map((d) => ({
        ...d,
        net: d.value,
      }));
    }

    const totalItemNet = rawData.reduce((s, d) => s + (d.value || 0), 0);
    const targetTotal =
      targetSummary?.totalTarget && targetSummary.totalTarget > 0
        ? targetSummary.totalTarget
        : 0;

    // Real Target LFS total allocated by each slice's net-sales share (same period as KPI cards)
    return rawData.map((d) => {
      const share = totalItemNet > 0 ? d.value / totalItemNet : 1 / rawData.length;
      const targetVal = targetTotal > 0 ? Math.round(targetTotal * share) : 0;
      const achievementPct = targetVal > 0 ? Math.round(((d.value || 0) * 100) / targetVal) : undefined;
      return {
        ...d,
        net: d.value,
        target: targetVal,
        achievementPct,
      };
    });
  }, [active.data, active.hasTarget, targetSummary]);

  const showTargetBars = Boolean(
    active.hasTarget && chartData.some((d) => Number((d as { target?: number }).target || 0) > 0)
  );

  return (
    <ChartShell
      title="Sales Breakdown"
      subtitle={`${periodWord} · ${periodLabel} · ${showTargetBars ? 'Target vs Net Sales' : 'Net Sales Breakdown'}`}
      icon={<TrendingUp className="h-4 w-4 text-purple-600" />}
      accent="bg-purple-50/80 dark:bg-purple-500/10"
      loading={loading}
      height={320}
      action={
        <div className="flex flex-wrap rounded-lg border border-slate-200 bg-slate-50 p-0.5 dark:border-slate-700 dark:bg-slate-800/50">
          {dims.map((d) => (
            <button
              key={d.id}
              type="button"
              onClick={() => setDim(d.id)}
              className={`rounded-md px-2.5 py-1 text-[11px] font-semibold ${
                dim === d.id
                  ? 'bg-white text-purple-600 shadow-sm dark:bg-slate-900 dark:text-purple-400'
                  : 'text-slate-500 hover:text-slate-700'
              }`}
            >
              {d.label}
            </button>
          ))}
        </div>
      }
    >
      {chartData.length === 0 ? (
        <div className="flex h-full items-center justify-center text-xs text-slate-500">
          No data for this {periodWord.toLowerCase()} period
        </div>
      ) : showTargetBars ? (
        /* Grouped vertical bars — Target vs Net Sales (same style as Sales Trend) */
        <ResponsiveContainer debounce={80} width="100%" height="100%" minWidth={0}>
          <ComposedChart data={chartData} margin={{ top: 18, right: 12, left: 0, bottom: 4 }} barCategoryGap="18%" barGap={4}>
            <defs>
              <linearGradient id="breakdownNetGradient" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#00c9a7" />
                <stop offset="100%" stopColor="#0d9488" />
              </linearGradient>
              <linearGradient id="breakdownTargetGradient" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#8b5cf6" />
                <stop offset="100%" stopColor="#6366f1" />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
            <XAxis
              dataKey="name"
              tick={{ fontSize: 10, fill: '#64748b' }}
              axisLine={false}
              tickLine={false}
              interval={0}
              angle={chartData.length > 5 ? -25 : 0}
              textAnchor={chartData.length > 5 ? 'end' : 'middle'}
              height={chartData.length > 5 ? 48 : 28}
            />
            <YAxis
              yAxisId="left"
              tickFormatter={(v) => formatCompactInr(Number(v))}
              tick={{ fontSize: 10, fill: '#64748b' }}
              axisLine={false}
              tickLine={false}
              width={48}
            />
            <YAxis
              yAxisId="right"
              orientation="right"
              tickFormatter={(v) => `${v}%`}
              tick={{ fontSize: 10, fill: '#f59e0b' }}
              axisLine={false}
              tickLine={false}
              domain={[0, 120]}
              width={36}
            />
            <Tooltip
              formatter={(v: number, name: string) => [
                name.includes('%') ? `${v}%` : formatInr(v),
                name === 'target' || name === 'Target' ? 'Target' : name.includes('%') ? 'Achievement %' : 'Net Sales',
              ]}
              labelStyle={{ fontWeight: 600 }}
            />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            <Bar
              yAxisId="left"
              dataKey="target"
              name="Target"
              fill="url(#breakdownTargetGradient)"
              radius={[6, 6, 0, 0]}
              maxBarSize={28}
            >
              <LabelList
                dataKey="target"
                position="top"
                formatter={(v: number) => (v ? formatCompactInr(v) : '')}
                style={{ fontSize: 9, fill: '#7c3aed', fontWeight: 600 }}
              />
            </Bar>
            <Bar
              yAxisId="left"
              dataKey="net"
              name="Net Sales"
              fill="url(#breakdownNetGradient)"
              radius={[6, 6, 0, 0]}
              maxBarSize={28}
              className="cursor-pointer"
              onClick={(d) => onCrossFilter(active.filterKey, (d as { name: string }).name)}
            >
              <LabelList
                dataKey="net"
                position="top"
                formatter={(v: number) => (v ? formatCompactInr(v) : '')}
                style={{ fontSize: 9, fill: '#0d9488', fontWeight: 600 }}
              />
            </Bar>
            <Line
              yAxisId="right"
              type="monotone"
              dataKey="achievementPct"
              name="Achievement %"
              stroke="#f59e0b"
              strokeDasharray="5 5"
              strokeWidth={2}
              dot={{ r: 3, fill: '#f59e0b' }}
            >
              <LabelList
                dataKey="achievementPct"
                position="top"
                formatter={(v: number) => (v ? `${v}%` : '')}
                style={{ fontSize: 9, fill: '#d97706', fontWeight: 700 }}
              />
            </Line>
          </ComposedChart>
        </ResponsiveContainer>
      ) : (
        <ResponsiveContainer debounce={80} width="100%" height="100%" minWidth={0}>
          <BarChart data={chartData} layout="vertical" margin={{ left: 4, right: 28, top: 4, bottom: 4 }}>
            <defs>
              <linearGradient id="breakdownNetOnlyGradient" x1="0" y1="0" x2="1" y2="0">
                <stop offset="0%" stopColor="#00c9a7" />
                <stop offset="100%" stopColor="#0d9488" />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" horizontal={false} />
            <XAxis
              type="number"
              tickFormatter={(v) => formatCompactInr(v)}
              tick={{ fontSize: 10 }}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              type="category"
              dataKey="name"
              width={dim === 'sku' || dim === 'discount' ? 100 : 88}
              tick={{ fontSize: 10 }}
              axisLine={false}
              tickLine={false}
            />
            <Tooltip
              formatter={(v: number) => [formatInr(v), active.valueLabel ?? 'Net Sales']}
            />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            <Bar
              dataKey="net"
              name={active.valueLabel ?? 'Net Sales'}
              fill="url(#breakdownNetOnlyGradient)"
              radius={[0, 4, 4, 0]}
              barSize={12}
              className="cursor-pointer"
              onClick={(d) => onCrossFilter(active.filterKey, (d as { name: string }).name)}
            >
              <LabelList dataKey="net" position="right" formatter={(v: number) => (v ? formatCompactInr(v) : '')} style={{ fontSize: 10, fill: '#0d9488', fontWeight: 600 }} />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      )}
    </ChartShell>
  );
}

/** Shared timeline mode for Trend + Breakdown */
export function SalesTrendBreakdownRow({
  rows,
  insights,
  monthlyTrendData,
  channelLabel,
  loading,
  chartLoading,
  activeKey,
  onCrossFilter,
  timelineMode,
  timelineValues = [],
  targetSummary,
  trendGrain,
  onTrendGrainChange,
  breakdownDim,
  onBreakdownDimChange,
}: {
  rows: SalesRow[];
  insights: SalesInsights | null;
  monthlyTrendData: MonthlyTrendPoint[];
  channelLabel: string;
  loading: boolean;
  chartLoading: boolean;
  activeKey: string | null;
  onCrossFilter: (key: string, value: string) => void;
  timelineMode: TimelineMode;
  timelineValues?: string[];
  targetSummary?: TargetSummary | null;
  trendGrain?: TrendGrain;
  onTrendGrainChange?: (grain: TrendGrain) => void;
  breakdownDim?: BreakdownDim;
  onBreakdownDimChange?: (dim: BreakdownDim) => void;
}) {
  return (
    <div className="grid min-w-0 gap-4 [grid-template-columns:repeat(auto-fit,minmax(min(100%,22rem),1fr))]">
      <SalesTrendTabs
        rows={rows}
        insights={insights}
        loading={chartLoading}
        monthlyTrendData={monthlyTrendData}
        timelineMode={timelineMode}
        targetSummary={targetSummary}
        grain={trendGrain}
        onGrainChange={onTrendGrainChange}
      />
      <SalesBreakdownSection
        rows={rows}
        insights={insights}
        channelLabel={channelLabel}
        loading={loading}
        activeKey={activeKey}
        onCrossFilter={onCrossFilter}
        timelineMode={timelineMode}
        timelineValues={timelineValues}
        targetSummary={targetSummary}
        dim={breakdownDim}
        onDimChange={onBreakdownDimChange}
      />
    </div>
  );
}

export function TopPerformersSection({
  rows,
  insights,
  loading,
  onSkuClick,
}: {
  rows: SalesRow[];
  insights: SalesInsights | null;
  loading: boolean;
  onSkuClick: (sku: string) => void;
}) {
  // Backend insights only — never re-aggregate paginated table rows.
  const topSku = insights?.top_sold_skus ?? [];
  const topCat = (insights?.top_sections ?? []).slice(0, 6).map((d) => ({ name: d.name, value: d.net_sales, units: d.units }));
  const topDept = (insights?.top_departments ?? []).slice(0, 6).map((d) => ({ name: d.name, value: d.net_sales, units: d.units }));
  const topStory: { name: string; value: number; units?: number }[] = [];
  const topStore = insights?.top_revenue_sites ?? [];

  return (
    <div className="grid min-w-0 gap-4 [grid-template-columns:repeat(auto-fit,minmax(min(100%,22rem),1fr))]">
      <ChartShell title="Top SKU" subtitle="By net sales" icon={<Award className="h-4 w-4 text-teal-600" />} accent="bg-teal-50/80" loading={loading} height={260}>
        <div className="space-y-1.5 overflow-y-auto">
          {topSku.slice(0, 8).map((s, i) => (
            <button key={s.sku} type="button" onClick={() => onSkuClick(s.sku)} className="flex w-full items-center justify-between rounded-lg px-2 py-1.5 text-left text-xs hover:bg-teal-50 dark:hover:bg-teal-500/10">
              <span className="font-semibold text-slate-800 dark:text-white"><span className="mr-2 text-teal-600">{i + 1}</span>{s.sku}</span>
              <span className="text-slate-500">{formatCompactInr(s.net_sales)}</span>
            </button>
          ))}
        </div>
      </ChartShell>
      {[
        { title: 'Top Sections', data: topCat },
        { title: 'Top Departments', data: topDept },
        { title: 'Top Stories', data: topStory },
      ].map((block) => (
        <React.Fragment key={block.title}>
        <ChartShell title={block.title} icon={<TrendingUp className="h-4 w-4 text-blue-600" />} accent="bg-indigo-50/80" loading={loading} height={260}>
          <ResponsiveContainer debounce={80} width="100%" height="100%" minWidth={0}>
            <BarChart data={block.data} layout="vertical" margin={{ left: 0, right: 8 }}>
              <XAxis type="number" hide />
              <YAxis type="category" dataKey="name" width={80} tick={{ fontSize: 10 }} axisLine={false} tickLine={false} />
              <Tooltip formatter={(v: number) => formatInr(v)} />
              <Bar dataKey="value" fill="#6366f1" radius={[0, 6, 6, 0]} barSize={14} />
            </BarChart>
          </ResponsiveContainer>
        </ChartShell>
        </React.Fragment>
      ))}
      <ChartShell title="Top Stores" subtitle="By net sales" icon={<TrendingUp className="h-4 w-4 text-blue-600" />} accent="bg-emerald-50/80" loading={loading} height={260}>
        <div className="space-y-1.5">
          {topStore.slice(0, 8).map((s, i) => (
            <div key={s.site_name ?? i} className="flex items-center justify-between rounded-lg px-2 py-1.5 text-xs">
              <span className="font-semibold text-slate-800 dark:text-white">{s.site_name}</span>
              <span className="text-blue-600">{formatCompactInr(s.net_sales)}</span>
            </div>
          ))}
        </div>
      </ChartShell>
    </div>
  );
}

function TreemapNode(props: { x?: number; y?: number; width?: number; height?: number; name?: string; size?: number; fill?: string }) {
  const { x = 0, y = 0, width = 0, height = 0, name = '', size = 0, fill = '#6366f1' } = props;
  if (width < 4 || height < 4) return null;
  return (
    <g>
      <rect x={x} y={y} width={width} height={height} fill={fill} rx={6} />
      {width > 48 && height > 28 && (
        <>
          <text x={x + 6} y={y + 16} fill="#fff" fontSize={10} fontWeight={600}>{name}</text>
          <text x={x + 6} y={y + 28} fill="rgba(255,255,255,0.85)" fontSize={9}>{formatCompactInr(size)}</text>
        </>
      )}
    </g>
  );
}

export function AdvancedAnalyticsSection({ rows: _rows, loading }: { rows: SalesRow[]; loading: boolean }) {
  // No client-side invent/aggregate — wait for dedicated backend analytics payloads.
  const pareto: ReturnType<typeof buildParetoData> = [];
  const heatmap: ReturnType<typeof buildHeatmapData> = [];
  const catContrib: { name: string; value: number; units: number; fill: string }[] = [];
  const deptContrib: { name: string; value: number; units: number }[] = [];
  const total = 1;

  return (
    <div className="space-y-4">
      <h3 className="text-sm font-semibold text-slate-800 dark:text-white">Advanced Analytics</h3>
      <div className="grid min-w-0 gap-4 [grid-template-columns:repeat(auto-fit,minmax(min(100%,22rem),1fr))]">
        <ChartShell title="Store vs Sales Heatmap" icon={<BarChart3 className="h-4 w-4 text-rose-600" />} accent="bg-rose-50/80" loading={loading} height={280}>
          <ResponsiveContainer debounce={80} width="100%" height="100%" minWidth={0}>
            <ScatterChart margin={{ top: 8, right: 8, bottom: 8, left: 8 }}>
              <XAxis type="category" dataKey="week" name="Week" tick={{ fontSize: 10 }} />
              <YAxis type="category" dataKey="store" name="Store" tick={{ fontSize: 9 }} width={72} />
              <ZAxis type="number" dataKey="value" range={[80, 400]} />
              <Tooltip cursor={{ strokeDasharray: '3 3' }} formatter={(v: number) => formatInr(v)} />
              <Scatter data={heatmap} fill="#6366f1" fillOpacity={0.7} />
            </ScatterChart>
          </ResponsiveContainer>
        </ChartShell>
        <ChartShell title="Pareto Analysis (80/20)" icon={<TrendingUp className="h-4 w-4 text-blue-600" />} accent="bg-indigo-50/80" loading={loading} height={280}>
          <ResponsiveContainer debounce={80} width="100%" height="100%" minWidth={0}>
            <ComposedChart data={pareto}>
              <CartesianGrid strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="name" tick={{ fontSize: 9 }} interval={0} angle={-25} textAnchor="end" height={50} />
              <YAxis yAxisId="left" tickFormatter={(v) => formatCompactInr(v)} tick={{ fontSize: 10 }} />
              <YAxis yAxisId="right" orientation="right" domain={[0, 100]} tickFormatter={(v) => `${v}%`} tick={{ fontSize: 10 }} />
              <Tooltip />
              <Bar yAxisId="left" dataKey="sales" fill="#6366f1" radius={[4, 4, 0, 0]} />
              <Line yAxisId="right" type="monotone" dataKey="cumulativePct" stroke="#10b981" strokeWidth={2} dot={false} />
            </ComposedChart>
          </ResponsiveContainer>
        </ChartShell>
        <ChartShell title="Category Contribution" icon={<Award className="h-4 w-4 text-teal-600" />} accent="bg-teal-50/80" loading={loading} height={260}>
          <ResponsiveContainer debounce={80} width="100%" height="100%" minWidth={0}>
            <Treemap data={catContrib} dataKey="value" nameKey="name" content={<TreemapNode />} />
          </ResponsiveContainer>
        </ChartShell>
        <ChartShell title="Department Contribution" icon={<Percent className="h-4 w-4 text-amber-600" />} accent="bg-amber-50/80" loading={loading} height={260}>
          <ResponsiveContainer debounce={80} width="100%" height="100%" minWidth={0}>
            <PieChart>
              <Pie data={deptContrib} dataKey="value" nameKey="name" outerRadius={90}>
                {deptContrib.map((_, i) => <Cell key={i} fill={['#6366f1', '#10b981', '#f59e0b', '#ec4899', '#0ea5e9', '#8b5cf6'][i % 6]} />)}
              </Pie>
              <Tooltip formatter={(v: number) => `${((v / total) * 100).toFixed(1)}% · ${formatInr(v)}`} />
            </PieChart>
          </ResponsiveContainer>
        </ChartShell>
      </div>
      <p className="text-[11px] text-slate-500">Top 20% SKUs contribute ~{pareto.filter((p) => p.cumulativePct <= 80).length || 3} of tracked assortment to 80% of page sales.</p>
    </div>
  );
}

export { ChartShell };

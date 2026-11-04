import React from 'react';
import { ArrowDownRight, ArrowUpRight } from 'lucide-react';
import { cardClass, cardHoverClass } from './tokens';
import { KpiHoverDetail } from './KpiHoverDetail';
import './ShimmerCard.css';

export type KpiCardProps = {
  key?: React.Key;
  title: string;
  value: string;
  /** Small secondary line under the value (e.g. Target ₹41.4Cr) */
  previous?: string;
  change?: number | null;
  /** @deprecated Sparkline removed — kept optional for call-site compatibility */
  trend?: number[];
  icon: React.ElementType;
  accent?: string;
  /** Shown on hover — explains what the metric means / why it looks this way */
  tooltip?: string;
  invertGrowth?: boolean;
  compact?: boolean;
  /** Per-card loading (e.g. Target / Achievement fetch separately from main KPIs) */
  loading?: boolean;
  /** Subtle value color: good / warn / bad (optional) */
  valueTone?: 'good' | 'warn' | 'bad';
};

/** Single KPI-shaped shimmer card (sweep gradient). */
export function KpiShimmerCard() {
  return (
    <div className="kpi-shimmer" aria-busy="true" aria-label="Loading KPI">
      <div className="wrapper">
        <div className="circle" />
        <div className="line-1" />
        <div className="line-2" />
        <div className="line-3" />
        <div className="line-4" />
      </div>
    </div>
  );
}

export function EnterpriseKpiCard({
  title,
  value,
  previous,
  change,
  icon: Icon,
  accent = 'bg-gradient-to-br from-blue-600 to-blue-700',
  tooltip,
  invertGrowth = false,
  compact = false,
  loading = false,
  valueTone,
}: KpiCardProps) {
  // NOTE: `change` has no default — `change={undefined}` must stay undefined
  // so Target/Achievement don't show a fake "+0.0%" badge.
  const numericChange = change ?? 0;
  const positive = invertGrowth ? numericChange <= 0 : numericChange >= 0;
  const p = compact ? 'p-2' : 'p-3';
  const detail = [tooltip, previous ? `Context: ${previous}` : null].filter(Boolean).join(' · ');

  const valueToneClass =
    valueTone === 'good'
      ? 'text-emerald-700 dark:text-emerald-400'
      : valueTone === 'warn'
        ? 'text-amber-700 dark:text-amber-400'
        : valueTone === 'bad'
          ? 'text-rose-700 dark:text-rose-400'
          : 'text-slate-900 dark:text-white';

  if (loading) {
    return <KpiShimmerCard />;
  }

  return (
    <KpiHoverDetail title={title} detail={detail} className={`group relative ${cardClass} ${cardHoverClass} ${p}`}>
      <div className="flex items-start justify-between gap-2">
        <div
          className={`flex shrink-0 items-center justify-center rounded-lg ${accent} ${
            compact ? 'h-6 w-6' : 'h-8 w-8 rounded-xl'
          }`}
        >
          <Icon className={`${compact ? 'h-3 w-3' : 'h-3.5 w-3.5'} text-white`} />
        </div>
        {change != null && Number.isFinite(numericChange) && (
          <span
            className={`inline-flex items-center gap-0.5 rounded-full px-1.5 py-0.5 font-bold ${
              compact ? 'text-[9px]' : 'text-[10px]'
            } ${
              positive
                ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-400'
                : 'bg-rose-50 text-rose-700 dark:bg-rose-500/15 dark:text-rose-400'
            }`}
          >
            {positive ? <ArrowUpRight className="h-3 w-3" /> : <ArrowDownRight className="h-3 w-3" />}
            {Math.abs(numericChange).toFixed(1)}%
          </span>
        )}
      </div>

      <p
        className={`font-medium uppercase tracking-wider text-slate-500 dark:text-slate-400 ${
          compact ? 'mt-1.5 text-[9px]' : 'mt-2.5 text-[10px]'
        }`}
      >
        {title}
      </p>
      <p
        className={`mt-0.5 font-bold tracking-tight ${valueToneClass} ${
          compact ? 'text-sm sm:text-base' : 'text-lg'
        }`}
      >
        {value}
      </p>
      {previous ? (
        <p
          className={`mt-0.5 font-medium text-slate-500 dark:text-slate-400 ${
            compact ? 'truncate text-[9px]' : 'text-[10px]'
          }`}
          title={previous}
        >
          {previous}
        </p>
      ) : null}
    </KpiHoverDetail>
  );
}

const STABLE_KPI_COLS = 'grid-cols-[repeat(auto-fill,minmax(11.25rem,1fr))]';

export function KpiGridShell({
  children,
  cols = STABLE_KPI_COLS,
}: {
  children: React.ReactNode;
  cols?: string;
}) {
  return <div className={`grid min-w-0 gap-2 sm:gap-2.5 ${cols}`}>{children}</div>;
}

export function KpiSkeletonGrid({
  count = 10,
  cols = STABLE_KPI_COLS,
}: {
  count?: number;
  cols?: string;
}) {
  return (
    <div className={`grid gap-3 ${cols}`}>
      {Array.from({ length: count }).map((_, i) => (
        <KpiShimmerCard key={i} />
      ))}
    </div>
  );
}

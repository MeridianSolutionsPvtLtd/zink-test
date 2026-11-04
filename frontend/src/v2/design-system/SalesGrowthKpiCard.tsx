import React, { useState } from 'react';
import { TrendingUp } from 'lucide-react';
import { cardClass, cardHoverClass } from './tokens';
import { KpiHoverDetail } from './KpiHoverDetail';

export type GrowthMode = 'GOLY' | 'GOLM' | 'GOLW';

export type GrowthRates = {
  goly: number | null;
  golm: number | null;
  golw: number | null;
};

const MODES: { id: GrowthMode; hint: string; detail: string }[] = [
  {
    id: 'GOLY',
    hint: 'vs last year',
    detail: 'Year-over-year growth — current period net sales vs the same period last year.',
  },
  {
    id: 'GOLM',
    hint: 'vs last month',
    detail: 'Month-over-month growth — latest month net sales vs the previous month.',
  },
  {
    id: 'GOLW',
    hint: 'vs last week',
    detail: 'Week-over-week growth — latest week net sales vs the previous week.',
  },
];

type Props = {
  growth?: GrowthRates | null;
  compact?: boolean;
};

const EMPTY_GROWTH: GrowthRates = { goly: null, golm: null, golw: null };

/** Sales Growth KPI — simple card with working GOLY / GOLM / GOLW period filter */
export function SalesGrowthKpiCard({ growth, compact = false }: Props) {
  const [mode, setMode] = useState<GrowthMode>('GOLM');
  const rates = growth ?? EMPTY_GROWTH;
  const raw = mode === 'GOLY' ? rates.goly : mode === 'GOLW' ? rates.golw : rates.golm;
  const hasValue = raw !== null && raw !== undefined && Number.isFinite(raw);
  const value = hasValue ? (raw as number) : 0;
  const positive = value >= 0;
  const meta = MODES.find((m) => m.id === mode)!;
  const p = compact ? 'p-2.5' : 'p-3';
  const detail = !hasValue
    ? `${meta.detail} Not enough history in the current filters to calculate this.`
    : meta.detail;

  return (
    <KpiHoverDetail
      title={`Sales Growth · ${mode}`}
      detail={detail}
      className={`group relative ${cardClass} ${cardHoverClass} ${p}`}
    >
      <div
        className={`flex ${compact ? 'h-7 w-7' : 'h-8 w-8'} items-center justify-center rounded-xl bg-gradient-to-br from-blue-600 to-blue-700`}
      >
        <TrendingUp className="h-3.5 w-3.5 text-white" />
      </div>

      <p
        className={`mt-2.5 font-medium uppercase tracking-wider text-slate-500 dark:text-slate-400 ${
          compact ? 'text-[9px]' : 'text-[10px]'
        }`}
      >
        Sales Growth
      </p>

      <p
        className={`mt-0.5 font-bold tracking-tight ${compact ? 'text-base' : 'text-lg'} ${
          !hasValue
            ? 'text-slate-400'
            : positive
              ? 'text-emerald-600 dark:text-emerald-400'
              : 'text-rose-600 dark:text-rose-400'
        }`}
      >
        {!hasValue ? '—' : `${positive ? '+' : ''}${value.toFixed(1)}%`}
      </p>

      <div
        role="group"
        aria-label="Growth period"
        className="mt-2 grid grid-cols-3 gap-0.5 rounded-lg border border-slate-200 bg-slate-50 p-0.5 dark:border-slate-700 dark:bg-slate-800/80"
      >
        {MODES.map(({ id }) => {
          const active = mode === id;
          return (
            <button
              key={id}
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                setMode(id);
              }}
              className={`rounded-md py-1 text-[10px] font-semibold tracking-wide transition ${
                active
                  ? 'bg-blue-600 text-white shadow-sm'
                  : 'text-slate-500 hover:bg-white hover:text-slate-800 dark:text-slate-400 dark:hover:bg-slate-700 dark:hover:text-slate-100'
              }`}
            >
              {id}
            </button>
          );
        })}
      </div>

      <p className={`mt-1 text-slate-400 ${compact ? 'text-[8px]' : 'text-[10px]'}`}>{meta.hint}</p>
    </KpiHoverDetail>
  );
}

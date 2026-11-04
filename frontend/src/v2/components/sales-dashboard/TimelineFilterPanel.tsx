import React, { useEffect, useMemo, useState } from 'react';
import { CalendarDays } from 'lucide-react';
import { ExecutiveMultiSelect } from '../executive/ExecutiveMultiSelect';
import {
  TIMELINE_MODE_GROUPS,
  buildTimelineOptions,
  calendarYearFilterOptions,
  fyWeekFilterOptions,
  fyYearFilterOptions,
  FY_FILTER_START,
  FY_FILTER_START_DATE,
  currentIndianFyEnd,
  timelineModeLabel,
  timelineLabelForKey,
  type TimelineMode,
} from './salesAnalytics';
import type { SalesRow } from './types';

export type FinancialCalendar = {
  latest_year: number | null;
  version: string;
  years: { id: string; year: number; label: string }[];
  weeks: { id: string; year: number; label: string; dates: string[] }[];
};

type Props = {
  financialCalendar?: FinancialCalendar | null;
  mode: TimelineMode;
  values: string[];
  rows: SalesRow[];
  onModeChange: (mode: TimelineMode) => void;
  onValuesChange: (values: string[]) => void;
  onClear: () => void;
  /** Soft calendar hints from full dataset (YYYY-MM-DD) — never from filtered rows */
  dateMin?: string;
  dateMax?: string;
  /** Full month list from API (YYYY-MM) — preferred over sampling chart rows */
  monthOptions?: { id: string; label: string }[];
  /** Compact strip for PageHeader / navbar */
  variant?: 'card' | 'header';
  /** True while month / FY calendar option lists are still loading */
  loading?: boolean;
};

const dateInputClass =
  'min-w-[9.5rem] rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-[11px] font-medium text-slate-700 outline-none transition focus:border-blue-400 focus:ring-2 focus:ring-blue-500/20 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200';

function normalizeRange(from: string, to: string): [string, string] | [] {
  if (!from && !to) return [];
  let f = from;
  let t = to;
  if (f && t && f > t) [f, t] = [t, f];
  if (f && t) return [f, t];
  // Single side set → treat as one-day range until the other side is chosen
  const one = f || t;
  return [one, one];
}

/** All YYYY-MM keys between dateMin and dateMax (inclusive). */
function monthsFromDateBounds(dateMin?: string, dateMax?: string): { id: string; label: string }[] {
  if (!dateMin || !dateMax) return [];
  const floor = '2025-04';
  const startKey = dateMin.slice(0, 7) < floor ? floor : dateMin.slice(0, 7);
  const endKey = dateMax.slice(0, 7);
  if (!/^\d{4}-\d{2}$/.test(startKey) || !/^\d{4}-\d{2}$/.test(endKey)) return [];
  const [sy, sm] = startKey.split('-').map(Number);
  const [ey, em] = endKey.split('-').map(Number);
  const out: { id: string; label: string }[] = [];
  let y = sy;
  let m = sm;
  while (y < ey || (y === ey && m <= em)) {
    const id = `${y}-${String(m).padStart(2, '0')}`;
    out.push({ id, label: timelineLabelForKey('month', id) });
    m += 1;
    if (m > 12) {
      m = 1;
      y += 1;
    }
  }
  return out;
}

function yearsFromDateBounds(dateMin?: string, dateMax?: string): { id: string; label: string }[] {
  const y1 = dateMax ? Number(dateMax.slice(0, 4)) : undefined;
  return calendarYearFilterOptions(Number.isFinite(y1) ? y1 : undefined);
}


/** Timeline (time-frame) filter — Date/Day/Month/Year, FY Week/Year (from 1 Apr), Special Events */
export function TimelineFilterPanel({
  mode,
  values,
  rows,
  onModeChange,
  onValuesChange,
  onClear,
  dateMin,
  dateMax,
  monthOptions,
  variant = 'card',
  financialCalendar,
  loading = false,
}: Props) {
  const [weekYear, setWeekYear] = useState<number | null>(null);
  const fyYears = useMemo(
    () =>
      (financialCalendar?.years ?? []).filter(
        (y) => y.year >= FY_FILTER_START && y.year <= currentIndianFyEnd()
      ),
    [financialCalendar]
  );
  const selectedFy = mode === 'fy_year' && values[0] ? fyYears.find((y) => y.id === values[0])?.year : undefined;
  const selectedWeekYear = mode === 'fy_week' && values[0] ? financialCalendar?.weeks.find((w) => w.id === values[0])?.year : undefined;
  const activeWeekYear = selectedFy ?? selectedWeekYear ?? weekYear ?? financialCalendar?.latest_year;
  const mappedWeeks = useMemo(() => financialCalendar?.weeks.filter((w) => w.year === activeWeekYear) ?? [], [financialCalendar, activeWeekYear]);
  const builtOptions = useMemo(() => {
    if (mode === 'fy_week') return mappedWeeks.length ? mappedWeeks : fyWeekFilterOptions(dateMax ? new Date(dateMax) : undefined);
    if (mode === 'fy_year' && fyYears.length) return fyYears;
    if (mode === 'month') {
      const fromApi = (monthOptions ?? []).filter(
        (o) => o.id && o.id !== 'all' && /^\d{4}-\d{2}$/.test(o.id)
      );
      if (fromApi.length) {
        return [...fromApi]
          .filter((o) => o.id >= '2025-04')
          .sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
      }
      const fromBounds = monthsFromDateBounds(dateMin, dateMax);
      if (fromBounds.length) return fromBounds;
    }
    if (mode === 'year') {
      const fromBounds = yearsFromDateBounds(dateMin, dateMax);
      if (fromBounds.length) return fromBounds;
    }
    if ((mode as string) === 'fy_week') {
      return fyWeekFilterOptions(dateMax ? new Date(dateMax) : undefined);
    }
    if (mode === 'fy_year') {
      const opts = buildTimelineOptions(rows, mode);
      if (opts.length) return opts;
      const yMax = dateMax ? Number(dateMax.slice(0, 4)) : undefined;
      return fyYearFilterOptions(Number.isFinite(yMax) ? yMax + 1 : undefined);
    }
    return buildTimelineOptions(rows, mode);
  }, [mode, rows, monthOptions, dateMin, dateMax, financialCalendar, mappedWeeks, fyYears]);

  const options = useMemo(() => builtOptions.map((o) => o.id), [builtOptions]);
  const optionLabels = useMemo(() => {
    const map = new Map(builtOptions.map((o) => [o.id, o.label]));
    return map;
  }, [builtOptions]);

  const useLabels = mode === 'event' || mode === 'fy_week' || mode === 'month' || mode === 'fy_year';

  const displayOptions = useMemo(() => {
    if (useLabels) return builtOptions.map((o) => o.label);
    return options;
  }, [builtOptions, options, useLabels]);

  const displayValues = useMemo(() => {
    if (useLabels) return values.map((id) => optionLabels.get(id) ?? id);
    return values;
  }, [useLabels, values, optionLabels]);

  const handleDisplayChange = (selectedLabels: string[]) => {
    if (useLabels) {
      const byLabel = new Map(builtOptions.map((o) => [o.label, o.id]));
      onValuesChange(selectedLabels.map((l) => byLabel.get(l) ?? l).filter(Boolean) as string[]);
      return;
    }
    onValuesChange(selectedLabels);
  };

  // Draft From/To so picking one side doesn't lock the calendar to a single day
  const [draftFrom, setDraftFrom] = useState('');
  const [draftTo, setDraftTo] = useState('');

  useEffect(() => {
    if (mode !== 'date') {
      setDraftFrom('');
      setDraftTo('');
      return;
    }
    setDraftFrom(values[0] ?? '');
    setDraftTo(values[1] ?? values[0] ?? '');
  }, [mode, values]);

  const commitDateRange = (from: string, to: string) => {
    const next = normalizeRange(from, to);
    onValuesChange([...next]);
  };

  const isHeader = variant === 'header';
  const modeButtons = (
    <div
      className={
        isHeader
          ? 'flex max-w-full items-center gap-x-2 gap-y-1 overflow-x-auto pb-0.5 [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden'
          : 'flex flex-wrap items-center gap-x-3 gap-y-2'
      }
    >
      {TIMELINE_MODE_GROUPS.map((group) => (
        <div
          key={group.title}
          className={`flex items-center gap-1 ${isHeader ? 'shrink-0' : 'flex-wrap gap-1.5'}`}
        >
          {!isHeader && (
            <span className="hidden text-[9px] font-bold uppercase tracking-wider text-slate-400 sm:inline">
              {group.title.replace(' Intervals', '').replace('Retail & ', '')}:
            </span>
          )}
          {group.modes.map((m) => {
            const active = mode === m.id;
            return (
              <button
                key={m.id}
                type="button"
                onClick={() => {
                  onModeChange(m.id);
                  if (m.id === 'fy_week') {
                    if (financialCalendar) {
                      const year = selectedFy ?? financialCalendar.latest_year;
                      setWeekYear(year);
                      onValuesChange(financialCalendar.weeks.filter((w) => w.year === year).map((w) => w.id));
                    } else {
                      onValuesChange(fyWeekFilterOptions(dateMax ? new Date(dateMax) : undefined).map((w) => w.id));
                    }
                  } else if (m.id === 'fy_year' && fyYears.length) {
                    onValuesChange([fyYears[0].id]);
                  } else onValuesChange([]);
                }}
                className={`rounded-md font-semibold transition ${
                  isHeader
                    ? `whitespace-nowrap px-1.5 py-0.5 text-[10px] ${
                        active
                          ? 'bg-blue-600 text-white shadow-sm'
                          : 'border border-slate-200 bg-white text-slate-600 hover:border-blue-300 hover:text-blue-700 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300'
                      }`
                    : `px-2 py-1 text-[10px] sm:px-2.5 sm:text-[11px] ${
                        active
                          ? 'bg-blue-600 text-white shadow-sm'
                          : 'border border-slate-200 bg-white text-slate-600 hover:border-blue-300 hover:text-blue-700 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300'
                      }`
                }`}
              >
                {m.label}
              </button>
            );
          })}
        </div>
      ))}
    </div>
  );

  const dateRangePicker = (
    <div className="flex flex-wrap items-end gap-2">
      <label className="block">
        <span className="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-slate-500">
          From
        </span>
        <input
          type="date"
          value={draftFrom}
          min={FY_FILTER_START_DATE}
          max={dateMax}
          onChange={(e) => {
            const from = e.target.value;
            setDraftFrom(from);
            // Apply as soon as both sides exist; otherwise wait for To
            if (from && draftTo) commitDateRange(from, draftTo);
            else if (from && !draftTo) {
              /* keep draft only — don’t shrink dataset yet */
            } else if (!from && !draftTo) {
              onValuesChange([]);
            }
          }}
          className={dateInputClass}
        />
      </label>
      <label className="block">
        <span className="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-slate-500">
          To
        </span>
        <input
          type="date"
          value={draftTo}
          min={draftFrom || FY_FILTER_START_DATE}
          max={dateMax}
          onChange={(e) => {
            const to = e.target.value;
            setDraftTo(to);
            if (draftFrom && to) commitDateRange(draftFrom, to);
            else if (!draftFrom && to) {
              /* wait for From */
            } else if (!draftFrom && !to) {
              onValuesChange([]);
            }
          }}
          className={dateInputClass}
        />
      </label>
      <button
        type="button"
        disabled={!draftFrom || !draftTo}
        onClick={() => commitDateRange(draftFrom, draftTo)}
        className="rounded-lg bg-blue-600 px-3 py-1.5 text-[11px] font-semibold text-white shadow-sm disabled:cursor-not-allowed disabled:opacity-40"
      >
        Apply
      </button>
      {(draftFrom || draftTo) && (
        <p className="pb-1.5 text-[10px] text-slate-400">
          {draftFrom || '…'} → {draftTo || '…'}
          {draftFrom && draftTo && values.length >= 2 ? ' · active' : draftFrom && draftTo ? '' : ' · pick both dates'}
        </p>
      )}
    </div>
  );

  const valueSelect =
    mode === 'date' ? (
      dateRangePicker
    ) : (
      <div className={isHeader ? 'w-52 max-w-full min-w-0' : 'max-w-md'}>
        {mode === 'fy_week' && financialCalendar && (
          <select aria-label="Financial year for weeks" className={dateInputClass} value={activeWeekYear ?? ''} onChange={(e) => {
            const year = Number(e.target.value);
            setWeekYear(year);
            onValuesChange(financialCalendar.weeks.filter((w) => w.year === year).map((w) => w.id));
          }}>
            {fyYears.map((y) => <option key={y.id} value={y.year}>{y.label}</option>)}
          </select>
        )}
        <ExecutiveMultiSelect
          dense={isHeader}
          multiple
          label={isHeader ? timelineModeLabel(mode) : `Select ${timelineModeLabel(mode)}`}
          icon={CalendarDays}
          options={displayOptions}
          value={displayValues}
          onChange={handleDisplayChange}
          placeholder={
            loading && displayOptions.length === 0
              ? 'Loading…'
              : `All ${timelineModeLabel(mode).toLowerCase()}s`
          }
          loading={loading && displayOptions.length === 0}
        />
        {mode === 'fy_year' && financialCalendar && (
          <ExecutiveMultiSelect
            dense={isHeader}
            multiple
            label="FY Week"
            icon={CalendarDays}
            options={mappedWeeks.map((w) => w.label)}
            value={[]}
            placeholder="Select financial weeks"
            onChange={(labels) => {
              const selected = mappedWeeks.filter((w) => labels.includes(w.label)).map((w) => w.id);
              if (selected.length) { onModeChange('fy_week'); onValuesChange(selected); }
            }}
          />
        )}
      </div>
    );

  const clearCount = mode === 'date' ? (values.length > 0 || draftFrom || draftTo ? 1 : 0) : values.length;

  if (variant === 'header') {
    return (
      <div className="flex w-full min-w-0 flex-wrap items-center justify-between gap-x-3 gap-y-2">
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <CalendarDays className="h-3.5 w-3.5 text-blue-600" />
            <span className="text-[10px] font-bold uppercase tracking-wider text-slate-500">Timeline</span>
            <span className="hidden text-[10px] text-slate-400 sm:inline">
              {mode === 'date' ? 'Pick a date range' : 'FY starts 1 Apr'}
            </span>
            {clearCount > 0 && (
              <button
                type="button"
                onClick={() => {
                  setDraftFrom('');
                  setDraftTo('');
                  onClear();
                }}
                className="text-[10px] font-semibold text-blue-600 hover:underline"
              >
                Clear
              </button>
            )}
          </div>
          {modeButtons}
        </div>
        <div className="sm:ml-auto shrink-0">{valueSelect}</div>
      </div>
    );
  }

  return (
    <div className="rounded-2xl border border-slate-200/80 bg-white p-4 shadow-[0_1px_3px_rgba(15,23,42,0.04),0_8px_24px_rgba(15,23,42,0.04)] dark:border-slate-800/80 dark:bg-slate-900/70">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-center gap-2">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-blue-600 to-blue-700 text-white">
            <CalendarDays className="h-4 w-4" />
          </div>
          <div>
            <p className="text-sm font-semibold text-slate-900 dark:text-white">Timeline</p>
            <p className="text-[11px] text-slate-500">
              {mode === 'date'
                ? 'Select From–To dates on the calendar'
                : `Time frames · FY starts 1 April · pick specific ${timelineModeLabel(mode).toLowerCase()}s`}
            </p>
          </div>
        </div>
        {clearCount > 0 && (
          <button
            type="button"
            onClick={() => {
              setDraftFrom('');
              setDraftTo('');
              onClear();
            }}
            className="rounded-lg border border-slate-200 px-2.5 py-1 text-[11px] font-semibold text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300"
          >
            Clear timeline
          </button>
        )}
      </div>
      <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>{modeButtons}</div>
        <div className="sm:ml-auto shrink-0">{valueSelect}</div>
      </div>
    </div>
  );
}

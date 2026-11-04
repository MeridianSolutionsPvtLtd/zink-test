import React from 'react';
import { Search } from 'lucide-react';

export type FilterSelectConfig = {
  id: string;
  label: string;
  value: string;
  options: { value: string; label: string }[];
  onChange: (value: string) => void;
};

const selectClass =
  'rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-[11px] font-medium text-slate-700 outline-none focus:border-blue-400 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200';

const inputClass =
  'w-full min-w-[140px] rounded-lg border border-slate-200 bg-white py-1.5 pl-8 pr-2 text-[11px] font-medium text-slate-700 outline-none focus:border-blue-400 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200';

export function uniqueFilterOptions(values: string[], allLabel = 'All') {
  const uniq = [...new Set(values.filter((v) => v && v !== '—'))].sort((a, b) => a.localeCompare(b));
  return [{ value: 'all', label: allLabel }, ...uniq.map((v) => ({ value: v, label: v }))];
}

type Props = {
  search?: string;
  onSearchChange?: (value: string) => void;
  searchPlaceholder?: string;
  selects?: FilterSelectConfig[];
  resultCount?: number;
  className?: string;
};

/** Compact search + select filters for inventory tables. */
export function TableFilterBar({
  search,
  onSearchChange,
  searchPlaceholder = 'Search table…',
  selects = [],
  resultCount,
  className = '',
}: Props) {
  return (
    <div className={`mb-2 flex flex-wrap items-center gap-2 ${className}`}>
      {onSearchChange !== undefined ? (
        <div className="relative min-w-[160px] flex-1 sm:max-w-[220px]">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
          <input
            value={search ?? ''}
            onChange={(e) => onSearchChange(e.target.value)}
            placeholder={searchPlaceholder}
            className={inputClass}
          />
        </div>
      ) : null}
      {selects.map((sel) => (
        <label key={sel.id} className="flex items-center gap-1.5">
          <span className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">{sel.label}</span>
          <select value={sel.value} onChange={(e) => sel.onChange(e.target.value)} className={selectClass}>
            {sel.options.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
      ))}
      {resultCount !== undefined ? (
        <span className="ml-auto text-[10px] text-slate-400">{resultCount} row{resultCount === 1 ? '' : 's'}</span>
      ) : null}
    </div>
  );
}

export function matchesTableSearch(q: string, parts: (string | number | null | undefined)[]) {
  if (!q.trim()) return true;
  const needle = q.trim().toLowerCase();
  return parts.some((p) => String(p ?? '').toLowerCase().includes(needle));
}

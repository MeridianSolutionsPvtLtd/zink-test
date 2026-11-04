import React, { useState } from 'react';
import { ChevronDown, FilterX, Loader2 } from 'lucide-react';

type Props = {
  badge?: string;
  title?: string;
  onClearAll?: () => void;
  disabled?: boolean;
  sticky?: boolean;
  /** Tighter padding / denser grid (Home dashboard) */
  compact?: boolean;
  /** Start collapsed so the page opens with more content visible */
  defaultCollapsed?: boolean;
  /** Small header spinner while filter option lists are still loading */
  optionsLoading?: boolean;
  children: React.ReactNode;
  footer?: React.ReactNode;
};

/** Unified filter panel shell — identical across all dashboards */
export function FilterPanelShell({
  badge = 'Global Filters',
  title,
  onClearAll,
  disabled,
  sticky = true,
  compact = false,
  defaultCollapsed = false,
  optionsLoading = false,
  children,
  footer,
}: Props) {
  const [collapsed, setCollapsed] = useState(defaultCollapsed);

  return (
    <div
      className={`rounded-xl border border-slate-200/80 bg-white/95 shadow-[0_1px_3px_rgba(15,23,42,0.04)] backdrop-blur-md dark:border-slate-800/80 dark:bg-slate-900/95 ${
        sticky ? 'sticky top-0 z-30' : ''
      }`}
    >
      <div
        className={`flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 dark:border-slate-800 ${
          compact ? 'px-3 py-1.5' : 'px-4 py-3'
        }`}
      >
        <div className="flex items-center gap-2">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">
            {title ?? 'Filters'}
          </span>
          <span className="rounded-full bg-blue-50 px-2 py-0.5 text-[10px] font-bold text-blue-700 dark:bg-blue-500/15 dark:text-blue-300">
            {badge}
          </span>
          {optionsLoading ? (
            <span
              className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-semibold text-slate-600 dark:bg-slate-800 dark:text-slate-300"
              aria-live="polite"
            >
              <Loader2 className="h-3 w-3 animate-spin text-blue-600" aria-hidden />
              Loading…
            </span>
          ) : null}
        </div>
        <div className="flex gap-1.5">
          {onClearAll && (
            <button
              type="button"
              onClick={onClearAll}
              disabled={disabled}
              className="inline-flex items-center gap-1 rounded-lg border border-slate-200 px-2.5 py-1 text-[11px] font-semibold text-slate-600 transition hover:border-blue-300 hover:bg-blue-50 disabled:opacity-40 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-blue-950/30"
            >
              <FilterX className="h-3 w-3" /> Clear
            </button>
          )}
          <button
            type="button"
            onClick={() => setCollapsed(!collapsed)}
            className="text-[11px] font-semibold text-slate-500"
          >
            {collapsed ? 'Expand' : 'Collapse'}{' '}
            <ChevronDown className={`inline h-3.5 w-3.5 transition-transform ${collapsed ? '' : 'rotate-180'}`} />
          </button>
        </div>
      </div>
      {!collapsed && (
        <>
          <div className={compact ? 'p-2.5' : 'p-4'}>
            <div
              className={
                compact
                  ? 'grid grid-cols-[repeat(auto-fill,minmax(10.5rem,1fr))] gap-x-2 gap-y-2'
                  : 'grid grid-cols-[repeat(auto-fill,minmax(12.5rem,1fr))] gap-x-3 gap-y-4'
              }
            >
              {children}
            </div>
          </div>
          {footer && (
            <div className={`border-t border-slate-100 dark:border-slate-800 ${compact ? 'px-3 py-1.5' : 'px-4 py-2'}`}>
              {footer}
            </div>
          )}
        </>
      )}
    </div>
  );
}

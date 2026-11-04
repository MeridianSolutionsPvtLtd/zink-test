import React from 'react';
import { Download, Loader2, Maximize2, RefreshCw } from 'lucide-react';
import { formatRefreshTime } from './tokens';

type Props = {
  icon?: React.ElementType;
  title: string;
  description?: string;
  badge?: string;
  lastRefresh?: Date;
  loading?: boolean;
  onRefresh?: () => void;
  onExport?: () => void;
  onFullscreen?: () => void;
  extra?: React.ReactNode;
  children?: React.ReactNode;
  /** Smaller chrome for denser dashboards (Home) */
  compact?: boolean;
};

export function PageHeader({
  icon: Icon,
  title,
  description,
  badge,
  lastRefresh,
  loading,
  onRefresh,
  onExport,
  onFullscreen,
  extra,
  children,
  compact = false,
}: Props) {
  return (
    <div
      className={`relative overflow-visible border border-slate-200/70 bg-white/95 shadow-[0_1px_2px_rgba(15,23,42,0.04),0_12px_32px_rgba(92,64,32,0.05)] backdrop-blur-sm dark:border-slate-800/80 dark:bg-slate-900/70 dark:shadow-none ${
        compact ? 'rounded-xl' : 'rounded-2xl'
      }`}
    >
      <div
        className={`pointer-events-none absolute inset-x-0 top-0 overflow-hidden bg-gradient-to-r from-slate-900 via-slate-700 to-amber-500 ${
          compact ? 'h-0.5 rounded-t-xl' : 'h-1 rounded-t-2xl'
        }`}
        aria-hidden
      />
      <div
        className={`flex flex-wrap items-center justify-between ${
          compact ? 'gap-2 px-3 py-2.5' : 'gap-4 px-5 py-5'
        }`}
      >
        <div className={`flex items-center ${compact ? 'gap-2' : 'gap-3 items-start'}`}>
          {Icon && (
            <div
              className={`flex shrink-0 items-center justify-center rounded-lg bg-slate-900 text-amber-300 dark:bg-white dark:text-slate-900 ${
                compact ? 'h-8 w-8 shadow-sm' : 'h-11 w-11 rounded-xl shadow-lg shadow-slate-900/15'
              }`}
            >
              <Icon className={compact ? 'h-4 w-4' : 'h-5 w-5'} />
            </div>
          )}
          <div>
            {badge && (
              <p className="text-[10px] font-bold uppercase tracking-widest text-blue-600 dark:text-blue-400">{badge}</p>
            )}
            <h1
              className={`font-bold tracking-tight text-slate-900 dark:text-white ${
                compact ? 'text-base sm:text-lg' : 'text-xl sm:text-2xl'
              }`}
            >
              {title}
            </h1>
            {description && <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">{description}</p>}
            {lastRefresh && (
              <p className="mt-1.5 text-[11px] text-slate-400">
                Last refresh:{' '}
                <span className="font-medium text-slate-600 dark:text-slate-300">
                  {formatRefreshTime(lastRefresh)}
                </span>
              </p>
            )}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {extra}
          {onExport && (
            <button
              type="button"
              onClick={onExport}
              className="inline-flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-3.5 py-2 text-xs font-semibold text-slate-700 transition hover:-translate-y-px hover:border-slate-400 hover:shadow-sm active:translate-y-0 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800"
            >
              <Download className="h-3.5 w-3.5" /> Export
            </button>
          )}
          {onFullscreen && (
            <button
              type="button"
              onClick={onFullscreen}
              className="inline-flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-3.5 py-2 text-xs font-semibold text-slate-700 transition hover:border-blue-300 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
            >
              <Maximize2 className="h-3.5 w-3.5" />
            </button>
          )}
          {onRefresh && (
            <button
              type="button"
              onClick={onRefresh}
              disabled={loading}
              className={`inline-flex items-center gap-1.5 rounded-lg bg-slate-900 text-xs font-semibold text-white shadow-sm transition hover:-translate-y-px hover:bg-slate-800 active:translate-y-0 disabled:opacity-50 dark:bg-white dark:text-slate-900 dark:hover:bg-slate-100 ${
                compact ? 'px-2.5 py-1.5' : 'rounded-xl px-4 py-2 gap-2'
              }`}
            >
              {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
              Refresh
            </button>
          )}
        </div>
      </div>
      {children && (
        <div
          className={`border-t border-slate-100 dark:border-slate-800 ${
            compact ? 'px-3 py-2' : 'px-5 py-3'
          }`}
        >
          {children}
        </div>
      )}
    </div>
  );
}

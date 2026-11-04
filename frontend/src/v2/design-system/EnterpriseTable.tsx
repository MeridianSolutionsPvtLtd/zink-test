import React from 'react';
import { ChevronLeft, ChevronRight, Download, Loader2, Search } from 'lucide-react';
import { cardClass } from './tokens';

type Props = {
  title: string;
  subtitle?: string;
  search?: string;
  onSearchChange?: (value: string) => void;
  searchPlaceholder?: string;
  toolbar?: React.ReactNode;
  onExport?: () => void;
  loading?: boolean;
  total?: number;
  page?: number;
  totalPages?: number;
  onPageChange?: (page: number) => void;
  pageSize?: number;
  children: React.ReactNode;
  className?: string;
};

/** Unified enterprise table shell — sticky header, search, pagination */
export function EnterpriseTable({
  title,
  subtitle,
  search,
  onSearchChange,
  searchPlaceholder = 'Search…',
  toolbar,
  onExport,
  loading,
  total,
  page,
  totalPages,
  onPageChange,
  pageSize,
  children,
  className = '',
}: Props) {
  const showPagination =
    page != null && totalPages != null && totalPages > 1 && onPageChange;

  return (
    <div className={`${cardClass} overflow-hidden ${className}`}>
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-100 px-5 py-4 dark:border-slate-800">
        <div>
          <h3 className="text-sm font-semibold tracking-tight text-slate-900 dark:text-white">{title}</h3>
          {subtitle && <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">{subtitle}</p>}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {onSearchChange && (
            <div className="relative">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
              <input
                type="search"
                value={search ?? ''}
                onChange={(e) => onSearchChange(e.target.value)}
                placeholder={searchPlaceholder}
                className="w-44 rounded-xl border border-slate-200 bg-white py-2 pl-8 pr-3 text-xs text-slate-800 outline-none ring-blue-500/30 focus:ring-2 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100 sm:w-52"
              />
            </div>
          )}
          {toolbar}
          {onExport && (
            <button
              type="button"
              onClick={onExport}
              className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-700 transition hover:border-blue-300 hover:bg-blue-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
            >
              <Download className="h-3.5 w-3.5" /> Export
            </button>
          )}
        </div>
      </div>

      <div className="relative overflow-x-auto">
        {loading && (
          <div className="absolute inset-0 z-10 flex items-center justify-center bg-white/60 dark:bg-slate-900/60">
            <Loader2 className="h-7 w-7 animate-spin text-blue-500" />
          </div>
        )}
        {children}
      </div>

      {(showPagination || total != null) && (
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 px-5 py-3 text-xs text-slate-500 dark:border-slate-800">
          <span>
            {total != null && (
              <>
                <span className="font-semibold text-slate-700 dark:text-slate-200">{total.toLocaleString('en-IN')}</span>{' '}
                records
                {pageSize ? ` · ${pageSize} per page` : ''}
              </>
            )}
          </span>
          {showPagination && (
            <div className="flex items-center gap-2">
              <button
                type="button"
                disabled={page <= 1}
                onClick={() => onPageChange!(page - 1)}
                className="inline-flex items-center gap-1 rounded-lg border border-slate-200 px-2.5 py-1.5 font-semibold text-slate-600 transition hover:border-blue-300 disabled:opacity-40 dark:border-slate-700 dark:text-slate-300"
              >
                <ChevronLeft className="h-3.5 w-3.5" /> Prev
              </button>
              <span className="font-medium text-slate-700 dark:text-slate-200">
                Page {page} of {totalPages}
              </span>
              <button
                type="button"
                disabled={page >= totalPages}
                onClick={() => onPageChange!(page + 1)}
                className="inline-flex items-center gap-1 rounded-lg border border-slate-200 px-2.5 py-1.5 font-semibold text-slate-600 transition hover:border-blue-300 disabled:opacity-40 dark:border-slate-700 dark:text-slate-300"
              >
                Next <ChevronRight className="h-3.5 w-3.5" />
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export const tableHeadClass =
  'sticky top-0 z-[1] bg-slate-50/95 px-3 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wider text-slate-500 backdrop-blur dark:bg-slate-900/95 dark:text-slate-400';

export const tableCellClass = 'px-3 py-2.5 text-xs text-slate-700 dark:text-slate-300';

export const tableRowClass =
  'border-b border-slate-100 transition hover:bg-blue-50/40 dark:border-slate-800 dark:hover:bg-blue-950/20';

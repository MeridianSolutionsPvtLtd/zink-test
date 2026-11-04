import React from 'react';
import {
  AlertTriangle,
  ArrowDown,
  ArrowRight,
  ArrowUp,
  ArrowUpDown,
  MessageSquare,
  TrendingUp,
  X,
} from 'lucide-react';
import { monthLabel, PLANNING_TABLE_HEADERS, type InsightRow } from './forecastAnalytics';

type Props = {
  insightsData: InsightRow[];
  tableLoading: boolean;
  totalInsights: number;
  isFetchingMore: boolean;
  hasMore: boolean;
  sortBy: string;
  sortOrder: string;
  onSort: (column: string) => void;
  onScroll: (e: React.UIEvent<HTMLDivElement>) => void;
  onRowClick: (row: InsightRow) => void;
};

export function ForecastPlanningTable({
  insightsData,
  tableLoading,
  totalInsights,
  isFetchingMore,
  hasMore,
  sortBy,
  sortOrder,
  onSort,
  onScroll,
  onRowClick,
}: Props) {
  const SortIcon = ({ column }: { column: string }) => {
    if (sortBy !== column) return <ArrowUpDown className="ml-1.5 inline h-3 w-3 text-slate-400 opacity-50" />;
    return sortOrder === 'ASC' ? <ArrowUp className="ml-1.5 inline h-3 w-3 text-blue-500" /> : <ArrowDown className="ml-1.5 inline h-3 w-3 text-blue-500" />;
  };

  return (
    <div className="rounded-2xl border border-slate-200/80 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900/60">
      <div className="border-b border-slate-100 px-4 py-3 dark:border-slate-800">
        <h3 className="text-sm font-bold text-slate-900 dark:text-white">Planning Insights</h3>
        <p className="text-[11px] text-slate-500">Detailed SKU-level AI recommendations and risk assessments</p>
      </div>
      <div className="custom-scrollbar relative max-h-[600px] overflow-x-auto overflow-y-auto" onScroll={onScroll}>
        <table className="sticky-header w-full text-sm text-left text-slate-500 dark:text-slate-400">
          <thead className="sticky top-0 z-[1] bg-slate-50 text-xs uppercase text-slate-700 dark:bg-slate-800 dark:text-slate-300">
            <tr>
              {PLANNING_TABLE_HEADERS.map((header) => (
                <th
                  key={header.id}
                  className="group cursor-pointer whitespace-nowrap bg-slate-50 px-4 py-3 font-semibold transition-colors hover:bg-slate-100 dark:bg-slate-800 dark:hover:bg-slate-700"
                  onClick={() => onSort(header.id)}
                >
                  <div className="flex items-center">
                    {header.label}
                    <SortIcon column={header.id} />
                  </div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-200 dark:divide-slate-800">
            {tableLoading ? (
              Array.from({ length: 5 }).map((_, i) => (
                <tr key={i} className="animate-pulse">
                  {Array.from({ length: 16 }).map((_, j) => (
                    <td key={j} className="px-4 py-3">
                      <div className="h-4 w-full rounded-md bg-slate-100 dark:bg-slate-800" />
                    </td>
                  ))}
                </tr>
              ))
            ) : insightsData.length > 0 ? (
              insightsData.map((row, idx) => (
                <tr key={idx} className="transition-colors hover:bg-slate-50 dark:hover:bg-slate-800/30">
                  <td className="whitespace-nowrap px-4 py-3 font-medium text-slate-900 dark:text-white">{row.site || '-'}</td>
                  <td className="whitespace-nowrap px-4 py-3 font-medium text-blue-600 dark:text-blue-400">{row.sku || '-'}</td>
                  <td className="whitespace-nowrap px-4 py-3 font-medium">{monthLabel(row)}</td>
                  <td className="whitespace-nowrap px-4 py-3">{row.department || '-'}</td>
                  <td className="whitespace-nowrap px-4 py-3">{row.color || '-'}</td>
                  <td className="whitespace-nowrap px-4 py-3">{row.fabric || '-'}</td>
                  <td className="whitespace-nowrap px-4 py-3">{row.section || '-'}</td>
                  <td className="whitespace-nowrap px-4 py-3">{row.occasion || '-'}</td>
                  <td className="whitespace-nowrap px-4 py-3">{row.product_type || '-'}</td>
                  <td className="whitespace-nowrap px-4 py-3">{row.fit_type || '-'}</td>
                  <td className="whitespace-nowrap px-4 py-3">{row.type || '-'}</td>
                  <td className="whitespace-nowrap px-4 py-3">
                    <button
                      type="button"
                      onClick={() => onRowClick(row)}
                      className="group flex items-center gap-2 rounded-lg border border-transparent bg-slate-50 px-3 py-1.5 font-bold text-slate-900 transition-all hover:border-blue-500/30 hover:bg-blue-50 dark:bg-slate-800/50 dark:text-white dark:hover:bg-blue-500/10"
                    >
                      {row.predicted_qty !== undefined ? new Intl.NumberFormat('en-IN').format(row.predicted_qty) : '-'}
                      <ArrowRight className="h-4 w-4 text-slate-300 opacity-0 transition-all group-hover:translate-x-1 group-hover:text-blue-500 group-hover:opacity-100" />
                    </button>
                  </td>
                  <td className="whitespace-nowrap px-4 py-3">
                    <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${
                      row.action === 'STOCK UP' ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-400' :
                      row.action === 'REDUCE' ? 'bg-rose-100 text-rose-700 dark:bg-rose-500/10 dark:text-rose-400' :
                      'bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-400'
                    }`}>
                      {row.action || '-'}
                    </span>
                  </td>
                  <td className="whitespace-nowrap px-4 py-3">
                    {row.risk_flag === 'HIGH' ? (
                      <div className="flex items-center gap-1.5 font-medium text-rose-500">
                        <span className="h-2 w-2 animate-ping rounded-full bg-rose-500" />
                        {row.risk_flag}
                      </div>
                    ) : (
                      <span className={row.risk_flag ? 'text-slate-400' : 'font-bold text-slate-500'}>{row.risk_flag || '-'}</span>
                    )}
                  </td>
                  <td className="whitespace-nowrap px-4 py-3">{row.discount_band || '-'}</td>
                  <td className="max-w-xs truncate whitespace-nowrap px-4 py-3" title={row.focus_comment || '-'}>
                    {row.focus_comment || '-'}
                  </td>
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan={16} className="px-6 py-12 text-center text-slate-400">
                  No matching records for the current season and filters.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="flex items-center justify-between border-t border-slate-100 bg-slate-50/50 px-4 py-3 dark:border-slate-800 dark:bg-slate-800/30">
        <div className="flex items-center gap-3">
          <p className="text-xs font-medium text-slate-500 dark:text-slate-400">
            Showing <span className="font-bold text-blue-600 dark:text-blue-400">{insightsData.length.toLocaleString()}</span> of{' '}
            <span className="font-bold">{totalInsights.toLocaleString()}</span> records
          </p>
          {isFetchingMore && (
            <div className="flex animate-pulse items-center gap-2 text-xs font-bold text-blue-500">
              <div className="h-1.5 w-1.5 animate-bounce rounded-full bg-blue-500" />
              Loading more records...
            </div>
          )}
        </div>
        {!hasMore && insightsData.length > 0 && (
          <p className="text-[10px] font-bold uppercase tracking-widest text-slate-400">End of insights</p>
        )}
      </div>
    </div>
  );
}

export function ForecastDetailModal({ row, onClose }: { row: InsightRow; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-50 flex animate-in fade-in items-center justify-center bg-slate-950/40 p-4 backdrop-blur-sm duration-200">
      <div className="flex max-h-[85vh] w-full max-w-lg animate-in zoom-in-95 flex-col overflow-hidden rounded-3xl border border-slate-200 bg-white shadow-2xl duration-200 dark:border-slate-800 dark:bg-slate-900" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-slate-100 bg-slate-50/50 p-6 dark:border-slate-700 dark:bg-slate-800/50">
          <div>
            <h3 className="font-bold text-slate-900 dark:text-white">SKU Recommendation Details</h3>
            <p className="text-sm text-slate-500 dark:text-slate-400">{row.sku} — {row.site}</p>
          </div>
          <button type="button" onClick={onClose} className="rounded-full p-2 text-slate-400 transition-colors hover:bg-slate-200 dark:hover:bg-slate-800">
            <X className="h-5 w-5" />
          </button>
        </div>
        <div className="custom-scrollbar flex-1 space-y-6 overflow-y-auto p-8">
          <div className="flex items-start gap-4">
            <div className="mt-1 rounded-lg bg-emerald-100 p-2 dark:bg-emerald-500/10">
              <TrendingUp className="h-5 w-5 text-emerald-600 dark:text-emerald-400" />
            </div>
            <div className="flex-1">
              <div className="mb-2 text-xs font-bold uppercase tracking-wider text-slate-400">Recommended Action</div>
              <div className={`inline-flex rounded-full px-3 py-1 text-sm font-bold shadow-sm ${
                row.action === 'STOCK UP' ? 'bg-emerald-500 text-white' :
                row.action === 'REDUCE' ? 'bg-rose-500 text-white' : 'bg-slate-600 text-white'
              }`}>
                {row.action || 'MAINTAIN'}
              </div>
            </div>
          </div>
          <div className="flex items-start gap-4">
            <div className={`mt-1 rounded-lg p-2 ${row.risk_flag === 'HIGH' ? 'bg-rose-100 dark:bg-rose-500/10' : 'bg-slate-100 dark:bg-slate-800'}`}>
              <AlertTriangle className={`h-5 w-5 ${row.risk_flag === 'HIGH' ? 'text-rose-500' : 'text-slate-500'}`} />
            </div>
            <div className="flex-1">
              <div className="mb-2 text-xs font-bold uppercase tracking-wider text-slate-400">Internal Risk Analysis</div>
              <div className={`text-lg font-bold ${row.risk_flag === 'HIGH' ? 'text-rose-500' : 'text-slate-700 dark:text-slate-300'}`}>
                {row.risk_flag || 'No Significant Risk'}
              </div>
            </div>
          </div>
          <div className="flex items-start gap-4">
            <div className="mt-1 rounded-lg bg-blue-100 p-2 dark:bg-blue-500/10">
              <MessageSquare className="h-5 w-5 text-blue-600 dark:text-blue-400" />
            </div>
            <div className="flex-1">
              <div className="mb-2 text-xs font-bold uppercase tracking-wider text-slate-400">AI Focus Comment</div>
              <div className="rounded-xl border border-slate-100 bg-slate-50 p-4 text-sm italic leading-relaxed text-slate-600 shadow-inner dark:border-slate-800 dark:bg-slate-950 dark:text-slate-400">
                &ldquo;{row.focus_comment || 'No specific focus comments generated for this SKU yet.'}&rdquo;
              </div>
            </div>
          </div>
        </div>
        <div className="flex justify-end border-t border-slate-100 bg-slate-50/50 p-6 dark:border-slate-700 dark:bg-slate-800/50">
          <button type="button" onClick={onClose} className="rounded-xl bg-slate-900 px-6 py-2.5 font-bold text-white transition-opacity hover:opacity-90 dark:bg-white dark:text-slate-900">
            Dismiss Details
          </button>
        </div>
      </div>
    </div>
  );
}

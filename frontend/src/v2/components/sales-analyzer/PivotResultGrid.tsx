import React, { useState, useMemo, useRef } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import {
  ChevronRight,
  ChevronDown,
  ArrowUpDown,
  ArrowUp,
  ArrowDown,
  TrendingUp,
  TrendingDown,
  Loader2,
  Table as TableIcon,
  Download,
  FileSpreadsheet,
  RefreshCw,
} from 'lucide-react';
import type {
  TreeNode,
  GrandTotalData,
  ValueConfig,
  MeasureMeta,
  DimensionMeta,
  SortConfig,
} from './types';

type FlatPivotRow = {
  node: TreeNode;
  key: string;
};

interface PivotResultGridProps {
  rows: TreeNode[];
  splitColKeys: string[];
  splitColDim?: string;
  grandTotal: GrandTotalData;
  values: ValueConfig[];
  measures: MeasureMeta[];
  dimensions: DimensionMeta[];
  loading: boolean;
  error?: string | null;
  sort?: SortConfig[];
  onSortChange: (field: string) => void;
  onRefresh?: () => void;
  onExportExcel?: () => void;
  onExportCsv?: () => void;
}

export function PivotResultGrid({
  rows,
  splitColKeys,
  splitColDim,
  grandTotal,
  values,
  measures,
  dimensions,
  loading,
  error,
  sort,
  onSortChange,
  onRefresh,
  onExportExcel,
  onExportCsv,
}: PivotResultGridProps) {
  const [collapsedKeys, setCollapsedKeys] = useState<Record<string, boolean>>({});
  const scrollParentRef = useRef<HTMLDivElement | null>(null);

  const toggleCollapse = (key: string) => {
    setCollapsedKeys((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  const expandAll = () => {
    setCollapsedKeys({});
  };

  const collapseAll = () => {
    const allKeys: Record<string, boolean> = {};
    const collectKeys = (nodes: TreeNode[]) => {
      nodes.forEach((n) => {
        if (n.children && n.children.length > 0) {
          allKeys[n.key] = true;
          collectKeys(n.children);
        }
      });
    };
    collectKeys(rows);
    setCollapsedKeys(allKeys);
  };

  /** Flatten visible hierarchy only — collapsed branches are skipped for virtualization. */
  const flatRows: FlatPivotRow[] = useMemo(() => {
    const out: FlatPivotRow[] = [];
    const walk = (nodes: TreeNode[]) => {
      for (const node of nodes) {
        out.push({ node, key: node.key });
        const hasChildren = !!(node.children && node.children.length > 0);
        const isCollapsed = collapsedKeys[node.key] ?? false;
        if (hasChildren && !isCollapsed) {
          walk(node.children);
        }
      }
    };
    walk(rows);
    return out;
  }, [rows, collapsedKeys]);

  const rowVirtualizer = useVirtualizer({
    count: flatRows.length,
    getScrollElement: () => scrollParentRef.current,
    estimateSize: () => 40,
    overscan: 12,
  });

  const getMeasureMeta = (field: string) =>
    measures.find((m) => m.field === field);

  const getDimensionLabel = (field: string) =>
    dimensions.find((d) => d.field === field)?.label || field;

  // Formatter for values
  const formatCellValue = (val: number | null | undefined, meta?: MeasureMeta) => {
    if (val == null || (typeof val === 'number' && isNaN(val))) return '—';
    if (meta?.field === 'sales_per_sqft' || meta?.field === 'sales_per_sqft_ly') {
      if (val === 0) return '—';
      return `₹${Math.round(val).toLocaleString('en-IN')}/sq ft`;
    }
    if (meta?.field === 'store_sqft') {
      if (val === 0) return '—';
      return `${Math.round(val).toLocaleString('en-IN')} sq ft`;
    }
    if (meta?.dataType === 'currency') {
      return new Intl.NumberFormat('en-IN', {
        style: 'currency',
        currency: 'INR',
        maximumFractionDigits: 0,
      }).format(val);
    }
    if (meta?.field?.includes('growth')) {
      const sign = val > 0 ? '+' : '';
      return `${sign}${val.toFixed(1)}%`;
    }
    if (meta?.field === 'mdq_fill_pct') {
      return `${val.toFixed(1)}%`;
    }
    if (meta?.field === 'mdq_deficit') {
      return val === 0 ? '0' : `${Math.round(val).toLocaleString('en-IN')} pcs`;
    }
    if (meta?.dataType === 'percentage' || meta?.field === 'sell_through_pct' || meta?.field === 'sell_through_ly_pct' || meta?.field === 'discount_pct') {
      return `${val.toFixed(1)}%`;
    }
    return val.toLocaleString('en-IN');
  };

  // Render Growth Badge
  const renderGrowthBadge = (val: number | null | undefined) => {
    if (val == null || (typeof val === 'number' && isNaN(val))) return <span>—</span>;
    const isPositive = val > 0;
    const isZero = Math.abs(val) < 0.05;

    if (isZero) {
      return <span className="font-semibold text-slate-500">0.0%</span>;
    }

    return (
      <span
        className={`inline-flex items-center gap-0.5 rounded px-1.5 py-0.5 text-xs font-bold ${
          isPositive
            ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300'
            : 'bg-rose-50 text-rose-700 dark:bg-rose-950/40 dark:text-rose-300'
        }`}
      >
        {isPositive ? (
          <TrendingUp className="h-3 w-3" />
        ) : (
          <TrendingDown className="h-3 w-3" />
        )}
        {isPositive ? `+${val.toFixed(1)}%` : `${val.toFixed(1)}%`}
      </span>
    );
  };

  // Render Sell-Through (STR) Badge
  const renderSellThroughBadge = (val: number | null | undefined) => {
    if (val == null || (typeof val === 'number' && isNaN(val))) return <span>—</span>;
    let badgeColor = 'bg-blue-50 text-blue-700 dark:bg-blue-950/40 dark:text-blue-300';
    if (val >= 60) {
      badgeColor = 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300';
    } else if (val < 40) {
      badgeColor = 'bg-amber-50 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300';
    }
    return (
      <span className={`inline-flex items-center rounded px-1.5 py-0.5 text-xs font-bold font-mono ${badgeColor}`}>
        {val.toFixed(1)}%
      </span>
    );
  };

  const renderCellContent = (rawVal: number | null | undefined, fieldName: string, mMeta?: MeasureMeta) => {
    if (fieldName.includes('growth')) {
      return renderGrowthBadge(rawVal);
    }
    if (fieldName === 'sell_through_pct' || fieldName === 'sell_through_ly_pct') {
      return renderSellThroughBadge(rawVal);
    }
    return formatCellValue(rawVal, mMeta);
  };

  const renderFlatRow = (node: TreeNode) => {
    const isCollapsed = collapsedKeys[node.key] ?? false;
    const hasChildren = !!(node.children && node.children.length > 0);
    const indentPx = node.level * 20;

    return (
      <>
          {/* Hierarchy Item Cell */}
          <td className="sticky left-0 z-10 bg-inherit px-4 py-2.5 whitespace-nowrap border-r-2 border-slate-300/90 shadow-[2px_0_4px_-2px_rgba(0,0,0,0.06)] dark:border-slate-700">
            <div
              className="flex items-center gap-1.5"
              style={{ paddingLeft: `${indentPx}px` }}
            >
              {hasChildren ? (
                <button
                  type="button"
                  onClick={() => toggleCollapse(node.key)}
                  className="rounded p-0.5 text-slate-400 hover:bg-slate-200 hover:text-slate-700 dark:hover:bg-slate-800"
                >
                  {isCollapsed ? (
                    <ChevronRight className="h-4 w-4 text-blue-600 dark:text-blue-400" />
                  ) : (
                    <ChevronDown className="h-4 w-4 text-blue-600 dark:text-blue-400" />
                  )}
                </button>
              ) : (
                <span className="h-4 w-4 inline-block opacity-0" />
              )}
              <span className="font-semibold text-xs tracking-tight">
                {node.value}
              </span>
              {hasChildren && (
                <span className="text-[10px] text-slate-400">
                  ({node.children.length})
                </span>
              )}
            </div>
          </td>

          {splitColKeys.map((colKey, colIdx) => {
            const splitMetricObj = node.split_metrics?.[colKey] || {};
            const isOddGroup = colIdx % 2 === 1;
            return values.map((val, valIdx) => {
              const mMeta = getMeasureMeta(val.field);
              const rawVal = splitMetricObj[val.field];
              const isLastInGroup = valIdx === values.length - 1;

              return (
                <td
                  key={`${colKey}_${val.field}`}
                  className={`px-3 py-2 text-right text-xs whitespace-nowrap font-mono ${
                    isLastInGroup
                      ? 'border-r-2 border-slate-300 dark:border-slate-700'
                      : 'border-r border-slate-200/60 dark:border-slate-800/60'
                  } ${
                    isOddGroup
                      ? 'bg-slate-50/60 dark:bg-slate-800/20'
                      : ''
                  }`}
                >
                  {renderCellContent(rawVal, val.field, mMeta)}
                </td>
              );
            });
          })}

          {values.map((val, valIdx) => {
            const mMeta = getMeasureMeta(val.field);
            const rawVal = node.metrics[val.field];
            const isFirstInTotal = valIdx === 0 && splitColKeys.length > 0;
            const isLastInTotal = valIdx === values.length - 1;

            return (
              <td
                key={val.field}
                className={`px-3.5 py-2 text-right text-xs whitespace-nowrap font-semibold font-mono bg-slate-50/80 dark:bg-slate-800/40 ${
                  isFirstInTotal
                    ? 'border-l-2 border-emerald-500/40'
                    : ''
                } ${
                  isLastInTotal
                    ? 'border-r border-slate-300 dark:border-slate-700'
                    : 'border-r border-slate-200/60 dark:border-slate-800/60'
                }`}
              >
                {renderCellContent(rawVal, val.field, mMeta)}
              </td>
            );
          })}
      </>
    );
  };

  const activeSortField = sort && sort.length > 0 ? sort[0].field : null;
  const activeSortDir = sort && sort.length > 0 ? sort[0].direction : 'desc';

  return (
    <div className="flex flex-col h-full min-h-[620px] rounded-2xl border border-slate-200/90 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900/80">
      {/* Grid Toolbar */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 px-5 py-3 dark:border-slate-800">
        <div className="flex items-center gap-2">
          <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-emerald-600/10 text-emerald-600 dark:bg-emerald-500/20 dark:text-emerald-400">
            <TableIcon className="h-4 w-4" />
          </div>
          <div>
            <h3 className="text-xs font-bold uppercase tracking-wider text-slate-800 dark:text-slate-100">
              Pivot Matrix Result
            </h3>
            <p className="text-[10px] text-slate-400">
              Showing {rows.length} top-level · {flatRows.length.toLocaleString('en-IN')} visible rows
              {splitColKeys.length > 0 ? ` · ${splitColKeys.length} split columns` : ''} · virtualized
            </p>
          </div>
        </div>

        {/* Expand / Collapse, Reload Indicator & Export Controls */}
        <div className="flex items-center gap-2">
          {/* Active Loading / Refresh Indicator */}
          {loading ? (
            <div className="inline-flex items-center gap-1.5 rounded-lg border border-blue-200 bg-blue-50 px-2.5 py-1 text-[11px] font-bold text-blue-700 shadow-xs dark:border-blue-800/60 dark:bg-blue-950/60 dark:text-blue-300">
              <Loader2 className="h-3.5 w-3.5 animate-spin text-blue-600 dark:text-blue-400" />
              <span>Calculating Data…</span>
            </div>
          ) : onRefresh ? (
            <button
              type="button"
              onClick={onRefresh}
              className="inline-flex items-center gap-1 rounded-lg border border-slate-200 bg-white px-2.5 py-1 text-[11px] font-semibold text-slate-700 shadow-2xs hover:border-blue-300 hover:text-blue-600 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 transition"
              title="Refresh / Recalculate Pivot Table"
            >
              <RefreshCw className="h-3 w-3 text-slate-500" />
              <span>Refresh</span>
            </button>
          ) : null}

          <button
            type="button"
            onClick={expandAll}
            className="rounded-lg border border-slate-200 bg-white px-2.5 py-1 text-[11px] font-semibold text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
          >
            Expand All
          </button>
          <button
            type="button"
            onClick={collapseAll}
            className="rounded-lg border border-slate-200 bg-white px-2.5 py-1 text-[11px] font-semibold text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
          >
            Collapse All
          </button>

          {onExportExcel && (
            <button
              type="button"
              disabled={loading || rows.length === 0}
              onClick={onExportExcel}
              className="inline-flex items-center gap-1 rounded-lg bg-emerald-600 px-3 py-1 text-[11px] font-bold text-white shadow-xs hover:bg-emerald-700 disabled:opacity-50 transition"
              title="Download exact Pivot table in Excel (.xlsx)"
            >
              <Download className="h-3 w-3" />
              <span>Download Excel</span>
            </button>
          )}
        </div>
      </div>

      {/* Top Animated Progress Bar on Loading */}
      {loading && (
        <div className="relative h-1 w-full overflow-hidden bg-blue-100 dark:bg-blue-950">
          <div className="absolute inset-y-0 left-0 h-full w-2/5 bg-blue-600 dark:bg-blue-400 animate-[shimmer_1.2s_infinite]" />
        </div>
      )}

      {/* Grid View Table (virtualized body) */}
      <div
        ref={scrollParentRef}
        className="relative max-h-[620px] overflow-auto rounded-b-2xl custom-scrollbar"
      >
        {loading && rows.length > 0 && (
          <div className="absolute inset-0 z-30 flex flex-col items-center justify-center bg-white/50 backdrop-blur-[1px] dark:bg-slate-950/50">
            <div className="flex items-center gap-2 rounded-xl border border-slate-200/90 bg-white/95 px-4 py-2.5 shadow-xl dark:border-slate-800 dark:bg-slate-900/95">
              <Loader2 className="h-4 w-4 animate-spin text-blue-600 dark:text-blue-400" />
              <span className="text-xs font-bold text-slate-800 dark:text-slate-100">
                Recalculating Pivot Matrix…
              </span>
            </div>
          </div>
        )}
        <table className={`w-full text-left text-xs border-collapse transition-opacity duration-200 ${loading && rows.length > 0 ? 'opacity-60' : 'opacity-100'}`}>
          {/* Header 1: Column Group Header */}
          <thead className="sticky top-0 z-20 bg-slate-900 text-white shadow-sm dark:bg-slate-950">
            {splitColKeys.length > 0 && (
              <tr className="border-b border-slate-700/80 text-[11px] font-bold uppercase tracking-wider">
                <th
                  rowSpan={2}
                  className="sticky left-0 z-30 bg-slate-900 px-4 py-2.5 border-r-2 border-slate-700 shadow-[2px_0_4px_-2px_rgba(0,0,0,0.3)]"
                >
                  Hierarchy
                </th>
                {splitColKeys.map((colKey, colIdx) => {
                  const isOdd = colIdx % 2 === 1;
                  return (
                    <th
                      key={colKey}
                      colSpan={values.length}
                      className={`px-3 py-2.5 text-center border-r-2 border-slate-700 ${
                        isOdd
                          ? 'bg-slate-850 text-indigo-300'
                          : 'bg-slate-800 text-blue-300'
                      }`}
                    >
                      <span className="inline-flex items-center gap-1.5 rounded-md bg-slate-900/60 px-2 py-0.5 border border-slate-700/60 tracking-wide font-extrabold text-[11px]">
                        {colKey}
                      </span>
                    </th>
                  );
                })}
                <th
                  colSpan={values.length}
                  className="px-3 py-2.5 text-center border-l-2 border-emerald-500/60 bg-emerald-950/80 text-emerald-300 font-extrabold"
                >
                  <span className="inline-flex items-center gap-1 rounded-md bg-emerald-900/60 px-2.5 py-0.5 border border-emerald-700/60 tracking-wider text-[11px]">
                    Grand Total
                  </span>
                </th>
              </tr>
            )}

            {/* Header 2: Measure Column Headers */}
            <tr className="border-b border-slate-700 text-[11px] font-bold uppercase tracking-wider">
              {splitColKeys.length === 0 && (
                <th className="sticky left-0 z-30 bg-slate-900 px-4 py-2.5 border-r-2 border-slate-700 shadow-[2px_0_4px_-2px_rgba(0,0,0,0.3)]">
                  Hierarchy
                </th>
              )}

              {splitColKeys.map((colKey) =>
                values.map((val, valIdx) => {
                  const mMeta = getMeasureMeta(val.field);
                  const isLastInGroup = valIdx === values.length - 1;
                  return (
                    <th
                      key={`${colKey}_${val.field}`}
                      className={`px-3 py-2 text-right whitespace-nowrap text-slate-300 ${
                        isLastInGroup
                          ? 'border-r-2 border-slate-700'
                          : 'border-r border-slate-800/80'
                      }`}
                    >
                      {val.custom_label || mMeta?.label || val.field}
                    </th>
                  );
                })
              )}

              {values.map((val, valIdx) => {
                const mMeta = getMeasureMeta(val.field);
                const isSorted = activeSortField === val.field;
                const isFirstInTotal = valIdx === 0 && splitColKeys.length > 0;
                const isLastInTotal = valIdx === values.length - 1;

                return (
                  <th
                    key={`tot_${val.field}`}
                    className={`px-3.5 py-2 text-right whitespace-nowrap text-slate-200 ${
                      isFirstInTotal ? 'border-l-2 border-emerald-500/50' : ''
                    } ${
                      isLastInTotal
                        ? 'border-r border-slate-700'
                        : 'border-r border-slate-800/80'
                    }`}
                  >
                    <button
                      type="button"
                      onClick={() => onSortChange(val.field)}
                      className="inline-flex items-center gap-1 hover:text-blue-400 transition"
                    >
                      <span>{val.custom_label || mMeta?.label || val.field}</span>
                      {isSorted ? (
                        activeSortDir === 'asc' ? (
                          <ArrowUp className="h-3 w-3 text-blue-400" />
                        ) : (
                          <ArrowDown className="h-3 w-3 text-blue-400" />
                        )
                      ) : (
                        <ArrowUpDown className="h-3 w-3 opacity-40 hover:opacity-100" />
                      )}
                    </button>
                  </th>
                );
              })}
            </tr>
          </thead>

          {/* Table Body */}
          <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
            {error ? (
              <tr>
                <td colSpan={1 + Math.max(1, splitColKeys.length) * values.length} className="py-12 text-center text-sm text-rose-600">
                  <p role="alert">{error}</p>
                  <button type="button" onClick={onRefresh} className="mt-3 rounded border border-rose-300 px-3 py-1.5 font-semibold">Retry pivot</button>
                </td>
              </tr>
            ) : loading && rows.length === 0 ? (
              <tr>
                <td
                  colSpan={
                    1 + (splitColKeys.length > 0 ? splitColKeys.length : 1) * values.length
                  }
                  className="py-20 text-center"
                >
                  <Loader2 className="mx-auto h-8 w-8 animate-spin text-blue-600" />
                  <span className="mt-2 block text-xs text-slate-500 font-semibold">
                    Calculating pivot analysis matrix…
                  </span>
                </td>
              </tr>
            ) : rows.length === 0 ? (
              <tr>
                <td
                  colSpan={
                    1 + (splitColKeys.length > 0 ? splitColKeys.length : 1) * values.length
                  }
                  className="py-16 text-center text-xs text-slate-500"
                >
                  No data matching the selected dimensions and filters.
                </td>
              </tr>
            ) : (
              <>
                {rowVirtualizer.getVirtualItems().length > 0 && (
                  <tr aria-hidden style={{ height: rowVirtualizer.getVirtualItems()[0]?.start ?? 0 }}>
                    <td
                      colSpan={
                        1 + (splitColKeys.length > 0 ? splitColKeys.length : 1) * values.length
                      }
                    />
                  </tr>
                )}
                {rowVirtualizer.getVirtualItems().map((vRow) => {
                  const item = flatRows[vRow.index];
                  if (!item) return null;
                  const node = item.node;
                  return (
                    <tr
                      key={item.key}
                      data-index={vRow.index}
                      ref={rowVirtualizer.measureElement}
                      className={`border-b border-slate-200/80 transition hover:bg-blue-50/50 dark:border-slate-800/80 dark:hover:bg-blue-950/30 ${
                        node.level === 0
                          ? 'bg-white font-semibold text-slate-900 dark:bg-slate-900 dark:text-slate-100'
                          : 'bg-slate-50/50 text-slate-800 dark:bg-slate-900/40 dark:text-slate-200'
                      }`}
                    >
                      {renderFlatRow(node)}
                    </tr>
                  );
                })}
                {rowVirtualizer.getVirtualItems().length > 0 && (
                  <tr
                    aria-hidden
                    style={{
                      height: Math.max(
                        0,
                        rowVirtualizer.getTotalSize() -
                          (rowVirtualizer.getVirtualItems().at(-1)?.end ?? 0),
                      ),
                    }}
                  >
                    <td
                      colSpan={
                        1 + (splitColKeys.length > 0 ? splitColKeys.length : 1) * values.length
                      }
                    />
                  </tr>
                )}
              </>
            )}
          </tbody>

          {/* Grand Total Sticky Footer */}
          {rows.length > 0 && (
            <tfoot className="sticky bottom-0 z-20 bg-slate-100 text-slate-900 shadow-md dark:bg-slate-800 dark:text-slate-100 font-extrabold border-t-2 border-slate-300 dark:border-slate-700">
              <tr>
                <td className="sticky left-0 z-30 bg-inherit px-4 py-3 whitespace-nowrap border-r-2 border-slate-400/90 shadow-[2px_0_4px_-2px_rgba(0,0,0,0.1)] dark:border-slate-600 text-xs">
                  Grand Total ({grandTotal.record_count?.toLocaleString('en-IN') || rows.length} records)
                </td>

                {splitColKeys.map((colKey, colIdx) => {
                  const splitMetricObj = grandTotal.split_metrics?.[colKey] || {};
                  const isOdd = colIdx % 2 === 1;
                  return values.map((val, valIdx) => {
                    const mMeta = getMeasureMeta(val.field);
                    const rawVal = splitMetricObj[val.field];
                    const isLastInGroup = valIdx === values.length - 1;

                    return (
                      <td
                        key={`gt_${colKey}_${val.field}`}
                        className={`px-3 py-3 text-right text-xs whitespace-nowrap font-mono ${
                          isLastInGroup
                            ? 'border-r-2 border-slate-400 dark:border-slate-600'
                            : 'border-r border-slate-200 dark:border-slate-700'
                        } ${isOdd ? 'bg-slate-200/40 dark:bg-slate-850/40' : ''}`}
                      >
                        {renderCellContent(rawVal, val.field, mMeta)}
                      </td>
                    );
                  });
                })}

                {values.map((val, valIdx) => {
                  const mMeta = getMeasureMeta(val.field);
                  const rawVal = grandTotal.metrics?.[val.field];
                  const isFirstInTotal = valIdx === 0 && splitColKeys.length > 0;
                  const isLastInTotal = valIdx === values.length - 1;

                  return (
                    <td
                      key={`gt_tot_${val.field}`}
                      className={`px-3.5 py-3 text-right text-xs whitespace-nowrap font-mono bg-emerald-50/50 dark:bg-emerald-950/20 ${
                        isFirstInTotal ? 'border-l-2 border-emerald-500/50' : ''
                      } ${
                        isLastInTotal
                          ? 'border-r border-slate-300 dark:border-slate-700'
                          : 'border-r border-slate-200 dark:border-slate-700'
                      }`}
                    >
                      {renderCellContent(rawVal, val.field, mMeta)}
                    </td>
                  );
                })}
              </tr>
            </tfoot>
          )}
        </table>
      </div>
    </div>
  );
}

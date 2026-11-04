import React, { useState } from 'react';
import {
  X,
  ChevronDown,
  Layers,
  Columns as ColumnsIcon,
  Hash,
  Filter as FilterIcon,
  Plus,
  Trash2,
} from 'lucide-react';
import type {
  DimensionMeta,
  MeasureMeta,
  ValueConfig,
  AggregationType,
} from './types';

interface PivotDropZoneProps {
  dimensions: DimensionMeta[];
  measures: MeasureMeta[];
  rows: string[];
  columns: string[];
  values: ValueConfig[];
  filters: Record<string, any>;
  onUpdateRows: (rows: string[]) => void;
  onUpdateColumns: (columns: string[]) => void;
  onUpdateValues: (values: ValueConfig[]) => void;
  onOpenFilterModal: (field: string) => void;
  onRemoveFilter: (field: string) => void;
  onClearAll: () => void;
  /** When true, hide Filters drop-zone (page-level filters drive the query). */
  hideFilters?: boolean;
}

export function PivotDropZone({
  dimensions,
  measures,
  rows,
  columns,
  values,
  filters,
  onUpdateRows,
  onUpdateColumns,
  onUpdateValues,
  onOpenFilterModal,
  onRemoveFilter,
  onClearAll,
  hideFilters = false,
}: PivotDropZoneProps) {
  const [activeAggMenuField, setActiveAggMenuField] = useState<string | null>(null);

  const getDimensionLabel = (field: string) =>
    dimensions.find((d) => d.field === field)?.label || field;

  const getMeasureMeta = (field: string) =>
    measures.find((m) => m.field === field);

  const handleDrop = (e: React.DragEvent, targetArea: 'rows' | 'columns' | 'values' | 'filters') => {
    e.preventDefault();
    try {
      const dataStr = e.dataTransfer.getData('text/plain');
      if (!dataStr) return;
      const { field, type } = JSON.parse(dataStr);

      if (targetArea === 'rows') {
        if (type === 'dimension' && !rows.includes(field)) {
          onUpdateRows([...rows, field]);
          // Remove from columns if present
          if (columns.includes(field)) {
            onUpdateColumns(columns.filter((c) => c !== field));
          }
        }
      } else if (targetArea === 'columns') {
        if (type === 'dimension' && !columns.includes(field)) {
          // One column split: a new field replaces the previous selection.
          onUpdateColumns([field]);
          if (rows.includes(field)) {
            onUpdateRows(rows.filter((r) => r !== field));
          }
        }
      } else if (targetArea === 'values') {
        if (type === 'measure' && !values.some((v) => v.field === field)) {
          const mMeta = getMeasureMeta(field);
          onUpdateValues([
            ...values,
            {
              field,
              aggregation: mMeta?.defaultAggregation || 'sum',
            },
          ]);
        }
      } else if (targetArea === 'filters') {
        if (type === 'dimension') {
          onOpenFilterModal(field);
        }
      }
    } catch (err) {
      console.error('Error handling drop:', err);
    }
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
  };

  const removeRow = (field: string) => {
    onUpdateRows(rows.filter((r) => r !== field));
  };

  const removeColumn = (field: string) => {
    onUpdateColumns(columns.filter((c) => c !== field));
  };

  const removeValue = (field: string) => {
    onUpdateValues(values.filter((v) => v.field !== field));
  };

  const changeAggregation = (field: string, agg: AggregationType) => {
    onUpdateValues(
      values.map((v) => (v.field === field ? { ...v, aggregation: agg } : v))
    );
    setActiveAggMenuField(null);
  };

  return (
    <div className="rounded-2xl border border-slate-200/90 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900/80">
      <div
        className={`grid grid-cols-1 md:grid-cols-2 gap-3 ${
          hideFilters ? 'lg:grid-cols-3' : 'lg:grid-cols-4'
        }`}
      >
        {/* 1. ROWS DROPZONE */}
        <div
          onDrop={(e) => handleDrop(e, 'rows')}
          onDragOver={handleDragOver}
          className="flex flex-col min-h-[90px] rounded-xl border border-blue-200/70 bg-blue-50/20 p-2.5 transition dark:border-blue-900/40 dark:bg-blue-950/15"
        >
          <div className="flex items-center justify-between mb-2 px-1">
            <span className="flex items-center gap-1.5 text-xs font-bold text-blue-900 dark:text-blue-300">
              <Layers className="h-3.5 w-3.5 text-blue-600 dark:text-blue-400" />
              ROWS (Hierarchy)
            </span>
            <span className="text-[10px] text-slate-400">{rows.length} fields</span>
          </div>

          <div className="flex flex-wrap items-center gap-1.5 flex-1">
            {rows.map((rowField, idx) => (
              <span
                key={rowField}
                className="inline-flex items-center gap-1 rounded-lg border border-blue-200 bg-white px-2 py-1 text-xs font-semibold text-slate-800 shadow-2xs dark:border-blue-800 dark:bg-slate-800 dark:text-slate-100"
              >
                <span className="text-[10px] font-extrabold text-blue-600 dark:text-blue-400">
                  {idx + 1}.
                </span>
                <span>{getDimensionLabel(rowField)}</span>
                <button
                  onClick={() => removeRow(rowField)}
                  className="rounded-full p-0.5 hover:bg-slate-100 hover:text-rose-600 dark:hover:bg-slate-700"
                >
                  <X className="h-3 w-3" />
                </button>
              </span>
            ))}
            {rows.length === 0 && (
              <div className="flex flex-1 items-center justify-center rounded-lg border border-dashed border-blue-300/60 p-2 text-center text-[11px] text-blue-500/80 dark:border-blue-800/60 dark:text-blue-400/80">
                Drag dimensions here for rows
              </div>
            )}
          </div>
        </div>

        {/* 2. COLUMNS DROPZONE */}
        <div
          onDrop={(e) => handleDrop(e, 'columns')}
          onDragOver={handleDragOver}
          className="flex flex-col min-h-[90px] rounded-xl border border-indigo-200/70 bg-indigo-50/20 p-2.5 transition dark:border-indigo-900/40 dark:bg-indigo-950/15"
        >
          <div className="flex items-center justify-between mb-2 px-1">
            <span className="flex items-center gap-1.5 text-xs font-bold text-indigo-900 dark:text-indigo-300">
              <ColumnsIcon className="h-3.5 w-3.5 text-indigo-600 dark:text-indigo-400" />
              COLUMNS (Matrix Split)
            </span>
            <span className="text-[10px] text-slate-400">{columns.length} fields</span>
          </div>

          <div className="flex flex-wrap items-center gap-1.5 flex-1">
            {columns.map((colField) => (
              <span
                key={colField}
                className="inline-flex items-center gap-1 rounded-lg border border-indigo-200 bg-white px-2 py-1 text-xs font-semibold text-slate-800 shadow-2xs dark:border-indigo-800 dark:bg-slate-800 dark:text-slate-100"
              >
                <span>{getDimensionLabel(colField)}</span>
                <button
                  onClick={() => removeColumn(colField)}
                  className="rounded-full p-0.5 hover:bg-slate-100 hover:text-rose-600 dark:hover:bg-slate-700"
                >
                  <X className="h-3 w-3" />
                </button>
              </span>
            ))}
            {columns.length === 0 && (
              <div className="flex flex-1 items-center justify-center rounded-lg border border-dashed border-indigo-300/60 p-2 text-center text-[11px] text-indigo-500/80 dark:border-indigo-800/60 dark:text-indigo-400/80">
                Drag fields here for column split
              </div>
            )}
          </div>
        </div>

        {/* 3. VALUES DROPZONE */}
        <div
          onDrop={(e) => handleDrop(e, 'values')}
          onDragOver={handleDragOver}
          className="flex flex-col min-h-[90px] rounded-xl border border-emerald-200/70 bg-emerald-50/20 p-2.5 transition dark:border-emerald-900/40 dark:bg-emerald-950/15"
        >
          <div className="flex items-center justify-between mb-2 px-1">
            <span className="flex items-center gap-1.5 text-xs font-bold text-emerald-900 dark:text-emerald-300">
              <Hash className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400" />
              VALUES (Aggregated)
            </span>
            <span className="text-[10px] text-slate-400">{values.length} measures</span>
          </div>

          <div className="flex flex-wrap items-center gap-1.5 flex-1">
            {values.map((val) => {
              const mMeta = getMeasureMeta(val.field);
              const allowedAggs = mMeta?.allowedAggregations || ['sum', 'avg'];
              const isAggOpen = activeAggMenuField === val.field;
              return (
                <div key={val.field} className="relative">
                  <span className="inline-flex items-center gap-1 rounded-lg border border-emerald-200 bg-white px-2 py-1 text-xs font-semibold text-slate-800 shadow-2xs dark:border-emerald-800 dark:bg-slate-800 dark:text-slate-100">
                    <span>{mMeta?.label || val.field}</span>
                    {val.aggregation && val.aggregation !== 'calculated' && (
                      <button
                        onClick={() =>
                          setActiveAggMenuField(isAggOpen ? null : val.field)
                        }
                        className="inline-flex items-center gap-0.5 rounded bg-emerald-100 px-1 text-[10px] font-bold text-emerald-800 hover:bg-emerald-200 dark:bg-emerald-900/60 dark:text-emerald-200"
                      >
                        {val.aggregation.toUpperCase()}
                        <ChevronDown className="h-2.5 w-2.5" />
                      </button>
                    )}
                    <button
                      onClick={() => removeValue(val.field)}
                      className="rounded-full p-0.5 hover:bg-slate-100 hover:text-rose-600 dark:hover:bg-slate-700"
                    >
                      <X className="h-3 w-3" />
                    </button>
                  </span>

                  {isAggOpen && (
                    <div className="absolute left-0 top-full z-50 mt-1 w-28 rounded-xl border border-slate-200 bg-white p-1 shadow-xl dark:border-slate-700 dark:bg-slate-900">
                      {allowedAggs.map((agg) => (
                        <button
                          key={agg}
                          onClick={() => changeAggregation(val.field, agg)}
                          className={`flex w-full items-center justify-between rounded px-2 py-1 text-left text-xs font-semibold ${
                            val.aggregation === agg
                              ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300'
                              : 'text-slate-700 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800'
                          }`}
                        >
                          {agg.toUpperCase()}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
            {values.length === 0 && (
              <div className="flex flex-1 items-center justify-center rounded-lg border border-dashed border-emerald-300/60 p-2 text-center text-[11px] text-emerald-500/80 dark:border-emerald-800/60 dark:text-emerald-400/80">
                Drag measures here for values
              </div>
            )}
          </div>
        </div>

        {/* 4. FILTERS DROPZONE — hidden when page-level filters drive the query */}
        {!hideFilters && (
          <div
            onDrop={(e) => handleDrop(e, 'filters')}
            onDragOver={handleDragOver}
            className="flex flex-col min-h-[90px] rounded-xl border border-amber-200/70 bg-amber-50/20 p-2.5 transition dark:border-amber-900/40 dark:bg-amber-950/15"
          >
            <div className="flex items-center justify-between mb-2 px-1">
              <span className="flex items-center gap-1.5 text-xs font-bold text-amber-900 dark:text-amber-300">
                <FilterIcon className="h-3.5 w-3.5 text-amber-600 dark:text-amber-400" />
                FILTERS
              </span>
              <span className="text-[10px] text-slate-400">
                {Object.keys(filters).length} active
              </span>
            </div>

            <div className="flex flex-wrap items-center gap-1.5 flex-1">
              {Object.entries(filters).map(([field, fval]) => {
                if (fval == null || fval === '' || (Array.isArray(fval) && fval.length === 0))
                  return null;
                const label = getDimensionLabel(field);
                const valCount = Array.isArray(fval) ? fval.length : 1;
                return (
                  <span
                    key={field}
                    onClick={() => onOpenFilterModal(field)}
                    className="inline-flex cursor-pointer items-center gap-1 rounded-lg border border-amber-200 bg-white px-2 py-1 text-xs font-semibold text-slate-800 shadow-2xs hover:border-amber-400 dark:border-amber-800 dark:bg-slate-800 dark:text-slate-100"
                  >
                    <span className="text-amber-600 font-bold">{label}:</span>
                    <span className="text-slate-600 dark:text-slate-300">
                      {Array.isArray(fval)
                        ? valCount > 1
                          ? `(${valCount}) items`
                          : String(fval[0])
                        : String(fval)}
                    </span>
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        onRemoveFilter(field);
                      }}
                      className="rounded-full p-0.5 hover:bg-slate-100 hover:text-rose-600 dark:hover:bg-slate-700"
                    >
                      <X className="h-3 w-3" />
                    </button>
                  </span>
                );
              })}
              {Object.keys(filters).length === 0 && (
                <div className="flex flex-1 items-center justify-center rounded-lg border border-dashed border-amber-300/60 p-2 text-center text-[11px] text-amber-500/80 dark:border-amber-800/60 dark:text-amber-400/80">
                  Drag fields here to filter
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

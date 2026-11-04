import React, { useState, useMemo } from 'react';
import { X, Search, Check, Filter } from 'lucide-react';
import type { DimensionMeta } from './types';

interface PivotFilterModalProps {
  field: string;
  dimensions: DimensionMeta[];
  options: string[];
  selectedValues: string[];
  onApply: (values: string[]) => void;
  onClose: () => void;
}

function indianFyEnd(d = new Date()): number {
  return d.getMonth() >= 3 ? d.getFullYear() + 1 : d.getFullYear();
}

function fyBounds(fyEnd: number) {
  const startY = fyEnd - 1;
  const short = String(fyEnd).slice(-2);
  return {
    fyEnd,
    short,
    label: `FY${short}`,
    fyStart: `${startY}-04-01`,
    fyEndDate: `${fyEnd}-03-31`,
    q1End: `${startY}-06-30`,
    q2Start: `${startY}-07-01`,
    q2End: `${startY}-09-30`,
    mayStart: `${startY}-05-01`,
    junEnd: `${startY}-06-30`,
    augStart: `${startY}-08-01`,
    augEnd: `${startY}-08-31`,
    lyStartY: startY - 1,
  };
}

export function PivotFilterModal({
  field,
  dimensions,
  options,
  selectedValues,
  onApply,
  onClose,
}: PivotFilterModalProps) {
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState<Set<string>>(
    new Set(selectedValues || [])
  );

  const fy = useMemo(() => fyBounds(indianFyEnd()), []);
  const isDateField = field === 'date';
  const [startDate, setStartDate] = useState<string>(
    isDateField && Array.isArray(selectedValues) && selectedValues.length >= 1
      ? selectedValues[0]
      : fy.fyStart
  );
  const [endDate, setEndDate] = useState<string>(
    isDateField && Array.isArray(selectedValues) && selectedValues.length >= 2
      ? selectedValues[1]
      : fy.q2End
  );

  const dimLabel = dimensions.find((d) => d.field === field)?.label || field;

  const filteredOptions = useMemo(() => {
    if (!options) return [];
    if (!search.trim()) return options;
    const q = search.toLowerCase();
    return options.filter((o) => String(o).toLowerCase().includes(q));
  }, [options, search]);

  const toggleOption = (opt: string) => {
    // Re-picking in the same filter replaces the previous value.
    if (selected.size === 1 && selected.has(opt)) {
      setSelected(new Set());
      return;
    }
    setSelected(new Set([opt]));
  };

  const clearAll = () => {
    setSelected(new Set());
  };

  const handleSave = () => {
    if (isDateField) {
      if (startDate && endDate) {
        onApply([startDate, endDate]);
      } else if (startDate) {
        onApply([startDate, startDate]);
      } else {
        onApply([]);
      }
    } else {
      onApply(Array.from(selected));
    }
    onClose();
  };

  const lyStart = startDate
    ? startDate.replace(String(fy.fyEnd - 1), String(fy.lyStartY))
    : '—';
  const lyEnd = endDate
    ? endDate.replace(String(fy.fyEnd - 1), String(fy.lyStartY))
    : '—';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/60 p-4 backdrop-blur-xs">
      <div className="flex w-full max-w-md flex-col rounded-2xl border border-slate-200 bg-white shadow-2xl dark:border-slate-800 dark:bg-slate-900">
        <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4 dark:border-slate-800">
          <div className="flex items-center gap-2">
            <Filter className="h-4 w-4 text-amber-500" />
            <h3 className="text-sm font-bold text-slate-900 dark:text-white">
              Filter: {dimLabel}
            </h3>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600 dark:hover:bg-slate-800"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="max-h-[60vh] overflow-y-auto px-5 py-4">
          {isDateField ? (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs font-bold text-slate-700 dark:text-slate-300 mb-1">
                  Start Date (From)
                </label>
                <input
                  type="date"
                  value={startDate}
                  min="2025-04-01"
                  onChange={(e) => setStartDate(e.target.value)}
                  className="w-full rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs font-semibold text-slate-800 focus:border-amber-500 focus:bg-white focus:outline-none dark:border-slate-700 dark:bg-slate-800 dark:text-white"
                />
              </div>
              <div>
                <label className="block text-xs font-bold text-slate-700 dark:text-slate-300 mb-1">
                  End Date (To)
                </label>
                <input
                  type="date"
                  value={endDate}
                  min={startDate || '2025-04-01'}
                  onChange={(e) => setEndDate(e.target.value)}
                  className="w-full rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs font-semibold text-slate-800 focus:border-amber-500 focus:bg-white focus:outline-none dark:border-slate-700 dark:bg-slate-800 dark:text-white"
                />
              </div>
            </div>

            <div>
              <span className="block text-[11px] font-bold text-slate-400 uppercase tracking-wider mb-2">
                Quick Shortcuts ({fy.label})
              </span>
              <div className="flex flex-wrap gap-1.5">
                <button
                  type="button"
                  onClick={() => {
                    setStartDate(fy.fyStart);
                    setEndDate(fy.q2End);
                  }}
                  className="rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1 text-[11px] font-semibold text-slate-700 hover:border-amber-500 hover:text-amber-600 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300"
                >
                  Full {fy.label} (Apr–Sep)
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setStartDate(fy.fyStart);
                    setEndDate(fy.q1End);
                  }}
                  className="rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1 text-[11px] font-semibold text-slate-700 hover:border-amber-500 hover:text-amber-600 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300"
                >
                  Q1 (Apr–Jun)
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setStartDate(fy.q2Start);
                    setEndDate(fy.q2End);
                  }}
                  className="rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1 text-[11px] font-semibold text-slate-700 hover:border-amber-500 hover:text-amber-600 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300"
                >
                  Q2 (Jul–Sep)
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setStartDate(fy.mayStart);
                    setEndDate(fy.junEnd);
                  }}
                  className="rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1 text-[11px] font-semibold text-slate-700 hover:border-amber-500 hover:text-amber-600 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300"
                >
                  May–Jun Peak
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setStartDate(fy.augStart);
                    setEndDate(fy.augEnd);
                  }}
                  className="rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1 text-[11px] font-semibold text-slate-700 hover:border-amber-500 hover:text-amber-600 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300"
                >
                  Aug {fy.fyEnd - 1} (Full Month)
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setStartDate(fy.fyStart);
                    setEndDate(fy.fyEndDate);
                  }}
                  className="rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1 text-[11px] font-semibold text-slate-700 hover:border-amber-500 hover:text-amber-600 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300"
                >
                  Full {fy.label} (Apr–Mar)
                </button>
              </div>
            </div>

            <div className="rounded-xl border border-blue-100 bg-blue-50/50 p-3 text-[11px] text-blue-800 dark:border-blue-900/40 dark:bg-blue-950/20 dark:text-blue-300">
              Like-for-like YoY: Last Year (LY) aligns to the same dates in {fy.lyStartY} (
              {lyStart} to {lyEnd}).
            </div>
          </div>
        ) : (
          <>
            <div className="mb-3 flex items-center gap-2">
              <div className="relative flex-1">
                <Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
                <input
                  type="text"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder={`Search ${dimLabel}…`}
                  className="w-full rounded-xl border border-slate-200 bg-slate-50 py-2 pl-9 pr-3 text-xs font-medium text-slate-800 focus:border-amber-500 focus:bg-white focus:outline-none dark:border-slate-700 dark:bg-slate-800 dark:text-white"
                />
              </div>
            </div>
            <div className="mb-2 flex gap-2">
              <button
                type="button"
                onClick={clearAll}
                className="rounded-lg border border-slate-200 px-2.5 py-1 text-[11px] font-semibold text-slate-600 hover:border-amber-500 hover:text-amber-600 dark:border-slate-700 dark:text-slate-300"
              >
                Clear
              </button>
            </div>
            <div className="space-y-1">
              {filteredOptions.map((opt) => {
                const checked = selected.has(opt);
                return (
                  <button
                    key={opt}
                    type="button"
                    onClick={() => toggleOption(opt)}
                    className={`flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-xs font-medium transition-colors ${
                      checked
                        ? 'bg-amber-50 text-amber-800 dark:bg-amber-950/30 dark:text-amber-200'
                        : 'text-slate-700 hover:bg-slate-50 dark:text-slate-300 dark:hover:bg-slate-800'
                    }`}
                  >
                    <span
                      className={`flex h-4 w-4 shrink-0 items-center justify-center rounded border ${
                        checked
                          ? 'border-amber-500 bg-amber-500 text-white'
                          : 'border-slate-300 dark:border-slate-600'
                      }`}
                    >
                      {checked && <Check className="h-3 w-3" />}
                    </span>
                    <span className="truncate">{opt}</span>
                  </button>
                );
              })}
              {!filteredOptions.length && (
                <p className="py-6 text-center text-xs text-slate-400">No options match.</p>
              )}
            </div>
          </>
        )}
        </div>

        <div className="flex justify-end gap-2 border-t border-slate-100 px-5 py-3 dark:border-slate-800">
          <button
            type="button"
            onClick={onClose}
            className="rounded-xl px-4 py-2 text-xs font-semibold text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleSave}
            className="rounded-xl bg-amber-500 px-4 py-2 text-xs font-bold text-white hover:bg-amber-600"
          >
            Apply
          </button>
        </div>
      </div>
    </div>
  );
}

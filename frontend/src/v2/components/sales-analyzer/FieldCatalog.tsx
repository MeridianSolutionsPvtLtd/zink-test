import React, { useState, useMemo } from 'react';
import { Search, GripVertical, Plus, Layers, Hash, Calendar, MapPin, Store, Tag, Sparkles } from 'lucide-react';
import type { DimensionMeta, MeasureMeta } from './types';

interface FieldCatalogProps {
  dimensions: DimensionMeta[];
  measures: MeasureMeta[];
  onAddField: (field: string, targetArea: 'rows' | 'columns' | 'values' | 'filters') => void;
  activeRows: string[];
  activeCols: string[];
  activeValues: { field: string }[];
  activeFilters: Record<string, any>;
}

export function FieldCatalog({
  dimensions,
  measures,
  onAddField,
  activeRows,
  activeCols,
  activeValues,
  activeFilters,
}: FieldCatalogProps) {
  const [searchTerm, setSearchTerm] = useState('');
  const [activeTab, setActiveTab] = useState<'all' | 'dimensions' | 'measures'>('all');

  const filteredDimensions = useMemo(() => {
    if (!dimensions) return [];
    if (!searchTerm.trim()) return dimensions;
    const q = searchTerm.toLowerCase();
    return dimensions.filter(
      (d) => d.label.toLowerCase().includes(q) || d.field.toLowerCase().includes(q) || d.category.toLowerCase().includes(q)
    );
  }, [dimensions, searchTerm]);

  const filteredMeasures = useMemo(() => {
    if (!measures) return [];
    if (!searchTerm.trim()) return measures;
    const q = searchTerm.toLowerCase();
    return measures.filter(
      (m) => m.label.toLowerCase().includes(q) || m.field.toLowerCase().includes(q)
    );
  }, [measures, searchTerm]);

  const getDimIcon = (cat: string) => {
    switch (cat) {
      case 'Store':
        return <Store className="h-3.5 w-3.5 text-blue-500" />;
      case 'Location':
        return <MapPin className="h-3.5 w-3.5 text-amber-500" />;
      case 'Product':
        return <Tag className="h-3.5 w-3.5 text-purple-500" />;
      case 'Time':
        return <Calendar className="h-3.5 w-3.5 text-emerald-500" />;
      default:
        return <Layers className="h-3.5 w-3.5 text-slate-400" />;
    }
  };

  const handleDragStart = (e: React.DragEvent, field: string, type: 'dimension' | 'measure') => {
    e.dataTransfer.setData('text/plain', JSON.stringify({ field, type }));
    e.dataTransfer.effectAllowed = 'copy';
  };

  return (
    <div className="flex flex-col h-[620px] max-h-[620px] overflow-hidden rounded-2xl border border-slate-200/90 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900/80">
      {/* Header */}
      <div className="p-3 border-b border-slate-200 dark:border-slate-800 shrink-0">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <div className="flex h-6 w-6 items-center justify-center rounded-lg bg-blue-600/10 text-blue-600 dark:bg-blue-500/20 dark:text-blue-400">
              <Layers className="h-3.5 w-3.5" />
            </div>
            <div>
              <h3 className="text-xs font-bold uppercase tracking-wider text-slate-800 dark:text-slate-100">
                Available Fields
              </h3>
              <p className="text-[10px] text-slate-400">Drag to Rows, Cols, Values</p>
            </div>
          </div>
          <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-bold text-slate-600 dark:bg-slate-800 dark:text-slate-400">
            {dimensions.length + measures.length}
          </span>
        </div>

        {/* Search */}
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
          <input
            type="text"
            placeholder="Search dimensions & measures…"
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="w-full rounded-xl border border-slate-200 bg-slate-50/70 pl-8 pr-3 py-1 text-xs text-slate-800 placeholder-slate-400 focus:border-blue-500 focus:bg-white focus:outline-none dark:border-slate-700 dark:bg-slate-800/80 dark:text-slate-100 dark:focus:bg-slate-800"
          />
        </div>

        {/* Filter Tabs */}
        <div className="mt-2 flex rounded-lg bg-slate-100 p-0.5 text-[10px] font-semibold dark:bg-slate-800">
          <button
            onClick={() => setActiveTab('all')}
            className={`flex-1 rounded-md py-1 text-center transition ${
              activeTab === 'all'
                ? 'bg-white text-blue-600 shadow-xs dark:bg-slate-700 dark:text-blue-400 font-bold'
                : 'text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-200'
            }`}
          >
            All ({dimensions.length + measures.length})
          </button>
          <button
            onClick={() => setActiveTab('dimensions')}
            className={`flex-1 rounded-md py-1 text-center transition ${
              activeTab === 'dimensions'
                ? 'bg-white text-blue-600 shadow-xs dark:bg-slate-700 dark:text-blue-400 font-bold'
                : 'text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-200'
            }`}
          >
            Dims ({dimensions.length})
          </button>
          <button
            onClick={() => setActiveTab('measures')}
            className={`flex-1 rounded-md py-1 text-center transition ${
              activeTab === 'measures'
                ? 'bg-white text-blue-600 shadow-xs dark:bg-slate-700 dark:text-blue-400 font-bold'
                : 'text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-200'
            }`}
          >
            Metrics ({measures.length})
          </button>
        </div>
      </div>

      {/* Field List Container with auto-flex scrollable area and visible custom scrollbar */}
      <div className="flex-1 min-h-0 overflow-y-auto p-2.5 space-y-3 custom-scrollbar">
        {/* Dimensions Section */}
        {(activeTab === 'all' || activeTab === 'dimensions') && (
          <div>
            <div className="flex items-center justify-between mb-1.5 px-1">
              <span className="text-[10px] font-extrabold uppercase tracking-wider text-slate-400 dark:text-slate-500">
                Dimensions ({filteredDimensions.length})
              </span>
            </div>
            <div className="space-y-1">
              {filteredDimensions.map((dim) => {
                const isRowActive = activeRows.includes(dim.field);
                const isColActive = activeCols.includes(dim.field);
                const isFilterActive = Boolean(activeFilters[dim.field]);
                return (
                  <div
                    key={dim.field}
                    draggable
                    onDragStart={(e) => handleDragStart(e, dim.field, 'dimension')}
                    className={`group flex items-center justify-between rounded-xl border px-2.5 py-1.5 text-xs transition cursor-grab active:cursor-grabbing ${
                      isRowActive || isColActive
                        ? 'border-blue-200 bg-blue-50/60 text-blue-900 dark:border-blue-800/60 dark:bg-blue-950/30 dark:text-blue-300 font-semibold'
                        : 'border-slate-200/70 bg-white text-slate-700 hover:border-blue-300 hover:bg-slate-50 dark:border-slate-800 dark:bg-slate-850 dark:text-slate-200 dark:hover:border-slate-700'
                    }`}
                  >
                    <div className="flex items-center gap-2 min-w-0">
                      <GripVertical className="h-3.5 w-3.5 text-slate-300 group-hover:text-slate-500 shrink-0" />
                      {getDimIcon(dim.category)}
                      <span className="truncate">{dim.label}</span>
                    </div>
                    <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition">
                      <button
                        title="Add to Rows"
                        onClick={() => onAddField(dim.field, 'rows')}
                        className="rounded px-1.5 py-0.5 text-[10px] font-bold bg-slate-100 hover:bg-blue-600 hover:text-white dark:bg-slate-700 dark:hover:bg-blue-500"
                      >
                        +Row
                      </button>
                      <button
                        title="Add to Columns"
                        onClick={() => onAddField(dim.field, 'columns')}
                        className="rounded px-1.5 py-0.5 text-[10px] font-bold bg-slate-100 hover:bg-blue-600 hover:text-white dark:bg-slate-700 dark:hover:bg-blue-500"
                      >
                        +Col
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Measures Section */}
        {(activeTab === 'all' || activeTab === 'measures') && (
          <div>
            <div className="flex items-center justify-between mb-1.5 px-1">
              <span className="text-[10px] font-extrabold uppercase tracking-wider text-slate-400 dark:text-slate-500">
                Measures ({filteredMeasures.length})
              </span>
            </div>
            <div className="space-y-1">
              {filteredMeasures.map((measure) => {
                const isValueActive = activeValues.some((v) => v.field === measure.field);
                return (
                  <div
                    key={measure.field}
                    draggable
                    onDragStart={(e) => handleDragStart(e, measure.field, 'measure')}
                    className={`group flex items-center justify-between rounded-xl border px-2.5 py-1.5 text-xs transition cursor-grab active:cursor-grabbing ${
                      isValueActive
                        ? 'border-emerald-200 bg-emerald-50/60 text-emerald-900 dark:border-emerald-800/60 dark:bg-emerald-950/30 dark:text-emerald-300 font-semibold'
                        : 'border-slate-200/70 bg-white text-slate-700 hover:border-emerald-300 hover:bg-slate-50 dark:border-slate-800 dark:bg-slate-850 dark:text-slate-200 dark:hover:border-slate-700'
                    }`}
                  >
                    <div className="flex items-center gap-2 min-w-0">
                      <GripVertical className="h-3.5 w-3.5 text-slate-300 group-hover:text-slate-500 shrink-0" />
                      <Hash className={`h-3.5 w-3.5 ${measure.isLy ? 'text-amber-500' : measure.isCalculated ? 'text-indigo-500' : 'text-emerald-500'}`} />
                      <span className="truncate">{measure.label}</span>
                    </div>
                    <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition">
                      <button
                        title="Add to Values"
                        onClick={() => onAddField(measure.field, 'values')}
                        className="rounded px-1.5 py-0.5 text-[10px] font-bold bg-slate-100 hover:bg-emerald-600 hover:text-white dark:bg-slate-700 dark:hover:bg-emerald-500"
                      >
                        +Val
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

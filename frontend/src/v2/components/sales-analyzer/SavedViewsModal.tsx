import React, { useState } from 'react';
import { X, Bookmark, Trash2, Check, FolderOpen, Plus } from 'lucide-react';
import type { SavedView, ValueConfig } from './types';

interface SavedViewsModalProps {
  currentConfig: {
    rows: string[];
    columns: string[];
    values: ValueConfig[];
    filters: Record<string, any>;
    year?: number;
  };
  savedViews: SavedView[];
  onLoadView: (view: SavedView) => void;
  onSaveCurrentView: (name: string, description?: string) => void;
  onDeleteView: (id: string) => void;
  onClose: () => void;
}

export function SavedViewsModal({
  currentConfig,
  savedViews,
  onLoadView,
  onSaveCurrentView,
  onDeleteView,
  onClose,
}: SavedViewsModalProps) {
  const [viewName, setViewName] = useState('');
  const [description, setDescription] = useState('');
  const [activeTab, setActiveTab] = useState<'save' | 'load'>('load');

  const handleSave = (e: React.FormEvent) => {
    e.preventDefault();
    if (!viewName.trim()) return;
    onSaveCurrentView(viewName.trim(), description.trim());
    setViewName('');
    setDescription('');
    setActiveTab('load');
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/60 p-4 backdrop-blur-xs">
      <div className="flex w-full max-w-lg flex-col rounded-2xl border border-slate-200 bg-white shadow-2xl dark:border-slate-800 dark:bg-slate-900">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-slate-100 p-4 dark:border-slate-800">
          <div className="flex items-center gap-2">
            <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-blue-600/10 text-blue-600 dark:bg-blue-500/20 dark:text-blue-400">
              <Bookmark className="h-4 w-4" />
            </div>
            <div>
              <h3 className="text-sm font-bold text-slate-900 dark:text-white">
                Saved Pivot Views
              </h3>
              <p className="text-[11px] text-slate-500">
                Save & load custom analysis layouts
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600 dark:hover:bg-slate-800"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Tabs */}
        <div className="flex border-b border-slate-100 px-4 pt-2 text-xs font-semibold dark:border-slate-800">
          <button
            onClick={() => setActiveTab('load')}
            className={`border-b-2 px-3 py-2 transition ${
              activeTab === 'load'
                ? 'border-blue-600 text-blue-600 dark:border-blue-400 dark:text-blue-400'
                : 'border-transparent text-slate-500 hover:text-slate-800 dark:text-slate-400'
            }`}
          >
            My Saved Views ({savedViews.length})
          </button>
          <button
            onClick={() => setActiveTab('save')}
            className={`border-b-2 px-3 py-2 transition ${
              activeTab === 'save'
                ? 'border-blue-600 text-blue-600 dark:border-blue-400 dark:text-blue-400'
                : 'border-transparent text-slate-500 hover:text-slate-800 dark:text-slate-400'
            }`}
          >
            + Save Current Layout
          </button>
        </div>

        {/* Content */}
        <div className="p-4">
          {activeTab === 'save' ? (
            <form onSubmit={handleSave} className="space-y-3">
              <div>
                <label className="block text-xs font-bold text-slate-700 dark:text-slate-300 mb-1">
                  View Name <span className="text-rose-500">*</span>
                </label>
                <input
                  type="text"
                  required
                  placeholder="e.g. Zone & Store Channel Matrix"
                  value={viewName}
                  onChange={(e) => setViewName(e.target.value)}
                  className="w-full rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-800 placeholder-slate-400 focus:border-blue-500 focus:bg-white focus:outline-none dark:border-slate-700 dark:bg-slate-800 dark:text-white"
                />
              </div>
              <div>
                <label className="block text-xs font-bold text-slate-700 dark:text-slate-300 mb-1">
                  Description (Optional)
                </label>
                <input
                  type="text"
                  placeholder="e.g. Compares CY vs LY sales by zone with channel split"
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  className="w-full rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-800 placeholder-slate-400 focus:border-blue-500 focus:bg-white focus:outline-none dark:border-slate-700 dark:bg-slate-800 dark:text-white"
                />
              </div>

              {/* Current Configuration Summary */}
              <div className="rounded-xl border border-slate-100 bg-slate-50 p-3 text-[11px] dark:border-slate-800 dark:bg-slate-800/60">
                <span className="font-bold text-slate-700 dark:text-slate-300">
                  Current Layout snapshot:
                </span>
                <div className="mt-1 space-y-0.5 text-slate-500 dark:text-slate-400">
                  <div>
                    <span className="font-semibold text-blue-600">Rows:</span>{' '}
                    {currentConfig.rows.join(' → ') || '(none)'}
                  </div>
                  <div>
                    <span className="font-semibold text-indigo-600">Columns:</span>{' '}
                    {currentConfig.columns.join(', ') || '(none)'}
                  </div>
                  <div>
                    <span className="font-semibold text-emerald-600">Values:</span>{' '}
                    {currentConfig.values.map((v) => `${v.field} (${v.aggregation})`).join(', ') || '(none)'}
                  </div>
                </div>
              </div>

              <div className="flex justify-end pt-2">
                <button
                  type="submit"
                  className="inline-flex items-center gap-1.5 rounded-xl bg-blue-600 px-4 py-2 text-xs font-semibold text-white shadow-sm hover:bg-blue-700 transition"
                >
                  <Check className="h-3.5 w-3.5" />
                  Save View
                </button>
              </div>
            </form>
          ) : (
            <div className="space-y-2 max-h-72 overflow-y-auto">
              {savedViews.map((sv) => (
                <div
                  key={sv.id}
                  className="group flex items-center justify-between rounded-xl border border-slate-200/80 bg-white p-3 shadow-2xs hover:border-blue-300 hover:bg-blue-50/20 dark:border-slate-800 dark:bg-slate-850 dark:hover:border-blue-800"
                >
                  <div className="flex-1 min-w-0 pr-3">
                    <h4 className="text-xs font-bold text-slate-900 dark:text-white truncate">
                      {sv.name}
                    </h4>
                    {sv.description && (
                      <p className="text-[11px] text-slate-500 dark:text-slate-400 truncate">
                        {sv.description}
                      </p>
                    )}
                    <div className="mt-1 flex flex-wrap items-center gap-2 text-[10px] text-slate-400">
                      <span>Rows: {sv.config.rows.join(' > ') || 'none'}</span>
                      {sv.config.columns.length > 0 && (
                        <span>Cols: {sv.config.columns.join(', ')}</span>
                      )}
                      <span>Values: {sv.config.values.length}</span>
                    </div>
                  </div>

                  <div className="flex items-center gap-1.5">
                    <button
                      onClick={() => {
                        onLoadView(sv);
                        onClose();
                      }}
                      className="inline-flex items-center gap-1 rounded-lg bg-blue-50 px-2.5 py-1.5 text-xs font-semibold text-blue-700 hover:bg-blue-600 hover:text-white dark:bg-blue-950/60 dark:text-blue-300 dark:hover:bg-blue-600 dark:hover:text-white transition"
                    >
                      <FolderOpen className="h-3.5 w-3.5" />
                      Load
                    </button>
                    <button
                      onClick={() => onDeleteView(sv.id)}
                      className="rounded-lg p-1.5 text-slate-400 hover:bg-rose-50 hover:text-rose-600 dark:hover:bg-rose-950/40"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </div>
              ))}

              {savedViews.length === 0 && (
                <div className="py-8 text-center text-xs text-slate-400">
                  <Bookmark className="mx-auto h-8 w-8 text-slate-300 mb-2 dark:text-slate-700" />
                  No saved views yet. Configure your layout and click "Save Current Layout" to create one!
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

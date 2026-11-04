import React, { useEffect, useState } from 'react';
import DashboardLayout from '../layouts/DashboardLayout';
import { BookOpen, Calendar, Trash2, ArrowRight } from 'lucide-react';
import { useNavigate } from 'react-router-dom';

const STORAGE_KEY = 'stockpulse_saved_plans';

function loadSavedPlans(): any[] {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');
  } catch {
    return [];
  }
}

function deletePlan(id: number) {
  const plans = loadSavedPlans().filter((p: any) => p.id !== id);
  localStorage.setItem(STORAGE_KEY, JSON.stringify(plans));
}

function formatDate(iso: string) {
  return new Date(iso).toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' });
}

export default function SavedPlans() {
  const [plans, setPlans] = useState<any[]>([]);
  const navigate = useNavigate();

  useEffect(() => {
    setPlans(loadSavedPlans());
  }, []);

  const handleDelete = (id: number) => {
    deletePlan(id);
    setPlans(loadSavedPlans());
  };

  return (
    <DashboardLayout>
      <div className="space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-slate-900 dark:text-white">Saved Plans</h1>
            <p className="text-slate-500 dark:text-slate-400">All previously generated seasonal inventory plans</p>
          </div>
          <button
            onClick={() => navigate('/seasonal-planning')}
            className="flex items-center gap-2 px-4 py-2 bg-emerald-500 text-white rounded-lg hover:bg-emerald-600 transition-colors text-sm font-medium"
          >
            <Calendar className="w-4 h-4" /> New Plan
          </button>
        </div>

        {/* Empty state */}
        {plans.length === 0 && (
          <div className="flex flex-col items-center justify-center py-24 text-center">
            <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-slate-100 dark:bg-slate-800 mb-4">
              <BookOpen className="w-7 h-7 text-slate-400" />
            </div>
            <h3 className="text-lg font-semibold text-slate-900 dark:text-white mb-1">No saved plans yet</h3>
            <p className="text-slate-500 dark:text-slate-400 mb-6 max-w-sm">Generate your first seasonal plan and it will appear here automatically.</p>
            <button
              onClick={() => navigate('/seasonal-planning')}
              className="flex items-center gap-2 px-5 py-2.5 bg-emerald-500 text-white rounded-lg hover:bg-emerald-600 transition-colors font-medium"
            >
              <Calendar className="w-4 h-4" /> Create First Plan
            </button>
          </div>
        )}

        {/* Plans grid */}
        {plans.length > 0 && (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {plans.map((plan: any) => (
              <div
                key={plan.id}
                className="bg-white dark:bg-slate-900/50 border border-slate-200 dark:border-slate-800 rounded-xl p-5 hover:shadow-md transition-shadow flex flex-col gap-4 cursor-pointer"
                onClick={() => navigate('/seasonal-planning', { state: { plan } })}
              >
                {/* Title row */}
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <h3 className="font-semibold text-slate-900 dark:text-white">{plan.name}</h3>
                    <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">{formatDate(plan.createdAt)}</p>
                  </div>
                  <button
                    onClick={e => { e.stopPropagation(); handleDelete(plan.id); }}
                    className="p-1.5 rounded-lg text-slate-400 hover:text-rose-500 hover:bg-rose-500/10 transition-colors"
                    title="Delete plan"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>

                {/* Season badges */}
                <div className="flex flex-wrap gap-2">
                  <span className="px-2.5 py-1 rounded-full text-xs font-medium bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">
                    🎯 {plan.targetSeason}
                  </span>
                  {(plan.baselines || [plan.baselineSeason]).map((b: string) => (
                    <span key={b} className="px-2.5 py-1 rounded-full text-xs font-medium bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-400">
                      📊 {b}
                    </span>
                  ))}
                </div>

                {/* Summary metrics */}
                {plan.summary && (
                  <div className="grid grid-cols-4 gap-1 border-t border-slate-100 dark:border-slate-800 pt-3">
                    <div className="text-center px-1">
                      <div className="text-[10px] uppercase tracking-wider text-slate-500 dark:text-slate-400 mb-0.5 whitespace-nowrap">Rec. Units</div>
                      <div className="text-xs font-semibold text-slate-900 dark:text-white">
                        {new Intl.NumberFormat('en-IN').format(plan.summary.recommendedUnits)}
                      </div>
                    </div>
                    <div className="text-center px-1 border-l border-slate-100 dark:border-slate-800">
                      <div className="text-[10px] uppercase tracking-wider text-slate-500 dark:text-slate-400 mb-0.5 whitespace-nowrap">LY ST%</div>
                      <div className="text-xs font-semibold text-emerald-600 dark:text-emerald-400">{plan.summary.lySellThrough}%</div>
                    </div>
                    <div className="text-center px-1 border-l border-slate-100 dark:border-slate-800">
                      <div className="text-[10px] uppercase tracking-wider text-slate-500 dark:text-slate-400 mb-0.5 whitespace-nowrap">Total Sold</div>
                      <div className="text-xs font-semibold text-slate-900 dark:text-white">
                        {new Intl.NumberFormat('en-IN').format(plan.summary.totalSoldQty || 0)}
                      </div>
                    </div>
                    <div className="text-center px-1 border-l border-slate-100 dark:border-slate-800">
                      <div className="text-[10px] uppercase tracking-wider text-slate-500 dark:text-slate-400 mb-0.5 whitespace-nowrap">At-Risk</div>
                      <div className="text-xs font-semibold text-rose-600 dark:text-rose-400">
                        {plan.summary.atRiskSkus ?? plan.summary.highRiskSkus ?? 0}
                      </div>
                    </div>
                  </div>
                )}

                {/* View button */}
                <button
                  onClick={e => { e.stopPropagation(); navigate('/seasonal-planning', { state: { plan } }); }}
                  className="w-full flex items-center justify-center gap-1.5 py-2 rounded-lg border border-slate-200 dark:border-slate-700 text-sm text-slate-600 dark:text-slate-400 hover:bg-slate-50 dark:hover:bg-slate-800 transition-colors"
                >
                  {plan.data ? <>Open Plan <ArrowRight className="w-3.5 h-3.5" /></> : 'Plan data not available (re-generate to save)'}
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </DashboardLayout>
  );
}

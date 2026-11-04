import React, { useEffect, useState } from 'react';
import DashboardLayout from '../layouts/DashboardLayout';
import { useLocation } from 'react-router-dom';
import { Calendar, Package, TrendingUp, AlertTriangle, ChevronLeft, ChevronRight, ArrowUpDown, CheckSquare, Square, Sparkles, BarChart2, Store } from 'lucide-react';
import { FilterDropdown } from '../components/FilterDropdown';
import { InfoTooltip } from '../components/InfoTooltip';
import { PageHelper } from '../components/PageHelper';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';

import { API_URL } from '../../config';

const TARGET_SEASONS = [
  { id: 'SS-26', label: 'Spring/Summer 26' },
  { id: 'AW-26', label: 'Autumn/Winter 26' },
];

const BASELINE_OPTIONS = [
  { id: 'SS-24', label: 'Spring/Summer 24' },
  { id: 'AW-24', label: 'Autumn/Winter 24' },
  { id: 'SS-25', label: 'Spring/Summer 25' },
  { id: 'AW-25', label: 'Autumn/Winter 25' },
];

// ─── Helper: save / load plans from localStorage ────────────────────────────
const STORAGE_KEY = 'stockpulse_saved_plans';

function loadSavedPlans(): any[] {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');
  } catch {
    return [];
  }
}

function savePlan(plan: any) {
  const existing = loadSavedPlans();
  existing.unshift(plan);   // newest first
  localStorage.setItem(STORAGE_KEY, JSON.stringify(existing.slice(0, 50))); // keep last 50
}

// ─── Custom Tooltip for Stacked Charts ─────────────────────────────────────────
const AllocationTooltip = ({ active, payload, label }: any) => {
  if (active && payload && payload.length) {
    const data = payload[0].payload;
    const currentTotal = data.current || 0;
    const currentFP = data.current_fp || 0;
    const currentDisc = data.current_disc || 0;
    const recTotal = data.recommended || 0;
    const recFP = data.recommended_fp || 0;
    const recDisc = data.recommended_disc || 0;

    const fmt = (v: number) => new Intl.NumberFormat('en-IN').format(Math.round(v));

    return (
      <div className="bg-white dark:bg-slate-900/95 border border-slate-200 dark:border-slate-800 p-3 rounded-lg shadow-xl backdrop-blur-sm min-w-[240px]">
        <p className="font-bold text-slate-900 dark:text-white mb-3 border-b border-slate-100 dark:border-slate-800 pb-2">{label}</p>
        
        <div className="space-y-3">
          <div>
            <div className="text-[10px] text-slate-400 dark:text-slate-500 uppercase tracking-wider mb-1">Current Base</div>
            <div className="text-sm font-semibold text-slate-900 dark:text-white flex flex-col gap-0.5">
              <span>Total units: {fmt(currentTotal)}</span>
              <span className="text-[11px] font-normal text-slate-500 dark:text-slate-400 italic">
                (Full Price: {fmt(currentFP)}, Discounted: {fmt(currentDisc)})
              </span>
            </div>
          </div>

          <div className="pt-2 border-t border-slate-100 dark:border-slate-800">
            <div className="text-[10px] text-emerald-500/70 dark:text-emerald-400/50 uppercase tracking-wider mb-1">Recommendation</div>
            <div className="text-sm font-semibold text-emerald-600 dark:text-emerald-400 flex flex-col gap-0.5">
              <span>Target units: {fmt(recTotal)}</span>
              <span className="text-[11px] font-normal text-emerald-500/60 dark:text-emerald-400/60 italic">
                (Full Price: {fmt(recFP)}, Discounted: {fmt(recDisc)})
              </span>
            </div>
          </div>
        </div>
      </div>
    );
  }
  return null;
};

// ─── Wizard (Landing) ────────────────────────────────────────────────────────
function PlanWizard({ onGenerate }: { onGenerate: (cfg: any) => void }) {
  const [planName, setPlanName] = useState('');
  const [targetSeason, setTargetSeason] = useState('');
  const [baselines, setBaselines] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [submitted, setSubmitted] = useState(false);

  const errors = {
    planName: !planName.trim(),
    targetSeason: !targetSeason,
    baselines: baselines.length === 0,
  };
  const hasErrors = errors.planName || errors.targetSeason || errors.baselines;

  const toggleBaseline = (id: string) => {
    setBaselines(prev =>
      prev.includes(id) ? prev.filter(b => b !== id) : [...prev, id]
    );
  };

  const handleGenerate = async () => {
    setSubmitted(true);
    if (hasErrors) return;
    setLoading(true);
    const primaryBaseline = baselines[0];
    onGenerate({ planName: planName.trim(), targetSeason, baselineSeason: primaryBaseline, baselines });
  };

  const availableBaselines = BASELINE_OPTIONS.filter(b => b.id !== targetSeason);

  return (
    <DashboardLayout>
      <div className="min-h-[80vh] flex flex-col items-center justify-center py-12 px-4">
        {/* Icon + heading */}
        <div className="mb-6 text-center">
          <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-emerald-500/15 mb-4">
            <Package className="w-7 h-7 text-emerald-500" />
          </div>
          <h1 className="text-3xl font-bold text-slate-900 dark:text-white mb-2">Create New Season Plan</h1>
          <p className="text-slate-500 dark:text-slate-400 max-w-md">
            Define your target season and let our AI engine allocate the optimal inventory
            across stores and SKUs based on historical sell-through velocity.
          </p>
        </div>

        {/* Form card */}
        <div className="w-full max-w-lg bg-white dark:bg-slate-900/60 border border-slate-200 dark:border-slate-700 rounded-2xl shadow-lg p-8 space-y-6">
          {/* Plan Name */}
          <div>
            <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">
              Plan Name <span className="text-rose-500">*</span>
            </label>
            <input
              type="text"
              value={planName}
              onChange={e => setPlanName(e.target.value)}
              placeholder={`e.g. Core Collection ${targetSeason || 'AW-26'}`}
              className={`w-full px-4 py-2.5 rounded-lg border bg-slate-50 dark:bg-slate-800 text-slate-900 dark:text-white placeholder-slate-400 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500 ${
                submitted && errors.planName
                  ? 'border-rose-400 ring-1 ring-rose-400'
                  : 'border-slate-200 dark:border-slate-700'
              }`}
            />
            {submitted && errors.planName && (
              <p className="text-xs text-rose-500 mt-1">Plan name is required.</p>
            )}
          </div>

          {/* Target Season */}
          <div>
            <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-2">
              Target Season <span className="text-rose-500">*</span>
            </label>
            <div className="grid grid-cols-2 gap-3">
              {TARGET_SEASONS.map(s => (
                <button
                  key={s.id}
                  onClick={() => setTargetSeason(s.id)}
                  className={`flex items-center justify-between px-4 py-3 rounded-xl border text-sm font-medium transition-colors ${
                    targetSeason === s.id
                      ? 'bg-emerald-500/10 border-emerald-500 text-emerald-700 dark:text-emerald-300'
                      : 'border-slate-200 dark:border-slate-700 text-slate-600 dark:text-slate-400 hover:border-slate-300 dark:hover:border-slate-600'
                  }`}
                >
                  {s.label}
                  {targetSeason === s.id && <span className="text-emerald-500">✓</span>}
                </button>
              ))}
            </div>
            {submitted && errors.targetSeason && (
              <p className="text-xs text-rose-500 mt-1">Please select a target season.</p>
            )}
          </div>

          {/* Historical Baselines */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <label className="block text-sm font-medium text-slate-700 dark:text-slate-300">
                Historical Baselines <span className="text-slate-400 font-normal">(Averaged)</span> <span className="text-rose-500">*</span>
              </label>
              <button
                onClick={() => setBaselines(availableBaselines.map(b => b.id))}
                className="text-xs text-emerald-600 dark:text-emerald-400 hover:underline"
              >
                Select All
              </button>
            </div>
            <div className="grid grid-cols-2 gap-2">
              {availableBaselines.map(b => {
                const selected = baselines.includes(b.id);
                return (
                  <button
                    key={b.id}
                    onClick={() => toggleBaseline(b.id)}
                    className={`flex items-center gap-2 px-3 py-2.5 rounded-xl border text-sm transition-colors ${
                      selected
                        ? 'bg-emerald-500/10 border-emerald-500 text-emerald-700 dark:text-emerald-300'
                        : 'border-slate-200 dark:border-slate-700 text-slate-600 dark:text-slate-400 hover:border-slate-300'
                    }`}
                  >
                    {selected
                      ? <CheckSquare className="w-4 h-4 text-emerald-500 shrink-0" />
                      : <Square className="w-4 h-4 text-slate-400 shrink-0" />}
                    {b.label}
                  </button>
                );
              })}
            </div>
            {submitted && errors.baselines && (
              <p className="text-xs text-rose-500 mt-1">Please select at least one baseline season.</p>
            )}
          </div>

          {/* Generate Button */}
          <button
            onClick={handleGenerate}
            disabled={loading || baselines.length === 0}
            className="w-full flex items-center justify-center gap-2 py-3 bg-emerald-500 hover:bg-emerald-600 disabled:opacity-50 disabled:cursor-not-allowed text-white font-semibold rounded-xl transition-colors"
          >
            {loading ? (
              <><div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" /> Generating...</>
            ) : (
              <><Sparkles className="w-4 h-4" /> Generate Plan</>
            )}
          </button>
        </div>
      </div>
    </DashboardLayout>
  );
}

// ─── Plan View ───────────────────────────────────────────────────────────────
export default function SeasonalPlanning() {
  const location = useLocation();
  const incoming = (location.state as any)?.plan;   // set by SavedPlans when opening a saved plan

  const [phase, setPhase] = useState<'wizard' | 'plan'>(incoming ? 'plan' : 'wizard');
  const [planConfig, setPlanConfig] = useState<any>(incoming
    ? { planName: incoming.name, targetSeason: incoming.targetSeason, baselineSeason: incoming.baselineSeason, baselines: incoming.baselines || [incoming.baselineSeason] }
    : null
  );
  const [data, setData] = useState<any>(incoming?.data ?? null);
  const [loading, setLoading] = useState(false);
  const [activeTab, setActiveTab] = useState('overview');
  const [sortConfig, setSortConfig] = useState<{ key: string | null; direction: 'asc' | 'desc' }>({ key: null, direction: 'asc' });
  const [currentPage, setCurrentPage] = useState(1);
  const [localStoreFilter, setLocalStoreFilter] = useState('All Stores');
  const itemsPerPage = 10;

  useEffect(() => { setCurrentPage(1); }, [activeTab, localStoreFilter]);

  const handleGenerate = async (cfg: any) => {
    setPlanConfig(cfg);
    setLoading(true);
    try {
      // Pass multiple baseline_seasons parameters for FastAPI list[str]
      const baselineParams = cfg.baselines.map((b: string) => `baseline_seasons=${b}`).join('&');
      const res = await fetch(`${API_URL}/seasonal/plan?target_season=${cfg.targetSeason}&${baselineParams}`);
      const json = await res.json();
      setData(json);
      // Persist to localStorage
      savePlan({
        id: Date.now(),
        name: cfg.planName,
        targetSeason: cfg.targetSeason,
        baselineSeason: cfg.baselines[0], // Keep as legacy primary
        baselines: cfg.baselines,
        createdAt: new Date().toISOString(),
        summary: json.summary,
        data: json,          // full plan data for reopening
      });
      setPhase('plan');
    } catch (err) {
      console.error('Error generating plan:', err);
    } finally {
      setLoading(false);
    }
  };

  if (phase === 'wizard' || loading) {
    if (loading) {
      return (
        <DashboardLayout>
          <div className="flex flex-col items-center justify-center h-96 gap-3">
            <div className="w-8 h-8 border-4 border-emerald-500 border-t-transparent rounded-full animate-spin" />
            <div className="text-slate-400">Generating plan for {planConfig?.targetSeason}…</div>
          </div>
        </DashboardLayout>
      );
    }
    return <PlanWizard onGenerate={handleGenerate} />;
  }

  if (!data) return null;

  const { summary, categoryAllocation, stores, skus } = data;

  const handleSort = (key: string) => {
    let direction: 'asc' | 'desc' = 'asc';
    if (sortConfig.key === key && sortConfig.direction === 'asc') direction = 'desc';
    setSortConfig({ key, direction });
  };

  const sortData = (arr: any[]) => {
    if (!sortConfig.key) return arr;
    return [...arr].sort((a, b) => {
      if (a[sortConfig.key!] < b[sortConfig.key!]) return sortConfig.direction === 'asc' ? -1 : 1;
      if (a[sortConfig.key!] > b[sortConfig.key!]) return sortConfig.direction === 'asc' ? 1 : -1;
      return 0;
    });
  };

  const filteredStores = localStoreFilter === 'All Stores' ? stores : (stores || []).filter((s: any) => s.store === localStoreFilter);
  const sortedStores = activeTab === 'stores' ? sortData(filteredStores) : filteredStores;
  
  const sortedSkus = activeTab === 'skus' ? sortData(skus || []) : (skus || []);

  return (
    <DashboardLayout>
      <div className="space-y-6">
        {/* Header */}
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold text-slate-900 dark:text-white">{planConfig?.planName}</h1>
            <p className="text-slate-500 dark:text-slate-400">
              Target: <span className="font-medium text-slate-700 dark:text-slate-300">{planConfig?.targetSeason}</span>
              {' '}· Baseline: <span className="font-medium text-slate-700 dark:text-slate-300">{planConfig?.baselines?.join(', ')}</span>
            </p>
          </div>
          <button
            onClick={() => { setPhase('wizard'); setData(null); }}
            className="flex items-center gap-2 px-4 py-2 bg-emerald-500 text-white rounded-lg hover:bg-emerald-600 transition-colors text-sm font-medium"
          >
            <Calendar className="w-4 h-4" /> New Plan
          </button>
        </div>

        {/* Plan Summary Banner */}
        <div className="p-4 bg-emerald-500/10 border border-emerald-500/20 rounded-xl flex items-center gap-4">
          <div className="p-2 bg-emerald-500/20 rounded-lg">
            <Package className="w-6 h-6 text-emerald-600 dark:text-emerald-400" />
          </div>
          <div>
            <h3 className="font-semibold text-emerald-800 dark:text-emerald-100">{planConfig?.targetSeason} Inventory Plan</h3>
            <p className="text-sm text-emerald-700/70 dark:text-emerald-200/60">
              {data.skus.length} Top SKUs · {data.skus.filter((s: any) => s.action === 'Increase').length} to increase · {data.skus.filter((s: any) => s.action === 'Reduce').length} to reduce
            </p>
          </div>
        </div>

        {/* Page Helper */}
        <PageHelper
          title="How Seasonal Planning works"
          summary="Compares last year's (LY) actual sell-through against the current season plan to generate recommended inventory adjustments at store and SKU level."
          dataSource="Historical Performance Records"
          refreshNote="Recalculated when season or baseline changes"
          items={[
            { icon: <Package className="w-4 h-4" />, label: 'Recommended Units', description: 'Target stock level calculated by applying performance multipliers to the baseline: ST > 80% (1.2x hike), ST 50-80% (1.1x hike), ST 40-50% (1.05x hike), and ST < 40% (0.9x reduction).' },
            { icon: <TrendingUp className="w-4 h-4" />, label: 'Avg Sell-Through (Baseline Seasons)', description: 'Weighted average sell-through from your selected baseline seasons. Formula: Total Sold ÷ Total Received × 100.' },
            { icon: <Sparkles className="w-4 h-4" />, label: 'Action', description: 'Automation recommendation: "Increase" if ST > 40%, "Reduce" if ST < 40%, or "Maintain" if stock perfectly matches demand velocity.' },
            { icon: <CheckSquare className="w-4 h-4" />, label: 'Readiness', description: 'Indicates distribution readiness. Marked "Ready" for stores with efficient sell-through (>60%) and "In Progress" for others.' },
            { icon: <BarChart2 className="w-4 h-4" />, label: 'Confidence', description: 'Statistical reliability score. Rated at 85% for stable high-performers (ST > 60%) and 75% for emerging or fluctuating patterns.' },
          ]}
        />

        {/* KPI Cards */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div className="p-6 bg-white dark:bg-slate-900/50 border border-slate-200 dark:border-slate-800 rounded-xl">
            <div className="flex items-center gap-3 mb-2">
              <div className="p-2 bg-slate-100 dark:bg-slate-800 rounded-lg"><Package className="w-5 h-5 text-slate-500" /></div>
              <div className="flex items-center gap-1">
                <div className="text-sm text-slate-500 dark:text-slate-400">Recommended Units</div>
                <InfoTooltip title="Recommended Units" description="Total units recommended for target season." formula="Averaged Baseline Sales × Trend Multiplier" source="Historical Sales Data" />
              </div>
            </div>
            <div className="text-2xl font-bold text-slate-900 dark:text-white">{new Intl.NumberFormat('en-IN').format(summary.recommendedUnits)}</div>
          </div>
          <div className="p-6 bg-white dark:bg-slate-900/50 border border-slate-200 dark:border-slate-800 rounded-xl">
            <div className="flex items-center gap-3 mb-2">
              <div className="p-2 bg-emerald-500/10 rounded-lg"><TrendingUp className="w-5 h-5 text-emerald-500" /></div>
              <div className="flex items-center gap-1">
                <div className="text-sm text-slate-500 dark:text-slate-400">Avg Sell-Through (Baseline Seasons)</div>
                <InfoTooltip title="Avg Sell-Through" description="Weighted average sell-through from your selected baseline historical seasons." formula="Units Sold ÷ Units Received × 100" source="Aggregated Seasonal Data" />
              </div>
            </div>
            <div className="text-2xl font-bold text-slate-900 dark:text-white">{summary.lySellThrough}%</div>
          </div>
          <div className="p-6 bg-white dark:bg-slate-900/50 border border-slate-200 dark:border-slate-800 rounded-xl">
            <div className="flex items-center gap-3 mb-2">
              <div className="p-2 bg-rose-500/10 rounded-lg"><BarChart2 className="w-5 h-5 text-rose-500" /></div>
              <div className="flex items-center gap-1">
                <div className="text-sm text-slate-500 dark:text-slate-400">Avg Sold Qty (Baseline Seasons)</div>
                <InfoTooltip title="Avg Sold Qty" description="Average units sold per season across your selected historical baseline periods." formula="Total Sold ÷ Number of Baselines" source="Averaged Historical Data" />
              </div>
            </div>
            <div className="text-2xl font-bold text-slate-900 dark:text-white">
              {new Intl.NumberFormat('en-IN').format(
                summary.avgSoldQty ?? (summary.totalSoldQty / (planConfig?.baselines?.length || 1))
              )}
            </div>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex items-center gap-4 border-b border-slate-200 dark:border-slate-800">
          {['overview', 'stores', 'skus'].map(tab => (
            <button key={tab} onClick={() => setActiveTab(tab)}
              className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${activeTab === tab ? 'border-emerald-500 text-emerald-600 dark:text-emerald-400' : 'border-transparent text-slate-500 dark:text-slate-400 hover:text-slate-900 dark:hover:text-white'}`}>
              {tab.charAt(0).toUpperCase() + tab.slice(1)}
            </button>
          ))}
        </div>

        {/* Overview Tab */}
        {activeTab === 'overview' && (
          <div className="bg-white dark:bg-slate-900/50 border border-slate-200 dark:border-slate-800 rounded-xl p-6">
            <h3 className="text-lg font-semibold text-slate-900 dark:text-white mb-6">Category Allocation — {planConfig?.targetSeason}</h3>
            <div className="h-96">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={categoryAllocation}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" className="dark:[stroke:#1e293b]" />
                  <XAxis dataKey="category" stroke="#94a3b8" />
                  <YAxis stroke="#94a3b8" />
                  <Tooltip content={<AllocationTooltip />} />
                  <Legend />
                  <Bar dataKey="current_fp" name="Current (Full Price)" fill="#94a3b8" stackId="current" />
                  <Bar dataKey="current_disc" name="Current (Discounted)" fill="#475569" stackId="current" />
                  <Bar dataKey="recommended_fp" name="Recommended (Full Price)" fill="#10b981" stackId="recommended" />
                  <Bar dataKey="recommended_disc" name="Recommended (Discounted)" fill="#065f46" stackId="recommended" />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
        )}

        {/* Stores Tab */}
        {activeTab === 'stores' && (
          <div className="space-y-4">
            <div className="flex justify-between items-center bg-white dark:bg-slate-900/50 p-4 border border-slate-200 dark:border-slate-800 rounded-xl">
              <div className="flex items-center gap-3">
                <span className="text-sm font-medium text-slate-500 dark:text-slate-400">Store filter:</span>
                <select
                  value={localStoreFilter}
                  onChange={(e) => setLocalStoreFilter(e.target.value)}
                  className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-900"
                >
                  <option value="All Stores">All Stores</option>
                </select>
              </div>
            </div>
            <div className="bg-white dark:bg-slate-900/50 border border-slate-200 dark:border-slate-800 rounded-xl overflow-hidden">
            <table className="w-full text-left text-sm">
              <thead className="bg-slate-50 dark:bg-slate-800/50 text-slate-500 dark:text-slate-400 border-b border-slate-100 dark:border-slate-800">
                <tr>
                  <th className="p-4 font-medium cursor-pointer hover:text-slate-900 dark:hover:text-white" onClick={() => handleSort('store')}><div className="flex items-center gap-2">Store <ArrowUpDown className="w-4 h-4" /></div></th>
                   <th className="p-4 font-medium text-right cursor-pointer hover:text-slate-900 dark:hover:text-white" onClick={() => handleSort('planned')}><div className="flex items-center justify-end gap-2">Average Units Sold (Baseline Seasons) <ArrowUpDown className="w-4 h-4" /></div></th>
                   <th className="p-4 font-medium text-right cursor-pointer hover:text-slate-900 dark:hover:text-white" onClick={() => handleSort('recommended')}><div className="flex items-center justify-end gap-2">Recommended Units <ArrowUpDown className="w-4 h-4" /></div></th>
                  <th className="p-4 font-medium text-right cursor-pointer hover:text-slate-900 dark:hover:text-white" onClick={() => handleSort('delta_units')}><div className="flex items-center justify-end gap-2">Δ Units <ArrowUpDown className="w-4 h-4" /></div></th>
                   <th className="p-4 font-medium text-right cursor-pointer hover:text-slate-900 dark:hover:text-white" onClick={() => handleSort('ly_sell_through')}><div className="flex items-center justify-end gap-2">Avg. ST% (Baseline Seasons) <ArrowUpDown className="w-4 h-4" /></div></th>
                  <th className="p-4 font-medium">Top Category</th>
                  <th className="p-4 font-medium cursor-pointer hover:text-slate-900 dark:hover:text-white" onClick={() => handleSort('readiness')}><div className="flex items-center gap-2">Readiness <ArrowUpDown className="w-4 h-4" /></div></th>
                  <th className="p-4 font-medium text-right">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {sortedStores.slice((currentPage - 1) * itemsPerPage, currentPage * itemsPerPage).map((store: any, idx: number) => (
                  <tr key={idx} className="hover:bg-slate-50 dark:hover:bg-slate-800/30 transition-colors">
                    <td className="p-4 font-medium text-slate-900 dark:text-white">{store.store}</td>
                    <td className="p-4 text-right text-slate-500 dark:text-slate-400">{new Intl.NumberFormat('en-IN').format(store.planned)}</td>
                    <td className="p-4 text-right font-medium text-slate-900 dark:text-white">{new Intl.NumberFormat('en-IN').format(store.recommended)}</td>
                    <td className={`p-4 text-right ${store.delta_units >= 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-rose-600 dark:text-rose-400'}`}>{store.delta_units > 0 ? '+' : ''}{new Intl.NumberFormat('en-IN').format(store.delta_units)}</td>
                    <td className={`p-4 text-right ${store.ly_sell_through >= 80 ? 'text-emerald-600 dark:text-emerald-400' : store.ly_sell_through < 50 ? 'text-rose-600 dark:text-rose-400' : 'text-amber-600 dark:text-amber-400'}`}>{store.ly_sell_through}%</td>
                    <td className="p-4 text-slate-500 dark:text-slate-400">{store.top_category}</td>
                    <td className="p-4"><span className={`px-2 py-1 rounded-full text-xs font-medium ${store.readiness === 'Ready' ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400' : 'bg-amber-500/10 text-amber-600 dark:text-amber-400'}`}>{store.readiness}</span></td>
                    <td className="p-4 text-right"><button className="text-slate-500 dark:text-slate-400 hover:text-slate-900 dark:hover:text-white border border-slate-200 dark:border-slate-700 px-3 py-1 rounded-lg text-xs hover:bg-slate-50 dark:hover:bg-slate-800 transition-colors">Review</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="flex items-center justify-between p-4 border-t border-slate-100 dark:border-slate-800">
              <div className="text-sm text-slate-500 dark:text-slate-400">Showing <span className="font-medium text-slate-900 dark:text-white">{Math.min((currentPage - 1) * itemsPerPage + 1, stores.length)}</span> to <span className="font-medium text-slate-900 dark:text-white">{Math.min(currentPage * itemsPerPage, stores.length)}</span> of <span className="font-medium text-slate-900 dark:text-white">{stores.length}</span> stores</div>
              <div className="flex items-center gap-2">
                <button onClick={() => setCurrentPage(p => Math.max(1, p - 1))} disabled={currentPage === 1} className="p-2 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-800 text-slate-500 disabled:opacity-50 transition-colors"><ChevronLeft className="w-5 h-5" /></button>
                <span className="text-sm text-slate-500 dark:text-slate-400">Page {currentPage} of {Math.ceil(stores.length / itemsPerPage)}</span>
                <button onClick={() => setCurrentPage(p => Math.min(Math.ceil(stores.length / itemsPerPage), p + 1))} disabled={currentPage === Math.ceil(stores.length / itemsPerPage)} className="p-2 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-800 text-slate-500 disabled:opacity-50 transition-colors"><ChevronRight className="w-5 h-5" /></button>
              </div>
            </div>
          </div>
        </div>
      )}

        {/* SKUs Tab */}
        {activeTab === 'skus' && (
          <div className="bg-white dark:bg-slate-900/50 border border-slate-200 dark:border-slate-800 rounded-xl overflow-hidden">
            <div className="overflow-x-auto">
            <table className="min-w-max w-full text-left text-sm">
              <thead className="bg-slate-50 dark:bg-slate-800/50 text-slate-500 dark:text-slate-400 border-b border-slate-100 dark:border-slate-800">
                <tr>
                  <th className="px-3 py-3 font-medium cursor-pointer hover:text-slate-900 dark:hover:text-white whitespace-nowrap" onClick={() => handleSort('section')}><div className="flex items-center gap-1">Section <ArrowUpDown className="w-3 h-3" /></div></th>
                  <th className="px-3 py-3 font-medium cursor-pointer hover:text-slate-900 dark:hover:text-white whitespace-nowrap" onClick={() => handleSort('department')}><div className="flex items-center gap-1">Department <ArrowUpDown className="w-3 h-3" /></div></th>
                  <th className="px-3 py-3 font-medium cursor-pointer hover:text-slate-900 dark:hover:text-white whitespace-nowrap" onClick={() => handleSort('color')}><div className="flex items-center gap-1">Color <ArrowUpDown className="w-3 h-3" /></div></th>
                  <th className="px-3 py-3 font-medium cursor-pointer hover:text-slate-900 dark:hover:text-white whitespace-nowrap" onClick={() => handleSort('fabric')}><div className="flex items-center gap-1">Fabric <ArrowUpDown className="w-3 h-3" /></div></th>
                  <th className="px-3 py-3 font-medium cursor-pointer hover:text-slate-900 dark:hover:text-white whitespace-nowrap" onClick={() => handleSort('size')}><div className="flex items-center gap-1">Size <ArrowUpDown className="w-3 h-3" /></div></th>
                  <th className="px-3 py-3 font-medium cursor-pointer hover:text-slate-900 dark:hover:text-white whitespace-nowrap" onClick={() => handleSort('mrp')}><div className="flex items-center gap-1">MRP <ArrowUpDown className="w-3 h-3" /></div></th>
                  <th className="px-3 py-3 font-medium text-right cursor-pointer hover:text-slate-900 dark:hover:text-white whitespace-nowrap" onClick={() => handleSort('ly_sell_through')}><div className="flex items-center justify-end gap-1">Avg. ST% (Baseline Seasons) <ArrowUpDown className="w-3 h-3" /></div></th>
                  <th className="px-3 py-3 font-medium text-right cursor-pointer hover:text-slate-900 dark:hover:text-white whitespace-nowrap" onClick={() => handleSort('planned')}><div className="flex items-center justify-end gap-1">Average Units Sold (Baseline Seasons) <ArrowUpDown className="w-3 h-3" /></div></th>
                  <th className="px-3 py-3 font-medium text-right cursor-pointer hover:text-slate-900 dark:hover:text-white whitespace-nowrap" onClick={() => handleSort('recommended')}><div className="flex items-center justify-end gap-1">Recommended units <ArrowUpDown className="w-3 h-3" /></div></th>
                  <th className="px-3 py-3 font-medium text-center cursor-pointer hover:text-slate-900 dark:hover:text-white whitespace-nowrap" onClick={() => handleSort('action')}><div className="flex items-center justify-center gap-1">Action <ArrowUpDown className="w-3 h-3" /></div></th>
                  <th className="px-3 py-3 font-medium text-center cursor-pointer hover:text-slate-900 dark:hover:text-white whitespace-nowrap" onClick={() => handleSort('risk')}><div className="flex items-center justify-center gap-1">Risk <ArrowUpDown className="w-3 h-3" /></div></th>
                  <th className="px-3 py-3 font-medium text-right cursor-pointer hover:text-slate-900 dark:hover:text-white whitespace-nowrap" onClick={() => handleSort('confidence')}><div className="flex items-center justify-end gap-1">Confidence <ArrowUpDown className="w-3 h-3" /></div></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {sortedSkus.slice((currentPage - 1) * itemsPerPage, currentPage * itemsPerPage).map((sku: any, idx: number) => (
                  <tr key={idx} className="hover:bg-slate-50 dark:hover:bg-slate-800/30 transition-colors">
                    <td className="px-3 py-3 text-slate-700 dark:text-slate-200 font-medium whitespace-nowrap">{sku.section || '—'}</td>
                    <td className="px-3 py-3 text-slate-500 dark:text-slate-400 whitespace-nowrap">{sku.department || '—'}</td>
                    <td className="px-3 py-3 text-slate-500 dark:text-slate-400 whitespace-nowrap">{sku.color || '—'}</td>
                    <td className="px-3 py-3 text-slate-500 dark:text-slate-400 whitespace-nowrap">{sku.fabric || '—'}</td>
                    <td className="px-3 py-3 text-slate-500 dark:text-slate-400 whitespace-nowrap">{sku.size || '—'}</td>
                    <td className="px-3 py-3 text-emerald-500 dark:text-emerald-400 font-medium whitespace-nowrap">₹{sku.mrp || '0'}</td>
                    <td className={`px-3 py-3 text-right whitespace-nowrap ${sku.ly_sell_through >= 80 ? 'text-emerald-600 dark:text-emerald-400' : 'text-slate-500 dark:text-slate-400'}`}>{sku.ly_sell_through}%</td>
                    <td className="px-3 py-3 text-right text-slate-500 dark:text-slate-400 whitespace-nowrap">{new Intl.NumberFormat('en-IN').format(sku.planned)}</td>
                    <td className="px-3 py-3 text-right font-medium text-slate-900 dark:text-white whitespace-nowrap">{new Intl.NumberFormat('en-IN').format(sku.recommended)}<span className={`ml-1 text-xs ${sku.delta_pct >= 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-rose-600 dark:text-rose-400'}`}>({sku.delta_pct > 0 ? '+' : ''}{sku.delta_pct}%)</span></td>
                    <td className="px-3 py-3 text-center whitespace-nowrap"><span className={`px-2 py-1 rounded-full text-xs font-medium ${sku.action === 'Increase' ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400' : sku.action === 'Reduce' ? 'bg-rose-500/10 text-rose-600 dark:text-rose-400' : 'bg-slate-100 dark:bg-slate-500/10 text-slate-500 dark:text-slate-400'}`}>{sku.action}</span></td>
                    <td className="px-3 py-3 text-center whitespace-nowrap"><span className={`px-2 py-1 rounded-full text-xs font-medium ${sku.risk === 'Low' ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400' : sku.risk === 'High' ? 'bg-rose-500/10 text-rose-600 dark:text-rose-400' : 'bg-amber-500/10 text-amber-600 dark:text-amber-400'}`}>{sku.risk}</span></td>
                    <td className="px-3 py-3 text-right text-emerald-600 dark:text-emerald-400 whitespace-nowrap">{sku.confidence}%</td>
                  </tr>


                ))}
              </tbody>
            </table>
            </div>{/* end overflow-x-auto */}
            <div className="flex items-center justify-between p-4 border-t border-slate-100 dark:border-slate-800">
              <div className="text-sm text-slate-500 dark:text-slate-400">Showing <span className="font-medium text-slate-900 dark:text-white">{Math.min((currentPage - 1) * itemsPerPage + 1, skus.length)}</span> to <span className="font-medium text-slate-900 dark:text-white">{Math.min(currentPage * itemsPerPage, skus.length)}</span> of <span className="font-medium text-slate-900 dark:text-white">{skus.length}</span> SKUs</div>
              <div className="flex items-center gap-2">
                <button onClick={() => setCurrentPage(p => Math.max(1, p - 1))} disabled={currentPage === 1} className="p-2 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-800 text-slate-500 disabled:opacity-50 transition-colors"><ChevronLeft className="w-5 h-5" /></button>
                <span className="text-sm text-slate-500 dark:text-slate-400">Page {currentPage} of {Math.ceil(skus.length / itemsPerPage)}</span>
                <button onClick={() => setCurrentPage(p => Math.min(Math.ceil(skus.length / itemsPerPage), p + 1))} disabled={currentPage === Math.ceil(skus.length / itemsPerPage)} className="p-2 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-800 text-slate-500 disabled:opacity-50 transition-colors"><ChevronRight className="w-5 h-5" /></button>
              </div>
            </div>
          </div>
        )}
      </div>
    </DashboardLayout>
  );
}

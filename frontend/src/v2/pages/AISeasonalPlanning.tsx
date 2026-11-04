import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Brain, Calendar, Sparkles } from 'lucide-react';
import DashboardLayout from '../layouts/DashboardLayout';
import { DashboardShell, PageHeader } from '../design-system';
import { PageHelper } from '../components/PageHelper';
import { InfoTooltip } from '../components/InfoTooltip';
import { useFilters } from '../contexts/SeasonContext';
import {
  ForecastFilterPanel,
  EMPTY_CLIENT_FORECAST_FILTERS,
} from '../components/forecast-dashboard/ForecastFilterPanel';
import { ForecastKpiGrid } from '../components/forecast-dashboard/ForecastKpiGrid';
import {
  DemandRiskSection,
  DemandSupplyChart,
  FocusDayPrediction,
  ForecastAccuracySection,
  ForecastVarianceChart,
  ForecastVsActualChart,
  PlanningCalendar,
  PremiumAllocationChart,
  ProjectedInventoryChart,
  ProjectedSalesChart,
  SeasonalHeatmap,
} from '../components/forecast-dashboard/ForecastCharts';
import { ForecastDetailTable } from '../components/forecast-dashboard/ForecastDetailTable';
import {
  ForecastDetailModal,
  ForecastPlanningTable,
} from '../components/forecast-dashboard/ForecastPlanningTable';
import { ForecastAIInsights } from '../components/forecast-dashboard/ForecastAIInsights';
import { ForecastAlertsPanel } from '../components/forecast-dashboard/ForecastAlerts';
import {
  applyClientFilters,
  computeForecastKpis,
  type AllocationRow,
  type ClientForecastFilters,
  type InsightRow,
} from '../components/forecast-dashboard/forecastAnalytics';

const TARGET_SEASONS = [
  { id: 'SS-26', label: 'Spring/Summer 26' },
  { id: 'AW-26', label: 'Autumn/Winter 26' },
];

/**
 * AI Seasonal Planning / Forecast Dashboard V2
 * Route unchanged: /ai-seasonal-planning
 * All existing APIs, filters (SeasonContext navbar), table logic preserved.
 */
export default function AISeasonalPlanning() {
  const { baselineYears, aiSite, aiSKU, aiDept, aiMonth } = useFilters();
  const [targetSeason, setTargetSeason] = useState(TARGET_SEASONS[0].id);
  const [recommendedUnits, setRecommendedUnits] = useState<number | null>(null);
  const [growthPct, setGrowthPct] = useState<number | null>(null);
  const [categoryData, setCategoryData] = useState<AllocationRow[]>([]);
  const [colorData, setColorData] = useState<AllocationRow[]>([]);
  const [fabricData, setFabricData] = useState<AllocationRow[]>([]);
  const [insightsData, setInsightsData] = useState<InsightRow[]>([]);
  const [selectedRow, setSelectedRow] = useState<InsightRow | null>(null);
  const [loading, setLoading] = useState(false);
  const [tableLoading, setTableLoading] = useState(false);
  const [totalInsights, setTotalInsights] = useState(0);
  const [sortBy, setSortBy] = useState('predicted_qty');
  const [sortOrder, setSortOrder] = useState('DESC');
  const [isFetchingMore, setIsFetchingMore] = useState(false);
  const [hasMore, setHasMore] = useState(true);
  const [clientFilters, setClientFilters] = useState<ClientForecastFilters>(EMPTY_CLIENT_FORECAST_FILTERS);
  const [lastRefresh, setLastRefresh] = useState(new Date());

  const getFilterParams = () => {
    let params = `target_season=${targetSeason}`;
    baselineYears.forEach((y) => (params += `&baseline_years=${y}`));
    if (aiSite !== 'All Sites') params += `&site=${encodeURIComponent(aiSite)}`;
    if (aiSKU !== 'All SKUs') params += `&sku=${encodeURIComponent(aiSKU)}`;
    if (aiDept !== 'All Departments') params += `&dept=${encodeURIComponent(aiDept)}`;
    if (aiMonth !== 'All Months') params += `&month=${aiMonth}`;
    return params;
  };

  const fetchData = async () => {
    setLoading(true);
    try {
      const res = await fetch(`/api/ai-seasonal/recommendation-summary?${getFilterParams()}`);
      const data = await res.json();
      if (data.status === 'success') {
        setRecommendedUnits(data.totalRecommendedUnits);
        setGrowthPct(data.qtyGrowthPct);
      } else {
        console.error('API Error:', data.error);
        setRecommendedUnits(0);
        setGrowthPct(0);
      }
    } catch (err) {
      console.error('Failed to fetch recommendation summary:', err);
    } finally {
      setLoading(false);
    }
  };

  const fetchAllocationData = async (attr: string, setter: (val: AllocationRow[]) => void) => {
    try {
      const res = await fetch(`/api/ai-seasonal/attribute-allocation?attribute=${attr}&${getFilterParams()}`);
      const data = await res.json();
      if (data.status === 'success') {
        setter(data.data ?? []);
      }
    } catch (err) {
      console.error(`Failed to fetch ${attr} allocation:`, err);
    }
  };

  const fetchInsightsData = async (isAppend = false) => {
    if (isAppend) {
      if (isFetchingMore || !hasMore) return;
      setIsFetchingMore(true);
    } else {
      setTableLoading(true);
    }

    try {
      const offset = isAppend ? insightsData.length : 0;
      const limit = 100;
      const res = await fetch(
        `/api/ai-seasonal/planning-insights?limit=${limit}&offset=${offset}&sort_by=${sortBy}&sort_order=${sortOrder}&${getFilterParams()}`
      );
      const data = await res.json();
      if (data.status === 'success') {
        const batch = data.data || [];
        setInsightsData((prev) => (isAppend ? [...prev, ...batch] : batch));
        setTotalInsights(data.total || 0);
        setHasMore(batch.length === limit);
      }
    } catch (err) {
      console.error('Failed to fetch planning insights:', err);
    } finally {
      if (isAppend) setIsFetchingMore(false);
      else setTableLoading(false);
    }
  };

  const handleTableScroll = (e: React.UIEvent<HTMLDivElement>) => {
    const { scrollTop, scrollHeight, clientHeight } = e.currentTarget;
    if (scrollHeight - scrollTop <= clientHeight + 100) {
      fetchInsightsData(true);
    }
  };

  useEffect(() => {
    fetchData();
    fetchAllocationData('category', setCategoryData);
    fetchAllocationData('color', setColorData);
    fetchAllocationData('fabric', setFabricData);
    fetchInsightsData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetSeason, baselineYears, aiSite, aiSKU, aiDept, aiMonth]);

  useEffect(() => {
    fetchInsightsData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sortBy, sortOrder]);

  const handleSort = (column: string) => {
    if (sortBy === column) {
      setSortOrder(sortOrder === 'ASC' ? 'DESC' : 'ASC');
    } else {
      setSortBy(column);
      setSortOrder('DESC');
    }
  };

  const filteredInsights = useMemo(
    () => applyClientFilters(insightsData, clientFilters),
    [insightsData, clientFilters]
  );

  const kpis = useMemo(
    () => computeForecastKpis(recommendedUnits, growthPct, categoryData, filteredInsights),
    [recommendedUnits, growthPct, categoryData, filteredInsights]
  );

  const skuOptions = useMemo(
    () => [...new Set(insightsData.map((r) => r.sku).filter(Boolean) as string[])],
    [insightsData]
  );

  const handleClientFilterChange = useCallback((key: keyof ClientForecastFilters, value: string[]) => {
    setClientFilters((prev) => ({ ...prev, [key]: value }));
  }, []);

  const handleRefresh = () => {
    setLastRefresh(new Date());
    fetchData();
    fetchAllocationData('category', setCategoryData);
    fetchAllocationData('color', setColorData);
    fetchAllocationData('fabric', setFabricData);
    fetchInsightsData();
  };

  return (
    <DashboardLayout>
      <DashboardShell>
        <PageHeader
          icon={Brain}
          title="AI Forecast & Demand Planning"
          description="Enterprise forecast dashboard · Seasonal planning & demand visibility"
          badge="Forecast Dashboard"
          lastRefresh={lastRefresh}
          onRefresh={handleRefresh}
          loading={loading}
          onExport={() => window.print()}
        />

        <PageHelper
          title="AI Seasonal Recommendations"
          summary="This page uses the seasonal planning dataset to suggest optimal stocking levels for upcoming seasons."
          dataSource="Published planning & forecast feed"
          items={[
            { icon: <Brain className="h-4 w-4" />, label: 'Predictive Analytics', description: 'Uses historical trends and seasonal forecasting to predict demand.' },
            { icon: <Sparkles className="h-4 w-4" />, label: 'Target Recommendations', description: 'Provides optimized unit counts for each SKU/Store combination.' },
          ]}
        />

        {/* Season Selection — preserved */}
        <div className="rounded-2xl border border-slate-200/80 bg-white p-6 shadow-sm dark:border-slate-800 dark:bg-slate-900/60">
          <label className="mb-3 block text-xs font-semibold uppercase tracking-wider text-slate-500">
            Select Target Season
          </label>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            {TARGET_SEASONS.map((s) => (
              <button
                key={s.id}
                type="button"
                onClick={() => setTargetSeason(s.id)}
                className={`flex items-center justify-between rounded-xl border px-5 py-4 text-left font-medium transition-all ${
                  targetSeason === s.id
                    ? 'border-blue-600 bg-blue-50 text-blue-700 ring-2 ring-blue-600/20 dark:bg-blue-950/30 dark:text-blue-300'
                    : 'border-slate-200 text-slate-600 hover:border-slate-300 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800'
                }`}
              >
                <div className="flex items-center gap-3">
                  <Calendar className={`h-5 w-5 ${targetSeason === s.id ? 'text-blue-600' : 'text-slate-400'}`} />
                  {s.label}
                </div>
                {targetSeason === s.id && <Sparkles className="h-5 w-5 animate-pulse text-blue-500" />}
              </button>
            ))}
          </div>
          <p className="mt-3 text-[11px] text-slate-400">
            Navbar filters (Site, SKU, Department, Month, Baseline Years) apply to all API calls — unchanged.
          </p>
        </div>

        <ForecastFilterPanel
          clientFilters={clientFilters}
          skuOptions={skuOptions}
          loading={loading}
          onChange={handleClientFilterChange}
          onClearAll={() => setClientFilters(EMPTY_CLIENT_FORECAST_FILTERS)}
        />

        <ForecastKpiGrid data={kpis} loading={loading} />

        {/* Legacy summary KPIs — preserved as compact strip */}
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          <div className="rounded-2xl border border-slate-200/80 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900/60">
            <div className="mb-1 flex items-center gap-1">
              <span className="text-[11px] font-medium uppercase text-slate-500">Total Recommended Units</span>
              <InfoTooltip title="Recommended Units" description="Total units recommended for the chosen target season based on AI prediction." formula="SUM(predicted_monthly_qty_2026)" />
            </div>
            {loading ? (
              <div className="flex items-center gap-2 text-sm text-slate-400">
                <div className="h-4 w-4 animate-spin rounded-full border-2 border-blue-500 border-t-transparent" />
                Calculating...
              </div>
            ) : (
              <p className="text-2xl font-bold text-slate-900 dark:text-white">
                {recommendedUnits !== null ? new Intl.NumberFormat('en-IN').format(recommendedUnits) : '0'}
              </p>
            )}
          </div>
          <div className="rounded-2xl border border-slate-200/80 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900/60">
            <div className="mb-1 flex items-center gap-1">
              <span className="text-[11px] font-medium uppercase text-slate-500">Qty Growth %</span>
              <InfoTooltip title="Quantity Growth" description="Percent change in quantity sold from baseline years to target season year (2026)." formula="(Target Predicted Qty - Baseline Sold Qty) / Baseline Sold Qty" />
            </div>
            {loading ? (
              <div className="flex items-center gap-2 text-sm text-slate-400">
                <div className="h-4 w-4 animate-spin rounded-full border-2 border-emerald-500 border-t-transparent" />
                Calculating...
              </div>
            ) : (
              <p className={`text-2xl font-bold ${growthPct !== null && growthPct >= 0 ? 'text-emerald-500' : 'text-rose-500'}`}>
                {growthPct !== null ? `${growthPct > 0 ? '+' : ''}${growthPct}%` : '—'}
              </p>
            )}
          </div>
        </div>

        <ForecastVsActualChart categoryData={categoryData} />

        <div className="grid gap-4 lg:grid-cols-2">
          <ProjectedSalesChart forecast={kpis.forecast} />
          <ProjectedInventoryChart inventoryTarget={kpis.inventoryTarget} />
        </div>

        <div className="grid gap-4 lg:grid-cols-2">
          <DemandSupplyChart categoryData={categoryData} />
          <ForecastAccuracySection accuracy={kpis.accuracy} />
        </div>

        <div className="grid gap-4 lg:grid-cols-2">
          <SeasonalHeatmap categoryData={categoryData} />
          <FocusDayPrediction baseForecast={kpis.forecast} />
        </div>

        <div className="grid gap-4 lg:grid-cols-2">
          <ForecastVarianceChart categoryData={categoryData} />
          <PlanningCalendar forecast={kpis.forecast} target={kpis.target} inventoryTarget={kpis.inventoryTarget} />
        </div>

        <DemandRiskSection insights={filteredInsights} />

        {/* Original allocation charts — premium redesign */}
        <PremiumAllocationChart
          title={`Category Allocation — ${targetSeason}`}
          subtitle="Individual Baseline Years vs AI-Predicted Quantity"
          data={categoryData}
          baselineYears={baselineYears}
          height={360}
        />

        <div className="grid gap-4 lg:grid-cols-2">
          <PremiumAllocationChart
            title={`Color Allocation — ${targetSeason}`}
            subtitle="Predicted Quantity by Color"
            data={colorData}
            baselineYears={baselineYears}
            height={300}
          />
          <PremiumAllocationChart
            title={`Fabric Allocation — ${targetSeason}`}
            subtitle="Predicted Quantity by Fabric"
            data={fabricData}
            baselineYears={baselineYears}
            height={300}
          />
        </div>

        <ForecastDetailTable insights={filteredInsights} />

        <ForecastPlanningTable
          insightsData={insightsData}
          tableLoading={tableLoading}
          totalInsights={totalInsights}
          isFetchingMore={isFetchingMore}
          hasMore={hasMore}
          sortBy={sortBy}
          sortOrder={sortOrder}
          onSort={handleSort}
          onScroll={handleTableScroll}
          onRowClick={setSelectedRow}
        />

        <ForecastAlertsPanel kpis={kpis} insights={filteredInsights} />

        <ForecastAIInsights kpis={kpis} insights={filteredInsights} targetSeason={targetSeason} />
      </DashboardShell>

      {selectedRow && <ForecastDetailModal row={selectedRow} onClose={() => setSelectedRow(null)} />}
    </DashboardLayout>
  );
}

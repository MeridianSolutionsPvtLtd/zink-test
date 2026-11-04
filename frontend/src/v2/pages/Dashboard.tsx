import React, { useEffect, useState } from 'react';
import DashboardLayout from '../layouts/DashboardLayout';
import MetricCards from '../components/MetricCards';
import SellThroughTrendChart from '../components/SellThroughTrendChart';
import DiscountAnalysisChart from '../components/DiscountAnalysisChart';
import StorePerformanceTable from '../components/StorePerformanceTable';
import { PageHelper } from '../components/PageHelper';
import { useFilters } from '../contexts/SeasonContext';
import { TrendingUp, BarChart2, Store, Activity, Layers } from 'lucide-react';
import { API_URL } from '../../config';

const DashboardV2 = () => {
    const [dashboardData, setDashboardData] = useState<any>(null);
    const { season, sku, store, monthYear } = useFilters();
    const [error, setError] = useState<string | null>(null);
    const [isCapacityError, setIsCapacityError] = useState(false);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        setLoading(true);
        // Consolidate API calls into a single query to reduce overhead
        const url = `${API_URL}/dashboard/all?season=${encodeURIComponent(season)}&sku=${encodeURIComponent(sku)}&store=${encodeURIComponent(store)}&month_year=${encodeURIComponent(monthYear)}`;
        fetch(url)
            .then(res => res.json())
            .then(data => {
                if (data.is_capacity_error) {
                    setIsCapacityError(true);
                    setError(data.error || "Fabric compute capacity exceeded.");
                } else if (data.error) {
                    setError(data.error);
                } else {
                    setDashboardData(data);
                    setError(null);
                    setLoading(false);
                }
            })
            .catch(err => {
                console.error(err);
                setError("Failed to connect to backend.");
                setLoading(false);
            });
    }, [season, sku, store, monthYear]);

    if (loading && !dashboardData) {
        return (
            <DashboardLayout>
                <div className="flex flex-col items-center justify-center min-h-[60vh] gap-4">
                    <div className="w-12 h-12 border-4 border-emerald-500 border-t-transparent rounded-full animate-spin shadow-lg shadow-emerald-500/20" />
                    <div className="flex flex-col items-center">
                        <p className="text-lg font-bold text-slate-900 dark:text-white">Connecting to Analytics Engine</p>
                        <p className="text-sm text-slate-500 dark:text-slate-400 animate-pulse">Synchronizing seasonal datasets...</p>
                    </div>
                </div>
            </DashboardLayout>
        );
    }

    return (
        <DashboardLayout>
            <div className="space-y-6">
                <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
                    <div>
                        <h1 className="text-2xl font-bold text-slate-900 dark:text-white flex items-center gap-2">
                            <Layers className="w-6 h-6 text-emerald-500" />
                            Operations Overview
                        </h1>
                        <p className="text-slate-500 dark:text-slate-400">Manage and monitor inventory performance</p>
                    </div>
                </div>


                {error && (

                    <div className={`p-4 rounded-xl border flex flex-col gap-2 ${isCapacityError 
                        ? 'bg-rose-500/10 border-rose-500/20 text-rose-600 dark:text-rose-400' 
                        : 'bg-amber-500/10 border-amber-500/20 text-amber-600 dark:text-amber-400'}`}>
                        <div className="flex items-center gap-2 font-bold uppercase text-xs tracking-wider">
                            <Activity className="w-4 h-4 ml-[-2px]" />
                            {isCapacityError ? 'Compute Capacity Limit Hit' : 'Data Sync Error'}
                        </div>
                        <p className="text-sm">
                            {isCapacityError 
                                ? "Your data compute capacity has been exceeded. This dashboard requires processing units which are currently exhausted. Please try again later." 
                                : error}
                        </p>
                    </div>
                )}
                {/* Page Helper */}
                <PageHelper
                    title="How this Dashboard works"
                    summary="Real-time overview of inventory health, sell-through performance, and store-level alerts."
                    dataSource="Enterprise Data Warehouse"
                    refreshNote="Live — data reflects latest transactions"
                    items={[
                        {
                            icon: <TrendingUp className="w-4 h-4" />,
                            label: 'KPI Cards',
                            description: 'Top-line metrics: Sell-Through Rate (Sold / (Sold + Stock)), Revenue (Total Gross Sales), Avg Discount (Avg Discount %), and Total Sold Qty (Sum of all units sold).'
                        },
                        {
                            icon: <BarChart2 className="w-4 h-4" />,
                            label: 'Trend Charts',
                            description: 'Monthly Sell-Through Rate trend and Discount Rate breakdown by Department. Identifies seasonal velocity and markdown depth.'
                        },
                        {
                            icon: <Store className="w-4 h-4" />,
                            label: 'Store Performance',
                            description: 'Ranking of retail locations by growth and efficiency metrics.'
                        }
                    ]}
                />

                {/* Metric Cards */}
                <MetricCards data={dashboardData?.summary} />

                {/* Top Row: Charts */}
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                    <SellThroughTrendChart data={dashboardData?.trends} />
                    <DiscountAnalysisChart data={dashboardData?.discounts} />
                </div>

                {/* Middle Row: Table */}
                <StorePerformanceTable 
                    season={season} 
                    sku={sku} 
                    store={store} 
                    monthYear={monthYear} 
                />


            </div>
        </DashboardLayout>
    );
};


export default DashboardV2;

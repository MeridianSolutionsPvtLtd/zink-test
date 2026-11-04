import React, { useState, useEffect } from 'react';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer, ScatterChart, Scatter, ZAxis } from 'recharts';
import { Brain, TrendingUp, Tag, Layers, RefreshCw } from 'lucide-react';
import DashboardLayout from '../layouts/DashboardLayout';
import { PageHelper } from '../components/PageHelper';
import InfoTooltip from '../components/InfoTooltip';

interface DemandCurveData {
    velocity_bucket: string;
    Organic: number;
    Discount: number;
}

interface HeatmapData {
    format: string;
    category: string;
    organic_qty: number;
    discount_qty: number;
    total_qty: number;
    discount_dependency: number;
}

const KPICard = ({ label, value, subtext, icon: Icon, color, tooltip }: any) => (
    <div className="bg-white dark:bg-slate-800 p-6 rounded-xl border border-slate-200 dark:border-slate-700 shadow-sm relative">
        <div className="flex items-start justify-between">
            <div>
                <div className="flex items-center gap-1.5 mb-1">
                    <p className="text-sm font-medium text-slate-500 dark:text-slate-400">{label}</p>
                    {tooltip && <InfoTooltip title={tooltip.title} description={tooltip.description} />}
                </div>
                <h3 className="text-2xl font-bold text-slate-900 dark:text-white">{value}</h3>
                {subtext && <p className={`text-xs mt-1 ${color}`}>{subtext}</p>}
            </div>
            <div className={`p-3 rounded-lg ${color.replace('text-', 'bg-').replace('600', '100').replace('500', '100')} dark:bg-opacity-10`}>
                <Icon className={`w-6 h-6 ${color}`} />
            </div>
        </div>
    </div>
);

export default function AIPlanning() {
    const [curveData, setCurveData] = useState<DemandCurveData[]>([]);
    const [heatmapData, setHeatmapData] = useState<HeatmapData[]>([]);
    const [loading, setLoading] = useState(true);
    const [regionFilter, setRegionFilter] = useState('All Regions');

    const fetchData = async () => {
        setLoading(true);
        try {
            const query = regionFilter !== 'All Regions' ? `?region=${encodeURIComponent(regionFilter)}` : '';
            const res = await fetch(`/api/ai/demand-signal${query}`);
            const data = await res.json();

            if (data.error) {
                console.error("API Error:", data.error);
                return;
            }

            setCurveData(data.demand_curve || []);
            setHeatmapData(data.heatmap_data || []);
        } catch (err) {
            console.error("Failed to fetch AI data:", err);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchData();
    }, [regionFilter]);

    // Calculate aggregate KPIs
    const totalOrganic = (heatmapData || []).reduce((acc, curr) => acc + (curr.organic_qty || 0), 0);
    const totalDiscount = (heatmapData || []).reduce((acc, curr) => acc + (curr.discount_qty || 0), 0);
    const totalQty = totalOrganic + totalDiscount;
    const discountDependency = totalQty > 0 ? (totalDiscount / totalQty) * 100 : 0;

    return (
        <DashboardLayout>
            <div className="max-w-7xl mx-auto space-y-6 mt-6">

                <div className="flex items-center justify-between">
                    <div>
                        <h1 className="text-3xl font-bold text-slate-900 dark:text-white flex items-center gap-3">
                            <Brain className="w-8 h-8 text-indigo-600" />
                            AI Inventory Planning
                        </h1>
                        <p className="text-slate-500 dark:text-slate-400 mt-1">
                            Demand Signal Extraction & Velocity Fingerprinting
                        </p>
                    </div>


                </div>

                <PageHelper
                    title="Demand Signal Extraction"
                    summary="Separates genuine (organic) demand from discount-driven sales to identify true product velocity and baseline demand."
                    dataSource="Demand Analysis Engine (90-day window)"
                    items={[
                        { icon: <TrendingUp className="w-4 h-4" />, label: "Organic Demand", description: "Sales with < 10% discount. Represents true customer intent and healthy turnover." },
                        { icon: <Tag className="w-4 h-4" />, label: "Discount Demand", description: "Sales driven by price cuts (> 10% discount). Risk of over-forecasting if replenishment is based on these signals." },
                    ]}
                />

                {/* KPI Section */}
                <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                    <KPICard
                        label="Organic Volume (90d)"
                        value={totalOrganic.toLocaleString()}
                        subtext={`${(100 - discountDependency).toFixed(1)}% of total volume`}
                        icon={TrendingUp}
                        color="text-emerald-600"
                        tooltip={{
                            title: "Organic Volume",
                            description: "Total units sold with < 10% discount. Calculated from organic sales records."
                        }}
                    />
                    <KPICard
                        label="Discount Volume (90d)"
                        value={totalDiscount.toLocaleString()}
                        subtext={`${discountDependency.toFixed(1)}% dependency`}
                        icon={Tag}
                        color="text-rose-600"
                        tooltip={{
                            title: "Discount Volume",
                            description: "Total units sold with >= 10% discount. Calculated from price-reduction records."
                        }}
                    />
                    <KPICard
                        label="High Velocity SKUs"
                        value={curveData.find(c => c.velocity_bucket === '3.0+')?.Organic || 0}
                        subtext="Organic > 3.0 units/day"
                        icon={Layers}
                        color="text-indigo-600"
                        tooltip={{
                            title: "High Velocity SKUs",
                            description: "Number of unique SKUs selling more than 3 units per day (organic sales only). These are your 'True Best Sellers'."
                        }}
                    />
                </div>

                {/* Charts */}
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">

                    {/* Demand Curve Chart */}
                    <div className="bg-white dark:bg-slate-800 p-6 rounded-xl border border-slate-200 dark:border-slate-700 shadow-sm">
                        <div className="flex items-center gap-2 mb-4">
                            <h3 className="text-lg font-bold text-slate-900 dark:text-white">Demand Velocity Curve (SKU Count)</h3>
                            <InfoTooltip
                                title="Demand Velocity"
                                description="Distribution of SKUs based on their daily sales velocity. Left side = Slow movers, Right side = Fast movers. Green bars show organic demand, Red bars show demand driven by discounts."
                            />
                        </div>
                        <div className="h-80 w-full">
                            {curveData.length > 0 ? (
                                <ResponsiveContainer width="100%" height="100%" minHeight={300}>
                                    <BarChart data={curveData}>
                                        <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#E2E8F0" />
                                        <XAxis dataKey="velocity_bucket" stroke="#64748B" fontSize={12} />
                                        <YAxis stroke="#64748B" fontSize={12} />
                                        <Tooltip
                                            contentStyle={{ backgroundColor: '#1E293B', borderColor: '#334155', color: '#F1F5F9' }}
                                            itemStyle={{ color: '#F1F5F9' }}
                                        />
                                        <Legend />
                                        <Bar dataKey="Organic" fill="#10B981" name="Organic Demand" radius={[4, 4, 0, 0]} />
                                        <Bar dataKey="Discount" fill="#F43F5E" name="Discount Driven" radius={[4, 4, 0, 0]} />
                                    </BarChart>
                                </ResponsiveContainer>
                            ) : (
                                <div className="flex items-center justify-center h-full text-slate-400">
                                    No data available for the selected filters
                                </div>
                            )}
                        </div>
                        <p className="text-xs text-slate-500 mt-4 text-center">
                            X-Axis: Daily Units Sold  |  Y-Axis: Number of SKUs
                        </p>
                    </div>

                    {/* Format/Category Heatmap */}
                    <div className="bg-white dark:bg-slate-800 p-6 rounded-xl border border-slate-200 dark:border-slate-700 shadow-sm">
                        <div className="flex items-center gap-2 mb-4">
                            <h3 className="text-lg font-bold text-slate-900 dark:text-white">Category Discount Dependency</h3>
                            <InfoTooltip
                                title="Discount Dependency"
                                description="Shows which categories rely most on discounts to drive volume. High dependency (>50%) indicates pricing pressure or clearance activity."
                            />
                        </div>
                        <div className="overflow-y-auto h-80">
                            <table className="w-full text-sm text-left">
                                <thead className="text-xs text-slate-500 uppercase bg-slate-50 dark:bg-slate-700 sticky top-0">
                                    <tr>
                                        <th className="px-4 py-3">Category</th>
                                        <th className="px-4 py-3 text-right">Total Vol</th>
                                        <th className="px-4 py-3 text-right">Dependency</th>
                                        <th className="px-4 py-3 text-center flex items-center justify-center gap-1">
                                            Signal Type
                                            <InfoTooltip
                                                title="Signal Integrity"
                                                description="Genuine: > 50% Organic Sales. Diluted: > 50% Discount-Driven. Diluted signals indicate demand is artificially inflated by pricing."
                                            />
                                        </th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {heatmapData.map((row, idx) => (
                                        <tr key={idx} className="border-b border-slate-100 dark:border-slate-700 hover:bg-slate-50 dark:hover:bg-slate-700/50">
                                            <td className="px-4 py-3 font-medium text-slate-900 dark:text-white">{row.category}</td>
                                            <td className="px-4 py-3 text-right">{row.total_qty.toLocaleString()}</td>
                                            <td className="px-4 py-3 text-right">
                                                <span className={`font-bold ${(row.discount_dependency * 100) > 30 ? 'text-rose-500' : 'text-emerald-500'}`}>
                                                    {(row.discount_dependency * 100).toFixed(0)}%
                                                </span>
                                            </td>
                                            <td className="px-4 py-3 text-center">
                                                {(row.discount_dependency * 100) > 40 ?
                                                    <span className="bg-rose-100 text-rose-700 px-2 py-1 rounded text-xs font-bold">Diluted</span> :
                                                    <span className="bg-emerald-100 text-emerald-700 px-2 py-1 rounded text-xs font-bold">Strong</span>
                                                }
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    </div>

                </div>

            </div>
        </DashboardLayout>
    );
}

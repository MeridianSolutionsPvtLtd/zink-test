import React, { useState, useEffect } from 'react';
import { TrendingUp, DollarSign, Activity, Percent, ArrowDownRight, BarChart2 } from 'lucide-react';
import DashboardLayout from '../layouts/DashboardLayout';
import { PageHelper } from '../components/PageHelper';
import InfoTooltip from '../components/InfoTooltip';
import { formatCurrency, formatNumber } from '../utils/formatters';

interface SalesKPIContractData {
    units_sold: number;
    units_returned: number;
    net_units: number;
    net_revenue: number;
    asp: number;
    return_rate: number;
    units_30d: number;
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

const BreakdownTable = ({ title, data, columns }: any) => (
    <div className="bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 overflow-hidden">
        <div className="px-6 py-4 border-b border-slate-200 dark:border-slate-700 flex justify-between items-center">
            <h3 className="font-semibold text-slate-900 dark:text-white">{title}</h3>
        </div>
        <div className="overflow-x-auto">
            <table className="w-full text-sm text-left">
                <thead className="text-xs text-slate-500 uppercase bg-slate-50 dark:bg-slate-900/50">
                    <tr>
                        {columns.map((col: any) => (
                            <th key={col.key} className="px-6 py-3 font-medium cursor-pointer hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors">
                                <div className="flex items-center gap-1">
                                    {col.label}
                                    {col.tooltip && <InfoTooltip title={col.label} description={col.tooltip} />}
                                </div>
                            </th>
                        ))}
                    </tr>
                </thead>
                <tbody className="divide-y divide-slate-200 dark:divide-slate-700">
                    {data.map((row: any, i: number) => (
                        <tr key={i} className="hover:bg-slate-50 dark:hover:bg-slate-800/50">
                            {columns.map((col: any) => (
                                <td key={col.key} className="px-6 py-4 whitespace-nowrap text-slate-700 dark:text-slate-300">
                                    {col.format ? col.format(row[col.key], row) : row[col.key]}
                                </td>
                            ))}
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    </div>
);

export default function SalesKPI() {
    const [data, setData] = useState<SalesKPIContractData | null>(null);
    const [categoryData, setCategoryData] = useState([]);
    const [seasonData, setSeasonData] = useState([]);
    const [topSkus, setTopSkus] = useState([]);
    const [lowVelocity, setLowVelocity] = useState([]);
    const [loading, setLoading] = useState(true);
    const [activeTab, setActiveTab] = useState('category');

    useEffect(() => {
        Promise.all([
            fetch('/api/sales-kpi').then(res => res.json()),
            fetch('/api/sales-kpi/breakdown/category').then(res => res.json()),
            fetch('/api/sales-kpi/breakdown/season').then(res => res.json()),
            fetch('/api/sales-kpi/top-skus').then(res => res.json()),
            fetch('/api/sales-kpi/low-velocity').then(res => res.json())
        ]).then(([kpi, cat, season, top, low]) => {
            setData(kpi);
            setCategoryData(cat || []);
            setSeasonData(season || []);
            setTopSkus(top || []);
            setLowVelocity(low || []);
            setLoading(false);
        }).catch(err => {
            console.error("Failed to fetch Sales KPI data:", err);
            setLoading(false);
        });
    }, []);

    if (loading) return <DashboardLayout><div className="p-8">Loading Sales KPIs...</div></DashboardLayout>;

    const formatPct = (num: number) => `${((num || 0) * 100).toFixed(1)}%`;

    return (
        <DashboardLayout>
            <div className="space-y-6">
                <div>
                    <h1 className="text-2xl font-bold text-slate-900 dark:text-white">Sales KPI</h1>
                    <p className="text-slate-500 dark:text-slate-400">Canonical view of sales performance and revenue.</p>
                </div>

                <PageHelper
                    title="Sales KPI"
                    summary="This page provides a canonical view of sales performance across all product categories."
                    dataSource="Enterprise Sales Records"
                    items={[
                        { icon: <TrendingUp className="w-4 h-4" />, label: "Gross vs Net", description: "Units Sold is GROSS. Net Revenue includes deductions for returns." },
                        { icon: <ArrowDownRight className="w-4 h-4" />, label: "Return Rate", description: "Units Returned ÷ Units Sold. Calculated from negative adjustment records." },
                        { icon: <Activity className="w-4 h-4" />, label: "Velocity", description: "Rolling 30-day sales velocity calculated per SKU per site." }
                    ]}
                />

                <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                    <KPICard
                        label="Units Sold (Lifetime)"
                        value={data?.units_sold.toLocaleString()}
                        subtext={`Net: ${(data?.net_units || 0).toLocaleString()}`}
                        icon={TrendingUp}
                        color="text-emerald-600"
                        tooltip={{ title: "Units Sold", description: "Gross units sold (excluding returns)." }}
                    />
                    <KPICard
                        label="Net Revenue"
                        value={formatCurrency(data?.net_revenue || 0)}
                        subtext="After Returns"
                        icon={DollarSign}
                        color="text-indigo-600"
                        tooltip={{ title: "Net Revenue", description: "Total Sales Amount - Returns Amount." }}
                    />
                    <KPICard
                        label="Average Selling Price"
                        value={`₹${(data?.asp || 0).toFixed(0)}`}
                        subtext="Gross Revenue / Units Sold"
                        icon={BarChart2}
                        color="text-blue-600"
                        tooltip={{ title: "ASP", description: "Gross Revenue divided by Gross Units Sold." }}
                    />
                    <KPICard
                        label="Return Rate"
                        value={formatPct(data?.return_rate || 0)}
                        subtext={`${(data?.units_returned || 0).toLocaleString()} Returned`}
                        icon={Percent}
                        color="text-rose-600"
                        tooltip={{ title: "Return Rate", description: "Absolute Returned Units / Gross Units Sold." }}
                    />
                    <KPICard
                        label="Units (Last 30 Days)"
                        value={data?.units_30d.toLocaleString()}
                        subtext={`${formatPct((data?.units_30d || 0) / (data?.units_sold || 1))} of Lifetime`}
                        icon={Activity}
                        color="text-amber-600"
                        tooltip={{ title: "30-Day Volume", description: "Total units sold in the last 30 days." }}
                    />
                    <KPICard
                        label="30D Velocity"
                        value={((data?.units_30d || 0) / 30).toFixed(1)}
                        subtext="Units per Day"
                        icon={Activity}
                        color="text-violet-600"
                        tooltip={{ title: "Sales Velocity", description: "Average units sold per day over the last 30 days." }}
                    />
                </div>

                <div className="border-t border-slate-200 dark:border-slate-800 pt-6">
                    <div className="flex gap-2 mb-6 overflow-x-auto pb-2">
                        {['category', 'season', 'top-skus', 'low-velocity'].map(tab => (
                            <button
                                key={tab}
                                onClick={() => setActiveTab(tab)}
                                className={`px-4 py-2 rounded-lg text-sm font-medium whitespace-nowrap transition-colors ${activeTab === tab
                                    ? 'bg-emerald-500 text-white'
                                    : 'bg-white dark:bg-slate-800 text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-700'
                                    }`}
                            >
                                {tab.replace('-', ' ').toUpperCase()}
                            </button>
                        ))}
                    </div>

                    {activeTab === 'category' && (
                        <BreakdownTable
                            title="Sales by Category"
                            data={categoryData}
                            columns={[
                                { key: 'category', label: 'Category' },
                                { key: 'units_sold', label: 'Units Sold', format: formatNumber },
                                { key: 'net_revenue', label: 'Net Revenue', format: formatCurrency },
                                { key: 'asp', label: 'ASP', format: (v: number) => `₹${v.toFixed(0)}` },
                                { key: 'sales_share', label: 'Share', format: formatPct, tooltip: "Share of Total Units Sold" },
                            ]}
                        />
                    )}

                    {activeTab === 'season' && (
                        <BreakdownTable
                            title="Sales by Season"
                            data={seasonData}
                            columns={[
                                { key: 'season', label: 'Season' },
                                { key: 'units_sold', label: 'Units Sold', format: formatNumber },
                                { key: 'net_revenue', label: 'Net Revenue', format: formatCurrency },
                                { key: 'asp', label: 'ASP', format: (v: number) => `₹${v.toFixed(0)}` },
                                { key: 'sales_share', label: 'Share', format: formatPct },
                            ]}
                        />
                    )}

                    {activeTab === 'top-skus' && (
                        <BreakdownTable
                            title="Top 100 Performing SKUs"
                            data={topSkus}
                            columns={[
                                { key: 'sku_code', label: 'SKU' },
                                { key: 'category', label: 'Category' },
                                { key: 'season', label: 'Season' },
                                { key: 'units_sold', label: 'Units Sold', format: formatNumber },
                                { key: 'net_revenue', label: 'Net Revenue', format: formatCurrency },
                                { key: 'asp', label: 'ASP', format: (v: number) => `₹${v.toFixed(0)}` },
                            ]}
                        />
                    )}

                    {activeTab === 'low-velocity' && (
                        <BreakdownTable
                            title="Low Velocity SKUs (Velocity 30D < 0.1)"
                            data={lowVelocity}
                            columns={[
                                { key: 'sku_code', label: 'SKU' },
                                { key: 'category', label: 'Category' },
                                { key: 'units_30d', label: 'Units (30D)', format: formatNumber },
                                { key: 'velocity_30d', label: 'Velocity (Units/Day)', format: (v: number) => v.toFixed(3), tooltip: "Average daily sales over 30 days" },
                            ]}
                        />
                    )}
                </div>
            </div>
        </DashboardLayout>
    );
}

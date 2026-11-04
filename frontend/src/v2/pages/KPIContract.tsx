
import React, { useState, useEffect } from 'react';
import { Package, TrendingUp, AlertTriangle, XCircle, Activity, Box, Filter, Download, ChevronDown, ChevronUp } from 'lucide-react';
import DashboardLayout from '../layouts/DashboardLayout';
import { PageHelper } from '../components/PageHelper';
import InfoTooltip from '../components/InfoTooltip';
import { formatCurrency, formatNumber } from '../utils/formatters';

interface KPIContractData {
    sku_count: number;
    total_stock: number;
    total_sold_lifetime: number;
    sell_through: number;
    median_dos_90: number;
    at_risk_units: number;
    dead_stock_units: number;
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

export default function KPIContract() {
    const [data, setData] = useState<KPIContractData | null>(null);
    const [categoryData, setCategoryData] = useState([]);
    const [seasonData, setSeasonData] = useState([]);
    const [dosBuckets, setDosBuckets] = useState([]);
    const [topAtRisk, setTopAtRisk] = useState([]);
    const [topDead, setTopDead] = useState([]);
    const [storeData, setStoreData] = useState([]);

    const [loading, setLoading] = useState(true);
    const [activeTab, setActiveTab] = useState('category');

    useEffect(() => {
        Promise.all([
            fetch('/api/kpi-contract').then(res => res.json()),
            fetch('/api/kpi-contract/breakdown/category').then(res => res.json()),
            fetch('/api/kpi-contract/breakdown/season').then(res => res.json()),
            fetch('/api/kpi-contract/breakdown/dos-buckets').then(res => res.json()),
            fetch('/api/kpi-contract/top/at-risk-skus').then(res => res.json()),
            fetch('/api/kpi-contract/top/dead-skus').then(res => res.json()),
            fetch('/api/kpi-contract/breakdown/store').then(res => res.json())
        ]).then(([kpi, cat, season, dos, risk, dead, store]) => {
            setData(kpi);
            setCategoryData(cat);
            setSeasonData(season);
            setDosBuckets(dos);
            setTopAtRisk(risk);
            setTopDead(dead);
            setStoreData(store);
            setLoading(false);
        }).catch(err => {
            console.error("Failed to fetch KPI Contract data:", err);
            setLoading(false);
        });
    }, []);

    if (loading) return <DashboardLayout><div className="p-8">Loading Canonical KPIs &amp; Diagnostics...</div></DashboardLayout>;

    const formatPct = (num: number) => `${((num || 0) * 100).toFixed(1)}%`;

    return (
        <DashboardLayout>
            <div className="space-y-6">
                <div>
                    <h1 className="text-2xl font-bold text-slate-900 dark:text-white">Inventory KPI</h1>
                    <p className="text-slate-500 dark:text-slate-400">Canonical view of inventory health and performance.</p>
                </div>

                <PageHelper
                    title="Inventory KPI & Health"
                    summary="The single source of truth for inventory health, eliminating grain mismatches by using common measures."
                    items={[
                        { icon: <Box className="w-4 h-4" />, label: "Canonical Inventory", description: "Aggregated at Site/SKU level. Stock = Received − Sold." },
                        { icon: <TrendingUp className="w-4 h-4" />, label: "30-Day Sales", description: "Rolling 30-day window, gross sales (excluding returns), used for velocity calculations." },
                    ]}
                />

                <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                    <KPICard
                        label="SKU Count"
                        value={formatNumber(data?.sku_count)}
                        subtext="Active in Latest Snapshot"
                        icon={Package}
                        color="text-blue-600"
                        tooltip={{ title: "SKU Count", description: "Count of unique barcodes with > 0 stock in the latest snapshot." }}
                    />
                    <KPICard
                        label="Total Stock Units"
                        value={formatNumber(data?.total_stock)}
                        subtext="Current On-Hand"
                        icon={Box}
                        color="text-indigo-600"
                        tooltip={{ title: "Total Stock", description: "Sum of quantity from latest snapshot." }}
                    />
                    <KPICard
                        label="Sell-Through %"
                        value={`${((data?.sell_through || 0) * 100).toFixed(1)}%`}
                        subtext="Lifetime Ratio"
                        icon={TrendingUp}
                        color="text-emerald-600"
                        tooltip={{ title: "Sell-Through", description: "Total Sold / (Total Sold + Current Stock)." }}
                    />
                    <KPICard
                        label="DOS (30D)"
                        value={data?.median_dos_90.toFixed(1)}
                        subtext="Days of Supply"
                        icon={Activity}
                        color="text-violet-600"
                        tooltip={{ title: "Days of Supply", description: "Median Days of Supply based on last 30 days sales velocity." }}
                    />
                    <KPICard
                        label="At Risk Units"
                        value={formatNumber(data?.at_risk_units)}
                        subtext="Zero Sales or > 180 DOS"
                        icon={AlertTriangle}
                        color="text-amber-600"
                        tooltip={{ title: "At Risk", description: "Stock with 0 sales in 30 days OR DOS > 90." }}
                    />
                    <KPICard
                        label="Dead Stock Units"
                        value={formatNumber(data?.dead_stock_units)}
                        subtext="Zero Sales in 90 Days"
                        icon={XCircle}
                        color="text-rose-600"
                        tooltip={{ title: "Dead Stock", description: "Stock with absolutely 0 sales in the last 30 days." }}
                    />
                </div>

                <div className="border-t border-slate-200 dark:border-slate-800 pt-6">
                    <h2 className="text-xl font-bold text-slate-900 dark:text-white mb-4">Diagnostics</h2>

                    <div className="flex gap-2 mb-6 overflow-x-auto pb-2">
                        {['category', 'season', 'dos', 'top-risk', 'top-dead', 'store'].map(tab => (
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
                            title="Risk Breakdown by Category"
                            data={categoryData}
                            columns={[
                                { key: 'category', label: 'Category' },
                                { key: 'sku_count', label: 'SKUs', format: formatNumber },
                                { key: 'stock_units', label: 'Stock', format: formatNumber },
                                { key: 'sell_through', label: 'Sell-Through', format: formatPct, tooltip: "Lifetime Ratio: Sold / (Sold + Stock)" },
                                { key: 'at_risk_units', label: 'At Risk Units', format: formatNumber, tooltip: "Stock > 0 AND (No Sales 30D OR DOS > 90)" },
                                { key: 'at_risk_pct', label: 'At Risk %', format: formatPct, tooltip: "At Risk Units / Total Stock" },
                            ]}
                        />
                    )}

                    {activeTab === 'season' && (
                        <BreakdownTable
                            title="Risk Breakdown by Season"
                            data={seasonData}
                            columns={[
                                { key: 'season', label: 'Season' },
                                { key: 'sku_count', label: 'SKUs', format: formatNumber },
                                { key: 'stock_units', label: 'Stock', format: formatNumber },
                                { key: 'sell_through', label: 'Sell-Through', format: formatPct, tooltip: "Lifetime Ratio: Sold / (Sold + Stock)" },
                                { key: 'at_risk_units', label: 'At Risk Units', format: formatNumber, tooltip: "Stock > 0 AND (No Sales 30D OR DOS > 90)" },
                                { key: 'at_risk_pct', label: 'At Risk %', format: formatPct, tooltip: "At Risk Units / Total Stock" },
                            ]}
                        />
                    )}

                    {activeTab === 'dos' && (
                        <BreakdownTable
                            title="DOS Distribution (30 Days)"
                            data={dosBuckets}
                            columns={[
                                { key: 'bucket', label: 'DOS Bucket' },
                                { key: 'sku_count', label: 'SKU Count', format: formatNumber },
                                { key: 'stock_units', label: 'Stock Units', format: formatNumber },
                                { key: 'stock_pct', label: '% of Total Stock', format: formatPct },
                            ]}
                        />
                    )}

                    {activeTab === 'top-risk' && (
                        <BreakdownTable
                            title="Top At-Risk SKUs (Priority: Dead > High DOS > High Stock)"
                            data={topAtRisk}
                            columns={[
                                { key: 'sku_code', label: 'SKU' },
                                { key: 'category', label: 'Category' },
                                { key: 'season', label: 'Season' },
                                { key: 'stock', label: 'Stock', format: formatNumber },
                                { key: 'sales_90', label: 'Sales (30D)', format: formatNumber },
                                { key: 'dos_90', label: 'DOS', format: (v: number) => v === 9999 ? '∞' : v.toFixed(1), tooltip: "Stock / (Sales30 / 30)" },
                                { key: 'is_dead', label: 'Dead?', format: (v: number) => v === 1 ? 'Yes' : 'No', tooltip: "No sales in 30 days" },
                                { key: 'stock_value', label: 'Value', format: formatCurrency, tooltip: "Stock * MRP" },
                            ]}
                        />
                    )}

                    {activeTab === 'top-dead' && (
                        <BreakdownTable
                            title="Top Dead Stock SKUs (Zero Sales in 30 Days)"
                            data={topDead}
                            columns={[
                                { key: 'sku_code', label: 'SKU' },
                                { key: 'category', label: 'Category' },
                                { key: 'season', label: 'Season' },
                                { key: 'stock', label: 'Stock', format: formatNumber },
                                { key: 'stock_value', label: 'Value', format: formatCurrency, tooltip: "Stock * MRP" },
                            ]}
                        />
                    )}

                    {activeTab === 'store' && (
                        <BreakdownTable
                            title="Risk Breakdown by Store"
                            data={storeData}
                            columns={[
                                { key: 'site_code', label: 'Site Code' },
                                { key: 'store_name', label: 'Store Name' },
                                { key: 'stock_units', label: 'Total Stock', format: formatNumber },
                                { key: 'at_risk_units', label: 'At Risk Stock', format: formatNumber, tooltip: "Inventory flagged as At Risk (National SKU Level)" },
                                { key: 'dead_units', label: 'Dead Stock', format: formatNumber, tooltip: "Inventory flagged as Dead Stock (National SKU Level)" },
                                { key: 'sell_through', label: 'Sell-Through', format: formatPct, tooltip: "Store Sales / (Store Sales + Store Stock)" },
                            ]}
                        />
                    )}
                </div>
            </div>
        </DashboardLayout>
    );
}

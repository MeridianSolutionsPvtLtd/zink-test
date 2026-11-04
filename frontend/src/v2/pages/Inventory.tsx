import React, { useEffect, useState } from 'react';
import DashboardLayout from '../layouts/DashboardLayout';
import { useSeason } from '../contexts/SeasonContext';
import { Package, TrendingUp, TrendingDown, AlertTriangle, Filter, ArrowUpDown, ArrowUp, ArrowDown, Layers } from 'lucide-react';
import { InfoTooltip } from '../components/InfoTooltip';
import { PageHelper } from '../components/PageHelper';
import { formatCurrency, formatNumber } from '../utils/formatters';

import { API_URL } from '../../config';


const Inventory = () => {
    const [data, setData] = useState<any>(null);
    const [loading, setLoading] = useState(true);
    const { season, sku, category } = useSeason();
    const [sortConfig, setSortConfig] = useState<{ key: string, direction: 'asc' | 'desc' }>({ key: 'stockValue', direction: 'desc' });

    useEffect(() => {
        setLoading(true);
        fetch(`${API_URL}/inventory?season=${encodeURIComponent(season)}&sku=${encodeURIComponent(sku)}&category=${encodeURIComponent(category)}&days=30`)
            .then(res => res.json())
            .then(data => {
                setData(data);
                setLoading(false);
            })
            .catch(err => {
                console.error("Failed to fetch inventory", err);
                setLoading(false);
            });
    }, [season, sku, category]);



    const handleSort = (key: string) => {
        let direction: 'asc' | 'desc' = 'desc';
        if (sortConfig.key === key && sortConfig.direction === 'desc') {
            direction = 'asc';
        }
        setSortConfig({ key, direction });
    };

    const sortedCategories = React.useMemo(() => {
        if (!data?.categories) return [];
        return [...data.categories].sort((a, b) => {
            if (a[sortConfig.key] < b[sortConfig.key]) {
                return sortConfig.direction === 'asc' ? -1 : 1;
            }
            if (a[sortConfig.key] > b[sortConfig.key]) {
                return sortConfig.direction === 'asc' ? 1 : -1;
            }
            return 0;
        });
    }, [data, sortConfig]);

    const SortIcon = ({ column }: { column: string }) => {
        if (sortConfig.key !== column) return <ArrowUpDown className="w-3 h-3 text-slate-400 ml-1 inline" />;
        return sortConfig.direction === 'asc' ?
            <ArrowUp className="w-3 h-3 text-emerald-400 ml-1 inline" /> :
            <ArrowDown className="w-3 h-3 text-emerald-400 ml-1 inline" />;
    };

    if (loading) {
        return (
            <DashboardLayout>
                <div className="flex items-center justify-center h-full text-slate-500">Loading inventory data...</div>
            </DashboardLayout>
        );
    }

    const { summary } = data || {};

    return (
        <DashboardLayout>
            <div className="space-y-6">
                {/* Header */}
                <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
                    <div>
                        <h1 className="text-2xl font-bold text-slate-900 dark:text-white flex items-center gap-2">
                            <Package className="w-6 h-6 text-emerald-500 dark:text-emerald-400" />
                            Inventory Health- Last Month
                        </h1>
                        <p className="text-slate-500 dark:text-slate-400">Real-time stock levels, health, and movement</p>
                    </div>
                </div>

                {/* Page Helper */}
                <PageHelper
                    title="How Inventory Health works"
                    summary="Tracks stock levels, movement velocity, and supply risk across all SKUs and product categories. Use the season filter to drill down by business period."

                    dataSource="Enterprise Data Records"
                    refreshNote="Recalculated on each page load from latest transaction data"
                    items={[
                        {
                            icon: <Package className="w-4 h-4" />,
                            label: 'Total SKUs',
                            description: 'Count of distinct SKUs where net stock (Qty_Received − Qty_Sold) > 0, across all stores in the selected region.'
                        },
                        {
                            icon: <TrendingUp className="w-4 h-4" />,
                            label: 'Fast Moving',
                            description: 'SKUs with more than 50% sell-through. Formula: Units Sold ÷ (Units Sold + Stock) > 50%. Indicates items with strong market demand.'
                        },
                        {
                            icon: <TrendingDown className="w-4 h-4" />,
                            label: 'Slow Moving',
                            description: 'SKUs with sell-through between 20% and 30%. These items are moving slowly and may require promotional activities.'
                        },
                        {
                            icon: <AlertTriangle className="w-4 h-4" />,
                            label: 'At Risk',
                            description: 'SKUs with less than 20% sell-through. High risk of overstock or obsolescence.'
                        },
                        {
                            label: 'Sell-Through Calculation',
                            description: 'Units Sold ÷ (Units Sold + Current Stock) mapped against thresholds: Healthy (>50%), Warning (20–30%), Critical (<20%).'
                        },
                        {
                            label: 'Category Health',
                            description: 'Healthy: > 50% sell-through. Warning: 20–30% sell-through. Critical: < 20% sell-through.'
                        }
                    ]}
                />

                {/* KPI Cards */}
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
                    <div className="p-6 bg-white dark:bg-slate-900/50 border border-slate-200 dark:border-slate-800 rounded-xl flex items-center gap-4">
                        <div className="p-3 bg-slate-100 dark:bg-slate-800 rounded-lg">
                            <Package className="w-6 h-6 text-slate-500 dark:text-slate-400" />
                        </div>
                        <div>
                            <div className="flex items-center gap-1">
                                <div className="text-sm text-slate-500 dark:text-slate-400">Total SKUs</div>
                                <InfoTooltip
                                    title="Total SKUs"
                                    description="Count of distinct SKUs where net stock (Qty_Received − Qty_Sold, all-time) is greater than zero."
                                    formula="DISTINCTCOUNT(SKU) WHERE (Qty_Received - Qty_Sold) > 0"
                                    source="Inventory Management System"
                                />
                            </div>
                            <div className="text-2xl font-bold text-slate-900 dark:text-white">
                                {formatNumber(summary?.totalSkus)}
                            </div>
                            <div className="text-xs text-slate-400 dark:text-slate-500 mt-1">All active items</div>
                        </div>
                    </div>
                    <div className="p-6 bg-white dark:bg-slate-900/50 border border-slate-200 dark:border-slate-800 rounded-xl flex items-center gap-4">
                        <div className="p-3 bg-emerald-500/10 rounded-lg">
                            <TrendingUp className="w-6 h-6 text-emerald-500 dark:text-emerald-400" />
                        </div>
                        <div>
                            <div className="flex items-center gap-1">
                                <div className="text-sm text-slate-500 dark:text-slate-400">Fast Moving</div>
                                <InfoTooltip
                                    title="Fast Moving SKUs"
                                    description="Count of SKUs with a sell-through rate exceeding 50%. These items are the primary revenue drivers."
                                    formula="Sell-through > 50%"
                                    source="Movement Logs"
                                />
                            </div>
                            <div className="text-2xl font-bold text-slate-900 dark:text-white">
                                {formatNumber(summary?.fastMovingSkus)}
                            </div>
                             <div className="text-xs text-emerald-600/70 dark:text-emerald-500/50 mt-1">&gt; 50% sell-through</div>
                        </div>
                    </div>
                    <div className="p-6 bg-white dark:bg-slate-900/50 border border-slate-200 dark:border-slate-800 rounded-xl flex items-center gap-4">
                        <div className="p-3 bg-amber-500/10 rounded-lg">
                            <TrendingDown className="w-6 h-6 text-amber-500 dark:text-amber-400" />
                        </div>
                        <div>
                            <div className="flex items-center gap-1">
                                <div className="text-sm text-slate-500 dark:text-slate-400">Slow Moving</div>
                                <InfoTooltip
                                    title="Slow Moving SKUs"
                                    description="Count of SKUs whose sell-through rate is between 20% and 30%. These items show moderate to low movement speed."
                                    formula="Sell-through 20–30%"
                                    source="Distribution Records"
                                />
                            </div>
                            <div className="text-2xl font-bold text-slate-900 dark:text-white">
                                {formatNumber(summary?.slowMovingSkus)}
                            </div>
                            <div className="text-xs text-amber-600/70 dark:text-amber-500/50 mt-1">20–30% sell-through</div>
                        </div>
                    </div>
                    <div className="p-6 bg-white dark:bg-slate-900/50 border border-slate-200 dark:border-slate-800 rounded-xl flex items-center gap-4">
                        <div className="p-3 bg-rose-500/10 rounded-lg">
                            <AlertTriangle className="w-6 h-6 text-rose-500 dark:text-rose-400" />
                        </div>
                        <div>
                            <div className="flex items-center gap-1">
                                <div className="text-sm text-slate-500 dark:text-slate-400">At Risk</div>
                                <InfoTooltip
                                    title="At-Risk SKUs"
                                    description="Count of SKUs with less than 20% sell-through. These items are at high risk of becoming dead stock."
                                    formula="Sell-through < 20%"
                                    source="Aging Risk Analysis"
                                />
                            </div>
                            <div className="text-2xl font-bold text-slate-900 dark:text-white">
                                {formatNumber(summary?.atRiskSkus)}
                            </div>
                             <div className="text-xs text-rose-600/70 dark:text-rose-500/50 mt-1">&lt; 20% sell-through</div>
                        </div>
                    </div>
                </div>

                {/* Category Table */}
                <div className="bg-white dark:bg-slate-900/50 border border-slate-200 dark:border-slate-800 rounded-xl overflow-hidden">
                    <div className="p-6 border-b border-slate-100 dark:border-slate-800">
                        <h3 className="text-lg font-semibold text-slate-900 dark:text-white">Category Performance</h3>
                        <p className="text-sm text-slate-500 dark:text-slate-400 mt-1">Stock and sell-through by product category. Click column headers to sort.</p>
                    </div>
                    <div className="overflow-x-auto">
                        <table className="w-full text-sm text-left">
                            <thead className="text-xs text-slate-500 uppercase bg-slate-50 dark:bg-slate-800/50 border-b border-slate-100 dark:border-slate-800">
                                <tr>
                                    <th className="px-6 py-4 font-medium cursor-pointer hover:text-slate-700 dark:hover:text-slate-300" onClick={() => handleSort('category')}>
                                        Category <SortIcon column="category" />
                                    </th>
                                    <th className="px-6 py-4 font-medium cursor-pointer text-left hover:text-slate-700 dark:hover:text-slate-300" onClick={() => handleSort('sku')}>
                                        SKU <SortIcon column="sku" />
                                    </th>
                                    <th className="px-6 py-4 font-medium text-right cursor-pointer hover:text-slate-700 dark:hover:text-slate-300" onClick={() => handleSort('stockUnits')}>
                                        Stock <SortIcon column="stockUnits" />
                                    </th>


                                    <th className="px-6 py-4 font-medium text-right cursor-pointer hover:text-slate-700 dark:hover:text-slate-300" onClick={() => handleSort('sellThrough')}>
                                        Sell-Through <SortIcon column="sellThrough" />
                                    </th>
                                    <th className="px-6 py-4 font-medium text-center cursor-pointer hover:text-slate-700 dark:hover:text-slate-300" onClick={() => handleSort('health')}>
                                        Health <SortIcon column="health" />
                                    </th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                                {sortedCategories.map((cat: any) => (
                                    <tr key={`${cat.category}-${cat.sku}`} className="hover:bg-slate-50 dark:hover:bg-slate-800/30 transition-colors">
                                        <td className="px-6 py-4 font-medium text-slate-900 dark:text-white">{cat.category}</td>
                                        <td className="px-6 py-4 font-mono text-xs text-slate-500 dark:text-slate-400">{cat.sku}</td>
                                        <td className="px-6 py-4 text-right text-slate-700 dark:text-slate-300">
                                            {formatNumber(cat.stockUnits)}
                                        </td>


                                        <td className="px-6 py-4 text-right">
                                            <div className="flex items-center justify-end gap-2">
                                                <div className="w-16 h-1 bg-slate-200 dark:bg-slate-700 rounded-full overflow-hidden">
                                                    <div
                                                        className={`h-full rounded-full ${cat.sellThrough >= 70 ? 'bg-emerald-500' :
                                                            cat.sellThrough >= 50 ? 'bg-amber-500' : 'bg-rose-500'
                                                            }`}
                                                        style={{ width: `${Math.min(cat.sellThrough, 100)}%` }}
                                                    />
                                                </div>
                                                <span className="text-slate-700 dark:text-slate-300 w-8">{cat.sellThrough}%</span>
                                            </div>
                                        </td>
                                        <td className="px-6 py-4 text-center">
                                            <span className={`inline-flex px-2.5 py-1 text-xs font-medium rounded-full ${cat.health === 'Healthy' ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20' :
                                                cat.health === 'Critical' ? 'bg-rose-500/10 text-rose-600 dark:text-rose-400 border border-rose-500/20' :
                                                    cat.health === 'Overstock' ? 'bg-purple-500/10 text-purple-600 dark:text-purple-400 border border-purple-500/20' :
                                                        'bg-slate-500/10 text-slate-600 dark:text-slate-400 border border-slate-500/20'
                                                }`}>
                                                {cat.health}
                                            </span>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </DashboardLayout>
    );
};

export default Inventory;

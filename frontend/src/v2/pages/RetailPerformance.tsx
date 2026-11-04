
import React, { useEffect, useState } from 'react';
import DashboardLayout from '../layouts/DashboardLayout';
import { formatCurrency, formatNumber } from '../utils/formatters';
import { ArrowUpRight, ArrowDownRight, Activity, AlertTriangle, Package, Store, HelpCircle, Zap, Database } from 'lucide-react';
import PageHelper from '../components/PageHelper';
import InfoTooltip from '../components/InfoTooltip';
import { API_URL } from '../../config';

// Simple Card components to replace missing UI library
const Card = ({ children, className }: { children: React.ReactNode, className?: string }) => (
    <div className={`bg-white rounded-lg border border-gray-200 shadow-sm ${className || ''}`}>
        {children}
    </div>
);

const CardHeader = ({ children }: { children: React.ReactNode }) => (
    <div className="px-6 py-4 border-b border-gray-100 flex items-center gap-2">
        {children}
    </div>
);

const CardTitle = ({ children }: { children: React.ReactNode }) => (
    <h3 className="text-lg font-semibold text-gray-900 flex items-center gap-2">
        {children}
    </h3>
);

const CardContent = ({ children, className }: { children: React.ReactNode, className?: string }) => (
    <div className={`p-6 ${className || ''}`}>
        {children}
    </div>
);

interface SummaryMetrics {
    sell_through: number;
    inventory_turn: number;
    weeks_of_cover: number;
    active_sku_pct: number;
    revenue_per_unit_stock: number;
}

interface CategoryBalance {
    category: string;
    stock_units: number;
    sold_units: number;
    stock_share: number;
    sales_share: number;
    gap: number;
    sell_through: number;
}

interface RiskOverview {
    total_stock: number;
    at_risk_units: number;
    dead_stock_units: number;
    at_risk_pct: number;
    dead_stock_pct: number;
    dos_over_180_pct: number;
}

interface StorePerformance {
    store_name: string;
    stock_units: number;
    sold_units_lifetime: number;
    sell_through: number;
    at_risk_units: number;
    active_sku_pct: number;
}

interface ConcentrationRisk {
    top_10pct_share: number;
    top_20pct_share: number;
}

const RetailPerformance = () => {
    const [summary, setSummary] = useState<SummaryMetrics | null>(null);
    const [categories, setCategories] = useState<CategoryBalance[]>([]);
    const [risk, setRisk] = useState<RiskOverview | null>(null);
    const [stores, setStores] = useState<StorePerformance[]>([]);
    const [concentration, setConcentration] = useState<ConcentrationRisk | null>(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        const fetchData = async () => {
            try {
                const [sumRes, catRes, riskRes, storeRes, concRes] = await Promise.all([
                    fetch(`${API_URL}/retail-performance/summary`),
                    fetch(`${API_URL}/retail-performance/category-balance`),
                    fetch(`${API_URL}/retail-performance/risk-overview`),
                    fetch(`${API_URL}/retail-performance/store-performance`),
                    fetch(`${API_URL}/retail-performance/concentration`)
                ]);

                const sumData = await sumRes.json();
                const catData = await catRes.json();
                const riskData = await riskRes.json();
                const storeData = await storeRes.json();
                const concData = await concRes.json();

                setSummary(sumData);
                setCategories(catData?.categories || []);
                setRisk(riskData);
                setStores(storeData?.stores || []);
                setConcentration(concData);
            } catch (error) {
                console.error("Error fetching data:", error);
            } finally {
                setLoading(false);
            }
        };

        fetchData();
    }, []);

    if (loading) return (
        <DashboardLayout>
            <div className="p-8">Loading Retail Performance...</div>
        </DashboardLayout>
    );

    return (
        <DashboardLayout>
            <div className="space-y-8">
                <div className="flex flex-col gap-4">
                    <div className="flex justify-between items-center">
                        <h1 className="text-3xl font-bold tracking-tight text-gray-900 dark:text-gray-100">Retail Performance</h1>
                    </div>

                    <PageHelper
                        title="Retail Performance Overview"
                        summary="Executive dashboard integrating Sales and Inventory KPIs to provide a holistic view of retail health."
                        items={[
                            { label: "Exec Summary", description: "Key high-level metrics including Lifetime Sell-Through, Turns, and Coverage." },
                            { label: "Category Gaps", description: "Visualizes alignment between Supply (Stock Share) and Demand (Sales Share)." },
                            { label: "Inventory Risk", description: "Breakdown of inventory health including At-Risk (>90 DOS) and Dead Stock analysis." },
                        ]}
                    />
                </div>

                {/* Section 1: Executive Tiles */}
                <div className="grid grid-cols-1 md:grid-cols-5 gap-4">
                    <KPICard
                        title="Retail Sell-Through"
                        value={summary?.sell_through ? `${summary.sell_through}%` : '-'}
                        icon={<Activity className="h-4 w-4 text-blue-500" />}
                        tooltip="Ratio of units sold to total inventory available (Sold / (Sold + Stock)). Higher is generally better."
                        formula="Units Sold / (Units Sold + Stock Units)"
                    />
                    <KPICard
                        title="Inventory Turns"
                        value={summary?.inventory_turn?.toString() || '-'}
                        subtext="Annualized"
                        icon={<Package className="h-4 w-4 text-green-500" />}
                        tooltip="Number of times inventory is sold and replaced over a period. Annualized based on lifetime sales."
                        formula="Lifetime Sold Units / Current Stock Units"
                    />
                    <KPICard
                        title="Weeks of Cover"
                        value={summary?.weeks_of_cover?.toString() || '-'}
                        icon={<Activity className="h-4 w-4 text-purple-500" />}
                        tooltip="Number of weeks current stock will last based on recent sales velocity (30 days)."
                        formula="Current Stock / (Units Sold Last 30 Days / 4.3)"
                    />
                    <KPICard
                        title="Active SKU %"
                        value={summary?.active_sku_pct ? `${summary.active_sku_pct}%` : '-'}
                        icon={<Activity className="h-4 w-4 text-orange-500" />}
                        tooltip="Percentage of stock-holding SKUs that have recorded sales in the last 30 days."
                        formula="Count(SKUs with Stock > 0 AND Sales_30 > 0) / Count(SKUs with Stock > 0)"
                    />
                    <KPICard
                        title="Rev per Unit Stock"
                        value={formatCurrency(summary?.revenue_per_unit_stock)}
                        icon={<Activity className="h-4 w-4 text-teal-500" />}
                        tooltip="Average revenue generated per unit currently in stock. Indicates inventory efficiency."
                        formula="Total Net Revenue / Total Stock Units"
                    />
                </div>

                <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
                    {/* Section 2: Category Balance */}
                    <Card className="col-span-1 shadow-sm">
                        <CardHeader>
                            <CardTitle>
                                Demand vs Supply Gap
                                <InfoTooltip
                                    title="Category Alignment"
                                    description="Compares the share of total inventory (Supply) against the share of total sales (Demand) for each category. Large gaps indicate misalignment."
                                />
                            </CardTitle>
                        </CardHeader>
                        <CardContent>
                            <div className="overflow-x-auto max-h-[400px]">
                                <table className="w-full text-sm text-left">
                                    <thead className="text-xs text-gray-700 uppercase bg-gray-100 dark:bg-gray-800 sticky top-0 z-10">
                                        <tr>
                                            <th className="px-4 py-3">Category</th>
                                            <th className="px-4 py-3 text-right">
                                                Stock %
                                                <InfoTooltip title="Stock Share" description="% of total company inventory units held in this category." />
                                            </th>
                                            <th className="px-4 py-3 text-right">
                                                Sales %
                                                <InfoTooltip title="Sales Share" description="% of total company unit sales contributed by this category." />
                                            </th>
                                            <th className="px-4 py-3 text-right">
                                                Gap
                                                <InfoTooltip title="Supply-Demand Gap" description="Difference between Stock Share and Sales Share. Positive gap > 5% represents potential overstocking." />
                                            </th>
                                            <th className="px-4 py-3 text-right">Sell-Through</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {categories.map((cat, idx) => {
                                            const isHighGap = cat.gap > 0.05;
                                            return (
                                                <tr key={idx} className={`border-b hover:bg-gray-50 dark:hover:bg-gray-800 ${isHighGap ? 'bg-red-50 dark:bg-red-900/10' : ''}`}>
                                                    <td className="px-4 py-3 font-medium text-gray-900">{cat.category}</td>
                                                    <td className="px-4 py-3 text-right">{(cat.stock_share * 100).toFixed(1)}%</td>
                                                    <td className="px-4 py-3 text-right">{(cat.sales_share * 100).toFixed(1)}%</td>
                                                    <td className={`px-4 py-3 text-right font-bold ${cat.gap > 0 ? 'text-red-600' : 'text-green-600'}`}>
                                                        {(cat.gap * 100).toFixed(1)}%
                                                    </td>
                                                    <td className="px-4 py-3 text-right">{(cat.sell_through * 100).toFixed(1)}%</td>
                                                </tr>
                                            );
                                        })}
                                    </tbody>
                                </table>
                            </div>
                        </CardContent>
                    </Card>

                    {/* Section 3 & 5: Risk & Concentration */}
                    <div className="space-y-8">
                        <Card className="shadow-sm">
                            <CardHeader>
                                <CardTitle>
                                    Inventory Risk Overview
                                    <InfoTooltip
                                        title="Inventory Health"
                                        description="Summary of inventory quantity classified by risk factors based on sales velocity and age."
                                    />
                                </CardTitle>
                            </CardHeader>
                            <CardContent>
                                <div className="grid grid-cols-3 gap-4 text-center">
                                    <div className="p-4 bg-red-50 rounded-lg">
                                        <div className="text-2xl font-bold text-red-700">{risk?.at_risk_pct?.toFixed(1)}%</div>
                                        <div className="text-sm text-red-600">At Risk Inventory</div>
                                        <InfoTooltip title="At Risk" description="Inventory with low sales velocity or high Days of Supply (>90)." />
                                    </div>
                                    <div className="p-4 bg-gray-100 rounded-lg">
                                        <div className="text-2xl font-bold text-gray-700">{risk?.dead_stock_pct?.toFixed(1)}%</div>
                                        <div className="text-sm text-gray-600">Dead Stock</div>
                                        <InfoTooltip title="Dead Stock" description="Inventory with zero sales in the last 30 days." />
                                    </div>
                                    <div className="p-4 bg-orange-50 rounded-lg">
                                        <div className="text-2xl font-bold text-orange-700">{risk?.dos_over_180_pct?.toFixed(1)}%</div>
                                        <div className="text-sm text-orange-600">DOS &gt; 90 Days</div>
                                        <InfoTooltip title="High DOS" description="Inventory with estimated Days of Supply exceeding 90 days based on current sales rate (30d)." />
                                    </div>
                                </div>
                            </CardContent>
                        </Card>

                        <Card className="shadow-sm">
                            <CardHeader>
                                <CardTitle>
                                    Concentration Risk
                                    <InfoTooltip
                                        title="Revenue Concentration"
                                        description="Measures the dependency of total revenue on top-performing SKUs (Pareto Principle)."
                                    />
                                </CardTitle>
                            </CardHeader>
                            <CardContent>
                                <div className="space-y-4">
                                    <div className="flex justify-between items-center p-3 border rounded-lg">
                                        <span className="text-gray-600">Top 10% SKUs Revenue Share</span>
                                        <span className="text-xl font-bold text-indigo-600">{concentration?.top_10pct_share}%</span>
                                    </div>
                                    <div className="flex justify-between items-center p-3 border rounded-lg">
                                        <span className="text-gray-600">Top 20% SKUs Revenue Share</span>
                                        <span className="text-xl font-bold text-blue-600">{concentration?.top_20pct_share}%</span>
                                    </div>
                                </div>
                            </CardContent>
                        </Card>
                    </div>
                </div>

                {/* Section 4: Store Performance */}
                <Card className="shadow-sm">
                    <CardHeader>
                        <CardTitle>
                            Store Performance
                            <InfoTooltip
                                title="Store KPI Table"
                                description="Comprehensive performance metrics aggregated by store location."
                            />
                        </CardTitle>
                    </CardHeader>
                    <CardContent>
                        <div className="overflow-x-auto max-h-[500px]">
                            <table className="w-full text-sm text-left">
                                <thead className="text-xs text-gray-700 uppercase bg-gray-100 dark:bg-gray-800 sticky top-0 z-10">
                                    <tr>
                                        <th className="px-4 py-3">Store</th>
                                        <th className="px-4 py-3 text-right">Stock Units</th>
                                        <th className="px-4 py-3 text-right">Lifecycle Sales</th>
                                        <th className="px-4 py-3 text-right">Sell-Through</th>
                                        <th className="px-4 py-3 text-right">At Risk Units</th>
                                        <th className="px-4 py-3 text-right">Active SKU %</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {stores.map((store, idx) => (
                                        <tr key={idx} className="border-b hover:bg-gray-50 dark:hover:bg-gray-800">
                                            <td className="px-4 py-3 font-medium text-gray-900 flex items-center gap-2">
                                                <Store className="h-4 w-4 text-gray-400" />
                                                {store.store_name}
                                            </td>
                                            <td className="px-4 py-3 text-right">{formatNumber(store.stock_units)}</td>
                                            <td className="px-4 py-3 text-right">{formatNumber(store.sold_units_lifetime)}</td>
                                            <td className="px-4 py-3 text-right">{(store.sell_through * 100).toFixed(1)}%</td>
                                            <td className="px-4 py-3 text-right text-red-600">{formatNumber(store.at_risk_units)}</td>
                                            <td className="px-4 py-3 text-right">{(store.active_sku_pct * 100).toFixed(1)}%</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    </CardContent>
                </Card>
            </div>
        </DashboardLayout>
    );
};

const KPICard = ({ title, value, subtext, icon, tooltip, formula }: { title: string, value: string, subtext?: string, icon?: React.ReactNode, tooltip?: string, formula?: string }) => (
    <Card className="shadow-sm border-l-4 border-l-blue-500">
        <CardContent className="p-4 flex flex-col justify-between h-full">
            <div className="flex justify-between items-start">
                <p className="text-sm font-medium text-gray-500 flex items-center gap-1">
                    {title}
                    {tooltip && <InfoTooltip title={title} description={tooltip} formula={formula} />}
                </p>
                {icon}
            </div>
            <div className="mt-2">
                <h3 className="text-2xl font-bold text-gray-900">{value}</h3>
                {subtext && <p className="text-xs text-gray-400 mt-1">{subtext}</p>}
            </div>
        </CardContent>
    </Card>
);

export default RetailPerformance;

import React, { useEffect, useState } from 'react';
import { TrendingUp, Package, Percent, AlertTriangle } from 'lucide-react';
import { InfoTooltip } from './InfoTooltip';
import { formatCurrency } from '../utils/formatters';

import { API_URL } from '../../config';

interface MetricCardProps {
    title: string;
    value: string;
    trend?: number;
    trendLabel?: string;
    icon: React.ReactNode;
    iconBgColor: string;
    iconColor: string;
    tooltip: { description: string; formula?: string; source?: string };
    key?: number | string;
}

const MetricCard = ({ title, value, icon, iconBgColor, iconColor, tooltip }: MetricCardProps) => {

    return (
        <div className="bg-white dark:bg-slate-900/50 p-6 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm relative group hover:shadow-md transition-shadow">
            <div className="flex justify-between items-start mb-4">
                <div className="flex items-center gap-1">
                    <h3 className="text-slate-500 dark:text-slate-400 font-medium text-sm">{title}</h3>
                    <InfoTooltip title={title} {...tooltip} />
                </div>
                <div className={`p-2 rounded-lg ${iconBgColor} ${iconColor}`}>
                    {icon}
                </div>
            </div>

            <div className="flex items-baseline gap-1 mb-2">
                <span className="text-3xl font-bold text-slate-900 dark:text-white">{value}</span>
            </div>


        </div>
    );
};


const MetricCards = ({ data }: { data?: any }) => {
    const [summary, setSummary] = useState<{
        sell_through_pct?: number;
        inventory_units?: number;
        avg_discount_pct?: number;
        at_risk_skus?: number;
        units_sold?: number;
        sell_through_trend?: number;
        inventory_trend?: number;
        discount_trend?: number;
        at_risk_trend?: number;
        revenue?: number;
    } | null>(null);

    useEffect(() => {
        if (data) {
            setSummary(data);
        }
    }, [data]);

    // Data from analytics engine
    const sellThrough = summary?.sell_through_pct ?? 0;
    const avgDiscount = summary?.avg_discount_pct ?? 0;
    const unitsSold = summary?.units_sold ?? 0;
    const sellTrend = summary?.sell_through_trend ?? 0;
    const invTrend = summary?.inventory_trend ?? 0;
    const discountTrend = summary?.discount_trend ?? 0;
    // Inventory value: API returns revenue; use as proxy (or inventory_units * est. cost)
    const invDisplayValue = summary?.revenue ?? 0;

    const metrics = [
        {
            title: 'Sell-Through Rate',
            value: `${sellThrough}%`,
            trend: sellTrend,
            trendLabel: 'vs last period',
            icon: <TrendingUp size={20} />,
            iconBgColor: 'bg-emerald-50 dark:bg-emerald-500/10',
            iconColor: 'text-emerald-600 dark:text-emerald-400',
            tooltip: {
                description: 'Percentage of received stock that has been sold across all stores — all-time. Higher is better.',
                formula: 'Sold Units ÷ (Sold Units + Current Stock)',
                source: 'Movement Logs'
            }
        },
        {
            title: 'Revenue',
            value: formatCurrency(invDisplayValue),
            trend: invTrend,
            trendLabel: 'vs last period',
            icon: <Package size={20} />,
            iconBgColor: 'bg-slate-100 dark:bg-slate-800',
            iconColor: 'text-slate-600 dark:text-slate-400',
            tooltip: {
                description: 'Total net sales revenue across all stores and all time.',
                formula: 'Σ (Sold Quantity × Selling Price)',
                source: 'Sales Transactions'
            }
        },
        {
            title: 'Avg Discount Rate',
            value: `${avgDiscount}%`,
            trend: discountTrend,
            trendLabel: 'vs last period',
            icon: <Percent size={20} />,
            iconBgColor: 'bg-blue-50 dark:bg-blue-500/10',
            iconColor: 'text-blue-600 dark:text-blue-400',
            tooltip: {
                description: 'Average markdown applied across all sales. A lower value means fewer markdowns are needed — a sign of healthy demand.',
                formula: 'Average( (MRP - Selling Price) ÷ MRP )',
                source: 'Price Adjustment Records'
            }
        },
        {
            title: 'Total Sold Qty',
            value: new Intl.NumberFormat('en-IN').format(unitsSold),
            icon: <Package size={20} />,
            iconBgColor: 'bg-amber-50 dark:bg-amber-500/10',
            iconColor: 'text-amber-600 dark:text-amber-400',
            tooltip: {
                description: 'Total number of units sold across all stores within the selected time period.',
                formula: 'Σ (Qty Sold) from Sales Detail records.',
                source: 'Analytics Engine / Transactional Data'
            }
        }
    ];

    return (
        <div>
            {summary && (
                <p className="mb-4 text-xs text-slate-500 dark:text-slate-400">
                    ✓ Analytics Engine Sync Complete
                </p>
            )}
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
                {metrics.map((metric, index) => (
                    <MetricCard key={index} {...metric} />
                ))}
            </div>
        </div>
    );
};

export default MetricCards;

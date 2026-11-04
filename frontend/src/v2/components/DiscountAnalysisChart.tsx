import React, { useEffect, useState } from 'react';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell, LabelList } from 'recharts';
import { Percent } from 'lucide-react';

import { API_URL } from '../../config';

const DiscountAnalysisChart = ({ data: initialData }: { data?: any }) => {
    const [data, setData] = useState<Array<{ category: string; discountRate: number }>>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        if (initialData) {
            const cats = (initialData.categories || []).map((c: any) => ({
                category: c.category,
                discountRate: c.discountRate ?? 0,
            }));
            setData(cats);
            setLoading(false);
        }
    }, [initialData]);

    if (loading) return <div className="h-64 flex items-center justify-center text-slate-500">Loading analysis...</div>;
    if (error || data.length === 0) return <div className="h-64 flex items-center justify-center text-slate-400">{error || 'No data'}</div>;

    const CustomTooltip = ({ active, payload, label }: any) => {
        if (active && payload && payload.length) {
            return (
                <div className="bg-slate-800 border border-slate-700 p-3 rounded-lg shadow-xl text-sm">
                    <p className="font-semibold text-white mb-1">{label}</p>
                    <div className="flex items-center gap-2">
                        <div className="w-2 h-2 rounded-full bg-amber-400" />
                        <span className="text-slate-300">Discount Rate:</span>
                        <span className="font-medium text-amber-400">{payload[0].value}%</span>
                    </div>
                </div>
            );
        }
        return null;
    };

    // Color scale: higher discount = more amber/red
    const getBarColor = (rate: number) => {
        if (rate >= 30) return '#ef4444';   // red
        if (rate >= 20) return '#f59e0b';   // amber
        return '#10b981';                    // green
    };

    const chartHeight = Math.max(240, data.length * 36);

    return (
        <div className="p-6 bg-white dark:bg-slate-900/50 border border-slate-200 dark:border-slate-800 rounded-xl">
            <div className="flex items-center justify-between mb-4">
                <div>
                    <h3 className="text-lg font-semibold text-slate-900 dark:text-white">Discount Rate by Department</h3>
                    <p className="text-sm text-slate-500 dark:text-slate-400">
                        Average discount % per department
                        <span className="ml-2 text-emerald-600 dark:text-emerald-400 font-medium">— Data: Discount analysis based on net sales</span>
                    </p>
                </div>
                <div className="p-2 bg-amber-500/10 rounded-lg">
                    <Percent className="w-5 h-5 text-amber-400" />
                </div>
            </div>

            {/* Legend */}
            <div className="flex items-center gap-4 mb-3 text-xs text-slate-500 dark:text-slate-400">
                <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-emerald-500 inline-block" /> &lt; 20%</span>
                <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-amber-500 inline-block" /> 20–30%</span>
                <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-red-500 inline-block" /> &gt; 30%</span>
            </div>

            {/* Scrollable chart container — fixed height to match Sell-Through chart */}
            <div className="overflow-y-auto" style={{ height: '300px' }}>
                <div style={{ height: `${chartHeight}px`, minHeight: '300px' }}>
                    <ResponsiveContainer width="100%" height="100%">
                        <BarChart
                            layout="vertical"
                            data={data}
                            margin={{ top: 0, right: 50, left: 10, bottom: 0 }}
                            barSize={18}
                        >
                            <CartesianGrid strokeDasharray="3 3" horizontal={false} vertical={true} stroke="#334155" opacity={0.3} />
                            <XAxis
                                type="number"
                                domain={[0, 'auto']}
                                tickFormatter={(v) => `${v}%`}
                                tick={{ fill: '#94a3b8', fontSize: 11 }}
                                axisLine={false}
                                tickLine={false}
                            />
                            <YAxis
                                type="category"
                                dataKey="category"
                                tick={{ fill: '#94a3b8', fontSize: 11 }}
                                width={90}
                                tickLine={false}
                                axisLine={false}
                            />
                            <Tooltip content={<CustomTooltip />} cursor={{ fill: '#334155', opacity: 0.15 }} />
                            <Bar dataKey="discountRate" radius={[0, 4, 4, 0]}>
                                {data.map((entry, index) => (
                                    <Cell key={`cell-${index}`} fill={getBarColor(entry.discountRate)} />
                                ))}
                                <LabelList
                                    dataKey="discountRate"
                                    position="right"
                                    formatter={(v: number) => `${v}%`}
                                    style={{ fill: '#94a3b8', fontSize: 11 }}
                                />
                            </Bar>
                        </BarChart>
                    </ResponsiveContainer>
                </div>
            </div>
        </div>
    );
};

export default DiscountAnalysisChart;

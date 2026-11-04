import React, { useEffect, useState } from 'react';
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';
import { Activity } from 'lucide-react';

import { API_URL } from '../../config';

const SellThroughTrendChart = ({ data: initialData }: { data?: any }) => {
    const [data, setData] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        if (initialData) {
            setData(initialData.months || []);
            setLoading(false);
        }
    }, [initialData]);

    if (loading) return <div className="h-64 flex items-center justify-center text-slate-500">Loading trend...</div>;

    return (
        <div className="p-6 bg-white dark:bg-slate-900/50 border border-slate-200 dark:border-slate-800 rounded-xl">
            <div className="flex items-center justify-between mb-6">
                <div>
                    <h3 className="text-lg font-semibold text-slate-900 dark:text-white">Monthly sell-through</h3>
                    <p className="text-sm text-slate-500 dark:text-slate-400">Based on opening stock</p>
                </div>
                <div className="p-2 bg-emerald-500/10 rounded-lg">
                    <Activity className="w-5 h-5 text-emerald-400" />
                </div>
            </div>

            <div className="h-[250px] w-full">
                <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={data} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                        <defs>
                            <linearGradient id="colorSt" x1="0" y1="0" x2="0" y2="1">
                                <stop offset="5%" stopColor="#10b981" stopOpacity={0.3} />
                                <stop offset="95%" stopColor="#10b981" stopOpacity={0} />
                            </linearGradient>
                        </defs>
                        <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#334155" />
                        <XAxis
                            dataKey="month"
                            axisLine={false}
                            tickLine={false}
                            tick={{ fill: '#94a3b8', fontSize: 12 }}
                            tickFormatter={(str) => {
                                if (!str) return '';
                                const parts = str.split('-');
                                if (parts.length === 2) {
                                    return `${parts[0]} ${parts[1].substring(2)}`;
                                }
                                return str;
                            }}
                        />
                        <YAxis
                            axisLine={false}
                            tickLine={false}
                            tick={{ fill: '#94a3b8', fontSize: 12 }}
                            domain={[0, 100]}
                            tickFormatter={(val) => `${val}%`}
                        />
                        <Tooltip
                            contentStyle={{ backgroundColor: '#1e293b', borderColor: '#334155', color: '#f8fafc' }}
                            itemStyle={{ color: '#10b981' }}
                            formatter={(val: any) => [`${val}%`, 'Sell-through']}
                            labelFormatter={(label) => label}
                        />
                        <Area
                            type="monotone"
                            dataKey="sellThroughPct"
                            stroke="#10b981"
                            strokeWidth={3}
                            fillOpacity={1}
                            fill="url(#colorSt)"
                        />
                    </AreaChart>
                </ResponsiveContainer>
            </div>
        </div>
    );
};

export default SellThroughTrendChart;

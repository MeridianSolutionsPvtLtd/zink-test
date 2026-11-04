import React, { useEffect, useState, useMemo } from 'react';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from 'recharts';
import { AlertCircle } from 'lucide-react';

import { API_URL } from '../../config';

const ActiveAlerts = ({ season = "All Seasons" }: { season?: string }) => {
    const [alerts, setAlerts] = useState<any[]>([]);

    useEffect(() => {
        // Fetch low stock alerts (top 5) with season filter
        fetch(`${API_URL}/alerts?limit=5&season=${encodeURIComponent(season)}`)
            .then(res => res.json())
            .then(data => setAlerts(data.alerts || []))
            .catch(console.error);
    }, [season]);


    // Transform alerts into chart data: Site Name vs Units Remaining
    const chartData = useMemo(() => {
        return alerts
            .filter(a => a.type === 'Low Stock' && a.metrics?.[0])
            .map(alert => {
                // description format: "Site FR-1234 — 2 units remaining"
                const desc = alert.description || '';
                const parts = desc.split('—');
                let siteName = parts[0]?.replace('Site ', '').trim() || 'Unknown Site';
                // Shorten very long site names for the Y-axis
                if (siteName.length > 40) {
                    siteName = siteName.substring(0, 37) + '...';
                }
                
                const units = parseInt(alert.metrics[0].value, 10) || 0;
                return {
                    name: siteName,
                    fullName: parts[0]?.replace('Site ', '').trim(),
                    units: units
                };
            });
    }, [alerts]);

    return (
        <div className="p-6 bg-white dark:bg-slate-900/50 border border-slate-200 dark:border-slate-800 rounded-xl h-[500px] flex flex-col">
            <div className="flex items-center justify-between mb-2">
                <div>
                    <h3 className="text-lg font-semibold text-slate-900 dark:text-white flex items-center gap-2">
                        <AlertCircle className="w-5 h-5 text-rose-500" />
                        Active Alerts
                    </h3>
                    <p className="text-sm text-slate-500 dark:text-slate-400">Lowest Stock Sites (Units Remaining)</p>
                </div>
            </div>

            <div className="flex-1 mt-4 min-h-[250px] w-full">
                {chartData.length === 0 ? (
                    <div className="flex items-center justify-center h-full text-slate-400 dark:text-slate-500">
                        No active alerts
                    </div>
                ) : (
                    <ResponsiveContainer width="100%" height="100%">
                        <BarChart
                            data={chartData}
                            layout="vertical"
                            margin={{ top: 5, right: 30, left: 30, bottom: 5 }}
                        >
                            <XAxis 
                                type="number" 
                                stroke="#94a3b8" 
                                fontSize={12}
                                tickLine={false}
                                axisLine={false}
                            />
                            <YAxis 
                                dataKey="name" 
                                type="category" 
                                stroke="#94a3b8" 
                                fontSize={11}
                                tickLine={false}
                                axisLine={false}
                                width={180}
                            />
                            <Tooltip
                                cursor={{ fill: 'rgba(244, 63, 94, 0.05)' }}
                                contentStyle={{
                                    backgroundColor: 'rgba(15, 23, 42, 0.95)',
                                    border: '1px solid rgba(51, 65, 85, 0.5)',
                                    borderRadius: '8px',
                                    color: '#fff',
                                    fontSize: '12px',
                                    padding: '8px 12px',
                                    boxShadow: '0 10px 15px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -2px rgba(0, 0, 0, 0.05)'
                                }}
                                formatter={(value: number) => [`${value} units`, 'Remaining']}
                                labelFormatter={(label, payload) => {
                                    if (payload && payload[0] && payload[0].payload) {
                                        return payload[0].payload.fullName || label;
                                    }
                                    return label;
                                }}
                            />
                            <Bar 
                                dataKey="units" 
                                radius={[0, 4, 4, 0]}
                                barSize={24}
                                animationDuration={1000}
                            >
                                {chartData.map((entry, index) => (
                                    <Cell 
                                        key={`cell-${index}`} 
                                        fill={entry.units === 0 ? '#f43f5e' : '#fbbf24'} 
                                    />
                                ))}
                            </Bar>
                        </BarChart>
                    </ResponsiveContainer>
                )}
            </div>
        </div>
    );
};

export default ActiveAlerts;

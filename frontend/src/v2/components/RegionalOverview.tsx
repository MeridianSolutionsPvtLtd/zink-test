import React, { useEffect, useState } from 'react';

import { API_URL } from '../../config';

const RegionalOverview = ({ data: initialData }: { data?: any }) => {
    const [regions, setRegions] = useState<any[]>([]);
    const [avgPerf, setAvgPerf] = useState(0);

    useEffect(() => {
        if (initialData) {
            setRegions(initialData.regions || []);
            if (initialData.regions && initialData.regions.length > 0) {
                const total = initialData.regions.reduce((acc: number, r: any) => acc + r.performance, 0);
                setAvgPerf(Math.round(total / initialData.regions.length));
            }
            return;
        }
        // Real zone sales from Home breakdowns (legacy /dashboard/regions removed)
        fetch(`${API_URL}/executive-overview/breakdowns?year=2027`)
            .then(res => res.json())
            .then(data => {
                const zones = Array.isArray(data?.zone_sales) ? data.zone_sales : [];
                const mapped = zones.map((z: any) => ({
                    name: z.zone,
                    storeCount: Number(z.stores || 0),
                    revenue: Number(z.sales || 0),
                    units: Number(z.units || 0),
                    performance: typeof z.achievement === 'number' ? z.achievement : Number(z.growth || 0),
                }));
                setRegions(mapped);
                if (mapped.length > 0) {
                    const total = mapped.reduce((acc: number, r: any) => acc + (r.performance || 0), 0);
                    setAvgPerf(Math.round(total / mapped.length));
                }
            })
            .catch(console.error);
    }, [initialData]);

    const stateData = regions.filter((r: any) => r.name !== 'All Regions');
    const totalStoresCount = regions.find((r: any) => r.name === 'All Regions')?.storeCount
        || stateData.reduce((acc, r) => acc + (r.storeCount || 0), 0);

    return (
        <div className="p-6 bg-white dark:bg-slate-900/50 border border-slate-200 dark:border-slate-800 rounded-xl h-[500px] flex flex-col">
            <div className="mb-4">
                <h3 className="text-lg font-semibold text-slate-900 dark:text-white">Regional Overview</h3>
                <p className="text-sm text-slate-500 dark:text-slate-400">
                    {totalStoresCount} stores across {stateData.length} states
                </p>
            </div>

            <div className="flex-1 overflow-y-auto pr-2 custom-scrollbar space-y-3">
                {stateData.map((region: any) => (
                    <div key={region.name} className="flex items-center justify-between p-3 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-800/30 transition-colors border border-transparent hover:border-slate-200 dark:hover:border-slate-800">
                        <div className="flex items-center gap-3">
                            <div className={`w-2 h-2 rounded-full ${region.performance >= 75 ? 'bg-emerald-500' :
                                region.performance >= 60 ? 'bg-amber-500' : 'bg-rose-500'
                                }`} />
                            <div>
                                <div className="text-sm font-medium text-slate-900 dark:text-white">{region.name}</div>
                                <div className="text-xs text-slate-500">{region.storeCount} stores</div>
                            </div>
                        </div>

                        <div className="text-right">
                            <div className="text-sm font-bold text-slate-900 dark:text-white">{region.performance}%</div>
                            <div className="w-24 h-1 mt-1 bg-slate-200 dark:bg-slate-800 rounded-full overflow-hidden">
                                <div
                                    className={`h-full rounded-full ${region.performance >= 75 ? 'bg-emerald-500' :
                                        region.performance >= 60 ? 'bg-amber-500' : 'bg-rose-500'
                                        }`}
                                    style={{ width: `${region.performance}%` }}
                                />
                            </div>
                        </div>
                    </div>
                ))}
            </div>

            <style>{`
                .custom-scrollbar::-webkit-scrollbar {
                    width: 4px;
                }
                .custom-scrollbar::-webkit-scrollbar-track {
                    background: transparent;
                }
                .custom-scrollbar::-webkit-scrollbar-thumb {
                    background: #334155;
                    border-radius: 10px;
                }
                .custom-scrollbar::-webkit-scrollbar-thumb:hover {
                    background: #475569;
                }
            `}</style>
        </div>
    );
};

export default RegionalOverview;

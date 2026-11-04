import React, { useEffect, useState } from 'react';
import { ArrowUpRight, ArrowDownRight, MoreHorizontal, ChevronLeft, ChevronRight, ArrowUpDown, ArrowUp, ArrowDown } from 'lucide-react';
import { formatCurrency } from '../utils/formatters';

import { API_URL } from '../../config';

const StorePerformanceTable = ({ 
    season = "All Seasons",
    sku = "All SKUs",
    store = "All Stores",
    monthYear = "All Months"
}: { 
    season?: string;
    sku?: string;
    store?: string;
    monthYear?: string;
}) => {
    const [stores, setStores] = useState([]);
    const [loading, setLoading] = useState(true);
    const [page, setPage] = useState(1);
    const [total, setTotal] = useState(0);
    const [sortBy, setSortBy] = useState('revenue');
    const [sortOrder, setSortOrder] = useState('desc');
    const limit = 10;

    useEffect(() => {
        setLoading(true);
        const offset = (page - 1) * limit;
        const url = `${API_URL}/stores?limit=${limit}&offset=${offset}&sort_by=${sortBy}&sort_order=${sortOrder}&season=${encodeURIComponent(season)}&sku=${encodeURIComponent(sku)}&site=${encodeURIComponent(store)}&month_year=${encodeURIComponent(monthYear)}`;
        
        fetch(url)
            .then(res => res.json())
            .then(data => {
                setStores(data.stores || []);
                setTotal(data.total || 0);
                setLoading(false);
            })
            .catch(err => {
                console.error("Failed to fetch stores", err);
                setLoading(false);
            });
    }, [page, sortBy, sortOrder, season, sku, store, monthYear]);


    const handleSort = (column: string) => {
        if (sortBy === column) {
            setSortOrder(sortOrder === 'asc' ? 'desc' : 'asc');
        } else {
            setSortBy(column);
            setSortOrder('desc');
        }
        setPage(1); // Reset to first page on sort change
    };

    const SortIcon = ({ column }: { column: string }) => {
        if (sortBy !== column) return <ArrowUpDown className="w-3 h-3 text-slate-600 ml-1 inline" />;
        return sortOrder === 'asc' ?
            <ArrowUp className="w-3 h-3 text-emerald-400 ml-1 inline" /> :
            <ArrowDown className="w-3 h-3 text-emerald-400 ml-1 inline" />;
    };

    const totalPages = Math.ceil(total / limit);

    if (loading && page === 1) return <div className="h-64 flex items-center justify-center text-slate-500">Loading stores...</div>;

    return (
        <div className="p-6 bg-white dark:bg-slate-900/50 border border-slate-200 dark:border-slate-800 rounded-xl">
            <div className="flex items-center justify-between mb-6">
                <div>
                    <h3 className="text-lg font-semibold text-slate-900 dark:text-white">Store Performance</h3>
                    <p className="text-sm text-slate-500 dark:text-slate-400">Top performing and at-risk stores</p>
                </div>
                <div className="text-xs font-medium text-slate-500 dark:text-slate-400 bg-slate-100 dark:bg-slate-800 px-3 py-1 rounded-full">
                    Total: {total}
                </div>
            </div>

            <div className="overflow-x-auto">
                <table className="w-full text-sm text-left">
                    <thead className="text-xs text-slate-500 uppercase bg-slate-100 dark:bg-slate-800/50 border-b border-slate-200 dark:border-slate-800">
                        <tr>
                            <th className="px-4 py-3 font-medium cursor-pointer hover:text-slate-700 dark:hover:text-slate-300" onClick={() => handleSort('name')}>
                                Store <SortIcon column="name" />
                            </th>

                            <th className="px-4 py-3 font-medium cursor-pointer hover:text-slate-700 dark:hover:text-slate-300" onClick={() => handleSort('sellThrough')}>
                                Sell-Through <SortIcon column="sellThrough" />
                            </th>

                            <th className="px-4 py-3 font-medium text-right cursor-pointer hover:text-slate-700 dark:hover:text-slate-300" onClick={() => handleSort('discountRate')}>
                                Discount Rate <SortIcon column="discountRate" />
                            </th>
                            <th className="px-4 py-3 font-medium text-center cursor-pointer hover:text-slate-700 dark:hover:text-slate-300" onClick={() => handleSort('status')}>
                                Status <SortIcon column="status" />
                            </th>
                            <th className="px-4 py-3 font-medium text-right cursor-pointer hover:text-slate-700 dark:hover:text-slate-300" onClick={() => handleSort('trend')}>
                                Trend <SortIcon column="trend" />
                            </th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-200 dark:divide-slate-800">
                        {stores.map((store: any) => (
                            <tr key={store.id} className="hover:bg-slate-50 dark:hover:bg-slate-800/30 transition-colors">
                                <td className="px-4 py-3 font-medium text-slate-900 dark:text-white">{store.name}</td>

                                <td className="px-4 py-3">
                                    <div className="flex items-center gap-3">
                                        <div className="w-16 h-1.5 bg-slate-200 dark:bg-slate-700 rounded-full overflow-hidden">
                                            <div
                                                className={`h-full rounded-full ${store.sellThrough >= 70 ? 'bg-emerald-500' :
                                                    store.sellThrough >= 50 ? 'bg-amber-500' : 'bg-rose-500'
                                                    }`}
                                                style={{ width: `${Math.min(store.sellThrough, 100)}%` }}
                                            />
                                        </div>
                                        <span className="text-slate-700 dark:text-slate-300">{store.sellThrough}%</span>
                                    </div>
                                </td>

                                <td className="px-4 py-3 text-right text-emerald-600 dark:text-emerald-400">{store.discountRate}%</td>
                                <td className="px-4 py-3 text-center">
                                    <span className={`inline-flex px-2 py-0.5 text-xs font-medium rounded-full ${store.status === 'Optimal' ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400' :
                                        store.status === 'Critical' ? 'bg-rose-500/10 text-rose-600 dark:text-rose-400' :
                                            'bg-amber-500/10 text-amber-600 dark:text-amber-400'
                                        }`}>
                                        {store.status}
                                    </span>
                                </td>
                                <td className="px-4 py-3 text-right">
                                    <div className={`flex items-center justify-end gap-1 ${store.trend >= 0 ? 'text-emerald-500' : 'text-rose-500'
                                        }`}>
                                        {store.trend >= 0 ? <ArrowUpRight className="w-3 h-3" /> : <ArrowDownRight className="w-3 h-3" />}
                                        <span>{Math.abs(store.trend)}%</span>
                                    </div>
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>

            {/* Pagination Controls */}
            <div className="flex items-center justify-between mt-4 px-4">
                <div className="text-sm text-slate-500 dark:text-slate-400">
                    Showing <span className="font-medium text-slate-900 dark:text-white">{(page - 1) * limit + 1}</span> to <span className="font-medium text-slate-900 dark:text-white">{Math.min(page * limit, total)}</span> of <span className="font-medium text-slate-900 dark:text-white">{total}</span>
                </div>
                <div className="flex gap-2">
                    <button
                        onClick={() => setPage(p => Math.max(1, p - 1))}
                        disabled={page === 1}
                        className="p-2 rounded-lg bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400 disabled:opacity-50 hover:bg-slate-200 dark:hover:bg-slate-700 hover:text-slate-900 dark:hover:text-white transition-colors"
                    >
                        <ChevronLeft className="w-4 h-4" />
                    </button>
                    <button
                        onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                        disabled={page === totalPages}
                        className="p-2 rounded-lg bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400 disabled:opacity-50 hover:bg-slate-200 dark:hover:bg-slate-700 hover:text-slate-900 dark:hover:text-white transition-colors"
                    >
                        <ChevronRight className="w-4 h-4" />
                    </button>
                </div>
            </div>
        </div >
    );
};

export default StorePerformanceTable;

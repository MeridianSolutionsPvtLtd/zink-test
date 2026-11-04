import React, { useEffect, useState } from 'react';
import DashboardLayout from '../layouts/DashboardLayout';
import { useFilters } from '../contexts/SeasonContext';
import { Search, Filter, Download, ArrowUpDown, ArrowUp, ArrowDown, ChevronLeft, ChevronRight, Store as StoreIcon, TrendingUp, DollarSign, Activity, Layers } from 'lucide-react';
import { PageHelper } from '../components/PageHelper';

import { API_URL } from '../../config';




const Stores = () => {
    const [stores, setStores] = useState([]);
    const [loading, setLoading] = useState(true);
    const [page, setPage] = useState(1);
    const [total, setTotal] = useState(0);
    const [sortBy, setSortBy] = useState('revenue');
    const [sortOrder, setSortOrder] = useState('desc');
    const [search, setSearch] = useState('');
    const { season, sku, store, monthYear } = useFilters();
    const [error, setError] = useState<string | null>(null);


    const [isCapacityError, setIsCapacityError] = useState(false);
    const limit = 20;

    useEffect(() => {
        setLoading(true);
        const offset = (page - 1) * limit;
        const queryParams = new URLSearchParams({
            limit: limit.toString(),
            offset: offset.toString(),
            sort_by: sortBy,
            sort_order: sortOrder,
            search,
            season,
            sku,
            site: store,
            month_year: monthYear
        });


        fetch(`${API_URL}/stores?${queryParams}`)
            .then(res => res.json())
            .then(data => {
                if (data.is_capacity_error) {
                    setIsCapacityError(true);
                    setError(data.error || "Compute capacity exceeded.");
                    setStores([]);
                    setTotal(0);
                } else if (data.error) {
                    setError(data.error);
                    setStores([]);
                    setTotal(0);
                } else {
                    setStores(data.stores || []);
                    setTotal(data.total || 0);
                    setError(null);
                    setIsCapacityError(false);
                }
                setLoading(false);
            })
            .catch(err => {
                console.error("Failed to fetch stores", err);
                setError("Connection failed. Please ensure the backend is running.");
                setLoading(false);
            });
    }, [page, sortBy, sortOrder, search, season, sku, store, monthYear]);


    const handleSort = (column: string) => {
        if (sortBy === column) {
            setSortOrder(sortOrder === 'asc' ? 'desc' : 'asc');
        } else {
            setSortBy(column);
            setSortOrder('desc');
        }
        setPage(1);
    };

    const SortIcon = ({ column }: { column: string }) => {
        if (sortBy !== column) return <ArrowUpDown className="w-3 h-3 text-slate-400 ml-1 inline" />;
        return sortOrder === 'asc' ?
            <ArrowUp className="w-3 h-3 text-emerald-400 ml-1 inline" /> :
            <ArrowDown className="w-3 h-3 text-emerald-400 ml-1 inline" />;
    };

    const totalPages = Math.ceil(total / limit);

    return (
        <DashboardLayout>
            <div className="space-y-6">
                {/* Header */}
                <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
                    <div>
                        <h1 className="text-2xl font-bold text-slate-900 dark:text-white flex items-center gap-2">
                            <StoreIcon className="w-6 h-6 text-emerald-500 dark:text-emerald-400" />
                            Store Performance
                        </h1>
                        <p className="text-slate-500 dark:text-slate-400">Manage and monitor performance across all retail locations</p>
                    </div>
                </div>

                {error && (
                    <div className={`p-4 rounded-xl border flex flex-col gap-2 ${isCapacityError 
                        ? 'bg-rose-500/10 border-rose-500/20 text-rose-600 dark:text-rose-400' 
                        : 'bg-amber-500/10 border-amber-500/20 text-amber-600 dark:text-amber-400'}`}>
                        <div className="flex items-center gap-2 font-bold uppercase text-xs tracking-wider">
                            <Activity className="w-4 h-4" />
                            {isCapacityError ? 'System Resource Limit Hit' : 'Data Sync Error'}
                        </div>
                        <p className="text-sm">
                            {isCapacityError 
                                ? "Your data compute capacity has been exceeded. This usually happens during high load or if the capacity tier is too low. Please wait a few minutes or contact your administrator." 
                                : error}
                        </p>
                    </div>
                )}

                {/* Page Helper */}
                <PageHelper
                    title="How Store Performance works"
                    summary="Ranks all retail stores by revenue, sell-through, and inventory health. Use search and season filter to find specific stores."

                    dataSource="Site Performance Logs"
                    refreshNote="Recalculated on each page load with live data"
                    items={[
                        {
                            icon: <DollarSign className="w-4 h-4" />,
                            label: 'Revenue',
                            description: 'Net sales revenue (after returns) for the store — cumulative / lifetime totals.'
                        },
                        {
                            icon: <TrendingUp className="w-4 h-4" />,
                            label: 'Sell-Through',
                            description: 'Lifetime Ratio: Units Sold ÷ (Units Sold + Units in Stock) × 100.'
                        },
                        {
                            icon: <Activity className="w-4 h-4" />,
                            label: 'Health Status',
                            description: 'Balanced: ST% ≥ 70%. Warning: 50–70%. Critical: < 50% or excessive at-risk stock.'
                        },
                    ]}
                />





                {/* Table */}
                <div className="bg-white dark:bg-slate-900/50 border border-slate-200 dark:border-slate-800 rounded-xl overflow-hidden">
                    <div className="overflow-x-auto">
                        <table className="w-full text-sm text-left">
                            <thead className="text-xs text-slate-500 uppercase bg-slate-50 dark:bg-slate-800/50 border-b border-slate-200 dark:border-slate-800">
                                <tr>
                                    <th className="px-6 py-4 font-medium cursor-pointer hover:text-slate-700 dark:hover:text-slate-300" onClick={() => handleSort('name')}>
                                        Store Name <SortIcon column="name" />
                                    </th>
                                    <th className="px-6 py-4 font-medium text-right cursor-pointer hover:text-slate-700 dark:hover:text-slate-300" onClick={() => handleSort('revenue')}>
                                        Revenue <SortIcon column="revenue" />
                                    </th>
                                    <th className="px-6 py-4 font-medium text-right cursor-pointer hover:text-slate-700 dark:hover:text-slate-300" onClick={() => handleSort('discountRate')}>
                                        Discount % <SortIcon column="discountRate" />
                                    </th>
                                    <th className="px-6 py-4 font-medium text-right cursor-pointer hover:text-slate-700 dark:hover:text-slate-300" onClick={() => handleSort('unitsSold')}>
                                        Units Sold <SortIcon column="unitsSold" />
                                    </th>
                                    <th className="px-6 py-4 font-medium cursor-pointer hover:text-slate-700 dark:hover:text-slate-300" onClick={() => handleSort('sellThrough')}>
                                        Sell-Through <SortIcon column="sellThrough" />
                                    </th>

                                    <th className="px-6 py-4 font-medium text-center cursor-pointer hover:text-slate-700 dark:hover:text-slate-300" onClick={() => handleSort('status')}>
                                        Health <SortIcon column="status" />
                                    </th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                                {loading ? (
                                    <tr>
                                        <td colSpan={6} className="px-6 py-8 text-center text-slate-500">
                                            Loading store data...
                                        </td>
                                    </tr>
                                ) : stores.length === 0 ? (
                                    <tr>
                                        <td colSpan={6} className="px-6 py-8 text-center text-slate-500">
                                            No stores found matching your criteria.
                                        </td>
                                    </tr>
                                ) : (
                                    stores.map((store: any) => (
                                        <tr key={store.id} className="hover:bg-slate-50 dark:hover:bg-slate-800/30 transition-colors">
                                            <td className="px-6 py-4 font-medium text-slate-900 dark:text-white">
                                                <div>{store.name}</div>
                                                <div className="text-xs text-slate-400 font-normal">{store.id}</div>
                                            </td>
                                            <td className="px-6 py-4 text-right text-emerald-600 dark:text-emerald-400 font-medium">
                                                {new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(store.revenue)}
                                            </td>
                                            <td className="px-6 py-4 text-right text-slate-700 dark:text-slate-300">
                                                {store.discountRate}%
                                            </td>
                                            <td className="px-6 py-4 text-right text-slate-700 dark:text-slate-300">
                                                {new Intl.NumberFormat('en-IN').format(store.unitsSold)}
                                            </td>
                                            <td className="px-6 py-4">
                                                <div className="flex items-center gap-3">
                                                    <div className="w-24 h-1.5 bg-slate-200 dark:bg-slate-700 rounded-full overflow-hidden">
                                                        <div
                                                            className={`h-full rounded-full ${store.sellThrough >= 70 ? 'bg-emerald-500' :
                                                                store.sellThrough >= 50 ? 'bg-amber-500' : 'bg-rose-500'
                                                                }`}
                                                            style={{ width: `${Math.min(store.sellThrough, 100)}%` }}
                                                        />
                                                    </div>
                                                    <span className={`text-sm ${store.sellThrough >= 70 ? 'text-emerald-600 dark:text-emerald-400' :
                                                        store.sellThrough >= 50 ? 'text-amber-600 dark:text-amber-400' : 'text-rose-600 dark:text-rose-400'
                                                        }`}>{store.sellThrough}%</span>
                                                </div>
                                            </td>

                                            <td className="px-6 py-4 text-center">
                                                <span className={`inline-flex px-2.5 py-1 text-xs font-medium rounded-full ${store.status === 'Optimal' ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20' :
                                                    store.status === 'Critical' ? 'bg-rose-500/10 text-rose-600 dark:text-rose-400 border border-rose-500/20' :
                                                        'bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-500/20'
                                                    }`}>
                                                    {store.status}
                                                </span>
                                            </td>
                                        </tr>
                                    ))
                                )}
                            </tbody>
                        </table>
                    </div>

                    {/* Pagination */}
                    <div className="px-6 py-4 border-t border-slate-100 dark:border-slate-800 flex items-center justify-between">
                        <div className="text-sm text-slate-500 dark:text-slate-400">
                            Showing <span className="font-medium text-slate-900 dark:text-white">{total === 0 ? 0 : (page - 1) * limit + 1}</span> to <span className="font-medium text-slate-900 dark:text-white">{Math.min(page * limit, total)}</span> of <span className="font-medium text-slate-900 dark:text-white">{total}</span> stores
                        </div>
                        <div className="flex gap-2">
                            <button
                                onClick={() => setPage(p => Math.max(1, p - 1))}
                                disabled={page === 1 || loading}
                                className="p-2 rounded-lg bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400 disabled:opacity-50 disabled:cursor-not-allowed hover:bg-slate-200 dark:hover:bg-slate-700 hover:text-slate-900 dark:hover:text-white transition-colors"
                            >
                                <ChevronLeft className="w-5 h-5" />
                            </button>
                            <button
                                onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                                disabled={page === totalPages || loading || total === 0}
                                className="p-2 rounded-lg bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400 disabled:opacity-50 disabled:cursor-not-allowed hover:bg-slate-200 dark:hover:bg-slate-700 hover:text-slate-900 dark:hover:text-white transition-colors"
                            >
                                <ChevronRight className="w-5 h-5" />
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        </DashboardLayout>
    );
};

export default Stores;

import React, { useEffect, useState } from 'react';
import DashboardLayout from '../layouts/DashboardLayout';
import { Truck, CheckCircle, Package, ArrowRight, Zap, Target, TrendingUp, Activity } from 'lucide-react';
import { InfoTooltip } from '../components/InfoTooltip';
import { PageHelper } from '../components/PageHelper';

import { API_URL } from '../../config';

export default function Replenishment() {
    const [data, setData] = useState<any>(null);
    const [loading, setLoading] = useState(true);

    const [showModal, setShowModal] = useState(false);
    const [showApproveAllModal, setShowApproveAllModal] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [isCapacityError, setIsCapacityError] = useState(false);
    const [newTransfer, setNewTransfer] = useState({
        source: '',
        destination: '',
        sku: '',
        quantity: 1
    });
    const [isSubmitting, setIsSubmitting] = useState(false);
    const LS_KEY = 'replenishment_acted_ids';
    const [actedIds, setActedIds] = useState<string[]>(() => {
        try { return JSON.parse(localStorage.getItem(LS_KEY) || '[]'); } catch { return []; }
    });
    const [pendingApprovalId, setPendingApprovalId] = useState<string | null>(null);

    const addActedId = (id: string) => {
        setActedIds(prev => {
            const next = [...prev, id];
            try { localStorage.setItem(LS_KEY, JSON.stringify(next)); } catch { }
            return next;
        });
    };

    useEffect(() => {
        fetchReplenishment();
    }, []);

    const fetchReplenishment = async () => {
        try {
            const res = await fetch(`${API_URL}/replenishment`);
            const json = await res.json();

            if (json.is_capacity_error) {
                setIsCapacityError(true);
                setError(json.error || "Compute capacity exceeded.");
                setData(null);
            } else if (json.error) {
                setError(json.error);
                setData(null);
            } else {
                setData(json);
                setError(null);
                setIsCapacityError(false);
            }
        } catch (error) {
            console.error('Error fetching replenishment:', error);
            setError("Failed to connect to backend.");
        } finally {
            setLoading(false);
        }
    };

    const handleCreateTransfer = async (e: React.FormEvent) => {
        e.preventDefault();
        setIsSubmitting(true);
        try {
            const payload = { ...newTransfer };

            // Optimistic UI updates to avoid freezing the UI with a full data refetch
            const currentObj = {
                id: `AUTO-${Date.now()}`,
                source: payload.source,
                destination: payload.destination,
                sku: payload.sku,
                items: payload.quantity,
                status: 'In Transit',
                eta: 'Just Now',
                product: 'N/A' // Generic since we might not have the full product string
            };

            setData((prev: any) => ({
                ...prev,
                current_transfers: [currentObj, ...prev.current_transfers]
            }));

            if (pendingApprovalId) {
                addActedId(pendingApprovalId);
                setPendingApprovalId(null);
            }

            setShowModal(false);
            setNewTransfer({ source: '', destination: '', sku: '', quantity: 1 });

            // Fire and forget
            fetch(`${API_URL}/replenishment/transfer`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            }).catch(err => console.error('Transfer failed silently:', err));
            
        } catch (error) {
            console.error('Error creating transfer:', error);
        } finally {
            setIsSubmitting(false);
        }
    };

    const handleApproveAll = async () => {
        setIsSubmitting(true);
        try {
            const newTransfers = visibleRecs.map((item: any) => ({
                source: item.source, destination: item.destination, sku: item.sku, quantity: item.quantity
            }));
            
            // Optimistic UI
            const newIds = visibleRecs.map((item: any) => item.id);
            setActedIds(prev => {
                const next = [...prev, ...newIds];
                try { localStorage.setItem(LS_KEY, JSON.stringify(next)); } catch {}
                return next;
            });
            
            const mappedCurrent = visibleRecs.map((item: any, i: number) => ({
                id: `AUTO-${Date.now()}-${i}`, source: item.source, destination: item.destination, sku: item.sku, items: item.quantity, status: 'In Transit', eta: 'Just Now', product: item.product
            }));
            
            setData((prev: any) => ({
                ...prev,
                current_transfers: [...mappedCurrent, ...prev.current_transfers]
            }));

            // Submit all silently
            for (const payload of newTransfers) {
                await fetch(`${API_URL}/replenishment/transfer`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }).catch(() => {});
            }
        } finally {
            setIsSubmitting(false);
            setShowApproveAllModal(false);
        }
    };

    if (loading) {
        return (
            <DashboardLayout>
                <div className="flex items-center justify-center h-96">
                    <div className="text-slate-400">Loading replenishment data...</div>
                </div>
            </DashboardLayout>
        );
    }

    if (error || !data) {
        return (
            <DashboardLayout>
                <div className="space-y-8">
                    <div>
                        <h1 className="text-2xl font-bold text-slate-900 dark:text-white">Replenishment and Transfers</h1>
                        <p className="text-slate-500 dark:text-slate-400">Optimize stock distribution across stores</p>
                    </div>

                    <div className={`p-6 rounded-xl border flex flex-col gap-3 ${isCapacityError
                        ? 'bg-rose-500/10 border-rose-500/20 text-rose-600 dark:text-rose-400'
                        : 'bg-amber-500/10 border-amber-500/20 text-amber-600 dark:text-amber-400'}`}>
                        <div className="flex items-center gap-2 font-bold uppercase text-xs tracking-wider">
                            <Activity className="w-5 h-5 ml-[-2px]" />
                            {isCapacityError ? 'System Resource Limit Hit' : 'Data Sync Error'}
                        </div>
                        <p className="text-sm">
                            {isCapacityError
                                ? "Your data compute capacity has been exceeded. This usually happens during high load or if the capacity tier is too low. Please wait a few minutes or contact your administrator."
                                : error}
                        </p>
                        <button
                            onClick={() => { setLoading(true); fetchReplenishment(); }}
                            className="mt-2 w-fit px-4 py-2 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg text-sm font-medium hover:bg-slate-50 dark:hover:bg-slate-700 transition-colors"
                        >
                            Retry Connection
                        </button>
                    </div>
                </div>
            </DashboardLayout>
        );
    }

    const { kpi, current_transfers, recommendations } = data;
    const visibleRecs = (recommendations || []).filter((r: any) => !actedIds.includes(r.id)).slice(0, 6);

    return (
        <DashboardLayout>
            <div className="space-y-8">
                {/* Header */}
                <div className="flex justify-between items-center">
                    <div>
                        <h1 className="text-2xl font-bold text-slate-900 dark:text-white">Replenishment and Transfers</h1>
                        <p className="text-slate-500 dark:text-slate-400">Optimize stock distribution across stores</p>
                    </div>
                    <div className="flex gap-3">
                        <button className="px-4 py-2 bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-white rounded-lg hover:bg-slate-200 dark:hover:bg-slate-700 transition-colors">
                            View History
                        </button>
                        <button
                            onClick={() => setShowModal(true)}
                            className="px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition-colors flex items-center gap-2"
                        >
                            <Truck className="w-4 h-4" />
                            Create Transfer
                        </button>
                    </div>
                </div>

                {/* Modal */}
                {showModal && (
                    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4">
                        <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl w-full max-w-md shadow-2xl animate-in fade-in zoom-in duration-200">
                            <div className="p-6 border-b border-slate-100 dark:border-slate-800">
                                <h3 className="text-xl font-bold text-slate-900 dark:text-white">Create Direct Transfer</h3>
                                <p className="text-sm text-slate-500 dark:text-slate-400 mt-1">Manually initiate a stock movement between stores</p>
                            </div>
                            <form onSubmit={handleCreateTransfer} className="p-6 space-y-4">
                                <div className="space-y-2">
                                    <label className="text-sm font-medium text-slate-700 dark:text-slate-300">Donor Store (Source)</label>
                                    <input
                                        required
                                        type="text"
                                        placeholder="e.g. SHOPPERS STOP - MUMBAI"
                                        className="w-full px-4 py-2 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg focus:ring-2 focus:ring-indigo-500 outline-none transition-all dark:text-white"
                                        value={newTransfer.source}
                                        onChange={e => setNewTransfer({ ...newTransfer, source: e.target.value })}
                                    />
                                </div>
                                <div className="space-y-2">
                                    <label className="text-sm font-medium text-slate-700 dark:text-slate-300">Receiver Store (Destination)</label>
                                    <input
                                        required
                                        type="text"
                                        placeholder="e.g. SHOPPERS STOP - DELHI"
                                        className="w-full px-4 py-2 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg focus:ring-2 focus:ring-indigo-500 outline-none transition-all dark:text-white"
                                        value={newTransfer.destination}
                                        onChange={e => setNewTransfer({ ...newTransfer, destination: e.target.value })}
                                    />
                                </div>
                                <div className="grid grid-cols-2 gap-4">
                                    <div className="space-y-2">
                                        <label className="text-sm font-medium text-slate-700 dark:text-slate-300">SKU Code</label>
                                        <input
                                            required
                                            type="text"
                                            placeholder="e.g. T09211"
                                            className="w-full px-4 py-2 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg focus:ring-2 focus:ring-indigo-500 outline-none transition-all dark:text-white font-mono"
                                            value={newTransfer.sku}
                                            onChange={e => setNewTransfer({ ...newTransfer, sku: e.target.value })}
                                        />
                                    </div>
                                    <div className="space-y-2">
                                        <label className="text-sm font-medium text-slate-700 dark:text-slate-300">Quantity</label>
                                        <input
                                            required
                                            type="number"
                                            min="1"
                                            className="w-full px-4 py-2 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg focus:ring-2 focus:ring-indigo-500 outline-none transition-all dark:text-white"
                                            value={newTransfer.quantity}
                                            onChange={e => setNewTransfer({ ...newTransfer, quantity: parseInt(e.target.value) })}
                                        />
                                    </div>
                                </div>
                                <div className="flex gap-3 pt-4">
                                    <button
                                        type="button"
                                        onClick={() => { setShowModal(false); setPendingApprovalId(null); }}
                                        className="flex-1 px-4 py-2 border border-slate-200 dark:border-slate-700 text-slate-600 dark:text-slate-300 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
                                    >
                                        Cancel
                                    </button>
                                    <button
                                        disabled={isSubmitting}
                                        type="submit"
                                        className="flex-1 px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition-colors flex items-center justify-center gap-2 shadow-lg shadow-indigo-500/20 disabled:opacity-50"
                                    >
                                        {isSubmitting ? 'Creating...' : (
                                            <>
                                                <Zap className="w-4 h-4 text-amber-400 fill-amber-400" />
                                                Create Transfer
                                            </>
                                        )}
                                    </button>
                                </div>
                            </form>
                        </div>
                    </div>
                )}

                {/* Approve All Modal */}
                {showApproveAllModal && (
                    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4">
                        <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl w-full max-w-sm shadow-2xl animate-in fade-in zoom-in duration-200">
                            <div className="p-6 border-b border-slate-100 dark:border-slate-800">
                                <h3 className="text-xl font-bold text-slate-900 dark:text-white">Batch Create Transfers</h3>
                            </div>
                            <div className="p-6 space-y-4">
                                <p className="text-slate-600 dark:text-slate-300">
                                    Proceed to approve all {visibleRecs.length} visible transactions?
                                </p>
                                <div className="flex gap-3 pt-4">
                                    <button
                                        type="button"
                                        onClick={() => setShowApproveAllModal(false)}
                                        className="flex-1 px-4 py-2 border border-slate-200 dark:border-slate-700 text-slate-600 dark:text-slate-300 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
                                    >
                                        Cancel
                                    </button>
                                    <button
                                        disabled={isSubmitting}
                                        onClick={handleApproveAll}
                                        className="flex-1 px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition-colors flex items-center justify-center gap-2 shadow-lg shadow-indigo-500/20 disabled:opacity-50"
                                    >
                                        {isSubmitting ? 'Creating...' : (
                                            <>
                                                <Zap className="w-4 h-4 text-amber-400 fill-amber-400" />
                                                Create Transfers
                                            </>
                                        )}
                                    </button>
                                </div>
                            </div>
                        </div>
                    </div>
                )}

                {/* Page Helper */}
                <PageHelper
                    title="How Replenishment and Transfers works"
                    summary="Analyzes 30-day sales velocity per SKU per store to identify overstocked donors and understocked receivers, then recommends transfers using strict brand and state rules."
                    items={[
                        {
                            icon: <Zap className="w-4 h-4" />,
                            label: 'Donor vs Receiver Logic',
                            description: 'Donor: site with Days of Supply > 90 (overstocked). Receiver: site with DOS < 14 (understocked). Matched greedily by urgency (lowest DOS first).'
                        },
                        {
                            icon: <Target className="w-4 h-4" />,
                            label: 'Brand Isolation Rule',
                            description: 'Shoppers Stop (SS) sites can only transfer with other SS sites. Pantaloons (PT) sites can only transfer with other PT sites.'
                        },
                        {
                            icon: <TrendingUp className="w-4 h-4" />,
                            label: 'State Restriction Rule',
                            description: 'Pantaloons (PT) transfers are strictly restricted to the same state. Shoppers Stop (SS) transfers are allowed across any state to optimize national inventory.'
                        },
                        {
                            icon: <CheckCircle className="w-4 h-4" />,
                            label: 'Local vs Network',
                            description: '"Local" applies to any same-state transfer. "Network" applies to cross-state Shoppers Stop transfers. Logic follows Customer_Master[STATE NAME] records.'
                        },
                        {
                            icon: <Package className="w-4 h-4" />,
                            label: 'Urgency Scoring',
                            description: 'High: receiver DOS < 7 days. Medium: 7–14 days. Urgency drives prioritization — most critical receivers are matched first.'
                        },
                    ]}
                />

                {/* KPI Cards */}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="bg-white dark:bg-slate-900/50 border border-slate-200 dark:border-slate-800 p-6 rounded-xl flex items-center gap-4">
                        <div className="p-3 bg-blue-500/10 rounded-lg">
                            <CheckCircle className="w-6 h-6 text-blue-500 dark:text-blue-400" />
                        </div>
                        <div>
                            <div className="text-2xl font-bold text-slate-900 dark:text-white">{kpi.completed_month}</div>
                            <div className="flex items-center gap-1">
                                <div className="text-sm text-slate-500 dark:text-slate-400">Completed transfers</div>
                                <InfoTooltip
                                    title="Completed This Month"
                                    description="Count of distinct stock transfer batches recorded for the current rolling 180-day window."
                                    formula="COUNT(Transfers) where Date within 180 days"
                                />
                            </div>
                        </div>
                    </div>
                    <div className="bg-white dark:bg-slate-900/50 border border-slate-200 dark:border-slate-800 p-6 rounded-xl flex items-center gap-4">
                        <div className="p-3 bg-indigo-500/10 rounded-lg">
                            <Package className="w-6 h-6 text-indigo-500 dark:text-indigo-400" />
                        </div>
                        <div>
                            <div className="text-2xl font-bold text-slate-900 dark:text-white">{new Intl.NumberFormat('en-IN').format(kpi.units_transferred)}</div>
                            <div className="flex items-center gap-1">
                                <div className="text-sm text-slate-500 dark:text-slate-400">Units Transferred</div>
                                <InfoTooltip
                                    title="Units Transferred"
                                    description="Total units received via inter-store stock transfers in the last 180 days."
                                    formula="Σ(Received Quantity) where Date within 180 days"
                                />
                            </div>
                        </div>
                    </div>
                </div>

                {/* Store Rebalancing Potential */}
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 items-stretch">
                    <div className="flex flex-col">
                        <h2 className="text-lg font-semibold text-slate-900 dark:text-white mb-4 flex items-center gap-2">
                            <TrendingUp className="w-5 h-5 text-rose-500" />
                            Potential Receivers (DOS &lt; 14)
                        </h2>
                        <div className="bg-white dark:bg-slate-900/50 border border-slate-200 dark:border-slate-800 rounded-xl overflow-hidden flex-1">
                            <table className="w-full text-sm text-left h-full">
                                <thead className="bg-slate-50 dark:bg-slate-800/50 text-slate-500 uppercase text-xs border-b border-slate-100 dark:border-slate-800">
                                    <tr>
                                        <th className="px-4 py-3">Store</th>
                                        <th className="px-4 py-3">SKU</th>
                                        <th className="px-4 py-3 text-right">DOS</th>
                                        <th className="px-4 py-3 text-right">Current Stock</th>
                                    </tr>
                                </thead>
                                <tbody className="divide-y divide-slate-100 dark:divide-slate-800 align-top">
                                    {data.receivers?.slice(0, 8).map((store: any) => (
                                        <tr key={`${store.name}-${store.sku}`} className="hover:bg-slate-50 dark:hover:bg-slate-800/30">
                                            <td className="px-4 py-3 font-medium text-slate-900 dark:text-white">{store.name}</td>
                                            <td className="px-4 py-3 text-slate-500 dark:text-slate-400 font-mono text-xs">{store.sku}</td>
                                            <td className="px-4 py-3 text-right text-rose-500 font-bold">{store.dos}d</td>
                                            <td className="px-4 py-3 text-right text-slate-600 dark:text-slate-400">{store.stock} units</td>
                                        </tr>
                                    ))}
                                    {(!data.receivers || data.receivers.length === 0) && (
                                        <tr><td colSpan={4} className="p-4 text-center text-slate-400">No stores in critical need.</td></tr>
                                    )}
                                </tbody>
                            </table>
                        </div>
                    </div>

                    <div className="flex flex-col">
                        <h2 className="text-lg font-semibold text-slate-900 dark:text-white mb-4 flex items-center gap-2">
                            <Package className="w-5 h-5 text-emerald-500" />
                            Potential Donors (DOS &gt; 90)
                        </h2>
                        <div className="bg-white dark:bg-slate-900/50 border border-slate-200 dark:border-slate-800 rounded-xl overflow-hidden flex-1">
                            <table className="w-full text-sm text-left h-full">
                                <thead className="bg-slate-50 dark:bg-slate-800/50 text-slate-500 uppercase text-xs border-b border-slate-100 dark:border-slate-800">
                                    <tr>
                                        <th className="px-4 py-3">Store</th>
                                        <th className="px-4 py-3">SKU</th>
                                        <th className="px-4 py-3 text-right">DOS</th>
                                        <th className="px-4 py-3 text-right">Excess Stock</th>
                                    </tr>
                                </thead>
                                <tbody className="divide-y divide-slate-100 dark:divide-slate-800 align-top">
                                    {data.donors?.slice(0, 8).map((store: any) => (
                                        <tr key={`${store.name}-${store.sku}`} className="hover:bg-slate-50 dark:hover:bg-slate-800/30">
                                            <td className="px-4 py-3 font-medium text-slate-900 dark:text-white">{store.name}</td>
                                            <td className="px-4 py-3 text-slate-500 dark:text-slate-400 font-mono text-xs">{store.sku}</td>
                                            <td className="px-4 py-3 text-right text-emerald-500 font-bold">{store.dos}d</td>
                                            <td className="px-4 py-3 text-right text-slate-600 dark:text-slate-400">{store.stock} units</td>
                                        </tr>
                                    ))}
                                    {(!data.donors || data.donors.length === 0) && (
                                        <tr><td colSpan={4} className="p-4 text-center text-slate-400">No overstocked stores found.</td></tr>
                                    )}
                                </tbody>
                            </table>
                        </div>
                    </div>
                </div>

                {/* Recommended Transfers */}
                <div>
                    <div className="flex items-center justify-between mb-4">
                        <div>
                            <h2 className="text-lg font-semibold text-slate-900 dark:text-white">Recommended Transfers</h2>
                            <p className="text-sm text-slate-500 dark:text-slate-400">AI-powered suggestions to optimize inventory</p>
                        </div>
                        <button 
                            onClick={() => setShowApproveAllModal(true)}
                            className="px-3 py-1.5 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 shadow-sm shadow-indigo-500/20 transition-colors">
                            Approve All ({visibleRecs.length})
                        </button>
                    </div>

                    <div className="bg-white dark:bg-slate-900/50 border border-slate-200 dark:border-slate-800 rounded-xl overflow-hidden">
                        <div className="divide-y divide-slate-100 dark:divide-slate-800">
                            {visibleRecs.map((item: any, idx: number) => (
                                <div key={item.id || idx} className="p-4 flex flex-col md:flex-row md:items-center justify-between gap-4 hover:bg-slate-50 dark:hover:bg-slate-800/30 transition-colors">
                                    <div className="flex items-center gap-4">
                                        <span className={`px-2 py-1 rounded-full text-xs font-medium uppercase w-16 text-center ${item.urgency === 'High' ? 'bg-rose-500/10 text-rose-600 dark:text-rose-400' :
                                            item.urgency === 'Medium' ? 'bg-amber-500/10 text-amber-600 dark:text-amber-400' :
                                                'bg-slate-100 dark:bg-slate-500/10 text-slate-500 dark:text-slate-400'
                                            }`}>
                                            {item.urgency}
                                        </span>

                                        <div className="flex-1 min-w-0">
                                            <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-slate-900 dark:text-white">
                                                <span className="font-semibold truncate max-w-[280px] cursor-help" title={item.source}>{item.source}</span>
                                                <ArrowRight className="w-4 h-4 text-slate-400 shrink-0" />
                                                <span className="font-semibold truncate max-w-[280px] cursor-help" title={item.destination}>{item.destination}</span>
                                            </div>
                                            <div className="text-[12px] text-slate-500 dark:text-slate-400 flex flex-wrap items-center gap-x-3 gap-y-1 mt-1">
                                                <div className="flex items-center gap-1.5">
                                                    <span className="font-mono text-[10px] bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300 px-1.5 py-0.5 rounded border border-slate-200 dark:border-slate-700">{item.sku}</span>
                                                    <span className="truncate max-w-[150px]">{item.product}</span>
                                                </div>
                                                <div className="flex items-center gap-1.5">
                                                    <span className="w-1 h-1 rounded-full bg-slate-300 dark:bg-slate-700 hidden sm:block"></span>
                                                    <span className="text-indigo-600 dark:text-indigo-400 font-bold">{item.quantity} units</span>
                                                </div>
                                            </div>
                                        </div>
                                    </div>

                                    <div className="flex items-center justify-between md:justify-end gap-6 w-full md:w-auto">
                                        <div className="text-sm text-slate-400 text-right">
                                            {item.reason}
                                        </div>
                                        <div className="flex items-center gap-2">
                                            <button
                                                onClick={() => addActedId(item.id)}
                                                className="px-3 py-1.5 text-sm border border-slate-200 dark:border-slate-700 text-slate-600 dark:text-slate-300 rounded-lg hover:bg-slate-50 dark:hover:bg-slate-800 hover:text-slate-900 dark:hover:text-white transition-colors">
                                                Decline
                                            </button>
                                            <button
                                                onClick={() => {
                                                    setPendingApprovalId(item.id);
                                                    setNewTransfer({
                                                        source: item.source,
                                                        destination: item.destination,
                                                        sku: item.sku,
                                                        quantity: item.quantity
                                                    });
                                                    setShowModal(true);
                                                }}
                                                className="px-3 py-1.5 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 border border-indigo-600 transition-colors"
                                            >
                                                Approve
                                            </button>
                                        </div>
                                    </div>
                                </div>
                            ))}
                            {visibleRecs.length === 0 && (
                                <div className="p-8 text-center text-slate-400">No recommendations available. All stores are balanced!</div>
                            )}
                        </div>
                    </div>
                </div>

                {/* Current Transfers */}
                <div>
                    <h2 className="text-lg font-semibold text-slate-900 dark:text-white mb-4">Current Transfers</h2>
                    <div className="bg-white dark:bg-slate-900/50 border border-slate-200 dark:border-slate-800 rounded-xl overflow-hidden">
                        <div className="divide-y divide-slate-100 dark:divide-slate-800">
                            {current_transfers.slice(0, 3).map((item: any, idx: number) => (
                                <div key={idx} className="p-4 flex items-center justify-between hover:bg-slate-50 dark:hover:bg-slate-800/30 transition-colors">
                                    <div className="flex items-center gap-4">
                                        <div className="px-2 py-0.5 bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300 rounded font-mono text-[10px] font-bold border border-slate-200 dark:border-slate-700">
                                            {item.sku || item.id}
                                        </div>
                                        <div className="flex flex-col">
                                            <div className="flex items-center gap-2 text-slate-900 dark:text-white">
                                                <span className="font-medium">{item.source}</span>
                                                <ArrowRight className="w-4 h-4 text-slate-400" />
                                                <span className="font-medium">{item.destination}</span>
                                            </div>
                                            {item.product && item.product !== 'N/A' && (
                                                <div className="text-[11px] text-slate-500 dark:text-slate-400 mt-0.5">
                                                    {item.product}
                                                </div>
                                            )}
                                        </div>
                                    </div>
                                    <div className="flex items-center gap-6">
                                        <div className="text-sm text-slate-500 dark:text-slate-400">{item.items} items</div>
                                        <span className={`px-2 py-1 rounded-full text-xs font-medium ${item.status === 'In Transit' ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400' :
                                            item.status === 'Pending' ? 'bg-amber-500/10 text-amber-600 dark:text-amber-400' :
                                                'bg-blue-500/10 text-blue-600 dark:text-blue-400'
                                            }`}>
                                            {item.status}
                                        </span>
                                        <div className="text-sm text-slate-400 w-24 text-right">{item.eta}</div>
                                    </div>
                                </div>
                            ))}
                            {current_transfers.length === 0 && (
                                <div className="p-8 text-center text-slate-400">No active transfers found.</div>
                            )}
                        </div>
                    </div>
                </div>
            </div>
        </DashboardLayout>
    );
}

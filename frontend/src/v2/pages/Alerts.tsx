import React, { useState, useEffect } from 'react';
import { Bell, AlertTriangle, CheckCircle, Clock, Package, TrendingDown, ArrowRight, X } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import DashboardLayout from '../layouts/DashboardLayout';
import { PageHelper } from '../components/PageHelper';

interface Alert {
    id: string;
    type: string;
    title: string;
    description: string;
    timeAgo: string;
    severity: 'high' | 'medium' | 'low';
    metrics: { label: string; value: string; status: string }[];
}

const AlertCard = ({ alert, onDismiss }: { alert: Alert; onDismiss: (id: string) => void; key?: React.Key }) => {
    const getIcon = () => {
        switch (alert.type) {
            case 'Low Stock': return <Package className="w-5 h-5 text-rose-500" />;
            case 'Overstock': return <AlertTriangle className="w-5 h-5 text-amber-500" />;
            case 'Slow-Moving': return <Clock className="w-5 h-5 text-orange-500" />;
            default: return <Bell className="w-5 h-5 text-slate-400" />;
        }
    };

    const getBgColor = () => {
        switch (alert.type) {
            case 'Low Stock': return 'bg-rose-500/10 border-rose-500/20';
            case 'Overstock': return 'bg-amber-500/10 border-amber-500/20';
            case 'Slow-Moving': return 'bg-orange-500/10 border-orange-500/20';
            default: return 'bg-slate-800 border-slate-700';
        }
    };

    const getBadgeColor = () => {
        switch (alert.type) {
            case 'Low Stock': return 'bg-rose-500/20 text-rose-400';
            case 'Overstock': return 'bg-amber-500/20 text-amber-400';
            case 'Slow-Moving': return 'bg-orange-500/20 text-orange-400';
            default: return 'bg-slate-700 text-slate-300';
        }
    };

    return (
        <div className={`p-5 rounded-xl border bg-white dark:bg-[#1E293B] border-slate-200 dark:border-slate-700/50 hover:border-slate-300 dark:hover:border-slate-600 transition-all group shadow-sm`}>
            <div className="flex items-start gap-4">
                <div className={`p-3 rounded-lg ${getBgColor()} border`}>
                    {getIcon()}
                </div>
                <div className="flex-1 min-w-0 pt-0.5">
                    <div className="flex items-center gap-3 mb-1.5">
                        <span className={`px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider rounded ${getBadgeColor()}`}>
                            {alert.type}
                        </span>
                        <span className="text-xs text-slate-500 font-medium">{alert.timeAgo}</span>
                    </div>
                    <h3 className="text-lg font-bold text-slate-900 dark:text-slate-100 mb-1">{alert.title}</h3>
                    <p className="text-sm text-slate-500 dark:text-slate-400 mb-4">{alert.description}</p>

                    <div className="flex flex-wrap gap-6">
                        {alert.metrics.map((m, idx) => (
                            <div key={idx} className="flex flex-col">
                                <span className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold mb-0.5">{m.label}</span>
                                <span className={`text-sm font-bold ${m.status === 'critical' ? 'text-rose-500 dark:text-rose-400' :
                                    m.status === 'warning' ? 'text-amber-500 dark:text-amber-400' : 'text-slate-700 dark:text-slate-200'
                                    }`}>
                                    {m.value}
                                </span>
                            </div>
                        ))}
                    </div>
                </div>
                <div className="flex items-center gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
                    <button className="px-3 py-1.5 text-xs font-medium bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 text-slate-600 dark:text-slate-300 rounded-md transition-colors">
                        View
                    </button>
                    <button
                        onClick={() => onDismiss(alert.id)}
                        className="p-1.5 hover:bg-slate-100 dark:hover:bg-slate-800 rounded-md text-slate-400 hover:text-rose-500 transition-colors"
                    >
                        <X className="w-4 h-4" />
                    </button>
                </div>
            </div>
        </div>
    );
};

const KPICard = ({ icon: Icon, value, label, iconClass, iconBg }: { icon: any; value: any; label: string; iconClass: string; iconBg: string }) => (
    <div className="flex-1 p-5 rounded-xl border border-slate-200 dark:border-slate-700/50 bg-white dark:bg-slate-800/60">
        <div className="flex items-center gap-4">
            <div className={`p-3 rounded-lg ${iconBg}`}>
                <Icon className={`w-6 h-6 ${iconClass}`} />
            </div>
            <div>
                <div className="text-3xl font-bold text-slate-900 dark:text-white">{value}</div>
                <div className="text-sm text-slate-500 dark:text-slate-400 mt-0.5">{label}</div>
            </div>
        </div>
    </div>
);

export default function Alerts() {
    const navigate = useNavigate();
    const [alerts, setAlerts] = useState<Alert[]>([]);
    const [loading, setLoading] = useState(true);
    const [activeTab, setActiveTab] = useState('All');
    const [dismissed, setDismissed] = useState<Set<string>>(new Set());

    useEffect(() => {
        fetch('/api/alerts?limit=50')
            .then(res => res.json())
            .then(data => {
                setAlerts(data.alerts || []);
                setLoading(false);
            })
            .catch(err => {
                console.error("Failed to fetch alerts:", err);
                setLoading(false);
            });
    }, []);

    const handleDismiss = (id: string) => {
        setDismissed(prev => new Set(prev).add(id));
    };

    const visibleAlerts = alerts.filter(a => !dismissed.has(a.id));
    const criticalCount = visibleAlerts.filter(a => a.severity === 'high').length;
    const warningCount = visibleAlerts.filter(a => a.severity === 'medium').length;

    const filteredAlerts = visibleAlerts.filter(a => {
        if (activeTab === 'Critical') return a.severity === 'high';
        if (activeTab === 'Warnings') return a.severity === 'medium';
        return true;
    });

    return (
        <DashboardLayout>
            <div className="max-w-6xl mx-auto space-y-8 mt-6">
                <div className="flex items-center justify-between">
                    <div>
                        <h1 className="text-3xl font-bold text-slate-900 dark:text-white mb-2">Alerts & Notifications</h1>
                        <p className="text-slate-500 dark:text-slate-400">Monitor critical inventory issues</p>
                    </div>
                    <div className="flex gap-3">
                        <button className="px-5 py-2.5 text-sm font-semibold text-white bg-slate-800 hover:bg-slate-900 dark:bg-[#0F172A] dark:hover:bg-slate-900 rounded-lg transition-colors flex items-center gap-2 border border-slate-700">
                            <CheckCircle className="w-4 h-4" /> Mark All Read
                        </button>
                        <button
                            onClick={() => navigate('/settings')}
                            className="px-5 py-2.5 text-sm font-semibold text-slate-700 dark:text-slate-900 bg-slate-100 dark:bg-white hover:bg-slate-200 dark:hover:bg-slate-100 rounded-lg transition-colors border border-slate-200 dark:border-transparent shadow-sm"
                        >
                            Configure Alerts
                        </button>
                    </div>
                </div>

                {/* Page Helper */}
                <PageHelper
                    title="How Alerts & Notifications works"
                    summary="Monitors inventory health in real-time and surfaces critical issues requiring immediate action — low stock, overstock, and slow-moving SKUs."
                    dataSource="Real-time Inventory Monitoring"
                    refreshNote="Alerts generated on each API call based on rolling 30-day sales velocity and current stock."
                    items={[
                        {
                            icon: <AlertTriangle className="w-4 h-4" />,
                            label: 'Critical Alerts',
                            description: 'High-severity issues: SKUs with < 7 days of supply (stockout risk) or > 90 days (extreme overstock).'
                        },
                        {
                            icon: <Bell className="w-4 h-4" />,
                            label: 'Warnings',
                            description: 'Medium-severity issues: SKUs with 7–14 days supply or 45–90 days supply. Target for proactive rebalancing.'
                        },
                        {
                            icon: <Package className="w-4 h-4" />,
                            label: 'Low Stock Alert',
                            description: 'Triggered when Days of Supply < 14. Formula: (Received Qty - Sold Qty) ÷ (Sold Qty Last 30d / 30).'
                        },
                    ]}
                />

                <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                    <KPICard
                        icon={AlertTriangle}
                        value={criticalCount}
                        label="Critical Alerts"
                        iconClass="text-rose-400"
                        iconBg="bg-rose-500/20"
                    />
                    <KPICard
                        icon={Bell}
                        value={warningCount}
                        label="Warnings"
                        iconClass="text-amber-400"
                        iconBg="bg-amber-500/20"
                    />
                    <KPICard
                        icon={CheckCircle}
                        value="24"
                        label="Resolved Today"
                        iconClass="text-emerald-400"
                        iconBg="bg-emerald-500/20"
                    />
                </div>

                <div className="border-b border-slate-200 dark:border-slate-700/50">
                    <div className="flex gap-8">
                        {['All', 'Critical', 'Warnings'].map((tab) => (
                            <button
                                key={tab}
                                onClick={() => setActiveTab(tab)}
                                className={`pb-4 text-sm font-bold transition-colors relative flex items-center gap-2 ${activeTab === tab ? 'text-emerald-500 dark:text-emerald-400' : 'text-slate-400 dark:text-slate-500 hover:text-slate-700 dark:hover:text-slate-300'
                                    }`}
                            >
                                {tab}
                                {tab === 'All' && <span className="text-xs bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300 px-2.5 py-0.5 rounded-full">{visibleAlerts.length}</span>}
                                {tab === 'Critical' && <span className="text-xs bg-rose-500 text-white px-2.5 py-0.5 rounded-full">{criticalCount}</span>}
                                {activeTab === tab && (
                                    <div className="absolute bottom-0 left-0 w-full h-0.5 bg-emerald-500 dark:bg-emerald-400 rounded-t-full" />
                                )}
                            </button>
                        ))}
                    </div>
                </div>

                <div className="space-y-4">
                    {loading ? (
                        <div className="text-center py-12 text-slate-500">Loading alerts...</div>
                    ) : filteredAlerts.length === 0 ? (
                        <div className="text-center py-12 text-slate-500">No alerts found.</div>
                    ) : (
                        filteredAlerts.map((alert, idx) => (
                            <AlertCard key={`${alert.id}-${idx}`} alert={alert} onDismiss={handleDismiss} />
                        ))
                    )}
                </div>
            </div>
        </DashboardLayout>
    );
}

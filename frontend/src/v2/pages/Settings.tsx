import React, { useState } from 'react';
import { Bell, Shield, Mail, ArrowLeftRight, Store, Database, Server } from 'lucide-react';
import DashboardLayout from '../layouts/DashboardLayout';
import { PageHelper } from '../components/PageHelper';

const Toggle = ({ enabled, onChange }: { enabled: boolean; onChange: () => void }) => (
    <button
        onClick={onChange}
        className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-emerald-500 focus:ring-offset-2 focus:ring-offset-white dark:focus:ring-offset-slate-900 ${enabled ? 'bg-emerald-500' : 'bg-slate-300 dark:bg-slate-700'
            }`}
    >
        <span
            className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${enabled ? 'translate-x-6' : 'translate-x-1'
                }`}
        />
    </button>
);

const SettingSection = ({ title, description, icon: Icon, children }: { title: string; description: string; icon: any; children: React.ReactNode }) => (
    <div className="mb-6 rounded-lg overflow-hidden shadow-sm">
        <div className="px-6 py-4 bg-slate-100 dark:bg-[#1E293B] flex items-center gap-3">
            <div className="bg-slate-200 dark:bg-slate-700/50 p-2 rounded-lg">
                <Icon className="w-5 h-5 text-slate-600 dark:text-slate-200" />
            </div>
            <div className="flex flex-col">
                <h3 className="text-lg font-bold text-slate-900 dark:text-white leading-tight">
                    {title}
                </h3>
                <p className="text-xs text-slate-500 dark:text-slate-400 font-medium">{description}</p>
            </div>
        </div>
        <div className="p-6 bg-slate-50 dark:bg-slate-800/40 space-y-6 border-t border-slate-200 dark:border-slate-600/30">
            {children}
        </div>
    </div>
);

const SettingItem = ({
    title,
    description,
    enabled,
    onChange
}: {
    title: string;
    description: string;
    enabled: boolean;
    onChange: () => void;
}) => (
    <div className="flex items-center justify-between">
        <div className="flex flex-col">
            <p className="text-base font-semibold text-slate-900 dark:text-slate-200">{title}</p>
            <p className="text-sm text-slate-500 dark:text-slate-400">{description}</p>
        </div>
        <Toggle enabled={enabled} onChange={onChange} />
    </div>
);

const ConfigInput = ({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) => (
    <div className="flex flex-col gap-1.5">
        <label className="text-sm font-medium text-slate-700 dark:text-slate-300">{label}</label>
        <input
            type="number"
            value={value}
            onChange={(e) => onChange(e.target.value)}
            className="w-full bg-white dark:bg-slate-700 border border-slate-300 dark:border-slate-600 text-slate-900 dark:text-white text-sm rounded-lg focus:ring-emerald-500 focus:border-emerald-500 block p-2.5 outline-none"
        />
    </div>
);

const IntegrationItem = ({ badge, title, description, connected }: { badge: string; title: string; description: string; connected: boolean }) => (
    <div className="flex items-center justify-between p-4 bg-white border border-slate-200 rounded-lg dark:bg-slate-800 dark:border-slate-700">
        <div className="flex items-center gap-4">
            <div className="flex items-center justify-center w-12 h-12 bg-slate-100 rounded-lg dark:bg-slate-700 font-bold text-slate-500 text-sm">
                {badge}
            </div>
            <div>
                <h4 className="text-base font-semibold text-slate-900 dark:text-white">{title}</h4>
                <p className="text-sm text-slate-500 dark:text-slate-400">{description}</p>
            </div>
        </div>
        <div>
            {connected ? (
                <span className="inline-flex items-center px-3 py-1 text-xs font-medium text-emerald-700 bg-emerald-100 rounded-full dark:bg-emerald-900/30 dark:text-emerald-400">
                    Connected
                </span>
            ) : (
                <button className="px-4 py-2 text-sm font-medium text-slate-700 bg-white border border-slate-300 rounded-lg hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-emerald-500 dark:bg-slate-800 dark:text-slate-300 dark:border-slate-600 dark:hover:bg-slate-700">
                    Connect
                </button>
            )}
        </div>
    </div>
);

export default function Settings() {
    const [settings, setSettings] = useState({
        criticalStock: true,
        sellThrough: true,
        emailDigest: true,
        transferNotify: false,
    });

    const [config, setConfig] = useState({
        targetSellThrough: '70',
        maxDiscount: '25',
        safetyStock: '14',
        agingThreshold: '90'
    });

    const toggle = (key: keyof typeof settings) => {
        setSettings(prev => ({ ...prev, [key]: !prev[key] }));
    };

    return (
        <DashboardLayout>
            <div className="max-w-4xl mx-auto space-y-8 pb-12">
                <div className="mb-8">
                    <h1 className="text-3xl font-bold text-slate-900 dark:text-white mb-1">Settings</h1>
                    <p className="text-slate-500 dark:text-slate-400">Manage preferences</p>
                </div>

                {/* Page Helper */}
                <PageHelper
                    title="How Settings works"
                    summary="Configure thresholds, alert rules, and integrations that drive the AI recommendations across all pages."
                    items={[
                        {
                            icon: <Store className="w-4 h-4" />,
                            label: 'Store Configuration',
                            description: 'Target Sell-Through: the % you aim to sell each season. Safety Stock: minimum days of supply before an alert fires. Aging Threshold: days before a SKU is flagged slow-moving.'
                        },
                        {
                            icon: <Bell className="w-4 h-4" />,
                            label: 'Alert Rules',
                            description: 'Toggle which alert types are active. Critical Stock fires when DOS < Safety Stock days. Sell-Through fires when actual % falls below target.'
                        },
                        {
                            icon: <Mail className="w-4 h-4" />,
                            label: 'Notifications',
                            description: 'Email Digest sends a daily summary of all active alerts. Transfer Notify sends an email when a replenishment transfer is approved.'
                        },
                        {
                            icon: <ArrowLeftRight className="w-4 h-4" />,
                            label: 'Replenishment Rules',
                            description: 'Max Discount: caps the markdown % the system can recommend. These thresholds feed directly into the Replenishment algorithm.'
                        },
                        {
                            icon: <Database className="w-4 h-4" />,
                            label: 'Integrations',
                            description: 'Connect your ERP, WMS, or BI tools. Connected integrations enable real-time data sync and automated transfer execution.'
                        },
                        {
                            icon: <Shield className="w-4 h-4" />,
                            label: 'Data & Privacy',
                            description: 'All data is stored in your own database. No inventory data is sent to external services. AI recommendations run locally on your server.'
                        }
                    ]}
                />

                {/* Store Configuration Section */}
                <SettingSection
                    title="Store Configuration"
                    description="Default settings for all stores"
                    icon={Store}
                >
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                        <ConfigInput
                            label="Target Sell-Through Rate (%)"
                            value={config.targetSellThrough}
                            onChange={(v) => setConfig({ ...config, targetSellThrough: v })}
                        />
                        <ConfigInput
                            label="Max Discount Threshold (%)"
                            value={config.maxDiscount}
                            onChange={(v) => setConfig({ ...config, maxDiscount: v })}
                        />
                        <ConfigInput
                            label="Safety Stock (Days)"
                            value={config.safetyStock}
                            onChange={(v) => setConfig({ ...config, safetyStock: v })}
                        />
                        <ConfigInput
                            label="Aging Alert Threshold (Days)"
                            value={config.agingThreshold}
                            onChange={(v) => setConfig({ ...config, agingThreshold: v })}
                        />
                    </div>
                </SettingSection>

                {/* Integrations Section */}
                <SettingSection
                    title="Integrations"
                    description="Connect external systems"
                    icon={Database}
                >
                    <div className="space-y-4">
                        <IntegrationItem
                            badge="POS"
                            title="Point of Sale"
                            description="Real-time sales sync"
                            connected={true}
                        />
                        <IntegrationItem
                            badge="ERP"
                            title="ERP System"
                            description="Inventory management"
                            connected={true}
                        />
                        <IntegrationItem
                            badge="WMS"
                            title="Warehouse Management"
                            description="Distribution tracking"
                            connected={false}
                        />
                    </div>
                </SettingSection>

                {/* Notifications Section */}
                <SettingSection
                    title="Notifications"
                    description="Configure alert preferences"
                    icon={Bell}
                >
                    <div className="space-y-8">
                        <SettingItem
                            title="Critical Stock Alerts"
                            description="Get notified when stock falls below safety levels"
                            enabled={settings.criticalStock}
                            onChange={() => toggle('criticalStock')}
                        />

                        <div className="h-px bg-slate-600/30" />

                        <SettingItem
                            title="Sell-Through Warnings"
                            description="Alert when stores underperform targets"
                            enabled={settings.sellThrough}
                            onChange={() => toggle('sellThrough')}
                        />

                        <div className="h-px bg-slate-600/30" />

                        <SettingItem
                            title="Email Digest"
                            description="Daily summary of key metrics"
                            enabled={settings.emailDigest}
                            onChange={() => toggle('emailDigest')}
                        />

                        <div className="h-px bg-slate-600/30" />

                        <SettingItem
                            title="Transfer Notifications"
                            description="Updates on inventory transfers"
                            enabled={settings.transferNotify}
                            onChange={() => toggle('transferNotify')}
                        />
                    </div>
                </SettingSection>
            </div>
        </DashboardLayout>
    );
}

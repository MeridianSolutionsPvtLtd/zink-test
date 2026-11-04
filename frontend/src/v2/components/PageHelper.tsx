import React, { useState } from 'react';
import { HelpCircle, ChevronDown, ChevronUp, BookOpen, Zap, Database } from 'lucide-react';

interface HelperItem {
    icon?: React.ReactNode;
    label: string;
    description: string;
}

interface PageHelperProps {
    title: string;
    summary: string;
    items: HelperItem[];
    dataSource?: string;
    refreshNote?: string;
}

export const PageHelper = ({ title, summary, items, dataSource, refreshNote }: PageHelperProps) => {
    const [open, setOpen] = useState(false);

    return (
        <div className="rounded-xl border border-blue-100 dark:border-blue-900/40 bg-blue-50/60 dark:bg-blue-950/20 overflow-hidden">
            <button
                onClick={() => setOpen(o => !o)}
                className="w-full flex items-center justify-between px-5 py-3.5 text-left hover:bg-blue-50 dark:hover:bg-blue-950/30 transition-colors"
            >
                <div className="flex items-center gap-2.5">
                    <HelpCircle className="w-4 h-4 text-blue-500 dark:text-blue-400 shrink-0" />
                    <span className="text-sm font-medium text-blue-700 dark:text-blue-300">{title}</span>
                    <span className="hidden md:block text-xs text-blue-500/70 dark:text-blue-400/60">— {summary}</span>
                </div>
                {open
                    ? <ChevronUp className="w-4 h-4 text-blue-400 shrink-0" />
                    : <ChevronDown className="w-4 h-4 text-blue-400 shrink-0" />
                }
            </button>

            {open && (
                <div className="px-5 pb-5 border-t border-blue-100 dark:border-blue-900/40">
                    <p className="text-sm text-slate-600 dark:text-slate-400 mt-4 mb-4 leading-relaxed">{summary}</p>

                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3 mb-4">
                        {items.map((item, i) => (
                            <div key={i} className="bg-white dark:bg-slate-900/60 rounded-lg p-3.5 border border-slate-100 dark:border-slate-800">
                                <div className="flex items-center gap-2 mb-1.5">
                                    {item.icon && <span className="text-blue-500 dark:text-blue-400">{item.icon}</span>}
                                    <span className="text-sm font-semibold text-slate-800 dark:text-slate-200">{item.label}</span>
                                </div>
                                <p className="text-xs text-slate-500 dark:text-slate-400 leading-relaxed">{item.description}</p>
                            </div>
                        ))}
                    </div>

                </div>
            )}
        </div>
    );
};

export default PageHelper;

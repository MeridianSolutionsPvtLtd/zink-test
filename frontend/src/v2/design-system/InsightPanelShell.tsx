import React from 'react';
import { Brain, Sparkles } from 'lucide-react';
import { cardClass } from './tokens';

type Insight = {
  icon: React.ElementType;
  label: string;
  value: string;
  tone?: 'default' | 'success' | 'warning' | 'danger';
};

const tones = {
  default: 'border-slate-100 bg-slate-50/50 dark:border-slate-800 dark:bg-slate-900/40',
  success: 'border-emerald-100 bg-emerald-50/50 dark:border-emerald-900/40 dark:bg-emerald-950/20',
  warning: 'border-amber-100 bg-amber-50/50 dark:border-amber-900/40 dark:bg-amber-950/20',
  danger: 'border-rose-100 bg-rose-50/50 dark:border-rose-900/40 dark:bg-rose-950/20',
};

export function InsightPanelShell({
  title = 'AI Business Insights',
  subtitle = 'Powered by StockPulse Analytics',
  items,
  footer,
}: {
  title?: string;
  subtitle?: string;
  items: Insight[];
  footer?: React.ReactNode;
}) {
  return (
    <section className={`overflow-hidden ${cardClass}`}>
      <div className="border-b border-blue-100 bg-gradient-to-r from-blue-600 to-blue-700 px-5 py-4 dark:border-blue-900">
        <div className="flex items-center gap-2 text-white">
          <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-white/20 backdrop-blur">
            <Brain className="h-4 w-4" />
          </div>
          <div>
            <h3 className="text-sm font-bold">{title}</h3>
            <p className="text-[10px] text-blue-100">{subtitle}</p>
          </div>
          <Sparkles className="ml-auto h-4 w-4 text-blue-200" />
        </div>
      </div>
      <div className="grid gap-3 p-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {items.map((item) => {
          const Icon = item.icon;
          return (
            <div key={item.label} className={`rounded-xl border p-2.5 transition hover:shadow-sm ${tones[item.tone ?? 'default']}`}>
              <div className="flex items-start gap-2">
                <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-blue-600" />
                <div className="min-w-0 flex-1">
                  <p className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">{item.label}</p>
                  <p className="text-xs font-bold text-slate-900 dark:text-white">{item.value}</p>
                </div>
              </div>
            </div>
          );
        })}
        {footer}
      </div>
    </section>
  );
}

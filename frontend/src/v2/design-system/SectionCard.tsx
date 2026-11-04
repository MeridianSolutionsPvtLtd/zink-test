import React from 'react';
import { Loader2 } from 'lucide-react';
import { cardClass, cardHoverClass } from './tokens';

type Props = {
  key?: React.Key;
  title: string;
  subtitle?: string;
  action?: React.ReactNode;
  loading?: boolean;
  children: React.ReactNode;
  className?: string;
  noPadding?: boolean;
  hover?: boolean;
};

/** Unified section / chart card — 16px radius, soft shadow */
export function SectionCard({
  title,
  subtitle,
  action,
  loading,
  children,
  className = '',
  noPadding,
  hover = true,
}: Props) {
  return (
    <div className={`min-w-0 ${cardClass} ${hover ? cardHoverClass : ''} ${className}`}>
      <div className="flex items-start justify-between gap-3 border-b border-slate-100/80 px-5 py-4 dark:border-slate-800/80">
        <div>
          <h3 className="text-sm font-semibold tracking-tight text-slate-900 dark:text-white">{title}</h3>
          {subtitle && <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">{subtitle}</p>}
        </div>
        {action}
      </div>
      <div className={`relative ${noPadding ? '' : 'p-4 pt-3'}`}>
        {loading && (
          <div className="absolute inset-0 z-10 flex items-center justify-center rounded-b-2xl bg-white/70 dark:bg-slate-900/70">
            <Loader2 className="h-7 w-7 animate-spin text-blue-500" />
          </div>
        )}
        <div className={loading ? 'pointer-events-none opacity-40' : ''}>{children}</div>
      </div>
    </div>
  );
}

/** @deprecated Use SectionCard — alias for backward compatibility */
export const ChartCard = SectionCard;

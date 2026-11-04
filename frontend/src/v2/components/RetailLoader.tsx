import React from 'react';
import { Store, Barcode, Package } from 'lucide-react';

export type RetailLoaderSize = 'sm' | 'md' | 'lg';

const DEFAULT_LABELS = [
  'Syncing live shelf & stock signals…',
  'Mapping stores, SKUs & warehouse cover…',
  'Getting your retail network picture ready…',
];

type RetailLoaderProps = {
  size?: RetailLoaderSize;
  /** Overrides rotating hint text */
  label?: string;
  /** `start` = pin content to the left (wide scroll areas); default centered in its box */
  align?: 'center' | 'start';
};

/**
 * Retail-themed loading state — emerald / amber POS-style cues.
 */
export function RetailLoader({ size = 'md', label, align = 'center' }: RetailLoaderProps) {
  const [hintIdx, setHintIdx] = React.useState(0);

  React.useEffect(() => {
    if (label) return;
    const id = window.setInterval(() => {
      setHintIdx((i) => (i + 1) % DEFAULT_LABELS.length);
    }, 1600);
    return () => window.clearInterval(id);
  }, [label]);

  const hint = label ?? DEFAULT_LABELS[hintIdx];

  const wrap =
    size === 'lg'
      ? 'min-h-[220px] gap-5 py-8'
      : size === 'sm'
        ? 'min-h-[72px] gap-2 py-3'
        : 'min-h-[180px] gap-4 py-6';

  const iconWrap =
    size === 'lg'
      ? 'h-[4.5rem] w-[4.5rem]'
      : size === 'sm'
        ? 'h-11 w-11'
        : 'h-16 w-16';

  const iconClass =
    size === 'lg' ? 'h-10 w-10' : size === 'sm' ? 'h-5 w-5' : 'h-8 w-8';

  const cross = align === 'start' ? 'items-start' : 'items-center';
  const shelfJustify = align === 'start' ? 'justify-start' : 'justify-center';
  const hintAlign = align === 'start' ? 'text-left' : 'text-center';

  return (
    <div
      className={`flex flex-col ${cross} justify-center px-4 ${wrap}`}
      role="status"
      aria-live="polite"
      aria-busy="true"
    >
      <div className="relative">
        <div className="pointer-events-none absolute -inset-4 rounded-3xl bg-gradient-to-tr from-emerald-400/30 via-amber-300/25 to-teal-400/25 blur-2xl animate-retail-pulse dark:from-emerald-500/20 dark:via-amber-400/15 dark:to-teal-500/15" />
        <div
          className={`relative flex ${iconWrap} items-center justify-center rounded-2xl border-2 border-emerald-300/70 bg-gradient-to-br from-white via-emerald-50/90 to-amber-50/50 shadow-xl dark:border-emerald-500/35 dark:from-slate-900 dark:via-emerald-950/50 dark:to-slate-900`}
        >
          <Store className={`${iconClass} text-emerald-600 dark:text-emerald-400 animate-retail-pulse`} strokeWidth={1.75} />
          <div className="absolute -right-1 -top-1 flex h-6 w-6 items-center justify-center rounded-lg bg-amber-400 text-white shadow-md dark:bg-amber-500">
            <Barcode className="h-3.5 w-3.5" strokeWidth={2.5} />
          </div>
        </div>
      </div>

      {size !== 'sm' && (
        <div className={`flex h-9 items-end gap-1 ${shelfJustify}`}>
          {[32, 52, 38, 68, 44, 58, 36].map((pct, i) => (
            <div
              key={i}
              className="w-1.5 rounded-t-md bg-gradient-to-t from-emerald-700 to-emerald-400 shadow-sm dark:from-emerald-400 dark:to-emerald-300/80 animate-retail-shelf"
              style={{ height: `${pct}%`, animationDelay: `${i * 0.06}s` }}
            />
          ))}
        </div>
      )}

      {size === 'sm' ? (
        <div className="flex items-center gap-2 text-[11px] font-semibold text-emerald-800 dark:text-emerald-200/90">
          <Package className="h-3.5 w-3.5 shrink-0 animate-retail-pulse text-amber-600 dark:text-amber-400" />
          <span className="uppercase tracking-wide">{hint}</span>
        </div>
      ) : (
        <p className={`max-w-sm text-[11px] font-bold uppercase leading-relaxed tracking-wide text-emerald-900/85 dark:text-emerald-100/90 ${hintAlign}`}>
          {hint}
        </p>
      )}
    </div>
  );
}

/** Shimmer block for KPI-style numeric placeholders */
export function RetailValueSkeleton({ className = '' }: { className?: string }) {
  return (
    <div className={`relative h-8 min-w-[5.5rem] overflow-hidden rounded-lg bg-gradient-to-r from-emerald-100/90 via-teal-100/70 to-amber-50/80 dark:from-emerald-950/50 dark:via-slate-800 dark:to-emerald-950/40 ${className}`}>
      <div className="absolute inset-y-0 left-0 w-2/5 bg-gradient-to-r from-transparent via-white/85 to-transparent dark:via-emerald-300/25 animate-retail-shimmer" />
    </div>
  );
}

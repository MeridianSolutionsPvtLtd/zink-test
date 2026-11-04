import React from 'react';
import { CheckCircle2, AlertTriangle, AlertCircle } from 'lucide-react';

export type FreshnessStatus = 'ok' | 'warning' | 'stale';

type Props = {
  /** e.g. "19 Aug 2026, 06:42 AM" — already formatted, or pass a Date via asOfDate */
  label?: string;
  asOfDate?: Date | null;
  status: FreshnessStatus;
  /** shown as native title tooltip, e.g. "Last synced 2 min ago" */
  tooltip?: string;
  compact?: boolean;
};

const STATUS_STYLE: Record<FreshnessStatus, { dot: string; text: string; Icon: React.ElementType; word: string }> = {
  ok: {
    dot: 'bg-emerald-500',
    text: 'text-emerald-700 dark:text-emerald-400',
    Icon: CheckCircle2,
    word: 'Up to date',
  },
  warning: {
    dot: 'bg-amber-500',
    text: 'text-amber-700 dark:text-amber-400',
    Icon: AlertTriangle,
    word: 'Needs attention',
  },
  stale: {
    dot: 'bg-rose-500',
    text: 'text-rose-700 dark:text-rose-400',
    Icon: AlertCircle,
    word: 'Sync missed',
  },
};

function formatAsOf(d: Date) {
  return d.toLocaleString('en-IN', {
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  });
}

/**
 * Small "Data as of ..." pill — drop into any dashboard's PageHeader (via the
 * `extra` slot) so users can see at a glance whether today's ERP sync landed.
 *
 * status should come from comparing the latest ingested date against "today"
 * (see DataSyncStatus.tsx for the freshness window used there):
 *   ok       — data includes today (or yesterday if sync hasn't run yet today)
 *   warning  — 1 day behind that window
 *   stale    — 2+ days behind, or the freshness API call failed
 */
export function DataFreshnessBadge({ label, asOfDate, status, tooltip, compact = false }: Props) {
  const { dot, text, Icon, word } = STATUS_STYLE[status];
  const shown = label ?? (asOfDate ? formatAsOf(asOfDate) : '—');

  return (
    <div
      title={tooltip ?? word}
      className={`inline-flex items-center gap-2 rounded-full border border-slate-200 bg-white px-3 py-1.5 dark:border-slate-700 dark:bg-slate-900 ${
        compact ? 'text-[10px]' : 'text-xs'
      }`}
    >
      <span className={`relative flex h-2 w-2 shrink-0`}>
        <span className={`absolute inline-flex h-full w-full animate-ping rounded-full ${dot} opacity-60`} />
        <span className={`relative inline-flex h-2 w-2 rounded-full ${dot}`} />
      </span>
      <Icon className={`h-3.5 w-3.5 shrink-0 ${text}`} />
      <span className="font-medium text-slate-500 dark:text-slate-400">Data as of</span>
      <span className={`font-semibold ${text}`}>{shown}</span>
    </div>
  );
}

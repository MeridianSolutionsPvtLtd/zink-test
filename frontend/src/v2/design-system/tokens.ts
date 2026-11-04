/** StockPulse Retail BI — unified design tokens */

export const colors = {
  primary: '#2563eb',
  primaryDark: '#1d4ed8',
  secondary: '#64748b',
  success: '#10b981',
  warning: '#f59e0b',
  danger: '#ef4444',
  bg: '#f4f7fb',
  card: '#ffffff',
  border: 'rgba(148, 163, 184, 0.35)',
} as const;

export const chartColors = {
  primary: '#2563eb',
  secondary: '#64748b',
  success: '#10b981',
  warning: '#f59e0b',
  danger: '#ef4444',
  cyan: '#06b6d4',
  violet: '#8b5cf6',
  palette: ['#2563eb', '#06b6d4', '#8b5cf6', '#10b981', '#f59e0b', '#ef4444', '#64748b', '#ec4899'],
} as const;

export const spacing = {
  page: 'space-y-4',
  section: 'gap-4',
  cardPadding: 'p-4',
  cardHeader: 'px-5 py-4',
} as const;

export const radius = {
  card: 'rounded-2xl', // 16px
  button: 'rounded-xl',
  badge: 'rounded-full',
} as const;

export const shadow = {
  card: 'shadow-[0_1px_3px_rgba(15,23,42,0.04),0_8px_24px_rgba(15,23,42,0.04)]',
  cardHover: 'hover:shadow-[0_2px_6px_rgba(15,23,42,0.06),0_12px_32px_rgba(37,99,235,0.08)]',
} as const;

export const kpiAccents = [
  'bg-gradient-to-br from-blue-600 to-blue-700',
  'bg-gradient-to-br from-sky-500 to-blue-600',
  'bg-gradient-to-br from-indigo-500 to-blue-600',
  'bg-gradient-to-br from-cyan-500 to-blue-500',
  'bg-gradient-to-br from-emerald-500 to-teal-600',
  'bg-gradient-to-br from-violet-500 to-indigo-600',
  'bg-gradient-to-br from-amber-500 to-orange-500',
  'bg-gradient-to-br from-rose-500 to-pink-600',
] as const;

export const cardClass =
  'rounded-2xl border border-slate-200/80 bg-white shadow-[0_1px_3px_rgba(15,23,42,0.04),0_8px_24px_rgba(15,23,42,0.04)] dark:border-slate-800/80 dark:bg-slate-900/70';

export const cardHoverClass =
  'transition-[transform,box-shadow,border-color] duration-300 hover:-translate-y-0.5 hover:border-blue-200/80 hover:shadow-[0_8px_30px_rgba(37,99,235,0.1)] dark:hover:border-blue-500/30';

export function formatRefreshTime(d: Date) {
  return d.toLocaleString('en-IN', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

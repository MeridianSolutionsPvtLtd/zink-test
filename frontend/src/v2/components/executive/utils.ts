function asFiniteNumber(value: unknown, fallback = 0): number {
  const n = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(n) ? n : fallback;
}

export function formatInr(value: number, compact = false) {
  const v = asFiniteNumber(value);
  if (compact) {
    if (Math.abs(v) >= 1_00_00_000) return `₹${(v / 1_00_00_000).toFixed(1)}Cr`;
    if (Math.abs(v) >= 1_00_000) return `₹${(v / 1_00_000).toFixed(1)}L`;
    if (Math.abs(v) >= 1_000) return `₹${(v / 1_000).toFixed(1)}K`;
  }
  return new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    maximumFractionDigits: 0,
  }).format(v);
}

export function formatNumber(value: number) {
  return new Intl.NumberFormat('en-IN').format(Math.round(asFiniteNumber(value)));
}

export function formatPct(value: number, digits = 1) {
  const v = asFiniteNumber(value);
  return `${v >= 0 ? '+' : ''}${v.toFixed(digits)}%`;
}

export function formatPctPlain(value: number, digits = 1) {
  return `${asFiniteNumber(value).toFixed(digits)}%`;
}

export function sparklinePath(values: number[], width = 64, height = 24): string {
  if (!values.length) return '';
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const step = width / Math.max(values.length - 1, 1);
  return values
    .map((v, i) => {
      const x = i * step;
      const y = height - ((v - min) / range) * height;
      return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');
}

export const ZONES = ['North', 'South', 'East', 'West', 'Central'] as const;
export const CHANNELS = ['Store', 'Online', 'Marketplace', 'Wholesale', 'LFS', 'EBO', 'MBO'] as const;
export const DEPARTMENTS = ['Men\'s Wear', 'Women\'s Wear', 'Kids', 'Footwear', 'Accessories', 'Ethnic'] as const;

export const CHART_COLORS = [
  '#6366f1', '#8b5cf6', '#a855f7', '#ec4899', '#f43f5e',
  '#f97316', '#eab308', '#22c55e', '#14b8a6', '#0ea5e9',
];

import React from 'react';
import {
  Boxes,
  Building2,
  Gauge,
  IndianRupee,
  Package,
  Percent,
  Sparkles,
  Target as TargetIcon,
  TrendingUp,
  Zap,
} from 'lucide-react';
import { EnterpriseKpiCard, KpiGridShell, KpiSkeletonGrid } from '../../design-system';
import { formatCompactInr, formatInr, type MonthGrowth } from './salesAnalytics';

type GrowthRates = {
  goly: number | null;
  golyUnits?: number | null;
  golm: number | null;
  golw: number | null;
};

export type TargetGrain = 'month' | 'ytd' | 'year';

type KpiData = {
  gross: number;
  net: number;
  discount: number;
  /** Prefers backend discount_pct; UI must not invent ratios when this is set */
  discountPct?: number;
  gst: number;
  units: number;
  mrpValue: number;
  costValue: number;
  asp: number;
  trend: number[];
  target?: number;
  /** True when selected site/scope has no Target LFS rows (show "Target not available"). */
  targetUnavailable?: boolean;
  targetAchievementPct?: number | null;
  /** Achieved / received sales vs Target LFS (same period) */
  targetActual?: number;
  targetPeriodLabel?: string;
  targetSitesWithTarget?: number;
  targetSitesWithAchieved?: number;
  currentSoh?: number;
  sellThroughPct?: number;
  fullPricePct?: number;
  fullPriceSales?: number;
  fullPriceQty?: number;
  fullPriceStrPct?: number;
  discountSales?: number;
  discountQty?: number;
  discountPricePct?: number;
  discountStrPct?: number;
  salesPerStock?: number;
  salesPerSqft?: number;
  sqftTotal?: number;
  sqftStoresCount?: number;
  sqftNetSales?: number;
  yoySalesGrowthPct?: number | null;
  yoyQtyGrowthPct?: number | null;
  ros?: number;
  monthGrowth: MonthGrowth | null;
  growthRates?: GrowthRates;
};

function changeFor(g: MonthGrowth | null, key: keyof MonthGrowth): number | undefined {
  const v = g?.[key];
  return v === null || v === undefined ? undefined : v;
}

export function SalesKpiGrid({
  data,
  loading,
  targetLoading = false,
  targetGrain = 'ytd',
  onTargetGrainChange,
  showTarget = true,
}: {
  data: KpiData;
  loading: boolean;
  /** Target / Achievement load separately — show spinner on those cards until ready */
  targetLoading?: boolean;
  targetGrain?: TargetGrain;
  onTargetGrainChange?: (g: TargetGrain) => void;
  /** Set false to hide the Target and Target Achievement cards. */
  showTarget?: boolean;
}) {
  if (loading) {
    return <KpiSkeletonGrid count={showTarget ? 10 : 8} />;
  }

  // Prefer backend discount_pct only — do not invent % from gross/discount on UI
  const discountPct =
    data.discountPct != null && Number.isFinite(data.discountPct) ? data.discountPct : null;
  const periodHint = data.targetPeriodLabel ? ` · ${data.targetPeriodLabel}` : '';
  const siteScopeHint =
    data.targetSitesWithTarget != null
      ? ` · ${data.targetSitesWithAchieved ?? 0}/${data.targetSitesWithTarget} Target LFS sites with sales`
      : '';

  const formatSoh = (soh?: number) => {
    // null = no movement rows for scope. 0 = known zero. Negative = recon issue — still show it
    // (hiding negatives as "—" made P033 look like SOH was missing).
    if (soh == null || Number.isNaN(soh)) return '—';
    if (soh === 0) return '—';
    const abs = Math.abs(soh);
    const body =
      abs >= 100_000 ? `${(abs / 100_000).toFixed(2)}L pcs` : `${abs.toLocaleString('en-IN')} pcs`;
    return soh < 0 ? `−${body}` : body;
  };

  const fpStrValue =
    data.fullPriceStrPct != null && data.fullPriceStrPct > 0
      ? `${data.fullPriceStrPct.toFixed(1)}% · ${formatCompactInr(data.fullPriceSales || 0)}`
      : data.fullPricePct != null && data.fullPricePct > 0
        ? `${data.fullPricePct.toFixed(1)}% · ${formatCompactInr(data.fullPriceSales || 0)}`
        : '—';

  const spsfValue =
    data.salesPerSqft != null && data.salesPerSqft > 0
      ? `${formatInr(data.salesPerSqft)} / sq ft`
      : '—';

  const spsfTooltip =
    data.sqftTotal != null && data.sqftTotal > 0
      ? `Sales per Sq Ft (SPSF) = ${formatCompactInr(data.sqftNetSales || 0)} Net Sales ÷ ${data.sqftTotal.toLocaleString('en-IN')} Sq Ft across ${data.sqftStoresCount || 0} stores with known area. Filter-responsive for any selected Zone, City, State, Channel, or Store.`
      : 'Sales per Sq Ft (SPSF): No stores in this filtered view have square feet defined in Customer Master.';

  const netYoy = data.yoySalesGrowthPct === undefined ? (data.growthRates?.goly ?? undefined) : (data.yoySalesGrowthPct ?? undefined);
  const qtyYoy = data.yoyQtyGrowthPct === undefined ? (data.growthRates?.golyUnits ?? undefined) : (data.yoyQtyGrowthPct ?? undefined);

  const achPct = data.targetAchievementPct;
  const achTone: 'good' | 'warn' | 'bad' | undefined =
    achPct == null
      ? undefined
      : achPct >= 100
        ? 'good'
        : achPct >= 80
          ? 'warn'
          : 'bad';
  const achAccent =
    achPct == null
      ? 'from-emerald-500 to-teal-600'
      : achPct >= 100
        ? 'from-emerald-500 to-teal-600'
        : achPct >= 80
          ? 'from-amber-500 to-orange-500'
          : 'from-rose-500 to-red-600';

  const cards = [
    {
      key: 'net',
      title: 'Net Sales',
      icon: TrendingUp,
      accent: 'from-blue-600 to-indigo-600',
      value: formatCompactInr(data.net),
      change: netYoy,
      tooltip: 'Net revenue after discounts and returns — compared with Same Period Last Year (SPLY).',
      loading: false,
    },
    {
      key: 'target',
      title: 'Target',
      icon: TargetIcon,
      accent: 'from-slate-600 to-slate-800',
      value:
        data.target != null && data.target > 0
          ? formatCompactInr(data.target)
          : data.targetUnavailable || data.target == null || data.target === 0
            ? 'Target not available'
            : '—',
      previous:
        data.target != null && data.target > 0 && data.targetSitesWithTarget != null
          ? `${data.targetSitesWithTarget} Target LFS stores`
          : undefined,
      tooltip: targetLoading
        ? 'Loading Target LFS for this period…'
        : !(data.target != null && data.target > 0)
          ? 'No Target LFS rows for this site/period. Target file mein yeh site nahi hai — Target not available.'
          : `Target LFS (rupee)${periodHint}${siteScopeHint}. Only sites with a target in LFS are included.`,
      loading: targetLoading,
    },
    {
      key: 'achievement',
      title: 'Target Achievement %',
      icon: Gauge,
      accent: achAccent,
      value:
        data.target != null && data.target > 0 && data.targetAchievementPct != null
          ? data.targetActual != null
            ? `${Number(data.targetAchievementPct).toFixed(1)}% · ${formatCompactInr(data.targetActual)}`
            : `${Number(data.targetAchievementPct).toFixed(1)}%`
          : data.targetUnavailable || data.target == null || data.target === 0
            ? 'Target not available'
            : '—',
      previous:
        data.target != null && data.target > 0
          ? `Target ${formatCompactInr(data.target)}${siteScopeHint ? ` · ${data.targetSitesWithAchieved ?? 0}/${data.targetSitesWithTarget} stores` : ''}`
          : undefined,
      valueTone: achTone,
      tooltip: targetLoading
        ? 'Loading Target Achievement…'
        : !(data.target != null && data.target > 0)
          ? 'Achievement needs Target LFS for the selected site/period. Jab target nahi hai to Achievement bhi N/A.'
          : `Received ${
              data.targetActual != null ? formatCompactInr(data.targetActual) : '—'
            } vs Target ${
              data.target != null ? formatCompactInr(data.target) : '—'
            }${periodHint}${siteScopeHint}. Only Target LFS sites — Achievement = Received ÷ Target × 100.`,
      loading: targetLoading,
    },
    {
      key: 'units',
      title: 'Quantity Sold',
      icon: Package,
      accent: 'from-cyan-500 to-blue-600',
      value: `${data.units.toLocaleString('en-IN')} pcs`,
      change: qtyYoy,
      tooltip: 'Total units sold across stores and channels in the filtered period — compared with Same Period Last Year (SPLY).',
    },
    {
      key: 'current_soh',
      title: 'Current SOH',
      icon: Boxes,
      accent: 'from-violet-600 to-purple-700',
      value: formatSoh(data.currentSoh),
      tooltip:
        data.currentSoh != null && data.currentSoh < 0
          ? 'Current SOH is negative in Daily Inventory Movement (recon/over-sell). Value is shown as-is from latest closing stock.'
          : 'Current SOH from Daily Inventory Movement — latest closing stock per store × barcode for the active filters (zone/site/SKU/barcode/season/…). Not from COMBINED_SOH / SOH-Master.',
      loading: false,
    },
    {
      key: 'sell_through',
      title: 'Sell-Through % (STR)',
      icon: Percent,
      accent: 'from-emerald-600 to-green-700',
      value: data.sellThroughPct != null && data.sellThroughPct > 0 ? `${data.sellThroughPct.toFixed(1)}%` : '—',
      tooltip: 'STR % = Cum Net Sale Qty ÷ (Opening + Dispatch In + IBT In + Sales Return) × 100. From Daily Inventory Movement.',
      loading: false,
    },
    {
      key: 'full_price_str',
      title: 'Full Price STR % (≤10% Disc)',
      icon: Sparkles,
      accent: 'from-indigo-500 to-sky-600',
      value: fpStrValue,
      tooltip: 'Full Price STR % = Sales generated at ≤ 10% discount (Fresh / Regular price) as a % of stock.',
      loading: false,
    },
    {
      key: 'sales_per_sqft',
      title: 'Sales / Sq Ft',
      icon: Building2,
      accent: 'from-amber-500 to-orange-600',
      value: spsfValue,
      tooltip: spsfTooltip,
      loading: false,
    },
    {
      key: 'asp',
      title: 'ASP',
      icon: IndianRupee,
      accent: 'from-teal-600 to-cyan-700',
      value: formatInr(data.asp),
      change: changeFor(data.monthGrowth, 'asp'),
      tooltip: 'Average Selling Price = Net Sales ÷ Quantity Sold. Higher ASP usually means richer mix or less discounting.',
    },
    {
      key: 'discount',
      title: 'Discount % / Value',
      icon: Percent,
      accent: 'from-orange-500 to-rose-600',
      value:
        discountPct != null
          ? `${discountPct.toFixed(1)}% · ${formatCompactInr(data.discount)}`
          : data.discount > 0
            ? formatCompactInr(data.discount)
            : '—',
      change: changeFor(data.monthGrowth, 'discount'),
      tooltip: 'Discount % of gross sales and absolute discount value given (from backend).',
    },
  ];

  return (
    <div className="space-y-2">
      <KpiGridShell>
        {(showTarget ? cards : cards.filter((card) => card.key !== 'target' && card.key !== 'achievement')).map((card) => {
          const Icon = card.icon;
          return (
            <EnterpriseKpiCard
              key={card.key}
              compact
              title={card.title}
              value={card.value}
              previous={'previous' in card ? card.previous : undefined}
              change={'change' in card ? card.change : undefined}
              icon={Icon}
              accent={`bg-gradient-to-br ${card.accent}`}
              tooltip={card.tooltip}
              loading={'loading' in card ? card.loading : false}
              valueTone={'valueTone' in card ? card.valueTone : undefined}
            />
          );
        })}
      </KpiGridShell>
    </div>
  );
}

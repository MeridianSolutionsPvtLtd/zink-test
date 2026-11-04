import React from 'react';
import {
  Gauge,
  IndianRupee,
  Package,
  Percent,
  Target,
  TrendingUp,
} from 'lucide-react';
import { EnterpriseKpiCard, KpiGridShell, KpiSkeletonGrid, SalesGrowthKpiCard, type GrowthRates } from '../../design-system';
import { formatInr, formatNumber, formatPctPlain } from './utils';

export type ExecutiveKpi = {
  id: string;
  title: string;
  value: string;
  comparison: string;
  change?: number;
  trend: number[];
  icon: React.ElementType;
  accent: string;
  tooltip: string;
};

type Props = {
  kpis: ExecutiveKpi[];
  growth?: GrowthRates;
  loading?: boolean;
};

export function ExecutiveKpiCards({ kpis, growth, loading }: Props) {
  if (loading) return <KpiSkeletonGrid count={8} cols="grid-cols-4 xl:grid-cols-8" />;

  return (
    <KpiGridShell cols="grid-cols-2 sm:grid-cols-4 xl:grid-cols-8">
      {kpis.map((kpi) => {
        const Icon = kpi.icon;
        return (
          <EnterpriseKpiCard
            key={kpi.id}
            title={kpi.title}
            value={kpi.value}
            previous={kpi.comparison}
            change={kpi.change}
            icon={Icon}
            accent={kpi.accent}
            tooltip={kpi.tooltip}
          />
        );
      })}
      {growth && <SalesGrowthKpiCard growth={growth} />}
    </KpiGridShell>
  );
}

export function buildExecutiveKpis(data: {
  grossSales: number;
  netSales: number;
  target: number;
  unitsSold: number;
  discountPct: number;
  totalOrders: number;
  sellThrough: number;
  salesToStock: number;
  inventoryValue: number;
  fillRate: number;
  oosRate: number;
  warehouseStock: number;
  storeStock: number;
  monthlyTrend: number[];
  yoyGrowth?: number;
  unitsYoyGrowth?: number;
}): { kpis: ExecutiveKpi[]; growth: GrowthRates } {
  const achievement = data.target > 0 ? (data.netSales / data.target) * 100 : 0;
  const asp = data.unitsSold > 0 ? data.netSales / data.unitsSold : 0;
  const discountValue = data.grossSales * (data.discountPct / 100);
  const trend = data.monthlyTrend.length ? data.monthlyTrend : [];
  const n = trend.length;

  const golm =
    n >= 2 && trend[n - 2]
      ? ((trend[n - 1] - trend[n - 2]) / Math.abs(trend[n - 2])) * 100
      : null;
  const goly =
    data.yoyGrowth !== undefined && Number.isFinite(data.yoyGrowth)
      ? data.yoyGrowth
      : n >= 12 && trend[n - 12]
        ? ((trend[n - 1] - trend[n - 12]) / Math.abs(trend[n - 12])) * 100
        : null;
  // WoW needs a real weekly series — never invent from MoM
  const golw = null;

  const mk = (
    id: string,
    title: string,
    value: string,
    comparison: string,
    change: number | undefined,
    icon: React.ElementType,
    accent: string,
    tooltip: string
  ): ExecutiveKpi => ({
    id,
    title,
    value,
    comparison,
    change,
    trend,
    icon,
    accent,
    tooltip,
  });

  const momChange = golm !== null && Number.isFinite(golm) ? golm : undefined;
  const netYoyChange = data.yoyGrowth !== undefined && Number.isFinite(data.yoyGrowth) ? data.yoyGrowth : momChange;
  const qtyYoyChange = data.unitsYoyGrowth !== undefined && Number.isFinite(data.unitsYoyGrowth) ? data.unitsYoyGrowth : undefined;

  const kpis: ExecutiveKpi[] = [
    mk('gross', 'Gross Sales', formatInr(data.grossSales, true), 'vs last period', momChange, IndianRupee, 'bg-gradient-to-br from-blue-600 to-blue-700', 'Total billed sales before returns and discounts'),
    mk('net', 'Net Sales', formatInr(data.netSales, true), data.yoyGrowth !== undefined ? 'YoY growth (SPLY)' : 'vs last period', netYoyChange, TrendingUp, 'bg-gradient-to-br from-sky-500 to-blue-600', 'Net revenue after discounts and returns — compared with Same Period Last Year (SPLY)'),
    mk('target', 'Target', formatInr(data.target, true), 'Monthly plan', undefined, Target, 'bg-gradient-to-br from-slate-600 to-slate-800', 'Revenue target for selected period'),
    mk('achievement', 'Target Achievement %', formatPctPlain(achievement), 'Of monthly target', undefined, Gauge, 'bg-gradient-to-br from-emerald-500 to-teal-600', 'Net sales as percentage of target'),
    mk('qty', 'Quantity Sold', formatNumber(data.unitsSold), data.unitsYoyGrowth !== undefined ? 'YoY growth (SPLY)' : 'Units moved', qtyYoyChange, Package, 'bg-gradient-to-br from-cyan-500 to-blue-600', 'Total units sold across all channels — compared with Same Period Last Year (SPLY)'),
    mk('asp', 'ASP', formatInr(asp), 'Avg selling price', undefined, IndianRupee, 'bg-gradient-to-br from-indigo-500 to-blue-600', 'Average selling price per unit'),
    mk(
      'discount',
      'Discount % / Value',
      `${formatPctPlain(data.discountPct)} · ${formatInr(discountValue, true)}`,
      'On gross sales',
      undefined,
      Percent,
      'bg-gradient-to-br from-amber-500 to-orange-600',
      'Discount percentage and absolute discount value'
    ),
  ];

  return {
    kpis,
    growth: {
      goly,
      golm,
      golw,
    },
  };
}

import React from 'react';
import {
  Boxes,
  Gauge,
  Percent,
  Target,
  TrendingUp,
  Truck,
} from 'lucide-react';
import { EnterpriseKpiCard, KpiGridShell, KpiSkeletonGrid } from '../../design-system';
import { formatCompact, type ForecastKpiData } from './forecastAnalytics';

const cards = [
  { key: 'forecast', title: 'Forecast', icon: TrendingUp, accent: 'from-blue-600 to-blue-700', fmt: (d: ForecastKpiData) => formatCompact(d.forecast), prev: () => 'Current forecast' },
  { key: 'target', title: 'Target', icon: Target, accent: 'from-indigo-500 to-blue-600', fmt: (d: ForecastKpiData) => formatCompact(d.target), prev: () => 'Plan target' },
  { key: 'accuracy', title: 'Forecast Accuracy %', icon: Gauge, accent: 'from-cyan-500 to-blue-500', fmt: (d: ForecastKpiData) => `${d.accuracy.toFixed(1)}%`, prev: () => 'Model accuracy' },
  { key: 'variance', title: 'Variance', icon: Percent, accent: 'from-amber-500 to-orange-500', fmt: (d: ForecastKpiData) => `${d.variance.toFixed(1)}%`, prev: () => 'Forecast vs actual' },
  { key: 'inv', title: 'Inventory Target', icon: Boxes, accent: 'from-violet-500 to-indigo-600', fmt: (d: ForecastKpiData) => formatCompact(d.inventoryTarget), prev: () => 'Inventory plan' },
  { key: 'demand', title: 'Demand', icon: TrendingUp, accent: 'from-emerald-500 to-teal-600', fmt: (d: ForecastKpiData) => formatCompact(d.demand), prev: () => 'Projected demand' },
  { key: 'supply', title: 'Supply', icon: Truck, accent: 'from-sky-500 to-blue-600', fmt: (d: ForecastKpiData) => formatCompact(d.supply), prev: () => 'Projected supply' },
  { key: 'bias', title: 'Forecast Bias', icon: Percent, accent: 'from-rose-500 to-pink-600', fmt: (d: ForecastKpiData) => `${d.bias.toFixed(1)}%`, prev: () => 'Bias' },
  {
    key: 'growth',
    title: 'Demand Growth %',
    icon: TrendingUp,
    accent: 'from-blue-500 to-cyan-500',
    fmt: (d: ForecastKpiData) =>
      d.demandGrowth != null && Number.isFinite(d.demandGrowth)
        ? `${d.demandGrowth > 0 ? '+' : ''}${d.demandGrowth.toFixed(1)}%`
        : '—',
    prev: () => 'From planning input',
    change: (d: ForecastKpiData) =>
      d.demandGrowth != null && Number.isFinite(d.demandGrowth) ? d.demandGrowth : undefined,
  },
  { key: 'coverage', title: 'Supply Coverage %', icon: Gauge, accent: 'from-blue-600 to-indigo-600', fmt: (d: ForecastKpiData) => `${d.supplyCoverage.toFixed(1)}%`, prev: () => 'Coverage' },
] as const;

export function ForecastKpiGrid({ data, loading }: { data: ForecastKpiData; loading: boolean }) {
  if (loading) return <KpiSkeletonGrid count={10} />;

  return (
    <KpiGridShell>
      {cards.map((card) => {
        const Icon = card.icon;
        const negativeGood = card.key === 'variance' || card.key === 'bias';
        const change = 'change' in card ? card.change(data) : undefined;
        return (
          <EnterpriseKpiCard
            key={card.key}
            title={card.title}
            value={card.fmt(data)}
            previous={card.prev()}
            change={change}
            icon={Icon}
            accent={`bg-gradient-to-br ${card.accent}`}
            tooltip={card.prev()}
            invertGrowth={negativeGood}
            compact
          />
        );
      })}
    </KpiGridShell>
  );
}

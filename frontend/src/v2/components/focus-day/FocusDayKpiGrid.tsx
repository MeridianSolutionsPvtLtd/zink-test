import React from 'react';
import {
  CalendarDays,
  IndianRupee,
  ShoppingBag,
  TrendingUp,
} from 'lucide-react';
import { EnterpriseKpiCard, KpiGridShell, KpiSkeletonGrid } from '../../design-system';
import { formatCompactInr, formatInr } from './focusDayData';

export type FestivalSummaryKpis = {
  sales2024: number;
  sales2025: number;
  sales2026: number;
  growth2025vs2024: number | null;
  growth2026vs2025: number | null;
  qty2025: number;
  qty2026: number;
  asp2025: number;
  asp2026: number;
  eventsTracked?: number;
};

function growthLabel(v: number | null) {
  if (v === null || v === undefined) return '—';
  return `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`;
}

export function FocusDayKpiGrid({
  data,
  loading,
}: {
  data: FestivalSummaryKpis | null;
  loading?: boolean;
}) {
  if (loading || !data) {
    return <KpiSkeletonGrid count={8} />;
  }

  const cards: Array<{
    key: string;
    title: string;
    icon: React.ElementType;
    accent: string;
    value: string;
    previous: string;
    change?: number;
  }> = [
    {
      key: 's24',
      title: 'Festive Sales 2024',
      icon: IndianRupee,
      accent: 'from-slate-500 to-slate-700',
      value: formatCompactInr(data.sales2024),
      previous: 'All festival windows',
    },
    {
      key: 's25',
      title: 'Festive Sales 2025',
      icon: IndianRupee,
      accent: 'from-blue-600 to-blue-700',
      value: formatCompactInr(data.sales2025),
      previous: growthLabel(data.growth2025vs2024) + ' vs 2024',
      change:
        data.growth2025vs2024 != null && Number.isFinite(data.growth2025vs2024)
          ? data.growth2025vs2024
          : undefined,
    },
    {
      key: 's26',
      title: 'Festive Sales 2026',
      icon: IndianRupee,
      accent: 'from-indigo-500 to-blue-600',
      value: formatCompactInr(data.sales2026),
      previous: growthLabel(data.growth2026vs2025) + ' vs 2025',
      change:
        data.growth2026vs2025 != null && Number.isFinite(data.growth2026vs2025)
          ? data.growth2026vs2025
          : undefined,
    },
    {
      key: 'g25',
      title: 'YoY Growth 25 vs 24',
      icon: TrendingUp,
      accent: 'from-emerald-500 to-teal-600',
      value: growthLabel(data.growth2025vs2024),
      previous: 'Sales growth',
      change:
        data.growth2025vs2024 != null && Number.isFinite(data.growth2025vs2024)
          ? data.growth2025vs2024
          : undefined,
    },
    {
      key: 'qty25',
      title: 'Qty Sold 2025',
      icon: ShoppingBag,
      accent: 'from-sky-500 to-blue-600',
      value: data.qty2025.toLocaleString('en-IN'),
      previous: `ASP ${formatInr(data.asp2025)}`,
    },
    {
      key: 'qty26',
      title: 'Qty Sold 2026',
      icon: ShoppingBag,
      accent: 'from-cyan-500 to-blue-500',
      value: data.qty2026.toLocaleString('en-IN'),
      previous: `ASP ${formatInr(data.asp2026)}`,
    },
    {
      key: 'asp25',
      title: 'ASP 2025',
      icon: IndianRupee,
      accent: 'from-amber-500 to-orange-500',
      value: formatInr(data.asp2025),
      previous: 'Festival windows',
    },
    {
      key: 'cal',
      title: 'Events Tracked',
      icon: CalendarDays,
      accent: 'from-violet-500 to-indigo-600',
      value: String(data.eventsTracked ?? '—'),
      previous: 'From festival comparison API',
    },
  ];

  return (
    <KpiGridShell>
      {cards.map((card) => {
        const Icon = card.icon;
        return (
          <EnterpriseKpiCard
            key={card.key}
            title={card.title}
            value={card.value}
            previous={card.previous}
            change={card.change}
            icon={Icon}
            accent={`bg-gradient-to-br ${card.accent}`}
            tooltip={card.previous}
            compact
          />
        );
      })}
    </KpiGridShell>
  );
}

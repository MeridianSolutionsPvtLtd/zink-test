import React from 'react';
import {
  Boxes,
  Gauge,
  IndianRupee,
  Percent,
  ShoppingBag,
  TrendingUp,
} from 'lucide-react';
import { EnterpriseKpiCard, KpiGridShell } from '../../design-system';
import {
  formatCompactInr,
  formatInr,
  type ProductKpiData,
} from './productData';

const cards = [
  { key: 'sales', title: 'Sales', icon: IndianRupee, accent: 'from-blue-600 to-blue-700', fmt: (d: ProductKpiData) => formatCompactInr(d.sales), prev: () => 'Current period' },
  { key: 'qty', title: 'Quantity Sold', icon: ShoppingBag, accent: 'from-sky-500 to-blue-600', fmt: (d: ProductKpiData) => d.qty.toLocaleString('en-IN'), prev: () => 'Units' },
  { key: 'stock', title: 'Stock', icon: Boxes, accent: 'from-blue-500 to-indigo-600', fmt: (d: ProductKpiData) => d.stock.toLocaleString('en-IN'), prev: () => 'On hand' },
  { key: 'inv', title: 'Inventory Value', icon: Boxes, accent: 'from-indigo-500 to-blue-600', fmt: (d: ProductKpiData) => formatCompactInr(d.invValue), prev: () => 'Stock value' },
  { key: 'st', title: 'Sell Through %', icon: TrendingUp, accent: 'from-cyan-500 to-blue-500', fmt: (d: ProductKpiData) => `${d.sellThrough.toFixed(1)}%`, prev: () => 'Sell-through' },
  { key: 'margin', title: 'Margin', icon: IndianRupee, accent: 'from-emerald-500 to-teal-600', fmt: (d: ProductKpiData) => formatCompactInr(d.margin), prev: () => 'Margin' },
  { key: 'disc', title: 'Discount %', icon: Percent, accent: 'from-amber-500 to-orange-500', fmt: (d: ProductKpiData) => `${d.discountPct.toFixed(1)}%`, prev: () => 'Discount' },
  { key: 'asp', title: 'ASP', icon: IndianRupee, accent: 'from-blue-500 to-cyan-500', fmt: (d: ProductKpiData) => formatInr(d.asp), prev: () => 'Avg selling price' },
  { key: 'wc', title: 'Week Cover', icon: Gauge, accent: 'from-rose-500 to-pink-600', fmt: (d: ProductKpiData) => `${d.weekCover.toFixed(1)}w`, prev: () => 'Cover weeks' },
  { key: 'ros', title: 'ROS', icon: TrendingUp, accent: 'from-blue-600 to-indigo-600', fmt: (d: ProductKpiData) => d.ros.toFixed(1), prev: () => 'Rate of sale' },
] as const;

export function ProductKpiGrid({ data }: { data: ProductKpiData }) {
  return (
    <KpiGridShell>
      {cards.map((card) => {
        const Icon = card.icon;
        const negativeGood = card.key === 'disc' || card.key === 'wc';
        return (
          <EnterpriseKpiCard
            key={card.key}
            title={card.title}
            value={card.fmt(data)}
            previous={card.prev()}
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

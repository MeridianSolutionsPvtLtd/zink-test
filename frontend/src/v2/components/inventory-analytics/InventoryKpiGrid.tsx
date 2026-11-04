import React from 'react';
import {
  Boxes,
  Clock,
  Target,
  Truck,
  Warehouse,
} from 'lucide-react';
import { EnterpriseKpiCard, KpiGridShell, KpiSkeletonGrid } from '../../design-system';
import {
  formatNumber,
  type InventoryKpiData,
  type InventoryScope,
} from './inventoryAnalytics';

type CardDef = {
  key: string;
  title: string;
  icon: React.ElementType;
  accent: string;
  fmt: (d: InventoryKpiData) => string;
  prev: (d: InventoryKpiData) => string;
  /** Real growth only — never invent */
  change?: (d: InventoryKpiData) => number | undefined;
  invertGrowth?: boolean;
};

const warehouseCards: CardDef[] = [
  {
    key: 'wh_close',
    title: 'Warehouse Closing Stocks',
    icon: Warehouse,
    accent: 'from-blue-600 to-blue-700',
    fmt: (d) => formatNumber(d.warehouseClosing),
    prev: () => 'Current closing',
  },
  {
    key: 'wh_dispatch',
    title: 'Warehouse Dispatch',
    icon: Truck,
    accent: 'from-indigo-500 to-blue-600',
    fmt: (d) => formatNumber(d.warehouseDispatch),
    prev: () => 'Current period',
  },
  {
    key: 'wh_lead',
    title: 'Dispatch Lead Time',
    icon: Clock,
    accent: 'from-amber-500 to-orange-500',
    fmt: (d) => `${d.dispatchLeadTimeDays.toFixed(1)} days`,
    prev: () => 'Avg lead time',
    invertGrowth: true,
  },
  {
    key: 'wh_inward',
    title: 'Daily Inward',
    icon: Boxes,
    accent: 'from-sky-500 to-blue-600',
    fmt: (d) => formatNumber(d.dailyInward),
    prev: () => 'Current period',
  },
  {
    key: 'wh_target',
    title: 'Inventory Target',
    icon: Target,
    accent: 'from-violet-500 to-indigo-600',
    fmt: (d) => formatNumber(d.inventoryTarget),
    prev: (d) => `${d.targetAchievement.toFixed(0)}% ach.`,
  },
];

/** Store Inventory: no KPI cards — Network SOH already shows closing qty in the table. */
const storeCards: CardDef[] = [];

export function InventoryKpiGrid({
  data,
  loading,
  scope,
}: {
  data: InventoryKpiData;
  loading: boolean;
  scope: InventoryScope;
}) {
  const cards = scope === 'warehouse' ? warehouseCards : storeCards;

  if (!cards.length) return null;

  if (loading) {
    return (
      <KpiSkeletonGrid
        count={cards.length}
      />
    );
  }

  return (
    <KpiGridShell>
      {cards.map((card) => {
        const Icon = card.icon;
        return (
          <EnterpriseKpiCard
            key={card.key}
            title={card.title}
            value={card.fmt(data)}
            previous={card.prev(data)}
            change={card.change?.(data)}
            icon={Icon}
            accent={`bg-gradient-to-br ${card.accent}`}
            tooltip={card.prev(data)}
            invertGrowth={card.invertGrowth}
          />
        );
      })}
    </KpiGridShell>
  );
}

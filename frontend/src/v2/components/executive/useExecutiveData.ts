import { useMemo } from 'react';
import { formatInr } from './utils';
import type { StoreRow, SkuRow, StyleCard, RegionMapData, CityMapData } from './ExecutiveSections';
import type { Insight, AlertItem } from './InsightsPanels';
import type { GlobalFilters, FilterOptions } from './ExecutiveHeader';
import { CITY_ANCHORS, ZONE_PATHS } from './indiaMapGeometry';
import {
  CLIENT_FILTER_OPTIONS,
  buildClientFilterOptionsFromMaster,
  type CustomerMasterFiltersResponse,
} from '../sales-dashboard/salesAnalytics';

type SalesKpis = {
  gross_sales: number;
  net_sales: number;
  units_sold: number;
  discount_given: number;
  avg_discount_pct: number;
  store_count: number;
  line_count: number;
};

type MonthlyTrendPoint = {
  month: string;
  label: string;
  net_sales: number;
  gross_sales: number;
  units: number;
};

type InsightSite = {
  site_id: string;
  site_name: string;
  net_sales: number;
  units: number;
};

type InsightSku = {
  sku: string;
  units: number;
  net_sales: number;
};

type SalesInsights = {
  top_revenue_sites: InsightSite[];
  top_sold_skus: InsightSku[];
  monthly_trend: MonthlyTrendPoint[];
};

type SalesResponse = {
  ok: boolean;
  kpis: SalesKpis;
  insights: SalesInsights;
  stores: { id: string; label: string }[];
  seasons: { id: string; label: string }[];
  channels?: { id: string; label: string }[];
};

type StockSummary = {
  total_soh: number;
  store_count: number;
  sku_count: number;
};

const REGION_PATHS: RegionMapData[] = ZONE_PATHS.map((z) => ({
  id: z.id,
  name: z.name,
  sales: 0,
  target: 0,
  stores: 0,
  path: z.path,
}));

/** Empty shell until API data arrives — never invent KPI / growth / chart numbers. */
export function useExecutiveData(
  salesData: SalesResponse | null,
  stockSummary: StockSummary | null,
  selectedZone: string | null,
  filters: GlobalFilters,
  masterFilters: CustomerMasterFiltersResponse | null = null
) {
  return useMemo(() => {
    const kpis = salesData?.kpis;
    const insights = salesData?.insights;
    const hasKpis = Boolean(kpis);

    // Use API numbers as-is — never invent client-side multipliers.
    const netSales = kpis?.net_sales ?? 0;
    const grossSales = kpis?.gross_sales ?? 0;
    const unitsSold = kpis?.units_sold ?? 0;
    const discountPct = kpis?.avg_discount_pct ?? 0;
    const totalSoh = stockSummary?.total_soh ?? 0;

    const adjustedNet = hasKpis ? netSales : 0;
    const target = 0; // Target only from Target LFS via executive-overview/summary

    const monthlyTrend = insights?.monthly_trend?.map((m) => m.net_sales) ?? [];

    const trendData = {
      daily: [] as { label: string; actual: number; target: number; forecast: number }[],
      weekly: [] as { label: string; actual: number; target: number; forecast: number }[],
      monthly: (insights?.monthly_trend ?? []).slice(-12).map((m) => ({
        label: m.label,
        actual: m.net_sales,
        target: 0,
        forecast: 0,
      })),
      quarterly: [] as { label: string; actual: number; target: number; forecast: number }[],
      yearly: [] as { label: string; actual: number; target: number; forecast: number }[],
    };

    const zoneSales: { zone: string; sales: number; growth?: number }[] = [];
    const targetAchievement: {
      name: string;
      target: number;
      actual: number;
      achievement: number;
    }[] = [];
    const channelSales: { name: string; value: number; color: string }[] = [];

    // Store leaderboards come from /executive-overview/breakdowns (Target LFS ranked) — not invented here
    const topStores: StoreRow[] = [];
    const bottomStores: StoreRow[] = [];

    const categoryTreemap: { name: string; size: number; fill: string }[] = [];
    const departments: { name: string; sales: number }[] = [];

    const topSkusRaw = insights?.top_sold_skus ?? [];
    const skuRows: SkuRow[] = topSkusRaw.slice(0, 8).map((s, i) => ({
      sku: s.sku,
      category: '—',
      sales: s.net_sales,
      stock: 0,
      margin: 0,
      sellThrough: 0,
      image_url: `/api/product-images/${s.sku}`,
      hue: 220 + i * 18,
    }));

    const styleCards: StyleCard[] = skuRows.slice(0, 6).map((s) => ({
      id: s.sku,
      name: s.sku,
      sku: s.sku,
      sales: formatInr(s.sales, true),
      hue: s.hue,
      image_url: `/api/product-images/${s.sku}`,
    }));

    const regionalMap: RegionMapData[] = REGION_PATHS.map((r) => ({
      ...r,
      sales: 0,
      target: 0,
      stores: 0,
    }));

    const cityMap: CityMapData[] = CITY_ANCHORS.map((c) => ({
      id: c.id,
      name: c.name,
      zone: c.zone.charAt(0).toUpperCase() + c.zone.slice(1),
      sales: 0,
      stores: 0,
      lat: c.lat,
      lng: c.lng,
    }));

    const masterOpts = buildClientFilterOptionsFromMaster(masterFilters);
    const filterOptions: FilterOptions = {
      seasons:
        salesData?.seasons?.filter((s) => s.id !== 'all').map((s) => s.label || s.id) ?? [],
      zones: masterOpts.zones.length ? masterOpts.zones : CLIENT_FILTER_OPTIONS.zones,
      siteCodes: masterOpts.siteCodes ?? [],
      siteNames: masterOpts.siteNames ?? [],
      storeCategories: masterOpts.storeCategories.length
        ? masterOpts.storeCategories
        : CLIENT_FILTER_OPTIONS.storeCategories,
      storeStatuses: CLIENT_FILTER_OPTIONS.storeStatuses,
      salesChannels: masterOpts.salesChannels.length
        ? masterOpts.salesChannels
        : CLIENT_FILTER_OPTIONS.salesChannels,
      customerFormats: masterOpts.customerFormats.length
        ? masterOpts.customerFormats
        : CLIENT_FILTER_OPTIONS.customerFormats,
      customerNames: masterOpts.customerNames?.length
        ? masterOpts.customerNames
        : salesData?.stores?.filter((s) => s.id !== 'all').map((s) => s.label) ?? [],
      cities: masterOpts.cities.length ? masterOpts.cities : CLIENT_FILTER_OPTIONS.cities,
      states: masterOpts.states.length ? masterOpts.states : CLIENT_FILTER_OPTIONS.states,
      clusters: masterOpts.clusters.length ? masterOpts.clusters : CLIENT_FILTER_OPTIONS.clusters,
      areaManagers: CLIENT_FILTER_OPTIONS.areaManagers,
      regionalManagers: CLIENT_FILTER_OPTIONS.regionalManagers,
      clusterRm: masterOpts.clusterRm.length ? masterOpts.clusterRm : CLIENT_FILTER_OPTIONS.clusterRm,
      storeGrades: masterOpts.storeGrades.length ? masterOpts.storeGrades : CLIENT_FILTER_OPTIONS.storeGrades,
      skus: [],
      barcodes: [],
    };

    const kpiInput = {
      grossSales: hasKpis ? grossSales : 0,
      netSales: adjustedNet,
      target,
      unitsSold: hasKpis ? Math.round(unitsSold) : 0,
      discountPct: hasKpis ? discountPct : 0,
      totalOrders: kpis?.line_count ?? 0,
      sellThrough: 0,
      salesToStock: 0,
      inventoryValue: 0,
      fillRate: 0,
      oosRate: 0,
      warehouseStock: 0,
      storeStock: totalSoh,
      monthlyTrend,
    };

    const aiInsights: Insight[] = [];
    const alerts: AlertItem[] = [];

    return {
      kpiInput,
      trendData,
      targetAchievement,
      zoneSales,
      channelSales,
      topStores,
      bottomStores,
      categoryTreemap,
      departments,
      skuRows,
      styleCards,
      regionalMap,
      cityMap,
      filterOptions,
      aiInsights,
      alerts,
    };
  }, [salesData, stockSummary, selectedZone, filters, masterFilters]);
}

export type ExecutiveData = ReturnType<typeof useExecutiveData>;

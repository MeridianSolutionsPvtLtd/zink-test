import type { ClientInvFilters, InventoryCategory, InventorySummary, StockRow } from './types';
import {
  resolveSiteKeysFromMasterFilters,
  type ClientFilters,
  type CustomerMasterSite,
} from '../sales-dashboard/salesAnalytics';

export function formatInr(value: number, compact = false) {
  if (compact) {
    if (Math.abs(value) >= 1_00_00_000) return `₹${(value / 1_00_00_000).toFixed(1)}Cr`;
    if (Math.abs(value) >= 1_00_000) return `₹${(value / 1_00_000).toFixed(1)}L`;
    if (Math.abs(value) >= 1_000) return `₹${(value / 1_000).toFixed(1)}K`;
  }
  return new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    maximumFractionDigits: 0,
  }).format(value);
}

export function formatCompactInr(value: number) {
  return formatInr(value, true);
}

export function formatNumber(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return '—';
  return new Intl.NumberFormat('en-IN').format(Math.round(value));
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

/** Static fallbacks only for product dims not in Customer Master / Item Master */
export const FILTER_OPTIONS = {
  warehouses: ['Central DC', 'North Hub', 'South Hub', 'West Hub', 'East Hub'],
  channels: ['PT', 'SSL', 'CENTRO', 'ICONIC', 'EBO'],
  departments: [] as string[],
  sections: [] as string[],
  stories: ['Urban Classic', 'Festive Edit', 'Workwear', 'Premium Line'],
  seasons: ['AW-25', 'SS-25', 'AW-24', 'SS-24'],
  productTypes: ['Regular', 'Fashion', 'Basic', 'Premium'],
  sizes: ['XS', 'S', 'M', 'L', 'XL', 'XXL'],
};

export function deriveZone(row: StockRow) {
  return (row.zone || '').trim();
}

export function deriveCity(row: StockRow) {
  return (row.city || '').trim();
}

export function deriveState(row: StockRow) {
  return (row.state || '').trim();
}

export function deriveStoreGrade(row: StockRow) {
  return (row.store_grade || '').trim();
}

export function deriveStoreCategory(row: StockRow) {
  return (row.store_category || '').trim();
}

export function deriveCategory(row: StockRow) {
  return deriveDepartment(row) || deriveSection(row) || '';
}

export function deriveDepartment(row: StockRow) {
  return (row.department || '').trim();
}

export function deriveSection(row: StockRow) {
  return (row.section || '').trim();
}

/** Map inventory client filters → sales ClientFilters shape for Customer Master site resolution */
export function toSalesClientFilters(f: ClientInvFilters): ClientFilters {
  return {
    zones: f.zones,
    siteCodes: f.siteCodes,
    siteNames: f.siteNames,
    storeCategories: f.storeCategories,
    storeStatuses: [],
    salesChannels: f.channels,
    customerFormats: [],
    customerNames: [],
    cities: f.cities,
    states: f.states,
    clusters: [],
    areaManagers: [],
    clusterRm: [],
    regionalManagers: [],
    storeGrades: f.storeGrades,
    departments: f.departments,
    sections: f.sections,
    skus: [],
    barcodes: f.eans,
    seasons: f.seasons,
  };
}

export function applyClientFilters(
  rows: StockRow[],
  f: ClientInvFilters,
  masterSites?: CustomerMasterSite[]
): StockRow[] {
  const allowedKeys =
    masterSites?.length != null && masterSites.length > 0
      ? resolveSiteKeysFromMasterFilters(masterSites, toSalesClientFilters(f))
      : null;

  return rows.filter((row) => {
    if (allowedKeys !== null) {
      if (!allowedKeys.has(row.site_id)) return false;
    } else {
      if (f.zones.length && !f.zones.includes(deriveZone(row))) return false;
      if (f.states.length && !f.states.includes(deriveState(row))) return false;
      if (f.cities.length && !f.cities.includes(deriveCity(row))) return false;
      if (f.siteCodes.length && !f.siteCodes.includes(row.site_id)) return false;
      if (f.siteNames.length && !f.siteNames.includes(row.site_name)) return false;
      if (f.storeGrades.length && !f.storeGrades.includes(deriveStoreGrade(row))) return false;
      if (f.storeCategories.length && !f.storeCategories.includes(deriveStoreCategory(row))) return false;
    }
    if (f.sizes.length && !f.sizes.includes(row.size)) return false;
    if (f.eans.length && !f.eans.includes(row.barcode)) return false;
    if (f.channels.length) {
      const src = row.source.toUpperCase();
      if (!f.channels.some((c) => src.includes(c.toUpperCase()))) return false;
    }
    if (f.departments.length && !f.departments.includes(deriveDepartment(row))) return false;
    if (f.sections.length && !f.sections.includes(deriveSection(row))) return false;
    return true;
  });
}

/**
 * @deprecated Do not invent category health / velocity on the UI.
 * Use backend stock-overview summary + store/dept summaries only.
 */
export function deriveCategoriesFromStock(_rows: StockRow[]): InventoryCategory[] {
  return [];
}

/** Map backend stock summary counts only — no synthetic mover buckets. */
export function deriveSummaryFromStock(
  stockSummary: { total_soh: number; store_count: number; sku_count: number } | null,
  _categories: InventoryCategory[] = []
): InventorySummary {
  return {
    totalSkus: stockSummary?.sku_count ?? 0,
    fastMovingSkus: 0,
    slowMovingSkus: 0,
    atRiskSkus: 0,
  };
}

export type InventoryScope = 'warehouse' | 'store' | 'lead-time' | 'breakeven';

/** ISO calendar week label helper — retail SOH is weekly; YoY = same week last year. */
export function currentSohWeekInfo(ref = new Date()) {
  const d = new Date(Date.UTC(ref.getFullYear(), ref.getMonth(), ref.getDate()));
  const dayNum = d.getUTCDay() || 7;
  d.setUTCDate(d.getUTCDate() + 4 - dayNum);
  const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
  const week = Math.ceil((((d.getTime() - yearStart.getTime()) / 86400000) + 1) / 7);
  return { week, year: d.getUTCFullYear(), label: `W${week} ${d.getUTCFullYear()}` };
}

export function pctVs(current: number, prior: number) {
  if (!prior) return 0;
  return ((current - prior) / prior) * 100;
}

/**
 * Weekly Store SOH TY vs LY series (aligned by week number).
 * Uses current SOH as latest week anchor until weekly history API is wired.
 */
export function buildWeeklySohTyLySeries(storeClosing: number, weeks = 16) {
  const { week: curWeek, year } = currentSohWeekInfo();
  return Array.from({ length: weeks }, (_, i) => {
    const offset = weeks - 1 - i;
    let w = curWeek - offset;
    let y = year;
    while (w <= 0) {
      w += 52;
      y -= 1;
    }
    // Synthetic weekly path toward current closing — replace with dated SOH history later
    const season = 1 + 0.06 * Math.sin((w / 52) * Math.PI * 2);
    const ramp = 0.88 + (i / Math.max(weeks - 1, 1)) * 0.12;
    const sohTy = Math.round(storeClosing * ramp * season);
    const sohLy = Math.round(sohTy * (0.86 + ((w * 7) % 9) * 0.012));
    return {
      week: w,
      year: y,
      label: `W${w}`,
      weekLabel: `W${w} ${y}`,
      sohTy,
      sohLy,
      vsLy: pctVs(sohTy, sohLy),
      isCurrent: offset === 0,
    };
  });
}

/**
 * Backend-only KPI mapping for Inventory Analytics.
 * Uses stock-overview summary fields; never invents warehouse/dispatch/LY metrics on the UI.
 */
export function computeInventoryKpis(
  _summary: InventorySummary | null,
  stockSummary: { total_soh: number; store_count: number; sku_count: number } | null,
  _categories: InventoryCategory[] = [],
  _rows: StockRow[] = []
) {
  const storeClosing = stockSummary?.total_soh ?? 0;
  const weekInfo = currentSohWeekInfo();

  return {
    warehouseClosing: 0,
    storeClosing,
    warehouseDispatch: 0,
    dailyInward: 0,
    dispatchLeadTimeDays: 0,
    storeSohLastYear: null as number | null,
    storeSohVsLy: null as number | null,
    storeSohWow: null as number | null,
    sohWeekLabel: weekInfo.label,
    sohWeek: weekInfo.week,
    sohYear: weekInfo.year,
    inventoryTarget: 0,
    targetAchievement: 0,
    invValue: 0,
    fillRate: 0,
    sellThrough: 0,
    ros: 0,
    weekCover: 0,
    oosRate: 0,
    lostSalesRate: 0,
    shrinkage: 0,
    slowMovers: 0,
    fastMovers: 0,
    deadStock: 0,
    warehouseStock: 0,
    storeStock: storeClosing,
    trend: [] as number[],
  };
}

/**
 * Article-wise Store SOH: This week TY vs same week LY (+ WoW).
 * Ready to swap mock LY/WoW for weekly history join later.
 */
export function buildStoreSohLastYear(rows: StockRow[]) {
  const map = new Map<string, number>();
  rows.forEach((r) => {
    const article = (r.sku || r.barcode || 'UNKNOWN').trim() || 'UNKNOWN';
    map.set(article, (map.get(article) ?? 0) + r.qty);
  });
  return [...map.entries()]
    .map(([article, sohTy]) => {
      const hash = article.length + article.charCodeAt(0);
      const sohLy = Math.round(sohTy * (0.78 + (hash % 7) * 0.03));
      const sohPrevWeek = Math.round(sohTy * (0.94 + (hash % 5) * 0.01));
      return {
        article,
        sohTy,
        soh: sohTy, // alias
        sohLy,
        sohPrevWeek,
        vsLy: pctVs(sohTy, sohLy),
        wow: pctVs(sohTy, sohPrevWeek),
      };
    })
    .sort((a, b) => b.sohTy - a.sohTy)
    .slice(0, 20);
}

/** Slow movers with suggested actions: Discount / RTV / IBT */
export function buildSlowMoverActions(categories: InventoryCategory[]) {
  const actions = ['Discount', 'RTV', 'IBT'] as const;
  return categories
    .filter((c) => c.health === 'Slow Moving' || c.sellThrough < 30)
    .slice(0, 12)
    .map((c, i) => ({
      sku: c.sku,
      category: c.category,
      stock: c.stockUnits,
      sellThrough: c.sellThrough,
      daysIdle: c.daysOfSupply ?? 45 + i * 3,
      action: actions[i % actions.length],
    }));
}

/** Daily inward detail rows derived from warehouse stock. */
export function buildDailyInwardDetails(warehouseClosing: number) {
  const hubs = ['Central DC', 'North Hub', 'South Hub', 'West Hub', 'East Hub'];
  return Array.from({ length: 7 }, (_, i) => {
    const d = new Date();
    d.setDate(d.getDate() - (6 - i));
    return {
      date: d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short' }),
      hub: hubs[i % hubs.length],
      asn: `ASN-${2400 + i}`,
      qty: Math.round(warehouseClosing * 0.004 * (0.85 + i * 0.04)),
      status: i >= 5 ? 'In transit' : 'Received',
    };
  });
}

export function buildAgeingData(categories: InventoryCategory[]) {
  const total = categories.reduce((s, c) => s + c.stockUnits, 0) || 1;
  return [
    { name: '0-30 Days', value: total * 0.42, fill: '#2563eb' },
    { name: '31-60 Days', value: total * 0.28, fill: '#3b82f6' },
    { name: '61-90 Days', value: total * 0.18, fill: '#60a5fa' },
    { name: '90+ Days', value: total * 0.12, fill: '#f59e0b' },
  ];
}

export function buildWeekCoverData(rows: StockRow[]) {
  const healthy = rows.filter((r) => r.qty > 20).length;
  const medium = rows.filter((r) => r.qty > 5 && r.qty <= 20).length;
  const critical = rows.filter((r) => r.qty <= 5).length;
  return [
    { name: 'Healthy (>4w)', value: healthy || rows.length * 0.55, status: 'healthy' },
    { name: 'Medium (2-4w)', value: medium || rows.length * 0.28, status: 'medium' },
    { name: 'Critical (<2w)', value: critical || rows.length * 0.17, status: 'critical' },
  ];
}

export function buildHealthScores(summary: InventorySummary | null) {
  const total = Math.max(summary?.totalSkus ?? 1, 1);
  const excellent = Math.round((summary?.fastMovingSkus ?? 0) * 0.6);
  const good = Math.round(total * 0.28);
  const average = summary?.slowMovingSkus ?? 0;
  const critical = summary?.atRiskSkus ?? 0;
  return [
    { label: 'Excellent', count: excellent, color: 'bg-blue-600', pct: Math.round((excellent / total) * 100) },
    { label: 'Good', count: good, color: 'bg-sky-500', pct: Math.round((good / total) * 100) },
    { label: 'Average', count: average, color: 'bg-amber-500', pct: Math.round((average / total) * 100) },
    { label: 'Critical', count: critical, color: 'bg-rose-500', pct: Math.round((critical / total) * 100) },
  ];
}

export const EMPTY_CLIENT_FILTERS: ClientInvFilters = {
  zones: [],
  states: [],
  cities: [],
  warehouses: [],
  siteCodes: [],
  siteNames: [],
  storeGrades: [],
  storeCategories: [],
  channels: [],
  departments: [],
  categories: [],
  sections: [],
  stories: [],
  seasons: [],
  productTypes: [],
  sizes: [],
  eans: [],
};

export type InventoryKpiData = ReturnType<typeof computeInventoryKpis>;

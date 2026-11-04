export type AllocationRow = {
  label: string;
  sold_2024?: number;
  sold_2025?: number;
  predicted?: number;
};

export type InsightRow = {
  site?: string;
  sku?: string;
  month?: string | number;
  department?: string;
  color?: string;
  fabric?: string;
  section?: string;
  occasion?: string;
  product_type?: string;
  fit_type?: string;
  type?: string;
  predicted_qty?: number;
  action?: string;
  risk_flag?: string;
  discount_band?: string;
  focus_comment?: string;
};

export type ClientForecastFilters = {
  dateRange: string[];
  zone: string[];
  state: string[];
  store: string[];
  channel: string[];
  department: string[];
  category: string[];
  story: string[];
  season: string[];
  sku: string[];
  productType: string[];
};

export const EMPTY_CLIENT_FORECAST_FILTERS: ClientForecastFilters = {
  dateRange: [],
  zone: [],
  state: [],
  store: [],
  channel: [],
  department: [],
  category: [],
  story: [],
  season: [],
  sku: [],
  productType: [],
};

export const CLIENT_FILTER_OPTIONS = {
  dateRange: ['MTD', 'QTD', 'YTD', 'Last 30 Days', 'Last 90 Days', 'Next 30 Days', 'Next 90 Days'],
  zone: ['North', 'South', 'East', 'West', 'Central'],
  state: ['Maharashtra', 'Karnataka', 'Delhi', 'Gujarat', 'Tamil Nadu', 'West Bengal'],
  store: ['Phoenix Mall', 'Select Citywalk', 'Forum Mall', 'Inorbit Vashi', 'DLF Promenade'],
  channel: ['Store', 'Online', 'Marketplace', 'EBO', 'Wholesale'],
  department: ['Mens Wear', 'Womens Wear', 'Kids', 'Footwear', 'Accessories', 'Ethnic'],
  category: ['Casual', 'Formal', 'Denim', 'Ethnic', 'Activewear', 'Winterwear'],
  story: ['Urban Classic', 'Festive Edit', 'Workwear', 'Premium Line', 'Weekend Casual'],
  season: ['AW-25', 'SS-25', 'AW-26', 'SS-26'],
  productType: ['Regular', 'Fashion', 'Basic', 'Premium', 'Seasonal'],
};

export type ForecastKpiData = {
  forecast: number;
  target: number;
  accuracy: number;
  variance: number;
  inventoryTarget: number;
  demand: number;
  supply: number;
  bias: number;
  demandGrowth: number | null;
  supplyCoverage: number;
  trend: number[];
  prevScale: number;
};

export function formatCompact(value: number) {
  if (Math.abs(value) >= 1_00_00_000) return `${(value / 1_00_00_000).toFixed(1)}Cr`;
  if (Math.abs(value) >= 1_00_000) return `${(value / 1_00_000).toFixed(1)}L`;
  if (Math.abs(value) >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return value.toLocaleString('en-IN');
}

export function sparklinePath(values: number[]): string {
  if (!values.length) return '';
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const w = 64;
  const h = 24;
  const step = w / Math.max(values.length - 1, 1);
  return values
    .map((v, i) => {
      const x = i * step;
      const y = h - ((v - min) / range) * h;
      return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');
}

export function computeForecastKpis(
  recommendedUnits: number | null,
  growthPct: number | null,
  categoryData: AllocationRow[],
  insights: InsightRow[]
): ForecastKpiData {
  const forecast = recommendedUnits ?? 0;
  const baselineSold = categoryData.reduce(
    (s, r) => s + (r.sold_2024 ?? 0) + (r.sold_2025 ?? 0),
    0
  );
  const predicted = categoryData.reduce((s, r) => s + (r.predicted ?? 0), 0);
  const target = predicted > 0 ? predicted * 1.05 : forecast * 1.05;
  const actualProxy = baselineSold / Math.max(categoryData.length, 1);
  const accuracy = target > 0 ? Math.min(98, 78 + (forecast / Math.max(target, 1)) * 15) : 82;
  const variance = target > 0 ? ((forecast - target) / target) * 100 : 0;
  const highRisk = insights.filter((r) => r.risk_flag === 'HIGH').length;
  const demand = forecast;
  const supply = Math.round(forecast * 0.92);
  const bias = variance * 0.4;
  const demandGrowth = growthPct;
  const supplyCoverage = demand > 0 ? (supply / demand) * 100 : 0;
  const inventoryTarget = Math.round(forecast * 1.15);
  const trend = categoryData.slice(0, 8).map((r) => r.predicted ?? r.sold_2025 ?? 0);

  return {
    forecast,
    target,
    accuracy,
    variance,
    inventoryTarget,
    demand,
    supply,
    bias,
    demandGrowth,
    supplyCoverage,
    trend: trend.length ? trend : [],
    prevScale: 1,
  };
}

export function buildForecastVsActual(
  categoryData: AllocationRow[],
  period: 'daily' | 'weekly' | 'monthly' | 'quarterly' | 'yearly'
) {
  const baseActual = categoryData.reduce((s, r) => s + (r.sold_2025 ?? r.sold_2024 ?? 0), 0);
  const baseForecast = categoryData.reduce((s, r) => s + (r.predicted ?? 0), 0);
  const n =
    period === 'daily' ? 14 : period === 'weekly' ? 8 : period === 'monthly' ? 12 : period === 'quarterly' ? 4 : 5;
  const labels =
    period === 'daily'
      ? Array.from({ length: n }, (_, i) => `D${i + 1}`)
      : period === 'weekly'
        ? Array.from({ length: n }, (_, i) => `W${i + 1}`)
        : period === 'monthly'
          ? ['Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec', 'Jan', 'Feb', 'Mar'].slice(0, n)
          : period === 'quarterly'
            ? ['Q1', 'Q2', 'Q3', 'Q4']
            : ['FY26', 'FY27'].slice(0, n);
  const actualBase = baseActual / n || 1000;
  const forecastBase = baseForecast / n || actualBase * 1.03;
  return labels.map((label, i) => ({
    label,
    actual: actualBase * (0.88 + i * 0.025 + Math.sin(i * 0.5) * 0.06),
    forecast: forecastBase * (0.9 + i * 0.022),
    target: forecastBase * 1.06 * (0.92 + i * 0.02),
  }));
}

export function buildProjectedSales(forecast: number) {
  const months = ['Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec', 'Jan', 'Feb', 'Mar'];
  const base = forecast / 12;
  return months.map((label, i) => ({
    label,
    projected: base * (0.85 + Math.sin(i * 0.55) * 0.15 + i * 0.02),
    actual: i < 6 ? base * (0.82 + Math.sin(i * 0.5) * 0.1) : undefined,
  }));
}

export function buildInventoryForecast(inventoryTarget: number) {
  const months = ['Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec', 'Jan', 'Feb', 'Mar'];
  const base = inventoryTarget / 12;
  return months.map((label, i) => {
    const inv = base * (0.9 + i * 0.025);
    const safe = base * 0.85;
    const risk = base * 1.15;
    return {
      label,
      inventory: inv,
      safe,
      risk,
      critical: base * 1.35,
      status: inv <= safe ? 'safe' : inv <= risk ? 'risk' : 'critical',
    };
  });
}

export function buildDemandSupply(categoryData: AllocationRow[]) {
  return categoryData.slice(0, 8).map((r, i) => ({
    name: r.label,
    demand: r.predicted ?? 0,
    supply: Math.round((r.predicted ?? 0) * (0.86 + (i % 4) * 0.03)),
  }));
}

export function buildMonthlyDetail(insights: InsightRow[]) {
  const map = new Map<string, { forecast: number; actual: number; demand: number; supply: number }>();
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  insights.forEach((row) => {
    const mIdx = parseInt(String(row.month ?? '0'), 10);
    const key = mIdx >= 1 && mIdx <= 12 ? months[mIdx - 1] : 'Unknown';
    const cur = map.get(key) ?? { forecast: 0, actual: 0, demand: 0, supply: 0 };
    const qty = row.predicted_qty ?? 0;
    cur.forecast += qty;
    cur.actual += qty * 0.94;
    cur.demand += qty;
    cur.supply += Math.round(qty * 0.9);
    map.set(key, cur);
  });
  if (map.size === 0) {
    return months.map((m) => ({
      month: m,
      forecast: 0,
      actual: 0,
      variance: 0,
      accuracy: 85,
      demand: 0,
      supply: 0,
      inventoryTarget: 0,
    }));
  }
  return [...map.entries()].map(([month, v]) => {
    const variance = v.forecast > 0 ? ((v.forecast - v.actual) / v.forecast) * 100 : 0;
    const accuracy = v.forecast > 0 ? Math.max(60, 100 - Math.abs(variance)) : 85;
    return {
      month,
      forecast: v.forecast,
      actual: Math.round(v.actual),
      variance,
      accuracy,
      demand: v.demand,
      supply: v.supply,
      inventoryTarget: Math.round(v.forecast * 1.12),
    };
  });
}

export function applyClientFilters(rows: InsightRow[], f: ClientForecastFilters): InsightRow[] {
  return rows.filter((r) => {
    const checks: [string[], string][] = [
      [f.department, r.department ?? ''],
      [f.sku, r.sku ?? ''],
      [f.store, r.site ?? ''],
      [f.category, r.section ?? ''],
      [f.productType, r.product_type ?? ''],
    ];
    return checks.every(([arr, val]) => !arr.length || arr.includes(val));
  });
}

export const PLANNING_TABLE_HEADERS = [
  { id: 'site', label: 'Site' },
  { id: 'sku', label: 'SKU' },
  { id: 'month', label: 'Month' },
  { id: 'department', label: 'DEPARTMENT' },
  { id: 'color', label: 'Color' },
  { id: 'fabric', label: 'Fabric' },
  { id: 'section', label: 'SECTION' },
  { id: 'occasion', label: 'OCCASION' },
  { id: 'product_type', label: 'PRODUCT_TYPE' },
  { id: 'fit_type', label: 'FIT_TYPE' },
  { id: 'type', label: 'TYPE' },
  { id: 'predicted_qty', label: 'Predicted Qty' },
  { id: 'action', label: 'Action' },
  { id: 'risk_flag', label: 'Risk Flag' },
  { id: 'discount_band', label: 'Discount Band' },
  { id: 'focus_comment', label: 'Focus Comment' },
] as const;

export function monthLabel(row: InsightRow) {
  const months = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
  const mIdx = parseInt(String(row.month ?? ''), 10);
  return mIdx >= 1 && mIdx <= 12 ? months[mIdx - 1] : String(row.month ?? '-');
}

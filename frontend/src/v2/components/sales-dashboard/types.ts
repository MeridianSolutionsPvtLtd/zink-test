export type SalesRow = {
  id: string;
  site_id: string;
  site_name: string;
  sku: string;
  barcode: string;
  size: string;
  mrp: number;
  qty: number;
  net_sale_amount: number;
  discount: number;
  discount_amount?: number;
  season: string;
  date: string;
  channel?: string;
  /** Derived sales channel: PT / SSL / CENTRO / ICONIC / EBO / Reliance */
  sales_channel?: string;
  // Customer Master (real dims — prefer over client-side derive)
  zone?: string;
  city?: string;
  state?: string;
  state_code?: string;
  cluster?: string;
  store_grade?: string;
  store_category?: string;
  customer_name?: string;
  store_season?: string;
  tax_region?: string;
  slcode?: string;
  address?: string;
  gstin?: string;
  pincode?: string;
  country?: string;
  site_short_code?: string;
  store_type_master?: string;
  store_sqft?: number | null;
  mdq?: number | null;
  /** GST Value from sales TAX_AMOUNT */
  tax_amount?: number | null;
  /** From sales Department / EBO DIVISION */
  department?: string;
  /** From sales Section */
  section?: string;
  soh?: number;
  current_soh?: number;
  current_stock?: number | null;
  fill_rate?: number | null;
};

export type FilterOption = { id: string; label: string };

export type SalesKpis = {
  gst?: number;
  ros?: number;
  gross_sales: number;
  net_sales: number;
  units_sold: number;
  discount_given: number;
  discount_pct?: number;
  avg_discount_pct: number;
  discount_on_gross_pct: number;
  store_count: number;
  line_count: number;
  asp?: number;
  current_soh?: number;
  sell_through_pct?: number;
  full_price_pct?: number;
  full_price_sales?: number;
  full_price_qty?: number;
  full_price_str_pct?: number;
  discount_sales?: number;
  discount_qty?: number;
  discount_price_pct?: number;
  discount_str_pct?: number;
  sales_per_stock?: number;
  sales_per_sqft?: number;
  sqft_total?: number;
  sqft_stores_count?: number;
  sqft_net_sales?: number;
  yoy_sales_growth_pct?: number | null;
  yoy_qty_growth_pct?: number | null;
  golm_pct?: number | null;
  golw_pct?: number | null;
  mom_gross_pct?: number | null;
  mom_net_pct?: number | null;
  mom_discount_pct?: number | null;
  mom_units_pct?: number | null;
  mom_asp_pct?: number | null;
};

export type InsightSku = {
  sku: string;
  units: number;
  net_sales: number;
  discount_amount: number;
};

export type InsightSite = {
  site_id: string;
  site_name: string;
  net_sales: number;
  units: number;
  discount_amount: number;
};

export type MonthlyTrendPoint = {
  month: string;
  label: string;
  net_sales: number;
  gross_sales: number;
  discount_amount: number;
  units: number;
};

export type WeeklyTrendPoint = {
  week: string;
  label: string;
  net_sales: number;
  gross_sales: number;
  units: number;
};

export type YearlyTrendPoint = {
  year: string;
  label: string;
  net_sales: number;
  gross_sales: number;
  units: number;
};

export type InsightDim = {
  name: string;
  net_sales: number;
  units: number;
};

export type SalesInsights = {
  top_discounted_skus: InsightSku[];
  top_sold_skus: InsightSku[];
  top_revenue_sites: InsightSite[];
  monthly_trend: MonthlyTrendPoint[];
  weekly_trend?: WeeklyTrendPoint[];
  yearly_trend?: YearlyTrendPoint[];
  top_departments?: InsightDim[];
  top_sections?: InsightDim[];
  top_channels?: InsightDim[];
  top_zones?: InsightDim[];
  top_states?: InsightDim[];
  top_cities?: InsightDim[];
  ly_metrics?: Record<string, Record<string, number> | number>;
};

/** Target LFS summary for Sales KPI cards (month / year grain). */
export type TargetSummary = {
  period: string;
  periodLabel?: string;
  grain?: 'month' | 'ytd' | 'year';
  totalTarget: number;
  totalActual: number;
  achievement: number;
  stores: number;
  zones?: number;
  sitesWithTarget?: number;
  sitesWithAchieved?: number;
  monthlyTargets?: Record<string, number>;
};

export type SalesDashboardResponse = {
  ok: boolean;
  channels?: FilterOption[];
  stores: FilterOption[];
  months: FilterOption[];
  skus: FilterOption[];
  seasons: FilterOption[];
  barcodes?: string[];
  kpis: SalesKpis;
  insights: SalesInsights;
  total: number;
  page: number;
  page_size: number;
  rows: SalesRow[];
  detail?: string;
};

/** Progressive slice responses — each paints independently as it arrives. */
export type SalesKpisResponse = {
  ok: boolean;
  year?: number;
  kpis: SalesKpis;
  total?: number;
  store_count?: number;
  detail?: string;
};

export type SalesInsightsResponse = {
  ok: boolean;
  year?: number;
  insights: SalesInsights;
  total?: number;
  detail?: string;
};

export type SalesRowsResponse = {
  ok: boolean;
  year?: number;
  channels?: FilterOption[];
  stores?: FilterOption[];
  months?: FilterOption[];
  skus?: FilterOption[];
  seasons?: FilterOption[];
  barcodes?: string[];
  total: number;
  page: number;
  page_size: number;
  rows: SalesRow[];
  detail?: string;
};

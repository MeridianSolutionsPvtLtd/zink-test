export type { InventoryKpiData } from './inventoryAnalytics';

export type StockRow = {
  id: string;
  site_id: string;
  site_name: string;
  barcode: string;
  sku: string;
  size: string;
  qty: number;
  source: string;
  date?: string;
  zone?: string;
  city?: string;
  state?: string;
  store_grade?: string;
  store_category?: string;
  customer_name?: string;
  department?: string;
  section?: string;
  cost_rate?: number;
  mrp?: number;
};

export type FilterOption = { id: string; label: string };

export type StockSummary = {
  total_soh: number;
  store_count: number;
  sku_count: number;
  barcode_count?: number;
};

export type InventoryCategory = {
  category: string;
  sku: string;
  stockUnits: number;
  stockValue: number;
  sellThrough: number;
  health: string;
  unitsSold?: number;
  daysOfSupply?: number;
  dailyVelocity?: number;
};

export type InventorySummary = {
  totalSkus: number;
  fastMovingSkus: number;
  slowMovingSkus: number;
  atRiskSkus: number;
};

export type StockOverviewResponse = {
  ok: boolean;
  stores: FilterOption[];
  sources: FilterOption[];
  channels?: FilterOption[];
  barcodes?: string[];
  skus?: string[];
  summary: StockSummary;
  insights: {
    top_stores: { site_id: string; site_name: string; qty: number }[];
    top_skus: { sku: string; qty: number }[];
  };
  total: number;
  page: number;
  page_size: number;
  rows: StockRow[];
  store_summaries?: any[];
  dept_summaries?: any[];
  soh_file_source?: string;
  detail?: string;
};

export type ClientInvFilters = {
  zones: string[];
  states: string[];
  cities: string[];
  warehouses: string[];
  siteCodes: string[];
  siteNames: string[];
  storeGrades: string[];
  storeCategories: string[];
  channels: string[];
  departments: string[];
  categories: string[];
  sections: string[];
  stories: string[];
  seasons: string[];
  productTypes: string[];
  sizes: string[];
  eans: string[];
};


const API_BASE = (import.meta.env.VITE_API_URL || 'http://127.0.0.1:5001') + '/api';

async function fetchApi<T>(path: string, params?: Record<string, string>): Promise<T> {
  const url = new URL(path, window.location.origin);
  if (params) {
    Object.entries(params).forEach(([k, v]) => {
      if (v != null && v !== '') url.searchParams.set(k, String(v));
    });
  }
  const res = await fetch(url.toString());
  if (!res.ok) throw new Error(`API ${res.status}: ${path}`);
  return res.json();
}

export const api = {
  health: () => fetchApi<{ status: string }>('/api/health'),

  // Legacy /api/dashboard/summary|regions|region-profile removed — use /api/executive-overview/*

  stores: {
    list: (region?: string, search?: string, days?: number) =>
      fetchApi<{ stores: { id: string; name: string; region: string; sellThrough: number; inventoryValue: number; idleInventory?: number; idleInventoryValue?: number; discountRate: number; status: string; trend: number; revenue: number; unitsSold: number }[] }>(
        '/api/stores', { region: region || 'All Regions', search: search || '', days: String(days ?? 30) }
      ),
    regions: () => fetchApi<{ regions: string[] }>('/api/stores/regions'),
    idleSummary: (region?: string, days?: number) =>
      fetchApi<{
        totalIdleUnits: number;
        totalIdleValue: number;
        avgAgeDays: number;
        storesAffected: number;
        ageBuckets: { label: string; units: number; pct: number }[];
        categories: { category: string; idleUnits: number; idleValue: number; avgAgeDays: number; storesAffected: number }[];
        regions: { region: string; idleUnits: number; idleValue: number; avgAgeDays: number; storeCount: number; totalStores: number }[];
        stores: { siteCode: string; storeName: string; region: string; idleUnits: number; idleValue: number; avgAgeDays: number; topCategory: string; topCategoryUnits: number }[];
      }>('/api/stores/idle-summary', { region: region || 'All Regions', days: String(days ?? 30) }),
    details: (siteCode: string, days?: number) =>
      fetchApi<{
        siteCode: string;
        categories: { category: string; sold: number; purchased: number; inventory: number; sellThrough: number; revenue: number; discountRate: number }[];
        topProducts: { barcode: string; sku: string; category: string; sold: number; revenue: number }[];
        alerts: { severity: string; message: string }[];
        summary: { totalSold: number; totalPurchased: number; totalInventory: number; sellThrough: number; avgDiscount: number };
      }>(`/api/stores/${encodeURIComponent(siteCode)}/details`, { days: String(days ?? 30) }),
    idleDetails: (siteCode: string, days?: number) =>
      fetchApi<{
        siteCode: string;
        totalIdleUnits: number;
        totalIdleValue: number;
        categories: {
          category: string;
          idleUnits: number;
          idleValue: number;
          avgAgeDays: number;
          skus: { sku: string; barcode: string; size: string; quantity: number; mrp: number; value: number; ageDays: number; lastSaleDate: string | null }[];
        }[];
      }>(`/api/stores/${encodeURIComponent(siteCode)}/idle-details`, { days: String(days ?? 30) }),
  },

  inventory: {
    overview: (region?: string, days?: number) =>
      fetchApi<{
        summary: {
          totalStockUnits: number; totalStockValue: number; totalUnitsSold: number;
          totalRevenue: number; totalUnitsPurchased: number; totalSkus: number;
          soldSkus: number; noSaleSkus: number; overallDaysOfSupply: number;
          dailyVelocity: number; sellThrough: number;
          needsReplenishCategories: number; overstockUnits: number;
          healthDistribution: Record<string, number>; categoryCount: number;
        };
        categories: {
          category: string; skuCount: number; storeCount: number;
          stockUnits: number; stockValue: number; unitsSold: number;
          revenue: number; unitsPurchased: number; sellThrough: number;
          avgDiscount: number; daysOfSupply: number | null;
          dailyVelocity: number; health: string;
          soldSkuCount: number; noSaleSkus: number;
        }[];
      }>('/api/inventory', { region: region || 'All Regions', days: String(days ?? 30) }),
    categorySkus: (category: string, region?: string, days?: number) =>
      fetchApi<{
        category: string;
        totalSkus: number;
        skus: {
          barcode: string; sku: string; size: string; mrp: number;
          stockUnits: number; stockValue: number; storeCount: number;
          unitsSold: number; revenue: number; daysOfSupply: number | null;
          dailyVelocity: number; lastSaleDate: string | null; health: string;
          stores: { siteCode: string; storeName: string; quantity: number }[];
        }[];
      }>(`/api/inventory/${encodeURIComponent(category)}/skus`, { region: region || 'All Regions', days: String(days ?? 30) }),
  },

  analytics: {
    insights: (region?: string, days?: number) =>
      fetchApi<{
        discountAnalysis: {
          brackets: { bracket: string; units: number; revenue: number; revenuePct: number; avgSellingPrice: number; avgMrp: number; realization: number }[];
          byCategory: { category: string; avgDiscount: number; sellThrough: number; revenue: number; realization: number; marginLoss: number; unitsSold: number }[];
          overallRealization: number; totalRevenue: number; totalMrpValue: number; marginLoss: number;
        };
        sizeAnalysis: { size: string; stock: number; sold: number; revenue: number; daysOfSupply: number | null; sellThrough: number }[];
        returnAnalysis: {
          overall: { returnUnits: number; saleUnits: number; returnRate: number; returnValue: number; saleValue: number; valueRate: number };
          byCategory: { category: string; returnUnits: number; saleUnits: number; returnRate: number; returnValue: number }[];
          byRegion: { region: string; returnUnits: number; saleUnits: number; returnRate: number; returnValue: number }[];
        };
        revenueInsights: {
          topSkus: { barcode: string; sku: string; category: string; size: string; unitsSold: number; revenue: number; asp: number; mrp: number; realization: number; cumulativeRevPct: number }[];
          revenuePerUnit: { category: string; revenuePerUnit: number; avgDiscount: number; realization: number; revenue: number; unitsSold: number }[];
        };
        seasonMix: { season: string; unitsSold: number; revenue: number; revenuePct: number; avgDiscount: number }[];
      }>('/api/analytics/insights', { region: region || 'All Regions', days: String(days ?? 30) }),
    trend: (region?: string, days?: number) =>
      fetchApi<{ months: { month: string; revenue: number; units: number }[] }>(
        '/api/analytics/trend', { region: region || 'All Regions', days: String(days ?? 30) }
      ),
  },

  sellthrough: {
    trend: (region?: string, days?: number) =>
      fetchApi<{ months: { month: string; sold: number; purchased: number; sellThroughPct: number | null }[] }>(
        '/api/sellthrough/trend', { region: region || 'All Regions', days: String(days ?? 30) }
      ),
    overview: (region?: string, days?: number) =>
      fetchApi<{
        summary: {
          totalPurchased: number; totalSold: number; totalStock: number;
          overallSellThrough: number; totalRevenue: number;
          unsoldStockValue: number; categoryCount: number; velocity: number;
        };
        categories: {
          category: string; purchased: number; sold: number; stock: number;
          sellThrough: number; revenue: number; stockValue: number;
          velocity: number; skuCount: number; storeCount: number;
        }[];
        distribution: { range: string; count: number; pct: number }[];
      }>('/api/sellthrough', { region: region || 'All Regions', days: String(days ?? 30) }),
    categorySkus: (category: string, region?: string, days?: number) =>
      fetchApi<{
        category: string; totalSkus: number;
        skus: {
          barcode: string; sku: string; size: string; mrp: number;
          purchased: number; sold: number; stock: number; sellThrough: number;
          revenue: number; velocity: number; storeCount: number;
          lastSale: string | null; health: string;
        }[];
      }>(`/api/sellthrough/${encodeURIComponent(category)}/skus`, { region: region || 'All Regions', days: String(days ?? 30) }),
  },

  seasonal: {
    data: (region?: string, days?: number) =>
      fetchApi<{ seasons: { season: string; purchases: number; sales: number; revenue: number; sku_count?: number }[] }>(
        '/api/seasonal', { region: region || 'All Regions', days: days != null ? String(days) : undefined }
      ),
    inventory: (region?: string) =>
      fetchApi<{ seasons: { season: string; units: number; sku_count?: number }[] }>(
        '/api/seasonal/inventory', { region: region || 'All Regions' }
      ),
    overview: (region?: string, days?: number) =>
      fetchApi<{
        seasons: {
          season: string; purchases: number; sales: number; revenue: number;
          inventory_units: number; sku_count: number; sell_through_pct: number;
        }[];
      }>('/api/seasonal/overview', { region: region || 'All Regions', days: days != null ? String(days) : undefined }),
    byCategory: (region?: string, days?: number) =>
      fetchApi<{
        seasons: { season: string; categories: { category: string; purchases: number; sales: number; revenue: number }[] }[];
      }>('/api/seasonal/by-category', { region: region || 'All Regions', days: days != null ? String(days) : undefined }),
  },

  alerts: {
    list: (region?: string, days?: number) =>
      fetchApi<{
        alerts: {
          id: string; type: string; title: string; description: string; timeAgo: string; severity: string;
          affectedStores?: number; affectedSKUs?: number; impactValue?: number;
          metrics?: { label: string; value: string; status: string }[];
        }[];
      }>(
        '/api/alerts', { region: region || 'All Regions', days: String(days ?? 30) }
      ),
  },

  replenishment: {
    locations: (region?: string) =>
      fetchApi<{ locations: { siteCode: string; name: string; zone: string }[] }>(
        '/api/replenishment/locations', { region: region || 'All Regions' }
      ),
    transfers: (region?: string) =>
      fetchApi<{ transfers: { id: string; from: string; to: string; items: number; value: number; status: string; eta: string; region: string }[] }>(
        '/api/replenishment/transfers', { region: region || 'All Regions' }
      ),
    recommendations: (region?: string) =>
      fetchApi<{ recommendations: { id: string; from: string; to: string; category: string; suggestedQty: number; estimatedValue: number; reason: string; urgency: string; potentialSales: number; region: string }[] }>(
        '/api/replenishment/recommendations', { region: region || 'All Regions' }
      ),
  },

  dataInsights: {
    overview: () =>
      fetchApi<{
        stores: number;
        cities: number;
        states: number;
        zones: number;
        uniqueSkus: number;
        barcodes: number;
        purchaseRecords: number;
        salesRecords: number;
        purchaseUnits: number;
        salesUnits: number;
        revenue: number;
        avgDiscount: number;
      }>('/api/data-insights/overview'),
    zoneDistribution: () =>
      fetchApi<{ zones: { name: string; stores: number; percentage: number; color: string }[] }>(
        '/api/data-insights/zone-distribution'
      ),
    seasonDistribution: () =>
      fetchApi<{ seasons: { name: string; items: number; purchases: number; sales: number; color: string }[] }>(
        '/api/data-insights/season-distribution'
      ),
    qualitySummary: () =>
      fetchApi<{
        returns: { count: number; percentage: number };
        negativeDiscount: { count: number; percentage: number };
        extremeDiscount: { count: number; percentage: number };
        validData: { count: number; percentage: number };
        missingZones: number;
        missingGrades: number;
        totalRecords: number;
      }>('/api/data-insights/quality-summary'),
    inventoryMovement: () =>
      fetchApi<{
        totalSkus: number;
        soldSkus: number;
        neverSoldSkus: number;
        neverSoldPercentage: number;
      }>('/api/data-insights/inventory-movement'),
    idleTime: () =>
      fetchApi<{
        distribution: { category: string; count: number; percentage: number; color: string }[];
        summary: {
          slowMoving: number;
          slowMovingPct: number;
          deadStock: number;
          deadStockPct: number;
          staleStock: number;
          staleStockPct: number;
        };
      }>('/api/data-insights/idle-time'),
  },
};

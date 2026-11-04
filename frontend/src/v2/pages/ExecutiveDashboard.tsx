import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Home, SlidersHorizontal, X } from 'lucide-react';
import DashboardLayout from '../layouts/DashboardLayout';
import { DashboardShell, PageHeader } from '../design-system';
import { API_URL, readApiJson } from '../../config';
import { fetchOnce, FILTER_CACHE_KEYS, productFilterCacheKey, productFilterQuery, readCached, type ProductFilterLists } from '../lib/dashboardFilterCache';
import {
  DEFAULT_DASHBOARD_YEAR,
  DASHBOARD_YEARS,
  type DashboardYear,
} from '../components/executive/ExecutiveHeader';
import {
  SalesFilterPanel,
  EMPTY_CLIENT_FILTERS,
} from '../components/sales-dashboard/SalesFilterPanel';
import {
  SalesTrendSection,
  TargetAchievementSection,
  SalesByZoneSection,
  SalesByChannelSection,
  StorePerformanceGrid,
  CategoryTreemapSection,
  DepartmentBarSection,
  TopSkuSection,
  TopStylesSection,
  RegionalMapSection,
  type MultiYearTrendData,
  type TargetRow,
} from '../components/executive/ExecutiveSections';
import { useExecutiveData } from '../components/executive/useExecutiveData';
import { findCityCoords } from '../components/executive/indiaMapGeometry';
import { SalesKpiGrid } from '../components/sales-dashboard/SalesKpiGrid';
import { TimelineFilterPanel, type FinancialCalendar } from '../components/sales-dashboard/TimelineFilterPanel';
import {
  buildCascadingFilterOptions,
  resolveSiteKeysFromMasterFilters,
  type ClientFilters,
  type CustomerMasterFiltersResponse,
  type ItemMasterFiltersResponse,
  type TimelineMode,
} from '../components/sales-dashboard/salesAnalytics';
import type {
  FilterOption,
  SalesRow,
} from '../components/sales-dashboard/types';

function toFilterOptions(list: Array<string | FilterOption> | undefined, allLabel: string): FilterOption[] {
  if (!list?.length) return [{ id: 'all', label: allLabel }];
  const out: FilterOption[] = [];
  const seen = new Set<string>();
  for (const item of list) {
    const id = typeof item === 'string' ? item.trim() : String(item?.id ?? '').trim();
    if (!id || id === 'all' || seen.has(id)) continue;
    seen.add(id);
    out.push(typeof item === 'string' ? { id, label: id } : { id, label: item.label || id });
  }
  return out.length ? out : [{ id: 'all', label: allLabel }];
}

export default function ExecutiveDashboard() {
  const latestRequest = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  const defaultProductQuery = productFilterQuery({
    year: String(DEFAULT_DASHBOARD_YEAR),
    timeline_mode: 'fy_year',
    timeline_values: `FY${String(DEFAULT_DASHBOARD_YEAR).slice(-2)}`,
  });
  const cachedProductLists = readCached<ProductFilterLists>(productFilterCacheKey(defaultProductQuery));
  const [loadError, setLoadError] = useState<string | null>(null);
  const [clientFilters, setClientFilters] = useState<ClientFilters>(EMPTY_CLIENT_FILTERS);
  const [isNavbarFilterOpen, setIsNavbarFilterOpen] = useState(false);
  const activeFilterCount = useMemo(
    () => Object.values(clientFilters).reduce<number>((n, arr) => n + (Array.isArray(arr) ? arr.length : 0), 0),
    [clientFilters]
  );
  const [selectedZone, setSelectedZone] = useState<string | null>(null);
  const [salesYear, setSalesYear] = useState<DashboardYear>(DEFAULT_DASHBOARD_YEAR);
  const [targetYear, setTargetYear] = useState<number>(DEFAULT_DASHBOARD_YEAR);
  const [targetLevel, setTargetLevel] = useState<'zone' | 'region'>('zone');
  const [timelineMode, setTimelineMode] = useState<TimelineMode>('fy_year');
  const defaultFyToken = `FY${String(DEFAULT_DASHBOARD_YEAR).slice(-2)}`;
  const [timelineValues, setTimelineValues] = useState<string[]>([defaultFyToken]);
  const [financialCalendar, setFinancialCalendar] = useState<FinancialCalendar | null>(
    () => {
      const cached = readCached<FinancialCalendar>(FILTER_CACHE_KEYS.financialCalendar);
      return cached?.years?.length ? cached : null;
    }
  );
  const [loadingSummary, setLoadingSummary] = useState(true);
  const [loadingTrend, setLoadingTrend] = useState(true);
  const [loadingTarget, setLoadingTarget] = useState(true);
  const [loadingBreakdowns, setLoadingBreakdowns] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [lastRefresh, setLastRefresh] = useState(new Date());

  const loading = loadingSummary || loadingTrend || loadingTarget || loadingBreakdowns;

  const [salesData, setSalesData] = useState<Parameters<typeof useExecutiveData>[0]>(null);
  const [stockSummary, setStockSummary] = useState<Parameters<typeof useExecutiveData>[1]>(null);
  const [masterFilters, setMasterFilters] = useState<CustomerMasterFiltersResponse | null>(() => {
    const cached = readCached<CustomerMasterFiltersResponse>(FILTER_CACHE_KEYS.customerMaster);
    return cached?.options ? cached : null;
  });
  const [masterFiltersLoading, setMasterFiltersLoading] = useState(
    () => !readCached<CustomerMasterFiltersResponse>(FILTER_CACHE_KEYS.customerMaster)?.options
  );
  const [skuFilterOpts, setSkuFilterOpts] = useState<FilterOption[]>(() =>
    toFilterOptions(cachedProductLists?.skus, 'All SKUs')
  );
  const [seasonFilterOpts, setSeasonFilterOpts] = useState<FilterOption[]>(() =>
    toFilterOptions(cachedProductLists?.seasons, 'All seasons')
  );
  const [apiBarcodes, setApiBarcodes] = useState<string[]>(() =>
    (cachedProductLists?.barcodes || []).map((b) => String(b).trim()).filter(Boolean)
  );
  const [chartRows] = useState<SalesRow[]>([]);
  const [filterListsLoading, setFilterListsLoading] = useState(
    () => !cachedProductLists?.skus?.length
  );
  const [drillLabel, setDrillLabel] = useState<string | null>(null);

  // Real-time backend executive state
  const [multiYearTrend, setMultiYearTrend] = useState<MultiYearTrendData | null>(null);
  const [targetData, setTargetData] = useState<TargetRow[]>([]);
  const [executiveSummary, setExecutiveSummary] = useState<{
    net_sales: number;
    gross_sales: number;
    target: number | null;
    achievement_pct: number | null;
    target_unavailable?: boolean;
    units_sold: number;
    active_stores: number;
    yoy_growth_pct: number;
    units_yoy_growth_pct?: number;
    total_soh: number;
    sku_count: number;
    sales_kpis?: Record<string, number | null>;
  } | null>(null);
  const [realBreakdowns, setRealBreakdowns] = useState<{
    channel_sales: any[];
    channel_total?: number;
    zone_sales: any[];
    zone_total?: number;
    state_sales?: any[];
    city_sales?: any[];
    top_stores: any[];
    bottom_stores: any[];
    top_skus?: any[];
    skus?: string[];
    barcodes?: string[];
    seasons?: string[];
    departments?: any[];
    category_sales?: any[];
    category_total?: number;
  } | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchOnce<FinancialCalendar>(
      FILTER_CACHE_KEYS.financialCalendar,
      `${API_URL}/sales-dashboard/financial-calendar`,
      (data) => Array.isArray(data?.years) && data.years.length > 0
    )
      .then((data: FinancialCalendar) => {
        if (!cancelled && data?.years) setFinancialCalendar(data);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  // 1. Load Customer Master filter options once on mount (same as Sales Overview)
  useEffect(() => {
    let cancelled = false;
    if (!readCached<CustomerMasterFiltersResponse>(FILTER_CACHE_KEYS.customerMaster)?.options) {
      setMasterFiltersLoading(true);
    }
    fetchOnce<CustomerMasterFiltersResponse>(
      FILTER_CACHE_KEYS.customerMaster,
      `${API_URL}/sales-dashboard/customer-master-filters`,
      (data) => Boolean(data?.options)
    )
      .then((data) => {
        if (!cancelled && data?.options) setMasterFilters(data);
      })
      .catch(() => {
        if (!cancelled && !readCached(FILTER_CACHE_KEYS.customerMaster)) setMasterFilters(null);
      })
      .finally(() => {
        if (!cancelled) setMasterFiltersLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // 2. Resolve selected site codes from customer master (before filter-list fetch)
  const allowedSites = useMemo(() => {
    const sites = masterFilters?.sites ?? [];
    return resolveSiteKeysFromMasterFilters(sites, clientFilters);
  }, [masterFilters?.sites, clientFilters]);

  const siteIdsParam = useMemo(() => {
    if (!allowedSites) return '';
    if (allowedSites.size === 0) return '__none__';
    return Array.from(allowedSites).join(',');
  }, [allowedSites]);

  const tableFilterQuery = useMemo(() => {
    const zoneParam = selectedZone ?? (clientFilters.zones.length === 1 ? clientFilters.zones[0] : 'all');
    const channelParam = clientFilters.salesChannels.length ? clientFilters.salesChannels.join(',') : 'all';
    const q = new URLSearchParams();
    q.set('year', String(salesYear));
    if (zoneParam && zoneParam !== 'all') q.set('zone', zoneParam);
    if (channelParam && channelParam !== 'all') q.set('channel', channelParam);
    if (clientFilters.seasons.length) q.set('season', clientFilters.seasons.join(','));
    if (siteIdsParam) q.set('site_ids', siteIdsParam);
    if (clientFilters.skus.length) q.set('sku', clientFilters.skus.join(','));
    if (clientFilters.barcodes.length) q.set('barcodes', clientFilters.barcodes.join(','));
    if (clientFilters.departments.length) q.set('departments', clientFilters.departments.join(','));
    if (clientFilters.sections.length) q.set('sections', clientFilters.sections.join(','));
    if (clientFilters.zones.length) q.set('zones', clientFilters.zones.join(','));
    if (clientFilters.storeGrades.length) q.set('store_grades', clientFilters.storeGrades.join(','));
    if (clientFilters.states.length) q.set('states', clientFilters.states.join(','));
    if (clientFilters.cities.length) q.set('cities', clientFilters.cities.join(','));
    if (timelineValues.length) {
      q.set('timeline_mode', timelineMode);
      q.set('timeline_values', timelineValues.join(','));
    }
    return q.toString();
  }, [selectedZone, clientFilters, siteIdsParam, salesYear, timelineMode, timelineValues]);

  // Season / SKU / barcode lists. Not the sales table — that call used to block every dropdown.
  useEffect(() => {
    let cancelled = false;
    const query = productFilterQuery({
      year: String(salesYear),
      channel: clientFilters.salesChannels.length ? clientFilters.salesChannels.join(',') : undefined,
      zones: clientFilters.zones.length ? clientFilters.zones.join(',') : undefined,
      store_grades: clientFilters.storeGrades.length ? clientFilters.storeGrades.join(',') : undefined,
      states: clientFilters.states.length ? clientFilters.states.join(',') : undefined,
      cities: clientFilters.cities.length ? clientFilters.cities.join(',') : undefined,
      departments: clientFilters.departments.length ? clientFilters.departments.join(',') : undefined,
      sections: clientFilters.sections.length ? clientFilters.sections.join(',') : undefined,
      site_ids:
        clientFilters.siteCodes.length || clientFilters.siteNames.length
          ? siteIdsParam || undefined
          : undefined,
      timeline_mode: timelineValues.length ? timelineMode : undefined,
      timeline_values: timelineValues.length ? timelineValues.join(',') : undefined,
    });
    const cached = readCached<ProductFilterLists>(productFilterCacheKey(query));
    if (!cached?.skus?.length) setFilterListsLoading(true);
    fetchOnce<ProductFilterLists>(
      productFilterCacheKey(query),
      `${API_URL}/sales-dashboard/product-filter-options?${query}`,
      (data) => data?.ok === true && Array.isArray(data.skus)
    )
      .then((data) => {
        if (cancelled || !data?.skus) return;
        setSkuFilterOpts(toFilterOptions(data.skus, 'All SKUs'));
        setSeasonFilterOpts(toFilterOptions(data.seasons, 'All seasons'));
        setApiBarcodes((data.barcodes || []).map((b) => String(b).trim()).filter(Boolean));
      })
      .catch(() => {
        // Keep the last good lists on screen.
      })
      .finally(() => {
        if (!cancelled) setFilterListsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [
    salesYear,
    siteIdsParam,
    clientFilters.salesChannels,
    clientFilters.zones,
    clientFilters.storeGrades,
    clientFilters.states,
    clientFilters.cities,
    clientFilters.departments,
    clientFilters.sections,
    timelineMode,
    timelineValues,
  ]);

  const markSectionsLoading = useCallback(() => {
    setLoadingSummary(true);
    setLoadingTrend(true);
    setLoadingTarget(true);
    setLoadingBreakdowns(true);
  }, []);

  // 3. Main Progressive Data Fetcher — each section renders immediately as it completes
  const fetchData = useCallback(
    (isManualRefresh = false) => {
      const requestId = ++latestRequest.current;
      // Cancel the previous batch so the server stops work nobody will read.
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      const { signal } = controller;
      const logFetchError = (label: string) => (e: unknown) => {
        if ((e as { name?: string })?.name !== 'AbortError') console.error(`${label} fetch error:`, e);
      };
      setLoadError(null);
      if (isManualRefresh) setRefreshing(true);

      setLoadingSummary(true);
      setLoadingTrend(true);
      setLoadingTarget(true);
      setLoadingBreakdowns(true);

      // Same query shape as Sales Overview — all selected filters reach the API
      const zoneParam = selectedZone ?? (clientFilters.zones.length === 1 ? clientFilters.zones[0] : 'all');
      const channelParam = clientFilters.salesChannels.length
        ? clientFilters.salesChannels.join(',')
        : 'all';
      const seasonParam = clientFilters.seasons.length ? clientFilters.seasons.join(',') : '';

      const qParams = new URLSearchParams();
      qParams.set('year', String(salesYear));
      if (zoneParam && zoneParam !== 'all') qParams.set('zone', zoneParam);
      if (channelParam && channelParam !== 'all') qParams.set('channel', channelParam);
      if (seasonParam) qParams.set('season', seasonParam);
      if (siteIdsParam) qParams.set('site_ids', siteIdsParam);
      if (clientFilters.skus.length) qParams.set('sku', clientFilters.skus.join(','));
      if (clientFilters.barcodes.length) qParams.set('barcodes', clientFilters.barcodes.join(','));
      if (clientFilters.departments.length) qParams.set('departments', clientFilters.departments.join(','));
      if (clientFilters.sections.length) qParams.set('sections', clientFilters.sections.join(','));
      if (clientFilters.zones.length) qParams.set('zones', clientFilters.zones.join(','));
      if (clientFilters.storeGrades.length) qParams.set('store_grades', clientFilters.storeGrades.join(','));
      if (clientFilters.states.length) qParams.set('states', clientFilters.states.join(','));
      if (clientFilters.cities.length) qParams.set('cities', clientFilters.cities.join(','));
      if (timelineValues.length) {
        qParams.set('timeline_mode', timelineMode);
        qParams.set('timeline_values', timelineValues.join(','));
      }
      // Manual refresh bypasses hosted page-cache / Home sales RAM.
      if (isManualRefresh) qParams.set('refresh', '1');

      const qStr = `?${qParams.toString()}`;
      const trendParams = new URLSearchParams(qParams);
      const trendQ = trendParams.toString() ? `?${trendParams.toString()}` : '';

      const targetQParams = new URLSearchParams(qParams);
      targetQParams.set('year', String(targetYear));
      targetQParams.set('group_by', targetLevel);

      // Section 1: Summary KPIs — render immediately
      fetch(`${API_URL}/executive-overview/summary${qStr}`, { signal })
        .then((r) => (r.ok ? r.json() : null))
        .then((data) => {
          if (requestId === latestRequest.current && data?.ok) {
            setExecutiveSummary(data);
          }
        })
        .catch(logFetchError('Summary'))
        .finally(() => {
          if (requestId === latestRequest.current) {
            setLoadingSummary(false);
            setRefreshing(false);
          }
        });

      // Section 2: Multi-Year Trend — render immediately
      fetch(`${API_URL}/executive-overview/multi-year-trend${trendQ}`, { signal })
        .then((r) => (r.ok ? r.json() : null))
        .then((data) => {
          if (requestId === latestRequest.current && data?.ok) {
            setMultiYearTrend(data);
          }
        })
        .catch(logFetchError('Trend'))
        .finally(() => {
          if (requestId === latestRequest.current) setLoadingTrend(false);
        });

      // Section 3: Target vs Achievement — render immediately
      fetch(`${API_URL}/executive-overview/target-vs-achievement?${targetQParams.toString()}`, { signal })
        .then((r) => (r.ok ? r.json() : null))
        .then((data) => {
          if (requestId === latestRequest.current && data?.ok) {
            setTargetData(data.data ?? []);
          }
        })
        .catch(logFetchError('Target'))
        .finally(() => {
          if (requestId === latestRequest.current) setLoadingTarget(false);
        });

      // Section 4: Breakdowns (Channels, Zones, Maps, Stores, Categories, SKUs) — render immediately
      fetch(`${API_URL}/executive-overview/breakdowns${qStr}`, { signal })
        .then((r) => (r.ok ? r.json() : null))
        .then((data) => {
          if (requestId === latestRequest.current && data?.ok) {
            setRealBreakdowns(data);
          }
        })
        .catch(logFetchError('Breakdowns'))
        .finally(() => {
          if (requestId === latestRequest.current) {
            setLoadingBreakdowns(false);
            setLastRefresh(new Date());
          }
        });

      // Do NOT fetch unfiltered stock-overview — Current SOH/STR come from executive summary (scoped).
      if (requestId === latestRequest.current) setStockSummary(null);
    },
    [selectedZone, clientFilters, siteIdsParam, salesYear, targetYear, targetLevel, timelineMode, timelineValues]
  );

  useEffect(() => {
    fetchData();
    return () => {
      latestRequest.current += 1;
      abortRef.current?.abort();
    };
  }, [fetchData]);

  // Client-side fallback data for maps / treemaps
  const data = useExecutiveData(salesData, stockSummary, selectedZone, clientFilters as any, masterFilters);

  // Hide Target entirely for true product cuts: dept / section / SKU / barcode.
  const productTargetUnavailable = useMemo(
    () =>
      clientFilters.skus.length > 0 ||
      clientFilters.barcodes.length > 0 ||
      clientFilters.departments.length > 0 ||
      clientFilters.sections.length > 0,
    [clientFilters]
  );
  // Season keeps FY Target ₹, but Achievement is N/A (no season-wise Target LFS).
  const seasonAchievementNa = clientFilters.seasons.length > 0;

  // KPI cards: display-only — every metric comes pre-calculated from executive summary / sales_kpis
  const kpiGridData = useMemo(() => {
    const metrics = executiveSummary?.sales_kpis;
    const gross = Number(metrics?.gross_sales ?? executiveSummary?.gross_sales ?? 0);
    const net = Number(metrics?.net_sales ?? executiveSummary?.net_sales ?? 0);
    const units = Number(metrics?.units_sold ?? executiveSummary?.units_sold ?? 0);
    const discount = Number(metrics?.discount_given ?? 0);
    const discountPct =
      typeof metrics?.discount_pct === 'number'
        ? Number(metrics.discount_pct)
        : typeof metrics?.discount_on_gross_pct === 'number'
          ? Number(metrics.discount_on_gross_pct)
          : typeof metrics?.avg_discount_pct === 'number'
            ? Number(metrics.avg_discount_pct)
            : undefined;
    const soh = Number(metrics?.current_soh ?? executiveSummary?.total_soh ?? 0);
    const asp = typeof metrics?.asp === 'number' ? Number(metrics.asp) : 0;
    const sellThroughPct = typeof metrics?.sell_through_pct === 'number' ? metrics.sell_through_pct : undefined;
    const apiTarget = Number(executiveSummary?.target ?? 0);
    const siteTargetUnavailable =
      Boolean((executiveSummary as { target_unavailable?: boolean } | null)?.target_unavailable) ||
      !(apiTarget > 0);
    const hideTarget = productTargetUnavailable || siteTargetUnavailable;

    return {
      gross,
      net,
      discount,
      discountPct,
      gst: Number(metrics?.gst ?? 0),
      units,
      mrpValue: gross,
      costValue: 0,
      asp,
      trend: data.kpiInput.monthlyTrend,
      target: hideTarget ? undefined : apiTarget,
      targetUnavailable: hideTarget && !productTargetUnavailable,
      targetAchievementPct:
        hideTarget || seasonAchievementNa
          ? undefined
          : executiveSummary?.achievement_pct,
      targetActual: hideTarget || seasonAchievementNa ? undefined : net,
      currentSoh: soh,
      sellThroughPct,
      fullPricePct: Number(metrics?.full_price_pct ?? 0),
      fullPriceSales: Number(metrics?.full_price_sales ?? 0),
      fullPriceQty: Number(metrics?.full_price_qty ?? 0),
      fullPriceStrPct: Number(metrics?.full_price_str_pct ?? 0),
      salesPerSqft: metrics?.sales_per_sqft != null ? Number(metrics.sales_per_sqft) : undefined,
      sqftTotal: metrics?.sqft_total != null ? Number(metrics.sqft_total) : undefined,
      sqftStoresCount: metrics?.sqft_stores_count != null ? Number(metrics.sqft_stores_count) : undefined,
      sqftNetSales: metrics?.sqft_net_sales != null ? Number(metrics.sqft_net_sales) : undefined,
      yoySalesGrowthPct: executiveSummary?.yoy_growth_pct,
      yoyQtyGrowthPct: executiveSummary?.units_yoy_growth_pct,
      monthGrowth: null,
      growthRates: {
        goly: executiveSummary?.yoy_growth_pct ?? null,
        golyUnits: executiveSummary?.units_yoy_growth_pct ?? null,
        golm: null,
        golw: null,
      },
    };
  }, [executiveSummary, data.kpiInput, productTargetUnavailable, seasonAchievementNa]);

  const handleZoneClick = (zone: string) => {
    markSectionsLoading();
    setSelectedZone((prev) => (prev === zone ? null : zone));
    setClientFilters((prev) => ({
      ...prev,
      zones: selectedZone === zone ? [] : [zone],
    }));
  };

  // Season / SKU / barcode — prefer sales-dashboard lists; fall back to breakdowns strings
  const panelSkus = useMemo(() => {
    if (skuFilterOpts.some((s) => s.id !== 'all')) return skuFilterOpts;
    return toFilterOptions(realBreakdowns?.skus, 'All SKUs');
  }, [skuFilterOpts, realBreakdowns?.skus]);

  const panelSeasons = useMemo(() => {
    if (seasonFilterOpts.some((s) => s.id !== 'all')) return seasonFilterOpts;
    return toFilterOptions(realBreakdowns?.seasons, 'All seasons');
  }, [seasonFilterOpts, realBreakdowns?.seasons]);

  const panelBarcodes = useMemo(() => {
    if (apiBarcodes.length) return apiBarcodes;
    if (realBreakdowns?.barcodes?.length) return realBreakdowns.barcodes;
    return [];
  }, [apiBarcodes, realBreakdowns?.barcodes]);

  // Real Departments from sales data
  const departmentData = useMemo(() => {
    if (realBreakdowns?.departments?.length) {
      return realBreakdowns.departments;
    }
    return [];
  }, [realBreakdowns?.departments]);

  // Real Top Categories from sales data
  const categoryData = useMemo(() => {
    if (realBreakdowns?.category_sales?.length) {
      const PALETTE = ['#6366f1', '#3b82f6', '#10b981', '#f59e0b', '#8b5cf6', '#ec4899', '#06b6d4'];
      return realBreakdowns.category_sales.map((c: any, i: number) => ({
        name: c.name,
        size: Number(c.size || c.sales || 0),
        sales: Number(c.sales || c.size || 0),
        share_pct: typeof c.share_pct === 'number' ? c.share_pct : undefined,
        fill: PALETTE[i % PALETTE.length],
      }));
    }
    return [];
  }, [realBreakdowns?.category_sales]);

  // Real Map Data (Zones + Cities) — sales/target/growth/stores all from backend
  const mapRegions = useMemo(() => {
    if (realBreakdowns?.zone_sales?.length) {
      return data.regionalMap.map((r) => {
        const found = realBreakdowns.zone_sales.find(
          (z: any) => z.zone.toLowerCase() === r.name.toLowerCase()
        );
        if (found) {
          return {
            ...r,
            sales: Number(found.sales || 0),
            target: Number(found.target || 0),
            growth: typeof found.growth === 'number' ? Number(found.growth) : undefined,
            units: Number(found.units || 0),
            stores: Number(found.stores) > 0 ? Number(found.stores) : 0,
          };
        }
        return {
          ...r,
          sales: 0,
          target: 0,
          growth: undefined,
        };
      });
    }
    return data.regionalMap.map((r) => ({ ...r, sales: 0, target: 0, growth: undefined }));
  }, [realBreakdowns?.zone_sales, data.regionalMap]);

  const mapCities = useMemo(() => {
    if (realBreakdowns?.city_sales?.length) {
      return realBreakdowns.city_sales.map((c: any, i: number) => {
        const coords = findCityCoords(c.city);
        return {
          id: `c-${i}`,
          name: c.city,
          zone: c.zone || '—',
          sales: Number(c.sales || 0),
          growth: typeof c.growth === 'number' ? Number(c.growth) : undefined,
          stores: Number(c.stores) > 0 ? Number(c.stores) : 0,
          lat: coords.lat,
          lng: coords.lng,
        };
      });
    }
    return [];
  }, [realBreakdowns?.city_sales]);

  // Load Item Master filter options (Department & Section)
  const [itemMasterFilters, setItemMasterFilters] = useState<ItemMasterFiltersResponse | null>(() => {
    const cached = readCached<ItemMasterFiltersResponse>(FILTER_CACHE_KEYS.itemMaster);
    return cached?.options ? cached : null;
  });
  const [itemMasterLoading, setItemMasterLoading] = useState(
    () => !readCached<ItemMasterFiltersResponse>(FILTER_CACHE_KEYS.itemMaster)?.options
  );

  useEffect(() => {
    let cancelled = false;
    if (!readCached<ItemMasterFiltersResponse>(FILTER_CACHE_KEYS.itemMaster)?.options) {
      setItemMasterLoading(true);
    }
    fetchOnce<ItemMasterFiltersResponse>(
      FILTER_CACHE_KEYS.itemMaster,
      `${API_URL}/sales-dashboard/item-master-filters`,
      (data) => Boolean(data?.options)
    )
      .then((data) => {
        if (!cancelled && data?.options) setItemMasterFilters(data);
      })
      .catch(() => {
        if (!cancelled && !readCached(FILTER_CACHE_KEYS.itemMaster)) setItemMasterFilters(null);
      })
      .finally(() => {
        if (!cancelled) setItemMasterLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Cascading Filter Options — same builder as Sales Overview (master + sales rows + item master)
  const masterFilterOptions = useMemo(
    () => buildCascadingFilterOptions(masterFilters, clientFilters, chartRows, itemMasterFilters),
    [masterFilters, itemMasterFilters, clientFilters, chartRows]
  );

  const barcodeOptionsForPanel = useMemo(() => {
    if (panelBarcodes.length) return panelBarcodes;
    if (masterFilterOptions.barcodes?.length) return masterFilterOptions.barcodes;
    return [];
  }, [panelBarcodes, masterFilterOptions.barcodes]);

  const handleClientChange = (key: keyof ClientFilters, value: string[]) => {
    markSectionsLoading();
    setClientFilters((prev) => ({ ...prev, [key]: value }));
    if (key === 'zones' && value.length === 1) setSelectedZone(value[0]);
    if (key === 'zones' && value.length === 0) setSelectedZone(null);
  };

  const handleClearFilters = () => {
    markSectionsLoading();
    setClientFilters(EMPTY_CLIENT_FILTERS);
    setSelectedZone(null);
    setDrillLabel(null);
    setTimelineMode('fy_year');
    setTimelineValues([defaultFyToken]);
    setSalesYear(DEFAULT_DASHBOARD_YEAR);
    setTargetYear(DEFAULT_DASHBOARD_YEAR);
  };

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isNavbarFilterOpen) {
        setIsNavbarFilterOpen(false);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isNavbarFilterOpen]);

  const navbarFilterButton = (
    <button
      type="button"
      onClick={() => setIsNavbarFilterOpen((prev) => !prev)}
      className={`relative inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs font-semibold transition-all ${
        isNavbarFilterOpen || activeFilterCount > 0
          ? 'border-blue-500 bg-blue-50 text-blue-700 shadow-sm dark:border-blue-500/60 dark:bg-blue-950/50 dark:text-blue-300'
          : 'border-slate-200 text-slate-600 hover:border-slate-300 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800'
      }`}
      title="Toggle Dashboard Filters Drawer"
    >
      <SlidersHorizontal className="h-3.5 w-3.5 text-blue-600 dark:text-blue-400" />
      <span>Filter</span>
      {activeFilterCount > 0 && (
        <span className="flex h-4 min-w-[16px] items-center justify-center rounded-full bg-blue-600 px-1 text-[10px] font-bold text-white">
          {activeFilterCount}
        </span>
      )}
    </button>
  );

  return (
    <DashboardLayout headerActions={navbarFilterButton}>
      <DashboardShell className="space-y-3">
        <PageHeader
          icon={Home}
          title="Dashboard"
          compact
          loading={loading || refreshing}
          onRefresh={() => fetchData(true)}
        >
          <div className="w-full">
            <TimelineFilterPanel
              financialCalendar={financialCalendar}
              variant="header"
              mode={timelineMode}
              values={timelineValues}
              rows={chartRows}
              loading={!financialCalendar && (timelineMode === 'fy_week' || timelineMode === 'fy_year')}
              onModeChange={(m) => {
                markSectionsLoading();
                setTimelineMode(m);
                setTimelineValues([]);
              }}
              onValuesChange={(v) => {
                markSectionsLoading();
                setTimelineValues(v);
                const fy = v[0]?.match(/^FY-?(\d{2})(?:$|-W)/i);
                if (fy && (timelineMode === 'fy_year' || timelineMode === 'fy_week')) {
                  const year = (2000 + Number(fy[1])) as DashboardYear;
                  setSalesYear(year);
                  setTargetYear(year);
                }
              }}
              onClear={() => {
                markSectionsLoading();
                setTimelineValues([]);
              }}
            />
          </div>
        </PageHeader>
        {loadError && <p role="alert" className="mb-4 text-sm text-rose-600">{loadError}</p>}

        {/* Inline Filters — identical to Sales Overview, non-sticky so it scrolls up naturally */}
        <SalesFilterPanel
          sticky={false}
          compact
          clientFilters={clientFilters}
          stores={[]}
          skus={panelSkus}
          seasons={panelSeasons}
          barcodeOptions={barcodeOptionsForPanel}
          filterOptions={masterFilterOptions}
          loading={filterListsLoading}
          masterLoading={masterFiltersLoading}
          itemMasterLoading={itemMasterLoading}
          onClientChange={handleClientChange}
          onClearAll={handleClearFilters}
        />

        {/* Slide-over Filter Drawer triggered from Navbar filter button */}
        {isNavbarFilterOpen && (
          <div className="fixed inset-0 z-[100] flex justify-end overflow-hidden">
            <div
              className="absolute inset-0 bg-slate-950/40 backdrop-blur-[2px] transition-opacity"
              onClick={() => setIsNavbarFilterOpen(false)}
            />
            <div className="relative z-10 flex h-full w-full max-w-4xl flex-col border-l border-slate-200/80 bg-slate-50 shadow-2xl dark:border-slate-800 dark:bg-slate-950">
              <div className="flex items-center justify-between border-b border-slate-200/80 bg-white px-6 py-4 dark:border-slate-800 dark:bg-slate-900">
                <div className="flex items-center gap-2.5">
                  <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-blue-50 text-blue-600 dark:bg-blue-950/50 dark:text-blue-400">
                    <SlidersHorizontal className="h-4 w-4" />
                  </div>
                  <div>
                    <h3 className="text-sm font-bold text-slate-900 dark:text-white">Filters</h3>
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  {activeFilterCount > 0 && (
                    <button
                      type="button"
                      onClick={handleClearFilters}
                      className="text-xs font-semibold text-rose-600 hover:text-rose-700 dark:text-rose-400"
                    >
                      Clear all ({activeFilterCount})
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => setIsNavbarFilterOpen(false)}
                    className="rounded-lg p-1.5 text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-700 dark:hover:bg-slate-800 dark:hover:text-slate-200"
                    aria-label="Close filter drawer"
                  >
                    <X className="h-5 w-5" />
                  </button>
                </div>
              </div>

              <div className="flex-1 overflow-y-auto p-3 sm:p-4">
                <SalesFilterPanel
                  sticky={false}
                  compact
                  clientFilters={clientFilters}
                  stores={[]}
                  skus={panelSkus}
                  seasons={panelSeasons}
                  barcodeOptions={barcodeOptionsForPanel}
                  filterOptions={masterFilterOptions}
                  loading={filterListsLoading}
                  masterLoading={masterFiltersLoading}
                  itemMasterLoading={itemMasterLoading}
                  onClientChange={handleClientChange}
                  onClearAll={handleClearFilters}
                />
              </div>
            </div>
          </div>
        )}

        <div className="space-y-4">
          <section className="space-y-2">
            <SalesKpiGrid data={kpiGridData} loading={loadingSummary} />
            {(productTargetUnavailable || seasonAchievementNa) && (
              <p className="text-xs text-slate-500 dark:text-slate-400">
                {productTargetUnavailable
                  ? 'Targets are store-level. Target / Achievement hide for department, section, SKU, or barcode filters.'
                  : 'Season filter: FY Target stays visible; Target Achievement is N/A (no season-wise targets).'}
              </p>
            )}
          </section>

          {/* Actual Sales Multi-Year Comparison (2026 YTD vs 2025 vs 2024) */}
          <section className="grid gap-5 xl:grid-cols-1">
            <SalesTrendSection multiYearData={multiYearTrend} loading={loadingTrend} />
          </section>

          {/* Target vs Achievement with 2026 / 2025 toggle and Zone / Region drilldown */}
          <section className="grid gap-5 [grid-template-columns:repeat(auto-fit,minmax(min(100%,28rem),1fr))]">
            <TargetAchievementSection
              data={targetData}
              year={targetYear}
              level={targetLevel}
              onYearChange={(y) => {
                setLoadingTarget(true);
                if ((DASHBOARD_YEARS as readonly number[]).includes(y)) {
                  setSalesYear(y as DashboardYear);
                }
                setTargetYear(y);
                setTimelineMode('fy_year');
                setTimelineValues([`FY${String(y).slice(-2)}`]);
              }}
              onLevelChange={(lvl) => {
                setLoadingTarget(true);
                setTargetLevel(lvl);
              }}
              loading={loadingTarget}
              onDrill={(name) => {
                setDrillLabel(name);
                handleZoneClick(name);
              }}
            />
            <SalesByZoneSection
              data={realBreakdowns?.zone_sales?.length ? realBreakdowns.zone_sales : []}
              selectedZone={selectedZone}
              onZoneClick={handleZoneClick}
              loading={loadingBreakdowns}
            />
          </section>

          {drillLabel && (
            <div className="rounded-xl border border-blue-200 bg-blue-50/60 px-4 py-2 text-xs font-medium text-blue-700 dark:border-blue-500/30 dark:bg-blue-500/10 dark:text-blue-300">
              Drill-down active: <strong>{drillLabel}</strong> — click zone again to reset cross-filter
            </div>
          )}

          <section className="grid gap-5 [grid-template-columns:repeat(auto-fit,minmax(min(100%,28rem),1fr))]">
            <SalesByChannelSection
              data={realBreakdowns?.channel_sales?.length ? realBreakdowns.channel_sales : []}
              totalSales={realBreakdowns?.channel_total}
              loading={loadingBreakdowns}
            />
            <CategoryTreemapSection
              data={categoryData}
              totalSales={realBreakdowns?.category_total}
              loading={loadingBreakdowns}
            />
          </section>

          <section className="grid gap-5 [grid-template-columns:repeat(auto-fit,minmax(min(100%,28rem),1fr))]">
            <StorePerformanceGrid
              title="Top Performing Stores"
              subtitle="Top 10 by achievement among stores with meaningful targets (≥₹10L)"
              variant="top"
              filterQuery={tableFilterQuery}
            />
            <StorePerformanceGrid
              title="Bottom Performing Stores"
              subtitle="Bottom 10 by achievement among stores with meaningful targets — needs attention"
              variant="bottom"
              filterQuery={tableFilterQuery}
            />
          </section>

          <section className="grid gap-5 [grid-template-columns:repeat(auto-fit,minmax(min(100%,28rem),1fr))]">
            <div className="min-w-0">
              <DepartmentBarSection data={departmentData} loading={loadingBreakdowns} />
            </div>
            <div className="min-w-0">
              <RegionalMapSection
                regions={mapRegions}
                cities={mapCities}
                loading={loadingBreakdowns}
                selectedZone={selectedZone}
                onZoneClick={handleZoneClick}
                onCityClick={(city) => setDrillLabel(city)}
              />
            </div>
          </section>

          <section>
            <TopSkuSection filterQuery={tableFilterQuery} />
          </section>

          <section>
            <TopStylesSection filterQuery={tableFilterQuery} />
          </section>
        </div>
      </DashboardShell>
    </DashboardLayout>
  );
}

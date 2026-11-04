import React, { Component, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AlertCircle, Calendar, Filter, SlidersHorizontal, TrendingUp, X } from 'lucide-react';
import DashboardLayout from '../layouts/DashboardLayout';
import { DashboardShell, PageHeader } from '../design-system';
import { API_URL, readApiJson } from '../../config';
import { fetchOnce, FILTER_CACHE_KEYS, productFilterCacheKey, productFilterQuery, readCached, type ProductFilterLists } from '../lib/dashboardFilterCache';
import {
  SalesFilterPanel,
  EMPTY_CLIENT_FILTERS,
} from '../components/sales-dashboard/SalesFilterPanel';
import { SalesKpiGrid } from '../components/sales-dashboard/SalesKpiGrid';
import { SalesTrendBreakdownRow, type BreakdownDim, type TrendGrain } from '../components/sales-dashboard/SalesCharts';
import { TimelineFilterPanel, type FinancialCalendar } from '../components/sales-dashboard/TimelineFilterPanel';
import { SalesEnterpriseTable } from '../components/sales-dashboard/SalesEnterpriseTable';
import { FinancialYearSalesComparison } from '../components/sales-dashboard/FinancialYearSalesComparison';
import { SalesAnalyzerModule } from '../components/sales-analyzer/SalesAnalyzerModule';
import { DrillDownBreadcrumb } from '../components/sales-dashboard/SalesInsightsPanel';
import {
  applyClientFilters,
  buildCascadingFilterOptions,
  buildTargetDateParams,
  pickTargetSummaryFromApi,
  computeExtendedKpis,
  computeMonthGrowth,
  computeSalesGrowthRates,
  resolveSiteKeysFromMasterFilters,
  type ClientFilters,
  type CustomerMasterFiltersResponse,
  type ItemMasterFiltersResponse,
  type TimelineMode,
  type TargetGrain,
  DRILL_ORDER,
  drillKey,
  deriveSalesChannel,
} from '../components/sales-dashboard/salesAnalytics';
import type {
  FilterOption,
  SalesInsights,
  SalesInsightsResponse,
  SalesKpis,
  SalesKpisResponse,
  SalesRow,
  SalesRowsResponse,
  TargetSummary,
} from '../components/sales-dashboard/types';

const INSIGHT_CARD_KEYS: Record<string, (keyof SalesInsights)[]> = {
  trend_month: ['monthly_trend'],
  trend_week: ['weekly_trend'],
  trend_year: ['yearly_trend'],
  breakdown_zone: ['top_zones'],
  breakdown_store: ['top_revenue_sites'],
  breakdown_state: ['top_states'],
  breakdown_city: ['top_cities'],
  breakdown_channel: ['top_channels'],
  channel: ['top_channels'],
  breakdown_sku: ['top_sold_skus'],
  breakdown_discount: ['top_discounted_skus'],
  breakdown_department: ['top_departments'],
  breakdown_section: ['top_sections'],
};

const BREAKDOWN_CARD: Record<BreakdownDim, string> = {
  zone: 'breakdown_zone',
  store: 'breakdown_store',
  state: 'breakdown_state',
  city: 'breakdown_city',
  channel: 'channel',
  sku: 'breakdown_sku',
  department: 'breakdown_department',
  section: 'breakdown_section',
  discount: 'breakdown_discount',
};

function emptyInsights(): SalesInsights {
  return {
    top_discounted_skus: [],
    top_sold_skus: [],
    top_revenue_sites: [],
    monthly_trend: [],
    weekly_trend: [],
    yearly_trend: [],
    top_departments: [],
    top_sections: [],
    top_channels: [],
    top_zones: [],
    top_states: [],
    top_cities: [],
  };
}

type AnalyzerErrorBoundaryProps = { children: React.ReactNode };
type AnalyzerErrorBoundaryState = { error: string | null };

class AnalyzerErrorBoundary extends Component<
  AnalyzerErrorBoundaryProps,
  AnalyzerErrorBoundaryState
> {
  declare props: AnalyzerErrorBoundaryProps;
  state: AnalyzerErrorBoundaryState = { error: null };

  static getDerivedStateFromError(err: unknown) {
    return { error: err instanceof Error ? err.message : 'Pivot analyzer failed to load' };
  }

  render() {
    if (this.state.error) {
  return (
        <div className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-300">
          Pivot analyzer could not load: {this.state.error}
    </div>
  );
}
    return this.props.children;
  }
}

function toSkuOptions(
  list: ProductFilterLists['skus'] | undefined,
  allLabel = 'All SKUs'
): FilterOption[] {
  const out: FilterOption[] = [];
  const seen = new Set<string>();
  for (const item of list || []) {
    const id = typeof item === 'string' ? item.trim() : String(item?.id ?? '').trim();
    if (!id || id === 'all' || seen.has(id)) continue;
    seen.add(id);
    out.push(typeof item === 'string' ? { id, label: id } : { id, label: item.label || id });
  }
  return out.length ? out : [{ id: 'all', label: allLabel }];
}

function _indianFyEnd(d = new Date()): number {
  return d.getMonth() >= 3 ? d.getFullYear() + 1 : d.getFullYear();
}

export default function SalesDashboard() {
  const defaultFyEnd = _indianFyEnd();
  const defaultFyFrom = `${defaultFyEnd - 1}-04-01`;
  const defaultFyTo = `${defaultFyEnd}-03-31`;
  const defaultFyToken = `FY${String(defaultFyEnd).slice(-2)}`;
  const [salesYear, setSalesYear] = useState<number>(defaultFyEnd);
  const cachedCalendar = readCached<FinancialCalendar>(FILTER_CACHE_KEYS.financialCalendar);
  const cachedCustomer = readCached<CustomerMasterFiltersResponse>(FILTER_CACHE_KEYS.customerMaster);
  const cachedItems = readCached<ItemMasterFiltersResponse>(FILTER_CACHE_KEYS.itemMaster);
  const [financialCalendar, setFinancialCalendar] = useState<FinancialCalendar | null>(
    cachedCalendar?.years?.length ? cachedCalendar : null
  );
  useEffect(() => {
    let cancelled = false;
    fetchOnce<FinancialCalendar>(
      FILTER_CACHE_KEYS.financialCalendar,
      `${API_URL}/sales-dashboard/financial-calendar`,
      (data) => Array.isArray(data?.years) && data.years.length > 0
    )
      .then((data: FinancialCalendar) => {
        if (cancelled || !data?.years?.length) return;
        setFinancialCalendar(data);
        // Prefer API latest FY over hardcoded 2027 bootstrap (adapts when FY28 lands).
        const fyEnd = Number(data.latest_year || data.years[data.years.length - 1]?.year || 0);
        if (fyEnd < 2020) return;
        const fyToken = `FY${String(fyEnd).slice(-2)}`;
        setSalesYear((prev) => (prev === defaultFyEnd ? fyEnd : prev));
        setSelectedYearVal((prev) => (prev === String(defaultFyEnd) ? String(fyEnd) : prev));
        setTimelineValues((prev) =>
          prev.length === 1 && (prev[0] === 'FY27' || prev[0] === defaultFyToken) ? [fyToken] : prev
        );
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);
  const [, setYearFilterType] = useState<'fy' | 'year'>('fy');
  const [selectedYearVal, setSelectedYearVal] = useState<string>(String(defaultFyEnd));
  const abortRef = useRef<AbortController | null>(null);
  const productQueryRef = useRef<string | null>(null);
  const [channelId, setChannelId] = useState('all');
  const [storeId, setStoreId] = useState('all');
  const [monthId, setMonthId] = useState('all');
  const [skuId, setSkuId] = useState('all');
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [page, setPage] = useState(1);
  const pageSize = 20;

  const [clientFilters, setClientFilters] = useState<ClientFilters>(EMPTY_CLIENT_FILTERS);
  const [isNavbarFilterOpen, setIsNavbarFilterOpen] = useState(false);
  const activeFilterCount = useMemo(
    () => Object.values(clientFilters).reduce<number>((n, a) => n + (Array.isArray(a) ? a.length : 0), 0),
    [clientFilters]
  );
  const [timelineMode, setTimelineMode] = useState<TimelineMode>('fy_year');
  const [timelineValues, setTimelineValues] = useState<string[]>([defaultFyToken]);
  const [tableSearch, setTableSearch] = useState('');
  const [debouncedTableSearch, setDebouncedTableSearch] = useState('');
  const [tableSortKey, setTableSortKey] = useState('sales');
  const [tableSortDir, setTableSortDir] = useState<'asc' | 'desc'>('desc');
  const [crossFilter, setCrossFilter] = useState<string | null>(null);
  const [drillPath, setDrillPath] = useState<{ level: string; value: string }[]>([]);

  const [channels, setChannels] = useState<FilterOption[]>([
    { id: 'all', label: 'All channels' },
    { id: 'PT', label: 'Pantaloons (PT)' },
    { id: 'SSL', label: 'Shoppers Stop (SSL)' },
    { id: 'CENTRO', label: 'Centro' },
    { id: 'ICONIC', label: 'Iconic' },
    { id: 'EBO', label: 'EBO' },
  ]);
  const [stores, setStores] = useState<FilterOption[]>([{ id: 'all', label: 'All stores' }]);
  const [months, setMonths] = useState<FilterOption[]>([{ id: 'all', label: 'All months' }]);
  const cachedProductLists = readCached<ProductFilterLists>(
    productFilterCacheKey(
      productFilterQuery({
        year: String(defaultFyEnd),
        timeline_mode: 'fy_year',
        timeline_values: defaultFyToken,
      })
    )
  );
  const [skus, setSkus] = useState<FilterOption[]>(() => toSkuOptions(cachedProductLists?.skus));
  const [seasons, setSeasons] = useState<FilterOption[]>(() => toSkuOptions(cachedProductLists?.seasons, 'All seasons'));
  const [apiBarcodes, setApiBarcodes] = useState<string[]>(() =>
    (cachedProductLists?.barcodes || []).map((b) => String(b).trim()).filter(Boolean)
  );
  const [productListsLoading, setProductListsLoading] = useState(!cachedProductLists?.skus?.length);
  const [rows, setRows] = useState<SalesRow[]>([]);
  /** Larger sample for charts/breakdown — independent of table pagination (page_size 20). */
  const [chartRows] = useState<SalesRow[]>([]);
  const [kpis, setKpis] = useState<SalesKpis | null>(null);
  const [insights, setInsights] = useState<SalesInsights | null>(null);
  const [total, setTotal] = useState(0);
  const [loadingKpis, setLoadingKpis] = useState(true);
  const [loadingTrend, setLoadingTrend] = useState(true);
  const [loadingBreakdown, setLoadingBreakdown] = useState(true);
  const [loadingChannel, setLoadingChannel] = useState(true);
  const [trendGrain, setTrendGrain] = useState<TrendGrain>('month');
  const [breakdownDim, setBreakdownDim] = useState<BreakdownDim>('zone');
  const [showYearWise, setShowYearWise] = useState(false);
  const [showMomTable, setShowMomTable] = useState(false);
  const [loadingRows, setLoadingRows] = useState(true);
  const loading = loadingKpis || loadingTrend || loadingRows;
  const [error, setError] = useState<string | null>(null);
  const [targetSummary, setTargetSummary] = useState<TargetSummary | null>(null);
  const [targetLoading, setTargetLoading] = useState(true);
  const [masterFilters, setMasterFilters] = useState<CustomerMasterFiltersResponse | null>(
    cachedCustomer?.options ? cachedCustomer : null
  );
  const [masterFiltersLoading, setMasterFiltersLoading] = useState(!cachedCustomer?.options);
  const [itemMasterFilters, setItemMasterFilters] = useState<ItemMasterFiltersResponse | null>(
    cachedItems?.options ? cachedItems : null
  );
  const [itemMasterLoading, setItemMasterLoading] = useState(!cachedItems?.options);
  const [dateBounds, setDateBounds] = useState<{ min?: string; max?: string }>({
    min: '2025-04-01',
    max: defaultFyTo,
  });

  useEffect(() => {
    const keys = months
      .map((m) => m.id)
      .filter((id) => /^\d{4}-\d{2}$/.test(id))
      .sort();
    if (!keys.length) return;
    const rawMin = `${keys[0]}-01`;
    const min = rawMin < '2025-04-01' ? '2025-04-01' : rawMin;
    const [y, mo] = keys[keys.length - 1].split('-').map(Number);
    const lastDay = new Date(y, mo, 0).getDate();
    const max = `${keys[keys.length - 1]}-${String(lastDay).padStart(2, '0')}`;
    setDateBounds((prev) => ({
      min: !prev.min || min < prev.min ? min : prev.min,
      max: !prev.max || max > prev.max ? max : prev.max,
    }));
  }, [months]);

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

  useEffect(() => {
    let cancelled = false;
    const pickedStores = clientFilters.siteCodes.length > 0 || clientFilters.siteNames.length > 0;
    const sites = masterFilters?.sites;
    let siteIds: string | undefined;
    if (pickedStores && sites?.length) {
      const allowed = resolveSiteKeysFromMasterFilters(sites, clientFilters);
      if (allowed !== null) siteIds = allowed.size === 0 ? '__none__' : [...allowed].join(',');
    }
    const query = productFilterQuery({
      year: String(salesYear),
      channel: clientFilters.salesChannels.length ? clientFilters.salesChannels.join(',') : undefined,
      month: monthId !== 'all' ? monthId : undefined,
      zones: clientFilters.zones.length ? clientFilters.zones.join(',') : undefined,
      store_grades: clientFilters.storeGrades.length ? clientFilters.storeGrades.join(',') : undefined,
      states: clientFilters.states.length ? clientFilters.states.join(',') : undefined,
      cities: clientFilters.cities.length ? clientFilters.cities.join(',') : undefined,
      departments: clientFilters.departments.length ? clientFilters.departments.join(',') : undefined,
      sections: clientFilters.sections.length ? clientFilters.sections.join(',') : undefined,
      site_id: storeId !== 'all' ? storeId : undefined,
      site_ids: siteIds,
      timeline_mode: timelineValues.length ? timelineMode : undefined,
      timeline_values: timelineValues.length ? timelineValues.join(',') : undefined,
    });
    if (productQueryRef.current === query) return;
    productQueryRef.current = query;
    const cached = readCached<ProductFilterLists>(productFilterCacheKey(query));
    if (!cached?.skus?.length) setProductListsLoading(true);
    fetchOnce<ProductFilterLists>(
      productFilterCacheKey(query),
      `${API_URL}/sales-dashboard/product-filter-options?${query}`,
      (data) => data?.ok === true && Array.isArray(data.skus)
    )
      .then((data) => {
        if (cancelled || !data?.skus) return;
        setSkus(toSkuOptions(data.skus));
        setSeasons(toSkuOptions(data.seasons, 'All seasons'));
        setApiBarcodes((data.barcodes || []).map((b) => String(b).trim()).filter(Boolean));
      })
      .catch(() => {
        // Keep the last good lists on screen.
      })
      .finally(() => {
        if (!cancelled) setProductListsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [
    salesYear,
    monthId,
    storeId,
    masterFilters?.sites,
    clientFilters,
    timelineMode,
    timelineValues,
  ]);

  useEffect(() => {
    const t = window.setTimeout(() => setDebouncedSearch(search.trim()), 350);
    return () => window.clearTimeout(t);
  }, [search]);

  useEffect(() => {
    const t = window.setTimeout(() => setDebouncedTableSearch(tableSearch.trim()), 350);
    return () => window.clearTimeout(t);
  }, [tableSearch]);

  const buildFilterParams = useCallback(() => {
    const seasonParam =
      clientFilters.seasons.length ? clientFilters.seasons.join(',') : 'all';
    // Prefer explicit Sales Channel filter over legacy channel dropdown
    const channelParam =
      clientFilters.salesChannels.length > 0
        ? clientFilters.salesChannels.join(',')
        : channelId;
      const params = new URLSearchParams({
      year: String(salesYear),
      channel: channelParam,
        site_id: storeId,
        month: monthId,
        sku: clientFilters.skus.length ? clientFilters.skus.join(',') : skuId,
      season: seasonParam,
        search: debouncedSearch,
    });
    const sites = masterFilters?.sites;
    if (sites?.length) {
      const allowed = resolveSiteKeysFromMasterFilters(sites, clientFilters);
      if (allowed !== null) {
        if (allowed.size === 0) {
          params.set('site_ids', '__none__');
        } else {
          params.set('site_ids', [...allowed].join(','));
        }
      }
    }
    // Same as Home — zone/state/city/grade must reach the API (not only resolved site_ids)
    if (clientFilters.zones.length) {
      params.set('zones', clientFilters.zones.join(','));
    }
    if (clientFilters.storeGrades.length) {
      params.set('store_grades', clientFilters.storeGrades.join(','));
    }
    if (clientFilters.states.length) {
      params.set('states', clientFilters.states.join(','));
    }
    if (clientFilters.cities.length) {
      params.set('cities', clientFilters.cities.join(','));
    }
    if (clientFilters.departments.length) {
      params.set('departments', clientFilters.departments.join(','));
    }
    if (clientFilters.sections.length) {
      params.set('sections', clientFilters.sections.join(','));
    }
    if (clientFilters.barcodes.length) {
      params.set('barcodes', clientFilters.barcodes.join(','));
    }
    if (timelineValues.length > 0) {
      params.set('timeline_mode', timelineMode);
      params.set('timeline_values', timelineValues.join(','));
    }
    return params;
  }, [salesYear, channelId, storeId, monthId, skuId, clientFilters, debouncedSearch, masterFilters?.sites, timelineMode, timelineValues]);

  const filterQuery = buildFilterParams().toString();

  /**
   * Progressive load — each slice paints as soon as its API returns (Home-style):
   *   /kpis → cards (qty sold + zone-scoped SOH)
   *   /insights → only the trend grain and breakdown tab on screen
   *   /rows → the visible table page only (next page, search, and sort hit this again)
   * All share the same filterQuery so season/store/timeline stay in sync.
   */
  const lastFilterQueryRef = useRef<string | null>(null);
  const latestFetchId = useRef(0);

  const fetchData = useCallback(async () => {
    if (abortRef.current) abortRef.current.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const signal = controller.signal;
    const requestId = ++latestFetchId.current;

    const filterChanged =
      lastFilterQueryRef.current === null || lastFilterQueryRef.current !== filterQuery;
    lastFilterQueryRef.current = filterQuery;

    setLoadingRows(true);
    setError(null);
    if (filterChanged) {
      setLoadingKpis(true);
      setKpis(null);
      setRows([]);
      setTotal(0);
    }

    const baseParams = new URLSearchParams(filterQuery);
    const errors: string[] = [];
    const stillCurrent = () => requestId === latestFetchId.current && !signal.aborted;

    const loadKpis = async () => {
      if (!filterChanged) return;
      try {
        const res = await fetch(`${API_URL}/sales-dashboard/kpis?${baseParams}`, { signal });
        if (!stillCurrent()) return;
        const data = await readApiJson<SalesKpisResponse>(res);
        if (!stillCurrent()) return;
        if (!res.ok) {
          throw new Error(typeof data.detail === 'string' ? data.detail : `KPIs failed (${res.status})`);
        }
        setKpis(data.kpis);
        if (typeof data.total === 'number') setTotal(data.total);
      } catch (err: unknown) {
        if ((err as { name?: string }).name === 'AbortError') return;
        if (!stillCurrent()) return;
        errors.push(err instanceof Error ? err.message : 'KPIs failed');
        setKpis(null);
      } finally {
        if (stillCurrent()) setLoadingKpis(false);
      }
    };

    const loadRows = async () => {
      try {
        const params = new URLSearchParams(baseParams);
        params.set('page', String(page));
        params.set('page_size', String(pageSize));
        params.set('sort', tableSortKey);
        params.set('sort_dir', tableSortDir);
        params.set('search', debouncedTableSearch);
        params.set('include_options', '0');
        const res = await fetch(`${API_URL}/sales-dashboard/rows?${params}`, { signal });
        if (!stillCurrent()) return;
        const data = await readApiJson<SalesRowsResponse>(res);
        if (!stillCurrent()) return;
        if (!res.ok) {
          throw new Error(typeof data.detail === 'string' ? data.detail : `Rows failed (${res.status})`);
        }
        if (data.channels?.length) setChannels(data.channels);
        if (data.stores?.length) setStores(data.stores);
        if (data.months?.length) setMonths(data.months);
        if (data.skus?.length) setSkus(data.skus);
        if (data.seasons?.length) setSeasons(data.seasons);
        if (data.barcodes?.length) setApiBarcodes(data.barcodes);
        setRows(data.rows || []);
        setTotal(data.total || 0);
      } catch (err: unknown) {
        if ((err as { name?: string }).name === 'AbortError') return;
        if (!stillCurrent()) return;
        errors.push(err instanceof Error ? err.message : 'Table failed');
        setRows([]);
        setTotal(0);
      } finally {
        if (stillCurrent()) setLoadingRows(false);
      }
    };

    // Table asks for one page only. Next, search, and sort each send their own request.
    await Promise.all([loadKpis(), loadRows()]);
    if (stillCurrent() && errors.length) {
      setError(errors.join(' · '));
    }
  }, [filterQuery, page, pageSize, debouncedTableSearch, tableSortKey, tableSortDir]);

  useEffect(() => {
    const timer = window.setTimeout(() => { void fetchData(); }, 300);
    return () => { window.clearTimeout(timer); abortRef.current?.abort(); };
  }, [fetchData]);

  const loadedInsightRef = useRef<{ query: string; keys: Set<string> }>({ query: '', keys: new Set() });

  const insightCardMissing = (card: string) =>
    (INSIGHT_CARD_KEYS[card] ?? []).some((key) => !loadedInsightRef.current.keys.has(key));

  const resetInsightsIfQueryChanged = (query: string) => {
    if (loadedInsightRef.current.query === query) return;
    loadedInsightRef.current = { query, keys: new Set() };
    setInsights(null);
    setLoadingTrend(true);
    setLoadingBreakdown(true);
    setLoadingChannel(true);
  };

  const storeInsightCard = (query: string, card: string, incoming: SalesInsights) => {
    if (loadedInsightRef.current.query !== query) return;
    const keys = INSIGHT_CARD_KEYS[card] ?? [];
    setInsights((prev) => {
      const next: SalesInsights = { ...(prev ?? emptyInsights()) };
      for (const key of keys) {
        const value = incoming[key];
        if (value !== undefined) {
          (next as Record<string, unknown>)[key] = value;
        }
      }
      return next;
    });
    for (const key of keys) loadedInsightRef.current.keys.add(key);
  };

  useEffect(() => {
    const controller = new AbortController();
    const query = filterQuery;
    const card = trendGrain === 'week' ? 'trend_week' : trendGrain === 'year' ? 'trend_year' : 'trend_month';
    resetInsightsIfQueryChanged(query);
    if (!insightCardMissing(card)) {
      setLoadingTrend(false);
      return;
    }
    setLoadingTrend(true);
    const loadTrend = async () => {
      try {
        const params = new URLSearchParams(query);
        params.set('cards', card);
        const res = await fetch(`${API_URL}/sales-dashboard/insights?${params}`, { signal: controller.signal });
        const data = await readApiJson<SalesInsightsResponse>(res);
        if (controller.signal.aborted || loadedInsightRef.current.query !== query) return;
        if (!res.ok) {
          throw new Error(typeof data.detail === 'string' ? data.detail : `Insights failed (${res.status})`);
        }
        storeInsightCard(query, card, data.insights ?? emptyInsights());
      } catch (err: unknown) {
        if ((err as { name?: string }).name === 'AbortError') return;
        if (controller.signal.aborted || loadedInsightRef.current.query !== query) return;
        setError((prev) => prev || (err instanceof Error ? err.message : 'Insights failed'));
      } finally {
        if (!controller.signal.aborted) setLoadingTrend(false);
      }
    };
    void loadTrend();
    return () => controller.abort();
  }, [filterQuery, trendGrain]);

  useEffect(() => {
    const controller = new AbortController();
    const query = filterQuery;
    const card = BREAKDOWN_CARD[breakdownDim];
    const markLoading = (on: boolean) => {
      if (card === 'channel') setLoadingChannel(on);
      else setLoadingBreakdown(on);
    };
    resetInsightsIfQueryChanged(query);
    if (!insightCardMissing(card)) {
      markLoading(false);
      return;
    }
    markLoading(true);
    const loadBreakdown = async () => {
      try {
        const params = new URLSearchParams(query);
        params.set('cards', card);
        const res = await fetch(`${API_URL}/sales-dashboard/insights?${params}`, { signal: controller.signal });
        const data = await readApiJson<SalesInsightsResponse>(res);
        if (controller.signal.aborted || loadedInsightRef.current.query !== query) return;
        if (!res.ok) {
          throw new Error(typeof data.detail === 'string' ? data.detail : `Insights failed (${res.status})`);
        }
        storeInsightCard(query, card, data.insights ?? emptyInsights());
      } catch (err: unknown) {
        if ((err as { name?: string }).name === 'AbortError') return;
        if (controller.signal.aborted || loadedInsightRef.current.query !== query) return;
        setError((prev) => prev || (err instanceof Error ? err.message : 'Insights failed'));
      } finally {
        if (!controller.signal.aborted) markLoading(false);
      }
    };
    void loadBreakdown();
    return () => controller.abort();
  }, [filterQuery, breakdownDim]);

  useEffect(() => {
    if (storeId !== 'all' && !stores.some((s) => s.id === storeId)) {
      setStoreId('all');
      setPage(1);
    }
  }, [stores, storeId]);

  useEffect(() => {
    if (monthId !== 'all' && !months.some((m) => m.id === monthId)) {
      setMonthId('all');
      setPage(1);
    }
  }, [months, monthId]);

  useEffect(() => {
    if (skuId !== 'all' && !skus.some((s) => s.id === skuId)) {
      setSkuId('all');
      setPage(1);
    }
  }, [skus, skuId]);

  // Drop SKU / barcode selections that are no longer in the cascaded option lists
  // (e.g. user picked Season=SS-26 → previous AW-25 SKUs must clear).
  useEffect(() => {
    if (loading) return;
    const skuIds = new Set(
      skus.filter((s) => s.id !== 'all').map((s) => String(s.id).trim().toUpperCase())
    );
    const bcIds = new Set(apiBarcodes.map((b) => String(b).trim().toUpperCase().replace(/\.0$/, '')));
    setClientFilters((prev) => {
      let changed = false;
      let nextSkus = prev.skus;
      let nextBcs = prev.barcodes;
      if (skuIds.size && prev.skus.length) {
        nextSkus = prev.skus.filter((s) => skuIds.has(String(s).trim().toUpperCase()));
        if (nextSkus.length !== prev.skus.length) changed = true;
      }
      if (bcIds.size && prev.barcodes.length) {
        nextBcs = prev.barcodes.filter((b) =>
          bcIds.has(String(b).trim().toUpperCase().replace(/\.0$/, ''))
        );
        if (nextBcs.length !== prev.barcodes.length) changed = true;
      }
      if (!changed) return prev;
      return { ...prev, skus: nextSkus, barcodes: nextBcs };
    });
  }, [skus, apiBarcodes, loading]);

  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const channelLabel = channels.find((c) => c.id === channelId)?.label ?? 'All channels';

  useEffect(() => {
    setPage(1);
  }, [timelineMode, timelineValues]);

  // Keep API year + date bounds in sync with timeline (calendar Year vs FY)
  useEffect(() => {
    if (timelineMode === 'year' && timelineValues.length > 0) {
      const years = timelineValues
        .filter((v) => /^\d{4}$/.test(v))
        .map(Number)
        .filter((y) => y >= 2000 && y <= 2100)
        .sort((a, b) => a - b);
      if (!years.length) return;
      const y0 = years[0];
      const y1 = years[years.length - 1];
      setYearFilterType('year');
      setSelectedYearVal(String(y1));
      setSalesYear(y1);
      setDateBounds({ min: `${y0}-01-01`, max: `${y1}-12-31` });
      return;
    }
    if (timelineMode === 'fy_year' && timelineValues.length > 0) {
      const raw = String(timelineValues[0] || '')
        .toUpperCase()
        .replace(/\s/g, '');
      const m = raw.match(/(\d{2,4})$/);
      if (!m) return;
      let fyEnd = Number(m[1]);
      if (fyEnd < 100) fyEnd += 2000;
      setYearFilterType('fy');
      setSelectedYearVal(String(fyEnd));
      setSalesYear(fyEnd);
      setDateBounds({ min: `${fyEnd - 1}-04-01`, max: `${fyEnd}-03-31` });
      return;
    }
    if (timelineMode === 'date' && timelineValues.length >= 2) {
      const from = timelineValues[0]?.slice(0, 10) || '';
      const to = (timelineValues[1] || timelineValues[0] || '').slice(0, 10);
      if (/^\d{4}-\d{2}-\d{2}$/.test(from) && /^\d{4}-\d{2}-\d{2}$/.test(to)) {
        setDateBounds({ min: from, max: to });
        const fromM = Number(from.slice(5, 7));
        const toM = Number(to.slice(5, 7));
        if (from.endsWith('-04-01') && to.endsWith('-03-31')) {
          setYearFilterType('fy');
          setSalesYear(Number(to.slice(0, 4)));
        } else if (from.endsWith('-01-01') && (to.endsWith('-12-31') || fromM === 1)) {
          setYearFilterType('year');
          setSalesYear(Number(to.slice(0, 4)));
        }
      }
    }
  }, [timelineMode, timelineValues]);

  const clearAllFilters = () => {
    const [fyFrom, fyTo] = [defaultFyFrom, defaultFyTo];
    const fyEnd = defaultFyEnd;
    setClientFilters(EMPTY_CLIENT_FILTERS);
    setTimelineMode('fy_year');
    setTimelineValues(['FY27']);
    setYearFilterType('fy');
    setSalesYear(fyEnd);
    setSelectedYearVal(String(fyEnd));
    setDateBounds({ min: fyFrom, max: fyTo });
    setChannelId('all');
    setStoreId('all');
    setMonthId('all');
    setSkuId('all');
    setSearch('');
    setDebouncedSearch('');
    setCrossFilter(null);
    setDrillPath([]);
    setTableSearch('');
    setDebouncedTableSearch('');
    setTableSortKey('sales');
    setTableSortDir('desc');
    setPage(1);
  };

  const analyticsRows = useMemo(() => {
    let data = chartRows;
    // Timeline filter is applied server-side on the full dataset (KPIs + insights + rows).
    if (drillPath.length) {
      data = data.filter((row) =>
        drillPath.every((step) => {
          const level = DRILL_ORDER.find((l) => l === step.level) ?? 'zone';
          return drillKey(row, level) === step.value;
        })
      );
    }
    if (crossFilter) {
      data = data.filter(
        (row) =>
          drillKey(row, 'zone') === crossFilter ||
          row.site_name === crossFilter ||
          drillKey(row, 'state') === crossFilter ||
          drillKey(row, 'city') === crossFilter ||
          drillKey(row, 'department') === crossFilter ||
          drillKey(row, 'section') === crossFilter ||
          deriveSalesChannel(row) === crossFilter ||
          row.sku === crossFilter
      );
    }
    return data;
  }, [chartRows, clientFilters, drillPath, crossFilter, masterFilters?.sites]);

  /** Options for timeline multi-select — before timeline value filter is applied */
  const timelineSourceRows = useMemo(
    () => applyClientFilters(chartRows, clientFilters, masterFilters?.sites),
    [chartRows, clientFilters, masterFilters?.sites]
  );

  const masterFilterOptions = useMemo(
    () => buildCascadingFilterOptions(masterFilters, clientFilters, chartRows, itemMasterFilters),
    [masterFilters, itemMasterFilters, clientFilters, chartRows]
  );

  const filteredTableRows = useMemo(
    () => rows,
    [rows]
  );

  // Trends/KPIs must come from backend insights — never rebuild from paginated table rows.
  const monthlyTrendData = useMemo(
    () => insights?.monthly_trend ?? [],
    [insights?.monthly_trend]
  );

  const weeklyTrendData = useMemo(
    () => insights?.weekly_trend ?? [],
    [insights?.weekly_trend]
  );

  const yearlyTrendData = useMemo(
    () => insights?.yearly_trend ?? [],
    [insights?.yearly_trend]
  );

  const extendedKpis = useMemo(() => {
    // Always map from backend kpis — never recompute from the visible table page.
    return computeExtendedKpis(kpis, []);
  }, [kpis]);

  const fullPricePct = useMemo(
    () => (kpis?.full_price_pct != null ? Number(kpis.full_price_pct) : 0),
    [kpis]
  );
  const ros = useMemo(() => Number(kpis?.ros) || 0, [kpis]);

  // MoM / WoW / YoY growth — prefer backend kpis fields; trend helpers only as last resort.
  const monthGrowth = useMemo(() => {
    if (
      kpis &&
      (kpis.mom_net_pct != null ||
        kpis.mom_gross_pct != null ||
        kpis.mom_asp_pct != null ||
        kpis.mom_discount_pct != null ||
        kpis.mom_units_pct != null)
    ) {
      return {
        gross: kpis.mom_gross_pct ?? null,
        net: kpis.mom_net_pct ?? null,
        discount: kpis.mom_discount_pct ?? null,
        units: kpis.mom_units_pct ?? null,
        asp: kpis.mom_asp_pct ?? null,
      };
    }
    return computeMonthGrowth(monthlyTrendData);
  }, [kpis, monthlyTrendData]);

  const growthRates = useMemo(() => {
    const fromTrends = computeSalesGrowthRates(monthlyTrendData, [], weeklyTrendData, yearlyTrendData);
    const goly =
      kpis?.yoy_sales_growth_pct != null && Number.isFinite(Number(kpis.yoy_sales_growth_pct))
        ? Number(kpis.yoy_sales_growth_pct)
        : fromTrends.goly;
    const golyUnits =
      kpis?.yoy_qty_growth_pct != null && Number.isFinite(Number(kpis.yoy_qty_growth_pct))
        ? Number(kpis.yoy_qty_growth_pct)
        : fromTrends.golyUnits;
    const golm =
      kpis?.golm_pct != null && Number.isFinite(Number(kpis.golm_pct))
        ? Number(kpis.golm_pct)
        : fromTrends.golm;
    const golw =
      kpis?.golw_pct != null && Number.isFinite(Number(kpis.golw_pct))
        ? Number(kpis.golw_pct)
        : fromTrends.golw;
    return { goly, golyUnits, golm, golw };
  }, [monthlyTrendData, weeklyTrendData, yearlyTrendData, kpis]);

  const targetChannel = useMemo(() => {
    if (clientFilters.salesChannels.length) return clientFilters.salesChannels.join(',');
    if (channelId && channelId !== 'all') return channelId;
    return 'all';
  }, [clientFilters.salesChannels, channelId]);

  const targetSiteIds = useMemo(() => {
    const sites = masterFilters?.sites ?? [];
    const allowed = resolveSiteKeysFromMasterFilters(sites, clientFilters);
    if (!allowed) return null;
    return Array.from(allowed);
  }, [masterFilters?.sites, clientFilters]);

  const targetSiteIdsKey = useMemo(() => {
    if (!targetSiteIds) return '';
    return targetSiteIds.length ? targetSiteIds.slice().sort().join(',') : '__none__';
  }, [targetSiteIds]);

  const momSiteIds = useMemo(() => {
    if (!targetSiteIds) return '';
    const codes = (masterFilters?.sites ?? [])
      .filter((site) => targetSiteIds.includes(site.site_key))
      .map((site) => site.site_code)
      .filter(Boolean);
    return codes.length ? codes.join(',') : '__none__';
  }, [targetSiteIds, masterFilters?.sites]);

  const productTargetUnavailable =
    clientFilters.departments.length > 0 ||
    clientFilters.sections.length > 0 ||
    clientFilters.skus.length > 0 ||
    clientFilters.barcodes.length > 0;

  const timelineValuesKey = timelineValues.join(',');
  const lastTargetReqId = useRef(0);

  useEffect(() => {
    const reqId = ++lastTargetReqId.current;
    setTargetSummary(null);
    if (productTargetUnavailable) {
      setTargetLoading(false);
      return;
    }
    setTargetLoading(true);
    const controller = new AbortController();
    const targetGrain: TargetGrain = 'ytd';
    const fallbackYear = Number(salesYear) || new Date().getFullYear();
    const dateParams = buildTargetDateParams(
      timelineMode,
      timelineValues,
      targetGrain,
      fallbackYear,
      monthId
    );

    const loadTarget = async () => {
      const params = new URLSearchParams({
        period: 'month',
        count: '24',
        channel: targetChannel,
        site_id: storeId !== 'all' ? storeId : 'all',
      });
      if (targetSiteIds !== null) {
        const codes = (masterFilters?.sites ?? [])
          .filter((site) => targetSiteIds.includes(site.site_key))
          .map((site) => site.site_code)
          .filter(Boolean);
        params.set('site_ids', codes.length ? codes.join(',') : '__none__');
      }
      params.set('timeline_mode', timelineMode);
      params.set('timeline_values', timelineValues.join(','));
      if (dateParams.date_from) params.set('date_from', dateParams.date_from);
      if (dateParams.date_to) params.set('date_to', dateParams.date_to);
      const res = await fetch(`${API_URL}/inventory-analytics/target-vs-achieved?${params}`, {
        signal: controller.signal,
      });
      if (!res.ok) return null;
      return readApiJson(res);
    };

    void (async () => {
      try {
        const data: any = await loadTarget();
        if (reqId !== lastTargetReqId.current) return;
        if (!data?.series?.length && !data?.current && !data?.range_summary) {
          setTargetSummary(null);
          return;
        }
        setTargetSummary(
          pickTargetSummaryFromApi(data, {
            grain: targetGrain,
            timelineMode,
            timelineValues,
            monthId,
          })
        );
      } catch (err: unknown) {
        if ((err as { name?: string }).name === 'AbortError') return;
        if (reqId === lastTargetReqId.current) setTargetSummary(null);
      } finally {
        if (reqId === lastTargetReqId.current) setTargetLoading(false);
      }
    })();
    return () => {
      controller.abort();
      lastTargetReqId.current += 1;
    };
  }, [
    productTargetUnavailable,
    targetChannel,
    timelineMode,
    timelineValuesKey,
    monthId,
    storeId,
    targetSiteIdsKey,
    salesYear,
    masterFilters?.sites,
    targetSiteIds,
  ]);

  const seasonAchievementNa = clientFilters.seasons.length > 0;

  const kpiGridData = useMemo(() => {
    const hasTarget = !productTargetUnavailable && (targetSummary?.totalTarget ?? 0) > 0;
    return {
      ...extendedKpis,
      discountPct:
        kpis?.discount_pct != null
          ? Number(kpis.discount_pct)
          : kpis?.discount_on_gross_pct != null
            ? Number(kpis.discount_on_gross_pct)
            : undefined,
      ros,
      monthGrowth,
      growthRates,
      target: hasTarget ? targetSummary?.totalTarget : undefined,
      targetUnavailable: productTargetUnavailable
        ? false
        : targetSummary != null && !((targetSummary.totalTarget ?? 0) > 0),
      targetAchievementPct: hasTarget && !seasonAchievementNa ? targetSummary?.achievement : undefined,
      targetActual: hasTarget && !seasonAchievementNa ? targetSummary?.totalActual : undefined,
      targetPeriodLabel: targetSummary?.periodLabel,
      targetSitesWithTarget: targetSummary?.sitesWithTarget ?? targetSummary?.stores,
      targetSitesWithAchieved: targetSummary?.sitesWithAchieved,
    };
  }, [extendedKpis, kpis, ros, monthGrowth, growthRates, targetSummary, productTargetUnavailable, seasonAchievementNa]);

  const eanOptions = useMemo(() => {
    // API barcodes are already scoped to season/channel/site filters from the last fetch.
    if (apiBarcodes.length) return apiBarcodes;
    if (masterFilterOptions.barcodes?.length) return masterFilterOptions.barcodes;
    return [...new Set(chartRows.map((r) => r.barcode).filter(Boolean))];
  }, [apiBarcodes, masterFilterOptions.barcodes, chartRows]);

  const handleDrillDown = (level: string, value: string) => {
    const mapped = (level === 'store' ? 'store' : level) as (typeof DRILL_ORDER)[number];
    setDrillPath((prev) => {
      const idx = DRILL_ORDER.indexOf(mapped);
      const next = idx >= 0 ? prev.slice(0, idx) : [...prev];
      next.push({ level: mapped, value });
      return next;
    });
    setCrossFilter(value);
  };

  const handleDrillNavigate = (index: number) => {
    setDrillPath((prev) => prev.slice(0, index + 1));
    const last = drillPath[index];
    setCrossFilter(last?.value ?? null);
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
      title="Toggle Sales Filters Drawer"
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
          icon={TrendingUp}
          title="Sales Overview"
          compact
          loading={loading}
          onRefresh={() => fetchData()}
        >
          <div className="w-full">
            <TimelineFilterPanel
              financialCalendar={financialCalendar}
              variant="header"
              mode={timelineMode}
              values={timelineValues}
              rows={timelineSourceRows}
              dateMin={dateBounds.min}
              dateMax={dateBounds.max}
              monthOptions={months}
              loading={
                (loadingRows && months.length === 0) ||
                (!financialCalendar && (timelineMode === 'fy_week' || timelineMode === 'fy_year'))
              }
              onModeChange={setTimelineMode}
              onValuesChange={setTimelineValues}
              onClear={() => setTimelineValues([])}
            />
          </div>
        </PageHeader>

        {/* Filters — compact, open by default */}
        <SalesFilterPanel
          sticky={false}
          compact
          clientFilters={clientFilters}
          stores={stores}
          skus={skus}
          seasons={seasons}
          barcodeOptions={eanOptions}
          filterOptions={masterFilterOptions}
          loading={productListsLoading}
          masterLoading={masterFiltersLoading}
          itemMasterLoading={itemMasterLoading}
          onClientChange={(key, value) => {
            setClientFilters((prev) => ({ ...prev, [key]: value }));
                    setPage(1);
                  }}
          onClearAll={clearAllFilters}
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
                      onClick={clearAllFilters}
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
                  stores={stores}
                  skus={skus}
                  seasons={seasons}
                  barcodeOptions={eanOptions}
                  filterOptions={masterFilterOptions}
                  loading={productListsLoading}
                  masterLoading={masterFiltersLoading}
                  itemMasterLoading={itemMasterLoading}
                  onClientChange={(key, value) => {
                    setClientFilters((prev) => ({ ...prev, [key]: value }));
                  setPage(1);
                }}
                  onClearAll={clearAllFilters}
                />
              </div>
          </div>
        </div>
        )}

        {error && (
          <div className="flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-300">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {drillPath.length > 0 && (
          <DrillDownBreadcrumb path={drillPath} onNavigate={handleDrillNavigate} />
        )}

        <div className="space-y-4">
          {/* Premium KPI grid — shimmer cards while filters fetch */}
          <SalesKpiGrid
            data={kpiGridData}
            loading={loadingKpis}
            targetLoading={targetLoading}
          />

          <SalesTrendBreakdownRow
            rows={analyticsRows}
            insights={insights}
            monthlyTrendData={monthlyTrendData}
            channelLabel={channelLabel}
            loading={breakdownDim === 'channel' ? loadingChannel : loadingBreakdown}
            chartLoading={loadingTrend}
            activeKey={crossFilter}
            onCrossFilter={handleDrillDown}
            timelineMode={timelineMode}
            timelineValues={timelineValues}
            targetSummary={targetSummary}
            trendGrain={trendGrain}
            onTrendGrainChange={setTrendGrain}
            breakdownDim={breakdownDim}
            onBreakdownDimChange={setBreakdownDim}
          />

          <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-slate-200/80 bg-white px-4 py-3 dark:border-slate-800 dark:bg-slate-900/60">
            <div>
              <h3 className="text-sm font-semibold text-slate-900 dark:text-white">
                {timelineMode === 'year'
                  ? 'Month-on-Month (MoM) Trend Comparison (Jan → Dec)'
                  : 'Month-on-Month (MoM) Trend Comparison (Apr → Mar)'}
              </h3>
              <p className="mt-0.5 text-[11px] text-slate-500 dark:text-slate-400">
                Sales lines stay on this page. Each option calls the API only when you open it.
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => setShowYearWise((open) => !open)}
                className={`rounded-lg px-3 py-1.5 text-xs font-semibold ${
                  showYearWise
                    ? 'bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-200'
                    : 'bg-blue-600 text-white'
                }`}
              >
                {showYearWise ? 'Hide year-wise' : 'Show year-wise'}
              </button>
              <button
                type="button"
                onClick={() => setShowMomTable((open) => !open)}
                className={`rounded-lg px-3 py-1.5 text-xs font-semibold ${
                  showMomTable
                    ? 'bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-200'
                    : 'bg-blue-600 text-white'
                }`}
              >
                {showMomTable ? 'Hide month table' : 'Show month table'}
              </button>
            </div>
          </div>

          {(showYearWise || showMomTable) && (
            <FinancialYearSalesComparison
              chartOnly
              showYearView={showYearWise}
              showDetailTable={showMomTable}
              enabled
              filterQuery={filterQuery}
              siteId={storeId}
              siteIds={momSiteIds}
              sourceId={channelId}
              departments={clientFilters.departments}
              sections={clientFilters.sections}
              seasons={clientFilters.seasons}
              periodMode={timelineMode === 'year' ? 'calendar' : 'fy'}
            />
          )}

          {/* One page from /rows. Next and search request the next slice. */}
          <SalesEnterpriseTable
            rows={filteredTableRows}
            allRows={analyticsRows}
            insights={insights}
            total={total}
            page={page}
            totalPages={totalPages}
            pageSize={pageSize}
            loading={loadingRows}
            onPageChange={setPage}
            tableSearch={tableSearch}
            onTableSearchChange={(value) => {
              setTableSearch(value);
              setPage(1);
            }}
            sortKey={tableSortKey}
            sortDir={tableSortDir}
            onSortChange={(key, dir) => {
              setTableSortKey(key);
              setTableSortDir(dir);
              setPage(1);
            }}
            onClearAllFilters={clearAllFilters}
            filterParams={buildFilterParams()}
          />

          <div className="border-t border-slate-200 pt-6 dark:border-slate-800">
            <div className="mb-4">
              <h2 className="flex flex-wrap items-center gap-2 text-lg font-bold text-slate-900 dark:text-white">
                <span>Sales Pivot Analyzer</span>
                <span className="rounded-full bg-blue-100 px-2 py-0.5 text-xs font-medium text-blue-700 dark:bg-blue-900/40 dark:text-blue-300">
                  Multidimensional Analysis & Matrix
                </span>
              </h2>
              <p className="text-xs text-slate-500 dark:text-slate-400">
                Drag and drop dimensions & measures to configure custom pivot slices, matrix cross-tabs, and download exact reports.
              </p>
              {(() => {
                const cur = _indianFyEnd();
                const visible = [cur - 1, cur];
                if (visible.includes(Number(salesYear))) return null;
                const fallback = cur;
                return (
                  <p className="mt-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
                    Analyzer supports FY{String(cur - 1).slice(-2)}–FY{String(cur).slice(-2)} only.
                    Overview is on FY{String(salesYear).slice(-2)}; pivot uses FY{String(fallback).slice(-2)}.
                  </p>
                );
              })()}
            </div>
            <AnalyzerErrorBoundary>
              <SalesAnalyzerModule
                enabled={!loadingKpis}
                initialYear={salesYear}
                pageContext={{
                  salesYear,
                  timelineMode,
                  timelineValues,
                  clientFilters,
                  masterSites: masterFilters?.sites,
                  storeId,
                  channelId,
                  monthId,
                }}
              />
            </AnalyzerErrorBoundary>
          </div>
          </div>
      </DashboardShell>
    </DashboardLayout>
  );
}

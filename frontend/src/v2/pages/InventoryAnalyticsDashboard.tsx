import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { AlertCircle, CalendarDays, Package, Scale, Store, Truck, Warehouse, X } from 'lucide-react';
import DashboardLayout from '../layouts/DashboardLayout';
import { DashboardShell, PageHeader } from '../design-system';
import { API_URL } from '../../config';
import { fetchOnce, FILTER_CACHE_KEYS, readCached } from '../lib/dashboardFilterCache';
import {
  InventoryFilterPanel,
  EMPTY_CLIENT_FILTERS,
} from '../components/inventory-analytics/InventoryFilterPanel';
import {
  StoreScopePanels,
  WarehouseScopePanels,
} from '../components/inventory-analytics/InventoryScopePanels';
import { DispatchLeadTimeTable } from '../components/inventory-analytics/DispatchLeadTimeTable';
import { SkuBreakevenTable } from '../components/inventory-analytics/SkuBreakevenTable';
import { SohReconciliationPanel } from '../components/inventory-analytics/SohReconciliationPanel';
import {
  applyClientFilters,
  toSalesClientFilters,
  type InventoryScope,
} from '../components/inventory-analytics/inventoryAnalytics';
import type {
  ClientInvFilters,
  FilterOption,
  StockOverviewResponse,
  StockRow,
} from '../components/inventory-analytics/types';
import {
  buildCascadingFilterOptions,
  resolveSiteKeysFromMasterFilters,
  type CustomerMasterFiltersResponse,
  type ItemMasterFiltersResponse,
} from '../components/sales-dashboard/salesAnalytics';

const dateInputClass =
  'min-w-[9.5rem] rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-[11px] font-medium text-slate-700 outline-none transition focus:border-blue-400 focus:ring-2 focus:ring-blue-500/20 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200';

/**
 * Inventory Analytics — Warehouse / Store views.
 * Store Current SOH: daily_inventory_movement (same as Sales Overview) + Customer Master filters.
 */
export default function InventoryAnalyticsDashboard() {
  const [scope, setScope] = useState<InventoryScope>('lead-time');
  /** Inline SOH comparison (calc vs actual) — not a top-level tab */
  const [showSohCompare, setShowSohCompare] = useState(false);
  const [siteId, setSiteId] = useState('all');
  const [sourceId, setSourceId] = useState('all');
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [page, setPage] = useState(1);
  const pageSize = 500;

  const [asOfDraft, setAsOfDraft] = useState('');
  const [appliedAsOf, setAppliedAsOf] = useState<string | null>(null);

  const [clientFilters, setClientFilters] = useState<ClientInvFilters>(EMPTY_CLIENT_FILTERS);
  const [masterFilters, setMasterFilters] = useState<CustomerMasterFiltersResponse | null>(() => {
    const cached = readCached<CustomerMasterFiltersResponse>(FILTER_CACHE_KEYS.customerMaster);
    return cached?.options ? cached : null;
  });
  const [masterFiltersLoading, setMasterFiltersLoading] = useState(
    () => !readCached<CustomerMasterFiltersResponse>(FILTER_CACHE_KEYS.customerMaster)?.options
  );
  const [itemMasterFilters, setItemMasterFilters] = useState<ItemMasterFiltersResponse | null>(() => {
    const cached = readCached<ItemMasterFiltersResponse>(FILTER_CACHE_KEYS.itemMaster);
    return cached?.options ? cached : null;
  });
  const [itemMasterLoading, setItemMasterLoading] = useState(
    () => !readCached<ItemMasterFiltersResponse>(FILTER_CACHE_KEYS.itemMaster)?.options
  );

  const [stores, setStores] = useState<FilterOption[]>([{ id: 'all', label: 'All stores' }]);
  const [sources, setSources] = useState<FilterOption[]>([
    { id: 'all', label: 'All channels' },
    { id: 'PT', label: 'Pantaloons (PT)' },
    { id: 'SSL', label: 'Shoppers Stop (SSL)' },
    { id: 'CENTRO', label: 'Centro' },
    { id: 'ICONIC', label: 'Iconic' },
    { id: 'EBO', label: 'EBO' },
  ]);
  const [rows, setRows] = useState<StockRow[]>([]);
  const [storeSummaries, setStoreSummaries] = useState<any[]>([]);
  const [deptSummaries, setDeptSummaries] = useState<any[]>([]);
  const [stockSummary, setStockSummary] = useState<StockOverviewResponse['summary'] | null>(null);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const t = window.setTimeout(() => setDebouncedSearch(search.trim()), 350);
    return () => window.clearTimeout(t);
  }, [search]);

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
        if (cancelled) return;
        setItemMasterFilters(data?.options ? data : null);
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

  const masterFilterOptions = useMemo(
    () =>
      buildCascadingFilterOptions(
        masterFilters,
        toSalesClientFilters(clientFilters),
        [],
        itemMasterFilters
      ),
    [masterFilters, itemMasterFilters, clientFilters]
  );

  const fetchData = useCallback(
    async (signal?: AbortSignal) => {
      // Only fetch stock-overview when store scope is active
      if (scope !== 'store') {
        setLoading(false);
        return;
      }

      setLoading(true);
      setError(null);
      try {
        const params = new URLSearchParams({
          site_id: siteId,
          source: sourceId,
          search: debouncedSearch,
          page: String(page),
          page_size: String(pageSize),
          soh_source: 'daily',
        });
        const sites = masterFilters?.sites;
        if (sites?.length) {
          const allowed = resolveSiteKeysFromMasterFilters(sites, toSalesClientFilters(clientFilters));
          if (allowed !== null) {
            if (allowed.size === 0) params.set('site_ids', '__none__');
            else if (allowed.size <= 2000) params.set('site_ids', [...allowed].join(','));
          }
        }
        if (appliedAsOf) params.set('as_of', appliedAsOf);
        if (clientFilters.departments.length) {
          params.set('departments', clientFilters.departments.join(','));
        }
        if (clientFilters.sections.length) {
          params.set('sections', clientFilters.sections.join(','));
        }
        if (clientFilters.sizes.length) {
          params.set('sizes', clientFilters.sizes.join(','));
        }
        if (clientFilters.eans.length) {
          params.set('barcodes', clientFilters.eans.join(','));
        }

        const res = await fetch(`${API_URL}/stock-overview?${params}`, { signal });
        const data = (await res.json()) as StockOverviewResponse;
        if (signal?.aborted) return;
        if (!res.ok) {
          throw new Error(
            typeof data.detail === 'string' ? data.detail : `Failed (${res.status})`
          );
        }

        setStores(data.stores?.length ? data.stores : [{ id: 'all', label: 'All stores' }]);
        setSources(
          data.channels?.length
            ? data.channels
            : data.sources?.length
              ? data.sources
              : [
                  { id: 'all', label: 'All channels' },
                  { id: 'PT', label: 'Pantaloons (PT)' },
                  { id: 'SSL', label: 'Shoppers Stop (SSL)' },
                  { id: 'CENTRO', label: 'Centro' },
                  { id: 'ICONIC', label: 'Iconic' },
                  { id: 'EBO', label: 'EBO' },
                ]
        );
        setRows(data.rows || []);
        setStoreSummaries(data.store_summaries || []);
        setDeptSummaries(data.dept_summaries || []);
        setStockSummary(data.summary ?? null);
        setTotal(data.total || 0);
      } catch (err: unknown) {
        if (err instanceof DOMException && err.name === 'AbortError') return;
        setError(err instanceof Error ? err.message : 'Could not load stock data');
        setRows([]);
        setStockSummary(null);
        setTotal(0);
      } finally {
        if (!signal?.aborted) setLoading(false);
      }
    },
    [
      scope,
      siteId,
      sourceId,
      debouncedSearch,
      page,
      masterFilters?.sites,
      clientFilters,
      appliedAsOf,
    ]
  );

  useEffect(() => {
    const controller = new AbortController();
    fetchData(controller.signal);
    return () => controller.abort();
  }, [fetchData]);

  useEffect(() => {
    setPage(1);
  }, [clientFilters, appliedAsOf, sourceId, siteId]);

  useEffect(() => {
    if (siteId !== 'all' && stores.length > 1 && !stores.some((s) => s.id === siteId)) {
      setSiteId('all');
      setPage(1);
    }
  }, [stores, siteId]);

  useEffect(() => {
    if (sourceId !== 'all' && sources.length > 1 && !sources.some((s) => s.id === sourceId)) {
      setSourceId('all');
      setPage(1);
    }
  }, [sources, sourceId]);

  const analyticsRows = useMemo(
    () => applyClientFilters(rows, clientFilters, masterFilters?.sites),
    [rows, clientFilters, masterFilters?.sites]
  );

  /** Full CSV export filters (same as stock-overview fetch, no pagination). */
  const stockExportQuery = useMemo(() => {
    const params = new URLSearchParams({
      site_id: siteId,
      source: sourceId,
      search: debouncedSearch,
      soh_source: 'daily',
    });
    const sites = masterFilters?.sites;
    if (sites?.length) {
      const allowed = resolveSiteKeysFromMasterFilters(sites, toSalesClientFilters(clientFilters));
      if (allowed !== null) {
        if (allowed.size === 0) params.set('site_ids', '__none__');
        else if (allowed.size <= 2000) params.set('site_ids', [...allowed].join(','));
      }
    }
    if (appliedAsOf) params.set('as_of', appliedAsOf);
    if (clientFilters.departments.length) params.set('departments', clientFilters.departments.join(','));
    if (clientFilters.sections.length) params.set('sections', clientFilters.sections.join(','));
    if (clientFilters.sizes.length) params.set('sizes', clientFilters.sizes.join(','));
    if (clientFilters.eans.length) params.set('barcodes', clientFilters.eans.join(','));
    return params.toString();
  }, [
    siteId,
    sourceId,
    debouncedSearch,
    masterFilters?.sites,
    clientFilters,
    appliedAsOf,
  ]);

  const [backendBarcodes, setBackendBarcodes] = useState<string[]>([]);
  const [backendSkus, setBackendSkus] = useState<string[]>([]);

  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const skuOptions = useMemo(
    () => (backendSkus.length ? backendSkus : [...new Set(rows.map((r) => r.sku).filter(Boolean))]),
    [backendSkus, rows]
  );
  const eanOptions = useMemo(
    () => (backendBarcodes.length ? backendBarcodes : [...new Set(rows.map((r) => r.barcode).filter(Boolean))]),
    [backendBarcodes, rows]
  );

  const applyAsOfDate = () => {
    const day = asOfDraft.trim();
    if (!day) {
      setAppliedAsOf(null);
      return;
    }
    setAppliedAsOf(day);
    setPage(1);
  };

  const clearAsOfDate = () => {
    setAsOfDraft('');
    setAppliedAsOf(null);
    setPage(1);
  };

  const clearAll = () => {
    setSiteId('all');
    setSourceId('all');
    setSearch('');
    clearAsOfDate();
    setClientFilters(EMPTY_CLIENT_FILTERS);
    setPage(1);
  };

  return (
    <DashboardLayout>
      <DashboardShell>
        <PageHeader
          icon={Package}
          title="Inventory Analytics"
          onExport={() => window.print()}
        >
          <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <div className="flex flex-wrap items-center gap-2">
              <div className="inline-flex items-center gap-1 rounded-xl border border-slate-200 bg-slate-50 p-1 dark:border-slate-700 dark:bg-slate-800/80">
                {(
                  [
                    { id: 'lead-time' as const, label: 'Dispatch Timing', icon: Truck },
                    { id: 'breakeven' as const, label: 'Breakeven', icon: Scale },
                    { id: 'warehouse' as const, label: 'Warehouse Demand & SOH', icon: Warehouse },
                    { id: 'store' as const, label: 'Store Inventory', icon: Store },
                  ] as const
                ).map((tab) => {
                  const Icon = tab.icon;
                  const active = scope === tab.id;
                  return (
                    <button
                      key={tab.id}
                      type="button"
                      onClick={() => {
                        setScope(tab.id);
                        if (tab.id !== 'store') setShowSohCompare(false);
                      }}
                      className={`inline-flex items-center gap-1.5 rounded-lg px-3.5 py-2 text-xs font-semibold transition hover:-translate-y-px active:translate-y-0 sm:px-4 sm:text-sm ${
                        active
                          ? 'bg-slate-900 text-white shadow-sm dark:bg-white dark:text-slate-900'
                          : 'text-slate-600 hover:bg-white hover:text-slate-900 dark:text-slate-300 dark:hover:bg-slate-700'
                      }`}
                    >
                      <Icon className="h-3.5 w-3.5 sm:h-4 sm:w-4" />
                      {tab.label}
                    </button>
                  );
                })}

                {/* Small compare toggle — only beside Store Inventory */}
                {scope === 'store' && (
                  <button
                    type="button"
                    onClick={() => setShowSohCompare((v) => !v)}
                    title={showSohCompare ? 'Hide SOH comparison' : 'Compare calculated vs actual SOH'}
                    aria-label={showSohCompare ? 'Hide SOH comparison' : 'Show SOH comparison'}
                    aria-pressed={showSohCompare}
                    className={`ml-0.5 inline-flex h-8 w-8 items-center justify-center rounded-lg transition ${
                      showSohCompare
                        ? 'bg-slate-900 text-white shadow-sm dark:bg-slate-100 dark:text-slate-900'
                        : 'text-slate-500 hover:bg-white hover:text-slate-800 dark:text-slate-400 dark:hover:bg-slate-700 dark:hover:text-slate-100'
                    }`}
                  >
                    <Scale className="h-3.5 w-3.5" strokeWidth={2.25} />
                  </button>
                )}
              </div>
            </div>

            {scope === 'store' && (
              <div className="flex flex-wrap items-end gap-2 rounded-2xl border border-amber-200/80 bg-amber-50/70 px-3 py-2 dark:border-amber-900/40 dark:bg-amber-950/20">
                <div className="flex items-center gap-1.5 pb-1.5 text-[10px] font-bold uppercase tracking-[0.14em] text-amber-800 dark:text-amber-300">
                  <CalendarDays className="h-3.5 w-3.5" />
                  Floor SOH as of
                </div>
                <label className="block">
                  <span className="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-slate-500">
                    Date
                  </span>
                  <input
                    type="date"
                    value={asOfDraft}
                    min="2025-04-01"
                    onChange={(e) => setAsOfDraft(e.target.value)}
                    className={dateInputClass}
                  />
                </label>
                <button
                  type="button"
                  disabled={!asOfDraft}
                  onClick={applyAsOfDate}
                  className="rounded-lg bg-slate-900 px-3 py-1.5 text-[11px] font-semibold text-white shadow-sm transition hover:-translate-y-px hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-40 dark:bg-white dark:text-slate-900"
                >
                  Apply
                </button>
                {(appliedAsOf || asOfDraft) && (
                  <button
                    type="button"
                    onClick={clearAsOfDate}
                    className="text-[10px] font-semibold text-slate-600 hover:underline dark:text-slate-300"
                  >
                    Latest
                  </button>
                )}
                {appliedAsOf && (
                  <p className="pb-1.5 text-[10px] font-medium text-amber-800 dark:text-amber-300">
                    Closing stock · {appliedAsOf}
                  </p>
                )}
              </div>
            )}
          </div>
        </PageHeader>

        {scope === 'store' && (
          <InventoryFilterPanel
            apiFilters={{ siteId, sourceId, search }}
            clientFilters={clientFilters}
            stores={stores}
            sources={sources}
            skus={skuOptions}
            eans={eanOptions}
            filterOptions={masterFilterOptions}
            loading={loading}
            masterLoading={masterFiltersLoading}
            itemMasterLoading={itemMasterLoading}
            onApiChange={(key, value) => {
              if (key === 'siteId') setSiteId(value);
              else if (key === 'sourceId') setSourceId(value);
              else if (key === 'search') setSearch(value);
              setPage(1);
            }}
            onClientChange={(key, value) =>
              setClientFilters((p) => {
                const next = { ...p, [key]: value };
                if (key === 'departments') {
                  if (!value.length) {
                    // all departments — keep section picks
                  } else if (itemMasterFilters?.sections_by_department) {
                    const allowed = new Set<string>();
                    value.forEach((d) => {
                      (itemMasterFilters.sections_by_department?.[d] ?? []).forEach((s) =>
                        allowed.add(s)
                      );
                    });
                    next.sections = p.sections.filter((s) => allowed.has(s));
                  } else {
                    next.sections = [];
                  }
                }
                return next;
              })
            }
            onClearAll={clearAll}
          />
        )}

        {error && (
          <div className="flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
            <AlertCircle className="mt-0.5 h-4 w-4" />
            <span>{error}</span>
          </div>
        )}

        {scope === 'lead-time' ? (
          <DispatchLeadTimeTable />
        ) : scope === 'breakeven' ? (
          <SkuBreakevenTable />
        ) : scope === 'warehouse' ? (
          <WarehouseScopePanels />
        ) : scope === 'store' && showSohCompare ? (
          <div className="space-y-3">
            <div className="flex items-center justify-between gap-3 rounded-xl border border-slate-200/80 bg-slate-50/80 px-3 py-2 dark:border-slate-800 dark:bg-slate-900/50">
              <div className="flex items-center gap-2 min-w-0">
                <span className="inline-flex h-7 w-7 items-center justify-center rounded-lg bg-white text-slate-700 shadow-sm ring-1 ring-slate-200/80 dark:bg-slate-800 dark:text-slate-200 dark:ring-slate-700">
                  <Scale className="h-3.5 w-3.5" />
                </span>
                <div className="min-w-0">
                  <p className="text-xs font-semibold text-slate-800 dark:text-slate-100">
                    Calculated vs Actual SOH
                  </p>
                  <p className="truncate text-[10px] text-slate-500">
                    Daily movement closing vs COMBINED_SOH — accuracy &amp; gap %
                  </p>
                </div>
              </div>
              <button
                type="button"
                onClick={() => setShowSohCompare(false)}
                className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-[11px] font-semibold text-slate-500 transition hover:bg-white hover:text-slate-800 dark:hover:bg-slate-800 dark:hover:text-slate-100"
              >
                <X className="h-3.5 w-3.5" />
                Close
              </button>
            </div>
            <SohReconciliationPanel compact />
          </div>
        ) : loading && !stockSummary && scope === 'store' ? (
          <div className="flex h-[400px] flex-col items-center justify-center rounded-2xl border border-dashed border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900/60">
            <div className="h-8 w-8 animate-spin rounded-full border-4 border-slate-200 border-t-blue-600 dark:border-slate-700 dark:border-t-blue-500"></div>
            <span className="mt-4 text-sm font-medium text-slate-500 dark:text-slate-400">Loading charts data...</span>
          </div>
        ) : (
          <StoreScopePanels
            rows={analyticsRows}
            storeSummaries={storeSummaries}
            deptSummaries={deptSummaries}
            summary={stockSummary}
            serverTotal={total}
            exportQuery={stockExportQuery}
            siteId={siteId}
            departments={clientFilters.departments}
            sections={clientFilters.sections}
            search={debouncedSearch}
            asOfDate={appliedAsOf}
          />
        )}
      </DashboardShell>
    </DashboardLayout>
  );
}

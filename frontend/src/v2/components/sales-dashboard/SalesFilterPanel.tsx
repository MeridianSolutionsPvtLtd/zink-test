import React, { useMemo } from 'react';
import {
  Hash,
  Layers,
  MapPin,
  Store,
  Tag,
  Truck,
  Users,
} from 'lucide-react';
import { FilterPanelShell } from '../../design-system';
import { ExecutiveMultiSelect } from '../executive/ExecutiveMultiSelect';
import type { FilterOption } from './types';
import {
  CLIENT_FILTER_OPTIONS,
  EMPTY_CLIENT_FILTERS,
  type ClientFilters,
} from './salesAnalytics';

export { EMPTY_CLIENT_FILTERS };

type FilterOpts = typeof CLIENT_FILTER_OPTIONS & {
  customerNames?: string[];
  siteCodes?: string[];
  siteNames?: string[];
  skus?: string[];
  barcodes?: string[];
  seasons?: string[];
  departments?: string[];
  sections?: string[];
};

type SkuOrSeasonOpt = FilterOption | string;

type Props = {
  clientFilters: ClientFilters;
  stores: FilterOption[];
  skus: SkuOrSeasonOpt[];
  seasons?: SkuOrSeasonOpt[];
  barcodeOptions?: string[];
  filterOptions?: Partial<FilterOpts>;
  /** SKU / barcode / season lists still loading from API */
  loading: boolean;
  /** Customer Master zone/site/city/… options still loading */
  masterLoading?: boolean;
  /** Item Master Department / Section still loading */
  itemMasterLoading?: boolean;
  sticky?: boolean;
  compact?: boolean;
  defaultCollapsed?: boolean;
  onClientChange: (key: keyof ClientFilters, value: string[]) => void;
  onClearAll: () => void;
};

function optId(s: SkuOrSeasonOpt): string {
  if (typeof s === 'string') return s.trim();
  return String(s?.id ?? '').trim();
}

export function SalesFilterPanel({
  clientFilters,
  stores,
  skus,
  seasons = [],
  barcodeOptions = [],
  filterOptions,
  loading,
  masterLoading = false,
  itemMasterLoading = false,
  sticky,
  compact = false,
  defaultCollapsed = false,
  onClientChange,
  onClearAll,
}: Props) {
  const opts = { ...CLIENT_FILTER_OPTIONS, ...filterOptions };
  // Site Code / Site Name: Customer Master SITE MANUAL CODE only — never SOH/sales store ids
  const siteCodes = useMemo(() => opts.siteCodes ?? [], [opts.siteCodes]);
  const siteNames = useMemo(() => opts.siteNames ?? [], [opts.siteNames]);
  // Prefer API lists — server cascades SKU/barcode with active filters
  // (Season=SS-26 → only that season's SKUs/barcodes). Never prefer chartRows sample.
  const skuOptions = useMemo(() => {
    const fromApi = skus.map(optId).filter((id) => id && id !== 'all');
    if (fromApi.length) return fromApi;
    // While loading / empty API, show nothing rather than an unscoped sample list
    if (loading) return [];
    return opts.skus?.length ? opts.skus : [];
  }, [opts.skus, skus, loading]);
  const seasonOptions = useMemo(() => {
    const fromApi = seasons.map(optId).filter((id) => id && id !== 'all');
    if (fromApi.length) return fromApi;
    if (loading) return [];
    return opts.seasons?.length ? opts.seasons : [];
  }, [opts.seasons, seasons, loading]);
  const barcodeList = useMemo(() => {
    if (barcodeOptions?.length) return barcodeOptions.map((b) => String(b).trim()).filter(Boolean);
    if (loading) return [];
    return opts.barcodes?.length ? opts.barcodes : [];
  }, [opts.barcodes, barcodeOptions, loading]);
  const customerNames = useMemo(() => {
    if (opts.customerNames?.length) return opts.customerNames;
    // Never fall back to store labels — those are site names, not customer companies
    return [];
  }, [opts.customerNames]);

  const storeCategories = useMemo(() => {
    if (opts.storeCategories?.length) return opts.storeCategories;
    return masterLoading ? [] : ['LTL', 'EXIT', 'NEW'];
  }, [opts.storeCategories, masterLoading]);

  const activeCount = Object.values(clientFilters).reduce((n, a) => n + a.length, 0);
  const badge = activeCount > 0 ? `${activeCount} active` : 'Sales Overview';
  const optionsLoading = masterLoading || itemMasterLoading || loading;

  const filterDimLabel: Record<keyof ClientFilters, string> = {
    seasons: 'Season',
    zones: 'Zone',
    siteCodes: 'Site',
    siteNames: 'Store',
    storeCategories: 'Category',
    storeStatuses: 'Status',
    salesChannels: 'Channel',
    customerFormats: 'Format',
    customerNames: 'Customer',
    cities: 'City',
    states: 'State',
    clusters: 'Cluster',
    areaManagers: 'AM',
    regionalManagers: 'RM',
    clusterRm: 'Cluster',
    storeGrades: 'Grade',
    departments: 'Dept',
    sections: 'Section',
    skus: 'SKU',
    barcodes: 'Barcode',
  };

  return (
    <FilterPanelShell
      badge={badge}
      sticky={sticky}
      compact={compact}
      defaultCollapsed={defaultCollapsed}
      optionsLoading={optionsLoading}
      onClearAll={onClearAll}
      disabled={activeCount === 0}
      footer={
        activeCount > 0 ? (
          <div className="flex flex-wrap gap-1">
            {(Object.keys(filterDimLabel) as (keyof ClientFilters)[]).flatMap((key) => {
              const vals = clientFilters[key];
              if (!vals?.length) return [];
              const dim = filterDimLabel[key];
              return vals.map((v) => (
                <span
                  key={`${key}-${v}`}
                  title={`${dim}: ${v}`}
                  className="inline-flex max-w-[200px] items-center truncate rounded-md bg-blue-50 px-1.5 py-0.5 text-[10px] font-semibold text-blue-700 dark:bg-blue-500/15 dark:text-blue-300"
                >
                  <span className="mr-0.5 font-medium text-blue-500/80 dark:text-blue-400/70">{dim}:</span>
                  {v}
                </span>
              ));
            })}
          </div>
        ) : undefined
      }
    >
      <ExecutiveMultiSelect dense={compact} label="Season" icon={Tag} options={seasonOptions} value={clientFilters.seasons} onChange={(v) => onClientChange('seasons', v)} placeholder={loading ? 'Loading…' : 'All seasons'} loading={loading} />
      <ExecutiveMultiSelect dense={compact} label="Zone" icon={MapPin} options={opts.zones} value={clientFilters.zones} onChange={(v) => onClientChange('zones', v)} placeholder={masterLoading ? 'Loading…' : 'All zones'} loading={masterLoading} />
      <ExecutiveMultiSelect dense={compact} label="Site Code" icon={Hash} options={siteCodes} value={clientFilters.siteCodes} onChange={(v) => onClientChange('siteCodes', v)} placeholder={masterLoading ? 'Loading…' : 'All site codes'} loading={masterLoading} />
      <ExecutiveMultiSelect dense={compact} label="Site Name" icon={Store} options={siteNames} value={clientFilters.siteNames} onChange={(v) => onClientChange('siteNames', v)} placeholder={masterLoading ? 'Loading…' : 'All site names'} loading={masterLoading} />
      <ExecutiveMultiSelect dense={compact} label="Store Category" icon={Tag} options={storeCategories} value={clientFilters.storeCategories} onChange={(v) => onClientChange('storeCategories', v)} placeholder={masterLoading ? 'Loading…' : 'All categories'} loading={masterLoading} />
      <ExecutiveMultiSelect dense={compact} label="Sales Channel" icon={Truck} options={opts.salesChannels} value={clientFilters.salesChannels} onChange={(v) => onClientChange('salesChannels', v)} />
      <ExecutiveMultiSelect dense={compact} label="Customer Name" icon={Users} options={customerNames} value={clientFilters.customerNames} onChange={(v) => onClientChange('customerNames', v)} placeholder={masterLoading ? 'Loading…' : 'All customers'} loading={masterLoading} />
      <ExecutiveMultiSelect dense={compact} label="City Name" icon={MapPin} options={opts.cities} value={clientFilters.cities} onChange={(v) => onClientChange('cities', v)} placeholder={masterLoading ? 'Loading…' : 'All cities'} loading={masterLoading} />
      <ExecutiveMultiSelect dense={compact} label="State Name" icon={MapPin} options={opts.states} value={clientFilters.states} onChange={(v) => onClientChange('states', v)} placeholder={masterLoading ? 'Loading…' : 'All states'} loading={masterLoading} />
      <ExecutiveMultiSelect dense={compact} label="Cluster / RM" icon={Users} options={opts.clusterRm} value={clientFilters.clusterRm} onChange={(v) => onClientChange('clusterRm', v)} placeholder={masterLoading ? 'Loading…' : 'All clusters / RM'} loading={masterLoading} />
      <ExecutiveMultiSelect dense={compact} label="Store Grade" icon={Tag} options={opts.storeGrades} value={clientFilters.storeGrades} onChange={(v) => onClientChange('storeGrades', v)} placeholder={masterLoading ? 'Loading…' : 'All grades'} loading={masterLoading} />
      <ExecutiveMultiSelect dense={compact} label="Department" icon={Layers} options={opts.departments ?? []} value={clientFilters.departments} onChange={(v) => onClientChange('departments', v)} placeholder={itemMasterLoading ? 'Loading…' : 'All departments'} loading={itemMasterLoading} />
      <ExecutiveMultiSelect dense={compact} label="Section" icon={Layers} options={opts.sections ?? []} value={clientFilters.sections} onChange={(v) => onClientChange('sections', v)} placeholder={itemMasterLoading ? 'Loading…' : 'All sections'} loading={itemMasterLoading} />
      <ExecutiveMultiSelect dense={compact} label="SKU" icon={Hash} options={skuOptions} value={clientFilters.skus} onChange={(v) => onClientChange('skus', v)} placeholder={loading ? 'Loading SKUs…' : 'All SKUs'} loading={loading} />
      <ExecutiveMultiSelect dense={compact} label="Barcode" icon={Hash} options={barcodeList} value={clientFilters.barcodes} onChange={(v) => onClientChange('barcodes', v)} placeholder={loading ? 'Loading barcodes…' : 'All barcodes'} loading={loading} />
    </FilterPanelShell>
  );
}

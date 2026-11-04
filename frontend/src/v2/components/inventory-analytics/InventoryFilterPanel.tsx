import React, { useMemo } from 'react';
import {
  Hash,
  Layers,
  MapPin,
  Ruler,
  Search,
  Store,
  Tag,
  Warehouse,
} from 'lucide-react';
import { FilterPanelShell } from '../../design-system';
import { ExecutiveMultiSelect } from '../executive/ExecutiveMultiSelect';
import { FILTER_OPTIONS, EMPTY_CLIENT_FILTERS } from './inventoryAnalytics';
import type { ClientInvFilters, FilterOption } from './types';

type ApiFilters = {
  siteId: string;
  sourceId: string;
  search: string;
};

type MasterOpts = {
  zones?: string[];
  states?: string[];
  cities?: string[];
  storeGrades?: string[];
  storeCategories?: string[];
  siteCodes?: string[];
  siteNames?: string[];
  salesChannels?: string[];
  departments?: string[];
  sections?: string[];
};

type Props = {
  apiFilters: ApiFilters;
  clientFilters: ClientInvFilters;
  stores: FilterOption[];
  sources: FilterOption[];
  skus: string[];
  eans: string[];
  filterOptions?: MasterOpts;
  loading: boolean;
  /** Customer Master zone/site/city/… options still loading */
  masterLoading?: boolean;
  /** Item Master Department / Section options still loading */
  itemMasterLoading?: boolean;
  onApiChange: (key: keyof ApiFilters, value: string) => void;
  onClientChange: (key: keyof ClientInvFilters, value: string[]) => void;
  onClearAll: () => void;
};

export { EMPTY_CLIENT_FILTERS };

export function InventoryFilterPanel(props: Props) {
  const {
    apiFilters,
    clientFilters,
    stores,
    sources,
    eans,
    filterOptions,
    loading,
    masterLoading = false,
    itemMasterLoading = false,
    onApiChange,
    onClientChange,
    onClearAll,
  } = props;

  const opts = filterOptions ?? {};
  // Site Code / Store Name: Customer Master only (SITE MANUAL CODE) — never SOH store list
  const siteCodes = useMemo(() => opts.siteCodes ?? [], [opts.siteCodes]);
  const siteNames = useMemo(() => opts.siteNames ?? [], [opts.siteNames]);

  const activeCount = Object.values(clientFilters).reduce((n, a) => n + a.length, 0);
  const optionsLoading = masterLoading || itemMasterLoading || loading;

  return (
    <FilterPanelShell
      sticky={false}
      badge={activeCount > 0 ? `${activeCount} active · Customer Master` : 'Customer Master filters'}
      optionsLoading={optionsLoading}
      onClearAll={onClearAll}
      disabled={(loading || masterLoading) && activeCount === 0}
    >
      <label className="block">
        <span className="mb-1.5 flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-wider text-slate-400">
          <Warehouse className="h-3.5 w-3.5" /> Channel / Source
        </span>
        <select
          value={apiFilters.sourceId}
          onChange={(e) => onApiChange('sourceId', e.target.value)}
          className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm font-medium text-slate-800 outline-none ring-blue-500/30 focus:ring-2 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
        >
          {sources.map((s) => (
            <option key={s.id} value={s.id}>
              {s.label}
            </option>
          ))}
        </select>
      </label>
      <label className="block">
        <span className="mb-1.5 flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-wider text-slate-400">
          <Search className="h-3.5 w-3.5" /> Search
        </span>
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
          <input
            value={apiFilters.search}
            onChange={(e) => onApiChange('search', e.target.value)}
            placeholder="Site, SKU, EAN…"
            className="w-full rounded-xl border border-slate-200 bg-white py-2.5 pl-9 pr-3 text-sm text-slate-800 outline-none ring-blue-500/30 focus:ring-2 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
          />
        </div>
      </label>
      <ExecutiveMultiSelect
        label="Zone"
        icon={MapPin}
        options={opts.zones?.length ? opts.zones : []}
        value={clientFilters.zones}
        onChange={(v) => onClientChange('zones', v)}
        placeholder={masterLoading ? 'Loading…' : 'All zones'}
        loading={masterLoading}
      />
      <ExecutiveMultiSelect
        label="State"
        icon={MapPin}
        options={opts.states?.length ? opts.states : []}
        value={clientFilters.states}
        onChange={(v) => onClientChange('states', v)}
        placeholder={masterLoading ? 'Loading…' : 'All states'}
        loading={masterLoading}
      />
      <ExecutiveMultiSelect
        label="City"
        icon={MapPin}
        options={opts.cities?.length ? opts.cities : []}
        value={clientFilters.cities}
        onChange={(v) => onClientChange('cities', v)}
        placeholder={masterLoading ? 'Loading…' : 'All cities'}
        loading={masterLoading}
      />
      <ExecutiveMultiSelect
        label="Site Code"
        icon={Hash}
        options={siteCodes}
        value={clientFilters.siteCodes}
        onChange={(v) => onClientChange('siteCodes', v)}
        placeholder={masterLoading ? 'Loading…' : 'All site codes'}
        loading={masterLoading}
      />
      <ExecutiveMultiSelect
        label="Store Name"
        icon={Store}
        options={siteNames}
        value={clientFilters.siteNames}
        onChange={(v) => onClientChange('siteNames', v)}
        placeholder={masterLoading ? 'Loading…' : 'All store names'}
        loading={masterLoading}
      />
      <ExecutiveMultiSelect
        label="Store Grade"
        icon={Tag}
        options={opts.storeGrades?.length ? opts.storeGrades : []}
        value={clientFilters.storeGrades}
        onChange={(v) => onClientChange('storeGrades', v)}
        placeholder={masterLoading ? 'Loading…' : 'All grades'}
        loading={masterLoading}
      />
      <ExecutiveMultiSelect
        label="Store Category"
        icon={Store}
        options={opts.storeCategories?.length ? opts.storeCategories : []}
        value={clientFilters.storeCategories}
        onChange={(v) => onClientChange('storeCategories', v)}
        placeholder={masterLoading ? 'Loading…' : 'All categories'}
        loading={masterLoading}
      />
      <ExecutiveMultiSelect
        label="Department"
        icon={Layers}
        options={opts.departments?.length ? opts.departments : []}
        value={clientFilters.departments}
        onChange={(v) => onClientChange('departments', v)}
        placeholder={itemMasterLoading ? 'Loading…' : 'All departments'}
        loading={itemMasterLoading}
      />
      <ExecutiveMultiSelect
        label="Section"
        icon={Layers}
        options={opts.sections?.length ? opts.sections : []}
        value={clientFilters.sections}
        onChange={(v) => onClientChange('sections', v)}
        placeholder={itemMasterLoading ? 'Loading…' : 'All sections'}
        loading={itemMasterLoading}
      />
      <ExecutiveMultiSelect
        label="Size"
        icon={Ruler}
        options={FILTER_OPTIONS.sizes}
        value={clientFilters.sizes}
        onChange={(v) => onClientChange('sizes', v)}
      />
      <ExecutiveMultiSelect
        label="EAN"
        icon={Hash}
        options={eans}
        value={clientFilters.eans}
        onChange={(v) => onClientChange('eans', v)}
        placeholder={loading ? 'Loading…' : 'All EANs'}
        loading={loading && eans.length === 0}
      />
    </FilterPanelShell>
  );
}

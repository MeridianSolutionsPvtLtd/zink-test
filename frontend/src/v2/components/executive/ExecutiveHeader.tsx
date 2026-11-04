import React from 'react';
import {
  Calendar,
  Hash,
  Home,
  MapPin,
  Store,
  Tag,
  Truck,
  Users,
} from 'lucide-react';
import { FilterPanelShell, PageHeader } from '../../design-system';
import { ExecutiveMultiSelect } from './ExecutiveMultiSelect';
import { CLIENT_FILTER_OPTIONS, visibleDashboardFyEnds } from '../sales-dashboard/salesAnalytics';

function _indianFyEnd(d = new Date()): number {
  return d.getMonth() >= 3 ? d.getFullYear() + 1 : d.getFullYear();
}

const _fyEnd = _indianFyEnd();
/** FY end years shown in filters: FY26 through the current Indian FY. */
export const DASHBOARD_YEARS = visibleDashboardFyEnds() as readonly number[];
export type DashboardYear = number;
export const DEFAULT_DASHBOARD_YEAR: DashboardYear = _fyEnd;

export type GlobalFilters = {
  seasons: string[];
  zones: string[];
  siteCodes: string[];
  siteNames: string[];
  storeCategories: string[];
  storeStatuses: string[];
  salesChannels: string[];
  customerFormats: string[];
  customerNames: string[];
  cities: string[];
  states: string[];
  clusters: string[];
  areaManagers: string[];
  regionalManagers: string[];
  clusterRm: string[];
  storeGrades: string[];
  skus: string[];
  barcodes: string[];
};

export type FilterOptions = {
  seasons: string[];
  zones: string[];
  siteCodes: string[];
  siteNames: string[];
  storeCategories: string[];
  storeStatuses: string[];
  salesChannels: string[];
  customerFormats: string[];
  customerNames: string[];
  cities: string[];
  states: string[];
  clusters: string[];
  areaManagers: string[];
  regionalManagers: string[];
  clusterRm: string[];
  storeGrades: string[];
  skus: string[];
  barcodes: string[];
};

export const EMPTY_GLOBAL_FILTERS: GlobalFilters = {
  seasons: [],
  zones: [],
  siteCodes: [],
  siteNames: [],
  storeCategories: [],
  storeStatuses: [],
  salesChannels: [],
  customerFormats: [],
  customerNames: [],
  cities: [],
  states: [],
  clusters: [],
  areaManagers: [],
  regionalManagers: [],
  clusterRm: [],
  storeGrades: [],
  skus: [],
  barcodes: [],
};

type HeaderProps = {
  lastRefresh: Date;
  onRefresh: () => void;
  refreshing: boolean;
};

export function ExecutiveHeader({ lastRefresh, onRefresh, refreshing }: HeaderProps) {
  return (
    <PageHeader
      icon={Home}
      title="Dashboard"
      lastRefresh={lastRefresh}
      loading={refreshing}
      onRefresh={onRefresh}
    />
  );
}

type FilterPanelProps = {
  filters: GlobalFilters;
  options: FilterOptions;
  year: DashboardYear;
  onYearChange: (year: DashboardYear) => void;
  onChange: (key: keyof GlobalFilters, value: string[]) => void;
  onClear: () => void;
  activeZone?: string | null;
  sticky?: boolean;
  /** Filter option lists still loading */
  loading?: boolean;
};

export function GlobalFilterPanel({
  filters,
  options,
  year,
  onYearChange,
  onChange,
  onClear,
  activeZone,
  sticky,
  loading = false,
}: FilterPanelProps) {
  const activeCount = Object.values(filters).reduce((n, arr) => n + arr.length, 0);
  const badge =
    activeCount > 0
      ? `${year} · ${activeCount} active${activeZone ? ` · ${activeZone}` : ''}`
      : activeZone
        ? `${year} · Zone: ${activeZone}`
        : `${year}`;
  const ph = loading ? 'Loading…' : 'All';

  return (
    <FilterPanelShell
      badge={badge}
      sticky={sticky}
      optionsLoading={loading}
      onClearAll={activeCount > 0 ? onClear : undefined}
      disabled={loading && activeCount === 0}
    >
      <div className="flex min-w-0 flex-col gap-1.5">
        <span className="inline-flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
          <Calendar className="h-3.5 w-3.5" />
          Financial Year (FY)
        </span>
        <div className="inline-flex rounded-lg border border-slate-200 bg-slate-50 p-0.5 dark:border-slate-700 dark:bg-slate-800/80">
          {DASHBOARD_YEARS.map((y) => {
            const short = String(y).slice(-2);
            const fyLabel = `FY-${short} (${y - 1}-${short})`;
            return (
              <button
                key={y}
                type="button"
                onClick={() => onYearChange(y)}
                className={`rounded-md px-3 py-1.5 text-xs font-semibold transition ${
                  year === y
                    ? 'bg-white text-slate-900 shadow-sm dark:bg-slate-700 dark:text-white'
                    : 'text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200'
                }`}
              >
                {fyLabel}
              </button>
            );
          })}
        </div>
      </div>
      <ExecutiveMultiSelect label="Season" icon={Calendar} options={options.seasons} value={filters.seasons} onChange={(v) => onChange('seasons', v)} placeholder={loading ? 'Loading…' : 'All seasons'} loading={loading} />
      <ExecutiveMultiSelect label="Zone" icon={MapPin} options={options.zones} value={filters.zones} onChange={(v) => onChange('zones', v)} placeholder={ph} loading={loading} />
      <ExecutiveMultiSelect label="Site Code" icon={Hash} options={options.siteCodes} value={filters.siteCodes} onChange={(v) => onChange('siteCodes', v)} placeholder={loading ? 'Loading…' : 'All site codes'} loading={loading} />
      <ExecutiveMultiSelect label="Site Name" icon={Store} options={options.siteNames} value={filters.siteNames} onChange={(v) => onChange('siteNames', v)} placeholder={loading ? 'Loading…' : 'All site names'} loading={loading} />
      <ExecutiveMultiSelect label="Store Category" icon={Tag} options={options.storeCategories} value={filters.storeCategories} onChange={(v) => onChange('storeCategories', v)} placeholder={ph} loading={loading} />
      {/* Store Status removed */}
      <ExecutiveMultiSelect label="Sales Channel" icon={Truck} options={options.salesChannels} value={filters.salesChannels} onChange={(v) => onChange('salesChannels', v)} />
      <ExecutiveMultiSelect label="Customer Name" icon={Users} options={options.customerNames} value={filters.customerNames} onChange={(v) => onChange('customerNames', v)} placeholder={loading ? 'Loading…' : 'All customers'} loading={loading} />
      <ExecutiveMultiSelect label="City Name" icon={MapPin} options={options.cities} value={filters.cities} onChange={(v) => onChange('cities', v)} placeholder={ph} loading={loading} />
      <ExecutiveMultiSelect label="State Name" icon={MapPin} options={options.states} value={filters.states} onChange={(v) => onChange('states', v)} placeholder={ph} loading={loading} />
      <ExecutiveMultiSelect label="Cluster Name / Regional Manager" icon={Users} options={options.clusterRm} value={filters.clusterRm} onChange={(v) => onChange('clusterRm', v)} placeholder={loading ? 'Loading…' : 'All clusters / RM'} loading={loading} />
      <ExecutiveMultiSelect label="Store Grade" icon={Tag} options={options.storeGrades} value={filters.storeGrades} onChange={(v) => onChange('storeGrades', v)} placeholder={ph} loading={loading} />
      <ExecutiveMultiSelect label="SKU" icon={Hash} options={options.skus} value={filters.skus} onChange={(v) => onChange('skus', v)} placeholder={loading ? 'Loading SKUs…' : 'All SKUs'} loading={loading} />
      <ExecutiveMultiSelect label="Barcode" icon={Hash} options={options.barcodes} value={filters.barcodes} onChange={(v) => onChange('barcodes', v)} placeholder={loading ? 'Loading…' : 'All barcodes'} loading={loading} />
    </FilterPanelShell>
  );
}

export { CLIENT_FILTER_OPTIONS };

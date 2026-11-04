import React, { useMemo } from 'react';
import {
  Calendar,
  Hash,
  Layers,
  MapPin,
  Shirt,
  Store,
  Tag,
  Truck,
} from 'lucide-react';
import { FilterPanelShell } from '../../design-system';
import { ExecutiveMultiSelect } from '../executive/ExecutiveMultiSelect';
import {
  CLIENT_FILTER_OPTIONS,
  EMPTY_CLIENT_FORECAST_FILTERS,
  type ClientForecastFilters,
} from './forecastAnalytics';

export { EMPTY_CLIENT_FORECAST_FILTERS };

type Props = {
  clientFilters: ClientForecastFilters;
  skuOptions: string[];
  loading?: boolean;
  onChange: (key: keyof ClientForecastFilters, value: string[]) => void;
  onClearAll: () => void;
};

export function ForecastFilterPanel({
  clientFilters,
  skuOptions,
  loading = false,
  onChange,
  onClearAll,
}: Props) {
  const options = useMemo(
    () => ({ ...CLIENT_FILTER_OPTIONS, sku: skuOptions }),
    [skuOptions]
  );
  const skuLoading = loading && skuOptions.length === 0;

  return (
    <FilterPanelShell
      badge="Forecast & Demand Planning"
      optionsLoading={loading}
      onClearAll={onClearAll}
      disabled={loading}
    >
      <>
        <ExecutiveMultiSelect label="Date Range" icon={Calendar} options={options.dateRange} value={clientFilters.dateRange} onChange={(v) => onChange('dateRange', v)} loading={loading} placeholder={loading ? 'Loading…' : 'All'} />
        <ExecutiveMultiSelect label="Zone" icon={MapPin} options={options.zone} value={clientFilters.zone} onChange={(v) => onChange('zone', v)} loading={loading} placeholder={loading ? 'Loading…' : 'All'} />
        <ExecutiveMultiSelect label="State" icon={MapPin} options={options.state} value={clientFilters.state} onChange={(v) => onChange('state', v)} loading={loading} placeholder={loading ? 'Loading…' : 'All'} />
        <ExecutiveMultiSelect label="Store" icon={Store} options={options.store} value={clientFilters.store} onChange={(v) => onChange('store', v)} loading={loading} placeholder={loading ? 'Loading…' : 'All'} />
        <ExecutiveMultiSelect label="Sales Channel" icon={Truck} options={options.channel} value={clientFilters.channel} onChange={(v) => onChange('channel', v)} loading={loading} placeholder={loading ? 'Loading…' : 'All'} />
        <ExecutiveMultiSelect label="Department" icon={Layers} options={options.department} value={clientFilters.department} onChange={(v) => onChange('department', v)} loading={loading} placeholder={loading ? 'Loading…' : 'All'} />
        <ExecutiveMultiSelect label="Category" icon={Shirt} options={options.category} value={clientFilters.category} onChange={(v) => onChange('category', v)} loading={loading} placeholder={loading ? 'Loading…' : 'All'} />
        <ExecutiveMultiSelect label="Story" icon={Tag} options={options.story} value={clientFilters.story} onChange={(v) => onChange('story', v)} loading={loading} placeholder={loading ? 'Loading…' : 'All'} />
        <ExecutiveMultiSelect label="Season" icon={Calendar} options={options.season} value={clientFilters.season} onChange={(v) => onChange('season', v)} loading={loading} placeholder={loading ? 'Loading…' : 'All'} />
        <ExecutiveMultiSelect label="SKU" icon={Hash} options={options.sku} value={clientFilters.sku} onChange={(v) => onChange('sku', v)} loading={skuLoading} placeholder={skuLoading ? 'Loading SKUs…' : 'All SKUs'} />
        <ExecutiveMultiSelect label="Product Type" icon={Tag} options={options.productType} value={clientFilters.productType} onChange={(v) => onChange('productType', v)} loading={loading} placeholder={loading ? 'Loading…' : 'All'} />
      </>
    </FilterPanelShell>
  );
}

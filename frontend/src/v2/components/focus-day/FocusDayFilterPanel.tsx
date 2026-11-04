import React, { useMemo } from 'react';
import {
  Calendar,
  Hash,
  Layers,
  MapPin,
  Shirt,
  Sparkles,
  Star,
  Store,
  Truck,
} from 'lucide-react';
import { FilterPanelShell } from '../../design-system';
import { ExecutiveMultiSelect } from '../executive/ExecutiveMultiSelect';
import { EMPTY_FOCUS_FILTERS, FILTER_OPTIONS, type FocusFilters } from './focusDayData';

export { EMPTY_FOCUS_FILTERS };

type Props = {
  filters: FocusFilters;
  skuOptions: string[];
  loading?: boolean;
  onChange: (key: keyof FocusFilters, value: string[]) => void;
  onClearAll: () => void;
};

export function FocusDayFilterPanel({ filters, skuOptions, loading = false, onChange, onClearAll }: Props) {
  const options = useMemo(() => ({ ...FILTER_OPTIONS, sku: skuOptions }), [skuOptions]);
  const skuLoading = loading && skuOptions.length === 0;

  return (
    <FilterPanelShell badge="Festival Day" optionsLoading={loading} onClearAll={onClearAll} disabled={loading}>
      <>
        <ExecutiveMultiSelect label="Season" icon={Calendar} options={options.season} value={filters.season} onChange={(v) => onChange('season', v)} loading={loading} placeholder={loading ? 'Loading…' : 'All'} />
        <ExecutiveMultiSelect label="Event / Festival Day" icon={Sparkles} options={options.event} value={filters.event} onChange={(v) => onChange('event', v)} loading={loading} placeholder={loading ? 'Loading…' : 'All'} />
        <ExecutiveMultiSelect label="Date Range" icon={Calendar} options={options.dateRange} value={filters.dateRange} onChange={(v) => onChange('dateRange', v)} loading={loading} placeholder={loading ? 'Loading…' : 'All'} />
        <ExecutiveMultiSelect label="Zone" icon={MapPin} options={options.zone} value={filters.zone} onChange={(v) => onChange('zone', v)} loading={loading} placeholder={loading ? 'Loading…' : 'All'} />
        <ExecutiveMultiSelect label="State" icon={MapPin} options={options.state} value={filters.state} onChange={(v) => onChange('state', v)} loading={loading} placeholder={loading ? 'Loading…' : 'All'} />
        <ExecutiveMultiSelect label="City" icon={MapPin} options={options.city} value={filters.city} onChange={(v) => onChange('city', v)} loading={loading} placeholder={loading ? 'Loading…' : 'All'} />
        <ExecutiveMultiSelect label="Store" icon={Store} options={options.store} value={filters.store} onChange={(v) => onChange('store', v)} loading={loading} placeholder={loading ? 'Loading…' : 'All'} />
        <ExecutiveMultiSelect label="Store Grade" icon={Star} options={options.storeGrade} value={filters.storeGrade} onChange={(v) => onChange('storeGrade', v)} loading={loading} placeholder={loading ? 'Loading…' : 'All'} />
        <ExecutiveMultiSelect label="Sales Channel" icon={Truck} options={options.channel} value={filters.channel} onChange={(v) => onChange('channel', v)} loading={loading} placeholder={loading ? 'Loading…' : 'All'} />
        <ExecutiveMultiSelect label="Department" icon={Layers} options={options.department} value={filters.department} onChange={(v) => onChange('department', v)} loading={loading} placeholder={loading ? 'Loading…' : 'All'} />
        <ExecutiveMultiSelect label="Category" icon={Shirt} options={options.category} value={filters.category} onChange={(v) => onChange('category', v)} loading={loading} placeholder={loading ? 'Loading…' : 'All'} />
        <ExecutiveMultiSelect label="SKU" icon={Hash} options={options.sku} value={filters.sku} onChange={(v) => onChange('sku', v)} loading={skuLoading} placeholder={skuLoading ? 'Loading SKUs…' : 'All SKUs'} />
      </>
    </FilterPanelShell>
  );
}

import React, { useMemo } from 'react';
import {
  Calendar,
  Hash,
  Image as ImageIcon,
  Layers,
  MapPin,
  Package,
  Palette,
  Ruler,
  Shirt,
  Store,
  Tag,
  Truck,
} from 'lucide-react';
import { FilterPanelShell } from '../../design-system';
import { ExecutiveMultiSelect } from '../executive/ExecutiveMultiSelect';
import {
  EMPTY_PRODUCT_FILTERS,
  FILTER_OPTIONS,
  type ProductFilters,
} from './productData';

export { EMPTY_PRODUCT_FILTERS };

type Props = {
  filters: ProductFilters;
  skuOptions: string[];
  eanOptions: string[];
  referenceOptions: string[];
  inwardDateOptions: string[];
  dispatchDateOptions: string[];
  loading?: boolean;
  onChange: (key: keyof ProductFilters, value: string[]) => void;
  onClearAll: () => void;
};

export function ProductFilterPanel({
  filters,
  skuOptions,
  eanOptions,
  referenceOptions,
  inwardDateOptions,
  dispatchDateOptions,
  loading,
  onChange,
  onClearAll,
}: Props) {
  const options = useMemo(
    () => ({
      ...FILTER_OPTIONS,
      sku: skuOptions,
      ean: eanOptions,
      referenceNumber: referenceOptions,
      stockInwardDate: inwardDateOptions,
      stockDispatchDate: dispatchDateOptions,
    }),
    [skuOptions, eanOptions, referenceOptions, inwardDateOptions, dispatchDateOptions]
  );

  const busy = Boolean(loading);

  return (
    <FilterPanelShell
      badge="Product & Inventory"
      optionsLoading={busy}
      onClearAll={onClearAll}
      disabled={busy}
    >
      <>
        {/* Compulsory Product & Inventory filters */}
        <ExecutiveMultiSelect
          label="Season *"
          icon={Calendar}
          options={options.season}
          value={filters.season}
          onChange={(v) => onChange('season', v)}
          loading={busy}
          placeholder={busy ? 'Loading…' : 'All'}
        />
        <ExecutiveMultiSelect
          label="Story Name *"
          icon={Tag}
          options={options.story}
          value={filters.story}
          onChange={(v) => onChange('story', v)}
          loading={busy}
          placeholder={busy ? 'Loading…' : 'All'}
        />
        <ExecutiveMultiSelect
          label="SKU *"
          icon={Hash}
          options={options.sku}
          value={filters.sku}
          onChange={(v) => onChange('sku', v)}
          loading={busy && options.sku.length === 0}
          placeholder={busy && options.sku.length === 0 ? 'Loading SKUs…' : 'All SKUs'}
        />
        <ExecutiveMultiSelect
          label="EAN *"
          icon={Hash}
          options={options.ean}
          value={filters.ean}
          onChange={(v) => onChange('ean', v)}
          loading={busy && options.ean.length === 0}
          placeholder={busy && options.ean.length === 0 ? 'Loading…' : 'All'}
        />
        <ExecutiveMultiSelect
          label="Size *"
          icon={Ruler}
          options={options.size}
          value={filters.size}
          onChange={(v) => onChange('size', v)}
          loading={busy}
          placeholder={busy ? 'Loading…' : 'All'}
        />
        <ExecutiveMultiSelect
          label="Reference Number *"
          icon={Hash}
          options={options.referenceNumber}
          value={filters.referenceNumber}
          onChange={(v) => onChange('referenceNumber', v)}
          loading={busy && options.referenceNumber.length === 0}
          placeholder={busy ? 'Loading…' : 'All'}
        />
        <ExecutiveMultiSelect
          label="Product Section *"
          icon={Layers}
          options={options.section}
          value={filters.section}
          onChange={(v) => onChange('section', v)}
          loading={busy}
          placeholder={busy ? 'Loading…' : 'All'}
        />
        <ExecutiveMultiSelect
          label="Product Department *"
          icon={Layers}
          options={options.department}
          value={filters.department}
          onChange={(v) => onChange('department', v)}
          loading={busy}
          placeholder={busy ? 'Loading…' : 'All'}
        />
        <ExecutiveMultiSelect
          label="Product Type * (Winter / Non-winter)"
          icon={Shirt}
          options={options.productType}
          value={filters.productType}
          onChange={(v) => onChange('productType', v)}
          loading={busy}
          placeholder={busy ? 'Loading…' : 'All'}
        />
        <ExecutiveMultiSelect
          label="Stock Inward Date *"
          icon={Calendar}
          options={options.stockInwardDate}
          value={filters.stockInwardDate}
          onChange={(v) => onChange('stockInwardDate', v)}
          loading={busy && options.stockInwardDate.length === 0}
          placeholder={busy ? 'Loading…' : 'All'}
        />
        <ExecutiveMultiSelect
          label="Stock Dispatch *"
          icon={Truck}
          options={options.stockDispatchDate}
          value={filters.stockDispatchDate}
          onChange={(v) => onChange('stockDispatchDate', v)}
          loading={busy && options.stockDispatchDate.length === 0}
          placeholder={busy ? 'Loading…' : 'All'}
        />
        <div className="flex items-end">
          <div className="w-full rounded-xl border border-dashed border-slate-200 bg-slate-50/80 px-3 py-2.5 text-[11px] text-slate-500 dark:border-slate-700 dark:bg-slate-800/50">
            <span className="mb-1 flex items-center gap-1.5 font-bold uppercase tracking-wider text-slate-400">
              <ImageIcon className="h-3.5 w-3.5" /> Product Image *
            </span>
            Shown in product table (compulsory column)
          </div>
        </div>

        {/* Optional extras */}
        <ExecutiveMultiSelect
          label="Date Range"
          icon={Calendar}
          options={options.dateRange}
          value={filters.dateRange}
          onChange={(v) => onChange('dateRange', v)}
          loading={busy}
          placeholder={busy ? 'Loading…' : 'All'}
        />
        <ExecutiveMultiSelect
          label="Category"
          icon={Package}
          options={options.category}
          value={filters.category}
          onChange={(v) => onChange('category', v)}
          loading={busy}
          placeholder={busy ? 'Loading…' : 'All'}
        />
        <ExecutiveMultiSelect
          label="Color"
          icon={Palette}
          options={options.color}
          value={filters.color}
          onChange={(v) => onChange('color', v)}
          loading={busy}
          placeholder={busy ? 'Loading…' : 'All'}
        />
        <ExecutiveMultiSelect
          label="Brand"
          icon={Tag}
          options={options.brand}
          value={filters.brand}
          onChange={(v) => onChange('brand', v)}
          loading={busy}
          placeholder={busy ? 'Loading…' : 'All'}
        />
        <ExecutiveMultiSelect
          label="Sales Channel"
          icon={Truck}
          options={options.channel}
          value={filters.channel}
          onChange={(v) => onChange('channel', v)}
          loading={busy}
          placeholder={busy ? 'Loading…' : 'All'}
        />
        <ExecutiveMultiSelect
          label="Store"
          icon={Store}
          options={options.store}
          value={filters.store}
          onChange={(v) => onChange('store', v)}
          loading={busy}
          placeholder={busy ? 'Loading…' : 'All'}
        />
        <ExecutiveMultiSelect
          label="Zone"
          icon={MapPin}
          options={options.zone}
          value={filters.zone}
          onChange={(v) => onChange('zone', v)}
          loading={busy}
          placeholder={busy ? 'Loading…' : 'All'}
        />
      </>
    </FilterPanelShell>
  );
}

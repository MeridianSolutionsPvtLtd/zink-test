import React, { useMemo, useState } from 'react';
import { ChevronDown, Download, Search } from 'lucide-react';
import {
  PRODUCT_INVENTORY_FIELDS,
  formatInr,
  type ProductRecord,
} from './productData';

const COMPULSORY_KEYS = new Set<string>(PRODUCT_INVENTORY_FIELDS);

const ALL_COLUMNS = [
  { key: 'image', label: 'Product Image *', align: 'left' as const, compulsory: true },
  { key: 'season', label: 'Season *', align: 'left' as const, compulsory: true },
  { key: 'story', label: 'Story Name *', align: 'left' as const, compulsory: true },
  { key: 'sku', label: 'SKU *', align: 'left' as const, compulsory: true },
  { key: 'ean', label: 'EAN *', align: 'left' as const, compulsory: true },
  { key: 'size', label: 'Size *', align: 'left' as const, compulsory: true },
  { key: 'referenceNumber', label: 'Reference Number *', align: 'left' as const, compulsory: true },
  { key: 'section', label: 'Product Section *', align: 'left' as const, compulsory: true },
  { key: 'department', label: 'Product Department *', align: 'left' as const, compulsory: true },
  { key: 'productType', label: 'Product Type *', align: 'left' as const, compulsory: true },
  { key: 'stockInwardDate', label: 'Stock Inward Date *', align: 'left' as const, compulsory: true },
  { key: 'stockDispatchDate', label: 'Stock Dispatch *', align: 'left' as const, compulsory: true },
  { key: 'stockDispatchQty', label: 'Dispatch Qty', align: 'right' as const, compulsory: false },
  { key: 'name', label: 'Product', align: 'left' as const, compulsory: false },
  { key: 'category', label: 'Category', align: 'left' as const, compulsory: false },
  { key: 'color', label: 'Color', align: 'left' as const, compulsory: false },
  { key: 'sales', label: 'Sales', align: 'right' as const, compulsory: false },
  { key: 'quantity', label: 'Qty', align: 'right' as const, compulsory: false },
  { key: 'margin', label: 'Margin', align: 'right' as const, compulsory: false },
  { key: 'discountPct', label: 'Discount', align: 'right' as const, compulsory: false },
  { key: 'stock', label: 'Stock', align: 'right' as const, compulsory: false },
  { key: 'ros', label: 'ROS', align: 'right' as const, compulsory: false },
  { key: 'sellThrough', label: 'Sell Through', align: 'right' as const, compulsory: false },
  { key: 'weekCover', label: 'Week Cover', align: 'right' as const, compulsory: false },
];

function ProductThumb({ hue, name }: { hue: number; name: string }) {
  return (
    <div
      className="flex h-9 w-9 items-center justify-center rounded-md text-[8px] font-bold text-white shadow-sm"
      style={{
        background: `linear-gradient(135deg, hsl(${hue},65%,45%), hsl(${hue + 30},55%,35%))`,
      }}
      title={name}
    >
      IMG
    </div>
  );
}

function formatDisplayDate(iso: string) {
  if (!iso) return '—';
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
}

type Props = {
  products: ProductRecord[];
};

export function ProductDetailTable({ products }: Props) {
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const [sortKey, setSortKey] = useState<keyof ProductRecord>('sales');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
  const [visibleCols, setVisibleCols] = useState(() => ALL_COLUMNS.map((c) => c.key));
  const [colsOpen, setColsOpen] = useState(false);
  const pageSize = 15;

  const filtered = useMemo(() => {
    const q = search.toLowerCase();
    let rows = products;
    if (q) {
      rows = rows.filter(
        (p) =>
          p.sku.toLowerCase().includes(q) ||
          p.ean.includes(q) ||
          p.referenceNumber.toLowerCase().includes(q) ||
          p.name.toLowerCase().includes(q) ||
          p.department.toLowerCase().includes(q) ||
          p.story.toLowerCase().includes(q)
      );
    }
    return [...rows].sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      if (typeof av === 'number' && typeof bv === 'number') {
        return sortDir === 'asc' ? av - bv : bv - av;
      }
      return sortDir === 'asc'
        ? String(av).localeCompare(String(bv))
        : String(bv).localeCompare(String(av));
    });
  }, [products, search, sortKey, sortDir]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const pageRows = filtered.slice((page - 1) * pageSize, page * pageSize);

  const toggleSort = (key: string) => {
    if (key === 'image') return;
    const k = key as keyof ProductRecord;
    if (sortKey === k) setSortDir(sortDir === 'asc' ? 'desc' : 'asc');
    else {
      setSortKey(k);
      setSortDir('desc');
    }
  };

  const toggleCol = (key: string) => {
    if (COMPULSORY_KEYS.has(key)) return; // cannot hide compulsory Product & Inventory fields
    setVisibleCols((prev) =>
      prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]
    );
  };

  const exportCsv = () => {
    const cols = ALL_COLUMNS.filter((c) => visibleCols.includes(c.key));
    const headers = cols.map((c) => c.label.replace(' *', ''));
    const keys = cols.map((c) => c.key);
    const lines = [headers.join(',')];
    filtered.forEach((p) => {
      lines.push(
        keys
          .map((k) => {
            if (k === 'image') return 'IMG';
            return String(p[k as keyof ProductRecord] ?? '');
          })
          .join(',')
      );
    });
    const blob = new Blob([lines.join('\n')], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'product-performance.csv';
    a.click();
    URL.revokeObjectURL(url);
  };

  const renderCell = (p: ProductRecord, key: string) => {
    switch (key) {
      case 'image':
        return <ProductThumb hue={p.hue} name={p.name} />;
      case 'stockInwardDate':
      case 'stockDispatchDate':
        return formatDisplayDate(p[key]);
      case 'stockDispatchQty':
        return new Intl.NumberFormat('en-IN').format(p.stockDispatchQty);
      case 'sales':
      case 'margin':
        return formatInr(p[key]);
      case 'discountPct':
        return `${p.discountPct}%`;
      case 'sellThrough':
        return `${p.sellThrough}%`;
      case 'weekCover':
        return `${p.weekCover}w`;
      case 'ros':
        return p.ros.toFixed(1);
      case 'productType':
        return (
          <span
            className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${
              p.productType === 'Winter'
                ? 'bg-sky-100 text-sky-800'
                : 'bg-amber-100 text-amber-800'
            }`}
          >
            {p.productType}
          </span>
        );
      default:
        return String(p[key as keyof ProductRecord] ?? '');
    }
  };

  return (
    <div className="rounded-2xl border border-slate-200/80 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900/60">
      <div className="relative z-30 flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-4 py-3 dark:border-slate-800">
        <div>
          <h3 className="text-sm font-bold text-slate-900 dark:text-white">Product Detail Table</h3>
          <p className="text-[11px] text-slate-500">
            {filtered.length} products · Product & Inventory fields marked * are compulsory
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
            <input
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setPage(1);
              }}
              placeholder="Search SKU, EAN, REF..."
              className="rounded-lg border border-slate-200 py-1.5 pl-8 pr-3 text-xs dark:border-slate-700 dark:bg-slate-900"
            />
          </div>
          <div className="relative">
            <button
              type="button"
              onClick={() => setColsOpen(!colsOpen)}
              className="inline-flex items-center gap-1 rounded-lg border px-3 py-1.5 text-xs font-semibold dark:border-slate-700"
            >
              Columns <ChevronDown className="h-3.5 w-3.5" />
            </button>
            {colsOpen && (
              <div className="absolute right-0 top-full z-50 mt-1 max-h-56 w-56 overflow-y-auto rounded-xl border bg-white p-2 shadow-lg dark:border-slate-700 dark:bg-slate-900">
                <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-slate-100 pb-1.5 px-1 text-[11px] font-semibold text-blue-600 dark:border-slate-800 dark:text-blue-400">
                  <button
                    type="button"
                    onClick={() => setVisibleCols(ALL_COLUMNS.map((c) => c.key))}
                    className="hover:underline"
                  >
                    Select all
                  </button>
                  <button
                    type="button"
                    onClick={() => setVisibleCols(Array.from(COMPULSORY_KEYS))}
                    className="hover:underline"
                  >
                    Unselect all
                  </button>
                </div>
                {ALL_COLUMNS.map((c) => (
                  <label
                    key={c.key}
                    className={`flex items-center gap-2 px-2 py-1 text-xs hover:bg-slate-50 dark:hover:bg-slate-800 ${
                      c.compulsory ? 'cursor-default opacity-80' : 'cursor-pointer'
                    }`}
                  >
                    <input
                      type="checkbox"
                      checked={visibleCols.includes(c.key)}
                      disabled={c.compulsory}
                      onChange={() => toggleCol(c.key)}
                    />
                    {c.label}
                  </label>
                ))}
              </div>
            )}
          </div>
          <button
            type="button"
            onClick={exportCsv}
            className="inline-flex items-center gap-1 rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-blue-700"
          >
            <Download className="h-3.5 w-3.5" /> Export
          </button>
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-full text-xs">
          <thead className="sticky top-0 z-10 bg-slate-50 dark:bg-slate-900">
            <tr className="border-b border-slate-200 text-left text-[10px] uppercase tracking-wider text-slate-500 dark:border-slate-800">
              {ALL_COLUMNS.filter((c) => visibleCols.includes(c.key)).map((col) => (
                <th
                  key={col.key}
                  className={`whitespace-nowrap px-3 py-2.5 font-semibold hover:text-blue-600 ${
                    col.key === 'image' ? '' : 'cursor-pointer'
                  } ${col.align === 'right' ? 'text-right' : ''}`}
                  onClick={() => toggleSort(col.key)}
                >
                  {col.label}
                  {sortKey === col.key && (sortDir === 'asc' ? ' ↑' : ' ↓')}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {pageRows.map((p) => (
              <tr
                key={p.id}
                className="border-b border-slate-50 transition hover:bg-blue-50/30 dark:border-slate-800 dark:hover:bg-blue-950/20"
              >
                {ALL_COLUMNS.filter((c) => visibleCols.includes(c.key)).map((col) => (
                  <td
                    key={col.key}
                    className={`whitespace-nowrap px-3 py-2 ${col.align === 'right' ? 'text-right' : ''}`}
                  >
                    {renderCell(p, col.key)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex items-center justify-between border-t border-slate-100 px-4 py-3 text-xs dark:border-slate-800">
        <span className="text-slate-500">
          Page {page} of {totalPages} · {filtered.length} rows
        </span>
        <div className="flex gap-1">
          <button
            type="button"
            disabled={page <= 1}
            onClick={() => setPage(page - 1)}
            className="rounded-lg border px-3 py-1 disabled:opacity-40 dark:border-slate-700"
          >
            Prev
          </button>
          <button
            type="button"
            disabled={page >= totalPages}
            onClick={() => setPage(page + 1)}
            className="rounded-lg border px-3 py-1 disabled:opacity-40 dark:border-slate-700"
          >
            Next
          </button>
        </div>
      </div>
    </div>
  );
}

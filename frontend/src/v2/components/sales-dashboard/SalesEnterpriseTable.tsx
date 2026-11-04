import React, { useMemo, useState } from 'react';
import { ChevronDown, Download, Image as ImageIcon, Loader2, RotateCcw, Search } from 'lucide-react';
import { API_URL } from '../../../config';
import type { SalesInsights, SalesRow, TargetSummary } from './types';
import {
  deriveCity,
  deriveCustomerName,
  deriveSalesChannel,
  deriveState,
  deriveStoreGrade,
  deriveZone,
  formatInr,
} from './salesAnalytics';
import { SkuImageThumbnail } from '../executive/ExecutiveSections';
import { SkuHoverCard } from '../common/SkuHoverCard';

function formatDate(iso: string) {
  const d = new Date(`${iso}T00:00:00`);
  return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
}

function skuHue(sku: string): number {
  let h = 0;
  for (let i = 0; i < sku.length; i++) h = (h + sku.charCodeAt(i) * (i + 1)) % 360;
  return h;
}

const API_BASE_URL = API_URL;

function resolveSkuImageUrl(sku: string): string {
  const encoded = encodeURIComponent(String(sku || '').trim());
  const path = `/api/product-images/${encoded}`;
  const base = (API_BASE_URL || '').replace(/\/api$/, '');
  return `${base}${path}`;
}

function createFallbackImageCanvas(sku: string, hue: number): string {
  const canvas = document.createElement('canvas');
  canvas.width = 64;
  canvas.height = 64;
  const ctx = canvas.getContext('2d');
  if (!ctx) return '';
  const grad = ctx.createLinearGradient(0, 0, 64, 64);
  grad.addColorStop(0, `hsl(${hue}, 65%, 55%)`);
  grad.addColorStop(1, `hsl(${(hue + 35) % 360}, 70%, 40%)`);
  ctx.fillStyle = grad;
  ctx.beginPath();
  if (typeof ctx.roundRect === 'function') {
    ctx.roundRect(0, 0, 64, 64, 10);
  } else {
    ctx.rect(0, 0, 64, 64);
  }
  ctx.fill();
  ctx.fillStyle = '#FFFFFF';
  ctx.font = 'bold 18px sans-serif';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(sku.substring(0, 3).toUpperCase(), 32, 33);
  return canvas.toDataURL('image/png').split(',')[1];
}

async function loadProductImageBase64(
  sku: string,
  hue: number
): Promise<{ base64: string; extension: 'jpeg' | 'png' }> {
  const url = resolveSkuImageUrl(sku);
  return new Promise((resolve) => {
    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.onload = () => {
      try {
        const canvas = document.createElement('canvas');
        canvas.width = 80;
        canvas.height = 80;
        const ctx = canvas.getContext('2d');
        if (!ctx) {
          return resolve({ base64: createFallbackImageCanvas(sku, hue), extension: 'png' });
        }
        ctx.drawImage(img, 0, 0, 80, 80);
        const dataUrl = canvas.toDataURL('image/jpeg', 0.85);
        resolve({ base64: dataUrl.split(',')[1], extension: 'jpeg' });
      } catch {
        resolve({ base64: createFallbackImageCanvas(sku, hue), extension: 'png' });
      }
    };
    img.onerror = () => {
      resolve({ base64: createFallbackImageCanvas(sku, hue), extension: 'png' });
    };
    img.src = url;
  });
}

/** Store / location columns requested for Sales Dashboard (shown first by default). */
const STORE_COLUMNS = [
  { key: 'zone', label: 'Zone', align: 'left' as const },
  { key: 'site_id', label: 'Site ID', align: 'left' as const },
  { key: 'site_name', label: 'Site Name', align: 'left' as const },
  { key: 'sales_channel', label: 'Sales Channel', align: 'left' as const },
  { key: 'customer_name', label: 'Customer Name', align: 'left' as const },
  { key: 'city_name', label: 'City Name', align: 'left' as const },
  { key: 'state_name', label: 'State Name', align: 'left' as const },
  { key: 'store_grade', label: 'Store Grade', align: 'left' as const },
];

const SALES_COLUMNS = [
  { key: 'date', label: 'Date', align: 'left' as const },
  { key: 'image', label: 'Image', align: 'left' as const },
  { key: 'sku', label: 'SKU', align: 'left' as const },
  { key: 'ean', label: 'EAN', align: 'left' as const },
  { key: 'product', label: 'Product', align: 'left' as const },
  { key: 'size', label: 'Size', align: 'left' as const },
  { key: 'season', label: 'Season', align: 'left' as const },
  // —— Requested sales / commercial columns ——
  { key: 'gross_sale', label: 'Gross Sale', align: 'right' as const },
  { key: 'discount_amount', label: 'Discount', align: 'right' as const },
  { key: 'discount', label: 'Discount %', align: 'right' as const },
  { key: 'gst_value', label: 'GST Value', align: 'right' as const },
  { key: 'net_sale', label: 'Net Sale', align: 'right' as const },
  { key: 'mrp', label: 'MRP', align: 'right' as const },
  { key: 'asp', label: 'ASP', align: 'right' as const },
  { key: 'store_sqft', label: 'Store Sqft Area', align: 'right' as const },
  { key: 'mdq', label: 'MDQ', align: 'right' as const },
  { key: 'current_stock', label: 'Current Stock', align: 'right' as const },
  { key: 'fill_rate', label: 'Stock Fill Rate %', align: 'right' as const },
  { key: 'qty', label: 'Qty', align: 'right' as const },
  { key: 'sales', label: 'Sales (Net)', align: 'right' as const },
];

type ColDef = {
  key: string;
  label: string;
  align: 'left' | 'right';
  pending?: boolean;
};

const ALL_COLUMNS: ColDef[] = [...STORE_COLUMNS, ...(SALES_COLUMNS as unknown as ColDef[])];

const DEFAULT_VISIBLE = [
  ...STORE_COLUMNS.map((c) => c.key),
  'date',
  'image',
  'sku',
  'ean',
  'size',
  'gross_sale',
  'discount_amount',
  'gst_value',
  'net_sale',
  'mrp',
  'asp',
  'store_sqft',
  'mdq',
  'current_stock',
  'fill_rate',
  'qty',
  'season',
];

function numOrNull(v: unknown): number | null {
  if (v == null || v === '') return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function blankableInr(v: number | null | undefined) {
  if (v == null) return '—';
  return formatInr(v);
}

type Props = {
  rows: SalesRow[];
  allRows?: SalesRow[];
  insights?: SalesInsights | null;
  targetSummary?: TargetSummary | null;
  total: number;
  page: number;
  totalPages: number;
  pageSize: number;
  loading: boolean;
  onPageChange: (page: number) => void;
  tableSearch: string;
  onTableSearchChange: (v: string) => void;
  sortKey: string;
  sortDir: 'asc' | 'desc';
  onSortChange: (key: string, dir: 'asc' | 'desc') => void;
  onClearAllFilters?: () => void;
  filterParams?: URLSearchParams;
};

export function SalesEnterpriseTable({
  rows,
  allRows,
  insights,
  targetSummary,
  total,
  page,
  totalPages,
  pageSize,
  loading,
  onPageChange,
  tableSearch,
  onTableSearchChange,
  sortKey,
  sortDir,
  onSortChange,
  onClearAllFilters,
  filterParams,
}: Props) {
  const [viewMode, setViewMode] = useState<'detailed'>('detailed');
  const [visibleCols, setVisibleCols] = useState<string[]>(DEFAULT_VISIBLE);
  const [colsOpen, setColsOpen] = useState(false);
  const pivotExportRef = React.useRef<(() => Promise<void>) | null>(null);

  const enriched = useMemo(
    () =>
      rows.map((row) => {
        const gross = Math.max(0, row.mrp * row.qty);
        const discAmt = row.discount_amount ?? Math.max(0, gross - row.net_sale_amount);
        const net = row.net_sale_amount;
        const qty = row.qty || 0;
        const asp = qty > 0 ? net / qty : null;
        const storeSqft = numOrNull(row.store_sqft);
        const mdq = numOrNull(row.mdq);
        const currentStock = numOrNull(row.current_stock ?? row.current_soh ?? row.soh) ?? 0;
        const fillRate =
          row.fill_rate != null
            ? Number(row.fill_rate)
            : mdq && mdq > 0
            ? Math.round((currentStock / mdq) * 1000) / 10
            : null;
        return {
          ...row,
          zone: deriveZone(row),
          site_id: row.site_id,
          site_name: row.site_name,
          sales_channel: deriveSalesChannel(row),
          customer_name: deriveCustomerName(row),
          city_name: deriveCity(row),
          state_name: deriveState(row),
          store_grade: deriveStoreGrade(row),
          ean: row.barcode,
          product: row.sku,
          gross_sale: gross,
          discount_amount: discAmt,
          gst_value: numOrNull(row.tax_amount),
          net_sale: net,
          asp,
          store_sqft: storeSqft,
          mdq,
          current_stock: currentStock,
          fill_rate: fillRate,
          sales: net,
          qty,
        };
      }),
    [rows]
  );

  const toggleSort = (key: string) => {
    if (sortKey === key) onSortChange(key, sortDir === 'asc' ? 'desc' : 'asc');
    else onSortChange(key, 'desc');
  };

  const [isExporting, setIsExporting] = useState(false);
  const [exportType, setExportType] = useState<'with-images' | 'without-images' | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);
  const [exportMenuOpen, setExportMenuOpen] = useState(false);

  const safeDownloadBlob = (blob: Blob, filename: string) => {
    const cleanFilename = (filename || `sales-overview-${Date.now()}.xlsx`)
      .replace(/[\/\\:*?"<>|\r\n\t]/g, '_')
      .replace(/_+/g, '_')
      .trim();

    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.style.display = 'none';
    a.href = url;
    a.download = cleanFilename;
    a.setAttribute('download', cleanFilename);
    document.body.appendChild(a);
    a.click();
    setTimeout(() => {
      try {
        if (document.body.contains(a)) {
          document.body.removeChild(a);
        }
        URL.revokeObjectURL(url);
      } catch {
        // ignore
      }
    }, 5000);
  };

  const handleExportExcel = async (withImages: boolean = true) => {
    try {
      setIsExporting(true);
      setExportType(withImages ? 'with-images' : 'without-images');
      setExportError(null);
      setExportMenuOpen(false);

      // Build parameters from filterParams, table search, and visible columns
      const params = new URLSearchParams(filterParams ? filterParams.toString() : '');
      if (tableSearch.trim()) {
        params.set('search', tableSearch.trim());
      }
      params.set('with_images', withImages ? 'true' : 'false');
      if (visibleCols.length > 0) {
        params.set('columns', visibleCols.join(','));
      }

      const exportEndpoint = `${API_BASE_URL}/sales-dashboard/export-excel?${params.toString()}`;

      // Download full filtered file directly
      const res = await fetch(exportEndpoint);
      if (!res.ok) {
        throw new Error(`Export response status: ${res.status}`);
      }

      const blob = await res.blob();
      const signature = new Uint8Array(await blob.slice(0, 4).arrayBuffer());
      if (res.headers.get('content-type')?.includes('text/html') || signature[0] !== 0x50 || signature[1] !== 0x4b || signature[2] !== 0x03 || signature[3] !== 0x04) {
        throw new Error('Server did not return a valid Excel workbook. Please retry the export.');
      }
      let filename = `sales-overview-${withImages ? 'with-images' : 'fast'}-${Date.now()}.xlsx`;
      const disposition = res.headers.get('content-disposition');
      if (disposition && disposition.includes('filename=')) {
        const match = disposition.match(/filename="?([^"]+)"?/);
        if (match && match[1]) filename = match[1];
      }

      safeDownloadBlob(blob, filename);
    } catch (err) {
      setExportError(err instanceof Error ? err.message : 'Excel export failed. Please retry.');
    } finally {
      setIsExporting(false);
      setExportType(null);
    }
  };

  const handleSingleClickExport = async () => {
    try {
      setIsExporting(true);
      const shouldIncludeImages = visibleCols.includes('image');
      await handleExportExcel(shouldIncludeImages);
    } catch (e) {
      console.error('Export failed:', e);
    } finally {
      setIsExporting(false);
    }
  };

  const exportCsv = () => {
    const cols = ALL_COLUMNS.filter((c) => visibleCols.includes(c.key));
    const header = cols.map((c) => c.label).join(',');
    const body = enriched
      .map((row) =>
        cols
          .map((c) => {
            const val =
              c.key === 'image'
                ? resolveSkuImageUrl(row.sku)
                : row[c.key as keyof typeof row];
            return `"${String(val ?? '').replace(/"/g, '""')}"`;
          })
          .join(',')
      )
      .join('\n');
    const blob = new Blob([`${header}\n${body}`], { type: 'text/csv' });
    safeDownloadBlob(blob, `sales-lines-${Date.now()}.csv`);
  };

  const columns = ALL_COLUMNS.filter((c) => visibleCols.includes(c.key));

  const renderCell = (row: (typeof enriched)[0], key: string) => {
    switch (key) {
      case 'image':
        return (
          <SkuImageThumbnail
            sku={row.sku}
            imageUrl={`/api/product-images/${encodeURIComponent(row.sku)}`}
            hue={skuHue(row.sku)}
            className="h-11 w-11"
          />
        );
      case 'date':
        return formatDate(row.date);
      case 'sku':
      case 'product':
        return <SkuHoverCard sku={row.sku}>{row.sku}</SkuHoverCard>;
      case 'gross_sale':
      case 'discount_amount':
      case 'net_sale':
      case 'mrp':
      case 'sales':
        return formatInr(Number(row[key as keyof typeof row] ?? 0));
      case 'gst_value':
        return blankableInr(row.gst_value);
      case 'asp':
        return row.asp == null ? '—' : formatInr(row.asp);
      case 'store_sqft':
        return row.store_sqft == null ? '—' : `${row.store_sqft.toLocaleString('en-IN')} sqft`;
      case 'mdq':
        return row.mdq == null ? '—' : Number(row.mdq).toLocaleString('en-IN');
      case 'current_stock':
        return Number(row.current_stock ?? 0).toLocaleString('en-IN');
      case 'fill_rate':
        if (row.fill_rate == null) return '—';
        return (
          <span
            className={
              row.fill_rate >= 80
                ? 'font-semibold text-emerald-600 dark:text-emerald-400'
                : row.fill_rate >= 50
                ? 'font-semibold text-amber-600 dark:text-amber-400'
                : 'font-semibold text-rose-600 dark:text-rose-400'
            }
          >
            {row.fill_rate}%
          </span>
        );
      case 'discount':
        return `${row.discount}%`;
      case 'sales_channel':
        return (
          <span className="rounded-md bg-slate-100 px-2 py-0.5 text-[10px] font-semibold text-slate-700 dark:bg-slate-800 dark:text-slate-200">
            {row.sales_channel}
          </span>
        );
      case 'store_grade':
        return (
          <span className="rounded-md bg-violet-50 px-2 py-0.5 text-[10px] font-bold text-violet-700 dark:bg-violet-500/15 dark:text-violet-300">
            {row.store_grade}
          </span>
        );
      case 'season':
        return (
          <span className="rounded-full bg-blue-50 px-2 py-0.5 text-[10px] font-bold text-blue-700 dark:bg-blue-500/15 dark:text-blue-300">
            {row.season}
          </span>
        );
      case 'size':
        return (
          <span className="rounded-md bg-slate-100 px-2 py-0.5 text-xs font-semibold dark:bg-slate-800">{row.size}</span>
        );
      default:
        return String(row[key as keyof typeof row] ?? '');
    }
  };

  return (
    <div className="rounded-2xl border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900/60">
      <div className="relative z-30 flex flex-col gap-3 border-b border-slate-200 px-5 py-4 dark:border-slate-800 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 className="text-base font-semibold text-slate-900 dark:text-white">Sales Analysis Table</h2>
          {exportError && <p role="alert" className="text-sm text-rose-600">{exportError}</p>}
          <p className="text-xs text-slate-500">
            {loading
              ? 'Loading…'
              : `Showing ${enriched.length} of ${total.toLocaleString('en-IN')} rows · * = not available in source data`}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
            <input
              value={tableSearch}
              onChange={(e) => onTableSearchChange(e.target.value)}
              placeholder="Search site, zone, city…"
              className="rounded-xl border border-slate-200 py-2 pl-8 pr-3 text-xs dark:border-slate-700 dark:bg-slate-900"
            />
          </div>
          <div className="relative">
            <button
              type="button"
              onClick={() => setColsOpen(!colsOpen)}
              className="inline-flex items-center gap-1 rounded-xl border border-slate-200 px-3 py-2 text-xs font-semibold dark:border-slate-700"
            >
              Columns <ChevronDown className="h-3.5 w-3.5" />
            </button>
            {colsOpen && (
              <div className="absolute right-0 top-full z-50 mt-1 max-h-72 w-56 overflow-y-auto rounded-xl border border-slate-200 bg-white p-2 shadow-xl dark:border-slate-700 dark:bg-slate-900">
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
                    onClick={() => setVisibleCols([])}
                    className="hover:underline"
                  >
                    Unselect all
                  </button>
                  <button
                    type="button"
                    onClick={() => setVisibleCols(DEFAULT_VISIBLE)}
                    className="ml-auto hover:underline"
                  >
                    Reset default
                  </button>
                </div>
                {ALL_COLUMNS.map((col) => (
                  <label
                    key={col.key}
                    className="flex cursor-pointer items-center gap-2 rounded px-2 py-1 text-xs hover:bg-slate-50 dark:hover:bg-slate-800"
                  >
                    <input
                      type="checkbox"
                      checked={visibleCols.includes(col.key)}
                      onChange={(e) => {
                        setVisibleCols((prev) => {
                          if (e.target.checked) return [...prev, col.key];
                          return prev.filter((k) => k !== col.key);
                        });
                      }}
                    />
                    {col.label}
                    {col.pending ? ' *' : ''}
                  </label>
                ))}
              </div>
            )}
          </div>

          {/* Clear Filters Button */}
          <button
            type="button"
            onClick={() => {
              onTableSearchChange('');
              setVisibleCols(DEFAULT_VISIBLE);
              onSortChange('sales', 'desc');
              onClearAllFilters?.();
            }}
            className="inline-flex items-center gap-1.5 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-semibold text-rose-700 shadow-sm hover:bg-rose-100 hover:text-rose-800 dark:border-rose-900/50 dark:bg-rose-950/40 dark:text-rose-300 dark:hover:bg-rose-900/60 transition"
            title="Clear all dashboard filters, table search, and reset columns"
          >
            <RotateCcw className="h-3.5 w-3.5 text-rose-600 dark:text-rose-400" />
            Clear Filters
          </button>

          {/* Export Excel (Data Only) */}
          <button
            type="button"
            disabled={isExporting}
            onClick={() => handleExportExcel(false)}
            className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-700 shadow-sm hover:bg-slate-50 hover:text-slate-900 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700 disabled:opacity-60 transition"
            title="Download Excel spreadsheet (Fast data export without images)"
          >
            {isExporting && exportType === 'without-images' ? (
              <>
                <Loader2 className="h-3.5 w-3.5 animate-spin text-slate-500" />
                <span>Exporting...</span>
              </>
            ) : (
              <>
                <Download className="h-3.5 w-3.5 text-slate-500" />
                <span>Download Excel</span>
              </>
            )}
          </button>

          {/* Export Excel with Images */}
          <button
            type="button"
            disabled={isExporting}
            onClick={() => handleExportExcel(true)}
            className="inline-flex items-center gap-1.5 rounded-xl bg-emerald-600 px-3.5 py-2 text-xs font-semibold text-white shadow-sm hover:bg-emerald-700 disabled:opacity-60 transition"
            title="Download Excel spreadsheet with embedded SKU product images"
          >
            {isExporting && exportType === 'with-images' ? (
              <>
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                <span>Exporting with Images...</span>
              </>
            ) : (
              <>
                <ImageIcon className="h-3.5 w-3.5" />
                <span>Download Excel with Images</span>
              </>
            )}
          </button>
        </div>
      </div>

      <div className="max-h-[580px] overflow-auto rounded-b-2xl border-t border-slate-100 dark:border-slate-800">
        {columns.length === 0 ? (
          <div className="px-4 py-16 text-center text-sm text-slate-500">
            No columns selected. Open <span className="font-semibold text-slate-700 dark:text-slate-200">Columns</span> above and select fields to show.
          </div>
        ) : (
        <table className="w-full min-w-full text-left text-sm">
          <thead className="sticky top-0 z-10 bg-slate-50/95 text-[11px] font-bold uppercase tracking-wider text-slate-600 backdrop-blur-sm shadow-sm dark:bg-slate-900/95 dark:text-slate-300">
            <tr>
              {columns.map((col) => (
                <th key={col.key} className={`whitespace-nowrap px-4 py-3 ${col.align === 'right' ? 'text-right' : ''}`}>
                  <button type="button" onClick={() => toggleSort(col.key)} className="inline-flex items-center gap-1 hover:text-blue-600">
                    {col.label}
                    {col.pending ? ' *' : ''}
                    {sortKey === col.key ? (sortDir === 'asc' ? ' ↑' : ' ↓') : ''}
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
            {loading && rows.length === 0 ? (
              <tr>
                <td colSpan={Math.max(1, columns.length)} className="px-4 py-16 text-center">
                  <Loader2 className="mx-auto h-8 w-8 animate-spin text-blue-500" />
                </td>
              </tr>
            ) : enriched.length === 0 ? (
              <tr>
                <td colSpan={Math.max(1, columns.length)} className="px-4 py-16 text-center text-sm text-slate-500">
                  No sales for this filter. Try another store, SKU, or season.
                </td>
              </tr>
            ) : (
              enriched.map((row) => (
                <tr key={row.id} className="transition hover:bg-blue-50/40 dark:hover:bg-blue-500/5">
                  {columns.map((col) => (
                    <td
                      key={col.key}
                      className={`whitespace-nowrap px-4 py-3.5 ${
                        col.align === 'right' ? 'text-right font-semibold' : 'text-slate-700 dark:text-slate-200'
                      } ${col.key === 'net_sale' || col.key === 'sales' ? 'text-blue-700 dark:text-blue-400' : ''}`}
                    >
                      {renderCell(row, col.key)}
                    </td>
                  ))}
                </tr>
              ))
            )}
          </tbody>
        </table>
        )}
      </div>

      <div className="flex items-center justify-between border-t border-slate-200 px-5 py-3 dark:border-slate-800">
        <p className="text-xs text-slate-500">
          Page {page} of {totalPages} · {pageSize} per page · {total.toLocaleString('en-IN')} total
        </p>
        <div className="flex gap-2">
          <button
            type="button"
            disabled={page <= 1 || loading}
            onClick={() => onPageChange(Math.max(1, page - 1))}
            className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-semibold disabled:opacity-40 dark:border-slate-700"
          >
            Previous
          </button>
          <button
            type="button"
            disabled={page >= totalPages || loading}
            onClick={() => onPageChange(Math.min(totalPages, page + 1))}
            className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-semibold disabled:opacity-40 dark:border-slate-700"
          >
            Next
          </button>
        </div>
      </div>
    </div>
  );
}

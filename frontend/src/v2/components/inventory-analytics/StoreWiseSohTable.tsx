import React, { useMemo, useState } from 'react';
import {
  ArrowUpDown,
  Barcode,
  Bot,
  Building2,
  Calendar,
  ChevronLeft,
  ChevronRight,
  Download,
  Filter,
  Layers,
  Package,
  Search,
  Store,
  Tag,
  TrendingDown,
  TrendingUp,
  X,
} from 'lucide-react';
import { API_URL } from '../../../config';
import { formatNumber, pctVs } from './inventoryAnalytics';
import { SkuHoverCard } from '../common/SkuHoverCard';
import type { StockRow } from './types';

export type StoreSummary = {
  siteId: string;
  siteName: string;
  soh: number;
  mdq?: number;
  mdqFillPct?: number;
  pctOfTotal: number;
  deptCount: number;
  secCount: number;
  skuCount: number;
  barcodeCount: number;
};

export type BarcodeSkuRow = {
  siteId: string;
  siteName: string;
  barcode: string;
  sku: string;
  size: string;
  department: string;
  section: string;
  currentSoh: number;
  sohLy: number | null;
  vsLy: number | null;
  costRate: number;
  storeMdq?: number;
  storeMdqFillPct?: number;
  storePctOfTotal?: number;
};

export type DeptSecSummary = {
  department: string;
  section: string;
  storeCount: number;
  skuCount: number;
  barcodeCount: number;
  currentSoh: number;
  sohLy: number | null;
  vsLy: number | null;
};

type Props = {
  rows: StockRow[];
  storeSummaries?: StoreSummary[];
  deptSummaries?: DeptSecSummary[];
  summary?: { total_soh: number; store_count: number; sku_count: number; barcode_count?: number } | null;
  /** Total filtered rows on server (may be >> loaded page). */
  serverTotal?: number;
  /** Query string for full CSV export (filters, no page). */
  exportQuery?: string;
  /** Closing SOH as of this calendar day (YYYY-MM-DD); empty = latest. */
  asOfDate?: string | null;
  selectedStore: string;
  onSelectStore: (storeId: string) => void;
};

function Delta({ value }: { value: number | null | undefined }) {
  if (value == null || !Number.isFinite(value)) {
    return <span className="text-slate-400">—</span>;
  }
  return (
    <span
      className={`font-semibold ${
        value > 0 ? 'text-emerald-600 dark:text-emerald-400' : value < 0 ? 'text-rose-600 dark:text-rose-400' : 'text-slate-500'
      }`}
    >
      {value > 0 ? '+' : ''}
      {value.toFixed(1)}%
    </span>
  );
}

export function StoreWiseSohTable({
  rows,
  storeSummaries,
  deptSummaries,
  summary,
  serverTotal = 0,
  exportQuery = '',
  asOfDate,
  selectedStore,
  onSelectStore,
}: Props) {
  const [activeTab, setActiveTab] = useState<'barcode_sku' | 'store_summary' | 'dept_section'>('barcode_sku');
  const [exporting, setExporting] = useState(false);

  // Search and column filters
  const [search, setSearch] = useState('');
  const [filterStore, setFilterStore] = useState('all');
  const [filterDept, setFilterDept] = useState('all');
  const [filterSec, setFilterSec] = useState('all');

  // Per-column search filters for Barcode/SKU table
  const [colFilters, setColFilters] = useState({
    store: '',
    barcode: '',
    sku: '',
    size: '',
    department: '',
    section: '',
    minSoh: '',
    minMdq: '',
  });

  // Sorting
  const [sortBy, setSortBy] = useState<string>('currentSoh');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');

  // Pagination
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);

  // 1. Process Store Summaries
  const { storeList, storeMetaMap, totalNetworkSoh, totalStores, uniqueSkusTotal, uniqueBarcodesTotal } = useMemo(() => {
    let list: StoreSummary[] = [];
    let totalSoh = 0;
    let totalStoresCount = 0;
    let uniqueSkus = 0;
    let uniqueBarcodes = 0;

    if (storeSummaries && storeSummaries.length > 0) {
      totalSoh = summary?.total_soh ?? storeSummaries.reduce((sum, s) => sum + s.soh, 0);
      totalStoresCount = summary?.store_count ?? storeSummaries.length;
      uniqueSkus = summary?.sku_count ?? storeSummaries.reduce((sum, s) => Math.max(sum, s.skuCount), 0);
      uniqueBarcodes =
        summary?.barcode_count ??
        storeSummaries.reduce((sum, s) => sum + (Number(s.barcodeCount) || 0), 0);
      list = storeSummaries;
    } else {
      const map = new Map<
        string,
        {
          siteId: string;
          siteName: string;
          soh: number;
          depts: Set<string>;
          secs: Set<string>;
          skus: Set<string>;
          barcodes: Set<string>;
        }
      >();

      const globalSkus = new Set<string>();
      const globalBarcodes = new Set<string>();

      rows.forEach((r) => {
        const id = (r.site_id || 'UNKNOWN').trim();
        const name = (r.site_name || id).trim();
        totalSoh += r.qty;

        const cur = map.get(id) ?? {
          siteId: id,
          siteName: name,
          soh: 0,
          depts: new Set<string>(),
          secs: new Set<string>(),
          skus: new Set<string>(),
          barcodes: new Set<string>(),
        };

        cur.soh += r.qty;
        if (r.department) cur.depts.add(r.department.trim());
        if (r.section) cur.secs.add(r.section.trim());
        if (r.sku) {
          cur.skus.add(r.sku.trim());
          globalSkus.add(r.sku.trim());
        }
        if (r.barcode) {
          cur.barcodes.add(r.barcode.trim());
          globalBarcodes.add(r.barcode.trim());
        }

        map.set(id, cur);
      });

      list = [...map.values()].map((s) => ({
        siteId: s.siteId,
        siteName: s.siteName,
        soh: s.soh,
        pctOfTotal: totalSoh > 0 ? (s.soh / totalSoh) * 100 : 0,
        deptCount: s.depts.size,
        secCount: s.secs.size,
        skuCount: s.skus.size,
        barcodeCount: s.barcodes.size,
      }));

      totalStoresCount = list.length;
      uniqueSkus = globalSkus.size;
      uniqueBarcodes = globalBarcodes.size;
    }

    const metaMap = new Map<string, StoreSummary>();
    list.forEach((s) => metaMap.set(s.siteId, s));

    return {
      storeList: list,
      storeMetaMap: metaMap,
      totalNetworkSoh: totalSoh,
      totalStores: totalStoresCount,
      uniqueSkusTotal: uniqueSkus,
      uniqueBarcodesTotal: uniqueBarcodes,
    };
  }, [storeSummaries, summary, rows]);

  // 2. Process Barcode & SKU Level Records with Store MDQ
  const barcodeSkuRows = useMemo(() => {
    const map = new Map<
      string,
      {
        siteId: string;
        siteName: string;
        barcode: string;
        sku: string;
        size: string;
        department: string;
        section: string;
        currentSoh: number;
        costRate: number;
      }
    >();

    rows.forEach((r) => {
      const siteId = (r.site_id || 'UNKNOWN').trim();
      const siteName = (r.site_name || siteId).trim();
      const barcode = (r.barcode || '').trim();
      const sku = (r.sku || barcode || 'UNKNOWN').trim();
      const size = (r.size || '').trim();
      const department = (r.department || '').trim();
      const section = (r.section || '').trim();

      const key = `${siteId}|${barcode}|${sku}|${size}`;
      const cur = map.get(key) ?? {
        siteId,
        siteName,
        barcode,
        sku,
        size,
        department,
        section,
        currentSoh: 0,
        costRate: r.cost_rate || 0,
      };

      cur.currentSoh += r.qty;
      if (r.cost_rate) cur.costRate = r.cost_rate;
      if (size && !cur.size) cur.size = size;
      if (department && !cur.department) cur.department = department;
      if (section && !cur.section) cur.section = section;

      map.set(key, cur);
    });

    return [...map.values()].map((r) => {
      const storeMeta = storeMetaMap.get(r.siteId);
      // LY only when backend provides it — never invent from current SOH
      const sohLy =
        typeof (r as any).sohLy === 'number'
          ? (r as any).sohLy
          : typeof (r as any).soh_ly === 'number'
            ? (r as any).soh_ly
            : null;
      const vsLy = sohLy != null && sohLy > 0 ? pctVs(r.currentSoh, sohLy) : null;

      return {
        ...r,
        sohLy,
        vsLy,
        // Customer Master MDQ is store-level only — do NOT paste store fill % onto SKU rows
        storeMdq: undefined,
        storeMdqFillPct: undefined,
        storePctOfTotal: storeMeta?.pctOfTotal,
      } as BarcodeSkuRow;
    });
  }, [rows, storeMetaMap]);

  // 3. Process Department & Section Level Summary
  const deptSecRows = useMemo(() => {
    if (deptSummaries && deptSummaries.length > 0) {
      return deptSummaries.map((d) => {
        const sohLy =
          typeof d.sohLy === 'number'
            ? d.sohLy
            : typeof d.soh_ly === 'number'
              ? d.soh_ly
              : null;
        const vsLy = sohLy != null && sohLy > 0 ? pctVs(d.currentSoh, sohLy) : null;
        return {
          ...d,
          sohLy,
          vsLy,
        } as DeptSecSummary;
      });
    }

    const map = new Map<
      string,
      {
        department: string;
        section: string;
        stores: Set<string>;
        skus: Set<string>;
        barcodes: Set<string>;
        currentSoh: number;
        sohLy: number | null;
        hasLy: boolean;
      }
    >();

    barcodeSkuRows.forEach((r) => {
      const dept = r.department || 'General';
      const sec = r.section || 'General';
      const key = `${dept}|${sec}`;

      const cur = map.get(key) ?? {
        department: dept,
        section: sec,
        stores: new Set<string>(),
        skus: new Set<string>(),
        barcodes: new Set<string>(),
        currentSoh: 0,
        sohLy: null as number | null,
        hasLy: false,
      };

      cur.currentSoh += r.currentSoh;
      if (r.sohLy != null) {
        cur.sohLy = (cur.sohLy ?? 0) + r.sohLy;
        cur.hasLy = true;
      }
      cur.stores.add(r.siteId);
      if (r.sku) cur.skus.add(r.sku);
      if (r.barcode) cur.barcodes.add(r.barcode);

      map.set(key, cur);
    });

    return [...map.values()].map((d) => {
      const sohLy = d.hasLy ? d.sohLy : null;
      return {
        department: d.department,
        section: d.section,
        storeCount: d.stores.size,
        skuCount: d.skus.size,
        barcodeCount: d.barcodes.size,
        currentSoh: d.currentSoh,
        sohLy,
        vsLy: sohLy != null && sohLy > 0 ? pctVs(d.currentSoh, sohLy) : null,
      };
    }) as DeptSecSummary[];
  }, [deptSummaries, barcodeSkuRows]);

  // Dynamic filter options
  const storeOptions = useMemo(() => {
    if (storeSummaries && storeSummaries.length > 0) {
      const unique = Array.from(new Set(storeSummaries.map((s) => s.siteId).filter(Boolean))).sort();
      return ['all', ...unique];
    }
    const unique = Array.from(new Set(rows.map((r) => r.site_id).filter(Boolean))).sort();
    return ['all', ...unique];
  }, [storeSummaries, rows]);

  const deptOptions = useMemo(() => {
    if (deptSummaries && deptSummaries.length > 0) {
      const unique = Array.from(new Set(deptSummaries.map((d) => d.department).filter(Boolean))).sort();
      return ['all', ...unique];
    }
    const unique = Array.from(new Set(rows.map((r) => r.department).filter(Boolean))).sort();
    return ['all', ...unique];
  }, [deptSummaries, rows]);

  const secOptions = useMemo(() => {
    if (deptSummaries && deptSummaries.length > 0) {
      let filtered = deptSummaries;
      if (filterDept !== 'all') {
        filtered = filtered.filter((d) => d.department === filterDept);
      }
      const unique = Array.from(new Set(filtered.map((d) => d.section).filter(Boolean))).sort();
      return ['all', ...unique];
    }
    let filtered = rows;
    if (filterDept !== 'all') {
      filtered = filtered.filter((r) => r.department === filterDept);
    }
    const unique = Array.from(new Set(filtered.map((r) => r.section).filter(Boolean))).sort();
    return ['all', ...unique];
  }, [deptSummaries, rows, filterDept]);

  // Filtered & Sorted Barcode / SKU rows
  const filteredBarcodeSku = useMemo(() => {
    let list = barcodeSkuRows;
    const q = search.trim().toLowerCase();

    if (q) {
      list = list.filter(
        (r) =>
          r.barcode.toLowerCase().includes(q) ||
          r.sku.toLowerCase().includes(q) ||
          (r.size && r.size.toLowerCase().includes(q)) ||
          r.siteId.toLowerCase().includes(q) ||
          r.siteName.toLowerCase().includes(q) ||
          r.department.toLowerCase().includes(q) ||
          r.section.toLowerCase().includes(q)
      );
    }

    if (filterStore !== 'all') {
      list = list.filter((r) => r.siteId === filterStore);
    }
    if (filterDept !== 'all') {
      list = list.filter((r) => r.department === filterDept);
    }
    if (filterSec !== 'all') {
      list = list.filter((r) => r.section === filterSec);
    }

    // Per-column filters
    if (colFilters.store) {
      const sq = colFilters.store.toLowerCase();
      list = list.filter((r) => r.siteId.toLowerCase().includes(sq) || r.siteName.toLowerCase().includes(sq));
    }
    if (colFilters.barcode) {
      const bq = colFilters.barcode.toLowerCase();
      list = list.filter((r) => r.barcode.toLowerCase().includes(bq));
    }
    if (colFilters.sku) {
      const kq = colFilters.sku.toLowerCase();
      list = list.filter((r) => r.sku.toLowerCase().includes(kq));
    }
    if (colFilters.size) {
      const szq = colFilters.size.toLowerCase();
      list = list.filter((r) => (r.size || '').toLowerCase().includes(szq));
    }
    if (colFilters.department) {
      const dq = colFilters.department.toLowerCase();
      list = list.filter((r) => r.department.toLowerCase().includes(dq));
    }
    if (colFilters.section) {
      const seq = colFilters.section.toLowerCase();
      list = list.filter((r) => r.section.toLowerCase().includes(seq));
    }
    if (colFilters.minSoh) {
      const minVal = Number(colFilters.minSoh);
      if (!isNaN(minVal)) list = list.filter((r) => r.currentSoh >= minVal);
    }
    if (colFilters.minMdq) {
      const minM = Number(colFilters.minMdq);
      if (!isNaN(minM)) list = list.filter((r) => (r.storeMdq || 0) >= minM);
    }

    return list.sort((a: any, b: any) => {
      let diff = 0;
      if (typeof a[sortBy] === 'number') {
        diff = a[sortBy] - b[sortBy];
      } else {
        diff = String(a[sortBy] || '').localeCompare(String(b[sortBy] || ''));
      }
      return sortDir === 'asc' ? diff : -diff;
    });
  }, [barcodeSkuRows, search, filterStore, filterDept, filterSec, colFilters, sortBy, sortDir]);

  // Filtered & Sorted Store Summary rows
  const filteredStoreList = useMemo(() => {
    let list = storeList;
    const q = search.trim().toLowerCase();

    if (q) {
      list = list.filter(
        (s) =>
          s.siteId.toLowerCase().includes(q) ||
          s.siteName.toLowerCase().includes(q)
      );
    }
    if (filterStore !== 'all') {
      list = list.filter((s) => s.siteId === filterStore);
    }

    if (colFilters.store) {
      const sq = colFilters.store.toLowerCase();
      list = list.filter((s) => s.siteId.toLowerCase().includes(sq) || s.siteName.toLowerCase().includes(sq));
    }
    if (colFilters.minSoh) {
      const minVal = Number(colFilters.minSoh);
      if (!isNaN(minVal)) list = list.filter((s) => s.soh >= minVal);
    }
    if (colFilters.minMdq) {
      const minM = Number(colFilters.minMdq);
      if (!isNaN(minM)) list = list.filter((s) => (s.mdq || 0) >= minM);
    }

    return list.sort((a: any, b: any) => {
      let diff = 0;
      if (sortBy === 'siteId') diff = a.siteId.localeCompare(b.siteId);
      else if (sortBy === 'skuCount') diff = a.skuCount - b.skuCount;
      else if (sortBy === 'barcodeCount') diff = a.barcodeCount - b.barcodeCount;
      else if (sortBy === 'mdq') diff = (a.mdq || 0) - (b.mdq || 0);
      else if (sortBy === 'mdqFillPct') diff = (a.mdqFillPct || 0) - (b.mdqFillPct || 0);
      else if (sortBy === 'pctOfTotal') diff = a.pctOfTotal - b.pctOfTotal;
      else diff = a.soh - b.soh;
      return sortDir === 'asc' ? diff : -diff;
    });
  }, [storeList, search, filterStore, colFilters, sortBy, sortDir]);

  // Filtered & Sorted Dept / Section rows
  const filteredDeptSec = useMemo(() => {
    let list = deptSecRows;
    const q = search.trim().toLowerCase();

    if (q) {
      list = list.filter(
        (d) =>
          d.department.toLowerCase().includes(q) ||
          d.section.toLowerCase().includes(q)
      );
    }
    if (filterDept !== 'all') {
      list = list.filter((d) => d.department === filterDept);
    }
    if (filterSec !== 'all') {
      list = list.filter((d) => d.section === filterSec);
    }

    if (colFilters.department) {
      const dq = colFilters.department.toLowerCase();
      list = list.filter((d) => d.department.toLowerCase().includes(dq));
    }
    if (colFilters.section) {
      const seq = colFilters.section.toLowerCase();
      list = list.filter((d) => d.section.toLowerCase().includes(seq));
    }
    if (colFilters.minSoh) {
      const minVal = Number(colFilters.minSoh);
      if (!isNaN(minVal)) list = list.filter((d) => d.currentSoh >= minVal);
    }

    return list.sort((a: any, b: any) => {
      let diff = 0;
      if (typeof a[sortBy] === 'number') {
        diff = a[sortBy] - b[sortBy];
      } else {
        diff = String(a[sortBy] || '').localeCompare(String(b[sortBy] || ''));
      }
      return sortDir === 'asc' ? diff : -diff;
    });
  }, [deptSecRows, search, filterDept, filterSec, colFilters, sortBy, sortDir]);

  // Active items for pagination based on tab
  const activeDataset =
    activeTab === 'barcode_sku'
      ? filteredBarcodeSku
      : activeTab === 'store_summary'
      ? filteredStoreList
      : filteredDeptSec;

  const totalPages = Math.max(1, Math.ceil(activeDataset.length / pageSize));
  const paginatedData = useMemo(() => {
    const start = (page - 1) * pageSize;
    return activeDataset.slice(start, start + pageSize);
  }, [activeDataset, page, pageSize]);

  const handleSort = (col: string) => {
    if (sortBy === col) {
      setSortDir((p) => (p === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortBy(col);
      setSortDir('desc');
    }
    setPage(1);
  };

  const handleExportCsv = async () => {
    if (activeTab === 'barcode_sku') {
      // Full filtered dataset from API — UI page is only top N by SOH
      if (exportQuery) {
        try {
          setExporting(true);
          const qs = exportQuery.startsWith('?') ? exportQuery.slice(1) : exportQuery;
          const res = await fetch(`${API_URL}/stock-overview/export.csv?${qs}&positive_only=0`);
          if (!res.ok) throw new Error(`Export failed (${res.status})`);
          const blob = await res.blob();
          const url = URL.createObjectURL(blob);
          const link = document.createElement('a');
          link.href = url;
          link.setAttribute('download', `store_barcode_sku_soh_${new Date().toISOString().slice(0, 10)}.csv`);
          document.body.appendChild(link);
          link.click();
          document.body.removeChild(link);
          URL.revokeObjectURL(url);
        } catch (err) {
          console.error(err);
          window.alert(err instanceof Error ? err.message : 'Could not export full SOH CSV');
        } finally {
          setExporting(false);
        }
        return;
      }
      // MDQ is store-level in Customer Master — not exported on SKU/barcode grain
      const headers = [
        'Store Code',
        'Store Name',
        'Barcode',
        'SKU',
        'Size',
        'Department',
        'Section',
        'Current SOH',
        'SOH LY',
        'vs LY %',
      ];
      const csvRows = filteredBarcodeSku.map((r) => [
        r.siteId,
        `"${r.siteName}"`,
        r.barcode,
        r.sku,
        r.size || '',
        r.department,
        r.section,
        r.currentSoh,
        r.sohLy ?? '',
        r.vsLy != null ? `${r.vsLy.toFixed(1)}%` : '',
      ]);
      const blob = new Blob([headers.join(',') + '\n' + csvRows.map((e) => e.join(',')).join('\n')], {
        type: 'text/csv;charset=utf-8;',
      });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', `store_barcode_sku_soh_${new Date().toISOString().slice(0, 10)}.csv`);
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
    } else if (activeTab === 'store_summary') {
      const headers = [
        'Store Code',
        'Store Name',
        'Current SOH',
        'Store MDQ',
        'MDQ Fill %',
        'Share of Total %',
        'Depts Count',
        'Sections Count',
        'SKUs Count',
        'Barcodes Count',
      ];
      const csvRows = filteredStoreList.map((s) => [
        s.siteId,
        `"${s.siteName}"`,
        s.soh,
        s.mdq || '',
        s.mdqFillPct != null ? `${s.mdqFillPct.toFixed(1)}%` : '',
        `${s.pctOfTotal.toFixed(2)}%`,
        s.deptCount,
        s.secCount,
        s.skuCount,
        s.barcodeCount,
      ]);
      const blob = new Blob([headers.join(',') + '\n' + csvRows.map((e) => e.join(',')).join('\n')], {
        type: 'text/csv;charset=utf-8;',
      });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', `store_wise_soh_summary_${new Date().toISOString().slice(0, 10)}.csv`);
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
    } else {
      const headers = ['Department', 'Section', 'Stores Count', 'SKUs Count', 'Barcodes Count', 'Current SOH', 'SOH LY', 'vs LY %'];
      const csvRows = filteredDeptSec.map((d) => [
        `"${d.department}"`,
        `"${d.section}"`,
        d.storeCount,
        d.skuCount,
        d.barcodeCount,
        d.currentSoh,
        d.sohLy ?? '',
        d.vsLy != null ? `${d.vsLy.toFixed(1)}%` : '',
      ]);
      const blob = new Blob([headers.join(',') + '\n' + csvRows.map((e) => e.join(',')).join('\n')], {
        type: 'text/csv;charset=utf-8;',
      });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', `dept_section_soh_${new Date().toISOString().slice(0, 10)}.csv`);
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
    }
  };

  const clearAllFilters = () => {
    setSearch('');
    setFilterStore('all');
    setFilterDept('all');
    setFilterSec('all');
    setColFilters({
      store: '',
      barcode: '',
      sku: '',
      department: '',
      section: '',
      minSoh: '',
      minMdq: '',
    });
    setPage(1);
  };

  const hasActiveFilters = Boolean(
    search ||
      filterStore !== 'all' ||
      filterDept !== 'all' ||
      filterSec !== 'all' ||
      colFilters.store ||
      colFilters.barcode ||
      colFilters.sku ||
      colFilters.department ||
      colFilters.section ||
      colFilters.minSoh ||
      colFilters.minMdq
  );

  return (
    <div className="space-y-4">
      {/* Top Network Highlights Bar */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-5">
        <div className="rounded-2xl border border-slate-200/80 bg-gradient-to-br from-blue-50/50 to-indigo-50/30 p-3.5 shadow-2xs dark:border-slate-800 dark:from-slate-900/60 dark:to-slate-900/30">
          <div className="flex items-center justify-between text-slate-500 dark:text-slate-400">
            <span className="text-[11px] font-semibold uppercase tracking-wider">Network SOH</span>
            <Package className="h-4 w-4 text-blue-500" />
          </div>
          <p className="mt-1 text-xl font-bold tracking-tight text-slate-900 dark:text-white">
            {formatNumber(totalNetworkSoh)}
          </p>
          <span className="text-[10px] text-slate-400">Units in Network</span>
        </div>

        <div className="rounded-2xl border border-slate-200/80 bg-gradient-to-br from-emerald-50/50 to-teal-50/30 p-3.5 shadow-2xs dark:border-slate-800 dark:from-slate-900/60 dark:to-slate-900/30">
          <div className="flex items-center justify-between text-slate-500 dark:text-slate-400">
            <span className="text-[11px] font-semibold uppercase tracking-wider">Active Stores</span>
            <Store className="h-4 w-4 text-emerald-500" />
          </div>
          <p className="mt-1 text-xl font-bold tracking-tight text-slate-900 dark:text-white">{totalStores}</p>
          <span className="text-[10px] text-emerald-600 dark:text-emerald-400">100% network mapped</span>
        </div>

        <div className="rounded-2xl border border-slate-200/80 bg-gradient-to-br from-violet-50/50 to-purple-50/30 p-3.5 shadow-2xs dark:border-slate-800 dark:from-slate-900/60 dark:to-slate-900/30">
          <div className="flex items-center justify-between text-slate-500 dark:text-slate-400">
            <span className="text-[11px] font-semibold uppercase tracking-wider">Unique SKUs</span>
            <Tag className="h-4 w-4 text-violet-500" />
          </div>
          <p className="mt-1 text-xl font-bold tracking-tight text-slate-900 dark:text-white">
            {formatNumber(uniqueSkusTotal)}
          </p>
          <span className="text-[10px] text-slate-400">Active Articles</span>
        </div>

        <div className="rounded-2xl border border-slate-200/80 bg-gradient-to-br from-amber-50/50 to-orange-50/30 p-3.5 shadow-2xs dark:border-slate-800 dark:from-slate-900/60 dark:to-slate-900/30">
          <div className="flex items-center justify-between text-slate-500 dark:text-slate-400">
            <span className="text-[11px] font-semibold uppercase tracking-wider">Unique Barcodes</span>
            <Barcode className="h-4 w-4 text-amber-500" />
          </div>
          <p className="mt-1 text-xl font-bold tracking-tight text-slate-900 dark:text-white">
            {formatNumber(uniqueBarcodesTotal)}
          </p>
          <span className="text-[10px] text-slate-400">Physical Barcodes</span>
        </div>

        <div className="hidden rounded-2xl border border-slate-200/80 bg-gradient-to-br from-sky-50/50 to-cyan-50/30 p-3.5 shadow-2xs dark:border-slate-800 dark:from-slate-900/60 dark:to-slate-900/30 lg:block">
          <div className="flex items-center justify-between text-slate-500 dark:text-slate-400">
            <span className="text-[11px] font-semibold uppercase tracking-wider">Time Horizon</span>
            <Calendar className="h-4 w-4 text-sky-500" />
          </div>
          <p className="mt-1 truncate text-xs font-bold text-slate-900 dark:text-white">
            {asOfDate ? `As of ${asOfDate}` : 'Latest SOH'}
          </p>
          <span className="text-[10px] text-slate-400">
            {asOfDate ? 'Closing SOH on/before date' : 'Current closing SOH'}
          </span>
        </div>
      </div>

      {/* Main Table Card */}
      <div className="rounded-2xl border border-slate-200/90 bg-white shadow-xs dark:border-slate-800 dark:bg-slate-900">
        {/* Header & Controls */}
        <div className="border-b border-slate-200/80 p-4 dark:border-slate-800">
          <div className="flex flex-wrap items-center justify-between gap-3">
            {/* Tab Selection */}
            <div className="flex items-center gap-1.5 rounded-xl border border-slate-200/80 bg-slate-50 p-1 dark:border-slate-800 dark:bg-slate-900/60">
              <button
                onClick={() => {
                  setActiveTab('barcode_sku');
                  setSortBy('currentSoh');
                  setSortDir('desc');
                  setPage(1);
                }}
                className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
                  activeTab === 'barcode_sku'
                    ? 'bg-blue-600 text-white shadow-2xs'
                    : 'text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-700'
                }`}
              >
                <Barcode className="h-3.5 w-3.5" />
                Barcode & SKU Wise SOH
              </button>

              <button
                onClick={() => {
                  setActiveTab('store_summary');
                  setSortBy('soh');
                  setSortDir('desc');
                  setPage(1);
                }}
                className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
                  activeTab === 'store_summary'
                    ? 'bg-blue-600 text-white shadow-2xs'
                    : 'text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-700'
                }`}
              >
                <Store className="h-3.5 w-3.5" />
                Store Wise Summary
              </button>

              <button
                onClick={() => {
                  setActiveTab('dept_section');
                  setSortBy('currentSoh');
                  setSortDir('desc');
                  setPage(1);
                }}
                className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
                  activeTab === 'dept_section'
                    ? 'bg-blue-600 text-white shadow-2xs'
                    : 'text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-700'
                }`}
              >
                <Layers className="h-3.5 w-3.5" />
                Department & Section
              </button>
            </div>

            {/* Quick Export & Actions */}
            <div className="flex items-center gap-2">
              {hasActiveFilters && (
                <button
                  onClick={clearAllFilters}
                  className="inline-flex items-center gap-1 rounded-xl border border-rose-200 bg-rose-50/50 px-2.5 py-1.5 text-xs font-semibold text-rose-700 shadow-2xs transition hover:bg-rose-100/70 dark:border-rose-900/50 dark:bg-rose-950/40 dark:text-rose-300"
                >
                  <X className="h-3.5 w-3.5" />
                  Clear Filters
                </button>
              )}

              <button
                type="button"
                onClick={() => void handleExportCsv()}
                disabled={exporting}
                title={
                  activeTab === 'barcode_sku' && serverTotal > rows.length
                    ? `Download all ${serverTotal.toLocaleString()} store×barcode rows (table shows top ${rows.length})`
                    : 'Export CSV'
                }
                className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 shadow-2xs transition hover:bg-slate-50 disabled:opacity-60 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
              >
                <Download className="h-3.5 w-3.5" />
                {exporting ? 'Exporting…' : activeTab === 'barcode_sku' ? 'Export all CSV' : 'Export CSV'}
              </button>

              <div className="flex items-center gap-1.5">
                <span className="text-[11px] text-slate-400">Rows:</span>
                <select
                  value={pageSize}
                  onChange={(e) => {
                    setPageSize(Number(e.target.value));
                    setPage(1);
                  }}
                  className="rounded-lg border border-slate-200 bg-white px-2 py-1 text-xs font-semibold text-slate-700 outline-none dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300"
                >
                  <option value={20}>20</option>
                  <option value={50}>50</option>
                  <option value={100}>100</option>
                </select>
              </div>
            </div>
          </div>

          {/* Quick Search & Dropdown Filters */}
          <div className="mt-3 flex flex-wrap items-center gap-2.5">
            <div className="relative min-w-[240px] flex-1">
              <Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
              <input
                type="text"
                placeholder={
                  activeTab === 'barcode_sku'
                    ? 'Search barcode, SKU, store, dept, section…'
                    : activeTab === 'store_summary'
                    ? 'Search store code or store name…'
                    : 'Search department or section…'
                }
                value={search}
                onChange={(e) => {
                  setSearch(e.target.value);
                  setPage(1);
                }}
                className="w-full rounded-xl border border-slate-200 bg-white py-1.5 pl-9 pr-3 text-xs text-slate-800 placeholder-slate-400 outline-none transition focus:border-blue-500 focus:ring-1 focus:ring-blue-500/20 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
              />
              {search && (
                <button
                  onClick={() => setSearch('')}
                  className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              )}
            </div>

            {/* Store Filter */}
            {activeTab !== 'dept_section' && (
              <select
                value={filterStore}
                onChange={(e) => {
                  setFilterStore(e.target.value);
                  setPage(1);
                }}
                className="rounded-xl border border-slate-200 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-700 outline-none transition focus:border-blue-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300"
              >
                <option value="all">All Stores ({storeOptions.length - 1})</option>
                {storeOptions
                  .filter((s) => s !== 'all')
                  .map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
              </select>
            )}

            {/* Dept Filter */}
            {activeTab !== 'store_summary' && (
              <select
                value={filterDept}
                onChange={(e) => {
                  setFilterDept(e.target.value);
                  setFilterSec('all');
                  setPage(1);
                }}
                className="rounded-xl border border-slate-200 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-700 outline-none transition focus:border-blue-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300"
              >
                <option value="all">All Departments ({deptOptions.length - 1})</option>
                {deptOptions
                  .filter((d) => d !== 'all')
                  .map((d) => (
                    <option key={d} value={d}>
                      {d}
                    </option>
                  ))}
              </select>
            )}

            {/* Section Filter */}
            {activeTab !== 'store_summary' && secOptions.length > 1 && (
              <select
                value={filterSec}
                onChange={(e) => {
                  setFilterSec(e.target.value);
                  setPage(1);
                }}
                className="rounded-xl border border-slate-200 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-700 outline-none transition focus:border-blue-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300"
              >
                <option value="all">All Sections ({secOptions.length - 1})</option>
                {secOptions
                  .filter((s) => s !== 'all')
                  .map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
              </select>
            )}
          </div>
        </div>

        {/* 1. Barcode & SKU Level Table (ALL COLUMNS INCLUDED) */}
        {activeTab === 'barcode_sku' && (
          <div className="max-h-[580px] overflow-x-auto overflow-y-auto custom-scrollbar">
            <table className="w-full min-w-[1100px] border-collapse text-left text-xs">
              <thead className="sticky top-0 z-10 bg-slate-100 text-[11px] font-bold uppercase tracking-wider text-slate-700 shadow-2xs dark:bg-slate-800 dark:text-slate-200">
                <tr className="border-b border-slate-200 dark:border-slate-700">
                  <th
                    onClick={() => handleSort('siteId')}
                    className="cursor-pointer px-3 py-2.5 hover:bg-slate-200/70 dark:hover:bg-slate-700"
                  >
                    <div className="flex items-center gap-1">
                      <span>Store Code / Name</span>
                      <ArrowUpDown className="h-3 w-3 text-slate-400" />
                    </div>
                  </th>
                  <th
                    onClick={() => handleSort('barcode')}
                    className="cursor-pointer px-3 py-2.5 hover:bg-slate-200/70 dark:hover:bg-slate-700"
                  >
                    <div className="flex items-center gap-1">
                      <span>Barcode</span>
                      <ArrowUpDown className="h-3 w-3 text-slate-400" />
                    </div>
                  </th>
                  <th
                    onClick={() => handleSort('sku')}
                    className="cursor-pointer px-3 py-2.5 hover:bg-slate-200/70 dark:hover:bg-slate-700"
                  >
                    <div className="flex items-center gap-1">
                      <span>SKU Code</span>
                      <ArrowUpDown className="h-3 w-3 text-slate-400" />
                    </div>
                  </th>
                  <th
                    onClick={() => handleSort('size')}
                    className="cursor-pointer px-3 py-2.5 hover:bg-slate-200/70 dark:hover:bg-slate-700"
                  >
                    <div className="flex items-center gap-1">
                      <span>Size</span>
                      <ArrowUpDown className="h-3 w-3 text-slate-400" />
                    </div>
                  </th>
                  <th
                    onClick={() => handleSort('department')}
                    className="cursor-pointer px-3 py-2.5 hover:bg-slate-200/70 dark:hover:bg-slate-700"
                  >
                    <div className="flex items-center gap-1">
                      <span>Department</span>
                      <ArrowUpDown className="h-3 w-3 text-slate-400" />
                    </div>
                  </th>
                  <th
                    onClick={() => handleSort('section')}
                    className="cursor-pointer px-3 py-2.5 hover:bg-slate-200/70 dark:hover:bg-slate-700"
                  >
                    <div className="flex items-center gap-1">
                      <span>Section</span>
                      <ArrowUpDown className="h-3 w-3 text-slate-400" />
                    </div>
                  </th>
                  <th
                    onClick={() => handleSort('currentSoh')}
                    className="cursor-pointer px-3 py-2.5 text-right hover:bg-slate-200/70 dark:hover:bg-slate-700"
                  >
                    <div className="flex items-center justify-end gap-1">
                      <span>Current SOH</span>
                      <ArrowUpDown className="h-3 w-3 text-slate-400" />
                    </div>
                  </th>
                  <th
                    onClick={() => handleSort('sohLy')}
                    className="cursor-pointer px-3 py-2.5 text-right hover:bg-slate-200/70 dark:hover:bg-slate-700"
                  >
                    <div className="flex items-center justify-end gap-1">
                      <span>SOH LY</span>
                      <ArrowUpDown className="h-3 w-3 text-slate-400" />
                    </div>
                  </th>
                  <th
                    onClick={() => handleSort('vsLy')}
                    className="cursor-pointer px-3 py-2.5 text-right hover:bg-slate-200/70 dark:hover:bg-slate-700"
                  >
                    <div className="flex items-center justify-end gap-1">
                      <span>vs LY %</span>
                      <ArrowUpDown className="h-3 w-3 text-slate-400" />
                    </div>
                  </th>
                </tr>

                {/* Per-Column Search Inputs Row */}
                <tr className="border-b border-slate-200/80 bg-slate-50/80 text-[10px] font-normal normal-case dark:border-slate-700 dark:bg-slate-800/80">
                  <th className="px-2 py-1.5">
                    <input
                      type="text"
                      placeholder="Filter store…"
                      value={colFilters.store}
                      onChange={(e) => {
                        setColFilters((p) => ({ ...p, store: e.target.value }));
                        setPage(1);
                      }}
                      className="w-full rounded border border-slate-200 bg-white px-2 py-0.5 text-[11px] text-slate-700 placeholder-slate-400 outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
                    />
                  </th>
                  <th className="px-2 py-1.5">
                    <input
                      type="text"
                      placeholder="Filter barcode…"
                      value={colFilters.barcode}
                      onChange={(e) => {
                        setColFilters((p) => ({ ...p, barcode: e.target.value }));
                        setPage(1);
                      }}
                      className="w-full rounded border border-slate-200 bg-white px-2 py-0.5 text-[11px] text-slate-700 placeholder-slate-400 outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
                    />
                  </th>
                  <th className="px-2 py-1.5">
                    <input
                      type="text"
                      placeholder="Filter SKU…"
                      value={colFilters.sku}
                      onChange={(e) => {
                        setColFilters((p) => ({ ...p, sku: e.target.value }));
                        setPage(1);
                      }}
                      className="w-full rounded border border-slate-200 bg-white px-2 py-0.5 text-[11px] text-slate-700 placeholder-slate-400 outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
                    />
                  </th>
                  <th className="px-2 py-1.5">
                    <input
                      type="text"
                      placeholder="Filter size…"
                      value={colFilters.size}
                      onChange={(e) => {
                        setColFilters((p) => ({ ...p, size: e.target.value }));
                        setPage(1);
                      }}
                      className="w-full rounded border border-slate-200 bg-white px-2 py-0.5 text-[11px] text-slate-700 placeholder-slate-400 outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
                    />
                  </th>
                  <th className="px-2 py-1.5">
                    <input
                      type="text"
                      placeholder="Filter dept…"
                      value={colFilters.department}
                      onChange={(e) => {
                        setColFilters((p) => ({ ...p, department: e.target.value }));
                        setPage(1);
                      }}
                      className="w-full rounded border border-slate-200 bg-white px-2 py-0.5 text-[11px] text-slate-700 placeholder-slate-400 outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
                    />
                  </th>
                  <th className="px-2 py-1.5">
                    <input
                      type="text"
                      placeholder="Filter section…"
                      value={colFilters.section}
                      onChange={(e) => {
                        setColFilters((p) => ({ ...p, section: e.target.value }));
                        setPage(1);
                      }}
                      className="w-full rounded border border-slate-200 bg-white px-2 py-0.5 text-[11px] text-slate-700 placeholder-slate-400 outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
                    />
                  </th>
                  <th className="px-2 py-1.5 text-right">
                    <input
                      type="text"
                      placeholder="Min SOH…"
                      value={colFilters.minSoh}
                      onChange={(e) => {
                        setColFilters((p) => ({ ...p, minSoh: e.target.value }));
                        setPage(1);
                      }}
                      className="w-20 rounded border border-slate-200 bg-white px-2 py-0.5 text-right text-[11px] text-slate-700 placeholder-slate-400 outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
                    />
                  </th>
                  <th className="px-2 py-1.5 text-right"></th>
                  <th className="px-2 py-1.5 text-right"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {paginatedData.length === 0 ? (
                  <tr>
                    <td colSpan={9} className="py-8 text-center text-slate-400">
                      No barcode & SKU records match the filters
                    </td>
                  </tr>
                ) : (
                  (paginatedData as BarcodeSkuRow[]).map((r, idx) => (
                    <tr
                      key={`${r.siteId}-${r.barcode}-${r.sku}-${r.size}-${idx}`}
                      className="transition hover:bg-slate-50/80 dark:hover:bg-slate-800/50"
                    >
                      <td className="px-3 py-2">
                        <span className="font-semibold text-slate-900 dark:text-white">{r.siteId}</span>
                        {r.siteName && r.siteName !== r.siteId && (
                          <span className="ml-1.5 text-[10px] text-slate-400">({r.siteName})</span>
                        )}
                      </td>
                      <td className="px-3 py-2 font-mono text-xs font-semibold text-slate-800 dark:text-slate-200">
                        {r.barcode || '—'}
                      </td>
                      <td className="px-3 py-2">
                        <SkuHoverCard sku={r.sku}>
                          <span className="cursor-pointer font-medium text-blue-600 hover:underline dark:text-blue-400">
                            {r.sku}
                          </span>
                        </SkuHoverCard>
                      </td>
                      <td className="px-3 py-2 font-mono text-xs font-semibold text-slate-700 dark:text-slate-300">
                        {r.size || '—'}
                      </td>
                      <td className="px-3 py-2 text-slate-600 dark:text-slate-300">{r.department || '—'}</td>
                      <td className="px-3 py-2 text-slate-600 dark:text-slate-300">{r.section || '—'}</td>
                      <td className="px-3 py-2 text-right font-bold text-slate-900 dark:text-slate-100">
                        {formatNumber(r.currentSoh)}
                      </td>
                      <td className="px-3 py-2 text-right text-slate-500 dark:text-slate-400">
                        {formatNumber(r.sohLy)}
                      </td>
                      <td className="px-3 py-2 text-right">
                        <Delta value={r.vsLy} />
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        )}

        {/* 2. Store Wise Summary Table (NO DRILLDOWN COLUMN - ALL DATA DIRECT) */}
        {activeTab === 'store_summary' && (
          <div className="max-h-[580px] overflow-x-auto overflow-y-auto custom-scrollbar">
            <table className="w-full min-w-[850px] border-collapse text-left text-xs">
              <thead className="sticky top-0 z-10 bg-slate-100 text-[11px] font-bold uppercase tracking-wider text-slate-700 shadow-2xs dark:bg-slate-800 dark:text-slate-200">
                <tr className="border-b border-slate-200 dark:border-slate-700">
                  <th
                    onClick={() => handleSort('siteId')}
                    className="cursor-pointer px-4 py-2.5 hover:bg-slate-200/70 dark:hover:bg-slate-700"
                  >
                    <div className="flex items-center gap-1">
                      <span>Store Code / Name</span>
                      <ArrowUpDown className="h-3 w-3 text-slate-400" />
                    </div>
                  </th>
                  <th
                    onClick={() => handleSort('soh')}
                    className="cursor-pointer px-3 py-2.5 text-right hover:bg-slate-200/70 dark:hover:bg-slate-700"
                  >
                    <div className="flex items-center justify-end gap-1">
                      <span>Current SOH</span>
                      <ArrowUpDown className="h-3 w-3 text-slate-400" />
                    </div>
                  </th>
                  <th
                    onClick={() => handleSort('mdq')}
                    className="cursor-pointer px-3 py-2.5 text-right hover:bg-slate-200/70 dark:hover:bg-slate-700"
                  >
                    <div className="flex items-center justify-end gap-1">
                      <span>Store MDQ</span>
                      <ArrowUpDown className="h-3 w-3 text-slate-400" />
                    </div>
                  </th>
                  <th
                    onClick={() => handleSort('mdqFillPct')}
                    className="cursor-pointer px-3 py-2.5 text-right hover:bg-slate-200/70 dark:hover:bg-slate-700"
                  >
                    <div className="flex items-center justify-end gap-1">
                      <span>MDQ Fill %</span>
                      <ArrowUpDown className="h-3 w-3 text-slate-400" />
                    </div>
                  </th>
                  <th
                    onClick={() => handleSort('pctOfTotal')}
                    className="cursor-pointer px-3 py-2.5 text-right hover:bg-slate-200/70 dark:hover:bg-slate-700"
                  >
                    <div className="flex items-center justify-end gap-1">
                      <span>Share of Total</span>
                      <ArrowUpDown className="h-3 w-3 text-slate-400" />
                    </div>
                  </th>
                  <th className="px-3 py-2.5 text-center">Depts</th>
                  <th className="px-3 py-2.5 text-center">Sections</th>
                  <th
                    onClick={() => handleSort('skuCount')}
                    className="cursor-pointer px-3 py-2.5 text-center hover:bg-slate-200/70 dark:hover:bg-slate-700"
                  >
                    <div className="flex items-center justify-center gap-1">
                      <span>SKUs</span>
                      <ArrowUpDown className="h-3 w-3 text-slate-400" />
                    </div>
                  </th>
                  <th
                    onClick={() => handleSort('barcodeCount')}
                    className="cursor-pointer px-3 py-2.5 text-center hover:bg-slate-200/70 dark:hover:bg-slate-700"
                  >
                    <div className="flex items-center justify-center gap-1">
                      <span>Barcodes</span>
                      <ArrowUpDown className="h-3 w-3 text-slate-400" />
                    </div>
                  </th>
                </tr>

                {/* Per-Column Search Inputs */}
                <tr className="border-b border-slate-200/80 bg-slate-50/80 text-[10px] font-normal normal-case dark:border-slate-700 dark:bg-slate-800/80">
                  <th className="px-3 py-1.5">
                    <input
                      type="text"
                      placeholder="Filter store…"
                      value={colFilters.store}
                      onChange={(e) => {
                        setColFilters((p) => ({ ...p, store: e.target.value }));
                        setPage(1);
                      }}
                      className="w-full rounded border border-slate-200 bg-white px-2 py-0.5 text-[11px] text-slate-700 placeholder-slate-400 outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
                    />
                  </th>
                  <th className="px-2 py-1.5 text-right">
                    <input
                      type="text"
                      placeholder="Min SOH…"
                      value={colFilters.minSoh}
                      onChange={(e) => {
                        setColFilters((p) => ({ ...p, minSoh: e.target.value }));
                        setPage(1);
                      }}
                      className="w-20 rounded border border-slate-200 bg-white px-2 py-0.5 text-right text-[11px] text-slate-700 placeholder-slate-400 outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
                    />
                  </th>
                  <th className="px-2 py-1.5 text-right">
                    <input
                      type="text"
                      placeholder="Min MDQ…"
                      value={colFilters.minMdq}
                      onChange={(e) => {
                        setColFilters((p) => ({ ...p, minMdq: e.target.value }));
                        setPage(1);
                      }}
                      className="w-20 rounded border border-slate-200 bg-white px-2 py-0.5 text-right text-[11px] text-slate-700 placeholder-slate-400 outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
                    />
                  </th>
                  <th className="px-2 py-1.5 text-right"></th>
                  <th className="px-2 py-1.5 text-right"></th>
                  <th className="px-2 py-1.5 text-center"></th>
                  <th className="px-2 py-1.5 text-center"></th>
                  <th className="px-2 py-1.5 text-center"></th>
                  <th className="px-2 py-1.5 text-center"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {paginatedData.length === 0 ? (
                  <tr>
                    <td colSpan={9} className="py-8 text-center text-slate-400">
                      No stores found
                    </td>
                  </tr>
                ) : (
                  (paginatedData as StoreSummary[]).map((s) => (
                    <tr
                      key={s.siteId}
                      className="transition hover:bg-slate-50/80 dark:hover:bg-slate-800/50"
                    >
                      <td className="px-4 py-2.5">
                        <div className="flex items-center gap-2">
                          <Building2 className="h-3.5 w-3.5 text-slate-400" />
                          <div>
                            <span className="font-semibold text-slate-900 dark:text-white">{s.siteId}</span>
                            {s.siteName && s.siteName !== s.siteId && (
                              <span className="ml-2 text-[11px] text-slate-500">{s.siteName}</span>
                            )}
                          </div>
                        </div>
                      </td>
                      <td className="px-3 py-2.5 text-right font-bold text-slate-900 dark:text-slate-100">
                        {formatNumber(s.soh)}
                      </td>
                      <td className="px-3 py-2.5 text-right font-medium text-slate-700 dark:text-slate-300">
                        {s.mdq ? formatNumber(s.mdq) : <span className="text-slate-400">—</span>}
                      </td>
                      <td className="px-3 py-2.5 text-right">
                        {s.mdqFillPct != null ? (
                          <div className="flex items-center justify-end gap-1.5">
                            <span
                              className={`font-semibold ${
                                s.mdqFillPct < 75
                                  ? 'text-rose-600 dark:text-rose-400'
                                  : s.mdqFillPct > 115
                                  ? 'text-amber-600 dark:text-amber-400'
                                  : 'text-emerald-600 dark:text-emerald-400'
                              }`}
                            >
                              {s.mdqFillPct.toFixed(1)}%
                            </span>
                            <div className="h-1.5 w-10 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-700">
                              <div
                                className={`h-full rounded-full ${
                                  s.mdqFillPct < 75
                                    ? 'bg-rose-500'
                                    : s.mdqFillPct > 115
                                    ? 'bg-amber-500'
                                    : 'bg-emerald-500'
                                }`}
                                style={{ width: `${Math.min(100, s.mdqFillPct)}%` }}
                              />
                            </div>
                          </div>
                        ) : (
                          <span className="text-slate-400">—</span>
                        )}
                      </td>
                      <td className="px-3 py-2.5 text-right text-slate-600 dark:text-slate-400">
                        <div className="flex items-center justify-end gap-1.5">
                          <span>{s.pctOfTotal.toFixed(1)}%</span>
                          <div className="h-1.5 w-10 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-700">
                            <div
                              className="h-full rounded-full bg-blue-500"
                              style={{ width: `${Math.min(100, s.pctOfTotal * 3)}%` }}
                            />
                          </div>
                        </div>
                      </td>
                      <td className="px-3 py-2.5 text-center text-slate-600 dark:text-slate-400">{s.deptCount}</td>
                      <td className="px-3 py-2.5 text-center text-slate-600 dark:text-slate-400">{s.secCount}</td>
                      <td className="px-3 py-2.5 text-center text-slate-600 dark:text-slate-400">{s.skuCount}</td>
                      <td className="px-3 py-2.5 text-center text-slate-600 dark:text-slate-400">{s.barcodeCount}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        )}

        {/* 3. Department & Section Summary Table */}
        {activeTab === 'dept_section' && (
          <div className="max-h-[580px] overflow-x-auto overflow-y-auto custom-scrollbar">
            <table className="w-full min-w-[800px] border-collapse text-left text-xs">
              <thead className="sticky top-0 z-10 bg-slate-100 text-[11px] font-bold uppercase tracking-wider text-slate-700 shadow-2xs dark:bg-slate-800 dark:text-slate-200">
                <tr className="border-b border-slate-200 dark:border-slate-700">
                  <th
                    onClick={() => handleSort('department')}
                    className="cursor-pointer px-4 py-2.5 hover:bg-slate-200/70 dark:hover:bg-slate-700"
                  >
                    <div className="flex items-center gap-1">
                      <span>Department</span>
                      <ArrowUpDown className="h-3 w-3 text-slate-400" />
                    </div>
                  </th>
                  <th
                    onClick={() => handleSort('section')}
                    className="cursor-pointer px-3 py-2.5 hover:bg-slate-200/70 dark:hover:bg-slate-700"
                  >
                    <div className="flex items-center gap-1">
                      <span>Section</span>
                      <ArrowUpDown className="h-3 w-3 text-slate-400" />
                    </div>
                  </th>
                  <th
                    onClick={() => handleSort('storeCount')}
                    className="cursor-pointer px-3 py-2.5 text-center hover:bg-slate-200/70 dark:hover:bg-slate-700"
                  >
                    <div className="flex items-center justify-center gap-1">
                      <span>Stores</span>
                      <ArrowUpDown className="h-3 w-3 text-slate-400" />
                    </div>
                  </th>
                  <th
                    onClick={() => handleSort('skuCount')}
                    className="cursor-pointer px-3 py-2.5 text-center hover:bg-slate-200/70 dark:hover:bg-slate-700"
                  >
                    <div className="flex items-center justify-center gap-1">
                      <span>SKUs</span>
                      <ArrowUpDown className="h-3 w-3 text-slate-400" />
                    </div>
                  </th>
                  <th
                    onClick={() => handleSort('barcodeCount')}
                    className="cursor-pointer px-3 py-2.5 text-center hover:bg-slate-200/70 dark:hover:bg-slate-700"
                  >
                    <div className="flex items-center justify-center gap-1">
                      <span>Barcodes</span>
                      <ArrowUpDown className="h-3 w-3 text-slate-400" />
                    </div>
                  </th>
                  <th
                    onClick={() => handleSort('currentSoh')}
                    className="cursor-pointer px-3 py-2.5 text-right hover:bg-slate-200/70 dark:hover:bg-slate-700"
                  >
                    <div className="flex items-center justify-end gap-1">
                      <span>Current SOH</span>
                      <ArrowUpDown className="h-3 w-3 text-slate-400" />
                    </div>
                  </th>
                  <th
                    onClick={() => handleSort('sohLy')}
                    className="cursor-pointer px-3 py-2.5 text-right hover:bg-slate-200/70 dark:hover:bg-slate-700"
                  >
                    <div className="flex items-center justify-end gap-1">
                      <span>SOH LY</span>
                      <ArrowUpDown className="h-3 w-3 text-slate-400" />
                    </div>
                  </th>
                  <th
                    onClick={() => handleSort('vsLy')}
                    className="cursor-pointer px-4 py-2.5 text-right hover:bg-slate-200/70 dark:hover:bg-slate-700"
                  >
                    <div className="flex items-center justify-end gap-1">
                      <span>vs LY Growth</span>
                      <ArrowUpDown className="h-3 w-3 text-slate-400" />
                    </div>
                  </th>
                </tr>

                {/* Per-column search inputs */}
                <tr className="border-b border-slate-200/80 bg-slate-50/80 text-[10px] font-normal normal-case dark:border-slate-700 dark:bg-slate-800/80">
                  <th className="px-3 py-1.5">
                    <input
                      type="text"
                      placeholder="Filter dept…"
                      value={colFilters.department}
                      onChange={(e) => {
                        setColFilters((p) => ({ ...p, department: e.target.value }));
                        setPage(1);
                      }}
                      className="w-full rounded border border-slate-200 bg-white px-2 py-0.5 text-[11px] text-slate-700 placeholder-slate-400 outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
                    />
                  </th>
                  <th className="px-3 py-1.5">
                    <input
                      type="text"
                      placeholder="Filter section…"
                      value={colFilters.section}
                      onChange={(e) => {
                        setColFilters((p) => ({ ...p, section: e.target.value }));
                        setPage(1);
                      }}
                      className="w-full rounded border border-slate-200 bg-white px-2 py-0.5 text-[11px] text-slate-700 placeholder-slate-400 outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
                    />
                  </th>
                  <th className="px-2 py-1.5 text-center"></th>
                  <th className="px-2 py-1.5 text-center"></th>
                  <th className="px-2 py-1.5 text-center"></th>
                  <th className="px-2 py-1.5 text-right">
                    <input
                      type="text"
                      placeholder="Min SOH…"
                      value={colFilters.minSoh}
                      onChange={(e) => {
                        setColFilters((p) => ({ ...p, minSoh: e.target.value }));
                        setPage(1);
                      }}
                      className="w-20 rounded border border-slate-200 bg-white px-2 py-0.5 text-right text-[11px] text-slate-700 placeholder-slate-400 outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
                    />
                  </th>
                  <th className="px-2 py-1.5 text-right"></th>
                  <th className="px-2 py-1.5 text-right"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {paginatedData.length === 0 ? (
                  <tr>
                    <td colSpan={8} className="py-8 text-center text-slate-400">
                      No departments match the filters
                    </td>
                  </tr>
                ) : (
                  (paginatedData as DeptSecSummary[]).map((d) => (
                    <tr
                      key={`${d.department}-${d.section}`}
                      className="transition hover:bg-slate-50/80 dark:hover:bg-slate-800/50"
                    >
                      <td className="px-4 py-2.5 font-semibold text-slate-900 dark:text-white">{d.department}</td>
                      <td className="px-3 py-2.5 text-slate-600 dark:text-slate-300">{d.section}</td>
                      <td className="px-3 py-2.5 text-center text-slate-600 dark:text-slate-400">{d.storeCount}</td>
                      <td className="px-3 py-2.5 text-center text-slate-600 dark:text-slate-400">{d.skuCount}</td>
                      <td className="px-3 py-2.5 text-center text-slate-600 dark:text-slate-400">{d.barcodeCount}</td>
                      <td className="px-3 py-2.5 text-right font-bold text-slate-900 dark:text-slate-100">
                        {formatNumber(d.currentSoh)}
                      </td>
                      <td className="px-3 py-2.5 text-right text-slate-500 dark:text-slate-400">
                        {formatNumber(d.sohLy)}
                      </td>
                      <td className="px-4 py-2.5 text-right">
                        <Delta value={d.vsLy} />
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        )}

        {/* Footer & Pagination */}
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-200/80 px-4 py-3 text-xs text-slate-500 dark:border-slate-800 dark:text-slate-400">
          <div className="flex items-center gap-2">
            <span>
              Showing {activeDataset.length > 0 ? (page - 1) * pageSize + 1 : 0} to{' '}
              {Math.min(page * pageSize, activeDataset.length)} of {formatNumber(activeDataset.length)} entries
            </span>
            {hasActiveFilters && (
              <span className="rounded bg-blue-50 px-1.5 py-0.5 text-[10px] font-semibold text-blue-600 dark:bg-blue-900/40 dark:text-blue-300">
                (Filtered)
              </span>
            )}
          </div>

          <div className="flex items-center gap-1.5">
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page === 1}
              className="rounded-lg border border-slate-200 p-1.5 transition hover:bg-slate-50 disabled:opacity-40 dark:border-slate-700 dark:hover:bg-slate-800"
            >
              <ChevronLeft className="h-4 w-4" />
            </button>
            <span className="px-2 font-semibold text-slate-700 dark:text-slate-200">
              Page {page} of {totalPages}
            </span>
            <button
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page === totalPages}
              className="rounded-lg border border-slate-200 p-1.5 transition hover:bg-slate-50 disabled:opacity-40 dark:border-slate-700 dark:hover:bg-slate-800"
            >
              <ChevronRight className="h-4 w-4" />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

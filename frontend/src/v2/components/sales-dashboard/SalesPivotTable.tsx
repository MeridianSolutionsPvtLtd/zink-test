import React, { useMemo, useState, useEffect, useRef } from 'react';
import {
  ArrowDown,
  ArrowUp,
  BarChart2,
  ChevronDown,
  ChevronRight,
  Download,
  Filter,
  Layers,
  Loader2,
  RotateCcw,
  Search,
  Table as TableIcon,
} from 'lucide-react';
import ExcelJS from 'exceljs';
import { API_URL } from '../../../config';
import type { SalesRow, TargetSummary } from './types';
import {
  deriveCity,
  deriveCluster,
  deriveCustomerName,
  deriveSalesChannel,
  deriveState,
  deriveStoreGrade,
  deriveZone,
  formatInr,
} from './salesAnalytics';

export type PivotDimensionKey =
  | 'zone'
  | 'sales_channel'
  | 'site_id'
  | 'state_name'
  | 'city_name'
  | 'store_grade'
  | 'cluster'
  | 'department'
  | 'section'
  | 'month'
  | 'fy_week'
  | 'size'
  | 'sku'
  | 'customer_name';

export type PivotColumnSplitKey = 'none' | 'sales_channel' | 'month';

const DIMENSION_OPTIONS: { key: PivotDimensionKey; label: string }[] = [
  { key: 'zone', label: 'Zone' },
  { key: 'sales_channel', label: 'Format / Channel' },
  { key: 'site_id', label: 'Store / Site' },
  { key: 'state_name', label: 'State' },
  { key: 'city_name', label: 'City' },
  { key: 'store_grade', label: 'Store Grade' },
  { key: 'cluster', label: 'Manager / Cluster' },
  { key: 'department', label: 'Department' },
  { key: 'section', label: 'Section' },
  { key: 'month', label: 'Month' },
  { key: 'fy_week', label: 'FY Week' },
  { key: 'size', label: 'Size' },
  { key: 'sku', label: 'SKU / Style' },
  { key: 'customer_name', label: 'Customer Name' },
];

const SECONDARY_DIMENSION_OPTIONS: { key: PivotDimensionKey | 'none'; label: string }[] = [
  { key: 'none', label: 'None (Level 1 Only)' },
  { key: 'sales_channel', label: 'Format / Channel' },
  { key: 'site_id', label: 'Store / Site' },
  { key: 'section', label: 'Section' },
  { key: 'department', label: 'Department' },
  { key: 'cluster', label: 'Manager / Cluster' },
  { key: 'size', label: 'Size' },
  { key: 'sku', label: 'SKU / Style' },
  { key: 'zone', label: 'Zone' },
  { key: 'state_name', label: 'State' },
  { key: 'city_name', label: 'City' },
  { key: 'month', label: 'Month' },
];

const TERTIARY_DIMENSION_OPTIONS: { key: PivotDimensionKey | 'none'; label: string }[] = [
  { key: 'none', label: 'None (Level 2 Max)' },
  { key: 'section', label: 'Section' },
  { key: 'size', label: 'Size' },
  { key: 'sku', label: 'SKU / Style' },
  { key: 'site_id', label: 'Store / Site' },
  { key: 'sales_channel', label: 'Format / Channel' },
  { key: 'department', label: 'Department' },
  { key: 'cluster', label: 'Manager / Cluster' },
];


const PIVOT_METRIC_COLUMNS = [
  { key: 'target', label: 'Target (LFS)' },
  { key: 'total_sales', label: 'Actual Sales (GSV)' },
  { key: 'ly_sales', label: 'LY Sales' },
  { key: 'yoyPct', label: 'Sales YoY %' },
  { key: 'total_qty', label: 'Qty Sold' },
  { key: 'total_soh', label: 'Current SOH' },
  { key: 'mdq', label: 'MDQ' },
  { key: 'achvPct', label: 'Achv %' },
  { key: 'asp', label: 'ASP' },
  { key: 'discPct', label: 'Discount %' },
];

const MULTI_GROUP_PRESETS: {
  label: string;
  primary: PivotDimensionKey;
  secondary: PivotDimensionKey | 'none';
  tertiary: PivotDimensionKey | 'none';
}[] = [
  { label: 'Zone → Store', primary: 'zone', secondary: 'site_id', tertiary: 'none' },
  { label: 'Zone → Format → Section', primary: 'zone', secondary: 'sales_channel', tertiary: 'section' },
  { label: 'Format → Store → Section', primary: 'sales_channel', secondary: 'site_id', tertiary: 'section' },
  { label: 'Manager → Store → Channel', primary: 'cluster', secondary: 'site_id', tertiary: 'sales_channel' },
  { label: 'Department → Section → Size', primary: 'department', secondary: 'section', tertiary: 'size' },
  { label: 'SKU → Size', primary: 'sku', secondary: 'size', tertiary: 'none' },
];

function deriveFyWeek(dateStr: string): string {
  if (!dateStr) return 'WK-01';
  const d = new Date(dateStr);
  if (isNaN(d.getTime())) return 'WK-01';
  const year = d.getFullYear();
  const month = d.getMonth() + 1;
  const fyStartYear = month >= 4 ? year : year - 1;
  const fyStart = new Date(fyStartYear, 3, 1);
  const diffDays = Math.floor((d.getTime() - fyStart.getTime()) / (1000 * 3600 * 24));
  if (diffDays < 0) return 'WK-01';
  const weekNum = Math.min(52, Math.floor(diffDays / 7) + 1);
  return `WK-${String(weekNum).padStart(2, '0')}`;
}

function deriveMonthName(dateStr: string): string {
  if (!dateStr) return 'Unspecified';
  const d = new Date(dateStr);
  if (isNaN(d.getTime())) return dateStr.substring(0, 7) || 'Unspecified';
  return d.toLocaleDateString('en-IN', { month: 'short', year: '2-digit' });
}

function getDimensionValue(row: any, key: PivotDimensionKey): string {
  switch (key) {
    case 'zone':
      return deriveZone(row) || 'Unspecified';
    case 'sales_channel':
      return deriveSalesChannel(row) || 'Unspecified';
    case 'site_id':
      return row.site_name ? `${row.site_id} - ${row.site_name}` : row.site_id || 'Unspecified';
    case 'state_name':
      return deriveState(row) || 'Unspecified';
    case 'city_name':
      return deriveCity(row) || 'Unspecified';
    case 'store_grade':
      return deriveStoreGrade(row) || 'Unspecified';
    case 'cluster':
      return deriveCluster(row) || 'Unspecified';
    case 'department':
      return (row.department || '').trim() || 'Unspecified';
    case 'section':
      return (row.section || '').trim() || 'Unspecified';
    case 'month':
      return deriveMonthName(row.date);
    case 'fy_week':
      return deriveFyWeek(row.date);
    case 'size':
      return (row.size || '').trim() || 'Unspecified';
    case 'sku':
      return (row.sku || '').trim() || 'Unspecified';
    case 'customer_name':
      return deriveCustomerName(row) || 'Unspecified';
    default:
      return 'Unspecified';
  }
}

interface SalesPivotTableProps {
  rows: SalesRow[];
  targetSummary?: TargetSummary | null;
  loading?: boolean;
  insights?: import('./types').SalesInsights | null;
  filterParams?: string;
  onClearAllFilters?: () => void;
  onRegisterExport?: (fn: () => Promise<void>) => void;
}

export function SalesPivotTable({
  rows,
  targetSummary,
  loading,
  insights,
  filterParams,
  onClearAllFilters,
  onRegisterExport,
}: SalesPivotTableProps) {
  const [primaryDim, setPrimaryDim] = useState<PivotDimensionKey>('zone');
  const [secondaryDim, setSecondaryDim] = useState<PivotDimensionKey | 'none'>('none');
  const [tertiaryDim, setTertiaryDim] = useState<PivotDimensionKey | 'none'>('none');
  const [columnSplit, setColumnSplit] = useState<PivotColumnSplitKey>('none');
  const [searchTerm, setSearchTerm] = useState('');
  const [sortField, setSortField] = useState<string>('net_sales');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
  const [collapsedGroups, setCollapsedGroups] = useState<Record<string, boolean>>({});

  const [visibleCols, setVisibleCols] = useState<string[]>(['total_sales']);
  const [colsOpen, setColsOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  const handleResetPivot = () => {
    setPrimaryDim('zone');
    setSecondaryDim('none');
    setTertiaryDim('none');
    setColumnSplit('none');
    setSearchTerm('');
    setSortField('net_sales');
    setSortDir('desc');
    setVisibleCols(['total_sales']);
    setCollapsedGroups({});
    onClearAllFilters?.();
  };

  const [serverPivotData, setServerPivotData] = useState<any>(null);
  const [serverLoading, setServerLoading] = useState<boolean>(false);

  useEffect(() => {
    let active = true;
    const fetchPivot = async () => {
      setServerLoading(true);
      try {
        const queryParams = new URLSearchParams(filterParams || '');
        queryParams.set('primary_dim', primaryDim);
        queryParams.set('secondary_dim', secondaryDim);
        queryParams.set('tertiary_dim', tertiaryDim);
        queryParams.set('column_split', columnSplit);
        queryParams.delete('page');
        queryParams.delete('page_size');

        const res = await fetch(`${API_URL}/sales-dashboard/pivot?${queryParams.toString()}`);
        if (!res.ok) throw new Error(`Pivot failed: ${res.status}`);
        const data = await res.json();
        if (active && data && data.ok) {
          setServerPivotData(data);
        }
      } catch (err) {
        console.warn('Server pivot fetch failed, fallback to client:', err);
      } finally {
        if (active) setServerLoading(false);
      }
    };

    fetchPivot();
    return () => {
      active = false;
    };
  }, [filterParams, primaryDim, secondaryDim, tertiaryDim, columnSplit]);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setColsOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const enrichedRows = useMemo(() => {
    return rows.map((r) => {
      const gross = Math.max(0, (r.mrp || 0) * (r.qty || 0));
      const discAmt = r.discount_amount ?? Math.max(0, gross - r.net_sale_amount);
      return {
        ...r,
        derived_gross: gross,
        derived_disc_amt: discAmt,
      };
    });
  }, [rows]);

  // Overall totals for proportional target allocation
  const overallTotals = useMemo(() => {
    if (serverPivotData?.grand_total) {
      const gt = serverPivotData.grand_total;
      const target = targetSummary?.totalTarget ?? (targetSummary as any)?.target ?? 0;
      return {
        sales: gt.total_sales,
        qty: gt.total_qty,
        soh: gt.total_soh,
        target,
        total_mdq: gt.total_mdq,
        ly_sales: gt.ly_sales,
        yoyPct: gt.yoyPct,
      };
    }

    let sales = 0;
    let qty = 0;
    let soh = 0;
    const siteMdqs = new Map<string, number>();
    enrichedRows.forEach((r) => {
      sales += r.net_sale_amount || 0;
      qty += r.qty || 0;
      soh += r.current_soh ?? r.soh ?? 0;
      if (r.site_id && r.mdq != null) {
        siteMdqs.set(String(r.site_id).trim().toUpperCase(), Number(r.mdq));
      }
    });
    const total_mdq = Array.from(siteMdqs.values()).reduce((a, b) => a + b, 0);
    const target = targetSummary?.totalTarget ?? (targetSummary as any)?.target ?? 0;
    return { sales, qty, soh, target, total_mdq };
  }, [serverPivotData, enrichedRows, targetSummary]);

  // Unique column split keys when matrix mode is enabled (e.g. PT, SSL, EBO or Apr, May, Jun...)
  const splitColKeys = useMemo(() => {
    if (columnSplit === 'none') return [];
    if (serverPivotData?.split_col_keys && serverPivotData.split_col_keys.length > 0) {
      return serverPivotData.split_col_keys;
    }
    const keys = new Set<string>();
    enrichedRows.forEach((r) => {
      const val = columnSplit === 'sales_channel' ? deriveSalesChannel(r) : deriveMonthName(r.date);
      if (val) keys.add(val);
    });
    return Array.from(keys).sort();
  }, [serverPivotData, enrichedRows, columnSplit]);

  // Pivot Aggregation
  const pivotGroups = useMemo(() => {
    const totalTarget = overallTotals.target;
    const totalSales = overallTotals.sales;

    // Use server-side aggregated data when available
    if (serverPivotData?.groups && serverPivotData.groups.length > 0) {
      return serverPivotData.groups.map((grp: any) => {
        const targetRatio = totalSales > 0 ? grp.total_sales / totalSales : 0;
        const grpTarget = Math.round(totalTarget * targetRatio);
        const achvPct = grpTarget > 0 ? (grp.total_sales / grpTarget) * 100 : null;

        // Assign targets for split columns
        const splitMetrics: Record<string, { sales: number; qty: number; target: number }> = {};
        if (columnSplit !== 'none') {
          splitColKeys.forEach((colKey) => {
            const cMetric = grp.splitMetrics?.[colKey] || { sales: 0, qty: 0 };
            const colRatio = grp.total_sales > 0 ? cMetric.sales / grp.total_sales : 0;
            splitMetrics[colKey] = {
              sales: cMetric.sales,
              qty: cMetric.qty,
              target: Math.round(grpTarget * colRatio),
            };
          });
        }

        const processedSubGroups = (grp.children || []).map((sub: any) => {
          const subRatio = totalSales > 0 ? sub.total_sales / totalSales : 0;
          const subTarget = Math.round(totalTarget * subRatio);
          const subAchv = subTarget > 0 ? (sub.total_sales / subTarget) * 100 : null;

          const subSplitMetrics: Record<string, { sales: number; qty: number; target: number }> = {};
          if (columnSplit !== 'none') {
            splitColKeys.forEach((colKey) => {
              const cMetric = sub.splitMetrics?.[colKey] || { sales: 0, qty: 0 };
              const colRatio = sub.total_sales > 0 ? cMetric.sales / sub.total_sales : 0;
              subSplitMetrics[colKey] = {
                sales: cMetric.sales,
                qty: cMetric.qty,
                target: Math.round(subTarget * colRatio),
              };
            });
          }

          const processedSubSubGroups = (sub.children || []).map((tSub: any) => {
            const tRatio = totalSales > 0 ? tSub.total_sales / totalSales : 0;
            const tTarget = Math.round(totalTarget * tRatio);
            const tAchv = tTarget > 0 ? (tSub.total_sales / tTarget) * 100 : null;

            return {
              tertiaryVal: tSub.label || tSub.key,
              target: tTarget,
              net_sales: tSub.total_sales,
              ly_sales: tSub.ly_sales ?? 0,
              yoyPct: tSub.yoyPct ?? null,
              qty: tSub.total_qty,
              soh: tSub.total_soh ?? 0,
              gross_sales: tSub.gross_sales,
              discount_amount: tSub.discount_amount,
              mdq: tSub.total_mdq ?? 0,
              achvPct: tAchv,
              asp: tSub.asp ?? 0,
              discPct: tSub.discPct ?? 0,
              splitMetrics: tSub.splitMetrics || {},
            };
          });

          return {
            secondaryVal: sub.label || sub.key,
            target: subTarget,
            net_sales: sub.total_sales,
            ly_sales: sub.ly_sales ?? 0,
            yoyPct: sub.yoyPct ?? null,
            qty: sub.total_qty,
            soh: sub.total_soh ?? 0,
            gross_sales: sub.gross_sales,
            discount_amount: sub.discount_amount,
            mdq: sub.total_mdq ?? 0,
            achvPct: subAchv,
            asp: sub.asp ?? 0,
            discPct: sub.discPct ?? 0,
            splitMetrics: subSplitMetrics,
            subSubGroups: processedSubSubGroups,
          };
        });

        return {
          primaryVal: grp.label || grp.key,
          target: grpTarget,
          total_sales: grp.total_sales,
          total_qty: grp.total_qty,
          total_soh: grp.total_soh ?? 0,
          total_gross: grp.gross_sales,
          total_discount: grp.discount_amount,
          ly_sales: grp.ly_sales ?? 0,
          yoyPct: grp.yoyPct ?? null,
          mdq: grp.total_mdq ?? 0,
          achvPct,
          asp: grp.asp ?? 0,
          discPct: grp.discPct ?? 0,
          splitMetrics,
          subGroups: processedSubGroups,
        };
      });
    }

    // Client-side fallback if server response not available
    const groups: Record<
      string,
      {
        primaryVal: string;
        siteMdqs: Map<string, number>;
        subGroups: Record<
          string,
          {
            secondaryVal: string;
            siteMdqs: Map<string, number>;
            subSubGroups: Record<
              string,
              {
                tertiaryVal: string;
                siteMdqs: Map<string, number>;
                net_sales: number;
                qty: number;
                soh: number;
                gross_sales: number;
                discount_amount: number;
              }
            >;
            net_sales: number;
            qty: number;
            soh: number;
            gross_sales: number;
            discount_amount: number;
            splitMetrics: Record<string, { sales: number; qty: number; target: number }>;
          }
        >;
        total_sales: number;
        total_qty: number;
        total_soh: number;
        total_gross: number;
        total_discount: number;
        splitMetrics: Record<string, { sales: number; qty: number; target: number }>;
      }
    > = {};

    enrichedRows.forEach((r) => {
      const pVal = getDimensionValue(r, primaryDim);
      const sVal = secondaryDim !== 'none' ? getDimensionValue(r, secondaryDim) : 'all';
      const tVal = tertiaryDim !== 'none' ? getDimensionValue(r, tertiaryDim) : 'all';
      const cVal = columnSplit !== 'none' ? (columnSplit === 'sales_channel' ? deriveSalesChannel(r) : deriveMonthName(r.date)) : 'all';

      if (!groups[pVal]) {
        groups[pVal] = {
          primaryVal: pVal,
          siteMdqs: new Map<string, number>(),
          subGroups: {},
          total_sales: 0,
          total_qty: 0,
          total_soh: 0,
          total_gross: 0,
          total_discount: 0,
          splitMetrics: {},
        };
      }

      const pGroup = groups[pVal];
      pGroup.total_sales += r.net_sale_amount || 0;
      pGroup.total_qty += r.qty || 0;
      pGroup.total_soh += r.current_soh ?? r.soh ?? 0;
      pGroup.total_gross += r.derived_gross || 0;
      pGroup.total_discount += r.derived_disc_amt || 0;

      if (!pGroup.splitMetrics[cVal]) {
        pGroup.splitMetrics[cVal] = { sales: 0, qty: 0, target: 0 };
      }
      pGroup.splitMetrics[cVal].sales += r.net_sale_amount || 0;
      pGroup.splitMetrics[cVal].qty += r.qty || 0;

      if (!pGroup.subGroups[sVal]) {
        pGroup.subGroups[sVal] = {
          secondaryVal: sVal,
          siteMdqs: new Map<string, number>(),
          subSubGroups: {},
          net_sales: 0,
          qty: 0,
          soh: 0,
          gross_sales: 0,
          discount_amount: 0,
          splitMetrics: {},
        };
      }

      const sGroup = pGroup.subGroups[sVal];
      sGroup.net_sales += r.net_sale_amount || 0;
      sGroup.qty += r.qty || 0;
      sGroup.soh += r.current_soh ?? r.soh ?? 0;
      sGroup.gross_sales += r.derived_gross || 0;
      sGroup.discount_amount += r.derived_disc_amt || 0;

      if (!sGroup.splitMetrics[cVal]) {
        sGroup.splitMetrics[cVal] = { sales: 0, qty: 0, target: 0 };
      }
      sGroup.splitMetrics[cVal].sales += r.net_sale_amount || 0;
      sGroup.splitMetrics[cVal].qty += r.qty || 0;

      if (r.site_id && r.mdq != null) {
        const sid = String(r.site_id).trim().toUpperCase();
        pGroup.siteMdqs.set(sid, Number(r.mdq));
        sGroup.siteMdqs.set(sid, Number(r.mdq));
      }

      if (tertiaryDim !== 'none') {
        if (!sGroup.subSubGroups[tVal]) {
          sGroup.subSubGroups[tVal] = {
            tertiaryVal: tVal,
            siteMdqs: new Map<string, number>(),
            net_sales: 0,
            qty: 0,
            soh: 0,
            gross_sales: 0,
            discount_amount: 0,
          };
        }
        const tGroup = sGroup.subSubGroups[tVal];
        tGroup.net_sales += r.net_sale_amount || 0;
        tGroup.qty += r.qty || 0;
        tGroup.soh += r.current_soh ?? r.soh ?? 0;
        tGroup.gross_sales += r.derived_gross || 0;
        tGroup.discount_amount += r.derived_disc_amt || 0;

        if (r.site_id && r.mdq != null) {
          const sid = String(r.site_id).trim().toUpperCase();
          tGroup.siteMdqs.set(sid, Number(r.mdq));
        }
      }
    });

    return Object.values(groups).map((grp) => {
      const targetRatio = totalSales > 0 ? grp.total_sales / totalSales : 0;
      const grpTarget = Math.round(totalTarget * targetRatio);
      const achvPct = grpTarget > 0 ? (grp.total_sales / grpTarget) * 100 : null;
      const asp = grp.total_qty > 0 ? grp.total_sales / grp.total_qty : 0;
      const discPct = grp.total_gross > 0 ? (grp.total_discount / grp.total_gross) * 100 : 0;

      splitColKeys.forEach((colKey) => {
        const cMetric = grp.splitMetrics[colKey];
        if (cMetric) {
          const colRatio = grp.total_sales > 0 ? cMetric.sales / grp.total_sales : 0;
          cMetric.target = Math.round(grpTarget * colRatio);
        }
      });

      const processedSubGroups = Object.values(grp.subGroups).map((sub) => {
        const subRatio = totalSales > 0 ? sub.net_sales / totalSales : 0;
        const subTarget = Math.round(totalTarget * subRatio);
        const subAchv = subTarget > 0 ? (sub.net_sales / subTarget) * 100 : null;
        const subAsp = sub.qty > 0 ? sub.net_sales / sub.qty : 0;
        const subDisc = sub.gross_sales > 0 ? (sub.discount_amount / sub.gross_sales) * 100 : 0;
        const subMdq = Array.from(sub.siteMdqs.values()).reduce((a, b) => a + b, 0);
        const subLy = insights?.ly_metrics?.[secondaryDim as string]?.[String(sub.secondaryVal)] ?? 0;
        const subYoyPct = subLy > 0 ? ((sub.net_sales - subLy) / subLy) * 100 : null;

        const processedSubSubGroups = Object.values(sub.subSubGroups).map((tSub) => {
          const tRatio = totalSales > 0 ? tSub.net_sales / totalSales : 0;
          const tTarget = Math.round(totalTarget * tRatio);
          const tAchv = tTarget > 0 ? (tSub.net_sales / tTarget) * 100 : null;
          const tAsp = tSub.qty > 0 ? tSub.net_sales / tSub.qty : 0;
          const tDisc = tSub.gross_sales > 0 ? (tSub.discount_amount / tSub.gross_sales) * 100 : 0;
          const tSubMdq = Array.from(tSub.siteMdqs.values()).reduce((a, b) => a + b, 0);
          const tSubLy = insights?.ly_metrics?.[tertiaryDim as string]?.[String(tSub.tertiaryVal)] ?? 0;
          const tSubYoyPct = tSubLy > 0 ? ((tSub.net_sales - tSubLy) / tSubLy) * 100 : null;
          return {
            ...tSub,
            target: tTarget,
            achvPct: tAchv,
            asp: tAsp,
            discPct: tDisc,
            mdq: tSubMdq,
            ly_sales: tSubLy,
            yoyPct: tSubYoyPct,
          };
        });

        return {
          ...sub,
          target: subTarget,
          achvPct: subAchv,
          asp: subAsp,
          discPct: subDisc,
          mdq: subMdq,
          ly_sales: subLy,
          yoyPct: subYoyPct,
          subSubGroups: processedSubSubGroups,
        };
      });

      const pValStr = String(grp.primaryVal);
      const ly_sales = insights?.ly_metrics?.[primaryDim]?.[pValStr] ?? 0;
      const yoyPct = ly_sales > 0 ? ((grp.total_sales - ly_sales) / ly_sales) * 100 : null;
      const grpMdq = Array.from(grp.siteMdqs.values()).reduce((a, b) => a + b, 0);

      return {
        ...grp,
        target: grpTarget,
        achvPct,
        asp,
        discPct,
        ly_sales,
        yoyPct,
        mdq: grpMdq,
        subGroups: processedSubGroups,
      };
    });
  }, [serverPivotData, enrichedRows, primaryDim, secondaryDim, tertiaryDim, columnSplit, splitColKeys, overallTotals, insights]);

  // Filter & Sort
  const filteredAndSortedGroups = useMemo(() => {
    let list = pivotGroups;
    if (searchTerm.trim()) {
      const q = searchTerm.toLowerCase();
      list = list.filter(
        (g) =>
          g.primaryVal.toLowerCase().includes(q) ||
          g.subGroups.some((s) => s.secondaryVal.toLowerCase().includes(q))
      );
    }

    return [...list].sort((a, b) => {
      let av: any = (a as any)[sortField];
      let bv: any = (b as any)[sortField];

      if (sortField === 'primaryVal') {
        av = a.primaryVal;
        bv = b.primaryVal;
      }

      if (typeof av === 'number' && typeof bv === 'number') {
        return sortDir === 'asc' ? av - bv : bv - av;
      }
      return sortDir === 'asc' ? String(av).localeCompare(String(bv)) : String(bv).localeCompare(String(av));
    });
  }, [pivotGroups, searchTerm, sortField, sortDir]);

  const handleSort = (field: string) => {
    if (sortField === field) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortField(field);
      setSortDir('desc');
    }
  };

  const toggleGroupCollapse = (key: string) => {
    setCollapsedGroups((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  const [isExportingPivot, setIsExportingPivot] = useState(false);

  // Helper for safe blob download
  const safeDownloadBlob = (blob: Blob, filename: string) => {
    const cleanFilename = (filename || `Sales_Pivot_${Date.now()}.xlsx`)
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

  // Export Pivot Table to Excel
  const exportPivotToExcel = async () => {
    try {
      setIsExportingPivot(true);
      const workbook = new ExcelJS.Workbook();
      workbook.creator = 'MeridianAI StockPulse';
      workbook.created = new Date();
      const sheet = workbook.addWorksheet('Pivot Summary', { views: [{ showGridLines: true }] });

      const dimHeaders = [
        DIMENSION_OPTIONS.find((d) => d.key === primaryDim)?.label || 'Primary',
        ...(secondaryDim !== 'none'
          ? [SECONDARY_DIMENSION_OPTIONS.find((d) => d.key === secondaryDim)?.label || 'Secondary']
          : []),
        ...(tertiaryDim !== 'none'
          ? [TERTIARY_DIMENSION_OPTIONS.find((d) => d.key === tertiaryDim)?.label || 'Tertiary']
          : []),
      ];

      const activeMetricCols = PIVOT_METRIC_COLUMNS.filter((c) => visibleCols.includes(c.key));

      const headers = [...dimHeaders];

      if (columnSplit !== 'none') {
        splitColKeys.forEach((col) => {
          headers.push(`${col} Target`, `${col} Sales (GSV)`, `${col} Achv%`);
        });
        headers.push('Total Target', 'Total Sales (GSV)', 'Total Achv%');
      } else {
        activeMetricCols.forEach((col) => headers.push(col.label));
      }

      const headerRow = sheet.addRow(headers);
      headerRow.height = 26;
      headerRow.eachCell((cell) => {
        cell.font = { bold: true, color: { argb: 'FFFFFFFF' }, size: 10, name: 'Calibri' };
        cell.alignment = { vertical: 'middle', horizontal: 'center' };
        cell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FF1E293B' } };
        cell.border = { bottom: { style: 'medium', color: { argb: 'FF0F172A' } } };
      });

      const buildMetricValues = (metricObj: any) => {
        return activeMetricCols.map((c) => {
          if (c.key === 'target') return metricObj.target || 0;
          if (c.key === 'total_sales') return metricObj.total_sales ?? metricObj.net_sales ?? 0;
          if (c.key === 'ly_sales') return metricObj.ly_sales || 0;
          if (c.key === 'yoyPct') return metricObj.yoyPct != null ? `${metricObj.yoyPct.toFixed(1)}%` : '—';
          if (c.key === 'total_qty') return metricObj.total_qty ?? metricObj.qty ?? 0;
          if (c.key === 'total_soh') return metricObj.total_soh ?? metricObj.soh ?? 0;
          if (c.key === 'mdq') return metricObj.mdq || 0;
          if (c.key === 'achvPct') return metricObj.achvPct != null ? `${metricObj.achvPct.toFixed(1)}%` : '—';
          if (c.key === 'asp') return Math.round(metricObj.asp || 0);
          if (c.key === 'discPct') return metricObj.discPct != null ? `${metricObj.discPct.toFixed(1)}%` : '—';
          return '';
        });
      };

      filteredAndSortedGroups.forEach((grp) => {
        if (columnSplit !== 'none') {
          const rowVal = [grp.primaryVal];
          if (secondaryDim !== 'none') rowVal.push('');
          if (tertiaryDim !== 'none') rowVal.push('');

          splitColKeys.forEach((col) => {
            const m = grp.splitMetrics[col] || { sales: 0, target: 0 };
            const achv = m.target > 0 ? (m.sales / m.target) * 100 : 0;
            rowVal.push(m.target, m.sales, `${achv.toFixed(1)}%`);
          });
          const totalAchv = grp.target > 0 ? (grp.total_sales / grp.target) * 100 : 0;
          rowVal.push(grp.target, grp.total_sales, `${totalAchv.toFixed(1)}%`);
          const r = sheet.addRow(rowVal);
          r.height = 20;
        } else {
          if (secondaryDim !== 'none') {
            grp.subGroups.forEach((sub) => {
              if (tertiaryDim !== 'none' && sub.subSubGroups.length > 0) {
                sub.subSubGroups.forEach((tSub) => {
                  const r = sheet.addRow([
                    grp.primaryVal,
                    sub.secondaryVal,
                    tSub.tertiaryVal,
                    ...buildMetricValues(tSub),
                  ]);
                  r.height = 20;
                });
                const subTot = sheet.addRow([
                  grp.primaryVal,
                  `${sub.secondaryVal} Total`,
                  '',
                  ...buildMetricValues(sub),
                ]);
                subTot.height = 21;
                subTot.font = { bold: true };
                subTot.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FFF1F5F9' } };
              } else {
                const r = sheet.addRow([
                  grp.primaryVal,
                  sub.secondaryVal,
                  ...(tertiaryDim !== 'none' ? [''] : []),
                  ...buildMetricValues(sub),
                ]);
                r.height = 20;
              }
            });

            const totalRow = sheet.addRow([
              `${grp.primaryVal} Total`,
              '',
              ...(tertiaryDim !== 'none' ? [''] : []),
              ...buildMetricValues(grp),
            ]);
            totalRow.height = 22;
            totalRow.font = { bold: true };
            totalRow.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FFE2E8F0' } };
          } else {
            const r = sheet.addRow([
              grp.primaryVal,
              ...buildMetricValues(grp),
            ]);
            r.height = 20;
          }
        }
      });

      // Grand Total Row
      const grandTotalValues: (string | number)[] = [
        'Grand Total',
        ...(secondaryDim !== 'none' ? [''] : []),
        ...(tertiaryDim !== 'none' ? [''] : []),
      ];

      if (columnSplit !== 'none') {
        splitColKeys.forEach((col) => {
          let colTarget = 0;
          let colSales = 0;
          pivotGroups.forEach((g) => {
            const m = g.splitMetrics[col];
            if (m) {
              colTarget += m.target;
              colSales += m.sales;
            }
          });
          const colAchv = colTarget > 0 ? (colSales / colTarget) * 100 : 0;
          grandTotalValues.push(colTarget, colSales, `${colAchv.toFixed(1)}%`);
        });
        const gtAchv = overallTotals.target > 0 ? (overallTotals.sales / overallTotals.target) * 100 : 0;
        grandTotalValues.push(overallTotals.target, overallTotals.sales, `${gtAchv.toFixed(1)}%`);
      } else {
        const grandMetricObj = {
          target: overallTotals.target,
          total_sales: overallTotals.sales,
          ly_sales: typeof insights?.ly_metrics?.total === 'number' ? insights.ly_metrics.total : 0,
          yoyPct: (typeof insights?.ly_metrics?.total === 'number' && insights.ly_metrics.total > 0)
            ? ((overallTotals.sales - insights.ly_metrics.total) / insights.ly_metrics.total) * 100
            : null,
          total_qty: overallTotals.qty,
          total_soh: overallTotals.soh,
          mdq: overallTotals.total_mdq || 0,
          achvPct: overallTotals.target > 0 ? (overallTotals.sales / overallTotals.target) * 100 : 0,
          asp: overallTotals.qty > 0 ? overallTotals.sales / overallTotals.qty : 0,
          discPct: 0,
        };
        grandTotalValues.push(...buildMetricValues(grandMetricObj));
      }

      const grandRow = sheet.addRow(grandTotalValues);
      grandRow.height = 24;
      grandRow.font = { bold: true, color: { argb: 'FF0F172A' }, size: 11 };
      grandRow.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FFCBD5E1' } };

      // Set auto column widths and number formats
      sheet.columns.forEach((col, idx) => {
        let maxLen = 14;
        col.eachCell?.({ includeEmpty: false }, (cell) => {
          const valStr = cell.value ? String(cell.value) : '';
          maxLen = Math.max(maxLen, Math.min(45, valStr.length + 3));
          if (idx >= dimHeaders.length && typeof cell.value === 'number') {
            cell.numFmt = '#,##0.00';
          }
        });
        col.width = maxLen;
      });

      const buffer = await workbook.xlsx.writeBuffer();
      const blob = new Blob([buffer], {
        type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      });
      safeDownloadBlob(blob, `Sales_Pivot_${primaryDim}_${new Date().toISOString().substring(0, 10)}.xlsx`);
    } catch (err) {
      console.error('Failed to export pivot table:', err);
      alert('Export failed. Please check browser console.');
    } finally {
      setIsExportingPivot(false);
    }
  };

  useEffect(() => {
    if (onRegisterExport) {
      onRegisterExport(exportPivotToExcel);
    }
  }, [onRegisterExport, exportPivotToExcel]);

  const primaryLabel = DIMENSION_OPTIONS.find((d) => d.key === primaryDim)?.label || 'Primary';
  const secondaryLabel = SECONDARY_DIMENSION_OPTIONS.find((d) => d.key === secondaryDim)?.label || '';
  const tertiaryLabel = TERTIARY_DIMENSION_OPTIONS.find((d) => d.key === tertiaryDim)?.label || '';

  return (
    <div className="space-y-4">
      {/* Pivot Controls Panel (Excel-style) */}
      <div className="flex flex-col gap-3 rounded-2xl border border-slate-200/80 bg-slate-50/70 p-4 dark:border-slate-800 dark:bg-slate-900/60">
        {/* Presets Quick-Select */}
        <div className="flex flex-wrap items-center gap-2 border-b border-slate-200/60 pb-3 dark:border-slate-800">
          <span className="text-[11px] font-bold text-slate-500 uppercase tracking-wider dark:text-slate-400">
            Multi-Group Presets:
          </span>
          {MULTI_GROUP_PRESETS.map((p) => {
            const isActive = primaryDim === p.primary && secondaryDim === p.secondary && tertiaryDim === p.tertiary;
            return (
              <button
                key={p.label}
                onClick={() => {
                  setPrimaryDim(p.primary);
                  setSecondaryDim(p.secondary);
                  setTertiaryDim(p.tertiary);
                }}
                className={`rounded-lg px-2.5 py-1 text-xs font-semibold transition ${
                  isActive
                    ? 'bg-blue-600 text-white shadow-sm'
                    : 'bg-white text-slate-700 hover:bg-slate-200/70 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700'
                }`}
              >
                {p.label}
              </button>
            );
          })}
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-3">
            {/* Primary Group By Dropdown */}
            <div className="flex items-center gap-2">
              <span className="text-xs font-semibold text-slate-500 dark:text-slate-400 flex items-center gap-1">
                <Layers className="h-3.5 w-3.5 text-blue-600 dark:text-blue-400" />
                Level 1:
              </span>
              <select
                value={primaryDim}
                onChange={(e) => setPrimaryDim(e.target.value as PivotDimensionKey)}
                className="rounded-xl border border-slate-200 bg-white px-3 py-1.5 text-xs font-bold text-slate-800 shadow-sm focus:border-blue-500 focus:outline-none dark:border-slate-700 dark:bg-slate-800 dark:text-white"
              >
                {DIMENSION_OPTIONS.map((d) => (
                  <option key={d.key} value={d.key}>
                    {d.label}
                  </option>
                ))}
              </select>
            </div>

            {/* Secondary Group By (Sub-rows) */}
            <div className="flex items-center gap-2">
              <span className="text-xs font-semibold text-slate-500 dark:text-slate-400">Level 2:</span>
              <select
                value={secondaryDim}
                onChange={(e) => setSecondaryDim(e.target.value as any)}
                className="rounded-xl border border-slate-200 bg-white px-3 py-1.5 text-xs font-bold text-slate-800 shadow-sm focus:border-blue-500 focus:outline-none dark:border-slate-700 dark:bg-slate-800 dark:text-white"
              >
                {SECONDARY_DIMENSION_OPTIONS.map((d) => (
                  <option key={d.key} value={d.key}>
                    {d.label}
                  </option>
                ))}
              </select>
            </div>

            {/* Tertiary Group By (Sub-sub-rows) */}
            <div className="flex items-center gap-2">
              <span className="text-xs font-semibold text-slate-500 dark:text-slate-400">Level 3:</span>
              <select
                value={tertiaryDim}
                onChange={(e) => setTertiaryDim(e.target.value as any)}
                className="rounded-xl border border-slate-200 bg-white px-3 py-1.5 text-xs font-bold text-slate-800 shadow-sm focus:border-blue-500 focus:outline-none dark:border-slate-700 dark:bg-slate-800 dark:text-white"
              >
                {TERTIARY_DIMENSION_OPTIONS.map((d) => (
                  <option key={d.key} value={d.key}>
                    {d.label}
                  </option>
                ))}
              </select>
            </div>

            {/* Column Split (Matrix View) */}
            <div className="flex items-center gap-2">
              <span className="text-xs font-semibold text-slate-500 dark:text-slate-400">Split:</span>
              <select
                value={columnSplit}
                onChange={(e) => setColumnSplit(e.target.value as PivotColumnSplitKey)}
                className="rounded-xl border border-slate-200 bg-white px-3 py-1.5 text-xs font-bold text-slate-800 shadow-sm focus:border-blue-500 focus:outline-none dark:border-slate-700 dark:bg-slate-800 dark:text-white"
              >
                <option value="none">None (Standard Table)</option>
                <option value="sales_channel">By Channel/Format (PT, SSL...)</option>
                <option value="month">By Month (Apr, May, Jun...)</option>
              </select>
            </div>
          </div>

        <div className="flex items-center gap-2">
          {/* Clear Filters */}
          <button
            type="button"
            onClick={handleResetPivot}
            className="inline-flex items-center gap-1.5 rounded-xl border border-rose-200 bg-rose-50 px-3 py-1.5 text-xs font-semibold text-rose-700 shadow-sm hover:bg-rose-100 hover:text-rose-800 dark:border-rose-900/50 dark:bg-rose-950/40 dark:text-rose-300 dark:hover:bg-rose-900/60 transition"
            title="Clear all dashboard filters, search, and reset pivot table"
          >
            <RotateCcw className="h-3.5 w-3.5 text-rose-600 dark:text-rose-400" />
            Clear Filters
          </button>

          {/* Search */}
          <div className="relative flex-1 lg:w-56">
            <Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
            <input
              type="text"
              placeholder="Search pivot data…"
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="w-full rounded-xl border border-slate-200 bg-white pl-8 pr-3 py-1.5 text-xs text-slate-800 placeholder-slate-400 shadow-sm focus:border-blue-500 focus:outline-none dark:border-slate-700 dark:bg-slate-800 dark:text-white"
            />
          </div>

          {/* Columns Dropdown */}
          <div className="relative" ref={dropdownRef}>
            <button
              onClick={() => setColsOpen(!colsOpen)}
              className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-3 py-1.5 text-xs font-bold text-slate-700 shadow-sm hover:bg-slate-50 hover:text-blue-600 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700"
            >
              Columns <ChevronDown className="h-3.5 w-3.5" />
            </button>
            {colsOpen && (
              <div className="absolute right-0 top-full z-50 mt-1 max-h-72 w-56 overflow-y-auto rounded-xl border border-slate-200 bg-white p-2 shadow-xl dark:border-slate-700 dark:bg-slate-900">
                <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-slate-100 pb-1.5 px-1 text-[11px] font-semibold text-blue-600 dark:border-slate-800 dark:text-blue-400">
                  <button
                    type="button"
                    onClick={() => setVisibleCols(PIVOT_METRIC_COLUMNS.map((c) => c.key))}
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
                    onClick={() => setVisibleCols(['total_sales'])}
                    className="ml-auto hover:underline"
                  >
                    Reset default
                  </button>
                </div>
                {PIVOT_METRIC_COLUMNS.map((col) => (
                  <label
                    key={col.key}
                    className="flex cursor-pointer items-center gap-2 rounded px-2 py-1 text-xs hover:bg-slate-50 dark:hover:bg-slate-800"
                  >
                    <input
                      type="checkbox"
                      checked={visibleCols.includes(col.key)}
                      onChange={(e) => {
                        if (e.target.checked) {
                          setVisibleCols((prev) => [...prev, col.key]);
                        } else {
                          setVisibleCols((prev) => prev.filter((k) => k !== col.key));
                        }
                      }}
                      className="rounded border-slate-300 text-blue-600 focus:ring-blue-500"
                    />
                    <span className="font-medium text-slate-700 dark:text-slate-300">
                      {col.label}
                    </span>
                  </label>
                ))}
              </div>
            )}
          </div>

          {/* Export Excel Button */}
          <button
            type="button"
            disabled={isExportingPivot}
            onClick={exportPivotToExcel}
            className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-3 py-1.5 text-xs font-bold text-slate-700 shadow-sm hover:bg-slate-50 hover:text-blue-600 disabled:opacity-60 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700 transition"
            title="Export formatted Pivot Summary to Excel"
          >
            {isExportingPivot ? (
              <>
                <Loader2 className="h-3.5 w-3.5 animate-spin text-emerald-600" />
                <span>Exporting...</span>
              </>
            ) : (
              <>
                <Download className="h-3.5 w-3.5 text-emerald-600" />
                <span>Export Pivot</span>
              </>
            )}
          </button>
        </div>
      </div>
    </div>

      {/* Pivot Table Rendering */}
      <div className="relative max-h-[580px] overflow-auto rounded-2xl border border-slate-200/80 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900/60">
        {serverLoading && (
          <div className="absolute inset-0 z-30 flex items-center justify-center bg-white/60 backdrop-blur-[1px] dark:bg-slate-900/60">
            <div className="flex items-center gap-2 rounded-xl bg-white px-4 py-2 text-xs font-bold text-blue-600 shadow-md border border-slate-200 dark:bg-slate-800 dark:border-slate-700 dark:text-blue-400">
              <div className="h-4 w-4 animate-spin rounded-full border-2 border-blue-600 border-t-transparent dark:border-blue-400 dark:border-t-transparent" />
              <span>Calculating server-side aggregation...</span>
            </div>
          </div>
        )}
        <table className="w-full text-left text-xs text-slate-700 dark:text-slate-200">
          <thead className="sticky top-0 z-10 bg-slate-100/95 text-[11px] font-bold uppercase tracking-wider text-slate-600 backdrop-blur-sm shadow-sm dark:bg-slate-800/95 dark:text-slate-300">
            {columnSplit !== 'none' ? (
              /* Matrix Pivot Header (Image 2 & 4 style) */
              <>
                <tr>
                  <th rowSpan={2} className="px-4 py-3 border-r border-slate-200 dark:border-slate-700">
                    <button
                      onClick={() => handleSort('primaryVal')}
                      className="inline-flex items-center gap-1 hover:text-blue-600"
                    >
                      {primaryLabel}
                      {sortField === 'primaryVal' && (sortDir === 'asc' ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />)}
                    </button>
                  </th>
                  {splitColKeys.map((col) => (
                    <th key={col} colSpan={3} className="px-3 py-2 text-center border-r border-b border-slate-200 dark:border-slate-700 bg-blue-50/50 dark:bg-blue-950/30 text-blue-800 dark:text-blue-300">
                      {col}
                    </th>
                  ))}
                  <th colSpan={3} className="px-3 py-2 text-center border-b border-slate-200 dark:border-slate-700 bg-slate-200/60 dark:bg-slate-700/60 text-slate-900 dark:text-white">
                    Grand Total
                  </th>
                </tr>
                <tr>
                  {splitColKeys.map((col) => (
                    <React.Fragment key={col}>
                      <th className="px-3 py-2 text-right border-r border-slate-200 dark:border-slate-700 font-semibold">Target</th>
                      <th className="px-3 py-2 text-right border-r border-slate-200 dark:border-slate-700 font-semibold">Sales (GSV)</th>
                      <th className="px-3 py-2 text-right border-r border-slate-200 dark:border-slate-700 font-semibold">Achv %</th>
                    </React.Fragment>
                  ))}
                  <th className="px-3 py-2 text-right border-r border-slate-200 dark:border-slate-700 font-bold">Total Target</th>
                  <th className="px-3 py-2 text-right border-r border-slate-200 dark:border-slate-700 font-bold">Total GSV</th>
                  <th className="px-3 py-2 text-right font-bold">Achv %</th>
                </tr>
              </>
            ) : (
              /* Flat / Hierarchical Summary Header (Image 3 & 5 style) */
              <tr>
                <th className="px-4 py-3 border-r border-slate-200 dark:border-slate-700">
                  <button onClick={() => handleSort('primaryVal')} className="inline-flex items-center gap-1 hover:text-blue-600">
                    {primaryLabel}
                    {sortField === 'primaryVal' && (sortDir === 'asc' ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />)}
                  </button>
                </th>
                {secondaryDim !== 'none' && (
                  <th className="px-4 py-3 border-r border-slate-200 dark:border-slate-700">
                    {secondaryLabel}
                  </th>
                )}
                {tertiaryDim !== 'none' && (
                  <th className="px-4 py-3 border-r border-slate-200 dark:border-slate-700">
                    {tertiaryLabel}
                  </th>
                )}
                {visibleCols.includes("target") && (<th className="px-4 py-3 text-right">
                  <button onClick={() => handleSort('target')} className="inline-flex items-center gap-1 hover:text-blue-600">
                    Target (LFS)
                    {sortField === 'target' && (sortDir === 'asc' ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />)}
                  </button>
                </th>)}
                {visibleCols.includes("total_sales") && (<th className="px-4 py-3 text-right">
                  <button onClick={() => handleSort('total_sales')} className="inline-flex items-center gap-1 hover:text-blue-600">
                    Actual Sales (GSV)
                    {sortField === 'total_sales' && (sortDir === 'asc' ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />)}
                  </button>
                </th>)}
                {visibleCols.includes("ly_sales") && (<th className="px-4 py-3 text-right bg-blue-50/30 dark:bg-blue-900/10">
                  <button onClick={() => handleSort('ly_sales')} className="inline-flex items-center gap-1 text-blue-700 dark:text-blue-300 hover:text-blue-800">
                    LY Sales
                    {sortField === 'ly_sales' && (sortDir === 'asc' ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />)}
                  </button>
                </th>)}
                {visibleCols.includes("yoyPct") && (<th className="px-4 py-3 text-right bg-blue-50/20 dark:bg-blue-900/10">
                  <button onClick={() => handleSort('yoyPct')} className="inline-flex items-center gap-1 text-slate-700 dark:text-slate-300 hover:text-blue-600">
                    Sales YoY %
                    {sortField === 'yoyPct' && (sortDir === 'asc' ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />)}
                  </button>
                </th>)}
                {visibleCols.includes("total_qty") && (<th className="px-4 py-3 text-right">
                  <button onClick={() => handleSort('total_qty')} className="inline-flex items-center gap-1 hover:text-blue-600">
                    Qty Sold
                    {sortField === 'total_qty' && (sortDir === 'asc' ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />)}
                  </button>
                </th>)}
                {visibleCols.includes("total_soh") && (<th className="px-4 py-3 text-right">
                  <button onClick={() => handleSort('total_soh')} className="inline-flex items-center gap-1 hover:text-blue-600">
                    Current SOH
                    {sortField === 'total_soh' && (sortDir === 'asc' ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />)}
                  </button>
                </th>)}
                {visibleCols.includes("mdq") && (<th className="px-4 py-3 text-right">
                  <button onClick={() => handleSort('mdq')} className="inline-flex items-center gap-1 hover:text-blue-600">
                    MDQ
                    {sortField === 'mdq' && (sortDir === 'asc' ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />)}
                  </button>
                </th>)}
                {visibleCols.includes("achvPct") && (<th className="px-4 py-3 text-right">
                  <button onClick={() => handleSort('achvPct')} className="inline-flex items-center gap-1 hover:text-blue-600">
                    Achv %
                    {sortField === 'achvPct' && (sortDir === 'asc' ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />)}
                  </button>
                </th>)}
                {visibleCols.includes("asp") && (<th className="px-4 py-3 text-right">
                  <button onClick={() => handleSort('asp')} className="inline-flex items-center gap-1 hover:text-blue-600">
                    ASP
                    {sortField === 'asp' && (sortDir === 'asc' ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />)}
                  </button>
                </th>)}
                {visibleCols.includes("discPct") && (<th className="px-4 py-3 text-right">
                  <button onClick={() => handleSort('discPct')} className="inline-flex items-center gap-1 hover:text-blue-600">
                    Discount %
                    {sortField === 'discPct' && (sortDir === 'asc' ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />)}
                  </button>
                </th>)}
              </tr>
            )}
          </thead>

          <tbody className="divide-y divide-slate-100 dark:divide-slate-800/60 font-medium">
            {filteredAndSortedGroups.map((grp, idx) => {
              const isCollapsed = collapsedGroups[grp.primaryVal];
              const achvVal = grp.achvPct;

              let achvBadgeClass = 'bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300';
              if (achvVal != null) {
                if (achvVal >= 100) achvBadgeClass = 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/80 dark:text-emerald-300 font-bold';
                else if (achvVal >= 85) achvBadgeClass = 'bg-blue-100 text-blue-800 dark:bg-blue-950/80 dark:text-blue-300';
                else if (achvVal >= 70) achvBadgeClass = 'bg-amber-100 text-amber-800 dark:bg-amber-950/80 dark:text-amber-300';
                else achvBadgeClass = 'bg-rose-100 text-rose-800 dark:bg-rose-950/80 dark:text-rose-300';
              }

              if (columnSplit !== 'none') {
                /* Matrix Row Rendering */
                return (
                  <tr key={grp.primaryVal} className="hover:bg-slate-50/80 dark:hover:bg-slate-800/50">
                    <td className="px-4 py-3 font-semibold text-slate-900 dark:text-white border-r border-slate-200 dark:border-slate-700">
                      {grp.primaryVal}
                    </td>
                    {splitColKeys.map((col) => {
                      const m = grp.splitMetrics[col] || { sales: 0, target: 0 };
                      const cAchv = m.target > 0 ? (m.sales / m.target) * 100 : null;
                      return (
                        <React.Fragment key={col}>
                          <td className="px-3 py-3 text-right font-mono text-slate-500 border-r border-slate-100 dark:border-slate-800">
                            {formatInr(m.target)}
                          </td>
                          <td className="px-3 py-3 text-right font-bold text-slate-800 dark:text-slate-100 border-r border-slate-100 dark:border-slate-800">
                            {formatInr(m.sales)}
                          </td>
                          <td className="px-3 py-3 text-right border-r border-slate-200 dark:border-slate-700">
                            {cAchv != null ? (
                              <span className={`inline-block rounded-md px-2 py-0.5 text-[11px] ${cAchv >= 100 ? 'bg-emerald-100 text-emerald-800 font-bold' : cAchv >= 85 ? 'bg-blue-100 text-blue-800' : 'bg-amber-100 text-amber-800'}`}>
                                {cAchv.toFixed(0)}%
                              </span>
                            ) : (
                              '—'
                            )}
                          </td>
                        </React.Fragment>
                      );
                    })}
                    <td className="px-3 py-3 text-right font-bold text-slate-700 dark:text-slate-300 border-r border-slate-200 dark:border-slate-700 bg-slate-50/50 dark:bg-slate-800/40">
                      {formatInr(grp.target)}
                    </td>
                    <td className="px-3 py-3 text-right font-extrabold text-blue-700 dark:text-blue-400 border-r border-slate-200 dark:border-slate-700 bg-slate-50/50 dark:bg-slate-800/40">
                      {formatInr(grp.total_sales)}
                    </td>
                    <td className="px-3 py-3 text-right bg-slate-50/50 dark:bg-slate-800/40">
                      {achvVal != null ? (
                        <span className={`inline-block rounded-md px-2 py-0.5 text-[11px] ${achvBadgeClass}`}>
                          {achvVal.toFixed(0)}%
                        </span>
                      ) : (
                        '—'
                      )}
                    </td>
                  </tr>
                );
              }

              /* Sub-rows Grouping View (Image 5 style) */
              if (secondaryDim !== 'none') {
                return (
                  <React.Fragment key={grp.primaryVal}>
                    {/* Header Row for Parent Group */}
                    <tr
                      onClick={() => toggleGroupCollapse(grp.primaryVal)}
                      className="cursor-pointer bg-slate-100/90 hover:bg-slate-200/90 dark:bg-slate-800/80 dark:hover:bg-slate-700/80 border-t-2 border-slate-200 dark:border-slate-700"
                    >
                      <td colSpan={(tertiaryDim !== 'none' ? 3 : 2) + visibleCols.length} className="px-4 py-2.5 font-bold text-slate-900 dark:text-white">
                        <div className="flex items-center gap-2">
                          {isCollapsed ? <ChevronRight className="h-4 w-4 text-slate-500" /> : <ChevronDown className="h-4 w-4 text-slate-500" />}
                          <span className="text-sm font-extrabold">{grp.primaryVal}</span>
                          <span className="text-[11px] font-semibold text-slate-500 dark:text-slate-400 bg-slate-200/70 dark:bg-slate-700/70 px-2 py-0.5 rounded-full">
                            {grp.subGroups.length} items
                          </span>
                        </div>
                      </td>
                    </tr>

                    {/* Sub-rows */}
                    {!isCollapsed &&
                      grp.subGroups.map((sub) => {
                        const sAchv = sub.achvPct;
                        if (tertiaryDim !== 'none' && sub.subSubGroups.length > 0) {
                          return (
                            <React.Fragment key={sub.secondaryVal}>
                              {/* Clean Sub-Category Header (No numbers cluttering the title row) */}
                              <tr className="bg-slate-50/80 dark:bg-slate-800/40 font-semibold border-t border-slate-200/60 dark:border-slate-700/60">
                                <td className="px-4 py-2 pl-6 text-slate-400 border-r border-slate-100 dark:border-slate-800">
                                  ↳
                                </td>
                                <td colSpan={2 + visibleCols.length} className="px-4 py-2 text-slate-800 dark:text-slate-200">
                                  <span className="font-bold text-slate-900 dark:text-white">{sub.secondaryVal}</span>
                                  <span className="ml-2 text-[11px] font-medium text-slate-500 dark:text-slate-400">
                                    ({sub.subSubGroups.length} sub-items)
                                  </span>
                                </td>
                              </tr>

                              {/* Level 3 items */}
                              {sub.subSubGroups.map((tSub) => {
                                const tAchv = tSub.achvPct;
                                return (
                                  <tr key={tSub.tertiaryVal} className="hover:bg-slate-50/60 dark:hover:bg-slate-800/30">
                                    <td className="px-4 py-1.5 pl-6 text-slate-300 border-r border-slate-100 dark:border-slate-800">
                                      —
                                    </td>
                                    <td className="px-4 py-1.5 pl-10 text-slate-400 border-r border-slate-100 dark:border-slate-800">
                                      ↳
                                    </td>
                                    <td className="px-4 py-1.5 font-medium text-slate-700 dark:text-slate-300 border-r border-slate-100 dark:border-slate-800">
                                      {tSub.tertiaryVal}
                                    </td>
                                    {visibleCols.includes("target") && (<td className="px-4 py-1.5 text-right font-mono text-slate-400">{formatInr(tSub.target)}</td>)}
                                    {visibleCols.includes("total_sales") && (<td className="px-4 py-1.5 text-right font-semibold text-slate-800 dark:text-slate-100">{formatInr(tSub.net_sales)}</td>)}
                                    {visibleCols.includes("ly_sales") && (<td className="px-4 py-1.5 text-right font-semibold text-blue-700 dark:text-blue-300 bg-blue-50/10 dark:bg-blue-900/10">{formatInr(tSub.ly_sales)}</td>)}
                                    {visibleCols.includes("yoyPct") && (<td className="px-4 py-1.5 text-right">
                                      {tSub.yoyPct != null ? (
                                        <span className={`inline-block rounded px-1.5 py-0.5 text-[10px] font-semibold ${tSub.yoyPct >= 0 ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-400' : 'bg-rose-50 text-rose-700 dark:bg-rose-950/50 dark:text-rose-400'}`}>
                                          {tSub.yoyPct >= 0 ? `+${tSub.yoyPct.toFixed(1)}%` : `${tSub.yoyPct.toFixed(1)}%`}
                                        </span>
                                      ) : ('—')}
                                    </td>)}
                                    {visibleCols.includes("total_qty") && (<td className="px-4 py-1.5 text-right font-mono text-slate-600 dark:text-slate-400">{tSub.qty.toLocaleString('en-IN')}</td>)}
                                    {visibleCols.includes("total_soh") && (<td className="px-4 py-1.5 text-right font-mono text-emerald-600 dark:text-emerald-400">{tSub.soh.toLocaleString('en-IN')}</td>)}
                                    {visibleCols.includes("mdq") && (<td className="px-4 py-1.5 text-right font-mono text-purple-600 dark:text-purple-400">{tSub.mdq > 0 ? tSub.mdq.toLocaleString('en-IN') : '—'}</td>)}
                                    {visibleCols.includes("achvPct") && (<td className="px-4 py-1.5 text-right">
                                      {tAchv != null ? (
                                        <span className="inline-block rounded px-1.5 py-0.5 text-[10px] bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300 font-semibold">
                                          {tAchv.toFixed(0)}%
                                        </span>
                                      ) : (
                                        '—'
                                      )}
                                    </td>)}
                                    {visibleCols.includes("asp") && (<td className="px-4 py-1.5 text-right font-mono text-slate-500">₹{Math.round(tSub.asp)}</td>)}
                                    {visibleCols.includes("discPct") && (<td className="px-4 py-1.5 text-right font-mono text-slate-400">{tSub.discPct.toFixed(1)}%</td>)}
                                  </tr>
                                );
                              })}

                              {/* Clean Subtotal Row Below Level 3 Items */}
                              <tr className="bg-slate-100/60 dark:bg-slate-800/50 font-bold border-b border-slate-200 dark:border-slate-700 text-xs">
                                <td className="px-4 py-2 pl-6 text-slate-300 border-r border-slate-100 dark:border-slate-800">
                                  —
                                </td>
                                <td colSpan={2} className="px-4 py-2 text-slate-900 dark:text-white font-bold border-r border-slate-100 dark:border-slate-800">
                                  {sub.secondaryVal} Total
                                </td>
                                {visibleCols.includes("target") && (<td className="px-4 py-2 text-right font-mono text-slate-500">{formatInr(sub.target)}</td>)}
                                {visibleCols.includes("total_sales") && (<td className="px-4 py-2 text-right font-bold text-slate-900 dark:text-slate-100">{formatInr(sub.net_sales)}</td>)}
                                {visibleCols.includes("ly_sales") && (<td className="px-4 py-2 text-right font-bold text-blue-700 dark:text-blue-300 bg-blue-50/20 dark:bg-blue-900/10">{formatInr(sub.ly_sales)}</td>)}
                                {visibleCols.includes("yoyPct") && (<td className="px-4 py-2 text-right">
                                  {sub.yoyPct != null ? (
                                    <span className={`inline-block rounded px-2 py-0.5 text-[11px] font-bold ${sub.yoyPct >= 0 ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/80 dark:text-emerald-300' : 'bg-rose-100 text-rose-800 dark:bg-rose-950/80 dark:text-rose-300'}`}>
                                      {sub.yoyPct >= 0 ? `+${sub.yoyPct.toFixed(1)}%` : `${sub.yoyPct.toFixed(1)}%`}
                                    </span>
                                  ) : ('—')}
                                </td>)}
                                {visibleCols.includes("total_qty") && (<td className="px-4 py-2 text-right font-mono text-slate-700 dark:text-slate-300">{sub.qty.toLocaleString('en-IN')}</td>)}
                                {visibleCols.includes("total_soh") && (<td className="px-4 py-2 text-right font-mono text-emerald-700 dark:text-emerald-400 font-semibold">{sub.soh.toLocaleString('en-IN')}</td>)}
                                {visibleCols.includes("mdq") && (<td className="px-4 py-2 text-right font-mono text-purple-700 dark:text-purple-400 font-semibold">{sub.mdq > 0 ? sub.mdq.toLocaleString('en-IN') : '—'}</td>)}
                                {visibleCols.includes("achvPct") && (<td className="px-4 py-2 text-right">
                                  {sAchv != null ? (
                                    <span className={`inline-block rounded px-2 py-0.5 text-[11px] ${sAchv >= 100 ? 'bg-emerald-100 text-emerald-800 font-bold' : sAchv >= 85 ? 'bg-blue-100 text-blue-800' : 'bg-amber-100 text-amber-800'}`}>
                                      {sAchv.toFixed(0)}%
                                    </span>
                                  ) : (
                                    '—'
                                  )}
                                </td>)}
                                {visibleCols.includes("asp") && (<td className="px-4 py-2 text-right font-mono text-slate-600 dark:text-slate-400">₹{Math.round(sub.asp)}</td>)}
                                {visibleCols.includes("discPct") && (<td className="px-4 py-2 text-right font-mono text-slate-500">{sub.discPct.toFixed(1)}%</td>)}
                              </tr>
                            </React.Fragment>
                          );
                        }

                        return (
                          <tr key={sub.secondaryVal} className="hover:bg-slate-50/60 dark:hover:bg-slate-800/30">
                            <td className="px-4 py-2 pl-8 text-slate-400 border-r border-slate-100 dark:border-slate-800">
                              ↳
                            </td>
                            <td className="px-4 py-2 font-medium text-slate-800 dark:text-slate-200 border-r border-slate-100 dark:border-slate-800" colSpan={tertiaryDim !== 'none' ? 2 : 1}>
                              {sub.secondaryVal}
                            </td>
                            {visibleCols.includes("target") && (<td className="px-4 py-2 text-right font-mono text-slate-500">{formatInr(sub.target)}</td>)}
                            {visibleCols.includes("total_sales") && (<td className="px-4 py-2 text-right font-bold text-slate-800 dark:text-slate-100">{formatInr(sub.net_sales)}</td>)}
                            {visibleCols.includes("ly_sales") && (<td className="px-4 py-2 text-right font-bold text-blue-700 dark:text-blue-300 bg-blue-50/20 dark:bg-blue-900/10">{formatInr(sub.ly_sales)}</td>)}
                            {visibleCols.includes("yoyPct") && (<td className="px-4 py-2 text-right">
                              {sub.yoyPct != null ? (
                                <span className={`inline-block rounded px-2 py-0.5 text-[11px] font-bold ${sub.yoyPct >= 0 ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/80 dark:text-emerald-300' : 'bg-rose-100 text-rose-800 dark:bg-rose-950/80 dark:text-rose-300'}`}>
                                  {sub.yoyPct >= 0 ? `+${sub.yoyPct.toFixed(1)}%` : `${sub.yoyPct.toFixed(1)}%`}
                                </span>
                              ) : ('—')}
                            </td>)}
                            {visibleCols.includes("total_qty") && (<td className="px-4 py-2 text-right font-mono text-slate-700 dark:text-slate-300">{sub.qty.toLocaleString('en-IN')}</td>)}
                            {visibleCols.includes("total_soh") && (<td className="px-4 py-2 text-right font-mono text-emerald-700 dark:text-emerald-400 font-semibold">{sub.soh.toLocaleString('en-IN')}</td>)}
                            {visibleCols.includes("mdq") && (<td className="px-4 py-2 text-right font-mono text-purple-700 dark:text-purple-400 font-semibold">{sub.mdq > 0 ? sub.mdq.toLocaleString('en-IN') : '—'}</td>)}
                            {visibleCols.includes("achvPct") && (<td className="px-4 py-2 text-right">
                              {sAchv != null ? (
                                <span className={`inline-block rounded px-2 py-0.5 text-[11px] ${sAchv >= 100 ? 'bg-emerald-100 text-emerald-800 font-bold' : sAchv >= 85 ? 'bg-blue-100 text-blue-800' : 'bg-amber-100 text-amber-800'}`}>
                                  {sAchv.toFixed(0)}%
                                </span>
                              ) : (
                                '—'
                              )}
                            </td>)}
                            {visibleCols.includes("asp") && (<td className="px-4 py-2 text-right font-mono text-slate-600 dark:text-slate-400">₹{Math.round(sub.asp)}</td>)}
                            {visibleCols.includes("discPct") && (<td className="px-4 py-2 text-right font-mono text-slate-500">{sub.discPct.toFixed(1)}%</td>)}
                          </tr>
                        );
                      })}

                    {/* Group Subtotal Row */}
                    {!isCollapsed && (
                      <tr className="border-b-2 border-slate-200/80 bg-slate-100/50 font-bold dark:border-slate-700 dark:bg-slate-800/40">
                        <td colSpan={tertiaryDim !== 'none' ? 3 : 2} className="px-4 py-2.5 text-slate-900 dark:text-white">
                          {grp.primaryVal} Total
                        </td>
                        {visibleCols.includes("target") && (<td className="px-4 py-2.5 text-right font-mono">{formatInr(grp.target)}</td>)}
                        {visibleCols.includes("total_sales") && (<td className="px-4 py-2.5 text-right text-blue-700 dark:text-blue-400">{formatInr(grp.total_sales)}</td>)}
                        {visibleCols.includes("ly_sales") && (<td className="px-4 py-2.5 text-right font-bold text-blue-700 dark:text-blue-300 bg-blue-50/30 dark:bg-blue-900/10">{formatInr(grp.ly_sales)}</td>)}
                        {visibleCols.includes("yoyPct") && (<td className="px-4 py-2.5 text-right font-bold">
                          {grp.yoyPct != null ? (
                            <span className={`inline-block rounded-md px-2 py-0.5 text-[11px] font-bold ${grp.yoyPct >= 0 ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/80 dark:text-emerald-300' : 'bg-rose-100 text-rose-800 dark:bg-rose-950/80 dark:text-rose-300'}`}>
                              {grp.yoyPct >= 0 ? `+${grp.yoyPct.toFixed(1)}%` : `${grp.yoyPct.toFixed(1)}%`}
                            </span>
                          ) : ('—')}
                        </td>)}
                        {visibleCols.includes("total_qty") && (<td className="px-4 py-2.5 text-right font-mono">{grp.total_qty.toLocaleString('en-IN')}</td>)}
                        {visibleCols.includes("total_soh") && (<td className="px-4 py-2.5 text-right font-mono text-emerald-700 dark:text-emerald-400">{grp.total_soh.toLocaleString('en-IN')}</td>)}
                        {visibleCols.includes("mdq") && (<td className="px-4 py-2.5 text-right font-mono text-purple-700 dark:text-purple-400 font-bold">{grp.mdq > 0 ? grp.mdq.toLocaleString('en-IN') : '—'}</td>)}
                        {visibleCols.includes("achvPct") && (<td className="px-4 py-2.5 text-right">
                          {achvVal != null ? (
                            <span className={`inline-block rounded-md px-2 py-0.5 text-[11px] ${achvBadgeClass}`}>
                              {achvVal.toFixed(0)}%
                            </span>
                          ) : (
                            '—'
                          )}
                        </td>)}
                        {visibleCols.includes("asp") && (<td className="px-4 py-2.5 text-right font-mono">₹{Math.round(grp.asp)}</td>)}
                        {visibleCols.includes("discPct") && (<td className="px-4 py-2.5 text-right font-mono">{grp.discPct.toFixed(1)}%</td>)}
                      </tr>
                    )}
                  </React.Fragment>
                );
              }

              /* Flat Group-By View */
              return (
                <tr key={grp.primaryVal} className="hover:bg-slate-50/80 dark:hover:bg-slate-800/50">
                  <td className="px-4 py-3 font-bold text-slate-900 dark:text-white border-r border-slate-100 dark:border-slate-800">
                    {grp.primaryVal}
                  </td>
                  {visibleCols.includes("target") && (<td className="px-4 py-3 text-right font-mono text-slate-500">{formatInr(grp.target)}</td>)}
                  {visibleCols.includes("total_sales") && (<td className="px-4 py-3 text-right font-bold text-slate-900 dark:text-white">{formatInr(grp.total_sales)}</td>)}
                  {visibleCols.includes("ly_sales") && (<td className="px-4 py-3 text-right font-bold text-blue-700 dark:text-blue-300 bg-blue-50/30 dark:bg-blue-900/10">{formatInr(grp.ly_sales)}</td>)}
                  {visibleCols.includes("yoyPct") && (<td className="px-4 py-3 text-right font-bold">
                    {grp.yoyPct != null ? (
                      <span className={`inline-block rounded-md px-2 py-0.5 text-[11px] font-bold ${grp.yoyPct >= 0 ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/80 dark:text-emerald-300' : 'bg-rose-100 text-rose-800 dark:bg-rose-950/80 dark:text-rose-300'}`}>
                        {grp.yoyPct >= 0 ? `+${grp.yoyPct.toFixed(1)}%` : `${grp.yoyPct.toFixed(1)}%`}
                      </span>
                    ) : ('—')}
                  </td>)}
                  {visibleCols.includes("total_qty") && (<td className="px-4 py-3 text-right font-mono text-slate-700 dark:text-slate-300">{grp.total_qty.toLocaleString('en-IN')}</td>)}
                  {visibleCols.includes("total_soh") && (<td className="px-4 py-3 text-right font-mono text-emerald-700 dark:text-emerald-400 font-semibold">{grp.total_soh.toLocaleString('en-IN')}</td>)}
                  {visibleCols.includes("mdq") && (<td className="px-4 py-3 text-right font-mono text-purple-700 dark:text-purple-400 font-semibold">{grp.mdq > 0 ? grp.mdq.toLocaleString('en-IN') : '—'}</td>)}
                  {visibleCols.includes("achvPct") && (<td className="px-4 py-3 text-right">
                    {achvVal != null ? (
                      <span className={`inline-block rounded-md px-2.5 py-0.5 text-[11px] ${achvBadgeClass}`}>
                        {achvVal.toFixed(0)}%
                      </span>
                    ) : (
                      '—'
                    )}
                  </td>)}
                  {visibleCols.includes("asp") && (<td className="px-4 py-3 text-right font-mono text-slate-600 dark:text-slate-400">₹{Math.round(grp.asp)}</td>)}
                  {visibleCols.includes("discPct") && (<td className="px-4 py-3 text-right font-mono text-slate-500">{grp.discPct.toFixed(1)}%</td>)}
                </tr>
              );
            })}
          </tbody>

          {/* Grand Total Footer */}
          <tfoot className="sticky bottom-0 z-10 border-t-2 border-slate-300 bg-slate-100/95 text-xs font-extrabold text-slate-900 backdrop-blur-sm shadow-[0_-2px_6px_rgba(0,0,0,0.06)] dark:border-slate-700 dark:bg-slate-800/95 dark:text-white">
            <tr>
              <td className="px-4 py-3 uppercase tracking-wider" colSpan={tertiaryDim !== 'none' ? 3 : secondaryDim !== 'none' ? 2 : 1}>
                Grand Total
              </td>
              {columnSplit !== 'none' ? (
                <>
                  {splitColKeys.map((col) => {
                    let colTarget = 0;
                    let colSales = 0;
                    pivotGroups.forEach((g) => {
                      const m = g.splitMetrics[col];
                      if (m) {
                        colTarget += m.target;
                        colSales += m.sales;
                      }
                    });
                    const colAchv = colTarget > 0 ? (colSales / colTarget) * 100 : null;
                    return (
                      <React.Fragment key={col}>
                        <td className="px-3 py-3 text-right font-mono text-slate-600 dark:text-slate-400">{formatInr(colTarget)}</td>
                        <td className="px-3 py-3 text-right font-bold text-slate-900 dark:text-white">{formatInr(colSales)}</td>
                        <td className="px-3 py-3 text-right">
                          {colAchv != null ? <span className="rounded bg-blue-100 px-2 py-0.5 text-[11px] text-blue-800">{colAchv.toFixed(0)}%</span> : '—'}
                        </td>
                      </React.Fragment>
                    );
                  })}
                  <td className="px-3 py-3 text-right font-mono text-slate-700 dark:text-slate-300">{formatInr(overallTotals.target)}</td>
                  <td className="px-3 py-3 text-right font-black text-blue-700 dark:text-blue-400">{formatInr(overallTotals.sales)}</td>
                  <td className="px-3 py-3 text-right">
                    {overallTotals.target > 0 ? (
                      <span className="rounded-md bg-emerald-600 px-2.5 py-0.5 text-[11px] font-bold text-white">
                        {((overallTotals.sales / overallTotals.target) * 100).toFixed(0)}%
                      </span>
                    ) : (
                      '—'
                    )}
                  </td>
                </>
              ) : (
                <>
                  {visibleCols.includes("target") && (<td className="px-4 py-3 text-right font-mono text-slate-600 dark:text-slate-400">{formatInr(overallTotals.target)}</td>)}
                  {visibleCols.includes("total_sales") && (<td className="px-4 py-3 text-right font-black text-blue-700 dark:text-blue-400">{formatInr(overallTotals.sales)}</td>)}
                  {visibleCols.includes("ly_sales") && (<td className="px-4 py-3 text-right font-black text-blue-800 dark:text-blue-300 bg-blue-100/40 dark:bg-blue-900/20">{formatInr(typeof insights?.ly_metrics?.total === 'number' ? insights.ly_metrics.total : 0)}</td>)}
                  {visibleCols.includes("yoyPct") && (<td className="px-4 py-3 text-right font-black">
                    {(() => {
                      const grandLy = typeof insights?.ly_metrics?.total === 'number' ? insights.ly_metrics.total : 0;
                      const grandYoy = grandLy > 0 ? ((overallTotals.sales - grandLy) / grandLy) * 100 : null;
                      return grandYoy != null ? (
                        <span className={`inline-block rounded-md px-2 py-0.5 text-[11px] font-bold ${grandYoy >= 0 ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/80 dark:text-emerald-300' : 'bg-rose-100 text-rose-800 dark:bg-rose-950/80 dark:text-rose-300'}`}>
                          {grandYoy >= 0 ? `+${grandYoy.toFixed(1)}%` : `${grandYoy.toFixed(1)}%`}
                        </span>
                      ) : '—';
                    })()}
                  </td>)}
                  {visibleCols.includes("total_qty") && (<td className="px-4 py-3 text-right font-mono">{overallTotals.qty.toLocaleString('en-IN')}</td>)}
                  {visibleCols.includes("total_soh") && (<td className="px-4 py-3 text-right font-mono text-emerald-700 dark:text-emerald-400 font-bold">{overallTotals.soh.toLocaleString('en-IN')}</td>)}
                  {visibleCols.includes("mdq") && (<td className="px-4 py-3 text-right font-mono text-purple-800 dark:text-purple-300 font-bold">{overallTotals.total_mdq > 0 ? overallTotals.total_mdq.toLocaleString('en-IN') : '—'}</td>)}
                  {visibleCols.includes("achvPct") && (<td className="px-4 py-3 text-right">
                    {overallTotals.target > 0 ? (
                      <span className="rounded-md bg-emerald-600 px-2.5 py-0.5 text-[11px] font-bold text-white">
                        {((overallTotals.sales / overallTotals.target) * 100).toFixed(0)}%
                      </span>
                    ) : (
                      '—'
                    )}
                  </td>)}
                  {visibleCols.includes("asp") && (<td className="px-4 py-3 text-right font-mono">
                    ₹{overallTotals.qty > 0 ? Math.round(overallTotals.sales / overallTotals.qty) : 0}
                  </td>)}
                  {visibleCols.includes("discPct") && (<td className="px-4 py-3 text-right font-mono">—</td>)}
                </>
              )}
            </tr>
          </tfoot>
        </table>
      </div>
    </div>
  );
}

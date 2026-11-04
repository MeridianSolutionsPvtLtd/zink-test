import React, { useState, useEffect, useMemo, useCallback } from 'react';
import {
  RotateCcw,
  Download,
  Bookmark,
  SlidersHorizontal,
  Calendar,
  Layers,
  Search,
  Check,
} from 'lucide-react';
import ExcelJS from 'exceljs';
import { API_URL } from '../../../config';
import { FieldCatalog } from './FieldCatalog';
import { PivotDropZone } from './PivotDropZone';
import { PivotResultGrid } from './PivotResultGrid';
import { querySalesPivot } from './querySalesPivot';
import { PivotFilterModal } from './PivotFilterModal';
import { SavedViewsModal } from './SavedViewsModal';
import type {
  DimensionMeta,
  MeasureMeta,
  ValueConfig,
  SortConfig,
  AnalyzerQueryResult,
  SavedView,
  TreeNode,
} from './types';
import {
  buildAnalyzerFiltersFromPage,
  type ClientFilters,
  type CustomerMasterSite,
  type TimelineMode,
} from '../sales-dashboard/salesAnalytics';

const DEFAULT_ROWS = ['zone', 'sales_channel'];
const DEFAULT_COLUMNS: string[] = [];

const DEFAULT_VALUES: ValueConfig[] = [
  { field: 'sales', aggregation: 'sum' },
  { field: 'sales_ly', aggregation: 'sum' },
  { field: 'sales_growth_pct', aggregation: 'calculated' },
  { field: 'dispatch_qty', aggregation: 'sum' },
  { field: 'quantity', aggregation: 'sum' },
  { field: 'current_soh', aggregation: 'sum' },
  { field: 'sell_through_pct', aggregation: 'calculated' },
  { field: 'asp', aggregation: 'avg' },
];

const PRESET_VIEWS: {
  name: string;
  rows: string[];
  columns: string[];
  values: ValueConfig[];
}[] = [
  {
    name: 'Zone → Channel / Format',
    rows: ['zone', 'sales_channel'],
    columns: [],
    values: [
      { field: 'sales', aggregation: 'sum' },
      { field: 'sales_ly', aggregation: 'sum' },
      { field: 'sales_growth_pct', aggregation: 'calculated' },
      { field: 'dispatch_qty', aggregation: 'sum' },
      { field: 'quantity', aggregation: 'sum' },
      { field: 'current_soh', aggregation: 'sum' },
      { field: 'sell_through_pct', aggregation: 'calculated' },
      { field: 'asp', aggregation: 'avg' },
    ],
  },
  {
    name: 'Channel Matrix by Month',
    rows: ['sales_channel'],
    columns: ['month'],
    values: [
      { field: 'sales', aggregation: 'sum' },
      { field: 'sales_growth_pct', aggregation: 'calculated' },
      { field: 'sell_through_pct', aggregation: 'calculated' },
    ],
  },
  {
    name: 'Manager → Store Performance',
    rows: ['manager', 'site_id_name'],
    columns: [],
    values: [
      { field: 'sales', aggregation: 'sum' },
      { field: 'sales_ly', aggregation: 'sum' },
      { field: 'sales_growth_pct', aggregation: 'calculated' },
      { field: 'sales_per_sqft', aggregation: 'calculated' },
      { field: 'quantity', aggregation: 'sum' },
      { field: 'current_soh', aggregation: 'sum' },
      { field: 'sell_through_pct', aggregation: 'calculated' },
      { field: 'store_sqft', aggregation: 'sum' },
      { field: 'mdq', aggregation: 'sum' },
    ],
  },
  {
    name: 'Store Area & Sales / Sq Ft Efficiency',
    rows: ['zone', 'site_id'],
    columns: [],
    values: [
      { field: 'sales', aggregation: 'sum' },
      { field: 'sales_per_sqft', aggregation: 'calculated' },
      { field: 'store_sqft', aggregation: 'sum' },
      { field: 'quantity', aggregation: 'sum' },
      { field: 'current_soh', aggregation: 'sum' },
      { field: 'sell_through_pct', aggregation: 'calculated' },
      { field: 'asp', aggregation: 'avg' },
    ],
  },
  {
    name: 'Department → Section → Size Drilldown',
    rows: ['department', 'section', 'size'],
    columns: [],
    values: [
      { field: 'sales', aggregation: 'sum' },
      { field: 'quantity', aggregation: 'sum' },
      { field: 'current_soh', aggregation: 'sum' },
      { field: 'sell_through_pct', aggregation: 'calculated' },
      { field: 'asp', aggregation: 'avg' },
      { field: 'discount_pct', aggregation: 'avg' },
    ],
  },
  {
    name: 'MDQ vs SOH Store Compliance',
    rows: ['zone', 'site_id', 'department'],
    columns: [],
    values: [
      { field: 'mdq', aggregation: 'sum' },
      { field: 'current_soh', aggregation: 'sum' },
      { field: 'mdq_fill_pct', aggregation: 'calculated' },
      { field: 'mdq_deficit', aggregation: 'sum' },
      { field: 'sales', aggregation: 'sum' },
      { field: 'quantity', aggregation: 'sum' },
      { field: 'sell_through_pct', aggregation: 'calculated' },
    ],
  },
  {
    name: 'Store Grade by Channel',
    rows: ['store_grade', 'site_id'],
    columns: ['sales_channel'],
    values: [
      { field: 'sales', aggregation: 'sum' },
      { field: 'sales_per_sqft', aggregation: 'calculated' },
      { field: 'quantity', aggregation: 'sum' },
      { field: 'current_soh', aggregation: 'sum' },
      { field: 'sell_through_pct', aggregation: 'calculated' },
    ],
  },
];

const SAVED_VIEWS_STORAGE_KEY = 'stockpulse_saved_pivot_views_v1';

export type SalesAnalyzerPageContext = {
  salesYear: number;
  timelineMode: TimelineMode;
  timelineValues: string[];
  clientFilters: ClientFilters;
  masterSites?: CustomerMasterSite[];
  storeId?: string;
  channelId?: string;
  monthId?: string;
};

type Props = {
  enabled?: boolean;
  initialYear?: number;
  /** When set (Sales Dashboard embed), use top-page filters only — hide analyzer FY/filters UI. */
  pageContext?: SalesAnalyzerPageContext;
};

function _indianFyEnd(d = new Date()): number {
  return d.getMonth() >= 3 ? d.getFullYear() + 1 : d.getFullYear();
}

/** Analyzer UI: FY26 through current FY (matches backend analyzer_visible_years). */
function analyzerVisibleYears(d = new Date()): number[] {
  const cur = _indianFyEnd(d);
  const start = Math.min(2026, cur);
  const out: number[] = [];
  for (let y = start; y <= cur; y += 1) out.push(y);
  return out;
}

function clampAnalyzerYear(year: number | undefined | null, d = new Date()): number {
  const visible = analyzerVisibleYears(d);
  const cur = visible[visible.length - 1];
  const y = Number(year);
  if (!Number.isFinite(y) || y === 0) return cur;
  return visible.includes(y) ? y : cur;
}

export function SalesAnalyzerModule({ initialYear = 2027, pageContext, enabled = true }: Props) {
  const usePageFilters = Boolean(pageContext);
  const visibleYears = useMemo(() => analyzerVisibleYears(), []);

  const [dimensions, setDimensions] = useState<DimensionMeta[]>([]);
  const [measures, setMeasures] = useState<MeasureMeta[]>([]);
  const [filterOptionsMap, setFilterOptionsMap] = useState<Record<string, string[]>>({});

  // Active Pivot State
  const [rows, setRows] = useState<string[]>(DEFAULT_ROWS);
  const [columns, setColumns] = useState<string[]>(DEFAULT_COLUMNS);
  const [values, setValues] = useState<ValueConfig[]>(DEFAULT_VALUES);
  const [filters, setFilters] = useState<Record<string, any>>({});
  const [selectedYear, setSelectedYear] = useState<number>(() => clampAnalyzerYear(initialYear));
  const [globalSearch, setGlobalSearch] = useState<string>('');
  const [sort, setSort] = useState<SortConfig[]>([{ field: 'sales', direction: 'desc' }]);

  // Sync with parent initialYear prop if updated (standalone mode only)
  useEffect(() => {
    if (!usePageFilters && initialYear !== undefined) {
      setSelectedYear(clampAnalyzerYear(initialYear));
    }
  }, [initialYear, usePageFilters]);

  const pageYearOutsideScope = Boolean(
    usePageFilters &&
      pageContext &&
      !visibleYears.includes(Number(pageContext.salesYear)),
  );

  const pageFilterPayload = useMemo(() => {
    if (!pageContext) return null;
    const clampedYear = clampAnalyzerYear(pageContext.salesYear);
    const outOfScope = !visibleYears.includes(Number(pageContext.salesYear));
    // When Overview FY is outside Analyzer scope, align timeline FY token too.
    let timelineMode = pageContext.timelineMode;
    let timelineValues = pageContext.timelineValues;
    if (outOfScope) {
      timelineMode = 'fy_year';
      timelineValues = [`FY${String(clampedYear).slice(-2)}`];
    }
    return buildAnalyzerFiltersFromPage({
      timelineMode,
      timelineValues,
      salesYear: clampedYear,
      clientFilters: pageContext.clientFilters,
      masterSites: pageContext.masterSites,
      storeId: pageContext.storeId,
      channelId: pageContext.channelId,
      monthId: pageContext.monthId,
    });
  }, [
    pageContext?.timelineMode,
    pageContext?.timelineValues,
    pageContext?.salesYear,
    pageContext?.clientFilters,
    pageContext?.masterSites,
    pageContext?.storeId,
    pageContext?.channelId,
    pageContext?.monthId,
    visibleYears,
  ]);

  const effectiveYear = clampAnalyzerYear(pageFilterPayload?.year ?? selectedYear);
  const effectiveFilters = pageFilterPayload?.filters ?? filters;

  // Query Result State
  const [queryResult, setQueryResult] = useState<AnalyzerQueryResult | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [queryError, setQueryError] = useState<string | null>(null);
  const [isExporting, setIsExporting] = useState<boolean>(false);

  // Modals
  const [activeFilterField, setActiveFilterField] = useState<string | null>(null);
  const [savedViewsModalOpen, setSavedViewsModalOpen] = useState<boolean>(false);
  const [savedViews, setSavedViews] = useState<SavedView[]>([]);

  // Load Saved Views from local storage on mount
  useEffect(() => {
    try {
      const stored = localStorage.getItem(SAVED_VIEWS_STORAGE_KEY);
      if (stored) {
        setSavedViews(JSON.parse(stored));
      }
    } catch (e) {
      console.warn('Failed to load saved views:', e);
    }
  }, []);

  // Fetch Metadata & Filter Options in parallel (hosted cold-start: don't wait sequentially)
  useEffect(() => {
    const fetchMetadata = async () => {
      try {
        const metaP = fetch(`${API_URL}/sales-analyzer/metadata`).then(async (res) => {
          if (!res.ok) return;
          const data = await res.json();
          if (data.ok) {
            setDimensions(data.dimensions || []);
            setMeasures(data.measures || []);
          }
        });
        const filtersP = !usePageFilters
          ? fetch(`${API_URL}/sales-analyzer/filter-options?year=${selectedYear}`).then(async (fRes) => {
              if (!fRes.ok) return;
              const fData = await fRes.json();
              if (fData.ok && fData.options) {
                setFilterOptionsMap(fData.options);
              }
            })
          : Promise.resolve();
        await Promise.all([metaP, filtersP]);
      } catch (err) {
        console.error('Failed to fetch metadata:', err);
      }
    };
    fetchMetadata();
  }, [selectedYear, usePageFilters]);

  // In-Memory Fast Client-Side Cache (0ms Instant Replay on Preset/Filter toggle)
  const clientQueryCacheRef = React.useRef<Map<string, AnalyzerQueryResult>>(new Map());
  const abortControllerRef = React.useRef<AbortController | null>(null);

  // Drop stale client cache when page filters / year change (SPLY window depends on them)
  useEffect(() => {
    clientQueryCacheRef.current.clear();
  }, [effectiveYear, effectiveFilters]);

  // Execute Query whenever pivot configuration changes
  const executeQuery = useCallback(async () => {
    abortControllerRef.current?.abort();
    setQueryError(null);
    if (!enabled) {
      setQueryResult(null);
      setLoading(true);
      return;
    }
    if (rows.length === 0 && columns.length === 0) {
      setQueryResult(null);
      setLoading(false);
      setQueryError('Select at least one row or column dimension.');
      return;
    }

    const payload = {
      rows,
      columns,
      values,
      filters: effectiveFilters,
      sort,
      year: effectiveYear,
      search: globalSearch.trim() || undefined,
    };

    const cacheKey = JSON.stringify(payload);

    // If cached, immediately show cached result (0ms UI latency!)
    if (clientQueryCacheRef.current.has(cacheKey)) {
      setQueryResult(clientQueryCacheRef.current.get(cacheKey)!);
      setLoading(false);
    } else {
      setQueryResult(null);
      setLoading(true);
    }

    // Cancel in-flight stale requests
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    const controller = new AbortController();
    abortControllerRef.current = controller;

    try {
      const data: AnalyzerQueryResult = await querySalesPivot(
        `${API_URL}/sales-analyzer/query`, payload, controller.signal,
      );
      if (controller.signal.aborted) return;
      if (data.ok) {
        clientQueryCacheRef.current.set(cacheKey, data);
        setQueryResult(data);
      }
    } catch (err: any) {
      if (!controller.signal.aborted && err.name !== 'AbortError') {
        console.error('Failed to execute sales analyzer query:', err);
        setQueryError(err instanceof Error ? err.message : 'Sales pivot could not load. Please retry.');
      }
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, [enabled, rows, columns, values, effectiveFilters, sort, effectiveYear, globalSearch]);

  useEffect(() => {
    // Debounce hierarchy/measure edits — Manager pivots are heavier; aborting
    // every 120ms drag caused long waits then "could not load" errors.
    const timer = setTimeout(() => {
      executeQuery();
    }, 350);
    return () => { clearTimeout(timer); abortControllerRef.current?.abort(); };
  }, [executeQuery]);

  // Add field helper
  const handleAddField = (field: string, targetArea: 'rows' | 'columns' | 'values' | 'filters') => {
    if (targetArea === 'rows') {
      if (!rows.includes(field)) {
        setRows([...rows, field]);
        setColumns(columns.filter((c) => c !== field));
      }
    } else if (targetArea === 'columns') {
      if (!columns.includes(field)) {
        setColumns([field]);
        setRows(rows.filter((r) => r !== field));
      }
    } else if (targetArea === 'values') {
      if (!values.some((v) => v.field === field)) {
        const mMeta = measures.find((m) => m.field === field);
        setValues([...values, { field, aggregation: mMeta?.defaultAggregation || 'sum' }]);
      }
    } else if (targetArea === 'filters' && !usePageFilters) {
      setActiveFilterField(field);
    }
  };

  const handleSortChange = (field: string) => {
    setSort((prev) => {
      if (prev && prev.length > 0 && prev[0].field === field) {
        return [{ field, direction: prev[0].direction === 'asc' ? 'desc' : 'asc' }];
      }
      return [{ field, direction: 'desc' }];
    });
  };

  const handleReset = () => {
    setRows(DEFAULT_ROWS);
    setColumns(DEFAULT_COLUMNS);
    setValues(DEFAULT_VALUES);
    if (!usePageFilters) setFilters({});
    setGlobalSearch('');
    setSort([{ field: 'sales', direction: 'desc' }]);
  };

  const handleApplyPreset = (preset: (typeof PRESET_VIEWS)[0]) => {
    setRows(preset.rows);
    setColumns(preset.columns);
    setValues(preset.values);
  };

  const handleSaveView = (name: string, description?: string) => {
    const newView: SavedView = {
      id: `view_${Date.now()}`,
      name,
      description,
      createdAt: new Date().toISOString(),
      config: {
        rows,
        columns,
        values,
        filters: usePageFilters ? {} : filters,
        year: effectiveYear,
      },
    };
    const updated = [newView, ...savedViews];
    setSavedViews(updated);
    try {
      localStorage.setItem(SAVED_VIEWS_STORAGE_KEY, JSON.stringify(updated));
    } catch (e) {
      console.warn('Failed to persist saved view:', e);
    }
  };

  const handleDeleteView = (id: string) => {
    const updated = savedViews.filter((v) => v.id !== id);
    setSavedViews(updated);
    try {
      localStorage.setItem(SAVED_VIEWS_STORAGE_KEY, JSON.stringify(updated));
    } catch (e) {
      console.warn('Failed to delete saved view:', e);
    }
  };

  const handleLoadView = (view: SavedView) => {
    setRows(view.config.rows || DEFAULT_ROWS);
    setColumns(view.config.columns || []);
    setValues(view.config.values || DEFAULT_VALUES);
    if (!usePageFilters) {
      setFilters(view.config.filters || {});
      if (view.config.year) setSelectedYear(view.config.year);
    }
  };

  // Safe download helper with filename sanitization
  const safeDownloadBlob = (blob: Blob, filename: string) => {
    const cleanFilename = (filename || `Sales_Analysis_${Date.now()}.xlsx`)
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
        if (document.body.contains(a)) document.body.removeChild(a);
        URL.revokeObjectURL(url);
      } catch {}
    }, 5000);
  };

  // Excel Export
  const handleExportExcel = async () => {
    if (!queryResult || queryResult.rows.length === 0) return;
    try {
      setIsExporting(true);
      const workbook = new ExcelJS.Workbook();
      workbook.creator = 'StockPulse Sales Analyzer';
      workbook.created = new Date();
      const sheet = workbook.addWorksheet('Sales Analysis Matrix', {
        views: [{ showGridLines: true }],
      });

      const splitColKeys = queryResult.split_col_keys || [];
      const rowDimensions = rows.map((r) => dimensions.find((d) => d.field === r)?.label || r);

      // Group Header Row
      if (splitColKeys.length > 0) {
        const groupHeader = [...rowDimensions.map(() => '')];
        splitColKeys.forEach((colKey) => {
          groupHeader.push(colKey);
          for (let i = 1; i < values.length; i++) groupHeader.push('');
        });
        groupHeader.push('Grand Total');
        for (let i = 1; i < values.length; i++) groupHeader.push('');

        const gRow = sheet.addRow(groupHeader);
        gRow.height = 24;
        gRow.font = { bold: true, color: { argb: 'FFFFFFFF' } };
        gRow.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FF0F172A' } };
      }

      // Measure Sub-header
      const measureHeader = [...rowDimensions];
      if (splitColKeys.length > 0) {
        splitColKeys.forEach(() => {
          values.forEach((v) => {
            const mMeta = measures.find((m) => m.field === v.field);
            measureHeader.push(v.custom_label || mMeta?.label || v.field);
          });
        });
      }
      values.forEach((v) => {
        const mMeta = measures.find((m) => m.field === v.field);
        measureHeader.push(v.custom_label || mMeta?.label || v.field);
      });

      const mRow = sheet.addRow(measureHeader);
      mRow.height = 26;
      mRow.font = { bold: true, color: { argb: 'FFFFFFFF' } };
      mRow.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FF1E293B' } };

      // Helper to add clean tabular rows to Excel (all hierarchy dimensions populated on single row)
      const addExcelTreeRows = (nodes: TreeNode[], parentHierarchy: string[] = []) => {
        nodes.forEach((node) => {
          const currentHierarchy = [...parentHierarchy, node.value];
          const hasChildren = node.children && node.children.length > 0;

          if (hasChildren) {
            addExcelTreeRows(node.children, currentHierarchy);
          } else {
            const rowVals: (string | number)[] = [...currentHierarchy];
            while (rowVals.length < rows.length) rowVals.push('');

            if (splitColKeys.length > 0) {
              splitColKeys.forEach((colKey) => {
                const sm = node.split_metrics?.[colKey] || {};
                values.forEach((v) => {
                  rowVals.push(sm[v.field] ?? '');
                });
              });
            }

            values.forEach((v) => {
              rowVals.push(node.metrics[v.field] ?? '');
            });

            const r = sheet.addRow(rowVals);
            r.height = 20;
          }
        });
      };

      addExcelTreeRows(queryResult.rows);

      // Grand Total on a separate sheet so summing the leaf sheet cannot double-count.
      const gtSheet = workbook.addWorksheet('Grand Total', {
        views: [{ showGridLines: true }],
      });
      const gtHeader = [...rowDimensions];
      if (splitColKeys.length > 0) {
        splitColKeys.forEach(() => {
          values.forEach((v) => {
            const mMeta = measures.find((m) => m.field === v.field);
            gtHeader.push(v.custom_label || mMeta?.label || v.field);
          });
        });
      }
      values.forEach((v) => {
        const mMeta = measures.find((m) => m.field === v.field);
        gtHeader.push(v.custom_label || mMeta?.label || v.field);
      });
      const gtHeaderRow = gtSheet.addRow(gtHeader);
      gtHeaderRow.font = { bold: true, color: { argb: 'FFFFFFFF' } };
      gtHeaderRow.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FF1E293B' } };

      const grandTotalVals: (string | number)[] = ['Grand Total', ...rowDimensions.slice(1).map(() => '')];
      if (splitColKeys.length > 0) {
        splitColKeys.forEach((colKey) => {
          const sm = queryResult.grand_total.split_metrics?.[colKey] || {};
          values.forEach((v) => {
            const raw = sm[v.field];
            grandTotalVals.push(raw == null ? '' : (raw as string | number));
          });
        });
      }
      values.forEach((v) => {
        const raw = queryResult.grand_total.metrics?.[v.field];
        grandTotalVals.push(raw == null ? '' : (raw as string | number));
      });
      const gtRow = gtSheet.addRow(grandTotalVals);
      gtRow.font = { bold: true, size: 11 };
      gtRow.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FFE2E8F0' } };

      const noteSheet = workbook.addWorksheet('Notes');
      noteSheet.addRow(['StockPulse Sales Analyzer export']);
      noteSheet.addRow(['Leaf rows only are on "Sales Analysis Matrix".']);
      noteSheet.addRow(['Do not sum leaf rows together with Grand Total — totals are on the "Grand Total" sheet.']);
      noteSheet.addRow([`Query engine: ${(queryResult as { query_engine?: string }).query_engine || 'pandas'}`]);
      noteSheet.addRow([`Exported at: ${new Date().toISOString()}`]);

      sheet.columns.forEach((col, idx) => {
        let maxLen = 14;
        col.eachCell?.({ includeEmpty: false }, (cell) => {
          const valStr = cell.value ? String(cell.value) : '';
          maxLen = Math.max(maxLen, Math.min(45, valStr.length + 3));
          if (idx >= rows.length && typeof cell.value === 'number') {
            cell.numFmt = '#,##0.00';
          }
        });
        col.width = maxLen;
      });

      const buffer = await workbook.xlsx.writeBuffer();
      const blob = new Blob([buffer], {
        type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      });
      const rowSummary = rows.map((r) => r.replace(/[^a-zA-Z0-9]/g, '_')).filter(Boolean).join('_') || 'Matrix';
      const dateStr = new Date().toISOString().slice(0, 10);
      safeDownloadBlob(blob, `Sales_Pivot_${rowSummary}_${dateStr}.xlsx`);
    } catch (err) {
      console.error('Export excel error:', err);
    } finally {
      setIsExporting(false);
    }
  };

  // CSV Export
  const handleExportCsv = () => {
    if (!queryResult || queryResult.rows.length === 0) return;
    try {
      const splitColKeys = queryResult.split_col_keys || [];
      const rowDimensions = rows.map((r) => dimensions.find((d) => d.field === r)?.label || r);

      const csvLines: string[] = [];

      // Header row
      const headers = [...rowDimensions];
      if (splitColKeys.length > 0) {
        splitColKeys.forEach((colKey) => {
          values.forEach((v) => {
            const mMeta = measures.find((m) => m.field === v.field);
            headers.push(`${colKey} - ${v.custom_label || mMeta?.label || v.field}`);
          });
        });
      }
      values.forEach((v) => {
        const mMeta = measures.find((m) => m.field === v.field);
        headers.push(`Grand Total - ${v.custom_label || mMeta?.label || v.field}`);
      });
      csvLines.push(headers.map((h) => `"${h.replace(/"/g, '""')}"`).join(','));

      // Helper to add clean tabular rows to CSV
      const addCsvTreeRows = (nodes: TreeNode[], parentHierarchy: string[] = []) => {
        nodes.forEach((node) => {
          const currentHierarchy = [...parentHierarchy, node.value];
          const hasChildren = node.children && node.children.length > 0;

          if (hasChildren) {
            addCsvTreeRows(node.children, currentHierarchy);
          } else {
            const rowVals = [...currentHierarchy];
            while (rowVals.length < rows.length) rowVals.push('');

            if (splitColKeys.length > 0) {
              splitColKeys.forEach((colKey) => {
                const sm = node.split_metrics?.[colKey] || {};
                values.forEach((v) => {
                  rowVals.push(sm[v.field] != null ? String(sm[v.field]) : '');
                });
              });
            }

            values.forEach((v) => {
              rowVals.push(node.metrics[v.field] != null ? String(node.metrics[v.field]) : '');
            });

            csvLines.push(rowVals.map((cell) => `"${String(cell).replace(/"/g, '""')}"`).join(','));
          }
        });
      };

      addCsvTreeRows(queryResult.rows);

      // Grand total
      const gtVals = ['Grand Total', ...rowDimensions.slice(1).map(() => '')];
      if (splitColKeys.length > 0) {
        splitColKeys.forEach((colKey) => {
          const sm = queryResult.grand_total.split_metrics?.[colKey] || {};
          values.forEach((v) => {
            gtVals.push(sm[v.field] != null ? String(sm[v.field]) : '');
          });
        });
      }
      values.forEach((v) => {
        gtVals.push(queryResult.grand_total.metrics?.[v.field] != null ? String(queryResult.grand_total.metrics?.[v.field]) : '');
      });
      csvLines.push(gtVals.map((cell) => `"${String(cell).replace(/"/g, '""')}"`).join(','));

      const csvContent = csvLines.join('\n');
      const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
      const rowSummary = rows.map((r) => r.replace(/[^a-zA-Z0-9]/g, '_')).filter(Boolean).join('_') || 'Matrix';
      const dateStr = new Date().toISOString().slice(0, 10);
      safeDownloadBlob(blob, `Sales_Pivot_${rowSummary}_${dateStr}.csv`);
    } catch (err) {
      console.error('Export CSV error:', err);
    }
  };

  return (
    <div className="rounded-2xl border border-slate-200/90 bg-white p-5 shadow-sm space-y-4 dark:border-slate-800 dark:bg-slate-900/90">
      {/* Sales Analyzer Card Header */}
      <div className="flex items-center justify-between border-b border-slate-100 pb-3 dark:border-slate-800">
        <div className="flex items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-blue-600 text-white shadow-sm shadow-blue-500/30">
            <SlidersHorizontal className="h-4 w-4" />
          </div>
          <h2 className="text-base font-bold text-slate-900 dark:text-white">
            Sales Analyzer
          </h2>
        </div>

        {/* Year Selector & Quick Search — FY dropdown only in standalone mode */}
        <div className="flex flex-wrap items-center gap-2.5">
          {!usePageFilters && (
            <div className="flex items-center gap-1.5 rounded-xl border border-slate-200 bg-slate-50 px-2.5 py-1.5 text-xs text-slate-700 shadow-sm dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200">
              <Calendar className="h-3.5 w-3.5 text-blue-600 dark:text-blue-400 shrink-0" />
              <span className="font-semibold text-slate-500 dark:text-slate-400">FY:</span>
              <select
                value={clampAnalyzerYear(selectedYear)}
                onChange={(e) => setSelectedYear(clampAnalyzerYear(Number(e.target.value)))}
                className="bg-transparent font-bold text-slate-800 outline-none cursor-pointer text-xs dark:text-white"
              >
                {[...visibleYears].reverse().map((fyEnd, i) => {
                  const startY = fyEnd - 1;
                  const short = String(fyEnd).slice(-2);
                  const label =
                    i === 0
                      ? `FY ${startY}-${short} (FY${short} - Current)`
                      : `FY ${startY}-${short} (FY${short})`;
                  return (
                    <option key={fyEnd} value={fyEnd} className="dark:bg-slate-800 text-slate-900 dark:text-white">
                      {label}
                    </option>
                  );
                })}
              </select>
            </div>
          )}
          {usePageFilters && (
            <span className="rounded-xl border border-slate-200 bg-slate-50 px-2.5 py-1.5 text-[11px] font-medium text-slate-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-400">
              Uses Sales Overview filters above
            </span>
          )}
          {(pageYearOutsideScope || queryResult?.year_fallback) && (
            <span className="rounded-xl border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-[11px] font-medium text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
              Pivot shows FY{String(effectiveYear).slice(-2)}
              {pageContext?.salesYear
                ? ` (Overview FY${String(pageContext.salesYear).slice(-2)} not in Analyzer scope)`
                : queryResult?.requested_year != null
                  ? ` (requested FY${String(queryResult.requested_year).slice(-2)} out of scope)`
                  : ''}
            </span>
          )}
          {queryResult?.period && (
            <span className="rounded-xl border border-blue-200 bg-blue-50 px-2.5 py-1.5 text-[11px] font-medium text-blue-700 dark:border-blue-500/30 dark:bg-blue-500/10 dark:text-blue-300">
              {queryResult.period.fy}: {queryResult.period.cy_from} – {queryResult.period.cy_to}
              {' · '}LY {queryResult.period.ly_from} – {queryResult.period.ly_to}
            </span>
          )}

          <div className="relative w-full sm:w-56">
            <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
            <input
              type="text"
              placeholder="Search matrix rows…"
              value={globalSearch}
              onChange={(e) => setGlobalSearch(e.target.value)}
              className="w-full rounded-xl border border-slate-200 bg-slate-50 pl-8 pr-3 py-1.5 text-xs text-slate-800 placeholder-slate-400 focus:border-blue-500 focus:bg-white focus:outline-none dark:border-slate-700 dark:bg-slate-800 dark:text-white"
            />
          </div>
        </div>
      </div>

      {/* Drag and Drop Configuration Drop Zones */}
      <PivotDropZone
        dimensions={dimensions}
        measures={measures}
        rows={rows}
        columns={columns}
        values={values}
        filters={usePageFilters ? {} : filters}
        hideFilters={usePageFilters}
        onUpdateRows={setRows}
        onUpdateColumns={setColumns}
        onUpdateValues={setValues}
        onOpenFilterModal={(field) => {
          if (!usePageFilters) setActiveFilterField(field);
        }}
        onRemoveFilter={(field) => {
          if (usePageFilters) return;
          const next = { ...filters };
          delete next[field];
          setFilters(next);
        }}
        onClearAll={handleReset}
      />

      {/* Main Workbench: Left Catalog + Center Grid */}
      <div className="grid grid-cols-1 items-stretch gap-4 xl:grid-cols-[18rem_minmax(0,1fr)]">
        {/* Left Field Catalog */}
        <div className="flex h-full min-w-0 flex-col">
          <FieldCatalog
            dimensions={dimensions}
            measures={measures}
            onAddField={handleAddField}
            activeRows={rows}
            activeCols={columns}
            activeValues={values}
            activeFilters={usePageFilters ? {} : filters}
          />
        </div>

        {/* Center Results Grid (9 cols) */}
        <div className="flex h-full min-w-0 flex-col">
          <PivotResultGrid
            rows={queryResult?.rows || []}
            splitColKeys={queryResult?.split_col_keys || []}
            splitColDim={queryResult?.split_col_dim}
            grandTotal={
              queryResult?.grand_total || { metrics: {}, record_count: 0 }
            }
            values={values}
            measures={measures}
            dimensions={dimensions}
            loading={loading}
            error={queryError}
            sort={sort}
            onSortChange={handleSortChange}
            onRefresh={executeQuery}
            onExportExcel={handleExportExcel}
            onExportCsv={handleExportCsv}
          />
        </div>
      </div>

      {/* Filter Modal — standalone analyzer only */}
      {!usePageFilters && activeFilterField && (
        <PivotFilterModal
          field={activeFilterField}
          dimensions={dimensions}
          options={filterOptionsMap[activeFilterField] || []}
          selectedValues={
            Array.isArray(filters[activeFilterField])
              ? filters[activeFilterField]
              : filters[activeFilterField]
              ? [filters[activeFilterField]]
              : []
          }
          onApply={(selected) => {
            setFilters((prev) => ({
              ...prev,
              [activeFilterField]: selected.length > 0 ? selected : undefined,
            }));
          }}
          onClose={() => setActiveFilterField(null)}
        />
      )}

      {/* Saved Views Modal */}
      {savedViewsModalOpen && (
        <SavedViewsModal
          currentConfig={{
            rows,
            columns,
            values,
            filters: usePageFilters ? {} : filters,
            year: effectiveYear,
          }}
          savedViews={savedViews}
          onLoadView={handleLoadView}
          onSaveCurrentView={handleSaveView}
          onDeleteView={handleDeleteView}
          onClose={() => setSavedViewsModalOpen(false)}
        />
      )}
    </div>
  );
}

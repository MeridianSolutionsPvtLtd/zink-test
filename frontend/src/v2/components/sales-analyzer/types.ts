export type DimensionCategory = 'Store' | 'Product' | 'Location' | 'Time';

export interface DimensionMeta {
  field: string;
  label: string;
  category: DimensionCategory;
  dataType: 'string' | 'number' | 'date';
}

export type AggregationType = 'sum' | 'avg' | 'count' | 'min' | 'max' | 'calculated';

export interface MeasureMeta {
  field: string;
  label: string;
  dataType: 'currency' | 'number' | 'percentage';
  allowedAggregations?: AggregationType[];
  defaultAggregation?: AggregationType;
  isLy?: boolean;
  isCalculated?: boolean;
}

export interface ValueConfig {
  field: string;
  aggregation?: AggregationType;
  custom_label?: string;
}

export interface SortConfig {
  field: string;
  direction: 'asc' | 'desc';
}

export interface AnalyzerQueryPayload {
  rows: string[];
  columns: string[];
  values: ValueConfig[];
  filters: Record<string, any>;
  sort?: SortConfig[];
  year?: number;
  search?: string;
}

export interface TreeNode {
  key: string;
  dimension: string;
  value: string;
  level: number;
  metrics: Record<string, number | null>;
  split_metrics?: Record<string, Record<string, number | null>>;
  children: TreeNode[];
  record_count: number;
}

export interface GrandTotalData {
  metrics: Record<string, number | null>;
  split_metrics?: Record<string, Record<string, number | null>>;
  record_count: number;
}

export interface AnalyzerQueryResult {
  ok: boolean;
  error?: string;
  period?: {
    fy: string;
    cy_from: string;
    cy_to: string;
    ly_from: string;
    ly_to: string;
  } | null;
  rows: TreeNode[];
  split_col_keys: string[];
  split_col_dim?: string;
  grand_total: GrandTotalData;
  total_records: number;
  dimensions: string[];
  measures: ValueConfig[];
  /** Requested FY end-year from client (may be out of Analyzer scope). */
  requested_year?: number;
  /** FY actually used after Analyzer visible-year fallback. */
  effective_year?: number;
  year_fallback?: boolean;
}

export interface SavedView {
  id: string;
  name: string;
  description?: string;
  createdAt: string;
  config: {
    rows: string[];
    columns: string[];
    values: ValueConfig[];
    filters: Record<string, any>;
    year?: number;
  };
}

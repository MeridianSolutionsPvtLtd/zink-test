import React, { Fragment, useState, useEffect, useMemo, useRef, useCallback, type ReactNode } from 'react';
import DashboardLayout from '../layouts/DashboardLayout';
import { API_URL } from '../../config';
import {
    Truck,
    Package,
    TrendingUp,
    RefreshCw,
    ArrowRight,
    X,
    Store,
    Box,
    Layers,
    ArrowUpDown,
    ArrowUp,
    ArrowDown,
    BarChart3,
    Download,
} from 'lucide-react';
import { FilterDropdown } from '../components/FilterDropdown';
import {
    BarChart,
    Bar,
    XAxis,
    YAxis,
    CartesianGrid,
    Tooltip,
    ResponsiveContainer,
    Legend,
    PieChart,
    Pie,
    Cell,
} from 'recharts';
import { ReplenishmentLineLoader } from '../components/ReplenishmentLineLoader';
import { StoreClearancePanel } from '../components/transfers/StoreClearancePanel';

const TRANSFERS_DONOR_RECV_COL_COUNT = 15;

/** After browser refresh, restore which tab the user was on. */
const SP_TRANSFERS_UI_KEY = 'sp_transfers_ui_v1';
type TransfersMainTab = 'optimizer' | 'no_sales_donors' | 'store_clearance';
type TransfersSubTab = 'receivers' | 'donors' | 'recommended' | 'current';

function readPersistedTransfersUi(): { main: TransfersMainTab; sub: TransfersSubTab; charts: boolean } {
    if (typeof window === 'undefined') {
        return { main: 'optimizer', sub: 'receivers', charts: false };
    }
    try {
        const raw = window.sessionStorage.getItem(SP_TRANSFERS_UI_KEY);
        if (!raw) {
            return { main: 'optimizer', sub: 'receivers', charts: false };
        }
        const p = JSON.parse(raw) as { main?: string; sub?: string; charts?: boolean };
        const main: TransfersMainTab =
            p.main === 'no_sales_donors' || p.main === 'optimizer' || p.main === 'store_clearance'
                ? p.main
                : 'optimizer';
        const subOk: TransfersSubTab[] = ['receivers', 'donors', 'recommended', 'current'];
        const sub: TransfersSubTab = subOk.includes(p.sub as TransfersSubTab) ? (p.sub as TransfersSubTab) : 'receivers';
        return { main, sub, charts: p.charts === true };
    } catch {
        return { main: 'optimizer', sub: 'receivers', charts: false };
    }
}

/** Same Uiverse-style polyline loader as Replenishments (main recommendations table). */
function TransfersTableLoaderBlock() {
    return (
        <div className="flex min-h-[180px] items-center justify-center py-2">
            <ReplenishmentLineLoader size={80} />
        </div>
    );
}

const SIZES = ['XS', 'S', 'M', 'L', 'XL', 'XXL'];
const normalizeSize = (v: any) => String(v || '').trim().toUpperCase();
const fmtIntCell = (n: unknown) => {
    const v = Number(n);
    return Number.isFinite(v) ? v.toLocaleString() : '—';
};
const fmtOptionalIntCell = (n: unknown) => (n === null || n === undefined || n === '' ? '—' : fmtIntCell(n));

/** Single-line transfer quantity: newer lake uses `transfer_qty_total`; API may normalize to `transfer_qty`. */
function pickTransferLineQty(rec: any): number | null {
    if (rec == null || typeof rec !== 'object') return null;
    const raw = rec.transfer_qty ?? rec.transfer_qty_total ?? rec.receiver_recommended_refill;
    if (raw == null || raw === '') return null;
    const n = Number(raw);
    return Number.isFinite(n) ? n : null;
}

const fmtDaysCell = (n: unknown) => {
    if (n === 999 || n === '999') return 'No sales';
    return fmtIntCell(n);
};

/** Per-size `don_sales_*` / `rec_sales_*` (currency or units); integers vs decimals. */
function fmtStackTotalSales(n: unknown): string {
    if (n == null || n === '') return '—';
    const v = Number(n);
    if (!Number.isFinite(v)) return '—';
    if (Math.abs(v) < 1e-9) return '0';
    if (Number.isInteger(v) || Math.abs(v - Math.round(v)) < 1e-6) {
        return Math.round(v).toLocaleString();
    }
    return v.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 });
}

/** Normalize size token from column suffix: `1_xs`, `01xs` → comparable to `1xs`. */
function _normSalesSizeToken(s: string): string {
    return String(s || '')
        .toLowerCase()
        .replace(/_/g, '')
        .replace(/^0+/, '');
}

/**
 * OneLake / Spark often names columns `don_sales_1XS`, `don_sales_1_XS`, `don_total_sales_1xs`, etc.
 * Match by prefix (don_/rec_) + "sales" + size token — not only exact `don_sales_1xs`.
 */
function getStackSalesField(rec: any, side: 'don' | 'rec', sizeToken: string): unknown {
    if (rec == null) return undefined;
    const want = _normSalesSizeToken(sizeToken);
    if (!want) return undefined;

    const candidates: string[] = [];
    const p1 = `${side}_sales_`;
    const p2 = `${side}_total_sales_`;
    for (const rk of Object.keys(rec)) {
        const kl = rk.toLowerCase();
        if (kl.startsWith(p1)) {
            const suf = _normSalesSizeToken(rk.slice(p1.length));
            if (suf === want) candidates.push(rk);
        } else if (kl.startsWith(p2)) {
            const suf = _normSalesSizeToken(rk.slice(p2.length));
            if (suf === want) candidates.push(rk);
        }
    }
    for (const rk of candidates) {
        const v = rec[rk];
        if (v === null || v === undefined) continue;
        if (typeof v === 'string' && v.trim() === '') continue;
        return v;
    }

    // Exact keys we use in UI (API lowercases to e.g. don_sales_1xs)
    const exact = `${side}_sales_${want}`;
    return getStackField(rec, exact, 'qty');
}

/** Spark may use `rec_barcode_6_XXL`, `rec_barcode_6XXL`, or rarely `rec_barcode_xxl` for XXL. */
function _barcodeSizeSuffixMatches(want: string, keySuffix: string): boolean {
    const w = _normSalesSizeToken(want);
    const s = _normSalesSizeToken(keySuffix);
    if (s === w) return true;
    if (w === '6xxl' && (s === 'xxl' || s === '6xxl')) return true;
    return false;
}

/**
 * `don_barcode_*` / `rec_barcode_*` by size — not only exact `rec_barcode_6xxl`.
 * Picks the first key under that prefix whose suffix matches the size (incl. XXL variants).
 */
function getStackBarcodeField(rec: any, side: 'don' | 'rec', sizeToken: string, exactKey: string): unknown {
    if (rec == null) return undefined;
    const want = _normSalesSizeToken(sizeToken);
    if (!want) return undefined;
    const prefix = `${side}_barcode_`.toLowerCase();
    const found: string[] = [];
    for (const rk of Object.keys(rec)) {
        if (!rk.toLowerCase().startsWith(prefix)) continue;
        const suf = rk.slice(prefix.length);
        if (_barcodeSizeSuffixMatches(want, suf)) found.push(rk);
    }
    for (const rk of found) {
        const v = rec[rk];
        if (v === null || v === undefined) continue;
        if (typeof v === 'string' && v.trim() === '') continue;
        return v;
    }
    return getStackField(rec, exactKey, 'barcode');
}

/** Lake datetime string, or unix seconds / ms, for in-transit meta lines. */
function formatTransferTimestamp(v: unknown): string {
    if (v == null || v === '') return '—';
    if (typeof v === 'number' && Number.isFinite(v)) {
        const ms = v < 1e12 ? v * 1000 : v;
        const d = new Date(ms);
        if (!Number.isNaN(d.getTime())) return d.toLocaleString();
    }
    const s = String(v).trim();
    if (!s) return '—';
    const d2 = new Date(s);
    if (!Number.isNaN(d2.getTime())) return d2.toLocaleString();
    return s;
}

const fmtPriorityCell = (n: unknown) => {
    if (n === null || n === undefined || n === '') return '—';
    const v = Number(n);
    return Number.isFinite(v) ? v.toFixed(2) : '—';
};

function getDisplayPriority(rec: any): 'HIGH' | 'MEDIUM' | 'LOW' {
    const score = Number(rec?.priority_score);
    if (Number.isFinite(score)) {
        if (score > 7) return 'HIGH';
        if (score >= 4) return 'MEDIUM';
        return 'LOW';
    }

    const p = String(rec?.priority || '').trim().toUpperCase();
    if (p === 'HIGH' || p === 'MEDIUM' || p === 'LOW') return p;

    const rp = Number(rec?.receiver_priority);
    if (Number.isFinite(rp)) {
        if (rp >= 10) return 'HIGH';
        if (rp >= 5) return 'MEDIUM';
    }
    return 'LOW';
}

function priorityRankForSort(rec: any): number {
    const p = getDisplayPriority(rec);
    return p === 'HIGH' ? 0 : p === 'MEDIUM' ? 1 : 2;
}

const formatZeroSohCellValue = (value: any) => {
    if (value === null || value === undefined || value === '') return '—';
    if (typeof value !== 'string') return value;

    const trimmed = value.trim();
    const looksLikeIsoDate = /^\d{4}-\d{2}-\d{2}T/.test(trimmed);
    const looksLikeDate = /^\d{4}-\d{2}-\d{2}$/.test(trimmed);
    if (!looksLikeIsoDate && !looksLikeDate) return value;

    const parsed = new Date(trimmed);
    if (Number.isNaN(parsed.getTime())) return value;
    return parsed.toLocaleDateString('en-GB');
};

const CHART_COLORS = {
    receiver: '#f43f5e',
    donor: '#10b981',
    refill: '#6366f1',
    donorSoh: '#14b8a6',
    salesDataDonor: '#3b82f6',
    noSalesDonor: '#f59e0b',
    priorityBar: '#a855f7',
    netMove: '#0ea5e9',
    stateNeed: '#ec4899',
};

function pickZeroSohRowDays(row: any): number {
    const v =
        row?.days_since_last_sale ??
        row?.days_from_last_sales ??
        row?.days_since_last_sales ??
        row?.Days_Since_Last_Sale;
    const n = Number(v);
    return Number.isFinite(n) ? Math.max(0, n) : 0;
}

function bucketDaysSinceSale(days: number): '0-35' | '35-60' | '60-90' | '90+' {
    const d = Number(days);
    if (!Number.isFinite(d) || d < 0) return '0-35';
    if (d < 35) return '0-35';
    if (d < 60) return '35-60';
    if (d < 90) return '60-90';
    return '90+';
}

function classifyDonorSource(raw: unknown): 'sales_data' | 'no_sales' | 'other' {
    const s = String(raw || '').toUpperCase().replace(/\s+/g, '_');
    if (s.includes('NO_SALES') && s.includes('DONOR')) return 'no_sales';
    if (s.includes('SALES_DATA') && s.includes('DONOR')) return 'sales_data';
    if (s === 'NO_SALES_DONOR' || s.includes('NO_SALES_DONOR')) return 'no_sales';
    if (s === 'SALES_DATA_DONOR' || s.includes('SALES_DATA_DONOR')) return 'sales_data';
    if (s.includes('NO_SALES')) return 'no_sales';
    if (s.includes('SALES_DATA')) return 'sales_data';
    return 'other';
}

function truncateLabel(s: string, max = 16) {
    const t = String(s || '').trim();
    if (!t) return '—';
    return t.length <= max ? t : `${t.slice(0, max - 1)}…`;
}

function normTxt(v: unknown): string {
    return String(v || '').trim();
}

function routeMovementType(rec: any): 'same_city' | 'same_state' | 'other_state' {
    const donorCity = normTxt(rec?.donor_city).toLowerCase();
    const recvCity = normTxt(rec?.receiver_city).toLowerCase();
    const donorState = normTxt(rec?.donor_state).toLowerCase();
    const recvState = normTxt(rec?.receiver_state).toLowerCase();
    if (donorCity && recvCity && donorCity === recvCity) return 'same_city';
    if (donorState && recvState && donorState === recvState) return 'same_state';
    return 'other_state';
}

function routeMovementLabel(kind: 'same_city' | 'same_state' | 'other_state'): string {
    if (kind === 'same_city') return 'Same city';
    if (kind === 'same_state') return 'Same state';
    return 'Other state';
}

const sortRowsBySize = (rows: any[]) => {
    const rank = new Map(SIZES.map((s, i) => [s, i]));
    return [...rows].sort((a, b) => {
        const sa = normalizeSize(a?.size);
        const sb = normalizeSize(b?.size);
        const ra = rank.has(sa) ? rank.get(sa)! : 999;
        const rb = rank.has(sb) ? rank.get(sb)! : 999;
        if (ra !== rb) return ra - rb;
        return sa.localeCompare(sb);
    });
};

/** OneLake recommended stack table (e.g. `recommended_transfers_new2`) — stack-style rows (no single `size` column). */
/** API (`_normalize_row`) lowercases all keys — must match e.g. `don_barcode_1xs` not `don_barcode_1XS`. */
/** Suffix after `don_sales_` / `rec_sales_` for flexible column matching (Spark may use `1_XS`, etc.). */
const REC_STACK_SIZE_KEYS = [
    {
        label: 'XS',
        salesSize: '1xs',
        req: 'req_1xs_qty', tr: 'transfer_1xs_qty',
        donBarcode: 'don_barcode_1xs', donSoh: 'don_soh_1xs',
        recBarcode: 'rec_barcode_1xs', recSoh: 'rec_soh_1xs',
        donSales: 'don_sales_1xs', recSales: 'rec_sales_1xs',
    },
    {
        label: 'S',
        salesSize: '2s',
        req: 'req_2s_qty', tr: 'transfer_2s_qty',
        donBarcode: 'don_barcode_2s', donSoh: 'don_soh_2s',
        recBarcode: 'rec_barcode_2s', recSoh: 'rec_soh_2s',
        donSales: 'don_sales_2s', recSales: 'rec_sales_2s',
    },
    {
        label: 'M',
        salesSize: '3m',
        req: 'req_3m_qty', tr: 'transfer_3m_qty',
        donBarcode: 'don_barcode_3m', donSoh: 'don_soh_3m',
        recBarcode: 'rec_barcode_3m', recSoh: 'rec_soh_3m',
        donSales: 'don_sales_3m', recSales: 'rec_sales_3m',
    },
    {
        label: 'L',
        salesSize: '4l',
        req: 'req_4l_qty', tr: 'transfer_4l_qty',
        donBarcode: 'don_barcode_4l', donSoh: 'don_soh_4l',
        recBarcode: 'rec_barcode_4l', recSoh: 'rec_soh_4l',
        donSales: 'don_sales_4l', recSales: 'rec_sales_4l',
    },
    {
        label: 'XL',
        salesSize: '5xl',
        req: 'req_5xl_qty', tr: 'transfer_5xl_qty',
        donBarcode: 'don_barcode_5xl', donSoh: 'don_soh_5xl',
        recBarcode: 'rec_barcode_5xl', recSoh: 'rec_soh_5xl',
        donSales: 'don_sales_5xl', recSales: 'rec_sales_5xl',
    },
    {
        label: 'XXL',
        salesSize: '6xxl',
        req: 'req_6xxl_qty', tr: 'transfer_6xxl_qty',
        donBarcode: 'don_barcode_6xxl', donSoh: 'don_soh_6xxl',
        recBarcode: 'rec_barcode_6xxl', recSoh: 'rec_soh_6xxl',
        donSales: 'don_sales_6xxl', recSales: 'rec_sales_6xxl',
    },
] as const;

function isNewRecommendedStackRec(rec: any): boolean {
    if (!rec || typeof rec !== 'object') return false;
    if (rec.match_type != null && String(rec.match_type).trim() !== '') return true;
    if (rec.required_stack_qty != null) return true;
    // If any of the per-size stack keys exist (either uppercase style or legacy lowercase), treat as stack rec
    for (const row of REC_STACK_SIZE_KEYS) {
        if (rec[row.tr] != null || rec[row.req] != null) return true;
        const trLower = String(row.tr).toLowerCase();
        const reqLower = String(row.req).toLowerCase();
        if (rec[trLower] != null || rec[reqLower] != null) return true;
    }
    return false;
}

/**
 * Per-size SOH: new lake uses `don_soh_*` / `rec_soh_*`; legacy used `don_storesoh_*` / `rec_storesoh_*`.
 * Also handle `storesoh` vs `store_soh` spellings.
 */
function _stackSohKeyFallbacks(canonical: string): string[] {
    const c = String(canonical).trim();
    const out: string[] = [c];
    if (c.includes('storesoh')) out.push(c.replace(/storesoh/gi, 'store_soh'));
    if (c.includes('store_soh')) out.push(c.replace(/store_soh/gi, 'storesoh'));
    if (/^don_soh_/i.test(c)) out.push(c.replace(/^don_soh_/i, 'don_storesoh_'));
    if (/^rec_soh_/i.test(c)) out.push(c.replace(/^rec_soh_/i, 'rec_storesoh_'));
    if (/^don_storesoh_/i.test(c)) out.push(c.replace(/^don_storesoh_/i, 'don_soh_'));
    if (/^rec_storesoh_/i.test(c)) out.push(c.replace(/^rec_storesoh_/i, 'rec_soh_'));
    return [...new Set(out)];
}

/**
 * Find value on a recommendation row: exact key, case-insensitive key match, then SOH key spelling variants.
 */
function getStackField(rec: any, primaryKey: string, kind: 'qty' | 'soh' | 'barcode'): any {
    if (rec == null) return undefined;
    const wantKeys = kind === 'soh' ? _stackSohKeyFallbacks(primaryKey) : [primaryKey];
    for (const wk of wantKeys) {
        const w = wk.toLowerCase();
        for (const rk of Object.keys(rec)) {
            if (rk.toLowerCase() === w) {
                const v = rec[rk];
                if (v === null || v === undefined) continue;
                if (typeof v === 'string' && v.trim() === '' && (kind === 'barcode' || kind === 'soh')) continue;
                return v;
            }
        }
    }
    return undefined;
}

/** EAN/UPC: backend may send number or float; avoid scientific-notation glitches. */
function formatStackBarcode(v: unknown): string {
    if (v == null) return '—';
    if (typeof v === 'number' && Number.isFinite(v)) {
        if (v >= 1e8) return String(Math.round(v));
        if (v === 0) return '—';
        return String(v);
    }
    const s = String(v).trim();
    if (!s || s.toLowerCase() === 'null' || s.toLowerCase() === 'undefined' || s.toLowerCase() === 'nan') return '—';
    return s;
}

function stackRecQty(rec: any, k: string): number {
    const v = getStackField(rec, k, 'qty');
    const raw = v !== undefined ? v : rec?.[k] ?? rec?.[k.toLowerCase()];
    const n = Number(raw);
    return Number.isFinite(n) ? n : 0;
}

/** Non-zero size columns on a current_transfers row (only if copied from full stack rec). */
function inTransitRowHasSizeBreakdown(rec: any): boolean {
    if (!rec || typeof rec !== 'object') return false;
    return REC_STACK_SIZE_KEYS.some((row) => {
        const dn = Number(getStackField(rec, row.donSoh, 'soh'));
        const hasDonSoh = Number.isFinite(dn) && dn > 0;
        return stackRecQty(rec, row.req) > 0 || stackRecQty(rec, row.tr) > 0 || hasDonSoh;
    });
}

/** dbo.current_transfers column order (OneLake); fallbacks for mapped API fields. */
const IN_TRANSIT_ONE_LAKE_COLS: {
    id: string;
    keys: string[];
    label: string;
    fmt: 'text' | 'ts' | 'score' | 'int';
}[] = [
    { id: 'transfer_id', keys: ['transfer_id', 'Transfer_ID', 'transfer_ID'], label: 'transfer_id', fmt: 'text' },
    { id: 'receiver_site_id', keys: ['receiver_site_id', 'receiver_store'], label: 'receiver_site_id', fmt: 'text' },
    { id: 'receiver_site_name', keys: ['receiver_site_name', 'receiver_store_name'], label: 'receiver_site_name', fmt: 'text' },
    { id: 'donor_site_id', keys: ['donor_site_id', 'donor_store'], label: 'donor_site_id', fmt: 'text' },
    { id: 'donor_site_name', keys: ['donor_site_name', 'donor_store_name'], label: 'donor_site_name', fmt: 'text' },
    { id: 'sku', keys: ['sku'], label: 'sku', fmt: 'text' },
    { id: 'receiver_city', keys: ['receiver_city'], label: 'receiver_city', fmt: 'text' },
    { id: 'receiver_state', keys: ['receiver_state'], label: 'receiver_state', fmt: 'text' },
    { id: 'donor_city', keys: ['donor_city'], label: 'donor_city', fmt: 'text' },
    { id: 'donor_state', keys: ['donor_state'], label: 'donor_state', fmt: 'text' },
    { id: 'store_type', keys: ['store_type'], label: 'store_type', fmt: 'text' },
    {
        id: 'transfer_qty',
        keys: ['transfer_qty', 'transfer_qty_total', 'receiver_recommended_refill'],
        label: 'transfer_qty',
        fmt: 'int',
    },
    { id: 'match_type', keys: ['match_type'], label: 'match_type', fmt: 'text' },
    { id: 'priority_score', keys: ['priority_score'], label: 'priority_score', fmt: 'score' },
    { id: 'status', keys: ['status', 'transfer_status'], label: 'status', fmt: 'text' },
    { id: 'transfer_date', keys: ['transfer_date', 'Transfer_Date'], label: 'transfer_date', fmt: 'text' },
    { id: 'created_at', keys: ['created_at'], label: 'created_at (approved)', fmt: 'ts' },
    { id: 'recommendation_created_at', keys: ['recommendation_created_at'], label: 'recommendation_created_at', fmt: 'ts' },
];

function pickInTransitField(item: any, keys: string[]): unknown {
    for (const k of keys) {
        const v = item?.[k];
        if (v != null && v !== '') return v;
    }
    return null;
}

function formatInTransitCell(
    item: any,
    col: (typeof IN_TRANSIT_ONE_LAKE_COLS)[number]
): string {
    const v = pickInTransitField(item, col.keys);
    if (v == null || v === '') return '—';
    switch (col.fmt) {
        case 'ts':
            return formatTransferTimestamp(v);
        case 'score':
            return fmtPriorityCell(v);
        case 'int':
            return fmtIntCell(v);
        default:
            return String(v);
    }
}

/** One line in stack verify modal: short label, optional title hint, value */
function StackModalFact({
    label,
    hint,
    value,
    valueClassName = 'text-slate-800 dark:text-slate-100',
}: {
    label: string;
    hint?: string;
    value: ReactNode;
    valueClassName?: string;
}) {
    return (
        <div className="text-[10px] leading-snug" title={hint || undefined}>
            <span className="text-slate-500 dark:text-slate-400">{label}</span>
            <div className={`mt-0.5 font-medium tabular-nums ${valueClassName}`}>{value}</div>
        </div>
    );
}

export default function TransfersTab() {
    const [receivers, setReceivers] = useState<any[]>([]);
    const [donors, setDonors] = useState<any[]>([]);
    const [recommendations, setRecommendations] = useState<any[]>([]);
    const [currentTransfers, setCurrentTransfers] = useState<any[]>([]);
    /** One tab at a time — data loads only for the active sub-tab. */
    const [optimizerSubTab, setOptimizerSubTab] = useState<TransfersSubTab>(
        () => readPersistedTransfersUi().sub
    );
    /** Off by default: combined charts (Recharts) are heavy. Toggle on to render charts; may load receivers + donors for combined views. */
    const [showDecisionCharts] = useState(false);
    const [decisionChartsReady, setDecisionChartsReady] = useState(false);
    const [decisionChartsLoading, setDecisionChartsLoading] = useState(false);
    const decisionChartsFetchGenRef = useRef(0);
    const [loadingReceivers, setLoadingReceivers] = useState(false);
    const [loadingReceiversMore, setLoadingReceiversMore] = useState(false);
    const [loadingDonors, setLoadingDonors] = useState(false);
    const [loadingDonorsMore, setLoadingDonorsMore] = useState(false);
    const [loadingRecs, setLoadingRecs] = useState(false);
    const [loadingRecsMore, setLoadingRecsMore] = useState(false);
    const [loadingCurrent, setLoadingCurrent] = useState(false);
    const [receiversTotalHint, setReceiversTotalHint] = useState<number | null>(null);
    const [donorsTotalHint, setDonorsTotalHint] = useState<number | null>(null);
    const [recsTotalHint, setRecsTotalHint] = useState<number | null>(null);
    const anyOptimizerListLoading =
        loadingReceivers ||
        loadingReceiversMore ||
        loadingDonors ||
        loadingDonorsMore ||
        loadingRecs ||
        loadingRecsMore ||
        loadingCurrent;
    const transfersFetchGenRef = useRef(0);
    /** Chart bulk R+D preload uses its own gen counter so it never cancels (or gets cancelled by) optimizer tab fetches. */
    const chartBulkFetchGenRef = useRef(0);
    const optimizerDataSigRef = useRef<Partial<Record<TransfersSubTab, string>>>({});
    const lastOptimizerFilterKeyRef = useRef<string>('');
    const [selectedRec, setSelectedRec] = useState<any | null>(null);
    const [selectedRecGroup, setSelectedRecGroup] = useState<any | null>(null);
    const [modalSoh, setModalSoh] = useState<{ donor: any[], receiver: any[] }>({ donor: [], receiver: [] });
    const [loadingModalSoh, setLoadingModalSoh] = useState(false);
    const [creatingTransferId, setCreatingTransferId] = useState<number | null>(null);
    const [downloadingExcel, setDownloadingExcel] = useState(false);
    const [approvingAll, setApprovingAll] = useState(false);
    const [approvingRoute, setApprovingRoute] = useState(false);
    /** No-sales rows for chart 4 — loaded on demand (keeps initial page light). */
    const [zeroSohHistogramSample, setZeroSohHistogramSample] = useState<any[]>([]);
    const [loadingNoSalesSample, setLoadingNoSalesSample] = useState(false);
    const [noSalesSampleLoaded, setNoSalesSampleLoaded] = useState(false);

    // Local Filters
    const [recvSite, setRecvSite] = useState('All Sites');
    const [donorSite, setDonorSite] = useState('All Sites');
    /** Debounced for API: avoids a full refetch on every intermediate dropdown value. */
    const [debouncedRecvSite, setDebouncedRecvSite] = useState('All Sites');
    const [debouncedDonorSite, setDebouncedDonorSite] = useState('All Sites');

    // Tab State (main + sub restored from sessionStorage on refresh)
    const [activeTab, setActiveTab] = useState<TransfersMainTab>(() => readPersistedTransfersUi().main);
    const [zeroSohData, setZeroSohData] = useState<any[]>([]);
    const [zeroSohCols, setZeroSohCols] = useState<string[]>([]);
    const [loadingZeroSoh, setLoadingZeroSoh] = useState(false);
    const [zeroSohError, setZeroSohError] = useState<string | null>(null);
    const [zeroSohTotal, setZeroSohTotal] = useState(0);
    const [zeroSohSummary, setZeroSohSummary] = useState<{
        total_soh: number;
        total_soh_no_transfer_date: number;
        total_soh_with_transfer_date: number;
        total_revenue: number;
        top_skus: any[];
        top_sites_by_amount: any[];
        top_skus_by_amount: any[];
    }>({
        total_soh: 0,
        total_soh_no_transfer_date: 0,
        total_soh_with_transfer_date: 0,
        total_revenue: 0,
        top_skus: [],
        top_sites_by_amount: [],
        top_skus_by_amount: [],
    });
    const [zeroDaysFilter, setZeroDaysFilter] = useState<number>(0);
    const [zeroDaysMax, setZeroDaysMax] = useState<number>(0);
    const [zeroSohColumnFilters, setZeroSohColumnFilters] = useState<Record<string, string>>({});
    const [zeroFullyLoaded, setZeroFullyLoaded] = useState(false);
    const [receiverSort, setReceiverSort] = useState<{ key: string; dir: 'asc' | 'desc' }>({ key: 'need_gap', dir: 'desc' });
    const [donorSort, setDonorSort] = useState<{ key: string; dir: 'asc' | 'desc' }>({ key: 'days_from_last_sales', dir: 'desc' });
    const zeroSohFetchInFlightRef = useRef(false);
    const zeroSohFetchSeqRef = useRef(0);
    const zeroSohAbortControllerRef = useRef<AbortController | null>(null);
    /** Larger pages + hard cap — full 60k auto-pagination starved Inventory Analytics APIs. */
    const ZERO_SOH_LIMIT = 1500;
    const ZERO_SOH_MAX_ROWS = 6000;
    /** Paged /data and /recommendations so the browser is not choked on one huge JSON. */
    const TRANSFERS_DATA_CHUNK = 300;
    const TRANSFERS_REC_CHUNK = 200;

    const recommendationsSortedByPriority = useMemo(() => {
        const list = [...recommendations];
        list.sort((a, b) => {
            const ra = priorityRankForSort(a);
            const rb = priorityRankForSort(b);
            if (ra !== rb) return ra - rb;
            const sa = Number(a?.priority_score);
            const sb = Number(b?.priority_score);
            if (Number.isFinite(sa) && Number.isFinite(sb) && sb !== sa) return sb - sa;
            return 0;
        });
        return list;
    }, [recommendations]);

    const groupedRecommendations = useMemo(() => {
        const grouped = new Map<string, any>();
        for (const rec of recommendationsSortedByPriority) {
            const donorSiteId = normTxt(rec?.donor_store || rec?.donor_site_id || rec?.donor_store_name);
            const receiverSiteId = normTxt(rec?.receiver_store || rec?.receiver_site_id || rec?.receiver_store_name);
            const key = `${donorSiteId}__${receiverSiteId}`;
            if (!grouped.has(key)) {
                grouped.set(key, {
                    key,
                    donorSiteId,
                    donorSiteName: normTxt(rec?.donor_store_name || rec?.donor_site_name || donorSiteId),
                    donorCity: normTxt(rec?.donor_city),
                    donorState: normTxt(rec?.donor_state),
                    receiverSiteId,
                    receiverSiteName: normTxt(rec?.receiver_store_name || rec?.receiver_site_name || receiverSiteId),
                    receiverCity: normTxt(rec?.receiver_city),
                    receiverState: normTxt(rec?.receiver_state),
                    movementType: routeMovementType(rec),
                    rows: [] as any[],
                });
            }
            const g = grouped.get(key);
            g.rows.push(rec);
        }
        const out = Array.from(grouped.values()).map((g) => {
            const skuSet = new Set(g.rows.map((r: any) => normTxt(r?.sku)).filter(Boolean));
            const barcodeSet = new Set(g.rows.map((r: any) => normTxt(r?.barcode)).filter(Boolean));
            const skuNames = Array.from(skuSet).sort((a: any, b: any) => String(a).localeCompare(String(b)));
            const totalTransferQty = g.rows.reduce((sum: number, r: any) => {
                const q = pickTransferLineQty(r);
                return sum + (q != null ? Number(q) : 0);
            }, 0);
            return {
                ...g,
                uniqueSkuCount: skuSet.size,
                skuNames,
                uniqueBarcodeCount: barcodeSet.size,
                totalTransferQty,
                topPriority: getDisplayPriority(g.rows[0]),
            };
        });
        out.sort((a, b) => {
            const pa = a.topPriority === 'HIGH' ? 0 : a.topPriority === 'MEDIUM' ? 1 : 2;
            const pb = b.topPriority === 'HIGH' ? 0 : b.topPriority === 'MEDIUM' ? 1 : 2;
            if (pa !== pb) return pa - pb;
            return b.totalTransferQty - a.totalTransferQty || b.uniqueSkuCount - a.uniqueSkuCount;
        });
        return out;
    }, [recommendationsSortedByPriority]);

    useEffect(() => {
        const id = window.setTimeout(() => setDebouncedRecvSite(recvSite), 350);
        return () => window.clearTimeout(id);
    }, [recvSite]);

    useEffect(() => {
        const id = window.setTimeout(() => setDebouncedDonorSite(donorSite), 350);
        return () => window.clearTimeout(id);
    }, [donorSite]);

    useEffect(() => {
        try {
            window.sessionStorage.setItem(
                SP_TRANSFERS_UI_KEY,
                JSON.stringify({
                    main: activeTab,
                    sub: optimizerSubTab,
                    charts: showDecisionCharts,
                })
            );
        } catch {
            /* private mode or quota */
        }
    }, [activeTab, optimizerSubTab, showDecisionCharts]);

    const sortIcon = (active: boolean, dir: 'asc' | 'desc') => {
        if (!active) return <ArrowUpDown className="w-3 h-3 inline ml-1 text-slate-400" />;
        return dir === 'asc'
            ? <ArrowUp className="w-3 h-3 inline ml-1 text-indigo-500" />
            : <ArrowDown className="w-3 h-3 inline ml-1 text-indigo-500" />;
    };

    const toggleSort = (
        setter: React.Dispatch<React.SetStateAction<{ key: string; dir: 'asc' | 'desc' }>>,
        key: string
    ) => {
        setter((prev) => prev.key === key ? { key, dir: prev.dir === 'asc' ? 'desc' : 'asc' } : { key, dir: 'desc' });
    };

    const fetchZeroSohData = async () => {
        const fetchSeq = zeroSohFetchSeqRef.current + 1;
        zeroSohFetchSeqRef.current = fetchSeq;
        zeroSohAbortControllerRef.current?.abort();
        const abortController = new AbortController();
        zeroSohAbortControllerRef.current = abortController;
        zeroSohFetchInFlightRef.current = true;
        setLoadingZeroSoh(true);
        setZeroSohError(null);
        setZeroFullyLoaded(false);
        try {
            const allRows: any[] = [];
            let hasMore = true;
            let offset = 0;
            let latestPayload: any = null;
            let firstChunkLoaded = false;
            while (hasMore) {
                const params = new URLSearchParams();
                params.set('limit', String(ZERO_SOH_LIMIT));
                params.set('offset', String(offset));
                if (zeroDaysFilter > 0) params.set('max_days', String(zeroDaysFilter));
                const query = params.toString() ? `?${params.toString()}` : '';
                const res = await fetch(`${API_URL}/transfers-recom/zero-soh${query}`, { signal: abortController.signal });
                const data = await res.json();
                if (zeroSohFetchSeqRef.current !== fetchSeq) return;
                latestPayload = data;
                if (data.status !== 'success') {
                    setZeroSohError(data.message || "Failed to fetch data");
                    if (data.columns) setZeroSohCols(data.columns);
                    break;
                }
                const chunk = Array.isArray(data.data) ? data.data : [];
                allRows.push(...chunk);
                hasMore = Boolean(data.has_more) && allRows.length < ZERO_SOH_MAX_ROWS;
                offset += chunk.length;
                setZeroSohData([...allRows]);
                if (!firstChunkLoaded) {
                    firstChunkLoaded = true;
                    setZeroSohCols(data.columns || []);
                    setZeroSohTotal(Number(data.total || allRows.length || 0));
                    setZeroSohSummary(
                        data.summary || {
                            total_soh: 0,
                            total_soh_no_transfer_date: 0,
                            total_soh_with_transfer_date: 0,
                            total_revenue: 0,
                            top_skus: [],
                            top_sites_by_amount: [],
                            top_skus_by_amount: [],
                        }
                    );
                    const maxDaysFromApi = Number(data.max_days_available || 0);
                    // Only track slider max — do NOT auto-change zeroDaysFilter (that
                    // re-triggers this effect and aborts/restarts a multi-page download).
                    setZeroDaysMax(maxDaysFromApi);
                    setLoadingZeroSoh(false);
                }
                if (chunk.length === 0) hasMore = false;
            }
            if (zeroSohFetchSeqRef.current !== fetchSeq) return;
            if (latestPayload?.status === 'success') {
                setZeroSohData(allRows);
                setZeroSohCols(latestPayload.columns || []);
                setZeroSohTotal(Number(latestPayload.total || allRows.length || 0));
                setZeroSohSummary(
                    latestPayload.summary || {
                        total_soh: 0,
                        total_soh_no_transfer_date: 0,
                        total_soh_with_transfer_date: 0,
                        total_revenue: 0,
                        top_skus: [],
                        top_sites_by_amount: [],
                        top_skus_by_amount: [],
                    }
                );
                const maxDaysFromApi = Number(latestPayload.max_days_available || 0);
                setZeroDaysMax(maxDaysFromApi);
                setZeroDaysFilter((prev) => {
                    if (maxDaysFromApi <= 0) return 0;
                    if (prev === 0 || prev > maxDaysFromApi) return maxDaysFromApi;
                    return prev;
                });
                setZeroFullyLoaded(true);
                setLoadingZeroSoh(false);
            }
        } catch (err: any) {
            if (err?.name === 'AbortError') return;
            console.error("Failed to fetch 0 SOH data:", err);
            setZeroSohError(err.message || "An unexpected error occurred");
        } finally {
            if (zeroSohFetchSeqRef.current === fetchSeq) {
                setLoadingZeroSoh(false);
                zeroSohFetchInFlightRef.current = false;
                zeroSohAbortControllerRef.current = null;
            }
        }
    };

    const fetchModalSoh = async (rec: any) => {
        setLoadingModalSoh(true);
        try {
            const [donorRes, receiverRes] = await Promise.all([
                fetch(`${API_URL}/transfers-recom/sku-soh?site=${encodeURIComponent(rec.donor_store)}&sku=${encodeURIComponent(rec.sku)}`),
                fetch(`${API_URL}/transfers-recom/sku-soh?site=${encodeURIComponent(rec.receiver_store)}&sku=${encodeURIComponent(rec.sku)}`)
            ]);
            const donorData = await donorRes.json();
            const receiverData = await receiverRes.json();
            setModalSoh({
                donor: donorData.data || [],
                receiver: receiverData.data || []
            });
        } catch (err) {
            console.error("Failed to fetch modal SOH:", err);
            setModalSoh({ donor: [], receiver: [] });
        } finally {
            setLoadingModalSoh(false);
        }
    };

    // Stack verify uses only fields from `RECOMMENDED_TRANSFERS_TABLE` (no /sku-soh). Non-stack still loads sku-soh below.
    useEffect(() => {
        if (!selectedRec) return;
        if (isNewRecommendedStackRec(selectedRec)) {
            setLoadingModalSoh(false);
            setModalSoh({ donor: [], receiver: [] });
        } else {
            fetchModalSoh(selectedRec);
        }
    }, [selectedRec]);

    const fetchQueryString = useCallback(() => {
        const params = new URLSearchParams();
        if (debouncedRecvSite !== 'All Sites') params.set('recv_site', debouncedRecvSite);
        if (debouncedDonorSite !== 'All Sites') params.set('donor_site', debouncedDonorSite);
        const s = params.toString();
        return s ? `?${s}` : '';
    }, [debouncedRecvSite, debouncedDonorSite]);

    const optimizerFilterKey = useMemo(
        () => `${debouncedRecvSite}|||${debouncedDonorSite}`,
        [debouncedRecvSite, debouncedDonorSite]
    );

    // When charts are enabled and filters change, load Receivers + Donors fully once,
    // then show charts only after both datasets are completely fetched.
    useEffect(() => {
        if (activeTab !== 'optimizer' || !showDecisionCharts) {
            setDecisionChartsReady(false);
            setDecisionChartsLoading(false);
            return;
        }
        const fk = optimizerFilterKey;
        const needR = optimizerDataSigRef.current.receivers !== fk;
        const needD = optimizerDataSigRef.current.donors !== fk;

        // If already fully loaded for this filter, we can render immediately.
        if (!needR && !needD) {
            setDecisionChartsReady(true);
            setDecisionChartsLoading(false);
            return;
        }

        const myGen = ++decisionChartsFetchGenRef.current;
        setDecisionChartsReady(false);
        setDecisionChartsLoading(true);

        void (async () => {
            try {
                await new Promise<void>((r) => setTimeout(r, 250));
                if (decisionChartsFetchGenRef.current !== myGen) return;
                await loadDecisionChartData();
                if (decisionChartsFetchGenRef.current === myGen) setDecisionChartsReady(true);
            } finally {
                if (decisionChartsFetchGenRef.current === myGen) setDecisionChartsLoading(false);
            }
        })();
        // eslint-disable-next-line react-hooks/exhaustive-deps -- loadDecisionChartData closes over state; gate with gens
    }, [activeTab, showDecisionCharts, optimizerFilterKey]);

    /** No-sales histogram slice (small cap) — on demand only. Optional site filter if one store is selected. */
    const fetchNoSalesHistogramSample = useCallback(async () => {
        setLoadingNoSalesSample(true);
        try {
            const u = new URLSearchParams({ limit: '400', offset: '0' });
            if (debouncedRecvSite !== 'All Sites') u.set('site', debouncedRecvSite);
            else if (debouncedDonorSite !== 'All Sites') u.set('site', debouncedDonorSite);
            const res = await fetch(`${API_URL}/transfers-recom/zero-soh?${u.toString()}`);
            if (!res.ok) {
                setZeroSohHistogramSample([]);
                return;
            }
            const zd = await res.json();
            if (zd.status === 'success' && Array.isArray(zd.data)) {
                setZeroSohHistogramSample(zd.data);
                setNoSalesSampleLoaded(true);
            } else {
                setZeroSohHistogramSample([]);
            }
        } catch {
            setZeroSohHistogramSample([]);
        } finally {
            setLoadingNoSalesSample(false);
        }
    }, [debouncedRecvSite, debouncedDonorSite]);

    const yieldToMain = () => new Promise<void>((r) => window.setTimeout(r, 0));

    const mergeParams = (init: string) => new URLSearchParams(init);
    const buildDataQuery = (init: string, ro: number, ddo: number, rL: string, dL: string) => {
        const p = mergeParams(init);
        p.set('r_off', String(ro));
        p.set('d_off', String(ddo));
        p.set('r_limit', rL);
        p.set('d_limit', dL);
        const s = p.toString();
        return s ? `?${s}` : '';
    };

    const fetchReceiversData = async (
        gen: number,
        forceRefresh: boolean,
        filterKey: string,
        genScopeRef: React.MutableRefObject<number> = transfersFetchGenRef,
    ) => {
        const baseQs = fetchQueryString().replace(/^\?/, '');
        setLoadingReceivers(true);
        setLoadingReceiversMore(false);
        setReceiversTotalHint(null);
        if (forceRefresh) setReceivers([]);

        let accR: any[] = [];
        let rOff = 0;
        let rCont = true;
        let dataFirst = true;
        try {
            while (rCont && gen === genScopeRef.current) {
                if (!dataFirst) setLoadingReceiversMore(true);
                const dataQuery = buildDataQuery(
                    baseQs,
                    rOff,
                    0,
                    String(TRANSFERS_DATA_CHUNK),
                    '0'
                );
                const dataRes = await fetch(`${API_URL}/transfers-recom/data${dataQuery}`);
                const data = await dataRes.json();
                if (gen !== genScopeRef.current) return;

                if (data.status === 'success') {
                    const cr = Array.isArray(data.receivers) ? data.receivers : [];
                    accR = accR.concat(cr);
                    rCont = Boolean(data.receivers_has_more);
                    if (rCont) rOff += TRANSFERS_DATA_CHUNK;
                    if (typeof data.receivers_total === 'number') setReceiversTotalHint(data.receivers_total);
                    setReceivers([...accR]);
                    if (dataFirst) {
                        setLoadingReceivers(false);
                        dataFirst = false;
                    }
                } else {
                    if (forceRefresh) setReceivers([]);
                    break;
                }
                if (dataFirst) {
                    dataFirst = false;
                }
                if (!rCont) break;
                await yieldToMain();
            }
            if (gen !== genScopeRef.current) return;
            const nextR =
                !forceRefresh && accR.length === 0 && receivers.length > 0 ? receivers : accR;
            setReceivers(nextR);
            optimizerDataSigRef.current.receivers = filterKey;
        } catch (err) {
            console.error('Failed to fetch receivers:', err);
            if (forceRefresh) setReceivers([]);
        } finally {
            if (gen === genScopeRef.current) {
                setLoadingReceivers(false);
                setLoadingReceiversMore(false);
            }
        }
    };

    const fetchDonorsData = async (
        gen: number,
        forceRefresh: boolean,
        filterKey: string,
        genScopeRef: React.MutableRefObject<number> = transfersFetchGenRef,
    ) => {
        const baseQs = fetchQueryString().replace(/^\?/, '');
        setLoadingDonors(true);
        setLoadingDonorsMore(false);
        setDonorsTotalHint(null);
        if (forceRefresh) setDonors([]);

        let accD: any[] = [];
        let dOff = 0;
        let dCont = true;
        let dataFirst = true;
        try {
            while (dCont && gen === genScopeRef.current) {
                if (!dataFirst) setLoadingDonorsMore(true);
                const dataQuery = buildDataQuery(
                    baseQs,
                    0,
                    dOff,
                    '0',
                    String(TRANSFERS_DATA_CHUNK)
                );
                const dataRes = await fetch(`${API_URL}/transfers-recom/data${dataQuery}`);
                const data = await dataRes.json();
                if (gen !== genScopeRef.current) return;

                if (data.status === 'success') {
                    const cd = Array.isArray(data.donors) ? data.donors : [];
                    accD = accD.concat(cd);
                    dCont = Boolean(data.donors_has_more);
                    if (dCont) dOff += TRANSFERS_DATA_CHUNK;
                    if (typeof data.donors_total === 'number') setDonorsTotalHint(data.donors_total);
                    setDonors([...accD]);
                    if (dataFirst) {
                        setLoadingDonors(false);
                        dataFirst = false;
                    }
                } else {
                    if (forceRefresh) setDonors([]);
                    break;
                }
                if (dataFirst) dataFirst = false;
                if (!dCont) break;
                await yieldToMain();
            }
            if (gen !== genScopeRef.current) return;
            const nextD = !forceRefresh && accD.length === 0 && donors.length > 0 ? donors : accD;
            setDonors(nextD);
            optimizerDataSigRef.current.donors = filterKey;
        } catch (err) {
            console.error('Failed to fetch donors:', err);
            if (forceRefresh) setDonors([]);
        } finally {
            if (gen === genScopeRef.current) {
                setLoadingDonors(false);
                setLoadingDonorsMore(false);
            }
        }
    };

    const fetchRecommendationsData = async (gen: number, forceRefresh: boolean, filterKey: string) => {
        const baseQs = fetchQueryString().replace(/^\?/, '');
        setLoadingRecs(true);
        setLoadingRecsMore(false);
        setRecsTotalHint(null);
        if (forceRefresh) setRecommendations([]);

        let accRec: any[] = [];
        let off = 0;
        let hasMoreRecs = true;
        let recFirst = true;
        try {
            while (hasMoreRecs && gen === transfersFetchGenRef.current) {
                if (!recFirst) setLoadingRecsMore(true);
                const rp = mergeParams(baseQs);
                rp.set('limit', String(TRANSFERS_REC_CHUNK));
                rp.set('offset', String(off));
                const rqs = rp.toString() ? `?${rp.toString()}` : '';
                const recRes = await fetch(`${API_URL}/transfers-recom/recommendations${rqs}`);
                const recData = await recRes.json();
                if (gen !== transfersFetchGenRef.current) return;

                if (recData.status !== 'success') {
                    if (forceRefresh) setRecommendations([]);
                    break;
                }
                const chunk = Array.isArray(recData.data) ? recData.data : [];
                accRec = accRec.concat(chunk);
                if (typeof recData.total === 'number') setRecsTotalHint(recData.total);
                if (Object.prototype.hasOwnProperty.call(recData, 'has_more')) {
                    hasMoreRecs = Boolean(recData.has_more);
                } else {
                    hasMoreRecs = false;
                }
                off += chunk.length;
                setRecommendations([...accRec]);
                if (recFirst) {
                    setLoadingRecs(false);
                    recFirst = false;
                }
                if (!hasMoreRecs) break;
                await yieldToMain();
            }
            if (gen !== transfersFetchGenRef.current) return;
            const nextSafe =
                !forceRefresh && accRec.length === 0 && recommendations.length > 0 ? recommendations : accRec;
            setRecommendations(nextSafe);
            optimizerDataSigRef.current.recommended = filterKey;
        } catch (err) {
            console.error('Failed to fetch recommendations:', err);
            if (gen === transfersFetchGenRef.current && forceRefresh) setRecommendations([]);
        } finally {
            if (gen === transfersFetchGenRef.current) {
                setLoadingRecs(false);
                setLoadingRecsMore(false);
            }
        }
    };

    const fetchCurrentData = async (gen: number, forceRefresh: boolean, filterKey: string) => {
        setLoadingCurrent(true);
        if (forceRefresh) setCurrentTransfers([]);
        try {
            const p = new URLSearchParams(fetchQueryString().replace(/^\?/, ''));
            // Server caches current_transfers ~120s; always bypass so In transit matches the lake
            p.set('refresh', '1');
            const q = p.toString();
            const curRes = await fetch(`${API_URL}/transfers-recom/current?${q}`);
            const curData = await curRes.json();
            if (gen !== transfersFetchGenRef.current) return;
            if (curData?.status === 'success' && Array.isArray(curData.data)) {
                // Always show API result for In transit; do not keep stale rows when the lake is empty
                // (earlier we preserved previous rows when the list was [] — that hid cleared tables).
                setCurrentTransfers(curData.data);
            } else {
                setCurrentTransfers([]);
            }
            optimizerDataSigRef.current.current = filterKey;
        } catch (err) {
            console.error('Failed to fetch current transfers:', err);
            if (gen === transfersFetchGenRef.current && forceRefresh) setCurrentTransfers([]);
        } finally {
            if (gen === transfersFetchGenRef.current) setLoadingCurrent(false);
        }
    };

    const loadDecisionChartData = async () => {
        const fk = optimizerFilterKey;
        const g1 = ++chartBulkFetchGenRef.current;
        await fetchReceiversData(g1, true, fk, chartBulkFetchGenRef);
        if (chartBulkFetchGenRef.current !== g1) return;
        const g2 = ++chartBulkFetchGenRef.current;
        await fetchDonorsData(g2, true, fk, chartBulkFetchGenRef);
    };

    const loadOptimizerSubTab = async (tab: TransfersSubTab, forceRefresh: boolean) => {
        const fk = optimizerFilterKey;
        if (!forceRefresh && optimizerDataSigRef.current[tab] === fk) return;

        const gen = ++transfersFetchGenRef.current;
        if (forceRefresh) {
            setNoSalesSampleLoaded(false);
            setZeroSohHistogramSample([]);
        }

        if (tab === 'receivers') await fetchReceiversData(gen, forceRefresh, fk);
        else if (tab === 'donors') await fetchDonorsData(gen, forceRefresh, fk);
        else if (tab === 'recommended') await fetchRecommendationsData(gen, forceRefresh, fk);
        else if (tab === 'current') await fetchCurrentData(gen, forceRefresh, fk);
    };

    const refetchRecommendationsAndCurrent = async (force: boolean) => {
        const gen = ++transfersFetchGenRef.current;
        const fk = optimizerFilterKey;
        if (force) {
            setNoSalesSampleLoaded(false);
            setZeroSohHistogramSample([]);
        }
        await fetchRecommendationsData(gen, force, fk);
        if (transfersFetchGenRef.current !== gen) return;
        await fetchCurrentData(gen, force, fk);
    };

    const handleSync = async () => {
        optimizerDataSigRef.current = {};
        setReceivers([]);
        setDonors([]);
        setRecommendations([]);
        setCurrentTransfers([]);
        setNoSalesSampleLoaded(false);
        setZeroSohHistogramSample([]);
        try {
            const syncRes = await fetch(`${API_URL}/transfers-recom/sync`, { method: 'POST' }).then((r) => r.json());
            if (syncRes?.source_table) {
                console.info('[transfers-recom/sync] source table:', syncRes.source_table);
            }
            if (syncRes?.refill_source_filter) {
                console.info('[transfers-recom/sync] refill source filter:', syncRes.refill_source_filter);
            }
            await loadOptimizerSubTab(optimizerSubTab, true);
        } catch (err) {
            console.error("Sync failed:", err);
        }
    };

    const handleCreateTransfer = async (recId: number) => {
        try {
            setCreatingTransferId(recId);
            const res = await fetch(`${API_URL}/transfers-recom/approve/${recId}`, { method: 'POST' });
            const data = await res.json();
            if (data.status === 'success') {
                setSelectedRec(null);
                setSelectedRecGroup(null);
                await refetchRecommendationsAndCurrent(true);
            } else {
                alert(data.message);
            }
        } catch (err) {
            console.error("Approval error:", err);
        } finally {
            setCreatingTransferId(null);
        }
    };

    const handleCreateRouteTransfers = async (group: any) => {
        if (!group?.rows?.length) return;
        setApprovingRoute(true);
        try {
            const failures: string[] = [];
            for (const rec of group.rows) {
                const recId = Number(rec?.id);
                if (!Number.isFinite(recId)) continue;
                const res = await fetch(`${API_URL}/transfers-recom/approve/${recId}`, { method: 'POST' });
                const data = await res.json();
                if (data?.status !== 'success') {
                    failures.push(`SKU ${rec?.sku || recId}: ${data?.message || 'approve failed'}`);
                }
            }
            if (failures.length > 0) {
                alert(`Some SKUs failed:\n${failures.slice(0, 5).join('\n')}`);
            }
            setSelectedRecGroup(null);
            await refetchRecommendationsAndCurrent(true);
        } catch (err) {
            console.error('Route approve error:', err);
            alert('Failed to approve route transfers.');
        } finally {
            setApprovingRoute(false);
        }
    };

    const handleApproveAllTransfers = async () => {
        try {
            setApprovingAll(true);
            const res = await fetch(`${API_URL}/transfers-recom/approve-all`, { method: 'POST' });
            const data = await res.json();
            if (data.status === 'success') {
                setSelectedRec(null);
                setSelectedRecGroup(null);
                await refetchRecommendationsAndCurrent(true);
            } else {
                alert(data.message || 'Failed to approve all transfers.');
            }
        } catch (err) {
            console.error('Approve all error:', err);
            alert('Failed to approve all transfers.');
        } finally {
            setApprovingAll(false);
        }
    };

    const handleDownloadTransferDetails = async () => {
        try {
            setDownloadingExcel(true);
            const res = await fetch(`${API_URL}/transfers-recom/download-transfer-details?max_rows=50000`, {
                cache: 'no-store',
            });
            if (!res.ok) {
                const text = await res.text();
                let detail = text?.trim() || '';
                try {
                    const j = JSON.parse(text) as { detail?: unknown };
                    if (j?.detail != null) {
                        detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail);
                    }
                } catch {
                    /* keep raw body */
                }
                throw new Error(detail || `Download failed (HTTP ${res.status})`);
            }
            const blob = await res.blob();
            const contentDisposition = res.headers.get('content-disposition') || '';
            const match = contentDisposition.match(/filename="?([^"]+)"?/i);
            const fileName = match?.[1] || `transfer_details_${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '_')}.xlsx`;
            const blobUrl = window.URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = blobUrl;
            link.download = fileName;
            document.body.appendChild(link);
            link.click();
            link.remove();
            window.URL.revokeObjectURL(blobUrl);
        } catch (err) {
            console.error('Failed to download transfer details:', err);
            const msg = err instanceof Error ? err.message : String(err);
            alert(msg ? `Failed to download transfer details Excel.\n\n${msg}` : 'Failed to download transfer details Excel.');
        } finally {
            setDownloadingExcel(false);
        }
    };

    /** No-sales donors full export; respects N-days filter (same as table). */
    const handleExportNoSalesDonors = useCallback(() => {
        const params = new URLSearchParams();
        if (zeroDaysFilter > 0) params.set('max_days', String(zeroDaysFilter));
        const q = params.toString();
        window.location.href = `${API_URL}/transfers-recom/zero-soh/export${q ? `?${q}` : ''}`;
    }, [zeroDaysFilter]);

    useEffect(() => {
        if (activeTab !== 'optimizer') return;
        const filterBumped = lastOptimizerFilterKeyRef.current !== optimizerFilterKey;
        if (filterBumped) {
            lastOptimizerFilterKeyRef.current = optimizerFilterKey;
            optimizerDataSigRef.current = {};
            setReceivers([]);
            setDonors([]);
            setRecommendations([]);
            setCurrentTransfers([]);
            setNoSalesSampleLoaded(false);
            setZeroSohHistogramSample([]);
        }
        // Always load the active optimizer sub-tab. Chart bulk R+D uses `chartBulkFetchGenRef` so it no longer
        // races / cancels tab fetches (`transfersFetchGenRef`).
        void loadOptimizerSubTab(optimizerSubTab, filterBumped);
    }, [activeTab, optimizerFilterKey, optimizerSubTab, showDecisionCharts]);

    useEffect(() => {
        if (activeTab !== 'no_sales_donors') return;
        setZeroSohData([]);
        void fetchZeroSohData();
    }, [activeTab, debouncedRecvSite, debouncedDonorSite, zeroDaysFilter]);

    const applyColumnFilters = (rows: any[], cols: string[], filters: Record<string, string>) => {
        if (!rows.length || !cols.length) return rows;
        return rows.filter((row) =>
            cols.every((col) => {
                const filterVal = (filters[col] || '').trim().toLowerCase();
                if (!filterVal) return true;
                const cellVal = row?.[col];
                return String(cellVal ?? '').toLowerCase().includes(filterVal);
            })
        );
    };

    const filteredZeroSohRows = useMemo(
        () => applyColumnFilters(zeroSohData, zeroSohCols, zeroSohColumnFilters),
        [zeroSohData, zeroSohCols, zeroSohColumnFilters]
    );

    const sortedReceivers = useMemo(() => {
        const enriched = (receivers || []).map((r: any) => {
            const stock = Number(r.store_soh || 0);
            const wh = Number(r.wh_soh || 0);
            const need = Number(r.qty_still_needed || 0);
            const needGap = Math.max(need - stock, 0);
            const urgency = (stock <= 0 ? 2 : 0) + (wh <= 0 ? 1 : 0) + (needGap >= 3 ? 1 : 0);
            let reason = 'Moderate need';
            if (stock <= 0 && wh <= 0 && needGap > 0) reason = 'Stockout + WH empty';
            else if (stock <= 0 && needGap > 0) reason = 'Store stockout';
            else if (needGap > 0) reason = 'Demand exceeds store stock';
            return { ...r, need_gap: needGap, urgency_score: urgency, receiver_reason: reason };
        });
        const arr = [...enriched];
        arr.sort((a: any, b: any) => {
            if (receiverSort.key === 'need_gap') {
                if (a.need_gap !== b.need_gap) return receiverSort.dir === 'asc' ? (a.need_gap - b.need_gap) : (b.need_gap - a.need_gap);
                if (a.store_soh !== b.store_soh) return a.store_soh - b.store_soh;
                if (a.qty_still_needed !== b.qty_still_needed) return b.qty_still_needed - a.qty_still_needed;
                if (a.wh_soh !== b.wh_soh) return a.wh_soh - b.wh_soh;
                return String(a.site_name ?? '').localeCompare(String(b.site_name ?? ''));
            }
            const dirMul = receiverSort.dir === 'asc' ? 1 : -1;
            const av = a?.[receiverSort.key];
            const bv = b?.[receiverSort.key];
            if (typeof av === 'number' && typeof bv === 'number') return (av - bv) * dirMul;
            return String(av ?? '').localeCompare(String(bv ?? '')) * dirMul;
        });
        return arr;
    }, [receivers, receiverSort]);

    /** 2 · SKU-wise: donor store SOH minus receiver need (per SKU) — "units to move" story. */
    const transferSkuOpportunityChart = useMemo(() => {
        const need = new Map<string, number>();
        const donorSoh = new Map<string, number>();
        (receivers || []).forEach((r: any) => {
            const sku = String(r?.sku || '').trim();
            if (!sku) return;
            need.set(sku, (need.get(sku) || 0) + Number(r?.qty_still_needed || 0));
        });
        (donors || []).forEach((d: any) => {
            const sku = String(d?.sku || '').trim();
            if (!sku) return;
            donorSoh.set(sku, (donorSoh.get(sku) || 0) + Number(d?.store_soh || 0));
        });
        const skus = new Set<string>([...need.keys(), ...donorSoh.keys()]);
        return [...skus]
            .map((sku) => {
                const dSoh = donorSoh.get(sku) || 0;
                const n = need.get(sku) || 0;
                const netRaw = dSoh - n;
                return {
                    sku: truncateLabel(sku, 12),
                    skuFull: sku,
                    netMove: Math.max(0, netRaw),
                    donorSoh: dSoh,
                    need: n,
                    netRaw,
                };
            })
            .filter((row) => row.donorSoh > 0 || row.need > 0)
            .sort((a, b) => b.netMove - a.netMove)
            .slice(0, 12);
    }, [receivers, donors]);

    /** 3 · Top receiver stores by Σ priority_score; if scores missing, fallback to Σ qty still needed. */
    const topReceiverPriorityChart = useMemo(() => {
        const rows = receivers || [];
        const hasPriority = rows.some(
            (r: any) => Number.isFinite(Number(r?.priority_score)) && Number(r?.priority_score) > 0
        );
        const m = new Map<string, number>();
        rows.forEach((r: any) => {
            const store = String(r?.site_name || r?.site_id || '').trim() || '—';
            if (hasPriority) {
                const p = Number(r?.priority_score);
                if (!Number.isFinite(p)) return;
                m.set(store, (m.get(store) || 0) + p);
            } else {
                m.set(store, (m.get(store) || 0) + Number(r?.qty_still_needed || 0));
            }
        });
        return [...m.entries()]
            .map(([store, score]) => ({ store: truncateLabel(store, 16), storeFull: store, score }))
            .sort((a, b) => b.score - a.score)
            .slice(0, 10);
    }, [receivers]);

    /** 4 · Days since last sale — donors vs no-sales (sample) distribution. */
    const daysSinceSaleDistributionChart = useMemo(() => {
        const order = ['0-35', '35-60', '60-90', '90+'] as const;
        const counts: Record<string, { donor: number; noSales: number }> = {
            '0-35': { donor: 0, noSales: 0 },
            '35-60': { donor: 0, noSales: 0 },
            '60-90': { donor: 0, noSales: 0 },
            '90+': { donor: 0, noSales: 0 },
        };
        (donors || []).forEach((d: any) => {
            const b = bucketDaysSinceSale(Number(d?.days_from_last_sales || 0));
            counts[b].donor += 1;
        });
        (zeroSohHistogramSample || []).forEach((row) => {
            const b = bucketDaysSinceSale(pickZeroSohRowDays(row));
            counts[b].noSales += 1;
        });
        return order.map((bucket) => ({
            bucket,
            donor: counts[bucket].donor,
            noSales: counts[bucket].noSales,
        }));
    }, [donors, zeroSohHistogramSample]);

    /** 5 · Donor source mix — SALES_DATA_DONOR vs NO_SALES_DONOR vs other. */
    const donorSourceMixPie = useMemo(() => {
        let sales = 0;
        let noS = 0;
        let other = 0;
        (donors || []).forEach((d: any) => {
            const t = classifyDonorSource(d?.donor_source);
            if (t === 'sales_data') sales += 1;
            else if (t === 'no_sales') noS += 1;
            else other += 1;
        });
        return [
            { name: 'SALES_DATA_DONOR', value: sales, fill: CHART_COLORS.salesDataDonor },
            { name: 'NO_SALES_DONOR', value: noS, fill: CHART_COLORS.noSalesDonor },
            { name: 'Other / unset', value: other, fill: '#94a3b8' },
        ].filter((x) => x.value > 0);
    }, [donors]);

    /** 6 · State × receiver need (geography). */
    const receiverStateNeedChart = useMemo(() => {
        const m = new Map<string, number>();
        (receivers || []).forEach((r: any) => {
            const st = String(r?.state || '').trim() || '—';
            m.set(st, (m.get(st) || 0) + Number(r?.qty_still_needed || 0));
        });
        return [...m.entries()]
            .map(([state, needSum]) => ({ state: truncateLabel(state, 12), stateFull: state, need: needSum }))
            .sort((a, b) => b.need - a.need)
            .slice(0, 12);
    }, [receivers]);

    /** Per-store counts + refill / donor SOH sums for grouped bar chart. */
    const transferSiteChartRows = useMemo(() => {
        const m = new Map<
            string,
            { site: string; recv: number; donor: number; sumNeed: number; donorStock: number }
        >();
        const bump = (siteRaw: string, kind: 'recv' | 'donor', needOrStock: number) => {
            const site = String(siteRaw || '').trim() || 'Unknown';
            const cur = m.get(site) || { site, recv: 0, donor: 0, sumNeed: 0, donorStock: 0 };
            if (kind === 'recv') {
                cur.recv += 1;
                cur.sumNeed += needOrStock;
            } else {
                cur.donor += 1;
                cur.donorStock += needOrStock;
            }
            m.set(site, cur);
        };
        (receivers || []).forEach((r: any) => bump(r?.site_name, 'recv', Number(r?.qty_still_needed || 0)));
        (donors || []).forEach((d: any) => bump(d?.site_name, 'donor', Number(d?.store_soh || 0)));
        return [...m.values()]
            .sort((a, b) => b.recv + b.donor - (a.recv + a.donor))
            .slice(0, 12)
            .map((row) => ({
                ...row,
                label: truncateLabel(row.site, 14),
            }));
    }, [receivers, donors]);

    const sortedDonors = useMemo(() => {
        const enriched = (donors || []).map((d: any) => {
            const stock = Number(d.store_soh || 0);
            const sellDays = Number(d.days_from_last_sales || 0);
            const transferDays = Number(d.days_from_last_transfer || 0);
            const surplus = Math.max(stock - 1, 0);
            const donorScore = (sellDays >= 35 ? 2 : 0) + (surplus >= 2 ? 1 : 0) + (transferDays >= 30 ? 1 : 0);
            let reason = 'Transfer candidate';
            if (sellDays >= 35 && surplus > 0) reason = 'No-sales age high + surplus';
            else if (sellDays >= 35) reason = 'Low movement for long period';
            else if (surplus > 0) reason = 'Has stock surplus';
            return { ...d, surplus_qty: surplus, donor_score: donorScore, donor_reason: reason };
        });
        const arr = [...enriched];
        arr.sort((a: any, b: any) => {
            const dirMul = donorSort.dir === 'asc' ? 1 : -1;
            const av = a?.[donorSort.key];
            const bv = b?.[donorSort.key];
            if (typeof av === 'number' && typeof bv === 'number') return (av - bv) * dirMul;
            return String(av ?? '').localeCompare(String(bv ?? '')) * dirMul;
        });
        return arr;
    }, [donors, donorSort]);

    const selectedRecRecommendedRefill = useMemo<number | null>(() => {
        if (!selectedRec) return null;
        if (isNewRecommendedStackRec(selectedRec)) {
            const line = pickTransferLineQty(selectedRec);
            return line ?? null;
        }
        const recSite = String(
            selectedRec.receiver_store || selectedRec.receiver_store_name || ''
        ).trim();
        const recSku = String(selectedRec.sku || '').trim();
        const recSize = normalizeSize(selectedRec.size);
        const matched = (receivers || []).find((r: any) => {
            const byName = String(r?.site_name || '').trim() === recSite;
            const byId = String(r?.site_id || '').trim() === recSite;
            if (!(byName || byId)) return false;
            return (
                String(r?.sku || '').trim() === recSku && normalizeSize(r?.size) === recSize
            );
        });
        if (matched && matched?.qty_still_needed != null) {
            const fromReceivers = Number(matched.qty_still_needed);
            return Number.isNaN(fromReceivers) ? null : fromReceivers;
        }
        const fromRec = Number(selectedRec.receiver_recommended_refill);
        if (!Number.isNaN(fromRec) && fromRec > 0) return fromRec;
        return null;
    }, [selectedRec, receivers]);

    const sanitizedCurrentTransfers = useMemo(() => {
        return (currentTransfers || [])
            .map((item: any) => {
                const donorLabel =
                    item?.donor_store_name ||
                    item?.donor_site ||
                    item?.donor_store ||
                    item?.donor_Site ||
                    item?.donor_Site_ID ||
                    '';
                const receiverLabel =
                    item?.receiver_store_name ||
                    item?.receiver_site ||
                    item?.receiver_store ||
                    item?.receiver_Site ||
                    item?.receiver_Site_ID ||
                    '';
                const rawDate = item?.transfer_date || item?.Transfer_Date || item?.created_at;
                const parsedDate = rawDate ? new Date(typeof rawDate === 'number' && rawDate < 1e12 ? rawDate * 1000 : rawDate) : null;
                const hasValidDate = !!parsedDate && !Number.isNaN(parsedDate.getTime());
                const rawStatus = item?.status || item?.transfer_status || item?.Transfer_Status || '';
                const statusLabel = String(rawStatus || '').trim() || 'IN_TRANSIT';
                const transferStacks = Number(
                    item?.required_stack_qty ?? item?.transfer_stacks ?? item?.Transfer_Stacks ?? 1
                );
                const stackLabel = Number.isFinite(transferStacks) && transferStacks > 0 ? transferStacks : 1;
                const skuLabel = item?.sku || item?.SKU || '—';
                const lineRecStatus = String(item?.recommendation_line_status ?? '').trim();
                return {
                    ...item,
                    skuLabel,
                    statusLabel,
                    stackLabel,
                    donorLabel: String(donorLabel || '').trim(),
                    receiverLabel: String(receiverLabel || '').trim(),
                    dateLabel: hasValidDate ? parsedDate!.toLocaleDateString() : '—',
                    lineCreatedLabel: formatTransferTimestamp(item?.recommendation_created_at),
                    approvedAtLabel: formatTransferTimestamp(item?.created_at),
                    recLineStatusLabel: lineRecStatus || '—',
                    donorIdLabel: String(
                        item?.donor_site_id ?? item?.donor_store ?? item?.donor_Site_ID ?? ''
                    ).trim(),
                    receiverIdLabel: String(
                        item?.receiver_site_id ?? item?.receiver_store ?? item?.receiver_Site_ID ?? ''
                    ).trim(),
                };
            })
            .filter((item: any) => {
                if (item.donorLabel || item.receiverLabel) return true;
                if (item?.transfer_id != null && String(item.transfer_id).trim() !== '') return true;
                if (item?.sku) return true;
                if (item?.donor_site_id != null || item?.receiver_site_id != null) return true;
                return false;
            });
    }, [currentTransfers]);

    return (
        <DashboardLayout>
            <div className="space-y-8">
                {/* Header */}
                <div className="flex justify-between items-center">
                    <div>
                        <h1 className="text-2xl font-bold text-slate-900 dark:text-white flex items-center gap-3">
                            <Truck className="w-8 h-8 text-indigo-500" />
                            Transfers Dashboard
                        </h1>
                        
                        {activeTab === 'optimizer' && anyOptimizerListLoading && (
                            <div className="mt-1 w-full max-w-md">
                                <div className="flex items-center justify-between gap-3">
                                    <p className="text-xs text-amber-600 dark:text-amber-400 font-medium">
                                        {decisionChartsLoading && showDecisionCharts
                                            ? 'Loading charts + tab data'
                                            : 'Loading tab data'}
                                    </p>
                                    <p className="text-[10px] text-slate-500 dark:text-slate-400 whitespace-nowrap overflow-hidden text-ellipsis">
                                        {(() => {
                                            const bits: string[] = [];
                                            if (receiversTotalHint != null) {
                                                bits.push(
                                                    `R ${receivers.length.toLocaleString()}/${receiversTotalHint.toLocaleString()}`
                                                );
                                            }
                                            if (donorsTotalHint != null) {
                                                bits.push(`D ${donors.length.toLocaleString()}/${donorsTotalHint.toLocaleString()}`);
                                            }
                                            if (recsTotalHint != null) {
                                                bits.push(
                                                    `Rec ${recommendations.length.toLocaleString()}/${recsTotalHint.toLocaleString()}`
                                                );
                                            }
                                            return bits.length > 0 ? bits.join(' · ') : '…';
                                        })()}
                                    </p>
                                </div>
                                <div className="mt-1 h-2 w-full rounded-full bg-slate-200/80 dark:bg-slate-800/70 overflow-hidden">
                                    <div className="h-full w-1/3 rounded-full bg-indigo-500/70 animate-pulse" />
                                </div>
                            </div>
                        )}
                    </div>
                    <div className="flex gap-3">
                        {activeTab === 'optimizer' && (
                            <button
                                type="button"
                                onClick={handleDownloadTransferDetails}
                                disabled={downloadingExcel}
                                className="px-4 py-2 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 text-slate-700 dark:text-slate-300 rounded-lg hover:bg-slate-50 dark:hover:bg-slate-800 transition-colors text-sm font-medium flex items-center gap-2 shadow-sm disabled:opacity-60 disabled:cursor-not-allowed"
                            >
                                <Box className={`w-4 h-4 ${downloadingExcel ? 'animate-pulse' : ''}`} />
                                {downloadingExcel ? 'Downloading...' : 'Download Excel'}
                            </button>
                        )}
                        {activeTab === 'optimizer' && (
                            <button
                                onClick={handleSync}
                                className="px-4 py-2 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 text-slate-700 dark:text-slate-300 rounded-lg hover:bg-slate-50 dark:hover:bg-slate-800 transition-colors text-sm font-medium flex items-center gap-2 shadow-sm"
                            >
                                <RefreshCw
                                    className={`w-4 h-4 ${
                                        anyOptimizerListLoading && activeTab === 'optimizer' ? 'animate-spin' : ''
                                    }`}
                                />
                                Refresh / Sync
                            </button>
                        )}
                        {activeTab === 'no_sales_donors' && (
                            <button
                                onClick={fetchZeroSohData}
                                className="px-4 py-2 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 text-slate-700 dark:text-slate-300 rounded-lg hover:bg-slate-50 dark:hover:bg-slate-800 transition-colors text-sm font-medium flex items-center gap-2 shadow-sm"
                            >
                                <RefreshCw className={`w-4 h-4 ${loadingZeroSoh ? 'animate-spin' : ''}`} />
                                Reload Data
                            </button>
                        )}
                    </div>
                </div>

                {/* Tab Switcher */}
                <div className="flex items-center gap-1 p-1 bg-slate-100 dark:bg-slate-800/50 rounded-xl w-fit border border-slate-200 dark:border-slate-800">
                    <button
                        onClick={() => setActiveTab('optimizer')}
                        className={`px-6 py-2 rounded-lg text-sm font-bold transition-all ${activeTab === 'optimizer'
                                ? 'bg-white dark:bg-indigo-600 text-indigo-600 dark:text-white shadow-sm'
                                : 'text-slate-500 hover:text-slate-700 dark:hover:text-slate-300'
                            }`}
                    >
                        Transfers Optimizer
                    </button>
                    <button
                        onClick={() => setActiveTab('no_sales_donors')}
                        className={`px-6 py-2 rounded-lg text-sm font-bold transition-all ${activeTab === 'no_sales_donors'
                                ? 'bg-white dark:bg-indigo-600 text-indigo-600 dark:text-white shadow-sm'
                                : 'text-slate-500 hover:text-slate-700 dark:hover:text-slate-300'
                            }`}
                    >
                        No Sales Donors
                    </button>
                    <button
                        onClick={() => setActiveTab('store_clearance')}
                        className={`px-6 py-2 rounded-lg text-sm font-bold transition-all ${activeTab === 'store_clearance'
                                ? 'bg-white dark:bg-indigo-600 text-indigo-600 dark:text-white shadow-sm'
                                : 'text-slate-500 hover:text-slate-700 dark:hover:text-slate-300'
                            }`}
                    >
                        Store Clearance
                    </button>
                </div>

                {activeTab === 'store_clearance' ? (
                    <StoreClearancePanel />
                ) : activeTab === 'optimizer' ? (
                    <>
                        <div className="mb-4 space-y-3">
                            <p className="text-xs text-slate-500 dark:text-slate-400">Filters apply.</p>
                            <div className="flex flex-wrap items-center gap-1 p-1 bg-slate-100 dark:bg-slate-800/50 rounded-xl border border-slate-200 dark:border-slate-800 w-fit max-w-full">
                                {(
                                    [
                                        { id: 'receivers' as const, label: 'Receivers' },
                                        { id: 'donors' as const, label: 'Donors' },
                                        { id: 'recommended' as const, label: 'Recommended' },
                                        { id: 'current' as const, label: 'In transit' },
                                    ] as const
                                ).map((t) => (
                                    <button
                                        key={t.id}
                                        type="button"
                                        onClick={() => setOptimizerSubTab(t.id)}
                                        className={`px-4 py-2 rounded-lg text-sm font-bold transition-all whitespace-nowrap ${
                                            optimizerSubTab === t.id
                                                ? 'bg-white dark:bg-indigo-600 text-indigo-600 dark:text-white shadow-sm'
                                                : 'text-slate-500 hover:text-slate-700 dark:hover:text-slate-300'
                                        }`}
                                    >
                                        {t.label}
                                    </button>
                                ))}
                            </div>
                            {/* Charts section removed as requested */}
                        </div>

                        {false && (
                        <div className="space-y-8 mb-8">
                            <section className="rounded-2xl border border-slate-200/90 bg-white p-5 shadow-sm relative dark:border-slate-700/80 dark:bg-slate-900/70">
                                <div className="mb-4 flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
                                    <div>
                                        <h2 className="text-lg font-semibold text-slate-900 dark:text-white flex items-center gap-2">
                                            <BarChart3 className="h-5 w-5 text-indigo-500" />
                                            Graphs
                                        </h2>
                                        <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
                                            Decision views for the current filters · <strong>Refresh / Sync</strong> to reload data for the open tab and charts.
                                        </p>
                                    </div>
                                </div>

                                {decisionChartsLoading && (
                                    <div className="absolute inset-0 z-10 flex items-center justify-center rounded-2xl bg-white/70 dark:bg-slate-950/50">
                                        <div className="w-full px-4">
                                            <div className="min-h-[320px] flex items-center justify-center">
                                                <TransfersTableLoaderBlock />
                                            </div>
                                            <div className="mt-2 text-center text-xs text-slate-600 dark:text-slate-300">
                                                Loading chart data…
                                            </div>
                                        </div>
                                    </div>
                                )}

                                <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 xl:grid-cols-3">
                                    {/* 1 — Store receiver vs donor (unchanged story) */}
                                    <div className="flex min-h-[280px] flex-col rounded-xl border border-slate-200/80 bg-slate-50/40 p-3 dark:border-slate-700 dark:bg-slate-800/30">
                                        <h3 className="mb-0.5 px-1 text-xs font-bold uppercase tracking-wide text-slate-700 dark:text-slate-200">
                                            1. Per store: need (receiving) vs surplus (sending)
                                        </h3>
                                        <p className="mb-2 px-1 text-[11px] text-slate-500 dark:text-slate-400">
                                            For each store, how many <strong>rows</strong> are “need more stock” (receiving) vs “have stock to send” (sending) — top 12 by
                                            activity.
                                        </p>
                                        <div className="min-h-[220px] flex-1 w-full">
                                            {transferSiteChartRows.length === 0 ? (
                                                <div className="flex h-[220px] items-center justify-center text-sm text-slate-400">No rows for charts.</div>
                                            ) : (
                                                <ResponsiveContainer width="100%" height={220}>
                                                    <BarChart data={transferSiteChartRows} margin={{ top: 4, right: 8, left: 0, bottom: 4 }}>
                                                        <CartesianGrid strokeDasharray="3 3" vertical={false} className="stroke-slate-200 dark:stroke-slate-600" />
                                                        <XAxis dataKey="label" tick={{ fontSize: 9 }} interval={0} angle={-22} textAnchor="end" height={56} />
                                                        <YAxis allowDecimals={false} tick={{ fontSize: 10 }} width={28} />
                                                        <Tooltip
                                                            content={({ active, payload }) => {
                                                                if (!active || !payload?.length) return null;
                                                                const d = payload[0].payload as (typeof transferSiteChartRows)[0];
                                                                return (
                                                                    <div className="max-w-xs rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs shadow-lg dark:border-slate-600 dark:bg-slate-900">
                                                                        <p className="font-semibold text-slate-900 dark:text-white">{d.site}</p>
                                                                        <p className="mt-1 text-rose-600 dark:text-rose-300">Need — receiving: {d.recv} rows</p>
                                                                        <p className="text-emerald-600 dark:text-emerald-300">Can send — surplus: {d.donor} rows</p>
                                                                        <p className="text-indigo-600 dark:text-indigo-300">Total qty still needed: {d.sumNeed.toLocaleString()}</p>
                                                                        <p className="text-teal-600 dark:text-teal-300">Total store stock (sending): {d.donorStock.toLocaleString()}</p>
                                                                    </div>
                                                                );
                                                            }}
                                                        />
                                                        <Legend wrapperStyle={{ fontSize: 11 }} />
                                                        <Bar
                                                            dataKey="recv"
                                                            name="Need (receiving stores)"
                                                            fill={CHART_COLORS.receiver}
                                                            radius={[3, 3, 0, 0]}
                                                            maxBarSize={28}
                                                        />
                                                        <Bar
                                                            dataKey="donor"
                                                            name="Surplus (sending stores)"
                                                            fill={CHART_COLORS.donor}
                                                            radius={[3, 3, 0, 0]}
                                                            maxBarSize={28}
                                                        />
                                                    </BarChart>
                                                </ResponsiveContainer>
                                            )}
                                        </div>
                                    </div>

                                    {/* 2 — SKU transfer opportunity */}
                                    <div className="flex min-h-[280px] flex-col rounded-xl border border-slate-200/80 bg-slate-50/40 p-3 dark:border-slate-700 dark:bg-slate-800/30">
                                        <h3 className="mb-0.5 px-1 text-xs font-bold uppercase tracking-wide text-slate-700 dark:text-slate-200">
                                            2. SKU transfer opportunity
                                        </h3>
                                        <p className="mb-2 px-1 text-[11px] text-slate-500 dark:text-slate-400">Units to move ≈ max(0, donor store SOH − receiver need) per SKU.</p>
                                        <div className="min-h-[220px] flex-1 w-full">
                                            {transferSkuOpportunityChart.length === 0 ? (
                                                <div className="flex h-[220px] items-center justify-center text-sm text-slate-400">Need donor + receiver data with matching SKUs.</div>
                                            ) : (
                                                <ResponsiveContainer width="100%" height={220}>
                                                    <BarChart layout="vertical" data={transferSkuOpportunityChart} margin={{ top: 4, right: 12, left: 4, bottom: 4 }}>
                                                        <CartesianGrid strokeDasharray="3 3" horizontal className="stroke-slate-200 dark:stroke-slate-600" />
                                                        <XAxis type="number" allowDecimals={false} tick={{ fontSize: 10 }} />
                                                        <YAxis type="category" dataKey="sku" width={68} tick={{ fontSize: 9 }} />
                                                        <Tooltip
                                                            content={({ active, payload }) => {
                                                                if (!active || !payload?.length) return null;
                                                                const d = payload[0].payload as (typeof transferSkuOpportunityChart)[0];
                                                                return (
                                                                    <div className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs shadow-lg dark:border-slate-600 dark:bg-slate-900">
                                                                        <p className="font-mono font-semibold text-slate-900 dark:text-white">{d.skuFull}</p>
                                                                        <p className="text-slate-600 dark:text-slate-300">
                                                                            Stock at sending stores: {d.donorSoh.toLocaleString()} · Need at receiving:{' '}
                                                                            {d.need.toLocaleString()}
                                                                        </p>
                                                                        <p className="text-sky-600 dark:text-sky-300">Net (raw): {d.netRaw.toLocaleString()} · Bar: max(0, net)</p>
                                                                    </div>
                                                                );
                                                            }}
                                                        />
                                                        <Bar dataKey="netMove" name="Net movable (max 0)" fill={CHART_COLORS.netMove} radius={[0, 4, 4, 0]} maxBarSize={20} />
                                                    </BarChart>
                                                </ResponsiveContainer>
                                            )}
                                        </div>
                                    </div>

                                    {/* 3 — Top receivers by priority */}
                                    <div className="flex min-h-[280px] flex-col rounded-xl border border-slate-200/80 bg-slate-50/40 p-3 dark:border-slate-700 dark:bg-slate-800/30">
                                        <h3 className="mb-0.5 px-1 text-xs font-bold uppercase tracking-wide text-slate-700 dark:text-slate-200">
                                            3. Top stores that need stock (by priority)
                                        </h3>
                                        <p className="mb-2 px-1 text-[11px] text-slate-500 dark:text-slate-400">
                                            Sum of priority score per store when available; otherwise sum of qty still needed (receiving side).
                                        </p>
                                        <div className="min-h-[220px] flex-1 w-full">
                                            {topReceiverPriorityChart.length === 0 ? (
                                                <div className="flex h-[220px] items-center justify-center text-sm text-slate-400">
                                                    No receiving-side rows for this filter.
                                                </div>
                                            ) : (
                                                <ResponsiveContainer width="100%" height={220}>
                                                    <BarChart layout="vertical" data={topReceiverPriorityChart} margin={{ top: 4, right: 12, left: 4, bottom: 4 }}>
                                                        <CartesianGrid strokeDasharray="3 3" horizontal className="stroke-slate-200 dark:stroke-slate-600" />
                                                        <XAxis type="number" tick={{ fontSize: 10 }} />
                                                        <YAxis type="category" dataKey="store" width={100} tick={{ fontSize: 9 }} />
                                                        <Tooltip
                                                            content={({ active, payload }) => {
                                                                if (!active || !payload?.length) return null;
                                                                const d = payload[0].payload as (typeof topReceiverPriorityChart)[0];
                                                                return (
                                                                    <div className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs shadow-lg dark:border-slate-600 dark:bg-slate-900">
                                                                        <p className="font-semibold text-slate-900 dark:text-white">{d.storeFull}</p>
                                                                        <p className="text-violet-600 dark:text-violet-300">Σ priority: {d.score.toFixed(2)}</p>
                                                                    </div>
                                                                );
                                                            }}
                                                        />
                                                        <Bar dataKey="score" name="Σ priority" fill={CHART_COLORS.priorityBar} radius={[0, 4, 4, 0]} maxBarSize={18} />
                                                    </BarChart>
                                                </ResponsiveContainer>
                                            )}
                                        </div>
                                    </div>

                                    {/* 4 — Days since last sale distribution */}
                                    <div className="flex min-h-[280px] flex-col rounded-xl border border-slate-200/80 bg-slate-50/40 p-3 dark:border-slate-700 dark:bg-slate-800/30 lg:col-span-2">
                                        <h3 className="mb-0.5 px-1 text-xs font-bold uppercase tracking-wide text-slate-700 dark:text-slate-200">
                                            4. How long since last sale? (slow sellers)
                                        </h3>
                                        <p className="mb-1 px-1 text-[11px] text-slate-500 dark:text-slate-400">
                                            This chart counts <strong>rows from “Potential donors”</strong> (stores/SKUs that <em>can send</em> stock) and groups them by
                                            <strong> days since that SKU last sold</strong> at that store — it does <strong>not</strong> use the “Potential receivers”
                                            table. <span className="text-amber-800/90 dark:text-amber-200">Amber: optional</span> no-sales / 0-SOH <strong>sample</strong> for
                                            comparison; load the button below if you need it.
                                        </p>
                                        <div className="mb-2 flex flex-wrap items-center gap-2 px-1">
                                            <button
                                                type="button"
                                                onClick={() => void fetchNoSalesHistogramSample()}
                                                disabled={loadingNoSalesSample}
                                                className="rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-[11px] font-semibold text-amber-900 shadow-sm transition hover:bg-amber-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100 dark:hover:bg-amber-500/20"
                                            >
                                                {loadingNoSalesSample
                                                    ? 'Loading no-sales sample…'
                                                    : noSalesSampleLoaded
                                                      ? 'Reload no-sales sample (~400 rows)'
                                                      : 'Load no-sales sample (~400 rows)'}
                                            </button>
                                            {noSalesSampleLoaded && !loadingNoSalesSample && (
                                                <span className="text-[10px] text-slate-500">
                                                    {zeroSohHistogramSample.length.toLocaleString()} rows (respects store filter if set)
                                                </span>
                                            )}
                                        </div>
                                        <div className="min-h-[220px] flex-1 w-full">
                                            {daysSinceSaleDistributionChart.every((b) => b.donor === 0 && b.noSales === 0) ? (
                                                <div className="flex h-[220px] items-center justify-center text-sm text-slate-400">
                                                    No data — add rows under Potential donors, or load the no-sales sample.
                                                </div>
                                            ) : (
                                                <ResponsiveContainer width="100%" height={220}>
                                                    <BarChart data={daysSinceSaleDistributionChart} margin={{ top: 4, right: 8, left: 0, bottom: 4 }}>
                                                        <CartesianGrid strokeDasharray="3 3" vertical={false} className="stroke-slate-200 dark:stroke-slate-600" />
                                                        <XAxis dataKey="bucket" tick={{ fontSize: 10 }} />
                                                        <YAxis allowDecimals={false} tick={{ fontSize: 10 }} width={32} />
                                                        <Tooltip
                                                            content={({ active, payload, label }) => {
                                                                if (!active || !payload?.length) return null;
                                                                return (
                                                                    <div className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs shadow-lg dark:border-slate-600 dark:bg-slate-900">
                                                                        <p className="font-semibold text-slate-900 dark:text-white">{String(label)} days</p>
                                                                        {payload.map((p) => (
                                                                            <p key={String(p.dataKey)} className="text-slate-600 dark:text-slate-300">
                                                                                {p.name}: {Number(p.value).toLocaleString()}
                                                                            </p>
                                                                        ))}
                                                                    </div>
                                                                );
                                                            }}
                                                        />
                                                        <Legend wrapperStyle={{ fontSize: 11 }} />
                                                        <Bar
                                                            dataKey="donor"
                                                            name="Sending-side — Potential donors table"
                                                            fill={CHART_COLORS.donor}
                                                            radius={[3, 3, 0, 0]}
                                                            maxBarSize={32}
                                                        />
                                                        <Bar
                                                            dataKey="noSales"
                                                            name="No-sales / 0-SOH sample (optional)"
                                                            fill={CHART_COLORS.noSalesDonor}
                                                            radius={[3, 3, 0, 0]}
                                                            maxBarSize={32}
                                                        />
                                                    </BarChart>
                                                </ResponsiveContainer>
                                            )}
                                        </div>
                                    </div>

                                    {/* 5 — Donor source mix */}
                                    <div className="flex min-h-[280px] flex-col rounded-xl border border-slate-200/80 bg-slate-50/40 p-3 dark:border-slate-700 dark:bg-slate-800/30">
                                        <h3 className="mb-0.5 px-1 text-xs font-bold uppercase tracking-wide text-slate-700 dark:text-slate-200">
                                            5. Where surplus rows come from (source mix)
                                        </h3>
                                        <p className="mb-2 px-1 text-[11px] text-slate-500 dark:text-slate-400">
                                            Split of <code className="rounded bg-slate-200/80 px-1 text-[10px] dark:bg-slate-700">donor_source</code> on the sending table
                                            (e.g. sales-data vs no-sales classification).
                                        </p>
                                        <div className="min-h-[220px] flex-1 w-full">
                                            {donorSourceMixPie.length === 0 ? (
                                                <div className="flex h-[220px] items-center justify-center text-sm text-slate-400">
                                                    No sending-side rows for this filter.
                                                </div>
                                            ) : (
                                                <ResponsiveContainer width="100%" height={220}>
                                                    <PieChart>
                                                        <Pie
                                                            data={donorSourceMixPie}
                                                            dataKey="value"
                                                            nameKey="name"
                                                            cx="50%"
                                                            cy="50%"
                                                            innerRadius={40}
                                                            outerRadius={70}
                                                            paddingAngle={2}
                                                        >
                                                            {donorSourceMixPie.map((entry, i) => (
                                                                <Cell key={i} fill={entry.fill} />
                                                            ))}
                                                        </Pie>
                                                        <Tooltip formatter={(v: number) => v.toLocaleString()} />
                                                        <Legend wrapperStyle={{ fontSize: 10 }} />
                                                    </PieChart>
                                                </ResponsiveContainer>
                                            )}
                                        </div>
                                    </div>

                                    {/* 6 — State need (geography) */}
                                    <div className="flex min-h-[280px] flex-col rounded-xl border border-slate-200/80 bg-slate-50/40 p-3 dark:border-slate-700 dark:bg-slate-800/30 lg:col-span-2 xl:col-span-3">
                                        <h3 className="mb-0.5 px-1 text-xs font-bold uppercase tracking-wide text-slate-700 dark:text-slate-200">
                                            6. State-wise receiver need
                                        </h3>
                                        <p className="mb-2 px-1 text-[11px] text-slate-500 dark:text-slate-400">Σ qty still needed by state (from receiver rows).</p>
                                        <div className="min-h-[220px] flex-1 w-full">
                                            {receiverStateNeedChart.length === 0 ? (
                                                <div className="flex h-[220px] items-center justify-center text-sm text-slate-400">No state or need data.</div>
                                            ) : (
                                                <ResponsiveContainer width="100%" height={220}>
                                                    <BarChart layout="vertical" data={receiverStateNeedChart} margin={{ top: 4, right: 12, left: 4, bottom: 4 }}>
                                                        <CartesianGrid strokeDasharray="3 3" horizontal className="stroke-slate-200 dark:stroke-slate-600" />
                                                        <XAxis type="number" allowDecimals={false} tick={{ fontSize: 10 }} />
                                                        <YAxis type="category" dataKey="state" width={88} tick={{ fontSize: 9 }} />
                                                        <Tooltip
                                                            content={({ active, payload }) => {
                                                                if (!active || !payload?.length) return null;
                                                                const d = payload[0].payload as (typeof receiverStateNeedChart)[0];
                                                                return (
                                                                    <div className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs shadow-lg dark:border-slate-600 dark:bg-slate-900">
                                                                        <p className="font-semibold text-slate-900 dark:text-white">{d.stateFull}</p>
                                                                        <p className="text-pink-600 dark:text-pink-300">Σ need: {d.need.toLocaleString()}</p>
                                                                    </div>
                                                                );
                                                            }}
                                                        />
                                                        <Bar dataKey="need" name="Qty still needed" fill={CHART_COLORS.stateNeed} radius={[0, 4, 4, 0]} maxBarSize={20} />
                                                    </BarChart>
                                                </ResponsiveContainer>
                                            )}
                                        </div>
                                    </div>
                                </div>
                            </section>
                        </div>
                        )}

                        {optimizerSubTab === 'receivers' && (
                            <section className="overflow-hidden rounded-2xl border border-rose-200/50 bg-gradient-to-br from-white via-rose-50/20 to-white shadow-md dark:border-rose-900/30 dark:from-slate-900 dark:via-rose-950/20 dark:to-slate-900">
                                <div className="flex flex-col gap-3 border-b border-rose-100/80 bg-rose-500/[0.06] px-5 py-4 dark:border-rose-900/40 dark:bg-rose-950/20 sm:flex-row sm:items-center sm:justify-between">
                                    <div className="flex items-center gap-3">
                                        <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-rose-500/15 text-rose-600 dark:bg-rose-500/20 dark:text-rose-300">
                                            <TrendingUp className="h-5 w-5" />
                                        </div>
                                        <div>
                                            <h2 className="text-lg font-semibold tracking-tight text-slate-900 dark:text-white">Potential receivers</h2>
                                            <p className="text-xs text-slate-500 dark:text-slate-400">
                                                Rows with <span className="font-semibold text-rose-600 dark:text-rose-300">qty still needed &gt; 0</span>. Run{' '}
                                                <strong>Refresh / Sync</strong> after pipeline updates.
                                            </p>
                                        </div>
                                    </div>
                                    <FilterDropdown
                                        label="Site"
                                        icon={Store}
                                        value={recvSite}
                                        onChange={setRecvSite}
                                        endpoint="/transfers-recom/filters/sites"
                                        defaultValue="All Sites"
                                    />
                                </div>
                                <div className="max-h-[min(520px,55vh)] overflow-auto bg-white/80 dark:bg-slate-900/40">
                                    <table className="w-full text-sm text-left border-collapse">
                                        <thead className="bg-slate-50 dark:bg-slate-800/50 text-slate-500 uppercase text-[10px] font-bold tracking-wider border-b border-slate-100 dark:border-slate-800">
                                            <tr>
                                                <th onClick={() => toggleSort(setReceiverSort, 'site_id')} className="px-3 py-3 cursor-pointer">Site ID {sortIcon(receiverSort.key === 'site_id', receiverSort.dir)}</th>
                                                <th onClick={() => toggleSort(setReceiverSort, 'site_name')} className="px-3 py-3 cursor-pointer">Store {sortIcon(receiverSort.key === 'site_name', receiverSort.dir)}</th>
                                                <th onClick={() => toggleSort(setReceiverSort, 'store_type')} className="px-3 py-3 cursor-pointer">Store type {sortIcon(receiverSort.key === 'store_type', receiverSort.dir)}</th>
                                                <th onClick={() => toggleSort(setReceiverSort, 'city')} className="px-3 py-3 cursor-pointer">City {sortIcon(receiverSort.key === 'city', receiverSort.dir)}</th>
                                                <th onClick={() => toggleSort(setReceiverSort, 'state')} className="px-3 py-3 cursor-pointer">State {sortIcon(receiverSort.key === 'state', receiverSort.dir)}</th>
                                                <th onClick={() => toggleSort(setReceiverSort, 'sku')} className="px-3 py-3 cursor-pointer">SKU {sortIcon(receiverSort.key === 'sku', receiverSort.dir)}</th>
                                                <th className="px-3 py-3">Size</th>
                                                <th onClick={() => toggleSort(setReceiverSort, 'store_soh')} className="px-3 py-3 text-right cursor-pointer">Store SOH {sortIcon(receiverSort.key === 'store_soh', receiverSort.dir)}</th>
                                                <th onClick={() => toggleSort(setReceiverSort, 'wh_soh')} className="px-3 py-3 text-right cursor-pointer">WH SOH {sortIcon(receiverSort.key === 'wh_soh', receiverSort.dir)}</th>
                                                <th onClick={() => toggleSort(setReceiverSort, 'days_since_last_sale')} className="px-3 py-3 text-right cursor-pointer">Days since<br />last sale {sortIcon(receiverSort.key === 'days_since_last_sale', receiverSort.dir)}</th>
                                                <th onClick={() => toggleSort(setReceiverSort, 'last_week_sales')} className="px-3 py-3 text-right cursor-pointer">Last week<br />units {sortIcon(receiverSort.key === 'last_week_sales', receiverSort.dir)}</th>
                                                <th onClick={() => toggleSort(setReceiverSort, 'last_35day_sold_qty')} className="px-3 py-3 text-right cursor-pointer">Last 35d<br />sold qty {sortIcon(receiverSort.key === 'last_35day_sold_qty', receiverSort.dir)}</th>
                                                <th onClick={() => toggleSort(setReceiverSort, 'total_sales')} className="px-3 py-3 text-right cursor-pointer">Total sales {sortIcon(receiverSort.key === 'total_sales', receiverSort.dir)}</th>
                                                <th onClick={() => toggleSort(setReceiverSort, 'qty_still_needed')} className="px-3 py-3 text-right cursor-pointer">Qty still<br />needed {sortIcon(receiverSort.key === 'qty_still_needed', receiverSort.dir)}</th>
                                                <th onClick={() => toggleSort(setReceiverSort, 'priority_score')} className="px-3 py-3 text-right cursor-pointer">Priority<br />score {sortIcon(receiverSort.key === 'priority_score', receiverSort.dir)}</th>
                                            </tr>
                                        </thead>
                                        <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                                            {loadingReceivers && receivers.length === 0 ? (
                                                <tr>
                                                    <td
                                                        colSpan={TRANSFERS_DONOR_RECV_COL_COUNT}
                                                        className="relative min-h-[min(36vh,320px)] p-0 align-top"
                                                    >
                                                        <div className="pointer-events-none absolute inset-0 border-y border-emerald-100/50 bg-gradient-to-r from-emerald-50/40 via-slate-50/30 to-transparent dark:border-emerald-900/20 dark:from-emerald-950/30 dark:via-slate-900/50 dark:to-transparent" />
                                                        <div className="pointer-events-none absolute inset-0 opacity-[0.05] dark:opacity-[0.08] [background-image:repeating-linear-gradient(-12deg,transparent,transparent_18px,rgba(16,185,129,0.12)_18px,rgba(16,185,129,0.12)_19px)]" />
                                                        <div className="relative z-10 flex min-h-[min(36vh,320px)] w-full items-center justify-center py-8">
                                                            <TransfersTableLoaderBlock />
                                                        </div>
                                                    </td>
                                                </tr>
                                            ) : sortedReceivers.length > 0 ? (
                                                sortedReceivers.map((row: any, idx: number) => (
                                                    <tr key={idx} className="hover:bg-slate-50 dark:hover:bg-slate-800/30 transition-colors">
                                                        <td className="px-3 py-3 text-xs font-mono text-slate-600 dark:text-slate-300 max-w-[100px] truncate" title={row.site_id}>{row.site_id ?? '—'}</td>
                                                        <td className="px-3 py-3 font-medium text-slate-900 dark:text-white truncate max-w-[130px]" title={row.site_name}>{row.site_name}</td>
                                                        <td className="px-3 py-3 text-xs text-slate-600 dark:text-slate-300 max-w-[100px] truncate" title={row.store_type}>{row.store_type || '—'}</td>
                                                        <td className="px-3 py-3 text-xs text-slate-600 dark:text-slate-300 max-w-[110px] truncate" title={row.city}>{row.city || '—'}</td>
                                                        <td className="px-3 py-3 text-xs text-slate-600 dark:text-slate-300 max-w-[72px] truncate" title={row.state}>{row.state || '—'}</td>
                                                        <td className="px-3 py-3 text-slate-500 dark:text-slate-400 font-mono text-xs">{row.sku}</td>
                                                        <td className="px-3 py-3 text-slate-500 dark:text-slate-400">{row.size}</td>
                                                        <td className="px-3 py-3 text-right font-bold text-rose-500">{row.store_soh}</td>
                                                        <td className="px-3 py-3 text-right font-bold text-slate-700 dark:text-slate-300">{row.wh_soh}</td>
                                                        <td className="px-3 py-3 text-right text-slate-600 dark:text-slate-400 text-xs">{fmtDaysCell(row.days_since_last_sale)}</td>
                                                        <td className="px-3 py-3 text-right font-mono text-xs text-slate-600 dark:text-slate-300">{fmtIntCell(row.last_week_sales)}</td>
                                                        <td className="px-3 py-3 text-right font-mono text-xs text-slate-600 dark:text-slate-300">{fmtIntCell(row.last_35day_sold_qty)}</td>
                                                        <td className="px-3 py-3 text-right font-mono text-xs text-slate-600 dark:text-slate-300">{fmtIntCell(row.total_sales)}</td>
                                                        <td className="px-3 py-3 text-right text-indigo-500 font-bold">{row.qty_still_needed}</td>
                                                        <td className="px-3 py-3 text-right font-mono text-xs text-violet-600 dark:text-violet-300">{fmtPriorityCell(row.priority_score)}</td>
                                                    </tr>
                                                ))
                                            ) : (
                                                <tr><td colSpan={TRANSFERS_DONOR_RECV_COL_COUNT} className="px-4 py-12 text-center text-slate-400 italic">No receivers found.</td></tr>
                                            )}
                                        </tbody>
                                    </table>
                                </div>
                            </section>
                        )}

                        {optimizerSubTab === 'donors' && (
                            <section className="overflow-hidden rounded-2xl border border-emerald-200/50 bg-gradient-to-br from-white via-emerald-50/25 to-white shadow-md dark:border-emerald-900/30 dark:from-slate-900 dark:via-emerald-950/20 dark:to-slate-900">
                                <div className="flex flex-col gap-3 border-b border-emerald-100/80 bg-emerald-500/[0.06] px-5 py-4 dark:border-emerald-900/40 dark:bg-emerald-950/20 sm:flex-row sm:items-center sm:justify-between">
                                    <div className="flex items-center gap-3">
                                        <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-emerald-500/15 text-emerald-600 dark:bg-emerald-500/20 dark:text-emerald-300">
                                            <Package className="h-5 w-5" />
                                        </div>
                                        <div>
                                            <h2 className="text-lg font-semibold tracking-tight text-slate-900 dark:text-white">Potential donors</h2>
                                            <p className="text-xs text-slate-500 dark:text-slate-400">Slow-moving stock that can supply other stores.</p>
                                        </div>
                                    </div>
                                    <FilterDropdown
                                        label="Site"
                                        icon={Store}
                                        value={donorSite}
                                        onChange={setDonorSite}
                                        endpoint="/transfers-recom/filters/sites"
                                        defaultValue="All Sites"
                                    />
                                </div>
                                <div className="max-h-[min(520px,55vh)] overflow-auto bg-white/80 dark:bg-slate-900/40">
                                    <table className="w-full text-sm text-left border-collapse">
                                        <thead className="bg-slate-50 dark:bg-slate-800/50 text-slate-500 uppercase text-[10px] font-bold tracking-wider border-b border-slate-100 dark:border-slate-800">
                                            <tr>
                                                <th onClick={() => toggleSort(setDonorSort, 'site_id')} className="px-3 py-3 cursor-pointer">Site ID {sortIcon(donorSort.key === 'site_id', donorSort.dir)}</th>
                                                <th onClick={() => toggleSort(setDonorSort, 'site_name')} className="px-3 py-3 cursor-pointer">Store {sortIcon(donorSort.key === 'site_name', donorSort.dir)}</th>
                                                <th onClick={() => toggleSort(setDonorSort, 'store_type')} className="px-3 py-3 cursor-pointer">Store type {sortIcon(donorSort.key === 'store_type', donorSort.dir)}</th>
                                                <th onClick={() => toggleSort(setDonorSort, 'city')} className="px-3 py-3 cursor-pointer">City {sortIcon(donorSort.key === 'city', donorSort.dir)}</th>
                                                <th onClick={() => toggleSort(setDonorSort, 'state')} className="px-3 py-3 cursor-pointer">State {sortIcon(donorSort.key === 'state', donorSort.dir)}</th>
                                                <th onClick={() => toggleSort(setDonorSort, 'sku')} className="px-3 py-3 cursor-pointer">SKU {sortIcon(donorSort.key === 'sku', donorSort.dir)}</th>
                                                <th className="px-3 py-3">Size</th>
                                                <th onClick={() => toggleSort(setDonorSort, 'store_soh')} className="px-3 py-3 text-right cursor-pointer">Store SOH {sortIcon(donorSort.key === 'store_soh', donorSort.dir)}</th>
                                                <th onClick={() => toggleSort(setDonorSort, 'wh_soh')} className="px-3 py-3 text-right cursor-pointer">WH SOH {sortIcon(donorSort.key === 'wh_soh', donorSort.dir)}</th>
                                                <th onClick={() => toggleSort(setDonorSort, 'days_from_last_transfer')} className="px-3 py-3 text-right cursor-pointer">Days from<br />last transfer {sortIcon(donorSort.key === 'days_from_last_transfer', donorSort.dir)}</th>
                                                <th onClick={() => toggleSort(setDonorSort, 'days_from_last_sales')} className="px-3 py-3 text-right cursor-pointer">Days from<br />last sales {sortIcon(donorSort.key === 'days_from_last_sales', donorSort.dir)}</th>
                                                <th onClick={() => toggleSort(setDonorSort, 'last_week_sales')} className="px-3 py-3 text-right cursor-pointer">Last week<br />units {sortIcon(donorSort.key === 'last_week_sales', donorSort.dir)}</th>
                                                <th onClick={() => toggleSort(setDonorSort, 'last_35day_sold_qty')} className="px-3 py-3 text-right cursor-pointer">Last 35d<br />sold qty {sortIcon(donorSort.key === 'last_35day_sold_qty', donorSort.dir)}</th>
                                                <th onClick={() => toggleSort(setDonorSort, 'total_sales')} className="px-3 py-3 text-right cursor-pointer">Total sales {sortIcon(donorSort.key === 'total_sales', donorSort.dir)}</th>
                                                <th onClick={() => toggleSort(setDonorSort, 'donor_source')} className="px-3 py-3 text-left cursor-pointer">Donor source {sortIcon(donorSort.key === 'donor_source', donorSort.dir)}</th>
                                            </tr>
                                        </thead>
                                        <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                                            {loadingDonors && donors.length === 0 ? (
                                                <tr>
                                                    <td
                                                        colSpan={TRANSFERS_DONOR_RECV_COL_COUNT}
                                                        className="relative min-h-[min(36vh,320px)] p-0 align-top"
                                                    >
                                                        <div className="pointer-events-none absolute inset-0 border-y border-emerald-100/50 bg-gradient-to-r from-emerald-50/40 via-slate-50/30 to-transparent dark:border-emerald-900/20 dark:from-emerald-950/30 dark:via-slate-900/50 dark:to-transparent" />
                                                        <div className="pointer-events-none absolute inset-0 opacity-[0.05] dark:opacity-[0.08] [background-image:repeating-linear-gradient(-12deg,transparent,transparent_18px,rgba(16,185,129,0.12)_18px,rgba(16,185,129,0.12)_19px)]" />
                                                        <div className="relative z-10 flex min-h-[min(36vh,320px)] w-full items-center justify-center py-8">
                                                            <TransfersTableLoaderBlock />
                                                        </div>
                                                    </td>
                                                </tr>
                                            ) : sortedDonors.length > 0 ? (
                                                sortedDonors.map((row: any, idx: number) => (
                                                    <tr key={idx} className="hover:bg-slate-50 dark:hover:bg-slate-800/30 transition-colors">
                                                        <td className="px-3 py-3 text-xs font-mono text-slate-600 dark:text-slate-300 max-w-[100px] truncate" title={row.site_id}>{row.site_id ?? '—'}</td>
                                                        <td className="px-3 py-3 font-medium text-slate-900 dark:text-white truncate max-w-[120px]" title={row.site_name}>{row.site_name}</td>
                                                        <td className="px-3 py-3 text-xs text-slate-600 dark:text-slate-300 max-w-[100px] truncate" title={row.store_type}>{row.store_type || '—'}</td>
                                                        <td className="px-3 py-3 text-xs text-slate-600 dark:text-slate-300 max-w-[110px] truncate" title={row.city}>{row.city || '—'}</td>
                                                        <td className="px-3 py-3 text-xs text-slate-600 dark:text-slate-300 max-w-[72px] truncate" title={row.state}>{row.state || '—'}</td>
                                                        <td className="px-3 py-3 text-slate-500 dark:text-slate-400 font-mono text-xs">{row.sku}</td>
                                                        <td className="px-3 py-3 text-slate-500 dark:text-slate-400 text-xs">{row.size}</td>
                                                        <td className="px-3 py-3 text-right font-bold text-emerald-500">{row.store_soh}</td>
                                                        <td className="px-3 py-3 text-right font-bold text-slate-700 dark:text-slate-300">{row.wh_soh}</td>
                                                        <td className="px-3 py-3 text-right text-slate-600 dark:text-slate-400 text-xs">{fmtIntCell(row.days_from_last_transfer)}</td>
                                                        <td className="px-3 py-3 text-right text-slate-600 dark:text-slate-400 text-xs">{fmtDaysCell(row.days_from_last_sales)}</td>
                                                        <td className="px-3 py-3 text-right font-mono text-xs text-slate-600 dark:text-slate-300">{fmtIntCell(row.last_week_sales)}</td>
                                                        <td className="px-3 py-3 text-right font-mono text-xs text-slate-600 dark:text-slate-300">{fmtIntCell(row.last_35day_sold_qty)}</td>
                                                        <td className="px-3 py-3 text-right font-mono text-xs text-slate-600 dark:text-slate-300">{fmtIntCell(row.total_sales)}</td>
                                                        <td className="px-3 py-3 text-xs text-slate-500 dark:text-slate-400 max-w-[140px] truncate font-mono" title={row.donor_source}>{row.donor_source || '—'}</td>
                                                    </tr>
                                                ))
                                            ) : (
                                                <tr><td colSpan={TRANSFERS_DONOR_RECV_COL_COUNT} className="px-4 py-12 text-center text-slate-400 italic">No donors found.</td></tr>
                                            )}
                                        </tbody>
                                    </table>
                                </div>
                            </section>
                        )}

                        {optimizerSubTab === 'recommended' && (
                        <div>
                            <div className="flex items-center justify-between mb-4">
                                <div>
                                    <h2 className="text-lg font-semibold text-slate-900 dark:text-white">Recommended Transfers</h2>
                                </div>
                                <button
                                    onClick={handleApproveAllTransfers}
                                    disabled={approvingAll || loadingRecs || recommendations.length === 0}
                                    className="px-3 py-1.5 text-sm bg-emerald-600 text-white rounded-lg hover:bg-emerald-700 border border-emerald-600 transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
                                >
                                    {approvingAll ? 'Approving...' : 'Approve All'}
                                </button>
                            </div>

                            <div className="bg-white dark:bg-slate-900/50 border border-slate-200 dark:border-slate-800 rounded-xl overflow-hidden">
                                {loadingRecsMore && recommendations.length > 0 && (
                                    <div className="flex items-center gap-2 border-b border-amber-200/80 bg-amber-50/90 px-3 py-2 text-[11px] text-amber-900 dark:border-amber-900/40 dark:bg-amber-950/40 dark:text-amber-200">
                                        <RefreshCw className="h-3.5 w-3.5 shrink-0 animate-spin" />
                                        <span>Loading more recommendations...</span>
                                    </div>
                                )}
                                <div className="max-h-[600px] overflow-y-auto divide-y divide-slate-100 dark:divide-slate-800 custom-scrollbar">
                                    {(loadingRecs || loadingRecsMore) && recommendations.length === 0 ? (
                                        <div className="relative flex min-h-[min(36vh,320px)] w-full items-center justify-center border-y border-slate-100/80 bg-slate-50/30 py-8 dark:border-slate-800/50 dark:bg-slate-900/20">
                                            <TransfersTableLoaderBlock />
                                        </div>
                                    ) : recommendations.length === 0 ? (
                                        <div className="p-12 text-center text-slate-400 italic">No recommendations available. Run sync to generate.</div>
                                    ) : (
                                        groupedRecommendations.map((group: any) => (
                                            <div key={group.key} className="p-4 flex flex-col md:flex-row md:items-center justify-between gap-4 hover:bg-slate-50 dark:hover:bg-slate-800/30 transition-colors">
                                                <div className="flex items-center gap-4">
                                                    <span className={`px-2 py-1 rounded-full text-xs font-medium uppercase w-16 text-center ${group.topPriority === 'HIGH' ? 'bg-rose-500/10 text-rose-600 dark:text-rose-400' :
                                                            group.topPriority === 'MEDIUM' ? 'bg-amber-500/10 text-amber-600 dark:text-amber-400' :
                                                                'bg-slate-100 dark:bg-slate-500/10 text-slate-500 dark:text-slate-400'
                                                        }`}>
                                                        {group.topPriority}
                                                    </span>

                                                    <div className="flex-1 min-w-0">
                                                        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-slate-900 dark:text-white">
                                                            <span className="font-semibold truncate max-w-[280px]">
                                                                {group.donorSiteName || group.donorSiteId}
                                                            </span>
                                                            {group.donorSiteId && (
                                                                <span className="text-[10px] font-mono text-slate-500 dark:text-slate-400 bg-slate-100 dark:bg-slate-800 px-1.5 py-0.5 rounded border border-slate-200 dark:border-slate-700">
                                                                    {group.donorSiteId}
                                                                </span>
                                                            )}
                                                        </div>
                                                        <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-slate-500 dark:text-slate-400">
                                                            <span className="font-semibold uppercase tracking-wide text-rose-500 dark:text-rose-400">From</span>
                                                            <span className="max-w-[220px] truncate font-medium text-slate-700 dark:text-slate-200">
                                                                {group.donorSiteName || group.donorSiteId || '—'}
                                                            </span>
                                                            <ArrowRight className="h-3.5 w-3.5 shrink-0 text-slate-400" />
                                                            <span className="font-semibold uppercase tracking-wide text-emerald-600 dark:text-emerald-400">To</span>
                                                            <span className="max-w-[220px] truncate font-medium text-slate-700 dark:text-slate-200">
                                                                {group.receiverSiteName || group.receiverSiteId || '—'}
                                                            </span>
                                                            {group.receiverSiteId && (
                                                                <span className="rounded border border-slate-200 bg-slate-100 px-1.5 py-0.5 font-mono text-[10px] text-slate-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-400">
                                                                    {group.receiverSiteId}
                                                                </span>
                                                            )}
                                                        </div>
                                                        <div className="text-[12px] text-slate-500 dark:text-slate-400 flex flex-wrap items-center gap-x-3 gap-y-1 mt-1">
                                                            <div className="flex items-center gap-1.5 flex-wrap">
                                                                <span className="text-[10px] font-semibold text-indigo-600 dark:text-indigo-400 bg-indigo-500/10 px-1.5 py-0.5 rounded border border-indigo-500/20">
                                                                    {routeMovementLabel(group.movementType)}
                                                                </span>
                                                                <span className="font-mono text-[10px] bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300 px-1.5 py-0.5 rounded border border-slate-200 dark:border-slate-700">
                                                                    SKU {fmtIntCell(group.uniqueSkuCount)}
                                                                </span>
                                                                <span className="text-[10px] text-slate-600 dark:text-slate-300">
                                                                    Qty {fmtIntCell(group.totalTransferQty)}
                                                                </span>
                                                            </div>
                                                            {group.skuNames?.length > 0 && (
                                                                <div className="w-full text-[11px] text-slate-600 dark:text-slate-300" title={group.skuNames.join(', ')}>
                                                                    SKU names: {group.skuNames.join(', ')}
                                                                </div>
                                                            )}
                                                        </div>
                                                    </div>
                                                </div>

                                                <div className="flex items-center justify-between md:justify-end gap-6 w-full md:w-auto">
                                                    <div className="hidden lg:block text-right mr-4">
                                                        <p className="text-[10px] font-bold text-slate-400 dark:text-slate-500 tracking-widest uppercase mb-0.5">Movement</p>
                                                        <p className="text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-tight">
                                                            {group.donorState || '—'} <span className="text-slate-300 dark:text-slate-600 mx-1">→</span> {group.receiverState || '—'}
                                                        </p>
                                                    </div>
                                                    <div className="flex items-center gap-2">
                                                        <button className="px-3 py-1.5 text-sm border border-slate-200 dark:border-slate-700 text-slate-600 dark:text-slate-300 rounded-lg hover:bg-slate-50 dark:hover:bg-slate-800 hover:text-slate-900 dark:hover:text-white transition-colors">
                                                            Decline
                                                        </button>
                                                        <button
                                                            onClick={() => setSelectedRecGroup(group)}
                                                            className="px-3 py-1.5 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 border border-indigo-600 transition-colors"
                                                        >
                                                            Verify
                                                        </button>
                                                    </div>
                                                </div>
                                            </div>
                                        ))
                                    )}
                                </div>
                            </div>
                        </div>
                        )}

                        {/* ── Route Verify Modal (Grouped SKUs) ── */}
                        {selectedRecGroup && (
                            <div className="fixed inset-0 z-50 flex items-center justify-center p-3 sm:p-4 bg-slate-900/60 backdrop-blur-sm shadow-2xl">
                                <div className="flex w-full max-w-6xl max-h-[92vh] flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl dark:border-slate-800 dark:bg-slate-900">
                                    <div className="flex items-center justify-between border-b border-slate-100 p-4 dark:border-slate-800">
                                        <div>
                                            <h3 className="text-base font-bold text-slate-900 dark:text-white">Verify transfer route</h3>
                                            <p className="text-xs text-slate-500 dark:text-slate-400 mt-1">
                                                {selectedRecGroup.donorSiteName} ({selectedRecGroup.donorSiteId}) → {selectedRecGroup.receiverSiteName} ({selectedRecGroup.receiverSiteId})
                                            </p>
                                        </div>
                                        <button
                                            onClick={() => setSelectedRecGroup(null)}
                                            className="rounded-full p-1.5 transition-colors hover:bg-slate-200 dark:hover:bg-slate-800"
                                            type="button"
                                        >
                                            <X className="h-4 w-4 text-slate-500" />
                                        </button>
                                    </div>
                                    <div className="p-4 grid grid-cols-1 sm:grid-cols-4 gap-2 border-b border-slate-100 dark:border-slate-800 text-xs">
                                        <div className="rounded-lg border border-slate-200 dark:border-slate-700 p-2">
                                            <div className="text-slate-500">Movement</div>
                                            <div className="font-semibold">{routeMovementLabel(selectedRecGroup.movementType)}</div>
                                        </div>
                                        <div className="rounded-lg border border-slate-200 dark:border-slate-700 p-2">
                                            <div className="text-slate-500">Unique SKU</div>
                                            <div className="font-semibold">{fmtIntCell(selectedRecGroup.uniqueSkuCount)}</div>
                                        </div>
                                        <div className="rounded-lg border border-slate-200 dark:border-slate-700 p-2">
                                            <div className="text-slate-500">Total Transfer Qty</div>
                                            <div className="font-semibold">{fmtIntCell(selectedRecGroup.totalTransferQty)}</div>
                                        </div>
                                    </div>
                                    <div className="min-h-0 flex-1 overflow-auto p-4">
                                        <table className="w-full min-w-[1080px] text-xs">
                                            <thead className="sticky top-0 bg-slate-50 dark:bg-slate-800 z-10">
                                                <tr className="text-left border-b border-slate-200 dark:border-slate-700">
                                                    <th className="px-2 py-2">SKU</th>
                                                    <th className="px-2 py-2">Size</th>
                                                    <th className="px-2 py-2">Barcode</th>
                                                    <th className="px-2 py-2 text-right">Required Qty</th>
                                                    <th className="px-2 py-2 text-right">Transfer Qty</th>
                                                    <th className="px-2 py-2 text-right">Receiver Stock</th>
                                                    <th className="px-2 py-2 text-right">Donor Stock</th>
                                                    <th className="px-2 py-2 text-center">Receiver Grade</th>
                                                    <th className="px-2 py-2 text-center">Donor Grade</th>
                                                    <th className="px-2 py-2 text-right">Receiver Sales (Last Week)</th>
                                                    <th className="px-2 py-2 text-right">Receiver Sales (Last 35 Days)</th>
                                                    <th className="px-2 py-2 text-right">Receiver Total Sales</th>
                                                    <th className="px-2 py-2 text-right">Donor Sales (Last Week)</th>
                                                    <th className="px-2 py-2 text-right">Donor Sales (Last 35 Days)</th>
                                                    <th className="px-2 py-2 text-right">Donor Total Sales</th>
                                                </tr>
                                            </thead>
                                            <tbody>
                                                {selectedRecGroup.rows.map((row: any, i: number) => (
                                                    <tr key={row.id != null ? `route-row-${row.id}` : `route-row-${i}`} className="border-b border-slate-100 dark:border-slate-800 hover:bg-slate-50 dark:hover:bg-slate-800/40">
                                                        <td className="px-2 py-2 font-mono font-semibold">{row.sku || '—'}</td>
                                                        <td className="px-2 py-2">{row.size || '—'}</td>
                                                        <td className="px-2 py-2 font-mono">{row.barcode || '—'}</td>
                                                        <td className="px-2 py-2 text-right">{fmtIntCell(row.required_qty)}</td>
                                                        <td className="px-2 py-2 text-right">{fmtIntCell(pickTransferLineQty(row))}</td>
                                                        <td className="px-2 py-2 text-right">{fmtIntCell(row.receiver_soh)}</td>
                                                        <td className="px-2 py-2 text-right">{fmtIntCell(row.donor_soh)}</td>
                                                        <td className="px-2 py-2 text-center text-xs font-semibold uppercase tracking-wide text-slate-700 dark:text-slate-200">{row.receiver_store_grade || '—'}</td>
                                                        <td className="px-2 py-2 text-center text-xs font-semibold uppercase tracking-wide text-slate-700 dark:text-slate-200">{row.donor_store_grade || '—'}</td>
                                                        <td className="px-2 py-2 text-right">{fmtIntCell(row.receiver_last_week_sales)}</td>
                                                        <td className="px-2 py-2 text-right">{fmtIntCell(row.receiver_sold_qty_35d)}</td>
                                                        <td className="px-2 py-2 text-right">{fmtIntCell(row.receiver_total_sales)}</td>
                                                        <td className="px-2 py-2 text-right">{fmtIntCell(row.donor_last_week_sales)}</td>
                                                        <td className="px-2 py-2 text-right">{fmtIntCell(row.donor_sold_qty_35d)}</td>
                                                        <td className="px-2 py-2 text-right">{fmtIntCell(row.donor_total_sales)}</td>
                                                    </tr>
                                                ))}
                                            </tbody>
                                        </table>
                                    </div>
                                    <div className="shrink-0 flex gap-2 p-4 border-t border-slate-100 dark:border-slate-800">
                                        <button
                                            onClick={() => setSelectedRecGroup(null)}
                                            className="flex-1 rounded-xl bg-slate-200 py-2.5 text-xs font-bold text-slate-700 transition hover:bg-slate-300 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700"
                                            type="button"
                                        >
                                            Cancel
                                        </button>
                                        <button
                                            onClick={() => handleCreateRouteTransfers(selectedRecGroup)}
                                            disabled={approvingRoute}
                                            className="flex-[1.3] rounded-xl bg-indigo-600 py-2.5 text-xs font-bold text-white transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-60"
                                            type="button"
                                        >
                                            {approvingRoute ? 'Approving...' : 'Approve all SKUs in this route'}
                                        </button>
                                    </div>
                                </div>
                            </div>
                        )}

                        {/* ── Approval Box (Modal) ── */}
                        {selectedRec && (
                            <div className="fixed inset-0 z-50 flex items-center justify-center p-3 sm:p-4 bg-slate-900/60 backdrop-blur-sm shadow-2xl">
                                <div
                                    className={`flex w-full flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl animate-in fade-in zoom-in duration-200 dark:border-slate-800 dark:bg-slate-900 ${
                                        isNewRecommendedStackRec(selectedRec)
                                            ? 'max-h-[min(94vh,900px)] max-w-2xl sm:max-w-5xl'
                                            : 'max-w-6xl'
                                    }`}
                                >
                                    <div className="flex shrink-0 items-center justify-between border-b border-slate-100 p-3 dark:border-slate-800 dark:bg-slate-800/40">
                                        <h3 className="text-base font-bold text-slate-900 dark:text-white">Verify transfer</h3>
                                        <button
                                            onClick={() => setSelectedRec(null)}
                                            className="rounded-full p-1.5 transition-colors hover:bg-slate-200 dark:hover:bg-slate-800"
                                            type="button"
                                        >
                                            <X className="h-4 w-4 text-slate-500" />
                                        </button>
                                    </div>

                                    <div
                                        className={`min-h-0 flex flex-1 flex-col ${
                                            isNewRecommendedStackRec(selectedRec) ? '' : 'overflow-y-auto p-8'
                                        }`}
                                    >
                                        {isNewRecommendedStackRec(selectedRec) ? (
                                            <div className="flex min-h-0 w-full min-w-0 flex-1 flex-col">
                                            <div className="min-h-0 flex-1 overflow-y-auto p-3">
                                                <div className="mb-3 overflow-hidden rounded-2xl border border-indigo-200/70 bg-gradient-to-br from-indigo-600 via-violet-600 to-indigo-800 p-[1px] shadow-lg shadow-indigo-500/20 dark:border-indigo-500/30">
                                                    <div className="rounded-[15px] bg-white/95 px-3 py-3 dark:bg-slate-900/95">
                                                        <p className="mb-1 text-[9px] font-bold uppercase tracking-[0.2em] text-indigo-500 dark:text-indigo-400">
                                                            SKU · this verification line
                                                        </p>
                                                        <p className="break-all font-mono text-lg font-black tracking-tight text-slate-900 dark:text-white sm:text-xl">
                                                            {selectedRec.sku || '—'}
                                                        </p>
                                                        <div className="mt-2 flex flex-wrap gap-1">
                                                            {selectedRec.match_type != null && String(selectedRec.match_type).trim() !== '' && (
                                                                <span
                                                                    className="rounded-full bg-indigo-500/10 px-2 py-0.5 text-[10px] font-semibold text-indigo-700 dark:text-indigo-300"
                                                                    title="How donor and receiver were paired"
                                                                >
                                                                    {String(selectedRec.match_type)}
                                                                </span>
                                                            )}
                                                            {selectedRec.status != null && String(selectedRec.status).trim() !== '' && (
                                                                <span className="rounded-full bg-slate-200/80 px-2 py-0.5 text-[10px] text-slate-700 dark:text-slate-200">
                                                                    {String(selectedRec.status)}
                                                                </span>
                                                            )}
                                                            {selectedRec.actual_transferable_stacks != null &&
                                                                String(selectedRec.actual_transferable_stacks).trim() !== '' && (
                                                                    <span
                                                                        className="rounded-full bg-emerald-500/10 px-2 py-0.5 text-[10px] font-medium text-emerald-700 dark:text-emerald-400"
                                                                        title="Stack bundles that can move after constraints"
                                                                    >
                                                                        Stacks {fmtIntCell(selectedRec.actual_transferable_stacks)}
                                                                    </span>
                                                                )}
                                                        </div>
                                                        <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
                                                            <div className="rounded-lg border border-rose-200/80 bg-rose-50/50 p-2.5 text-[10px] dark:border-rose-900/40 dark:bg-rose-950/25">
                                                                <p className="text-[9px] font-bold uppercase tracking-wide text-rose-600 dark:text-rose-400">
                                                                    From (donor)
                                                                </p>
                                                                <p className="mt-1 line-clamp-3 text-xs font-bold leading-snug text-slate-900 dark:text-white">
                                                                    {selectedRec.donor_store_name || selectedRec.donor_store}
                                                                </p>
                                                                <dl className="mt-2 space-y-1.5 text-slate-700 dark:text-slate-200">
                                                                    <div className="flex flex-col gap-0.5 sm:flex-row sm:justify-between sm:gap-2">
                                                                        <dt className="shrink-0 text-slate-500 dark:text-slate-400">Site / store id</dt>
                                                                        <dd className="font-medium tabular-nums sm:text-right">{selectedRec.donor_store ?? '—'}</dd>
                                                                    </div>
                                                                    <div className="flex flex-col gap-0.5 sm:flex-row sm:justify-between sm:gap-2">
                                                                        <dt className="shrink-0 text-slate-500 dark:text-slate-400">Location</dt>
                                                                        <dd className="text-right text-xs sm:max-w-[65%]">
                                                                            {String(selectedRec.donor_city || '—')}, {String(selectedRec.donor_state || '—')}
                                                                        </dd>
                                                                    </div>
                                                                    <div className="flex flex-col gap-0.5 sm:flex-row sm:justify-between sm:gap-2">
                                                                        <dt className="shrink-0 text-slate-500 dark:text-slate-400">Store Grade</dt>
                                                                        <dd className="font-medium tabular-nums text-slate-900 dark:text-slate-100 uppercase tracking-wide text-right">{selectedRec.donor_store_grade || '—'}</dd>
                                                                    </div>
                                                                    {selectedRec.days_from_last_sales != null && selectedRec.days_from_last_sales !== '' ? (
                                                                        <div className="flex flex-col gap-0.5 sm:flex-row sm:justify-between sm:gap-2">
                                                                            <dt className="shrink-0 text-slate-500 dark:text-slate-400">Days since last sale</dt>
                                                                            <dd className="tabular-nums sm:text-right">{fmtDaysCell(selectedRec.days_from_last_sales)}</dd>
                                                                        </div>
                                                                    ) : null}
                                                                    <div className="flex flex-col gap-0.5 sm:flex-row sm:justify-between sm:gap-2">
                                                                        <dt className="shrink-0 text-slate-500 dark:text-slate-400">Units sold (35 days)</dt>
                                                                        <dd className="tabular-nums sm:text-right">{fmtIntCell(selectedRec.donor_sold_qty_35d)}</dd>
                                                                    </div>
                                                                </dl>
                                                            </div>
                                                            <div className="rounded-lg border border-emerald-200/80 bg-emerald-50/50 p-2.5 text-[10px] dark:border-emerald-900/40 dark:bg-emerald-950/25">
                                                                <p className="text-[9px] font-bold uppercase tracking-wide text-emerald-700 dark:text-emerald-400">
                                                                    To (receiver)
                                                                </p>
                                                                <p className="mt-1 line-clamp-3 text-xs font-bold leading-snug text-slate-900 dark:text-white">
                                                                    {selectedRec.receiver_store_name || selectedRec.receiver_store}
                                                                </p>
                                                                <div className="mt-2 flex items-center justify-between gap-2 rounded-md border border-emerald-300/60 bg-white/70 px-2 py-1.5 dark:border-emerald-800/60 dark:bg-emerald-950/40">
                                                                    <span className="font-semibold text-emerald-800 dark:text-emerald-200">Total to send</span>
                                                                    <span className="text-lg font-black tabular-nums text-emerald-600 dark:text-emerald-300">
                                                                        {selectedRecRecommendedRefill === null ? '—' : selectedRecRecommendedRefill}
                                                                    </span>
                                                                </div>
                                                                <dl className="mt-2 space-y-1.5 text-slate-700 dark:text-slate-200">
                                                                    <div className="flex flex-col gap-0.5 sm:flex-row sm:justify-between sm:gap-2">
                                                                        <dt className="shrink-0 text-slate-500 dark:text-slate-400">Site / store id</dt>
                                                                        <dd className="font-medium tabular-nums sm:text-right">{selectedRec.receiver_store ?? '—'}</dd>
                                                                    </div>
                                                                    <div className="flex flex-col gap-0.5 sm:flex-row sm:justify-between sm:gap-2">
                                                                        <dt className="shrink-0 text-slate-500 dark:text-slate-400">Location</dt>
                                                                        <dd className="text-right text-xs sm:max-w-[65%]">
                                                                            {String(selectedRec.receiver_city || '—')}, {String(selectedRec.receiver_state || '—')}
                                                                        </dd>
                                                                    </div>
                                                                    <div className="flex flex-col gap-0.5 sm:flex-row sm:justify-between sm:gap-2">
                                                                        <dt className="shrink-0 text-slate-500 dark:text-slate-400">Store Grade</dt>
                                                                        <dd className="font-medium tabular-nums text-slate-900 dark:text-slate-100 uppercase tracking-wide text-right">{selectedRec.receiver_store_grade || '—'}</dd>
                                                                    </div>
                                                                    <div className="flex flex-col gap-0.5 sm:flex-row sm:justify-between sm:gap-2">
                                                                        <dt className="shrink-0 text-slate-500 dark:text-slate-400">Days since last sale</dt>
                                                                        <dd className="tabular-nums sm:text-right">{fmtIntCell(selectedRec.receiver_days_since_last_sale)}</dd>
                                                                    </div>
                                                                    <div className="flex flex-col gap-0.5 sm:flex-row sm:justify-between sm:gap-2">
                                                                        <dt className="shrink-0 text-slate-500 dark:text-slate-400">Last week — units sold</dt>
                                                                        <dd className="tabular-nums sm:text-right">{fmtIntCell(selectedRec.receiver_last_week_sales)}</dd>
                                                                    </div>
                                                                    <div className="flex flex-col gap-0.5 sm:flex-row sm:justify-between sm:gap-2">
                                                                        <dt className="shrink-0 text-slate-500 dark:text-slate-400">Units sold (35 days)</dt>
                                                                        <dd className="tabular-nums sm:text-right">{fmtIntCell(selectedRec.receiver_sold_qty_35d)}</dd>
                                                                    </div>
                                                                </dl>
                                                            </div>
                                                        </div>
                                                    </div>
                                                </div>
                                                <details className="group mb-2 rounded-xl border border-slate-200/80 bg-slate-50/50 open:bg-white dark:border-slate-700 dark:bg-slate-900/40 dark:open:bg-slate-900/60 [&_summary::-webkit-details-marker]:hidden">
                                                    <summary className="cursor-pointer list-none px-2.5 py-2 text-[10px] font-bold text-indigo-600 transition hover:bg-slate-100/80 dark:text-indigo-400 dark:hover:bg-slate-800/60">
                                                        <span className="underline decoration-indigo-300 underline-offset-2 group-open:no-underline">
                                                            Full donor &amp; receiver field list
                                                        </span>
                                                        <span className="ml-2 font-normal text-slate-500 dark:text-slate-400">(tap to expand)</span>
                                                    </summary>
                                                    <div className="border-t border-slate-100 px-2 pb-2 pt-1 dark:border-slate-800">
                                                        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                                                    <div className="rounded-xl border border-rose-200/60 bg-rose-50/30 p-2.5 dark:border-rose-900/30 dark:bg-rose-950/20">
                                                        <div className="text-[9px] font-bold uppercase tracking-wider text-rose-600 dark:text-rose-400">
                                                            From (donor)
                                                        </div>
                                                        <div className="line-clamp-2 text-xs font-bold leading-tight text-slate-900 dark:text-white">
                                                            {selectedRec.donor_store_name || selectedRec.donor_store}
                                                        </div>
                                                        <div className="mt-2 grid grid-cols-1 gap-2.5 sm:grid-cols-2">
                                                            <StackModalFact
                                                                label="Site / store id"
                                                                hint="Internal code for the donor store in your system."
                                                                value={selectedRec.donor_store ?? '—'}
                                                            />
                                                            <StackModalFact
                                                                label="Donor rule (model tag)"
                                                                hint="How the engine classified this source (e.g. no-sales = low or no sell-through, used as a source candidate)."
                                                                value={String(selectedRec.donor_source || '—')}
                                                            />
                                                            <StackModalFact
                                                                label="Location"
                                                                value={`${String(selectedRec.donor_city || '—')}, ${String(selectedRec.donor_state || '—')}`}
                                                            />
                                                            <StackModalFact
                                                                label="Units in store (SOH)"
                                                                hint="On-hand quantity for this store (same scope as the AI run)."
                                                                value={fmtIntCell(selectedRec.donor_store_soh)}
                                                            />
                                                            {selectedRec.days_from_last_sales != null &&
                                                            selectedRec.days_from_last_sales !== '' ? (
                                                                <StackModalFact
                                                                    label="Days since last customer sale (donor)"
                                                                    hint="If empty in data, the engine may not have a sale date for this line."
                                                                    value={fmtDaysCell(selectedRec.days_from_last_sales)}
                                                                />
                                                            ) : null}
                                                            <StackModalFact
                                                                label="Units sold (last 35 days)"
                                                                hint="How many units of this SKU sold at the donor in the last 35 days, if tracked."
                                                                value={fmtIntCell(selectedRec.donor_sold_qty_35d)}
                                                            />
                                                            <div className="sm:col-span-2">
                                                                <StackModalFact
                                                                    label="Total sales — donor line (SKU scope)"
                                                                    hint="Line-level `donor_total_sales` from recommendation row (header, not size split)."
                                                                    value={fmtIntCell(selectedRec.donor_total_sales)}
                                                                />
                                                            </div>
                                                        </div>
                                                    </div>
                                                    <div className="rounded-xl border border-emerald-200/60 bg-emerald-50/30 p-2.5 dark:border-emerald-900/30 dark:bg-emerald-950/20">
                                                        <div className="text-[9px] font-bold uppercase tracking-wider text-emerald-600 dark:text-emerald-400">
                                                            To (receiver)
                                                        </div>
                                                        <div className="line-clamp-2 text-xs font-bold leading-tight text-slate-900 dark:text-white">
                                                            {selectedRec.receiver_store_name || selectedRec.receiver_store}
                                                        </div>
                                                        <div className="my-1.5 flex items-center justify-between rounded-lg border border-emerald-200/80 bg-emerald-500/5 px-2 py-1 dark:border-emerald-800/70">
                                                            <div className="text-[9px] font-bold text-emerald-700 dark:text-emerald-300">
                                                                Total to send (this line)
                                                            </div>
                                                            <div
                                                                className="text-lg font-black tabular-nums text-emerald-600 dark:text-emerald-200"
                                                                title="Total units in this move for the SKU; size split is in the table below."
                                                            >
                                                                {selectedRecRecommendedRefill === null ? '—' : selectedRecRecommendedRefill}
                                                            </div>
                                                        </div>
                                                        <div className="mt-1 grid grid-cols-1 gap-2.5 sm:grid-cols-2">
                                                            <StackModalFact
                                                                label="Site / store id"
                                                                value={selectedRec.receiver_store ?? '—'}
                                                            />
                                                            <StackModalFact
                                                                label="Format"
                                                                hint="Store format in your org (e.g. SSL) — for reference only."
                                                                value={String(selectedRec.store_type || '—')}
                                                            />
                                                            <StackModalFact
                                                                label="Location"
                                                                value={`${String(selectedRec.receiver_city || '—')}, ${String(selectedRec.receiver_state || '—')}`}
                                                            />
                                                            <StackModalFact
                                                                label="Units in store (SOH)"
                                                                value={fmtIntCell(selectedRec.receiver_store_soh)}
                                                            />
                                                            <StackModalFact
                                                                label="Days since last sale (receiver)"
                                                                hint="How long this SKU had no sell-through at the receiver, if tracked."
                                                                value={fmtIntCell(selectedRec.receiver_days_since_last_sale)}
                                                            />
                                                            <StackModalFact
                                                                label="Last week — units sold"
                                                                hint="Units of this SKU sold in the last week at the receiver, if your column is populated."
                                                                value={fmtIntCell(selectedRec.receiver_last_week_sales)}
                                                            />
                                                            <StackModalFact
                                                                label="Last 35 days — units sold"
                                                                value={fmtIntCell(selectedRec.receiver_sold_qty_35d)}
                                                            />
                                                            <div className="sm:col-span-2">
                                                                <StackModalFact
                                                                    label="Total sales — receiver line (SKU scope)"
                                                                    hint="Line-level `receiver_total_sales` from recommendation row (header, not size split)."
                                                                    value={fmtIntCell(selectedRec.receiver_total_sales)}
                                                                />
                                                            </div>
                                                        </div>
                                                    </div>
                                                </div>
                                                    </div>
                                                </details>
                                                <div className="relative mb-2 mt-3 overflow-hidden rounded-xl border border-slate-200/90 bg-gradient-to-r from-white via-indigo-50/40 to-emerald-50/40 px-3 py-2.5 shadow-sm dark:border-slate-600 dark:from-slate-900 dark:via-indigo-950/50 dark:to-emerald-950/30">
                                                    <div className="flex flex-wrap items-center justify-between gap-2">
                                                        <div>
                                                            <p className="text-[11px] font-extrabold uppercase tracking-[0.12em] text-slate-800 dark:text-slate-100">
                                                                Size-level plan · barcodes &amp; send
                                                            </p>
                                                            <p className="mt-0.5 text-[10px] text-slate-600 dark:text-slate-400">
                                                                Donor vs receiver rows for{' '}
                                                                <span className="font-mono font-bold text-indigo-600 dark:text-indigo-400">{selectedRec.sku}</span>{' '}
                                                                — scroll horizontally on small screens.
                                                            </p>
                                                        </div>
                                                    </div>
                                                </div>
                                                <div className="overflow-x-auto rounded-xl border border-slate-300/90 shadow-inner dark:border-slate-600 bg-slate-50/40 dark:bg-slate-900/50">
                                                    <table className="w-full min-w-[780px] border-collapse text-left text-[10px] sm:text-[11px]">
                                                        <thead className="sticky top-0 z-10 shadow-sm">
                                                            {/* Grouped headers */}
                                                            <tr className="border-b border-slate-200 bg-slate-100/95 text-slate-600 dark:border-slate-700 dark:bg-slate-800/95 dark:text-slate-300">
                                                                <th className="px-2 py-2" rowSpan={2}>Size</th>
                                                                <th className="px-2 py-2 text-center border-l border-rose-200 dark:border-rose-800 text-rose-600 dark:text-rose-400 bg-rose-50/50 dark:bg-rose-950/20" colSpan={3}>🔴 Donor (From)</th>
                                                                <th className="px-2 py-2 text-center border-l border-emerald-200 dark:border-emerald-800 text-emerald-600 dark:text-emerald-400 bg-emerald-50/50 dark:bg-emerald-950/20" colSpan={4}>🟢 Receiver (To)</th>
                                                                <th className="px-2 py-2 text-right border-l border-indigo-200 dark:border-indigo-800 text-indigo-600 dark:text-indigo-300 bg-indigo-50/50 dark:bg-indigo-950/20" rowSpan={2} title="Units planned to transfer in this size">Send</th>
                                                            </tr>
                                                            <tr className="border-b-2 border-slate-200 bg-slate-50/95 text-xs text-slate-500 dark:border-slate-700 dark:bg-slate-800/60 dark:text-slate-400">
                                                                <th className="whitespace-nowrap px-2 py-1.5 border-l border-rose-200/60 dark:border-rose-800/40 font-semibold text-rose-600 dark:text-rose-400" title="Barcode at donor for this size">Barcode</th>
                                                                <th className="px-2 py-1.5 text-right font-semibold text-rose-600 dark:text-rose-400" title="Donor stock on hand (`don_soh_*`)">SOH</th>
                                                                <th className="px-2 py-1.5 text-right font-semibold text-rose-600 dark:text-rose-400" title="Donor sales this size (`don_sales_*`)">Sales</th>
                                                                <th className="whitespace-nowrap px-2 py-1.5 border-l border-emerald-200/60 dark:border-emerald-800/40 font-semibold text-emerald-700 dark:text-emerald-400" title="Barcode at receiver for this size">Barcode</th>
                                                                <th className="px-2 py-1.5 text-right font-semibold text-emerald-700 dark:text-emerald-400" title="Receiver stock">SOH</th>
                                                                <th className="px-2 py-1.5 text-right font-semibold text-emerald-700 dark:text-emerald-400" title="Need (`req_*_qty`)">Need</th>
                                                                <th className="px-2 py-1.5 text-right font-semibold text-emerald-700 dark:text-emerald-400" title="Receiver sales this size">Sales</th>
                                                            </tr>
                                                        </thead>
                                                        <tbody className="divide-y divide-slate-100 dark:divide-slate-800/50 [&_tr:nth-child(even)]:bg-slate-50/80 dark:[&_tr:nth-child(even)]:bg-slate-800/40">
                                                            {REC_STACK_SIZE_KEYS.map((row) => {
                                                                const sendQty = stackRecQty(selectedRec, row.tr);
                                                                const recQty = stackRecQty(selectedRec, row.req);
                                                                const donBc = formatStackBarcode(
                                                                    getStackBarcodeField(
                                                                        selectedRec,
                                                                        'don',
                                                                        row.salesSize,
                                                                        row.donBarcode
                                                                    )
                                                                );
                                                                const donSoh = getStackField(selectedRec, row.donSoh, 'soh');
                                                                const recBc = formatStackBarcode(
                                                                    getStackBarcodeField(
                                                                        selectedRec,
                                                                        'rec',
                                                                        row.salesSize,
                                                                        row.recBarcode
                                                                    )
                                                                );
                                                                const recSoh = getStackField(selectedRec, row.recSoh, 'soh');
                                                                const donSales = getStackSalesField(selectedRec, 'don', row.salesSize);
                                                                const recSales = getStackSalesField(selectedRec, 'rec', row.salesSize);
                                                                const hasActivity = sendQty > 0 || recQty > 0;
                                                                return (
                                                                    <tr
                                                                        key={row.label}
                                                                        className={`transition-colors hover:bg-indigo-50/50 dark:hover:bg-indigo-950/30 ${ hasActivity ? 'bg-indigo-500/[0.06] dark:bg-indigo-500/[0.1]' : '' }`}
                                                                    >
                                                                        <td className={`px-2 py-2 font-extrabold ${ hasActivity ? 'text-indigo-700 dark:text-indigo-300' : 'text-slate-500 dark:text-slate-500' }`}>{row.label}</td>
                                                                        {/* Donor */}
                                                                        <td className="min-w-[7.5rem] px-2 py-2 border-l border-rose-100 dark:border-rose-900/30 font-mono text-[10px] text-rose-700 dark:text-rose-300 sm:text-[11px]" title={donBc}>{donBc !== '—' && donBc ? donBc : '—'}</td>
                                                                        <td className="px-2 py-2 text-right font-mono text-sm font-semibold text-rose-800 dark:text-rose-200">{donSoh != null && donSoh !== '' ? fmtIntCell(donSoh) : '—'}</td>
                                                                        <td className="px-2 py-2 text-right font-mono text-rose-700/95 dark:text-rose-300/95">{fmtStackTotalSales(donSales)}</td>
                                                                        {/* Receiver */}
                                                                        <td className="min-w-[7.5rem] px-2 py-2 border-l border-emerald-100 dark:border-emerald-900/30 font-mono text-[10px] text-emerald-700 dark:text-emerald-300 sm:text-[11px]" title={recBc}>{recBc !== '—' && recBc ? recBc : '—'}</td>
                                                                        <td className="px-2 py-2 text-right font-mono text-sm font-semibold text-emerald-800 dark:text-emerald-200">{recSoh != null && recSoh !== '' ? fmtIntCell(recSoh) : '—'}</td>
                                                                        <td className="px-2 py-2 text-right font-mono text-slate-700 dark:text-slate-200">{fmtIntCell(recQty)}</td>
                                                                        <td className="px-2 py-2 text-right font-mono text-emerald-700/95 dark:text-emerald-300/95">{fmtStackTotalSales(recSales)}</td>
                                                                        {/* Send */}
                                                                        <td className={`px-2 py-2 text-right border-l border-indigo-100 dark:border-indigo-900/30 font-mono text-sm font-black tabular-nums ${ sendQty > 0 ? 'text-indigo-600 dark:text-indigo-300' : 'text-slate-400 dark:text-slate-600' }`}>{fmtIntCell(sendQty)}</td>
                                                                    </tr>
                                                                );
                                                            })}
                                                        </tbody>
                                                    </table>
                                                </div>
                                            </div>
                                            <div className="shrink-0 space-y-2 border-t border-slate-200 bg-slate-50/50 p-3 dark:border-slate-800 dark:bg-slate-900/80">
                                                <p className="text-[9px] leading-snug text-slate-500 dark:text-slate-400">
                                                    <span className="text-rose-500 font-semibold">Donor</span>: barcode{' '}
                                                    <span className="font-mono">don_barcode_*</span>, SOH{' '}
                                                    <span className="font-mono">don_soh_*</span>, sales{' '}
                                                    <span className="font-mono">don_sales_*</span>.{' '}
                                                    <span className="text-emerald-600 font-semibold">Receiver</span>: barcode{' '}
                                                    <span className="font-mono">rec_barcode_*</span>, SOH{' '}
                                                    <span className="font-mono">rec_soh_*</span>, Need{' '}
                                                    <span className="font-mono">req_*_qty</span>, sales{' '}
                                                    <span className="font-mono">rec_sales_*</span>.{' '}
                                                    <span className="text-indigo-600 font-semibold">Send</span> = units to transfer per size.
                                                </p>
                                                <div className="flex gap-2">
                                                    <button
                                                        onClick={() => setSelectedRec(null)}
                                                        className="flex-1 rounded-xl bg-slate-200 py-2.5 text-xs font-bold text-slate-700 transition hover:bg-slate-300 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700"
                                                        type="button"
                                                    >
                                                        Cancel
                                                    </button>
                                                    <button
                                                        onClick={() => handleCreateTransfer(selectedRec.id)}
                                                        disabled={creatingTransferId === selectedRec.id}
                                                        className="flex-[1.3] flex items-center justify-center gap-2 rounded-xl bg-indigo-600 py-2.5 text-xs font-bold text-white shadow-md shadow-indigo-500/20 transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-60"
                                                        type="button"
                                                    >
                                                        {creatingTransferId === selectedRec.id ? (
                                                            <>
                                                                <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                                                                Creating
                                                            </>
                                                        ) : (
                                                            <>
                                                                Create transfer
                                                                <ArrowRight className="h-3.5 w-3.5" />
                                                            </>
                                                        )}
                                                    </button>
                                                </div>
                                            </div>
                                            </div>
                                        ) : (
                                        <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
                                            <div className="flex flex-col h-full">
                                                <div className="text-[11px] font-black uppercase text-rose-500 tracking-widest flex items-center gap-2 mb-3">
                                                    <Package className="w-4 h-4" /> Donor Store Detail
                                                </div>
                                                <div className="p-4 bg-slate-50 dark:bg-slate-800/40 rounded-2xl border border-slate-200 dark:border-slate-700/60 flex-1 flex flex-col shadow-inner overflow-hidden">
                                                    <div className="font-bold text-slate-900 dark:text-white text-sm mb-4">
                                                        {selectedRec.donor_store_name || selectedRec.donor_store}
                                                    </div>
                                                    <div className="">

                                                        <table className="w-full text-[10px] text-left border-collapse">
                                                            <thead>
                                                                <tr className="bg-slate-100/50 dark:bg-slate-800 text-slate-500 uppercase font-bold tracking-tighter border-b border-slate-200 dark:border-slate-700">
                                                                    <th className="px-2 py-2">SKU</th>
                                                                    <th className="px-2 py-2">Barcode</th>
                                                                    <th className="px-2 py-2 text-center">Size</th>
                                                                    <th className="px-2 py-2 text-right">Store_SOH</th>
                                                                    <th className="px-2 py-2 text-right">WH_SOH</th>
                                                                    <th className="px-2 py-2 text-right">Last Week<br />Sales</th>
                                                                    <th className="px-2 py-2 text-right">Last 5w<br />sold qty</th>
                                                                    <th className="px-2 py-2 text-right">Total<br />Sales</th>
                                                                </tr>
                                                            </thead>
                                                            <tbody className="divide-y divide-slate-100 dark:divide-slate-800/50">
                                                                {loadingModalSoh ? (
                                                                    <tr>
                                                                        <td colSpan={8} className="px-2 py-6 text-center text-slate-400 text-xs">Loading donor SOH...</td>
                                                                    </tr>
                                                                ) : (
                                                                    <>
                                                                {sortRowsBySize(modalSoh.donor || []).map((sohData, i) => {
                                                                    const s = String(sohData?.size || '');
                                                                    const isSelected = normalizeSize(s) === normalizeSize(selectedRec.size);
                                                                    return (
                                                                        <tr key={s} className={isSelected ? 'bg-indigo-500/5 transition-colors' : ''}>
                                                                            <td className="px-2 py-2 font-mono text-slate-500">{selectedRec.sku}</td>
                                                                            <td className="px-2 py-2 font-mono text-slate-400">{sohData?.barcode || '—'}</td>
                                                                            <td className={`px-2 py-2 text-center font-bold ${isSelected ? 'text-indigo-500' : 'text-slate-700 dark:text-slate-300'}`}>{s}</td>
                                                                            <td className={`px-2 py-2 text-right font-black ${isSelected ? 'text-indigo-600 dark:text-indigo-400' : 'text-slate-900 dark:text-white'}`}>
                                                                                {sohData?.store_soh ?? 0}
                                                                            </td>
                                                                            <td className="px-2 py-2 text-right font-medium text-slate-500">{sohData?.wh_soh ?? 0}</td>
                                                                            <td className="px-2 py-2 text-right font-mono text-slate-500">{fmtIntCell(sohData?.last_week_sales)}</td>
                                                                            <td className="px-2 py-2 text-right font-mono text-slate-500">{fmtIntCell(sohData?.sold_qty_5w)}</td>
                                                                            <td className="px-2 py-2 text-right font-mono text-slate-500">{fmtIntCell(sohData?.total_sales)}</td>
                                                                        </tr>
                                                                    );
                                                                })}
                                                                {(!modalSoh.donor || modalSoh.donor.length === 0) && (
                                                                    <tr>
                                                                        <td colSpan={8} className="px-2 py-3 text-center text-slate-400 text-xs">No donor SOH rows found.</td>
                                                                    </tr>
                                                                )}
                                                                    </>
                                                                )}
                                                            </tbody>
                                                        </table>
                                                    </div>
                                                </div>
                                            </div>

                                            <div className="flex flex-col h-full">
                                                <div className="text-[11px] font-black uppercase text-emerald-500 tracking-widest flex items-center gap-2 mb-3">
                                                    <Package className="w-4 h-4" /> Receiver Store Detail
                                                </div>
                                                <div className="p-4 bg-slate-50 dark:bg-slate-800/40 rounded-2xl border border-slate-200 dark:border-slate-700/60 flex-1 flex flex-col shadow-inner overflow-hidden">
                                                    <div className="font-bold text-slate-900 dark:text-white text-sm mb-4">
                                                        {selectedRec.receiver_store_name || selectedRec.receiver_store}
                                                    </div>
                                                    <div className="mb-3 flex items-center justify-between rounded-lg border border-emerald-200/70 dark:border-emerald-800/70 bg-emerald-500/5 px-3 py-2">
                                                        <span className="text-[10px] font-bold uppercase tracking-wider text-emerald-600 dark:text-emerald-400">
                                                            Recommended Refill
                                                        </span>
                                                        <span className="text-sm font-black text-emerald-600 dark:text-emerald-300">
                                                            {(loadingModalSoh || selectedRecRecommendedRefill === null) ? 'Loading...' : selectedRecRecommendedRefill}
                                                        </span>
                                                    </div>
                                                    <div className="">
                                                        <table className="w-full text-[10px] text-left border-collapse">
                                                            <thead>
                                                                <tr className="bg-slate-100/50 dark:bg-slate-800 text-slate-500 uppercase font-bold tracking-tighter border-b border-slate-200 dark:border-slate-700">
                                                                    <th className="px-2 py-2">SKU</th>
                                                                    <th className="px-2 py-2">Barcode</th>
                                                                    <th className="px-2 py-2 text-center">Size</th>
                                                                    <th className="px-2 py-2 text-right">Store_SOH</th>
                                                                    <th className="px-2 py-2 text-right">WH_SOH</th>
                                                                    <th className="px-2 py-2 text-right">Last Week<br />Sales</th>
                                                                    <th className="px-2 py-2 text-right">Last 5w<br />sold qty</th>
                                                                    <th className="px-2 py-2 text-right">Total<br />Sales</th>
                                                                </tr>
                                                            </thead>
                                                            <tbody className="divide-y divide-slate-100 dark:divide-slate-800/50">
                                                                {loadingModalSoh ? (
                                                                    <tr>
                                                                        <td colSpan={8} className="px-2 py-6 text-center text-slate-400 text-xs">Loading receiver SOH...</td>
                                                                    </tr>
                                                                ) : (
                                                                    <>
                                                                {sortRowsBySize(modalSoh.receiver || []).map((sohData, i) => {
                                                                    const s = String(sohData?.size || '');
                                                                    const isSelected = normalizeSize(s) === normalizeSize(selectedRec.size);
                                                                    return (
                                                                        <tr key={s} className={isSelected ? 'bg-rose-500/5 transition-colors' : ''}>
                                                                            <td className="px-2 py-2 font-mono text-slate-500">{selectedRec.sku}</td>
                                                                            <td className="px-2 py-2 font-mono text-slate-400">{sohData?.barcode || '—'}</td>
                                                                            <td className={`px-2 py-2 text-center font-bold ${isSelected ? 'text-rose-500' : 'text-slate-700 dark:text-slate-300'}`}>{s}</td>
                                                                            <td className={`px-2 py-2 text-right font-black ${isSelected ? 'text-rose-600 dark:text-rose-400' : 'text-slate-900 dark:text-white'}`}>
                                                                                {sohData?.store_soh ?? 0}
                                                                            </td>
                                                                            <td className="px-2 py-2 text-right font-medium text-slate-500">{sohData?.wh_soh ?? 0}</td>
                                                                            <td className="px-2 py-2 text-right font-mono text-slate-500">{fmtIntCell(sohData?.last_week_sales)}</td>
                                                                            <td className="px-2 py-2 text-right font-mono text-slate-500">{fmtIntCell(sohData?.sold_qty_5w)}</td>
                                                                            <td className="px-2 py-2 text-right font-mono text-slate-500">{fmtIntCell(sohData?.total_sales)}</td>
                                                                        </tr>
                                                                    );
                                                                })}
                                                                {(!modalSoh.receiver || modalSoh.receiver.length === 0) && (
                                                                    <tr>
                                                                        <td colSpan={8} className="px-2 py-3 text-center text-slate-400 text-xs">No receiver SOH rows found.</td>
                                                                    </tr>
                                                                )}
                                                                    </>
                                                                )}
                                                            </tbody>
                                                        </table>
                                                    </div>
                                                </div>
                                            </div>
                                        </div>
                                        )}

                                        {!isNewRecommendedStackRec(selectedRec) && (
                                            <>
                                        <div className="mt-4 text-xs text-slate-500 dark:text-slate-400 bg-slate-50 dark:bg-slate-800/30 border border-slate-200 dark:border-slate-700 rounded-xl px-3 py-2">
                                            Transfer plan: required size quantity will always transfer. If donor has other sizes in stock, one unit of each available size is also included.
                                        </div>

                                        <div className="mt-10 flex gap-4">
                                            <button onClick={() => setSelectedRec(null)} className="flex-1 py-4 bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-white font-bold rounded-2xl hover:bg-slate-200 dark:hover:bg-slate-700 transition-all uppercase tracking-widest text-xs">
                                                Cancel
                                            </button>
                                            <button
                                                onClick={() => handleCreateTransfer(selectedRec.id)}
                                                disabled={creatingTransferId === selectedRec.id || (!isNewRecommendedStackRec(selectedRec) && loadingModalSoh)}
                                                className="flex-[2] py-4 bg-indigo-600 text-white font-bold rounded-2xl shadow-xl shadow-indigo-500/30 hover:bg-indigo-700 disabled:opacity-60 disabled:cursor-not-allowed transform hover:-translate-y-1 transition-all uppercase tracking-widest text-xs flex items-center justify-center gap-3"
                                            >
                                                {creatingTransferId === selectedRec.id ? (
                                                    <>
                                                        <RefreshCw className="w-4 h-4 animate-spin" />
                                                        Creating...
                                                    </>
                                                ) : (
                                                    <>
                                                        Create Transfer
                                                        <ArrowRight className="w-4 h-4" />
                                                    </>
                                                )}
                                            </button>
                                        </div>
                                            </>
                                        )}
                                    </div>
                                </div>
                            </div>
                        )}

                        {optimizerSubTab === 'current' && (
                        <div>
                            <h2 className="text-lg font-semibold text-slate-900 dark:text-white mb-1 flex items-center gap-2">
                                <Truck className="w-5 h-5 text-indigo-500" />
                                In transit
                            </h2>
                            <p className="mb-3 text-xs text-slate-500 dark:text-slate-400">
                                Columns match OneLake <span className="font-mono text-slate-600 dark:text-slate-300">dbo.current_transfers</span>
                                (loaded from <span className="font-mono text-slate-600 dark:text-slate-300">/transfers-recom/current</span>).
                            </p>
                            <div className="bg-white dark:bg-slate-900/50 border border-slate-200 dark:border-slate-800 rounded-xl overflow-hidden shadow-sm">
                                {loadingCurrent && sanitizedCurrentTransfers.length === 0 ? (
                                    <div className="relative flex min-h-[min(36vh,320px)] w-full items-center justify-center border-y border-slate-100/80 bg-slate-50/30 py-8 dark:border-slate-800/50 dark:bg-slate-900/20">
                                        <TransfersTableLoaderBlock />
                                    </div>
                                ) : sanitizedCurrentTransfers.length > 0 ? (
                                    <div className="max-h-[min(70vh,720px)] overflow-auto">
                                        <table className="w-full min-w-[1280px] border-collapse text-left text-[11px]">
                                            <thead>
                                                <tr className="sticky top-0 z-10 border-b border-slate-200 bg-slate-100/95 text-slate-600 shadow-sm backdrop-blur dark:border-slate-700 dark:bg-slate-800/95 dark:text-slate-300">
                                                    {IN_TRANSIT_ONE_LAKE_COLS.map((col) => (
                                                        <th key={col.id} className="whitespace-nowrap px-2 py-2 text-left text-[10px] font-bold uppercase tracking-wide">
                                                            {col.label}
                                                        </th>
                                                    ))}
                                                </tr>
                                            </thead>
                                            <tbody className="divide-y divide-slate-100 dark:divide-slate-800/80">
                                                {sanitizedCurrentTransfers.map((item: any, idx: number) => {
                                                    const rowKey = String(
                                                        item.transfer_id ?? item.Transfer_ID ?? `ct-${idx}`
                                                    );
                                                    return (
                                                        <Fragment key={rowKey}>
                                                            <tr className="align-top transition-colors hover:bg-slate-50/80 dark:hover:bg-slate-800/40">
                                                                {IN_TRANSIT_ONE_LAKE_COLS.map((col) => {
                                                                    const cell = formatInTransitCell(item, col);
                                                                    return (
                                                                        <td
                                                                            key={col.id}
                                                                            className="max-w-[11rem] truncate px-2 py-1.5 font-mono text-slate-800 dark:text-slate-200"
                                                                            title={cell}
                                                                        >
                                                                            {cell}
                                                                        </td>
                                                                    );
                                                                })}
                                                            </tr>
                                                            {inTransitRowHasSizeBreakdown(item) && (
                                                                <tr className="bg-slate-50/60 dark:bg-slate-800/25">
                                                                    <td
                                                                        colSpan={IN_TRANSIT_ONE_LAKE_COLS.length}
                                                                        className="px-2 py-1.5"
                                                                    >
                                                                        <details className="text-[10px]">
                                                                            <summary className="cursor-pointer font-semibold text-indigo-600 dark:text-indigo-400">
                                                                                Per-size columns (from approved stack row)
                                                                            </summary>
                                                                            <div className="mt-1 overflow-x-auto rounded-md border border-slate-200 dark:border-slate-700">
                                                                                <table className="w-full min-w-[360px] border-collapse text-left">
                                                                                    <thead>
                                                                                        <tr className="border-b border-slate-200 bg-slate-100/60 text-slate-500 dark:border-slate-700 dark:bg-slate-800/80">
                                                                                            <th className="px-2 py-1">Size</th>
                                                                                            <th className="px-2 py-1 text-right">Need</th>
                                                                                            <th className="px-2 py-1 text-right" title="Donor SOH per size">Donor SOH</th>
                                                                                            <th className="px-2 py-1 text-right text-indigo-600 dark:text-indigo-300">
                                                                                                To send
                                                                                            </th>
                                                                                        </tr>
                                                                                    </thead>
                                                                                    <tbody>
                                                                                        {REC_STACK_SIZE_KEYS.map((row) => (
                                                                                            <tr key={row.label}>
                                                                                                <td className="px-2 py-0.5 font-semibold text-slate-800 dark:text-slate-200">
                                                                                                    {row.label}
                                                                                                </td>
                                                                                                <td className="px-2 py-0.5 text-right font-mono text-slate-600 dark:text-slate-400">
                                                                                                    {fmtIntCell(
                                                                                                        stackRecQty(
                                                                                                            item,
                                                                                                            row.req
                                                                                                        )
                                                                                                    )}
                                                                                                </td>
                                                                                                <td className="px-2 py-0.5 text-right font-mono text-slate-600 dark:text-slate-400">
                                                                                                    {fmtIntCell(getStackField(item, row.donSoh, 'soh'))}
                                                                                                </td>
                                                                                                <td className="px-2 py-0.5 text-right font-mono font-semibold text-indigo-600 dark:text-indigo-300">
                                                                                                    {fmtIntCell(
                                                                                                        stackRecQty(
                                                                                                            item,
                                                                                                            row.tr
                                                                                                        )
                                                                                                    )}
                                                                                                </td>
                                                                                            </tr>
                                                                                        ))}
                                                                                    </tbody>
                                                                                </table>
                                                                            </div>
                                                                        </details>
                                                                    </td>
                                                                </tr>
                                                            )}
                                                        </Fragment>
                                                    );
                                                })}
                                            </tbody>
                                        </table>
                                    </div>
                                ) : (
                                    <div className="p-12 text-center text-slate-400 italic font-medium">
                                        No active transfers currently being tracked.
                                    </div>
                                )}
                            </div>
                        </div>
                        )}

                        <div className="p-4 bg-indigo-500/5 border border-indigo-500/10 rounded-xl">
                            <p className="text-xs text-indigo-600 dark:text-indigo-400 flex items-center gap-2">
                                <Truck className="w-4 h-4" />
                                Approve a recommendation on the <strong>Recommended</strong> tab; in‑transit rows appear on <strong>In transit</strong> after you load that tab.
                            </p>
                        </div>
                    </>
                ) : (
                    <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
                        <div className="bg-white dark:bg-slate-900/50 border border-slate-200 dark:border-slate-800 rounded-2xl overflow-hidden shadow-xl border-t-4 border-t-indigo-500">
                            <div className="p-6 border-b border-slate-100 dark:border-slate-800 flex items-center justify-between">
                                <div className="flex flex-col gap-1">
                                    <h2 className="text-lg font-bold text-slate-900 dark:text-white flex items-center gap-2">
                                        <Package className="w-5 h-5 text-indigo-500" />
                                        No Sales Donors
                                    </h2>
                                    <div className="flex items-center gap-3 mt-2">
                                        <div className="inline-flex items-center gap-2 rounded-lg border border-slate-200 dark:border-slate-700 px-3 py-2 bg-white dark:bg-slate-900 min-w-[260px]">
                                            <span className="text-xs font-semibold text-slate-500 dark:text-slate-400">N Days</span>
                                            <input
                                                type="range"
                                                min={0}
                                                max={Math.max(zeroDaysMax, 1)}
                                                value={Math.min(zeroDaysFilter, Math.max(zeroDaysMax, 1))}
                                                onChange={(e) => setZeroDaysFilter(Number(e.target.value))}
                                                className="w-36"
                                            />
                                            <span className="text-xs font-semibold text-indigo-500 w-16 text-right">
                                                0-{Math.min(zeroDaysFilter, zeroDaysMax || zeroDaysFilter)}
                                            </span>
                                        </div>
                                    </div>
                                </div>
                                <div className="text-right flex flex-col items-end gap-2">
                                    <button
                                        type="button"
                                        onClick={handleExportNoSalesDonors}
                                        disabled={loadingZeroSoh || (zeroSohTotal ?? 0) === 0}
                                        className="inline-flex items-center gap-2 rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-2 text-xs font-bold uppercase tracking-wide text-indigo-700 shadow-sm transition hover:bg-indigo-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-indigo-500/30 dark:bg-indigo-500/10 dark:text-indigo-200 dark:hover:bg-indigo-500/20"
                                    >
                                        <Download className="h-4 w-4" aria-hidden />
                                        Export Excel
                                    </button>
                                    <div>
                                        <span className="text-2xl font-black text-indigo-600 dark:text-indigo-400">
                                            {loadingZeroSoh ? '...' : zeroSohTotal}
                                        </span>
                                        <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mt-1">Total Records</p>
                                    </div>
                                </div>
                            </div>

                            {!loadingZeroSoh && (
                                <div className="p-6 border-b border-slate-100 dark:border-slate-800 grid grid-cols-1 lg:grid-cols-4 gap-4">
                                    <div className="rounded-xl border border-indigo-200 dark:border-indigo-800 bg-gradient-to-br from-indigo-50 to-white dark:from-indigo-950/40 dark:to-slate-900 p-4">
                                        <p className="text-xs uppercase tracking-wider text-slate-500">Total SOH</p>
                                        <p className="text-3xl font-black text-indigo-600 mt-1">{(zeroSohSummary.total_soh || 0).toLocaleString()}</p>
                                        <div className="mt-3 space-y-1 text-[12px]">
                                            <div className="flex items-center justify-between text-slate-600 dark:text-slate-300">
                                                <span>SOH (no transfer date)</span>
                                                <span className="font-bold text-amber-600 dark:text-amber-300">
                                                    {(zeroSohSummary.total_soh_no_transfer_date || 0).toLocaleString()}
                                                </span>
                                            </div>
                                            <div className="flex items-center justify-between text-slate-600 dark:text-slate-300">
                                                <span>SOH (with transfer date)</span>
                                                <span className="font-bold text-emerald-600 dark:text-emerald-300">
                                                    {(zeroSohSummary.total_soh_with_transfer_date || 0).toLocaleString()}
                                                </span>
                                            </div>
                                        </div>
                                    </div>
                                    <div className="rounded-xl border border-emerald-200 dark:border-emerald-800 bg-gradient-to-br from-emerald-50 to-white dark:from-emerald-950/40 dark:to-slate-900 p-4">
                                        <p className="text-xs uppercase tracking-wider text-slate-500">Total Stock Amount</p>
                                        <p className="text-3xl font-black text-emerald-600 mt-1">
                                            {(zeroSohSummary.total_revenue || 0).toLocaleString()}
                                        </p>
                                        <p className="text-[11px] text-slate-500 mt-2">
                                            {zeroFullyLoaded ? "Fully loaded from table" : `Loaded ${zeroSohData.length.toLocaleString()} of ${zeroSohTotal.toLocaleString()} rows...`}
                                        </p>
                                    </div>
                                    <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50/60 dark:bg-slate-800/40 p-4">
                                        <p className="text-xs uppercase tracking-wider text-slate-500 mb-2">Top 5 Sites by Stock Amount</p>
                                        <div className="space-y-2">
                                            {(zeroSohSummary.top_sites_by_amount || []).slice(0, 5).map((s: any, idx: number) => {
                                                const maxAmt = Number((zeroSohSummary.top_sites_by_amount || [])[0]?.total_stock_amount || 1);
                                                const width = Math.max(8, Math.round((Number(s.total_stock_amount || 0) / maxAmt) * 100));
                                                return (
                                                    <div key={`${s.site}-${idx}`} className="space-y-1">
                                                        <div className="flex items-center justify-between text-xs">
                                                            <span className="font-medium text-slate-700 dark:text-slate-300 truncate max-w-[140px]">{s.site}</span>
                                                            <span className="font-semibold text-indigo-500">{Number(s.total_stock_amount || 0).toLocaleString()}</span>
                                                        </div>
                                                        <div className="h-2 rounded bg-slate-200 dark:bg-slate-700 overflow-hidden">
                                                            <div className="h-full bg-indigo-500 rounded" style={{ width: `${width}%` }} />
                                                        </div>
                                                    </div>
                                                );
                                            })}
                                        </div>
                                    </div>
                                    <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50/60 dark:bg-slate-800/40 p-4">
                                        <p className="text-xs uppercase tracking-wider text-slate-500 mb-2">Top 5 SKU by Stock Amount</p>
                                        <div className="space-y-2">
                                            {(zeroSohSummary.top_skus_by_amount || []).slice(0, 5).map((s: any, idx: number) => {
                                                const maxAmt = Number((zeroSohSummary.top_skus_by_amount || [])[0]?.total_stock_amount || 1);
                                                const width = Math.max(8, Math.round((Number(s.total_stock_amount || 0) / maxAmt) * 100));
                                                return (
                                                    <div key={`${s.sku}-${idx}`} className="space-y-1">
                                                        <div className="flex items-center justify-between text-xs">
                                                            <span className="font-mono text-slate-700 dark:text-slate-300 truncate max-w-[140px]">{s.sku}</span>
                                                            <span className="font-semibold text-emerald-500">{Number(s.total_stock_amount || 0).toLocaleString()}</span>
                                                        </div>
                                                        <div className="h-2 rounded bg-slate-200 dark:bg-slate-700 overflow-hidden">
                                                            <div className="h-full bg-emerald-500 rounded" style={{ width: `${width}%` }} />
                                                        </div>
                                                    </div>
                                                );
                                            })}
                                        </div>
                                    </div>
                                </div>
                            )}

                            <div className="overflow-x-auto overflow-y-auto max-h-[520px] custom-scrollbar">
                                <table className="w-full text-sm text-left border-collapse">
                                    <thead>
                                        <tr className="bg-slate-50 dark:bg-slate-800/80 text-slate-500 uppercase text-[10px] font-black tracking-widest border-b border-slate-100 dark:border-slate-800">
                                            {zeroSohCols.map(col => (
                                                <th key={col} className="px-6 py-5 whitespace-nowrap sticky top-0 bg-slate-50 dark:bg-slate-800/80">{col.replace(/_/g, ' ')}</th>
                                            ))}
                                        </tr>
                                        <tr className="bg-white dark:bg-slate-900 border-b border-slate-100 dark:border-slate-800">
                                            {zeroSohCols.map(col => (
                                                <th key={`${col}-filter`} className="px-6 py-3 sticky top-[48px] bg-white dark:bg-slate-900">
                                                    <input
                                                        value={zeroSohColumnFilters[col] || ''}
                                                        onChange={(e) => setZeroSohColumnFilters((prev) => ({ ...prev, [col]: e.target.value }))}
                                                        placeholder={`Filter ${col.replace(/_/g, ' ')}`}
                                                        className="w-full px-2 py-1 rounded border border-slate-200 dark:border-slate-700 bg-transparent text-xs font-normal text-slate-700 dark:text-slate-300 outline-none"
                                                    />
                                                </th>
                                            ))}
                                        </tr>
                                    </thead>
                                    <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                                        {loadingZeroSoh ? (
                                            <tr>
                                                <td colSpan={Math.max(zeroSohCols.length, 1)} className="px-6 py-16 text-center text-slate-400">
                                                    <div className="flex items-center justify-center gap-3">
                                                        <RefreshCw className="w-5 h-5 animate-spin" />
                                                        <span className="font-medium">Loading no-sales donor data…</span>
                                                    </div>
                                                </td>
                                            </tr>
                                        ) : filteredZeroSohRows.length > 0 ? (
                                            filteredZeroSohRows.map((row: any, idx: number) => (
                                                <tr key={idx} className="hover:bg-slate-50 dark:hover:bg-white/5 transition-all group">
                                                    {zeroSohCols.map(col => {
                                                        const val = row[col];
                                                        const isNumeric = typeof val === 'number';
                                                        return (
                                                            <td key={col} className={`px-6 py-4 font-medium text-slate-700 dark:text-slate-300 ${isNumeric ? 'font-mono text-right text-indigo-500' : ''}`}>
                                                                {formatZeroSohCellValue(val)}
                                                            </td>
                                                        );
                                                    })}
                                                </tr>
                                            ))
                                        ) : (
                                            <tr>
                                                <td colSpan={zeroSohCols.length || 1} className="px-6 py-20 text-center">
                                                    <div className="flex flex-col items-center justify-center gap-4 text-slate-400">
                                                        <div className="p-4 bg-slate-100 dark:bg-slate-800 rounded-full">
                                                            <Package className="w-10 h-10 opacity-20" />
                                                        </div>
                                                        <div>
                                                            <p className="text-lg font-bold">No Records Found</p>
                                                            <p className="text-sm">No no-sales donor records are available for this view. Try a wider date range or run Refresh / Sync.</p>
                                                        </div>
                                                    </div>
                                                </td>
                                            </tr>
                                        )}
                                    </tbody>
                                </table>
                            </div>

                            {zeroSohError && (
                                <div className="m-6 p-4 bg-rose-500/10 border border-rose-500/20 rounded-xl flex items-center gap-3 text-rose-500">
                                    <div className="p-2 bg-rose-500/20 rounded-lg">
                                        <X className="w-5 h-5" />
                                    </div>
                                    <div>
                                        <p className="font-bold text-sm">Data Loading Error</p>
                                        <p className="text-xs opacity-80">{zeroSohError}</p>
                                    </div>
                                </div>
                            )}
                        </div>
                    </div>
                )}
            </div>
        </DashboardLayout>
    );
}

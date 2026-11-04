import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Calendar,
  Check,
  ChevronRight,
  Clock,
  Copy,
  ExternalLink,
  Flame,
  IndianRupee,
  Layers,
  Loader2,
  PackageCheck,
  Percent,
  Shirt,
  ShoppingBag,
  Sparkles,
  Store,
  TrendingDown,
  TrendingUp,
  Warehouse,
} from 'lucide-react';
import { API_URL } from '../../../config';
import { formatCompactInr, formatInr } from '../product-performance/productData';

export type SkuCardData = {
  ok: boolean;
  sku: string;
  barcode: string;
  ref_no: string;
  product_type: string;
  category: string;
  department: string;
  section: string;
  season: string;
  story_name: string;
  image_url: string;
  launch_date: string;
  shelf_life_days: number;
  style_analysis: string;
  offer_status: string;
  costing: number;
  mrp: number;
  ho_soh_qty: number;
  ho_amt_cost: number;
  floor_soh_qty: number;
  floor_amt_cost: number;
  dispatch_qty: number;
  production_qty: number;
  production_amt_cost: number;
  production_amt_mrp: number;
  fy27: {
    qty: number;
    sales: number;
    gross: number;
    asp: number;
    discount_pct: number;
  };
  fy26: {
    qty: number;
    sales: number;
    gross: number;
    asp: number;
    discount_pct: number;
  };
  yoy_qty_growth_pct: number | null;
  yoy_sales_growth_pct: number | null;
  total_sold_qty: number;
  total_net_sales: number;
  total_discount_pct: number;
  sell_through_pct: number;
  offline_st_dispatch_pct: number;
  ros: number;
  wk_minus_1_qty: number;
  wk_minus_2_qty: number;
  wk_minus_3_qty: number;
  breakeven_val: number;
  active_stores_count: number;
};

// Global in-memory cache so repeated hovers are instant (0ms)
const SKU_CACHE = new Map<string, SkuCardData>();

interface SkuHoverCardProps {
  sku: string;
  children?: React.ReactNode;
  className?: string;
  underline?: boolean;
  showIcon?: boolean;
  asLink?: boolean;
  key?: React.Key;
}

export function SkuHoverCard({
  sku,
  children,
  className = '',
  underline = true,
  showIcon = false,
  asLink = false,
}: SkuHoverCardProps) {
  const cleanSku = (sku || '').trim().toUpperCase();
  const [isOpen, setIsOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<SkuCardData | null>(() => (cleanSku ? SKU_CACHE.get(cleanSku) || null : null));
  const [copied, setCopied] = useState(false);
  const [imgError, setImgError] = useState(false);
  const [pos, setPos] = useState<{ x: number; y: number; placement: 'bottom' | 'top' }>({
    x: 0,
    y: 0,
    placement: 'bottom',
  });

  const triggerRef = useRef<HTMLSpanElement>(null);
  const enterTimer = useRef<NodeJS.Timeout | null>(null);
  const leaveTimer = useRef<NodeJS.Timeout | null>(null);

  const fetchData = useCallback(async () => {
    if (!cleanSku) return;
    if (SKU_CACHE.has(cleanSku)) {
      setData(SKU_CACHE.get(cleanSku)!);
      return;
    }
    setLoading(true);
    try {
      const res = await fetch(`${API_URL}/product-performance/sku-card/${encodeURIComponent(cleanSku)}`);
      if (res.ok) {
        const json: SkuCardData = await res.json();
        if (json && json.ok) {
          SKU_CACHE.set(cleanSku, json);
          setData(json);
        }
      }
    } catch {
      // ignore network errors on fast hover
    } finally {
      setLoading(false);
    }
  }, [cleanSku]);

  const updatePosition = () => {
    if (!triggerRef.current) return;
    const rect = triggerRef.current.getBoundingClientRect();
    const cardWidth = 360;
    const cardHeight = 380;
    const padding = 12;

    let x = rect.left + window.scrollX;
    if (x + cardWidth > window.innerWidth - padding) {
      x = window.innerWidth - cardWidth - padding;
    }
    if (x < padding) x = padding;

    let y = rect.bottom + window.scrollY + 6;
    let placement: 'bottom' | 'top' = 'bottom';

    if (rect.bottom + cardHeight > window.innerHeight && rect.top > cardHeight) {
      y = rect.top + window.scrollY - cardHeight - 6;
      placement = 'top';
    }

    setPos({ x, y, placement });
  };

  const handleMouseEnter = () => {
    if (leaveTimer.current) clearTimeout(leaveTimer.current);
    enterTimer.current = setTimeout(() => {
      updatePosition();
      setIsOpen(true);
      if (!data) fetchData();
    }, 180);
  };

  const handleMouseLeave = () => {
    if (enterTimer.current) clearTimeout(enterTimer.current);
    leaveTimer.current = setTimeout(() => {
      setIsOpen(false);
    }, 200);
  };

  const handleCopy = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!cleanSku) return;
    navigator.clipboard.writeText(cleanSku);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  if (!cleanSku) return <>{children || '—'}</>;

  const moverBadge = () => {
    const type = data?.style_analysis;
    if (type === 'FAST_MOVER') {
      return (
        <span className="inline-flex items-center gap-1 rounded-md bg-emerald-500/10 px-2 py-0.5 text-[10px] font-bold text-emerald-600 dark:text-emerald-400">
          <Flame className="h-3 w-3" /> Fast Mover
        </span>
      );
    }
    if (type === 'SLOW_MOVER') {
      return (
        <span className="inline-flex items-center gap-1 rounded-md bg-amber-500/10 px-2 py-0.5 text-[10px] font-bold text-amber-600 dark:text-amber-400">
          <Clock className="h-3 w-3" /> Slow Mover
        </span>
      );
    }
    if (type === 'NON_MOVER') {
      return (
        <span className="inline-flex items-center gap-1 rounded-md bg-rose-500/10 px-2 py-0.5 text-[10px] font-bold text-rose-600 dark:text-rose-400">
          Non Mover
        </span>
      );
    }
    return (
      <span className="inline-flex items-center rounded-md bg-slate-100 px-2 py-0.5 text-[10px] font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-400">
        Regular
      </span>
    );
  };

  return (
    <>
      <span
        ref={triggerRef}
        onMouseEnter={handleMouseEnter}
        onMouseLeave={handleMouseLeave}
        className={`group relative inline-flex cursor-pointer items-center gap-1 font-mono font-medium transition-colors ${
          underline ? 'underline decoration-blue-300 decoration-dotted underline-offset-2 hover:decoration-blue-600' : ''
        } text-blue-600 hover:text-blue-800 dark:text-blue-400 dark:hover:text-blue-200 ${className}`}
      >
        {showIcon && <Shirt className="h-3 w-3 shrink-0 text-blue-500/70" />}
        <span>{children || cleanSku}</span>
      </span>

      {/* Popover Portal */}
      {isOpen && (
        <div
          onMouseEnter={handleMouseEnter}
          onMouseLeave={handleMouseLeave}
          style={{
            position: 'absolute',
            left: `${pos.x}px`,
            top: `${pos.y}px`,
            zIndex: 99999,
          }}
          className="w-[360px] animate-in fade-in zoom-in-95 duration-150 rounded-2xl border border-slate-200/90 bg-white/95 p-4 shadow-2xl backdrop-blur-xl dark:border-slate-700/80 dark:bg-slate-900/95 dark:text-slate-100"
        >
          {loading && !data ? (
            <div className="flex h-48 flex-col items-center justify-center gap-2 text-slate-400">
              <Loader2 className="h-6 w-6 animate-spin text-blue-600" />
              <span className="text-xs font-medium">Fetching product intelligence…</span>
            </div>
          ) : data ? (
            <div className="flex flex-col gap-3">
              {/* Top Header Card */}
              <div className="flex items-start gap-3">
                <div className="relative h-16 w-14 shrink-0 overflow-hidden rounded-xl border border-slate-200 bg-slate-100 dark:border-slate-800 dark:bg-slate-950">
                  {imgError ? (
                    <div className="flex h-full w-full items-center justify-center text-slate-400">
                      <Shirt className="h-6 w-6" />
                    </div>
                  ) : (
                    <img
                      src={`${API_URL}/product-images/${encodeURIComponent(cleanSku)}`}
                      alt={cleanSku}
                      onError={() => setImgError(true)}
                      className="h-full w-full object-cover transition-transform hover:scale-110"
                    />
                  )}
                </div>

                <div className="flex flex-1 flex-col overflow-hidden">
                  <div className="flex items-center justify-between gap-1">
                    <span className="font-mono text-sm font-bold text-slate-900 dark:text-white">{data.sku}</span>
                    <button
                      type="button"
                      onClick={handleCopy}
                      title="Copy SKU"
                      className="rounded-md p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700 dark:hover:bg-slate-800 dark:hover:text-slate-200"
                    >
                      {copied ? <Check className="h-3.5 w-3.5 text-emerald-500" /> : <Copy className="h-3.5 w-3.5" />}
                    </button>
                  </div>

                  <div className="flex flex-wrap items-center gap-1 text-[11px] text-slate-500 dark:text-slate-400">
                    {data.ref_no && <span className="font-mono font-semibold text-slate-700 dark:text-slate-300">Ref: {data.ref_no}</span>}
                    {data.product_type && <span>· {data.product_type}</span>}
                    {data.story_name && <span>· {data.story_name}</span>}
                  </div>

                  <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                    {moverBadge()}
                    {data.section && (
                      <span className="rounded-md bg-slate-100 px-1.5 py-0.5 text-[10px] font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                        {data.section}
                      </span>
                    )}
                  </div>
                </div>
              </div>

              {/* Price & Costing Strip */}
              <div className="grid grid-cols-2 gap-2 rounded-xl bg-slate-50 p-2.5 text-xs dark:bg-slate-950/60">
                <div>
                  <span className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider">MRP</span>
                  <div className="text-sm font-bold text-slate-800 dark:text-slate-100">{formatInr(data.mrp)}</div>
                </div>
                <div>
                  <span className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider">Costing</span>
                  <div className="text-sm font-bold text-slate-800 dark:text-slate-100">{formatInr(data.costing)}</div>
                </div>
              </div>

              {/* FY-27 Sales & Velocity Highlight (USER'S MAIN REQUEST) */}
              <div className="rounded-xl border border-blue-100 bg-blue-50/50 p-3 dark:border-blue-900/40 dark:bg-blue-950/20">
                <div className="flex items-center justify-between text-xs font-bold text-blue-900 dark:text-blue-200">
                  <div className="flex items-center gap-1">
                    <Sparkles className="h-3.5 w-3.5 text-blue-600 dark:text-blue-400" />
                    <span>FY 2026-27 Performance</span>
                  </div>
                  {data.yoy_qty_growth_pct !== null && (
                    <span
                      className={`inline-flex items-center gap-0.5 rounded-md px-1.5 py-0.5 text-[10px] font-bold ${
                        data.yoy_qty_growth_pct >= 0
                          ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-300'
                          : 'bg-rose-100 text-rose-700 dark:bg-rose-950/60 dark:text-rose-300'
                      }`}
                    >
                      {data.yoy_qty_growth_pct >= 0 ? <TrendingUp className="h-3 w-3" /> : <TrendingDown className="h-3 w-3" />}
                      {data.yoy_qty_growth_pct > 0 ? `+${data.yoy_qty_growth_pct}%` : `${data.yoy_qty_growth_pct}%`} YoY
                    </span>
                  )}
                </div>

                <div className="mt-2 grid grid-cols-3 gap-2 text-center">
                  <div className="rounded-lg bg-white p-1.5 shadow-xs dark:bg-slate-900">
                    <span className="text-[9px] font-semibold text-slate-400 uppercase">FY-27 Qty</span>
                    <div className="text-xs font-bold text-slate-900 dark:text-white">
                      {data.fy27.qty.toLocaleString('en-IN')}
                    </div>
                  </div>
                  <div className="rounded-lg bg-white p-1.5 shadow-xs dark:bg-slate-900">
                    <span className="text-[9px] font-semibold text-slate-400 uppercase">FY-27 Sales</span>
                    <div className="text-xs font-bold text-slate-900 dark:text-white">
                      {formatCompactInr(data.fy27.sales)}
                    </div>
                  </div>
                  <div className="rounded-lg bg-white p-1.5 shadow-xs dark:bg-slate-900">
                    <span className="text-[9px] font-semibold text-slate-400 uppercase">Sell Through</span>
                    <div className="text-xs font-bold text-blue-600 dark:text-blue-400">
                      {data.sell_through_pct.toFixed(1)}%
                    </div>
                  </div>
                </div>
              </div>

              {/* SOH Inventory Snapshot */}
              <div className="grid grid-cols-3 gap-1.5 text-center text-xs">
                <div className="rounded-lg border border-slate-100 bg-slate-50/80 p-2 dark:border-slate-800 dark:bg-slate-950/40">
                  <span className="text-[9px] font-medium text-slate-400">Warehouse SOH</span>
                  <div className="text-xs font-bold text-slate-800 dark:text-slate-200">
                    {data.ho_soh_qty.toLocaleString('en-IN')}
                  </div>
                  <span className="text-[9px] text-slate-400">{formatCompactInr(data.ho_amt_cost)}</span>
                </div>
                <div className="rounded-lg border border-slate-100 bg-slate-50/80 p-2 dark:border-slate-800 dark:bg-slate-950/40">
                  <span className="text-[9px] font-medium text-slate-400">Floor SOH</span>
                  <div className="text-xs font-bold text-slate-800 dark:text-slate-200">
                    {data.floor_soh_qty.toLocaleString('en-IN')}
                  </div>
                  <span className="text-[9px] text-slate-400">{formatCompactInr(data.floor_amt_cost)}</span>
                </div>
                <div className="rounded-lg border border-slate-100 bg-slate-50/80 p-2 dark:border-slate-800 dark:bg-slate-950/40">
                  <span className="text-[9px] font-medium text-slate-400">Dispatch ST%</span>
                  <div className="text-xs font-bold text-emerald-600 dark:text-emerald-400">
                    {data.offline_st_dispatch_pct.toFixed(1)}%
                  </div>
                  <span className="text-[9px] text-slate-400">of {data.dispatch_qty.toLocaleString('en-IN')}</span>
                </div>
              </div>

              {/* Weekly Velocity & Breakeven Footer */}
              <div className="flex items-center justify-between border-t border-slate-100 pt-2 text-[11px] text-slate-500 dark:border-slate-800">
                <div className="flex items-center gap-1 font-mono">
                  <span>Wk Velocity:</span>
                  <span className="font-semibold text-slate-700 dark:text-slate-300">
                    {data.wk_minus_1_qty} / {data.wk_minus_2_qty} / {data.wk_minus_3_qty}
                  </span>
                </div>
                <div className="flex items-center gap-1">
                  <span>Breakeven:</span>
                  <span
                    className={`font-semibold ${
                      data.breakeven_val >= 0
                        ? 'text-emerald-600 dark:text-emerald-400'
                        : 'text-rose-600 dark:text-rose-400'
                    }`}
                  >
                    {data.breakeven_val >= 0
                      ? `+${formatCompactInr(data.breakeven_val)}`
                      : `-${formatCompactInr(Math.abs(data.breakeven_val))}`}
                  </span>
                </div>
              </div>
            </div>
          ) : (
            <div className="flex h-32 flex-col items-center justify-center text-xs text-slate-400">
              <span>No details found for SKU {cleanSku}</span>
            </div>
          )}
        </div>
      )}
    </>
  );
}

export default SkuHoverCard;

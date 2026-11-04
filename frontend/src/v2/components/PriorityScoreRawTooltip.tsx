import React, { useState, useRef, useId, useEffect, useCallback } from 'react';
import { createPortal } from 'react-dom';

export type PriorityScoreRawTooltipProps = {
  total_sold_qty?: number | null;
  priority_expl_timing_bonus?: number | null;
  priority_score_raw?: number | null;
  children: React.ReactNode;
};

function fmtField(n: unknown, decimals: number): string {
  if (n === null || n === undefined || n === '') return '—';
  const x = Number(n);
  if (!Number.isFinite(x)) return '—';
  return x.toFixed(decimals);
}

/** `total_sold_qty` — use integers without trailing .0000, else 4 d.p. */
function fmtSold(n: unknown): string {
  if (n === null || n === undefined || n === '') return '—';
  const x = Number(n);
  if (!Number.isFinite(x)) return '—';
  if (Number.isInteger(x)) return String(x);
  return x.toFixed(4);
}

export function PriorityScoreRawTooltip({
  total_sold_qty,
  priority_expl_timing_bonus,
  priority_score_raw,
  children,
}: PriorityScoreRawTooltipProps) {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLSpanElement>(null);
  const tipId = useId();
  const [pos, setPos] = useState({ top: 0, left: 0 });

  const position = useCallback(() => {
    const el = triggerRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    setPos({ top: r.top - 8, left: r.left + r.width / 2 });
  }, []);

  const show = useCallback(() => {
    position();
    setOpen(true);
  }, [position]);

  const hide = useCallback(() => setOpen(false), []);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') hide();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, hide]);

  useEffect(() => {
    if (!open) return;
    const onScroll = () => position();
    window.addEventListener('scroll', onScroll, true);
    window.addEventListener('resize', onScroll);
    return () => {
      window.removeEventListener('scroll', onScroll, true);
      window.removeEventListener('resize', onScroll);
    };
  }, [open, position]);

  const a = fmtSold(total_sold_qty);
  const b = fmtField(priority_expl_timing_bonus, 4);
  const c = fmtField(priority_score_raw, 4);

  const body = [
    'This is the number the model uses before the 0–10 “display” priority is shown in the app.',
    '',
    'Raw priority ≈ unit sales in the model window (total_sold_qty) + a small timing boost (priority_expl_timing_bonus).',
    '',
    `This row: ${a} + ${b} = ${c}`,
  ].join('\n');

  return (
    <>
      <span
        ref={triggerRef}
        tabIndex={0}
        aria-label="Raw priority score: hover or focus for how unit sales and timing bonus combine"
        className="inline-block min-w-[3ch] cursor-default rounded outline-none focus-visible:ring-2 focus-visible:ring-violet-500/70 focus-visible:ring-offset-1 dark:focus-visible:ring-offset-slate-900"
        onMouseEnter={show}
        onMouseLeave={hide}
        onFocus={show}
        onBlur={hide}
        aria-describedby={open ? tipId : undefined}
      >
        {children}
      </span>
      {open &&
        createPortal(
          <div
            id={tipId}
            role="tooltip"
            className="pointer-events-none fixed z-[100] max-w-sm -translate-x-1/2 -translate-y-full px-1"
            style={{ top: pos.top, left: pos.left }}
          >
            <div className="pointer-events-auto rounded-lg border border-slate-200 bg-white p-3 text-left text-xs leading-relaxed text-slate-700 shadow-xl dark:border-slate-600 dark:bg-slate-800 dark:text-slate-200">
              <p className="mb-2 font-semibold text-slate-900 dark:text-slate-100">Raw priority score</p>
              <div className="whitespace-pre-wrap font-sans">{body}</div>
            </div>
          </div>,
          document.body
        )}
    </>
  );
}

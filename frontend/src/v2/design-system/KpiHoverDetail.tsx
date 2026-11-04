import React, { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

type Props = {
  title: string;
  detail: string;
  children: React.ReactNode;
  className?: string;
};

/** Hover detail via portal — never clipped by card/grid overflow */
export function KpiHoverDetail({ title, detail, children, className = '' }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [coords, setCoords] = useState({ top: 0, left: 0 });

  const place = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const tipW = 240;
    const tipH = 88;
    let left = r.left + r.width / 2;
    let top = r.bottom + 8;
    // Keep inside viewport
    left = Math.min(Math.max(left, tipW / 2 + 8), window.innerWidth - tipW / 2 - 8);
    if (top + tipH > window.innerHeight - 8) {
      top = r.top - tipH - 8;
    }
    setCoords({ top, left });
  }, []);

  useEffect(() => {
    if (!open) return;
    const close = () => setOpen(false);
    window.addEventListener('scroll', close, true);
    window.addEventListener('resize', close);
    return () => {
      window.removeEventListener('scroll', close, true);
      window.removeEventListener('resize', close);
    };
  }, [open]);

  return (
    <div
      ref={ref}
      className={className}
      onMouseEnter={() => {
        place();
        setOpen(true);
      }}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => {
        place();
        setOpen(true);
      }}
      onBlur={() => setOpen(false)}
    >
      {children}
      {open &&
        detail &&
        createPortal(
          <div
            role="tooltip"
            style={{ top: coords.top, left: coords.left }}
            className="pointer-events-none fixed z-[9999] w-max max-w-[240px] -translate-x-1/2 rounded-lg border border-slate-700 bg-slate-900 px-2.5 py-1.5 text-left text-[11px] leading-snug text-white shadow-xl"
          >
            <p className="font-semibold text-white">{title}</p>
            <p className="mt-0.5 font-normal text-slate-300">{detail}</p>
          </div>,
          document.body
        )}
    </div>
  );
}

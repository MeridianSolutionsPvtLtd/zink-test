import React from 'react';
import './ReplenishmentLineLoader.css';

type ReplenishmentLineLoaderProps = {
  /** Pixel box for the SVG (square). */
  size?: number;
  label?: string;
  /** Optional: use red accent instead of green (`#ef4444`). */
  variant?: 'emerald' | 'rose';
};

/**
 * Uiverse-style dashed polyline loop (after milley69). Default: emerald; `rose` = red.
 */
export function ReplenishmentLineLoader({
  size = 80,
  label,
  variant = 'emerald',
}: ReplenishmentLineLoaderProps) {
  const style =
    variant === 'rose'
      ? ({
          ['--replen-line-stroke' as string]: '#ef4444',
          ['--replen-line-stroke-muted' as string]: 'rgb(239 68 68 / 0.22)',
        } as React.CSSProperties)
      : undefined;

  return (
    <div
      className="replen-line-root flex flex-col items-center justify-center gap-3"
      role="status"
      aria-live="polite"
      aria-busy="true"
    >
      <div className="replen-line-loading" style={{ width: size, height: size, ...style }}>
        <svg viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg" aria-hidden>
          <polyline
            id="back"
            pathLength="192"
            points="22,22 78,22 78,78 22,78 22,22"
          />
          <polyline
            id="front"
            pathLength="192"
            points="22,22 78,22 78,78 22,78 22,22"
          />
        </svg>
      </div>
      {label ? (
        <p className="max-w-sm text-center text-[11px] font-bold uppercase leading-relaxed tracking-wide text-emerald-900/85 dark:text-emerald-100/90">
          {label}
        </p>
      ) : null}
    </div>
  );
}

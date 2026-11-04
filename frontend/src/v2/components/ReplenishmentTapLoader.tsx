import React from 'react';
import './ReplenishmentTapLoader.css';

export type ReplenishmentTapLoaderSize = 'sm' | 'md' | 'lg';

type ReplenishmentTapLoaderProps = {
  size?: ReplenishmentTapLoaderSize;
  label?: string;
  align?: 'center' | 'start';
  /** Merged onto root (e.g. `gap-5` between hand and label). */
  className?: string;
  /** Merged onto the label line (e.g. `max-w-[400px]`). */
  labelClassName?: string;
};

const SCALE: Record<ReplenishmentTapLoaderSize, number> = {
  sm: 0.72,
  md: 1,
  lg: 1.2,
};

const WRAP: Record<ReplenishmentTapLoaderSize, string> = {
  sm: 'min-h-[120px] gap-2 py-4',
  md: 'min-h-[180px] gap-3 py-6',
  lg: 'min-h-[240px] gap-4 py-8',
};

/**
 * Replenishments-only loading animation (tapping hand). Stage background is transparent.
 */
export function ReplenishmentTapLoader({
  size = 'md',
  label,
  align = 'center',
  className = '',
  labelClassName = '',
}: ReplenishmentTapLoaderProps) {
  const cross = align === 'start' ? 'items-start' : 'items-center';
  const textAlign = align === 'start' ? 'text-left' : 'text-center';
  const justify = align === 'start' ? 'justify-start' : 'justify-center';

  return (
    <div
      className={`replen-tap-root flex flex-col ${cross} ${justify} px-4 ${WRAP[size]} ${className}`.trim()}
      role="status"
      aria-live="polite"
      aria-busy="true"
    >
      <div className="replen-tap-visual" style={{ transform: `scale(${SCALE[size]})` }}>
        <div className="replen-tap-hand">
          <span className="replen-tap-palm" aria-hidden />
          <span className="replen-tap-thumb" aria-hidden />
          <span className="replen-tap-finger replen-tap-finger--1" aria-hidden />
          <span className="replen-tap-finger replen-tap-finger--2" aria-hidden />
          <span className="replen-tap-finger replen-tap-finger--3" aria-hidden />
          <span className="replen-tap-finger replen-tap-finger--4" aria-hidden />
        </div>
      </div>
      {label ? (
        <p
          className={`max-w-sm text-[11px] font-bold uppercase leading-relaxed tracking-wide text-emerald-900/85 dark:text-emerald-100/90 ${textAlign} ${labelClassName}`.trim()}
        >
          {label}
        </p>
      ) : null}
    </div>
  );
}

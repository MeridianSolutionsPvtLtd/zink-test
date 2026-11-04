import React, { useEffect, useRef, useState } from 'react';

type Props = {
  children: React.ReactNode;
  /** px margin before viewport — start loading slightly early */
  rootMargin?: string;
  minHeight?: number;
  placeholder?: React.ReactNode;
};

/** Defer mounting children until near viewport — avoids API storms on page load. */
export function LazyMount({
  children,
  rootMargin = '240px',
  minHeight = 96,
  placeholder,
}: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el || visible) return;

    if (typeof IntersectionObserver === 'undefined') {
      setVisible(true);
      return;
    }

    const obs = new IntersectionObserver(
      ([entry]) => {
        if (entry?.isIntersecting) {
          setVisible(true);
          obs.disconnect();
        }
      },
      { rootMargin },
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, [rootMargin, visible]);

  return (
    <div ref={ref} style={{ minHeight: visible ? undefined : minHeight }}>
      {visible
        ? children
        : placeholder ?? (
            <div
              className="animate-pulse rounded-2xl border border-slate-200/60 bg-slate-50 dark:border-slate-800 dark:bg-slate-900/40"
              style={{ minHeight }}
            />
          )}
    </div>
  );
}

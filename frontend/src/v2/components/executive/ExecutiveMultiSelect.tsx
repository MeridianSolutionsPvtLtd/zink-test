import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Check, ChevronDown, Loader2, LucideIcon, Search, X } from 'lucide-react';

type Props = {
  label: string;
  icon: LucideIcon;
  options: string[];
  value: string[];
  onChange: (value: string[]) => void;
  placeholder?: string;
  /** Spinner until options ready (e.g. Item Master Department / Section) */
  loading?: boolean;
  minWidth?: number;
  /** Smaller trigger for dense filter bars */
  dense?: boolean;
  /** Timeline weeks/months may keep several values. Dimension filters replace the previous pick. */
  multiple?: boolean;
};

/** Multi-select dropdown — portal + fixed position so it never clips inside filter bars. */
export function ExecutiveMultiSelect({
  label,
  icon: Icon,
  options,
  value,
  onChange,
  placeholder = 'All',
  loading = false,
  minWidth = 360,
  dense = false,
  multiple = false,
}: Props) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');
  const [pos, setPos] = useState({ top: 0, left: 0, width: 360 });
  const btnRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  const updatePos = () => {
    const el = btnRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const targetWidth = Math.max(rect.width, minWidth || 360);
    const width = Math.min(targetWidth, window.innerWidth - 24);
    let left = rect.left;
    if (left + width > window.innerWidth - 12) {
      left = Math.max(12, window.innerWidth - width - 12);
    }
    const spaceBelow = window.innerHeight - rect.bottom;
    const openUp = spaceBelow < 300 && rect.top > spaceBelow;
    setPos({
      top: openUp ? rect.top - 4 : rect.bottom + 4,
      left,
      width,
    });
  };

  useLayoutEffect(() => {
    if (!open) return;
    updatePos();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onScroll = () => updatePos();
    const onResize = () => updatePos();
    window.addEventListener('scroll', onScroll, true);
    window.addEventListener('resize', onResize);
    return () => {
      window.removeEventListener('scroll', onScroll, true);
      window.removeEventListener('resize', onResize);
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      const t = e.target as Node;
      if (btnRef.current?.contains(t) || panelRef.current?.contains(t)) return;
      setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  useEffect(() => {
    if (loading && open) setOpen(false);
  }, [loading, open]);

  const safeOptions = (Array.isArray(options) ? options : []).flatMap((o) => {
    if (typeof o !== 'string') return [];
    const text = o.trim();
    return text ? [text] : [];
  });
  const query = search.trim().toLowerCase();
  // Filter only while the menu is open. A full SKU/store list otherwise blocks the page.
  const filtered = open
    ? query
      ? safeOptions.filter((o) => o.toLowerCase().includes(query))
      : safeOptions
    : [];

  const toggle = (opt: string) => {
    if (multiple) {
      onChange(value.includes(opt) ? value.filter((v) => v !== opt) : [...value, opt]);
      return;
    }
    // One value per filter: a new pick replaces the previous chip so both are not selected.
    onChange(value.length === 1 && value[0] === opt ? [] : [opt]);
  };

  const display = loading
    ? 'Loading…'
    : value.length === 0
      ? placeholder
      : value.length === 1
        ? value[0]
        : `${value.length} selected`;

  const panelStyle: React.CSSProperties = {
    position: 'fixed',
    left: pos.left,
    width: pos.width,
    zIndex: 9999,
    ...(pos.top < (btnRef.current?.getBoundingClientRect().top ?? 0)
      ? { bottom: window.innerHeight - pos.top, top: 'auto' }
      : { top: pos.top }),
  };

  return (
    <div className="relative min-w-0">
      {label && dense ? (
        <span className="mb-0.5 block truncate text-[9px] font-semibold uppercase tracking-wider text-slate-400">
          {label}
        </span>
      ) : null}
      <button
        ref={btnRef}
        type="button"
        disabled={loading}
        aria-busy={loading}
        onClick={() => {
          if (!loading) setOpen((o) => !o);
        }}
        className={`group flex w-full min-w-0 items-center text-left font-medium text-slate-700 shadow-sm transition-colors hover:border-blue-300 disabled:cursor-wait disabled:opacity-80 dark:border-slate-700/80 dark:bg-slate-900/60 dark:text-slate-200 dark:hover:border-blue-500/40 ${
          dense
            ? 'gap-1.5 rounded-lg border border-slate-200/80 bg-white/80 px-2 py-1.5 text-[11px] hover:shadow-sm'
            : 'gap-2 rounded-xl border border-slate-200/80 bg-white/80 px-3 py-2 text-xs hover:shadow-md'
        }`}
      >
        {loading ? (
          <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-blue-600" aria-hidden />
        ) : (
          <Icon className="h-3.5 w-3.5 shrink-0 text-blue-600" />
        )}
        <span className="flex-1 truncate">{display}</span>
        <ChevronDown className={`h-3.5 w-3.5 text-slate-400 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      {label && !dense ? (
        <span className="mt-1 block truncate text-[10px] font-medium uppercase tracking-wider text-slate-400">
          {label}
        </span>
      ) : null}

      {open &&
        !loading &&
        createPortal(
          <div
            ref={panelRef}
            style={panelStyle}
            className="overflow-hidden rounded-2xl border border-slate-200/90 bg-white shadow-2xl shadow-slate-900/15 dark:border-slate-700 dark:bg-slate-900"
          >
            <div className="border-b border-slate-100 p-2.5 dark:border-slate-800">
              <div className="relative">
                <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
                <input
                  autoFocus
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder={`Search ${label.toLowerCase()}…`}
                  className="w-full rounded-lg border border-slate-200 bg-slate-50 py-1.5 pl-8 pr-3 text-xs focus:border-blue-400 focus:outline-none focus:ring-2 focus:ring-blue-500/20 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-200"
                />
              </div>
            </div>
            {value.length > 0 && (
              <div className="flex flex-nowrap gap-1 overflow-x-auto border-b border-slate-100 p-2 dark:border-slate-800">
                {value.map((v) => (
                  <span
                    key={v}
                    className="inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-md bg-blue-50 px-2 py-0.5 text-[10px] font-semibold text-blue-700 dark:bg-blue-500/15 dark:text-blue-300"
                  >
                    {v}
                    <button type="button" onClick={() => toggle(v)}>
                      <X className="h-3 w-3" />
                    </button>
                  </span>
                ))}
                <button
                  type="button"
                  onClick={() => onChange([])}
                  className="text-[10px] font-semibold text-slate-400 hover:text-blue-600"
                >
                  Clear
                </button>
              </div>
            )}
            <div className="max-h-60 overflow-y-auto p-1.5">
              {multiple && filtered.length > 0 && (
                <div className="mb-1.5 flex items-center justify-between border-b border-slate-100 px-2 pb-1.5 pt-0.5 text-[11px] font-semibold dark:border-slate-800">
                  <button
                    type="button"
                    onClick={() => onChange([...new Set([...value, ...filtered])])}
                    className="text-blue-600 hover:underline dark:text-blue-400"
                  >
                    Select all {filtered.length > 1 ? `(${filtered.length})` : ''}
                  </button>
                  <button
                    type="button"
                    onClick={() => onChange(value.filter((v) => !filtered.includes(v)))}
                    className="text-slate-400 hover:text-slate-600 hover:underline dark:hover:text-slate-200"
                  >
                    Unselect all
                  </button>
                </div>
              )}
              {filtered.length === 0 ? (
                <p className="px-2.5 py-3 text-xs text-slate-400">No options</p>
              ) : (
                filtered.map((opt) => {
                  const selected = value.includes(opt);
                  return (
                    <button
                      key={opt}
                      type="button"
                      onClick={() => toggle(opt)}
                      className={`flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-xs transition-colors ${
                        selected
                          ? 'bg-blue-50 text-blue-700 dark:bg-blue-500/15 dark:text-blue-300 font-medium'
                          : 'text-slate-700 hover:bg-slate-50 dark:text-slate-300 dark:hover:bg-slate-800'
                      }`}
                    >
                      <span
                        className={`flex h-4 w-4 shrink-0 items-center justify-center rounded border ${
                          selected
                            ? 'border-blue-500 bg-blue-500 text-white'
                            : 'border-slate-300 dark:border-slate-600'
                        }`}
                      >
                        {selected && <Check className="h-2.5 w-2.5" />}
                      </span>
                      <span className="flex-1 break-words leading-relaxed">{opt}</span>
                    </button>
                  );
                })
              )}
            </div>
          </div>,
          document.body
        )}
    </div>
  );
}

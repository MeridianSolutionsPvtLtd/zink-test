import React, { useEffect, useLayoutEffect, useState, useRef } from 'react';
import { createPortal } from 'react-dom';
import { LucideIcon, Search, Check, ChevronDown, Loader2 } from 'lucide-react';
import { API_URL } from '../../config';

interface FilterOption {
    id: string;
    label: string;
}

interface FilterDropdownProps {
    label: string;
    icon: LucideIcon;
    value: string;
    onChange: (value: string) => void;
    endpoint: string;
    defaultValue: string;
    /** Query params from other filters to get relevant options */
    sisterQuery?: string;
}

/** Accepts a raw array or common API wrappers so filter lists still populate. */
function coerceFilterOptions(raw: unknown): (string | FilterOption)[] | null {
    if (Array.isArray(raw)) return raw as (string | FilterOption)[];
    if (raw && typeof raw === 'object' && Array.isArray((raw as { data?: unknown }).data)) {
        return (raw as { data: (string | FilterOption)[] }).data;
    }
    return null;
}

export const FilterDropdown = ({ 
    label, 
    icon: Icon, 
    value, 
    onChange, 
    endpoint, 
    defaultValue, 
    sisterQuery 
}: FilterDropdownProps) => {
    const [options, setOptions] = useState<(string | FilterOption)[]>([]);
    const [isOpen, setIsOpen] = useState(false);
    const [searchTerm, setSearchTerm] = useState("");
    const [isLoading, setIsLoading] = useState(false);
    const [pos, setPos] = useState({ top: 0, left: 0, width: 280 });
    const dropdownRef = useRef<HTMLDivElement>(null);
    const btnRef = useRef<HTMLButtonElement>(null);
    const panelRef = useRef<HTMLDivElement>(null);
    const onChangeRef = useRef(onChange);
    const previousSisterQueryRef = useRef<string>('');
    const valueRef = useRef(value);
    
    onChangeRef.current = onChange;
    valueRef.current = value;

    const updatePos = () => {
        const el = btnRef.current;
        if (!el) return;
        const rect = el.getBoundingClientRect();
        const width = Math.max(rect.width, 280);
        let left = rect.left;
        if (left + width > window.innerWidth - 8) left = Math.max(8, window.innerWidth - width - 8);
        const spaceBelow = window.innerHeight - rect.bottom;
        const openUp = spaceBelow < 320 && rect.top > spaceBelow;
        setPos({
            top: openUp ? rect.top - 4 : rect.bottom + 4,
            left,
            width,
        });
    };

    useLayoutEffect(() => {
        if (!isOpen) return;
        updatePos();
    }, [isOpen]);

    useEffect(() => {
        if (!isOpen) return;
        const onScroll = () => updatePos();
        const onResize = () => updatePos();
        window.addEventListener('scroll', onScroll, true);
        window.addEventListener('resize', onResize);
        return () => {
            window.removeEventListener('scroll', onScroll, true);
            window.removeEventListener('resize', onResize);
        };
    }, [isOpen]);

    // Fetch options whenever endpoint or sisterQuery changes (NOT on every value change)
    useEffect(() => {
        const q = sisterQuery && sisterQuery.length > 0 ? `?${sisterQuery}` : '';
        const fullUrl = `${API_URL}${endpoint}${q}`;
        let cancelled = false;

        setIsLoading(true);

        fetch(fullUrl)
            .then(res => res.json())
            .then((data) => {
                if (cancelled) return;
                const list = coerceFilterOptions(data);
                if (list && list.length > 0) {
                    setOptions(list);

                    // Only reset value when sister filters change and current value is invalid
                    if (sisterQuery !== previousSisterQueryRef.current) {
                        const ids = new Set(
                            list.map((opt) => (typeof opt === 'string' ? opt : opt.id))
                        );
                        const current = valueRef.current;
                        if (!ids.has(current) && current !== defaultValue) {
                            onChangeRef.current(defaultValue);
                        }
                        previousSisterQueryRef.current = sisterQuery || '';
                    }
                } else {
                    setOptions([]);
                    if (valueRef.current !== defaultValue) {
                        onChangeRef.current(defaultValue);
                    }
                }
            })
            .catch(err => {
                if (cancelled) return;
                console.error(`Failed to fetch ${label}:`, err);
                setOptions([]);
            })
            .finally(() => {
                if (!cancelled) setIsLoading(false);
            });

        return () => {
            cancelled = true;
        };
    }, [endpoint, sisterQuery, label, defaultValue]);

    const getLabel = (opt: string | FilterOption) => typeof opt === 'string' ? opt : opt.label;
    const getId = (opt: string | FilterOption) => typeof opt === 'string' ? opt : opt.id;

    // Close on click outside (button + portal panel)
    useEffect(() => {
        const handleClickOutside = (event: MouseEvent) => {
            const t = event.target as Node;
            if (btnRef.current?.contains(t) || panelRef.current?.contains(t) || dropdownRef.current?.contains(t)) {
                return;
            }
            setIsOpen(false);
            setSearchTerm("");
        };
        document.addEventListener('mousedown', handleClickOutside);
        return () => document.removeEventListener('mousedown', handleClickOutside);
    }, []);

    const filteredOptions = options.filter(opt =>
        getLabel(opt).toLowerCase().includes(searchTerm.toLowerCase())
    );

    // Find active option from current options list
    const activeOption = options.find(opt => getId(opt) === value);
    const activeLabel = activeOption ? getLabel(activeOption) : (value || defaultValue);

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
        <div className="relative" ref={dropdownRef}>
            {/* Toggle Button */}
            <button
                ref={btnRef}
                type="button"
                onClick={() => setIsOpen(!isOpen)}
                disabled={isLoading && options.length === 0}
                className={`relative flex items-center gap-2 pl-10 pr-4 py-1.5 bg-slate-50 dark:bg-slate-800/50 border border-slate-200 dark:border-slate-700 rounded-lg text-sm font-semibold text-slate-700 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-emerald-500/20 transition-all min-w-[140px] max-w-[220px] hover:border-slate-300 dark:hover:border-slate-600 ${
                    isLoading && options.length === 0 ? 'opacity-60 cursor-not-allowed' : 'cursor-pointer'
                }`}
            >
                {isLoading && options.length === 0 ? (
                    <Loader2 className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 animate-spin text-emerald-500" aria-hidden />
                ) : (
                    <Icon className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-emerald-500" />
                )}
                <span className="truncate flex-1 text-left">
                    {isLoading && options.length === 0 ? 'Loading…' : activeLabel}
                </span>
                <ChevronDown className={`h-4 w-4 text-slate-400 transition-transform ${isOpen ? 'rotate-180' : ''}`} />
            </button>

            {/* Portal menu — never clipped by overflow parents */}
            {isOpen &&
                createPortal(
                    <div
                        ref={panelRef}
                        style={panelStyle}
                        className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-2xl dark:border-slate-800 dark:bg-slate-900"
                    >
                        <div className="border-b border-slate-100 bg-slate-50/50 p-3 dark:border-slate-800 dark:bg-slate-800/30">
                            <div className="relative">
                                <Search className="absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
                                <input
                                    autoFocus
                                    type="text"
                                    placeholder={`Search ${label}...`}
                                    value={searchTerm}
                                    onChange={(e) => setSearchTerm(e.target.value)}
                                    className="w-full rounded-lg border border-slate-200 bg-white py-1.5 pl-9 pr-4 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-emerald-500/20 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-200"
                                />
                            </div>
                        </div>

                        <div className="max-h-[300px] overflow-y-auto py-1">
                            {isLoading && options.length === 0 ? (
                                <div className="px-4 py-8 text-center text-sm text-slate-400">Loading…</div>
                            ) : filteredOptions.length > 0 ? (
                                filteredOptions.map((opt) => {
                                    const optId = getId(opt);
                                    const optLabel = getLabel(opt);
                                    const isActive = value === optId;
                                    return (
                                        <button
                                            key={optId}
                                            type="button"
                                            onClick={() => {
                                                onChange(optId);
                                                setIsOpen(false);
                                                setSearchTerm("");
                                            }}
                                            className={`group flex w-full items-center justify-between px-4 py-2.5 text-sm transition-colors hover:bg-slate-50 dark:hover:bg-slate-800/50 ${
                                                isActive
                                                    ? 'bg-emerald-50/30 font-bold text-emerald-600 dark:bg-emerald-500/5 dark:text-emerald-400'
                                                    : 'text-slate-600 dark:text-slate-300'
                                            }`}
                                        >
                                            <span className="truncate pr-2">{optLabel}</span>
                                            {isActive && <Check className="h-4 w-4 flex-shrink-0" />}
                                        </button>
                                    );
                                })
                            ) : (
                                <div className="px-4 py-8 text-center text-sm italic text-slate-400">
                                    {searchTerm ? `No results found for "${searchTerm}"` : 'No options available'}
                                </div>
                            )}
                        </div>
                    </div>,
                    document.body
                )}
        </div>
    );
};

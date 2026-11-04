import React, { useEffect, useState, useRef } from 'react';
import { LucideIcon, Search, Check, ChevronDown, X, Loader2 } from 'lucide-react';
import { API_URL } from '../../config';

interface MultiFilterDropdownProps {
    label: string;
    icon: LucideIcon;
    value: string[];
    onChange: (value: string[]) => void;
    endpoint?: string;
    defaultValue: string;
    staticOptions?: string[];
}

export const MultiFilterDropdown = ({ label, icon: Icon, value, onChange, endpoint, defaultValue, staticOptions }: MultiFilterDropdownProps) => {
    const [options, setOptions] = useState<string[]>(staticOptions || [defaultValue]);
    const [isOpen, setIsOpen] = useState(false);
    const [searchTerm, setSearchTerm] = useState("");
    const [isLoading, setIsLoading] = useState(Boolean(endpoint) && !staticOptions?.length);
    const dropdownRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        if (!endpoint) return;
        let cancelled = false;
        setIsLoading(true);
        fetch(`${API_URL}${endpoint}`)
            .then(res => res.json())
            .then(data => {
                if (cancelled) return;
                if (Array.isArray(data)) {
                    // Filter out 'All Seasons' or defaultValue if we handle multiple
                    setOptions(data.filter(opt => opt !== defaultValue));
                }
            })
            .catch(err => {
                if (!cancelled) console.error(`Failed to fetch ${label}:`, err);
            })
            .finally(() => {
                if (!cancelled) setIsLoading(false);
            });
        return () => {
            cancelled = true;
        };
    }, [endpoint, label, defaultValue]);

    // Close on click outside
    useEffect(() => {
        const handleClickOutside = (event: MouseEvent) => {
            if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
                setIsOpen(false);
            }
        };
        document.addEventListener('mousedown', handleClickOutside);
        return () => document.removeEventListener('mousedown', handleClickOutside);
    }, []);

    const toggleOption = (opt: string) => {
        if (value.includes(opt)) {
            onChange(value.filter(v => v !== opt));
        } else {
            onChange([...value, opt]);
        }
    };

    const filteredOptions = options.filter(opt =>
        opt.toLowerCase().includes(searchTerm.toLowerCase())
    );

    const displayValue = isLoading && options.length === 0
        ? 'Loading…'
        : value.length === 0
            ? defaultValue
            : value.length === 1
                ? value[0]
                : `${value.length} Selected`;

    return (
        <div className="relative" ref={dropdownRef}>
            {/* Toggle Button */}
            <button
                type="button"
                onClick={() => {
                    if (!(isLoading && options.length === 0)) setIsOpen(!isOpen);
                }}
                disabled={isLoading && options.length === 0}
                className={`flex items-center gap-2 pl-10 pr-4 py-1.5 bg-slate-50 dark:bg-slate-800/50 border border-slate-200 dark:border-slate-700 rounded-lg text-sm font-semibold text-slate-700 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-emerald-500/20 transition-all min-w-[140px] max-w-[220px] hover:border-slate-300 dark:hover:border-slate-600 ${
                    isLoading && options.length === 0 ? 'opacity-60 cursor-wait' : 'cursor-pointer'
                }`}
            >
                {isLoading && options.length === 0 ? (
                    <Loader2 className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 animate-spin text-emerald-500" aria-hidden />
                ) : (
                    <Icon className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-emerald-500" />
                )}
                <span className="truncate flex-1 text-left">{displayValue}</span>
                <ChevronDown className={`h-4 w-4 text-slate-400 transition-transform ${isOpen ? 'rotate-180' : ''}`} />
            </button>

            {/* Dropdown Menu */}
            {isOpen && (
                <div className="absolute top-full mt-2 left-0 w-[280px] bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl shadow-2xl z-50 overflow-hidden animate-in fade-in slide-in-from-top-1 duration-200">
                    {/* Search Section */}
                    <div className="p-3 border-b border-slate-100 dark:border-slate-800 bg-slate-50/50 dark:bg-slate-800/30">
                        <div className="relative">
                            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
                            <input
                                autoFocus
                                type="text"
                                placeholder={`Search ${label}...`}
                                value={searchTerm}
                                onChange={(e) => setSearchTerm(e.target.value)}
                                className="w-full pl-9 pr-4 py-1.5 text-sm bg-white dark:bg-slate-950 border border-slate-200 dark:border-slate-700 rounded-lg focus:outline-none focus:ring-2 focus:ring-emerald-500/20 text-slate-700 dark:text-slate-200"
                            />
                        </div>
                    </div>

                    {/* Selected Badges */}
                    {value.length > 0 && (
                        <div className="p-2 border-b border-slate-100 dark:border-slate-800 flex flex-wrap gap-1 max-h-[100px] overflow-y-auto">
                            {value.map(v => (
                                <span key={v} className="inline-flex items-center gap-1 px-2 py-0.5 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 text-[10px] font-bold rounded-md">
                                    {v}
                                    <button onClick={() => toggleOption(v)} className="hover:text-emerald-800">
                                        <X className="w-3 h-3" />
                                    </button>
                                </span>
                            ))}
                            <button
                                onClick={() => onChange([])}
                                className="text-[10px] text-slate-400 hover:text-slate-600 underline ml-1"
                            >
                                Clear All
                            </button>
                        </div>
                    )}

                    {/* Options List */}
                    <div className="max-h-[300px] overflow-y-auto py-1 scrollbar-thin scrollbar-thumb-slate-200 dark:scrollbar-thumb-slate-800">
                        {isLoading && filteredOptions.length === 0 ? (
                            <div className="flex items-center justify-center gap-2 px-4 py-8 text-sm text-slate-400">
                                <Loader2 className="h-4 w-4 animate-spin text-emerald-500" />
                                Loading…
                            </div>
                        ) : filteredOptions.length > 0 ? (
                            filteredOptions.map((opt) => {
                                const isSelected = value.includes(opt);
                                return (
                                    <button
                                        key={opt}
                                        type="button"
                                        onClick={() => toggleOption(opt)}
                                        className={`group flex w-full items-center justify-between px-4 py-2.5 text-sm transition-colors hover:bg-slate-50 dark:hover:bg-slate-800/50 ${
                                            isSelected
                                                ? 'bg-emerald-50/30 font-bold text-emerald-600 dark:bg-emerald-500/5 dark:text-emerald-400'
                                                : 'text-slate-600 dark:text-slate-300'
                                        }`}
                                    >
                                        <span className="truncate pr-2">{opt}</span>
                                        {isSelected && <Check className="h-4 w-4 flex-shrink-0" />}
                                    </button>
                                );
                            })
                        ) : (
                            <div className="px-4 py-8 text-center text-sm italic text-slate-400">
                                {searchTerm ? `No results found for "${searchTerm}"` : 'No options available'}
                            </div>
                        )}
                    </div>
                </div>
            )}
        </div>
    );
};

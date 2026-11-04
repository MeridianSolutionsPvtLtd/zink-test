import React, { createContext, useContext, useState, useEffect, useRef, ReactNode } from 'react';

interface FilterContextType {
    season: string;
    setSeason: (season: string) => void;
    sku: string;
    setSku: (sku: string) => void;
    store: string;
    setStore: (store: string) => void;
    monthYear: string;
    setMonthYear: (monthYear: string) => void;
    category: string;
    setCategory: (category: string) => void;
    baselineYears: string[];
    setBaselineYears: (years: string[]) => void;
    // AI Specific Filters
    aiSite: string;
    setAiSite: (s: string) => void;
    aiSKU: string;
    setAiSKU: (s: string) => void;
    aiDept: string;
    setAiDept: (s: string) => void;
    aiMonth: string;
    setAiMonth: (s: string) => void;
    // Replenishments Specific Filters
    repSite: string;
    setRepSite: (s: string) => void;
    repSKU: string;
    setRepSKU: (s: string) => void;
    repMonth: string;
    setRepMonth: (s: string) => void;
    repWeek: string;
    setRepWeek: (s: string) => void;
    repSiteId: string;
    setRepSiteId: (s: string) => void;
    /** Replenishments table tab: drives header /filters ?refill_source= for site→site slice. */
    repRecomTableMode: 'wh_store' | 'site_transfer';
    setRepRecomTableMode: (m: 'wh_store' | 'site_transfer') => void;
}

const FilterContext = createContext<FilterContextType | undefined>(undefined);

export const FilterProvider = ({ children }: { children: ReactNode }) => {
    const [season, setSeason] = useState('All Seasons');
    const [sku, setSku] = useState('All SKUs');
    const [store, setStore] = useState('All Stores');
    const [monthYear, setMonthYear] = useState('Jan-2026');
    const [category, setCategory] = useState('All Categories');
    const [baselineYears, setBaselineYears] = useState<string[]>(['2025']);

    // AI Specific Filters
    const [aiSite, setAiSite] = useState('All Sites');
    const [aiSKU, setAiSKU] = useState('All SKUs');
    const [aiDept, setAiDept] = useState('All Departments');
    const [aiMonth, setAiMonth] = useState('All Months');

    // Replenishments Specific Filters
    const [repSite, setRepSite] = useState('All Sites');
    const [repSKU, setRepSKU] = useState('All SKUs');
    const [repMonth, setRepMonth] = useState('All Months');
    const [repWeek, setRepWeek] = useState('All Weeks');
    const [repSiteId, setRepSiteId] = useState('All Site IDs');
    const [repRecomTableMode, setRepRecomTableMode] = useState<'wh_store' | 'site_transfer'>('wh_store');

    /** Replenishments: narrower filters reset when a parent scope changes so lists stay consistent (cascading). */
    const repSiteSkip = useRef(true);
    const repSiteIdSkip = useRef(true);
    const repSkuSkip = useRef(true);
    const repMonthSkip = useRef(true);

    useEffect(() => {
        if (repSiteSkip.current) {
            repSiteSkip.current = false;
            return;
        }
        setRepSiteId('All Site IDs');
        setRepSKU('All SKUs');
        setRepMonth('All Months');
        setRepWeek('All Weeks');
    }, [repSite]);

    useEffect(() => {
        if (repSiteIdSkip.current) {
            repSiteIdSkip.current = false;
            return;
        }
        setRepSKU('All SKUs');
        setRepMonth('All Months');
        setRepWeek('All Weeks');
    }, [repSiteId]);

    useEffect(() => {
        if (repSkuSkip.current) {
            repSkuSkip.current = false;
            return;
        }
        setRepMonth('All Months');
        setRepWeek('All Weeks');
    }, [repSKU]);

    useEffect(() => {
        if (repMonthSkip.current) {
            repMonthSkip.current = false;
            return;
        }
        setRepWeek('All Weeks');
    }, [repMonth]);

    return (
        <FilterContext.Provider value={{ 
            season, setSeason, 
            sku, setSku, 
            store, setStore, 
            monthYear, setMonthYear,
            category, setCategory,
            baselineYears, setBaselineYears,
            aiSite, setAiSite,
            aiSKU, setAiSKU,
            aiDept, setAiDept,
            aiMonth, setAiMonth,
            repSite, setRepSite,
            repSKU, setRepSKU,
            repMonth, setRepMonth,
            repWeek, setRepWeek,
            repSiteId, setRepSiteId,
            repRecomTableMode, setRepRecomTableMode
        }}>
            {children}
        </FilterContext.Provider>
    );
};

export const SeasonProvider = FilterProvider;

export const useFilters = () => {
    const context = useContext(FilterContext);
    if (!context) {
        throw new Error('useFilters must be used within a FilterProvider');
    }
    return context;
};

// Keep old export for backward compatibility
export const useSeason = useFilters;

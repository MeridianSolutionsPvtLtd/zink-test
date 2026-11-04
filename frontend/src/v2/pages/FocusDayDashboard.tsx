import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { CalendarDays, Loader2, Sparkles } from 'lucide-react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import DashboardLayout from '../layouts/DashboardLayout';
import { DashboardShell, PageHeader } from '../design-system';
import { FocusDayKpiGrid, type FestivalSummaryKpis } from '../components/focus-day/FocusDayKpiGrid';
import { FocusFestivalComparisonView } from '../components/focus-day/FocusFestivalComparisonView';
import { API_URL } from '../../config';
import { formatCompactInr, formatInr } from '../components/focus-day/focusDayData';

type RankRow = { name: string; sales: number; qty: number };

type FestivalApi = {
  ok: boolean;
  source?: string;
  summary: {
    totalFestiveSales2024: number;
    totalFestiveSales2025: number;
    totalFestiveSales2026: number;
    totalQty2025?: number;
    totalQty2026?: number;
    totalGrowth2025vs2024: number | null;
    totalGrowth2026vs2025?: number | null;
    asp2025?: number;
    asp2026?: number;
  };
  events: Array<{
    id: string;
    name: string;
    topDepartments: Array<{ department: string; sales: number; qty: number; year: number }>;
    topStores?: RankRow[];
    topSkus?: RankRow[];
    detailYear?: number | null;
  }>;
};

function RankChart({
  title,
  subtitle,
  rows,
  nameKey = 'name',
}: {
  title: string;
  subtitle: string;
  rows: Array<Record<string, string | number>>;
  nameKey?: string;
}) {
  return (
    <div className="rounded-2xl border border-slate-200/80 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900/60">
      <div className="border-b border-slate-100 px-5 py-4 dark:border-slate-800">
        <h3 className="text-sm font-semibold text-slate-900 dark:text-white">{title}</h3>
        <p className="text-xs text-slate-500">{subtitle}</p>
      </div>
      <div className="h-[280px] p-4">
        {rows.length === 0 ? (
          <div className="flex h-full items-center justify-center text-xs text-slate-400">
            No sales in selected festival windows yet
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={rows} layout="vertical" margin={{ left: 8, right: 12 }}>
              <CartesianGrid strokeDasharray="3 3" horizontal={false} />
              <XAxis type="number" tickFormatter={(v) => formatCompactInr(Number(v))} tick={{ fontSize: 10 }} />
              <YAxis type="category" dataKey={nameKey} width={110} tick={{ fontSize: 10 }} />
              <Tooltip
                formatter={(v: number) => [formatInr(v), 'Sales']}
                labelFormatter={(l) => String(l)}
              />
              <Bar dataKey="sales" fill="#2563eb" radius={[0, 6, 6, 0]} />
            </BarChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}

export default function FocusDayDashboard() {
  const [refreshKey, setRefreshKey] = useState(0);
  const [lastRefresh, setLastRefresh] = useState(new Date());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [api, setApi] = useState<FestivalApi | null>(null);
  const [eventFilter, setEventFilter] = useState<string>('all');

  const load = useCallback(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    fetch(`${API_URL}/focus-day/festival-comparison`, { signal: controller.signal })
      .then(async (res) => {
        const raw = await res.text();
        let json: FestivalApi & { detail?: string; error?: string };
        try {
          json = JSON.parse(raw);
        } catch {
          throw new Error(
            res.ok
              ? 'Backend returned HTML instead of JSON — restart API with focus-day route'
              : `Failed (${res.status})`
          );
        }
        if (!res.ok) throw new Error(String(json.detail || json.error || `Failed (${res.status})`));
        if (json.error) throw new Error(String(json.error));
        if (!controller.signal.aborted) {
          setApi(json);
          setLoading(false);
        }
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setError(err instanceof Error ? err.message : 'Could not load festival sales');
        setLoading(false);
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const abort = load();
    return abort;
  }, [load, refreshKey]);

  const kpis: FestivalSummaryKpis | null = useMemo(() => {
    if (!api?.summary) return null;
    const s = api.summary;
    return {
      sales2024: s.totalFestiveSales2024,
      sales2025: s.totalFestiveSales2025,
      sales2026: s.totalFestiveSales2026,
      growth2025vs2024: s.totalGrowth2025vs2024,
      growth2026vs2025: s.totalGrowth2026vs2025 ?? null,
      qty2025: s.totalQty2025 ?? 0,
      qty2026: s.totalQty2026 ?? 0,
      asp2025: s.asp2025 ?? 0,
      asp2026: s.asp2026 ?? 0,
      eventsTracked: api.events?.length ?? 0,
    };
  }, [api]);

  const activeEvents = useMemo(() => {
    const events = api?.events ?? [];
    if (eventFilter === 'all') return events;
    return events.filter((e) => e.id === eventFilter);
  }, [api, eventFilter]);

  const topDepts = useMemo(() => {
    const map = new Map<string, number>();
    activeEvents.forEach((e) => {
      (e.topDepartments || []).forEach((d) => {
        map.set(d.department, (map.get(d.department) || 0) + d.sales);
      });
    });
    return [...map.entries()]
      .map(([name, sales]) => ({ name: name.slice(0, 18), sales }))
      .sort((a, b) => b.sales - a.sales)
      .slice(0, 8);
  }, [activeEvents]);

  const topStores = useMemo(() => {
    const map = new Map<string, number>();
    activeEvents.forEach((e) => {
      (e.topStores || []).forEach((d) => {
        map.set(d.name, (map.get(d.name) || 0) + d.sales);
      });
    });
    return [...map.entries()]
      .map(([name, sales]) => ({ name: name.slice(0, 18), sales }))
      .sort((a, b) => b.sales - a.sales)
      .slice(0, 8);
  }, [activeEvents]);

  const topSkus = useMemo(() => {
    const map = new Map<string, number>();
    activeEvents.forEach((e) => {
      (e.topSkus || []).forEach((d) => {
        map.set(d.name, (map.get(d.name) || 0) + d.sales);
      });
    });
    return [...map.entries()]
      .map(([name, sales]) => ({ name: name.slice(0, 14), sales }))
      .sort((a, b) => b.sales - a.sales)
      .slice(0, 8);
  }, [activeEvents]);

  const detailYear =
    activeEvents.find((e) => e.detailYear)?.detailYear ??
    activeEvents[0]?.detailYear ??
    2026;

  return (
    <DashboardLayout>
      <DashboardShell>
        <PageHeader
          icon={CalendarDays}
          title="Festival Day Dashboard"
          description="Live festival window sales from sales master · DurgaPuja · Diwali · Christmas · Valentine"
          badge="Festival Day"
          lastRefresh={lastRefresh}
          onRefresh={() => {
            setRefreshKey((k) => k + 1);
            setLastRefresh(new Date());
          }}
          onExport={() => window.print()}
        />

        <div className="flex flex-wrap items-center gap-2 rounded-2xl border border-slate-200/80 bg-white px-4 py-3 shadow-sm dark:border-slate-800 dark:bg-slate-900/60">
          <span className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">
            Event filter
          </span>
          {[
            { id: 'all', label: 'All festivals' },
            { id: 'durgapuja', label: 'DurgaPuja' },
            { id: 'diwali', label: 'Diwali' },
            { id: 'christmas', label: 'Christmas' },
            { id: 'valentine', label: 'Valentine' },
          ].map((opt) => (
            <button
              key={opt.id}
              type="button"
              onClick={() => setEventFilter(opt.id)}
              className={`rounded-full px-3 py-1 text-xs font-semibold transition ${
                eventFilter === opt.id
                  ? 'bg-blue-600 text-white'
                  : 'bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300'
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>

        {error && (
          <div className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
            {error}
          </div>
        )}

        <FocusDayKpiGrid data={kpis} loading={loading && !api} />

        <FocusFestivalComparisonView refreshTrigger={refreshKey} initialData={api} skipFetch />

        {loading && !api ? (
          <div className="flex h-40 items-center justify-center gap-2 text-sm text-slate-500">
            <Loader2 className="h-5 w-5 animate-spin text-blue-600" />
            Loading festival rankings from sales data…
          </div>
        ) : (
          <div className="grid gap-4 lg:grid-cols-3">
            <RankChart
              title="Top Departments"
              subtitle={`From real sales · detail year ${detailYear}`}
              rows={topDepts}
            />
            <RankChart
              title="Top Stores"
              subtitle={`Site-level festival sales · ${detailYear}`}
              rows={topStores}
            />
            <RankChart
              title="Top SKUs"
              subtitle={`Best sellers in festival windows · ${detailYear}`}
              rows={topSkus}
            />
          </div>
        )}

        <div className="flex items-center justify-center gap-2 pt-2 text-[10px] text-slate-400">
          <Sparkles className="h-3 w-3" />
          Festival Day · live sales master / COMBINED_SALES
          {api?.source ? ` · ${api.source}` : ''}
        </div>
      </DashboardShell>
    </DashboardLayout>
  );
}

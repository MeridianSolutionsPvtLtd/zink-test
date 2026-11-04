import React, { useMemo, useState } from 'react';
import { Database, RefreshCw, FolderSync, Clock } from 'lucide-react';
import {
  PageHeader,
  SectionCard,
  tableHeadClass,
  tableCellClass,
  tableRowClass,
  cardClass,
  DataFreshnessBadge,
  type FreshnessStatus,
} from '../design-system';
// import { API_URL } from '../../config'; // ← uncomment when wiring the real endpoint (see NOTE below)

/**
 * ── Data Sync Status window ──────────────────────────────────────────────
 * Shows, per ERP category, how current the merged data is: the last date
 * actually present ("Data as of"), when the pipeline last merged it, how
 * many rows, and a status pill so a stale/missed daily sync is obvious at a
 * glance instead of silently going unnoticed.
 *
 * THIS IS UI-ONLY FOR NOW — rows below are mock data shaped exactly like the
 * backend already has available (merge_category_inputs.py already writes a
 * `<canonical>.date_bounds.json` sidecar per category with min/max/updated_at
 * — see stockpulse-sales-kpi-gap-report.md notes). Wiring this up later is
 * just: build a small `/api/pipeline/freshness` endpoint that reads those
 * sidecars for every category in CATEGORY_MERGE_CONFIG and returns this same
 * shape, then replace MOCK_CATEGORIES below with the fetch effect that's
 * already stubbed out (search "REAL DATA WIRING").
 */

type SyncMode = 'date_merge' | 'replace';

type SyncCategory = {
  id: string;
  label: string;
  group: 'Sales & Transfer' | 'Stock (SOH) & Masters';
  mode: SyncMode;
  /** Latest date actually present in the merged data (date_merge categories only) */
  dataAsOf: string | null;
  /** When the pipeline last wrote this category's `current` file */
  lastSyncedAt: string;
  rows: number;
  note?: string;
};

// ── MOCK DATA — shaped like the real per-category date_bounds.json sidecars ──
const MOCK_CATEGORIES: SyncCategory[] = [
  {
    id: 'ebo-sales',
    label: 'EBO Sales',
    group: 'Sales & Transfer',
    mode: 'date_merge',
    dataAsOf: '2026-08-19',
    lastSyncedAt: '2026-08-19T06:42:00',
    rows: 214380,
  },
  {
    id: 'sales-master-pt-ssl-centro-iconic',
    label: 'Sales-Master (PT, SSL, Centro, Iconic, EBO)',
    group: 'Sales & Transfer',
    mode: 'date_merge',
    dataAsOf: '2025-12-31',
    lastSyncedAt: '2026-08-19T06:40:00',
    rows: 2100000,
    note: 'Full history master — predict uses last 180d cut from this',
  },
  {
    id: 'sales-pt-ssl-centro-iconic',
    label: 'Sales (PT, SSL, Centro, Iconic, EBO)',
    group: 'Sales & Transfer',
    mode: 'date_merge',
    dataAsOf: '2026-08-19',
    lastSyncedAt: '2026-08-19T06:44:00',
    rows: 861204,
  },
  {
    id: 'transfer-ebo',
    label: 'Transfer — EBO',
    group: 'Sales & Transfer',
    mode: 'date_merge',
    dataAsOf: '2026-08-18',
    lastSyncedAt: '2026-08-18T22:10:00',
    rows: 42918,
    note: 'Yesterday’s file only — today’s DC_DATE export not received yet',
  },
  {
    id: 'transfer-master-pt-ssl-centro-iconic',
    label: 'Transfer-Master (PT, SSL, Centro, Iconic)',
    group: 'Sales & Transfer',
    mode: 'date_merge',
    dataAsOf: '2025-12-31',
    lastSyncedAt: '2026-08-19T06:41:00',
    rows: 1200000,
    note: 'Full history master — working transfer uses rolling/180d cut',
  },
  {
    id: 'transfer-pt-ssl-centro-iconic',
    label: 'Transfer — (PT, SSL, Centro, Iconic)',
    group: 'Sales & Transfer',
    mode: 'date_merge',
    dataAsOf: '2026-08-17',
    lastSyncedAt: '2026-08-17T21:05:00',
    rows: 503774,
    note: 'No new Raw_Files upload for 2 days — check ERP export job',
  },
  {
    id: 'soh-master',
    label: 'SOH-Master',
    group: 'Stock (SOH) & Masters',
    mode: 'date_merge',
    dataAsOf: '2026-08-19',
    lastSyncedAt: '2026-08-19T05:35:00',
    rows: 2500000,
    note: 'Full SOH history master — date-merge on Date column',
  },
  {
    id: 'pantaloons-soh',
    label: 'Pantaloons — SOH',
    group: 'Stock (SOH) & Masters',
    mode: 'replace',
    dataAsOf: null,
    lastSyncedAt: '2026-08-19T05:30:00',
    rows: 118902,
  },
  {
    id: 'shoppers-stop-soh',
    label: 'Shoppers Stop — SOH',
    group: 'Stock (SOH) & Masters',
    mode: 'replace',
    dataAsOf: null,
    lastSyncedAt: '2026-08-19T05:31:00',
    rows: 76410,
  },
  {
    id: 'iconic-soh',
    label: 'Iconic — SOH',
    group: 'Stock (SOH) & Masters',
    mode: 'replace',
    dataAsOf: null,
    lastSyncedAt: '2026-08-19T05:31:00',
    rows: 54220,
  },
  {
    id: 'ebo-soh',
    label: 'EBO — SOH',
    group: 'Stock (SOH) & Masters',
    mode: 'replace',
    dataAsOf: null,
    lastSyncedAt: '2026-08-19T05:32:00',
    rows: 61830,
  },
  {
    id: 'centro-soh',
    label: 'Centro — SOH',
    group: 'Stock (SOH) & Masters',
    mode: 'replace',
    dataAsOf: null,
    lastSyncedAt: '2026-08-18T05:29:00',
    rows: 39510,
    note: 'Not refreshed today',
  },
  {
    id: 'warehouse-soh',
    label: 'Warehouse SOH',
    group: 'Stock (SOH) & Masters',
    mode: 'replace',
    dataAsOf: null,
    lastSyncedAt: '2026-08-19T05:33:00',
    rows: 28904,
  },
  {
    id: 'item-master',
    label: 'Item Master',
    group: 'Stock (SOH) & Masters',
    mode: 'replace',
    dataAsOf: null,
    lastSyncedAt: '2026-08-14T11:00:00',
    rows: 42980,
  },
  {
    id: 'store-grade-list',
    label: 'Store Grade List',
    group: 'Stock (SOH) & Masters',
    mode: 'replace',
    dataAsOf: null,
    lastSyncedAt: '2026-08-10T09:15:00',
    rows: 640,
  },
];

// ── Freshness rules ──────────────────────────────────────────────────────
// date_merge categories: compare dataAsOf to "today"/"yesterday" (ERP files
// typically land the next morning for the previous day's business).
// replace categories (SOH/masters): compare lastSyncedAt instead, since
// there's no rolling date range — a master just needs to have run recently.
function daysBetween(a: Date, b: Date): number {
  const ms = 24 * 60 * 60 * 1000;
  const da = new Date(a.getFullYear(), a.getMonth(), a.getDate());
  const db = new Date(b.getFullYear(), b.getMonth(), b.getDate());
  return Math.round((da.getTime() - db.getTime()) / ms);
}

function statusFor(cat: SyncCategory, today: Date): FreshnessStatus {
  if (cat.mode === 'date_merge') {
    if (!cat.dataAsOf) return 'stale';
    const behind = daysBetween(today, new Date(cat.dataAsOf));
    if (behind <= 1) return 'ok';
    if (behind === 2) return 'warning';
    return 'stale';
  }
  // replace mode — judge by last sync recency
  const behind = daysBetween(today, new Date(cat.lastSyncedAt));
  if (behind <= 1) return 'ok';
  if (behind <= 3) return 'warning';
  return 'stale';
}

const STATUS_LABEL: Record<FreshnessStatus, string> = {
  ok: 'Up to date',
  warning: 'Needs attention',
  stale: 'Sync missed',
};

const STATUS_PILL_CLASS: Record<FreshnessStatus, string> = {
  ok: 'bg-emerald-50 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-400',
  warning: 'bg-amber-50 text-amber-700 dark:bg-amber-500/15 dark:text-amber-400',
  stale: 'bg-rose-50 text-rose-700 dark:bg-rose-500/15 dark:text-rose-400',
};

function formatDate(iso: string | null) {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
}

function formatDateTime(iso: string) {
  return new Date(iso).toLocaleString('en-IN', {
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export default function DataSyncStatus() {
  const [loading, setLoading] = useState(false);
  const [categories, setCategories] = useState<SyncCategory[]>(MOCK_CATEGORIES);
  const [lastRefresh, setLastRefresh] = useState(new Date());
  const today = useMemo(() => new Date(), []);

  // ── REAL DATA WIRING (later) ───────────────────────────────────────────
  // useEffect(() => {
  //   let cancelled = false;
  //   (async () => {
  //     setLoading(true);
  //     try {
  //       const res = await fetch(`${API_URL}/pipeline/freshness`);
  //       const json = await res.json();
  //       if (!cancelled) setCategories(json.categories);
  //     } finally {
  //       if (!cancelled) setLoading(false);
  //     }
  //   })();
  //   return () => { cancelled = true; };
  // }, []);

  const withStatus = useMemo(
    () => categories.map((c) => ({ ...c, status: statusFor(c, today) })),
    [categories, today]
  );

  const overallAsOf = useMemo(() => {
    const dated = withStatus.filter((c) => c.mode === 'date_merge' && c.dataAsOf);
    if (!dated.length) return null;
    return dated.reduce((min, c) => (c.dataAsOf! < min ? c.dataAsOf! : min), dated[0].dataAsOf!);
  }, [withStatus]);

  const overallStatus: FreshnessStatus = withStatus.some((c) => c.status === 'stale')
    ? 'stale'
    : withStatus.some((c) => c.status === 'warning')
    ? 'warning'
    : 'ok';

  const counts = useMemo(
    () => ({
      ok: withStatus.filter((c) => c.status === 'ok').length,
      warning: withStatus.filter((c) => c.status === 'warning').length,
      stale: withStatus.filter((c) => c.status === 'stale').length,
    }),
    [withStatus]
  );

  const handleRefresh = () => {
    // Placeholder until wired — just re-stamps "checked at" so the header
    // reflects the manual refresh action.
    setLoading(true);
    setTimeout(() => {
      setLastRefresh(new Date());
      setLoading(false);
    }, 500);
  };

  const grouped: Record<string, typeof withStatus> = {};
  for (const c of withStatus) {
    grouped[c.group] = grouped[c.group] ? [...grouped[c.group], c] : [c];
  }

  return (
    <div className="space-y-4 p-4 lg:p-6">
      <PageHeader
        icon={FolderSync}
        badge="PIPELINE"
        title="Data Sync Status"
        description="Freshness of every ERP category merged from Raw_Files — Sales, Transfer, Stock (SOH) and Masters."
        lastRefresh={lastRefresh}
        loading={loading}
        onRefresh={handleRefresh}
        extra={
          <DataFreshnessBadge
            label={overallAsOf ? formatDate(overallAsOf) : '—'}
            status={overallStatus}
            tooltip="Oldest 'data as of' among Sales/Transfer categories"
          />
        }
      />

      {/* Summary tiles */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <SummaryTile icon={Database} label="Categories tracked" value={String(withStatus.length)} accent="from-blue-600 to-blue-700" />
        <SummaryTile icon={Clock} label="Up to date" value={String(counts.ok)} accent="from-emerald-500 to-teal-600" />
        <SummaryTile icon={RefreshCw} label="Needs attention" value={String(counts.warning)} accent="from-amber-500 to-orange-600" />
        <SummaryTile icon={FolderSync} label="Sync missed" value={String(counts.stale)} accent="from-rose-500 to-pink-600" />
      </div>

      {Object.entries(grouped).map(([group, rows]) => (
        <SectionCard
          key={group}
          title={group}
          subtitle={
            group === 'Sales & Transfer'
              ? 'Date-aware merge — “Data as of” reflects the newest transaction date actually present'
              : 'Full-replace snapshots — freshness judged by last sync time, not a date range'
          }
          noPadding
        >
          <div className="overflow-x-auto">
            <table className="w-full border-collapse">
              <thead>
                <tr>
                  <th className={tableHeadClass}>Category</th>
                  <th className={tableHeadClass}>Data as of</th>
                  <th className={tableHeadClass}>Last synced</th>
                  <th className={tableHeadClass}>Rows</th>
                  <th className={tableHeadClass}>Status</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((c) => (
                  <tr key={c.id} className={tableRowClass}>
                    <td className={tableCellClass}>
                      <div className="font-semibold text-slate-800 dark:text-slate-100">{c.label}</div>
                      {c.note && <div className="mt-0.5 text-[11px] text-slate-400">{c.note}</div>}
                    </td>
                    <td className={tableCellClass}>{c.mode === 'date_merge' ? formatDate(c.dataAsOf) : '—'}</td>
                    <td className={tableCellClass}>{formatDateTime(c.lastSyncedAt)}</td>
                    <td className={tableCellClass}>{c.rows.toLocaleString('en-IN')}</td>
                    <td className={tableCellClass}>
                      <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-semibold ${STATUS_PILL_CLASS[c.status]}`}>
                        <span className="h-1.5 w-1.5 rounded-full bg-current" />
                        {STATUS_LABEL[c.status]}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </SectionCard>
      ))}
    </div>
  );
}

function SummaryTile({
  icon: Icon,
  label,
  value,
  accent,
}: {
  icon: React.ElementType;
  label: string;
  value: string;
  accent: string;
}) {
  return (
    <div className={`${cardClass} flex items-center gap-3 p-4`}>
      <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br ${accent} text-white`}>
        <Icon className="h-5 w-5" />
      </div>
      <div>
        <p className="text-[11px] font-medium uppercase tracking-wider text-slate-500 dark:text-slate-400">{label}</p>
        <p className="mt-0.5 text-xl font-bold tracking-tight text-slate-900 dark:text-white">{value}</p>
      </div>
    </div>
  );
}

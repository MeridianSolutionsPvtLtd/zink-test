import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertCircle,
  ArrowRight,
  CheckCircle2,
  Cloud,
  FileSpreadsheet,
  FolderOpen,
  Loader2,
  Play,
  RefreshCw,
  SkipForward,
  Sparkles,
} from 'lucide-react';
import { API_URL } from '../../config';
import { DailyPipelinePanel } from './DailyPipelinePanel';

type PlannedFile = {
  blob: string;
  filename: string;
  size: number;
  category_id: string;
  file_date: string | null;
  action: string;
  current_max: string | null;
  folder?: string;
};

type SkippedFile = PlannedFile & { reason?: string };

type PreviewResponse = {
  ok?: boolean;
  current_max?: Record<string, string | null>;
  planned?: PlannedFile[];
  skipped?: SkippedFile[];
  raw_file_count?: number;
  container?: string;
  raw_prefix?: string;
  hint?: string;
  empty_hint?: string;
  detail?: string;
  coverage?: Record<
    string,
    {
      category_id: string;
      current_min?: string | null;
      current_max?: string | null;
      raw_file_dates?: string[];
      will_merge_dates?: string[];
      missing_dates?: string[];
      gaps_in_raw_files?: string[];
      expected_through?: string;
      ok?: boolean;
    }
  >;
};

const CATEGORY_LABEL: Record<string, string> = {
  'sales-master-pt-ssl-centro-iconic': 'Sales-Master (PT, SSL, Centro, Iconic, EBO)',
  'sales-pt-ssl-centro-iconic': 'Sales (PT, SSL, Centro, Iconic, EBO)',
  'ebo-sales': 'EBO Sales',
  'transfer-master-pt-ssl-centro-iconic': 'Transfer-Master (PT, SSL, Centro, Iconic)',
  'transfer-pt-ssl-centro-iconic': 'Transfer (PT, SSL, Centro, Iconic)',
  'transfer-ebo': 'Transfer – EBO',
  'ebo-soh': 'EBO SOH',
  'warehouse-soh': 'Warehouse SOH',
  'soh-master': 'SOH-Master',
  'item-master': 'Item Master',
  'customer-master': 'Customer Master',
  'financial-week-info': 'Financial Week Info',
  'combined-soh': 'Combined SOH',
  'pantaloons-soh': 'Pantaloons SOH',
  'shoppers-stop-soh': 'Shoppers Stop SOH',
  'iconic-soh': 'Iconic SOH',
  'centro-soh': 'Centro SOH',
  'store-grade-list': 'Store Grade List',
  'top-sellers-list': 'Top Sellers List',
  'sku-remap': 'SKU Remap',
  'target-lfs': 'Target (LFS)',
  'rtv-data': 'RTV Data',
  'ibt-data': 'IBT Data',
  'grc-report': 'GRC Report',
};

const ACTION_META: Record<string, { label: string; className: string }> = {
  date_merge_append: {
    label: 'New dates only',
    className:
      'border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-300',
  },
  date_merge_gap_fill: {
    label: 'Fill missing date',
    className:
      'border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300',
  },
  replace_latest: {
    label: 'Latest file only',
    className:
      'border-indigo-200 bg-indigo-50 text-indigo-700 dark:border-indigo-500/30 dark:bg-indigo-500/10 dark:text-indigo-300',
  },
};

function fmtBytes(n: number) {
  if (!Number.isFinite(n)) return '—';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function fmtDate(iso: string | null | undefined) {
  if (!iso) return '—';
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
}

function prettyReason(reason?: string) {
  if (!reason) return 'Skipped';
  if (reason.startsWith('already_in_current')) return 'Already in current';
  if (reason === 'older_than_latest') return 'Older than latest';
  if (reason === 'unmapped_filename') return 'Filename not mapped';
  if (reason === 'no_date_in_filename') return 'No date in filename';
  if (reason === 'history_bulk_dump_skip_daily_merge') {
    return 'Year/history dump (skip daily merge)';
  }
  return reason.replace(/_/g, ' ');
}

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function pollMergeUntilDone(
  jobId: string,
  onProgress?: (pct: number, message: string) => void,
  maxWaitMs = 30 * 60 * 1000
) {
  const started = Date.now();
  let delay = 800;
  while (Date.now() - started < maxWaitMs) {
    const res = await fetch(`${API_URL}/upload/merge-status?job_id=${encodeURIComponent(jobId)}`);
    const data = (await res.json()) as Record<string, unknown>;
    const status = String(data?.status ?? '');
    const pct = Number(data?.progress_pct ?? 0);
    const msg = String(data?.progress_message || data?.status || 'Merging…');
    onProgress?.(Number.isFinite(pct) ? pct : 0, msg);
    if (status === 'success') return data;
    if (status === 'failed') {
      throw new Error(String(data?.error || 'Merge failed'));
    }
    await sleep(delay);
    delay = Math.min(Math.round(delay * 1.25), 4000);
  }
  throw new Error('Merge timed out (30 min). Check backend logs — large Excel merges can take a while.');
}

function StatCard({
  label,
  value,
  hint,
  accent,
}: {
  label: string;
  value: number | string;
  hint?: string;
  accent: string;
}) {
  return (
    <div className="rounded-2xl border border-slate-200/80 bg-white px-4 py-4 shadow-[0_1px_3px_rgba(15,23,42,0.04)] dark:border-slate-800 dark:bg-slate-900">
      <p className="text-[11px] font-bold uppercase tracking-widest text-slate-400">{label}</p>
      <p className={`mt-1 text-2xl font-bold tabular-nums tracking-tight ${accent}`}>{value}</p>
      {hint ? <p className="mt-1 text-[11px] text-slate-500">{hint}</p> : null}
    </div>
  );
}

export function BlobFetchPanel() {
  const [loading, setLoading] = useState(false);
  const [fetching, setFetching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [selectedCats, setSelectedCats] = useState<Set<string>>(new Set());
  const [mergeProgress, setMergeProgress] = useState<{ pct: number; message: string; phase: 'copy' | 'merge' } | null>(
    null
  );

  const loadPreview = useCallback(async () => {
    setLoading(true);
    setError(null);
    setMessage(null);
    try {
      const res = await fetch(`${API_URL}/upload/blob-preview`);
      const data = (await res.json()) as PreviewResponse;
      if (!res.ok) {
        const detail = String(data?.detail || 'Preview failed');
        if (res.status === 404) {
          throw new Error(
            'Blob preview API is not on this backend. Restart uvicorn on the backend that has /api/upload/blob-preview.'
          );
        }
        throw new Error(detail);
      }
      setPreview(data);
      // Default: select all planned categories
      const ids = new Set((data.planned || []).map((f) => f.category_id).filter(Boolean));
      setSelectedCats(ids);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Preview failed');
      setPreview(null);
      setSelectedCats(new Set());
    } finally {
      setLoading(false);
    }
  }, []);

  // Auto-scan Raw_Files when the Blob tab opens
  useEffect(() => {
    void loadPreview();
  }, [loadPreview]);

  const planned = preview?.planned ?? [];
  const skipped = preview?.skipped ?? [];
  const selectedPlanned = useMemo(
    () => planned.filter((f) => selectedCats.has(f.category_id)),
    [planned, selectedCats]
  );
  const grouped = useMemo(() => {
    const map = new Map<string, PlannedFile[]>();
    for (const f of planned) {
      const key = f.category_id || 'other';
      map.set(key, [...(map.get(key) || []), f]);
    }
    return [...map.entries()];
  }, [planned]);

  const skippedGrouped = useMemo(() => {
    const map = new Map<string, SkippedFile[]>();
    for (const f of skipped) {
      const key = prettyReason(f.reason);
      map.set(key, [...(map.get(key) || []), f]);
    }
    return [...map.entries()];
  }, [skipped]);

  const toggleCat = (cid: string) => {
    setSelectedCats((prev) => {
      const next = new Set(prev);
      if (next.has(cid)) next.delete(cid);
      else next.add(cid);
      return next;
    });
  };

  const selectAllCats = () => setSelectedCats(new Set(grouped.map(([cid]) => cid)));
  const clearCats = () => setSelectedCats(new Set());

  const handleFetch = async () => {
    if (!selectedPlanned.length) return;
    setFetching(true);
    setError(null);
    setMessage(null);
    setMergeProgress({ pct: 0, message: 'Copying files to incoming/…', phase: 'copy' });
    try {
      const cats = encodeURIComponent([...selectedCats].join(','));
      const res = await fetch(
        `${API_URL}/upload/blob-fetch?wait_merge=0&sync_local=1&categories=${cats}`,
        { method: 'POST' }
      );
      const data = (await res.json()) as Record<string, unknown>;
      if (!res.ok) throw new Error(String(data?.detail || 'Fetch failed'));
      const jobId = typeof data.job_id === 'string' ? data.job_id : '';
      if (jobId) {
        setMergeProgress({ pct: 5, message: 'Starting merge into current/…', phase: 'merge' });
        await pollMergeUntilDone(jobId, (pct, msg) => {
          setMergeProgress({ pct, message: msg, phase: 'merge' });
        });
        setMergeProgress(null);
        setMessage(
          'Merge complete → current/ updated. Daily 9 AM job also runs combine only. For refill/prediction: Manual Upload → Run Pipeline.'
        );
      } else {
        setMergeProgress(null);
        setMessage(String(data.message || 'No new files to fetch.'));
      }
      await loadPreview();
    } catch (err) {
      setMergeProgress(null);
      setError(err instanceof Error ? err.message : 'Fetch failed');
    } finally {
      setFetching(false);
    }
  };

  const MASTER_IDS = [
    'sales-master-pt-ssl-centro-iconic',
    'transfer-master-pt-ssl-centro-iconic',
  ];
  const maxDates = Object.entries(preview?.current_max || {}).filter(([, v]) => v) as [string, string][];
  const masterDates = MASTER_IDS.map(
    (id) => [id, preview?.current_max?.[id] || null] as [string, string | null]
  );
  const otherDates = maxDates.filter(([k]) => !MASTER_IDS.includes(k));
  const coverageRows = useMemo(() => {
    const cov = preview?.coverage || {};
    return Object.values(cov).filter(Boolean);
  }, [preview?.coverage]);
  const missingAlerts = useMemo(
    () =>
      coverageRows.filter(
        (c) => (c.missing_dates?.length || 0) > 0 || (c.gaps_in_raw_files?.length || 0) > 0
      ),
    [coverageRows]
  );

  return (
    <div className="mx-auto w-full max-w-5xl px-2 sm:px-4">
      <header className="mb-7 text-center">
        <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-gradient-to-br from-indigo-500 to-sky-600 text-white shadow-lg shadow-indigo-500/25">
          <Cloud className="h-8 w-8" strokeWidth={1.75} />
        </div>
        <h1 className="text-2xl font-bold tracking-tight text-slate-900 dark:text-white sm:text-3xl">
          Upload from Blob
        </h1>
        <p className="mx-auto mt-2 max-w-xl text-sm leading-relaxed text-slate-500 dark:text-slate-400">
          Pull daily Ginesys dumps from <span className="font-semibold text-slate-600 dark:text-slate-300">Raw_Files/</span>,
          merge into current, then run the pipeline from Manual Upload.
        </p>
        <div className="mt-4 flex flex-wrap items-center justify-center gap-2">
          <span className="rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1 text-[11px] font-semibold text-emerald-700 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-300">
            Sales / Transfer · new dates
          </span>
          <span className="rounded-full border border-indigo-200 bg-indigo-50 px-3 py-1 text-[11px] font-semibold text-indigo-700 dark:border-indigo-500/30 dark:bg-indigo-500/10 dark:text-indigo-300">
            Master / Combined SOH · latest only
          </span>
        </div>
      </header>

      <DailyPipelinePanel />

      <div className="mb-5 overflow-hidden rounded-2xl border border-slate-200/90 bg-white shadow-xl shadow-slate-200/40 dark:border-slate-700 dark:bg-slate-900 dark:shadow-none">
        <div className="flex flex-col gap-4 border-b border-slate-100 bg-slate-50/80 px-5 py-4 sm:flex-row sm:items-center sm:justify-between dark:border-slate-800 dark:bg-slate-800/50">
          <div>
            <p className="text-[11px] font-bold uppercase tracking-widest text-slate-400">Step 1 · Preview</p>
            <p className="mt-0.5 text-sm font-semibold text-slate-800 dark:text-slate-100">
              Scan Raw_Files, then copy only what is new
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => void loadPreview()}
              disabled={loading || fetching}
              className="inline-flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700 shadow-sm transition hover:bg-slate-50 disabled:opacity-50 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700"
            >
              {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
              {preview ? 'Refresh preview' : 'Fetch preview'}
            </button>
            <button
              type="button"
              disabled={!selectedPlanned.length || loading || fetching}
              onClick={() => void handleFetch()}
              className="inline-flex items-center gap-2 rounded-xl bg-gradient-to-r from-indigo-600 to-sky-600 px-4 py-2.5 text-sm font-semibold text-white shadow-md shadow-indigo-500/20 transition hover:from-indigo-500 hover:to-sky-500 disabled:cursor-not-allowed disabled:opacity-40 disabled:shadow-none"
            >
              {fetching ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
              Copy & merge selected
              {selectedPlanned.length > 0 ? (
                <span className="rounded-full bg-white/20 px-2 py-0.5 text-[11px]">
                  {selectedCats.size} cat · {selectedPlanned.length} file
                  {selectedPlanned.length !== 1 ? 's' : ''}
                </span>
              ) : null}
            </button>
          </div>
        </div>

        <div className="grid gap-3 p-4 sm:grid-cols-3">
          <StatCard
            label="Raw files"
            value={preview ? preview.raw_file_count ?? 0 : '—'}
            hint={preview?.raw_prefix || 'Raw_Files/'}
            accent="text-slate-900 dark:text-white"
          />
          <StatCard
            label="To fetch"
            value={preview ? selectedPlanned.length : '—'}
            hint={
              preview
                ? `${selectedCats.size}/${grouped.length || 0} categories selected`
                : 'Will copy into incoming/'
            }
            accent="text-indigo-600 dark:text-indigo-300"
          />
          <StatCard
            label="Skipped"
            value={preview ? skipped.length : '—'}
            hint="Already current or older"
            accent="text-slate-500"
          />
        </div>
      </div>

      {error && (
        <div className="mb-4 flex items-start gap-3 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 dark:border-rose-900/50 dark:bg-rose-950/30 dark:text-rose-300">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}
      {message && (
        <div className="mb-4 flex items-start gap-3 rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800 dark:border-emerald-900/40 dark:bg-emerald-950/30 dark:text-emerald-200">
          <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{message}</span>
        </div>
      )}
      {(loading || fetching) && (
        <div className="mb-4 overflow-hidden rounded-2xl border border-indigo-200 bg-indigo-50 px-4 py-4 dark:border-indigo-500/30 dark:bg-indigo-500/10">
          <div className="flex items-start gap-3">
            <Loader2 className="mt-0.5 h-4 w-4 shrink-0 animate-spin text-indigo-700 dark:text-indigo-300" />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold text-indigo-900 dark:text-indigo-100">
                {fetching && mergeProgress?.phase === 'merge'
                  ? `Merging into current/… ${mergeProgress.pct}%`
                  : fetching
                    ? mergeProgress?.message || 'Copying files and merging into current/…'
                    : 'Scanning Raw_Files… can take up to a minute.'}
              </p>
              {fetching && mergeProgress?.phase === 'merge' && mergeProgress.message ? (
                <p className="mt-1 text-xs text-indigo-800/90 dark:text-indigo-200/90">{mergeProgress.message}</p>
              ) : fetching ? (
                <p className="mt-1 text-xs text-indigo-800/80 dark:text-indigo-200/80">
                  Large Excel merges can take a few minutes — progress updates every few seconds.
                </p>
              ) : null}
              {fetching && mergeProgress?.phase === 'merge' ? (
                <div className="mt-3 h-2 overflow-hidden rounded-full bg-indigo-200/80 dark:bg-indigo-900/50">
                  <div
                    className="h-full rounded-full bg-gradient-to-r from-indigo-600 to-sky-500 transition-all duration-500"
                    style={{ width: `${Math.min(100, Math.max(2, mergeProgress.pct))}%` }}
                  />
                </div>
              ) : fetching ? (
                <div className="mt-3 h-2 overflow-hidden rounded-full bg-indigo-200/80 dark:bg-indigo-900/50">
                  <div className="h-full w-1/3 animate-pulse rounded-full bg-indigo-400/70" />
                </div>
              ) : null}
            </div>
          </div>
        </div>
      )}

      {!preview && !loading && !error && (
        <div className="rounded-2xl border border-dashed border-slate-300 bg-white px-6 py-14 text-center dark:border-slate-700 dark:bg-slate-900">
          <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-slate-100 text-slate-500 dark:bg-slate-800">
            <FolderOpen className="h-6 w-6" />
          </div>
          <p className="text-base font-semibold text-slate-800 dark:text-white">No preview yet</p>
          <p className="mx-auto mt-1 max-w-md text-sm text-slate-500">
            Click Fetch preview to list new Ginesys files. Item Master and Combined SOH will pick the latest file only.
          </p>
        </div>
      )}

      {preview?.empty_hint && (
        <div className="mb-4 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
          {preview.empty_hint}
        </div>
      )}

      {missingAlerts.length > 0 && (
        <div className="mb-4 rounded-2xl border border-amber-300 bg-amber-50 px-4 py-4 dark:border-amber-500/40 dark:bg-amber-500/10">
          <p className="mb-2 flex items-center gap-2 text-[11px] font-bold uppercase tracking-widest text-amber-700 dark:text-amber-300">
            <AlertCircle className="h-3.5 w-3.5" />
            Missing daily files (Sales / Transfer)
          </p>
          <p className="mb-3 text-xs text-amber-800/80 dark:text-amber-200/80">
            Same daily filename pattern — date not in Raw_Files yet (e.g. 18 Aug). When that file arrives later it
            shows as “Fill missing date” and still merges, even if current max already moved ahead.
          </p>
          <div className="space-y-3">
            {missingAlerts.map((c) => (
              <div
                key={c.category_id}
                className="rounded-xl border border-amber-200/80 bg-white/70 px-3 py-2.5 dark:border-amber-500/20 dark:bg-slate-900/40"
              >
                <p className="text-xs font-semibold text-slate-800 dark:text-slate-100">
                  {CATEGORY_LABEL[c.category_id] || c.category_id}
                  <span className="ml-2 font-normal text-slate-500">
                    current {fmtDate(c.current_min)} → {fmtDate(c.current_max)}
                  </span>
                </p>
                {!!c.missing_dates?.length && (
                  <p className="mt-1 text-[11px] text-rose-700 dark:text-rose-300">
                    Not in Raw_Files yet: {c.missing_dates.slice(0, 12).map(fmtDate).join(', ')}
                    {c.missing_dates.length > 12 ? ` +${c.missing_dates.length - 12} more` : ''}
                  </p>
                )}
                {!!c.gaps_in_raw_files?.length && (
                  <p className="mt-1 text-[11px] text-amber-800 dark:text-amber-200">
                    Gap inside Raw_Files: {c.gaps_in_raw_files.slice(0, 12).map(fmtDate).join(', ')}
                    {c.gaps_in_raw_files.length > 12 ? ` +${c.gaps_in_raw_files.length - 12} more` : ''}
                  </p>
                )}
                {!!c.will_merge_dates?.length && (
                  <p className="mt-1 text-[11px] text-emerald-700 dark:text-emerald-300">
                    Will merge: {c.will_merge_dates.slice(0, 10).map(fmtDate).join(', ')}
                    {c.will_merge_dates.length > 10 ? ` +${c.will_merge_dates.length - 10} more` : ''}
                  </p>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {coverageRows.length > 0 && missingAlerts.length === 0 && (
        <div className="mb-4 rounded-2xl border border-emerald-200 bg-emerald-50/80 px-4 py-3 text-sm text-emerald-800 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-200">
          Sales / Transfer daily coverage looks complete through{' '}
          {fmtDate(coverageRows[0]?.expected_through)} (no missing Raw_Files dates after current max).
        </div>
      )}

      {preview && (
        <div className="mb-4 rounded-2xl border border-indigo-200/80 bg-white px-4 py-4 dark:border-indigo-500/30 dark:bg-slate-900">
          <p className="mb-1 text-[11px] font-bold uppercase tracking-widest text-indigo-500">
            Masters — current/ from blob
          </p>
          <p className="mb-3 text-xs text-slate-500 dark:text-slate-400">
            Sales-Master and Transfer-Master update when daily files are copied and merged. Only days after the date below are appended.
          </p>
          <div className="grid gap-2 sm:grid-cols-2">
            {masterDates.map(([k, v]) => (
              <div
                key={k}
                className="rounded-xl border border-indigo-100 bg-indigo-50/50 px-3 py-2.5 dark:border-indigo-500/20 dark:bg-indigo-500/10"
              >
                <p className="text-xs font-semibold text-slate-800 dark:text-slate-100">
                  {CATEGORY_LABEL[k] || k}
                </p>
                <p className="text-[11px] text-slate-500 dark:text-slate-400">
                  Data as of{' '}
                  <span className="font-semibold text-slate-800 dark:text-slate-100">{fmtDate(v)}</span>
                </p>
              </div>
            ))}
          </div>
        </div>
      )}

      {otherDates.length > 0 && (
        <div className="mb-4 rounded-2xl border border-slate-200/80 bg-white px-4 py-4 dark:border-slate-800 dark:bg-slate-900">
          <p className="mb-3 text-[11px] font-bold uppercase tracking-widest text-slate-400">
            Input files — data as of
          </p>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {otherDates.map(([k, v]) => (
              <div
                key={k}
                className="rounded-xl border border-slate-100 bg-slate-50/60 px-3 py-2.5 dark:border-slate-800 dark:bg-slate-800/40"
              >
                <p className="truncate text-xs font-semibold text-slate-700 dark:text-slate-200">
                  {CATEGORY_LABEL[k] || k}
                </p>
                <p className="text-[11px] text-slate-500 dark:text-slate-400">
                  Data as of <span className="font-semibold text-slate-700 dark:text-slate-200">{fmtDate(v)}</span>
                </p>
              </div>
            ))}
          </div>
        </div>
      )}

      {grouped.length > 0 && (
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <p className="text-[11px] font-bold uppercase tracking-widest text-slate-400">
            Step 2 · Select categories · {selectedCats.size}/{grouped.length} selected
          </p>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={selectAllCats}
              disabled={fetching}
              className="rounded-lg border border-slate-200 bg-white px-2.5 py-1 text-[11px] font-semibold text-slate-600 hover:bg-slate-50 disabled:opacity-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300"
            >
              Select all
            </button>
            <button
              type="button"
              onClick={clearCats}
              disabled={fetching}
              className="rounded-lg border border-slate-200 bg-white px-2.5 py-1 text-[11px] font-semibold text-slate-600 hover:bg-slate-50 disabled:opacity-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300"
            >
              Clear
            </button>
          </div>
        </div>
      )}

      <div className="space-y-3">
        {grouped.map(([cid, files]) => {
          const action = ACTION_META[files[0]?.action] || {
            label: files[0]?.action || 'Fetch',
            className: 'border-slate-200 bg-slate-50 text-slate-600',
          };
          const checked = selectedCats.has(cid);
          return (
            <section
              key={cid}
              className={`overflow-hidden rounded-2xl border bg-white shadow-[0_1px_3px_rgba(15,23,42,0.04)] dark:bg-slate-900 ${
                checked
                  ? 'border-indigo-300 dark:border-indigo-500/40'
                  : 'border-slate-200/90 opacity-70 dark:border-slate-800'
              }`}
            >
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 bg-slate-50/70 px-4 py-3 dark:border-slate-800 dark:bg-slate-800/40">
                <label className="flex min-w-0 cursor-pointer items-start gap-3">
                  <input
                    type="checkbox"
                    checked={checked}
                    disabled={fetching}
                    onChange={() => toggleCat(cid)}
                    className="mt-1 h-4 w-4 shrink-0 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
                  />
                  <div className="min-w-0">
                    <p className="truncate text-sm font-bold text-slate-800 dark:text-white">
                      {files[0]?.folder || CATEGORY_LABEL[cid] || cid}
                    </p>
                    <p className="text-[11px] text-slate-500">
                      {files[0]?.current_max
                        ? `Current data as of ${fmtDate(files[0].current_max)} — ${files.length} file(s) newer`
                        : `No current data yet · ${files.length} file(s)`}
                    </p>
                  </div>
                </label>
                <span className={`rounded-full border px-2.5 py-1 text-[11px] font-semibold ${action.className}`}>
                  {action.label}
                </span>
              </div>
              <ul className="divide-y divide-slate-100 dark:divide-slate-800">
                {files.map((f) => (
                  <li key={f.blob} className="flex items-center gap-3 px-4 py-3">
                    <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-indigo-50 text-indigo-600 dark:bg-indigo-500/10 dark:text-indigo-300">
                      <FileSpreadsheet className="h-4 w-4" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium text-slate-800 dark:text-slate-100">{f.filename}</p>
                      <p className="text-[11px] text-slate-500">
                        {fmtDate(f.file_date)} · {fmtBytes(f.size)}
                      </p>
                    </div>
                    <ArrowRight className="hidden h-4 w-4 text-slate-300 sm:block dark:text-slate-600" />
                  </li>
                ))}
              </ul>
            </section>
          );
        })}
      </div>

      {preview && planned.length > 0 && (
        <p className="mt-4 flex items-center justify-center gap-1.5 text-xs text-slate-500">
          <Sparkles className="h-3.5 w-3.5" />
          After merge, switch to Manual Upload → Confirm & run pipeline
        </p>
      )}

      {skipped.length > 0 && (
        <details className="mt-5 overflow-hidden rounded-2xl border border-slate-200/90 bg-white dark:border-slate-800 dark:bg-slate-900">
          <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-3 text-sm font-semibold text-slate-600 marker:content-none dark:text-slate-300 [&::-webkit-details-marker]:hidden">
            <SkipForward className="h-4 w-4 text-slate-400" />
            Skipped files ({skipped.length})
            <span className="ml-auto text-[11px] font-medium text-slate-400">Show</span>
          </summary>
          <div className="max-h-64 space-y-3 overflow-y-auto border-t border-slate-100 px-4 py-3 dark:border-slate-800">
            {skippedGrouped.map(([reason, files]) => (
              <div key={reason}>
                <p className="mb-1.5 text-[11px] font-bold uppercase tracking-wide text-slate-400">{reason}</p>
                <ul className="space-y-1 text-xs text-slate-600 dark:text-slate-400">
                  {files.map((f) => (
                    <li key={f.blob} className="truncate">
                      {f.filename}
                      {f.file_date ? <span className="text-slate-400"> · {fmtDate(f.file_date)}</span> : null}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}

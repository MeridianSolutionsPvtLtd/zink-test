import React, { useEffect, useState } from 'react';
import { Loader2, AlertCircle, CheckCircle2, Square, Ban, RotateCcw } from 'lucide-react';
import { API_URL } from '../../config';

type PipelineStatus = {
  status: string;
  stage?: string;
  current_step?: string | null;
  current_label?: string;
  progress_pct?: number;
  phase?: string;
  error?: string | null;
  resume_from?: string | null;
  can_resume?: boolean;
};

/** Backend used to tag phase=ml when step text contained "prediction/" — fix display from label/step. */
function resolveDisplayPhase(status: PipelineStatus): string {
  if (status.status === 'failed') return 'failed';
  if (status.status === 'cancelled') return 'cancelled';
  if (status.status === 'success') return 'done';

  const blob = `${status.current_step || ''} ${status.current_label || ''}`.toLowerCase();

  if (blob.includes('publish_replenishment') || blob.includes('publish replenishment')) {
    return 'refill';
  }
  if (
    blob.includes('6.build_refill') ||
    blob.includes('build refill') ||
    blob.includes('7_enrich') ||
    blob.includes('enrich sku')
  ) {
    return 'refill';
  }
  if (
    blob.includes('transfer/') ||
    blob.includes('store_soh') ||
    blob.includes('soh_null') ||
    blob.includes('donors') ||
    blob.includes('broken size') ||
    blob.includes('recommend transfer') ||
    blob.includes('final transfer')
  ) {
    return 'transfer';
  }
  if (
    blob.includes('5.predict') ||
    blob.includes('predict_weekly') ||
    blob.includes('predict weekly')
  ) {
    return 'ml';
  }
  if (blob.includes('combine')) return 'combine';
  if (blob.includes('sync')) return 'sync';
  if (blob.includes('publish_outputs') || blob.includes('publish outputs')) return 'refill';
  if (
    blob.includes('sales_report') ||
    blob.includes('weekly_aggregate') ||
    blob.includes('add_soh') ||
    blob.includes('first transfer')
  ) {
    return 'analyze';
  }

  // Ignore bogus backend "ml" when step is not actually predict
  if (status.phase === 'ml' && !/5\.predict|predict_weekly|predict weekly/i.test(blob)) {
    return 'analyze';
  }
  return status.phase || 'analyze';
}

type PhaseStyle = {
  title: string;
  bar: string;
  lightBg: string;
  darkBg: string;
  ring: string;
  glow: string;
};

const PHASES: Record<string, PhaseStyle> = {
  idle: {
    title: 'Ready',
    bar: 'from-slate-400 to-slate-500',
    lightBg: 'from-slate-50 to-white',
    darkBg: 'from-slate-900 to-slate-950',
    ring: 'border-slate-300 dark:border-slate-600',
    glow: 'bg-slate-400/20',
  },
  sync: {
    title: 'Syncing live data',
    bar: 'from-sky-400 to-cyan-500',
    lightBg: 'from-sky-50 to-cyan-50/60',
    darkBg: 'from-sky-950/80 to-slate-950',
    ring: 'border-sky-400 dark:border-sky-500',
    glow: 'bg-sky-400/25',
  },
  combine: {
    title: 'Combining channel files',
    bar: 'from-emerald-400 to-teal-500',
    lightBg: 'from-emerald-50 to-teal-50/60',
    darkBg: 'from-emerald-950/70 to-slate-950',
    ring: 'border-emerald-400 dark:border-emerald-500',
    glow: 'bg-emerald-400/25',
  },
  analyze: {
    title: 'Feature engineering',
    bar: 'from-violet-400 to-indigo-500',
    lightBg: 'from-violet-50 to-indigo-50/60',
    darkBg: 'from-violet-950/70 to-slate-950',
    ring: 'border-violet-400 dark:border-violet-500',
    glow: 'bg-violet-400/25',
  },
  ml: {
    title: 'ML prediction running',
    bar: 'from-fuchsia-400 to-purple-500',
    lightBg: 'from-fuchsia-50 to-purple-50/60',
    darkBg: 'from-fuchsia-950/70 to-slate-950',
    ring: 'border-fuchsia-400 dark:border-fuchsia-500',
    glow: 'bg-fuchsia-400/25',
  },
  refill: {
    title: 'Warehouse intelligence',
    bar: 'from-amber-400 to-orange-500',
    lightBg: 'from-amber-50 to-orange-50/60',
    darkBg: 'from-amber-950/60 to-slate-950',
    ring: 'border-amber-400 dark:border-amber-500',
    glow: 'bg-amber-400/25',
  },
  transfer: {
    title: 'Transfer optimizer',
    bar: 'from-rose-400 to-pink-500',
    lightBg: 'from-rose-50 to-pink-50/60',
    darkBg: 'from-rose-950/70 to-slate-950',
    ring: 'border-rose-400 dark:border-rose-500',
    glow: 'bg-rose-400/25',
  },
  done: {
    title: 'Pipeline complete',
    bar: 'from-emerald-400 to-green-500',
    lightBg: 'from-emerald-50 to-green-50/60',
    darkBg: 'from-emerald-950/60 to-slate-950',
    ring: 'border-emerald-400 dark:border-emerald-500',
    glow: 'bg-emerald-400/25',
  },
  failed: {
    title: 'Pipeline failed',
    bar: 'from-rose-500 to-red-600',
    lightBg: 'from-rose-50 to-red-50/60',
    darkBg: 'from-rose-950/70 to-slate-950',
    ring: 'border-rose-400 dark:border-rose-500',
    glow: 'bg-rose-400/25',
  },
  cancelled: {
    title: 'Pipeline stopped',
    bar: 'from-slate-400 to-slate-500',
    lightBg: 'from-slate-50 to-orange-50/40',
    darkBg: 'from-slate-900 to-slate-950',
    ring: 'border-slate-400 dark:border-slate-500',
    glow: 'bg-slate-400/25',
  },
};

function SoftScene({
  phase,
  pct,
  running,
  subtitle,
}: {
  phase: string;
  pct: number;
  running: boolean;
  subtitle?: string;
}) {
  const theme = PHASES[phase] || PHASES.analyze;

  return (
    <div
      key={phase}
      className="relative h-48 w-full overflow-hidden rounded-2xl transition-all duration-700"
    >
      {/* Explicit light / dark backgrounds */}
      <div className={`absolute inset-0 bg-gradient-to-br ${theme.lightBg} dark:hidden`} />
      <div className={`absolute inset-0 hidden bg-gradient-to-br ${theme.darkBg} dark:block`} />

      {/* soft floating blobs */}
      <div
        className={`absolute -left-8 top-4 h-28 w-28 rounded-full blur-2xl ${theme.glow}`}
        style={{ animation: 'soft-float 5s ease-in-out infinite' }}
      />
      <div
        className={`absolute -right-6 bottom-2 h-32 w-32 rounded-full blur-2xl ${theme.glow}`}
        style={{ animation: 'soft-float 6s ease-in-out infinite reverse' }}
      />

      {/* center ring spinner */}
      <div className="absolute inset-0 flex flex-col items-center justify-center px-5 text-center">
        <div className="relative mb-4 h-16 w-16">
          <div className={`absolute inset-0 rounded-full border-[3px] ${theme.ring} opacity-30`} />
          {running ? (
            <div
              className={`absolute inset-0 rounded-full border-[3px] border-transparent border-t-current ${theme.ring}`}
              style={{ animation: 'soft-spin 0.9s linear infinite' }}
            />
          ) : phase === 'done' ? (
            <div className="absolute inset-0 flex items-center justify-center">
              <CheckCircle2 className="h-8 w-8 text-emerald-500" />
            </div>
          ) : phase === 'failed' ? (
            <div className="absolute inset-0 flex items-center justify-center">
              <AlertCircle className="h-8 w-8 text-rose-500" />
            </div>
          ) : phase === 'cancelled' ? (
            <div className="absolute inset-0 flex items-center justify-center">
              <Ban className="h-8 w-8 text-slate-500" />
            </div>
          ) : (
            <div className="absolute inset-0 flex items-center justify-center">
              <Loader2 className="h-7 w-7 animate-spin text-slate-400" />
            </div>
          )}
          {running && (
            <div className="absolute inset-0 flex items-center justify-center">
              <div
                className={`h-8 w-8 rounded-full ${theme.glow}`}
                style={{ animation: 'soft-pulse 1.6s ease-in-out infinite' }}
              />
            </div>
          )}
        </div>

        <p
          key={theme.title}
          className="text-base font-bold tracking-tight text-slate-900 transition-all dark:text-white sm:text-lg"
          style={{ animation: 'soft-in 0.4s ease-out' }}
        >
          {theme.title}
        </p>
        {subtitle ? (
          <p className="mt-1 max-w-[90%] truncate text-[11px] font-medium text-slate-500 dark:text-slate-400">
            {subtitle}
          </p>
        ) : null}
        <p className="mt-2 text-xs font-semibold tabular-nums text-slate-600 dark:text-slate-300">
          {pct}%
        </p>
      </div>

      <div className="absolute inset-x-0 bottom-0 h-1.5 bg-black/5 dark:bg-white/10">
        <div
          className={`h-full bg-gradient-to-r ${theme.bar} transition-all duration-700`}
          style={{ width: `${pct}%` }}
        />
      </div>

      <style>{`
        @keyframes soft-spin { to { transform: rotate(360deg); } }
        @keyframes soft-float {
          0%, 100% { transform: translateY(0); }
          50% { transform: translateY(-10px); }
        }
        @keyframes soft-pulse {
          0%, 100% { opacity: 0.35; transform: scale(0.85); }
          50% { opacity: 0.9; transform: scale(1.15); }
        }
        @keyframes soft-in {
          from { opacity: 0; transform: translateY(6px); }
          to { opacity: 1; transform: translateY(0); }
        }
      `}</style>
    </div>
  );
}

const STATUS_CACHE_KEY = 'stockpulse-pipeline-status';
const VISIBLE_STATUSES = new Set(['running', 'failed', 'cancelled']);

function readCachedStatus(): PipelineStatus | null {
  try {
    const raw = sessionStorage.getItem(STATUS_CACHE_KEY);
    if (!raw) return null;
    const data = JSON.parse(raw) as PipelineStatus;
    return VISIBLE_STATUSES.has(data?.status) ? data : null;
  } catch {
    return null;
  }
}

function writeCachedStatus(data: PipelineStatus) {
  try {
    if (VISIBLE_STATUSES.has(data.status)) {
      sessionStorage.setItem(STATUS_CACHE_KEY, JSON.stringify(data));
    } else {
      sessionStorage.removeItem(STATUS_CACHE_KEY);
    }
  } catch {
    /* ignore */
  }
}

export function PipelineLivePanel() {
  // Instant paint from last known running status when returning to Home
  const [status, setStatus] = useState<PipelineStatus | null>(() => readCachedStatus());
  const [stopping, setStopping] = useState(false);
  const [resuming, setResuming] = useState(false);
  const [stopError, setStopError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const res = await fetch(`${API_URL}/pipeline/status`);
        if (!res.ok) return;
        const data = (await res.json()) as PipelineStatus;
        if (cancelled) return;
        setStatus((prev) => {
          const held =
            prev?.status === 'failed' &&
            typeof prev.error === 'string' &&
            prev.error.includes('Server restarted while the pipeline was still running');
          if (data.status === 'idle' && (prev?.status === 'running' || held)) {
            const stopped: PipelineStatus =
              prev?.status === 'running'
                ? {
                    ...prev,
                    status: 'failed',
                    error:
                      'Server restarted while the pipeline was still running, so this window closed the live progress. ' +
                      (prev.current_label || prev.current_step
                        ? `It stopped around: ${prev.current_label || prev.current_step}. `
                        : '') +
                      'Run the pipeline again.',
                  }
                : (prev as PipelineStatus);
            writeCachedStatus(stopped);
            return stopped;
          }
          writeCachedStatus(data);
          return data;
        });
        if (data.status !== 'running') {
          setStopping(false);
        }
      } catch {
        /* ignore */
      }
    };
    tick();
    const timer = setInterval(tick, 2000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  const handleStop = async () => {
    if (stopping) return;
    setStopError(null);
    setStopping(true);
    try {
      const res = await fetch(`${API_URL}/pipeline/stop`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: 'Stopped from UI' }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setStopError(
          typeof data?.detail === 'string'
            ? data.detail
            : 'Could not stop pipeline'
        );
        setStopping(false);
        return;
      }
      if (data?.job) {
        writeCachedStatus(data.job as PipelineStatus);
        setStatus(data.job as PipelineStatus);
      }
    } catch {
      setStopError('Could not reach backend to stop pipeline');
      setStopping(false);
    }
  };

  const handleResume = async () => {
    if (resuming || !status) return;
    const from =
      status.resume_from ||
      (status.current_step || '').split(' — ')[0]?.trim() ||
      'combine_soh.py';
    const stageRaw = (status.stage || 'all').split('@from:')[0].trim() || 'all';
    setStopError(null);
    setResuming(true);
    try {
      const qs = new URLSearchParams({
        stage: stageRaw,
        start_from: from,
        skip_blob_sync: '1',
      });
      const res = await fetch(`${API_URL}/pipeline/run?${qs}`, { method: 'POST' });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setStopError(
          typeof data?.detail === 'string' ? data.detail : data?.message || 'Could not resume pipeline'
        );
        setResuming(false);
        return;
      }
      if (data?.job) {
        writeCachedStatus(data.job as PipelineStatus);
        setStatus(data.job as PipelineStatus);
      }
    } catch {
      setStopError('Could not reach backend to resume pipeline');
    } finally {
      setResuming(false);
    }
  };

  // Show while running; keep failed/cancelled visible so it doesn't vanish
  if (!status || !VISIBLE_STATUSES.has(status.status)) {
    return null;
  }

  const phase = resolveDisplayPhase(status);
  const pct = Math.min(100, Math.max(0, status.progress_pct ?? 0));
  const running = status.status === 'running';
  const stepLabel = (status.current_label || status.current_step || '').trim();

  return (
    <div className="mb-6 overflow-hidden rounded-2xl border border-slate-200/90 bg-white shadow-xl shadow-slate-200/40 dark:border-slate-700 dark:bg-slate-900 dark:shadow-none">
      <div className="px-3 pb-3 pt-3 sm:px-4 sm:pb-4 sm:pt-4">
        <SoftScene phase={phase} pct={pct} running={running} subtitle={stepLabel || undefined} />

        {running ? (
          <div className="mt-3 flex flex-col items-stretch gap-2 sm:flex-row sm:items-center sm:justify-between">
            <p className="px-1 text-xs text-slate-500 dark:text-slate-400">
              Stop ends the current step and skips the rest of the run.
            </p>
            <button
              type="button"
              onClick={handleStop}
              disabled={stopping}
              className="inline-flex items-center justify-center gap-2 rounded-xl border border-rose-300 bg-rose-50 px-4 py-2 text-sm font-semibold text-rose-700 transition hover:bg-rose-100 disabled:cursor-not-allowed disabled:opacity-60 dark:border-rose-700 dark:bg-rose-950/50 dark:text-rose-300 dark:hover:bg-rose-900/60"
            >
              {stopping ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Square className="h-3.5 w-3.5 fill-current" />
              )}
              {stopping ? 'Stopping…' : 'Stop pipeline'}
            </button>
          </div>
        ) : null}

        {stopError ? (
          <p className="mt-2 px-1 text-xs text-rose-600 dark:text-rose-400">{stopError}</p>
        ) : null}

        {(status.status === 'failed' || status.status === 'cancelled') && status.error ? (
          <div className="mt-2 space-y-2 px-1">
            <p className="line-clamp-3 text-xs text-rose-600 dark:text-rose-400">
              {status.error.split('\n')[0]}
            </p>
            {(status.can_resume ||
              status.resume_from ||
              /Server restarted while the pipeline/i.test(status.error || '')) && (
              <button
                type="button"
                onClick={handleResume}
                disabled={resuming}
                className="inline-flex items-center justify-center gap-2 rounded-xl border border-emerald-300 bg-emerald-50 px-4 py-2 text-sm font-semibold text-emerald-800 transition hover:bg-emerald-100 disabled:cursor-not-allowed disabled:opacity-60 dark:border-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-300 dark:hover:bg-emerald-900/60"
              >
                {resuming ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <RotateCcw className="h-3.5 w-3.5" />
                )}
                {resuming
                  ? 'Resuming…'
                  : `Resume from ${status.resume_from || 'last step'}`}
              </button>
            )}
          </div>
        ) : null}
      </div>
    </div>
  );
}

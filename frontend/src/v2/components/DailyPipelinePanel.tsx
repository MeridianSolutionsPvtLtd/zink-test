import React, { useCallback, useEffect, useState } from 'react';
import {
  AlertCircle,
  CalendarClock,
  CheckCircle2,
  Clock,
  Loader2,
  Play,
  SkipForward,
  XCircle,
} from 'lucide-react';
import { API_URL } from '../../config';

type StepStatus = 'pending' | 'running' | 'done' | 'failed' | 'skipped';

type DailyStep = {
  id: string;
  label: string;
  status: StepStatus;
  detail?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
};

type DailyRunStatus = {
  running?: boolean;
  trigger?: string;
  started_at?: string | null;
  finished_at?: string | null;
  progress_pct?: number;
  current_phase?: string;
  current_message?: string;
  steps?: DailyStep[];
  result?: { ok?: boolean; error?: string; warnings?: string[] } | null;
  scheduler?: {
    enabled?: boolean;
    schedule?: string;
    timezone?: string;
    last_result_ok?: boolean | null;
  };
};

function StepIcon({ status }: { status: StepStatus }) {
  if (status === 'running') return <Loader2 className="h-4 w-4 shrink-0 animate-spin text-indigo-600" />;
  if (status === 'done') return <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600" />;
  if (status === 'failed') return <XCircle className="h-4 w-4 shrink-0 text-rose-500" />;
  if (status === 'skipped') return <SkipForward className="h-4 w-4 shrink-0 text-slate-400" />;
  return <Clock className="h-4 w-4 shrink-0 text-slate-300" />;
}

function stepStatusLabel(status: StepStatus) {
  if (status === 'running') return 'Running';
  if (status === 'done') return 'Done';
  if (status === 'failed') return 'Failed';
  if (status === 'skipped') return 'Skipped';
  return 'Pending';
}

export function DailyPipelinePanel() {
  const [status, setStatus] = useState<DailyRunStatus | null>(null);
  const [triggering, setTriggering] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadStatus = useCallback(async () => {
    try {
      const res = await fetch(`${API_URL}/pipeline/daily-run/status`);
      const data = (await res.json()) as DailyRunStatus;
      if (!res.ok) throw new Error(String((data as { detail?: string }).detail || 'Status failed'));
      setStatus(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load daily pipeline status');
    }
  }, []);

  useEffect(() => {
    void loadStatus();
    const id = window.setInterval(() => void loadStatus(), status?.running ? 2000 : 8000);
    return () => window.clearInterval(id);
  }, [loadStatus, status?.running]);

  const handleTrigger = async () => {
    setTriggering(true);
    setError(null);
    try {
      const res = await fetch(`${API_URL}/pipeline/daily-run/trigger`, { method: 'POST' });
      const data = (await res.json()) as { detail?: string; error?: string };
      if (!res.ok) throw new Error(data.detail || data.error || 'Trigger failed');
      await loadStatus();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Trigger failed');
    } finally {
      setTriggering(false);
    }
  };

  const running = Boolean(status?.running);
  const pct = Math.min(100, Math.max(0, Number(status?.progress_pct ?? 0)));
  const schedule = status?.scheduler?.schedule || '09:00';
  const tz = status?.scheduler?.timezone || 'Asia/Kolkata';
  const steps = status?.steps || [];
  const lastOk = status?.result?.ok ?? status?.scheduler?.last_result_ok;

  return (
    <div className="mb-5 overflow-hidden rounded-2xl border border-violet-200/90 bg-white shadow-xl shadow-violet-200/30 dark:border-violet-500/30 dark:bg-slate-900 dark:shadow-none">
      <div className="flex flex-col gap-4 border-b border-violet-100 bg-gradient-to-r from-violet-50/90 to-indigo-50/60 px-5 py-4 sm:flex-row sm:items-center sm:justify-between dark:border-violet-500/20 dark:from-violet-950/40 dark:to-indigo-950/30">
        <div className="flex items-start gap-3">
          <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-violet-100 text-violet-700 dark:bg-violet-500/15 dark:text-violet-300">
            <CalendarClock className="h-5 w-5" />
          </div>
          <div>
            <p className="text-[11px] font-bold uppercase tracking-widest text-violet-500/80">Daily auto job</p>
            <h2 className="text-base font-bold text-slate-900 dark:text-white">
              Scheduled {schedule} {tz.includes('Kolkata') ? 'IST' : ''}
            </h2>
            <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
              Auto: fetch → merge → combine. Historical SOH rebuild skipped (already built). Refill / prediction → Manual Upload → Run Pipeline.
            </p>
          </div>
        </div>
        <button
          type="button"
          disabled={running || triggering}
          onClick={() => void handleTrigger()}
          className="inline-flex shrink-0 items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-violet-600 to-indigo-600 px-5 py-2.5 text-sm font-semibold text-white shadow-md shadow-violet-500/20 transition hover:from-violet-500 hover:to-indigo-500 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {running || triggering ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Play className="h-4 w-4" />
          )}
          {running ? 'Running now…' : 'Run daily combine now (test)'}
        </button>
      </div>

      <div className="px-5 py-4">
        {error && (
          <div className="mb-4 flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-300">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            {error}
          </div>
        )}

        {(running || pct > 0) && (
          <div className="mb-4">
            <div className="mb-2 flex items-center justify-between gap-3">
              <p className="text-sm font-semibold text-slate-800 dark:text-slate-100">
                {running ? status?.current_message || 'Daily pipeline running…' : 'Last run'}
              </p>
              <span className="text-sm font-bold tabular-nums text-violet-700 dark:text-violet-300">{pct}%</span>
            </div>
            <div className="h-2.5 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-700">
              <div
                className="h-full rounded-full bg-gradient-to-r from-violet-500 to-indigo-500 transition-all duration-500"
                style={{ width: `${Math.max(running ? 2 : 0, pct)}%` }}
              />
            </div>
          </div>
        )}

        {!running && lastOk != null && (
          <div
            className={`mb-4 flex items-center gap-2 rounded-xl px-3 py-2 text-sm font-medium ${
              lastOk
                ? 'border border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-200'
                : 'border border-rose-200 bg-rose-50 text-rose-800 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200'
            }`}
          >
            {lastOk ? <CheckCircle2 className="h-4 w-4" /> : <XCircle className="h-4 w-4" />}
            {lastOk ? 'Last daily run succeeded' : `Last run failed: ${status?.result?.error || 'see steps'}`}
          </div>
        )}

        <ul className="space-y-2">
          {steps.map((step) => (
            <li
              key={step.id}
              className={`rounded-xl border px-3 py-2.5 ${
                step.status === 'running'
                  ? 'border-indigo-300 bg-indigo-50/80 dark:border-indigo-500/40 dark:bg-indigo-500/10'
                  : step.status === 'done'
                    ? 'border-emerald-200/80 bg-emerald-50/40 dark:border-emerald-500/20 dark:bg-emerald-500/5'
                    : step.status === 'failed'
                      ? 'border-rose-200 bg-rose-50/50 dark:border-rose-500/30 dark:bg-rose-500/10'
                      : 'border-slate-200/80 bg-slate-50/50 dark:border-slate-700 dark:bg-slate-800/40'
              }`}
            >
              <div className="flex items-start gap-2.5">
                <StepIcon status={step.status} />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <p className="text-sm font-semibold text-slate-800 dark:text-slate-100">{step.label}</p>
                    <span className="text-[10px] font-bold uppercase tracking-wide text-slate-400">
                      {stepStatusLabel(step.status)}
                    </span>
                  </div>
                  {step.detail ? (
                    <p className="mt-0.5 text-xs leading-relaxed text-slate-600 dark:text-slate-400">{step.detail}</p>
                  ) : null}
                </div>
              </div>
            </li>
          ))}
        </ul>

        {!!status?.result?.warnings?.length && (
          <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100">
            <p className="mb-1 font-semibold">Warnings</p>
            <ul className="list-inside list-disc space-y-0.5">
              {status.result.warnings.slice(0, 8).map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          </div>
        )}

        <p className="mt-4 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2.5 text-xs leading-relaxed text-slate-600 dark:border-slate-700 dark:bg-slate-800/60 dark:text-slate-400">
          <span className="font-semibold text-slate-800 dark:text-slate-200">Historical weekly/monthly SOH</span> is not
          rebuilt on the daily job — it already lives under{' '}
          <span className="font-mono text-[11px]">output_files/historical_soh/</span>. Only masters + COMBINED_SOH refresh
          here.
          <br />
          <span className="font-semibold text-slate-800 dark:text-slate-200">Refill / prediction / transfer</span> is not
          part of the daily job either. After combine finishes, go to{' '}
          <span className="font-semibold">Manual Upload → Run Pipeline</span> when you want replenishment.
        </p>
      </div>
    </div>
  );
}

import React, { useCallback, useMemo, useRef, useState } from 'react';
import {
  Upload,
  FolderOpen,
  FileText,
  X,
  CheckCircle2,
  CloudUpload,
  Paperclip,
  AlertCircle,
  AlertTriangle,
  Store,
  Loader2,
  Clock,
  Play,
  Sparkles,
} from 'lucide-react';
import { API_URL } from '../../config';
import { PipelineLivePanel } from './PipelineLivePanel';

/** ids must match backend CATEGORY_FOLDER_MAP → Blob input/<Folder>/ */
const DESTINATIONS = [
  { id: 'sales-master-pt-ssl-centro-iconic', label: 'Sales-Master (PT, SSL, Centro, Iconic, EBO)' },
  { id: 'sales-pt-ssl-centro-iconic', label: 'Sales (PT, SSL, Centro, Iconic, EBO)' },
  { id: 'ebo-sales', label: 'EBO Sales' },
  { id: 'pantaloons-soh', label: 'Pantaloons - SOH' },
  { id: 'shoppers-stop-soh', label: 'Shoppers Stop - SOH' },
  { id: 'iconic-soh', label: 'Iconic - SOH' },
  { id: 'ebo-soh', label: 'EBO - SOH' },
  { id: 'centro-soh', label: 'Centro - SOH' },
  { id: 'warehouse-soh', label: 'Warehouse SOH' },
  { id: 'soh-master', label: 'SOH-Master' },
  { id: 'transfer-ebo', label: 'Transfer - EBO' },
  { id: 'transfer-master-pt-ssl-centro-iconic', label: 'Transfer-Master (PT, SSL, Centro, Iconic)' },
  { id: 'transfer-pt-ssl-centro-iconic', label: 'Transfer - (PT, SSL, Centro, Iconic)' },
  { id: 'item-master', label: 'Item Master' },
  { id: 'customer-master', label: 'Customer Master' },
  { id: 'financial-week-info', label: 'Financial Week Info (Date / FY / Week)' },
  { id: 'store-grade-list', label: 'Store Grade List' },
  { id: 'top-sellers-list', label: 'Top Sellers List' },
  { id: 'sku-remap', label: 'SKU Remap (OLD → NEW)' },
  { id: 'target-lfs', label: 'Target (LFS)' },
  { id: 'rtv-data', label: 'RTV Data' },
  { id: 'ibt-data', label: 'IBT Data' },
  { id: 'grc-report', label: 'GRC Report' },
] as const;

/** 5 store channels for post-upload replenishment selection */
const REPLENISHMENT_STORES = [
  { id: 'pantaloons', label: 'Pantaloons (PT)' },
  { id: 'shoppers-stop', label: 'Shoppers Stop (SSL)' },
  { id: 'centro', label: 'Centro' },
  { id: 'iconic', label: 'Iconic' },
  { id: 'ebo', label: 'EBO' },
] as const;

type ReplenishmentStoreId = (typeof REPLENISHMENT_STORES)[number]['id'];

type WarehouseMode = 'incremental' | 'fresh';

type DestinationId = (typeof DESTINATIONS)[number]['id'];

type FilesByDestination = Record<DestinationId, File[]>;

type FileJobStatus = 'waiting' | 'uploading' | 'merging' | 'done' | 'error';

type UploadJob = {
  key: string;
  destId: DestinationId;
  categoryLabel: string;
  fileName: string;
  file: File;
  status: FileJobStatus;
  detail?: string;
  mergePct?: number;
  mergeMessage?: string;
};

const EMPTY_FILES: FilesByDestination = Object.fromEntries(
  DESTINATIONS.map((d) => [d.id, [] as File[]])
) as FilesByDestination;

function formatFileSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function getRelativePath(file: File) {
  const path = (file as File & { webkitRelativePath?: string }).webkitRelativePath;
  return path || file.name;
}

function fileKey(file: File) {
  return `${getRelativePath(file)}|${file.size}|${file.lastModified}`;
}

function jobKey(destId: DestinationId, file: File) {
  return `${destId}|${fileKey(file)}`;
}

function parseUploadError(data: Record<string, unknown> | null, status: number): string {
  let message = `Upload failed (${status})`;
  if (!data) return message;
  const detail = (data.detail ?? data.message ?? data.error ?? message) as unknown;
  if (typeof detail === 'string') {
    message = detail;
  } else if (Array.isArray(detail)) {
    message = detail.map((m: { msg?: string }) => m.msg || JSON.stringify(m)).join(', ');
  } else if (detail && typeof detail === 'object') {
    const detailObj = detail as { errors?: unknown; warnings?: unknown };
    const errs = detailObj.errors;
    const warns = detailObj.warnings;
    if (Array.isArray(errs) && errs.length) {
      message = errs.map(String).join(' | ');
    } else if (Array.isArray(warns) && warns.length) {
      message = warns.map(String).join(' | ');
    } else {
      message = JSON.stringify(detail);
    }
    const blob = message.toLowerCase();
    if (blob.includes('column') && !blob.includes('check the column')) {
      message = `Check the column name. ${message}`;
    }
  }
  if (status === 404) {
    message =
      'Upload API not found on backend (POST /api/upload). Backend is connected — add this endpoint to accept files.';
  }
  if (status === 502 || status === 504) {
    message =
      'Server timed out while merging files. Retry upload — merge now runs in the background after the file reaches storage.';
  }
  return message;
}

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function pollMergeUntilDone(
  jobId: string,
  maxWaitMs = 45 * 60 * 1000,
  onProgress?: (pct: number, message: string) => void
): Promise<Record<string, unknown>> {
  const started = Date.now();
  let delay = 800;
  while (Date.now() - started < maxWaitMs) {
    const res = await fetch(`${API_URL}/upload/merge-status?job_id=${encodeURIComponent(jobId)}`);
    let data: Record<string, unknown> | null = null;
    try {
      data = (await res.json()) as Record<string, unknown>;
    } catch {
      data = null;
    }
    if (!res.ok) {
      const msg =
        typeof data?.detail === 'string'
          ? data.detail
          : `Merge status check failed (${res.status})`;
      throw new Error(msg);
    }
    const pct = Number(data?.progress_pct ?? 0);
    const message =
      (typeof data?.progress_message === 'string' && data.progress_message) || 'Merging…';
    if (onProgress) onProgress(Number.isFinite(pct) ? pct : 0, message);

    const status = String(data?.status ?? '');
    if (status === 'success') {
      if (onProgress) onProgress(100, 'Done');
      return data;
    }
    if (status === 'failed') {
      const err =
        (typeof data?.error === 'string' && data.error) ||
        'Merge failed. Check backend logs for details.';
      throw new Error(err);
    }
    await sleep(delay);
    delay = Math.min(Math.round(delay * 1.25), 2500);
  }

  // UI wait limit hit — merge may still be running server-side (large transfer Excel).
  try {
    const res = await fetch(`${API_URL}/upload/merge-status?job_id=${encodeURIComponent(jobId)}`);
    const data = (await res.json()) as Record<string, unknown>;
    const status = String(data?.status ?? '');
    if (res.ok && (status === 'running' || status === 'pending')) {
      const pct = Number(data?.progress_pct ?? 0);
      const message =
        (typeof data?.progress_message === 'string' && data.progress_message) ||
        'Still merging in background…';
      if (onProgress) onProgress(Number.isFinite(pct) ? pct : 0, message);
      return {
        ...data,
        background_still_running: true,
        warnings: [
          `Merge still running in background (${Number.isFinite(pct) ? pct : '?'}%). Large transfer files can take 15–30 minutes — refresh this page in a few minutes to confirm.`,
        ],
      };
    }
    if (res.ok && status === 'success') {
      if (onProgress) onProgress(100, 'Done');
      return data;
    }
  } catch {
    /* fall through */
  }

  throw new Error(
    'Merge timed out after 45 minutes. Check backend logs — the file may still be processing.'
  );
}

function collectWarnings(data: Record<string, unknown> | null): string[] {
  if (!data) return [];
  const warnings = Array.isArray(data.warnings)
    ? (data.warnings as unknown[]).map(String).filter(Boolean)
    : [];
  const mergeWarnings =
    data.merge &&
    typeof data.merge === 'object' &&
    Array.isArray((data.merge as { warnings?: unknown }).warnings)
      ? ((data.merge as { warnings: unknown[] }).warnings).map(String).filter(Boolean)
      : [];
  return [...new Set([...warnings, ...mergeWarnings])];
}

function FileStatusIcon({ status }: { status?: FileJobStatus }) {
  if (!status || status === 'waiting') {
    return <Clock className="h-3.5 w-3.5 shrink-0 text-slate-400" aria-hidden />;
  }
  if (status === 'uploading') {
    return <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-teal-600" aria-hidden />;
  }
  if (status === 'merging') {
    return <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-indigo-600" aria-hidden />;
  }
  if (status === 'done') {
    return <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-emerald-600" aria-hidden />;
  }
  return <AlertCircle className="h-3.5 w-3.5 shrink-0 text-rose-500" aria-hidden />;
}

function statusLabel(status: FileJobStatus, mergePct?: number): string {
  if (status === 'waiting') return 'Waiting';
  if (status === 'uploading') return 'Uploading…';
  if (status === 'merging') {
    if (typeof mergePct === 'number' && mergePct > 0) return `Merging ${mergePct}%`;
    return 'Merging…';
  }
  if (status === 'done') return 'Done';
  return 'Failed';
}

interface CategoryRowProps {
  key?: React.Key;
  index: number;
  id: DestinationId;
  label: string;
  files: File[];
  isDragging: boolean;
  fileStatuses?: Record<string, FileJobStatus>;
  uploading?: boolean;
  onDragOver: (e: React.DragEvent) => void;
  onDragLeave: (e: React.DragEvent) => void;
  onDrop: (e: React.DragEvent) => void;
  onAddFiles: (files: FileList | File[]) => void;
  onRemoveFile: (index: number) => void;
  onClear: () => void;
}

function CategoryRow({
  index,
  id,
  label,
  files,
  isDragging,
  fileStatuses,
  uploading,
  onDragOver,
  onDragLeave,
  onDrop,
  onAddFiles,
  onRemoveFile,
  onClear,
}: CategoryRowProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);
  const hasFiles = files.length > 0;

  return (
    <div
      className={`group relative border-b border-slate-100/80 transition-colors last:border-b-0 dark:border-slate-800/80 ${
        isDragging
          ? 'bg-emerald-50/70 dark:bg-emerald-500/10'
          : hasFiles
            ? 'bg-white dark:bg-slate-900'
            : index % 2 === 0
              ? 'bg-slate-50/40 dark:bg-slate-900/40'
              : 'bg-white dark:bg-slate-900/80'
      }`}
    >
      {/* Left accent */}
      <div
        className={`absolute inset-y-0 left-0 w-1 transition-colors ${
          isDragging
            ? 'bg-emerald-500'
            : hasFiles
              ? 'bg-emerald-400/80'
              : 'bg-transparent group-hover:bg-slate-300 dark:group-hover:bg-slate-600'
        }`}
      />

      <div className="flex flex-col gap-3 py-4 pl-5 pr-4 sm:flex-row sm:items-center sm:gap-5 sm:pl-6 sm:pr-5">
        {/* Category label */}
        <div className="flex shrink-0 items-center gap-3 sm:w-60">
          <span className="hidden h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-slate-200/80 text-[11px] font-bold tabular-nums text-slate-500 dark:bg-slate-700 dark:text-slate-400 sm:inline-flex">
            {String(index + 1).padStart(2, '0')}
          </span>
          <div className="min-w-0">
            <p className="text-sm font-semibold leading-snug text-slate-900 dark:text-white">{label}</p>
            {hasFiles ? (
              <p className="mt-0.5 text-[11px] font-medium text-emerald-600 dark:text-emerald-400">
                {files.length} file{files.length !== 1 ? 's' : ''} attached
              </p>
            ) : (
              <p className="mt-0.5 text-[11px] text-slate-400 dark:text-slate-500">No files yet</p>
            )}
          </div>
        </div>

        {/* Drop zone bar */}
        <div
          onDragOver={onDragOver}
          onDragLeave={onDragLeave}
          onDrop={onDrop}
          className={`flex min-h-[72px] flex-1 flex-col items-center justify-center gap-3 rounded-xl border border-dashed px-4 py-3 transition-all duration-200 sm:flex-row sm:justify-between sm:gap-4 ${
            isDragging
              ? 'scale-[1.01] border-emerald-500 bg-emerald-100/80 shadow-lg shadow-emerald-500/10 dark:bg-emerald-500/15'
              : hasFiles
                ? 'border-emerald-300/70 bg-emerald-50/50 dark:border-emerald-500/30 dark:bg-emerald-500/5'
                : 'border-slate-200 bg-white/80 hover:border-emerald-400/60 hover:bg-emerald-50/30 dark:border-slate-600 dark:bg-slate-800/50 dark:hover:border-emerald-500/40'
          }`}
        >
          <div className="flex items-center gap-3">
            <div
              className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-xl transition-all ${
                isDragging
                  ? 'bg-emerald-500 text-white shadow-md shadow-emerald-500/30'
                  : hasFiles
                    ? 'bg-emerald-100 text-emerald-600 dark:bg-emerald-500/20 dark:text-emerald-400'
                    : 'bg-slate-100 text-slate-400 dark:bg-slate-700 dark:text-slate-400'
              }`}
            >
              {hasFiles && !isDragging ? (
                <Paperclip className="h-5 w-5" strokeWidth={1.75} />
              ) : (
                <Upload className="h-5 w-5" strokeWidth={1.75} />
              )}
            </div>
            <div className="text-left">
              <p className="text-sm font-semibold text-slate-800 dark:text-slate-100">
                {isDragging ? 'Release to add files' : 'Drag & drop files here'}
              </p>
              <p className="text-xs text-slate-500 dark:text-slate-400">
                {isDragging ? `Adding to ${label}` : 'or use browse buttons'}
              </p>
            </div>
          </div>

          <div className="flex shrink-0 gap-2">
            <button
              type="button"
              disabled={uploading}
              onClick={() => fileInputRef.current?.click()}
              className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-600 px-3.5 py-2 text-xs font-semibold text-white shadow-sm shadow-emerald-600/20 transition hover:bg-emerald-700 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50"
            >
              <FileText className="h-3.5 w-3.5" />
              Files
            </button>
            <button
              type="button"
              disabled={uploading}
              onClick={() => folderInputRef.current?.click()}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3.5 py-2 text-xs font-semibold text-slate-700 shadow-sm transition hover:border-slate-300 hover:bg-slate-50 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700"
            >
              <FolderOpen className="h-3.5 w-3.5" />
              Folder
            </button>
          </div>

          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept=".xlsx,.xls,.csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.ms-excel,text/csv"
            className="hidden"
            onChange={(e) => {
              if (e.target.files) onAddFiles(e.target.files);
              e.target.value = '';
            }}
          />
          <input
            ref={folderInputRef}
            type="file"
            multiple
            accept=".xlsx,.xls,.csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.ms-excel,text/csv"
            className="hidden"
            {...({ webkitdirectory: '', directory: '' } as React.InputHTMLAttributes<HTMLInputElement>)}
            onChange={(e) => {
              if (e.target.files) onAddFiles(e.target.files);
              e.target.value = '';
            }}
          />
        </div>
      </div>

      {/* Selected files */}
      {hasFiles && (
        <div className="border-t border-emerald-100/80 bg-gradient-to-b from-emerald-50/40 to-transparent px-4 pb-4 pt-3 dark:border-emerald-500/10 dark:from-emerald-500/5 sm:pl-[17.5rem] sm:pr-5">
          <div className="mb-2.5 flex items-center justify-between">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">
              {files.length} selected · {formatFileSize(files.reduce((s, f) => s + f.size, 0))}
            </span>
            <button
              type="button"
              disabled={uploading}
              onClick={onClear}
              className="text-[11px] font-semibold text-slate-400 transition hover:text-rose-500 disabled:opacity-40"
            >
              Clear all
            </button>
          </div>
          <ul className="flex flex-col gap-1.5">
            {files.map((file, fileIndex) => {
              const key = fileKey(file);
              const status = fileStatuses?.[`${id}|${key}`] ?? fileStatuses?.[key];
              return (
                <li
                  key={`${id}-${key}-${fileIndex}`}
                  className={`flex items-center gap-3 rounded-lg border px-3 py-2 shadow-sm transition dark:bg-slate-800/80 ${
                    status === 'uploading'
                      ? 'border-teal-300 bg-teal-50/80 dark:border-teal-500/40 dark:bg-teal-500/10'
                      : status === 'merging'
                        ? 'border-indigo-300 bg-indigo-50/80 dark:border-indigo-500/40 dark:bg-indigo-500/10'
                        : status === 'done'
                        ? 'border-emerald-300 bg-emerald-50/70 dark:border-emerald-500/30 dark:bg-emerald-500/10'
                        : status === 'error'
                          ? 'border-rose-300 bg-rose-50/70 dark:border-rose-500/30 dark:bg-rose-500/10'
                          : 'border-slate-200/80 bg-white hover:border-slate-300 dark:border-slate-700 dark:hover:border-slate-600'
                  }`}
                >
                  <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-slate-100 dark:bg-slate-700">
                    <FileStatusIcon status={status} />
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-slate-800 dark:text-slate-200">
                      {getRelativePath(file)}
                    </p>
                    <p className="text-[11px] text-slate-500">
                      {formatFileSize(file.size)}
                      {status ? ` · ${statusLabel(status)}` : ''}
                    </p>
                  </div>
                  {!uploading && (
                    <button
                      type="button"
                      onClick={() => onRemoveFile(fileIndex)}
                      className="rounded-md p-1.5 text-slate-400 transition hover:bg-rose-50 hover:text-rose-500 dark:hover:bg-rose-500/10"
                      aria-label={`Remove ${getRelativePath(file)}`}
                    >
                      <X className="h-4 w-4" />
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}

export function FileUploadPanel() {
  const [filesByDest, setFilesByDest] = useState<FilesByDestination>({ ...EMPTY_FILES });
  const [draggingDest, setDraggingDest] = useState<DestinationId | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadDone, setUploadDone] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploadWarning, setUploadWarning] = useState<string | null>(null);
  const [uploadJobs, setUploadJobs] = useState<UploadJob[]>([]);
  const [showStoreModal, setShowStoreModal] = useState(false);
  const [storeModalMode, setStoreModalMode] = useState<'after-upload' | 'run-only'>('after-upload');
  const [selectedStores, setSelectedStores] = useState<ReplenishmentStoreId[]>([]);
  const [savingStores, setSavingStores] = useState(false);
  const [storeSaveError, setStoreSaveError] = useState<string | null>(null);
  const [storeSaveDone, setStoreSaveDone] = useState(false);
  const [warehouseMode, setWarehouseMode] = useState<WarehouseMode>('incremental');
  const [remapStarting, setRemapStarting] = useState(false);
  const [remapError, setRemapError] = useState<string | null>(null);
  const [showRemapModal, setShowRemapModal] = useState(false);
  const [remapWarehouseMode, setRemapWarehouseMode] = useState<WarehouseMode>('incremental');

  const fileStatusMap = useMemo(() => {
    const map: Record<string, FileJobStatus> = {};
    for (const job of uploadJobs) {
      // CategoryRow looks up by plain fileKey within that row's files
      map[`${job.destId}|${fileKey(job.file)}`] = job.status;
      map[fileKey(job.file)] = job.status;
    }
    return map;
  }, [uploadJobs]);

  const completedUploadSummary = useMemo(() => {
    if (!uploadDone || !uploadJobs.length) return null;
    const categoryIds = new Set(uploadJobs.map((j) => j.destId));
    return {
      totalFiles: uploadJobs.length,
      categoriesCount: categoryIds.size,
    };
  }, [uploadDone, uploadJobs]);

  const progressSummary = useMemo(() => {
    if (!uploadJobs.length) return null;
    const done = uploadJobs.filter((j) => j.status === 'done').length;
    const failed = uploadJobs.filter((j) => j.status === 'error').length;
    const active =
      uploadJobs.find((j) => j.status === 'uploading') ??
      uploadJobs.find((j) => j.status === 'merging');
    const mergingCount = uploadJobs.filter((j) => j.status === 'merging').length;
    const mergePct = active?.status === 'merging' ? Number(active.mergePct ?? 0) : null;
    const mergeMessage =
      active?.status === 'merging' ? active.mergeMessage || 'Merging…' : null;
    const overall = Math.round(
      ((done +
        (active?.status === 'merging' ? Math.min(99, Math.max(0, mergePct ?? 0)) / 100 : 0) +
        (active?.status === 'uploading' ? 0.15 : 0)) /
        Math.max(uploadJobs.length, 1)) *
        100
    );
    return {
      total: uploadJobs.length,
      done,
      failed,
      active,
      mergingCount,
      mergePct,
      mergeMessage,
      overall,
    };
  }, [uploadJobs]);

  const addFilesToDest = useCallback((dest: DestinationId, incoming: FileList | File[]) => {
    const list = Array.from(incoming);
    if (!list.length) return;
    setFilesByDest((prev) => {
      const existing = new Set(prev[dest].map(fileKey));
      const merged = [...prev[dest]];
      for (const file of list) {
        const key = fileKey(file);
        if (!existing.has(key)) {
          existing.add(key);
          merged.push(file);
        }
      }
      return { ...prev, [dest]: merged };
    });
    setUploadDone(false);
    setUploadError(null);
    setUploadJobs([]);
    setStoreSaveDone(false);
  }, []);

  const removeFile = (dest: DestinationId, index: number) => {
    setFilesByDest((prev) => ({
      ...prev,
      [dest]: prev[dest].filter((_, i) => i !== index),
    }));
    setUploadDone(false);
    setUploadError(null);
    setUploadJobs([]);
  };

  const clearCategory = (dest: DestinationId) => {
    setFilesByDest((prev) => ({ ...prev, [dest]: [] }));
    setUploadDone(false);
    setUploadError(null);
    setUploadJobs([]);
  };

  const clearAllFiles = useCallback(() => {
    if (uploading) return;
    setFilesByDest({ ...EMPTY_FILES });
    setUploadDone(false);
    setUploadError(null);
    setUploadWarning(null);
    setUploadJobs([]);
    setStoreSaveDone(false);
  }, [uploading]);

  const handleRemapReplenish = async (mode: WarehouseMode = remapWarehouseMode) => {
    setRemapStarting(true);
    setRemapError(null);
    try {
      const qs = new URLSearchParams({
        stage: 'all',
        start_from: '6.build_refill.py',
        warehouse_mode: mode,
        sku_scope: 'remapped',
        skip_blob_sync: '0',
      });
      const res = await fetch(`${API_URL}/pipeline/run?${qs.toString()}`, { method: 'POST' });
      const data = (await res.json().catch(() => ({}))) as { detail?: string; message?: string };
      if (!res.ok) {
        throw new Error(
          typeof data.detail === 'string'
            ? data.detail
            : 'Could not start remapped-SKU replenishment'
        );
      }
      setShowRemapModal(false);
    } catch (err) {
      setRemapError(err instanceof Error ? err.message : 'Could not start remapped-SKU replenishment');
    } finally {
      setRemapStarting(false);
    }
  };

  const toggleStore = (id: ReplenishmentStoreId) => {
    setSelectedStores((prev) =>
      prev.includes(id) ? prev.filter((s) => s !== id) : [...prev, id]
    );
    setStoreSaveError(null);
  };

  const selectAllStores = () => {
    setSelectedStores(REPLENISHMENT_STORES.map((s) => s.id));
    setStoreSaveError(null);
  };

  const clearStores = () => {
    setSelectedStores([]);
    setStoreSaveError(null);
  };

  const handleConfirmStores = async () => {
    if (!selectedStores.length) {
      setStoreSaveError('Select at least one store for replenishment');
      return;
    }
    setSavingStores(true);
    setStoreSaveError(null);
    try {
      const res = await fetch(`${API_URL}/upload/replenishment-stores`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          stores: selectedStores,
          start_pipeline: true,
          warehouse_mode: warehouseMode,
          skip_blob_sync: false,
        }),
      });
      if (!res.ok) {
        let message = `Save failed (${res.status})`;
        try {
          const data = await res.json();
          message = data.detail || data.message || message;
        } catch {
          /* ignore */
        }
        throw new Error(typeof message === 'string' ? message : 'Save failed');
      }
      const data = await res.json().catch(() => ({}));
      localStorage.setItem(
        'stockpulse-replenishment-stores',
        JSON.stringify({
          stores: selectedStores,
          store_types: data.store_types,
          path: data.path,
          savedAt: new Date().toISOString(),
          pipeline_started: data.pipeline_started,
          warehouse_mode: data.warehouse_mode ?? warehouseMode,
        })
      );
      setStoreSaveDone(true);
      setShowStoreModal(false);
    } catch (err: unknown) {
      setStoreSaveError(err instanceof Error ? err.message : 'Could not save store selection');
    } finally {
      setSavingStores(false);
    }
  };

  const handleUpload = async () => {
    const entries = DESTINATIONS.filter((d) => filesByDest[d.id].length > 0);
    if (!entries.length) return;

    const jobs: UploadJob[] = [];
    for (const dest of entries) {
      for (const file of filesByDest[dest.id]) {
        jobs.push({
          key: jobKey(dest.id, file),
          destId: dest.id,
          categoryLabel: dest.label,
          fileName: getRelativePath(file),
          file,
          status: 'waiting',
        });
      }
    }

    setUploading(true);
    setUploadError(null);
    setUploadWarning(null);
    setUploadDone(false);
    setStoreSaveDone(false);
    setUploadJobs(jobs);

    const allWarnings: string[] = [];

    try {
      for (let i = 0; i < jobs.length; i++) {
        const job = jobs[i];
        const isLast = i === jobs.length - 1;

        setUploadJobs((prev) =>
          prev.map((j) => (j.key === job.key ? { ...j, status: 'uploading' as const } : j))
        );

        const formData = new FormData();
        formData.append('categories', job.destId);
        formData.append('files', job.file, job.file.name);
        formData.append('file_categories', job.destId);

        // Intermediate files skip full local sync; last file syncs everything once.
        const res = await fetch(`${API_URL}/upload?sync_local=${isLast ? 1 : 0}&wait_merge=0`, {
          method: 'POST',
          body: formData,
        });

        let data: Record<string, unknown> | null = null;
        try {
          data = (await res.json()) as Record<string, unknown>;
        } catch {
          data = null;
        }

        const accepted = res.status === 202 || res.ok;
        if (!accepted) {
          const message = parseUploadError(data, res.status);
          setUploadJobs((prev) =>
            prev.map((j) =>
              j.key === job.key
                ? { ...j, status: 'error' as const, detail: message }
                : j.status === 'waiting'
                  ? { ...j, status: 'waiting' as const }
                  : j
            )
          );
          throw new Error(`${job.fileName}: ${message}`);
        }

        const mergeObj =
          data?.merge && typeof data.merge === 'object'
            ? (data.merge as Record<string, unknown>)
            : null;
        const mergeJobId =
          (typeof data?.merge_job_id === 'string' && data.merge_job_id) ||
          (typeof mergeObj?.job_id === 'string' && mergeObj.job_id) ||
          null;

        if (mergeJobId) {
          setUploadJobs((prev) =>
            prev.map((j) =>
              j.key === job.key
                ? { ...j, status: 'merging' as const, mergePct: 2, mergeMessage: 'Starting merge…' }
                : j
            )
          );
          const mergeStatus = await pollMergeUntilDone(mergeJobId, 45 * 60 * 1000, (pct, message) => {
            setUploadJobs((prev) =>
              prev.map((j) =>
                j.key === job.key
                  ? { ...j, status: 'merging' as const, mergePct: pct, mergeMessage: message }
                  : j
              )
            );
          });
          if (mergeStatus.background_still_running) {
            allWarnings.push(
              ...((mergeStatus.warnings as string[] | undefined) ?? []).map(String)
            );
            setUploadJobs((prev) =>
              prev.map((j) =>
                j.key === job.key
                  ? {
                      ...j,
                      status: 'merging' as const,
                      mergePct: Number(mergeStatus.progress_pct ?? j.mergePct ?? 0),
                      mergeMessage: String(
                        mergeStatus.progress_message || 'Merge continues in background…'
                      ),
                      detail:
                        'Merge continues in background on server — refresh in a few minutes.',
                    }
                  : j
              )
            );
          } else {
            data = {
              ...(data ?? {}),
              merge: mergeStatus.result ?? mergeStatus,
              warnings: [
                ...(Array.isArray(data?.warnings) ? (data.warnings as unknown[]) : []),
                ...collectWarnings((mergeStatus.result as Record<string, unknown> | null) ?? null),
              ],
            };
            allWarnings.push(...collectWarnings(data));
            setUploadJobs((prev) =>
              prev.map((j) => (j.key === job.key ? { ...j, status: 'done' as const } : j))
            );
          }
        } else {
          allWarnings.push(...collectWarnings(data));
          setUploadJobs((prev) =>
            prev.map((j) => (j.key === job.key ? { ...j, status: 'done' as const } : j))
          );
        }
      }

      if (allWarnings.length) {
        setUploadWarning([...new Set(allWarnings)].join(' · '));
      }

      setUploadDone(true);
      setFilesByDest({ ...EMPTY_FILES });
      setSelectedStores([]);
      setStoreSaveError(null);
      setStoreModalMode('after-upload');
      setShowStoreModal(true);
    } catch (err: unknown) {
      let msg =
        err instanceof Error
          ? err.message
          : 'Could not reach backend. Check that http://127.0.0.1:5001 is running.';
      if (msg === '[object Object]') {
        msg = 'Upload/merge failed. Check backend logs.';
      }
      setUploadError(msg);
      setUploadDone(false);
    } finally {
      setUploading(false);
    }
  };

  const { totalFiles, totalSize, categoriesWithFiles } = useMemo(() => {
    let count = 0;
    let size = 0;
    const withFiles: { id: DestinationId; label: string }[] = [];
    for (const dest of DESTINATIONS) {
      const list = filesByDest[dest.id];
      if (list.length > 0) {
        withFiles.push({ id: dest.id, label: dest.label });
        count += list.length;
        size += list.reduce((s, f) => s + f.size, 0);
      }
    }
    return { totalFiles: count, totalSize: size, categoriesWithFiles: withFiles };
  }, [filesByDest]);

  const canUpload = totalFiles > 0;

  return (
    <div className="mx-auto w-full max-w-5xl px-2 sm:px-4">
      {/* Header */}
      <header className="mb-7 text-center">
        <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-gradient-to-br from-emerald-500 to-teal-600 text-white shadow-lg shadow-emerald-500/25">
          <CloudUpload className="h-8 w-8" strokeWidth={1.75} />
        </div>
        <h1 className="text-2xl font-bold tracking-tight text-slate-900 dark:text-white sm:text-3xl">
          Upload Data
        </h1>
        <p className="mx-auto mt-2 max-w-xl text-sm leading-relaxed text-slate-500 dark:text-slate-400">
          Drop files into any category bar below — mix Sales, SOH, Transfers & Masters, then upload all at once.
        </p>
        {totalFiles > 0 && (
          <div className="mt-4 flex flex-wrap items-center justify-center gap-3">
            <div className="inline-flex items-center gap-2 rounded-full border border-emerald-200 bg-emerald-50 px-4 py-1.5 text-xs font-semibold text-emerald-700 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-400">
              <Paperclip className="h-3.5 w-3.5" />
              {totalFiles} file{totalFiles !== 1 ? 's' : ''} · {categoriesWithFiles.length} categor
              {categoriesWithFiles.length !== 1 ? 'ies' : 'y'} · {formatFileSize(totalSize)}
            </div>
            {!uploading && (
              <button
                type="button"
                onClick={clearAllFiles}
                className="inline-flex items-center gap-1.5 rounded-full border border-rose-200 bg-white px-4 py-1.5 text-xs font-semibold text-rose-600 shadow-sm transition hover:bg-rose-50 dark:border-rose-500/30 dark:bg-slate-900 dark:text-rose-400 dark:hover:bg-rose-500/10"
              >
                <X className="h-3.5 w-3.5" strokeWidth={2.5} />
                Clear all
              </button>
            )}
          </div>
        )}
      </header>

      <PipelineLivePanel />

      {/* Bars list */}
      <div className="overflow-hidden rounded-2xl border border-slate-200/90 bg-white shadow-xl shadow-slate-200/40 dark:border-slate-700 dark:bg-slate-900 dark:shadow-none">
        <div className="flex items-center justify-between border-b border-slate-100 bg-slate-50/80 px-5 py-3 dark:border-slate-800 dark:bg-slate-800/50">
          <p className="text-[11px] font-bold uppercase tracking-widest text-slate-400">
            Categories · {DESTINATIONS.length}
          </p>
          <div className="flex items-center gap-3">
            {totalFiles > 0 && !uploading && (
              <button
                type="button"
                onClick={clearAllFiles}
                className="text-[11px] font-semibold text-rose-600 transition hover:text-rose-700 dark:text-rose-400 dark:hover:text-rose-300"
              >
                Clear all files
              </button>
            )}
            <p className="text-[11px] text-slate-400">Drag into any bar</p>
          </div>
        </div>

        <div className="max-h-[min(68vh,720px)] overflow-y-auto">
          {DESTINATIONS.map((dest, index) => (
            <CategoryRow
              key={dest.id}
              index={index}
              id={dest.id}
              label={dest.label}
              files={filesByDest[dest.id]}
              isDragging={draggingDest === dest.id}
              fileStatuses={fileStatusMap}
              uploading={uploading}
              onDragOver={(e) => {
                e.preventDefault();
                e.stopPropagation();
                if (!uploading) setDraggingDest(dest.id);
              }}
              onDragLeave={(e) => {
                e.preventDefault();
                e.stopPropagation();
                setDraggingDest(null);
              }}
              onDrop={(e) => {
                e.preventDefault();
                e.stopPropagation();
                setDraggingDest(null);
                if (!uploading && e.dataTransfer.files?.length) {
                  addFilesToDest(dest.id, e.dataTransfer.files);
                }
              }}
              onAddFiles={(incoming) => addFilesToDest(dest.id, incoming)}
              onRemoveFile={(fileIndex) => removeFile(dest.id, fileIndex)}
              onClear={() => clearCategory(dest.id)}
            />
          ))}
        </div>

        {(uploading || (uploadJobs.length > 0 && !uploadDone)) && progressSummary && (
          <div className="border-t border-slate-200 bg-slate-50/90 px-5 py-4 dark:border-slate-700 dark:bg-slate-800/60">
            <div className="mb-3 flex items-center justify-between gap-3">
              <p className="text-sm font-semibold text-slate-800 dark:text-slate-100">
                {uploading
                  ? progressSummary?.mergingCount
                    ? `Appending / merging… ${progressSummary.mergePct != null ? `${progressSummary.mergePct}%` : ''}`
                    : 'Uploading files…'
                  : 'Upload stopped'}
              </p>
              <p className="text-xs font-medium tabular-nums text-slate-500">
                {progressSummary.done}/{progressSummary.total} done
                {progressSummary.failed > 0 ? ` · ${progressSummary.failed} failed` : ''}
                {progressSummary.mergePct != null && progressSummary.mergingCount
                  ? ` · merge ${progressSummary.mergePct}%`
                  : ''}
              </p>
            </div>
            <div className="mb-3 h-2 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-700">
              <div
                className="h-full rounded-full bg-gradient-to-r from-emerald-500 to-teal-500 transition-all duration-300"
                style={{
                  width: `${Math.min(100, Math.max(progressSummary.overall, Math.round((progressSummary.done / Math.max(progressSummary.total, 1)) * 100)))}%`,
                }}
              />
            </div>
            {progressSummary.active && (
              <p className="mb-2 text-xs font-medium text-teal-700 dark:text-teal-300">
                Now: {progressSummary.active.fileName}
                <span className="font-normal text-slate-500"> · {progressSummary.active.categoryLabel}</span>
                {progressSummary.mergeMessage ? (
                  <span className="mt-0.5 block font-normal text-indigo-600 dark:text-indigo-300">
                    {progressSummary.mergeMessage}
                  </span>
                ) : null}
              </p>
            )}
            <ul className="max-h-40 space-y-1 overflow-y-auto text-xs">
              {uploadJobs.map((job) => (
                <li
                  key={job.key}
                  className="flex items-center gap-2 rounded-md px-1.5 py-1 text-slate-600 dark:text-slate-300"
                >
                  <FileStatusIcon status={job.status} />
                  <span className="min-w-0 flex-1 truncate font-medium">{job.fileName}</span>
                  <span className="shrink-0 text-[11px] text-slate-400">
                    {statusLabel(job.status, job.mergePct)}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Sticky-feel footer */}
        <div className="flex flex-col gap-3 border-t border-slate-200 bg-gradient-to-r from-slate-50 to-white px-5 py-4 dark:border-slate-700 dark:from-slate-800/80 dark:to-slate-900 sm:flex-row sm:items-center sm:justify-between">
          {uploadError ? (
            <div className="flex max-w-xl items-start gap-2 text-sm font-medium text-rose-600 dark:text-rose-400">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>{uploadError}</span>
            </div>
          ) : uploadDone ? (
            <div className="flex flex-col gap-1 text-sm font-medium text-emerald-600 dark:text-emerald-400">
              <div className="flex items-center gap-2">
                <CheckCircle2 className="h-5 w-5" />
                Uploaded {completedUploadSummary?.totalFiles ?? totalFiles} file
                {(completedUploadSummary?.totalFiles ?? totalFiles) !== 1 ? 's' : ''} across{' '}
                {completedUploadSummary?.categoriesCount ?? categoriesWithFiles.length}{' '}
                categor
                {(completedUploadSummary?.categoriesCount ?? categoriesWithFiles.length) !== 1
                  ? 'ies'
                  : 'y'}
              </div>
              {uploadWarning && (
                <div className="mt-1 flex max-w-xl items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-medium leading-snug text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
                  <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                  <span>{uploadWarning}</span>
                </div>
              )}
              {storeSaveDone && selectedStores.length > 0 && (
                <p className="pl-7 text-xs font-normal text-slate-500 dark:text-slate-400">
                  Replenishment stores:{' '}
                  {selectedStores
                    .map((id) => REPLENISHMENT_STORES.find((s) => s.id === id)?.label || id)
                    .join(', ')}
                  {' · '}pipeline started
                </p>
              )}
                  {!storeSaveDone && (
                <button
                  type="button"
                  onClick={() => {
                    setStoreModalMode(uploadDone ? 'after-upload' : 'run-only');
                    setShowStoreModal(true);
                  }}
                  className="pl-7 text-left text-xs font-semibold text-teal-700 underline-offset-2 hover:underline dark:text-teal-400"
                >
                  Choose replenishment stores
                </button>
              )}
            </div>
          ) : (
            <div>
              <p className="text-sm font-medium text-slate-700 dark:text-slate-200">
                {canUpload
                  ? `Ready to upload ${totalFiles} file${totalFiles !== 1 ? 's' : ''}`
                  : 'Add files to any category above'}
              </p>
              {canUpload && (
                <p className="mt-0.5 max-w-md truncate text-xs text-slate-500 dark:text-slate-400">
                  {categoriesWithFiles.map((c) => c.label).join(' · ')}
                </p>
              )}
            </div>
          )}

          <div className="flex shrink-0 flex-col gap-2 sm:flex-row sm:items-center">
            <button
              type="button"
              disabled={uploading || savingStores}
              onClick={() => {
                setStoreSaveError(null);
                setStoreModalMode('run-only');
                setShowStoreModal(true);
              }}
              className="inline-flex items-center justify-center gap-2 rounded-xl border border-teal-300 bg-white px-6 py-3 text-sm font-semibold text-teal-800 shadow-sm transition hover:bg-teal-50 disabled:cursor-not-allowed disabled:opacity-40 dark:border-teal-500/40 dark:bg-slate-800 dark:text-teal-200 dark:hover:bg-teal-500/10 sm:min-w-[160px]"
            >
              <Play className="h-4 w-4" strokeWidth={2.5} />
              Run Pipeline
            </button>
            <button
              type="button"
              disabled={!canUpload || uploading}
              onClick={handleUpload}
              className="inline-flex items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-emerald-600 to-teal-600 px-7 py-3 text-sm font-semibold text-white shadow-lg shadow-emerald-600/25 transition hover:from-emerald-500 hover:to-teal-500 disabled:cursor-not-allowed disabled:opacity-40 disabled:shadow-none sm:min-w-[170px]"
            >
              {uploading ? (
                <>
                  <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
                  {progressSummary
                    ? progressSummary.mergingCount
                      ? progressSummary.mergePct != null && progressSummary.mergePct > 0
                        ? `Merging ${progressSummary.mergePct}%…`
                        : `Merging ${progressSummary.done + 1}/${progressSummary.total}…`
                      : `Uploading ${progressSummary.done + 1}/${progressSummary.total}…`
                    : 'Uploading & merging...'}
                </>
              ) : (
                <>
                  <Upload className="h-4 w-4" strokeWidth={2.5} />
                  Upload All
                </>
              )}
            </button>
          </div>
        </div>
        <div className="flex flex-col gap-2 border-t border-slate-100 bg-indigo-50/60 px-5 py-3 dark:border-slate-800 dark:bg-indigo-950/20">
          <button
            type="button"
            disabled={uploading || savingStores || remapStarting}
            onClick={() => {
              setRemapError(null);
              setShowRemapModal(true);
            }}
            className="inline-flex items-center justify-center gap-2 rounded-xl border border-indigo-200 bg-white px-4 py-2.5 text-sm font-semibold text-indigo-800 shadow-sm transition hover:bg-indigo-50 disabled:cursor-not-allowed disabled:opacity-40 dark:border-indigo-500/40 dark:bg-slate-900 dark:text-indigo-200 dark:hover:bg-indigo-500/10"
          >
            {remapStarting ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Sparkles className="h-4 w-4" />
            )}
            {remapStarting ? 'Starting remapped replenishment…' : 'Replenish remapped SKUs'}
          </button>
          <p className="text-[11px] leading-relaxed text-slate-500 dark:text-slate-400">
            After a full pipeline: only SKUs in SKU Remap (OLD → NEW) get warehouse refill. Other SKUs stay as-is.
            Click to choose Remaining WH or Fresh WH.
          </p>
          {remapError && (
            <p className="flex items-start gap-1.5 text-xs font-medium text-rose-600 dark:text-rose-400">
              <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              {remapError}
            </p>
          )}
        </div>
      </div>

      {/* Post-upload: select replenishment stores */}
      {showStoreModal && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-4 backdrop-blur-sm"
          onClick={() => setShowStoreModal(false)}
          role="presentation"
        >
          <div
            className="w-full max-w-lg overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl dark:border-slate-700 dark:bg-slate-900"
            role="dialog"
            aria-modal="true"
            aria-labelledby="replenish-stores-title"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-start justify-between gap-3 border-b border-slate-100 px-5 py-4 dark:border-slate-800">
              <div className="flex items-start gap-3">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-teal-50 text-teal-700 dark:bg-teal-500/15 dark:text-teal-300">
                  <Store className="h-5 w-5" />
                </div>
                <div>
                  <h2
                    id="replenish-stores-title"
                    className="text-base font-bold text-slate-900 dark:text-white"
                  >
                    Select replenishment stores
                  </h2>
                  <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
                    {storeModalMode === 'run-only'
                      ? 'Select store channels for WH refill + transfer, then start pipeline on existing files (no upload needed).'
                      : 'Choose which of these 5 store channels need replenishment, then run pipeline. (File merge already happens on Upload.)'}
                  </p>
                </div>
              </div>
              <button
                type="button"
                onClick={() => setShowStoreModal(false)}
                className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600 dark:hover:bg-slate-800"
                aria-label="Close"
              >
                <X className="h-5 w-5" />
              </button>
            </div>

            <div className="space-y-2 px-5 py-4">
              <div className="mb-2 flex items-center justify-between">
                <p className="text-[11px] font-bold uppercase tracking-wider text-slate-400">
                  {selectedStores.length} of {REPLENISHMENT_STORES.length} selected
                </p>
                <div className="flex gap-2 text-xs font-semibold">
                  <button
                    type="button"
                    onClick={selectAllStores}
                    className="text-teal-700 hover:underline dark:text-teal-400"
                  >
                    Select all
                  </button>
                  <span className="text-slate-300">·</span>
                  <button
                    type="button"
                    onClick={clearStores}
                    className="text-slate-500 hover:underline"
                  >
                    Clear
                  </button>
                </div>
              </div>

              {REPLENISHMENT_STORES.map((store) => {
                const checked = selectedStores.includes(store.id);
                return (
                  <label
                    key={store.id}
                    className={`flex cursor-pointer items-center gap-3 rounded-xl border px-4 py-3 transition ${
                      checked
                        ? 'border-teal-300 bg-teal-50/80 dark:border-teal-500/40 dark:bg-teal-500/10'
                        : 'border-slate-200 hover:border-slate-300 dark:border-slate-700 dark:hover:border-slate-600'
                    }`}
                  >
                    <input
                      type="checkbox"
                      className="h-4 w-4 rounded border-slate-300 text-teal-600 focus:ring-teal-500"
                      checked={checked}
                      onChange={() => toggleStore(store.id)}
                    />
                    <span className="text-sm font-semibold text-slate-800 dark:text-slate-100">
                      {store.label}
                    </span>
                  </label>
                );
              })}

              <div className="mt-4 border-t border-slate-100 pt-4 dark:border-slate-800">
                <p className="mb-2 text-[11px] font-bold uppercase tracking-wider text-slate-400">
                  Warehouse stock
                </p>
                <div className="space-y-2">
                  <label
                    className={`flex cursor-pointer gap-3 rounded-xl border px-4 py-3 transition ${
                      warehouseMode === 'incremental'
                        ? 'border-amber-300 bg-amber-50/80 dark:border-amber-500/40 dark:bg-amber-500/10'
                        : 'border-slate-200 hover:border-slate-300 dark:border-slate-700'
                    }`}
                  >
                    <input
                      type="radio"
                      name="warehouse-mode"
                      className="mt-0.5 h-4 w-4 border-slate-300 text-amber-600 focus:ring-amber-500"
                      checked={warehouseMode === 'incremental'}
                      onChange={() => setWarehouseMode('incremental')}
                    />
                    <span>
                      <span className="block text-sm font-semibold text-slate-800 dark:text-slate-100">
                        Use Remaining WH Stock (Incremental)
                      </span>
                      <span className="mt-0.5 block text-xs text-slate-500 dark:text-slate-400">
                        Default for a second pass — only leftover godown qty after earlier refills.
                      </span>
                    </span>
                  </label>
                  <label
                    className={`flex cursor-pointer gap-3 rounded-xl border px-4 py-3 transition ${
                      warehouseMode === 'fresh'
                        ? 'border-amber-300 bg-amber-50/80 dark:border-amber-500/40 dark:bg-amber-500/10'
                        : 'border-slate-200 hover:border-slate-300 dark:border-slate-700'
                    }`}
                  >
                    <input
                      type="radio"
                      name="warehouse-mode"
                      className="mt-0.5 h-4 w-4 border-slate-300 text-amber-600 focus:ring-amber-500"
                      checked={warehouseMode === 'fresh'}
                      onChange={() => setWarehouseMode('fresh')}
                    />
                    <span>
                      <span className="block text-sm font-semibold text-slate-800 dark:text-slate-100">
                        Use Full WH Stock (Fresh Re-run)
                      </span>
                      <span className="mt-0.5 block text-xs text-slate-500 dark:text-slate-400">
                        Same WH file, recalculate from full register (same stores rerun).
                      </span>
                    </span>
                  </label>
                </div>
              </div>

              {storeSaveError && (
                <div className="flex items-start gap-2 pt-1 text-sm text-rose-600 dark:text-rose-400">
                  <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                  <span>{storeSaveError}</span>
                </div>
              )}
            </div>

            <div className="flex flex-col-reverse gap-2 border-t border-slate-100 bg-slate-50/80 px-5 py-4 dark:border-slate-800 dark:bg-slate-800/40 sm:flex-row sm:justify-end">
              <button
                type="button"
                onClick={() => setShowStoreModal(false)}
                className="rounded-xl px-4 py-2.5 text-sm font-semibold text-slate-600 hover:bg-slate-200/60 dark:text-slate-300 dark:hover:bg-slate-700"
              >
                Skip for now
              </button>
              <button
                type="button"
                disabled={savingStores || selectedStores.length === 0}
                onClick={handleConfirmStores}
                className="inline-flex items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-emerald-600 to-teal-600 px-5 py-2.5 text-sm font-semibold text-white shadow-md shadow-emerald-600/20 disabled:cursor-not-allowed disabled:opacity-40"
              >
                {savingStores ? (
                  <>
                    <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
                    Saving & starting pipeline...
                  </>
                ) : (
                  <>Confirm & run pipeline</>
                )}
              </button>
            </div>
          </div>
        </div>
      )}

      {showRemapModal && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-4 backdrop-blur-sm"
          onClick={() => !remapStarting && setShowRemapModal(false)}
          role="presentation"
        >
          <div
            className="w-full max-w-lg overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl dark:border-slate-700 dark:bg-slate-900"
            role="dialog"
            aria-modal="true"
            aria-labelledby="remap-wh-title"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-start justify-between gap-3 border-b border-slate-100 px-5 py-4 dark:border-slate-800">
              <div>
                <h2 id="remap-wh-title" className="text-base font-bold text-slate-900 dark:text-white">
                  Remapped SKUs — warehouse stock?
                </h2>
                <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
                  Only renamed SKUs get replenishment. Other SKUs stay as last pipeline. Choose WH pool:
                </p>
              </div>
              <button
                type="button"
                onClick={() => setShowRemapModal(false)}
                disabled={remapStarting}
                className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600 disabled:opacity-40 dark:hover:bg-slate-800"
                aria-label="Close"
              >
                <X className="h-5 w-5" />
              </button>
            </div>
            <div className="space-y-2 px-5 py-4">
              <label
                className={`flex cursor-pointer gap-3 rounded-xl border px-4 py-3 transition ${
                  remapWarehouseMode === 'incremental'
                    ? 'border-amber-300 bg-amber-50/80 dark:border-amber-500/40 dark:bg-amber-500/10'
                    : 'border-slate-200 hover:border-slate-300 dark:border-slate-700'
                }`}
              >
                <input
                  type="radio"
                  name="remap-warehouse-mode"
                  className="mt-0.5 h-4 w-4 border-slate-300 text-amber-600 focus:ring-amber-500"
                  checked={remapWarehouseMode === 'incremental'}
                  onChange={() => setRemapWarehouseMode('incremental')}
                />
                <span>
                  <span className="block text-sm font-semibold text-slate-800 dark:text-slate-100">
                    Use Remaining WH Stock
                  </span>
                  <span className="mt-0.5 block text-xs text-slate-500 dark:text-slate-400">
                    Leftover godown qty after the last full pipeline. Remapped SKUs take from what is still left.
                  </span>
                </span>
              </label>
              <label
                className={`flex cursor-pointer gap-3 rounded-xl border px-4 py-3 transition ${
                  remapWarehouseMode === 'fresh'
                    ? 'border-amber-300 bg-amber-50/80 dark:border-amber-500/40 dark:bg-amber-500/10'
                    : 'border-slate-200 hover:border-slate-300 dark:border-slate-700'
                }`}
              >
                <input
                  type="radio"
                  name="remap-warehouse-mode"
                  className="mt-0.5 h-4 w-4 border-slate-300 text-amber-600 focus:ring-amber-500"
                  checked={remapWarehouseMode === 'fresh'}
                  onChange={() => setRemapWarehouseMode('fresh')}
                />
                <span>
                  <span className="block text-sm font-semibold text-slate-800 dark:text-slate-100">
                    Use Fresh WH SOH
                  </span>
                  <span className="mt-0.5 block text-xs text-slate-500 dark:text-slate-400">
                    Full current warehouse file. Remapped SKUs allocate from complete WH, then remaining is leftover.
                  </span>
                </span>
              </label>
              {remapError && (
                <div className="flex items-start gap-2 pt-1 text-sm text-rose-600 dark:text-rose-400">
                  <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                  <span>{remapError}</span>
                </div>
              )}
            </div>
            <div className="flex flex-col-reverse gap-2 border-t border-slate-100 bg-slate-50/80 px-5 py-4 dark:border-slate-800 dark:bg-slate-800/40 sm:flex-row sm:justify-end">
              <button
                type="button"
                disabled={remapStarting}
                onClick={() => setShowRemapModal(false)}
                className="rounded-xl px-4 py-2.5 text-sm font-semibold text-slate-600 hover:bg-slate-200/60 dark:text-slate-300 dark:hover:bg-slate-700"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={remapStarting}
                onClick={() => void handleRemapReplenish(remapWarehouseMode)}
                className="inline-flex items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-indigo-600 to-sky-600 px-5 py-2.5 text-sm font-semibold text-white shadow-md shadow-indigo-500/20 disabled:cursor-not-allowed disabled:opacity-40"
              >
                {remapStarting ? (
                  <>
                    <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
                    Starting...
                  </>
                ) : (
                  <>Start remapped replenishment</>
                )}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

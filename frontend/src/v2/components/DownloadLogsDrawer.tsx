import React, { useState, useEffect, useMemo } from 'react';
import { 
  X, History, Trash2, Search, Laptop, Globe, Server, 
  Terminal, Calendar, AlertCircle, RefreshCw, Check
} from 'lucide-react';
import { API_URL } from '../../config';

interface LogEntry {
  id: number;
  timestamp: string;
  page: string;
  filename: string;
  ip_address: string;
  user_agent: string;
  device: string;
  location_type: string;
}

interface DownloadLogsDrawerProps {
  isOpen: boolean;
  onClose: () => void;
}

const formatLocalDate = (utcString: string) => {
  try {
    if (!utcString) return '';
    // If it's a simple string like "2026-05-20 06:44:17" without timezone indicator, treat it as UTC by adding 'Z'
    let dateStr = utcString;
    if (!utcString.includes('T') && !utcString.includes('Z')) {
      dateStr = utcString.replace(' ', 'T') + 'Z';
    }
    const date = new Date(dateStr);
    if (isNaN(date.getTime())) return utcString;
    return date.toLocaleString(undefined, {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: true
    });
  } catch (e) {
    return utcString;
  }
};

export const DownloadLogsDrawer = ({ isOpen, onClose }: DownloadLogsDrawerProps) => {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [confirmClear, setConfirmClear] = useState(false);
  const [copiedId, setCopiedId] = useState<number | null>(null);

  // Fetch logs from backend
  const fetchLogs = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_URL}/download-logs`);
      if (!res.ok) {
        throw new Error(`Failed to fetch logs (status ${res.status})`);
      }
      const data = await res.json();
      if (data.status === 'success' && Array.isArray(data.data)) {
        setLogs(data.data);
      } else {
        throw new Error(data.message || 'Invalid log format');
      }
    } catch (err: any) {
      console.error('Error loading download logs:', err);
      setError(err.message || 'Failed to connect to logging server');
    } finally {
      setLoading(false);
    }
  };

  // Clear logs in backend
  const handleClearLogs = async () => {
    try {
      const res = await fetch(`${API_URL}/download-logs/clear`, { method: 'POST' });
      if (!res.ok) {
        throw new Error('Failed to clear logs');
      }
      setLogs([]);
      setConfirmClear(false);
    } catch (err: any) {
      alert(`Error: ${err.message}`);
    }
  };

  // Fetch logs whenever the drawer opens
  useEffect(() => {
    if (isOpen) {
      fetchLogs();
      setConfirmClear(false);
      setSearchQuery('');
    }
  }, [isOpen]);

  // Handle closing drawer on Escape key press
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    if (isOpen) {
      window.addEventListener('keydown', handleKeyDown);
    }
    return () => {
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [isOpen, onClose]);

  // Filter logs by search query
  const filteredLogs = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    if (!query) return logs;
    return logs.filter(log => 
      log.filename.toLowerCase().includes(query) ||
      log.page.toLowerCase().includes(query) ||
      log.ip_address.toLowerCase().includes(query) ||
      log.device.toLowerCase().includes(query) ||
      log.location_type.toLowerCase().includes(query) ||
      log.timestamp.toLowerCase().includes(query)
    );
  }, [logs, searchQuery]);

  const copyToClipboard = (text: string, id: number) => {
    navigator.clipboard.writeText(text);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-[100] flex justify-end overflow-hidden">
      {/* Background glassmorphic overlay with fade-in */}
      <div 
        className="absolute inset-0 bg-slate-950/40 backdrop-blur-[4px] transition-opacity duration-300"
        onClick={onClose}
      />

      {/* Slide-out drawer body */}
      <div className="relative z-10 flex h-full w-full max-w-lg flex-col bg-white shadow-2xl border-l border-slate-200/80 dark:border-slate-800 dark:bg-slate-950 transition-transform duration-300">
        
        {/* Top Header */}
        <div className="flex items-center justify-between border-b border-slate-100 px-6 py-5 dark:border-slate-800/80">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-indigo-500/10 text-indigo-600 dark:bg-indigo-500/20 dark:text-indigo-400">
              <History className="h-5 w-5" />
            </div>
            <div>
              <h2 className="text-lg font-semibold text-slate-900 dark:text-white">Download Logs</h2>
              <p className="text-xs text-slate-500 dark:text-slate-400">Track client & server file exports</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={fetchLogs}
              title="Refresh logs"
              disabled={loading}
              className="rounded-lg p-2 text-slate-500 transition-colors hover:bg-slate-50 hover:text-slate-900 dark:text-slate-400 dark:hover:bg-slate-900 dark:hover:text-slate-100"
            >
              <RefreshCw className={`h-4.5 w-4.5 ${loading ? 'animate-spin' : ''}`} />
            </button>
            <button 
              onClick={onClose}
              className="rounded-lg p-2 text-slate-500 transition-colors hover:bg-slate-50 hover:text-slate-900 dark:text-slate-400 dark:hover:bg-slate-900 dark:hover:text-slate-100"
              aria-label="Close drawer"
            >
              <X className="h-5 w-5" />
            </button>
          </div>
        </div>

        {/* Action Bar (Search & Clear) */}
        <div className="border-b border-slate-100 bg-slate-50/50 px-6 py-4 dark:border-slate-800/60 dark:bg-slate-900/30">
          <div className="flex items-center gap-3">
            {/* Search */}
            <div className="relative flex-1">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400 dark:text-slate-500" />
              <input
                type="text"
                placeholder="Search logs by IP, page, file name..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full rounded-xl border border-slate-200/80 bg-white py-2 pl-9 pr-4 text-xs outline-none transition-all placeholder:text-slate-400 focus:border-indigo-400 focus:ring-1 focus:ring-indigo-400 dark:border-slate-800 dark:bg-slate-900 dark:text-white dark:placeholder:text-slate-600 dark:focus:border-indigo-500"
              />
              {searchQuery && (
                <button
                  onClick={() => setSearchQuery('')}
                  className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200"
                >
                  <X className="h-3 w-3" />
                </button>
              )}
            </div>

            {/* Clear Logs Button */}
            {logs.length > 0 && (
              <div className="relative shrink-0">
                {confirmClear ? (
                  <div className="flex items-center gap-1.5 rounded-xl border border-red-200 bg-red-50 p-1 dark:border-red-900/40 dark:bg-red-950/20">
                    <button
                      onClick={handleClearLogs}
                      className="rounded-lg bg-red-600 px-2.5 py-1 text-[10px] font-bold text-white transition-colors hover:bg-red-700"
                    >
                      Confirm
                    </button>
                    <button
                      onClick={() => setConfirmClear(false)}
                      className="rounded-lg px-2 py-1 text-[10px] font-medium text-slate-600 hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800"
                    >
                      Cancel
                    </button>
                  </div>
                ) : (
                  <button
                    onClick={() => setConfirmClear(true)}
                    className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-600 shadow-sm transition-colors hover:bg-red-50 hover:text-red-600 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-400 dark:hover:bg-red-950/30 dark:hover:text-red-400"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                    Clear Logs
                  </button>
                )}
              </div>
            )}
          </div>
        </div>

        {/* Logs List Area */}
        <div className="flex-1 overflow-y-auto px-6 py-4 custom-scrollbar">
          {error && (
            <div className="flex items-start gap-3 rounded-xl border border-red-200 bg-red-50 p-4 text-xs text-red-700 dark:border-red-900/30 dark:bg-red-950/20 dark:text-red-400">
              <AlertCircle className="h-4 w-4 shrink-0 mt-0.5" />
              <div>
                <span className="font-bold">Error loading logs:</span>
                <p className="mt-1 font-mono text-[10px] leading-relaxed">{error}</p>
              </div>
            </div>
          )}

          {loading && logs.length === 0 ? (
            <div className="space-y-4 py-8">
              {[...Array(6)].map((_, i) => (
                <div key={i} className="flex flex-col gap-2 rounded-xl border border-slate-100 p-4 dark:border-slate-800/60 animate-pulse">
                  <div className="flex items-center justify-between">
                    <div className="h-4 w-28 rounded bg-slate-100 dark:bg-slate-800" />
                    <div className="h-4 w-20 rounded bg-slate-100 dark:bg-slate-800" />
                  </div>
                  <div className="h-3.5 w-full rounded bg-slate-100 dark:bg-slate-800" />
                  <div className="h-3 w-40 rounded bg-slate-100 dark:bg-slate-800" />
                </div>
              ))}
            </div>
          ) : filteredLogs.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-20 text-center text-slate-400 dark:text-slate-500">
              <History className="h-12 w-12 opacity-15 mb-3" />
              <span className="text-sm font-semibold">No Download Logs Found</span>
              <p className="mt-1 text-xs text-slate-500 max-w-xs">
                {searchQuery ? 'Try adjusting your search filters.' : 'Logs will populate here as soon as anyone downloads files.'}
              </p>
            </div>
          ) : (
            <div className="space-y-3.5">
              <div className="flex items-center justify-between text-[11px] font-bold text-slate-400 uppercase tracking-wider">
                <span>Export Details</span>
                <span>{filteredLogs.length} logged</span>
              </div>

              {filteredLogs.map((log) => {
                const isLocal = log.location_type.includes('Local');
                const isReplenish = log.page.includes('Replenishment');
                const isTransfer = log.page.includes('Transfer');
                
                return (
                  <div 
                    key={log.id} 
                    className="group relative flex flex-col gap-2 rounded-xl border border-slate-200/70 bg-white p-4 shadow-sm transition-all hover:border-slate-300 dark:border-slate-800/80 dark:bg-slate-900/40 dark:hover:border-slate-700/80"
                  >
                    {/* Page badge and timestamp */}
                    <div className="flex items-center justify-between gap-4">
                      <span className={`inline-flex items-center rounded-lg px-2 py-0.5 text-[10px] font-bold tracking-wide uppercase ${
                        isReplenish 
                          ? 'bg-emerald-500/10 text-emerald-600 dark:bg-emerald-500/20 dark:text-emerald-400' 
                          : isTransfer
                            ? 'bg-indigo-500/10 text-indigo-600 dark:bg-indigo-500/20 dark:text-indigo-400'
                            : 'bg-amber-500/10 text-amber-600 dark:bg-amber-500/20 dark:text-amber-400'
                      }`}>
                        {log.page}
                      </span>
                      <span className="inline-flex items-center gap-1 text-[10px] text-slate-400 dark:text-slate-500">
                        <Calendar className="h-3 w-3" />
                        {formatLocalDate(log.timestamp)}
                      </span>
                    </div>

                    {/* Filename with copy button */}
                    <div className="flex items-start justify-between gap-3">
                      <code className="text-xs font-semibold font-mono text-slate-700 dark:text-slate-300 break-all select-all leading-normal">
                        {log.filename}
                      </code>
                      <button
                        onClick={() => copyToClipboard(log.filename, log.id)}
                        className="opacity-0 group-hover:opacity-100 rounded p-1 text-[10px] text-slate-400 hover:bg-slate-100 hover:text-slate-600 dark:hover:bg-slate-800 dark:hover:text-slate-200 transition-all shrink-0"
                        title="Copy filename"
                      >
                        {copiedId === log.id ? (
                          <Check className="h-3.5 w-3.5 text-emerald-500" />
                        ) : (
                          <span className="text-[10px] font-semibold">Copy</span>
                        )}
                      </button>
                    </div>

                    {/* Divider */}
                    <div className="h-[1px] w-full bg-slate-100 dark:bg-slate-800/80" />

                    {/* Metadata: Location type, IP and Device agent */}
                    <div className="flex flex-wrap items-center justify-between gap-y-2 gap-x-4 text-[11px]">
                      {/* Location Badge */}
                      <div className="flex items-center gap-1.5">
                        {isLocal ? (
                          <span className="inline-flex items-center gap-1 rounded-md bg-emerald-500/10 px-1.5 py-0.5 text-[10px] font-semibold text-emerald-700 dark:bg-emerald-500/20 dark:text-emerald-300">
                            <Laptop className="h-3 w-3" />
                            Local PC / LAN
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 rounded-md bg-sky-500/10 px-1.5 py-0.5 text-[10px] font-semibold text-sky-700 dark:bg-sky-500/20 dark:text-sky-300">
                            <Globe className="h-3 w-3" />
                            Client Network
                          </span>
                        )}
                        <span className="font-mono text-[10px] text-slate-400 dark:text-slate-500">
                          {log.ip_address}
                        </span>
                      </div>

                      {/* Device Agent badge */}
                      <span className="inline-flex items-center gap-1 rounded-md bg-slate-100 px-1.5 py-0.5 text-[10px] font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-400">
                        <Terminal className="h-3 w-3 opacity-60" />
                        {log.device}
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Bottom Footer Info */}
        <div className="border-t border-slate-100 bg-slate-50/50 px-6 py-4.5 text-[11px] text-slate-400 dark:border-slate-800/60 dark:bg-slate-900/20">
          <p className="leading-relaxed">
            Logs are saved locally in a secure SQLite database. Real-time classification distinguishes if the download originated from your local environment or an external device.
          </p>
        </div>
      </div>
    </div>
  );
};

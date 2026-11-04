import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertCircle,
  CheckCircle2,
  Download,
  Layers,
  Loader2,
  Package,
  Search,
  Store,
  Tags,
  Truck,
} from 'lucide-react';
import { API_URL } from '../../../config';
import { ExecutiveMultiSelect } from '../executive/ExecutiveMultiSelect';
import { ReplenishmentLineLoader } from '../ReplenishmentLineLoader';

type DonorRow = {
  site_id?: string;
  site_name?: string;
  sku?: string;
  barcode?: string;
  size?: string;
  store_soh?: number;
  city?: string;
  state?: string;
  days_from_last_sales?: number;
};

type ReceiverRow = {
  site_id?: string;
  site_name?: string;
  sku?: string;
  barcode?: string;
  size?: string;
  store_soh?: number;
  qty_still_needed?: number;
  priority_score?: number;
  city?: string;
  state?: string;
};

type RecommendationRow = {
  id?: number;
  donor_store?: string;
  donor_store_name?: string;
  receiver_store?: string;
  receiver_store_name?: string;
  sku?: string;
  barcode?: string;
  size?: string;
  transfer_qty?: number;
  transfer_qty_total?: number;
  priority_score?: number;
  receiver_soh?: number;
  donor_soh?: number;
  status?: string;
};

type ClearanceSummary = {
  donor_sites?: string[];
  receiver_sites?: string[];
  donor_sku?: string;
  donor_rows?: number;
  receiver_rows?: number;
  recommendation_rows?: number;
  total_donor_soh?: number;
  unique_skus?: number;
};

function fmtQty(n: unknown) {
  const v = Number(n);
  return Number.isFinite(v) ? v.toLocaleString('en-IN') : '—';
}

function StoreNameInline({
  name,
  id,
  className = '',
}: {
  name?: string;
  id?: string;
  className?: string;
}) {
  const code = (id || '').trim();
  const label = (name || id || '—').trim();
  const hasCodeInName = Boolean(
    code &&
      (label.toLowerCase().includes(`(${code.toLowerCase()})`) ||
        label.toLowerCase().includes(`( ${code.toLowerCase()} )`) ||
        label.toLowerCase().includes(`(${code.toLowerCase().replace(/^p/i, '')})`))
  );
  const showCode = Boolean(code && code !== label && !hasCodeInName);
  return (
    <div className={`flex min-w-0 items-center gap-1.5 whitespace-nowrap ${className}`}>
      <span className="truncate font-medium text-slate-800 dark:text-slate-200">{label}</span>
      {showCode ? <span className="shrink-0 text-[10px] text-slate-400">({code})</span> : null}
    </div>
  );
}

function coerceFilterIds(raw: unknown): string[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .map((opt) => {
      if (typeof opt === 'string') return opt;
      if (opt && typeof opt === 'object' && 'id' in opt) return String((opt as { id: string }).id);
      return '';
    })
    .filter(Boolean);
}

async function downloadBlob(res: Response, fallbackName: string) {
  if (!res.ok) {
    const text = await res.text();
    let detail = text?.trim() || '';
    try {
      const j = JSON.parse(text) as { detail?: unknown };
      if (j?.detail != null) {
        detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail);
      }
    } catch {
      /* keep raw */
    }
    throw new Error(detail || `Download failed (HTTP ${res.status})`);
  }
  const blob = await res.blob();
  const cd = res.headers.get('content-disposition') || '';
  const match = cd.match(/filename="?([^"]+)"?/i);
  const fileName = match?.[1] || fallbackName;
  const url = window.URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = fileName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(url);
}

type StoreOption = { id: string; channel: string };
type GeoMode = 'default' | 'in_state' | 'other_state';

export function StoreClearancePanel() {
  const [donorSites, setDonorSites] = useState<string[]>([]);
  const [receiverSites, setReceiverSites] = useState<string[]>([]);
  const [storeOptions, setStoreOptions] = useState<StoreOption[]>([]);
  const [channelOptions, setChannelOptions] = useState<string[]>([]);
  const [seasonOptions, setSeasonOptions] = useState<string[]>([]);
  const [selectedChannels, setSelectedChannels] = useState<string[]>([]);
  const [selectedSeasons, setSelectedSeasons] = useState<string[]>([]);
  const [geoMode, setGeoMode] = useState<GeoMode>('default');
  const [selectedSkus, setSelectedSkus] = useState<string[]>([]);
  const [skuOptions, setSkuOptions] = useState<string[]>([]);
  const [skuLoading, setSkuLoading] = useState(false);
  const [filtersLoading, setFiltersLoading] = useState(false);
  const [loading, setLoading] = useState(false);
  const [approvingId, setApprovingId] = useState<number | null>(null);
  const [approvingAll, setApprovingAll] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searched, setSearched] = useState(false);

  const [donors, setDonors] = useState<DonorRow[]>([]);
  const [receivers, setReceivers] = useState<ReceiverRow[]>([]);
  const [recommendations, setRecommendations] = useState<RecommendationRow[]>([]);
  const [summary, setSummary] = useState<ClearanceSummary | null>(null);

  const canSearch = donorSites.length > 0;
  const showSkuCol = selectedSkus.length !== 1;
  const skuDisplayLabel =
    selectedSkus.length === 0
      ? 'All SKUs'
      : selectedSkus.length === 1
        ? selectedSkus[0]
        : `${selectedSkus.length} SKUs`;

  const visibleStores = useMemo(() => {
    if (!selectedChannels.length) return storeOptions;
    const want = new Set(selectedChannels.map((c) => c.toUpperCase()));
    return storeOptions.filter((s) => want.has(s.channel.toUpperCase()));
  }, [storeOptions, selectedChannels]);
  const siteOptions = useMemo(() => visibleStores.map((s) => s.id), [visibleStores]);

  // Load store, channel, and season options
  useEffect(() => {
    let cancelled = false;
    setFiltersLoading(true);
    fetch(`${API_URL}/transfers-recom/filters/clearance`)
      .then((res) => res.json())
      .then((data) => {
        if (cancelled) return;
        const stores = Array.isArray(data?.stores)
          ? data.stores
              .map((row: { id?: string; label?: string; site_code?: string; site_name?: string; channel?: string }) => {
                const channel = String(row?.channel || '').trim().toUpperCase();
                const code = String(row?.site_code || '').trim();
                const name = String(row?.site_name || '').trim();
                const label = String(row?.label || '').trim() || [code, name, channel].filter(Boolean).join(' · ');
                return {
                  id: label || String(row?.id || '').trim(),
                  channel,
                };
              })
              .filter((row: StoreOption) => row.id && !['All Sites', 'All Stores', 'All'].includes(row.id))
          : [];
        setStoreOptions(stores);
        setChannelOptions(
          Array.isArray(data?.channels) ? data.channels.map((c: unknown) => String(c)).filter(Boolean) : []
        );
        setSeasonOptions(
          Array.isArray(data?.seasons) ? data.seasons.map((s: unknown) => String(s)).filter(Boolean) : []
        );
      })
      .catch(() => {
        if (!cancelled) {
          setStoreOptions([]);
          setChannelOptions([]);
          setSeasonOptions([]);
        }
      })
      .finally(() => {
        if (!cancelled) setFiltersLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Fetch SKUs for selected clearing stores and seasons
  useEffect(() => {
    let cancelled = false;
    if (donorSites.length === 0) {
      setSkuOptions([]);
      setSkuLoading(false);
      return;
    }
    const fetchSkus = async () => {
      setSkuLoading(true);
      try {
        const params = new URLSearchParams();
        if (donorSites.length > 0) params.set('donor_sites', donorSites.join(','));
        if (selectedSeasons.length > 0) params.set('seasons', selectedSeasons.join(','));
        const query = params.toString() ? `?${params}` : '';
        const res = await fetch(`${API_URL}/transfers-recom/filters/skus${query}`);
        const data = await res.json();
        if (!cancelled && Array.isArray(data)) {
          const skus = coerceFilterIds(data).filter((s) => !['All SKUs', 'All', ''].includes(s));
          setSkuOptions(skus);
        }
      } catch {
        if (!cancelled) setSkuOptions([]);
      } finally {
        if (!cancelled) setSkuLoading(false);
      }
    };
    fetchSkus();
    return () => {
      cancelled = true;
    };
  }, [donorSites, selectedSeasons]);

  const donorSohTotal = useMemo(
    () => donors.reduce((s, r) => s + (Number(r.store_soh) || 0), 0),
    [donors]
  );

  const loadClearance = useCallback(async () => {
    if (!canSearch) return;
    setLoading(true);
    setError(null);
    setSearched(true);
    try {
      const skuParam = selectedSkus.length > 0 ? selectedSkus.join(',') : 'All SKUs';
      const params = new URLSearchParams({
        donor_sites: donorSites.join(','),
        donor_sku: skuParam,
      });
      if (receiverSites.length > 0) {
        params.set('receiver_sites', receiverSites.join(','));
      }
      if (selectedChannels.length > 0) {
        params.set('channels', selectedChannels.join(','));
      }
      if (selectedSeasons.length > 0) {
        params.set('seasons', selectedSeasons.join(','));
      }
      params.set('geo_mode', geoMode);
      const res = await fetch(`${API_URL}/transfers-recom/clearance/search?${params}`);
      const json = await res.json();

      if (json?.status === 'error') {
        throw new Error(String(json.message || 'Failed to load clearance data'));
      }

      setDonors(Array.isArray(json.donors) ? json.donors : []);
      setReceivers(Array.isArray(json.receivers) ? json.receivers : []);
      setRecommendations(Array.isArray(json.recommendations) ? json.recommendations : []);
      setSummary(json.summary ?? null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load clearance data');
      setDonors([]);
      setReceivers([]);
      setRecommendations([]);
      setSummary(null);
    } finally {
      setLoading(false);
    }
  }, [canSearch, donorSites, selectedSkus, receiverSites, selectedChannels, selectedSeasons, geoMode]);

  const handleApprove = async (recId: number) => {
    setApprovingId(recId);
    try {
      const rec = recommendations.find((r) => Number(r.id) === recId);
      const res = await fetch(`${API_URL}/transfers-recom/clearance/approve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          recommendation_ids: [recId],
          recommendations: rec ? [rec] : [],
        }),
      });
      const data = await res.json();
      if (data.status !== 'success') {
        // Fallback to legacy approve endpoint if clearance endpoint gave error
        const fallbackRes = await fetch(`${API_URL}/transfers-recom/approve/${recId}`, { method: 'POST' });
        const fallbackData = await fallbackRes.json();
        if (fallbackData.status !== 'success') {
          alert(data.message || fallbackData.message || 'Approve failed');
          return;
        }
      }
      await loadClearance();
    } catch {
      alert('Failed to approve transfer');
    } finally {
      setApprovingId(null);
    }
  };

  const handleApproveAll = async () => {
    if (!recommendations.length) return;
    if (!window.confirm(`Approve all ${recommendations.length} recommended transfer(s)?`)) return;
    setApprovingAll(true);
    try {
      const res = await fetch(`${API_URL}/transfers-recom/clearance/approve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          recommendations,
        }),
      });
      const data = await res.json();
      if (data.status !== 'success') {
        alert(data.message || 'Failed to approve transfers');
        return;
      }
      await loadClearance();
    } catch {
      alert('Failed to approve transfers');
    } finally {
      setApprovingAll(false);
    }
  };

  const handleExport = async () => {
    if (!canSearch) return;
    setExporting(true);
    try {
      const skuParam = selectedSkus.length > 0 ? selectedSkus.join(',') : 'All SKUs';
      const params = new URLSearchParams({
        donor_sites: donorSites.join(','),
        donor_sku: skuParam,
      });
      if (receiverSites.length > 0) {
        params.set('receiver_sites', receiverSites.join(','));
      }
      if (selectedChannels.length > 0) {
        params.set('channels', selectedChannels.join(','));
      }
      if (selectedSeasons.length > 0) {
        params.set('seasons', selectedSeasons.join(','));
      }
      params.set('geo_mode', geoMode);
      const res = await fetch(`${API_URL}/transfers-recom/clearance/export?${params}`, { cache: 'no-store' });
      await downloadBlob(res, `store_clearance_${Date.now()}.xlsx`);
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Export failed');
    } finally {
      setExporting(false);
    }
  };

  const siteLabel =
    donorSites.length === 1 ? donorSites[0] : `${donorSites.length} stores (${donorSites.slice(0, 2).join(', ')}${donorSites.length > 2 ? '…' : ''})`;

  return (
    <div className="space-y-6">
      <div className="rounded-2xl border border-amber-200/80 bg-gradient-to-br from-amber-50/90 to-orange-50/50 p-5 dark:border-amber-900/40 dark:from-amber-950/30 dark:to-slate-900/50">
        <div className="flex items-start gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-amber-500/15 text-amber-700 dark:text-amber-400">
            <Store className="h-5 w-5" />
          </div>
          <div>
            <h2 className="text-lg font-bold text-slate-900 dark:text-white">Store / SKU Clearance</h2>
            <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
              Pick the clearing store and, if you want, the exact receiver stores. Default keeps PT in the same state and lets SSL go to other states. Change State rule, then find transfers.
            </p>
          </div>
        </div>
      </div>

      <div className="flex flex-wrap items-end gap-4 overflow-visible rounded-2xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900/60">
        <div className="flex shrink-0 flex-col gap-1 min-w-[180px] max-w-xs">
          <span className="text-[10px] font-medium uppercase tracking-wider text-slate-400">Channel</span>
          <ExecutiveMultiSelect
            label=""
            icon={Layers}
            options={channelOptions}
            value={selectedChannels}
            minWidth={240}
            loading={filtersLoading}
            onChange={(v) => {
              setSelectedChannels(v);
              const want = new Set(v.map((c) => c.toUpperCase()));
              const allowed = new Set(
                (v.length ? storeOptions.filter((s) => want.has(s.channel.toUpperCase())) : storeOptions).map((s) => s.id)
              );
              setDonorSites((prev) => prev.filter((id) => allowed.has(id)));
              setReceiverSites((prev) => prev.filter((id) => allowed.has(id)));
              setSearched(false);
            }}
            placeholder="All channels"
          />
        </div>

        <div className="flex shrink-0 flex-col gap-1 min-w-[180px] max-w-xs">
          <span className="text-[10px] font-medium uppercase tracking-wider text-slate-400">Season</span>
          <ExecutiveMultiSelect
            label=""
            icon={Tags}
            options={seasonOptions}
            value={selectedSeasons}
            minWidth={240}
            loading={filtersLoading}
            onChange={(v) => {
              setSelectedSeasons(v);
              setSelectedSkus([]);
              setSearched(false);
            }}
            placeholder="All seasons"
          />
        </div>

        <div className="flex shrink-0 flex-col gap-1 min-w-[280px] max-w-md">
          <span className="text-[10px] font-medium uppercase tracking-wider text-slate-400">Clearing Store(s)</span>
          <ExecutiveMultiSelect
            label=""
            icon={Store}
            options={siteOptions}
            value={donorSites}
            minWidth={420}
            onChange={(v) => {
              setDonorSites(v);
              setSelectedSkus([]);
              setSearched(false);
            }}
            placeholder="Select clearing store(s)"
          />
        </div>

        <div className="flex shrink-0 flex-col gap-1 min-w-[280px] max-w-md">
          <span className="text-[10px] font-medium uppercase tracking-wider text-slate-400">Receiver Store(s)</span>
          <ExecutiveMultiSelect
            label=""
            icon={Store}
            options={siteOptions.filter(
              (s) =>
                !donorSites.some((d) => {
                  const sNorm = s.trim().toUpperCase();
                  const dNorm = d.trim().toUpperCase();
                  return sNorm === dNorm || sNorm.includes(dNorm) || dNorm.includes(sNorm);
                })
            )}
            value={receiverSites}
            minWidth={420}
            onChange={(v) => {
              setReceiverSites(v);
              setSearched(false);
            }}
            placeholder="All Receivers (or pick stores)"
          />
        </div>

        <div className="flex shrink-0 flex-col gap-1 min-w-[240px] max-w-sm">
          <span className="text-[10px] font-medium uppercase tracking-wider text-slate-400">SKU to Empty</span>
          <ExecutiveMultiSelect
            label=""
            icon={Package}
            options={skuOptions}
            value={selectedSkus}
            minWidth={360}
            onChange={(v) => {
              setSelectedSkus(v);
              setSearched(false);
            }}
            placeholder={donorSites.length === 0 ? 'Pick a clearing store first' : 'All SKUs (or pick SKUs)'}
            loading={skuLoading}
          />
        </div>

        <div className="flex shrink-0 flex-col gap-1 min-w-[220px]">
          <span className="text-[10px] font-medium uppercase tracking-wider text-slate-400">State rule</span>
          <select
            value={geoMode}
            onChange={(e) => {
              setGeoMode(e.target.value as GeoMode);
              setSearched(false);
            }}
            className="h-[42px] rounded-xl border border-slate-200 bg-white px-3 text-sm font-medium text-slate-800 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
          >
            <option value="default">Default (PT same state, SSL any)</option>
            <option value="in_state">In state only</option>
            <option value="other_state">Allow other state</option>
          </select>
        </div>

        <button
          type="button"
          disabled={!canSearch || loading}
          onClick={() => void loadClearance()}
          className="inline-flex shrink-0 items-center gap-2 rounded-xl bg-indigo-600 px-5 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
          Find Receivers & Transfers
        </button>
        {searched && canSearch && (
          <button
            type="button"
            disabled={exporting}
            onClick={() => void handleExport()}
            className="inline-flex shrink-0 items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
          >
            {exporting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
            Export Excel
          </button>
        )}
      </div>

      {error && (
        <div className="flex items-center gap-2 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 dark:border-rose-900/50 dark:bg-rose-950/30 dark:text-rose-300">
          <AlertCircle className="h-4 w-4 shrink-0" />
          {error}
        </div>
      )}

      {loading && (
        <div className="flex min-h-[200px] items-center justify-center">
          <ReplenishmentLineLoader size={80} />
        </div>
      )}

      {!loading && searched && (
        <>
          {donors.length > 0 && (
            <div className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm dark:border-slate-700 dark:bg-slate-800/50">
              <div className="flex flex-nowrap items-center gap-2 overflow-x-auto whitespace-nowrap">
                {donorSites.length > 1 ? (
                  <div className="flex shrink-0 items-center gap-1.5">
                    {donorSites.map((site) => (
                      <span
                        key={site}
                        className="inline-flex shrink-0 items-center rounded-md bg-white px-2 py-0.5 text-xs font-semibold text-slate-700 dark:bg-slate-900 dark:text-slate-200"
                      >
                        {site}
                      </span>
                    ))}
                  </div>
                ) : (
                  <strong>{siteLabel}</strong>
                )}
                <span className="shrink-0 text-slate-400">·</span>
                <span className="shrink-0">
                  SKU <strong>{skuDisplayLabel}</strong>
                </span>
                {receiverSites.length > 0 && (
                  <>
                    <span className="shrink-0 text-slate-400">·</span>
                    <span className="shrink-0">
                      Target Receivers: <strong>{receiverSites.length} store(s)</strong>
                    </span>
                  </>
                )}
                {showSkuCol && summary?.unique_skus != null && (
                  <>
                    <span className="shrink-0 text-slate-400">·</span>
                    <span className="shrink-0">{summary.unique_skus} SKU(s)</span>
                  </>
                )}
                <span className="shrink-0 text-slate-400">·</span>
                <span className="shrink-0">
                  Total store SOH to clear: <strong>{fmtQty(summary?.total_donor_soh ?? donorSohTotal)}</strong> units
                  across {donors.length} row(s)
                </span>
              </div>
            </div>
          )}

          <section className="rounded-2xl border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900/60">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-4 py-3 dark:border-slate-800">
              <div className="flex items-center gap-2">
                <Truck className="h-4 w-4 text-indigo-600" />
                <h3 className="text-sm font-bold text-slate-900 dark:text-white">
                  Recommended Transfers ({recommendations.length})
                </h3>
              </div>
              {recommendations.length > 0 && (
                <button
                  type="button"
                  disabled={approvingAll}
                  onClick={() => void handleApproveAll()}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-700 disabled:opacity-50"
                >
                  {approvingAll ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <CheckCircle2 className="h-3.5 w-3.5" />}
                  Approve All
                </button>
              )}
            </div>
            {recommendations.length === 0 ? (
              <p className="px-4 py-8 text-center text-sm text-slate-500">
                No clearance transfers needed for this store / SKU selection (no matching receiver demand or no donor stock).
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[900px] text-left text-xs">
                  <thead className="bg-slate-50 text-[10px] font-bold uppercase tracking-wide text-slate-500 dark:bg-slate-800/80">
                    <tr>
                      <th className="px-3 py-2">Donor Site</th>
                      <th className="px-3 py-2">SKU</th>
                      <th className="px-3 py-2">Barcode</th>
                      <th className="px-3 py-2">Receiver</th>
                      <th className="px-3 py-2">Size</th>
                      <th className="px-3 py-2 text-right">Transfer Qty</th>
                      <th className="px-3 py-2 text-right">Donor SOH</th>
                      <th className="px-3 py-2 text-right">Recv SOH</th>
                      <th className="px-3 py-2 text-right">Priority</th>
                      <th className="px-3 py-2 text-right">Action</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                    {recommendations.map((rec) => {
                      const qty = rec.transfer_qty ?? rec.transfer_qty_total;
                      const recId = Number(rec.id);
                      return (
                        <tr key={recId} className="hover:bg-slate-50/80 dark:hover:bg-slate-800/40">
                          <td className="px-3 py-2.5">
                            <StoreNameInline
                              name={rec.donor_store_name}
                              id={rec.donor_store}
                            />
                          </td>
                          <td className="px-3 py-2.5 whitespace-nowrap">{rec.sku || '—'}</td>
                          <td className="px-3 py-2.5 whitespace-nowrap font-mono text-[11px] text-slate-600 dark:text-slate-300">
                            {rec.barcode || '—'}
                          </td>
                          <td className="px-3 py-2.5">
                            <StoreNameInline
                              name={rec.receiver_store_name}
                              id={rec.receiver_store}
                            />
                          </td>
                          <td className="px-3 py-2.5">{rec.size || '—'}</td>
                          <td className="px-3 py-2.5 text-right font-semibold text-indigo-600">{fmtQty(qty)}</td>
                          <td className="px-3 py-2.5 text-right">{fmtQty(rec.donor_soh)}</td>
                          <td className="px-3 py-2.5 text-right">{fmtQty(rec.receiver_soh)}</td>
                          <td className="px-3 py-2.5 text-right">{fmtQty(rec.priority_score)}</td>
                          <td className="px-3 py-2.5 text-right">
                            <button
                              type="button"
                              disabled={!Number.isFinite(recId) || approvingId === recId}
                              onClick={() => void handleApprove(recId)}
                              className="rounded-lg bg-indigo-600 px-2.5 py-1 text-[11px] font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
                            >
                              {approvingId === recId ? '…' : 'Approve'}
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}

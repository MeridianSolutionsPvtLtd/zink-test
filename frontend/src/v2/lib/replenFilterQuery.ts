/** Query string (no leading `?`) for replenishment filter APIs — omit the dimension being listed. */
export type ReplenSisterOmit = 'site' | 'site_id' | 'sku' | 'month' | 'week';

/** Must match lake `refill_source` and `backend-V4` `SITE_TO_SITE_REFILL_SOURCE`. */
export const REPLEN_SITE_TRANSFER_REFILL_SOURCE = 'SITE_TO_SITE_TRANSFER_REQUIRED';

export type ReplenFilterState = {
  repSite: string;
  repSiteId: string;
  repSKU: string;
  repMonth: string;
  repWeek: string;
};

export type ReplenSisterQueryOpts = {
  /** When set (e.g. site→site tab), /filters/* distinct lists match that `refill_source` slice like the table. */
  refillSource?: string | null;
};

export function buildReplenishmentSisterQuery(
  omit: ReplenSisterOmit,
  s: ReplenFilterState,
  opts?: ReplenSisterQueryOpts
): string {
  const p = new URLSearchParams();
  if (omit !== 'site' && s.repSite && s.repSite !== 'All Sites') p.set('site', s.repSite);
  if (omit !== 'site_id' && s.repSiteId && s.repSiteId !== 'All Site IDs') p.set('site_id', s.repSiteId);
  if (omit !== 'sku' && s.repSKU && s.repSKU !== 'All SKUs') p.set('sku', s.repSKU);
  if (omit !== 'month' && s.repMonth && s.repMonth !== 'All Months') p.set('month', s.repMonth);
  if (omit !== 'week' && s.repWeek && s.repWeek !== 'All Weeks' && s.repWeek !== 'All') p.set('week', s.repWeek);
  const rs = (opts?.refillSource ?? '').trim();
  if (rs) p.set('refill_source', rs);
  return p.toString();
}

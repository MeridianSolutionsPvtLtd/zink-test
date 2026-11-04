import { API_URL, readApiJson } from '../../config';

/** Master dropdowns change with the morning file drop, not with every page open. */
const TTL_MS = 6 * 60 * 60 * 1000;

type Entry<T> = { at: number; data: T };

const memory = new Map<string, Entry<unknown>>();
const inflight = new Map<string, Promise<unknown>>();

export const FILTER_CACHE_KEYS = {
  customerMaster: 'sp-customer-master-filters-v1',
  itemMaster: 'sp-item-master-filters-v1',
  financialCalendar: 'sp-financial-calendar-v1',
} as const;

export type ProductFilterLists = {
  ok?: boolean;
  skus?: Array<{ id: string; label: string } | string>;
  seasons?: Array<{ id: string; label: string } | string>;
  barcodes?: string[];
};

/** Stable key so Home and Sales Overview share one season/SKU/barcode fetch. */
export function productFilterCacheKey(query: string): string {
  return `sp-product-filter-options-v1|${query}`;
}

export function productFilterQuery(parts: Record<string, string | undefined>): string {
  const params = new URLSearchParams();
  for (const key of [
    'year',
    'channel',
    'month',
    'zones',
    'states',
    'cities',
    'departments',
    'sections',
    'store_grades',
    'site_id',
    'site_ids',
    'timeline_mode',
    'timeline_values',
  ]) {
    const value = parts[key]?.trim();
    if (!value || value.toLowerCase() === 'all') continue;
    params.set(key, value);
  }
  return params.toString();
}

function fresh<T>(entry: Entry<T> | undefined | null): T | null {
  if (!entry || typeof entry.at !== 'number') return null;
  if (Date.now() - entry.at > TTL_MS) return null;
  return entry.data;
}

export function readCached<T>(key: string): T | null {
  const fromMemory = fresh(memory.get(key) as Entry<T> | undefined);
  if (fromMemory) return fromMemory;
  try {
    const raw = sessionStorage.getItem(key);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Entry<T>;
    const data = fresh(parsed);
    if (!data) return null;
    memory.set(key, parsed);
    return data;
  } catch {
    return null;
  }
}

function remember<T>(key: string, data: T) {
  const entry: Entry<T> = { at: Date.now(), data };
  memory.set(key, entry);
  try {
    sessionStorage.setItem(key, JSON.stringify(entry));
  } catch {
    // Quota — this tab still serves the in-memory copy.
  }
}

/** One network call per key, even if Home and Sales Overview ask at the same time.
 *  A fresh cached copy (within TTL) is returned without touching the network. */
export function fetchOnce<T>(key: string, url: string, accept: (data: T) => boolean): Promise<T> {
  const cached = readCached<T>(key);
  if (cached && accept(cached)) return Promise.resolve(cached);
  const pending = inflight.get(key);
  if (pending) return pending as Promise<T>;
  const run = fetch(url)
    .then((res) => readApiJson<T>(res))
    .then((data) => {
      if (accept(data)) remember(key, data);
      return data;
    })
    .finally(() => {
      inflight.delete(key);
    });
  inflight.set(key, run);
  return run;
}

export function prefetchDashboardFilterLists() {
  void fetchOnce(
    FILTER_CACHE_KEYS.customerMaster,
    `${API_URL}/sales-dashboard/customer-master-filters`,
    (data: { options?: unknown }) => Boolean(data?.options),
  );
  void fetchOnce(
    FILTER_CACHE_KEYS.itemMaster,
    `${API_URL}/sales-dashboard/item-master-filters`,
    (data: { options?: unknown }) => Boolean(data?.options),
  );
  void fetchOnce(
    FILTER_CACHE_KEYS.financialCalendar,
    `${API_URL}/sales-dashboard/financial-calendar`,
    (data: { years?: unknown[] }) => Array.isArray(data?.years) && data.years.length > 0,
  );
  const productQuery = productFilterQuery({
    year: '2027',
    timeline_mode: 'fy_year',
    timeline_values: 'FY27',
  });
  void fetchOnce(
    productFilterCacheKey(productQuery),
    `${API_URL}/sales-dashboard/product-filter-options?${productQuery}`,
    (data: { ok?: boolean; skus?: unknown }) => data?.ok === true && Array.isArray(data.skus),
  );
}

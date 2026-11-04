import type { MonthlyTrendPoint, SalesRow, SalesKpis, TargetSummary, WeeklyTrendPoint, YearlyTrendPoint } from './types';

export function formatInr(value: number | null | undefined) {
  const n = Number(value);
  if (!Number.isFinite(n)) {
    return new Intl.NumberFormat('en-IN', {
      style: 'currency',
      currency: 'INR',
      maximumFractionDigits: 0,
    }).format(0);
  }
  return new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    maximumFractionDigits: 0,
  }).format(n);
}

export function formatCompactInr(value: number | null | undefined) {
  const n = Number(value);
  if (!Number.isFinite(n)) return '—';
  if (n >= 1_00_00_000) return `₹${(n / 1_00_00_000).toFixed(1)}Cr`;
  if (n >= 1_00_000) return `₹${(n / 1_00_000).toFixed(1)}L`;
  if (n >= 1_000) return `₹${(n / 1_000).toFixed(1)}K`;
  return formatInr(n);
}

export function sparklinePath(values: number[], width = 64, height = 24): string {
  if (!values.length) return '';
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const step = width / Math.max(values.length - 1, 1);
  return values
    .map((v, i) => {
      const x = i * step;
      const y = height - ((v - min) / range) * height;
      return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');
}

const STORE_CATEGORIES = ['LTL', 'EXIT', 'NEW'];
const SALES_CHANNELS = ['PT', 'SSL', 'CENTRO', 'ICONIC', 'EBO'];

export function deriveZone(row: SalesRow): string {
  return row.zone && String(row.zone).trim() ? String(row.zone).trim() : '';
}

export function deriveState(row: SalesRow): string {
  return row.state && String(row.state).trim() ? String(row.state).trim() : '';
}

export function deriveCity(row: SalesRow): string {
  return row.city && String(row.city).trim() ? String(row.city).trim() : '';
}

/** Store Status — blank until master provides it. */
export function deriveStoreStatus(_row: SalesRow): string {
  return '';
}

/** Sales Channel — PT / SSL / Centro / Iconic / EBO (from enriched row). */
export function deriveSalesChannel(row: SalesRow): string {
  // 1. Prefer backend-derived sales_channel (display labels or short codes)
  const sc = ((row as any).sales_channel || '').trim().toUpperCase();
  if (['PT', 'SSL', 'CENTRO', 'ICONIC', 'EBO', 'RELIANCE'].includes(sc)) return sc;
  if (sc.includes('SHOPPER') || sc.includes('(SSL)')) return 'SSL';
  if (sc.includes('PANTALOON') || sc.includes('(PT)')) return 'PT';
  if (sc.includes('CENTRO')) return 'CENTRO';
  if (sc.includes('ICONIC')) return 'ICONIC';
  if (sc.includes('EBO') || sc.includes('FRESCO')) return 'EBO';

  // 2. Store name / site label — SS-PUNJAGUTTA must not fall through to LFS→PT
  const label = [
    (row as any).site_id_name,
    row.site_id,
    row.site_name,
    (row as any).customer_name,
  ]
    .map((v) => String(v || '').trim().toUpperCase())
    .join(' ');
  if (/SHOPPERS?|HOPPER|\bSS[- ]|\bSSL\b/.test(label)) return 'SSL';
  if (/PANTALOON|\bPT[- ]|\bPW[- ]|\(P\d{2,5}\)/.test(label)) return 'PT';

  // 3. Fallback: channel / store_type (LFS is format — not a channel; ignore it)
  const raw = (row.channel || (row as any).store_type || row.store_type_master || '')
    .trim()
    .toUpperCase();
  if (raw === 'LFS') return '';
  if (raw === 'PANTALOONS') return 'PT';
  if (raw === 'SHOPPERS STOP' || raw === 'SHOPPERS-STOP' || raw === 'SHOPPERS_STOP') return 'SSL';
  if (['PT', 'SSL', 'CENTRO', 'ICONIC', 'EBO'].includes(raw)) return raw;
  if (raw.includes('PANTA')) return 'PT';
  if (raw.includes('SSL') || raw.includes('SHOPPER')) return 'SSL';
  if (raw.includes('CENTRO')) return 'CENTRO';
  if (raw.includes('ICONIC')) return 'ICONIC';
  if (raw.includes('EBO') || raw.includes('FRESCO')) return 'EBO';
  return raw;
}

/** Customer Format — PT / SSL / CENTRO / ICONIC / EBO (same as sales channel). */
export function deriveCustomerFormat(row: SalesRow): string {
  return deriveSalesChannel(row);
}

/** Canonical company names — collapses SSL spelling variants / EBO person names. */
export function normalizeCustomerName(name: string, channelHint = ''): string {
  const raw = String(name || '').trim();
  const ch = String(channelHint || '').trim().toUpperCase();
  const u = raw.toUpperCase();
  if (u.includes('SHOPPER') || ch === 'SSL') return 'Shoppers Stop Ltd';
  if (u.includes('ICONIC') || ch === 'ICONIC') return 'ICONIC FASHION RETAILING PRIVATE LIMITED';
  if (u.includes('RELIANCE') || u.includes('AJIO') || ch === 'CENTRO') return 'RELIANCE RETAIL LIMITED';
  if (u.includes('ADITYA BIRLA') || u.includes('PANTALOON') || ch === 'PT') {
    if (u.includes('PAHARIA')) return raw;
    return 'M/S Aditya Birla Fashion and Retail Ltd(Frmly Knwn As PRFL)';
  }
  if (u.includes('FRESCO') || ch === 'EBO') return 'Fresco Global Pvt Ltd';
  if (raw && !u.startsWith('XXX') && !u.startsWith('XX_') && !u.startsWith('XX-')) return raw;
  return '';
}

/** Customer display — prefer master CUSTOMER NAME (parent), else site name. */
export function deriveCustomerName(row: SalesRow): string {
  const ch = deriveSalesChannel(row);
  if (row.customer_name && String(row.customer_name).trim()) {
    const n = normalizeCustomerName(String(row.customer_name).trim(), ch);
    if (n) return n;
  }
  const fromChannel = normalizeCustomerName('', ch);
  if (fromChannel) return fromChannel;
  const site = (row.site_name || '').trim();
  if (site) return site;
  return row.site_id;
}

const JUNK_FILTER_VALS = new Set(['', '0', '0.0', 'nan', 'none', '<na>', 'null', 'unassigned', '-', '--']);
const STORE_GRADE_RE = /^(?:\d{0,2})?[A-D]\+?(?:-W\d+)?$/i;

export function isValidStoreGrade(val: string): boolean {
  const s = String(val || '').trim();
  if (!s || JUNK_FILTER_VALS.has(s.toLowerCase())) return false;
  return STORE_GRADE_RE.test(s);
}

export function deriveCluster(row: SalesRow): string {
  const c = row.cluster && String(row.cluster).trim() ? String(row.cluster).trim() : '';
  if (!c || JUNK_FILTER_VALS.has(c.toLowerCase())) return '';
  return c;
}

export function deriveAreaManager(_row: SalesRow): string {
  return '';
}

export function deriveRegionalManager(row: SalesRow): string {
  return deriveCluster(row);
}

export function deriveDepartment(row: SalesRow): string {
  const d = (row.department || '').trim();
  return d;
}

/** Product section from sales (Dress / Top / Outerwear / …). */
export function deriveSection(row: SalesRow): string {
  return (row.section || '').trim();
}

/** Kept for charts — blank until product master is joined. */
export function deriveCategory(_row: SalesRow): string {
  return '';
}

export function deriveStory(_row: SalesRow): string {
  return '';
}

/** Store Category (LTL / NEW / EXIT) from customer master LTL/New. */
export function deriveStoreCategory(row: SalesRow): string {
  const raw = (row.store_category || '').trim().toUpperCase();
  if (raw === 'LTL' || raw === 'EXIT' || raw === 'NEW') return raw;
  return '';
}

/** Store Grade from STORE_GRADE file (via API enrich). */
export function deriveStoreGrade(row: SalesRow): string {
  if (row.store_grade && String(row.store_grade).trim()) {
    const g = String(row.store_grade).trim();
    return isValidStoreGrade(g) ? g : '';
  }
  return '';
}

export type ClientFilters = {
  zones: string[];
  siteCodes: string[];
  siteNames: string[];
  storeCategories: string[];
  storeStatuses: string[];
  salesChannels: string[];
  customerFormats: string[];
  customerNames: string[];
  cities: string[];
  states: string[];
  clusters: string[];
  areaManagers: string[];
  regionalManagers: string[];
  clusterRm: string[];
  storeGrades: string[];
  departments: string[];
  sections: string[];
  skus: string[];
  barcodes: string[];
  seasons: string[];
};

export const EMPTY_CLIENT_FILTERS: ClientFilters = {
  zones: [],
  siteCodes: [],
  siteNames: [],
  storeCategories: [],
  storeStatuses: [],
  salesChannels: [],
  customerFormats: [],
  customerNames: [],
  cities: [],
  states: [],
  clusters: [],
  areaManagers: [],
  regionalManagers: [],
  clusterRm: [],
  storeGrades: [],
  departments: [],
  sections: [],
  skus: [],
  barcodes: [],
  seasons: [],
};

export const CLIENT_FILTER_OPTIONS = {
  zones: [] as string[],
  states: [] as string[],
  cities: [] as string[],
  storeGrades: [] as string[],
  storeCategories: STORE_CATEGORIES,
  storeStatuses: [] as string[],
  salesChannels: [...SALES_CHANNELS],
  customerFormats: [] as string[],
  clusters: [] as string[],
  areaManagers: [] as string[],
  clusterRm: [] as string[],
  regionalManagers: [] as string[],
};

export function hasActiveClientFilters(filters: ClientFilters): boolean {
  return Object.values(filters).some((values) => values.length > 0);
}

export type CustomerMasterSite = {
  site_key: string;
  site_code: string;
  site_name: string;
  zone: string;
  city: string;
  state: string;
  cluster: string;
  store_category: string;
  store_grade: string;
  customer_name: string;
  channel: string;
  store_type_master: string;
};

export type CustomerMasterFilterOptions = {
  zones: string[];
  siteCodes: string[];
  siteNames: string[];
  storeCategories: string[];
  salesChannels: string[];
  customerFormats: string[];
  customerNames: string[];
  cities: string[];
  states: string[];
  clusterRm: string[];
  storeGrades: string[];
};

export type CustomerMasterFiltersResponse = {
  ok: boolean;
  detail?: string;
  sites: CustomerMasterSite[];
  options: CustomerMasterFilterOptions;
  row_count?: number;
};

export type ItemMasterFilterOptions = {
  departments: string[];
  sections: string[];
};

export type ItemMasterFiltersResponse = {
  ok: boolean;
  detail?: string;
  options: ItemMasterFilterOptions;
  sections_by_department?: Record<string, string[]>;
  row_count?: number;
  source?: string;
};

export const MASTER_SALES_CHANNELS = ['PT', 'SSL', 'CENTRO', 'ICONIC', 'EBO'] as const;

function normChannel(v: string): string {
  const u = v.trim().toUpperCase();
  if (u === 'PANTALOONS') return 'PT';
  if (u === 'SHOPPERS STOP' || u === 'SHOPPERS-STOP' || u === 'SHOPPERS_STOP') return 'SSL';
  return u;
}

function uniqSorted(vals: string[]) {
  return [
    ...new Set(
      vals
        .map((v) => (v || '').trim())
        .filter((v) => v && !JUNK_FILTER_VALS.has(v.toLowerCase()))
    ),
  ].sort((a, b) => a.localeCompare(b));
}

/** Site Code = Customer Master SITE MANUAL CODE only (no SITECODE fallback). */
function siteCodeOf(site: CustomerMasterSite) {
  return (site.site_code || '').trim();
}

/** Sites matching all active master filters except one dimension (for cascading dropdowns). */
function filterSitesForCascading(
  sites: CustomerMasterSite[],
  filters: ClientFilters,
  exclude?: keyof ClientFilters
): CustomerMasterSite[] {
  if (!sites.length) return [];
  const scoped: ClientFilters = { ...filters };
  if (exclude) scoped[exclude] = [];
  const allowed = resolveSiteKeysFromMasterFilters(sites, scoped);
  if (allowed === null) return sites;
  return sites.filter((s) => allowed.has(s.site_key));
}

/** Rows matching all active filters except one dimension (for SKU / barcode / season cascading). */
function filterRowsForCascading(
  rows: SalesRow[],
  sites: CustomerMasterSite[],
  filters: ClientFilters,
  exclude?: keyof ClientFilters
): SalesRow[] {
  const scoped: ClientFilters = { ...filters };
  if (exclude) scoped[exclude] = [];
  return applyClientFilters(rows, scoped, sites.length ? sites : undefined);
}

/** Item Master Department / Section options; sections narrow when departments are selected. */
export function buildItemMasterFilterOptions(
  payload: ItemMasterFiltersResponse | null,
  filters: ClientFilters
): ItemMasterFilterOptions {
  const opts = payload?.options;
  if (!opts) return { departments: [], sections: [] };

  let sections = opts.sections ?? [];
  if (filters.departments.length && payload?.sections_by_department) {
    const secSet = new Set<string>();
    filters.departments.forEach((d) => {
      (payload.sections_by_department?.[d] ?? []).forEach((s) => secSet.add(s));
    });
    sections = [...secSet].sort((a, b) => a.localeCompare(b));
  }

  return {
    departments: opts.departments ?? [],
    sections,
  };
}

/** Build filter dropdown options purely from Customer Master API payload (no cascading). */
export function buildClientFilterOptionsFromMaster(payload: CustomerMasterFiltersResponse | null) {
  return buildCascadingFilterOptions(payload, EMPTY_CLIENT_FILTERS, []);
}

/** Cascading filter options — each dropdown reflects all other active selections. */
export function buildCascadingFilterOptions(
  payload: CustomerMasterFiltersResponse | null,
  filters: ClientFilters,
  rows: SalesRow[],
  itemMaster?: ItemMasterFiltersResponse | null
) {
  const sites = payload?.sites ?? [];
  const opts = payload?.options;
  const baseChannels = opts?.salesChannels?.length ? opts.salesChannels : [...MASTER_SALES_CHANNELS];

  const zones = uniqSorted(filterSitesForCascading(sites, filters, 'zones').map((s) => s.zone));
  const siteCodes = uniqSorted(
    filterSitesForCascading(sites, filters, 'siteCodes').map(siteCodeOf).filter(Boolean)
  );
  const siteNames = uniqSorted(filterSitesForCascading(sites, filters, 'siteNames').map((s) => s.site_name));
  const storeCategories = uniqSorted(
    filterSitesForCascading(sites, filters, 'storeCategories').map((s) => s.store_category)
  );
  const salesChannels = uniqSorted(
    filterSitesForCascading(sites, filters, 'salesChannels').map((s) => normChannel(s.channel))
  );
  const customerNames = uniqSorted(
    filterSitesForCascading(sites, filters, 'customerNames')
      .map((s) => normalizeCustomerName(s.customer_name, s.channel))
      .filter(Boolean)
  );
  const cities = uniqSorted(filterSitesForCascading(sites, filters, 'cities').map((s) => s.city));
  const states = uniqSorted(filterSitesForCascading(sites, filters, 'states').map((s) => s.state));
  const clusterRm = uniqSorted(filterSitesForCascading(sites, filters, 'clusterRm').map((s) => s.cluster));
  const storeGrades = uniqSorted(
    filterSitesForCascading(sites, filters, 'storeGrades')
      .map((s) => s.store_grade)
      .filter((g) => isValidStoreGrade(String(g || '')))
  );

  const skuRows = filterRowsForCascading(rows, sites, filters, 'skus');
  const skus = uniqSorted(skuRows.map((r) => r.sku));
  const barcodes = uniqSorted(filterRowsForCascading(rows, sites, filters, 'barcodes').map((r) => r.barcode));
  const seasons = uniqSorted(filterRowsForCascading(rows, sites, filters, 'seasons').map((r) => r.season));

  const itemOpts = buildItemMasterFilterOptions(itemMaster ?? null, filters);

  const fallbackCustomerNames = (opts?.customerNames ?? [])
    .map((n) => normalizeCustomerName(n))
    .filter(Boolean);

  return {
    zones: zones.length ? zones : opts?.zones ?? [],
    states: states.length ? states : opts?.states ?? [],
    cities: cities.length ? cities : opts?.cities ?? [],
    storeGrades: storeGrades.length
      ? storeGrades
      : (opts?.storeGrades ?? []).filter((g) => isValidStoreGrade(String(g || ''))),
    storeCategories: storeCategories.length ? storeCategories : STORE_CATEGORIES,
    storeStatuses: [] as string[],
    salesChannels: salesChannels.length ? salesChannels : baseChannels,
    customerFormats: (opts?.customerFormats?.length ? opts.customerFormats : baseChannels) ?? baseChannels,
    clusters: clusterRm,
    areaManagers: [] as string[],
    clusterRm: clusterRm.length ? clusterRm : opts?.clusterRm ?? [],
    regionalManagers: [] as string[],
    customerNames: customerNames.length ? customerNames : uniqSorted(fallbackCustomerNames),
    siteCodes: siteCodes.length ? siteCodes : opts?.siteCodes ?? [],
    siteNames: siteNames.length ? siteNames : opts?.siteNames ?? [],
    departments: itemOpts.departments,
    sections: itemOpts.sections,
    skus,
    barcodes,
    seasons,
  };
}

/** Resolve allowed site keys from Customer Master when dim filters are active. */
export function resolveSiteKeysFromMasterFilters(
  sites: CustomerMasterSite[],
  filters: ClientFilters
): Set<string> | null {
  const masterKeys: (keyof ClientFilters)[] = [
    'zones',
    'siteCodes',
    'siteNames',
    'storeCategories',
    'salesChannels',
    'customerFormats',
    'customerNames',
    'cities',
    'states',
    'clusters',
    'clusterRm',
    'storeGrades',
  ];
  const active = masterKeys.some((k) => filters[k].length > 0);
  if (!active || !sites.length) return null;

  let allowed: Set<string> | null = null;
  const intersect = (next: Set<string>) => {
    allowed = allowed === null ? next : new Set([...allowed].filter((k) => next.has(k)));
  };

  if (filters.zones.length) {
    intersect(new Set(sites.filter((s) => filters.zones.includes(s.zone)).map((s) => s.site_key)));
  }
  if (filters.siteCodes.length) {
    const codeSet = new Set(filters.siteCodes.map((c) => c.trim()).filter(Boolean));
    intersect(
      new Set(
        sites
          .filter((s) => {
            const manual = (s.site_code || '').trim();
            return manual && codeSet.has(manual);
          })
          .map((s) => s.site_key)
      )
    );
  }
  if (filters.siteNames.length) {
    intersect(new Set(sites.filter((s) => filters.siteNames.includes(s.site_name)).map((s) => s.site_key)));
  }
  if (filters.storeCategories.length) {
    const catSet = new Set(filters.storeCategories.map((c) => c.trim().toUpperCase()));
    intersect(
      new Set(
        sites
          .filter((s) => catSet.has(String(s.store_category || '').trim().toUpperCase()))
          .map((s) => s.site_key)
      )
    );
  }
  if (filters.salesChannels.length) {
    const chSet = new Set(filters.salesChannels.map(normChannel));
    intersect(new Set(sites.filter((s) => chSet.has(normChannel(s.channel))).map((s) => s.site_key)));
  }
  if (filters.customerNames.length) {
    const custSet = new Set(filters.customerNames.map((n) => normalizeCustomerName(n)).filter(Boolean));
    // Also accept raw selected labels for backward compatibility
    filters.customerNames.forEach((n) => {
      if (n.trim()) custSet.add(n.trim());
    });
    intersect(
      new Set(
        sites
          .filter((s) => {
            const canon = normalizeCustomerName(s.customer_name, s.channel);
            return (
              custSet.has(canon) ||
              custSet.has(String(s.customer_name || '').trim()) ||
              filters.customerNames.includes(s.site_name)
            );
          })
          .map((s) => s.site_key)
      )
    );
  }
  if (filters.customerFormats.length) {
    const fmtSet = new Set(filters.customerFormats.map(normChannel));
    intersect(
      new Set(
        sites
          .filter(
            (s) =>
              fmtSet.has(normChannel(s.channel)) ||
              filters.customerFormats.includes(s.store_type_master)
          )
          .map((s) => s.site_key)
      )
    );
  }
  if (filters.cities.length) {
    intersect(new Set(sites.filter((s) => filters.cities.includes(s.city)).map((s) => s.site_key)));
  }
  if (filters.states.length) {
    intersect(new Set(sites.filter((s) => filters.states.includes(s.state)).map((s) => s.site_key)));
  }
  if (filters.clusters.length) {
    intersect(new Set(sites.filter((s) => filters.clusters.includes(s.cluster)).map((s) => s.site_key)));
  }
  if (filters.clusterRm.length) {
    intersect(new Set(sites.filter((s) => filters.clusterRm.includes(s.cluster)).map((s) => s.site_key)));
  }
  if (filters.storeGrades.length) {
    intersect(
      new Set(sites.filter((s) => filters.storeGrades.includes(s.store_grade)).map((s) => s.site_key))
    );
  }

  return allowed ?? new Set();
}

/** Unique values from enriched sales rows only — no static fallbacks. */
export function buildClientFilterOptionsFromRows(rows: SalesRow[]) {
  return {
    ...CLIENT_FILTER_OPTIONS,
    zones: uniqSorted(rows.map(deriveZone)),
    cities: uniqSorted(rows.map(deriveCity)),
    states: uniqSorted(rows.map(deriveState)),
    clusters: uniqSorted(rows.map(deriveCluster)),
    clusterRm: uniqSorted(rows.map(deriveCluster)),
    storeGrades: uniqSorted(rows.map(deriveStoreGrade)),
    storeCategories: uniqSorted(rows.map(deriveStoreCategory)),
    salesChannels: uniqSorted(rows.map(deriveSalesChannel)),
    customerNames: uniqSorted(rows.map(deriveCustomerName)),
    skus: uniqSorted(rows.map((r) => r.sku)),
    barcodes: uniqSorted(rows.map((r) => r.barcode)),
    seasons: uniqSorted(rows.map((r) => r.season)),
  };
}

/** Match sales site_id / site_name against Customer Master site_key set (manual codes). */
function rowMatchesMasterSiteKeys(row: SalesRow, allowed: Set<string>): boolean {
  const sid = String(row.site_id || '').trim();
  const sname = String(row.site_name || '').trim();
  if (sid && allowed.has(sid)) return true;
  const sidU = sid.toUpperCase();
  if (sidU && allowed.has(sidU)) return true;
  const tokens = new Set<string>();
  for (const raw of [sid, sname]) {
    if (!raw) continue;
    const u = raw.toUpperCase();
    tokens.add(u);
    const paren = u.match(/\(\s*([A-Z0-9]+)\s*\)/g) || [];
    paren.forEach((m) => {
      const code = m.replace(/[()]/g, '').trim();
      if (code) tokens.add(code);
    });
    const tail = u.match(/[-_\s]([A-Z0-9]+)$/);
    if (tail?.[1]) tokens.add(tail[1]);
    // SS-MALAD-114 → 114
    const dashNum = u.match(/-(\d{2,5})$/);
    if (dashNum?.[1]) tokens.add(dashNum[1]);
  }
  for (const t of tokens) {
    if (allowed.has(t)) return true;
  }
  return false;
}

export function applyClientFilters(
  rows: SalesRow[],
  filters: ClientFilters,
  masterSites?: CustomerMasterSite[]
): SalesRow[] {
  const allowedKeys = masterSites?.length
    ? resolveSiteKeysFromMasterFilters(masterSites, filters)
    : null;

  return rows.filter((row) => {
    if (allowedKeys !== null && !rowMatchesMasterSiteKeys(row, allowedKeys)) return false;

    // When master sites not loaded, fall back to row-level derive matching
    if (allowedKeys === null) {
      if (filters.zones.length && !filters.zones.includes(deriveZone(row))) return false;
      if (filters.siteCodes.length && !filters.siteCodes.includes(row.site_id)) return false;
      if (filters.siteNames.length && !filters.siteNames.includes(row.site_name)) return false;
      if (filters.storeCategories.length && !filters.storeCategories.includes(deriveStoreCategory(row)))
        return false;
      if (filters.salesChannels.length && !filters.salesChannels.includes(deriveSalesChannel(row))) return false;
      if (filters.customerFormats.length && !filters.customerFormats.includes(deriveCustomerFormat(row)))
        return false;
      if (
        filters.customerNames.length &&
        !filters.customerNames.includes(row.site_name) &&
        !filters.customerNames.some(
          (n) =>
            n === deriveCustomerName(row) ||
            normalizeCustomerName(n) === deriveCustomerName(row)
        )
      )
        return false;
      if (filters.cities.length && !filters.cities.includes(deriveCity(row))) return false;
      if (filters.states.length && !filters.states.includes(deriveState(row))) return false;
      if (filters.clusters.length && !filters.clusters.includes(deriveCluster(row))) return false;
      if (filters.clusterRm.length && !filters.clusterRm.includes(deriveCluster(row))) return false;
      if (filters.storeGrades.length && !filters.storeGrades.includes(deriveStoreGrade(row))) return false;
    }

    if (filters.storeStatuses.length && !filters.storeStatuses.includes(deriveStoreStatus(row))) return false;
    if (filters.areaManagers.length && !filters.areaManagers.includes(deriveAreaManager(row))) return false;
    if (filters.regionalManagers.length && !filters.regionalManagers.includes(deriveRegionalManager(row)))
      return false;
    if (filters.skus.length) {
      const skuSet = new Set(filters.skus.map((s) => s.trim().toUpperCase()));
      if (!skuSet.has(String(row.sku || '').trim().toUpperCase())) return false;
    }
    if (filters.barcodes.length) {
      const bcSet = new Set(
        filters.barcodes.map((b) => String(b).trim().toUpperCase().replace(/\.0$/, ''))
      );
      if (!bcSet.has(String(row.barcode || '').trim().toUpperCase().replace(/\.0$/, ''))) return false;
    }
    if (filters.seasons.length) {
      const seasonSet = new Set(filters.seasons.map((s) => s.trim().toUpperCase()));
      if (!seasonSet.has(String(row.season || '').trim().toUpperCase())) return false;
    }
    if (filters.departments.length && !filters.departments.includes(deriveDepartment(row))) return false;
    if (filters.sections.length && !filters.sections.includes(deriveSection(row))) return false;
    return true;
  });
}

export function buildMonthlyTrendFromRows(rows: SalesRow[]): MonthlyTrendPoint[] {
  const buckets = new Map<string, { net: number; gross: number; discount: number; units: number }>();
  rows.forEach((r) => {
    const d = new Date(r.date);
    if (isNaN(d.getTime())) return;
    const month = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
    const cur = buckets.get(month) ?? { net: 0, gross: 0, discount: 0, units: 0 };
    const gross = r.mrp * r.qty;
    const disc = r.discount_amount ?? Math.max(0, gross - r.net_sale_amount);
    cur.net += r.net_sale_amount;
    cur.gross += gross;
    cur.discount += disc;
    cur.units += r.qty;
    buckets.set(month, cur);
  });
  return [...buckets.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([month, v]) => ({
      month,
      label: new Date(`${month}-01T00:00:00`).toLocaleDateString('en-IN', {
        month: 'short',
        year: 'numeric',
      }),
      net_sales: v.net,
      gross_sales: v.gross,
      discount_amount: v.discount,
      units: v.units,
    }));
}

/** Calendar Mon-start weekly net/gross series from row dates (matches backend weekly_trend). */
export function buildWeeklyTrendFromRows(rows: SalesRow[]): WeeklyTrendPoint[] {
  const buckets = new Map<string, { net: number; gross: number; units: number }>();
  rows.forEach((r) => {
    const d = new Date(`${r.date}T00:00:00`);
    if (isNaN(d.getTime())) return;
    const ws = weekStart(d);
    const week = ws.toISOString().slice(0, 10);
    const cur = buckets.get(week) ?? { net: 0, gross: 0, units: 0 };
    const gross = r.mrp * r.qty;
    cur.net += r.net_sale_amount;
    cur.gross += gross;
    cur.units += r.qty;
    buckets.set(week, cur);
  });
  return [...buckets.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([week, v]) => ({
      week,
      label: `Wk ${new Date(`${week}T00:00:00`).toLocaleDateString('en-IN', { day: '2-digit', month: 'short' })}`,
      net_sales: v.net,
      gross_sales: v.gross,
      units: v.units,
    }));
}

/** Real month-over-month growth (GOLM), computed from the backend's real monthly_trend series. */
export type MonthGrowth = {
  gross: number | null;
  net: number | null;
  discount: number | null;
  units: number | null;
  asp: number | null;
};

function pctChange(cur: number, prev: number): number | null {
  if (!prev) return null;
  return ((cur - prev) / prev) * 100;
}

export function computeMonthGrowth(monthlyTrendData: MonthlyTrendPoint[]): MonthGrowth | null {
  if (monthlyTrendData.length < 2) return null;
  const cur = monthlyTrendData[monthlyTrendData.length - 1];
  const prev = monthlyTrendData[monthlyTrendData.length - 2];
  const curAsp = cur.units > 0 ? cur.net_sales / cur.units : 0;
  const prevAsp = prev.units > 0 ? prev.net_sales / prev.units : 0;
  return {
    gross: pctChange(cur.gross_sales, prev.gross_sales),
    net: pctChange(cur.net_sales, prev.net_sales),
    discount: pctChange(cur.discount_amount, prev.discount_amount),
    units: pctChange(cur.units, prev.units),
    asp: pctChange(curAsp, prevAsp),
  };
}

/** Week-over-week net sales growth (GOLW) from calendar Mon-start weekly series. */
export function computeWeekGrowth(weeklyTrendData: WeeklyTrendPoint[]): number | null {
  if (weeklyTrendData.length < 2) return null;
  const cur = weeklyTrendData[weeklyTrendData.length - 1];
  const prev = weeklyTrendData[weeklyTrendData.length - 2];
  return pctChange(cur.net_sales, prev.net_sales);
}

export type GrowthRates = {
  goly: number | null;
  golyUnits?: number | null;
  golm: number | null;
  golw: number | null;
};

/** GOLY / GOLM / GOLW from backend trend series (full filtered dataset), with row fallback. */
export function computeSalesGrowthRates(
  monthlyTrendData: MonthlyTrendPoint[],
  rows: SalesRow[],
  weeklyTrendData: WeeklyTrendPoint[] = [],
  yearlyTrendData: YearlyTrendPoint[] = []
): GrowthRates {
  const golm = computeMonthGrowth(monthlyTrendData)?.net ?? null;

  let goly: number | null = null;
  let golyUnits: number | null = null;

  // 1. Calculate SPLY YoY growth from rows matching elapsed months
  if (rows && rows.length > 0) {
    const yearsMap: Record<number, { net: number; units: number; months: Set<number> }> = {};
    for (const r of rows) {
      const d = r.date ? new Date(r.date) : null;
      const yr = d && !isNaN(d.getTime()) ? d.getFullYear() : ((r as any).year ? Number((r as any).year) : null);
      const mo = d && !isNaN(d.getTime()) ? d.getMonth() + 1 : ((r as any).month ? Number((r as any).month) : null);
      if (yr && yr > 2000) {
        if (!yearsMap[yr]) yearsMap[yr] = { net: 0, units: 0, months: new Set() };
        yearsMap[yr].net += r.net_sale_amount || 0;
        yearsMap[yr].units += r.qty || 0;
        if (mo) yearsMap[yr].months.add(mo);
      }
    }
    const years = Object.keys(yearsMap).map(Number).sort((a, b) => a - b);
    if (years.length >= 2) {
      const curYr = years[years.length - 1];
      const prevYr = years[years.length - 2];
      const curMonths = yearsMap[curYr].months;

      let prevNetSply = 0;
      let prevUnitsSply = 0;
      if (curMonths.size > 0 && curMonths.size < 12) {
        for (const r of rows) {
          const d = r.date ? new Date(r.date) : null;
          const yr = d && !isNaN(d.getTime()) ? d.getFullYear() : ((r as any).year ? Number((r as any).year) : null);
          const mo = d && !isNaN(d.getTime()) ? d.getMonth() + 1 : ((r as any).month ? Number((r as any).month) : null);
          if (yr === prevYr && curMonths.has(mo!)) {
            prevNetSply += r.net_sale_amount || 0;
            prevUnitsSply += r.qty || 0;
          }
        }
      } else {
        prevNetSply = yearsMap[prevYr].net;
        prevUnitsSply = yearsMap[prevYr].units;
      }

      if (prevNetSply > 0) {
        goly = Math.round((((yearsMap[curYr].net - prevNetSply) / prevNetSply) * 100) * 10) / 10;
      }
      if (prevUnitsSply > 0) {
        golyUnits = Math.round((((yearsMap[curYr].units - prevUnitsSply) / prevUnitsSply) * 100) * 10) / 10;
      }
    }
  }

  if (goly === null && monthlyTrendData.length >= 13) {
    const cur = monthlyTrendData[monthlyTrendData.length - 1];
    const prevYear = monthlyTrendData[monthlyTrendData.length - 13];
    goly = pctChange(cur.net_sales, prevYear.net_sales);
    golyUnits = pctChange(cur.units, prevYear.units);
  }
  if (goly === null && yearlyTrendData.length >= 2) {
    const cur = yearlyTrendData[yearlyTrendData.length - 1];
    const prev = yearlyTrendData[yearlyTrendData.length - 2];
    goly = pctChange(cur.net_sales, prev.net_sales);
    golyUnits = pctChange((cur as any).units || 0, (prev as any).units || 0);
  }

  let golw: number | null = null;
  if (weeklyTrendData.length >= 2) {
    golw = computeWeekGrowth(weeklyTrendData);
  } else {
    const weekly = buildWeeklyTrendFromRows(rows);
    if (weekly.length >= 2) {
      golw = computeWeekGrowth(weekly);
    } else {
      const legacy = buildRealPeriodSeries(rows, 'weekly');
      golw =
        legacy.length >= 2
          ? pctChange(legacy[legacy.length - 1].actual, legacy[legacy.length - 2].actual)
          : null;
    }
  }

  return { goly, golyUnits, golm, golw };
}

/**
 * Map Sales Overview KPI grid from backend `/sales-dashboard` kpis only.
 * Never re-aggregate from the current table page — that undercounts hosted KPIs.
 */
export function computeExtendedKpis(kpis: SalesKpis | null, _rows: SalesRow[] = []) {
  if (!kpis) {
    return {
      gross: 0,
      net: 0,
      discount: 0,
      gst: 0,
      units: 0,
      mrpValue: 0,
      costValue: 0,
      asp: 0,
      trend: [] as number[],
      currentSoh: 0,
      sellThroughPct: 0,
      fullPricePct: 0,
      fullPriceSales: 0,
      fullPriceQty: 0,
      fullPriceStrPct: 0,
      discountSales: 0,
      discountQty: 0,
      discountPricePct: 0,
      discountStrPct: 0,
      salesPerStock: 0,
      salesPerSqft: 0,
      sqftTotal: 0,
      sqftStoresCount: 0,
      sqftNetSales: 0,
      yoySalesGrowthPct: undefined as number | undefined,
      yoyQtyGrowthPct: undefined as number | undefined,
    };
  }

  const gross = Number(kpis.gross_sales) || 0;
  const net = Number(kpis.net_sales) || 0;
  const units = Number(kpis.units_sold) || 0;
  // Backend-only mapping — do not invent discount / ASP / full-price splits on the UI.
  const discount = Number(kpis.discount_given) || 0;
  const currentSoh =
    kpis.current_soh != null && Number.isFinite(Number(kpis.current_soh))
      ? Number(kpis.current_soh)
      : 0;
  const fullPricePct = Number(kpis.full_price_pct) || 0;
  const fullPriceSales = Number(kpis.full_price_sales) || 0;
  const discountPricePct = Number(kpis.discount_price_pct) || 0;
  const discountSales = Number(kpis.discount_sales) || 0;

  return {
    gross,
    net,
    discount,
    gst: Number(kpis.gst) || 0,
    units,
    mrpValue: gross,
    costValue: 0,
    asp: Number(kpis.asp) || 0,
    trend: [] as number[],
    currentSoh,
    sellThroughPct: Number(kpis.sell_through_pct) || 0,
    fullPricePct,
    fullPriceSales,
    fullPriceQty: Number(kpis.full_price_qty) || 0,
    fullPriceStrPct: Number(kpis.full_price_str_pct) || 0,
    discountSales,
    discountQty: Number(kpis.discount_qty) || 0,
    discountPricePct,
    discountStrPct: Number(kpis.discount_str_pct) || 0,
    salesPerStock: Number(kpis.sales_per_stock) || 0,
    salesPerSqft: Number(kpis.sales_per_sqft) || 0,
    sqftTotal: Number(kpis.sqft_total) || 0,
    sqftStoresCount: Number(kpis.sqft_stores_count) || 0,
    sqftNetSales: Number(kpis.sqft_net_sales) || 0,
    yoySalesGrowthPct: kpis.yoy_sales_growth_pct,
    yoyQtyGrowthPct: kpis.yoy_qty_growth_pct,
  };
}

/** Full price vs discounted sale ratio — % of net sales value sold at <= 10% discount. */
export function computeFullPricePct(rows: SalesRow[]): number {
  if (!rows.length) return 0;
  const total = rows.reduce((s, r) => s + r.net_sale_amount, 0);
  if (!total) return 0;
  const fullPrice = rows
    .filter((r) => (r.discount ?? 0) <= 10.0)
    .reduce((s, r) => s + r.net_sale_amount, 0);
  return (fullPrice / total) * 100;
}

/** ROS (Rate of Sale) — average units sold per selling day across the current filtered rows. */
export function computeRos(rows: SalesRow[]): number {
  if (!rows.length) return 0;
  const days = new Set(rows.map((r) => r.date)).size || 1;
  const units = rows.reduce((s, r) => s + r.qty, 0);
  return units / days;
}

export function aggregateByKey(
  rows: SalesRow[],
  keyFn: (r: SalesRow) => string
): { name: string; value: number; units: number }[] {
  const map = new Map<string, { value: number; units: number }>();
  rows.forEach((r) => {
    const k = keyFn(r);
    const cur = map.get(k) ?? { value: 0, units: 0 };
    cur.value += r.net_sale_amount;
    cur.units += r.qty;
    map.set(k, cur);
  });
  return [...map.entries()]
    .map(([name, v]) => ({ name, ...v }))
    .sort((a, b) => b.value - a.value);
}

/** Indian fiscal year (Apr–Mar). Returns the FY-ending calendar year, e.g. Aug 2025 → 2026 ("FY26"). */
function fiscalYearEnd(d: Date): number {
  return d.getMonth() >= 3 ? d.getFullYear() + 1 : d.getFullYear();
}

/** Fiscal quarter index 1–4 (Q1 = Apr–Jun … Q4 = Jan–Mar). */
function fiscalQuarter(d: Date): number {
  const m = d.getMonth();
  if (m >= 3 && m <= 5) return 1;
  if (m >= 6 && m <= 8) return 2;
  if (m >= 9 && m <= 11) return 3;
  return 4;
}

/** Monday-anchored week start, for weekly bucketing. */
function weekStart(d: Date): Date {
  const copy = new Date(d);
  const day = copy.getDay();
  const diff = (day === 0 ? -6 : 1) - day; // shift to Monday
  copy.setDate(copy.getDate() + diff);
  copy.setHours(0, 0, 0, 0);
  return copy;
}

/** Filter rows to the latest week / calendar month / fiscal year for breakdown charts. */
export function filterRowsForPeriod(
  rows: SalesRow[],
  period: 'weekly' | 'monthly' | 'yearly'
): { rows: SalesRow[]; periodLabel: string } {
  const parsed = rows
    .map((r) => ({ r, d: new Date(r.date) }))
    .filter((x) => !isNaN(x.d.getTime()));
  if (!parsed.length) return { rows: [], periodLabel: 'No data' };

  const latest = parsed.reduce((a, b) => (a.d > b.d ? a : b)).d;

  if (period === 'weekly') {
    const ws = weekStart(latest);
    const key = ws.toISOString().slice(0, 10);
    const filtered = parsed
      .filter((x) => weekStart(x.d).toISOString().slice(0, 10) === key)
      .map((x) => x.r);
    return {
      rows: filtered,
      periodLabel: `Week of ${ws.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' })}`,
    };
  }

  if (period === 'monthly') {
    const ym = `${latest.getFullYear()}-${String(latest.getMonth() + 1).padStart(2, '0')}`;
    const filtered = parsed
      .filter((x) => `${x.d.getFullYear()}-${String(x.d.getMonth() + 1).padStart(2, '0')}` === ym)
      .map((x) => x.r);
    return {
      rows: filtered,
      periodLabel: latest.toLocaleDateString('en-IN', { month: 'long', year: 'numeric' }),
    };
  }

  const fy = fiscalYearEnd(latest);
  const filtered = parsed.filter((x) => fiscalYearEnd(x.d) === fy).map((x) => x.r);
  return { rows: filtered, periodLabel: `FY${String(fy).slice(-2)} (Apr–Mar)` };
}

export type TrendPoint = { label: string; actual: number; key: string };

/** Timeline / time-frame modes for Sales Overview */
export type TimelineMode =
  | 'date'
  | 'day'
  | 'month'
  | 'year'
  | 'fy_week'
  | 'fy_year'
  | 'event';

export const TIMELINE_MODE_GROUPS: {
  title: string;
  modes: { id: TimelineMode; label: string }[];
}[] = [
  {
    title: 'Standard Intervals',
    modes: [
      { id: 'date', label: 'Date' },
      { id: 'day', label: 'Day' },
      { id: 'month', label: 'Month' },
      { id: 'year', label: 'Year (Jan–Dec)' },
    ],
  },
  {
    title: 'Retail & Fiscal',
    modes: [
      { id: 'fy_week', label: 'FY Week' },
      { id: 'fy_year', label: 'FY Year (Apr–Mar)' },
    ],
  },
  {
    title: 'Special Events',
    modes: [{ id: 'event', label: 'Events' }],
  },
];

export const SPECIAL_EVENTS: {
  id: string;
  label: string;
  /** Return true if date falls in this retail event window */
  match: (d: Date) => boolean;
}[] = [
  {
    id: 'diwali',
    label: 'Diwali / Festive (Oct–Nov)',
    match: (d) => d.getMonth() === 9 || d.getMonth() === 10,
  },
  {
    id: 'eos_ss',
    label: 'EOS Sale · Summer (Jun–Jul)',
    match: (d) => d.getMonth() === 5 || d.getMonth() === 6,
  },
  {
    id: 'eos_aw',
    label: 'EOS Sale · Winter (Jan–Feb)',
    match: (d) => d.getMonth() === 0 || d.getMonth() === 1,
  },
  {
    id: 'wedding',
    label: 'Wedding Season (Nov–Feb)',
    match: (d) => d.getMonth() === 10 || d.getMonth() === 11 || d.getMonth() === 0 || d.getMonth() === 1,
  },
  {
    id: 'republic',
    label: 'Republic Day (Jan)',
    match: (d) => d.getMonth() === 0 && d.getDate() >= 15,
  },
  {
    id: 'independence',
    label: 'Independence Day (Aug)',
    match: (d) => d.getMonth() === 7 && d.getDate() >= 5 && d.getDate() <= 20,
  },
  {
    id: 'newyear',
    label: 'New Year (Dec–Jan)',
    match: (d) => (d.getMonth() === 11 && d.getDate() >= 20) || (d.getMonth() === 0 && d.getDate() <= 5),
  },
  {
    id: 'holi',
    label: 'Holi (Mar)',
    match: (d) => d.getMonth() === 2,
  },
];

const WEEKDAY_LABELS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

/** Calendar-year filter floor (Year dropdown starts here → latest). */
export const CALENDAR_YEAR_FILTER_START = 2025;

/** Earliest FY end year in filters. FY26 = 1 Apr 2025 – 31 Mar 2026. */
export const FY_FILTER_START = 2026;
export const FY_FILTER_START_DATE = '2025-04-01';

/** FY end years offered in filters: FY26 through the current Indian FY. */
export function visibleDashboardFyEnds(asOf: Date = new Date()): number[] {
  const end = currentIndianFyEnd(asOf);
  const start = Math.min(FY_FILTER_START, end);
  const out: number[] = [];
  for (let y = start; y <= end; y += 1) out.push(y);
  return out;
}

/** Indian FY end year for a date (Apr–Mar). */
export function currentIndianFyEnd(asOf: Date = new Date()): number {
  return asOf.getMonth() >= 3 ? asOf.getFullYear() + 1 : asOf.getFullYear();
}

/** Current Indian FY inclusive date bounds [from, to] as YYYY-MM-DD. */
export function currentIndianFyDateRange(asOf: Date = new Date()): [string, string] {
  const fyEnd = currentIndianFyEnd(asOf);
  return [`${fyEnd - 1}-04-01`, `${fyEnd}-03-31`];
}

/** Calendar years for Year filter: 2024 … latest (data max or today / current FY). */
export function calendarYearFilterOptions(latestYear?: number): { id: string; label: string }[] {
  const end = Math.max(
    CALENDAR_YEAR_FILTER_START,
    latestYear ?? 0,
    new Date().getFullYear(),
    currentIndianFyEnd()
  );
  const out: { id: string; label: string }[] = [];
  for (let y = CALENDAR_YEAR_FILTER_START; y <= end; y += 1) {
    out.push({ id: String(y), label: `${y} (Jan–Dec)` });
  }
  return out;
}

/** FY Year options from FY26 … current FY (dynamic labels). */
export function fyYearFilterOptions(_latestFyEnd?: number): { id: string; label: string }[] {
  const end = currentIndianFyEnd();
  const out: { id: string; label: string }[] = [];
  for (let fyEnd = FY_FILTER_START; fyEnd <= end; fyEnd += 1) {
    const id = `FY${String(fyEnd).slice(-2)}`;
    out.push({
      id,
      label: `FY ${fyEnd - 1}-${String(fyEnd).slice(-2)} (1 Apr ${fyEnd - 1} – 31 Mar ${fyEnd})`,
    });
  }
  return out.reverse(); // newest first
}

/**
 * Growth comparison rule (permanent): always same period last year (SPLY).
 * Year / FY / date range / month / week → shift the on-screen window −1 year.
 */
export const SPLY_GROWTH_RULE =
  'Growth always compares the selected period to the same dates one year earlier (SPLY).';

/** FY start = 1 April of the current fiscal year */
export function fiscalYearStartDate(d: Date): Date {
  const y = d.getMonth() >= 3 ? d.getFullYear() : d.getFullYear() - 1;
  return new Date(y, 3, 1);
}

function atMidnight(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate());
}

function addDays(d: Date, days: number): Date {
  const next = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  next.setDate(next.getDate() + days);
  return next;
}

/** Inclusive bounds of a retail FY week. Week 1 = 1 Apr through that Sunday. */
export function fyWeekBounds(fyEnd: number, week: number): { start: Date; end: Date } {
  const fyStart = new Date(fyEnd - 1, 3, 1);
  const fyLast = new Date(fyEnd, 2, 31);
  const daysToSunday = fyStart.getDay() === 0 ? 0 : 7 - fyStart.getDay();
  const week1End = addDays(fyStart, daysToSunday);
  if (week <= 1) return { start: fyStart, end: week1End };
  const firstMonday = addDays(week1End, 1);
  const start = addDays(firstMonday, (week - 2) * 7);
  const end = addDays(start, 6);
  return { start, end: end.getTime() > fyLast.getTime() ? fyLast : end };
}

function formatDayMonth(d: Date): string {
  return d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });
}

/**
 * Retail FY week: Monday–Sunday.
 * Week 1 starts 1 April and ends that Sunday (short if 1 April is not Monday).
 * The next 1 April resets to week 1.
 */
export function fyWeekInfo(d: Date): { key: string; label: string; week: number; fyEnd: number } {
  const day = atMidnight(d);
  const startFy = fiscalYearStartDate(day);
  const fyEnd = startFy.getFullYear() + 1;
  const daysToSunday = startFy.getDay() === 0 ? 0 : 7 - startFy.getDay();
  const week1End = addDays(startFy, daysToSunday);
  let week = 1;
  if (day.getTime() > week1End.getTime()) {
    const firstMonday = addDays(week1End, 1);
    const delta = Math.floor((day.getTime() - firstMonday.getTime()) / 86_400_000);
    week = 2 + Math.floor(delta / 7);
  }
  const bounds = fyWeekBounds(fyEnd, week);
  const key = `FY${String(fyEnd).slice(-2)}-W${String(week).padStart(2, '0')}`;
  const label = `FY${String(fyEnd).slice(-2)} Wk ${week} (${formatDayMonth(bounds.start)} – ${formatDayMonth(bounds.end)})`;
  return { key, label, week, fyEnd };
}

/** Weeks elapsed in the latest FY: 1 Apr through the week that contains today. */
export function fyWeekFilterOptions(asOf: Date = new Date()): { id: string; label: string }[] {
  const today = atMidnight(asOf);
  const current = fyWeekInfo(today);
  const yy = String(current.fyEnd).slice(-2);
  const out: { id: string; label: string }[] = [];
  for (let week = 1; week <= current.week; week += 1) {
    const bounds = fyWeekBounds(current.fyEnd, week);
    if (bounds.start.getTime() > today.getTime()) break;
    out.push({
      id: `FY${yy}-W${String(week).padStart(2, '0')}`,
      label: `FY${yy} Wk ${week} (${formatDayMonth(bounds.start)} – ${formatDayMonth(bounds.end)})`,
    });
  }
  return out;
}

export function timelineKeyForRow(row: SalesRow, mode: TimelineMode): string[] {
  const d = new Date(`${row.date}T00:00:00`);
  if (isNaN(d.getTime())) return [];

  if (mode === 'date') return [d.toISOString().slice(0, 10)];
  if (mode === 'day') return [WEEKDAY_LABELS[d.getDay()]];
  if (mode === 'month') {
    return [`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`];
  }
  if (mode === 'year') return [String(d.getFullYear())];
  if (mode === 'fy_week') return [fyWeekInfo(d).key];
  if (mode === 'fy_year') {
    const fyEnd = fiscalYearEnd(d);
    return [`FY${String(fyEnd).slice(-2)}`];
  }
  // event — a row can match multiple events
  return SPECIAL_EVENTS.filter((e) => e.match(d)).flatMap((e) => [e.id, `${e.id}:${d.getFullYear()}`]);
}

export function timelineLabelForKey(mode: TimelineMode, key: string): string {
  if (mode === 'event') {
    const [id, year] = key.split(':');
    const label = SPECIAL_EVENTS.find((e) => e.id === id)?.label ?? id;
    return year ? `${year} · ${label}` : label;
  }
  if (mode === 'date') {
    const d = new Date(key);
    if (!isNaN(d.getTime())) {
      return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
    }
  }
  if (mode === 'month' && /^\d{4}-\d{2}$/.test(key)) {
    const [y, m] = key.split('-').map(Number);
    return new Date(y, m - 1, 1).toLocaleDateString('en-IN', { month: 'short', year: 'numeric' });
  }
  if (mode === 'fy_week') {
    const info = key.match(/^FY(\d+)-W(\d+)$/);
    if (info) {
      const fyEnd = 2000 + Number(info[1].slice(-2));
      const bounds = fyWeekBounds(fyEnd, Number(info[2]));
      return `FY${String(fyEnd).slice(-2)} Wk ${Number(info[2])} (${formatDayMonth(bounds.start)} – ${formatDayMonth(bounds.end)})`;
    }
  }
  if (mode === 'fy_year') {
    const info = key.match(/^FY(\d{2})$/);
    if (info) {
      const endYear = 2000 + Number(info[1]);
      const startYear = endYear - 1;
      return `FY ${startYear}-${String(endYear).slice(-2)} (1 Apr–31 Mar)`;
    }
  }
  return key;
}

/** Distinct selectable values for the current timeline mode, from loaded rows */
export function buildTimelineOptions(rows: SalesRow[], mode: TimelineMode): { id: string; label: string }[] {
  if (mode === 'event') {
    const years = Array.from({ length: Math.max(1, new Date().getFullYear() - 2025 + 1) }, (_, i) => 2025 + i).reverse();
    return years.flatMap((year) => SPECIAL_EVENTS.map((e) => ({ id: `${e.id}:${year}`, label: `${year} · ${e.label}` })));
  }
  if (mode === 'day') {
    return WEEKDAY_LABELS.map((d) => ({ id: d, label: d }));
  }

  const keys = new Set<string>();
  rows.forEach((r) => timelineKeyForRow(r, mode).forEach((k) => keys.add(k)));
  return [...keys]
    .sort((a, b) => (a < b ? -1 : a > b ? 1 : 0))
    .map((id) => ({ id, label: timelineLabelForKey(mode, id) }));
}

export function applyTimelineFilter(
  rows: SalesRow[],
  mode: TimelineMode,
  values: string[]
): SalesRow[] {
  if (!values.length) return rows;

  // Date mode: calendar range — values = [from, to] (YYYY-MM-DD), inclusive
  if (mode === 'date') {
    const from = values[0] || '';
    const to = values[1] || values[0] || '';
    if (!from && !to) return rows;
    return rows.filter((r) => {
      const d = r.date?.slice(0, 10);
      if (!d) return false;
      if (from && d < from) return false;
      if (to && d > to) return false;
      return true;
    });
  }

  const set = new Set(values);
  return rows.filter((r) => timelineKeyForRow(r, mode).some((k) => set.has(k)));
}

export function timelineModeLabel(mode: TimelineMode): string {
  for (const g of TIMELINE_MODE_GROUPS) {
    const m = g.modes.find((x) => x.id === mode);
    if (m) return m.label;
  }
  return mode;
}

export type TargetGrain = 'ytd' | 'month' | 'year';

/** Map Sales Overview timeline + target grain → Target LFS API date bounds. */
export function buildTargetDateParams(
  timelineMode: TimelineMode,
  timelineValues: string[],
  targetGrain: TargetGrain,
  fallbackYear: number,
  monthId?: string
): { date_from?: string; date_to?: string } {
  // Event selections already carry their calendar years; the API applies exact
  // event windows. Do not intersect historical events with the default FY.
  if (timelineMode === 'event') return {};
  const pad = (n: number) => String(n).padStart(2, '0');
  const lastDay = (y: number, m: number) => new Date(y, m, 0).getDate();

  /** Indian FY start for a calendar date (Apr–Mar). */
  const indianFyStart = (iso: string): string => {
    const y = Number(iso.slice(0, 4));
    const m = Number(iso.slice(5, 7));
    if (!y || !m) return `${fallbackYear - 1}-04-01`;
    return m >= 4 ? `${y}-04-01` : `${y - 1}-04-01`;
  };

  if (timelineMode === 'date' && timelineValues.length > 0) {
    const from = timelineValues[0]?.slice(0, 10) || '';
    const to = (timelineValues[1] || timelineValues[0] || '').slice(0, 10);
    if (!from && !to) return {};
    const end = to || from;
    // YTD / FY: use the selected range start (e.g. FY-27 → 2026-04-01), not calendar Jan 1
    if (targetGrain === 'ytd') {
      return { date_from: from || indianFyStart(end), date_to: end };
    }
    return { date_from: from || end, date_to: end };
  }

  if (timelineMode === 'month' && timelineValues.length === 1 && /^\d{4}-\d{2}$/.test(timelineValues[0])) {
    const [y, m] = timelineValues[0].split('-').map(Number);
    const end = `${timelineValues[0]}-${pad(lastDay(y, m))}`;
    if (targetGrain === 'ytd') {
      return { date_from: indianFyStart(end), date_to: end };
    }
    return { date_from: `${timelineValues[0]}-01`, date_to: end };
  }

  if (timelineMode === 'year' && timelineValues.length >= 1) {
    // Calendar year(s): 1 Jan – 31 Dec (not Indian FY)
    const years = timelineValues
      .filter((v) => /^\d{4}$/.test(v))
      .map(Number)
      .sort((a, b) => a - b);
    if (!years.length) return {};
    const y0 = years[0];
    const y1 = years[years.length - 1];
    return { date_from: `${y0}-01-01`, date_to: `${y1}-12-31` };
  }

  if (timelineMode === 'fy_year') {
    const raw = (timelineValues[0] || '').toUpperCase().replace(/\s/g, '');
    const m = raw.match(/(\d{2,4})$/);
    if (m) {
      let fyEnd = Number(m[1]);
      if (fyEnd < 100) fyEnd += 2000;
      return { date_from: `${fyEnd - 1}-04-01`, date_to: `${fyEnd}-03-31` };
    }
  }

  if (monthId && monthId !== 'all' && /^\d{4}-\d{2}$/.test(monthId)) {
    const [y, m] = monthId.split('-').map(Number);
    const end = `${monthId}-${pad(lastDay(y, m))}`;
    if (targetGrain === 'ytd') return { date_from: indianFyStart(end), date_to: end };
    return { date_from: `${monthId}-01`, date_to: end };
  }

  if (targetGrain === 'ytd') {
    const [fyFrom, fyTo] = currentIndianFyDateRange();
    // Prefer current FY when no timeline context; else FY of fallbackYear
    if (!fallbackYear || fallbackYear === currentIndianFyEnd()) {
      return { date_from: fyFrom, date_to: fyTo };
    }
    return { date_from: `${fallbackYear - 1}-04-01`, date_to: `${fallbackYear}-03-31` };
  }

  return {};
}

function seriesYear(key: number[] | undefined): number {
  return Number(key?.[0]) || 0;
}

function seriesMonth(key: number[] | undefined): number {
  return Number(key?.[1]) || 0;
}

/** Pick target KPI totals from target-vs-achieved API payload. */
export function pickTargetSummaryFromApi(
  data: {
    current?: {
      key?: number[];
      periodLabel?: string;
      target?: number;
      achieved?: number;
      achievementPct?: number;
    } | null;
    series?: {
      key?: number[];
      periodLabel?: string;
      target?: number;
      achieved?: number;
      achievementPct?: number;
    }[];
    range_summary?: {
      target?: number;
      achieved?: number;
      achievementPct?: number;
      periodLabel?: string;
    } | null;
    site_count?: number;
    sites_with_target?: number;
    sites_with_achieved?: number;
  },
  opts: {
    grain: TargetGrain;
    timelineMode: TimelineMode;
    timelineValues: string[];
    monthId?: string;
  }
): TargetSummary | null {
  const series = data.series ?? [];
  const meta = {
    stores: data.site_count ?? data.sites_with_target ?? 0,
    sitesWithTarget: data.sites_with_target ?? data.site_count,
    sitesWithAchieved: data.sites_with_achieved,
  };

  const monthlyTargets: Record<string, number> = {};
  const monthNames = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  for (const s of series) {
    if (s.target && s.target > 0) {
      const yr = seriesYear(s.key);
      const mo = seriesMonth(s.key);
      if (mo >= 1 && mo <= 12) {
        const mName = monthNames[mo - 1];
        if (yr > 0) {
          monthlyTargets[`${yr}-${String(mo).padStart(2, '0')}`] = s.target;
          monthlyTargets[`${mName} '${String(yr).slice(-2)}`] = s.target;
          monthlyTargets[`${mName} ${yr}`] = s.target;
        }
        monthlyTargets[mName] = s.target;
        monthlyTargets[mName.toLowerCase()] = s.target;
        monthlyTargets[mName.toUpperCase()] = s.target;
      }
      if (s.periodLabel) {
        monthlyTargets[s.periodLabel] = s.target;
        monthlyTargets[s.periodLabel.trim()] = s.target;
      }
    }
  }

  const fromRange = (rs: NonNullable<typeof data.range_summary>, grain: TargetGrain): TargetSummary | null => {
    if (!(rs.target && rs.target > 0)) return null;
    return {
      period: rs.periodLabel ?? 'range',
      periodLabel: rs.periodLabel,
      grain,
      totalTarget: rs.target,
      totalActual: rs.achieved ?? 0,
      achievement: rs.achievementPct ?? 0,
      stores: meta.stores,
      sitesWithTarget: meta.sitesWithTarget,
      sitesWithAchieved: meta.sitesWithAchieved,
      monthlyTargets,
    };
  };

  if (data.range_summary?.target && data.range_summary.target > 0) {
    // When grain is 'month', only use range_summary if the user specifically filtered for that single month
    if (opts.grain !== 'month' || opts.timelineMode === 'month' || (opts.timelineMode === 'date' && opts.timelineValues.length <= 1)) {
      const hit = fromRange(data.range_summary, opts.grain);
      if (hit) return hit;
    }
  }

  const resolveYear = (): number => {
    if (opts.timelineMode === 'year' && opts.timelineValues.length === 1 && /^\d{4}$/.test(opts.timelineValues[0])) {
      return Number(opts.timelineValues[0]);
    }
    if (opts.timelineMode === 'month' && opts.timelineValues.length === 1 && /^\d{4}-\d{2}$/.test(opts.timelineValues[0])) {
      return Number(opts.timelineValues[0].slice(0, 4));
    }
    if (opts.monthId && opts.monthId !== 'all' && /^\d{4}/.test(opts.monthId)) {
      return Number(opts.monthId.slice(0, 4));
    }
    const cur = data.current ?? series[series.length - 1];
    return seriesYear(cur?.key) || new Date().getFullYear();
  };

  if (opts.grain === 'ytd') {
    let ytdMonths: typeof series = [];
    let periodLabel = '';
    const monthName = (yr: number, m: number) =>
      new Date(yr, m - 1, 1).toLocaleDateString('en-IN', { month: 'short' });

    if (opts.timelineMode === 'date' && opts.timelineValues.length >= 2) {
      const fromYm = opts.timelineValues[0].slice(0, 7);
      const toYm = opts.timelineValues[1].slice(0, 7);
      ytdMonths = series.filter((s) => {
        const yr = seriesYear(s.key);
        const mo = seriesMonth(s.key);
        if (!yr || !mo) return false;
        const ym = `${yr}-${String(mo).padStart(2, '0')}`;
        return ym >= fromYm && ym <= toYm;
      });
      const [fromY, fromM] = fromYm.split('-').map(Number);
      const [toY, toM] = toYm.split('-').map(Number);
      periodLabel = `${monthName(fromY, fromM)} ${fromY}–${monthName(toY, toM)} ${toY}`;
    } else {
      const year = resolveYear();
      let monthTo = 12;
      if (opts.timelineMode === 'date' && opts.timelineValues.length) {
        const end = (opts.timelineValues[1] || opts.timelineValues[0] || '').slice(0, 10);
        if (/^\d{4}-\d{2}/.test(end)) monthTo = Number(end.slice(5, 7));
      } else if (opts.timelineMode === 'month' && opts.timelineValues.length === 1) {
        monthTo = Number(opts.timelineValues[0].slice(5, 7));
      } else if (opts.monthId && /^\d{4}-\d{2}$/.test(opts.monthId)) {
        monthTo = Number(opts.monthId.slice(5, 7));
      } else {
        const cur = data.current ?? series[series.length - 1];
        if (seriesYear(cur?.key) === year && seriesMonth(cur?.key) > 0) {
          monthTo = seriesMonth(cur?.key);
        }
      }

      ytdMonths = series.filter(
        (s) => seriesYear(s.key) === year && seriesMonth(s.key) >= 1 && seriesMonth(s.key) <= monthTo
      );
      periodLabel = `Jan–${monthName(year, monthTo)} ${year} YTD`;
    }

    if (!ytdMonths.length) return null;
    const totalTarget = ytdMonths.reduce((sum, p) => sum + (p.target || 0), 0);
    const totalActual = ytdMonths.reduce((sum, p) => sum + (p.achieved || 0), 0);
    if (!(totalTarget > 0)) return null;
    return {
      period: 'YTD',
      periodLabel,
      grain: 'ytd',
      totalTarget,
      totalActual,
      achievement: Math.round((1000 * totalActual) / totalTarget) / 10,
      stores: meta.stores,
      sitesWithTarget: meta.sitesWithTarget,
      sitesWithAchieved: meta.sitesWithAchieved,
      monthlyTargets,
    };
  }

  let point = data.current ?? series[series.length - 1] ?? null;

  if (opts.grain === 'year') {
    const year = resolveYear();
    point = series.find((s) => seriesYear(s.key) === year && !seriesMonth(s.key)) ?? point;
  } else {
    let want: string | null = null;
    if (opts.monthId && opts.monthId !== 'all' && /^\d{4}-\d{2}$/.test(opts.monthId)) want = opts.monthId;
    else if (opts.timelineMode === 'month' && opts.timelineValues.length === 1) want = opts.timelineValues[0];
    else if (opts.timelineMode === 'date' && opts.timelineValues.length) {
      const end = (opts.timelineValues[1] || opts.timelineValues[0] || '').slice(0, 10);
      if (/^\d{4}-\d{2}-\d{2}$/.test(end)) want = end.slice(0, 7);
    }
    if (want) {
      const [y, m] = want.split('-').map(Number);
      point = series.find((s) => seriesYear(s.key) === y && seriesMonth(s.key) === m) ?? point;
    } else {
      const validPoints = series.filter((s) => (s.target || 0) > 0);
      if (validPoints.length > 0) {
        point = validPoints[validPoints.length - 1];
      }
    }
  }

  if (!point || !(point.target && point.target > 0)) return null;
  return {
    period:
      opts.grain === 'year'
        ? String(seriesYear(point.key) || point.periodLabel)
        : `${seriesYear(point.key)}-${String(seriesMonth(point.key) || 1).padStart(2, '0')}`,
    periodLabel: point.periodLabel,
    grain: opts.grain,
    totalTarget: point.target,
    totalActual: point.achieved ?? 0,
    achievement: point.achievementPct ?? 0,
    stores: meta.stores,
    sitesWithTarget: meta.sitesWithTarget,
    sitesWithAchieved: meta.sitesWithAchieved,
    monthlyTargets,
  };
}

/**
 * Real Date/Day/Month/Year/FY trend series from row dates.
 */
export function buildTimelineSeries(rows: SalesRow[], mode: TimelineMode): TrendPoint[] {
  if (mode === 'event') {
    return SPECIAL_EVENTS.map((ev) => {
      const value = rows
        .filter((r) => {
          const d = new Date(r.date);
          return !isNaN(d.getTime()) && ev.match(d);
        })
        .reduce((s, r) => s + r.net_sale_amount, 0);
      return { key: ev.id, label: ev.label.replace(/\s*\(.*\)$/, ''), actual: value };
    }).filter((p) => p.actual > 0);
  }

  if (mode === 'day') {
    const totals = new Array(7).fill(0);
    rows.forEach((r) => {
      const d = new Date(r.date);
      if (!isNaN(d.getTime())) totals[d.getDay()] += r.net_sale_amount;
    });
    // Mon → Sun order
    const order = [1, 2, 3, 4, 5, 6, 0];
    return order.map((di) => ({
      key: WEEKDAY_LABELS[di],
      label: WEEKDAY_LABELS[di],
      actual: totals[di],
    }));
  }

  const buckets = new Map<string, { label: string; value: number }>();
  rows.forEach((r) => {
    const d = new Date(r.date);
    if (isNaN(d.getTime())) return;
    const keys = timelineKeyForRow(r, mode);
    keys.forEach((key) => {
      const cur = buckets.get(key) ?? { label: timelineLabelForKey(mode, key), value: 0 };
      cur.value += r.net_sale_amount;
      buckets.set(key, cur);
    });
  });

  let points = [...buckets.entries()]
    .map(([key, v]) => ({ key, label: v.label, actual: v.value }))
    .sort((a, b) => (a.key < b.key ? -1 : a.key > b.key ? 1 : 0));

  if (mode === 'date') points = points.slice(-45);
  if (mode === 'fy_week') points = points.slice(-26);

  return points;
}

/**
 * Real Date/Day/Month/Year trend series, built from the actual `date` field on each row —
 * replaces the previous placeholder curves (which used fabricated sine-wave/multiplier formulas).
 * "monthly" is handled separately by the backend's real `monthly_trend` insight.
 */
export function buildRealPeriodSeries(
  rows: SalesRow[],
  period: 'daily' | 'weekly' | 'quarterly' | 'yearly'
): TrendPoint[] {
  if (period === 'weekly') {
    // calendar Mon-week series (legacy)
    const parsed = rows
      .map((r) => ({ row: r, d: new Date(r.date) }))
      .filter((x) => !isNaN(x.d.getTime()));
    if (!parsed.length) return [];
    const buckets = new Map<string, { label: string; value: number; sortKey: string }>();
    parsed.forEach(({ row, d }) => {
      const ws = weekStart(d);
      const key = ws.toISOString().slice(0, 10);
      const label = `Wk of ${ws.toLocaleDateString('en-IN', { day: '2-digit', month: 'short' })}`;
      const cur = buckets.get(key) ?? { label, value: 0, sortKey: key };
      cur.value += row.net_sale_amount;
      buckets.set(key, cur);
    });
    return [...buckets.entries()]
      .map(([key, v]) => ({ key, label: v.label, actual: v.value }))
      .sort((a, b) => (a.key < b.key ? -1 : a.key > b.key ? 1 : 0))
      .slice(-12);
  }
  if (period === 'daily') return buildTimelineSeries(rows, 'date').slice(-30);
  if (period === 'yearly') return buildTimelineSeries(rows, 'fy_year');
  // quarterly — keep old fiscal quarter logic
  const parsed = rows
    .map((r) => ({ row: r, d: new Date(r.date) }))
    .filter((x) => !isNaN(x.d.getTime()));
  if (!parsed.length) return [];
  const buckets = new Map<string, { label: string; value: number; sortKey: string }>();
  parsed.forEach(({ row, d }) => {
    const fy = fiscalYearEnd(d);
    const q = fiscalQuarter(d);
    const key = `FY${fy}-Q${q}`;
    const label = `FY${String(fy).slice(-2)} Q${q}`;
    const cur = buckets.get(key) ?? { label, value: 0, sortKey: key };
    cur.value += row.net_sale_amount;
    buckets.set(key, cur);
  });
  return [...buckets.entries()]
    .map(([key, v]) => ({ key, label: v.label, actual: v.value }))
    .sort((a, b) => (a.key < b.key ? -1 : a.key > b.key ? 1 : 0));
}

export function buildParetoData(rows: SalesRow[]) {
  const bySku = aggregateByKey(rows, (r) => r.sku);
  const total = bySku.reduce((s, d) => s + d.value, 0) || 1;
  let cumulative = 0;
  return bySku.slice(0, 15).map((d) => {
    cumulative += d.value;
    return {
      name: d.name,
      sales: d.value,
      cumulativePct: (cumulative / total) * 100,
      sharePct: (d.value / total) * 100,
    };
  });
}

export function buildHeatmapData(rows: SalesRow[]) {
  const stores = [...new Set(rows.map((r) => r.site_name))].slice(0, 8);
  const weeks = ['W1', 'W2', 'W3', 'W4'];
  return stores.flatMap((store) =>
    weeks.map((week, wi) => {
      const subset = rows.filter((r) => r.site_name === store);
      const base = subset.reduce((s, r) => s + r.net_sale_amount, 0) / 4;
      return {
        store: store.slice(0, 12),
        week,
        value: base * (0.7 + wi * 0.12 + (store.length % 3) * 0.05),
      };
    })
  );
}

export type DrillLevel = 'zone' | 'state' | 'city' | 'store' | 'department' | 'section' | 'category' | 'story' | 'sku';

export const DRILL_ORDER: DrillLevel[] = ['zone', 'state', 'city', 'store', 'department', 'section', 'category', 'story', 'sku'];

export function drillKey(row: SalesRow, level: DrillLevel): string {
  switch (level) {
    case 'zone': return deriveZone(row);
    case 'state': return deriveState(row);
    case 'city': return deriveCity(row);
    case 'store': return row.site_name;
    case 'department': return deriveDepartment(row);
    case 'section': return deriveSection(row);
    case 'category': return deriveCategory(row);
    case 'story': return deriveStory(row);
    case 'sku': return row.sku;
  }
}

/** Analyzer display labels for Sales Channel codes (matches backend _CHANNEL_DISPLAY_MAP). */
const ANALYZER_CHANNEL_DISPLAY: Record<string, string> = {
  PT: 'Pantaloons (PT)',
  SSL: 'Shoppers Stop (SSL)',
  CENTRO: 'Centro',
  ICONIC: 'Iconic',
  EBO: 'EBO',
};

/**
 * Map Sales Overview timeline + client filters → Sales Analyzer /query payload.
 * Analyzer-local year/filter UI must not apply when this is used.
 */
export function buildAnalyzerFiltersFromPage(opts: {
  timelineMode: TimelineMode;
  timelineValues: string[];
  salesYear: number;
  clientFilters: ClientFilters;
  masterSites?: CustomerMasterSite[];
  storeId?: string;
  channelId?: string;
  monthId?: string;
}): { year: number; filters: Record<string, unknown> } {
  const {
    timelineMode,
    timelineValues,
    salesYear,
    clientFilters,
    masterSites,
    storeId = 'all',
    channelId = 'all',
    monthId = 'all',
  } = opts;

  const filters: Record<string, unknown> = {};
  const pad = (n: number) => String(n).padStart(2, '0');
  const lastDay = (y: number, m: number) => new Date(y, m, 0).getDate();

  if (timelineMode === 'day' && timelineValues.length > 0) {
    const d = timelineValues[0].slice(0, 10);
    if (/^\d{4}-\d{2}-\d{2}$/.test(d)) filters.date_range = [d, d];
  } else if (timelineMode === 'fy_week' && timelineValues.length > 0) {
    filters.fy_week = [...timelineValues];
  } else if (timelineMode === 'month' && timelineValues.length > 1) {
    const months = timelineValues.filter((v) => /^\d{4}-\d{2}$/.test(v)).sort();
    if (months.length) {
      const first = months[0];
      const last = months[months.length - 1];
      const [y, m] = last.split('-').map(Number);
      filters.date_range = [`${first}-01`, `${last}-${pad(lastDay(y, m))}`];
    }
  } else {
    const dates = buildTargetDateParams(
      timelineMode,
      timelineValues,
      'year',
      salesYear,
      monthId
    );
    if (dates.date_from && dates.date_to) {
      filters.date_range = [dates.date_from, dates.date_to];
    }
  }

  // Preserve exact selections, including disjoint months/days, for the analyzer.
  delete filters.date_range;
  delete filters.fy_week;
  filters.timeline_mode = timelineMode;
  filters.timeline_values = timelineValues.length ? timelineValues.join(',') : `FY${String(salesYear).slice(-2)}`;
  if (!timelineValues.length) filters.timeline_mode = 'fy_year';

  const sites = masterSites ?? [];

  // Always apply explicit dimension filters directly
  if (clientFilters.zones.length) filters.zone = [...clientFilters.zones];
  if (clientFilters.siteCodes.length) filters.site_id = [...clientFilters.siteCodes];
  if (clientFilters.siteNames.length) filters.site_name = [...clientFilters.siteNames];
  if (clientFilters.cities.length) filters.city_name = [...clientFilters.cities];
  if (clientFilters.states.length) filters.state_name = [...clientFilters.states];
  if (clientFilters.clusters.length) filters.cluster = [...clientFilters.clusters];
  if (clientFilters.clusterRm.length) {
    filters.cluster = [
      ...new Set([...((filters.cluster as string[] | undefined) ?? []), ...clientFilters.clusterRm]),
    ];
  }
  if (clientFilters.storeGrades.length) filters.store_grade = [...clientFilters.storeGrades];
  if (clientFilters.customerNames.length) filters.customer_name = [...clientFilters.customerNames];
  if (clientFilters.salesChannels.length) {
    filters.sales_channel = clientFilters.salesChannels.map(
      (c) => ANALYZER_CHANNEL_DISPLAY[c.toUpperCase()] || c
    );
  }

  if (clientFilters.departments.length) filters.department = [...clientFilters.departments];
  if (clientFilters.sections.length) filters.section = [...clientFilters.sections];
  if (clientFilters.seasons.length) filters.season = [...clientFilters.seasons];
  if (clientFilters.skus.length) filters.sku = [...clientFilters.skus];
  if (clientFilters.barcodes.length) filters.barcode = [...clientFilters.barcodes];
  if (clientFilters.storeStatuses.length) filters.store_status = [...clientFilters.storeStatuses];
  if (clientFilters.areaManagers.length) filters.area_manager = [...clientFilters.areaManagers];
  if (clientFilters.regionalManagers.length) filters.regional_manager = [...clientFilters.regionalManagers];
  if (clientFilters.customerFormats.length) {
    filters.customer_format = [...clientFilters.customerFormats];
  }

  // Prefer Customer Master site intersection (zone/store/grade/…) so analyzer matches KPIs.
  if (sites.length) {
    const allowed = resolveSiteKeysFromMasterFilters(sites, clientFilters);
    if (allowed !== null) {
      const codes = sites
        .filter((s) => allowed.has(s.site_key))
        .map((s) => String(s.site_code || s.site_key || '').trim())
        .filter(Boolean);
      filters.site_id = codes.length ? [...new Set(codes)] : ['__none__'];
    }
  }

  if (storeId && storeId !== 'all') {
    filters.site_id = [storeId];
  }
  if (
    !filters.sales_channel &&
    channelId &&
    channelId !== 'all' &&
    !clientFilters.salesChannels.length
  ) {
    filters.sales_channel = [ANALYZER_CHANNEL_DISPLAY[channelId.toUpperCase()] || channelId];
  }

  // If Store Category (LTL, NEW, EXIT) is filtered, intersect with matching store codes from Customer Master
  if (sites.length && clientFilters.storeCategories.length) {
    const catUpper = clientFilters.storeCategories.map((c) => c.trim().toUpperCase());
    const catSites = sites
      .filter((s) => catUpper.includes(String(s.store_category || '').trim().toUpperCase()))
      .map((s) => s.site_code || s.site_key)
      .filter(Boolean);
    if (catSites.length) {
      const existing = (filters.site_id as string[] | undefined) ?? [];
      filters.site_id = existing.length ? existing.filter((id) => catSites.includes(id)) : catSites;
    }
  }

  return { year: salesYear, filters };
}

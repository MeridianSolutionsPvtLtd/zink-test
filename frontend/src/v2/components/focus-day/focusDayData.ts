export type FocusEvent = {
  id: string;
  name: string;
  status: 'upcoming' | 'running' | 'completed';
  startDate: string;
  endDate: string;
  lySales: number;
  target: number;
};

export type FocusRecord = {
  id: string;
  event: string;
  zone: string;
  state: string;
  city: string;
  store: string;
  storeGrade: string;
  channel: string;
  department: string;
  category: string;
  story: string;
  sku: string;
  season: string;
  manager: string;
  sales: number;
  grossSales: number;
  netSales: number;
  target: number;
  forecast: number;
  quantity: number;
  margin: number;
  discount: number;
  discountPct: number;
  sellThrough: number;
  stock: number;
  invValue: number;
  asp: number;
  hue: number;
};

export type FocusFilters = {
  event: string[];
  dateRange: string[];
  zone: string[];
  state: string[];
  city: string[];
  store: string[];
  storeGrade: string[];
  channel: string[];
  department: string[];
  category: string[];
  story: string[];
  sku: string[];
  season: string[];
};

export const EMPTY_FOCUS_FILTERS: FocusFilters = {
  event: [],
  dateRange: [],
  zone: [],
  state: [],
  city: [],
  store: [],
  storeGrade: [],
  channel: [],
  department: [],
  category: [],
  story: [],
  sku: [],
  season: [],
};

export const EVENTS: FocusEvent[] = [
  { id: 'durgapuja', name: 'DurgaPuja', status: 'upcoming', startDate: '2026-09-08', endDate: '2026-10-20', lySales: 8.98e7, target: 10.5e7 },
  { id: 'diwali', name: 'Diwali', status: 'upcoming', startDate: '2026-10-21', endDate: '2026-11-08', lySales: 3.00e7, target: 3.5e7 },
  { id: 'christmas', name: 'Christmas', status: 'upcoming', startDate: '2026-12-05', endDate: '2026-12-25', lySales: 6.94e7, target: 7.5e7 },
  { id: 'valentine', name: 'Valentine', status: 'completed', startDate: '2026-02-01', endDate: '2026-02-14', lySales: 2.29e7, target: 2.6e7 },
];

const ZONES = ['North', 'South', 'East', 'West', 'Central'];
const STATES = ['Maharashtra', 'Karnataka', 'Delhi', 'Gujarat', 'Tamil Nadu', 'West Bengal'];
const CITIES = ['Mumbai', 'Bengaluru', 'New Delhi', 'Ahmedabad', 'Chennai', 'Kolkata'];
const STORES = ['Phoenix Mall', 'Select Citywalk', 'Forum Mall', 'Inorbit Vashi', 'DLF Promenade', 'Orion Gateway', 'Ambience Mall', 'High Street Phoenix'];
const GRADES = ['A+', 'A', 'B', 'C'];
const CHANNELS = ['Store', 'Online', 'Marketplace', 'EBO'];
const DEPTS = ['Mens Wear', 'Womens Wear', 'Kids', 'Footwear', 'Accessories', 'Ethnic'];
const CATS = ['Casual', 'Formal', 'Denim', 'Ethnic', 'Activewear', 'Winterwear'];
const STORIES = ['Urban Classic', 'Festive Edit', 'Workwear', 'Premium Line', 'Weekend Casual'];
const SEASONS = ['AW-25', 'SS-25', 'AW-24'];
const MANAGERS = ['Rajesh K.', 'Priya S.', 'Amit M.', 'Neha G.', 'Vikram R.', 'Anita P.'];

function seeded(n: number) {
  const x = Math.sin(n * 7919) * 10000;
  return x - Math.floor(x);
}

export function generateFocusCatalog(count = 150): FocusRecord[] {
  const rows: FocusRecord[] = [];
  for (let i = 0; i < count; i++) {
    const qty = Math.round(60 + seeded(i * 3) * 380);
    const asp = Math.round(900 + seeded(i * 5) * 2800);
    const sales = qty * asp;
    const discountPct = Math.round(seeded(i * 7) * 32);
    const marginPct = Math.round(26 + seeded(i * 11) * 34);
    const event = EVENTS[i % EVENTS.length].name;
    rows.push({
      id: `FD${1000 + i}`,
      event,
      zone: ZONES[i % ZONES.length],
      state: STATES[i % STATES.length],
      city: CITIES[i % CITIES.length],
      store: STORES[i % STORES.length],
      storeGrade: GRADES[i % GRADES.length],
      channel: CHANNELS[i % CHANNELS.length],
      department: DEPTS[i % DEPTS.length],
      category: CATS[i % CATS.length],
      story: STORIES[i % STORIES.length],
      sku: `SKU-${3000 + i}`,
      season: SEASONS[i % SEASONS.length],
      manager: MANAGERS[i % MANAGERS.length],
      sales,
      grossSales: sales * 1.08,
      netSales: sales * (1 - discountPct / 100),
      target: sales * (1.05 + seeded(i) * 0.15),
      forecast: sales * (0.95 + seeded(i * 2) * 0.12),
      quantity: qty,
      margin: sales * (marginPct / 100),
      discount: sales * (discountPct / 100),
      discountPct,
      sellThrough: Math.min(96, Math.round(38 + seeded(i * 13) * 52)),
      stock: Math.round(qty * (0.5 + seeded(i * 17) * 1.5)),
      invValue: Math.round(qty * 850 * (0.8 + seeded(i * 19))),
      asp,
      hue: 205 + (i * 13) % 110,
    });
  }
  return rows;
}

export const FOCUS_CATALOG = generateFocusCatalog(150);

export const FILTER_OPTIONS = {
  event: EVENTS.map((e) => e.name),
  dateRange: ['Event Period', 'WTD', 'MTD', 'Last 7 Days', 'Last 30 Days', 'Custom Range'],
  zone: ZONES,
  state: STATES,
  city: CITIES,
  store: STORES,
  storeGrade: GRADES,
  channel: CHANNELS,
  department: DEPTS,
  category: CATS,
  story: STORIES,
  season: SEASONS,
};

export function formatInr(value: number, compact = false) {
  if (compact) {
    if (Math.abs(value) >= 1_00_00_000) return `₹${(value / 1_00_00_000).toFixed(1)}Cr`;
    if (Math.abs(value) >= 1_00_000) return `₹${(value / 1_00_000).toFixed(1)}L`;
    if (Math.abs(value) >= 1_000) return `₹${(value / 1_000).toFixed(1)}K`;
  }
  return new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(value);
}

export function formatCompactInr(value: number) {
  return formatInr(value, true);
}

export function sparklinePath(values: number[]): string {
  if (!values.length) return '';
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const w = 64;
  const h = 24;
  const step = w / Math.max(values.length - 1, 1);
  return values.map((v, i) => {
    const x = i * step;
    const y = h - ((v - min) / range) * h;
    return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
}

export function applyFocusFilters(rows: FocusRecord[], f: FocusFilters): FocusRecord[] {
  return rows.filter((r) => {
    const checks: [string[], string][] = [
      [f.event, r.event],
      [f.zone, r.zone],
      [f.state, r.state],
      [f.city, r.city],
      [f.store, r.store],
      [f.storeGrade, r.storeGrade],
      [f.channel, r.channel],
      [f.department, r.department],
      [f.category, r.category],
      [f.story, r.story],
      [f.sku, r.sku],
      [f.season, r.season],
    ];
    return checks.every(([arr, val]) => !arr.length || arr.includes(val));
  });
}

export type FocusKpiData = {
  festivalSales: number;
  lyComparison: number;
  target: number;
  achievement: number;
  forecast: number;
  grossSales: number;
  netSales: number;
  quantity: number;
  asp: number;
  sellThrough: number;
  invValue: number;
  discountPct: number;
  footfall: number;
  conversion: number;
  trend: number[];
  prevScale: number;
};

export function computeFocusKpis(rows: FocusRecord[]): FocusKpiData {
  const festivalSales = rows.reduce((s, r) => s + r.sales, 0);
  const grossSales = rows.reduce((s, r) => s + r.grossSales, 0);
  const netSales = rows.reduce((s, r) => s + r.netSales, 0);
  const target = rows.reduce((s, r) => s + r.target, 0);
  const forecast = rows.reduce((s, r) => s + r.forecast, 0);
  const quantity = rows.reduce((s, r) => s + r.quantity, 0);
  const invValue = rows.reduce((s, r) => s + r.invValue, 0);
  const discount = rows.reduce((s, r) => s + r.discount, 0);
  const lySales = festivalSales * 0.88;
  const lyComparison = lySales > 0 ? ((festivalSales - lySales) / lySales) * 100 : 0;
  const achievement = target > 0 ? (festivalSales / target) * 100 : 0;
  const asp = quantity > 0 ? festivalSales / quantity : 0;
  const sellThrough = rows.length ? rows.reduce((s, r) => s + r.sellThrough, 0) / rows.length : 0;
  const discountPct = festivalSales > 0 ? (discount / festivalSales) * 100 : 0;
  const footfall = Math.round(quantity * 2.8 + rows.length * 120);
  const conversion = footfall > 0 ? (quantity / footfall) * 100 : 0;
  const trend = rows.slice(0, 10).map((r) => r.sales);

  return {
    festivalSales,
    lyComparison,
    target,
    achievement,
    forecast,
    grossSales,
    netSales,
    quantity,
    asp,
    sellThrough,
    invValue,
    discountPct,
    footfall,
    conversion,
    trend: trend.length ? trend : [1, 2, 3, 4, 5, 6, 7, 8],
    prevScale: 0.9,
  };
}

export function aggregateBy(rows: FocusRecord[], key: keyof FocusRecord) {
  const map = new Map<string, { sales: number; target: number; qty: number; margin: number }>();
  rows.forEach((r) => {
    const k = String(r[key]);
    const cur = map.get(k) ?? { sales: 0, target: 0, qty: 0, margin: 0 };
    cur.sales += r.sales;
    cur.target += r.target;
    cur.qty += r.quantity;
    cur.margin += r.margin;
    map.set(k, cur);
  });
  return [...map.entries()].map(([name, v]) => ({ name, ...v })).sort((a, b) => b.sales - a.sales);
}

export type DrillLevel = 'zone' | 'state' | 'city' | 'store' | 'department' | 'category' | 'story' | 'sku';
export const DRILL_ORDER: DrillLevel[] = ['zone', 'state', 'city', 'store', 'department', 'category', 'story', 'sku'];

export function buildEventTrend(total: number, period: 'daily' | 'weekly' | 'hourly') {
  const n = period === 'hourly' ? 12 : period === 'daily' ? 14 : 8;
  const labels =
    period === 'hourly'
      ? Array.from({ length: n }, (_, i) => `${10 + i}:00`)
      : period === 'daily'
        ? Array.from({ length: n }, (_, i) => `D${i + 1}`)
        : Array.from({ length: n }, (_, i) => `W${i + 1}`);
  const base = total / n || 50000;
  return labels.map((label, i) => ({
    label,
    current: base * (0.85 + i * 0.04 + Math.sin(i * 0.7) * 0.08),
    previous: base * (0.78 + i * 0.035),
    target: base * 1.08 * (0.9 + i * 0.02),
  }));
}

export function buildEventComparison() {
  return ['Diwali', 'EOSS', 'Christmas', 'Republic Day', 'New Year', 'Independence Day'].map((name, i) => {
    const base = 2.5e7 + i * 35e5;
    return { name, current: base * 1.05, previous: base * 0.92, target: base * 1.12 };
  });
}

export function buildHourlyHeatmap(stores: string[]) {
  const hours = ['10AM', '11AM', '12PM', '1PM', '2PM', '3PM', '4PM', '5PM', '6PM', '7PM', '8PM', '9PM'];
  return hours.flatMap((hour, hi) =>
    stores.slice(0, 6).map((store, si) => ({
      hour,
      store,
      value: Math.round(8000 + Math.sin(hi * 0.8 + si) * 4000 + hi * 1200 + si * 500),
    }))
  );
}

export function buildStoreEventHeatmap(rows: FocusRecord[]) {
  const stores = [...new Set(rows.map((r) => r.store))].slice(0, 8);
  const events = [...new Set(rows.map((r) => r.event))].slice(0, 6);
  return stores.flatMap((store) =>
    events.map((event, ei) => {
      const match = rows.filter((r) => r.store === store && r.event === event);
      const sales = match.reduce((s, r) => s + r.sales, 0) || 500000 * (0.7 + ei * 0.1);
      return { store, event, sales };
    })
  );
}

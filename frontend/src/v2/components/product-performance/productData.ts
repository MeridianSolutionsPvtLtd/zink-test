export type ProductRecord = {
  id: string;
  sku: string;
  ean: string;
  referenceNumber: string;
  name: string;
  department: string;
  category: string;
  section: string;
  story: string;
  season: string;
  size: string;
  /** Winter / Non-winter */
  productType: 'Winter' | 'Non-winter';
  stockInwardDate: string;
  stockDispatchDate: string;
  stockDispatchQty: number;
  color: string;
  brand: string;
  channel: string;
  store: string;
  zone: string;
  sales: number;
  quantity: number;
  stock: number;
  margin: number;
  marginPct: number;
  discount: number;
  discountPct: number;
  sellThrough: number;
  ros: number;
  weekCover: number;
  lifecycle: 'New Arrival' | 'Growing' | 'Best Seller' | 'Mature' | 'Declining' | 'Dead Stock';
  abcClass: 'A' | 'B' | 'C';
  hue: number;
  imageUrl?: string;
};

export type ProductFilters = {
  dateRange: string[];
  season: string[];
  story: string[];
  department: string[];
  category: string[];
  section: string[];
  productType: string[];
  sku: string[];
  ean: string[];
  referenceNumber: string[];
  size: string[];
  stockInwardDate: string[];
  stockDispatchDate: string[];
  color: string[];
  brand: string[];
  channel: string[];
  store: string[];
  zone: string[];
};

export const EMPTY_PRODUCT_FILTERS: ProductFilters = {
  dateRange: [],
  season: [],
  story: [],
  department: [],
  category: [],
  section: [],
  productType: [],
  sku: [],
  ean: [],
  referenceNumber: [],
  size: [],
  stockInwardDate: [],
  stockDispatchDate: [],
  color: [],
  brand: [],
  channel: [],
  store: [],
  zone: [],
};

/** Compulsory Product & Inventory attributes — always present in filters + table */
export const PRODUCT_INVENTORY_FIELDS = [
  'season',
  'story',
  'sku',
  'ean',
  'size',
  'referenceNumber',
  'section',
  'department',
  'productType',
  'stockInwardDate',
  'stockDispatchDate',
  'image',
] as const;

const DEPARTMENTS = ['Mens Wear', 'Womens Wear', 'Kids', 'Footwear', 'Accessories', 'Ethnic'];
const CATEGORIES = ['Casual', 'Formal', 'Denim', 'Ethnic', 'Activewear', 'Winterwear', 'Innerwear'];
const SECTIONS = ['Topwear', 'Bottomwear', 'Outerwear', 'Footwear', 'Bags'];
const STORIES = ['Urban Classic', 'Festive Edit', 'Workwear', 'Premium Line', 'Weekend Casual', 'Athleisure'];
const SEASONS = ['AW-25', 'SS-25', 'AW-24', 'SS-24'];
const SIZES = ['XS', 'S', 'M', 'L', 'XL', 'XXL'];
const COLORS = ['Navy', 'Black', 'White', 'Olive', 'Maroon', 'Beige', 'Grey'];
const BRANDS = ['Brand A', 'Brand B', 'Brand C', 'In-House', 'Premium'];
const CHANNELS = ['Store', 'Online', 'Marketplace', 'EBO', 'Wholesale'];
const STORES = ['Phoenix Mall', 'Select Citywalk', 'Forum Mall', 'Inorbit Vashi', 'DLF Promenade', 'Orion Gateway'];
const ZONES = ['North', 'South', 'East', 'West', 'Central'];
const LIFECYCLES: ProductRecord['lifecycle'][] = [
  'New Arrival',
  'Growing',
  'Best Seller',
  'Mature',
  'Declining',
  'Dead Stock',
];

function formatDateOffset(daysAgo: number) {
  const d = new Date();
  d.setDate(d.getDate() - daysAgo);
  return d.toISOString().slice(0, 10);
}

export const FILTER_OPTIONS = {
  dateRange: ['Today', 'WTD', 'MTD', 'QTD', 'YTD', 'Last 30 Days', 'Last 90 Days'],
  season: SEASONS,
  story: STORIES,
  department: DEPARTMENTS,
  category: CATEGORIES,
  section: SECTIONS,
  productType: ['Winter', 'Non-winter'],
  sku: [] as string[],
  ean: [] as string[],
  referenceNumber: [] as string[],
  size: SIZES,
  stockInwardDate: [] as string[],
  stockDispatchDate: [] as string[],
  color: COLORS,
  brand: BRANDS,
  channel: CHANNELS,
  store: STORES,
  zone: ZONES,
};

function seededRandom(seed: number) {
  const x = Math.sin(seed) * 10000;
  return x - Math.floor(x);
}

export function generateProductCatalog(count = 120): ProductRecord[] {
  const products: ProductRecord[] = [];
  for (let i = 0; i < count; i++) {
    const dept = DEPARTMENTS[i % DEPARTMENTS.length];
    const cat = CATEGORIES[i % CATEGORIES.length];
    const productType: ProductRecord['productType'] =
      cat === 'Winterwear' || i % 3 === 0 ? 'Winter' : 'Non-winter';
    const qty = Math.round(80 + seededRandom(i * 7) * 420);
    const asp = Math.round(800 + seededRandom(i * 11) * 3200);
    const sales = qty * asp;
    const stock = Math.round(qty * (0.4 + seededRandom(i * 13) * 1.8));
    const marginPct = Math.round(28 + seededRandom(i * 17) * 32);
    const discountPct = Math.round(seededRandom(i * 19) * 28);
    const sellThrough = Math.min(95, Math.round(35 + seededRandom(i * 23) * 55));
    const ros = Math.round((qty / 30) * 10) / 10;
    const weekCover = Math.round((stock / Math.max(qty / 4, 1)) * 10) / 10;
    const lifecycle = LIFECYCLES[Math.floor(seededRandom(i * 29) * LIFECYCLES.length)];
    const inwardDays = Math.round(20 + seededRandom(i * 31) * 100);
    const dispatchDays = Math.max(1, inwardDays - Math.round(3 + seededRandom(i * 37) * 14));

    products.push({
      id: `P${1000 + i}`,
      sku: `SKU-${2000 + i}`,
      ean: `890${String(1000000000 + i * 7919).slice(0, 10)}`,
      referenceNumber: `REF-${8000 + i}`,
      name: `${cat} ${['Polo', 'Shirt', 'Jean', 'Kurta', 'Sneaker', 'Belt', 'Jacket'][i % 7]} ${i + 1}`,
      department: dept,
      category: cat,
      section: SECTIONS[i % SECTIONS.length],
      story: STORIES[i % STORIES.length],
      season: SEASONS[i % SEASONS.length],
      size: SIZES[i % SIZES.length],
      productType,
      stockInwardDate: formatDateOffset(inwardDays),
      stockDispatchDate: formatDateOffset(dispatchDays),
      stockDispatchQty: Math.round(qty * (0.15 + seededRandom(i * 41) * 0.35)),
      color: COLORS[i % COLORS.length],
      brand: BRANDS[i % BRANDS.length],
      channel: CHANNELS[i % CHANNELS.length],
      store: STORES[i % STORES.length],
      zone: ZONES[i % ZONES.length],
      sales,
      quantity: qty,
      stock,
      margin: sales * (marginPct / 100),
      marginPct,
      discount: sales * (discountPct / 100),
      discountPct,
      sellThrough,
      ros,
      weekCover,
      lifecycle,
      abcClass: 'C',
      hue: 210 + ((i * 17) % 120),
    });
  }

  const sorted = [...products].sort((a, b) => b.sales - a.sales);
  const totalSales = sorted.reduce((s, p) => s + p.sales, 0);
  let cumulative = 0;
  return sorted.map((p) => {
    cumulative += p.sales;
    const pct = (cumulative / totalSales) * 100;
    return {
      ...p,
      abcClass: (pct <= 70 ? 'A' : pct <= 90 ? 'B' : 'C') as 'A' | 'B' | 'C',
    };
  });
}

export const PRODUCT_CATALOG = generateProductCatalog(120);

export function formatInr(value: number, compact = false) {
  if (compact) {
    if (Math.abs(value) >= 1_00_00_000) return `₹${(value / 1_00_00_000).toFixed(1)}Cr`;
    if (Math.abs(value) >= 1_00_000) return `₹${(value / 1_00_000).toFixed(1)}L`;
    if (Math.abs(value) >= 1_000) return `₹${(value / 1_000).toFixed(1)}K`;
  }
  return new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    maximumFractionDigits: 0,
  }).format(value);
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
  return values
    .map((v, i) => {
      const x = i * step;
      const y = h - ((v - min) / range) * h;
      return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');
}

export function applyProductFilters(products: ProductRecord[], f: ProductFilters): ProductRecord[] {
  return products.filter((p) => {
    const checks: [string[], string][] = [
      [f.season, p.season],
      [f.story, p.story],
      [f.department, p.department],
      [f.category, p.category],
      [f.section, p.section],
      [f.productType, p.productType],
      [f.sku, p.sku],
      [f.ean, p.ean],
      [f.referenceNumber, p.referenceNumber],
      [f.size, p.size],
      [f.stockInwardDate, p.stockInwardDate],
      [f.stockDispatchDate, p.stockDispatchDate],
      [f.color, p.color],
      [f.brand, p.brand],
      [f.channel, p.channel],
      [f.store, p.store],
      [f.zone, p.zone],
    ];
    return checks.every(([arr, val]) => !arr.length || arr.includes(val));
  });
}

export function computeProductKpis(products: ProductRecord[]) {
  const sales = products.reduce((s, p) => s + p.sales, 0);
  const qty = products.reduce((s, p) => s + p.quantity, 0);
  const stock = products.reduce((s, p) => s + p.stock, 0);
  const invValue = stock * 850;
  const margin = products.reduce((s, p) => s + p.margin, 0);
  const discount = products.reduce((s, p) => s + p.discount, 0);
  const avgSt = products.length
    ? products.reduce((s, p) => s + p.sellThrough, 0) / products.length
    : 0;
  const avgDisc = sales > 0 ? (discount / sales) * 100 : 0;
  const asp = qty > 0 ? sales / qty : 0;
  const avgRos = products.length ? products.reduce((s, p) => s + p.ros, 0) / products.length : 0;
  const avgWc = products.length
    ? products.reduce((s, p) => s + p.weekCover, 0) / products.length
    : 0;
  const trend = products.slice(0, 8).map((p) => p.sales);

  return {
    sales,
    qty,
    stock,
    invValue,
    sellThrough: avgSt,
    margin,
    marginPct: sales > 0 ? (margin / sales) * 100 : 0,
    discountPct: avgDisc,
    asp,
    weekCover: avgWc,
    ros: avgRos,
    trend: trend.length ? trend : [1, 2, 3, 4, 5, 6, 7, 8],
    prevScale: 0.91,
  };
}

export type ProductKpiData = ReturnType<typeof computeProductKpis>;

export function aggregateBy(products: ProductRecord[], key: keyof ProductRecord) {
  const map = new Map<string, { sales: number; qty: number; margin: number }>();
  products.forEach((p) => {
    const k = String(p[key]);
    const cur = map.get(k) ?? { sales: 0, qty: 0, margin: 0 };
    cur.sales += p.sales;
    cur.qty += p.quantity;
    cur.margin += p.margin;
    map.set(k, cur);
  });
  return [...map.entries()]
    .map(([name, v]) => ({ name, ...v }))
    .sort((a, b) => b.sales - a.sales);
}

export function buildTrendData(
  totalSales: number,
  period: 'daily' | 'weekly' | 'monthly' | 'quarterly' | 'yearly'
) {
  const n =
    period === 'daily'
      ? 14
      : period === 'weekly'
        ? 8
        : period === 'monthly'
          ? 12
          : period === 'quarterly'
            ? 4
            : 5;
  const labels =
    period === 'daily'
      ? Array.from({ length: n }, (_, i) => `D${i + 1}`)
      : period === 'weekly'
        ? Array.from({ length: n }, (_, i) => `W${i + 1}`)
        : period === 'monthly'
          ? ['Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec', 'Jan', 'Feb', 'Mar'].slice(
              0,
              n
            )
          : period === 'quarterly'
            ? ['Q1', 'Q2', 'Q3', 'Q4']
            : ['FY26', 'FY27'].slice(0, n);
  const base = totalSales / n;
  return labels.map((label, i) => ({
    label,
    sales: base * (0.82 + i * 0.035 + Math.sin(i * 0.6) * 0.08),
    quantity: Math.round((base / 1200) * (0.85 + i * 0.03)),
  }));
}

export type DrillLevel = 'department' | 'category' | 'section' | 'story' | 'sku' | 'ean';

export const DRILL_ORDER: DrillLevel[] = ['department', 'category', 'section', 'story', 'sku', 'ean'];

export function drillValue(p: ProductRecord, level: DrillLevel) {
  return p[level === 'sku' ? 'sku' : level === 'ean' ? 'ean' : level];
}

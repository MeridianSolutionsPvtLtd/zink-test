/** India map geometry + city anchors for Regional Performance (SVG, no map SDK). */

export type ZoneId = 'north' | 'south' | 'east' | 'west' | 'central';

export type ZonePath = {
  id: ZoneId;
  name: string;
  /** Approximate geographic zone polygon inside India viewBox */
  path: string;
};

/** viewBox: 0 0 420 520 — simplified India silhouette */
export const INDIA_OUTLINE =
  'M210,28 L235,32 L255,48 L268,70 L278,95 L290,118 L305,140 L318,155 L328,175 L335,200 L340,225 L338,250 L330,275 L318,295 L305,318 L295,345 L288,370 L280,395 L268,420 L250,440 L230,455 L210,462 L190,455 L170,440 L152,420 L140,395 L132,370 L125,345 L115,318 L102,295 L90,275 L82,250 L80,225 L85,200 L92,175 L102,155 L115,140 L130,118 L142,95 L152,70 L165,48 L185,32 Z';

/**
 * Zone fills roughly matching North / West / Central / East / South geography.
 * Not exact state borders — readable executive zones on a stylized India map.
 */
export const ZONE_PATHS: ZonePath[] = [
  {
    id: 'north',
    name: 'North',
    path: 'M168,55 L210,35 L248,48 L262,78 L255,115 L230,130 L185,128 L162,100 Z',
  },
  {
    id: 'west',
    name: 'West',
    path: 'M95,175 L155,155 L175,210 L168,265 L145,295 L110,280 L95,230 Z',
  },
  {
    id: 'central',
    name: 'Central',
    path: 'M175,145 L255,140 L268,195 L255,250 L200,265 L168,230 L162,180 Z',
  },
  {
    id: 'east',
    name: 'East',
    path: 'M268,130 L325,155 L335,210 L318,255 L275,245 L255,195 Z',
  },
  {
    id: 'south',
    name: 'South',
    path: 'M155,280 L255,265 L295,300 L280,360 L250,420 L210,445 L170,420 L145,360 L140,310 Z',
  },
];

/** Major retail cities with lat/lng — projected onto the SVG */
export const CITY_ANCHORS: {
  id: string;
  name: string;
  zone: ZoneId;
  lat: number;
  lng: number;
}[] = [
  { id: 'delhi', name: 'Delhi', zone: 'north', lat: 28.61, lng: 77.21 },
  { id: 'new-delhi', name: 'New Delhi', zone: 'north', lat: 28.61, lng: 77.21 },
  { id: 'noida', name: 'Noida', zone: 'north', lat: 28.53, lng: 77.39 },
  { id: 'gurgaon', name: 'Gurgaon', zone: 'north', lat: 28.45, lng: 77.02 },
  { id: 'chandigarh', name: 'Chandigarh', zone: 'north', lat: 30.73, lng: 76.78 },
  { id: 'ludhiana', name: 'Ludhiana', zone: 'north', lat: 30.9, lng: 75.85 },
  { id: 'amritsar', name: 'Amritsar', zone: 'north', lat: 31.63, lng: 74.87 },
  { id: 'jaipur', name: 'Jaipur', zone: 'west', lat: 26.91, lng: 75.79 },
  { id: 'lucknow', name: 'Lucknow', zone: 'north', lat: 26.85, lng: 80.95 },
  { id: 'kanpur', name: 'Kanpur', zone: 'north', lat: 26.44, lng: 80.33 },
  { id: 'varanasi', name: 'Varanasi', zone: 'north', lat: 25.31, lng: 82.97 },
  { id: 'dehradun', name: 'Dehradun', zone: 'north', lat: 30.31, lng: 78.03 },
  { id: 'mumbai', name: 'Mumbai', zone: 'west', lat: 19.08, lng: 72.88 },
  { id: 'pune', name: 'Pune', zone: 'west', lat: 18.52, lng: 73.86 },
  { id: 'thane', name: 'Thane', zone: 'west', lat: 19.21, lng: 72.97 },
  { id: 'ahmedabad', name: 'Ahmedabad', zone: 'west', lat: 23.02, lng: 72.57 },
  { id: 'surat', name: 'Surat', zone: 'west', lat: 21.17, lng: 72.83 },
  { id: 'vadodara', name: 'Vadodara', zone: 'west', lat: 22.3, lng: 73.18 },
  { id: 'bengaluru', name: 'Bengaluru', zone: 'south', lat: 12.97, lng: 77.59 },
  { id: 'bangalore', name: 'Bangalore', zone: 'south', lat: 12.97, lng: 77.59 },
  { id: 'chennai', name: 'Chennai', zone: 'south', lat: 13.08, lng: 80.27 },
  { id: 'hyderabad', name: 'Hyderabad', zone: 'south', lat: 17.39, lng: 78.49 },
  { id: 'kochi', name: 'Kochi', zone: 'south', lat: 9.93, lng: 76.27 },
  { id: 'coimbatore', name: 'Coimbatore', zone: 'south', lat: 11.01, lng: 76.95 },
  { id: 'visakhapatnam', name: 'Visakhapatnam', zone: 'south', lat: 17.68, lng: 83.21 },
  { id: 'kolkata', name: 'Kolkata', zone: 'east', lat: 22.57, lng: 88.36 },
  { id: 'patna', name: 'Patna', zone: 'east', lat: 25.59, lng: 85.13 },
  { id: 'ranchi', name: 'Ranchi', zone: 'east', lat: 23.34, lng: 85.3, },
  { id: 'bhubaneswar', name: 'Bhubaneswar', zone: 'east', lat: 20.3, lng: 85.82 },
  { id: 'guwahati', name: 'Guwahati', zone: 'east', lat: 26.14, lng: 91.74 },
  { id: 'indore', name: 'Indore', zone: 'central', lat: 22.72, lng: 75.86 },
  { id: 'nagpur', name: 'Nagpur', zone: 'central', lat: 21.15, lng: 79.09 },
  { id: 'bhopal', name: 'Bhopal', zone: 'central', lat: 23.26, lng: 77.41 },
  { id: 'raipur', name: 'Raipur', zone: 'central', lat: 21.25, lng: 81.63 },
];

export function findCityCoords(name: string): { lat: number; lng: number } {
  const norm = name.trim().toLowerCase();
  const match = CITY_ANCHORS.find((c) => c.name.toLowerCase() === norm || c.id === norm);
  if (match) return { lat: match.lat, lng: match.lng };
  if (norm.includes('delhi')) return { lat: 28.61, lng: 77.21 };
  if (norm.includes('mumbai')) return { lat: 19.08, lng: 72.88 };
  if (norm.includes('bangalore') || norm.includes('bengaluru')) return { lat: 12.97, lng: 77.59 };
  if (norm.includes('pune')) return { lat: 18.52, lng: 73.86 };
  if (norm.includes('kolkata')) return { lat: 22.57, lng: 88.36 };
  if (norm.includes('hyderabad')) return { lat: 17.39, lng: 78.49 };
  if (norm.includes('noida')) return { lat: 28.53, lng: 77.39 };
  if (norm.includes('gurgaon') || norm.includes('gurugram')) return { lat: 28.45, lng: 77.02 };
  if (norm.includes('ahmedabad')) return { lat: 23.02, lng: 72.57 };
  if (norm.includes('lucknow')) return { lat: 26.85, lng: 80.95 };
  // Default central fallback if city is unknown
  return { lat: 21.5, lng: 78.5 };
}

/** Equirectangular project India lat/lng → SVG x,y inside viewBox 420×520 */
export function projectIndia(lat: number, lng: number): { x: number; y: number } {
  const minLng = 68;
  const maxLng = 97;
  const minLat = 8;
  const maxLat = 37;
  const padX = 40;
  const padY = 30;
  const w = 420 - padX * 2;
  const h = 520 - padY * 2;
  const x = padX + ((lng - minLng) / (maxLng - minLng)) * w;
  const y = padY + ((maxLat - lat) / (maxLat - minLat)) * h;
  return { x, y };
}

export function zoneFill(intensity: number, active: boolean, muted: boolean): string {
  // intensity 0–1 → blue scale
  const t = Math.max(0, Math.min(1, intensity));
  if (muted) return '#e2e8f0';
  if (active) return '#1d4ed8';
  const lightness = 78 - t * 38;
  return `hsl(217, 85%, ${lightness}%)`;
}

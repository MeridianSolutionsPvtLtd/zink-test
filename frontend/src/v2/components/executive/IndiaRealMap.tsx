import React, { useEffect, useMemo, useRef, useState } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { formatInr } from './utils';
import type { CityMapData, RegionMapData } from './ExecutiveSections';

const STATE_TO_ZONE: Record<string, string> = {
  // —— North ——
  'jammu and kashmir': 'North',
  'jammu & kashmir': 'North',
  ladakh: 'North',
  'himachal pradesh': 'North',
  punjab: 'North',
  haryana: 'North',
  uttarakhand: 'North',
  uttaranchal: 'North',
  'uttar pradesh': 'North',
  delhi: 'North',
  'nct of delhi': 'North',
  chandigarh: 'North',

  // —— West ——
  rajasthan: 'West',
  gujarat: 'West',
  maharashtra: 'West',
  goa: 'West',
  'daman and diu': 'West',
  'dadra and nagar haveli': 'West',
  'dadra and nagar haveli and daman and diu': 'West',

  // —— Central ——
  'madhya pradesh': 'Central',
  chhattisgarh: 'Central',

  // —— East ——
  bihar: 'East',
  jharkhand: 'East',
  odisha: 'East',
  orissa: 'East',
  'west bengal': 'East',
  sikkim: 'East',
  assam: 'East',
  tripura: 'East',
  meghalaya: 'East',
  manipur: 'East',
  mizoram: 'East',
  nagaland: 'East',
  'arunachal pradesh': 'East',

  // —— South ——
  'andhra pradesh': 'South',
  telangana: 'South',
  karnataka: 'South',
  kerala: 'South',
  'tamil nadu': 'South',
  puducherry: 'South',
  'andaman and nicobar': 'South',
  'andaman and nicobar islands': 'South',
  lakshadweep: 'South',
};

/** Sales heat scale — light (low) → deep blue (high) */
function salesHeatColor(intensity: number): string {
  const t = Math.max(0, Math.min(1, intensity));
  const stops = [
    [219, 234, 254], // #dbeafe
    [147, 197, 253], // #93c5fd
    [59, 130, 246],  // #3b82f6
    [37, 99, 235],   // #2563eb
    [30, 64, 175],   // #1e40af
    [30, 58, 138],   // #1e3a8a
  ];
  const idx = t * (stops.length - 1);
  const i = Math.floor(idx);
  const f = idx - i;
  const a = stops[i];
  const b = stops[Math.min(i + 1, stops.length - 1)];
  const r = Math.round(a[0] + (b[0] - a[0]) * f);
  const g = Math.round(a[1] + (b[1] - a[1]) * f);
  const bl = Math.round(a[2] + (b[2] - a[2]) * f);
  return `rgb(${r},${g},${bl})`;
}

function cityHeatColor(intensity: number): string {
  const t = Math.max(0, Math.min(1, intensity));
  if (t < 0.33) return '#fbbf24';
  if (t < 0.66) return '#f97316';
  return '#dc2626';
}

function normalizeStateName(raw: string): string {
  return raw.trim().toLowerCase().replace(/\s+/g, ' ');
}

function zoneForState(props: Record<string, unknown>): string {
  const candidates = [props.NAME_1, props.ST_NM, props.st_nm, props.name, props.NAME, props.State_Name, props.state]
    .filter(Boolean)
    .map((v) => normalizeStateName(String(v)));

  for (const c of candidates) {
    if (STATE_TO_ZONE[c]) return STATE_TO_ZONE[c];
  }
  for (const c of candidates) {
    for (const [key, zone] of Object.entries(STATE_TO_ZONE)) {
      if (key.length < 4) continue;
      if (c === key || c.includes(key) || key.includes(c)) return zone;
    }
  }
  return 'Central';
}

function stateLabel(props: Record<string, unknown>): string {
  return String(
    props.NAME_1 || props.ST_NM || props.st_nm || props.name || props.NAME || props.State_Name || 'State'
  );
}

type HoverInfo = {
  title: string;
  zone: string;
  sales?: number;
  growth?: number;
  stores?: number;
  target?: number;
};

type Props = {
  key?: React.Key;
  regions: RegionMapData[];
  cities: CityMapData[];
  selectedZone?: string | null;
  onZoneClick?: (zone: string) => void;
  onCityClick?: (city: string) => void;
  mode: 'zone' | 'city';
};

const INDIA_BOUNDS = L.latLngBounds([6.5, 68.0], [37.5, 97.5]);
const GEO_URL = '/geo/india_states.geojson?v=4';

function styleForSales(
  zone: string,
  salesIntensity: number,
  selectedZone: string | null | undefined,
  mode: 'zone' | 'city'
): L.PathOptions {
  const isSelected = Boolean(selectedZone && selectedZone.toLowerCase() === zone.toLowerCase());
  const muted = Boolean(selectedZone && !isSelected);
  const heat = salesHeatColor(salesIntensity);
  return {
    fillColor: muted ? '#cbd5e1' : heat,
    fillOpacity: mode === 'zone' ? (isSelected ? 0.92 : muted ? 0.22 : 0.78) : 0.15,
    weight: isSelected ? 2.5 : 1.2,
    color: isSelected ? '#0f172a' : '#ffffff',
  };
}

type LayerWithZone = L.Path & { __zone?: string };

export function IndiaRealMap({
  regions,
  cities,
  selectedZone,
  onZoneClick,
  onCityClick,
  mode,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const geoLayerRef = useRef<L.GeoJSON | null>(null);
  const cityLayerRef = useRef<L.LayerGroup | null>(null);
  const onZoneClickRef = useRef(onZoneClick);
  const regionByZoneRef = useRef(new Map<string, RegionMapData>());
  const [geoReady, setGeoReady] = useState(false);
  const [hoverInfo, setHoverInfo] = useState<HoverInfo | null>(null);

  const selectedZoneRef = useRef(selectedZone);
  const modeRef = useRef(mode);
  selectedZoneRef.current = selectedZone;
  modeRef.current = mode;
  onZoneClickRef.current = onZoneClick;

  const regionByZone = useMemo(() => {
    const m = new Map<string, RegionMapData>();
    regions.forEach((r) => m.set(r.name.toLowerCase(), r));
    return m;
  }, [regions]);
  regionByZoneRef.current = regionByZone;

  const maxZoneSales = Math.max(...regions.map((r) => r.sales), 1);
  const maxCitySales = Math.max(...cities.map((c) => c.sales), 1);

  const salesIntensityFor = (zone: string) => {
    const reg = regionByZone.get(zone.toLowerCase());
    return reg ? reg.sales / maxZoneSales : 0.15;
  };

  const intensityRef = useRef(salesIntensityFor);
  intensityRef.current = salesIntensityFor;

  const zonesBySales = useMemo(
    () => [...regions].sort((a, b) => b.sales - a.sales),
    [regions]
  );

  // Init Leaflet map with clean Esri Light Gray Canvas basemap
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    const map = L.map(containerRef.current, {
      center: [22.5, 82],
      zoom: 5,
      minZoom: 4,
      maxZoom: 12,
      maxBounds: INDIA_BOUNDS.pad(0.2),
      maxBoundsViscosity: 0.9,
      zoomControl: true,
    });

    // Clean Esri World Light Gray Canvas basemap — NO API key required, NO watermark
    L.tileLayer(
      'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}',
      {
        attribution: '&copy; Esri, HERE, Garmin, FAO, USGS',
        maxZoom: 16,
      }
    ).addTo(map);

    map.fitBounds(INDIA_BOUNDS, { padding: [16, 16] });
    cityLayerRef.current = L.layerGroup().addTo(map);
    mapRef.current = map;

    let cancelled = false;

    (async () => {
      try {
        const res = await fetch(GEO_URL, { cache: 'no-cache' });
        if (!res.ok) throw new Error(`Missing GeoJSON file (${res.status})`);
        const data = (await res.json()) as GeoJSON.FeatureCollection;

        if (cancelled || !mapRef.current) return;

        const layer = L.geoJSON(data, {
          style: {
            weight: 1.2,
            color: '#ffffff',
            fillOpacity: 0.78,
            fillColor: '#3b82f6',
          },
          onEachFeature: (feature, lyr) => {
            const props = (feature.properties || {}) as Record<string, unknown>;
            const zone = zoneForState(props);
            const name = stateLabel(props);
            const path = lyr as LayerWithZone;
            path.__zone = zone;

            path.setStyle(
              styleForSales(
                zone,
                intensityRef.current(zone),
                selectedZoneRef.current,
                modeRef.current
              )
            );

            path.on({
              mouseover: (e) => {
                (e.target as L.Path).setStyle({ weight: 2.8, fillOpacity: 0.95 });
                const reg = regionByZoneRef.current.get(zone.toLowerCase());
                setHoverInfo({
                  title: name,
                  zone,
                  sales: reg?.sales,
                  growth: reg?.growth,
                  stores: reg?.stores,
                  target: reg?.target,
                });
              },
              mouseout: (e) => {
                const t = e.target as LayerWithZone;
                const z = t.__zone || zone;
                t.setStyle(
                  styleForSales(z, intensityRef.current(z), selectedZoneRef.current, modeRef.current)
                );
                setHoverInfo(null);
              },
              click: () => onZoneClickRef.current?.(zone),
            });
          },
        });

        layer.addTo(mapRef.current);
        geoLayerRef.current = layer;
        setGeoReady(true);
        window.setTimeout(() => mapRef.current?.invalidateSize(), 100);
      } catch (err) {
        console.error('GeoJSON boundary load error:', err);
      }
    })();

    return () => {
      cancelled = true;
      map.remove();
      mapRef.current = null;
      geoLayerRef.current = null;
      cityLayerRef.current = null;
    };
  }, []);

  // Update Choropleth state colors — darker blue = higher zone sales
  useEffect(() => {
    const layer = geoLayerRef.current;
    if (!layer || !geoReady) return;

    layer.eachLayer((lyr) => {
      const path = lyr as LayerWithZone;
      const zone =
        path.__zone ||
        zoneForState(
          (((lyr as L.Layer & { feature?: GeoJSON.Feature }).feature?.properties ||
            {}) as Record<string, unknown>)
        );
      path.__zone = zone;
      path.setStyle(styleForSales(zone, salesIntensityFor(zone), selectedZone, mode));
    });
  }, [selectedZone, mode, geoReady, regions, maxZoneSales]);

  // Update City Bubbles
  useEffect(() => {
    const group = cityLayerRef.current;
    if (!group) return;
    group.clearLayers();
    if (mode !== 'city') return;

    const list = selectedZone
      ? cities.filter((c) => c.zone.toLowerCase() === selectedZone.toLowerCase())
      : cities;

    list.forEach((city) => {
      const intensity = city.sales / maxCitySales;
      const r = 8 + intensity * 24;
      const marker = L.circleMarker([city.lat, city.lng], {
        radius: Math.max(7, Math.min(r, 30)),
        color: '#fff',
        weight: 2,
        fillColor: cityHeatColor(intensity),
        fillOpacity: 0.9,
      });

      marker.bindTooltip(
        `<strong>${city.name}</strong><br/>${city.zone} · ${formatInr(city.sales, true)}<br/>Stores: ${city.stores}`,
        { direction: 'top', opacity: 0.95 }
      );
      marker.on('click', () => onCityClick?.(city.name));
      marker.on('mouseover', () =>
        setHoverInfo({
          title: city.name,
          zone: city.zone,
          sales: city.sales,
          growth: city.growth,
          stores: city.stores,
        })
      );
      marker.on('mouseout', () => setHoverInfo(null));
      group.addLayer(marker);
    });
  }, [cities, mode, selectedZone, maxCitySales, onCityClick]);

  useEffect(() => {
    const t = window.setTimeout(() => mapRef.current?.invalidateSize(), 120);
    return () => window.clearTimeout(t);
  }, [mode, geoReady]);

  return (
    <div className="relative h-[440px] w-full overflow-hidden rounded-xl border border-slate-200/60 dark:border-slate-700">
      <div ref={containerRef} className="z-0 h-full w-full" />

      {hoverInfo && (
        <div className="pointer-events-none absolute right-3 top-3 z-[500] w-52 rounded-2xl border border-slate-200/80 bg-white/95 p-3 shadow-xl backdrop-blur dark:border-slate-700 dark:bg-slate-900/95">
          <p className="text-sm font-bold text-slate-900 dark:text-white">{hoverInfo.title}</p>
          <p className="text-[10px] font-semibold uppercase tracking-wider text-blue-600">
            {hoverInfo.zone} zone
          </p>
          <div className="mt-2 space-y-1 text-xs text-slate-600 dark:text-slate-300">
            {hoverInfo.sales != null && (
              <p>
                Sales: <span className="font-semibold">{formatInr(hoverInfo.sales, true)}</span>
              </p>
            )}
            {hoverInfo.target != null && (
              <p>
                Target: <span className="font-semibold">{formatInr(hoverInfo.target, true)}</span>
              </p>
            )}
            {hoverInfo.growth != null && (
              <p>
                Growth:{' '}
                <span
                  className={`font-semibold ${
                    hoverInfo.growth >= 0 ? 'text-emerald-600' : 'text-rose-600'
                  }`}
                >
                  {hoverInfo.growth >= 0 ? '+' : ''}
                  {hoverInfo.growth.toFixed(1)}%
                </span>
              </p>
            )}
            {hoverInfo.stores != null && (
              <p>
                Stores: <span className="font-semibold">{hoverInfo.stores}</span>
              </p>
            )}
          </div>
        </div>
      )}

      <div className="absolute bottom-3 left-3 z-[500] max-w-[200px] rounded-lg border border-slate-200/80 bg-white/95 px-2.5 py-2 shadow dark:border-slate-700 dark:bg-slate-900/95">
        {mode === 'zone' ? (
          <>
            <p className="mb-1.5 text-[9px] font-bold uppercase tracking-wider text-slate-400">
              Sales intensity
            </p>
            <div className="mb-2 flex h-2.5 overflow-hidden rounded-full">
              {[0, 0.25, 0.5, 0.75, 1].map((t) => (
                <div key={t} className="flex-1" style={{ background: salesHeatColor(t) }} />
              ))}
            </div>
            <div className="mb-2 flex justify-between text-[9px] text-slate-400">
              <span>Low</span>
              <span>High sales</span>
            </div>
            <div className="flex flex-col gap-1">
              {zonesBySales.map((r, i) => (
                <button
                  key={r.id}
                  type="button"
                  onClick={() => onZoneClick?.(r.name)}
                  className={`flex items-center gap-1.5 rounded px-1 py-0.5 text-[10px] font-semibold ${
                    selectedZone === r.name ? 'bg-blue-50 text-blue-800' : 'text-slate-600'
                  }`}
                >
                  <span
                    className="h-2.5 w-2.5 shrink-0 rounded-sm"
                    style={{ background: salesHeatColor(r.sales / maxZoneSales) }}
                  />
                  <span className="truncate">
                    {i + 1}. {r.name}
                  </span>
                  <span className="ml-auto tabular-nums text-slate-400">
                    {formatInr(r.sales, true)}
                  </span>
                </button>
              ))}
            </div>
          </>
        ) : (
          <>
            <p className="mb-1 text-[9px] font-bold uppercase tracking-wider text-slate-400">
              City sales
            </p>
            <p className="text-[10px] text-slate-500">Bubble size + colour = sales</p>
            <div className="mt-1.5 flex items-center gap-1 text-[9px] text-slate-400">
              <span className="h-2.5 w-2.5 rounded-full bg-amber-400" /> Low
              <span className="h-2.5 w-2.5 rounded-full bg-orange-500" /> Mid
              <span className="h-2.5 w-2.5 rounded-full bg-red-600" /> High
            </div>
          </>
        )}
      </div>
    </div>
  );
}

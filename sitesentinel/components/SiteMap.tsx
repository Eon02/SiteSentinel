'use client';

import 'maplibre-gl/dist/maplibre-gl.css';
import { useEffect, useMemo, useRef, useState } from 'react';
import Map, { Layer, Marker, NavigationControl, Popup, Source, type MapLayerMouseEvent, type MapRef } from 'react-map-gl/maplibre';
import HazardIcon from './HazardIcon';
import { MAP_STYLE } from '@/lib/maplibre';
import { factorLabel, formatScore, riskColor, riskLevel } from '@/lib/risk';
import { hazardLabel, type HazardReport, type Site } from '@/lib/types';

const US_VIEW = { longitude: -96, latitude: 38.5, zoom: 3.2 };

type Props = {
  sites: Site[];
  reports?: HazardReport[];
  stopWorkSiteIds?: Set<string>;
  selectedId?: string | null;
  onSelect?: (id: string) => void;
  initialView?: { longitude: number; latitude: number; zoom: number };
  className?: string;
};

// Load with next/dynamic({ ssr: false }); MapLibre needs the browser.
export default function SiteMap({
  sites, reports = [], stopWorkSiteIds, selectedId, onSelect, initialView = US_VIEW, className,
}: Props) {
  const mapRef = useRef<MapRef>(null);
  const [hoverId, setHoverId] = useState<string | null>(null);
  const [openReportId, setOpenReportId] = useState<number | null>(null);

  // The API stays plain JSON; convert to GeoJSON here.
  const geojson = useMemo<GeoJSON.FeatureCollection<GeoJSON.Point>>(() => ({
    type: 'FeatureCollection',
    features: sites.map((s) => ({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [s.lon, s.lat] },
      properties: {
        id: s.id,
        score: s.score,
        color: riskColor(s.score),
        radius: 6 + (Math.min(Math.max(s.score, 0), 10) / 10) * 6,
      },
    })),
  }), [sites]);

  // Fly to the selected site whenever the selection changes.
  const selected = sites.find((s) => s.id === selectedId);
  const selectedLon = selected?.lon;
  const selectedLat = selected?.lat;
  useEffect(() => {
    if (selectedLon === undefined || selectedLat === undefined) return;
    const map = mapRef.current;
    map?.flyTo({ center: [selectedLon, selectedLat], zoom: Math.max(map.getZoom(), 7), duration: 1200 });
  }, [selectedId, selectedLon, selectedLat]);

  const popupSite = sites.find((s) => s.id === (hoverId ?? selectedId));
  const openReport = reports.find((r) => r.id === openReportId);

  const siteIdAt = (e: MapLayerMouseEvent) => e.features?.[0]?.properties?.id as string | undefined;

  return (
    <div className={className}>
      <Map
        ref={mapRef}
        initialViewState={initialView}
        mapStyle={MAP_STYLE}
        style={{ width: '100%', height: '100%' }}
        interactiveLayerIds={['site-circles']}
        cursor={hoverId ? 'pointer' : 'grab'}
        onMouseMove={(e) => setHoverId(siteIdAt(e) ?? null)}
        onMouseLeave={() => setHoverId(null)}
        onClick={(e) => {
          const id = siteIdAt(e);
          if (id) onSelect?.(id);
        }}
      >
        <NavigationControl position="top-right" showCompass={false} />
        <Source id="sites" type="geojson" data={geojson}>
          <Layer
            id="site-circles"
            type="circle"
            paint={{
              'circle-color': ['get', 'color'],
              'circle-radius': ['get', 'radius'],
              'circle-stroke-color': '#16213E',
              'circle-stroke-width': ['case', ['==', ['get', 'id'], selectedId ?? ''], 3.5, 1.5],
              'circle-opacity': 0.95,
            }}
          />
        </Source>

        {sites
          .filter((s) => stopWorkSiteIds?.has(s.id))
          .map((s) => (
            <Marker key={`pulse-${s.id}`} longitude={s.lon} latitude={s.lat} style={{ pointerEvents: 'none' }}>
              <span className="pulse-ring" aria-hidden />
            </Marker>
          ))}

        {reports.map((r) => (
          <Marker key={`report-${r.id}`} longitude={r.lon} latitude={r.lat} anchor="bottom">
            <button
              type="button"
              aria-label={`Crew report: ${hazardLabel(r.hazardType)}`}
              onClick={(e) => {
                e.stopPropagation();
                setOpenReportId(r.id);
              }}
              className="flex h-7 w-7 items-center justify-center rounded-full border-2 border-[#16213E] bg-white text-[#16213E] shadow"
            >
              <HazardIcon type={r.hazardType} size={15} />
            </button>
          </Marker>
        ))}

        {popupSite && !openReport && (
          <Popup
            longitude={popupSite.lon}
            latitude={popupSite.lat}
            closeButton={false}
            closeOnClick={false}
            offset={14}
          >
            <div className="text-sm text-slate-900">
              <div className="font-bold">{popupSite.name}</div>
              <div>
                Score <b>{formatScore(popupSite.score)}</b> ({riskLevel(popupSite.score).label})
              </div>
              {popupSite.topFactors[0] && <div>Top factor: {factorLabel(popupSite.topFactors[0].factor)}</div>}
            </div>
          </Popup>
        )}

        {openReport && (
          <Popup
            longitude={openReport.lon}
            latitude={openReport.lat}
            anchor="bottom"
            offset={30}
            onClose={() => setOpenReportId(null)}
            closeOnClick
          >
            <div className="max-w-56 text-sm text-slate-900">
              <div className="font-bold">{hazardLabel(openReport.hazardType)}</div>
              {openReport.description && <p>{openReport.description}</p>}
              {openReport.photoUrl && (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={openReport.photoUrl} alt="Hazard photo" className="mt-1 max-h-32 rounded" />
              )}
              <div className="mt-1 text-xs text-slate-700">
                {openReport.reportedBy ? `${openReport.reportedBy} · ` : ''}
                {new Date(openReport.createdAt).toLocaleString()}
              </div>
            </div>
          </Popup>
        )}
      </Map>
    </div>
  );
}

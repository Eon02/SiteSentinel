'use client';

import 'maplibre-gl/dist/maplibre-gl.css';
import { useEffect, useRef } from 'react';
import Map, { Marker, NavigationControl, type MapRef } from 'react-map-gl/maplibre';
import { MapPin } from 'lucide-react';
import { MAP_STYLE } from '@/lib/maplibre';

type Props = { lat: number; lon: number; onChange: (lat: number, lon: number) => void };

// Small map with a draggable pin. Load with next/dynamic({ ssr: false }).
export default function LocationPickerMap({ lat, lon, onChange }: Props) {
  const mapRef = useRef<MapRef>(null);

  // Recenter when the location changes from outside (GPS fix or a new site), not while dragging.
  const dragging = useRef(false);
  useEffect(() => {
    if (!dragging.current) mapRef.current?.easeTo({ center: [lon, lat], duration: 600 });
  }, [lat, lon]);

  return (
    <Map
      ref={mapRef}
      initialViewState={{ longitude: lon, latitude: lat, zoom: 15 }}
      mapStyle={MAP_STYLE}
      style={{ width: '100%', height: '100%' }}
      onClick={(e) => onChange(e.lngLat.lat, e.lngLat.lng)}
    >
      <NavigationControl position="top-right" showCompass={false} />
      <Marker
        longitude={lon}
        latitude={lat}
        anchor="bottom"
        draggable
        onDragStart={() => (dragging.current = true)}
        onDragEnd={(e) => {
          onChange(e.lngLat.lat, e.lngLat.lng);
          setTimeout(() => (dragging.current = false), 0);
        }}
      >
        <MapPin size={40} className="fill-[#E8590C] text-[#16213E]" aria-label="Hazard location (drag to adjust)" />
      </Marker>
    </Map>
  );
}

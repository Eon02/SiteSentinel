-- FEMA National Flood Hazard Layer (NFHL) flood zones around each site.
-- Source: https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28
-- Loaded by scripts/load_flood_zones.py. Polygons are clipped to the site's search radius,
-- since a single FEMA polygon can cover a whole county.

CREATE TABLE flood_zones (
  id            bigserial PRIMARY KEY,
  site_id       text NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
  fld_ar_id     text NOT NULL,          -- FEMA flood area id
  dfirm_id      text,                   -- FEMA flood map (county/community) id
  fld_zone      text NOT NULL,          -- 'A', 'AE', 'AH', 'AO', 'VE', 'X', 'D', ...
  zone_subtype  text,                   -- e.g. '0.2 PCT ANNUAL CHANCE FLOOD HAZARD', 'FLOODWAY'
  sfha          boolean NOT NULL,       -- Special Flood Hazard Area: 1% annual chance flood
  static_bfe    real,                   -- base flood elevation; null when FEMA has none
  depth         real,                   -- flood depth for AO zones; null when FEMA has none
  len_unit      text,                   -- unit for static_bfe and depth, usually 'Feet'
  geom          geometry(MultiPolygon, 4326) NOT NULL,
  UNIQUE (site_id, fld_ar_id)
);

CREATE INDEX flood_zones_geom_idx ON flood_zones USING gist (geom);

-- One row per site per load, so "no FEMA data here" can be told apart from "never loaded".
CREATE TABLE flood_zone_loads (
  site_id     text PRIMARY KEY REFERENCES sites(id) ON DELETE CASCADE,
  radius_mi   real NOT NULL,
  zone_count  integer NOT NULL,
  loaded_at   timestamptz NOT NULL DEFAULT now()
);

-- Flood summary per site: the zone at the site point and the distance to the nearest
-- Special Flood Hazard Area within the loaded radius.
CREATE VIEW site_flood_risk WITH (security_invoker = true) AS
SELECT
  s.id                          AS site_id,
  l.loaded_at,
  l.radius_mi,
  at_site.fld_zone              AS zone_at_site,
  at_site.zone_subtype          AS zone_subtype_at_site,
  coalesce(at_site.sfha, false) AS site_in_sfha,
  nearest.sfha_distance_mi      AS nearest_sfha_mi
FROM sites s
LEFT JOIN flood_zone_loads l ON l.site_id = s.id
LEFT JOIN LATERAL (
  SELECT fz.fld_zone, fz.zone_subtype, fz.sfha
  FROM flood_zones fz
  WHERE fz.site_id = s.id AND ST_Intersects(fz.geom, s.geom)
  ORDER BY fz.sfha DESC
  LIMIT 1
) at_site ON true
LEFT JOIN LATERAL (
  SELECT round((min(ST_Distance(fz.geom::geography, s.geom::geography)) / 1609.344)::numeric, 2)
           AS sfha_distance_mi
  FROM flood_zones fz
  WHERE fz.site_id = s.id AND fz.sfha
) nearest ON true;

-- Same reasoning as 0002: block Supabase's public REST API; the backend bypasses RLS.
-- security_invoker above makes the view respect these policies too.
ALTER TABLE flood_zones ENABLE ROW LEVEL SECURITY;
ALTER TABLE flood_zone_loads ENABLE ROW LEVEL SECURITY;

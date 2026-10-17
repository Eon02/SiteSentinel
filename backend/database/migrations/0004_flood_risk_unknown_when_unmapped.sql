-- site_flood_risk previously reported site_in_sfha = false for sites FEMA hasn't mapped
-- (or that were never loaded), which reads as "safe". Report unknown (null) instead, and
-- add fema_mapped so "no high-risk zone nearby" can be told apart from "no data".
--
--   fema_mapped   null = flood zones never loaded, false = no FEMA map coverage, true = mapped
--   site_in_sfha  null unless the site point falls inside a mapped zone

CREATE OR REPLACE VIEW site_flood_risk WITH (security_invoker = true) AS
SELECT
  s.id                          AS site_id,
  l.loaded_at,
  l.radius_mi,
  at_site.fld_zone              AS zone_at_site,
  at_site.zone_subtype          AS zone_subtype_at_site,
  at_site.sfha                  AS site_in_sfha,
  nearest.sfha_distance_mi      AS nearest_sfha_mi,
  l.zone_count > 0              AS fema_mapped
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

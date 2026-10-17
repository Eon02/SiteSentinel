-- Core tables: job sites, conditions, hazard scores, alerts and crew hazard reports.

-- Job sites from EIA-860M (50+ rows)
CREATE TABLE sites (
  id           text PRIMARY KEY,        -- EIA plant code as text
  name         text NOT NULL,
  utility      text,
  plant_type   text,                    -- e.g. 'Solar', 'Natural Gas'
  county       text,
  state        text,
  timezone     text NOT NULL,           -- IANA, e.g. 'America/New_York'
  near_river   boolean DEFAULT false,
  geom         geometry(Point, 4326) NOT NULL
);

-- Latest weather per site, written by the worker
CREATE TABLE conditions (
  site_id        text REFERENCES sites(id),
  observed_at    timestamptz NOT NULL,
  temp_f         real,
  heat_index_f   real,
  precip_24h_in  real,
  soil_moisture  real,                  -- 0 to 1
  wind_mph       real,
  PRIMARY KEY (site_id, observed_at)
);

-- ML hazard index, today and tomorrow (forecast)
CREATE TABLE hazard_scores (
  site_id      text REFERENCES sites(id),
  score_date   date NOT NULL,
  score        real NOT NULL,           -- 0 to 10
  is_forecast  boolean NOT NULL,
  top_factors  jsonb NOT NULL,          -- [{"factor":"heat","weight":0.4}, ...]
  PRIMARY KEY (site_id, score_date)
);

-- Active and past alerts, written by the worker and the demo endpoint
CREATE TABLE alerts (
  id              bigserial PRIMARY KEY,
  site_id         text REFERENCES sites(id),
  type            text NOT NULL,        -- 'lightning' | 'flood' | 'heat' | 'wildfire' | 'storm'
  severity        text NOT NULL,        -- 'warning' | 'stop_work'
  message         text NOT NULL,
  started_at      timestamptz NOT NULL DEFAULT now(),
  last_strike_at  timestamptz,          -- lightning only
  cleared_at      timestamptz           -- null = active
);

-- Crew hazard reports, written by the frontend
CREATE TABLE hazard_reports (
  id           bigserial PRIMARY KEY,
  site_id      text REFERENCES sites(id),
  hazard_type  text NOT NULL,           -- see list in section 6
  description  text,
  photo_url    text,
  reported_by  text,
  geom         geometry(Point, 4326) NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now()
);

-- Spatial indexes for radius queries (e.g. lightning within 10 miles of a site).
CREATE INDEX sites_geom_idx ON sites USING gist (geom);
CREATE INDEX hazard_reports_geom_idx ON hazard_reports USING gist (geom);

-- Lookups by site. The composite primary keys already cover conditions and hazard_scores.
CREATE INDEX alerts_active_site_idx ON alerts (site_id) WHERE cleared_at IS NULL;
CREATE INDEX hazard_reports_site_created_idx ON hazard_reports (site_id, created_at DESC);

-- Supabase exposes the public schema through its REST API using the public anon key.
-- Enabling RLS with no policies blocks that path; the backend connects as the database
-- owner, which bypasses RLS. Add policies here if the frontend should query Supabase directly.
ALTER TABLE sites ENABLE ROW LEVEL SECURITY;
ALTER TABLE conditions ENABLE ROW LEVEL SECURITY;
ALTER TABLE hazard_scores ENABLE ROW LEVEL SECURITY;
ALTER TABLE alerts ENABLE ROW LEVEL SECURITY;
ALTER TABLE hazard_reports ENABLE ROW LEVEL SECURITY;
ALTER TABLE schema_migrations ENABLE ROW LEVEL SECURITY;

-- Workers attached to each site (project). Flood warnings for an at-risk site are emailed
-- to every worker listed here.

CREATE TABLE site_workers (
  id          bigserial PRIMARY KEY,
  site_id     text NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
  email       text NOT NULL,
  name        text,
  role        text,                     -- e.g. 'supervisor', 'crew lead', 'operator'
  created_at  timestamptz NOT NULL DEFAULT now()
);

-- One row per address per site, ignoring case.
CREATE UNIQUE INDEX site_workers_site_email ON site_workers (site_id, lower(email));

ALTER TABLE site_workers ENABLE ROW LEVEL SECURITY;

-- At most one active flood alert per site. Lets the automated flood check tell a newly
-- raised warning (email the workers) from one that is still in effect (don't re-send),
-- even with several API instances running.
CREATE UNIQUE INDEX alerts_one_active_flood ON alerts (site_id)
  WHERE type = 'flood' AND cleared_at IS NULL;

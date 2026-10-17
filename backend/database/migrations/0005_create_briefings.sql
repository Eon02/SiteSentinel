-- Next-day safety briefings: one row per site per send attempt series.
-- Scheduled briefings go out at 6 PM site-local time; manual ones come from POST /briefings/run.

CREATE TABLE briefings (
  id             bigserial PRIMARY KEY,
  site_id        text NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
  briefing_date  date NOT NULL,                 -- the day the briefing covers (site-local)
  trigger        text NOT NULL,                 -- 'scheduled' | 'manual'
  status         text NOT NULL,                 -- 'sending' | 'sent' | 'failed' | 'skipped'
  attempts       integer NOT NULL DEFAULT 1,
  last_error     text,
  payload        jsonb,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  sent_at        timestamptz
);

-- At most one scheduled briefing per site per day. This is also the lock that stops two
-- running API processes from sending the same briefing twice.
CREATE UNIQUE INDEX briefings_scheduled_once ON briefings (site_id, briefing_date)
  WHERE trigger = 'scheduled';

CREATE INDEX briefings_site_date_idx ON briefings (site_id, briefing_date DESC);

ALTER TABLE briefings ENABLE ROW LEVEL SECURITY;

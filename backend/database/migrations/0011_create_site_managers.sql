-- Project managers responsible for each site. POST /reports/email emails each manager one
-- Gemini-written report covering every site they manage.

CREATE TABLE site_managers (
  id          bigserial PRIMARY KEY,
  site_id     text NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
  email       text NOT NULL,           -- stored lowercase
  name        text,
  created_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (site_id, email)
);

CREATE INDEX site_managers_email ON site_managers (email);

ALTER TABLE site_managers ENABLE ROW LEVEL SECURITY;

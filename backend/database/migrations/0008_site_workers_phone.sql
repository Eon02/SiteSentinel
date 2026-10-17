-- Workers can be reached by text message. Each worker needs an email, a phone, or both;
-- phones are stored in E.164 form (e.g. +12055550123).
ALTER TABLE site_workers ADD COLUMN phone text;
ALTER TABLE site_workers ALTER COLUMN email DROP NOT NULL;
ALTER TABLE site_workers ADD CONSTRAINT site_workers_has_contact
  CHECK (email IS NOT NULL OR phone IS NOT NULL);

-- One row per phone per site (the email index already covers emails).
CREATE UNIQUE INDEX site_workers_site_phone ON site_workers (site_id, phone)
  WHERE phone IS NOT NULL;

-- Crew reports made without signal are queued on the phone and retried until they upload. Each
-- carries a device-generated UUID so a retry after a dropped connection returns the report that
-- already arrived instead of creating a duplicate. NULL for reports from older clients.
ALTER TABLE hazard_reports ADD COLUMN client_id uuid UNIQUE;

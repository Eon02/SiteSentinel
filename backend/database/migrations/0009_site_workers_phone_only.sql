-- Email warnings were removed; workers are reached by SMS only, so every worker needs a phone.
DELETE FROM site_workers WHERE phone IS NULL;
ALTER TABLE site_workers DROP CONSTRAINT site_workers_has_contact;
DROP INDEX site_workers_site_email;
ALTER TABLE site_workers DROP COLUMN email;
ALTER TABLE site_workers ALTER COLUMN phone SET NOT NULL;

-- Enable spatial support. Supabase installs extensions into the "extensions"
-- schema, which is on the default search_path, so ST_* functions work unqualified.
CREATE SCHEMA IF NOT EXISTS extensions;
CREATE EXTENSION IF NOT EXISTS postgis WITH SCHEMA extensions;

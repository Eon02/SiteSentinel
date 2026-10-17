import postgres from 'postgres';

// Reuse one connection pool across hot reloads in dev.
const globalForDb = globalThis as unknown as { sql?: postgres.Sql };

export function getSql(): postgres.Sql {
  if (!globalForDb.sql) {
    const url = process.env.DATABASE_URL;
    if (!url) throw new Error('DATABASE_URL is not set (or set USE_MOCK=true)');
    globalForDb.sql = postgres(url, { max: 5, idle_timeout: 20 });
  }
  return globalForDb.sql;
}

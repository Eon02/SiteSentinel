import { getSites } from '@/lib/data';

// Cache Components is on: no `dynamic`/`runtime` exports. lib/data.ts calls connection(),
// which keeps every response request-time and uncached.
export async function GET() {
  return Response.json(await getSites());
}

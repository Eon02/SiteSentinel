import { z } from 'zod';
import { recordStrike, siteExists } from '@/lib/data';

const StrikeInput = z.object({ siteId: z.string().min(1) });

export async function POST(req: Request) {
  const parsed = StrikeInput.safeParse(await req.json().catch(() => null));
  if (!parsed.success) return Response.json({ error: 'siteId is required' }, { status: 400 });
  if (!(await siteExists(parsed.data.siteId))) {
    return Response.json({ error: 'Site not found' }, { status: 404 });
  }
  return Response.json(await recordStrike(parsed.data.siteId));
}

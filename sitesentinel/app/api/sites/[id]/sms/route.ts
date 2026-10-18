import { z } from 'zod';
import { getSite, usingMockData } from '@/lib/data';

export type CrewSmsDelivery = { to: string; name: string | null; status: 'sent' | 'failed' | 'skipped'; error: string | null };

// No message = the backend's default status text (the site page's "Force SMS Update" button).
const SmsInput = z.object({ message: z.string().trim().min(1).max(280, 'Keep it to 280 characters').optional() });

// Where the FastAPI backend runs; it owns Twilio and the crew list (site_workers).
const BACKEND_URL = process.env.BACKEND_URL || 'http://localhost:8000';

// Text every crew member assigned to the site. Sending is done by the backend
// (POST /sites/{id}/sms) so Twilio credentials never reach the browser. Mock mode sends nothing.
export async function POST(req: Request, ctx: RouteContext<'/api/sites/[id]/sms'>) {
  const { id } = await ctx.params;
  const parsed = SmsInput.safeParse((await req.json().catch(() => null)) ?? {});
  if (!parsed.success) {
    return Response.json({ error: parsed.error.issues[0]?.message ?? 'Invalid message' }, { status: 400 });
  }

  if (usingMockData()) {
    const detail = await getSite(id);
    if (!detail) return Response.json({ error: 'Site not found' }, { status: 404 });
    const results: CrewSmsDelivery[] = detail.crew.map((c) => ({
      to: c.phone,
      name: c.name ?? null,
      status: 'skipped',
      error: 'Mock mode: no texts sent',
    }));
    return Response.json(results);
  }

  let res: Response;
  try {
    res = await fetch(`${BACKEND_URL}/sites/${encodeURIComponent(id)}/sms`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(parsed.data),
      cache: 'no-store',
    });
  } catch {
    return Response.json({ error: 'The SiteSentinel backend isn’t reachable, so no texts were sent.' }, { status: 502 });
  }
  const json = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = typeof json.detail === 'string' ? json.detail : json.detail?.[0]?.msg;
    return Response.json({ error: detail ?? `Backend error (HTTP ${res.status})` }, { status: res.status === 404 ? 404 : 502 });
  }
  return Response.json(json as CrewSmsDelivery[]);
}

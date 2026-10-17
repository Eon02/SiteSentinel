import { clearAllAlerts } from '@/lib/data';

export async function POST() {
  await clearAllAlerts();
  return Response.json({ ok: true });
}

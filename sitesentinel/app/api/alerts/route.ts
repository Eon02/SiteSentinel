import { getActiveAlerts } from '@/lib/data';

export async function GET() {
  return Response.json(await getActiveAlerts());
}

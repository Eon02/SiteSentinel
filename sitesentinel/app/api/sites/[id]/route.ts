import { getSite } from '@/lib/data';

export async function GET(_req: Request, ctx: RouteContext<'/api/sites/[id]'>) {
  const { id } = await ctx.params;
  const detail = await getSite(id);
  if (!detail) return Response.json({ error: 'Site not found' }, { status: 404 });
  return Response.json(detail);
}

import { clearAlert } from '@/lib/data';

export async function POST(_req: Request, ctx: RouteContext<'/api/alerts/[id]/clear'>) {
  const id = Number((await ctx.params).id);
  if (!Number.isInteger(id)) return Response.json({ error: 'Invalid alert id' }, { status: 400 });
  await clearAlert(id);
  return Response.json({ ok: true });
}

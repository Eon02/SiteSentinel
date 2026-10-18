import { removeCrewMember } from '@/lib/data';

// Remove a crew member from a site.
export async function DELETE(_req: Request, ctx: RouteContext<'/api/sites/[id]/crew/[crewId]'>) {
  const { id, crewId } = await ctx.params;
  const crewNumber = Number(crewId);
  if (!Number.isInteger(crewNumber)) return Response.json({ error: 'Invalid crew id' }, { status: 400 });
  if (!(await removeCrewMember(id, crewNumber))) {
    return Response.json({ error: 'Crew member not found on this site' }, { status: 404 });
  }
  return Response.json({ ok: true });
}

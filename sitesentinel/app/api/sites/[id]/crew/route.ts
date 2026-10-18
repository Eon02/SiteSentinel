import { z } from 'zod';
import { addCrewMember, siteExists } from '@/lib/data';
import { normalizePhone } from '@/lib/phone';

const optionalText = (max: number) =>
  z.string().trim().max(max).optional().transform((v) => (v ? v : undefined));

const CrewInput = z.object({
  name: optionalText(200),
  role: optionalText(100),
  phone: z
    .string()
    .transform((v, ctx) => {
      const phone = normalizePhone(v);
      if (!phone) ctx.addIssue({ code: 'custom', message: 'Enter a valid phone number, e.g. (205) 555-0123' });
      return phone ?? z.NEVER;
    }),
});

// Assign a crew member to a site. 201 when added; 200 with the existing member when that phone
// is already on the site (so a retried offline add never creates a duplicate).
export async function POST(req: Request, ctx: RouteContext<'/api/sites/[id]/crew'>) {
  const { id } = await ctx.params;
  const parsed = CrewInput.safeParse(await req.json().catch(() => null));
  if (!parsed.success) {
    return Response.json({ error: parsed.error.issues[0]?.message ?? 'Invalid crew member' }, { status: 400 });
  }
  if (!(await siteExists(id))) return Response.json({ error: 'Site not found' }, { status: 404 });
  const { member, created } = await addCrewMember(id, parsed.data);
  return Response.json(member, { status: created ? 201 : 200 });
}

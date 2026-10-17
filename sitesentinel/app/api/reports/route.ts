import { put } from '@vercel/blob';
import { z } from 'zod';
import { createReport, getRecentReports, siteExists } from '@/lib/data';
import { HAZARD_TYPES } from '@/lib/types';

const MAX_PHOTO_BYTES = 5 * 1024 * 1024;

const optionalText = (max: number) =>
  z.string().trim().max(max).optional().transform((v) => (v ? v : undefined));

const ReportInput = z.object({
  siteId: z.string().min(1),
  hazardType: z.enum(HAZARD_TYPES.map((h) => h.value) as [string, ...string[]]),
  description: optionalText(1000),
  reportedBy: optionalText(100),
  lat: z.coerce.number().min(-90).max(90),
  lon: z.coerce.number().min(-180).max(180),
});

const PhotoInput = z
  .instanceof(File)
  .refine((f) => f.size <= MAX_PHOTO_BYTES, 'Photo must be 5 MB or smaller')
  .refine((f) => f.type.startsWith('image/'), 'Photo must be an image');

// Recent reports across all sites, used for the dashboard map pins.
export async function GET() {
  return Response.json(await getRecentReports());
}

export async function POST(req: Request) {
  const form = await req.formData().catch(() => null);
  if (!form) return Response.json({ error: 'Expected multipart/form-data' }, { status: 400 });

  const fields = Object.fromEntries(
    ['siteId', 'hazardType', 'description', 'reportedBy', 'lat', 'lon'].map((k) => [k, form.get(k) ?? undefined]),
  );
  const parsed = ReportInput.safeParse(fields);
  if (!parsed.success) {
    return Response.json({ error: 'Invalid report', issues: parsed.error.issues }, { status: 400 });
  }
  if (!(await siteExists(parsed.data.siteId))) {
    return Response.json({ error: 'Site not found' }, { status: 404 });
  }

  let photoUrl: string | undefined;
  const photo = form.get('photo');
  if (photo instanceof File && photo.size > 0) {
    const checked = PhotoInput.safeParse(photo);
    if (!checked.success) {
      return Response.json({ error: checked.error.issues[0].message }, { status: 400 });
    }
    photoUrl = await storePhoto(photo, parsed.data.siteId);
  }

  const report = await createReport({ ...parsed.data, photoUrl });
  return Response.json(report, { status: 201 });
}

async function storePhoto(photo: File, siteId: string): Promise<string> {
  if (process.env.BLOB_READ_WRITE_TOKEN) {
    const ext = photo.type.split('/')[1]?.replace('jpeg', 'jpg') ?? 'jpg';
    const blob = await put(`reports/${siteId}/${Date.now()}.${ext}`, photo, {
      access: 'public',
      addRandomSuffix: true,
      contentType: photo.type,
    });
    return blob.url;
  }
  // No Blob token (local mock mode): keep the photo inline as a data URL.
  const base64 = Buffer.from(await photo.arrayBuffer()).toString('base64');
  return `data:${photo.type};base64,${base64}`;
}

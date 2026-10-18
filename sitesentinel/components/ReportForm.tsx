'use client';

import dynamic from 'next/dynamic';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';
import { ArrowLeft, Camera, CircleCheck, CloudUpload, LoaderCircle, X } from 'lucide-react';
import HazardIcon from './HazardIcon';
import { Skeleton } from './Skeleton';
import { usePolling } from '@/lib/usePolling';
import { submitReport } from '@/lib/reportQueue';
import { nearestSite } from '@/lib/geo';
import { hasInAppHistory } from '@/lib/navHistory';
import { HAZARD_TYPES, hazardLabel, type Site } from '@/lib/types';

const LocationPickerMap = dynamic(() => import('./LocationPickerMap'), {
  ssr: false,
  loading: () => <Skeleton className="h-full w-full" />,
});

const MAX_PHOTO_WIDTH = 1600;
const MAX_PHOTO_BYTES = 5 * 1024 * 1024;

export default function ReportForm() {
  const searchParams = useSearchParams();
  const presetSiteId = searchParams.get('site');
  const { data: sites } = usePolling<Site[]>('/api/sites', 60_000);

  const [chosenSiteId, setSiteId] = useState(presetSiteId ?? '');
  const [hazardType, setHazardType] = useState('');
  const [description, setDescription] = useState('');
  const [reportedBy, setReportedBy] = useState('');
  const [photo, setPhoto] = useState<Blob | null>(null);
  const [photoPreview, setPhotoPreview] = useState<string | null>(null);
  const [photoBusy, setPhotoBusy] = useState(false);
  const [gps, setGps] = useState<{ lat: number; lon: number } | null>(null);
  const [gpsStatus, setGpsStatus] = useState<'pending' | 'ok' | 'denied'>('pending');
  const [manualPin, setManualPin] = useState<{ lat: number; lon: number } | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // 'sent' = on the server; 'queued' = saved on this device, uploads when there's signal.
  const [submitted, setSubmitted] = useState<{ siteId: string; hazardType: string; status: 'sent' | 'queued' } | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  // Ask for the phone's location once.
  useEffect(() => {
    if (!('geolocation' in navigator)) {
      queueMicrotask(() => setGpsStatus('denied'));
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setGps({ lat: pos.coords.latitude, lon: pos.coords.longitude });
        setGpsStatus('ok');
      },
      () => setGpsStatus('denied'),
      { enableHighAccuracy: true, timeout: 15_000, maximumAge: 60_000 },
    );
  }, []);

  // No ?site= preset or user choice: default to the nearest site once we have a GPS fix.
  const siteId = chosenSiteId || (gps && sites?.length ? nearestSite(sites, gps).id : '');
  const site = sites?.find((s) => s.id === siteId);

  // Pin: a manual drag wins, then GPS, then (location denied) the site's coordinates.
  const pin = manualPin ?? gps ?? (gpsStatus === 'denied' && site ? { lat: site.lat, lon: site.lon } : null);

  useEffect(() => () => {
    if (photoPreview) URL.revokeObjectURL(photoPreview);
  }, [photoPreview]);

  async function onPhotoChosen(file: File | undefined) {
    if (!file) return;
    if (!file.type.startsWith('image/')) {
      setError('Please choose an image file.');
      return;
    }
    setError(null);
    setPhotoBusy(true);
    try {
      const resized = await resizeImage(file, MAX_PHOTO_WIDTH);
      if (resized.size > MAX_PHOTO_BYTES) throw new Error('Photo is larger than 5 MB even after resizing.');
      setPhoto(resized);
      setPhotoPreview(URL.createObjectURL(resized));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not read that photo.');
    } finally {
      setPhotoBusy(false);
    }
  }

  function clearPhoto() {
    setPhoto(null);
    setPhotoPreview(null);
    if (fileInput.current) fileInput.current.value = '';
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!siteId) return setError('Choose a site.');
    if (!hazardType) return setError('Choose a hazard type.');
    if (!pin) return setError('Waiting for a location. Tap the map to place the pin.');

    setError(null);
    setSubmitting(true);
    try {
      // Saved on the device first, so no signal never loses a report; see lib/reportQueue.ts.
      const result = await submitReport({
        siteId,
        hazardType,
        description,
        reportedBy,
        lat: pin.lat,
        lon: pin.lon,
        photo,
      });
      if (result.status === 'rejected') setError(result.error);
      else setSubmitted({ siteId, hazardType, status: result.status });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save the report. Try again.');
    } finally {
      setSubmitting(false);
    }
  }

  function reportAnother() {
    setSubmitted(null);
    setHazardType('');
    setDescription('');
    clearPhoto();
    setManualPin(null);
  }

  if (submitted) {
    return (
      <div className="mx-auto flex w-full max-w-xl flex-col items-center gap-4 p-6 text-center">
        <CloseReportButton label="Back" className="self-start" />
        {submitted.status === 'sent' ? (
          <CircleCheck size={72} className="text-[#2F9E44]" aria-hidden />
        ) : (
          <CloudUpload size={72} className="text-[#E8590C]" aria-hidden />
        )}
        <h1 className="text-2xl font-extrabold">{submitted.status === 'sent' ? 'Report sent' : 'Report saved'}</h1>
        <p className="text-lg">
          {hazardLabel(submitted.hazardType)} at {sites?.find((s) => s.id === submitted.siteId)?.name ?? submitted.siteId}.{' '}
          {submitted.status === 'sent'
            ? 'Supervisors can see it on the map now.'
            : 'No signal right now, so it’s saved on this device and will upload automatically when you’re back in signal. You can keep using the app.'}
        </p>
        <div className="flex w-full flex-col gap-3 sm:flex-row">
          <button type="button" onClick={reportAnother} className="min-h-12 flex-1 rounded-xl bg-[#E8590C] px-4 text-lg font-bold text-white">
            Report another
          </button>
          <Link
            href={`/site/${encodeURIComponent(submitted.siteId)}`}
            className="flex min-h-12 flex-1 items-center justify-center rounded-xl bg-[#16213E] px-4 text-lg font-bold text-white"
          >
            View site
          </Link>
        </div>
      </div>
    );
  }

  return (
    <form onSubmit={onSubmit} className="mx-auto flex w-full max-w-xl flex-col gap-5 p-4" noValidate>
      <div className="flex flex-col items-start gap-1">
        <CloseReportButton label="Cancel" confirmDiscard={Boolean(hazardType || description.trim() || photo)} />
        <h1 className="text-2xl font-extrabold">Report a hazard</h1>
      </div>

      <fieldset className="flex flex-col gap-2">
        <label htmlFor="site" className="text-lg font-bold">1. Site</label>
        {sites ? (
          <select
            id="site"
            value={siteId}
            onChange={(e) => setSiteId(e.target.value)}
            className="min-h-12 w-full rounded-lg border-2 border-slate-500 bg-white px-3 text-lg"
          >
            <option value="">{gpsStatus === 'pending' ? 'Finding nearest site…' : 'Choose a site'}</option>
            {sites.map((s) => (
              <option key={s.id} value={s.id}>{s.name}{s.state ? ` (${s.state})` : ''}</option>
            ))}
          </select>
        ) : (
          <Skeleton className="h-12" />
        )}
      </fieldset>

      <fieldset>
        <legend className="mb-2 text-lg font-bold">2. What is the hazard?</legend>
        <div className="grid grid-cols-2 gap-2">
          {HAZARD_TYPES.map((h) => {
            const active = hazardType === h.value;
            return (
              <button
                key={h.value}
                type="button"
                aria-pressed={active}
                onClick={() => setHazardType(h.value)}
                className={`flex min-h-16 items-center gap-2 rounded-xl border-2 px-3 text-left text-base font-bold ${
                  active ? 'border-[#E8590C] bg-[#E8590C] text-white' : 'border-slate-400 bg-white text-slate-900'
                } ${h.value === 'other' ? 'col-span-2' : ''}`}
              >
                <HazardIcon type={h.value} size={24} />
                {h.label}
              </button>
            );
          })}
        </div>
      </fieldset>

      <fieldset className="flex flex-col gap-2">
        <legend className="mb-2 text-lg font-bold">3. Photo</legend>
        <input
          ref={fileInput}
          id="photo"
          type="file"
          accept="image/*"
          capture="environment"
          className="sr-only"
          onChange={(e) => onPhotoChosen(e.target.files?.[0])}
        />
        {photoPreview ? (
          <div className="relative">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={photoPreview} alt="Selected hazard photo" className="max-h-72 w-full rounded-xl object-contain bg-slate-200" />
            <button
              type="button"
              onClick={clearPhoto}
              aria-label="Remove photo"
              className="absolute right-2 top-2 flex h-11 w-11 items-center justify-center rounded-full bg-white text-slate-900 shadow"
            >
              <X size={22} aria-hidden />
            </button>
          </div>
        ) : (
          <label
            htmlFor="photo"
            className="flex min-h-16 cursor-pointer items-center justify-center gap-2 rounded-xl border-2 border-dashed border-slate-500 bg-white text-lg font-bold text-slate-900"
          >
            {photoBusy ? <LoaderCircle className="animate-spin" aria-hidden /> : <Camera aria-hidden />}
            {photoBusy ? 'Preparing photo…' : 'Take or choose a photo'}
          </label>
        )}
      </fieldset>

      <fieldset className="flex flex-col gap-2">
        <legend className="mb-2 text-lg font-bold">4. Location</legend>
        <p className="text-base text-slate-900">
          {gpsStatus === 'pending' && 'Getting your location…'}
          {gpsStatus === 'ok' && 'Using your phone’s location. Drag the pin if the hazard is somewhere else.'}
          {gpsStatus === 'denied' && 'Location is off, so the pin starts at the site. Drag it to the hazard.'}
        </p>
        <div className="h-60 overflow-hidden rounded-xl">
          {pin ? (
            <LocationPickerMap lat={pin.lat} lon={pin.lon} onChange={(lat, lon) => setManualPin({ lat, lon })} />
          ) : (
            <Skeleton className="h-full w-full" />
          )}
        </div>
      </fieldset>

      <fieldset className="flex flex-col gap-3">
        <legend className="mb-2 text-lg font-bold">5. Details (optional)</legend>
        <label className="flex flex-col gap-1 font-semibold">
          Description
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            maxLength={1000}
            rows={3}
            className="rounded-lg border-2 border-slate-500 bg-white p-3 text-base font-normal"
            placeholder="What, where, how big?"
          />
        </label>
        <label className="flex flex-col gap-1 font-semibold">
          Your name
          <input
            value={reportedBy}
            onChange={(e) => setReportedBy(e.target.value)}
            maxLength={100}
            autoComplete="name"
            className="min-h-12 rounded-lg border-2 border-slate-500 bg-white px-3 text-base font-normal"
          />
        </label>
      </fieldset>

      {error && (
        <p role="alert" className="rounded-lg bg-[#C92A2A] p-3 font-bold text-white">{error}</p>
      )}

      <button
        type="submit"
        disabled={submitting || photoBusy}
        className="flex min-h-14 items-center justify-center gap-2 rounded-xl bg-[#E8590C] text-xl font-extrabold text-white disabled:opacity-60"
      >
        {submitting && <LoaderCircle className="animate-spin" aria-hidden />}
        {submitting ? 'Saving…' : 'Send report'}
      </button>
    </form>
  );
}

// Back arrow that leaves the report page (cancels the report): back to the app page the crew
// member came from (e.g. the crew view), or home if they opened it directly (never back out of
// the app). Asks first if a half-filled report would be lost.
function CloseReportButton({
  label,
  confirmDiscard = false,
  className = '',
}: {
  label: string;
  confirmDiscard?: boolean;
  className?: string;
}) {
  const router = useRouter();
  function close() {
    if (confirmDiscard && !window.confirm('Discard this report?')) return;
    if (hasInAppHistory()) router.back();
    else router.push('/');
  }
  return (
    <button
      type="button"
      onClick={close}
      className={`-ml-2 flex min-h-11 items-center gap-1 rounded-lg px-2 text-lg font-bold text-[#16213E] hover:bg-slate-200 ${className}`}
    >
      <ArrowLeft size={24} aria-hidden />
      {label}
    </button>
  );
}

// Downscale to maxWidth and re-encode as JPEG so uploads are fast on a weak signal.
async function resizeImage(file: File, maxWidth: number): Promise<Blob> {
  const bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' });
  const scale = Math.min(1, maxWidth / bitmap.width);
  const width = Math.round(bitmap.width * scale);
  const height = Math.round(bitmap.height * scale);
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('Could not process the photo on this device.');
  ctx.drawImage(bitmap, 0, 0, width, height);
  bitmap.close();
  return new Promise((resolve, reject) =>
    canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error('Could not process the photo.'))), 'image/jpeg', 0.85),
  );
}

'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import {
  Cloud,
  CloudDrizzle,
  CloudFog,
  CloudLightning,
  CloudRain,
  CloudSnow,
  CloudSun,
  Droplets,
  MapPin,
  Sun,
  TriangleAlert,
  type LucideIcon,
} from 'lucide-react';
import { useAlerts } from './AlertsProvider';
import LightningCountdown from './LightningCountdown';
import { OfflineNote, Skeleton } from './Skeleton';
import { nearestSite } from '@/lib/geo';
import { usePolling } from '@/lib/usePolling';
import type { Site } from '@/lib/types';
import type { Forecast, WeatherKind } from '@/lib/weather';

const SITE_KEY = 'sitesentinel:crew-site';

const ICONS: Record<WeatherKind, LucideIcon> = {
  clear: Sun,
  partly: CloudSun,
  cloudy: Cloud,
  fog: CloudFog,
  drizzle: CloudDrizzle,
  rain: CloudRain,
  snow: CloudSnow,
  storm: CloudLightning,
};

// Phone-first view for crew members: the weather coming up at their site, a big Report a
// hazard button, and the site's active alerts. The site is the one they picked before, else the
// nearest to the phone, else the first.
export default function CrewView({ onSupervisorView }: { onSupervisorView: () => void }) {
  const sites = usePolling<Site[]>('/api/sites', 60_000);
  const [chosen, setChosen] = useState<string | null>(() => readSaved());
  const [gps, setGps] = useState<{ lat: number; lon: number } | null>(null);

  useEffect(() => {
    if (chosen || !('geolocation' in navigator)) return;
    navigator.geolocation.getCurrentPosition(
      (pos) => setGps({ lat: pos.coords.latitude, lon: pos.coords.longitude }),
      () => undefined,
      { timeout: 15_000, maximumAge: 300_000 },
    );
  }, [chosen]);

  const list = sites.data ?? [];
  const siteId =
    (chosen && list.some((s) => s.id === chosen) ? chosen : null) ??
    (gps && list.length ? nearestSite(list, gps).id : list[0]?.id ?? null);
  const site = list.find((s) => s.id === siteId);

  function choose(id: string) {
    setChosen(id);
    try {
      localStorage.setItem(SITE_KEY, id);
    } catch {
      // storage blocked: the choice lasts until reload
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-xl flex-col gap-4 p-4">
      <div className="flex items-center justify-between gap-2">
        <h1 className="text-xl font-extrabold">Crew view</h1>
        <button type="button" onClick={onSupervisorView} className="min-h-11 px-2 text-sm font-semibold text-[#16213E] underline">
          Supervisor view
        </button>
      </div>

      <label className="flex flex-col gap-1">
        <span className="flex items-center gap-1 text-sm font-bold text-slate-800">
          <MapPin size={16} aria-hidden /> Your site
        </span>
        {sites.data ? (
          <select
            value={siteId ?? ''}
            onChange={(e) => choose(e.target.value)}
            className="min-h-12 w-full rounded-lg border-2 border-slate-500 bg-white px-3 text-lg font-semibold"
          >
            {list.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
                {s.state ? ` (${s.state})` : ''}
              </option>
            ))}
          </select>
        ) : (
          <Skeleton className="h-12" />
        )}
      </label>

      <OfflineNote show={sites.offline} />

      {siteId ? <WeatherCard siteId={siteId} /> : <Skeleton className="h-48" />}

      <Link
        href={siteId ? `/report?site=${encodeURIComponent(siteId)}` : '/report'}
        className="flex min-h-16 items-center justify-center gap-2 rounded-xl bg-[#E8590C] px-4 text-xl font-extrabold text-white hover:bg-[#C2410C]"
      >
        <TriangleAlert size={26} aria-hidden />
        Report a hazard
      </Link>

      <SiteAlerts siteId={siteId} siteName={site?.name} />
    </div>
  );
}

function WeatherCard({ siteId }: { siteId: string }) {
  const { data, loading, offline } = usePolling<Forecast>(`/api/sites/${encodeURIComponent(siteId)}/forecast`, 600_000);
  if (loading && !data) return <Skeleton className="h-48" />;
  if (!data) {
    return <p className="rounded-xl bg-white p-4 text-slate-800">Weather isn&apos;t available right now.</p>;
  }
  const Icon = ICONS[data.kind];
  const deg = (v: number | null) => (v === null ? '–' : `${Math.round(v)}°`);
  return (
    <section aria-label="Weather" className="flex flex-col items-center gap-2 rounded-xl bg-white p-5 text-center">
      <Icon size={96} strokeWidth={1.5} className="text-[#16213E]" aria-hidden />
      <div className="text-5xl font-extrabold text-slate-900">{deg(data.currentTempF)}F</div>
      <div className="text-xl font-bold text-slate-900">{data.label}</div>
      <div className="text-base font-semibold text-slate-800">Next {data.hours} hours</div>
      <div className="mt-1 grid w-full grid-cols-2 gap-2">
        <div className="rounded-lg bg-slate-100 p-2">
          <div className="flex items-center justify-center gap-1 text-2xl font-extrabold text-[#16213E]">
            <Droplets size={22} aria-hidden />
            {data.precipChancePct === null ? '–' : `${data.precipChancePct}%`}
          </div>
          <div className="text-sm font-semibold text-slate-800">Chance of rain</div>
        </div>
        <div className="rounded-lg bg-slate-100 p-2">
          <div className="text-2xl font-extrabold text-[#16213E]">
            {deg(data.highF)} / {deg(data.lowF)}
          </div>
          <div className="text-sm font-semibold text-slate-800">High / low</div>
        </div>
      </div>
      <OfflineNote show={offline} />
    </section>
  );
}

function SiteAlerts({ siteId, siteName }: { siteId: string | null; siteName?: string }) {
  const { data: alerts } = useAlerts();
  const mine = (alerts ?? []).filter((a) => a.siteId === siteId);
  return (
    <section aria-label="Active alerts" className="flex flex-col gap-2">
      <h2 className="text-lg font-bold">Active alerts{siteName ? ` · ${siteName}` : ''}</h2>
      {!alerts ? (
        <Skeleton className="h-16" />
      ) : mine.length === 0 ? (
        <p className="rounded-xl bg-white p-4 font-semibold text-slate-800">No active alerts.</p>
      ) : (
        mine.map((a) => (
          <div
            key={a.id}
            className={`flex flex-col gap-1 rounded-xl p-4 ${
              a.severity === 'stop_work' ? 'bg-[#C92A2A] text-white' : 'bg-[#F2C200] text-[#1A1A1A]'
            }`}
          >
            <div className="text-lg font-extrabold">{a.severity === 'stop_work' ? 'STOP WORK' : 'Warning'} · {a.type}</div>
            <div className="font-semibold">{a.message}</div>
            {a.type === 'lightning' && <LightningCountdown alert={a} />}
          </div>
        ))
      )}
    </section>
  );
}

function readSaved(): string | null {
  if (typeof window === 'undefined') return null;
  try {
    return localStorage.getItem(SITE_KEY);
  } catch {
    return null;
  }
}

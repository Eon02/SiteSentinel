'use client';

import dynamic from 'next/dynamic';
import Link from 'next/link';
import { useEffect, useState } from 'react';
import ConditionsSummary from './ConditionsSummary';
import CrewSection from './CrewSection';
import FactorBars from './FactorBars';
import HazardIcon from './HazardIcon';
import LightningCountdown from './LightningCountdown';
import RiskBadge from './RiskBadge';
import { OfflineNote, Skeleton } from './Skeleton';
import { usePolling } from '@/lib/usePolling';
import { hazardLabel, type SiteDetail as SiteDetailData } from '@/lib/types';

const SiteMap = dynamic(() => import('./SiteMap'), {
  ssr: false,
  loading: () => <Skeleton className="h-full w-full" />,
});

export default function SiteDetail({ id }: { id: string }) {
  const { data, loading, offline, notFound } = usePolling<SiteDetailData>(`/api/sites/${encodeURIComponent(id)}`, 15_000);

  if (notFound) {
    return (
      <div className="mx-auto w-full max-w-5xl p-4">
        <h1 className="text-2xl font-bold">Site not found</h1>
        <p className="mt-2">
          No site with id <b>{id}</b>. <Link href="/" className="font-semibold text-[#16213E] underline">Back to dashboard</Link>
        </p>
      </div>
    );
  }
  if (loading && !data) return <SiteDetailSkeleton />;
  if (!data) {
    return (
      <div className="mx-auto w-full max-w-5xl p-4">
        <OfflineNote show />
        <p className="mt-2">Could not load this site yet.</p>
      </div>
    );
  }

  const { site, conditions, alerts, reports, crew } = data;
  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-4 p-4">
      <OfflineNote show={offline} />

      <section className="flex flex-col gap-4 rounded-xl bg-white p-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <h1 className="text-2xl font-extrabold text-slate-900 sm:text-3xl">{site.name}</h1>
          <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-base text-slate-900">
            <dt className="font-semibold">Utility</dt><dd>{site.utility ?? '—'}</dd>
            <dt className="font-semibold">Plant type</dt><dd>{site.plantType ?? '—'}</dd>
            <dt className="font-semibold">Location</dt>
            <dd>{[site.county && `${site.county} County`, site.state].filter(Boolean).join(', ') || '—'}{site.nearRiver ? ' · near river' : ''}</dd>
            <dt className="font-semibold">Local time</dt><dd><LocalTime timezone={site.timezone} /></dd>
          </dl>
        </div>
        <div className="flex gap-3">
          <RiskBadge score={site.score} size="lg" caption="Today" />
          {site.forecastScore !== undefined && <RiskBadge score={site.forecastScore} size="lg" caption="Tomorrow" />}
        </div>
      </section>

      <Link
        href={`/report?site=${encodeURIComponent(site.id)}`}
        className="flex min-h-12 items-center justify-center rounded-xl bg-[#E8590C] px-4 text-lg font-bold text-white hover:bg-[#C2410C]"
      >
        Report a hazard here
      </Link>

      <section className="rounded-xl bg-white p-4">
        <h2 className="mb-3 text-xl font-bold">Active alerts</h2>
        {alerts.length === 0 ? (
          <p className="text-slate-800">No active alerts.</p>
        ) : (
          <ul className="flex flex-col gap-3">
            {alerts.map((a) => (
              <li
                key={a.id}
                className={`flex flex-wrap items-center gap-3 rounded-lg p-3 ${
                  a.severity === 'stop_work' ? 'bg-[#C92A2A] text-white' : 'bg-[#F2C200] text-[#1A1A1A]'
                }`}
              >
                <div className="min-w-0 flex-1">
                  <div className="font-extrabold">{a.severity === 'stop_work' ? 'STOP WORK' : 'Warning'} · {a.type}</div>
                  <div className="font-semibold">{a.message}</div>
                  <div className="text-sm">Since {new Date(a.startedAt).toLocaleTimeString([], { timeZone: site.timezone, hour: 'numeric', minute: '2-digit' })} site time</div>
                </div>
                {a.type === 'lightning' && <LightningCountdown alert={a} />}
              </li>
            ))}
          </ul>
        )}
      </section>

      <CrewSection siteId={site.id} siteName={site.name} crew={crew ?? []} />

      <div className="grid gap-4 md:grid-cols-2">
        <section className="rounded-xl bg-white p-4">
          <h2 className="mb-3 text-xl font-bold">Why this score</h2>
          <FactorBars factors={site.topFactors} />
        </section>

        <section className="rounded-xl bg-white p-4">
          <h2 className="mb-3 text-xl font-bold">Current conditions</h2>
          <ConditionsSummary conditions={conditions} timezone={site.timezone} />
        </section>
      </div>

      <section className="rounded-xl bg-white p-4">
        <h2 className="mb-3 text-xl font-bold">Crew reports</h2>
        <div className="mb-4 h-64 overflow-hidden rounded-lg">
          <SiteMap
            className="h-full w-full"
            sites={[site]}
            reports={reports}
            initialView={{ longitude: site.lon, latitude: site.lat, zoom: 14 }}
          />
        </div>
        {reports.length === 0 ? (
          <p className="text-slate-800">No crew reports for this site.</p>
        ) : (
          <ul className="flex flex-col gap-3">
            {reports.map((r) => (
              <li key={r.id} className="flex gap-3 rounded-lg border-2 border-slate-200 p-3">
                {r.photoUrl ? (
                  <a href={r.photoUrl} target="_blank" rel="noreferrer" className="shrink-0">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={r.photoUrl} alt={`Photo: ${hazardLabel(r.hazardType)}`} className="h-20 w-20 rounded-md object-cover" />
                  </a>
                ) : (
                  <div className="flex h-20 w-20 shrink-0 items-center justify-center rounded-md bg-slate-100 text-[#16213E]">
                    <HazardIcon type={r.hazardType} size={32} />
                  </div>
                )}
                <div className="min-w-0">
                  <div className="font-bold">{hazardLabel(r.hazardType)}</div>
                  {r.description && <p className="text-slate-900">{r.description}</p>}
                  <div className="text-sm text-slate-800">
                    {r.reportedBy ? `${r.reportedBy} · ` : ''}
                    {new Date(r.createdAt).toLocaleString([], { timeZone: site.timezone, dateStyle: 'medium', timeStyle: 'short' })}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function LocalTime({ timezone }: { timezone: string }) {
  const [now, setNow] = useState<Date | null>(null);
  useEffect(() => {
    const tick = () => setNow(new Date());
    tick();
    const t = setInterval(tick, 30_000);
    return () => clearInterval(t);
  }, []);
  if (!now) return <span>…</span>;
  const time = now.toLocaleTimeString([], { timeZone: timezone, hour: 'numeric', minute: '2-digit', timeZoneName: 'short' });
  return <span>{time}</span>;
}

export function SiteDetailSkeleton() {
  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-4 p-4">
      <Skeleton className="h-40" />
      <Skeleton className="h-12" />
      <Skeleton className="h-28" />
      <Skeleton className="h-28" />
      <div className="grid gap-4 md:grid-cols-2">
        <Skeleton className="h-56" />
        <Skeleton className="h-56" />
      </div>
    </div>
  );
}

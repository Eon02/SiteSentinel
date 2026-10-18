'use client';

import dynamic from 'next/dynamic';
import { useEffect, useMemo, useState, useSyncExternalStore } from 'react';
import { useAlerts } from './AlertsProvider';
import CrewView from './CrewView';
import SiteList from './SiteList';
import { OfflineNote, Skeleton } from './Skeleton';
import { setCrewViewActive } from '@/lib/crewViewState';
import { usePolling } from '@/lib/usePolling';
import type { HazardReport, Site } from '@/lib/types';

const SiteMap = dynamic(() => import('./SiteMap'), {
  ssr: false,
  loading: () => <Skeleton className="h-full w-full rounded-none" />,
});

const VIEW_KEY = 'sitesentinel:view';
const PHONE_QUERY = '(max-width: 767px)';

// Phones get the Crewmember View by default; anyone can switch views and the choice is kept.
export default function Dashboard() {
  const isPhone = useSyncExternalStore(subscribePhone, () => window.matchMedia(PHONE_QUERY).matches, () => null);
  const [choice, setChoice] = useState<'crew' | 'supervisor' | null>(() => readView());
  const view = isPhone === null ? null : (choice ?? (isPhone ? 'crew' : 'supervisor'));

  // Lets the header's alert banner hide its bars while the crew view is showing.
  useEffect(() => {
    setCrewViewActive(view === 'crew');
    return () => setCrewViewActive(false);
  }, [view]);

  if (view === null) return <Skeleton className="m-4 h-64" />; // server render: screen size unknown

  const switchTo = (v: 'crew' | 'supervisor') => {
    setChoice(v);
    try {
      localStorage.setItem(VIEW_KEY, v);
    } catch {
      // storage blocked: the choice lasts until reload
    }
  };
  if (view === 'crew') return <CrewView onSupervisorView={() => switchTo('supervisor')} />;
  return <SupervisorDashboard onCrewView={isPhone ? () => switchTo('crew') : undefined} />;
}

function subscribePhone(onChange: () => void) {
  const mq = window.matchMedia(PHONE_QUERY);
  mq.addEventListener('change', onChange);
  return () => mq.removeEventListener('change', onChange);
}

function readView(): 'crew' | 'supervisor' | null {
  if (typeof window === 'undefined') return null;
  try {
    const v = localStorage.getItem(VIEW_KEY);
    return v === 'crew' || v === 'supervisor' ? v : null;
  } catch {
    return null;
  }
}

function SupervisorDashboard({ onCrewView }: { onCrewView?: () => void }) {
  const sites = usePolling<Site[]>('/api/sites', 30_000);
  const reports = usePolling<HazardReport[]>('/api/reports', 30_000);
  const { data: alerts } = useAlerts();
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const stopWorkSiteIds = useMemo(
    () => new Set((alerts ?? []).filter((a) => a.severity === 'stop_work').map((a) => a.siteId)),
    [alerts],
  );
  const stopWorkCount = (alerts ?? []).filter((a) => a.severity === 'stop_work').length;
  const siteList = sites.data ?? [];
  const highRisk = siteList.filter((s) => s.score >= 7).length;

  return (
    <div className="flex flex-1 flex-col lg:min-h-0 lg:flex-row">
      <section aria-label="Site map" className="h-[50vh] lg:h-auto lg:w-[65%]">
        <SiteMap
          className="h-full w-full"
          sites={siteList}
          reports={reports.data}
          stopWorkSiteIds={stopWorkSiteIds}
          selectedId={selectedId}
          onSelect={setSelectedId}
        />
      </section>

      <section aria-label="Ranked sites" className="flex flex-col gap-3 p-4 lg:w-[35%] lg:overflow-y-auto">
        {onCrewView && (
          <button type="button" onClick={onCrewView} className="min-h-11 self-end px-2 text-sm font-semibold text-[#16213E] underline">
            Crew view
          </button>
        )}
        <div className="grid grid-cols-3 gap-2">
          <Stat label="Sites" value={sites.data ? siteList.length : undefined} />
          <Stat label="High risk (7+)" value={sites.data ? highRisk : undefined} tone={highRisk ? 'orange' : undefined} />
          <Stat label="Stop-work alerts" value={alerts ? stopWorkCount : undefined} tone={stopWorkCount ? 'red' : undefined} />
        </div>
        <OfflineNote show={sites.offline} />
        {sites.loading && !sites.data ? (
          <div className="flex flex-col gap-2">
            {Array.from({ length: 6 }, (_, i) => <Skeleton key={i} className="h-16" />)}
          </div>
        ) : siteList.length === 0 ? (
          <p className="rounded-lg bg-white p-4 text-slate-800">No sites yet. Check back once the database is loaded.</p>
        ) : (
          <SiteList sites={siteList} selectedId={selectedId} onSelect={setSelectedId} />
        )}
      </section>
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value?: number; tone?: 'orange' | 'red' }) {
  const color = tone === 'red' ? 'text-[#C92A2A]' : tone === 'orange' ? 'text-[#C2410C]' : 'text-[#16213E]';
  return (
    <div className="rounded-lg bg-white p-2 text-center">
      <div className={`text-3xl font-extrabold ${color}`}>{value ?? '–'}</div>
      <div className="text-sm font-semibold leading-tight text-slate-800">{label}</div>
    </div>
  );
}

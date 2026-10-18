'use client';

import Link from 'next/link';
import { useEffect, useRef, useState } from 'react';
import { ArrowDown, ArrowUp, ChevronDown, Minus, Search, TriangleAlert } from 'lucide-react';
import ConditionsSummary from './ConditionsSummary';
import RiskBadge from './RiskBadge';
import { OfflineNote, Skeleton } from './Skeleton';
import { factorLabel, formatScore, topFactorLabels } from '@/lib/risk';
import { usePolling } from '@/lib/usePolling';
import type { Site, SiteDetail } from '@/lib/types';

type Props = {
  sites: Site[];
  selectedId?: string | null;
  onSelect: (id: string | null) => void;
};

export default function SiteList({ sites, selectedId, onSelect }: Props) {
  const [query, setQuery] = useState('');
  const [alertsOnly, setAlertsOnly] = useState(false);

  const q = query.trim().toLowerCase();
  // Rank is the position in the full score-sorted list, so it stays stable while filtering.
  const ranked = sites.map((site, i) => ({ site, rank: i + 1 }));
  const visible = ranked.filter(({ site }) =>
    (!alertsOnly || site.activeAlertCount > 0) &&
    (!q || [site.name, site.state, site.county, site.utility].some((v) => v?.toLowerCase().includes(q))),
  );

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-3">
        <label className="relative min-w-0 flex-1">
          <span className="sr-only">Search sites</span>
          <Search size={18} aria-hidden className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-700" />
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search name, state, utility"
            className="h-11 w-full rounded-lg border-2 border-slate-400 bg-white pl-10 pr-3 text-base text-slate-900 placeholder:text-slate-600 focus:border-[#16213E] focus:outline-none"
          />
        </label>
        <label className="flex h-11 cursor-pointer items-center gap-2 text-base font-semibold text-slate-900">
          <input
            type="checkbox"
            checked={alertsOnly}
            onChange={(e) => setAlertsOnly(e.target.checked)}
            className="h-5 w-5 accent-[#E8590C]"
          />
          Active alerts only
        </label>
      </div>

      {visible.length === 0 ? (
        <p className="rounded-lg bg-white p-4 text-slate-800">
          {alertsOnly ? 'No sites with active alerts.' : 'No sites match your search.'}
        </p>
      ) : (
        <ol className="flex flex-col gap-2">
          {visible.map(({ site, rank }) => (
            <li key={site.id}>
              <SiteRow site={site} rank={rank} selected={site.id === selectedId} onSelect={onSelect} />
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

// The selected card is the expanded one; clicking it again collapses it.
function SiteRow({ site, rank, selected, onSelect }: { site: Site; rank: number; selected: boolean; onSelect: (id: string | null) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const panelId = `site-summary-${site.id}`;

  // Selecting from the map expands this card; bring it into view.
  useEffect(() => {
    if (selected) ref.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }, [selected]);

  return (
    <div
      ref={ref}
      className={`rounded-lg border-2 bg-white ${selected ? 'border-[#E8590C]' : 'border-transparent'}`}
    >
      <div className="flex items-center gap-3 p-2 pr-3">
        <button
          type="button"
          onClick={() => onSelect(selected ? null : site.id)}
          aria-expanded={selected}
          aria-controls={panelId}
          className="flex min-h-11 min-w-0 flex-1 items-center gap-3 text-left"
        >
          <span className="w-7 shrink-0 text-center text-lg font-extrabold text-slate-900">{rank}</span>
          <span className="min-w-0 flex-1">
            <span className="flex items-center gap-1.5 font-bold text-slate-900">
              <span className="truncate">{site.name}</span>
              {site.activeAlertCount > 0 && (
                <TriangleAlert
                  size={18}
                  className="shrink-0 text-[#C92A2A]"
                  aria-label={`${site.activeAlertCount} active alert${site.activeAlertCount === 1 ? '' : 's'}`}
                />
              )}
            </span>
            <span className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-slate-800">
              <span>{site.state ?? '—'}</span>
              <RiskBadge score={site.score} />
              <Forecast today={site.score} tomorrow={site.forecastScore} />
            </span>
          </span>
          <ChevronDown
            size={20}
            aria-hidden
            className={`shrink-0 text-slate-800 transition-transform ${selected ? 'rotate-180' : ''}`}
          />
        </button>
        <Link
          href={`/site/${site.id}`}
          className="flex h-11 shrink-0 items-center rounded-lg bg-[#16213E] px-3 text-sm font-bold text-white"
        >
          View site
        </Link>
      </div>
      {selected && (
        <div id={panelId} className="border-t-2 border-slate-200 p-3">
          <SiteSummary site={site} />
        </div>
      )}
    </div>
  );
}

// Fetched only while expanded; refreshes with the site page's 15 s interval.
function SiteSummary({ site }: { site: Site }) {
  const { data, loading, offline } = usePolling<SiteDetail>(`/api/sites/${encodeURIComponent(site.id)}`, 15_000);

  if (loading && !data) return <Skeleton className="h-36" />;
  if (!data) return <OfflineNote show />;

  const factors = [...data.site.topFactors].sort((a, b) => b.weight - a.weight).slice(0, 3);
  return (
    <div className="flex flex-col gap-3">
      <OfflineNote show={offline} />
      <div>
        <h3 className="mb-2 text-sm font-bold uppercase tracking-wide text-slate-800">Current conditions</h3>
        <ConditionsSummary conditions={data.conditions} timezone={site.timezone} compact />
      </div>
      {factors.length > 0 && (
        <p className="text-sm text-slate-900">
          <span className="font-bold">Top factors: </span>
          {factors.map((f) => factorLabel(f.factor)).join(', ')}
        </p>
      )}
      {data.site.forecastTopFactors.length > 0 && (
        <p className="text-sm text-slate-600">
          Tomorrow: {topFactorLabels(data.site.forecastTopFactors).join(', ')}
        </p>
      )}
      {data.alerts.length > 0 && (
        <ul className="flex flex-col gap-1">
          {data.alerts.map((a) => (
            <li
              key={a.id}
              className={`rounded-md px-2 py-1 text-sm font-semibold ${
                a.severity === 'stop_work' ? 'bg-[#C92A2A] text-white' : 'bg-[#F2C200] text-[#1A1A1A]'
              }`}
            >
              {a.severity === 'stop_work' ? 'STOP WORK' : 'Warning'}: {a.message}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Forecast({ today, tomorrow }: { today: number; tomorrow?: number }) {
  if (tomorrow === undefined) return null;
  const diff = Math.round((tomorrow - today) * 10) / 10;
  const Icon = diff > 0 ? ArrowUp : diff < 0 ? ArrowDown : Minus;
  const trend = diff > 0 ? 'rising' : diff < 0 ? 'falling' : 'steady';
  return (
    <span className="inline-flex items-center gap-0.5 font-semibold" title={`Tomorrow: ${formatScore(tomorrow)} (${trend})`}>
      Tmrw {formatScore(tomorrow)}
      <Icon size={16} aria-label={trend} className={diff > 0 ? 'text-[#C92A2A]' : diff < 0 ? 'text-[#2F9E44]' : ''} />
    </span>
  );
}

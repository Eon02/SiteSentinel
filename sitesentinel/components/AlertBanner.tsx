'use client';

import Link from 'next/link';
import { useEffect, useRef, useState } from 'react';
import { CloudLightning, Flame, Thermometer, TriangleAlert, Waves, Wind, X, type LucideIcon } from 'lucide-react';
import { useAlerts } from './AlertsProvider';
import LightningCountdown, { LightningAutoClear } from './LightningCountdown';
import { useCrewViewActive } from '@/lib/crewViewState';
import type { Alert, AlertType } from '@/lib/types';

const TYPE_ICONS: Record<AlertType, LucideIcon> = {
  lightning: CloudLightning,
  flood: Waves,
  heat: Thermometer,
  wildfire: Flame,
  storm: Wind,
};

// A new lightning strike changes the key, so a dismissed lightning alert comes back.
const dismissKey = (a: Alert) => `${a.id}:${a.lastStrikeAt ?? ''}`;

export default function AlertBanner() {
  const { data: alerts, offline } = useAlerts();
  // Kept in memory: dismissals last while the app is open and reset on reload.
  const [dismissed, setDismissed] = useState<Set<string>>(() => new Set());
  const crewView = useCrewViewActive();

  // The Crewmember View shows its own site's alerts, so the bars stay hidden there. Lightning
  // alerts still auto-clear in the background, exactly as when their bar is dismissed.
  if (crewView) {
    return (
      <>
        {alerts?.filter((a) => a.type === 'lightning').map((a) => <LightningAutoClear key={a.id} alert={a} />)}
      </>
    );
  }
  if (!alerts?.length && !offline) return null;
  const visible = alerts?.filter((a) => !dismissed.has(dismissKey(a))) ?? [];
  const hiddenCount = (alerts?.length ?? 0) - visible.length;

  return (
    <div className="flex flex-col">
      {visible.map((a) => (
        <AlertBar
          key={a.id}
          alert={a}
          onDismiss={() => setDismissed((prev) => new Set(prev).add(dismissKey(a)))}
        />
      ))}
      {alerts
        ?.filter((a) => a.type === 'lightning' && dismissed.has(dismissKey(a)))
        .map((a) => <LightningAutoClear key={a.id} alert={a} />)}
      {hiddenCount > 0 && (
        <div className="flex items-center justify-between gap-3 bg-slate-800 px-4 text-sm font-semibold text-white">
          <span>
            {hiddenCount} alert{hiddenCount === 1 ? '' : 's'} hidden
          </span>
          <button type="button" onClick={() => setDismissed(new Set())} className="min-h-11 px-2 underline">
            Show
          </button>
        </div>
      )}
      {offline && (
        <p className="bg-slate-800 px-4 py-1 text-center text-sm font-semibold text-white">Offline, retrying…</p>
      )}
    </div>
  );
}

function AlertBar({ alert, onDismiss }: { alert: Alert; onDismiss: () => void }) {
  const flash = useStrikeFlash(alert.lastStrikeAt);
  const stopWork = alert.severity === 'stop_work';
  const Icon = TYPE_ICONS[alert.type] ?? TriangleAlert;
  return (
    <div
      className={`flex items-center ${stopWork ? 'bg-[#C92A2A] text-white' : 'bg-[#F2C200] text-[#1A1A1A]'} ${
        flash ? 'animate-strike-flash' : ''
      }`}
    >
      <Link
        href={`/site/${alert.siteId}`}
        className="flex min-h-11 min-w-0 flex-1 flex-wrap items-center gap-x-4 gap-y-2 py-2 pl-4"
      >
        <Icon size={24} aria-hidden className="shrink-0" />
        <div className="min-w-0 flex-1">
          <div className="font-extrabold">
            {stopWork ? 'STOP WORK' : 'Warning'} · {alert.siteName}
          </div>
          <div className="text-sm font-semibold">{alert.message}</div>
        </div>
        {alert.type === 'lightning' && <LightningCountdown alert={alert} compact />}
      </Link>
      <button
        type="button"
        onClick={onDismiss}
        aria-label={`Dismiss ${alert.type} alert for ${alert.siteName}`}
        className="mx-1 flex h-11 w-11 shrink-0 items-center justify-center rounded-full hover:bg-black/15"
      >
        <X size={22} aria-hidden />
      </button>
    </div>
  );
}

// Flash briefly whenever a new strike moves lastStrikeAt.
function useStrikeFlash(lastStrikeAt?: string) {
  const prev = useRef(lastStrikeAt);
  const [flash, setFlash] = useState(false);
  useEffect(() => {
    if (prev.current === lastStrikeAt) return;
    const isNewStrike = prev.current !== undefined;
    prev.current = lastStrikeAt;
    if (!isNewStrike) return;
    setFlash(true);
    const t = setTimeout(() => setFlash(false), 1600);
    return () => clearTimeout(t);
  }, [lastStrikeAt]);
  return flash;
}

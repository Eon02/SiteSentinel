'use client';

import Link from 'next/link';
import { useEffect, useRef, useState } from 'react';
import { CloudLightning, Flame, Thermometer, TriangleAlert, Waves, Wind, type LucideIcon } from 'lucide-react';
import { useAlerts } from './AlertsProvider';
import LightningCountdown from './LightningCountdown';
import type { Alert, AlertType } from '@/lib/types';

const TYPE_ICONS: Record<AlertType, LucideIcon> = {
  lightning: CloudLightning,
  flood: Waves,
  heat: Thermometer,
  wildfire: Flame,
  storm: Wind,
};

export default function AlertBanner() {
  const { data: alerts, offline } = useAlerts();
  if (!alerts?.length && !offline) return null;
  return (
    <div className="flex flex-col">
      {alerts?.map((a) => <AlertBar key={a.id} alert={a} />)}
      {offline && (
        <p className="bg-slate-800 px-4 py-1 text-center text-sm font-semibold text-white">Offline, retrying…</p>
      )}
    </div>
  );
}

function AlertBar({ alert }: { alert: Alert }) {
  const flash = useStrikeFlash(alert.lastStrikeAt);
  const stopWork = alert.severity === 'stop_work';
  const Icon = TYPE_ICONS[alert.type] ?? TriangleAlert;
  return (
    <Link
      href={`/site/${alert.siteId}`}
      className={`flex min-h-11 flex-wrap items-center gap-x-4 gap-y-2 px-4 py-2 ${
        stopWork ? 'bg-[#C92A2A] text-white' : 'bg-[#F2C200] text-[#1A1A1A]'
      } ${flash ? 'animate-strike-flash' : ''}`}
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

'use client';

import { useEffect, useRef, useState } from 'react';
import { DEMO_SPEED_FACTOR, LIGHTNING_WINDOW_MS } from '@/lib/risk';
import { useDemoSpeed } from '@/lib/useDemoSpeed';
import type { Alert } from '@/lib/types';

const ALL_CLEAR_SHOW_MS = 10_000;

function formatMmSs(ms: number) {
  const total = Math.max(0, Math.ceil(ms / 1000));
  return `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`;
}

// Counts down locally every second from lastStrikeAt; never polls for the time.
// Once "All clear" has shown for 10 s, clears the alert on the server. Returns ms remaining.
function useLightningCountdown(alert: Alert): number {
  const demoSpeed = useDemoSpeed();
  const [now, setNow] = useState(() => Date.now());
  const clearedFor = useRef<string | null>(null);

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  const windowMs = demoSpeed ? LIGHTNING_WINDOW_MS / DEMO_SPEED_FACTOR : LIGHTNING_WINDOW_MS;
  const strikeAt = Date.parse(alert.lastStrikeAt ?? alert.startedAt);
  const remaining = windowMs - (now - strikeAt);
  const strikeKey = `${alert.id}:${alert.lastStrikeAt ?? alert.startedAt}`;

  useEffect(() => {
    if (remaining > -ALL_CLEAR_SHOW_MS || clearedFor.current === strikeKey) return;
    clearedFor.current = strikeKey;
    void fetch(`/api/alerts/${alert.id}/clear`, { method: 'POST' }).catch(() => {
      clearedFor.current = null; // retry on the next tick
    });
  }, [remaining, strikeKey, alert.id]);

  return remaining;
}

// Keeps the countdown (and its server clear) running for an alert whose banner was dismissed.
export function LightningAutoClear({ alert }: { alert: Alert }) {
  useLightningCountdown(alert);
  return null;
}

export default function LightningCountdown({ alert, compact = false }: { alert: Alert; compact?: boolean }) {
  const remaining = useLightningCountdown(alert);

  if (remaining <= 0) {
    return (
      <div
        role="status"
        className={`rounded-lg bg-[#2F9E44] font-extrabold text-white ${compact ? 'px-3 py-1 text-lg' : 'px-4 py-3 text-2xl'}`}
      >
        All clear
      </div>
    );
  }

  return (
    <div role="timer" aria-live="off" className={`rounded-lg bg-white ${compact ? 'px-3 py-1' : 'px-4 py-3'}`}>
      <div className={`font-bold text-[#C92A2A] ${compact ? 'text-xs' : 'text-sm'}`}>Stop work. All clear in</div>
      <div className={`font-mono font-extrabold leading-none text-[#C92A2A] ${compact ? 'text-2xl' : 'text-5xl'}`}>
        {formatMmSs(remaining)}
      </div>
    </div>
  );
}

'use client';

import { useEffect, useState } from 'react';
import { CloudLightning, Send, Trash2 } from 'lucide-react';
import { useAlerts } from './AlertsProvider';
import { Skeleton } from './Skeleton';
import { setDemoSpeed, useDemoSpeed } from '@/lib/useDemoSpeed';
import { requestRefresh, usePolling } from '@/lib/usePolling';
import type { Site } from '@/lib/types';

export default function DemoPanel() {
  const { data: sites } = usePolling<Site[]>('/api/sites', 30_000);
  const { refetch: refetchAlerts } = useAlerts();
  const demoSpeed = useDemoSpeed();
  const [siteId, setSiteId] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [toast, setToast] = useState<{ text: string; error?: boolean } | null>(null);

  // Default to the Everglades site (highest score) once sites load.
  const effectiveSiteId = siteId || sites?.[0]?.id || '';

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 3000);
    return () => clearTimeout(t);
  }, [toast]);

  async function run(name: string, url: string, body?: unknown, success?: string) {
    setBusy(name);
    try {
      const res = await fetch(url, {
        method: 'POST',
        headers: body ? { 'Content-Type': 'application/json' } : undefined,
        body: body ? JSON.stringify(body) : undefined,
      });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error ?? `HTTP ${res.status}`);
      refetchAlerts();
      requestRefresh();
      if (success) setToast({ text: success });
    } catch (e) {
      setToast({ text: `Failed: ${e instanceof Error ? e.message : 'unknown error'}`, error: true });
    } finally {
      setBusy(null);
    }
  }

  const siteName = sites?.find((s) => s.id === effectiveSiteId)?.name ?? 'site';

  return (
    <div className="mx-auto flex w-full max-w-xl flex-col gap-5 p-4">
      <h1 className="text-2xl font-extrabold">Demo control panel</h1>

      <label className="flex flex-col gap-1 text-lg font-bold">
        Site
        {sites ? (
          <select
            value={effectiveSiteId}
            onChange={(e) => setSiteId(e.target.value)}
            className="min-h-12 rounded-lg border-2 border-slate-500 bg-white px-3 text-lg font-normal"
          >
            {sites.map((s) => (
              <option key={s.id} value={s.id}>{s.name}{s.state ? ` (${s.state})` : ''}</option>
            ))}
          </select>
        ) : (
          <Skeleton className="h-12" />
        )}
      </label>

      <button
        type="button"
        disabled={!effectiveSiteId || busy !== null}
        onClick={() => run('strike', '/api/demo/strike', { siteId: effectiveSiteId }, `Lightning strike at ${siteName}`)}
        className="flex min-h-16 items-center justify-center gap-2 rounded-xl bg-[#C92A2A] text-xl font-extrabold text-white disabled:opacity-60"
      >
        <CloudLightning size={28} aria-hidden /> Lightning strike
      </button>

      <label className="flex min-h-12 cursor-pointer items-center justify-between gap-3 rounded-xl bg-white px-4 py-2">
        <span>
          <span className="block text-lg font-bold">Demo speed</span>
          <span className="block text-sm text-slate-800">30-minute countdown runs in 30 seconds (60× faster) in every tab of this browser.</span>
        </span>
        <input
          type="checkbox"
          role="switch"
          checked={demoSpeed}
          onChange={(e) => setDemoSpeed(e.target.checked)}
          className="h-7 w-7 shrink-0 accent-[#E8590C]"
        />
      </label>

      <button
        type="button"
        disabled={busy !== null}
        onClick={() => run('clear', '/api/demo/clear', undefined, 'All alerts reset')}
        className="flex min-h-12 items-center justify-center gap-2 rounded-xl border-2 border-[#16213E] bg-white text-lg font-bold text-[#16213E] disabled:opacity-60"
      >
        <Trash2 size={22} aria-hidden /> Clear all alerts
      </button>

      {/* Placeholder until the backend provides a briefing endpoint. */}
      <button
        type="button"
        onClick={() => setToast({ text: 'Briefing sent' })}
        className="flex min-h-12 items-center justify-center gap-2 rounded-xl bg-[#16213E] text-lg font-bold text-white"
      >
        <Send size={22} aria-hidden /> Send briefing now
      </button>

      {toast && (
        <div
          role="status"
          className={`fixed bottom-6 left-1/2 z-50 -translate-x-1/2 rounded-xl px-5 py-3 text-lg font-bold text-white shadow-lg ${
            toast.error ? 'bg-[#C92A2A]' : 'bg-[#2F9E44]'
          }`}
        >
          {toast.text}
        </div>
      )}
    </div>
  );
}

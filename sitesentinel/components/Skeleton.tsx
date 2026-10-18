import type { ReactNode } from 'react';

export function Skeleton({ className = '' }: { className?: string }) {
  return <div className={`animate-pulse rounded-lg bg-slate-300 ${className}`} aria-hidden />;
}

// Shown where data would be when this device has nothing saved for it (e.g. first launch with
// no signal).
export function NothingSavedNote({ children }: { children: ReactNode }) {
  return (
    <p role="status" className="rounded-xl border-2 border-dashed border-slate-500 bg-white p-4 text-base font-semibold text-slate-800">
      {children}
    </p>
  );
}

export function OfflineNote({ show }: { show: boolean }) {
  if (!show) return null;
  return (
    <p role="status" className="inline-block rounded bg-slate-800 px-2 py-0.5 text-sm font-semibold text-white">
      Offline, retrying…
    </p>
  );
}

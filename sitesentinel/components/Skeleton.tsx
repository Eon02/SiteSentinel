export function Skeleton({ className = '' }: { className?: string }) {
  return <div className={`animate-pulse rounded-lg bg-slate-300 ${className}`} aria-hidden />;
}

export function OfflineNote({ show }: { show: boolean }) {
  if (!show) return null;
  return (
    <p role="status" className="inline-block rounded bg-slate-800 px-2 py-0.5 text-sm font-semibold text-white">
      Offline, retrying…
    </p>
  );
}

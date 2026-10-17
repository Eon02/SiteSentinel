import Link from 'next/link';
import { WifiOff } from 'lucide-react';

// Shown by the service worker when there's no signal and the requested page was never opened
// on this device. Pages opened before (and /report) load from the phone's cache instead.
export default function OfflinePage() {
  return (
    <div className="mx-auto flex w-full max-w-xl flex-col items-center gap-4 p-6 text-center">
      <WifiOff size={64} className="text-slate-700" aria-hidden />
      <h1 className="text-2xl font-extrabold">No signal</h1>
      <p className="text-lg">
        This page hasn&apos;t been opened on this device yet, so it isn&apos;t saved for offline use. You can still
        report a hazard: it&apos;s saved on your phone and sent automatically when you&apos;re back in signal.
      </p>
      <div className="flex w-full flex-col gap-3 sm:flex-row">
        <a href="/report" className="flex min-h-12 flex-1 items-center justify-center rounded-xl bg-[#E8590C] px-4 text-lg font-bold text-white">
          Report a hazard
        </a>
        <Link href="/" className="flex min-h-12 flex-1 items-center justify-center rounded-xl bg-[#16213E] px-4 text-lg font-bold text-white">
          Dashboard
        </Link>
      </div>
    </div>
  );
}

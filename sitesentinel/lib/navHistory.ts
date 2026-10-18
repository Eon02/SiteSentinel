'use client';

import { usePathname } from 'next/navigation';
import { useEffect } from 'react';

// Remembers whether this tab has moved between pages inside the app, so a "close"/back button
// can return to the previous app page instead of leaving the app (e.g. when /report was
// opened from a link in another app, or as the first page of a fresh tab).
let previousPath: string | null = null;
let currentPath: string | null = null;

export function hasInAppHistory(): boolean {
  return previousPath !== null;
}

// Mounted once in the root layout.
export function NavTracker() {
  const pathname = usePathname();
  useEffect(() => {
    if (currentPath !== null && currentPath !== pathname) previousPath = currentPath;
    currentPath = pathname;
  }, [pathname]);
  return null;
}

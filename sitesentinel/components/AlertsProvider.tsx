'use client';

import { createContext, useContext } from 'react';
import { usePolling, type Polled } from '@/lib/usePolling';
import type { Alert } from '@/lib/types';

const AlertsContext = createContext<Polled<Alert[]> | null>(null);

// One /api/alerts poll (every 5 s) shared by the banner and every page.
export default function AlertsProvider({ children }: { children: React.ReactNode }) {
  const alerts = usePolling<Alert[]>('/api/alerts', 5000);
  return <AlertsContext.Provider value={alerts}>{children}</AlertsContext.Provider>;
}

export function useAlerts(): Polled<Alert[]> {
  const ctx = useContext(AlertsContext);
  if (!ctx) throw new Error('useAlerts must be used inside <AlertsProvider>');
  return ctx;
}

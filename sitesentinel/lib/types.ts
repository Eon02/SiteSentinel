export type HazardFactor = { factor: string; weight: number };

export type Site = {
  id: string; name: string; utility?: string; plantType?: string;
  county?: string; state?: string; timezone: string; nearRiver: boolean;
  lat: number; lon: number;
  score: number;               // today's score, 0-10
  forecastScore?: number;      // tomorrow's score
  topFactors: HazardFactor[];
  activeAlertCount: number;
};

export type Conditions = {
  observedAt: string; tempF?: number; heatIndexF?: number;
  precip24hIn?: number; soilMoisture?: number; windMph?: number;
};

export type AlertType = 'lightning' | 'flood' | 'heat' | 'wildfire' | 'storm';

export type Alert = {
  id: number; siteId: string; siteName: string; type: AlertType;
  severity: 'warning' | 'stop_work'; message: string;
  startedAt: string; lastStrikeAt?: string; clearedAt?: string;
};

export type HazardReport = {
  id: number; siteId: string; hazardType: string; description?: string;
  photoUrl?: string; reportedBy?: string; lat: number; lon: number; createdAt: string;
};

export type NewReport = {
  siteId: string; hazardType: string; description?: string;
  photoUrl?: string; reportedBy?: string; lat: number; lon: number;
  clientId?: string; // UUID from the device's offline queue; makes retried uploads idempotent
};

export type SiteDetail = {
  site: Site; conditions?: Conditions;
  alerts: Alert[]; reports: HazardReport[];
};

export const HAZARD_TYPES = [
  { value: 'wildlife', label: 'Wildlife' },
  { value: 'mud', label: 'Mud / stuck vehicle' },
  { value: 'standing_water', label: 'Standing water' },
  { value: 'downed_line', label: 'Downed line' },
  { value: 'heat_illness', label: 'Heat illness' },
  { value: 'fire_smoke', label: 'Fire / smoke' },
  { value: 'other', label: 'Other' },
] as const;

export function hazardLabel(value: string): string {
  return HAZARD_TYPES.find((h) => h.value === value)?.label ?? value;
}
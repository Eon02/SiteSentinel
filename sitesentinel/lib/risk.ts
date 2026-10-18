export type RiskLevel = { label: 'Low' | 'Moderate' | 'High' | 'Extreme'; color: string; textColor: string };

export function riskLevel(score: number): RiskLevel {
  if (score >= 8) return { label: 'Extreme', color: '#C92A2A', textColor: '#FFFFFF' };
  if (score >= 6) return { label: 'High', color: '#E8590C', textColor: '#FFFFFF' };
  if (score >= 3) return { label: 'Moderate', color: '#F2C200', textColor: '#1A1A1A' };
  return { label: 'Low', color: '#2F9E44', textColor: '#FFFFFF' };
}

export function riskColor(score: number): string {
  return riskLevel(score).color;
}

export function formatScore(score: number): string {
  return (Math.round(score * 10) / 10).toFixed(1);
}

const FACTOR_LABELS: Record<string, string> = {
  heat: 'Heat',
  heat_index: 'Heat',
  lightning: 'Lightning',
  soil: 'Soil saturation',
  soil_moisture: 'Soil saturation',
  soil_saturation: 'Soil saturation',
  precip: 'Heavy rain',
  precipitation: 'Heavy rain',
  rain: 'Heavy rain',
  flood: 'Flooding',
  river: 'River flooding',
  wind: 'High wind',
  wildfire: 'Wildfire',
  fire: 'Wildfire',
  storm: 'Storms',
  wildlife: 'Wildlife',
};

export function factorLabel(factor: string): string {
  return FACTOR_LABELS[factor] ?? factor.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase());
}

// Labels of the heaviest few factors, e.g. ['High wind', 'Ground'].
export function topFactorLabels(factors: { factor: string; weight: number }[], limit = 3): string[] {
  return [...factors].sort((a, b) => b.weight - a.weight).slice(0, limit).map((f) => factorLabel(f.factor));
}

export const LIGHTNING_WINDOW_MS = 30 * 60 * 1000;
export const DEMO_SPEED_FACTOR = 60;

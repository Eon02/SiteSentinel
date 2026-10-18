// Turns WMO weather codes (as used by Open-Meteo) into a few simple kinds a crew member can read
// at a glance, each with its own icon in the Crewmember View.
export type WeatherKind = 'clear' | 'partly' | 'cloudy' | 'fog' | 'drizzle' | 'rain' | 'snow' | 'storm';

export type Forecast = {
  siteId: string;
  timezone: string;
  observedAt: string;
  currentTempF: number | null;
  hours: number; // the window below covers the next `hours` hours
  kind: WeatherKind; // most significant weather in the window
  label: string;
  precipChancePct: number | null; // highest hourly chance of precipitation in the window
  highF: number | null;
  lowF: number | null;
};

const LABELS: Record<WeatherKind, string> = {
  clear: 'Clear',
  partly: 'Partly cloudy',
  cloudy: 'Cloudy',
  fog: 'Fog',
  drizzle: 'Drizzle',
  rain: 'Rain',
  snow: 'Snow',
  storm: 'Thunderstorms',
};

// Higher = more important for a crew planning the next few hours.
const SEVERITY: WeatherKind[] = ['clear', 'partly', 'cloudy', 'fog', 'drizzle', 'rain', 'snow', 'storm'];

export function weatherKind(code: number): WeatherKind {
  if (code >= 95) return 'storm'; // 95-99 thunderstorm
  if ((code >= 71 && code <= 77) || code === 85 || code === 86) return 'snow';
  if ((code >= 61 && code <= 67) || (code >= 80 && code <= 82)) return 'rain'; // incl. freezing rain
  if (code >= 51 && code <= 57) return 'drizzle';
  if (code === 45 || code === 48) return 'fog';
  if (code === 3) return 'cloudy';
  if (code === 1 || code === 2) return 'partly';
  return 'clear';
}

export function weatherLabel(kind: WeatherKind): string {
  return LABELS[kind];
}

// The most significant weather among several hourly codes (a storm outranks sun, and so on).
export function worstKind(codes: number[]): WeatherKind {
  return codes.map(weatherKind).reduce<WeatherKind>(
    (worst, k) => (SEVERITY.indexOf(k) > SEVERITY.indexOf(worst) ? k : worst),
    'clear',
  );
}

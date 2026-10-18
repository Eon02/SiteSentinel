import { getSiteLocation } from '@/lib/data';
import { worstKind, weatherLabel, type Forecast } from '@/lib/weather';

const OPEN_METEO_URL = process.env.OPEN_METEO_URL || 'https://api.open-meteo.com/v1/forecast';
const HOURS = 12;

// The next 12 hours of weather at a site, simplified for the Crewmember View. Same-origin so the
// service worker caches it and crews still see the last forecast without signal.
export async function GET(_req: Request, ctx: RouteContext<'/api/sites/[id]/forecast'>) {
  const { id } = await ctx.params;
  const site = await getSiteLocation(id);
  if (!site) return Response.json({ error: 'Site not found' }, { status: 404 });

  const params = new URLSearchParams({
    latitude: String(site.lat),
    longitude: String(site.lon),
    current: 'temperature_2m,weather_code',
    hourly: 'temperature_2m,precipitation_probability,weather_code',
    forecast_hours: String(HOURS),
    temperature_unit: 'fahrenheit',
    timezone: 'auto',
  });
  let data: {
    current?: { time: string; temperature_2m: number | null; weather_code: number | null };
    hourly?: { temperature_2m: (number | null)[]; precipitation_probability: (number | null)[]; weather_code: (number | null)[] };
  };
  try {
    const res = await fetch(`${OPEN_METEO_URL}?${params}`, { cache: 'no-store', signal: AbortSignal.timeout(10_000) });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    data = await res.json();
  } catch {
    return Response.json({ error: 'Weather forecast is unavailable right now' }, { status: 502 });
  }

  const nums = (xs: (number | null)[] = []) => xs.filter((x): x is number => typeof x === 'number');
  const temps = nums(data.hourly?.temperature_2m);
  const chances = nums(data.hourly?.precipitation_probability);
  const codes = nums(data.hourly?.weather_code);
  if (typeof data.current?.weather_code === 'number') codes.push(data.current.weather_code);
  const kind = worstKind(codes);

  const forecast: Forecast = {
    siteId: id,
    timezone: site.timezone,
    observedAt: data.current?.time ?? new Date().toISOString(),
    currentTempF: data.current?.temperature_2m ?? null,
    hours: HOURS,
    kind,
    label: weatherLabel(kind),
    precipChancePct: chances.length ? Math.max(...chances) : null,
    highF: temps.length ? Math.max(...temps) : null,
    lowF: temps.length ? Math.min(...temps) : null,
  };
  return Response.json(forecast);
}

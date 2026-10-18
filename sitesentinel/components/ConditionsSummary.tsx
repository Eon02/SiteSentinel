import { CloudRain, Sprout, Thermometer, Wind, type LucideIcon } from 'lucide-react';
import type { Conditions } from '@/lib/types';

type Props = { conditions?: Conditions; timezone: string; compact?: boolean };

// Current weather cards, shared by the site page and the expanded dashboard card.
export default function ConditionsSummary({ conditions, timezone, compact = false }: Props) {
  if (!conditions) return <p className="text-slate-800">No weather observations yet.</p>;
  const cards: { icon: LucideIcon; label: string; value: string }[] = [
    { icon: Thermometer, label: 'Temperature', value: fmt(conditions.tempF, '°F') },
    { icon: Thermometer, label: 'Heat index', value: fmt(conditions.heatIndexF, '°F') },
    { icon: CloudRain, label: '24-hr rain', value: fmt(conditions.precip24hIn, ' in', 1) },
    // soil_moisture is a 0-1 saturation fraction (the backend worker converts it).
    {
      icon: Sprout,
      label: 'Soil saturation',
      value: conditions.soilMoisture === undefined ? '—' : `${Math.round(conditions.soilMoisture * 100)}%`,
    },
    { icon: Wind, label: 'Wind', value: fmt(conditions.windMph, ' mph') },
    { icon: Wind, label: 'Gusts', value: fmt(conditions.windGustMph, ' mph') },
  ];
  return (
    <>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        {cards.map((c) => (
          <div key={c.label} className={`rounded-lg bg-slate-100 ${compact ? 'p-2' : 'p-3'}`}>
            <div className={`flex items-center gap-1 font-semibold text-slate-800 ${compact ? 'text-xs' : 'text-sm'}`}>
              <c.icon size={compact ? 14 : 16} aria-hidden /> {c.label}
            </div>
            <div className={`font-extrabold text-slate-900 ${compact ? 'text-lg' : 'text-2xl'}`}>{c.value}</div>
          </div>
        ))}
      </div>
      <p className={`mt-2 text-slate-800 ${compact ? 'text-xs' : 'text-sm'}`}>
        Observed {new Date(conditions.observedAt).toLocaleString([], { timeZone: timezone, dateStyle: 'medium', timeStyle: 'short' })} site time
      </p>
    </>
  );
}

function fmt(v: number | undefined, unit: string, digits = 0) {
  return v === undefined ? '—' : `${v.toFixed(digits)}${unit}`;
}

import { factorLabel } from '@/lib/risk';
import type { HazardFactor } from '@/lib/types';

export default function FactorBars({ factors }: { factors: HazardFactor[] }) {
  if (factors.length === 0) {
    return <p className="text-slate-700">No hazard factors reported today.</p>;
  }
  const sorted = [...factors].sort((a, b) => b.weight - a.weight);
  const total = sorted.reduce((sum, f) => sum + f.weight, 0) || 1;
  const max = sorted[0].weight || 1;
  return (
    <ul className="space-y-3">
      {sorted.map((f) => {
        const pct = Math.round((f.weight / total) * 100);
        return (
          <li key={f.factor}>
            <div className="mb-1 flex justify-between text-base font-semibold text-slate-900">
              <span>{factorLabel(f.factor)}</span>
              <span>{pct}%</span>
            </div>
            <div className="h-4 w-full overflow-hidden rounded-full bg-slate-200" aria-hidden>
              <div className="h-full rounded-full bg-[#16213E]" style={{ width: `${(f.weight / max) * 100}%` }} />
            </div>
          </li>
        );
      })}
    </ul>
  );
}

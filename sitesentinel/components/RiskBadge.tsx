import { formatScore, riskLevel } from '@/lib/risk';

type Props = { score: number; size?: 'sm' | 'lg'; caption?: string };

// Color plus number plus label, so color is never the only signal.
export default function RiskBadge({ score, size = 'sm', caption }: Props) {
  const { label, color, textColor } = riskLevel(score);
  if (size === 'lg') {
    return (
      <div
        className="flex min-w-36 flex-col items-center rounded-xl px-5 py-3"
        style={{ backgroundColor: color, color: textColor }}
      >
        {caption && <span className="text-sm font-semibold uppercase tracking-wide">{caption}</span>}
        <span className="text-4xl font-extrabold leading-tight">{formatScore(score)}</span>
        <span className="text-lg font-bold">{label}</span>
      </div>
    );
  }
  return (
    <span
      className="inline-flex items-center gap-1 whitespace-nowrap rounded-md px-2 py-0.5 text-sm font-bold"
      style={{ backgroundColor: color, color: textColor }}
    >
      {formatScore(score)} <span className="font-semibold">{label}</span>
    </span>
  );
}

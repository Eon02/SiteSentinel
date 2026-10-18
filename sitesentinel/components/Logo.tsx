import { Big_Shoulders } from "next/font/google";

// Next has no metrics for this font, so skip its generated fallback (and the build warning).
const bigShoulders = Big_Shoulders({
  subsets: ["latin"],
  weight: "800",
  adjustFontFallback: false,
  fallback: ["Arial Narrow", "sans-serif"],
});

const ACCENT = "#EE6A12";
// Matches the header background so the lightning bolt reads as a cutout in the flag.
const BACKGROUND = "#16213E";

/** "Locate Flag" mark and wordmark, drawn for the dark header. */
export default function Logo() {
  return (
    <span className="flex items-center gap-2">
      <svg width="34" height="34" viewBox="0 0 64 64" aria-hidden="true" className="shrink-0">
        <rect x="14" y="6" width="4" height="50" rx="2" fill="#FFFFFF" />
        <path d="M18 8 H54 L46 19 L54 30 H18 Z" fill={ACCENT} />
        <path d="M37 11 L28 21 H34 L31 28 L41 17 H35 Z" fill={BACKGROUND} />
        <rect x="4" y="54" width="56" height="3" rx="1.5" fill="#FFFFFF" />
        <line
          x1="8"
          y1="61"
          x2="56"
          y2="61"
          stroke={ACCENT}
          strokeWidth="2.5"
          strokeDasharray="5 3.5"
          strokeLinecap="round"
        />
      </svg>
      <span
        className={`${bigShoulders.className} text-[28px] leading-none tracking-[0.045em] uppercase`}
      >
        <span className="text-white">Site</span>
        <span style={{ color: ACCENT }}>Sentinel</span>
      </span>
    </span>
  );
}

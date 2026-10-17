import { Droplets, Flame, PawPrint, Thermometer, TriangleAlert, Truck, Zap, type LucideIcon } from 'lucide-react';

const ICONS: Record<string, LucideIcon> = {
  wildlife: PawPrint,
  mud: Truck,
  standing_water: Droplets,
  downed_line: Zap,
  heat_illness: Thermometer,
  fire_smoke: Flame,
};

export default function HazardIcon({ type, size = 16 }: { type: string; size?: number }) {
  const Icon = ICONS[type] ?? TriangleAlert;
  return <Icon size={size} aria-hidden />;
}

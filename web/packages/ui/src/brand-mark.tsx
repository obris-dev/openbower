import { clsx } from "clsx";

/** The product mark (the favicon's starburst), inline SVG so it scales
 * crisply anywhere; size via className. */
export function BrandMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 400 400" aria-hidden className={clsx("h-7 w-7 shrink-0", className)}>
      <circle cx="200" cy="200" r="180" fill="#00B7C3" />
      <polygon
        points="244.3,34.8 228.7,171.3 365.2,155.7 239.1,210.5 320.9,320.9 210.5,239.1 155.7,365.2 171.3,228.7 34.8,244.3 160.9,189.5 79.1,79.1 189.5,160.9"
        fill="#F7F7F5"
      />
    </svg>
  );
}

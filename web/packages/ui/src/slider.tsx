import type { CSSProperties, ComponentPropsWithoutRef } from "react";
import { cn } from "./cn";
import { sliderFill } from "./slider-fill";

// Each engine's own parts. The thumb is the fill's color, so its surface
// ring is the only separation and must stay. Firefox draws a track of its
// own inside the input; it is cleared so the drawn track shows through.
const ENGINE_PARTS = [
  "[&::-moz-range-track]:h-full [&::-moz-range-track]:rounded-full [&::-moz-range-track]:bg-transparent",
  "[&::-webkit-slider-thumb]:size-4 [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full",
  "[&::-webkit-slider-thumb]:bg-signal-600 [&::-webkit-slider-thumb]:ring-2 [&::-webkit-slider-thumb]:ring-surface",
  "[&::-moz-range-thumb]:size-4 [&::-moz-range-thumb]:rounded-full [&::-moz-range-thumb]:border-0",
  "[&::-moz-range-thumb]:bg-signal-600 [&::-moz-range-thumb]:ring-2 [&::-moz-range-thumb]:ring-surface",
];

/** A number picked along a range, on the native range input: arrow keys,
 * Home and End, and the screen reader's value come from the browser. The
 * track is drawn here (the browser's own empty track cannot take a
 * role): signal up to the value, the track role after it. */
export function Slider({
  value,
  onChange,
  min,
  max,
  step,
  className,
  ...rest
}: {
  value: number;
  onChange: (next: number) => void;
  min: number;
  max: number;
  step: number;
  className?: string;
} & Omit<ComponentPropsWithoutRef<"input">, "type" | "value" | "onChange" | "min" | "max" | "step" | "className" | "style">) {
  const { value: clamped, percent: filled } = sliderFill(value, min, max);
  const style = { "--slider-fill": `${filled}%` } as CSSProperties;
  return (
    <input
      type="range"
      value={clamped}
      min={min}
      max={max}
      step={step}
      onChange={(event) => onChange(Number(event.target.value))}
      style={style}
      {...rest}
      className={cn(
        "h-2 w-full cursor-pointer appearance-none rounded-full border border-edge",
        "bg-[linear-gradient(to_right,var(--color-signal-600)_var(--slider-fill),var(--color-track)_var(--slider-fill))]",
        ENGINE_PARTS,
        "focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-signal-600",
        "disabled:cursor-not-allowed disabled:opacity-40",
        className,
      )}
    />
  );
}

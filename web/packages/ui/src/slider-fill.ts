/** Where a slider's value sits: the browser clamps the thumb into the
 * range, so the value and the drawn fill take the same clamp and never
 * disagree with it. */
export function sliderFill(value: number, min: number, max: number): { value: number; percent: number } {
  const clamped = Math.min(max, Math.max(min, value));
  const percent = max > min ? ((clamped - min) / (max - min)) * 100 : 0;
  return { value: clamped, percent };
}

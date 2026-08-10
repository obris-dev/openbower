import { Skeleton } from "@bower/ui";

import { MODES } from "./modes";
import type { ModeName } from "./types";

/** The Suspense fallback, mirroring the mode's real structure (subtitle,
 * fields, the remember row, the button) so the card paints at its final
 * size instead of flashing empty and jumping. Reads the same MODES
 * record as the form, so the skeleton cannot drift. */
export function CredentialsFormFallback({ mode }: { mode: ModeName }) {
  const { confirmPassword, rememberEmail } = MODES[mode];
  const fields = confirmPassword ? 3 : 2;
  return (
    <div aria-hidden>
      <Skeleton className="mb-6 mt-1 h-5 w-40" />
      <div className="space-y-4">
        {Array.from({ length: fields }, (_, i) => (
          <div key={i}>
            <Skeleton className="h-4 w-24" />
            <Skeleton className="mt-1.5 h-9 w-full" />
          </div>
        ))}
        {rememberEmail && <Skeleton className="h-5 w-36" />}
        <Skeleton className="h-9 w-full" />
      </div>
      <Skeleton className="mx-auto mt-6 h-5 w-48" />
    </div>
  );
}

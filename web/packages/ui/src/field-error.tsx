import type { ReactNode } from "react";

/** Tier-a error: field-level validation, rendered at its field. Give it
 * an `id` and point the input's `aria-describedby` at it (pair with the
 * input's `invalid` prop) so the message is announced in context. The
 * form-level banner is ErrorMessage; operation outcomes are toasts. */
export function FieldError({ id, children }: { id?: string; children: ReactNode }) {
  return (
    <p id={id} className="mt-1 text-xs text-red-600 dark:text-red-400">
      {children}
    </p>
  );
}

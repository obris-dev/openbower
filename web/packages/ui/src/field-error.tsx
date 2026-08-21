import type { ReactNode } from "react";

/** Tier-a field message, rendered at its field. `tone` picks the
 * severity: danger (the server refused / validation failed) or
 * warning (incomplete, not wrong). Give it an `id` and point the
 * input's `aria-describedby` at it (pair with the input's `invalid`
 * or `warned` prop) so the message is announced in context. The
 * form-level banner is ErrorMessage; operation outcomes are toasts. */
export function FieldError({
  id,
  tone = "danger",
  children,
}: {
  id?: string;
  tone?: "danger" | "warning";
  children: ReactNode;
}) {
  return (
    <p id={id} className={tone === "warning" ? "mt-1 text-xs text-warning" : "mt-1 text-xs text-danger"}>
      {children}
    </p>
  );
}

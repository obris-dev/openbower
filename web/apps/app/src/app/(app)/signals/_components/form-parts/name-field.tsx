import { FieldError, Input } from "@bower/ui";

import { NAME_TITLE } from "./copy";
import { Section } from "./section";

/** A signal's Name, as both forms ask it: the section titled by its label,
 * an optional hint under the title, the input, and its gap once an attempt
 * was blocked, each described to the input. The caller owns the bound (a
 * hosted type's is the tighter of the service's and the sheet's). */
export function NameField({
  id,
  hint,
  gap,
  value,
  placeholder,
  maxLength,
  onChange,
}: {
  id: string;
  hint?: string;
  /** The gap's message, shown only after a blocked attempt; null otherwise. */
  gap: string | null;
  value: string;
  placeholder?: string;
  maxLength?: number;
  onChange: (value: string) => void;
}) {
  const hintId = `${id}-hint`;
  const gapId = `${id}-gap`;
  const describedBy = [hint ? hintId : null, gap ? gapId : null].filter(Boolean).join(" ");
  return (
    <Section title={NAME_TITLE} titleFor={id}>
      {hint && (
        <p id={hintId} className="-mt-2 text-xs text-muted">
          {hint}
        </p>
      )}
      <div>
        <Input
          id={id}
          maxLength={maxLength}
          warned={gap !== null}
          aria-describedby={describedBy || undefined}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder={placeholder}
        />
        {gap && (
          <FieldError id={gapId} tone="warning">
            {gap}
          </FieldError>
        )}
      </div>
    </Section>
  );
}

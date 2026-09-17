import {
  STANDARD_WEBHOOKS_SPEC_URL,
  STANDARD_WEBHOOKS_URL,
  VERIFY_BODY,
  VERIFY_EXAMPLE,
  VERIFY_HEADERS,
  VERIFY_INTRO,
  VERIFY_LIBRARY_LEAD,
  VERIFY_LIBRARY_LINK,
  VERIFY_LIBRARY_TAIL,
  VERIFY_SPEC_LEAD,
  VERIFY_SPEC_LINK,
} from "./copy";

const LINK_CLASS = "underline hover:text-foreground";

/** How a receiver checks a delivery: the headers it gets, the recipe,
 * and one worked example. Rendered where the secret is revealed and
 * again on the detail page, where the secret is no longer available. */
export function VerifyGuide({ heading = true }: { heading?: boolean }) {
  return (
    <section className="space-y-3">
      {heading && <h3 className="text-sm font-semibold text-foreground">Verifying deliveries</h3>}
      <p className="text-sm text-muted">{VERIFY_INTRO}</p>
      <ul className="space-y-1 text-sm text-muted">
        {VERIFY_HEADERS.map((header) => (
          <li key={header.name}>
            <code className="font-mono text-xs text-foreground">{header.name}</code>
            <span> {header.meaning}</span>
          </li>
        ))}
      </ul>
      <pre className="overflow-x-auto rounded-md bg-wash p-3 font-mono text-xs text-foreground">{VERIFY_EXAMPLE}</pre>
      <p className="text-sm text-muted">{VERIFY_BODY}</p>
      <p className="text-xs text-muted">
        {VERIFY_SPEC_LEAD}
        <a href={STANDARD_WEBHOOKS_SPEC_URL} target="_blank" rel="noreferrer" className={LINK_CLASS}>
          {VERIFY_SPEC_LINK}
        </a>
        {VERIFY_LIBRARY_LEAD}
        <a href={STANDARD_WEBHOOKS_URL} target="_blank" rel="noreferrer" className={LINK_CLASS}>
          {VERIFY_LIBRARY_LINK}
        </a>
        {VERIFY_LIBRARY_TAIL}
      </p>
    </section>
  );
}

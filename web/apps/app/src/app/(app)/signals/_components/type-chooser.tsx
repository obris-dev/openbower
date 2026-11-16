import Link from "next/link";
import { CUSTOM_SIGNAL_TYPE, webRoutes, type KnownSignalType, type SignalsCatalog } from "@bower/api";

import { CUSTOM_CHOICE_DESCRIPTION, CUSTOM_TYPE_LABEL } from "./copy";

/** How each hosted type this bundle can offer reads as a choice. Keyed by
 * the bundle's known types, so a type it learns is a compile error here
 * until it has words. */
const HOSTED_CHOICE: Record<KnownSignalType, { title: string; description: string }> = {
  funding: { title: "Funding rounds", description: "Know when a company you'd sell to raises a round." },
};

type Choice = { key: string; title: string; description: string; href: string | null; note?: string };

/** Create signal's first question: what the signal collects. One tile per
 * hosted type the catalog offers, then Custom; each opens that type's own
 * form. A catalog that could not be read leaves the hosted tiles shown but
 * closed, with the read's own message (the server's words for a refusal,
 * the request funnel's general line otherwise), and Custom open (it needs
 * no catalog). */
export function TypeChooser(
  props: { catalog: SignalsCatalog; failure?: never } | { catalog: null; failure: string },
) {
  const { catalog } = props;
  const hosted: Choice[] =
    catalog === null
      ? Object.entries(HOSTED_CHOICE).map(([key, words]) => ({ key, ...words, href: null, note: props.failure }))
      : catalog.types.map((offered) => ({
          key: offered.type,
          ...HOSTED_CHOICE[offered.type],
          href: webRoutes.signalNewOf(offered.type),
        }));
  const choices: Choice[] = [...hosted, {
    key: CUSTOM_SIGNAL_TYPE,
    title: CUSTOM_TYPE_LABEL,
    description: CUSTOM_CHOICE_DESCRIPTION,
    href: webRoutes.signalNewOf(CUSTOM_SIGNAL_TYPE),
  }];

  return (
    <ul className="grid gap-4 sm:grid-cols-2 lg:max-w-4xl">
      {choices.map((choice) => (
        <li key={choice.key}>
          {choice.href === null ? (
            <div aria-disabled className="h-full rounded-lg border border-hairline bg-surface p-5">
              <p className="text-base font-semibold text-faint">{choice.title}</p>
              <p className="mt-1 text-sm text-faint">{choice.description}</p>
              {choice.note && <p className="mt-3 text-xs text-warning">{choice.note}</p>}
            </div>
          ) : (
            <Link
              href={choice.href}
              className="block h-full rounded-lg border border-hairline bg-surface p-5 shadow transition-colors hover:border-edge focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-signal-600"
            >
              <p className="text-base font-semibold text-foreground">{choice.title}</p>
              <p className="mt-1 text-sm text-muted">{choice.description}</p>
            </Link>
          )}
        </li>
      ))}
    </ul>
  );
}

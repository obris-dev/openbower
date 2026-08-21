import Link from "next/link";

export type Crumb = { label: string; href: string };

/** The back trail above a detail page's title. One parent renders the
 * back affordance (‹ Agents); deeper nests join with slashes, the
 * chevron staying on the first hop, so every crumb is a link and the
 * trail grows without the consumer changing shape. App-level (not
 * @bower/ui): navigation owns next/link, and the ui package stays
 * framework-free. */
export function Breadcrumbs({ trail }: { trail: Crumb[] }) {
  return (
    <nav aria-label="Breadcrumb" className="text-xs font-semibold uppercase tracking-wide text-faint">
      <ol className="flex items-center gap-1.5">
        {trail.map((crumb, index) => (
          <li key={crumb.href} className="flex items-center gap-1.5">
            <span aria-hidden>{index === 0 ? "‹" : "/"}</span>
            <Link href={crumb.href} className="hover:text-foreground">
              {crumb.label}
            </Link>
          </li>
        ))}
      </ol>
    </nav>
  );
}

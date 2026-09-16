import Link from "next/link";
import { ChevronRight, CircleUser, Webhook } from "lucide-react";
import { Card } from "@bower/ui";
import { webRoutes } from "@bower/api";

const SECTIONS = [
  {
    title: "Account",
    description: "Who is signed in, and the account id.",
    href: webRoutes.settingsAccount,
    icon: CircleUser,
  },
  {
    title: "Webhooks",
    description: "Destinations that receive signed deliveries from your sheets.",
    href: webRoutes.settingsWebhooks,
    icon: Webhook,
  },
];

/** The settings hub: one card per section, reached from the user
 * menu. Sections land here as their screens are built. */
export default function SettingsPage() {
  return (
    <div className="p-6">
      <div className="mx-auto w-full max-w-3xl space-y-6 pb-24 pt-2">
        <div>
          <h1 className="text-2xl font-bold text-foreground">Settings</h1>
          <p className="mt-1 text-sm text-muted">The account, and what it connects to.</p>
        </div>
        <Card className="divide-y divide-hairline p-0">
          {SECTIONS.map((section) => (
            <Link
              key={section.href}
              href={section.href}
              className="flex items-center gap-4 px-5 py-4 text-left hover:bg-wash"
            >
              <section.icon aria-hidden className="h-5 w-5 shrink-0 text-muted" />
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-medium text-foreground">{section.title}</span>
                <span className="block text-xs text-muted">{section.description}</span>
              </span>
              <ChevronRight aria-hidden className="h-4 w-4 shrink-0 text-faint" />
            </Link>
          ))}
        </Card>
      </div>
    </div>
  );
}

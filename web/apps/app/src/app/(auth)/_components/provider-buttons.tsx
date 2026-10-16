import { AUTH_PROVIDERS, type AuthProvider, authProviders, providerStartUrl } from "@bower/api";
import { Button, GitHubMark, GoogleMark } from "@bower/ui";

const MARKS: Record<AuthProvider, typeof GoogleMark> = { google: GoogleMark, github: GitHubMark };

/** One button per provider this build offers, above the credentials
 * form, then the divider. Each is a full navigation to the identity
 * service's start endpoint (the auth boundary rule), carrying the
 * in-flight authorize so the round trip resumes it. Renders nothing
 * when no provider is configured, so the card is the plain form. */
export function ProviderButtons({ next }: { next: string | null }) {
  const providers = authProviders();
  if (providers.length === 0) return null;
  return (
    <>
      <div className="space-y-3">
        {providers.map((provider) => {
          const Mark = MARKS[provider];
          return (
            <Button key={provider} href={providerStartUrl(provider, next)} variant="outline" fullWidth>
              <Mark className="mr-2" />
              {AUTH_PROVIDERS[provider].label}
            </Button>
          );
        })}
      </div>
      <ProviderDivider />
    </>
  );
}

export function ProviderDivider() {
  return (
    <div className="my-5 flex items-center gap-3 text-xs text-faint" aria-hidden>
      <span className="flex-1 border-t border-hairline" />
      or
      <span className="flex-1 border-t border-hairline" />
    </div>
  );
}

import { GithubLink } from "./GithubLink";
import { WaitlistForm } from "./WaitlistForm";

const CARD = "rounded-xl border border-ink/10 bg-paper p-6 dark:border-paper/10 dark:bg-ink";
const LINK =
  "mt-6 inline-flex items-center gap-1.5 text-sm font-semibold text-ink/80 transition-colors hover:text-ink dark:text-paper/80 dark:hover:text-paper";

/** The model in two cards: free to own, a fee to have it run. The fee's
 * number and shape stay off the page until they are set; the card names
 * what the fee is FOR, and takes the waitlist since hosted is not open. */
export function PricingSection() {
  return (
    <section id="pricing" className="reveal px-6 py-16">
      <div className="mx-auto max-w-5xl">
        <h2 className="font-display text-4xl tracking-tight text-ink sm:text-5xl dark:text-paper">
          Free to prove. Pay to scale.
        </h2>
        <p className="mt-4 max-w-2xl text-lg text-ink/60 dark:text-paper/60">
          Getting your idea in front of potential users should not be pay to play. Self-host the whole thing free forever, or
          just while you prove it. The only costs are your vendors&rsquo;, and they grow when you do.
        </p>

        <div className="mt-12 grid gap-6 sm:grid-cols-2">
          <div className={CARD}>
            <p className="text-sm font-medium uppercase tracking-wide text-ink/50 dark:text-paper/50">Self-host</p>
            <p className="mt-2 font-display text-3xl text-ink dark:text-paper">Free</p>
            <p className="mt-4 text-base leading-relaxed text-ink/60 dark:text-paper/60">
              The whole engine, run as your own GTM infrastructure on your laptop or your server. Your models, your
              keys, your vendor accounts.
            </p>
            <GithubLink iconClassName="h-4 w-4" className={LINK}>
              Get the repo
            </GithubLink>
          </div>

          <div className={CARD}>
            <p className="text-sm font-medium uppercase tracking-wide text-ink/50 dark:text-paper/50">Hosted</p>
            <p className="mt-2 font-display text-3xl text-ink dark:text-paper">A hosting fee</p>
            <p className="mt-4 text-base leading-relaxed text-ink/60 dark:text-paper/60">
              We run the engine for you: always on, listening for your GTM signals.
            </p>
            <div className="mt-6">
              <WaitlistForm source="pricing" />
            </div>
          </div>
        </div>

      </div>
    </section>
  );
}

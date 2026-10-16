import type { Metadata } from "next";
import Link from "next/link";

import { LegalPage, Mail, Section } from "../_components/LegalPage";

export const metadata: Metadata = {
  title: "Privacy policy",
  description: "What OpenBower collects, what it does with it, and what it never does.",
};

export default function PrivacyPage() {
  return (
    <LegalPage title="Privacy policy" effective="October 16, 2026">
      <Section title="1. Who this covers">
        <p>
          OpenBower is operated by Obris LLC (&quot;we&quot;). This policy covers the website at openbower.ai, the
          hosted app at app.openbower.ai, and the account service at auth.openbower.ai that signs you into both
          (together, the &quot;Service&quot;).
        </p>
        <p>
          A self-hosted install of OpenBower is your own. It runs on your machine, and we receive nothing from it
          except the sign-in described below. Everything else it holds, your sheets, your keys, your results, stays
          with you.
        </p>
      </Section>

      <Section title="2. What we collect">
        <p>
          <strong>Your account.</strong> Your email address and, if you choose a password, a hash of it. If you sign in
          with Google or GitHub, the stable identifier that provider assigns you and the email address it reports as
          verified. We never see your password at the provider.
        </p>
        <p>
          <strong>Your content in the hosted app.</strong> The sheets, rows, columns, agent configurations and webhook
          settings you create there.
        </p>
        <p>
          <strong>Keys you add.</strong> API keys for the model and data providers you connect are stored to run your
          own work and nothing else. We never pool them, share what they return with other accounts, or build anything
          on what a provider returned through your key.
        </p>
        <p>
          <strong>Operational records.</strong> Request logs with IP address, browser type and timestamps, kept to
          operate and secure the Service.
        </p>
        <p>
          <strong>Updates you ask for.</strong> The email address you give us to hear from us, and nothing else about
          you.
        </p>
      </Section>

      <Section title="3. How we use it">
        <p>
          To sign you in, run the research you ask for, deliver the webhooks you configure, keep the Service working,
          and reply when you write to us. We do not sell your information and we do not use your content to train
          models.
        </p>
      </Section>

      <Section title="4. Where your rows go">
        <p>
          When an agent fills a column, the rows it works on are sent to the model and search providers you have
          configured, under your keys and their terms. The hosted company universe behind look-alike discovery is
          built from data licensed to us; nothing you add to it comes from another user&apos;s keys.
        </p>
      </Section>

      <Section title="5. Who else sees it">
        <p>
          The infrastructure providers that host the Service and deliver its email, under contracts that limit them to
          providing that service to us; and authorities when the law requires it. No one else.
        </p>
      </Section>

      <Section title="6. Cookies">
        <p>
          The Service sets the cookies sign-in needs: a session cookie at auth.openbower.ai and one at the app. Your
          theme preference and a remembered email live in your browser alone. There is no advertising or cross-site
          tracking.
        </p>
      </Section>

      <Section title="7. Keeping and deleting">
        <p>
          We keep your account and content for as long as you use the Service. Write to{" "}
          <Mail address="privacy@openbower.ai" /> to delete your account; we remove your content and sign-in records
          within 30 days, and operational logs age out on their own.
        </p>
      </Section>

      <Section title="8. Security">
        <p>
          Traffic to the Service is encrypted in transit. Passwords are stored as salted hashes, and the tokens that
          sign you into the hosted app are encrypted at rest. Report a concern to{" "}
          <Mail address="security@openbower.ai" />.
        </p>
      </Section>

      <Section title="9. Your rights">
        <p>
          You can see, correct, export or delete what we hold about you. Where the law gives you rights beyond these,
          you have them. Write to <Mail address="privacy@openbower.ai" />.
        </p>
      </Section>

      <Section title="10. Children">
        <p>
          The Service is not for anyone under 13, and we do not knowingly collect their information. If we learn that
          we have, we delete it.
        </p>
      </Section>

      <Section title="11. Changes">
        <p>
          When this policy changes, the date at the top changes with it. A change that matters to you is announced in
          the app before it takes effect.
        </p>
      </Section>

      <Section title="12. Contact">
        <p>
          <Mail address="privacy@openbower.ai" /> for anything about this policy. The{" "}
          <Link href="/terms" className="text-signal hover:underline">
            terms of service
          </Link>{" "}
          cover the rest.
        </p>
      </Section>
    </LegalPage>
  );
}

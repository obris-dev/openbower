import type { Metadata } from "next";
import Link from "next/link";

import { GITHUB_URL } from "../_lib/urls";
import { LegalPage, Mail, Section } from "../_components/LegalPage";

export const metadata: Metadata = {
  title: "Terms of service",
  description: "The terms for the hosted OpenBower service and the open-source software behind it.",
};

export default function TermsPage() {
  return (
    <LegalPage title="Terms of service" effective="October 16, 2026">
      <Section title="1. What these terms cover">
        <p>
          These terms are between you and Obris LLC (&quot;we&quot;), the operator of OpenBower, and cover the website
          at openbower.ai, the hosted app at app.openbower.ai and the account service at auth.openbower.ai (together,
          the &quot;Service&quot;). Using the Service means you accept them.
        </p>
        <p>
          The OpenBower software itself is open source under the Apache License, Version 2.0, in{" "}
          <a href={GITHUB_URL} className="text-signal hover:underline" target="_blank" rel="noreferrer noopener">
            its repository
          </a>
          . A self-hosted install is governed by that license, not by these terms.
        </p>
      </Section>

      <Section title="2. The Service">
        <p>
          OpenBower is a spreadsheet for prospecting whose columns are filled by AI research agents you configure. The
          hosted app runs that for you on our infrastructure. Access to it is granted per account; an account you
          create on your own install signs into the hosted app only once it has been granted.
        </p>
      </Section>

      <Section title="3. Your account">
        <p>
          You must be 13 or older. You are responsible for what happens under your account and for keeping its
          credentials to yourself; tell us at <Mail address="security@openbower.ai" /> if you think someone else has
          them.
        </p>
      </Section>

      <Section title="4. Your keys and the providers behind them">
        <p>
          The model, search and data providers you connect are your relationships, under their terms and at their
          prices. What a provider returns through your key is licensed to you and stays in your account; we never pool
          it or pass it to anyone else.
        </p>
      </Section>

      <Section title="5. Acceptable use">
        <p>You agree not to use the Service to:</p>
        <ul className="list-disc space-y-2 pl-6">
          <li>break the law, or contact people in ways the law or their consent does not allow;</li>
          <li>collect or process personal data you have no right to;</li>
          <li>interfere with the Service, probe it for weaknesses without permission, or evade its limits;</li>
          <li>resell access to the hosted app or present its output as your own product.</li>
        </ul>
      </Section>

      <Section title="6. Your content">
        <p>
          Your sheets, rows and configurations are yours. You give us only the permission needed to store them and run
          the work you ask for. You are responsible for having the right to the data you bring in and for what you do
          with what comes out.
        </p>
      </Section>

      <Section title="7. Our content">
        <p>
          The hosted company universe behind look-alike discovery, and the Service&apos;s name and marks, are ours. You
          may use the universe through the Service for your own prospecting and not to rebuild or redistribute it.
        </p>
      </Section>

      <Section title="8. Availability and changes">
        <p>
          The hosted app is early. We change it often, and we may suspend or withdraw it. We give notice in the app for
          changes that affect how you use it, and you can export your sheets at any time.
        </p>
      </Section>

      <Section title="9. Disclaimers">
        <p>
          The Service is provided as is. Research agents are wrong sometimes, and sources can be stale; check what
          matters before you act on it. We do not promise that the Service will be uninterrupted or error free.
        </p>
      </Section>

      <Section title="10. Limitation of liability">
        <p>
          To the fullest extent the law allows, we are not liable for indirect or consequential loss, lost profits or
          lost data arising from the Service, and our total liability to you is limited to the amount you paid us for
          the Service in the twelve months before the claim, or one hundred US dollars if you paid nothing.
        </p>
      </Section>

      <Section title="11. Indemnification">
        <p>
          You will defend and indemnify us against claims arising from your content, your use of the Service, or your
          breach of these terms.
        </p>
      </Section>

      <Section title="12. Ending the agreement">
        <p>
          You can leave at any time by asking us to delete your account at <Mail address="privacy@openbower.ai" />.
          We can suspend or end an account that breaks these terms. Sections 6, 7, 9, 10 and 11 survive.
        </p>
      </Section>

      <Section title="13. Governing law">
        <p>
          These terms are governed by the laws of the Commonwealth of Massachusetts, without regard to its conflict of
          law rules. Nothing in them limits rights you have under the law where you live.
        </p>
      </Section>

      <Section title="14. Changes to these terms">
        <p>
          When these terms change, the date at the top changes with it, and a change that matters to you is announced
          in the app before it takes effect. Using the Service after that is acceptance.
        </p>
      </Section>

      <Section title="15. Contact">
        <p>
          <Mail address="support@openbower.ai" /> for anything about these terms. How we handle your information is in
          the{" "}
          <Link href="/privacy" className="text-signal hover:underline">
            privacy policy
          </Link>
          .
        </p>
      </Section>
    </LegalPage>
  );
}

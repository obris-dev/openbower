import { AccountDetails } from "./_components/account-details";

/** The account section: read-only facts about who is signed in. */
export default function AccountSettingsPage() {
  return (
    <div className="p-6">
      <div className="mx-auto w-full max-w-3xl space-y-6 pb-24 pt-2">
        <AccountDetails />
      </div>
    </div>
  );
}

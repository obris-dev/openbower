import { API_VERSION, AUTH_URL } from "./urls";

// The hosted waitlist post, to the identity provider (the waitlist is a
// pre-account, so it lives where accounts do). Public and cookie-less,
// so it is a bare fetch that reports {ok, status} rather than the app's
// credentialed request funnel; the endpoint is idempotent on the
// address. Both services version their API under the same prefix.

export interface WaitlistResult {
  ok: boolean;
  status: number;
}

export async function submitWaitlist(email: string, source: string): Promise<WaitlistResult> {
  const res = await fetch(`${AUTH_URL}/${API_VERSION}/waitlist`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, source }),
  });
  return { ok: res.ok, status: res.status };
}

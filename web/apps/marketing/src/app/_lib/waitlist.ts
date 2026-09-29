import { API_URL, API_VERSION } from "./urls";

// The hosted waitlist post. Public and cookie-less, so it is a bare
// fetch that reports {ok, status} rather than the app's credentialed
// request funnel; the endpoint is idempotent on the address.

export interface WaitlistResult {
  ok: boolean;
  status: number;
}

export async function submitWaitlist(email: string, source: string): Promise<WaitlistResult> {
  const res = await fetch(`${API_URL}/${API_VERSION}/waitlist`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, source }),
  });
  return { ok: res.ok, status: res.status };
}

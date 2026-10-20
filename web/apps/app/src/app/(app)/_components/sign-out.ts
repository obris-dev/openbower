import { logout, webRoutes } from "@bower/api";

/** End the session everywhere: the API revokes the app session and
 * clears its cookie, then a full document load to the IdP's logout
 * ends the identity session and lands back on the app. Without the
 * second hop the IdP stays signed in and the next sign-in silently
 * re-issues the same account. Callers must not clear local auth state
 * before this navigates (the auth guard would race it with a login
 * redirect that the still-alive IdP session auto-approves). */
export async function signOut(): Promise<void> {
  const idpLogoutUrl = await logout();
  window.location.href = idpLogoutUrl ?? webRoutes.home;
}

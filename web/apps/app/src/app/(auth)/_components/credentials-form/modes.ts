import { idpLogin, idpSignup, webRoutes } from "@bower/api";

import type { Mode, ModeName } from "./types";

// The per-screen config lives WITH the component (pages are server
// components and cannot pass the action function across the boundary;
// they name a mode instead). A future forgot-password screen is another
// entry here plus its route. The fallback reads the same record, so the
// skeleton cannot drift from the form it stands in for.
export const MODES: Record<ModeName, Mode> = {
  login: {
    subtitle: "Sign in to continue.",
    action: idpLogin,
    submitLabel: "Sign in",
    busyLabel: "Signing in…",
    passwordAutoComplete: "current-password",
    confirmPassword: false,
    rememberEmail: true,
    footer: { prompt: "Don't have an account?", label: "Create account", href: webRoutes.signup },
  },
  signup: {
    subtitle: "Create your account.",
    action: idpSignup,
    submitLabel: "Create account",
    busyLabel: "Creating account…",
    passwordAutoComplete: "new-password",
    confirmPassword: true,
    rememberEmail: false,
    footer: { prompt: "Already have an account?", label: "Sign in", href: webRoutes.login },
  },
};

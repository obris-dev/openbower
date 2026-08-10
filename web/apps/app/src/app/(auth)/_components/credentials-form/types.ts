/** The two signed-out screens share one form; a mode names which. */
export type ModeName = "login" | "signup";

/** The cross-link under the form (login <-> signup); `href` is a
 * webRoutes path, and the in-flight ?next rides along automatically. */
export type CredentialsFooter = {
  prompt: string;
  label: string;
  href: string;
};

export type Mode = {
  subtitle: string;
  action: (email: string, password: string) => Promise<string | null>;
  submitLabel: string;
  busyLabel: string;
  passwordAutoComplete: "current-password" | "new-password";
  confirmPassword: boolean;
  rememberEmail: boolean;
  footer: CredentialsFooter;
};

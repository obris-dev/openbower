// Single source of truth for the theme contract, shared by the provider,
// the toggle, and the blocking head script. Kept dependency-free so the
// head script can inline these literals safely.

export const THEME_COOKIE_NAME = "openbower-theme";
export const THEME_STORAGE_KEY = "theme";

export const THEME = {
  LIGHT: "light",
  DARK: "dark",
  SYSTEM: "system",
} as const;

export type Theme = (typeof THEME)[keyof typeof THEME];

// The two explicit (non-system) themes the toggle cycles between.
export const EXPLICIT_THEMES: Theme[] = [THEME.LIGHT, THEME.DARK];

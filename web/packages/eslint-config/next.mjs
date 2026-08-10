// Shared ESLint flat config for the Next app(s). `eslint-config-next` ships a
// native flat-config array (no FlatCompat shim); core-web-vitals is the
// app-facing preset (next + next/typescript + the Core Web Vitals rules).
import next from "eslint-config-next/core-web-vitals";

const config = [{ ignores: [".next/**", "node_modules/**"] }, ...next];

export default config;

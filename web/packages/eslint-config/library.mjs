// Shared ESLint flat config for the @bower/* library packages (ui, auth,
// api, schema). Built on typescript-eslint's recommended rules plus the
// React Hooks rules; the hooks rules simply do not fire in the non-React
// packages, so one baseline covers all of them. The Next app extends the
// separate ./next preset (it needs the next plugin); this is the library
// baseline with no framework assumptions.
import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist/**", "node_modules/**"] },
  ...tseslint.configs.recommended,
  reactHooks.configs.flat.recommended,
);

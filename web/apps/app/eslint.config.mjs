// The Next app's lint config lives in the shared @bower/eslint-config package
// (its ./next preset), so all workspace lint config has one home. Next 16
// removed the `next lint` subcommand; ESLint 9 is invoked directly (`eslint .`).
import config from "@bower/eslint-config/next";

export default config;

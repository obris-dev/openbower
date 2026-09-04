// @bower/api: the typed client, one module per domain.
// Relative imports carry explicit .ts extensions ON PURPOSE: the
// package runs under plain node for its test lane, whose ESM loader
// resolves nothing extensionless. Bundlers resolve them identically;
// do not normalize them away.
// The transport funnel stays internal; consumers get the outcome type
// and the probe primitive, nothing else.
export { fetchJson, GENERIC_FAILURE } from "./request.ts";
export type { ApiResult } from "./request.ts";
export * from "./domains.ts";
export * from "./agents.ts";
export * from "./fills.ts";
// Explicit, not a star: buildApiFetchUrl carries the container-internal
// origin and only the fetch funnel inside this package may reach it, so
// it is deliberately absent from the public surface.
export {
  apiRoutes,
  authRoutes,
  buildApiUrl,
  buildAuthUrl,
  resolveApiBase,
  resolveAuthBase,
  webRoutes,
  withNext,
} from "./routes.ts";
export * from "./auth.ts";
export * from "./discover.ts";
export * from "./lists.ts";

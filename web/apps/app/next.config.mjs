import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // The production image runs the self-contained server `next build`
  // emits (server.js plus the traced node_modules), not a workspace
  // install. The tracing root is pinned to the pnpm workspace, where
  // the lock and the .pnpm store live; left to inference, a stray
  // lockfile above the checkout would re-root the trace.
  output: "standalone",
  outputFileTracingRoot: path.join(here, "../.."),
  // Every workspace package the app imports must be listed, or `next
  // build` ships it untranspiled and its env-derived origins resolve
  // undefined in production (dev hides this behind localhost fallbacks).
  transpilePackages: ["@bower/ui", "@bower/api", "@bower/auth", "@bower/schema"],
};
export default nextConfig;

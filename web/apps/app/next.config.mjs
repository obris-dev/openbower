/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Every workspace package the app imports must be listed, or `next
  // build` ships it untranspiled and its env-derived origins resolve
  // undefined in production (dev hides this behind localhost fallbacks).
  transpilePackages: ["@bower/ui", "@bower/api", "@bower/auth", "@bower/schema"],
};
export default nextConfig;

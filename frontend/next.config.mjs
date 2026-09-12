/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,

  // The container build is not the type gate: `npx tsc --noEmit` (strict, per
  // tsconfig.json) is, so a type error surfaces in review rather than blocking
  // `docker compose up` for everyone.
  typescript: { ignoreBuildErrors: true },
};

export default nextConfig;

/** @type {import('next').NextConfig} */
const nextConfig = {
  // Disabled in dev to prevent double WebSocket connections / duplicate analysis sessions
  reactStrictMode: false,
};

export default nextConfig;

/** @type {import('next').NextConfig} */
const nextConfig = {
  // Produce a minimal, self-contained server bundle for tiny Docker images.
  output: 'standalone',
  reactStrictMode: true,
  poweredByHeader: false,
  // Security headers applied at the framework edge (ingress should add HSTS/TLS).
  async headers() {
    return [
      {
        source: '/(.*)',
        headers: [
          { key: 'X-Content-Type-Options', value: 'nosniff' },
          { key: 'X-Frame-Options', value: 'DENY' },
          { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
          { key: 'Permissions-Policy', value: 'camera=(), microphone=(), geolocation=()' },
        ],
      },
    ];
  },
};

export default nextConfig;

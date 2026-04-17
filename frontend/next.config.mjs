/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
  webpack: (config) => {
    // konva tries to `require('canvas')` on the server for node-side
    // rendering; we only use it in client components, so tell webpack to
    // stub it out instead of installing the native canvas bindings.
    config.resolve.alias = {
      ...(config.resolve.alias ?? {}),
      canvas: false,
    };
    return config;
  },
};

export default nextConfig;

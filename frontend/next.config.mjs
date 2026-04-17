/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
  webpack: (config) => {
    // konva transitively `require('canvas')` from `index-node.js` so it can
    // render on node-canvas when present.  We never do node-canvas work
    // (client components only, wrapped in `dynamic({ ssr: false })`), so
    // tell webpack to treat the missing module as `false` instead of
    // erroring the build.  Using `resolve.fallback` rather than an outright
    // alias means any future code that *does* install and need node-canvas
    // will still resolve to the real package.
    config.resolve.fallback = {
      ...(config.resolve.fallback ?? {}),
      canvas: false,
    };
    return config;
  },
};

export default nextConfig;

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
  // Pull `content/docs/**/*.md` into the standalone bundle so the
  // /dashboard/docs route can fs.readFile them at request time in
  // production.  Without this, the runtime image ships without the
  // markdown source and every docs page 404s.
  experimental: {
    outputFileTracingIncludes: {
      "/dashboard/docs/**": ["./content/docs/**/*.md"],
    },
  },
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

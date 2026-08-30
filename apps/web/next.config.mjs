/**
 * The board lives under /v2 while the Python board keeps serving /.
 *
 * `basePath` rather than a route group, because every asset URL has to carry
 * the prefix too. A route group would put the pages under /v2 and leave
 * /_next/* at the root, which works on a dev server and 404s behind any
 * path-based routing in front of it. That is the same shape as the favicon
 * the old renderer read from deploy/public/: correct locally, absent in the
 * deployment, and invisible until it is live.
 */
const nextConfig = {
  basePath: "/v2",
  // Standalone output exists here for the deployed-shape check rather than for
  // deployment: it runs the same @vercel/nft tracer Vercel runs, so a file the
  // server reads at request time and that the tracer does not follow is absent
  // from the standalone tree exactly as it is absent from the lambda. That is
  // the local reproduction of the outage where the renderer read a favicon out
  // of deploy/public/, which every laptop has and the serverless bundle does
  // not. It is a close reproduction, not the identical pipeline, and the check
  // that uses it says so.
  output: "standalone",
  reactStrictMode: true,
  poweredByHeader: false,
  // The board reads Postgres directly from server components; `pg` must stay
  // on the server side of the bundle.
  serverExternalPackages: ["pg"],
};

export default nextConfig;

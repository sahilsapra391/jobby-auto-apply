/**
 * The board is its own Vercel project on its own domain, and that is why there
 * is no basePath here. It was built beside the original Python board on a
 * separate domain so neither could break the other. A basePath to share a
 * domain was considered and rejected: it put one project's config on the
 * other's critical path. Do not reintroduce one.
 */
const nextConfig = {
  // Standalone output is for the deployed-shape check, not for deployment,
  // and it is opt-in so the build Vercel runs is a stock Next build. The check
  // needs it because it runs the same @vercel/nft tracer Vercel runs, so a
  // file the server reads at request time that the tracer does not follow is
  // absent from the standalone tree exactly as it is absent from the lambda --
  // the local reproduction of an outage where a server read a static file
  // that every laptop has and the serverless bundle does not.
  //
  // Behind a flag rather than always on, because whether Vercel's own builder
  // honours, ignores or trips over `output: "standalone"` is a question I
  // could not answer from the documentation, and the first deploy of a new
  // project is the worst place to find out. The check sets the variable
  // itself; nothing else needs to know.
  ...(process.env.JOBBY_STANDALONE_BUILD ? { output: "standalone" } : {}),
  reactStrictMode: true,
  poweredByHeader: false,
  // The board reads Postgres directly from server components; `pg` must stay
  // on the server side of the bundle.
  serverExternalPackages: ["pg"],
};

export default nextConfig;

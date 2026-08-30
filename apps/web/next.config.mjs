/**
 * The board is its own project on its own domain, v2.jobby.fyi, and that is
 * why there is no basePath here.
 *
 * The plan's coexistence table read `jobby.fyi/v2/ -> apps/web` as though it
 * were a routing table. A Vercel domain maps to exactly one project, and
 * jobby.fyi maps to jobby-board rooted at deploy/, so serving a path off it
 * from a second project needs either a proxy rewrite out of the Python board's
 * own vercel.json or Vercel's microfrontends routing. The requirement behind
 * the path was isolation -- neither board able to break the other -- and a
 * rewrite delivers the path by putting the Python board's config on the new
 * board's critical path, which is the coupling the requirement was written to
 * prevent. A separate domain delivers the isolation structurally: neither
 * project's configuration mentions the other, and there is nothing to contend
 * over.
 *
 * Do not reintroduce basePath to chase the /v2/ path string. See
 * docs/plans/phase2-live-board.md, which records the rejection.
 */
const nextConfig = {
  // Standalone output is for the deployed-shape check, not for deployment,
  // and it is opt-in so the build Vercel runs is a stock Next build. The check
  // needs it because it runs the same @vercel/nft tracer Vercel runs, so a
  // file the server reads at request time that the tracer does not follow is
  // absent from the standalone tree exactly as it is absent from the lambda --
  // the local reproduction of the outage where the renderer read a favicon out
  // of deploy/public/, which every laptop has and the serverless bundle does
  // not.
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

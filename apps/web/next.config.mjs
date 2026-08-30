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

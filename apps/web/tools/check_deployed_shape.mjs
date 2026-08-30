#!/usr/bin/env node
/**
 * What CI owes /v2/ before anything depends on it.
 *
 * `scripts/sync_vercel_renderer.py` guards the Python board against two
 * hazards, and CLAUDE.md records what each cost:
 *
 *   1. A stale bundle. Three commits of UI work sat merged and invisible while
 *      the deployment served an older copy.
 *   2. An asset the renderer reads that the deployment does not carry. The
 *      favicon lived in deploy/public/, which Vercel serves at the edge and
 *      leaves out of the function, so it resolved on every laptop and raised
 *      FileNotFoundError on every deployed request. The board showed "The
 *      board state could not be rendered." for two days while local renders
 *      and the full test suite stayed green.
 *
 * A Next.js app has both in different clothes. The first is narrower here,
 * because nothing is checked in for a copy to drift from: what can go stale is
 * the lockfile against package.json, so a clean-checkout install produces a
 * tree CI never built. The second is identical in kind.
 *
 * So this builds from a clean install, serves the STANDALONE output rather
 * than the working tree, and asserts against what that server actually emits.
 * Standalone runs the same @vercel/nft tracer Vercel runs, so a file the
 * server reads at request time that the tracer does not follow is missing from
 * it for the same reason it is missing from the lambda. It is a close
 * reproduction of the deployment, not the identical pipeline, and the
 * difference is worth naming: it proves tracing, not Vercel's routing layer.
 *
 *   node tools/check_deployed_shape.mjs            build, serve, check
 *   node tools/check_deployed_shape.mjs --no-build reuse an existing build
 */
import { spawn, execFileSync } from "node:child_process";
import { existsSync, cpSync, rmSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const WEB = dirname(dirname(fileURLToPath(import.meta.url)));
const BASE_PATH = "/v2";
/** Every route the app serves. A page absent from here is a page unchecked. */
const ROUTES = ["/v2", "/v2/settings"];
const PORT = 3199;

const problems = [];
const notes = [];
function fail(msg) { problems.push(msg); }
function note(msg) { notes.push(msg); }

// ---------------------------------------------------------------- build

if (!process.argv.includes("--no-build")) {
  note("building");
  execFileSync("npm", ["run", "build"], { cwd: WEB, stdio: "inherit" });
}

const standalone = join(WEB, ".next", "standalone");
if (!existsSync(standalone)) {
  console.error("no standalone output; is `output: \"standalone\"` still set?");
  process.exit(1);
}

// ------------------------------------------------- isolate from the tree
//
// The whole point. Copy the standalone server somewhere with no source tree
// and no node_modules above it, so anything it reaches for that the tracer did
// not carry is as absent as it would be in the deployment. Serving from .next/
// in place would find those files on disk and report healthy.

const isolated = mkdtempSync(join(tmpdir(), "v2-deployed-shape-"));
cpSync(standalone, isolated, { recursive: true });
// Static chunks and public/ are edge-served by Vercel and deliberately left
// out of the traced server, so they are placed the way Vercel places them.
cpSync(join(WEB, ".next", "static"), join(isolated, ".next", "static"),
       { recursive: true });
if (existsSync(join(WEB, "public"))) {
  cpSync(join(WEB, "public"), join(isolated, "public"), { recursive: true });
}

// ---------------------------------------------------------------- serve

const server = spawn(process.execPath, [join(isolated, "server.js")], {
  cwd: isolated,
  env: { ...process.env, PORT: String(PORT), HOSTNAME: "127.0.0.1",
         NODE_ENV: "production" },
  stdio: ["ignore", "pipe", "pipe"],
});
// Never DEVNULL. The failure that matters here explains itself once, in the
// server's own stderr, at the moment it dies.
let serverLog = "";
server.stdout.on("data", (d) => { serverLog += d; });
server.stderr.on("data", (d) => { serverLog += d; });

async function ready(deadlineMs = 30000) {
  const until = Date.now() + deadlineMs;
  while (Date.now() < until) {
    try {
      await fetch(`http://127.0.0.1:${PORT}${BASE_PATH}`, { redirect: "manual" });
      return true;
    } catch {
      await new Promise((r) => setTimeout(r, 250));
    }
  }
  return false;
}

function done(code) {
  server.kill("SIGTERM");
  rmSync(isolated, { recursive: true, force: true });
  process.exit(code);
}

if (!(await ready())) {
  console.error("the isolated server never answered. Its own output:\n" + serverLog);
  done(1);
}

// ---------------------------------------------------------------- check

const seen = new Set();

for (const route of ROUTES) {
  const res = await fetch(`http://127.0.0.1:${PORT}${route}`);
  if (!res.ok) {
    fail(`${route} answered ${res.status} from the traced bundle. ` +
         `A route that renders from the working tree and fails here is ` +
         `reading something the deployment will not carry.\n${serverLog}`);
    continue;
  }
  const html = await res.text();

  // Hazard 2, and the reason basePath is used rather than a route group: an
  // absolute URL without the prefix resolves against a dev server sitting at
  // the root and 404s behind any path-based routing.
  for (const m of html.matchAll(/(?:href|src)="(\/[^"]*)"/g)) {
    const url = m[1];
    if (!url.startsWith(BASE_PATH + "/") && url !== BASE_PATH) {
      fail(`${route} references ${url}, which does not carry ${BASE_PATH}. ` +
           `It resolves in dev and 404s deployed.`);
      continue;
    }
    seen.add(url);
  }
}

// Every referenced asset actually answers, from the isolated tree.
for (const url of [...seen].sort()) {
  const res = await fetch(`http://127.0.0.1:${PORT}${url}`, { redirect: "follow" });
  if (!res.ok) {
    fail(`${url} answered ${res.status} from the traced bundle`);
  }
}

// The check has to be able to fail. A crawl that found nothing passes silently,
// which is how a matcher that quietly stops matching reads as healthy.
if (seen.size < 4) {
  fail(`only ${seen.size} asset URLs were found across ${ROUTES.length} routes. ` +
       `The crawl matched almost nothing, so its passing means nothing.`);
}

// Hazard 1, in its Next.js form: `npm ci` fails outright when the lockfile and
// package.json disagree, so CI would never have built the tree it tested.
try {
  execFileSync("npm", ["ls", "--depth=0", "--json"],
               { cwd: WEB, stdio: ["ignore", "pipe", "pipe"] });
} catch (e) {
  const out = (e.stdout?.toString() || "") + (e.stderr?.toString() || "");
  fail(`the installed tree disagrees with package.json:\n${out.slice(0, 800)}`);
}

// ---------------------------------------------------------------- report

console.log(`\nchecked ${ROUTES.length} routes, ${seen.size} asset URLs, ` +
            `served from a traced bundle with no source tree behind it`);
for (const n of notes) console.log(`  note: ${n}`);

if (problems.length) {
  console.error(`\n${problems.length} problem(s):`);
  for (const p of problems) console.error(`  - ${p}`);
  done(1);
}
console.log("deployed shape OK");
done(0);

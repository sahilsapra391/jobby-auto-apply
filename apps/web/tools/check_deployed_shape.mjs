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
import net from "node:net";
import { existsSync, cpSync, rmSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const WEB = dirname(dirname(fileURLToPath(import.meta.url)));
/**
 * The board sits at the root of its own domain, so there is no path prefix to
 * carry. The constant stays because the check is about what the deployment
 * emits rather than about one routing choice: set it and every absolute asset
 * URL is required to carry it again.
 */
const BASE_PATH = "";
/** Every route the app serves. A page absent from here is a page unchecked. */
const ROUTES = ["/", "/profile", "/settings"];
const PORT = 3199;
/**
 * The gate is on in this check, not bypassed for it.
 *
 * middleware.ts refuses every request without Basic auth and answers 503 when
 * DASHBOARD_PASSWORD is unset. Running the shape crawl with no password would
 * have meant crawling 503s and asserting happily that they carry no bad asset
 * URLs, which is the shape of a check that passes while testing nothing. So
 * the check supplies a password, and asserts the gate first.
 */
const PASSWORD = "check-deployed-shape";
/** Distinct per run, so a stored idempotent answer can never stand in for one. */
const runId = `${process.pid}-${Date.now()}`;
const CREDENTIALS = "Basic " + Buffer.from(":" + PASSWORD).toString("base64");

const problems = [];
const notes = [];
function fail(msg) { problems.push(msg); }
function note(msg) { notes.push(msg); }

// ---------------------------------------------------------------- build

if (!process.argv.includes("--no-build")) {
  note("building");
  execFileSync("npm", ["run", "build"], {
    cwd: WEB, stdio: "inherit",
    // The flag that turns on the traced output this check reads.
    env: { ...process.env, JOBBY_STANDALONE_BUILD: "1" },
  });
}

const standalone = join(WEB, ".next", "standalone");
if (!existsSync(standalone)) {
  console.error("no standalone output. This check builds with\n"
    + "  JOBBY_STANDALONE_BUILD=1, which next.config.mjs turns into\n"
    + "  output: \"standalone\". If that wiring changed, this cannot run.");
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

const running = [];

/** Boot the traced server on `port`, optionally with a password configured. */
async function boot(port, password) {
  const env = { ...process.env, PORT: String(port), HOSTNAME: "127.0.0.1",
                NODE_ENV: "production" };
  // The board reads Postgres, so the traced server needs one or every route
  // 500s with a named reason -- which is the page failing correctly and this
  // check failing for a reason that is not about shape. CI supplies the
  // throwaway container; a laptop supplies whatever it has.
  if (!env.JOBBY_DATABASE_URL && process.env.JOBBY_TEST_DATABASE_URL) {
    env.JOBBY_DATABASE_URL = process.env.JOBBY_TEST_DATABASE_URL;
  }
  if (password === undefined) delete env.DASHBOARD_PASSWORD;
  else env.DASHBOARD_PASSWORD = password;

  // **Refuse a port somebody else is on, rather than talk to them.**
  // A readiness probe that fetches `/` cannot tell this server's answer from
  // a stale one's, so a child that dies of EADDRINUSE leaves the whole check
  // measuring a build from some earlier run -- observed on 2026-09-02, when a
  // crash left two of these alive and three subsequent runs happily reported
  // "deployed shape OK" against a bundle four minutes old, including one run
  // whose whole purpose was to prove a deliberately broken build would fail.
  if (await listening(port)) {
    throw new Error(
      `port ${port} is already in use, so this check would have been served ` +
      `by a process it did not start. Kill it and run again:\n` +
      `  lsof -nP -iTCP:${port} -sTCP:LISTEN`);
  }

  const proc = spawn(process.execPath, [join(isolated, "server.js")],
                     { cwd: isolated, env, stdio: ["ignore", "pipe", "pipe"] });
  // Never DEVNULL. The failure that matters here explains itself once, in the
  // server's own stderr, at the moment it dies.
  const entry = { proc, log: "" };
  proc.stdout.on("data", (d) => { entry.log += d; });
  proc.stderr.on("data", (d) => { entry.log += d; });
  running.push(entry);

  const until = Date.now() + 30000;
  while (Date.now() < until) {
    // **The child's own death ends the wait.** Without this the loop keeps
    // polling a port that some other process may be answering on, and a server
    // that never started reads as one that started fine.
    if (proc.exitCode !== null || proc.signalCode !== null) {
      entry.dead = true;
      return entry;
    }
    try {
      await fetch(`http://127.0.0.1:${port}/`, { redirect: "manual" });
      return entry;
    } catch {
      await new Promise((r) => setTimeout(r, 250));
    }
  }
  entry.dead = true;
  return entry;
}

/** Is anything listening here? Asked before spawning, never after. */
async function listening(port) {
  return await new Promise((resolve) => {
    const probe = net.connect({ host: "127.0.0.1", port });
    const settle = (answer) => { probe.destroy(); resolve(answer); };
    probe.once("connect", () => settle(true));
    probe.once("error", () => settle(false));
    probe.setTimeout(1000, () => settle(false));
  });
}

function done(code) {
  for (const { proc } of running) proc.kill("SIGTERM");
  rmSync(isolated, { recursive: true, force: true });
  process.exit(code);
}

// **A crash must still take its servers with it.** The failure above was one
// exception away: `Pool is not a constructor` ended the process between the
// spawn and the cleanup, and the orphans it left silently served every later
// run. Cleaning up on the way out costs four lines and closes the class.
for (const signal of ["uncaughtException", "unhandledRejection"]) {
  process.on(signal, (err) => {
    console.error(`\n${signal}:`, err);
    done(1);
  });
}
for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => done(1));

const server = await boot(PORT, PASSWORD);
const serverLog = server.log;
if (server.dead) {
  console.error("the isolated server never answered. Its own output:\n" + server.log);
  done(1);
}

// ----------------------------------------------------------------- gate
//
// A custom domain is exempt from the team's SSO, so this middleware is the
// only thing between v2.jobby.fyi and the internet. It is checked here, on the
// traced bundle, because a gate that works in dev and is absent from the
// deployment is the exact failure this whole tool exists for.

for (const route of ROUTES) {
  const url = `http://127.0.0.1:${PORT}${route}`;

  const anon = await fetch(url);
  if (anon.status !== 401) {
    fail(`${route} answered ${anon.status} with no credentials. It must be 401.`);
  }
  if (!(anon.headers.get("www-authenticate") || "").startsWith("Basic ")) {
    fail(`${route} refused anonymously without a WWW-Authenticate challenge, ` +
         `so a browser has no way to offer credentials.`);
  }

  const wrong = await fetch(url, {
    headers: { authorization: "Basic " + Buffer.from(":wrong").toString("base64") },
  });
  if (wrong.status !== 401) {
    fail(`${route} answered ${wrong.status} to a wrong password. It must be 401.`);
  }
}

// **The case this check could not see about itself.**
//
// Every assertion above runs against a server with a password configured, so
// none of them can observe what happens when there is none -- and an unset
// environment variable is the most likely way this board ends up serving the
// world, because it looks like nothing at all. It happens on a fresh project,
// on a renamed variable, on a preview environment nobody populated. So a second
// server is booted with DASHBOARD_PASSWORD absent, and it must refuse.
{
  const bare = await boot(PORT + 1, undefined);
  if (bare.dead) {
    fail("the server with no DASHBOARD_PASSWORD never answered:\n" + bare.log);
  } else {
    const res = await fetch(`http://127.0.0.1:${PORT + 1}/`);
    if (res.status !== 503) {
      fail(`with DASHBOARD_PASSWORD unset the board answered ${res.status}, ` +
           `not 503. An unconfigured board must refuse, never fall open: on a ` +
           `custom domain there is no SSO behind it to catch this.`);
    }
    // And it must not be openable by guessing the empty password.
    const empty = await fetch(`http://127.0.0.1:${PORT + 1}/`, {
      headers: { authorization: "Basic " + Buffer.from(":").toString("base64") },
    });
    if (empty.status !== 503) {
      fail(`with DASHBOARD_PASSWORD unset, an empty password got ` +
           `${empty.status}. Absent config must not be satisfiable.`);
    }
  }
}

// Static chunks are the board's own code and are not exempt.
const chunkProbe = await fetch(`http://127.0.0.1:${PORT}/_next/static/chunks/`);
if (chunkProbe.status !== 401) {
  fail(`/_next/static/ answered ${chunkProbe.status} unauthenticated. The ` +
       `matcher exempts static assets, and a lane's shape is inferable from ` +
       `the board's own chunks.`);
}

// ------------------------------------------------- writes refuse, flag off

// **The credential v2 holds can write.** It is the device's own and it owns
// the tables, so nothing about the database stops a write reaching the user's
// store; `accounts.flags.live_board` is the only thing that does. A route that
// is merely unreachable is not the same as one that refuses, and the
// difference shows up the first time somebody adds a link to it.
//
// Checked from the traced bundle, authenticated, with a body that would
// otherwise be valid. The expected answer is 503 and no row.
const MUTATIONS = [
  ["a valid mutation", { "idempotency-key": "shape-check" },
   { applicationId: "shape-check", status: "watch" }],
  ["a mutation with no Idempotency-Key", {}, { applicationId: "shape-check" }],
  // The Found path, which reaches `job_decisions` as well as `applications`
  // and so is the one with the most to lose from an accidental write.
  ["a posting decision", { "idempotency-key": "shape-check-2" },
   { postingId: "00000000-0000-0000-0000-000000000000", decision: "pass" }],
];

const post = (headers, body) =>
  fetch(`http://127.0.0.1:${PORT}/api/applications`, {
    method: "POST",
    headers: { authorization: CREDENTIALS, "content-type": "application/json",
               ...headers },
    body: JSON.stringify(body),
  });

for (const [label, headers, body] of MUTATIONS) {
  const res = await post(headers, body);
  if (res.status !== 503) {
    fail(`POST /api/applications answered ${res.status} for ${label}. With ` +
         `live_board off it must refuse with 503 before it does anything ` +
         `else, including validating the request.`);
  }
}

// **And the other direction, because a route that 503s unconditionally would
// pass every assertion above.** A check that can only fail one way is half a
// check: this one would go on passing after somebody deleted the handler, or
// broke the pool, or typoed the flag name so it never reads true.
//
// So the flag is turned on against the same database the server reads, one
// request is sent, and the answer must be *anything but* 503. It is turned
// off again in a `finally`, because leaving a write gate open is worse than
// not having checked it.
{
  const url = process.env.JOBBY_DATABASE_URL || process.env.JOBBY_TEST_DATABASE_URL;
  if (!url) {
    note("no database, so the flag-on control did not run. The 503s above " +
         "prove a refusal and not that the flag is what causes it.");
  } else {
    // `pg` is CommonJS, so the named export is not there under a dynamic
    // import; the constructor hangs off `default`.
    const pg = await import("pg");
    const Pool = pg.Pool ?? pg.default.Pool;
    const pool = new Pool({ connectionString: url, max: 1 });
    try {
      const { rows } = await pool.query(
        "select id from accounts order by created_at limit 1");
      if (!rows.length) {
        fail("no account row, so the flag-on control could not run");
      } else {
        const account = rows[0].id;
        try {
          // **The control asserts a row, not a status.** A 200 is not evidence
          // the route did anything: `remember` stores the first answer for a
          // key and `replay` serves it back, so re-using a key would return
          // 200 forever without the handler running. Measured locally, where
          // the database survives between runs -- CI's throwaway container
          // hides it, which is the same excuse that let a test suite write the
          // real store for three days.
          //
          // So the row is cleared first, a fresh key is used, and the check is
          // whether the row exists afterwards.
          await pool.query("delete from applications where legacy_id = 'shape-check'");
          await pool.query(
            "update accounts set flags = coalesce(flags,'{}'::jsonb) || " +
            "'{\"live_board\": true}'::jsonb where id = $1", [account]);
          const res = await post({ "idempotency-key": `shape-check-${runId}` },
                                 { applicationId: "shape-check", status: "watch" });
          const after = await pool.query(
            "select count(*) from applications where legacy_id = 'shape-check'");
          const wrote = Number(after.rows[0].count) === 1;
          if (res.status === 503 || !wrote) {
            fail(`with live_board ON the route answered ${res.status} and ` +
                 `${wrote ? "wrote" : "wrote nothing"}. The 503s above are ` +
                 `therefore not evidence the flag gates anything: a route ` +
                 `that always refuses passes them identically.`);
          } else {
            note(`flag-on control: answered ${res.status} and the row exists`);
          }
        } finally {
          await pool.query(
            "update accounts set flags = coalesce(flags,'{}'::jsonb) || " +
            "'{\"live_board\": false}'::jsonb where id = $1", [account]);
          const back = await post({ "idempotency-key": "shape-check-off" },
                                  { applicationId: "shape-check", status: "watch" });
          if (back.status !== 503) {
            fail(`the flag was turned back off and the route answered ` +
                 `${back.status}. It must refuse again; a check that leaves a ` +
                 `write gate open is worse than one that never ran.`);
          }
          // Both, and the keys too. A key left behind is a stored answer the
          // next run would be served instead of exercising anything.
          await pool.query("delete from applications where legacy_id = 'shape-check'");
          await pool.query("delete from idempotency_keys where key like 'shape-check%'");
        }
      }
    } finally {
      await pool.end();
    }
  }
}

// ------------------------------------------------- the lane row endpoint
//
// What makes an event applicable: the board asks the lane whether it still
// holds a subject. Checked from the traced bundle because it is a new route,
// and a route the deployment does not carry answers 404 exactly like one
// nobody wrote.
for (const [label, query, want] of [
  ["an unknown lane", "lane=nope&id=00000000-0000-0000-0000-000000000000", 400],
  ["an id that is not a uuid", "lane=found&id=nope", 400],
  ["a well-formed ask", "lane=found&id=00000000-0000-0000-0000-000000000000", 200],
]) {
  const res = await fetch(`http://127.0.0.1:${PORT}/api/lane/row?${query}`,
                          { headers: { authorization: CREDENTIALS } });
  if (res.status !== want) {
    fail(`GET /api/lane/row answered ${res.status} for ${label}, wanted ${want}`);
  }
  if (res.status === 200) {
    const body = await res.json();
    if (!("row" in body)) {
      fail("a 200 from /api/lane/row must carry `row`, null or otherwise: " +
           "empty is the answer 'not in this lane', not a fault");
    }
  }
}

// **A cursor belongs to the ordering it was cut from.** Page one of Found by
// score ends on a number; carrying it into the date query reached Postgres as
// a timestamp and raised, the client caught it, and the lane drew "and that is
// all of them" over 17,341 rows.
for (const [label, query, want] of [
  ["a score cursor on the date sort",
   "lane=found&sort=new&afterId=00000000-0000-0000-0000-000000000000&afterAt=93", 400],
  ["a date cursor on the score sort",
   "lane=found&sort=score&afterId=00000000-0000-0000-0000-000000000000" +
   "&afterAt=2026-01-01T00:00:00Z", 400],
  ["a date cursor on the date sort",
   "lane=found&sort=new&afterId=00000000-0000-0000-0000-000000000000" +
   "&afterAt=2026-01-01T00:00:00Z", 200],
]) {
  const res = await fetch(`http://127.0.0.1:${PORT}/api/lane?${query}`,
                          { headers: { authorization: CREDENTIALS } });
  if (res.status !== want) {
    fail(`GET /api/lane answered ${res.status} for ${label}, wanted ${want}`);
  }
}

// ---------------------------------------------------------------- check

const seen = new Set();

for (const route of ROUTES) {
  const res = await fetch(`http://127.0.0.1:${PORT}${route}`,
                          { headers: { authorization: CREDENTIALS } });
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
    if (BASE_PATH && !url.startsWith(BASE_PATH + "/") && url !== BASE_PATH) {
      fail(`${route} references ${url}, which does not carry ${BASE_PATH}. ` +
           `It resolves in dev and 404s deployed.`);
      continue;
    }
    seen.add(url);
  }
}

// Every referenced asset actually answers, from the isolated tree.
//
// **A route that says "not found" and a route that is not there both answer
// 404**, and only one of them is a fault. The board asks
// `/api/company-logo?domain=` for every card, and a favicon endpoint answers
// nothing for a domain it does not know, which is the designed outcome: a
// wrong guess costs a monogram rather than a stranger's mark. So a 404 from
// that route is read by its body, which is the route's own words, and a 404
// from Next's router is HTML. That distinction is the same one the
// `.gitignore` finding turned on: a route git ignores and a route nobody
// wrote look identical from the outside.
//
// One probe covers it, not fifty. Fifty identical-shaped misses is a slow
// crawl asserting one thing.
const logoUrls = [...seen].filter((u) => u.startsWith("/api/company-logo"));
const assets = [...seen].filter((u) => !u.startsWith("/api/company-logo")).sort();

for (const url of assets) {
  const res = await fetch(`http://127.0.0.1:${PORT}${url}`,
                          { redirect: "follow", headers: { authorization: CREDENTIALS } });
  if (!res.ok) {
    fail(`${url} answered ${res.status} from the traced bundle`);
  }
}

if (logoUrls.length) {
  const url = logoUrls.sort()[0];
  const res = await fetch(`http://127.0.0.1:${PORT}${url}`,
                          { redirect: "follow", headers: { authorization: CREDENTIALS } });
  const body = res.ok ? "" : await res.text();
  if (res.ok) {
    const type = res.headers.get("content-type") || "";
    if (!type.startsWith("image/")) {
      fail(`${url} answered 200 with content-type ${type}. The logo proxy may ` +
           `only return an image; anything else is a page it fetched and did ` +
           `not check.`);
    }
  } else if (res.status === 404 && body.includes("No company logo")) {
    note(`the logo proxy answered its own 404 for ${logoUrls.length} card` +
         `${logoUrls.length === 1 ? "" : "s"}, which is a miss and not a ` +
         `missing route`);
  } else {
    fail(`${url} answered ${res.status} and the body was not the route's own ` +
         `("${body.slice(0, 60).replace(/\s+/g, " ")}"). The route is absent ` +
         `from the traced bundle, which is what happened when the Soft ` +
         `Premium port carried the markup and left the handler behind.`);
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

console.log(`\nchecked ${ROUTES.length} routes gated and ${seen.size} asset URLs, ` +
            `served from a traced bundle with no source tree behind it`);
for (const n of notes) console.log(`  note: ${n}`);

if (problems.length) {
  console.error(`\n${problems.length} problem(s):`);
  for (const p of problems) console.error(`  - ${p}`);
  done(1);
}
console.log("deployed shape OK");
done(0);

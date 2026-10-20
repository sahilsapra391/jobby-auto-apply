// The logo proxy's own rules, and the set it shares with Python.
//
// **This route fetches a URL the browser chose, so its refusals are the whole
// of it.** Everything it accepts becomes a request this server makes from
// inside the deployment, and the two things standing in front of that are a
// hostname check and a trusted-host allowlist.
//
// The allowlist is generated from `company_logos.TRUSTED_SOURCE_HOSTS`, and
// this file compares the generated copy with the Python one rather than
// reading either and agreeing: a hand-written copy is the one that drifts, and
// drift here is an open redirect nobody is watching.

import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { readFileSync } from "node:fs";
import { LOGO_HOSTS, CANDIDATE_TLDS } from "../apps/web/app/logos.generated.ts";
import {
  candidates, faviconFor, safeDomain, trustedSource,
} from "../apps/web/app/_ui/logo_source.ts";

const REPO = dirname(dirname(fileURLToPath(import.meta.url)));
const ROUTE = readFileSync(
  join(REPO, "apps", "web", "app", "api", "company-logo", "route.ts"), "utf8");
const SOURCE = readFileSync(
  join(REPO, "apps", "web", "app", "_ui", "logo_source.ts"), "utf8");

/* --------------------------------------------------- one set, two copies */

test("the generated hosts are Python's, not a retyped list", () => {
  const python = JSON.parse(execFileSync("python3", [
    "-c",
    "import sys, json; sys.path.insert(0, 'scripts'); import company_logos; "
    + "print(json.dumps(sorted(company_logos.TRUSTED_SOURCE_HOSTS)))",
  ], { cwd: REPO, encoding: "utf8" }));
  assert.deepEqual([...LOGO_HOSTS].sort(), python);
  assert.ok(python.length > 0, "an empty set would satisfy the line above");
});

/* ----------------------------------------------------------- the guards */

// The predicates live in `_ui/logo_source.ts` rather than in the handler, so
// these are the real functions the route calls. A copy retyped here would pass
// while the route was wrong, which is the whole failure this file is about.

test("a hostname that is not one is refused", () => {
  for (const bad of [
    "", "x", "nodot", ".leading.com", "trailing.com.", "double..dot.com",
    "has space.com", "under_score.com", "UPPER.com", "a".repeat(254) + ".com",
    "127.0.0.1:8080", "localhost", "example.com/path", "example.com?q=1",
    "user@example.com", "exa\nmple.com",
  ]) {
    assert.equal(safeDomain(bad), false, `accepted ${JSON.stringify(bad)}`);
  }
});

test("control: ordinary hostnames are accepted", () => {
  for (const good of ["stripe.com", "a.co", "sub.domain.example.org",
                      "my-company.io", "x1.ai"]) {
    assert.equal(safeDomain(good), true, `refused ${good}`);
  }
});

test("alt expands the label, and never past the known TLDs", () => {
  assert.ok(CANDIDATE_TLDS.every((t) => t.startsWith(".")),
            "a TLD without its dot concatenates into the label");
  assert.deepEqual(candidates("acme.com", true),
                   ["acme.com", "acme.io", "acme.ai", "acme.co"],
                   "the domain itself first, then its siblings, no duplicate");
  assert.deepEqual(candidates("acme.com", false), ["acme.com"],
                   "without alt it is one attempt: a domain read off a real "
                   + "posting URL is not a guess to be widened");
  assert.deepEqual(candidates("not a domain", true), [],
                   "and a name that is not a hostname produces no requests");
});

test("no trusted source survives a scheme or a credential", () => {
  const host = [...LOGO_HOSTS][0];
  assert.ok(trustedSource(`https://${host}/logo.png`));
  for (const bad of [
    `http://${host}/logo.png`,
    `https://user:pass@${host}/logo.png`,
    `https://evil.example/logo.png`,
    `https://${host}.evil.example/logo.png`,
    `//${host}/logo.png`,
    "file:///etc/passwd",
    "",
  ]) {
    assert.equal(trustedSource(bad), null, `accepted ${JSON.stringify(bad)}`);
  }
});

test("a favicon target names the domain and nothing else", () => {
  const url = faviconFor("acme.com");
  assert.equal(url.hostname, "t3.gstatic.com");
  assert.equal(url.searchParams.get("url"), "https://acme.com");
});

/* ------------------------------------------------ what the source pins */

test("only https, and never a URL carrying credentials", () => {
  assert.match(SOURCE, /url\.protocol !== "https:"/);
  assert.match(SOURCE, /url\.username \|\| url\.password/,
    "https://user:pass@trusted.host is a credential this server would send");
});

test("the route fetches only what the shared module hands it", () => {
  assert.ok(!/new URL\(/.test(ROUTE),
    "a URL built inside the handler is one the predicates never saw");
  assert.match(ROUTE, /candidates\(params\.get\("domain"\)/);
  assert.match(ROUTE, /trustedSource\(params\.get\("src"\)/);
});

test("the trusted host is re-checked after the redirect", () => {
  assert.match(ROUTE, /!trustedSource\(upstream\.url\)/,
    "a redirect off a trusted host is no longer a trusted host, and the "
    + "check has to be where it could have gone");
});

test("the response is bounded and has to be an image", () => {
  assert.match(ROUTE, /type\.startsWith\("image\/"\)/);
  assert.match(ROUTE, /byteLength < 100 \|\| bytes\.byteLength > 750_000/);
  assert.match(ROUTE, /nosniff/);
  assert.match(ROUTE, /"cache-control": "private/,
    "one reader's board behind a password: a shared cache has no business "
    + "holding what it looked at");
});

test("a miss is a 404 and not an empty 200", () => {
  assert.match(ROUTE, /status: 404/);
  assert.ok(!/status: 200[^]*No company logo/.test(ROUTE));
});

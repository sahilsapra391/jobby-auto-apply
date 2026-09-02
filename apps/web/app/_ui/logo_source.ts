/**
 * Which URLs the logo proxy is allowed to fetch.
 *
 * **Separate from the route because this is the part with teeth.** The route
 * fetches a URL the browser chose, from inside the deployment; these two
 * predicates are what stand in front of that. Kept out of the route file so
 * `tests/test_company_logo_route.test.mjs` can run the real functions instead
 * of scraping them out of a handler that imports `next/server`.
 */

import { CANDIDATE_TLDS, LOGO_HOSTS } from "../logos.generated.ts";

/** A syntactically plausible hostname, checked before it reaches `fetch`. */
export const safeDomain = (d: string) =>
  /^[a-z0-9.-]{3,253}$/.test(d) && d.includes(".")
  && !d.startsWith(".") && !d.endsWith(".") && !d.includes("..");

/**
 * A feed-supplied logo URL, or null.
 *
 * https only, no embedded credentials, and the host has to be one a job feed
 * actually serves images from. Anything else is an arbitrary URL the browser
 * asked this server to make a request to.
 */
export function trustedSource(value: string): URL | null {
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" || url.username || url.password) return null;
    return LOGO_HOSTS.has(url.hostname.toLowerCase()) ? url : null;
  } catch {
    return null;
  }
}

/**
 * The domains to try, in order.
 *
 * `alt` means "and the other TLDs of this label", which is what a derived name
 * gets: `company_logos.logo_plan` decides that a name was guessed rather than
 * read off a posting URL, and a guess deserves more than one attempt.
 */
export function candidates(primary: string, alt: boolean): string[] {
  const domain = primary.trim().toLowerCase();
  if (!safeDomain(domain)) return [];
  const out = [domain];
  if (!alt) return out;
  const dot = domain.lastIndexOf(".");
  const label = dot > 0 ? domain.slice(0, dot) : domain;
  for (const tld of CANDIDATE_TLDS) {
    const next = label + tld;
    if (next !== domain && safeDomain(next)) out.push(next);
  }
  return out;
}

/** Google's favicon endpoint, which answers 404 for a domain it does not know. */
export const faviconFor = (domain: string) => new URL(
  "https://t3.gstatic.com/faviconV2?client=SOCIAL&type=FAVICON"
  + `&fallback_opts=TYPE,SIZE&url=https://${domain}&size=128`);

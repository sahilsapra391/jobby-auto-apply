"use client";

import { useState } from "react";

/** Every board mutation carries these. A POST without `x-jobby-request` is
 *  refused as cross-site, which is how Watch, Pass and Apply from Found 403'd
 *  for five days while the dashboard's own buttons kept working. */
function headers() {
  return {
    "content-type": "application/json",
    "idempotency-key": crypto.randomUUID(),
    "x-jobby-request": "1",
  };
}

/**
 * Post, and say what came back.
 *
 * The server's own error, never a generic one. "That did not reach the queue"
 * with no reason is what let fifteen sessions walk past a 403.
 */
async function send(path: string, body: unknown) {
  const res = await fetch(path, {
    method: "POST", headers: headers(), body: JSON.stringify(body),
  });
  if (!res.ok) {
    const answer = await res.json().catch(() => ({}));
    throw new Error(String(answer.error ?? `HTTP ${res.status}`));
  }
  return res.json().catch(() => ({}));
}

/**
 * Find new sources: the agent discovery sweep.
 *
 * **Two different things, so two different names.** This is `run_scan.py`, an
 * LLM-driven pass that finds boards the registry does not have. The connectors
 * are a separate pass that runs on its own every four hours in the cloud, and
 * calling both "Scan" hid that one of them was a discovery step somebody has to
 * ask for.
 *
 * It was a raw `<button type="button">` with no handler, and v2 had no scan
 * route, so the lane's one deliberate way to ask for work sent nothing.
 */
export function FindSources({ enabled }: { enabled: boolean }) {
  const [state, setState] = useState<"idle" | "sending" | "sent" | "failed">("idle");
  const [why, setWhy] = useState("");
  return (
    <>
      <button className="jb-btn primary" type="button"
              disabled={!enabled || state === "sending"}
              title={enabled ? undefined : READ_ONLY}
              onClick={async () => {
                setState("sending"); setWhy("");
                try { await send("/api/scan", {}); setState("sent"); }
                catch (err) {
                  setWhy(err instanceof Error ? err.message : String(err));
                  setState("failed");
                }
              }}>
        {state === "sending" ? "Queueing…"
         : state === "sent" ? "Queued" : "Find new sources"}
      </button>
      {state === "failed"
        ? <span className="jb-savestate" role="alert">{why}</span> : null}
    </>
  );
}


/**
 * Read the filter form and build the config `search_config.py` accepts.
 *
 * **A wired button posting `{}` is a stranded control one hop later**, which is
 * what this existed as for one commit. The payload is read off the form at
 * click time rather than held in React state, because the filter surface is a
 * server component rendering 199 countries inside a `<form method="get">` and
 * lifting all of it into state to build one object would be a rewrite for the
 * sake of the payload.
 *
 * v2's field names are its own (`q`, `not`, `within`, `wp`, `cos`) and the
 * config's are the store's (`keywords`, `excludeKeywords`, `postedWithin`,
 * `workplace`, `companiesMode`). The mapping between them lives here, once.
 * Anything the form does not carry is simply absent: `search_config.validate`
 * fills its own defaults, and inventing a value here would be this board
 * asserting a filter the reader never set.
 */
function configFromForm(root: HTMLElement | Document): Record<string, unknown> {
  const scope = root instanceof Document ? root : root;
  const one = (name: string) =>
    scope.querySelector<HTMLInputElement | HTMLSelectElement>(`[name="${name}"]`);
  const value = (name: string) => (one(name)?.value ?? "").trim();
  const many = (name: string) => Array.from(
    scope.querySelectorAll<HTMLInputElement>(`[name="${name}"]:checked`))
    .map((n) => n.value).filter(Boolean);

  const config: Record<string, unknown> = {};

  const q = value("q");
  if (q) config.keywords = q.split(/\s+/).filter(Boolean);
  const not = value("not");
  if (not) config.excludeKeywords = [not];
  const scopeValue = value("scope");
  if (scopeValue) config.searchScope = scopeValue;
  const within = value("within");
  if (within) config.postedWithin = within;
  const sort = value("sort");
  if (sort) config.sort = sort;

  const workplaces = many("wp");
  if (workplaces.length) config.workplace = workplaces;

  const cos = value("cos");
  if (cos) config.companiesMode = cos;
  const names = value("conames");
  if (names) {
    config.companies = names.split(",").map((c) => c.trim()).filter(Boolean);
  }

  const exp = value("exp");
  // "" is "Open to all", which the store spells `null`, and that is a real
  // value rather than an absent one: leaving it out would keep whatever the
  // last save set.
  if (one("exp")) config.maxExperienceYears = exp === "" ? null : Number(exp);

  const direct = scope.querySelector<HTMLInputElement>('[name="direct"]');
  if (direct) config.directOnly = direct.checked;

  // Empty means every country, which is the storage contract. A partial
  // selection stores explicit codes, so an unchecked picker and a fully
  // checked one are the same saved value and both mean Worldwide.
  const boxes = Array.from(
    scope.querySelectorAll<HTMLInputElement>("input[data-country-name]"));
  if (boxes.length) {
    const picked = boxes.filter((b) => b.checked);
    config.locations = picked.length === boxes.length
      ? []
      : picked.map((b) => ({ country: b.value }));
  }
  return config;
}

/** Save search. Parked in the queue; `search_config.py` validates it, because
 *  a board that could write an invalid config would break every later scan. */
export function SaveSearch({ enabled }: { enabled: boolean }) {
  const [state, setState] = useState<"idle" | "sending" | "sent" | "failed">("idle");
  const [why, setWhy] = useState("");
  return (
    <>
      <button className="jb-btn primary" type="button"
              disabled={!enabled || state === "sending"}
              title={enabled ? undefined : READ_ONLY}
              onClick={async (e) => {
                setState("sending"); setWhy("");
                try {
                  const form = (e.currentTarget as HTMLElement)
                    .closest(".jb-filters") ?? document;
                  const config = configFromForm(form as HTMLElement);
                  const locations = config.locations;
                  if (Array.isArray(locations) && locations.length === 0
                      && document.querySelector("input[data-country-name]:checked") === null) {
                    // The old board refused this rather than saving Worldwide
                    // by accident: an empty picker and a full one store the
                    // same value, so a reader who unchecked everything has to
                    // be told rather than silently given the world.
                    setWhy("Choose a location, or select Worldwide.");
                    setState("failed");
                    return;
                  }
                  await send("/api/search-config", config);
                  setState("sent");
                }
                catch (err) {
                  setWhy(err instanceof Error ? err.message : String(err));
                  setState("failed");
                }
              }}>
        {state === "sending" ? "Saving…" : "Save search"}
      </button>
      {/* **Whole literals, one branch per state.** The computed version of
          this line -- a template literal with the state interpolated -- is the
          exact shape CLAUDE.md records twice: the reachability check cannot
          see `waiting` inside it and calls the rule dead CSS, and the
          invented-class check tokenises the same text and reports `?`, `:`,
          `===` and `waiting"` as classes v2 made up. Both guards were wrong
          about the identical string, in opposite directions, and neither is
          fixable with a better pattern because the name does not exist until
          the expression runs. */}
      <span className={state === "sending" ? "jb-savestate waiting" : "jb-savestate"}
            role={state === "failed" ? "alert" : "status"}>
        {state === "sent" ? "Saved. The next sweep uses it."
         : state === "failed" ? why : ""}
      </span>
    </>
  );
}

/** Paste a posting. It rides the scan queue, matching the old board exactly
 *  rather than growing an endpoint of its own. */
export function AddPosting({ enabled }: { enabled: boolean }) {
  const [url, setUrl] = useState("");
  const [state, setState] = useState<"idle" | "sending" | "sent" | "failed">("idle");
  const [why, setWhy] = useState("");
  return (
    <div className="jb-row">
      <input type="url" placeholder="https://…" aria-label="Posting link"
             value={url} onChange={(e) => setUrl(e.target.value)}
             disabled={!enabled || state === "sending"}
             title={enabled ? undefined : READ_ONLY} />
      <button className="jb-btn" type="button"
              disabled={!enabled || state === "sending" || !url.trim()}
              title={enabled ? undefined : READ_ONLY}
              onClick={async () => {
                setState("sending"); setWhy("");
                try {
                  await send("/api/scan", { url: url.trim() });
                  setUrl(""); setState("sent");
                } catch (err) {
                  setWhy(err instanceof Error ? err.message : String(err));
                  setState("failed");
                }
              }}>
        {state === "sending" ? "Adding…" : "Add"}
      </button>
      {state === "sent"
        ? <span className="jb-savestate" role="status">Queued.</span> : null}
      {state === "failed"
        ? <span className="jb-savestate" role="alert">{why}</span> : null}
    </div>
  );
}

const READ_ONLY = "The board is read-only on this deployment.";

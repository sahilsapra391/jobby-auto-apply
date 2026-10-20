"use client";

import { useEffect, useRef, useState } from "react";
import { configFromSnapshot, noLocationPicked, readFilterForm } from "./searchConfig";

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


/*
 * **`SaveFilters` is deleted, not disabled, and this is why.**
 *
 * It saved the filter form on every change, debounced, with no confirmation.
 * What it actually did was turn any URL somebody navigated to into the
 * account's saved search -- and the saved search is not a view preference.
 * `ingest.run` reads it to decide which jobs to fetch and keep, so narrowing it
 * narrows sourcing.
 *
 * Observed 2026-09-09, and the damage was mine: diagnostic navigation on the
 * deployed board wrote `locations: [{country: "IN"}]`, `keywords:
 * ["Technical", "Program", "Manager"]` and `postedWithin: "6h"` over a search
 * that had been worldwide, keywordless and 30 days. Version 23 to 27 in an
 * afternoon, four writes, none of them asked for. From the reader's side every
 * filter looked broken, because every filter was operating inside 221 rows.
 *
 * The premise was wrong rather than the implementation. "The filters you are
 * looking at are the search" is true of a view and false of a sourcing config,
 * and a write nobody asked for has no undo. If this comes back it needs an
 * explicit control, and what it writes has to be a view preference that is not
 * the sweep's input.
 */

/**
 * What the status line says after a save, and the class it wears.
 *
 * **Whole literals, one branch per state**, for the reason the old version of
 * this line recorded: a template literal with the state interpolated is the
 * shape the reachability check cannot see and the invented-class check
 * mis-tokenises, in opposite directions, about the identical string.
 */
type SaveState =
  | { kind: "idle" } | { kind: "sending" }
  | { kind: "waiting"; text: string }
  | { kind: "landed"; text: string }
  | { kind: "lost"; text: string };

/** How long a parked save may sit before it is called lost: the board's ten
 *  minutes (`SAVE_GRACE_MS`), generous enough that a slow tick never reads as
 *  a loss. */
const SAVE_GRACE_MS = 10 * 60 * 1000;
/** How often to ask while a save is parked. The watcher polls every few
 *  seconds, so most saves resolve on the first or second ask. */
const SAVE_POLL_MS = 4000;

type SaveStatus = {
  pending?: boolean; version?: number | null; refused?: string | null;
  error?: string;
};

async function saveStatus(): Promise<SaveStatus> {
  const res = await fetch("/api/search-config", { cache: "no-store" });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) return { error: String(body.error ?? `HTTP ${res.status}`) };
  return body as SaveStatus;
}

/**
 * Save search: the filters on screen become the search the sweep runs.
 *
 * **An explicit press, and nothing else, because this is not a view
 * preference.** `ingest.run` reads the saved search to decide which jobs to
 * fetch and keep, so a save narrows sourcing. The version of this that saved
 * on every change rewrote the account's search from whatever URL somebody
 * navigated to; it is gone, and `tests/the_saved_search_needs_a_press.test.mjs`
 * keeps it gone.
 *
 * **"Saved" is two facts and the line says which one is true.** A 202 means the
 * config is parked in the queue; it is applied when the device claims it and
 * `search_config.py` accepts it, which bumps the version the mirror carries
 * into Postgres. So after posting, this asks `GET /api/search-config` until the
 * version moves (applied), the device refuses it (its own sentence), or ten
 * minutes pass with nothing parked and nothing moved (lost: the queue key has
 * no compare-and-swap, and a save landing in the same instant as a claim can
 * vanish). That is the board's `reconcileSave`, asked from the press rather
 * than from page load, so no effect ever touches this route.
 */
export function SaveSearch({ enabled }: { enabled: boolean }) {
  const [state, setState] = useState<SaveState>({ kind: "idle" });
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Only a cleanup: a poll scheduled by a press stops with the component.
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  const follow = (prior: number | null, since: number) => {
    timer.current = setTimeout(async () => {
      let status: SaveStatus;
      try { status = await saveStatus(); }
      catch { status = { error: "the board could not be reached" }; }
      if (status.error) {
        setState({ kind: "waiting",
                   text: `Saved. Whether it was applied could not be read: ${status.error}` });
        return;
      }
      // Version first: a refusal can outlive the save it was about, because
      // the device clears `blocked` on its next healthy tick rather than on
      // the next accepted save, so a moved version outranks it.
      if (status.pending) {
        setState({ kind: "waiting",
                   text: "Saved. Waiting for your device to pick it up." });
      } else if (typeof status.version === "number"
                 && (prior === null || status.version > prior)) {
        setState({ kind: "landed",
                   text: `Saved, as search v${status.version}. It applies to the next scan.` });
        return;
      } else if (status.refused) {
        setState({ kind: "lost", text: status.refused });
        return;
      } else if (Date.now() - since > SAVE_GRACE_MS) {
        setState({ kind: "lost",
                   text: "That save did not land. Press Save search again." });
        return;
      } else {
        setState({ kind: "waiting",
                   text: "Saved. Waiting for the board to catch up." });
      }
      follow(prior, since);
    }, SAVE_POLL_MS);
  };

  return (
    <>
      <button className="jb-btn primary" type="button"
              disabled={!enabled || state.kind === "sending"}
              title={enabled ? undefined : READ_ONLY}
              onClick={async (e) => {
                if (timer.current) clearTimeout(timer.current);
                const card = (e.currentTarget as HTMLElement)
                  .closest(".jb-filters") ?? document;
                const form = readFilterForm(card);
                if (noLocationPicked(form)) {
                  // The board refused this rather than saving Worldwide by
                  // accident: an empty picker and a full one store the same
                  // value, so a reader who unchecked everything is told.
                  setState({ kind: "lost",
                             text: "Choose a location, or select Worldwide." });
                  return;
                }
                setState({ kind: "sending" });
                // The version before this save, so "applied" means this save
                // moved it rather than that some save once did.
                const before = await saveStatus().catch(() => ({} as SaveStatus));
                const prior = typeof before.version === "number" ? before.version : null;
                try {
                  await send("/api/search-config", configFromSnapshot(form));
                } catch (err) {
                  setState({ kind: "lost",
                             text: err instanceof Error ? err.message : String(err) });
                  return;
                }
                setState({ kind: "waiting",
                           text: "Saved. Waiting for your device to pick it up." });
                follow(prior, Date.now());
              }}>
        {state.kind === "sending" ? "Saving…" : "Save search"}
      </button>
      {state.kind === "waiting"
        ? <span className="jb-savestate waiting" role="status">{state.text}</span>
        : state.kind === "landed"
          ? <span className="jb-savestate landed" role="status">{state.text}</span>
          : state.kind === "lost"
            ? <span className="jb-savestate lost" role="alert">{state.text}</span>
            : <span className="jb-savestate" role="status" />}
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

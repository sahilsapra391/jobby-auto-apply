/**
 * `TenantChannel`: one Durable Object per account, and the Worker in front.
 *
 * The logic is in `channel.mjs`, which is plain JavaScript with no Workers API
 * in it and is tested under `node --test` against the real thing. This file is
 * the shell: routing, the DO, auth on the publish side, and the WebSocket
 * plumbing. It adds nothing `Channel` already does and must not.
 *
 * **The two sides must name the same Durable Object or this fails in the one
 * way that looks like working.** The publisher knows the account -- Vercel
 * POSTs `{account_id, kind, payload}` -- and the subscriber does not: a browser
 * WebSocket cannot set headers, and `useLive` sends exactly one frame, ever,
 * `{resume_from: <seq>}`, with no account in it. Verified in the source rather
 * than assumed. So the account has to be in the socket URL, and `useLive` uses
 * `NEXT_PUBLIC_JOBBY_CHANNEL_WS` verbatim, which means it can carry a path.
 *
 * `channelFor` is therefore the only place either side turns an account into a
 * stub. Two derivations would let sockets connect, `/publish` answer 200, and
 * nothing ever arrive.
 *
 * **The account in that URL is an address, not a credential**, and the
 * difference matters. `NEXT_PUBLIC_*` is inlined at build time, so it is one
 * public constant readable in the JS bundle by anyone who can load the board.
 * Today the board is behind HTTP Basic on every path including `_next/static`,
 * so the address is not public; the channel itself has no second gate. A real
 * per-connection credential is section 6's work, and it needs a client change
 * -- a ticket in the query, or the subprotocol, which is the one handshake
 * header a browser will set. Do not read the account id here as authorisation.
 *
 * **Replay is the ordinary path, not the failure path.** `useLive`'s socket
 * effect lists `lane` in its dependencies and carries the old `seq` into the
 * new session, so changing tab closes the socket and reopens it with a
 * non-zero `resume_from`. A shell exercised only against `resume_from: 0`
 * looks perfect and loses events on every lane change.
 *
 * **No subprotocol on the 101.** The client calls `new WebSocket(url)` with one
 * argument, so it offers none; a response naming one fails the handshake in
 * the browser, and a failed handshake is indistinguishable from a channel with
 * nothing to say.
 *
 * Hibernation is deliberately not used yet. The spec says it "matters at 1,000
 * accounts"; at one, the standard API keeps `Channel.subscribe` used exactly as
 * written and tested, and hibernation would mean the subscriber set lives in
 * `ctx.getWebSockets()` instead. That is the change to make at scale, not now.
 */

import { Channel } from "./channel.mjs";

/** Constant time, because a publish token is a secret and `===` leaks a prefix. */
function sameToken(a, b) {
  const left = new TextEncoder().encode(String(a ?? ""));
  const right = new TextEncoder().encode(String(b ?? ""));
  if (left.length !== right.length) return false;
  let diff = 0;
  for (let i = 0; i < left.length; i += 1) diff |= left[i] ^ right[i];
  return diff === 0;
}

/**
 * The account this request is about, from the path.
 *
 * One shape, `/a/<accountId>`, for both the socket and `/publish`'s target, so
 * there is no second rule to keep in step. Anything else is refused rather
 * than defaulted: a channel that silently picks an account when the URL does
 * not name one delivers somebody else's events, and on a board this is one
 * person's applications.
 */
export function accountFromPath(pathname) {
  const parts = String(pathname || "").split("/").filter(Boolean);
  if (parts.length < 2 || parts[0] !== "a") return null;
  const account = parts[1];
  // `.` and `..` match the character class and are not names. Caught by this
  // module's own test rather than in review: the class allows a dot because
  // ids may contain one, and that is exactly how a traversal segment gets in.
  if (account === "." || account === "..") return null;
  return /^[A-Za-z0-9._-]{1,128}$/.test(account) ? account : null;
}

/** The one derivation. Both sides call this or the two stop meeting. */
export function channelFor(env, accountId) {
  return env.TENANT_CHANNEL.get(env.TENANT_CHANNEL.idFromName(`account:${accountId}`));
}

export class TenantChannel {
  constructor(ctx, env) {
    this.ctx = ctx;
    this.env = env;
    this.channel = null;
    // Loaded once, then held: the DO is single-threaded, which is what makes
    // `seq` a total order rather than a best effort.
    this.ready = ctx.blockConcurrencyWhile(async () => {
      const saved = await ctx.storage.get("channel");
      this.channel = saved ? Channel.restore(saved) : new Channel();
    });
  }

  async #persist() {
    await this.ctx.storage.put("channel", this.channel.snapshot());
  }

  async fetch(request) {
    await this.ready;
    const url = new URL(request.url);

    // **The envelope carries its account, and it did not.** `Channel` puts
    // `accountId` in every envelope and the shell never passed one, so every
    // event went out with `account_id: null` -- observed on the live channel,
    // not in a test. The client declares the field and does not read it, so it
    // cost nothing today; a field that is always null is still a field that
    // lies, and section 5's cross-account check is exactly the reader that
    // would want it. A Durable Object cannot recover its own name from its id,
    // so it comes from the path, which both the socket and the publish request
    // carry by construction.
    const named = accountFromPath(url.pathname);
    if (named && this.channel.accountId !== named) {
      this.channel.accountId = named;
      await this.#persist();
    }

    if (request.headers.get("Upgrade") === "websocket") {
      const pair = new WebSocketPair();
      const [client, server] = Object.values(pair);
      server.accept();
      const unsubscribe = this.channel.subscribe(server);

      server.addEventListener("message", (event) => {
        // The only frame the client ever sends. Anything else is ignored
        // rather than closed on: a stray frame from a future client should not
        // cost this one its connection.
        let hello;
        try { hello = JSON.parse(event.data); } catch { return; }
        if (hello === null || typeof hello !== "object") return;
        if (!("resume_from" in hello)) return;
        const answer = this.channel.resume(hello.resume_from);
        try { server.send(JSON.stringify(answer)); } catch { /* gone */ }
      });

      const drop = () => unsubscribe();
      server.addEventListener("close", drop);
      server.addEventListener("error", drop);

      // 101 with no `webSocketProtocol`: the client offered none.
      return new Response(null, { status: 101, webSocket: client });
    }

    if (url.pathname.endsWith("/publish") && request.method === "POST") {
      let body;
      try { body = await request.json(); } catch {
        return json({ error: "body is not JSON" }, 400);
      }
      let envelope;
      try {
        envelope = this.channel.publish({ kind: body?.kind, payload: body?.payload });
      } catch (err) {
        // An unknown kind is the caller's mistake and is named, because a
        // channel that accepted anything would carry a fact nobody can read.
        return json({ error: String(err?.message || err) }, 400);
      }
      await this.#persist();
      return json({ seq: envelope.seq, delivered: this.channel.subscribers.size });
    }

    return json({ error: "not found" }, 404);
  }
}

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status, headers: { "content-type": "application/json" },
  });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === "/healthz") {
      return json({ ok: true, channel: "tenant" });
    }

    // **The publish path is the only one with a credential, and it is checked
    // here rather than in the DO**, so a request that cannot publish never
    // reaches an account's object at all.
    if (url.pathname === "/publish" && request.method === "POST") {
      const auth = request.headers.get("authorization") || "";
      const offered = auth.startsWith("Bearer ") ? auth.slice(7) : "";
      if (!env.JOBBY_CHANNEL_TOKEN) {
        // A missing secret is a refusal, never an open door. Same rule as the
        // board's own middleware, and for the same reason: an unset variable
        // looks like nothing at all.
        return json({ error: "JOBBY_CHANNEL_TOKEN is not configured" }, 503);
      }
      if (!sameToken(offered, env.JOBBY_CHANNEL_TOKEN)) {
        return json({ error: "bad token" }, 401);
      }
      let body;
      try { body = await request.clone().json(); } catch {
        return json({ error: "body is not JSON" }, 400);
      }
      const account = String(body?.account_id || "");
      if (!/^[A-Za-z0-9._-]{1,128}$/.test(account)) {
        return json({ error: "account_id is required" }, 400);
      }
      return channelFor(env, account).fetch(
        new Request(`https://channel/a/${account}/publish`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(body),
        }));
    }

    // Everything else is a socket, and it must name its account in the path.
    if (request.headers.get("Upgrade") === "websocket") {
      const account = accountFromPath(url.pathname);
      if (!account) {
        // **A 400, and the reason cannot be delivered over the socket.**
        // Closing right after `accept()` was tried and deployed: the client
        // still saw 1006 with an empty reason, because the close races the
        // handshake and there is no established socket to carry a close frame
        // on. Measured against the deployed Worker, not reasoned about.
        //
        // It is the wrong place regardless. 1006 is equally what a wrong host,
        // a DNS failure or a TLS failure gives, and a server cannot annotate
        // any of those because it never sees the request. Only the client
        // knows whether `open` ever fired, and that is the one fact that
        // separates "this address is wrong forever" from "the network went
        // away" -- so `useLive` draws that distinction instead.
        return json({ error: "the socket URL must name an account: /a/<id>" }, 400);
      }
      return channelFor(env, account).fetch(request);
    }

    return json({ error: "not found" }, 404);
  },
};

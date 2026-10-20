"use client";

import { useState } from "react";

/**
 * Sign in and sign out, for the owner-only resume wording editor.
 *
 * Both are JSON requests carrying `x-jobby-request`, because `middleware.ts`
 * refuses any other non-GET; a plain HTML form post would be turned away as
 * cross-site, which is the protection working rather than a bug to route
 * around.
 */

export function OwnerSignIn({ next = "/profile#wording" }: { next?: string }) {
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [why, setWhy] = useState("");

  return (
    <form className="bank-edit" onSubmit={async (event) => {
      event.preventDefault();
      setBusy(true);
      setWhy("");
      try {
        const res = await fetch("/api/owner/session", {
          method: "POST",
          headers: { "content-type": "application/json", "x-jobby-request": "1" },
          body: JSON.stringify({ password }),
        });
        if (res.ok) {
          window.location.assign(next);
          return;
        }
        const said = await res.json().catch(() => ({}));
        setWhy(String(said.error ?? `the server answered ${res.status}`));
      } catch {
        setWhy("That did not reach the server.");
      }
      setBusy(false);
    }}>
      <label>Owner password
        <input type="password" name="password" autoComplete="current-password"
               value={password} required
               onChange={(e) => setPassword(e.target.value)} />
      </label>
      <div className="bank-act">
        <button className="btn" type="submit" disabled={busy || !password}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
        {why ? <span className="profile-message bad" role="alert">{why}</span> : null}
      </div>
    </form>
  );
}

export function OwnerSignOut() {
  const [busy, setBusy] = useState(false);
  return (
    <button className="btn ghost" type="button" disabled={busy} onClick={async () => {
      setBusy(true);
      try {
        await fetch("/api/owner/session", {
          method: "DELETE",
          headers: { "content-type": "application/json", "x-jobby-request": "1" },
          body: "{}",
        });
      } finally {
        window.location.reload();
      }
    }}>Sign out</button>
  );
}

"use client";

import { useState } from "react";

/**
 * Settings' one button, wired.
 *
 * **It rendered and reached nothing.** `settings/page.tsx` drew a raw
 * `<button type="button">` carrying `data-provider` and `data-action` that no
 * script and no handler ever read, and v2 had no provider route to receive
 * them either. Fifth control of that shape after Confirm submitted, Open &
 * finish, Undrop and Apply-from-Watching.
 *
 * **Not `<Act>`, and the reason is worth stating rather than working around.**
 * `Act` needs `useActions()` -- a lane's `enabled`, `act`, `busy` and `draw` --
 * and its `act` posts to `/api/applications` and takes a row out of a list
 * while it waits. Settings has no lane, no row and a different endpoint, so
 * reusing it would mean inventing a `rowId` and a no-op `draw`, which is the
 * synthetic-record shape this repository has already paid for: a stand-in
 * built to satisfy one predicate answers every other predicate with a default.
 *
 * A fresh idempotency key per click, deliberately. `writes.remember` stores the
 * first answer for a key and `replay` serves it back, so a key fixed per render
 * would make the second Connect return the first one's 202 without the daemon
 * ever hearing it. What stops a double click connecting twice is the
 * `connectionKey` the route mints, which is sortable so the first successful
 * Connect wins.
 */
export function ProviderAction({
  provider, action, enabled, children, onSent,
}: {
  provider: string;
  action: "connect" | "disconnect" | "refresh";
  enabled: boolean;
  children: React.ReactNode;
  /** Told when the queue took the click, so the card can wait on the device. */
  onSent?: (action: "connect" | "disconnect" | "refresh") => void;
}) {
  const [state, setState] = useState<"idle" | "sending" | "sent" | "failed">("idle");
  const [why, setWhy] = useState<string>("");

  return (
    <>
      <button
        className="btn provider-action"
        type="button"
        data-provider={provider}
        data-action={action}
        disabled={!enabled || state === "sending"}
        title={enabled ? undefined
                       : "The board is read-only on this deployment."}
        onClick={async () => {
          setState("sending");
          setWhy("");
          try {
            const res = await fetch("/api/providers", {
              method: "POST",
              headers: {
                "content-type": "application/json",
                "idempotency-key": crypto.randomUUID(),
                // Every board mutation carries it; the deployed API refuses a
                // POST without it as cross-site, which is how Watch, Pass and
                // Apply from Found 403'd for five days.
                "x-jobby-request": "1",
              },
              body: JSON.stringify({ provider, action }),
            });
            if (!res.ok) {
              // The server's own reason, not a generic failure. "That did not
              // reach the queue" with no reason is what let fifteen sessions
              // walk past a 403.
              const answer = await res.json().catch(() => ({}));
              setWhy(String(answer.error ?? `HTTP ${res.status}`));
              setState("failed");
              return;
            }
            setState("sent");
            onSent?.(action);
          } catch (err) {
            setWhy(err instanceof Error ? err.message : String(err));
            setState("failed");
          }
        }}
      >
        {state === "sending" ? "Sending…" : children}
      </button>
      {state === "sent" ? (
        <p className="provider-detail" role="status">
          {action === "connect"
            // 202: asked, not done. The device opens the vendor's own login in
            // a terminal, which is where the credential is entered and where
            // it stays.
            ? "Asked your Mac to start the login. Finish it in the terminal window it opens."
            : "Asked your Mac to apply it."}
        </p>
      ) : null}
      {/* `provider-message` is the board's own status line for this card and it
          has no red variant. Inventing one here would be a second design system
          growing beside the first, which is what the invented-class check
          refused. The alert role carries the urgency. */}
      {state === "failed" ? (
        <p className="provider-detail provider-message" role="alert">{why}</p>
      ) : null}
    </>
  );
}

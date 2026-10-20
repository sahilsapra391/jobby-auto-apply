"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ProviderAction } from "./ProviderAction";
import {
  cardView, FAST_POLL_MS, PENDING_CEILING_MS, readOf, settled, SLOW_POLL_MS,
  type CardView, type LiveRead, type Pending, type StoredProvider,
} from "./providerLive";

/**
 * The three provider cards, kept live.
 *
 * The page renders them from `provider_links`, which is right for a first
 * paint and one mirror hop behind the device. This polls `GET /api/providers`,
 * the device's own published status, every thirty seconds, and once a second
 * while a Connect or Disconnect is waiting on the device, which is the
 * retired board's cadence (`settings_panel.SCRIPT`). The wording of each state
 * lives in `providerLive.ts` so it can be run in a test rather than read.
 */
// Compared against by name rather than by literal inside `className`, because
// the style guards read every string in a class expression as a class.
const BAD: CardView["tone"] = "bad";
const PENDING: CardView["tone"] = "pending";

export type CardInput = {
  id: string;
  name: string;
  agent: string;
  model: string | null;
  orderChip: string | null;
  stored: StoredProvider | undefined;
};

export function ProviderCards({ cards, enabled }: {
  cards: CardInput[]; enabled: boolean;
}) {
  const [read, setRead] = useState<LiveRead>({ kind: "unread" });
  const [pending, setPending] = useState<Record<string, Pending>>({});
  const pendingRef = useRef(pending);
  pendingRef.current = pending;

  const refresh = useCallback(async () => {
    let next: LiveRead;
    try {
      const res = await fetch("/api/providers", { cache: "no-store" });
      next = readOf(res.status, await res.json().catch(() => ({})));
    } catch (err) {
      next = { kind: "unreadable",
               why: err instanceof Error ? err.message : String(err) };
    }
    setRead(next);
    // A click is done when the device says so, or when it has waited longer
    // than any status is believed for.
    const now = Date.now();
    const held = pendingRef.current;
    const keep: Record<string, Pending> = {};
    for (const [id, p] of Object.entries(held)) {
      const live = next.kind === "live" ? next.providers[id] : undefined;
      if (!settled(p, live) && now - p.started < PENDING_CEILING_MS) keep[id] = p;
    }
    if (Object.keys(keep).length !== Object.keys(held).length) setPending(keep);
  }, []);

  const waiting = Object.keys(pending).length > 0;

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, waiting ? FAST_POLL_MS : SLOW_POLL_MS);
    return () => clearInterval(timer);
  }, [refresh, waiting]);

  return (
    <div className="provider-grid">
      {cards.map((card) => {
        const view = cardView(card.stored, read, pending[card.id], card.id);
        return (
          // `article`, like the board: `.provider-grid > article` is what the
          // stylesheet sizes.
          <article className="provider-card" key={card.id}
                   data-provider-card={card.id}>
            <div className="provider-head">
              <div>
                <h3>{card.name}</h3>
                <p>{card.agent}{card.model ? <> &middot; {card.model}</> : null}</p>
              </div>
              {/* The provider new work goes to right now, from the device's
                  own status. The order chip beside it is the configured
                  failover order, which is a different fact: the first in
                  order can be unhealthy and skipped. */}
              {view.active ? <span className="provider-active">Active</span> : null}
              {card.orderChip ? (
                <span className="chip" title="Failover order">{card.orderChip}</span>
              ) : null}
            </div>
            <div className="host-row" data-host="local">
              <div>
                <strong>Your Device</strong>
                <span>Resumes, cover letters, and application forms</span>
              </div>
              {/* The board's three states, written out whole. */}
              <span className={view.tone === BAD ? "provider-state bad"
                      : view.tone === PENDING ? "provider-state pending"
                      : "provider-state"}
                    data-provider-state="local">
                {view.label}
              </span>
            </div>
            {/* Keyed by the action, so once the device flips the card from
                Connect to Disconnect the button starts fresh instead of still
                saying it asked for the opposite. */}
            <ProviderAction key={view.action} provider={card.id} enabled={enabled}
                            action={view.action}
                            onSent={(action) => setPending((held) => ({
                              ...held, [card.id]: { action, started: Date.now() },
                            }))}>
              {view.action === "disconnect" ? "Disconnect" : "Connect"}
            </ProviderAction>
            <p className="provider-detail">{view.detail ?? ""}</p>
          </article>
        );
      })}
      {read.kind === "offline" ? (
        <p className="hint">
          Your Device has not published a provider status
          {read.lastHeard
            ? ` since ${new Date(read.lastHeard).toLocaleString()}`
            : " yet"}. Statuses older than fifteen minutes are not shown as
          current.
        </p>
      ) : null}
      {read.kind === "unreadable" ? (
        <p className="hint" role="status">
          The live status could not be read: {read.why}
        </p>
      ) : null}
    </div>
  );
}

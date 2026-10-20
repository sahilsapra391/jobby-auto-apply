/**
 * What one Settings card says, from what is known about its provider.
 *
 * No React here, so `tests/settings_provider_live.test.mjs` can transpile this
 * file and run it. The rules are the retired board's (`settings_panel.SCRIPT`,
 * `paintHost`), ported rather than reinvented:
 *
 *   - a click waits: "Waiting for sign-in" or "Disconnecting" until the live
 *     status shows it done, then the status speaks again;
 *   - a live status names the state in the board's words, plus " · Active" on
 *     the provider new work goes to;
 *   - no live status means the device is offline, because a status the device
 *     stopped refreshing fifteen minutes ago is not a status.
 *
 * One addition the board did not have: a status this deployment could not
 * read is not an offline device, and says so.
 */

/** One provider as `local-status.mjs sanitize` publishes it. */
export type LiveProvider = {
  id: string;
  installed: boolean | null;
  enabled: boolean;
  connected: boolean;
  active: boolean;
  status: string;
  checkedAt: string | null;
  detail: string;
};

/** What the page knows before the first poll, from `provider_links`. */
export type StoredProvider = {
  status: string | null;
  health: string | null;
  detail: string | null;
};

/** The poll's state. `unread` until the first answer arrives. */
export type LiveRead =
  | { kind: "unread" }
  | { kind: "live"; providers: Record<string, LiveProvider> }
  | { kind: "offline"; lastHeard: string | null }
  | { kind: "unreadable"; why: string };

export type Pending = { action: "connect" | "disconnect" | "refresh"; started: number };

/** The board's labels, verbatim from `settings_panel._status_label`. */
export const LABELS: Record<string, string> = {
  connected: "Connected",
  disconnected: "Not connected",
  connecting: "Waiting for sign-in",
  checking: "Checking",
  needs_auth: "Sign-in needed",
  missing_cli: "CLI not installed",
  error: "Connection error",
};

/** Fast while a click waits on the device, slow otherwise. The board's own. */
export const FAST_POLL_MS = 1_000;
export const SLOW_POLL_MS = 30_000;
/**
 * How long a click may wait before the card stops saying so. The board had no
 * ceiling and a sign-in nobody finished polled once a second until the tab
 * closed; fifteen minutes is the same span the status itself is believed for.
 */
export const PENDING_CEILING_MS = 15 * 60_000;

/** Whether the device's live status shows this click done. */
export function settled(pending: Pending, live: LiveProvider | undefined): boolean {
  if (!live) return false;
  if (pending.action === "disconnect") return !live.enabled;
  if (pending.action === "connect") return live.enabled && live.status !== "checking";
  return live.status !== "checking";
}

export type CardView = {
  label: string;
  /** Which of the board's three state treatments. `ProviderCards` writes the
   *  whole class string per tone, because the style guards read class names
   *  from the markup and a name returned from here is invisible to them. */
  tone: "ok" | "pending" | "bad";
  /** Which button the card offers. */
  action: "connect" | "disconnect";
  active: boolean;
  detail: string | null;
};

export function cardView(stored: StoredProvider | undefined, read: LiveRead,
                         pending: Pending | undefined, id: string): CardView {
  const live = read.kind === "live" ? read.providers[id] : undefined;
  const enabled = live ? live.enabled : stored?.status === "connected";
  const action = enabled ? "disconnect" : "connect";
  const active = Boolean(live?.active);
  const detail = live?.detail || stored?.detail || null;

  if (pending && !settled(pending, live)) {
    return {
      label: pending.action === "connect" ? "Waiting for sign-in"
        : pending.action === "disconnect" ? "Disconnecting" : "Checking",
      tone: "pending", action, active, detail,
    };
  }
  if (read.kind === "live") {
    if (!live) {
      return { label: "Your Device is offline", tone: "bad",
               action, active, detail };
    }
    return {
      label: (LABELS[live.status] ?? "Not connected") + (live.active ? " · Active" : ""),
      tone: live.status === "connected" ? "ok"
        : live.status === "checking" ? "pending" : "bad",
      action, active, detail,
    };
  }
  if (read.kind === "offline") {
    return { label: "Your Device is offline", tone: "bad",
             action, active: false, detail };
  }
  if (read.kind === "unreadable") {
    return { label: "Status could not be read", tone: "bad",
             action, active: false, detail };
  }
  // Before the first poll: what the mirror last held, as the page drew it.
  const connected = stored?.status === "connected";
  const unhealthy = connected && stored?.health && stored.health !== "healthy";
  return {
    label: connected ? (unhealthy ? `Connected, ${stored!.health}` : "Connected")
      : "Not connected",
    tone: connected ? (unhealthy ? "bad" : "ok") : "pending",
    action, active, detail,
  };
}

/** The poll's JSON, turned into a `LiveRead`. */
export function readOf(status: number, body: unknown): LiveRead {
  const said = (body ?? {}) as Record<string, unknown>;
  if (status !== 200 || said.ok !== true) {
    return { kind: "unreadable",
             why: typeof said.error === "string" ? said.error : `HTTP ${status}` };
  }
  const local = said.local as { providers?: LiveProvider[] } | null;
  if (!local || !Array.isArray(local.providers)) {
    return { kind: "offline",
             lastHeard: typeof said.lastHeard === "string" ? said.lastHeard : null };
  }
  const providers: Record<string, LiveProvider> = {};
  for (const p of local.providers) if (p && typeof p.id === "string") providers[p.id] = p;
  return { kind: "live", providers };
}

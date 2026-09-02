import { AppBar } from "../_ui/Chrome";
import { SelectFace } from "../_ui/SelectFace";
import { providers } from "../_ui/db";

/**
 * Settings. One row per provider, and the row is the board's own shape:
 * provider, status, action.
 *
 * The connection itself stays on the device -- nothing here connects anything
 * -- but the board still has to show connected from not, which is what the
 * scaffold was missing. `settings_panel.py` already defines this layout:
 * `.provider-head` names the provider and its pinned model, `.host-row` is the
 * target with its status, and `.provider-action` is the one button.
 *
 * **It read a fixture until 2026-09-02**, which said OpenAI was connected and
 * the other two were not while all three were connected and healthy, and which
 * called Gemini's agent "agy". Behind it `provider_links` held zero rows,
 * because `providers.json` had been classified as unmirrorable by a hardcoded
 * list in one test and by nothing else.
 */
export const dynamic = "force-dynamic";

/**
 * What each provider's agent is called, and what to call the provider.
 *
 * **A label, not data.** The model beside it comes from `providers.MODEL_IDS`
 * through the mirror and is never retyped here; the fixture that preceded this
 * got the tool name wrong ("agy" for the Gemini CLI) precisely because it was
 * inventing both halves.
 */
const AGENTS: Record<string, string> = {
  openai: "Codex", anthropic: "Claude Code", gemini: "Gemini CLI",
};

const NAMES: Record<string, string> = {
  openai: "OpenAI", anthropic: "Anthropic", gemini: "Gemini",
};

export default async function Settings() {
  const links = await providers();
  return (
    <>
      <AppBar here="settings" />
      <div className="wrap">
        <section className="settings-page">
          <div className="settings-titlebar">
            <h2>Settings</h2>
            <p className="eyebrow">Provider connections</p>
          </div>

          <div className="split-note">
            Every agent runs on your device. Keys are stored there and never
            reach the board: this page reads which providers are connected and
            healthy, and nothing about how they authenticate.
          </div>
          <p className="hint">
            New work probes connected providers in order and uses the first
            that can invoke its pinned model. A failed login, usage or model
            check moves the next piece of work to the next healthy connection.
          </p>

          <div className="provider-grid">
            {links.length === 0 ? (
              <p className="hint">
                No provider connections are recorded. Connect one on your
                device; the board reads what the device mirrors and invents
                nothing.
              </p>
            ) : null}
            {links.map((p) => {
              const connected = p.status === "connected";
              return (
              <div className="provider-card" key={p.provider}
                   data-provider-card={p.provider}>
                <div className="provider-head">
                  <div>
                    <h3>{NAMES[p.provider] ?? p.provider}</h3>
                    <p>{AGENTS[p.provider] ?? p.provider} &middot; {p.model}</p>
                  </div>
                  {/* The failover order, which the old board explains and v2
                      dropped. New work probes connected accounts in this order
                      and takes the first that can invoke its pinned model. */}
                  {p.order != null ? (
                    <span className="chip" title="Failover order">
                      {p.order === Math.min(...links.map((l) => l.order ?? 1e9))
                        ? "Default" : `#${p.order}`}
                    </span>
                  ) : null}
                </div>
                <div className="host-row" data-host="local">
                  <div>
                    <strong>Your Device</strong>
                    <span>Resumes, cover letters, and application forms</span>
                  </div>
                  {/* The board's own three states, not an invented pair.
                      settings_panel.py writes `provider-state`, with `pending`
                      while a check is in flight and `bad` on an error; v2 had
                      `ok`/`warn`, which the stylesheet does not define and
                      which no board has ever emitted. */}
                  <span className={connected
                          ? (p.health && p.health !== "healthy"
                             ? "provider-state bad" : "provider-state")
                          : "provider-state pending"}
                        data-provider-state="local">
                    {connected
                      ? (p.health && p.health !== "healthy"
                         ? `Connected, ${p.health}` : "Connected")
                      : "Not connected"}
                  </span>
                </div>
                <button className="btn provider-action" type="button"
                        data-provider={p.provider} data-target="local"
                        data-action={connected ? "disconnect" : "connect"}>
                  {connected ? "Disconnect" : "Connect"}
                </button>
                <p className="provider-detail">{p.detail ?? ""}</p>
              </div>
              );
            })}
          </div>
        </section>
      </div>
      <SelectFace />
    </>
  );
}

import { AppBar } from "../_ui/Chrome";
import { SelectFace } from "../_ui/SelectFace";
import { PROVIDERS } from "../_ui/fixture";

/**
 * Settings. One row per provider, and the row is the board's own shape:
 * provider, status, action.
 *
 * The connection itself stays on the device -- nothing here connects anything
 * -- but the board still has to show connected from not, which is what the
 * scaffold was missing. `settings_panel.py` already defines this layout:
 * `.provider-head` names the provider and its pinned model, `.host-row` is the
 * target with its status, and `.provider-action` is the one button.
 */
export const dynamic = "force-dynamic";

export default function Settings() {
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
            reach the board.
          </div>

          <div className="provider-grid">
            {PROVIDERS.map((p) => (
              <div className="provider-card" key={p.id} data-provider-card={p.id}>
                <div className="provider-head">
                  <div>
                    <h3>{p.name}</h3>
                    <p>{p.agent} &middot; {p.model}</p>
                  </div>
                </div>
                <div className="host-row" data-host="local">
                  <div>
                    <strong>Your Device</strong>
                    <span>Resumes, cover letters, and application forms</span>
                  </div>
                  <span className={p.connected ? "ok" : "warn"}
                        data-provider-state="local">
                    {p.connected ? "Connected" : "Not connected"}
                  </span>
                </div>
                <button className="btn provider-action" type="button"
                        data-provider={p.id} data-target="local"
                        data-action={p.connected ? "disconnect" : "connect"}>
                  {p.connected ? "Disconnect" : "Connect"}
                </button>
                <p className="provider-detail">{p.detail}</p>
              </div>
            ))}
          </div>
        </section>
      </div>
      <SelectFace />
    </>
  );
}

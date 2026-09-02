import { cookies } from "next/headers";
import { AppBar } from "../_ui/Chrome";
import { LANE_COOKIE, THEME_COOKIE, themeFrom } from "../_ui/theme";
import { SelectFace } from "../_ui/SelectFace";
import { providers } from "../_ui/db";
import { PROVIDER_LABELS } from "../providers.generated";

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
 * The labels come from `settings_panel.PROVIDERS`; the model does not.
 *
 * That file also carries a model string and it disagrees with the pin:
 * `gemini-3.7-flash` there against `gemini-3.7-flash-medium` in
 * `providers.MODEL_IDS`, which is what every agent task actually invokes. The
 * card shows the pin, because naming a model the system does not run is worse
 * than disagreeing with the other board.
 */

export default async function Settings() {
  const jar = await cookies();
  const theme = themeFrom(jar.get(THEME_COOKIE)?.value);
  // The logo returns to the lane the reader came from, which is the board's
  // own rule and what v2 lost by always sending them to Found.
  const home = `/?lane=${jar.get(LANE_COOKIE)?.value ?? "found"}`;
  const links = await providers();
  return (
    <>
      <AppBar here="settings" theme={theme} home={home} />
      <div className="wrap">
        <section className="settings-page">
          {/* The board's own framing, which v2 reduced to a one-line banner.
              It explains the failover the cards then show an order for, and
              says where credentials live -- both are the answer to "what is
              this page for", and a page of three cards without them is a
              list. */}
          <div className="settings-titlebar">
            <div>
              <p className="eyebrow">Agent providers</p>
              <h2>Choose who does the work</h2>
              <p>
                Jobby checks connected providers in your connection order and
                uses the first account that can run. If its login or usage
                fails, new work moves to the next healthy connection
                automatically. Resume tailoring and application filling both
                use that selected provider on Your Device.
              </p>
            </div>
          </div>

          <div className="split-note">
            <strong>All agent work stays on Your Device.</strong>{" "}
            Jobby creates and checks the resume and cover letter locally, then
            uses the same connected provider to fill the application in the
            local signed-in browser.
          </div>

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
              // `article`, like the board: `.provider-grid > article` is what
              // the stylesheet sizes, and a `div` in its place left the three
              // cards at different heights because the descriptions wrap.
              <article className="provider-card" key={p.provider}
                       data-provider-card={p.provider}>
                <div className="provider-head">
                  <div>
                    <h3>{PROVIDER_LABELS[p.provider]?.name ?? p.provider}</h3>
                    <p>{PROVIDER_LABELS[p.provider]?.agent ?? p.provider} &middot; {p.model}</p>
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
              </article>
              );
            })}
          </div>
          {/* Where credentials live, which is the one thing a reader wants
              settled before connecting anything. v2 dropped it entirely. */}
          <p className="credential-note">
            Jobby stores connection order and health only in the dashboard.
            OpenAI credentials remain in Codex. Anthropic and Gemini API keys
            stay in owner-only files on Your Device and are removed when you
            disconnect. No provider credential is sent to Vercel or Cloudflare.
            Agent work uses that account&rsquo;s usage quota.
          </p>
        </section>
      </div>
      <SelectFace />
    </>
  );
}

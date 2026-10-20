import { cookies } from "next/headers";
import { AppBar } from "../_ui/Chrome";
import { LANE_COOKIE, THEME_COOKIE, themeFrom } from "../_ui/theme";
import { SelectFace } from "../_ui/SelectFace";
import { boardMeta, openQuestions, providers } from "../_ui/db";
import { accountId, writesEnabled } from "../_ui/writes";
import { PROVIDER_LABELS } from "../providers.generated";
import { ProviderCards } from "../_ui/ProviderCards";

/**
 * Settings. One row per provider, and the row is the board's own shape:
 * provider, status, action.
 *
 * The connection itself stays on the device, and the button still has to reach
 * it. That sentence used to end "-- nothing here connects anything", which was
 * true about where a credential lives and stopped anybody asking whether the
 * control sent anything: it was a raw `<button>` with data attributes nothing
 * read, and there was no provider route to read them. `ProviderAction` posts to
 * `/api/providers`, which enqueues to `providerLocal/` where
 * `watch.process_local_provider_controls` has been claiming throughout.
 *
 * The board still has to show connected from not, which is what the `settings_panel.py` already defines this layout:
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
  const [links, open] = await Promise.all([
    providers(), openQuestions().catch(() => null)]);
  const firstOrder = Math.min(...links.map((l) => l.order ?? 1e9));
  // Whether the control is *offered*, never authorisation: `/api/providers`
  // reads the same flag against the same database before it enqueues, so a
  // client that ignores this gets a 503 and a button that says why. Same
  // contract `Act` states for the lanes.
  const writes = await writesEnabled(await accountId()).catch(() => false);
  // Only for the page-wide stopped banner; a board with no meta has none.
  const meta = await boardMeta().catch(() => null);
  return (
    <>
      <AppBar here="settings" theme={theme} home={home}
              stopped={meta?.blocked} openQuestions={open} />
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

          {/* Three cards always, like the board: a provider with no
              `provider_links` row is a provider not connected, not a provider
              that does not exist. The cards then follow the device's live
              status; see `_ui/ProviderCards`. */}
          <ProviderCards enabled={writes} cards={Object.entries(PROVIDER_LABELS)
            .map(([id, label]) => {
              const link = links.find((l) => l.provider === id);
              return {
                id, name: label.name, agent: label.agent,
                model: link?.model ?? null,
                // The failover order, which the old board explains and v2
                // dropped. New work probes connected accounts in this order
                // and takes the first that can invoke its pinned model.
                orderChip: link?.order == null ? null
                  : link.order === firstOrder ? "Default" : `#${link.order}`,
                stored: link ? { status: link.status, health: link.health,
                                 detail: link.detail } : undefined,
              };
            })} />
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

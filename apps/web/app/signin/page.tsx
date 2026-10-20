import { cookies } from "next/headers";
import { AppBar } from "../_ui/Chrome";
import { LANE_COOKIE, THEME_COOKIE, themeFrom } from "../_ui/theme";
import { config, isOwner } from "../_ui/owner";
import { OwnerSignIn, OwnerSignOut } from "../_ui/OwnerSession";
import { openQuestions } from "../_ui/db";

/**
 * Owner sign-in, which unlocks the resume wording editor and nothing else.
 *
 * The rest of the site is public and stays public; this page says so, because
 * a sign-in page on a public board invites the reasonable assumption that the
 * board is what it protects.
 */
export const dynamic = "force-dynamic";

export default async function SignIn() {
  const jar = await cookies();
  const theme = themeFrom(jar.get(THEME_COOKIE)?.value);
  const home = `/?lane=${jar.get(LANE_COOKIE)?.value ?? "found"}`;
  const cfg = config();
  const [owner, open] = await Promise.all([
    cfg.ok ? isOwner() : Promise.resolve(false),
    openQuestions().catch(() => null),
  ]);
  return (
    <>
      <AppBar here="profile" theme={theme} home={home} openQuestions={open} />
      <div className="wrap">
        <section className="profile-page">
          <div className="settings-titlebar">
            <div>
              <p className="eyebrow">Owner sign-in</p>
              <h2>Edit your resume wording</h2>
              <p>
                Everything else on Jobby is public and needs no sign-in. Signing
                in unlocks one thing: the editor for your resume wording on the
                Profile page, which is never shown to anyone else.
              </p>
            </div>
          </div>
          <section className="profile-block">
            {!cfg.ok ? (
              <p className="bank-note">{cfg.why}</p>
            ) : owner ? (
              <div className="bank-act">
                <span className="bank-note">You are signed in as the owner.</span>
                <a className="btn" href="/profile#wording">Open the editor</a>
                <OwnerSignOut />
              </div>
            ) : (
              <OwnerSignIn />
            )}
          </section>
        </section>
      </div>
    </>
  );
}

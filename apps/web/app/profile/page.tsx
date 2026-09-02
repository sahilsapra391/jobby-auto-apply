import { cookies } from "next/headers";
import { AppBar } from "../_ui/Chrome";
import { LANE_COOKIE, THEME_COOKIE, themeFrom } from "../_ui/theme";

/**
 * Profile. A page behind the nav item, so the pill is not a link to a 404.
 * Its own content is not part of this piece.
 */
export const dynamic = "force-dynamic";

export default async function Profile() {
  const jar = await cookies();
  const theme = themeFrom(jar.get(THEME_COOKIE)?.value);
  const home = `/?lane=${jar.get(LANE_COOKIE)?.value ?? "found"}`;
  return (
    <>
      <AppBar here="profile" theme={theme} home={home} />
      <div className="wrap">
        <section className="profile-page">
          <div className="settings-titlebar">
            <h2>Profile</h2>
            <p className="eyebrow">What every tailored resume starts from</p>
          </div>
          <div className="split-note">
            The wording travels in the job bundle, not off the disk. Editing it
            here is its own piece.
          </div>
        </section>
      </div>
    </>
  );
}

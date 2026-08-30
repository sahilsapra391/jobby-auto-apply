import { AppBar } from "../_ui/Chrome";

/**
 * Profile. A page behind the nav item, so the pill is not a link to a 404.
 * Its own content is not part of this piece.
 */
export const dynamic = "force-dynamic";

export default function Profile() {
  return (
    <>
      <AppBar here="profile" />
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

import { LOCKUP, DEFS } from "../chrome.generated";

/** Profile. Read-only, and a stub until its own piece. */
export const dynamic = "force-dynamic";
const raw = (html: string) => ({ dangerouslySetInnerHTML: { __html: html } });

export default function Profile() {
  return (
    <>
      <span {...raw(DEFS)} />
      <header className="appbar">
        <a className="brand" href="/" aria-label="Jobby home" {...raw(LOCKUP)} />
        <nav className="primary-nav">
          <a href="/">Home</a>
          <a href="/profile" aria-current="page">Profile</a>
          <a href="/settings">Settings</a>
        </nav>
      </header>
      <div className="wrap">
        <section className="profile-page">
          <p className="note">Profile arrives with the rest of the surface.</p>
        </section>
      </div>
    </>
  );
}

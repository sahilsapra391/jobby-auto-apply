import { LOCKUP, DEFS } from "../chrome.generated";

/** Settings. Read-only; the row layout arrives with the rest of the surface. */
export const dynamic = "force-dynamic";
const raw = (html: string) => ({ dangerouslySetInnerHTML: { __html: html } });

export default function Settings() {
  return (
    <>
      <span {...raw(DEFS)} />
      <header className="appbar">
        <a className="brand" href="/" aria-label="Jobby home" {...raw(LOCKUP)} />
        <nav className="primary-nav">
          <a href="/">Home</a>
          <a href="/profile">Profile</a>
          <a href="/settings" aria-current="page">Settings</a>
        </nav>
      </header>
      <div className="wrap">
        <section className="settings-page">
          <p className="note">
            Provider connection stays on the device. The row layout -- provider,
            status, action -- arrives with the rest of the surface.
          </p>
        </section>
      </div>
    </>
  );
}

/** Settings. Read-only like everything else under /v2/ during Phase 2. */
export const dynamic = "force-dynamic";

const PROVIDERS = ["OpenAI", "Anthropic", "Gemini"] as const;

export default function Settings() {
  return (
    <div className="jb-wrap">
      <header className="jb-appbar">
        <div className="jb-logo">Jobby</div>
        <nav className="jb-nav">
          <a className="jb-pill" href="/">
            Home
          </a>
          <a className="jb-pill" href="/settings" aria-current="page">
            Settings
          </a>
        </nav>
      </header>

      <section className="jb-panel">
        <p className="jb-note">
          Scaffold only. Provider connection stays on the device and is not
          moving in Phase 2.
        </p>
        {PROVIDERS.map((name) => (
          <div className="jb-row" key={name}>
            <div className="jb-co">
              <span className="jb-mono">{name.slice(0, 2).toUpperCase()}</span>
              {name}
            </div>
            <div className="jb-meta">Your Device</div>
          </div>
        ))}
      </section>
    </div>
  );
}

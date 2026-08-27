#!/usr/bin/env python3
"""Provider and worker settings UI for the generated dashboard."""

import html
import json

PROVIDERS = (
    ("openai", "OpenAI", "Codex", "gpt-5.6-luna"),
    ("anthropic", "Anthropic", "Claude Code", "claude-sonnet-5"),
    ("gemini", "Gemini", "Gemini CLI", "gemini-3.7-flash"),
)


def esc(value):
    return html.escape(str(value if value is not None else ""))


def _cards(status):
    if not isinstance(status, dict):
        return {}
    connections = status.get("connections")
    if isinstance(connections, dict):
        return connections
    providers = status.get("providers")
    if isinstance(providers, list):
        return {item.get("id"): item for item in providers if isinstance(item, dict)}
    if isinstance(providers, dict):
        return providers
    return {}


def public_payload(status):
    """Return only fields that are safe to publish behind the board password."""
    source = _cards(status)
    active = status.get("activeProvider") if isinstance(status, dict) else None
    has_active_field = isinstance(status, dict) and "activeProvider" in status
    ordered_source = None
    if isinstance(status, dict):
        if isinstance(status.get("connections"), dict):
            ordered_source = status["connections"]
        elif isinstance(status.get("providers"), dict):
            ordered_source = status["providers"]
    if not has_active_field and not active and ordered_source is not None:
        ordered = [
            (item.get("connectionOrder"), provider)
            for provider, item in ordered_source.items()
            if isinstance(item, dict)
            and (item.get("connected") is True or item.get("enabled") is True)
            and item.get("connectionOrder")
        ]
        active = min(ordered)[1] if ordered else None
    cards = []
    for provider, name, agent, model in PROVIDERS:
        item = source.get(provider) or {}
        enabled = bool(item.get("enabled", item.get("connected", False)))
        healthy = item.get("healthy")
        raw_state = item.get("status") or item.get("health")
        state = {
            "healthy": "connected",
            "authentication_required": "needs_auth",
            "not_installed": "missing_cli",
            "unavailable": "error",
            "unknown": "checking" if enabled else "disconnected",
            "not_connected": "disconnected",
        }.get(raw_state, raw_state) or (
            "connected" if enabled and healthy is not False else
            "needs_auth" if enabled else "disconnected"
        )
        detail = str(item.get("detail") or item.get("message") or "")[:180]
        if not detail and state == "missing_cli":
            detail = str(item.get("installHint") or "Install this provider CLI on Your Device.")[:180]
        elif not detail and state == "needs_auth":
            detail = str(item.get("authHint") or "Finish provider sign-in on Your Device.")[:180]
        cards.append({
            "id": provider,
            "name": name,
            "agent": agent,
            "model": model,
            "installed": item.get("installed"),
            "enabled": enabled,
            "connected": bool(enabled and state == "connected"),
            "active": bool(item.get("active", provider == active)),
            "status": state,
            "checkedAt": item.get("checkedAt") or item.get("lastCheckedAt"),
            "detail": detail,
        })
    checked = sorted(
        str(item.get("checkedAt") or "") for item in cards
        if item.get("checkedAt")
    )
    return {
        "activeProvider": active,
        "checkedAt": checked[-1] if checked else None,
        "providers": cards,
    }


def _status_label(item):
    labels = {
        "connected": "Connected",
        "disconnected": "Not connected",
        "connecting": "Waiting for sign-in",
        "checking": "Checking",
        "needs_auth": "Sign-in needed",
        "missing_cli": "CLI not installed",
        "error": "Connection error",
    }
    return labels.get(item.get("status"), "Not connected")


def panel(status=None):
    payload = public_payload(status or {})
    payload_json = json.dumps(payload).replace("<", "\\u003c")
    by_id = _cards(payload)
    cards = []
    for provider, name, agent_name, model in PROVIDERS:
        item = by_id[provider]
        active = '<span class="provider-active">Default</span>' \
            if item.get("active") else ""
        action = "disconnect" if item.get("enabled") else "connect"
        button = "Disconnect" if action == "disconnect" else "Connect"
        state_class = "provider-state" + (
            "" if item.get("status") == "connected" else
            " pending" if item.get("status") == "checking" else " bad"
        )
        cards.append(f"""
        <article class="provider-card" data-provider-card="{provider}">
          <div class="provider-head">
            <div><h3>{esc(name)}</h3><p>{esc(agent_name)} · {esc(model)}</p></div>
            {active}
          </div>
          <div class="host-row" data-host="local">
            <div><strong>Your Device</strong><span>Resumes, cover letters, and application forms</span></div>
            <span class="{state_class}" data-provider-state="local">{esc(_status_label(item))}</span>
          </div>
          <button class="btn provider-action" data-provider="{provider}"
                  data-target="local" data-action="{action}">{button}</button>
          <p class="provider-detail" data-provider-detail>{esc(item.get('detail') or '')}</p>
        </article>""")
    return f"""
    <section class="settings-page" aria-labelledby="settings-title">
      <div class="settings-titlebar">
        <div>
          <p class="eyebrow">Agent providers</p>
          <h2 id="settings-title">Choose who does the work</h2>
          <p>Jobby checks connected providers in your connection order and uses the first
             account that can run. If its login or usage fails, new work moves to the next
             healthy connection automatically. Resume tailoring and application filling
             both use that selected provider on Your Device.</p>
        </div>
      </div>
      <div class="split-note">
        <strong>All agent work stays on Your Device.</strong>
        Jobby creates and checks the resume and cover letter locally, then uses the same
        connected provider to fill the application in the local signed-in browser.
      </div>
      <div class="provider-grid">{''.join(cards)}</div>
      <p class="credential-note">Jobby stores connection order and health only in the dashboard.
         OpenAI credentials remain in Codex. Anthropic and Gemini API keys stay in owner-only
         files on Your Device and are removed when you disconnect. No provider credential is
         sent to Vercel or Cloudflare. Agent work uses that account's usage quota.</p>
      <div class="provider-message" id="provider-message" role="status" aria-live="polite"></div>
    </section>
    <script type="application/json" id="jb-provider-data">{payload_json}</script>
    """


CSS = r"""
  .settings-link { display:inline-flex; align-items:center; gap:.42rem; color:var(--ink-2);
    text-decoration:none; border:1px solid var(--hair); border-radius:999px; padding:.46rem .75rem;
    font-size:.86rem; font-weight:650; background:var(--card); }
  .settings-link:hover { color:var(--ink); border-color:var(--ink-3); }
  .settings-page { display:none; max-width:1050px; margin:0 auto; padding:1.5rem 0 4rem; }
  body.settings-view .settings-page { display:block; }
  body.settings-view .top, body.settings-view .segwrap,
  body.settings-view .panel { display:none !important; }
  .settings-titlebar { display:flex; justify-content:space-between; align-items:flex-start;
    gap:2rem; margin-bottom:1.2rem; }
  .settings-titlebar h2 { margin:.15rem 0 .45rem; font-size:clamp(1.65rem,4vw,2.45rem);
    letter-spacing:-.045em; }
  .settings-titlebar p { margin:0; color:var(--ink-2); max-width:680px; line-height:1.5; }
  .eyebrow { color:var(--blue) !important; text-transform:uppercase; letter-spacing:.11em;
    font-size:.72rem; font-weight:750; }
  .split-note { border:1px solid var(--hair); background:var(--sunk); border-radius:16px;
    padding:1rem 1.1rem; color:var(--ink-2); line-height:1.5; margin-bottom:1.1rem; }
  .split-note strong { color:var(--ink); display:block; margin-bottom:.16rem; }
  .provider-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:1rem; }
  .provider-card { background:var(--card); border:1px solid var(--hair); border-radius:18px;
    padding:1rem; box-shadow:0 8px 30px rgba(0,0,0,.045); }
  .provider-head { display:flex; justify-content:space-between; gap:.7rem; align-items:flex-start;
    padding:.15rem .15rem .9rem; }
  .provider-head h3 { margin:0; font-size:1.12rem; }
  .provider-head p { margin:.18rem 0 0; color:var(--ink-3); font-size:.84rem; }
  .provider-active { background:var(--green-bg); color:var(--green); border-radius:999px;
    padding:.28rem .48rem; font-size:.7rem; font-weight:750; white-space:nowrap; }
  .host-row { display:grid; grid-template-columns:minmax(0,1fr) auto; align-items:center;
    gap:.5rem .7rem; border-top:1px solid var(--hair); padding:.85rem .15rem; }
  .host-row > div span { display:block; color:var(--ink-3); font-size:.76rem; margin-top:.12rem; }
  .provider-state { color:var(--green); font-size:.76rem; font-weight:700; }
  .provider-state.pending { color:var(--ink-3); }
  .provider-state.bad { color:var(--red); }
  .provider-action { width:100%; padding:.55rem .75rem; margin-top:.25rem;
    background:var(--btn-g); color:var(--btn-g-ink); }
  .provider-action:disabled { opacity:.58; cursor:wait; }
  .provider-detail { color:var(--red); min-height:1.1em; font-size:.76rem; margin:.2rem .15rem 0; }
  .credential-note { color:var(--ink-3); font-size:.8rem; line-height:1.5; margin:1rem .2rem 0; }
  .provider-message { margin-top:.8rem; color:var(--ink-2); min-height:1.3em; }
  @media (max-width:860px) { .provider-grid { grid-template-columns:1fr; }
    .settings-titlebar { align-items:stretch; } }
  @media (max-width:560px) { .settings-titlebar { flex-direction:column; gap:1rem; }
    .back-board { width:100%; text-align:center; } }
"""


SCRIPT = r"""
  (function () {
    var homeTitle = document.title;
    // One mechanism owns the screen change, and this is it. Home, Profile and
    // Settings are one document: the link is intercepted, the body class flips
    // inside a view transition, and pushState moves the URL. A third view is a
    // third entry in these tables and nothing else.
    var VIEWS = {
      home: { path: '/', klass: null, title: null },
      profile: { path: '/profile', klass: 'profile-view', title: 'Profile | Jobby' },
      settings: { path: '/settings', klass: 'settings-view', title: 'Settings | Jobby' }
    };
    function viewFromPath() {
      var path = location.pathname.replace(/\/+$/, '');
      if (path === '/settings') return 'settings';
      if (path === '/profile') return 'profile';
      return 'home';
    }
    function currentView() {
      if (document.body.classList.contains('settings-view')) return 'settings';
      if (document.body.classList.contains('profile-view')) return 'profile';
      return 'home';
    }
    function paintPrimaryNav(view) {
      document.querySelectorAll('[data-primary-view]').forEach(function (link) {
        if (link.dataset.primaryView === view) link.setAttribute('aria-current', 'page');
        else link.removeAttribute('aria-current');
      });
    }
    function applyView(view) {
      var wanted = VIEWS[view] || VIEWS.home;
      Object.keys(VIEWS).forEach(function (name) {
        var klass = VIEWS[name].klass;
        if (klass) document.body.classList.toggle(klass, name === view);
      });
      document.title = wanted.title || homeTitle;
      paintPrimaryNav(view);
      if (view === 'settings') startProviderPolling();
      // The Profile page holds nothing until it asks for it, so opening it is
      // what starts the fetch.
      if (view === 'profile' && window.jobbyLoadProfile) window.jobbyLoadProfile();
    }
    function swapView(view) {
      if (view === currentView()) return;
      var run = function () { window.scrollTo(0, 0); applyView(view); };
      var still = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      if (!document.startViewTransition || still) { run(); return; }
      var root = document.documentElement;
      root.classList.add('jb-vt');
      var transition = document.startViewTransition(run);
      transition.finished.then(clear, clear);
      // `ready` rejects on its own when a transition is superseded or the
      // document is hidden, and nothing was listening: two nav clicks in quick
      // succession printed "Transition was aborted because of invalid state"
      // as an unhandled rejection. Harmless to the switch, which `finished`
      // already settles, but a console error on a healthy board is a console
      // error nobody reads the next time.
      if (transition.ready && transition.ready.catch) transition.ready.catch(function () {});
      function clear() { root.classList.remove('jb-vt'); }
    }
    document.querySelectorAll('[data-primary-view]').forEach(function (link) {
      link.addEventListener('click', function (event) {
        if (event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        var view = link.dataset.primaryView;
        if (!VIEWS[view] || view === currentView()) return;
        history.pushState({ primaryView: view }, '', VIEWS[view].path);
        swapView(view);
      });
    });
    window.addEventListener('popstate', function () { swapView(viewFromPath()); });
    window.jobbyGoHome = function () {
      if (currentView() === 'home') return;
      history.pushState({ primaryView: 'home' }, '', '/');
      swapView('home');
    };
    var message = document.getElementById('provider-message');
    var labels = {
      connected: 'Connected', disconnected: 'Not connected', connecting: 'Waiting for sign-in',
      checking: 'Checking', needs_auth: 'Sign-in needed', missing_cli: 'CLI not installed',
      error: 'Connection error'
    };
    var hostStatus = { local: {} };
    var pending = {};
    var fastPoll = null;
    function paintAction(card) {
      var provider = card.dataset.providerCard;
      var local = hostStatus.local[provider];
      var button = card.querySelector('.provider-action');
      var badge = card.querySelector('.provider-active');
      var enabled = Boolean(local && local.enabled);
      button.dataset.action = enabled ? 'disconnect' : 'connect';
      button.textContent = enabled ? 'Disconnect' : 'Connect';
      var active = Boolean(local && local.active);
      if (active && !badge) {
        badge = document.createElement('span');
        badge.className = 'provider-active';
        card.querySelector('.provider-head').appendChild(badge);
      }
      if (badge) {
        badge.textContent = 'Default';
        badge.hidden = !active;
      }
    }
    function paintHost(target, status) {
      var byId = {};
      ((status && status.providers) || []).forEach(function (p) { byId[p.id] = p; });
      hostStatus[target] = byId;
      document.querySelectorAll('[data-provider-card]').forEach(function (card) {
        var p = byId[card.dataset.providerCard];
        var state = card.querySelector('[data-provider-state="' + target + '"]');
        if (!state) return;
        var waiting = pending[card.dataset.providerCard];
        if (waiting) {
          var completed = waiting.action === 'disconnect'
            ? Boolean(p && !p.enabled)
            : Boolean(p && p.enabled && p.status !== 'checking');
          if (completed) {
            delete pending[card.dataset.providerCard];
            waiting = null;
          }
        }
        if (waiting) {
          state.textContent = waiting.action === 'connect'
            ? 'Waiting for sign-in' : 'Disconnecting';
          state.className = 'provider-state pending';
          paintAction(card);
          return;
        }
        if (!p) {
          state.textContent = 'Your Device is offline';
          state.className = 'provider-state bad';
          paintAction(card);
          return;
        }
        state.textContent = (labels[p.status] || 'Not connected') +
          (p.active ? ' · Active' : '');
        state.className = 'provider-state' +
          ((p.status === 'connected') ? '' : (p.status === 'checking' ? ' pending' : ' bad'));
        var detail = card.querySelector('[data-provider-detail]');
        if (detail && p.detail) detail.textContent = p.detail;
        paintAction(card);
      });
    }
    function refreshProviders() {
      return fetch('/api/providers', { credentials: 'same-origin', cache: 'no-store' })
        .then(function (r) { return r.json(); })
        .then(function (body) {
          if (!body.ok) throw new Error(body.error || 'Status check failed');
          paintHost('local', body.local);
        })
        .catch(function () { paintHost('local', null); });
    }
    function startFastPolling() {
      if (fastPoll) return;
      fastPoll = setInterval(function () {
        refreshProviders();
        if (!Object.keys(pending).length) {
          clearInterval(fastPoll);
          fastPoll = null;
        }
      }, 1000);
    }
    var slowPoll = null;
    function startProviderPolling() {
      refreshProviders();
      if (slowPoll) return;
      slowPoll = setInterval(function () {
        if (document.body.classList.contains('settings-view')) refreshProviders();
      }, 30000);
    }
    applyView(viewFromPath());
    document.querySelectorAll('.provider-action').forEach(function (button) {
      button.addEventListener('click', function () {
        var body = { provider: button.dataset.provider, target: button.dataset.target,
                     action: button.dataset.action };
        var providerNames = { openai: 'OpenAI', anthropic: 'Anthropic', gemini: 'Gemini' };
        var accountFlow = body.provider === 'openai';
        pending[body.provider] = { action: body.action, started: Date.now() };
        button.disabled = true;
        var card = button.closest('[data-provider-card]');
        var state = card.querySelector('[data-provider-state="local"]');
        state.textContent = body.action === 'connect'
          ? (accountFlow ? 'Requesting sign-in' : 'Requesting API key')
          : 'Disconnecting';
        state.className = 'provider-state pending';
        message.textContent = body.action === 'connect'
          ? 'Requesting the ' + providerNames[body.provider] +
            (accountFlow ? ' sign-in' : ' API key prompt') + ' on Your Device…'
          : 'Disconnecting ' + providerNames[body.provider] + ' from Jobby…';
        startFastPolling();
        fetch('/api/provider', { method: 'POST', credentials: 'same-origin',
          headers: { 'content-type': 'application/json', 'x-jobby-request': '1' },
          body: JSON.stringify(body) })
          .then(function (r) {
            return r.text().then(function (raw) {
              var parsed;
              try { parsed = raw ? JSON.parse(raw) : {}; }
              catch (ignored) { parsed = { error: raw.trim() || ('HTTP ' + r.status) }; }
              return { ok: r.ok, b: parsed, status: r.status };
            });
          })
          .then(function (result) {
            if (!result.ok) throw new Error(result.b.error || 'Could not queue connection');
            message.textContent = body.action === 'connect'
              ? (accountFlow
                ? 'Opening the OpenAI account sign-in in your browser on Your Device. Keep the terminal window open until Jobby confirms the connection.'
                : 'Opening the official ' + providerNames[body.provider] + ' API key page and a private terminal prompt on Your Device. Paste the key there and keep the terminal open until Jobby validates the pinned model.')
              : 'Disconnect requested. Jobby is updating Your Device now.';
            refreshProviders();
          })
          .catch(function (error) {
            delete pending[body.provider];
            message.textContent = error.message;
            refreshProviders();
          })
          .finally(function () {
            button.disabled = false;
          });
      });
    });
  }());
"""

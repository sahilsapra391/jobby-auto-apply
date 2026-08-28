#!/usr/bin/env python3
"""The Profile page: base resume identities, and the Memory Bank.

Two sections, one page, and the second is the point. The Memory Bank is every
question an employer has ever asked that the user answered once, and the loop
that fills it: an application that parks on a question it cannot answer records
that question here, unanswered, so the answer can be given somewhere calmer
than a form at 11pm and every later application finds it.

**This panel renders no data.** It renders the shell, and fetches everything it
shows from `/api/profile`. That is measured rather than stylistic: on
2026-08-25 the board at its embed cap was 3,840,457 bytes against a 3.7 MB
budget, leaving 39,274 bytes, and the bank was 73,978. It does not fit.

It should not fit either. Twenty-two of these entries are compensation and
demographic answers. Fetching them means they are never in a saved copy of the
page, never in a cache of it, and never in the view-source of a board left open
on a screen in an office.

Every string here comes from two untrusted places: questions arrive off
employer forms and answers are the user's own free text. Nothing is
concatenated into markup. Rows are built with createElement and textContent,
the way the Found lane already builds cards.
"""

import html
import json


def esc(value):
    return html.escape(str(value if value is not None else ""))


def tight(text):
    """The same CSS or JS with this file's left margin taken off.

    Everything here ships verbatim into one HTML document that shares a
    measured byte budget with 3,350 embedded job records. Blank lines, trailing
    spaces and the four columns of indentation this file needs to be readable
    are 2.9 KB of that budget and mean nothing to a browser.

    Nothing else is touched. No comment is dropped, no identifier shortened,
    and the JavaScript's own nesting survives, so a stack trace off the real
    board still lands on a line anyone can find in here.
    """
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    return "\n".join(line[4:] if line.startswith("    ") else line for line in lines)


def panel(counts=None):
    """The Profile page shell. Counts only; never an answer, never a question."""
    counts = counts or {}
    total = int(counts.get("total") or 0)
    unanswered = int(counts.get("unanswered") or 0)
    return f"""
    <section class="profile-page" id="profile-page" aria-labelledby="profile-title">
      <div class="settings-titlebar">
        <div>
          <p class="eyebrow">Your profile</p>
          <h2 id="profile-title">What Jobby answers with</h2>
          <p>Your base resumes and every answer Jobby has learned. Edit anything here
             and the next application uses it. Nothing on this page is stored in the
             page itself; it is fetched when you open it, over the same password.</p>
        </div>
      </div>

      <section class="profile-block" aria-labelledby="bases-title">
        <header class="block-head">
          <div>
            <h3 id="bases-title">Base resumes</h3>
            <p>One per right to work. The default is what a posting falls back to when
               its country matches none of them.</p>
          </div>
        </header>
        <div class="identity-grid" data-identities>
          <p class="bank-note" data-identities-empty>Loading your base resumes&hellip;</p>
        </div>
        <p class="profile-message" data-identity-warnings></p>
        <div class="identity-add">
          <button class="btn ghost" type="button" data-base-add>Add a base resume</button>
          <div class="base-form" data-base-form hidden>
            <label>Name<input type="text" data-base-label placeholder="Europe"></label>
            <label>Key<input type="text" data-base-key placeholder="eu"></label>
            <label>Countries<input type="text" data-base-countries
                   placeholder="GB, DE, FR, NL"></label>
            <label>Copy from<span class="vh"> an existing base</span>
              <select data-base-copy></select></label>
            <label>Document
              <span class="base-file">
                <input type="file" accept="application/pdf,.pdf" data-base-file>
                <span class="bank-note" data-base-filename>Using the copied base's PDF.
                  Choose a file to use your own instead.</span>
              </span>
            </label>
            <div class="bank-act">
              <button class="btn" type="button" data-base-save>Create</button>
              <button class="btn ghost" type="button" data-base-cancel>Cancel</button>
            </div>
            <p class="bank-note">Contact details always come from the base you copy.
               A PDF you upload becomes what this base is <em>based on</em>: the text a
               tailored resume is written against. The wording itself still starts from
               the copied base, because the layout a resume renders through is the
               template's, not the uploaded file's.</p>
            <span class="profile-message" data-base-message></span>
          </div>
        </div>

        <details class="wording">
          <summary>
            <span>Edit your resume</span>
            <span class="wording-hint">The document itself. Click any line and type.</span>
          </summary>
          <div class="wording-body">
            <div class="split-note">
              <strong>The words are yours, the layout is the template's.</strong>
              You are editing the real document, so what you see is what the PDF will be.
              Lines can say anything; there is always the same number of them, because the
              layout is what the page-fit check measures against.
            </div>
            <p class="profile-message" data-font-note hidden></p>
            <div class="resume-stage" data-resume-stage>
              <p class="bank-note" data-resume-empty>Loading your resume&hellip;</p>
            </div>
            <div class="wording-act">
              <button class="btn" type="button" data-wording-save>Save resume</button>
              <button class="btn ghost" type="button" data-wording-revert>Revert</button>
              <span class="profile-message" data-wording-message role="status" aria-live="polite"></span>
            </div>
          </div>
        </details>
      </section>

      <section class="profile-block" aria-labelledby="bank-title">
        <header class="block-head">
          <div>
            <h3 id="bank-title">Memory Bank</h3>
            <p>Every question answered once, kept for the next form.
               <span class="bank-count" data-bank-count>{total} stored</span></p>
          </div>
        </header>

        <div class="bank-tools">
          <label class="bank-search">
            <span class="vh">Search the Memory Bank</span>
            <input type="search" data-bank-search placeholder="Search questions and answers"
                   autocomplete="off" spellcheck="false">
          </label>
          <div class="bank-filters" role="group" aria-label="Which kind of record">
            <button class="bank-chip on" type="button" data-bank-kind="question">
              Questions<span class="chip-n" data-kind-n="question"></span>
            </button>
            <button class="bank-chip" type="button" data-bank-kind="policy">
              Your policies<span class="chip-n" data-kind-n="policy"></span>
            </button>
            <button class="bank-chip" type="button" data-bank-kind="story">
              Story bank<span class="chip-n" data-kind-n="story"></span>
            </button>
            <button class="bank-chip" type="button" data-bank-kind="open">
              Needs you<span class="chip-n" data-bank-open>{unanswered}</span>
            </button>
            <!-- Hidden until there is something in it. A filter for an empty
                 set is an invitation to wonder what is missing. -->
            <button class="bank-chip" type="button" data-bank-kind="dismissed" hidden>
              Dismissed<span class="chip-n" data-kind-n="dismissed"></span>
            </button>
          </div>
        </div>
        <div class="bank-cats" role="group" aria-label="Filter by category" data-bank-cats></div>

        <div class="bank-groups" data-bank>
          <p class="bank-note">Loading your answers&hellip;</p>
        </div>
      </section>

      <p class="credential-note">Answers are stored on Your Device and published to the board
         behind this password so you can read and edit them here, including compensation and
         demographics: this is your board and your data. That changes when a second account
         exists, and the per-user encryption in the multi-tenant plan is what changes it.
         One-time codes and verification codes are never stored,
         and neither is an answer to anything Jobby refuses to sign on your behalf.</p>
    </section>
    """


CSS = r"""
  .profile-page { display:none; max-width:1050px; margin:0 auto; padding:1.5rem 0 4rem; }
  body.profile-view .profile-page { display:block; }
  body.profile-view .top, body.profile-view .segwrap,
  body.profile-view .panel, body.profile-view .settings-page,
  body.profile-view .dropped-dock { display:none !important; }
  body.settings-view .profile-page { display:none !important; }

  .vh { position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0 0 0 0);
    white-space:nowrap; }

  /* The one number the nav is allowed to carry. It is warm rather than loud
     because an unanswered question is a thing to get to, not an alarm. */
  .primary-nav a { display:inline-flex; align-items:center; gap:.4rem; }
  .primary-nav .nav-n { background:var(--t-o); color:var(--orange); border-radius:999px;
    padding:.06rem .38rem; font-size:.7rem; font-weight:750; line-height:1.5; }
  .primary-nav a[aria-current="page"] .nav-n { background:var(--sunk); color:var(--ink-2); }
  .primary-nav .nav-n[hidden] { display:none; }

  .profile-block { margin-top:1.6rem; }
  .block-head { display:flex; justify-content:space-between; align-items:flex-start;
    gap:1.5rem; margin-bottom:.85rem; }
  .block-head h3 { margin:0; font-size:1.28rem; letter-spacing:-.03em; }
  .block-head p { margin:.28rem 0 0; color:var(--ink-2); line-height:1.5; max-width:62ch; }
  .bank-count { color:var(--ink-3); white-space:nowrap; }

  /* One card per right to work. Rests on --hair, hovers to --hair-strong, and
     does not move: nothing on this board answers hover by moving except a
     control you press. */
  .identity-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(255px,1fr));
    gap:1rem; }
  .identity-card { background:var(--card); border:1px solid var(--hair); border-radius:18px;
    padding:1rem 1.05rem; box-shadow:var(--sh); transition:border-color .28s var(--ease); }
  .identity-card:hover { border-color:var(--hair-strong); }
  .identity-head { display:flex; justify-content:space-between; align-items:flex-start;
    gap:.7rem; }
  .identity-head h4 { margin:0; font-size:1.05rem; letter-spacing:-.02em; }
  .identity-scope { color:var(--ink-3); font-size:.8rem; margin:.16rem 0 0; }
  .identity-default { background:var(--green-bg); color:var(--green); border-radius:999px;
    padding:.28rem .5rem; font-size:.7rem; font-weight:750; white-space:nowrap; }
  .identity-rows { margin:.85rem 0 0; padding:.8rem 0 0; border-top:1px solid var(--hair);
    display:grid; gap:.42rem; }
  .identity-row { display:grid; grid-template-columns:5.4rem minmax(0,1fr); gap:.6rem;
    font-size:.85rem; align-items:baseline; }
  .identity-row dt { color:var(--ink-3); }
  .identity-row dd { margin:0; color:var(--ink); overflow-wrap:anywhere; }
  .identity-act { margin-top:.9rem; display:flex; gap:.5rem; }
  .identity-act .btn { flex:1; padding:.5rem .7rem; }

  .wording { margin-top:1.15rem; border:1px solid var(--hair); border-radius:18px;
    background:var(--card); box-shadow:var(--sh); overflow:hidden; }
  .wording > summary { cursor:pointer; padding:.95rem 1.05rem; display:flex;
    justify-content:space-between; align-items:center; gap:1rem; font-weight:620;
    transition:background-color .28s var(--ease); }
  .wording > summary::-webkit-details-marker { display:none; }
  .wording > summary:hover { background:var(--hover-wash); }
  .wording-hint { color:var(--ink-3); font-weight:500; font-size:.82rem; }
  .wording-body { padding:0 1.05rem 1.05rem; border-top:1px solid var(--hair); }
  .wording-body .split-note { margin:1rem 0; }
  .wording-field { margin-top:.9rem; }
  .wording-field > label { display:block; color:var(--ink-3); font-size:.78rem;
    font-weight:700; text-transform:uppercase; letter-spacing:.08em; margin-bottom:.35rem; }
  .wording-list { display:grid; gap:.45rem; }
  .wording-role { border:1px solid var(--hair); border-radius:14px; padding:.85rem;
    margin-top:.6rem; background:var(--sunk); }
  .wording-role h5 { margin:0 0 .5rem; font-size:.84rem; color:var(--ink-2);
    text-transform:uppercase; letter-spacing:.07em; }
  .wording-act { margin-top:1.1rem; display:flex; align-items:center; gap:.8rem; }

  .profile-page textarea, .profile-page input[type="text"], .profile-page input[type="search"] {
    width:100%; font:inherit; font-size:.92rem; color:var(--ink); background:var(--card);
    border:1px solid var(--hair); border-radius:11px; padding:.55rem .7rem;
    line-height:1.45; resize:vertical;
    transition:border-color .24s var(--ease),background-color .24s var(--ease); }
  .profile-page textarea:hover, .profile-page input:hover { border-color:var(--hair-strong); }
  .profile-page textarea:focus-visible, .profile-page input:focus-visible {
    outline:2px solid var(--blue); outline-offset:1px; border-color:transparent; }

  .bank-tools { display:flex; gap:.7rem; align-items:center; flex-wrap:wrap;
    margin-bottom:1rem; }
  .bank-search { flex:1 1 18rem; min-width:0; max-width:100%; }
  .bank-filters { display:flex; gap:.25rem; padding:3px; border:1px solid var(--hair);
    border-radius:13px; background:var(--seg); }
  .bank-chip { border-radius:10px; padding:.46rem .8rem; font-size:.86rem; font-weight:600;
    color:var(--ink-2); background:transparent; border:0; cursor:pointer;
    display:inline-flex; align-items:center; gap:.4rem;
    transition:background-color .28s var(--ease),color .24s; }
  /* An unselected chip washes toward the selected chip's own treatment at about
     half strength, so the hover previews what the click gives. */
  .bank-chip:not(.on):hover { background:color-mix(in srgb,var(--t-p) 55%,transparent);
    color:color-mix(in srgb,var(--blue) 62%,var(--ink-2)); }
  .bank-chip.on { background:var(--t-p); color:var(--blue); }
  .chip-n { background:var(--sunk); border-radius:999px; padding:.1rem .38rem;
    font-size:.72rem; font-weight:750; }
  .bank-chip.on .chip-n { background:var(--card); }

  /* Category chips: a second, quieter row under the kind split, because they
     are a filter within a filter and should not compete with it. */
  .bank-cats { display:flex; flex-wrap:wrap; gap:.35rem; margin:-.4rem 0 1rem; }
  .bank-cat { border:1px solid var(--hair); border-radius:999px; padding:.3rem .7rem;
    font-size:.8rem; font-weight:600; color:var(--ink-2); background:var(--card);
    cursor:pointer; display:inline-flex; align-items:center; gap:.35rem;
    transition:border-color .24s var(--ease),background-color .24s var(--ease),color .2s; }
  .bank-cat:hover { border-color:var(--hair-strong); color:var(--ink); }
  .bank-cat.on { background:var(--t-p); border-color:transparent; color:var(--blue); }
  .bank-cat .chip-n { background:var(--sunk); }
  .bank-cat.on .chip-n { background:var(--card); }

  .bank-more { background:none; border:0; padding:0; margin:.35rem 0 0; cursor:pointer;
    color:var(--blue); font-size:.8rem; font-weight:650; }
  .bank-more:hover { text-decoration:underline; }
  .bank-alts { margin:.4rem 0 0; padding-left:1.1rem; color:var(--ink-3); font-size:.84rem;
    line-height:1.5; }

  /* The resume, shown as the document. Scaled to fit rather than clipped: it is
     816px wide at 96dpi and the panel is narrower on most screens. */
  .resume-stage { position:relative; overflow:hidden; border:1px solid var(--hair);
    border-radius:14px; background:#fff; margin-top:.4rem; }
  .resume-frame { border:0; display:block; background:#fff; }
  .identity-add { margin-top:1rem; }
  .base-form { margin-top:.8rem; border:1px solid var(--hair); border-radius:16px;
    padding:1rem; background:var(--card); display:grid; gap:.6rem; }
  .base-form label { display:grid; gap:.25rem; color:var(--ink-3); font-size:.8rem;
    font-weight:650; }
  .base-file { display:grid; gap:.3rem; }
  .base-file input[type="file"] { font:inherit; font-size:.86rem; color:var(--ink-2); }
  .base-file input[type="file"]::file-selector-button { font:inherit; font-size:.86rem;
    padding:.4rem .7rem; margin-right:.6rem; border-radius:10px; cursor:pointer;
    border:1px solid var(--hair); background:var(--card); color:var(--ink); }
  .base-file input[type="file"]::file-selector-button:hover { border-color:var(--hair-strong); }
  .base-form select { width:100%; font:inherit; font-size:.92rem; color:var(--ink);
    background:var(--card); border:1px solid var(--hair); border-radius:11px;
    padding:.55rem .7rem; }

  .bank-group { margin-top:1.4rem; }
  .bank-group:first-child { margin-top:0; }
  .bank-group > h4 { margin:0 0 .2rem; font-size:.78rem; font-weight:750;
    text-transform:uppercase; letter-spacing:.1em; color:var(--ink-3); }
  .bank-group.open > h4 { color:var(--orange); }
  .bank-group > p { margin:0 0 .7rem; color:var(--ink-3); font-size:.84rem; }
  .bank-list { display:grid; gap:.6rem; }

  .bank-row { background:var(--card); border:1px solid var(--hair); border-radius:16px;
    padding:.9rem 1rem; box-shadow:var(--sh); transition:border-color .28s var(--ease); }
  .bank-row:hover { border-color:var(--hair-strong); }
  .bank-row.open { border-left:3px solid var(--orange); }
  .bank-q { margin:0; font-weight:620; line-height:1.45; overflow-wrap:anywhere; }
  .bank-a { margin:.4rem 0 0; color:var(--ink-2); line-height:1.5; white-space:pre-wrap;
    overflow-wrap:anywhere; }
  .bank-a.none { color:var(--orange); font-style:italic; }
  .bank-meta { margin:.6rem 0 0; display:flex; flex-wrap:wrap; gap:.35rem .5rem;
    align-items:center; }
  .bank-tag { background:var(--sunk); color:var(--ink-3); border-radius:999px;
    padding:.16rem .48rem; font-size:.72rem; font-weight:650; }
  .bank-tag.state-sensitive { background:var(--t-red); color:var(--red); }
  .bank-tag.state-missing { background:var(--t-o); color:var(--orange); }

  /* A dismissed row is struck through and set back, because it is still a real
     record the loop consults and not a deleted one. It stays legible enough to
     read and be put back, and quiet enough not to compete with the questions
     that are still outstanding. */
  .bank-row.struck { background:transparent; box-shadow:none; border-style:dashed; }
  .bank-row.struck .bank-q { text-decoration:line-through; color:var(--ink-3);
    font-weight:560; }
  .bank-row.struck .bank-a.none { color:var(--ink-3); }
  .bank-tag.state-dismissed { background:var(--sunk); color:var(--ink-3);
    text-decoration:none; }
  .bank-when { color:var(--ink-3); font-size:.76rem; margin-left:auto; }
  .bank-act { margin-top:.7rem; display:flex; gap:.45rem; flex-wrap:wrap; }
  .bank-act .btn { padding:.44rem .8rem; font-size:.86rem; }
  .bank-edit { margin-top:.7rem; display:grid; gap:.55rem; }
  .bank-sensitive { display:flex; align-items:center; gap:.5rem; color:var(--ink-2);
    font-size:.85rem; cursor:pointer; }
  .bank-sensitive input { accent-color:var(--blue); width:1rem; height:1rem; margin:0; }
  .bank-note { color:var(--ink-3); line-height:1.5; margin:0; }
  .bank-empty { border:1px dashed var(--hair-strong); border-radius:16px;
    padding:1.4rem 1.1rem; text-align:center; color:var(--ink-3); }
  .profile-message { color:var(--ink-2); font-size:.86rem; min-height:1.2em; }
  .profile-message.bad { color:var(--red); }
  .profile-error { border:1px solid var(--hair); border-left:3px solid var(--red);
    background:var(--card); border-radius:16px; padding:1rem 1.1rem; color:var(--ink-2);
    line-height:1.5; }
  .profile-error strong { display:block; color:var(--ink); margin-bottom:.2rem; }

  @media (max-width:640px) {
    .block-head { flex-direction:column; gap:.5rem; }
    .bank-tools { flex-direction:column; align-items:stretch; }
    /* A flex item's min-width is auto, so `overflow-x:auto` alone never
       engages: the track just grows and takes the document with it. Measured
       at 375px, the four kind chips pushed the page to 459px wide. */
    .bank-filters { overflow-x:auto; min-width:0; max-width:100%;
      scrollbar-width:none; }
    .bank-filters::-webkit-scrollbar { display:none; }
    .bank-chip { flex:0 0 auto; }
    .bank-cats { min-width:0; max-width:100%; }
    .resume-stage { max-width:100%; }
    .bank-when { margin-left:0; }
    .identity-row { grid-template-columns:1fr; gap:.1rem; }
  }
"""


SCRIPT = r"""
  (function () {
    var page = document.getElementById('profile-page');
    if (!page || !page.querySelector) return;

    // Fetched, never embedded. See the module docstring for the measurement.
    var data = null;
    var loading = false;
    var failure = null;
    var query = '';
    var kind = 'question';
    var category = null;

    var bankHost = page.querySelector('[data-bank]');
    var identityHost = page.querySelector('[data-identities]');

    function el(tag, className, text) {
      var node = document.createElement(tag);
      if (className) node.className = className;
      // Questions come off employer forms and answers are free text the user
      // typed. Neither is ever concatenated into markup.
      if (text !== undefined && text !== null) node.textContent = String(text);
      return node;
    }

    function editId(prefix) {
      var rand = Math.random().toString(36).slice(2, 10);
      return prefix + '-' + Date.now().toString(36) + '-' + rand;
    }

    function post(path, body) {
      return fetch(path, {
        method: 'POST',
        headers: { 'content-type': 'application/json', 'x-jobby-request': '1' },
        body: JSON.stringify(body)
      });
    }

    // The house pattern: remember what was sent, re-send on load whatever the
    // published state has not caught up with. Restoring only the appearance is
    // what once let a queue wipe destroy fourteen requests while the board
    // still drew them as done.
    var PENDING_KEY = 'jobby.profileEdits';

    function pending() {
      try { return JSON.parse(localStorage.getItem(PENDING_KEY) || '[]'); }
      catch (e) { return []; }
    }

    function remember(entry) {
      var all = pending().filter(function (x) { return x.editId !== entry.editId; });
      all.push(entry);
      try { localStorage.setItem(PENDING_KEY, JSON.stringify(all.slice(-60))); }
      catch (e) {}
    }

    function forget(ids) {
      var drop = {};
      ids.forEach(function (id) { drop[id] = true; });
      var left = pending().filter(function (x) { return !drop[x.editId]; });
      try { localStorage.setItem(PENDING_KEY, JSON.stringify(left)); } catch (e) {}
    }

    function replay() {
      var held = pending();
      if (!held.length) return;
      var byKey = {};
      (data && data.answers ? data.answers : []).forEach(function (a) { byKey[a.key] = a; });
      var settled = [];
      held.forEach(function (item) {
        if (item.kind === 'answer') {
          var live = byKey[item.body.key];
          // The store moves the version past the one the edit was made against
          // when it applies. Past it means landed; a deleted entry is gone.
          // A delete lands by the entry going away. Every other action lands
          // by the version moving past the one the click was made against,
          // dismiss and restore included: both keep the record and bump it.
          var landed = item.body.action === 'delete'
            ? !live
            : live && Number(live.version || 0) > Number(item.body.version || 0);
          if (landed) { settled.push(item.editId); return; }
        }
        post(item.path, item.body).catch(function () {});
      });
      if (settled.length) forget(settled);
    }

    function answered(entry) {
      return Boolean(entry && entry.value !== null && String(entry.value || '').trim());
    }

    // A question the user has refused to answer. It carries no value, like an
    // outstanding one, and means the opposite thing: the device's grow-only
    // loop reads this state and stops opening the question when the next
    // employer asks it.
    function isDismissed(entry) {
      return Boolean(entry) && entry.state === 'dismissed';
    }

    function isOpen(entry) {
      return !answered(entry) && entry.remembered !== false && !isDismissed(entry);
    }

    function stamp(value) {
      if (!value) return '';
      var when = new Date(value);
      if (isNaN(when.getTime())) return '';
      return when.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
    }

    // --- base resumes --------------------------------------------------------

    function identityCard(entry) {
      var card = el('article', 'identity-card');
      var head = el('div', 'identity-head');
      var titles = el('div');
      titles.appendChild(el('h4', null,
        entry.label || (entry.key ? String(entry.key).toUpperCase() : 'Base')));
      titles.appendChild(el('p', 'identity-scope', claimText(entry)));
      head.appendChild(titles);
      if (entry.isDefault) head.appendChild(el('span', 'identity-default', 'Default'));
      card.appendChild(head);

      var rows = el('dl', 'identity-rows');
      var contact = entry.contact || {};
      [['Location', contact.location], ['Phone', contact.phone],
       ['Address', contact.oneLine], ['Document', entry.resumeRef]].forEach(function (pair) {
        if (!pair[1]) return;
        var row = el('div', 'identity-row');
        row.appendChild(el('dt', null, pair[0]));
        row.appendChild(el('dd', null, pair[1]));
        rows.appendChild(row);
      });
      card.appendChild(rows);

      if (!entry.isDefault) {
        var act = el('div', 'identity-act');
        var button = el('button', 'btn ghost', 'Make default');
        button.type = 'button';
        button.addEventListener('click', function () {
          button.disabled = true;
          button.textContent = 'Saving…';
          var body = { editId: editId('res'), action: 'default', key: entry.key };
          remember({ editId: body.editId, kind: 'resume', path: '/api/resume', body: body });
          (data.identities || []).forEach(function (other) {
            other.isDefault = other.key === entry.key;
          });
          post('/api/resume', body).catch(function () {});
          renderIdentities();
        });
        act.appendChild(button);

        // Two deliberate clicks, because removing the base a region renders
        // from is not something to do by brushing past a button. Your device
        // refuses it outright when open work still depends on it, and that
        // refusal is what the message below reports.
        var remove = el('button', 'btn ghost', 'Remove');
        remove.type = 'button';
        var armed = false;
        var note = el('span', 'profile-message');
        remove.addEventListener('click', function () {
          if (!armed) {
            armed = true;
            remove.textContent = 'Confirm remove';
            note.className = 'profile-message';
            note.textContent = 'Click again to remove this base resume.';
            return;
          }
          remove.disabled = true;
          var body = { editId: editId('res'), action: 'remove', key: entry.key };
          remember({ editId: body.editId, kind: 'resume', path: '/api/resume', body: body });
          post('/api/resume', body).then(function (response) {
            if (response && response.ok) {
              note.className = 'profile-message';
              note.textContent = 'Queued. It disappears here once your device applies it.';
            } else {
              remove.disabled = false;
              armed = false;
              remove.textContent = 'Remove';
              note.className = 'profile-message bad';
              note.textContent = 'Your device would not remove it. Nothing changed.';
            }
          }).catch(function () {
            remove.disabled = false;
            armed = false;
            remove.textContent = 'Remove';
            note.className = 'profile-message bad';
            note.textContent = 'That did not reach the queue. Nothing changed.';
          });
        });
        act.appendChild(remove);
        card.appendChild(act);
        card.appendChild(note);
      }
      return card;
    }

    // The rule in words, generated from the rule rather than typed by hand. A
    // selector the reader cannot read is a selector they cannot correct.
    function claimText(entry) {
      var countries = entry.countryScope || [];
      if (entry.isDefault && !countries.length) return 'Used when nothing else matches';
      if (!countries.length) return 'Claims no country, so it is never selected';
      var shown = countries.slice(0, 6).join(', ');
      var rest = countries.length > 6 ? ' and ' + (countries.length - 6) + ' more' : '';
      return 'Used for ' + shown + rest + (entry.isDefault ? ', and when nothing matches' : '');
    }

    function paintIdentityWarnings() {
      var node = page.querySelector('[data-identity-warnings]');
      if (!node) return;
      node.textContent = '';
      var list = (data && data.identities) || [];
      var claims = {};
      list.forEach(function (entry) {
        (entry.countryScope || []).forEach(function (c) {
          (claims[c] = claims[c] || []).push(entry.key);
        });
      });
      var overlaps = Object.keys(claims).filter(function (c) { return claims[c].length > 1; });
      var messages = [];
      // Surfaced, never resolved silently: two bases claiming one country is a
      // priority question, and the person who wrote both rules should answer it.
      if (overlaps.length) {
        messages.push(overlaps.map(function (c) {
          return c + ' is claimed by ' + claims[c].join(' and ');
        }).join('; ') + '. The higher priority wins.');
      }
      if (!list.some(function (e) { return e.isDefault; })) {
        messages.push('No base is marked default, so a posting matching none of them has '
          + 'nothing to fall back to.');
      }
      node.textContent = messages.join(' ');
      node.className = messages.length ? 'profile-message bad' : 'profile-message';
    }

    function renderIdentities() {
      if (!identityHost) return;
      identityHost.textContent = '';
      var list = (data && data.identities) || [];
      if (!list.length) {
        identityHost.appendChild(el('p', 'bank-note',
          'No base resume is on record for this account yet.'));
        return;
      }
      list.forEach(function (entry) { identityHost.appendChild(identityCard(entry)); });
      paintIdentityWarnings();
      var copy = page.querySelector('[data-base-copy]');
      if (copy) {
        copy.textContent = '';
        list.forEach(function (entry) {
          var opt = document.createElement('option');
          opt.value = entry.key;
          opt.textContent = entry.label || entry.key;
          copy.appendChild(opt);
        });
      }
    }

    var baseAdd = page.querySelector('[data-base-add]');
    var baseForm = page.querySelector('[data-base-form]');
    if (baseAdd && baseAdd.addEventListener && baseForm) {
      baseAdd.addEventListener('click', function () { baseForm.hidden = !baseForm.hidden; });
      var cancel = page.querySelector('[data-base-cancel]');
      if (cancel) cancel.addEventListener('click', function () { baseForm.hidden = true; });
      var filePicker = page.querySelector('[data-base-file]');
      var fileName = page.querySelector('[data-base-filename]');
      if (filePicker && filePicker.addEventListener && fileName) {
        filePicker.addEventListener('change', function () {
          var f = filePicker.files && filePicker.files[0];
          fileName.textContent = f
            ? f.name + ' (' + Math.round(f.size / 1024) + ' KB)'
            : "Using the copied base's PDF. Choose a file to use your own instead.";
        });
      }
      var save = page.querySelector('[data-base-save]');
      if (save) save.addEventListener('click', function () {
        var message = page.querySelector('[data-base-message]');
        var key = String((page.querySelector('[data-base-key]') || {}).value || '').trim();
        var label = String((page.querySelector('[data-base-label]') || {}).value || '').trim();
        var raw = String((page.querySelector('[data-base-countries]') || {}).value || '');
        var copyFrom = String((page.querySelector('[data-base-copy]') || {}).value || '');
        var countries = raw.split(/[,\s]+/).map(function (c) { return c.trim().toUpperCase(); })
          .filter(Boolean);
        function fail(text) {
          if (message) { message.className = 'profile-message bad'; message.textContent = text; }
        }
        if (!/^[A-Za-z0-9_-]+$/.test(key)) return fail('The key can only be letters, numbers, - and _.');
        if (!countries.length) return fail('Name at least one country, like GB or DE.');
        if (!countries.every(function (c) { return /^[A-Z]{2}$/.test(c); })) {
          return fail('Countries are two-letter codes, like GB, DE, FR.');
        }
        var body = { editId: editId('base'), action: 'add', key: key, label: label,
                     countries: countries, copyFrom: copyFrom };
        save.disabled = true;

        function queueTheBase() {
          remember({ editId: body.editId, kind: 'resume', path: '/api/resume', body: body });
          return post('/api/resume', body).then(function (response) {
            save.disabled = false;
            if (response && response.ok) {
              if (message) {
                message.className = 'profile-message';
                message.textContent = 'Queued. It appears here once your device applies it.';
              }
            } else { fail('Your device would not add it. Nothing changed.'); }
          });
        }

        var picker = page.querySelector('[data-base-file]');
        var chosen = picker && picker.files && picker.files[0];
        if (!chosen) {
          queueTheBase().catch(function () {
            save.disabled = false; fail('That did not reach the queue.');
          });
          return;
        }
        if (chosen.size > 2 * 1024 * 1024) {
          save.disabled = false;
          return fail('That file is ' + Math.round(chosen.size / 1024) + ' KB. The limit is 2 MB.');
        }
        // The document goes to a key of its own and the queued request carries
        // only its name, so a shared key the watcher reads every few seconds
        // never holds a PDF. Uploaded first: a base pointing at a document that
        // failed to arrive is worse than no base at all.
        if (message) {
          message.className = 'profile-message';
          message.textContent = 'Uploading the document…';
        }
        var reader = new FileReader();
        reader.onerror = function () { save.disabled = false; fail('That file could not be read.'); };
        reader.onload = function () {
          var encoded = String(reader.result || '');
          encoded = encoded.slice(encoded.indexOf(',') + 1);
          var uploadId = editId('doc');
          post('/api/resume-upload', { uploadId: uploadId, pdf: encoded })
            .then(function (response) {
              if (!response || !response.ok) {
                save.disabled = false;
                return response.json().then(function (payload) {
                  fail(payload && payload.error ? payload.error : 'That upload was refused.');
                }).catch(function () { fail('That upload was refused.'); });
              }
              body.uploadId = uploadId;
              return queueTheBase();
            })
            .catch(function () { save.disabled = false; fail('That upload did not reach the board.'); });
        };
        reader.readAsDataURL(chosen);
      });
    }

    // --- the wording ---------------------------------------------------------

    // --- the resume, edited as a document ------------------------------------
    //
    // A same-origin `srcdoc` iframe, not a Shadow DOM, and that was measured
    // rather than chosen. Both survive the board's CSP and both accept
    // `contenteditable`, but a shadow root has no `html` or `body`, and the
    // resume's own stylesheet targets exactly those. Rendered in a shadow root
    // the banner comes out in `system-ui`; in the iframe it comes out in
    // `"Calibri Light", Calibri, sans-serif`, which is what the PDF uses. A
    // preview with the wrong typeface cannot claim to be what the document will
    // look like, and that claim is the entire feature.
    //
    // The slots below are the same ones `resume_build.extract` matches on, so
    // reading edits back out is the inverse of the render rather than a second
    // parser with its own opinions.
    var SLOTS = [
      { field: 'banner', sel: '.banner', many: false },
      { field: 'summary', sel: '.body.summary', many: false },
      { field: 'areasOfExpertise', sel: '.aoe', many: true },
      { field: 'lede', sel: '.body.lede', many: true },
      { field: 'bullets', sel: 'ul', many: true }
    ];

    var stage = page.querySelector('[data-resume-stage]');
    var frame = null;

    function editableDoc() {
      return frame && frame.contentDocument;
    }

    function renderResume() {
      if (!stage) return;
      stage.textContent = '';
      var html = data && data.resumeRender;
      if (!html) {
        stage.appendChild(el('p', 'bank-note',
          'Your device could not render the resume, so there is nothing to edit here. '
          + 'The wording is still used for tailored resumes.'));
        return;
      }
      frame = document.createElement('iframe');
      frame.className = 'resume-frame';
      frame.setAttribute('sandbox', 'allow-same-origin');
      frame.setAttribute('title', 'Your resume, editable');
      frame.srcdoc = html;
      frame.addEventListener('load', function () {
        var doc = editableDoc();
        if (!doc) return;
        SLOTS.forEach(function (slot) {
          Array.prototype.forEach.call(doc.querySelectorAll(slot.sel), function (node) {
            if (slot.field === 'bullets') {
              Array.prototype.forEach.call(node.querySelectorAll('li'), function (li) {
                li.setAttribute('contenteditable', 'true');
              });
            } else {
              node.setAttribute('contenteditable', 'true');
              // The bullet glyph is the template's, not the user's text.
              Array.prototype.forEach.call(node.querySelectorAll('.b'), function (b) {
                b.setAttribute('contenteditable', 'false');
              });
            }
          });
        });
        // The document is 816px wide at 96dpi and the panel is narrower, so it
        // is scaled to fit rather than clipped or scrolled sideways.
        fitFrame();
        reportFonts(doc);
      });
      stage.appendChild(frame);
    }

    function fitFrame() {
      if (!frame || !stage) return;
      var available = stage.clientWidth || 816;
      var scale = Math.min(1, available / 816);
      frame.style.width = '816px';
      frame.style.transform = 'scale(' + scale + ')';
      frame.style.transformOrigin = 'top left';
      var doc = editableDoc();
      var pages = doc ? doc.querySelectorAll('.page').length || 1 : 1;
      frame.style.height = (1056 * pages) + 'px';
      stage.style.height = Math.ceil(1056 * pages * scale) + 'px';
    }
    if (window.addEventListener) window.addEventListener('resize', fitFrame);

    function reportFonts(doc) {
      // A surface that cannot be exact says so. The PDF is rendered by Chrome
      // on the device that has Calibri Light; a phone almost certainly does
      // not, and the preview there is an approximation rather than a lie.
      var note = page.querySelector('[data-font-note]');
      if (!note || !doc.fonts || !doc.fonts.check) return;
      var ok = false;
      try { ok = doc.fonts.check('12pt "Calibri Light"'); } catch (e) { ok = false; }
      note.hidden = ok;
      if (!ok) {
        note.textContent = 'This device does not have Calibri Light, so the preview '
          + 'approximates the spacing. The PDF is rendered on your Mac, which does.';
      }
    }

    function collectResume() {
      // The inverse of the render, reading the same slots extract writes.
      var doc = editableDoc();
      if (!doc) return null;
      var base = (data && data.resumeContent) || {};
      var text = function (node) { return String(node.textContent || '').trim(); };
      // An area of expertise is rendered as `<span class="b">•</span>&nbsp;&nbsp;`
      // followed by the words, and `extract` captures only the words. Reading
      // the whole node instead puts the glyph and its two non-breaking spaces
      // into the content, and the next render draws a second bullet in front of
      // the first, compounding on every save. Caught in the browser, because a
      // save with no edits looked like a no-op and was not one.
      var areaText = function (node) {
        var clone = node.cloneNode(true);
        Array.prototype.forEach.call(clone.querySelectorAll('.b'), function (b) {
          b.parentNode.removeChild(b);
        });
        return String(clone.textContent || '').replace(/^[\s\u00a0]+/, '').trim();
      };
      var out = {
        banner: doc.querySelector('.banner') ? text(doc.querySelector('.banner')) : base.banner,
        summary: doc.querySelector('.body.summary')
          ? text(doc.querySelector('.body.summary')) : base.summary,
        areasOfExpertise: Array.prototype.map.call(doc.querySelectorAll('.aoe'), areaText),
        technical: [],
        roles: []
      };
      Array.prototype.forEach.call(doc.querySelectorAll('.tech'), function (row, i) {
        var existing = (base.technical || [])[i] || {};
        var bold = row.querySelector('b');
        var label = bold ? String(bold.textContent || '').replace(/:\s*$/, '').trim()
          : existing.label;
        var items = text(row).replace(/^[^:]*:\s*/, '');
        out.technical.push({ label: label, items: items });
      });
      var ledes = doc.querySelectorAll('.body.lede');
      var lists = doc.querySelectorAll('ul');
      var count = Math.max(ledes.length, lists.length);
      for (var i = 0; i < count; i++) {
        out.roles.push({
          lede: ledes[i] ? text(ledes[i]) : ((base.roles || [])[i] || {}).lede || '',
          bullets: lists[i]
            ? Array.prototype.map.call(lists[i].querySelectorAll('li'), text)
            : (((base.roles || [])[i] || {}).bullets || [])
        });
      }
      return out;
    }

    var wordingSave = page.querySelector('[data-wording-save]');
    var wordingRevert = page.querySelector('[data-wording-revert]');
    var wordingMessage = page.querySelector('[data-wording-message]');

    if (wordingRevert && wordingRevert.addEventListener) {
      wordingRevert.addEventListener('click', function () {
        renderResume();
        if (wordingMessage) {
          wordingMessage.className = 'profile-message';
          wordingMessage.textContent = 'Reverted to the saved version.';
        }
      });
    }

    if (wordingSave && wordingSave.addEventListener) {
      wordingSave.addEventListener('click', function () {
        var content = collectResume();
        if (!content) return;
        var body = { editId: editId('wrd'), action: 'content', content: content };
        wordingSave.disabled = true;
        if (wordingMessage) {
          wordingMessage.className = 'profile-message';
          wordingMessage.textContent = 'Saving…';
        }
        remember({ editId: body.editId, kind: 'resume', path: '/api/resume', body: body });
        data.resumeContent = content;
        post('/api/resume', body).then(function (response) {
          wordingSave.disabled = false;
          if (!wordingMessage) return;
          if (response && response.ok) {
            wordingMessage.className = 'profile-message';
            wordingMessage.textContent = 'Saved. Your next tailored resume starts from this.';
          } else {
            wordingMessage.className = 'profile-message bad';
            wordingMessage.textContent = 'Your device would not take that. Nothing was changed.';
          }
        }).catch(function () {
          wordingSave.disabled = false;
          if (!wordingMessage) return;
          wordingMessage.className = 'profile-message bad';
          wordingMessage.textContent = 'That did not reach the queue. Nothing was changed.';
        });
      });
    }

    // --- the Memory Bank -----------------------------------------------------

    function matches(entry) {
      // A dismissed row appears under its own filter and nowhere else. Leaving
      // it in the question list would have made the click change nothing the
      // user can see, which is the same as not having worked.
      if (!matches_kind(entry)) return false;
      if (category && entry.category !== category) return false;
      if (!query) return true;
      var hay = String(entry.question || '') + ' ' + (entry.tags || []).join(' ')
        + ' ' + String(entry.value || '');
      return hay.toLowerCase().indexOf(query) !== -1;
    }

    // **Group for display. Never merge records. Keep every key.**
    //
    // Eleven rows say "Yes" to what a reader sees as one question: six ways of
    // asking whether sponsorship is required, five ways of asking about
    // relocation. Collapsing those to two rows is the point of this function.
    //
    // What it must never do is merge the records behind them. The bank holds
    // fourteen sponsorship-and-authorisation keys because fourteen employers
    // word the question fourteen ways, and each key is what makes the lookup
    // match that employer's form. And seven of them answer a bare "Yes" while
    // meaning opposite facts: "Do you require visa sponsorship?" is Yes, and
    // "Are you legally authorized to work in the United States?" is also Yes.
    // Merge on the answer and one of them starts filling forms with the
    // opposite of the truth, under a real name, unrecallable.
    //
    // So the grouping key is computed on the device, in answer_categories.py,
    // from a narrow topic plus the exact answer, with the topics written so
    // opposite-polarity questions cannot land in the same one. A row with no
    // groupKey stands alone, which is the safe default and the common case.
    function grouped(entries) {
      var byKey = {};
      var order = [];
      entries.forEach(function (entry) {
        var key = entry.groupKey;
        if (!key) { order.push({ lead: entry, members: [entry] }); return; }
        if (!byKey[key]) {
          byKey[key] = { lead: entry, members: [] };
          order.push(byKey[key]);
        }
        byKey[key].members.push(entry);
      });
      return order;
    }

    function bankRow(entry, members) {
      members = members || [entry];
      var row = el('article', 'bank-row' + (isOpen(entry) ? ' open' : '')
        + (isDismissed(entry) ? ' struck' : ''));
      row.appendChild(el('p', 'bank-q', entry.question || entry.key || 'Untitled question'));
      if (members.length > 1) {
        // One row, every record kept. The others are the same fact asked in
        // different words, and each of their keys is what matches the employer
        // who used those words.
        var more = el('button', 'bank-more',
          'asked ' + members.length + ' ways');
        more.type = 'button';
        var list = el('ul', 'bank-alts');
        list.hidden = true;
        members.slice(1).forEach(function (other) {
          list.appendChild(el('li', null, other.question || other.key));
        });
        more.addEventListener('click', function () {
          list.hidden = !list.hidden;
          more.textContent = list.hidden
            ? 'asked ' + members.length + ' ways'
            : 'hide the other wordings';
        });
        row.appendChild(more);
        row.appendChild(list);
      }

      var body = el('p', 'bank-a');
      function paintValue() {
        if (!answered(entry)) {
          body.className = 'bank-a none';
          body.textContent = isDismissed(entry)
            ? 'You dismissed this. Jobby will not ask again.'
            : (entry.remembered === false
              ? 'Filled when asked, deliberately not kept.'
              : 'No answer yet.');
        } else {
          body.className = 'bank-a';
          body.textContent = entry.value;
        }
      }
      paintValue();
      row.appendChild(body);

      var meta = el('p', 'bank-meta');
      var stateTag = el('span', 'bank-tag state-' + (entry.state || 'confirmed'),
        isOpen(entry) ? 'needs you' : (entry.state || 'confirmed'));
      if (isDismissed(entry) && entry.dismissedAt) {
        stateTag.title = 'Dismissed ' + stamp(entry.dismissedAt);
      }
      meta.appendChild(stateTag);
      (entry.tags || []).forEach(function (tag) {
        if (tag === 'unanswered') return;
        meta.appendChild(el('span', 'bank-tag', tag));
      });
      var asked = (entry.askedBy || []).length;
      if (asked) {
        meta.appendChild(el('span', 'bank-tag',
          asked === 1 ? 'asked by 1 application' : 'asked by ' + asked + ' applications'));
      }
      var when = stamp(entry.answeredAt || entry.updatedAt || entry.lastAskedAt);
      if (when) meta.appendChild(el('span', 'bank-when', 'Updated ' + when));
      row.appendChild(meta);

      var act = el('div', 'bank-act');
      var edit = el('button', 'btn ghost', answered(entry) ? 'Edit' : 'Answer');
      edit.type = 'button';
      if (!isDismissed(entry)) act.appendChild(edit);
      var rowNote = el('span', 'profile-message');

      // Two clicks, and the second one is a different word. The same shape
      // Forget and Remove already use, in one place instead of a third copy:
      // the button says what it will do, arms, then does it.
      function armed(button, resting, confirming, sending, build, onOk) {
        var live = false;
        button.addEventListener('click', function () {
          if (!live) {
            live = true;
            button.textContent = confirming;
            rowNote.className = 'profile-message';
            rowNote.textContent = '';
            return;
          }
          button.disabled = true;
          button.textContent = sending;
          var payload = build();
          remember({ editId: payload.editId, kind: 'answer', path: '/api/answer',
                     body: payload });
          function failed(text) {
            button.disabled = false;
            live = false;
            button.textContent = resting;
            rowNote.className = 'profile-message bad';
            rowNote.textContent = text;
          }
          post('/api/answer', payload).then(function (response) {
            return response.json().then(function (payload2) {
              return { ok: response.ok, payload: payload2 };
            }).catch(function () { return { ok: response.ok, payload: null }; });
          }).then(function (result) {
            if (!result.ok) {
              // Named, not "something went wrong". A Found-lane click said
              // exactly that for five days while the server was answering with
              // the reason every time.
              failed('Your device would not take that. '
                + ((result.payload && result.payload.error) || 'Nothing changed.'));
              return;
            }
            onOk();
            render();
          }).catch(function () {
            failed('That did not reach the queue. Nothing changed.');
          });
        });
      }

      if (isOpen(entry)) {
        // The way out of Needs you for a question that will never be answered.
        // Without it the bank only grows: five Korean-language questions from
        // one dropped application badged the nav with a 5 that nothing could
        // clear.
        var never = el('button', 'btn ghost', 'Never ask this');
        never.type = 'button';
        never.title = 'Stop tracking this question. It can be put back.';
        act.appendChild(never);
        armed(never, 'Never ask this', 'Confirm, never ask', 'Dismissing\u2026',
          function () {
            return { editId: editId('dis'), action: 'dismiss', key: entry.key,
                     version: Number(entry.version || 0) };
          },
          function () {
            entry.state = 'dismissed';
            entry.version = Number(entry.version || 0) + 1;
            entry.dismissedAt = new Date().toISOString();
            entry.tags = (entry.tags || []).filter(function (t) {
              return t !== 'unanswered';
            });
          });
      }

      if (isDismissed(entry)) {
        var back = el('button', 'btn ghost', 'Put it back');
        back.type = 'button';
        act.appendChild(back);
        armed(back, 'Put it back', 'Confirm, ask again', 'Restoring\u2026',
          function () {
            return { editId: editId('res'), action: 'restore', key: entry.key,
                     version: Number(entry.version || 0) };
          },
          function () {
            // `missing` unless the question is Class 2 on sight, which is the
            // device's own rule. The board cannot run that classifier, so it
            // keeps the flag the device already set rather than guessing.
            entry.state = entry.sensitive ? 'sensitive' : 'missing';
            entry.version = Number(entry.version || 0) + 1;
            delete entry.dismissedAt;
          });
      }

      row.appendChild(act);
      row.appendChild(rowNote);

      var open = false;
      edit.addEventListener('click', function () {
        if (open) return;
        open = true;
        act.style.display = 'none';
        var editor = el('div', 'bank-edit');
        var box = el('textarea');
        box.rows = 3;
        box.value = answered(entry) ? String(entry.value) : '';
        box.setAttribute('aria-label', 'Answer for: ' + (entry.question || ''));
        editor.appendChild(box);

        // This no longer hides anything on this board and the label must not
        // claim it does. What it still does is set the flag the multi-tenant
        // envelope-encryption path selects on, so the classification stays
        // correct for the work #82 tracks, and the person answering can correct
        // a classifier that cannot read "Which communities do you belong to?".
        var mark = el('label', 'bank-sensitive');
        var tick = el('input');
        tick.type = 'checkbox';
        tick.checked = Boolean(entry.sensitive);
        mark.appendChild(tick);
        mark.appendChild(el('span', null,
          'Personal data: compensation, demographics, identity'));
        editor.appendChild(mark);
        var buttons = el('div', 'bank-act');
        var save = el('button', 'btn', 'Save');
        save.type = 'button';
        var cancel = el('button', 'btn ghost', 'Cancel');
        cancel.type = 'button';
        var message = el('span', 'profile-message');
        buttons.appendChild(save);
        buttons.appendChild(cancel);
        editor.appendChild(buttons);
        editor.appendChild(message);
        row.appendChild(editor);
        if (box.focus) box.focus();

        function close() {
          open = false;
          row.removeChild(editor);
          act.style.display = '';
        }
        cancel.addEventListener('click', close);

        if (answered(entry)) {
          // Inside the editor rather than on the resting row: forgetting an
          // answer takes opening the thing first, which is one more deliberate
          // act than a button sitting next to Edit would ask for.
          var forget2 = el('button', 'btn ghost', 'Forget this answer');
          forget2.type = 'button';
          var armedForget = false;
          buttons.appendChild(forget2);
          forget2.addEventListener('click', function () {
            if (!armedForget) {
              armedForget = true;
              forget2.textContent = 'Confirm forget';
              return;
            }
            forget2.disabled = true;
            var body3 = {
              editId: editId('del'), action: 'delete', key: entry.key,
              version: Number(entry.version || 0)
            };
            remember({ editId: body3.editId, kind: 'answer', path: '/api/answer', body: body3 });
            post('/api/answer', body3).then(function (response) {
              if (response && response.ok) {
                data.answers = (data.answers || []).filter(function (x) {
                  return x.key !== entry.key;
                });
                close();
                render();
              } else {
                forget2.disabled = false;
                armedForget = false;
                forget2.textContent = 'Forget this answer';
                message.className = 'profile-message bad';
                message.textContent = 'Your device would not remove it. Nothing changed.';
              }
            }).catch(function () {
              forget2.disabled = false;
              armedForget = false;
              forget2.textContent = 'Forget this answer';
              message.className = 'profile-message bad';
              message.textContent = 'That did not reach the queue. Nothing changed.';
            });
          });
        }

        save.addEventListener('click', function () {
          var value = String(box.value || '').trim();
          if (!value) {
            message.className = 'profile-message bad';
            message.textContent = 'An empty answer is not an answer. Type one, or cancel.';
            return;
          }
          save.disabled = true;
          var body2 = {
            editId: editId('ans'), action: 'save', key: entry.key,
            question: entry.question, value: value,
            state: tick.checked ? 'sensitive' : 'confirmed',
            version: Number(entry.version || 0)
          };
          remember({ editId: body2.editId, kind: 'answer', path: '/api/answer', body: body2 });
          post('/api/answer', body2).then(function (response) {
            save.disabled = false;
            if (response && response.ok) {
              // Optimistic, and the version moves with it, so a second edit in
              // the same sitting chains off the first instead of colliding.
              entry.value = value;
              entry.version = Number(entry.version || 0) + 1;
              entry.state = body2.state;
              entry.sensitive = tick.checked;
              entry.tags = (entry.tags || []).filter(function (t) { return t !== 'unanswered'; });
              entry.answeredAt = new Date().toISOString();
              close();
              render();
            } else {
              message.className = 'profile-message bad';
              message.textContent = 'Your device would not take that answer. Nothing changed.';
            }
          }).catch(function () {
            save.disabled = false;
            message.className = 'profile-message bad';
            message.textContent = 'That did not reach the queue. Nothing changed.';
          });
        });
      });
      return row;
    }

    // Standing policies first, then the questions still waiting, then the rest.
    // The waiting ones lead visually even though policies lead in order,
    // because an open question is the only thing here that is costing anything.
    var CATEGORY_LABELS = {
      'this-employer': 'This employer', 'how-you-heard': 'How you heard',
      'consent': 'Consent & privacy', 'work-authorisation': 'Work authorisation',
      'compensation': 'Compensation', 'demographics': 'Demographics',
      'background-check': 'Background check', 'legal': 'Legal declarations',
      'location': 'Location & travel', 'education': 'Education',
      'skills': 'Skills & experience', 'availability': 'Availability',
      'contact': 'Contact & links', 'other': 'Uncategorised'
    };

    // The kind half of `matches`, without the category or the search box, so
    // the category counts describe the list the reader is about to see.
    function matches_kind(entry) {
      if (kind === 'dismissed') return isDismissed(entry);
      if (isDismissed(entry)) return false;
      if (kind === 'open') return isOpen(entry);
      return (entry.kind || 'question') === kind;
    }

    function paintCategories() {
      var host = page.querySelector('[data-bank-cats]');
      if (!host) return;
      host.textContent = '';
      if (kind !== 'question' && kind !== 'open' && kind !== 'dismissed') return;
      var counts = {};
      ((data && data.answers) || []).forEach(function (entry) {
        if (!matches_kind(entry)) return;
        if (!entry.category) return;
        counts[entry.category] = (counts[entry.category] || 0) + 1;
      });
      var slugs = Object.keys(counts).sort(function (a, b) { return counts[b] - counts[a]; });
      if (!slugs.length) return;
      var all = el('button', 'bank-cat' + (category ? '' : ' on'), 'All');
      all.type = 'button';
      all.addEventListener('click', function () { category = null; render(); });
      host.appendChild(all);
      slugs.forEach(function (slug) {
        var chip = el('button', 'bank-cat' + (category === slug ? ' on' : ''),
          CATEGORY_LABELS[slug] || slug);
        chip.type = 'button';
        chip.appendChild(el('span', 'chip-n', String(counts[slug])));
        chip.addEventListener('click', function () {
          category = (category === slug) ? null : slug;
          render();
        });
        host.appendChild(chip);
      });
    }

    function render() {
      if (!bankHost) return;
      bankHost.textContent = '';
      if (failure) {
        var box = el('div', 'profile-error');
        box.appendChild(el('strong', null, 'Your answers could not be loaded.'));
        box.appendChild(el('span', null, failure));
        bankHost.appendChild(box);
        return;
      }
      if (loading || !data) {
        bankHost.appendChild(el('p', 'bank-note', 'Loading your answers…'));
        return;
      }
      paintKindChips();
      paintCategories();
      var entries = (data.answers || []).filter(matches);
      if (!entries.length) {
        var empty;
        if (!(data.answers && data.answers.length)) {
          empty = 'Nothing stored yet. The first application that stops on a question '
            + 'will put it here.';
        } else if (kind === 'open' && !query && !category) {
          // Reaching zero here is the good outcome, not a failed search, and
          // it is the one the nav badge has been counting down to.
          empty = 'Nothing is waiting on you.';
        } else if (kind === 'dismissed' && !query && !category) {
          empty = 'You have not dismissed any questions.';
        } else {
          empty = 'Nothing here matches that.';
        }
        bankHost.appendChild(el('div', 'bank-empty', empty));
        paintCounts();
        return;
      }
      var rows = grouped(entries);
      var list = el('div', 'bank-list');
      rows.forEach(function (group) { list.appendChild(bankRow(group.lead, group.members)); });
      var head = el('p', 'bank-note',
        rows.length === entries.length
          ? entries.length + (entries.length === 1 ? ' entry' : ' entries')
          : rows.length + ' rows, from ' + entries.length + ' stored entries');
      bankHost.appendChild(head);
      bankHost.appendChild(list);
      paintCounts();
    }

    function paintKindChips() {
      var counts = { question: 0, policy: 0, story: 0, dismissed: 0 };
      ((data && data.answers) || []).forEach(function (entry) {
        // A dismissed question is counted once, as dismissed. Counting it in
        // both places would make the three kind chips add up to more rows than
        // the bank can show.
        var k = isDismissed(entry) ? 'dismissed' : (entry.kind || 'question');
        if (counts[k] !== undefined) counts[k] += 1;
      });
      Object.keys(counts).forEach(function (k) {
        var node = page.querySelector('[data-kind-n="' + k + '"]');
        if (node) node.textContent = String(counts[k]);
      });
      var dismissChip = page.querySelector('[data-bank-kind="dismissed"]');
      if (dismissChip) {
        // Stays visible while it is the selected filter, or emptying it would
        // take the tab out from under the reader who is standing on it.
        dismissChip.hidden = counts.dismissed === 0 && kind !== 'dismissed';
      }
      Array.prototype.forEach.call(page.querySelectorAll('[data-bank-kind]'), function (chip) {
        chip.classList.toggle('on', chip.dataset.bankKind === kind);
      });
    }

    function paintCounts() {
      var all = (data && data.answers) || [];
      var totalNode = page.querySelector('[data-bank-count]');
      if (totalNode) totalNode.textContent = all.length + ' stored';
      var openNode = page.querySelector('[data-bank-open]');
      var openCount = all.filter(isOpen).length;
      if (openNode) openNode.textContent = String(openCount);
      var pill = document.querySelector('[data-profile-open]');
      if (pill) {
        pill.textContent = String(openCount);
        pill.hidden = openCount === 0;
      }
    }

    var search = page.querySelector('[data-bank-search]');
    if (search && search.addEventListener) {
      search.addEventListener('input', function () {
        query = String(search.value || '').trim().toLowerCase();
        render();
      });
    }
    var chips = page.querySelectorAll('[data-bank-kind]');
    Array.prototype.forEach.call(chips || [], function (chip) {
      if (!chip.addEventListener) return;
      chip.addEventListener('click', function () {
        kind = chip.dataset.bankKind;
        // Categories only describe employer questions, so switching away from
        // them drops a category filter rather than leaving one applied to a
        // list it cannot match.
        category = null;
        render();
      });
    });

    function load() {
      if (loading || data) return;
      loading = true;
      failure = null;
      render();
      fetch('/api/profile', { headers: { 'x-jobby-request': '1' } })
        .then(function (response) {
          return response.json().then(function (payload) {
            return { ok: response.ok, payload: payload };
          });
        })
        .then(function (result) {
          loading = false;
          if (!result.ok || !result.payload || !result.payload.ok) {
            failure = (result.payload && result.payload.error)
              || 'The board could not read them.';
            render();
            return;
          }
          data = result.payload;
          renderIdentities();
          renderResume();
          render();
          replay();
        })
        .catch(function () {
          loading = false;
          failure = 'The board did not answer. Check your connection and reload.';
          render();
        });
    }

    // Loaded when the Profile view is first opened, not on every board load: a
    // board opened to look at Found should not pay for a fetch nobody asked for.
    window.jobbyLoadProfile = load;
    if (document.body && document.body.classList
        && document.body.classList.contains('profile-view')) load();
  })();
"""


# What the page actually carries. The readable source is above; these are what
# dashboard.py interpolates.
CSS = tight(CSS)
SCRIPT = tight(SCRIPT)

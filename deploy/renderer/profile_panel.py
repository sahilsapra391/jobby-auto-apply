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
    sensitive = int(counts.get("sensitive") or 0)
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

        <details class="wording">
          <summary>
            <span>Edit the wording every tailored resume starts from</span>
            <span class="wording-hint">The layout is fixed. The words are not.</span>
          </summary>
          <div class="wording-body">
            <div class="split-note">
              <strong>The counts are the template, the text is yours.</strong>
              Each tailored resume replaces wording in a fixed layout, so a line can say
              anything and there is always the same number of them. Adding or removing one
              is refused rather than rendered wrong.
            </div>
            <div data-wording></div>
            <div class="wording-act">
              <button class="btn" type="button" data-wording-save>Save wording</button>
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
          <div class="bank-filters" role="group" aria-label="Filter the Memory Bank">
            <button class="bank-chip on" type="button" data-bank-filter="all">All</button>
            <button class="bank-chip" type="button" data-bank-filter="open">
              Needs you<span class="chip-n" data-bank-open>{unanswered}</span>
            </button>
            <button class="bank-chip" type="button" data-bank-filter="policy">Policies</button>
            <button class="bank-chip" type="button" data-bank-filter="sensitive">
              Sensitive<span class="chip-n">{sensitive}</span>
            </button>
          </div>
        </div>

        <div class="bank-groups" data-bank>
          <p class="bank-note">Loading your answers&hellip;</p>
        </div>
      </section>

      <p class="credential-note">Answers are stored on Your Device and published to the board
         behind this password so you can read and edit them here. Sensitive answers stay on
         your device: the board is told one exists so you can replace it, never what it says.
         Reading one back here waits on the per-user encryption in the multi-tenant plan,
         rather than shipping without it. One-time codes and verification codes are never stored,
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
  .bank-search { flex:1 1 18rem; min-width:0; }
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
  .bank-a.held { color:var(--ink-3); font-style:italic; }
  .bank-a.none { color:var(--orange); font-style:italic; }
  .bank-meta { margin:.6rem 0 0; display:flex; flex-wrap:wrap; gap:.35rem .5rem;
    align-items:center; }
  .bank-tag { background:var(--sunk); color:var(--ink-3); border-radius:999px;
    padding:.16rem .48rem; font-size:.72rem; font-weight:650; }
  .bank-tag.state-sensitive { background:var(--t-red); color:var(--red); }
  .bank-tag.state-missing { background:var(--t-o); color:var(--orange); }
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
    .bank-filters { overflow-x:auto; }
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
    var filter = 'all';

    var bankHost = page.querySelector('[data-bank]');
    var identityHost = page.querySelector('[data-identities]');
    var wordingHost = page.querySelector('[data-wording]');

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

    function isOpen(entry) {
      // A withheld sensitive answer has no value here and is not a question
      // waiting on anybody.
      return !answered(entry) && !entry.heldOnDevice && entry.remembered !== false;
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
      titles.appendChild(el('h4', null, entry.key ? String(entry.key).toUpperCase() : 'Base'));
      var scope = (entry.countryScope || []).join(', ');
      titles.appendChild(el('p', 'identity-scope', scope ? 'Used for ' + scope : 'No country set'));
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
    }

    // --- the wording ---------------------------------------------------------

    function textField(label, value, rows) {
      var wrap = el('div', 'wording-field');
      var id = 'w-' + Math.random().toString(36).slice(2, 9);
      var tag = el('label', null, label);
      tag.setAttribute('for', id);
      wrap.appendChild(tag);
      var box = el('textarea');
      box.id = id;
      box.rows = rows || 2;
      box.value = value === undefined || value === null ? '' : String(value);
      wrap.appendChild(box);
      wrap.__input = box;
      return wrap;
    }

    function renderWording() {
      if (!wordingHost) return;
      wordingHost.textContent = '';
      var content = (data && data.resumeContent) || null;
      if (!content || !Object.keys(content).length) {
        wordingHost.appendChild(el('p', 'bank-note',
          'No base wording is on record, so there is nothing to edit yet.'));
        return;
      }
      var fields = { areas: [], technical: [], roles: [] };

      var banner = textField('Headline', content.banner, 1);
      wordingHost.appendChild(banner);
      var summary = textField('Summary', content.summary, 6);
      wordingHost.appendChild(summary);

      var areaWrap = el('div', 'wording-field');
      areaWrap.appendChild(el('label', null,
        'Areas of expertise (' + (content.areasOfExpertise || []).length + ')'));
      var areaList = el('div', 'wording-list');
      (content.areasOfExpertise || []).forEach(function (area) {
        var box = el('input');
        box.type = 'text';
        box.value = area === null || area === undefined ? '' : String(area);
        areaList.appendChild(box);
        fields.areas.push(box);
      });
      areaWrap.appendChild(areaList);
      wordingHost.appendChild(areaWrap);

      var techWrap = el('div', 'wording-field');
      techWrap.appendChild(el('label', null, 'Technical proficiencies'));
      var techList = el('div', 'wording-list');
      (content.technical || []).forEach(function (row) {
        var line = el('div', 'identity-row');
        var label = el('input');
        label.type = 'text';
        label.value = String((row && row.label) || '');
        var items = el('input');
        items.type = 'text';
        items.value = String((row && row.items) || '');
        line.appendChild(label);
        line.appendChild(items);
        techList.appendChild(line);
        fields.technical.push({ label: label, items: items });
      });
      techWrap.appendChild(techList);
      wordingHost.appendChild(techWrap);

      (content.roles || []).forEach(function (role, index) {
        var box = el('div', 'wording-role');
        box.appendChild(el('h5', null, 'Role ' + (index + 1)));
        var lede = textField('Opening line', role && role.lede, 3);
        box.appendChild(lede);
        var bullets = [];
        ((role && role.bullets) || []).forEach(function (bullet, n) {
          var field = textField('Point ' + (n + 1), bullet, 2);
          box.appendChild(field);
          bullets.push(field.__input);
        });
        wordingHost.appendChild(box);
        fields.roles.push({ lede: lede.__input, bullets: bullets });
      });

      wordingHost.__collect = function () {
        return {
          banner: banner.__input.value,
          summary: summary.__input.value,
          areasOfExpertise: fields.areas.map(function (b) { return b.value; }),
          technical: fields.technical.map(function (row) {
            return { label: row.label.value, items: row.items.value };
          }),
          roles: fields.roles.map(function (role) {
            return {
              lede: role.lede.value,
              bullets: role.bullets.map(function (b) { return b.value; })
            };
          })
        };
      };
    }

    var wordingSave = page.querySelector('[data-wording-save]');
    var wordingMessage = page.querySelector('[data-wording-message]');
    if (wordingSave && wordingSave.addEventListener) {
      wordingSave.addEventListener('click', function () {
        if (!wordingHost || !wordingHost.__collect) return;
        var content = wordingHost.__collect();
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
            // A refusal has to be visible. An edit the user believes is saved
            // and silently is not is the worst thing this page can do.
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
      if (filter === 'open' && !isOpen(entry)) return false;
      if (filter === 'sensitive' && !entry.sensitive) return false;
      if (filter === 'policy' && (entry.tags || []).indexOf('policy') === -1) return false;
      if (!query) return true;
      var hay = String(entry.question || '') + ' ' + (entry.tags || []).join(' ');
      // A sensitive answer is not searchable by its text. Typing a salary into
      // the box and watching a row appear is a disclosure of the same kind as
      // rendering it.
      if (!entry.sensitive) hay += ' ' + String(entry.value || '');
      return hay.toLowerCase().indexOf(query) !== -1;
    }

    function bankRow(entry) {
      var row = el('article', 'bank-row' + (isOpen(entry) ? ' open' : ''));
      row.appendChild(el('p', 'bank-q', entry.question || entry.key || 'Untitled question'));

      var body = el('p', 'bank-a');
      function paintValue() {
        if (entry.heldOnDevice) {
          // There is nothing to reveal and no control that pretends there is.
          // The value was never sent, so a masked row with a Reveal button
          // would be a button that cannot work.
          body.className = 'bank-a held';
          body.textContent = 'Answered. Held on your device, not sent to the board.';
        } else if (!answered(entry)) {
          body.className = 'bank-a none';
          body.textContent = entry.remembered === false
            ? 'Filled when asked, deliberately not kept.'
            : 'No answer yet.';
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
      var edit = el('button', 'btn ghost',
        entry.heldOnDevice ? 'Replace' : (answered(entry) ? 'Edit' : 'Answer'));
      edit.type = 'button';
      act.appendChild(edit);
      row.appendChild(act);

      var open = false;
      edit.addEventListener('click', function () {
        if (open) return;
        open = true;
        act.style.display = 'none';
        var editor = el('div', 'bank-edit');
        if (entry.heldOnDevice) {
          // Write-only, and said so plainly rather than by an empty box the
          // reader has to interpret.
          editor.appendChild(el('p', 'bank-note',
            'Your device holds the current answer and does not send it here. '
            + 'Anything you type replaces it.'));
        }
        var box = el('textarea');
        box.rows = 3;
        box.value = (!entry.heldOnDevice && answered(entry)) ? String(entry.value) : '';
        box.setAttribute('aria-label',
          (entry.heldOnDevice ? 'Replacement answer for: ' : 'Answer for: ')
          + (entry.question || ''));
        editor.appendChild(box);

        // The store guesses sensitivity from the question when the loop opens
        // it, and a guess is all it can be: measured against the real corpus,
        // the wording of "Which of the following communities do you belong to?"
        // names nothing sensitive at all. So the person answering gets the
        // final say, in both directions.
        var mark = el('label', 'bank-sensitive');
        var tick = el('input');
        tick.type = 'checkbox';
        tick.checked = Boolean(entry.sensitive);
        mark.appendChild(tick);
        mark.appendChild(el('span', null, 'Sensitive: keep this hidden until I ask'));
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

        if (answered(entry) || entry.heldOnDevice) {
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
    function groupsFor(entries) {
      // Under a filter the grouping is the filter. Splitting a Sensitive
      // filter into "Standing policies" and "Everything else" labels 22
      // sensitive answers as everything else, which is worse than no heading.
      if (filter !== 'all') {
        var titles = { open: 'Needs your answer', policy: 'Standing policies',
                       sensitive: 'Sensitive answers' };
        return [{ key: filter, title: titles[filter] || 'Matching', rows: entries,
                  note: filter === 'sensitive'
                    ? 'Answers your device keeps to itself. The board is told they '
                      + 'exist so you can replace one, never what they say.'
                    : '' }];
      }
      var open = [], policy = [], rest = [];
      entries.forEach(function (entry) {
        if (isOpen(entry)) open.push(entry);
        else if ((entry.tags || []).indexOf('policy') !== -1) policy.push(entry);
        else rest.push(entry);
      });
      return [
        { key: 'open', title: 'Needs your answer', rows: open,
          note: 'An application stopped on each of these. Answer it once and every '
                + 'later application finds it.' },
        { key: 'policy', title: 'Standing policies', rows: policy,
          note: 'Answered the same way on every form.' },
        { key: 'rest', title: 'Everything else', rows: rest, note: '' }
      ];
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
      var entries = (data.answers || []).filter(matches);
      if (!entries.length) {
        bankHost.appendChild(el('div', 'bank-empty', data.answers && data.answers.length
          ? 'Nothing here matches that.'
          : 'Nothing stored yet. The first application that stops on a question will '
            + 'put it here.'));
        return;
      }
      groupsFor(entries).forEach(function (group) {
        if (!group.rows.length) return;
        var section = el('section', 'bank-group' + (group.key === 'open' ? ' open' : ''));
        section.appendChild(el('h4', null, group.title + ' (' + group.rows.length + ')'));
        if (group.note) section.appendChild(el('p', null, group.note));
        var list = el('div', 'bank-list');
        group.rows.forEach(function (entry) { list.appendChild(bankRow(entry)); });
        section.appendChild(list);
        bankHost.appendChild(section);
      });
      paintCounts();
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
    var chips = page.querySelectorAll('[data-bank-filter]');
    Array.prototype.forEach.call(chips || [], function (chip) {
      if (!chip.addEventListener) return;
      chip.addEventListener('click', function () {
        filter = chip.dataset.bankFilter;
        Array.prototype.forEach.call(chips, function (other) {
          other.classList.toggle('on', other === chip);
        });
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
          renderWording();
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

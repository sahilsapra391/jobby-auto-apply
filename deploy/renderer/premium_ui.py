#!/usr/bin/env python3
"""Soft Premium visual system for the generated Jobby interface.

The dashboard remains one offline-capable HTML artifact. Keeping the visual
overrides here makes the design system reviewable without mixing color,
spacing, and motion decisions into the application state machine.
"""


CSS = r"""
  @view-transition { navigation:auto; }
  :root {
    --bg:#f7f3ed; --card:#fffdf9; --sunk:#f0ebe3; --seg:#ece7df;
    --ink:#202131; --ink-2:#65636b; --ink-3:#8b8790;
    --hair:rgba(47,43,55,.12); --hair-strong:rgba(47,43,55,.34);
    --hover-wash:rgba(255,253,249,.85); --hover-wash-strong:#fffdf9;
    --mark-plate:transparent; --mark-ink:#202131;
    --blue:#37388f; --purple:#5150a9;
    --green:#347157; --green-bg:#e5f1e8; --orange:#a65a32;
    --red:#a6403d; --red-ink:#963936; --btn-g:#37388f; --btn-g-ink:#fff;
    --t-o:rgba(166,90,50,.09); --t-g:rgba(52,113,87,.10);
    --t-p:rgba(81,80,169,.09); --t-red:rgba(166,64,61,.09);
    --sh:0 1px 2px rgba(34,29,45,.04),0 14px 38px rgba(34,29,45,.055);
    --sh-hover:0 4px 8px rgba(34,29,45,.055),0 22px 54px rgba(34,29,45,.10);
    --radius:15px; --ease:cubic-bezier(.22,1,.36,1); --shell:1800px;
  }
  @media (prefers-color-scheme:dark) {
    :root:not([data-theme="light"]) {
      --bg:#141519; --card:#1d1e23; --sunk:#27282e; --seg:#222329;
      --ink:#f2eee7; --ink-2:#b6b0aa; --ink-3:#85828a;
      --hair:rgba(255,255,255,.105); --hair-strong:rgba(255,255,255,.32);
      --hover-wash:rgba(255,255,255,.07); --hover-wash-strong:rgba(255,255,255,.11);
      --mark-plate:#f4efe3; --mark-ink:#1d1e27;
      --blue:#a6a7ff; --purple:#b9a8ff;
      --green:#7cc59e; --green-bg:rgba(70,151,105,.17); --orange:#e79b70;
      --red:#e07d79; --red-ink:#ee8b87; --btn-g:#9c9dff; --btn-g-ink:#171827;
      --t-o:rgba(231,155,112,.11); --t-g:rgba(124,197,158,.11);
      --t-p:rgba(185,168,255,.12); --t-red:rgba(224,125,121,.13);
      --sh:0 1px 2px rgba(0,0,0,.32),0 16px 42px rgba(0,0,0,.22);
      --sh-hover:0 4px 12px rgba(0,0,0,.28),0 24px 58px rgba(0,0,0,.36);
    }
  }
  :root[data-theme="dark"] {
    --bg:#141519; --card:#1d1e23; --sunk:#27282e; --seg:#222329;
    --ink:#f2eee7; --ink-2:#b6b0aa; --ink-3:#85828a;
    --hair:rgba(255,255,255,.105); --hair-strong:rgba(255,255,255,.32);
    --hover-wash:rgba(255,255,255,.07); --hover-wash-strong:rgba(255,255,255,.11);
    --mark-plate:#f4efe3; --mark-ink:#1d1e27;
    --blue:#a6a7ff; --purple:#b9a8ff;
    --green:#7cc59e; --green-bg:rgba(70,151,105,.17); --orange:#e79b70;
    --red:#e07d79; --red-ink:#ee8b87; --btn-g:#9c9dff; --btn-g-ink:#171827;
    --t-o:rgba(231,155,112,.11); --t-g:rgba(124,197,158,.11);
    --t-p:rgba(185,168,255,.12); --t-red:rgba(224,125,121,.13);
    --sh:0 1px 2px rgba(0,0,0,.32),0 16px 42px rgba(0,0,0,.22);
    --sh-hover:0 4px 12px rgba(0,0,0,.28),0 24px 58px rgba(0,0,0,.36);
  }

  html { background:var(--bg); }
  body { min-height:100vh; background:var(--bg); font-size:16px;
    letter-spacing:-.016em; transition:background-color .38s var(--ease),color .3s var(--ease); }
  body::before { content:""; position:fixed; inset:0; pointer-events:none; z-index:-1;
    background:radial-gradient(circle at 82% 3%,var(--t-p),transparent 30rem); opacity:.55; }
  .appbar { width:100%; max-width:var(--shell); min-height:5.4rem; margin:0 auto;
    padding:1rem clamp(1rem,3vw,2.5rem); border-bottom:1px solid var(--hair);
    position:relative; view-transition-name:jobby-header; }
  .lockup { height:1.85rem; }
  .primary-nav { padding:.28rem; border-radius:13px; background:var(--seg); gap:.15rem; }
  .primary-nav a { min-height:2.45rem; border-radius:10px; padding:.48rem 1.2rem;
    font-weight:620; transition:background-color .28s var(--ease),color .24s,transform .24s var(--ease); }
  .primary-nav a:not([aria-current="page"]):hover { background:var(--hover-wash); color:var(--ink); }
  .primary-nav a[aria-current="page"] { box-shadow:0 1px 3px rgba(30,26,38,.11); }

  .themeseg { padding:3px; border:1px solid var(--hair); border-radius:13px;
    background:var(--seg); gap:2px; }
  .themebtn { width:2.15rem; min-width:2.15rem; height:2rem; padding:0;
    border-radius:9px; font-size:.72rem; font-weight:620;
    transition:background-color .3s var(--ease),color .24s,transform .2s var(--ease); }
  .themebtn:not(.on):hover { background:var(--hover-wash); color:var(--ink); }
  .themebtn svg { width:.92rem; height:.92rem; }

  .wrap { max-width:var(--shell); padding:clamp(2rem,4vw,3.5rem) clamp(1rem,3vw,2.5rem) 7rem; }
  /* The table lanes used to break out of a narrower wrap with negative margins.
     The wrap is that width now, so the break-out has nothing left to do and
     would only pull the sheets back in by the difference. */
  .panel .sheet,.panel .filters.wide { width:auto; max-width:none;
    margin-left:0; margin-right:0; }
  .top { display:grid; grid-template-columns:minmax(0,1fr) auto; align-items:center;
    gap:1.25rem; text-align:left; margin:0 auto 2.4rem;
    width:100%; max-width:1360px; }
  .hero-copy { min-width:0; }
  .top h1 { margin:0 0 .3rem; font-family:"Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif;
    font-size:clamp(2.25rem,4.6vw,4.2rem); font-weight:500; line-height:.98;
    letter-spacing:-.045em; }
  .lede { font-size:1rem; }
  .stamp { margin:.35rem 0 0; }
  .jb-morph { width:139px; height:153px; margin:0; }
  .jb-mascot2 { height:111px; }

  .segwrap { position:sticky; top:.7rem; margin:0 auto 1.6rem; z-index:20;
    width:100%; max-width:1360px; }
  .seg { width:100%; display:grid; grid-template-columns:repeat(5,minmax(0,1fr));
    padding:0; gap:0; overflow:visible; border:1px solid var(--hair);
    border-radius:var(--radius); background:var(--card); box-shadow:var(--sh); }
  /* `text-decoration:none` is a no-op on this board, where every .tab is a
     <button>. It is here because v2 serves the same lanes as links -- a
     server-rendered board navigates rather than switching a panel with script
     -- and an anchor arrives underlined. One line in the source of truth
     beats a v2-only override, which is where two design systems start. */
  .tab { min-height:4.3rem; justify-content:center; border-radius:0; padding:.8rem 1rem;
    position:relative; font-weight:570; text-decoration:none;
    transition:color .24s,background-color .3s var(--ease); }
  .tab:first-child { border-radius:calc(var(--radius) - 1px) 0 0 calc(var(--radius) - 1px); }
  .tab:last-child { border-radius:0 calc(var(--radius) - 1px) calc(var(--radius) - 1px) 0; }
  .tab + .tab { border-left:1px solid var(--hair); }
  .tab:not(.on):hover { background:color-mix(in srgb,var(--t-p) 55%,transparent);
    color:color-mix(in srgb,var(--blue) 62%,var(--ink-2)); }
  .tab:not(.on):hover .n { background:color-mix(in srgb,var(--t-p) 60%,var(--sunk)); }
  .tab.on { color:var(--blue); background:var(--t-p); box-shadow:none; }
  .tab.on::after { content:""; position:absolute; left:1rem; right:1rem; bottom:0;
    height:3px; border-radius:3px 3px 0 0; background:var(--blue); }
  .tab .n { background:var(--sunk); }

  .panel { animation:premium-panel-in .32s var(--ease); }
  @keyframes premium-panel-in { from { opacity:0; transform:translateY(8px); }
    to { opacity:1; transform:none; } }
  html.jb-vt .panel { animation:none; }
  ::view-transition-old(root) { animation:premium-fade-out .22s var(--ease) both; }
  ::view-transition-new(root) { animation:premium-fade-in .34s var(--ease) both; }
  ::view-transition-old(jobby-header),::view-transition-new(jobby-header) { animation:none; }
  @keyframes premium-fade-out { to { opacity:0; } }
  @keyframes premium-fade-in { from { opacity:0; } }

  .card,.sheet,.empty,.note,.jb-banner,.jb-filters,.jb-addlink,.provider-card,.split-note {
    border:1px solid var(--hair); border-radius:var(--radius); box-shadow:var(--sh); }
  /* One ring. The status rule along the top is 3px, so the other three sides
     are too, and only the colour differs. At 1px the grey met the coloured rule
     at a step and read as two separate lines rather than one frame. Width does
     not change on hover, so nothing moves. */
  .card { border-width:3px; }
  .card { border-radius:var(--radius); padding:1.25rem;
    transition:border-color .24s var(--ease),box-shadow .24s var(--ease),
    background-color .3s; }
  .card:hover { border-color:var(--hair-strong); }
  .card.hold,.card.ready,.card.ref,.card.flight { background:var(--card); }
  .card.hold { border-top-color:var(--orange); }
  .card.ready { border-top-color:var(--green); }
  .card.ref,.card.flight { border-top-color:var(--purple); }
  /* A park nobody can act on. Red rather than orange, because orange in this
     lane means "a step is waiting for you" and this one is waiting for us. */
  .card.fault { background:var(--card); border-top-color:var(--red); }
  .fault-note { margin:.15rem 0 .6rem; color:var(--red-ink); font-size:.9rem;
    line-height:1.45; }
  .grid { grid-template-columns:repeat(3,minmax(0,1fr)); gap:1rem; }
  .act { flex-wrap:wrap; gap:.45rem; }
  .act .btn { flex:0 0 auto; white-space:nowrap; padding:.52rem .7rem; }
  .sheet { border-radius:var(--radius); }
  tr { transition:background-color .2s ease; }
  tbody tr:hover { background:var(--t-p); }
  th { background:color-mix(in srgb,var(--sunk) 55%,transparent); letter-spacing:.025em;
    text-transform:uppercase; font-size:.7rem; }
  td,th { border-color:var(--hair); }
  .btn,.jb-btn { border-radius:10px; font-weight:600;
    transition:transform .22s var(--ease),box-shadow .28s var(--ease),background-color .24s,color .24s,border-color .24s; }
  .btn:hover,.jb-btn:hover { opacity:1; transform:translateY(-2px); box-shadow:0 8px 20px rgba(54,55,143,.16); }
  .btn:active,.jb-btn:active { transform:translateY(0) scale(.98); }
  .filters input[type=search],.filters select,.jb-row input[type=search],.jb-row input[type=url],
  .jb-row select,.jb-location-picker > summary { border-radius:11px; background:var(--card);
    min-height:2.7rem; transition:border-color .22s,box-shadow .22s,background-color .3s; }
  .filters input[type=search]:focus,.filters select:focus,.jb-row input:focus,.jb-row select:focus {
    border-color:var(--blue); box-shadow:0 0 0 3px var(--t-p); outline:none; }
  .ui-separator { display:inline-block; margin-left:.45rem; color:var(--ink-2); }
  .ui-separator::before { content:": "; }

  .jb-wide { width:auto; margin-left:0; margin-right:0; }
  .jb-banner,.jb-ingest,.jb-probs {
    width:100%; max-width:1360px; margin-left:auto; margin-right:auto; }
  .jb-grid { --cols:1; gap:.8rem; }
  .jb-col { gap:.8rem; }
  .jb-banner,.jb-filters,.jb-addlink { border-radius:var(--radius); background:var(--card); }
  .jb-card { display:grid; grid-template-columns:minmax(16rem,1fr) minmax(20rem,1.7fr) minmax(17rem,1.1fr) minmax(13rem,auto);
    grid-template-rows:auto auto auto; align-items:center; gap:.7rem 1.4rem;
    border:1px solid var(--hair); border-radius:var(--radius); padding:1rem 1.1rem;
    box-shadow:var(--sh); transition:border-color .24s var(--ease),background-color .3s; }
  .jb-card:hover,.jb-card.open { border-color:var(--hair-strong); }
  .jb-head { grid-column:1; grid-row:1 / span 2; align-items:center; justify-content:space-between; }
  .jb-facts { grid-column:3; grid-row:1 / span 2; display:flex; flex-wrap:wrap;
    align-content:center; gap:.3rem; }
  .jb-identity { display:flex; align-items:center; gap:.72rem; min-width:0; }
  .jb-identity > div { min-width:0; }
  .jb-sum { grid-column:2; grid-row:1; -webkit-line-clamp:3; }
  .jb-chips { grid-column:2; grid-row:2; }
  .jb-acts { grid-column:4; grid-row:1 / span 2; justify-content:flex-end;
    border-top:0; padding-top:0; }
  .jb-det { grid-column:1; grid-row:3; }
  .jb-slot { grid-column:1 / -1; grid-row:4; }
  .jb-ring { width:52px; height:52px; }
  .jb-ring i { width:42px; height:42px; }
  .jb-location-menu { border-radius:var(--radius); box-shadow:var(--sh-hover); }

  .company-mark { width:2.75rem; height:2.75rem; flex:none; border-radius:12px;
    display:grid; place-items:center; overflow:hidden; position:relative;
    background:var(--sunk); border:1px solid var(--hair); color:var(--ink-2);
    font-size:.75rem; font-weight:750; letter-spacing:.02em; }
  .company-mark img { position:absolute; inset:0; width:100%; height:100%;
    object-fit:cover; padding:0; background:transparent;
    opacity:0; transition:opacity .3s ease; }
  .company-mark img.loaded { opacity:1; }
  .company-title,.company-cell { display:flex; align-items:center; gap:.72rem; min-width:0; }
  .company-title > div,.company-cell > span { min-width:0; }
  .company-cell .company-mark { width:2.15rem; height:2.15rem; border-radius:9px; }

  .settings-page { max-width:1180px; padding:1rem 0 5rem; }
  .settings-titlebar { margin-bottom:1.5rem; }
  .settings-titlebar h2,.dialog-head h2 { font-family:"Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif;
    font-weight:500; letter-spacing:-.035em; }
  .split-note { background:var(--card); padding:1.15rem 1.25rem; }
  .provider-grid { gap:1rem; }
  .provider-card { border-radius:var(--radius); padding:1.15rem;
    transition:border-color .24s var(--ease),background-color .3s; }
  .provider-card:hover { border-color:var(--hair-strong); }

  .dropped-dock { position:fixed; left:1rem; bottom:1rem; z-index:45; height:2.8rem;
    max-width:3rem; padding:0 .76rem; border:1px solid var(--hair); border-radius:12px;
    display:flex; align-items:center; gap:.5rem; overflow:hidden; background:var(--card);
    color:var(--ink-2); box-shadow:var(--sh); cursor:pointer; white-space:nowrap;
    transition:max-width .4s var(--ease),transform .3s var(--ease),box-shadow .3s var(--ease),color .2s; }
  body.settings-view .dropped-dock { display:none; }
  .dropped-dock:hover,.dropped-dock:focus-visible { max-width:10.5rem; transform:translateY(-3px);
    box-shadow:var(--sh-hover); color:var(--red-ink); }
  .dropped-dock svg { width:1.25rem; height:1.25rem; flex:none; }
  .dropped-label { opacity:0; transform:translateX(-5px); transition:opacity .2s ease .05s,transform .3s var(--ease); }
  .dropped-dock:hover .dropped-label,.dropped-dock:focus-visible .dropped-label { opacity:1; transform:none; }
  .dropped-count { min-width:1.3rem; height:1.3rem; padding:0 .3rem; border-radius:999px;
    display:grid; place-items:center; background:var(--t-red); color:var(--red-ink);
    font-size:.68rem; font-weight:700; }
  .dropped-dialog { width:min(1100px,calc(100vw - 2rem)); max-height:min(82vh,760px);
    padding:1.35rem; border:1px solid var(--hair); border-radius:20px;
    background:var(--bg); color:var(--ink); box-shadow:0 32px 90px rgba(24,19,33,.28); }
  .dropped-dialog[open] { animation:dialog-in .42s var(--ease); }
  .dropped-dialog::backdrop { background:rgba(24,21,30,.42); backdrop-filter:blur(8px);
    animation:backdrop-in .3s ease; }
  @keyframes dialog-in { from { opacity:0; transform:translateY(22px) scale(.98); }
    to { opacity:1; transform:none; } }
  @keyframes backdrop-in { from { opacity:0; } to { opacity:1; } }
  .dialog-head { display:flex; align-items:flex-start; justify-content:space-between; gap:1rem;
    padding:.15rem .2rem 1rem; }
  .dialog-head h2 { margin:.08rem 0 0; font-size:2rem; }
  .dialog-head .eyebrow { margin:0; }
  .dialog-close { width:2.5rem; height:2.5rem; border:1px solid var(--hair); border-radius:10px;
    background:var(--card); color:var(--ink-2); cursor:pointer; font-size:1.5rem; line-height:1; }
  .dropped-dialog .hint { text-align:left; margin:0 0 1rem; }
  .dropped-dialog .filters { margin-bottom:1rem; }
  .dropped-dialog .sheet { width:100%; margin:0; }
  .dropped-dialog .tablewrap table { min-width:720px; }
  .dropped-dialog .rowacts-inline { white-space:nowrap; gap:.65rem; }
  .dropped-dialog .rowacts-inline a,
  .dropped-dialog .rowacts-inline button { white-space:nowrap; }

  /* What the employer's form asked and what went into it. It borrows the
     dropped dialog's shell and adds nothing to the palette: every colour below
     is an existing token, so there is no new value to define three times and no
     way for one theme block to be missed. */
  .answers-dialog { width:min(820px,calc(100vw - 2rem)); max-height:min(84vh,780px);
    padding:1.35rem; border:1px solid var(--hair); border-radius:20px;
    background:var(--bg); color:var(--ink); box-shadow:0 32px 90px rgba(24,19,33,.28); }
  .answers-dialog[open] { animation:dialog-in .42s var(--ease); }
  .answers-dialog::backdrop { background:rgba(24,21,30,.42); backdrop-filter:blur(8px);
    animation:backdrop-in .3s ease; }
  .answers-dialog .hint { text-align:left; margin:0 0 1rem; }
  .answers-dialog #answers-body { overflow-y:auto; max-height:min(58vh,540px);
    padding-right:.35rem; }
  .answers-count { margin:0 0 .8rem; color:var(--ink-3); font-size:.78rem;
    text-transform:uppercase; letter-spacing:.06em; }
  .answers-loading { margin:0; color:var(--ink-3); font-size:.9rem; }
  .ans-list { display:grid; gap:.55rem; }
  .ans-row { padding:.8rem .9rem; border:1px solid var(--hair); border-radius:12px;
    background:var(--card); }
  .ans-q { margin:0 0 .35rem; font-size:.88rem; font-weight:600; color:var(--ink-2);
    line-height:1.4; overflow-wrap:anywhere; }
  .ans-a { margin:0 0 .5rem; }
  .ans-value { font-size:.98rem; color:var(--ink); line-height:1.45;
    white-space:pre-wrap; overflow-wrap:anywhere; }
  /* A row whose value was never captured at all: a code, a password, an
     identifier. Not a covered value, so it gets no affordance suggesting there
     is something behind it. There is nothing behind it. */
  .ans-withheld { font-size:.9rem; color:var(--orange); }
  /* A question salvaged from before answers were recorded. Quieter than a
     withheld row, because nothing was refused here; the value is simply gone. */
  .ans-unrecorded { font-size:.9rem; color:var(--ink-3); font-style:italic; }
  .ans-salvage { margin:0 0 .9rem; padding:.6rem .8rem; border-radius:10px;
    background:var(--sunk); color:var(--ink-2); font-size:.84rem; line-height:1.5; }
  .ans-meta { display:flex; flex-wrap:wrap; align-items:center; gap:.4rem; }
  .ans-badge { padding:.16rem .5rem; border-radius:999px; font-size:.68rem;
    font-weight:700; letter-spacing:.03em; text-transform:uppercase; }
  .ans-src-stored { background:var(--t-g); color:var(--green); }
  .ans-src-profile { background:var(--t-p); color:var(--purple); }
  .ans-src-generated { background:var(--sunk); color:var(--ink-2); }
  .ans-src-provided { background:var(--t-o); color:var(--orange); }
  .ans-differs { background:var(--t-red); color:var(--red-ink); }
  .ans-unverified { background:var(--sunk); color:var(--ink-3); }
  .ans-note { font-size:.78rem; color:var(--ink-3); overflow-wrap:anywhere; }
  @media (max-width:640px) {
    .answers-dialog { width:calc(100vw - 1rem); padding:1rem; }
    .answers-dialog .dialog-head h2 { font-size:1.45rem; }
  }


  /* Checkboxes were the other control the operating system was drawing. The
     continent rows go indeterminate when a continent is partly chosen, so that
     state needs a mark of its own rather than reading as unchecked. */
  input[type="checkbox"] { appearance:none; -webkit-appearance:none; position:relative;
    width:1.05rem; height:1.05rem; flex:none; margin:0; border:1.5px solid var(--hair-strong);
    border-radius:5px; background:var(--card); cursor:pointer;
    transition:background-color .16s,border-color .16s; }
  input[type="checkbox"]:hover { border-color:var(--blue); }
  input[type="checkbox"]:checked,input[type="checkbox"]:indeterminate {
    background:var(--btn-g); border-color:var(--btn-g); }
  input[type="checkbox"]:checked::after { content:""; position:absolute; left:50%; top:46%;
    width:.26rem; height:.54rem; transform:translate(-50%,-50%) rotate(45deg);
    border:solid var(--btn-g-ink); border-width:0 2px 2px 0; }
  input[type="checkbox"]:indeterminate::after { content:""; position:absolute; left:50%; top:50%;
    width:.52rem; height:2px; border-radius:2px; transform:translate(-50%,-50%);
    background:var(--btn-g-ink); }
  input[type="checkbox"]:focus-visible { outline:2px solid var(--blue); outline-offset:2px; }

  /* A native select popup is drawn by the operating system, so the controls
     people touch most were the only ones on the board that could not follow the
     design system. The native element stays in the DOM and keeps owning value
     and events; everything below is only its face. */
  .jb-select { position:relative; display:inline-flex; vertical-align:middle; }
  .jb-select > select { position:absolute; inset:0; width:100%; height:100%;
    opacity:0; pointer-events:none; }
  .jb-select-btn { display:inline-flex; align-items:center; gap:.55rem; width:100%;
    min-height:2.7rem; padding:.42rem .8rem; border:1px solid var(--hair);
    border-radius:11px; background:var(--card); color:var(--ink); font:inherit;
    font-size:.88rem; font-weight:540; letter-spacing:-.014em; cursor:pointer;
    white-space:nowrap; text-align:left;
    transition:border-color .2s,background-color .2s,box-shadow .2s; }
  .jb-select-btn:hover { border-color:var(--hair-strong);
    background:var(--hover-wash-strong); }
  .jb-select-btn:focus-visible,.jb-select[data-open="true"] .jb-select-btn {
    outline:none; border-color:var(--blue); box-shadow:0 0 0 3px var(--t-p); }
  .jb-select-value { flex:1 1 auto; overflow:hidden; text-overflow:ellipsis; }
  .jb-select-chevron { width:.4rem; height:.4rem; flex:none;
    border-right:2px solid var(--ink-3); border-bottom:2px solid var(--ink-3);
    transform:rotate(45deg) translateY(-2px); transition:transform .22s var(--ease); }
  .jb-select[data-open="true"] .jb-select-chevron {
    transform:rotate(225deg) translateY(-1px); }
  .jb-select-menu { position:absolute; z-index:60; top:calc(100% + .35rem); left:0;
    min-width:100%; max-height:17rem; overflow-y:auto; padding:.3rem; display:none;
    border:1px solid var(--hair); border-radius:var(--radius);
    background:var(--card); box-shadow:var(--sh-hover); }
  .jb-select[data-open="true"] .jb-select-menu { display:block;
    animation:jb-select-in .18s var(--ease); }
  .jb-select.drop-up .jb-select-menu { top:auto; bottom:calc(100% + .35rem); }
  @keyframes jb-select-in { from { opacity:0; transform:translateY(-4px); } }
  .jb-select-opt { display:block; width:100%; padding:.46rem .6rem; border:0;
    border-radius:8px; background:none; color:var(--ink-2); font:inherit;
    font-size:.86rem; text-align:left; white-space:nowrap; cursor:pointer;
    transition:background-color .14s,color .14s; }
  /* The wash tokens are keyed to the segment, which is darker than a card in
     light themes and lighter in dark ones. On a card the option needs --sunk,
     which moves the right way in both. */
  .jb-select-opt:hover,.jb-select-opt:focus-visible { background:var(--sunk);
    color:var(--ink); outline:none; }
  .jb-select-opt[aria-selected="true"] { background:var(--t-p); color:var(--blue);
    font-weight:600; }
  @media (prefers-reduced-motion:reduce) {
    .jb-select[data-open="true"] .jb-select-menu { animation:none; }
  }

  @media (max-width:1200px) {
    .jb-card { grid-template-columns:minmax(15rem,1.15fr) minmax(18rem,1.8fr) minmax(15rem,auto); }
    .jb-facts { grid-column:2; grid-row:3; }
    .jb-acts { grid-column:3; }
    .jb-det { grid-row:4; }
    .jb-slot { grid-row:5; }
    .act { flex-wrap:wrap; }
  }
  @media (max-width:1100px) {
    .jb-card { grid-template-columns:minmax(14rem,1fr) minmax(18rem,1.5fr); }
    .jb-head { grid-column:1; }
    .jb-sum,.jb-chips,.jb-facts { grid-column:2; }
    .jb-facts { grid-row:3; }
    .jb-acts { grid-column:1 / -1; grid-row:4; justify-content:flex-start; padding-top:.75rem;
      border-top:1px solid var(--hair); }
    .jb-det { grid-row:5; }
    .jb-slot { grid-row:6; }
    .grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
  }
  @media (max-width:760px) {
    .appbar { min-height:4.5rem; }
    .themebtn { padding:0; width:1.9rem; min-width:1.9rem; }
    .top { grid-template-columns:1fr auto; gap:.85rem; }
    .top h1 { font-size:clamp(2rem,10vw,3rem); }
    .seg { overflow-x:auto; display:flex; }
    .tab { min-width:8rem; min-height:3.8rem; flex:1 0 auto; }
    .grid { grid-template-columns:1fr; }
    .jb-card { display:flex; gap:.75rem; }
    .jb-radios { max-width:100%; overflow-x:auto; scrollbar-width:none; }
    .jb-radios::-webkit-scrollbar { display:none; }
    .jb-count { width:100%; max-width:100%; white-space:normal; }
    .jb-acts { justify-content:flex-start; padding-top:.75rem; border-top:1px solid var(--hair); }
    .jb-det { order:8; }
    .jb-slot { order:9; }
    .dropped-dock { left:.75rem; bottom:.75rem; }
  }
  @media (max-width:520px) {
    .appbar { grid-template-columns:1fr auto; }
    .primary-nav { grid-column:1 / -1; grid-row:2; width:100%; }
    .primary-nav a { flex:1; }
    .themeseg { grid-column:2; grid-row:1; }
    .wrap { padding-top:1.6rem; }
    .jb-morph { width:109px; height:121px; }
    .jb-mascot2 { height:88px; }
  }
  @media (prefers-reduced-motion:reduce) {
    ::view-transition-group(*),::view-transition-old(*),::view-transition-new(*) { animation:none !important; }
    .dropped-dialog[open],.dropped-dialog::backdrop,.panel { animation:none; }
    .answers-dialog[open],.answers-dialog::backdrop { animation:none; }
  }
"""


# Behaviour for the styled select. It lives beside the CSS because the two are
# one control, and dashboard.py emits it last so every native select on the page
# already has its listeners attached before any of them is wrapped.
SCRIPT = r"""
(function () {
  var nativeValue = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value');
  var openWrap = null;

  function closeOpen() {
    if (!openWrap) return;
    openWrap.setAttribute('data-open', 'false');
    openWrap.querySelector('.jb-select-btn').setAttribute('aria-expanded', 'false');
    openWrap = null;
  }

  function enhance(select) {
    if (select.multiple || select.dataset.jbSelect) return;
    select.dataset.jbSelect = 'on';

    var wrap = document.createElement('div');
    wrap.className = 'jb-select';
    wrap.setAttribute('data-open', 'false');
    select.parentNode.insertBefore(wrap, select);
    wrap.appendChild(select);
    select.setAttribute('tabindex', '-1');
    select.setAttribute('aria-hidden', 'true');

    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'jb-select-btn';
    btn.setAttribute('aria-haspopup', 'listbox');
    btn.setAttribute('aria-expanded', 'false');
    var described = select.getAttribute('aria-label');
    if (described) btn.setAttribute('aria-label', described);

    var valueText = document.createElement('span');
    valueText.className = 'jb-select-value';
    var chevron = document.createElement('span');
    chevron.className = 'jb-select-chevron';
    chevron.setAttribute('aria-hidden', 'true');
    btn.appendChild(valueText);
    btn.appendChild(chevron);

    var menu = document.createElement('div');
    menu.className = 'jb-select-menu';
    menu.setAttribute('role', 'listbox');

    var buttons = [];
    Array.prototype.forEach.call(select.options, function (option, index) {
      var item = document.createElement('button');
      item.type = 'button';
      item.className = 'jb-select-opt';
      item.setAttribute('role', 'option');
      item.textContent = option.textContent;
      item.addEventListener('click', function () { choose(index); });
      menu.appendChild(item);
      buttons.push(item);
    });

    wrap.appendChild(btn);
    wrap.appendChild(menu);

    function sync() {
      var current = select.options[select.selectedIndex];
      valueText.textContent = current ? current.textContent : '';
      buttons.forEach(function (item, index) {
        item.setAttribute('aria-selected', index === select.selectedIndex ? 'true' : 'false');
      });
    }

    function choose(index) {
      if (select.selectedIndex !== index) {
        select.selectedIndex = index;
        sync();
        select.dispatchEvent(new Event('input', { bubbles: true }));
        select.dispatchEvent(new Event('change', { bubbles: true }));
      }
      closeOpen();
      btn.focus();
    }

    function open() {
      if (openWrap === wrap) { closeOpen(); return; }
      closeOpen();
      openWrap = wrap;
      wrap.setAttribute('data-open', 'true');
      btn.setAttribute('aria-expanded', 'true');
      // Flipped upward when the menu would run off the bottom, which is most of
      // the time for the sort control inside the Dropped dialog.
      var room = window.innerHeight - btn.getBoundingClientRect().bottom;
      wrap.classList.toggle('drop-up', room < Math.min(menu.scrollHeight + 16, 288));
      var active = buttons[select.selectedIndex] || buttons[0];
      if (active) active.focus();
    }

    function step(delta) {
      var here = buttons.indexOf(document.activeElement);
      var next = here < 0 ? select.selectedIndex : here + delta;
      if (next < 0) next = buttons.length - 1;
      if (next >= buttons.length) next = 0;
      if (buttons[next]) buttons[next].focus();
    }

    btn.addEventListener('click', open);
    btn.addEventListener('keydown', function (event) {
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); open(); }
    });
    menu.addEventListener('keydown', function (event) {
      if (event.key === 'ArrowDown') { event.preventDefault(); step(1); }
      else if (event.key === 'ArrowUp') { event.preventDefault(); step(-1); }
      else if (event.key === 'Escape') { event.preventDefault(); closeOpen(); btn.focus(); }
      else if (event.key === 'Tab') closeOpen();
    });

    // An implicit label forwards its clicks to the control it wraps, and the
    // control it wraps is now invisible. Send them to the button instead.
    var label = select.closest ? select.closest('label') : null;
    if (label) label.addEventListener('click', function (event) {
      if (btn.contains(event.target) || menu.contains(event.target)) return;
      event.preventDefault();
      btn.focus();
    });

    // Saved searches and Clear both assign to .value directly and dispatch
    // nothing, so a listener alone would let the face drift out of step with
    // the control it is meant to be showing.
    Object.defineProperty(select, 'value', {
      configurable: true,
      get: function () { return nativeValue.get.call(this); },
      set: function (next) { nativeValue.set.call(this, next); sync(); }
    });
    select.addEventListener('change', sync);
    sync();
  }

  document.addEventListener('click', function (event) {
    if (openWrap && !openWrap.contains(event.target)) closeOpen();
  });
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') closeOpen();
  });
  document.querySelectorAll('select').forEach(enhance);
  window.jobbyEnhanceSelects = function (root) {
    (root || document).querySelectorAll('select').forEach(enhance);
  };
})();
"""

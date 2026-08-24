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
    --hair:rgba(47,43,55,.12); --blue:#37388f; --purple:#5150a9;
    --green:#347157; --green-bg:#e5f1e8; --orange:#a65a32;
    --red:#a6403d; --red-ink:#963936; --btn-g:#37388f; --btn-g-ink:#fff;
    --t-o:rgba(166,90,50,.09); --t-g:rgba(52,113,87,.10);
    --t-p:rgba(81,80,169,.09); --t-red:rgba(166,64,61,.09);
    --sh:0 1px 2px rgba(34,29,45,.04),0 14px 38px rgba(34,29,45,.055);
    --sh-hover:0 4px 8px rgba(34,29,45,.055),0 22px 54px rgba(34,29,45,.10);
    --radius:15px; --ease:cubic-bezier(.22,1,.36,1);
  }
  @media (prefers-color-scheme:dark) {
    :root:not([data-theme="light"]) {
      --bg:#141519; --card:#1d1e23; --sunk:#27282e; --seg:#222329;
      --ink:#f2eee7; --ink-2:#b6b0aa; --ink-3:#85828a;
      --hair:rgba(255,255,255,.105); --blue:#a6a7ff; --purple:#b9a8ff;
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
    --hair:rgba(255,255,255,.105); --blue:#a6a7ff; --purple:#b9a8ff;
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
  .appbar { width:100%; max-width:1360px; min-height:5.4rem; margin:0 auto;
    padding:1rem clamp(1rem,3vw,2.5rem); border-bottom:1px solid var(--hair);
    position:relative; view-transition-name:jobby-header; }
  .lockup { height:1.85rem; }
  .primary-nav { padding:.28rem; border-radius:13px; background:var(--seg); gap:.15rem; }
  .primary-nav a { min-height:2.45rem; border-radius:10px; padding:.48rem 1.2rem;
    font-weight:620; transition:background-color .28s var(--ease),color .24s,transform .24s var(--ease); }
  .primary-nav a:hover { transform:translateY(-1px); }
  .primary-nav a[aria-current="page"] { box-shadow:0 1px 3px rgba(30,26,38,.11); }

  .themeseg { padding:3px; border:1px solid var(--hair); border-radius:13px;
    background:var(--seg); gap:2px; }
  .themebtn { width:2.15rem; min-width:2.15rem; height:2rem; padding:0;
    border-radius:9px; font-size:.72rem; font-weight:620;
    transition:background-color .3s var(--ease),color .24s,transform .2s var(--ease); }
  .themebtn:hover { transform:translateY(-1px); }
  .themebtn svg { width:.92rem; height:.92rem; }

  .wrap { max-width:1360px; padding:clamp(2rem,4vw,3.5rem) clamp(1rem,3vw,2.5rem) 7rem; }
  .top { display:grid; grid-template-columns:minmax(0,1fr) auto; align-items:center;
    gap:1.25rem; text-align:left; margin-bottom:2.4rem; }
  .hero-copy { min-width:0; }
  .top h1 { margin:0 0 .3rem; font-family:"Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif;
    font-size:clamp(2.25rem,4.6vw,4.2rem); font-weight:500; line-height:.98;
    letter-spacing:-.045em; }
  .lede { font-size:1rem; }
  .stamp { margin:.35rem 0 0; }
  .jb-morph { width:68px; height:68px; margin:0; }
  .jb-morph .heromark { width:68px; height:68px; }
  .jb-mascot2 { height:114px; }

  .segwrap { position:sticky; top:.7rem; margin-bottom:1.6rem; z-index:20; }
  .seg { width:100%; display:grid; grid-template-columns:repeat(5,minmax(0,1fr));
    padding:0; gap:0; overflow:visible; border:1px solid var(--hair);
    border-radius:var(--radius); background:var(--card); box-shadow:var(--sh); }
  .tab { min-height:4.3rem; justify-content:center; border-radius:0; padding:.8rem 1rem;
    position:relative; font-weight:570; transition:color .24s,background-color .3s var(--ease); }
  .tab:first-child { border-radius:calc(var(--radius) - 1px) 0 0 calc(var(--radius) - 1px); }
  .tab:last-child { border-radius:0 calc(var(--radius) - 1px) calc(var(--radius) - 1px) 0; }
  .tab + .tab { border-left:1px solid var(--hair); }
  .tab.on { color:var(--blue); background:var(--t-p); box-shadow:none; }
  .tab.on::after { content:""; position:absolute; left:1rem; right:1rem; bottom:0;
    height:3px; border-radius:3px 3px 0 0; background:var(--blue); }
  .tab .n { background:var(--sunk); }

  .panel { view-transition-name:jobby-content; animation:premium-panel-in .46s var(--ease); }
  @keyframes premium-panel-in { from { opacity:0; transform:translateY(12px); }
    to { opacity:1; transform:none; } }
  ::view-transition-old(jobby-content) { animation:premium-old .2s ease both; }
  ::view-transition-new(jobby-content) { animation:premium-new .42s var(--ease) both; }
  @keyframes premium-old { to { opacity:0; transform:translateY(-6px); } }
  @keyframes premium-new { from { opacity:0; transform:translateY(10px); } }

  .card,.sheet,.empty,.note,.jb-banner,.jb-filters,.jb-addlink,.provider-card,.split-note {
    border:1px solid var(--hair); border-radius:var(--radius); box-shadow:var(--sh); }
  .card { border-radius:var(--radius); padding:1.25rem; transition:transform .38s var(--ease),
    box-shadow .38s var(--ease),border-color .28s,background-color .3s; }
  .card:hover { transform:translateY(-4px) scale(1.004); box-shadow:var(--sh-hover);
    border-color:color-mix(in srgb,var(--blue) 24%,var(--hair)); }
  .card.hold,.card.ready,.card.ref,.card.flight { background:var(--card); }
  .card.hold { border-top:3px solid var(--orange); }
  .card.ready { border-top:3px solid var(--green); }
  .card.ref,.card.flight { border-top:3px solid var(--purple); }
  .grid { grid-template-columns:repeat(3,minmax(0,1fr)); gap:1rem; }
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
  .jb-grid { --cols:1; gap:.8rem; }
  .jb-col { gap:.8rem; }
  .jb-banner,.jb-filters,.jb-addlink { border-radius:var(--radius); background:var(--card); }
  .jb-card { display:grid; grid-template-columns:minmax(15rem,1.25fr) minmax(18rem,1.8fr) minmax(15rem,auto);
    grid-template-rows:auto auto auto; align-items:center; gap:.7rem 1.25rem;
    border:1px solid var(--hair); border-radius:var(--radius); padding:1rem 1.1rem;
    box-shadow:var(--sh); transition:transform .4s var(--ease),box-shadow .4s var(--ease),
    border-color .28s,background-color .3s; }
  .jb-card:hover,.jb-card.open { transform:translateY(-4px) scale(1.003);
    box-shadow:var(--sh-hover); border-color:color-mix(in srgb,var(--blue) 25%,var(--hair)); }
  .jb-head { grid-column:1; grid-row:1 / span 2; align-items:center; justify-content:space-between; }
  .jb-identity { display:flex; align-items:center; gap:.72rem; min-width:0; }
  .jb-identity > div { min-width:0; }
  .jb-sum { grid-column:2; grid-row:1; -webkit-line-clamp:2; }
  .jb-chips { grid-column:2; grid-row:2; }
  .jb-acts { grid-column:3; grid-row:1 / span 2; justify-content:flex-end;
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
  .company-mark img { position:absolute; inset:0; width:100%; height:100%; object-fit:contain;
    padding:.32rem; background:var(--card); opacity:0; transition:opacity .3s ease; }
  .company-mark img.loaded { opacity:1; }
  .company-title,.company-cell { display:flex; align-items:center; gap:.72rem; min-width:0; }
  .company-title > div,.company-cell > span { min-width:0; }
  .company-cell .company-mark { width:2.15rem; height:2.15rem; border-radius:9px; }

  .settings-page { max-width:1180px; padding:1rem 0 5rem; view-transition-name:jobby-content; }
  .settings-titlebar { margin-bottom:1.5rem; }
  .settings-titlebar h2,.dialog-head h2 { font-family:"Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif;
    font-weight:500; letter-spacing:-.035em; }
  .split-note { background:var(--card); padding:1.15rem 1.25rem; }
  .provider-grid { gap:1rem; }
  .provider-card { border-radius:var(--radius); padding:1.15rem;
    transition:transform .34s var(--ease),box-shadow .34s var(--ease),border-color .25s; }
  .provider-card:hover { transform:translateY(-4px); box-shadow:var(--sh-hover);
    border-color:color-mix(in srgb,var(--blue) 22%,var(--hair)); }

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

  @media (max-width:1100px) {
    .jb-card { grid-template-columns:minmax(14rem,1fr) minmax(18rem,1.5fr); }
    .jb-head { grid-column:1; }
    .jb-sum,.jb-chips { grid-column:2; }
    .jb-acts { grid-column:1 / -1; grid-row:3; justify-content:flex-start; padding-top:.75rem;
      border-top:1px solid var(--hair); }
    .jb-det { grid-row:4; }
    .jb-slot { grid-row:5; }
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
    .jb-morph,.jb-morph .heromark { width:56px; height:56px; }
    .jb-mascot2 { height:92px; }
  }
  @media (prefers-reduced-motion:reduce) {
    ::view-transition-group(*),::view-transition-old(*),::view-transition-new(*) { animation:none !important; }
    .dropped-dialog[open],.dropped-dialog::backdrop { animation:none; }
  }
"""

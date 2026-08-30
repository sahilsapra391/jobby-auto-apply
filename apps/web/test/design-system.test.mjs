/**
 * The Soft Premium constraints, as tests rather than as comments.
 *
 * Every rule here cost something to learn on the old board, and CLAUDE.md
 * records what. A rewrite that carries the tokens but not the constraints
 * rediscovers all of it: two panels differing by tens of thousands of pixels
 * stretched into each other, a 50,000px layer rasterizing blank, a page that
 * looks right in light mode and borrows the host's background in dark.
 *
 * Comments do not survive a refactor. These do.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const css = readFileSync(join(here, "..", "app", "globals.css"), "utf8");

/** CSS with every comment removed: the rules, not the prose about them. */
const rules = css.replace(/\/\*[\s\S]*?\*\//g, "");

test("the light palette is defined on bare :root", () => {
  // Not inside a media query. The default "system" setting stamps no
  // data-theme, so a palette defined only under a media block leaves the
  // light case with no values at all.
  const bare = rules.match(/(^|\})\s*:root\s*\{([\s\S]*?)\}/);
  assert.ok(bare, "no bare :root block");
  for (const token of ["--bg", "--card", "--ink", "--hair", "--radius"]) {
    assert.match(bare[2], new RegExp(`${token}\\s*:`), `${token} missing from :root`);
  }
});

test("the dark palette is guarded so an explicit light choice wins", () => {
  assert.match(
    rules,
    /@media\s*\(prefers-color-scheme:\s*dark\)\s*\{\s*:root:not\(\[data-theme="light"\]\)/,
    "the dark media block is not guarded with :root:not([data-theme=\"light\"]), " +
      "so choosing light explicitly would not survive a dark system setting",
  );
});

test("an explicit dark choice wins without a dark system setting", () => {
  assert.match(rules, /:root\[data-theme="dark"\]\s*\{/,
    "there is no [data-theme=\"dark\"] block, so the toggle only works one way");
});

test("every colour token has a definition outside any conditional block", () => {
  // The failure this prevents: a token defined only under the dark media
  // query is undefined in light mode, and the property silently falls back to
  // its initial value.
  const bare = rules.match(/(^|\})\s*:root\s*\{([\s\S]*?)\}/)[2];
  const declared = new Set([...bare.matchAll(/(--[\w-]+)\s*:/g)].map((m) => m[1]));
  const used = new Set([...rules.matchAll(/var\((--[\w-]+)\)/g)].map((m) => m[1]));
  const missing = [...used].filter((t) => !declared.has(t));
  assert.deepEqual(missing, [],
    `these tokens are used but never defined on bare :root: ${missing.join(", ")}`);
});

test("body paints an explicit token background", () => {
  // A transparent body borrows whatever the host paints behind it.
  assert.match(rules, /body\s*\{[^}]*background:\s*var\(--bg\)/,
    "body has no explicit background, so it borrows the host's");
});

test("no panel carries a view-transition-name", () => {
  // Two panels can differ in height by tens of thousands of pixels. The
  // transition group animates that size and stretches both snapshots to fit.
  const panelBlocks = [...rules.matchAll(/\.jb-panel[^{]*\{([^}]*)\}/g)];
  assert.ok(panelBlocks.length > 0, "no .jb-panel rule found at all");
  for (const [, body] of panelBlocks) {
    assert.doesNotMatch(body, /view-transition-name/,
      "a panel has a view-transition-name; a tab switch must get none");
  }
});

test("only the app bar is given a view-transition-name", () => {
  // Parsed block by block rather than by one regex over the whole file. The
  // first version swept `view-transition-name:` with a negative lookahead for
  // `none` and still matched the reduced-motion `* { ...: none !important }`,
  // because the lookahead landed inside a neighbouring declaration rather than
  // on the value. It reported the rule that disables transitions as one that
  // creates them.
  const named = [];
  for (const [, selector, body] of rules.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const decl = body.match(/view-transition-name\s*:\s*([^;]+)/);
    if (!decl) continue;
    if (decl[1].trim().startsWith("none")) continue;   // a disable, not a name
    named.push(selector.trim().split("\n").pop().trim());
  }
  assert.deepEqual(named, [".jb-appbar"],
    `expected only .jb-appbar to be transition-named, got: ${named.join(", ") || "nothing"}`);
});

test("no panel keyframe uses a fill mode", () => {
  // The Found panel runs past 50,000px. A transform held on a layer that tall
  // exceeds the max texture and rasterizes blank.
  const anim = rules.match(/\.jb-panel\s*\{[^}]*animation:([^;]+);/);
  assert.ok(anim, "the panel has no animation to check");
  for (const mode of ["forwards", "backwards", "both"]) {
    assert.ok(!anim[1].includes(mode),
      `the panel animation uses fill mode "${mode}"; a held transform on a ` +
      `50,000px layer rasterizes blank`);
  }
});

test("the panel keyframe animates no compositor-promoting property", () => {
  // opacity and clip-path both promote the layer, and Chrome rasterizes a
  // promoted layer coarse for the length of the animation.
  const frames = rules.match(/@keyframes\s+jb-panel-in\s*\{([\s\S]*?)\n\}/);
  assert.ok(frames, "no jb-panel-in keyframes");
  assert.doesNotMatch(frames[1], /transform\s*:/,
    "the panel keyframe animates transform on a layer that can exceed the max texture");
});

test("surfaces hover without moving", () => {
  // Nothing on the board answers hover by moving except a control you press.
  for (const selector of [".jb-row", ".jb-pill", ".jb-tab"]) {
    const block = rules.match(new RegExp(`\\${selector}:hover\\s*\\{([^}]*)\\}`));
    assert.ok(block, `${selector}:hover is not defined`);
    assert.doesNotMatch(block[1], /transform\s*:\s*(?!none)/,
      `${selector} moves on hover; only a control you press may lift`);
  }
});

test("a button may lift, because a button is pressed", () => {
  const block = rules.match(/\.jb-btn:hover\s*\{([^}]*)\}/);
  assert.ok(block, ".jb-btn:hover is not defined");
  assert.match(block[1], /transform\s*:\s*translateY/,
    "the lift was removed from buttons; the distinction is the point");
});

test("reduced motion turns decorative motion off rather than slowing it", () => {
  const block = rules.match(/@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{([\s\S]*?)\n\}/);
  assert.ok(block, "there is no reduced-motion block at all");
  assert.match(block[1], /animation:\s*none/, "reduced motion does not stop animation");
  assert.match(block[1], /transition:\s*none/, "reduced motion does not stop transitions");
  assert.match(block[1], /view-transition-name:\s*none/,
    "reduced motion leaves view transitions running");
});

test("the shell width is a token, so the bar and the wrap cannot disagree", () => {
  // Every lane is one width. The old board had card lanes stopping at 1360
  // while table lanes broke out to 1800, which is why Found looked like it was
  // using half the screen next to Watching.
  assert.match(rules, /--shell:\s*1800px/);
  assert.match(rules, /\.jb-wrap\s*\{[^}]*max-width:\s*var\(--shell\)/);
});

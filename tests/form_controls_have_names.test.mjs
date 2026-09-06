// Every control inside a form that submits carries a `name`.
//
// **A control without a name looks exactly like one that works.** It renders,
// it is interactive, the reader picks a value, and the browser never sends it:
// a form control with no name is omitted from the submission entirely. The
// whole Advanced search card was built that way -- Locations, Max experience,
// hide-mentioning, the company include/exclude, every workplace checkbox --
// and the four controls that did work were the four that had names.
//
// It took a user report, and worse, it survived a diagnosis. "The server side
// works" was measured, true, and beside the point, because the measurement was
// taken against `q`, `within`, `direct` and `sort`. That is the
// substituted-subject shape: the right assertion about the wrong subject.
//
// **This parses rather than greps.** A regex over TSX cannot tell a `<select>`
// in a comment from one in the tree, and this repository has three separate
// afternoons lost to exactly that. The TypeScript compiler gives a real JSX
// tree, so prose is not a control and a control is not prose.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { createRequire } from 'node:module';

const REPO = join(dirname(fileURLToPath(import.meta.url)), '..');
const UI = join(REPO, 'apps/web/app/_ui');
const ts = createRequire(join(REPO, 'apps/web/package.json'))('typescript');

const CONTROLS = new Set(['input', 'select', 'textarea']);

/**
 * A control may go without a name only by saying so and why.
 *
 * `data-decorative` takes a reason, because "this one is fine" with no reason
 * is how an exemption list becomes the thing it was guarding against.
 */
const DECORATIVE = 'data-decorative';

function controlsUnderForms(file) {
  const source = ts.createSourceFile(
    file, readFileSync(file, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const found = [];

  const tagOf = (node) => {
    const open = ts.isJsxElement(node) ? node.openingElement
      : ts.isJsxSelfClosingElement(node) ? node : null;
    return open ? open.tagName.getText(source) : null;
  };
  const attrs = (node) => {
    const open = ts.isJsxElement(node) ? node.openingElement : node;
    const out = new Map();
    for (const a of open.attributes.properties) {
      if (ts.isJsxAttribute(a)) out.set(a.name.getText(source), a);
    }
    return out;
  };

  // Depth-first, carrying whether we are inside a <form>. A control's own
  // ancestry is what decides whether it is submitted, so the flag travels down
  // rather than being asked for afterwards.
  const walk = (node, inForm) => {
    const tag = tagOf(node);
    const nowInForm = inForm || tag === 'form';
    if (tag && CONTROLS.has(tag)) {
      const a = attrs(node);
      const { line } = source.getLineAndCharacterOfPosition(node.getStart());
      found.push({
        tag, line: line + 1, inForm: nowInForm,
        named: a.has('name'),
        decorative: a.get(DECORATIVE)?.initializer?.getText(source) ?? null,
        type: a.get('type')?.initializer?.getText(source) ?? '',
      });
    }
    node.forEachChild((child) => walk(child, nowInForm));
  };
  walk(source, false);
  return found;
}

function everyControl() {
  const out = [];
  for (const name of readdirSync(UI)) {
    if (!name.endsWith('.tsx')) continue;
    for (const c of controlsUnderForms(join(UI, name))) out.push({ file: name, ...c });
  }
  return out;
}

test('the parser finds controls at all, so this cannot pass vacuously', () => {
  const all = everyControl();
  assert.ok(all.length >= 8,
            `only ${all.length} form controls found across apps/web/app/_ui. ` +
            `A walker that has stopped walking reports full compliance.`);
  assert.ok(all.some((c) => c.named), 'no named control found; the parse is wrong');
});

test('every control is submitted, or says why not', () => {
  // **Two ways to be inert, and the first version of this test only caught
  // one.** It required a name and looked only at controls inside a form, and
  // it passed against the very card that prompted it: the Advanced search
  // block sits *outside* the form entirely -- the form closes and the
  // `<details>` opens after it -- so those controls would not submit even if
  // every one were named. A guard that passes against the bug it was written
  // for is the vacuous check this repository has now met a dozen times, and
  // the only reason it was caught is that it went green when it should not.
  const inert = everyControl().filter(
    (c) => !c.decorative && (!c.inForm || !c.named));
  assert.deepEqual(
    inert, [],
    'these controls cannot reach the server. A control outside a form is ' +
    'never submitted, and a control without a `name` is omitted from the ' +
    'submission -- both render, take a value, and do nothing, which is ' +
    'indistinguishable from a filter that works and matches nothing:\n' +
    inert.map((c) => `  ${c.file}:${c.line} <${c.tag}${c.type ? ' type=' + c.type : ''}>` +
                     `${c.inForm ? ' has no name' : ' is outside any form'}`)
      .join('\n') +
    `\n\nPut it in the form and name it, or mark it ` +
    `${DECORATIVE}="why it is not submitted".`);
});

test('a decorative marker carries a reason rather than being a bare flag', () => {
  const bare = everyControl()
    .filter((c) => c.decorative !== null && String(c.decorative).replace(/["'{}]/g, '').trim().length < 12);
  assert.deepEqual(bare.map((c) => `${c.file}:${c.line}`), [],
    `${DECORATIVE} must say why. A bare marker is an exemption nobody can ` +
    `review, which is how an exemption list becomes the thing it guards against.`);
});

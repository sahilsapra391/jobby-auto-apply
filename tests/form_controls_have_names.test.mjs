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

function controlsUnderForms(file, text) {
  const source = ts.createSourceFile(
    file, text ?? readFileSync(file, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
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
  // The identifiers a `value={x}` binds to, so `controlled` can ask whether
  // anything else in the file reads x. Collected in the same pass rather than
  // re-parsed, because a second parse is a second thing to keep in step.
  const bindings = new Map();
  const readsOf = new Map();

  let sawForm = false;
  const walk = (node, inForm) => {
    const tag = tagOf(node);
    const nowInForm = inForm || tag === 'form';
    if (tag === 'form') sawForm = true;
    if (tag && CONTROLS.has(tag)) {
      const a = attrs(node);
      const { line } = source.getLineAndCharacterOfPosition(node.getStart());
      const bound = boundIdentifier(a.get('value'));
      if (bound) bindings.set(node, bound);
      found.push({
        node,
        tag, line: line + 1, inForm: nowInForm,
        named: a.has('name'),
        bound,
        hasOnChange: a.has('onChange'),
        decorative: a.get(DECORATIVE)?.initializer?.getText(source) ?? null,
        type: a.get('type')?.initializer?.getText(source) ?? '',
      });
    }
    node.forEachChild((child) => walk(child, nowInForm));
  };

  // Every identifier read in the file that is NOT a `value=` binding and NOT
  // the `const [x, setX] = useState()` that declares it. What is left is
  // somebody using the value.
  const valueAttrExpressions = new Set();
  const collectValueAttrs = (node) => {
    if (ts.isJsxAttribute(node) && node.name.getText(source) === 'value'
        && node.initializer && ts.isJsxExpression(node.initializer)
        && node.initializer.expression) {
      valueAttrExpressions.add(node.initializer.expression);
    }
    node.forEachChild(collectValueAttrs);
  };
  collectValueAttrs(source);

  const countReads = (node) => {
    if (ts.isIdentifier(node) && !valueAttrExpressions.has(node)) {
      const parent = node.parent;
      const isDeclaration =
        (ts.isBindingElement(parent) || ts.isVariableDeclaration(parent))
        && parent.name === node;
      const isPropertyName = ts.isPropertyAssignment(parent) && parent.name === node;
      const isMemberName = ts.isPropertyAccessExpression(parent) && parent.name === node;
      if (!isDeclaration && !isPropertyName && !isMemberName) {
        readsOf.set(node.getText(source), (readsOf.get(node.getText(source)) ?? 0) + 1);
      }
    }
    node.forEachChild(countReads);
  };
  countReads(source);

  walk(source, false);
  for (const c of found) {
    // **The third way a control reaches the server, and the two the guard had
    // could not see it.** `named` and `inForm` both assume the value travels
    // by form submission. A controlled React input's value lives in state and
    // travels in a JSON body a click handler builds, so a `name` on it would
    // do nothing at all. The claim is checkable and it is checked: the bound
    // identifier has to be read somewhere that is not its own declaration and
    // not the `value=` attribute, which is the same "a script names it"
    // notion `controls_reach_something` uses for buttons. A controlled input
    // nothing reads is still inert and still fails.
    c.controlled = Boolean(c.bound && c.hasOnChange && (readsOf.get(c.bound) ?? 0) > 0);
    delete c.node;
  }
  return { controls: found, sawForm };
}

/** The identifier inside `value={x}`, or null for `value="literal"`, `value={x.y}`
 *  and anything else this cannot follow. Null is "cannot look", and a control
 *  it cannot look at is judged by the two older rules rather than excused. */
function boundIdentifier(attr) {
  const init = attr?.initializer;
  if (!init || !init.expression) return null;
  return init.expression.kind === ts.SyntaxKind.Identifier
    ? init.expression.getText() : null;
}

/**
 * The whole rule, in one place.
 *
 * The mutation tests below had their own copy of this and that is the
 * declared-twice shape this repository has now met five times: a comparison
 * whose two sides drift, where the half nobody looks at decides what the check
 * can see. Both sides call this.
 */
function isInert(c, fileHasForm) {
  return !c.decorative && !c.controlled && (!c.named || (fileHasForm && !c.inForm));
}

function everyControl() {
  const out = [];
  for (const name of readdirSync(UI)) {
    if (!name.endsWith('.tsx')) continue;
    // **`fileHasForm` was a regex over the raw source, in the file whose own
    // header says a regex cannot tell a control in a comment from one in the
    // tree.** It read `<form method="get">` inside two docstrings explaining
    // why those components deliberately have no form, and judged both files as
    // form-bearing on the strength of the sentence saying they are not. The
    // careful half and the careless half were twelve lines apart. It comes off
    // the same AST walk as everything else now.
    const { controls, sawForm } = controlsUnderForms(join(UI, name));
    for (const c of controls) out.push({ file: name, fileHasForm: sawForm, ...c });
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
  // **A file with no form of its own cannot decide where its controls land.**
  // `LocationPicker` is a fragment the Found card renders inside its form;
  // asking it to contain one would be asking the wrong component. What it does
  // control is the name, so that is what is required of it. A file that does
  // have a form is judged on both, because there the placement is its choice.
  const inert = everyControl().filter((c) => isInert(c, c.fileHasForm));
  assert.deepEqual(
    inert, [],
    'these controls cannot reach the server. A control outside a form is ' +
    'never submitted, and a control without a `name` is omitted from the ' +
    'submission -- both render, take a value, and do nothing, which is ' +
    'indistinguishable from a filter that works and matches nothing:\n' +
    inert.map((c) => `  ${c.file}:${c.line} <${c.tag}${c.type ? ' type=' + c.type : ''}>` +
                     `${!c.named ? ' has no name'
                                  : ' is outside its file\'s form'}`)
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

// **Three mutations, because the rule above was widened and a widened guard
// that has not been made to fail is a guard nobody has checked.** Each feeds
// the parser a source it should refuse, and each fails if the corresponding
// half of the rule is removed. Verified by removing them one at a time.

/** One control's verdict, from source text rather than from the tree on disk. */
function judge(text) {
  const { controls, sawForm } = controlsUnderForms('mutation.tsx', text);
  return controls.map((c) => ({ ...c, fileHasForm: sawForm, inert: isInert(c, sawForm) }));
}

test('a controlled input nothing reads is still inert', () => {
  // The narrow claim `controlled` makes is that somebody reads the state. An
  // input that binds a value and a setter and is never read again renders,
  // takes a value and drops it, which is precisely what this file exists for.
  const [stranded] = judge(`
    export function X() {
      const [url, setUrl] = useState("");
      return <input type="url" value={url} onChange={(e) => setUrl(e.target.value)} />;
    }`);
  assert.equal(stranded.controlled, false,
               'a bound value nobody reads was excused as controlled');
  assert.equal(stranded.inert, true);

  const [wired] = judge(`
    export function X() {
      const [url, setUrl] = useState("");
      const go = () => send("/api/scan", { url: url.trim() });
      return <input type="url" value={url} onChange={(e) => setUrl(e.target.value)} />;
    }`);
  assert.equal(wired.controlled, true, 'a value read by a handler was called inert');
  assert.equal(wired.inert, false);
});

test('the real tree, not a fixture: prose about a form is not a form', () => {
  // **The mutation for this one has to run against `everyControl`, because
  // that is where the regex was.** The fixture test below proves the parser
  // reports `sawForm` correctly and says nothing about who reads it, so
  // reverting `everyControl` to the regex left it green -- a mutation aimed at
  // something the test does not exercise, which this repository has written up
  // once already and repeated here within the hour.
  //
  // So this asks the question on the files that actually disagree. It is
  // deliberately floored: if nobody's docstring mentions a `<form>` any more
  // the test has no subject left, and it says so rather than passing.
  const disagreeing = readdirSync(UI).filter((name) => {
    if (!name.endsWith('.tsx')) return false;
    const text = readFileSync(join(UI, name), 'utf8');
    return /<form[\s>]/.test(text) && !controlsUnderForms(join(UI, name)).sawForm;
  });
  assert.ok(disagreeing.length > 0,
            'no file mentions a <form> in prose without having one, so this ' +
            'test has no subject and proves nothing about everyControl');
  const byFile = new Map(everyControl().map((c) => [c.file, c.fileHasForm]));
  for (const name of disagreeing) {
    if (!byFile.has(name)) continue;   // no controls in it; nothing to judge
    assert.equal(byFile.get(name), false,
                 `${name} has no <form> in its tree and everyControl says it does. ` +
                 'That is the regex reading a comment, and every control in the ' +
                 'file is then judged on a placement the file never chose.');
  }
});

test('a form written in a comment is not a form', () => {
  // The fault this replaced: `<form method="get">` inside a docstring
  // explaining why the component has none made the file form-bearing, so every
  // control in it was judged on a placement the file never chose.
  const prose = judge(`
    /** The board did this inside a \`<form method="get">\` and v2 does not. */
    export function X() { return <input name="q" />; }`);
  assert.equal(prose[0].fileHasForm, false,
               'a <form> in a comment was read as a form in the tree');

  // The control, because a strip that hides a real one is worse than the bug.
  const real = judge('export function X() { return <form><input name="q" /></form>; }');
  assert.equal(real[0].fileHasForm, true, 'a real <form> went unseen');
  assert.equal(real[0].inForm, true);
});

test('an unnamed uncontrolled control outside a form still fails', () => {
  // The original rule, unchanged. Both older notions have to keep biting or
  // the widening bought silence rather than accuracy.
  const [c] = judge('export function X() { return <form></form>; }\n'
                    + 'export function Y() { return <input type="text" />; }');
  assert.equal(c.inert, true, 'an unnamed control in a form-bearing file passed');
});

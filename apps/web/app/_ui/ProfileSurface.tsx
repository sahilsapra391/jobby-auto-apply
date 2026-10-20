"use client";

import { useState } from "react";
import { categoryLabel } from "./bankLabels";
import type { BankEntry, BaseResume } from "./db";
import { EditStateLine, slotOf, useEdits } from "./ProfileEdits";

/**
 * The controls on the Profile page. Every one of them enqueues and none of
 * them writes.
 *
 * **Nothing here draws the change it asked for.** The row keeps showing what
 * `answer_bank` holds until the device says otherwise, with the state of the
 * edit beside it. Painting the new answer in would be v2 asserting an outcome
 * `store.py` has not reached, and `store.py` genuinely refuses edits -- a
 * version conflict, a question the product will not answer, a one-time code.
 * That gap between "shown" and "saved" is the thing `edit_outcomes` exists to
 * close, so opening it here would be perverse.
 *
 * **A destructive control confirms, and the confirmation moves.** Same rule as
 * `ApplyWithConfirm`: a button that turns into its own confirmation is armed
 * exactly where an impatient double click is already landing, so the question
 * replaces the row of controls and the confirming button is somewhere the
 * second click was never going.
 *
 * **The category label comes from `_ui/bankLabels`, which is neither side.**
 * It was a prop from the server component first and an export from this file
 * second, and both threw. That file records why.
 */

/** Why a control is dark. Read from the account's own flag, not assumed. */
const OFF = "Writes are off for this account.";

function Confirm({ question, verb, onYes, onNo }: {
  question: string; verb: string; onYes: () => void; onNo: () => void;
}) {
  return (
    <div className="bank-act">
      <span className="bank-note">{question}</span>
      <button className="btn ghost" type="button" onClick={onNo}>Cancel</button>
      <button className="btn" type="button" onClick={onYes}>{verb}</button>
    </div>
  );
}

// ------------------------------------------------------------- Memory Bank

/**
 * One stored answer, with the four things a reader can do to it.
 *
 * `dismiss` and `restore` are one pair and `delete` is not their sibling: a
 * dismissal refuses a *question* the reader will never answer, which is what
 * stops the grow-only loop reopening it, and Forget removes an *answer*.
 * `store.py` keeps them apart to the point of refusing to dismiss a record
 * that still holds an answer, and the wording here says which is which rather
 * than offering two buttons that look interchangeable.
 */
export function BankRow({ entry }: { entry: BankEntry }) {
  const { enabled, send } = useEdits();
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(entry.value ?? "");
  const [sensitive, setSensitive] = useState(entry.state === "sensitive");
  const [asking, setAsking] = useState<"delete" | null>(null);
  const [local, setLocal] = useState<string | null>(null);
  const slot = slotOf("answerEdits", entry.key);

  const dispatch = async (body: Record<string, unknown>) => {
    setLocal(null);
    setAsking(null);
    setEditing(false);
    // **The version travels with the edit, and it is the one that was read.**
    // `store.py` compares it against what it holds and refuses a mismatch as
    // `conflict`, which is what stops this board overwriting an answer that
    // moved while the page was open. Sending a constant would defeat that
    // check rather than satisfy it.
    setLocal(await send({ kind: "answer", slot, key: entry.key,
                          version: entry.version, ...body }));
  };

  return (
    <article className={entry.state === "needs-you" ? "bank-row open"
      : entry.state === "dismissed" ? "bank-row struck" : "bank-row"}>
      <p className="bank-q">{entry.question ?? entry.key}</p>
      {/* **Drawn plainly, including the sensitive ones.** CLAUDE.md settles
          this: one reader, who knows his own salary and his own demographics,
          and a tap to reveal was friction protecting nobody. The flag stays on
          the row because it is what a second account will mask on. */}
      {entry.value ? <p className="bank-a">{entry.value}</p> : (
        <p className="bank-a none">
          {entry.state === "dismissed"
            ? "You dismissed this. Jobby will not ask again."
            : "No answer yet."}
        </p>
      )}
      <p className="bank-meta">
        {/* The three states written out, not composed: a name assembled with
            `+` exists only after the expression runs, and both style guards
            read the source. */}
        <span className={
          entry.state === "sensitive" ? "bank-tag state-sensitive"
          : entry.state === "dismissed" ? "bank-tag state-dismissed"
          : entry.state === "missing" ? "bank-tag state-missing" : "bank-tag"}>
          {entry.state === "needs-you" ? "needs you" : (entry.state ?? "confirmed")}
        </span>
        {entry.category && entry.kind === "question"
          ? <span className="bank-tag">{categoryLabel(entry.category)}</span>
          : null}
        {entry.tags.map((t) => <span className="bank-tag" key={t}>{t}</span>)}
        {entry.updatedAt ? (
          <span className="bank-when">
            Updated {String(entry.updatedAt).slice(0, 10)}
          </span>
        ) : null}
      </p>

      {editing ? (
        <div className="bank-edit">
          <label>
            <span className="vh">Your answer to {entry.question ?? entry.key}</span>
            <textarea name="value" rows={3} value={value} autoFocus
                      onChange={(e) => setValue(e.target.value)} />
          </label>
          <label className="bank-sensitive">
            <input type="checkbox" name="sensitive" checked={sensitive}
                   onChange={(e) => setSensitive(e.target.checked)} />
            Sensitive: compensation, demographics, work authorisation
          </label>
          <div className="bank-act">
            <button className="btn ghost" type="button"
                    onClick={() => { setEditing(false); setValue(entry.value ?? ""); }}>
              Cancel
            </button>
            <button className="btn" type="button" onClick={() => dispatch({
              action: "save", question: entry.question ?? entry.key, value,
              state: sensitive ? "sensitive" : "confirmed",
            })}>Save answer</button>
          </div>
        </div>
      ) : asking === "delete" ? (
        <Confirm
          question={`Forget the answer to "${entry.question ?? entry.key}"? The
                     question stays and Jobby will ask again.`}
          verb="Forget it" onNo={() => setAsking(null)}
          onYes={() => dispatch({ action: "delete" })} />
      ) : (
        <div className="bank-act">
          {entry.state === "dismissed" ? (
            <button className="btn ghost" type="button" disabled={!enabled}
                    title={enabled ? "Let Jobby ask this again" : OFF}
                    onClick={() => dispatch({ action: "restore" })}>
              Ask me again
            </button>
          ) : (
            <>
              <button className="btn ghost" type="button" disabled={!enabled}
                      title={enabled ? undefined : OFF}
                      onClick={() => setEditing(true)}>
                {entry.value ? "Edit" : "Answer"}
              </button>
              <button className="btn ghost" type="button" disabled={!enabled}
                      title={enabled
                        ? "Refuse the question, without answering it" : OFF}
                      onClick={() => dispatch({ action: "dismiss" })}>
                Never ask
              </button>
              {entry.value ? (
                <button className="btn ghost" type="button" disabled={!enabled}
                        title={enabled ? "Remove the answer, keep the question" : OFF}
                        onClick={() => setAsking("delete")}>Forget</button>
              ) : null}
            </>
          )}
        </div>
      )}
      <EditStateLine slot={slot} local={local} />
    </article>
  );
}

// ------------------------------------------------------------ base resumes

/**
 * One base resume, with the two registry writes that are safe to offer.
 *
 * **Remove confirms and Make default does not**, because one of them can be
 * taken back from this page and the other cannot. Making a different base the
 * default is one more click away from where it started; removing a base takes
 * its countries with it, and the applications that would have used it fall to
 * whatever is default next.
 */
export function ResumeCard({ resume, fallback, children }: {
  resume: BaseResume; fallback: string; children: React.ReactNode;
}) {
  const { enabled, send } = useEdits();
  const [asking, setAsking] = useState(false);
  const [local, setLocal] = useState<string | null>(null);
  const slot = slotOf("resumeEdits", resume.key);
  const name = (resume.key ?? "").toUpperCase() || "Base";

  const dispatch = async (action: string) => {
    setAsking(false);
    setLocal(await send({ kind: "resume", slot, action, key: resume.key ?? "" }));
  };

  return (
    <article className="identity-card">
      {children}
      {asking ? (
        <Confirm
          question={`Remove the ${name} base? Postings ${resume.appliesTo.length
            ? `in ${resume.appliesTo.join(", ")}` : "it covers"} fall back to
            ${fallback}. Your device keeps the PDF; the registry entry goes.`}
          verb="Remove it" onNo={() => setAsking(false)}
          onYes={() => dispatch("remove")} />
      ) : (
        <div className="identity-act">
          {!resume.isDefault ? (
            <button className="btn ghost" type="button" disabled={!enabled}
                    title={enabled
                      ? "Use this for postings no other base claims" : OFF}
                    onClick={() => dispatch("default")}>Make default</button>
          ) : null}
          {!resume.isDefault ? (
            <button className="btn ghost" type="button" disabled={!enabled}
                    title={enabled ? undefined : OFF}
                    onClick={() => setAsking(true)}>Remove</button>
          ) : null}
        </div>
      )}
      <EditStateLine slot={slot} local={local} />
    </article>
  );
}

/**
 * Add a base resume: copy one that exists, and optionally upload its PDF.
 *
 * **The PDF goes first and travels by name.** The board's rule
 * (`profile_panel.py`): the document is posted to `/api/resume-upload`, which
 * parks it in a KV key of its own, and only then is the `add` edit sent,
 * carrying the upload's id rather than the document. A base pointing at a
 * document that failed to arrive is worse than no base at all, so a refused
 * upload sends no edit. The device takes the key when it applies the edit
 * (`watch.install_uploaded_resume`) and deletes it.
 *
 * Contact details always come from the base being copied. An uploaded PDF
 * becomes what the base is *based on*; the wording still starts from the
 * copied base, because the layout a resume renders through is the template's.
 */

/** The board's ceiling, checked here so a 9 MB scan fails before it uploads. */
const MAX_UPLOAD_BYTES = 2 * 1024 * 1024;

function readAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("That file could not be read."));
    reader.onload = () => {
      const url = String(reader.result ?? "");
      resolve(url.slice(url.indexOf(",") + 1));
    };
    reader.readAsDataURL(file);
  });
}

/** A random upload id the queue and KV both accept. */
function mintUploadId(): string {
  const bytes = new Uint8Array(12);
  crypto.getRandomValues(bytes);
  return "doc-" + Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

export function AddBaseResume({ resumes }: { resumes: BaseResume[] }) {
  const { enabled, send } = useEdits();
  const [open, setOpen] = useState(false);
  const [key, setKey] = useState("");
  const [label, setLabel] = useState("");
  const [countries, setCountries] = useState("");
  const [copyFrom, setCopyFrom] = useState(resumes[0]?.key ?? "");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [local, setLocal] = useState<string | null>(null);
  // **Frozen when the form is sent, not read from the field.** The key is what
  // names the slot, and a reader who clears the box after pressing Create
  // would otherwise watch the state line move to a slot nothing was sent on.
  const [slot, setSlot] = useState<string | null>(null);

  const codes = countries.toUpperCase().split(/[^A-Z]+/).filter(Boolean);

  return (
    <div className="identity-add">
      <button className="btn ghost" type="button" disabled={!enabled}
              title={enabled ? undefined : OFF}
              onClick={() => setOpen(!open)}>Add a base resume</button>
      {open ? (
        <div className="base-form">
          <label>Name
            <input type="text" name="label" placeholder="Europe" value={label}
                   onChange={(e) => setLabel(e.target.value)} /></label>
          <label>Key
            <input type="text" name="key" placeholder="eu" value={key}
                   onChange={(e) => setKey(e.target.value)} /></label>
          <label>Countries
            <input type="text" name="countries" placeholder="GB, DE, FR, NL"
                   value={countries}
                   onChange={(e) => setCountries(e.target.value)} /></label>
          <label>Copy from<span className="vh"> an existing base</span>
            <select name="copyFrom" value={copyFrom}
                    onChange={(e) => setCopyFrom(e.target.value)}>
              {resumes.map((r) => (
                <option key={r.key ?? ""} value={r.key ?? ""}>
                  {(r.key ?? "").toUpperCase() || "Base"}
                </option>))}
            </select></label>
          <label>Document
            <span className="base-file">
              <input type="file" name="document" accept="application/pdf,.pdf"
                     onChange={(e) => { setFile(e.target.files?.[0] ?? null); setLocal(null); }} />
              <span className="bank-note">
                {file ? `${file.name}, ${Math.round(file.size / 1024)} KB`
                  : "Using the copied base's PDF. Choose a file to use your own instead."}
              </span>
            </span>
          </label>
          <div className="bank-act">
            <button className="btn ghost" type="button"
                    onClick={() => setOpen(false)}>Cancel</button>
            <button className="btn" type="button" disabled={busy !== null}
                    onClick={async () => {
              const sending = slotOf("resumeEdits", key);
              setSlot(sending);
              setLocal(null);
              let uploadId: string | undefined;
              if (file) {
                if (file.size > MAX_UPLOAD_BYTES) {
                  setLocal(`That file is ${Math.round(file.size / 1024)} KB. The limit is 2 MB.`);
                  return;
                }
                setBusy("Uploading the document…");
                try {
                  const id = mintUploadId();
                  const res = await fetch("/api/resume-upload", {
                    method: "POST",
                    headers: {
                      "content-type": "application/json",
                      "x-jobby-request": "1",
                      "idempotency-key": id,
                    },
                    body: JSON.stringify({ uploadId: id, pdf: await readAsBase64(file) }),
                  });
                  if (!res.ok) {
                    const said = await res.json().catch(() => ({}));
                    setLocal(String(said.error ?? `the upload was refused (${res.status})`)
                             + ". No base was created.");
                    setBusy(null);
                    return;
                  }
                  uploadId = id;
                } catch (err) {
                  setLocal((err instanceof Error ? err.message : "That upload did not reach the board")
                           + " No base was created.");
                  setBusy(null);
                  return;
                }
              }
              setBusy(null);
              setOpen(false);
              setLocal(await send({ kind: "resume", slot: sending,
                                    action: "add", key, label,
                                    countries: codes, copyFrom,
                                    ...(uploadId ? { uploadId } : {}) }));
            }}>{busy ?? "Create"}</button>
          </div>
          <p className="bank-note">
            Contact details always come from the base you copy. A PDF you
            upload becomes what this base is <em>based on</em>: the text a
            tailored resume is written against. The wording still starts from
            the copied base, because the layout a resume renders through is
            the template&apos;s. The countries decide which postings use it; a
            posting matching none of them falls to the default.
          </p>
        </div>
      ) : null}
      {slot ? <EditStateLine slot={slot} local={local} /> : null}
    </div>
  );
}

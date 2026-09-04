"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * What the employer's form asked and what went into it.
 *
 * **Ported from `dashboard.py`'s `renderAnswers`, rule for rule**, because the
 * decisions in it are about honesty rather than layout and re-deriving them is
 * how one gets quietly dropped. The five that matter:
 *
 *   a `questions-only` record says so before the rows, not after -- someone
 *   scanning a column of "Answer not recorded" should already know why
 *
 *   an `unrecorded` field gets no source badge and no read-back badge, because
 *   both would be invented. It still gets an empty `ans-meta`, so the grid does
 *   not collapse and the row reads as deliberately blank
 *
 *   a `withheld` value names the class of thing it was. It was never written,
 *   so there is nothing a click could uncover
 *
 *   `readBack === "differs"` is never folded into the answer column. That is
 *   the failure the whole read-back pass exists to catch
 *
 *   `sensitive` is a property of the data, not an instruction to this
 *   component. Pay and demographics render like everything else: this is one
 *   person's own board behind his own password, and covering them was friction
 *   protecting nobody. The flag stays on the record because a second account's
 *   board is what will key masking on it.
 *
 * Delegated to `document` rather than wired per row, for the reason
 * `SubmitOnChange` documents: `LaneList` replaces the rows when it pages, so an
 * effect holding a node is holding the wrong one by the second page. Nothing
 * here holds a node.
 */

const SOURCE_LABEL: Record<string, string> = {
  stored: "answer store", profile: "profile",
  generated: "generated", provided: "you",
};

const SOURCE_WHY: Record<string, string> = {
  stored: "A confirmed answer you had already given to this question.",
  profile: "Taken from your profile: name, contact details, work history.",
  generated: "Built for this application from the tailored resume or the posting.",
  provided: "You supplied this one yourself while the application was in Needs you.",
};

type Field = {
  question?: string; answer?: string; note?: string;
  source?: string; readBack?: string; readBackValue?: string;
  unrecorded?: boolean; withheld?: string; sensitive?: boolean;
};

type Record_ = {
  capture?: string; capturedAt?: string; ats?: string; fields?: Field[];
};

/** Kept for the life of the page, like the board's own `answersCache`. */
const cache = new Map<string, Record_>();

/**
 * The badge's class, built here rather than inline.
 *
 * `className={`ans-badge ans-src-${source}`}` is correct and unreadable to the
 * scanners: `test_no_class_is_invented` tokenises class literals and came back
 * with `"generated"}`, `generated` and `||` as classes v2 had invented. The
 * interpolation is real -- the board builds this name the same way -- so the
 * fix is to keep it out of the attribute rather than to pretend it is static.
 */
function badgeClass(source?: string) {
  return "ans-badge " + "ans-src-" + (source || "generated");
}


function AnswerRow({ field }: { field: Field }) {
  return (
    <div className="ans-row">
      <p className="ans-q">{field.question || "Unlabelled field"}</p>
      <div className="ans-a">
        {field.unrecorded
          ? <span className="ans-unrecorded">Answer not recorded</span>
          : field.withheld
            ? <span className="ans-withheld">Not recorded &mdash; {field.withheld}</span>
            : <span className="ans-value">{field.answer || "(left blank)"}</span>}
      </div>
      {/* Present and empty for an unrecorded field. A source badge here would
          be a claim about where an answer came from, and there is no answer. */}
      <div className="ans-meta">
        {field.unrecorded ? null : (
          <>
            <span className={badgeClass(field.source)}
                  title={SOURCE_WHY[field.source || "generated"] || ""}>
              {SOURCE_LABEL[field.source || "generated"] || field.source}
            </span>
            {field.readBack === "differs" ? (
              <span className="ans-badge ans-differs"
                    title={field.readBackValue
                      ? `The page showed: ${field.readBackValue}`
                      : "The value read back off the page did not match what was entered."}>
                read back differently
              </span>
            ) : field.readBack === "unverified" ? (
              <span className="ans-badge ans-unverified"
                    title="This field was filled but its value was not read back off the page.">
                not read back
              </span>
            ) : null}
            {field.note ? <span className="ans-note">{field.note}</span> : null}
          </>
        )}
      </div>
    </div>
  );
}

export function AnswersModal() {
  const dialog = useRef<HTMLDialogElement | null>(null);
  const [where, setWhere] = useState("Submitted");
  const [title, setTitle] = useState("Application answers");
  const [state, setState] = useState<
    { kind: "loading" } | { kind: "error"; why: string } | { kind: "ok"; record: Record_ }
  >({ kind: "loading" });

  const load = useCallback(async (app: string) => {
    const hit = cache.get(app);
    if (hit) { setState({ kind: "ok", record: hit }); return; }
    setState({ kind: "loading" });
    try {
      const res = await fetch(
        `/api/artifact?app=${encodeURIComponent(app)}&kind=answers`,
        { credentials: "same-origin" });
      if (!res.ok) {
        // Named rather than blank. A 404 here is a record that is not there and
        // a 503 is a deployment that cannot look, and the two are not the same
        // news -- the route already keeps them apart and this keeps that.
        const body = await res.json().catch(() => null);
        setState({ kind: "error",
                   why: body?.error || `the record could not be read (${res.status})` });
        return;
      }
      const record = (await res.json()) as Record_;
      cache.set(app, record);
      setState({ kind: "ok", record });
    } catch (err) {
      setState({ kind: "error", why: String((err as Error)?.message ?? err) });
    }
  }, []);

  useEffect(() => {
    const onClick = (event: Event) => {
      const target = event.target as HTMLElement | null;
      const button = target?.closest<HTMLElement>("[data-answers]");
      if (!button) return;
      event.preventDefault();
      const app = button.dataset.app || "";
      setWhere(button.dataset.company || "Submitted");
      setTitle(button.dataset.role || "Application answers");
      const node = dialog.current;
      if (node) {
        if (node.showModal) node.showModal();
        else node.setAttribute("open", "");
      }
      void load(app);
    };
    document.addEventListener("click", onClick);
    return () => document.removeEventListener("click", onClick);
  }, [load]);

  const close = useCallback(() => {
    const node = dialog.current;
    if (!node) return;
    if (node.close) node.close();
    else node.removeAttribute("open");
  }, []);

  const record = state.kind === "ok" ? state.record : null;
  const fields = record?.fields ?? [];
  const stamp = [
    `${fields.length} ${fields.length === 1 ? "field" : "fields"}`,
    record?.capturedAt ? `captured ${record.capturedAt.slice(0, 10)}` : null,
    record?.ats || null,
  ].filter(Boolean).join(" · ");

  return (
    <dialog className="answers-dialog" id="p-answers" ref={dialog}
            aria-labelledby="answers-title">
      <div className="dialog-head">
        <div>
          <p className="eyebrow" id="answers-where">{where}</p>
          <h2 id="answers-title">{title}</h2>
        </div>
        <button className="dialog-close" type="button" onClick={close}
                aria-label="Close application answers">&times;</button>
      </div>
      <p className="hint answers-note">
        What the form asked, what went in, and where each value came from. Codes,
        passwords and government or bank identifiers were never recorded, so
        those rows say so.
      </p>
      <div id="answers-body">
        {state.kind === "loading"
          ? <p className="answers-loading">Reading the record&hellip;</p>
          : state.kind === "error"
            ? <p className="answers-loading">{state.why}</p>
            : (
              <>
                {record?.capture === "questions-only" ? (
                  <p className="ans-salvage">
                    These are the questions this form asked, kept by the session
                    that filled it. The answers were never written down, and
                    nothing here guesses at them.
                  </p>
                ) : null}
                {fields.length === 0
                  ? <p className="answers-loading">
                      The record was written but lists no fields.
                    </p>
                  : (
                    <>
                      <p className="answers-count">{stamp}</p>
                      <div className="ans-list">
                        {fields.map((field, i) => (
                          <AnswerRow field={field} key={i} />
                        ))}
                      </div>
                    </>
                  )}
              </>
            )}
      </div>
    </dialog>
  );
}

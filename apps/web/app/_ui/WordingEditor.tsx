"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { EditStateLine, useEdits, WORDING_SLOT } from "./ProfileEdits";
import { OwnerSignOut } from "./OwnerSession";

/**
 * The resume wording, edited as the document itself. Owner-only.
 *
 * A port of the retired board's editor (`profile_panel.py`, "the resume,
 * edited as a document"): the device renders the base resume through the real
 * template, this draws that HTML in a same-origin `srcdoc` iframe, marks the
 * template's text slots `contenteditable`, and reads them back out on Save in
 * the same order `resume_build.extract` matches them. The board measured the
 * iframe against a shadow root and kept the iframe because the resume's own
 * stylesheet targets `html` and `body`, which a shadow root does not have;
 * rendered there, the banner came out in `system-ui` rather than Calibri
 * Light, and a preview in the wrong typeface cannot claim to be the PDF.
 *
 * **Nothing here is fetched until the owner opens it.** The page never embeds
 * the wording; this asks `GET /api/profile/wording`, which answers only a
 * signed-in owner, when the section is opened.
 */

type Published = {
  content: Record<string, unknown>;
  render: string;
  publishedAt: string | null;
};

/** The template's slots, the same ones `resume_build.extract` matches on. */
const SLOTS = [
  { field: "banner", sel: ".banner" },
  { field: "summary", sel: ".body.summary" },
  { field: "areasOfExpertise", sel: ".aoe" },
  { field: "lede", sel: ".body.lede" },
  { field: "bullets", sel: "ul" },
];

const PAGE_W = 816;
const PAGE_H = 1056;

function text(node: Element | null | undefined): string {
  return String(node?.textContent ?? "").trim();
}

/**
 * The inverse of the render, reading the slots back out.
 *
 * An area of expertise renders as a bullet glyph, two non-breaking spaces and
 * the words, and `extract` captures only the words. Reading the whole node
 * puts the glyph into the content and the next render draws a second bullet in
 * front of the first, compounding on every save; the board caught that in a
 * browser, so the glyph is stripped here exactly as it was there.
 */
function collect(doc: Document, base: Record<string, unknown>) {
  const areaText = (node: Element) => {
    const clone = node.cloneNode(true) as Element;
    clone.querySelectorAll(".b").forEach((b) => b.remove());
    return String(clone.textContent ?? "").replace(/^[\s ]+/, "").trim();
  };
  const baseTech = (base.technical ?? []) as { label?: string }[];
  const baseRoles = (base.roles ?? []) as { lede?: string; bullets?: string[] }[];
  const banner = doc.querySelector(".banner");
  const summary = doc.querySelector(".body.summary");
  const ledes = doc.querySelectorAll(".body.lede");
  const lists = doc.querySelectorAll("ul");
  const roles = [];
  for (let i = 0; i < Math.max(ledes.length, lists.length); i++) {
    roles.push({
      lede: ledes[i] ? text(ledes[i]) : baseRoles[i]?.lede ?? "",
      bullets: lists[i]
        ? Array.from(lists[i].querySelectorAll("li"), (li) => text(li))
        : baseRoles[i]?.bullets ?? [],
    });
  }
  return {
    banner: banner ? text(banner) : base.banner,
    summary: summary ? text(summary) : base.summary,
    areasOfExpertise: Array.from(doc.querySelectorAll(".aoe"), areaText),
    technical: Array.from(doc.querySelectorAll(".tech"), (row, i) => {
      const bold = row.querySelector("b");
      return {
        label: bold ? String(bold.textContent ?? "").replace(/:\s*$/, "").trim()
          : baseTech[i]?.label,
        items: text(row).replace(/^[^:]*:\s*/, ""),
      };
    }),
    roles,
  };
}

export function WordingEditor({ configured, owner, why }: {
  /** Whether the two owner variables are set on this deployment. */
  configured: boolean;
  /** Whether this request carried a live owner session. */
  owner: boolean;
  /** How to turn the editor on, when it is off. */
  why: string | null;
}) {
  const { enabled, send } = useEdits();
  const [open, setOpen] = useState(false);
  const [loaded, setLoaded] = useState<Published | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [local, setLocal] = useState<string | null>(null);
  const [fontNote, setFontNote] = useState(false);
  const [generation, setGeneration] = useState(0);
  const frame = useRef<HTMLIFrameElement | null>(null);
  const stage = useRef<HTMLDivElement | null>(null);

  const load = useCallback(async () => {
    setProblem(null);
    try {
      const res = await fetch("/api/profile/wording", { cache: "no-store" });
      const said = await res.json().catch(() => ({}));
      if (!res.ok) {
        setProblem(String(said.error ?? `the server answered ${res.status}`));
        return;
      }
      setLoaded(said as Published);
      setGeneration((g) => g + 1);
    } catch {
      setProblem("The wording did not load. Nothing was changed.");
    }
  }, []);

  useEffect(() => {
    if (open && owner && configured && !loaded && !problem) load();
  }, [open, owner, configured, loaded, problem, load]);

  // The board's own deep link from the sign-in page.
  useEffect(() => {
    if (window.location.hash === "#wording") setOpen(true);
  }, []);

  const fit = useCallback(() => {
    const f = frame.current;
    const s = stage.current;
    if (!f || !s) return;
    const scale = Math.min(1, (s.clientWidth || PAGE_W) / PAGE_W);
    const pages = f.contentDocument?.querySelectorAll(".page").length || 1;
    f.style.width = `${PAGE_W}px`;
    f.style.height = `${PAGE_H * pages}px`;
    f.style.transform = `scale(${scale})`;
    f.style.transformOrigin = "top left";
    s.style.height = `${Math.ceil(PAGE_H * pages * scale)}px`;
  }, []);

  useEffect(() => {
    window.addEventListener("resize", fit);
    return () => window.removeEventListener("resize", fit);
  }, [fit]);

  const onFrameLoad = () => {
    const doc = frame.current?.contentDocument;
    if (!doc) return;
    for (const slot of SLOTS) {
      doc.querySelectorAll(slot.sel).forEach((node) => {
        if (slot.field === "bullets") {
          node.querySelectorAll("li").forEach((li) => li.setAttribute("contenteditable", "true"));
        } else {
          node.setAttribute("contenteditable", "true");
          // The bullet glyph is the template's, not the user's text.
          node.querySelectorAll(".b").forEach((b) => b.setAttribute("contenteditable", "false"));
        }
      });
    }
    fit();
    // A surface that cannot be exact says so. The PDF is rendered by Chrome
    // on the device, which has Calibri Light; a phone almost certainly does
    // not, and the preview there approximates the spacing.
    let ok = false;
    try { ok = Boolean(doc.fonts?.check?.('12pt "Calibri Light"')); } catch { ok = false; }
    setFontNote(!ok);
  };

  const save = async () => {
    const doc = frame.current?.contentDocument;
    if (!doc || !loaded) return;
    setMessage(null);
    setLocal(await send({ kind: "resume", slot: WORDING_SLOT, action: "content",
                          content: collect(doc, loaded.content) },
                        "/api/profile/wording"));
  };

  return (
    <details className="wording" id="wording" open={open}
             onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
      <summary>
        <span>Edit your resume</span>
        <span className="wording-hint">
          {owner ? "The document itself. Click any line and type."
                 : "Owner only. Sign in to edit."}
        </span>
      </summary>
      <div className="wording-body">
        <div className="split-note">
          <strong>The words are yours, the layout is the template&apos;s.</strong>{" "}
          You are editing the real document, so what you see is what the PDF
          will be. Lines can say anything; there is always the same number of
          them, because the layout is what the page-fit check measures against.
        </div>
        {/* Not inside `resume-stage` when there is no document: that stage
            is the paper the resume is drawn on, white in both themes. */}
        {!configured ? (
          <p className="bank-note">{why}</p>
        ) : !owner ? (
          <>
            <p className="bank-note">
              Your resume wording is your employment history in your own words,
              so it is never shown on the public board. Only the owner can
              open it here.
            </p>
            <div className="wording-act">
              <a className="btn" href="/signin">Sign in as the owner</a>
            </div>
          </>
        ) : (
          <>
            {fontNote ? (
              <p className="profile-message">
                This device does not have Calibri Light, so the preview
                approximates the spacing. The PDF is rendered on your Mac,
                which does.
              </p>
            ) : null}
            <div className="resume-stage" ref={stage}>
              {problem ? <p className="bank-note">{problem}</p>
                : !loaded ? <p className="bank-note">Loading your resume&hellip;</p>
                : !loaded.render ? (
                  <p className="bank-note">
                    Your device could not render the resume, so there is
                    nothing to edit here. The wording is still used for
                    tailored resumes.
                  </p>
                ) : (
                  <iframe key={generation} ref={frame} className="resume-frame"
                          sandbox="allow-same-origin" title="Your resume, editable"
                          srcDoc={loaded.render} onLoad={onFrameLoad} />
                )}
            </div>
            <div className="wording-act">
              <button className="btn" type="button"
                      disabled={!enabled || !loaded?.render}
                      title={enabled ? undefined : "Writes are off for this account."}
                      onClick={save}>Save resume</button>
              <button className="btn ghost" type="button" disabled={!loaded}
                      onClick={() => {
                        // Back to what the device last published, which is
                        // the saved version as far as anything here knows.
                        setLocal(null);
                        setGeneration((g) => g + 1);
                        setMessage("Reverted to the saved version.");
                      }}>Revert</button>
              <button className="btn ghost" type="button"
                      onClick={() => { setLoaded(null); setProblem(null); load(); }}>
                Reload from device
              </button>
              <OwnerSignOut />
              {message ? (
                <span className="profile-message" role="status">{message}</span>
              ) : null}
              <EditStateLine slot={WORDING_SLOT} local={local} />
            </div>
            {loaded?.publishedAt ? (
              <p className="bank-note">
                Published by your device {new Date(loaded.publishedAt).toLocaleString()}.
                A saved edit appears here once your device has applied it and
                published again.
              </p>
            ) : null}
          </>
        )}
      </div>
    </details>
  );
}

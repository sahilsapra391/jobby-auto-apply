"use client";

import { useEffect } from "react";

/**
 * Reveal a company logo once it has actually loaded, and take the monogram
 * with it.
 *
 * **46 working logos were painted at `opacity: 0` on every lane.** The
 * stylesheet has said, since the board was written:
 *
 *     .company-mark img       { opacity: 0; transition: opacity .3s ease; }
 *     .company-mark img.loaded { opacity: 1; }
 *
 * and nothing in v2 ever added `.loaded`. The data was there, the markup was
 * there, `/api/company-logo` answered 200 with real PNGs for 46 of 48 distinct
 * URLs on the first page, and every one of them was invisible. This is the
 * third class v2 has inherited whose styling ships and whose activating state
 * nobody sets -- `.posted.fresh` and `.yrs.over` were the first two -- which is
 * why `tests/test_v2_styles_are_reachable.py` now fails on the shape rather
 * than waiting for someone to notice the fourth.
 *
 * **`naturalWidth > 1` is the whole guard and it is not a formality.** A logo
 * endpoint answers a domain it does not know with a 1x1 pixel as often as with
 * a 404, and a 1x1 stretched over a 2.75rem tile is a smear of one colour that
 * reads as a broken image rather than as no image. Both cases end the same
 * way: the `<img>` is removed and the monogram underneath is what shows. That
 * check is `dashboard.py`'s and it is taken whole rather than reimplemented.
 *
 * **The monogram is removed on success and kept on failure, in that order.**
 * Roughly 40% of real logos carry an alpha channel -- 8 of 20 sampled from the
 * live route -- so a transparent mark sits over the letter and Datadog's logo
 * renders on top of a "D". Hiding the letter up front is the obvious fix and it
 * is wrong: a 404 or a 1x1 would then leave an empty plate rather than falling
 * back. So the class that hides it goes on in the same branch that adds
 * `.loaded`, after the image has proved itself. The old board has this bug too
 * and the rule lives in `premium_ui.py`, so fixing it here fixes both.
 *
 * **A MutationObserver rather than one pass at load.** `LaneList` windows rows
 * in and out as the reader scrolls, so a one-shot `querySelectorAll` decorates
 * the first fifty and nothing after. The board has the same problem and solves
 * it by exposing `window.jobbyPrepareCompanyLogo` for its own lane script to
 * call on new nodes; v2's rows arrive from React rather than from a script we
 * control, so the equivalent is to watch for them.
 */
export function CompanyLogos() {
  useEffect(() => {
    /** `dashboard.py:1601`, kept whole. */
    const prepare = (img: HTMLImageElement) => {
      if (img.dataset.jbLogoWired) return;
      img.dataset.jbLogoWired = "1";
      const loaded = () => {
        if (img.naturalWidth > 1 && img.naturalHeight > 1) {
          img.classList.add("loaded");
          // Only now, and only here. See the note above about the order.
          img.closest(".company-mark")?.classList.add("has-logo");
        } else {
          img.remove();
        }
      };
      img.addEventListener("load", loaded);
      img.addEventListener("error", () => img.remove());
      // Already in cache: `load` has fired before this effect ran and will not
      // fire again. Without this the first paint after a client navigation
      // shows monograms over images that are sitting decoded and ready.
      if (img.complete) loaded();
    };

    const sweep = (root: ParentNode) =>
      root.querySelectorAll<HTMLImageElement>("[data-company-logo]")
        .forEach(prepare);

    sweep(document);

    const observer = new MutationObserver((records) => {
      for (const record of records) {
        for (const node of record.addedNodes) {
          if (!(node instanceof Element)) continue;
          if (node.matches("[data-company-logo]")) {
            prepare(node as HTMLImageElement);
          } else {
            sweep(node);
          }
        }
      }
    });
    observer.observe(document.body, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, []);
  return null;
}

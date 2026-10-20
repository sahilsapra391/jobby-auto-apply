"use client";

import { useEffect } from "react";

/**
 * The dock opens the dialog, and the close button, Escape and a click on the
 * backdrop close it.
 *
 * A `<dialog>` closes on Escape by itself, but nothing opens it without a call
 * to showModal, and the original board's opener lived in its page script,
 * which v2 does not carry. This draws nothing.
 *
 * **One listener on the document, not one on the dock.** The dock is raw
 * markup with the count interpolated into it (`Dropped.tsx`), so when a soft
 * navigation changes the count React replaces the whole button. A listener
 * attached to the first button at mount then sits on a detached node, and the
 * dock looks fine and opens nothing. Delegation asks what was clicked at the
 * moment of the click, so it does not care how many times the node was
 * replaced; the dialog is looked up the same way for the same reason.
 *
 * **A click whose target is the dialog itself is a click on the backdrop**,
 * because everything drawn inside the dialog is a descendant and would be the
 * target instead. The board closes on that (`event.target === droppedDialog`)
 * and so does this.
 */
export function DroppedOpen() {
  useEffect(() => {
    const onClick = (event: MouseEvent) => {
      const target = event.target as Element | null;
      if (!target || !(target instanceof Element)) return;
      const dialog = document.querySelector<HTMLDialogElement>(".dropped-dialog");
      if (!dialog) return;
      if (target.closest(".dropped-dock")) {
        if (!dialog.open) dialog.showModal();
        return;
      }
      if (target === dialog || (dialog.contains(target)
                                 && target.closest(".dialog-close"))) {
        dialog.close();
      }
    };
    document.addEventListener("click", onClick);
    return () => document.removeEventListener("click", onClick);
  }, []);
  return null;
}

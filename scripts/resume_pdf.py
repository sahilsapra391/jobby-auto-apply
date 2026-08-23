#!/usr/bin/env python3
"""Portable PDF toolchain for per-application resumes and cover letters.

The Mac uses its installed Chrome. The OCI document worker uses Chromium on
Ubuntu. Keeping both behind the same commands means a document is rendered and
checked the same way before it reaches the application browser.

  check    what is installed, and what a given resume needs
  render   HTML to PDF through the Chrome already on this machine
  verify   compare a rebuilt PDF against the source, geometrically

`check` and `render` need no third-party packages beyond Chrome. Reading a PDF
(`check --resume`, `verify`) needs pymupdf, which `check` will tell you how to
install.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/snap/bin/chromium",
    "/snap/bin/chromium-browser",
]

FONT_DIRS = [
    Path("/System/Library/Fonts"),
    Path("/Library/Fonts"),
    Path.home() / "Library/Fonts",
    Path("/usr/share/fonts"),
    Path("/usr/local/share/fonts"),
    Path.home() / ".local" / "share" / "fonts",
]

# Office ships the real Microsoft fonts inside its app bundles, where the system
# font machinery cannot see them. Copying them out beats substituting, and it
# is the only way to get weights the substitutes do not carry (Calibri Light).
OFFICE_FONT_DIRS = [
    Path("/Applications/Microsoft Word.app/Contents/Resources/DFonts"),
    Path("/Applications/Microsoft Excel.app/Contents/Resources/DFonts"),
    Path("/Applications/Microsoft PowerPoint.app/Contents/Resources/DFonts"),
]

# Microsoft fonts are not on macOS by default. These substitutes have identical
# metrics, so a resume built with them keeps the source's line breaks. Note that
# Carlito has no Light weight, so a Calibri Light resume renders too heavy with
# it; prefer the real font from an Office bundle when one is present.
METRIC_SUBSTITUTES = {
    "calibri": ("Carlito", "font-carlito"),
    "cambria": ("Caladea", "font-caladea"),
    "arial": ("Liberation Sans", "font-liberation"),
    "times new roman": ("Liberation Serif", "font-liberation"),
    "courier new": ("Liberation Mono", "font-liberation"),
}


# Email, phone, or a profile link: the things a resume contact line is made of.
# Deliberately not a bare ".com": body text mentions domains, and matching
# those turned ordinary prose into phantom contact lines.
CONTACT_MARKER = re.compile(r"@|linkedin|github|\+\d{2}\s?\d|\(\d{3}\)\s?\d{3}", re.I)


class ToolError(Exception):
    pass


def find_chrome():
    for path in CHROME_CANDIDATES:
        if Path(path).exists():
            return path
    for name in ("chromium", "chromium-browser", "google-chrome"):
        found = shutil.which(name)
        if found:
            return found
    raise ToolError(
        "no Chrome or Chromium found. Install Google Chrome on macOS or "
        "chromium-browser on Ubuntu."
    )


_FONT_CACHE = None


def installed_font_families():
    """Filenames of installed fonts, lowercased. A heuristic, not a font DB."""
    global _FONT_CACHE
    if _FONT_CACHE is not None:
        return _FONT_CACHE
    names = set()
    for d in FONT_DIRS:
        if not d.exists():
            continue
        for f in d.rglob("*"):
            if f.suffix.lower() in (".ttf", ".otf", ".ttc", ".dfont"):
                names.add(f.stem.lower().replace(" ", "").replace("-", ""))
    _FONT_CACHE = names
    return names


def font_available(family):
    key = family.lower().replace(" ", "").replace("-", "")
    return any(key in n or n in key for n in installed_font_families())


def office_source_for(family):
    """An installed Office bundle carrying this font, if there is one."""
    key = family.lower().replace(" ", "").replace("-", "")[:7]
    for d in OFFICE_FONT_DIRS:
        if not d.exists():
            continue
        for f in d.glob("*"):
            if f.suffix.lower() in (".ttf", ".otf", ".ttc") and key in f.stem.lower():
                return d
    return None


def require_pymupdf():
    try:
        import pymupdf  # noqa: F401
        return pymupdf
    except ImportError:
        try:
            import fitz as pymupdf  # older name, still shipped
            return pymupdf
        except ImportError:
            raise ToolError(
                "pymupdf is not installed. Install it with:\n"
                "    python3 -m pip install pymupdf pdfplumber"
            )


# --- commands ---------------------------------------------------------------


def cmd_check(args):
    report = {"ok": True, "chrome": None, "pymupdf": None, "fonts": {}, "fixes": []}

    try:
        report["chrome"] = find_chrome()
    except ToolError as exc:
        report["ok"] = False
        report["chrome"] = None
        report["fixes"].append(str(exc))

    try:
        mod = require_pymupdf()
        report["pymupdf"] = getattr(mod, "__version__", "installed")
    except ToolError as exc:
        report["ok"] = False
        report["fixes"].append(str(exc))

    if args.resume:
        src = Path(args.resume).expanduser()
        if not src.exists():
            raise ToolError(f"no resume at {src}")
        if report["pymupdf"]:
            for family in sorted(source_fonts(src)):
                # Type3 entries are inline glyph programs the PDF carries with
                # it (bullets, rules). Nothing to install.
                if family.startswith("Type3"):
                    continue
                base = family.split("+")[-1].split("-")[0].split(",")[0]
                have = font_available(base)
                report["fonts"][family] = "installed" if have else "MISSING"
                if not have:
                    report["ok"] = False
                    office = office_source_for(base)
                    sub = METRIC_SUBSTITUTES.get(base.lower())
                    if office:
                        fix = (f"{base} is not installed system-wide, but the real "
                               f"font ships inside an Office bundle. Copy it out:\n"
                               f"    cp '{office}'/{base.lower()}*.ttf ~/Library/Fonts/\n"
                               f"    (preferred over a substitute, which may lack "
                               f"weights such as Light)")
                    elif sub:
                        fix = (f"{base} is not installed. Its metric-compatible "
                               f"substitute is {sub[0]}. Install the matching font "
                               "package for this operating system.")
                    else:
                        fix = (f"{base} is not installed and has no known metric "
                               f"substitute. Chrome will fall back and line breaks "
                               f"may shift; check the rendered result closely.")
                    if fix not in report["fixes"]:
                        report["fixes"].append(fix)
    return report


def source_fonts(pdf_path):
    pymupdf = require_pymupdf()
    doc = pymupdf.open(str(pdf_path))
    fonts = set()
    for page in doc:
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    fonts.add(span["font"])
    doc.close()
    return fonts


def chrome_command(chrome, out, source_uri, *, profile_dir=None):
    """Build the print command, with a bounded Linux profile for tiny workers."""
    command = [chrome, "--headless", "--disable-gpu"]
    if os.environ.get("JOBBY_CHROMIUM_LOW_MEMORY") == "1":
        if profile_dir is None:
            raise ToolError("low-memory Chromium requires a private profile directory")
        command.extend([
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-background-networking",
            "--disable-extensions",
            "--renderer-process-limit=1",
            f"--user-data-dir={profile_dir}",
        ])
    command.extend([
        "--no-pdf-header-footer",
        f"--print-to-pdf={out}",
        source_uri,
    ])
    return command


def cmd_render(args):
    chrome = find_chrome()
    html = Path(args.html).expanduser().resolve()
    if not html.exists():
        raise ToolError(f"no HTML at {html}")
    out = Path(args.out).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    low_memory = os.environ.get("JOBBY_CHROMIUM_LOW_MEMORY") == "1"
    if low_memory:
        # The locked-down OCI unit cannot write to ~/.config. Give each render
        # a private profile beside its output and remove it after Chrome exits.
        with tempfile.TemporaryDirectory(
            prefix=".chrome-profile.", dir=out.parent,
        ) as profile:
            proc = subprocess.run(
                chrome_command(
                    chrome, out, html.as_uri(), profile_dir=Path(profile),
                ),
                capture_output=True, text=True, timeout=60,
            )
    else:
        # A fresh --user-data-dir can hang on macOS first-run setup. Headless
        # uses its own profile and coexists with the user's application Chrome.
        proc = subprocess.run(
            chrome_command(chrome, out, html.as_uri()),
            capture_output=True, text=True, timeout=60,
        )
    if not out.exists():
        raise ToolError(f"Chrome produced no PDF: {(proc.stderr or '').strip()[:400]}")
    os.chmod(out, 0o600)
    return {"written": str(out), "bytes": out.stat().st_size}


def geometry(pdf_path):
    """The measurements build_pdf.md says a rebuild has to reproduce."""
    pymupdf = require_pymupdf()
    doc = pymupdf.open(str(pdf_path))
    pages, sizes, colors, fonts = [], set(), {}, set()
    for page in doc:
        spans = [s for b in page.get_text("dict")["blocks"]
                 for l in b.get("lines", []) for s in l["spans"]]
        spans = [s for s in spans if s["text"].strip()]
        if not spans:
            pages.append(None)
            continue
        pages.append({
            "width": round(page.rect.width, 1),
            "height": round(page.rect.height, 1),
            "left": round(min(s["bbox"][0] for s in spans), 1),
            "right": round(page.rect.width - max(s["bbox"][2] for s in spans), 1),
            "top": round(min(s["bbox"][1] for s in spans), 1),
            "bottom": round(page.rect.height - max(s["bbox"][3] for s in spans), 1),
        })
        # How many lines the contact block occupies. A contact line that wraps
        # reflows everything below it while leaving page count, margins,
        # colours and font sizes untouched, so no other check notices. Counted
        # by contact markers rather than a y-band, because a y-band also picks
        # up body text and gives false positives.
        if len(pages) == 1:
            band = page.rect.height * 0.13   # name, contact line, profile title
            rows = {}
            for sp in spans:
                if sp["bbox"][1] < band:
                    rows.setdefault(round(sp["bbox"][1]), []).append(sp["text"])
            contact = [y for y, texts in rows.items()
                       if CONTACT_MARKER.search(" ".join(texts))]
            pages[0]["contactLines"] = len(contact)
        for s in spans:
            sizes.add(round(s["size"], 1))
            fonts.add(s["font"].split("+")[-1])
            colors[f"#{s['color']:06x}"] = colors.get(f"#{s['color']:06x}", 0) + 1
    doc.close()
    return {"pageCount": len(pages), "pages": pages, "sizes": sorted(sizes),
            "fonts": sorted(fonts), "colors": colors}


# Body line height in the generated resume, in points. The bottom of a page can
# only fall on a line boundary, so this is the smallest difference a rebuild can
# actually express there.
LINE = 13.0


def cmd_verify(args):
    built = geometry(Path(args.pdf).expanduser())
    if not args.against:
        return built

    src = geometry(Path(args.against).expanduser())
    diffs = []

    if built["pageCount"] != src["pageCount"]:
        diffs.append(f"page count: source {src['pageCount']}, built {built['pageCount']}")

    for i, (a, b) in enumerate(zip(src["pages"], built["pages"])):
        if not a or not b:
            continue
        if i == 0 and a.get("contactLines") and b.get("contactLines") and \
                a["contactLines"] != b["contactLines"]:
            diffs.append(
                f"contact block: source occupies {a['contactLines']} line(s), built "
                f"occupies {b['contactLines']} (a wrapped contact line reflows "
                f"everything below it)")
        # Text printing past the bottom edge is the failure that actually
        # matters here, and it is absolute: it does not depend on the source.
        #
        # A review called this branch "effectively unreachable under Chrome's
        # print-to-pdf". It is not. Reproduce it: render templates/resume.html
        # with eight technical rows from templates/resume-content.json and
        # verify against the base resume. Page 1 comes back at -4.2pt and this
        # fires. 48 of the 165 bottom failures in the recorded runs were
        # negative, between -0.5 and -6.5pt. Do not delete it as dead code.
        if b["bottom"] < 0:
            diffs.append(
                f"page {i+1}: text prints {-b['bottom']:.1f}pt past the bottom edge "
                f"and will be clipped")

        for edge in ("width", "height", "left", "right", "top", "bottom"):
            # 2pt is under a third of a millimetre, below what a reader sees.
            #
            # Except "bottom", which is not a margin. width/height/left/right/top
            # are set by the template and hold still, but bottom is measured to
            # the LAST glyph on the page, so it moves with how much text there
            # is, and a page break can only land between lines. Demanding it
            # match within 2pt asks a quantity that advances in ~12pt steps to
            # hit a 4pt window, which a rebuild cannot do on purpose: across 84
            # real runs it produced 165 bottom failures and agents re-rendered
            # up to 13 times trimming blindly at it. One line of slack is the
            # honest tolerance.
            #
            # It still catches what matters. 48 of those 165 had the built page
            # reaching past the bottom edge, and every one exceeds a line's
            # slack as well as tripping the absolute check above. Of the 85 that
            # now pass, the largest gap is 12.8pt, under one line. A page
            # genuinely half empty is several lines and still fails.
            tolerance = LINE if edge == "bottom" else 2
            if abs(a[edge] - b[edge]) > tolerance:
                diffs.append(f"page {i+1} {edge}: source {a[edge]}pt, built {b[edge]}pt")

    # The accent colour is the single most visible attribute of a resume
    # template, so any colour in the source must survive into the rebuild.
    for hexcode in src["colors"]:
        if hexcode not in built["colors"]:
            diffs.append(f"colour {hexcode} in source is absent from the rebuild")

    src_sizes, built_sizes = set(src["sizes"]), set(built["sizes"])
    for size in sorted(src_sizes):
        if not any(abs(size - b) <= 0.6 for b in built_sizes):
            diffs.append(f"font size {size}pt in source has no match in the rebuild")

    return {"match": not diffs, "differences": diffs,
            "source": src, "built": built}


COMMANDS = {"check": cmd_check, "render": cmd_render, "verify": cmd_verify}


def main():
    ap = argparse.ArgumentParser(description="Local PDF toolchain for tailored resumes.")
    sub = ap.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("check", help="report the local toolchain and a resume's fonts")
    sp.add_argument("--resume", help="source resume PDF, to list the fonts it needs")

    sp = sub.add_parser("render", help="HTML to PDF via local Chrome")
    sp.add_argument("--html", required=True)
    sp.add_argument("--out", required=True)

    sp = sub.add_parser("verify", help="measure a PDF, or diff it against the source")
    sp.add_argument("--pdf", required=True)
    sp.add_argument("--against", help="source PDF to compare geometry against")

    args = ap.parse_args()
    try:
        result = COMMANDS[args.command](args)
    except ToolError as exc:
        print(f"resume_pdf: {exc}", file=sys.stderr)
        return 2
    except subprocess.TimeoutExpired:
        print("resume_pdf: Chrome timed out rendering the PDF", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok", True) and result.get("match", True) else 1


if __name__ == "__main__":
    sys.exit(main())

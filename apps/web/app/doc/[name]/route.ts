import { NextRequest, NextResponse } from "next/server";
// @ts-expect-error -- plain JS with no types, so node can test it directly.
import { contentTypeFor, readByName } from "../../_ui/documents.mjs";

/**
 * `/doc/<filename>`, the address every v1 board and every saved copy of one
 * printed for a resume, a cover letter or an answer record.
 *
 * v1 is retired and www.jobby.fyi is v2, so without this every such link is a
 * 404 from Next's router for a document sitting in R2. It is the second door
 * to the one serving path in `_ui/documents.mjs`, which `/api/artifact` also
 * falls back to; it adds nothing of its own beyond the headers v1 set.
 *
 * A document is never cached and never indexed, which is what v1's
 * `BASE_HEADERS` said, and the reply on a fault is text naming it rather than a
 * 404, because the document exists and the reader should not go looking for it.
 */
export const dynamic = "force-dynamic";

const HEADERS = {
  "cache-control": "no-store, max-age=0",
  "x-robots-tag": "noindex, nofollow, noarchive",
  "referrer-policy": "no-referrer",
  "x-content-type-options": "nosniff",
};

function text(body: string, status: number) {
  return new NextResponse(body, {
    status, headers: { ...HEADERS, "content-type": "text/plain; charset=utf-8" },
  });
}

export async function GET(request: NextRequest,
                          { params }: { params: Promise<{ name: string }> }) {
  const { name: raw } = await params;
  let name: string;
  try {
    name = decodeURIComponent(raw);
  } catch {
    return text("No such document.\n", 404);
  }
  const read = await readByName(process.env, name, request.headers.get("range"));
  if (read.ok) {
    const headers = new Headers({
      ...HEADERS,
      "content-type": read.contentType ?? contentTypeFor(name, null),
      "content-disposition": `inline; filename="${name.replace(/"/g, "")}"`,
    });
    for (const [header, value] of Object.entries(read.headers as Record<string, string>)) {
      headers.set(header, value);
    }
    return new NextResponse(read.body, { status: read.status, headers });
  }
  if (read.status === 404) return text("No such document.\n", 404);
  return text(
    "This document could not be read, so there is nothing to open.\n"
    + `${read.error}\nThe file is still on the machine that made it.\n`,
    read.status);
}

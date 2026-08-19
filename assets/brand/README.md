# Jobby — logo & favicon pack

Everything here is generated from the mark you supplied. The bitmap was traced to
vector first, so every asset is a true curve rather than an upscaled 77×134 PNG.

Open `preview.html` in a browser to see the whole set, including a live tab-strip
test at real pixel sizes.

---

## Colours

| Token | Hex | Where it came from |
|---|---|---|
| Ink | `#2B2D2C` | sampled from your original file |
| Paper | `#F8F8F8` | sampled from your original file |
| White | `#FFFFFF` | knockout on ink |

Both brand colours are lifted from the source artwork, so nothing was invented.

---

## 01-favicon — the part you actually asked for

Drop these five files in your web root and paste `head.html` into `<head>`:

```
favicon.ico              16 / 32 / 48 in one file, PNG-compressed
favicon.svg              scalable, auto-inverts in dark browser chrome
apple-touch-icon.png     180×180, iOS home screen
icon-192x192.png         Android / PWA
icon-512x512.png         Android / PWA, splash
icon-maskable-512x512.png  Android adaptive-icon safe zone
site.webmanifest
```

Plus loose PNGs at 16, 32, 48, 64, 96, 128 if your framework wants them explicitly.

**Optical sizing.** The mark is not scaled uniformly across sizes. Your mark has
two fine interior counters (the crossbar notch and the hook's eye) that close up
below about 24px, so the mark grows as the canvas shrinks: 58% of the circle at
64px and above, 60% at 48, 64% at 32, 70% at 16. This is the same trick Apple and
Google use on their own system icons — it keeps the silhouette legible instead of
letting it turn to grey mush in the tab strip.

**Dark browser chrome.** An ink circle on a dark tab bar has almost no edge
contrast. `favicon.svg` handles this with an embedded `prefers-color-scheme`
query that flips to a paper circle with an ink mark. Every current browser
prefers the SVG when both are declared, and the `.ico` stays as the fallback for
older ones. If you later pick a mid-tone brand accent, a single-colour circle in
that accent would read on both light and dark chrome and you could drop the flip.

---

## 02-app-icon

The circular logo on its own, SVG plus PNG at 256 / 512 / 1024.

- `jobby-icon-dark` — white mark on an ink circle. **This is the primary.**
- `jobby-icon-light` — ink mark on a paper circle with an 18%-opacity hairline
  ring, so the circle still has an edge when it sits on white.

Mark height is 58% of the circle diameter, centred optically rather than
mathematically: the mark's mass sits left of its bounding box, so it is offset
1.6% to the right of true centre. Centring on the bounding box alone makes it
look like it has drifted left.

---

## 03-mark

The bare mark with no circle, in ink and in white, transparent background.
Use it when the container already provides the shape — a dark app bar, a stamped
watermark, a loading state.

---

## 04-lockup

Mark plus wordmark, horizontal and stacked, each in ink and white.

Wordmark is **Poppins Bold**, tracked −1.2%, converted to outlines — the SVGs
carry no font dependency and will render identically anywhere. Poppins is
SIL Open Font License, so it is free to use commercially.

Lockup construction, if you ever need to rebuild it:

- cap height = 42% of the circle diameter
- gap between circle and word = 26% of the diameter
- word sits 2.6% of the diameter below true vertical centre, which compensates
  for the descenders in J and y
- stacked version: 26% vertical gap, word centred on the circle

**Clear space:** keep a margin equal to the circle's radius on all four sides.
Nothing else in that zone.

---

## 05-social

`og-image-1200x630.png` — Open Graph / Twitter card. Add to `<head>`:

```html
<meta property="og:image" content="https://yourdomain.com/og-image-1200x630.png">
<meta name="twitter:card" content="summary_large_image">
```

---

## Don't

- Don't re-crop the circle to sit tighter around the mark; the breathing room is
  what stops it looking like a button.
- Don't rebuild the small favicons by resizing the 512 PNG — you lose the
  optical sizing and the counters fill in.
- Don't stretch the lockups; scale proportionally.
- Don't set the wordmark in a different weight of Poppins next to the outlined
  version. If you need live text, Poppins Bold at −1.2% tracking matches.

---
name: endoxia-film
description: >-
  Build a short product film in Remotion in the Endoxia house style — paper, ink and
  one line of crimson — from real app screenshots, delivered in every social format
  from one component. Use when the user wants a product video, marketing animation,
  launch clip, or "an animation like the Claude Code / Harvey videos", or asks to add
  a scene or change the timing of an existing one.
---

# Endoxia film — method

A film is **one component rendered at N canvases**. Nothing is authored per format,
nothing is cropped, and every number on screen traces back to something that was
measured rather than chosen.

The house look: **paper, ink, and a single line of crimson.** Lexend for everything
readable, JetBrains Mono for figures and code, 24 fps, drawn marks that boil, type
that never does.

---

## 1. Intake — two things to ask for, one thing to deliver

**Ask for images first. Draft the script yourself. Never animate before both are
settled.**

### Ask: the two kinds of visual reference

These are different questions and both get asked, explicitly, before any scene is
written:

1. **Surfaces to RECREATE** — screenshots of the actual product screens the film
   shows. Every product block is *reconstructed at product pixels* from these: a
   simplified, recognisable rebuild in real components, never an invention and never
   a pasted bitmap. Ask which screens, in which state (empty composer, mid-answer,
   document open), and at what window size.
2. **References for INSPIRATION** — stills or clips of films whose motion or feel is
   the target, plus any brand or layout the look should sit next to. Ask what
   specifically is liked in each: the pacing, the type, the camera, the restraint.
   A reference is only useful once it is named down to the mechanism.

If either is missing, ask for it and stop. A film built from a described screen is
a film that gets rebuilt.

### Ask: the case and the claim

3. **The topic or case.** A real one, with real figures and real citations. A film
   about a calculation needs the actual calculation; a film about sources needs the
   actual sources.
4. **The one claim.** A film argues one thing. Everything else is setup for it.
5. **The formats.** Default set below unless told otherwise.

### Deliver: the script, written for them

**Do not ask the user to supply the script.** Draft it and put it in front of them
as plain text. They edit words; they should never have to invent the argument.

The shape that works, from the films that have shipped:

```
line 1   setup      the situation everyone recognises
line 2   turn       the thing that breaks it
line 3   stakes     why that matters in this domain
line 4   claim      what the product does about it, carrying the wordmark
```

Rules for the draft:

- **Four lines.** Five is one too many; the cut usually merges the product line and
  the payoff into one claim.
- **≤50 characters per line**, and under 46 is better: at 46 a line already wraps to
  two on 16:9 and three on 4:5.
- **The claim line carries the brand name**, set at 700 inside the sentence.
- **The lines do not address the reader.** No "u", no "je". They are statements.
- **House voice**: short, professional, confident understatement. No em dashes, no
  reassurance, no second sentence restating the first, no hype.
- **Every implied capability must be real.** Check the claim against what the product
  genuinely does *and* against its published limits. A film that implies a feature
  the "what we don't do" page disclaims is worse than no film.

Offer an alternative for line 1 and line 4 — the two that carry the most and are the
most personal. Then get approval on the text before a single frame is timed. Copy is
cheap to change as text and expensive to change as timing.

**No pen.** No hand drawing itself on screen, no cursor tracing strokes, no
"sketching" metaphor. Marks arrive; they are not performed.

---

## 2. Formats

```
desktop   1600 x 900    16:9
square    1080 x 1080   1:1
feed      1080 x 1350   4:5   <- densest mobile reach
vertical  1080 x 1920   9:16
```

One `Composition` registration per canvas, all pointing at the same component.
Duration is **derived** (`SCRIPT_LEN + SCENE_LEN + …`), never typed in twice, so
shortening a beat can never leave dead frames at the end.

---

## 3. The laws

### 3.1 Every dimension derives from the canvas — but bucket on the right axis

A `useLayout()` hook turns `useVideoConfig()` into margins, column width, UI scale
and type sizes. Scenes read those; scenes never read raw pixel constants.

**The trap, and it is the expensive one:** `portrait = height > width` conflates two
unrelated questions —

- *is this canvas NARROW?* → margins, headline size
- *how much COLUMN can a dense scene spend?* → UI scale, top offset

4:5 is narrow but not tall. Bucketing it as "portrait" gave it the 9:16 treatment —
larger type starting further down — and **an entire paragraph fell off the bottom of
the frame**, silently, while the *shorter* square canvas rendered it fine. Keep the
two axes as two flags:

```ts
const portrait = height > width;      // narrow: margins, headline
const tall = height / width >= 1.5;   // vertical budget: scale, offset
```

The threshold sits between the two ratios it separates (4:5 = 1.25, 9:16 = 1.78).

### 3.2 Frame the CONTENT, not the container

Aim the camera at the thing the viewer must read, never at the box around it.

Framing "the composer" left the typed sentence filling **47%** of a desktop frame,
the rest empty input field — the thing to read was the smallest thing on screen.
Framing *the sentence* fixed it and was **format-independent as a bonus**: content
is the same product width on every canvas, so one share replaced a table of
per-format shares.

```ts
const scale = (width * TEXT_SHARE) / TEXT_W;   // 0.82 reads well
```

### 3.3 Measure. Never estimate.

Anything that could be measured and was guessed instead will be wrong, and will be
wrong invisibly. Established techniques:

| need | method |
|---|---|
| width of a rendered string | render a frame, scan ink extents with PIL, back-project through the known camera |
| element offset before it exists | measure in a real browser at the real font |
| is anything actually moving | `ffmpeg blend=all_mode=difference` + `signalstats` |
| does the frame show what I claim | extract that frame and **look at it** |

A measured constant carries its provenance and its re-measure trigger:

```ts
/** MEASURED off a 1600x900 render at scale 1.4512: ink runs canvas x 210..966.
 *  Depends on Lexend's metrics for this exact string — RE-MEASURE IF IT CHANGES. */
const TEXT_W = 521;
```

Keep such a constant in the **same module as the string it measures**. Split across
files, it is a trap.

### 3.4 One continuous move, and geometric scale

- **A push that pauses reads as two moves.** No hold in the middle, ever. A scene
  fades in *already moving*.
- **Interpolate `log(scale)`, not `scale`.** Linear scale reads as a rush that
  stalls, because the same `+0.1` is a large change when wide and a small one when
  close. Log-space makes every frame the same *multiple* of the last.
- **Never put a waypoint inside `interpolate`** for a camera. A 3-point interpolate
  applies the easing curve *per segment* and produces a measured **1.37× velocity
  jump** at the waypoint. One eased `p` from 0→1, then map both scale and centre
  through it.
- **Camera curve:** `Easing.bezier(0.3, 0.4, 0.3, 1)` — moving on arrival, long
  deceleration. `EASE_OUT` (2.77× at t=0) reads as a lurch.

### 3.5 Fades clean up travel; they are never the animation

Text **rises into place**. Opacity only tidies the entry and exit. A cross-fade
between two sentences reads as a slideshow; a line that travels carries the argument
forward.

Corollary for timing: **do not charge reading time to the fade.** While a line is
travelling it is only partly legible. Reading time is the *opaque* window.

### 3.6 Cadence — everything is cut to one beat

Pick the beat (a script line ≈ 50 frames at 24 fps) and hold every gesture near it.

The camera push once ran **2.08× the beat** and read as a different film spliced in.
At 1.40× it is still the longest single gesture — which it should be — and
recognisably the same tempo.

**Evenness is a design choice, not a defect.** A flat cadence is a rhythm. Deriving
each line's hold from its own character count dissolves that rhythm. Fix the
*outlier*, not the system: when one line demanded **43.6 cps** against a 17 cps
standard for on-screen Dutch, it got a short beat more and every other line stayed
exactly as it was.

Use cps to *diagnose*, not to *drive*.

**Per-line config keyed by index must move when the script does.** `{ 3: 14 }` hands
its extra beat to whatever line inherits index 3 after a cut. Worse, an index that no
longer exists — `LINE_STARTS[4]` in a script that lost a line — is `undefined` inside
an `interpolate` range, which is a **NaN opacity, not an error**. Nothing throws; the
element just stops rendering. Re-derive every index-keyed value after any change to
the list, and say so in the comment next to it.

### 3.7 Boil vs flicker — two mechanisms, two homes

- **Boil** — lines redrawn every frame or two. **Only on drawn artwork.**
  Implemented by **regenerating the geometry** from a seeded PRNG (jittered control
  points, stroke width, overshoot). *Not* by displacement maps: displacement moves
  pixels but cannot change line weight, so it reads as a wobbling photo rather than a
  redrawn line. Drawn marks want a **bold** stroke, not a hairline.
- **Flicker** — glyphs swapping on independent per-character clocks. For numbers and
  figures.
- **Type never boils.** Ever.

Both ride the 24 fps carrier; redraw on twos (12/s) unless there is a reason.

### 3.8 Crimson marks the meaning, not the word

At most twice per screen, per the design system. And prefer marking the *state* over
the *token*: in the closing card, red marks the money glyphs **while they are
guessing**, and the settled word lands in ink. Colour then reads as "a figure being
pinned down" rather than as a highlighted word — and it says the thing the film is
about.

### 3.9 The wordmark inside prose is 700

Setting the brand name at 500 reads as vocal stress. At 700 — the end card's own
weight and tracking — it reads as **the logo appearing inside the sentence**, which
is what makes the end card land as a return rather than a first sighting.

### 3.10 Nothing outside the palette

No photographic wash, no stock texture, no device mockup, no browser chrome. A panel
reads as an object floating in space through **shadow and eased perspective alone** —
that is sufficient, verified. Every exception granted to this rule has been withdrawn
later.

### 3.11 Bind related timings to one anchor

Two things that must move together must not carry two numbers.

```ts
click: 156,
get panStart() { return this.click - 20; },   // the pointer's own stop frames
get panEnd()   { return this.click - 2;  },
```

The camera and the cursor are then one gesture by construction, and moving the click
can never desynchronise them.

### 3.12 A pan stops as soon as its subject is framed

Do not centre on a small target at high zoom — it parks half the frame on empty
background. Stop when the subject reaches ~90% across. Anchoring on the *container's*
edge overshoots, because the target usually sits inside that edge.

---

## 4. Structure

```
src/
  Root.tsx                  one Composition per canvas
  compositions/<Film>.tsx   script · sequencing · scene lengths
  scenes/<scene>.tsx        one scene, exporting its OWN length
  lib/motion.ts             the shared easing curves + clamp
  lib/question.ts           content + the measurements bound to it
  lib/<block>.tsx           reconstructed product blocks at product pixels
  studies/<Study>.tsx       one mechanism, one meaning, for reacting to
```

A scene **exports its own `SCENE_LEN`**. The composition imports it. The scene owns
its length; the two can never drift.

Curves live in `lib/motion.ts` so a scene cannot quietly invent a fifth easing that
reads as a different film. An inline `Easing.bezier(...)` duplicating a named curve
is a defect.

### Motion studies

When direction is unsettled, build a **reel of contrasting one-mechanism studies**
with a label and frame counter burnt in, and let the user say "4 yes, 7 no". Far
cheaper than arguing about a described idea. Keep them; they are the vocabulary.

---

## 5. Verification — a render completing is not a render being correct

Every claim about what is on screen must come from a frame you extracted and looked
at. Never from arithmetic alone; the arithmetic has been right and the frame wrong.

```bash
ffprobe -v error -show_entries format=duration -of csv=p=0 out/film-desktop.mp4
ffmpeg -y -ss <t> -i out/film-desktop.mp4 -frames:v 1 out/check.png
```

Then **read the image**.

Compute each beat's timestamp from the derived scene offsets rather than reusing an
old number — offsets shift whenever any earlier scene changes.

Traps that have each cost real time:

- **Stale renders.** A render started before an edit finishes *after* it and looks
  authoritative. Re-render after every change; never sample a file whose render began
  earlier.
- **Sampling exactly on a state change.** Landing on the click frame shows the field
  already cleared and reads as a bug. Sample a few frames either side.
- **`pgrep` patterns.** The process is `remotion-cli.js render`, not `remotion render`.
- **Verify the narrowest and widest canvas.** The failures live at the extremes, and
  the shorter canvas is not always the one that breaks.
- **`pnpm lint` may be a stub** in a Remotion app with no eslint config wired — it
  prints success without checking. `typecheck` is the real gate.

---

## 6. Anti-patterns

- Animating before the script text is approved.
- Asking the user to write the script. Draft it; they edit words, not arguments.
- Starting from a described screen instead of a screenshot of it.
- Taking "make it like X" as a reference without pinning down what in X.
- A claim the product's own limits page disclaims.
- A number on screen that nobody derived or measured.
- A hold in the middle of a camera move.
- Linear scale interpolation, or a waypoint inside a camera `interpolate`.
- Opacity used as the animation instead of as cleanup.
- Boil on type. Displacement-map "boil" on anything.
- Per-format authoring, cropping to fit, or a per-format constant that a
  content-derived one would replace.
- A measured constant living in a different file from the thing it measures.
- Two numbers for two things that must move together.
- Deriving every line's timing from its own length and calling the flat cadence a bug.
- Leaving an index-keyed constant pointing at the old index after cutting a line.
- An array index in an `interpolate` range that no longer exists — silent NaN.
- Claiming a frame shows something without extracting it.

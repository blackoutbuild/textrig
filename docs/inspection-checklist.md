# Inspection checklist — self-check loop protocol

Operating procedure for the AI agent driving the rig/animate loop: after
every build + render, judge the result against this checklist.

## 1. The 7 yes/no items

Check every item against the filmstrip (and other artifacts per §4 below)
before delivering. Each is yes/no — no partial credit.

1. Static zones do not move.
2. Pieces move independently, no separation at joints.
3. No holes/gaps revealed behind pieces.
4. Draw order correct, no z-flicker. (Order may be animated — a multi-piece
   rig can carry `order` piece tracks that shift a piece's depth for a
   window; check draw order AT the keyed windows, not just at rest — a piece
   crossing in front on schedule is correct, not flicker.)
5. Swaps fire at the intended moments.
6. Loop is seamless.
7. Amplitude matches the request.

## 2. Fast vs quality mode

Mode is selected by the user's request in prose (e.g. "quick draft, don't
polish" → fast); when the request doesn't say, default
is **quality**.

- **`fast`** — one pass: markup → build → render → deliver immediately. No
  inspection loop runs. Deterministic validators (pre-build, free — seamless-loop
  check, scale sanity, polygon bounds, variant existence) still run. (The
  seamless-loop VALIDATOR only asserts key-value equality, final = first,
  pre-build; checklist item 6 is a different check — the rendered motion,
  judged visually.) Deliver labeled **"draft, not self-checked"**.
- **`quality`** (default) — run the full loop below. Deliver only when every
  one of the 7 items is "yes", or the iteration budget is exhausted — in the
  latter case deliver anyway, WITH a shortfall report naming which items are
  still "no".

## 3. Iteration budget

- **≤4 iterations.** One change per iteration — never bundle two fixes into
  one round, or you can't attribute the effect.
- **Escalation order**: markup (polygons/bones) → generator knobs
  (fill/power/mesh/bg) → keyframes. Try the cheapest, most structural fix
  first; only reach for keyframe tweaks once markup and knobs are ruled out.
- **No convergence in 4 iterations → stop.** Report to the user with
  filmstrips of every attempt (not just the last). Never silently keep
  looping past the budget.

## 4. Which artifact to inspect for which suspicion

- **Filmstrip 8×256** (what `render` returns) — the per-iteration
  workhorse. Default inspection artifact for every round.
- **`out/<name>.debug.png`** — when the suspect is markup (polygon cuts,
  bone placement) rather than motion. Shows polygons + bones + piece labels
  over the source image.
- **Denser filmstrip (`render(frames=12)`) or numeric probes** (crop +
  measure pixel brightness/color with PIL) — for timing questions or subtle
  motion that a coarse 8-frame filmstrip can't resolve (e.g. "did the swap
  fire at the right moment").
- **Large frames (`render(frames=4, size=512)`, or a `zoom` close-up)** — for joint/seam
  close-ups (separation, holes, z-flicker) where 256px is too coarse to see
  the defect.
- **Gif** — final acceptance only, never mid-loop. Shows real-time motion but
  is expensive to inspect frame-by-frame.

## 5. Practical probes (worked examples on the robot rig)

- **Swap timing** — verified numerically, not by eyeballing: measure the
  eye-zone's average brightness per filmstrip panel and confirm it jumps only
  at the sampled times matching the `display` track keys, nowhere else
  (robot eye-glow swap: brightness
  jumps at t=1.125, 2.25, 2.4375 and nowhere else — matches item 5).
- **Rest-pose fidelity** — side-by-side the rendered rest frame against the
  source PNG; catches occlusion-fill artifacts and polygon-cut errors before
  animating at all (robot hose piece: no double image,
  no dome tear at rest).
- **Deform quality / joint smear** — stress-test at an amplitude beyond the
  planned one (e.g. head −6° when the target is −1.5°), confirm no smear or
  detachment, then settle on a calmer final value inside the range that held
  up under stress (robot: stress-tested at head −6°, shipped
  head −3° — livelier than the original −1.5° but still clean at the tested
  extreme).

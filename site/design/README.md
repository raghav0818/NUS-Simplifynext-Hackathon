# Design source for the marketing site

These are the working files behind the Ante marketing-site design. They are the
*source*, not the deliverable: `site/index.html` gets built from them.

Published canvas:
<https://claude.ai/code/artifact/57800032-0142-4dec-9c0c-d3e39414351c>

## What is here

| File | |
|---|---|
| `Main.dc.html` | 1 — Hero. `ANTE.` low-left over the darkened board |
| `Thesis.dc.html` | 2 — *Nobody tells you the number moved* |
| `Clocks.dc.html` | 3 — law / growth / recurring, and why calendars only catch the third |
| `Findings.dc.html` | 4 — the six real demo-vault findings as a menu, S$4,800/yr total |
| `HowItWorks.dc.html` | 5 — the vault, and the curator that nobody starts |
| `Guarantee.dc.html` | 6 — the five gates, and a real refusal |
| `WhereAI.dc.html` | 7 — two model calls, and everything deliberately not AI |
| `Privacy.dc.html` | 8 — payroll stays on the laptop |
| `Cta.dc.html` | 9 — the commands that need no AWS |
| `Components.dc.html` | every button, chip, row and card in all four states |
| `canvas.json` | frame positions and sizes; `board-hero.jpg` is referenced by filename |

Each `.dc.html` is one artboard. Frames are 1440×900 desktop; heights vary per
section and are sized to the content — a frame smaller than its root clips
silently rather than scaling, so heights here were measured, not guessed.

## Editing

Re-seed a fresh payload from these files with the `design` skill's helper, then
republish to the same artifact URL. Never hand-edit the seeded ~2.5MB output —
it is a build artifact and is deliberately not committed here.

## Design rules worth not losing

Black and white everywhere. The **only** colour on the site is the three clock
hues and the in-force red, and they appear **only** inside findings — which is
what makes colour read as meaning rather than decoration, and what makes the
site and the Runway Board look like one product.

Poppins caps for display, Lora serif for all body copy (a serif reads as
document and statute — the register a compliance product wants), IBM Plex Mono
for figures, commands and `.gov.sg` citations. Buttons have zero border radius.

Every figure on the site traces to something real in this repo. A compliance
product overstating itself on its own homepage is the one irony to avoid.

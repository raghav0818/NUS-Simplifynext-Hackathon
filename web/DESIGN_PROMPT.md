# Design prompt — Ante

Paste everything below the line into Claude Design.

---

Design the interface for **Ante**, a regulatory early-warning tool for Singapore startup
founders. I want a restrained, typographic, information-dense product interface — not a
marketing page and not a generic dashboard. Read all of this before drawing anything.

## 1. What the product does

A founder's payroll and company facts live in a local markdown vault on their own laptop.
Ante watches six Singapore government rules (MOM, CPF, IRAS, ACRA) and answers one
question: **who here is on the wrong side of a rule, and what does it cost?**

Two loops run behind it. An **advisor** answers questions with citations. A **curator**
re-reads the government pages every morning, and when a figure moves it rewrites the rule
— but only after five deterministic checks prove the new number is literally printed on
the `.gov.sg` page. When it cannot prove that, it refuses and escalates to a human.

The product's whole credibility rests on one claim: **Ante cannot state a number that is
not on a government page.** The interface has to make that visible, not just true.

## 2. Who opens it, and what they feel

A non-technical founder of a 12-person startup. No HR department, no compliance officer.
They open Ante because they are quietly afraid there is a bill they have not budgeted for.

They do not want a compliance checklist. **They want the bill, the deadline, and the one
thing to do next.** If they read one screen for eight seconds and close it, they should
have learned: how much, who, and when.

The single most valuable and most under-served thing this product shows is
**already-in-force obligations** — a rule that took effect months ago and was never
adjusted for. That is money leaking right now, and no calendar app can show it because the
date is in the past. In this interface it is the loudest thing on the screen.

## 3. Visual direction

Take the reference from **Mobbin's own marketing site** — but I care about the underlying
discipline, not the surface:

- **Near-monochrome.** A warm off-white ground and near-black ink in light mode; a true
  dark ground in dark mode. Colour is a scalpel, not a theme.
- **Typography carries the hierarchy**, not boxes, not colour fills, not shadows. If two
  things differ in importance, they differ in size, weight and spacing first.
- **Hairline borders.** 1px at roughly 8–10% ink. Structure comes from lines and
  whitespace. Shadows are for genuinely floating layers only (menu, modal) — never to
  separate a card from a page.
- **Generous, confident whitespace**, but dense where density is information — a roster
  row does not need 32px of padding.
- **Content is the hero.** The numbers and the government sentences are the design.

Concrete tokens to work to:

| | |
|---|---|
| Type | One neutral grotesque (Inter, Söhne, or similar). Body 14px/1.55. |
| Scale | 11–12px meta (uppercase, +0.06em tracking), 14px body, 15–16px card title, 24–28px section, **48–64px for the money figure** |
| Numbers | `font-variant-numeric: tabular-nums` **everywhere**, without exception |
| Radius | 10px cards, 6px controls, 999px pills |
| Rhythm | 24px card padding, 16px grid gap, 72–96px between sections |
| Motion | 120–160ms ease-out. Opacity and 2px translate only. No bounce, no spring. |

**Colour rules, and they are strict:**

- The three clocks each get exactly one hue — `law` (violet), `growth` (green),
  `recurring` (amber) — and it may only appear as a **2px left rule, a 6px dot, or the
  label text**. Never as a filled card background.
- **Red is reserved.** It means *already in force* or *overdue human review*. Nothing else
  in the product is allowed to be red, so that when red appears it means one thing.
- Both light and dark must be first-class. Design the light palette first, then re-derive
  dark — do not just invert.

## 4. Anti-patterns — do not do these

The previous attempt failed on these, so be explicit about avoiding them:

- No gradient hero, no glassmorphism, no glow, no neon, no mesh backgrounds.
- No emoji as icons. Use a single line-icon set at one weight, or no icons at all.
- No "dashboard soup" — eight stat tiles of equal visual weight, none of them the point.
- No card whose header is a saturated colour block.
- **No chart that does not earn its place.** A pie chart of six rules is noise. The only
  chart-shaped thing that belongs here is the timeline in §5.1.
- No `rounded-3xl` + heavy drop-shadow + purple-to-blue button. That is the default AI-UI
  look and it reads as untrustworthy on a compliance product.
- No centred marketing hero. This is a tool someone opens every morning.
- **No invented data.** Use the real payloads in §6. If a number is not in them, it does
  not appear on screen.

## 5. Screens

### 5.1 The Runway Board — the hero screen

One page. This is 80% of the product and the only screen that must be perfect.

**The one job:** in eight seconds, how much, who, when.

- **A headline number at the top.** But read this carefully, because it is the most
  interesting constraint in the whole brief: only **two** of the six findings have a
  computed dollar figure (`annual_gap_total: 4800`). The other **four** are priced in
  human-written prose in the rule note, because an employer CPF rate or a penalty tier is
  not in the vault and the system refuses to invent one.

  So the headline may **not** read "S$4,800 total exposure" — that would understate the
  bill by omission, which is exactly the failure this product exists to prevent. Design an
  honest headline that shows the provable figure *and* signals that four more findings
  carry costs stated in prose. Solving this well is the single best thing you can do in
  this brief.

- **Three columns, matching the three clocks:** *Law moved · You grew · Date arriving*.
  A card per affected party per rule.

- **Each card carries:** who, their number against the threshold, the date it lands, the
  clock, and a source link. Already-in-force cards show a red `−248d` badge and sort to
  the top of their column — being in the past makes a card *more* urgent, never less.

- **A timeline strip**, 18 months wide, today as a vertical line. Everything left of the
  line is in force and unbudgeted. This is the one graphic that earns its place, because
  it shows the thing a calendar structurally cannot.

- **Empty state matters.** "Nothing bites in the next 90 days — 6 rules checked 2 hours
  ago" should feel like proof of work, not absence. A quiet day is a feature here.

### 5.2 Rule detail — the trust screen

Opens from a card. The job is to make a claim inspectable.

- The rule's headline figure, `threshold_before → threshold_after` as a real diff.
- **The `cost` and `next_step` prose, quoted verbatim.** A human wrote these. Set them in
  a way that reads as quotation, not as UI copy — they must not look machine-generated,
  because they aren't.
- The government source, with the URL visible rather than hidden behind "Learn more".
- **The three freshness clocks, shown separately and never merged:**
  `verified` (a person read the page), `checked` (the bot fetched it), `confirmed` (the
  bot found the figure still there). Collapsing these into one "last updated" would let a
  daily bot sweep pass for a human having actually read the law. Keeping them apart is a
  feature — design it as one.
- If `overdue_human_review` is true, that is red.

### 5.3 The refusals tray

Alerts where `status: needs_human_check`. Most products hide failure. This one leads with
it: a bot that declines to write an unverified figure is more convincing than one that
claims it is never wrong.

Each entry: what happened, why Ante refused, and what the human must now do. Give this a
calm, confident tone — it is a strength being reported, not an error state.

### 5.4 Change history and undo

Per rule: every auto-applied change, newest first, as a diff — *"S$7,400 → S$8,000"* —
with the old note beside the new one and the matched government sentence highlighted. One
**Undo** button per change.

A machine amending a regulation, and a person overruling it in one click, is the demo.
Design that moment.

### 5.5 Onboarding / upload

Four fields — company name, UEN, financial year end, revenue run-rate — plus a payroll CSV
drop zone. Target: first real finding in under two minutes.

**Design the failure case, because it is the common one.** When a CSV column cannot be
identified, the API returns `needs_mapping` with the file's actual headers. Show a
per-field dropdown of the founder's own column names. This must feel like a normal step,
not an error — real payroll exports never match a schema on the first try.

Also surface, quietly and clearly: **this file never leaves your computer.**

### 5.6 Chat — a sidebar, never the front door

The board is already on screen for free. Chat answers "why?". Collapse the agent's tool
calls into readable steps — *searched 6 rules → read 3 → scanned 12 staff → 4 findings* —
so the reasoning is legible without being a log dump.

## 6. Real data — design against these exact payloads

`GET /api/findings` (free, instant, no AI — this renders the board):

```json
{
  "rules_with_exposure": 6, "parties": 6, "annual_gap_total": 4800.0,
  "priced_in_prose": 4, "company": "Harborlight Analytics Pte Ltd",
  "as_of": "2026-09-06",
  "rules": [{
    "path": "rules/cpf-ow-ceiling-2026.md",
    "title": "CPF Ordinary Wage ceiling rises from S$7,400 to S$8,000",
    "clock": "law", "lands": "2026-01-01", "days_until": -248, "in_force": true,
    "severity": "medium", "trigger_field": "monthly_salary",
    "threshold_before": 7400, "threshold_after": 8000, "unit": "SGD_per_month",
    "bites": "above",
    "resource": "https://www.cpf.gov.sg/service/article/what-is-the-ordinary-wage-ow-ceiling",
    "affected": [{"who": "Staff 01", "value": 8200, "age_band_on": "30-34", "annual_gap": null}]
  }, {
    "title": "GST registration becomes compulsory above S$1 million taxable turnover",
    "clock": "growth", "lands": null, "days_until": null, "in_force": false,
    "severity": "high", "trigger_field": "annual_revenue_run_rate",
    "threshold_after": 1000000, "unit": "SGD_per_year", "bites": "forecast",
    "resource": "https://www.iras.gov.sg/taxes/goods-services-tax-(gst)/...",
    "affected": [{"who": "Harborlight Analytics Pte Ltd", "value": 862000, "annual_gap": null}]
  }]
}
```

The six real findings: **Staff 01** CPF ceiling (in force 248 days), **Staff 02** senior
CPF rates, **Staff 04** S Pass floor S$3,400 vs S$3,600, **Staff 07** EP floor S$5,800 vs
S$6,000, **the company** GST S$862k vs S$1m, **the company** ACRA return due 31 Jul.

`GET /api/rules` — the prose and the three clocks:

```json
{"rules": [{
  "slug": "s-pass-qualifying-salary-2027",
  "title": "S Pass qualifying salary rises to S$3,600 from 1 January 2027",
  "clock": "law", "effective": "2027-01-01", "severity": "high",
  "threshold_before": 3300, "threshold_after": 3600, "revision": null,
  "resource": "https://www.mom.gov.sg/passes-and-permits/s-pass/eligibility",
  "freshness": {"verified": {"on": "2026-09-04", "days": 2},
                "checked": {"on": null, "days": null},
                "confirmed": {"on": null, "days": null}},
  "overdue_human_review": false,
  "cost": "The gap between current salary and the applicable age-banded floor, times 12, plus employer CPF where the holder is CPF-liable. A holder at S$3,400 facing a S$3,600 floor costs an extra S$2,400 a year at the entry band alone.\n\nIf the salary is not raised, **the renewal fails and the worker cannot continue.**",
  "next_step": "List every S Pass holder with their pass expiry date and current salary. For anyone whose pass expires on or after 1 Jan 2028, look up their exact age-banded requirement on the MOM page and calculate the gap now. Decide well before renewal whether to raise the salary, restructure the role, or hire locally — a failed renewal has no remedy."
}], "stale_after_days": 365}
```

`GET /api/alerts` — the refusals tray:

```json
{"needs_human_check": 1, "alerts": [{
  "raised": "2026-09-05", "rule": "rules/cpf-ow-ceiling-2026.md",
  "severity": "medium", "status": "needs_human_check", "drift": "unverifiable",
  "what_happened": "`unverifiable` checking CPF Ordinary Wage ceiling against https://www.cpf.gov.sg/... — page never mentions ['Ordinary','Wage','ceiling']; likely JS-rendered",
  "next_step": "A human must open the source and re-confirm the figure, then update `verified:` in the rule note. The agent may not edit rules/."
}]}
```

`GET /api/health` — the status bar:

```json
{"problems": [], "rules": 6, "people": 12,
 "bedrock": {"alive": false, "detail": "credentials expired -- refresh env/.env"},
 "last_sweep": "2026-09-06 curator: 6 checked, 0 changed, 0 applied, 0 refused, 0 alerts, 0 in / 0 out tokens",
 "overdue_human_review": []}
```

Other endpoints: `GET /api/history/{slug}`, `POST /api/rollback/{slug}` (undo),
`POST /api/ask` (chat, ~30s), `POST /api/upload`, `POST /api/brief` (email).

**A degraded state you must design, not treat as an error:** when `bedrock.alive` is
false, the board is still completely correct — every figure came from the vault, not the
AI. Only the written commentary is missing. Show "commentary unavailable", never "Ante is
down". A greyed banner, not a red one.

## 7. Formatting rules

- Money: `S$8,200`, thousands separated, no cents unless they exist.
- Days: `−248d` for in force (red), `in 117d` for upcoming, `standing threshold` when the
  rule has no date.
- People are `Staff 01` / `Staff 04` in the demo vault; real vaults carry real names, so
  do not design around a fixed-width name.
- Every figure on screen is one click from its `.gov.sg` source. A number with no
  reachable citation is a bug.
- Long government URLs need truncation that keeps the domain visible.

## 8. Deliver

Artboards for: the Runway Board (light **and** dark), rule detail, the refusals tray, a
change diff with undo, upload including the column-mapping fallback, and the chat sidebar
open over the board.

Show the empty state and the credentials-expired state — the states that are usually
skipped are the ones this product is judged on.

Wide desktop first, but the board must reflow to one column on a laptop without becoming a
list of undifferentiated cards.

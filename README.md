# Ante

**Regulatory early warning for Singapore startup founders.**

A founder doesn't miss a deadline because they're lazy. They miss it because nobody told them
the number moved. Ante watches Singapore's rules and the company's own figures, and warns
when the two are about to collide — with a `.gov.sg` link attached to every claim.

Two things are going on, and it matters that they're separate:

| | Who starts it | What it does |
|---|---|---|
| **advisor** | the founder asks a question | answers it from the vault, with citations |
| **curator** | nobody — it runs daily | checks the law still says what we claim it says |

Everything both of them know lives in one folder of markdown files (`vault/`). That folder is
the product. The Python is just how it gets read and written.

---

## The core idea: three clocks

A founder gets blindsided in exactly three ways, and the vault tags every rule with which one:

- **`law`** — the rule moved. Same company, new number. *(S Pass floor rises to S$3,600 in 2027.)*
- **`growth`** — the company moved. Same rule, and you just grew into it. *(Revenue crosses S$1m → GST registration becomes compulsory.)*
- **`recurring`** — neither moved, a date just arrived. *(ACRA annual return, 7 months after your own FYE.)*

Calendar apps only handle the third one. The first two are where the money is.

---

## The layers

Bottom to top. Each layer only trusts the one below it.

### 1. `vault/` — the knowledge base (markdown, human-owned)

Plain `.md` files with YAML frontmatter. Open it in Obsidian and browse it like a wiki; it's
also just text, so git diffs are readable and an agent can grep it.

```
vault/
  rules/          6 Singapore rules, one file each — the note IS the retrieval chunk
  company/        Harborlight Analytics Pte Ltd (synthetic demo customer)
  people/         12 synthetic staff — salary, pass type, age band
  counterparties/ agent-written: watched entities and their ACRA status
  alerts/         agent-written: what was raised, with dollar impact and deadline
  log.md          agent-written: chronological run history
  _SCHEMA.md      the contract between the markdown and the code — read this one first
```

It's an [Open Knowledge Format](https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing)
v0.1 bundle: every file has a non-empty `type`, only five types exist
(`rule` · `company` · `person` · `counterparty` · `alert`), links are standard markdown
(never `[[wikilinks]]`), and `index.md` / `log.md` are reserved names.

**Two fields do the joining.** A rule says `applies_to: s_pass_holder` and
`trigger_field: monthly_salary`; the code matches that against the roster and pulls each
person's own salary to compare. The vocabulary is tiny and matched by exact string, so it has
to be spelled identically on both sides — that's the whole integration surface.

**Three rules about who writes what**, and they are the safety story:

1. `# Next step` in a rule note is **written by a human**. The agent quotes it. It never
   invents regulatory advice.
2. `verified:` is **written by a human** — the day a person read the government page. The
   machine writes `checked:` and `confirmed:` instead. Different claims, kept apart.
3. The agent can only write to `alerts/` and `counterparties/`. `rules/`, `people/` and
   `company/` are human-owned and writes to them are refused.

All person records are **synthetic**. Our own rule base contains the PDPA; shipping real
employee data inside it would breach the thing we're building.

### 2. `vault.py` — read/write the vault (no LangChain, no AWS)

Four functions the agent eventually calls as tools, plus two extras:

- `vault_search(query)` — keyword-score the rules, return **summaries only**. Search first.
- `vault_read(path)` — one note in full: frontmatter + body.
- `roster_scan(applies_to)` — everyone in that category, with their numbers. Or the company's
  own figures when `applies_to: company`.
- `vault_write(path, frontmatter, body)` — refuses anything outside `alerts/` and
  `counterparties/`, refuses path traversal (`alerts/../rules/x.md` is checked *after*
  resolving), refuses a note with no `type`.
- `validate()` — the integration guard. Fails loudly when the markdown and the code drift
  apart, including *"this rule's `applies_to` matches nobody"* — a rule that joins to no one is
  a dead rule.
- `upcoming()` — what lands in the window, looking **backwards too**. A rule that took effect
  eight months ago and was never budgeted for is the expensive kind, not the irrelevant kind.

`roster_scan` deliberately returns everyone in a *category*, not everyone who *breaches* the
rule. Doing the comparison is the model's job — this file reports facts, it doesn't judge.

Run it standalone: `python vault.py` validates the vault, self-tests all four tools
(including the refusals), then prints what lands within 500 days.

### 3. `ante/model.py` — one Bedrock client

Claude Haiku 4.5 via `langchain-aws`, `temperature=0` (regulatory work, not creative writing).

Two facts about the hackathon AWS account are baked in here:

- SSO tokens die every ~12h, so **credential failure is a normal operating state**, not an
  exception. It gets its own type (`CredentialsExpired`) so the curator can checkpoint and
  resume instead of crashing.
- The org's service control policy denies `global.` inference profiles everywhere, and
  ap-southeast-1 offers *only* `global.` for this model. So despite Singapore being the obvious
  home for Singapore compliance data, the only combination that actually invokes is
  **us-east-1 + `us.`**. Candidates are *probed* rather than assumed, so the day the policy is
  relaxed Ante moves to Singapore with no code change.

`python -m ante.model` checks credentials and pings the model.

### 4. `ante/schema.py` — the contracts

Two Pydantic models, deliberately strict, because validation failures here *are* a published
metric:

- **`Alert`** — what the advisor produces. `resource` is required and regex-checked to be a
  primary `.gov.sg` URL, so **an uncited alert physically cannot validate**. That's answer
  fidelity enforced structurally, not by prompt.
- **`Verdict`** — what the curator's model returns. Every field is a *claim*, re-checked
  against the fetched page before anything is written.

### 5. `ante/advisor.py` — the question-answering graph

A LangGraph ReAct loop over the five tools. Four non-obvious decisions:

1. **The company's figures and the rule index are pre-injected** into the system prompt, not
   discovered. Every question needs both, neither changes mid-run, and the turn budget is ~6
   model calls — spending two of them fetching what we already know is how a run runs out of
   road before it reaches the roster.
2. **Only the profile's frontmatter goes in, not its body.** The body is the vault author's
   commentary and it names which staff trigger which rule. Injecting it would let the model
   *recite* the answer instead of deriving it.
3. **The checkpointer is keyed on the company's UEN** (sqlite, `state.db`). Follow-ups land
   with the previous answer in scope, and a run that trips the recursion limit can still be
   read back out of sqlite rather than vanishing.
4. **Alerts are extracted by a second, separate call.** One turn asked to both explain a
   situation to a founder *and* emit strict JSON does neither well.

The extractor is handed a literal `rule_path -> resource` table to **copy** URLs from, rather
than recalling them — which is why alerts validate instead of failing on a hallucinated link.

```python
out = ask("what changes for us in the next 90 days?")
out["answer"]   # prose for the founder
out["alerts"]   # list[Alert], validated
out["metrics"]  # the six numbers
```

### 6. `ante/detect.py` + `ante/apply.py` + `ante/curator.py` — the daily sweep

Three tiers, cheapest first, because **a daily sweep that costs tokens on a no-change day is a
daily sweep that gets switched off**.

**Tier 1 — `detect`. Pure Python. Zero tokens.** Every rule, every day. Fetches the source page
and hashes *only the region around that rule's own keywords* — a whole-page hash fires on
cookie banners and rotating footers and would wake the model for nothing. On a quiet day the
run stops here and **the model is never even constructed**.

**Tier 2 — `understand`. Bedrock, only for rules whose page actually moved.** The model is given
**no tools at all**. It can't fetch, write, or act — it returns a `Verdict` describing what it
read, and it's told in caps that `quote` must be copied character-for-character out of the new
page because a literal string comparison is coming.

**Tier 3 — `verify` → `apply`. Pure Python. Five gates, all must pass.**

1. verdict is actually an applicable change (`figure_changed` / `date_changed`)
2. confidence is `high` — a hedge is refused
3. the quote appears **verbatim** on the page we fetched (≥20 chars)
4. the new figure appears **inside the quote it was supposedly read from**
5. it isn't a no-op, and the rule isn't "flapping" (changed >2 times in 30 days → frozen for a
   human, because a rule that keeps moving means *our extraction* is wrong, not the law)

Gates 3 and 4 are the guarantee the whole pitch rests on: **Ante cannot write a figure that is
not literally printed on a `.gov.sg` page**, and that's enforced by string comparison, not by
asking the model nicely. `python -m ante.curator --negative` proves it — it feeds the gates a
fabricated "$9,900" quote against the real live page and asserts the rule file comes back
byte-identical.

Anything that fails a gate isn't dropped, it's **escalated**: an alert lands in `alerts/` for a
human, and the stale snapshot is deliberately kept so tomorrow's sweep raises it again until
someone clears it. Applied changes back up the old file into `vault/.history/` first, so
`--rollback <slug>` always works. Frontmatter is edited line-by-line rather than re-dumped
through YAML, so human comments and key order survive.

**Why LangGraph and not a for-loop:** `detect` never needs AWS and always succeeds, but this
account's SSO dies every ~12h, so `understand` failing mid-run is routine. The sqlite
checkpoint on `sweep-<date>` means the next authenticated run **resumes at `understand` with
the already-fetched pages still in state**, instead of re-crawling six government sites.

### 7. `ante/metrics.py` — the six published numbers

One counter object per run, printed at the end of everything. Retrofitting measurement the
night before submission is how teams lose the technical mark.

Schema Validation Pass Rate · Tool-Call Success Rate · Task Completion Rate · Token Cost Per
Run · Loop Discipline (turns used / limit) · Answer Fidelity (claims carrying a `.gov.sg` cite).

This is also why **every tool returns `{"error": ...}` instead of raising**. An exception inside
a `ToolNode` kills the run; a returned error lets the model read what went wrong and try
something else. That difference is most of the Tool-Call Success Rate.

---

## Running it

```bash
pip install -r requirements.txt          # python 3.13
# AWS SSO creds go in env/.env  (gitignored)

python vault.py                          # validate vault + self-test tools. No AWS needed.
python -m ante.apply                     # gate self-check. No AWS needed.
python -m ante.model                     # is Bedrock reachable?
python -m ante.advisor                   # ask the demo question, print alerts + metrics
python -m ante.curator --dry-run         # full sweep, writes nothing
python -m ante.curator                   # live sweep
python -m ante.curator --only s-pass-qualifying-salary-2027
python -m ante.curator --rollback s-pass-qualifying-salary-2027
python -m ante.curator --negative        # proof it cannot invent a figure
```

Every module runs standalone and self-checks. Nothing needs a UI to demo.

### One thing to know before you touch the code

`freshness.py` at the repo root is the **standalone precursor** to `ante/detect.py` — same
figure-matching logic, written first, kept because `python freshness.py` gives a fast
no-AWS "are all six sources still saying what we claim" table. `ante/detect.py` is the version
the graphs actually use. If you change the figure-matching heuristics, change both or delete
`freshness.py`.

---

## UI/UX ideas

There is **no UI yet** — everything is CLI plus Obsidian. That's a blank canvas, and the demo
currently lives or dies on terminal output. Ranked by demo impact per hour of work:

### 1. The Runway Board (the hero screen)
One page, three columns matching the three clocks: **Law moved · You grew · Date arriving**.
Each card: who it hits, the dollar number, the date, a `.gov.sg` favicon-linked source. Cost of
inaction totalled at the top in one big number — *"S$4,400 of unbudgeted payroll and S$600 of
avoidable penalties in the next 14 months."* Founders don't want a compliance list, they want
the bill.

### 2. Timeline, not a calendar
A horizontal 18-month strip with today as a vertical line. Anything **left of the line is
already in force and unbudgeted** — colour it red, because that's the expensive category
calendar apps structurally cannot show. `vault.upcoming()` already returns exactly this shape
including negative `days_until`; it's a rendering job, not a logic job.

### 3. Show the citation, not a link
Hovering a figure expands the actual sentence lifted off the government page, greyed, with the
URL beneath. We already store that quote in the snapshot and the `Verdict`. This is the single
highest-trust-per-pixel thing we can build and it's nearly free.

### 4. A visible diff for every auto-applied change
"CPF ceiling **S$7,400 → S$8,000**" with the old and new page regions side by side, the matched
quote highlighted, and which of the five gates passed. Plus a one-click **Undo** wired to
`--rollback`. Watching a machine change a regulation and then watching a human veto it in one
click *is* the pitch.

### 5. Show the refusals
A small "Ante refused to act on 2 changes" tray. Most demos hide failure; showing a bot that
declines to write an unverified figure is more convincing than one that's never wrong. Feed it
straight from `alerts/` where `status: needs_human_check`.

### 6. Chat as a sidebar, never the whole product
The advisor is the drill-down, not the front door. Cards on the left, "why?" opens the thread
on the right with tool calls collapsed into readable steps: *searched 6 rules → read 3 → scanned
12 staff → 4 findings*. Loop Discipline becomes a visible trust signal instead of a metric.

### 7. Onboarding is the real UX risk
Nobody fills in twelve staff notes by hand. Ask **four** questions — UEN, FYE, revenue
run-rate, headcount — and generate the vault. Paste-a-payroll-CSV → `people/*.md` is the
follow-up. The rule base ships pre-curated, so a founder should reach their first real alert in
under two minutes.

### 8. Freshness as a visible badge
Each rule shows "human-verified 3 days ago · machine-confirmed today". Ageing past 90 days
turns it amber. It's honest about what a bot can and can't confirm, and it turns the
`verified` / `checked` / `confirmed` distinction from an internal nicety into a feature.

### 9. Small things that punch above their weight
Dollar figures as the largest text on every card. `-14d` badges in red for already-in-force.
Empty state that says *"Nothing bites in the next 90 days — last checked 2 hours ago"* with the
checked count, because a quiet day should feel like proof of work, not absence. And a
**"copy to your accountant"** button that emits the whole finding as pasteable text with
sources — founders forward, they don't share dashboards.

### 10. Skip these for now
Push notifications, multi-tenant login, a settings page, dark mode. None of them are the demo.

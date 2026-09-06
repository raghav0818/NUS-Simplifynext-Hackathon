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
- `roster_scan(applies_to, on=None)` — everyone in that category, with their numbers. Or the
  company's own figures when `applies_to: company`. `on` is the date to judge them at —
  see "an age band is a snapshot" below.
- `vault_write(path, frontmatter, body)` — refuses anything outside `alerts/` and
  `counterparties/`, refuses path traversal (`alerts/../rules/x.md` is checked *after*
  resolving), refuses a note with no `type`.
- `validate()` — the integration guard. Fails loudly when the markdown and the code drift
  apart, including *"this rule's `applies_to` matches nobody"* — a rule that joins to no one is
  a dead rule.
- `upcoming()` — what lands in the window, looking **backwards too**. A rule that took effect
  eight months ago and was never budgeted for is the expensive kind, not the irrelevant kind.
- `exposure()` — **the join, done in Python.** Applies each rule's own `bites` test to the
  people it touches and returns only those genuinely on the wrong side, each carrying an
  `annual_gap` where the shortfall is arithmetic (see below) and `None` where it is not.
- `section(body, heading)` — one `# Heading` block of a note, for quoting a human's `# Cost`
  and `# Next step` wording to the UI verbatim rather than paraphrasing it.

`roster_scan` returns everyone in a *category*; `exposure()` returns everyone who *breaches*.
The difference is the whole reason `bites` exists.

**An age band is a snapshot, and rules bite in the future.** Someone banded `50-54` today
who turns 55 in three weeks *is* in scope for a rule effective 1 Jan 2027 — the CPF
senior-worker note says so itself: *"including anyone crossing 55 during 2026."* Reporting
them only from their birthday would drop the finding for exactly as long as it is still
cheap to act on. So `upcoming()` judges each rule's roster **on the day that rule lands**,
and ingest records `band_changes_on` (the date they enter the next band) whenever the
payroll gave a real date of birth. A CSV that gave only a bare age cannot be projected —
there is no birthday to project from — so the band stands as recorded and the upload
*says so* in `problems` rather than leaving a silent gap.

**Why `annual_gap` is missing on four of the six findings.** The distance between a salary
and the floor it must clear, times twelve, is derived from two figures the vault already
holds and nothing else — so `_gap()` computes it, and the self-check asserts it against what
the human wrote in `# Cost` (both notes say "S$2,400 a year"; Python has to agree to the
dollar). An employer CPF rate and a late-filing penalty tier are *not* in the frontmatter,
so deriving those would mean inventing a figure. Those rules stay `None` and are priced by
their own prose instead. Half a bill that is provable beats a whole one that is not.

**Why `bites:` is a field and not a prompt.** "Is this person non-compliant" is arithmetic, and
arithmetic must not vary between runs. A qualifying-salary floor bites people paid *below* it;
a contribution ceiling bites people paid *above the old one*; a rate change bites the whole
age band; a growth threshold bites on forecast. We tried leaving that taxonomy to the model
and it dropped a different finding every run — six, then five, then three. So each rule now
*states* which side is wrong (`bites: below | above | band | forecast | always`) and Python
does the comparison. The model explains, prices and prioritises; it never decides membership.
Same split the curator uses.

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

A LangGraph ReAct loop over six tools. Four non-obvious decisions:

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
4. **The prose and the structured alerts come from the same run**, via LangGraph's
   `response_format` — not from re-reading the answer afterwards. An earlier version parsed
   the prose back into JSON and kept losing findings the prose had grouped, demoted or moved
   into a "worth planning for later" list; the extractor faithfully copied a summary table
   with fewer rows than the discussion above it. The structured pass now runs *inside* the
   graph, where it can see the tool results instead of a lossy summary.

`exposure()` is called first and decides who is affected, so the model's remaining job is to
explain and cost the findings, not to work out which ones exist. It is handed a literal
`rule_path -> resource` table to **copy** URLs from rather than recall them — which is why
alerts validate instead of failing on a hallucinated link.

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
asking the model nicely. `python run.py negative` proves it — it feeds the gates a
fabricated "$9,900" quote against the real live page and asserts the rule file comes back
byte-identical.

**`python -m ante.replay` proves the same thing without a network.** The live negative test
is the right one to run on stage and the wrong one to depend on: it needs outbound HTTPS to
`mom.gov.sg`, so it cannot run behind a locked-down egress policy, in CI, on a plane, or on
conference wifi. A proof you can only perform when the network agrees with you is not a
regression test. Replay stubs exactly two things — `detect.fetch` and the model call — and
runs everything between them for real: the region hashing, the snapshots, the LangGraph
sweep, the five gates, the writer, and `--rollback`. Six scenarios, each asserting on the
rule file's bytes: baseline · unchanged (0 tokens, `verified:` untouched) · a fabricated
quote · a real quote carrying an invented number · the honest change · an unreadable page.

**A quiet day still runs `apply`.** It costs nothing and it is where two things that must
happen daily live: `checked:` advancing on the rules we really did read, and an alert for a
page that came back unverifiable or errored. Routing a no-change day straight to `log` —
as an earlier version did — meant a sweep where every government page was unreachable or
JS-rendered printed *"0 changed, 0 alerts"* and read as a clean day, while the freshness
badge quietly went stale. A tool built to notice silence cannot be silent about its own.

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
python run.py ask "..."                   # ask the demo question, print alerts + metrics
python run.py sweep --dry-run         # full sweep, writes nothing
python run.py sweep                    # live sweep
python run.py sweep --only s-pass-qualifying-salary-2027
python run.py rollback s-pass-qualifying-salary-2027
python run.py negative        # proof it cannot invent a figure

python -m ante.notify                    # brief self-check, writes to the outbox, sends nothing
python -m ante.ingest                    # onboarding self-check, builds a throwaway vault
python -m ante.api                       # every free route, in-process. No AWS, no network.
python -m ante.replay                    # the WHOLE curator on canned pages. No AWS, no network.
python run.py brief                      # sweep, then email the founder IF something moved
python run.py brief --force              # send regardless (the demo)
python run.py serve                      # the board at 127.0.0.1:8000, plus /docs
```

Every module runs standalone and self-checks. Nothing needs a UI to demo.

### One thing to know before you touch the code

`ante/detect.py` owns **all** the figure-matching heuristics — the number forms a government
page might print, the keyword anchoring, the region hashing. There used to be a second copy in
a root-level `freshness.py`; it was deleted rather than kept in sync, because two copies of a
matching heuristic drift and the wrong one is the one that stays quiet. The no-AWS source table
it gave you is now `python run.py sweep --dry-run`.

---

## The two ends: an email out, an upload in

Everything above is a closed loop — the vault knows, and nobody is told. These two files
open it at both ends.

### 8. `ante/notify.py` — how the founder actually hears about it

`python run.py brief` sweeps the law, reads the roster, and emails the founder **only if
something is new**. stdlib `smtplib`, no email dependency.

Three decisions worth knowing:

- **The email body is the terminal output.** `advisor.render()` builds the string,
  `advisor.show()` prints it, `notify.brief()` mails it. One layout, two destinations — a
  finding cannot reach the screen without also reaching the inbox.
- **The fingerprint is over the findings, never the body.** The advisor's prose contains
  today's date and is regenerated every run, so hashing the text would mail an identical
  brief every morning, which is how a compliance alert becomes a filter rule. The hash
  covers `(rule, who, urgency bucket, dollar impact)`. The bucket is in there so a deadline
  standing still while today moves past it still counts as news.
- **No SMTP configured, or SMTP throws → the brief is written to
  `vault/alerts/outbox/`.** Conference wifi does not get to eat the demo.

It also degrades rather than failing: this account's SSO tokens die every ~12h, and
`vault.exposure()` needs no AWS at all, so an expired token downgrades the morning email to
figures-only instead of silence. Every number in it still came off a government page; only
the commentary is missing.

```
FOUNDER_EMAIL=you@example.com     # env/.env, overrides founder_email in the vault
SMTP_HOST=smtp.gmail.com          # blank -> outbox
SMTP_USER=you@example.com
SMTP_PASS=<16-char app password>  # Gmail: needs 2FA on. NOT your account password.
```

Daily, unattended:

```
schtasks /Create /TN "Ante daily brief" /SC DAILY /ST 07:00 ^
  /TR "<repo>\.venv\Scripts\python.exe <repo>\run.py brief"
```

### 9. `ante/ingest.py` — onboarding, without writing YAML twelve times

A payroll CSV plus four answers becomes a valid vault. Column mapping is a **deterministic
alias table, never a model** — same split the rest of the codebase rests on, because "which
column is the salary" is structural, not a judgement call. When a required column cannot be
identified it returns `needs_mapping` with the CSV's headers, so the UI offers a dropdown
instead of the code guessing.

The load-bearing detail is age banding: **five-year bands, not decades.** Decade bands put
a 57-year-old in `50-59`, `vault._band()` reads the lower bound as 50, and the
`employees_over_55` CPF rule silently stops matching them. A dropped finding is the one
failure this product cannot have.

Two things it refuses to guess, because both move money: an unrecognised pass type (wrong
class moves somebody in or out of a salary floor) and an unreadable financial year end
(wrong FYE moves the ACRA deadline by months). Both are reported against the row.

### 10. `ante/api.py` — the local server the UI talks to

Bound to `127.0.0.1`. The founder's payroll is parsed, written and queried **without
leaving the laptop**; the only thing crossing the wire is the text of a government page,
sent to Bedrock to ask whether a figure moved.

> **Your payroll never leaves your laptop. The only thing Ante uploads is the law.**

| Endpoint | Does | Cost |
|---|---|---|
| `GET /api/health` | vault valid? Bedrock alive? last sweep, overdue human reviews | free |
| `GET /api/findings` | `vault.exposure()` — the hero screen's data | **free, no AWS** |
| `GET /api/rules` | the rule base + `# Cost` / `# Next step` prose + freshness | **free, no AWS** |
| `GET /api/alerts` | what was raised, and what Ante **refused** to write | **free, no AWS** |
| `GET /api/history/{slug}` | every auto-applied change to one rule, for the diff | **free, no AWS** |
| `POST /api/rollback/{slug}` | the human's veto, one click | free |
| `POST /api/upload` | payroll CSV + four answers → a validated vault | free |
| `POST /api/ask` | the advisor, for the chat sidebar | ~17k in / 2k out |
| `POST /api/brief` | sweep + advise + email | ~17k in / 2k out |
| `GET /docs` | FastAPI's interactive explorer | free |

Everything the board needs is in the free half, so it renders before the advisor has
finished thinking — and still renders when the SSO token has expired. `/api/ask` answers
**503**, not 500, when credentials are dead: the board is still correct, and the UI says
"commentary unavailable" rather than "Ante is down".

`{slug}` is matched against the rule base rather than sanitised. It reaches `ante.apply`,
which joins it onto a filesystem path, and an allowlist cannot be talked past the way a
filter can.

`python -m ante.api` self-checks every free route in-process, including the refusals.

**One process serves one vault.** `vault.VAULT` resolves at import from `ANTE_VAULT`, so
switching founders means restarting the server. That is correct for a local single-founder
tool, and it is what protects the shipped demo vault: an upload refuses a vault that
already has a roster.

```bash
ANTE_VAULT=./vaults/acme python run.py serve
```

`vaults/` is gitignored, so a real payroll cannot reach a commit.

---

### 11. `web/index.html` — the Runway Board

```bash
python run.py serve      # then open http://127.0.0.1:8000/
```

One file. No build step, no npm, no framework, no CDN — three ways of saying the same
thing: it is served off `127.0.0.1`, it has to render with the network unplugged, and a
demo that needs `npm install` to start is a demo that does not start. Dropping it in
`web/` also means it is served from the same origin as the API, so CORS never enters the
room.

It boots on the four **free** endpoints in parallel and paints before asking anything of
AWS. `/api/health` is fetched *after* the first paint, because it makes an STS call that
is slow exactly when credentials are dead — the board must not wait on the one thing
that might be broken.

What is on it:

| | |
|---|---|
| **Three columns** | Law moved · You grew · Date arriving, straight off each rule's `clock`. Founders don't want a compliance list, they want the bill |
| **The bill** | biggest text on the page — see "what the number is" below |
| **18-month strip** | today as a red line, everything left of it on red ground, because *already in force and unbudgeted* is the expensive category a calendar structurally cannot show. Rules landing on the same day are grouped (`+117d ×3`) rather than drawn on top of each other — three clocks converge on 1 Jan 2027 and the strip should show that |
| **The human's prose** | expand a card and the `# Cost` and `# Next step` sections appear *as written*, escaped and quoted, never paraphrased |
| **Refusals tray** | fed from `alerts/` where `status: needs_human_check`. Most demos hide failure; a bot that declines to write an unverified figure is more convincing than one that is never wrong |
| **Chat sidebar** | `POST /api/ask`, with Loop Discipline printed under the answer — the metric as a visible trust signal. The advisor is the drill-down, never the front door |
| **Freshness** | human vs machine, side by side, amber past 90 days. It turns the `verified` / `checked` / `confirmed` distinction from an internal nicety into a feature |
| **Copy to your accountant** | the whole finding as pasteable text with its source — founders forward, they don't share dashboards |
| **Empty state** | "Nothing bites" plus the last sweep line, because a quiet day should read as proof of work rather than absence |

**What the big number is, exactly.** `S$4,800/yr` is the *provable* half: the annualised
distance between a salary and the floor it must clear, for the two rules where that is
arithmetic over two figures the vault already holds. `vault._gap()` computes it and
`vault.py`'s self-check asserts it **against the human-written `# Cost` prose** — both
notes read "S$2,400 a year", and Python has to agree to the dollar.

The other four findings deliberately have no number on the card. An employer CPF rate and
a late-filing penalty tier are not in the vault's frontmatter, so deriving them here would
be inventing a figure — the one thing this codebase does not do. Those cards say *"priced
in this rule's cost note"* and show the note. The header says how many, so the total is
never mistaken for the whole bill.

**The prose is quoted, not rendered.** Everything is escaped first and only `**bold**` and
pipe tables are promoted back — a rule note can never inject markup into the page quoting
it. Blank lines separate paragraphs; single newlines are the author's 90-column git-diff
wrapping and get reflowed, so cards do not show ragged mid-sentence breaks.

### What is still only an idea

Ranked by demo impact per hour of work:

- **The visible diff.** `GET /api/history/{slug}` and `POST /api/rollback/{slug}` are live
  and self-checked; what is missing is the panel that draws "CPF ceiling **S$7,400 →
  S$8,000**" with the old and new page regions side by side, the matched quote highlighted,
  which of the five gates passed, and a one-click **Undo** on it. The endpoints return
  everything it needs. Watching a machine change a regulation and then watching a human veto
  it in one click *is* the pitch, and it is still the best thirty seconds in the demo.
- **The upload form.** `ante/ingest.py` and `POST /api/upload` are done, so onboarding
  works from the CLI; the form itself and the dropdown that resolves a `needs_mapping`
  response are not built. Nobody fills in twelve staff notes by hand — four questions plus
  a payroll CSV should reach the first real alert in under two minutes.
- **Counterparties.** `_SCHEMA.md` defines the type and `vault.py` makes the folder
  agent-writable, but nothing writes to it yet. Watching an ACRA status flip to `Struck Off`
  the week it happens is a whole feature, not a polish pass.

### Skip these for now
Push notifications, multi-tenant login, a settings page. None of them are the demo. (Dark
mode came free with `prefers-color-scheme` and is not worth a line of anyone's time.)

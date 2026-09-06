"""Ante's second brain: read and write the OKF markdown vault.

Four tools the agent calls, plus a validator that fails loudly when the vault
and this file drift apart. No LangChain in here on purpose -- run it standalone
before wiring it into agent.py.

    python vault.py
"""
import calendar
import datetime as dt
import os
import pathlib
import re
import sys

import yaml

#: ANTE_VAULT lets one install serve one founder per vault, and keeps an upload
#: from ever landing in the synthetic demo vault whose findings are asserted below.
VAULT = pathlib.Path(
    os.environ.get("ANTE_VAULT") or pathlib.Path(__file__).parent / "vault").resolve()
GOV = re.compile(r"^https://[\w.-]*\.gov\.sg/", re.I)
TODAY = dt.date.today()

RULE_REQUIRED = ("clock", "effective", "applies_to", "trigger_field",
                 "threshold_after", "unit", "severity", "resource", "verified",
                 "bites")
CLOCKS = {"law", "growth", "recurring"}
UNITS = {"SGD_per_month", "SGD_per_year", "percent", "months"}
SEVERITIES = {"high", "medium", "low"}

#: Which side of its threshold a rule bites on. Stated by the rule rather than
#: re-derived from its prose, because "is this person non-compliant" is
#: arithmetic and must not vary between runs.
BITES = {
    "below":    lambda v, fm: v is not None and v < fm["threshold_after"],
    "above":    lambda v, fm: v is not None and v > fm["threshold_before"],
    "band":     lambda v, fm: True,          # a rate change hits the whole band
    "forecast": lambda v, fm: v is not None and v >= 0.8 * fm["threshold_after"],
    "always":   lambda v, fm: True,          # a filing deadline needs no test
}
PASS_TYPES = {"citizen", "pr", "s_pass", "ep", "entrepass"}
SECTIONS = ("# What changes", "# Who it hits", "# Cost", "# Next step", "# Citations")
WRITABLE = ("alerts/", "counterparties/")

#: Units whose shortfall is money rather than a rate or a duration.
MONEY = {"SGD_per_month", "SGD_per_year"}


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------

def _resolve(rel):
    """Vault-relative path -> absolute, or None if it escapes the vault."""
    p = (VAULT / str(rel).replace("\\", "/")).resolve()
    try:
        p.relative_to(VAULT)
    except ValueError:
        return None
    return p


def _split(path):
    """-> (frontmatter dict, body str). Empty dict if the note has none."""
    text = pathlib.Path(path).read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}, text.strip()
    _, fm, body = text.split("---", 2)
    return yaml.safe_load(fm) or {}, body.strip()


def _notes(folder):
    """Every content note in a folder. index.md and _SCHEMA.md are navigation."""
    for p in sorted((VAULT / folder).glob("*.md")):
        if p.stem not in ("index", "_SCHEMA"):
            yield p


def _rel(p):
    return p.relative_to(VAULT).as_posix()


def _band(fm, on=None):
    """Lower bound of an age_band like '55-59' or '65+', as at date `on`.

    A band is a snapshot of an age, and an age moves. A rule that takes effect
    on 1 Jan 2027 bites whoever is in scope *then*, not whoever was in scope the
    day their payroll was uploaded -- so someone banded '50-54' today who turns
    55 in three weeks is in scope for a 2027 rule, and reporting them only from
    their birthday onwards would drop the finding for exactly as long as it is
    still cheap to act on. The CPF senior-worker note says so itself: "including
    anyone crossing 55 during 2026".

    `band_changes_on` is the date they enter the next band, written by ingest
    when the payroll gave a real date of birth. Every band after that is another
    five years on. Without it -- a CSV that gave a bare age, or a hand-written
    note -- there is nothing to project from, so the band stands as recorded.
    """
    raw = str(fm.get("age_band", "")).split("-")[0].rstrip("+")
    band = int(raw) if raw.isdigit() else 0
    changes = fm.get("band_changes_on")
    if not (band and on and changes):
        return band
    if isinstance(changes, str):
        try:
            changes = dt.date.fromisoformat(changes)
        except ValueError:
            return band
    # one step per five years elapsed since that crossing
    while changes <= on:
        band += 5
        # 29 Feb + 5 years is not a date; the 28th is the same birthday in law
        try:
            changes = changes.replace(year=changes.year + 5)
        except ValueError:
            changes = changes.replace(month=2, day=28, year=changes.year + 5)
    return band


def _company():
    return _split(VAULT / "company" / "profile.md")[0]


def section(body: str, heading: str) -> str:
    """One '# Heading' section of a note body, heading line stripped.

    The rule notes' prose is human-owned and the UI quotes it rather than
    paraphrasing -- '# Cost' priced by a person and '# Next step' written by one
    are the two the founder actually acts on. Empty string when absent, because a
    missing section is a rendering gap, not a reason to fail a request.
    """
    out, taking = [], False
    for line in (body or "").splitlines():
        if line.startswith("# "):
            taking = line.strip() == heading
            continue
        if taking:
            out.append(line)
    return "\n".join(out).strip()


def _gap(value, fm) -> float | None:
    """Annualised shortfall against a floor, or None when it is not arithmetic.

    Only `bites: below` on a money unit qualifies: the distance between a salary
    and the floor it must clear, times twelve, is derived from two figures the
    vault already holds and nothing else. The rules' own '# Cost' sections agree
    with it to the dollar -- S Pass and EP both read "S$2,400 a year".

    Everything else is deliberately None. An employer CPF rate or a late-filing
    penalty tier is not in the vault's frontmatter, and re-deriving one here
    would be inventing a figure -- the one thing this codebase does not do. Those
    rules carry their price in the human-written '# Cost' prose instead, and the
    UI quotes that.
    """
    if fm.get("bites") != "below" or fm.get("unit") not in MONEY:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    short = fm["threshold_after"] - value
    if short <= 0:
        return None
    return round(short * (12 if fm["unit"] == "SGD_per_month" else 1), 2)


def _fye():
    """(month, day) of the financial year end.

    Tolerates a full YYYY-MM-DD as well as MM-DD -- YAML turns an unquoted
    2026-12-31 into a date object, and a founder typing a whole date into an
    onboarding box is not a mistake worth losing every finding over. Anything
    genuinely unparseable raises here, where validate() reports it, rather than
    silently emptying upcoming() at query time.
    """
    raw = _company().get("financial_year_end")
    parts = str(raw).split("-")
    if len(parts) < 2 or not all(p.strip().isdigit() for p in parts[-2:]):
        raise ValueError(f"financial_year_end must be MM-DD, got {raw!r}")
    return int(parts[-2]), int(parts[-1])


# --------------------------------------------------------------------------
# the four tools
# --------------------------------------------------------------------------

def vault_search(query: str) -> dict:
    """Find rules relevant to a question.

    Returns each match's identity and summary only -- never the full note. Call
    vault_read on the paths that look relevant.
    """
    words = {w for w in re.findall(r"[a-z0-9]+", query.lower()) if len(w) > 2}
    hits = []
    for p in _notes("rules"):
        fm, body = _split(p)
        hay = " ".join([
            str(fm.get("title", "")), str(fm.get("description", "")),
            " ".join(fm.get("tags") or []), str(fm.get("applies_to", "")), body,
        ]).lower()
        score = sum(1 for w in words if w in hay)
        if score:
            hits.append((score, {
                "path": _rel(p),
                "title": fm.get("title"),
                "clock": fm.get("clock"),
                "effective": str(fm.get("effective")),
                "applies_to": fm.get("applies_to"),
                "severity": fm.get("severity"),
            }))
    hits.sort(key=lambda h: -h[0])
    return {"matches": len(hits), "rules": [h[1] for h in hits]}


def vault_read(path: str) -> dict:
    """Read one note in full. Path is relative to the vault root."""
    p = _resolve(path)
    if p is None or not p.is_file():
        return {"error": f"no note at {path}"}
    fm, body = _split(p)
    return {"path": _rel(p), "frontmatter": fm, "body": body}


def roster_scan(applies_to: str, on=None) -> dict:
    """Who does a rule's applies_to actually hit?

    Pass the applies_to value straight from a rule's frontmatter. Returns the
    matching people with their salary and pass details, or the company profile
    when the rule applies to the company rather than to staff.

    `on` is the date to judge them at -- the day the rule bites, which upcoming()
    supplies. It only changes anything for the age-banded categories, where a
    rule landing next year hits whoever will be old enough by then. It defaults
    to today, so a bare tool call from the model still asks the obvious
    question.
    """
    if applies_to == "company":
        fm = _company()
        return {"applies_to": applies_to, "matched": 1, "company": fm}
    test = MATCH.get(applies_to)
    if test is None:
        return {"error": f"unknown applies_to '{applies_to}'; "
                         f"expected one of {sorted(MATCH) + ['company']}"}
    on = on or TODAY
    people = []
    for p in _notes("people"):
        fm, _ = _split(p)
        if test(fm, on):
            people.append({"path": _rel(p), **{k: fm.get(k) for k in (
                "title", "role", "pass_type", "monthly_salary",
                "pass_expiry", "age_band")},
                "age_band_on": f"{_band(fm, on)}-{_band(fm, on) + 4}"})
    return {"applies_to": applies_to, "matched": len(people), "people": people,
            "as_at": str(on)}


MATCH = {
    "s_pass_holder":     lambda fm, on: fm.get("pass_type") == "s_pass",
    "ep_holder":         lambda fm, on: fm.get("pass_type") == "ep",
    "entrepass_holder":  lambda fm, on: fm.get("pass_type") == "entrepass",
    "all_employees":     lambda fm, on: True,
    "employees_over_55": lambda fm, on: _band(fm, on) >= 55,
    "employees_over_60": lambda fm, on: _band(fm, on) >= 60,
}


def vault_write(path: str, frontmatter: dict, body: str) -> dict:
    """Record an alert or a counterparty. Only alerts/ and counterparties/."""
    p = _resolve(path)
    if p is None:
        return {"error": f"path escapes the vault: {path}"}
    # check the prefix on the RESOLVED path -- "alerts/../rules/x.md" starts with
    # "alerts/" as a string but lands in the human-owned rule base
    rel = _rel(p)
    if not rel.startswith(WRITABLE):
        return {"error": f"agent may only write to {' or '.join(WRITABLE)}; "
                         f"'{rel}' is human-owned"}
    if not frontmatter.get("type"):
        return {"error": "every note needs a non-empty 'type' (OKF requirement)"}
    p.parent.mkdir(parents=True, exist_ok=True)
    fm = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).strip()
    p.write_text(f"---\n{fm}\n---\n\n{body.strip()}\n", encoding="utf-8")
    return {"written": _rel(p)}


# --------------------------------------------------------------------------
# validator -- the integration guard
# --------------------------------------------------------------------------

def validate(strict: bool = True) -> list:
    """Every problem found, as a list of strings. Empty list = vault is sound.

    strict=False for a vault built from a real upload. The only difference is the
    "this rule matches nobody" check: in the curated demo vault a dead rule is a
    curation bug, but in a founder's own vault it means they have no S Pass
    holders and nobody over 55 -- which is compliance, not corruption. Every
    other check, including an applies_to outside the contract, still applies.
    """
    problems = []
    seen_applies_to = set()

    for p in _notes("rules"):
        fm, body = _split(p)
        where = _rel(p)
        if fm.get("type") != "rule":
            problems.append(f"{where}: type is {fm.get('type')!r}, expected 'rule'")
        for key in RULE_REQUIRED:
            if key not in fm:
                problems.append(f"{where}: missing required field '{key}'")
        if fm.get("clock") not in CLOCKS:
            problems.append(f"{where}: clock {fm.get('clock')!r} not in {sorted(CLOCKS)}")
        if fm.get("unit") not in UNITS:
            problems.append(f"{where}: unit {fm.get('unit')!r} not in {sorted(UNITS)}")
        if fm.get("bites") not in BITES:
            problems.append(f"{where}: bites {fm.get('bites')!r} not in {sorted(BITES)}")
        if fm.get("bites") == "above" and "threshold_before" not in fm:
            problems.append(f"{where}: bites 'above' needs threshold_before to compare to")
        if fm.get("severity") not in SEVERITIES:
            problems.append(f"{where}: severity {fm.get('severity')!r} not in {sorted(SEVERITIES)}")
        if not GOV.match(str(fm.get("resource", ""))):
            problems.append(f"{where}: resource is not a primary .gov.sg URL")
        for section in SECTIONS:
            if section not in body:
                problems.append(f"{where}: body is missing '{section}'")
        seen_applies_to.add(fm.get("applies_to"))

    for p in _notes("people"):
        fm, _ = _split(p)
        where = _rel(p)
        if fm.get("type") != "person":
            problems.append(f"{where}: type is {fm.get('type')!r}, expected 'person'")
        if fm.get("pass_type") not in PASS_TYPES:
            problems.append(f"{where}: pass_type {fm.get('pass_type')!r} not in {sorted(PASS_TYPES)}")
        if not _band(fm):
            problems.append(f"{where}: age_band {fm.get('age_band')!r} does not parse")

    company = _company()
    for key in ("uen", "financial_year_end", "headcount", "annual_revenue_run_rate"):
        if key not in company:
            problems.append(f"company/profile.md: missing '{key}'")
    try:
        # a bad FYE takes out every recurring rule, so it is caught here rather
        # than as an empty findings list nobody can explain
        _fye()
    except ValueError as exc:
        problems.append(f"company/profile.md: {exc}")

    # the join has to actually join -- a rule nobody matches is a dead rule
    for value in sorted(seen_applies_to, key=str):
        hit = roster_scan(value)
        if "error" in hit:
            problems.append(f"applies_to {value!r}: {hit['error']}")
        elif hit["matched"] == 0 and strict:
            problems.append(f"applies_to {value!r} matches nobody in people/")

    return problems


# --------------------------------------------------------------------------
# what lands, and when
# --------------------------------------------------------------------------

def _add_months(d, n):
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    return dt.date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def _lands_on(fm):
    """The date a rule bites, or None if it is a standing threshold."""
    if fm.get("clock") == "law":
        return fm.get("effective")
    if fm.get("clock") == "recurring":
        month, day = _fye()
        for year in range(TODAY.year - 1, TODAY.year + 3):
            due = _add_months(dt.date(year, month, day), int(fm["threshold_after"]))
            if due >= TODAY:
                return due
    return None


def upcoming(horizon_days=500, lookback_days=400) -> list:
    """Rules landing inside the window, with who they touch and their numbers.

    The window reaches backwards as well as forwards: a rule that took effect
    months ago and was never adjusted for is the expensive kind, not the
    irrelevant kind. Negative days_until means already in force.

    Deliberately reports the comparison rather than a verdict -- deciding
    whether a number is on the wrong side of a threshold is the model's job,
    not this file's.
    """
    out = []
    for p in _notes("rules"):
        fm, _ = _split(p)
        lands = _lands_on(fm)
        if lands and not (-lookback_days <= (lands - TODAY).days <= horizon_days):
            continue
        # judged on the day it bites: a 2027 rule hits whoever is in scope in
        # 2027. A rule already in force is judged today, not retrospectively --
        # nobody gets to age backwards out of an obligation.
        hit = roster_scan(fm["applies_to"], on=max(lands, TODAY) if lands else TODAY)
        subjects = hit.get("people") or ([hit["company"]] if "company" in hit else [])
        out.append({
            "path": _rel(p),
            "title": fm.get("title"),
            "clock": fm.get("clock"),
            "lands": lands,
            "days_until": (lands - TODAY).days if lands else None,
            "in_force": bool(lands) and lands <= TODAY,
            "severity": fm.get("severity"),
            "trigger_field": fm.get("trigger_field"),
            "threshold_after": fm.get("threshold_after"),
            "unit": fm.get("unit"),
            "resource": fm.get("resource"),
            # age_band_on rides along where it differs from the band recorded
            # today, so a card can say WHY someone banded 50-54 is in scope for
            # a rule about the over-55s instead of looking like a false positive
            "subjects": [{"who": s.get("title"),
                          "value": s.get(fm["trigger_field"]),
                          **({"age_band_on": s["age_band_on"]}
                             if s.get("age_band_on") not in (None, s.get("age_band"))
                             else {})}
                         for s in subjects],
        })
    out.sort(key=lambda r: (r["days_until"] is None, r["days_until"] or 0))
    return out


def exposure() -> dict:
    """Who is actually on the wrong side of each rule. The join, done in Python.

    `upcoming()` reports every subject a rule could touch; this applies the
    rule's own `bites` test and keeps only those it really touches. Deciding
    non-compliance is arithmetic, so it is done here rather than by a model that
    has to re-derive it from prose on every run -- the same split the curator
    uses, where the model describes and Python decides.

    Each affected party carries `annual_gap` where the shortfall is arithmetic
    (see _gap) and None where it is not, so a caller can total what is provably
    known and quote the rule's '# Cost' prose for the rest.
    """
    out, total = [], 0.0
    for rule in upcoming():
        fm, _ = _split(VAULT / rule["path"])
        test = BITES[fm["bites"]]
        hit = [{**s, "annual_gap": _gap(s["value"], fm)}
               for s in rule["subjects"] if test(s["value"], fm)]
        if hit:
            total += sum(a["annual_gap"] or 0 for a in hit)
            out.append({**{k: rule[k] for k in
                           ("path", "title", "clock", "lands", "days_until",
                            "in_force", "severity", "trigger_field",
                            "threshold_after", "unit", "resource")},
                        "bites": fm["bites"],
                        "threshold_before": fm.get("threshold_before"),
                        "affected": hit})
    # only the provable half: rules priced in prose are not in this number, and
    # the UI says so rather than presenting it as the whole bill
    return {"rules_with_exposure": len(out), "rules": out,
            "annual_gap_total": round(total, 2)}


# --------------------------------------------------------------------------

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")

    problems = validate()
    for problem in problems:
        print("  FAIL", problem)
    assert not problems, f"{len(problems)} vault problem(s)"
    print(f"vault ok  ->  {len(list(_notes('rules')))} rules, "
          f"{len(list(_notes('people')))} people, 1 company")

    assert vault_search("s pass foreign worker salary")["matches"] >= 1
    assert vault_read("rules/gst-registration-threshold.md")["frontmatter"]["threshold_after"] == 1000000
    assert "error" in vault_read("../../.env"), "must not read outside the vault"
    assert roster_scan("s_pass_holder")["matched"] == 1
    assert roster_scan("employees_over_55")["matched"] == 1
    assert roster_scan("all_employees")["matched"] == 12
    assert "error" in roster_scan("s_pass"), "must reject vocabulary that is not the contract"
    assert "error" in vault_write("rules/hack.md", {"type": "rule"}, "x"), "must refuse human-owned folders"
    assert "error" in vault_write("alerts/../rules/hack.md", {"type": "alert"}, "x"), "must refuse traversal"
    assert "error" in vault_write("alerts/a.md", {}, "x"), "must refuse a note with no type"
    probe = {"type": "alert", "raised": str(TODAY), "rule": "rules/_selfcheck.md"}
    assert vault_write("alerts/_selfcheck.md", probe, "round-trip probe")["written"]
    assert vault_read("alerts/_selfcheck.md")["frontmatter"]["rule"] == "rules/_selfcheck.md"
    (VAULT / "alerts" / "_selfcheck.md").unlink()
    print("tools ok  ->  search, read, roster_scan, write\n")

    # the money path: who is non-compliant is arithmetic, so it is asserted.
    # Staff 10 is an EP holder already ABOVE the coming floor and Staff 03 is
    # under the old CPF ceiling -- both must stay out, or the join is too eager.
    exp = exposure()
    hit = {r["path"]: {a["who"] for a in r["affected"]} for r in exp["rules"]}
    assert exp["rules_with_exposure"] == 6, exp["rules_with_exposure"]
    assert hit["rules/s-pass-qualifying-salary-2027.md"] == {"Staff 04"}
    assert hit["rules/ep-qualifying-salary-2027.md"] == {"Staff 07"}, "Staff 10 complies"
    assert hit["rules/cpf-ow-ceiling-2026.md"] == {"Staff 01"}, "Staff 03 is under it"
    assert hit["rules/cpf-senior-worker-rates-2027.md"] == {"Staff 02"}
    assert all(len(v) == 1 for v in hit.values()), hit
    print(f"exposure ok  ->  {exp['rules_with_exposure']} rules bite, "
          f"{sum(len(v) for v in hit.values())} parties, no compliant party included")

    # the gap is arithmetic, so it is asserted against the human "# Cost" prose:
    # both notes read "S$2,400 a year", and Python must agree to the dollar
    gaps = {r["path"]: [a["annual_gap"] for a in r["affected"]] for r in exp["rules"]}
    assert gaps["rules/s-pass-qualifying-salary-2027.md"] == [2400.0], gaps
    assert gaps["rules/ep-qualifying-salary-2027.md"] == [2400.0], gaps
    # priced in prose, never re-derived here: an employer CPF rate and a
    # penalty tier are not vault frontmatter, so these must stay None
    assert gaps["rules/cpf-ow-ceiling-2026.md"] == [None], gaps
    assert gaps["rules/cpf-senior-worker-rates-2027.md"] == [None], gaps
    assert gaps["rules/acra-annual-return.md"] == [None], gaps
    assert exp["annual_gap_total"] == 4800.0, exp["annual_gap_total"]

    # a band is a snapshot; rules bite in the future. Someone 54 today who turns
    # 55 on 2026-11-14 is in scope for a rule effective 2027-01-01, and each
    # further five years moves them another band.
    crossing = {"age_band": "50-54", "band_changes_on": "2026-11-14"}
    assert _band(crossing) == 50, "with no date, the band stands as recorded"
    assert _band(crossing, dt.date(2026, 11, 13)) == 50, "not until the birthday"
    assert _band(crossing, dt.date(2026, 11, 14)) == 55, "on the day it changes"
    assert _band(crossing, dt.date(2027, 1, 1)) == 55, "the rule's effective date"
    assert _band(crossing, dt.date(2031, 11, 14)) == 60, "five years on again"
    assert _band({"age_band": "50-54"}, dt.date(2099, 1, 1)) == 50, \
        "no band_changes_on means nothing to project from -- never guess"
    assert MATCH["employees_over_55"](crossing, dt.date(2027, 1, 1)), \
        "the CPF senior rule must see whoever will be 55 when it bites"
    assert not MATCH["employees_over_55"](crossing, TODAY), \
        "and must not claim they are 55 today"
    print("age ok       ->  bands projected to the date a rule bites, "
          "never past a date the vault does not have")

    body = vault_read("rules/s-pass-qualifying-salary-2027.md")["body"]
    assert section(body, "# Next step").startswith("List every S Pass holder")
    assert "# Cost" not in section(body, "# Cost"), "heading line must be stripped"
    assert section(body, "# Nonexistent") == "", "a missing section is empty, not fatal"
    print(f"gap ok       ->  S${exp['annual_gap_total']:,.0f}/yr provable from two "
          f"vault figures, 3 rules left to their own '# Cost' prose")

    print(f"what lands within 500 days of {TODAY}:")
    for rule in upcoming():
        if not rule["lands"]:
            when = "standing threshold"
        elif rule["in_force"]:
            when = f"IN FORCE since {rule['lands']} ({-rule['days_until']}d ago)"
        else:
            when = f"{rule['lands']} (in {rule['days_until']}d)"
        print(f"\n  {rule['title']}")
        print(f"    {when} · {rule['severity']} · {rule['clock']} clock")
        print(f"    {rule['trigger_field']} vs {rule['threshold_after']} {rule['unit']}")
        for s in rule["subjects"]:
            print(f"      - {s['who']}: {s['value']}")

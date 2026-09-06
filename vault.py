"""Ante's second brain: read and write the OKF markdown vault.

Four tools the agent calls, plus a validator that fails loudly when the vault
and this file drift apart. No LangChain in here on purpose -- run it standalone
before wiring it into agent.py.

    python vault.py
"""
import calendar
import datetime as dt
import pathlib
import re
import sys

import yaml

VAULT = (pathlib.Path(__file__).parent / "vault").resolve()
GOV = re.compile(r"^https://[\w.-]*\.gov\.sg/", re.I)
TODAY = dt.date.today()

RULE_REQUIRED = ("clock", "effective", "applies_to", "trigger_field",
                 "threshold_after", "unit", "severity", "resource", "verified")
CLOCKS = {"law", "growth", "recurring"}
UNITS = {"SGD_per_month", "SGD_per_year", "percent", "months"}
SEVERITIES = {"high", "medium", "low"}
PASS_TYPES = {"citizen", "pr", "s_pass", "ep", "entrepass"}
SECTIONS = ("# What changes", "# Who it hits", "# Cost", "# Next step", "# Citations")
WRITABLE = ("alerts/", "counterparties/")


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


def _band(fm):
    """Lower bound of an age_band like '55-59' or '65+'. 0 if absent."""
    raw = str(fm.get("age_band", "")).split("-")[0].rstrip("+")
    return int(raw) if raw.isdigit() else 0


def _company():
    return _split(VAULT / "company" / "profile.md")[0]


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


def roster_scan(applies_to: str) -> dict:
    """Who does a rule's applies_to actually hit?

    Pass the applies_to value straight from a rule's frontmatter. Returns the
    matching people with their salary and pass details, or the company profile
    when the rule applies to the company rather than to staff.
    """
    if applies_to == "company":
        fm = _company()
        return {"applies_to": applies_to, "matched": 1, "company": fm}
    test = MATCH.get(applies_to)
    if test is None:
        return {"error": f"unknown applies_to '{applies_to}'; "
                         f"expected one of {sorted(MATCH) + ['company']}"}
    people = []
    for p in _notes("people"):
        fm, _ = _split(p)
        if test(fm):
            people.append({"path": _rel(p), **{k: fm.get(k) for k in (
                "title", "role", "pass_type", "monthly_salary",
                "pass_expiry", "age_band")}})
    return {"applies_to": applies_to, "matched": len(people), "people": people}


MATCH = {
    "s_pass_holder":     lambda fm: fm.get("pass_type") == "s_pass",
    "ep_holder":         lambda fm: fm.get("pass_type") == "ep",
    "entrepass_holder":  lambda fm: fm.get("pass_type") == "entrepass",
    "all_employees":     lambda fm: True,
    "employees_over_55": lambda fm: _band(fm) >= 55,
    "employees_over_60": lambda fm: _band(fm) >= 60,
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

def validate() -> list:
    """Every problem found, as a list of strings. Empty list = vault is sound."""
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

    # the join has to actually join -- a rule nobody matches is a dead rule
    for value in sorted(seen_applies_to, key=str):
        hit = roster_scan(value)
        if "error" in hit:
            problems.append(f"applies_to {value!r}: {hit['error']}")
        elif hit["matched"] == 0:
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
        month, day = (int(x) for x in str(_company()["financial_year_end"]).split("-"))
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
        hit = roster_scan(fm["applies_to"])
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
            "subjects": [{"who": s.get("title"),
                          "value": s.get(fm["trigger_field"])} for s in subjects],
        })
    out.sort(key=lambda r: (r["days_until"] is None, r["days_until"] or 0))
    return out


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

"""Onboarding: a payroll CSV plus four answers become a vault.

Ingest writes to `vault.VAULT`, which `ANTE_VAULT` chooses -- so a founder's
real payroll lands in their own directory and the shipped synthetic vault is
never the default target. It also refuses a vault that already has a roster
unless told to replace it, which is what protects the demo data in practice.

Column mapping is a deterministic alias table, never a model. The rest of this
codebase rests on the same split -- the model explains, Python decides -- and
"which column is the salary" is structural, not a judgement call. When the table
cannot find a required field it says so and hands back the headers, so the UI can
offer a dropdown instead of the code guessing.

    python -m ante.ingest        self-check, builds a throwaway vault in a temp dir
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

import yaml

from vault import PASS_TYPES, VAULT, _notes, _split, exposure, validate

SHIPPED = pathlib.Path(__file__).resolve().parent.parent / "vault"
REQUIRED = ("title", "pass_type", "monthly_salary", "age_band")

#: field -> header aliases. Order matters for the substring pass: `pass_expiry`
#: is tried before `pass_type` so "Work Pass Expiry" is not eaten by "work pass".
COLUMNS = {
    "title":          ("name", "employee", "employee name", "staff", "full name"),
    "role":           ("role", "job title", "title", "position", "designation"),
    "monthly_salary": ("monthly salary", "salary", "gross salary", "basic pay",
                       "gross monthly pay", "gross", "pay"),
    "age_band":       ("age band", "age", "date of birth", "dob", "birthdate"),
    "pass_expiry":    ("pass expiry", "work pass expiry", "expiry"),
    "pass_type":      ("pass type", "work pass", "pass", "citizenship",
                       "residency", "visa", "nationality"),
}

PASS_MAP = {
    "citizen": "citizen", "sc": "citizen", "singaporean": "citizen",
    "singapore citizen": "citizen", "local": "citizen",
    "pr": "pr", "spr": "pr", "permanent resident": "pr",
    "singapore pr": "pr", "singapore permanent resident": "pr",
    "s pass": "s_pass", "spass": "s_pass", "s_pass": "s_pass",
    "ep": "ep", "employment pass": "ep", "e pass": "ep",
    "entrepass": "entrepass", "entre pass": "entrepass",
}

ANNUAL = ("annual", "yearly", "per year", "per annum")


# --------------------------------------------------------------------------
# parsing one row
# --------------------------------------------------------------------------

def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()


def _round(n):
    """Whole dollars stay ints. A web form sends 450000.0; the vault should not
    read back as though the figure carried cents it never had."""
    return int(n) if isinstance(n, (int, float)) and n == int(n) else n


def map_columns(headers: list, overrides: dict | None = None) -> tuple[dict, list]:
    """-> ({field: header}, [required fields we could not find]).

    Exact matches are taken across every field before any substring match is
    tried, so a header that is exactly one field's alias is never stolen by
    another field's looser one.
    """
    found = dict(overrides or {})
    taken = set(found.values())
    norms = {h: _norm(h) for h in headers if h}

    for exact in (True, False):
        for field, aliases in COLUMNS.items():
            if field in found:
                continue
            for header, n in norms.items():
                if header in taken:
                    continue
                if (n in aliases) if exact else any(a in n for a in aliases):
                    found[field] = header
                    taken.add(header)
                    break

    return found, [f for f in REQUIRED if f not in found]


def _pass_type(raw) -> str | None:
    """-> a PASS_TYPES value, or None when we do not recognise it.

    None is reported against its row rather than guessed: putting somebody in
    the wrong pass class silently moves them in or out of a salary floor.
    """
    n = _norm(raw)
    return PASS_MAP.get(n) or (n if n in PASS_TYPES else None)


def _salary(raw, header: str) -> float:
    """Monthly, in dollars. Divides an annual column by twelve."""
    digits = re.sub(r"[^\d.]", "", str(raw))
    n = float(digits) if digits else 0.0
    if any(w in _norm(header) for w in ANNUAL):
        n /= 12
    return int(n) if n == int(n) else round(n, 2)


def _born(raw) -> dt.date | None:
    """A date of birth out of whatever the payroll column held, or None."""
    s = str(raw).strip()[:10]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _age_band(raw) -> tuple[str, str | None] | None:
    """-> (band, band_changes_on) or None if unreadable.

    Five years, not ten, and this is load-bearing: decade bands put a
    57-year-old in '50-59', vault._band() reads the lower bound as 50, and the
    `employees_over_55` rule silently stops matching them. A dropped finding is
    the one failure this product cannot have.

    The band alone is not enough, for the same reason. It is a snapshot, and
    rules bite in the future: someone who is 54 today and 55 in three weeks is
    in scope for a rule effective next January, and banding them '50-54' forever
    drops that finding. So when the payroll gave a real date of birth we also
    record the day they enter the next band, and vault._band() projects forward
    from it. A bare age cannot support that -- we know they are 54, not when
    they turn 55 -- so it returns None and the band stands as recorded.
    """
    s = str(raw).strip()
    if re.fullmatch(r"\d{1,3}\s*-\s*\d{1,3}|\d{1,3}\s*\+", s):
        return re.sub(r"\s+", "", s), None                # already a band

    today = dt.date.today()
    born = _born(s)
    if born is not None:
        age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
    elif s.isdigit() and int(s) < 120:
        age, born = int(s), None
    else:
        return None

    if not 14 <= age < 100:
        return None
    band = "65+" if age >= 65 else f"{age // 5 * 5}-{age // 5 * 5 + 4}"
    if born is None:
        return band, None

    # the birthday on which they enter the next five-year band
    next_at = (age // 5 * 5) + 5
    try:
        changes = born.replace(year=born.year + next_at)
    except ValueError:                                    # born 29 February
        changes = born.replace(month=2, day=28, year=born.year + next_at)
    return band, str(changes)


def _expiry(raw):
    s = str(raw or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return dt.datetime.strptime(s[:10], fmt).date().isoformat()
        except ValueError:
            continue
    return None


def normalise_fye(raw) -> str:
    """-> 'MM-DD'. Refuses what it cannot read rather than guessing.

    A wrong financial year end moves the ACRA deadline by months, so anything
    outside the accepted shapes is an error the founder resolves. MM-DD is the
    contract, so a bare 03-04 is March 4 by definition; only a day-first form we
    can PROVE is day-first (31/12) gets swapped.
    """
    nums = [int(p) for p in re.split(r"[-/.\s]+", str(raw).strip()) if p.isdigit()]
    if len(nums) == 3:
        if nums[0] > 31:                                   # YYYY-MM-DD
            nums = nums[1:]
        elif nums[2] > 31:                                 # DD-MM-YYYY
            nums = [nums[1], nums[0]]
    if len(nums) == 2:
        month, day = nums
        if month > 12 and day <= 12:                       # DD-MM, unambiguous
            month, day = day, month
        if 1 <= month <= 12 and 1 <= day <= 31:
            return f"{month:02d}-{day:02d}"
    raise ValueError(f"financial_year_end must be MM-DD or YYYY-MM-DD, got {raw!r}")


# --------------------------------------------------------------------------
# the roster
# --------------------------------------------------------------------------

def read_people(csv_text: str, overrides: dict | None = None) -> dict:
    """CSV -> person frontmatter, or a request for the UI to map columns."""
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    if not rows:
        return {"error": "the CSV has no data rows"}

    cols, missing = map_columns(list(rows[0]), overrides)
    if missing:
        return {"needs_mapping": missing, "headers": list(rows[0]), "sample": rows[0]}

    people, problems = [], []
    for i, row in enumerate(rows, start=1):
        name = str(row.get(cols["title"], "")).strip()
        if not name:
            problems.append(f"row {i}: no name, skipped")
            continue
        pass_type = _pass_type(row.get(cols["pass_type"]))
        banded = _age_band(row.get(cols["age_band"]))
        if pass_type is None:
            problems.append(f"row {i} ({name}): pass type "
                            f"{row.get(cols['pass_type'])!r} not recognised")
        if banded is None:
            problems.append(f"row {i} ({name}): age/DOB "
                            f"{row.get(cols['age_band'])!r} not readable")
        if pass_type is None or banded is None:
            continue
        band, changes = banded
        # an age with no date of birth cannot be projected forward, so an
        # age-banded rule landing after their next birthday may miss them. Said
        # out loud rather than left as a silent gap.
        if changes is None:
            problems.append(f"row {i} ({name}): age given without a date of "
                            f"birth, so band '{band}' cannot be projected to a "
                            f"future rule's effective date")
        people.append({
            "type": "person",
            "title": name,
            "role": str(row.get(cols.get("role", ""), "")).strip() or "Employee",
            "pass_type": pass_type,
            "monthly_salary": _salary(row.get(cols["monthly_salary"]),
                                      cols["monthly_salary"]),
            "pass_expiry": _expiry(row.get(cols.get("pass_expiry", ""))),
            "age_band": band,
            **({"band_changes_on": changes} if changes else {}),
        })
    return {"people": people, "problems": problems, "columns": cols}


# --------------------------------------------------------------------------
# writing the vault
# --------------------------------------------------------------------------

def _write(path: pathlib.Path, fm: dict, body: str) -> None:
    """Same on-disk shape as vault.vault_write, which may not write here: that
    tool is the agent's and is fenced out of people/ and company/ on purpose.
    Ingest is onboarding, not the agent, the same way ante.apply is."""
    path.parent.mkdir(parents=True, exist_ok=True)
    head = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip()
    path.write_text(f"---\n{head}\n---\n\n{body.strip()}\n", encoding="utf-8")


def skeleton(target: pathlib.Path) -> None:
    """The pre-curated half of the vault: the rule base and the schema.

    The founder onboards their company, not the law -- rules/ is human-curated
    and ships with the product. The index notes are regenerated rather than
    copied, because the shipped ones describe Harborlight by name.
    """
    target.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SHIPPED / "rules", target / "rules", dirs_exist_ok=True)
    shutil.copy2(SHIPPED / "_SCHEMA.md", target / "_SCHEMA.md")
    for folder in ("people", "company", "alerts", "counterparties"):
        (target / folder).mkdir(exist_ok=True)
    if not (target / "log.md").is_file():
        _write(target / "log.md", {"type": "log", "title": "Run Log",
                                   "description": "Chronological history of agent runs."},
               "# Run Log\n\nAppended by the agent, newest last.")
    # the front door when the founder opens this folder in Obsidian -- the
    # "second brain" is only a second brain if it is navigable by hand
    _write(target / "index.md",
           {"type": "index", "title": "Ante Knowledge Vault",
            "description": "Your company's regulatory knowledge base."},
           "# Ante Knowledge Vault\n\n"
           "| Folder | Who writes it | What it holds |\n|---|---|---|\n"
           "| [rules/](rules/index.md) | human | The Singapore rule base, every "
           "figure cited to a primary `.gov.sg` page |\n"
           "| [company/](company/profile.md) | you | Your company's own facts |\n"
           "| [people/](people/index.md) | you | Your roster, imported from payroll |\n"
           "| alerts/ | **agent** | What Ante raised, and what it refused to do |\n"
           "| [log.md](log.md) | **agent** | Every run, including the quiet ones |\n\n"
           "Nothing in this folder leaves your machine.")


def build(company: dict, csv_text: str, overrides: dict | None = None,
          replace: bool = False) -> dict:
    """Payroll + company answers -> a validated vault at vault.VAULT."""
    if list(_notes("people")) and not replace:
        return {"error": f"{VAULT} already has a roster; pass replace=true to "
                         f"overwrite it (the shipped demo vault is protected "
                         f"this way -- set ANTE_VAULT to build elsewhere)"}

    roster = read_people(csv_text, overrides)
    if "people" not in roster:
        return roster                                      # needs_mapping / error
    if not roster["people"]:
        return {"error": "no usable rows", "problems": roster["problems"]}

    try:
        fye = normalise_fye(company.get("financial_year_end"))
    except ValueError as exc:
        return {"error": str(exc)}

    skeleton(VAULT)
    for old in _notes("people"):                           # a leaver must not linger
        old.unlink()

    today = str(dt.date.today())
    people = roster["people"]
    for i, person in enumerate(people, start=1):
        _write(VAULT / "people" / f"staff-{i:02d}.md", person,
               f"Imported from payroll on {today}.")

    _write(VAULT / "company" / "profile.md", {
        "type": "company",
        "title": company.get("company_name") or "Unnamed Pte Ltd",
        "uen": company.get("uen"),
        "financial_year_end": fye,
        # from the roster, never the typed field: two sources for one number is
        # two numbers, and this one moves thresholds
        "headcount": len(people),
        "annual_revenue_run_rate": _round(company.get("annual_revenue_run_rate")),
        "sector": company.get("sector") or "unspecified",
        "founder_email": company.get("founder_email"),
        "verified": today,
    }, f"# About\n\nOnboarded {today} from an uploaded payroll of {len(people)} staff.")

    rows = "\n".join(
        f"| [{i:02d}](staff-{i:02d}.md) | {p['title']} | {p['role']} | {p['pass_type']} "
        f"| {p['monthly_salary']} | {p['pass_expiry'] or '—'} | {p['age_band']} |"
        for i, p in enumerate(people, start=1))
    _write(VAULT / "people" / "index.md",
           {"type": "index", "title": "Roster",
            "description": f"{len(people)} staff, imported {today}."},
           "# Roster\n\n| # | Name | Role | Pass | Salary | Pass expiry | Age band |\n"
           "|---|---|---|---|---|---|---|\n" + rows)

    # the advisor bakes the company profile into a cached system prompt; without
    # this a long-lived server answers the next question about the old company
    try:
        from ante.advisor import graph
        graph.cache_clear()
    except ImportError:
        pass

    problems = validate(strict=False)
    return {"vault": str(VAULT), "people": len(people),
            "company": company.get("company_name"),
            "problems": roster["problems"] + problems,
            "findings": exposure()}


# --------------------------------------------------------------------------

CSV = """Employee Name,Job Title,Visa,Gross Monthly Pay,Date of Birth,Work Pass Expiry
Ada Tan,Co-founder and CEO,Singaporean,"$8,200",1990-03-14,
Ben Ong,Finance Lead,SC,"6,200",1969-07-02,
Chloe Lim,Co-founder and CTO,Singapore Citizen,7100,1988-11-30,
Dinesh Raj,Data Engineer,S Pass,"3,400",1999-01-22,2028-02-20
Elena Cruz,Customer Success,SPR,5200,1991-05-09,
Farid Hassan,Product Designer,citizen,5600,1997-08-18,
Grace Wong,Senior Backend Engineer,Employment Pass,"5,800",1989-02-11,2028-04-10
Hakim Yusof,Sales Lead,Singaporean,6800,1980-12-05,
Ivy Chen,Data Analyst,Permanent Resident,4600,1998-06-27,
Jonas Meier,Frontend Engineer,EP,"7,200",1987-09-03,2029-06-30
Kavya Nair,HR Coordinator,SC,4200,2000-04-16,
Lim Wei Jie,Junior Data Analyst,citizen,3900,2001-10-08,
Margaret Soh,Operations Manager,SC,6000,1971-11-14,
"""


def _selfcheck() -> None:
    out = build({"company_name": "Testbed Pte Ltd", "uen": "202500001A",
                 "financial_year_end": "2026-12-31", "annual_revenue_run_rate": 862000,
                 "founder_email": "founder@testbed.example"}, CSV)
    assert "error" not in out, out
    assert out["people"] == 13, out["people"]
    assert not out["problems"], out["problems"]

    fm = {p.stem: _split(p)[0] for p in _notes("people")}
    # Ben Ong, born 1969, is 57 today -- decade banding would file him under
    # 50-59 and the senior-worker CPF rule would stop seeing him
    ben = next(v for v in fm.values() if v["title"] == "Ben Ong")
    assert ben["age_band"] == "55-59", ben["age_band"]
    ada = next(v for v in fm.values() if v["title"] == "Ada Tan")
    assert ada["monthly_salary"] == 8200 and ada["pass_type"] == "citizen", ada
    din = next(v for v in fm.values() if v["title"] == "Dinesh Raj")
    assert din["pass_type"] == "s_pass" and din["pass_expiry"] == "2028-02-20", din

    hit = {r["path"]: {a["who"] for a in r["affected"]} for r in out["findings"]["rules"]}
    assert hit["rules/s-pass-qualifying-salary-2027.md"] == {"Dinesh Raj"}, hit
    assert hit["rules/ep-qualifying-salary-2027.md"] == {"Grace Wong"}, hit
    assert hit["rules/cpf-ow-ceiling-2026.md"] == {"Ada Tan"}, hit
    print(f"ingest ok  ->  13 staff from messy headers, vault validates, "
          f"{out['findings']['rules_with_exposure']} rules bite the right people")

    # Margaret Soh is 54 today and 55 well before the rule takes effect on
    # 2027-01-01. Banding her '50-54' forever is the dropped finding this
    # product cannot have -- the rule note says "including anyone crossing 55
    # during 2026" -- so she must be in scope now, while a pay review can still
    # budget for it, not from her birthday onwards.
    meg = next(v for v in fm.values() if v["title"] == "Margaret Soh")
    assert meg["age_band"] == "50-54", meg["age_band"]
    assert meg["band_changes_on"] == "2026-11-14", meg
    assert hit["rules/cpf-senior-worker-rates-2027.md"] == {"Ben Ong", "Margaret Soh"}, hit
    print(f"           age projected to the date each rule bites: Margaret Soh "
          f"is 54 today, 55 on {meg['band_changes_on']}, and in scope for the "
          f"2027 CPF rule")

    # a bare age carries no birthday, so it cannot be projected -- and that is
    # reported against the row rather than left as a silent gap
    aged = read_people("Name,Pass,Pay,Age\nBob Tan,SC,6000,54\n")
    assert aged["people"][0]["age_band"] == "50-54", aged
    assert "band_changes_on" not in aged["people"][0], aged
    assert any("cannot be projected" in p for p in aged["problems"]), aged["problems"]
    print("           a bare age is banded but flagged as unprojectable, not guessed")

    assert "error" in build({}, CSV), "must refuse to overwrite a populated vault"

    bad = read_people("Name,Pay\nAda,100\n")
    assert bad["needs_mapping"] == ["pass_type", "age_band"], bad
    assert bad["headers"] == ["Name", "Pay"], bad
    print(f"           unmappable columns reported, not guessed: {bad['needs_mapping']}")

    for raw in ("12-31", "2026-12-31", "31/12"):
        assert normalise_fye(raw) == "12-31", raw
    try:
        normalise_fye("31 December")
        raise AssertionError("an unreadable FYE must raise, not guess")
    except ValueError:
        pass
    print("           FYE parsed from 3 formats, refused when ambiguous")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if not os.environ.get("ANTE_VAULT"):
        # re-exec against a throwaway vault: proves ANTE_VAULT actually redirects
        # the writes, and guarantees the self-check cannot touch the demo data
        tmp = tempfile.mkdtemp(prefix="ante-selfcheck-")
        try:
            code = subprocess.run([sys.executable, "-m", "ante.ingest"],
                                  env={**os.environ, "ANTE_VAULT": tmp}).returncode
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        raise SystemExit(code)
    _selfcheck()

"""Ante's local HTTP face. The founder's browser talks to their own machine.

Bound to 127.0.0.1 on purpose. The payroll a founder uploads is parsed, written
and queried without leaving the laptop; the only thing that ever crosses the
wire is the text of a government page, sent to Bedrock to ask whether a figure
moved. That is what makes "an Obsidian second brain on your laptop" and "a
website you open" the same product rather than a contradiction.

One process serves one vault: `vault.VAULT` resolves at import, from ANTE_VAULT.
Switching founders means restarting the server, which is correct for a local
single-founder tool and is the honest version of the multi-tenant story.

    ANTE_VAULT=./vaults/acme  python run.py serve
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import pathlib

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import vault as _vault
from ante.detect import STALE_AFTER, stale

WEB = pathlib.Path(__file__).resolve().parent.parent / "web"

#: A rule note ages on three different clocks and the UI shows all three,
#: because collapsing them is exactly the claim this product refuses to make:
#: `verified` is a person's, `checked` and `confirmed` are the bot's.
FRESHNESS = ("verified", "checked", "confirmed")


def _age(value) -> int | None:
    """Days since a frontmatter date, or None if it was never set."""
    if not value:
        return None
    if isinstance(value, str):
        try:
            value = dt.date.fromisoformat(value)
        except ValueError:
            return None
    return (dt.date.today() - value).days


def _slug(slug: str) -> str:
    """A slug that names a real rule, or a 404.

    `slug` reaches ante.apply as a path segment, so it is matched against the
    rule base rather than sanitised -- an allowlist cannot be talked past the way
    a filter can, and '../../etc/passwd' is simply not a rule.
    """
    if slug not in {p.stem for p in _vault._notes("rules")}:
        raise HTTPException(404, f"no rule with slug {slug!r}")
    return slug

app = FastAPI(
    title="Ante",
    description="Regulatory early warning for Singapore startup founders. "
                "Local-only: this server binds 127.0.0.1 and your payroll "
                "never leaves the machine.",
)

# the UI is served from a different origin during development (Vite's :5173).
# Without this the browser fails the request with an opaque CORS error rather
# than anything a developer can act on. Localhost-only server, so the wildcard
# is not the exposure it would be on a public host.
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])


@app.get("/api/health")
def health() -> dict:
    """Is this vault sound, and can we reach Bedrock? Cheap, nothing billed."""
    from ante.model import credentials_ok
    alive, detail = credentials_ok()
    log = _vault.VAULT / "log.md"
    runs = [l for l in log.read_text(encoding="utf-8").splitlines()
            if l.startswith("- ")] if log.is_file() else []
    return {
        "vault": str(_vault.VAULT),
        "problems": _vault.validate(strict=False),
        "rules": len(list(_vault._notes("rules"))),
        "people": len(list(_vault._notes("people"))),
        "bedrock": {"alive": alive, "detail": detail},
        "last_sweep": runs[-1][2:] if runs else None,
        # the one staleness a bot cannot clear by working harder
        "overdue_human_review": stale(STALE_AFTER),
    }


@app.get("/api/findings")
def findings() -> dict:
    """Who is on the wrong side of which rule. Pure Python -- no AWS, no tokens.

    This is the hero screen's data and it is free, so the board renders before
    the advisor has finished thinking, and it still renders when the SSO token
    has expired.

    `annual_gap` is present per party only where the shortfall is arithmetic over
    two figures the vault already holds -- a salary against the floor it must
    clear -- and `annual_gap_total` sums exactly those. Every other rule is
    priced in its own human-written `# Cost` prose, served verbatim by
    /api/rules, because an employer CPF rate or a penalty tier is not in the
    vault and this endpoint does not invent one.
    """
    exp = _vault.exposure()
    return {**exp,
            "parties": sum(len(r["affected"]) for r in exp["rules"]),
            "priced_in_prose": sum(1 for r in exp["rules"]
                                   for a in r["affected"] if a["annual_gap"] is None),
            "company": _vault._company().get("title"),
            "as_of": str(dt.date.today())}


@app.get("/api/rules")
def rules() -> dict:
    """The whole rule base, with its freshness and its human-written prose.

    Carries three things the board cannot get from /api/findings: the `# Cost`
    and `# Next step` sections a person wrote (quoted, never paraphrased), and
    the three ageing clocks. `verified` is a human's claim that the rule still
    means what the note says; `checked` and `confirmed` are the curator's. They
    are reported separately because merging them would let a bot's daily sweep
    pass for a person having read the page.
    """
    out = []
    for p in _vault._notes("rules"):
        fm, body = _vault._split(p)
        out.append({
            "path": _vault._rel(p), "slug": p.stem,
            **{k: fm.get(k) for k in ("title", "clock", "effective", "applies_to",
                                      "severity", "resource", "unit", "bites",
                                      "threshold_before", "threshold_after",
                                      "revision", "last_change")},
            "effective": str(fm.get("effective")),
            "freshness": {k: {"on": str(fm.get(k)) if fm.get(k) else None,
                              "days": _age(fm.get(k))} for k in FRESHNESS},
            "overdue_human_review": (_age(fm.get("verified")) or STALE_AFTER + 1) > STALE_AFTER,
            "cost": _vault.section(body, "# Cost"),
            "next_step": _vault.section(body, "# Next step"),
        })
    return {"rules": out, "stale_after_days": STALE_AFTER}


@app.get("/api/rule/{slug}")
def rule(slug: str) -> dict:
    """One rule as the founder can audit it: raw frontmatter, every prose section.

    The board quotes `# Cost` and `# Next step`; this is the drawer behind
    "read the full note", and it exists so a claim on screen can be traced to the
    file it came from without leaving the browser for a text editor.
    """
    p = _vault.VAULT / "rules" / f"{_slug(slug)}.md"
    fm, body = _vault._split(p)
    return {
        "slug": slug, "path": _vault._rel(p), "title": fm.get("title"),
        "resource": fm.get("resource"),
        # str() so dates and numbers survive JSON exactly as the note spells them
        "frontmatter": [[k, str(v)] for k, v in fm.items() if k != "title"],
        "sections": [[h[2:], _vault.section(body, h)] for h in _vault.SECTIONS
                     if _vault.section(body, h)],
    }


@app.get("/api/alerts")
def alerts() -> dict:
    """What Ante raised -- and, more usefully, what it refused to do.

    A bot that declines to write an unverified figure is a better trust signal
    than one that is never wrong, so `needs_human_check` is broken out rather
    than buried: it is the refusals tray, fed straight from the vault.
    """
    out = []
    for p in sorted((_vault.VAULT / "alerts").glob("*.md"), reverse=True):
        if p.stem in ("index", "_SCHEMA"):
            continue
        fm, body = _vault._split(p)
        out.append({"path": _vault._rel(p), **{k: str(fm[k]) if k in fm else None
                                               for k in ("raised", "rule", "severity",
                                                         "status", "drift")},
                    "what_happened": _vault.section(body, "# What happened"),
                    "next_step": _vault.section(body, "# Next step")})
    return {"alerts": out,
            "needs_human_check": sum(a["status"] == "needs_human_check" for a in out)}


@app.get("/api/history/{slug}")
def history(slug: str) -> dict:
    """Every auto-applied change to one rule, newest first, with its old figures.

    The board renders this as a diff -- "S$7,400 -> S$8,000", the old note beside
    the new one -- so a founder can see what the machine changed before deciding
    whether to keep it. Undo is POST /api/rollback/{slug}.
    """
    from ante.apply import HISTORY
    now, _ = _vault._split(_vault.VAULT / "rules" / f"{_slug(slug)}.md")
    folder = HISTORY / slug
    out = []
    for f in sorted(folder.glob("*.md"), reverse=True) if folder.is_dir() else []:
        was, _ = _vault._split(f)
        out.append({"backup": f.name, "taken": f.stem,
                    "threshold_after": was.get("threshold_after"),
                    "effective": str(was.get("effective")),
                    "revision": was.get("revision")})
    return {"slug": slug, "can_rollback": bool(out),
            "now": {"threshold_after": now.get("threshold_after"),
                    "effective": str(now.get("effective")),
                    "revision": now.get("revision"),
                    "last_change": str(now.get("last_change")) if now.get("last_change") else None},
            "history": out}


@app.post("/api/rollback/{slug}")
def undo(slug: str) -> dict:
    """One-click undo of the last auto-applied change. The human's veto.

    Watching a machine change a regulation and a person overrule it in one click
    is the demo; this is the button behind it.
    """
    from ante.apply import rollback
    res = rollback(_slug(slug))
    if "error" in res:
        raise HTTPException(409, res["error"])
    return res


@app.post("/api/ask")
def ask_advisor(question: str = Form(...), thread: str = Form("")) -> dict:
    """The advisor, for the chat sidebar. Needs Bedrock; ~17k in / 2k out.

    Chat is the drill-down, never the front door: /api/findings has already put
    the board on screen for free, and this answers "why?" against the same vault.
    Returns the prose, the validated alerts, and the run's metrics, so the UI can
    show Loop Discipline as a trust signal instead of hiding it in a log.
    """
    from ante.model import credentials_ok
    alive, detail = credentials_ok()
    if not alive:
        # 503, not 500: the board is still correct and still rendering, and the
        # UI should say "commentary unavailable", not "Ante is down"
        raise HTTPException(503, f"bedrock unavailable: {detail}")
    from ante.advisor import ask
    out = ask(question, thread_id=thread or None)
    return {"answer": out["answer"],
            "alerts": [a.model_dump(mode="json") for a in out["alerts"]],
            "turns": out["turns"],
            "metrics": dataclasses.asdict(out["metrics"]),
            "report": out["metrics"].report()}


@app.post("/api/upload")
async def upload(
    payroll: UploadFile = File(..., description="Payroll CSV, one row per employee"),
    company_name: str = Form(...),
    uen: str = Form(...),
    financial_year_end: str = Form(..., description="MM-DD or YYYY-MM-DD"),
    annual_revenue_run_rate: float = Form(...),
    founder_email: str = Form(...),
    sector: str = Form(""),
    replace: bool = Form(False, description="Overwrite an existing roster"),
    mapping: str = Form("", description='JSON {"field": "CSV header"} after a '
                                        'needs_mapping response'),
) -> dict:
    """Payroll + four answers -> a validated vault on this machine.

    A `needs_mapping` response is not an error: it means a required column could
    not be identified, and it carries the CSV's headers so the UI can offer a
    dropdown. Re-post with `mapping` filled in.
    """
    from ante.ingest import build
    # utf-8-sig: Excel prefixes a BOM that would otherwise ride along in the
    # first header name
    text = (await payroll.read()).decode("utf-8-sig", errors="replace")
    return build(
        {"company_name": company_name, "uen": uen,
         "financial_year_end": financial_year_end,
         "annual_revenue_run_rate": annual_revenue_run_rate,
         "founder_email": founder_email, "sector": sector},
        text,
        overrides=json.loads(mapping) if mapping else None,
        replace=replace,
    )


@app.post("/api/brief")
def send_brief(force: bool = False) -> dict:
    """Sweep the law, read the roster, email the founder if anything is new.

    Synchronous and slow (~30s: six government fetches plus an advisor run).
    Show a spinner; /api/findings is the instant one.
    """
    from ante.notify import brief
    return brief(force=force)


# a UI dropped in web/ is served from this same origin, which sidesteps CORS
# entirely. Mounted last so it cannot shadow /api or /docs.
if WEB.is_dir():
    app.mount("/", StaticFiles(directory=WEB, html=True), name="web")


# --------------------------------------------------------------------------

def _selfcheck() -> None:
    """Every free endpoint, against the real vault, with no network and no AWS.

    FastAPI's TestClient talks to the app in-process, so this is the same code
    path uvicorn serves -- a route that 500s here 500s on stage.
    """
    from fastapi.testclient import TestClient

    c = TestClient(app)

    f = c.get("/api/findings").json()
    assert f["rules_with_exposure"] == 6, f
    assert f["parties"] == 6 and f["annual_gap_total"] == 4800.0, f
    assert f["priced_in_prose"] == 4, f
    print(f"findings ok  ->  {f['parties']} parties, S${f['annual_gap_total']:,.0f}/yr "
          f"provable, {f['priced_in_prose']} priced in prose")

    r = c.get("/api/rules").json()
    assert len(r["rules"]) == 6, r
    spass = next(x for x in r["rules"] if x["slug"] == "s-pass-qualifying-salary-2027")
    # the two sections the founder acts on must arrive as the human wrote them
    assert spass["next_step"].startswith("List every S Pass holder"), spass["next_step"]
    assert "S$2,400 a year" in spass["cost"], spass["cost"]
    # the three clocks stay apart: a machine confirmation is not a human re-read
    assert set(spass["freshness"]) == {"verified", "checked", "confirmed"}, spass
    assert spass["freshness"]["verified"]["days"] is not None, spass
    print(f"rules ok     ->  {len(r['rules'])} rules with '# Cost' and '# Next step' "
          f"quoted, 3 freshness clocks kept separate")

    one = c.get("/api/rule/s-pass-qualifying-salary-2027").json()
    # the drawer's whole job is auditability: the raw note, not a summary of it
    assert [s[0] for s in one["sections"]] == ["What changes", "Who it hits", "Cost",
                                               "Next step", "Citations"], one["sections"]
    assert any(k == "verified" for k, _ in one["frontmatter"]), one["frontmatter"]
    assert c.get("/api/rule/nope").status_code == 404
    print(f"rule ok      ->  {len(one['sections'])} prose sections + "
          f"{len(one['frontmatter'])} frontmatter keys, verbatim")

    a = c.get("/api/alerts").json()
    assert a["needs_human_check"] >= 1, a
    assert all(x["what_happened"] for x in a["alerts"]), a
    print(f"alerts ok    ->  {len(a['alerts'])} raised, "
          f"{a['needs_human_check']} refusals for the tray")

    h = c.get("/api/history/s-pass-qualifying-salary-2027").json()
    assert h["now"]["threshold_after"] == 3600, h
    assert "history" in h and isinstance(h["can_rollback"], bool), h
    print(f"history ok   ->  rule at {h['now']['threshold_after']}, "
          f"{len(h['history'])} backup(s), rollback {h['can_rollback']}")

    # the slug allowlist: this segment reaches ante.apply, which joins it onto a
    # filesystem path, so a slug that is not a rule must never get that far
    assert c.get("/api/history/nope").status_code == 404
    assert c.post("/api/rollback/nope").status_code == 404
    # traversal never even routes: a path parameter cannot hold a "/", so these
    # normalise to some other URL or fail to match. Asserted as "never
    # succeeds" rather than "is 404", because the status is the router's
    # business and pinning it would be testing Starlette, not Ante.
    for bad in ("../../etc/passwd", "..", "a/../../b",
                "%2e%2e%2f%2e%2e%2fetc%2fpasswd"):
        assert not c.get(f"/api/history/{bad}").is_success, bad
        assert not c.post(f"/api/rollback/{bad}").is_success, bad
    assert c.post("/api/rollback/s-pass-qualifying-salary-2027").status_code == 409, \
        "nothing to undo must be a 409, not a traceback"
    print("guards ok    ->  unknown slugs 404, traversal unroutable, "
          "nothing-to-undo 409")

    # /api/ask needs Bedrock; without it the board is still right, so it must
    # say "commentary unavailable" (503) rather than "Ante is down" (500)
    from ante.model import credentials_ok
    if not credentials_ok()[0]:
        got = c.post("/api/ask", data={"question": "what changes?"})
        assert got.status_code == 503, got.status_code
        print("ask ok       ->  no credentials degrades to 503, board unaffected")

    assert c.get("/api/health").json()["problems"] == [], "vault must validate"
    print(f"\napi ok       ->  {'web/ mounted at /' if WEB.is_dir() else 'no web/ yet'}")


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    _selfcheck()

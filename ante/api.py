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

import datetime as dt
import json
import pathlib

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import vault as _vault
from ante.detect import STALE_AFTER, stale

WEB = pathlib.Path(__file__).resolve().parent.parent / "web"

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
    has expired. Dollar impact is deliberately absent: those figures live in the
    human-written `# Cost` section of each rule note and reach the UI through
    /api/brief, rather than being invented here.
    """
    exp = _vault.exposure()
    return {**exp,
            "parties": sum(len(r["affected"]) for r in exp["rules"]),
            "as_of": str(dt.date.today())}


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

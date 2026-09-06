"""Get the notice to the founder. stdlib only -- no email dependency.

Two things are worth knowing before changing this file:

1. The email body IS the terminal output. `advisor.render()` produces it and
   `advisor.show()` prints it, so a finding cannot reach the screen without also
   reaching the inbox.
2. The "did anything change" fingerprint is taken over the FINDINGS, never over
   the body text. The advisor's prose contains today's date and is regenerated
   every run, so hashing the body would mail the founder an identical brief
   every single morning -- which is how a compliance alert becomes a filter rule.

    python -m ante.notify        self-check, writes to the outbox, sends nothing
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import smtplib
import sys
from email.message import EmailMessage

from vault import VAULT, _company, exposure

QUESTION = "what changes for us in the next 90 days?"
STATE = VAULT / ".brief.json"
OUTBOX = VAULT / "alerts" / "outbox"


def _bucket(deadline) -> str:
    """How urgent, in buckets. In the fingerprint so that a deadline standing
    still while today moves past it still counts as news -- otherwise "the date
    arrived" is the one clock that could never fire an email."""
    if not deadline:
        return "standing"
    days = (deadline - dt.date.today()).days
    return ("overdue" if days < 0 else "urgent" if days < 30
            else "soon" if days < 90 else "later")


def send(subject: str, body: str) -> dict:
    """-> {"sent": addr} or {"outbox": path}. Never raises.

    An unreachable SMTP server must not lose the brief: conference wifi, an
    expired app password and a missing config all land in the same place, which
    is a file the founder can still open.
    """
    from ante.model import load_env
    load_env()

    # env first: a real founder's vault carries their own address and needs no
    # env at all, but the committed demo profile holds a placeholder, so the
    # gitignored env/.env is what redirects the demo to a real inbox
    to = os.environ.get("FOUNDER_EMAIL") or _company().get("founder_email")
    if not to:
        return {"error": "no FOUNDER_EMAIL and no founder_email in company/profile.md"}

    host, user = os.environ.get("SMTP_HOST"), os.environ.get("SMTP_USER", "ante")
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, user, to
    msg.set_content(body)
    try:
        if not host:
            raise RuntimeError("no SMTP_HOST configured")
        with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", 587)), timeout=20) as s:
            s.starttls()
            s.login(user, os.environ["SMTP_PASS"])
            s.send_message(msg)
        return {"sent": to}
    except Exception as exc:
        OUTBOX.mkdir(parents=True, exist_ok=True)
        f = OUTBOX / f"{dt.date.today()}-brief.txt"
        f.write_text(f"To: {to}\nSubject: {subject}\n\n{body}\n", encoding="utf-8")
        return {"outbox": str(f), "why": f"{type(exc).__name__}: {exc}"}


def offline(exp: dict) -> str:
    """The brief without the model.

    exposure() is pure Python, so an expired SSO token -- the normal state of
    this account for half of every day -- degrades the morning email to
    figures-only rather than to silence. Every number here still came off a
    government page; only the commentary is missing.
    """
    lines = []
    for r in exp["rules"]:
        lines += ["", f"  {r['title']}",
                  f"    lands   {r['lands'] or 'standing threshold'}"
                  f"{'   (IN FORCE)' if r['in_force'] else ''}"]
        for a in r["affected"]:
            # only a numeric trigger has a meaningful comparison. An age band or
            # a year end reads as nonsense next to a threshold ("12-31 vs 7
            # months"), and the date above already carries the point.
            v = a["value"]
            versus = (f": {v} vs {r['threshold_after']} {r['unit']}"
                      if isinstance(v, (int, float)) else "")
            # the gap is arithmetic over two vault figures, so it survives an
            # expired token along with everything else here
            if a.get("annual_gap"):
                versus += f"  (S${a['annual_gap']:,.0f}/yr short)"
            lines.append(f"    who     {a['who']}{versus}")
        lines.append(f"    source  {r['resource']}")
    return "\n".join(lines)


def _fingerprint(items: list) -> str:
    return hashlib.sha256(json.dumps(items, sort_keys=True).encode()).hexdigest()[:16]


def brief(force: bool = False) -> dict:
    """Check the law, check the roster, mail the founder if anything is new.

    -> {"sent"|"outbox"|"quiet"|"error": ..., "findings": n}
    """
    from ante.curator import sweep
    from ante.model import credentials_ok

    moved = sweep()                     # pure Python, 0 tokens, on a quiet day
    alive, detail = credentials_ok()

    if alive:
        from ante.advisor import ask, render
        out = ask(QUESTION)
        body, alerts = render(out), out["alerts"]
        found = len(alerts)
        key = [[a.rule_path, sorted(a.who), _bucket(a.deadline), a.dollar_impact]
               for a in alerts]
    else:
        exp = exposure()
        found = sum(len(r["affected"]) for r in exp["rules"])
        body = (f"{found} findings. ({detail} -- vault figures only, "
                f"no AI commentary this run.)\n{offline(exp)}")
        key = [[r["path"], sorted(a["who"] for a in r["affected"]),
                _bucket(r["lands"]), None] for r in exp["rules"]]

    for c in moved.get("applied", []):
        body = f"THE LAW MOVED  {c['path']}\n  {c['was']} -> {c['now']}\n\n{body}"
    for r in moved.get("refused", []):
        body += f"\n\nAnte REFUSED a change to {r['path']}: {r['reason']}"

    seen = _fingerprint(key)
    last = json.loads(STATE.read_text(encoding="utf-8")) if STATE.is_file() else {}
    if not force and seen == last.get("hash") and not moved.get("applied"):
        return {"quiet": True, "findings": found}

    res = send(f"Ante · {found} finding{'' if found == 1 else 's'} need attention", body)
    # only remember a brief that actually went somewhere, so a failed send is
    # retried tomorrow instead of being silently marked as delivered
    if "error" not in res:
        STATE.write_text(json.dumps({"hash": seen, "sent": str(dt.date.today())}),
                         encoding="utf-8")
    return {**res, "findings": found}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")

    # no network: force the outbox path even on a machine with SMTP configured
    from ante.model import load_env
    load_env()
    os.environ["SMTP_HOST"] = ""

    exp = exposure()
    text = offline(exp)
    who = [a["who"] for r in exp["rules"] for a in r["affected"]]
    assert who, "no exposure to render"
    for name in who:
        assert name in text, f"{name} is exposed but missing from the brief"
    assert ".gov.sg" in text, "a brief with no citation is not a brief"

    res = send("Ante · self-check", text)
    assert "outbox" in res, f"expected the outbox fallback, got {res}"
    written = (VAULT / "alerts" / "outbox" /
               f"{dt.date.today()}-brief.txt").read_text(encoding="utf-8")
    for name in who:
        assert name in written, f"{name} reached the renderer but not the file"

    # the fingerprint must move when a finding does, and hold when it does not
    key = [[r["path"], sorted(a["who"] for a in r["affected"]), _bucket(r["lands"])]
           for r in exp["rules"]]
    assert _fingerprint(key) == _fingerprint(key), "fingerprint is not stable"
    assert _fingerprint(key) != _fingerprint(key[:-1]), "a dropped finding went unnoticed"

    print(f"notify ok  ->  {len(who)} findings rendered, "
          f"{len(written)} chars to {res['outbox']}")
    print(f"             no SMTP -> outbox, as designed ({res['why']})")

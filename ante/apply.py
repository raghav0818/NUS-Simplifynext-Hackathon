"""The only code in Ante that may write to vault/rules/.

Deliberately NOT a tool. The curator's model is given no tools at all -- it
returns a Verdict describing what it read, and this module decides whether that
description survives contact with the actual page bytes.

Five gates, all of which must pass. Gates 3 and 4 are the guarantee that matters:
Ante cannot write a figure that is not literally printed on a .gov.sg page, and
that is enforced by string comparison, not by asking the model nicely.

Frontmatter is edited line-by-line rather than re-dumped through YAML, so human
comments, key order and formatting survive. The vault stays readable.
"""
from __future__ import annotations

import datetime as dt
import pathlib
import re
import shutil

from vault import VAULT, _rel, _split
from ante.detect import forms_for

HISTORY = VAULT / ".history"
APPLY_VERDICTS = {"figure_changed", "date_changed"}
FLAP_WINDOW_DAYS = 30
FLAP_LIMIT = 2


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


def gates(fm: dict, verdict, page_text: str) -> tuple[bool, str]:
    """(passed, reason). Reason explains the refusal, for the alert."""
    if verdict.verdict not in APPLY_VERDICTS:
        return False, f"verdict is '{verdict.verdict}', not an applicable change"
    if verdict.confidence != "high":
        return False, f"confidence is '{verdict.confidence}', not high"

    quote = _norm(verdict.quote)
    if len(quote) < 20:
        return False, "quote too short to verify"
    if quote not in _norm(page_text):
        return False, "quote does not appear verbatim on the fetched page"

    if verdict.verdict == "figure_changed":
        if verdict.new_value is None:
            return False, "figure_changed with no new_value"
        if verdict.new_value == fm.get("threshold_after"):
            return False, "new_value equals the current value"
        if not any(_norm(f) in quote for f in forms_for(verdict.new_value, fm.get("unit"))):
            return False, (f"new_value {verdict.new_value} does not appear inside "
                           f"the quote it was supposedly read from")
    else:
        if verdict.new_effective is None:
            return False, "date_changed with no new_effective"
        if str(verdict.new_effective) == str(fm.get("effective")):
            return False, "new_effective equals the current value"
        if str(verdict.new_effective.year) not in quote:
            return False, "new_effective year does not appear inside the quote"

    return True, "all gates passed"


def flapping(fm: dict) -> bool:
    """A rule changing repeatedly means our extraction is wrong, not the law."""
    last, revision = fm.get("last_change"), int(fm.get("revision", 1) or 1)
    if not last or revision <= FLAP_LIMIT:
        return False
    if isinstance(last, str):
        last = dt.date.fromisoformat(last)
    return (dt.date.today() - last).days < FLAP_WINDOW_DAYS


def set_fields(text: str, updates: dict) -> str:
    """Rewrite frontmatter keys in place, preserving comments and key order."""
    head, sep, rest = text.partition("\n---")
    lines = head.split("\n")
    for key, value in updates.items():
        line = f"{key}: {value}"
        for i, existing in enumerate(lines):
            if re.match(rf"^{re.escape(key)}\s*:", existing):
                trailing = existing.split("#", 1)
                lines[i] = line + (f"  #{trailing[1]}" if len(trailing) > 1 else "")
                break
        else:
            lines.append(line)
    return "\n".join(lines) + sep + rest


def apply_rule_update(rule_path, verdict, page_text: str) -> dict:
    """Apply a verdict to a rule if and only if all five gates pass."""
    path = pathlib.Path(rule_path)
    if not path.is_absolute():
        path = VAULT / str(rule_path).replace("\\", "/")
    if not path.is_file():
        return {"applied": False, "reason": f"no rule at {rule_path}"}

    fm, _ = _split(path)
    if flapping(fm):
        return {"applied": False, "reason": "frozen: changed too often, needs a human",
                "path": _rel(path)}

    ok, reason = gates(fm, verdict, page_text)
    if not ok:
        return {"applied": False, "reason": reason, "path": _rel(path)}

    today = dt.date.today()
    stamp = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    HISTORY.mkdir(parents=True, exist_ok=True)
    (HISTORY / path.stem).mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, HISTORY / path.stem / f"{stamp}.md")

    updates = {"revision": int(fm.get("revision", 1) or 1) + 1,
               "last_change": today, "checked": today, "confirmed": today}
    was = None
    if verdict.verdict == "figure_changed":
        was = fm.get("threshold_after")
        updates["threshold_before"] = was
        updates["threshold_after"] = (int(verdict.new_value)
                                      if float(verdict.new_value).is_integer()
                                      else verdict.new_value)
    else:
        was = fm.get("effective")
        updates["effective"] = verdict.new_effective

    path.write_text(set_fields(path.read_text(encoding="utf-8"), updates), encoding="utf-8")
    return {"applied": True, "path": _rel(path), "was": was,
            "now": updates.get("threshold_after", updates.get("effective")),
            "revision": updates["revision"], "quote": verdict.quote,
            "history": f".history/{path.stem}/{stamp}.md"}


def touch(rule_path, confirmed: bool) -> None:
    """Record that we looked. `checked` always; `confirmed` only when the figure held.

    Never touches `verified` -- that date means a human read the page, and a
    machine confirmation is not the same claim.
    """
    path = VAULT / str(rule_path).replace("\\", "/")
    today = dt.date.today()
    updates = {"checked": today} | ({"confirmed": today} if confirmed else {})
    path.write_text(set_fields(path.read_text(encoding="utf-8"), updates), encoding="utf-8")


def rollback(slug: str) -> dict:
    """Restore the most recent pre-change copy of a rule."""
    folder = HISTORY / slug
    copies = sorted(folder.glob("*.md")) if folder.is_dir() else []
    if not copies:
        return {"error": f"no history for {slug}"}
    target = VAULT / "rules" / f"{slug}.md"
    shutil.copy2(copies[-1], target)
    copies[-1].unlink()
    return {"restored": _rel(target), "from": copies[-1].name}


def _demo() -> None:
    """Self-check: the gates must reject a fabricated quote."""
    from ante.schema import Verdict

    fm = {"threshold_after": 3600, "unit": "SGD_per_month", "effective": "2027-01-01"}
    page = "The qualifying salary rises to $3,800 from 1 January 2028 for new applications."

    real = Verdict(verdict="figure_changed", new_value=3800, confidence="high",
                   quote="The qualifying salary rises to $3,800 from 1 January 2028",
                   reasoning="figure moved")
    ok, why = gates(fm, real, page)
    assert ok, why

    fake = real.model_copy(update={"quote": "The qualifying salary rises to $9,900 tomorrow"})
    ok, why = gates(fm, fake, page)
    assert not ok and "verbatim" in why, why

    liar = real.model_copy(update={"new_value": 9900})
    ok, why = gates(fm, liar, page)
    assert not ok and "does not appear inside" in why, why

    hedge = real.model_copy(update={"confidence": "medium"})
    assert not gates(fm, hedge, page)[0]

    noop = real.model_copy(update={"new_value": 3600})
    assert not gates(fm, noop, page)[0]

    assert set_fields("---\nrevision: 1\nx: 2\n---\nbody", {"revision": 2, "new": "z"}) \
        .startswith("---\nrevision: 2\nx: 2\nnew: z\n---")
    print("apply gates ok  ->  real change accepted, fabricated quote refused,")
    print("                    invented figure refused, hedge refused, no-op refused")


if __name__ == "__main__":
    _demo()

"""The whole curator, proved without a network and without AWS.

`run.py negative` is the strongest claim this project makes -- Ante cannot write
a figure that is not literally printed on a .gov.sg page -- and it proves it
against the live MOM page. That is the right test to run on stage, and the wrong
one to depend on: it needs outbound HTTPS to mom.gov.sg, so it cannot run behind
a locked-down egress policy, in CI, on a plane, or on conference wifi. A proof
you can only perform when the network agrees with you is not a regression test.

So this replays the same pipeline against canned pages. `detect.fetch` and the
model call are the only two things stubbed; everything between them is the real
code -- the real region hashing, the real snapshots, the real LangGraph sweep,
the real five gates, the real writer. Four scenarios, each asserting on the rule
file's bytes afterwards:

    baseline      first sight of a page records a snapshot, changes nothing
    unchanged     the same page again -> `checked:` advances, the model is
                  never called, and the sweep costs zero tokens
    honest        a figure really moved and the quote is real -> APPLIED, with
                  the previous file preserved in .history/
    fabricated    a quote that is not on the page -> REFUSED, an alert raised
                  for a human, and the rule byte-identical
    misattributed a real quote carrying an invented number -> REFUSED

It runs in a throwaway vault, so it can never touch the shipped one.

    python -m ante.replay
"""
from __future__ import annotations

import datetime as dt
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

SLUG = "s-pass-qualifying-salary-2027"

#: What the "government page" says today. Deliberately full of the things a real
#: page has and a hash must not fire on -- navigation, a cookie line, a footer.
PAGE_OLD = (
    "Skip to main content. Ministry of Manpower. This site uses cookies. "
    "S Pass eligibility. To qualify for an S Pass, candidates must earn at "
    "least $3,300 a month. The qualifying salary increases progressively with "
    "age. Employers must also meet the quota and levy requirements. "
    "Last updated 12 August 2026. Report vulnerability. Privacy statement."
)
PAGE_NEW = PAGE_OLD.replace(
    "must earn at least $3,300 a month",
    "must earn at least $3,800 a month from 1 January 2028")


def _fixture_vault(root: pathlib.Path) -> None:
    """A copy of the shipped vault, so the real rule notes are under test."""
    shipped = pathlib.Path(__file__).resolve().parent.parent / "vault"
    for folder in ("rules", "people", "company"):
        shutil.copytree(shipped / folder, root / folder, dirs_exist_ok=True)
    for f in ("_SCHEMA.md", "log.md", "index.md"):
        if (shipped / f).is_file():
            shutil.copy2(shipped / f, root / f)
    (root / "alerts").mkdir(exist_ok=True)


def _run() -> None:
    import vault
    from ante import curator, detect
    from ante.schema import Verdict

    root = vault.VAULT
    rule = root / "rules" / f"{SLUG}.md"
    curator.STATE_DB = root / "state.db"          # never the repo's own

    page = {"text": PAGE_OLD}
    detect.fetch = lambda url: {"text": page["text"], "url": url}

    verdict = {"v": None}
    calls = {"n": 0}

    def fake_ask(fm, found):
        calls["n"] += 1
        return verdict["v"]
    curator.ask = fake_ask

    def sweep():
        return curator.sweep(only=SLUG)

    def threshold():
        return vault._split(rule)[0]["threshold_after"]

    # -- baseline ---------------------------------------------------------
    out = sweep()
    assert out["checked"] == 1 and out["changed"] == 0, out
    assert calls["n"] == 0, "a first sighting must not wake the model"
    assert threshold() == 3600, threshold()
    print(f"  baseline      snapshot recorded, model not called, "
          f"threshold still {threshold()}")

    # -- unchanged --------------------------------------------------------
    before = rule.read_text(encoding="utf-8")
    out = sweep()
    assert out["changed"] == 0, out
    assert calls["n"] == 0, "an unchanged page must never reach Bedrock"
    assert out["metrics"].input_tokens == 0, out["metrics"].input_tokens
    after = vault._split(rule)[0]
    assert str(after["checked"]) == str(dt.date.today()), after
    assert after["threshold_after"] == 3600
    assert "verified: 2026-09-04" in rule.read_text(encoding="utf-8"), \
        "a machine check must never advance the human's verified: date"
    print(f"  unchanged     0 tokens, model not called, checked: advanced, "
          f"verified: untouched")

    # -- a fabricated quote, against a page that really did move ----------
    page["text"] = PAGE_NEW
    verdict["v"] = Verdict(
        verdict="figure_changed", new_value=9900.0, confidence="high",
        reasoning="fabricated for the replay test",
        quote="The S Pass qualifying salary rises to $9,900 from 1 January 2027")
    before = rule.read_text(encoding="utf-8")
    out = sweep()
    assert out["changed"] == 1 and calls["n"] == 1, out
    assert not out["applied"], f"GATES LET A FABRICATION THROUGH: {out['applied']}"
    assert len(out["refused"]) == 1, out
    assert "verbatim" in out["refused"][0]["reason"], out["refused"][0]
    assert rule.read_text(encoding="utf-8") == before, "rule file was modified"
    assert out["alerts"], "a refusal must leave an alert for a human"
    print(f"  fabricated    refused ({out['refused'][0]['reason']}), "
          f"rule byte-identical, {len(out['alerts'])} alert raised")

    # -- a REAL quote carrying an invented number -------------------------
    verdict["v"] = Verdict(
        verdict="figure_changed", new_value=9900.0, confidence="high",
        reasoning="quote is real, the number in it is not",
        quote="must earn at least $3,800 a month from 1 January 2028")
    out = sweep()
    assert not out["applied"], "gate 4 must reject a number not inside its quote"
    assert "does not appear inside" in out["refused"][0]["reason"], out["refused"][0]
    assert rule.read_text(encoding="utf-8") == before, "rule file was modified"
    print(f"  misattributed refused ({out['refused'][0]['reason'][:58]}...)")

    # -- the honest change ------------------------------------------------
    verdict["v"] = Verdict(
        verdict="figure_changed", new_value=3800.0, confidence="high",
        reasoning="the page really says 3,800 now",
        quote="must earn at least $3,800 a month from 1 January 2028")
    out = sweep()
    assert len(out["applied"]) == 1, out
    assert threshold() == 3800, threshold()
    fm = vault._split(rule)[0]
    assert fm["threshold_before"] == 3600, fm
    assert fm["revision"] == 2, fm
    assert "bites: below  # a qualifying-salary floor" in rule.read_text(encoding="utf-8"), \
        "line-by-line editing must preserve the human's inline comment"
    history = sorted((root / ".history" / SLUG).glob("*.md"))
    assert history, "an applied change must leave a rollback copy"
    print(f"  honest        APPLIED 3600 -> {threshold()}, revision {fm['revision']}, "
          f"comment preserved, {len(history)} backup")

    # -- and the undo -----------------------------------------------------
    from ante.apply import rollback
    res = rollback(SLUG)
    assert "restored" in res, res
    assert threshold() == 3600, threshold()
    print(f"  rollback      restored to {threshold()}")

    # -- a page that no longer mentions the rule at all -------------------
    # Nothing "changed" here, so this is a quiet day by the detector's
    # reckoning -- and a quiet day used to skip apply() entirely, which meant an
    # unreadable government page raised no alert and printed as a clean sweep.
    page["text"] = ("Ministry of Manpower. This page has moved. "
                    "Please use the search function. Contact us.")
    out = sweep()
    assert out["changed"] == 0, "an unreadable page is not a changed figure"
    assert calls["n"] == 3, "and it must not wake the model"
    assert out["alerts"], "an unverifiable page MUST still raise an alert"
    raised = (root / "alerts").glob(f"*-curator-{SLUG}.md")
    assert any("unverifiable" in p.read_text(encoding="utf-8") for p in raised)
    print(f"  unverifiable  page unreadable -> {len(out['alerts'])} alert raised "
          f"on a day nothing changed, 0 tokens")

    print(f"\nreplay ok  ->  the full sweep, five gates and rollback, "
          f"with no network and no AWS")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if os.environ.get("ANTE_REPLAY") != "1":
        # vault.VAULT resolves at import, so the throwaway vault has to be in
        # the environment before anything imports it -- same re-exec the ingest
        # self-check uses, and the reason neither can touch the shipped vault
        tmp = tempfile.mkdtemp(prefix="ante-replay-")
        _fixture_vault(pathlib.Path(tmp))
        try:
            code = subprocess.run(
                [sys.executable, "-m", "ante.replay"],
                env={**os.environ, "ANTE_VAULT": tmp, "ANTE_REPLAY": "1"}).returncode
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        raise SystemExit(code)
    _run()

"""The curator graph: nobody asks, Ante checks the law still says what we claim.

Three tiers, cheapest first, because a daily sweep that costs tokens on a
no-change day is a daily sweep that gets switched off:

    detect      pure Python. Hashes the region of each source page around the
                rule's own keywords. No AWS, no model, zero tokens. Runs on
                every rule, every day, and on almost every day stops here.
    understand  Bedrock, and ONLY for the rules whose page actually moved. The
                model is given NO TOOLS -- it returns a Verdict describing what
                it read. It cannot write, fetch, or act.
    verify      ante.apply.gates. Pure Python. Re-checks every claim in the
                Verdict against the fetched page bytes. That is what makes the
                model's honesty irrelevant.

Why LangGraph and not a for-loop: `detect` never needs AWS and always succeeds,
but this account's SSO tokens die every ~12h, so `understand` failing mid-run is
routine, not exceptional. The SqliteSaver checkpoint on thread_id
"sweep-<date>" means the next authenticated run resumes at `understand` with the
already-fetched pages still in state, instead of re-crawling six government
sites.

    python -m ante.curator --dry-run
    python -m ante.curator
    python -m ante.curator --only s-pass-qualifying-salary-2027
    python -m ante.curator --rollback s-pass-qualifying-salary-2027
    python -m ante.curator --negative      # proof it cannot invent a figure
"""
from __future__ import annotations

import datetime as dt
import operator
import pathlib
import sys
from typing import Annotated, Optional, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from vault import VAULT, _notes, _rel, _split, vault_write
from ante.apply import _norm, apply_rule_update, gates, rollback, touch
from ante.detect import detect_one, save_snapshot
from ante.metrics import Metrics
from ante.model import CredentialsExpired, _is_expired, chat
from ante.schema import Verdict

STATE_DB = pathlib.Path(__file__).resolve().parent.parent / "state.db"
LOG = VAULT / "log.md"
CLAIMS = ("figure_changed", "date_changed")

#: One counter set per process; sweep() resets it. The nodes stay module-level
#: and readable -- threading a Metrics object through a checkpointed state just
#: to avoid one global would cost more than it buys.
M = Metrics()

SYSTEM = """You read Singapore government pages and report what moved.

You are given one compliance rule, the region of its source page as we last
recorded it, and the same region as it reads today. Say what changed.

Judge ONLY the rule's own headline figure and its effective date. Government
pages are edited constantly -- navigation, cookie notices, reflowed sentences,
new examples. All of that is verdict="unchanged". Say "figure_changed" only if
the rule's threshold is literally a different number on the new page, and
"date_changed" only if its effective date moved.

`quote` MUST be copied CHARACTER-FOR-CHARACTER out of the NEW PAGE text below,
and must be long enough to contain the number or date you are claiming (20
characters minimum). It is checked by literal string comparison against the page
we fetched. A paraphrase, a summary, a retyped dollar sign or a tidied-up
sentence WILL FAIL that check, and your whole verdict will be thrown away and
escalated to a human. Copy it. Do not compose it.

Use confidence="high" only when the new page states the change plainly and you
copied the quote straight out of it. Anything you inferred, deduced or
reconstructed is "medium" or "low", and will not be applied."""


# --------------------------------------------------------------------------
# state
# --------------------------------------------------------------------------

class State(TypedDict, total=False):
    day: str
    dry_run: bool
    rules: list           # vault-relative rule paths, chosen by sweep()
    detected: Annotated[list, operator.add]
    verdicts: Annotated[list, operator.add]
    checked: list         # a detect result plus its gate decision, per changed rule
    applied: list
    refused: list
    alerts: list


class RuleIn(TypedDict):
    """Send payload for one detect branch."""
    rule: str


class ChangeIn(TypedDict):
    """Send payload for one understand branch."""
    found: dict


# --------------------------------------------------------------------------
# tier 1 -- detect. No AWS, no model, no tokens.
# --------------------------------------------------------------------------

def fan_rules(state: State):
    return [Send("detect", {"rule": rel}) for rel in state["rules"]]


def detect(payload: RuleIn) -> dict:
    found = detect_one(VAULT / payload["rule"])
    M.tasks_attempted += 1
    # detect_one signals failure in `status`, not with an "error" key
    M.tool({"error": found["detail"]} if found["status"] == "error" else found)
    if not found["changed"]:
        # an unchanged page's full text would be checkpointed for nothing
        for heavy in ("page_text", "old_region", "new_region"):
            found.pop(heavy, None)
    return {"detected": [found]}


def after_detect(state: State):
    """The whole point of tier 1: on a quiet day this returns "log", and the
    model is never constructed, let alone invoked."""
    changed = [d for d in state["detected"] if d["changed"]]
    return [Send("understand", {"found": d}) for d in changed] or "log"


# --------------------------------------------------------------------------
# tier 2 -- understand. Bedrock, no tools bound.
# --------------------------------------------------------------------------

def _brief(fm: dict, found: dict) -> str:
    return (f"RULE\n"
            f"  title           : {fm.get('title')}\n"
            f"  threshold_after : {fm.get('threshold_after')} {fm.get('unit')}\n"
            f"  effective       : {fm.get('effective')}\n"
            f"  source          : {found['url']}\n\n"
            f"OLD PAGE -- what we recorded last time\n"
            f"----------------------------------------\n{found['old_region']}\n\n"
            f"NEW PAGE -- fetched today. Copy `quote` from HERE, verbatim.\n"
            f"----------------------------------------\n{found['new_region']}")


def ask(fm: dict, found: dict) -> Verdict:
    """One Verdict, or an honest 'unclear'. Never raises on bad model output."""
    llm = chat().with_structured_output(Verdict, include_raw=True)
    brief = _brief(fm, found)
    for _ in range(2):
        try:
            out = llm.invoke([("system", SYSTEM), ("user", brief)])
        except Exception as exc:
            if _is_expired(exc):
                raise CredentialsExpired(str(exc)) from exc
            raise
        M.turns_used += 1
        M.usage(out["raw"].usage_metadata)
        parsed, why = out["parsed"], out["parsing_error"]
        M.schema(parsed is not None and why is None)
        if parsed is not None and why is None:
            return parsed
        brief += (f"\n\nYour previous answer was rejected: {why or 'no Verdict returned'}\n"
                  f"Return a valid Verdict. Copy `quote` verbatim from the NEW PAGE above.")
    return Verdict(verdict="unclear", confidence="low", quote="",
                   reasoning="model returned no valid Verdict in two attempts")


def understand(payload: ChangeIn) -> dict:
    found = payload["found"]
    fm, _ = _split(VAULT / found["path"])
    return {"verdicts": [{**found, "verdict": ask(fm, found),
                          "title": fm.get("title")}]}


# --------------------------------------------------------------------------
# tier 3 -- verify. Pure Python; the model does not get a vote here.
# --------------------------------------------------------------------------

def verify(state: State) -> dict:
    out = []
    for v in state["verdicts"]:
        fm, _ = _split(VAULT / v["path"])
        ok, reason = gates(fm, v["verdict"], v["page_text"])
        if v["verdict"].verdict in CLAIMS:
            M.claims += 1
            M.claims_cited += int(_norm(v["verdict"].quote) in _norm(v["page_text"]))
        out.append({**v, "ok": ok, "reason": reason})
    return {"checked": out}


# --------------------------------------------------------------------------
# apply -- the only node that writes
# --------------------------------------------------------------------------

def _alert(day: str, rel: str, title, kind: str, detail: str, severity="high") -> str:
    res = vault_write(
        f"alerts/{day}-curator-{pathlib.Path(rel).stem}.md",
        {"type": "alert", "raised": day, "rule": rel, "severity": severity,
         "status": "needs_human_check", "drift": kind},
        f"# What happened\n\n`{kind}` checking [{title}]({rel})\n\n{detail}\n\n"
        f"# Next step\n\nA human must open the government page and decide. Ante "
        f"refused to make this change itself.\n")
    M.tool(res)
    return res.get("written") or res.get("error")


def apply(state: State) -> dict:
    """Applies passers, touches confirmed rules, escalates everything else.

    A snapshot is re-baselined only when the change was understood -- applied,
    or read as cosmetic. A refused change deliberately keeps its stale snapshot,
    so tomorrow's sweep raises it again until a human clears it.
    """
    live = not state["dry_run"]
    applied, refused, alerts = [], [], []

    for c in state.get("checked", []):
        verdict, rel = c["verdict"], c["path"]

        if verdict.verdict == "unchanged":
            print(f"  UNCHANGED  {c['slug']}: page edited, figure held "
                  f"({verdict.confidence}) -- {verdict.reasoning[:90]}")
            if live:
                touch(rel, confirmed=True)
                save_snapshot(c["slug"], c["url"], c["new_region"])
            continue

        res = ({"applied": False, "reason": c["reason"]} if not c["ok"]
               else apply_rule_update(rel, verdict, c["page_text"]) if live
               else {"applied": True, "path": rel, "was": "(dry run)",
                     "now": verdict.new_value or verdict.new_effective})
        M.tool(res)

        if res["applied"]:
            print(f"  APPLIED    {c['slug']}: {res.get('was')} -> {res.get('now')}")
            print(f"             \"{verdict.quote[:110]}\"")
            if live:
                save_snapshot(c["slug"], c["url"], c["new_region"])
            applied.append(res)
        else:
            print(f"  REFUSED    {c['slug']}: {res['reason']}")
            refused.append({"slug": c["slug"], "path": rel, "reason": res["reason"],
                            "verdict": verdict.verdict, "quote": verdict.quote})
            if live:
                alerts.append(_alert(
                    state["day"], rel, c["title"], "refused_change",
                    f"The source page changed. The model reported "
                    f"`{verdict.verdict}` (confidence {verdict.confidence}) but "
                    f"Ante refused to write it: **{res['reason']}**\n\n"
                    f"Model's reasoning: {verdict.reasoning}\n\n"
                    f"Quote it offered:\n\n> {verdict.quote or '(none)'}"))

    for d in state["detected"]:
        if d["status"] in ("unchanged", "baseline"):
            if live:
                touch(d["path"], confirmed=(d["status"] == "unchanged"))
        elif d["status"] in ("unverifiable", "error"):
            print(f"  {d['status'].upper():<10} {d['slug']}: {d['detail']}")
            if live:
                fm, _ = _split(VAULT / d["path"])
                alerts.append(_alert(
                    state["day"], d["path"], fm.get("title"), d["status"],
                    f"{d['detail']}\n\nSource: {d['url']}",
                    severity="medium" if d["status"] == "unverifiable" else "low"))

    return {"applied": applied, "refused": refused, "alerts": alerts}


# --------------------------------------------------------------------------
# log -- always runs, so a quiet day is provable rather than merely claimed
# --------------------------------------------------------------------------

def log(state: State) -> dict:
    detected = state["detected"]
    # counted here, not in apply, because apply does not run on a quiet day and
    # a rule whose page held is a task completed, not a task skipped
    decided = {c["slug"] for c in state.get("checked", [])}
    M.tasks_completed = sum(1 for d in detected if d["slug"] in decided
                            or d["status"] in ("unchanged", "baseline"))

    line = (f"- {state['day']} curator: {len(detected)} checked, "
            f"{sum(d['changed'] for d in detected)} changed, "
            f"{len(state.get('applied', []))} applied, "
            f"{len(state.get('refused', []))} refused, "
            f"{len(state.get('alerts', []))} alerts, "
            f"{M.input_tokens} in / {M.output_tokens} out tokens")
    print(f"\n{line}")
    if not state["dry_run"]:
        text = LOG.read_text(encoding="utf-8").replace("_No runs yet._", "").rstrip()
        gap = "" if text.rsplit("\n", 1)[-1].startswith("- ") else "\n"
        LOG.write_text(f"{text}\n{gap}{line}\n", encoding="utf-8")
    return {}


# --------------------------------------------------------------------------
# graph
# --------------------------------------------------------------------------

def build() -> StateGraph:
    g = StateGraph(State)
    for name, fn in (("detect", detect), ("understand", understand),
                     ("verify", verify), ("apply", apply), ("log", log)):
        g.add_node(name, fn)
    g.add_conditional_edges(START, fan_rules, ["detect"])
    g.add_conditional_edges("detect", after_detect, ["understand", "log"])
    g.add_edge("understand", "verify")
    g.add_edge("verify", "apply")
    g.add_edge("apply", "log")
    g.add_edge("log", END)
    return g


def sweep(dry_run: bool = False, only: Optional[str] = None) -> dict:
    global M
    M = Metrics()
    day = str(dt.date.today())
    rules = [_rel(p) for p in _notes("rules") if only is None or p.stem == only]
    if not rules:
        return {"error": f"no rule with slug {only!r}"}
    M.turn_limit = 2 * len(rules)   # at most one Verdict plus one retry per rule

    # from_conn_string returns a context manager, not a saver -- the connection
    # has to stay open for as long as the graph runs
    with SqliteSaver.from_conn_string(str(STATE_DB)) as cp:
        graph = build().compile(checkpointer=cp)
        thread = f"sweep-{day}"
        config = {"configurable": {"thread_id": thread}}
        snap = graph.get_state(config)
        resuming = bool(snap.next)
        if snap.created_at and not resuming:
            # a finished thread is history; reusing it would re-add its results
            cp.delete_thread(thread)

        print(f"curator {'(dry run) ' if dry_run else ''}{day} -- {len(rules)} rule(s)")
        if resuming:
            print(f"  resuming '{thread}' at {snap.next} -- "
                  f"pages already in the checkpoint, nothing re-crawled")
        try:
            final = graph.invoke(
                None if resuming else {"day": day, "dry_run": dry_run, "rules": rules},
                config)
        except CredentialsExpired as exc:
            print(f"\n  AWS credentials expired mid-run: {exc}")
            print(f"  checkpointed at thread '{thread}'. Refresh env/.env and "
                  f"re-run -- detect will not repeat.")
            return {"error": "credentials expired", "checkpoint": thread, "metrics": M}

    return {"checked": len(final["detected"]),
            "changed": sum(d["changed"] for d in final["detected"]),
            "applied": final.get("applied", []),
            "refused": final.get("refused", []),
            "alerts": final.get("alerts", []),
            "metrics": M}


# --------------------------------------------------------------------------

def negative_test(slug: str = "s-pass-qualifying-salary-2027") -> bool:
    """The claim the pitch rests on: Ante cannot write a figure that is not
    printed on a .gov.sg page. Fetches the real page, hands the gates a Verdict
    carrying a fabricated quote, and checks the rule file is untouched."""
    path = VAULT / "rules" / f"{slug}.md"
    before = path.read_text(encoding="utf-8")
    fm, _ = _split(path)
    found = detect_one(path)
    page = found.get("page_text") or found.get("new_region") or ""
    assert len(page) > 500, f"could not fetch {slug}: {found}"

    fake = Verdict(verdict="figure_changed", new_value=9900.0, confidence="high",
                   reasoning="fabricated for the negative test",
                   quote="The S Pass qualifying salary rises to $9,900 from 1 January 2027")
    assert _norm(fake.quote) not in _norm(page), "the test quote is accidentally real"

    ok, why = gates(fm, fake, page)
    res = apply_rule_update(path, fake, page)
    assert not ok and not res["applied"], f"GATES LET A FABRICATION THROUGH: {why}"
    assert path.read_text(encoding="utf-8") == before, "rule file was modified"
    print(f"negative test ok  ->  fabricated $9,900 quote refused: {why}")
    print(f"                      {_rel(path)} byte-identical, nothing written")
    return True


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    argv = sys.argv[1:]

    if "--rollback" in argv:
        print(rollback(argv[argv.index("--rollback") + 1]))
    elif "--negative" in argv:
        negative_test()
    else:
        only = argv[argv.index("--only") + 1] if "--only" in argv else None
        out = sweep(dry_run="--dry-run" in argv, only=only)
        if "metrics" not in out:
            sys.exit(out["error"])
        print(f"\n{out['metrics'].report()}")

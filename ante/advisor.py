"""The advisor graph: the founder asks, Ante answers with citations.

A LangGraph ReAct loop over the five vault tools. Four decisions are worth
explaining, because none of them is the obvious one:

1. The company's own figures and the rule index are pre-injected into the system
   message instead of being discovered. Every question needs both, neither
   changes mid-run, and the turn budget is roughly six model calls -- spending
   two of them fetching what we already know is how a run runs out of road
   before it reaches the roster.
2. Only the profile's FRONTMATTER goes in, not its body. The body is the vault
   author's commentary and it names which staff trigger which rule; injecting it
   would let the model recite the answer instead of deriving it.
3. The checkpointer is keyed on the company's UEN. Follow-up questions land with
   the previous answer already in scope, and a run that trips the recursion
   limit can still be read back out of sqlite rather than vanishing.
4. The founder-facing prose and the structured alerts are produced by the SAME
   run, via response_format, not by re-reading the prose afterwards. A second
   pass over the answer text kept losing findings the prose had grouped or
   demoted; the structured pass sees the tool results instead.

    python -m ante.advisor
"""
from __future__ import annotations

import datetime as dt
import functools
import json
import pathlib
import sqlite3
import sys

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.errors import GraphRecursionError
from langgraph.prebuilt import create_react_agent

import vault
from ante.metrics import Metrics
from ante.model import chat, credentials_ok
from ante.schema import GOV, Findings
from ante.tools import TOOLS

DB = pathlib.Path(__file__).resolve().parent.parent / "state.db"
TODAY = dt.date.today()

SYSTEM = f"""You are Ante. You warn Singapore startup founders about regulatory
obligations that are about to bite. Today is {TODAY}. Every figure is SGD.

Be concrete and short. A founder wants four things: who, how much, by when, and
what to do about it.

HOW TO WORK
1. exposure() FIRST, always, before anything else. It returns the complete list
   of who is on the wrong side of which rule, with the arithmetic already done.
   That list IS your set of findings: report every party in it, and no party
   outside it.
2. vault_read each rule exposure() returned, batched in ONE turn -- several tool
   calls in the same message. You need each rule's "# Next step" wording and its
   "# Cost" figures. You do NOT need to re-check who is affected; that is
   settled. Never state a figure you have not read out of a rule note.
3. Then answer.

roster_scan and vault_search are there for follow-up questions ("who else is
near this?"). They are not part of answering this one, and roster_scan returns
everyone in a CATEGORY rather than everyone a rule breaks -- exposure() is the
one that has already told them apart.

HORIZON
"The next 90 days" means what must be ACTED ON now, not what has an effective
date inside 90 days. Report every one of these:

- Anything ALREADY IN FORCE. A date in the past is the strongest reason to raise
  a rule, never a reason to drop it: the cost is being incurred right now and
  grows every month it goes unadjusted. Nothing in the vault tells you whether
  the company has already adjusted for it, so you may not assume it has, and
  "already in force" is not a finding you get to write off as settled.
- Anything landing within the next 18 months. Payroll changes, pass renewals and
  tax registrations all need two quarters of lead time.
- Any standing threshold the company is close to.

Always say the date each one actually lands, and never drop a finding for
landing late -- exposure() already decided membership, and timing is a column,
not a filter.

CITATIONS
Every claim cites the `resource` URL of the rule note it came from. The
`# Next step` section of a note is written by a human: quote it. Never invent
regulatory advice and never state a figure the vault does not contain.

Do not call vault_write unless the founder asks you to record something.

FINISH WITH THE COMPLETE LIST
Work through the rule base one rule at a time -- every rule in the index, not
the ones that feel urgent -- and for each, say who is on the wrong side of it.
Then end with a table headed "Findings" carrying ONE ROW PER AFFECTED PARTY PER
RULE, with their number, the threshold, the date it lands, and the URL. A party
is a named person OR the company itself -- company obligations like registration
thresholds and filing deadlines are findings exactly as staff ones are.

Membership in that table is decided by one question only: is someone on the
wrong side of this rule? It is never decided by when the rule lands. A rule
taking effect in 2027 or 2028 earns a row today if somebody is already short of
it, because the salary review that fixes it happens now. Put the timing in the
date column and let the founder judge urgency for themselves.

So: no second list of things that are "outside the window" or "worth planning
for later" -- anything you would have put there is a row in the table instead.
If you name someone as affected anywhere above, they have a row. The table is
the deliverable, and a finding missing from it never reaches the founder."""

EXTRACT = """List the findings from this run as structured data.

One finding per party per rule that exposure() returned as affected. exposure()
already decided who is affected and its answer is final -- do not add a party it
left out, and do not drop one it included because the rule lands late. Timing
goes in `deadline`.

Take `resource` and `rule_path` from the list below, character for character.
Quote `next_step` from that rule's "# Next step" section. `who` is the party's
name exactly as the vault gives it -- "Staff 04", or the company's full name,
with no role appended.

{table}"""


# --------------------------------------------------------------------------
# context and plumbing
# --------------------------------------------------------------------------

def load_context() -> str:
    """The company's own figures and the rule index, injected up front.

    Frontmatter only for the profile -- see the module docstring. The rule index
    is a bare table of what exists, which orients without answering anything.
    """
    fm, _ = vault._split(vault.VAULT / "company" / "profile.md")
    facts = "\n".join(f"  {k}: {v}" for k, v in fm.items())
    index = vault.vault_read("rules/index.md")["body"]
    return (f"THE COMPANY (vault/company/profile.md)\n{facts}\n\n"
            f"THE RULE BASE (vault/rules/index.md)\n{index}")


def _resources() -> dict:
    """rule path -> government URL, read straight from the vault. Anchors the
    structured pass on the real rule base instead of the model's recall."""
    return {vault._rel(p): vault._split(p)[0].get("resource")
            for p in vault._notes("rules")}


def _rule_table() -> str:
    """The rule base as `path -> url` lines. The structured pass walks this,
    so completeness is bounded by the vault rather than by the model's recall."""
    sep = chr(10)
    return sep.join(f"  {p} -> {u}" for p, u in _resources().items())


def _text(msg) -> str:
    """Message content as a string. Bedrock returns it as content blocks."""
    if isinstance(msg.content, str):
        return msg.content
    return "".join(b.get("text", "") for b in msg.content if isinstance(b, dict))


def _result(msg: ToolMessage):
    """A ToolMessage back into the dict the tool returned, so Metrics can see
    the {"error": ...} our tools return instead of raising."""
    try:
        return json.loads(msg.content)
    except (json.JSONDecodeError, TypeError):
        # ToolNode str()s a dict when it is not JSON-serialisable
        return {"error": msg.content} if msg.status == "error" else msg.content


@functools.lru_cache(maxsize=1)
def graph():
    """The compiled agent. One sqlite connection for the life of the process.

    SqliteSaver.from_conn_string() is a context manager in current langgraph, so
    the saver is built from the connection directly -- otherwise the checkpointer
    closes the moment the `with` block ends and ask() cannot stay a plain call.
    """
    saver = SqliteSaver(sqlite3.connect(DB, check_same_thread=False))
    saver.setup()
    return create_react_agent(
        # 2000 truncated the findings list mid-sentence and silently lost the
        # last finding -- the one failure mode a compliance tool cannot have
        chat(max_tokens=4000),
        TOOLS,
        prompt=SystemMessage(f"{SYSTEM}\n\n{load_context()}"),
        # the structured pass runs INSIDE the graph, so the model writing it
        # sees the roster rows and rule frontmatter this run actually fetched.
        # Re-reading the prose answer instead lost findings to formatting: a
        # summary table with fewer rows than the discussion above it, and an
        # extractor faithfully copying the table.
        response_format=(EXTRACT.format(table=_rule_table()), Findings),
        checkpointer=saver,
    )


# --------------------------------------------------------------------------
# the public call
# --------------------------------------------------------------------------

def _this_run(messages: list, question: str) -> list:
    """Only the messages this question produced. The thread holds older turns
    too, and counting those would inflate every metric on a follow-up."""
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], HumanMessage) and messages[i].content == question:
            return messages[i:]
    return messages


def ask(question: str, thread_id: str | None = None, recursion_limit: int = 12) -> dict:
    """Answer a founder's question from the vault. -> answer, alerts, metrics, turns.

    thread_id defaults to the company's UEN, which is what makes the memory
    per-company rather than per-process.
    """
    if thread_id is None:
        thread_id = str(vault._split(vault.VAULT / "company" / "profile.md")[0]["uen"])
    config = {"configurable": {"thread_id": thread_id},
              "recursion_limit": recursion_limit}
    metrics = Metrics(turn_limit=recursion_limit, tasks_attempted=1)

    try:
        state = graph().invoke({"messages": [HumanMessage(question)]}, config)
        messages, completed = state["messages"], True
    except GraphRecursionError:
        # the checkpoint holds everything up to the wall; truncated beats nothing
        messages, completed = graph().get_state(config).values["messages"], False

    messages = _this_run(messages, question)
    ai = [m for m in messages if isinstance(m, AIMessage)]
    for m in ai:
        metrics.usage(m.usage_metadata)
    for m in messages:
        if isinstance(m, ToolMessage):
            metrics.tool(_result(m))
    # one turn per node visit: the model, plus the tool batch it asked for
    metrics.turns_used = len(ai) + sum(1 for m in ai if m.tool_calls)

    answer = _text(ai[-1]) if ai else ""
    # validated by langgraph on the way out, so every alert here already carries
    # a .gov.sg resource -- Answer Fidelity is enforced, not merely measured
    found = state.get("structured_response") if completed else None
    alerts = list(found.findings) if found else []
    metrics.schema(found is not None)
    for a in alerts:
        metrics.claims += 1
        metrics.claims_cited += bool(GOV.match(str(a.resource)))
    metrics.tasks_completed = int(completed and bool(answer))
    return {"answer": answer, "alerts": alerts,
            "metrics": metrics, "turns": metrics.turns_used}


def render(out: dict) -> str:
    """An ask() result as text.

    The terminal output and the founder's email body are the same string. Two
    layouts drift apart; one cannot, and a finding that reaches the screen
    therefore reaches the inbox by construction.
    """
    total = sum(a.dollar_impact or 0 for a in out["alerts"])
    lines = [out["answer"], "", f"{len(out['alerts'])} findings"]
    if total:
        lines[-1] += f"  ·  S${total:,.0f} exposed"
    for a in out["alerts"]:
        cost = f"S${a.dollar_impact:,.0f}" if a.dollar_impact else "-"
        lines += ["",
                  f"  {a.headline}",
                  f"    who     {', '.join(a.who)}",
                  f"    lands   {a.deadline or 'standing threshold'}",
                  f"    cost    {cost}",
                  f"    do      {a.next_step}",
                  f"    source  {a.resource}"]
    return "\n".join(lines)


def show(out: dict) -> None:
    """Print an ask() result. Shared by __main__ and run.py."""
    print(render(out))
    print(f"\n{out['metrics'].report()}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")

    alive, detail = credentials_ok()
    if not alive:
        sys.exit(f"bedrock unavailable: {detail}")

    show(ask("what changes for us in the next 90 days?"))

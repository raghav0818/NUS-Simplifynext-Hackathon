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
4. Alerts are extracted by a second, separate call. One turn asked to both
   explain a situation to a founder and emit strict JSON does neither well.

    python -m ante.advisor
"""
from __future__ import annotations

import datetime as dt
import functools
import json
import pathlib
import re
import sqlite3
import sys

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.errors import GraphRecursionError
from langgraph.prebuilt import create_react_agent
from pydantic import ValidationError

import vault
from ante.metrics import Metrics
from ante.model import chat, credentials_ok
from ante.schema import GOV, Alert
from ante.tools import TOOLS

DB = pathlib.Path(__file__).resolve().parent.parent / "state.db"
TODAY = dt.date.today()

SYSTEM = f"""You are Ante. You warn Singapore startup founders about regulatory
obligations that are about to bite. Today is {TODAY}. Every figure is SGD.

Be concrete and short. A founder wants four things: who, how much, by when, and
what to do about it.

HOW TO WORK
1. vault_search first, with words from the question. It returns candidates only.
2. vault_read the rules that look relevant -- all of them in ONE turn, several
   tool calls in the same message. Your turn budget is small; batching is how it
   lasts. Never state a figure you have not read out of a rule note.
3. roster_scan each rule's `applies_to` value, copied verbatim from that rule's
   frontmatter. Batch these in one turn too.
4. Then answer.

THE COMPARISON IS YOURS TO MAKE
roster_scan returns everyone in a CATEGORY, not everyone the rule breaks. All
twelve staff match `all_employees`; almost none of them have a problem. For each
person it returns, compare that person's own number against the rule's threshold
yourself, and report ONLY the ones on the wrong side of it. Someone who already
complies is not a finding -- leave them out entirely, do not list them as
reassurance.

Which side is the wrong side depends on the rule, so read `# Who it hits`:
- a qualifying-salary floor bites people paid BELOW it
- a contribution ceiling bites people paid ABOVE the OLD ceiling
  (`threshold_before`), because that is the pay newly caught by the rise
- a `growth` threshold bites the company when its own figure is past the line or
  within about 20% of it: the obligation starts when a crossing is reasonably
  forecast, not when it happens
- a `recurring` rule always bites. Add its `threshold_after` months to the
  company's financial_year_end and give the next such date that has not passed.

One finding per rule per affected party. Do not split one rule into several
findings, and do not raise the same rule twice.

HORIZON
"The next 90 days" means what has to be acted on now, not what has an effective
date inside 90 days. Include anything already in force that has not been
adjusted for, anything landing within the next 18 months -- payroll changes,
pass renewals and tax registrations all need two quarters of lead time -- and
any standing threshold the company is close to. Always say the date each one
actually lands.

CITATIONS
Every claim cites the `resource` URL of the rule note it came from. The
`# Next step` section of a note is written by a human: quote it. Never invent
regulatory advice and never state a figure the vault does not contain.

Do not call vault_write unless the founder asks you to record something.

Finish with a numbered list, one line per finding, naming the person or the
company, their number, the threshold, the date it lands, and the URL."""

EXTRACT = """Turn the findings in the answer below into JSON and nothing else.

Output a JSON array, one object per finding, with exactly these keys:
  rule_path      the rule's vault path, taken from the table below
  headline       one sentence naming who is affected and the number
  who            list of names, e.g. ["Staff 04"] or ["Harborlight Analytics Pte Ltd"]
  deadline       "YYYY-MM-DD", or null for a standing threshold
  dollar_impact  annual SGD cost as a plain number, or null
  next_step      one sentence, from that rule's "# Next step"
  resource       copied character for character from the table below

rule_path -> resource
{table}

No markdown, no code fence, no commentary. No findings means [].
"""


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
    """rule path -> government URL, read straight from the vault.

    The extractor copies these rather than recalling them, which is why alerts
    validate against Alert.resource instead of failing on a hallucinated link.
    """
    return {vault._rel(p): vault._split(p)[0].get("resource")
            for p in vault._notes("rules")}


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
        chat(),
        TOOLS,
        prompt=SystemMessage(f"{SYSTEM}\n\n{load_context()}"),
        checkpointer=saver,
    )


# --------------------------------------------------------------------------
# alerts
# --------------------------------------------------------------------------

def _json_list(text: str):
    """The first JSON array in a model reply, or None if there isn't a readable
    one. None and [] are different answers: [] means the model found nothing,
    None means we could not read what it said."""
    match = re.search(r"\[.*]", text, re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, list) else None


def extract(answer: str, metrics: Metrics, retry: bool = True) -> list:
    """The answer's findings as validated Alerts. Invalid ones are dropped.

    A dropped alert is a recorded schema failure, not an exception -- an uncited
    or malformed alert must not reach a founder, but it must show up in the
    Schema Validation Pass Rate rather than take the run down with it.
    """
    table = "\n".join(f"  {p} -> {u}" for p, u in _resources().items())
    reply = chat(max_tokens=3000).invoke(
        [SystemMessage(EXTRACT.format(table=table)), HumanMessage(answer)])
    metrics.usage(reply.usage_metadata)

    found = _json_list(_text(reply))
    alerts = []
    for raw in found or []:
        if not isinstance(raw, dict):
            continue
        metrics.claims += 1
        metrics.claims_cited += bool(GOV.match(str(raw.get("resource", ""))))
        try:
            alerts.append(Alert(**raw))
            metrics.schema(True)
        except (ValidationError, TypeError):
            metrics.schema(False)

    # Unreadable, or everything in it failed validation -- one more go. An
    # explicit [] is a real answer ("nothing to report"), so it is not retried.
    if retry and not alerts and found != []:
        return extract(answer, metrics, retry=False)
    return alerts


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
    alerts = extract(answer, metrics) if answer else []
    metrics.tasks_completed = int(completed and bool(answer))
    return {"answer": answer, "alerts": alerts,
            "metrics": metrics, "turns": metrics.turns_used}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")

    alive, detail = credentials_ok()
    if not alive:
        sys.exit(f"bedrock unavailable: {detail}")

    out = ask("what changes for us in the next 90 days?")
    print(out["answer"], "\n")

    print(f"alerts  ->  {len(out['alerts'])} validated")
    for a in out["alerts"]:
        cost = f"S${a.dollar_impact:,.0f}" if a.dollar_impact else "-"
        print(f"\n  {a.headline}")
        print(f"    who     {', '.join(a.who)}")
        print(f"    lands   {a.deadline or 'standing threshold'}")
        print(f"    cost    {cost}")
        print(f"    do      {a.next_step}")
        print(f"    source  {a.resource}")

    print()
    print(out["metrics"].report())

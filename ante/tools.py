"""The tools both graphs share.

Docstrings here are not comments -- langchain-aws turns them into the tool
schema Bedrock reads, so they are prompts. Write them for the model.

Every tool RETURNS {"error": ...} rather than raising. An exception inside a
ToolNode kills the run; a returned error lets the model read what went wrong and
try something else. That difference is most of the Tool-Call Success Rate metric.
"""
from __future__ import annotations

from langchain_core.tools import tool

import vault as _vault
from ante import detect as _detect


@tool
def vault_search(query: str) -> dict:
    """Find Singapore rules relevant to a question.

    Returns each match's path, title, clock, effective date, who it applies to
    and severity -- a summary only, never the full rule. Call vault_read on the
    two or three paths that look most relevant. Search first, read second.
    """
    return _vault.vault_search(query)


@tool
def vault_read(path: str) -> dict:
    """Read one note in full, given a vault-relative path like
    'rules/gst-registration-threshold.md'.

    Returns its frontmatter (the structured facts: thresholds, dates, the
    government source URL) and its body. The body's '# Next step' section is
    written by a human -- quote it, never paraphrase or invent your own advice.
    """
    return _vault.vault_read(path)


@tool
def roster_scan(applies_to: str) -> dict:
    """Find who a rule actually affects.

    Pass the rule's `applies_to` value verbatim from its frontmatter. Valid
    values: s_pass_holder, ep_holder, entrepass_holder, all_employees,
    employees_over_55, employees_over_60, company.

    Returns everyone matching, with their salary, pass type, pass expiry and age
    band -- or the company's own figures when applies_to is 'company'. It returns
    everyone who matches the category, NOT everyone who breaches the rule.
    Comparing each person's number against the threshold is your job.
    """
    return _vault.roster_scan(applies_to)


@tool
def exposure() -> dict:
    """Who is actually on the wrong side of each rule. Call this FIRST.

    Does the whole join for you: every rule, the people or company it touches,
    and the arithmetic of who breaches it -- already applied. Returns only the
    parties genuinely affected, each with their own number, the threshold, the
    date it lands, and the government URL.

    This is the authoritative list of findings. Report every party it returns,
    and report no party it does not return. It is complete whatever window the
    founder asked about: a rule landing in 2028 still appears, because the pay
    review that fixes it happens now. Use vault_read for the wording of each
    rule's "# Next step" and its cost, not to second-guess who is affected.
    """
    return _vault.exposure()


@tool
def source_check(rule_path: str) -> dict:
    """Verify a rule's headline figure is still printed on its government page.

    Fetches the live page and looks for the rule's own threshold. Returns
    'confirmed' with the sentence it was found in, or 'missing', or
    'unverifiable' when the page is JavaScript-rendered and cannot be read.
    Use this when a figure matters enough to be worth confirming live.
    """
    return _detect.source_check(rule_path)


@tool
def vault_write(path: str, frontmatter: dict, body: str) -> dict:
    """Record an alert you have raised, or a counterparty you have checked.

    Path must start with 'alerts/' or 'counterparties/'. Frontmatter needs at
    minimum a 'type' of 'alert' or 'counterparty'. You cannot write to rules/,
    people/ or company/ -- those are human-owned and the attempt will be refused.
    """
    return _vault.vault_write(path, frontmatter, body)


#: Bound to the advisor graph. The curator does NOT get these -- its model is
#: given no tools at all, so it can only describe what it read.
TOOLS = [exposure, vault_search, vault_read, roster_scan, source_check,
         vault_write]

BY_NAME = {t.name: t for t in TOOLS}

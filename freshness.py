"""Ante's third clock: has the law moved since a human last read it?

Rules carry `verified:` -- the day a person read the government page. Nothing
ages that date, so a stale vault cites yesterday's law with today's confidence.
This checks each rule's own figure against its own source and reports one of:

    confirmed     the figure is still on the page, in context
    missing       page rendered fine, figure is gone -> a human must look
    unverifiable  the page is JavaScript-rendered; no bot can read it
    error         network or HTTP problem

It never edits a rule. vault_write refuses rules/ by design, so drift becomes an
alert for a human to confirm -- the model does not get to rewrite the law.

    python freshness.py
"""
import datetime as dt
import re
import sys

import requests

from vault import TODAY, _notes, _rel, _split, vault_write

UA = {"User-Agent": "Mozilla/5.0 (compatible; AnteComplianceBot/0.1)"}
STOPWORDS = {"from", "rises", "with", "within", "that", "than", "must", "and",
             "the", "for", "due", "aged", "date", "January", "workers", "year"}
NEAR = 300          # chars a figure may sit from a keyword and still count
STALE_AFTER = 90    # days before a human re-read is overdue


def _text(html):
    """Visible-ish text. Crude on purpose -- we only need figures and keywords."""
    html = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def _forms(fm):
    """Every way a government page might write this rule's figure.

    Two traps this avoids. A bare "7" matches "24/7" on any page carrying a
    helpline number, so small integers are only searched with their unit, and
    also spelled out -- ACRA writes "within seven months after FYE". But a rate
    table prints "16.5" with no % sign, so a decimal is distinctive enough to
    match bare.
    """
    n, unit = fm.get("threshold_after"), fm.get("unit")
    words = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six",
             7: "seven", 8: "eight", 9: "nine", 10: "ten", 11: "eleven",
             12: "twelve"}
    distinctive = n != int(n) or n >= 100   # bare match is safe above this bar

    if unit == "months":
        forms = {f"{n:g} months", f"{n:g} month"}
        if n in words:
            forms.add(f"{words[int(n)]} months")
        return forms
    if unit == "percent":
        forms = {f"{n:g}%", f"{n:g} per cent", f"{n:g} percent"}
        if distinctive:
            forms.add(f"{n:g}")
        return forms

    forms = {f"{n:,}"}
    if distinctive:
        forms.add(f"{n:g}")
    if isinstance(n, int) and n >= 1_000_000 and n % 1_000_000 == 0:
        m = n // 1_000_000
        forms |= {f"{m} million", f"{m}million"}
    return forms


def _keywords(fm):
    """Distinctive words from the rule's title, to anchor the figure in context."""
    words = re.findall(r"[A-Za-z]{4,}", str(fm.get("title", "")))
    return [w for w in words if w not in STOPWORDS] or ["the"]


def source_check(rule_path: str) -> dict:
    """Is this rule's headline figure still on its own government page?"""
    fm, _ = _split(rule_path)
    url, want = fm.get("resource"), fm.get("threshold_after")
    try:
        r = requests.get(url, headers=UA, timeout=25)
        r.raise_for_status()
    except Exception as exc:
        return {"status": "error", "detail": f"{type(exc).__name__}: {exc}", "url": url}

    text = _text(r.text)
    keywords = _keywords(fm)
    anchors = [m.start() for k in keywords
               for m in re.finditer(re.escape(k), text, re.I)]
    if not anchors:
        # the page never mentions its own subject -> we fetched a JS shell
        return {"status": "unverifiable", "url": url,
                "detail": f"page never mentions {keywords[:3]}; likely JS-rendered"}

    for form in _forms(fm):
        for m in re.finditer(re.escape(form), text):
            if any(abs(m.start() - a) < NEAR for a in anchors):
                s = max(0, m.start() - 90)
                return {"status": "confirmed", "url": url, "figure": want,
                        "found_as": form, "quote": text[s:m.start() + 110].strip()}

    return {"status": "missing", "url": url, "figure": want,
            "detail": f"none of {sorted(_forms(fm))} appears near {keywords[:3]}"}


def stale(days=STALE_AFTER) -> list:
    """Rules a human has not re-read recently. Staleness is a claim about us."""
    out = []
    for p in _notes("rules"):
        fm, _ = _split(p)
        verified = fm.get("verified")
        if isinstance(verified, str):
            verified = dt.date.fromisoformat(verified)
        age = (TODAY - verified).days
        if age > days:
            out.append({"path": _rel(p), "verified": str(verified), "days_old": age})
    return out


def sweep(write_alerts=False) -> dict:
    """Check every rule. Optionally raise a drift alert for each problem."""
    results = {}
    for p in _notes("rules"):
        fm, _ = _split(p)
        res = source_check(p)
        res["title"] = fm.get("title")
        results[_rel(p)] = res
        if write_alerts and res["status"] in ("missing", "unverifiable"):
            slug = p.stem
            vault_write(
                f"alerts/{TODAY}-drift-{slug}.md",
                {"type": "alert", "raised": str(TODAY), "rule": _rel(p),
                 "severity": "high" if res["status"] == "missing" else "medium",
                 "status": "needs_human_check", "drift": res["status"]},
                f"# What happened\n\n`{res['status']}` checking "
                f"[{fm.get('title')}]({_rel(p)}) against {res['url']}\n\n"
                f"{res.get('detail', res.get('quote', ''))}\n\n"
                f"# Next step\n\nA human must open the source and re-confirm the "
                f"figure, then update `verified:` in the rule note. "
                f"The agent may not edit rules/.\n",
            )
    return results


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    write = "--write-alerts" in sys.argv

    print(f"checking 6 rules against their own sources  ({TODAY})\n")
    results = sweep(write_alerts=write)
    icon = {"confirmed": "OK  ", "missing": "DRIFT", "unverifiable": "BLIND", "error": "ERR "}
    for path, r in results.items():
        print(f"  {icon[r['status']]:<6} {path}")
        if r["status"] == "confirmed":
            print(f"         found {r['figure']} as '{r['found_as']}'")
            print(f"         \"...{r['quote'][:110]}...\"")
        else:
            print(f"         {r['detail']}")
        print()

    counts = {}
    for r in results.values():
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print("  ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    overdue = stale()
    print(f"\nhuman re-read overdue (>{STALE_AFTER}d): "
          f"{[s['path'] for s in overdue] or 'none'}")

    assert counts.get("confirmed", 0) >= 2, "source checking is not working at all"
    if write:
        print("\ndrift alerts written to vault/alerts/")

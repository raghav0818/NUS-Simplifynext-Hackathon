"""Tier 1: has a rule's source page changed? No AWS, no LLM, no tokens.

Runs on every rule every day. Hashes only the region of the page around the
rule's own keywords, because a whole-page hash fires on cookie banners and
rotating footers and would wake the model for nothing.

Also carries source_check(), the cheap "is the figure still literally there"
test, which doubles as a tool the advisor can call mid-answer.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import pathlib
import re

import requests

from vault import VAULT, _rel, _split

UA = {"User-Agent": "Mozilla/5.0 (compatible; AnteComplianceBot/0.1)"}
SNAPSHOTS = VAULT / ".snapshots"
PAD = 2000          # chars either side of the keyword cluster
MAX_ANCHORS = 200   # ponytail: O(n^2) anchor scoring; cap it rather than index
TIMEOUT = 25

STOPWORDS = {"from", "rises", "with", "within", "that", "than", "must", "and",
             "the", "for", "due", "aged", "date", "January", "workers", "year",
             "above", "becomes", "returns"}
WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six",
         7: "seven", 8: "eight", 9: "nine", 10: "ten", 11: "eleven", 12: "twelve"}


def _text(html: str) -> str:
    """Visible-ish text. Crude on purpose -- we need figures and keywords only."""
    html = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def keywords(fm: dict) -> list:
    """Distinctive words from the rule's title, anchoring both hash and match."""
    words = re.findall(r"[A-Za-z]{4,}", str(fm.get("title", "")))
    return [w for w in words if w not in STOPWORDS] or ["the"]


def forms(fm: dict) -> set:
    """Every way a government page might write this rule's figure.

    Two traps this avoids. A bare "7" matches "24/7" on any page with a helpline
    number, so small integers are searched only with their unit, and also spelled
    out -- ACRA writes "within seven months after FYE". But a rate table prints
    "16.5" with no % sign, so a decimal is distinctive enough to match bare.
    """
    return forms_for(fm.get("threshold_after"), fm.get("unit"))


def forms_for(n, unit) -> set:
    """forms() for an arbitrary value -- used to gate a proposed NEW figure."""
    whole = n == int(n)
    plain = str(int(n)) if whole else f"{n:g}"      # 1000000, not 1e+06
    comma = f"{int(n):,}" if whole else f"{n:,}"    # 3,800 -- not 3,800.0
    distinctive = not whole or n >= 100

    if unit == "months":
        out = {f"{plain} months", f"{plain} month"}
        if int(n) in WORDS:
            out.add(f"{WORDS[int(n)]} months")
        return out
    if unit == "percent":
        out = {f"{plain}%", f"{plain} per cent", f"{plain} percent"}
        if distinctive:
            out.add(plain)
        return out

    out = {comma}
    if distinctive:
        out.add(plain)
    if whole and int(n) >= 1_000_000 and int(n) % 1_000_000 == 0:
        m = int(n) // 1_000_000
        out |= {f"{m} million", f"{m}million"}
    return out


def region(text: str, kws: list, pad: int = PAD):
    """The slice of page most densely about this rule. None if never mentioned."""
    anchors = [(m.start(), k) for k in kws
               for m in re.finditer(re.escape(k), text, re.I)][:MAX_ANCHORS]
    if not anchors:
        return None
    best, best_score = anchors[0][0], -1
    for pos, _ in anchors:
        score = len({k for p, k in anchors if abs(p - pos) <= pad})
        if score > best_score:
            best, best_score = pos, score
    return text[max(0, best - pad): best + pad]


def watch_url(fm: dict) -> str:
    """What the bot reads. watch_also wins when the canonical page is JS-rendered."""
    return fm.get("watch_also") or fm.get("resource")


def digest(s: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", " ", s).strip().encode("utf-8")).hexdigest()[:16]


def _slug(rule_path) -> str:
    return pathlib.Path(rule_path).stem


def _snap_file(slug: str) -> pathlib.Path:
    return SNAPSHOTS / f"{slug}.json"


def load_snapshot(slug: str):
    f = _snap_file(slug)
    return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else None


def save_snapshot(slug: str, url: str, reg: str) -> dict:
    SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    snap = {"slug": slug, "url": url, "hash": digest(reg),
            "region": reg, "checked": str(dt.date.today())}
    _snap_file(slug).write_text(json.dumps(snap, indent=2), encoding="utf-8")
    return snap


def fetch(url: str) -> dict:
    try:
        r = requests.get(url, headers=UA, timeout=TIMEOUT)
        r.raise_for_status()
        return {"text": _text(r.text), "url": url}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}", "url": url}


def detect_one(rule_path) -> dict:
    """Did this rule's source move? The whole of Tier 1, for one rule."""
    path = pathlib.Path(rule_path)
    fm, _ = _split(path)
    slug, url = _slug(path), watch_url(fm)
    rel = _rel(path)
    got = fetch(url)
    if "error" in got:
        return {"slug": slug, "path": rel, "status": "error", "changed": False,
                "detail": got["error"], "url": url}

    kws = keywords(fm)
    new_region = region(got["text"], kws)
    if new_region is None:
        return {"slug": slug, "path": rel, "status": "unverifiable", "changed": False,
                "url": url, "detail": f"page never mentions {kws[:3]}; likely JS-rendered"}

    old = load_snapshot(slug)
    new_hash = digest(new_region)
    if old is None:
        save_snapshot(slug, url, new_region)
        return {"slug": slug, "path": rel, "status": "baseline", "changed": False,
                "hash": new_hash, "url": url, "detail": "first run -- snapshot recorded"}

    changed = old.get("hash") != new_hash
    return {"slug": slug, "path": rel,
            "status": "changed" if changed else "unchanged", "changed": changed,
            "hash": new_hash, "old_hash": old.get("hash"), "url": url,
            "old_region": old.get("region", ""), "new_region": new_region,
            "page_text": got["text"]}


def source_check(rule_path: str) -> dict:
    """Is this rule's headline figure still printed on its own government page?

    Cheap, deterministic, no LLM. Returns confirmed / missing / unverifiable /
    error, plus the sentence the figure was found in.
    """
    p = pathlib.Path(rule_path)
    if not p.is_absolute():
        p = VAULT / str(rule_path).replace("\\", "/")
    if not p.is_file():
        return {"error": f"no rule at {rule_path}"}

    fm, _ = _split(p)
    url = watch_url(fm)
    got = fetch(url)
    if "error" in got:
        return {"status": "error", "url": url, "detail": got["error"]}

    text, kws = got["text"], keywords(fm)
    anchors = [m.start() for k in kws for m in re.finditer(re.escape(k), text, re.I)]
    if not anchors:
        return {"status": "unverifiable", "url": url,
                "detail": f"page never mentions {kws[:3]}; likely JS-rendered"}

    for form in forms(fm):
        for m in re.finditer(re.escape(form), text):
            if any(abs(m.start() - a) < 300 for a in anchors):
                s = max(0, m.start() - 90)
                return {"status": "confirmed", "url": url,
                        "figure": fm.get("threshold_after"), "found_as": form,
                        "quote": text[s:m.start() + 110].strip()}

    return {"status": "missing", "url": url, "figure": fm.get("threshold_after"),
            "detail": f"none of {sorted(forms(fm))} appears near {kws[:3]}"}

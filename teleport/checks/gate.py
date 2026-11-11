"""The evidence gate. Deterministic code, so the LLM (or a prompt injection in the data) cannot
talk its way past it. Each proposed change becomes verified, flagged, or dropped as cosmetic."""
import re

from .. import config
from .. import plan as planlib
from .domains import domain, name_match


def _kind(field):
    return next((c["kind"] for c in planlib.load()["checks"] if c["field"] == field), "text")


def is_cosmetic(issue):
    """http->https, www, trailing slash, case: same value, not worth a human's time."""
    old, new = str(issue.get("old") or "").strip(), str(issue.get("new") or "").strip()
    if not issue.get("field") or old.lower() == new.lower():
        return True
    return _kind(issue["field"]) == "url" and domain(new) == domain(old) != ""


def evidence_texts(steps):
    texts = {}
    for r in steps["search"].get("results") or []:
        if r.get("url"):
            texts[r["url"]] = f"{r.get('title', '')} {r.get('snippet', '')}"
    f = steps["fetch"]
    if f.get("url") and f.get("content"):
        texts[f["url"]] = texts.get(f["url"], "") + f" {f.get('title', '')} {f['content']}"
    return texts


def check(issue, steps, record):
    """Return (verified: bool, why: str).
    1. Every cited URL must be one Keenable actually returned in this run (no invented evidence).
    2. Confidence must clear the bar.
    3. Cited evidence must name THIS entity (every distinctive word of its name), not a lookalike.
    4. URL fields: the new URL's domain must be in the evidence and cited, and the current URL must
       not itself be confirmed alive by the evidence.
    """
    plan = planlib.load()
    name = planlib.record_name(plan, record)
    field, kind = issue.get("field"), _kind(issue.get("field"))
    texts = evidence_texts(steps)
    cited = [u for u in (issue.get("evidence_urls") or []) if u in texts]
    if not cited:
        return False, "no cited URL was returned by Keenable"
    if float(issue.get("confidence") or 0) < config.MIN_CONFIDENCE:
        return False, f"confidence {issue.get('confidence')} < {config.MIN_CONFIDENCE}"
    best = max(name_match(name, texts[u]) for u in cited)
    if name and best < config.MIN_NAME_MATCH:
        return False, f"evidence does not name this {plan['entity']} (best name match {best:.0%})"
    if kind == "url":
        new_d = domain(issue.get("new"))
        old_d = domain(issue.get("old") or planlib.get(record, field))
        if not new_d or new_d not in {domain(u) for u in texts}:
            return False, f"proposed domain {new_d!r} not in Keenable results"
        if not any(domain(u) == new_d for u in cited):
            return False, "no cited evidence on the proposed domain"
        pr = steps["probe"]
        old_alive = pr.get("status") in ("ok", "blocked") and not pr.get("redirected_offsite")
        old_indexed = old_d and any(domain(u) == old_d and name_match(name, t) >= config.MIN_NAME_MATCH
                                    for u, t in texts.items())
        if old_alive or old_indexed:
            return False, f"current URL {old_d} still looks live ({'probe ok' if old_alive else 'indexed by Keenable'})"
    if kind == "category" and str(issue.get("new")).lower() in ("other", "none", "unknown", ""):
        return False, "refusing to downgrade to a catch-all category"
    if kind == "country":
        old = str(issue.get("old") or planlib.get(record, field) or "")
        if re.fullmatch(r"[A-Z]{2}", old) and not re.fullmatch(r"[A-Z]{2}", str(issue.get("new"))):
            return False, "country must keep the ISO-2 format"
    return True, f"{len(cited)} Keenable URL(s) support it, name match {best:.0%}"

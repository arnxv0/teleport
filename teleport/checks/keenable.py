"""Steps 2 and 3: Keenable web search and page fetch. Uses the keyless public endpoint if no key."""
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from .. import config
from . import http
from .. import plan as planlib


def _headers():
    return {"X-API-Key": config.KEENABLE_KEY} if config.KEENABLE_KEY else {"X-Keenable-Title": "teleport"}


def _path(p):
    return config.KEENABLE_URL + (p if config.KEENABLE_KEY else p + "/public")


def _one(q):
    res = http.request("POST", _path("/v1/search"), _headers(), {"query": q})
    return [{"url": r.get("url"), "title": (r.get("title") or "")[:160],
             "snippet": (r.get("snippet") or r.get("description") or "")[:400]}
            for r in (res.get("results") or [])[:5]]


def search(record, _steps):
    """One targeted Keenable search per field being verified, in parallel; evidence is tagged with
    the field(s) it was searched for and merged (deduplicated by URL)."""
    queries = planlib.search_queries(planlib.load(), record)
    answers = [_one(q) for q in queries]  # sequential: a 50-agent swarm must stay under Keenable's rate limit
    merged = {}
    for (q, fields), results in zip(queries.items(), answers):
        for r in results:
            m = merged.setdefault(r["url"], {**r, "for_fields": []})
            m["for_fields"] = sorted(set(m["for_fields"]) | set(fields))
    return {"query": next(iter(queries)), "queries": [{"query": q, "fields": f} for q, f in queries.items()],
            "results": list(merged.values())[:12]}


def candidate(steps):
    """Page to read for country/type: the live current homepage, else Keenable's top hit."""
    pr = steps["probe"]
    if pr.get("status") == "ok" and pr.get("final_url"):
        return pr["final_url"]
    rs = steps["search"].get("results") or []
    url_fields = {c["field"] for c in planlib.load()["checks"] if c["kind"] == "url"}
    aimed = [r for r in rs if url_fields & set(r.get("for_fields") or [])]  # hits from the URL-targeted search
    return (aimed or rs)[0]["url"] if rs else None


def fetch(_record, steps):
    url = candidate(steps)
    if not url:
        return {"url": None, "content": ""}
    try:
        res = http.request("GET", _path("/v1/fetch") + "?" + urllib.parse.urlencode({"url": url}),
                           _headers(), retries=2)
        return {"url": res.get("url") or url, "title": res.get("title", ""),
                "content": (res.get("content") or "")[:3500]}
    except RuntimeError as e:
        return {"url": url, "content": "", "error": str(e)[:200]}

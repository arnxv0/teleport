"""Tiny JSON-over-HTTP client with retries (stdlib only, so workers need no pip install)."""
import json
import random
import socket
import ssl
import time
import urllib.error
import urllib.request

from .. import config

CTX = ssl.create_default_context()


def request(method, url, headers=None, body=None, timeout=20, retries=3, rate_retries=8):
    data = json.dumps(body).encode() if body is not None else None
    h = {"User-Agent": config.USER_AGENT, **(headers or {})}
    if data is not None:
        h["Content-Type"] = "application/json"
    attempt, rate_hits = -1, 0
    while attempt < retries - 1:
        attempt += 1
        req = urllib.request.Request(url, data=data, headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429 and rate_hits < rate_retries:
                # Rate limited: back off (honour Retry-After) with jitter so a big swarm doesn't
                # retry in lockstep. Doesn't consume the normal retry budget.
                rate_hits += 1
                wait = float(e.headers.get("Retry-After") or 0) or min(30.0, 1.5 * 2 ** rate_hits)
                time.sleep(wait * random.uniform(0.7, 1.4))
                attempt -= 1
                continue
            if e.code in (500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            raise RuntimeError(f"{method} {url.split('?')[0]} -> HTTP {e.code}: {e.read()[:200]!r}")
        except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
            if attempt < retries - 1:
                time.sleep(1 + attempt)
                continue
            raise RuntimeError(f"{method} {url.split('?')[0]} -> {e}")

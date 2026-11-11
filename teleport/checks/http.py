"""Tiny JSON-over-HTTP client with retries (stdlib only, so workers need no pip install)."""
import json
import socket
import ssl
import time
import urllib.error
import urllib.request

from .. import config

CTX = ssl.create_default_context()


def request(method, url, headers=None, body=None, timeout=20, retries=3):
    data = json.dumps(body).encode() if body is not None else None
    h = {"User-Agent": config.USER_AGENT, **(headers or {})}
    if data is not None:
        h["Content-Type"] = "application/json"
    for attempt in range(retries):
        req = urllib.request.Request(url, data=data, headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(float(e.headers.get("Retry-After") or 2 * (attempt + 1)))
                continue
            raise RuntimeError(f"{method} {url.split('?')[0]} -> HTTP {e.code}: {e.read()[:200]!r}")
        except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
            if attempt < retries - 1:
                time.sleep(1 + attempt)
                continue
            raise RuntimeError(f"{method} {url.split('?')[0]} -> {e}")

"""Step 1: is the recorded homepage alive, and where does it end up?

Statuses: ok | dead (DNS failure, refused, 404/410/5xx) | timeout (inconclusive) |
          blocked (401/403/429: bot-blocking, site is up) | tls_error | missing
"""
import socket
import ssl
import urllib.error
import urllib.request

from .. import config
from .domains import domain
from .http import CTX
from .. import plan as planlib


def url_field(plan):
    return next((c["field"] for c in plan["checks"] if c["kind"] == "url"), None)


def run(record, _steps):
    field = url_field(planlib.load())
    if not field:
        return {"status": "n/a", "final_url": None, "code": None}
    url = planlib.get(record, field)
    if isinstance(url, list):  # e.g. "web_pages": ["https://..."]
        url = url[0] if url else None
    if url and "://" not in str(url):
        url = "http://" + str(url)
    if not url:
        return {"status": "missing", "final_url": None, "code": None}
    req = urllib.request.Request(url, headers={"User-Agent": config.USER_AGENT}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10, context=CTX) as r:
            final = r.geturl()
            return {"status": "ok", "code": r.status, "final_url": final,
                    "redirected_offsite": domain(final) != domain(url)}
    except urllib.error.HTTPError as e:
        status = "dead" if e.code in (404, 410) or e.code >= 500 else "blocked"
        return {"status": status, "code": e.code, "final_url": e.geturl()}
    except ssl.SSLError as e:
        return {"status": "tls_error", "code": None, "final_url": None, "error": str(e)[:120]}
    except (socket.timeout, TimeoutError) as e:
        return {"status": "timeout", "code": None, "final_url": None, "error": str(e)[:120]}
    except urllib.error.URLError as e:
        if isinstance(e.reason, (socket.timeout, TimeoutError)):
            return {"status": "timeout", "code": None, "final_url": None, "error": str(e)[:120]}
        return {"status": "dead", "code": None, "final_url": None, "error": str(e)[:120]}
    except Exception as e:
        return {"status": "dead", "code": None, "final_url": None, "error": str(e)[:120]}

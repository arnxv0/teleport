"""URL and name normalisation helpers."""
import re
import urllib.parse

_SLD = {"ac", "co", "com", "edu", "gov", "org", "net", "or", "go", "ne", "res", "nic", "mil", "sch"}
_STOP = {"of", "the", "and", "for", "de", "du", "la", "le", "des", "del", "di", "da", "at", "in"}


def domain(url):
    """Registrable domain: https://www.unilag.edu.ng/x -> unilag.edu.ng"""
    if not url:
        return ""
    host = urllib.parse.urlparse(url if "://" in url else "http://" + url).hostname or ""
    host = host.lower().removeprefix("www.")
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in _SLD and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def tokens(s):
    return {t for t in re.findall(r"[a-z0-9]+", (s or "").lower()) if len(t) >= 3 and t not in _STOP}


def name_match(name, text):
    """Fraction of the name's distinctive words present in text."""
    nt = tokens(name)
    return len(nt & tokens(text)) / len(nt) if nt else 0.0

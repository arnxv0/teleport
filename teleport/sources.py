"""Read rows from a public dataset URL: CSV / TSV / JSON / JSONL, optionally gzipped, over https or s3://."""
import csv
import gzip
import io
import json
import re
import urllib.request

from . import config


def to_https(url):
    """s3://bucket/key -> public https URL (no AWS account needed for public buckets)."""
    m = re.match(r"s3://([^/]+)/(.+)", url)
    return f"https://{m.group(1)}.s3.amazonaws.com/{m.group(2)}" if m else url


def _open(url):
    req = urllib.request.Request(to_https(url), headers={"User-Agent": config.USER_AGENT})
    raw = io.BufferedReader(urllib.request.urlopen(req, timeout=120))
    head = raw.peek(2)[:2]
    if url.endswith(".gz") or head == b"\x1f\x8b":
        return io.BufferedReader(gzip.GzipFile(fileobj=raw))
    return raw


def _looks_like_data(cells):
    """A header row has no URLs, emails or long numbers."""
    return any(re.search(r"https?://|www\.|@|^\d{4,}$|\.\w{2,3}/?$", c or "") for c in cells)


def _csv_rows(lines, limit, delimiter):
    reader = csv.reader(lines, delimiter=delimiter)
    first = next(reader)
    header = [f"col_{i}" for i in range(len(first))] if _looks_like_data(first) else \
        [h.strip() or f"col_{i}" for i, h in enumerate(first)]
    rows = [] if header[0] != "col_0" else [dict(zip(header, first))]
    for cells in reader:
        if len(rows) >= limit:
            break
        if any(c.strip() for c in cells):
            rows.append(dict(zip(header, cells)))
    return rows


def read_rows(url, limit=200):
    """Return up to `limit` rows as dicts. Streams, so huge files are fine."""
    f = _open(url)
    peek = f.peek(4096)[:4096].decode("utf-8", "replace").lstrip()
    path = to_https(url).split("?")[0].lower().removesuffix(".gz")
    text = io.TextIOWrapper(f, encoding="utf-8", errors="replace", newline="")
    if path.endswith((".jsonl", ".ndjson")) or (peek.startswith("{") and "\n{" in peek):
        rows = []
        for line in text:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
            if len(rows) >= limit:
                break
    elif path.endswith(".json") or peek.startswith(("[", "{")):
        data = json.loads(text.read(50_000_000))
        if isinstance(data, dict):  # {"results": [...]} style envelopes
            data = next((v for v in data.values() if isinstance(v, list)), [data])
        rows = [r for r in data if isinstance(r, dict)][:limit]
    else:
        delim = "\t" if path.endswith(".tsv") or peek.count("\t") > peek.count(",") else ","
        rows = _csv_rows(text, limit, delim)
    for i, r in enumerate(rows):
        r["_row"] = i
    return rows


def inspect(url, cap=200_000):
    """Format, total row count (streamed, capped) and columns of a dataset, for the homepage."""
    f = _open(url)
    peek = f.peek(4096)[:4096].decode("utf-8", "replace").lstrip()
    path = to_https(url).split("?")[0].lower().removesuffix(".gz")
    text = io.TextIOWrapper(f, encoding="utf-8", errors="replace", newline="")
    if path.endswith((".jsonl", ".ndjson")) or (peek.startswith("{") and "\n{" in peek):
        fmt, n, first = "JSONL", 0, None
        for line in text:
            if line.strip():
                n += 1
                if first is None:
                    first = json.loads(line)
                if n >= cap:
                    break
        cols = list(first.keys()) if isinstance(first, dict) else []
    elif path.endswith(".json") or peek.startswith(("[", "{")):
        data = json.loads(text.read(50_000_000))
        if isinstance(data, dict):
            data = next((v for v in data.values() if isinstance(v, list)), [data])
        rows = [r for r in data if isinstance(r, dict)]
        fmt, n, cols = "JSON", len(rows), (list(rows[0].keys()) if rows else [])
    else:
        delim = "\t" if path.endswith(".tsv") or peek.count("\t") > peek.count(",") else ","
        reader = csv.reader(text, delimiter=delim)
        first = next(reader, [])
        has_header = not _looks_like_data(first)
        cols = [h.strip() or f"col_{i}" for i, h in enumerate(first)] if has_header else [f"col_{i}" for i in range(len(first))]
        n = 0 if has_header else 1
        for cells in reader:
            if any(c.strip() for c in cells):
                n += 1
                if n >= cap:
                    break
        fmt = "TSV" if delim == "\t" else "CSV"
    return {"format": fmt + (" (gzip)" if url.endswith(".gz") else ""), "rows": n, "capped": n >= cap,
            "columns": cols[:40]}


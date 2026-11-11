"""Apply human-accepted fixes onto a cleaned copy of the data. raw/ is never written.

  python3 -m teleport.apply_fixes                local disk (TELEPORT_ROOT, default ./disk)
  python3 -m teleport.apply_fixes --root DIR      any local dir, e.g. the "cleaned" branch mount
  python3 -m teleport.apply_fixes --archil        the Archil disk in .teleport.json, via the object API
  python3 -m teleport.apply_fixes --branch        checkpoint "raw" + branch "cleaned" (SDK where possible)

Reads:   raw/institutions.jsonl, fixes/<fix_id>.json, accepted/<fix_id>.json
Writes:  cleaned/institutions.jsonl   raw + accepted fixes, each changed record carries
                                      _teleport_provenance [{fix_id, field, old, new, evidence_urls}]
         cleaned/diff.json            the list of changes, with evidence
Output depends only on the inputs (no timestamps, stable ordering), so reruns are byte-identical.
"""
import argparse
import json
import uuid
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
STATE = os.path.join(REPO, ".teleport.json")
RAW = "raw/institutions.jsonl"
OUT = "cleaned/institutions.jsonl"
DIFF = "cleaned/diff.json"
REGION = os.environ.get("ARCHIL_REGION", "aws-us-east-1")


# ---------------------------------------------------------------- storage backends

class LocalStore:
    def __init__(self, root):
        self.root = os.path.abspath(root)

    def get(self, key):
        try:
            with open(os.path.join(self.root, key), "rb") as f:
                return f.read()
        except FileNotFoundError:
            return None

    def list(self, prefix):
        d = os.path.join(self.root, prefix)
        try:
            return sorted(prefix + f for f in os.listdir(d) if ".tmp-" not in f)
        except FileNotFoundError:
            return []

    def put(self, key, data, content_type):
        path = os.path.join(self.root, key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.tmp-{os.getpid()}"
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)  # readers see the old or the new file, never a partial one

    def where(self):
        return self.root


class ArchilStore:
    def __init__(self, disk_id):
        import archil
        archil.configure(api_key=os.environ.get("ARCHIL_API_KEY"), region=REGION)
        self.disk_id = disk_id
        self.disk = archil.get_disk(disk_id)

    def get(self, key):
        try:
            return self.disk.get_object(key)
        except Exception as e:  # ArchilS3Error 404 NoSuchKey -> missing; anything else is real
            if "NoSuchKey" in str(e) or ": 404" in str(e) or getattr(e, "status", None) == 404:
                return None
            raise

    def list(self, prefix):
        out, token = [], None
        while True:
            r = self.disk.list_objects(prefix, recursive=True, continuation_token=token)
            out += [o.key for o in r.objects if ".tmp-" not in o.key]
            if not r.is_truncated:
                return sorted(out)
            token = r.next_continuation_token

    def put(self, key, data, content_type):
        self.disk.put_object(key, data, content_type)

    def where(self):
        return f"archil:{self.disk_id}"


def disk_id_from_state():
    try:
        with open(STATE) as f:
            did = json.load(f).get("disk_id")
    except FileNotFoundError:
        did = None
    if not did:
        sys.exit(f"no disk_id in {STATE}; run `python3 -m teleport.swarm archil setup` first")
    return did


# ---------------------------------------------------------------- core logic (backend-agnostic)

def _load_json(store, key):
    raw = store.get(key)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _ids(store, prefix):
    return sorted(k.rsplit("/", 1)[-1][:-5] for k in store.list(prefix) if k.endswith(".json"))


def build(store):
    """Return (cleaned_jsonl_bytes, diff_list, warnings). Pure function of what is on the disk."""
    raw = store.get(RAW)
    if raw is None:
        sys.exit(f"{RAW} not found in {store.where()}")
    records = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
    by_id = {r["id"]: r for r in records}

    warnings, fixes = [], []
    for fid in _ids(store, "accepted/"):
        fix = _load_json(store, f"fixes/{fid}.json")
        if fix is None:  # accepted something that is not a verified fix (e.g. a flag): refuse
            warnings.append(f"accepted/{fid}: no verified fixes/{fid}.json, skipped")
            continue
        if fix.get("record_id") not in by_id:
            warnings.append(f"fix {fid}: record {fix.get('record_id')} not in raw, skipped")
            continue
        fixes.append(fix)

    diff = []
    for fix in sorted(fixes, key=lambda f: (f["record_id"], f["field"], f["fix_id"])):
        rec = by_id[fix["record_id"]]
        field = fix["field"]
        current = rec.get(field)
        if field in rec and current != fix.get("old"):
            warnings.append(f"fix {fix['fix_id']}: raw {field} is {current!r}, fix expected "
                            f"{fix.get('old')!r}; applied anyway")
        rec[field] = fix["new"]
        entry = {"fix_id": fix["fix_id"], "field": field, "old": current, "new": fix["new"],
                 "evidence_urls": fix.get("evidence_urls") or []}
        rec.setdefault("_teleport_provenance", []).append(entry)
        diff.append({"record_id": rec["id"], "name": rec.get("display_name"), **entry,
                     "reason": fix.get("reason"), "gate": fix.get("gate")})

    body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records).encode()
    return body, diff, warnings


def render(diff):
    lines = [f"--- a/{RAW}", f"+++ b/{OUT}"]
    for d in diff:
        lines.append(f"@@ {d['record_id']}  {d['name']} @@")
        lines.append(f"-  {d['field']}: {json.dumps(d['old'], ensure_ascii=False)}")
        lines.append(f"+  {d['field']}: {json.dumps(d['new'], ensure_ascii=False)}")
        for u in d["evidence_urls"]:
            lines.append(f"   evidence: {u}")
    records = len({d["record_id"] for d in diff})
    lines.append(f"{len(diff)} change(s) across {records} record(s)")
    return "\n".join(lines)


def apply(store, dry_run=False):
    body, diff, warnings = build(store)
    for w in warnings:
        print("warning:", w, file=sys.stderr)
    print(render(diff))
    if dry_run:
        print("(dry run: nothing written)")
        return diff
    store.put(OUT, body, "application/x-ndjson")
    store.put(DIFF, (json.dumps(diff, indent=2, ensure_ascii=False) + "\n").encode(), "application/json")
    print(f"wrote {OUT} and {DIFF} to {store.where()}")
    return diff


# ---------------------------------------------------------------- branches

def branch(checkpoint="raw", name="cleaned", mount="/mnt/archil"):
    """Checkpoint `raw` + branch `cleaned`. The SDK (archil 0.21) can create and list branches but
    has no checkpoint-create call, so the checkpoint step is the CLI against a mounted disk."""
    try:
        with open(STATE) as f:
            did = json.load(f).get("disk_id")
    except FileNotFoundError:
        did = None
    if not did:
        print("# no disk_id in .teleport.json yet: commands below use a placeholder\n")
    did = did or "dsk-XXXX"
    print("# 1) checkpoint the ingested data (CLI only; disk must be mounted):")
    print(f"sudo archil mount {did} {mount} --region {REGION}")
    print(f"archil checkpoints create {mount} {checkpoint}")
    print("# 2) branch from it:")
    print(f"archil branches --region {REGION} create {did} {name} --from-checkpoint {checkpoint}")
    print("# 3) mount the branch and apply accepted fixes there (raw on the main disk stays untouched):")
    print(f"sudo archil mount {did} /mnt/{name} --region {REGION} --branch {name}")
    print(f"python3 -m teleport.apply_fixes --root /mnt/{name}")
    print()
    if did == "dsk-XXXX":
        return
    try:
        import archil
        archil.configure(api_key=os.environ.get("ARCHIL_API_KEY"), region=REGION)
        disk = archil.get_disk(did)
        existing = {b.branch_name: b for b in disk.list_branches()}
        if name in existing:
            b = existing[name]
        else:
            b = disk.create_branch(name, checkpoint, idempotency_key=str(uuid.uuid5(uuid.NAMESPACE_URL, f"teleport-{did}-{name}-{checkpoint}")))
        print(f"branch {b.branch_name!r} from checkpoint {b.from_checkpoint_name!r}: "
              f"filesystem {b.filesystem_id}")
    except Exception as e:
        print(f"SDK branch step not done ({type(e).__name__}: {e}).")
        print("If the checkpoint is missing or not committed yet, run step 1 and retry.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", help="local disk root (default: TELEPORT_ROOT or ./disk)")
    ap.add_argument("--archil", action="store_true", help="read/write the Archil disk via the object API")
    ap.add_argument("--branch", action="store_true", help="print + run checkpoint/branch steps, then exit")
    ap.add_argument("--dry-run", action="store_true", help="print the diff without writing")
    a = ap.parse_args()
    if a.branch:
        branch()
    elif a.archil:
        apply(ArchilStore(disk_id_from_state()), a.dry_run)
    else:
        root = a.root or os.environ.get("TELEPORT_ROOT") or os.path.join(REPO, "disk")
        apply(LocalStore(root), a.dry_run)

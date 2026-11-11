"""Durable file primitives for a shared file system (Archil mount, or a local dir in dev).

Disk layout (relative to config.ROOT):
  raw/institutions.jsonl        the ingested slice (checkpoint "raw", never modified)
  tasks/manifest.json           every task id (claiming never depends on cached dir listings)
  tasks/todo|claimed|done/      task lifecycle; claim = atomic rename todo -> claimed
  tasks/requeued.json           tids handed back by a reaper (read via open(): strongly consistent)
  claims/<tid>.<gen>            O_EXCL claim marker: the real arbiter of who owns a task
  leases/<tid>.json             {worker, machine, epoch, expires_at}
  checkpoints/<tid>.json        per-step outputs, so another machine resumes mid-task
  verdicts/<tid>.json           write-once outcome per record (the "no lost work" proof)
  fixes/<fix_id>.json           write-once verified fix with evidence
  flags/<fix_id>.json           write-once proposed change that lacked evidence
  accepted/<fix_id>.json        human review decisions
  log/<worker>.jsonl            per-worker append-only events
  workers/<worker>.json         heartbeat: machine, task, step
"""
import json
import os
import uuid

from . import config

DIRS = ["raw", "tasks/todo", "tasks/claimed", "tasks/done", "claims", "leases", "checkpoints",
        "verdicts", "fixes", "flags", "log", "workers", "accepted"]


def p(*parts):
    return os.path.join(config.ROOT, *parts)


def ensure_dirs():
    for d in DIRS:
        os.makedirs(p(d), exist_ok=True)


def _fsync_dir(path):
    try:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def _write_tmp(path, data):
    tmp = f"{path}.tmp-{uuid.uuid4().hex[:8]}"
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    return tmp


def write_atomic(rel, obj):
    """Durable replace: tmp + fsync + rename. Readers see old or new, never partial."""
    path = p(rel)
    tmp = _write_tmp(path, json.dumps(obj).encode())
    os.replace(tmp, path)
    _fsync_dir(os.path.dirname(path))


def write_once(rel, obj):
    """Create rel only if absent. True if we created it, False if it already existed.

    O_CREAT|O_EXCL is the one primitive our Archil smoke test showed to be exclusive across
    clients on a conditional mount (concurrent link() and rename() can both "succeed").
    """
    path = p(rel)
    created = _create_excl(path, json.dumps(obj).encode())
    _fsync_dir(os.path.dirname(path))
    return created


def _create_excl(path, data):
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return False
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    return True


def append_line(rel, line):
    fd = os.open(p(rel), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, line.encode())
        os.fsync(fd)
    finally:
        os.close(fd)


def read_json(rel, default=None):
    try:
        with open(p(rel), "rb") as f:
            return json.loads(f.read() or b"null")
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def exists(rel):
    return os.path.exists(p(rel))


def remove(rel):
    try:
        os.unlink(p(rel))
    except FileNotFoundError:
        pass


def try_rename(src_rel, dst_rel):
    """Atomic move. True if we won, False if the source was already gone (someone else won)."""
    try:
        os.rename(p(src_rel), p(dst_rel))
        return True
    except FileNotFoundError:
        return False


def listdir(rel):
    try:
        return sorted(f for f in os.listdir(p(rel)) if ".tmp-" not in f)
    except FileNotFoundError:
        return []

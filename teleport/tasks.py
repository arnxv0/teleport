"""Task queue on a shared file system: atomic-rename claims, leases, and a reaper anyone can run."""
import hashlib
import time

from . import config, fs


def fix_id(record_id, field):
    """Deterministic id: a retried task always maps to the same fix file, so it can't duplicate."""
    return hashlib.sha1(f"{record_id}|{field}".encode()).hexdigest()[:16]


def manifest():
    return (fs.read_json("tasks/manifest.json") or {}).get("tids", [])


def requeued():
    return (fs.read_json("tasks/requeued.json") or {}).get("tids", [])


def is_done(tid):
    return fs.exists(f"tasks/done/{tid}.json")


def all_done():
    return all(is_done(t) for t in manifest())


def create(tasks, meta):
    """tasks: list of (tid, payload). Writes todo files, then the manifest last."""
    for tid, payload in tasks:
        fs.write_atomic(f"tasks/todo/{tid}.json", payload)
    fs.write_atomic("tasks/manifest.json", {"tids": [t for t, _ in tasks], **meta})
    fs.write_atomic("tasks/requeued.json", {"tids": []})


def write_lease(tid, worker, epoch):
    fs.write_atomic(f"leases/{tid}.json", {
        "tid": tid, "worker": worker, "machine": config.machine_id(),
        "epoch": epoch, "expires_at": time.time() + config.LEASE_TTL})


def read_lease(tid):
    return fs.read_json(f"leases/{tid}.json")


def try_claim(tid, worker, log=None):
    """Two-phase claim. Returns the lease epoch if we own the task, else None.

    1. rename todo -> claimed: cheap filter; almost always exactly one winner.
    2. O_EXCL create claims/<tid>.<gen>: the real arbiter. Our Archil smoke test caught two clients
       both "winning" the same rename on a conditional mount; only one can win this create.
    gen increases every time a reaper requeues the task, so a requeued task gets a fresh marker.
    """
    if not fs.try_rename(f"tasks/todo/{tid}.json", f"tasks/claimed/{tid}.json"):
        return None
    if is_done(tid):  # a stale todo copy of a finished task (double-rename leftover)
        fs.remove(f"tasks/claimed/{tid}.json")
        return None
    task = fs.read_json(f"tasks/claimed/{tid}.json") or {}
    gen = task.get("gen", 0)
    if not fs.write_once(f"claims/{tid}.{gen}", {"worker": worker, "machine": config.machine_id(),
                                                  "ts": time.time()}):
        if log:
            owner = fs.read_json(f"claims/{tid}.{gen}") or {}
            log("duplicate_prevented", tid=tid,
                detail=f"rename race also won by {owner.get('worker')}; claim marker says it's theirs")
        return None
    epoch = f"{worker}:{gen}:{time.time():.3f}"
    write_lease(tid, worker, epoch)
    return epoch


def load_claimed(tid):
    return fs.read_json(f"tasks/claimed/{tid}.json")


def finish(tid):
    """Move to done. If a reaper already put it back in todo, take it from there instead."""
    if not fs.try_rename(f"tasks/claimed/{tid}.json", f"tasks/done/{tid}.json"):
        fs.try_rename(f"tasks/todo/{tid}.json", f"tasks/done/{tid}.json")
    # Mark the lease done rather than deleting it: other clients' stat caches (up to 30s on Archil)
    # can still show claimed/<tid>, and a missing lease would make a finished task look orphaned.
    lease = read_lease(tid) or {"tid": tid}
    fs.write_atomic(f"leases/{tid}.json", {**lease, "done": True, "expires_at": float("inf")})


def reap(log, seen_missing):
    """Return expired claims to todo. Safe from any number of processes at once: the rename
    claimed -> todo succeeds for exactly one. Scans the manifest, not a cached dir listing.

    seen_missing: tid -> first time we saw claimed-without-lease (a crash between claim and lease).
    """
    reassigned = []
    t = time.time()
    for tid in manifest():
        if is_done(tid) or not fs.exists(f"tasks/claimed/{tid}.json"):
            seen_missing.pop(tid, None)
            continue
        lease = read_lease(tid)
        if lease is None:
            # Crash between claim and first lease write. Wait well past the 30s stat cache.
            if t - seen_missing.setdefault(tid, t) < 3 * config.LEASE_TTL:
                continue
            lease = {"worker": None, "machine": None}
        elif lease.get("done") or lease["expires_at"] > t:
            continue
        task = fs.read_json(f"tasks/claimed/{tid}.json")
        if task is None:
            continue
        gen = task.get("gen", 0)
        # Exclusive right to requeue this generation: two reapers can't both hand it back.
        if not fs.write_once(f"claims/{tid}.{gen}.reaped", {"ts": t}):
            continue
        task["gen"] = gen + 1
        fs.write_atomic(f"tasks/claimed/{tid}.json", task)
        if fs.try_rename(f"tasks/claimed/{tid}.json", f"tasks/todo/{tid}.json"):
            seen_missing.pop(tid, None)
            reassigned.append(tid)
            log("reassigned", tid=tid, from_worker=lease.get("worker"), from_machine=lease.get("machine"),
                detail=f"lease expired (was {lease.get('worker')}@{lease.get('machine')})")
    if reassigned:
        cur = [x for x in requeued() if not is_done(x)]
        fs.write_atomic("tasks/requeued.json", {"tids": sorted(set(cur + reassigned))})
    return reassigned

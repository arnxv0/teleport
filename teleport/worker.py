"""A stateless Teleport worker. Kill it at any point; any machine that mounts the disk takes over.

  python3 -m teleport.worker --id w1          (TELEPORT_ROOT points at the shared disk)
"""
import argparse
import os
import random
import threading
import time
import traceback

from . import config, fs, tasks
from . import plan as planlib
from .checks import PIPELINE, STEPS, gate
from .events import Log


class Worker:
    def __init__(self, wid):
        self.wid = wid
        self.machine = config.machine_id()
        self.log = Log(wid)
        self.tid = self.epoch = None
        self.step = "idle"
        self.known_done = set()  # optimisation only; correctness never depends on it
        self.seen_missing = {}
        self.stats = {"done": 0, "fixes": 0, "flags": 0}
        self._stop = threading.Event()

    # ----- heartbeat: refresh our lease and publish status -----
    def heartbeat_loop(self):
        while not self._stop.wait(config.HEARTBEAT):
            try:
                self.beat()
            except Exception as e:
                print("heartbeat error", e, flush=True)

    def beat(self):
        tid, epoch = self.tid, self.epoch
        if tid:
            lease = tasks.read_lease(tid)
            if not lease or lease.get("epoch") == epoch:  # never overwrite another worker's lease
                tasks.write_lease(tid, self.wid, epoch)
        fs.write_atomic(f"workers/{self.wid}.json", {
            "worker": self.wid, "machine": self.machine, "pid": os.getpid(), "tid": self.tid,
            "step": self.step, "last_seen": time.time(), **self.stats})

    # ----- claiming -----
    def _try(self, tid, how=""):
        if tid in self.known_done:
            return None
        epoch = tasks.try_claim(tid, self.wid, self.log)
        if epoch:
            self.tid, self.epoch = tid, epoch
            self.log("claim", tid=tid, detail=how)
            return tid
        return None

    def claim(self):
        # 1) tasks a reaper just handed back (file read via open(): strongly consistent)
        for tid in tasks.requeued():
            if self._try(tid, "requeued"):
                return tid
        # 2) directory listing: fast, but may be stale on a shared FS
        listed = [f[:-5] for f in fs.listdir("tasks/todo") if f.endswith(".json")]
        random.shuffle(listed)
        for tid in listed:
            if self._try(tid):
                return tid
        # 3) probe the manifest directly
        rest = [t for t in tasks.manifest() if t not in self.known_done]
        random.shuffle(rest)
        for tid in rest:
            if tasks.is_done(tid):
                self.known_done.add(tid)
            elif self._try(tid, "via manifest scan"):
                return tid
        return None

    # ----- one task -----
    def run_task(self, tid):
        task = tasks.load_claimed(tid)
        if task is None:  # reaped between claim and read
            return
        rec = task["record"]
        ck_rel = f"checkpoints/{tid}.json"
        ck = fs.read_json(ck_rel) or {"tid": tid, "steps": {}, "history": []}
        steps = ck["steps"]
        finished = [s for s in STEPS if s in steps]
        if ck["history"]:
            prev = ck["history"][-1]
            self.log("resume", tid=tid, from_worker=prev["worker"], from_machine=prev["machine"],
                     skipped=finished,
                     detail=f"teleported from {prev['worker']}@{prev['machine']}, skipping {finished}")
        ck["history"].append({"worker": self.wid, "machine": self.machine, "ts": time.time(),
                              "resumed_after": finished})
        fs.write_atomic(ck_rel, ck)

        if ck.get("errors", 0) >= config.MAX_ATTEMPTS:  # poison pill: stop retrying, hand to a human
            summary = f"gave up after {ck['errors']} failed attempts: {ck.get('last_error')}"
            return self.commit(tid, rec, {}, summary=summary)

        for name, fn in PIPELINE:
            if name in steps:
                continue
            self.step = name
            if config.STEP_DELAY:
                time.sleep(config.STEP_DELAY * random.uniform(0.6, 1.4))
            steps[name] = fn(rec, steps)
            fs.write_atomic(ck_rel, ck)  # durable checkpoint after every step
            self.log("step", tid=tid, step=name, detail=name)
        self.commit(tid, rec, steps)

    def commit(self, tid, rec, steps, summary=None):
        self.step = "commit"
        plan = planlib.load()
        rid, rname = planlib.record_id(plan, rec), planlib.record_name(plan, rec)
        fixes, flags = [], []
        for issue in (steps.get("judge") or {}).get("issues", []):
            if gate.is_cosmetic(issue):
                continue
            ok, why = gate.check(issue, steps, rec)
            field = issue["field"]
            fid = tasks.fix_id(rid, field)
            current = planlib.get(rec, field)
            doc = {"fix_id": fid, "tid": tid, "record_id": rid, "name": rname,
                   "field": field, "old": current if current is not None else issue.get("old"), "new": issue.get("new"),
                   "evidence_urls": issue.get("evidence_urls") or [], "confidence": issue.get("confidence"),
                   "reason": issue.get("reason"), "gate": why, "worker": self.wid, "machine": self.machine,
                   "ts": time.time(), "status": "verified" if ok else "flagged"}
            dest = "fixes" if ok else "flags"
            if fs.write_once(f"{dest}/{fid}.json", doc):
                (fixes if ok else flags).append(fid)
                self.log("fix" if ok else "flag", tid=tid, fix_id=fid, field=field,
                         detail=f"{field}: {doc['old']!r} -> {doc['new']!r} ({why})")
            else:
                self.log("duplicate_prevented", tid=tid, fix_id=fid, detail=f"{dest}/{fid} already written")

        verdict = {"tid": tid, "record_id": rid, "name": rname,
                   "summary": summary or (steps.get("judge") or {}).get("summary"),
                   "probe": (steps.get("probe") or {}).get("status"), "fixes": fixes, "flags": flags,
                   "worker": self.wid, "machine": self.machine, "ts": time.time()}
        if not fs.write_once(f"verdicts/{tid}.json", verdict):
            self.log("duplicate_prevented", tid=tid, detail="verdict already written")
        tasks.finish(tid)
        self.known_done.add(tid)
        self.stats["done"] += 1
        self.stats["fixes"] += len(fixes)
        self.stats["flags"] += len(flags)
        self.log("done", tid=tid, fixes=len(fixes), flags=len(flags),
                 detail=f"{len(fixes)} fix(es), {len(flags)} flag(s)")

    def record_error(self, tid, e):
        """Count failures on the disk so the retry cap holds across machines."""
        try:
            ck = fs.read_json(f"checkpoints/{tid}.json") or {"tid": tid, "steps": {}, "history": []}
            ck["errors"] = ck.get("errors", 0) + 1
            ck["last_error"] = f"{type(e).__name__}: {e}"[:200]
            fs.write_atomic(f"checkpoints/{tid}.json", ck)
        except Exception:
            pass

    # ----- main loop -----
    def run(self, max_idle=30):
        fs.ensure_dirs()
        self.log("start", detail=f"pid {os.getpid()}")
        threading.Thread(target=self.heartbeat_loop, daemon=True).start()
        self.beat()
        idle_since = None
        while True:
            tid = self.claim()
            if not tid:
                self.step = "idle"
                # Idle workers double as reapers, so the swarm needs no central coordinator.
                if tasks.reap(self.log, self.seen_missing):
                    continue
                idle_since = idle_since or time.time()
                if time.time() - idle_since > max_idle and tasks.all_done():
                    self.log("exit", detail="all tasks done")
                    break
                time.sleep(2)
                continue
            idle_since = None
            try:
                self.run_task(tid)
            except Exception as e:
                # Leave it claimed: our lease lapses and someone (maybe us) retries it.
                self.log("error", tid=tid, detail=f"{type(e).__name__}: {e}"[:300])
                traceback.print_exc()
                self.record_error(tid, e)
                self.tid = None
                self.step = "backoff"
                time.sleep(3)
                continue
            self.tid, self.epoch, self.step = None, None, "idle"
        self._stop.set()
        self.beat()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", required=True)
    ap.add_argument("--max-idle", type=float, default=30)
    a = ap.parse_args()
    Worker(a.id).run(max_idle=a.max_idle)


if __name__ == "__main__":
    main()

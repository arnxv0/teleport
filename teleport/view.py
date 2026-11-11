"""Read-only view of the swarm's state, from a local dir or from an Archil disk via its S3 object API."""
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from .config import LEASE_TTL


class LocalReader:
    def __init__(self, root):
        self.root = root

    def list(self, prefix):
        d = os.path.join(self.root, prefix)
        try:
            return [prefix + f for f in os.listdir(d) if ".tmp-" not in f]
        except FileNotFoundError:
            return []

    def get(self, key):
        try:
            with open(os.path.join(self.root, key), "rb") as f:
                return f.read()
        except FileNotFoundError:
            return None

    def put(self, key, data):
        path = os.path.join(self.root, key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)


class ArchilReader:
    def __init__(self, disk_id):
        import archil
        self.disk = archil.get_disk(disk_id)

    def list(self, prefix):
        out, token = [], None
        while True:
            r = self.disk.list_objects(prefix, recursive=True, continuation_token=token)
            out += [o.key for o in r.objects if ".tmp-" not in o.key]
            if not r.is_truncated:
                return out
            token = r.next_continuation_token

    def get(self, key):
        try:
            return self.disk.get_object(key)
        except Exception:
            return None

    def put(self, key, data):
        self.disk.put_object(key, data, "application/json")


IMMUTABLE = ("fixes/", "flags/", "verdicts/", "accepted/")


class View:
    def __init__(self, reader):
        self.r = reader
        self.cache = {}
        self.pool = ThreadPoolExecutor(32)
        self.lock = threading.Lock()

    def _json(self, key, immutable=False):
        if immutable and key in self.cache:
            return self.cache[key]
        raw = self.r.get(key)
        try:
            v = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            v = None
        if immutable and v is not None:
            self.cache[key] = v
        return v

    def _many(self, keys, immutable=False):
        return [v for v in self.pool.map(lambda k: self._json(k, immutable), keys) if v is not None]

    def _tids(self, prefix):
        return {k.rsplit("/", 1)[-1][:-5] for k in self.r.list(prefix) if k.endswith(".json")}

    def state(self):
        with self.lock:
            return self._state()

    def _state(self):
        t = time.time()
        man = self._json("tasks/manifest.json") or {}
        todo, claimed, done = (self._tids(f"tasks/{x}/") for x in ("todo", "claimed", "done"))
        verdict_keys = [k for k in self.r.list("verdicts/") if k.endswith(".json")]
        verdicts = self._many(verdict_keys, True)
        fixes = self._many([k for k in self.r.list("fixes/") if k.endswith(".json")], True)
        flags = self._many([k for k in self.r.list("flags/") if k.endswith(".json")], True)
        accepted = {a["fix_id"] for a in self._many(
            [k for k in self.r.list("accepted/") if k.endswith(".json")], True)}
        workers = self._many([k for k in self.r.list("workers/") if k.endswith(".json")])

        events = []
        for raw in self.pool.map(self.r.get, [k for k in self.r.list("log/") if k.endswith(".jsonl")]):
            for line in (raw or b"").decode(errors="replace").splitlines():
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        events.sort(key=lambda e: e["ts"])
        self.events = events
        count = lambda ty: sum(1 for e in events if e["type"] == ty)  # noqa: E731

        verdict_tids = {v["tid"] for v in verdicts}
        lost = sorted(done - verdict_tids)  # finished tasks with no recorded outcome: must stay 0
        for w in workers:
            w["alive"] = t - w.get("last_seen", 0) < LEASE_TTL
        for f in fixes:
            f["accepted"] = f["fix_id"] in accepted
        machines = sorted({w["machine"] for w in workers})
        teleports = [e for e in events if e["type"] == "resume"]

        return {
            "now": t, "country": man.get("country"), "source": man.get("source"),
            "plan": self._json("plan.json", True), "total": len(man.get("tids", [])),
            "todo": len(todo), "claimed": len(claimed), "done": len(done),
            "verdicts": len(verdicts), "fixes": len(fixes), "flags": len(flags), "accepted": len(accepted),
            "reassigned": count("reassigned"), "teleported": len(teleports),
            "duplicates_prevented": count("duplicate_prevented"), "errors": count("error"),
            "lost": len(lost), "lost_tids": lost[:20],
            "workers_alive": sum(w["alive"] for w in workers), "workers": workers, "machines": machines,
            "events": events[-80:], "teleports": teleports[-20:],
            "fix_list": sorted(fixes, key=lambda f: -f["ts"]),
            "flag_list": sorted(flags, key=lambda f: -f["ts"]),
            "probe_dead": sum(1 for v in verdicts if v.get("probe") in ("dead", "tls_error")),
        }

    def accept(self, fix_id, who="reviewer"):
        self.r.put(f"accepted/{fix_id}.json",
                   json.dumps({"fix_id": fix_id, "by": who, "ts": time.time()}).encode())

    # ----- teleport inspector -----
    def teleports(self):
        """Per-task journeys across machines, for every task that was resumed somewhere else."""
        if not hasattr(self, "events"):
            self.state()  # the dashboard's refresh loop normally keeps self.events current
        events = self.events
        by_tid = {}
        for e in events:
            if e.get("tid"):
                by_tid.setdefault(e["tid"], []).append(e)
        items = []
        for tid, evs in by_tid.items():
            if not any(e["type"] == "resume" for e in evs):
                continue
            hops, cur, item = [], None, {"tid": tid, "lease_expired": [], "skipped": []}
            for e in evs:
                ty = e["type"]
                if ty == "claim":
                    cur = {"worker": e["worker"], "machine": e["machine"], "claimed_ts": e["ts"],
                           "steps": [], "ended": "running", "end_ts": None}
                    hops.append(cur)
                elif ty == "step" and cur:
                    cur["steps"].append({"step": e.get("step"), "ts": e["ts"]})
                elif ty == "reassigned":
                    item["lease_expired"].append(e["ts"])
                    for h in hops:
                        if h["ended"] == "running" and h["worker"] == e.get("from_worker"):
                            h["ended"] = "killed"
                            h["end_ts"] = h["steps"][-1]["ts"] if h["steps"] else h["claimed_ts"]
                elif ty == "resume":
                    item["skipped"] = e.get("skipped") or []
                    item.setdefault("resumes", []).append({"ts": e["ts"], "from": e.get("from_machine"),
                                                           "to": e["machine"], "skipped": e.get("skipped") or []})
                elif ty == "done" and cur:
                    cur["ended"], cur["end_ts"] = "done", e["ts"]
                    item["outcome"] = {"fixes": e.get("fixes", 0), "flags": e.get("flags", 0)}
            killed = [h for h in hops if h["ended"] == "killed"]
            for h in hops[:-1]:
                if h["ended"] == "running":  # superseded without an explicit reassign event
                    h["ended"], h["end_ts"] = "killed", h["steps"][-1]["ts"] if h["steps"] else h["claimed_ts"]
            nxt = [h for h in hops if h["ended"] != "killed"]
            if killed and nxt:
                item["recovery_s"] = round(nxt[-1]["claimed_ts"] - killed[-1]["end_ts"], 1)
            v = self._json(f"verdicts/{tid}.json", True) or {}
            item.update(name=v.get("name"), summary=v.get("summary"), hops=hops,
                        first_ts=evs[0]["ts"], last_ts=evs[-1]["ts"])
            items.append(item)
        items.sort(key=lambda i: -i["last_ts"])
        rec = sorted(i["recovery_s"] for i in items if "recovery_s" in i)
        machines_lost = {h["machine"] for i in items for h in i["hops"] if h["ended"] == "killed"}
        return {"summary": {"teleports": len(items),
                            "steps_saved": sum(len(i["skipped"]) for i in items),
                            "median_recovery_s": rec[len(rec) // 2] if rec else None,
                            "machines_lost": len(machines_lost)},
                "items": items[:100]}


"""Append-only audit log. One file per worker: conditional mounts are last-write-wins per byte range,
so concurrent appends from different machines to one shared file could clobber each other."""
import json
import time

from . import config, fs


class Log:
    def __init__(self, worker):
        self.worker = worker
        self.machine = config.machine_id()
        self.rel = f"log/{worker}.jsonl"

    def __call__(self, type_, **kw):
        ev = {"ts": time.time(), "worker": self.worker, "machine": self.machine, "type": type_, **kw}
        fs.append_line(self.rel, json.dumps(ev) + "\n")
        print(f"[{self.worker}@{self.machine}] {type_} {kw.get('tid', '')} {kw.get('detail', '')}", flush=True)
        return ev

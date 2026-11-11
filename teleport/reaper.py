"""Lease reaper: hands tasks held by dead workers back to the queue. Idle workers reap too, so this
process only makes reassignment snappier; killing it loses nothing.

  python3 -m teleport.reaper
"""
import os
import time

from . import config, fs, tasks
from .events import Log


def main():
    fs.ensure_dirs()
    log = Log(os.environ.get("TELEPORT_REAPER_ID", "reaper"))
    log("start", detail=f"lease ttl {config.LEASE_TTL}s")
    seen = {}
    while True:
        try:
            tasks.reap(log, seen)
            if tasks.manifest() and tasks.all_done():
                log("exit", detail="all tasks done")
                return
        except Exception as e:
            print("reap error", e, flush=True)
        time.sleep(2)


if __name__ == "__main__":
    main()

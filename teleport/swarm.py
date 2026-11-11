"""Swarm control CLI.

  python3 -m teleport.swarm local  launch --n 6 --machine laptop-A
  python3 -m teleport.swarm local  reaper
  python3 -m teleport.swarm archil setup [--name teleport]          # disk + upload code
  python3 -m teleport.swarm archil upload                           # re-upload code after edits
  python3 -m teleport.swarm archil ingest --country NG --limit 500 --poison   # runs IN a sandbox
  python3 -m teleport.swarm archil launch --n 10 --machine A
  python3 -m teleport.swarm archil reaper
  python3 -m teleport.swarm archil kill --machine A --n 5
  python3 -m teleport.swarm archil teardown
"""
import argparse
import json


def get_backend(name):
    if name == "archil":
        from .backends.archil import ArchilBackend
        return ArchilBackend()
    from .backends.local import LocalBackend
    return LocalBackend()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("backend", choices=["local", "archil"])
    ap.add_argument("cmd", choices=["launch", "reaper", "kill", "setup", "upload", "ingest", "teardown"])
    ap.add_argument("--n", type=int, default=4)
    ap.add_argument("--machine", default=None, help="local: machine label; archil: pool name")
    ap.add_argument("--name", default="teleport")
    a, extra = ap.parse_known_args()
    b = get_backend(a.backend)

    if a.cmd == "launch":
        print(json.dumps(b.launch(a.n, a.machine or ("laptop" if a.backend == "local" else "A"))))
    elif a.cmd == "reaper":
        b.reaper()
    elif a.cmd == "kill" and a.backend == "archil":
        wids = [w for w, m in b.s.get("sandboxes", {}).items() if m["pool"] == (a.machine or "A")][:a.n]
        print("killed", b.kill(wids))
    elif a.cmd in ("setup", "upload", "ingest", "teardown"):
        if a.backend != "archil":
            raise SystemExit(f"{a.cmd} is archil-only (locally run: python3 -m teleport.ingest ...)")
        {"setup": lambda: b.setup(a.name), "upload": b.upload_code,
         "ingest": lambda: b.ingest(" ".join(extra)), "teardown": b.teardown}[a.cmd]()
    else:
        raise SystemExit("local kill: use the dashboard, or kill -9 a pid from disk/workers/*.json")


if __name__ == "__main__":
    main()

"""Workers as Archil sandboxes, one per worker, all mounting the same disk with conditional mounts.
The code itself lives on the disk (teleport/ prefix), so every sandbox runs the same version."""
import json
import os
import time

from .. import config

# Local record of disk + sandbox ids (not swarm state). TELEPORT_STATE selects another run, e.g. a live-demo disk.
STATE = os.path.join(config.REPO, os.environ.get("TELEPORT_STATE", ".teleport.json"))
PKG = os.path.join(config.REPO, "teleport")


def load_state(path=None):
    try:
        with open(path or STATE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_state(s, path=None):
    with open(path or STATE, "w") as f:
        json.dump(s, f, indent=2)


def worker_env():
    return {k: os.environ[k] for k in config.PASS_ENV if os.environ.get(k)}


class ArchilBackend:
    name = "archil"

    def __init__(self, state_path=None):
        import archil
        self.archil = archil
        archil.configure(api_key=os.environ.get("ARCHIL_API_KEY"), region=config.ARCHIL_REGION)
        self.state_path = state_path or STATE
        self.s = load_state(self.state_path)

    @property
    def disk(self):
        return self.archil.get_disk(self.s["disk_id"])

    # ----- disk -----
    def setup(self, name):
        if not self.s.get("disk_id"):
            res = self.archil.create_disk(name=name)
            self.s.update(disk_id=res.disk.id, disk_name=name, mount_token=res.token)
            save_state(self.s, self.state_path)
            print("created disk", res.disk.id, "(mount token saved to .teleport.json)")
            try:
                res.disk.wait_until_ready()
            except Exception:
                pass
        self.upload_code()

    def upload_code(self):
        d, n = self.disk, 0
        for dirpath, _, files in os.walk(PKG):
            if "__pycache__" in dirpath:
                continue
            for f in files:
                if f.endswith((".py", ".html")):
                    path = os.path.join(dirpath, f)
                    key = os.path.relpath(path, config.REPO)  # teleport/...
                    with open(path, "rb") as fh:
                        d.put_object(key, fh.read(), "text/plain")
                    n += 1
        print(f"uploaded {n} files under teleport/ to {self.s['disk_id']}")

    # ----- sandboxes -----
    def _sandbox(self, name):
        mounts = [self.archil.SandboxMountSpec(disk=self.s["disk_id"], path=config.MOUNT, conditional=True)]
        return self.archil.create_sandbox(
            name=name, vcpu_count=1, mem_size_mib=1024,
            base_image=os.environ.get("TELEPORT_IMAGE", "python:3.12-slim"),
            mounts=mounts, env={**worker_env(), "TELEPORT_ROOT": config.MOUNT},
            max_ttl_seconds=int(os.environ.get("TELEPORT_SANDBOX_TTL", "1800")),  # Free plan caps at 1800
            idle_ttl_seconds=0)  # detached processes don't count as activity; without this they pause at 30m

    def _detach(self, sb, module, args, machine, logname):
        env = {**worker_env(), "TELEPORT_ROOT": config.MOUNT, "TELEPORT_MACHINE": machine}
        # Listings are cached up to 5 min by default; turn that off so requeued tasks show up fast.
        sb.exec(f"archil set-cache-expiry {config.MOUNT} --readdir-expiry 0 || true", timeout_seconds=30)
        # Smoke test: a `setsid nohup ... &` child dies when exec returns; run() + disconnect() survives.
        proc = sb.run(f"python3 -m {module} {args} > /tmp/{logname}.out 2>&1", cwd=config.MOUNT, env=env)
        proc.disconnect()
        return proc

    def ingest(self, args):
        sb = self._sandbox(f"tp-ingest-{int(time.time())}")
        try:
            r = sb.exec(f"cd {config.MOUNT} && python3 -m teleport.ingest {args}",
                        env={**worker_env(), "TELEPORT_ROOT": config.MOUNT}, timeout_seconds=1200)
            print(r.stdout[-3000:], r.stderr[-2000:])
            return r
        finally:
            sb.stop()
            sb.delete()

    def launch(self, n, machine="A"):
        """machine is the pool name; each sandbox is its own machine, labelled archil:<pool>:<id>."""
        self.s = load_state(self.state_path)
        sbs = self.s.setdefault("sandboxes", {})
        out = []
        for _ in range(n):
            wid = f"{machine}{sum(1 for m in sbs.values() if m['pool'] == machine) + 1:02d}"
            sb = self._sandbox(f"tp-{wid.lower()}-{int(time.time()) % 10000}")
            self._detach(sb, "teleport.worker", f"--id {wid}", f"archil:{machine}:{sb.id[-6:]}", wid)
            sbs[wid] = {"id": sb.id, "pool": machine, "worker": wid}
            save_state(self.s, self.state_path)
            out.append({"worker": wid, "sandbox": sb.id, "machine": machine})
            print("launched", wid, sb.id, flush=True)
        return out

    def reaper(self):
        sb = self._sandbox(f"tp-reaper-{int(time.time()) % 10000}")
        self._detach(sb, "teleport.reaper", "", f"archil:reaper:{sb.id[-6:]}", "reaper")
        self.s["reaper"] = sb.id
        save_state(self.s, self.state_path)

    def kill(self, workers):
        """Hard kill: SIGKILL every process (no flush, no cleanup), then stop the sandbox.
        A plain stop() would SIGTERM and flush first, which is gentler than a real crash."""
        self.s = load_state(self.state_path)  # sandboxes may have been launched by another process
        out = []
        for w in workers:
            wid = w["worker"] if isinstance(w, dict) else w
            meta = self.s.get("sandboxes", {}).get(wid)
            if not meta:
                continue
            try:
                sb = self.archil.get_sandbox(meta["id"])
                try:
                    sb.exec("kill -9 -1", timeout_seconds=5)
                except Exception:
                    pass  # the exec connection dies with the processes; expected
                sb.stop(wait=False)
                out.append(wid)
            except Exception as e:
                print("kill failed", wid, e)
        return out

    def fork(self, wid, new_wid):
        """Cell division: clone a worker's sandbox and run a worker in both copies."""
        meta = self.s["sandboxes"][wid]
        sb = self.archil.get_sandbox(meta["id"])
        sb.stop()  # sandboxes with mounted disks must be stopped before forking
        child = sb.fork(name=f"tp-{new_wid.lower()}")
        sb.start()
        pool = meta["pool"]
        self._detach(sb, "teleport.worker", f"--id {wid}", f"archil:{pool}:{sb.id[-6:]}", wid)
        self._detach(child, "teleport.worker", f"--id {new_wid}", f"archil:{pool}:{child.id[-6:]}", new_wid)
        self.s["sandboxes"][new_wid] = {"id": child.id, "pool": pool, "worker": new_wid}
        save_state(self.s, self.state_path)

    def teardown(self):
        for wid, meta in list(self.s.get("sandboxes", {}).items()):
            try:
                sb = self.archil.get_sandbox(meta["id"])
                sb.stop()
                sb.delete()
            except Exception as e:
                print("teardown", wid, e)
        self.s["sandboxes"] = {}
        save_state(self.s, self.state_path)

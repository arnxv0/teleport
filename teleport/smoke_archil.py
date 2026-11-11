"""Smoke-test the Archil assumptions Teleport's harness (teleport/fs.py, teleport/backends/archil.py) relies on.

Run the moment an API key exists:

    export ARCHIL_API_KEY=key-...            # ARCHIL_REGION defaults to aws-us-east-1
    .venv/bin/python teleport/smoke_archil.py
    .venv/bin/python teleport/smoke_archil.py --keep --crash --fork   # extra (slower) checks

Creates a throwaway disk "teleport-smoke" (or reuses --disk-id), two small sandboxes that mount it
with SandboxMountSpec(disk=..., path="/mnt/archil", conditional=True), and checks:

  A  os.rename is atomic across clients (concurrent 200-file claim race from both sandboxes)
  B  write-once: link(tmp, final) -> EEXIST for the loser (or O_CREAT|O_EXCL if link unsupported)
  C  fsync'd writes survive SIGKILL / sandbox.stop() (/ --crash: guest reboot), visible to the other
     sandbox via open() and to the laptop via disk.get_object()
  D  detached processes keep running after exec returns (setsid nohup ... &, and run()+disconnect())
  E  egress to the APIs/hosts the worker needs (INFO: free plan = managed allowlist)
  F  base_image python:3.12-slim works with a conditional mount
  G  cross-client directory-listing / stat staleness (and whether `archil set-cache-expiry` fixes it)

Exit code: 0 if no FAIL in A-D, 1 if any, 2 if setup itself failed. Sandboxes are always stopped
and deleted in `finally`; the disk is deleted unless --keep or --disk-id was given.
Stdlib + archil only.
"""
import argparse
import base64
import json
import os
import shlex
import sys
import threading
import time
import traceback
import uuid

MOUNT = "/mnt/archil"
SLIM = "python:3.12-slim"
FALLBACK_IMAGE = "node:24-bookworm"  # Archil's default image; ships python3
DEFAULT_URLS = [
    "https://api.keenable.ai",
    "https://api.openai.com/v1/models",
    "https://openalex.s3.amazonaws.com/RELEASE_NOTES.txt",
    "https://pypi.org/simple/",            # control: on the free-plan allowlist
    "https://www.mit.edu",
    "https://www.ox.ac.uk",
    "https://unilag.edu.ng",
    "https://www.uct.ac.za",
]

# ------------------------------------------------------------------ result bookkeeping

RESULTS = []
T0 = time.time()


def log(msg):
    print(f"[{time.time() - T0:7.1f}s] {msg}", flush=True)


def record(check, status, msg, secs=None):
    RESULTS.append({"check": check, "status": status, "msg": msg, "secs": secs})
    t = f" ({secs:.1f}s)" if secs is not None else ""
    print(f"{status:<4} {check:<3} {msg}{t}", flush=True)


# ------------------------------------------------------------------ code that runs inside sandboxes

COMMON = r'''
import os, sys, json, time, errno, uuid
ARGS = json.loads(sys.argv[1])
def out(**kw):
    print("RESULT:" + json.dumps(kw), flush=True)
def write_fsync(path, data):
    with open(path, "wb") as f:
        f.write(data); f.flush(); os.fsync(f.fileno())
def fsync_dir(d):
    try:
        fd = os.open(d, os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)
    except OSError:
        pass
def wait_until(t):
    while time.time() < t:
        time.sleep(min(0.01, max(0.0, t - time.time())))
'''

S_ENV = r'''
import shutil, subprocess, platform
def sh(c):
    try:
        return subprocess.run(c, shell=True, capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception as e:
        return repr(e)
m = ARGS["mount"]
probe = os.path.join(m, "smoke", ARGS["run"], "probe-" + ARGS["who"])
os.makedirs(os.path.dirname(probe), exist_ok=True)
write_fsync(probe, b"ok")
out(python=sys.version.split()[0], os=sh("head -1 /etc/os-release"), uid=os.getuid(),
    mount=sh(f"grep ' {m} ' /proc/mounts || mount | grep {m}"), probe_ok=open(probe).read() == "ok",
    tools={t: shutil.which(t) for t in ["setsid", "nohup", "archil", "ps", "curl", "base64"]},
    archil_version=sh("archil --version 2>&1 | head -1") if shutil.which("archil") else None,
    hostname=platform.node(), time=time.time())
'''

S_RACE_SETUP = r'''
d = ARGS["dir"]
for sub in ("todo", "claimed"):
    os.makedirs(os.path.join(d, sub), exist_ok=True)
t = time.time()
for i in range(ARGS["n"]):
    write_fsync(os.path.join(d, "todo", f"{i:04d}.json"), json.dumps({"i": i}).encode())
fsync_dir(os.path.join(d, "todo"))
out(created=ARGS["n"], secs=time.time() - t)
'''

S_RACE = r'''
d, who = ARGS["dir"], ARGS["who"]
wait_until(ARGS["start_at"])
t = time.time()
wins, lost, other = [], 0, []
lat = []
for i in range(ARGS["n"]):
    src = os.path.join(d, "todo", f"{i:04d}.json")
    dst = os.path.join(d, "claimed", f"{i:04d}.json")   # same dst for both, exactly like fs.try_rename
    t1 = time.time()
    try:
        os.rename(src, dst)
        wins.append(i)
    except FileNotFoundError:
        lost += 1
    except OSError as e:
        other.append([i, e.errno, str(e)])
    lat.append(time.time() - t1)
out(who=who, wins=wins, lost=lost, other=other, started=t, secs=time.time() - t,
    avg_ms=1000 * sum(lat) / max(1, len(lat)), max_ms=1000 * max(lat or [0]))
'''

S_ONCE = r'''
d, who, mode = ARGS["dir"], ARGS["who"], ARGS["mode"]
os.makedirs(d, exist_ok=True)
UNSUP = (errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOSYS, errno.EXDEV)
def excl(path, data):
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return False
    try:
        os.write(fd, data); os.fsync(fd)
    finally:
        os.close(fd)
    return True
wait_until(ARGS["start_at"])
t = time.time()
created, existed, other, link_errnos = [], [], [], set()
for i in range(ARGS["n"]):
    path = os.path.join(d, f"{i:04d}.json")
    data = json.dumps({"who": who, "i": i}).encode()
    try:
        if mode == "excl":
            ok = excl(path, data)
        else:  # identical to fs.write_once
            tmp = f"{path}.tmp-{uuid.uuid4().hex[:8]}"
            write_fsync(tmp, data)
            try:
                os.link(tmp, path); ok = True
            except FileExistsError:
                ok = False
            except OSError as e:
                if e.errno not in UNSUP:
                    raise
                link_errnos.add(e.errno)
                ok = excl(path, data)
            finally:
                try: os.unlink(tmp)
                except OSError: pass
        (created if ok else existed).append(i)
    except OSError as e:
        other.append([i, e.errno, str(e)])
fsync_dir(d)
out(who=who, created=created, existed=existed, other=other, link_errnos=sorted(link_errnos),
    secs=time.time() - t)
'''

S_READ_ALL = r'''
res = {}
for i in range(ARGS["n"]):
    try:
        with open(os.path.join(ARGS["dir"], f"{i:04d}.json")) as f:
            res[i] = json.loads(f.read()).get("who")
    except Exception as e:
        res[i] = "ERR:" + repr(e)
out(owners=res)
'''

S_WRITE_THEN_DIE = r'''
d = ARGS["dir"]; os.makedirs(d, exist_ok=True)
write_fsync(os.path.join(d, "fsynced"), ARGS["payload"].encode())
fsync_dir(d)
f = open(os.path.join(d, "unsynced"), "wb"); f.write(ARGS["payload"].encode())  # never flushed/closed
print("RESULT:" + json.dumps({"written": time.time()}), flush=True)
sys.stdout.flush()
if ARGS.get("die"):
    os.kill(os.getpid(), 9)
'''

S_WRITE_CLOSE = r'''
d = ARGS["dir"]; os.makedirs(d, exist_ok=True)
write_fsync(os.path.join(d, "fsynced"), ARGS["payload"].encode()); fsync_dir(d)
with open(os.path.join(d, "closed-no-fsync"), "wb") as f:
    f.write(ARGS["payload"].encode())
out(written=time.time())
'''

S_READ = r'''
res = {}
for name in ARGS["names"]:
    try:
        with open(name, "rb") as f:
            res[name] = f.read().decode(errors="replace")
    except Exception as e:
        res[name] = None
out(files=res)
'''

# Heartbeat program used for D: writes an incrementing counter (fsync + atomic rename) every second.
HB_PROGRAM = r'''
import json, os, sys, time
path, limit = sys.argv[1], float(sys.argv[2])
os.makedirs(os.path.dirname(path), exist_ok=True)
n, t0 = 0, time.time()
while time.time() - t0 < limit:
    n += 1
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(json.dumps({"pid": os.getpid(), "n": n, "t": time.time(), "ppid": os.getppid(),
                            "sid": os.getsid(0)}))
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)
    time.sleep(1)
'''

S_WRITE_FILE = r'''
with open(ARGS["path"], "w") as f:
    f.write(ARGS["content"])
out(ok=True)
'''

S_EGRESS = r'''
import urllib.request, urllib.error, ssl
res = {}
for url in ARGS["urls"]:
    t = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (teleport-smoke)"})
    try:
        with urllib.request.urlopen(req, timeout=ARGS["timeout"]) as r:
            res[url] = {"ok": True, "status": r.status, "ms": int(1000 * (time.time() - t))}
    except urllib.error.HTTPError as e:   # an HTTP answer from the real host still proves egress
        res[url] = {"ok": True, "status": e.code, "ms": int(1000 * (time.time() - t))}
    except Exception as e:
        res[url] = {"ok": False, "error": f"{type(e).__name__}: {e}"[:200], "ms": int(1000 * (time.time() - t))}
out(results=res)
'''

S_CREATE = r'''
d = ARGS["dir"]; os.makedirs(d, exist_ok=True)
p = os.path.join(d, ARGS["name"])
write_fsync(p, b"x"); fsync_dir(d)
out(created=time.time())
'''

S_PRIME = r'''
d = ARGS["dir"]; os.makedirs(d, exist_ok=True)
for _ in range(3):  # populate this client's listing + negative-dentry caches like a polling worker would
    names = os.listdir(d)
    for n in ARGS["names"]:
        os.path.exists(os.path.join(d, n))
out(listed=sorted(names))
'''

S_POLL = r'''
d, name, deadline = ARGS["dir"], ARGS["name"], time.time() + ARGS["timeout"]
p = os.path.join(d, name)
seen = {"listdir": None, "exists": None, "open": None}
start = time.time()
while time.time() < deadline and None in seen.values():
    now = time.time()
    if seen["listdir"] is None and name in os.listdir(d):
        seen["listdir"] = now
    if seen["exists"] is None and os.path.exists(p):
        seen["exists"] = now
    if seen["open"] is None:
        try:
            open(p, "rb").close(); seen["open"] = now
        except OSError:
            pass
    time.sleep(ARGS["interval"])
out(seen=seen, start=start)
'''


def _cmd(body, args):
    code = base64.b64encode((COMMON + body).encode()).decode()
    boot = f"import base64;exec(compile(base64.b64decode('{code}'),'smoke','exec'))"
    return f"python3 -c {shlex.quote(boot)} {shlex.quote(json.dumps(args))}"


def pyrun(sb, body, args, timeout=180, allow_fail=False):
    """Run a COMMON+body python snippet in the sandbox, return the RESULT dict (and the raw result)."""
    r = sb.exec(_cmd(body, args), timeout_seconds=timeout)
    payload = None
    for line in (r.stdout or "").splitlines():
        if line.startswith("RESULT:"):
            payload = json.loads(line[len("RESULT:"):])
    if payload is None and not allow_fail:
        raise RuntimeError(f"sandbox script failed: status={r.status} exit={r.exit_code} "
                           f"reason={r.exit_reason} stderr={r.stderr[-1500:]!r} stdout={r.stdout[-500:]!r}")
    return payload, r


# ------------------------------------------------------------------ the smoke test

class Smoke:
    def __init__(self, a):
        self.a = a
        import archil
        self.archil = archil
        self.run = time.strftime("%H%M%S") + "-" + uuid.uuid4().hex[:4]
        self.base = f"smoke/{self.run}"           # key prefix on the disk
        self.mbase = f"{MOUNT}/{self.base}"        # same path as seen inside sandboxes
        self.disk = None
        self.created_disk = False
        self.sandboxes = []                        # every sandbox id we created (for cleanup)
        self.A = self.B = None
        self.tools = {}

    # ---------------------------------------------------------- setup / teardown

    def setup_disk(self):
        ar = self.archil
        t = time.time()
        if self.a.disk_id:
            self.disk = ar.get_disk(self.a.disk_id)
            log(f"reusing disk {self.disk.id} ({self.disk.name}, status={self.disk.status})")
        else:
            try:
                res = ar.create_disk(name=self.a.disk_name)
                self.disk = res.disk
                self.created_disk = True
                log(f"created disk {self.disk.id} (token returned: {bool(res.token)})")
            except ar.ArchilError as e:
                log(f"create_disk failed ({e.status} {e.code}: {e}); looking for an existing '{self.a.disk_name}'")
                found = ar.list_disks(name=self.a.disk_name)
                if not found:
                    raise
                self.disk = found[0]
                log(f"reusing existing disk {self.disk.id}; pass --disk-id {self.disk.id} next time")
        self.disk = self.disk.wait_until_ready(timeout=180)
        log(f"disk ready in {time.time() - t:.1f}s")
        self.disk.put_object(f"{self.base}/laptop.txt", b"from-laptop", "text/plain")

    def mounts(self):
        return [self.archil.SandboxMountSpec(disk=self.disk.id, path=MOUNT, conditional=True)]

    def _create(self, name, image, max_ttl, idle_ttl):
        kw = dict(name=name, vcpu_count=1, mem_size_mib=1024, base_image=image,
                  env={"TELEPORT_ROOT": MOUNT, "SMOKE_RUN": self.run}, mounts=self.mounts(),
                  max_ttl_seconds=max_ttl)
        if idle_ttl is not None:
            kw["idle_ttl_seconds"] = idle_ttl
        t = time.time()
        sb = self.archil.create_sandbox(**kw)
        self.sandboxes.append(sb.id)
        log(f"sandbox {name} -> {sb.id} status={sb.status} image={image} max_ttl={max_ttl} "
            f"idle_ttl={idle_ttl} in {time.time() - t:.1f}s; mounts={sb.mounts}")
        return sb, time.time() - t

    def create_sandbox(self, label, image):
        """Try backends/archil.py exact params first (max_ttl 3h) so we learn whether they're accepted, then degrade."""
        ar = self.archil
        name = f"tp-smoke-{label}-{self.run}"
        attempts = [(image, 3 * 3600, 0), (image, self.a.ttl, 0), (image, self.a.ttl, None)]
        last = None
        for img, mt, it in attempts:
            try:
                sb, secs = self._create(name, img, mt, it)
                return sb, secs, (img, mt, it), last
            except ar.ArchilError as e:
                last = f"{img} max_ttl={mt} idle_ttl={it}: HTTP {e.status} {e.code}: {e}"
                log("create_sandbox rejected: " + last)
            except Exception as e:  # SandboxStartError etc.
                last = f"{img} max_ttl={mt} idle_ttl={it}: {type(e).__name__}: {e}"
                log("create_sandbox failed: " + last)
        raise RuntimeError(f"could not create sandbox {label}: {last}")

    def setup_sandboxes(self):
        # F: python:3.12-slim with a conditional mount
        try:
            self.A, secs, used, err = self.create_sandbox("a", SLIM)
            if used[1] != 3 * 3600 or used[2] != 0:
                record("F", "INFO", f"backends/archil.py params rejected, used max_ttl={used[1]} "
                                    f"idle_ttl={used[2]}. Reason: {err}")
            f_ok = True
        except Exception as e:
            record("F", "FAIL", f"{SLIM} + conditional mount failed: {e}; falling back to {FALLBACK_IMAGE}")
            self.A, secs, used, err = self.create_sandbox("a", FALLBACK_IMAGE)
            f_ok = False
        image = used[0]
        self.B, _, _, _ = self.create_sandbox("b", image)
        envA, _ = pyrun(self.A, S_ENV, {"mount": MOUNT, "run": self.run, "who": "a"})
        envB, _ = pyrun(self.B, S_ENV, {"mount": MOUNT, "run": self.run, "who": "b"})
        self.tools = envA["tools"]
        cond = [m.conditional for m in self.A.mounts]
        log(f"sandbox A env: {json.dumps(envA)}")
        log(f"sandbox B env: {json.dumps(envB)}")
        if f_ok:
            ok = envA["probe_ok"] and envB["probe_ok"] and envA["python"].startswith("3.12")
            record("F", "PASS" if ok else "FAIL",
                   f"{image}: python {envA['python']}, mount ok={envA['probe_ok']}/{envB['probe_ok']}, "
                   f"conditional={cond}, tools={envA['tools']}", secs)
        # also prove laptop put_object -> sandbox open() (this is how ArchilBackend.upload_code ships code)
        got, _ = pyrun(self.B, S_READ, {"names": [f"{self.mbase}/laptop.txt"]})
        v = got["files"][f"{self.mbase}/laptop.txt"]
        record("C", "PASS" if v == "from-laptop" else "FAIL",
               f"laptop put_object -> sandbox open(): {v!r}")

    def cleanup(self):
        ar = self.archil
        for sid in self.sandboxes:
            try:
                sb = ar.get_sandbox(sid)
                if sb.status not in ("stopped", "exited", "failed", "deleted", "deleting"):
                    try:
                        sb = sb.stop(wait=True)
                    except Exception as e:
                        log(f"stop {sid} failed: {e}")
                        sb = ar.get_sandbox(sid)
                if sb.status not in ("deleted", "deleting"):
                    sb.delete()
                log(f"deleted sandbox {sid}")
            except Exception as e:
                log(f"cleanup of sandbox {sid} failed: {type(e).__name__}: {e}  (delete it in the console)")
        if self.disk is None:
            return
        if self.created_disk and not self.a.keep:
            for attempt in range(6):
                try:
                    self.disk.delete()
                    log(f"deleted disk {self.disk.id}")
                    break
                except Exception as e:
                    log(f"disk delete attempt {attempt + 1} failed: {e}")
                    time.sleep(5)
        else:
            log(f"kept disk {self.disk.id} (reuse with --disk-id {self.disk.id})")

    # ---------------------------------------------------------- A: rename race

    def check_A(self):
        n = self.a.n
        d = f"{self.mbase}/race"
        t = time.time()
        pyrun(self.A, S_RACE_SETUP, {"dir": d, "n": n})
        start_at = time.time() + self.a.race_delay
        out = {}

        def go(label, sb):
            try:
                out[label] = pyrun(sb, S_RACE, {"dir": d, "n": n, "who": label, "start_at": start_at},
                                   timeout=600)[0]
            except Exception as e:
                out[label] = {"error": f"{type(e).__name__}: {e}"}

        ths = [threading.Thread(target=go, args=(k, sb)) for k, sb in (("a", self.A), ("b", self.B))]
        for th in ths:
            th.start()
        for th in ths:
            th.join()
        if any("error" in v for v in out.values()):
            record("A", "FAIL", f"race script error: {out}", time.time() - t)
            return
        wa, wb = set(out["a"]["wins"]), set(out["b"]["wins"])
        overlap = wa & wb
        missing = set(range(n)) - (wa | wb)
        other = out["a"]["other"] + out["b"]["other"]
        # ground truth from the strongly consistent S3 view
        claimed = self.disk.list_objects(f"{self.base}/race/claimed/").objects
        todo = self.disk.list_objects(f"{self.base}/race/todo/").objects
        starts = abs(out["a"]["started"] - out["b"]["started"])
        detail = (f"a won {len(wa)}, b won {len(wb)}, overlap={sorted(overlap)[:10]}, "
                  f"missing={sorted(missing)[:10]}, other_errors={other[:5]}, "
                  f"S3 view: claimed={len(claimed)} todo={len(todo)}, start skew={starts:.2f}s, "
                  f"avg rename {out['a']['avg_ms']:.1f}/{out['b']['avg_ms']:.1f} ms "
                  f"(max {out['a']['max_ms']:.0f}/{out['b']['max_ms']:.0f})")
        ok = not overlap and not missing and not other and len(claimed) == n and not todo
        contended = min(len(wa), len(wb)) > 0
        record("A", "PASS" if ok else "FAIL", detail, time.time() - t)
        if ok and not contended:
            record("A", "INFO", "one side won everything: race was not actually contended "
                                "(raise --race-delay so both start together, or larger --n)")

    # ---------------------------------------------------------- B: write-once

    def _once(self, mode, n):
        d = f"{self.mbase}/once-{mode}"
        start_at = time.time() + self.a.race_delay
        out = {}

        def go(label, sb):
            try:
                out[label] = pyrun(sb, S_ONCE, {"dir": d, "n": n, "who": label, "mode": mode,
                                                "start_at": start_at}, timeout=600)[0]
            except Exception as e:
                out[label] = {"error": f"{type(e).__name__}: {e}"}

        ths = [threading.Thread(target=go, args=(k, sb)) for k, sb in (("a", self.A), ("b", self.B))]
        for th in ths:
            th.start()
        for th in ths:
            th.join()
        if any("error" in v for v in out.values()):
            return False, f"{mode}: script error {out}", None
        ca, cb = set(out["a"]["created"]), set(out["b"]["created"])
        both = ca & cb
        neither = set(range(n)) - (ca | cb)
        other = out["a"]["other"] + out["b"]["other"]
        owners, _ = pyrun(self.B, S_READ_ALL, {"dir": d, "n": n})
        wrong = [i for i in range(n)
                 if owners["owners"][str(i)] != ("a" if i in ca else "b" if i in cb else None)]
        leftovers = [o.key for o in self.disk.list_objects(f"{self.base}/once-{mode}/").objects
                     if ".tmp-" in o.key]
        ok = not both and not neither and not other and not wrong
        msg = (f"{mode}: a created {len(ca)}, b created {len(cb)}, double-created={sorted(both)[:10]}, "
               f"none={sorted(neither)[:10]}, content-mismatch={wrong[:10]}, other_errors={other[:5]}, "
               f"link errnos={out['a']['link_errnos'] or out['b']['link_errnos'] or 'none (link works)'}, "
               f"tmp leftovers={len(leftovers)}")
        return ok, msg, out["a"]["link_errnos"] or out["b"]["link_errnos"]

    def check_B(self):
        t = time.time()
        ok_link, msg_link, link_errnos = self._once("link", self.a.n_once)
        ok_excl, msg_excl, _ = self._once("excl", self.a.n_once)
        # fs.write_once is correct if link works exclusively, or link is unsupported and O_EXCL is exclusive
        ok = ok_link and (not link_errnos or ok_excl)
        record("B", "PASS" if ok else "FAIL", f"fs.write_once algorithm -> {msg_link}", time.time() - t)
        record("B", "PASS" if ok_excl else ("FAIL" if link_errnos else "INFO"),
               f"raw O_CREAT|O_EXCL fallback -> {msg_excl}")

    # ---------------------------------------------------------- C: durability

    def _get(self, key):
        try:
            return self.disk.get_object(key).decode(errors="replace")
        except Exception as e:
            return f"<{type(e).__name__}: {getattr(e, 'code', '')}>"

    def check_C(self):
        # C1: process hard kill (SIGKILL right after fsync)
        t = time.time()
        payload = "c1-" + uuid.uuid4().hex
        d = f"{self.mbase}/c1"
        res, raw = pyrun(self.A, S_WRITE_THEN_DIE, {"dir": d, "payload": payload, "die": True},
                         allow_fail=True)
        laptop = self._get(f"{self.base}/c1/fsynced")
        other, _ = pyrun(self.B, S_READ, {"names": [f"{d}/fsynced", f"{d}/unsynced"]})
        ok = laptop == payload and other["files"][f"{d}/fsynced"] == payload
        record("C", "PASS" if ok else "FAIL",
               f"SIGKILL after fsync: exit={raw.exit_code} status={raw.status}; laptop get_object "
               f"match={laptop == payload}, sandbox B open() match={other['files'][f'{d}/fsynced'] == payload}",
               time.time() - t)
        record("C", "INFO", f"un-fsynced, never-closed write before SIGKILL visible to B: "
                            f"{other['files'][f'{d}/unsynced'] == payload}")

        # C2: sandbox.stop() right after the write returns; then start again (what ArchilBackend.fork does)
        t = time.time()
        payload = "c2-" + uuid.uuid4().hex
        d = f"{self.mbase}/c2"
        pyrun(self.A, S_WRITE_CLOSE, {"dir": d, "payload": payload})
        ts = time.time()
        stopped = self.A.stop(wait=True)
        stop_secs = time.time() - ts
        laptop = self._get(f"{self.base}/c2/fsynced")
        laptop_nf = self._get(f"{self.base}/c2/closed-no-fsync")
        other, _ = pyrun(self.B, S_READ, {"names": [f"{d}/fsynced"]})
        ok = laptop == payload and other["files"][f"{d}/fsynced"] == payload
        record("C", "PASS" if ok else "FAIL",
               f"sandbox.stop() (took {stop_secs:.1f}s, status={stopped.status}): laptop match={laptop == payload}, "
               f"B open() match={other['files'][f'{d}/fsynced'] == payload}; closed-without-fsync file "
               f"survived={laptop_nf == payload}", time.time() - t)
        ts = time.time()
        try:
            self.A = self.A.start()
            back, _ = pyrun(self.A, S_READ, {"names": [f"{d}/fsynced"]})
            record("C", "INFO", f"stop -> start keeps the conditional mount: status={self.A.status}, "
                                f"file readable={back['files'][f'{d}/fsynced'] == payload}", time.time() - ts)
        except Exception as e:
            record("C", "INFO", f"restart after stop failed: {type(e).__name__}: {e}", time.time() - ts)

    def check_C_crash(self):
        """--crash: fsync, then reboot the guest without syncing (closest thing to pulling the plug)."""
        t = time.time()
        payload = "c3-" + uuid.uuid4().hex
        d = f"{self.mbase}/c3"
        try:
            crash_sb, _, _, _ = self.create_sandbox("crash", self.A.base_image or SLIM)
            pyrun(crash_sb, S_WRITE_CLOSE, {"dir": d, "payload": payload})
            try:
                crash_sb.exec("echo 1 > /proc/sys/kernel/sysrq; echo b > /proc/sysrq-trigger", timeout_seconds=15)
            except Exception as e:
                log(f"crash exec ended with {type(e).__name__} (expected)")
            status = None
            for _ in range(30):
                status = self.archil.get_sandbox(crash_sb.id).status
                if status != "running":
                    break
                time.sleep(2)
            laptop = self._get(f"{self.base}/c3/fsynced")
            other, _ = pyrun(self.B, S_READ, {"names": [f"{d}/fsynced"]})
            ok = laptop == payload and other["files"][f"{d}/fsynced"] == payload
            record("C", "PASS" if ok else "FAIL",
                   f"guest reboot (sysrq b) after fsync: sandbox status now={status}; laptop match="
                   f"{laptop == payload}, B match={other['files'][f'{d}/fsynced'] == payload}", time.time() - t)
        except Exception as e:
            record("C", "FAIL", f"crash test error: {type(e).__name__}: {e}", time.time() - t)

    # ---------------------------------------------------------- D: detached processes

    def _hb(self, key):
        try:
            return json.loads(self.disk.get_object(key))
        except Exception:
            return None

    def _hb_check(self, name, key, t_launch):
        time.sleep(self.a.d_wait)
        h1 = self._hb(key)
        time.sleep(5)
        h2 = self._hb(key)
        alive = bool(h1 and h2 and h2["n"] > h1["n"])
        return alive, h1, h2

    def check_D(self):
        hb_path = "/tmp/teleport_hb.py"
        pyrun(self.A, S_WRITE_FILE, {"path": hb_path, "content": HB_PROGRAM})
        limit = 600
        # D1: exactly backends/archil.py _detach pattern
        key1 = f"{self.base}/d/hb1.json"
        t = time.time()
        cmd = (f"cd {MOUNT} && SMOKE=1 setsid nohup python3 {hb_path} {MOUNT}/{key1} {limit} "
               f"> /tmp/hb1.out 2>&1 < /dev/null &")
        try:
            r = self.A.exec(cmd, timeout_seconds=30)
            exec_secs = time.time() - t
            info = f"exec returned in {exec_secs:.1f}s status={r.status} exit={r.exit_code} stderr={r.stderr[-200:]!r}"
        except Exception as e:
            exec_secs = time.time() - t
            info = f"exec raised {type(e).__name__}: {e} after {exec_secs:.1f}s"
        alive, h1, h2 = self._hb_check("D1", key1, t)
        ok = alive and exec_secs < 25
        record("D", "PASS" if ok else "FAIL",
               f"setsid nohup ... & via exec: {info}; heartbeat {h1 and h1['n']} -> {h2 and h2['n']} "
               f"{self.a.d_wait + 5:.0f}s later (alive={alive})", time.time() - t)
        if not alive:
            r = self.A.exec("cat /tmp/hb1.out; ls /proc | head -3", timeout_seconds=20)
            log(f"D1 debug: {r.stdout[-800:]} {r.stderr[-300:]}")

        # D2: the SDK's documented detach: run() then disconnect()
        key2 = f"{self.base}/d/hb2.json"
        t = time.time()
        try:
            p = self.A.run(f"python3 {hb_path} {MOUNT}/{key2} {limit}", cwd=MOUNT, env={"SMOKE": "2"})
            pid = p.id
            p.disconnect()
            alive, h1, h2 = self._hb_check("D2", key2, t)
            record("D", "PASS" if alive else "INFO",
                   f"sandbox.run() + disconnect(): process id={pid}; heartbeat {h1 and h1['n']} -> "
                   f"{h2 and h2['n']} (alive={alive})", time.time() - t)
            try:
                self.A.attach(pid).kill()
            except Exception as e:
                log(f"D2 attach/kill: {type(e).__name__}: {e}")
        except Exception as e:
            record("D", "INFO", f"run()+disconnect() errored: {type(e).__name__}: {e}", time.time() - t)

        # D3: does the D1 process survive another connection opening and closing? (it should)
        h = self._hb(key1)
        if h:
            r = self.A.exec(f"kill -0 {h['pid']} && echo alive || echo dead", timeout_seconds=20)
            record("D", "INFO", f"D1 pid {h['pid']} (sid={h.get('sid')}, ppid={h.get('ppid')}) after more execs: "
                                f"{r.stdout.strip()}")
        if self.tools.get("archil"):
            r = self.A.exec("archil services --help 2>&1 | head -20", timeout_seconds=20)
            record("D", "INFO", "`archil` CLI present in sandbox; `archil services create` (supervised, "
                                f"auto-restart) is available: {r.stdout.strip()[:300]!r}")
        else:
            record("D", "INFO", "`archil` CLI not on PATH in this image (no `archil services`)")

    # ---------------------------------------------------------- E: egress

    def check_E(self):
        t = time.time()
        try:
            net = self.A.get_network()
            eg = net.egress
            pol = (f"default={eg.default} allow={len(eg.allow or [])} rules "
                   f"(e.g. {[a if isinstance(a, str) else a.target for a in (eg.allow or [])][:6]})"
                   if eg else "no egress policy (unrestricted)")
        except Exception as e:
            pol = f"get_network failed: {type(e).__name__}: {e}"
        res, _ = pyrun(self.A, S_EGRESS, {"urls": self.a.urls, "timeout": 12}, timeout=300)
        record("E", "INFO", f"network policy: {pol}")
        for url, r in res["results"].items():
            if r["ok"]:
                record("E", "PASS", f"{url} -> HTTP {r['status']} ({r['ms']} ms)")
            else:
                record("E", "FAIL", f"{url} -> {r['error']} ({r['ms']} ms)")
        log(f"E done in {time.time() - t:.1f}s")

    # ---------------------------------------------------------- G: listing staleness

    def _staleness(self, label, d, name):
        pyrun(self.B, S_PRIME, {"dir": d, "names": [name]})          # B caches listing + negative lookup
        created, _ = pyrun(self.A, S_CREATE, {"dir": d, "name": name})  # A creates (fsync)
        res, _ = pyrun(self.B, S_POLL, {"dir": d, "name": name, "timeout": self.a.g_timeout,
                                        "interval": 0.5}, timeout=self.a.g_timeout + 120)
        c = created["created"]
        lag = {k: (None if v is None else round(v - c, 2)) for k, v in res["seen"].items()}
        never = [k for k, v in lag.items() if v is None]
        status = "PASS" if not never and max(lag.values()) < 5 else "INFO"
        record("G", status, f"{label}: seconds until B sees A's new file: {lag}"
                            + (f" (not within {self.a.g_timeout}s: {never})" if never else ""))
        return lag

    def check_G(self):
        t = time.time()
        d = f"{self.mbase}/g"
        pyrun(self.A, S_CREATE, {"dir": d, "name": "seed"})
        self._staleness("default cache", d, "new1")
        if self.tools.get("archil"):
            r = self.B.exec(f"archil set-cache-expiry {MOUNT} --readdir-expiry 0", timeout_seconds=30)
            record("G", "INFO", f"`archil set-cache-expiry {MOUNT} --readdir-expiry 0` in B: exit={r.exit_code} "
                                f"{(r.stdout + r.stderr).strip()[:200]!r}")
            d2 = f"{self.mbase}/g2"
            pyrun(self.A, S_CREATE, {"dir": d2, "name": "seed"})
            self._staleness("after set-cache-expiry 0", d2, "new2")
            pyrun(self.B, S_PRIME, {"dir": d2, "names": ["new3"]})
            pyrun(self.A, S_CREATE, {"dir": d2, "name": "new3"})
            r = self.B.exec(f"archil invalidate-cache {MOUNT} && ls {d2}", timeout_seconds=30)
            record("G", "INFO", f"`archil invalidate-cache` then ls in B shows new3: {'new3' in r.stdout} "
                                f"(exit={r.exit_code})")
        log(f"G done in {time.time() - t:.1f}s")

    # ---------------------------------------------------------- H: fork (what ArchilBackend.fork does)

    def check_fork(self):
        t = time.time()
        child = None
        try:
            stopped = self.B.stop()
            child = stopped.fork(name=f"tp-smoke-fork-{self.run}")
            self.sandboxes.append(child.id)
            self.B = stopped.start()
            env, _ = pyrun(child, S_ENV, {"mount": MOUNT, "run": self.run, "who": "fork"})
            record("H", "INFO", f"stop -> fork -> start (ArchilBackend.fork): child {child.id} status={child.status}, "
                                f"conditional mount works={env['probe_ok']}, mounts={child.mounts}", time.time() - t)
        except Exception as e:
            record("H", "INFO", f"fork failed: {type(e).__name__}: {e}", time.time() - t)

    # ---------------------------------------------------------- driver

    def main(self):
        self.setup_disk()
        self.setup_sandboxes()
        checks = self.a.checks.upper()
        plan = [("G", self.check_G), ("A", self.check_A), ("B", self.check_B), ("D", self.check_D),
                ("E", self.check_E), ("C", self.check_C)]   # C last: it stops/restarts sandbox A
        for key, fn in plan:
            if key not in checks:
                continue
            log(f"--- check {key}")
            t = time.time()
            try:
                fn()
            except Exception as e:
                traceback.print_exc()
                record(key, "FAIL" if key in "ABCD" else "INFO",
                       f"check crashed: {type(e).__name__}: {e}", time.time() - t)
        if self.a.crash and "C" in checks:
            log("--- check C (crash)")
            self.check_C_crash()
        if self.a.fork:
            log("--- check H (fork)")
            self.check_fork()


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--disk-id", help="reuse this disk instead of creating 'teleport-smoke' (never deleted)")
    ap.add_argument("--disk-name", default="teleport-smoke")
    ap.add_argument("--keep", action="store_true", help="don't delete the throwaway disk")
    ap.add_argument("--region", default=os.environ.get("ARCHIL_REGION", "aws-us-east-1"))
    ap.add_argument("--checks", default="ABCDEG", help="subset of checks to run (F always runs)")
    ap.add_argument("--n", type=int, default=200, help="files in the rename race (A)")
    ap.add_argument("--n-once", type=int, default=50, help="targets in the write-once race (B)")
    ap.add_argument("--race-delay", type=float, default=8.0,
                    help="seconds ahead to schedule the synchronized race start (covers exec startup)")
    ap.add_argument("--ttl", type=int, default=1800, help="fallback max_ttl_seconds (free plan caps at 1800)")
    ap.add_argument("--d-wait", type=float, default=10.0, help="seconds to wait before checking D heartbeats")
    ap.add_argument("--g-timeout", type=float, default=90.0, help="max seconds to poll for G visibility")
    ap.add_argument("--urls", nargs="*", default=DEFAULT_URLS, help="egress targets for E")
    ap.add_argument("--crash", action="store_true", help="also hard-crash a 3rd sandbox after fsync (C)")
    ap.add_argument("--fork", action="store_true", help="also test stop -> fork -> start like ArchilBackend.fork (H)")
    return ap.parse_args(argv)


def main(argv=None):
    a = parse_args(argv)
    key = os.environ.get("ARCHIL_API_KEY")
    if not key:
        print("ARCHIL_API_KEY is not set", file=sys.stderr)
        return 2
    import archil
    archil.configure(api_key=key, region=a.region)
    log(f"archil SDK {archil.__version__}, region {a.region}")
    s = Smoke(a)
    setup_failed = False
    try:
        s.main()
    except KeyboardInterrupt:
        log("interrupted")
        setup_failed = True
    except Exception as e:
        traceback.print_exc()
        record("-", "FAIL", f"setup/driver error: {type(e).__name__}: {e}")
        setup_failed = True
    finally:
        log("--- cleanup")
        s.cleanup()
    print("\n==================== SUMMARY ====================")
    for r in RESULTS:
        t = f" ({r['secs']:.1f}s)" if r["secs"] is not None else ""
        print(f"{r['status']:<4} {r['check']:<3} {r['msg']}{t}")
    hard = [r for r in RESULTS if r["status"] == "FAIL" and r["check"] in ("A", "B", "C", "D")]
    print(f"\n{len(hard)} FAIL(s) in A-D; total {time.time() - T0:.0f}s")
    if setup_failed and not hard:
        return 2
    return 1 if hard else 0


if __name__ == "__main__":
    sys.exit(main())

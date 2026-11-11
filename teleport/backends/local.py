"""Workers as processes on this machine, each labelled with a "machine" name (dev + fallback demo)."""
import os
import signal
import subprocess
import sys
import time

from .. import config


class LocalBackend:
    name = "local"

    def __init__(self, root=None):
        self.root = root or config.ROOT
        os.makedirs(os.path.join(self.root, "_proc"), exist_ok=True)

    def _spawn(self, module, args, machine, logname):
        env = {**os.environ, "TELEPORT_ROOT": self.root, "TELEPORT_MACHINE": machine}
        log = open(os.path.join(self.root, "_proc", f"{logname}.out"), "w")
        return subprocess.Popen([sys.executable, "-m", module, *args], env=env, cwd=config.REPO,
                                stdout=log, stderr=subprocess.STDOUT, start_new_session=True)

    def launch(self, n, machine="laptop"):
        out = []
        for i in range(n):
            wid = f"{machine}-{int(time.time() * 1000) % 100000:05d}-{i}"
            proc = self._spawn("teleport.worker", ["--id", wid], machine, wid)
            out.append({"worker": wid, "pid": proc.pid, "machine": machine})
        return out

    def reaper(self):
        self._spawn("teleport.reaper", [], "laptop", "reaper")

    def kill(self, workers):
        """SIGKILL: no cleanup, no lease release. The worst case, on purpose."""
        killed = []
        for w in workers:
            try:
                os.kill(w["pid"], signal.SIGKILL)
                killed.append(w["worker"])
            except (ProcessLookupError, KeyError, TypeError):
                pass
        return killed

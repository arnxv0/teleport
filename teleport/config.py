"""Every tunable in one place, read from the environment."""
import os
import socket

_PKG = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(_PKG)

ROOT = os.path.abspath(os.environ.get("TELEPORT_ROOT", os.path.join(REPO, "disk")))

# harness
LEASE_TTL = float(os.environ.get("TELEPORT_LEASE_TTL", "20"))
HEARTBEAT = float(os.environ.get("TELEPORT_HEARTBEAT", "5"))
MAX_ATTEMPTS = int(os.environ.get("TELEPORT_MAX_ATTEMPTS", "3"))
STEP_DELAY = float(os.environ.get("TELEPORT_STEP_DELAY", "0"))  # slow steps down for a visible demo

# verification
KEENABLE_URL = "https://api.keenable.ai"
KEENABLE_KEY = os.environ.get("KEENABLE_API_KEY", "")
OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "")
MODEL = os.environ.get("TELEPORT_MODEL", "gpt-4.1-mini")
MIN_CONFIDENCE = float(os.environ.get("TELEPORT_MIN_CONFIDENCE", "0.7"))
MIN_NAME_MATCH = float(os.environ.get("TELEPORT_MIN_NAME_MATCH", "1.0"))
USER_AGENT = "Mozilla/5.0 (compatible; TeleportVerifier/0.1; data-quality research)"

# archil
ARCHIL_REGION = os.environ.get("ARCHIL_REGION", "aws-us-east-1")
MOUNT = "/mnt/archil"
PASS_ENV = ["OPENAI_API_KEY", "KEENABLE_API_KEY", "TELEPORT_MODEL", "TELEPORT_LEASE_TTL",
            "TELEPORT_HEARTBEAT", "TELEPORT_STEP_DELAY", "TELEPORT_MIN_CONFIDENCE", "TELEPORT_MAX_ATTEMPTS"]


def machine_id():
    return os.environ.get("TELEPORT_MACHINE") or socket.gethostname()

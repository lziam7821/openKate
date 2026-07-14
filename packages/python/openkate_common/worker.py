import json
from typing import Iterable


def capability_heartbeat(worker: str, capabilities: Iterable[str]) -> str:
    return json.dumps({"worker": worker, "status": "ready", "capabilities": list(capabilities)})

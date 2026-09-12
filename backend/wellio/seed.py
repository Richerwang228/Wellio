import copy
import json
from pathlib import Path
from uuid import uuid4

SCHEMA_VERSION = 4
SEED_SOURCE = "demo_fixture_v1"
_SEEDS = {name: json.loads((Path(__file__).parent / "data" / f"{name}.json").read_text()) for name in ("normal", "low_recovery")}


def create_seed(session_id, scenario="normal", locale="en"):
    snapshot = copy.deepcopy(_SEEDS[scenario])
    snapshot["sessionId"] = session_id
    snapshot["conversationId"] = str(uuid4())
    snapshot["locale"] = locale
    return snapshot

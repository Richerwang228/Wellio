from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from .runtime import invalidate_readiness_context, synchronize_snapshot
from .seed import create_seed


READINESS_DEMO_PRESETS = {
    "low": {"score": 38, "sleepMinutes": 312, "deepSleepPercent": 9, "restingHeartRate": 68, "hrvMs": 31},
    "balanced": {"score": 65, "sleepMinutes": 405, "deepSleepPercent": 17, "restingHeartRate": 59, "hrvMs": 48},
    "high": {"score": 86, "sleepMinutes": 468, "deepSleepPercent": 22, "restingHeartRate": 54, "hrvMs": 62},
}


def _set_readiness_demo(database, snapshot, mode):
    preset = READINESS_DEMO_PRESETS[mode]
    invalidate_readiness_context(database, snapshot)
    sleep_id = str(uuid4())
    wake_time = datetime.fromisoformat(snapshot["dayKey"] + "T07:00:00").replace(tzinfo=ZoneInfo(snapshot["timeZone"]))
    snapshot["sleep"] = {
        "id": sleep_id, "minutes": preset["sleepMinutes"], "deepSleepPercent": preset["deepSleepPercent"],
        "bedtime": (wake_time - timedelta(minutes=preset["sleepMinutes"])).isoformat(),
        "wakeTime": wake_time.isoformat(), "source": "mock_watch",
    }
    snapshot["readiness"] = {
        "id": str(uuid4()), "version": snapshot["readiness"]["version"] + 1, "dayKey": snapshot["dayKey"],
        "quality": "valid", "score": preset["score"], "scoreScale": 100,
        "guidanceHint": "consider_rest" if mode == "low" else "keep_plan",
        "observedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "restingHeartRate": preset["restingHeartRate"], "baselineHeartRate": 55, "baselineSleepMinutes": 450,
        "source": "mock_watch", "sleepRecordId": sleep_id, "demoMode": mode, "hrvMs": preset["hrvMs"],
        "reasons": ["sleep_below_baseline", "resting_hr_above_baseline"] if mode == "low" else ["sleep_near_baseline", "resting_hr_near_baseline"],
    }
    # Reuse runtime synchronization under mutate's lock so the idempotent receipt
    # already contains the check for the new readiness identity.
    synchronize_snapshot(database, snapshot, snapshot["capabilities"])


def execute_action(database, session_id, request):
    from .workouts import record_workout_progress, apply_proposal, dismiss_proposal
    from .meals import undo_meal
    kind = request["kind"]
    if kind in ("start_workout", "complete_exercise", "undo_exercise", "finish_workout"):
        return record_workout_progress(database, session_id, request)
    if kind == "apply_proposal":
        return apply_proposal(database, session_id, request)
    if kind == "dismiss_proposal":
        return dismiss_proposal(database, session_id, request)
    if kind == "undo_meal":
        return undo_meal(database, session_id, request)

    def execute(current):
        if kind == "set_locale":
            current["locale"] = request["locale"]
            current["revision"] += 1
            return {"httpStatus": 200, "result": {"status": "succeeded", "operationId": str(uuid4())}, "snapshot": current}
        if kind == "reset_demo":
            snapshot = create_seed(session_id, request["scenario"], current["locale"])
            snapshot["resetEpoch"] = current["resetEpoch"] + 1
            snapshot["revision"] = current["revision"] + 1
            return {"httpStatus": 200, "result": {"status": "succeeded", "operationId": str(uuid4())}, "snapshot": snapshot}
        if kind == "set_readiness_demo":
            _set_readiness_demo(database, current, request["mode"])
            current["revision"] += 1
            return {"httpStatus": 200, "result": {"status": "succeeded", "operationId": str(uuid4())}, "snapshot": current}
        provider = kind in ("check_readiness", "request_proposal")
        return {"httpStatus": 503 if provider else 501, "result": {"status": "failed", "errorCode": "PROVIDER_NOT_CONFIGURED" if provider else "ACTION_NOT_AVAILABLE"}}

    return database.mutate(session_id, request, execute)

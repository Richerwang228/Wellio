"""Workout transitions, fixed equipment, and reviewable proposals; no model calls."""
from copy import deepcopy
from datetime import date, datetime, timezone
from math import ceil, isfinite
import re
from uuid import uuid4

from .errors import BackendError
from .validation import canonical_json, strict_object, require_id, require_int


def _label(en, zh):
    return {"en": en, "zh-CN": zh}


def _weighted(basis, maximum, step):
    return {"basis": basis, "unit": "kg", "minKg": 5, "maxKg": maximum, "stepKg": step, "allowedKg": [5 + index * step for index in range(int((maximum - 5) / step) + 1)]}


_EQUIPMENT = [
    {"equipmentId": "gym-a-dumbbells", "gymId": "gym-a", "kind": "dumbbells", "name": _label("Dumbbells", "哑铃"), "load": _weighted("per_hand", 30, 2.5)},
    {"equipmentId": "gym-a-bench", "gymId": "gym-a", "kind": "bench", "name": _label("Training bench", "训练凳")},
    {"equipmentId": "gym-a-cable", "gymId": "gym-a", "kind": "cable", "name": _label("Cable machine", "拉力器"), "load": _weighted("machine_stack", 60, 5)},
    {"equipmentId": "gym-a-pullup-bar", "gymId": "gym-a", "kind": "pullup_bar", "name": _label("Pull-up bar", "单杠"), "load": {"basis": "bodyweight", "allowsAdditionalLoad": False}},
    {"equipmentId": "gym-b-dumbbells", "gymId": "gym-b", "kind": "dumbbells", "name": _label("Dumbbells", "哑铃"), "load": _weighted("per_hand", 25, 2.5)},
    {"equipmentId": "gym-b-bench", "gymId": "gym-b", "kind": "bench", "name": _label("Training bench", "训练凳")},
    {"equipmentId": "gym-b-cable", "gymId": "gym-b", "kind": "cable", "name": _label("Cable machine", "拉力器"), "load": _weighted("machine_stack", 50, 5)},
]
EQUIPMENT_STATUSES = ("available", "temporarily_occupied", "unavailable")
_CATALOG = {
    "seated-cable-row": {"equipment": "cable", "split": "Pull"},
    "lat-pulldown": {"equipment": "cable", "split": "Pull"},
    "dumbbell-curl": {"equipment": "dumbbells", "split": "Pull"},
    "one-arm-dumbbell-row": {"equipment": "dumbbells", "split": "Pull", "bench": True, "perSide": True},
    "pull-up": {"equipment": "pullup_bar", "split": "Pull"},
    "goblet-squat": {"equipment": "dumbbells", "split": "Legs"},
    "dumbbell-romanian-deadlift": {"equipment": "dumbbells", "split": "Legs"},
    "reverse-lunge": {"equipment": "dumbbells", "split": "Legs", "perSide": True},
    "dumbbell-bench-press": {"equipment": "dumbbells", "split": "Push", "bench": True},
    "dumbbell-shoulder-press": {"equipment": "dumbbells", "split": "Push"},
    "triceps-pushdown": {"equipment": "cable", "split": "Push"},
    "lateral-raise": {"equipment": "dumbbells", "split": "Push"},
}


def lookup_equipment(equipment_id):
    return next((deepcopy(entry) for entry in _EQUIPMENT if entry["equipmentId"] == equipment_id), None)


_UNSET = object()


def get_gym_equipment(gym_id, equipment_status=_UNSET):
    if gym_id not in ("gym-a", "gym-b"):
        raise BackendError("GYM_NOT_FOUND" if isinstance(gym_id, str) else "INVALID_INPUT", 404 if isinstance(gym_id, str) else 400)
    statuses = {} if equipment_status is _UNSET else equipment_status
    if not isinstance(statuses, dict) or any(lookup_equipment(key) is None or value not in EQUIPMENT_STATUSES for key, value in statuses.items()):
        raise BackendError("INVALID_INPUT", 400)
    return {"gymId": gym_id, "equipment": [{**deepcopy(entry), "status": statuses.get(entry["equipmentId"], "available")} for entry in _EQUIPMENT if entry["gymId"] == gym_id]}


def get_exercise_catalog(gym_id):
    """Expose only executable catalog identities and their fixed constraints."""
    equipment = get_gym_equipment(gym_id)['equipment']
    names = {
        'seated-cable-row': ('Seated cable row', '坐姿绳索划船'),
        'lat-pulldown': ('Lat pulldown', '高位下拉'),
        'dumbbell-curl': ('Dumbbell curl', '哑铃弯举'),
        'one-arm-dumbbell-row': ('One-arm dumbbell row', '单臂哑铃划船'),
        'pull-up': ('Pull-up', '引体向上'),
        'goblet-squat': ('Goblet squat', '高脚杯深蹲'),
        'dumbbell-romanian-deadlift': ('Dumbbell Romanian deadlift', '哑铃罗马尼亚硬拉'),
        'reverse-lunge': ('Reverse lunge', '反向箭步蹲'),
        'dumbbell-bench-press': ('Dumbbell bench press', '哑铃卧推'),
        'dumbbell-shoulder-press': ('Dumbbell shoulder press', '哑铃肩推'),
        'triceps-pushdown': ('Triceps pushdown', '绳索下压'),
        'lateral-raise': ('Lateral raise', '侧平举'),
    }
    result = []
    for catalog_id, definition in _CATALOG.items():
        for item in equipment:
            if item['kind'] != definition['equipment']:
                continue
            required = [item['equipmentId']]
            if definition.get('bench'):
                bench = next((entry for entry in equipment if entry['kind'] == 'bench'), None)
                if bench is None:
                    continue
                required.append(bench['equipmentId'])
            result.append({'catalogId': catalog_id, 'name': _label(*names[catalog_id]), 'split': definition['split'],
                           'equipmentId': item['equipmentId'], 'equipment': item['name'], 'equipmentKind': item['kind'],
                           'basis': item['load']['basis'], 'unit': 'kg', 'requiresEquipmentIds': required,
                           'perSide': bool(definition.get('perSide')), 'sets': {'min': 1, 'max': 6},
                           'reps': {'min': 1, 'max': 30}, 'restSeconds': {'min': 15, 'max': 300}})
    return result


def exercise_availability(snapshot, exercise):
    statuses = snapshot["conditions"].get("equipmentStatus", {})
    requirements = [exercise["equipmentId"]]
    if _CATALOG.get(exercise["catalogId"], {}).get("bench"):
        requirements.append(exercise["equipmentId"][:5] + "-bench")
    for status in ("unavailable", "temporarily_occupied"):
        if any(statuses.get(key) == status for key in requirements):
            return status
    return "available"


def minimum_workout_minutes(exercises):
    pending = [exercise for exercise in exercises if not exercise["completed"]]
    seconds = sum(exercise["sets"] * exercise["reps"] * 4 * (2 if _CATALOG.get(exercise["catalogId"], {}).get("perSide") else 1) + (exercise["sets"] - 1) * exercise["restSeconds"] for exercise in pending)
    return ceil((seconds + max(0, len(pending) - 1) * 90) / 60)


def _localized(value):
    result = strict_object(value, {"en", "zh-CN"})
    if any(not isinstance(text, str) or not 1 <= len(text.strip()) <= 2000 for text in result.values()):
        raise BackendError("INVALID_INPUT", 400)
    return {key: text.strip() for key, text in result.items()}


def _number(value, minimum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or (minimum is not None and value < minimum):
        raise BackendError("INVALID_INPUT", 400)


def _enum(value, allowed):
    if not isinstance(value, str) or value not in allowed:
        raise BackendError("INVALID_INPUT", 400)


def _parse_workout(candidate):
    result = deepcopy(strict_object(candidate, {"id", "trainingSessionId", "version", "dayKey", "name", "gymId", "estimatedMinutes", "status", "exercises"}, {"startedAt", "endedAt", "actualMinutes", "source"}))
    for key in ("id", "trainingSessionId"):
        require_id(result[key])
    require_int(result["version"])
    require_int(result["estimatedMinutes"], min=1, max=180)
    if not isinstance(result["dayKey"], str):
        raise BackendError("INVALID_INPUT", 400)
    result["name"] = _localized(result["name"])
    _enum(result["gymId"], ("gym-a", "gym-b"))
    _enum(result["status"], ("planned", "in_progress", "completed"))
    for key in ("startedAt", "endedAt"):
        if key in result and not isinstance(result[key], str):
            raise BackendError("INVALID_INPUT", 400)
    if "actualMinutes" in result:
        _number(result["actualMinutes"])
    if "source" in result:
        _enum(result["source"], ("demo_preset", "agent_proposal"))
    if not isinstance(result["exercises"], list) or not 1 <= len(result["exercises"]) <= 12:
        raise BackendError("INVALID_INPUT", 400)
    for exercise in result["exercises"]:
        strict_object(exercise, {"id", "catalogId", "name", "equipmentId", "equipment", "sets", "reps", "restSeconds", "suggestedLoad", "completed", "instructions"}, {"animation", "replacesId", "equipmentStatus"})
        for key in ("id", "catalogId", "equipmentId", "replacesId"):
            if key in exercise:
                require_id(exercise[key])
        for key in ("name", "equipment", "instructions"):
            exercise[key] = _localized(exercise[key])
        for key, low, high in (("sets", 1, 6), ("reps", 1, 30), ("restSeconds", 15, 300)):
            require_int(exercise[key], min=low, max=high)
        if type(exercise["completed"]) is not bool:
            raise BackendError("INVALID_INPUT", 400)
        if "animation" in exercise:
            _enum(exercise["animation"], ("row", "pulldown", "lateral", "squat"))
        if "equipmentStatus" in exercise:
            _enum(exercise["equipmentStatus"], EQUIPMENT_STATUSES)
        load = strict_object(exercise["suggestedLoad"], {"value", "unit", "basis", "source", "reason"}, {"sourceHistoryId", "sourceMessageId"})
        if load["value"] is not None:
            _number(load["value"], 0)
        _enum(load["unit"], ("kg",))
        _enum(load["basis"], ("per_hand", "machine_stack", "bodyweight"))
        _enum(load["source"], ("mock_history", "user", "missing"))
        for key in ("sourceHistoryId", "sourceMessageId"):
            if key in load:
                require_id(load[key])
        load["reason"] = _localized(load["reason"])
        exercise["suggestedLoad"] = load
    return result


def _validate_load(snapshot, exercise, evidence):
    from .load_confirmation import validate_user_load
    equipment = lookup_equipment(exercise["equipmentId"])
    if equipment is None or equipment["kind"] == "bench":
        raise BackendError("INVALID_EQUIPMENT", 400)
    load = exercise["suggestedLoad"]
    if load["basis"] != equipment["load"]["basis"]:
        raise BackendError("INVALID_LOAD", 400)
    if equipment["kind"] == "pullup_bar":
        if load["value"] is not None:
            raise BackendError("INVALID_LOAD", 400)
        return
    if load["value"] is not None and load["value"] not in equipment["load"]["allowedKg"]:
        raise BackendError("INVALID_LOAD", 400)
    if load["source"] == "user" and any(validate_user_load(snapshot, exercise, record) for record in evidence):
        return
    if load["value"] is None or load["source"] != "mock_history" or not load.get("sourceHistoryId"):
        raise BackendError("LOAD_CONFIRMATION_REQUIRED", 200)
    record = next((row for row in snapshot["history"]["load"] if row["id"] == load["sourceHistoryId"]), None)
    if record is None:
        raise BackendError("LOAD_CONFIRMATION_REQUIRED", 200)
    if record["exerciseId"] != exercise["catalogId"] or record["equipmentId"] != exercise["equipmentId"] or record["basis"] != load["basis"] or record["kg"] != load["value"] or record["date"] > snapshot["dayKey"]:
        raise BackendError("INVALID_LOAD", 400)
    if record.get("sets") != exercise["sets"] or record.get("reps") != exercise["reps"]:
        raise BackendError("LOAD_CONFIRMATION_REQUIRED", 200)
    latest = sorted((row for row in snapshot["history"]["load"] if row["exerciseId"] == exercise["catalogId"] and row["equipmentId"] == exercise["equipmentId"] and row["basis"] == load["basis"] and row.get("sets") == exercise["sets"] and row.get("reps") == exercise["reps"] and row["date"] <= snapshot["dayKey"]), key=lambda row: row["date"], reverse=True)
    if latest and latest[0]["id"] != record["id"]:
        raise BackendError("INVALID_LOAD", 400)


def validate_workout_candidate(snapshot, candidate, evidence=None):
    try:
        next_workout = _parse_workout(candidate)
    except BackendError:
        raise BackendError("INVALID_WORKOUT_PROPOSAL", 400) from None
    current = snapshot.get("workout")
    session = next((row for row in snapshot["plan"]["sessions"] if row["id"] == next_workout["trainingSessionId"]), None)
    if session is None or session["status"] == "completed" or session["date"] != snapshot["dayKey"] or snapshot["dayKey"] in snapshot["plan"]["restDates"] or next_workout["dayKey"] != snapshot["dayKey"]:
        raise BackendError("WORKOUT_NOT_SCHEDULED", 409)
    if current:
        if current["id"] != next_workout["id"] or current["trainingSessionId"] != next_workout["trainingSessionId"]:
            raise BackendError("NOT_FOUND", 404)
        if current["status"] == "completed" or any(next_workout.get(key) != current.get(key) for key in ("status", "startedAt", "endedAt", "actualMinutes")):
            raise BackendError("WORKOUT_STATE_CONFLICT", 409)
        for index, exercise in enumerate(current["exercises"]):
            if exercise["completed"] and (index >= len(next_workout["exercises"]) or canonical_json(exercise) != canonical_json(next_workout["exercises"][index])):
                raise BackendError("COMPLETED_EXERCISE_IMMUTABLE", 409)
    elif next_workout["status"] != "planned" or next_workout.get("startedAt") or next_workout.get("endedAt") or "actualMinutes" in next_workout or session.get("workoutId"):
        raise BackendError("WORKOUT_STATE_CONFLICT", 409)
    ids, replaced = set(), set()
    occupied_seen, available_count = False, 0
    for exercise in next_workout["exercises"]:
        if exercise["id"] in ids:
            raise BackendError("INVALID_EXERCISE_ID", 400)
        ids.add(exercise["id"])
        old = next((row for row in current["exercises"] if row["id"] == exercise["id"]), None) if current else None
        if exercise["completed"] and not (old and old["completed"]):
            raise BackendError("COMPLETED_EXERCISE_IMMUTABLE", 409)
        if old and old["completed"]:
            continue
        definition, equipment = _CATALOG.get(exercise["catalogId"]), lookup_equipment(exercise["equipmentId"])
        if not definition or not equipment or equipment["gymId"] != next_workout["gymId"] or equipment["kind"] != definition["equipment"] or definition["split"] != session["split"]:
            raise BackendError("INVALID_EQUIPMENT", 400)
        if old:
            if any(exercise.get(key) != old.get(key) for key in ("catalogId", "equipmentId", "replacesId")):
                raise BackendError("INVALID_EXERCISE_ID", 400)
        elif current:
            previous = next((row for row in current["exercises"] if row["id"] == exercise.get("replacesId")), None)
            if not previous or previous["completed"] or previous["id"] in replaced or any(row["id"] == previous["id"] for row in next_workout["exercises"]):
                raise BackendError("INVALID_EXERCISE_ID", 400)
            replaced.add(previous["id"])
        _validate_load(snapshot, exercise, evidence or [])
        availability = exercise_availability(snapshot, exercise)
        if availability == "unavailable":
            raise BackendError("EQUIPMENT_UNAVAILABLE", 409)
        if availability == "temporarily_occupied":
            occupied_seen = True
        else:
            if occupied_seen:
                raise BackendError("OCCUPIED_ORDER_INVALID", 409)
            available_count += 1
        exercise["equipmentStatus"] = availability
    if not any(not exercise["completed"] for exercise in next_workout["exercises"]):
        raise BackendError("EMPTY_WORKOUT", 400)
    if not available_count:
        raise BackendError("EQUIPMENT_OCCUPIED", 200)
    if next_workout["estimatedMinutes"] < minimum_workout_minutes(next_workout["exercises"]) or next_workout["estimatedMinutes"] > snapshot["conditions"]["availableMinutes"]:
        raise BackendError("WORKOUT_TIME_EXCEEDED", 400)
    next_workout["version"] = (current["version"] if current else 0) + 1
    next_workout["source"] = "agent_proposal"
    return next_workout


def invalidate_workout_advice(snapshot):
    for proposal in snapshot["proposals"]:
        if proposal["status"] == "pending":
            proposal["status"] = "stale"
    if snapshot["advice"]["status"] == "valid":
        snapshot["advice"]["status"] = "stale"


def resolve_exercise_target(snapshot, exercise_id=None):
    exercises = (snapshot.get("workout") or {}).get("exercises", [])
    if exercise_id:
        if not any(exercise["id"] == exercise_id for exercise in exercises):
            raise BackendError("NOT_FOUND", 404)
        return exercise_id
    remaining = [exercise for exercise in exercises if not exercise["completed"]]
    if len(remaining) != 1:
        raise BackendError("EXERCISE_TARGET_REQUIRED", 200)
    return remaining[0]["id"]


def _assert_today(snapshot, workout):
    session = next((row for row in snapshot["plan"]["sessions"] if row["id"] == workout["trainingSessionId"]), None)
    if not session or session.get("workoutId") != workout["id"] or session["date"] != snapshot["dayKey"] or workout["dayKey"] != snapshot["dayKey"] or not session.get("slotId") or snapshot["dayKey"] in snapshot["plan"]["restDates"]:
        raise BackendError("WORKOUT_NOT_SCHEDULED", 409)
    return session


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def transition_workout(snapshot, request):
    workout = snapshot.get("workout")
    if not workout or workout["id"] != request["workoutId"]:
        raise BackendError("NOT_FOUND", 404)
    if workout["version"] != request["expectedWorkoutVersion"]:
        raise BackendError("VERSION_CONFLICT", 409)
    session = _assert_today(snapshot, workout)
    success = {"httpStatus": 200, "result": {"requestId": request["requestId"], "status": "succeeded", "snapshot": snapshot}}
    kind = request["kind"]
    if kind == "start_workout":
        if workout["status"] == "in_progress" and session["status"] == "in_progress":
            return success
        if workout["status"] != "planned" or session["status"] != "pending":
            raise BackendError("WORKOUT_STATE_CONFLICT", 409)
        if workout["gymId"] != snapshot["conditions"]["gymId"]:
            raise BackendError("CONDITIONS_CHANGED", 409)
        if workout["estimatedMinutes"] > snapshot["conditions"]["availableMinutes"]:
            raise BackendError("WORKOUT_TIME_EXCEEDED", 409)
        if any(not exercise["completed"] and exercise_availability(snapshot, exercise) == "unavailable" for exercise in workout["exercises"]):
            raise BackendError("EQUIPMENT_UNAVAILABLE", 409)
        first = next((exercise for exercise in workout["exercises"] if not exercise["completed"]), None)
        if not first:
            raise BackendError("EMPTY_WORKOUT", 409)
        availability = exercise_availability(snapshot, first)
        if availability != "available":
            raise BackendError("EQUIPMENT_UNAVAILABLE" if availability == "unavailable" else "EQUIPMENT_OCCUPIED", 409)
        workout["status"], workout["startedAt"] = "in_progress", _now()
        session["status"] = "in_progress"
        snapshot["plan"]["pendingSessionIds"] = [value for value in snapshot["plan"]["pendingSessionIds"] if value != session["id"]]
        snapshot["plan"]["version"] += 1
    elif kind == "finish_workout":
        if workout["status"] == "completed" and workout.get("actualMinutes") == request["actualMinutes"]:
            return success
        if workout["status"] != "in_progress" or session["status"] != "in_progress":
            raise BackendError("WORKOUT_STATE_CONFLICT", 409)
        if any(not exercise["completed"] for exercise in workout["exercises"]) and not request["confirmIncomplete"]:
            return {"httpStatus": 200, "result": {"requestId": request["requestId"], "status": "needs_input", "errorCode": "INCOMPLETE_CONFIRMATION_REQUIRED", "snapshot": snapshot}}
        workout.update({"status": "completed", "endedAt": _now(), "actualMinutes": request["actualMinutes"]})
        session["status"] = "completed"
        snapshot["plan"]["pendingSessionIds"] = [value for value in snapshot["plan"]["pendingSessionIds"] if value != session["id"]]
        snapshot["plan"]["version"] += 1
        if any(record.get("workoutId") == workout["id"] or record.get("trainingSessionId") == session["id"] for record in snapshot["history"]["training"]):
            raise BackendError("WORKOUT_STATE_CONFLICT", 409)
        snapshot["history"]["training"].append({"date": snapshot["dayKey"], "type": session["split"], "minutes": request["actualMinutes"], "workoutId": workout["id"], "trainingSessionId": session["id"]})
    else:
        if workout["status"] != "in_progress" or session["status"] != "in_progress":
            raise BackendError("WORKOUT_STATE_CONFLICT", 409)
        exercise = next((row for row in workout["exercises"] if row["id"] == request["exerciseId"]), None)
        if exercise is None:
            raise BackendError("NOT_FOUND", 404)
        completed = kind == "complete_exercise"
        if exercise["completed"] == completed:
            return success
        exercise["completed"] = completed
    workout["version"] += 1
    snapshot["revision"] += 1
    invalidate_workout_advice(snapshot)
    return {"httpStatus": 200, "result": {"requestId": request["requestId"], "status": "succeeded", "operationId": str(uuid4())}, "snapshot": snapshot}


def record_workout_progress(database, session_id, request):
    from .validation import parse_action
    validated = parse_action(request)
    if validated["kind"] not in ("start_workout", "complete_exercise", "undo_exercise", "finish_workout"):
        raise BackendError("INVALID_INPUT", 400)
    if validated["source"] not in ("today", "agent", "workout"):
        raise BackendError("WORKOUT_REQUIRES_USER_ACTION", 403)
    return database.mutate(session_id, validated, lambda snapshot: transition_workout(snapshot, validated))


def build_rest_schedule(snapshot):
    if snapshot["readiness"]["quality"] != "valid" or snapshot["readiness"]["dayKey"] != snapshot["dayKey"]:
        raise BackendError("READINESS_UNAVAILABLE", 200)
    plan = snapshot["plan"]
    if snapshot["dayKey"] in plan["restDates"]:
        raise BackendError("WORKOUT_NOT_SCHEDULED", 409)
    today = [session for session in plan["sessions"] if session["date"] == snapshot["dayKey"]]
    workout = snapshot.get("workout")
    if any(session["status"] != "pending" for session in today) or (workout and any(session["id"] == workout["trainingSessionId"] for session in today) and workout["status"] != "planned"):
        raise BackendError("WORKOUT_STATE_CONFLICT", 409)
    if len(today) != 1:
        raise BackendError("WORKOUT_NOT_SCHEDULED", 409)
    ordered = plan["pendingSessionIds"]
    if len(set(ordered)) != len(ordered) or len({session["id"] for session in plan["sessions"]}) != len(plan["sessions"]) or today[0]["id"] not in ordered:
        raise BackendError("INVALID_SCHEDULE", 409)
    targets = [next((session for session in plan["sessions"] if session["id"] == key), None) for key in ordered[ordered.index(today[0]["id"]):]]
    if any(not session or session["status"] != "pending" for session in targets):
        raise BackendError("WORKOUT_STATE_CONFLICT", 409)
    target_ids = {session["id"] for session in targets}
    occupied_dates = {session["date"] for session in plan["sessions"] if session["id"] not in target_ids and session["date"] is not None}
    try:
        if len({slot["id"] for slot in plan["availableSlots"]}) != len(plan["availableSlots"]):
            raise ValueError
        for slot in plan["availableSlots"]:
            require_id(slot["id"])
            if not isinstance(slot["date"], str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", slot["date"]):
                raise ValueError
            date.fromisoformat(slot["date"])
    except (ValueError, TypeError, KeyError, BackendError):
        raise BackendError("INVALID_SCHEDULE", 409) from None
    slots, seen_dates = [], set()
    for slot in sorted(plan["availableSlots"], key=lambda row: (row["date"], row["id"])):
        if slot["date"] <= snapshot["dayKey"] or slot["date"] in occupied_dates or slot["date"] in plan["restDates"] or slot["date"] in seen_dates:
            continue
        seen_dates.add(slot["date"])
        slots.append(slot)
    moves = [{"sessionId": session["id"], "split": session["split"], "from": session["date"], "to": slots[index]["date"] if index < len(slots) else None} for index, session in enumerate(targets)]
    return {"restDate": snapshot["dayKey"], "moves": moves, "unassignedSessionIds": [move["sessionId"] for move in moves if move["to"] is None]}


def apply_rest_schedule(snapshot, proposal):
    computed = build_rest_schedule(snapshot)
    if computed["moves"] != proposal.get("moves") or proposal.get("restDate") != computed["restDate"]:
        raise BackendError("STALE_PROPOSAL", 409)
    snapshot["plan"]["restDates"].append(snapshot["dayKey"])
    for move in computed["moves"]:
        session = next(row for row in snapshot["plan"]["sessions"] if row["id"] == move["sessionId"])
        session["date"] = move["to"]
        session["slotId"] = next(slot["id"] for slot in sorted(snapshot["plan"]["availableSlots"], key=lambda row: row["id"]) if slot["date"] == move["to"]) if move["to"] else None
        if snapshot.get("workout") and snapshot["workout"]["trainingSessionId"] == session["id"]:
            snapshot["workout"]["dayKey"] = move["to"]
            snapshot["workout"]["version"] += 1
    snapshot["plan"]["version"] += 1


def _verified_loads(database, snapshot):
    from .load_confirmation import load_confirmations
    seen, evidence = set(), []
    for source in database.list_user_inputs(snapshot["sessionId"], snapshot["resetEpoch"]):
        result = load_confirmations(snapshot, source)
        if result["kind"] != "confirmed":
            continue
        for record in result["confirmations"]:
            key = tuple(record[name] for name in ("trainingSessionId", "catalogId", "equipmentId", "basis"))
            if key not in seen:
                seen.add(key)
                evidence.append(record)
    return evidence


def _failure(request_id, error, snapshot=None, **flags):
    result = {"requestId": request_id, "status": "needs_input" if error.http_status == 200 else "conflict" if error.http_status == 409 else "failed", "errorCode": error.code, **flags}
    if snapshot is not None:
        result["snapshot"] = snapshot
    return {"httpStatus": error.http_status, "result": result}


def propose_workout(database, session_id, input):
    if not isinstance(input, dict) or input.get("scope") not in ("workout", "schedule"):
        raise BackendError("INVALID_INPUT", 400)
    required = {"kind", "requestId", "resetEpoch", "runId", "contextReadId", "scope", "reason"} | ({"workout"} if input["scope"] == "workout" else set())
    request = deepcopy(strict_object(input, required))
    if request["kind"] != "propose_workout":
        raise BackendError("INVALID_INPUT", 400)
    for key in ("requestId", "runId", "contextReadId"):
        require_id(request[key])
    require_int(request["resetEpoch"])
    request["reason"] = _localized(request["reason"])
    if request["scope"] == "workout":
        request["workout"] = _parse_workout(request["workout"])

    def execute(snapshot):
        try:
            context = database.get_context_read(session_id, request["contextReadId"], request["runId"], request["resetEpoch"])
            proposal = {"id": str(uuid4()), "scope": request["scope"], "status": "pending", "reason": request["reason"], "expected": context["versions"], "contextReadId": context["id"], "readinessSnapshotId": context["readinessSnapshotId"], "runId": context["runId"], "resetEpoch": context["resetEpoch"]}
            if request["scope"] == "workout":
                proposal["workout"] = validate_workout_candidate(snapshot, request["workout"], _verified_loads(database, snapshot))
            else:
                proposal.update(build_rest_schedule(snapshot))
            for previous in snapshot["proposals"]:
                if previous["status"] == "pending":
                    previous["status"] = "stale"
            snapshot["proposals"].append(proposal)
            snapshot["revision"] += 1
            return {"httpStatus": 200, "result": {"requestId": request["requestId"], "status": "succeeded", "proposalId": proposal["id"], "operationId": str(uuid4())}, "snapshot": snapshot}
        except BackendError as error:
            return _failure(request["requestId"], error, database.get_snapshot(session_id))
    return database.mutate(session_id, request, execute)


def _find_proposal(snapshot, proposal_id):
    proposal = next((row for row in snapshot["proposals"] if row["id"] == proposal_id), None)
    if proposal is None:
        raise BackendError("NOT_FOUND", 404)
    return proposal


def _validate_proposal_context(database, snapshot, proposal):
    from .database import assert_context_versions
    if proposal["status"] != "pending" or proposal.get("resetEpoch") != snapshot["resetEpoch"] or not proposal.get("runId"):
        raise BackendError("STALE_PROPOSAL", 409)
    context = database.get_context_read(snapshot["sessionId"], proposal["contextReadId"], proposal["runId"], proposal["resetEpoch"])
    if context["readinessSnapshotId"] != proposal["readinessSnapshotId"]:
        raise BackendError("CONTEXT_STALE", 409)
    assert_context_versions(snapshot, proposal["expected"])


def apply_proposal_phase(database, session_id, request):
    def execute(snapshot):
        try:
            if request["source"] not in ("today", "agent"):
                raise BackendError("APPLY_REQUIRES_USER_ACTION", 403)
            proposal = _find_proposal(snapshot, request["proposalId"])
            if proposal["scope"] == "schedule" and request["startAfterApply"]:
                raise BackendError("REST_CANNOT_START", 400)
            if proposal["status"] == "applied":
                raise BackendError("PROPOSAL_ALREADY_APPLIED", 409)
            _validate_proposal_context(database, snapshot, proposal)
            if proposal["scope"] == "schedule":
                apply_rest_schedule(snapshot, proposal)
            else:
                if not proposal.get("workout"):
                    raise BackendError("INVALID_WORKOUT_PROPOSAL", 400)
                snapshot["workout"] = validate_workout_candidate(snapshot, proposal["workout"], _verified_loads(database, snapshot))
                session = next(row for row in snapshot["plan"]["sessions"] if row["id"] == snapshot["workout"]["trainingSessionId"])
                if session.get("workoutId") != snapshot["workout"]["id"]:
                    session["workoutId"] = snapshot["workout"]["id"]
                    snapshot["plan"]["version"] += 1
                if snapshot["conditions"]["gymId"] != snapshot["workout"]["gymId"]:
                    snapshot["conditions"]["gymId"] = snapshot["workout"]["gymId"]
                    snapshot["conditions"]["version"] += 1
            proposal["status"] = "applied"
            database.consume_readiness_check(snapshot, proposal, "applied")
            invalidate_workout_advice(snapshot)
            snapshot["revision"] += 1
            continuation = {"kind": "start_workout", "workoutId": snapshot["workout"]["id"], "expectedWorkoutVersion": snapshot["workout"]["version"]} if request["startAfterApply"] and snapshot["workout"]["status"] == "planned" else None
            outcome = {"httpStatus": 200, "result": {"requestId": request["requestId"], "status": "succeeded", "operationId": str(uuid4()), "applyStatus": "succeeded", "startStatus": ("not_started" if continuation else "succeeded") if request["startAfterApply"] else "not_requested"}, "snapshot": snapshot}
            if continuation:
                outcome["continuation"] = continuation
            return outcome
        except BackendError as error:
            return _failure(request["requestId"], error, database.get_snapshot(session_id), applyStatus="failed", startStatus="failed" if request["startAfterApply"] else "not_requested")
    return database.mutate(session_id, request, execute)


def apply_proposal(database, session_id, request):
    applied = apply_proposal_phase(database, session_id, request)
    if not applied.get("continuation"):
        return applied

    def resume(snapshot, continuation, previous):
        try:
            transition = transition_workout(snapshot, {**continuation, "requestId": request["requestId"], "resetEpoch": request["resetEpoch"], "source": request["source"]})
            transition["result"].update({"operationId": previous["operationId"], "applyStatus": "succeeded", "startStatus": "succeeded"})
            return transition
        except BackendError as error:
            return {"httpStatus": 200, "result": {"requestId": request["requestId"], "status": "failed", "errorCode": error.code, "operationId": previous["operationId"], "applyStatus": "succeeded", "startStatus": "failed", "snapshot": database.get_snapshot(session_id)}}
    try:
        return database.resume_mutation(session_id, request, resume)
    except BackendError:
        raise
    except Exception:
        snapshot = database.get_snapshot(session_id)
        return {"httpStatus": 200, "result": {"requestId": request["requestId"], "resetEpoch": snapshot["resetEpoch"], "status": "failed", "errorCode": "START_FAILED", "operationId": applied["result"]["operationId"], "applyStatus": "succeeded", "startStatus": "failed", "snapshot": snapshot}}


def dismiss_proposal(database, session_id, request):
    def execute(snapshot):
        if request["source"] not in ("today", "agent"):
            raise BackendError("APPLY_REQUIRES_USER_ACTION", 403)
        proposal = _find_proposal(snapshot, request["proposalId"])
        if proposal["status"] == "dismissed":
            return {"httpStatus": 200, "result": {"requestId": request["requestId"], "status": "succeeded", "snapshot": snapshot}}
        _validate_proposal_context(database, snapshot, proposal)
        proposal["status"] = "dismissed"
        database.consume_readiness_check(snapshot, proposal, "dismissed")
        snapshot["revision"] += 1
        return {"httpStatus": 200, "result": {"requestId": request["requestId"], "status": "succeeded", "operationId": str(uuid4())}, "snapshot": snapshot}
    return database.mutate(session_id, request, execute)

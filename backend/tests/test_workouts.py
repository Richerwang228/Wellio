from copy import deepcopy
from contextlib import closing
import psycopg
from uuid import uuid4

import pytest

from wellio.database import Database, context_versions
from wellio.errors import BackendError
from wellio.workouts import (
    apply_proposal, apply_proposal_phase, apply_rest_schedule, build_rest_schedule,
    dismiss_proposal, get_gym_equipment, lookup_equipment, minimum_workout_minutes,
    propose_workout, record_workout_progress, resolve_exercise_target,
    validate_workout_candidate,
)


def patch(database, sid, edit):
    def execute(snapshot):
        edit(snapshot)
        return {"changed": True, "value": snapshot}
    return database.runtime_transaction(sid, execute)


def progress(database, sid, kind, **values):
    snapshot = database.get_snapshot(sid)
    return {"kind": kind, "requestId": str(uuid4()), "resetEpoch": snapshot["resetEpoch"], "source": "today", "workoutId": snapshot["workout"]["id"], "expectedWorkoutVersion": snapshot["workout"]["version"], **values}


def proposal_input(database, sid, scope="workout", candidate=None):
    snapshot = database.get_snapshot(sid)
    run_id = str(uuid4())
    context = database.capture_context(sid, {"runId": run_id, "requestId": str(uuid4()), "resetEpoch": snapshot["resetEpoch"]})
    request = {"kind": "propose_workout", "requestId": str(uuid4()), "resetEpoch": snapshot["resetEpoch"], "runId": run_id, "contextReadId": context["id"], "scope": scope, "reason": {"en": "Adjust the remaining workout.", "zh-CN": "调整未完成训练。"}}
    if scope == "workout":
        request["workout"] = deepcopy(candidate or snapshot["workout"])
    return request


def apply_request(snapshot, proposal_id, start=True):
    return {"kind": "apply_proposal", "requestId": str(uuid4()), "resetEpoch": snapshot["resetEpoch"], "source": "today", "proposalId": proposal_id, "startAfterApply": start}


def test_workout_lifecycle_replay_and_incomplete_confirmation(database):
    initial = database.create_session()
    sid = initial["sessionId"]
    start = progress(database, sid, "start_workout")
    started = record_workout_progress(database, sid, start)
    assert started["result"]["snapshot"]["workout"]["status"] == "in_progress"
    assert record_workout_progress(database, sid, start) == started
    complete = progress(database, sid, "complete_exercise", exerciseId=initial["workout"]["exercises"][0]["id"])
    record_workout_progress(database, sid, complete)
    before = database.get_snapshot(sid)
    incomplete = record_workout_progress(database, sid, progress(database, sid, "finish_workout", actualMinutes=12, confirmIncomplete=False))
    assert incomplete["result"]["errorCode"] == "INCOMPLETE_CONFIRMATION_REQUIRED"
    assert database.get_snapshot(sid) == before
    finish = progress(database, sid, "finish_workout", actualMinutes=12, confirmIncomplete=True)
    finished = record_workout_progress(database, sid, finish)
    assert record_workout_progress(database, sid, finish) == finished
    saved = database.get_snapshot(sid)
    assert saved["workout"]["status"] == "completed"
    assert sum(exercise["completed"] for exercise in saved["workout"]["exercises"]) == 1
    assert len(saved["history"]["training"]) == len(initial["history"]["training"]) + 1
    assert saved["history"]["load"] == initial["history"]["load"]
    assert saved["profile"] == initial["profile"]
    with pytest.raises(BackendError, match="WORKOUT_STATE_CONFLICT"):
        record_workout_progress(database, sid, progress(database, sid, "undo_exercise", exerciseId=complete["exerciseId"]))
    assert database.get_snapshot(sid) == saved


def test_progress_versions_sources_and_ambiguous_target_are_protected(database):
    snapshot = database.create_session()
    sid = snapshot["sessionId"]
    for changes, code in [({"expectedWorkoutVersion": 9}, "VERSION_CONFLICT"), ({"source": "app_open"}, "WORKOUT_REQUIRES_USER_ACTION"), ({"unexpected": True}, "INVALID_INPUT"), ({"expectedWorkoutVersion": True}, "INVALID_INPUT")]:
        with pytest.raises(BackendError, match=code):
            record_workout_progress(database, sid, progress(database, sid, "start_workout", **changes))
        assert database.get_snapshot(sid) == snapshot
    with pytest.raises(BackendError, match="EXERCISE_TARGET_REQUIRED") as error:
        resolve_exercise_target(snapshot)
    assert error.value.http_status == 200


def test_apply_then_start_and_replay_are_persisted_once(database, database_url):
    initial = database.create_session()
    sid = initial["sessionId"]
    proposed = propose_workout(database, sid, proposal_input(database, sid))
    assert proposed["result"]["status"] == "succeeded"
    assert database.get_snapshot(sid)["workout"] == initial["workout"]
    request = apply_request(initial, proposed["result"]["proposalId"])
    applied = apply_proposal(database, sid, request)
    saved = applied["result"]["snapshot"]
    assert (applied["result"]["applyStatus"], applied["result"]["startStatus"]) == ("succeeded", "succeeded")
    assert saved["workout"]["status"] == "in_progress"
    assert saved["workout"]["version"] == initial["workout"]["version"] + 2
    assert saved["revision"] == initial["revision"] + 3
    with closing(Database(database_url)) as reopened:
        assert apply_proposal(reopened, sid, request) == applied
        assert reopened.get_snapshot(sid) == saved


def test_apply_checkpoint_survives_failed_start_and_resumes_after_restart(database, database_url, monkeypatch):
    initial = database.create_session()
    sid = initial["sessionId"]
    proposed = propose_workout(database, sid, proposal_input(database, sid))
    request = apply_request(initial, proposed["result"]["proposalId"])
    with monkeypatch.context() as scoped:
        scoped.setattr(database, "resume_mutation", lambda *_args: (_ for _ in ()).throw(psycopg.OperationalError("temporary storage error")))
        interrupted = apply_proposal(database, sid, request)
    assert interrupted["result"]["errorCode"] == "START_FAILED"
    checkpoint = database.get_snapshot(sid)
    assert checkpoint["proposals"][-1]["status"] == "applied"
    assert checkpoint["workout"]["status"] == "planned"
    with closing(Database(database_url)) as reopened:
        continued = apply_proposal(reopened, sid, request)
        assert continued["result"]["operationId"] == interrupted["result"]["operationId"]
        assert continued["result"]["snapshot"]["revision"] == checkpoint["revision"] + 1
        assert continued["result"]["startStatus"] == "succeeded"
        assert apply_proposal(reopened, sid, request) == continued


def test_failed_start_keeps_applied_candidate_without_reapplying(database):
    initial = database.create_session()
    sid = initial["sessionId"]
    proposed = propose_workout(database, sid, proposal_input(database, sid))
    request = apply_request(initial, proposed["result"]["proposalId"])
    applied = apply_proposal_phase(database, sid, request)
    patch(database, sid, lambda snapshot: snapshot["conditions"].update({"gymId": "gym-a", "version": 2}))
    before = database.get_snapshot(sid)
    failed = apply_proposal(database, sid, request)
    assert failed["result"]["errorCode"] == "CONDITIONS_CHANGED"
    assert failed["result"]["applyStatus"] == "succeeded"
    assert failed["result"]["operationId"] == applied["result"]["operationId"]
    assert database.get_snapshot(sid) == before
    assert apply_proposal(database, sid, request) == failed


def test_completed_exercises_are_immutable_and_do_not_accept_changed_loads(database):
    initial = database.create_session()
    sid = initial["sessionId"]
    record_workout_progress(database, sid, progress(database, sid, "start_workout"))
    record_workout_progress(database, sid, progress(database, sid, "complete_exercise", exerciseId=initial["workout"]["exercises"][0]["id"]))
    snapshot = database.get_snapshot(sid)
    for change in (lambda candidate: candidate["exercises"].pop(0), lambda candidate: candidate["exercises"].reverse(), lambda candidate: candidate["exercises"][0].update({"reps": 8})):
        candidate = deepcopy(snapshot["workout"])
        change(candidate)
        with pytest.raises(BackendError, match="COMPLETED_EXERCISE_IMMUTABLE"):
            validate_workout_candidate(snapshot, candidate)
    candidate = deepcopy(snapshot["workout"])
    candidate["exercises"] = candidate["exercises"][:2]
    assert validate_workout_candidate(snapshot, candidate)["exercises"][0] == snapshot["workout"]["exercises"][0]


def test_exact_history_loads_and_verified_persisted_user_sources(database):
    initial = database.create_session()
    sid = initial["sessionId"]
    candidate = deepcopy(initial["workout"])
    candidate["exercises"][0]["suggestedLoad"].update({"source": "user", "value": 30, "sourceMessageId": "confirmed-load"})
    with pytest.raises(BackendError, match="LOAD_CONFIRMATION_REQUIRED"):
        validate_workout_candidate(initial, candidate)
    source = {"id": "confirmed-load", "sessionId": sid, "resetEpoch": initial["resetEpoch"], "conversationId": initial["conversationId"], "requestId": "load-message", "content": "Use 30 kg on the machine stack for seated cable row.", "createdAt": "2026-09-12T00:00:00.000Z", "versions": context_versions(initial), "workoutContext": {"workoutId": initial["workout"]["id"], "trainingSessionId": initial["workout"]["trainingSessionId"], "gymId": initial["conditions"]["gymId"]}}
    with database.transaction():
        database.store_user_input(source)
    reply = propose_workout(database, sid, proposal_input(database, sid, candidate=candidate))
    assert reply["result"]["status"] == "succeeded"
    assert reply["result"]["snapshot"]["proposals"][-1]["workout"]["exercises"][0]["suggestedLoad"]["value"] == 30
    assert database.get_snapshot(sid)["history"]["load"] == initial["history"]["load"]
    invalid = deepcopy(initial["workout"])
    invalid["exercises"][0]["suggestedLoad"]["sourceHistoryId"] = "load-a-0902-pulldown"
    with pytest.raises(BackendError, match="INVALID_LOAD"):
        validate_workout_candidate(initial, invalid)
    invalid = deepcopy(initial["workout"])
    invalid["exercises"][0]["sets"] = 2
    with pytest.raises(BackendError, match="LOAD_CONFIRMATION_REQUIRED"):
        validate_workout_candidate(initial, invalid)


def test_rest_schedule_uses_real_slots_protects_assignments_and_leaves_overflow_unassigned(database):
    snapshot = database.create_session()
    before = deepcopy(snapshot)
    snapshot["plan"]["availableSlots"] = snapshot["plan"]["availableSlots"][:3]
    snapshot["plan"]["sessions"].append({"id": "already-finished", "split": "Push", "status": "completed", "date": "2026-09-14", "slotId": "protected"})
    computed = build_rest_schedule(snapshot)
    assert [move["to"] for move in computed["moves"]] == ["2026-09-16", None, None]
    assert computed["unassignedSessionIds"] == ["planned-legs-02", "planned-push-03"]
    protected = deepcopy(snapshot["plan"]["sessions"][-1])
    apply_rest_schedule(snapshot, computed)
    assert snapshot["plan"]["sessions"][-1] == protected
    assert snapshot["workout"]["dayKey"] == "2026-09-16"
    assert snapshot["workout"]["version"] == before["workout"]["version"] + 1
    assert snapshot["plan"]["pendingSessionIds"] == before["plan"]["pendingSessionIds"]
    assert snapshot["history"] == before["history"]
    before["plan"]["pendingSessionIds"].append(before["plan"]["pendingSessionIds"][0])
    with pytest.raises(BackendError, match="INVALID_SCHEDULE"):
        build_rest_schedule(before)


def test_schedule_apply_consumes_matching_readiness_once_and_never_starts(database):
    snapshot = database.create_session()
    sid = snapshot["sessionId"]
    proposed = propose_workout(database, sid, proposal_input(database, sid, "schedule"))
    proposal_id = proposed["result"]["proposalId"]
    check = {"sessionId": sid, "resetEpoch": snapshot["resetEpoch"], "key": "check-current", "status": "completed", "attemptId": "attempt-current", "proposalId": proposal_id}
    def attach(current):
        current["proposals"][-1].update({"checkKey": check["key"], "checkAttemptId": check["attemptId"]})
        current["readinessCheck"] = {"key": check["key"], "status": "completed", "proposalId": proposal_id}
        database.save_readiness_check(check)
    patch(database, sid, attach)
    invalid = apply_proposal(database, sid, apply_request(snapshot, proposal_id))
    assert invalid["result"]["errorCode"] == "REST_CANNOT_START"
    request = apply_request(snapshot, proposal_id, False)
    applied = apply_proposal(database, sid, request)
    assert applied["result"]["startStatus"] == "not_requested"
    assert applied["result"]["snapshot"]["readinessCheck"]["status"] == "applied"
    assert database.get_readiness_check(sid, check["key"])["status"] == "applied"
    assert applied["result"]["snapshot"]["workout"]["status"] == "planned"
    assert apply_proposal(database, sid, request) == applied


def test_equipment_ranges_occupancy_order_bench_and_timing(database):
    assert lookup_equipment("gym-a-dumbbells")["load"]["allowedKg"] == [5 + index * 2.5 for index in range(11)]
    assert lookup_equipment("gym-b-cable")["load"]["allowedKg"] == list(range(5, 51, 5))
    assert lookup_equipment("gym-b-pullup-bar") is None
    assert "load" not in lookup_equipment("gym-a-bench")
    assert get_gym_equipment("gym-b", {"gym-b-cable": "temporarily_occupied"})["equipment"][-1]["status"] == "temporarily_occupied"
    snapshot = database.create_session()
    snapshot["conditions"]["equipmentStatus"] = {"gym-b-cable": "temporarily_occupied"}
    with pytest.raises(BackendError, match="OCCUPIED_ORDER_INVALID"):
        validate_workout_candidate(snapshot, snapshot["workout"])
    candidate = deepcopy(snapshot["workout"])
    candidate["exercises"] = [candidate["exercises"][2], *candidate["exercises"][:2]]
    assert validate_workout_candidate(snapshot, candidate)["exercises"][0]["equipmentStatus"] == "available"
    snapshot["conditions"]["equipmentStatus"] = {"gym-b-cable": "unavailable"}
    with pytest.raises(BackendError, match="EQUIPMENT_UNAVAILABLE"):
        validate_workout_candidate(snapshot, candidate)
    snapshot["conditions"]["equipmentStatus"] = {}
    candidate["estimatedMinutes"] = minimum_workout_minutes(candidate["exercises"]) - 1
    with pytest.raises(BackendError, match="WORKOUT_TIME_EXCEEDED"):
        validate_workout_candidate(snapshot, candidate)


def test_stale_proposal_cannot_apply_or_dismiss(database):
    initial = database.create_session()
    sid = initial["sessionId"]
    proposed = propose_workout(database, sid, proposal_input(database, sid))
    patch(database, sid, lambda snapshot: snapshot["conditions"].update({"version": 2}))
    request = apply_request(initial, proposed["result"]["proposalId"], False)
    reply = apply_proposal(database, sid, request)
    assert reply["httpStatus"] == 409
    assert reply["result"]["errorCode"] == "VERSION_CONFLICT"
    with pytest.raises(BackendError, match="VERSION_CONFLICT"):
        dismiss_proposal(database, sid, {"kind": "dismiss_proposal", "requestId": str(uuid4()), "resetEpoch": 1, "source": "today", "proposalId": request["proposalId"]})
    assert database.get_snapshot(sid)["workout"] == initial["workout"]


def test_finish_rejects_duplicate_history_atomically_and_rest_preserves_started_session(database):
    initial = database.create_session()
    sid = initial["sessionId"]
    record_workout_progress(database, sid, progress(database, sid, "start_workout"))
    with pytest.raises(BackendError, match="WORKOUT_STATE_CONFLICT"):
        build_rest_schedule(database.get_snapshot(sid))
    patch(database, sid, lambda snapshot: snapshot["history"]["training"].append({"date": snapshot["dayKey"], "type": "Pull", "minutes": 10, "workoutId": snapshot["workout"]["id"], "trainingSessionId": snapshot["workout"]["trainingSessionId"]}))
    before = database.get_snapshot(sid)
    request = progress(database, sid, "finish_workout", actualMinutes=12, confirmIncomplete=True)
    with pytest.raises(BackendError, match="WORKOUT_STATE_CONFLICT"):
        record_workout_progress(database, sid, request)
    assert database.get_snapshot(sid) == before
    assert database.get_mutation_reply(sid, request) is None


def test_directory_rejects_null_and_unknown_status_overrides():
    for statuses in (None, [], {"gym-b-imaginary": "available"}, {"gym-b-cable": "busy"}):
        with pytest.raises(BackendError, match="INVALID_INPUT"):
            get_gym_equipment("gym-b", statuses)
    assert all(entry["status"] == "available" for entry in get_gym_equipment("gym-b")["equipment"])

from contextlib import closing
from copy import deepcopy
import hashlib
import json

import pytest

from wellio.database import Database
from wellio.runtime import readiness_key, synchronize_runtime


AVAILABLE = {"agent": True, "menuSearch": False}
UNAVAILABLE = {"agent": False, "menuSearch": False}
NOW = 1_789_171_200_000


def patch(database, sid, update):
    def execute(snapshot):
        update(snapshot)
        return {"value": snapshot, "changed": True}
    return database.runtime_transaction(sid, execute)


def pending_run(database, sid, suffix="current", lease=NOW, status="pending", epoch=None, with_check=True):
    snapshot = database.get_snapshot(sid)
    run = {"id": f"run-{suffix}", "sessionId": sid, "requestId": f"request-{suffix}", "resetEpoch": epoch if epoch is not None else snapshot["resetEpoch"], "payloadHash": "0" * 64, "request": {"requestId": f"request-{suffix}", "resetEpoch": snapshot["resetEpoch"], "conversationId": snapshot["conversationId"], "message": "", "locale": "en", "attachmentIds": [], "source": "app_open"}, "source": "app_open", "messageId": f"message-{suffix}", "status": status, "leaseExpiresAt": lease, "searchUsed": False, "toolIds": ["context", "equipment"]}
    check = None
    if with_check:
        run.update({"checkKey": readiness_key(snapshot), "checkAttemptId": f"attempt-{suffix}"})
        check = {"key": run["checkKey"], "sessionId": sid, "resetEpoch": snapshot["resetEpoch"], "status": "pending", "attemptId": run["checkAttemptId"], "runId": run["id"], "messageId": run["messageId"], "leaseExpiresAt": lease}

    def insert(current):
        current["messages"].append({"id": run["messageId"], "role": "assistant", "source": "app_open", "content": "Partial response", "createdAt": "2026-09-12T00:00:00.000Z", "status": "streaming", "phase": "thinking", "steps": [{"toolCallId": "context", "operation": "context", "status": "succeeded"}, {"toolCallId": "equipment", "operation": "equipment", "status": "started"}]})
        current["advice"] = {"status": "pending", "messageId": run["messageId"]}
        database.save_agent_run(run)
        if check:
            database.save_readiness_check(check)
            current["readinessCheck"] = {"key": check["key"], "status": check["status"], "messageId": check["messageId"]}
    patch(database, sid, insert)
    return run, check


def test_no_provider_preserves_legacy_shape_and_unchanged_revision(database):
    initial = database.create_session()
    saved = synchronize_runtime(database, initial["sessionId"], UNAVAILABLE, NOW)
    assert saved == initial
    assert "readinessCheck" not in saved
    assert database.get_readiness_check(initial["sessionId"], readiness_key(initial)) is None
    assert synchronize_runtime(database, initial["sessionId"], UNAVAILABLE, NOW) == initial


def test_check_availability_transitions_and_only_changed_snapshots_increment_revision(database):
    initial = database.create_session()
    sid = initial["sessionId"]
    idle = synchronize_runtime(database, sid, AVAILABLE, NOW)
    assert idle["readinessCheck"] == {"key": readiness_key(initial), "status": "idle"}
    assert idle["revision"] == initial["revision"] + 1
    assert synchronize_runtime(database, sid, AVAILABLE, NOW) == idle
    unavailable = synchronize_runtime(database, sid, UNAVAILABLE, NOW)
    assert unavailable["readinessCheck"] == {"key": readiness_key(initial), "status": "unavailable", "errorCode": "PROVIDER_NOT_CONFIGURED"}
    assert unavailable["revision"] == idle["revision"] + 1
    assert synchronize_runtime(database, sid, UNAVAILABLE, NOW) == unavailable
    ready = synchronize_runtime(database, sid, AVAILABLE, NOW)
    assert ready["readinessCheck"] == idle["readinessCheck"]
    assert ready["revision"] == unavailable["revision"] + 1
    assert ready["messages"] == initial["messages"]


@pytest.mark.parametrize("changes", [{"quality": "missing"}, {"quality": "stale"}, {"quality": "failed"}, {"score": None}, {"dayKey": "2026-09-11"}])
def test_unusable_readiness_creates_unavailable_without_model_or_message(database, changes):
    initial = database.create_session()
    sid = initial["sessionId"]
    patched = patch(database, sid, lambda snapshot: snapshot["readiness"].update(changes))
    saved = synchronize_runtime(database, sid, AVAILABLE, NOW)
    assert saved["readinessCheck"]["status"] == "unavailable"
    assert saved["readinessCheck"]["errorCode"] == "READINESS_UNAVAILABLE"
    assert saved["messages"] == initial["messages"]
    assert saved["revision"] == patched["revision"] + 1
    assert database.list_agent_runs(sid) == []
    assert synchronize_runtime(database, sid, AVAILABLE, NOW) == saved


def test_expired_lease_fails_run_message_pending_step_advice_and_matching_check_atomically(database, database_url):
    initial = database.create_session()
    sid = initial["sessionId"]
    synchronize_runtime(database, sid, AVAILABLE, NOW)
    run, check = pending_run(database, sid)
    before = database.get_snapshot(sid)
    saved = synchronize_runtime(database, sid, AVAILABLE, NOW)
    assert saved["revision"] == before["revision"] + 1
    assert database.get_agent_run(sid, run["id"]) == {**run, "status": "failed", "errorCode": "RUN_LEASE_EXPIRED"}
    assert database.get_readiness_check(sid, check["key"]) == {**check, "status": "failed", "errorCode": "RUN_LEASE_EXPIRED"}
    message = saved["messages"][-1]
    assert message["status"] == "failed" and message["content"] == "Partial response"
    assert "phase" not in message
    assert message["steps"][0] == before["messages"][-1]["steps"][0]
    assert message["steps"][1] == {"toolCallId": "equipment", "operation": "equipment", "status": "failed", "errorCode": "RUN_LEASE_EXPIRED"}
    assert saved["advice"] == {"status": "failed", "messageId": run["messageId"], "errorCode": "RUN_LEASE_EXPIRED"}
    assert saved["readinessCheck"] == {"key": check["key"], "status": "failed", "messageId": run["messageId"], "errorCode": "RUN_LEASE_EXPIRED"}
    assert saved["history"] == before["history"] and saved["proposals"] == before["proposals"]
    assert synchronize_runtime(database, sid, AVAILABLE, NOW + 1) == saved
    with closing(Database(database_url)) as reopened:
        assert synchronize_runtime(reopened, sid, AVAILABLE, NOW + 2) == saved


@pytest.mark.parametrize("status", ["completed", "applied", "dismissed", "failed", "stopped"])
def test_terminal_checks_never_reopen_or_get_overwritten_by_expired_attempt(database, status):
    sid = database.create_session()["sessionId"]
    run, check = pending_run(database, sid)
    check.update({"status": status, "proposalId": "saved-proposal"})
    with database.transaction():
        database.save_readiness_check(check)
    saved = synchronize_runtime(database, sid, AVAILABLE, NOW)
    assert saved["readinessCheck"]["status"] == status
    assert saved["readinessCheck"]["proposalId"] == "saved-proposal"
    assert database.get_readiness_check(sid, check["key"]) == check
    assert database.get_agent_run(sid, run["id"])["status"] == "failed"
    unavailable = synchronize_runtime(database, sid, UNAVAILABLE, NOW)
    assert unavailable["readinessCheck"] == saved["readinessCheck"]


def test_expiring_an_old_attempt_cannot_fail_a_new_pending_check(database):
    sid = database.create_session()["sessionId"]
    run, check = pending_run(database, sid)
    current_check = {**check, "attemptId": "new-attempt", "runId": "new-run", "messageId": "new-message", "leaseExpiresAt": NOW + 1000}
    with database.transaction():
        database.save_readiness_check(current_check)
    saved = synchronize_runtime(database, sid, AVAILABLE, NOW)
    assert saved["readinessCheck"] == {"key": check["key"], "status": "pending", "messageId": "new-message"}
    assert database.get_readiness_check(sid, check["key"]) == current_check
    assert database.get_agent_run(sid, run["id"])["status"] == "failed"


def test_other_epochs_unexpired_finished_and_other_sessions_are_untouched(database):
    sid = database.create_session()["sessionId"]
    other_sid = database.create_session()["sessionId"]
    runs = [pending_run(database, sid, "future", lease=NOW + 1, with_check=False)[0], pending_run(database, sid, "old-epoch", epoch=2, with_check=False)[0], pending_run(database, sid, "finished", status="completed", with_check=False)[0]]
    other, other_check = pending_run(database, other_sid, "other")
    before_other = database.get_snapshot(other_sid)
    saved = synchronize_runtime(database, sid, UNAVAILABLE, NOW)
    assert all(database.get_agent_run(sid, run["id"]) == run for run in runs)
    assert saved["messages"][-1]["status"] == "streaming"
    assert database.get_agent_run(other_sid, other["id"]) == other
    assert database.get_readiness_check(other_sid, other_check["key"]) == other_check
    assert database.get_snapshot(other_sid) == before_other


def test_storage_failure_rolls_back_run_and_snapshot_updates(database, monkeypatch):
    sid = database.create_session()["sessionId"]
    run, check = pending_run(database, sid)
    before = database.get_snapshot(sid)
    with monkeypatch.context() as scoped:
        scoped.setattr(database, "save_readiness_check", lambda *_args: (_ for _ in ()).throw(RuntimeError("controlled write failure")))
        with pytest.raises(RuntimeError, match="controlled write failure"):
            synchronize_runtime(database, sid, AVAILABLE, NOW)
    assert database.get_agent_run(sid, run["id"]) == run
    assert database.get_readiness_check(sid, check["key"]) == check
    assert database.get_snapshot(sid) == before


def test_readiness_identity_changes_only_for_session_epoch_day_or_readiness(database):
    snapshot = database.create_session()
    identity = [snapshot["sessionId"], snapshot["resetEpoch"], snapshot["dayKey"], snapshot["readiness"]["id"], snapshot["readiness"]["version"]]
    assert readiness_key(snapshot) == hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
    original = readiness_key(snapshot)
    unrelated = deepcopy(snapshot)
    unrelated["locale"] = "zh-CN"
    unrelated["conditions"]["version"] += 1
    unrelated["mealRevision"] += 1
    unrelated["plan"]["version"] += 1
    unrelated["workout"]["version"] += 1
    assert readiness_key(unrelated) == original
    for key in ("id", "version"):
        changed = deepcopy(snapshot)
        changed["readiness"][key] = "new-readiness" if key == "id" else 2
        assert readiness_key(changed) != original

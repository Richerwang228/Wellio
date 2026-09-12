"""Synchronize persisted run/check state without executing an agent."""
from hashlib import sha256
from time import time_ns

from .validation import canonical_json


def readiness_key(snapshot):
    identity = [snapshot["sessionId"], snapshot["resetEpoch"], snapshot["dayKey"], snapshot["readiness"]["id"], snapshot["readiness"]["version"]]
    return sha256(canonical_json(identity).encode("utf-8")).hexdigest()


def _public_check(check):
    return {"key": check["key"], "status": check["status"], **{key: check[key] for key in ("messageId", "proposalId", "errorCode") if check.get(key)}}


def _valid_readiness(snapshot):
    readiness = snapshot["readiness"]
    return readiness["quality"] == "valid" and readiness["dayKey"] == snapshot["dayKey"] and readiness["score"] is not None


def _stop_record(database, snapshot, run, status, code):
    run.update({"status": status, "errorCode": code})
    database.save_agent_run(run)
    message = next((message for message in snapshot["messages"] if message["id"] == run["messageId"]), None)
    if message:
        message.update({"status": status, "errorCode": code})
        message.pop("phase", None)
        for step in message["steps"]:
            if step["status"] == "started":
                step.update({"status": "failed", "errorCode": code})
    if run.get("checkKey"):
        check = database.get_readiness_check(snapshot["sessionId"], run["checkKey"])
        if check and check.get("attemptId") == run.get("checkAttemptId") and check["status"] == "pending":
            check.update({"status": status, "errorCode": code})
            database.save_readiness_check(check)
            if (snapshot.get("readinessCheck") or {}).get("key") == check["key"]:
                snapshot["readinessCheck"] = _public_check(check)
    if snapshot["advice"].get("messageId") == run["messageId"] and snapshot["advice"]["status"] == "pending":
        snapshot["advice"] = {**snapshot["advice"], "status": "failed", "errorCode": code}


def invalidate_readiness_context(database, snapshot):
    """Fence old runs before replacing watch facts, while retaining chat history."""
    for run in database.list_agent_runs(snapshot["sessionId"]):
        if run["resetEpoch"] == snapshot["resetEpoch"] and run["status"] == "pending":
            _stop_record(database, snapshot, run, "stopped", "READINESS_CHANGED")
    for proposal in snapshot["proposals"]:
        if proposal["status"] == "pending":
            proposal["status"] = "stale"
    if snapshot["advice"]["status"] != "unavailable":
        snapshot["advice"]["status"] = "stale"
        snapshot["advice"].pop("errorCode", None)


def synchronize_snapshot(database, snapshot, capabilities, now=None):
    """Shared synchronization inside an existing locked database transaction."""
    current_time = time_ns() // 1_000_000 if now is None else now
    session_id = snapshot["sessionId"]
    before = canonical_json(snapshot)
    for run in database.list_agent_runs(session_id):
        if run["resetEpoch"] == snapshot["resetEpoch"] and run["status"] == "pending" and run["leaseExpiresAt"] <= current_time:
            _stop_record(database, snapshot, run, "failed", "RUN_LEASE_EXPIRED")
    snapshot["capabilities"] = {**capabilities, "persistence": "server"}
    key = readiness_key(snapshot)
    check = database.get_readiness_check(session_id, key)
    # Keep legacy no-provider seeds unchanged; an explicit demo switch gets a check.
    if check or capabilities["agent"] or snapshot["readiness"].get("demoMode"):
        available = capabilities["agent"] and _valid_readiness(snapshot)
        unavailable_code = "READINESS_UNAVAILABLE" if capabilities["agent"] else "PROVIDER_NOT_CONFIGURED"
        if not check:
            check = {"sessionId": session_id, "resetEpoch": snapshot["resetEpoch"], "key": key, "status": "idle" if available else "unavailable"}
            if not available:
                check["errorCode"] = unavailable_code
        elif check["status"] == "unavailable" and available:
            check["status"] = "idle"
            check.pop("errorCode", None)
        elif check["status"] == "idle" and not available:
            check.update({"status": "unavailable", "errorCode": unavailable_code})
        database.save_readiness_check(check)
        snapshot["readinessCheck"] = _public_check(check)
    return {"value": snapshot, "changed": before != canonical_json(snapshot)}


def synchronize_runtime(database, session_id, capabilities, now=None):
    """Atomically expire leases and expose server capabilities/checks; now is Unix ms."""
    def synchronize(snapshot):
        return synchronize_snapshot(database, snapshot, capabilities, now)

    return database.runtime_transaction(session_id, synchronize)

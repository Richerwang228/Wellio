from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
import copy
import json
import psycopg

import pytest


def request(snapshot, request_id="storage-change"):
    return {"kind": "storage_test", "requestId": request_id, "resetEpoch": snapshot["resetEpoch"]}


def locale_outcome(snapshot, locale="zh-CN"):
    snapshot["locale"] = locale
    snapshot["revision"] += 1
    return {"httpStatus": 200, "result": {"status": "succeeded"}, "snapshot": snapshot}


def receipt_count(path):
    with psycopg.connect(path) as connection:
        return connection.execute("SELECT COUNT(*) FROM action_requests").fetchone()[0]


def test_mutation_callback_failure_rolls_back_snapshot_and_receipt(database, database_url):
    initial = database.create_session()

    def fail(snapshot):
        snapshot["locale"] = "zh-CN"
        snapshot["revision"] += 1
        raise RuntimeError("controlled transaction failure")

    with pytest.raises(RuntimeError, match="controlled transaction failure"):
        database.mutate(initial["sessionId"], request(initial), fail)
    assert database.get_snapshot(initial["sessionId"]) == initial
    assert receipt_count(database_url) == 0
    saved = database.mutate(initial["sessionId"], request(initial), locale_outcome)
    assert saved["result"]["snapshot"]["locale"] == "zh-CN"
    assert receipt_count(database_url) == 1


def test_async_callback_is_rejected_before_any_state_or_receipt_commits(database, database_url):
    initial = database.create_session()

    async def async_change(snapshot):
        return locale_outcome(snapshot)

    pending = async_change(copy.deepcopy(initial))
    try:
        with pytest.raises(Exception, match="ASYNC"):
            database.mutate(initial["sessionId"], request(initial), lambda snapshot: pending)
    finally:
        pending.close()
    assert database.get_snapshot(initial["sessionId"]) == initial
    assert receipt_count(database_url) == 0


def test_sql_failure_after_snapshot_update_rolls_back_all_changes(database, database_url):
    initial = database.create_session()
    with psycopg.connect(database_url) as connection:
        connection.execute("CREATE FUNCTION reject_receipt_fn() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'injected receipt failure'; END $$")
        connection.execute("CREATE TRIGGER reject_receipt BEFORE INSERT ON action_requests FOR EACH ROW EXECUTE FUNCTION reject_receipt_fn()")
    with pytest.raises(Exception, match="injected receipt failure"):
        database.mutate(initial["sessionId"], request(initial), locale_outcome)
    assert database.get_snapshot(initial["sessionId"]) == initial
    assert receipt_count(database_url) == 0
    with psycopg.connect(database_url) as connection:
        connection.execute("DROP TRIGGER reject_receipt ON action_requests")
    assert database.mutate(initial["sessionId"], request(initial), locale_outcome)["result"]["status"] == "succeeded"


def test_invalid_revision_transition_cannot_commit(database, database_url):
    initial = database.create_session()

    def wrong_version(snapshot):
        outcome = locale_outcome(snapshot)
        outcome["snapshot"]["revision"] += 1
        return outcome

    with pytest.raises(Exception, match="INVALID_MUTATION_VERSION"):
        database.mutate(initial["sessionId"], request(initial), wrong_version)
    assert database.get_snapshot(initial["sessionId"]) == initial
    assert receipt_count(database_url) == 0


def test_nested_mutation_does_not_commit_the_outer_transaction(database, database_url):
    initial = database.create_session()

    def nested(snapshot):
        database.mutate(initial["sessionId"], request(initial, "inner-change"), locale_outcome)
        return locale_outcome(snapshot)

    with pytest.raises(Exception, match="NESTED_TRANSACTION_NOT_ALLOWED"):
        database.mutate(initial["sessionId"], request(initial), nested)
    assert database.get_snapshot(initial["sessionId"]) == initial
    assert receipt_count(database_url) == 0


def test_two_database_connections_serialize_identical_mutations(database_url):
    from wellio.database import Database

    setup = Database(database_url)
    initial = setup.create_session()
    setup.close()

    def write(_):
        db = Database(database_url)
        try:
            return db.mutate(initial["sessionId"], request(initial), locale_outcome)
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(write, range(2)))
    assert replies[0] == replies[1]
    reopened = Database(database_url)
    try:
        saved = reopened.get_snapshot(initial["sessionId"])
        assert saved["revision"] == initial["revision"] + 1
        assert saved["locale"] == "zh-CN"
    finally:
        reopened.close()
    assert receipt_count(database_url) == 1


def test_distinct_concurrent_requests_see_each_others_committed_version(database_url):
    from wellio.database import Database

    first, second = Database(database_url), Database(database_url)
    initial = first.create_session()
    ready = Barrier(2)

    def write(index):
        ready.wait(timeout=5)

        def consume_minute(snapshot):
            snapshot["conditions"]["availableMinutes"] -= 1
            snapshot["conditions"]["version"] += 1
            snapshot["revision"] += 1
            return {"httpStatus": 200, "result": {"status": "succeeded"}, "snapshot": snapshot}

        return (first, second)[index].mutate(initial["sessionId"], request(initial, f"parallel-{index}"), consume_minute)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            replies = list(pool.map(write, range(2)))
        assert {reply["result"]["snapshot"]["revision"] for reply in replies} == {initial["revision"] + 1, initial["revision"] + 2}
        saved = first.get_snapshot(initial["sessionId"])
        assert saved["conditions"]["availableMinutes"] == initial["conditions"]["availableMinutes"] - 2
        assert saved["conditions"]["version"] == initial["conditions"]["version"] + 2
        assert receipt_count(database_url) == 2
    finally:
        first.close()
        second.close()


def test_long_transaction_for_one_session_does_not_lock_another_session(database_url):
    from wellio.database import Database

    first, second = Database(database_url), Database(database_url)
    a, b = first.create_session(), second.create_session()
    entered, release = Event(), Event()

    def held(snapshot):
        entered.set()
        assert release.wait(timeout=5)
        return locale_outcome(snapshot)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(first.mutate, a["sessionId"], request(a), held)
            try:
                assert entered.wait(timeout=5)
                independent = pool.submit(second.mutate, b["sessionId"], request(b), locale_outcome)
                assert independent.result(timeout=2)["result"]["snapshot"]["locale"] == "zh-CN"
                assert not pending.done()
            finally:
                release.set()
            assert pending.result(timeout=5)["result"]["status"] == "succeeded"
    finally:
        first.close()
        second.close()


def test_persisted_signing_key_and_snapshot_survive_reopen(database_url):
    from wellio.database import Database

    first = Database(database_url)
    key = first.signing_key
    initial = first.create_session()
    first.close()
    assert isinstance(key, bytes) and len(key) == 32
    reopened = Database(database_url)
    try:
        assert reopened.signing_key == key
        assert reopened.get_snapshot(initial["sessionId"]) == initial
    finally:
        reopened.close()


def test_future_schema_is_rejected_without_recreating_the_database(database_url):
    from wellio.database import Database

    initialized = Database(database_url)
    initialized.close()
    with psycopg.connect(database_url) as connection:
        connection.execute("CREATE TABLE future_data(value TEXT)")
        connection.execute("INSERT INTO future_data VALUES ('preserve me')")
        connection.execute("INSERT INTO schema_migrations(version, name, checksum) VALUES (999, 'future.sql', 'future-checksum')")
    with pytest.raises(Exception, match="DATABASE_SCHEMA_TOO_NEW"):
        Database(database_url)
    with psycopg.connect(database_url) as connection:
        assert connection.execute("SELECT value FROM future_data").fetchone()[0] == "preserve me"
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 999


def test_changed_applied_migration_is_rejected_without_touching_session(database_url):
    from wellio.database import Database

    database = Database(database_url)
    initial = database.create_session()
    database.close()
    with psycopg.connect(database_url) as connection:
        connection.execute("UPDATE schema_migrations SET checksum = 'changed-checksum' WHERE version = 1")
    with pytest.raises(ValueError, match="DATABASE_MIGRATION_CHANGED"):
        Database(database_url)
    with psycopg.connect(database_url) as connection:
        assert connection.execute("SELECT snapshot_json FROM sessions WHERE id = %s", (initial["sessionId"],)).fetchone()[0] == initial
        assert connection.execute("SELECT checksum FROM schema_migrations WHERE version = 1").fetchone()[0] == "changed-checksum"


@pytest.mark.parametrize("field,value", [("schemaVersion", 999), ("sessionId", "forged-session"), ("revision", 999), ("resetEpoch", 999), ("revision", True), ("resetEpoch", True)])
def test_snapshot_json_must_match_the_authoritative_row(database, database_url, field, value):
    initial = database.create_session()
    corrupt = {**initial, field: value}
    with psycopg.connect(database_url) as connection:
        connection.execute("UPDATE sessions SET snapshot_json = %s WHERE id = %s", (json.dumps(corrupt), initial["sessionId"]))
    with pytest.raises(Exception, match="CORRUPT_SESSION_STATE|SNAPSHOT_SCHEMA_UNSUPPORTED"):
        database.get_snapshot(initial["sessionId"])


@pytest.mark.parametrize("corrupt_key", [" " * 64, "A" * 64, "g" * 64])
def test_invalid_persisted_signing_key_cannot_be_silently_accepted(database_url, corrupt_key):
    from wellio.database import Database

    database = Database(database_url)
    database.close()
    with psycopg.connect(database_url) as connection:
        connection.execute("UPDATE server_metadata SET value = %s WHERE key = 'session_signing_key_v1'", (corrupt_key,))
    with pytest.raises(Exception, match="INVALID_SESSION_SIGNING_KEY"):
        Database(database_url)


def test_context_read_is_persistent_scoped_and_rejects_changed_versions(database, database_url):
    initial = database.create_session()
    session_id = initial["sessionId"]
    inputs = {"runId": "trusted-run", "requestId": "read-before", "resetEpoch": initial["resetEpoch"]}
    captured = database.capture_context(session_id, inputs)
    assert database.get_context_read(session_id, captured["id"], inputs["runId"], inputs["resetEpoch"]) == captured
    assert database.capture_context(session_id, inputs) == captured
    other = database.create_session()
    with pytest.raises(Exception, match="CONTEXT_READ_INVALID"):
        database.get_context_read(other["sessionId"], captured["id"], inputs["runId"], inputs["resetEpoch"])
    with pytest.raises(Exception, match="CONTEXT_READ_INVALID"):
        database.get_context_read(session_id, captured["id"], "another-run", inputs["resetEpoch"])

    def change_conditions(snapshot):
        snapshot["conditions"]["version"] += 1
        snapshot["conditions"]["availableMinutes"] = 20
        snapshot["revision"] += 1
        return {"httpStatus": 200, "result": {"status": "succeeded"}, "snapshot": snapshot}

    database.mutate(session_id, request(initial), change_conditions)
    with pytest.raises(Exception, match="VERSION_CONFLICT"):
        database.get_context_read(session_id, captured["id"], inputs["runId"], inputs["resetEpoch"])
    fresh = database.capture_context(session_id, {**inputs, "requestId": "read-after"})
    assert fresh["id"] != captured["id"]
    assert fresh["snapshot"]["conditions"]["availableMinutes"] == 20
    assert fresh["versions"]["conditions"] == captured["versions"]["conditions"] + 1
    with psycopg.connect(database_url) as connection:
        assert connection.execute("SELECT record_json FROM context_reads WHERE id = %s", (captured["id"],)).fetchone()[0] == captured

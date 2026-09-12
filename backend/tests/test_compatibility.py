from __future__ import annotations

import copy
import json
from pathlib import Path

import psycopg
import pytest


def normalized_seed(snapshot):
    result = copy.deepcopy(snapshot)
    for field in ("sessionId", "conversationId", "resetEpoch", "revision"):
        result.pop(field, None)
    return result


REFERENCE = json.loads((Path(__file__).parent / "fixtures/typescript-contract.json").read_text(encoding="utf-8"))


def test_json_and_payload_hash_match_frozen_typescript_for_numeric_unicode_and_nested_inputs():
    from wellio.validation import canonical_json, payload_hash

    vectors = REFERENCE["canonicalVectors"]
    assert len(vectors) == 4
    for vector in vectors:
        assert canonical_json(vector["input"]) == vector["canonical"]
        assert payload_hash(vector["input"]) == vector["hash"]
    assert payload_hash({"n": 1}) == payload_hash({"n": 1.0})
    assert payload_hash({"n": 0}) == payload_hash({"n": -0.0})
    assert payload_hash({"items": [1, 2]}) != payload_hash({"items": [2, 1]})


def test_pg_receipt_replay_after_reopen_uses_canonical_payload_without_losing_state(database_url):
    from wellio.database import Database
    from wellio.validation import payload_hash

    database = Database(database_url)
    initial = database.create_session()
    request = {
        "kind": "compatibility_probe", "requestId": "canonical-restart", "resetEpoch": initial["resetEpoch"],
        "payload": {"z": [1, {"薯条": 0.5}, "🍟"], "a": {"预算": 70, "quantity": 1}},
    }

    def change(snapshot):
        snapshot["locale"] = "zh-CN"
        snapshot["revision"] += 1
        return {"httpStatus": 200, "snapshot": snapshot, "result": {"status": "succeeded", "message": "已保存。"}}

    saved = database.mutate(initial["sessionId"], request, change)
    key = database.signing_key
    database.close()
    replay = {
        "payload": {"a": {"quantity": 1.0, "预算": 70.0}, "z": [1.0, {"薯条": 0.5}, "🍟"]},
        "resetEpoch": initial["resetEpoch"], "requestId": request["requestId"], "kind": request["kind"],
    }
    database = Database(database_url)
    try:
        def must_not_run(snapshot):
            pytest.fail("A durable receipt must replay without executing the mutation again")

        assert database.signing_key == key
        assert database.mutate(initial["sessionId"], replay, must_not_run) == saved
        actual = database.get_snapshot(initial["sessionId"])
        assert actual == saved["result"]["snapshot"]
        assert normalized_seed(actual) == {**normalized_seed(initial), "locale": "zh-CN"}
        reordered_array = copy.deepcopy(replay)
        reordered_array["payload"]["z"].reverse()
        with pytest.raises(Exception, match="IDEMPOTENCY_CONFLICT"):
            database.mutate(initial["sessionId"], reordered_array, must_not_run)
        assert database.get_snapshot(initial["sessionId"]) == actual
    finally:
        database.close()
    with psycopg.connect(database_url) as connection:
        rows = connection.execute("SELECT payload_hash, result_json FROM action_requests").fetchall()
        assert rows == [(payload_hash(request), saved["result"])]


@pytest.mark.parametrize("scenario", ["normal", "low_recovery"])
def test_python_seed_matches_original_typescript_shape_and_business_values(client, scenario):
    from wellio.validation import payload_hash
    response = client.get("/api/state")
    assert response.status_code == 200
    initial = response.json()
    if scenario == "normal":
        actual = initial
    else:
        response = client.post("/api/actions", json={"kind": "reset_demo", "requestId": "compare-low-seed", "resetEpoch": initial["resetEpoch"], "source": "profile", "scenario": scenario})
        assert response.status_code == 200
        actual = response.json()["snapshot"]
    assert payload_hash(normalized_seed(actual)) == REFERENCE["seedHashes"][scenario]

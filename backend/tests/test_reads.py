from copy import deepcopy
from datetime import date, timedelta

import pytest

from wellio.errors import BackendError
from wellio.read_services import calculate_daily_totals, calculate_meal_totals, get_day_context, query_history


def test_nutrition_is_absolute_fraction_preserves_negative_remaining_and_missing(database):
    snapshot = database.create_session()
    item = snapshot["meals"][0]["items"][0]
    item.update({"base": {"kcal": 300, "protein": 7.5, "carbs": 35, "fat": 15}, "consumedFraction": 0.5})
    snapshot["meals"] = [{**snapshot["meals"][0], "items": [item]}]
    snapshot["profile"]["targets"] = {"kcal": 100, "protein": 1, "carbs": 1, "fat": 1}
    original = deepcopy(snapshot)
    meal = calculate_meal_totals(snapshot["meals"][0])
    assert meal["total"] == {"kcal": 150, "protein": 3.75, "carbs": 17.5, "fat": 7.5}
    assert meal["items"][0]["total"] == meal["total"]
    daily = calculate_daily_totals(snapshot)
    assert daily["consumed"] == meal["total"]
    assert daily["remaining"]["kcal"] == -50
    assert daily["energyDeficit"] == snapshot["profile"]["expenditure"] - 150
    assert snapshot == original
    snapshot["meals"] = []
    missing = calculate_daily_totals(snapshot)
    assert missing["consumed"] is None and missing["remaining"] is None and missing["energyDeficit"] is None
    assert missing["intakeStatus"] == "missing"


def test_context_captures_actual_session_snapshot_and_replays_without_revision_change(database):
    initial = database.create_session()
    request = {"runId": "read-run", "requestId": "read-request", "resetEpoch": initial["resetEpoch"]}
    context = get_day_context(database, initial["sessionId"], request)
    assert context["snapshot"] == initial
    assert context["readiness"] == initial["readiness"]
    assert context["totals"] == calculate_daily_totals(initial)
    assert context["contextReadId"] == context["id"]
    assert get_day_context(database, initial["sessionId"], request) == context
    assert database.get_snapshot(initial["sessionId"]) == initial
    with pytest.raises(BackendError, match="INVALID_INPUT"):
        get_day_context(database, initial["sessionId"], {**request, "sessionId": "forged"})
    with pytest.raises(BackendError, match="STALE_EPOCH"):
        get_day_context(database, initial["sessionId"], {**request, "resetEpoch": 2})


def test_history_summaries_are_scoped_sorted_and_equipment_specific(database):
    snapshot = database.create_session()
    sid = snapshot["sessionId"]
    query = {"from": "2026-08-29", "to": snapshot["dayKey"]}
    weight = query_history(database, sid, {**query, "metric": "weight"})
    assert weight["summary"]["latestKg"] == weight["data"][-1]["kg"]
    assert weight["summary"]["changeKg"] == pytest.approx(weight["data"][-1]["kg"] - weight["data"][0]["kg"])
    training = query_history(database, sid, {**query, "metric": "training"})
    assert training["summary"]["totalMinutes"] == sum(row["minutes"] for row in training["data"])
    nutrition = query_history(database, sid, {**query, "metric": "nutrition"})
    assert nutrition["summary"]["consumed"]["kcal"] == sum(row["kcal"] for row in nutrition["data"])
    assert nutrition["summary"]["energyDeficit"] == sum(row["expenditure"] - row["kcal"] for row in nutrition["data"])
    assert nutrition["summary"]["averageDaily"]["expenditure"] == pytest.approx(sum(row["expenditure"] for row in nutrition["data"]) / len(nutrition["data"]), abs=1e-6)
    loads = query_history(database, sid, {**query, "metric": "exercise_load", "exerciseId": "lat-pulldown", "equipmentId": "gym-b-cable"})
    assert loads["summary"]["basis"] == "machine_stack"
    assert all(row["equipmentId"] == "gym-b-cable" and row["basis"] == "machine_stack" for row in loads["data"])
    assert loads["summary"]["latestKg"] == 40
    loads["data"][0]["kg"] = 999
    assert database.get_snapshot(sid) == snapshot


@pytest.mark.parametrize("query,code", [
    ({"metric": "weight", "from": "2026-08-12", "to": "2026-09-12"}, "INVALID_DATE_RANGE"),
    ({"metric": "weight", "from": "2026-09-13", "to": "2026-09-13"}, "INVALID_DATE_RANGE"),
    ({"metric": "weight", "from": "2026-09-12", "to": "2026-09-11"}, "INVALID_DATE_RANGE"),
    ({"metric": "weight", "from": "2026-02-30", "to": "2026-09-12"}, "INVALID_INPUT"),
    ({"metric": "weight", "from": "2026-9-01", "to": "2026-09-12"}, "INVALID_INPUT"),
    ({"metric": "weight", "from": "2026-09-01", "to": "2026-09-12", "sql": "SELECT * FROM sessions"}, "INVALID_INPUT"),
    ({"metric": "exercise_load", "from": "2026-09-01", "to": "2026-09-12"}, "INVALID_INPUT"),
    ({"metric": "exercise_load", "from": "2026-09-01", "to": "2026-09-12", "exerciseId": "row", "equipmentId": "gym-b-pullup-bar"}, "EQUIPMENT_NOT_FOUND"),
    ({"metric": "exercise_load", "from": "2026-09-01", "to": "2026-09-12", "exerciseId": "row", "equipmentId": "gym-b-bench"}, "EQUIPMENT_HAS_NO_LOAD"),
])
def test_history_rejects_unbounded_or_unstructured_queries(database, query, code):
    snapshot = database.create_session()
    with pytest.raises(BackendError, match=code):
        query_history(database, snapshot["sessionId"], query)


def test_exact_31_day_boundary_empty_history_and_stale_epoch(database):
    snapshot = database.create_session()
    query = {"metric": "weight", "from": str(date.fromisoformat(snapshot["dayKey"]) - timedelta(days=30)), "to": snapshot["dayKey"]}
    assert query_history(database, snapshot["sessionId"], query)["summary"]["recordCount"] > 0
    with pytest.raises(BackendError, match="STALE_EPOCH"):
        query_history(database, snapshot["sessionId"], {**query, "resetEpoch": 2})
    empty = query_history(database, snapshot["sessionId"], {"metric": "nutrition", "from": "2026-01-01", "to": "2026-01-02"})
    assert empty["summary"] == {"recordCount": 0, "consumed": None, "expenditure": None, "energyDeficit": None, "averageDaily": None}

"""Authenticated, bounded reads and pure nutrition calculations."""
from copy import deepcopy
from datetime import date
from math import floor
import re

from .errors import BackendError
from .validation import strict_object, require_id, require_int
from .workouts import lookup_equipment

NUTRIENTS = ("kcal", "protein", "carbs", "fat")


def _round(value):
    # Match JavaScript Math.round, including negative half values.
    return floor(value * 1_000_000 + 0.5) / 1_000_000


def _nutrients(calculate):
    return {key: _round(calculate(key)) for key in NUTRIENTS}


def calculate_meal_totals(meal):
    items = [{"mealItemId": item["id"], "total": _nutrients(lambda key: item["base"][key] * item["consumedFraction"])} for item in meal["items"]]
    return {"mealId": meal["id"], "total": _nutrients(lambda key: sum(item["base"][key] * item["consumedFraction"] for item in meal["items"])), "items": items}


def calculate_daily_totals(snapshot):
    items = [item for meal in snapshot["meals"] for item in meal["items"]]
    consumed = _nutrients(lambda key: sum(item["base"][key] * item["consumedFraction"] for item in items)) if items else None
    return {
        "consumed": consumed,
        "remaining": _nutrients(lambda key: snapshot["profile"]["targets"][key] - consumed[key]) if consumed is not None else None,
        "expenditure": snapshot["profile"]["expenditure"],
        "energyDeficit": _round(snapshot["profile"]["expenditure"] - consumed["kcal"]) if consumed is not None else None,
        "mealCount": len(snapshot["meals"]),
        "estimated": any(item["estimated"] for item in items),
        "intakeStatus": "recorded_so_far" if consumed is not None else "missing",
    }


def get_day_context(database, session_id, input):
    query = strict_object(input, {"runId", "requestId", "resetEpoch"})
    require_id(query["runId"])
    require_id(query["requestId"])
    require_int(query["resetEpoch"])
    context = database.capture_context(session_id, query)
    return {**context, "contextReadId": context["id"], "readiness": context["snapshot"]["readiness"], "totals": calculate_daily_totals(context["snapshot"])}


def _date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise BackendError("INVALID_INPUT", 400)
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise BackendError("INVALID_INPUT", 400) from None


def _numeric_summary(records):
    first, latest = (records[0], records[-1]) if records else (None, None)
    return {
        "recordCount": len(records), "latestDate": latest["date"] if latest else None,
        "latestKg": latest["kg"] if latest else None,
        "changeKg": _round(latest["kg"] - first["kg"]) if latest else None,
        "minKg": min(record["kg"] for record in records) if records else None,
        "maxKg": max(record["kg"] for record in records) if records else None,
    }


def query_history(database, session_id, input):
    if not isinstance(input, dict) or input.get("metric") not in ("weight", "training", "nutrition", "exercise_load"):
        raise BackendError("INVALID_INPUT", 400)
    required = {"metric", "from", "to"}
    if input["metric"] == "exercise_load":
        required |= {"exerciseId", "equipmentId"}
    query = strict_object(input, required, {"resetEpoch"})
    start, end = _date(query["from"]), _date(query["to"])
    if "resetEpoch" in query:
        require_int(query["resetEpoch"])
    if query["metric"] == "exercise_load":
        require_id(query["exerciseId"])
        require_id(query["equipmentId"])
    snapshot = database.get_snapshot(session_id)
    if "resetEpoch" in query and query["resetEpoch"] != snapshot["resetEpoch"]:
        raise BackendError("STALE_EPOCH", 409)
    if not 1 <= (end - start).days + 1 <= 31 or query["to"] > snapshot["dayKey"]:
        raise BackendError("INVALID_DATE_RANGE", 400)
    result = {"from": query["from"], "to": query["to"], "dayKey": snapshot["dayKey"], "resetEpoch": snapshot["resetEpoch"], "metric": query["metric"]}
    metric = query["metric"]
    records = snapshot["history"]["load" if metric == "exercise_load" else metric]
    data = deepcopy(sorted((record for record in records if query["from"] <= record["date"] <= query["to"]), key=lambda row: row["date"]))
    if metric == "weight":
        summary = _numeric_summary(data)
    elif metric == "training":
        summary = {"recordCount": len(data), "sessionCount": sum(record["minutes"] > 0 for record in data), "totalMinutes": _round(sum(record["minutes"] for record in data))}
    elif metric == "nutrition":
        for record in data:
            record["energyDeficit"] = _round(record["expenditure"] - record["kcal"])
        consumed = {key: 0 for key in NUTRIENTS} if data else None
        for record in data:
            consumed = _nutrients(lambda key: consumed[key] + record[key])
        expenditure = _round(sum(record["expenditure"] for record in data)) if data else None
        deficit = _round(expenditure - consumed["kcal"]) if data else None
        average = {**_nutrients(lambda key: consumed[key] / len(data)), "expenditure": _round(expenditure / len(data)), "energyDeficit": _round(deficit / len(data))} if data else None
        summary = {"recordCount": len(data), "consumed": consumed, "expenditure": expenditure, "energyDeficit": deficit, "averageDaily": average}
    else:
        equipment = lookup_equipment(query["equipmentId"])
        if equipment is None:
            raise BackendError("EQUIPMENT_NOT_FOUND", 404)
        if "load" not in equipment:
            raise BackendError("EQUIPMENT_HAS_NO_LOAD", 400)
        basis = equipment["load"]["basis"]
        data = [record for record in data if record["exerciseId"] == query["exerciseId"] and record["equipmentId"] == query["equipmentId"] and record["basis"] == basis]
        summary = _numeric_summary(data)
        if basis == "bodyweight":
            summary.update(dict.fromkeys(("latestKg", "changeKg", "minKg", "maxKg")))
        summary.update({"exerciseId": query["exerciseId"], "equipmentId": query["equipmentId"], "basis": basis})
    return {**result, "data": data, "summary": summary}

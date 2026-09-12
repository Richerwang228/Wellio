"""Strict inputs and ECMAScript-compatible request hashes shared across runtimes."""
import hashlib
import math
import re
from typing import Annotated, Literal, Union

import rfc8785
from pydantic import BaseModel, ConfigDict, Field, StrictBool, TypeAdapter, ValidationError

from .errors import BackendError

MAX_SAFE_INTEGER = 9007199254740991


def canonical_json(value):
    # RFC 8785 uses ECMAScript number formatting and UTF-16 key ordering, exactly
    # the existing TypeScript canonicalJson + JSON.stringify contract.
    def js_numbers(item):
        if isinstance(item, int) and not isinstance(item, bool) and abs(item) > MAX_SAFE_INTEGER:
            return float(item)
        if isinstance(item, list):
            return [js_numbers(child) for child in item]
        if isinstance(item, dict):
            return {key: js_numbers(child) for key, child in item.items()}
        return item
    return rfc8785.dumps(js_numbers(value)).decode("utf-8")


def payload_hash(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def strict_object(value, required, optional=()):
    if not isinstance(value, dict) or not set(required).issubset(value) or set(value) - set(required) - set(optional):
        raise BackendError("INVALID_INPUT", 400)
    return value


def require_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise BackendError("INVALID_INPUT", 400)
    return value


def require_number(value, min=0, max=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or (isinstance(value, float) and not math.isfinite(value)) or value < min or (max is not None and value > max):
        raise BackendError("INVALID_INPUT", 400)
    return value


def require_int(value, min=1, max=MAX_SAFE_INTEGER):
    require_number(value, min, max)
    if value != int(value):
        raise BackendError("INVALID_INPUT", 400)
    return int(value)


Id = Annotated[str, Field(strict=True, pattern=r"^[A-Za-z0-9_-]{1,128}$")]
# JSON 1.0 is a number/integer in JavaScript. Preserve that compatibility without
# accepting numeric strings or booleans through Pydantic's normal coercion.
Version = Annotated[int, Field(ge=1, le=MAX_SAFE_INTEGER)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Envelope(StrictModel):
    requestId: Id
    resetEpoch: Version
    source: Literal["today", "agent", "workout", "profile", "app_open"]


class LocaleAction(Envelope):
    kind: Literal["set_locale"]
    locale: Literal["en", "zh-CN"]


class ResetAction(Envelope):
    kind: Literal["reset_demo"]
    scenario: Literal["normal", "low_recovery"]


class ReadinessDemoAction(Envelope):
    kind: Literal["set_readiness_demo"]
    mode: Literal["low", "balanced", "high"]


class WorkoutAction(Envelope):
    workoutId: Id
    expectedWorkoutVersion: Version


class StartAction(WorkoutAction):
    kind: Literal["start_workout"]


class ExerciseAction(WorkoutAction):
    kind: Literal["complete_exercise", "undo_exercise"]
    exerciseId: Id


class FinishAction(WorkoutAction):
    kind: Literal["finish_workout"]
    actualMinutes: Annotated[int, Field(ge=1, le=1440)]
    confirmIncomplete: StrictBool


class ApplyAction(Envelope):
    kind: Literal["apply_proposal"]
    proposalId: Id
    startAfterApply: StrictBool


class DismissAction(Envelope):
    kind: Literal["dismiss_proposal"]
    proposalId: Id


class UndoAction(Envelope):
    kind: Literal["undo_meal"]
    operationId: Id


class CheckAction(Envelope):
    kind: Literal["check_readiness"]
    retry: StrictBool = False


class ProposalAction(Envelope):
    kind: Literal["request_proposal"]
    gymId: Literal["gym-a", "gym-b"] | None = None


Action = Annotated[Union[LocaleAction, ResetAction, ReadinessDemoAction, StartAction, ExerciseAction, FinishAction, ApplyAction, DismissAction, UndoAction, CheckAction, ProposalAction], Field(discriminator="kind")]
action_adapter = TypeAdapter(Action)


def parse_action(value):
    if not isinstance(value, dict):
        raise BackendError("INVALID_INPUT", 400)
    for key in ("resetEpoch", "expectedWorkoutVersion", "actualMinutes"):
        if key in value:
            require_int(value[key], max=1440 if key == "actualMinutes" else MAX_SAFE_INTEGER)
    if "gymId" in value and value["gymId"] is None:
        raise BackendError("INVALID_INPUT", 400)
    try:
        # exclude_unset preserves the original canonical request (e.g. omitted retry).
        return action_adapter.validate_python(value).model_dump(exclude_unset=True)
    except ValidationError:
        raise BackendError("INVALID_INPUT", 400) from None

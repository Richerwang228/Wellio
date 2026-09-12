"""PostgreSQL schema-4 repository with atomic synchronous business mutations."""
import copy
import inspect
import json
import os
import re
import psycopg
from psycopg.rows import dict_row
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import set_json_loads
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4

from .errors import BackendError
from .migrate import migrate, validate_database_url
from .seed import SCHEMA_VERSION, SEED_SOURCE, create_seed
from .validation import canonical_json, payload_hash, require_id, require_int


def now_ms():
    return int(time.time() * 1000)


def iso_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def context_versions(snapshot):
    return {"meal": snapshot["mealRevision"], "plan": snapshot["plan"]["version"], "workout": (snapshot.get("workout") or {}).get("version", 0), "conditions": snapshot["conditions"]["version"], "readiness": snapshot["readiness"]["version"]}


def assert_context_versions(snapshot, expected):
    current = context_versions(snapshot)
    if any(current.get(key) != value for key, value in expected.items()):
        raise BackendError("VERSION_CONFLICT", 409)


class Database:
    def __init__(self, database_url, *, connect_timeout_seconds=3, statement_timeout_ms=5000,
                 sql_lock_timeout_ms=1000, lock_timeout_seconds=2):
        self.database_url = validate_database_url(database_url)
        for value in (connect_timeout_seconds, statement_timeout_ms, sql_lock_timeout_ms):
            if type(value) is not int or value <= 0:
                raise ValueError("INVALID_DATABASE_TIMEOUT")
        if type(lock_timeout_seconds) not in (int, float) or not 0 < lock_timeout_seconds <= 30:
            raise ValueError("INVALID_DATABASE_TIMEOUT")
        self.connect_timeout_seconds = connect_timeout_seconds
        self.statement_timeout_ms = statement_timeout_ms
        self.sql_lock_timeout_ms = sql_lock_timeout_ms
        self.lock_timeout_seconds = lock_timeout_seconds
        self._lock = threading.RLock()
        self._scope_depth = 0
        self._transaction_thread = None
        self._closed = False
        self._agent_tool = None
        self.connection = self._connect()
        try:
            migrate(self.connection)
            with self.transaction():
                self.connection.execute("INSERT INTO server_metadata(key,value) VALUES (%s,%s) ON CONFLICT (key) DO NOTHING", ("session_signing_key_v1", os.urandom(32).hex()))
                raw_key = self.connection.execute("SELECT value FROM server_metadata WHERE key=%s", ("session_signing_key_v1",)).fetchone()["value"]
                if not isinstance(raw_key, str) or not re.fullmatch(r"[0-9a-f]{64}", raw_key):
                    raise ValueError("INVALID_SESSION_SIGNING_KEY")
                self.signing_key = bytes.fromhex(raw_key)
        except BaseException:
            self.connection.close()
            raise

    def _connect(self):
        # Preserve caller options such as an isolated test schema, then enforce
        # server and socket budgets on every new physical connection.
        options = conninfo_to_dict(self.database_url).get("options", "")
        options += (f" -cstatement_timeout={self.statement_timeout_ms}"
                    f" -clock_timeout={self.sql_lock_timeout_ms}"
                    " -cidle_in_transaction_session_timeout=10000")
        connection = psycopg.connect(
            self.database_url, autocommit=True, row_factory=dict_row,
            connect_timeout=self.connect_timeout_seconds, options=options.strip(),
            keepalives=1, keepalives_idle=2, keepalives_interval=1,
            keepalives_count=3, tcp_user_timeout=5000,
        )
        connection.isolation_level = psycopg.IsolationLevel.READ_COMMITTED
        # Keep JSON text decoding local to this repository connection.
        set_json_loads(lambda data: bytes(data).decode("utf-8"), connection)
        return connection

    def _ensure_connection(self):
        if self._closed:
            raise BackendError("DATABASE_UNAVAILABLE", 503)
        if not self.connection.closed and not self.connection.broken:
            return
        self.connection = self._connect()
        try:
            # Reconnecting must not create a new signing identity or migrate a
            # replacement database underneath existing session cookies.
            row = self.connection.execute(
                "SELECT value FROM server_metadata WHERE key=%s", ("session_signing_key_v1",)
            ).fetchone()
            if not row or row["value"] != self.signing_key.hex():
                raise BackendError("DATABASE_NOT_READY", 503)
        except BaseException:
            self.connection.close()
            raise

    @contextmanager
    def _connection_scope(self):
        if not self._lock.acquire(timeout=self.lock_timeout_seconds):
            raise BackendError("DATABASE_BUSY", 503)
        outermost = self._scope_depth == 0
        self._scope_depth += 1
        try:
            if outermost:
                self._ensure_connection()
            yield
        except (psycopg.OperationalError, psycopg.InterfaceError) as error:
            if not outermost:
                raise
            # Never replay a statement or transaction here: COMMIT may have
            # succeeded before its acknowledgement was lost. The caller may
            # retry its existing request ID and recover the durable receipt.
            self.connection.close()
            code = "DATABASE_TIMEOUT" if isinstance(error, (psycopg.errors.QueryCanceled, psycopg.errors.LockNotAvailable)) else "DATABASE_UNAVAILABLE"
            raise BackendError(code, 503) from None
        finally:
            self._scope_depth -= 1
            self._lock.release()

    def health(self):
        with self._connection_scope():
            return self.connection.execute("SELECT 1 AS ok").fetchone()["ok"] == 1

    def close(self):
        with self._lock:
            if not self._closed:
                self.connection.close()
                self._closed = True

    @contextmanager
    def transaction(self):
        with self._connection_scope():
            if self._transaction_thread is not None:
                raise RuntimeError("NESTED_TRANSACTION_NOT_ALLOWED")
            self._transaction_thread = threading.get_ident()
            try:
                with self.connection.transaction():
                    yield
            finally:
                self._transaction_thread = None

    def _lock_session(self, session_id):
        self._require_transaction()
        # Lock before snapshot/receipt reads. Independent workers then observe
        # the preceding committed receipt under PostgreSQL READ COMMITTED.
        self.connection.execute("SELECT id FROM sessions WHERE id=%s FOR UPDATE", (session_id,)).fetchone()

    def _require_transaction(self):
        if self._transaction_thread != threading.get_ident():
            raise RuntimeError("MUTATION_TRANSACTION_REQUIRED")

    def _one(self, sql, parameters=()):
        with self._connection_scope():
            return self.connection.execute(sql, parameters).fetchone()

    def create_session(self, expires_at=None):
        snapshot = create_seed(str(uuid4()))
        with self._connection_scope():
            self.connection.execute("INSERT INTO sessions(id,reset_epoch,revision,schema_version,seed_source,snapshot_json,created_at,expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", (snapshot["sessionId"], snapshot["resetEpoch"], snapshot["revision"], SCHEMA_VERSION, SEED_SOURCE, encode(snapshot), now_ms(), expires_at if expires_at is not None else now_ms() + 30 * 86400000))
        return snapshot

    def get_snapshot(self, session_id):
        row = self._one("SELECT * FROM sessions WHERE id=%s", (session_id,))
        if not row or row["expires_at"] <= now_ms():
            raise BackendError("INVALID_SESSION", 401)
        if row["schema_version"] != SCHEMA_VERSION:
            raise RuntimeError("SNAPSHOT_SCHEMA_UNSUPPORTED")
        snapshot = json.loads(row["snapshot_json"])
        if any(isinstance(snapshot.get(key), bool) or not isinstance(snapshot.get(key), (int, float)) for key in ("schemaVersion", "resetEpoch", "revision")) or (snapshot.get("schemaVersion"), snapshot.get("sessionId"), snapshot.get("resetEpoch"), snapshot.get("revision")) != (row["schema_version"], session_id, row["reset_epoch"], row["revision"]):
            raise RuntimeError("CORRUPT_SESSION_STATE")
        return snapshot

    @staticmethod
    def _reply(row):
        reply = {"httpStatus": row["http_status"], "result": json.loads(row["result_json"])}
        if row["continuation_json"]:
            reply["continuation"] = json.loads(row["continuation_json"])
        return reply

    def get_mutation_reply(self, session_id, request):
        with self._connection_scope():
            current = self.get_snapshot(session_id)
            previous = self._one("SELECT * FROM action_requests WHERE session_id=%s AND request_id=%s", (session_id, request["requestId"]))
            reset_replay = previous and previous["kind"] == "reset_demo" and previous["http_status"] == 200 and previous["result_epoch"] == current["resetEpoch"] and previous["request_epoch"] == request["resetEpoch"]
            if request["resetEpoch"] != current["resetEpoch"] and not reset_replay:
                raise BackendError("STALE_EPOCH", 409)
            if previous:
                if previous["payload_hash"] != payload_hash(request):
                    raise BackendError("IDEMPOTENCY_CONFLICT", 409)
                return self._reply(previous)
            return None

    def _save_snapshot(self, current, request, snapshot):
        if snapshot is None:
            return
        expected_epoch = current["resetEpoch"] + (1 if request["kind"] == "reset_demo" else 0)
        if any(isinstance(snapshot.get(key), bool) or not isinstance(snapshot.get(key), (int, float)) for key in ("schemaVersion", "resetEpoch", "revision")) or (snapshot.get("schemaVersion"), snapshot.get("sessionId"), snapshot.get("resetEpoch"), snapshot.get("revision")) != (SCHEMA_VERSION, current["sessionId"], expected_epoch, current["revision"] + 1):
            raise RuntimeError("INVALID_MUTATION_VERSION")
        updated = self.connection.execute("UPDATE sessions SET reset_epoch=%s,revision=%s,snapshot_json=%s WHERE id=%s AND reset_epoch=%s AND revision=%s", (snapshot["resetEpoch"], snapshot["revision"], encode(snapshot), current["sessionId"], current["resetEpoch"], current["revision"]))
        if updated.rowcount != 1:
            raise BackendError("VERSION_CONFLICT", 409)

    @staticmethod
    def _result(current, request, outcome):
        snapshot = outcome.get("snapshot")
        return {**outcome["result"], "requestId": request["requestId"], "resetEpoch": snapshot["resetEpoch"] if snapshot else current["resetEpoch"], **({"snapshot": snapshot} if snapshot else {})}

    def mutate(self, session_id, request, execute):
        with self._business_transaction():
            self._lock_session(session_id)
            current = self.get_snapshot(session_id)
            previous = self.get_mutation_reply(session_id, request)
            if previous:
                return previous
            reserved = self.find_agent_run(session_id, request["requestId"])
            if reserved and reserved["source"] == "ui_proposal" and canonical_json(reserved.get("actionRequest")) != canonical_json(request):
                raise BackendError("IDEMPOTENCY_CONFLICT", 409)
            outcome = execute(copy.deepcopy(current))
            if inspect.isawaitable(outcome):
                if inspect.iscoroutine(outcome):
                    outcome.close()
                raise RuntimeError("ASYNC_MUTATION_NOT_ALLOWED")
            self._preempt_background_run(current, request, outcome)
            self._bind_agent_mutation(current, request, outcome)
            self._save_snapshot(current, request, outcome.get("snapshot"))
            result = self._result(current, request, outcome)
            continuation = outcome.get("continuation")
            self.connection.execute("INSERT INTO action_requests(session_id,request_id,request_epoch,result_epoch,kind,payload_hash,http_status,result_json,created_at,continuation_json) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", (session_id, request["requestId"], request["resetEpoch"], result["resetEpoch"], request["kind"], payload_hash(request), outcome["httpStatus"], encode(result), now_ms(), encode(continuation) if continuation else None))
            if request["kind"] == "reset_demo" and outcome.get("snapshot"):
                for table in ("context_reads", "write_authorizations", "meal_operations", "meal_entities", "agent_runs", "readiness_checks", "user_inputs"):
                    self.connection.execute(f"DELETE FROM {table} WHERE session_id=%s", (session_id,))
            return {"httpStatus": outcome["httpStatus"], "result": result, **({"continuation": continuation} if continuation else {})}

    def resume_mutation(self, session_id, request, execute):
        with self.transaction():
            self._lock_session(session_id)
            current = self.get_snapshot(session_id)
            if current["resetEpoch"] != request["resetEpoch"]:
                raise BackendError("STALE_EPOCH", 409)
            previous = self.get_mutation_reply(session_id, request)
            if not previous:
                raise BackendError("NOT_FOUND", 404)
            if not previous.get("continuation"):
                return previous
            outcome = execute(copy.deepcopy(current), previous["continuation"], previous["result"])
            if inspect.isawaitable(outcome):
                if inspect.iscoroutine(outcome):
                    outcome.close()
                raise RuntimeError("ASYNC_MUTATION_NOT_ALLOWED")
            self._save_snapshot(current, request, outcome.get("snapshot"))
            result = self._result(current, request, outcome)
            self.connection.execute("UPDATE action_requests SET result_json=%s,http_status=%s,result_epoch=%s,continuation_json=NULL WHERE session_id=%s AND request_id=%s", (encode(result), outcome["httpStatus"], current["resetEpoch"], session_id, request["requestId"]))
            return {"httpStatus": outcome["httpStatus"], "result": result}

    def runtime_transaction(self, session_id, execute):
        with self.transaction():
            self._lock_session(session_id)
            current = self.get_snapshot(session_id)
            snapshot = copy.deepcopy(current)
            result = execute(snapshot)
            if inspect.isawaitable(result):
                if inspect.iscoroutine(result):
                    result.close()
                raise RuntimeError("ASYNC_TRANSACTION_NOT_ALLOWED")
            if result["changed"]:
                snapshot["revision"] = current["revision"] + 1
                self._save_snapshot(current, {"kind": "runtime_state"}, snapshot)
            return result["value"]

    def capture_context(self, session_id, request):
        require_id(request["runId"]); require_id(request["requestId"]); require_int(request["resetEpoch"])
        with self.transaction():
            self._lock_session(session_id)
            snapshot = self.get_snapshot(session_id)
            if snapshot["resetEpoch"] != request["resetEpoch"]:
                raise BackendError("STALE_EPOCH", 409)
            row = self._one("SELECT record_json FROM context_reads WHERE session_id=%s AND run_id=%s AND request_id=%s AND reset_epoch=%s", (session_id, request["runId"], request["requestId"], request["resetEpoch"]))
            if row:
                return json.loads(row["record_json"])
            record = {"id": str(uuid4()), "sessionId": session_id, "runId": request["runId"], "requestId": request["requestId"], "resetEpoch": snapshot["resetEpoch"], "dayKey": snapshot["dayKey"], "versions": context_versions(snapshot), "readinessSnapshotId": snapshot["readiness"]["id"], "createdAt": iso_now(), "snapshot": snapshot}
            self.connection.execute("INSERT INTO context_reads(id,session_id,run_id,request_id,reset_epoch,record_json) VALUES (%s,%s,%s,%s,%s,%s)", (record["id"], session_id, record["runId"], record["requestId"], record["resetEpoch"], encode(record)))
            return record

    def get_context_read(self, session_id, context_id, run_id, epoch):
        with self._connection_scope():
            current = self.get_snapshot(session_id)
            if current["resetEpoch"] != epoch:
                raise BackendError("STALE_EPOCH", 409)
            row = self._one("SELECT record_json FROM context_reads WHERE id=%s AND session_id=%s AND run_id=%s AND reset_epoch=%s", (context_id, session_id, run_id, epoch))
            if not row:
                raise BackendError("CONTEXT_READ_INVALID", 409)
            record = json.loads(row["record_json"])
            if record["dayKey"] != current["dayKey"] or record["readinessSnapshotId"] != current["readiness"]["id"]:
                raise BackendError("CONTEXT_STALE", 409)
            assert_context_versions(current, record["versions"])
            return record

    def get_agent_run(self, session_id, run_id):
        row = self._one("SELECT record_json FROM agent_runs WHERE session_id=%s AND id=%s", (session_id, run_id))
        return json.loads(row["record_json"]) if row else None

    def with_agent_tool(self, context, execute):
        """Bind one synchronous domain mutation to its run under the same lock."""
        with self.transaction():
            self._lock_session(context['sessionId'])
            previous, self._agent_tool = self._agent_tool, context
            try:
                self._assert_agent_mutation(self.get_snapshot(context['sessionId']))
                result = execute()
                if inspect.isawaitable(result):
                    if inspect.iscoroutine(result):
                        result.close()
                    raise RuntimeError("ASYNC_MUTATION_NOT_ALLOWED")
                self._assert_agent_mutation(self.get_snapshot(context['sessionId']))
                return result
            finally:
                self._agent_tool = previous

    @contextmanager
    def _business_transaction(self):
        # Only the controlled grant + mutation composition may reuse its outer
        # transaction. Arbitrary nested runtime transactions remain forbidden.
        with self._connection_scope():
            if self._agent_tool is not None:
                self._require_transaction()
                yield
            else:
                with self.transaction():
                    yield

    def _assert_agent_mutation(self, current):
        context = self._agent_tool
        run = self.get_agent_run(current['sessionId'], context['runId'])
        if not run or run['status'] != 'pending' or run['resetEpoch'] != current['resetEpoch'] or run['leaseExpiresAt'] <= context['now']():
            raise BackendError('RUN_NOT_ACTIVE', 409)
        return run

    def _preempt_background_run(self, current, request, outcome):
        if self._agent_tool is not None or outcome['result']['status'] != 'succeeded' or request['kind'] not in ('start_workout', 'complete_exercise', 'undo_exercise', 'finish_workout', 'apply_proposal', 'dismiss_proposal', 'undo_meal'):
            return
        from .runtime import _stop_record
        active = [run for run in self.list_agent_runs(current['sessionId']) if run['source'] == 'app_open' and run['status'] == 'pending' and run['resetEpoch'] == current['resetEpoch']]
        if not active:
            return
        snapshot = outcome.get('snapshot')
        if snapshot is None:
            snapshot = copy.deepcopy(current)
            snapshot['revision'] += 1
            outcome['snapshot'] = snapshot
        for run in active:
            _stop_record(self, snapshot, run, 'stopped', 'USER_PRIORITY')

    def _bind_agent_mutation(self, current, request, outcome):
        context = self._agent_tool
        if context is None:
            return
        run = self._assert_agent_mutation(current)
        result = outcome["result"]
        if result['status'] == 'succeeded':
            run.pop('lastContextReadId', None)
            run.pop('lastVersions', None)
        if request["kind"] in ("undo_meal", "start_workout", "complete_exercise", "undo_exercise", "finish_workout") and result["status"] == "succeeded":
            if run.get("intentConsumedBy") and run["intentConsumedBy"] != context["toolCallId"]:
                raise BackendError("AUTHORIZATION_CONSUMED", 409)
            run["intentConsumedBy"] = context["toolCallId"]
        snapshot = outcome.get("snapshot")
        if snapshot is None:
            snapshot = copy.deepcopy(current)
            snapshot["revision"] += 1
        message = next((item for item in snapshot["messages"] if item["id"] == run["messageId"]), None)
        if message is None:
            raise BackendError("RUN_NOT_ACTIVE", 409)
        step = next((item for item in message["steps"] if item.get("toolCallId") == context["toolCallId"]), None)
        if step is not None:
            step["status"] = "succeeded" if result["status"] == "succeeded" else "awaiting_user" if result["status"] == "needs_input" else "failed"
            if result.get("errorCode"):
                step["errorCode"] = result["errorCode"]
        if result["status"] == "succeeded" and result.get("operationId") and request["kind"] in ("mutate_meal_log", "undo_meal"):
            operation = self.get_meal_operation(current["sessionId"], current["resetEpoch"], result["operationId"])
            if operation:
                message.update(operationId=operation["id"], mealId=operation["mealId"])
        if result["status"] == "succeeded" and result.get("proposalId"):
            proposal = next((item for item in snapshot["proposals"] if item["id"] == result["proposalId"]), None)
            if proposal:
                message["proposalId"] = proposal["id"]
                proposal["messageId"] = message["id"]
                run["proposalId"] = proposal["id"]
                if run.get("checkKey") and run.get("checkAttemptId"):
                    proposal.update(checkKey=run["checkKey"], checkAttemptId=run["checkAttemptId"])
                    check = self.get_readiness_check(current["sessionId"], run["checkKey"])
                    if check and check.get("attemptId") == run["checkAttemptId"]:
                        check["proposalId"] = proposal["id"]
                        self.save_readiness_check(check)
                        if snapshot.get("readinessCheck", {}).get("key") == check["key"]:
                            snapshot["readinessCheck"]["proposalId"] = proposal["id"]
        self.save_agent_run(run)
        outcome["snapshot"] = snapshot

    def store_agent_action_reply(self, snapshot, request, result):
        """Save the original UI Action receipt inside the final run transaction."""
        self._require_transaction()
        previous = self.get_mutation_reply(snapshot["sessionId"], request)
        if previous:
            return previous
        reply = {**result, "requestId": request["requestId"], "resetEpoch": snapshot["resetEpoch"], "snapshot": snapshot}
        self.connection.execute("INSERT INTO action_requests(session_id,request_id,request_epoch,result_epoch,kind,payload_hash,http_status,result_json,created_at,continuation_json) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL)",
                                (snapshot["sessionId"], request["requestId"], request["resetEpoch"], snapshot["resetEpoch"], request["kind"], payload_hash(request), 200, encode(reply), now_ms()))
        return {"httpStatus": 200, "result": reply}

    def find_agent_run(self, session_id, request_id):
        row = self._one("SELECT record_json FROM agent_runs WHERE session_id=%s AND request_id=%s", (session_id, request_id))
        return json.loads(row["record_json"]) if row else None

    def list_agent_runs(self, session_id):
        with self._connection_scope():
            return [json.loads(row["record_json"]) for row in self.connection.execute("SELECT record_json FROM agent_runs WHERE session_id=%s", (session_id,))]

    def save_agent_run(self, run):
        self._require_transaction()
        self.connection.execute("INSERT INTO agent_runs(id,session_id,request_id,reset_epoch,record_json) VALUES (%s,%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET record_json=excluded.record_json", (run["id"], run["sessionId"], run["requestId"], run["resetEpoch"], encode(run)))

    def get_readiness_check(self, session_id, key):
        row = self._one("SELECT record_json FROM readiness_checks WHERE session_id=%s AND check_key=%s", (session_id, key))
        return json.loads(row["record_json"]) if row else None

    def save_readiness_check(self, check):
        self._require_transaction()
        self.connection.execute("INSERT INTO readiness_checks(check_key,session_id,reset_epoch,record_json) VALUES (%s,%s,%s,%s) ON CONFLICT(check_key) DO UPDATE SET record_json=excluded.record_json", (check["key"], check["sessionId"], check["resetEpoch"], encode(check)))

    def consume_readiness_check(self, snapshot, proposal, status):
        self._require_transaction()
        if not proposal.get("checkKey") or not proposal.get("checkAttemptId"):
            return
        check = self.get_readiness_check(snapshot["sessionId"], proposal["checkKey"])
        if not check or check["resetEpoch"] != snapshot["resetEpoch"] or check.get("attemptId") != proposal["checkAttemptId"] or check.get("proposalId") != proposal["id"]:
            return
        check["status"] = status
        self.save_readiness_check(check)
        if (snapshot.get("readinessCheck") or {}).get("key") == check["key"]:
            snapshot["readinessCheck"]["status"] = status

    def store_user_input(self, record):
        self._require_transaction()
        self.connection.execute("INSERT INTO user_inputs(id,session_id,request_id,reset_epoch,record_json) VALUES (%s,%s,%s,%s,%s)", (record["id"], record["sessionId"], record["requestId"], record["resetEpoch"], encode(record)))

    def get_user_input(self, session_id, epoch, source_id):
        row = self._one("SELECT record_json FROM user_inputs WHERE id=%s AND session_id=%s AND reset_epoch=%s", (source_id, session_id, epoch))
        return json.loads(row["record_json"]) if row else None

    def list_user_inputs(self, session_id, epoch):
        with self._connection_scope():
            return [json.loads(row["record_json"]) for row in self.connection.execute("SELECT record_json FROM user_inputs WHERE session_id=%s AND reset_epoch=%s ORDER BY sequence DESC LIMIT 100", (session_id, epoch))]

    @staticmethod
    def _assert_source_versions(source):
        try:
            versions = source["versions"]
            require_int(versions["meal"]); require_int(versions["conditions"])
        except (KeyError, TypeError, BackendError):
            raise BackendError("AUTHORIZATION_INVALID", 403) from None

    def _assert_authorization_source(self, record, source):
        self._assert_source_versions(source)
        if any(record.get(key) != source.get(other) for key, other in (("sourceMessageId", "id"), ("sessionId", "sessionId"), ("resetEpoch", "resetEpoch"))):
            raise BackendError("AUTHORIZATION_INVALID", 403)
        if (record.get("expectedConditionsVersion") != source["versions"]["conditions"] if record["scope"] == "conditions_update" else record.get("expectedMealRevision") != source["versions"]["meal"]):
            raise BackendError("AUTHORIZATION_INVALID", 403)

    def issue_authorization(self, session_id, request, derive):
        with self._business_transaction():
            self._lock_session(session_id)
            snapshot = self.get_snapshot(session_id)
            if snapshot["resetEpoch"] != request["resetEpoch"]:
                raise BackendError("STALE_EPOCH", 409)
            source = self.get_user_input(session_id, request["resetEpoch"], request["sourceMessageId"])
            if not source or source["conversationId"] != snapshot["conversationId"]:
                raise BackendError("AUTHORIZATION_INVALID", 403)
            self._assert_source_versions(source)
            row = self._one("SELECT record_json FROM write_authorizations WHERE session_id=%s AND reset_epoch=%s AND source_message_id=%s", (session_id, request["resetEpoch"], source["id"]))
            if row:
                record = json.loads(row["record_json"])
                self._assert_authorization_source(record, source)
                if record["runId"] != request["runId"]:
                    raise BackendError("AUTHORIZATION_RUN_MISMATCH", 403)
                return record
            constraint = derive(snapshot, source)
            conditions = constraint["scope"] == "conditions_update"
            if (source["versions"]["conditions"] != snapshot["conditions"]["version"] if conditions else source["versions"]["meal"] != snapshot["mealRevision"]):
                raise BackendError("VERSION_CONFLICT", 409)
            record = {"id": str(uuid4()), "sessionId": session_id, "sourceMessageId": source["id"], "resetEpoch": request["resetEpoch"], "runId": request["runId"], "scope": constraint["scope"], "constraint": constraint, "createdAt": iso_now()}
            if conditions:
                record["expectedConditionsVersion"] = snapshot["conditions"]["version"]
            else:
                record["expectedMealRevision"] = snapshot["mealRevision"]
                if constraint["scope"] != "meal_add":
                    meal = next((m for m in snapshot["meals"] if m["id"] == constraint["mealId"]), None)
                    if not meal:
                        raise BackendError("NOT_FOUND", 404)
                    record["expectedMealVersion"] = meal["version"]
            self.connection.execute("INSERT INTO write_authorizations(id,session_id,reset_epoch,source_message_id,run_id,record_json) VALUES (%s,%s,%s,%s,%s,%s)", (record["id"], session_id, record["resetEpoch"], source["id"], record["runId"], encode(record)))
            return record

    def assert_authorization(self, session_id, request, constraint):
        self._require_transaction()
        row = self._one("SELECT a.record_json,a.consumed_by_request_id,u.record_json AS source_json FROM write_authorizations a JOIN user_inputs u ON u.id=a.source_message_id AND u.session_id=a.session_id AND u.reset_epoch=a.reset_epoch WHERE a.id=%s AND a.session_id=%s AND a.reset_epoch=%s AND a.run_id=%s", (request["authorizationId"], session_id, request["resetEpoch"], request["runId"]))
        if not row:
            raise BackendError("AUTHORIZATION_INVALID", 403)
        if row["consumed_by_request_id"] is not None:
            raise BackendError("AUTHORIZATION_CONSUMED", 409)
        record = json.loads(row["record_json"])
        self._assert_authorization_source(record, json.loads(row["source_json"]))
        if canonical_json(record["constraint"]) != canonical_json(constraint) or any(record.get(key) != request.get(key) for key in ("expectedMealRevision", "expectedMealVersion", "expectedConditionsVersion")):
            raise BackendError("AUTHORIZATION_MISMATCH", 403)
        return record

    def consume_authorization(self, session_id, authorization_id, request_id):
        self._require_transaction()
        changed = self.connection.execute("UPDATE write_authorizations SET consumed_by_request_id=%s WHERE id=%s AND session_id=%s AND consumed_by_request_id IS NULL", (request_id, authorization_id, session_id))
        if changed.rowcount != 1:
            raise BackendError("AUTHORIZATION_CONSUMED", 409)

    def get_meal_entity(self, session_id, epoch, meal_id):
        self._require_transaction()
        row = self._one("SELECT version,head_operation_id FROM meal_entities WHERE session_id=%s AND reset_epoch=%s AND meal_id=%s", (session_id, epoch, meal_id))
        return {"mealId": meal_id, "version": row["version"], "headOperationId": row["head_operation_id"]} if row else None

    def save_meal_entity(self, session_id, epoch, entity):
        self._require_transaction()
        self.connection.execute("INSERT INTO meal_entities(session_id,reset_epoch,meal_id,version,head_operation_id) VALUES (%s,%s,%s,%s,%s) ON CONFLICT(session_id,reset_epoch,meal_id) DO UPDATE SET version=excluded.version,head_operation_id=excluded.head_operation_id", (session_id, epoch, entity["mealId"], entity["version"], entity.get("headOperationId")))

    def get_meal_operation(self, session_id, epoch, operation_id):
        self._require_transaction()
        row = self._one("SELECT record_json FROM meal_operations WHERE id=%s AND session_id=%s AND reset_epoch=%s", (operation_id, session_id, epoch))
        return json.loads(row["record_json"]) if row else None

    def store_meal_operation(self, record):
        self._require_transaction()
        self.connection.execute("INSERT INTO meal_operations(id,session_id,reset_epoch,meal_id,record_json) VALUES (%s,%s,%s,%s,%s)", (record["id"], record["sessionId"], record["resetEpoch"], record["mealId"], encode(record)))

    def mark_meal_operation_undone(self, record, request_id):
        self._require_transaction()
        updated = {**record, "status": "undone", "undoneByRequestId": request_id}
        changed = self.connection.execute("UPDATE meal_operations SET record_json=%s WHERE id=%s AND session_id=%s AND reset_epoch=%s", (encode(updated), record["id"], record["sessionId"], record["resetEpoch"]))
        if changed.rowcount != 1:
            raise BackendError("UNDO_CONFLICT", 409)


WellioDatabase = Database

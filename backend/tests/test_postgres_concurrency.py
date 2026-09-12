"""Database row locks must coordinate independent server processes, not just threads."""
from contextlib import closing
import multiprocessing

from wellio.actions import execute_action
from wellio.database import Database


def _apply_shared_request(database_url, session_id, request, ready, replies):
    try:
        with closing(Database(database_url)) as database:
            ready.wait(timeout=15)
            replies.put({"reply": execute_action(database, session_id, request)})
    except BaseException as error:
        replies.put({"error": f"{type(error).__name__}: {error}"})


def test_same_request_from_four_server_processes_commits_one_receipt_and_revision(database, database_url):
    initial = database.create_session()
    session_id = initial["sessionId"]
    request = {"kind": "set_locale", "requestId": "parallel-process-request", "resetEpoch": initial["resetEpoch"], "source": "profile", "locale": "zh-CN"}
    # Spawn prevents children from inheriting the parent's connection or RLock.
    context = multiprocessing.get_context("spawn")
    ready = context.Barrier(4)
    replies = context.Queue()
    workers = [context.Process(target=_apply_shared_request, args=(database_url, session_id, request, ready, replies)) for _ in range(4)]
    try:
        for worker in workers:
            worker.start()
        results = [replies.get(timeout=25) for _ in workers]
        for worker in workers:
            worker.join(timeout=10)
            assert worker.exitcode == 0
        assert all("error" not in result for result in results), results
        first = results[0]["reply"]
        assert all(result["reply"] == first for result in results)
        assert first["httpStatus"] == 200 and first["result"]["status"] == "succeeded"
        persisted = database.get_snapshot(session_id)
        assert persisted == first["result"]["snapshot"]
        assert persisted["locale"] == "zh-CN"
        assert persisted["revision"] == initial["revision"] + 1
        count = database.connection.execute("SELECT count(*) AS count FROM action_requests WHERE session_id=%s AND request_id=%s", (session_id, request["requestId"])).fetchone()["count"]
        assert count == 1
        with closing(Database(database_url)) as reopened:
            assert execute_action(reopened, session_id, request) == first
            assert reopened.get_snapshot(session_id) == persisted
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
            if worker.pid is not None:
                worker.join(timeout=5)
        replies.close()
        replies.join_thread()

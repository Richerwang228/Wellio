from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import psycopg

import pytest


def get_state(client):
    response = client.get("/api/state")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def action(snapshot, kind="set_locale", request_id="action-1", **fields):
    return {"kind": kind, "requestId": request_id, "resetEpoch": snapshot["resetEpoch"], "source": "profile", **fields}


def test_two_cookies_isolate_state_and_locale(client_factory, database_url):
    first, second = client_factory(database_url), client_factory(database_url)
    a, b = get_state(first), get_state(second)
    assert a["sessionId"] != b["sessionId"]
    assert a["conversationId"] != b["conversationId"]
    assert first.cookies.get("wellio_session") != second.cookies.get("wellio_session")
    saved = first.post("/api/actions", json=action(a, locale="zh-CN"))
    assert saved.status_code == 200
    assert saved.json()["snapshot"]["locale"] == "zh-CN"
    assert get_state(second) == b
    assert get_state(first)["revision"] == a["revision"] + 1


def test_cookie_security_and_reopen_preserve_same_session(database_url):
    from fastapi.testclient import TestClient
    from wellio.app import create_app

    with TestClient(create_app(database_url=database_url), base_url="http://testserver") as first:
        response = first.get("/api/state")
        state = response.json()
        cookie = response.headers["set-cookie"]
        assert "httponly" in cookie.lower()
        assert "samesite=lax" in cookie.lower()
        assert "path=/" in cookie.lower()
        assert "secure" not in cookie.lower()
        token = first.cookies.get("wellio_session")
        request = action(state, locale="zh-CN")
        saved = first.post("/api/actions", json=request).json()
    with TestClient(create_app(database_url=database_url), base_url="http://testserver") as reopened:
        reopened.cookies.set("wellio_session", token)
        assert get_state(reopened) == saved["snapshot"]
        assert reopened.post("/api/actions", json=request).json() == saved


def test_reset_preserves_language_changes_conversation_and_has_bounded_old_epoch_replay(client):
    initial = get_state(client)
    locale_request = action(initial, locale="zh-CN")
    assert client.post("/api/actions", json=locale_request).status_code == 200
    before = get_state(client)
    reset_request = action(before, "reset_demo", "reset-low", scenario="low_recovery")
    reset = client.post("/api/actions", json=reset_request)
    assert reset.status_code == 200
    saved = reset.json()["snapshot"]
    assert saved["resetEpoch"] == before["resetEpoch"] + 1
    assert saved["revision"] == before["revision"] + 1
    assert saved["conversationId"] != before["conversationId"]
    assert saved["locale"] == "zh-CN"
    assert saved["readiness"]["score"] == 42
    assert saved["sleep"]["minutes"] == 240
    assert saved["workout"]["status"] == "planned"
    assert client.post("/api/actions", json=reset_request).json() == reset.json()
    stale = client.post("/api/actions", json=locale_request)
    assert stale.status_code == 409
    assert stale.json()["errorCode"] == "STALE_EPOCH"
    second = action(saved, "reset_demo", "reset-again", scenario="normal")
    assert client.post("/api/actions", json=second).status_code == 200
    stale_reset = client.post("/api/actions", json=reset_request)
    assert stale_reset.status_code == 409
    assert stale_reset.json()["errorCode"] == "STALE_EPOCH"


def test_idempotency_ignores_json_key_order_but_rejects_changed_payload(client, database_url):
    initial = get_state(client)
    request = action(initial, locale="zh-CN")
    first = client.post("/api/actions", json=request)
    assert first.status_code == 200
    assert client.post("/api/actions", json=dict(reversed(list(request.items())))).json() == first.json()
    conflict = client.post("/api/actions", json={**request, "locale": "en"})
    assert conflict.status_code == 409
    assert conflict.json()["errorCode"] == "IDEMPOTENCY_CONFLICT"
    assert get_state(client) == first.json()["snapshot"]
    with psycopg.connect(database_url) as connection:
        assert connection.execute("SELECT COUNT(*) FROM action_requests WHERE request_id = %s", (request["requestId"],)).fetchone()[0] == 1


def test_concurrent_same_request_has_one_receipt_and_one_revision(client_factory, database_url):
    first, second = client_factory(database_url), client_factory(database_url)
    initial = get_state(first)
    second.cookies.set("wellio_session", first.cookies.get("wellio_session"))
    request = action(initial, locale="zh-CN")
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda client: client.post("/api/actions", json=request), [first, second]))
    assert [response.status_code for response in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    assert get_state(first)["revision"] == initial["revision"] + 1


@pytest.mark.parametrize("patch", [
    {"kind": "unknown_action"}, {"role": "admin"}, {"sessionId": "somebody-else"},
    {"snapshot": {"locale": "zh-CN"}}, {"source": "user"}, {"resetEpoch": True},
    {"resetEpoch": "1"}, {"resetEpoch": 10 ** 400}, {"locale": "fr"}, {"requestId": "../request"},
])
def test_strict_invalid_actions_never_mutate(client, patch):
    before = get_state(client)
    response = client.post("/api/actions", json={**action(before, locale="zh-CN"), **patch})
    assert response.status_code == 400
    assert response.json()["errorCode"] == "INVALID_INPUT"
    assert get_state(client) == before


@pytest.mark.parametrize("headers", [
    {"origin": "https://attacker.invalid"}, {"sec-fetch-site": "cross-site"},
    {"referer": "https://attacker.invalid/page"},
])
def test_cross_origin_requests_cannot_read_or_mutate(client, headers):
    before = get_state(client)
    for response in [client.get("/api/state", headers=headers), client.post("/api/actions", headers=headers, json=action(before, locale="zh-CN"))]:
        assert response.status_code == 403
        assert response.json()["errorCode"] == "ORIGIN_NOT_ALLOWED"
    assert get_state(client) == before


def test_transport_method_media_type_size_and_missing_session(client):
    missing = client.post("/api/actions", json={})
    assert missing.status_code == 401
    assert missing.json()["errorCode"] == "SESSION_REQUIRED"
    before = get_state(client)
    wrong_method = client.post("/api/state")
    assert wrong_method.status_code == 405
    assert wrong_method.headers["allow"] == "GET"
    wrong_type = client.post("/api/actions", content="{}", headers={"content-type": "text/plain"})
    assert wrong_type.status_code == 415
    invalid_json = client.post("/api/actions", content="{", headers={"content-type": "application/json"})
    assert invalid_json.status_code == 400
    large = client.post("/api/actions", content=" " * (16 * 1024 + 1), headers={"content-type": "application/json"})
    assert large.status_code == 413
    for response in [missing, wrong_method, wrong_type, invalid_json, large]:
        assert response.headers["cache-control"] == "no-store"
    assert get_state(client) == before


@pytest.mark.parametrize("invalidity", ["signature", "expired_row", "deleted_row"])
def test_invalid_cookie_get_clears_cookie_post_does_not_create_session(client, database_url, invalidity):
    state = get_state(client)
    valid_cookie = client.cookies.get("wellio_session")
    invalid_cookie = valid_cookie[:-1] + ("A" if valid_cookie[-1] != "A" else "B") if invalidity == "signature" else valid_cookie
    if invalidity != "signature":
        with psycopg.connect(database_url) as connection:
            if invalidity == "expired_row":
                connection.execute("UPDATE sessions SET expires_at = 0 WHERE id = %s", (state["sessionId"],))
            else:
                connection.execute("DELETE FROM sessions WHERE id = %s", (state["sessionId"],))
    headers = {"cookie": f"wellio_session={invalid_cookie}"}
    rejected = client.post("/api/actions", headers=headers, json=action(state, locale="zh-CN"))
    assert rejected.status_code == 401
    assert rejected.json()["errorCode"] == "INVALID_SESSION"
    assert "set-cookie" not in rejected.headers
    cleared = client.get("/api/state", headers=headers)
    assert cleared.status_code == 401
    assert cleared.json()["errorCode"] == "INVALID_SESSION"
    assert "max-age=0" in cleared.headers["set-cookie"].lower()
    client.cookies.clear()
    assert get_state(client)["sessionId"] != state["sessionId"]


def test_missing_agent_is_honestly_unavailable(client):
    before = get_state(client)
    assert before["capabilities"]["agent"] is False
    response = client.post("/api/chat", json={})
    assert response.status_code == 503
    assert response.json()["errorCode"] == "PROVIDER_NOT_CONFIGURED"
    assert get_state(client) == before


def test_application_factory_requires_explicit_postgresql_configuration():
    from wellio.main import application

    with pytest.raises(RuntimeError, match="DATABASE_URL_REQUIRED"):
        application()


def test_real_png_upload_is_private_persistent_and_reset_scoped(client_factory, database_url, png_bytes):
    first, second = client_factory(database_url), client_factory(database_url)
    initial = get_state(first)
    get_state(second)
    response = first.post("/api/attachments", files={"file": ("meal.png", png_bytes, "image/png")}, data={"purpose": "food"})
    assert response.status_code == 200, response.text
    attachment = response.json()
    assert attachment["mediaType"] == "image/png"
    assert attachment["purpose"] == "food"
    saved = first.get(attachment["url"])
    assert saved.status_code == 200
    assert saved.content == png_bytes
    assert "no-store" in saved.headers["cache-control"]
    assert second.get(attachment["url"]).status_code == 404
    assert get_state(first)["meals"] == initial["meals"]
    reopened = client_factory(database_url)
    reopened.cookies.set("wellio_session", first.cookies.get("wellio_session"))
    assert reopened.get(attachment["url"]).content == png_bytes
    reset_request = action(initial, "reset_demo", "reset-upload", scenario="normal")
    assert first.post("/api/actions", json=reset_request).status_code == 200
    assert first.get(attachment["url"]).status_code == 404
    new = first.post("/api/attachments", files={"file": ("menu.png", png_bytes, "image/png")}, data={"purpose": "menu"}).json()
    assert first.post("/api/actions", json=reset_request).status_code == 200
    assert first.get(new["url"]).content == png_bytes


@pytest.mark.parametrize("content,content_type,code", [(b"not an image", "image/png", 415), (b"\x89PNG\r\n\x1a\n", "image/png", 400), (b"", "image/png", 400)])
def test_invalid_attachment_bytes_are_not_saved(client, content, content_type, code):
    before = get_state(client)
    response = client.post("/api/attachments", files={"file": ("image.png", content, content_type)}, data={"purpose": "food"})
    assert response.status_code == code
    assert get_state(client) == before

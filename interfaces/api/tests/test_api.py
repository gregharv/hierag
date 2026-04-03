from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def auth_headers(user_id: str, ip: str | None = None, **extra: str) -> dict[str, str]:
    headers = {"x-user-id": user_id}
    if ip:
        headers["x-forwarded-for"] = ip
    headers.update(extra)
    return headers


def seed_admin_interaction(
    *,
    login_code: str,
    question: str,
    answer: str,
    query_effective: str | None = None,
    query_rewritten: str | None = None,
    feedback_ratings: list[tuple[int, str]] | None = None,
    sources: list[dict] | None = None,
) -> dict[str, int]:
    import json
    import core.service as service

    user_id = service.get_or_create_user_by_login(login_code, "127.0.0.1")
    chat_id = service.create_chat(user_id=user_id, title=f"{login_code} chat")
    user_message_id = service.insert_message(
        chat_id=chat_id,
        role="user",
        content=question,
        question_norm=service.normalize_question(question),
        app_version="0.1.10-test",
    )
    assistant_message_id = service.insert_message(
        chat_id=chat_id,
        role="assistant",
        content=answer,
        app_version="0.1.10-test",
    )
    debug_payload = {
        "query": question,
        "query_effective": query_effective or question,
        "query_rewritten": query_rewritten,
        "query_rewrite": {
            "used": bool(query_rewritten),
            "reason": "used" if query_rewritten else "no_history",
            "model": "gpt-5-nano",
            "history_turns": 0,
        },
        "sources": sources or [],
        "llm_request": {"system_text": "system", "user_text": question},
        "llm_response_text": answer,
    }
    service.update_message(
        assistant_message_id,
        debug_json=json.dumps(debug_payload, ensure_ascii=True),
        sources_json=json.dumps(sources or [], ensure_ascii=True),
    )
    for rating, note in feedback_ratings or []:
        service.insert_feedback(assistant_message_id, user_id, rating, note)
    return {
        "user_id": user_id,
        "chat_id": chat_id,
        "user_message_id": user_message_id,
        "assistant_message_id": assistant_message_id,
    }


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "test_app.db"
    scraper_db_path = tmp_path / "test_scraper.db"
    monkeypatch.setenv("HIERAG_APP_DB_PATH", str(db_path))
    monkeypatch.setenv("HIERAG_SCRAPER_DB_PATH", str(scraper_db_path))
    monkeypatch.setenv("HIERAG_URL_CLEANUP_ON_STARTUP", "0")
    monkeypatch.setenv("HIERAG_RENDER_DOCS_ON_STARTUP", "0")

    import interfaces.api.main as main
    import core.service as service

    importlib.reload(service)
    importlib.reload(main)
    service.create_db_and_tables()

    with TestClient(main.app) as test_client:
        yield test_client


def test_startup_cleanup_executes_and_refreshes_cache(tmp_path, monkeypatch):
    db_path = tmp_path / "startup_app.db"
    scraper_db_path = tmp_path / "startup_scraper.db"
    monkeypatch.setenv("HIERAG_APP_DB_PATH", str(db_path))
    monkeypatch.setenv("HIERAG_SCRAPER_DB_PATH", str(scraper_db_path))
    monkeypatch.setenv("HIERAG_URL_CLEANUP_ON_STARTUP", "1")
    monkeypatch.setenv("HIERAG_URL_CLEANUP_SITE_ID", "2")
    monkeypatch.setenv("HIERAG_URL_CLEANUP_DROP_NON_TARGET", "0")
    monkeypatch.setenv("HIERAG_RENDER_DOCS_ON_STARTUP", "0")

    import interfaces.api.main as main
    import core.service as service

    importlib.reload(service)
    importlib.reload(main)

    calls = {"plan": 0, "apply": 0, "refresh": 0}

    def fake_plan(db, site_id=None, drop_non_target=False):
        calls["plan"] += 1
        assert site_id == 2
        assert drop_non_target is False
        return [object()], {"planned_deletes": 1, "planned_updates": 0}

    def fake_apply(db, actions):
        calls["apply"] += 1
        assert len(actions) == 1
        return {"deleted": 1, "updated": 0, "skipped": 0}

    class FakeLLM:
        def refresh_retrieval_cache(self):
            calls["refresh"] += 1

    monkeypatch.setattr(main, "plan_pages_cleanup", fake_plan)
    monkeypatch.setattr(main, "apply_pages_actions", fake_apply)
    monkeypatch.setattr(main, "_load_llmapi", lambda: FakeLLM())

    with TestClient(main.app) as test_client:
        response = test_client.get("/api/release")
        assert response.status_code == 200

    assert calls == {"plan": 1, "apply": 1, "refresh": 1}


def test_startup_cleanup_fail_open_when_cleanup_raises(tmp_path, monkeypatch):
    db_path = tmp_path / "startup_fail_app.db"
    scraper_db_path = tmp_path / "startup_fail_scraper.db"
    monkeypatch.setenv("HIERAG_APP_DB_PATH", str(db_path))
    monkeypatch.setenv("HIERAG_SCRAPER_DB_PATH", str(scraper_db_path))
    monkeypatch.setenv("HIERAG_URL_CLEANUP_ON_STARTUP", "1")
    monkeypatch.setenv("HIERAG_RENDER_DOCS_ON_STARTUP", "0")

    import interfaces.api.main as main
    import core.service as service

    importlib.reload(service)
    importlib.reload(main)

    def fake_plan(*args, **kwargs):
        raise RuntimeError("cleanup failed")

    monkeypatch.setattr(main, "plan_pages_cleanup", fake_plan)

    with TestClient(main.app) as test_client:
        response = test_client.get("/api/release")
        assert response.status_code == 200


def test_startup_docs_render_runs_when_enabled(tmp_path, monkeypatch):
    db_path = tmp_path / "startup_docs_app.db"
    scraper_db_path = tmp_path / "startup_docs_scraper.db"
    monkeypatch.setenv("HIERAG_APP_DB_PATH", str(db_path))
    monkeypatch.setenv("HIERAG_SCRAPER_DB_PATH", str(scraper_db_path))
    monkeypatch.setenv("HIERAG_URL_CLEANUP_ON_STARTUP", "0")
    monkeypatch.setenv("HIERAG_RENDER_DOCS_ON_STARTUP", "1")

    import interfaces.api.main as main
    import core.service as service

    importlib.reload(service)
    importlib.reload(main)

    calls = {"docs_render": 0}

    def fake_render_docs():
        calls["docs_render"] += 1
        return True

    monkeypatch.setattr(main, "_render_docs_site_quarto", fake_render_docs)

    with TestClient(main.app) as test_client:
        response = test_client.get("/api/release")
        assert response.status_code == 200

    assert calls["docs_render"] == 1


def test_profile_endpoint(client: TestClient):
    response = client.get("/api/profile", headers=auth_headers("1234AB", "10.1.2.3"))
    assert response.status_code == 200
    payload = response.json()
    assert payload["user_id"] == "1234AB"
    assert "ip" not in payload
    assert "avatar" in payload


def test_profile_endpoint_accepts_five_character_login(client: TestClient):
    response = client.get("/api/profile", headers=auth_headers("1234A", "10.1.2.3"))
    assert response.status_code == 200
    payload = response.json()
    assert payload["user_id"] == "1234A"
    assert "ip" not in payload
    assert "avatar" in payload


def test_profile_endpoint_accepts_seven_character_login(client: TestClient):
    response = client.get("/api/profile", headers=auth_headers("1234AB1", "10.1.2.3"))
    assert response.status_code == 200
    payload = response.json()
    assert payload["user_id"] == "1234AB1"
    assert "ip" not in payload
    assert "avatar" in payload


def test_release_endpoint(client: TestClient):
    response = client.get("/api/release")
    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload.get("version"), str)
    assert payload["version"].strip()
    assert payload.get("changelog_url") == "/connections/reference/changelog"
    assert "last_crawled" in payload
    assert isinstance(payload.get("last_crawled"), str)


def test_changelog_page_route(client: TestClient):
    response = client.get("/connections/reference/changelog")
    assert response.status_code == 200
    assert "Changelog" in response.text


def test_chat_lifecycle(client: TestClient):
    create_response = client.post(
        "/api/chats",
        json={"title": "Test Chat"},
        headers=auth_headers("1111AA", "1.2.3.4"),
    )
    assert create_response.status_code == 200
    chat = create_response.json()["chat"]

    list_response = client.get("/api/chats", headers=auth_headers("1111AA", "1.2.3.4"))
    assert list_response.status_code == 200
    chat_ids = [item["id"] for item in list_response.json()["chats"]]
    assert chat["id"] in chat_ids

    rename_response = client.patch(
        f"/api/chats/{chat['id']}",
        json={"title": "Renamed"},
        headers=auth_headers("1111AA", "1.2.3.4"),
    )
    assert rename_response.status_code == 200

    delete_response = client.delete(
        f"/api/chats/{chat['id']}",
        headers=auth_headers("1111AA", "1.2.3.4"),
    )
    assert delete_response.status_code == 200


def test_distinct_users_can_share_one_ip(client: TestClient):
    import core.service as service

    shared_ip = "55.66.77.88"

    first = client.post(
        "/api/chats",
        json={"title": "Alpha"},
        headers=auth_headers("4444DD", shared_ip),
    )
    assert first.status_code == 200

    second = client.post(
        "/api/chats",
        json={"title": "Beta"},
        headers=auth_headers("5555EE", shared_ip),
    )
    assert second.status_code == 200

    first_list = client.get("/api/chats", headers=auth_headers("4444DD", shared_ip))
    second_list = client.get("/api/chats", headers=auth_headers("5555EE", shared_ip))
    assert [chat["title"] for chat in first_list.json()["chats"]] == ["Alpha"]
    assert [chat["title"] for chat in second_list.json()["chats"]] == ["Beta"]

    rows = list(service.db.t.user_ip_logs.rows_where("ip=?", [shared_ip]))
    assert len(rows) == 2
    assert len({row["user_id"] for row in rows}) == 2


def test_stream_passes_prior_turn_history(client: TestClient, monkeypatch):
    import interfaces.api.main as main

    class FakeLLM:
        def __init__(self):
            self.calls = []
            self.counter = 0

        def stream_answer_with_context(self, query, top_k=10, max_extracts=6, history=None):
            self.calls.append(
                {
                    "query": query,
                    "top_k": top_k,
                    "max_extracts": max_extracts,
                    "history": history or [],
                }
            )
            self.counter += 1
            yield {"type": "delta", "text": f"answer-{self.counter}"}
            yield {"type": "sources", "sources": []}
            yield {"type": "done"}

    fake = FakeLLM()
    monkeypatch.setattr(main, "_load_llmapi", lambda: fake)

    create_chat = client.post(
        "/api/chats",
        json={"title": "History Test"},
        headers=auth_headers("2222BB", "2.3.4.5"),
    )
    assert create_chat.status_code == 200
    chat_id = create_chat.json()["chat"]["id"]

    first_turn = client.post(
        f"/api/chats/{chat_id}/messages",
        json={"message": "What is MyWay?"},
        headers=auth_headers("2222BB", "2.3.4.5"),
    )
    assert first_turn.status_code == 200
    first_payload = first_turn.json()
    first_stream = client.post(
        "/api/stream",
        headers=auth_headers(
            "2222BB",
            "2.3.4.5",
            **{"Content-Type": "application/x-www-form-urlencoded"},
        ),
        data={
            "message": "What is MyWay?",
            "stream_id": first_payload["stream_id"],
            "message_id": str(first_payload["assistant_message_id"]),
            "chat_id": str(chat_id),
        },
    )
    assert first_stream.status_code == 200
    assert fake.calls[0]["history"] == []

    second_turn = client.post(
        f"/api/chats/{chat_id}/messages",
        json={"message": "How do I enroll?"},
        headers=auth_headers("2222BB", "2.3.4.5"),
    )
    assert second_turn.status_code == 200
    second_payload = second_turn.json()
    second_stream = client.post(
        "/api/stream",
        headers=auth_headers(
            "2222BB",
            "2.3.4.5",
            **{"Content-Type": "application/x-www-form-urlencoded"},
        ),
        data={
            "message": "How do I enroll?",
            "stream_id": second_payload["stream_id"],
            "message_id": str(second_payload["assistant_message_id"]),
            "chat_id": str(chat_id),
        },
    )
    assert second_stream.status_code == 200

    assert len(fake.calls) == 2
    assert [turn["role"] for turn in fake.calls[1]["history"]] == ["user", "assistant"]
    assert [turn["content"] for turn in fake.calls[1]["history"]] == [
        "What is MyWay?",
        "answer-1",
    ]


def test_build_rewrite_history_includes_fallback_metadata(client: TestClient):
    import interfaces.api.main as main
    import core.service as service
    import json

    create_chat = client.post(
        "/api/chats",
        json={"title": "History Metadata"},
        headers=auth_headers("3333CC", "3.4.5.6"),
    )
    assert create_chat.status_code == 200
    chat_id = create_chat.json()["chat"]["id"]

    first_turn = client.post(
        f"/api/chats/{chat_id}/messages",
        json={"message": "Customer is off for nonpayment"},
        headers=auth_headers("3333CC", "3.4.5.6"),
    )
    assert first_turn.status_code == 200
    first_payload = first_turn.json()
    service.update_message(
        first_payload["assistant_message_id"],
        content="What is the specific customer program or plan?",
        debug_json=json.dumps(
            {
                "fallback": {
                    "triggered": True,
                    "reason": "retry_still_insufficient",
                    "final_mode": "clarify",
                }
            },
            ensure_ascii=True,
        ),
    )

    second_turn = client.post(
        f"/api/chats/{chat_id}/messages",
        json={"message": "traditional"},
        headers=auth_headers("3333CC", "3.4.5.6"),
    )
    assert second_turn.status_code == 200
    second_payload = second_turn.json()

    history = main._build_rewrite_history(chat_id=chat_id, assistant_message_id=second_payload["assistant_message_id"])
    assistant_items = [item for item in history if item.get("role") == "assistant"]
    assert assistant_items
    last_assistant = assistant_items[-1]
    assert last_assistant["content"] == "What is the specific customer program or plan?"
    assert last_assistant["fallback_final_mode"] == "clarify"
    assert last_assistant["message_id"] == first_payload["assistant_message_id"]


def test_stream_persists_fallback_debug_payload(client: TestClient, monkeypatch):
    import interfaces.api.main as main

    class FakeLLM:
        def stream_answer_with_context(self, query, top_k=10, max_extracts=6, history=None):
            _ = (query, top_k, max_extracts, history)
            yield {"type": "delta", "text": "Can you clarify the customer type and program?"}
            yield {"type": "sources", "sources": [{"url": "https://connections/?docs=residential/alpha"}]}
            yield {
                "type": "debug",
                "debug": {
                    "query": "Need help",
                    "query_effective": "Need help",
                    "query_rewritten": None,
                    "query_rewrite": {"used": False, "reason": "no_history"},
                    "cached": False,
                    "cache": {"hit": False, "cache_id": None, "lookup_order": ["Need help"], "hit_query": None},
                    "glossary": {"included": False, "trigger_terms": [], "reason": "no_match"},
                    "retrieval": {"ranked_chunks": []},
                    "sources": [{"url": "https://connections/?docs=residential/alpha"}],
                    "llm_request": None,
                    "llm_response_text": "Can you clarify the customer type and program?",
                    "fallback": {
                        "triggered": True,
                        "reason": "retry_still_insufficient",
                        "final_mode": "answer",
                        "first_pass_retrieval": {"ranked_chunks": []},
                        "second_pass_retrieval": {"ranked_chunks": []},
                        "retry_config": {"top_k": 120, "max_extracts": 10},
                        "loop_guard_applied": True,
                        "clarify_turns_recent": 1,
                        "answer_mode": "loop_guard_best_effort",
                    },
                },
            }
            yield {"type": "done"}

    monkeypatch.setattr(main, "_load_llmapi", lambda: FakeLLM())

    create_chat = client.post(
        "/api/chats",
        json={"title": "Fallback Debug"},
        headers=auth_headers("7777DD", "7.8.9.10"),
    )
    assert create_chat.status_code == 200
    chat_id = create_chat.json()["chat"]["id"]

    create_msg = client.post(
        f"/api/chats/{chat_id}/messages",
        json={"message": "Need help"},
        headers=auth_headers("7777DD", "7.8.9.10"),
    )
    assert create_msg.status_code == 200
    payload = create_msg.json()

    stream_resp = client.post(
        "/api/stream",
        headers=auth_headers(
            "7777DD",
            "7.8.9.10",
            **{"Content-Type": "application/x-www-form-urlencoded"},
        ),
        data={
            "message": "Need help",
            "stream_id": payload["stream_id"],
            "message_id": str(payload["assistant_message_id"]),
            "chat_id": str(chat_id),
        },
    )
    assert stream_resp.status_code == 200
    assert "event: delta" in stream_resp.text
    assert "event: debug" in stream_resp.text
    assert "event: done" in stream_resp.text

    debug_resp = client.get(
        f"/api/messages/{payload['assistant_message_id']}/debug",
        headers=auth_headers("7777DD", "7.8.9.10"),
    )
    assert debug_resp.status_code == 200
    debug = debug_resp.json()["debug"]
    assert debug["fallback"]["triggered"] is True
    assert debug["fallback"]["reason"] == "retry_still_insufficient"
    assert debug["fallback"]["final_mode"] == "answer"
    assert debug["fallback"]["retry_config"] == {"top_k": 120, "max_extracts": 10}
    assert debug["fallback"]["loop_guard_applied"] is True
    assert debug["fallback"]["clarify_turns_recent"] == 1
    assert debug["fallback"]["answer_mode"] == "loop_guard_best_effort"


def test_stream_error_event_uses_friendly_message_and_persists(client: TestClient, monkeypatch):
    import interfaces.api.main as main

    class FakeLLM:
        def stream_answer_with_context(self, query, top_k=10, max_extracts=6, history=None):
            _ = (query, top_k, max_extracts, history)
            yield {"type": "error", "error": None}

    monkeypatch.setattr(main, "_load_llmapi", lambda: FakeLLM())

    create_chat = client.post(
        "/api/chats",
        json={"title": "Friendly Error"},
        headers=auth_headers("8888EE", "8.8.8.8"),
    )
    assert create_chat.status_code == 200
    chat_id = create_chat.json()["chat"]["id"]

    create_msg = client.post(
        f"/api/chats/{chat_id}/messages",
        json={"message": "elephant"},
        headers=auth_headers("8888EE", "8.8.8.8"),
    )
    assert create_msg.status_code == 200
    payload = create_msg.json()
    assistant_message_id = int(payload["assistant_message_id"])

    stream_resp = client.post(
        "/api/stream",
        headers=auth_headers(
            "8888EE",
            "8.8.8.8",
            **{"Content-Type": "application/x-www-form-urlencoded"},
        ),
        data={
            "message": "elephant",
            "stream_id": payload["stream_id"],
            "message_id": str(assistant_message_id),
            "chat_id": str(chat_id),
        },
    )
    assert stream_resp.status_code == 200
    assert "event: error" in stream_resp.text
    assert main._STREAM_ERROR_FALLBACK in stream_resp.text

    messages_resp = client.get(
        f"/api/chats/{chat_id}/messages?limit=20",
        headers=auth_headers("8888EE", "8.8.8.8"),
    )
    assert messages_resp.status_code == 200
    assistant_row = next(
        item for item in messages_resp.json()["messages"] if int(item["id"]) == assistant_message_id
    )
    assert assistant_row["content"] == main._STREAM_ERROR_FALLBACK


def test_stream_exception_uses_friendly_message_and_persists(client: TestClient, monkeypatch):
    import interfaces.api.main as main

    class EmptyMessageError(Exception):
        def __str__(self) -> str:
            return ""

    class FakeLLM:
        def stream_answer_with_context(self, query, top_k=10, max_extracts=6, history=None):
            _ = (query, top_k, max_extracts, history)
            raise EmptyMessageError()
            yield {"type": "done"}  # pragma: no cover

    monkeypatch.setattr(main, "_load_llmapi", lambda: FakeLLM())

    create_chat = client.post(
        "/api/chats",
        json={"title": "Exception Error"},
        headers=auth_headers("9999FF", "8.8.4.4"),
    )
    assert create_chat.status_code == 200
    chat_id = create_chat.json()["chat"]["id"]

    create_msg = client.post(
        f"/api/chats/{chat_id}/messages",
        json={"message": "elephant"},
        headers=auth_headers("9999FF", "8.8.4.4"),
    )
    assert create_msg.status_code == 200
    payload = create_msg.json()
    assistant_message_id = int(payload["assistant_message_id"])

    stream_resp = client.post(
        "/api/stream",
        headers=auth_headers(
            "9999FF",
            "8.8.4.4",
            **{"Content-Type": "application/x-www-form-urlencoded"},
        ),
        data={
            "message": "elephant",
            "stream_id": payload["stream_id"],
            "message_id": str(assistant_message_id),
            "chat_id": str(chat_id),
        },
    )
    assert stream_resp.status_code == 200
    assert "event: error" in stream_resp.text
    assert main._STREAM_ERROR_FALLBACK in stream_resp.text

    messages_resp = client.get(
        f"/api/chats/{chat_id}/messages?limit=20",
        headers=auth_headers("9999FF", "8.8.4.4"),
    )
    assert messages_resp.status_code == 200
    assistant_row = next(
        item for item in messages_resp.json()["messages"] if int(item["id"]) == assistant_message_id
    )
    assert assistant_row["content"] == main._STREAM_ERROR_FALLBACK


def test_profile_returns_is_admin_flag(client: TestClient, monkeypatch):
    monkeypatch.setenv("HIERAG_ADMIN_LOGIN_CODES", "9999ZZ")

    admin_response = client.get("/api/profile", headers=auth_headers("9999ZZ", "9.9.9.9"))
    assert admin_response.status_code == 200
    assert admin_response.json()["is_admin"] is True

    user_response = client.get("/api/profile", headers=auth_headers("1234AB", "1.1.1.1"))
    assert user_response.status_code == 200
    assert user_response.json()["is_admin"] is False


def test_admin_endpoints_require_whitelist(client: TestClient, monkeypatch):
    monkeypatch.setenv("HIERAG_ADMIN_LOGIN_CODES", "9999ZZ")

    response = client.get("/api/admin/stats/users", headers=auth_headers("1234AB", "1.2.3.4"))
    assert response.status_code == 403
    assert response.json()["detail"] == "Admin access required"


def test_admin_stats_and_interaction_detail_use_latest_feedback(client: TestClient, monkeypatch):
    monkeypatch.setenv("HIERAG_ADMIN_LOGIN_CODES", "9999ZZ")

    row_a = seed_admin_interaction(
        login_code="1111AA",
        question="How do I transfer service?",
        answer="Transfer service answer",
        query_effective="transfer service effective",
        query_rewritten="how do i transfer active service",
        feedback_ratings=[(-1, "wrong"), (1, "fixed")],
        sources=[
            {
                "url": "https://connections/?docs=residential/transfer-service",
                "vector_score_raw": 0.88,
                "bm25_score_raw": 12.4,
                "source_score_eligible": True,
            }
        ],
    )
    seed_admin_interaction(
        login_code="2222BB",
        question="How do I stop service?",
        answer="Stop service answer",
        feedback_ratings=[(-1, "missing step")],
        sources=[
            {
                "url": "https://connections/?docs=residential/stop-service",
                "vector_score_raw": 0.9,
                "bm25_score_raw": 11.0,
                "source_score_eligible": True,
            }
        ],
    )

    stats_response = client.get(
        "/api/admin/stats/users?range=all&sort=user_id:asc",
        headers=auth_headers("9999ZZ", "9.9.9.9"),
    )
    assert stats_response.status_code == 200
    stats_payload = stats_response.json()
    users = {item["user_id"]: item for item in stats_payload["users"]}
    assert users["1111AA"]["question_count"] == 1
    assert users["1111AA"]["positive_feedback_count"] == 1
    assert users["1111AA"]["negative_feedback_count"] == 0
    assert users["2222BB"]["negative_feedback_count"] == 1
    assert stats_payload["summary"]["positive_feedback_count"] == 1
    assert stats_payload["summary"]["negative_feedback_count"] == 1

    interactions_response = client.get(
        "/api/admin/interactions?range=all&rating=positive&user_id=1111AA",
        headers=auth_headers("9999ZZ", "9.9.9.9"),
    )
    assert interactions_response.status_code == 200
    interactions = interactions_response.json()["interactions"]
    assert len(interactions) == 1
    assert interactions[0]["assistant_message_id"] == row_a["assistant_message_id"]
    assert interactions[0]["rating"] == 1
    assert interactions[0]["query_rewritten"] == "how do i transfer active service"

    detail_response = client.get(
        f"/api/admin/interactions/{row_a['assistant_message_id']}",
        headers=auth_headers("9999ZZ", "9.9.9.9"),
    )
    assert detail_response.status_code == 200
    detail = detail_response.json()["interaction"]
    assert detail["question"] == "How do I transfer service?"
    assert detail["answer"] == "Transfer service answer"
    assert detail["query_effective"] == "transfer service effective"
    assert detail["query_rewritten"] == "how do i transfer active service"
    assert detail["rating"] == 1
    assert detail["note"] == "fixed"
    assert detail["sources"][0]["url"] == "https://connections/?docs=residential/transfer-service"


def test_admin_interaction_filters_support_search_and_unrated(client: TestClient, monkeypatch):
    monkeypatch.setenv("HIERAG_ADMIN_LOGIN_CODES", "9999ZZ")

    seed_admin_interaction(
        login_code="3333CC",
        question="How do I start service?",
        answer="Start service answer",
        query_effective="start service",
    )
    seed_admin_interaction(
        login_code="4444DD",
        question="What is MyWay?",
        answer="MyWay answer",
        feedback_ratings=[(-1, "not enough detail")],
    )

    unrated_response = client.get(
        "/api/admin/interactions?range=all&rating=unrated&search=start",
        headers=auth_headers("9999ZZ", "9.9.9.9"),
    )
    assert unrated_response.status_code == 200
    unrated_items = unrated_response.json()["interactions"]
    assert len(unrated_items) == 1
    assert unrated_items[0]["user_id"] == "3333CC"
    assert unrated_items[0]["rating"] == 0

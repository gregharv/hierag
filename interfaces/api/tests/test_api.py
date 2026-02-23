from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "test_app.db"
    scraper_db_path = tmp_path / "test_scraper.db"
    monkeypatch.setenv("HIERAG_APP_DB_PATH", str(db_path))
    monkeypatch.setenv("HIERAG_SCRAPER_DB_PATH", str(scraper_db_path))
    monkeypatch.setenv("HIERAG_URL_CLEANUP_ON_STARTUP", "0")

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
        response = test_client.get("/api/profile")
        assert response.status_code == 200

    assert calls == {"plan": 1, "apply": 1, "refresh": 1}


def test_startup_cleanup_fail_open_when_cleanup_raises(tmp_path, monkeypatch):
    db_path = tmp_path / "startup_fail_app.db"
    scraper_db_path = tmp_path / "startup_fail_scraper.db"
    monkeypatch.setenv("HIERAG_APP_DB_PATH", str(db_path))
    monkeypatch.setenv("HIERAG_SCRAPER_DB_PATH", str(scraper_db_path))
    monkeypatch.setenv("HIERAG_URL_CLEANUP_ON_STARTUP", "1")

    import interfaces.api.main as main
    import core.service as service

    importlib.reload(service)
    importlib.reload(main)

    def fake_plan(*args, **kwargs):
        raise RuntimeError("cleanup failed")

    monkeypatch.setattr(main, "plan_pages_cleanup", fake_plan)

    with TestClient(main.app) as test_client:
        response = test_client.get("/api/profile")
        assert response.status_code == 200


def test_profile_endpoint(client: TestClient):
    response = client.get("/api/profile", headers={"x-profile-ip": "10.1.2.3"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["ip"] == "10.1.2.3"
    assert "avatar" in payload


def test_release_endpoint(client: TestClient):
    response = client.get("/api/release")
    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload.get("version"), str)
    assert payload["version"].strip()
    assert payload.get("changelog_url") == "/connections/reference/changelog"


def test_changelog_page_route(client: TestClient):
    response = client.get("/connections/reference/changelog")
    assert response.status_code == 200
    assert "Changelog" in response.text


def test_chat_lifecycle(client: TestClient):
    create_response = client.post(
        "/api/chats",
        json={"title": "Test Chat"},
        headers={"x-profile-ip": "1.2.3.4"},
    )
    assert create_response.status_code == 200
    chat = create_response.json()["chat"]

    list_response = client.get("/api/chats", headers={"x-profile-ip": "1.2.3.4"})
    assert list_response.status_code == 200
    chat_ids = [item["id"] for item in list_response.json()["chats"]]
    assert chat["id"] in chat_ids

    rename_response = client.patch(
        f"/api/chats/{chat['id']}",
        json={"title": "Renamed"},
        headers={"x-profile-ip": "1.2.3.4"},
    )
    assert rename_response.status_code == 200

    delete_response = client.delete(
        f"/api/chats/{chat['id']}",
        headers={"x-profile-ip": "1.2.3.4"},
    )
    assert delete_response.status_code == 200


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
        headers={"x-profile-ip": "2.3.4.5"},
    )
    assert create_chat.status_code == 200
    chat_id = create_chat.json()["chat"]["id"]

    first_turn = client.post(
        f"/api/chats/{chat_id}/messages",
        json={"message": "What is MyWay?"},
        headers={"x-profile-ip": "2.3.4.5"},
    )
    assert first_turn.status_code == 200
    first_payload = first_turn.json()
    first_stream = client.post(
        "/api/stream",
        headers={"x-profile-ip": "2.3.4.5", "Content-Type": "application/x-www-form-urlencoded"},
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
        headers={"x-profile-ip": "2.3.4.5"},
    )
    assert second_turn.status_code == 200
    second_payload = second_turn.json()
    second_stream = client.post(
        "/api/stream",
        headers={"x-profile-ip": "2.3.4.5", "Content-Type": "application/x-www-form-urlencoded"},
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
        headers={"x-profile-ip": "3.4.5.6"},
    )
    assert create_chat.status_code == 200
    chat_id = create_chat.json()["chat"]["id"]

    first_turn = client.post(
        f"/api/chats/{chat_id}/messages",
        json={"message": "Customer is off for nonpayment"},
        headers={"x-profile-ip": "3.4.5.6"},
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
        headers={"x-profile-ip": "3.4.5.6"},
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
        headers={"x-profile-ip": "7.8.9.10"},
    )
    assert create_chat.status_code == 200
    chat_id = create_chat.json()["chat"]["id"]

    create_msg = client.post(
        f"/api/chats/{chat_id}/messages",
        json={"message": "Need help"},
        headers={"x-profile-ip": "7.8.9.10"},
    )
    assert create_msg.status_code == 200
    payload = create_msg.json()

    stream_resp = client.post(
        "/api/stream",
        headers={"x-profile-ip": "7.8.9.10", "Content-Type": "application/x-www-form-urlencoded"},
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
        headers={"x-profile-ip": "7.8.9.10"},
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

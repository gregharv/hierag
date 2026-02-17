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

    import interfaces.api.main as main
    import core.service as service

    importlib.reload(service)
    importlib.reload(main)
    service.create_db_and_tables()

    with TestClient(main.app) as test_client:
        yield test_client


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

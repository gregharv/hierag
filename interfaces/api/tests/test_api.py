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

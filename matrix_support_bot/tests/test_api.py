import pytest
from fastapi.testclient import TestClient
from app.main import app

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app import database, models


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Lifespan creates tables in this test's database, never in deployment data.
    engine = create_engine("sqlite:///" + str(tmp_path / "tickets.sqlite3"), connect_args={"check_same_thread": False})
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        engine.dispose()

def test_create_and_get_ticket(client):
    # Create ticket
    resp = client.post("/tickets/", json={"title": "Test ticket", "description": "Help needed"})
    assert resp.status_code == 200
    data = resp.json()
    ticket_id = data["id"]
    assert data["title"] == "Test ticket"
    assert data["status"] == "open"

    # Retrieve ticket
    resp = client.get(f"/tickets/{ticket_id}")
    assert resp.status_code == 200
    got = resp.json()
    assert got["id"] == ticket_id
    assert got["title"] == "Test ticket"

def test_message_flow(client):
    # Create ticket first
    resp = client.post("/tickets/", json={"title": "Msg flow", "description": ""})
    ticket_id = resp.json()["id"]

    # Add user message
    resp = client.post(f"/tickets/{ticket_id}/messages/", json={"content": "Hello"})
    assert resp.status_code == 200
    msg = resp.json()
    assert msg["role"] == "assistant"
    assert "Echo: Hello" in msg["content"]

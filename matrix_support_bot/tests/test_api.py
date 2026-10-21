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


@pytest.mark.parametrize("path", ["/tickets/999999", "/tickets/not-a-number"])
def test_missing_and_malformed_ticket(client, path):
    assert client.get(path).status_code in {404, 422}

@pytest.mark.parametrize("query", ["skip=-1", "limit=-1", "limit=0", "limit=501"])
def test_pagination_bounds(client, query):
    assert client.get("/tickets/?" + query).status_code == 422

@pytest.mark.parametrize("title", ["", "   ", "x"*201])
def test_invalid_title(client, title):
    assert client.post("/tickets/", json={"title":title}).status_code == 422


def test_status_messages_and_missing_ticket_are_consistent(client):
    ticket_id=client.post("/tickets/",json={"title":"  Проверка  "}).json()["id"]
    assert client.get(f"/tickets/{ticket_id}").json()["title"] == "Проверка"
    assert client.put(f"/tickets/{ticket_id}/status/",json={"status":"made-up"}).status_code == 422
    for status in ["pending","closed","open"]:
        assert client.put(f"/tickets/{ticket_id}/status/",json={"status":status}).json()["status"] == status
    assert client.post(f"/tickets/{ticket_id}/messages/",json={"content":"  "}).status_code == 422
    assert client.post("/tickets/99999/messages/",json={"content":"hello"}).status_code == 404
    assert client.put("/tickets/99999/status/",json={"status":"open"}).status_code == 404
    client.post(f"/tickets/{ticket_id}/messages/",json={"content":"Тест"})
    messages=client.get(f"/tickets/{ticket_id}").json()["messages"]
    assert [m["role"] for m in messages] == ["user","assistant"]
    assert messages[0]["content"] == "Тест"


@pytest.mark.parametrize("length,status", [(12000,200),(12001,422)])
def test_message_length_boundary_keeps_room_for_assistant_reply(client,length,status):
    ticket_id=client.post("/tickets/",json={"title":"Boundary check"}).json()["id"]
    result=client.post(f"/tickets/{ticket_id}/messages/",json={"content":"я"*length})
    assert result.status_code == status
    if status==200:
        assert len(result.json()["content"]) == length + len("Echo: ")
        assert len(client.get(f"/tickets/{ticket_id}").json()["messages"])==2

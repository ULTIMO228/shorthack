from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_shorten_and_redirect():
    r = client.post("/api/shorten", json={"url": "https://example.com/very/long/path"})
    assert r.status_code == 200
    code = r.json()["code"]

    r = client.get(f"/{code}", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "https://example.com/very/long/path"

    r = client.get(f"/api/stats/{code}")
    assert r.json()["clicks"] == 1


def test_not_found():
    assert client.get("/nope42", follow_redirects=False).status_code == 404

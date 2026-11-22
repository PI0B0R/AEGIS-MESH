"""Agents (phones and the server's scanner) and the unique ids on findings and scans."""
import uuid

import pytest
from fastapi.testclient import TestClient

from aegis import db as store
from aegis.api import create_app
from test_api import finish

PHONE = "3f2b8c1e-6d4a-4b7e-9c2f-1a2b3c4d5e6f"
OFFICE = {"bssid": "aa:bb:cc:00:00:01", "ssid": "Biuro", "security": "WPA2-PERSONAL", "rssi": -50}


@pytest.fixture
def client(data_dir, tmp_path, fake_nmap, monkeypatch):
    monkeypatch.setenv("AEGIS_ADMIN_PASSWORD", "admin-haslo-123")
    app = create_app(data_dir, tmp_path / "keys" / "k", background=False, run_chunk=fake_nmap)
    with TestClient(app) as c:
        token = c.post("/api/auth/login", json={"username": "admin", "password": "admin-haslo-123"}).json()["token"]
        c.headers["Authorization"] = f"Bearer {token}"
        c.app_state = app.state
        yield c


def test_the_server_scanner_is_an_agent(client):
    agents = client.get("/api/agents").json()["agents"]
    assert [(a["kind"], a["name"], a["this_server"]) for a in agents] == [("skaner", "Skaner serwera", True)]
    uuid.UUID(agents[0]["id"])


def test_every_scan_and_finding_has_its_own_uuid_and_the_scanner_agent(client):
    scanner = client.get("/api/agents").json()["agents"][0]["id"]
    client.post("/api/targets", json={"text": "10.0.0.0/29"})
    run = client.post("/api/scans").json()["id"]
    finish(client, run)
    scan = client.get(f"/api/scans/{run}").json()
    assert uuid.UUID(scan["uid"]).version == 4 and scan["agent_id"] == scanner
    findings = client.get("/api/findings").json()["findings"]
    assert len(findings) > 1 and len({f["uid"] for f in findings}) == len(findings)
    assert {f["agent_id"] for f in findings} == {scanner}
    # the next scan keeps the same finding ids
    finish(client, client.post("/api/scans").json()["id"])
    assert {f["uid"] for f in client.get("/api/findings").json()["findings"]} == {f["uid"] for f in findings}


def test_phone_registers_and_its_red_flags_carry_its_id(client):
    r = client.post("/api/agents/register", json={"id": PHONE, "name": "Samsung A54"})
    assert r.status_code == 200 and r.json()["kind"] == "telefon"
    client.post("/api/wifi/known", json={"agent_id": PHONE, "routers": [OFFICE]})
    client.post("/api/wifi/report", json={"agent_id": PHONE, "aps": [OFFICE, {"bssid": "12:34:56:78:9a:bc", "ssid": "Obcy"}]})
    flag = next(f for f in client.get("/api/findings").json()["findings"] if f["proto"] == "wifi-nowy")
    assert flag["agent_id"] == PHONE and uuid.UUID(flag["uid"])
    assert client.get("/api/wifi/known").json()["routers"][0]["agent_id"] == PHONE
    phone = next(a for a in client.get("/api/agents").json()["agents"] if a["id"] == PHONE)
    assert phone["findings"] == 1 and phone["short"] == PHONE[:8] and phone["registered_by"] == "admin"


def test_admin_renames_and_revokes_an_agent(client):
    client.post("/api/agents/register", json={"id": PHONE, "name": "Telefon"})
    assert client.patch(f"/api/agents/{PHONE}", json={"name": "Telefon Piotra"}).json()["name"] == "Telefon Piotra"
    # registering again keeps the admin's name
    assert client.post("/api/agents/register", json={"id": PHONE, "name": "Telefon"}).json()["name"] == "Telefon Piotra"
    client.patch(f"/api/agents/{PHONE}", json={"revoked": True})
    assert client.post("/api/agents/register", json={"id": PHONE}).status_code == 403
    assert client.post("/api/wifi/report", json={"agent_id": PHONE, "aps": [OFFICE]}).status_code == 403
    client.patch(f"/api/agents/{PHONE}", json={"revoked": False})
    assert client.post("/api/wifi/report", json={"agent_id": PHONE, "aps": [OFFICE]}).status_code == 200


def test_only_admin_changes_agents_and_ids_must_be_uuids(client):
    client.post("/api/users", json={"username": "analityk", "password": "analityk-haslo-1", "role": "analityk"})
    token = client.post("/api/auth/login", json={"username": "analityk", "password": "analityk-haslo-1"}).json()["token"]
    other = {"Authorization": f"Bearer {token}"}
    assert client.post("/api/agents/register", json={"id": PHONE}, headers=other).status_code == 200
    assert client.patch(f"/api/agents/{PHONE}", json={"revoked": True}, headers=other).status_code == 403
    assert client.post("/api/agents/register", json={"id": "telefon-1"}).status_code == 400


def test_older_rows_get_ids_on_start(client, data_dir):
    path = str(data_dir / "aegis.sqlite3")
    db = store.connect(path)
    db.execute("INSERT INTO findings (ip, port, proto, auto, title, first_seen, last_seen) VALUES ('10.9.9.9', 22, 'tcp', 'x', 't', 'a', 'a')")
    db.execute("UPDATE findings SET uid=NULL, agent_id=NULL")
    db.close()
    store.initialize(path)
    db = store.connect(path)
    row = db.execute("SELECT uid, agent_id FROM findings").fetchone()
    assert uuid.UUID(row["uid"]) and row["agent_id"] == store.settings(db)["scanner_agent_id"]
    db.close()

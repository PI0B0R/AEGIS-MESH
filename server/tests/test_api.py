import time

import pytest
from fastapi.testclient import TestClient

from aegis.api import create_app


@pytest.fixture
def client(data_dir, tmp_path, fake_nmap):
    app = create_app(data_dir, tmp_path / "keys" / "aegis.key", background=False, run_chunk=fake_nmap)
    with TestClient(app) as c:
        yield c


def finish(client, run_id, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = client.get(f"/api/scans/{run_id}").json()
        if run["status"] not in ("queued", "running"):
            return run
        time.sleep(0.05)
    raise AssertionError("scan did not finish")


def test_full_flow(client, data_dir):
    r = client.post("/api/targets", json={"text": "10.0.0.0/28, 10.0.1.1-3\nexample.com", "note": "laboratorium"})
    assert r.status_code == 200 and r.json()["addresses"] == 14 + 3 + 1
    assert client.post("/api/targets", json={"text": "10.0.0.0/28"}).json()["duplicates"] == 1
    r = client.post("/api/targets/import", json={"content": "adres,opis\n10.0.2.0/30,druga sieć\n", "filename": "cele.csv"})
    assert r.json()["added"][0]["value"] == "10.0.2.0/30"
    r = client.post("/api/targets/import", json={"content": '[{"ip": "10.0.3.5", "note": "serwer"}]'})
    assert r.json()["added"][0]["value"] == "10.0.3.5"
    targets = client.get("/api/targets").json()
    assert targets["addresses"] == 18 + 2 + 1
    assert {t["note"] for t in targets["targets"]} >= {"laboratorium", "druga sieć", "serwer"}

    run = client.post("/api/scans").json()
    assert run["status"] in ("queued", "running")
    run = finish(client, run["id"])
    assert run["status"] == "done" and run["progress"] == 100.0 and run["hosts_up"] > 0
    assert all(c["duration_seconds"] is not None for c in run["chunks"])
    hosts = client.get("/api/hosts").json()
    assert hosts["run_id"] == run["id"]
    web = next(h for h in hosts["hosts"] if h["ip"] == "10.0.0.1")
    assert web["services"][0] == {"port": 80, "proto": "tcp", "state": "open", "name": "http",
                                  "product": "nginx", "version": "1.25.3", "extrainfo": "Ubuntu"}
    est = client.get("/api/scans/estimate").json()
    assert est["addresses"] == 21 and est["estimate_seconds"] is not None

    second = finish(client, client.post("/api/scans").json()["id"])
    assert second["calibrated"] and second["estimate_seconds"] is not None
    assert [s["id"] for s in client.get("/api/scans").json()["scans"]] == [second["id"], run["id"]]

    raw = (data_dir / "aegis.sqlite3").read_bytes() + b"".join(p.read_bytes() for p in data_dir.glob("aegis.sqlite3-*"))
    assert b"laboratorium" not in raw and b"nginx" not in raw


def test_bad_input_is_400_with_polish_message(client):
    r = client.post("/api/targets", json={"text": "10.0.0.0/33"})
    assert r.status_code == 400 and "CIDR" in r.json()["detail"]
    assert client.post("/api/scans").status_code == 400                     # no targets yet
    assert client.patch("/api/settings", json={"rate": 0}).status_code == 422
    assert client.patch("/api/settings", json={"ports": "80; rm -rf /"}).status_code == 422


def test_pause_resume_over_api(client, fake_nmap):
    client.post("/api/targets", json={"text": "10.0.0.0/24"})
    client.patch("/api/settings", json={"min_chunk": 8, "max_chunk": 8})
    fake_nmap.gate.clear()
    run = client.post("/api/scans").json()
    assert client.post("/api/scans").status_code == 409                    # one at a time
    paused = client.post(f"/api/scans/{run['id']}/pause").json()
    assert paused["status"] == "paused"
    fake_nmap.gate.set()
    client.post(f"/api/scans/{run['id']}/resume")
    assert finish(client, run["id"])["status"] == "done"


def test_schedules_and_settings(client):
    assert set(client.get("/api/schedules/templates").json()) == {"nocny", "weekend", "co-6-godzin", "co-godzine"}
    s = client.post("/api/schedules", json={"template": "nocny"}).json()
    assert (s["window_start"], s["window_end"], s["frequency"]) == ("22:00", "06:00", "daily")
    custom = client.post("/api/schedules", json={"name": "rano", "frequency": "every:2h",
                                                 "window_start": "06:00", "window_end": "09:00"})
    assert custom.status_code == 200
    assert client.post("/api/schedules", json={"name": "zle", "frequency": "co chwilę",
                                               "window_start": "06:00", "window_end": "09:00"}).status_code == 400
    assert len(client.get("/api/schedules").json()["schedules"]) == 2
    assert client.delete(f"/api/schedules/{s['id']}").json() == {"deleted": s["id"]}
    assert client.patch("/api/settings", json={"rate": 500, "threads": 8}).json()["rate"] == 500


def test_docs_are_published(client):
    schema = client.get("/openapi.json").json()
    assert "/api/targets/import" in schema["paths"] and "/api/scans/{run_id}/pause" in schema["paths"]
    assert client.get("/docs").status_code == 200

"""Known Wi-Fi routers (baseline) and red flags from the phones' reports."""
import pytest
from fastapi.testclient import TestClient

from aegis import dojo, wifi
from aegis.api import create_app

HOME = (50.06470, 19.92340)
OFFICE = {"bssid": "aa:bb:cc:00:00:01", "ssid": "Biuro", "security": "WPA2-PERSONAL", "frequency": 2437,
          "rssi": -50, "lat": HOME[0], "lon": HOME[1], "radius": 10}
PRINTER = {"bssid": "aa:bb:cc:00:00:02", "ssid": "Drukarka", "security": "WPA2-PERSONAL", "frequency": 5180,
           "rssi": -60, "lat": HOME[0] + 0.0002, "lon": HOME[1], "radius": 12}
HERE = {"lat": HOME[0], "lon": HOME[1], "acc": 8}


@pytest.fixture
def client(data_dir, tmp_path, fake_nmap, monkeypatch):
    monkeypatch.setenv("AEGIS_ADMIN_PASSWORD", "admin-haslo-123")
    app = create_app(data_dir, tmp_path / "keys" / "k", background=False, run_chunk=fake_nmap)
    with TestClient(app) as c:
        token = c.post("/api/auth/login", json={"username": "admin", "password": "admin-haslo-123"}).json()["token"]
        c.headers["Authorization"] = f"Bearer {token}"
        yield c


def remember(client, *routers):
    r = client.post("/api/wifi/known", json={"routers": list(routers)})
    assert r.status_code == 200, r.text
    return r.json()


def report(client, *aps, position=HERE):
    r = client.post("/api/wifi/report", json={"aps": list(aps), "position": position})
    assert r.status_code == 200, r.text
    return r.json()


def kinds(result):
    return sorted(f["kind"] for f in result["flags"])


def test_remember_saves_position_channel_and_signal(client):
    assert remember(client, OFFICE, PRINTER) == {"saved": 2, "added": 2, "updated": 0, "removed": 0}
    assert remember(client, {**OFFICE, "rssi": -45})["updated"] == 1
    routers = client.get("/api/wifi/known").json()["routers"]
    office = next(r for r in routers if r["ssid"] == "Biuro")
    assert (office["lat"], office["frequency"], office["rssi"], office["added_by"]) == (HOME[0], 2437, -45, "admin")


def test_no_flags_without_a_baseline(client):
    assert report(client, OFFICE)["flags"] == []


def test_known_routers_in_place_raise_nothing(client):
    remember(client, OFFICE, PRINTER)
    assert report(client, OFFICE, PRINTER)["flags"] == []


def test_new_router_and_hotspot_become_p4_alerts(client):
    remember(client, OFFICE)
    result = report(client, OFFICE, {"bssid": "12:34:56:78:9a:bc", "ssid": "iPhone Ani", "rssi": -40, "hotspot": True})
    assert kinds(result) == ["wifi-nowy"]
    alerts = client.get("/api/findings?min_priority=4").json()["findings"]
    assert [(a["ip"], a["proto"], a["priority"], a["status"]) for a in alerts] == [("12:34:56:78:9a:bc", "wifi-nowy", 4, "nowe")]
    assert "hotspot" in alerts[0]["title"]
    # the same hotspot again is not announced twice
    assert report(client, OFFICE, {"bssid": "12:34:56:78:9a:bc", "ssid": "iPhone Ani", "rssi": -40})["flags"] == []


def test_known_name_on_unknown_bssid_is_an_evil_twin(client):
    remember(client, OFFICE)
    result = report(client, {"bssid": "de:ad:be:ef:00:01", "ssid": "Biuro", "security": "OPEN", "rssi": -45})
    assert kinds(result) == ["wifi-evil-twin"]
    alert = client.get("/api/findings?min_priority=5").json()["findings"][0]
    assert "aa:bb:cc:00:00:01" in " ".join(r["message"] for r in alert["reasons"])
    assert "słabsze zabezpieczenie" in " ".join(r["message"] for r in alert["reasons"])


def test_weaker_security_on_a_known_bssid(client):
    remember(client, OFFICE)
    assert kinds(report(client, {**OFFICE, "security": "OPEN"})) == ["wifi-zabezpieczenie"]


def test_known_router_far_away_has_moved(client):
    remember(client, OFFICE)
    far = {**OFFICE, "lat": HOME[0] + 0.002, "radius": 15}           # about 220 m north
    assert kinds(report(client, far)) == ["wifi-przesuniety"]
    # a rough estimate (big radius) is not enough to say it moved
    remember(client, OFFICE)
    assert report(client, {**OFFICE, "lat": HOME[0] + 0.004, "radius": 200})["flags"] == []


def test_known_router_silent_three_times_next_to_it_is_gone(client):
    remember(client, OFFICE, PRINTER)
    assert report(client, PRINTER)["flags"] == []
    assert report(client, PRINTER)["flags"] == []
    assert kinds(report(client, PRINTER)) == ["wifi-zniknal"]
    # weak GPS or standing far away does not count
    remember(client, OFFICE)
    for _ in range(5):
        assert report(client, PRINTER, position={**HERE, "acc": 100})["flags"] == []
    # heard again: the "gone" alert is settled
    report(client, OFFICE, PRINTER)
    states = {f["proto"]: f["state"] for f in client.get("/api/findings?state=all").json()["findings"]}
    assert states["wifi-zniknal"] == "gone"


def test_remembering_answers_the_new_flag(client):
    remember(client, OFFICE)
    stranger = {"bssid": "12:34:56:78:9a:bc", "ssid": "Gość", "rssi": -70}
    report(client, OFFICE, stranger)
    remember(client, stranger)
    assert client.get("/api/findings?min_priority=4").json()["findings"] == []


def test_edit_note_and_delete(client):
    remember(client, OFFICE)
    r = client.patch("/api/wifi/known/AA-BB-CC-00-00-01", json={"note": "router w serwerowni"})
    assert r.status_code == 200 and r.json()["note"] == "router w serwerowni"
    assert client.delete("/api/wifi/known/aa:bb:cc:00:00:01").status_code == 200
    assert client.get("/api/wifi/known").json()["total"] == 0
    assert client.delete("/api/wifi/known/aa:bb:cc:00:00:01").status_code == 404
    assert client.post("/api/wifi/known", json={"routers": [{"bssid": "nie-mac"}]}).status_code == 400


def test_replace_removes_routers_missing_from_the_list(client):
    remember(client, OFFICE, PRINTER)
    r = client.post("/api/wifi/known", json={"routers": [OFFICE], "replace": True}).json()
    assert r["removed"] == 1 and client.get("/api/wifi/known").json()["total"] == 1


def test_reader_sees_the_map_but_cannot_change_it(client):
    client.post("/api/users", json={"username": "czytelnik", "password": "czytelnik-haslo-1", "role": "czytelnik"})
    token = client.post("/api/auth/login", json={"username": "czytelnik", "password": "czytelnik-haslo-1"}).json()["token"]
    reader = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/wifi/known", headers=reader).status_code == 200
    assert client.post("/api/wifi/known", json={"routers": [OFFICE]}, headers=reader).status_code == 403
    assert client.post("/api/wifi/report", json={"aps": [OFFICE]}, headers=reader).status_code == 403


def test_wifi_flag_goes_to_defectdojo_without_an_endpoint():
    finding = {"ip": "de:ad:be:ef:00:01", "port": 0, "proto": "wifi-evil-twin", "priority": 5, "title": "Możliwy evil twin",
               "technologies": [{"name": "Wi-Fi", "version": "Biuro"}], "tags": ["wifi"], "reasons": [],
               "first_seen": "2026-11-22", "last_seen": "2026-11-22", "state": "active", "status": "nowe"}
    entry = dojo.to_generic(finding, None)
    assert entry["endpoints"] == [] and "Wi-Fi de:ad:be:ef:00:01" in entry["title"]
    assert entry["unique_id_from_tool"] == "aegis:de:ad:be:ef:00:01:0/wifi-evil-twin"


def test_distance_and_security_helpers():
    assert 215 < wifi.metres(HOME[0], HOME[1], HOME[0] + 0.002, HOME[1]) < 225
    assert wifi.weaker("OPEN", "WPA2-PERSONAL") and not wifi.weaker("WPA3-PERSONAL", "WPA2-PERSONAL")
    assert not wifi.weaker(None, "WPA2-PERSONAL")

"""Scope radar (BGP): RIPEstat answers are faked, nothing leaves the machine."""
import json
import urllib.error

import pytest
from fastapi.testclient import TestClient

from aegis import bgp
from aegis.api import create_app


def ripestat(*prefixes: str) -> bytes:
    return json.dumps({"status": "ok", "data": {"resource": "8501", "prefixes": [
        {"prefix": p, "timelines": [{"starttime": "2026-11-08T00:00:00", "endtime": "2026-11-22T00:00:00"}]}
        for p in prefixes]}}).encode()


ANSWER = ripestat("149.156.0.0/16", "149.156.96.0/24", "192.0.2.0/24", "198.51.100.0/24", "2001:db8::/32")


def offline(_url):
    raise urllib.error.URLError("no route to host")


def test_fetch_parses_and_asks_only_for_the_asn():
    seen = []
    prefixes = bgp.fetch_prefixes(8501, download=lambda url: seen.append(url) or ANSWER)
    assert prefixes == ["149.156.0.0/16", "149.156.96.0/24", "192.0.2.0/24", "198.51.100.0/24", "2001:db8::/32"]
    assert seen == ["https://stat.ripe.net/data/announced-prefixes/data.json?resource=AS8501&sourceapp=aegis-mesh"]


@pytest.mark.parametrize("download, text", [
    (offline, "Brak połączenia"),
    (lambda _u: b"<html>", "nieznanym formacie"),
    (lambda _u: json.dumps({"status": "error", "message": "bad resource"}).encode(), "bad resource"),
])
def test_fetch_failures_are_polish(download, text):
    with pytest.raises(bgp.BgpUnavailable, match=text):
        bgp.fetch_prefixes(8501, download=download)


def test_parse_asn():
    assert bgp.parse_asn("AS8501") == bgp.parse_asn(" as8501 ") == bgp.parse_asn(8501) == 8501
    for bad in ("ASx", "0", "-5", "99999999999"):
        with pytest.raises(ValueError, match="ASN"):
            bgp.parse_asn(bad)


def test_compare_coverage_outside_and_import_list():
    targets = [
        {"kind": "cidr", "value": "192.0.2.0/25", "enabled": 1},         # half of 192.0.2.0/24
        {"kind": "range", "value": "192.0.2.128-192.0.2.255", "enabled": 1},  # the other half
        {"kind": "cidr", "value": "198.51.100.0/26", "enabled": 1},      # part of 198.51.100.0/24
        {"kind": "ip", "value": "10.0.0.5", "enabled": 1},               # not announced
        {"kind": "cidr", "value": "198.51.100.192/26", "enabled": 0},    # disabled: no coverage
        {"kind": "domain", "value": "agh.edu.pl", "enabled": 1},
    ]
    result = bgp.compare(8501, bgp.fetch_prefixes(8501, download=lambda _u: ANSWER), targets)
    state = {p["prefix"]: (p["state"], p["percent"]) for p in result["prefixes"]}
    assert state["192.0.2.0/24"] == ("pokryty", 100.0)
    assert state["198.51.100.0/24"] == ("częściowo", 25.0)
    assert state["149.156.0.0/16"] == ("brak", 0.0)
    assert result["counts"] == {"pokryty": 1, "częściowo": 1, "brak": 3}
    # the /24 inside the /16 is not imported twice; IPv6 /32 is too big to be a target
    assert result["to_import"] == ["149.156.0.0/16"]
    assert "za duży" in next(p for p in result["prefixes"] if p["version"] == 6)["blocked"]
    outside = {o["value"]: o["reason"] for o in result["outside"]}
    assert outside == {"10.0.0.5": "poza ogłaszaną przestrzenią", "agh.edu.pl": "domena: sprawdź jej adres ręcznie"}


def test_partly_outside_target():
    result = bgp.compare(1, ["192.0.2.0/25"], [{"kind": "cidr", "value": "192.0.2.0/24", "enabled": 1}])
    assert result["outside"][0]["reason"] == "częściowo poza ogłaszaną przestrzenią"


@pytest.fixture
def app(data_dir, tmp_path, fake_nmap):
    app = create_app(data_dir, tmp_path / "keys" / "aegis.key", background=False, run_chunk=fake_nmap)
    app.state.bgp_download = lambda _url: ANSWER
    return app


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {app.state.cli_token}"
        yield c


def test_api_scope_and_import(client, app):
    assert client.get("/api/scope/bgp").status_code == 400                   # no ASN given or saved
    client.post("/api/targets", json={"text": "192.0.2.0/24 10.0.0.5"})
    scope = client.get("/api/scope/bgp", params={"asn": "AS8501"}).json()
    assert scope["asn"] == 8501 and not scope["stale"] and scope["to_import"] == ["149.156.0.0/16", "198.51.100.0/24"]

    # one chosen prefix, then all the missing ones
    r = client.post("/api/scope/bgp/import", json={"asn": "8501", "prefixes": ["198.51.100.0/24"]}).json()
    assert [a["value"] for a in r["added"]] == ["198.51.100.0/24"]
    r = client.post("/api/scope/bgp/import", json={"asn": "8501"}).json()
    assert [a["value"] for a in r["added"]] == ["149.156.0.0/16"] and r["addresses"] == 65534
    targets = {t["value"]: t for t in client.get("/api/targets").json()["targets"]}
    assert targets["149.156.0.0/16"]["source"] == "bgp" and targets["149.156.0.0/16"]["note"] == "AS8501 (BGP)"
    assert client.get("/api/scope/bgp", params={"asn": "8501"}).json()["to_import"] == []

    for bad in (["203.0.113.0/24"], ["2001:db8::/32"]):                      # not announced / too big
        assert client.post("/api/scope/bgp/import", json={"asn": "8501", "prefixes": bad}).status_code == 400


def test_api_offline_uses_last_answer(client, app):
    client.patch("/api/settings", json={"bgp_asn": 8501})
    assert "bgp_last" not in client.get("/api/settings").json()
    app.state.bgp_download = offline
    r = client.get("/api/scope/bgp")
    assert r.status_code == 503 and "Brak połączenia" in r.json()["detail"]   # nothing saved yet

    app.state.bgp_download = lambda _url: ANSWER
    assert client.get("/api/scope/bgp").json()["stale"] is False
    app.state.bgp_download = offline
    scope = client.get("/api/scope/bgp").json()
    assert scope["stale"] is True and "Brak połączenia" in scope["error"] and len(scope["prefixes"]) == 5
    assert client.get("/api/scope/bgp", params={"asn": "1"}).status_code == 503   # other ASN: no saved answer


def test_terminal_text(client, app):
    from aegis.cli import main, scope_text
    client.post("/api/targets", json={"text": "10.0.0.5"})
    app.state.bgp_download = lambda _url: ANSWER
    scope = client.get("/api/scope/bgp", params={"asn": "8501"}).json()
    text = scope_text(scope)
    assert "AS8501: 5 ogłaszanych prefiksów" in text and "brak: 5" in text and "za duży" in text
    assert "10.0.0.5" in text and "aegis scope import --asn 8501" in text
    app.state.bgp_download = offline
    assert "UWAGA: brak internetu" in scope_text(client.get("/api/scope/bgp", params={"asn": "8501"}).json())
    with pytest.raises(SystemExit):
        main(["scope"])                                                      # subcommand is required

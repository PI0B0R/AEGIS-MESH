"""End-to-end test against the docker compose test network, the way the jury will test it.

Runs only when AEGIS_URL points at a running server (fresh volumes), e.g.:

    docker compose down -v && docker compose up -d --build --wait
    AEGIS_URL=http://127.0.0.1:8000 pytest -m lab

With AEGIS_LAB_DOJO=1 (and `--profile dojo`) it also sends findings to DefectDojo.
The test starts and stops lab containers with `docker compose` to make changes between scans.
"""
import os
import subprocess
import time
from pathlib import Path

import httpx
import pytest

URL = os.environ.get("AEGIS_URL")
ROOT = Path(os.environ.get("AEGIS_COMPOSE_DIR", Path(__file__).resolve().parents[2]))
pytestmark = [pytest.mark.lab, pytest.mark.skipif(not URL, reason="AEGIS_URL not set (needs docker compose)")]

EXPECTED = {  # address -> (port, word that must appear in product or version)
    "172.30.0.20": (80, "nginx"),
    "172.30.0.21": (80, "Apache"),
    "172.30.0.22": (8080, "SimpleHTTPServer"),
    "172.30.0.23": (80, "nginx"),
    "172.30.0.24": (443, "nginx"),
    "172.30.0.30": (6379, "Redis"),
}


def compose(*args: str, check: bool = True) -> str:
    return subprocess.run(["docker", "compose", *args], cwd=ROOT, check=check, capture_output=True, text=True).stdout


def auth_headers() -> dict:
    """AEGIS_TOKEN, or the terminal token from inside the server container (same as the `aegis` command uses)."""
    token = os.environ.get("AEGIS_TOKEN") or compose("exec", "-T", "aegis", "cat", "/data/cli.token").strip()
    return {"Authorization": f"Bearer {token}"}


def _scan(client: httpx.Client, timeout: float = 300) -> dict:
    run = client.post("/api/scans").raise_for_status().json()
    deadline = time.monotonic() + timeout
    while run["status"] in ("queued", "running"):
        assert time.monotonic() < deadline, "scan did not finish in time"
        time.sleep(1)
        run = client.get(f"/api/scans/{run['id']}").raise_for_status().json()
    assert run["status"] == "done", run
    assert run["progress"] == 100.0 and run["active_seconds"] > 0
    assert all(c["status"] == "done" and c["duration_seconds"] is not None for c in run["chunks"])
    return run


def _changes(client, run) -> dict:
    return {(c["kind"], c["ip"]): c for c in client.get("/api/changes", params={"run_id": run["id"]}).json()["changes"]}


def _finding(client, ip, port) -> dict:
    return next(f for f in client.get("/api/findings", params={"ip": ip, "state": "all"}).json()["findings"]
                if f["port"] == port)


@pytest.fixture
def lab():
    compose("start", "staging")
    compose("--profile", "zmiana", "stop", "nowy-serwer", check=False)      # may not exist yet
    yield
    compose("start", "staging", check=False)
    compose("--profile", "zmiana", "stop", "nowy-serwer", check=False)


def test_scans_fingerprints_changes_unstable_hosts_and_edits(lab):
    with httpx.Client(base_url=URL, timeout=60, headers=auth_headers()) as client:
        assert httpx.get(f"{URL}/api/findings").status_code == 401             # nothing without a login
        client.post("/api/targets", json={"text": "172.30.0.0/27", "note": "siec testowa"}).raise_for_status()

        # Scan 1: discovery and fingerprinting.
        first = _scan(client)
        hosts = {h["ip"]: h for h in client.get("/api/hosts", params={"run_id": first["id"]}).json()["hosts"]}
        for ip, (port, word) in EXPECTED.items():
            service = next((s for s in hosts[ip]["services"] if s["port"] == port), None)
            assert service, f"{ip}:{port} not found"
            assert word.lower() in f"{service['product'] or ''} {service['version'] or ''}".lower(), service

        staging = _finding(client, "172.30.0.22", 8080)
        assert staging["priority"] == 5 and {"staging", "panel-admina", "logowanie-bez-https"} <= set(staging["tags"])
        wordpress = _finding(client, "172.30.0.23", 80)
        assert {"name": "WordPress", "version": "6.4.2", "category": "cms"} in wordpress["technologies"]
        camera = _finding(client, "172.30.0.24", 443)
        assert "iot" in camera["tags"] and "certyfikat" in camera["tags"]
        card = client.get("/api/hosts/172.30.0.24").json()["services"][0]["web"]
        assert card["scheme"] == "https" and card["tls"]["self_signed"] and card["tls"]["subject"] == "kamera.lab"
        assert any(c["name"] == "camsession" and not c["httponly"] for c in card["cookies"])
        assert _finding(client, "172.30.0.30", 6379)["priority"] == 5

        # A manual edit that the next scans must not overwrite.
        client.patch(f"/api/findings/{wordpress['id']}", json={"priority": 1, "note": "blog wydziału, znany"}).raise_for_status()

        # Scan 2: a new host appears, staging goes silent.
        compose("--profile", "zmiana", "up", "-d", "--no-build", "nowy-serwer")
        compose("stop", "staging")
        time.sleep(3)
        second = _scan(client)
        changes = _changes(client, second)
        assert ("new_host", "172.30.0.28") in changes
        assert "(1/3)" in changes[("unstable_host", "172.30.0.22")]["message"]
        assert any(c["kind"] == "recheck" for c in second["chunks"])
        edited = _finding(client, "172.30.0.23", 80)
        assert edited["priority"] == 1 and edited["is_edited"] and edited["note"] == "blog wydziału, znany"
        assert second["estimate_seconds"] / 3 <= second["active_seconds"] <= second["estimate_seconds"] * 3

        # Scans 3 and 4: staging is inactive only after the third silent scan.
        _scan(client)
        assert client.get("/api/hosts/172.30.0.22").json()["state"] == "unstable"
        fourth = _scan(client)
        assert ("gone_host", "172.30.0.22") in _changes(client, fourth)
        assert client.get("/api/hosts/172.30.0.22").json()["state"] == "inactive"

        # Scan 5: staging is back.
        compose("start", "staging")
        time.sleep(3)
        fifth = _scan(client)
        assert ("host_back", "172.30.0.22") in _changes(client, fifth)
        assert _finding(client, "172.30.0.22", 8080)["state"] == "active"

        summary = client.get("/api/summary").json()
        assert summary["active_hosts"] >= 8 and summary["high_priority"] >= 2
        assert client.get("/api/export/findings", params={"format": "csv"}).text.count("\n") >= 9

        if os.environ.get("AEGIS_LAB_DOJO") == "1":
            assert client.post("/api/dojo/check").raise_for_status().json()["ok"]
            sent = client.post("/api/dojo/send", json={"min_priority": 4}).raise_for_status().json()
            again = client.post("/api/dojo/send", json={"min_priority": 4}).raise_for_status().json()
            total = lambda result: result["statistics"]["after"]["total"]["total"]  # noqa: E731
            assert sent["sent"] >= 3 and total(again) == total(sent)                  # resend: no duplicates

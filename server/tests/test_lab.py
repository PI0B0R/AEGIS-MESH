"""End-to-end test against the docker compose test network.

Runs only when AEGIS_URL points at a running server, e.g.:

    docker compose up -d --build
    AEGIS_URL=http://127.0.0.1:8000 pytest -m lab
"""
import os
import time

import httpx
import pytest

URL = os.environ.get("AEGIS_URL")
pytestmark = [pytest.mark.lab, pytest.mark.skipif(not URL, reason="AEGIS_URL not set (needs docker compose)")]

EXPECTED = {  # address -> (port, word that must appear in product or version)
    "172.30.0.20": (80, "nginx"),
    "172.30.0.21": (80, "Apache"),
    "172.30.0.22": (8080, "SimpleHTTPServer"),
    "172.30.0.30": (6379, "Redis"),
}


def _scan(client: httpx.Client, timeout: float = 300) -> dict:
    run = client.post("/api/scans").raise_for_status().json()
    deadline = time.monotonic() + timeout
    while run["status"] in ("queued", "running"):
        assert time.monotonic() < deadline, "scan did not finish in time"
        time.sleep(1)
        run = client.get(f"/api/scans/{run['id']}").raise_for_status().json()
    return run


def test_two_scans_find_lab_services_and_time_them():
    with httpx.Client(base_url=URL, timeout=30) as client:
        client.post("/api/targets", json={"text": "172.30.0.0/27", "note": "siec testowa"}).raise_for_status()

        runs = [_scan(client), _scan(client)]
        for run in runs:
            assert run["status"] == "done", run
            assert run["active_seconds"] > 0
            assert all(c["status"] == "done" and c["duration_seconds"] is not None for c in run["chunks"])
            assert run["progress"] == 100.0

        # The second run is calibrated from the first, and its estimate is in the right ballpark.
        second = runs[1]
        assert second["calibrated"] == 1 and second["estimate_seconds"]
        assert second["estimate_seconds"] / 3 <= second["active_seconds"] <= second["estimate_seconds"] * 3

        hosts = {h["ip"]: h for h in client.get("/api/hosts", params={"run_id": second["id"]}).json()["hosts"]}
        for ip, (port, word) in EXPECTED.items():
            assert ip in hosts, f"{ip} not found"
            service = next((s for s in hosts[ip]["services"] if s["port"] == port), None)
            assert service, f"{ip}:{port} not found"
            banner = f"{service['product'] or ''} {service['version'] or ''}".lower()
            assert word.lower() in banner, service

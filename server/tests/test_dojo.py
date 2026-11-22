"""DefectDojo export against a small fake of its API v2."""
import email.parser
import email.policy
import http.server
import json
import threading

import pytest
from fastapi.testclient import TestClient

from aegis.api import create_app
from test_api import finish

RECEIVED: list[dict] = []


class FakeDojo(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        raw = self.rfile.read(int(self.headers["Content-Length"]))
        if self.path == "/api/v2/api-token-auth/":
            creds = json.loads(raw)
            return self._json(200, {"token": "t0k"}) if creds == {"username": "admin", "password": "tajne"} \
                else self._json(400, {"non_field_errors": ["Unable to log in"]})
        if self.headers.get("Authorization") != "Token t0k":
            return self._json(401, {"detail": "Invalid token."})
        message = email.parser.BytesParser(policy=email.policy.default).parsebytes(
            b"Content-Type: " + self.headers["Content-Type"].encode() + b"\r\n\r\n" + raw)
        form = {part.get_param("name", header="content-disposition"): part.get_content() for part in message.iter_parts()}
        form["file"] = json.loads(form["file"]) if isinstance(form["file"], (str, bytes)) else form["file"]
        RECEIVED.append({"path": self.path, "form": form})
        return self._json(201, {"test": 7, "engagement_id": 3, "product_id": 2, "statistics": {"after": {"info": 1}}})

    def do_GET(self):  # noqa: N802
        return self._json(200, {"count": 1}) if self.headers.get("Authorization") == "Token t0k" else self._json(401, {})


@pytest.fixture
def dojo_url():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeDojo)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    RECEIVED.clear()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture
def client(data_dir, tmp_path, fake_nmap):
    app = create_app(data_dir, tmp_path / "keys" / "k", background=False, run_chunk=fake_nmap)
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {app.state.cli_token}"
        yield c


def test_send_selected_findings_and_auto_export(client, fake_web, dojo_url, data_dir):
    assert client.post("/api/dojo/check").status_code == 502                  # not configured yet
    client.patch("/api/settings", json={"dojo_url": dojo_url, "dojo_user": "admin", "dojo_password": "tajne"})
    assert client.get("/api/settings").json()["dojo_password"] == "********"
    assert b"tajne" not in (data_dir / "aegis.sqlite3").read_bytes()
    assert client.post("/api/dojo/check").json()["ok"]

    client.post("/api/targets", json={"text": "10.0.0.0/29"})
    fake_web.pages[("10.0.0.2", 80)] = {"status": 200, "title": "Logowanie", "headers": {}, "body": "<input type=password>"}
    finish(client, client.post("/api/scans").json()["id"])
    assert RECEIVED == []                                                         # auto export is off

    top = client.get("/api/findings", params={"min_priority": 5}).json()["findings"][0]
    client.patch(f"/api/findings/{top['id']}", json={"note": "zgłoszone do IT", "status": "potwierdzone"})
    result = client.post("/api/dojo/send", json={"finding_ids": [top["id"]]}).json()
    assert result["sent"] == 1 and result["test_id"] == 7
    form = RECEIVED[0]["form"]
    assert RECEIVED[0]["path"] == "/api/v2/reimport-scan/" and form["scan_type"] == "Generic Findings Import"
    assert form["auto_create_context"] == "true" and form["product_name"] == "AEGIS MESH"
    entry = form["file"]["findings"][0]
    assert entry["severity"] == "Info" and entry["verified"] and entry["unique_id_from_tool"] == "aegis:10.0.0.2:80/tcp"
    assert "Panel logowania bez HTTPS" in entry["title"] and "zgłoszone do IT" in entry["description"]
    assert entry["endpoints"] == [{"host": "10.0.0.2", "port": 80, "protocol": "http"}]
    assert client.get(f"/api/findings/{top['id']}").json()["dojo_sent_at"]

    client.patch("/api/settings", json={"dojo_auto": True, "dojo_min_priority": 1})
    client.post("/api/targets", json={"text": "10.0.0.13"})
    finish(client, client.post("/api/scans").json()["id"])
    auto = RECEIVED[-1]["form"]["file"]["findings"]
    assert len(RECEIVED) == 2 and len(auto) == 4                                  # only the ones not sent before


def test_bad_credentials_give_a_clear_error(client, dojo_url):
    client.patch("/api/settings", json={"dojo_url": dojo_url, "dojo_user": "admin", "dojo_password": "zle"})
    r = client.post("/api/dojo/check")
    assert r.status_code == 502 and "400" in r.json()["detail"]

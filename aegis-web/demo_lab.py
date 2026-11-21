"""Observe two temporary services on loopback for the AGH demonstration."""
import http.client
import json
import ssl
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TLS_DIR = Path(__file__).resolve().parent / "demo-tls"
TIMEOUT = 0.8


class PageTitle(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_title = False
        self.title = ""

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self.in_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title += data


class LabService(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        behavior = self.server.behavior
        if behavior == "timeout":
            time.sleep(TIMEOUT * 2)
            return
        changed = behavior == "changed"
        title = "Przerwa techniczna" if changed else self.server.title
        body = f"<html><head><title>{title}</title></head></html>".encode()
        self.send_response(503 if changed else 200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Set-Cookie", "lab_session=fixture; HttpOnly")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@contextmanager
def local_service(title, tls=False):
    with ThreadingHTTPServer(("127.0.0.1", 0), LabService) as server:
        if tls:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(TLS_DIR / "loopback-test.crt", TLS_DIR / "loopback-test.key")
            server.socket = context.wrap_socket(server.socket, server_side=True)
        server.title = title
        server.behavior = "stable"
        worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        worker.start()
        try:
            yield server
        finally:
            server.shutdown()
            worker.join()


def observe(server, scheme, context):
    host, port = server.server_address
    row = {"host": host, "port": port, "scheme": scheme, "at": datetime.now(timezone.utc).isoformat(),
           "status": "timeout", "http": None, "title": "", "server": "", "cookies": [], "tls": ""}
    connection = (http.client.HTTPSConnection(host, port, context=context, timeout=TIMEOUT)
                  if scheme == "https" else http.client.HTTPConnection(host, port, timeout=TIMEOUT))
    started = time.perf_counter()
    try:
        connection.connect()
        if scheme == "https":
            row["tls"] = connection.sock.version()
        connection.request("GET", "/")
        response = connection.getresponse()
        parser = PageTitle()
        parser.feed(response.read(8192).decode("utf-8", errors="replace"))
        row.update(status="response", http=response.status, title=parser.title,
                   server=response.getheader("Server", ""),
                   cookies=[v.split("=", 1)[0] for k, v in response.getheaders() if k.lower() == "set-cookie"])
    except TimeoutError:
        pass
    finally:
        connection.close()
    row["ms"] = round((time.perf_counter() - started) * 1000, 1)
    return row


def run(scenario):
    if scenario not in ("changed", "timeout", "stable"):
        raise ValueError("Nieznany scenariusz.")
    context = ssl.create_default_context(cafile=str(TLS_DIR / "loopback-test.crt"))
    runs = []
    with local_service("Serwis informacyjny") as http, local_service("Portal testowy", tls=True) as https:
        for behavior in ("stable", scenario):
            https.behavior = behavior
            start = time.perf_counter()
            rows = [observe(http, "http", context), observe(https, "https", context)]
            runs.append({"durationMs": round((time.perf_counter() - start) * 1000, 1), "services": rows})
    return {"format": "aegis-discovery-1", "scenario": scenario, "runs": runs}


def last_run(db):
    row = db.execute("SELECT result FROM discovery_demo WHERE id=1").fetchone()
    if row:
        data = json.loads(row[0])
        if data.get("format") == "aegis-discovery-1":
            return data

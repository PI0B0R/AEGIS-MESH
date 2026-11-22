"""Real HTTP and HTTPS probing against small local servers."""
import datetime as dt
import http.server
import ssl
import threading

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from aegis import web
from conftest import REAL_PROBE

ICON = b"\x00\x00\x01\x00fake-icon-bytes"
REQUESTS: list[tuple[str, str]] = []


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "nginx/1.25.3"
    sys_version = ""

    def version_string(self):
        return self.server_version

    def log_message(self, *args):
        pass

    def do_GET(self):  # noqa: N802
        REQUESTS.append((self.command, self.path))
        if self.path == "/":
            self.send_response(302)
            self.send_header("Location", "/start")
            self.end_headers()
        elif self.path == "/start":
            body = ("<html><head><title>Panel  Staging &amp; Test</title></head><body>"
                    "<form><input type='password' name='p'></form>"
                    "<footer>Moja-App wersja 2.4.1</footer></body></html>").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("X-Powered-By", "PHP/8.1.2")
            self.send_header("Set-Cookie", "PHPSESSID=abc; Path=/")
            self.send_header("Set-Cookie", "lang=pl; Secure; HttpOnly; SameSite=Lax")
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/favicon.ico":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(ICON)
        elif self.path == "/admin/":
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="admin"')
            self.end_headers()
        elif self.path == "/away":
            self.send_response(301)
            self.send_header("Location", "http://example.org/")
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()


def serve(context: ssl.SSLContext | None = None):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    if context:
        server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture
def plain():
    server = serve()
    yield server.server_address[1]
    server.shutdown()


@pytest.fixture
def tls(tmp_path):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "staging.lab")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now - dt.timedelta(days=30)).not_valid_after(now - dt.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("staging.lab")]), critical=False)
            .sign(key, hashes.SHA256()))
    (tmp_path / "c.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (tmp_path / "k.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                       serialization.NoEncryption()))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(tmp_path / "c.pem", tmp_path / "k.pem")
    server = serve(context)
    yield server.server_address[1]
    server.shutdown()


def test_http_probe_reads_page_headers_cookies_favicon_and_paths(plain):
    REQUESTS.clear()
    info = REAL_PROBE("127.0.0.1", plain, False, paths=["/admin/", "/nope"], limiter=web.RateLimiter(1000))
    assert info.error is None and info.status == 200 and info.scheme == "http"
    assert info.redirects == [{"from": "/", "status": 302, "to": "/start", "followed": True}]
    assert info.url.endswith("/start") and info.title == "Panel Staging & Test"
    assert info.server == "nginx/1.25.3" and info.headers["x-powered-by"] == "PHP/8.1.2"
    cookies = {c["name"]: c for c in info.cookies}
    assert not cookies["PHPSESSID"]["httponly"] and cookies["lang"]["secure"] and cookies["lang"]["samesite"] == "Lax"
    assert info.favicon_mmh3 == web.favicon_hash(ICON)
    assert info.paths["/admin/"]["status"] == 401 and info.paths["/nope"]["status"] == 404
    assert {m for m, _ in REQUESTS} == {"GET"}                     # only GET, never a form submit


def test_https_probe_describes_certificate_without_trusting_it(tls):
    info = REAL_PROBE("127.0.0.1", tls, True, limiter=web.RateLimiter(1000))
    assert info.scheme == "https" and info.status == 200
    assert info.tls["subject"] == "staging.lab" and info.tls["self_signed"] and info.tls["expired"]
    assert info.tls["san"] == ["staging.lab"] and len(info.tls["sha256"]) == 64


def test_plain_request_to_tls_port_falls_back_to_https(tls):
    info = REAL_PROBE("127.0.0.1", tls, False, limiter=web.RateLimiter(1000))
    assert info.scheme == "https" and info.status == 200


def test_redirects_never_leave_the_host(plain):
    client = web.Client("127.0.0.1", plain, False, None, 3, web.RateLimiter(1000))
    assert web._same_host("http://example.org/", client) is None
    assert web._same_host(f"http://127.0.0.1:{plain}/x?y=1", client) == "/x?y=1"
    assert web._same_host("https://127.0.0.1/x", client) is None                 # scheme change
    assert web._same_host("javascript:alert(1)", client) is None


def test_closed_port_gives_an_error_not_an_exception():
    info = REAL_PROBE("127.0.0.1", 1, False, timeout=1, limiter=web.RateLimiter(1000))
    assert info.status is None and info.error


def test_mmh3_matches_reference_values():
    assert web.mmh3_32(b"hello") == 613153351
    assert web.mmh3_32(b"foo") == -156908512
    assert web.mmh3_32(b"") == 0


def test_rate_limiter_spaces_requests():
    import time
    limiter = web.RateLimiter(50)
    started = time.monotonic()
    for _ in range(6):
        limiter.wait()
    assert time.monotonic() - started >= 0.09


def test_which_services_are_web():
    assert web.is_web("http", 1234) and web.is_web("ssl", 9999) and web.is_web(None, 8080)
    assert web.is_web("unknown", 4443, "ssl") and not web.is_web("ssh", 22)
    assert web.wants_tls("https", 4443) and web.wants_tls("http", 8443) and not web.wants_tls("http", 80)

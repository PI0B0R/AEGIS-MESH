"""HTTP/HTTPS fingerprinting of web services that nmap found.

Only safe GET requests: the main page, the favicon and a short list of paths that
signatures ask for (e.g. /admin, /login, /version). No form is ever submitted, no
credentials are sent, redirects are followed only on the same host, bodies are
capped and requests are rate limited.
"""
import base64
import hashlib
import http.client
import re
import ssl
import threading
import time
from dataclasses import asdict, dataclass, field
from html import unescape
from urllib.parse import urljoin, urlsplit

WEB_PORTS = {80, 81, 443, 591, 3000, 5000, 5601, 8000, 8008, 8080, 8081, 8088, 8443, 8888, 9000, 9090, 9200, 9443}
TLS_PORTS = {443, 8443, 9443}
MAX_BODY = 256 * 1024
KEEP_BODY = 64 * 1024            # kept (encrypted) for signatures checked later on existing hosts
KEEP_PATH_BODY = 16 * 1024
USER_AGENT = "AEGIS-MESH/0.2 (discovery; bez prób logowania)"
TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
ICON = re.compile(r"<link[^>]+rel=[\"'][^\"']*icon[^\"']*[\"'][^>]*>", re.I)
HREF = re.compile(r"href=[\"']([^\"']+)[\"']", re.I)


@dataclass
class Page:
    path: str
    status: int | None
    title: str | None = None
    length: int = 0
    body: str = ""
    location: str | None = None


@dataclass
class WebInfo:
    url: str
    scheme: str
    status: int | None = None
    title: str | None = None
    server: str | None = None
    headers: dict = field(default_factory=dict)
    cookies: list = field(default_factory=list)
    redirects: list = field(default_factory=list)
    tls: dict | None = None
    favicon_mmh3: int | None = None
    favicon_md5: str | None = None
    body: str = ""
    paths: dict = field(default_factory=dict)          # path -> Page as dict
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "WebInfo":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


def is_web(name: str | None, port: int, tunnel: str | None = None) -> bool:
    name = (name or "").lower()
    return "http" in name or name in ("ssl", "https") or tunnel == "ssl" or port in WEB_PORTS


def wants_tls(name: str | None, port: int, tunnel: str | None = None) -> bool:
    name = (name or "").lower()
    return tunnel == "ssl" or name in ("https", "ssl") or "https" in name or port in TLS_PORTS


class RateLimiter:
    """At most `per_second` requests per second, shared by all probing threads."""

    def __init__(self, per_second: float):
        self.interval = 1.0 / max(0.1, per_second)
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = self._next - now
            self._next = max(now, self._next) + self.interval
        if delay > 0:
            time.sleep(delay)


def mmh3_32(data: bytes, seed: int = 0) -> int:
    """MurmurHash3 x86 32-bit, signed: the favicon hash format used by Shodan and others."""
    c1, c2, mask = 0xcc9e2d51, 0x1b873593, 0xffffffff
    h, length = seed & mask, len(data)
    for i in range(0, length - length % 4, 4):
        k = int.from_bytes(data[i:i + 4], "little")
        k = (k * c1) & mask
        k = ((k << 15) | (k >> 17)) & mask
        h ^= (k * c2) & mask
        h = ((h << 13) | (h >> 19)) & mask
        h = (h * 5 + 0xe6546b64) & mask
    tail, k = data[length - length % 4:], 0
    if len(tail) >= 3:
        k ^= tail[2] << 16
    if len(tail) >= 2:
        k ^= tail[1] << 8
    if tail:
        k ^= tail[0]
        k = (k * c1) & mask
        k = ((k << 15) | (k >> 17)) & mask
        h ^= (k * c2) & mask
    h ^= length
    h ^= h >> 16
    h = (h * 0x85ebca6b) & mask
    h ^= h >> 13
    h = (h * 0xc2b2ae35) & mask
    h ^= h >> 16
    return h - (1 << 32) if h & 0x80000000 else h


def favicon_hash(raw: bytes) -> int:
    return mmh3_32(base64.encodebytes(raw))


def title_of(html: str) -> str | None:
    found = TITLE.search(html)
    if not found:
        return None
    return re.sub(r"\s+", " ", unescape(found.group(1))).strip()[:200] or None


def parse_cookie(header: str) -> dict:
    parts = [p.strip() for p in header.split(";")]
    name, _, value = parts[0].partition("=")
    flags = {p.split("=", 1)[0].lower(): (p.split("=", 1)[1] if "=" in p else True) for p in parts[1:] if p}
    return {"name": name, "value": value, "secure": "secure" in flags, "httponly": "httponly" in flags,
            "samesite": flags.get("samesite") if isinstance(flags.get("samesite"), str) else None,
            "path": flags.get("path") if isinstance(flags.get("path"), str) else None}


def describe_cert(der: bytes) -> dict:
    from cryptography import x509
    from cryptography.x509.oid import ExtensionOID, NameOID

    cert = x509.load_der_x509_certificate(der)

    def cn(name):
        values = name.get_attributes_for_oid(NameOID.COMMON_NAME)
        return values[0].value if values else name.rfc4514_string()

    try:
        sans = cert.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME).value.get_values_for_type(x509.DNSName)
    except x509.ExtensionNotFound:
        sans = []
    not_after = cert.not_valid_after_utc
    return {"subject": cn(cert.subject), "issuer": cn(cert.issuer), "san": sans[:20],
            "not_before": cert.not_valid_before_utc.isoformat(), "not_after": not_after.isoformat(),
            "expired": not_after.timestamp() < time.time(), "self_signed": cert.subject == cert.issuer,
            "sha256": hashlib.sha256(der).hexdigest()}


class Client:
    """One host:port. Keeps the TLS decision and certificate from the first request."""

    def __init__(self, ip: str, port: int, tls: bool, host_header: str | None, timeout: float, limiter: RateLimiter):
        self.ip, self.port, self.tls, self.timeout, self.limiter = ip, port, tls, timeout, limiter
        default = 443 if tls else 80
        self.host_header = (host_header or ip) + ("" if port == default else f":{port}")
        self.cert: dict | None = None

    def _connection(self):
        if self.tls:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE          # we describe the certificate, we do not trust it
            return http.client.HTTPSConnection(self.ip, self.port, timeout=self.timeout, context=context)
        return http.client.HTTPConnection(self.ip, self.port, timeout=self.timeout)

    def get(self, path: str, limit: int = MAX_BODY):
        self.limiter.wait()
        conn = self._connection()
        try:
            conn.request("GET", path, headers={"Host": self.host_header, "User-Agent": USER_AGENT,
                                               "Accept": "text/html,*/*;q=0.8", "Connection": "close"})
            if self.tls and self.cert is None and conn.sock is not None:
                der = conn.sock.getpeercert(binary_form=True)
                if der:
                    try:
                        self.cert = describe_cert(der)
                    except Exception as error:  # noqa: BLE001 - a broken certificate is information, not a failure
                        self.cert = {"error": str(error)[:200]}
            response = conn.getresponse()
            raw = response.read(limit)
            return response, raw
        finally:
            conn.close()


def _decode(raw: bytes, response) -> str:
    charset = response.headers.get_content_charset() or "utf-8"
    try:
        return raw.decode(charset, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def _same_host(location: str, client: Client) -> str | None:
    """Path to follow, or None when the redirect leaves the host (we never leave the target)."""
    parts = urlsplit(location)
    if parts.scheme and parts.scheme not in ("http", "https"):
        return None
    if parts.hostname and parts.hostname not in (client.ip, client.host_header.split(":")[0]):
        return None
    if parts.scheme and (parts.scheme == "https") != client.tls:
        return None
    return (parts.path or "/") + (f"?{parts.query}" if parts.query else "")


def probe(ip: str, port: int, tls: bool, hostname: str | None = None, paths=(), timeout: float = 6,
          limiter: RateLimiter | None = None, stop: threading.Event | None = None) -> WebInfo:
    limiter = limiter or RateLimiter(10)
    client = Client(ip, port, tls, hostname, timeout, limiter)
    try:
        response, raw = client.get("/")
    except (ssl.SSLError, http.client.BadStatusLine, ConnectionResetError) as error:
        if tls:
            return WebInfo(f"https://{ip}:{port}/", "https", error=str(error)[:200])
        client = Client(ip, port, True, hostname, timeout, limiter)      # plain request to a TLS port
        try:
            response, raw = client.get("/")
        except (OSError, http.client.HTTPException) as again:
            return WebInfo(f"http://{ip}:{port}/", "http", error=str(again)[:200])
    except (OSError, http.client.HTTPException) as error:
        scheme = "https" if tls else "http"
        return WebInfo(f"{scheme}://{ip}:{port}/", scheme, error=str(error)[:200])

    scheme = "https" if client.tls else "http"
    info = WebInfo(f"{scheme}://{client.host_header}/", scheme)
    path = "/"
    for _ in range(5):                       # follow up to 5 redirects on the same host
        location = response.headers.get("Location")
        if response.status not in (301, 302, 303, 307, 308) or not location:
            break
        nxt = _same_host(urljoin(f"{scheme}://{client.host_header}{path}", location), client)
        info.redirects.append({"from": path, "status": response.status, "to": location, "followed": nxt is not None})
        if nxt is None or (stop and stop.is_set()):
            break
        path = nxt
        try:
            response, raw = client.get(path)
        except (OSError, http.client.HTTPException) as error:
            info.error = str(error)[:200]
            break

    body = _decode(raw, response)
    info.status = response.status
    info.headers = {k.lower(): v for k, v in response.getheaders() if k.lower() != "set-cookie"}
    info.cookies = [parse_cookie(v) for k, v in response.getheaders() if k.lower() == "set-cookie"]
    info.server = response.headers.get("Server")
    info.title = title_of(body)
    info.body = body[:KEEP_BODY]
    info.tls = client.cert
    if info.redirects:
        info.url = f"{scheme}://{client.host_header}{path}"

    icon = "/favicon.ico"
    link = ICON.search(body)
    if link and HREF.search(link.group(0)):
        icon = _same_host(urljoin(f"{scheme}://{client.host_header}{path}", HREF.search(link.group(0)).group(1)), client) or icon
    if not (stop and stop.is_set()):
        try:
            icon_response, icon_raw = client.get(icon, 512 * 1024)
            if icon_response.status == 200 and icon_raw and not icon_raw.lstrip()[:1] == b"<":
                info.favicon_mmh3 = favicon_hash(icon_raw)
                info.favicon_md5 = hashlib.md5(icon_raw).hexdigest()  # noqa: S324 - an identifier, not security
        except (OSError, http.client.HTTPException):
            pass

    for extra in dict.fromkeys(paths):
        if stop and stop.is_set():
            break
        try:
            page_response, page_raw = client.get(extra, KEEP_PATH_BODY)
            text = _decode(page_raw, page_response)
            info.paths[extra] = asdict(Page(extra, page_response.status, title_of(text), len(page_raw), text,
                                            page_response.headers.get("Location")))
        except (OSError, http.client.HTTPException) as error:
            info.paths[extra] = asdict(Page(extra, None, body=str(error)[:200]))
    return info


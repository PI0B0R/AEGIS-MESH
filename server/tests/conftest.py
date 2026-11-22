import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import aegis.web  # noqa: E402
from aegis.scanner import HostResult, Interrupted, Service  # noqa: E402
from aegis.web import WebInfo  # noqa: E402

REAL_PROBE = aegis.web.probe


class FakeNmap:
    """Stands in for nmap: every address ending in .1-.5 is up with a web server. Honours stop."""

    def __init__(self, delay_per_host=0.002, alive=lambda a: a.rsplit(".", 1)[-1] in {"1", "2", "3", "4", "5"},
                 services=None):
        self.delay, self.alive, self.calls = delay_per_host, alive, []
        # address -> list of Service; default: nginx on port 80
        self.services = services or (lambda a: [Service(80, "tcp", "open", "http", "nginx", "1.25.3", "Ubuntu")])
        self.gate = threading.Event()
        self.gate.set()

    def __call__(self, addresses, settings, stop: threading.Event):
        self.calls.append(list(addresses))
        started = time.monotonic()
        deadline = started + self.delay * len(addresses)
        while time.monotonic() < deadline or not self.gate.is_set():
            if stop.wait(0.005):
                raise Interrupted()
        hosts = [HostResult(a, None, list(self.services(a))) for a in addresses if self.alive(a)]
        return hosts, max(time.monotonic() - started, 1e-3)


class FakeWeb:
    """Stands in for HTTP probing. pages[(ip, port)] = WebInfo fields; default: a plain nginx page."""

    def __init__(self):
        self.pages, self.calls = {}, []

    def __call__(self, ip, port, tls, hostname=None, paths=(), timeout=6, limiter=None, stop=None):
        self.calls.append((ip, port, tls, tuple(paths)))
        page = self.pages.get((ip, port), {"status": 200, "title": "Witaj", "headers": {"server": "nginx/1.25.3"},
                                           "server": "nginx/1.25.3", "body": "<html><title>Witaj</title></html>"})
        scheme = "https" if tls else "http"
        return WebInfo(**{"url": f"{scheme}://{ip}:{port}/", "scheme": scheme, **page})


@pytest.fixture(autouse=True)
def fake_web(monkeypatch):
    web = FakeWeb()
    monkeypatch.setattr(aegis.web, "probe", web)
    return web


@pytest.fixture
def fake_nmap():
    return FakeNmap()


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("AEGIS_KEY", raising=False)
    return tmp_path / "data"

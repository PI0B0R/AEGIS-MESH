import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aegis.scanner import HostResult, Interrupted, Service  # noqa: E402


class FakeNmap:
    """Stands in for nmap: every address ending in .1-.5 is up with a web server. Honours stop."""

    def __init__(self, delay_per_host=0.002, alive=lambda a: a.rsplit(".", 1)[-1] in {"1", "2", "3", "4", "5"}):
        self.delay, self.alive, self.calls = delay_per_host, alive, []
        self.gate = threading.Event()
        self.gate.set()

    def __call__(self, addresses, settings, stop: threading.Event):
        self.calls.append(list(addresses))
        started = time.monotonic()
        deadline = started + self.delay * len(addresses)
        while time.monotonic() < deadline or not self.gate.is_set():
            if stop.wait(0.005):
                raise Interrupted()
        hosts = [HostResult(a, None, [Service(80, "tcp", "open", "http", "nginx", "1.25.3", "Ubuntu")])
                 for a in addresses if self.alive(a)]
        return hosts, max(time.monotonic() - started, 1e-3)


@pytest.fixture
def fake_nmap():
    return FakeNmap()


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("AEGIS_KEY", raising=False)
    return tmp_path / "data"

"""One nmap process per subgroup. Safe defaults: rate limit, allowed ports only, light version probes."""
import os
import shutil
import subprocess
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field


class Interrupted(Exception):
    """The subgroup was stopped (pause or end of the time window). It goes back to the queue."""


@dataclass
class Service:
    port: int
    proto: str
    state: str
    name: str | None = None
    product: str | None = None
    version: str | None = None
    extrainfo: str | None = None
    tunnel: str | None = None       # "ssl" when nmap saw TLS in front of the service


@dataclass
class HostResult:
    ip: str
    hostname: str | None
    services: list[Service] = field(default_factory=list)


def nmap_binary() -> str:
    found = shutil.which(os.environ.get("AEGIS_NMAP", "nmap"))
    if not found:
        raise RuntimeError("Nie znaleziono programu nmap. Uruchom AEGIS przez docker compose albo zainstaluj nmap.")
    return found


def build_command(targets_file: str, settings: dict) -> list[str]:
    return [
        nmap_binary(), "-oX", "-", "-n",
        "-T4", "--max-rate", str(int(settings["rate"])),
        "--max-retries", str(int(settings["max_retries"])),
        "--host-timeout", f"{int(settings['host_timeout'])}s",
        "-p", str(settings["ports"]),
        "-sV", "--version-light",
        "-iL", targets_file,
    ]


def parse_xml(text: str) -> list[HostResult]:
    root = ET.fromstring(text)
    hosts = []
    for host in root.findall("host"):
        status = host.find("status")
        if status is None or status.get("state") != "up":
            continue
        address = next((a.get("addr") for a in host.findall("address") if a.get("addrtype") in ("ipv4", "ipv6")), None)
        if not address:
            continue
        names = [h.get("name") for h in host.findall("hostnames/hostname") if h.get("name")]
        result = HostResult(address, names[0] if names else None)
        for port in host.findall("ports/port"):
            state = port.find("state")
            if state is None or state.get("state") not in ("open", "open|filtered"):
                continue
            svc = port.find("service")
            get = (lambda key: svc.get(key) if svc is not None else None)
            result.services.append(Service(int(port.get("portid")), port.get("protocol"), state.get("state"),
                                           get("name"), get("product"), get("version"), get("extrainfo"),
                                           get("tunnel")))
        hosts.append(result)
    return hosts


def run_chunk(addresses: list[str], settings: dict, stop: threading.Event) -> tuple[list[HostResult], float]:
    """Runs nmap on one subgroup. Returns (hosts that are up, duration in seconds)."""
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
        handle.write("\n".join(addresses) + "\n")
        path = handle.name
    started = time.monotonic()
    try:
        process = subprocess.Popen(build_command(path, settings), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, errors="replace")
        output: dict[str, str] = {}
        reader = threading.Thread(target=lambda: output.update(zip(("out", "err"), process.communicate())))
        reader.start()
        while reader.is_alive():
            if stop.wait(0.3):
                process.terminate()
                try:
                    process.wait(5)
                except subprocess.TimeoutExpired:
                    process.kill()
                reader.join()
                raise Interrupted()
        if process.returncode != 0:
            raise RuntimeError(f"nmap zakończył się kodem {process.returncode}: {output.get('err', '').strip()[:300]}")
        return parse_xml(output["out"]), time.monotonic() - started
    finally:
        os.unlink(path)

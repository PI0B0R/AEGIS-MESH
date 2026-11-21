from pathlib import Path

from aegis import db
from aegis.scanner import build_command, parse_xml

SAMPLE = (Path(__file__).parent / "data" / "nmap-sample.xml").read_text()


def test_parse_only_up_hosts_and_open_ports():
    hosts = parse_xml(SAMPLE)
    assert [h.ip for h in hosts] == ["172.30.0.20", "172.30.0.30"]
    web = hosts[0]
    assert web.hostname == "web-nginx"
    assert [(s.port, s.name, s.product, s.version) for s in web.services] == [(80, "http", "nginx", "1.27.2")]
    assert hosts[1].services[0].name == "redis"


def test_command_is_rate_limited_and_restricted_to_allowed_ports(monkeypatch):
    monkeypatch.setattr("aegis.scanner.nmap_binary", lambda: "nmap")
    cmd = build_command("/tmp/list.txt", dict(db.DEFAULT_SETTINGS, rate=250, ports="22,80"))
    assert cmd[cmd.index("--max-rate") + 1] == "250"
    assert cmd[cmd.index("-p") + 1] == "22,80"
    assert "--version-light" in cmd and cmd[-2:] == ["-iL", "/tmp/list.txt"]
    assert not any(flag in cmd for flag in ("--script", "-A", "-O"))       # no exploit or intrusive scripts

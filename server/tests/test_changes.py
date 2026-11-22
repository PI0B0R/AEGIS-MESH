"""Two and more scans: new, missing and changed services, unstable hosts, protected edits, time of day."""
from datetime import datetime

from aegis import db as store
from aegis import diff
from aegis import findings as F
from aegis.crypto import FieldCipher
from aegis.engine import Engine
from aegis.scanner import Service
from aegis.targets import parse_text
from conftest import FakeNmap


class Lab:
    """A tiny network whose hosts and services each test changes between scans."""

    def __init__(self, tmp_path, fake_web):
        self.up = {"10.0.0.1", "10.0.0.2", "10.0.0.3"}
        self.svc = {
            "10.0.0.1": [Service(80, "tcp", "open", "http", "nginx", "1.25.3")],
            "10.0.0.2": [Service(80, "tcp", "open", "http", "nginx", "1.25.3"), Service(22, "tcp", "open", "ssh", "OpenSSH", "9.6")],
            "10.0.0.3": [Service(80, "tcp", "open", "http", "Apache httpd", "2.4.58")],
            "10.0.0.4": [Service(6379, "tcp", "open", "redis", "Redis key-value store", "7.4.1")],
        }
        self.nmap = FakeNmap(delay_per_host=0, alive=lambda a: a in self.up, services=lambda a: self.svc.get(a, []))
        self.web = fake_web
        self.hour = 23
        path = str(tmp_path / "a.sqlite3")
        store.initialize(path)
        self.cipher = FieldCipher.load(tmp_path / "k")
        self.db = store.connect(path)
        for t in parse_text("10.0.0.0/29"):
            self.db.execute("INSERT INTO targets (kind, value, size, source, created_at) VALUES (?, ?, ?, 'manual', ?)",
                            (t.kind, t.value, t.size, store.now()))
        self.engine = Engine(path, self.cipher, run_chunk=self.nmap,
                             clock=lambda: datetime(2026, 11, 21, self.hour, 0))

    def scan(self) -> int:
        run_id = self.engine.create_run()
        self.engine.start(run_id)
        self.engine.wait(run_id, 20)
        assert self.db.execute("SELECT status FROM scan_runs WHERE id=?", (run_id,)).fetchone()[0] == "done"
        return run_id

    def changes(self, run_id) -> list[tuple]:
        return [(r["kind"], r["ip"], r["port"], r["priority"], self.cipher.open(r["message"], "changes.message"))
                for r in self.db.execute("SELECT * FROM changes WHERE run_id=? ORDER BY id", (run_id,))]

    def finding(self, ip, port):
        return F.to_dict(self.db.execute("SELECT * FROM findings WHERE ip=? AND port=?", (ip, port)).fetchone(), self.cipher)

    def host(self, ip):
        return self.db.execute("SELECT * FROM hosts WHERE ip=?", (ip,)).fetchone()


def test_first_scan_reports_new_hosts(tmp_path, fake_web):
    lab = Lab(tmp_path, fake_web)
    changes = lab.changes(lab.scan())
    assert [c[:2] for c in changes] == [("new_host", "10.0.0.1"), ("new_host", "10.0.0.2"), ("new_host", "10.0.0.3")]
    assert "80/tcp nginx 1.25.3" in changes[0][4]
    assert lab.finding("10.0.0.2", 22)["technologies"][0]["name"] == "SSH"


def test_second_scan_finds_new_gone_and_changed_services_and_rechecks_missing_host(tmp_path, fake_web):
    lab = Lab(tmp_path, fake_web)
    lab.scan()
    lab.svc["10.0.0.1"] = [Service(80, "tcp", "open", "http", "nginx", "1.27.0")]
    lab.svc["10.0.0.2"] = [Service(80, "tcp", "open", "http", "nginx", "1.25.3"),
                           Service(6379, "tcp", "open", "redis", "Redis key-value store", "7.4.1")]
    lab.up = {"10.0.0.1", "10.0.0.2", "10.0.0.4"}                  # .3 went silent, .4 is new
    fake_web.pages[("10.0.0.2", 80)] = {"status": 200, "title": "Staging sklepu", "headers": {}, "body": ""}
    run = lab.scan()

    assert lab.nmap.calls[-1] == ["10.0.0.3"]                      # the silent host got one more try
    kinds = {(k, ip, port): (prio, msg) for k, ip, port, prio, msg in lab.changes(run)}
    assert kinds[("new_service", "10.0.0.2", 6379)][0] == 5          # exposed Redis
    assert ("gone_service", "10.0.0.2", 22) in kinds
    assert "1.25.3 → 1.27.0" in kinds[("changed_service", "10.0.0.1", 80)][1]
    assert "Staging sklepu" in kinds[("changed_service", "10.0.0.2", 80)][1]
    assert kinds[("new_host", "10.0.0.4", None)][0] == 5
    assert "(1/3)" in kinds[("unstable_host", "10.0.0.3", None)][1]
    assert lab.host("10.0.0.3")["state"] == "unstable"
    assert lab.finding("10.0.0.2", 22)["state"] == "gone" and lab.finding("10.0.0.3", 80)["state"] == "active"


def test_host_becomes_inactive_after_three_silent_scans_and_can_come_back(tmp_path, fake_web):
    lab = Lab(tmp_path, fake_web)
    lab.scan()
    lab.up.discard("10.0.0.3")
    runs = [lab.scan() for _ in range(3)]
    states = [c[0] for r in runs for c in lab.changes(r)]
    assert states == ["unstable_host", "unstable_host", "gone_host"]
    assert lab.host("10.0.0.3")["state"] == "inactive" and lab.finding("10.0.0.3", 80)["state"] == "gone"
    lab.scan()                                                      # inactive hosts are not re-checked any more
    assert lab.nmap.calls[-1] != ["10.0.0.3"]
    lab.up.add("10.0.0.3")
    back = lab.changes(lab.scan())
    assert back[0][0] == "host_back" and lab.host("10.0.0.3")["state"] == "active"
    assert lab.finding("10.0.0.3", 80)["state"] == "active"


def test_manual_edits_survive_scans_until_reset(tmp_path, fake_web):
    lab = Lab(tmp_path, fake_web)
    lab.scan()
    finding = lab.finding("10.0.0.1", 80)
    F.edit(lab.db, lab.cipher, finding["id"], {"priority": 5, "tags": ["Ważne", "ważne"], "note": "sprawdzić w poniedziałek",
                                               "title": "Serwer księgowości"})
    fake_web.pages[("10.0.0.1", 80)] = {"status": 200, "title": "Logowanie", "headers": {}, "body": "<input type=password>"}
    lab.scan()
    after = lab.finding("10.0.0.1", 80)
    assert (after["priority"], after["tags"], after["title"]) == (5, ["ważne"], "Serwer księgowości")
    assert after["is_edited"] and set(after["edited"]) == {"priority", "tags", "note", "title"}
    assert after["auto"]["priority"] == 5 and "logowanie-bez-https" in after["auto"]["tags"]   # scan still learns
    F.reset(lab.db, lab.cipher, after["id"], ["tags", "title"])
    reset = lab.finding("10.0.0.1", 80)
    assert "logowanie-bez-https" in reset["tags"] and reset["title"].startswith("Panel logowania bez HTTPS")
    assert reset["edited"] == ["priority", "note"] and reset["note"] == "sprawdzić w poniedziałek"


def test_sensitive_derived_data_is_encrypted_at_rest(tmp_path, fake_web):
    lab = Lab(tmp_path, fake_web)
    lab.scan()
    lab.db.execute("PRAGMA wal_checkpoint(FULL)")
    raw = (tmp_path / "a.sqlite3").read_bytes()
    assert b"nginx" not in raw and b"OpenSSH" not in raw and b"Witaj" not in raw


def test_host_answering_only_at_night_is_reported(tmp_path, fake_web):
    lab = Lab(tmp_path, fake_web)
    lab.hour = 23
    lab.scan()
    lab.scan()
    lab.up.discard("10.0.0.3")
    lab.hour = 10
    lab.scan()
    lab.up.add("10.0.0.3")
    lab.hour = 2
    lab.scan()
    lab.up.discard("10.0.0.3")
    lab.hour = 14
    changes = lab.changes(lab.scan())
    day = [c for c in changes if c[0] == "daypart" and c[1] == "10.0.0.3"]
    assert day and "noc, wieczór" in day[0][4] and "rano, popołudnie" in day[0][4]
    summary = diff.presence_summary(lab.db, "10.0.0.3")
    assert summary["parts"]["wieczór"] == {"up": 2, "total": 2} and summary["parts"]["rano"] == {"up": 0, "total": 1}

import json
import time

import pytest

from aegis import db as store
from aegis.crypto import FieldCipher
from aegis.engine import Engine
from aegis.targets import parse_text


def setup(tmp_path, fake, text="10.0.0.0/24", **settings):
    path = str(tmp_path / "a.sqlite3")
    store.initialize(path)
    cipher = FieldCipher.load(tmp_path / "k")
    conn = store.connect(path)
    for key, value in settings.items():
        store.save_setting(conn, key, value)
    for t in parse_text(text):
        conn.execute("INSERT INTO targets (kind, value, size, source, created_at) VALUES (?, ?, ?, 'manual', ?)",
                     (t.kind, t.value, t.size, store.now()))
    return path, cipher, conn, Engine(path, cipher, run_chunk=fake)


def wait_status(conn, run_id, wanted, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = conn.execute("SELECT status FROM scan_runs WHERE id=?", (run_id,)).fetchone()[0]
        if status in wanted:
            return status
        time.sleep(0.02)
    raise AssertionError(f"run {run_id} stuck in {status}")


def test_calibration_then_right_sized_subgroups(tmp_path, fake_nmap):
    path, cipher, conn, engine = setup(tmp_path, fake_nmap, chunk_seconds=0.1, min_chunk=4, max_chunk=64)
    run_id = engine.create_run()
    first = conn.execute("SELECT chunk_size, calibrated, estimate_seconds FROM scan_runs WHERE id=?", (run_id,)).fetchone()
    assert (first["chunk_size"], first["calibrated"], first["estimate_seconds"]) == (16, 0, None)
    engine.start(run_id)
    engine.wait(run_id, 10)
    run = conn.execute("SELECT * FROM scan_runs WHERE id=?", (run_id,)).fetchone()
    assert run["status"] == "done" and run["calibrated"] == 1
    assert len(fake_nmap.calls[0]) == 16                        # calibration subgroup
    assert {len(c) for c in fake_nmap.calls[1:-1]} == {run["chunk_size"]} and run["chunk_size"] != 16
    assert sorted(a for c in fake_nmap.calls for a in c) == sorted(f"10.0.0.{i}" for i in range(1, 255))
    assert run["hosts_up"] == 5 and run["active_seconds"] > 0 and run["estimate_seconds"] > 0
    chunks = conn.execute("SELECT duration_seconds, started_at, finished_at FROM scan_chunks WHERE run_id=?", (run_id,)).fetchall()
    assert all(c["duration_seconds"] > 0 and c["started_at"] and c["finished_at"] for c in chunks)
    assert store.settings(conn)["measured_rate"] > 0

    second = engine.create_run()                                 # next run starts already calibrated
    row = conn.execute("SELECT calibrated, estimate_seconds FROM scan_runs WHERE id=?", (second,)).fetchone()
    assert row["calibrated"] == 1 and row["estimate_seconds"] > 0


def test_banners_are_encrypted_at_rest(tmp_path, fake_nmap):
    path, cipher, conn, engine = setup(tmp_path, fake_nmap, text="10.0.0.1")
    run_id = engine.create_run()
    engine.start(run_id)
    engine.wait(run_id, 5)
    row = conn.execute("SELECT product, version, extrainfo FROM services").fetchone()
    assert all(v.startswith("enc1:") for v in row)
    raw = (tmp_path / "a.sqlite3").read_bytes()
    assert b"nginx" not in raw and b"1.25.3" not in raw
    assert cipher.open(row["version"], "services.version") == "1.25.3"


def test_pause_keeps_progress_and_resume_finishes(tmp_path, fake_nmap):
    path, cipher, conn, engine = setup(tmp_path, fake_nmap, text="10.0.0.0/23", threads=2, min_chunk=16,
                                       max_chunk=16, measured_rate=100, chunk_seconds=1)
    fake_nmap.delay = 0.003
    run_id = engine.create_run()
    engine.start(run_id)
    while conn.execute("SELECT COUNT(*) FROM scan_chunks WHERE run_id=? AND status='done'", (run_id,)).fetchone()[0] < 3:
        time.sleep(0.01)
    engine.pause(run_id)
    engine.wait(run_id, 5)
    run = conn.execute("SELECT * FROM scan_runs WHERE id=?", (run_id,)).fetchone()
    done_before = conn.execute("SELECT COUNT(*) FROM scan_chunks WHERE run_id=? AND status='done'", (run_id,)).fetchone()[0]
    assert run["status"] == "paused" and run["pauses"] == 1 and 3 <= done_before < 32
    assert conn.execute("SELECT COUNT(*) FROM scan_chunks WHERE status='running'").fetchone()[0] == 0
    calls_before = len(fake_nmap.calls)
    engine.start(run_id)
    engine.wait(run_id, 10)
    assert conn.execute("SELECT status FROM scan_runs WHERE id=?", (run_id,)).fetchone()[0] == "done"
    scanned = [a for c in fake_nmap.calls[calls_before:] for a in c]
    finished_first = {a for r in conn.execute("SELECT targets FROM scan_chunks WHERE run_id=? AND finished_at IS NOT NULL", (run_id,))
                      for a in json.loads(r[0])}
    assert len(finished_first) == 510
    assert len(scanned) <= 510 - 16 * done_before + 16 * 2        # finished subgroups are not scanned again


def test_restart_recovers_running_run_as_paused(tmp_path, fake_nmap):
    path, cipher, conn, engine = setup(tmp_path, fake_nmap, text="10.0.0.0/28", measured_rate=50)
    run_id = engine.create_run()
    conn.execute("UPDATE scan_runs SET status='running' WHERE id=?", (run_id,))
    conn.execute("UPDATE scan_chunks SET status='running' WHERE run_id=?", (run_id,))
    again = Engine(path, cipher, run_chunk=fake_nmap)                 # server restarted
    assert conn.execute("SELECT status FROM scan_runs WHERE id=?", (run_id,)).fetchone()[0] == "paused"
    again.start(run_id)
    again.wait(run_id, 5)
    assert tuple(conn.execute("SELECT status, hosts_up FROM scan_runs WHERE id=?", (run_id,)).fetchone()) == ("done", 5)


def test_failed_subgroup_is_reported(tmp_path):
    def broken(addresses, settings, stop):
        raise RuntimeError("nmap: brak uprawnień")
    path, cipher, conn, engine = setup(tmp_path, broken, text="10.0.0.1", measured_rate=1)
    run_id = engine.create_run()
    engine.start(run_id)
    engine.wait(run_id, 5)
    run = conn.execute("SELECT status, error FROM scan_runs WHERE id=?", (run_id,)).fetchone()
    assert run["status"] == "done" and "1 podgrup" in run["error"]
    assert "uprawnień" in conn.execute("SELECT error FROM scan_chunks").fetchone()[0]


def test_one_run_at_a_time_and_no_targets(tmp_path, fake_nmap):
    path, cipher, conn, engine = setup(tmp_path, fake_nmap, text="10.0.0.0/24")
    fake_nmap.gate.clear()
    first = engine.create_run()
    engine.start(first)
    second = engine.create_run()
    with pytest.raises(RuntimeError, match="Trwa już skan"):
        engine.start(second)
    fake_nmap.gate.set()
    engine.wait(first, 10)
    conn.execute("DELETE FROM targets")
    with pytest.raises(ValueError, match="Brak celów"):
        engine.create_run()

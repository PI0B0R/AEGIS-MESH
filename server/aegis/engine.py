"""Scan runs: calibration, subgroups, parallel nmap, timing, pause and resume.

Progress lives in the database (one row per subgroup), so a run that is paused,
stopped by the end of its time window or interrupted by a restart continues
where it stopped instead of starting over.
"""
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import db as store
from . import scanner as nmap
from .crypto import FieldCipher
from .targets import chunks, expand_all

ACTIVE = ("queued", "running")


class Engine:
    def __init__(self, db_path: str, cipher: FieldCipher, run_chunk=nmap.run_chunk):
        self.db_path = db_path
        self.cipher = cipher
        self.run_chunk = run_chunk
        self._lock = threading.Lock()
        self._stops: dict[int, threading.Event] = {}
        self._threads: dict[int, threading.Thread] = {}
        self._recover()

    # ---- public API -------------------------------------------------------------
    def create_run(self, trigger: str = "manual", schedule_id: int | None = None) -> int:
        db = store.connect(self.db_path)
        try:
            rows = db.execute("SELECT kind, value FROM targets WHERE enabled=1 ORDER BY id").fetchall()
            addresses = expand_all((r["kind"], r["value"]) for r in rows)
            if not addresses:
                raise ValueError("Brak celów do skanowania. Dodaj adresy, zakresy lub domeny.")
            s = store.settings(db)
            measured = s.get("measured_rate")
            if measured:
                size, calibrated = self.chunk_size(s, measured), 1
                estimate = self.estimate(len(addresses), s, measured)
            else:
                size, calibrated, estimate = int(s["calibration_chunk"]), 0, None
            db.execute("BEGIN IMMEDIATE")
            run_id = db.execute(
                "INSERT INTO scan_runs (status, trigger, schedule_id, created_at, total_hosts, calibrated, chunk_size,"
                " rate, estimate_seconds) VALUES ('queued', ?, ?, ?, ?, ?, ?, ?, ?)",
                (trigger, schedule_id, store.now(), len(addresses), calibrated, size, int(s["rate"]), estimate)).lastrowid
            self._insert_chunks(db, run_id, addresses, size, 0)
            db.execute("COMMIT")
            return run_id
        finally:
            db.close()

    def start(self, run_id: int) -> None:
        with self._lock:
            active = self.active_run_id()
            if active is not None and active != run_id:
                raise RuntimeError(f"Trwa już skan nr {active}. Poczekaj albo go wstrzymaj.")
            if run_id in self._threads and self._threads[run_id].is_alive():
                return
            db = store.connect(self.db_path)
            try:
                row = db.execute("SELECT status FROM scan_runs WHERE id=?", (run_id,)).fetchone()
                if row is None:
                    raise KeyError(run_id)
                if row["status"] not in ("queued", "paused", "running"):
                    raise RuntimeError(f"Skanu w stanie „{row['status']}” nie można uruchomić.")
                db.execute("UPDATE scan_runs SET status='queued' WHERE id=?", (run_id,))
            finally:
                db.close()
            stop = threading.Event()
            self._stops[run_id] = stop
            thread = threading.Thread(target=self._execute, args=(run_id, stop), name=f"scan-{run_id}", daemon=True)
            self._threads[run_id] = thread
            thread.start()

    def pause(self, run_id: int) -> None:
        stop = self._stops.get(run_id)
        thread = self._threads.get(run_id)
        if not stop or not thread or not thread.is_alive():
            raise RuntimeError("Ten skan nie jest uruchomiony.")
        stop.set()

    def wait(self, run_id: int, timeout: float | None = None) -> None:
        thread = self._threads.get(run_id)
        if thread:
            thread.join(timeout)

    def active_run_id(self) -> int | None:
        for run_id, thread in self._threads.items():
            if thread.is_alive():
                return run_id
        return None

    @staticmethod
    def chunk_size(s: dict, hosts_per_second: float) -> int:
        """Subgroup size that takes about `chunk_seconds` at the measured speed."""
        wanted = int(hosts_per_second * float(s["chunk_seconds"]))
        return max(int(s["min_chunk"]), min(int(s["max_chunk"]), wanted))

    @classmethod
    def estimate(cls, addresses: int, s: dict, hosts_per_second: float) -> float:
        """Expected duration: subgroups run `threads` at a time, but never more at once than there are subgroups."""
        size = cls.chunk_size(s, hosts_per_second)
        parallel = max(1, min(int(s["threads"]), -(-addresses // size)))
        return max(0.1, round(addresses / (hosts_per_second * parallel), 1))

    # ---- internals ---------------------------------------------------------------
    def _recover(self) -> None:
        """After a restart, runs that were running are marked paused; their subgroups go back to the queue."""
        db = store.connect(self.db_path)
        try:
            db.execute("UPDATE scan_chunks SET status='pending' WHERE status='running'")
            db.execute("UPDATE scan_runs SET status='paused' WHERE status IN ('queued','running')")
        finally:
            db.close()

    @staticmethod
    def _insert_chunks(db, run_id: int, addresses: list[str], size: int, first_idx: int) -> None:
        db.executemany("INSERT INTO scan_chunks (run_id, idx, targets, size, status) VALUES (?, ?, ?, ?, 'pending')",
                       [(run_id, first_idx + i, json.dumps(part), len(part)) for i, part in enumerate(chunks(addresses, size))])

    def _execute(self, run_id: int, stop: threading.Event) -> None:
        db = store.connect(self.db_path)
        session = time.monotonic()
        try:
            db.execute("UPDATE scan_runs SET status='running', started_at=COALESCE(started_at, ?) WHERE id=?",
                       (store.now(), run_id))
            run = db.execute("SELECT * FROM scan_runs WHERE id=?", (run_id,)).fetchone()
            s = store.settings(db)
            if not run["calibrated"]:
                first = db.execute("SELECT * FROM scan_chunks WHERE run_id=? AND status='pending' ORDER BY idx LIMIT 1",
                                   (run_id,)).fetchone()
                if first is not None:
                    self._run_one(first["id"], s, stop)
                self._recalibrate(db, run_id)
            pending = [r["id"] for r in db.execute(
                "SELECT id FROM scan_chunks WHERE run_id=? AND status='pending' ORDER BY idx", (run_id,))]
            if pending and not stop.is_set():
                with ThreadPoolExecutor(max_workers=max(1, int(s["threads"])), thread_name_prefix=f"scan-{run_id}") as pool:
                    list(pool.map(lambda chunk_id: self._run_one(chunk_id, s, stop), pending))
            self._finish(db, run_id, stop.is_set(), time.monotonic() - session)
        except Exception as error:  # noqa: BLE001 - the run must end in a visible state
            db.execute("UPDATE scan_runs SET status='failed', error=?, finished_at=?, active_seconds=active_seconds+? "
                       "WHERE id=?", (str(error)[:500], store.now(), time.monotonic() - session, run_id))
        finally:
            db.close()

    def _recalibrate(self, db, run_id: int) -> None:
        """After the first subgroup: learn the speed and re-split the rest into right-sized subgroups."""
        s = store.settings(db)
        measured = s.get("measured_rate")
        if not measured:
            return
        size = self.chunk_size(s, measured)
        db.execute("BEGIN IMMEDIATE")
        rows = db.execute("SELECT idx, targets FROM scan_chunks WHERE run_id=? AND status='pending' ORDER BY idx",
                          (run_id,)).fetchall()
        if rows:
            addresses = [a for r in rows for a in json.loads(r["targets"])]
            db.execute("DELETE FROM scan_chunks WHERE run_id=? AND status='pending'", (run_id,))
            self._insert_chunks(db, run_id, addresses, size, rows[0]["idx"])
        total = db.execute("SELECT total_hosts FROM scan_runs WHERE id=?", (run_id,)).fetchone()[0]
        db.execute("UPDATE scan_runs SET calibrated=1, chunk_size=?, estimate_seconds=? WHERE id=?",
                   (size, self.estimate(total, s, measured), run_id))
        db.execute("COMMIT")

    def _run_one(self, chunk_id: int, s: dict, stop: threading.Event) -> None:
        if stop.is_set():
            return
        db = store.connect(self.db_path)
        try:
            chunk = db.execute("SELECT * FROM scan_chunks WHERE id=?", (chunk_id,)).fetchone()
            db.execute("UPDATE scan_chunks SET status='running', attempts=attempts+1, started_at=?, error=NULL WHERE id=?",
                       (store.now(), chunk_id))
            addresses = json.loads(chunk["targets"])
            try:
                hosts, duration = self.run_chunk(addresses, s, stop)
            except nmap.Interrupted:
                db.execute("UPDATE scan_chunks SET status='pending', started_at=NULL WHERE id=?", (chunk_id,))
                return
            except Exception as error:  # noqa: BLE001
                db.execute("UPDATE scan_chunks SET status='failed', finished_at=?, error=? WHERE id=?",
                           (store.now(), str(error)[:500], chunk_id))
                return
            self._store(db, chunk["run_id"], chunk_id, hosts, duration)
            self._learn_speed(db, len(addresses), duration)
        finally:
            db.close()

    def _store(self, db, run_id: int, chunk_id: int, hosts: list, duration: float) -> None:
        seen = store.now()
        seal = self.cipher.seal
        db.execute("BEGIN IMMEDIATE")
        for host in hosts:
            db.execute("INSERT OR REPLACE INTO host_results VALUES (?, ?, ?, ?)", (run_id, host.ip, host.hostname, seen))
            db.execute("INSERT INTO hosts (ip, hostname, first_seen, last_seen, last_run_id) VALUES (?, ?, ?, ?, ?) "
                       "ON CONFLICT(ip) DO UPDATE SET hostname=COALESCE(excluded.hostname, hostname), "
                       "last_seen=excluded.last_seen, last_run_id=excluded.last_run_id",
                       (host.ip, host.hostname, seen, seen, run_id))
            for svc in host.services:
                db.execute("INSERT OR REPLACE INTO services VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                           (run_id, host.ip, svc.port, svc.proto, svc.state, svc.name,
                            seal(svc.product, "services.product"), seal(svc.version, "services.version"),
                            seal(svc.extrainfo, "services.extrainfo")))
        db.execute("UPDATE scan_chunks SET status='done', finished_at=?, duration_seconds=?, hosts_up=? WHERE id=?",
                   (store.now(), round(duration, 3), len(hosts), chunk_id))
        db.execute("COMMIT")

    @staticmethod
    def _learn_speed(db, size: int, duration: float) -> None:
        """Hosts per second of one nmap process, smoothed over finished subgroups."""
        if duration <= 0:
            return
        sample = size / duration
        db.execute("BEGIN IMMEDIATE")
        previous = store.settings(db).get("measured_rate")
        store.save_setting(db, "measured_rate", round(sample if not previous else 0.7 * previous + 0.3 * sample, 4))
        db.execute("COMMIT")

    def _finish(self, db, run_id: int, stopped: bool, session_seconds: float) -> None:
        counts = dict(db.execute("SELECT status, COUNT(*) FROM scan_chunks WHERE run_id=? GROUP BY status",
                                 (run_id,)).fetchall())
        up = db.execute("SELECT COUNT(*) FROM host_results WHERE run_id=?", (run_id,)).fetchone()[0]
        if stopped and counts.get("pending"):
            db.execute("UPDATE scan_runs SET status='paused', pauses=pauses+1, hosts_up=?, "
                       "active_seconds=active_seconds+? WHERE id=?", (up, session_seconds, run_id))
            return
        failed = counts.get("failed", 0)
        db.execute("UPDATE scan_runs SET status='done', finished_at=?, hosts_up=?, active_seconds=active_seconds+?, "
                   "error=? WHERE id=?", (store.now(), up, session_seconds,
                                          f"Nie udało się zeskanować {failed} podgrup." if failed else None, run_id))

"""SQLite storage. One connection per thread; WAL lets the API read while a scan writes."""
import json
import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS targets (
    id INTEGER PRIMARY KEY, kind TEXT NOT NULL, value TEXT NOT NULL UNIQUE, size INTEGER NOT NULL,
    note TEXT, source TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS scan_runs (
    id INTEGER PRIMARY KEY, status TEXT NOT NULL, trigger TEXT NOT NULL, schedule_id INTEGER,
    created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, total_hosts INTEGER NOT NULL,
    calibrated INTEGER NOT NULL DEFAULT 0, chunk_size INTEGER NOT NULL, rate INTEGER NOT NULL,
    hosts_up INTEGER NOT NULL DEFAULT 0, active_seconds REAL NOT NULL DEFAULT 0,
    estimate_seconds REAL, pauses INTEGER NOT NULL DEFAULT 0, error TEXT);
CREATE TABLE IF NOT EXISTS scan_chunks (
    id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE,
    idx INTEGER NOT NULL, targets TEXT NOT NULL, size INTEGER NOT NULL, status TEXT NOT NULL,
    started_at TEXT, finished_at TEXT, duration_seconds REAL, hosts_up INTEGER, attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT, kind TEXT NOT NULL DEFAULT 'scan');
CREATE INDEX IF NOT EXISTS scan_chunks_run ON scan_chunks(run_id, status, idx);
CREATE TABLE IF NOT EXISTS hosts (
    ip TEXT PRIMARY KEY, hostname TEXT, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, last_run_id INTEGER,
    first_run_id INTEGER, state TEXT NOT NULL DEFAULT 'active', missed INTEGER NOT NULL DEFAULT 0, daypart TEXT);
CREATE TABLE IF NOT EXISTS host_results (
    run_id INTEGER NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE, ip TEXT NOT NULL, hostname TEXT,
    seen_at TEXT NOT NULL, PRIMARY KEY (run_id, ip));
CREATE TABLE IF NOT EXISTS services (
    run_id INTEGER NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE, ip TEXT NOT NULL, port INTEGER NOT NULL,
    proto TEXT NOT NULL, state TEXT NOT NULL, name TEXT, product TEXT, version TEXT, extrainfo TEXT, tunnel TEXT,
    PRIMARY KEY (run_id, ip, port, proto));
CREATE TABLE IF NOT EXISTS web (
    run_id INTEGER NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE, ip TEXT NOT NULL, port INTEGER NOT NULL,
    proto TEXT NOT NULL, url TEXT, status INTEGER, title TEXT, server TEXT, error TEXT, data TEXT,
    PRIMARY KEY (run_id, ip, port, proto));
CREATE TABLE IF NOT EXISTS findings (
    id INTEGER PRIMARY KEY, ip TEXT NOT NULL, port INTEGER NOT NULL, proto TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'active', auto TEXT NOT NULL, technologies TEXT NOT NULL DEFAULT '[]',
    tags TEXT NOT NULL DEFAULT '[]', priority INTEGER NOT NULL DEFAULT 1, title TEXT NOT NULL, reasons TEXT NOT NULL DEFAULT '[]',
    note TEXT, status TEXT NOT NULL DEFAULT 'nowe', edited TEXT NOT NULL DEFAULT '[]', edited_at TEXT,
    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, first_run_id INTEGER, last_run_id INTEGER,
    dojo_id INTEGER, dojo_sent_at TEXT, UNIQUE (ip, port, proto));
CREATE TABLE IF NOT EXISTS changes (
    id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE, kind TEXT NOT NULL,
    ip TEXT NOT NULL, port INTEGER, proto TEXT, priority INTEGER NOT NULL, message TEXT NOT NULL, detail TEXT,
    created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS changes_run ON changes(run_id, priority);
CREATE TABLE IF NOT EXISTS host_presence (
    run_id INTEGER NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE, ip TEXT NOT NULL, up INTEGER NOT NULL,
    hour INTEGER NOT NULL, PRIMARY KEY (run_id, ip));
CREATE TABLE IF NOT EXISTS signatures (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, pattern TEXT NOT NULL DEFAULT '', field TEXT,
    category TEXT NOT NULL DEFAULT 'inne', priority INTEGER NOT NULL DEFAULT 1, tags TEXT NOT NULL DEFAULT '[]',
    message TEXT NOT NULL DEFAULT '', status TEXT, enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, role TEXT NOT NULL,
    disabled INTEGER NOT NULL DEFAULT 0, failed_logins INTEGER NOT NULL DEFAULT 0, locked_until TEXT,
    last_login TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL, expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY, at TEXT NOT NULL, username TEXT, action TEXT NOT NULL, target TEXT);
CREATE TABLE IF NOT EXISTS schedules (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, frequency TEXT NOT NULL, window_start TEXT NOT NULL,
    window_end TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, last_started_at TEXT, created_at TEXT NOT NULL);
"""

# Columns sealed by FieldCipher. Banners reveal exact software versions, notes are free text.
# Banners and everything derived from them reveal exact software versions; web data holds
# headers, cookies and page bodies; notes are free text. Priority, tags and states stay plain
# so that lists can be filtered and sorted without the key.
SENSITIVE = {"targets.note", "services.product", "services.version", "services.extrainfo",
             "web.title", "web.server", "web.data",
             "findings.auto", "findings.technologies", "findings.title", "findings.reasons", "findings.note",
             "changes.message", "changes.detail", "settings.dojo_token", "settings.dojo_password"}

# Columns added after stage 1: (table, column, definition). Applied to older databases on start.
MIGRATIONS = [
    ("scan_chunks", "kind", "TEXT NOT NULL DEFAULT 'scan'"),
    ("services", "tunnel", "TEXT"),
    ("hosts", "first_run_id", "INTEGER"),
    ("hosts", "state", "TEXT NOT NULL DEFAULT 'active'"),
    ("hosts", "missed", "INTEGER NOT NULL DEFAULT 0"),
    ("hosts", "daypart", "TEXT"),
]

DEFAULT_SETTINGS = {
    "rate": 300,                 # nmap --max-rate, packets per second
    "threads": 4,                # parallel nmap processes (subgroups at once)
    "ports": "21,22,23,25,53,80,110,143,443,445,1883,3306,3389,5432,5900,6379,8000,8080,8443,9200",
    "host_timeout": 120,         # seconds per host
    "max_retries": 2,
    "chunk_seconds": 60,         # target duration of one subgroup
    "min_chunk": 8,
    "max_chunk": 256,
    "calibration_chunk": 16,     # size of the first subgroup when speed is unknown
    "measured_rate": None,       # hosts per second, learned from finished subgroups
    "http_rate": 10,             # HTTP requests per second during fingerprinting (all threads together)
    "http_timeout": 6,           # seconds per HTTP request
    "unstable_after": 3,         # a host becomes inactive after this many scans in a row without an answer
    "dojo_url": None,            # e.g. http://dojo-nginx:8080 (DefectDojo in docker compose)
    "dojo_user": None,
    "dojo_password": None,       # stored encrypted; or give dojo_token
    "dojo_token": None,          # stored encrypted
    "dojo_product": "AEGIS MESH",
    "dojo_auto": False,          # send new findings with priority >= dojo_min_priority after every scan
    "dojo_min_priority": 4,
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def connect(path: str) -> sqlite3.Connection:
    db = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA busy_timeout=30000")
    return db


def initialize(path: str) -> None:
    db = connect(path)
    try:
        for table, column, definition in MIGRATIONS:
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
            if exists and column not in {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        db.executescript(SCHEMA)
        for key, value in DEFAULT_SETTINGS.items():
            db.execute("INSERT OR IGNORE INTO settings VALUES (?, ?)", (key, json.dumps(value)))
    finally:
        db.close()


def settings(db: sqlite3.Connection) -> dict:
    values = dict(DEFAULT_SETTINGS)
    values.update({row["key"]: json.loads(row["value"]) for row in db.execute("SELECT key, value FROM settings")})
    return values


def save_setting(db: sqlite3.Connection, key: str, value) -> None:
    db.execute("INSERT INTO settings VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (key, json.dumps(value)))

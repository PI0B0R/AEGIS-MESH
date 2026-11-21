"""Import inventory and timestamped Wi-Fi observations in one SQLite transaction."""
import csv
import hashlib
import io
import json
import math
import re
import sqlite3
import uuid
import inspection
from datetime import datetime, timezone
from pathlib import Path

FIELDS = {"inventory": ("ssid", "bssid", "security"),
          "scan": ("ssid", "bssid", "security", "sensor", "at", "lat", "lng")}
MODES = {"OPEN", "WEP", "OWE", "WPA-PERSONAL", "WPA-ENTERPRISE", "WPA2-PERSONAL",
         "WPA3-PERSONAL", "WPA2-ENTERPRISE", "WPA3-ENTERPRISE", "UNKNOWN"}


def initialize(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS wifi_scans (
            id TEXT PRIMARY KEY, session TEXT NOT NULL, ssid TEXT NOT NULL, sensor TEXT NOT NULL,
            at TEXT NOT NULL, lat REAL, lng REAL, location TEXT NOT NULL, browser_hint TEXT,
            report_hash TEXT NOT NULL, unchanged INTEGER NOT NULL, last_changed_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS wifi_scans_series ON wifi_scans(session,ssid,at);
        CREATE TABLE IF NOT EXISTS location_fixes (
            scan_id TEXT PRIMARY KEY REFERENCES wifi_scans(id), session TEXT NOT NULL, sensor TEXT NOT NULL,
            device_time REAL NOT NULL, lat REAL NOT NULL, lng REAL NOT NULL, accuracy REAL NOT NULL,
            location_source TEXT NOT NULL, UNIQUE(session,sensor,device_time));
        CREATE TABLE IF NOT EXISTS place_fixes (
            scan_id TEXT PRIMARY KEY REFERENCES wifi_scans(id), session TEXT NOT NULL, sensor TEXT NOT NULL,
            device_time REAL NOT NULL, building TEXT NOT NULL, floor TEXT NOT NULL, room TEXT NOT NULL,
            UNIQUE(session,sensor,device_time));
        CREATE TABLE IF NOT EXISTS wifi_inventory (
            ssid TEXT, bssid TEXT, security TEXT NOT NULL, PRIMARY KEY(ssid,bssid));
        CREATE TABLE IF NOT EXISTS wifi_own_hotspots (
            ssid TEXT NOT NULL, bssid TEXT NOT NULL, PRIMARY KEY(ssid,bssid));
        CREATE TABLE IF NOT EXISTS wifi_observations (
            id TEXT PRIMARY KEY, ssid TEXT, bssid TEXT, security TEXT,
            sensor TEXT, at TEXT, lat REAL, lng REAL, expected TEXT, incident TEXT);
        CREATE INDEX IF NOT EXISTS wifi_cases ON wifi_observations(incident,at);
        CREATE TABLE IF NOT EXISTS wifi_example_ids (id TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS wifi_example_inventory (
            ssid TEXT, bssid TEXT, security TEXT, PRIMARY KEY(ssid,bssid));
        CREATE VIEW IF NOT EXISTS live_wifi_observations AS
            SELECT * FROM wifi_observations WHERE id NOT IN (SELECT id FROM wifi_example_ids);
        CREATE VIEW IF NOT EXISTS live_wifi_inventory AS
            SELECT * FROM wifi_inventory i WHERE NOT EXISTS (
                SELECT 1 FROM wifi_example_inventory e WHERE e.ssid=i.ssid AND e.bssid=i.bssid AND e.security=i.security);
    """)
    inspection.initialize(db)
    db.execute("BEGIN IMMEDIATE")
    if "location" not in {r[1] for r in db.execute("PRAGMA table_info(wifi_observations)")}:
        db.execute("ALTER TABLE wifi_observations ADD COLUMN location TEXT")
    columns = {r[1] for r in db.execute("PRAGMA table_info(wifi_observations)")}
    for name, typ in (("scan_id", "TEXT REFERENCES wifi_scans(id)"), ("signal_pct", "INTEGER"),
                      ("rssi_dbm", "INTEGER"), ("channel", "INTEGER"), ("frequency_mhz", "INTEGER"),
                      ("capabilities", "TEXT"), ("radio_age_ms", "INTEGER")):
        if name not in columns:
            db.execute(f"ALTER TABLE wifi_observations ADD COLUMN {name} {typ}")
    db.execute("CREATE INDEX IF NOT EXISTS wifi_scan_members ON wifi_observations(scan_id)")
    if not db.execute("SELECT 1 FROM wifi_example_ids LIMIT 1").fetchone():
        folder = Path(__file__).resolve().parent / "static" / "examples"
        ids = [(digest(row),) for name in ("scan-ok.csv", "scan-open.csv")
               for row in parse_csv((folder / name).read_bytes(), "scan")]
        db.executemany("INSERT INTO wifi_example_ids VALUES (?)", ids)
        if db.execute("SELECT 1 FROM wifi_observations JOIN wifi_example_ids USING(id) LIMIT 1").fetchone():
            for item in parse_csv((folder / "inventory.csv").read_bytes(), "inventory"):
                db.execute("INSERT INTO wifi_example_inventory SELECT * FROM wifi_inventory "
                           "WHERE ssid=? AND bssid=? AND security=?", item)


def parse_csv(content, kind):
    text = content.decode("utf-8-sig")
    first = text.partition("\n")[0]
    reader = csv.DictReader(io.StringIO(text, newline=""), strict=True,
                            delimiter=";" if ";" in first else ",")
    fields = FIELDS[kind]
    extended = kind=="inventory" and set(reader.fieldnames or [])=={"ssid","bssid","zone","expected_security"}
    if extended:
        fields=("ssid","bssid","zone","expected_security")
    if not reader.fieldnames or set(reader.fieldnames) != set(fields) or len(reader.fieldnames) != len(fields):
        raise ValueError("Wymagane nagłówki: " + ", ".join(fields))
    parsed = []
    inventory = {}
    for row in reader:
        try:
            if None in row or any(row[k] is None for k in fields):
                raise ValueError("niepoprawna liczba pól")
            ssid = row["ssid"]
            bssid = row["bssid"].strip().lower().replace("-", ":")
            security = row["expected_security" if extended else "security"].strip().upper()
            if not ssid or len(ssid.encode()) > 32 or not re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", bssid):
                raise ValueError("sprawdź SSID i BSSID")
            if security not in (MODES | ({"802.1X"} if kind=="inventory" else set())) or (kind == "inventory" and security == "UNKNOWN"):
                raise ValueError("nieobsługiwany tryb zabezpieczeń")
            values = (ssid, bssid, security)
            if kind == "inventory":
                if extended:
                    values += (inspection.zone(row["zone"]),)
                if inventory.setdefault((ssid,bssid), values) != values:
                    raise ValueError("sprzeczne zabezpieczenia dla jednego punktu")
            else:
                sensor = row["sensor"].strip()
                when = datetime.fromisoformat(row["at"].strip().replace("Z", "+00:00"))
                if not sensor or len(sensor) > 120 or when.tzinfo is None:
                    raise ValueError("podaj czujnik i czas ze strefą")
                lat, lng = float(row["lat"]), float(row["lng"])
                if not (math.isfinite(lat) and -85 <= lat <= 85 and math.isfinite(lng) and -180 <= lng <= 180):
                    raise ValueError("niepoprawne współrzędne")
                values += (sensor, when.astimezone(timezone.utc).isoformat(timespec="microseconds"), lat, lng)
            parsed.append(values)
        except (ValueError, OverflowError) as error:
            raise ValueError(f"Wiersz {reader.line_num}: {error}") from error
    if not parsed:
        raise ValueError("Plik nie zawiera danych.")
    return parsed


def digest(values):
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()


def set_own_hotspot(db, data):
    if not isinstance(data, dict) or not isinstance(data.get("ssid"), str) or not isinstance(data.get("bssid"), str):
        raise ValueError("Wybierz sygnał z historii odczytów.")
    key = (data["ssid"], data["bssid"].lower())
    db.execute("BEGIN IMMEDIATE")
    if not db.execute("SELECT 1 FROM live_wifi_observations WHERE ssid=? AND bssid=? LIMIT 1", key).fetchone():
        raise ValueError("Brak odczytu dla tej nazwy i identyfikatora punktu.")
    if data.get("confirmed") is True:
        db.execute("INSERT OR IGNORE INTO wifi_own_hotspots VALUES (?,?)", key)
    elif data.get("confirmed") is False:
        db.execute("DELETE FROM wifi_own_hotspots WHERE ssid=? AND bssid=?", key)
    else:
        raise ValueError("Wybierz potwierdzenie lub cofnięcie oznaczenia.")


def signal_category(key, hotspots, inventory):
    if key in hotspots:
        return "own_hotspot"
    if key in inventory:
        return "inventory"
    words = re.findall(r"[^\W_]+", key[0].casefold())
    if {"iphone", "hotspot", "galaxy", "pixel", "androidap", "redmi", "poco"}.intersection(words):
        return "possible_hotspot"
    return "unknown"


def import_file(db, content, kind):
    rows = parse_csv(content, kind)
    db.execute("BEGIN IMMEDIATE")
    if kind == "inventory":
        db.executemany("DELETE FROM wifi_example_inventory WHERE ssid=? AND bssid=?", (row[:2] for row in rows))
        db.executemany("INSERT INTO wifi_inventory VALUES (?,?,?) ON CONFLICT(ssid,bssid) "
                       "DO UPDATE SET security=excluded.security", (r[:3] for r in rows))
        db.executemany("INSERT INTO inventory_zones VALUES (?,?,?) ON CONFLICT(ssid,bssid) DO UPDATE SET zone=excluded.zone",
                       ((r[0],r[1],r[3]) for r in rows if len(r)==4))
        return {"points": db.execute("SELECT count(*) FROM live_wifi_inventory").fetchone()[0]}
    examples = {item[0] for item in db.execute("SELECT id FROM wifi_example_ids")}
    if any(digest(row) in examples for row in rows):
        raise ValueError("Plik zawiera odczyty z archiwalnego przykładu. Wczytaj własne obserwacje.")
    return save_observations(db, rows, require_inventory=True, location={"source": "csv"})


def save_observations(db, rows, require_inventory=False, location=None, scan_id=None, radio=None, default_policy=None):
    policies = {}
    for ssid, bssid, security in db.execute("SELECT * FROM live_wifi_inventory"):
        policies.setdefault(ssid, {})[bssid] = security
    if require_inventory and not policies:
        raise ValueError("Najpierw wczytaj inwentarz.")
    existing = {r[0] for r in db.execute("SELECT DISTINCT incident FROM live_wifi_observations WHERE incident IS NOT NULL")}
    added = new_cases = 0
    for row in rows:
        ssid, bssid, seen = row[:3]
        points = policies.get(ssid, {})
        expected = {points[bssid]} if bssid in points else set(points.values())
        if not expected and default_policy:
            expected = default_policy.get(ssid, set())
        matches = seen in expected or ("802.1X" in expected and seen.endswith("-ENTERPRISE"))
        mismatch = bool(expected) and seen != "UNKNOWN" and not matches
        unknown_bssid = bool(points) and bssid not in points
        reason = "new_bssid_security" if mismatch and unknown_bssid else "security" if mismatch else "new_bssid"
        case = digest(row[:3]) if mismatch or unknown_bssid else None
        pct, channel = radio[bssid][3:] if radio else (None, None)
        identifier = digest((scan_id, *row)) if scan_id else digest(row)
        inserted = db.execute("INSERT OR IGNORE INTO wifi_observations "
                              "(id,ssid,bssid,security,sensor,at,lat,lng,expected,incident,location,scan_id,signal_pct,rssi_dbm,channel) "
                              "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                              (identifier, *row, ", ".join(sorted(expected)), case, json.dumps(location), scan_id,
                               pct, int(pct / 2 - 100) if pct is not None else None, channel)).rowcount
        if inserted and case:
            db.execute("INSERT INTO radio_findings VALUES (?,?)",(identifier,reason))
        added += inserted
        if inserted and case and case not in existing:
            new_cases += 1
            existing.add(case)
    return {"added": added, "duplicates": len(rows)-added, "new_cases": new_cases}


def save_scan(db, session, ssid, points, at, point, hint=None, zone="", sensor="Laptop · sesja WWW", default_policy=None, radio_signature=None):
    zone=inspection.zone(zone)
    scan_id = str(uuid.uuid4())
    # A filtered report may mention a BSSID on multiple interfaces.
    unique = {}
    for item in points:
        previous = unique.get(item[1])
        if previous is None or (item[3] if item[3] is not None else -1) > (previous[3] if previous[3] is not None else -1):
            unique[item[1]] = item
    report_hash = digest(sorted(unique.values(), key=lambda item: item[1]) if radio_signature is None else radio_signature)
    previous = db.execute("SELECT report_hash,last_changed_at FROM wifi_scans WHERE session=? AND ssid=? "
                          "ORDER BY rowid DESC LIMIT 1", (session, ssid)).fetchone()
    unchanged = bool(previous and previous[0] == report_hash)
    changed = previous[1] if unchanged else at
    last_sensor = db.execute("SELECT report_hash,last_changed_at FROM wifi_scans WHERE sensor=? AND ssid=? ORDER BY at DESC,rowid DESC LIMIT 1",(sensor,ssid)).fetchone()
    radio_changed = not last_sensor or last_sensor[0]!=report_hash
    if not radio_changed:
        changed=last_sensor[1]
    db.execute("INSERT INTO wifi_scans VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
               (scan_id, session, ssid, sensor, at, point.get("lat"), point.get("lng"),
                json.dumps(point["location"]), json.dumps(hint) if hint else None, report_hash, unchanged, changed))
    db.execute("INSERT INTO room_scans VALUES (?,?,?)",(scan_id,zone,int(radio_changed)))
    rows = [(*item[:3], sensor, at, None, None) for item in unique.values()]
    result = save_observations(db, rows, scan_id=scan_id, radio=unique, default_policy=default_policy)
    return dict(result, id=scan_id, at=at, unchanged=unchanged, last_changed_at=changed, **point)


def state(db, project=None):
    db.row_factory = sqlite3.Row
    incidents = {}
    readings = []
    hotspots = {tuple(row) for row in db.execute("SELECT ssid,bssid FROM wifi_own_hotspots")}
    inventory = {tuple(row) for row in db.execute("SELECT ssid,bssid FROM live_wifi_inventory")}
    own_names = {ssid for ssid, _ in hotspots}
    reasons = dict(db.execute("SELECT observation_id,reason FROM radio_findings"))
    scans = {}
    for raw in db.execute("SELECT id,session,ssid,sensor,at,lat,lng,location,unchanged,last_changed_at FROM wifi_scans ORDER BY at DESC"):
        scan = dict(raw)
        scan["location"] = json.loads(scan["location"])
        scans[scan["id"]] = scan
    if project:
        project(db, list(scans.values()), "scan")
    for row in db.execute("""SELECT id,scan_id,signal_pct,rssi_dbm,channel,frequency_mhz,radio_age_ms,incident,ssid,bssid,security,sensor,at,lat,lng,expected,location
                             FROM live_wifi_observations ORDER BY at DESC, id"""):
        reading = dict(row)
        reading["category"] = signal_category((row["ssid"], row["bssid"]), hotspots, inventory)
        reading["possibleRotation"] = (reading["category"] not in ("own_hotspot","inventory")
                                       and row["ssid"] in own_names and row["rssi_dbm"] is not None
                                       and row["rssi_dbm"] >= -55)
        reading["location"] = (json.loads(row["location"] or "null")
                               or {"source": "unknown" if row["lat"] is not None else "none"})
        if row["scan_id"] in scans:
            scan = scans[row["scan_id"]]
            reading.update({k: scan[k] for k in ("lat", "lng", "location")})
        readings.append(reading)
    if project:
        project(db, [r for r in readings if not r["scan_id"]], "wifi")
    for reading in readings:
        incident = reading.pop("incident")
        reading["reason"]=reasons.get(reading["id"])
        reading["mismatch"] = incident is not None and reading["reason"]!="new_bssid"
        if not incident:
            continue
        case = incidents.setdefault(incident, {"reason":reading["reason"], "ssid": reading["ssid"], "bssid": reading["bssid"],
                                    "security": reading["security"], "observations": []})
        case["observations"].append({k: reading[k] for k in ("sensor","at","lat","lng","expected","location")})
    return {"inventory": db.execute("SELECT count(*) FROM live_wifi_inventory").fetchone()[0],
            "scans": list(scans.values()), "observations": len(readings), "readings": readings,
            "cases": list(incidents.values())}

"""Project records onto the selected working area without rewriting their evidence."""
import json
import math
from pathlib import Path

from position import metadata, valid_point

CITIES = {row["name"]: row for row in json.loads(
    (Path(__file__).parent / "static" / "miasta.json").read_text(encoding="utf-8-sig"))}
RADIUS_M = 25000


def initialize(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS work_area (id INTEGER PRIMARY KEY CHECK (id=1), city TEXT);
        CREATE TABLE IF NOT EXISTS position_corrections (record_key TEXT PRIMARY KEY, point TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS correction_history(id INTEGER PRIMARY KEY,record_key TEXT NOT NULL,point TEXT NOT NULL,at TEXT NOT NULL);
        CREATE TRIGGER IF NOT EXISTS correction_history_insert AFTER INSERT ON position_corrections
        BEGIN INSERT INTO correction_history(record_key,point,at) VALUES (NEW.record_key,NEW.point,strftime('%Y-%m-%dT%H:%M:%fZ','now')); END;
    """)


def selected(db):
    row = db.execute("SELECT city FROM work_area WHERE id=1").fetchone()
    return CITIES.get(row[0]) if row else None


def outside(city, lat, lng):
    if not city or not valid_point(lat, lng):
        return False
    a, b = map(math.radians, (lat, city["lat"]))
    chord = math.sin((b-a)/2)**2 + math.cos(a)*math.cos(b)*math.sin(math.radians(lng-city["lng"])/2)**2
    return 12742000*math.asin(math.sqrt(min(1, chord))) > RADIUS_M


def project(db, rows, kind):
    city = selected(db)
    edits = dict(db.execute("SELECT record_key,point FROM position_corrections WHERE record_key LIKE ?", (kind+":%",)))
    for row in rows:
        key = kind + ":" + row["id"]
        row["recordKey"] = key
        if key in edits:
            row.update(json.loads(edits[key]))
        if valid_point(row["lat"], row["lng"]) and (outside(city, row["lat"], row["lng"]) or
                row["location"].get("source") in ("browser", "unknown")):
            row["unverifiedPoint"] = {"lat":row["lat"], "lng":row["lng"], "location":row["location"]}
        if outside(city, row["lat"], row["lng"]):
            row.update(lat=None, lng=None, location={"source":"none","reason":"outside_city","city":city["name"]})
        elif "unverifiedPoint" in row:
            row.update(lat=None, lng=None, location={"source":"none","reason":"unverified_history"})
    return rows


def correct(db, data):
    db.execute("BEGIN IMMEDIATE")
    city = selected(db)
    keys = data["keys"]
    if not city or data["city"] != city["name"] or not isinstance(keys,list) or not 0 < len(keys) <= 5000:
        raise ValueError("Sprawdź wybrany obszar i zakres korekty.")
    lat, lng = data["lat"], data["lng"]
    info = metadata(data)
    if info["source"] != "manual" or outside(city, lat, lng):
        raise ValueError("Wskaż rzeczywiste stanowisko w wybranym obszarze.")
    info["corrected"] = True
    point = json.dumps({"lat":lat, "lng":lng, "location":info})
    for key in set(keys):
        kind, _, identifier = key.partition(":")
        if kind == "measurement":
            row = db.execute("SELECT record FROM measurements WHERE id=?", (identifier,)).fetchone()
            original = json.loads(row[0]) if row else {}
        elif kind == "scan":
            row = db.execute("SELECT lat,lng FROM wifi_scans WHERE id=?", (identifier,)).fetchone()
            original = {"lat":row[0],"lng":row[1]} if row else {}
        elif kind == "wifi":
            row = db.execute("SELECT lat,lng FROM live_wifi_observations WHERE id=?", (identifier,)).fetchone()
            original = {"lat":row[0],"lng":row[1]} if row else {}
        else:
            raise ValueError("Nieznany rodzaj wpisu.")
        existing = db.execute("SELECT point FROM position_corrections WHERE record_key=?", (key,)).fetchone()
        if existing:
            original = json.loads(existing[0])
            if existing[0] == point:
                continue
        if not outside(city,original.get("lat"),original.get("lng")):
            raise ValueError("Zakres korekty się zmienił. Odśwież historię.")
        db.execute("INSERT OR REPLACE INTO position_corrections VALUES (?,?)",(key,point))
    return {"count":len(set(keys))}

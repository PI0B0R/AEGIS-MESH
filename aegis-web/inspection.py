"""Indoor inspection evidence and operator decisions. Zones refer to sensor positions."""
from datetime import datetime, timezone

ZONES = ("A", "B", "C", "D")
FRESH_SECONDS = 90
TIE_DB = 4
OUTCOMES = ("Zatwierdzona wymiana sprzętu", "Urządzenie przekazane do wyjaśnienia", "Rozbieżność usunięta")


def zone(value):
    if not isinstance(value,str):
        raise ValueError("Wybierz strefę A–D.")
    code = value.strip().upper().removeprefix("STREFA ").strip()
    if code and code not in ZONES:
        raise ValueError("Wybierz strefę A–D.")
    return code


def initialize(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS inventory_zones(ssid TEXT,bssid TEXT,zone TEXT,PRIMARY KEY(ssid,bssid));
        CREATE TABLE IF NOT EXISTS room_scans(scan_id TEXT PRIMARY KEY REFERENCES wifi_scans(id),zone TEXT NOT NULL, changed INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS radio_findings(observation_id TEXT PRIMARY KEY,reason TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS inspection_log(id INTEGER PRIMARY KEY,incident TEXT NOT NULL,at TEXT NOT NULL,outcome TEXT NOT NULL,note TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS rejected_phone_fixes(id INTEGER PRIMARY KEY,received TEXT NOT NULL,reason TEXT NOT NULL,lat REAL,lng REAL,accuracy REAL,device_time REAL);
        CREATE INDEX IF NOT EXISTS wifi_sensor_reports ON wifi_scans(sensor,ssid,at);
    """)


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def decide(db, data):
    incident, outcome, note = data.get("incident"),data.get("outcome"),data.get("note","")
    if outcome not in OUTCOMES or not isinstance(note,str) or len(note)>1000 or not isinstance(incident,str):
        raise ValueError("Wybierz wynik oględzin i wpisz do 1000 znaków.")
    row = db.execute("SELECT MAX(at) FROM live_wifi_observations WHERE incident=?",(incident,)).fetchone()
    if not row[0]:
        raise ValueError("Nie znaleziono sprawy.")
    last = db.execute("SELECT at FROM inspection_log WHERE incident=? ORDER BY id DESC LIMIT 1",(incident,)).fetchone()
    if last and last[0]>=row[0]:
        raise ValueError("Oględziny tego zdarzenia są już zapisane.")
    db.execute("INSERT INTO inspection_log(incident,at,outcome,note) VALUES (?,?,?,?)",(incident,stamp(),outcome,note.strip()))


def snapshot(db, now=None):
    now = now or datetime.now(timezone.utc)
    def recent(at):
        return 0 <= (now-datetime.fromisoformat(at)).total_seconds() <= FRESH_SECONDS
    sensors = {}
    latest = {}
    for sid,sensor,ssid,at,z,changed,changed_at in db.execute("""
        SELECT s.id,s.sensor,s.ssid,s.at,coalesce(r.zone,''),r.changed,s.last_changed_at
        FROM wifi_scans s LEFT JOIN room_scans r ON r.scan_id=s.id ORDER BY s.at DESC,s.rowid DESC"""):
        sensors.setdefault((sensor,ssid),dict(sensor=sensor,ssid=ssid,at=at,zone=z,online=recent(at),changedAt=changed_at))
        if z and (sensor,ssid,z) not in latest:
            latest[sensor,ssid,z] = (sid,bool(changed) and recent(at))
    usable = {sid:k[2] for k,(sid,ok) in latest.items() if ok}
    cases = {}
    for key,ssid,bssid,mode,expected,sensor,at,sid,dbm,reason in db.execute("""
        SELECT o.incident,o.ssid,o.bssid,o.security,o.expected,o.sensor,o.at,o.scan_id,o.rssi_dbm,f.reason
        FROM live_wifi_observations o LEFT JOIN radio_findings f ON f.observation_id=o.id
        WHERE o.incident IS NOT NULL ORDER BY o.at DESC"""):
        item = cases.setdefault(key,dict(id=key,ssid=ssid,bssid=bssid,security=mode,expected=expected,
            reason=reason or "security",at=at,sensors=[],strength={},log=[]))
        if sensor not in item["sensors"]:
            item["sensors"].append(sensor)
        if sid in usable and dbm is not None:
            z=usable[sid]
            item["strength"][z]=max(dbm,item["strength"].get(z,-200))
    for key,at,outcome,note in db.execute("SELECT incident,at,outcome,note FROM inspection_log ORDER BY id DESC"):
        if key in cases:
            cases[key]["log"].append(dict(at=at,outcome=outcome,note=note))
    for item in cases.values():
        levels=item["strength"]
        ranked=sorted(levels,key=levels.get,reverse=True)
        zones=[]
        if len(ranked)>=2:
            tied=[z for z in ranked if levels[ranked[0]]-levels[z]<=TIE_DB]
            if len(tied)==1 or (len(tied)==2 and abs(ZONES.index(tied[0])-ZONES.index(tied[1])) in (1,3)):
                zones=tied
        item["zones"]=zones
        item["closed"]=bool(item["log"] and item["log"][0]["at"]>=item["at"])
    changes=[dict(ssid=ssid,bssid=bssid,at=at) for ssid,bssid,at in db.execute(
        "SELECT ssid,bssid,MIN(at) first FROM live_wifi_observations GROUP BY ssid,bssid ORDER BY first DESC LIMIT 25")]
    inventory=[dict(ssid=s,bssid=b,zone=z,security=m) for s,b,m,z in db.execute(
        "SELECT i.ssid,i.bssid,i.security,z.zone FROM live_wifi_inventory i LEFT JOIN inventory_zones z USING(ssid,bssid) ORDER BY i.ssid,i.bssid")]
    return dict(cases=sorted(cases.values(),key=lambda c:(c["closed"],c["reason"]=="new_bssid")),
                sensors=list(sensors.values()),changes=changes,inventory=inventory,outcomes=OUTCOMES)

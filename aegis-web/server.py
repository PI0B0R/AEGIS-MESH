"""Local AEGIS-MESH web prototype. Run with Python 3.10+."""
import argparse
import atexit
import csv
import json
import os
import sqlite3
import socket
import phone_pairing
from mobile_console import MobileConsole
import subprocess
import time
import uuid
import threading
from contextlib import closing
from datetime import datetime, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
import wifi
import demo_lab
import inspection
import room_demo
import tempfile
import work_area
from position import metadata, valid_point
from export_wifi import connected_ssids, read_network

ROOT = Path(__file__).resolve().parent
DEMO_LOCK = threading.Lock()


PHONE_LOCK = threading.Lock()


def lan_addresses():
    addresses = set()
    try:
        addresses.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    # Linux and macOS often resolve the hostname to 127.0.1.1 only; ask the routing table instead.
    # A UDP "connect" sends no packet, it only picks the outgoing interface.
    for target in ("192.168.0.1", "10.0.0.1", "8.8.8.8"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.connect((target, 9))
                addresses.add(probe.getsockname()[0])
        except OSError:
            pass
    return sorted(addr for addr in addresses if phone_pairing.private_ip(addr))


def preferred_address(addresses):
    """Home and lab Wi-Fi is usually 192.168.x.x, then 10.x, then 172.16-31.x."""
    order = lambda a: (0 if a.startswith("192.168.") else 1 if a.startswith("10.") else 2, a)
    return sorted(addresses, key=order)[0]


def quick_pairing(server, renew=False):
    """One QR, no choices: LAN address picked automatically, HTTPS pinned by fingerprint in the QR."""
    addresses = lan_addresses()
    if not addresses:
        raise ValueError("Laptop nie ma adresu w sieci lokalnej. Połącz go z tą samą siecią Wi-Fi co telefon.")
    address = preferred_address(addresses)
    beacon = server.beacon
    if beacon and (getattr(beacon, "mode", None) != "quick" or beacon.address != address):
        phone_pairing.close_beacon(beacon)
        server.beacon = server.phone_ca = beacon = None
    if not beacon:
        try:
            beacon, server.phone_ca = phone_pairing.listen(address, 8767, server.database)
        except (OSError, subprocess.SubprocessError):
            # No OpenSSL on this laptop: plain HTTP in the LAN (accepted by the debug APK only).
            beacon, server.phone_ca = phone_pairing.listen(address, 8767, server.database, cleartext=True)
        beacon.mode, beacon.address = "quick", address
        server.beacon = beacon
    phone = beacon.phone
    with phone.lock:
        live = phone.invite and time.monotonic() - phone.started <= phone.invite_seconds
        if renew or (not phone.key and not live):
            phone.start(invite_seconds=600, lifetime=12 * 3600)
            live = True
        result = {"paired": bool(phone.key), "address": address, "secure": phone.url.startswith("https"),
                  "session": phone.session, "seen": round(time.time() - phone.seen) if phone.seen else None}
        if live and phone.invite:
            result["qr"] = phone.url + "/phone-beacon#t=" + phone.invite + ("&fp=" + phone.fingerprint if phone.fingerprint else "")
    return result


def sensor_point(db, data, phone=None):
    hint = data.get("browserHint")
    if data.get("location", {}).get("source") == "browser":
        hint = data
    diagnostic = None
    if hint:
        info = metadata(hint)
        if info["source"] != "browser":
            raise ValueError("Niepoprawna diagnostyka przeglądarki.")
        diagnostic = {"lat":hint["lat"], "lng":hint["lng"], "location":info}
    info = metadata(data)
    point = {"lat":None, "lng":None, "location":{"source":"none"}}
    city = work_area.selected(db)
    if info["source"] == "manual":
        point.update(lat=data["lat"], lng=data["lng"], location=info)
    elif data.get("phoneSession"):
        phone_fix = phone.status(data["phoneSession"]) if phone else {}
        point.update({k:phone_fix[k] for k in ("lat","lng","location") if k in phone_fix})
        if phone_fix.get("reason"):
            point["location"] = {"source":"none","reason":phone_fix["reason"]}
    elif diagnostic:
        point["location"]["reason"] = "outside_city" if work_area.outside(city, diagnostic["lat"], diagnostic["lng"]) else "unconfirmed"
    elif info["source"] == "none":
        point["location"] = info
    if work_area.outside(city, point["lat"], point["lng"]):
        point = {"lat":None,"lng":None,"location":{"source":"none","reason":"outside_city"}}
    if point["location"].get("reason") == "outside_city":
        point["location"]["city"] = city["name"] if city else ""
    return point, diagnostic


class Handler(SimpleHTTPRequestHandler):
    def local_request(self):
        return (self.client_address[0] in ("127.0.0.1", "::1")
                and self.headers.get("Host") in {f"127.0.0.1:{self.server.server_port}",
                                                f"localhost:{self.server.server_port}"})

    def local_radio(self):
        return os.name == "nt" and self.local_request()

    def reply(self, data, status=200):
        payload = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/probe":
            self.reply({"time": datetime.now(timezone.utc).isoformat()})
        elif path == "/api/capabilities":
            self.reply({"localWifi": self.local_radio(), "onlinePairing": self.local_request(), "phoneAddresses": lan_addresses() if self.local_request() else []})
        elif path == "/api/phone/ca" and self.local_request() and self.server.phone_ca:
            payload = self.server.phone_ca.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type","application/x-x509-ca-cert")
            self.send_header("Content-Disposition",'attachment; filename="AEGIS-phone-ca.crt"')
            self.send_header("Content-Length",str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        elif path == "/api/work-area":
            with closing(sqlite3.connect(self.server.database)) as db, db:
                self.reply({"city":work_area.selected(db), "radius":work_area.RADIUS_M})
        elif path == "/api/wifi/connection":
            if not self.local_radio() or self.headers.get("X-Aegis-Session") != "1":
                return self.reply({"error": "Odczyt SSID jest dostępny na lokalnym komputerze z Windows."}, 403)
            try:
                names = connected_ssids()
            except (OSError, subprocess.TimeoutExpired, ValueError):
                return self.reply({"error": "Windows nie udostępnił nazwy sieci. Możesz wpisać SSID ręcznie."}, 503)
            self.reply({"ssids": names})
        elif path == "/api/measurements":
            with closing(sqlite3.connect(self.server.database)) as db, db:
                rows = db.execute("SELECT record FROM measurements ORDER BY rowid DESC LIMIT 500").fetchall()
            records = [json.loads(row[0]) for row in rows]
            for item in records:
                item.setdefault("location", {"source": "unknown" if item.get("lat") is not None else "none"})
            with closing(sqlite3.connect(self.server.database)) as db, db:
                self.reply(work_area.project(db, records, "measurement"))
        elif path == "/api/room":
            with closing(sqlite3.connect(self.server.database)) as db, db:
                result = inspection.snapshot(db)
                result.update(demo=getattr(self.server,"room_demo",False),canReview=self.local_request())
                self.reply(result)
        elif path == "/api/wifi":
            with closing(sqlite3.connect(self.server.database)) as db, db:
                result = wifi.state(db,work_area.project)
                result["canClassify"] = self.local_request()
                self.reply(result)
        elif path == "/api/demo":
            with closing(sqlite3.connect(self.server.database)) as db, db:
                self.reply(demo_lab.last_run(db))
        elif path.startswith("/api/"):
            self.reply({"error": "Nieznana ścieżka"}, 404)
        else:
            super().do_GET()

    def list_directory(self, path):
        self.send_error(404)

    def do_POST(self):
        if self.path == "/api/station-radio":
            if not self.local_radio() or self.headers.get("X-Aegis-Session")!="1":
                return self.reply({"error":"Sterowanie wymaga lokalnego panelu na laptopie z Windows."},403)
            if self.server.room_demo:
                return self.reply({"error":"W pokazie dla jury działają czujniki przykładowe."},403)
            try:
                size=int(self.headers.get("Content-Length",0))
                if self.headers.get_content_type()!="application/json" or not 0<size<=512:
                    raise ValueError("Niepoprawne dane stanowiska.")
                data=json.loads(self.rfile.read(size))
                action=data["action"]
                radio=self.server.station_radio
                if action=="start":
                    radio.start({"ssid":data.get("ssid","AGH-LAB"),"zone":data.get("zone","")},None)
                elif action=="stop":
                    radio.stop()
                elif action!="status":
                    raise ValueError("Nieznana operacja stanowiska.")
                return self.reply(radio.status())
            except (ValueError,KeyError,TypeError):
                return self.reply({"error":"Sprawdź polecenie, nazwę sieci i strefę."},400)
        if self.path in ("/api/room/review","/api/room/simulate"):
            if not self.local_request() or self.headers.get("X-Aegis-Session")!="1":
                return self.reply({"error":"Wymagany lokalny panel laptopa."},403)
            try:
                size=int(self.headers.get("Content-Length",0))
                if self.headers.get_content_type()!="application/json" or not 0<size<=8192:
                    raise ValueError("Niepoprawne dane oględzin.")
                data=json.loads(self.rfile.read(size))
                with closing(sqlite3.connect(self.server.database)) as db, db:
                    if self.path.endswith("/simulate"):
                        if not getattr(self.server,"room_demo",False):
                            return self.reply({"error":"Symulacja dostępna tylko w osobnym pokazie."},403)
                        room_demo.run(db,True)
                    else:
                        db.execute("BEGIN IMMEDIATE")
                        inspection.decide(db,data)
                return self.reply({"saved":True})
            except (ValueError,TypeError,AttributeError) as error:
                return self.reply({"error":str(error)},400)
        if self.path in ("/api/phone/start","/api/phone/status","/api/phone/stop","/api/phone/control","/api/phone/quick"):
            if not self.local_request() or self.headers.get("X-Aegis-Session") != "1":
                return self.reply({"error":"Parowanie jest dostępne w lokalnym panelu laptopa."},403)
            try:
                size = int(self.headers.get("Content-Length",0))
                if self.headers.get_content_type() != "application/json" or not 0 < size <= 1024:
                    raise ValueError("Niepoprawne dane parowania.")
                data = json.loads(self.rfile.read(size))
                with PHONE_LOCK:
                    if self.path.endswith("/start"):
                        host = data["host"]
                        online=host in ("online","cloudflare")
                        address = host.removeprefix("http://")
                        if (not online and address not in lan_addresses()) or data.get("together") is not True:
                            raise ValueError("Wybierz adres laptopa i potwierdź obecność telefonu.")
                        if self.server.beacon and getattr(self.server.beacon,"mode",None)!=host:
                            phone_pairing.close_beacon(self.server.beacon)
                            self.server.beacon = None
                        if not self.server.beacon:
                            self.server.beacon,self.server.phone_ca = (phone_pairing.listen_cloudflare(self.server.database) if host=="cloudflare" else phone_pairing.listen_online(self.server.database) if online else phone_pairing.listen(address, 8767, self.server.database, cleartext=address!=host))
                            self.server.beacon.mode=host
                        self.server.beacon.console.stop()
                        result = self.server.beacon.phone.start(invite_seconds=900 if online else 120)
                    elif self.path.endswith("/quick"):
                        result = quick_pairing(self.server, bool(data.get("new")))
                    elif self.path.endswith("/control"):
                        result={}
                        if self.server.beacon:
                            phone=self.server.beacon.phone
                            with phone.lock:
                                result={"session":phone.session,"mode":self.server.beacon.mode}
                                if phone.invite and time.monotonic()-phone.started<=phone.invite_seconds:
                                    result.update(url=phone.url+"/phone-beacon#"+phone.invite,pin=phone.pin)
                    elif self.path.endswith("/status"):
                        result = self.server.beacon.phone.status(data.get("session")) if self.server.beacon else {}
                    else:
                        if self.server.beacon and self.server.beacon.phone.stop(data.get("session")):
                            phone_pairing.close_beacon(self.server.beacon)
                            self.server.beacon = self.server.phone_ca = None
                        result = {"stopped":True}
                return self.reply(result)
            except (ValueError,TypeError,KeyError,AttributeError) as error:
                return self.reply({"error":str(error)},400)
            except (OSError,subprocess.SubprocessError):
                return self.reply({"error":"Nie uruchomiono połączenia. Sprawdź wybrany program tunelu lub lokalny HTTPS. Sprawdź adres, port 8767 oraz dostępność OpenSSL (np. z Git for Windows)."},503)
        if self.path == "/api/wifi/own-hotspot":
            if not self.local_request() or self.headers.get("X-Aegis-Session") != "1":
                return self.reply({"error":"Potwierdź własny hotspot w lokalnym panelu laptopa."},403)
            try:
                size = int(self.headers.get("Content-Length", 0))
                if self.headers.get_content_type() != "application/json" or not 0 < size < 1024:
                    raise ValueError("Niepoprawne oznaczenie.")
                with closing(sqlite3.connect(self.server.database)) as db, db:
                    wifi.set_own_hotspot(db,json.loads(self.rfile.read(size)))
            except ValueError as error:
                return self.reply({"error":str(error)},400)
            return self.reply({"saved":True})
        if self.path in ("/api/work-area","/api/location-corrections"):
            if self.headers.get("X-Aegis-Area") != "1" or self.headers.get_content_type() != "application/json":
                return self.reply({"error":"Oczekiwano żądania z panelu pomiarów."},403)
            try:
                size=int(self.headers.get("Content-Length",0))
                if not 0<size<=512000:
                    raise ValueError("Niepoprawny rozmiar korekty.")
                data=json.loads(self.rfile.read(size))
                with closing(sqlite3.connect(self.server.database)) as db, db:
                    if self.path == "/api/work-area":
                        city=data["city"]
                        if city is not None and city not in work_area.CITIES:
                            raise ValueError("Wybierz miasto z listy.")
                        db.execute("INSERT OR REPLACE INTO work_area VALUES (1,?)",(city,))
                        result={"city":work_area.selected(db),"radius":work_area.RADIUS_M}
                    else:
                        result=work_area.correct(db,data)
                return self.reply(result)
            except (TypeError,KeyError,ValueError,AttributeError):
                return self.reply({"error":"Sprawdź zakres czasu, miasto i pozycję stanowiska. Odśwież dane przed ponowieniem."},400)
        if self.path == "/api/demo":
            if self.headers.get("X-Aegis-Demo") != "1" or self.headers.get_content_type() != "application/json":
                return self.reply({"error": "Uruchom pokaz z panelu demo."}, 400)
            if not DEMO_LOCK.acquire(blocking=False):
                return self.reply({"error": "Pokaz jest już uruchomiony. Spróbuj ponownie za chwilę."}, 409)
            try:
                size = int(self.headers.get("Content-Length", 0))
                if not 0 < size < 256:
                    raise ValueError("Niepoprawna konfiguracja pokazu.")
                options = json.loads(self.rfile.read(size))
                result = demo_lab.run(options["scenario"])
                with closing(sqlite3.connect(self.server.database)) as db, db:
                    db.execute("INSERT OR REPLACE INTO discovery_demo VALUES (1, ?)", (json.dumps(result),))
                return self.reply(result)
            except (ValueError, KeyError, TypeError):
                return self.reply({"error": "Niepoprawna konfiguracja pokazu."}, 400)
            except (OSError, sqlite3.Error):
                return self.reply({"error": "Lokalna usługa HTTPS lub baza wyników jest niedostępna."}, 503)
            finally:
                DEMO_LOCK.release()
        if self.path == "/api/wifi/read-local":
            if not self.local_radio() or self.headers.get("X-Aegis-Session") != "1":
                return self.reply({"error": "Uruchom sesję Wi-Fi lokalnie na laptopie z Windows."}, 403)
            try:
                size = int(self.headers.get("Content-Length", 0))
                if self.headers.get_content_type() != "application/json" or not 0 < size <= 2048:
                    raise ValueError("Błędne dane sesji.")
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError("Błędne dane sesji.")
                session = str(uuid.UUID(data["session"])) if "session" in data else "legacy"
                ssid = data["ssid"]
                if not isinstance(ssid, str) or not ssid.strip() or len(ssid.encode()) > 32:
                    raise ValueError("Podaj poprawną nazwę sieci (SSID).")
                points = read_network(ssid)
                at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
                with closing(sqlite3.connect(self.server.database)) as db, db:
                    db.execute("BEGIN IMMEDIATE")
                    phone = self.server.beacon.phone if self.server.beacon else None
                    point, hint = sensor_point(db, data, phone)
                    result = wifi.save_scan(db, session, ssid, points, at, point, hint, zone=data.get("zone",""))
            except (ValueError, KeyError, TypeError) as error:
                return self.reply({"error": str(error)}, 400)
            except (OSError, subprocess.TimeoutExpired):
                return self.reply({"error": "Karta Wi-Fi nie udostępniła odczytu w wyznaczonym czasie."}, 503)
            return self.reply(result)
        if self.path in ("/api/wifi/inventory", "/api/wifi/scan"):
            if self.headers.get_content_type() != "text/csv" or self.headers.get("X-Aegis-Import") != "1":
                return self.reply({"error": "Oczekiwano pliku CSV."}, 415)
            try:
                size = int(self.headers.get("Content-Length", 0))
                if not 0 < size <= 1024 * 1024:
                    raise ValueError("Wybierz plik CSV do 1 MB.")
                content = self.rfile.read(size)
                with closing(sqlite3.connect(self.server.database)) as db, db:
                    result = wifi.import_file(db, content, self.path.rsplit("/", 1)[1])
            except UnicodeError:
                return self.reply({"error": "Zapisz plik w kodowaniu UTF-8."}, 400)
            except (ValueError, csv.Error) as error:
                return self.reply({"error": str(error)}, 400)
            return self.reply(result)
        if self.path not in ("/api/measurements", "/api/measurements/location"):
            return self.reply({"error": "Nieznana ścieżka"}, 404)
        try:
            if self.headers.get_content_type() != "application/json":
                raise ValueError()
            size = int(self.headers.get("Content-Length", 0))
            if not 0 < size < 3000:
                raise ValueError()
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError()
            identifier = str(uuid.UUID(data["id"]))
            lat, lng = data.get("lat"), data.get("lng")
            location = metadata(data)
            if self.path.endswith("/location"):
                if location["source"] != "manual" or not valid_point(lat, lng):
                    raise ValueError()
                with closing(sqlite3.connect(self.server.database)) as db, db:
                    db.execute("BEGIN IMMEDIATE")
                    row = db.execute("SELECT record FROM measurements WHERE id=?", (identifier,)).fetchone()
                    if row is None:
                        return self.reply({"error": "Nie znaleziono pomiaru."}, 404)
                    saved = json.loads(row[0])
                    correction=db.execute("SELECT point FROM position_corrections WHERE record_key=?",("measurement:"+identifier,)).fetchone()
                    if correction:
                        saved.update(json.loads(correction[0]))
                    if saved["lat"] is not None and (saved["lat"], saved["lng"]) != (lat, lng):
                        return self.reply({"error": "Ten pomiar ma już przypisane miejsce."}, 409)
                    if saved["lat"] is not None:
                        return self.reply(saved)
                    point={"lat":lat,"lng":lng,"location":dict(location,corrected=True)}
                    db.execute("INSERT INTO position_corrections VALUES (?,?)",("measurement:"+identifier,json.dumps(point)))
                    saved.update(point)
                return self.reply(saved)
            if (lat is not None or lng is not None) and not valid_point(lat, lng):
                raise ValueError()
            if type(data["ms"]) not in (float, int) or not 0 <= data["ms"] <= 60000:
                raise ValueError()
            when = datetime.fromisoformat(data["at"].replace("Z", "+00:00"))
            if when.tzinfo is None or not 0 <= when.timestamp() <= time.time() + 300:
                raise ValueError()
            with closing(sqlite3.connect(self.server.database)) as db, db:
                phone = self.server.beacon.phone if self.local_request() and self.server.beacon else None
                point, hint = sensor_point(db, data, phone)
            if point["location"]["source"] == "phone":
                fix_time = datetime.fromisoformat(point["location"]["at"])
                if abs((when-fix_time).total_seconds()) > 15:
                    point = {"lat":None,"lng":None,"location":{"source":"none","reason":"phone_unavailable"}}
            record = {"id": identifier, **point,
                      "ms": round(data["ms"], 1), "at": when.astimezone(timezone.utc).isoformat(),
                      "name": "Pomiar przeglądarki", "demo": False}
            if hint:
                record["browserHint"] = hint
        except (ValueError, KeyError, TypeError, AttributeError, OverflowError):
            return self.reply({"error": "Nieprawidłowy pomiar"}, 400)
        with closing(sqlite3.connect(self.server.database)) as db, db:
            db.execute("INSERT OR IGNORE INTO measurements VALUES (?, ?)", (record["id"], json.dumps(record)))
            saved = db.execute("SELECT record FROM measurements WHERE id = ?", (record["id"],)).fetchone()
        with closing(sqlite3.connect(self.server.database)) as db, db:
            self.reply(work_area.project(db,[json.loads(saved[0])],"measurement")[0], 201)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--db", default=str(ROOT / "measurements.sqlite3"))
    parser.add_argument("--room-demo",action="store_true",help="Osobny pokaz Wi-Fi w tymczasowej bazie.")
    parser.add_argument("--mobile-test",action="store_true",help="Mobilny test z pustą, tymczasową bazą.")
    args = parser.parse_args()
    if args.room_demo or args.mobile_test:
        demo_folder=tempfile.TemporaryDirectory(prefix="aegis-jury-")
        args.db=str(Path(demo_folder.name)/"room.sqlite3")
    server = ThreadingHTTPServer((args.host, args.port), partial(Handler, directory=str(ROOT / "static")))
    server.room_demo = args.room_demo
    server.database = args.db
    server.station_radio=MobileConsole(args.db)
    atexit.register(server.station_radio.stop)
    server.phone_ca = server.beacon = None
    with closing(sqlite3.connect(args.db)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS measurements (id TEXT PRIMARY KEY, record TEXT NOT NULL)")
        wifi.initialize(db)
        work_area.initialize(db)
        db.execute("CREATE TABLE IF NOT EXISTS discovery_demo (id INTEGER PRIMARY KEY, result TEXT NOT NULL)")
    if args.room_demo:
        with closing(sqlite3.connect(args.db)) as db, db:
            room_demo.run(db)
    print(f"AEGIS-MESH: http://{args.host}:{server.server_port}", flush=True)
    atexit.register(lambda: phone_pairing.close_beacon(server.beacon) if server.beacon else None)
    server.serve_forever()

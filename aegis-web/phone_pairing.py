"""Local, short-lived pairing. Only the beacon page and its two POSTs are served on LAN."""
import hashlib
import ipaddress
import json
import math
import re
import queue
from mobile_console import MobileConsole
import os
import secrets
import shutil
import ssl
import subprocess
import tempfile
import sqlite3
from urllib.request import build_opener, ProxyHandler
from urllib.parse import urlsplit
import inspection
import mobile_telemetry
import threading
import time
from contextlib import closing
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from position import valid_point
import work_area

NETWORKS = tuple(map(ipaddress.ip_network, ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")))


def private_ip(value):
    ip = ipaddress.ip_address(value)
    return any(ip in network for network in NETWORKS)


def meters(a, b):
    lat, lng, lat2, lng2 = map(math.radians, (a["lat"], a["lng"], b["lat"], b["lng"]))
    chord = math.sin((lat2-lat)/2)**2 + math.cos(lat)*math.cos(lat2)*math.sin((lng2-lng)/2)**2
    return 12742000 * math.asin(math.sqrt(min(1, chord)))


class Phone:
    def __init__(self, url, fingerprint=None):
        self.url = url
        self.fingerprint = fingerprint
        self.lifetime = 3600
        self.seen = None
        self.lock = threading.RLock()
        self.session = self.invite = self.key = None
        self.last = None
        self.reason = None

    def start(self, invite_seconds=120, lifetime=3600):
        with self.lock:
            self.lifetime = lifetime
            self.seen = None
            self.session = secrets.token_urlsafe(18)
            self.invite = secrets.token_urlsafe(32)
            self.pin = f"{secrets.randbelow(100_000_000):08d}"
            self.pin_attempts = 0
            self.key = self.last = self.reason = None
            self.field_last = None
            self.apk_fix = None
            self.invite_seconds=invite_seconds
            self.inspecting = False
            self.started = time.monotonic()
            return {"session": self.session, "pin": self.pin, "url": self.url + "/phone-beacon#" + self.invite}

    def stop(self, session):
        with self.lock:
            if session and self.session == session:
                self.session = self.invite = self.pin = self.key = self.last = None
                return True
            return False

    def exchange(self, token=None, *, pin=None):
        with self.lock:
            if not self.invite or time.monotonic()-self.started > self.invite_seconds:
                raise ValueError("Link wygasł lub został wykorzystany.")
            if pin is not None:
                if self.pin_attempts >= 5:
                    raise ValueError("Wyczerpano próby PIN. Użyj kodu QR lub rozpocznij nowe parowanie.")
                self.pin_attempts += 1
                if not isinstance(pin,str) or not pin.isascii() or not secrets.compare_digest(self.pin,pin):
                    raise ValueError("Niepoprawny PIN.")
            elif not isinstance(token,str) or not token.isascii() or not secrets.compare_digest(self.invite,token):
                raise ValueError("Niepoprawny link parowania.")
            self.invite = self.pin = None
            self.key = secrets.token_urlsafe(32)
            return {"key": self.key}

    def authorize(self, key):
        if not self.key or not secrets.compare_digest(self.key,key) or time.monotonic()-self.started>self.lifetime:
            raise ValueError("Sesja telefonu wygasła.")

    def receive(self, key, data, city, reject=None, field=False):
        with self.lock:
            self.authorize(key)
            if self.inspecting and not field:
                raise ValueError("Telefon jest w trybie oględzin.")
            lat, lng, accuracy, ts = (data.get(k) for k in ("lat","lng","accuracy","timestamp"))
            if not valid_point(lat, lng) or type(accuracy) not in (int,float) or not 0 <= accuracy <= 50:
                raise ValueError("Potrzebna dokładność do 50 m.")
            if type(ts) not in (int,float) or not 0 <= time.time()-ts <= 15:
                raise ValueError("Pozycja jest nieaktualna. Sprawdź godzinę telefonu.")
            previous=self.field_last if field else self.last
            if previous and ts <= previous["ts"]:
                raise ValueError("Oczekiwanie na nowszą pozycję.")
            candidate = {"lat":lat,"lng":lng,"ts":ts,"accuracy":accuracy}
            if not field and work_area.outside(city, lat, lng):
                self.reason = "outside_city"
                raise ValueError("Pozycja telefonu poza obszarem pracy.")
            if previous:
                minimum = max(0, meters(previous, candidate)-accuracy-previous["accuracy"])
                if minimum / (ts-previous["ts"]) > 150/3.6:
                    if not field:
                        self.reason = "kinematic_jump"
                    if reject:
                        reject(self.reason,candidate)
                    raise ValueError("Odrzucono skok pozycji telefonu.")
            if field:
                self.field_last=candidate
            else:
                self.reason = None
                self.last = candidate
            return {"saved":True}

    def detach(self, key):
        with self.lock:
            if not self.key or not secrets.compare_digest(self.key, key):
                raise ValueError("Sesja telefonu wygasła.")
            self.field_last=self.last = None
            self.reason = "phone_unavailable"
            return {"detached":True}

    def status(self, session):
        with self.lock:
            empty = {"location":{"source":"none","reason":"phone_unavailable"}}
            if not session or session != self.session or time.monotonic()-self.started > self.lifetime:
                return empty
            if not self.last:
                return dict(empty, paired=bool(self.key), reason=self.reason)
            age = time.time()-self.last["ts"]
            if self.reason or not 0 <= age <= 15:
                return dict(empty, paired=True, age=round(age), remaining=max(0,60-int(age)), reason=self.reason)
            p = self.last
            return {"lat":p["lat"],"lng":p["lng"],"paired":True,"age":round(age),
                    "location":{"source":"phone","accuracy":p["accuracy"],
                                "at":datetime.fromtimestamp(p["ts"],timezone.utc).isoformat()}}


def certificate(folder, host):
    """Issue a LAN IP certificate. Discard the CA private key after signing."""
    folder.mkdir(parents=True, exist_ok=True)
    leaf, key, ca = (folder / name for name in ("server.crt","server.key","AEGIS-phone-ca.crt"))
    openssl = shutil.which("openssl") or str(Path(os.getenv("ProgramFiles", "")) / "Git/mingw64/bin/openssl.exe")
    def run(*args):
        return subprocess.run([openssl, *map(str,args)], capture_output=True, check=True, timeout=30)
    if all(p.exists() for p in (leaf,key,ca)):
        try:
            run("x509","-checkend","86400","-noout","-in",leaf)
            run("verify","-CAfile",ca,"-verify_ip",host,leaf)
            return leaf,key,ca
        except subprocess.CalledProcessError:
            pass
    with tempfile.TemporaryDirectory(dir=folder) as scratch:
        tmp = Path(scratch)
        run("req","-new","-x509","-newkey","rsa:2048","-nodes","-days","90","-subj","/CN=AEGIS local phone CA",
            "-addext","basicConstraints=critical,CA:TRUE,pathlen:0","-addext","keyUsage=critical,keyCertSign",
            "-keyout",tmp/"ca.key","-out",tmp/ca.name)
        run("req","-new","-newkey","rsa:2048","-nodes","-subj","/CN=AEGIS phone beacon",
            "-keyout",tmp/key.name,"-out",tmp/"server.csr")
        (tmp/"ext").write_text(f"subjectAltName=IP:{host}\nextendedKeyUsage=serverAuth\nbasicConstraints=critical,CA:FALSE\nkeyUsage=digitalSignature,keyEncipherment\n",encoding="ascii")
        run("x509","-req","-in",tmp/"server.csr","-CA",tmp/ca.name,"-CAkey",tmp/"ca.key","-CAcreateserial",
            "-days","89","-sha256","-extfile",tmp/"ext","-out",tmp/leaf.name)
        for target in (leaf,key,ca):
            (tmp/target.name).replace(target)
    return leaf,key,ca


class Beacon(BaseHTTPRequestHandler):
    def allowed_peer(self):
        if getattr(self.server,"tunnel",None):
            return self.client_address[0]=="127.0.0.1"
        return private_ip(self.client_address[0])

    def log_message(self, *args):
        pass

    def send(self, status, body, mime="application/json"):
        self.send_response(status)
        self.send_header("Content-Type",mime)
        self.send_header("Content-Length",str(len(body)))
        self.send_header("Cache-Control","no-store")
        self.send_header("Referrer-Policy","strict-origin-when-cross-origin")
        self.send_header("X-Content-Type-Options","nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        assets={"/":"phone.html","/phone-beacon":"phone.html","/phone.js":"phone.js",
                "/inspection-ar.js":"inspection-ar.js","/mobile.js":"mobile.js","/mobile.css":"mobile.css",
                "/vendor/leaflet.js":"vendor/leaflet.js","/vendor/leaflet.css":"vendor/leaflet.css",
                "/demo.js":"demo.js"}
        file=assets.get(self.path)
        if not file or not self.allowed_peer():
            return self.send(404,b"{}")
        mime={".html":"text/html",".js":"text/javascript",".css":"text/css"}[Path(file).suffix]
        self.send(200,(Path(__file__).parent/"static"/file).read_bytes(),mime+"; charset=utf-8")

    def do_POST(self):
        if not self.allowed_peer() or self.headers.get("Origin") != self.server.phone.url:
            return self.send(403,b"{}")
        try:
            size = int(self.headers.get("Content-Length",0))
            limit = 262144 if self.path == "/api/mobile-telemetry" else 1024
            if self.headers.get_content_type() != "application/json" or not 0 < size <= limit:
                raise ValueError("Niepoprawny format.")
            data = json.loads(self.rfile.read(size))
            if self.path == "/exchange":
                result = self.server.phone.exchange(data.get("token"),pin=data.get("pin"))
                result["scope"] = "AGH-LAB" if getattr(self.server,"tunnel",None) else "local"
            elif self.path == "/api/mobile-telemetry":
                phone = self.server.phone
                key = self.headers.get("Authorization","").removeprefix("Bearer ")
                with phone.lock:
                    try:
                        issued = None
                        if not key and data.get("pin") is not None:
                            original_invite, original_pin = phone.invite, phone.pin
                            issued = phone.exchange(pin=data["pin"])["key"]
                            key = issued
                        phone.authorize(key)
                    except ValueError as error:
                        return self.send(401,json.dumps({"error":str(error)}).encode())
                    try:
                        with closing(sqlite3.connect(self.server.database)) as db, db:
                            result = mobile_telemetry.save(db,phone.session,data,bool(getattr(self.server,"tunnel",None)))
                    except Exception:
                        if issued:
                            phone.invite, phone.pin, phone.key = original_invite, original_pin, None
                        raise
                    phone.seen = time.time()
                    if "location" in result:
                        phone.apk_fix = result["location"]
                    if issued:
                        result["key"] = issued
            elif self.path == "/known":
                # Known routers for the phone's offline alerts, straight from the laptop inventory.
                with self.server.phone.lock:
                    self.server.phone.authorize(self.headers.get("Authorization","").removeprefix("Bearer "))
                with closing(sqlite3.connect(self.server.database)) as db:
                    rows = db.execute("SELECT ssid,bssid,security FROM live_wifi_inventory ORDER BY ssid,bssid").fetchall()
                result = {"routers": [{"ssid": a or "", "bssid": b, "security": c} for a, b, c in rows]}
            elif self.path == "/detach":
                result = self.server.phone.detach(self.headers.get("Authorization","").removeprefix("Bearer "))
            elif self.path in ("/inspection","/alerts"):
                phone=self.server.phone
                with phone.lock:
                    phone.authorize(self.headers.get("Authorization","").removeprefix("Bearer "))
                    if self.path=="/inspection":
                        phone.inspecting=True
                        phone.last=None
                with closing(sqlite3.connect(self.server.database)) as db:
                    cases=inspection.snapshot(db)["cases"]
                result={"cases":[{k:c[k] for k in ("id","ssid","bssid","expected","security","zones","at")} for c in cases if not c["closed"]]}
            elif self.path in ("/mobile","/radio","/sample","/service-test","/service-report"):
                with self.server.phone.lock:
                    self.server.phone.authorize(self.headers.get("Authorization","").removeprefix("Bearer "))
                result=self.server.console.dispatch(self.path,data,self.server.phone)
                if self.path == "/mobile":
                    result["apkFix"] = getattr(self.server.phone,"apk_fix",None)
            elif self.path == "/fix":
                with closing(sqlite3.connect(self.server.database)) as db, db:
                    city = work_area.selected(db)
                    def rejected(reason,fix):
                        db.execute("INSERT INTO rejected_phone_fixes(received,reason,lat,lng,accuracy,device_time) VALUES (?,?,?,?,?,?)",
                                   (inspection.stamp(),reason,fix["lat"],fix["lng"],fix["accuracy"],fix["ts"]))
                        db.commit()
                    result = self.server.phone.receive(self.headers.get("Authorization","").removeprefix("Bearer "),data,city,rejected,field=data.get("device")=="mobile")
            else:
                return self.send(404,b"{}")
            self.send(200,json.dumps(result).encode())
        except (KeyError,ValueError,TypeError,AttributeError,OverflowError) as error:
            self.send(400,json.dumps({"error":str(error)}).encode())
        except sqlite3.Error:
            self.send(503,b'{"error":"SQLite unavailable"}')


def listen(host, port, database, cleartext=False):
    if not private_ip(host):
        raise ValueError("Podaj prywatny adres IPv4 interfejsu laptopa.")
    folder = Path(database).resolve().parent/"phone-tls"/host
    ca = None
    if not cleartext:
        leaf,key,ca = certificate(folder,host)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(leaf,key)
    server = ThreadingHTTPServer((host,port),Beacon)
    server.database = database
    server.console=MobileConsole(database)
    scheme = "http" if cleartext else "https"
    fingerprint = None
    if not cleartext:
        der = ssl.PEM_cert_to_DER_cert(Path(leaf).read_text(encoding="ascii"))
        fingerprint = hashlib.sha256(der).hexdigest()
    server.phone = Phone(f"{scheme}://{host}:{server.server_port}", fingerprint)
    if not cleartext:
        server.socket = context.wrap_socket(server.socket,server_side=True)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    return server,ca

def listen_online(database):
    """Publish only the phone handler. Discover the HTTPS URL for this specific upstream."""
    agent=shutil.which("ngrok")
    if not agent:
        raise ValueError("Zainstaluj i skonfiguruj ngrok lub wybierz parowanie w sieci lokalnej.")
    server=ThreadingHTTPServer(("127.0.0.1",0),Beacon)
    server.database=database
    server.console=MobileConsole(database)
    upstream=f"http://127.0.0.1:{server.server_port}"
    process=None
    try:
        process=subprocess.Popen([agent,"http",upstream,"--inspect=false"],stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        server.tunnel=process
        client=build_opener(ProxyHandler({}))
        deadline=time.monotonic()+10
        while time.monotonic()<deadline and process.poll() is None:
            try:
                with client.open("http://127.0.0.1:4040/api/tunnels",timeout=.5) as response:
                    entries=json.load(response)["tunnels"]
                for entry in entries:
                    url=entry["public_url"].rstrip("/")
                    if entry["config"]["addr"].rstrip("/")==upstream and urlsplit(url).scheme=="https":
                        server.phone=Phone(url)
                        threading.Thread(target=server.serve_forever,daemon=True).start()
                        return server,None
            except (OSError,ValueError,KeyError):
                pass
            time.sleep(.2)
        raise ValueError("Ngrok nie zwrócił adresu HTTPS. Sprawdź konfigurację konta ngrok i dostępność jego lokalnego API na porcie 4040.")
    except BaseException:
        server.server_close()
        if process and process.poll() is None:
            process.terminate()
            process.wait()
        raise


def listen_cloudflare(database):
    binary=shutil.which("cloudflared") or str(Path(os.environ.get("ProgramFiles(x86)",""))/"cloudflared/cloudflared.exe")
    server=ThreadingHTTPServer(("127.0.0.1",0),Beacon)
    server.database=database
    server.console=MobileConsole(database)
    found=queue.Queue(maxsize=1)
    upstream=f"http://127.0.0.1:{server.server_port}"
    try:
        process=subprocess.Popen([binary,"tunnel","--no-autoupdate","--protocol","http2","--url",upstream],
            stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,errors="replace",
            creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        server.tunnel=process
        def read_output():
            announced=False
            with process.stdout as lines:
                for line in lines:
                    match=re.search(r"https://[a-z0-9-]+\.trycloudflare\.com",line)
                    if match and not announced:
                        found.put(match.group());announced=True
            if not announced:
                found.put(None)
        threading.Thread(target=read_output,daemon=True).start()
        try:
            url=found.get(timeout=35)
        except queue.Empty:
            url=None
        if not url:
            raise ValueError("Cloudflare nie przydzielił adresu. Sprawdź dostęp do internetu.")
        server.phone=Phone(url)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        return server,None
    except BaseException:
        server.server_close()
        proc=getattr(server,"tunnel",None)
        if proc and proc.poll() is None:
            proc.terminate();proc.wait()
        raise


def close_beacon(server):
    server.console.stop()
    server.shutdown()
    server.server_close()
    process=getattr(server,"tunnel",None)
    if process and process.poll() is None:
        process.terminate()
        process.wait()

import json
import sqlite3
import sys
import tempfile
import time
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import export_wifi
import phone_pairing
import server
import wifi
import work_area


class LocationTrust(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        wifi.initialize(self.db)
        work_area.initialize(self.db)
        self.db.execute("INSERT INTO work_area VALUES (1,'Bydgoszcz')")
        self.city = work_area.selected(self.db)
        self.at = datetime.now(timezone.utc).isoformat()
        self.station = {"lat":self.city["lat"], "lng":self.city["lng"],
                        "location":{"source":"manual","at":self.at,"station":"Akademik"}}

    def tearDown(self):
        self.db.close()

    def test_recorded_jumps_never_create_browser_pins(self):
        cracow = work_area.CITIES["Kraków"]
        for index, (city, accuracy) in enumerate(((cracow,95),(self.city,465),(cracow,241),(cracow,56))):
            hint = {"lat":city["lat"], "lng":city["lng"], "location":{"source":"browser","at":self.at,"accuracy":accuracy}}
            point, raw = server.sensor_point(self.db, dict(self.station,browserHint=hint))
            self.assertEqual(point, self.station)
            wifi.save_scan(self.db,"series","lab",[],self.at,point,raw)
            unlocated, _ = server.sensor_point(self.db,hint)
            self.assertIsNone(unlocated["lat"])
        self.assertEqual(self.db.execute("SELECT count(*) FROM wifi_scans WHERE lat=?",(self.station["lat"],)).fetchone()[0],4)
        self.assertEqual(self.db.execute("SELECT count(*) FROM wifi_scans WHERE json_extract(browser_hint,'$.lat')=?",(cracow["lat"],)).fetchone()[0],3)

    def test_pl_en_blocks_and_scan_membership(self):
        for auth, signal, channel in (("Authentication","Signal","Channel"),("Uwierzytelnianie","Sygnał","Kanał")):
            lines = ["SSID 1 : lab", f"{auth} : WPA2-Personal"]
            for n in range(27):
                lines.extend((f"BSSID {n+1} : 02:00:00:00:00:{n:02x}",f"{signal} : 88%",f"{channel} : 6"))
            lines.extend(("SSID 2 : inne","BSSID 1 : 00:00:00:00:00:00"))
            points=list(export_wifi.parse_report("\n".join(lines),"lab"))
            self.assertEqual(len(points),27)
            self.assertTrue(all(p[3:]==(88,6) for p in points))
        wifi.save_scan(self.db,"series","lab",points,self.at,self.station)
        state=wifi.state(self.db,work_area.project)
        self.assertEqual(len(state["scans"]),1)
        self.assertEqual({p["scan_id"] for p in state["readings"]},{state["scans"][0]["id"]})
        self.assertEqual(len(state["readings"]),27)
        self.assertTrue(all(p["rssi_dbm"]==-56 for p in state["readings"]))
        self.assertEqual(self.db.execute("SELECT count(lat) FROM wifi_observations").fetchone()[0],0)

    def test_unchanged_changed_empty_and_concurrent_series(self):
        def save(points, series="a"):
            return wifi.save_scan(self.db,series,"lab",points,self.at,{"location":{"source":"none"}})
        point=("lab","02:00:00:00:00:01","OPEN",80,1)
        self.assertFalse(save([point])["unchanged"])
        self.assertTrue(save([point])["unchanged"])
        self.assertFalse(save([point],"b")["unchanged"])
        self.assertFalse(save([(*point[:3],78,1)])["unchanged"])
        self.assertEqual(save([])["added"],0)
        state=wifi.state(self.db,work_area.project)
        self.assertEqual((len(state["scans"]),len(state["readings"])),(5,4))

    def test_legacy_and_csv(self):
        row=("lab","02:00:00:00:00:01","OPEN","Laptop",self.at,50.06,19.94)
        wifi.save_observations(self.db,[row],location={"source":"browser"})
        self.db.commit()
        wifi.initialize(self.db)
        historical=wifi.state(self.db,work_area.project)["readings"][0]
        self.assertIsNone(historical["lat"])
        self.assertEqual(historical["unverifiedPoint"]["lat"],row[-2])
        self.assertEqual(self.db.execute("SELECT lat FROM wifi_observations").fetchone()[0],row[-2])
        self.assertEqual(wifi.save_observations(self.db,[row])["duplicates"],1)

    def test_pairing_jump_expiry_and_manual_priority(self):
        phone=phone_pairing.Phone("https://192.168.0.2:8767")
        invite=phone.start()
        token=invite["url"].split("#")[1]
        key=phone.exchange(token)["key"]
        with self.assertRaises(ValueError):
            phone.exchange(token)
        now=time.time()
        data={"lat":self.station["lat"],"lng":self.station["lng"],"accuracy":12,"timestamp":now-3}
        with patch("phone_pairing.time.time",return_value=now):
            phone.receive(key,data,self.city)
            with self.assertRaises(ValueError):
                phone.receive(key,data,self.city)
            self.assertEqual(phone.status(invite["session"])["location"]["source"],"phone")
            self.assertEqual(server.sensor_point(self.db,dict(self.station,phoneSession=invite["session"]),phone)[0],self.station)
            with self.assertRaises(ValueError):
                phone.receive(key,dict(data,lat=50.06,lng=19.94,timestamp=now-1),None)
            self.assertEqual(phone.status(invite["session"])["reason"],"kinematic_jump")
            phone.receive(key,dict(data,timestamp=now-1),self.city)
        for age, remaining in ((25,35),(65,0)):
            with patch("phone_pairing.time.time",return_value=data["timestamp"]+2+age):
                state=phone.status(invite["session"])
                self.assertEqual(state["remaining"],remaining)
                self.assertEqual(state["location"]["source"],"none")
        phone.stop(invite["session"])
        with self.assertRaises(ValueError):
            phone.receive(key,data,self.city)

    def test_mobile_position_does_not_move_fixed_windows_sensor(self):
        import mobile_console
        import threading
        phone=phone_pairing.Phone("https://example.test")
        invite=phone.start()
        key=phone.exchange(pin=invite["pin"])["key"]
        now=time.time()
        data={"lat":53.12,"lng":18.01,"accuracy":10,"timestamp":now-3}
        phone.receive(key,data,None,field=True)
        self.assertEqual(phone.status(invite["session"])["location"]["source"],"none")
        console=mobile_console.MobileConsole(":memory:")
        entered=threading.Event()
        def scan(session,ssid,zone,station,cancel):
            entered.set()
            cancel.wait(5)
        with patch.object(console,"_scan",side_effect=scan):
            try:
                console.start({"anchor":True,"zone":"A","ssid":"AGH-LAB"},phone)
                self.assertTrue(entered.wait(1))
                phone.receive(key,dict(data,lat=53.1201,timestamp=now-1),None,field=True)
                self.assertEqual(console.status()["station"]["lat"],53.12)
                self.assertEqual(phone.field_last["lat"],53.1201)
            finally:
                console.stop()
        with patch("phone_pairing.time.monotonic",return_value=phone.started+901):
            with self.assertRaises(ValueError):
                phone.exchange(pin=invite["pin"])

    def test_pin_fallback_is_short_lived_and_single_use(self):
        phone=phone_pairing.Phone("https://example.test")
        invite=phone.start()
        self.assertRegex(invite["pin"],r"^[0-9]{8}$")
        self.assertTrue(phone.exchange(pin=invite["pin"])["key"])
        with self.assertRaises(ValueError):
            phone.exchange(invite["url"].split("#")[1])
        invite=phone.start()
        with patch("phone_pairing.time.monotonic",return_value=phone.started+121):
            with self.assertRaises(ValueError):
                phone.exchange(pin=invite["pin"])
        for _ in range(5):
            with self.assertRaises(ValueError):
                phone.exchange(pin="bad")
        with self.assertRaises(ValueError):
            phone.exchange(pin=invite["pin"])
        self.assertTrue(phone.exchange(invite["url"].split("#")[1])["key"])

    def test_scan_http_entry_point(self):
        import threading
        from functools import partial
        from http.client import HTTPConnection
        from http.server import ThreadingHTTPServer
        from uuid import uuid4

        with tempfile.TemporaryDirectory() as scratch:
            database = str(Path(scratch)/"test.sqlite3")
            with closing(sqlite3.connect(database)) as db, db:
                wifi.initialize(db)
                work_area.initialize(db)
                db.execute("INSERT INTO work_area VALUES (1,'Bydgoszcz')")
            api=ThreadingHTTPServer(("127.0.0.1",0),partial(server.Handler,directory=scratch))
            api.database,api.beacon=database,None
            thread=threading.Thread(target=api.serve_forever,daemon=True)
            thread.start()
            client=HTTPConnection("127.0.0.1",api.server_port,timeout=5)
            try:
                headers={"Content-Type":"application/json","X-Aegis-Session":"1"}
                body={"ssid":"lab","session":str(uuid4()),**self.station}
                with patch.object(server.Handler,"local_radio",return_value=True), patch.object(server,"read_network",return_value=[]):
                    client.request("POST","/api/wifi/read-local",json.dumps(body),headers)
                    response=client.getresponse()
                    self.assertEqual(response.status,200)
                    self.assertEqual(json.loads(response.read())["location"]["source"],"manual")
                client.request("GET","/api/wifi")
                result=json.loads(client.getresponse().read())
                self.assertEqual((len(result["scans"]),result["observations"]),(1,0))
            finally:
                client.close()
                api.shutdown()
                api.server_close()
                thread.join()

    def test_verified_https_beacon(self):
        import ssl
        import threading
        import shutil
        from http.client import HTTPSConnection
        from http.server import ThreadingHTTPServer
        from unittest.mock import patch
        if not shutil.which("openssl") and not Path("C:/Program Files/Git/mingw64/bin/openssl.exe").exists():
            self.skipTest("Optional OpenSSL for phone pairing unavailable")
        with tempfile.TemporaryDirectory() as scratch:
            folder=Path(scratch)
            leaf,key,ca=phone_pairing.certificate(folder,"127.0.0.1")
            context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(leaf,key)
            beacon=ThreadingHTTPServer(("127.0.0.1",0),phone_pairing.Beacon)
            beacon.socket=context.wrap_socket(beacon.socket,server_side=True)
            beacon.phone=phone_pairing.Phone(f"https://127.0.0.1:{beacon.server_port}")
            beacon.database=str(folder/"test.db")
            with closing(sqlite3.connect(beacon.database)) as db, db:
                work_area.initialize(db)
            invite=beacon.phone.start()
            thread=threading.Thread(target=beacon.serve_forever,daemon=True)
            thread.start()
            client=HTTPSConnection("127.0.0.1",beacon.server_port,context=ssl.create_default_context(cafile=str(ca)),timeout=5)
            try:
                with patch.object(phone_pairing,"private_ip",return_value=True):
                    client.request("GET","/phone-beacon")
                    response=client.getresponse()
                    self.assertEqual(response.status,200)
                    self.assertIn(b"/phone.js",response.read())
                    client.request("GET","/api/wifi")
                    response=client.getresponse()
                    self.assertEqual(response.status,404)
                    response.read()
                    headers={"Content-Type":"application/json","Origin":beacon.phone.url}
                    client.request("POST","/exchange",json.dumps({"token":invite["url"].split("#")[1]}),headers)
                    credential=json.loads(client.getresponse().read())["key"]
                    headers["Authorization"]="Bearer "+credential
                    client.request("POST","/fix",json.dumps({"lat":53.12,"lng":18.01,"accuracy":12,"timestamp":time.time()-1}),headers)
                    self.assertTrue(json.loads(client.getresponse().read())["saved"])
                    self.assertEqual(beacon.phone.status(invite["session"])["location"]["source"],"phone")
                    client.request("POST","/detach","{}",headers)
                    self.assertTrue(json.loads(client.getresponse().read())["detached"])
                    self.assertEqual(beacon.phone.status(invite["session"])["location"]["source"],"none")
            finally:
                client.close()
                beacon.shutdown()
                beacon.server_close()
                thread.join()

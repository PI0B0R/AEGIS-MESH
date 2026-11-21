import json
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from contextlib import closing
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mobile_telemetry
import phone_pairing
import wifi


def batch():
    ap = {"ssid":"AGH-LAB","bssid":"02:00:00:00:00:01","rssi_dbm":-53,"channel":6,
          "frequency_mhz":2437,"security":"[ESS]","age_ms":500}
    return {"sensor_id":"PHONE-APK-01","location":{"lat":53.1,"lng":18.0,"accuracy":9,"provider":"gps",
            "timestamp":time.time()-1},"networks":[ap, dict(ap,bssid="02:00:00:00:00:02",security="[RSN-EAP-CCMP][ESS]")]}


class TelemetryTest(unittest.TestCase):
    def test_pair_and_submit_over_http(self):
        with tempfile.TemporaryDirectory() as folder:
            database = str(Path(folder)/"test.db")
            with closing(sqlite3.connect(database)) as db, db:
                wifi.initialize(db)
            with ThreadingHTTPServer(("127.0.0.1",0),phone_pairing.Beacon) as api:
                host, port = api.server_address
                origin = f"http://{host}:{port}"
                api.phone = phone_pairing.Phone(origin)
                api.database, api.tunnel = database, True
                invitation = api.phone.start()
                worker = threading.Thread(target=api.serve_forever,daemon=True)
                worker.start()
                def post(path, data, key=""):
                    with closing(HTTPConnection(host,port,timeout=3)) as client:
                        client.request("POST",path,json.dumps(data),{"Origin":origin,"Content-Type":"application/json",
                                       "Authorization":"Bearer "+key})
                        response = client.getresponse()
                        return response.status, json.loads(response.read())
                path = "/api/mobile-telemetry"
                try:
                    data = batch()
                    self.assertEqual(post(path,data)[0],401)
                    status, result = post("/exchange",{"pin":invitation["pin"]})
                    self.assertEqual((status,result["scope"]),(200,"AGH-LAB"))
                    key = result["key"]
                    status, saved = post(path,data,key)
                    self.assertEqual((status,saved["points"],saved["new_cases"]),(200,2,1))
                    self.assertTrue(post(path,data,key)[1]["duplicate"])
                    for field,value in (("accuracy",51),("provider","network"),("timestamp",time.time()-30)):
                        invalid = batch()
                        invalid["location"][field] = value
                        self.assertEqual(post(path,invalid,key)[0],400)
                    data["networks"][0]["ssid"] = "poza-zakresem"
                    self.assertEqual(post(path,data,key)[0],400)
                    with closing(sqlite3.connect(database)) as db:
                        self.assertEqual(db.execute("SELECT count(*) FROM location_fixes").fetchone()[0],1)
                        rows = db.execute("SELECT DISTINCT scan_id,rssi_dbm,frequency_mhz FROM wifi_observations").fetchall()
                        self.assertEqual(rows,[(saved["scan_id"],-53,2437)])
                finally:
                    api.shutdown()
                    worker.join()

    def test_lan_multiple_ssids_and_cached_results(self):
        with closing(sqlite3.connect(":memory:")) as db:
            with db:
                wifi.initialize(db)
            data = batch()
            data["networks"][1]["ssid"] = "Local lab"
            data["networks"].append(dict(data["networks"][0],age_ms=31000,bssid="02:00:00:00:00:03"))
            with db:
                result = mobile_telemetry.save(db,"session",data,False)
            self.assertEqual(result["points"],2)
            self.assertEqual(db.execute("SELECT count(DISTINCT ssid),count(DISTINCT scan_id) FROM wifi_observations").fetchone(),(2,1))

    def test_manual_place_without_gps(self):
        with closing(sqlite3.connect(":memory:")) as db:
            with db:
                wifi.initialize(db)
            data = batch()
            data["sensor_id"] = "PHONE-1A2B3C4D"
            data["location"] = {"provider":"manual","timestamp":time.time()-1,
                                "place":{"building":"B1","floor":"2","room":"215"}}
            with db:
                result = mobile_telemetry.save(db,"session",data,True)
            self.assertEqual((result["points"],result["place"]["room"]),(2,"215"))
            self.assertNotIn("location",result)
            with db:
                self.assertTrue(mobile_telemetry.save(db,"session",data,True)["duplicate"])
            self.assertEqual(db.execute("SELECT building,floor,room FROM place_fixes").fetchall(),[("B1","2","215")])
            scan = json.loads(db.execute("SELECT location FROM wifi_scans").fetchone()[0])
            self.assertEqual((scan["source"],scan["place"]["building"]),("paired_phone_manual","B1"))
            for place in (None, {"building":""}, {"building":"B1;DROP"}, "B1"):
                invalid = batch()
                invalid["location"] = {"provider":"manual","timestamp":time.time(),"place":place}
                with self.assertRaises(ValueError), db:
                    mobile_telemetry.save(db,"session",invalid,True)

    def test_gps_with_place(self):
        with closing(sqlite3.connect(":memory:")) as db:
            with db:
                wifi.initialize(db)
            data = batch()
            data["location"]["place"] = {"building":"C2","floor":"","room":""}
            with db:
                result = mobile_telemetry.save(db,"session",data,True)
            self.assertEqual(result["location"]["location"]["place"]["building"],"C2")

    def test_scan_without_any_location(self):
        with closing(sqlite3.connect(":memory:")) as db:
            with db:
                wifi.initialize(db)
            data = batch()
            data["location"] = {"provider":"none","timestamp":time.time()}
            with db:
                result = mobile_telemetry.save(db,"session",data,True)
            self.assertEqual(result["points"],2)
            self.assertEqual(db.execute("SELECT lat,location FROM wifi_scans").fetchone()[0],None)

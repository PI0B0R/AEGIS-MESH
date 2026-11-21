import hashlib
import json
import ssl
import sqlite3
import sys
import tempfile
import time
import unittest
from contextlib import closing
from http.client import HTTPSConnection
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit, parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import phone_pairing
import server
import wifi


class QuickPairingTest(unittest.TestCase):
    """One QR from the laptop: auto address, pinned HTTPS, known routers, telemetry."""

    def test_qr_pair_known_and_telemetry(self):
        original = phone_pairing.private_ip, server.lan_addresses
        phone_pairing.private_ip = lambda value: True
        server.lan_addresses = lambda: ["127.0.0.1"]
        with tempfile.TemporaryDirectory() as folder:
            database = str(Path(folder) / "t.db")
            with closing(sqlite3.connect(database)) as db, db:
                wifi.initialize(db)
                db.execute("INSERT INTO wifi_inventory VALUES ('AGH-LAB','02:00:00:00:00:01','WPA2-ENTERPRISE')")
            host = SimpleNamespace(beacon=None, phone_ca=None, database=database)
            try:
                first = server.quick_pairing(host)
                self.assertFalse(first["paired"])
                self.assertTrue(first["secure"])
                again = server.quick_pairing(host)
                self.assertEqual(first["qr"], again["qr"])   # polling keeps the same code
                url = urlsplit(first["qr"])
                params = {k: v[0] for k, v in parse_qs(url.fragment).items()}
                origin = f"{url.scheme}://{url.netloc}"
                context = ssl.create_default_context()
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE

                def post(path, data, key=""):
                    with closing(HTTPSConnection(url.hostname, url.port, context=context, timeout=5)) as client:
                        client.connect()
                        der = client.sock.getpeercert(binary_form=True)
                        self.assertEqual(hashlib.sha256(der).hexdigest(), params["fp"])  # what the app pins
                        client.request("POST", path, json.dumps(data), {"Origin": origin,
                                       "Content-Type": "application/json", "Authorization": "Bearer " + key})
                        response = client.getresponse()
                        return response.status, json.loads(response.read())

                status, paired = post("/exchange", {"token": params["t"]})
                self.assertEqual(status, 200)
                key = paired["key"]
                self.assertEqual(post("/exchange", {"token": params["t"]})[0], 400)   # single use
                self.assertTrue(server.quick_pairing(host)["paired"])
                self.assertNotIn("qr", server.quick_pairing(host))
                status, known = post("/known", {}, key)
                self.assertEqual(known["routers"][0]["bssid"], "02:00:00:00:00:01")
                self.assertEqual(post("/known", {}, "wrong")[0], 400)
                body = {"sensor_id": "PHONE-1", "networks": [], "location": {"provider": "manual",
                        "timestamp": time.time(), "place": {"building": "B1", "floor": "", "room": ""}}}
                self.assertEqual(post("/api/mobile-telemetry", body, key)[0], 200)
                self.assertEqual(server.quick_pairing(host)["seen"], 0)
                renewed = server.quick_pairing(host, renew=True)
                self.assertFalse(renewed["paired"])
                self.assertNotEqual(renewed["qr"], first["qr"])
            finally:
                if host.beacon:
                    phone_pairing.close_beacon(host.beacon)
                phone_pairing.private_ip, server.lan_addresses = original


if __name__ == "__main__":
    unittest.main()

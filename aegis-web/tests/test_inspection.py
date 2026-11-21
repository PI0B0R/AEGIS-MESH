import sqlite3
import sys
import unittest
from pathlib import Path
from datetime import datetime,timedelta,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import wifi
import inspection

class InspectionTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(":memory:")
        wifi.initialize(self.db)
        self.db.commit()
        self.at=datetime.now(timezone.utc)-timedelta(seconds=10)
        self.ssid="AGH-LAB"
        self.known="02:01:00:00:00:01"
        self.other="02:01:00:00:00:02"
        data=f"ssid,bssid,zone,expected_security\n{self.ssid},{self.known},Strefa A,802.1X"
        wifi.import_file(self.db,data.encode(),"inventory")
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def scan(self,z,percent,mode="OPEN",bssid=None,sensor=None):
        self.at+=timedelta(milliseconds=1)
        points=[(self.ssid,bssid or self.other,mode,percent,6)]
        wifi.save_scan(self.db,"series",self.ssid,points,self.at.isoformat(),{"location":{"source":"none"}},zone=z,sensor=sensor or z)

    def test_enterprise_baseline_and_confirmed_difference(self):
        self.scan("A",88,"WPA3-ENTERPRISE",self.known)
        self.assertEqual(inspection.snapshot(self.db)["cases"],[])
        self.scan("B",80,"WPA2-ENTERPRISE")
        self.assertEqual(inspection.snapshot(self.db)["cases"][0]["reason"],"new_bssid")
        self.scan("B",72)
        item=inspection.snapshot(self.db)["cases"][0]
        self.assertEqual(item["reason"],"new_bssid_security")
        self.assertEqual(item["zones"],[])
        self.assertFalse(item["closed"])
        self.assertEqual(inspection.snapshot(self.db)["inventory"][0]["zone"],"A")

    def test_adjacent_and_stale(self):
        for z,p in (("A",85),("B",84),("C",45)):
            self.scan(z,p)
        state=inspection.snapshot(self.db)
        self.assertEqual(set(state["cases"][0]["zones"]),{"A","B"})
        later=inspection.snapshot(self.db,self.at+timedelta(seconds=91))
        self.assertEqual(later["cases"][0]["zones"],[])
        self.assertFalse(any(s["online"] for s in later["sensors"]))

    def test_no_cached_report_vote_after_move(self):
        self.scan("B",90,sensor="laptop")
        self.scan("C",90,sensor="laptop")
        self.assertEqual(inspection.snapshot(self.db)["cases"][0]["strength"],{"B":-55})

    def test_decision_persists_then_new_detection_reopens(self):
        self.scan("A",77)
        case=inspection.snapshot(self.db)["cases"][0]
        data={"incident":case["id"],"outcome":inspection.OUTCOMES[0],"note":"Sprawdzono rejestr."}
        inspection.decide(self.db,data)
        with self.assertRaises(ValueError):inspection.decide(self.db,data)
        self.assertTrue(inspection.snapshot(self.db)["cases"][0]["closed"])
        self.at=datetime.now(timezone.utc)
        self.scan("D",65)
        case=inspection.snapshot(self.db)["cases"][0]
        self.assertFalse(case["closed"])
        self.assertEqual(case["log"][0]["note"],data["note"])

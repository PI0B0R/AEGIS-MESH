"""Session-scoped mobile controls for the fixed Windows sensor."""
from contextlib import closing
from datetime import datetime, timezone
import json
import sqlite3
import subprocess
import threading
import uuid

import demo_lab
import inspection
import room_demo
import wifi
from export_wifi import read_network


class MobileConsole:
    def __init__(self,database):
        self.database=database
        self.lock=threading.RLock()
        self.worker=None
        self.cancel=threading.Event()
        self.ssid="AGH-LAB"
        self.zone=""
        self.station=None
        self.last_at=None
        self.count=None
        self.error=""
        self.service_lock=threading.Lock()

    def status(self):
        with self.lock:
            return dict(active=bool(self.worker and self.worker.is_alive()),ssid=self.ssid,zone=self.zone,
                        station=self.station,at=self.last_at,count=self.count,error=self.error)

    def stop(self):
        with self.lock:
            worker=self.worker
            self.cancel.set()
        if worker and worker is not threading.current_thread():
            worker.join(timeout=12)

    def start(self,data,phone):
        with self.lock:
            if self.worker and self.worker.is_alive():
                return self.status()
            ssid=data.get("ssid","AGH-LAB")
            if not isinstance(ssid,str) or not ssid.strip() or len(ssid.encode())>32:
                raise ValueError("Wpisz nazwę sieci o długości do 32 bajtów.")
            zone=inspection.zone(data.get("zone",""))
            point=None
            if data.get("anchor") is True:
                with phone.lock:
                    fix=phone.field_last
                    if not fix or not 0 <= datetime.now(timezone.utc).timestamp()-fix["ts"] <= 15:
                        raise ValueError("Stań przy laptopie i poczekaj na świeży odczyt telefonu.")
                    point={"lat":fix["lat"],"lng":fix["lng"],"location":{"source":"manual",
                        "at":datetime.fromtimestamp(fix["ts"],timezone.utc).isoformat(),"accuracy":fix["accuracy"],
                        "station":"Stanowisko laptopa · potwierdzone przy telefonie"}}
            self.ssid,self.zone,self.station=ssid,zone,point
            self.count=self.last_at=None
            self.error=""
            self.cancel=threading.Event()
            self.worker=threading.Thread(target=self._scan,args=(str(uuid.uuid4()),ssid,zone,point,self.cancel),daemon=True)
            self.worker.start()
            return self.status()

    def _scan(self,session,ssid,zone,station,cancel):
        while not cancel.is_set():
            try:
                points=read_network(ssid)
                at=inspection.stamp()
                with closing(sqlite3.connect(self.database)) as db,db:
                    db.execute("BEGIN IMMEDIATE")
                    wifi.save_scan(db,session,ssid,points,at,station or {"location":{"source":"none"}},zone=zone)
                with self.lock:
                    self.last_at,self.count,self.error=at,len(points),""
            except (OSError,ValueError,subprocess.SubprocessError,sqlite3.Error):
                with self.lock:
                    self.error="Brak raportu WLAN. Sprawdź na laptopie kartę Wi-Fi i uprawnienia lokalizacji Windows."
            cancel.wait(30)

    def dispatch(self,path,data,phone):
        if path=="/radio":
            if data.get("action")=="start":
                return self.start(data,phone)
            if data.get("action")!="stop":
                raise ValueError("Nieznana operacja czujnika.")
            self.stop()
            return self.status()
        if path=="/sample":
            with closing(sqlite3.connect(":memory:")) as db,db:
                wifi.initialize(db)
                db.commit()
                room_demo.run(db,True)
                return inspection.snapshot(db)["cases"][0]
        if path=="/service-test":
            if not self.service_lock.acquire(blocking=False):
                raise ValueError("Próba usług już trwa.")
            try:
                result=demo_lab.run(data.get("scenario","changed"))
                with closing(sqlite3.connect(self.database)) as db,db:
                    db.execute("INSERT OR REPLACE INTO discovery_demo VALUES (1,?)",(json.dumps(result),))
                return result
            finally:
                self.service_lock.release()
        with closing(sqlite3.connect(self.database)) as db:
            if path=="/service-report":
                return demo_lab.last_run(db)
            cases=inspection.snapshot(db)["cases"]
            return {"radio":self.status(),"cases":[{k:c[k] for k in ("id","ssid","bssid","expected","security","zones","at","reason")}
                    for c in cases if not c["closed"]]}

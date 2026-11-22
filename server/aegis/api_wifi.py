"""Wi-Fi radar API: the team's known routers (baseline) and the phones' reports that raise red flags."""
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from . import db as store
from . import agents
from . import dojo
from . import wifi


class RouterIn(BaseModel):
    bssid: str = Field(..., examples=["aa:bb:cc:dd:ee:ff"])
    ssid: str = Field("", max_length=64)
    security: str | None = Field(None, max_length=30, examples=["WPA2-PERSONAL"])
    frequency: int | None = Field(None, ge=0, le=100000, description="MHz, z niej wynika kanał.")
    rssi: int | None = Field(None, ge=-130, le=0, description="Siła sygnału w dBm.")
    lat: float | None = Field(None, ge=-90, le=90)
    lon: float | None = Field(None, ge=-180, le=180)
    radius: float | None = Field(None, ge=0, le=10000, description="Niepewność położenia w metrach.")


class KnownIn(BaseModel):
    agent_id: str | None = Field(None, description="Identyfikator telefonu (agenta), który zapamiętuje.")
    routers: list[RouterIn] = Field(..., max_length=2000)
    replace: bool = Field(False, description="Usuń znane routery, których nie ma na tej liście.")


class KnownPatch(BaseModel):
    ssid: str | None = Field(None, max_length=64)
    security: str | None = Field(None, max_length=30)
    note: str | None = Field(None, max_length=2000, description="Notatka (przechowywana zaszyfrowana).")


class SeenIn(RouterIn):
    hotspot: bool = Field(False, description="Telefon uznał go za hotspot (nazwa telefonu, losowy MAC).")


class Position(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    acc: float = Field(..., ge=0, description="Dokładność GPS w metrach.")


class ReportIn(BaseModel):
    agent_id: str | None = Field(None, description="Identyfikator telefonu (agenta); trafia do każdej czerwonej flagi.")
    position: Position | None = None
    aps: list[SeenIn] = Field(..., max_length=2000, description="Routery słyszane teraz (z oszacowanym położeniem, jeśli jest).")


def register(app: FastAPI, conn, cipher) -> None:
    @app.get("/api/wifi/known", tags=["wi-fi"], summary="Znane routery Wi-Fi (wspólna mapa drużyny)")
    def known():
        db = conn()
        try:
            items = wifi.known_list(db, cipher)
            return {"total": len(items), "routers": items}
        finally:
            db.close()

    @app.post("/api/wifi/known", tags=["wi-fi"], summary="Zapamiętaj routery jako znane (z położeniem, kanałem i sygnałem)")
    def remember(request: Request, body: KnownIn):
        db = conn()
        try:
            user = getattr(request.state, "user", None) or {}
            agent = agents.active(db, body.agent_id, user.get("username"))
            return wifi.remember(db, [r.model_dump() for r in body.routers], user.get("username"), body.replace, agent)
        finally:
            db.close()

    @app.patch("/api/wifi/known/{mac}", tags=["wi-fi"], summary="Popraw nazwę, zabezpieczenie albo notatkę")
    def edit(mac: str, body: KnownPatch):
        mac = wifi.bssid(mac)
        values = body.model_dump(exclude_none=True)
        if not values:
            raise ValueError("Nic do zmiany. Pola: ssid, security, note.")
        if "note" in values:
            values["note"] = cipher.seal(values["note"], "wifi_known.note")
        if "security" in values:
            values["security"] = values["security"].upper()
        values["updated_at"] = store.now()
        db = conn()
        try:
            if not db.execute(f"UPDATE wifi_known SET {', '.join(k + '=?' for k in values)} WHERE bssid=?",
                              (*values.values(), mac)).rowcount:
                raise HTTPException(404, "Nie ma takiego znanego routera.")
            return wifi.known_row(db.execute("SELECT * FROM wifi_known WHERE bssid=?", (mac,)).fetchone(), cipher)
        finally:
            db.close()

    @app.delete("/api/wifi/known/{mac}", tags=["wi-fi"], summary="Usuń router ze znanych")
    def forget(mac: str):
        mac = wifi.bssid(mac)
        db = conn()
        try:
            if not db.execute("DELETE FROM wifi_known WHERE bssid=?", (mac,)).rowcount:
                raise HTTPException(404, "Nie ma takiego znanego routera.")
            return {"deleted": mac}
        finally:
            db.close()

    @app.post("/api/wifi/report", tags=["wi-fi"],
              summary="Telefon zgłasza, co słyszy; serwer porównuje ze znanymi i tworzy czerwone flagi (alerty)")
    def report(request: Request, body: ReportIn):
        db = conn()
        try:
            user = getattr(request.state, "user", None) or {}
            agent = agents.active(db, body.agent_id, user.get("username"))
            result = wifi.report(db, cipher, [a.model_dump() for a in body.aps],
                                 body.position.model_dump() if body.position else None, agent)
            s = store.settings(db)
            if result["flags"] and s.get("dojo_auto") and s.get("dojo_url"):
                try:
                    dojo.send(db, cipher, min_priority=int(s["dojo_min_priority"]), only_unsent=True)
                except dojo.DojoError as error:
                    result["dojo_error"] = str(error)
            return result
        finally:
            db.close()

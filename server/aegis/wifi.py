"""Known Wi-Fi routers (the team's baseline) and red flags from the phones' radar.

A phone saves "everything I see now is ours" as the baseline. Later each phone reports what it hears;
the server compares it with the baseline and raises red flags as findings, so they show up in the
Alerts tab and can go to DefectDojo like any other finding:

    wifi-nowy          a router or hotspot that is not in the baseline              P4
    wifi-evil-twin     the name of a known network on an unknown BSSID              P5
    wifi-zabezpieczenie a known BSSID with weaker security than remembered          P5
    wifi-przesuniety   a known BSSID estimated far from where it was remembered     P4
    wifi-zniknal       a known router not heard 3 times in a row right next to it   P4

A Wi-Fi finding has ip = BSSID, port = 0 and proto = one of the kinds above.
"""
import json
import math
import re

from . import db as store

MAC = re.compile(r"^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$")
KINDS = {"wifi-nowy": 4, "wifi-evil-twin": 5, "wifi-zabezpieczenie": 5, "wifi-przesuniety": 4, "wifi-zniknal": 4}
GOOD_GPS_M = 30          # phone positions worse than this are not used for "moved" or "gone"
MOVED_EXTRA_M = 50       # moved = farther apart than both radii plus this
GONE_NEAR_M = 25         # "gone" counts only when the phone stands this close to the remembered spot
GONE_AFTER = 3           # reports in a row without the router

# Same order as the phone's Radio.kt strength(): higher is stronger.
STRENGTH = {"OPEN": 0, "WEP": 1, "OWE": 2, "WPA-PERSONAL": 2, "WPA2-PERSONAL": 3, "WPA3-PERSONAL": 4,
            "WPA-ENTERPRISE": 4, "WPA2-ENTERPRISE": 5, "802.1X": 5, "WPA3-ENTERPRISE": 6}


def is_wifi(finding: dict) -> bool:
    return str(finding.get("proto", "")).startswith("wifi")


def bssid(value: str) -> str:
    mac = str(value).strip().lower().replace("-", ":")
    if not MAC.match(mac):
        raise ValueError(f"To nie jest BSSID (adres MAC): {value!r}.")
    return mac


def metres(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Flat-earth distance, fine at building and campus scale (same as the phone)."""
    y = (lat2 - lat1) * 110_540.0
    x = (lon2 - lon1) * 111_320.0 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(x, y)


def weaker(seen: str | None, remembered: str | None) -> bool:
    a, b = STRENGTH.get((seen or "").upper(), -1), STRENGTH.get((remembered or "").upper(), -1)
    return a >= 0 and b >= 0 and a < b


def name(ssid: str | None) -> str:
    return f"„{ssid}”" if ssid else "(ukryta sieć)"


# ---- baseline ------------------------------------------------------------------------

def known_row(row, cipher) -> dict:
    out = dict(row)
    out["note"] = cipher.open(out["note"], "wifi_known.note")
    return out


def known_list(db, cipher) -> list[dict]:
    return [known_row(r, cipher) for r in db.execute("SELECT * FROM wifi_known ORDER BY ssid, bssid")]


def remember(db, routers: list[dict], user: str | None, replace: bool = False, agent_id: str | None = None) -> dict:
    """Saves routers as known. An existing one gets the new position and signal; its note stays."""
    clean = []
    for r in routers:
        clean.append({"bssid": bssid(r["bssid"]), "ssid": (r.get("ssid") or "").strip()[:64],
                      "security": (r.get("security") or "").upper()[:30] or None, "frequency": r.get("frequency"),
                      "rssi": r.get("rssi"), "lat": r.get("lat"), "lon": r.get("lon"), "radius": r.get("radius")})
    if not clean and not replace:
        raise ValueError("Brak routerów do zapamiętania.")
    moment = store.now()
    db.execute("BEGIN IMMEDIATE")
    removed = 0
    if replace:
        keep = [r["bssid"] for r in clean]
        removed = db.execute(f"DELETE FROM wifi_known WHERE bssid NOT IN ({','.join('?' * len(keep)) or 'NULL'})",
                             keep).rowcount
    added = 0
    for r in clean:
        added += 0 if db.execute("SELECT 1 FROM wifi_known WHERE bssid=?", (r["bssid"],)).fetchone() else 1
        db.execute(
            "INSERT INTO wifi_known (bssid, ssid, security, frequency, rssi, lat, lon, radius, added_by, created_at, "
            "updated_at, last_seen, agent_id) VALUES (:bssid, :ssid, :security, :frequency, :rssi, :lat, :lon, :radius, :user, "
            ":at, :at, :at, :agent) ON CONFLICT(bssid) DO UPDATE SET ssid=excluded.ssid, agent_id=COALESCE(excluded.agent_id, agent_id), "
            "security=COALESCE(excluded.security, security), frequency=COALESCE(excluded.frequency, frequency), "
            "rssi=COALESCE(excluded.rssi, rssi), lat=COALESCE(excluded.lat, lat), lon=COALESCE(excluded.lon, lon), "
            "radius=COALESCE(excluded.radius, radius), updated_at=excluded.updated_at, last_seen=excluded.last_seen, "
            "missed=0", {**r, "user": user, "at": moment, "agent": agent_id})
    # remembering a router answers its "new" and "evil twin" flags
    if clean:
        marks = ",".join("?" * len(clean))
        db.execute(f"UPDATE findings SET state='gone' WHERE ip IN ({marks}) AND proto IN ('wifi-nowy','wifi-evil-twin')",
                   [r["bssid"] for r in clean])
    db.execute("COMMIT")
    return {"saved": len(clean), "added": added, "updated": len(clean) - added, "removed": removed}


# ---- red flags ------------------------------------------------------------------------

def _flag(db, cipher, mac: str, kind: str, title: str, reasons: list[str], ssid: str | None, seen: str,
          agent_id: str | None = None) -> bool:
    """Creates or refreshes one Wi-Fi finding. True when it is new or came back (worth announcing)."""
    priority = KINDS[kind]
    tech = [{"name": "Wi-Fi", "version": ssid or None, "category": "sieć bezprzewodowa"}]
    why = [{"signature": kind, "message": m, "priority": priority, "evidence": mac} for m in reasons]
    auto = {"technologies": tech, "tags": ["wifi", kind], "priority": priority, "title": title, "reasons": why}
    sealed = {"auto": cipher.seal(json.dumps(auto), "findings.auto"),
              "technologies": cipher.seal(json.dumps(tech), "findings.technologies"),
              "reasons": cipher.seal(json.dumps(why), "findings.reasons"),
              "title": cipher.seal(title, "findings.title")}
    row = db.execute("SELECT id, state, edited FROM findings WHERE ip=? AND port=0 AND proto=?", (mac, kind)).fetchone()
    if row is None:
        db.execute("INSERT INTO findings (ip, port, proto, state, auto, technologies, tags, priority, title, reasons, "
                   "first_seen, last_seen, agent_id) VALUES (?, 0, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   (mac, kind, sealed["auto"], sealed["technologies"], json.dumps(["wifi", kind]), priority,
                    sealed["title"], sealed["reasons"], seen, seen, agent_id))
        return True
    edited = set(json.loads(row["edited"]))
    values = {"state": "active", "auto": sealed["auto"], "technologies": sealed["technologies"],
              "reasons": sealed["reasons"], "last_seen": seen}
    if "title" not in edited:
        values["title"] = sealed["title"]
    db.execute(f"UPDATE findings SET {', '.join(k + '=?' for k in values)} WHERE id=?", (*values.values(), row["id"]))
    return row["state"] != "active"


def _settle(db, mac: str, kind: str) -> None:
    db.execute("UPDATE findings SET state='gone' WHERE ip=? AND port=0 AND proto=? AND state='active'", (mac, kind))


def report(db, cipher, aps: list[dict], position: dict | None, agent_id: str | None = None) -> dict:
    """Compares what one phone hears now with the baseline. Returns the flags that are new or came back."""
    known = {r["bssid"]: r for r in db.execute("SELECT * FROM wifi_known")}
    if not known:
        return {"baseline": 0, "flags": [], "message": "Brak zapamiętanych routerów: najpierw „Zapamiętaj wszystkie jako znane”."}
    by_ssid: dict[str, list] = {}
    for r in known.values():
        if r["ssid"]:
            by_ssid.setdefault(r["ssid"], []).append(r)
    seen_at, raised, heard = store.now(), [], set()
    good_fix = position and position.get("acc") is not None and float(position["acc"]) <= GOOD_GPS_M

    db.execute("BEGIN IMMEDIATE")
    for ap in aps:
        mac, ssid = bssid(ap["bssid"]), (ap.get("ssid") or "").strip()
        security = (ap.get("security") or "").upper() or None
        heard.add(mac)
        here = (ap.get("lat"), ap.get("lon"))
        remembered = known.get(mac)
        flags = []
        if remembered:
            db.execute("UPDATE wifi_known SET last_seen=?, missed=0 WHERE bssid=?", (seen_at, mac))
            _settle(db, mac, "wifi-zniknal")
            if weaker(security, remembered["security"]):
                flags.append(("wifi-zabezpieczenie", f"Znany router {name(ssid)} ma słabsze zabezpieczenie",
                              [f"Zapamiętano {remembered['security']}, teraz {security}.",
                               "Ktoś mógł zmienić ustawienia albo podszywa się pod ten BSSID."]))
            if None not in here and remembered["lat"] is not None and (ap.get("radius") or 999) <= GOOD_GPS_M * 2:
                gap = metres(remembered["lat"], remembered["lon"], *here)
                limit = (remembered["radius"] or 0) + (ap.get("radius") or 0) + MOVED_EXTRA_M
                if gap > limit:
                    flags.append(("wifi-przesuniety", f"Znany router {name(ssid)} jest w innym miejscu",
                                  [f"Jest ok. {gap:.0f} m od zapamiętanego miejsca (limit {limit:.0f} m).",
                                   "Router mógł zostać przeniesiony albo ktoś podstawił kopię."]))
                else:
                    _settle(db, mac, "wifi-przesuniety")
        elif ssid and ssid in by_ssid:
            twins = by_ssid[ssid]
            why = [f"Nazwa {name(ssid)} należy do znanej sieci, ale ten BSSID nie jest zapamiętany.",
                   f"Znane BSSID tej sieci: {', '.join(t['bssid'] for t in twins[:4])}."]
            if any(weaker(security, t["security"]) for t in twins):
                why.append(f"Do tego słabsze zabezpieczenie ({security}).")
            flags.append(("wifi-evil-twin", f"Możliwy evil twin sieci {name(ssid)}", why))
        else:
            what = "hotspot" if ap.get("hotspot") else "router"
            why = [f"Nowy {what} {name(ssid)} nie jest na liście znanych.",
                   f"Sygnał {ap.get('rssi')} dBm, zabezpieczenie {security or 'nieznane'}."]
            flags.append(("wifi-nowy", f"Nowy {what} {name(ssid)}", why))
        for kind, title, why in flags:
            if _flag(db, cipher, mac, kind, title, why, ssid, seen_at, agent_id):
                raised.append({"bssid": mac, "ssid": ssid, "kind": kind, "priority": KINDS[kind], "title": title})

    if good_fix:
        lat, lon = float(position["lat"]), float(position["lon"])
        for mac, r in known.items():
            if mac in heard or r["lat"] is None or metres(r["lat"], r["lon"], lat, lon) > GONE_NEAR_M:
                continue
            missed = r["missed"] + 1
            db.execute("UPDATE wifi_known SET missed=? WHERE bssid=?", (missed, mac))
            if missed >= GONE_AFTER:
                title = f"Znany router {name(r['ssid'])} zniknął"
                why = [f"Telefon stał {GONE_NEAR_M} m od niego i nie słyszał go {missed} razy z rzędu.",
                       "Router mógł zostać wyłączony, zabrany albo uszkodzony."]
                if _flag(db, cipher, mac, "wifi-zniknal", title, why, r["ssid"], seen_at, agent_id):
                    raised.append({"bssid": mac, "ssid": r["ssid"], "kind": "wifi-zniknal", "priority": 4, "title": title})
    db.execute("COMMIT")
    return {"baseline": len(known), "flags": raised}

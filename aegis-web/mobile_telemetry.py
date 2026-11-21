"""Receive a paired Android sensor report in one SQLite transaction."""
import math
import re
import time
from datetime import datetime, timezone

import wifi
from position import valid_point


def security_mode(flags):
    tags = flags.upper()
    if "EAP" in tags or "802.1X" in tags:
        return "WPA3-ENTERPRISE" if "SUITE_B" in tags else "WPA2-ENTERPRISE"
    if "SAE" in tags:
        return "WPA3-PERSONAL"
    if "PSK" in tags:
        return "WPA2-PERSONAL" if "RSN" in tags or "WPA2" in tags else "WPA-PERSONAL"
    if "OWE" in tags:
        return "OWE"
    if "WEP" in tags:
        return "WEP"
    return "OPEN" if tags == "OPEN" or re.fullmatch(r"(?:\[(?:ESS|WPS)\])+", tags) else "UNKNOWN"


def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError("Niepoprawny parametr radiowy.")
    return value


PLACE_PART = re.compile(r"[\w .\/-]{0,40}")


def place(value):
    """Optional {"building","floor","room"} from the phone. Building is required when present."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("Niepoprawne miejsce.")
    parts = {k: value.get(k, "") for k in ("building", "floor", "room")}
    if any(not isinstance(v, str) or not PLACE_PART.fullmatch(v.strip()) for v in parts.values()):
        raise ValueError("Niepoprawne miejsce.")
    parts = {k: v.strip() for k, v in parts.items()}
    return parts if parts["building"] else None


def save(db, session, data, scoped):
    sensor = data.get("sensor_id")
    if not isinstance(sensor, str) or not re.fullmatch(r"[\w.-]{1,64}", sensor):
        raise ValueError("Niepoprawny identyfikator telefonu.")
    fix = data["location"]
    ts, provider = fix["timestamp"], fix.get("provider")
    if type(ts) not in (int, float) or not math.isfinite(ts):
        raise ValueError("Niepoprawny czas pozycji.")
    room = place(fix.get("place"))
    if provider == "gps":
        accuracy = fix["accuracy"]
        if (not valid_point(fix["lat"], fix["lng"]) or type(accuracy) not in (int, float)
                or not 0 <= accuracy <= 50):
            raise ValueError("Wymagana pozycja GPS o dokładności do 50 m.")
    elif provider == "manual":
        # Indoors GPS rarely reaches 50 m: the phone reports building / floor / room instead.
        if room is None:
            raise ValueError("Pozycja ręczna wymaga budynku.")
        accuracy = None
    else:
        raise ValueError("Wymagana pozycja GPS albo miejsce ręczne.")
    rows = data["networks"]
    if not isinstance(rows, list) or len(rows) > 512:
        raise ValueError("Niepoprawna lista punktów Wi-Fi.")
    radios = {}
    for row in rows:
        ssid, mac, flags = row["ssid"], row["bssid"].lower(), row["security"]
        if scoped and ssid != "AGH-LAB":
            raise ValueError("Tunel obsługuje tylko AGH-LAB.")
        if not isinstance(ssid, str) or len(ssid.encode()) > 32 or not re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", mac):
            raise ValueError("Niepoprawny SSID lub BSSID.")
        if not isinstance(flags, str) or len(flags) > 512:
            raise ValueError("Niepoprawny opis zabezpieczeń.")
        signal = integer(row["rssi_dbm"], -127, 0)
        frequency = integer(row["frequency_mhz"], 2000, 75000)
        channel = row.get("channel")
        if channel is not None:
            integer(channel, 1, 255)
        age = integer(row.get("age_ms", 0), 0, 2**53)
        if age <= 15000 and (mac not in radios or signal > radios[mac][2]):
            radios[mac] = (ssid, flags, signal, frequency, channel, age)

    db.execute("BEGIN IMMEDIATE")
    table = "location_fixes" if provider == "gps" else "place_fixes"
    previous = db.execute(f"SELECT scan_id FROM {table} WHERE session=? AND sensor=? AND device_time=?",
                          (session, sensor, ts)).fetchone()
    if previous:
        return {"scan_id": previous[0], "duplicate": True}
    if not 0 <= time.time() - ts <= 15:
        raise ValueError("Pozycja GPS ma więcej niż 15 sekund lub pochodzi z przyszłości.")
    at = datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="microseconds")
    if provider == "gps":
        location = {"source": "paired_phone_gnss", "accuracy": accuracy, "at": at}
        point = {"lat": fix["lat"], "lng": fix["lng"], "location": location}
    else:
        location = {"source": "paired_phone_manual", "at": at}
        point = {"location": location}
    if room:
        location["place"] = room
    points = [(values[0], mac, security_mode(values[1]), None, values[4]) for mac, values in radios.items()]
    saved = wifi.save_scan(db, session, "AGH-LAB" if scoped else "Android Wi-Fi", points, at, point,
                           sensor=sensor, default_policy={"AGH-LAB": {"802.1X"}},
                           radio_signature=sorted((mac,*values[:-1]) for mac,values in radios.items()))
    scan = saved["id"]
    if provider == "gps":
        db.execute("INSERT INTO location_fixes VALUES (?,?,?,?,?,?,?,?)",
                   (scan, session, sensor, ts, fix["lat"], fix["lng"], accuracy, location["source"]))
    else:
        db.execute("INSERT INTO place_fixes VALUES (?,?,?,?,?,?,?)",
                   (scan, session, sensor, ts, room["building"], room["floor"], room["room"]))
    db.executemany("UPDATE wifi_observations SET rssi_dbm=?,frequency_mhz=?,capabilities=?,radio_age_ms=? "
                   "WHERE scan_id=? AND bssid=?", ((v[2], v[3], v[1], v[5], scan, mac) for mac, v in radios.items()))
    result = {"scan_id": scan, "points": saved["added"], "new_cases": saved["new_cases"]}
    if provider == "gps":
        result["location"] = point
    if room:
        result["place"] = room
    return result

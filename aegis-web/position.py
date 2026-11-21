"""Validate provenance independently of the time of the network observation."""
import math
import time
from datetime import datetime, timezone


def valid_point(lat, lng):
    return (type(lat) in (float, int) and -85 <= lat <= 85
            and type(lng) in (float, int) and -180 <= lng <= 180)


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("Brak czasu pozycji.")
    date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if date.tzinfo is None or not 0 <= date.timestamp() <= time.time() + 300:
        raise ValueError("Niepoprawny czas pozycji.")
    return date.astimezone(timezone.utc).isoformat()


def distance(value):
    if type(value) not in (float, int) or not math.isfinite(value) or value < 0:
        raise ValueError("Niepoprawna odległość.")
    return value


def metadata(data):
    lat, lng = data.get("lat"), data.get("lng")
    located = lat is not None or lng is not None
    if located and not valid_point(lat, lng):
        raise ValueError("Niepoprawne współrzędne.")
    info = data.get("location", {})
    if not isinstance(info, dict):
        raise ValueError("Niepoprawny opis pozycji.")
    source = info.get("source", "unknown" if located else "none")
    if source not in ("unknown", "none", "browser", "manual") or (source == "none") == located:
        raise ValueError("Źródło pozycji nie odpowiada współrzędnym.")
    result = {"source": source}
    if source == "none" and "reason" in info:
        if info["reason"] not in ("outside_area", "uncertain_area", "unconfirmed"):
            raise ValueError("Niepoprawny powód pominięcia lokalizacji.")
        result["reason"] = info["reason"]
    if source in ("manual", "browser"):
        result["at"] = timestamp(info.get("at"))
    if source == "browser":
        result["accuracy"] = distance(info.get("accuracy"))
    if source == "manual":
        label = info.get("station", "")
        if not isinstance(label, str) or len(label) > 120:
            raise ValueError("Nazwa stanowiska może mieć do 120 znaków.")
        result["station"] = label.strip()
        if "conflict" in info:
            check = info["conflict"]
            if not isinstance(check, dict):
                raise ValueError("Niepoprawny zapis rozbieżności.")
            result["conflict"] = {"at": timestamp(check.get("at")), "meters": distance(check.get("meters")),
                                  "accuracy": distance(check.get("accuracy"))}
    return result

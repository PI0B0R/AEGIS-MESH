"""Export the Windows WLAN report for one SSID to an AEGIS-MESH CSV."""
import argparse
import csv
import os
import re
import subprocess
from datetime import datetime, timezone


def security_mode(label):
    label = label.upper().strip()
    if label in {"OPEN", "OTWARTE", "OTWARTA", "OTWARTY"}:
        return "OPEN"
    if label in {"OWE", "WEP"}:
        return label
    match = re.search(r"WPA([23]?)", label)
    if match:
        if any(word in label for word in ("ENTERPRISE", "FIRM", "802.1X")):
            return match[0] + "-ENTERPRISE"
        if any(word in label for word in ("PERSONAL", "OSOB", "PSK", "SAE")):
            return match[0] + "-PERSONAL"
    return "UNKNOWN"


def parse_report(report, wanted):
    ssid = None
    authentication = encryption = ""
    pending = None
    for line in report.splitlines():
        network = re.match(r"^\s*SSID\s+\d+\s*:\s?(.*)$", line, re.I)
        if network:
            if pending:
                yield tuple(pending)
                pending = None
            ssid = network[1]
            authentication = encryption = ""
            continue
        if ssid != wanted:
            continue
        key, _, value = line.strip().partition(":")
        key = key.strip().lower()
        if key.startswith(("authenticat", "uwierzyteln")):
            authentication = value.strip()
        elif key in ("encryption", "szyfrowanie"):
            encryption = value.strip()
        elif re.fullmatch(r"bssid\s+\d+", key):
            if pending:
                yield tuple(pending)
            pending = None
            address = value.strip().lower()
            if re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", address):
                mode = "WEP" if "WEP" in encryption.upper() else security_mode(authentication)
                pending = [ssid, address, mode, None, None]
        elif pending and key in ("signal", "sygnał"):
            match = re.fullmatch(r"(\d{1,3})\s*%", value.strip())
            if match and int(match[1]) <= 100:
                pending[3] = int(match[1])
        elif pending and key in ("channel", "kanał") and value.strip().isdecimal():
            pending[4] = int(value)
    if pending:
        yield tuple(pending)


def read_network(ssid):
    report = subprocess.run(["netsh", "wlan", "show", "networks", "mode=bssid"],
                            capture_output=True, timeout=10)
    if report.returncode:
        raise ValueError("Windows nie udostępnił raportu WLAN. Sprawdź kartę Wi-Fi i uprawnienia lokalizacji.")
    return list(parse_report(report.stdout.decode("oem", errors="replace"), ssid))


def connected_ssids():
    """Read associated network names, never saved profiles or nearby networks."""
    result = subprocess.run(["netsh", "wlan", "show", "interfaces"], capture_output=True, timeout=8)
    report = (result.stdout + result.stderr).decode("oem", errors="replace")
    if result.returncode or any(word in report.casefold() for word in
                                ("access is denied", "odmowa dostępu", "location permission", "uprawnień lokalizacji")):
        raise ValueError("Windows nie udostępnił nazwy sieci. Sprawdź dostęp do lokalizacji lub wpisz SSID ręcznie.")
    names = []
    for line in report.splitlines():
        field, separator, value = line.partition(":")
        if separator and field.strip().upper() == "SSID":
            name = value.removeprefix(" ")
            if name.strip() and name not in names:
                names.append(name)
    return names


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ("ssid", "sensor", "output"):
        parser.add_argument("--" + arg, required=True)
    parser.add_argument("--lat", type=float, required=True)
    parser.add_argument("--lng", type=float, required=True)
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("Eksporter wymaga systemu Windows.")
    if not (-85 <= args.lat <= 85 and -180 <= args.lng <= 180):
        parser.error("Niepoprawne współrzędne miejsca odczytu.")
    try:
        observations = read_network(args.ssid)
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        parser.exit(1, f"Odczyt WLAN nie powiódł się: {error}\n")
    if not observations:
        parser.exit(1, "Brak pasujących odczytów. Sprawdź SSID, dostęp do WLAN i język raportu (PL/EN).\n")
    timestamp = datetime.now(timezone.utc).isoformat()
    try:
        with open(args.output, "x", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(("ssid", "bssid", "security", "sensor", "at", "lat", "lng"))
            for measurement in observations:
                writer.writerow((*measurement[:3], args.sensor, timestamp, args.lat, args.lng))
    except FileExistsError:
        parser.exit(1, "Plik już istnieje. Podaj nową nazwę wyniku.\n")
    print(f"Zapisano {len(observations)} obserwacji do {args.output}")

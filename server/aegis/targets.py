"""Scan targets: single IPs, CIDR ranges, IP ranges (a-b) and domain names.

Everything the user gives (form, pasted text, CSV, JSON) goes through `parse_any`,
so every input path is processed the same way (task point 11).
"""
import csv
import io
import ipaddress
import json
import re
from dataclasses import dataclass
from typing import Iterable, Iterator

MAX_TARGET_SIZE = 1 << 20          # one entry may expand to at most ~1M addresses (a /12)
DOMAIN = re.compile(r"^(?=.{1,253}$)(?!-)([a-z0-9-]{1,63}(?<!-)\.)+[a-z]{2,63}$")
TARGET_COLUMNS = ("target", "cel", "address", "adres", "ip", "host", "cidr", "domain", "domena", "range", "zakres")
NOTE_COLUMNS = ("note", "notatka", "opis", "description", "comment")


@dataclass(frozen=True)
class Target:
    kind: str           # ip | cidr | range | domain
    value: str          # normalised form
    size: int           # number of addresses it expands to
    note: str | None = None


def classify(raw: str, note: str | None = None) -> Target:
    text = raw.strip().strip(",;").lower()
    if not text:
        raise ValueError("Pusty cel.")
    if text.startswith(("http://", "https://")):          # a pasted URL: keep only the host name
        text = text.split("://", 1)[1].split("/", 1)[0].split(":", 1)[0]
    if "/" in text:
        try:
            net = ipaddress.ip_network(text, strict=False)
        except ValueError:
            raise ValueError(f"Niepoprawny zakres CIDR: {raw}") from None
        if net.num_addresses == 1:
            return Target("ip", str(net.network_address), 1, note)
        size = net.num_addresses - (2 if net.version == 4 and net.prefixlen < 31 else 0)
        _check_size(raw, size)
        return Target("cidr", str(net), size, note)
    if "-" in text and re.fullmatch(r"[0-9a-f.:]+-[0-9a-f.:]+", text):
        first, last = text.split("-", 1)
        try:
            start = ipaddress.ip_address(first)
            end = ipaddress.ip_address(last) if ("." in last or ":" in last) else \
                ipaddress.ip_address(".".join(first.split(".")[:3] + [last]))
        except ValueError:
            raise ValueError(f"Niepoprawny zakres adresów: {raw}") from None
        if start.version != end.version or int(end) < int(start):
            raise ValueError(f"Niepoprawny zakres adresów: {raw}")
        size = int(end) - int(start) + 1
        _check_size(raw, size)
        return Target("range", f"{start}-{end}", size, note)
    try:
        return Target("ip", str(ipaddress.ip_address(text)), 1, note)
    except ValueError:
        pass
    text = text.rstrip(".")
    if DOMAIN.match(text):
        return Target("domain", text, 1, note)
    raise ValueError(f"Nie rozpoznano celu: {raw}")


def _check_size(raw: str, size: int) -> None:
    if size > MAX_TARGET_SIZE:
        raise ValueError(f"Zakres {raw} jest za duży (ponad {MAX_TARGET_SIZE} adresów). Podziel go na mniejsze.")


def parse_text(text: str) -> list[Target]:
    targets = []
    for line in text.splitlines():
        line = line.split("#", 1)[0]
        for token in re.split(r"[\s,;]+", line):
            if token:
                targets.append(classify(token))
    return targets


def parse_csv(content: str) -> list[Target]:
    rows = list(csv.reader(io.StringIO(content.lstrip("﻿"))))
    rows = [r for r in rows if any(cell.strip() for cell in r)]
    if not rows:
        return []
    header = [cell.strip().lower() for cell in rows[0]]
    target_col = next((header.index(c) for c in TARGET_COLUMNS if c in header), None)
    note_col = next((header.index(c) for c in NOTE_COLUMNS if c in header), None)
    if target_col is None:              # no header: first column is the target
        target_col, body = 0, rows
    else:
        body = rows[1:]
    out = []
    for row in body:
        if target_col < len(row) and row[target_col].strip() and not row[target_col].strip().startswith("#"):
            note = row[note_col].strip() if note_col is not None and note_col < len(row) else None
            out.append(classify(row[target_col], note or None))
    return out


def parse_json(content: str) -> list[Target]:
    data = json.loads(content)
    if isinstance(data, dict):
        data = data.get("targets", data.get("cele"))
    if not isinstance(data, list):
        raise ValueError("JSON musi być listą celów albo obiektem z polem \"targets\".")
    out = []
    for item in data:
        if isinstance(item, str):
            out.append(classify(item))
        elif isinstance(item, dict):
            value = next((item[k] for k in TARGET_COLUMNS if k in item), None)
            note = next((item[k] for k in NOTE_COLUMNS if k in item), None)
            if not isinstance(value, str):
                raise ValueError(f"Brak pola z adresem w obiekcie: {item}")
            out.append(classify(value, note if isinstance(note, str) else None))
        else:
            raise ValueError(f"Niepoprawny element listy: {item!r}")
    return out


def parse_any(content: str, filename: str | None = None) -> list[Target]:
    """Detects the format by file extension, then by content."""
    name = (filename or "").lower()
    stripped = content.lstrip("﻿ \n\r\t")
    if name.endswith(".json") or stripped[:1] in "[{":
        return parse_json(stripped)
    first = stripped.splitlines()[0] if stripped else ""
    if name.endswith(".csv") or any(c.strip().lower() in TARGET_COLUMNS for c in first.split(",")):
        return parse_csv(stripped)
    try:
        return parse_text(stripped)
    except ValueError:
        if "," in first:            # CSV without a header, e.g. "10.0.0.1,serwer pocztowy"
            return parse_csv(stripped)
        raise


def expand(kind: str, value: str) -> Iterator[str]:
    """Addresses to scan. Domains are passed to nmap by name."""
    if kind in ("ip", "domain"):
        yield value
    elif kind == "cidr":
        net = ipaddress.ip_network(value)
        yield from (str(a) for a in (net.hosts() if net.num_addresses > 2 else net))
    elif kind == "range":
        first, last = (ipaddress.ip_address(v) for v in value.split("-"))
        for n in range(int(first), int(last) + 1):
            yield str(ipaddress.ip_address(n))


def expand_all(targets: Iterable[tuple[str, str]]) -> list[str]:
    seen, out = set(), []
    for kind, value in targets:
        for address in expand(kind, value):
            if address not in seen:
                seen.add(address)
                out.append(address)
    return out


def chunks(items: list[str], size: int) -> list[list[str]]:
    size = max(1, size)
    return [items[i:i + size] for i in range(0, len(items), size)]

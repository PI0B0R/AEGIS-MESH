"""Scope radar: compares the prefixes an autonomous system (ASN) announces in BGP with the target list.

The announced prefixes come from public RIPEstat data (announced-prefixes, built from the NLRI that
RIPE RIS route collectors see). This is the only place where AEGIS MESH asks an outside service, and only
when a user presses "Sprawdź" or runs `aegis scope bgp`. Only the ASN number is sent. Nothing is scanned here.

Without internet the last good answer (kept in settings) is shown and marked as stale.
"""
import ipaddress
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Iterable

from .targets import MAX_TARGET_SIZE

RIPESTAT = "https://stat.ripe.net/data/announced-prefixes/data.json"
TIMEOUT = 15


class BgpUnavailable(RuntimeError):
    """RIPEstat could not be reached or gave an answer we cannot use."""


def parse_asn(value) -> int:
    text = str(value).strip().upper()
    if text.startswith("AS"):
        text = text[2:]
    if not text.isdigit() or not 1 <= int(text) <= 4294967295:
        raise ValueError(f"Niepoprawny numer ASN: {value}. Podaj np. 8501 albo AS8501.")
    return int(text)


def _download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "aegis-mesh", "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310 - fixed https URL
        return response.read()


def fetch_prefixes(asn: int, download: Callable[[str], bytes] = _download) -> list[str]:
    """Prefixes announced by the ASN, as normalised CIDR strings, sorted."""
    url = RIPESTAT + "?" + urllib.parse.urlencode({"resource": f"AS{asn}", "sourceapp": "aegis-mesh"})
    try:
        raw = download(url)
    except (urllib.error.URLError, OSError, TimeoutError) as error:
        raise BgpUnavailable(f"Brak połączenia z RIPEstat ({getattr(error, 'reason', error)}).") from None
    try:
        data = json.loads(raw)
        if data.get("status") not in (None, "ok"):
            raise BgpUnavailable(f"RIPEstat odpowiedział: {data.get('message') or data.get('status')}.")
        items = data["data"]["prefixes"]
        nets = {ipaddress.ip_network(item["prefix"], strict=False) for item in items}
    except BgpUnavailable:
        raise
    except (ValueError, KeyError, TypeError):
        raise BgpUnavailable("RIPEstat zwrócił odpowiedź w nieznanym formacie.") from None
    return [str(n) for n in sorted(nets, key=lambda n: (n.version, n.network_address, n.prefixlen))]


def _interval(kind: str, value: str) -> tuple[int, int, int] | None:
    """(ip version, first, last) for an address target; None for a domain."""
    if kind in ("ip", "cidr"):
        net = ipaddress.ip_network(value, strict=False)
        return net.version, int(net.network_address), int(net.broadcast_address)
    if kind == "range":
        first, last = (ipaddress.ip_address(v) for v in value.split("-"))
        return first.version, int(first), int(last)
    return None


def _covered(net, intervals: list[tuple[int, int, int]]) -> int:
    """How many addresses of the prefix lie inside the targets (overlaps counted once)."""
    lo, hi = int(net.network_address), int(net.broadcast_address)
    parts = sorted((max(a, lo), min(b, hi)) for v, a, b in intervals if v == net.version and a <= hi and b >= lo)
    total, end = 0, lo - 1
    for a, b in parts:
        if b <= end:
            continue
        total += b - max(a, end + 1) + 1
        end = b
    return total


def importable(prefix: str) -> str | None:
    """Why a prefix cannot become a target, or None when it can."""
    net = ipaddress.ip_network(prefix)
    if net.num_addresses > MAX_TARGET_SIZE:
        return f"za duży (ponad {MAX_TARGET_SIZE} adresów), podziel go ręcznie"
    return None


def compare(asn: int, prefixes: Iterable[str], targets: Iterable[dict]) -> dict:
    """Announced prefixes vs targets.

    targets: rows with kind, value, enabled (only enabled ones count as coverage).
    Returns each prefix with its coverage, the targets outside the announced space,
    and the list of missing prefixes that can be imported in one step.
    """
    nets = [ipaddress.ip_network(p) for p in prefixes]
    rows = list(targets)
    intervals = [iv for r in rows if r.get("enabled", True) and (iv := _interval(r["kind"], r["value"]))]

    out_prefixes = []
    for net in nets:
        covered = _covered(net, intervals)
        state = "pokryty" if covered == net.num_addresses else ("częściowo" if covered else "brak")
        out_prefixes.append({"prefix": str(net), "version": net.version, "addresses": net.num_addresses,
                             "covered": covered, "percent": round(100 * covered / net.num_addresses, 1),
                             "state": state, "blocked": importable(str(net))})

    # Missing prefixes to import: skip ones inside another missing prefix that will be imported too.
    missing = [ipaddress.ip_network(p["prefix"]) for p in out_prefixes if p["state"] == "brak" and not p["blocked"]]
    to_import = [str(n) for n in missing if not any(o != n and o.version == n.version and n.subnet_of(o) for o in missing)]

    announced = {v: list(ipaddress.collapse_addresses(n for n in nets if n.version == v)) for v in (4, 6)}
    outside = []
    for r in rows:
        iv = _interval(r["kind"], r["value"])
        if iv is None:
            outside.append({"value": r["value"], "kind": r["kind"], "reason": "domena: sprawdź jej adres ręcznie"})
            continue
        version, first, last = iv
        inside = sum(max(0, min(last, int(n.broadcast_address)) - max(first, int(n.network_address)) + 1)
                     for n in announced[version])
        if inside < last - first + 1:
            outside.append({"value": r["value"], "kind": r["kind"],
                            "reason": "poza ogłaszaną przestrzenią" if inside == 0 else "częściowo poza ogłaszaną przestrzenią"})

    counts = {s: sum(1 for p in out_prefixes if p["state"] == s) for s in ("pokryty", "częściowo", "brak")}
    return {"asn": asn, "prefixes": out_prefixes, "outside": outside, "to_import": to_import, "counts": counts}

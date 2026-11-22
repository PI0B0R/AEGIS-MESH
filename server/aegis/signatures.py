"""Signatures and rules: what a service is (technology, version) and why it matters (tags, priority 1-5).

A signature only *detects* a feature in data we already have (headers, cookies, HTML,
favicon hash, title, nmap banner) or in the answer to one GET of a path. It never logs
in and never sends payloads. Built-in signatures live in signatures.json; custom ones
are stored in the database and have the same fields.
"""
import json
import re
from dataclasses import dataclass
from dataclasses import field as dc_field
from pathlib import Path

KINDS = ("header", "cookie", "html", "title", "favicon", "path", "service", "tls")
CATEGORIES = ("serwer", "serwer aplikacji", "framework", "cms", "devops", "panel admina", "staging", "iot",
              "baza danych", "usługa", "inne")
BUILTIN_FILE = Path(__file__).with_name("signatures.json")
DEFAULT_PATH_STATUS = (200, 401, 403)


@dataclass
class Signature:
    id: str
    name: str
    kind: str
    pattern: str = ""
    field: str | None = None
    category: str = "inne"
    priority: int = 1
    tags: list = dc_field(default_factory=list)
    message: str = ""
    status: list | None = None
    builtin: bool = True
    enabled: bool = True

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"Nieznany rodzaj sygnatury: {self.kind}. Dozwolone: {', '.join(KINDS)}.")
        if not 1 <= int(self.priority) <= 5:
            raise ValueError("Priorytet sygnatury musi być od 1 do 5.")
        if self.kind in ("header", "path") and not self.field:
            raise ValueError("Sygnatura nagłówka wymaga nazwy nagłówka, a sygnatura ścieżki samej ścieżki (np. /admin).")
        if self.kind == "path" and not str(self.field).startswith("/"):
            raise ValueError("Ścieżka musi zaczynać się od /, np. /admin.")
        if self.kind == "favicon":
            self.pattern = str(self.pattern).strip()
            if not re.fullmatch(r"-?\d+|[0-9a-f]{32}", self.pattern):
                raise ValueError("Sygnatura favicony to hash mmh3 (liczba, jak w Shodanie) albo md5.")
        else:
            try:
                re.compile(self.pattern)
            except re.error as error:
                raise ValueError(f"Niepoprawne wyrażenie regularne: {error}") from None
        self.priority = int(self.priority)
        self.field = self.field.lower() if self.kind == "header" and self.field else self.field

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


@dataclass
class Match:
    signature: str
    name: str
    category: str
    version: str | None
    priority: int
    tags: list
    message: str
    evidence: str


def builtin() -> list[Signature]:
    return [Signature(**item) for item in json.loads(BUILTIN_FILE.read_text(encoding="utf-8"))]


def from_row(row) -> Signature:
    data = dict(row)
    data["tags"] = json.loads(data.get("tags") or "[]")
    data["status"] = json.loads(data["status"]) if data.get("status") else None
    data["builtin"], data["enabled"] = False, bool(data.get("enabled", 1))
    return Signature(**{k: v for k, v in data.items() if k in Signature.__dataclass_fields__})


def load_all(db) -> list[Signature]:
    custom = [from_row(r) for r in db.execute("SELECT * FROM signatures ORDER BY id")]
    return builtin() + [s for s in custom if s.enabled]


def paths_to_probe(signatures: list[Signature]) -> list[str]:
    return list(dict.fromkeys(s.field for s in signatures if s.kind == "path" and s.enabled))


def _search(pattern: str, text: str | None):
    if not text:
        return None
    best = None
    for found in re.finditer(pattern, text, re.I | re.S):
        if best is None:
            best = found
        if found.groups() and any(found.groups()):
            return found
        if found.end() - found.start() == 0 and found.end() >= len(text):
            break
    return best


def _version(found) -> str | None:
    if found and found.groups():
        return next((g for g in found.groups() if g), None)
    return None


def match(sig: Signature, service: dict, web: dict | None) -> Match | None:
    """service: {name, product, version, extrainfo}; web: WebInfo.to_dict() or None."""
    found, evidence = None, ""
    web = web or {}
    if sig.kind == "service":
        text = " ".join(str(service.get(k) or "") for k in ("name", "product", "version", "extrainfo")).strip()
        found, evidence = _search(sig.pattern, text), text
    elif not web or web.get("status") is None:
        return None
    elif sig.kind == "header":
        value = (web.get("headers") or {}).get(sig.field)
        found, evidence = _search(sig.pattern, value), f"{sig.field}: {value}"
    elif sig.kind == "cookie":
        for cookie in web.get("cookies") or []:
            found = _search(sig.pattern, cookie.get("name"))
            if found:
                evidence = f"Set-Cookie: {cookie.get('name')}"
                break
    elif sig.kind == "html":
        found = _search(sig.pattern, web.get("body"))
        evidence = found.group(0)[:120] if found else ""
    elif sig.kind == "title":
        found, evidence = _search(sig.pattern, web.get("title")), f"<title>{web.get('title')}</title>"
    elif sig.kind == "favicon":
        wanted = sig.pattern
        if wanted in (str(web.get("favicon_mmh3")), web.get("favicon_md5")):
            return Match(sig.id, sig.name, sig.category, None, sig.priority, list(sig.tags), sig.message,
                         f"favicon {wanted}")
        return None
    elif sig.kind == "tls":
        tls = web.get("tls") or {}
        text = " ".join(str(tls.get(k) or "") for k in ("subject", "issuer")) + " " + " ".join(tls.get("san") or [])
        found, evidence = (_search(sig.pattern, text), text.strip()) if tls else (None, "")
    elif sig.kind == "path":
        page = (web.get("paths") or {}).get(sig.field)
        if not page or page.get("status") not in (sig.status or DEFAULT_PATH_STATUS):
            return None
        if page.get("status") in (301, 302, 303, 307, 308):
            return None
        found = _search(sig.pattern, page.get("body") or "") if sig.pattern else None
        if sig.pattern and not found:
            return None
        evidence = f"GET {sig.field} → {page.get('status')}" + (f" ({page.get('title')})" if page.get("title") else "")
        return Match(sig.id, sig.name, sig.category, _version(found), sig.priority, list(sig.tags), sig.message, evidence)
    if not found:
        return None
    return Match(sig.id, sig.name, sig.category, _version(found), sig.priority, list(sig.tags), sig.message,
                 evidence[:200])


# ---- rules that are not a single pattern ------------------------------------------
def rules(web: dict | None, matches: list[Match]) -> list[Match]:
    out = []
    if not web or web.get("status") is None:
        return out
    tls, scheme = web.get("tls"), web.get("scheme")
    login = any("logowanie" in m.tags or "panel-admina" in m.tags for m in matches)
    if login and scheme == "http":
        out.append(Match("rule:login-http", "Logowanie bez HTTPS", "panel admina", None, 5, ["logowanie-bez-https"],
                         "Panel logowania bez HTTPS: hasła idą otwartym tekstem.", web.get("url", "")))
    if tls and tls.get("expired"):
        out.append(Match("rule:cert-expired", "Wygasły certyfikat", "inne", None, 3, ["certyfikat"],
                         "Certyfikat TLS wygasł.", f"ważny do {tls.get('not_after')}"))
    elif tls and tls.get("self_signed"):
        out.append(Match("rule:cert-self-signed", "Certyfikat self-signed", "inne", None, 2, ["certyfikat"],
                         "Certyfikat TLS podpisany sam przez siebie.", f"wystawca: {tls.get('issuer')}"))
    for cookie in web.get("cookies") or []:
        name = cookie.get("name") or ""
        if re.search(r"sess|token|auth|sid", name, re.I) and not cookie.get("httponly"):
            out.append(Match("rule:cookie-httponly", "Ciasteczko sesji bez HttpOnly", "inne", None, 2, ["ciasteczka"],
                             f"Ciasteczko {name} bez flagi HttpOnly.", f"Set-Cookie: {name}"))
            break
    if scheme == "https" and any(not c.get("secure") for c in web.get("cookies") or []):
        out.append(Match("rule:cookie-secure", "Ciasteczko bez Secure", "inne", None, 2, ["ciasteczka"],
                         "Ciasteczko na HTTPS bez flagi Secure.", ""))
    return out


@dataclass
class Analysis:
    technologies: list
    tags: list
    priority: int
    title: str
    reasons: list


def analyse(service: dict, web: dict | None, signatures: list[Signature]) -> Analysis:
    matches = [m for m in (match(s, service, web) for s in signatures if s.enabled) if m]
    matches += rules(web, matches)
    technologies, seen = [], {}
    for m in matches:
        if m.signature.startswith("rule:") or m.name in ("Generator strony",):
            continue
        if m.name in seen:
            if m.version and not technologies[seen[m.name]]["version"]:
                technologies[seen[m.name]]["version"] = m.version
            continue
        seen[m.name] = len(technologies)
        technologies.append({"name": m.name, "version": m.version, "category": m.category})
    tags = sorted({t for m in matches for t in m.tags})
    ranked = sorted(matches, key=lambda m: -m.priority)
    priority = ranked[0].priority if ranked else 1
    label = (service.get("product") or service.get("name") or "usługa")
    title = ranked[0].message if ranked and ranked[0].priority >= 2 else f"Usługa {label}."
    reasons = [{"signature": m.signature, "message": m.message, "priority": m.priority, "evidence": m.evidence}
               for m in ranked]
    return Analysis(technologies, tags, priority, title, reasons)

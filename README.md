# AEGIS MESH

System do regularnego skanowania dużych sieci (np. sieci uczelni): znajduje hosty i usługi,
rozpoznaje technologie, nadaje tagi i priorytet 1–5 z uzasadnieniem po polsku
i po każdym skanie pokazuje, co się zmieniło. Działa lokalnie w Dockerze, bez chmury i bez AI,
na darmowych narzędziach open source.

![Pulpit panelu](docs/img/pulpit.png)

## Szybki start

```bash
AEGIS_ADMIN_PASSWORD='dlugie-haslo-admina' docker compose up -d --build
./demo/pokaz.sh              # cały scenariusz krok po kroku na sieci testowej
```

Panel: http://127.0.0.1:8000 (login `admin`), API: http://127.0.0.1:8000/docs.

## Dokumenty

| | |
|---|---|
| [docs/DOKUMENTACJA.md](docs/DOKUMENTACJA.md) | dokumentacja techniczna: architektura, bezpieczeństwo, dane, role, API, testy |
| [server/README.md](server/README.md) | obsługa krok po kroku: cele, skany, harmonogram, sygnatury, DefectDojo, eksport |
| [demo/pokaz.sh](demo/pokaz.sh) | pokaz dla jury: 5 skanów ze zmianami, edycje, role, sygnatury, eksport i import |
| [ZAMYSL_PROJEKTU.md](ZAMYSL_PROJEKTU.md) | pierwotny zamysł projektu |

## Katalogi

- `server/`: serwer (Python, FastAPI, SQLite, nmap) i panel WWW
- `lab/`: strony sieci testowej z `docker-compose.yml`
- `demo/`: skrypt pokazu
- `docs/`: dokumentacja i zrzuty ekranu
- `android-app/`, `aegis-web/`: wcześniejsze prototypy (aplikacja na telefon to etap 5)

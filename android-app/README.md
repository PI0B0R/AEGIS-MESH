# AEGIS Mesh na Androidzie

Telefon jest osobnym czujnikiem. Jego moduł GPS podaje miejsce odbioru sygnału. Laptop nadal może równolegle prowadzić własne pomiary.

## Instalacja i pierwszy pomiar

1. Na laptopie uruchom z katalogu `aegis-web` polecenie `python server.py --mobile-test --port 8772`. Jeżeli ten proces już działa, wystarczy otworzyć [panel parowania](http://127.0.0.1:8772/). Ta instancja zapisuje dane w tymczasowej bazie. Dla trwałej historii uruchom serwer z osobną bazą przez `--db` zamiast `--mobile-test`.
2. W sekcji **Aplikacja telefonu · parowanie i kod QR** wybierz Cloudflare albo adres sieci LAN. Potwierdź parowanie, wygeneruj zaproszenie i odczytaj URL oraz ośmiocyfrowy PIN. Każda aplikacja wymaga osobnego parowania. Nowe zaproszenie w tej samej instancji zastępuje poprzednią sesję.
3. Zainstaluj plik APK na Androidzie. Wybierz **Połącz**, potem **Skanuj QR** i zeskanuj kod z panelu laptopa. Możesz też wkleić adres sesji telefonu (może zawierać /phone-beacon), podać świeży PIN i wybrać **Połącz**. Po wymianie jednorazowego PIN-u aplikacja przechowuje w prywatnych preferencjach adres i klucz sesji. Panel WebView korzysta z tej samej sesji.
4. Włącz w systemie lokalizację i Wi-Fi. Zezwól aplikacji na dokładną lokalizację i dostęp do urządzeń w pobliżu, a następnie wybierz **Uruchom czujnik**. W budynku ustaw najpierw **Miejsce**. Najłatwiej uzyskać pierwszy odczyt GPS na zewnątrz. Telefon zachowuje ostatni przyjęty punkt na mapie z czasem odczytu.
5. Sprawdź komunikat **HTTP 200**, dokładność i liczbę wysłanych punktów. W panelu laptopa otwórz historię skanów Wi-Fi. Dwa punkty dostępowe z jednej paczki powinny mieć jeden identyfikator pomiaru i wspólną pinezkę czujnika. Zatrzymaj pomiar przyciskiem w aplikacji lub z powiadomienia Androida.

## Co robi aplikacja

Telefon jest czujnikiem radiowym, nie skanerem sieci. Widzi punkty dostępowe (SSID, BSSID, siła sygnału, zabezpieczenie), a nie komputery w sieci. Hosty wykrywa skaner na serwerze.

- **Lista sieci w pobliżu.** Każdy punkt ma status: *Podejrzany* (znana nazwa z obcym BSSID i słabszym zabezpieczeniem, czyli możliwy evil twin, albo znany BSSID ze słabszym zabezpieczeniem), *Hotspot* (nazwa typu iPhone, AndroidAP, DIRECT-, MAC lokalnie administrowany), *Nowy* (brak na liście znanych) i *Znany*. Logika jest w `Radio.kt` i ma testy w `app/src/test`.
- **Znane routery.** Przycisk wczytuje plik CSV `ssid,bssid,security`, ten sam format co `aegis-web/static/examples/inventory.csv`.
- **Czujnik w tle.** Usługa pierwszoplanowa co 30 s prosi Androida o skan (system pozwala na około 4 skany na 2 minuty), wysyła tylko nowe wyniki i pokazuje powiadomienie o każdym nowym podejrzanym punkcie lub hotspocie. Każda instalacja ma własny identyfikator `PHONE-XXXXXXXX`.
- **Miejsce.** W budynku GPS rzadko daje dokładność do 50 m. Wtedy raport dostaje budynek, piętro i salę wpisane ręcznie albo odczytane z kodu QR na drzwiach. Kody do druku generuje strona `/place-qr.html` serwera (treść kodu: `aegis-place:B1/2/215`). Przy dobrym GPS wysyłane są współrzędne i dodatkowo miejsce.
- **Ciepło-zimno.** Dotknięcie punktu na liście otwiera ekran z siłą sygnału na żywo. Idź tam, gdzie liczba rośnie. Na demo wyłącz w opcjach programisty „Ograniczanie skanowania Wi-Fi”, wtedy odczyt przychodzi co kilka sekund.
- **Panel laptopa.** Mapa i alerty z serwera są w widoku WebView pod przyciskiem „Panel laptopa”.

## Zasady pomiaru

- Pozycja GPS (`provider: gps`) musi mieć dokładność do 50 m i wiek do 15 s. Bez niej telefon wysyła `provider: manual` z polem `place` (`building`, `floor`, `room`). Serwer zapisuje miejsce w tabeli `place_fixes`, a w historii skanów pokazuje „Miejsce podane w telefonie”.
- Tunel przyjmuje tylko SSID **AGH-LAB**. W prywatnym LAN przesyłane są wszystkie świeże wyniki.
- Wersja **release** łączy się tylko przez HTTPS z certyfikatem zaufanym systemowo. Wersja **debug** (`src/debug/res/xml/network_security_config.xml`) pozwala też na HTTP w LAN i na lokalny certyfikat AEGIS-phone-ca.crt zainstalowany przez użytkownika. Tryb HTTP przesyła dane i klucz otwartym tekstem, więc używaj go tylko w zaufanej sieci testowej.
- Serwer telefonu udostępnia `POST /exchange` i `POST /api/mobile-telemetry`. Żądanie zawiera nagłówki `Origin`, `Content-Type: application/json` i `Authorization: Bearer …`. Ten sam czas pozycji wysłany drugi raz nie powiela zapisu.
- Kod QR skanuje Google Code Scanner (usługi Google Play, bez uprawnienia do aparatu). Ten sam przycisk rozpoznaje kod parowania z laptopa i naklejkę z miejscem.

## Kompilacja

Projekt wymaga JDK 17, Android SDK Platform 35 i Android SDK Build-Tools 34.0.0. Testy logiki radiowej uruchomisz poleceniem `.\gradlew.bat testDebugUnitTest`. Otwórz folder `android-app` w Android Studio albo ustaw `ANDROID_HOME` i wykonaj na Windows z tego katalogu:

```powershell
.\gradlew.bat assembleDebug
```

Gradle zapisuje podpisany kluczem debug plik w `app/build/outputs/apk/debug/app-debug.apk`. Pierwsza kompilacja pobiera zależności. Pakiet można również zainstalować z komputera poleceniem `adb install -r app/build/outputs/apk/debug/app-debug.apk`.

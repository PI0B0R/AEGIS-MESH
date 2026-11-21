# AEGIS Mesh na Androidzie

Telefon jest osobnym czujnikiem. Jego moduł GPS podaje miejsce odbioru sygnału. Laptop nadal może równolegle prowadzić własne pomiary.

## Instalacja i pierwsze połączenie

1. Na laptopie uruchom w katalogu `aegis-web` polecenie `python server.py` i otwórz [panel](http://127.0.0.1:8765). Laptop i telefon muszą być w tej samej sieci Wi-Fi. Przy pierwszym uruchomieniu Windows może zapytać o zaporę: wybierz **Zezwól**.
2. Na stronie panelu jest karta **Dodaj telefon** z dwoma kodami QR. Kod **Pobierz** zeskanuj aparatem telefonu i zainstaluj APK.
3. Otwórz aplikację, dotknij **Zeskanuj kod z laptopa** i zeskanuj kod **Połącz**. Zgódź się na lokalizację i powiadomienia. Czujnik włącza się sam, a karta na laptopie pokazuje „Telefon połączony”.

Kod **Połącz** zawiera adres laptopa, jednorazowy token i odcisk SHA-256 certyfikatu laptopa. Aplikacja ufa tylko temu certyfikatowi, więc połączenie jest szyfrowane bez instalowania certyfikatu na telefonie. Laptop sam wybiera adres w sieci lokalnej. Sesja trwa 12 godzin. Drugi telefon dodasz przyciskiem **Dodaj inny telefon** (zastępuje poprzedni). Stare opcje parowania (tunel, PIN, wybór adresu) są w panelu pod **Zaawansowane**.

## Co robi aplikacja

Telefon jest czujnikiem radiowym, nie skanerem sieci. Widzi punkty dostępowe (SSID, BSSID, siła sygnału, zabezpieczenie), a nie komputery w sieci. Hosty wykrywa skaner na serwerze.

- **Lista sieci w pobliżu.** Każdy punkt ma status: *Podejrzany* (znana nazwa z obcym BSSID i słabszym zabezpieczeniem, czyli możliwy evil twin, albo znany BSSID ze słabszym zabezpieczeniem), *Hotspot* (nazwa typu iPhone, AndroidAP, DIRECT-, MAC lokalnie administrowany), *Nowy* (brak na liście znanych) i *Znany*. Logika jest w `Radio.kt` i ma testy w `app/src/test`.
- **Znane routery.** Aplikacja pobiera je z inwentarza laptopa po połączeniu i przy każdym starcie czujnika. Można też wczytać plik CSV `ssid,bssid,security` (⋯ Więcej).
- **Czujnik w tle.** Usługa pierwszoplanowa co 30 s prosi Androida o skan (system pozwala na około 4 skany na 2 minuty), wysyła tylko nowe wyniki i pokazuje powiadomienie o każdym nowym podejrzanym punkcie lub hotspocie. Każda instalacja ma własny identyfikator `PHONE-XXXXXXXX`.
- **Miejsce.** W budynku GPS rzadko daje dokładność do 50 m. Wtedy raport dostaje budynek, piętro i salę wpisane ręcznie albo odczytane z kodu QR na drzwiach. Kody do druku generuje strona `/place-qr.html` serwera (treść kodu: `aegis-place:B1/2/215`). Przy dobrym GPS wysyłane są współrzędne i dodatkowo miejsce.
- **Radar i ciepło-zimno.** Ekran główny ma czarno-złoty radar: każdy router to ping, im bliżej środka, tym silniejszy sygnał (kąt tylko rozsuwa pingi, Wi-Fi nie podaje kierunku). Dotknięcie pinga albo wiersza listy otwiera stożek ciepło-zimno: daleko jest szeroki i czerwony, blisko wąski i zielony. Siła sygnału jest wygładzana, bo pojedyncze odczyty skaczą o kilka dB. Dotknięcie pinga na górze stożka włącza tryb szybki (skan co 2 s). Android pozwala aplikacji na 4 skany w 2 minuty, więc żeby odczyt naprawdę przychodził co 2 s, wyłącz w opcjach programisty „Ograniczanie skanowania Wi-Fi”.
- **Panel laptopa.** Mapa i alerty z serwera są w widoku WebView w menu ⋯ Więcej → Panel laptopa.

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

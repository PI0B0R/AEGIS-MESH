"""The web panel in a real browser (Chromium via Playwright), against the docker compose lab.

Needs AEGIS_URL, AEGIS_ADMIN_PASSWORD (the admin password the server was started with) and
at least one finished scan (run after test_lab.py, which is the order pytest uses).
"""
import os

import httpx
import pytest

URL = os.environ.get("AEGIS_URL")
ADMIN = os.environ.get("AEGIS_ADMIN_PASSWORD")
playwright = pytest.importorskip("playwright.sync_api")
pytestmark = [pytest.mark.lab, pytest.mark.skipif(not (URL and ADMIN), reason="needs AEGIS_URL and AEGIS_ADMIN_PASSWORD")]


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as p:
        path = os.environ.get("AEGIS_CHROMIUM")
        b = p.chromium.launch(**({"executable_path": path, "args": ["--no-sandbox"]} if path else {}))
        yield b
        b.close()


@pytest.fixture(scope="module")
def reader():
    token = httpx.post(f"{URL}/api/auth/login", json={"username": "admin", "password": ADMIN}).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    httpx.post(f"{URL}/api/users", json={"username": "jury-czytelnik", "password": "czytelnik-haslo-9", "role": "czytelnik"},
               headers=headers)
    return "jury-czytelnik", "czytelnik-haslo-9"


def open_panel(browser, username, password):
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(URL + "/")
    page.fill("#u", username)
    page.fill("#p", password)
    page.click("button[type=submit]")
    page.wait_for_selector("#nav a")
    return page, errors


def test_admin_sees_five_screens_and_can_edit_a_finding(browser):
    page, errors = open_panel(browser, "admin", ADMIN)
    assert page.locator("#nav a").all_inner_texts() == ["Pulpit", "Cele", "Hosty", "Sygnatury", "Ustawienia"]
    page.wait_for_selector(".tile")
    assert page.locator(".tile .n").count() == 3 and page.get_by_role("button", name="Skanuj teraz").is_visible()
    assert page.locator(".news li").count() >= 1

    page.goto(URL + "/#hosty")
    page.wait_for_selector("tbody tr.click")
    page.fill("#szukaj", "172.30.0.24")
    assert page.locator("tbody tr.click").count() == 1
    page.locator("tbody tr.click").first.click()
    page.wait_for_selector("text=Certyfikat")
    page.get_by_role("button", name="Edytuj").first.click()
    page.select_option("select[id^=p-]", "2")
    page.fill("textarea[id^=n-]", "sprawdzone w przeglądarce")
    page.get_by_role("button", name="Zapisz").click()
    page.wait_for_selector(".badge:text('edytowane')")
    assert page.locator("text=Notatka: sprawdzone w przeglądarce").is_visible()

    for screen, text in (("cele", "Wklej adresy"), ("sygnatury", "Nowa sygnatura"), ("ustawienia", "Użytkownicy i role")):
        page.goto(f"{URL}/#{screen}")
        page.wait_for_selector(f"text={text}")
    assert not errors, errors
    page.close()


def test_reader_sees_no_buttons_they_cannot_use(browser, reader):
    page, errors = open_panel(browser, *reader)
    assert "Ustawienia" not in page.locator("#nav a").all_inner_texts()
    page.wait_for_selector(".tile")
    assert page.get_by_role("button", name="Skanuj teraz").count() == 0
    page.goto(URL + "/#cele")
    page.wait_for_selector("text=Lista celów")
    assert page.get_by_role("button", name="Dodaj").count() == 0 and page.get_by_role("button", name="Usuń").count() == 0
    page.goto(URL + "/#hosty/172.30.0.22")
    page.wait_for_selector("text=Usługi")
    assert page.get_by_role("button", name="Edytuj").count() == 0
    page.goto(URL + "/#ustawienia")                                   # typed by hand: sent back to the dashboard
    page.wait_for_selector(".tile")
    assert page.url.endswith("#pulpit")
    assert not errors, errors
    page.close()


def test_login_errors_are_shown(browser):
    page = browser.new_page()
    page.goto(URL + "/")
    page.fill("#u", "admin")
    page.fill("#p", "zle-haslo-xyz")
    page.click("button[type=submit]")
    page.wait_for_selector("text=Nieprawidłowy login lub hasło.")
    page.close()

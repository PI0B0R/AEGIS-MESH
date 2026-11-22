import pytest

from aegis import signatures as sigs

NGINX = {"name": "http", "product": "nginx", "version": "1.25.3", "extrainfo": None}


def page(**kw):
    base = {"url": "http://10.0.0.1/", "scheme": "http", "status": 200, "title": None, "headers": {}, "cookies": [],
            "body": "", "paths": {}, "tls": None, "favicon_mmh3": None, "favicon_md5": None}
    base.update(kw)
    return base


def run(service, web):
    return sigs.analyse(service, web, sigs.builtin())


def test_builtin_signatures_are_valid_and_unique():
    builtin = sigs.builtin()
    assert len(builtin) >= 40 and len({s.id for s in builtin}) == len(builtin)
    assert {"cms", "staging", "panel admina", "iot"} <= {s.category for s in builtin}


def test_server_header_with_version():
    result = run(NGINX, page(headers={"server": "nginx/1.25.3"}))
    assert {"name": "nginx", "version": "1.25.3", "category": "serwer"} in result.technologies
    assert result.priority == 1 and result.title == "Usługa nginx."


def test_wordpress_with_version_and_cms_tag():
    body = '<meta name="generator" content="WordPress 6.4.2"><link href="/wp-content/themes/x.css">'
    result = run(NGINX, page(body=body))
    wp = next(t for t in result.technologies if t["name"] == "WordPress")
    assert wp["version"] == "6.4.2" and "cms" in result.tags and result.priority == 3


def test_login_over_http_is_priority_5():
    result = run(NGINX, page(title="Logowanie", body="<input type=password name=p>"))
    assert result.priority == 5 and "logowanie-bez-https" in result.tags
    assert result.title.startswith("Panel logowania bez HTTPS")


def test_login_over_https_is_not_flagged_as_plaintext():
    result = run(NGINX, page(scheme="https", body="<input type='password'>", tls={"self_signed": False}))
    assert "logowanie-bez-https" not in result.tags and result.priority == 3


def test_staging_title_and_admin_path():
    result = run(NGINX, page(title="Staging - sklep", paths={"/admin/": {"status": 401, "body": "", "title": None}}))
    assert {"staging", "panel-admina", "logowanie-bez-https"} <= set(result.tags)
    reasons = {r["signature"] for r in result.reasons}
    assert {"staging-title", "admin-path"} <= reasons


def test_redirecting_path_is_not_a_panel():
    result = run(NGINX, page(paths={"/admin/": {"status": 302, "body": "", "title": None}}))
    assert "panel-admina" not in result.tags


def test_footer_version_cookie_and_iot():
    body = "<html><footer>Copyright Firma, wersja 3.2.1</footer></html>"
    result = run(NGINX, page(title="IP Camera", body=body, cookies=[{"name": "laravel_session", "httponly": True}]))
    names = {t["name"]: t["version"] for t in result.technologies}
    assert names["Wersja w stopce"] == "3.2.1" and "Laravel" in names and "iot" in result.tags


def test_service_signature_from_nmap_banner():
    result = run({"name": "redis", "product": "Redis key-value store", "version": "7.4.12"}, None)
    assert result.priority == 5 and "baza-danych" in result.tags
    assert {"name": "Redis", "version": "7.4.12", "category": "baza danych"} in result.technologies


def test_certificate_and_cookie_rules():
    result = run(NGINX, page(scheme="https", tls={"expired": True, "not_after": "2020-01-01"},
                             cookies=[{"name": "sessionid", "httponly": False, "secure": False}]))
    reasons = {r["signature"] for r in result.reasons}
    assert {"rule:cert-expired", "rule:cookie-httponly", "rule:cookie-secure"} <= reasons


def test_custom_favicon_and_header_signatures():
    favicon = sigs.Signature(id="moj", name="Moja aplikacja", kind="favicon", pattern="-1234", priority=4,
                             tags=["wewnetrzne"], message="Wewnętrzna aplikacja.")
    header = sigs.Signature(id="hdr", name="App", kind="header", field="X-App", pattern=r"app/([\d.]+)")
    web = page(favicon_mmh3=-1234, headers={"x-app": "app/1.2"})
    assert sigs.match(favicon, NGINX, web).priority == 4
    assert sigs.match(header, NGINX, web).version == "1.2"


@pytest.mark.parametrize("bad", [
    {"kind": "nope"}, {"kind": "header"}, {"kind": "path", "field": "admin"}, {"kind": "html", "pattern": "("},
    {"kind": "favicon", "pattern": "abc"}, {"kind": "title", "priority": 9},
])
def test_invalid_signatures_are_rejected(bad):
    with pytest.raises(ValueError):
        sigs.Signature(**{"id": "x", "name": "x", "pattern": "a", **bad})


def test_no_web_answer_means_only_service_signatures():
    assert run(NGINX, page(status=None, error="timeout")).technologies == []


def test_words_in_titles_are_not_reported_as_versions():
    result = run(NGINX, page(title="Panel testowy (staging)", body=""))
    staging = next(t for t in result.technologies if t["name"] == "Środowisko testowe")
    assert staging["version"] is None


def test_signature_ids_keep_polish_letters_readable():
    from aegis.api_results import _slug
    assert _slug("Blog Wydziału Łączności") == "blog-wydzialu-lacznosci"

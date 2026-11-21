import pytest

from aegis.targets import chunks, classify, expand_all, parse_any, parse_csv, parse_json, parse_text


def test_classify_kinds():
    assert classify("10.0.0.7").kind == "ip"
    assert classify(" 10.0.0.0/24 ").size == 254
    assert classify("10.0.0.5/24").value == "10.0.0.0/24"          # host bits are dropped
    assert classify("192.168.1.10-20").value == "192.168.1.10-192.168.1.20"
    assert classify("192.168.1.10-192.168.1.20").size == 11
    assert classify("https://Intranet.AGH.edu.pl/login").value == "intranet.agh.edu.pl"
    assert classify("10.0.0.1/32").kind == "ip"
    assert classify("2001:db8::1").kind == "ip"


@pytest.mark.parametrize("bad", ["", "300.1.1.1", "10.0.0.0/33", "not a host", "10.0.0.9-10.0.0.1", "10.0.0.0/8x"])
def test_classify_rejects(bad):
    with pytest.raises(ValueError):
        classify(bad)


def test_size_limit():
    classify("10.0.0.0/12")
    with pytest.raises(ValueError, match="za duży"):
        classify("10.0.0.0/11")


def test_text_with_separators_and_comments():
    found = parse_text("10.0.0.1, 10.0.0.2;example.com\n# komentarz\n10.0.1.0/30  # sieć lab")
    assert [t.value for t in found] == ["10.0.0.1", "10.0.0.2", "example.com", "10.0.1.0/30"]


def test_csv_with_header_and_notes():
    found = parse_csv("﻿adres,opis\n10.0.0.1,serwer www\n\n10.0.2.0/24,laboratorium\n")
    assert [(t.value, t.note) for t in found] == [("10.0.0.1", "serwer www"), ("10.0.2.0/24", "laboratorium")]


def test_csv_without_header_detected():
    found = parse_any("10.0.0.1,serwer pocztowy\n10.0.0.2,drukarka\n")
    assert [(t.value, t.note) for t in found] == [("10.0.0.1", None), ("10.0.0.2", None)]


def test_json_forms():
    assert [t.value for t in parse_json('["10.0.0.1", "example.org"]')] == ["10.0.0.1", "example.org"]
    found = parse_json('{"targets": [{"ip": "10.0.0.0/30", "note": "lab"}]}')
    assert (found[0].value, found[0].note, found[0].size) == ("10.0.0.0/30", "lab", 2)
    assert parse_any('[{"target": "10.0.0.9"}]', "cele.json")[0].value == "10.0.0.9"
    with pytest.raises(ValueError):
        parse_json('{"x": 1}')


def test_same_result_for_every_input_path():
    text = parse_any("10.1.0.0/30 example.com")
    csv = parse_any("target\n10.1.0.0/30\nexample.com\n", "cele.csv")
    js = parse_any('["10.1.0.0/30", "example.com"]')
    assert [t.value for t in text] == [t.value for t in csv] == [t.value for t in js]


def test_expand_dedupes_and_handles_10k():
    addresses = expand_all([("cidr", "10.0.0.0/18"), ("ip", "10.0.0.5"), ("range", "10.0.0.1-10.0.0.3"), ("domain", "x.pl")])
    assert len(addresses) == 16382 + 1
    assert addresses[0] == "10.0.0.1" and addresses[-1] == "x.pl"
    parts = chunks(addresses, 256)
    assert sum(map(len, parts)) == len(addresses) and max(map(len, parts)) == 256

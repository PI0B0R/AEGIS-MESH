import os
import stat

import pytest
from cryptography.exceptions import InvalidTag

from aegis.crypto import FieldCipher


def test_round_trip_and_column_binding(tmp_path):
    cipher = FieldCipher.load(tmp_path / "keys" / "k")
    token = cipher.seal("Apache httpd 2.4.49", "services.version")
    assert token.startswith("enc1:") and "Apache" not in token
    assert cipher.open(token, "services.version") == "Apache httpd 2.4.49"
    assert cipher.seal("Apache", "services.version") != cipher.seal("Apache", "services.version")   # random nonce
    with pytest.raises(InvalidTag):
        cipher.open(token, "targets.note")                       # moved to another column: refused
    assert cipher.seal(None, "x") is None and cipher.open("plain", "x") == "plain"


def test_key_file_is_private_and_stable(tmp_path):
    path = tmp_path / "keys" / "k"
    a = FieldCipher.load(path)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    b = FieldCipher.load(path)
    assert b.open(a.seal("x", "c"), "c") == "x"


def test_wrong_key_cannot_read(tmp_path):
    token = FieldCipher.load(tmp_path / "a").seal("tajne", "targets.note")
    with pytest.raises(InvalidTag):
        FieldCipher.load(tmp_path / "b").open(token, "targets.note")

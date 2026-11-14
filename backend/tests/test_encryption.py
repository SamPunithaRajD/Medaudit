import base64
import os

import pytest

from app.security.encryption import (KeyRing, TamperDetected, EncryptionError, decrypt, encrypt, parse_header,
                                     rotate_blob)


def ring(*versions, active=None):
    keys = {v: os.urandom(32) for v in versions}
    return KeyRing(keys, active or versions[-1])


def test_roundtrip_and_unique_nonce():
    r = ring("v1")
    a, b = encrypt(b"secret", b"ctx", r), encrypt(b"secret", b"ctx", r)
    assert a != b and parse_header(a)[1] != parse_header(b)[1]
    assert decrypt(a, b"ctx", r) == b"secret"


def test_tamper_detected():
    r = ring("v1")
    blob = bytearray(encrypt(b"patient record", b"doc", r))
    blob[-1] ^= 0x01
    with pytest.raises(TamperDetected):
        decrypt(bytes(blob), b"doc", r)


def test_wrong_aad_rejected():
    r = ring("v1")
    with pytest.raises(TamperDetected):
        decrypt(encrypt(b"x", b"doc-A", r), b"doc-B", r)


def test_malformed_and_unknown_version():
    r = ring("v1")
    with pytest.raises(TamperDetected):
        decrypt(b"garbage", None, r)
    other = ring("v9")
    with pytest.raises(EncryptionError):
        decrypt(encrypt(b"x", None, other), None, r)


def test_rotation():
    old = ring("v1")
    blob = encrypt(b"data", b"a", old)
    both = KeyRing({**old.keys, "v2": os.urandom(32)}, "v2")
    rotated = rotate_blob(blob, b"a", both)
    assert parse_header(rotated)[0] == "v2"
    assert decrypt(rotated, b"a", both) == b"data"


def test_encrypted_column_at_rest(db):
    from sqlalchemy import text
    from app.models import PatientCase
    c = PatientCase(patient_name="Fictional Person")
    db.add(c)
    db.commit()
    raw = db.execute(text("SELECT patient_name FROM patient_cases WHERE id=:i"), {"i": c.id}).scalar()
    assert "Fictional" not in raw and base64.b64decode(raw).startswith(b"MAE1")
    db.expire_all()
    assert db.get(PatientCase, c.id).patient_name == "Fictional Person"
    db.delete(db.get(PatientCase, c.id))
    db.commit()

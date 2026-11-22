"""Lab only: creates a self-signed certificate for the fake camera (runs once in the AEGIS image)."""
import datetime as dt
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

out = Path(sys.argv[1])
if (out / "kamera.crt").exists():
    sys.exit(0)
key = ec.generate_private_key(ec.SECP256R1())
name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "kamera.lab")])
now = dt.datetime.now(dt.timezone.utc)
cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now).not_valid_after(now + dt.timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("kamera.lab")]), critical=False)
        .sign(key, hashes.SHA256()))
(out / "kamera.key").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                   serialization.NoEncryption()))
(out / "kamera.crt").write_bytes(cert.public_bytes(serialization.Encoding.PEM))

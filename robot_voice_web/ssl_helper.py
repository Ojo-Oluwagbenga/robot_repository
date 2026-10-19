"""
SSL Helper for Nova Robot Voice Web App.
Automatically generates self-signed certificates with SANs (Subject Alternative Names)
covering localhost, 127.0.0.1, and all detected LAN IP addresses.
Enables HTTPS and WSS (Secure WebSockets), which are REQUIRED by modern mobile browsers
(iOS Safari, Android Chrome) to grant microphone access over local network IPs.
"""

import datetime
import ipaddress
import logging
import os
import socket
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger("robot.voice_web.ssl")

CERTS_DIR = Path(__file__).parent / "certs"


def get_local_ips() -> List[str]:
    """Retrieve all non-loopback IPv4 addresses of this host."""
    ips = ["127.0.0.1"]
    try:
        host_name = socket.gethostname()
        for ip in socket.gethostbyname_ex(host_name)[2]:
            if not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ext_ip = s.getsockname()[0]
        if ext_ip not in ips:
            ips.append(ext_ip)
        s.close()
    except Exception:
        pass
    return ips


def get_or_create_ssl_cert() -> Tuple[Path, Path]:
    """
    Returns (cert_path, key_path).
    If they do not exist, generates a self-signed X.509 certificate with SANs
    covering localhost and all active network IPs.
    """
    CERTS_DIR.mkdir(parents=True, exist_ok=True)
    cert_path = CERTS_DIR / "cert.pem"
    key_path = CERTS_DIR / "key.pem"

    if cert_path.exists() and key_path.exists():
        return cert_path, key_path

    logger.info("Generating self-signed SSL certificate for mobile HTTPS voice access...")

    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, "Nova Robot Voice Web"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Nova Robotics Companion"),
        ])

        # Add Subject Alternative Names for localhost and all local LAN IPs
        san_list = [x509.DNSName("localhost")]
        for ip_str in get_local_ips():
            try:
                san_list.append(x509.IPAddress(ipaddress.IPv4Address(ip_str)))
            except ValueError:
                pass

        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=730)) # 2 years
            .add_extension(x509.SubjectAlternativeName(san_list), critical=False)
            .sign(key, hashes.SHA256())
        )

        cert_bytes = cert.public_bytes(serialization.Encoding.PEM)
        key_bytes = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )

        cert_path.write_bytes(cert_bytes)
        key_path.write_bytes(key_bytes)
        logger.info(f"Generated SSL cert: {cert_path} and key: {key_path}")
        return cert_path, key_path

    except Exception as e:
        logger.error(f"Failed to generate self-signed certificate: {e}")
        raise

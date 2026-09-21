"""PIN-Pruefung.

Bewusst ein Hash und keine Verschluesselung: verschluesselt hiesse, dass
irgendwo ein Schluessel liegt, mit dem sich die PIN zurueckholen laesst -
und dann koennte, wer an Server und Schluessel kommt, auch die PIN lesen.
Ein Hash mit langsamer Schluesselableitung laesst sich pruefen, aber nicht
umkehren. Selbst mit vollem Datenbankzugriff bleibt nur Raten.

scrypt kommt aus der Standardbibliothek, es braucht also keine weitere
Abhaengigkeit. Die Parameter kosten rund 100 ms pro Pruefung - fuer eine
Anmeldung unmerklich, fuer das Durchprobieren von Millionen PINs teuer.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

MIN_LAENGE = 6

# n=2**14, r=8, p=1 ist die uebliche interaktive Einstellung.
_N = 2**14
_R = 8
_P = 1
_LAENGE = 32


class PinFehler(ValueError):
    """Die PIN genuegt den Anforderungen nicht."""


def pruefe_form(pin: str) -> str:
    pin = (pin or "").strip()
    if len(pin) < MIN_LAENGE:
        raise PinFehler(f"Die PIN braucht mindestens {MIN_LAENGE} Zeichen.")
    if len(pin) > 128:
        raise PinFehler("Die PIN ist zu lang (hoechstens 128 Zeichen).")
    if pin.isdigit() and len(set(pin)) < 3:
        raise PinFehler("Diese PIN ist zu einfach - bitte mehr verschiedene Zeichen.")
    return pin


def verschluesseln(pin: str) -> str:
    """Ergibt 'scrypt$<salz>$<hash>', beides hexadezimal."""
    pin = pruefe_form(pin)
    salz = secrets.token_bytes(16)
    abgeleitet = hashlib.scrypt(
        pin.encode("utf-8"), salt=salz, n=_N, r=_R, p=_P, dklen=_LAENGE
    )
    return f"scrypt${salz.hex()}${abgeleitet.hex()}"


def stimmt(pin: str, gespeichert: str | None) -> bool:
    if not gespeichert:
        return False
    try:
        verfahren, salz_hex, hash_hex = gespeichert.split("$")
        if verfahren != "scrypt":
            return False
        abgeleitet = hashlib.scrypt(
            (pin or "").strip().encode("utf-8"),
            salt=bytes.fromhex(salz_hex),
            n=_N,
            r=_R,
            p=_P,
            dklen=_LAENGE,
        )
    except (ValueError, TypeError):
        return False
    # Zeitkonstanter Vergleich: sonst verraet die Laufzeit, wie weit man
    # mit einem Rateversuch gekommen ist.
    return hmac.compare_digest(abgeleitet.hex(), hash_hex)

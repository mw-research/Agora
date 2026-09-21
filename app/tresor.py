"""Umschlagverschluesselung fuer die Modell-Zugaenge.

Das Problem: der Worker ruft Modelle auf, wenn niemand angemeldet ist. Er
braucht den Provider-Schluessel dafuer im Klartext. Ein Geheimnis, das eine
Maschine unbeaufsichtigt benutzt, laesst sich vor dem Root dieser Maschine
nicht verbergen - das ist keine Luecke, sondern die Eigenschaft des Problems.

Was sich aendern laesst, ist *wie lange*. Deshalb zwei Schluessel:

  Datenschluessel (DK)   verschluesselt die Modell-Zugaenge einer Person.
                         32 Zufallsbytes, entsteht mit der PIN.

  PIN-Schluessel (KEK)   verschluesselt den DK. Wird bei jeder Anmeldung aus
                         der PIN abgeleitet (scrypt) und nirgends gespeichert.

Im Ruhezustand liegt in der Datenbank nur der mit dem KEK verpackte DK. Ohne
die PIN kommt da niemand heran - auch kein Root, dem bliebe nur, die PIN zu
raten, und das kostet rund 100 ms pro Versuch.

Beim Anmelden wird der DK ausgepackt und fuer eine begrenzte Zeit hinterlegt,
damit der Worker in einem anderen Prozess ihn benutzen kann. **In diesem
Fenster - und nur dort - kaeme auch jemand mit Serverzugriff heran.** Danach
wird er geloescht.

Wer die PIN aendert, packt denselben DK nur neu ein; die Zugaenge bleiben.
Wer die PIN vergisst, verliert den DK - und damit die Zugaenge. Anders waere
es keine Sperre.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import datetime, timedelta

from cryptography.fernet import Fernet, InvalidToken

from .crypto import decrypt as server_entschluesseln
from .crypto import encrypt as server_verschluesseln

# Gleiche Haerte wie bei der PIN-Pruefung.
_N = 2**14
_R = 8
_P = 1


class GesperrtFehler(RuntimeError):
    """Der Datenschluessel ist gerade nicht verfuegbar."""


def _kek(pin: str, salz: bytes) -> Fernet:
    roh = hashlib.scrypt(
        (pin or "").strip().encode("utf-8"), salt=salz, n=_N, r=_R, p=_P, dklen=32
    )
    return Fernet(base64.urlsafe_b64encode(roh))


def neuer_datenschluessel(pin: str) -> tuple[str, str]:
    """Erzeugt einen frischen DK und verpackt ihn mit der PIN.

    Liefert (salz_hex, verpackt) zum Ablegen am Nutzer.
    """
    salz = secrets.token_bytes(16)
    dk = Fernet.generate_key()
    return salz.hex(), _kek(pin, salz).encrypt(dk).decode()


def auspacken(pin: str, salz_hex: str | None, verpackt: str | None) -> bytes:
    """Den DK mit der PIN oeffnen."""
    if not salz_hex or not verpackt:
        raise GesperrtFehler("Fuer dieses Konto gibt es keinen Datenschluessel.")
    try:
        return _kek(pin, bytes.fromhex(salz_hex)).decrypt(verpackt.encode())
    except (InvalidToken, ValueError) as exc:
        raise GesperrtFehler("Der Datenschluessel laesst sich mit dieser PIN nicht oeffnen.") from exc


def umpacken(alte_pin: str, neue_pin: str, salz_hex: str, verpackt: str) -> tuple[str, str]:
    """Bei einem PIN-Wechsel: denselben DK neu einpacken."""
    dk = auspacken(alte_pin, salz_hex, verpackt)
    salz = secrets.token_bytes(16)
    return salz.hex(), _kek(neue_pin, salz).encrypt(dk).decode()


# --- Freischaltung ----------------------------------------------------------
# Der hinterlegte DK ist mit dem Serverschluessel geschuetzt. Das schuetzt ihn
# gegen einen blossen Datenbankabzug, nicht gegen Root - dafuer ist das
# Fenster da.
def hinterlegen(dk: bytes, stunden: int) -> tuple[str, datetime]:
    from .models import utcnow

    return server_verschluesseln(dk.decode()), utcnow() + timedelta(hours=stunden)


def abholen(hinterlegt: str | None, gueltig_bis: datetime | None) -> bytes:
    from .models import as_utc, utcnow

    if not hinterlegt or gueltig_bis is None:
        raise GesperrtFehler("Die Modell-Zugaenge sind gesperrt.")
    if as_utc(gueltig_bis) < utcnow():
        raise GesperrtFehler("Die Freischaltung ist abgelaufen.")
    return server_entschluesseln(hinterlegt).encode()


# --- Zugaenge ver- und entschluesseln ---------------------------------------
def zugang_verschluesseln(dk: bytes, klartext: str) -> str:
    return Fernet(dk).encrypt(klartext.encode()).decode()


def zugang_entschluesseln(dk: bytes, geheim: str) -> str:
    try:
        return Fernet(dk).decrypt(geheim.encode()).decode()
    except InvalidToken as exc:
        raise GesperrtFehler(
            "Der Zugang laesst sich nicht entschluesseln - gehoert er zu einer "
            "aelteren PIN?"
        ) from exc

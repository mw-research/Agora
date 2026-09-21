"""Das Forum sichern und zurueckspielen.

Ein Abbild ist eine einzelne Datei: alle Tabellen als JSON, gepackt. Es geht
den Weg ueber den Browser, nicht ueber einen Pfad auf dem Server - der Server
kann nicht auf die Platte der Person schreiben, die vor dem Bildschirm sitzt,
und einen Pfad von aussen entgegenzunehmen hiesse, jedem Admin das Dateisystem
des Clusters zu oeffnen.

WAS EIN ABBILD UEBERLEBT

Der entscheidende Punkt fuer den Ernstfall: die Modell-Zugaenge haengen NICHT
am Serverschluessel. Ein Zugang ist mit dem Datenschluessel der Person
verschluesselt, und der liegt mit ihrer PIN verpackt daneben (app/tresor.py).
Ein Abbild laesst sich deshalb auf einem frisch aufgesetzten Server mit einem
NEUEN AGORA_SECRET_KEY einspielen, und alle behalten ihre Zugaenge - sie
melden sich an, geben ihre PIN ein, fertig.

Genau das ist nach einem Einbruch der richtige Weg: neuer Server, neuer
Serverschluessel, altes Abbild. Der erbeutete alte Schluessel ist dann wertlos.

Zwei Ausnahmen, und das Abbild sagt beide an:

  * Zugaenge aus der Zeit vor der Umschlagverschluesselung (enc_scheme leer
    oder "server") haengen doch am Serverschluessel. Sie werden gezaehlt und
    beim Einspielen gemeldet.
  * dk_unlocked - der Datenschluessel waehrend einer Freischaltung - geht
    bewusst NICHT mit. Er ist mit dem alten Serverschluessel verschluesselt,
    auf dem neuen Server also ohnehin wertlos, und nach einem Einbruch soll
    niemand eine offene Freischaltung erben.

WAS IN EINEM ABBILD STEHT

Alles: Personen samt Token- und PIN-Hashes, Foren, Themen, Beitraege und die
verschluesselten Modell-Zugaenge. Es ist damit so schuetzenswert wie die
Datenbank selbst. Ohne die passenden PINs sind die Zugaenge zwar nicht zu
oeffnen, aber Token-Hashes und Beitraege stehen offen darin.

Deshalb laesst sich ein Passwort darauflegen. Dann ist die Datei als Ganzes
verschluesselt und kann auch einmal aus der Hand gegeben werden - auf einen
USB-Stick, in eine fremde Cloud, an eine Vertretung.

Die Ableitung ist haerter eingestellt als die der PIN (n=2**15 statt 2**14).
Grund: eine PIN wird am Server probiert, und der bremst nach fuenf
Fehlversuchen. Eine Datei nimmt jemand mit nach Hause und probiert dort, so
oft er mag. Daher auch die Mindestlaenge - nimm einen Satz, kein Wort.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import logging
import secrets
from datetime import datetime
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import DateTime, delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .models import Base, utcnow

log = logging.getLogger(__name__)

# Steigt, wenn sich der Aufbau eines Abbilds so aendert, dass aeltere nicht
# mehr passen. Ein Abbild mit unbekannter Nummer wird abgelehnt statt geraten.
FORMAT = 1

# Felder, die bewusst nicht mitgehen: Laufzeitzustand, der auf einem anderen
# Server falsch waere. Die Lease-Felder der Themen gehoeren dazu - sonst
# haelte ein laengst toter Worker nach dem Einspielen noch Themen besetzt.
FLUECHTIG: dict[str, set[str]] = {
    "users": {"dk_unlocked", "unlocked_until", "failed_logins", "locked_until"},
    "threads": {"locked_until", "locked_by"},
}

# Ein Abbild ist gross, aber nicht beliebig gross.
MAX_BYTES = 200 * 1024 * 1024

# Schluesselableitung fuer das Passwort auf dem Abbild. Haerter als bei der
# PIN, weil eine Datei offline durchprobiert werden kann - siehe Modulkopf.
_N = 2**15
_R = 8
_P = 1
# OpenSSL riegelt scrypt bei 32 MB ab, n=2**15 braucht knapp 34 - ohne diese
# Grenze bricht die Ableitung mit "memory limit exceeded" ab.
_MAXMEM = 96 * 1024 * 1024
MIN_PASSWORT = 10


class SicherungFehler(ValueError):
    """Das Abbild passt nicht - Aufbau, Version oder Inhalt."""


class PasswortFehlt(ValueError):
    """Das Abbild ist geschuetzt, es wurde aber keins mitgegeben."""


class PasswortFalsch(ValueError):
    """Das Passwort passt nicht."""


def _schluessel(passwort: str, salz: bytes) -> Fernet:
    roh = hashlib.scrypt(
        passwort.strip().encode("utf-8"),
        salt=salz, n=_N, r=_R, p=_P, dklen=32, maxmem=_MAXMEM,
    )
    return Fernet(base64.urlsafe_b64encode(roh))


def ist_verschluesselt(daten: bytes) -> bool:
    """Ein gepacktes Abbild faengt mit der gzip-Kennung an, ein geschuetztes
    mit einer geschweiften Klammer. Damit ist es ohne Raten zu unterscheiden."""
    return daten[:1] == b"{"


def verschluesseln(abbild: bytes, passwort: str) -> bytes:
    """Legt ein Passwort auf ein fertiges (gepacktes) Abbild."""
    if len(passwort.strip()) < MIN_PASSWORT:
        raise SicherungFehler(
            f"Das Passwort braucht mindestens {MIN_PASSWORT} Zeichen."
        )
    salz = secrets.token_bytes(16)
    huelle = {
        "agora": "sicherung",
        "verschluesselt": 1,
        "kdf": "scrypt",
        "n": _N,
        "r": _R,
        "p": _P,
        "salz": salz.hex(),
        "inhalt": _schluessel(passwort, salz).encrypt(abbild).decode(),
    }
    return json.dumps(huelle).encode("utf-8")


def entschluesseln(daten: bytes, passwort: str | None) -> bytes:
    """Nimmt die Huelle ab. Ein ungeschuetztes Abbild geht unveraendert durch."""
    if not ist_verschluesselt(daten):
        return daten

    try:
        huelle = json.loads(daten)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SicherungFehler("Das ist kein Agora-Abbild.") from exc
    if huelle.get("agora") != "sicherung":
        raise SicherungFehler("Das ist kein Agora-Abbild.")
    if not passwort:
        raise PasswortFehlt("Dieses Abbild ist mit einem Passwort geschuetzt.")

    salz = bytes.fromhex(huelle["salz"])
    # Die Parameter kommen aus der Datei, nicht aus dem Code: ein aelteres
    # Abbild bleibt lesbar, auch wenn wir die Haerte spaeter hochsetzen.
    roh = hashlib.scrypt(
        passwort.strip().encode("utf-8"),
        salt=salz,
        n=int(huelle.get("n", _N)),
        r=int(huelle.get("r", _R)),
        p=int(huelle.get("p", _P)),
        dklen=32,
        maxmem=_MAXMEM,
    )
    try:
        return Fernet(base64.urlsafe_b64encode(roh)).decrypt(huelle["inhalt"].encode())
    except (InvalidToken, KeyError) as exc:
        raise PasswortFalsch("Falsches Passwort.") from exc


def schluessel_fingerabdruck() -> str:
    """Kurzer Abdruck des Serverschluessels, um Abbilder zuzuordnen.

    Ein Hash, kein Schluessel: daraus laesst sich nichts zurueckrechnen. Er
    dient allein dazu, beim Einspielen sagen zu koennen, ob der Server ein
    anderer ist als der, auf dem gesichert wurde.
    """
    schluessel = get_settings().secret_key or ""
    return hashlib.sha256(schluessel.encode()).hexdigest()[:16]


def _hinaus(wert: Any) -> Any:
    if isinstance(wert, datetime):
        return wert.isoformat()
    return wert


def _herein(wert: Any, spalte) -> Any:
    if wert is None:
        return None
    if isinstance(spalte.type, DateTime) and isinstance(wert, str):
        return datetime.fromisoformat(wert)
    return wert


async def erstellen(session: AsyncSession) -> tuple[bytes, dict]:
    """Liefert (gepacktes Abbild, Kopfdaten)."""
    tabellen: dict[str, list[dict]] = {}
    zaehler: dict[str, int] = {}
    am_serverschluessel = 0

    for tabelle in Base.metadata.sorted_tables:
        weglassen = FLUECHTIG.get(tabelle.name, set())
        zeilen = []
        for zeile in (await session.execute(select(tabelle))).mappings():
            daten = {k: _hinaus(v) for k, v in zeile.items() if k not in weglassen}
            if tabelle.name == "credentials" and daten.get("enc_scheme") != "user":
                am_serverschluessel += 1
            zeilen.append(daten)
        tabellen[tabelle.name] = zeilen
        zaehler[tabelle.name] = len(zeilen)

    from .main import APP_VERSION  # spaet, sonst importieren sich beide im Kreis

    kopf = {
        "format": FORMAT,
        "app_version": APP_VERSION,
        "erstellt_am": utcnow().isoformat(),
        "schluessel_fingerabdruck": schluessel_fingerabdruck(),
        # Diese Zugaenge ueberleben einen Serverschluesselwechsel NICHT.
        "am_serverschluessel": am_serverschluessel,
        "zaehler": zaehler,
    }

    roh = json.dumps({"kopf": kopf, "tabellen": tabellen}, ensure_ascii=False).encode("utf-8")
    return gzip.compress(roh, compresslevel=6), kopf


def lesen(daten: bytes, passwort: str | None = None) -> tuple[dict, dict]:
    """Ein hochgeladenes Abbild oeffnen und pruefen. Liefert (kopf, tabellen)."""
    if not daten:
        raise SicherungFehler("Die Datei ist leer.")
    if len(daten) > MAX_BYTES:
        raise SicherungFehler(f"Das Abbild ist groesser als {MAX_BYTES // (1024 * 1024)} MB.")

    geschuetzt = ist_verschluesselt(daten)
    daten = entschluesseln(daten, passwort)

    try:
        roh = gzip.decompress(daten)
    except (OSError, EOFError) as exc:
        raise SicherungFehler("Das ist kein Agora-Abbild (nicht gepackt).") from exc

    try:
        inhalt = json.loads(roh)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SicherungFehler("Das Abbild ist beschaedigt.") from exc

    if not isinstance(inhalt, dict) or "kopf" not in inhalt or "tabellen" not in inhalt:
        raise SicherungFehler("Das ist kein Agora-Abbild.")

    kopf = inhalt["kopf"]
    if kopf.get("format") != FORMAT:
        raise SicherungFehler(
            f"Abbild-Format {kopf.get('format')}, diese Version erwartet {FORMAT}."
        )

    tabellen = inhalt["tabellen"]
    bekannt = {t.name for t in Base.metadata.sorted_tables}
    fehlend = bekannt - set(tabellen)
    if fehlend:
        raise SicherungFehler(f"Im Abbild fehlen Tabellen: {', '.join(sorted(fehlend))}")

    # Der Abdruck sagt, ob der Server derselbe ist. Ein anderer ist kein
    # Fehler - nur die alten Zugaenge am Serverschluessel gehen dann verloren.
    kopf["gleicher_server"] = kopf.get("schluessel_fingerabdruck") == schluessel_fingerabdruck()
    kopf["verschluesselt"] = geschuetzt
    return kopf, tabellen


async def einspielen(session: AsyncSession, tabellen: dict) -> dict[str, int]:
    """Ersetzt den gesamten Bestand durch das Abbild.

    Alles oder nichts: laeuft in einer Transaktion. Bricht etwas ab, steht
    hinterher der alte Stand da und nicht eine halbe Mischung aus beidem.
    """
    geschrieben: dict[str, int] = {}

    # Erst leeren, in umgekehrter Abhaengigkeitsreihenfolge - sonst stehen
    # Fremdschluessel im Weg.
    for tabelle in reversed(Base.metadata.sorted_tables):
        await session.execute(delete(tabelle))

    # Dann fuellen, in Abhaengigkeitsreihenfolge.
    for tabelle in Base.metadata.sorted_tables:
        zeilen = tabellen.get(tabelle.name) or []
        if not zeilen:
            geschrieben[tabelle.name] = 0
            continue
        spalten = {s.name: s for s in tabelle.columns}
        sauber = []
        for zeile in zeilen:
            # Unbekannte Felder still weglassen: ein Abbild aus einer Version
            # mit einer spaeter entfernten Spalte soll trotzdem laufen.
            sauber.append(
                {name: _herein(wert, spalten[name]) for name, wert in zeile.items()
                 if name in spalten}
            )
        await session.execute(insert(tabelle), sauber)
        geschrieben[tabelle.name] = len(sauber)

    log.info("Abbild eingespielt: %s", geschrieben)
    return geschrieben


def dateiname(geschuetzt: bool = False) -> str:
    """Die Endung sagt, ob ein Passwort darauf liegt."""
    endung = "agora" if geschuetzt else "json.gz"
    return f"agora-sicherung-{utcnow():%Y-%m-%d-%H%M}.{endung}"

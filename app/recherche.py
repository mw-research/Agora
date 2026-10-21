"""Nachsehen, was draussen steht - ohne das Gespraech hinauszugeben.

Was hier passiert und was ausdruecklich nicht:

  * Gesucht wird ueber ein SearXNG im eigenen Haus. Die Anfrage geht von
    EUREM Dienst an die Suchmaschinen, nicht von einem Konto, das auf euch
    laeuft.
  * Die Treffer werden sortiert und die besten abgerufen. Zurueck kommt
    reiner Text - keine Bilder, keine Skripte, kein HTML.
  * ZUSAMMENGEFASST WIRD HIER NICHT. Das macht der Worker mit dem Modell
    des fragenden Agenten. Dieser Dienst hat keine Modell-Schluessel und
    soll auch keine bekommen: er ist der einzige Teil von Agora, der ins
    Netz darf, und genau deshalb traegt er nichts Wertvolles.

Zwei Dinge, die man beim Lesen mitdenken muss:

  * Die Suchanfrage IST Inhalt. Sie schreibt ein Agent, der die ganze
    Diskussion im Kontext hat. Deshalb steht sie sichtbar im Thread, bevor
    das Ergebnis kommt, und deshalb ist sie in der Laenge begrenzt.
  * Was von einer Webseite zurueckkommt, ist fremder Text. Er kann alles
    enthalten, auch "ignoriere die vorherigen Anweisungen". Der Worker
    rahmt ihn deshalb als Zitat und nicht als Auftrag.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

log = logging.getLogger(__name__)

# Nur genau diese Auszeichnung loest eine Recherche aus.
BLOCK = re.compile(r"```recherche[ \t]*\n(.*?)```", re.DOTALL | re.IGNORECASE)

# Die Anfrage steht im Protokoll einer Suchmaschine. Kurz halten ist hier
# kein Schoenheitsthema: ein langer Satz traegt mehr aus dem Material
# hinaus als ein paar Stichworte.
MAX_ANFRAGE = 200

MAX_SEITEN_BYTES = 2 * 1024 * 1024
MAX_SEITEN_ZEICHEN = 6000


class RechercheFehler(RuntimeError):
    """Die Recherche kam nicht zustande - lesbar fuer die Diskussion."""


@dataclass
class Treffer:
    titel: str
    adresse: str
    ausschnitt: str
    text: str = ""


@dataclass
class Ergebnis:
    anfrage: str
    treffer: list[Treffer] = field(default_factory=list)
    hinweis: str = ""


def anfragen_finden(text: str) -> list[str]:
    """Alle Recherche-Bloecke eines Beitrags, in der Reihenfolge des Textes."""
    roh = [t.group(1).strip() for t in BLOCK.finditer(text or "")]
    # Mehrzeilige Bloecke auf eine Anfrage eindampfen: was gesucht wird,
    # soll in einer Zeile stehen und nachlesbar sein.
    return [" ".join(a.split())[:MAX_ANFRAGE] for a in roh if a.strip()]


def _oeffentlich(host: str) -> bool:
    """Zeigt der Name auf eine oeffentliche Adresse?

    Gegen SSRF: ohne diese Pruefung koennte eine Suchmaschine - oder eine
    Umleitung - den Dienst auf 169.254.169.254 oder in das Clusternetz
    schicken. Die NetworkPolicy haelt das im Cluster ebenfalls auf; das
    hier ist die zweite Tuer, und sie gilt auch beim Entwickeln ohne
    Cluster.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        try:
            adresse = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if (
            adresse.is_private
            or adresse.is_loopback
            or adresse.is_link_local
            or adresse.is_reserved
            or adresse.is_multicast
            or adresse.is_unspecified
        ):
            return False
    return True


def _brauchbar(adresse: str) -> bool:
    teile = urlsplit(adresse)
    if teile.scheme not in ("http", "https") or not teile.hostname:
        return False
    return True


def _punkte(anfrage: str, treffer: Treffer, rang: int) -> float:
    """Wie gut passt der Treffer?

    Bewusst ohne zweites Modell. SearXNG hat ueber mehrere Suchmaschinen
    hinweg schon sortiert; hier wird nur nachgebessert, was man ohne
    Rechenaufwand sieht: kommen die gesuchten Woerter in Titel und
    Ausschnitt vor, und steht der Treffer weit vorn.

    Ein richtiger Reranker waere ein weiteres Modell in einem weiteren Pod.
    Das lohnt erst, wenn sich zeigt, dass es hier nicht reicht.
    """
    woerter = {w for w in re.findall(r"\w{3,}", anfrage.lower())}
    if not woerter:
        return -rang
    titel = treffer.titel.lower()
    text = treffer.ausschnitt.lower()
    im_titel = sum(1 for w in woerter if w in titel)
    im_text = sum(1 for w in woerter if w in text)
    # Der Titel wiegt schwerer: er sagt, worum es auf der Seite geht,
    # waehrend der Ausschnitt oft nur die Fundstelle zeigt.
    return (im_titel * 2.0 + im_text) / len(woerter) * 10 - rang * 0.1


async def _suchen(client: httpx.AsyncClient, searxng: str, anfrage: str,
                  sprache: str) -> list[Treffer]:
    try:
        antwort = await client.get(
            searxng.rstrip("/") + "/search",
            params={
                "q": anfrage,
                "format": "json",
                "language": sprache,
                "safesearch": "0",
            },
            headers={"Accept": "application/json"},
        )
        antwort.raise_for_status()
        daten = antwort.json()
    except httpx.HTTPError as exc:
        raise RechercheFehler(f"Die Suche war nicht erreichbar: {exc}") from exc
    except ValueError as exc:
        raise RechercheFehler(
            "Die Suche antwortete nicht in JSON. Laeuft SearXNG mit "
            "aktiviertem JSON-Format?"
        ) from exc

    treffer = []
    for eintrag in daten.get("results", []):
        adresse = (eintrag.get("url") or "").strip()
        if not _brauchbar(adresse):
            continue
        treffer.append(
            Treffer(
                titel=" ".join((eintrag.get("title") or "").split())[:200],
                adresse=adresse,
                ausschnitt=" ".join((eintrag.get("content") or "").split())[:400],
            )
        )
    return treffer


async def _abrufen(client: httpx.AsyncClient, treffer: Treffer) -> None:
    """Den Text einer Seite holen - oder es bleiben lassen."""
    host = urlsplit(treffer.adresse).hostname or ""
    if not await asyncio.to_thread(_oeffentlich, host):
        log.info("Uebersprungen, keine oeffentliche Adresse: %s", treffer.adresse)
        return
    try:
        antwort = await client.get(
            treffer.adresse,
            headers={"Accept": "text/html,text/plain"},
            follow_redirects=True,
        )
        antwort.raise_for_status()
    except httpx.HTTPError as exc:
        log.info("Seite nicht abrufbar (%s): %s", exc, treffer.adresse)
        return

    art = antwort.headers.get("content-type", "")
    if "html" not in art and "text/plain" not in art:
        return
    roh = antwort.content[:MAX_SEITEN_BYTES]

    # Denselben Entwirrer wie fuer hochgeladene Dateien benutzen, statt
    # einen zweiten zu pflegen.
    from .dokumente import _aus_html

    try:
        text = _aus_html(roh)
    except Exception:  # pragma: no cover - verkorkste Seite
        return
    treffer.text = text[:MAX_SEITEN_ZEICHEN]


async def nachsehen(
    anfrage: str,
    searxng: str,
    hoechstens: int = 5,
    lesen: int = 3,
    sekunden: int = 25,
    sprache: str = "de",
) -> Ergebnis:
    """Suchen, sortieren, die besten lesen."""
    anfrage = " ".join(anfrage.split())[:MAX_ANFRAGE]
    if not anfrage:
        raise RechercheFehler("Die Anfrage war leer.")
    if not searxng:
        raise RechercheFehler(
            "Es ist keine Suche eingerichtet (AGORA_RECHERCHE_SEARXNG)."
        )

    grenzen = httpx.Limits(max_connections=6)
    async with httpx.AsyncClient(
        timeout=sekunden, limits=grenzen, max_redirects=3,
        headers={"User-Agent": "Agora/1.0 (Forschungsforum; +https://github.com/mw-research/Agora)"},
    ) as client:
        gefunden = await _suchen(client, searxng, anfrage, sprache)
        if not gefunden:
            return Ergebnis(anfrage=anfrage, hinweis="Die Suche lieferte keine Treffer.")

        # Je Gastgeber nur den besten Treffer: zehn Unterseiten derselben
        # Seite sind keine zehn Quellen.
        gesehen: set[str] = set()
        sortiert = sorted(
            gefunden, key=lambda t: _punkte(anfrage, t, gefunden.index(t)), reverse=True
        )
        auswahl: list[Treffer] = []
        for treffer in sortiert:
            host = urlsplit(treffer.adresse).hostname or ""
            if host in gesehen:
                continue
            gesehen.add(host)
            auswahl.append(treffer)
            if len(auswahl) >= hoechstens:
                break

        if lesen > 0:
            await asyncio.gather(
                *(_abrufen(client, t) for t in auswahl[:lesen]),
                return_exceptions=True,
            )

    return Ergebnis(anfrage=anfrage, treffer=auswahl)

"""Welche Modelle bietet ein Endpunkt an?

Damit man beim Anlegen eines Agenten nicht den genauen Namen kennen muss.
Der Weg ist bei allen OpenAI-kompatiblen Anbietern derselbe - GET /models -
und den sprechen ausser OpenAI auch vLLM, Ollama, Groq, Mistral, Together
und ein LiteLLM-Proxy. Anthropic kann es ebenfalls, will aber ein eigenes
Kopffeld.

Das hier ist bewusst eine BEQUEMLICHKEIT und keine Voraussetzung: geht es
schief, bleibt das Eingabefeld, in das man den Namen von Hand schreibt.
Deshalb wird jeder Fehler als lesbarer Satz zurueckgegeben und nicht als
Absturz.
"""

from __future__ import annotations

import logging

import httpx

from .models import Credential

log = logging.getLogger(__name__)

# Wo der Anbieter seine Liste fuehrt, wenn keine eigene Basis-URL gesetzt ist.
# Fehlt ein Anbieter hier und hat kein api_base, laesst sich nichts abfragen.
STANDARD_BASIS = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "groq": "https://api.groq.com/openai/v1",
    "mistral": "https://api.mistral.ai/v1",
    "together_ai": "https://api.together.xyz/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "ollama": "http://localhost:11434/v1",
}

# Lieber schnell aufgeben als die Oberflaeche haengen lassen - es ist nur
# eine Bequemlichkeit.
ZEIT = 15.0


class ModellFehler(RuntimeError):
    """Die Liste liess sich nicht holen - mit einem Satz, den man zeigen kann."""


def _basis(cred: Credential) -> str:
    roh = (cred.api_base or STANDARD_BASIS.get((cred.provider or "").strip().lower(), "")).strip()
    if not roh:
        raise ModellFehler(
            f"Fuer '{cred.provider}' ist keine Basis-URL bekannt. Trag eine ein, "
            "dann laesst sich die Liste holen."
        )
    return roh.rstrip("/")


def _kopffelder(cred: Credential, schluessel: str | None) -> dict[str, str]:
    kopf: dict[str, str] = {"Accept": "application/json"}
    stil = (cred.auth_style or "api_key").strip()
    anbieter = (cred.provider or "").strip().lower()

    if not schluessel or stil == "none":
        return kopf

    if anbieter == "anthropic":
        # Anthropic nimmt kein Bearer und besteht auf der Versionsangabe.
        kopf["x-api-key"] = schluessel
        kopf["anthropic-version"] = "2023-06-01"
    elif stil == "header":
        kopf[cred.header_name or "x-api-key"] = schluessel
    else:
        # api_key wie bearer: die Liste hinter /models will ueberall ein
        # Authorization-Kopffeld, auch dort, wo der Chat-Aufruf den
        # Schluessel anders entgegennimmt.
        kopf["Authorization"] = f"Bearer {schluessel}"
    return kopf


def _namen(daten: object) -> list[str]:
    """Aus der Antwort die Modellnamen ziehen - zwei verbreitete Formen."""
    if not isinstance(daten, dict):
        return []

    # OpenAI, Anthropic, vLLM, Groq, Mistral: {"data": [{"id": ...}]}
    eintraege = daten.get("data")
    if isinstance(eintraege, list):
        return [e["id"] for e in eintraege if isinstance(e, dict) and e.get("id")]

    # Ollama nativ unter /api/tags: {"models": [{"name": ...}]}
    eintraege = daten.get("models")
    if isinstance(eintraege, list):
        namen = []
        for e in eintraege:
            if isinstance(e, dict):
                name = e.get("id") or e.get("name") or e.get("model")
                if name:
                    namen.append(name)
        return namen

    return []


async def auflisten(cred: Credential, schluessel: str | None) -> list[str]:
    """Liefert die Modellnamen des Endpunkts, alphabetisch.

    Ohne Anbieter-Praefix: das setzt app/llm.py beim Aufruf selbst davor,
    wenn im Namen kein Schraegstrich steht.
    """
    basis = _basis(cred)
    kopf = _kopffelder(cred, schluessel)

    # /models ist der Normalfall; /api/tags faengt ein Ollama ab, das nicht
    # unter der OpenAI-kompatiblen Adresse laeuft.
    adressen = [f"{basis}/models"]
    if "/v1" in basis:
        adressen.append(basis.replace("/v1", "") + "/api/tags")

    letzter = ""
    async with httpx.AsyncClient(timeout=ZEIT, follow_redirects=True) as client:
        for adresse in adressen:
            try:
                antwort = await client.get(adresse, headers=kopf)
            except httpx.HTTPError as exc:
                letzter = f"{adresse} nicht erreichbar ({type(exc).__name__})"
                continue

            if antwort.status_code in (401, 403):
                raise ModellFehler(
                    "Der Endpunkt hat den Schluessel abgelehnt "
                    f"(HTTP {antwort.status_code}). Stimmen Schluessel und Anmeldeart?"
                )
            if antwort.status_code >= 400:
                letzter = f"{adresse} antwortete mit HTTP {antwort.status_code}"
                continue

            try:
                namen = _namen(antwort.json())
            except ValueError:
                letzter = f"{adresse} lieferte kein JSON"
                continue

            if namen:
                return sorted(set(namen))
            letzter = f"{adresse} lieferte eine leere Liste"

    raise ModellFehler(
        f"Keine Liste zu holen: {letzter}. Den Modellnamen kannst du von Hand eintragen."
    )

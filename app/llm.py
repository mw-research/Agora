"""Duenne Schicht um LiteLLM.

Bewusst die *Bibliothek*, nicht der Proxy: so kann jeder Agent seinen eigenen
Endpunkt und seinen eigenen Key mitbringen (Cloud, on-prem vLLM, Ollama),
ohne dass jemand eine zentrale Proxy-Konfiguration anfassen muss.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

import litellm

from . import tresor
from .config import get_settings
from .crypto import decrypt
from .models import Agent

log = logging.getLogger(__name__)

# Unbekannte Modellnamen (eigene vLLM-Deployments) sollen nicht am
# Kosten-Tracking scheitern, und nicht unterstuetzte Parameter werden still
# verworfen statt einen 400er zu werfen.
litellm.drop_params = True
litellm.suppress_debug_info = True


class LLMError(RuntimeError):
    pass


def _geheimnis(geheim: str | None, schema: str | None, dk: bytes | None) -> str | None:
    """Entschluesselt je nach Verfahren.

    Neue Zugaenge haengen am Datenschluessel der Person; alte, vor der
    Umstellung angelegte, noch am Serverschluessel.
    """
    if not geheim:
        return None
    if schema == "user":
        if dk is None:
            raise LLMError("Die Modell-Zugaenge sind gesperrt - bitte freischalten.")
        return tresor.zugang_entschluesseln(dk, geheim)
    return decrypt(geheim)


def _call_kwargs(agent: Agent, dk: bytes | None = None) -> dict:
    model = agent.model.strip()
    cred = agent.credential
    if cred and "/" not in model:
        model = f"{cred.provider}/{model}"

    kwargs: dict = {
        "model": model,
        "temperature": agent.temperature,
        "max_tokens": agent.max_tokens,
        "timeout": get_settings().request_timeout,
    }
    if cred is None:
        return kwargs

    if cred.api_base:
        kwargs["api_base"] = cred.api_base

    schluessel = _geheimnis(cred.api_key_enc, cred.enc_scheme, dk)
    stil = (cred.auth_style or "api_key").strip()
    kopffelder: dict[str, str] = {}

    if schluessel and stil == "bearer":
        kopffelder["Authorization"] = f"Bearer {schluessel}"
    elif schluessel and stil == "header":
        kopffelder[cred.header_name or "x-api-key"] = schluessel
    elif schluessel and stil != "none":
        kwargs["api_key"] = schluessel

    if kopffelder:
        kwargs["extra_headers"] = kopffelder
        # Die OpenAI-kompatiblen Clients verweigern den Dienst ohne
        # irgendeinen api_key, auch wenn die Anmeldung ueber das Kopffeld
        # laeuft. Ein Platzhalter genuegt ihnen.
        kwargs.setdefault("api_key", "nicht-verwendet")
    elif "api_key" not in kwargs:
        # Dasselbe bei einem offenen Endpunkt - auth_style "none" oder ein
        # Zugang ohne Schluessel, wie ihn vLLM und Ollama ohne Absicherung
        # anbieten. Ohne diesen Platzhalter bricht der Aufruf mit "Missing
        # credentials" ab, bevor er den Endpunkt ueberhaupt erreicht.
        #
        # Nur wenn ein Zugang gewaehlt ist: ein Agent OHNE Zugang kehrt
        # weiter oben zurueck und nimmt bewusst den Schluessel aus der
        # Umgebung.
        kwargs["api_key"] = "nicht-verwendet"

    if cred.extra_json_enc:
        try:
            zusatz = json.loads(_geheimnis(cred.extra_json_enc, cred.enc_scheme, dk) or "{}")
        except (json.JSONDecodeError, RuntimeError) as exc:
            raise LLMError(f"Zusatzparameter von '{cred.label}' unlesbar: {exc}") from exc
        if isinstance(zusatz, dict):
            # Kopffelder aus den Zusatzparametern ergaenzen statt ersetzen.
            weitere = zusatz.pop("extra_headers", None)
            if isinstance(weitere, dict):
                kwargs["extra_headers"] = {**kwargs.get("extra_headers", {}), **weitere}
                kwargs.setdefault("api_key", "nicht-verwendet")
            kwargs.update(zusatz)

    return kwargs


async def stream(
    agent: Agent, messages: list[dict], dk: bytes | None = None
) -> AsyncIterator[tuple[str, str, dict | None]]:
    """Liefert Tupel (kind, payload, usage).

    kind ist "delta" waehrend der Generierung und "usage" als letztes Element.
    """
    kwargs = _call_kwargs(agent, dk)
    try:
        response = await litellm.acompletion(messages=messages, stream=True, **kwargs)
    except Exception as exc:  # LiteLLM wirft je nach Provider sehr verschiedene Typen
        raise LLMError(f"{type(exc).__name__}: {exc}") from exc

    usage: dict | None = None
    try:
        async for chunk in response:
            raw_usage = getattr(chunk, "usage", None)
            if raw_usage is not None:
                usage = _usage_dict(raw_usage)
            choices = getattr(chunk, "choices", None)
            if not choices:
                continue
            delta = getattr(choices[0], "delta", None)
            text = getattr(delta, "content", None) if delta is not None else None
            if text:
                yield "delta", text, None
    except Exception as exc:
        raise LLMError(f"Stream abgebrochen - {type(exc).__name__}: {exc}") from exc

    yield "usage", "", usage


async def vervollstaendige(
    agent: Agent, messages: list[dict], max_tokens: int | None = None, dk: bytes | None = None
) -> tuple[str, str | None]:
    """Wie complete(), gibt aber auch den Abbruchgrund zurueck.

    Der Grund ist der einzige Weg zu erfahren, ob eine Antwort zu Ende war
    oder am Token-Budget abgeschnitten wurde. Ohne ihn endet ein zu langer
    Text stumm mitten im Satz und sieht aus wie ein Modellfehler.
    """
    kwargs = _call_kwargs(agent, dk)
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    try:
        response = await litellm.acompletion(messages=messages, **kwargs)
    except Exception as exc:
        raise LLMError(f"{type(exc).__name__}: {exc}") from exc
    wahl = response.choices[0]
    return (wahl.message.content or "").strip(), getattr(wahl, "finish_reason", None)


async def complete(
    agent: Agent, messages: list[dict], max_tokens: int | None = None, dk: bytes | None = None
) -> str:
    """Einmaliger Aufruf ohne Streaming - fuer die Sprecherwahl des Moderators."""
    text, _ = await vervollstaendige(agent, messages, max_tokens, dk)
    return text


def _usage_dict(usage) -> dict:
    out = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = getattr(usage, key, None)
        if value is not None:
            out[key] = value
    return out

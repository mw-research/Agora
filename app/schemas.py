from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer

from .models import to_utc_iso

UtcDatetime = Annotated[datetime, PlainSerializer(to_utc_iso, return_type=str)]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- User -------------------------------------------------------------------
class LoginIn(BaseModel):
    token: str = Field(min_length=1)
    # Bei der ersten Anmeldung wird sie gesetzt, danach abgefragt.
    pin: str = ""


class PinChange(BaseModel):
    alte_pin: str = ""
    neue_pin: str = Field(min_length=1)


class ResetIn(BaseModel):
    # Auch die PIN loeschen - dann gehen zugleich alle Modell-Zugaenge der
    # Person verloren, damit ein Admin sie nicht erbt.
    pin_zuruecksetzen: bool = False


class UserCreate(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    is_admin: bool = False


class UserRechte(BaseModel):
    # Verwaltungsrechte geben oder nehmen. Damit laesst sich das
    # Installationskonto entmachten, sobald es ein eigenes Admin-Konto gibt.
    is_admin: bool


class UserOut(ORMModel):
    id: str
    name: str
    is_admin: bool
    has_pin: bool = False
    # Bis wann die Modell-Zugaenge lesbar sind; None = gesperrt.
    unlocked_until: UtcDatetime | None = None
    teilnahme_stunden: int = 24


class MeUpdate(BaseModel):
    # 0 = unbegrenzt
    teilnahme_stunden: int = Field(ge=0, le=8760)


class UserCreated(UserOut):
    # Einmal-Token zum Weitergeben. Es gilt nur fuer die erste Anmeldung und
    # wird dabei gegen ein neues getauscht, das nur die Person selbst kennt.
    token: str
    expires_at: datetime | None = None


class LoginOut(UserOut):
    """Antwort der Anmeldung.

    new_token ist gesetzt, wenn ein Einmal-Token eingeloest wurde: dann steht
    dort das dauerhafte Token, das die Person sich notieren muss. Danach kennt
    es niemand sonst - auch kein Admin.
    """

    new_token: str | None = None


class RequestCreate(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    # Wohin das Einmal-Token gehen soll - Mail, Kuerzel, Raumnummer.
    contact: str = Field(default="", max_length=200)
    note: str = Field(default="", max_length=500)


class RequestOut(ORMModel):
    id: str
    name: str
    contact: str | None
    note: str
    status: str
    decided_by: str | None
    created_at: UtcDatetime


# --- Credential -------------------------------------------------------------
class CredentialCreate(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    provider: str = Field(min_length=1, max_length=40)
    api_key: str | None = None
    api_base: str | None = None
    # api_key | bearer | header | none
    auth_style: str = "api_key"
    header_name: str | None = None
    # Freie LiteLLM-Parameter als JSON-Text, z.B. {"api_version": "2024-10-21"}
    extra_json: str | None = None


class CredentialOut(ORMModel):
    id: str
    label: str
    provider: str
    api_base: str | None
    has_key: bool
    auth_style: str
    header_name: str | None
    has_extra: bool


# --- Agent ------------------------------------------------------------------
class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=160)
    role: str = ""
    persona: str = ""
    credential_id: str | None = None
    temperature: float = 0.7
    max_tokens: int = 1200
    is_public: bool = True


class AgentUpdate(BaseModel):
    name: str | None = None
    model: str | None = None
    role: str | None = None
    persona: str | None = None
    credential_id: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    is_public: bool | None = None


class AgentOut(ORMModel):
    id: str
    owner_id: str
    name: str
    role: str
    persona: str
    model: str
    credential_id: str | None
    temperature: float
    max_tokens: int
    is_public: bool


# --- Forum ------------------------------------------------------------------
class ForumCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    parent_id: str | None = None
    position: int = 0


class ForumUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    parent_id: str | None = None
    position: int | None = None


class ThreadUpdate(BaseModel):
    """Ein Thema umhaengen oder umbenennen.

    forum_id traegt drei Bedeutungen, deshalb die Sonderbehandlung in der
    API: nicht mitgeschickt = nichts aendern, None = aus allen Foren heraus,
    eine Kennung = dorthin.
    """

    title: str | None = Field(default=None, min_length=1, max_length=200)
    forum_id: str | None = None


class UngelesenOut(BaseModel):
    # Themen mit Beitraegen, die diese Person noch nicht gesehen hat.
    threads: list[str] = []
    # Foren, in denen solche Themen liegen - samt aller Foren darueber,
    # damit der Punkt auch an einem zugeklappten Ast sichtbar ist.
    foren: list[str] = []
    # Gibt es Ungelesenes, das in gar keinem Forum liegt?
    ohne_forum: bool = False


class ForumOut(ORMModel):
    id: str
    name: str
    description: str
    parent_id: str | None
    position: int
    creator_id: str


# --- Thread -----------------------------------------------------------------
class SicherungAnfrage(BaseModel):
    """Leer = ohne Passwort. Sonst liegt es als Huelle auf dem Abbild."""

    passwort: str = ""


class ThreadCreate(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    # frei | pruefen | begutachten | antrag | code - siehe app/pruefarten.py
    art: str = "frei"
    goal: str = ""
    forum_id: str | None = None
    agent_ids: list[str] = Field(min_length=1)
    mode: str = "selector"
    moderator_agent_id: str | None = None
    max_rounds: int = Field(default=12, ge=1)
    pace_seconds: int = Field(default=5, ge=0, le=3600)
    stop_phrase: str = "EINVERSTANDEN"
    synthesize_on_end: bool = True
    start_now: bool = True
    # Nur wirksam, wenn der Server es erlaubt (AGORA_WERKZEUGE).
    werkzeuge: bool = False


class ThreadOut(ORMModel):
    id: str
    title: str
    goal: str
    creator_id: str
    forum_id: str | None
    status: str
    status_detail: str
    mode: str
    moderator_agent_id: str | None
    max_rounds: int
    rounds_done: int
    pace_seconds: int
    stop_phrase: str
    synthesize_on_end: bool
    werkzeuge: bool = False
    art: str = "frei"
    created_at: UtcDatetime
    updated_at: UtcDatetime


class ThreadDetail(ThreadOut):
    participants: list[AgentOut]


class ParticipantAdd(BaseModel):
    agent_id: str


class ThreadControl(BaseModel):
    # pause | resume | stop | extend
    action: str
    rounds: int = Field(default=5, ge=1, le=200)


# --- Post -------------------------------------------------------------------
class PostCreate(BaseModel):
    content: str = Field(min_length=1)
    # Nach einem menschlichen Beitrag sofort weiterlaufen lassen.
    resume: bool = True


class TranslateIn(BaseModel):
    # Zielsprache, wie in der Oberflaeche: de oder en
    ziel: str = Field(min_length=2, max_length=5)


class TranslateOut(BaseModel):
    sprache: str
    text: str
    # True, wenn sie schon vorlag und niemand dafuer bezahlen musste
    gecacht: bool


class PostOut(ORMModel):
    id: str
    thread_id: str
    author_type: str
    author_name: str
    agent_id: str | None
    content: str
    status: str
    round_no: int
    model: str | None
    usage: dict | None
    created_at: UtcDatetime

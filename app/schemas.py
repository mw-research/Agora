from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, PlainSerializer

from .models import to_utc_iso

UtcDatetime = Annotated[datetime, PlainSerializer(to_utc_iso, return_type=str)]


def _statt_null(ersatz: object) -> BeforeValidator:
    """NULL aus der Datenbank wie einen Vorgabewert lesen.

    Eine nachgezogene Spalte entsteht mit ALTER TABLE ohne DEFAULT - jede
    Zeile von vorher traegt dort NULL. Das Python-seitige default= greift
    nur beim INSERT, nicht rueckwirkend. Ein Ausgabefeld, das kein None
    vertraegt, antwortet dann mit 500, und zwar auf JEDE Bestandszeile.
    Genau das ist im Betrieb passiert: nach dem Update sahen Foren leer
    aus, obwohl alle Daten dalagen.

    Jedes Feld, das auf einer nullable Spalte sitzt, bekommt deshalb hier
    eine Umdeutung. tests/laengen_test.py besteht darauf.
    """

    def umdeuten(wert: object) -> object:
        return ersatz if wert is None else wert

    return BeforeValidator(umdeuten)


# Fuer Felder auf nullable Spalten - der Wert dahinter ist der, den eine
# Bestandszeile bekommt.
JaNein = Annotated[bool, _statt_null(False)]
ArtOderFrei = Annotated[str, _statt_null("frei")]
StundenOderNull = Annotated[int, _statt_null(0)]


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
    teilnahme_stunden: StundenOderNull = 24


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
    api_base: str | None = Field(default=None, max_length=300)
    # api_key | bearer | header | none
    auth_style: str = Field(default="api_key", max_length=20)
    header_name: str | None = Field(default=None, max_length=80)
    # Freie LiteLLM-Parameter als JSON-Text, z.B. {"api_version": "2024-10-21"}
    extra_json: str | None = None
    # Ein Zugang fuer alle. Nur Admins duerfen das setzen, und er liegt dann
    # am Serverschluessel statt an einer PIN - siehe models.Credential.
    geteilt: JaNein = False


class CredentialKopie(BaseModel):
    """Denselben Zugang noch einmal, mit anderen Zusatzparametern.

    Wofuer: ein Modell mit Reasoning laesst sich ueber die Zusatzparameter
    zum Denken bringen oder davon abhalten - aber die haengen am Zugang und
    nicht am Agenten. Wer beides braucht, braucht zwei Zugaenge auf
    dieselbe Adresse.

    Den Schluessel dafuer noch einmal einzutippen waere nicht nur laestig,
    sondern schlechter: er muesste dazu erst wieder jemandem vorliegen.
    Deshalb wird hier der Geheimtext unveraendert uebernommen - derselbe
    Schluessel, dasselbe Verfahren, nichts wird unterwegs entschluesselt.
    """

    label: str = Field(min_length=1, max_length=80)
    # Leer heisst: ohne Zusatzparameter - und das ist der Normalfall, denn
    # der Zweck der Kopie ist ja gerade, andere zu setzen als das Original.
    extra_json: str | None = None


class CredentialUpdate(BaseModel):
    """Was sich an einem Zugang aendern laesst.

    Nicht dabei: geteilt. Der Wechsel zwischen persoenlich und gemeinsam
    heisst Umschluesseln - vom Datenschluessel der Person auf den
    Serverschluessel oder zurueck - und das ist kein Nebenbei. Wer das
    will, legt einen zweiten Zugang an.

    api_key und extra_json unterscheiden drei Faelle: nicht mitgeschickt
    heisst unveraendert, ein Wert ersetzt, und eine leere Zeichenkette
    entfernt. Ohne diese Unterscheidung muesste man den Schluessel bei
    jeder Kleinigkeit neu eintippen.
    """

    label: str | None = Field(default=None, min_length=1, max_length=80)
    provider: str | None = Field(default=None, min_length=1, max_length=40)
    api_key: str | None = None
    api_base: str | None = Field(default=None, max_length=300)
    auth_style: str | None = Field(default=None, max_length=20)
    header_name: str | None = Field(default=None, max_length=80)
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
    geteilt: JaNein = False
    # Wem er gehoert - die Oberflaeche zeigt fremde Gemeinschaftszugaenge an,
    # laesst sie aber nicht aendern.
    owner_id: str | None = None


# --- Agent ------------------------------------------------------------------
class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=160)
    # Kurz, und zwar mit Absicht: die Rolle steht als EINE Zeile im
    # Auswahlprompt des Moderators, einmal je Teilnehmer, in jedem Zug.
    # Ein Absatz hier kostet in jeder Runde. Lange Beschreibungen gehoeren
    # in die Persona, die ist unbegrenzt.
    role: str = Field(default="", max_length=200)
    persona: str = ""
    credential_id: str | None = Field(default=None, max_length=32)
    temperature: float = 0.7
    max_tokens: int = 1200
    is_public: bool = True
    # Muster-Agent fuer alle. Nur Admins duerfen das setzen.
    geteilt: JaNein = False


class AgentUpdate(BaseModel):
    # Dieselben Grenzen wie beim Anlegen - sonst ist das Aendern die
    # Hintertuer, durch die zu lange Werte doch in die Datenbank kommen.
    name: str | None = Field(default=None, min_length=1, max_length=80)
    model: str | None = Field(default=None, min_length=1, max_length=160)
    role: str | None = Field(default=None, max_length=200)
    persona: str | None = None
    credential_id: str | None = Field(default=None, max_length=32)
    temperature: float | None = None
    max_tokens: int | None = None
    is_public: bool | None = None
    geteilt: bool | None = None


class MusterAnfrage(BaseModel):
    """Womit die mitgelieferten Muster-Agenten laufen sollen."""

    credential_id: str = Field(min_length=1, max_length=32)
    model: str = Field(min_length=1, max_length=160)

    # Optional ein zweiter Zugang fuer die fuenf Agenten, deren Arbeit eine
    # Kette ist statt eines Urteils (siehe Muster.denkt). Gedacht fuer den
    # Fall, dass auf dem einen Zugang das Reasoning abgeschaltet ist und auf
    # dem anderen nicht. Fehlt er, laufen alle zwoelf auf dem ersten - und
    # das soll so bleiben, denn mit einem Modell ohne Reasoning waere die
    # Unterscheidung nur ein Formularfeld mehr.
    credential_denken_id: str | None = Field(default=None, max_length=32)
    model_denken: str | None = Field(default=None, max_length=160)


class MusterBericht(BaseModel):
    aufgestellt: list[str] = []
    schon_da: list[str] = []


class WissenOut(ORMModel):
    id: str
    name: str
    zeichen: int


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
    # Von einem Admin fuer alle angelegt - jeder darf ihn einsetzen.
    geteilt: JaNein = False
    # Nur die ANZAHL, nie die Namen und nie der Inhalt. Mitlesende sollen
    # sehen, dass jemand aus eigenem Material argumentiert - sonst wirkt der
    # Widerspruch grundlos. Was darin steht, geht sie nichts an.
    wissen_dateien: int = 0


# --- Forum ------------------------------------------------------------------
class ForumCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    parent_id: str | None = Field(default=None, max_length=32)
    position: int = 0
    # offen | geschlossen - siehe sichtbare_foren() in app/main.py
    sichtbar: str = Field(default="offen", max_length=12)


class ForumUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    parent_id: str | None = Field(default=None, max_length=32)
    position: int | None = None
    sichtbar: str | None = Field(default=None, max_length=12)


class ThreadUpdate(BaseModel):
    """Ein Thema umhaengen oder umbenennen.

    forum_id traegt drei Bedeutungen, deshalb die Sonderbehandlung in der
    API: nicht mitgeschickt = nichts aendern, None = aus allen Foren heraus,
    eine Kennung = dorthin.
    """

    title: str | None = Field(default=None, min_length=1, max_length=200)
    forum_id: str | None = Field(default=None, max_length=32)


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
    sichtbar: str = "offen"


class MitgliedOut(BaseModel):
    user_id: str
    name: str


class MitgliedIn(BaseModel):
    user_id: str


# --- Thread -----------------------------------------------------------------
class SicherungAnfrage(BaseModel):
    """Leer = ohne Passwort. Sonst liegt es als Huelle auf dem Abbild."""

    passwort: str = ""


class ThreadCreate(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    # frei | pruefen | begutachten | antrag | code - siehe app/pruefarten.py
    art: str = Field(default="frei", max_length=20)
    goal: str = ""
    forum_id: str | None = Field(default=None, max_length=32)
    agent_ids: list[str] = Field(min_length=1)
    mode: str = Field(default="selector", max_length=20)
    moderator_agent_id: str | None = Field(default=None, max_length=32)
    max_rounds: int = Field(default=12, ge=1)
    pace_seconds: int = Field(default=5, ge=0, le=3600)
    stop_phrase: str = Field(default="EINVERSTANDEN", max_length=60)
    synthesize_on_end: bool = True
    start_now: bool = True
    # Nur wirksam, wenn der Server es erlaubt (AGORA_WERKZEUGE).
    werkzeuge: JaNein = False
    # Nur wirksam, wenn eine Suche eingerichtet ist (AGORA_RECHERCHE_SEARXNG).
    recherche: JaNein = False


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
    werkzeuge: JaNein = False
    recherche: JaNein = False
    art: ArtOderFrei = "frei"
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
    # Diagramme aus einer Rechnung, als base64-PNG. Stehen bewusst neben
    # dem Inhalt und nicht darin: content geht in den Prompt der naechsten
    # Runde, ein Bild waere dort zwanzigtausend Token.
    bilder: list[str] | None = None

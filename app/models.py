from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    """Naive Werte als UTC deuten - SQLite liefert sie ohne Zeitzone."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def to_utc_iso(value: datetime) -> str:
    """Immer mit Zeitzone ausliefern.

    SQLite gibt naive Datetimes zurueck; ohne Offset liest der Browser die
    UTC-Zeit als Lokalzeit und zeigt Beitraege um Stunden verschoben an.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def new_id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    token_hash: Mapped[str] = mapped_column(String(64), index=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)

    # Einmal-Token: gilt nur fuer die erste Anmeldung und wird dabei gegen ein
    # neues getauscht, das nur die Person selbst kennt. Wer das Einmal-Token
    # ausgegeben hat - typischerweise ein Admin - kommt danach nicht mehr
    # hinein. Bewusst nullable: bestehende Zeilen bekommen NULL und das ist
    # hier dasselbe wie False.
    one_time: Mapped[bool | None] = mapped_column(Boolean, default=False, nullable=True)
    one_time_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # PIN als scrypt-Hash, siehe app/sicherheit.py. Nicht entschluesselbar -
    # auch nicht mit vollem Datenbankzugriff. Wer sie vergisst, bekommt vom
    # Admin ein neues Konto-Geheimnis, verliert dabei aber seine
    # Modell-Zugaenge.
    pin_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    failed_logins: Mapped[int | None] = mapped_column(Integer, default=0, nullable=True)

    # Umschlagverschluesselung, siehe app/tresor.py. dk_wrapped ist der mit der
    # PIN verpackte Datenschluessel; ohne PIN kommt da niemand heran. dk_unlocked
    # ist derselbe Schluessel waehrend der Freischaltung - nur dort kaeme auch
    # jemand mit Serverzugriff daran.
    # Wie lange die eigenen Agenten nach dem letzten eigenen Beitrag in einem
    # Thread noch mitreden duerfen. 0 oder leer = unbegrenzt.
    teilnahme_stunden: Mapped[int | None] = mapped_column(Integer, default=24, nullable=True)

    dk_salt: Mapped[str | None] = mapped_column(String(40), nullable=True)
    dk_wrapped: Mapped[str | None] = mapped_column(Text, nullable=True)
    dk_unlocked: Mapped[str | None] = mapped_column(Text, nullable=True)
    unlocked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AccessRequest(Base):
    """Antrag auf einen Zugang.

    Jemand traegt sich ein, ein Admin gibt frei - und bekommt dabei ein
    Einmal-Token zum Weitergeben, nicht das spaetere Token der Person.
    """

    __tablename__ = "access_requests"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(80), index=True)
    # Wohin das Einmal-Token geschickt werden soll. Ohne das wuesste der Admin
    # nach der Freigabe nicht, wem er es geben muss.
    contact: Mapped[str | None] = mapped_column(String(200), nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    # offen | freigegeben | abgelehnt
    status: Mapped[str] = mapped_column(String(20), default="offen", index=True)
    decided_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Credential(Base):
    """Zugangsdaten zu einem Modell-Endpunkt - gehoert immer genau einem User."""

    __tablename__ = "credentials"
    __table_args__ = (UniqueConstraint("owner_id", "label", name="uq_credential_label"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(80))
    # LiteLLM-Provider-Praefix: anthropic | gemini | openai | azure | ollama | ...
    provider: Mapped[str] = mapped_column(String(40))
    api_key_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    api_base: Mapped[str | None] = mapped_column(String(300), nullable=True)

    # Wie der Schluessel mitgeschickt wird:
    #   api_key = als api_key an LiteLLM (Normalfall, auch fuer Anthropic)
    #   bearer  = als "Authorization: Bearer <schluessel>"
    #   header  = in einem frei benannten Kopffeld (header_name)
    #   none    = gar nicht (offene Endpunkte wie vLLM ohne Absicherung)
    auth_style: Mapped[str] = mapped_column(String(20), default="api_key", server_default="api_key")
    header_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # Zusaetzliche LiteLLM-Parameter als JSON, z.B. {"api_version": "2024-10-21"}
    # oder {"aws_region_name": "eu-central-1"}. Verschluesselt, weil dort auch
    # Geheimnisse stehen koennen.
    extra_json_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    # "user" = mit dem Datenschluessel der Person verschluesselt (Normalfall),
    # leer/"server" = alter Stand mit dem Serverschluessel. Beim ersten
    # Freischalten wird umgestellt.
    enc_scheme: Mapped[str | None] = mapped_column(String(10), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    # Kurzbeschreibung fuer die Sprecherwahl des Moderators
    role: Mapped[str] = mapped_column(String(200), default="")
    persona: Mapped[str] = mapped_column(Text, default="")
    # Vollstaendiger LiteLLM-Modellstring, z.B. "anthropic/claude-opus-5"
    model: Mapped[str] = mapped_column(String(160))
    credential_id: Mapped[str | None] = mapped_column(
        ForeignKey("credentials.id", ondelete="SET NULL"), nullable=True
    )
    temperature: Mapped[float] = mapped_column(Float, default=0.7)
    max_tokens: Mapped[int] = mapped_column(Integer, default=1200)
    # Oeffentliche Agenten duerfen von allen in Threads eingeladen werden.
    is_public: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    credential: Mapped[Credential | None] = relationship(lazy="selectin")


class Wissen(Base):
    """Unterlagen, die nur einem Agenten vorliegen.

    Der Zweck ist eine wirklich andere Ausgangslage: wer etwas weiss, was die
    anderen nicht wissen, argumentiert anders - und das ist der Unterschied
    zwischen vier Stimmen und vier Perspektiven.

    Der Text wird beim Hochladen einmal aus der Datei gewonnen und liegt hier
    als Text. Die Datei selbst wird nicht aufbewahrt, genauso wie bei den
    Dokumenten an einem Thema.

    KEIN Tresor: der Text liegt im Klartext, und der Agent darf daraus
    zitieren - sonst nuetzte ihm das Wissen in der Debatte nichts. Es ist
    asymmetrische Eingabe, kein Geheimnis.
    """

    __tablename__ = "wissen"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    agent_id: Mapped[str] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text, default="")
    # Laenge doppelt gefuehrt, damit die Liste den Umfang zeigen kann, ohne
    # jedes Mal den ganzen Text zu holen.
    zeichen: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Forum(Base):
    """Ordner fuer Themen, beliebig tief schachtelbar.

    parent_id zeigt auf das uebergeordnete Forum; ist es leer, steht das
    Forum ganz oben. Zyklen verhindert die API beim Setzen des Elternteils.
    """

    __tablename__ = "forums"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("forums.id", ondelete="CASCADE"), nullable=True, index=True
    )
    position: Mapped[int] = mapped_column(Integer, default=0)

    # offen = jeder Angemeldete sieht es; geschlossen = nur die Mitglieder.
    # Die Sichtbarkeit vererbt sich nach unten: wer ein geschlossenes Forum
    # nicht sehen darf, sieht auch nichts darunter - sonst waere jedes
    # Unterforum ein Loch in der Wand.
    sichtbar: Mapped[str] = mapped_column(
        String(12), default="offen", server_default="offen"
    )

    creator_id: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Mitglied(Base):
    """Wer ein geschlossenes Forum sehen darf.

    Fuer offene Foren steht hier nichts - die sieht ohnehin jeder. Ein
    geschlossenes Forum ohne Mitglieder ist niemandem mehr zugaenglich;
    deshalb raeumt das Loeschen einer Person solche Foren mit ab, statt
    unerreichbare Daten stehen zu lassen.
    """

    __tablename__ = "mitglieder"

    forum_id: Mapped[str] = mapped_column(
        ForeignKey("forums.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Thread(Base):
    __tablename__ = "threads"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(200))
    goal: Mapped[str] = mapped_column(Text, default="")
    creator_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # Leer = liegt direkt im Forum, ohne Einordnung.
    forum_id: Mapped[str | None] = mapped_column(
        ForeignKey("forums.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # active = Worker arbeitet; paused = wartet auf Menschen; done/error = fertig
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    status_detail: Mapped[str] = mapped_column(Text, default="")

    # roundrobin = feste Reihenfolge; selector = Moderator waehlt den naechsten
    mode: Mapped[str] = mapped_column(String(20), default="selector")
    moderator_agent_id: Mapped[str | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )

    max_rounds: Mapped[int] = mapped_column(Integer, default=12)
    rounds_done: Mapped[int] = mapped_column(Integer, default=0)
    # Sekunden Pause zwischen zwei Beitraegen - drosselt Kosten und macht
    # den Verlauf fuer Menschen lesbar.
    pace_seconds: Mapped[int] = mapped_column(Integer, default=5)
    # Zustimmungswort. Der Thread endet erst, wenn ALLE Teilnehmer es im
    # selben Durchgang setzen; leer bedeutet: laeuft bis zum Rundenbudget.
    stop_phrase: Mapped[str] = mapped_column(String(60), default="EINVERSTANDEN")
    # Abschlusssynthese durch den Moderator, wenn das Rundenbudget aufgebraucht ist
    synthesize_on_end: Mapped[bool] = mapped_column(Boolean, default=True)

    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_agent_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Wer als naechstes dran ist, wenn es nicht der Reihe nach gehen soll -
    # etwa nachdem ein Agent gerechnet hat und das Ergebnis einordnen muss.
    naechster_agent_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Duerfen die Agenten in diesem Thema rechnen?
    werkzeuge: Mapped[bool | None] = mapped_column(Boolean, default=False, nullable=True)
    # Was fuer ein Material liegt vor - ein Antrag will anders gelesen werden
    # als ein Beweis. Siehe app/pruefarten.py.
    art: Mapped[str | None] = mapped_column(String(20), default="frei", nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Participant(Base):
    __tablename__ = "participants"
    __table_args__ = (UniqueConstraint("thread_id", "agent_id", name="uq_participant"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    thread_id: Mapped[str] = mapped_column(ForeignKey("threads.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer, default=0)
    # Startpunkt des Teilnahmefensters, solange der Besitzer selbst noch
    # nichts in den Thread geschrieben hat.
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=True
    )

    agent: Mapped[Agent] = relationship(lazy="selectin")


class Gelesen(Base):
    """Bis wann jemand ein Thema gelesen hat.

    Der gruene Punkt am Forum haengt hieran: gibt es darin ein Thema, dessen
    letzter Beitrag juenger ist als dieser Zeitpunkt - oder ueberhaupt keine
    Zeile fuer diese Person -, dann ist da etwas Neues.

    Bewusst pro Thema und nicht pro Beitrag: das waeren bei einem Forum, das
    Wochen laeuft, zehntausende Zeilen fuer denselben Zweck.
    """

    __tablename__ = "gelesen"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    thread_id: Mapped[str] = mapped_column(
        ForeignKey("threads.id", ondelete="CASCADE"), primary_key=True
    )
    gelesen_bis: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class Uebersetzung(Base):
    """Einmal uebersetzt, immer da.

    Uebersetzen kostet Tokens. Was einmal uebertragen wurde, soll nicht beim
    naechsten Leser noch einmal bezahlt werden.
    """

    __tablename__ = "uebersetzungen"
    __table_args__ = (UniqueConstraint("post_id", "sprache", name="uq_uebersetzung"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    post_id: Mapped[str] = mapped_column(
        ForeignKey("posts.id", ondelete="CASCADE"), index=True
    )
    sprache: Mapped[str] = mapped_column(String(5))
    text: Mapped[str] = mapped_column(Text)
    # Wer es bezahlt hat - nur fuers Nachvollziehen.
    veranlasst_von: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    thread_id: Mapped[str] = mapped_column(ForeignKey("threads.id", ondelete="CASCADE"), index=True)
    # agent | human | system
    author_type: Mapped[str] = mapped_column(String(10))
    author_name: Mapped[str] = mapped_column(String(80))
    agent_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    content: Mapped[str] = mapped_column(Text, default="")
    # streaming | complete | error
    status: Mapped[str] = mapped_column(String(12), default="complete")
    round_no: Mapped[int] = mapped_column(Integer, default=0)

    model: Mapped[str | None] = mapped_column(String(160), nullable=True)
    usage: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

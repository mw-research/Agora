from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AGORA_", env_file=".env", extra="ignore")

    secret_key: str = ""
    admin_token: str = ""
    database_url: str = "sqlite+aiosqlite:///./agora.db"

    # Wie viel eigenes Material ein Agent hoechstens tragen darf, in Zeichen
    # ueber alle seine Unterlagen zusammen. Das liegt in JEDEM seiner
    # Aufrufe - wie die Persona -, die Kosten wachsen also mit jeder Runde.
    #
    # Stand frueher auf 12000 und war damit zu knapp fuer den Zweck: ein
    # einzelnes Dokument darf nach dokumente.MAX_ZEICHEN 40000 Zeichen haben,
    # eine Handakte fasste also nicht einmal eines davon. Drei, vier Seiten
    # PDF sind schnell 10000 Zeichen.
    #
    # 60000 Zeichen sind grob 17000 Token je Aufruf. Das ist viel Geld, wenn
    # man es ausschoepft - deshalb zeigt der Dialog den Umfang und die
    # geschaetzten Token je Runde an, statt es zu verschweigen.
    wissen_zeichen: int = 60_000

    # Notschluessel. Normalerweise legt AGORA_ADMIN_TOKEN das Admin-Konto
    # EINMAL an und wird danach nie wieder angefasst. Wer sich ausgesperrt
    # hat, setzt das hier auf true, startet einmal neu - dann uebernimmt der
    # Start das Token aus der Umgebung - und setzt es wieder auf false.
    #
    # Dauerhaft auf true ist gefaehrlich: dann holt sich jeder Neustart das
    # Token aus der Umgebung zurueck, und ein zurueckgesetztes Admin-Token
    # ist nach dem naechsten Neustart wieder das alte.
    admin_token_uebernehmen: bool = False

    # --- Recherche ---
    # Adresse eines SearXNG im eigenen Haus. Leer = die Recherche ist aus,
    # und zwar ueberall; ohne sie laesst sich kein Thema damit anlegen.
    #
    # Die Suchanfrage verlaesst das Haus - sie schreibt ein Agent, der die
    # Diskussion im Kontext hat. Sie steht deshalb sichtbar im Thread,
    # bevor das Ergebnis kommt. Zusammengefasst wird mit dem Modell des
    # fragenden Agenten, es geht also kein Inhalt an ein Modell, das nicht
    # ohnehin mitliest.
    recherche_searxng: str = ""
    # Adresse des Recherche-Dienstes. Leer = der Worker sieht selbst nach -
    # bequem beim Entwickeln, im Betrieb nicht gedacht, denn dann braeuchte
    # der Worker einen Weg ins Netz. Siehe k8s/agora.recherche.yaml.
    recherche_url: str = ""
    recherche_treffer: int = 5
    recherche_lesen: int = 3
    recherche_sekunden: int = 25

    worker_enabled: bool = True
    worker_interval: float = 2.0

    # Duerfen Agenten rechnen? Dabei laeuft vom Modell erzeugter Code auf dem
    # Server - siehe app/werkzeuge.py. Deshalb standardmaessig aus.
    werkzeuge: bool = False
    werkzeug_sekunden: int = 15
    werkzeug_speicher_mb: int = 512
    werkzeug_zeichen: int = 4000
    # Wie viele Diagramme eine Rechnung hoechstens zurueckgeben darf und wie
    # gross eines davon sein darf. Die Bilder gehen nicht in den Prompt,
    # kosten also keine Token - aber sie liegen in der Datenbank und gehen
    # durch jede Sicherung.
    werkzeug_bilder: int = 3
    werkzeug_bild_kb: int = 400
    # So oft darf ein Agent in Folge rechnen, bevor wieder jemand anders dran
    # ist. Ohne die Grenze koennte er sich in einer Rechnung verlieren.
    werkzeug_runden: int = 3
    # Adresse eines eigenen Rechner-Dienstes ohne Netzausgang. Leer =
    # im Worker selbst rechnen; bequem beim Entwickeln, im Betrieb nicht
    # gedacht - siehe app/rechner_dienst.py.
    rechner_url: str = ""

    # Offenes Antragsformular auf der Anmeldeseite. Fuer eine Installation im
    # offenen Netz abschaltbar - dann legt nur der Admin Zugaenge an.
    antraege_offen: bool = True

    # So lange bleiben die Modell-Zugaenge nach einer Anmeldung entsperrt.
    # Danach pausieren laufende Diskussionen, bis jemand wieder freischaltet.
    freischaltung_stunden: int = 12

    max_rounds_hard: int = 200
    max_context_posts: int = 40
    # Reichlich bemessen, und das mit Absicht: ein Modell mit Reasoning
    # denkt erst und schreibt dann, und beides zusammen passt bei 180
    # Sekunden nicht mehr hinein. Was man dabei gewinnt, ist nichts - ein
    # Aufruf, der frueher fertig ist, ist frueher fertig; die Grenze greift
    # nur im Fehlerfall. Was man verliert, wenn sie zu knapp sitzt, ist der
    # ganze Zug: der Agent faellt aus, und bei drei Ausfaellen pausiert die
    # Diskussion. Wer einen haengenden Anbieter schnell bemerken will,
    # setzt sie herunter.
    request_timeout: int = 600

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgresql")


@lru_cache
def get_settings() -> Settings:
    return Settings()

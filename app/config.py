from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AGORA_", env_file=".env", extra="ignore")

    secret_key: str = ""
    admin_token: str = ""
    database_url: str = "sqlite+aiosqlite:///./agora.db"

    worker_enabled: bool = True
    worker_interval: float = 2.0

    # Duerfen Agenten rechnen? Dabei laeuft vom Modell erzeugter Code auf dem
    # Server - siehe app/werkzeuge.py. Deshalb standardmaessig aus.
    werkzeuge: bool = False
    werkzeug_sekunden: int = 15
    werkzeug_speicher_mb: int = 512
    werkzeug_zeichen: int = 4000
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
    request_timeout: int = 180

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgresql")


@lru_cache
def get_settings() -> Settings:
    return Settings()

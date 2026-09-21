"""Verschluesselung der Provider-Credentials.

Die Keys der Nutzer liegen nie im Klartext in der Datenbank. Entschluesselt
wird ausschliesslich im Moment des LLM-Aufrufs; ueber die API werden sie nie
zurueckgegeben (siehe schemas.CredentialOut).
"""

from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings

_fernet: Fernet | None = None


GENERATE_HINT = (
    'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
)


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        key = get_settings().secret_key
        if not key:
            raise RuntimeError(f"AGORA_SECRET_KEY ist nicht gesetzt. Erzeugen mit: {GENERATE_HINT}")
        try:
            _fernet = Fernet(key.encode() if isinstance(key, str) else key)
        except (ValueError, TypeError) as exc:
            raise RuntimeError(
                "AGORA_SECRET_KEY ist kein gueltiger Fernet-Schluessel "
                f"(erwartet: 32 Byte base64-url, 44 Zeichen). Erzeugen mit: {GENERATE_HINT}"
            ) from exc
    return _fernet


def check_ready() -> None:
    """Beim Start pruefen statt beim ersten Credential.

    Ein Platzhalter im Secret wuerde sonst erst auffallen, wenn jemand nach
    Tagen den ersten Modell-Zugang anlegt - und dann im laufenden Betrieb.
    """
    decrypt(encrypt("agora"))


def encrypt(value: str) -> str:
    return _get_fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    try:
        return _get_fernet().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise RuntimeError(
            "Credential laesst sich nicht entschluesseln - wurde AGORA_SECRET_KEY geaendert?"
        ) from exc

"""Rechnen lassen: Python-Code aus einem Beitrag ausfuehren.

Agenten koennen nicht rechnen, sie koennen nur plausibel klingen. Fuer eine
Fachdiskussion ist das zu wenig: eine Zahl muss nachgerechnet, eine Umformung
geprueft, ein Gegenbeispiel gesucht werden koennen. Dafuer schreibt ein Agent
einen Block

    ```rechnen
    import sympy as sp
    x = sp.symbols("x")
    print(sp.solve(x**2 - 2, x))
    ```

und bekommt die Ausgabe als eigenen Beitrag zurueck, den auch Menschen lesen.

Bewusst eine eigene Auszeichnung statt ```python: Agenten schreiben staendig
Beispielcode, der nicht laufen soll. Was gerechnet wird, muss ausdruecklich
so gemeint sein.

SICHERHEIT - bitte lesen, bevor das eingeschaltet wird:

Hier laeuft vom Modell erzeugter Code auf dem Server. Der Unterprozess ist
gehaertet (eigene Ausfuehrungsumgebung, Zeit- und Speichergrenzen, kein
Netzwerk, kein Starten weiterer Programme), aber das ist eine Huerde, keine
Mauer - wer sie ueberwinden will, kann es. Deshalb:

  * standardmaessig abgeschaltet (AGORA_WERKZEUGE=false),
  * im offenen Netz nur mit dem eigenen Rechner-Dienst benutzen, den
    app/rechner_dienst.py und k8s/agora.rechner.yaml mitbringen: ein Pod ohne
    Datenbank, ohne Schluessel und ohne jeden Netzausgang. Den Worker selbst
    abzuschotten geht nicht - er muss die Modelle erreichen,
  * die Grenzen greifen nur unter Linux; unter Windows bleibt allein die
    Zeitschranke.

Was NICHT hier landet: Code in ```python und aehnlichen Bloecken. Den schreiben
die Agenten zum Lesen und Besprechen, und er wird nie ausgefuehrt.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import tempfile

log = logging.getLogger(__name__)

# Nur genau diese Auszeichnung wird ausgefuehrt.
BLOCK = re.compile(r"```rechnen[ \t]*\n(.*?)```", re.DOTALL | re.IGNORECASE)

# Wird dem Code vorangestellt. Haelt die haeufigsten Ausbruchsversuche auf -
# und vor allem versehentliche Netzzugriffe, die sonst minutenlang haengen.
VORSPANN = '''
import sys as _sys

def _gesperrt(name):
    def _nein(*a, **k):
        raise OSError(f"{name} ist beim Rechnen abgeschaltet.")
    return _nein

import socket as _socket
_socket.socket = _gesperrt("Netzwerkzugriff")
_socket.create_connection = _gesperrt("Netzwerkzugriff")
_socket.getaddrinfo = _gesperrt("Netzwerkzugriff")

import subprocess as _subprocess
for _name in ("Popen", "run", "call", "check_output", "check_call"):
    setattr(_subprocess, _name, _gesperrt("Programme starten"))

import os as _os
for _name in ("system", "popen", "execv", "execve", "fork", "spawnl", "spawnv"):
    if hasattr(_os, _name):
        setattr(_os, _name, _gesperrt("Programme starten"))

_sys.setrecursionlimit(3000)
'''


def bloecke_finden(text: str) -> list[str]:
    """Alle rechnen-Bloecke eines Beitrags, in der Reihenfolge des Textes."""
    return [treffer.group(1) for treffer in BLOCK.finditer(text or "")]


def _grenzen(cpu_sekunden: int, speicher_mb: int):
    """Prozessgrenzen setzen - nur unter POSIX vorhanden."""
    try:
        import resource
    except ImportError:  # pragma: no cover - Windows
        return None

    def setzen() -> None:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_sekunden, cpu_sekunden))
        speicher = speicher_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (speicher, speicher))
        # Keine weiteren Prozesse, keine grossen Dateien.
        resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))
        resource.setrlimit(resource.RLIMIT_FSIZE, (5 * 1024 * 1024, 5 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    return setzen


def rechnen(
    code: str,
    sekunden: int = 15,
    speicher_mb: int = 512,
    max_zeichen: int = 4000,
) -> tuple[str, bool]:
    """Fuehrt Code aus und liefert (ausgabe, geglueckt).

    Laeuft in einem eigenen Prozess mit eigener, leerer Arbeitsumgebung. Was
    dort geschrieben wird, ist nach dem Aufruf fort.
    """
    if not code.strip():
        return "Der Block war leer.", False

    with tempfile.TemporaryDirectory(prefix="agora-rechnen-") as ordner:
        datei = os.path.join(ordner, "rechnung.py")
        with open(datei, "w", encoding="utf-8") as f:
            f.write(VORSPANN + "\n" + code)

        umgebung = {
            "PATH": "/usr/bin:/bin",
            "HOME": ordner,
            "TMPDIR": ordner,
            "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            # Falls jemand matplotlib benutzt: kein Fenster aufmachen.
            "MPLBACKEND": "Agg",
            "OPENBLAS_NUM_THREADS": "1",
            "OMP_NUM_THREADS": "1",
        }

        try:
            fertig = subprocess.run(
                # -I: eigene Umgebung, kein Nutzer-Verzeichnis, keine Variablen
                [sys.executable, "-I", datei],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=sekunden,
                cwd=ordner,
                env=umgebung,
                preexec_fn=_grenzen(sekunden, speicher_mb) if os.name == "posix" else None,
            )
        except subprocess.TimeoutExpired:
            return f"Abgebrochen: laenger als {sekunden} Sekunden gelaufen.", False
        except Exception as exc:  # pragma: no cover - Notnagel
            log.exception("Rechnen fehlgeschlagen")
            return f"Konnte nicht ausgefuehrt werden: {exc}", False

    ausgabe = (fertig.stdout or "") + (fertig.stderr or "")
    ausgabe = ausgabe.strip()

    if fertig.returncode != 0 and not ausgabe:
        ausgabe = f"Beendet mit Rueckgabewert {fertig.returncode} und ohne Ausgabe."
    if not ausgabe:
        ausgabe = "Kein Ergebnis - vergiss print() nicht."
    if len(ausgabe) > max_zeichen:
        ausgabe = ausgabe[:max_zeichen].rstrip() + f"\n[... nach {max_zeichen} Zeichen abgeschnitten]"

    return ausgabe, fertig.returncode == 0


async def ausfuehren(
    code: str,
    sekunden: int = 15,
    speicher_mb: int = 512,
    max_zeichen: int = 4000,
    dienst_url: str = "",
) -> tuple[str, bool]:
    """Rechnen lassen - moeglichst nicht im eigenen Prozess.

    Mit dienst_url geht der Code an einen eigenen Rechner-Dienst, der im
    Cluster ohne jeden Netzausgang laeuft. Ohne sie wird hier gerechnet; das
    ist fuer die Entwicklung bequem und fuer den Betrieb nicht gedacht.
    """
    import asyncio

    if not dienst_url:
        return await asyncio.to_thread(rechnen, code, sekunden, speicher_mb, max_zeichen)

    import httpx

    try:
        async with httpx.AsyncClient(timeout=sekunden + 15) as client:
            antwort = await client.post(
                dienst_url.rstrip("/") + "/rechnen",
                json={
                    "code": code,
                    "sekunden": sekunden,
                    "speicher_mb": speicher_mb,
                    "max_zeichen": max_zeichen,
                },
            )
        antwort.raise_for_status()
        daten = antwort.json()
        return daten["ausgabe"], bool(daten["geglueckt"])
    except Exception as exc:
        log.warning("Rechner-Dienst nicht erreichbar: %s", exc)
        return (
            "Der Rechner-Dienst ist nicht erreichbar - gerechnet wurde nichts. "
            f"({type(exc).__name__})",
            False,
        )


def verfuegbare_pakete() -> list[str]:
    """Was zum Rechnen bereitsteht - fuer den Hinweis im Prompt."""
    da = ["math", "statistics", "fractions", "decimal", "itertools", "random", "re"]
    for name in ("sympy", "numpy", "mpmath", "scipy"):
        try:
            __import__(name)
            da.append(name)
        except ImportError:
            pass
    return da

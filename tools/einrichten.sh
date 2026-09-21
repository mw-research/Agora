#!/usr/bin/env bash
#
# Agora einrichten und starten - ohne Docker, ohne Registry, ohne Cluster.
#
# Gedacht fuer eine Sandbox oder eine schlichte Linux-Kiste: alles, was es
# braucht, ist Python 3.11 oder neuer. Die Datenbank ist dann SQLite in einer
# Datei daneben.
#
#   ./tools/einrichten.sh            # einrichten und starten (Port 8000)
#   ./tools/einrichten.sh 8080       # auf einem anderen Port
#   ./tools/einrichten.sh --nur-einrichten
#
# Das Skript ist wiederholbar: es ueberschreibt nichts, was schon dasteht.
#
# WICHTIG - warum eine vorhandene .env unangetastet bleibt: an
# AGORA_SECRET_KEY haengen die hinterlegten Modell-Zugaenge. Ein neuer
# Schluessel macht sie unlesbar. Das Skript legt die Datei deshalb nur an,
# wenn es sie noch nicht gibt, und fasst sie sonst nie an.
set -euo pipefail

cd "$(dirname "$0")/.."
WURZEL="$(pwd)"
PORT="8000"
STARTEN=1

for arg in "$@"; do
  case "$arg" in
    --nur-einrichten) STARTEN=0 ;;
    [0-9]*)           PORT="$arg" ;;
    -h|--help)        sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unbekannt: $arg (--help hilft)"; exit 2 ;;
  esac
done

meldung() { printf '\n==> %s\n' "$1"; }

# --- 1. Python finden -------------------------------------------------------
PY=""
for kandidat in python3.13 python3.12 python3.11 python3 python; do
  if command -v "$kandidat" >/dev/null 2>&1 &&
     "$kandidat" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)' 2>/dev/null; then
    PY="$kandidat"; break
  fi
done
if [ -z "$PY" ]; then
  echo "FEHLT: Python 3.11 oder neuer. Gefunden:"
  for k in python3 python; do command -v "$k" >/dev/null 2>&1 && "$k" --version; done
  exit 1
fi
meldung "Python: $("$PY" --version) ($(command -v "$PY"))"

# --- 2. Umgebung ------------------------------------------------------------
if [ -d .venv ]; then
  meldung "Umgebung besteht schon - Abhaengigkeiten werden nachgezogen"
else
  meldung "Lege .venv an"
  "$PY" -m venv .venv
fi
VENV_PY=".venv/bin/python"
[ -x "$VENV_PY" ] || VENV_PY=".venv/Scripts/python.exe"   # falls jemand das unter Windows faehrt

meldung "Installiere Abhaengigkeiten (dauert beim ersten Mal ein paar Minuten)"
"$VENV_PY" -m pip install --quiet --upgrade pip
"$VENV_PY" -m pip install --quiet -r requirements.txt
meldung "$("$VENV_PY" -m pip list 2>/dev/null | wc -l) Pakete installiert"

# --- 3. Geheimnisse ---------------------------------------------------------
# Nur anlegen, was fehlt. Ein bestehender Schluessel wird NIE ersetzt.
if [ ! -f .env ]; then
  meldung "Lege .env mit frischen Geheimnissen an"
  "$VENV_PY" - <<'PYCODE'
import pathlib, secrets
from cryptography.fernet import Fernet
pathlib.Path(".env").write_text(
    "# Von tools/einrichten.sh angelegt. Nicht ins Repository geben.\n"
    "#\n"
    "# AGORA_SECRET_KEY NIEMALS aendern, solange Modell-Zugaenge hinterlegt\n"
    "# sind - sie haengen daran und waeren sonst unlesbar.\n"
    f"AGORA_SECRET_KEY={Fernet.generate_key().decode()}\n"
    f"AGORA_ADMIN_TOKEN={secrets.token_urlsafe(32)}\n"
    "AGORA_DATABASE_URL=sqlite+aiosqlite:///./agora.db\n"
    "AGORA_WORKER_ENABLED=true\n"
    "# Rechnen lassen? Dabei laeuft vom Modell erzeugter Code auf diesem\n"
    "# Rechner. In einer Sandbox vertretbar, im offenen Netz nur mit dem\n"
    "# eigenen Rechner-Pod (k8s/agora.rechner.yaml).\n"
    "AGORA_WERKZEUGE=false\n",
    encoding="utf-8")
print("   .env angelegt")
PYCODE
else
  meldung ".env besteht schon - unveraendert gelassen"
fi

# --- 4. Kurz pruefen, dass es traegt ---------------------------------------
meldung "Pruefe die Installation"
"$VENV_PY" - <<'PYCODE'
import importlib
for name in ("fastapi", "sqlalchemy", "litellm", "cryptography", "httpx", "pypdf"):
    importlib.import_module(name)
print("   alle Pflichtpakete importierbar")
try:
    import sympy, numpy  # noqa: F401
    print("   sympy und numpy da - die Agenten koennen rechnen")
except ImportError:
    print("   ohne sympy/numpy - Rechnen bleibt eingeschraenkt")
PYCODE

TOKEN="$(grep '^AGORA_ADMIN_TOKEN=' .env | cut -d= -f2-)"

cat <<ENDE

--------------------------------------------------------------------
  Eingerichtet in $WURZEL

  Admin-Token:  $TOKEN
                (steht in .env - der einzige Weg hinein, gut aufheben)

  Selbst starten:
    .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT

  Testen, ohne Modelle zu bezahlen:
    .venv/bin/python tests/smoke_test.py
    .venv/bin/python tools/mock_model_server.py   # falscher Endpunkt
--------------------------------------------------------------------
ENDE

if [ "$STARTEN" -eq 1 ]; then
  meldung "Starte auf Port $PORT - mit Strg-C beenden"
  exec "$VENV_PY" -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
fi

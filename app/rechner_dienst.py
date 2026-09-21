"""Ein eigener Dienst, der nur rechnet - und sonst nichts kann.

Warum getrennt: die Haertung des Unterprozesses in app/werkzeuge.py ist eine
Huerde, keine Mauer. Wer sie ueberwindet, sitzt sonst im Worker - und der hat
die Datenbank, die Modell-Schluessel und einen Weg nach draussen.

Dieser Dienst hat nichts davon. Er bekommt Code, gibt die Ausgabe zurueck und
kennt weder Datenbank noch Modelle. Im Cluster laeuft er in einem eigenen Pod,
dem eine NetworkPolicy jeden ausgehenden Verkehr verbietet (siehe
k8s/agora.rechner.yaml). Selbst ein vollstaendiger Ausbruch endet damit in
einer Sackgasse.

Starten:

    uvicorn app.rechner_dienst:app --host 0.0.0.0 --port 8000

Der Worker findet ihn ueber AGORA_RECHNER_URL. Ohne die Variable rechnet er
selbst - bequem fuer die Entwicklung, fuer den Betrieb nicht gedacht.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from pydantic import BaseModel, Field

from . import werkzeuge

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("agora.rechner")

app = FastAPI(title="Agora - Rechner", version="1.0.0")


class Auftrag(BaseModel):
    code: str = Field(min_length=1)
    sekunden: int = Field(default=15, ge=1, le=120)
    speicher_mb: int = Field(default=512, ge=64, le=4096)
    max_zeichen: int = Field(default=4000, ge=100, le=100_000)


class Ergebnis(BaseModel):
    ausgabe: str
    geglueckt: bool


@app.post("/rechnen", response_model=Ergebnis)
async def rechnen(auftrag: Auftrag) -> Ergebnis:
    import asyncio

    log.info("Auftrag ueber %d Zeichen", len(auftrag.code))
    ausgabe, geglueckt = await asyncio.to_thread(
        werkzeuge.rechnen,
        auftrag.code,
        auftrag.sekunden,
        auftrag.speicher_mb,
        auftrag.max_zeichen,
    )
    return Ergebnis(ausgabe=ausgabe, geglueckt=geglueckt)


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok", "pakete": werkzeuge.verfuegbare_pakete()}

"""Ein eigener Dienst, der nur nachsieht - und sonst nichts kann.

Das Gegenstueck zum Rechner, und zwar genau spiegelverkehrt: der Rechner
darf alles rechnen und nirgendwohin, dieser Dienst darf nirgendwohin
rechnen und dafuer ins Netz.

Warum getrennt: er ist der einzige Teil von Agora mit einem Weg nach
draussen. Er traegt deshalb nichts, was dort nuetzlich waere - keine
Datenbank, keine Modell-Schluessel, keine Beitraege. Er bekommt eine
Suchanfrage, gibt Fundstellen als Text zurueck und vergisst sie.

Zusammengefasst wird ausdruecklich NICHT hier, sondern im Worker mit dem
Modell des fragenden Agenten. Laeuft das Forum auf einem Modell im eigenen
Haus, verlaesst damit kein Diskussionsinhalt das Haus - nur die
Suchanfrage, und die steht sichtbar im Thread.

Starten:

    uvicorn app.recherche_dienst:app --host 0.0.0.0 --port 8000

Der Worker findet ihn ueber AGORA_RECHERCHE_URL. Ohne die Variable sieht
er selbst nach; bequem beim Entwickeln, fuer den Betrieb nicht gedacht -
dann haette der Worker einen Weg ins Netz, und genau den soll er nicht
haben.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from . import recherche

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("agora.recherche")

app = FastAPI(title="Agora - Recherche", version="1.0.0")


class Auftrag(BaseModel):
    anfrage: str = Field(min_length=2, max_length=recherche.MAX_ANFRAGE)
    searxng: str = Field(min_length=1, max_length=300)
    hoechstens: int = Field(default=5, ge=1, le=10)
    lesen: int = Field(default=3, ge=0, le=5)
    sekunden: int = Field(default=25, ge=5, le=120)
    sprache: str = Field(default="de", max_length=5)


class Fundstelle(BaseModel):
    titel: str
    adresse: str
    ausschnitt: str
    text: str = ""


class Ergebnis(BaseModel):
    anfrage: str
    treffer: list[Fundstelle] = []
    hinweis: str = ""


@app.post("/recherche", response_model=Ergebnis)
async def nachsehen(auftrag: Auftrag) -> Ergebnis:
    # Die Anfrage wird protokolliert, und das ist Absicht: sie hat das Haus
    # verlassen, also soll nachlesbar sein, was.
    log.info("Anfrage: %s", auftrag.anfrage)
    try:
        ergebnis = await recherche.nachsehen(
            auftrag.anfrage,
            auftrag.searxng,
            auftrag.hoechstens,
            auftrag.lesen,
            auftrag.sekunden,
            auftrag.sprache,
        )
    except recherche.RechercheFehler as exc:
        raise HTTPException(502, str(exc)) from exc

    return Ergebnis(
        anfrage=ergebnis.anfrage,
        hinweis=ergebnis.hinweis,
        treffer=[
            Fundstelle(
                titel=t.titel, adresse=t.adresse, ausschnitt=t.ausschnitt, text=t.text
            )
            for t in ergebnis.treffer
        ],
    )


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok", "dienst": "recherche"}

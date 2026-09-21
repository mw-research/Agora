"""Minimaler OpenAI-kompatibler Endpunkt - Forum vorfuehren ohne Tokens zu verbrennen.

Starten:  .venv/Scripts/python -m uvicorn tools.mock_model_server:app --port 8078
Dann im Forum einen Zugang anlegen: Provider openai, Basis-URL
http://127.0.0.1:8078/v1, kein Schluessel; Agenten auf openai/mock-modell.
"""
import asyncio
import json
import random
import time

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

app = FastAPI()

SENTENCES = [
    "Der entscheidende Punkt ist die Konsistenzgarantie: ohne Quorum ueber Regionen hinweg handelt man sich Lesekonflikte ein.",
    "Ich widerspreche: die Latenz ueber Kontinente macht synchrone Replikation praktisch unbrauchbar, CRDTs sind hier die ehrlichere Antwort.",
    "Beide Ansaetze scheitern an der Betriebsrealitaet, wenn niemand die Partitionierung nach Zugriffsmustern plant.",
    "Konkret wuerde ich Regionen als Schreib-Heimat pro Tenant festlegen und nur Metadaten global halten.",
    # Zeigt den Rechenweg: der Block wird ausgefuehrt, die Ausgabe erscheint
    # als eigener Beitrag, danach ist derselbe Agent wieder dran.
    "Das laesst sich nachrechnen statt schaetzen.\n\n```rechnen\n"
    "import sympy as sp\n"
    "n = sp.symbols('n', positive=True, integer=True)\n"
    "print('Quorum bei 5 Regionen:', 5 // 2 + 1)\n"
    "print('Summe 1/n^2:', sp.summation(1/n**2, (n, 1, sp.oo)))\n"
    "```",
]


def moderator_pick(body) -> str:
    text = json.dumps(body)
    names = [n for n in ("Architekt", "Kritikerin", "Betreiber") if n in text]
    return random.choice(names) if names else "Architekt"


@app.get("/v1/models")
async def models():
    """Die Liste, die jeder OpenAI-kompatible Endpunkt fuehrt.

    Ohne sie liesse sich die Modell-Auswahl im Agentenformular nicht
    ausprobieren - und die ist der haeufigste Grund, diesen Fake zu starten.
    """
    return {
        "object": "list",
        "data": [
            {"id": name, "object": "model", "owned_by": "mock"}
            for name in ("mock-modell", "mock-modell-gross", "mock-modell-schnell")
        ],
    }


@app.post("/v1/chat/completions")
async def completions(request: Request):
    body = await request.json()
    roh = json.dumps(body)
    if "Antworte ausschliesslich mit dem Namen" in roh:
        content = moderator_pick(body)
    elif "Uebersetze den folgenden Forumsbeitrag" in roh:
        # Keine echte Uebersetzung - nur erkennbar genug, um die Kette zu zeigen.
        content = ("[EN] The decisive point is the consistency guarantee: without a "
                   "quorum across regions you buy yourself read conflicts.")
    else:
        content = random.choice(SENTENCES)

    if not body.get("stream"):
        return {
            "id": "mock", "object": "chat.completion", "created": int(time.time()),
            "model": body.get("model", "mock"),
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
        }

    async def gen():
        for word in content.split(" "):
            chunk = {
                "id": "mock", "object": "chat.completion.chunk", "created": int(time.time()),
                "model": body.get("model", "mock"),
                "choices": [{"index": 0, "delta": {"content": word + " "}, "finish_reason": None}],
            }
            yield f"data: {json.dumps(chunk)}\n\n"
            await asyncio.sleep(0.12)
        done = {
            "id": "mock", "object": "chat.completion.chunk", "created": int(time.time()),
            "model": body.get("model", "mock"),
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
        yield f"data: {json.dumps(done)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")

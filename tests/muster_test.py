"""Muster-Agenten und Gemeinschaftszugaenge.

Der Punkt, an dem es sonst still kaputtgeht: ein Zugang fuer alle darf
NICHT an der PIN eines Admins haengen. Taete er das, stuende jeder
Muster-Agent still, sobald dessen Freischaltfenster ablaeuft - fuer alle
gleichzeitig, und ohne dass jemand die Ursache sieht.
"""
import asyncio
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

VERZEICHNIS = tempfile.mkdtemp(prefix="agora-muster-")
os.environ["AGORA_DATABASE_URL"] = f"sqlite+aiosqlite:///{VERZEICHNIS}/m.db"
os.environ["AGORA_SECRET_KEY"] = "xUqK7nHnV1nQ0Yq4Zk8vZ0mVQ1pQ9yYy2Zt8Xy3Kk3A="
os.environ["AGORA_ADMIN_TOKEN"] = "admin-token-fuer-den-mustertest"
os.environ["AGORA_WORKER_ENABLED"] = "true"
os.environ["AGORA_WORKER_INTERVAL"] = "0.2"

import httpx  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app import llm, main, musteragenten, pruefarten  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import Credential  # noqa: E402

GERUFEN: list[str] = []


async def fake_stream(agent, messages, dk=None):
    GERUFEN.append(f"{agent.name}|dk={'ja' if dk else 'nein'}")
    yield "delta", f"Beitrag von {agent.name}.", None
    yield "usage", "", None


async def fake_complete(agent, messages, max_tokens=None, dk=None):
    return "egal"


llm.stream = fake_stream
llm.complete = fake_complete


async def main_test() -> int:
    transport = httpx.ASGITransport(app=main.app)
    async with main.app.router.lifespan_context(main.app):
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:

            async def anmelden(token, pin):
                r = await c.post("/api/login", json={"token": token, "pin": pin})
                assert r.status_code == 200, r.text
                c.cookies.clear()
                return r.json().get("new_token") or token

            def kopf(t):
                return {"Authorization": f"Bearer {t}"}

            # Das Installationskonto bekommt seine PIN ueber /api/me/pin:
            # /api/login setzt sie nur beim Einloesen eines Einmal-Tokens,
            # und dieses Konto kommt aus der Umgebung.
            A = kopf(os.environ["AGORA_ADMIN_TOKEN"])
            r = await c.post("/api/me/pin", headers=A, json={"neue_pin": "Admin-PIN-4711"})
            assert r.status_code == 200, r.text
            admin = await anmelden(os.environ["AGORA_ADMIN_TOKEN"], "Admin-PIN-4711")
            A = kopf(admin)

            r = await c.post("/api/users", headers=A, json={"name": "gast"})
            gast = kopf(await anmelden(r.json()["token"], "Gast-PIN-4711"))

            # --- Nur ein Admin darf einen Zugang fuer alle hinterlegen ----
            r = await c.post("/api/credentials", headers=gast, json={
                "label": "Heimlich", "provider": "openai",
                "api_key": "sk-egal", "geteilt": True})
            assert r.status_code == 403, r.text
            print("[ok] Ein Zugang fuer alle ist Admin-Sache")

            r = await c.post("/api/credentials", headers=A, json={
                "label": "Hausmodell", "provider": "openai",
                "api_key": "sk-im-eigenen-haus",
                "api_base": "http://vllm.intern:8000/v1", "geteilt": True})
            assert r.status_code == 201, r.text
            gemeinsam = r.json()
            assert gemeinsam["geteilt"] is True and gemeinsam["has_key"] is True

            # Er liegt am SERVERschluessel, nicht an der PIN des Admins.
            async with SessionLocal() as sitzung:
                roh = (
                    await sitzung.execute(
                        select(Credential).where(Credential.id == gemeinsam["id"])
                    )
                ).scalar_one()
                assert roh.enc_scheme == "server", roh.enc_scheme
                assert llm._geheimnis(roh.api_key_enc, roh.enc_scheme, None) == \
                    "sk-im-eigenen-haus"
            print("[ok] Der Zugang liegt am Serverschluessel und ist ohne PIN lesbar")

            # --- Auch der Gast sieht ihn, aber er gehoert ihm nicht -------
            seine = (await c.get("/api/credentials", headers=gast)).json()
            assert any(z["id"] == gemeinsam["id"] and z["geteilt"] for z in seine), seine
            r = await c.delete(f"/api/credentials/{gemeinsam['id']}", headers=gast)
            assert r.status_code == 404, "ein Gast darf ihn nicht entfernen"
            print("[ok] Jeder sieht ihn, entfernen darf ihn nur ein Admin")

            # --- Ein eigener Agent darf ihn nicht greifen -----------------
            r = await c.post("/api/agents", headers=gast, json={
                "name": "Schmarotzer", "model": "openai/x",
                "credential_id": gemeinsam["id"]})
            assert r.status_code == 400 and "Muster-Agenten vorbehalten" in r.text, r.text
            print("[ok] Ein eigener Agent kommt nicht an den Gemeinschaftszugang")

            # --- Ein Muster-Agent braucht einen ---------------------------
            r = await c.post("/api/agents", headers=A, json={
                "name": "Ohne", "model": "openai/x", "geteilt": True})
            assert r.status_code == 400 and "braucht einen Gemeinschaftszugang" in r.text
            r = await c.post("/api/credentials", headers=A, json={
                "label": "Privat", "provider": "openai", "api_key": "sk-privat"})
            assert r.status_code == 201, f"privater Zugang: {r.status_code} {r.text}"
            privat = r.json()["id"]
            r = await c.post("/api/agents", headers=A, json={
                "name": "AmPrivaten", "model": "openai/x",
                "credential_id": privat, "geteilt": True})
            assert r.status_code == 400 and "Zugang fuer alle" in r.text, r.text
            print("[ok] Ein Muster-Agent haengt nur an einem Zugang fuer alle")

            # --- Jetzt richtig --------------------------------------------
            r = await c.post("/api/agents", headers=A, json={
                "name": "Hausgutachter", "model": "openai/x",
                "role": "prueft", "persona": "Du pruefst.",
                "credential_id": gemeinsam["id"], "geteilt": True})
            assert r.status_code == 201, r.text
            muster = r.json()["id"]
            assert r.json()["geteilt"] is True
            print("[ok] Muster-Agent aufgestellt")

            # --- Und der Gast darf ihn einsetzen --------------------------
            sichtbar = (await c.get("/api/agents", headers=gast)).json()
            assert any(a["id"] == muster and a["geteilt"] for a in sichtbar), sichtbar

            r = await c.post("/api/agents", headers=gast,
                             json={"name": "Eigener", "model": "openai/x"})
            eigener = r.json()["id"]
            r = await c.post("/api/threads", headers=gast, json={
                "title": "Mit dem Hausgutachter",
                "goal": "Soll gehen.",
                "agent_ids": [muster, eigener],
                "mode": "roundrobin", "max_rounds": 1, "pace_seconds": 0,
                "start_now": False})
            assert r.status_code == 201, f"Gast darf den Muster-Agenten nicht: {r.text}"
            thema = r.json()["id"]
            print("[ok] Der Gast holt den Muster-Agenten in seine Diskussion")

            # --- Das Freischaltfenster des Admins ist egal ----------------
            # Genau hier ginge es sonst still kaputt: der Admin meldet sich
            # ab, und jeder Muster-Agent steht still.
            r = await c.post("/api/me/lock", headers=A)
            assert r.status_code in (200, 204), r.text
            r = await c.post(f"/api/threads/{thema}/control", headers=gast,
                             json={"action": "resume"})
            assert r.status_code == 200, r.text
            for _ in range(80):
                await asyncio.sleep(0.05)
                stand = (await c.get(f"/api/threads/{thema}", headers=gast)).json()
                beitraege = (
                    await c.get(f"/api/threads/{thema}/posts", headers=gast)
                ).json()
                if any(b["author_name"] == "Hausgutachter" for b in beitraege):
                    break
                if stand["status"] == "paused" and "gesperrt" in stand["status_detail"]:
                    raise AssertionError(
                        "Der Muster-Agent steht still, weil der Admin gesperrt ist: "
                        + stand["status_detail"]
                    )
            assert any("Hausgutachter" in g for g in GERUFEN), GERUFEN
            assert "Hausgutachter|dk=nein" in GERUFEN, \
                f"er haette ohne Datenschluessel laufen muessen: {GERUFEN}"
            print("[ok] Er laeuft auch, wenn der Admin abgemeldet ist")

            # --- Entfernen: erst die Agenten, dann der Zugang -------------
            r = await c.delete(f"/api/credentials/{gemeinsam['id']}", headers=A)
            assert r.status_code == 409 and "haengen noch" in r.text, r.text
            r = await c.delete(f"/api/agents/{muster}", headers=gast)
            assert r.status_code == 404, "ein Gast entfernt keinen Muster-Agenten"
            r = await c.delete(f"/api/agents/{muster}", headers=A)
            assert r.status_code == 204, r.text
            r = await c.delete(f"/api/credentials/{gemeinsam['id']}", headers=A)
            assert r.status_code == 204, r.text
            print("[ok] Entfernen in der richtigen Reihenfolge, sonst Hinweis")

            # --- Die mitgelieferten Muster-Agenten ------------------------
            # Jede Art der Diskussion braucht ihr Paar. Kommt eine neue Art
            # hinzu und niemand legt Agenten dafuer an, faellt das sonst
            # erst auf, wenn jemand die Art auswaehlt und allein dasteht.
            for art in pruefarten.ARTEN:
                paar = musteragenten.fuer(art)
                assert len(paar) == 2, f"Art '{art}' hat {len(paar)} Muster statt 2"
            fremde = {m.art for m in musteragenten.MUSTER} - set(pruefarten.ARTEN)
            assert not fremde, f"Muster fuer Arten, die es nicht gibt: {fremde}"
            print(f"[ok] Zwei Muster je Art ({len(pruefarten.ARTEN)} Arten)")

            # Sie muessen in ihre Spalten passen - sonst scheitert erst das
            # Aufstellen, und zwar beim Nutzer.
            for m in musteragenten.MUSTER:
                assert 0 < len(m.role) <= 200, f"{m.name}: Rolle {len(m.role)}"
                assert 0 < len(m.name) <= 80, f"{m.name}: Name {len(m.name)}"
                assert m.persona.strip(), f"{m.name}: keine Persona"
                assert 0.0 <= m.temperature <= 2.0, f"{m.name}: {m.temperature}"
            print("[ok] Alle passen in ihre Spalten")

            # --- Aufstellen ----------------------------------------------
            r = await c.post("/api/credentials", headers=A, json={
                "label": "Hausmodell zwei", "provider": "openai",
                "api_key": "sk-haus", "geteilt": True})
            zweiter = r.json()["id"]

            # Ohne Gemeinschaftszugang geht es nicht.
            r = await c.post("/api/admin/musteragenten", headers=A,
                             json={"credential_id": privat, "model": "openai/x"})
            assert r.status_code == 400 and "Zugang fuer alle" in r.text, r.text
            # Und nur Admins duerfen es.
            r = await c.post("/api/admin/musteragenten", headers=gast,
                             json={"credential_id": zweiter, "model": "openai/x"})
            assert r.status_code == 403, r.text

            r = await c.post("/api/admin/musteragenten", headers=A,
                             json={"credential_id": zweiter, "model": "openai/x"})
            assert r.status_code == 200, r.text
            bericht = r.json()
            assert len(bericht["aufgestellt"]) == len(musteragenten.MUSTER), bericht
            print(f"[ok] {len(bericht['aufgestellt'])} Muster-Agenten aufgestellt")

            # Zweimal druecken aendert nichts - sonst waeren Aenderungen weg.
            r = await c.post("/api/admin/musteragenten", headers=A,
                             json={"credential_id": zweiter, "model": "openai/x"})
            assert r.json()["aufgestellt"] == [], r.json()
            assert len(r.json()["schon_da"]) == len(musteragenten.MUSTER)
            print("[ok] Ein zweiter Klick legt nichts doppelt an")

            # Und der Gast kann sie sofort benutzen.
            sichtbar = {a["name"]: a for a in (await c.get("/api/agents", headers=gast)).json()}
            fehlend = {m.name for m in musteragenten.MUSTER} - set(sichtbar)
            assert not fehlend, fehlend
            paar = musteragenten.fuer("antrag")
            r = await c.post("/api/threads", headers=gast, json={
                "title": "Sofort loslegen",
                "goal": "Ohne eigene Persona.",
                "agent_ids": [sichtbar[m.name]["id"] for m in paar],
                "art": "antrag",
                "mode": "roundrobin", "max_rounds": 1, "pace_seconds": 0,
                "start_now": False})
            assert r.status_code == 201, f"Gast kann sie nicht einsetzen: {r.text}"
            print("[ok] Der Gast startet damit sofort, ohne eigene Persona")

    print("\nALLE TESTS BESTANDEN")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main_test()))

"""Geschlossene Foren - sieht wirklich niemand hinein, der nicht drin ist?

Der Test geht bewusst jeden Weg einzeln ab, auf dem etwas herauskommen
koennte: Forenliste, Themenliste, Thema, Beitraege, Uebersetzen, Ungelesenes,
Verschieben und die beiden Ereignisstroeme. Eine vergessene Pruefung ist
hier kein Schoenheitsfehler, sondern das ganze Versprechen.
"""
import asyncio
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

VERZEICHNIS = tempfile.mkdtemp(prefix="agora-raum-")
os.environ["AGORA_DATABASE_URL"] = f"sqlite+aiosqlite:///{VERZEICHNIS}/raum.db"
os.environ["AGORA_SECRET_KEY"] = "xUqK7nHnV1nQ0Yq4Zk8vZ0mVQ1pQ9yYy2Zt8Xy3Kk3A="
os.environ["AGORA_ADMIN_TOKEN"] = "admin-token-fuer-den-raumtest-0001"
os.environ["AGORA_WORKER_ENABLED"] = "false"

import httpx  # noqa: E402

from app import llm, main  # noqa: E402


async def fake_stream(agent, messages, dk=None):
    yield "delta", f"Beitrag von {agent.name}.", None
    yield "usage", "", None


async def fake_complete(agent, messages, max_tokens=None, dk=None):
    return "egal"


llm.stream = fake_stream
llm.complete = fake_complete


async def main_test() -> int:
    transport = httpx.ASGITransport(app=main.app)
    async with main.app.router.lifespan_context(main.app):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:

            async def anmelden(token, pin):
                r = await c.post("/api/login", json={"token": token, "pin": pin})
                assert r.status_code == 200, r.text
                # Das Anmelden setzt ein Sitzungs-Cookie, und current_user
                # zieht das Cookie dem Bearer-Header vor. In einem Test mit
                # mehreren Leuten an einem Client waere sonst jeder der,
                # der sich zuletzt angemeldet hat.
                c.cookies.clear()
                return r.json().get("new_token") or token

            def kopf(token):
                return {"Authorization": f"Bearer {token}"}

            # --- Drei Leute: drinnen, draussen, und ein Admin --------------
            admin = await anmelden(os.environ["AGORA_ADMIN_TOKEN"], "Admin-PIN-4711")
            A = kopf(admin)

            async def person(name, pin):
                r = await c.post("/api/users", headers=A, json={"name": name})
                assert r.status_code == 201, r.text
                return kopf(await anmelden(r.json()["token"], pin))

            drin = await person("drin", "Drin-PIN-4711")
            draussen = await person("draussen", "Draussen-PIN-4711")

            async def agent_von(h, name):
                r = await c.post("/api/agents", headers=h,
                                 json={"name": name, "model": "openai/x"})
                assert r.status_code == 201, r.text
                return r.json()["id"]

            a_drin = await agent_von(drin, "Innen")

            # --- Ein geschlossener Raum mit einem Unterforum --------------
            r = await c.post("/api/forums", headers=drin,
                             json={"name": "Stiller Raum", "sichtbar": "geschlossen"})
            assert r.status_code == 201, r.text
            raum = r.json()["id"]
            assert r.json()["sichtbar"] == "geschlossen", r.json()

            # Ein Unterforum darunter ist KEIN Loch in der Wand.
            r = await c.post("/api/forums", headers=drin,
                             json={"name": "Darunter", "parent_id": raum})
            assert r.status_code == 201, r.text
            unten = r.json()["id"]
            assert r.json()["sichtbar"] == "offen", "steht offen, vererbt aber zu"

            r = await c.post("/api/threads", headers=drin, json={
                "title": "Nicht fuer alle",
                "goal": "Soll drinnen bleiben.",
                "agent_ids": [a_drin],
                "forum_id": unten,
                "mode": "roundrobin", "max_rounds": 1, "pace_seconds": 0,
                "start_now": False})
            assert r.status_code == 201, r.text
            thema = r.json()["id"]

            r = await c.post(f"/api/threads/{thema}/posts", headers=drin,
                             json={"content": "Das hier liest niemand von aussen."})
            assert r.status_code in (200, 201), r.text
            beitrag = r.json()["id"]
            print("[ok] Geschlossener Raum mit Unterforum und einem Beitrag")

            # --- Jeder einzelne Weg nach draussen --------------------------
            # Wer drin ist, sieht alles.
            for weg, erwartet in (
                (f"/api/forums", 200),
                (f"/api/threads?forum_id={raum}", 200),
                (f"/api/threads/{thema}", 200),
                (f"/api/threads/{thema}/posts", 200),
            ):
                r = await c.get(weg, headers=drin)
                assert r.status_code == erwartet, f"{weg}: {r.text}"

            namen = [f["name"] for f in (await c.get("/api/forums", headers=drin)).json()]
            assert "Stiller Raum" in namen and "Darunter" in namen, namen

            # Wer draussen ist - und der Admin - sehen nichts davon.
            for wer, wie in (("draussen", draussen), ("admin", A)):
                foren = (await c.get("/api/forums", headers=wie)).json()
                namen = [f["name"] for f in foren]
                assert "Stiller Raum" not in namen, f"{wer} sieht den Raum: {namen}"
                assert "Darunter" not in namen, f"{wer} sieht das Unterforum: {namen}"

                titel = [t["title"] for t in (await c.get("/api/threads", headers=wie)).json()]
                assert "Nicht fuer alle" not in titel, f"{wer} sieht das Thema: {titel}"

                # Direkt aufgerufen: 404, nicht 403 - ein 403 verriete, dass
                # es das Thema gibt.
                r = await c.get(f"/api/threads/{thema}", headers=wie)
                assert r.status_code == 404, f"{wer}: {r.status_code}"
                r = await c.get(f"/api/threads/{thema}/posts", headers=wie)
                assert r.status_code == 404, f"{wer}: {r.text}"
                r = await c.post(f"/api/posts/{beitrag}/translate", headers=wie,
                                 json={"ziel": "en"})
                assert r.status_code == 404, f"{wer} konnte uebersetzen: {r.text}"
                r = await c.post(f"/api/threads/{thema}/posts", headers=wie,
                                 json={"content": "Hallo?"})
                assert r.status_code == 404, f"{wer} konnte hineinschreiben: {r.text}"
                r = await c.post(f"/api/threads/{thema}/control", headers=wie,
                                 json={"action": "stop"})
                assert r.status_code == 404, f"{wer} konnte steuern: {r.text}"
                r = await c.get(f"/api/forums/{raum}/mitglieder", headers=wie)
                assert r.status_code == 404, f"{wer} sieht die Mitglieder: {r.text}"

                # Auch der Punkt verraet nichts: er sagt sonst, dass es da
                # etwas gibt und dass sich darin etwas tut.
                stand = (await c.get("/api/ungelesen", headers=wie)).json()
                assert thema not in stand["threads"], f"{wer}: {stand}"
                assert raum not in stand["foren"], f"{wer}: {stand}"
                assert unten not in stand["foren"], f"{wer}: {stand}"
            print("[ok] Von aussen ist nichts zu sehen - auch nicht fuer den Admin")

            # --- Und nichts hineinschieben --------------------------------
            a_aussen = await agent_von(draussen, "Aussen")
            r = await c.post("/api/threads", headers=draussen, json={
                "title": "Reingemogelt", "goal": "Soll nicht gehen.",
                "agent_ids": [a_aussen], "forum_id": unten,
                "mode": "roundrobin", "max_rounds": 1, "pace_seconds": 0,
                "start_now": False})
            assert r.status_code in (400, 403, 404), f"Thema im fremden Raum: {r.text}"

            r = await c.post("/api/threads", headers=draussen, json={
                "title": "Eigenes", "goal": "Steht offen.",
                "agent_ids": [a_aussen],
                "mode": "roundrobin", "max_rounds": 1, "pace_seconds": 0,
                "start_now": False})
            assert r.status_code == 201, r.text
            offenes = r.json()["id"]
            r = await c.patch(f"/api/threads/{offenes}", headers=draussen,
                              json={"forum_id": unten})
            assert r.status_code == 400, f"in den fremden Raum verschoben: {r.text}"

            # Ein Unterforum unter einen fremden Raum haengen geht auch nicht.
            r = await c.post("/api/forums", headers=draussen, json={"name": "Mein Ast"})
            ast = r.json()["id"]
            r = await c.patch(f"/api/forums/{ast}", headers=draussen,
                              json={"parent_id": raum})
            assert r.status_code == 400, f"unter den fremden Raum gehaengt: {r.text}"
            print("[ok] Von aussen kommt auch nichts hinein")

            # --- Die Stroeme ----------------------------------------------
            r = await c.get(f"/api/threads/{thema}/stream",
                            params={"token": draussen["Authorization"].split()[1]})
            assert r.status_code == 404, f"Livestream aus dem Raum: {r.status_code}"
            print("[ok] Der Livestream folgt derselben Regel")

            # --- Aufnehmen und hinauswerfen -------------------------------
            # Einladen koennen muss auch, wer kein Admin ist - sonst bliebe
            # jeder selbst angelegte Raum fuer immer einsam. Dafuer gibt es
            # /api/personen mit Kennung und Name, sonst nichts.
            r = await c.get("/api/users", headers=drin)
            assert r.status_code == 403, "Kontozustaende bleiben Admin-Sache"
            r = await c.get("/api/personen", headers=drin)
            assert r.status_code == 200, r.text
            verzeichnis = r.json()
            assert {"user_id", "name"} == set(verzeichnis[0]), verzeichnis[0]
            id_draussen = next(x["user_id"] for x in verzeichnis if x["name"] == "draussen")

            leute = (await c.get("/api/users", headers=A)).json()
            r = await c.post(f"/api/forums/{raum}/mitglieder", headers=drin,
                             json={"user_id": id_draussen})
            assert r.status_code == 201, r.text
            titel = [t["title"] for t in (await c.get("/api/threads", headers=draussen)).json()]
            assert "Nicht fuer alle" in titel, f"aufgenommen, sieht aber nichts: {titel}"

            # Der Admin ist immer noch draussen.
            r = await c.get(f"/api/threads/{thema}", headers=A)
            assert r.status_code == 404, "Admin darf auch jetzt nicht hinein"

            # Die letzte Person kann nicht gehen - sonst waere der Raum zu
            # und leer, und niemand kaeme mehr hinein.
            id_drin = next(x["id"] for x in leute if x["name"] == "drin")
            r = await c.delete(f"/api/forums/{raum}/mitglieder/{id_draussen}", headers=drin)
            assert r.status_code == 204, r.text
            r = await c.delete(f"/api/forums/{raum}/mitglieder/{id_drin}", headers=drin)
            assert r.status_code == 400, "das letzte Mitglied darf nicht gehen"
            print("[ok] Aufnehmen wirkt sofort, der letzte kann nicht gehen")

            # --- Loeschen duerfen, ohne lesen zu duerfen ------------------
            # Zweiter Raum, damit der erste fuer den Loeschtest heil bleibt.
            r = await c.post("/api/forums", headers=drin,
                             json={"name": "Wird blind geloescht", "sichtbar": "geschlossen"})
            blind = r.json()["id"]
            r = await c.post("/api/threads", headers=drin, json={
                "title": "Geht mit", "goal": "Verschwindet.",
                "agent_ids": [a_drin], "forum_id": blind,
                "mode": "roundrobin", "max_rounds": 1, "pace_seconds": 0,
                "start_now": False})
            mitgegangen = r.json()["id"]

            r = await c.delete(f"/api/forums/{blind}", headers=A)
            assert r.status_code == 204, f"Admin darf blind loeschen: {r.text}"
            r = await c.get(f"/api/threads/{mitgegangen}", headers=drin)
            assert r.status_code == 404, "das Thema muss mitgegangen sein"
            print("[ok] Der Admin loescht ungesehen - samt Inhalt")

            # --- Der Loeschfall einer Person ------------------------------
            # 'drin' ist allein im stillen Raum. Wird sie geloescht, darf der
            # Admin den Raum NICHT erben - er geht ganz.
            r = await c.delete(f"/api/users/{id_drin}", headers=A)
            assert r.status_code == 200, r.text
            ergebnis = r.json()
            assert ergebnis["raeume_entfernt"] == 1, ergebnis
            r = await c.get(f"/api/threads/{thema}", headers=A)
            assert r.status_code == 404, "der Admin hat den Raum geerbt!"
            foren = [f["name"] for f in (await c.get("/api/forums", headers=A)).json()]
            assert "Stiller Raum" not in foren, foren
            print("[ok] Loeschen einer Person vererbt keinen geschlossenen Raum")

            # --- Mit einem zweiten Mitglied bleibt der Raum stehen --------
            zwei = await person("zwei", "Zwei-PIN-4711")
            drei = await person("drei", "Drei-PIN-4711")
            a_zwei = await agent_von(zwei, "Zwo")
            r = await c.post("/api/forums", headers=zwei,
                             json={"name": "Zu zweit", "sichtbar": "geschlossen"})
            geteilt = r.json()["id"]
            leute = (await c.get("/api/users", headers=A)).json()
            id_zwei = next(x["id"] for x in leute if x["name"] == "zwei")
            id_drei = next(x["id"] for x in leute if x["name"] == "drei")
            r = await c.post(f"/api/forums/{geteilt}/mitglieder", headers=zwei,
                             json={"user_id": id_drei})
            assert r.status_code == 201, r.text
            r = await c.post("/api/threads", headers=zwei, json={
                "title": "Bleibt zu zweit", "goal": "Soll stehen bleiben.",
                "agent_ids": [a_zwei], "forum_id": geteilt,
                "mode": "roundrobin", "max_rounds": 1, "pace_seconds": 0,
                "start_now": False})
            bleibt = r.json()["id"]

            r = await c.delete(f"/api/users/{id_zwei}", headers=A)
            assert r.status_code == 200, r.text
            assert r.json()["raeume_entfernt"] == 0, r.json()
            # 'drei' sieht es weiter, der Admin nicht.
            r = await c.get(f"/api/threads/{bleibt}", headers=drei)
            assert r.status_code == 200, "das verbliebene Mitglied verliert den Raum"
            assert r.json()["creator_id"] == id_drei, r.json()
            r = await c.get(f"/api/threads/{bleibt}", headers=A)
            assert r.status_code == 404, "der Admin hat geerbt!"
            print("[ok] Mit einem zweiten Mitglied bleibt der Raum - beim Mitglied")

    print("\nALLE TESTS BESTANDEN")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main_test()))

"use strict";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

let TOKEN = localStorage.getItem("agora_token") || "";
let ME = null;
let CURRENT = null;      // aktuell geoeffneter Thread
let SOURCE = null;       // EventSource des geoeffneten Themas
let UEBERSICHT = null;   // EventSource fuer die Liste an der Seite
const NODES = new Map(); // post_id -> DOM-Knoten
let FORUMS = [];         // flache Liste, der Baum entsteht beim Zeichnen
let FORUM_FILTER = "";   // "" = alle, "-" = ohne Forum, sonst forum_id

// ---------------------------------------------------------------- API ----
async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${TOKEN}`,
      // Zusaetzlich, falls eine vorgelagerte Middleware den
      // Authorization-Header fuer sich beansprucht.
      "X-Agora-Token": TOKEN,
      // Damit Fehlermeldungen in derselben Sprache kommen wie die Oberflaeche.
      "X-Agora-Sprache": SPRACHE,
      ...(options.headers || {}),
    },
  });
  if (response.status === 204) return null;
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) throw new Error(data?.detail || `HTTP ${response.status}`);
  return data;
}

async function dokumentHochladen(threadId, datei) {
  // Nicht ueber api(): dort steht Content-Type auf JSON, und den
  // multipart-Trennstring muss der Browser selbst setzen.
  const inhalt = new FormData();
  inhalt.append("datei", datei);
  const antwort = await fetch(`/api/threads/${threadId}/documents`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${TOKEN}`,
      "X-Agora-Token": TOKEN,
      "X-Agora-Sprache": SPRACHE,
    },
    body: inhalt,
  });
  const text = await antwort.text();
  const daten = text ? JSON.parse(text) : null;
  if (!antwort.ok) throw new Error(daten?.detail || `HTTP ${antwort.status}`);
  return daten;
}

const escapeHtml = (value) =>
  String(value).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]),
  );

const clock = (iso) =>
  new Date(iso).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });

// Fuer Dinge, die Tage alt sein koennen - bei einer Sicherung ist die Uhrzeit
// allein wertlos.
const zeitpunkt = (iso) => new Date(iso).toLocaleString("de-DE");

// Unter 1 kB stuenden sonst "0 kB" da.
const groesse = (bytes) =>
  bytes < 1024
    ? `${bytes} B`
    : bytes < 1024 * 1024
      ? `${(bytes / 1024).toFixed(1)} kB`
      : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;

// --------------------------------------------------------------- Login ---
$("#gate-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  TOKEN = $("#gate-token").value.trim();
  $("#gate-error").textContent = t("gate.laeuft");
  try {
    // Ueber /api/login statt /api/me: die Antwort setzt ein Cookie, und
    // Cookies kommen auch durch vorgelagerte Authentifizierungs-Schichten,
    // die Header umschreiben.
    ME = await api("/api/login", {
      method: "POST",
      body: JSON.stringify({ token: TOKEN, pin: $("#gate-pin").value }),
    });
    if (ME.new_token) {
      // Einmal-Token eingeloest: ab jetzt gilt dieses hier, und es kennt
      // niemand sonst - auch kein Admin.
      TOKEN = ME.new_token;
      zeigeToken(t("token.dauerhaftTitel"), t("token.dauerhaftText"), ME.new_token);
    }
    localStorage.setItem("agora_token", TOKEN);
    $("#gate-error").textContent = "";
    $("#gate-pin").value = "";
    await boot();
  } catch (error) {
    $("#gate-error").textContent = error.message;
  }
});

// Was der Server kann, entscheidet, was die Oberflaeche zeigt.
let KANN = {};
fetch("/healthz")
  .then((r) => r.json())
  .then((d) => {
    KANN = d;
    $("#antrag-oeffnen").hidden = d.antraege_offen === false;
    $("#werkzeuge-zeile").hidden = !d.werkzeuge;
    $("#werkzeuge-hinweis").hidden = !d.werkzeuge;
  })
  .catch(() => {});

$("#antrag-oeffnen").addEventListener("click", () => {
  $("#gate-form").hidden = true;
  $("#antrag-form").hidden = false;
});

$("#antrag-zurueck").addEventListener("click", () => {
  $("#antrag-form").hidden = true;
  $("#gate-form").hidden = false;
});

$("#antrag-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const meldung = $("#antrag-meldung");
  meldung.style.color = "";
  meldung.textContent = "";
  try {
    // Ohne Anmeldung, deshalb ohne api(): dort haengt immer ein Token dran.
    const antwort = await fetch("/api/requests", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Agora-Sprache": SPRACHE },
      body: JSON.stringify({
        name: $("#antrag-name").value.trim(),
        contact: $("#antrag-kontakt").value.trim(),
        note: $("#antrag-note").value.trim(),
      }),
    });
    const daten = JSON.parse((await antwort.text()) || "null");
    if (!antwort.ok) throw new Error(daten?.detail || `HTTP ${antwort.status}`);
    meldung.style.color = "var(--ok)";
    meldung.textContent = t("antrag.eingegangen");
    $("#antrag-form").reset();
  } catch (error) {
    meldung.textContent = error.message;
  }
});


/** Wird von i18n.js nach einem Sprachwechsel gerufen. */
async function nachSprachwechsel() {
  if (!ME) return; // Auf der Anmeldeseite genuegt uebersetzeSeite()
  zeichneSchloss();
  zeichneTeilnahme();
  zeichneForumBaum();
  await loadThreads();
  await ungelesenHolen();
  if (CURRENT) await openThread(CURRENT.id);
  await Promise.all([loadAgents(), loadCredentials()]);
  if (ME.is_admin) await Promise.all([loadUsers(), loadRequests()]);
}

$("#pin-aendern").addEventListener("click", () => {
  $("#pin-meldung").textContent = "";
  $("#pin-form").reset();
  $("#pin-alt").closest("label").hidden = !ME.has_pin;
  $("#pin-dialog").showModal();
});

$("#pin-abbrechen").addEventListener("click", () => $("#pin-dialog").close());

$("#pin-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    ME = await api("/api/me/pin", {
      method: "POST",
      body: JSON.stringify({
        alte_pin: $("#pin-alt").value,
        neue_pin: $("#pin-neu").value,
      }),
    });
    $("#pin-dialog").close();
    zeichneSchloss();
  } catch (error) {
    $("#pin-meldung").textContent = error.message;
  }
});

// ------------------------------------------------------ Freischaltung ---
function istFrei() {
  return ME.unlocked_until && new Date(ME.unlocked_until) > new Date();
}

function zeichneSchloss() {
  const knopf = $("#schloss");
  if (istFrei()) {
    const bis = new Date(ME.unlocked_until).toLocaleTimeString("de-DE", {
      hour: "2-digit",
      minute: "2-digit",
    });
    knopf.textContent = t("schloss.frei", { zeit: bis });
    knopf.style.color = "var(--ok)";
    knopf.title = t("schloss.sperrenTitel");
  } else {
    knopf.textContent = t("schloss.gesperrt");
    knopf.style.color = "var(--warn)";
    knopf.title = t("schloss.freischaltenTitel");
  }
}

$("#schloss").addEventListener("click", async () => {
  if (istFrei()) {
    ME = await api("/api/me/lock", { method: "POST" });
    zeichneSchloss();
    return;
  }
  $("#frei-meldung").textContent = "";
  $("#frei-form").reset();
  $("#frei-dialog").showModal();
});

$("#frei-abbrechen").addEventListener("click", () => $("#frei-dialog").close());

$("#frei-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    // Freischalten ist nichts anderes als eine Anmeldung: nur mit der PIN
    // laesst sich der Datenschluessel oeffnen.
    ME = await api("/api/login", {
      method: "POST",
      body: JSON.stringify({ token: TOKEN, pin: $("#frei-pin").value }),
    });
    $("#frei-dialog").close();
    zeichneSchloss();
    await loadThreads();
  } catch (error) {
    $("#frei-meldung").textContent = error.message;
  }
});

// ----------------------------------------------------- Teilnahmefenster ---
function teilnahmeText(stunden) {
  if (!stunden) return t("teilnahme.unbegrenzt");
  if (stunden === 1) return t("teilnahme.eineStunde");
  return t("teilnahme.stunden", { n: stunden });
}

function zeichneTeilnahme() {
  const regler = $("#teilnahme");
  regler.value = ME.teilnahme_stunden ?? 24;
  $("#teilnahme-text").textContent = teilnahmeText(Number(regler.value));
}

$("#teilnahme").addEventListener("input", (event) => {
  $("#teilnahme-text").textContent = teilnahmeText(Number(event.target.value));
});

$("#teilnahme").addEventListener("change", async (event) => {
  try {
    ME = await api("/api/me", {
      method: "PATCH",
      body: JSON.stringify({ teilnahme_stunden: Number(event.target.value) }),
    });
    zeichneTeilnahme();
  } catch (error) {
    alert(error.message);
  }
});

function zeigeToken(titel, text, wert) {
  $("#token-titel").textContent = titel;
  $("#token-text").textContent = text;
  $("#token-wert").value = wert;
  $("#token-dialog").showModal();
}

$("#token-kopieren").addEventListener("click", async () => {
  $("#token-wert").select();
  try {
    await navigator.clipboard.writeText($("#token-wert").value);
    $("#token-kopieren").textContent = t("token.kopiert");
  } catch {
    $("#token-kopieren").textContent = t("token.vonHand");
  }
});

$("#token-ok").addEventListener("click", () => {
  $("#token-dialog").close();
  $("#token-kopieren").textContent = t("token.kopieren");
});

$("#logout").addEventListener("click", async () => {
  localStorage.removeItem("agora_token");
  try {
    await api("/api/logout", { method: "POST" });
  } catch {
    /* auch ohne gueltige Sitzung abmelden */
  }
  location.reload();
});

async function boot() {
  $("#gate").hidden = true;
  $("#app").hidden = false;
  $("#whoami").textContent = `${ME.name}${ME.is_admin ? " (Admin)" : ""}`;
  $("#tab-people").hidden = !ME.is_admin;
  zeichneSchloss();
  zeichneTeilnahme();
  const laden = [loadForums(), loadAgents(), loadCredentials()];
  if (ME.is_admin) laden.push(loadUsers(), loadRequests());
  await Promise.all(laden);
  await ungelesenHolen();
  uebersichtVerbinden();
}

/** Haelt die Liste an der Seite aktuell - auch ohne geoeffnetes Thema.
 *
 * Der Strom eines Themas liefert nur Ereignisse dieses einen Themas. Ohne
 * diesen zweiten Anschluss sieht man erst nach einem Neuladen, dass anderswo
 * weiterdiskutiert wurde - und hat man gar kein Thema offen, nie.
 */
function uebersichtVerbinden() {
  if (UEBERSICHT) UEBERSICHT.close();
  UEBERSICHT = new EventSource(`/api/stream?token=${encodeURIComponent(TOKEN)}`);

  UEBERSICHT.addEventListener("thread.update", async () => {
    await loadThreads();
    await ungelesenHolen();
  });

  // Faellt die Verbindung (Proxy, Schlaf, Netzwechsel), baut der Browser sie
  // selbst wieder auf - aber der Stand dazwischen fehlt. Also einmal
  // nachladen, sobald es weitergeht.
  UEBERSICHT.addEventListener("open", () => {
    loadThreads().catch(() => {});
  });
}

// --------------------------------------------------------------- Views ---
$$(".tab").forEach((tab) =>
  tab.addEventListener("click", () => {
    $$(".tab").forEach((other) => other.classList.toggle("active", other === tab));
    $$(".view").forEach((view) => (view.hidden = view.id !== `view-${tab.dataset.view}`));
  }),
);

// --------------------------------------------------------------- Foren ---
async function loadForums() {
  FORUMS = await api("/api/forums");
  zeichneForumBaum();
  await loadThreads();
}

function kinderVon(parentId) {
  return FORUMS.filter((f) => (f.parent_id || null) === parentId);
}

// Wo liegt etwas, das ich noch nicht gesehen habe? Wird nach jedem Ereignis
// nachgeholt, das etwas daran aendern koennte - nicht in einem Takt.
let UNGELESEN = { threads: [], foren: [], ohne_forum: false };

async function ungelesenHolen() {
  try {
    UNGELESEN = await api("/api/ungelesen");
  } catch {
    // Ein fehlender Punkt ist kein Grund, die Seite scheitern zu lassen.
    return;
  }
  zeichneForumBaum();
  punkteInThemenliste();
}

/** Diesem Thema den Punkt nehmen - ich sehe es ja gerade an. */
async function gelesenMelden(threadId) {
  try {
    await api(`/api/threads/${threadId}/gelesen`, { method: "POST" });
  } catch {
    // Im schlimmsten Fall leuchtet der Punkt eine Weile zu lang.
    return;
  }
  await ungelesenHolen();
}

function punkteInThemenliste() {
  const neu = new Set(UNGELESEN.threads);
  for (const item of $$("#thread-list li")) {
    // Nicht am offenen Thema: das lese ich gerade.
    const zeigen = neu.has(item.dataset.id) && !(CURRENT && CURRENT.id === item.dataset.id);
    item.classList.toggle("neu", zeigen);
    if (zeigen) item.setAttribute("aria-label", t("forum.neuesDa"));
    else item.removeAttribute("aria-label");
  }
}

function zeichneForumBaum() {
  const tree = $("#forum-tree");
  tree.innerHTML = "";

  const eintrag = (text, wert, tiefe, forum) => {
    const li = document.createElement("li");
    li.style.paddingLeft = `${0.5 + tiefe * 0.9}rem`;
    li.classList.toggle("active", FORUM_FILTER === wert);
    li.textContent = text;
    // Der Punkt wird nicht allein durch Farbe getragen: fetter Text und ein
    // dunkler Ring darum, sonst ist er fuer Farbschwache nicht da.
    const hatNeues =
      wert === "-" ? UNGELESEN.ohne_forum : wert !== "" && UNGELESEN.foren.includes(wert);
    li.classList.toggle("neu", Boolean(hatNeues));
    if (hatNeues) li.setAttribute("aria-label", `${text} - ${t("forum.neuesDa")}`);
    li.addEventListener("click", async (event) => {
      if (event.target.classList.contains("weg")) return;
      FORUM_FILTER = wert;
      zeichneForumBaum();
      await loadThreads();
    });
    if (forum && forum.sichtbar === "geschlossen") {
      // Wer das sieht, ist ohnehin drin - das Schloss sagt nur, dass es
      // nicht alle sehen.
      li.classList.add("geschlossen");
      li.title = t("forum.geschlossenTitel");

      const leute = document.createElement("button");
      leute.className = "weg mitglieder";
      leute.type = "button";
      leute.textContent = "\u{1F465}";
      leute.title = t("forum.mitgliederTitel");
      leute.addEventListener("click", (event) => {
        event.stopPropagation();
        mitgliederPflegen(forum);
      });
      li.appendChild(leute);
    }

    if (forum && (forum.creator_id === ME.id || ME.is_admin)) {
      // Damit laesst sich eine Ebene einziehen: erst das neue Oberforum
      // anlegen, dann die vorhandenen daruntersetzen.
      const umhaengen = document.createElement("button");
      umhaengen.className = "weg umhaengen";
      umhaengen.type = "button";
      umhaengen.textContent = "\u21b3";
      umhaengen.title = t("forum.umhaengenTitel");
      umhaengen.addEventListener("click", (event) => {
        event.stopPropagation();
        forumUmhaengen(forum);
      });
      li.appendChild(umhaengen);

      const weg = document.createElement("button");
      weg.className = "weg";
      weg.type = "button";
      weg.textContent = "×";
      weg.title = t("forum.loeschenTitel");
      weg.addEventListener("click", async (event) => {
        event.stopPropagation();
        try {
          await api(`/api/forums/${forum.id}`, { method: "DELETE" });
          if (FORUM_FILTER === forum.id) FORUM_FILTER = "";
          await loadForums();
        } catch (error) {
          alert(error.message);
        }
      });
      li.appendChild(weg);
    }
    tree.appendChild(li);
  };

  eintrag(t("forum.alleThemen"), "", 0, null);

  const zeichne = (parentId, tiefe) => {
    for (const forum of kinderVon(parentId)) {
      eintrag(forum.name, forum.id, tiefe, forum);
      zeichne(forum.id, tiefe + 1);
    }
  };
  zeichne(null, 1);

  eintrag(t("forum.ohneForum"), "-", 1, null);
}

/** Ein Forum unter ein anderes haengen - oder ganz nach oben.
 *
 * Bewusst eine Auswahlliste und kein Ziehen mit der Maus: der Baum kann
 * tiefer sein als das Fenster hoch ist, und ein Ring waere mit der Maus
 * schnell gebaut. Der Server weist ihn ab, aber erst hinterher.
 */
function forumUmhaengen(forum) {
  const auswahl = document.createElement("select");
  auswahl.innerHTML = forumOptionen(forum.parent_id, true, t("forum.ganzOben"));
  // Sich selbst als eigenes Oberforum gibt es nicht.
  for (const option of auswahl.options) {
    if (option.value === forum.id) option.remove();
  }

  const feld = document.createElement("dialog");
  feld.className = "card";
  const titel = document.createElement("p");
  titel.textContent = t("forum.umhaengenFrage", { name: forum.name });
  const reihe = document.createElement("div");
  reihe.className = "row";
  const ok = document.createElement("button");
  ok.className = "primary";
  ok.textContent = t("forum.umhaengen");
  const ab = document.createElement("button");
  ab.textContent = t("allgemein.abbrechen");
  reihe.append(ok, ab);
  feld.append(titel, auswahl, reihe);
  document.body.appendChild(feld);
  feld.showModal();

  const schliessen = () => {
    feld.close();
    feld.remove();
  };
  ab.addEventListener("click", schliessen);
  ok.addEventListener("click", async () => {
    try {
      await api(`/api/forums/${forum.id}`, {
        method: "PATCH",
        body: JSON.stringify({ parent_id: auswahl.value || null }),
      });
      schliessen();
      await loadForums();
    } catch (error) {
      alert(error.message);
    }
  });
}

/** Wer ist in diesem Raum - und wer soll noch hinein?
 *
 * Bewusst offen fuer jedes Mitglied, nicht nur fuer den Ersteller: ein
 * Raum, in den nur eine Person jemanden holen kann, steht still, sobald
 * sie weg ist.
 */
async function mitgliederPflegen(forum) {
  const feld = document.createElement("dialog");
  feld.className = "card";

  const titel = document.createElement("h3");
  titel.textContent = t("forum.mitgliederTitel2", { name: forum.name });
  const hinweis = document.createElement("p");
  hinweis.className = "muted";
  hinweis.textContent = t("forum.mitgliederHinweis");
  const liste = document.createElement("ul");
  liste.className = "mitgliederliste";

  const dazu = document.createElement("select");
  const dazuKnopf = document.createElement("button");
  dazuKnopf.textContent = t("forum.dazuholen");
  const zu = document.createElement("button");
  zu.textContent = t("allgemein.schliessen");
  const fehler = document.createElement("p");
  fehler.className = "error";

  const reihe = document.createElement("div");
  reihe.className = "row";
  reihe.append(dazu, dazuKnopf);
  const unten = document.createElement("div");
  unten.className = "row";
  unten.append(zu);
  feld.append(titel, hinweis, liste, reihe, fehler, unten);
  document.body.appendChild(feld);
  feld.showModal();

  const zeichnen = async () => {
    fehler.textContent = "";
    let drin = [];
    try {
      drin = await api(`/api/forums/${forum.id}/mitglieder`);
    } catch (error) {
      fehler.textContent = error.message;
      return;
    }
    liste.innerHTML = "";
    for (const person of drin) {
      const zeile = document.createElement("li");
      zeile.textContent = person.name;
      const raus = document.createElement("button");
      raus.className = "link";
      raus.textContent =
        person.user_id === ME.id ? t("forum.selbstGehen") : t("forum.hinauswerfen");
      raus.addEventListener("click", async () => {
        try {
          await api(`/api/forums/${forum.id}/mitglieder/${person.user_id}`,
                    { method: "DELETE" });
          if (person.user_id === ME.id) {
            // Man sieht den Raum jetzt selbst nicht mehr.
            feld.close();
            feld.remove();
            await loadForums();
            return;
          }
          await zeichnen();
        } catch (error) {
          fehler.textContent = error.message;
        }
      });
      zeile.appendChild(raus);
      liste.appendChild(zeile);
    }

    // /api/personen traegt nur Kennung und Name und steht allen offen -
    // sonst koennte niemand ausser einem Admin jemanden hereinholen, und
    // der sieht den Raum gerade nicht.
    dazu.innerHTML = "";
    const schon = new Set(drin.map((p) => p.user_id));
    for (const person of await api("/api/personen")) {
      if (schon.has(person.user_id)) continue;
      const option = document.createElement("option");
      option.value = person.user_id;
      option.textContent = person.name;
      dazu.appendChild(option);
    }
    dazu.hidden = dazu.options.length === 0;
    dazuKnopf.hidden = dazu.hidden;
  };

  dazuKnopf.addEventListener("click", async () => {
    if (!dazu.value) return;
    try {
      await api(`/api/forums/${forum.id}/mitglieder`, {
        method: "POST",
        body: JSON.stringify({ user_id: dazu.value }),
      });
      await zeichnen();
    } catch (error) {
      fehler.textContent = error.message;
    }
  });
  zu.addEventListener("click", () => {
    feld.close();
    feld.remove();
  });

  await zeichnen();
}

function forumName(id) {
  const forum = FORUMS.find((f) => f.id === id);
  return forum ? forum.name : t("forum.alleThemen");
}

function forumOptionen(auswahl, mitLeer, leerText) {
  const teile = mitLeer ? [`<option value="">${leerText}</option>`] : [];
  const zeichne = (parentId, tiefe) => {
    for (const forum of kinderVon(parentId)) {
      const einzug = "  ".repeat(tiefe);
      const gewaehlt = forum.id === auswahl ? " selected" : "";
      teile.push(`<option value="${forum.id}"${gewaehlt}>${einzug}${escapeHtml(forum.name)}</option>`);
      zeichne(forum.id, tiefe + 1);
    }
  };
  zeichne(null, 0);
  return teile.join("");
}

$("#new-forum-btn").addEventListener("click", () => {
  $("#f-parent").innerHTML = forumOptionen(
    FORUM_FILTER && FORUM_FILTER !== "-" ? FORUM_FILTER : null,
    true,
    t("forum.ganzOben"),
  );
  $("#forum-error").textContent = "";
  $("#forum-dialog").showModal();
});

$("#f-cancel").addEventListener("click", () => $("#forum-dialog").close());

$("#forum-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api("/api/forums", {
      method: "POST",
      body: JSON.stringify({
        name: $("#f-name").value.trim(),
        description: $("#f-desc").value.trim(),
        parent_id: $("#f-parent").value || null,
        sichtbar: $("#f-geschlossen").checked ? "geschlossen" : "offen",
      }),
    });
    $("#forum-dialog").close();
    $("#forum-form").reset();
    await loadForums();
  } catch (error) {
    $("#forum-error").textContent = error.message;
  }
});

// ------------------------------------------------------------- Threads ---
async function loadThreads() {
  $("#thread-head").textContent =
    FORUM_FILTER === "-" ? t("forum.ohneForum") : forumName(FORUM_FILTER);
  const pfad = FORUM_FILTER ? `/api/threads?forum_id=${encodeURIComponent(FORUM_FILTER)}` : "/api/threads";
  const threads = await api(pfad);
  const list = $("#thread-list");
  list.innerHTML = "";
  for (const thread of threads) {
    const item = document.createElement("li");
    item.dataset.id = thread.id;
    item.classList.toggle("active", CURRENT && thread.id === CURRENT.id);
    item.innerHTML = `
      <div class="t-title">${escapeHtml(thread.title)}</div>
      <div class="muted">
        <span class="badge ${thread.status}">${thread.status}</span>
        ${thread.rounds_done}/${thread.max_rounds} ${t("thread.runden")}
      </div>`;
    item.addEventListener("click", () => openThread(thread.id));
    list.appendChild(item);
  }
  punkteInThemenliste();
}

async function openThread(threadId) {
  CURRENT = await api(`/api/threads/${threadId}`);
  const posts = await api(`/api/threads/${threadId}/posts`);
  NODES.clear();
  renderThread(CURRENT, posts);
  $$("#thread-list li").forEach((item) =>
    item.classList.toggle("active", item.dataset.id === threadId),
  );
  connectStream(threadId);
  await gelesenMelden(threadId);
}

function renderThread(thread, posts) {
  const roster = thread.participants
    .map(
      (agent) => `<span class="teilnehmer" data-agent="${agent.id}">
        <button type="button" class="wer" title="${t("thread.werTitel")}">${escapeHtml(agent.name)}</button>
        <span class="muted">(${escapeHtml(agent.model)})</span>
        <button type="button" class="raus" title="${t("thread.rausTitel")}">&times;</button></span>`,
    )
    .join(" ");

  $("#thread-pane").innerHTML = `
    <div class="thread-head">
      <div class="row">
        <h2 style="margin:0">${escapeHtml(thread.title)}</h2>
        <span class="badge ${thread.status}" id="th-status">${thread.status}</span>
      </div>
      <p class="muted" style="margin:.4rem 0">${escapeHtml(thread.goal || t("thread.keinZiel"))}</p>
      <div id="wer-ist-das" class="steckbrief" hidden></div>
      <p class="muted" style="margin:.2rem 0">${roster}
        <select id="dazu" style="width:auto;display:inline-block;margin-left:.4rem">
          <option value="">${t("thread.dazuholen")}</option>
        </select>
      </p>
      <p class="muted" style="margin:.2rem 0">
        <span id="th-rounds">${thread.rounds_done}/${thread.max_rounds}</span> ${t("thread.runden")} &middot;
        ${t("thread.takt")} ${thread.pace_seconds}s &middot; ${
          thread.stop_phrase
            ? `${t("thread.endeBeiEinigkeit")} <code>${escapeHtml(thread.stop_phrase)}</code>`
            : t("thread.bisBudget")
        }
        <span id="th-detail"></span>
      </p>
      <p class="muted" style="margin:.2rem 0">
        <label style="display:inline-flex;align-items:center;gap:.3rem;margin:0">
          ${t("thread.imForum")}
          <select id="th-forum" style="width:auto;display:inline-block">
            ${forumOptionen(thread.forum_id, true, t("forum.ohne"))}
          </select>
        </label>
      </p>
      <div class="controls">
        <button data-action="pause">${t("thread.pause")}</button>
        <button data-action="resume">${t("thread.weiter")}</button>
        <button data-action="extend">${t("thread.mehrRunden")}</button>
        <button data-action="stop">${t("thread.beenden")}</button>
        <button data-action="delete" class="danger">${t("thread.loeschen")}</button>
      </div>
    </div>
    <div class="posts" id="posts"></div>
    <form class="reply card" id="reply-form">
      <label>${t("thread.mitschreiben")}
        <textarea id="reply-text" placeholder="${t("thread.mitschreibenPh")}"></textarea>
      </label>
      <div class="row">
        <button type="submit" class="primary">${t("thread.senden")}</button>
        <label class="check" style="margin:0"><input type="checkbox" id="reply-resume" checked> ${t("thread.weiterlaufen")}</label>
      </div>
      <div class="row" style="margin-top:.7rem;flex-wrap:wrap">
        <label style="margin:0;font-size:.82rem">${t("thread.datei")}
          <input type="file" id="datei" style="width:auto;padding:.3rem">
        </label>
        <span class="muted" id="datei-hinweis">${t("thread.dateiHinweis")}</span>
      </div>
    </form>`;

  $("#th-detail").textContent = thread.status_detail ? ` — ${thread.status_detail}` : "";

  // Ein Thema umhaengen, wenn es im falschen Forum gelandet ist. Anders als
  // in einem Forum, das seine Adressen aus dem Forumsnamen baut, bleibt die
  // Kennung dieselbe - es braucht also weder Umleitung noch alte URL.
  $("#th-forum").addEventListener("change", async (event) => {
    try {
      await api(`/api/threads/${thread.id}`, {
        method: "PATCH",
        body: JSON.stringify({ forum_id: event.target.value || null }),
      });
      CURRENT.forum_id = event.target.value || null;
      await loadThreads();
    } catch (error) {
      alert(error.message);
      event.target.value = thread.forum_id || "";
    }
  });
  const container = $("#posts");
  posts.forEach((post) => container.appendChild(postNode(post)));
  // Beim Oeffnen ans Ende: man will den Stand sehen, nicht den Anfang.
  window.scrollTo(0, document.body.scrollHeight);
  zurueckgebliebenZuruecksetzen();

  $$("#thread-pane .controls button").forEach((button) =>
    button.addEventListener("click", () => controlThread(button.dataset.action)),
  );

  fuelleTeilnehmerAuswahl(thread);

  $$("#thread-pane .teilnehmer .raus").forEach((button) =>
    button.addEventListener("click", async () => {
      const agentId = button.closest(".teilnehmer").dataset.agent;
      try {
        await api(`/api/threads/${thread.id}/participants/${agentId}`, { method: "DELETE" });
        await openThread(thread.id);
      } catch (error) {
        alert(error.message);
      }
    }),
  );

  // Wer ist das eigentlich? Rolle und Persona bestimmen, was ein Agent
  // beitraegt - ohne sie liest man Beitraege, ohne zu wissen, wessen
  // Blickwinkel man vor sich hat.
  for (const knopf of $$(".teilnehmer .wer")) {
    knopf.addEventListener("click", () => {
      const id = knopf.closest(".teilnehmer").dataset.agent;
      const agent = thread.participants.find((a) => a.id === id);
      const kasten = $("#wer-ist-das");
      if (!agent || !kasten) return;
      // Zweiter Klick auf denselben schliesst wieder.
      if (!kasten.hidden && kasten.dataset.agent === id) {
        kasten.hidden = true;
        return;
      }
      kasten.dataset.agent = id;
      kasten.textContent = "";

      const kopf = document.createElement("strong");
      kopf.textContent = agent.name;
      kasten.appendChild(kopf);

      const zeile = document.createElement("p");
      zeile.className = "muted";
      zeile.style.margin = ".1rem 0";
      zeile.textContent = `${agent.model} · T=${agent.temperature} · ${agent.max_tokens} Tokens`;
      kasten.appendChild(zeile);

      for (const [beschriftung, wert] of [
        [t("agent.rolle"), agent.role],
        [t("agent.persona"), agent.persona],
      ]) {
        const absatz = document.createElement("p");
        absatz.style.margin = ".4rem 0 0";
        const name = document.createElement("span");
        name.className = "muted";
        name.textContent = `${beschriftung}: `;
        absatz.appendChild(name);
        // textContent, nicht innerHTML: die Persona schreibt ein Mensch.
        absatz.appendChild(document.createTextNode(wert || t("thread.ohneAngabe")));
        kasten.appendChild(absatz);
      }
      kasten.hidden = false;
    });
  }

  $("#dazu").addEventListener("change", async (event) => {
    const agentId = event.target.value;
    if (!agentId) return;
    try {
      await api(`/api/threads/${thread.id}/participants`, {
        method: "POST",
        body: JSON.stringify({ agent_id: agentId }),
      });
      await openThread(thread.id);
      await loadThreads();
    } catch (error) {
      alert(error.message);
      event.target.value = "";
    }
  });

  $("#datei").addEventListener("change", async (event) => {
    const datei = event.target.files[0];
    if (!datei) return;
    const hinweis = $("#datei-hinweis");
    hinweis.textContent = t("thread.dateiLaeuft", { name: datei.name });
    try {
      await dokumentHochladen(thread.id, datei);
      hinweis.textContent = "";
      event.target.value = "";
      await openThread(thread.id);
      await loadThreads();
    } catch (error) {
      hinweis.innerHTML = `<span style="color:var(--err)">${escapeHtml(error.message)}</span>`;
      event.target.value = "";
    }
  });

  $("#reply-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const content = $("#reply-text").value.trim();
    if (!content) return;
    await api(`/api/threads/${thread.id}/posts`, {
      method: "POST",
      body: JSON.stringify({ content, resume: $("#reply-resume").checked }),
    });
    $("#reply-text").value = "";
    await loadThreads();
  });
}

async function fuelleTeilnehmerAuswahl(thread) {
  // Siehe oben - fremde Agenten holt ihr Besitzer selbst dazu.
  const alle = (await api("/api/agents")).filter((a) => a.owner_id === ME.id);
  const drin = new Set(thread.participants.map((a) => a.id));
  const auswahl = $("#dazu");
  if (!auswahl) return;
  for (const agent of alle) {
    if (drin.has(agent.id)) continue;
    const option = document.createElement("option");
    option.value = agent.id;
    option.textContent = `${agent.name} — ${agent.role || agent.model}`;
    auswahl.appendChild(option);
  }
}

/** Fuellt einen Beitrag und hebt Code-Bloecke ab.
 *
 * Bewusst ohne innerHTML: der Text kommt aus Modellen und von Menschen, und
 * beides darf keine Auszeichnung in die Seite schmuggeln.
 */
// Mathematik zwischen Dollarzeichen: $...$ mitten im Satz, $$...$$ als
// eigene Zeile. Die Schreibweise ist die, die LLMs von sich aus benutzen -
// ohne Darstellung stehen die Dollarzeichen roh im Text.
//
// Ein einzelnes $ ist oft einfach Geld ("$5 pro Tausend Token"). Deshalb
// zaehlt nur, was auf beiden Seiten direkt an ein Zeichen grenzt, das kein
// Leerraum ist, und was keinen Zeilenumbruch enthaelt.
const MATHE = /\$\$([\s\S]+?)\$\$|\$(?![\s$])((?:[^$\n\\]|\\.)+?)(?<![\s\\])\$/g;

/** Text in einen Knoten schreiben und dabei Formeln setzen. */
function matheEinsetzen(ziel, text) {
  if (typeof katex === "undefined" || !text.includes("$")) {
    ziel.appendChild(document.createTextNode(text));
    return;
  }
  let zuletzt = 0;
  MATHE.lastIndex = 0;
  for (let treffer; (treffer = MATHE.exec(text)) !== null; ) {
    const abgesetzt = treffer[1] !== undefined;
    const formel = abgesetzt ? treffer[1] : treffer[2];
    if (treffer.index > zuletzt) {
      ziel.appendChild(document.createTextNode(text.slice(zuletzt, treffer.index)));
    }
    const knoten = document.createElement(abgesetzt ? "div" : "span");
    if (abgesetzt) knoten.className = "mathe-block";
    try {
      // throwOnError: false wuerde den Fehler rot in die Seite schreiben.
      // Lieber die Quelle stehen lassen - sie ist lesbar, die rote Zeile
      // nicht.
      katex.render(formel, knoten, { displayMode: abgesetzt, throwOnError: true });
      ziel.appendChild(knoten);
    } catch {
      ziel.appendChild(document.createTextNode(treffer[0]));
    }
    zuletzt = treffer.index + treffer[0].length;
  }
  if (zuletzt < text.length) {
    ziel.appendChild(document.createTextNode(text.slice(zuletzt)));
  }
}

function koerperFuellen(el, text) {
  el.textContent = "";
  const teile = String(text || "").split("```");
  teile.forEach((teil, nummer) => {
    if (nummer % 2 === 1) {
      const zeilen = teil.split("\n");
      const kopf = zeilen[0].trim();
      const istSprache = kopf.length > 0 && kopf.length < 20 && !kopf.includes(" ");
      const block = document.createElement("pre");
      block.className = "code";
      if (istSprache) block.dataset.sprache = kopf;
      block.textContent = (istSprache ? zeilen.slice(1).join("\n") : teil).replace(/\n$/, "");
      el.appendChild(block);
    } else if (teil) {
      // Nur ausserhalb der Code-Bloecke setzen: in Quelltext ist ein $ ein $.
      matheEinsetzen(el, teil);
    }
  });
}

function postNode(post) {
  const node = document.createElement("article");
  node.className =
    `post ${post.author_type === "human" ? "human" : ""}` +
    `${post.author_type === "system" ? " system" : ""}` +
    `${post.author_type === "document" ? " dokument" : ""}` +
    `${post.author_type === "tool" ? " rechner" : ""} ${post.status}`;
  node.dataset.id = post.id;
  node.innerHTML = `
    <div class="meta">
      <span class="who">${escapeHtml(post.author_name)}</span>
      <span>${
        post.author_type === "human"
          ? t("thread.mensch")
          : post.author_type === "system"
            ? t("thread.system")
            : post.author_type === "document"
              ? t("thread.dokument")
              : post.author_type === "tool"
                ? t("thread.rechner")
                : escapeHtml(post.model || "")
      }</span>
      <span>${t("thread.runde")} ${post.round_no}</span>
      <span>${clock(post.created_at)}</span>
    </div>
    <div class="body"></div>`;
  koerperFuellen(node.querySelector(".body"), post.content);
  node._roh = post.content || "";

  // Uebersetzen auf Knopfdruck: automatisch waere teuer fuer Beitraege,
  // die nie jemand liest.
  if (post.content && post.author_type !== "tool" && post.author_type !== "system") {
    const koerper = node.querySelector(".body");
    const original = post.content;
    const knopf = document.createElement("button");
    knopf.type = "button";
    knopf.className = "link uebersetzen";
    knopf.textContent = t("thread.uebersetzen");
    let zeigtUebersetzung = false;

    knopf.addEventListener("click", async () => {
      if (zeigtUebersetzung) {
        koerperFuellen(koerper, original);
        knopf.textContent = t("thread.uebersetzen");
        zeigtUebersetzung = false;
        return;
      }
      knopf.textContent = t("thread.uebersetztLaeuft");
      try {
        const antwort = await api(`/api/posts/${post.id}/translate`, {
          method: "POST",
          body: JSON.stringify({ ziel: zielsprache() }),
        });
        koerperFuellen(koerper, antwort.text);
        knopf.textContent = t("thread.original");
        zeigtUebersetzung = true;
      } catch (error) {
        knopf.textContent = t("thread.uebersetzen");
        alert(error.message);
      }
    });
    node.appendChild(knopf);
  }

  if (post.author_type === "document" && CURRENT) {
    const weg = document.createElement("button");
    weg.type = "button";
    weg.className = "link";
    weg.style.marginTop = ".4rem";
    weg.textContent = t("thread.dokumentWeg");
    weg.addEventListener("click", async () => {
      try {
        await api(`/api/threads/${CURRENT.id}/documents/${post.id}`, { method: "DELETE" });
        node.remove();
        NODES.delete(post.id);
      } catch (error) {
        alert(error.message);
      }
    });
    node.appendChild(weg);
  }

  NODES.set(post.id, node);
  return node;
}

async function controlThread(action) {
  if (!CURRENT) return;
  if (action === "delete") {
    if (!confirm(t("thread.loeschenFrage"))) return;
    await api(`/api/threads/${CURRENT.id}`, { method: "DELETE" });
    if (SOURCE) SOURCE.close();
    CURRENT = null;
    $("#thread-pane").innerHTML = `<div class="empty">${t("forum.keinThema")}</div>`;
    await loadThreads();
    return;
  }
  await api(`/api/threads/${CURRENT.id}/control`, {
    method: "POST",
    body: JSON.stringify({ action, rounds: 5 }),
  });
  await loadThreads();
}

// -------------------------------------------------------- Mitlaufen ------
// Wie weit man vom Ende weg sein darf und trotzdem mitgezogen wird.
const ENDE_TOLERANZ = 120;
// Wie viele Beitraege gekommen sind, seit man zurueckgeblieben ist.
let NEUE_SEIT_ABRISS = 0;

function amEndeDran() {
  return window.innerHeight + window.scrollY >= document.body.scrollHeight - ENDE_TOLERANZ;
}

/** Schreibt gerade jemand? Dann auf keinen Fall wegscrollen. */
function amTippen() {
  const aktiv = document.activeElement;
  if (!aktiv) return false;
  return ["TEXTAREA", "INPUT", "SELECT"].includes(aktiv.tagName) || aktiv.isContentEditable;
}

/** Ans Ende gehen - aber nur, wenn das gerade niemanden stoert.
 *
 * Der alte Stand sprang bei jedem neuen Beitrag ans Ende. Wer weiter oben
 * las, wurde mitten im Satz weggerissen. Jetzt entscheidet die Position:
 * wer schon unten steht, laeuft mit; wer liest, bleibt stehen und bekommt
 * einen Knopf.
 */
function mitlaufen({ neuerBeitrag = false } = {}) {
  if (amEndeDran() && !amTippen()) {
    window.scrollTo(0, document.body.scrollHeight);
    zurueckgebliebenZuruecksetzen();
    return;
  }
  if (neuerBeitrag) {
    NEUE_SEIT_ABRISS += 1;
    const knopf = $("#neue-beitraege");
    knopf.textContent = t("thread.neueBeitraege", { anzahl: NEUE_SEIT_ABRISS });
    knopf.hidden = false;
  }
}

function zurueckgebliebenZuruecksetzen() {
  NEUE_SEIT_ABRISS = 0;
  const knopf = $("#neue-beitraege");
  if (knopf) knopf.hidden = true;
}

$("#neue-beitraege").addEventListener("click", () => {
  window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
  zurueckgebliebenZuruecksetzen();
});

// Wer von Hand ans Ende scrollt, hat aufgeholt - dann darf der Knopf weg.
window.addEventListener("scroll", () => {
  if (NEUE_SEIT_ABRISS && amEndeDran()) zurueckgebliebenZuruecksetzen();
}, { passive: true });

// ---------------------------------------------------------- Live-Stream --
function connectStream(threadId) {
  if (SOURCE) SOURCE.close();
  SOURCE = new EventSource(
    `/api/threads/${threadId}/stream?token=${encodeURIComponent(TOKEN)}`,
  );
  // UEBERSICHT bleibt bewusst offen: sie traegt die Liste, nicht das Thema.

  SOURCE.addEventListener("post.created", (event) => {
    const data = JSON.parse(event.data);
    if (NODES.has(data.post_id)) return;
    // Das Event heisst post_id, postNode() indiziert nach id - ohne die
    // Umbenennung landet der Knoten unter "undefined" und alle folgenden
    // Deltas finden ihn nicht mehr.
    $("#posts").appendChild(
      postNode({ ...data, id: data.post_id, content: "", status: "streaming" }),
    );
    mitlaufen({ neuerBeitrag: true });
  });

  SOURCE.addEventListener("post.delta", (event) => {
    const data = JSON.parse(event.data);
    const node = NODES.get(data.post_id);
    if (!node || !data.text) return;
    const mitziehen = amEndeDran() && !amTippen();
    node._roh = (node._roh || "") + data.text;
    koerperFuellen(node.querySelector(".body"), node._roh);
    if (mitziehen) window.scrollTo(0, document.body.scrollHeight);
  });

  SOURCE.addEventListener("post.done", (event) => {
    const data = JSON.parse(event.data);
    const node = NODES.get(data.post_id);
    if (!node) return;
    node._roh = data.content || "";
    koerperFuellen(node.querySelector(".body"), node._roh);
    node.className = `post ${node.classList.contains("human") ? "human" : ""} ${data.status}`;
    // Das Thema steht offen vor mir - also habe ich es gesehen.
    if (CURRENT) gelesenMelden(CURRENT.id);
  });

  SOURCE.addEventListener("post.removed", (event) => {
    const data = JSON.parse(event.data);
    const node = NODES.get(data.post_id);
    if (node) {
      node.remove();
      NODES.delete(data.post_id);
    }
  });

  SOURCE.addEventListener("thread.update", async (event) => {
    const data = JSON.parse(event.data);
    const status = $("#th-status");
    if (status) {
      status.textContent = data.status;
      status.className = `badge ${data.status}`;
      $("#th-rounds").textContent = `${data.rounds_done}/${data.max_rounds}`;
      $("#th-detail").textContent = data.status_detail ? ` — ${data.status_detail}` : "";
    }
    await loadThreads();
  });
}

// ------------------------------------------------------ Neues Thema ------
$("#new-thread-btn").addEventListener("click", async () => {
  // Nur eigene: ein fremder Agent laeuft auf dem Modell-Zugang seines
  // Besitzers. Der Server weist das ab - also gar nicht erst anbieten.
  const agents = (await api("/api/agents")).filter((a) => a.owner_id === ME.id);
  if (agents.length < 2) {
    alert(t("neuesThema.zuWenigAgenten"));
    return;
  }
  const options = agents
    .map((agent) => `<option value="${agent.id}">${escapeHtml(agent.name)} — ${escapeHtml(agent.model)}</option>`)
    .join("");
  $("#t-agents").innerHTML = options;
  $("#t-moderator").innerHTML = options;
  $("#t-forum").innerHTML = forumOptionen(
    FORUM_FILTER && FORUM_FILTER !== "-" ? FORUM_FILTER : null,
    true,
    t("forum.ohne"),
  );
  $("#thread-error").textContent = "";
  $("#thread-dialog").showModal();
});

$("#t-cancel").addEventListener("click", () => $("#thread-dialog").close());

$("#thread-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const agentIds = Array.from($("#t-agents").selectedOptions).map((option) => option.value);
  if (agentIds.length < 1) {
    $("#thread-error").textContent = t("neuesThema.keinTeilnehmer");
    return;
  }
  const moderator = $("#t-moderator").value;
  const dateien = Array.from($("#t-dateien").files || []);
  const payload = {
    title: $("#t-title").value.trim(),
    goal: $("#t-goal").value.trim(),
    forum_id: $("#t-forum").value || null,
    agent_ids: agentIds.includes(moderator) ? agentIds : [...agentIds, moderator],
    mode: $("#t-mode").value,
    moderator_agent_id: moderator,
    max_rounds: Number($("#t-rounds").value),
    pace_seconds: Number($("#t-pace").value),
    stop_phrase: $("#t-stop").value.trim(),
    art: $("#t-art").value,
    werkzeuge: $("#t-werkzeuge").checked,
    synthesize_on_end: $("#t-synth").checked,
    // Liegt Material bei, muss es im Verlauf stehen, BEVOR der erste Agent
    // spricht - sonst diskutiert die erste Runde ins Leere. Also angehalten
    // anlegen, hochladen, dann loslaufen lassen.
    start_now: dateien.length === 0,
  };

  const hinweis = $("#t-dateien-hinweis");
  $("#thread-error").textContent = "";

  const aufraeumen = () => {
    hinweis.textContent = "";
    $("#thread-dialog").close();
    $("#t-title").value = "";
    $("#t-goal").value = "";
    $("#t-dateien").value = "";
  };

  let thread;
  try {
    thread = await api("/api/threads", { method: "POST", body: JSON.stringify(payload) });
  } catch (error) {
    $("#thread-error").textContent = error.message;
    return;
  }

  // Ab hier gibt es das Thema schon. Geht ein Dokument schief, bleibt es
  // angehalten stehen, statt verloren zu sein - man sieht es und kann
  // nachlegen.
  try {
    for (const [nummer, datei] of dateien.entries()) {
      hinweis.textContent = t("neuesThema.grundlageLaeuft", {
        name: datei.name,
        nummer: nummer + 1,
        gesamt: dateien.length,
      });
      await dokumentHochladen(thread.id, datei);
    }
    if (dateien.length > 0) {
      await api(`/api/threads/${thread.id}/control`, {
        method: "POST",
        body: JSON.stringify({ action: "resume" }),
      });
    }
  } catch (error) {
    // Dialog trotzdem schliessen: bliebe er offen, legte ein zweiter Klick
    // auf "Starten" ein zweites Thema an. Das Thema ist angehalten, das
    // Dokument laesst sich dort nachlegen.
    aufraeumen();
    await loadThreads();
    await openThread(thread.id);
    alert(t("neuesThema.grundlageFehler", { fehler: error.message }));
    return;
  }

  aufraeumen();
  await loadThreads();
  await openThread(thread.id);
});

// ------------------------------------------------------------- Agenten ---
async function loadAgents() {
  const [agents, zugaenge] = await Promise.all([
    api("/api/agents"),
    api("/api/credentials"),
  ]);
  const list = $("#agent-list");
  list.innerHTML = "";
  for (const agent of agents) {
    const zugang = zugaenge.find((z) => z.id === agent.credential_id);
    const eigener = agent.owner_id === ME.id;
    // Ohne Zugang schickt LiteLLM einen Platzhalter statt eines Schluessels,
    // und der Anbieter antwortet mit 401. Das muss man sehen koennen.
    const zugangText = zugang
      ? escapeHtml(zugang.label)
      : agent.credential_id
        ? t("agent.fremderZugang")
        : `<span style="color:var(--err)">${t("agent.keinZugangWarnung")}</span>`;

    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML = `
      <div style="flex:1">
        <h3>${escapeHtml(agent.name)}</h3>
        <p class="muted" style="margin:.1rem 0">${escapeHtml(agent.model)} &middot; T=${agent.temperature} &middot; ${agent.max_tokens} Tokens</p>
        <p class="muted" style="margin:.1rem 0">${escapeHtml(agent.role || "")}</p>
        <p class="persona">${escapeHtml(agent.persona || t("thread.ohneAngabe"))}</p>
        <p class="muted" style="margin:.3rem 0 0">${t("agent.zugangZeile")}: ${zugangText}</p>
      </div>`;

    if (eigener) {
      const wechsel = document.createElement("select");
      wechsel.style.maxWidth = "14rem";
      wechsel.innerHTML =
        `<option value="">${t("agent.ohneZugang")}</option>` +
        zugaenge
          .map(
            (z) =>
              `<option value="${z.id}"${z.id === agent.credential_id ? " selected" : ""}>${escapeHtml(z.label)}</option>`,
          )
          .join("");
      wechsel.addEventListener("change", async () => {
        try {
          await api(`/api/agents/${agent.id}`, {
            method: "PATCH",
            body: JSON.stringify({ credential_id: wechsel.value || null }),
          });
          await loadAgents();
        } catch (error) {
          alert(error.message);
        }
      });
      card.appendChild(wechsel);
    }

    if (agent.owner_id === ME.id) {
      const remove = document.createElement("button");
      remove.className = "danger";
      remove.textContent = t("agent.loeschen");
      remove.addEventListener("click", async () => {
        await api(`/api/agents/${agent.id}`, { method: "DELETE" });
        await loadAgents();
      });
      card.appendChild(remove);
    }
    list.appendChild(card);
  }
}


// Modell-Liste zum gewaehlten Endpunkt. Bewusst auf Knopfdruck und nicht
// automatisch: der Aufruf geht nach draussen und kostet Zeit, und wer den
// Namen kennt, tippt ihn schneller. Das Textfeld bleibt massgeblich - die
// Liste fuellt es nur.
async function modelleHolen() {
  const zugang = $("#agent-credential").value;
  const stand = $("#agent-modelle-stand");
  const liste = $("#agent-modelliste");
  if (!zugang) {
    stand.textContent = t("agent.modelleKeinZugang");
    return;
  }
  stand.textContent = t("agent.modelleLaeuft");
  liste.innerHTML = '<option value=""></option>';
  try {
    const antwort = await api(`/api/credentials/${zugang}/modelle`);
    for (const name of antwort.modelle) {
      const option = document.createElement("option");
      option.value = name;
      option.textContent = name;
      liste.appendChild(option);
    }
    stand.textContent = t("agent.modelleGefunden", { anzahl: antwort.modelle.length });
  } catch (error) {
    stand.textContent = error.message;
  }
}

$("#agent-modelle-holen").addEventListener("click", modelleHolen);
$("#agent-modelliste").addEventListener("change", (event) => {
  if (event.target.value) $("#agent-model").value = event.target.value;
});
// Anderer Endpunkt, andere Modelle - die alte Liste waere irrefuehrend.
$("#agent-credential").addEventListener("change", () => {
  $("#agent-modelliste").innerHTML = '<option value=""></option>';
  $("#agent-modelle-stand").textContent = "";
});

$("#agent-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("#agent-error").textContent = "";
  try {
    await api("/api/agents", {
      method: "POST",
      body: JSON.stringify({
        name: $("#agent-name").value.trim(),
        role: $("#agent-role").value.trim(),
        model: $("#agent-model").value.trim(),
        persona: $("#agent-persona").value.trim(),
        credential_id: $("#agent-credential").value || null,
        temperature: Number($("#agent-temp").value),
        max_tokens: Number($("#agent-maxtok").value),
        is_public: $("#agent-public").checked,
      }),
    });
    $("#agent-form").reset();
    $("#agent-public").checked = true;
    await loadAgents();
  } catch (error) {
    $("#agent-error").textContent = error.message;
  }
});

// ------------------------------------------------------------- Modelle ---
function authText(stil) {
  return {
    api_key: t("zugang.alsApiKey"),
    bearer: t("zugang.alsBearer"),
    header: t("zugang.alsKopffeld"),
    none: t("zugang.garNicht"),
  }[stil] || stil;
}

async function loadCredentials() {
  const credentials = await api("/api/credentials");
  const list = $("#cred-list");
  list.innerHTML = "";
  const select = $("#agent-credential");
  select.innerHTML = `<option value="">${t("agent.ohneZugangLang")}</option>`;

  for (const credential of credentials) {
    const option = document.createElement("option");
    option.value = credential.id;
    option.textContent = `${credential.label} (${credential.provider})`;
    select.appendChild(option);

    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML = `
      <div>
        <h3>${escapeHtml(credential.label)}</h3>
        <p class="muted" style="margin:.1rem 0">
          Provider <code>${escapeHtml(credential.provider)}</code>
          ${credential.api_base ? `&middot; ${escapeHtml(credential.api_base)}` : ""}
          &middot; ${credential.has_key ? t("zugang.hinterlegt") : t("zugang.ohneSchluessel")}
          &middot; ${authText(credential.auth_style)}${
            credential.auth_style === "header" ? ` (${escapeHtml(credential.header_name || "x-api-key")})` : ""
          }
          ${credential.has_extra ? `&middot; ${t("zugang.mitZusatz")}` : ""}
        </p>
      </div>`;
    const remove = document.createElement("button");
    remove.className = "danger";
    remove.textContent = t("zugang.loeschen");
    remove.addEventListener("click", async () => {
      await api(`/api/credentials/${credential.id}`, { method: "DELETE" });
      await loadCredentials();
    });
    card.appendChild(remove);
    list.appendChild(card);
  }
}

// Das Kopffeld ist nur bei der entsprechenden Auswahl sinnvoll.
$("#cred-auth").addEventListener("change", (event) => {
  const eigenesFeld = event.target.value === "header";
  $("#cred-header").disabled = !eigenesFeld;
  if (eigenesFeld && !$("#cred-header").value) $("#cred-header").value = "x-api-key";
});

$("#cred-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("#cred-error").textContent = "";
  try {
    await api("/api/credentials", {
      method: "POST",
      body: JSON.stringify({
        label: $("#cred-label").value.trim(),
        provider: $("#cred-provider").value.trim(),
        api_key: $("#cred-key").value || null,
        api_base: $("#cred-base").value.trim() || null,
        auth_style: $("#cred-auth").value,
        header_name: $("#cred-header").value.trim() || null,
        extra_json: $("#cred-extra").value.trim() || null,
      }),
    });
    $("#cred-form").reset();
    await loadCredentials();
  } catch (error) {
    $("#cred-error").textContent = error.message;
  }
});

// ------------------------------------------------------------ Personen ---
async function loadUsers() {
  const users = await api("/api/users");
  const list = $("#user-list");
  list.innerHTML = "";
  for (const user of users) {
    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML = `
      <div>
        <h3>${escapeHtml(user.name)}</h3>
        <p class="muted" style="margin:.1rem 0">
          ${user.is_admin ? t("person.verwaltet") : t("person.teilnehmer")}
          ${user.id === ME.id ? `&middot; ${t("person.dasBistDu")}` : ""}
        </p>
      </div>`;

    if (user.id !== ME.id) {
      const zuruecksetzen = async (mitPin) => {
        const frage = mitPin
          ? t("person.fragePin", { name: user.name })
          : t("person.frageToken", { name: user.name });
        if (!confirm(frage)) return;
        try {
          const angelegt = await api(`/api/users/${user.id}/reset`, {
            method: "POST",
            body: JSON.stringify({ pin_zuruecksetzen: mitPin }),
          });
          zeigeToken(
            t("token.neuTitel", { name: angelegt.name }),
            mitPin ? t("token.neuTextPin") : t("token.neuTextOhnePin"),
            angelegt.token,
          );
        } catch (error) {
          alert(error.message);
        }
      };

      const knoepfe = document.createElement("div");
      knoepfe.style.display = "flex";
      knoepfe.style.gap = ".4rem";
      knoepfe.style.flexWrap = "wrap";

      const neu = document.createElement("button");
      neu.textContent = t("person.neuesToken");
      neu.title = t("person.neuesTokenTitel");
      neu.addEventListener("click", () => zuruecksetzen(false));

      const notausgang = document.createElement("button");
      notausgang.className = "danger";
      notausgang.textContent = t("person.pinZuruecksetzen");
      notausgang.title = t("person.pinZuruecksetzenTitel");
      notausgang.addEventListener("click", () => zuruecksetzen(true));

      // Verwaltungsrechte geben oder nehmen. Genau so wird das
      // Installationskonto zum blossen Notschluessel, sobald es ein
      // eigenes Admin-Konto gibt.
      const rechte = document.createElement("button");
      rechte.textContent = user.is_admin ? t("person.rechteNehmen") : t("person.rechteGeben");
      rechte.title = t("person.rechteTitel");
      rechte.addEventListener("click", async () => {
        try {
          await api(`/api/users/${user.id}`, {
            method: "PATCH",
            body: JSON.stringify({ is_admin: !user.is_admin }),
          });
          await loadUsers();
        } catch (error) {
          alert(error.message);
        }
      });
      knoepfe.append(rechte);

      // Das Installationskonto laesst der Server nicht loeschen - also
      // hier auch keinen Knopf dafuer anbieten.
      if (user.name !== "admin") {
        const weg = document.createElement("button");
        weg.className = "danger";
        weg.textContent = t("person.loeschen");
        weg.title = t("person.loeschenTitel");
        weg.addEventListener("click", async () => {
          if (!confirm(t("person.loeschenFrage", { name: user.name }))) return;
          try {
            const ergebnis = await api(`/api/users/${user.id}`, { method: "DELETE" });
            alert(t("person.geloescht", {
              name: ergebnis.geloescht,
              themen: ergebnis.themen_uebernommen,
            }));
            await loadUsers();
          } catch (error) {
            alert(error.message);
          }
        });
        knoepfe.append(neu, notausgang, weg);
      } else {
        knoepfe.append(neu, notausgang);
      }
      card.appendChild(knoepfe);
    }

    list.appendChild(card);
  }
}

async function loadRequests() {
  const antraege = (await api("/api/requests")).filter((a) => a.status === "offen");
  const liste = $("#request-list");
  liste.innerHTML = "";
  if (!antraege.length) {
    liste.innerHTML = `<p class="muted">${t("person.keineAntraege")}</p>`;
    return;
  }
  for (const antrag of antraege) {
    const karte = document.createElement("div");
    karte.className = "card";
    karte.innerHTML = `
      <div style="flex:1">
        <h3>${escapeHtml(antrag.name)}</h3>
        <p class="muted" style="margin:.1rem 0">${escapeHtml(antrag.note || t("person.ohneBegruendung"))}</p>
        <p class="muted" style="margin:.1rem 0">${t("person.erreichbar")}: ${
          antrag.contact
            ? escapeHtml(antrag.contact)
            : `<span style="color:var(--warn)">${t("person.keinKontakt")}</span>`
        }</p>
        <p class="muted" style="margin:.1rem 0">${new Date(antrag.created_at).toLocaleString("de-DE")}</p>
      </div>`;

    const ja = document.createElement("button");
    ja.className = "primary";
    ja.textContent = t("person.freigeben");
    ja.addEventListener("click", async () => {
      try {
        const angelegt = await api(`/api/requests/${antrag.id}/approve`, { method: "POST" });
        zeigeToken(
          t("token.einmalTitel", { name: angelegt.name }),
          (antrag.contact
            ? t("token.schickeAn", { kontakt: antrag.contact })
            : t("token.gibWeiter")) + t("token.einmalText"),
          angelegt.token,
        );
        await Promise.all([loadRequests(), loadUsers()]);
      } catch (error) {
        alert(error.message);
      }
    });

    const nein = document.createElement("button");
    nein.className = "danger";
    nein.textContent = t("person.ablehnen");
    nein.addEventListener("click", async () => {
      try {
        await api(`/api/requests/${antrag.id}/reject`, { method: "POST" });
        await loadRequests();
      } catch (error) {
        alert(error.message);
      }
    });

    const knoepfe = document.createElement("div");
    knoepfe.style.display = "flex";
    knoepfe.style.gap = ".4rem";
    knoepfe.append(ja, nein);
    karte.appendChild(knoepfe);
    liste.appendChild(karte);
  }
}

$("#user-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("#user-error").textContent = "";
  try {
    const user = await api("/api/users", {
      method: "POST",
      body: JSON.stringify({
        name: $("#user-name").value.trim(),
        is_admin: $("#user-admin").checked,
      }),
    });
    zeigeToken(
      t("token.einmalTitel", { name: user.name }),
      t("token.gibWeiter") + t("token.einmalText"),
      user.token,
    );
    $("#user-form").reset();
    await loadUsers();
  } catch (error) {
    $("#user-error").textContent = error.message;
  }
});


// ----------------------------------------------------------- Zielsprache ---
// Bewusst NICHT an die Oberflaechensprache gekoppelt: sonst liesse sich nur
// in die eine oder andere Richtung uebersetzen, und ins Russische gar nicht,
// solange es die Oberflaeche nicht auf Russisch gibt.
function zielsprache() {
  const feld = $("#zielsprache");
  return (feld && feld.value) || SPRACHE;
}

function zielsprachenAnbieten() {
  const feld = $("#zielsprache");
  if (!feld) return;
  let gemerkt = null;
  try {
    gemerkt = localStorage.getItem("agora-zielsprache");
  } catch {
    // Privates Fenster oder gesperrter Speicher - dann eben ohne Gedaechtnis.
  }
  feld.innerHTML = "";
  for (const [kuerzel, name] of Object.entries(SPRACHNAMEN)) {
    const option = document.createElement("option");
    option.value = kuerzel;
    option.textContent = name;
    feld.appendChild(option);
  }
  feld.value = gemerkt && SPRACHNAMEN[gemerkt] ? gemerkt : SPRACHE;
  feld.addEventListener("change", () => {
    try {
      localStorage.setItem("agora-zielsprache", feld.value);
    } catch {
      // siehe oben
    }
  });
}

zielsprachenAnbieten();

// ------------------------------------------------------------ Sicherung ---
// Das Abbild geht durch den Browser: wohin gesichert wird, entscheidet die
// Person am Bildschirm. Der Server kann nicht auf ihre Platte schreiben.
async function sicherungLaden() {
  const stand = $("#sich-stand");
  const knopf = $("#sich-laden");
  knopf.disabled = true;
  stand.textContent = t("sicherung.laeuft");
  try {
    const antwort = await fetch("/api/admin/sicherung/erstellen", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${TOKEN}`,
        "X-Agora-Token": TOKEN,
        "X-Agora-Sprache": SPRACHE,
        "Content-Type": "application/json",
      },
      // Per POST, nicht als Adresse: ein Passwort in der URL stuende im
      // Zugriffsprotokoll jedes Proxys davor.
      body: JSON.stringify({ passwort: $("#sich-pw").value }),
    });
    if (!antwort.ok) {
      const text = await antwort.text();
      throw new Error(JSON.parse(text || "{}")?.detail || `HTTP ${antwort.status}`);
    }
    // Der Dateiname kommt vom Server, damit Datum und Uhrzeit darin stehen.
    const kopf = antwort.headers.get("content-disposition") || "";
    const name = /filename="([^"]+)"/.exec(kopf)?.[1] || "agora-sicherung.json.gz";
    const blob = await antwort.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    a.click();
    URL.revokeObjectURL(url);
    stand.textContent = t(
      $("#sich-pw").value ? "sicherung.fertigGeschuetzt" : "sicherung.fertig",
      { name, groesse: groesse(blob.size) },
    );
  } catch (error) {
    stand.textContent = "";
    $("#sich-fehler").textContent = error.message;
  } finally {
    knopf.disabled = false;
  }
}

function sicherungBefund(kopf) {
  const zahlen = kopf.zaehler || {};
  const zeilen = [
    t("sicherung.befundStand", {
      datum: zeitpunkt(kopf.erstellt_am),
      version: kopf.app_version,
    }),
    t("sicherung.befundInhalt", {
      personen: zahlen.users ?? 0,
      foren: zahlen.forums ?? 0,
      themen: zahlen.threads ?? 0,
      beitraege: zahlen.posts ?? 0,
      zugaenge: zahlen.credentials ?? 0,
    }),
  ];
  if (!kopf.gleicher_server) zeilen.push(t("sicherung.andererServer"));
  if (kopf.am_serverschluessel > 0) {
    zeilen.push(t("sicherung.alteZugaenge", { anzahl: kopf.am_serverschluessel }));
  }
  const kasten = $("#sich-befund");
  kasten.textContent = "";
  for (const zeile of zeilen) {
    const p = document.createElement("p");
    p.style.margin = ".2rem 0";
    p.textContent = zeile;
    kasten.appendChild(p);
  }
  kasten.hidden = false;
}

async function sicherungPruefen() {
  const datei = $("#sich-datei").files[0];
  $("#sich-fehler").textContent = "";
  $("#sich-befund").hidden = true;
  $("#sich-zurueck").hidden = true;
  if (!datei) return;
  const inhalt = new FormData();
  inhalt.append("datei", datei);
  inhalt.append("passwort", $("#sich-pw2").value);
  try {
    const antwort = await fetch("/api/admin/sicherung/pruefen", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${TOKEN}`,
        "X-Agora-Token": TOKEN,
        "X-Agora-Sprache": SPRACHE,
      },
      body: inhalt,
    });
    const text = await antwort.text();
    const daten = text ? JSON.parse(text) : null;
    if (!antwort.ok) throw new Error(daten?.detail || `HTTP ${antwort.status}`);
    if (daten.passwort_noetig) {
      // Kein Fehler, eine Rueckfrage: Feld einblenden und warten.
      $("#sich-pw2-zeile").hidden = false;
      $("#sich-pw2").focus();
      $("#sich-fehler").textContent = t("sicherung.passwortNoetig");
      return;
    }
    $("#sich-pw2-zeile").hidden = !daten.verschluesselt;
    sicherungBefund(daten);
    $("#sich-zurueck").hidden = false;
  } catch (error) {
    $("#sich-fehler").textContent = error.message;
  }
}

async function sicherungZurueck() {
  const datei = $("#sich-datei").files[0];
  if (!datei) return;
  // Zweimal fragen ist hier richtig: danach ist der bisherige Bestand fort.
  if (!confirm(t("sicherung.sicherFrage"))) return;
  const knopf = $("#sich-zurueck");
  knopf.disabled = true;
  $("#sich-fehler").textContent = "";
  const inhalt = new FormData();
  inhalt.append("datei", datei);
  inhalt.append("passwort", $("#sich-pw2").value);
  inhalt.append("bestaetigt", "true");
  try {
    const antwort = await fetch("/api/admin/sicherung/einspielen", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${TOKEN}`,
        "X-Agora-Token": TOKEN,
        "X-Agora-Sprache": SPRACHE,
      },
      body: inhalt,
    });
    const text = await antwort.text();
    const daten = text ? JSON.parse(text) : null;
    if (!antwort.ok) throw new Error(daten?.detail || `HTTP ${antwort.status}`);
    alert(t("sicherung.eingespielt"));
    // Alles im Bild ist jetzt veraltet, und die Freischaltung ist fort.
    location.reload();
  } catch (error) {
    $("#sich-fehler").textContent = error.message;
    knopf.disabled = false;
  }
}

$("#sich-laden").addEventListener("click", sicherungLaden);
$("#sich-datei").addEventListener("change", sicherungPruefen);
$("#sich-pw2").addEventListener("change", sicherungPruefen);
$("#sich-zurueck").addEventListener("click", sicherungZurueck);

// --------------------------------------------------------------- Start ---
(async () => {
  if (!TOKEN) return;
  try {
    ME = await api("/api/me");
    await boot();
  } catch {
    localStorage.removeItem("agora_token");
  }
})();

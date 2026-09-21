"use strict";

// Woerterliste fuer die Oberflaeche.
//
// Der Quelltext bleibt deutsch - uebersetzt wird nur, was Nutzer lesen. Eine
// weitere Sprache ist ein weiterer Block hier und ein Eintrag in SPRACHEN,
// sonst nichts.
//
// Im HTML markiert man Text mit data-i18n="schluessel":
//   data-i18n        ersetzt den Textinhalt
//   data-i18n-html   ersetzt den HTML-Inhalt (fuer <strong> und dergleichen)
//   data-i18n-ph     setzt das placeholder-Attribut
//   data-i18n-title  setzt das title-Attribut

const TEXTE = {
  de: {
    "app.untertitel": "Forum, in dem Agenten Themen miteinander bearbeiten.",
    "app.sprache": "English",

    "gate.token": "Zugangs-Token",
    "gate.pin": "PIN",
    "gate.pinHinweis":
      "Bei der <strong>ersten</strong> Anmeldung vergibst du die PIN selbst " +
      "(mindestens 6 Zeichen). Danach brauchst du sie jedes Mal &ndash; sie ist " +
      "nirgends nachschlagbar, auch nicht f&uuml;r Admins.",
    "gate.anmelden": "Anmelden",
    "gate.laeuft": "Anmeldung läuft ...",
    "gate.antragOeffnen": "Noch keinen Zugang? Hier beantragen",

    "antrag.titel": "Zugang beantragen",
    "antrag.hinweis":
      "Ein Admin gibt frei. Du bekommst dann ein Einmal-Token, das bei deiner " +
      "ersten Anmeldung gegen dein eigenes getauscht wird &ndash; danach kennt " +
      "es niemand sonst.",
    "antrag.name": "Name",
    "antrag.kontakt": "Wie erreichen wir dich?",
    "antrag.kontaktPh": "Mail-Adresse oder Kürzel",
    "antrag.zweck": "Wofür brauchst du den Zugang?",
    "antrag.zweckPh": "kurz, damit der Admin einordnen kann",
    "antrag.senden": "Antrag senden",
    "antrag.zurueck": "zurück zur Anmeldung",
    "antrag.eingegangen":
      "Antrag eingegangen. Sobald jemand freigibt, bekommst du ein Einmal-Token.",

    "nav.forum": "Forum",
    "nav.agenten": "Agenten",
    "nav.modelle": "Modelle",
    "nav.personen": "Personen",
    "nav.pinAendern": "PIN ändern",
    "nav.abmelden": "Abmelden",

    "schloss.frei": "Zugänge frei bis {zeit}",
    "schloss.gesperrt": "Zugänge gesperrt",
    "schloss.sperrenTitel": "Anklicken, um sofort zu sperren",
    "schloss.freischaltenTitel": "Anklicken, um mit der PIN freizuschalten",

    "forum.neuesThema": "+ Neues Thema",
    "forum.foren": "Foren",
    "forum.neuesForum": "+ Forum",
    "forum.alleThemen": "Alle Themen",
    "forum.ohneForum": "Ohne Forum",
    "forum.keinThema": "Kein Thema ausgewählt.",
    "forum.loeschenTitel": "Forum löschen (muss leer sein)",
    "forum.ganzOben": "– ganz oben –",
    "forum.ohne": "– ohne Forum –",

    "thread.keinZiel": "kein Ziel gesetzt",
    "thread.runden": "Runden",
    "thread.takt": "Takt",
    "thread.endeBeiEinigkeit": "Ende bei Einigkeit aller auf",
    "thread.bisBudget": "läuft bis zum Rundenbudget",
    "thread.pause": "Pause",
    "thread.weiter": "Weiter",
    "thread.mehrRunden": "+5 Runden",
    "thread.beenden": "Beenden",
    "thread.loeschen": "Löschen",
    "thread.loeschenFrage": "Thema mit allen Beiträgen endgültig löschen?",
    "thread.dazuholen": "+ Teilnehmer dazuholen",
    "thread.rausTitel": "aus der Diskussion nehmen",
    "thread.mitschreiben": "Als Mensch mitschreiben",
    "thread.mitschreibenPh": "Zwischenruf, Korrektur, neue Anforderung ...",
    "thread.senden": "Beitrag senden",
    "thread.weiterlaufen": "Diskussion danach weiterlaufen lassen",
    "thread.datei": "Schriftstück beisteuern",
    "thread.dateiHinweis":
      "PDF, Word, PowerPoint, OpenDocument, HTML, Text – ausgelesen wird der " +
      "Text, die Datei selbst wird nicht gespeichert.",
    "thread.dateiLaeuft": '"{name}" wird ausgelesen ...',
    "thread.dokumentWeg": "Dokument aus der Diskussion entfernen",
    "thread.runde": "Runde",
    "thread.mensch": "Mensch",
    "thread.system": "Forum",
    "thread.dokument": "Dokument",
    "thread.rechner": "Rechner",
    "thread.uebersetzen": "übersetzen",
    "thread.uebersetztLaeuft": "wird übersetzt ...",
    "thread.original": "Original anzeigen",

    "neuesThema.titel": "Neues Thema",
    "neuesThema.name": "Titel",
    "neuesThema.namePh": "Architektur für eine global verteilte Datenbank",
    "neuesThema.ziel": "Ziel der Diskussion",
    "neuesThema.zielPh":
      "Zwei Ansätze gegenüberstellen und eine Empfehlung mit Begründung liefern.",
    "neuesThema.forum": "Forum",
    "neuesThema.teilnehmer": "Teilnehmer (Mehrfachauswahl)",
    "neuesThema.ablauf": "Ablauf",
    "neuesThema.moderatorWaehlt": "Moderator wählt",
    "neuesThema.reihum": "Reihum",
    "neuesThema.moderator": "Moderator",
    "neuesThema.runden": "Runden",
    "neuesThema.takt": "Takt (Sek.)",
    "neuesThema.zustimmung": "Zustimmungswort (leer = volle Rundenzahl)",
    "neuesThema.synthese": "Abschluss-Synthese",
    "neuesThema.grundlage": "Grundlage (optional, mehrere möglich)",
    "neuesThema.grundlageHinweis":
      "Wird vor der ersten Runde eingelesen und steht den Agenten von Anfang " +
      "an zur Verfügung. PDF, Word, LaTeX, Text, Quelltext.",
    "neuesThema.grundlageLaeuft": "Lese {name} ein ({nummer}/{gesamt}) …",
    "neuesThema.grundlageFehler":
      "Das Thema steht, aber ein Dokument kam nicht durch: {fehler} – " +
      "es ist angehalten, du kannst nachlegen und dann starten.",
    "neuesThema.art": "Art der Diskussion",
    "neuesThema.artFrei": "Freie Diskussion \u2013 beliebiges Thema",
    "neuesThema.artPruefen": "Beweis, Herleitung oder Rechnung pr\u00fcfen",
    "neuesThema.artText": "Text pr\u00fcfen \u2013 Artikel, Bericht, Blogpost",
    "neuesThema.artBegutachten": "Wissenschaftliches Paper begutachten",
    "neuesThema.artAntrag": "Antrag begutachten",
    "neuesThema.artCode": "Code besprechen",
    "neuesThema.artHinweis":
      "Bestimmt, welche Fragen die Agenten stellen. Freie Diskussion braucht " +
      "kein Dokument \u2013 alles andere gibt den Agenten einen Pr\u00fcfauftrag " +
      "und verbietet ihnen, zuzustimmen, bevor gepr\u00fcft wurde.",
    "neuesThema.werkzeuge": "Agenten dürfen rechnen",
    "neuesThema.werkzeugeHinweis":
      "Die Agenten können Python-Blöcke schreiben, die ausgeführt werden – für " +
      "Zahlen, Umformungen und Gegenbeispiele. Die Ausgabe erscheint als eigener " +
      "Beitrag im Verlauf.",
    "neuesThema.starten": "Starten",
    "neuesThema.zuWenigAgenten":
      "Lege zuerst mindestens zwei Agenten an (Reiter 'Agenten').",
    "neuesThema.keinTeilnehmer": "Bitte mindestens einen Teilnehmer wählen.",

    "agent.anlegen": "Agent anlegen",
    "agent.name": "Name",
    "agent.namePh": "Kritikerin",
    "agent.rolle": "Rolle (für die Sprecherwahl)",
    "agent.rollePh": "prüft Annahmen und Methodik",
    "agent.modell": "Modell (LiteLLM-String)",
    "agent.zugang": "Zugang",
    "agent.ohneZugang": "– kein Zugang –",
    "agent.ohneZugangLang": "– ohne (Schlüssel aus der Umgebung) –",
    "agent.persona": "Persona / System-Prompt",
    "agent.personaPh": "Du bist ...",
    "agent.temperatur": "Temperatur",
    "agent.maxTokens": "Max. Tokens",
    "agent.oeffentlich": "für alle sichtbar",
    "agent.speichern": "Agent speichern",
    "agent.zugangZeile": "Zugang",
    "agent.fremderZugang": "fremder Zugang",
    "agent.keinZugangWarnung": "kein Zugang – Modellaufrufe schlagen fehl",
    "agent.loeschen": "Löschen",

    "teilnahme.titel": "Teilnahme meiner Agenten",
    "teilnahme.hinweis":
      "So lange dürfen <strong>deine</strong> Agenten in einem Thema noch " +
      "mitreden, gerechnet ab deinem letzten eigenen Beitrag dort. Danach " +
      "steigen sie aus, bis du wieder etwas schreibst &ndash; so kann niemand " +
      "ein Thema unbemerkt auf deine Kosten weiterlaufen lassen.",
    "teilnahme.unbegrenzt": "Unbegrenzt – deine Agenten reden weiter, bis das Thema endet.",
    "teilnahme.eineStunde": "1 Stunde nach deinem letzten Beitrag im Thema.",
    "teilnahme.stunden": "{n} Stunden nach deinem letzten Beitrag im Thema.",

    "zugang.titel": "Modell-Zugang hinterlegen",
    "zugang.hinweis":
      "Der Schlüssel wird verschlüsselt gespeichert und niemals wieder " +
      "ausgeliefert. Für ein eigenes vLLM/Ollama im Cluster genügt Provider " +
      "<code>openai</code> plus die Basis-URL &ndash; ein Schlüssel ist dann " +
      "oft nicht nötig.",
    "zugang.bezeichnung": "Bezeichnung",
    "zugang.bezeichnungPh": "Mein Anthropic-Schlüssel",
    "zugang.provider": "Provider",
    "zugang.schluessel": "Schlüssel / Token (optional)",
    "zugang.basisUrl": "Basis-URL (optional)",
    "zugang.wieGeschickt": "Wie wird er mitgeschickt?",
    "zugang.alsApiKey": "als API-Key (Normalfall)",
    "zugang.alsBearer": "als Bearer-Token",
    "zugang.alsKopffeld": "in einem eigenen Kopffeld",
    "zugang.garNicht": "gar nicht (offener Endpunkt)",
    "zugang.kopffeldName": "Name des Kopffelds",
    "zugang.zusatz": "Zusatzparameter als JSON (optional)",
    "zugang.speichern": "Zugang speichern",
    "zugang.hinterlegt": "Schlüssel hinterlegt",
    "zugang.ohneSchluessel": "ohne Schlüssel",
    "zugang.mitZusatz": "mit Zusatzparametern",
    "zugang.loeschen": "Löschen",

    "sicherung.titel": "Sicherung",
    "sicherung.passwort": "Passwort für das Abbild (optional)",
    "sicherung.passwortHinweis":
      "Leer lassen heißt: ungeschützt. Mit Passwort ist die Datei als Ganzes " +
      "verschlüsselt – nimm mindestens 10 Zeichen, besser einen Satz. Eine " +
      "Datei kann jemand in Ruhe zu Hause durchprobieren, anders als eine " +
      "PIN am Server. Verlierst du es, ist das Abbild verloren.",
    "sicherung.fertigGeschuetzt": "Gespeichert und verschlüsselt: {name} ({groesse})",
    "sicherung.passwortDatei": "Passwort des Abbilds",
    "sicherung.passwortNoetig":
      "Auf diesem Abbild liegt ein Passwort. Bitte eintragen.",
    "sicherung.hinweis":
      "Ein Abbild des ganzen Forums als eine Datei – Personen, Foren, Themen, " +
      "Beiträge und die verschlüsselten Modell-Zugänge. Dein Browser fragt, " +
      "wohin. Bewahre es so sorgfältig auf wie die Datenbank selbst.",
    "sicherung.herunterladen": "Sicherung herunterladen",
    "sicherung.laeuft": "Wird erstellt …",
    "sicherung.fertig": "Gespeichert: {name} ({groesse})",
    "sicherung.zurueckTitel": "Abbild einspielen",
    "sicherung.zurueckHinweis":
      "Ersetzt den gesamten Bestand – für einen neuen Server oder nach einem " +
      "Einbruch. Die Modell-Zugänge überleben auch einen neuen " +
      "AGORA_SECRET_KEY: sie hängen an der PIN, nicht am Server. Danach " +
      "melden sich alle neu an.",
    "sicherung.datei": "Abbild auswählen",
    "sicherung.befundStand": "Erstellt am {datum}, Version {version}",
    "sicherung.befundInhalt":
      "Enthält – Personen: {personen}, Foren: {foren}, Themen: {themen}, " +
      "Beiträge: {beitraege}, Modell-Zugänge: {zugaenge}",
    "sicherung.andererServer":
      "Dieses Abbild stammt von einem Server mit anderem Schlüssel. Das ist in " +
      "Ordnung – die Zugänge hängen an den PINs.",
    "sicherung.alteZugaenge":
      "Achtung – Zugänge am alten Serverschlüssel: {anzahl}. Sie stammen aus " +
      "der Zeit vor der Umschlagverschlüsselung und sind nach dem Einspielen " +
      "neu einzutragen.",
    "sicherung.einspielen": "Einspielen und Bestand ersetzen",
    "sicherung.sicherFrage":
      "Der gesamte bisherige Bestand wird gelöscht und durch das Abbild " +
      "ersetzt. Das lässt sich nicht rückgängig machen. Fortfahren?",
    "sicherung.eingespielt":
      "Eingespielt. Alle müssen sich neu anmelden und ihre PIN eingeben.",
    "person.hinzufuegen": "Person hinzufügen",
    "person.hinweis":
      "Das Zugangs-Token wird <strong>genau einmal</strong> angezeigt. " +
      "Gespeichert wird nur sein Hashwert &ndash; geht es verloren, legst du " +
      "die Person neu an.",
    "person.name": "Name",
    "person.admin": "darf Personen verwalten",
    "person.anlegen": "Anlegen",
    "person.offeneAntraege": "Offene Anträge",
    "person.keineAntraege": "Keine offenen Anträge.",
    "person.personen": "Personen",
    "person.verwaltet": "verwaltet Personen",
    "person.teilnehmer": "Teilnehmer",
    "person.dasBistDu": "das bist du",
    "person.ohneBegruendung": "ohne Begründung",
    "person.erreichbar": "Erreichbar",
    "person.keinKontakt": "kein Kontakt angegeben",
    "person.freigeben": "Freigeben",
    "person.ablehnen": "Ablehnen",
    "person.neuesToken": "Neues Einmal-Token",
    "person.neuesTokenTitel": "Wenn jemand sein Token verloren hat – die PIN bleibt",
    "person.pinZuruecksetzen": "PIN zurücksetzen",
    "person.pinZuruecksetzenTitel":
      "Wenn auch die PIN weg ist – löscht die Modell-Zugänge",
    "person.fragePin":
      "{name}: PIN UND Token zurücksetzen?\n\nAlle Modell-Zugänge dieser Person " +
      "werden dabei gelöscht – sonst wäre das ein Weg, ein Konto samt " +
      "hinterlegter Schlüssel zu übernehmen.",
    "person.frageToken":
      "{name}: neues Einmal-Token? Das alte gilt dann nicht mehr. Die PIN bleibt bestehen.",

    "token.titel": "Dein Zugangs-Token",
    "token.kopieren": "Kopieren",
    "token.kopiert": "Kopiert",
    "token.vonHand": "Bitte von Hand kopieren",
    "token.notiert": "Ich habe es notiert",
    "token.dauerhaftTitel": "Dein dauerhaftes Token",
    "token.dauerhaftText":
      "Das Einmal-Token ist verbraucht. Notiere dir dieses hier: es wird " +
      "nirgends noch einmal angezeigt, und niemand kann es nachschlagen.",
    "token.einmalTitel": "Einmal-Token für {name}",
    "token.einmalText":
      "Es gilt 48 Stunden und nur für die erste Anmeldung; dabei vergibt die " +
      "Person ihre PIN und bekommt ein eigenes Token – danach kommst auch du " +
      "nicht mehr hinein.",
    "token.schickeAn": "Schicke es an: {kontakt}. ",
    "token.gibWeiter": "Gib es weiter. ",
    "token.neuTitel": "Neues Einmal-Token für {name}",
    "token.neuTextPin":
      "PIN und Modell-Zugänge sind gelöscht. Bei der nächsten Anmeldung vergibt " +
      "die Person eine neue PIN.",
    "token.neuTextOhnePin":
      "Das bisherige Token gilt nicht mehr. Die PIN bleibt – ohne sie kommt " +
      "auch mit diesem Token niemand hinein.",

    "frei.titel": "Modell-Zugänge freischalten",
    "frei.hinweis":
      "Deine Zugänge liegen mit einem Schlüssel verschlüsselt, den nur deine " +
      "PIN öffnet. Zum Freischalten brauchst du sie noch einmal.",
    "frei.freischalten": "Freischalten",

    "pin.titel": "PIN ändern",
    "pin.hinweis":
      "Die PIN ist nur dir bekannt und liegt als nicht umkehrbarer Hash in der " +
      "Datenbank. Vergisst du sie, kann ein Admin dir einen neuen Zugang geben " +
      "&ndash; deine hinterlegten Modell-Zugänge gehen dabei verloren.",
    "pin.bisherige": "Bisherige PIN",
    "pin.neue": "Neue PIN",
    "pin.speichern": "Speichern",

    "neuesForum.titel": "Neues Forum",
    "neuesForum.hinweis":
      "Foren lassen sich beliebig tief schachteln – ein Oberforum je Vorhaben, " +
      "darunter Unterforen je Fragestellung.",
    "neuesForum.name": "Name",
    "neuesForum.namePh": "Datenarchitektur",
    "neuesForum.beschreibung": "Beschreibung",
    "neuesForum.beschreibungPh": "kurz, wofür dieses Forum da ist",
    "neuesForum.eltern": "Untergeordnet zu",
    "neuesForum.anlegen": "Anlegen",

    "allgemein.abbrechen": "Abbrechen",
  },

  en: {
    "app.untertitel": "A forum where agents work through topics together.",
    "app.sprache": "Deutsch",

    "gate.token": "Access token",
    "gate.pin": "PIN",
    "gate.pinHinweis":
      "On your <strong>first</strong> sign-in you choose the PIN yourself " +
      "(at least 6 characters). After that you need it every time &ndash; it " +
      "cannot be looked up anywhere, not even by admins.",
    "gate.anmelden": "Sign in",
    "gate.laeuft": "Signing in ...",
    "gate.antragOeffnen": "No access yet? Request it here",

    "antrag.titel": "Request access",
    "antrag.hinweis":
      "An admin approves it. You then get a one-time token, which is exchanged " +
      "for your own on your first sign-in &ndash; after that nobody else knows it.",
    "antrag.name": "Name",
    "antrag.kontakt": "How can we reach you?",
    "antrag.kontaktPh": "email address or handle",
    "antrag.zweck": "What do you need access for?",
    "antrag.zweckPh": "briefly, so the admin can place you",
    "antrag.senden": "Send request",
    "antrag.zurueck": "back to sign-in",
    "antrag.eingegangen":
      "Request received. As soon as someone approves it you will get a one-time token.",

    "nav.forum": "Forum",
    "nav.agenten": "Agents",
    "nav.modelle": "Models",
    "nav.personen": "People",
    "nav.pinAendern": "Change PIN",
    "nav.abmelden": "Sign out",

    "schloss.frei": "Models unlocked until {zeit}",
    "schloss.gesperrt": "Models locked",
    "schloss.sperrenTitel": "Click to lock right away",
    "schloss.freischaltenTitel": "Click to unlock with your PIN",

    "forum.neuesThema": "+ New topic",
    "forum.foren": "Boards",
    "forum.neuesForum": "+ Board",
    "forum.alleThemen": "All topics",
    "forum.ohneForum": "Unsorted",
    "forum.keinThema": "No topic selected.",
    "forum.loeschenTitel": "Delete board (must be empty)",
    "forum.ganzOben": "– top level –",
    "forum.ohne": "– no board –",

    "thread.keinZiel": "no goal set",
    "thread.runden": "rounds",
    "thread.takt": "pace",
    "thread.endeBeiEinigkeit": "Ends once everyone agrees on",
    "thread.bisBudget": "runs until the round budget is used up",
    "thread.pause": "Pause",
    "thread.weiter": "Resume",
    "thread.mehrRunden": "+5 rounds",
    "thread.beenden": "Finish",
    "thread.loeschen": "Delete",
    "thread.loeschenFrage": "Delete this topic and all its posts for good?",
    "thread.dazuholen": "+ add participant",
    "thread.rausTitel": "remove from the discussion",
    "thread.mitschreiben": "Join in as a human",
    "thread.mitschreibenPh": "objection, correction, new requirement ...",
    "thread.senden": "Post",
    "thread.weiterlaufen": "Let the discussion continue afterwards",
    "thread.datei": "Contribute a document",
    "thread.dateiHinweis":
      "PDF, Word, PowerPoint, OpenDocument, HTML, text – the text is extracted, " +
      "the file itself is not stored.",
    "thread.dateiLaeuft": 'Reading "{name}" ...',
    "thread.dokumentWeg": "Remove document from the discussion",
    "thread.runde": "Round",
    "thread.mensch": "Human",
    "thread.system": "Forum",
    "thread.dokument": "Document",
    "thread.rechner": "Compute",
    "thread.uebersetzen": "translate",
    "thread.uebersetztLaeuft": "translating ...",
    "thread.original": "show original",

    "neuesThema.titel": "New topic",
    "neuesThema.name": "Title",
    "neuesThema.namePh": "Architecture for a globally distributed database",
    "neuesThema.ziel": "Goal of the discussion",
    "neuesThema.zielPh":
      "Compare two approaches and give a recommendation with reasons.",
    "neuesThema.forum": "Board",
    "neuesThema.teilnehmer": "Participants (multiple selection)",
    "neuesThema.ablauf": "Turn taking",
    "neuesThema.moderatorWaehlt": "Moderator picks",
    "neuesThema.reihum": "Round robin",
    "neuesThema.moderator": "Moderator",
    "neuesThema.runden": "Rounds",
    "neuesThema.takt": "Pace (sec.)",
    "neuesThema.zustimmung": "Agreement word (empty = full round count)",
    "neuesThema.synthese": "Closing synthesis",
    "neuesThema.grundlage": "Basis (optional, several possible)",
    "neuesThema.grundlageHinweis":
      "Read in before the first round and available to the agents from the " +
      "start. PDF, Word, LaTeX, text, source code.",
    "neuesThema.grundlageLaeuft": "Reading {name} ({nummer}/{gesamt}) …",
    "neuesThema.grundlageFehler":
      "The topic exists, but a document did not make it: {fehler} – it is " +
      "paused, you can add the document and then start.",
    "neuesThema.art": "Kind of discussion",
    "neuesThema.artFrei": "Open discussion \u2013 any topic",
    "neuesThema.artPruefen": "Check a proof, derivation or calculation",
    "neuesThema.artText": "Check a text \u2013 article, report, blog post",
    "neuesThema.artBegutachten": "Review an academic paper",
    "neuesThema.artAntrag": "Review a funding proposal",
    "neuesThema.artCode": "Discuss code",
    "neuesThema.artHinweis":
      "Decides which questions the agents ask. An open discussion needs no " +
      "document \u2013 everything else gives the agents a checking brief and " +
      "forbids them to agree before they have checked.",
    "neuesThema.werkzeuge": "Agents may compute",
    "neuesThema.werkzeugeHinweis":
      "Agents can write Python blocks that get executed – for figures, " +
      "transformations and counterexamples. The output appears as a post of its " +
      "own in the transcript.",
    "neuesThema.starten": "Start",
    "neuesThema.zuWenigAgenten": "Create at least two agents first (tab 'Agents').",
    "neuesThema.keinTeilnehmer": "Please select at least one participant.",

    "agent.anlegen": "Create agent",
    "agent.name": "Name",
    "agent.namePh": "Reviewer",
    "agent.rolle": "Role (used when picking the next speaker)",
    "agent.rollePh": "checks assumptions and method",
    "agent.modell": "Model (LiteLLM string)",
    "agent.zugang": "Credential",
    "agent.ohneZugang": "– no credential –",
    "agent.ohneZugangLang": "– none (key from the environment) –",
    "agent.persona": "Persona / system prompt",
    "agent.personaPh": "You are ...",
    "agent.temperatur": "Temperature",
    "agent.maxTokens": "Max. tokens",
    "agent.oeffentlich": "visible to everyone",
    "agent.speichern": "Save agent",
    "agent.zugangZeile": "Credential",
    "agent.fremderZugang": "someone else's credential",
    "agent.keinZugangWarnung": "no credential – model calls will fail",
    "agent.loeschen": "Delete",

    "teilnahme.titel": "How long my agents take part",
    "teilnahme.hinweis":
      "This is how long <strong>your</strong> agents may keep talking in a " +
      "topic, counted from your own last post there. After that they drop out " +
      "until you write again &ndash; so nobody can keep a topic running at your " +
      "expense without you noticing.",
    "teilnahme.unbegrenzt": "Unlimited – your agents keep going until the topic ends.",
    "teilnahme.eineStunde": "1 hour after your own last post in the topic.",
    "teilnahme.stunden": "{n} hours after your own last post in the topic.",

    "zugang.titel": "Add a model credential",
    "zugang.hinweis":
      "The key is stored encrypted and never handed back out. For your own " +
      "vLLM/Ollama in the cluster, provider <code>openai</code> plus the base " +
      "URL is enough &ndash; a key is often not needed then.",
    "zugang.bezeichnung": "Label",
    "zugang.bezeichnungPh": "My Anthropic key",
    "zugang.provider": "Provider",
    "zugang.schluessel": "Key / token (optional)",
    "zugang.basisUrl": "Base URL (optional)",
    "zugang.wieGeschickt": "How is it sent?",
    "zugang.alsApiKey": "as an API key (the usual case)",
    "zugang.alsBearer": "as a bearer token",
    "zugang.alsKopffeld": "in a header of its own",
    "zugang.garNicht": "not at all (open endpoint)",
    "zugang.kopffeldName": "Header name",
    "zugang.zusatz": "Extra parameters as JSON (optional)",
    "zugang.speichern": "Save credential",
    "zugang.hinterlegt": "key stored",
    "zugang.ohneSchluessel": "without a key",
    "zugang.mitZusatz": "with extra parameters",
    "zugang.loeschen": "Delete",

    "sicherung.titel": "Backup",
    "sicherung.passwort": "Password for the image (optional)",
    "sicherung.passwortHinweis":
      "Leaving it empty means unprotected. With a password the file is " +
      "encrypted as a whole – use at least 10 characters, better a sentence. " +
      "A file can be attacked at leisure at home, unlike a PIN at the server. " +
      "Lose the password and the image is lost.",
    "sicherung.fertigGeschuetzt": "Saved and encrypted: {name} ({groesse})",
    "sicherung.passwortDatei": "Password of the image",
    "sicherung.passwortNoetig":
      "This image is password-protected. Please enter it.",
    "sicherung.hinweis":
      "An image of the whole forum as a single file – people, forums, topics, " +
      "posts and the encrypted model credentials. Your browser asks where to " +
      "put it. Keep it as carefully as the database itself.",
    "sicherung.herunterladen": "Download backup",
    "sicherung.laeuft": "Creating …",
    "sicherung.fertig": "Saved: {name} ({groesse})",
    "sicherung.zurueckTitel": "Restore an image",
    "sicherung.zurueckHinweis":
      "Replaces everything – for a new server or after a break-in. The model " +
      "credentials survive even a new AGORA_SECRET_KEY: they hang on the PIN, " +
      "not on the server. Afterwards everyone logs in again.",
    "sicherung.datei": "Choose an image",
    "sicherung.befundStand": "Created {datum}, version {version}",
    "sicherung.befundInhalt":
      "Contains – people: {personen}, forums: {foren}, topics: {themen}, " +
      "posts: {beitraege}, model credentials: {zugaenge}",
    "sicherung.andererServer":
      "This image comes from a server with a different key. That is fine – the " +
      "credentials hang on the PINs.",
    "sicherung.alteZugaenge":
      "Careful: {anzahl} credentials still predate envelope encryption and hang " +
      "on the old server key. They have to be entered again after restoring.",
    "sicherung.einspielen": "Restore and replace everything",
    "sicherung.sicherFrage":
      "Everything currently stored will be deleted and replaced by the image. " +
      "This cannot be undone. Continue?",
    "sicherung.eingespielt":
      "Restored. Everyone has to log in again and enter their PIN.",
    "person.hinzufuegen": "Add a person",
    "person.hinweis":
      "The access token is shown <strong>exactly once</strong>. Only its hash " +
      "is stored &ndash; if it is lost, you create the person again.",
    "person.name": "Name",
    "person.admin": "may manage people",
    "person.anlegen": "Create",
    "person.offeneAntraege": "Open requests",
    "person.keineAntraege": "No open requests.",
    "person.personen": "People",
    "person.verwaltet": "manages people",
    "person.teilnehmer": "participant",
    "person.dasBistDu": "that's you",
    "person.ohneBegruendung": "no reason given",
    "person.erreichbar": "Reachable at",
    "person.keinKontakt": "no contact given",
    "person.freigeben": "Approve",
    "person.ablehnen": "Reject",
    "person.neuesToken": "New one-time token",
    "person.neuesTokenTitel": "If someone lost their token – the PIN stays",
    "person.pinZuruecksetzen": "Reset PIN",
    "person.pinZuruecksetzenTitel":
      "If the PIN is gone too – deletes the model credentials",
    "person.fragePin":
      "{name}: reset PIN AND token?\n\nAll model credentials of this person will " +
      "be deleted – otherwise this would be a way to take over an account " +
      "together with its stored keys.",
    "person.frageToken":
      "{name}: issue a new one-time token? The old one stops working. The PIN stays.",

    "token.titel": "Your access token",
    "token.kopieren": "Copy",
    "token.kopiert": "Copied",
    "token.vonHand": "Please copy it by hand",
    "token.notiert": "I have written it down",
    "token.dauerhaftTitel": "Your permanent token",
    "token.dauerhaftText":
      "The one-time token is used up. Write this one down: it is never shown " +
      "again, and nobody can look it up.",
    "token.einmalTitel": "One-time token for {name}",
    "token.einmalText":
      "It is valid for 48 hours and only for the first sign-in; during that the " +
      "person chooses their PIN and gets a token of their own – after that even " +
      "you cannot get in.",
    "token.schickeAn": "Send it to: {kontakt}. ",
    "token.gibWeiter": "Pass it on. ",
    "token.neuTitel": "New one-time token for {name}",
    "token.neuTextPin":
      "PIN and model credentials are deleted. On the next sign-in the person " +
      "chooses a new PIN.",
    "token.neuTextOhnePin":
      "The previous token stops working. The PIN stays – without it nobody gets " +
      "in with this token either.",

    "frei.titel": "Unlock model credentials",
    "frei.hinweis":
      "Your credentials are encrypted with a key that only your PIN opens. To " +
      "unlock them you need it once more.",
    "frei.freischalten": "Unlock",

    "pin.titel": "Change PIN",
    "pin.hinweis":
      "The PIN is known only to you and is stored as a one-way hash. If you " +
      "forget it, an admin can give you a new access &ndash; your stored model " +
      "credentials are lost in the process.",
    "pin.bisherige": "Current PIN",
    "pin.neue": "New PIN",
    "pin.speichern": "Save",

    "neuesForum.titel": "New board",
    "neuesForum.hinweis":
      "Boards can be nested as deeply as you like – one per undertaking, with " +
      "sub-boards per question.",
    "neuesForum.name": "Name",
    "neuesForum.namePh": "Data architecture",
    "neuesForum.beschreibung": "Description",
    "neuesForum.beschreibungPh": "briefly, what this board is for",
    "neuesForum.eltern": "Nested under",
    "neuesForum.anlegen": "Create",

    "allgemein.abbrechen": "Cancel",
  },
};

function spracheErmitteln() {
  const gemerkt = localStorage.getItem("agora_sprache");
  if (gemerkt && TEXTE[gemerkt]) return gemerkt;
  return (navigator.language || "de").toLowerCase().startsWith("de") ? "de" : "en";
}

let SPRACHE = spracheErmitteln();

/** Uebersetzt einen Schluessel; {platzhalter} werden aus werte ersetzt. */
function t(schluessel, werte) {
  let text = TEXTE[SPRACHE][schluessel];
  if (text === undefined) {
    // Fehlt eine Uebersetzung, ist Deutsch besser als ein nackter Schluessel.
    text = TEXTE.de[schluessel];
    if (text === undefined) return schluessel;
  }
  if (werte) {
    for (const [name, wert] of Object.entries(werte)) {
      text = text.split(`{${name}}`).join(wert);
    }
  }
  return text;
}

/** Setzt Text, ohne verschachtelte Elemente zu zerstoeren.
 *
 * Ein <label>Beschriftung <input></label> wuerde bei textContent sein
 * Eingabefeld verlieren. Deshalb wird nur der erste Textknoten ersetzt,
 * wenn es Kindelemente gibt.
 */
function setzeText(el, text) {
  if (!el.children.length) {
    el.textContent = text;
    return;
  }
  const textknoten = Array.from(el.childNodes).find((n) => n.nodeType === Node.TEXT_NODE);
  if (textknoten) {
    textknoten.nodeValue = text;
  } else {
    el.insertBefore(document.createTextNode(text), el.firstChild);
  }
}

/** Setzt alle im HTML markierten Texte. */
function uebersetzeSeite(wurzel = document) {
  document.documentElement.lang = SPRACHE;
  wurzel.querySelectorAll("[data-i18n]").forEach((el) => {
    setzeText(el, t(el.dataset.i18n));
  });
  wurzel.querySelectorAll("[data-i18n-html]").forEach((el) => {
    el.innerHTML = t(el.dataset.i18nHtml);
  });
  wurzel.querySelectorAll("[data-i18n-ph]").forEach((el) => {
    el.placeholder = t(el.dataset.i18nPh);
  });
  wurzel.querySelectorAll("[data-i18n-title]").forEach((el) => {
    el.title = t(el.dataset.i18nTitle);
  });
}

function spracheWechseln() {
  SPRACHE = SPRACHE === "de" ? "en" : "de";
  localStorage.setItem("agora_sprache", SPRACHE);
  uebersetzeSeite();
  // Die dynamisch erzeugten Teile zeichnet app.js neu.
  if (typeof nachSprachwechsel === "function") nachSprachwechsel();
}

document.addEventListener("DOMContentLoaded", () => uebersetzeSeite());

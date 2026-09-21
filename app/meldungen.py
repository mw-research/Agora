"""Uebersetzung der Fehlermeldungen an die Oberflaeche.

Der Quelltext bleibt deutsch, auch die Meldungen im Code. Hier steht nur, wie
sie auf Englisch heissen; uebersetzt wird ganz am Ende, in einem einzigen
Ausnahme-Handler, damit an den Fundstellen nichts verkompliziert wird.

Zusammengesetzte Meldungen (mit eingefuegten Namen oder Zahlen) fallen nicht
unter die feste Liste - dafuer stehen unten ein paar Muster. Was weder noch
trifft, bleibt deutsch; das ist unschoen, aber besser als eine falsche
Uebersetzung.
"""

from __future__ import annotations

import re

EN: dict[str, str] = {
    # Anmeldung und Zugang
    "Kein Token uebermittelt (Cookie, X-Agora-Token oder Bearer)":
        "No token supplied (cookie, X-Agora-Token or bearer)",
    "Unbekanntes Token": "Unknown token",
    "Einmal-Token: bitte zuerst ueber die Anmeldung einloesen.":
        "One-time token: please redeem it through the sign-in page first.",
    "Das Einmal-Token ist abgelaufen - bitte einen neuen Zugang anfragen.":
        "The one-time token has expired - please request access again.",
    "PIN stimmt nicht.": "Wrong PIN.",
    "Die bisherige PIN stimmt nicht.": "The current PIN is wrong.",
    "Nur fuer Admins": "Admins only",
    "Name schon vergeben": "That name is taken",
    "Nicht gefunden": "Not found",

    # Antraege
    "Selbstregistrierung ist abgeschaltet - wende dich an die Betreiber.":
        "Self-registration is switched off - please contact the operators.",
    "Diesen Namen gibt es schon - frag nach einem neuen Token.":
        "That name already exists - ask for a new token.",
    "Fuer diesen Namen laeuft schon ein Antrag.":
        "There is already a pending request for that name.",
    "Zu viele offene Antraege - bitte spaeter noch einmal.":
        "Too many pending requests - please try again later.",
    "Antrag nicht gefunden": "Request not found",
    "Diesen Namen gibt es inzwischen schon.": "That name exists by now.",

    # Modell-Zugaenge
    "auth_style muss api_key, bearer, header oder none sein":
        "auth_style must be api_key, bearer, header or none",
    "Zusatzparameter muessen ein JSON-Objekt sein":
        "Extra parameters must be a JSON object",
    "Credential gehoert dir nicht": "That credential is not yours",
    "Die Modell-Zugaenge sind gesperrt.": "The model credentials are locked.",
    "Die Freischaltung ist abgelaufen.": "The unlock period has expired.",
    "Fuer dieses Konto gibt es keinen Datenschluessel.":
        "This account has no data key.",
    "Der Datenschluessel laesst sich mit dieser PIN nicht oeffnen.":
        "The data key cannot be opened with this PIN.",

    # PIN
    "Die PIN braucht mindestens 6 Zeichen.": "The PIN needs at least 6 characters.",
    "Die PIN ist zu lang (hoechstens 128 Zeichen).":
        "The PIN is too long (128 characters at most).",
    "Diese PIN ist zu einfach - bitte mehr verschiedene Zeichen.":
        "That PIN is too simple - please use more different characters.",

    # Foren und Themen
    "Uebergeordnetes Forum gibt es nicht": "The parent board does not exist",
    "Ein Forum kann nicht sich selbst enthalten": "A board cannot contain itself",
    "Das waere ein Ring: Ziel liegt unterhalb dieses Forums":
        "That would be a cycle: the target sits below this board",
    "Forum ist nicht leer - erst Unterforen und Themen verschieben oder loeschen":
        "The board is not empty - move or delete its sub-boards and topics first",
    "Forum gibt es nicht": "That board does not exist",
    "Nur der Ersteller oder ein Admin darf loeschen":
        "Only the creator or an admin may delete this",
    "mode muss 'selector' oder 'roundrobin' sein":
        "mode must be 'selector' or 'roundrobin'",
    "Teilnehmernamen muessen im Thread eindeutig sein":
        "Participant names must be unique within a topic",
    "Moderator muss Teilnehmer des Threads sein":
        "The moderator must be a participant of the topic",
    "action muss pause | resume | stop | extend sein":
        "action must be pause | resume | stop | extend",
    "Thread nicht gefunden": "Topic not found",
    "Agent nicht verfuegbar": "Agent not available",
    "Der letzte Teilnehmer kann nicht entfernt werden":
        "The last participant cannot be removed",
    "Teilnehmer nicht gefunden": "Participant not found",

    # Dokumente
    "Dokument nicht gefunden": "Document not found",
    "Nur wer es beigesteuert hat oder ein Admin darf es entfernen":
        "Only whoever contributed it, or an admin, may remove it",
    "Die Datei ist leer.": "The file is empty.",
    "In der Datei steht kein Text.": "There is no text in the file.",
    "Die Datei ist keine lesbare Textdatei.": "The file is not readable text.",
    "Die Datei ist beschaedigt oder kein Office-Dokument.":
        "The file is damaged or not an Office document.",
    "Im Dokument ist kein Textteil zu finden.":
        "No text part could be found in the document.",
    "Das PDF ist passwortgeschuetzt.": "The PDF is password protected.",
    "Aus dem PDF liess sich kein Text gewinnen - vermutlich ein Scan "
    "ohne Texterkennung.":
        "No text could be extracted from the PDF - probably a scan without OCR.",
}

# Zusammengesetzte Meldungen: Muster statt fester Text.
MUSTER: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^Zu viele Fehlversuche\. Naechster Versuch in (\d+) Minuten\.$"),
     r"Too many failed attempts. Next try in \1 minutes."),
    (re.compile(r"^Der Antrag ist bereits freigegeben\.$"),
     "The request has already been approved."),
    (re.compile(r"^Der Antrag ist bereits abgelehnt\.$"),
     "The request has already been rejected."),
    (re.compile(r"^'(.+)' ist schon dabei$"), r"'\1' is already taking part"),
    (re.compile(r"^Es gibt hier schon einen Teilnehmer namens '(.+)'$"),
     r"There is already a participant called '\1' here"),
    (re.compile(r"^Agent (\S+) nicht verfuegbar$"), r"Agent \1 not available"),
    (re.compile(r"^Zusatzparameter sind kein gueltiges JSON: (.+)$"),
     r"Extra parameters are not valid JSON: \1"),
    (re.compile(r"^Die Datei ist groesser als (\d+) MB\.$"),
     r"The file is larger than \1 MB."),
    (re.compile(r"^Mit Dateien der Art '(.+)' kann ich nichts anfangen\.$"),
     r"Files of type '\1' cannot be used."),
    (re.compile(r"^(.+) Melde dich mit deiner PIN an, dann kannst du Zugaenge anlegen\.$"),
     r"\1 Sign in with your PIN, then you can add credentials."),
]


def uebersetze(text: str, sprache: str) -> str:
    if sprache != "en" or not isinstance(text, str):
        return text
    fest = EN.get(text)
    if fest is not None:
        return fest
    for muster, ersatz in MUSTER:
        neu, treffer = muster.subn(ersatz, text)
        if treffer:
            # Die Vorderteile mancher Muster sind selbst uebersetzbar.
            return uebersetze_teile(neu)
    return text


def uebersetze_teile(text: str) -> str:
    """Zweiter Durchgang fuer Meldungen, die eine andere enthalten."""
    for deutsch, englisch in EN.items():
        if deutsch in text:
            return text.replace(deutsch, englisch)
    return text

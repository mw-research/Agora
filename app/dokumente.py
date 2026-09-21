"""Text aus hochgeladenen Schriftstuecken gewinnen.

Die Datei selbst wird nirgends abgelegt - sie kommt als Bytes herein, der
Text geht als Beitrag in den Thread, die Bytes werden verworfen. Damit die
Agenten ein Dokument besprechen koennen, muss sein Text im Verlauf stehen:
der Worker laeuft in einem anderen Prozess und kennt nichts, was nur im
Arbeitsspeicher der API liegt.

Absichtlich ohne schwere Abhaengigkeiten. Office-Dateien sind ZIP-Archive
mit XML darin, das laesst sich mit der Standardbibliothek auslesen; nur
fuer PDF braucht es eine Bibliothek.
"""

from __future__ import annotations

import io
import re
import zipfile
from xml.etree import ElementTree

# Groessere Dateien lehnen wir ab, statt sie halb zu verarbeiten.
MAX_BYTES = 10 * 1024 * 1024
# Laenger schneiden wir den Text ab: er geht in JEDEN Modellaufruf des
# Threads ein, ein ganzes Buch wuerde den Kontext sprengen und teuer werden.
MAX_ZEICHEN = 40_000

NUR_TEXT = {
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".yaml", ".yml",
    ".log", ".ini", ".cfg", ".toml", ".properties",
    # LaTeX und BibTeX bewusst hier und nicht bei QUELLTEXT: ein Beweis
    # ist Prosa mit Mathematik darin. Als Code-Block ausgezeichnet wuerden
    # die Agenten das Markup besprechen statt den Inhalt.
    ".tex", ".bib", ".sty", ".cls",
}
# Bewusst NICHT dabei: .env und aehnliche Geheimnisdateien. Was hier
# hochgeladen wird, geht an die Modellanbieter der Teilnehmer.

# Quelltext. Wird wie Text gelesen, aber im Beitrag als Code ausgezeichnet,
# damit die Agenten ihn als solchen behandeln und nicht als Prosa.
QUELLTEXT = {
    ".py": "python", ".pyi": "python", ".ipynb": "json",
    ".js": "javascript", ".mjs": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript",
    ".java": "java", ".kt": "kotlin", ".scala": "scala", ".groovy": "groovy",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp", ".hpp": "cpp",
    ".cs": "csharp", ".go": "go", ".rs": "rust", ".rb": "ruby", ".php": "php",
    ".swift": "swift", ".m": "objectivec", ".r": "r", ".jl": "julia",
    ".sh": "bash", ".bash": "bash", ".zsh": "bash", ".ps1": "powershell",
    ".sql": "sql", ".css": "css", ".scss": "scss", ".vue": "vue",
    ".diff": "diff", ".patch": "diff",
    ".dockerfile": "dockerfile", ".cmake": "cmake", ".mk": "make",
    ".xml": "xml", ".xsd": "xml", ".gradle": "groovy", ".lua": "lua",
}


class DokumentFehler(ValueError):
    """Die Datei laesst sich nicht in Text verwandeln."""


def endung(dateiname: str) -> str:
    name = dateiname.strip().lower()
    # Dateien ohne Punkt, die trotzdem Quelltext sind.
    for besonders in ("dockerfile", "makefile", "cmakelists.txt"):
        if name.endswith(besonders):
            return {"dockerfile": ".dockerfile", "makefile": ".mk",
                    "cmakelists.txt": ".cmake"}[besonders]
    _, punkt, rest = name.rpartition(".")
    return f".{rest}" if punkt else ""


def _entschluesseln(daten: bytes) -> str:
    for kodierung in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            return daten.decode(kodierung)
        except UnicodeDecodeError:
            continue
    raise DokumentFehler("Die Datei ist keine lesbare Textdatei.")


def _aus_xml_teilen(daten: bytes, pfad_muster: str, tag_ende: str, trenner_tag: str = "") -> str:
    """Text aus den XML-Teilen eines Office-Archivs einsammeln.

    pfad_muster waehlt die XML-Dateien im Archiv, tag_ende den Tag mit dem
    Text (Namensraeume ignorieren wir und pruefen nur das Ende).
    """
    try:
        archiv = zipfile.ZipFile(io.BytesIO(daten))
    except zipfile.BadZipFile as exc:
        raise DokumentFehler("Die Datei ist beschaedigt oder kein Office-Dokument.") from exc

    teile = sorted(n for n in archiv.namelist() if re.fullmatch(pfad_muster, n))
    if not teile:
        raise DokumentFehler("Im Dokument ist kein Textteil zu finden.")

    absaetze: list[str] = []
    for name in teile:
        try:
            baum = ElementTree.fromstring(archiv.read(name))
        except ElementTree.ParseError:
            continue
        aktuell: list[str] = []
        for element in baum.iter():
            marke = element.tag.rpartition("}")[2]
            if marke == tag_ende and element.text:
                aktuell.append(element.text)
            elif trenner_tag and marke == trenner_tag and aktuell:
                absaetze.append("".join(aktuell))
                aktuell = []
        if aktuell:
            absaetze.append("".join(aktuell))
    return "\n".join(a for a in absaetze if a.strip())


def _aus_pdf(daten: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - nur bei fehlender Abhaengigkeit
        raise DokumentFehler("PDF-Unterstuetzung fehlt (pypdf ist nicht installiert).") from exc

    try:
        leser = PdfReader(io.BytesIO(daten))
    except Exception as exc:
        raise DokumentFehler(f"PDF nicht lesbar: {exc}") from exc

    if getattr(leser, "is_encrypted", False):
        raise DokumentFehler("Das PDF ist passwortgeschuetzt.")

    seiten = []
    for nummer, seite in enumerate(leser.pages, start=1):
        try:
            text = seite.extract_text() or ""
        except Exception:
            text = ""
        if text.strip():
            seiten.append(f"[Seite {nummer}]\n{text.strip()}")
    if not seiten:
        raise DokumentFehler(
            "Aus dem PDF liess sich kein Text gewinnen - vermutlich ein Scan "
            "ohne Texterkennung."
        )
    return "\n\n".join(seiten)


def _aus_html(daten: bytes) -> str:
    roh = _entschluesseln(daten)
    ohne_kopf = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", roh)
    mit_umbruch = re.sub(r"(?i)<(br|/p|/div|/h[1-6]|/li|/tr)\s*/?>", "\n", ohne_kopf)
    nur_text = re.sub(r"(?s)<[^>]+>", " ", mit_umbruch)
    entwirrt = (
        nur_text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
    )
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]{2,}", " ", entwirrt)).strip()


def _aus_rtf(daten: bytes) -> str:
    roh = _entschluesseln(daten)
    ohne_gruppen = re.sub(r"\\\*\\[a-zA-Z]+[^{}]*", " ", roh)
    ohne_befehle = re.sub(r"\\[a-zA-Z]+-?\d* ?", " ", ohne_gruppen)
    return re.sub(r"\s{2,}", " ", ohne_befehle.replace("{", " ").replace("}", " ")).strip()


def text_gewinnen(dateiname: str, daten: bytes) -> tuple[str, bool]:
    """Liefert (text, gekuerzt).

    Wirft DokumentFehler, wenn sich nichts Sinnvolles gewinnen laesst.
    """
    if not daten:
        raise DokumentFehler("Die Datei ist leer.")
    if len(daten) > MAX_BYTES:
        raise DokumentFehler(
            f"Die Datei ist groesser als {MAX_BYTES // (1024 * 1024)} MB."
        )

    art = endung(dateiname)
    if art == ".pdf":
        text = _aus_pdf(daten)
    elif art == ".docx":
        text = _aus_xml_teilen(daten, r"word/document\.xml", "t", "p")
    elif art == ".pptx":
        text = _aus_xml_teilen(daten, r"ppt/slides/slide\d+\.xml", "t", "p")
    elif art in (".odt", ".odp", ".ods"):
        text = _aus_xml_teilen(daten, r"content\.xml", "p")
    elif art in (".html", ".htm", ".xhtml"):
        text = _aus_html(daten)
    elif art == ".rtf":
        text = _aus_rtf(daten)
    elif art in QUELLTEXT:
        # Als Code auszeichnen: so behandeln die Agenten ihn als Quelltext und
        # nicht als Prosa - und die Oberflaeche stellt ihn in Festbreite dar.
        text = f"```{QUELLTEXT[art]}\n{_entschluesseln(daten).strip()}\n```"
    elif art in NUR_TEXT or art == "":
        text = _entschluesseln(daten)
    else:
        # Unbekannte Endung: einen Textversuch ist es wert, aber nur wenn
        # nicht offensichtlich Binaeres darin steht.
        if b"\x00" in daten[:4096]:
            raise DokumentFehler(f"Mit Dateien der Art '{art}' kann ich nichts anfangen.")
        text = _entschluesseln(daten)

    text = text.replace("\r\n", "\n").strip()
    if not text:
        raise DokumentFehler("In der Datei steht kein Text.")

    if len(text) > MAX_ZEICHEN:
        return text[:MAX_ZEICHEN].rstrip(), True
    return text, False

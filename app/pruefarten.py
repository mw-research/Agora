"""Was fuer ein Material liegt vor - und welche Fragen stellt man daran?

Ein Forschungsantrag wie ein Beweis geprueft ergibt Unsinn: dort zaehlen
Arbeitsplan, Passung und Budget, nicht Induktionsschritte. Umgekehrt hilft es
einem Beweis nicht, wenn nach Verwertungsperspektiven gefragt wird.

Deshalb waehlt man beim Anlegen eines Themas die Art, und die entscheidet,
welcher Auftrag im Prompt landet. Bewusst eine Auswahl von Hand und keine
Erkennung: was jemand mit einem Dokument vorhat, steht nicht darin. Dieselbe
PDF kann ein Gutachtenauftrag sein oder eine Literaturstelle.

Zum Erweitern reicht ein Eintrag in ARTEN. Der Schluessel wandert in die
Datenbank, die Beschriftung in app/static/i18n.js ("neuesThema.art*").
"""

from __future__ import annotations

from dataclasses import dataclass

# Gilt fuer jede Art ausser der freien Diskussion. Ohne diesen Satz einigen
# sich die Agenten auf den Gesamteindruck, und die Einigkeitsregel beendet das
# Thema, bevor irgendetwas geprueft wurde.
KEINE_VORSCHNELLE_ZUSTIMMUNG = (
    "- Stimme nicht zu, solange ein Punkt ungeprueft ist. 'Wirkt plausibel' "
    "ist keine Pruefung.\n"
)


@dataclass(frozen=True)
class Pruefart:
    """Ein Auftrag an die Agenten, zweigeteilt.

    ``auftrag`` gilt immer, ``mit_rechnen`` kommt nur dazu, wenn im Thema
    gerechnet werden darf - sonst fordert der Prompt etwas ein, das gar nicht
    geht.
    """

    schluessel: str
    auftrag: str
    mit_rechnen: str = ""

    def prompt(self, darf_rechnen: bool) -> str:
        if not self.auftrag:
            return ""
        teile = [self.auftrag, KEINE_VORSCHNELLE_ZUSTIMMUNG]
        if darf_rechnen and self.mit_rechnen:
            teile.insert(1, self.mit_rechnen)
        return "".join(teile)


ARTEN: dict[str, Pruefart] = {
    # Der Normalfall: reden, nicht pruefen.
    "frei": Pruefart(schluessel="frei", auftrag=""),

    "pruefen": Pruefart(
        schluessel="pruefen",
        auftrag=(
            "- Das ist kein Gespraech ueber das Material, sondern eine "
            "Pruefung. Benenne zuerst, was genau behauptet wird, geh die "
            "Schritte dann EINZELN durch und sage bei jedem, ob er traegt. "
            "Eine Herleitung faellt an einem Schritt, nicht am "
            "Gesamteindruck.\n"
        ),
        mit_rechnen=(
            "- Rechne nach, statt zu glauben: Summen, Reihen und Grenzwerte "
            "ausrechnen, Umformungen mit sympy nachvollziehen, fuer jede "
            "Allaussage ein Gegenbeispiel suchen, Randfaelle einsetzen "
            "(n=0, leere Menge, Nenner null).\n"
        ),
    ),

    "text": Pruefart(
        schluessel="text",
        auftrag=(
            "- Pruefe den Text, statt ihn nachzuerzaehlen. Trenne sauber: Was "
            "wird behauptet, was ist belegt, was nur nahegelegt? Woher stammen "
            "Zahlen und Zitate, und geben die Quellen her, was der Text aus "
            "ihnen macht? Was muesste zutreffen, damit die Kernaussage faellt "
            "- steht dazu etwas da?\n"
            "- Achte auf die Auswahl: Was fehlt, welche Gegenposition kommt "
            "nicht vor, welche Deutung legt die Wortwahl nahe? Belege das an "
            "konkreten Stellen, statt dem Text eine Absicht zu unterstellen.\n"
        ),
        mit_rechnen=(
            "- Rechne Zahlenangaben nach: Prozente gegen Grundgesamtheiten, "
            "Steigerungen gegen Ausgangswerte, Hochrechnungen gegen "
            "Groessenordnungen, Anteile die sich zu mehr als hundert "
            "addieren. In Texten fuer ein breites Publikum ist das die "
            "haeufigste Bruchstelle.\n"
        ),
    ),

    "begutachten": Pruefart(
        schluessel="begutachten",
        auftrag=(
            "- Begutachte wie fuer eine Fachzeitschrift. Der Reihe nach: "
            "Welche Frage wird gestellt? Traegt die Methodik sie? Stuetzen "
            "die gezeigten Daten die Behauptung - oder nur eine schwaechere? "
            "Was ist die naechstliegende alternative Erklaerung? Liesse sich "
            "die Arbeit mit dem Beschriebenen wiederholen? Welche "
            "Einschraenkung fehlt?\n"
            "- Trenne, was belegt ist, von dem, was nur behauptet wird, und "
            "zitiere die Stelle, auf die du dich beziehst.\n"
        ),
        mit_rechnen=(
            "- Rechne die Zahlen nach: Stichprobengroessen und Prozentangaben "
            "auf Konsistenz, Mittelwerte gegen Streuungen, Einheiten und "
            "Groessenordnungen, Teststatistiken und Effektstaerken. Zahlen in "
            "Papern gehen ueberraschend oft nicht auf.\n"
        ),
    ),

    "antrag": Pruefart(
        schluessel="antrag",
        auftrag=(
            "- Lies wie ein Gutachter des Geldgebers, nicht wie ein "
            "wohlwollender Kollege. Pruefe: Passt das Vorhaben zur "
            "Ausschreibung und ihren Kriterien? Ist das Ziel ueberpruefbar "
            "formuliert oder nur ambitioniert? Traegt der Arbeitsplan das "
            "Ziel, und ist er in der Laufzeit zu schaffen? Was ist das "
            "groesste Risiko, und steht ein Plan dafuer da?\n"
            "- Benenne, was ein Gutachter bemaengeln wuerde, und schlage eine "
            "konkrete Formulierung vor - nicht nur die Kritik.\n"
        ),
        mit_rechnen=(
            "- Rechne den Plan nach: Personenmonate gegen Arbeitspakete, "
            "Summen gegen Einzelposten, Zeitplan gegen Laufzeit, "
            "Stellenanteile gegen das, was die Arbeitspakete verlangen. Ein "
            "Antrag scheitert oft an einer Tabelle, die nicht aufgeht.\n"
        ),
    ),

    "code": Pruefart(
        schluessel="code",
        auftrag=(
            "- Lies den Code wie in einem Review und geh auf konkrete Zeilen: "
            "Tut er, was er verspricht? Welche Randfaelle fallen durch - "
            "leere Eingabe, Null, negative Werte, Nebenlaeufigkeit, "
            "Fehlerpfade? Was passiert, wenn eine Annahme nicht gilt?\n"
            "- Schlage Verbesserungen als Code vor, in einem Block mit "
            "Sprachangabe. Der wird nicht ausgefuehrt, er ist zum Lesen da.\n"
        ),
        mit_rechnen=(
            "- Willst du eine Annahme wirklich pruefen, schreibe dafuer einen "
            "eigenen ```rechnen-Block mit einem kleinen, eigenstaendigen "
            "Beispiel. Der besprochene Code selbst wird nie ausgefuehrt.\n"
        ),
    ),
}

STANDARD = "frei"


def waehlen(schluessel: str | None) -> Pruefart:
    """Nie stolpern: eine unbekannte oder fehlende Art ist eine freie."""
    return ARTEN.get(schluessel or STANDARD, ARTEN[STANDARD])

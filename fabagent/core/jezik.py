"""Jezik sucelja: bosanski (izvorni) i engleski (US).

Prevodi se PO IZVORNOM TEKSTU, ne po sifri. Razlog je jednostavan: sabloni
ostaju citljivi na nasem jeziku, a engleski je samo pretraga u recniku. Kad
prijevoda nema, vraca se izvorni tekst - stranica nikad ne ostane prazna i
nikad ne pukne zbog jedne recenice koja jos nije prevedena.

Jezik se bira sa ?lang=en i pamti u kolacicu. Podrazumijevani ostaje bosanski,
pa postojeci rad nije dirnut.
"""

from __future__ import annotations

PODRAZUMIJEVANI = "bs"
JEZICI = {"bs": "Bosanski", "en": "English"}

# ---------------------------------------------------------------- recnik
# Kljuc je tacan izvorni tekst iz sablona. Ako se izvorni tekst promijeni,
# prijevod prestaje da se poklapa i vraca se bosanski - vidi se odmah, bez pada.
EN: dict[str, str] = {
    # --- navigacija i zaglavlje
    "Radni ekran": "Workspace",
    "Šabloni": "Templates",
    "Razgovor": "Assistant",
    "Upload": "Upload",
    "Kamera": "Camera",
    "Laser": "Laser",
    "Istorija": "History",
    "sve": "all",
    "radi lokalno, bez interneta": "runs locally, no internet",
    "kod": "build",
    "Još nema buildova.": "No jobs yet.",

    # --- razgovor
    "Razgovor sa asistentom": "Talk to the assistant",
    "Poznato o poslu": "What the job says",
    "Postavke mašine": "Machine settings",
    "Ovo zaustavlja posao": "This blocks the job",
    "Mora se znati prije starta": "Must be known before start",
    "Pošalji": "Send",
    "Napravi posao": "Build the job",
    "Fajl:": "File:",
    "nije priložen": "not attached",
    "Čeka se:": "Waiting for:",
    "Nalog:": "Order:",
    "Faza:": "Stage:",
    "Mogu te pitati o:": "You can ask me about:",
    "Još ništa nije pročitano iz naloga.": "Nothing read from the order yet.",
    "Zadnji napravljen posao:": "Last job built:",
    "Otvori na laseru": "Open on the laser",
    "Pregled": "Preview",
    "Radna površina": "Work area",
    "kalibracija": "calibration",

    # --- laser / masina
    "Upravljanje laserom (GRBL, Web Serial)": "Laser control (GRBL, Web Serial)",
    "Veza": "Connection",
    "Poveži se sa laserom": "Connect to the laser",
    "Prekini vezu": "Disconnect",
    "Otkopčano": "Disconnected",
    "Povezano": "Connected",
    "Kontrole": "Controls",
    "Otključaj ($X)": "Unlock ($X)",
    "Frame": "Frame",
    "Start": "Start",
    "Stop": "Stop",
    "Konzola": "Console",
    "očisti": "clear",
    "Pozicija na stolu X": "Position on bed X",
    "Pregled posla": "Job preview",
    "Novi posao u razgovoru": "New job in the assistant",
    "Sigurnost:": "Safety:",

    # --- upload
    "Uploaduj fajl (SVG, DXF, PDF, PNG, JPG)": "Upload a file (SVG, DXF, PDF, PNG, JPG)",
    "Fajl se obrađuje lokalno na ovom računaru. Ništa se ne šalje na internet.":
        "The file is processed locally on this computer. Nothing is sent to the internet.",
    "Materijal": "Material",
    "Pretvori, provjeri i popravi": "Convert, check and repair",
    "Šta se automatski radi": "What happens automatically",
    "Šta se ne pogađa": "What is never guessed",

    # --- posao i rezultat
    "Dimenzije": "Size",
    "Procijenjeno vrijeme": "Estimated time",
    "Udio piksela sa laserom": "Pixels with laser on",
    "Upozorenja": "Warnings",
    "Odobri i pošalji u produkciju": "Approve and send to production",
    "Odobreno i poslano u produkciju.": "Approved and sent to production.",
    "Pošalji na laser": "Send to the laser",
    "Otvori u editoru": "Open in the editor",
    "Radni nalog": "Work order",
    "Nazad na posao": "Back to the job",
    "Fajlovi:": "Files:",
    "Prije nego pokreneš": "Before you start",

    # --- opste
    "Materijal:": "Material:",
    "Debljina materijala": "Material thickness",
    "Velicina na materijalu": "Size on the material",
    "Rezolucija": "Resolution",
    "Razmak linija": "Line interval",
    "Posao": "Job",
    "Izvor": "Source",
    "obavezno polje": "required field",
    "Dozvoljeno:": "Allowed:",
}

RECNICI = {"en": EN}


def ocisti(oznaka: str | None) -> str:
    """Vrati podrzan jezik; sve nepoznato pada na bosanski."""
    o = (oznaka or "").strip().lower()[:5]
    if o in JEZICI:
        return o
    o2 = o.split("-")[0]
    return o2 if o2 in JEZICI else PODRAZUMIJEVANI


def prevedi(tekst: str, jezik: str = PODRAZUMIJEVANI) -> str:
    """Prijevod po izvornom tekstu; kad ga nema, vraca izvorni tekst."""
    if jezik == PODRAZUMIJEVANI:
        return tekst
    return RECNICI.get(jezik, {}).get(tekst, tekst)


def prevodilac(jezik: str):
    """Funkcija za sablone: {{ t("Razgovor") }}."""
    j = ocisti(jezik)

    def t(tekst: str) -> str:
        return prevedi(tekst, j)

    return t


def nedostaju(jezik: str, tekstovi) -> list[str]:
    """Koji od datih tekstova jos nemaju prijevod - za provjeru, ne za prikaz."""
    r = RECNICI.get(ocisti(jezik), {})
    return [t for t in tekstovi if t not in r]

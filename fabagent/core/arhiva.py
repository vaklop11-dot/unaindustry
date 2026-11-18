"""Citanje ZIP arhiva sa diska - bez raspakivanja i bez kopiranja.

Zasto postoji: paketi crteza stizu kao arhive od 30 MB do 15 GB. Kroz formu za
upload ne mogu proci (granica je 10 MB), a raspakivanje bi zauzelo desetine
gigabajta i zatrpalo disk. ZIP na kraju nosi spisak svega sto je u njemu, pa se
taj spisak moze procitati za djelic sekunde i na arhivi od 15 GB - cita se samo
nekoliko kilobajta sa kraja fajla, ne cijela arhiva.

Jedan crtez se vadi tek kad ga operater izabere, u memoriju, i odmah ide kroz
isti tok kao i da ga je priloziti rukom. Na disk se ne pise nista.

RAR se ne cita: za njega nema nicega u standardnoj biblioteci, a dovlacenje
tudje biblioteke je tacno ono sto smo upravo izbacili zbog licenci.
"""

from __future__ import annotations

import tarfile
import zipfile
from pathlib import Path

from fabagent.core import config as C
from fabagent.core.errors import InvalidParam, OutputError

# Arhive koje umijemo otvoriti, i one koje ne umijemo ali ih treba imenovati.
ZIP_EXT = (".zip",)
# TAR i njegove kompresije cita standardna biblioteka (modul tarfile), pa za
# njih ne treba nijedna tudja biblioteka ni licenca.
TAR_EXT = (".tar", ".tgz", ".tbz", ".tbz2", ".txz")
TAR_SLOZENI = (".tar.gz", ".tar.bz2", ".tar.xz")
# RAR je zatvoren format (RARLAB), a 7z trazi biblioteku pod copyleft licencom.
# Zato se oni ne otvaraju - prepakivanje u .zip je jedini cist put.
NEPODRZANE_ARHIVE = (".rar", ".7z")


def _vrsta(p: Path) -> str:
    """'zip' | 'tar' | '' - po imenu fajla, ne po sadrzaju."""
    ime = p.name.lower()
    if ime.endswith(ZIP_EXT):
        return "zip"
    if ime.endswith(TAR_SLOZENI) or ime.endswith(TAR_EXT) or ime.endswith((".gz", ".bz2", ".xz")):
        return "tar"
    return ""

# Najveci pojedinacni crtez koji se vadi iz arhive. Ista granica kao za upload:
# ako nesto ne bi proslo kroz formu, nema razloga da prodje ni ovuda.
MAX_UNOS_BAJTOVA = C.UPLOAD_MAX_BYTES


def _provjeri_putanju(putanja: str | Path) -> Path:
    p = Path(putanja).expanduser()
    if not p.is_absolute():
        raise InvalidParam("putanja: treba puna putanja do fajla, npr. C:\\Users\\...\\paket.zip")
    if not p.exists():
        raise InvalidParam(f"putanja: '{p}' ne postoji")
    return p


def arhive_u_folderu(folder: str | Path, dubina: int = 1) -> list[dict]:
    """Spisak ZIP arhiva u folderu. Vraca ime, punu putanju i velicinu.

    Dubina 1 znaci samo taj folder; 2 i njegove podfoldere, i tako dalje. Bez
    ogranicenja bi pretraga cijelog diska trajala minutama.
    """
    p = Path(folder).expanduser()
    if not p.is_dir():
        raise InvalidParam(f"folder: '{p}' nije folder")
    out = []
    maske = ["*.zip", "*.tar", "*.tgz", "*.tbz", "*.tbz2", "*.txz",
             "*.tar.gz", "*.tar.bz2", "*.tar.xz"]
    vidjeno = set()
    for nivo in range(dubina):
        for maska in maske:
            obrazac = "/".join(["*"] * nivo + [maska]) if nivo else maska
            for f in p.glob(obrazac):
                if str(f) in vidjeno:
                    continue
                vidjeno.add(str(f))
                try:
                    out.append({"ime": f.name, "putanja": str(f), "bajtova": f.stat().st_size})
                except OSError:
                    continue
    out.sort(key=lambda x: x["ime"].lower())
    return out


def spisak(zip_putanja: str | Path, filter_teksta: str = "") -> dict:
    """Sta je u arhivi. Cita se samo spisak, ne i sadrzaj.

    Vraca {"unosi": [...], "ukupno": n, "podrzanih": n, "preskoceno": n}
    gdje je svaki unos {"ime", "bajtova", "ext"}.
    """
    from fabagent.core.converter import SUPPORTED_EXT

    p = _provjeri_putanju(zip_putanja)
    if p.suffix.lower() in NEPODRZANE_ARHIVE:
        raise InvalidParam(f"arhiva: '{p.suffix}' se ne moze otvoriti ovdje - "
                           f"prepakuj je u .zip pa pokusaj ponovo")
    vrsta = _vrsta(p)
    if not vrsta:
        raise InvalidParam(f"arhiva: '{p.suffix}' nije arhiva koju citam "
                           f"(idu .zip, .tar, .tar.gz, .tgz, .tar.bz2, .tar.xz)")

    try:
        if vrsta == "zip":
            with zipfile.ZipFile(p) as z:
                svi = [(i.filename, i.file_size, i.is_dir()) for i in z.infolist()]
        else:
            with tarfile.open(p, "r:*") as t:
                svi = [(m.name, m.size, not m.isfile()) for m in t.getmembers()]
    except (zipfile.BadZipFile, tarfile.TarError) as exc:
        raise InvalidParam(f"arhiva: '{p.name}' je ostecena ili nije arhiva ({exc})") from None
    except OSError as exc:
        raise InvalidParam(f"arhiva: ne mogu otvoriti '{p.name}' ({exc})") from None

    trazi = (filter_teksta or "").strip().lower()
    unosi = []
    podrzanih = 0
    for ime_u, velicina, je_folder in svi:
        if je_folder:
            continue
        ext = Path(ime_u).suffix.lower()
        if ext not in SUPPORTED_EXT:
            continue
        podrzanih += 1
        if trazi and trazi not in ime_u.lower():
            continue
        unosi.append({"ime": ime_u, "bajtova": velicina, "ext": ext})

    unosi.sort(key=lambda x: x["ime"].lower())
    return {"unosi": unosi, "ukupno": len(svi), "podrzanih": podrzanih,
            "preskoceno": len(svi) - podrzanih, "arhiva": p.name, "putanja": str(p)}


def izvadi(zip_putanja: str | Path, ime: str) -> tuple[str, bytes]:
    """Izvadi jedan crtez iz arhive u memoriju. Vraca (ime fajla, bajtovi).

    Ime se uzima iz spiska same arhive - ne gradi se od onoga sto je stiglo sa
    strane. Tako putanja tipa '../../nesto' ne moze proci, jer takvog imena u
    spisku nema.
    """
    p = _provjeri_putanju(zip_putanja)
    mb = MAX_UNOS_BAJTOVA // (1024 * 1024)
    try:
        if _vrsta(p) == "tar":
            with tarfile.open(p, "r:*") as t:
                try:
                    clan = t.getmember(ime)
                except KeyError:
                    raise InvalidParam(f"u arhivi '{p.name}' nema fajla '{ime}'") from None
                if not clan.isfile():
                    raise InvalidParam(f"'{ime}' nije obican fajl")
                if clan.size > MAX_UNOS_BAJTOVA:
                    raise InvalidParam(f"'{ime}' je {clan.size / 1024 / 1024:.1f} MB, "
                                       f"a granica je {mb} MB")
                tok = t.extractfile(clan)
                if tok is None:
                    raise InvalidParam(f"'{ime}' se ne moze procitati iz arhive")
                return Path(clan.name).name, tok.read()
        with zipfile.ZipFile(p) as z:
            try:
                info = z.getinfo(ime)
            except KeyError:
                raise InvalidParam(f"u arhivi '{p.name}' nema fajla '{ime}'") from None
            if info.is_dir():
                raise InvalidParam(f"'{ime}' je folder, ne fajl")
            if info.file_size > MAX_UNOS_BAJTOVA:
                raise InvalidParam(f"'{ime}' je {info.file_size / 1024 / 1024:.1f} MB, "
                                   f"a granica je {mb} MB")
            data = z.read(info)
    except (zipfile.BadZipFile, tarfile.TarError) as exc:
        raise InvalidParam(f"arhiva: '{p.name}' je ostecena ({exc})") from None
    except OSError as exc:
        raise OutputError(f"ne mogu procitati iz arhive: {exc}") from None
    return Path(info.filename).name, data

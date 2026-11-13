"""Code 128 (set B) enkoder: tekst -> sirine barova/razmaka u modulima."""

from __future__ import annotations

from fabagent.core import config as C
from fabagent.core.errors import InvalidParam

# Sirine elemenata (bar, razmak, bar, ...) za vrijednosti 0..106.
PATTERNS = (
    "212222", "222122", "222221", "121223", "121322", "131222", "122213", "122312", "132212",
    "221213", "221312", "231212", "112232", "122132", "122231", "113222", "123122", "123221",
    "223211", "221132", "221231", "213212", "223112", "312131", "311222", "321122", "321221",
    "312212", "322112", "322211", "212123", "212321", "232121", "111323", "131123", "131321",
    "112313", "132113", "132311", "211313", "231113", "231311", "112133", "112331", "132131",
    "113123", "113321", "133121", "313121", "211331", "231131", "213113", "213311", "213131",
    "311123", "311321", "331121", "312113", "312311", "332111", "314111", "221411", "431111",
    "111224", "111422", "121124", "121421", "141122", "141221", "112214", "112412", "122114",
    "122411", "142112", "142211", "241211", "221114", "413111", "241112", "134111", "111242",
    "121142", "121241", "114212", "124112", "124211", "411212", "421112", "421211", "212141",
    "214121", "412121", "111143", "111341", "131141", "114113", "114311", "411113", "411311",
    "113141", "114131", "311141", "411131", "211412", "211214", "211232", "2331112",
)
START_B = 104
STOP = 106
CHECKSUM_MOD = 103
SET_B_OFFSET = 32


def encode_values(text: str) -> list[int]:
    bad = sorted({c for c in text if c not in C.ASCII_PRINTABLE})
    if bad:
        raise InvalidParam(f"Code 128-B ne podrzava znakove {bad!r}")
    values = [START_B] + [ord(c) - SET_B_OFFSET for c in text]
    checksum = START_B
    for i, v in enumerate(values[1:], start=1):
        checksum += i * v
    values.append(checksum % CHECKSUM_MOD)
    values.append(STOP)
    return values


def encode_widths(text: str) -> list[int]:
    """Lista sirina (u modulima), naizmjenicno bar/razmak, pocinje barom."""
    widths: list[int] = []
    for v in encode_values(text):
        widths.extend(int(c) for c in PATTERNS[v])
    return widths


def bars(text: str) -> tuple[list[tuple[int, int]], int]:
    """Vraca ([(start_modul, sirina_modula) za svaki bar], ukupna_sirina_modula)."""
    pos = 0
    result = []
    for i, w in enumerate(encode_widths(text)):
        if i % 2 == 0:
            result.append((pos, w))
        pos += w
    return result, pos

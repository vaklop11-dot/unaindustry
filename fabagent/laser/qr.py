"""QR kod enkoder (ISO/IEC 18004), byte mod, verzije 1-40, nivoi L/M/Q/H.

Cisti Python, bez zavisnosti. Bira najmanju verziju u koju sadrzaj stane i masku sa
najmanjim penalom (standardna pravila N1-N4).
"""

from __future__ import annotations

import numpy as np

from fabagent.core import config as C
from fabagent.core.errors import InvalidParam

# Tabele iz standarda (indeks = verzija; indeks 0 se ne koristi).
ECC_CODEWORDS_PER_BLOCK = {
    "L": (-1, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30, 28, 28,
          28, 28, 30, 30, 26, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
    "M": (-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26,
          26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28),
    "Q": (-1, 13, 22, 18, 26, 18, 24, 18, 22, 20, 24, 28, 26, 24, 20, 30, 24, 28, 28, 26, 30,
          28, 30, 30, 30, 30, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
    "H": (-1, 17, 28, 22, 16, 22, 28, 26, 26, 24, 28, 24, 28, 22, 24, 24, 30, 28, 28, 26, 28,
          30, 24, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
}
NUM_ECC_BLOCKS = {
    "L": (-1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4, 4, 4, 4, 4, 6, 6, 6, 6, 7, 8,
          8, 9, 9, 10, 12, 12, 12, 13, 14, 15, 16, 17, 18, 19, 19, 20, 21, 22, 24, 25),
    "M": (-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16,
          17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49),
    "Q": (-1, 1, 1, 2, 2, 4, 4, 6, 6, 8, 8, 8, 10, 12, 16, 12, 17, 16, 18, 21, 20,
          23, 23, 25, 27, 29, 34, 34, 35, 38, 40, 43, 45, 48, 51, 53, 56, 59, 62, 65, 68),
    "H": (-1, 1, 1, 2, 4, 4, 4, 5, 6, 8, 8, 11, 11, 16, 16, 18, 16, 19, 21, 25, 25,
          25, 34, 30, 32, 35, 37, 40, 42, 45, 48, 51, 54, 57, 60, 63, 66, 70, 74, 77, 81),
}
FORMAT_BITS = {"L": 1, "M": 0, "Q": 3, "H": 2}
PENALTY_N1, PENALTY_N2, PENALTY_N3, PENALTY_N4 = 3, 3, 40, 10
GF_POLY = 0x11D
FORMAT_GEN, FORMAT_MASK = 0x537, 0x5412
VERSION_GEN = 0x1F25


# ------------------------------------------------------------ GF(256) / RS
def gf_mul(x: int, y: int) -> int:
    z = 0
    for i in reversed(range(8)):
        z = (z << 1) ^ ((z >> 7) * GF_POLY)
        z ^= ((y >> i) & 1) * x
    return z & 0xFF


def rs_divisor(degree: int) -> list[int]:
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = gf_mul(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = gf_mul(root, 0x02)
    return result


def rs_remainder(data: list[int], divisor: list[int]) -> list[int]:
    result = [0] * len(divisor)
    for b in data:
        factor = b ^ result.pop(0)
        result.append(0)
        for i, coef in enumerate(divisor):
            result[i] ^= gf_mul(coef, factor)
    return result


def rs_syndromes_zero(codeword: list[int], ecc_len: int) -> bool:
    """Provjera: polinom kodne rijeci je djeljiv generatorom (svi sindromi 0)."""
    for i in range(ecc_len):
        alpha_i = 1
        for _ in range(i):
            alpha_i = gf_mul(alpha_i, 2)
        acc = 0
        for c in codeword:
            acc = gf_mul(acc, alpha_i) ^ c
        if acc != 0:
            return False
    return True


# ------------------------------------------------------------ kapacitet
def num_raw_data_modules(ver: int) -> int:
    result = (16 * ver + 128) * ver + 64
    if ver >= 2:
        numalign = ver // 7 + 2
        result -= (25 * numalign - 10) * numalign - 55
        if ver >= 7:
            result -= 36
    return result


def num_data_codewords(ver: int, ecl: str) -> int:
    return num_raw_data_modules(ver) // 8 - ECC_CODEWORDS_PER_BLOCK[ecl][ver] * NUM_ECC_BLOCKS[ecl][ver]


def _char_count_bits(ver: int) -> int:
    return 8 if ver <= 9 else 16


def byte_capacity(ver: int, ecl: str) -> int:
    return (num_data_codewords(ver, ecl) * 8 - 4 - _char_count_bits(ver)) // 8


def alignment_positions(ver: int) -> list[int]:
    if ver == 1:
        return []
    numalign = ver // 7 + 2
    size = ver * 4 + 17
    step = (ver * 8 + numalign * 3 + 5) // (numalign * 4 - 4) * 2
    result = [size - 7 - i * step for i in range(numalign - 1)] + [6]
    return list(reversed(result))


def format_bits(ecl: str, mask: int) -> int:
    data = FORMAT_BITS[ecl] << 3 | mask
    rem = data
    for _ in range(10):
        rem = (rem << 1) ^ ((rem >> 9) * FORMAT_GEN)
    return (data << 10 | rem) ^ FORMAT_MASK


def version_bits(ver: int) -> int:
    rem = ver
    for _ in range(12):
        rem = (rem << 1) ^ ((rem >> 11) * VERSION_GEN)
    return ver << 12 | rem


# ------------------------------------------------------------ enkoder
class QrCode:
    def __init__(self, data: bytes, ecl: str):
        if ecl not in FORMAT_BITS:
            raise InvalidParam(f"nepoznat ECC nivo {ecl}")
        self.ecl = ecl
        self.version = self._pick_version(len(data), ecl)
        self.size = self.version * 4 + 17
        self.modules = np.zeros((self.size, self.size), dtype=bool)   # [y, x]
        self.is_function = np.zeros((self.size, self.size), dtype=bool)
        self.data_codewords: list[int] = []
        self.blocks: list[list[int]] = []
        self._draw_function_patterns()
        codewords = self._add_ecc_and_interleave(self._encode(data))
        self._draw_codewords(codewords)
        best_mask, best_penalty = 0, None
        for mask in range(8):
            self._apply_mask(mask)
            self._draw_format_bits(mask)
            penalty = self._penalty()
            if best_penalty is None or penalty < best_penalty:
                best_mask, best_penalty = mask, penalty
            self._apply_mask(mask)  # XOR ponovo = ponisti
        self.mask = best_mask
        self._apply_mask(best_mask)
        self._draw_format_bits(best_mask)

    @staticmethod
    def _pick_version(n: int, ecl: str) -> int:
        for ver in range(C.QR_MIN_VERSION, C.QR_MAX_VERSION + 1):
            if n <= byte_capacity(ver, ecl):
                return ver
        raise InvalidParam(f"sadrzaj od {n} bajtova ne stane u QR (ECC {ecl}, max verzija {C.QR_MAX_VERSION})")

    # --- bitovi podataka
    def _encode(self, data: bytes) -> list[int]:
        bits: list[int] = []

        def put(val: int, n: int):
            bits.extend((val >> i) & 1 for i in reversed(range(n)))

        put(0b0100, 4)
        put(len(data), _char_count_bits(self.version))
        for b in data:
            put(b, 8)
        capacity = num_data_codewords(self.version, self.ecl) * 8
        put(0, min(4, capacity - len(bits)))
        put(0, (-len(bits)) % 8)
        pad = 0xEC
        while len(bits) < capacity:
            put(pad, 8)
            pad ^= 0xEC ^ 0x11
        words = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
        self.data_codewords = words
        return words

    def _add_ecc_and_interleave(self, data: list[int]) -> list[int]:
        ver, ecl = self.version, self.ecl
        numblocks = NUM_ECC_BLOCKS[ecl][ver]
        blockecclen = ECC_CODEWORDS_PER_BLOCK[ecl][ver]
        rawcodewords = num_raw_data_modules(ver) // 8
        numshort = numblocks - rawcodewords % numblocks
        shortlen = rawcodewords // numblocks
        divisor = rs_divisor(blockecclen)
        blocks = []
        k = 0
        for i in range(numblocks):
            datlen = shortlen - blockecclen + (0 if i < numshort else 1)
            dat = data[k:k + datlen]
            k += datlen
            ecc = rs_remainder(dat, divisor)
            self.blocks.append(dat + ecc)
            if i < numshort:
                dat = dat + [0]
            blocks.append(dat + ecc)
        result = []
        for i in range(len(blocks[0])):
            for j, blk in enumerate(blocks):
                if i != shortlen - blockecclen or j >= numshort:
                    result.append(blk[i])
        return result

    # --- crtanje
    def _set_function(self, x: int, y: int, dark: bool):
        self.modules[y, x] = dark
        self.is_function[y, x] = True

    def _draw_function_patterns(self):
        size = self.size
        for i in range(size):
            self._set_function(6, i, i % 2 == 0)
            self._set_function(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < size and 0 <= y < size:
                        self._set_function(x, y, max(abs(dx), abs(dy)) not in (2, 4))
        pos = alignment_positions(self.version)
        last = len(pos) - 1
        for i, ax in enumerate(pos):
            for j, ay in enumerate(pos):
                if (i == 0 and j == 0) or (i == 0 and j == last) or (i == last and j == 0):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self._set_function(ax + dx, ay + dy, max(abs(dx), abs(dy)) != 1)
        self._draw_format_bits(0)  # rezervise mjesta
        if self.version >= 7:
            bits = version_bits(self.version)
            for i in range(18):
                bit = (bits >> i) & 1 == 1
                a, b = size - 11 + i % 3, i // 3
                self._set_function(a, b, bit)
                self._set_function(b, a, bit)

    def _draw_format_bits(self, mask: int):
        bits = format_bits(self.ecl, mask)
        size = self.size

        def bit(i):
            return (bits >> i) & 1 == 1

        for i in range(6):
            self._set_function(8, i, bit(i))
        self._set_function(8, 7, bit(6))
        self._set_function(8, 8, bit(7))
        self._set_function(7, 8, bit(8))
        for i in range(9, 15):
            self._set_function(14 - i, 8, bit(i))
        for i in range(8):
            self._set_function(size - 1 - i, 8, bit(i))
        for i in range(8, 15):
            self._set_function(8, size - 15 + i, bit(i))
        self._set_function(8, size - 8, True)

    def _draw_codewords(self, data: list[int]):
        size = self.size
        i = 0
        right = size - 1
        while right >= 1:
            if right == 6:
                right = 5
            for vert in range(size):
                for j in range(2):
                    x = right - j
                    upward = ((right + 1) & 2) == 0
                    y = (size - 1 - vert) if upward else vert
                    if not self.is_function[y, x] and i < len(data) * 8:
                        self.modules[y, x] = ((data[i >> 3] >> (7 - (i & 7))) & 1) == 1
                        i += 1
            right -= 2

    def _apply_mask(self, mask: int):
        y, x = np.mgrid[0:self.size, 0:self.size]
        if mask == 0:
            m = (x + y) % 2 == 0
        elif mask == 1:
            m = y % 2 == 0
        elif mask == 2:
            m = x % 3 == 0
        elif mask == 3:
            m = (x + y) % 3 == 0
        elif mask == 4:
            m = (x // 3 + y // 2) % 2 == 0
        elif mask == 5:
            m = (x * y % 2 + x * y % 3) == 0
        elif mask == 6:
            m = (x * y % 2 + x * y % 3) % 2 == 0
        else:
            m = ((x + y) % 2 + x * y % 3) % 2 == 0
        self.modules ^= m & ~self.is_function

    def _penalty(self) -> int:
        mods = self.modules
        size = self.size
        result = 0
        for grid in (mods, mods.T):
            for row in grid:
                run_color, run_len = row[0], 1
                for v in row[1:]:
                    if v == run_color:
                        run_len += 1
                    else:
                        if run_len >= 5:
                            result += PENALTY_N1 + run_len - 5
                        run_color, run_len = v, 1
                if run_len >= 5:
                    result += PENALTY_N1 + run_len - 5
                # N3: uzorak nalik tragacu sa 4 svijetla modula sa strane (izvan = svijetlo)
                padded = np.concatenate([np.zeros(4, bool), row, np.zeros(4, bool)])
                s = "".join("1" if b else "0" for b in padded)
                for pat in ("00001011101", "10111010000"):
                    start = s.find(pat)
                    while start != -1:
                        result += PENALTY_N3
                        start = s.find(pat, start + 1)
        same = (mods[:-1, :-1] == mods[1:, :-1]) & (mods[:-1, :-1] == mods[:-1, 1:]) & \
               (mods[:-1, :-1] == mods[1:, 1:])
        result += PENALTY_N2 * int(same.sum())
        dark = int(mods.sum())
        total = size * size
        k = (abs(dark * 20 - total * 10) + total - 1) // total - 1
        result += k * PENALTY_N4
        return result


def encode(text: str, ecl: str) -> QrCode:
    """Enkodira ASCII tekst. Ne-ASCII znakovi su greska (nema tihe konverzije)."""
    try:
        data = text.encode("ascii")
    except UnicodeEncodeError as exc:
        raise InvalidParam("QR sadrzaj mora biti ASCII") from exc
    return QrCode(data, ecl)


def decode_format(qr: QrCode) -> tuple[str, int]:
    """Cita format bitove iz matrice (prva kopija) - za provjeru."""
    m = qr.modules
    bits = 0
    for i in range(6):
        bits |= int(m[i, 8]) << i
    bits |= int(m[7, 8]) << 6
    bits |= int(m[8, 8]) << 7
    bits |= int(m[8, 7]) << 8
    for i in range(9, 15):
        bits |= int(m[8, 14 - i]) << i
    for ecl in FORMAT_BITS:
        for mask in range(8):
            if format_bits(ecl, mask) == bits:
                return ecl, mask
    raise ValueError("format bitovi nisu validni")

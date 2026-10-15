"""
BAĞIMLILIKSIZ PNG GRAFİK — Trello kart kapakları için yığılmış (stacked) günlük çubuk grafik.
Vercel'de Pillow/matplotlib yok (paket boyutu); bu modül saf Python (zlib + struct) ile PNG üretir.
Grafikte yalnızca rakam ve tarih yazılır (küçük bit eşlem yazı tipi); seri adları (renk açıklaması) kart metninde.
"""
import struct
import zlib

# 3×5 bit eşlem yazı tipi — yalnızca eksen etiketleri için gerekenler
_FONT = {
    "0": ("111", "101", "101", "101", "111"), "1": ("010", "110", "010", "010", "111"),
    "2": ("111", "001", "111", "100", "111"), "3": ("111", "001", "111", "001", "111"),
    "4": ("101", "101", "111", "001", "001"), "5": ("111", "100", "111", "001", "111"),
    "6": ("111", "100", "111", "101", "111"), "7": ("111", "001", "010", "010", "010"),
    "8": ("111", "101", "111", "101", "111"), "9": ("111", "101", "111", "001", "111"),
    ".": ("000", "000", "000", "000", "010"), "-": ("000", "000", "111", "000", "000"),
    "/": ("001", "001", "010", "100", "100"), " ": ("000", "000", "000", "000", "000"),
}

BG = (255, 255, 255)
GRID = (226, 232, 240)
AXIS = (148, 163, 184)
TEXT = (71, 85, 105)


class Canvas:
    def __init__(self, w: int, h: int, bg=BG):
        self.w, self.h = w, h
        self.px = bytearray(bytes(bg) * (w * h))

    def rect(self, x0: int, y0: int, x1: int, y1: int, color):
        x0, x1 = max(0, min(x0, x1)), min(self.w, max(x0, x1))
        y0, y1 = max(0, min(y0, y1)), min(self.h, max(y0, y1))
        if x0 >= x1 or y0 >= y1:
            return
        row = bytes(color) * (x1 - x0)
        for y in range(y0, y1):
            i = (y * self.w + x0) * 3
            self.px[i:i + len(row)] = row

    def text(self, x: int, y: int, s: str, color=TEXT, scale: int = 2):
        for ch in s:
            g = _FONT.get(ch)
            if g:
                for gy, line in enumerate(g):
                    for gx, bit in enumerate(line):
                        if bit == "1":
                            self.rect(x + gx * scale, y + gy * scale, x + (gx + 1) * scale, y + (gy + 1) * scale, color)
            x += 4 * scale

    def png(self) -> bytes:
        raw = b"".join(b"\x00" + bytes(self.px[y * self.w * 3:(y + 1) * self.w * 3]) for y in range(self.h))

        def chunk(tag: bytes, data: bytes) -> bytes:
            return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", self.w, self.h, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def text_width(s: str, scale: int = 2) -> int:
    return len(s) * 4 * scale - scale


def stacked_bars(labels: list[str], series: list[tuple[tuple, list[int]]], width: int = 960, height: int = 340) -> bytes:
    """labels: gün etiketleri (ör. "06.10"); series: [(renk, değerler)] — aynı uzunlukta, alttan üste yığılır."""
    n = max(1, len(labels))
    totals = [sum(s[1][i] for s in series) for i in range(n)]
    top = max(totals) if totals else 0
    ymax = max(1, top)
    if ymax > 4:   # ızgarayı okunur tam sayılara yuvarla
        step = max(1, -(-ymax // 4))
        ymax = step * 4
    left, right, tpad, bottom = 56, 16, 16, 34
    c = Canvas(width, height)
    pw, ph = width - left - right, height - tpad - bottom
    y_of = lambda v: tpad + ph - round(ph * v / ymax)           # noqa: E731
    for k in range(5):
        v = round(ymax * k / 4)
        y = y_of(v)
        c.rect(left, y, left + pw, y + 1, GRID if k else AXIS)
        lab = str(v)
        c.text(left - 8 - text_width(lab), y - 5, lab)
    slot = pw / n
    bw = max(2, int(slot * 0.7))
    for i in range(n):
        x0 = left + int(i * slot + (slot - bw) / 2)
        base = 0
        for color, vals in series:
            v = vals[i] if i < len(vals) else 0
            if v:
                c.rect(x0, y_of(base + v), x0 + bw, y_of(base), color)
                base += v
    every = max(1, -(-n * 52 // pw))   # etiketler üst üste binmesin (~52 px/etiket)
    for i in range(0, n, every):
        lab = labels[i]
        cx = left + int(i * slot + slot / 2)
        c.text(cx - text_width(lab) // 2, tpad + ph + 10, lab)
    return c.png()

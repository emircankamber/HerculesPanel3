"""
BAĞIMLILIKSIZ PNG ÇİZİMİ — Trello kart kapakları için performans panosu görselleri.
Vercel'de Pillow/matplotlib yok (paket boyutu); bu modül saf Python (zlib + struct) ile PNG üretir.

Yazı: 5×7 bit eşlem yazı tipi — BÜYÜK harf (Türkçe Ç Ğ İ Ö Ş Ü dahil), rakam ve temel noktalama. Metin `up()` ile
Türkçe kurallarına göre büyütülür (i → İ, ı → I); tanınmayan harfler aksanı atılarak ya da "?" ile çizilir.
"""
import struct
import unicodedata
import zlib

# --- 5×7 yazı tipi: 7 satır × 5 sütun; Türkçe işaretler kutunun üstüne (ÜST) ya da altına (ALT) çizilir ---
_G = {
    "A": "01110 10001 10001 11111 10001 10001 10001", "B": "11110 10001 10001 11110 10001 10001 11110",
    "C": "01110 10001 10000 10000 10000 10001 01110", "D": "11110 10001 10001 10001 10001 10001 11110",
    "E": "11111 10000 10000 11110 10000 10000 11111", "F": "11111 10000 10000 11110 10000 10000 10000",
    "G": "01110 10001 10000 10111 10001 10001 01111", "H": "10001 10001 10001 11111 10001 10001 10001",
    "I": "01110 00100 00100 00100 00100 00100 01110", "J": "00111 00010 00010 00010 00010 10010 01100",
    "K": "10001 10010 10100 11000 10100 10010 10001", "L": "10000 10000 10000 10000 10000 10000 11111",
    "M": "10001 11011 10101 10101 10001 10001 10001", "N": "10001 10001 11001 10101 10011 10001 10001",
    "O": "01110 10001 10001 10001 10001 10001 01110", "P": "11110 10001 10001 11110 10000 10000 10000",
    "Q": "01110 10001 10001 10001 10101 10010 01101", "R": "11110 10001 10001 11110 10100 10010 10001",
    "S": "01111 10000 10000 01110 00001 00001 11110", "T": "11111 00100 00100 00100 00100 00100 00100",
    "U": "10001 10001 10001 10001 10001 10001 01110", "V": "10001 10001 10001 10001 10001 01010 00100",
    "W": "10001 10001 10001 10101 10101 10101 01010", "X": "10001 10001 01010 00100 01010 10001 10001",
    "Y": "10001 10001 01010 00100 00100 00100 00100", "Z": "11111 00001 00010 00100 01000 10000 11111",
    "0": "01110 10001 10011 10101 11001 10001 01110", "1": "00100 01100 00100 00100 00100 00100 01110",
    "2": "01110 10001 00001 00010 00100 01000 11111", "3": "11110 00001 00001 01110 00001 00001 11110",
    "4": "00010 00110 01010 10010 11111 00010 00010", "5": "11111 10000 11110 00001 00001 10001 01110",
    "6": "00110 01000 10000 11110 10001 10001 01110", "7": "11111 00001 00010 00100 01000 01000 01000",
    "8": "01110 10001 10001 01110 10001 10001 01110", "9": "01110 10001 10001 01111 00001 00010 01100",
    ".": "00000 00000 00000 00000 00000 01100 01100", ",": "00000 00000 00000 00000 01100 00100 01000",
    ":": "00000 01100 01100 00000 01100 01100 00000", "%": "11000 11001 00010 00100 01000 10011 00011",
    "/": "00001 00001 00010 00100 01000 10000 10000", "-": "00000 00000 00000 11111 00000 00000 00000",
    "–": "00000 00000 00000 11111 00000 00000 00000", "+": "00000 00100 00100 11111 00100 00100 00000",
    "(": "00010 00100 01000 01000 01000 00100 00010", ")": "01000 00100 00010 00010 00010 00100 01000",
    "·": "00000 00000 00000 01100 01100 00000 00000", "×": "00000 10001 01010 00100 01010 10001 00000",
    "▲": "00000 00100 01110 11111 00000 00000 00000", "▼": "00000 00000 11111 01110 00100 00000 00000",
    "→": "00000 00100 00010 11111 00010 00100 00000", "'": "00100 00100 01000 00000 00000 00000 00000",
    "?": "01110 10001 00001 00010 00100 00000 00100", "&": "01100 10010 10100 01000 10101 10010 01101",
    "#": "01010 11111 01010 01010 11111 01010 00000", "!": "00100 00100 00100 00100 00100 00000 00100",
    "›": "00000 01000 00100 00010 00100 01000 00000", "‹": "00000 00010 00100 01000 00100 00010 00000",
    "—": "00000 00000 00000 11111 00000 00000 00000", "\"": "01010 01010 00000 00000 00000 00000 00000",
    " ": "00000 00000 00000 00000 00000 00000 00000",
}
_MARK = {"Ç": ("C", None, "00100"), "Ş": ("S", None, "00100"), "Ğ": ("G", "01110", None),
         "İ": ("I", "00100", None), "Ö": ("O", "01010", None), "Ü": ("U", "01010", None)}
GLYPH_W, GLYPH_H, ADV = 5, 7, 6     # yazı kutusu ve harf aralığı (piksel, ölçek 1)

WHITE = (255, 255, 255)
INK = (15, 23, 42)
MUTED = (100, 116, 139)
FAINT = (148, 163, 184)
GRID = (226, 232, 240)
PANEL = (248, 250, 252)
UP = (22, 128, 61)
DOWN = (190, 18, 60)


def up(s: str) -> str:
    """Türkçe büyük harfe çevir; çizilemeyen harfleri aksansız haline indir, kalanı "?"."""
    s = str(s or "").replace("i", "İ").replace("ı", "I").upper()
    out = []
    for ch in s:
        if ch in _G or ch in _MARK:
            out.append(ch)
            continue
        base = unicodedata.normalize("NFKD", ch)[0].upper()
        out.append(base if base in _G else ("·" if ch in "•●" else "?"))
    return "".join(out)


def text_width(s: str, scale: int = 2) -> int:
    return max(0, len(s) * ADV * scale - scale)


def fit(s: str, max_w: int, scale: int = 2) -> str:
    """Genişliğe sığmayan metni "…" yerine ".." ile kısalt (yazı tipinde üç nokta yok)."""
    if text_width(s, scale) <= max_w:
        return s
    while s and text_width(s + "..", scale) > max_w:
        s = s[:-1]
    return s.rstrip() + ".."


class Canvas:
    def __init__(self, w: int, h: int, bg=WHITE):
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

    def _bits(self, x: int, y: int, rows: list[str], scale: int, color):
        for gy, line in enumerate(rows):
            for gx, bit in enumerate(line):
                if bit == "1":
                    self.rect(x + gx * scale, y + gy * scale, x + (gx + 1) * scale, y + (gy + 1) * scale, color)

    def text(self, x: int, y: int, s: str, color=INK, scale: int = 2, align: str = "left") -> int:
        """Metni (zaten up() ile hazırlanmış) çizer; y = harf kutusunun üstü. Bitiş x'ini döner."""
        w = text_width(s, scale)
        if align == "right":
            x -= w
        elif align == "center":
            x -= w // 2
        for ch in s:
            base, top, bottom = _MARK.get(ch, (ch, None, None))
            g = _G.get(base)
            if g:
                self._bits(x, y, g.split(), scale, color)
                if top:
                    self._bits(x, y - 2 * scale, [top], scale, color)
                if bottom:
                    self._bits(x, y + 7 * scale, [bottom], scale, color)
            x += ADV * scale
        return x

    def png(self) -> bytes:
        stride = self.w * 3
        raw = b"".join(b"\x00" + bytes(self.px[y * stride:(y + 1) * stride]) for y in range(self.h))

        def chunk(tag: bytes, data: bytes) -> bytes:
            return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", self.w, self.h, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


# ---------------------------------------------------------------------------
# Pano parçaları
# ---------------------------------------------------------------------------
def _nice_max(v: int) -> int:
    if v <= 4:
        return max(1, v)
    step = -(-v // 4)
    return step * 4


def fmt_int(v) -> str:
    return f"{int(v):,}".replace(",", ".") if v is not None else "-"


def delta_text(cur, prev) -> tuple[str, tuple] | None:
    """Önceki döneme göre değişim etiketi ve rengi (yoksa None)."""
    if cur is None or prev is None or (not cur and not prev):
        return None
    if not prev:
        return "▲ YENİ", UP
    ch = (cur - prev) / prev
    if abs(ch) < 0.005:
        return "= AYNI", MUTED
    return (f"▲ %{round(ch * 100)}" if ch > 0 else f"▼ %{abs(round(ch * 100))}"), (UP if ch > 0 else DOWN)


def tile(c: Canvas, x: int, y: int, w: int, h: int, label: str, value: str, sub: tuple | None = None,
         accent=None):
    c.rect(x, y, x + w, y + h, PANEL)
    c.rect(x, y, x + 4, y + h, accent or GRID)
    c.text(x + 16, y + 14, fit(up(label), w - 28, 2), MUTED, 2)
    c.text(x + 16, y + 38, fit(value, w - 28, 5), INK, 5)
    if sub:
        c.text(x + 16, y + h - 24, fit(sub[0], w - 28, 2), sub[1], 2)


def legend(c: Canvas, x: int, y: int, items: list[tuple[str, tuple]]) -> int:
    for label, color in items:
        c.rect(x, y + 1, x + 12, y + 13, color)
        x = c.text(x + 18, y, up(label), MUTED, 2) + 20
    return x


def stacked_bars(c: Canvas, x: int, y: int, w: int, h: int, labels: list[str], series: list[tuple[tuple, list[int]]]):
    """Yığılmış günlük çubuklar (eksenler + ızgara + tarih etiketleri) — (x, y, w, h) kutusuna."""
    n = max(1, len(labels))
    totals = [sum(s[1][i] for s in series) for i in range(n)]
    ymax = _nice_max(max(totals) if totals else 0)
    left, bottom = 44, 26
    pw, ph = w - left, h - bottom
    y_of = lambda v: y + ph - round(ph * v / ymax)           # noqa: E731
    seen = set()
    for k in range(5):
        v = round(ymax * k / 4)
        if v in seen:
            continue
        seen.add(v)
        yy = y_of(v)
        c.rect(x + left, yy, x + left + pw, yy + 1, GRID if k else FAINT)
        c.text(x + left - 8, yy - 7, str(v), MUTED, 2, "right")
    slot = pw / n
    bw = max(2, int(slot * 0.68))
    for i in range(n):
        x0 = x + left + int(i * slot + (slot - bw) / 2)
        base = 0
        for color, vals in series:
            v = vals[i] if i < len(vals) else 0
            if v:
                c.rect(x0, y_of(base + v), x0 + bw, y_of(base), color)
                base += v
    every = max(1, -(-n * 64 // pw))
    for i in range(n - 1, -1, -every):     # son gün her zaman etiketli
        c.text(x + left + int(i * slot + slot / 2), y + ph + 10, labels[i], MUTED, 2, "center")


def hbar_split(c: Canvas, x: int, y: int, w: int, h: int, parts: list[tuple[str, int, tuple]]):
    """Tek yatay çubukta oranlar (ör. karar dağılımı) + altında renkli açıklama ve sayılar."""
    total = sum(v for _, v, _ in parts)
    c.rect(x, y, x + w, y + h, GRID)
    if total:
        xx = x
        for i, (_, v, color) in enumerate(parts):
            seg = round(w * v / total) if i < len(parts) - 1 else x + w - xx
            c.rect(xx, y, xx + seg, y + h, color)
            xx += seg
    legend(c, x, y + h + 10, [(f"{lab} {v}", color) for lab, v, color in parts])


def progress(c: Canvas, x: int, y: int, w: int, h: int, done: int, total: int, color):
    c.rect(x, y, x + w, y + h, GRID)
    if total:
        c.rect(x, y, x + round(w * min(done, total) / total), y + h, color)


def hbars(c: Canvas, x: int, y: int, w: int, rows: list[tuple[str, list[tuple[int, tuple]]]], row_h: int = 30):
    """Kişi bazında yatay yığılmış çubuklar: [(ad, [(değer, renk), ...])]. Sayı satır sonunda."""
    if not rows:
        c.text(x, y, up("Bu dönemde etkinlik yok"), MUTED, 2)
        return
    name_w = min(220, max(text_width(r[0], 2) for r in rows) + 12)
    bar_w = w - name_w - 56
    top = max(1, max(sum(v for v, _ in parts) for _, parts in rows))
    for i, (name, parts) in enumerate(rows):
        yy = y + i * row_h
        c.text(x, yy + 4, fit(name, name_w - 12, 2), INK, 2)
        xx = x + name_w
        for v, color in parts:
            seg = round(bar_w * v / top)
            c.rect(xx, yy, xx + seg, yy + row_h - 10, color)
            xx += seg
        c.text(xx + 8, yy + 4, str(sum(v for v, _ in parts)), MUTED, 2)


def header(c: Canvas, title: str, subtitle: str, badge: tuple | None = None):
    c.rect(0, 0, c.w, 6, (0, 92, 85))
    c.text(32, 26, fit(up(title), c.w - 300, 3), INK, 3)
    c.text(32, 60, fit(up(subtitle), c.w - 64, 2), MUTED, 2)
    if badge:
        label, color = badge
        s = up(label)
        bw = text_width(s, 2) + 24
        c.rect(c.w - 32 - bw, 24, c.w - 32, 52, color)
        c.text(c.w - 32 - bw + 12, 31, s, WHITE, 2)

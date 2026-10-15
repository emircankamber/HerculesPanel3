"""
MODERN PANO GÖRSELLERİ (Trello kart kapakları) — Pillow + Inter yazı tipi.

- Gerçek, kenar yumuşatmalı yazı (Inter, Türkçe karakterli; `api/fonts/`, SIL OFL lisansı `Inter-LICENSE.txt`).
- Yuvarlatılmış kartlar, hap rozetler, üstü yuvarlak çubuklar. Şekiller `SS` kat büyük çizilip küçültülür → pürüzsüz
  kenar; çıktı `OUT` kat (2×) çözünürlükte → Retina ekranda da net.
- Düzen 1000 birim genişliğe göre yazılır (tüm koordinatlar "birim"); çizimde SS ile çarpılır.
- Pillow ya da yazı tipi yüklenemezse çağıran (`trello.person_dashboard` / `team_dashboard`) eski bit eşlem çizime
  (`png_chart.py`) düşer — aktarım hiçbir koşulda görsel yüzünden durmaz.
Saf modül: veritabanına/ağa dokunmaz.
"""
import io
import os

from PIL import Image, ImageDraw, ImageFont

FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
SS = 3          # çizim ölçeği (birim → piksel), sonra OUT'a küçültülür
OUT = 2         # çıktı ölçeği (1000 birim → 2000 px)

BG = (246, 248, 251)
CARD = (255, 255, 255)
BORDER = (229, 233, 240)
INK = (15, 23, 42)
BODY = (51, 65, 85)
MUTED = (100, 116, 139)
FAINT = (148, 163, 184)
GRID = (238, 242, 247)
TRACK = (237, 241, 246)
UP_BG, UP_FG = (220, 252, 231), (21, 128, 61)
DOWN_BG, DOWN_FG = (255, 228, 230), (190, 18, 60)
FLAT_BG, FLAT_FG = (241, 245, 249), (100, 116, 139)

_FONTS: dict = {}


def font(weight: str, size: float) -> ImageFont.FreeTypeFont:
    key = (weight, size)
    if key not in _FONTS:
        _FONTS[key] = ImageFont.truetype(os.path.join(FONT_DIR, f"Inter-{weight}.ttf"), int(round(size * SS)))
    return _FONTS[key]


def tint(rgb, a: float = 0.14):
    """Rengin açık tonu (beyaza karıştır) — rozet/etiket zemini için."""
    return tuple(round(255 - (255 - c) * a) for c in rgb)


class Board:
    def __init__(self, w: int, h: int):
        self.w, self.h = w, h
        self.img = Image.new("RGB", (w * SS, h * SS), BG)
        self.d = ImageDraw.Draw(self.img)

    @staticmethod
    def _p(v):
        return int(round(v * SS))

    def rect(self, x0, y0, x1, y1, fill=None, r=0, outline=None, width=1):
        if x1 - x0 <= 0 or y1 - y0 <= 0:
            return
        r = min(r, (x1 - x0) / 2, (y1 - y0) / 2)
        self.d.rounded_rectangle([self._p(x0), self._p(y0), self._p(x1) - 1, self._p(y1) - 1], radius=self._p(r),
                                 fill=fill, outline=outline, width=max(1, self._p(width)) if outline else 0)

    def card(self, x0, y0, x1, y1, r=16):
        self.rect(x0, y0 + 1.5, x1, y1 + 1.5, (234, 238, 244), r)        # yumuşak alt gölge
        self.rect(x0, y0, x1, y1, CARD, r, BORDER, 1)

    def circle(self, cx, cy, r, fill):
        self.d.ellipse([self._p(cx - r), self._p(cy - r), self._p(cx + r), self._p(cy + r)], fill=fill)

    def tri(self, cx, cy, size, fill, up=True):
        h = size * 0.9
        pts = [(cx - size / 2, cy + h / 2), (cx + size / 2, cy + h / 2), (cx, cy - h / 2)] if up else \
              [(cx - size / 2, cy - h / 2), (cx + size / 2, cy - h / 2), (cx, cy + h / 2)]
        self.d.polygon([(self._p(x), self._p(y)) for x, y in pts], fill=fill)

    def text(self, x, y, s, f, fill=INK, anchor="la"):
        self.d.text((self._p(x), self._p(y)), s, font=f, fill=fill, anchor=anchor)

    def tw(self, s, f) -> float:
        return self.d.textlength(s, font=f) / SS

    def fit(self, s, f, max_w) -> str:
        s = str(s or "")
        if self.tw(s, f) <= max_w:
            return s
        while s and self.tw(s + "…", f) > max_w:
            s = s[:-1]
        return s.rstrip() + "…"

    def pill(self, x, y, s, f, bg, fg, pad_x=10, h=24, anchor_right=False, dot=None) -> float:
        """Hap rozet; genişliğini döner. anchor_right: x sağ kenar."""
        w = self.tw(s, f) + 2 * pad_x + (14 if dot else 0)
        if anchor_right:
            x -= w
        self.rect(x, y, x + w, y + h, bg, h / 2)
        tx = x + pad_x
        if dot:
            self.circle(tx + 4, y + h / 2, 4, dot)
            tx += 14
        self.text(tx, y + h / 2, s, f, fg, "lm")
        return w

    def png(self) -> bytes:
        out = self.img.resize((self.w * OUT, self.h * OUT), Image.LANCZOS)
        buf = io.BytesIO()
        out.save(buf, "PNG", compress_level=6)
        return buf.getvalue()


# ---------------------------------------------------------------------------
# Parçalar
# ---------------------------------------------------------------------------
def _fmt(v) -> str:
    return f"{int(v):,}".replace(",", ".") if v is not None else "–"


def _pct(v) -> str:
    return f"%{round(v * 100)}" if v is not None else "–"


def delta(cur, prev):
    """(metin, yön) — yön: up | down | flat | None (gösterme)."""
    if cur is None or prev is None or (not cur and not prev):
        return None
    if not prev:
        return "yeni", "up"
    ch = (cur - prev) / prev
    if abs(ch) < 0.005:
        return "aynı", "flat"
    return f"%{abs(round(ch * 100))}", ("up" if ch > 0 else "down")


def header(b: Board, title: str, subtitle: str, badge: tuple | None):
    b.card(24, 20, b.w - 24, 104)
    right = b.w - 48
    if badge:
        label, color = badge
        right -= b.pill(right, 46, label, font("SemiBold", 13), tint(color, 0.16), color, 12, 28,
                        anchor_right=True, dot=color) + 16
    b.text(48, 38, b.fit(title, font("Bold", 26), right - 48), font("Bold", 26), INK)
    b.text(48, 76, b.fit(subtitle, font("Regular", 14), b.w - 96), font("Regular", 14), MUTED)


def kpi(b: Board, x, y, w, h, label, value, color, d=None, sub=None):
    b.card(x, y, x + w, y + h, 14)
    b.circle(x + 22, y + 26, 5, color)
    b.text(x + 34, y + 26, label, font("Medium", 13), MUTED, "lm")
    b.text(x + 18, y + 48, b.fit(value, font("Bold", 34), w - 36), font("Bold", 34), INK)
    if d:
        txt, way = d
        bg, fg = {"up": (UP_BG, UP_FG), "down": (DOWN_BG, DOWN_FG)}.get(way, (FLAT_BG, FLAT_FG))
        f = font("SemiBold", 12)
        pw = b.tw(txt, f) + 34
        b.rect(x + 18, y + h - 36, x + 18 + pw, y + h - 14, bg, 11)
        if way in ("up", "down"):
            b.tri(x + 30, y + h - 25, 9, fg, way == "up")
        else:
            b.rect(x + 25, y + h - 26, x + 35, y + h - 24, fg, 1)
        b.text(x + 40, y + h - 25, txt, f, fg, "lm")
        note, fn = "önceki döneme göre", font("Regular", 11)
        if b.tw(note, fn) <= w - pw - 44:     # sığmıyorsa yazma (taşmasın)
            b.text(x + 26 + pw, y + h - 25, note, fn, FAINT, "lm")
    elif sub:
        b.text(x + 18, y + h - 25, b.fit(sub, font("Medium", 12), w - 36), font("Medium", 12), MUTED, "lm")


def legend(b: Board, x_right, y, items):
    f = font("Medium", 12)
    x = x_right - sum(b.tw(t, f) + 30 for t, _ in items)
    for t, c in items:
        b.circle(x + 5, y, 5, c)
        b.text(x + 15, y, t, f, MUTED, "lm")
        x += b.tw(t, f) + 30


def _nice(v: int) -> int:
    if v <= 4:
        return max(1, v)
    step = -(-v // 4)
    for nice in (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000):
        if nice >= step:
            return nice * 4
    return step * 4


def bars(b: Board, x, y, w, h, labels, series):
    """Yığılmış günlük çubuklar; en üst parça yuvarlak uçlu."""
    n = max(1, len(labels))
    tot = [sum(s[1][i] for s in series) for i in range(n)]
    ymax = _nice(max(tot) if tot else 0)
    left, bottom = 34, 26
    pw, ph = w - left, h - bottom
    fy = font("Regular", 11)
    seen = set()
    for k in range(5):
        v = round(ymax * k / 4)
        if v in seen:
            continue
        seen.add(v)
        yy = y + ph - ph * v / ymax
        b.rect(x + left, yy - 0.5, x + left + pw, yy + 0.5, GRID if k else BORDER)
        b.text(x + left - 10, yy, str(v), fy, FAINT, "rm")
    slot = pw / n
    bw = max(3, slot * 0.62)
    for i in range(n):
        x0 = x + left + i * slot + (slot - bw) / 2
        segs = [(c, s[i] if i < len(s) else 0) for c, s in series]
        total = sum(v for _, v in segs)
        if not total:
            continue
        top = y + ph - ph * total / ymax
        top_color = next(c for c, v in reversed(segs) if v)
        b.rect(x0, top, x0 + bw, y + ph, top_color, min(4, bw / 2))       # tüm sütun, üstü yuvarlak
        base = 0
        for c, v in segs:                                                   # alttan üste düz parçalar
            if not v or c == top_color and base + v == total:
                base += v
                continue
            y1 = y + ph - ph * base / ymax
            y0 = y + ph - ph * (base + v) / ymax
            b.rect(x0, y0, x0 + bw, y1, c, 0)
            base += v
    if not any(tot):
        b.text(x + left + pw / 2, y + ph / 2, "Bu dönemde kayıtlı etkinlik yok", font("Medium", 13), FAINT, "mm")
    fl = font("Regular", 11)
    every = max(1, -(-n * 52 // int(pw)))
    for i in range(n - 1, -1, -every):
        b.text(x + left + i * slot + slot / 2, y + ph + 16, labels[i], fl, FAINT, "mm")


def split_bar(b: Board, x, y, w, parts, h=14):
    """Oran çubuğu (ör. karar dağılımı) — parçalar arası boşluk, uçlar yuvarlak; altında renkli açıklama."""
    total = sum(v for _, v, _ in parts)
    b.rect(x, y, x + w, y + h, TRACK, h / 2)
    if total:
        nz = [(lab, v, c) for lab, v, c in parts if v]
        gap = 3
        avail = w - gap * (len(nz) - 1)
        xx = x
        for i, (_, v, c) in enumerate(nz):
            seg = avail * v / total if i < len(nz) - 1 else x + w - xx
            b.rect(xx, y, xx + seg, y + h, c, h / 2)
            xx += seg + gap
    f = font("Medium", 12)
    xx = x
    for lab, v, c in parts:
        b.circle(xx + 5, y + h + 20, 5, c)
        s = f"{lab}  {v}"
        b.text(xx + 15, y + h + 20, s, f, BODY, "lm")
        xx += b.tw(s, f) + 34


def progress(b: Board, x, y, w, done, total, color, h=14):
    b.rect(x, y, x + w, y + h, TRACK, h / 2)
    if total and done:
        b.rect(x, y, x + max(h, w * min(done, total) / total), y + h, color, h / 2)


def hbars(b: Board, x, y, w, rows, row_h=34):
    """[(ad, [(değer, renk), ...])] — yatay yığılmış, yuvarlak uçlu; sayı satır sonunda."""
    if not rows:
        b.text(x, y + 12, "Bu dönemde etkinlik yok", font("Regular", 13), MUTED, "lm")
        return
    fn, fv = font("Medium", 13), font("SemiBold", 12)
    name_w = min(170, max(b.tw(r[0], fn) for r in rows) + 14)
    bar_w = w - name_w - 40
    top = max(1, max(sum(v for v, _ in p) for _, p in rows))
    for i, (name, parts) in enumerate(rows):
        cy = y + i * row_h + 12
        b.text(x, cy, b.fit(name, fn, name_w - 14), fn, BODY, "lm")
        tot = sum(v for v, _ in parts)
        full = bar_w * tot / top
        b.rect(x + name_w, cy - 6, x + name_w + bar_w, cy + 6, TRACK, 6)
        nz = [(v, c) for v, c in parts if v]
        if nz:   # önce son rengin tam boyu (sağ uç yuvarlak), sonra öndeki parçalar düz
            b.rect(x + name_w, cy - 6, x + name_w + max(12, full), cy + 6, nz[-1][1], 6)
            xx = x + name_w
            for v, c in nz[:-1]:
                seg = bar_w * v / top
                b.rect(xx, cy - 6, xx + seg, cy + 6, c, 6 if xx == x + name_w else 0)
                xx += seg
        b.text(x + name_w + max(12, full) + 8, cy, str(tot), fv, INK, "lm")


def funnel(b: Board, x, y, w, stages):
    """[(etiket, değer, renk)] — basamaklı ürün hattı."""
    top = max(1, stages[0][1] if stages else 1)
    fl, fv = font("Medium", 13), font("SemiBold", 13)
    for i, (lab, v, c) in enumerate(stages):
        cy = y + i * 30 + 10
        b.text(x, cy, lab, fl, BODY, "lm")
        bx = x + 92
        bw = (w - 92 - 44) * v / top
        b.rect(bx, cy - 9, bx + (w - 92 - 44), cy + 9, TRACK, 9)
        if v:
            b.rect(bx, cy - 9, bx + max(18, bw), cy + 9, c, 9)
        b.text(bx + max(18, bw) + 8, cy, str(v), fv, INK, "lm")


# ---------------------------------------------------------------------------
# Panolar
# ---------------------------------------------------------------------------
C_ANALYSIS, C_DECISION, C_FORUM = (22, 163, 74), (249, 115, 22), (37, 99, 235)
C_TEAL, C_PURPLE = (13, 148, 136), (124, 58, 237)
DECISION = (("Uygun", (22, 163, 74)), ("Sınırda", (245, 158, 11)), ("Elenmiş", (225, 29, 72)))
LEGEND = [("Analiz", C_ANALYSIS), ("Karar", C_DECISION), ("Forum", C_FORUM)]


def _series(bk):
    return [(C_ANALYSIS, bk["analyses"]), (C_DECISION, bk["decisions"]), (C_FORUM, bk["posts"])]


def person(x: dict, labels, bk, period: str, status: tuple, chart_days: int) -> bytes:
    W, H = 1000, 680
    b = Board(W, H)
    sub = " · ".join(s for s in (period, x.get("title") or "", ", ".join(x.get("teams") or [])) if s)
    header(b, x["name"], sub, status)
    pv = x["prev"]
    g, cw = 12, (W - 48 - 4 * 12) / 5
    tiles = [
        ("Analiz", _fmt(x["analyses"]), C_ANALYSIS, delta(x["analyses"], pv["analyses"]), None),
        ("Karar", _fmt(x["decisions"]), C_DECISION, delta(x["decisions"], pv["decisions"]), None),
        ("Aktif gün", _fmt(x["active_days"]), C_TEAL, delta(x["active_days"], pv["active_days"]), None),
        ("Dönüşüm", _pct(x["conversion"]), C_PURPLE, None, f"{x['decided_products']}/{x['products']} ürün"),
        ("Forum", _fmt(x["threads"] + x["replies"]), C_FORUM, delta(x["threads"] + x["replies"], pv["posts"]), None),
    ]
    for i, (lab, val, col, d, s) in enumerate(tiles):
        kpi(b, 24 + i * (cw + g), 120, cw, 124, lab, val, col, d, s)
    b.card(24, 260, W - 24, 516)
    b.text(44, 286, f"Son {chart_days} gün · günlük etkinlik", font("SemiBold", 15), INK, "lm")
    legend(b, W - 44, 286, LEGEND)
    bars(b, 44, 310, W - 88, 194, labels, _series(bk))
    half = (W - 48 - 16) / 2
    b.card(24, 532, 24 + half, 656)
    b.text(44, 558, "Karar dağılımı", font("SemiBold", 15), INK, "lm")
    b.text(24 + half - 20, 558, f"{sum(x['mix'].get(k, 0) for k, _ in DECISION)} karar", font("Regular", 12), MUTED, "rm")
    split_bar(b, 44, 582, half - 40, [(k, x["mix"].get(k, 0), c) for k, c in DECISION])
    rx = 24 + half + 16
    b.card(rx, 532, W - 24, 656)
    done, total = x["lessons_done"], x["lessons_assigned"]
    b.text(rx + 20, 558, "Eğitim", font("SemiBold", 15), INK, "lm")
    b.text(W - 44, 558, f"{done}/{total} ders" + (f" · %{round(done * 100 / total)}" if total else ""),
           font("Medium", 12), MUTED, "rm")
    progress(b, rx + 20, 582, half - 40, done, total, C_TEAL)
    od = len(x.get("lessons_overdue") or [])
    b.text(rx + 20, 622, f"Ön öneriyle uyum {_pct(x['agreement'])}", font("Medium", 12), BODY, "lm")
    if od:
        b.pill(W - 44, 610, f"{od} gecikmiş ders", font("SemiBold", 12), DOWN_BG, DOWN_FG, 10, 24, anchor_right=True)
    return b.png()


def team(data: dict, labels, bk, title: str, period: str, chart_days: int) -> bytes:
    W, H = 1000, 800
    t, pv = data["totals"], data["prev"]
    b = Board(W, H)
    header(b, title, period, (f"{t['active_members']}/{t['members']} kişi aktif", C_TEAL))
    g, cw = 12, (W - 48 - 4 * 12) / 5
    tiles = [
        ("Aktif kişi", f"{t['active_members']}/{t['members']}", C_TEAL, delta(t["active_members"], pv["active_members"]), None),
        ("Analiz", _fmt(t["analyses"]), C_ANALYSIS, delta(t["analyses"], pv["analyses"]), None),
        ("Karar", _fmt(t["decisions"]), C_DECISION, delta(t["decisions"], pv["decisions"]), None),
        ("Dönüşüm", _pct(t["conversion"]), C_PURPLE, None, f"Ön öneriyle uyum {_pct(t['agreement'])}"),
        ("Eğitim", _pct(t["training_rate"]), C_FORUM, None, f"{t['lessons_done']}/{t['lessons_assigned']} ders tamamlandı"),
    ]
    for i, (lab, val, col, d, s) in enumerate(tiles):
        kpi(b, 24 + i * (cw + g), 120, cw, 124, lab, val, col, d, s)
    lw = 584
    b.card(24, 260, 24 + lw, 540)
    b.text(44, 286, f"Son {chart_days} gün · ekip", font("SemiBold", 15), INK, "lm")
    legend(b, 24 + lw - 20, 286, LEGEND)
    bars(b, 44, 310, lw - 40, 218, labels, _series(bk))
    rx = 24 + lw + 16
    b.card(rx, 260, W - 24, 540)
    b.text(rx + 20, 286, "Kişi bazında (dönem)", font("SemiBold", 15), INK, "lm")
    ppl = sorted(data["people"], key=lambda p: -(p["analyses"] + p["decisions"] + p["threads"] + p["replies"]))
    rows = [(p["name"], [(p["analyses"], C_ANALYSIS), (p["decisions"], C_DECISION), (p["threads"] + p["replies"], C_FORUM)])
            for p in ppl if p["analyses"] + p["decisions"] + p["threads"] + p["replies"]][:6]
    hbars(b, rx + 20, 314, W - 24 - rx - 40, rows, 36)
    half = (W - 48 - 16) / 2
    b.card(24, 556, 24 + half, 776)
    b.text(44, 582, "Karar dağılımı", font("SemiBold", 15), INK, "lm")
    mix = data.get("mix") or {}
    b.text(24 + half - 20, 582, f"{sum(mix.get(k, 0) for k, _ in DECISION)} karar", font("Regular", 12), MUTED, "rm")
    split_bar(b, 44, 608, half - 40, [(k, mix.get(k, 0), c) for k, c in DECISION])
    b.text(44, 690, "Kontrol listesi", font("Medium", 12), MUTED, "lm")
    b.text(44, 716, f"{t['lists_started']} başlatıldı · {t['lists_approved']} onaylandı · {t['lists_open']} açık",
           font("SemiBold", 14), INK, "lm")
    if t.get("lists_stale"):
        b.pill(44, 736, f"{t['lists_stale']} liste bekliyor", font("SemiBold", 12), DOWN_BG, DOWN_FG, 10, 24)
    fx = 24 + half + 16
    b.card(fx, 556, W - 24, 776)
    b.text(fx + 20, 582, "Ürün hattı", font("SemiBold", 15), INK, "lm")
    f = data.get("funnel") or [0, 0, 0, 0, 0]
    funnel(b, fx + 20, 606, half - 40, [("Analiz", f[0], C_ANALYSIS), ("Karar", f[1], C_DECISION),
                                       ("Uygun", f[2], DECISION[0][1]), ("Liste", f[3], C_TEAL), ("Onay", f[4], C_PURPLE)])
    return b.png()

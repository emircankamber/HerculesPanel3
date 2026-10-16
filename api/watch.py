"""EĞİTİM VİDEOSU İZLEME TAKİBİ — saf hesap (veritabanı/ağ yok).

Panel YouTube oynatıcısından (IFrame API) videonun GERÇEKTEN oynatılan aralıklarını toplar ve ~10 sn'de bir
gönderir (`POST /api/training/lessons/{id}/watch`). Sunucu:
  - aralıkları birleştirir (aynı yeri iki kez izlemek iki kez sayılmaz, ileri sarılan kısım hiç gelmez),
  - ZAMAN KURALI: iki bildirim arasında yeni sayılan süre en fazla MAX_SPEED (2×) × geçen gerçek zaman + SLACK
    olabilir (2× hızda izleyen tam sayılır; daha hızlı/sahte bildirim kırpılır). Geçen süre MAX_GAP ile sınırlı:
    dün açılıp bugün dönülen bir oturum tek seferde tüm videoyu "izlenmiş" bildiremez.
  - oran = izlenen ÷ süre; tamamlamak için ≥ WATCH_MIN_RATIO.
Bilinen sınır: süreyi (duration) oynatıcı bildirir; tarayıcı araçlarıyla sahte istek atan biri yine de
videonun süresinin yarısı kadar beklemek zorundadır.
"""
from __future__ import annotations

WATCH_MIN_RATIO = 0.8      # tamamlamak için izlenmesi gereken oran (kullanıcı isteği: %80)
MAX_SPEED = 2.0            # 2× izlemek normal (kullanıcı isteği) — daha hızlısı sayılmaz
SLACK = 5.0                # ağ/zamanlama payı (sn)
MAX_GAP = 60.0             # bildirimler arası en fazla bu kadar gerçek zaman hesaba katılır (sn)
MAX_DURATION = 6 * 3600.0  # makul video süresi üst sınırı (sn)
MAX_SEGMENTS_IN = 200


def merge(segs) -> list[list[float]]:
    out: list[list[float]] = []
    for s, e in sorted((float(a), float(b)) for a, b in segs if b > a):
        if out and s <= out[-1][1] + 0.5:   # yarım saniyelik boşluk = kesintisiz izleme
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return out


def covered(segs) -> float:
    return sum(e - s for s, e in segs)


def _uncovered(seg, segs) -> list[list[float]]:
    """seg'in, segs (birleştirilmiş) tarafından kapsanmayan parçaları."""
    s, e = seg
    parts, cur = [], s
    for a, b in segs:
        if b <= cur:
            continue
        if a >= e:
            break
        if a > cur:
            parts.append([cur, min(a, e)])
        cur = max(cur, b)
        if cur >= e:
            break
    if cur < e:
        parts.append([cur, e])
    return parts


def apply_beat(state: dict | None, new_segs, duration, now: float) -> dict:
    """state = {"segments", "duration", "last_at"} (yoksa None) → yeni state. Yeni aralıklar videonun süresine
    kırpılır, zaten izlenmiş kısımlar düşülür, kalan yeni süre zaman kuralıyla sınırlanır (sırayla, aşan kırpılır)."""
    st = {"segments": merge((state or {}).get("segments") or []),
          "duration": (state or {}).get("duration"), "last_at": (state or {}).get("last_at")}
    if not st["duration"]:
        try:
            d = float(duration or 0)
        except (TypeError, ValueError):
            d = 0.0
        if 0 < d <= MAX_DURATION:
            st["duration"] = d   # İLK bildirilen süre sabitlenir (sonradan küçültülemez)
    dur = st["duration"]
    elapsed = 0.0 if st["last_at"] is None else max(0.0, min(now - float(st["last_at"]), MAX_GAP))
    allowance = MAX_SPEED * elapsed + (SLACK if st["last_at"] is not None else 0.0)
    if dur:
        for raw in list(new_segs or [])[:MAX_SEGMENTS_IN]:
            try:
                s, e = max(0.0, float(raw[0])), min(dur, float(raw[1]))
            except (TypeError, ValueError, IndexError):
                continue
            if e <= s:
                continue
            for a, b in _uncovered([s, e], st["segments"]):
                if allowance <= 0:
                    break
                take = min(b - a, allowance)
                allowance -= take
                st["segments"] = merge(st["segments"] + [[a, a + take]])
    st["last_at"] = now
    return st


def summary(state: dict | None) -> dict:
    segs = merge((state or {}).get("segments") or [])
    dur = (state or {}).get("duration") or 0
    watched = covered(segs)
    return {"segments": [[round(s, 2), round(e, 2)] for s, e in segs], "duration": dur or None,
            "watched": round(watched, 1), "ratio": round(min(1.0, watched / dur), 4) if dur else 0.0}


def normalized_len(note: str | None) -> int:
    """Kazanım notunun uzunluğu — boşluklar tekilleştirilerek sayılır (boşlukla doldurmak sayılmaz)."""
    return len(" ".join((note or "").split()))

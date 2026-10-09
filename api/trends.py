"""
Trendler & Fırsatlar — kategori listesi, yasak ve metrik filtreleri (saf modül, MCP çağrısı YOK).

GERÇEK MCP ÇAĞRILARIYLA DOĞRULANDI (aba_research_weekly/monthly, US/UK/CA):
- `departments` İSTEK parametresi görünen adı DEĞİL Amazon arama takma adını ister: "Toys & Games" → 0 sonuç,
  "toys-and-games" → çalışır. Yanıttaki `departments` alanı ise görünen addır ("Toys & Games").
- Takma adlar pazar yerine göre değişir (UK'de "toys", CA'da "toys-and-games" 0 sonuç) → yalnızca US listesi
  doğrulandı; diğer pazarlarda kategori filtresi panelde (görünen adla) uygulanır.
- includeKeywords / excludeKeywords çalışır. minSearches/maxSearches, maxWordCount, minConversionRate,
  maxMonopolyClickRate SESSİZCE YOK SAYILIR (2.5M aramalı keyword maxSearches=100000 ile döndü) → sayısal
  filtrelerin HEPSİ burada, Python'da uygulanır.
- Sayfa başına en fazla 40 kayıt döner (size=100 → 40).
- `clickShareRate` / `cvsShareRate` = ilk 3 ASIN'in clickRate / conversionRate TOPLAMI (0-1 oran; needoh:
  .3582+.0751+.0495 = .4828).
"""
import re

PAGE_SIZE = 40          # SellerSprite'ın sayfa başına verdiği üst sınır
MAX_SCAN_PAGES = 5      # tarama başına en fazla MCP çağrısı (yasak/filtre sonrası sonuç azalırsa sonraki sayfa)

# US — (Amazon arama takma adı, yanıttaki görünen ad). Her biri gerçek çağrıyla doğrulandı.
# ("appliances" ve "stripbooks" 0 sonuç verdi → listede yok.)
US_DEPARTMENTS = [
    ("amazon-devices", "Amazon Devices"),
    ("arts-crafts", "Arts, Crafts & Sewing"),
    ("automotive", "Automotive Parts & Accessories"),
    ("baby-products", "Baby"),
    ("beauty", "Beauty & Personal Care"),
    ("mobile", "Cell Phones & Accessories"),
    ("fashion", "Clothing, Shoes & Jewelry"),
    ("electronics", "Electronics"),
    ("lawngarden", "Garden & Outdoor"),
    ("grocery", "Grocery & Gourmet Food"),
    ("hpc", "Health, Household & Baby Care"),
    ("kitchen", "Home & Kitchen"),
    ("industrial", "Industrial & Scientific"),
    ("jewelry", "Jewelry"),
    ("digital-text", "Kindle Store"),
    ("mi", "Musical Instruments"),
    ("office-products", "Office Products"),
    ("pets", "Pet Supplies"),
    ("sporting", "Sports & Outdoors"),
    ("tools", "Tools & Home Improvement"),
    ("toys-and-games", "Toys & Games"),
    ("videogames", "Video Games"),
]
DEPARTMENT_ALIASES = {"US": {name.lower(): alias for alias, name in US_DEPARTMENTS}}
KNOWN_DEPARTMENTS = {"US": [name for _, name in US_DEPARTMENTS]}

_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")


def clean_category_name(name) -> str:
    """Boşlukları sadeleştir, kontrol karakterlerini at. Boş ya da >80 karakter → ValueError."""
    s = " ".join(_CTRL_RE.sub(" ", str(name or "")).split())
    if not s:
        raise ValueError("Kategori adı boş olamaz")
    if len(s) > 80:
        raise ValueError("Kategori adı en fazla 80 karakter olabilir")
    return s


def canonical_name(name: str) -> str:
    """Bilinen bir kategorinin adı farklı harf büyüklüğüyle yazıldıysa SellerSprite'taki yazımı döner."""
    for names in KNOWN_DEPARTMENTS.values():
        for n in names:
            if n.lower() == name.lower():
                return n
    return name


def department_alias(marketplace: str, name: str) -> str | None:
    return DEPARTMENT_ALIASES.get((marketplace or "").upper(), {}).get((name or "").lower())


def is_banned(departments, banned_keys: set[str]) -> bool:
    """Keyword'ün kategorilerinden HERHANGİ BİRİ yasaklıysa gizlenir. Kategorisiz keyword etkilenmez."""
    return any((d or "").lower() in banned_keys for d in (departments or []))


def in_category(departments, name: str) -> bool:
    key = (name or "").lower()
    return any((d or "").lower() == key for d in (departments or []))


def word_count(keyword) -> int:
    return len((keyword or "").split())


def passes_filters(row: dict, f: dict) -> bool:
    """row = discovery_trending çıktı satırı. Değeri eksik (None) satır, o metrik için filtre verilmişse elenir."""
    def num(k):
        v = row.get(k)
        return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None

    checks = [
        ("min_searches", "searches", lambda v, t: v >= t),
        ("max_searches", "searches", lambda v, t: v <= t),
        ("min_purchases", "purchases", lambda v, t: v >= t),
        ("min_purchase_rate", "purchase_rate", lambda v, t: v >= t),
        ("min_growth", "growth_rate", lambda v, t: v >= t),
        ("max_click_share", "click_share", lambda v, t: v <= t),
        ("max_bid", "bid", lambda v, t: v <= t),
    ]
    for fk, rk, ok in checks:
        t = f.get(fk)
        if t is None:
            continue
        v = num(rk)
        if v is None or not ok(v, t):
            return False
    if f.get("max_words") is not None and word_count(row.get("keyword")) > f["max_words"]:
        return False
    return True


# ---------------------------------------------------------------------------
# Periyot & keyword hacim geçmişi — aba_research_trend (GERÇEK MCP ÇAĞRISIYLA DOĞRULANDI, "magnetic tiles" US):
# parametreler DÜZ (request sarmalı YOK: keyword, marketplace, timeGranularity="M"); `data` doğrudan liste,
# her eleman {"label": "yyyyMM", "searches": int, "rank": int, "date": ms}. Geçmiş 2022-01'den son aya kadar
# (~57 ay) → 3 yıllık karşılaştırma mümkün. Bu, ABA'nın AYLIK ARAMA HACMİ — sıralama ivmesi değil.
# ---------------------------------------------------------------------------
# Periyot → ay sayısı. weekly/monthly = SellerSprite'ın kendi haftalık/aylık listesi (dönem karşılaştırması yok).
PERIOD_MONTHS = {"weekly": None, "monthly": None, "3m": 3, "6m": 6, "9m": 9, "12m": 12, "36m": 36}
HISTORY_MONTHS = 37          # keyword başına en fazla ay (3 yıl karşılaştırması için 36+1; panel 1 yıl, 3 yıl periyodunda 3 yıl çizer)
HISTORY_CONCURRENCY = 6      # eşzamanlı aba_research_trend çağrısı
PERIOD_POOL_MAX = 40         # dönem modunda hacim değişimine göre sıralanacak aday havuzu (sonuç sayısı küçükse)


def parse_history(raw) -> list[dict]:
    """aba_research_trend yanıtı → [{"month": "yyyyMM", "searches": int}] (eskiden yeniye, geçersizler atılır)."""
    data = raw.get("data") if isinstance(raw, dict) else None
    out = {}
    for it in data if isinstance(data, list) else []:
        if not isinstance(it, dict):
            continue
        label = str(it.get("label") or "")
        v = it.get("searches")
        if not re.fullmatch(r"\d{6}", label) or not isinstance(v, (int, float)) or isinstance(v, bool):
            continue
        out[label] = int(v)
    return [{"month": m, "searches": out[m]} for m in sorted(out)][-HISTORY_MONTHS:]


def _shift_month(yyyymm: str, back: int) -> str:
    y, m = int(yyyymm[:4]), int(yyyymm[4:])
    idx = y * 12 + (m - 1) - back
    return f"{idx // 12:04d}{idx % 12 + 1:02d}"


def period_change(history: list[dict], months: int) -> dict | None:
    """Son ayın arama hacmi ÷ N ay önceki ayın hacmi − 1 (ör. 202609 vs 202606). İki uçtan biri yoksa ya da
    başlangıç 0 ise None."""
    if not history or not months:
        return None
    by_month = {h["month"]: h["searches"] for h in history}
    end = history[-1]["month"]
    start = _shift_month(end, months)
    a, b = by_month.get(start), by_month.get(end)
    if not a or b is None:
        return None
    return {"from": start, "to": end, "from_searches": a, "to_searches": b, "change": round(b / a - 1, 4)}

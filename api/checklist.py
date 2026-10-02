"""
Araştırma Kontrol Listesi — saf mantık (DB/MCP yok, bağımsız import edilebilir).

- DEFAULT_TEMPLATE: Stitch tasarımındaki 5 aşama / 22 madde. 6 madde OTOMATİK:
  liste oluşturulurken alınan snapshot'tan değerlendirilir, elle işaretlenemez.
- extract_snapshot(): /api/analyze(-asin) payload'undan (keyword_analysis kaydı)
  gereken değerleri çıkarır — sonradan MCP çağrısı YOK.
- evaluate_auto(): eşiklere göre pass / fail / no_data. Veri yoksa geçti sayılmaz.
"""
import copy
import secrets

# Eşik varsayılanları (owner Ayarlar'dan değil, Kontrol Listesi şablon editöründen değiştirir)
DEFAULT_THRESHOLDS = {
    "searches_min": 40000,        # ana keyword aylık arama  >  40.000 (kesin büyüktür)
    "top10_revenue_min": 500000,  # ilk 10 rakip toplam aylık ciro  >  $500.000
    "reviews_max": 800,           # ortalama yorum  <  800
    "new_brands_min": 3,          # son 1 yılda yeni marka  >=  3
    "top3_share_max": 0.65,       # ilk 3 marka ciro payı toplamı  <  %65 (0-1 oran)
    "price_min": 25.0,            # ortalama fiyat  $25 – $70 (iki uç dahil)
    "price_max": 70.0,
}

THRESHOLD_LIMITS = {  # (min, max, tam sayı mı)
    "searches_min": (0, 100_000_000, True),
    "top10_revenue_min": (0, 1_000_000_000, False),
    "reviews_max": (0, 10_000_000, True),
    "new_brands_min": (0, 20, True),
    "top3_share_max": (0, 1, False),
    "price_min": (0, 100_000, False),
    "price_max": (0, 100_000, False),
}

AUTO_KEYS = ("searches", "top10_revenue", "reviews", "new_brands", "top3_share", "price")


def _fmt_int(v) -> str:
    return f"{int(round(v)):,}".replace(",", ".")


def _fmt_usd(v) -> str:
    return "$" + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _fmt_pct(v) -> str:
    return "%" + f"{v * 100:.1f}".replace(".", ",")


def auto_text(auto: str, th: dict) -> str:
    """Otomatik madde metni eşiklerden üretilir (şablon kopyasıyla listede saklanır)."""
    return {
        "searches": f"Ana keyword aylık arama hacmi > {_fmt_int(th['searches_min'])}",
        "top10_revenue": f"İlk 10 rakibin toplam aylık cirosu > {_fmt_usd(th['top10_revenue_min'])}",
        "reviews": f"Ortalama yorum sayısı < {_fmt_int(th['reviews_max'])}",
        "new_brands": f"Son 1 yılda en az {_fmt_int(th['new_brands_min'])} yeni marka",
        "top3_share": f"İlk 3 markanın ciro payı toplamı < {_fmt_pct(th['top3_share_max'])}",
        "price": f"Ortalama fiyat {_fmt_usd(th['price_min'])} – {_fmt_usd(th['price_max'])} arasında",
    }[auto]


DEFAULT_TEMPLATE = {
    "version": 1,
    "thresholds": dict(DEFAULT_THRESHOLDS),
    "stages": [
        {"key": "market", "title": "Pazar Büyüklüğü ve Talep Tespiti",
         "subtitle": "Ana pazar talebinin sürdürülebilirliği ve kümülatif ciro derinliği", "critical": False,
         "items": [{"key": "auto_searches", "auto": "searches"},
                   {"key": "auto_top10_revenue", "auto": "top10_revenue"},
                   {"key": "m_trends", "text": "Google Trends ve 2 yıllık pazar grafiğinde kalıcı talep var"}]},
        {"key": "competition", "title": "Rekabet ve Giriş Bariyeri Analizi",
         "subtitle": "Pazar tekelleşmesi, yorum bariyerleri ve yeni marka tutunma oranları", "critical": False,
         "items": [{"key": "auto_reviews", "auto": "reviews"},
                   {"key": "auto_new_brands", "auto": "new_brands"},
                   {"key": "auto_top3_share", "auto": "top3_share"},
                   {"key": "auto_price", "auto": "price"},
                   {"key": "m_lqs", "text": "Listing kalite skoru (LQS) zayıf en az 3 rakip tespit edildi"}]},
        {"key": "defects", "title": "Ürün Kusur Analizi & Müşteri Şikayet Haritası",
         "subtitle": "Olumsuz yorum madenciliği ve tedarikçi iyileştirme brifingi", "critical": False,
         "items": [{"key": "m_neg_reviews", "text": "Rakiplerin 1, 2 ve 3 yıldızlı olumsuz yorumları kümelendi"},
                   {"key": "m_top_complaint", "text": "En çok tekrarlanan donanım/kalite şikayeti listelendi"},
                   {"key": "m_fix", "text": "Şikayeti çözecek mühendislik veya tedarikçi revizyonu netleştirildi"},
                   {"key": "m_brief", "text": "Numune için tedarikçiye iletilecek iyileştirme brifingi hazırlandı"}]},
        {"key": "legal", "title": "Patent, Marka & Hukuki Risk Kontrolü",
         "subtitle": "Kritik darboğaz — onaysız ilerlemek hesap askısına yol açabilir", "critical": True,
         "items": [{"key": "m_ip_search", "text": "USPTO ve EUIPO veri tabanlarında tasarım / faydalı model tescil taraması yapıldı"},
                   {"key": "m_patent_risk", "text": "Ürün ve gövde tasarımı için aktif patent ihlali riski kontrol edildi"},
                   {"key": "m_certs", "text": "FDA / CE / RoHS sertifikasyon gereksinimi kontrol edildi"},
                   {"key": "m_restricted", "text": "Amazon Restricted Products (Kısıtlı Ürünler) politikası kontrol edildi"}]},
        {"key": "costs", "title": "Birim Maliyet, Lojistik & Kârlılık Simülasyonu",
         "subtitle": "FOB, DDP navlun, FBA komisyonları ve hedef net kâr projeksiyonu", "critical": False,
         "items": [{"key": "m_fob", "text": "Alibaba FOB tedarikçi fiyat teklifleri alındı"},
                   {"key": "m_freight", "text": "Deniz/hava kargo DDP navlun birim maliyeti hesaplandı"},
                   {"key": "m_moq", "text": "İlk parti minimum sipariş miktarı (MOQ) bütçeye uyuyor"},
                   {"key": "m_margin", "text": "FBA komisyonu ve depolama sonrası net kâr marjı > %35"},
                   {"key": "m_tcos", "text": "Reklam maliyeti (TCOS) < %25 hedefine uygun"},
                   {"key": "m_roi", "text": "12 aylık projeksiyonda ROI > %100 onaylandı"}]},
    ],
}

STAGE_KEYS = tuple(s["key"] for s in DEFAULT_TEMPLATE["stages"])
# Otomatik maddelerin sabit yeri (şablonda silinemez/taşınamaz)
AUTO_STAGE = {"searches": "market", "top10_revenue": "market", "reviews": "competition",
              "new_brands": "competition", "top3_share": "competition", "price": "competition"}


def default_template() -> dict:
    return copy.deepcopy(DEFAULT_TEMPLATE)


class TemplateError(ValueError):
    pass


def _num(v, name):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise TemplateError(f"{name} sayı olmalı")
    return v


def validate_template(tpl: dict) -> dict:
    """
    Owner'ın gönderdiği şablonu doğrular ve normalize eder:
    - 5 aşama sabit (anahtar/sıra/kritiklik değişmez); başlık/alt başlık düzenlenebilir.
    - Otomatik maddeler sabit (silinemez); yalnızca eşikleri değişir.
    - Manuel maddeler eklenip/silinip/düzenlenebilir (aşama başına en fazla 30).
    """
    if not isinstance(tpl, dict):
        raise TemplateError("Şablon bir nesne olmalı")
    th_in = tpl.get("thresholds") or {}
    th = {}
    for k, (lo, hi, is_int) in THRESHOLD_LIMITS.items():
        v = _num(th_in.get(k, DEFAULT_THRESHOLDS[k]), k)
        if is_int and float(v) != int(v):
            raise TemplateError(f"{k} tam sayı olmalı")
        if not (lo <= v <= hi):
            raise TemplateError(f"{k} {lo}–{hi} aralığında olmalı")
        th[k] = int(v) if is_int else float(v)
    if th["price_min"] >= th["price_max"]:
        raise TemplateError("Fiyat alt sınırı üst sınırdan küçük olmalı")

    stages_in = tpl.get("stages")
    if not isinstance(stages_in, list) or [s.get("key") if isinstance(s, dict) else None for s in stages_in] != list(STAGE_KEYS):
        raise TemplateError("Aşamalar sabittir: " + ", ".join(STAGE_KEYS))
    out_stages = []
    for base, s in zip(DEFAULT_TEMPLATE["stages"], stages_in):
        title = str(s.get("title") or "").strip()
        subtitle = str(s.get("subtitle") or "").strip()
        if not (1 <= len(title) <= 120) or len(subtitle) > 300:
            raise TemplateError(f"{base['key']}: başlık 1–120, alt başlık en fazla 300 karakter")
        items = []
        # Otomatik maddeler her zaman sabit sırayla başta
        for it in base["items"]:
            if it.get("auto"):
                items.append({"key": it["key"], "auto": it["auto"]})
        manual_in = [it for it in (s.get("items") or []) if isinstance(it, dict) and not it.get("auto")]
        if len(manual_in) > 30:
            raise TemplateError(f"{base['key']}: en fazla 30 manuel madde")
        seen = set()
        for it in manual_in:
            text = str(it.get("text") or "").strip()
            if not (1 <= len(text) <= 300):
                raise TemplateError("Madde metni 1–300 karakter olmalı")
            key = str(it.get("key") or "")
            if not key.startswith("m_") or len(key) > 40 or not key[2:].replace("_", "").isalnum() or key in seen:
                key = "m_" + secrets.token_hex(4)
            seen.add(key)
            items.append({"key": key, "text": text})
        out_stages.append({"key": base["key"], "title": title, "subtitle": subtitle,
                           "critical": base["critical"], "items": items})
    return {"version": 1, "thresholds": th, "stages": out_stages}


# ---------------------------------------------------------------------------
# Snapshot & değerlendirme
# ---------------------------------------------------------------------------
def _ratio(v):
    """totalRevenueRatio 0-1 oran (scoring.py ile aynı); savunma: >1 gelirse yüzde say."""
    if v is None:
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v / 100 if v > 1 else v


def extract_snapshot(payload: dict, fetched_at: int | None = None) -> dict:
    """Analiz payload'undan otomatik maddeler için gereken değerleri çıkarır (None = veri yok)."""
    payload = payload or {}
    stats = payload.get("market_stats") or {}
    is_asin = payload.get("analysis_mode") == "asin"

    # 1) Ana keyword'ün KENDİ (exact, keywordList) arama hacmi — yalnızca keyword modunda
    searches = None
    if not is_asin:
        kw = str(payload.get("keyword") or "").strip().lower()
        for r in payload.get("keyword_rows") or []:
            if str(r.get("keyword") or "").strip().lower() == kw and r.get("searches") is not None:
                searches = r.get("searches")
                break

    # 2) İlk 10 rakibin (backend sırası: total_units desc) toplam aylık cirosu
    comps = (payload.get("top_competitors") or [])[:10]
    revs = [c.get("revenue") for c in comps if isinstance(c.get("revenue"), (int, float))]
    top10_revenue = float(sum(revs)) if revs else None

    # 4) Güçlü yeni marka sayısı — pre_assessment'taki değer (strong_new_brands_count)
    new_brands = None
    for c in (payload.get("pre_assessment") or {}).get("criteria") or []:
        if c.get("label") == "Güçlü Yeni Marka (1 yıl)":
            new_brands = c.get("value")

    # 5) İlk 3 markanın ciro payı toplamı
    shares = sorted([s for s in (_ratio(b.get("totalRevenueRatio")) for b in payload.get("brand_concentration") or [])
                     if s is not None], reverse=True)
    top3_share = round(sum(shares[:3]), 6) if shares else None

    num = lambda v: float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
    return {
        "analysis_key": payload.get("_analysis_key"),
        "keyword": payload.get("keyword"),
        "marketplace": payload.get("marketplace"),
        "analysis_mode": "asin" if is_asin else "keyword",
        "fetched_at": fetched_at,
        "category": payload.get("category_used"),
        "values": {
            "searches": num(searches),
            "top10_revenue": num(top10_revenue),
            "top10_count": len(comps),
            "reviews": num(stats.get("avgRatings")),
            "new_brands": num(new_brands),
            "top3_share": num(top3_share),
            "price": num(stats.get("avgPrice")),
        },
    }


def evaluate_auto(auto: str, values: dict, th: dict) -> dict:
    """{'status': 'pass'|'fail'|'no_data', 'value': ham değer, 'display': rozet metni}"""
    v = (values or {}).get(auto)
    if v is None:
        return {"status": "no_data", "value": None, "display": "Veri yok"}
    if auto == "searches":
        ok, disp = v > th["searches_min"], f"Aylık {_fmt_int(v)} arama"
    elif auto == "top10_revenue":
        n = (values or {}).get("top10_count") or 0
        ok, disp = v > th["top10_revenue_min"], f"{_fmt_usd(v)} / ay" + (f" ({n} rakip)" if n and n < 10 else "")
    elif auto == "reviews":
        ok, disp = v < th["reviews_max"], f"Ortalama {_fmt_int(v)} yorum"
    elif auto == "new_brands":
        ok, disp = v >= th["new_brands_min"], f"{_fmt_int(v)} yeni marka"
    elif auto == "top3_share":
        ok, disp = v < th["top3_share_max"], f"İlk 3 marka {_fmt_pct(v)}"
    elif auto == "price":
        ok, disp = th["price_min"] <= v <= th["price_max"], f"Ortalama {_fmt_usd(v)}"
    else:
        return {"status": "no_data", "value": None, "display": "Veri yok"}
    return {"status": "pass" if ok else "fail", "value": v, "display": disp}
